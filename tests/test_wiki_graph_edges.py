"""Wiki 沉淀时 AI 判断知识关系并建立图谱边。"""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import KnowledgeGraphEdge, RawSource, WikiPage
from app.services.wiki_service import WikiMaintenanceService


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _add_raw_source(db, title="测试资料", rid=None):
    src = RawSource(
        platform="manual",
        source_url=f"https://example.com/{title}",
        canonical_url=f"https://example.com/{title}",
        external_item_id=f"ext-{title}",
        source_type="passive_link",
        title=title,
        raw_content_path="",
        clean_text_path="",
        transcript_path="",
    )
    if rid:
        src.id = rid
    db.add(src)
    db.commit()
    return src


def _add_wiki_page(db, raw_source_id, title, page_id="page-x", tags=None):
    page = WikiPage(
        page_id=page_id,
        page_type="knowledge",
        title=title,
        markdown_path="/tmp/test.md",
        source_refs_json=json.dumps([{"raw_source_id": raw_source_id, "url": "https://example.com"}]),
        tags_json=json.dumps(tags or ["manual", "知识页面"]),
        status="active",
    )
    db.add(page)
    db.commit()
    return page


class FakeEdgeProvider:
    """Returns wiki markdown for summarize, JSON for edge building."""

    def __init__(self, edge_response=None):
        self.edge_response = edge_response
        self.edge_prompt = ""

    async def chat(self, messages, model, temperature=0.2):
        user_msg = ""
        for m in messages:
            if m["role"] == "user":
                user_msg = m["content"]
        if "知识关联" in user_msg:
            self.edge_prompt = user_msg
            if self.edge_response is None:
                raise RuntimeError("no edge response configured")
            return self.edge_response
        return "## 核心观点\n\n- 这是一条测试知识。\n"


class FailingEdgeProvider:
    async def chat(self, messages, model, temperature=0.2):
        raise RuntimeError("model unavailable")


def test_wiki_creation_builds_graph_edges_with_llm(tmp_path, monkeypatch):
    db = make_session()
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)

    src_a = _add_raw_source(db, title="已有资料A", rid=1)
    _add_wiki_page(db, src_a.id, "已有知识A", page_id="page-a")
    src_b = _add_raw_source(db, title="新资料B", rid=2)

    edge_json = json.dumps([{"page_id": "page-a", "relation": "topic_overlap", "reason": "都讲AI", "confidence": 0.86, "concepts": ["AI", "产品经理"]}])
    fake = FakeEdgeProvider(edge_response=edge_json)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (fake, "fake-model", {}))

    new_page = asyncio.run(WikiMaintenanceService(db).create_page_from_raw_source(src_b.id, page_type="knowledge"))

    edges = db.query(KnowledgeGraphEdge).all()
    assert len(edges) >= 1
    edge = edges[0]
    assert edge.source_page_id == new_page.page_id
    assert edge.target_page_id == "page-a"
    assert edge.relation == "topic_overlap"
    assert edge.weight == 0.86
    assert json.loads(edge.shared_concepts_json) == ["AI", "产品经理"]
    assert "已有知识A" in fake.edge_prompt
    assert "只连接强相关" in fake.edge_prompt


def test_wiki_edge_building_does_not_fallback_to_tag_edges_on_llm_failure(tmp_path, monkeypatch):
    db = make_session()
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)

    src_a = _add_raw_source(db, title="已有资料A", rid=1)
    _add_wiki_page(db, src_a.id, "已有知识A", page_id="page-a", tags=["bilibili", "知识页面"])
    src_b = _add_raw_source(db, title="新资料B", rid=2)

    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FailingEdgeProvider(), "fake-model", {}))

    asyncio.run(WikiMaintenanceService(db).create_page_from_raw_source(src_b.id, page_type="knowledge"))

    assert db.query(KnowledgeGraphEdge).count() == 0


def test_wiki_edge_building_no_edges_when_only_one_page(tmp_path, monkeypatch):
    db = make_session()
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)

    src = _add_raw_source(db, title="唯一资料", rid=1)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeEdgeProvider(edge_response="[]"), "fake-model", {}))

    asyncio.run(WikiMaintenanceService(db).create_page_from_raw_source(src.id, page_type="knowledge"))

    edges = db.query(KnowledgeGraphEdge).all()
    assert len(edges) == 0


def test_graph_service_does_not_infer_edges_from_tags_without_saved_ai_edges():
    from app.services.graph_service import GraphService

    db = make_session()
    src_a = _add_raw_source(db, title="已有资料A", rid=1)
    src_b = _add_raw_source(db, title="已有资料B", rid=2)
    page_a = _add_wiki_page(db, src_a.id, "已有知识A", page_id="page-a", tags=["AI", "知识页面"])
    page_b = _add_wiki_page(db, src_b.id, "已有知识B", page_id="page-b", tags=["AI", "知识页面"])

    data = GraphService(db).get_graph_data()

    assert {node["id"] for node in data["nodes"]} == {page_a.page_id, page_b.page_id}
    assert data["edges"] == []


def test_create_edge_dedupes_reverse_pair():
    db = make_session()
    svc = WikiMaintenanceService(db)

    svc._create_edge("page-a", "page-b", "topic_overlap", "A 和 B 相关", 0.9, ["策略"])
    svc._create_edge("page-b", "page-a", "topic_overlap", "B 和 A 相关", 0.88, ["策略"])

    edge = db.query(KnowledgeGraphEdge).one()
    assert edge.source_page_id == "page-a"
    assert edge.target_page_id == "page-b"
    assert edge.weight == 0.9


def test_graph_service_hides_existing_reverse_duplicate_edges():
    from app.services.graph_service import GraphService

    db = make_session()
    src_a = _add_raw_source(db, title="已有资料A", rid=1)
    src_b = _add_raw_source(db, title="已有资料B", rid=2)
    _add_wiki_page(db, src_a.id, "已有知识A", page_id="page-a")
    _add_wiki_page(db, src_b.id, "已有知识B", page_id="page-b")
    db.add(KnowledgeGraphEdge(source_page_id="page-a", target_page_id="page-b", relation="topic_overlap", weight=0.91))
    db.add(KnowledgeGraphEdge(source_page_id="page-b", target_page_id="page-a", relation="topic_overlap", weight=0.9))
    db.commit()

    data = GraphService(db).get_graph_data()

    assert len(data["edges"]) == 1
    assert data["edges"][0]["source"] == "page-a"
    assert data["edges"][0]["target"] == "page-b"


def test_rebuild_graph_uses_ai_edges_for_existing_wiki_pages(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app

    db = make_session()
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)

    src_a = _add_raw_source(db, title="已有资料A", rid=1)
    _add_wiki_page(db, src_a.id, "已有知识A", page_id="page-a", tags=["AI", "知识页面"])
    src_b = _add_raw_source(db, title="已有资料B", rid=2)
    _add_wiki_page(db, src_b.id, "已有知识B", page_id="page-b", tags=["AI", "知识页面"])

    fake = FakeEdgeProvider(edge_response=json.dumps([{"page_id": "page-a", "relation": "extends", "reason": "B 延伸 A", "confidence": 0.91, "concepts": ["策略"]}]))
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (fake, "fake-model", {}))

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/graph/rebuild")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["edges_created"] == 1
    edge = db.query(KnowledgeGraphEdge).one()
    assert edge.source_page_id == "page-b"
    assert edge.target_page_id == "page-a"
    assert edge.relation == "extends"
    assert edge.weight == 0.91


def test_graph_api_includes_wiki_generated_edges(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app

    db = make_session()
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)

    src_a = _add_raw_source(db, title="已有资料A", rid=1)
    page_a = _add_wiki_page(db, src_a.id, "已有知识A", page_id="page-a")
    src_b = _add_raw_source(db, title="新资料B", rid=2)

    edge_json = json.dumps([{"page_id": "page-a", "relation": "topic_overlap", "reason": "相关", "confidence": 0.82, "concepts": ["知识关联"]}])
    fake = FakeEdgeProvider(edge_response=edge_json)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (fake, "fake-model", {}))

    new_page = asyncio.run(WikiMaintenanceService(db).create_page_from_raw_source(src_b.id, page_type="knowledge"))

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/api/graph")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert len(data["edges"]) >= 1
    all_node_ids = {e["source"] for e in data["edges"]} | {e["target"] for e in data["edges"]}
    assert new_page.page_id in all_node_ids
    assert page_a.page_id in all_node_ids
