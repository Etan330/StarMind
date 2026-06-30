from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import KnowledgeGraphEdge, WikiPage


class GraphService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_graph_data(self, domain_filter: str | None = None) -> dict[str, Any]:
        pages = self.db.query(WikiPage).filter(WikiPage.status.in_(["active", "needs_review"])).all()
        edges = self.db.query(KnowledgeGraphEdge).all()

        page_map = {p.page_id: p for p in pages}
        page_tags = {p.page_id: self._safe_tags(p.tags_json) for p in pages}
        node_ids = set()
        filtered_edges = []

        for e in edges:
            if e.source_page_id not in page_map or e.target_page_id not in page_map:
                continue
            if domain_filter and domain_filter not in page_tags[e.source_page_id]:
                continue
            filtered_edges.append(e)
            node_ids.add(e.source_page_id)
            node_ids.add(e.target_page_id)

        edge_list = self._dedupe_edge_dicts([self._edge_to_dict(e) for e in filtered_edges])

        if not domain_filter:
            node_ids.update(p.page_id for p in pages)
        else:
            node_ids.update(edge["source"] for edge in edge_list)
            node_ids.update(edge["target"] for edge in edge_list)
            node_ids.update(p.page_id for p in pages if domain_filter in page_tags[p.page_id])

        edge_count: dict[str, int] = {}
        for edge in edge_list:
            edge_count[edge["source"]] = edge_count.get(edge["source"], 0) + 1
            edge_count[edge["target"]] = edge_count.get(edge["target"], 0) + 1

        nodes = []
        for pid in node_ids:
            page = page_map.get(pid)
            if not page:
                continue
            tags = page_tags.get(pid, [])
            nodes.append({
                "id": pid,
                "label": page.title[:40],
                "domain": tags[0] if tags else "未分类",
                "topics": tags,
                "size": max(1, edge_count.get(pid, 0)),
                "type": page.page_type,
            })

        return {"nodes": nodes, "edges": edge_list}

    def _edge_to_dict(self, edge: KnowledgeGraphEdge) -> dict[str, Any]:
        return {
            "source": edge.source_page_id,
            "target": edge.target_page_id,
            "relation": edge.relation,
            "weight": edge.weight,
            "shared_concepts": self._safe_tags(edge.shared_concepts_json),
        }

    def _dedupe_edge_dicts(self, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: dict[tuple[str, str, str], dict[str, Any]] = {}
        for edge in edges:
            source = str(edge["source"])
            target = str(edge["target"])
            key = (*sorted([source, target]), str(edge["relation"]))
            existing = selected.get(key)
            if existing is None or float(edge.get("weight") or 0) > float(existing.get("weight") or 0):
                selected[key] = edge
        return list(selected.values())

    def _safe_tags(self, raw_json: str | None) -> list[str]:
        try:
            value = json.loads(raw_json or "[]")
        except json.JSONDecodeError:
            return []
        return value if isinstance(value, list) else []

    def get_node_detail(self, page_id: str) -> dict[str, Any]:
        page = self.db.query(WikiPage).filter(WikiPage.page_id == page_id).first()
        if not page:
            return {"error": "not found"}

        edges = self.db.query(KnowledgeGraphEdge).filter(
            (KnowledgeGraphEdge.source_page_id == page_id) | (KnowledgeGraphEdge.target_page_id == page_id)
        ).all()

        connected_ids = {
            e.source_page_id if e.source_page_id != page_id else e.target_page_id
            for e in edges
        }
        connected_pages = self.db.query(WikiPage).filter(WikiPage.page_id.in_(connected_ids)).all() if connected_ids else []

        return {
            "id": page_id,
            "title": page.title,
            "type": page.page_type,
            "tags": json.loads(page.tags_json or "[]"),
            "connected": [{"id": p.page_id, "title": p.title, "type": p.page_type} for p in connected_pages],
            "edge_count": len(edges),
        }
