from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import RawSource, RecycleBinItem


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _add_raw_source(db, title="待删除原始资料"):
    source = RawSource(
        candidate_id=None,
        platform="douyin",
        source_url="https://example.com/raw-source",
        canonical_url="https://example.com/raw-source",
        external_item_id="raw-source",
        source_type="link",
        title=title,
        metadata_json="{}",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def test_source_delete_route_moves_raw_source_to_recycle_bin():
    db = make_session()
    source = _add_raw_source(db)
    source_id = source.id

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app, follow_redirects=False)
        response = client.post(
            f"/api/sources/{source_id}/delete",
            headers={"accept": "text/html"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/sources?deleted=1"
    assert db.get(RawSource, source_id) is None
    recycle_item = db.query(RecycleBinItem).filter(RecycleBinItem.item_type == "raw_source").one()
    assert recycle_item.title == "待删除原始资料"
    assert recycle_item.source_label == "原始资料"


def test_source_batch_delete_route_moves_sources_to_recycle_bin():
    db = make_session()
    source_a = _add_raw_source(db, title="原始资料 A")
    source_b = _add_raw_source(db, title="原始资料 B")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/sources/batch-delete", json={"ids": [source_a.id, source_b.id]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert db.get(RawSource, source_a.id) is None
    assert db.get(RawSource, source_b.id) is None
    assert db.query(RecycleBinItem).filter(RecycleBinItem.item_type == "raw_source").count() == 2


def test_source_restore_route_returns_raw_source_and_removes_recycle_item():
    db = make_session()
    source = _add_raw_source(db, title="待恢复原始资料")
    source_id = source.id

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app, follow_redirects=False)
        delete_response = client.post(
            f"/api/sources/{source_id}/delete",
            headers={"accept": "text/html"},
        )
        recycle_item = db.query(RecycleBinItem).filter(RecycleBinItem.item_type == "raw_source").one()
        restore_response = client.post(
            f"/recycle/{recycle_item.id}/restore",
            headers={"accept": "application/json"},
        )
    finally:
        app.dependency_overrides.clear()

    assert delete_response.status_code == 303
    assert restore_response.status_code == 200
    restored_id = restore_response.json()["raw_source_id"]
    restored = db.get(RawSource, restored_id)
    assert restored is not None
    assert restored.title == "待恢复原始资料"
    assert restored.canonical_url == "https://example.com/raw-source"
    assert db.query(RecycleBinItem).filter(RecycleBinItem.item_type == "raw_source").count() == 0
