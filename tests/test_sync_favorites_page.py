from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import ProductEvent


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_sync_favorites_page_lists_platforms_and_management_links():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync")

        assert response.status_code == 200
        assert "同步收藏夹" in response.text
        assert "抖音" in response.text
        assert "小红书" in response.text
        assert "管理" in response.text
        assert 'href="/ui/source-setup/douyin"' in response.text
        assert 'href="/ui/source-setup/xiaohongshu"' in response.text
        assert "/douyin/favorites/extract" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_source_setup_pages_show_only_supported_extract_controls():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        douyin = client.get("/ui/source-setup/douyin")
        xiaohongshu = client.get("/ui/source-setup/xiaohongshu")
        bilibili = client.get("/ui/source-setup/bilibili")

        assert douyin.status_code == 200
        assert "/api/collect-and-extract/douyin" in douyin.text
        assert "一键采集" in douyin.text

        assert xiaohongshu.status_code == 200
        assert "/api/collect-and-extract/xiaohongshu" in xiaohongshu.text

        assert bilibili.status_code == 200
        assert "/api/collect-and-extract/bilibili" in bilibili.text

        # Unsupported platform should show "待接入"
        reddit = client.get("/ui/source-setup/reddit")
        assert reddit.status_code == 200
        assert "待接入" in reddit.text
    finally:
        app.dependency_overrides.clear()


def test_v3_favorites_entry_redirects_to_sync_page():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/ui/v3/input", data={"content": "", "entry_mode": "favorites"}, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/ui/sync"
        assert db.query(ProductEvent).filter(ProductEvent.event_name == "v3_primary_input_submitted").count() == 1
    finally:
        app.dependency_overrides.clear()
