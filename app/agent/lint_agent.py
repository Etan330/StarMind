from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import KnowledgeGraphEdge, RawSource, WikiPage
from app.services.graph_service import GraphService


@dataclass
class LintFinding:
    check_type: str
    severity: str  # "info" | "warning" | "error"
    target_type: str
    target_id: str
    message: str
    suggestion: str


class LintAgent:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def run_full_check(self) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        findings.extend(self._check_orphan_nodes())
        findings.extend(self._check_single_source_topics())
        findings.extend(self._check_stale_pages())
        findings.extend(self._check_duplicates())
        findings.extend(self._check_missing_refs())
        return {"findings": findings, "total": len(findings), "checked_at": datetime.now().isoformat()}

    def _check_orphan_nodes(self) -> list[dict[str, Any]]:
        orphans = GraphService(self.db).detect_orphans()
        findings = []
        for oid in orphans[:20]:
            src = self.db.get(RawSource, oid)
            if src:
                findings.append({
                    "check_type": "orphan_node",
                    "severity": "info",
                    "target_type": "raw_source",
                    "target_id": str(oid),
                    "message": f"'{src.title}' 在知识图谱中没有任何关联",
                    "suggestion": "尝试为此内容建立与其他知识的关联",
                })
        return findings

    def _check_single_source_topics(self) -> list[dict[str, Any]]:
        import json
        domain_counts: dict[str, int] = {}
        for src in self.db.query(RawSource).all():
            meta = json.loads(src.metadata_json or "{}")
            domain = meta.get("domain", "")
            if domain:
                domain_counts[domain] = domain_counts.get(domain, 0) + 1

        findings = []
        for domain, count in domain_counts.items():
            if count == 1:
                findings.append({
                    "check_type": "single_source_topic",
                    "severity": "info",
                    "target_type": "domain",
                    "target_id": domain,
                    "message": f"领域 '{domain}' 仅有 1 个来源",
                    "suggestion": f"建议补充更多关于 '{domain}' 的内容",
                })
        return findings

    def _check_stale_pages(self) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        stale = self.db.query(WikiPage).filter(WikiPage.last_updated_at < cutoff).all()
        return [
            {
                "check_type": "stale_page",
                "severity": "warning",
                "target_type": "wiki_page",
                "target_id": p.page_id,
                "message": f"'{p.title}' 超过 90 天未更新",
                "suggestion": "建议检查是否需要刷新内容",
            }
            for p in stale[:20]
        ]

    def _check_duplicates(self) -> list[dict[str, Any]]:
        pages = self.db.query(WikiPage).all()
        findings = []
        seen_titles: dict[str, str] = {}
        for p in pages:
            normalized = p.title.strip().lower()
            if normalized in seen_titles:
                findings.append({
                    "check_type": "duplicate_page",
                    "severity": "warning",
                    "target_type": "wiki_page",
                    "target_id": p.page_id,
                    "message": f"'{p.title}' 与 '{seen_titles[normalized]}' 标题重复",
                    "suggestion": "建议合并这两个页面",
                })
            else:
                seen_titles[normalized] = p.page_id
        return findings

    def _check_missing_refs(self) -> list[dict[str, Any]]:
        import json
        findings = []
        source_ids = {s.id for s in self.db.query(RawSource.id).all()}
        for page in self.db.query(WikiPage).all():
            refs = json.loads(page.source_refs_json or "[]")
            for ref in refs:
                rid = ref.get("raw_source_id")
                if rid and int(rid) not in source_ids:
                    findings.append({
                        "check_type": "missing_source_ref",
                        "severity": "error",
                        "target_type": "wiki_page",
                        "target_id": page.page_id,
                        "message": f"'{page.title}' 引用的 RawSource #{rid} 不存在",
                        "suggestion": "来源已丢失，建议移除引用或标记为异常",
                    })
                    break
        return findings
