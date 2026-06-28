from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_creator_mode_page_returns_200():
    """访问 /ui/sync?mode=creator 应返回 200"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync?mode=creator")

        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_creator_mode_has_platform_selection():
    """响应中应包含"抖音"和"小红书"平台选择元素"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync?mode=creator")

        assert response.status_code == 200
        # 检查平台选择元素
        assert 'data-creator-platform-tab="douyin"' in response.text or 'data-platform-tab="douyin"' in response.text
        assert 'data-creator-platform-tab="xiaohongshu"' in response.text or 'data-platform-tab="xiaohongshu"' in response.text
        assert "抖音" in response.text
        assert "小红书" in response.text
    finally:
        app.dependency_overrides.clear()


def test_creator_mode_has_creator_input_and_scan_button():
    """响应中应包含博主输入框和扫描按钮"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync?mode=creator")

        assert response.status_code == 200
        # 检查博主输入框
        assert 'data-creator-input' in response.text or 'name="creator_url"' in response.text
        # 检查扫描按钮
        assert 'data-creator-scan' in response.text or 'data-filter-scan' in response.text
    finally:
        app.dependency_overrides.clear()


def test_creator_mode_has_works_list_and_extract_button():
    """响应中应包含作品列表容器和提取按钮"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync?mode=creator")

        assert response.status_code == 200
        # 检查作品列表容器
        assert 'data-creator-results' in response.text or 'data-filter-results' in response.text
        # 检查提取按钮
        assert 'data-creator-extract' in response.text or 'data-filter-extract' in response.text
    finally:
        app.dependency_overrides.clear()


def test_creator_mode_no_history_or_incremental_tabs():
    """响应中不应包含"历史收藏"或"新增收藏"字样（因为这些模式对博主蒸馏不适用）"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync?mode=creator")

        assert response.status_code == 200
        # 不应包含历史收藏或新增收藏
        assert "历史收藏" not in response.text
        assert "新增收藏" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_creator_mode_js_helper_exists():
    """验证 app.js 中包含 initCreatorDistillPanel 函数"""
    app_js = open("app/static/app.js", encoding="utf-8").read()

    assert "function initCreatorDistillPanel" in app_js
