from pathlib import Path
from types import SimpleNamespace

from app.connectors.base import ConnectorItem
from app.services.douyin_transcript_service import DouyinTranscriptService
import app.services.douyin_transcript_service as transcript_module


def test_enrich_item_adds_transcript_metadata(tmp_path):
    audio_path = tmp_path / "audio.m4a"
    audio_path.write_text("fake audio", encoding="utf-8")

    service = DouyinTranscriptService(
        metadata_loader=lambda url: {
            "id": "7380000112236",
            "title": "解析出来的标题",
            "channel": "李厂长来了",
            "description": "视频描述",
            "webpage_url": "https://www.douyin.com/video/7380000112236",
        },
        audio_downloader=lambda url, video_id: audio_path,
        audio_transcriber=lambda path: "这是从音频 ASR 得到的逐字稿。",
    )
    item = ConnectorItem(
        raw_url="https://v.douyin.com/test/",
        title="短链标题",
        platform="douyin",
        content_type="video",
        metadata={"source": "test"},
    )

    enriched = service.enrich_item(item)

    assert enriched.raw_url == "https://www.douyin.com/video/7380000112236"
    assert enriched.title == "解析出来的标题"
    assert enriched.author == "李厂长来了"
    assert enriched.metadata["transcript"] == "这是从音频 ASR 得到的逐字稿。"
    assert enriched.metadata["transcript_status"] == "provided"
    assert enriched.metadata["transcript_source"] == "yt_dlp_faster_whisper"
    assert enriched.metadata["yt_dlp_id"] == "7380000112236"
    assert enriched.metadata["douyin_canonical_url"] == "https://www.douyin.com/video/7380000112236"
    assert enriched.metadata["original_raw_url"] == "https://v.douyin.com/test/"
    assert Path(enriched.metadata["audio_path"]) == audio_path


def test_asr_python_detection_prefers_interpreter_with_faster_whisper(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        return SimpleNamespace(returncode=0 if command[0] == "/ok/python3" else 1)

    monkeypatch.delenv("STARMIND_ASR_PYTHON", raising=False)
    monkeypatch.setattr(transcript_module.sys, "executable", "/venv/python3")
    monkeypatch.setattr(transcript_module, "ASR_PYTHON_CANDIDATES", ["/ok/python3", "/other/python3"])
    monkeypatch.setattr(transcript_module.subprocess, "run", fake_run)

    service = DouyinTranscriptService(
        metadata_loader=lambda url: {},
        audio_downloader=lambda url, video_id: Path("/tmp/audio.m4a"),
        audio_transcriber=lambda path: "",
    )

    assert service.python_bin == "/ok/python3"
    assert calls == ["/venv/python3", "/ok/python3"]


def test_yt_dlp_commands_include_cookie_file(tmp_path, monkeypatch):
    cookies_file = tmp_path / "douyin_cookies.txt"
    cookies_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if "-J" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"id":"7380000112238","title":"带 cookie 的视频","webpage_url":"https://www.douyin.com/video/7380000112238"}',
                stderr="",
            )
        output_template = command[command.index("-o") + 1]
        Path(output_template.replace("%(ext)s", "m4a")).write_text("fake audio", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transcript_module.subprocess, "run", fake_run)
    service = DouyinTranscriptService(
        cache_dir=tmp_path,
        python_bin="/python",
        cookies_file=cookies_file,
        audio_transcriber=lambda path: "cookie 转写逐字稿",
    )

    enriched = service.enrich_item(
        ConnectorItem(
            raw_url="https://www.douyin.com/video/7380000112238",
            title="原标题",
            platform="douyin",
            content_type="video",
        )
    )

    assert enriched.metadata["transcript"] == "cookie 转写逐字稿"
    yt_dlp_commands = [command for command in commands if command[0] == "yt-dlp"]
    assert len(yt_dlp_commands) == 2
    for command in yt_dlp_commands:
        assert "--cookies" in command
        assert str(cookies_file) in command
    download_command = [command for command in yt_dlp_commands if "-f" in command][0]
    assert download_command[download_command.index("-S") + 1] == "+size,+br,+res,+fps"
    assert download_command[download_command.index("-f") + 1] == "best"
