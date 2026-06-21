from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from app.config import LOCAL_DATA_DIR
from app.connectors.base import ConnectorItem


class DouyinTranscriptError(RuntimeError):
    pass


MetadataLoader = Callable[[str], dict[str, Any]]
AudioDownloader = Callable[[str, str], Path]
AudioTranscriber = Callable[[Path], str]
ASR_PYTHON_CANDIDATES = [
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
    "python3",
]


class DouyinTranscriptService:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        yt_dlp_bin: str | None = None,
        python_bin: str | None = None,
        whisper_model: str | None = None,
        cookies_file: str | Path | None = None,
        language: str = "zh",
        metadata_loader: MetadataLoader | None = None,
        audio_downloader: AudioDownloader | None = None,
        audio_transcriber: AudioTranscriber | None = None,
    ) -> None:
        self.cache_dir = cache_dir or LOCAL_DATA_DIR / "transcripts" / "douyin"
        self.yt_dlp_bin = yt_dlp_bin or os.getenv("STARMIND_YTDLP_BIN", "yt-dlp")
        self.python_bin = python_bin or os.getenv("STARMIND_ASR_PYTHON") or self._detect_asr_python()
        self.whisper_model = whisper_model or os.getenv("STARMIND_WHISPER_MODEL", "tiny")
        self.yt_dlp_format = os.getenv("STARMIND_YTDLP_FORMAT", "best")
        self.yt_dlp_sort = os.getenv("STARMIND_YTDLP_SORT", "+size,+br,+res,+fps")
        configured_cookies_file = cookies_file or os.getenv("STARMIND_YTDLP_COOKIES_FILE")
        self.cookies_file = Path(configured_cookies_file) if configured_cookies_file else None
        self.language = language
        self.metadata_loader = metadata_loader or self._load_metadata
        self.audio_downloader = audio_downloader or self._download_audio
        self.audio_transcriber = audio_transcriber or self._transcribe_audio

    def enrich_item(self, item: ConnectorItem, *, require_transcript: bool = True) -> ConnectorItem:
        metadata = dict(item.metadata or {})
        if str(metadata.get("transcript") or "").strip():
            metadata.setdefault("transcript_status", "provided")
            metadata.setdefault("transcript_source", "provided")
            return ConnectorItem(
                raw_url=item.raw_url,
                title=item.title,
                author=item.author,
                platform=item.platform,
                content_type=item.content_type,
                metadata=metadata,
            )

        source_meta = self.metadata_loader(item.raw_url)
        video_id = self._video_id(item.raw_url, source_meta)
        canonical_url = self._canonical_video_url(video_id) or item.raw_url
        audio_path = self.audio_downloader(item.raw_url, video_id)
        transcript = self.audio_transcriber(audio_path).strip()
        if not transcript and require_transcript:
            raise DouyinTranscriptError(f"ASR returned an empty transcript for {item.raw_url}")

        metadata.update(
            {
                "transcript": transcript,
                "transcript_status": "provided" if transcript else "empty",
                "transcript_source": "yt_dlp_faster_whisper",
                "yt_dlp_id": video_id,
                "yt_dlp_title": source_meta.get("title") or "",
                "yt_dlp_description": source_meta.get("description") or "",
                "yt_dlp_webpage_url": source_meta.get("webpage_url") or item.raw_url,
                "douyin_canonical_url": canonical_url,
                "original_raw_url": item.raw_url,
                "audio_path": str(audio_path),
            }
        )
        title = str(source_meta.get("title") or item.title or item.raw_url).strip()
        author = str(source_meta.get("channel") or source_meta.get("uploader") or item.author or "").strip() or None
        return ConnectorItem(
            raw_url=canonical_url,
            title=title,
            author=author,
            platform=item.platform,
            content_type=item.content_type or "video",
            metadata=metadata,
        )

    def enrich_items(self, items: list[ConnectorItem], *, limit: int | None = None, require_transcript: bool = True) -> list[ConnectorItem]:
        enriched: list[ConnectorItem] = []
        for item in items[: limit or len(items)]:
            enriched.append(self.enrich_item(item, require_transcript=require_transcript))
        return enriched

    def _load_metadata(self, url: str) -> dict[str, Any]:
        completed = subprocess.run(
            [*self._yt_dlp_base_args(), "-J", "--no-playlist", "--socket-timeout", "25", url],
            capture_output=True,
            text=True,
            timeout=int(os.getenv("STARMIND_YTDLP_METADATA_TIMEOUT_SEC", "90")),
            check=False,
        )
        if completed.returncode != 0:
            raise DouyinTranscriptError((completed.stderr or completed.stdout or "yt-dlp metadata failed").strip())
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DouyinTranscriptError("yt-dlp returned invalid JSON metadata") from exc
        return payload if isinstance(payload, dict) else {}

    def _download_audio(self, url: str, video_id: str) -> Path:
        work_dir = self.cache_dir / self._safe_name(video_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(work_dir / "audio.%(ext)s")
        completed = subprocess.run(
            [
                self.yt_dlp_bin,
                *self._yt_dlp_cookie_args(),
                *self._yt_dlp_sort_args(),
                "-f",
                self.yt_dlp_format,
                "-x",
                "--audio-format",
                "m4a",
                "--no-playlist",
                "-o",
                output_template,
                url,
            ],
            capture_output=True,
            text=True,
            timeout=int(os.getenv("STARMIND_YTDLP_DOWNLOAD_TIMEOUT_SEC", "900")),
            check=False,
        )
        if completed.returncode != 0:
            raise DouyinTranscriptError((completed.stderr or completed.stdout or "yt-dlp audio download failed").strip())
        candidates = sorted(work_dir.glob("audio.*"))
        if not candidates:
            raise DouyinTranscriptError("yt-dlp completed but no audio file was created")
        return candidates[0]

    def _yt_dlp_base_args(self) -> list[str]:
        return [self.yt_dlp_bin, *self._yt_dlp_cookie_args()]

    def _yt_dlp_cookie_args(self) -> list[str]:
        if self.cookies_file and self.cookies_file.exists():
            return ["--cookies", str(self.cookies_file)]
        return []

    def _yt_dlp_sort_args(self) -> list[str]:
        if self.yt_dlp_sort:
            return ["-S", self.yt_dlp_sort]
        return []

    def _transcribe_audio(self, audio_path: Path) -> str:
        script = r"""
import json
import sys
import warnings
from faster_whisper import WhisperModel

audio_path, model_name, language = sys.argv[1], sys.argv[2], sys.argv[3]
warnings.filterwarnings("ignore", category=RuntimeWarning)
model = WhisperModel(model_name, device="cpu", compute_type="int8")
segments, info = model.transcribe(audio_path, language=language or None, vad_filter=True, beam_size=1)
text = "\n".join(segment.text.strip() for segment in segments if segment.text.strip())
print(json.dumps({"text": text, "language": info.language, "duration": info.duration}, ensure_ascii=False))
"""
        completed = subprocess.run(
            [self.python_bin, "-c", script, str(audio_path), self.whisper_model, self.language],
            capture_output=True,
            text=True,
            timeout=int(os.getenv("STARMIND_ASR_TIMEOUT_SEC", "1800")),
            check=False,
        )
        if completed.returncode != 0:
            raise DouyinTranscriptError((completed.stderr or completed.stdout or "faster-whisper ASR failed").strip())
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DouyinTranscriptError("faster-whisper returned invalid JSON") from exc
        return str(payload.get("text") or "").strip()

    def _detect_asr_python(self) -> str:
        seen: set[str] = set()
        candidates = [sys.executable, *ASR_PYTHON_CANDIDATES]
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                completed = subprocess.run(
                    [
                        candidate,
                        "-c",
                        "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('faster_whisper') else 1)",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except Exception:
                continue
            if completed.returncode == 0:
                return candidate
        return "python3"

    def _video_id(self, url: str, metadata: dict[str, Any]) -> str:
        explicit = str(metadata.get("id") or "").strip()
        if explicit:
            return explicit
        match = re.search(r"/video/(\d+)", url)
        if match:
            return match.group(1)
        return self._safe_name(url)[-80:] or "unknown"

    def _canonical_video_url(self, video_id: str) -> str | None:
        if not video_id or video_id == "unknown":
            return None
        return f"https://www.douyin.com/video/{video_id}"

    def _safe_name(self, value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
        return safe[:160] or "unknown"
