from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import ImportedItem, ImportTask, SourceConnection, TranscriptRecord, WikiPage
from app.services.input_router_service import extract_urls
from app.services.v3_1_workflow_service import V31WorkflowService


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_v3_1_extracts_multiple_douyin_urls():
    content = """
    请同步这两个链接：
    https://www.douyin.com/jingxuan?modal_id=7648123596673550565
    https://v.douyin.com/52QJVFO5h5A/
    """

    urls = extract_urls(content)

    assert urls == [
        "https://www.douyin.com/jingxuan?modal_id=7648123596673550565",
        "https://v.douyin.com/52QJVFO5h5A/",
    ]


def test_v3_1_link_import_task_creates_items_transcripts_and_kb_entries():
    db = make_session()
    service = V31WorkflowService(db)

    task = service.create_link_import_task(
        [
            "https://www.douyin.com/jingxuan?modal_id=7648123596673550565",
            "https://v.douyin.com/52QJVFO5h5A/",
        ]
    )

    assert task.type == "link_import"
    assert task.status == "needs_confirmation"
    assert task.imported_count == 2
    assert task.saved_count == 0
    assert task.provider == "mock_import_adapter"

    items = db.query(ImportedItem).filter(ImportedItem.task_id == task.id).all()
    transcripts = db.query(TranscriptRecord).all()
    assert len(items) == 2
    assert len(transcripts) == 2
    assert all(item.selected for item in items)
    assert all(transcript.provider == "mock_transcript_adapter" for transcript in transcripts)
    assert "模拟逐字稿" in transcripts[0].content

    pages = service.save_task_to_knowledge(task.id)

    assert len(pages) == 2
    assert db.get(ImportTask, task.id).status == "saved_to_kb"
    assert db.query(WikiPage).count() == 2


def test_v3_1_favorites_sync_is_task_based_and_never_launches_external_popup():
    db = make_session()
    service = V31WorkflowService(db)

    source = service.ensure_source("douyin", "favorites")
    task = service.create_favorites_sync_task(source.id, latest_count=10)

    assert source.platform == "douyin"
    assert source.type == "favorites"
    assert task.type == "favorites_sync"
    assert task.status == "needs_confirmation"
    assert task.imported_count == 10
    assert task.saved_count == 6
    assert task.discarded_count == 3
    assert task.failed_count == 1
    assert task.external_popup_required is False
    assert db.query(SourceConnection).count() == 1


def test_v3_1_routes_cover_source_task_transcript_and_kb_loop():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        source_page = client.get("/ui/source-management")
        assert source_page.status_code == 200
        assert "来源管理" in source_page.text
        assert "一横条一个平台" in source_page.text
        assert "同步最新 10 条" in source_page.text

        create_response = client.post(
            "/v3-1/import-links",
            data={
                "links": "https://www.douyin.com/jingxuan?modal_id=7648123596673550565\nhttps://v.douyin.com/52QJVFO5h5A/",
            },
            follow_redirects=False,
        )
        assert create_response.status_code == 303
        result_location = create_response.headers["location"]
        assert result_location.startswith("/ui/import-result/")

        task = db.query(ImportTask).one()
        result_page = client.get(result_location)
        assert result_page.status_code == 200
        assert "导入结果摘要" in result_page.text
        assert "查看逐字稿 Markdown" in result_page.text

        transcript = db.query(TranscriptRecord).first()
        transcript_page = client.get(f"/ui/transcripts/{transcript.id}")
        assert transcript_page.status_code == 200
        assert "逐字稿" in transcript_page.text
        assert "复制 Markdown" in transcript_page.text

        save_response = client.post(f"/v3-1/tasks/{task.id}/save-to-kb", follow_redirects=False)
        assert save_response.status_code == 303
        assert save_response.headers["location"].startswith("/ui/wiki?created=v3-1-task")
        assert db.query(WikiPage).count() == 2
    finally:
        app.dependency_overrides.clear()
