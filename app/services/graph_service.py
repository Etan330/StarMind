from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import KnowledgeGraphEdge, RawSource


class GraphService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_edges_for_source(self, raw_source_id: int, concepts: list[str], domain: str | None = None) -> list[KnowledgeGraphEdge]:
        """Match concepts against existing sources and create edges."""
        if not concepts:
            return []

        all_sources = self.db.query(RawSource).filter(RawSource.id != raw_source_id).all()
        new_edges: list[KnowledgeGraphEdge] = []

        for other in all_sources:
            meta = json.loads(other.metadata_json or "{}")
            other_concepts = meta.get("related_concepts", [])
            other_domain = meta.get("domain", "")

            shared = set(concepts) & set(other_concepts)
            if shared:
                relation = "topic_overlap"
                weight = min(0.9, 0.3 * len(shared))
            elif domain and other_domain == domain:
                relation = "domain_same"
                weight = 0.6
                shared = set()
            else:
                continue

            # Check if edge already exists
            existing = self.db.query(KnowledgeGraphEdge).filter(
                KnowledgeGraphEdge.source_id == raw_source_id,
                KnowledgeGraphEdge.target_id == other.id,
                KnowledgeGraphEdge.relation == relation,
            ).first()
            if existing:
                continue

            edge = KnowledgeGraphEdge(
                source_id=raw_source_id,
                target_id=other.id,
                relation=relation,
                weight=weight,
                shared_concepts_json=json.dumps(list(shared), ensure_ascii=False),
            )
            self.db.add(edge)
            new_edges.append(edge)

        if new_edges:
            self.db.commit()
        return new_edges

    def get_graph_data(self, domain_filter: str | None = None) -> dict[str, Any]:
        sources = self.db.query(RawSource).all()
        edges = self.db.query(KnowledgeGraphEdge).all()

        # Build node map
        source_map = {s.id: s for s in sources}
        node_ids = set()
        filtered_edges = []

        for e in edges:
            if domain_filter:
                src_meta = json.loads(source_map[e.source_id].metadata_json or "{}") if e.source_id in source_map else {}
                if src_meta.get("domain") != domain_filter:
                    continue
            filtered_edges.append(e)
            node_ids.add(e.source_id)
            node_ids.add(e.target_id)

        # Include orphans if no filter
        if not domain_filter:
            node_ids.update(s.id for s in sources)

        # Count edges per node for sizing
        edge_count: dict[int, int] = {}
        for e in filtered_edges:
            edge_count[e.source_id] = edge_count.get(e.source_id, 0) + 1
            edge_count[e.target_id] = edge_count.get(e.target_id, 0) + 1

        nodes = []
        for nid in node_ids:
            src = source_map.get(nid)
            if not src:
                continue
            meta = json.loads(src.metadata_json or "{}")
            nodes.append({
                "id": nid,
                "label": src.title[:40],
                "domain": meta.get("domain", "未分类"),
                "topics": meta.get("related_concepts", []),
                "size": max(1, edge_count.get(nid, 0)),
                "type": src.source_type,
            })

        edge_list = [
            {
                "source": e.source_id,
                "target": e.target_id,
                "relation": e.relation,
                "weight": e.weight,
                "shared_concepts": json.loads(e.shared_concepts_json or "[]"),
            }
            for e in filtered_edges
        ]

        return {"nodes": nodes, "edges": edge_list}

    def get_node_detail(self, raw_source_id: int) -> dict[str, Any]:
        src = self.db.get(RawSource, raw_source_id)
        if not src:
            return {"error": "not found"}

        edges = self.db.query(KnowledgeGraphEdge).filter(
            (KnowledgeGraphEdge.source_id == raw_source_id) | (KnowledgeGraphEdge.target_id == raw_source_id)
        ).all()

        connected_ids = set()
        for e in edges:
            connected_ids.add(e.source_id if e.source_id != raw_source_id else e.target_id)

        connected = self.db.query(RawSource).filter(RawSource.id.in_(connected_ids)).all() if connected_ids else []

        return {
            "id": src.id,
            "title": src.title,
            "platform": src.platform,
            "source_url": src.source_url,
            "metadata": json.loads(src.metadata_json or "{}"),
            "connected": [{"id": c.id, "title": c.title, "platform": c.platform} for c in connected],
            "edge_count": len(edges),
        }

    def detect_orphans(self) -> list[int]:
        """Find RawSource IDs with no graph edges."""
        all_ids = {s.id for s in self.db.query(RawSource.id).all()}
        connected = set()
        for e in self.db.query(KnowledgeGraphEdge).all():
            connected.add(e.source_id)
            connected.add(e.target_id)
        return sorted(all_ids - connected)
