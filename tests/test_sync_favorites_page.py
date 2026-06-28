import re

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
        assert "可执行预筛选" in response.text
        assert "/douyin/favorites/extract" not in response.text

        douyin_pos = response.text.index('href="/ui/source-setup/douyin"')
        tiktok_pos = response.text.index('href="/ui/source-setup/tiktok"')
        assert douyin_pos < response.text.index('href="/ui/source-setup/xiaohongshu"')
        assert tiktok_pos > response.text.index('href="/ui/source-setup/reddit"')
        assert 'src="https://cdn.simpleicons.org/tiktok/FFFFFF"' in response.text
    finally:
        app.dependency_overrides.clear()


def test_sync_favorites_page_moves_douyin_first_tiktok_last_and_hides_douyin_hint():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync")

        assert response.status_code == 200
        cards = re.findall(r'<div class="sync-card">(.*?)</div>\s*</div>', response.text, flags=re.S)
        assert len(cards) >= 3
        assert "抖音" in cards[0]
        assert "TikTok" in cards[-1]
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
        assert "收藏预筛选" in douyin.text
        assert "扫描标题" in douyin.text
        assert "AI 分类" in douyin.text
        assert "仅提取我勾选的内容" in douyin.text
        assert "高级：跳过筛选直接提取" in douyin.text

        assert xiaohongshu.status_code == 200
        assert "/api/collect-and-extract/xiaohongshu" in xiaohongshu.text
        assert "收藏预筛选" in xiaohongshu.text

        assert bilibili.status_code == 200
        assert "/api/collect-and-extract/bilibili" in bilibili.text
        assert "收藏预筛选" in bilibili.text

        # Unsupported platform should show "待接入"
        reddit = client.get("/ui/source-setup/reddit")
        assert reddit.status_code == 200
        assert "待接入" in reddit.text
    finally:
        app.dependency_overrides.clear()


def test_source_setup_bilibili_and_xiaohongshu_show_open_official_login_buttons_when_unsaved(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        bilibili = client.get("/ui/source-setup/bilibili")
        xiaohongshu = client.get("/ui/source-setup/xiaohongshu")

        assert bilibili.status_code == 200
        assert '/bilibili/browser/open' in bilibili.text
        assert '/source-connections/bilibili/save-current-page' in bilibili.text
        assert "打开 B站官网登录" in bilibili.text
        assert "保存当前收藏页" in bilibili.text
        assert "打开已保存收藏页" not in bilibili.text
        assert xiaohongshu.status_code == 200
        assert '/xiaohongshu/browser/open' in xiaohongshu.text
        assert '/source-connections/xiaohongshu/save-current-page' in xiaohongshu.text
        assert "打开小红书官网登录" in xiaohongshu.text
        assert "保存当前收藏页" in xiaohongshu.text
        assert "打开已保存收藏页" not in xiaohongshu.text
    finally:
        app.dependency_overrides.clear()


def test_source_setup_bilibili_and_xiaohongshu_show_saved_favorites_button_when_configured(monkeypatch):
    db = make_session()
    bilibili_url = "https://space.bilibili.com/351585377/favlist?fid=277411877&ftype=create"
    xiaohongshu_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"

    def override_get_db():
        yield db

    monkeypatch.setattr(
        "app.api.routes.get_source_connections",
        lambda: {
            "connections": {
                "bilibili": {"homepage_url": bilibili_url},
                "xiaohongshu": {"homepage_url": xiaohongshu_url},
            }
        },
    )
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        bilibili = client.get("/ui/source-setup/bilibili")
        xiaohongshu = client.get("/ui/source-setup/xiaohongshu")

        assert bilibili.status_code == 200
        assert "打开已保存收藏页" in bilibili.text
        assert "保存后可直接扫描，不需要再次从官网登录流程开始" in bilibili.text
        assert "https://space.bilibili.com/351585377/favlist?fid=277411877&amp;ftype=create" in bilibili.text
        assert xiaohongshu.status_code == 200
        assert "打开已保存收藏页" in xiaohongshu.text
        assert "保存后可直接扫描，不需要再次从官网登录流程开始" in xiaohongshu.text
        assert "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&amp;subTab=note" in xiaohongshu.text
    finally:
        app.dependency_overrides.clear()


def test_source_setup_marks_homepage_url_as_optional_fallback(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/source-setup/bilibili")

        assert response.status_code == 200
        assert "B站/小红书需保存登录后的真实收藏页" in response.text
        assert "登录并进入真实收藏页，再保存当前收藏页" in response.text
        assert "保存后下次可直接扫描" in response.text
        assert "data-source-homepage-input" in response.text
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


def test_sync_favorites_page_renders_live_platform_tabbar():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sync")

        assert response.status_code == 200
        text = response.text
        # 顶部平台标签栏 + 内联面板宿主
        assert "data-platform-tabs" in text
        assert "data-platform-panel-host" in text
        # 三个可执行平台各有标签
        assert 'data-platform-tab="douyin"' in text
        assert 'data-platform-tab="xiaohongshu"' in text
        assert 'data-platform-tab="bilibili"' in text
        # 未接入平台不上标签（TikTok 只在下方降级列表）
        assert 'data-platform-tab="tiktok"' not in text
        # 默认平台 = 抖音（排序后首位）
        assert 'data-default-platform="douyin"' in text
    finally:
        app.dependency_overrides.clear()


def test_source_setup_panel_endpoint_returns_fragment_only():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        panel = client.get("/ui/source-setup/douyin/panel")

        assert panel.status_code == 200
        text = panel.text
        # 是提取工作台片段
        assert "data-source-shell" in text
        assert "收藏预筛选" in text
        assert "data-source-setup-panel" in text
        # 不是整页：无 base.html 外壳
        assert "<!doctype" not in text.lower()
        assert "<nav" not in text

        # 未知平台 → 404（前端只对 live 平台请求）
        assert client.get("/ui/source-setup/definitely-not-a-platform/panel").status_code == 404
    finally:
        app.dependency_overrides.clear()

