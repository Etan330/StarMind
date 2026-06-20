from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.llm import get_model_settings, get_provider_runtime
from app.models import CandidateItem, KnowledgeClassification, RecycleBinItem
from app.services.statuses import (
    ARCHIVED_RECOVERABLE,
    CLASSIFIED_KNOWLEDGE,
    CLASSIFIED_UNCERTAIN,
    KNOWLEDGE_CONFIDENCE,
    PENDING_CLASSIFICATION,
    UNCERTAIN_CONFIDENCE,
)


@dataclass
class ClassificationResult:
    candidate_id: int
    label: str
    confidence: float
    decision: str
    status: str
    reason: str


class ClassifierService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def classify_candidate(self, candidate_id: int) -> ClassificationResult:
        candidate = self.db.get(CandidateItem, candidate_id)
        if candidate is None:
            raise ValueError(f"Candidate {candidate_id} not found")
        payload = await self._classify_payload(candidate)
        result = self._normalize_result(payload)
        status = self._status_for(result)
        self._save_classification(candidate, result, status)
        return ClassificationResult(
            candidate_id=candidate.id,
            label=result["label"],
            confidence=float(result["confidence"]),
            decision=result["decision"],
            status=status,
            reason=result["reason"],
        )

    async def classify_pending(self, limit: int = 20) -> list[ClassificationResult]:
        candidates = (
            self.db.query(CandidateItem)
            .filter(CandidateItem.status == PENDING_CLASSIFICATION)
            .order_by(CandidateItem.created_at.desc())
            .limit(limit)
            .all()
        )
        results: list[ClassificationResult] = []
        for candidate in candidates:
            results.append(await self.classify_candidate(candidate.id))
        return results

    async def _classify_payload(self, candidate: CandidateItem) -> dict[str, Any]:
        metadata = self._metadata(candidate)
        prompt = self._prompt()
        item = {
            "title": candidate.title,
            "platform": candidate.platform,
            "url": candidate.canonical_url,
            "author": candidate.author,
            "content_type": candidate.content_type,
            "metadata": metadata,
        }
        settings = get_model_settings()
        task = settings.get("task_models", {}).get("classifier_model", {})
        provider, model, _config = get_provider_runtime(task.get("provider"), task.get("model"))
        try:
            payload = await provider.json_chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(item, ensure_ascii=False)},
                ],
                model=model,
                schema={
                    "type": "object",
                    "properties": {
                        "is_knowledge": {"type": "boolean"},
                        "label": {"type": "string"},
                        "confidence": {"type": "number"},
                        "knowledge_type": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                        "decision": {"type": "string"},
                    },
                    "required": ["label", "confidence", "reason", "decision"],
                },
            )
            if str(payload.get("label") or "").strip().lower() not in {"knowledge", "uncertain", "non_knowledge"}:
                return self._heuristic_fallback(candidate, metadata)
            return payload
        except Exception:
            return self._heuristic_fallback(candidate, metadata)

    def _normalize_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        label = str(payload.get("label") or "").strip().lower()
        if label not in {"knowledge", "uncertain", "non_knowledge"}:
            label = "uncertain"
        try:
            confidence = float(payload.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(confidence, 1.0))
        decision = str(payload.get("decision") or "").strip()
        if not decision:
            decision = {
                "knowledge": "ingest_to_raw_sources",
                "uncertain": "send_to_review_queue",
                "non_knowledge": "archive_to_recycle_bin",
            }[label]
        return {
            "is_knowledge": bool(payload.get("is_knowledge", label == "knowledge")),
            "label": label,
            "confidence": confidence,
            "knowledge_type": payload.get("knowledge_type") if isinstance(payload.get("knowledge_type"), list) else [],
            "reason": str(payload.get("reason") or "分类器未给出充分理由，已按安全策略处理。").strip(),
            "decision": decision,
        }

    def _status_for(self, result: dict[str, Any]) -> str:
        label = result["label"]
        confidence = float(result["confidence"])
        if label == "knowledge" and confidence >= KNOWLEDGE_CONFIDENCE:
            return CLASSIFIED_KNOWLEDGE
        if label == "non_knowledge" and confidence < UNCERTAIN_CONFIDENCE:
            return ARCHIVED_RECOVERABLE
        if label == "non_knowledge" and confidence >= KNOWLEDGE_CONFIDENCE:
            return ARCHIVED_RECOVERABLE
        return CLASSIFIED_UNCERTAIN

    def _save_classification(self, candidate: CandidateItem, result: dict[str, Any], status: str) -> None:
        self.db.add(
            KnowledgeClassification(
                candidate_id=candidate.id,
                is_knowledge=bool(result["is_knowledge"]),
                label=result["label"],
                confidence=float(result["confidence"]),
                knowledge_type_json=json.dumps(result["knowledge_type"], ensure_ascii=False),
                reason=result["reason"],
                decision=result["decision"],
            )
        )
        candidate.status = status
        if status == ARCHIVED_RECOVERABLE:
            existing = self.db.query(RecycleBinItem).filter(RecycleBinItem.candidate_id == candidate.id).first()
            if existing is None:
                self.db.add(
                    RecycleBinItem(
                        candidate_id=candidate.id,
                        canonical_url=candidate.canonical_url,
                        external_item_id=candidate.external_item_id,
                        title=candidate.title,
                        platform=candidate.platform,
                        reason=result["reason"],
                        confidence=float(result["confidence"]),
                        status=ARCHIVED_RECOVERABLE,
                    )
                )
        self.db.commit()

    def _heuristic_fallback(self, candidate: CandidateItem, metadata: dict[str, Any]) -> dict[str, Any]:
        text = " ".join(
            [
                candidate.title or "",
                candidate.platform or "",
                str(metadata.get("page_text") or ""),
                str(metadata.get("description") or ""),
            ]
        ).lower()
        knowledge_markers = [
            "ai",
            "agent",
            "教程",
            "方法",
            "技巧",
            "指南",
            "架构",
            "复盘",
            "面试",
            "职业",
            "coding",
            "sop",
            "rag",
        ]
        low_value_markers = ["抽奖", "搞笑", "明星", "娱乐", "颜值", "减肥", "穿搭", "吐槽"]
        if any(marker in text for marker in knowledge_markers):
            return {
                "is_knowledge": True,
                "label": "knowledge",
                "confidence": 0.78,
                "knowledge_type": ["启发/方法"],
                "reason": "标题或页面文本包含方法、教程、职业或 AI 等知识信号。",
                "decision": "ingest_to_raw_sources",
            }
        if any(marker in text for marker in low_value_markers):
            return {
                "is_knowledge": False,
                "label": "non_knowledge",
                "confidence": 0.82,
                "knowledge_type": [],
                "reason": "内容更像生活娱乐或低信息量收藏，先放入可恢复回收站。",
                "decision": "archive_to_recycle_bin",
            }
        return {
            "is_knowledge": False,
            "label": "uncertain",
            "confidence": 0.58,
            "knowledge_type": [],
            "reason": "元数据不足，无法确定是否值得入库，需要用户确认。",
            "decision": "send_to_review_queue",
        }

    def _metadata(self, candidate: CandidateItem) -> dict[str, Any]:
        try:
            value = json.loads(candidate.metadata_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _prompt(self) -> str:
        path = PROJECT_ROOT / "app" / "llm" / "prompts" / "classify_knowledge.md"
        if not Path(path).exists():
            return "判断收藏内容是否为知识内容，只输出 JSON。"
        return path.read_text(encoding="utf-8")
