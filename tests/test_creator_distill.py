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


# ─── Task 2: Creator Input Normalization ───────────────────────────────────


def test_douyin_share_text_extracts_profile_url():
    """抖音分享文本应提取出 v.douyin.com 主页链接"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input(
        "douyin",
        "【抖音】长按复制此条消息，打开抖音搜索...\nhttps://v.douyin.com/abc123/\n查看TA的更多作品。",
    )
    assert result["input_type"] == "direct_link"
    assert result["profile_url"] == "https://v.douyin.com/abc123/"


def test_xiaohongshu_profile_url_accepted():
    """小红书主页 URL 应直接通过，不做额外解析"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input(
        "xiaohongshu",
        "https://www.xiaohongshu.com/user/profile/5f7febc7000000000101c14a?xsec_token=ABV",
    )
    assert result["input_type"] == "direct_link"
    assert "xiaohongshu.com" in result["profile_url"]


def test_pure_id_marked_lookup_required():
    """纯账号 ID 应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input("douyin", "41966650155")
    assert result["input_type"] == "lookup_required"


def test_pure_name_marked_lookup_required():
    """博主名应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input("douyin", "大牙大")
    assert result["input_type"] == "lookup_required"


def test_resolve_profile_endpoint_returns_direct_link():
    """POST /api/creator/resolve-profile 对抖音分享文本返回 direct_link"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/resolve-profile",
            json={"platform": "douyin", "value": "【抖音】长按复制此条消息，打开抖音搜索...\nhttps://v.douyin.com/xyz789/\n查看TA的更多作品。"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["input_type"] == "direct_link"
        assert body["profile_url"] == "https://v.douyin.com/xyz789/"
    finally:
        app.dependency_overrides.clear()


def test_resolve_profile_endpoint_returns_lookup_required():
    """POST /api/creator/resolve-profile 对纯 ID 返回 lookup_required"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/resolve-profile",
            json={"platform": "xiaohongshu", "value": "1004429614"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["input_type"] == "lookup_required"
    finally:
        app.dependency_overrides.clear()


# Task 2: Creator Input Normalization


def test_douyin_share_text_extracts_profile_url():
    """抖音分享文本应能提取出 v.douyin.com 主页 URL"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input(
        "douyin",
        "【抖音】长按复制此条消息，打开抖音搜索，查看TA的更多作品。\nhttps://v.douyin.com/abc123/\n查看TA的更多作品。",
    )
    assert result["input_type"] == "direct_link"
    assert "v.douyin.com" in result["profile_url"]


def test_xiaohongshu_profile_url_accepted():
    """小红书主页 URL 应直接通过为 direct_link"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input(
        "xiaohongshu",
        "https://www.xiaohongshu.com/user/profile/5f7febc7000000000101c14a",
    )
    assert result["input_type"] == "direct_link"
    assert "5f7febc7000000000101c14a" in result["profile_url"]


def test_pure_id_marked_lookup_required():
    """纯账号 ID（数字）应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input("douyin", "41966650155")
    assert result["input_type"] == "lookup_required"


def test_pure_name_marked_lookup_required():
    """纯博主名（无 URL）应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input("xiaohongshu", "大牙大")
    assert result["input_type"] == "lookup_required"


def test_resolve_profile_endpoint_returns_direct_link(tmp_path, monkeypatch):
    """POST /api/creator/resolve-profile 对直接链接返回 direct_link 状态"""
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/resolve-profile",
            json={"platform": "douyin", "value": "https://v.douyin.com/test/"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["input_type"] == "direct_link"
        assert "v.douyin.com" in body["profile_url"]
    finally:
        app.dependency_overrides.clear()


def test_resolve_profile_endpoint_returns_lookup_required(tmp_path, monkeypatch):
    """POST /api/creator/resolve-profile 对 ID/昵称返回 lookup_required 状态"""
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/resolve-profile",
            json={"platform": "xiaohongshu", "value": "An epsilon of llm"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["input_type"] == "lookup_required"
    finally:
        app.dependency_overrides.clear()


# --- Task 2: Creator Input Normalization ---

def test_douyin_share_text_extracts_profile_url():
    """抖音分享文本应能提取出 v.douyin.com 短链"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input(
        "douyin",
        "【抖音】长按复制此条消息，打开抖音搜索...\nhttps://v.douyin.com/abc123/\n查看TA的更多作品。",
    )
    assert result["input_type"] == "direct_link"
    assert "v.douyin.com/abc123" in result["profile_url"]


def test_xiaohongshu_profile_url_accepted():
    """小红书主页 URL 应直接通过"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input(
        "xiaohongshu",
        "https://www.xiaohongshu.com/user/profile/5f7febc7000000000101c14a",
    )
    assert result["input_type"] == "direct_link"
    assert "xiaohongshu.com" in result["profile_url"]


def test_pure_id_marked_lookup_required():
    """纯账号 ID 应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input("douyin", "41966650155")
    assert result["input_type"] == "lookup_required"

    result2 = CreatorProfileService.normalize_creator_input("xiaohongshu", "1004429614")
    assert result2["input_type"] == "lookup_required"


def test_pure_name_marked_lookup_required():
    """纯博主名应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService

    result = CreatorProfileService.normalize_creator_input("douyin", "大牙大")
    assert result["input_type"] == "lookup_required"

    result2 = CreatorProfileService.normalize_creator_input("xiaohongshu", "An epsilon of llm")
    assert result2["input_type"] == "lookup_required"


def test_resolve_profile_endpoint_exists():
    """POST /api/creator/resolve-profile 路由应存在并返回 JSON"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/resolve-profile",
            json={"platform": "douyin", "value": "41966650155"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["input_type"] == "lookup_required"
    finally:
        app.dependency_overrides.clear()


def test_resolve_profile_direct_link():
    """直接链接输入应返回 direct_link 类型"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/resolve-profile",
            json={
                "platform": "xiaohongshu",
                "value": "https://www.xiaohongshu.com/user/profile/5f7febc7000000000101c14a",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["input_type"] == "direct_link"
        assert "xiaohongshu.com" in body["profile_url"]
    finally:
        app.dependency_overrides.clear()


# Task 2: Creator Input Normalization
def test_douyin_share_text_extracts_profile_url():
    """抖音分享文本应解析出 v.douyin.com 链接"""
    from app.services.creator_profile_service import CreatorProfileService
    share_text = (
        "【抖音】长按复制此条消息，打开抖音搜索，查看TA的更多作品。\n"
        "https://v.douyin.com/abc123/\n"
        "查看TA的更多作品。"
    )
    result = CreatorProfileService.normalize_creator_input("douyin", share_text)
    assert result["input_type"] == "direct_link"
    assert result["profile_url"] == "https://v.douyin.com/abc123/"


def test_xiaohongshu_profile_url_accepted():
    """小红书主页 URL 应直接通过"""
    from app.services.creator_profile_service import CreatorProfileService
    url = "https://www.xiaohongshu.com/user/profile/5f7febc7000000000101c14a?xsec_token=ABLVFt9zJ3BBVGqtyaupLzpcBbDpMdgFpWJALIh5TTqog="
    result = CreatorProfileService.normalize_creator_input("xiaohongshu", url)
    assert result["input_type"] == "direct_link"
    assert "xiaohongshu.com" in result["profile_url"]


def test_pure_id_marked_lookup_required():
    """纯账号 ID 应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService
    result = CreatorProfileService.normalize_creator_input("douyin", "41966650155")
    assert result["input_type"] == "lookup_required"


def test_pure_name_marked_lookup_required():
    """纯博主名应标记为 lookup_required"""
    from app.services.creator_profile_service import CreatorProfileService
    result = CreatorProfileService.normalize_creator_input("douyin", "大牙大")
    assert result["input_type"] == "lookup_required"


def test_resolve_profile_endpoint_returns_correct_types():
    """resolve-profile 端点返回正确的 input_type"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        # 抖音分享文本 -> direct_link
        resp = client.post("/api/creator/resolve-profile", json={
            "platform": "douyin",
            "value": "【抖音】长按复制此条消息，打开抖音搜索，查看TA的更多作品。\nhttps://v.douyin.com/xyz789/\n查看TA的更多作品。"
        })
        assert resp.status_code == 200
        assert resp.json()["input_type"] == "direct_link"
        # 纯 ID -> lookup_required
        resp2 = client.post("/api/creator/resolve-profile", json={
            "platform": "douyin",
            "value": "41966650155"
        })
        assert resp2.status_code == 200
        assert resp2.json()["input_type"] == "lookup_required"
    finally:
        app.dependency_overrides.clear()
