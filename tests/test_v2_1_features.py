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
    OnboardingStatus, PushHistory, PushSettings, RawSource, UserPreference, WikiCategory, WikiPage,
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
    page1 = WikiPage(page_id="page-rag", page_type="knowledge", title="RAG 入门", markdown_path="/tmp/rag.md")
    page2 = WikiPage(page_id="page-vector", page_type="knowledge", title="向量数据库", markdown_path="/tmp/vector.md")
    db.add_all([page1, page2])
    db.commit()

    edge = KnowledgeGraphEdge(source_page_id=page1.page_id, target_page_id=page2.page_id, relation="topic_overlap", weight=0.85,
                              shared_concepts_json=json.dumps(["RAG", "向量"]))
    db.add(edge)
    db.commit()

    assert db.query(KnowledgeGraphEdge).count() == 1
    assert db.query(KnowledgeGraphEdge).first().weight == 0.85


def test_graph_service_detect_orphans():
    db = make_session()
    page1 = WikiPage(page_id="page-a", page_type="knowledge", title="A", markdown_path="/tmp/a.md")
    page2 = WikiPage(page_id="page-b", page_type="knowledge", title="B", markdown_path="/tmp/b.md")
    page3 = WikiPage(page_id="page-c", page_type="knowledge", title="C", markdown_path="/tmp/c.md")
    db.add_all([page1, page2, page3])
    db.commit()

    # Only connect page1 and page2
    db.add(KnowledgeGraphEdge(source_page_id=page1.page_id, target_page_id=page2.page_id, relation="topic_overlap", weight=0.5))
    db.commit()

    from app.services.graph_service import GraphService
    data = GraphService(db).get_graph_data()
    connected = {edge["source"] for edge in data["edges"]} | {edge["target"] for edge in data["edges"]}
    assert page3.page_id not in connected
    assert page1.page_id in connected


def test_graph_service_get_graph_data():
    db = make_session()
    page1 = WikiPage(page_id="page-topic-a", page_type="knowledge", title="Topic A", markdown_path="/tmp/a.md", tags_json=json.dumps(["AI", "RAG"]))
    page2 = WikiPage(page_id="page-topic-b", page_type="knowledge", title="Topic B", markdown_path="/tmp/b.md", tags_json=json.dumps(["AI", "RAG"]))
    db.add_all([page1, page2])
    db.commit()
    db.add(KnowledgeGraphEdge(source_page_id=page1.page_id, target_page_id=page2.page_id, relation="topic_overlap", weight=0.8, shared_concepts_json='["RAG"]'))
    db.commit()

    from app.services.graph_service import GraphService
    data = GraphService(db).get_graph_data()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["weight"] == 0.8


def test_graph_nodes_use_exact_wiki_page_id():
    db = make_session()
    page = WikiPage(page_id="page-exact", page_type="knowledge", title="Exact Topic", markdown_path="/tmp/exact.md")
    db.add(page)
    db.commit()

    from app.services.graph_service import GraphService
    data = GraphService(db).get_graph_data()

    node = next(node for node in data["nodes"] if node["id"] == page.page_id)
    assert node["id"] == page.page_id


def test_push_service_no_crash_empty():
    db = make_session()
    from app.services.push_service import PushService
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(PushService(db).generate_push())
    loop.close()
    assert result == []


def test_scheduler_push_service_deduplicates_same_minute_requests(monkeypatch):
    db = make_session()
    db.add(PushSettings(total_push_count=0, items_per_push=3))
    cat = WikiCategory(name="AI", slug="ai")
    db.add(cat)
    db.flush()
    db.add_all([
        UserPreference(domain="AI", score=100),
        WikiPage(page_id="ai-1", page_type="knowledge", title="AI 1", markdown_path="/tmp/ai1.md", category_id=cat.id),
        WikiPage(page_id="ai-2", page_type="knowledge", title="AI 2", markdown_path="/tmp/ai2.md", category_id=cat.id),
    ])
    db.commit()
    monkeypatch.setattr("app.services.push_scheduler_service.random.choices", lambda population, weights, k: ["AI"])
    monkeypatch.setattr("app.services.push_scheduler_service.random.choice", lambda items: items[0])

    from app.services.push_scheduler_service import PushSchedulerService

    service = PushSchedulerService(db)
    first = asyncio.run(service.generate_push_items())
    second = asyncio.run(service.generate_push_items())

    assert len(first) == 1
    assert second == []
    assert db.query(PushHistory).count() == 1
    assert db.query(PushSettings).first().total_push_count == 1


def test_scheduler_push_service_returns_one_weighted_category_item(monkeypatch):
    db = make_session()
    db.add(PushSettings(total_push_count=4, items_per_push=3))
    ai = WikiCategory(name="AI", slug="ai")
    career = WikiCategory(name="职场", slug="career")
    db.add_all([ai, career])
    db.flush()
    db.add_all([
        UserPreference(domain="AI", score=80),
        UserPreference(domain="职场", score=20),
        WikiPage(page_id="ai-1", page_type="knowledge", title="AI 1", markdown_path="/tmp/ai1.md", category_id=ai.id),
        WikiPage(page_id="ai-2", page_type="knowledge", title="AI 2", markdown_path="/tmp/ai2.md", category_id=ai.id),
        WikiPage(page_id="career-1", page_type="knowledge", title="职场 1", markdown_path="/tmp/c1.md", category_id=career.id),
    ])
    db.commit()

    choices_calls = []

    def fake_choices(population, weights, k):
        choices_calls.append((list(population), list(weights), k))
        return ["AI"]

    monkeypatch.setattr("app.services.push_scheduler_service.random.choices", fake_choices)
    monkeypatch.setattr("app.services.push_scheduler_service.random.choice", lambda items: items[0])

    from app.services.push_scheduler_service import PushSchedulerService

    result = asyncio.run(PushSchedulerService(db).generate_push_items())

    assert len(result) == 1
    assert result[0]["title"] == "AI 1"
    assert result[0]["category"] == "AI"
    assert result[0]["show_feedback"] is True
    assert db.query(PushHistory).count() == 1
    assert db.query(PushSettings).first().total_push_count == 5
    assert choices_calls == [(["AI", "职场"], [0.8, 0.2], 1)]


def test_server_scheduler_does_not_generate_desktop_pushes():
    scheduler_py = (Path(__file__).resolve().parents[1] / "app" / "scheduler.py").read_text(encoding="utf-8")
    push_job = scheduler_py[scheduler_py.index("async def push_check_job"):scheduler_py.index("def _schedule_retry")]

    assert "generate_push_items" not in push_job
    assert "server-side desktop notifications are browser-owned" in push_job


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


def test_page_load_does_not_poll_pending_feedback():
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "fetch('/api/push/pending-feedback')" not in app_js
    assert 'fetch("/api/push/pending-feedback")' not in app_js


def test_graph_page_builds_links_from_node_ids():
    graph = (Path(__file__).resolve().parents[1] / "app" / "templates" / "graph.html").read_text(encoding="utf-8")

    assert "https://cdn.jsdelivr.net/npm/echarts" not in graph
    assert '<script src="/static/echarts.min.js' in graph
    assert "function openWikiPage(pageId)" in graph
    assert "function getClickedPageId(params)" in graph
    assert "window.location.assign(`/ui/wiki?page_id=${encodeURIComponent(pageId)}`)" in graph
    assert "chart.on('click'" in graph
    assert "getClickedPageId(params)" in graph
    assert "cursor: 'pointer'" in graph
    assert "draggable: false" in graph


def test_dashboard_graph_drawer_builds_links_from_node_ids():
    dashboard = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert "https://cdn.jsdelivr.net/npm/echarts" not in dashboard
    assert '<script src="/static/echarts.min.js' in dashboard
    assert "function openWikiPage(pageId)" in dashboard
    assert "function getClickedPageId(params)" in dashboard
    assert "window.location.assign('/ui/wiki?page_id='+encodeURIComponent(pageId))" in dashboard
    assert "ch.on('click'" in dashboard
    assert "getClickedPageId(params)" in dashboard
    assert "cursor:'pointer'" in dashboard
    assert "draggable:false" in dashboard


def test_pending_feedback_endpoint_disabled_for_legacy_cached_js():
    db = make_session()
    db.add(PushHistory(raw_source_id=1, category_name="AI", feedback_requested=True))
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/api/push/pending-feedback")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []


def test_base_uses_versioned_app_js_to_bust_browser_cache():
    base = (Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html").read_text(encoding="utf-8")

    assert "/static/app.js?v=" in base


def test_feedback_banner_is_only_driven_by_push_items():
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "if (item.show_feedback)" in app_js
    assert "showFeedbackBanner(item)" in app_js
    assert "data-push-feedback-banner" in app_js


def test_push_polling_runs_immediately_and_then_frequently():
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "async function pollPushItems()" in app_js
    assert "pollPushItems();" in app_js
    assert "setInterval(pollPushItems, 10000)" in app_js


def test_push_polling_uses_cross_tab_owner_lock():
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "StarMind_push_poll_owner" in app_js
    assert "function ownsPushPolling()" in app_js
    assert "if (!ownsPushPolling()) return;" in app_js
    assert "< 15000" in app_js


def test_like_unlike_feedback_adjusts_category_preference():
    db = make_session()
    db.add(UserPreference(domain="AI", score=50))
    raw = RawSource(
        platform="manual",
        source_url="https://example.com/source",
        canonical_url="https://example.com/source",
        external_item_id="source",
        source_type="manual",
        title="source",
    )
    page = WikiPage(page_id="p1", page_type="knowledge", title="知识", markdown_path="/tmp/p1.md")
    db.add_all([raw, page])
    db.flush()
    db.add(PushHistory(raw_source_id=raw.id, wiki_page_id=page.id, category_name="AI", feedback_requested=True))
    db.commit()

    from app.services.push_scheduler_service import PushSchedulerService

    like_result = PushSchedulerService(db).handle_feedback(1, "like")
    assert like_result["new_score"] == 60
    assert db.query(UserPreference).filter(UserPreference.domain == "AI").one().score == 60

    unlike_result = PushSchedulerService(db).handle_feedback(1, "unlike")
    assert unlike_result["new_score"] == 50
    assert db.query(UserPreference).filter(UserPreference.domain == "AI").one().score == 50


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


def test_dashboard_replays_assistant_history_with_rendered_markdown():
    dashboard = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert "if(m.role==='assistant'&&m.content_html)d.innerHTML=m.content_html;else d.textContent=m.content;" in dashboard
    assert ".chat-bubble-ai .markdown-body" in dashboard


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


def test_history_page_is_removed():
    templates_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
    base = (templates_dir / "base.html").read_text(encoding="utf-8")

    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/ui/history")

    assert response.status_code == 404
    assert not (templates_dir / "history.html").exists()
    assert "/ui/history" not in base



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



def test_conversation_message_api_returns_agent_answer(monkeypatch):
    db = make_session()

    async def fake_answer_question(self, question):
        return type(
            "Answer",
            (),
            {
                "answer": f"回答：{question}",
                "sources": [{"title": "测试页面"}],
            },
        )()

    monkeypatch.setattr("app.api.routes.AgentRunner.answer_question", fake_answer_question)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        created = client.post("/api/conversations")
        response = client.post(f"/api/conversations/{created.json()['id']}/messages", json={"question": "知识库有什么"})
        messages = client.get(f"/api/conversations/{created.json()['id']}/messages")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200
    assert response.status_code == 200
    assert response.json()["content"] == "回答：知识库有什么"
    assert response.json()["sources"] == [{"title": "测试页面"}]
    assert [item["role"] for item in messages.json()] == ["user", "assistant"]


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
