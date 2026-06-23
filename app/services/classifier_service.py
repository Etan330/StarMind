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

    def latest_for_candidate(self, candidate_id: int) -> KnowledgeClassification | None:
        return (
            self.db.query(KnowledgeClassification)
            .filter(KnowledgeClassification.candidate_id == candidate_id)
            .order_by(KnowledgeClassification.created_at.desc())
            .first()
        )

    def ensure_manual_skip_audit(self, candidate: CandidateItem) -> KnowledgeClassification:
        existing = self.latest_for_candidate(candidate.id)
        if existing is not None:
            return existing
        audit = KnowledgeClassification(
            candidate_id=candidate.id,
            is_knowledge=True,
            label="skipped",
            confidence=0.0,
            knowledge_type_json="[]",
            reason="用户直接确认保存为原始资料，跳过模型分类；系统已记录人工确认审计。",
            decision="manual_confirmed_without_classification",
        )
        self.db.add(audit)
        self.db.commit()
        self.db.refresh(audit)
        return audit

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
            "方法论",
            "技巧",
            "指南",
            "架构",
            "复盘",
            "coding",
            "sop",
            "rag",
            "编程",
            "算法",
            "设计模式",
            "源码",
            "论文",
            "研究",
            "原理",
            "认知",
            "思维模型",
            "商业模式",
            "策略",
        ]
        low_value_markers = [
            "抽奖", "搞笑", "明星", "娱乐", "颜值", "减肥", "穿搭", "吐槽",
            "甄嬛传", "电视剧", "追剧", "惊险一幕", "监控拍下", "瘦身", "苗条",
            "作文", "高考", "提分", "满分作文", "个税", "报税", "退税",
            "扭扭捏捏", "性格", "情绪", "崩溃", "懂事", "隐忍",
            "火灾", "起火", "阴燃", "充电线",
        ]
        # Require stronger knowledge signals — single keyword like "面试" alone is not enough
        knowledge_hits = [m for m in knowledge_markers if m in text]
        low_hits = [m for m in low_value_markers if m in text]

        # If both knowledge and low-value markers present, prefer filtering out
        if low_hits:
            return {
                "is_knowledge": False,
                "label": "non_knowledge",
                "confidence": 0.82,
                "knowledge_type": [],
                "reason": f"内容包含非知识类信号（{', '.join(low_hits[:3])}），先放入可恢复回收站。",
                "decision": "archive_to_recycle_bin",
            }
        if len(knowledge_hits) >= 2 or any(m in text for m in ["ai", "agent", "rag", "sop", "编程", "算法", "架构", "论文"]):
            return {
                "is_knowledge": True,
                "label": "knowledge",
                "confidence": 0.78,
                "knowledge_type": ["启发/方法"],
                "reason": f"标题包含知识信号（{', '.join(knowledge_hits[:3])}）。",
                "decision": "ingest_to_raw_sources",
            }
        # Default: uncertain — needs user review, not auto-ingest
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

    async def batch_classify_titles(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Classify a batch of titles into domain categories using LLM."""
        settings = get_model_settings()
        task = settings.get("task_models", {}).get("classifier_model", {})
        provider, model, _config = get_provider_runtime(task.get("provider"), task.get("model"))

        # Process in batches of 20
        all_classified: list[dict[str, Any]] = []
        for i in range(0, len(items), 20):
            batch = items[i:i + 20]
            titles_text = "\n".join(f"{idx+1}. {item.get('title', '')} ({item.get('url', '')})" for idx, item in enumerate(batch))
            prompt = (
                "请将以下收藏内容按知识领域分类。对每条内容输出其所属领域（如：AI/大模型、产品设计、编程开发、搞笑视频、美食探店等）。\n"
                "输出 JSON 数组，每个元素包含 index(从1开始)、domain(领域名)。\n\n"
                f"内容列表：\n{titles_text}"
            )
            try:
                result = await provider.json_chat(
                    [{"role": "user", "content": prompt}],
                    model=model,
                )
                classified = result if isinstance(result, list) else result.get("items", result.get("classifications", []))
                for item_result in classified:
                    idx = int(item_result.get("index", 0)) - 1
                    if 0 <= idx < len(batch):
                        batch[idx]["domain"] = item_result.get("domain", "未分类")
                all_classified.extend(batch)
            except Exception:
                # Fallback: assign "未分类"
                for item in batch:
                    item.setdefault("domain", "未分类")
                all_classified.extend(batch)

        # Group by domain
        categories: dict[str, list[dict[str, Any]]] = {}
        for item in all_classified:
            domain = item.get("domain", "未分类")
            categories.setdefault(domain, []).append(item)

        return {
            "categories": [
                {"domain": domain, "count": len(items_list), "items": items_list}
                for domain, items_list in sorted(categories.items(), key=lambda x: -len(x[1]))
            ]
        }
