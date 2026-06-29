import json
from pathlib import Path

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


def test_creator_results_js_groups_items_and_uses_compact_checkboxes():
    """扫描结果应先展示高赞、再展示最新，并使用小型左对齐勾选框"""
    app_js = open("app/static/app.js", encoding="utf-8").read()

    assert "高赞 10 条" in app_js
    assert "最新 10 条" in app_js
    assert app_js.index("高赞 10 条") < app_js.index("最新 10 条")
    assert "topLikedItems = scannedItems" in app_js
    assert "sort((a, b) => Number(b.like_count" in app_js
    assert "creator-work-checkbox" in app_js
    assert "creator-work-stats" in app_js
    assert "share_count" in app_js


def test_creator_result_groups_have_select_all_buttons():
    """高赞 10 条和最新 10 条分组都应支持各自全选"""
    app_js = open("app/static/app.js", encoding="utf-8").read()

    assert 'data-select-group="top_liked"' in app_js
    assert 'data-select-group="latest"' in app_js
    assert "selectCreatorGroup" in app_js
    assert "selectedItemIds = Array.from(new Set" in app_js


def test_creator_duplicate_work_checkboxes_stay_in_sync():
    """同一作品同时出现在高赞和最新时，两个复选框状态应同步"""
    app_js = open("app/static/app.js", encoding="utf-8").read()

    assert "syncCreatorCheckboxes" in app_js
    assert 'checkbox.value === itemId' in app_js
    assert "syncCreatorCheckboxes(checkbox.value, checkbox.checked)" in app_js


def test_creator_work_title_removes_leading_like_count_prefix():
    """作品标题不应把卡片上的点赞量/统计数字拼进标题开头"""
    from app.services.creator_profile_service import normalize_creator_work

    item = normalize_creator_work(
        "xiaohongshu",
        {
            "id": "work-1",
            "url": "https://www.xiaohongshu.com/user/profile/u/work-1",
            "title": "17.0万 Flipbook爆火：UI的未来是无限视觉 我这两天被这个爆火的Flipbook刷屏并被震撼到",
            "like_count": 170000,
        },
    )

    assert item["title"].startswith("Flipbook爆火：UI的未来是无限视觉")
    assert not item["title"].startswith("17.0万")


def test_creator_work_title_keeps_legitimate_numeric_prefix():
    """标题本身以年份/编号开头时不应被误删"""
    from app.services.creator_profile_service import normalize_creator_work

    item = normalize_creator_work(
        "xiaohongshu",
        {
            "id": "work-2",
            "url": "https://www.xiaohongshu.com/user/profile/u/work-2",
            "title": "2024 年最值得看的设计趋势",
        },
    )

    assert item["title"] == "2024 年最值得看的设计趋势"


def test_creator_panel_persists_last_scan_state_and_shows_profile_card():
    """离开后返回输入博主页，应能从 localStorage 恢复上次扫描结果，并展示博主基础信息"""
    app_js = open("app/static/app.js", encoding="utf-8").read()

    assert "creatorDistillState:v4" in app_js
    assert "saveCreatorState" in app_js
    assert "restoreCreatorState" in app_js
    assert "renderCreatorProfile" in app_js
    assert "粉丝" in app_js
    assert "获赞" in app_js
    assert "liked_count" in app_js
    assert "data-creator-profile" in app_js


def test_creator_extract_can_resume_after_human_verification():
    """博主作品提取遇到豆包/点点人机验证后，应显示反馈按钮并带 job_id 续跑"""
    template = open("app/templates/sync_favorites.html", encoding="utf-8").read()
    app_js = open("app/static/app.js", encoding="utf-8").read()

    assert "data-creator-resume" in template
    assert "我已完成验证，继续" in template
    assert "creatorResumeButton" in app_js
    assert "response.status === \"paused\"" in app_js
    assert "payload.job_id = currentJobId" in app_js
    assert "runCreatorExtraction" in app_js
    assert "pending_remaining" in app_js


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


def test_sources_page_groups_distill_raw_sources_by_creator(tmp_path, monkeypatch):
    """原始资料页应在博主蒸馏下按平台/博主名分组展示 RawSource"""
    from app.models import CandidateItem, RawSource

    db = make_session()
    transcript = tmp_path / "creator.txt"
    transcript.write_text("博主作品逐字稿", encoding="utf-8")
    candidate = CandidateItem(
        source_type="distill_profile",
        platform="douyin",
        external_item_id="work-1",
        canonical_url="https://www.douyin.com/video/1",
        raw_url="https://www.douyin.com/video/1",
        title="测试作品标题",
        author="大牙大",
        content_type="video",
        metadata_json=json.dumps({"creator_key": "douyin:abc123", "creator_name": "大牙大", "creator_platform": "douyin"}, ensure_ascii=False),
    )
    db.add(candidate)
    db.flush()
    db.add(
        RawSource(
            candidate_id=candidate.id,
            platform="douyin",
            source_url="https://www.douyin.com/video/1",
            canonical_url="https://www.douyin.com/video/1",
            external_item_id="work-1",
            source_type="distill_profile",
            title="测试作品标题",
            author="大牙大",
            transcript_path=str(transcript),
            metadata_json=candidate.metadata_json,
        )
    )
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sources")
        assert response.status_code == 200
        assert "博主蒸馏" in response.text
        assert "抖音" in response.text
        assert "大牙大" in response.text
        assert "测试作品标题" in response.text
        assert "已提取 1 条" in response.text
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_creator_work_script_prefers_profile_note_urls():
    """小红书博主页作品应使用能直接打开的 /user/profile/{user}/{note} 链接，而不是封面或 /explore 链接"""
    from app.services.creator_profile_service import _creator_work_extract_script

    script = _creator_work_extract_script("xiaohongshu")

    assert "/user/profile/[^/]+/[0-9a-fA-F]+" in script
    assert "normalizeXhsUrl" in script
    assert 'url: url.href' in script
    assert "cover_url: img?.src" in script
    assert "data:image" not in script


def test_creator_profile_info_normalizes_homepage_text_name_followers_and_likes():
    """主页上名字/粉丝数/获赞数即使分散在相邻文本里，也应归一化为可展示数据"""
    from app.services.creator_profile_service import normalize_creator_profile_info

    result = normalize_creator_profile_info(
        {
            "creator_name": "大牙大 - 抖音",
            "body_text": "获赞\n12.8万\n关注\n52\n粉丝\n3.4万",
        }
    )

    assert result["creator_name"] == "大牙大"
    assert result["follower_count"] == 34000
    assert result["liked_count"] == 128000

    douyin_precise = normalize_creator_profile_info(
        {
            "platform": "douyin",
            "creator_name": "架构师阿Q",
            "follower_text": "粉丝 1.3万",
            "liked_text": "获赞 3.9万",
        }
    )

    assert douyin_precise["creator_name"] == "架构师阿Q"
    assert douyin_precise["follower_count"] == 13000
    assert douyin_precise["liked_count"] == 39000

    xiaohongshu = normalize_creator_profile_info(
        {
            "creator_name": "孙沐晏_小红书",
            "body_text": "关注 30 粉丝 1170 获赞与收藏 2万",
            "platform": "xiaohongshu",
        }
    )

    assert xiaohongshu["creator_name"] == "孙沐晏"
    assert xiaohongshu["follower_count"] == 1170
    assert xiaohongshu["liked_count"] == 20000

    xiaohongshu_precise = normalize_creator_profile_info(
        {
            "platform": "xiaohongshu",
            "creator_name": "孙沐晏",
            "follower_count": "1170",
            "liked_count": "2万",
        }
    )

    assert xiaohongshu_precise["creator_name"] == "孙沐晏"
    assert xiaohongshu_precise["follower_count"] == 1170
    assert xiaohongshu_precise["liked_count"] == 20000

    body_only = normalize_creator_profile_info(
        {
            "body_text": "李云放而已\n关注\n64\n粉丝\n2877\n获赞\n21.4万\n抖音号：1002336398",
            "platform": "douyin",
        }
    )

    assert body_only["creator_name"] == "李云放而已"
    assert body_only["follower_count"] == 2877
    assert body_only["liked_count"] == 214000



def test_collect_creator_profile_retries_profile_after_work_scan(monkeypatch):
    """如果主页信息初次渲染为空，应在作品扫描后再兜底抓一次主页信息"""
    import asyncio
    import types

    from app.services import creator_profile_service as service

    class FakeCDP:
        def __init__(self):
            self.profile_calls = 0

        async def connect(self):
            return True

        async def new_tab(self, url):
            return types.SimpleNamespace(tab_id="tab-1")

        async def wait_for_load(self, tab, timeout=12):
            return None

        async def eval_script(self, tab, script):
            if "creator_name" in script and "body_text" in script:
                self.profile_calls += 1
                if self.profile_calls <= 8:
                    return '{"creator_name":"","body_text":""}'
                return '{"creator_name":"李云放而已","body_text":"李云放而已\\n关注\\n64\\n粉丝\\n2877\\n获赞\\n21.4万"}'
            return '[{"id":"work-1","url":"https://www.douyin.com/video/1","title":"测试作品","like_count":1}]'

        async def scroll(self, tab, distance=800):
            return None

        async def close_tab(self, tab):
            return None

    fake_cdp = FakeCDP()
    monkeypatch.setitem(__import__("sys").modules, "app.connectors.cdp_proxy", types.SimpleNamespace(cdp_proxy=fake_cdp))

    result = asyncio.run(service.collect_creator_profile_works("douyin", "https://www.douyin.com/user/test"))

    assert result["creator_name"] == "李云放而已"
    assert result["follower_count"] == 2877
    assert result["liked_count"] == 214000
    assert result["works"]



def test_scan_profile_returns_creator_name_and_follower_count(monkeypatch):
    """扫描主页时应返回博主名和粉丝数，供页面和 RawSource 分组使用"""
    from app.services.creator_profile_service import CreatorProfileService

    async def fake_collect(platform, profile_url):
        return {
            "creator_name": "大牙大",
            "follower_count": 123456,
            "works": [
                {
                    "id": "work-1",
                    "title": "测试作品标题",
                    "url": "https://www.douyin.com/video/1",
                    "published_at": "2026-06-28",
                    "like_count": 10,
                }
            ],
        }

    monkeypatch.setattr("app.services.creator_profile_service.collect_creator_profile_works", fake_collect)
    result = __import__("asyncio").run(CreatorProfileService().scan_profile("douyin", "https://v.douyin.com/abc123/"))
    assert result["creator"]["creator_name"] == "大牙大"
    assert result["creator"]["follower_count"] == 123456


def test_prepare_selected_uses_extracted_creator_name_for_raw_source_grouping():
    """prepare-selected 应用扫描到的博主名，避免原始资料显示未命名博主"""
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
                    "follower_count": 123456,
                    "liked_count": 214000,
                },
                "selected_items": [
                    {"id": "work-1", "title": "测试作品标题", "url": "https://www.douyin.com/video/1", "bucket": "latest"}
                ],
            },
        )
        assert response.status_code == 200
        from app.models import CandidateItem
        candidate = db.get(CandidateItem, response.json()["candidate_ids"][0])
        assert candidate.author == "大牙大"
        assert "\"creator_name\": \"大牙大\"" in candidate.metadata_json
        assert "\"follower_count\": 123456" in candidate.metadata_json
        assert "\"liked_count\": 214000" in candidate.metadata_json
    finally:
        app.dependency_overrides.clear()


def test_select_creator_works_prioritizes_top_liked_desc_then_latest():
    """高赞列表应取点赞量最高的 10 条并排在最新列表之前"""
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
    assert len(top_liked) == 10
    assert [item["like_count"] for item in top_liked] == sorted([item["like_count"] for item in top_liked], reverse=True)
    assert selected[0]["bucket"] in ("top_liked", "both")
    assert snapshot["overlap_count"] == 10
    assert snapshot["top_liked_extension_count"] == 10
