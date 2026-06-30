"""Wiki 页面批量删除应走回收站（软删除 + RecycleBinItem），恢复时还原状态。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import RecycleBinItem, WikiPage


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _add_wiki_page(db, page_id="page-a", title="测试页面", status="active"):
    page = WikiPage(
        page_id=page_id,
        page_type="knowledge",
        title=title,
        markdown_path="/tmp/test.md",
        status=status,
    )
    db.add(page)
    db.commit()
    return page


def test_wiki_batch_delete_creates_recycle_item_and_soft_deletes():
    db = make_session()
    page = _add_wiki_page(db, page_id="page-a", title="测试页面")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/wiki/batch-delete", json={"page_ids": ["page-a"]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["count"] == 1

    # Page should still exist but with status=deleted
    db.refresh(page)
    assert page.status == "deleted"

    # RecycleBinItem should be created with item_type=wiki_page
    recycle_item = db.query(RecycleBinItem).filter(RecycleBinItem.page_id == "page-a").first()
    assert recycle_item is not None
    assert recycle_item.item_type == "wiki_page"
    assert recycle_item.title == "测试页面"
    assert recycle_item.status == "archived"


def test_wiki_batch_delete_does_not_hard_delete_page():
    db = make_session()
    _add_wiki_page(db, page_id="page-b")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        client.post("/api/wiki/batch-delete", json={"page_ids": ["page-b"]})
    finally:
        app.dependency_overrides.clear()

    # Page row must still exist (soft delete)
    page = db.query(WikiPage).filter(WikiPage.page_id == "page-b").first()
    assert page is not None
    assert page.status == "deleted"


def test_restore_wiki_page_sets_status_active():
    db = make_session()
    _add_wiki_page(db, page_id="page-c", title="待恢复页面", status="deleted")
    recycle_item = RecycleBinItem(
        item_type="wiki_page",
        page_id="page-c",
        canonical_url="",
        external_item_id="",
        title="待恢复页面",
        platform="wiki",
        reason="user_deleted",
    )
    db.add(recycle_item)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(f"/recycle/{recycle_item.id}/restore", data={"target": "wiki"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code in (200, 303)

    page = db.query(WikiPage).filter(WikiPage.page_id == "page-c").first()
    assert page is not None
    assert page.status == "active"

    db.refresh(recycle_item)
    assert recycle_item.status == "restored"


def test_recycle_page_shows_wiki_items():
    db = make_session()
    recycle_item = RecycleBinItem(
        item_type="wiki_page",
        page_id="page-d",
        canonical_url="",
        external_item_id="",
        title="回收站里的Wiki",
        platform="wiki",
        reason="user_deleted",
    )
    db.add(recycle_item)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/recycle")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.text
    assert "回收站里的Wiki" in body
    assert "page-d" in body or "wiki" in body.lower()


def test_wiki_batch_delete_confirm_mentions_recycle_bin():
    wiki_html = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app" / "templates" / "wiki.html"
    ).read_text(encoding="utf-8")
    # The confirm or tooltip should mention recycle bin / recovery
    assert "回收站" in wiki_html or "可恢复" in wiki_html


def test_recycle_bin_item_has_item_type_and_page_id_columns():
    """Model should expose item_type and page_id fields."""
    db = make_session()
    item = RecycleBinItem(
        item_type="wiki_page",
        page_id="page-e",
        canonical_url="",
        external_item_id="",
        title="字段测试",
        platform="wiki",
        reason="user_deleted",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    assert item.item_type == "wiki_page"
    assert item.page_id == "page-e"
