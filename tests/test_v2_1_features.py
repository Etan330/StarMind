"""Tests for new V2.1 features: CDP proxy, graph, push, lint, onboarding, scheduler."""
import asyncio
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import (
    ChatConversation, ChatMessage, Connector, CandidateItem, KnowledgeClassification, KnowledgeGraphEdge,
    OnboardingStatus, PushHistory, PushSettings, RawSource, UserPreference,
)


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_new_models_create():
    db = make_session()
    # OnboardingStatus
    db.add(OnboardingStatus(current_step=3, skipped=False))
    # UserPreference
    db.add(UserPreference(domain="AI/大模型", score=85))
    # PushSettings
    db.add(PushSettings(start_time="09:00", end_time="21:00", frequency_hours=2, items_per_push=3, is_paused=False))
    db.commit()

    assert db.query(OnboardingStatus).first().current_step == 3
    assert db.query(UserPreference).first().score == 85
    assert db.query(PushSettings).first().frequency_hours == 2


def test_knowledge_graph_edge():
    db = make_session()
    # Need raw sources first
    src1 = RawSource(platform="bilibili", source_url="https://bilibili.com/video/BV1", canonical_url="https://bilibili.com/video/BV1",
                     external_item_id="BV1", source_type="video", title="RAG 入门")
    src2 = RawSource(platform="bilibili", source_url="https://bilibili.com/video/BV2", canonical_url="https://bilibili.com/video/BV2",
                     external_item_id="BV2", source_type="video", title="向量数据库")
    db.add_all([src1, src2])
    db.commit()

    edge = KnowledgeGraphEdge(source_id=src1.id, target_id=src2.id, relation="topic_overlap", weight=0.85,
                              shared_concepts_json=json.dumps(["RAG", "向量"]))
    db.add(edge)
    db.commit()

    assert db.query(KnowledgeGraphEdge).count() == 1
    assert db.query(KnowledgeGraphEdge).first().weight == 0.85


def test_graph_service_detect_orphans():
    db = make_session()
    src1 = RawSource(platform="test", source_url="u1", canonical_url="u1", external_item_id="e1", source_type="video", title="A")
    src2 = RawSource(platform="test", source_url="u2", canonical_url="u2", external_item_id="e2", source_type="video", title="B")
    src3 = RawSource(platform="test", source_url="u3", canonical_url="u3", external_item_id="e3", source_type="video", title="C")
    db.add_all([src1, src2, src3])
    db.commit()

    # Only connect src1 and src2
    db.add(KnowledgeGraphEdge(source_id=src1.id, target_id=src2.id, relation="topic_overlap", weight=0.5))
    db.commit()

    from app.services.graph_service import GraphService
    orphans = GraphService(db).detect_orphans()
    assert src3.id in orphans
    assert src1.id not in orphans


def test_graph_service_get_graph_data():
    db = make_session()
    src1 = RawSource(platform="test", source_url="u1", canonical_url="u1", external_item_id="e1", source_type="video", title="Topic A",
                     metadata_json=json.dumps({"domain": "AI", "related_concepts": ["RAG"]}))
    src2 = RawSource(platform="test", source_url="u2", canonical_url="u2", external_item_id="e2", source_type="video", title="Topic B",
                     metadata_json=json.dumps({"domain": "AI", "related_concepts": ["RAG"]}))
    db.add_all([src1, src2])
    db.commit()
    db.add(KnowledgeGraphEdge(source_id=src1.id, target_id=src2.id, relation="topic_overlap", weight=0.8, shared_concepts_json='["RAG"]'))
    db.commit()

    from app.services.graph_service import GraphService
    data = GraphService(db).get_graph_data()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["weight"] == 0.8


def test_push_service_no_crash_empty():
    db = make_session()
    from app.services.push_service import PushService
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(PushService(db).generate_push())
    loop.close()
    assert result == []


def test_push_schedule_api_persists_empty_time_list():
    db = make_session()
    db.add(PushSettings(push_days="1,2,3", push_time="09:00,18:00"))
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/push/schedule", json={"days": [4, 5], "times": []})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    settings = db.query(PushSettings).first()
    assert settings.push_days == "4,5"
    assert settings.push_time == ""


def test_global_form_busy_handler_respects_no_busy_forms():
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")

    no_busy_check = 'form.hasAttribute("data-no-busy")' in app_js or "form.dataset.noBusy" in app_js
    busy_check_position = app_js.index("button.dataset.busyApplied")
    first_no_busy_position = min(
        [position for position in [app_js.find('form.hasAttribute("data-no-busy")'), app_js.find("form.dataset.noBusy")] if position != -1],
        default=-1,
    )

    assert no_busy_check
    assert first_no_busy_position != -1
    assert first_no_busy_position < busy_check_position


def test_dashboard_uses_smooth_layered_graph_chat_layout():
    dashboard = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert "graph-drawer-open" in dashboard
    assert "home-container chat-first" in dashboard
    assert "graph-drawer-toggle" in dashboard
    assert "transition:transform .32s" in dashboard
    assert "pointer-events:none" in dashboard
    assert "textarea id=\"chat-input\"" in dashboard
    assert "newBtn.onclick=function(){cid=null;mb.innerHTML='';loadConversations();ip.focus();};" in dashboard
    assert "g.addEventListener('pointerenter'" not in dashboard


def test_dashboard_has_restartable_precise_onboarding_tour():
    dashboard = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    base = (Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html").read_text(encoding="utf-8")

    assert "本地优先 · 数据只在你的设备" not in base
    assert "id=\"restart-tour-btn\"" in base
    assert "id=\"restart-tour-btn\"" not in dashboard
    assert "StarMind_onboarding_completed" in dashboard
    assert "tourSteps" in dashboard
    assert "#chat-messages" in dashboard
    assert "#chat-input" in dashboard
    assert "#chat-new-btn" in dashboard
    assert ".home-entries" in dashboard
    assert "#graph-drawer" in dashboard
    assert "graphDrawer:true" in dashboard
    assert "setHomeGraphDrawerOpen" in dashboard
    assert "scrollIntoView" not in dashboard
    assert "restoreTourViewport" in dashboard
    assert "a[href='/ui/wiki']" in dashboard
    assert "a[href='/ui/push-settings']" in dashboard
    assert "跳过" in dashboard
    assert "这里是 StarMind 的主工作区" not in dashboard
    assert "系统会优先使用知识库内容回答" not in dashboard


def test_conversation_list_hides_empty_conversations():
    db = make_session()
    empty = ChatConversation(id="empty", title="新对话")
    used = ChatConversation(id="used", title="已有内容")
    db.add_all([empty, used])
    db.flush()
    db.add(ChatMessage(conversation_id="used", role="user", content="你好"))
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/api/conversations")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["used"]


def test_connector_auto_sync_fields():
    db = make_session()
    c = Connector(name="Test", platform="bilibili", connector_type="browser_bilibili", auto_sync_enabled=True, auto_sync_cron="0 0 * * *")
    db.add(c)
    db.commit()
    assert db.query(Connector).first().auto_sync_enabled is True
    assert db.query(Connector).first().auto_sync_cron == "0 0 * * *"


def test_lint_agent_runs_without_error():
    db = make_session()
    from app.agent.lint_agent import LintAgent
    loop = asyncio.new_event_loop()
    report = loop.run_until_complete(LintAgent(db).run_full_check())
    loop.close()
    assert "findings" in report
    assert report["total"] == 0  # empty DB = no findings
