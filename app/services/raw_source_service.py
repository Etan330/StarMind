from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import LOCAL_DATA_DIR
from app.models import CandidateItem, RawSource, SyncLedgerItem
from app.services.statuses import INGESTED


class RawSourceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ingest_candidate(self, candidate_id: int) -> RawSource:
        candidate = self.db.get(CandidateItem, candidate_id)
        if candidate is None:
            raise ValueError(f"Candidate {candidate_id} not found")
        existing = self.db.query(RawSource).filter(RawSource.canonical_url == candidate.canonical_url).first()
        metadata = json.loads(candidate.metadata_json or "{}")
        if existing:
            self._link_existing_source(existing, candidate)
            return existing

        base_dir = LOCAL_DATA_DIR / "raw_sources" / candidate.platform / str(candidate.id)
        base_dir.mkdir(parents=True, exist_ok=True)

        transcript = self._build_transcript(candidate, metadata)
        raw_text = self._build_raw_text(candidate, metadata, transcript)
        metadata_path = base_dir / "metadata.json"
        transcript_path = base_dir / "transcript.md"
        raw_path = base_dir / "raw.md"
        clean_path = base_dir / "clean.md"

        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        transcript_path.write_text(transcript, encoding="utf-8")
        raw_path.write_text(raw_text, encoding="utf-8")
        clean_path.write_text(transcript, encoding="utf-8")

        raw_source = RawSource(
            candidate_id=candidate.id,
            platform=candidate.platform,
            source_url=candidate.raw_url,
            canonical_url=candidate.canonical_url,
            external_item_id=candidate.external_item_id,
            source_type=candidate.source_type,
            title=candidate.title,
            author=candidate.author,
            raw_content_path=str(raw_path),
            clean_text_path=str(clean_path),
            transcript_path=str(transcript_path),
            metadata_json=json.dumps({**metadata, "transcript_status": self._transcript_status(metadata)}, ensure_ascii=False),
        )
        self.db.add(raw_source)
        self.db.flush()
        candidate.status = INGESTED
        ledger = self.db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).first()
        if ledger:
            ledger.raw_source_id = raw_source.id
            ledger.classification_label = "knowledge"
        self.db.commit()
        self.db.refresh(raw_source)
        return raw_source

    def _link_existing_source(self, raw_source: RawSource, candidate: CandidateItem) -> None:
        raw_source.candidate_id = raw_source.candidate_id or candidate.id
        candidate.status = INGESTED
        ledger = self.db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).first()
        if ledger:
            ledger.raw_source_id = raw_source.id
            ledger.classification_label = "knowledge"
        self.db.commit()
        self.db.refresh(raw_source)

    def _transcript_status(self, metadata: dict[str, Any]) -> str:
        if metadata.get("transcript"):
            return "provided"
        if metadata.get("content"):
            return "provided"
        if metadata.get("page_text") or metadata.get("description") or metadata.get("caption"):
            return "page_text_draft"
        return "audio_asr_pending"

    def _build_transcript(self, candidate: CandidateItem, metadata: dict[str, Any]) -> str:
        transcript = str(metadata.get("transcript") or "").strip()
        user_content = str(metadata.get("content") or "").strip()
        page_text = str(metadata.get("page_text") or metadata.get("description") or metadata.get("caption") or "").strip()
        now = datetime.now().isoformat(timespec="seconds")
        if transcript:
            body = transcript
            status = "平台或导入流程已提供逐字稿。"
        elif user_content:
            body = user_content
            status = "用户已提供原始正文。"
        elif page_text:
            body = page_text
            status = "当前使用页面可见文本生成逐字稿草稿，后续可接入音频 ASR 补全。"
        else:
            body = "当前还没有拿到平台字幕或音频 ASR 文本。已先保存视频链接、标题和页面信息，等待后续 ASR 任务补全。"
            status = "音频 ASR 待补全。"
        return (
            f"# {candidate.title}\n\n"
            f"- 来源：{candidate.platform}\n"
            f"- 链接：{candidate.canonical_url}\n"
            f"- 作者：{candidate.author or '未知'}\n"
            f"- 生成时间：{now}\n"
            f"- 状态：{status}\n\n"
            "## 逐字稿\n\n"
            f"{body}\n"
        )

    def _build_raw_text(self, candidate: CandidateItem, metadata: dict[str, Any], transcript: str) -> str:
        return (
            f"# 原始资料：{candidate.title}\n\n"
            f"URL: {candidate.canonical_url}\n\n"
            f"平台: {candidate.platform}\n\n"
            f"元数据:\n```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n\n"
            f"{transcript}\n"
        )
