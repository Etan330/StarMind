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

    title = "17.0万 Flipbook爆火：UI的未来是无限视觉 我这两天被这个爆火的Flipbook刷屏并被震撼到，现在的UI界面也许会在不久的将来彻底改变。"
    item = normalize_creator_work(
        "xiaohongshu",
        {
            "id": "work-1",
            "url": "https://www.xiaohongshu.com/user/profile/u/work-1",
            "title": title,
            "like_count": 170000,
        },
    )

    assert item["title"].startswith("Flipbook爆火：UI的未来是无限视觉")
    assert not item["title"].startswith("17.0万")


def test_creator_frontend_cleans_cached_work_titles_before_rendering():
    """localStorage/API 中残留旧标题时，前端渲染前也要兜底清洗点赞量前缀"""
    app_js = open("app/static/app.js", encoding="utf-8").read()

    assert "cleanCreatorWorkTitle" in app_js
    assert "normalizeCreatorWorkItem" in app_js
    assert "scannedItems = (state.items || []).map(normalizeCreatorWorkItem)" in app_js
    assert "scannedItems = (response.items || []).map(normalizeCreatorWorkItem)" in app_js
    assert "cleanCreatorWorkTitle(item.title)" in app_js


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


def test_sources_page_links_and_ideas_are_selectable_sources(tmp_path):
    """用户贴的链接和临时 idea 应能在右侧预览 RawSource，而不是跳待处理页或点不动"""
    from app.models import RawSource

    db = make_session()
    link_path = tmp_path / "link.md"
    idea_path = tmp_path / "idea.md"
    link_path.write_text("用户链接抓取正文", encoding="utf-8")
    idea_path.write_text("临时 idea 原始正文", encoding="utf-8")
    db.add_all(
        [
            RawSource(platform="web", source_url="https://example.com/a", canonical_url="https://example.com/a", external_item_id="link-1", source_type="passive_link", title="用户贴的链接标题", transcript_path=str(link_path), metadata_json="{}"),
            RawSource(platform="manual", source_url="manual://idea", canonical_url="manual://idea", external_item_id="idea-1", source_type="manual_idea", title="临时 idea 标题", transcript_path=str(idea_path), metadata_json="{}"),
        ]
    )
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/sources")
        assert response.status_code == 200
        assert 'href="/ui/sources?source_id=1"' in response.text
        assert 'href="/ui/sources?source_id=2"' in response.text
        assert 'href="/ui/pending" data-expand-item' not in response.text
        detail = client.get("/ui/sources?source_id=2")
        assert "临时 idea 原始正文" in detail.text
    finally:
        app.dependency_overrides.clear()


def test_sources_page_preserves_scroll_and_open_groups_for_source_links():
    """点击原始资料条目后应保存滚动位置和展开分组，返回详情页时不跳到顶部/不收起博主"""
    template = open("app/templates/sources.html", encoding="utf-8").read()

    assert 'data-source-select-link' in template
    assert 'sessionStorage.setItem("sourcesScrollY"' in template
    assert 'window.scrollTo(0, savedScrollY)' in template
    assert 'data-source-group-key' in template
    assert 'sessionStorage.setItem("sourcesOpenGroups"' in template
    assert 'details.open = openGroups.includes(key)' in template


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


def test_douyin_creator_work_script_restricts_to_profile_work_area():
    """抖音博主页扫描只应从主页作品区域取 /video/，不能混入推荐/侧边栏视频"""
    from app.services.creator_profile_service import _creator_work_extract_script

    script = _creator_work_extract_script("douyin")

    assert "douyinWorkRoots" in script
    assert "isDouyinProfileWorkAnchor" in script
    assert "douyinRejectRoot" in script
    assert "if (platform === \"douyin\" && !isDouyinProfileWorkAnchor(a, url)) continue;" in script


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


def test_sources_creator_group_has_create_wiki_button():
    """原始资料的博主分组应提供加入知识库入口"""
    template = open("app/templates/sources.html", encoding="utf-8").read()

    assert "加入知识库" in template
    assert "/api/creator/" in template
    assert "/create-wiki" in template


def test_create_creator_wiki_aggregates_raw_sources(tmp_path, monkeypatch):
    """点击加入知识库应聚合同一博主 RawSource，生成博主分析 WikiPage"""
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    from app.models import RawSource, WikiPage

    class Provider:
        provider_name = "mock"

        async def chat(self, messages, model, temperature=0.2):
            return "## 人设\n本地测试分析\n\n## 最新与高赞差异\n最新和高赞有差异"

    monkeypatch.setattr(
        "app.services.wiki_service.get_provider_runtime",
        lambda provider_id=None, model=None: (Provider(), "mock-model", {"api_style": "mock"}),
    )
    metadata = {"creator_key": "douyin:abc123", "creator_name": "大牙大", "creator_bucket": "top_liked"}
    db.add_all(
        [
            RawSource(
                platform="douyin",
                source_url="https://www.douyin.com/video/1",
                canonical_url="https://www.douyin.com/video/1",
                external_item_id="work-1",
                source_type="distill_profile",
                title="高赞作品",
                author="大牙大",
                transcript_path=str(tmp_path / "work-1.txt"),
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            ),
            RawSource(
                platform="douyin",
                source_url="https://www.douyin.com/video/2",
                canonical_url="https://www.douyin.com/video/2",
                external_item_id="work-2",
                source_type="distill_profile",
                title="最新作品",
                author="大牙大",
                transcript_path=str(tmp_path / "work-2.txt"),
                metadata_json=json.dumps({**metadata, "creator_bucket": "latest"}, ensure_ascii=False),
            ),
        ]
    )
    (tmp_path / "work-1.txt").write_text("高赞作品逐字稿", encoding="utf-8")
    (tmp_path / "work-2.txt").write_text("最新作品逐字稿", encoding="utf-8")
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/creator/douyin%3Aabc123/create-wiki")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "created"
        page = db.query(WikiPage).one()
        assert page.page_type == "creator"
        assert page.title == "博主分析：大牙大"
        assert "creator:douyin:abc123" in page.tags_json
        markdown = Path(page.markdown_path).read_text(encoding="utf-8")
        assert "高赞作品" in markdown
        assert "最新与高赞差异" in markdown
        assert "高赞作品逐字稿" not in markdown
        assert "最新作品逐字稿" not in markdown
        assert payload["source_count"] == 2
    finally:
        app.dependency_overrides.clear()



def test_create_creator_wiki_uses_llm_when_configured(tmp_path, monkeypatch):
    """博主加入知识库阶段应调用模型生成分析，而不是豆包/点点提取阶段做分析"""
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    from app.models import RawSource, WikiPage

    class Provider:
        provider_name = "mock"

        async def chat(self, messages, model, temperature=0.2):
            assert "人设" in messages[-1]["content"]
            assert "高赞作品逐字稿" not in messages[-1]["content"]
            assert "## 逐字稿" not in messages[-1]["content"]
            return "## 人设\n模型生成的人设分析\n\n## 商业价值\n模型生成的商业价值"

    monkeypatch.setattr(
        "app.services.wiki_service.get_provider_runtime",
        lambda provider_id=None, model=None: (Provider(), "mock-model", {"api_style": "mock"}),
    )
    metadata = {"creator_key": "douyin:abc123", "creator_name": "大牙大", "creator_bucket": "top_liked"}
    db.add(
        RawSource(
            platform="douyin",
            source_url="https://www.douyin.com/video/1",
            canonical_url="https://www.douyin.com/video/1",
            external_item_id="work-1",
            source_type="distill_profile",
            title="高赞作品",
            author="大牙大",
            transcript_path=str(tmp_path / "work-1.txt"),
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
    )
    (tmp_path / "work-1.txt").write_text("高赞作品逐字稿", encoding="utf-8")
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/creator/douyin%3Aabc123/create-wiki", json={"force": True})

        assert response.status_code == 200
        page = db.query(WikiPage).one()
        markdown = Path(page.markdown_path).read_text(encoding="utf-8")
        assert "已调用模型完成博主分析" in markdown
        assert "模型生成的人设分析" in markdown
    finally:
        app.dependency_overrides.clear()


def test_creator_wiki_page_shows_creator_section_and_sources(tmp_path, monkeypatch):
    """知识库应提供博主一级目录，并在博主页展示作品来源列表"""
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    from app.models import RawSource
    from app.services.wiki_service import WikiMaintenanceService

    metadata = {"creator_key": "douyin:abc123", "creator_name": "大牙大", "creator_bucket": "top_liked"}
    source = RawSource(
        platform="douyin",
        source_url="https://www.douyin.com/video/1",
        canonical_url="https://www.douyin.com/video/1",
        external_item_id="work-1",
        source_type="distill_profile",
        title="高赞作品",
        author="大牙大",
        transcript_path=str(tmp_path / "work-1.txt"),
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )
    db.add(source)
    (tmp_path / "work-1.txt").write_text("高赞作品逐字稿", encoding="utf-8")
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        client.post("/api/creator/douyin%3Aabc123/create-wiki")
        response = client.get("/ui/wiki?section=creator")

        assert response.status_code == 200
        assert "博主" in response.text
        assert "大牙大" in response.text
        assert "高赞作品" in response.text
        assert "creator-source-ref-list" in response.text
        assert "高赞" in response.text
    finally:
        app.dependency_overrides.clear()


def test_creator_wiki_page_has_creator_scoped_question_marker():
    """博主页问答表单应携带 creator_key，避免全局检索串台"""
    template = open("app/templates/wiki.html", encoding="utf-8").read()

    assert 'name="creator_key"' in template
    assert "selected_creator_key" in template
    assert "只能基于该博主资料回答" in template


def test_creator_wiki_and_sources_use_structured_layout_classes():
    """博主原始资料和知识库页面应有结构化排版容器，避免文字堆叠"""
    sources_template = open("app/templates/sources.html", encoding="utf-8").read()
    wiki_template = open("app/templates/wiki.html", encoding="utf-8").read()
    css = open("app/static/css/v3-design-system.css", encoding="utf-8").read()

    assert "creator-source-folder-head" in sources_template
    assert "creator-source-summary-grid" in sources_template
    assert "creator-wiki-article" in wiki_template
    assert "creator-analysis-layout" in css
    assert "creator-source-ref-list" in css
    assert "wiki-markdown" in css


def test_creator_wiki_sources_are_collapsed_and_transcript_hidden():
    """知识库博主页的作品列表默认折叠，页面分析里不展示逐字稿片段"""
    wiki_template = open("app/templates/wiki.html", encoding="utf-8").read()
    service = open("app/services/wiki_service.py", encoding="utf-8").read()

    assert '<details class="creator-source-ref-list"' in wiki_template
    assert "<summary>作品列表" in wiki_template
    creator_markdown_builder = service.split("async def _creator_analysis_markdown", 1)[1].split("def _format_creator_analysis_body", 1)[0]
    assert "片段：" not in creator_markdown_builder
    assert "逐字稿" not in creator_markdown_builder


def test_creator_analysis_strips_transcript_sections_from_generated_text():
    """模型输出里若夹带逐字稿章节，落盘前应删掉整段逐字稿内容"""
    from app.services.wiki_service import WikiMaintenanceService

    dirty = """## 人设
正常分析

## 逐字稿
链接解析结果 链接地址：https://www.douyin.com/video/1
片段：这些原始内容不该进知识库
完整视频口播逐字稿（原文完整保留）
更多原始正文

## 商业价值
可合作方向明确"""

    cleaned = WikiMaintenanceService(None)._strip_transcript_sections(dirty)

    assert "## 人设" in cleaned
    assert "## 商业价值" in cleaned
    assert "链接解析结果" not in cleaned
    assert "片段：" not in cleaned
    assert "完整视频口播" not in cleaned
    assert "更多原始正文" not in cleaned


def test_creator_wiki_section_ignores_non_creator_page_id(tmp_path, monkeypatch):
    """section=creator 时不应渲染非博主页，避免问答范围错乱"""
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    from app.models import RawSource, WikiPage

    creator_meta = {"creator_key": "douyin:abc123", "creator_name": "大牙大", "creator_bucket": "top_liked"}
    db.add_all(
        [
            WikiPage(page_id="knowledge-page", page_type="knowledge", title="普通知识页", markdown_path=str(tmp_path / "knowledge.md"), source_refs_json="[]", tags_json="[]"),
            RawSource(platform="douyin", source_url="https://a", canonical_url="https://a", external_item_id="a", source_type="distill_profile", title="大牙大作品", transcript_path=str(tmp_path / "creator.txt"), metadata_json=json.dumps(creator_meta, ensure_ascii=False)),
        ]
    )
    (tmp_path / "knowledge.md").write_text("普通知识", encoding="utf-8")
    (tmp_path / "creator.txt").write_text("博主资料", encoding="utf-8")
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        creator_page_id = client.post("/api/creator/douyin%3Aabc123/create-wiki").json()["wiki_page_id"]
        response = client.get("/ui/wiki?section=creator&page_id=knowledge-page")

        assert response.status_code == 200
        assert "普通知识页" not in response.text
        assert creator_page_id in response.text
    finally:
        app.dependency_overrides.clear()


def test_creator_wiki_question_filters_before_global_limit(tmp_path, monkeypatch):
    """博主限定检索应先按 creator_key 过滤，再限制数量"""
    monkeypatch.setattr("app.agent.tools.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    from app.agent.tools import KnowledgeSearchTool
    from app.models import RawSource

    for index in range(35):
        path = tmp_path / f"other-{index}.txt"
        path.write_text("其他博主内容", encoding="utf-8")
        db.add(RawSource(platform="douyin", source_url=f"https://o/{index}", canonical_url=f"https://o/{index}", external_item_id=f"o-{index}", source_type="distill_profile", title=f"其他作品 {index}", transcript_path=str(path), metadata_json=json.dumps({"creator_key": "douyin:other"}, ensure_ascii=False)))
    creator_path = tmp_path / "creator-old.txt"
    creator_path.write_text("只属于大牙大的深层资料", encoding="utf-8")
    creator_source = RawSource(platform="douyin", source_url="https://a", canonical_url="https://a", external_item_id="a", source_type="distill_profile", title="大牙大旧作品", transcript_path=str(creator_path), metadata_json=json.dumps({"creator_key": "douyin:abc123", "creator_name": "大牙大"}, ensure_ascii=False))
    db.add(creator_source)
    db.commit()
    creator_source.created_at = creator_source.created_at.replace(year=2000)
    db.commit()

    result = KnowledgeSearchTool(db).run("深层资料", creator_key="douyin:abc123")

    assert "大牙大旧作品" in result.content
    assert "其他作品" not in result.content


def test_creator_wiki_question_uses_creator_scoped_search(tmp_path, monkeypatch):
    """博主页追问应只检索同一 creator_key 的资料"""
    monkeypatch.setattr("app.agent.tools.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    from app.agent.tools import KnowledgeSearchTool
    from app.models import RawSource, WikiPage

    creator_meta = {"creator_key": "douyin:abc123", "creator_name": "大牙大"}
    other_meta = {"creator_key": "douyin:other", "creator_name": "其他博主"}
    creator_file = tmp_path / "creator.txt"
    other_file = tmp_path / "other.txt"
    creator_file.write_text("只属于大牙大的商业化内容", encoding="utf-8")
    other_file.write_text("其他博主内容不应该出现", encoding="utf-8")
    db.add_all(
        [
            RawSource(platform="douyin", source_url="https://a", canonical_url="https://a", external_item_id="a", source_type="distill_profile", title="大牙大作品", transcript_path=str(creator_file), metadata_json=json.dumps(creator_meta, ensure_ascii=False)),
            RawSource(platform="douyin", source_url="https://b", canonical_url="https://b", external_item_id="b", source_type="distill_profile", title="其他作品", transcript_path=str(other_file), metadata_json=json.dumps(other_meta, ensure_ascii=False)),
            WikiPage(page_id="creator-page", page_type="creator", title="博主分析：大牙大", markdown_path=str(creator_file), source_refs_json="[]", tags_json=json.dumps(["creator:douyin:abc123"], ensure_ascii=False)),
        ]
    )
    db.commit()

    result = KnowledgeSearchTool(db).run("内容", creator_key="douyin:abc123")

    assert "大牙大" in result.content
    assert "其他博主" not in result.content
    assert result.metadata["creator_key"] == "douyin:abc123"


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
