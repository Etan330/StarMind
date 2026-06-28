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


# Task 3: Creator Scan


def test_creator_scan_endpoint_returns_mocked_profile_items(monkeypatch):
    """POST /api/creator/scan 应调用博主扫描服务并返回作品列表，而不是 404 Not Found"""
    from app.services.creator_profile_service import CreatorProfileService

    async def fake_scan_profile(self, platform, profile_url):
        return {
            "status": "ok",
            "creator": {
                "creator_key": "douyin:abc123",
                "creator_name": "大牙大",
                "platform": platform,
                "profile_url": profile_url,
            },
            "snapshot": {
                "captured_count": 1,
                "overlap_count": 0,
                "top_liked_extension_count": 0,
            },
            "items": [
                {
                    "id": "work-1",
                    "title": "测试作品标题",
                    "url": "https://www.douyin.com/video/1",
                    "bucket": "latest",
                    "like_count": 10,
                    "comment_count": 2,
                    "collect_count": 1,
                }
            ],
        }

    monkeypatch.setattr(CreatorProfileService, "scan_profile", fake_scan_profile)
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/scan",
            json={"platform": "douyin", "creator_url": "https://v.douyin.com/abc123/"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["items"][0]["title"] == "测试作品标题"
    finally:
        app.dependency_overrides.clear()


def test_creator_prepare_selected_creates_distill_profile_candidates():
    """勾选博主作品后应创建 distill_profile 候选，并保留完整博主/作品 metadata"""
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/creator/prepare-selected",
            json={
                "platform": "douyin",
                "creator": {
                    "creator_key": "douyin:abc123",
                    "creator_name": "大牙大",
                    "creator_profile_id": "abc123",
                    "creator_profile_url": "https://v.douyin.com/abc123/",
                },
                "selected_items": [
                    {
                        "id": "work-1",
                        "title": "测试作品标题",
                        "url": "https://www.douyin.com/video/1",
                        "bucket": "latest",
                        "published_at": "2026-06-28",
                        "like_count": 10,
                        "comment_count": 2,
                        "collect_count": 1,
                        "cover_url": "https://example.com/cover.jpg",
                    }
                ],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["candidate_ids"]
        from app.models import CandidateItem
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "distill_profile"
        assert candidate.title == "测试作品标题"
        assert "creator_key" in candidate.metadata_json
        assert "like_count" in candidate.metadata_json
    finally:
        app.dependency_overrides.clear()


def test_select_creator_works_extends_top_liked_when_overlapping():
    """最新作品和高赞作品重合时，高赞列表应顺延补足不重复作品"""
    from app.services.creator_profile_service import CreatorProfileService

    works = []
    for index in range(1, 14):
        works.append(
            {
                "id": f"work-{index}",
                "title": f"作品 {index}",
                "url": f"https://example.com/work/{index}",
                "published_at": f"2026-06-{30 - index:02d}",
                "like_count": 1000 - index,
                "comment_count": index,
                "collect_count": index * 2,
            }
        )

    selected, snapshot = CreatorProfileService.select_creator_works(works, latest_limit=10, top_liked_limit=10)

    latest = [item for item in selected if item["bucket"] in ("latest", "both")]
    top_liked = [item for item in selected if item["bucket"] in ("top_liked", "both")]
    assert len(latest) == 10
    assert len(top_liked) == 3
    assert snapshot["overlap_count"] == 10
    assert snapshot["top_liked_extension_count"] == 3
