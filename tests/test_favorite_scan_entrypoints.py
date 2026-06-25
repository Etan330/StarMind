import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connectors.base import ConnectorItem
from app.connectors.douyin import DouyinBrowserCollector
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


def test_scan_titles_passes_saved_xiaohongshu_favorites_url(monkeypatch):
    db = make_session()
    called = {}

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="小红书收藏笔记",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            )
        ]

    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        favorites_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"
        response = client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url},
        )

        assert response.status_code == 200
        assert called == {"url": favorites_url, "limit": 5}
        assert response.json()["items"][0]["title"] == "小红书收藏笔记"
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_passes_saved_bilibili_favlist_url(monkeypatch):
    db = make_session()
    called = {}

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.bilibili.com/video/BV1SM4y1K7ax",
                title="B站收藏视频",
                platform="bilibili",
                content_type="video",
                metadata={"source": "test"},
            )
        ]

    monkeypatch.setattr("app.connectors.bilibili_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        favlist_url = "https://space.bilibili.com/351585377/favlist?spm_id_from=333.1007.0.0"
        response = client.post(
            "/api/sync/scan-titles",
            json={"platform": "bilibili", "limit": 5, "homepage_url": favlist_url},
        )

        assert response.status_code == 200
        assert called == {"url": favlist_url, "limit": 5}
        assert response.json()["items"][0]["title"] == "B站收藏视频"
    finally:
        app.dependency_overrides.clear()



def test_scan_titles_bilibili_requires_real_user_favorites_url_when_missing(monkeypatch):
    db = make_session()
    called = False

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.connectors.bilibili_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/sync/scan-titles", json={"platform": "bilibili", "limit": 5})

        assert response.status_code == 428
        assert response.json()["detail"]["code"] == "user_favorites_url_required"
        assert "绑定你的账号/收藏夹 ID" in response.json()["detail"]["message"]
        assert called is False
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_xiaohongshu_requires_real_user_favorites_url_when_missing(monkeypatch):
    db = make_session()
    called = False

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/sync/scan-titles", json={"platform": "xiaohongshu", "limit": 5})

        assert response.status_code == 428
        assert response.json()["detail"]["code"] == "user_favorites_url_required"
        assert "绑定你的账号/收藏夹 ID" in response.json()["detail"]["message"]
        assert called is False
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_bilibili_reuses_saved_homepage_url_when_request_omits_url(monkeypatch):
    db = make_session()
    called = {}
    saved_url = "https://space.bilibili.com/351585377/favlist?spm_id_from=333.1007.0.0"

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.bilibili.com/video/BV1saved",
                title="B站保存收藏视频",
                platform="bilibili",
                content_type="video",
                metadata={"source": "test"},
            )
        ]

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"bilibili": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.connectors.bilibili_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/sync/scan-titles", json={"platform": "bilibili", "limit": 5})

        assert response.status_code == 200
        assert called == {"url": saved_url, "limit": 5}
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_xiaohongshu_reuses_saved_homepage_url_when_request_omits_url(monkeypatch):
    db = make_session()
    called = {}
    saved_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="小红书保存收藏笔记",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            )
        ]

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"xiaohongshu": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/sync/scan-titles", json={"platform": "xiaohongshu", "limit": 5})

        assert response.status_code == 200
        assert called == {"url": saved_url, "limit": 5}
    finally:
        app.dependency_overrides.clear()


def test_collect_and_extract_bilibili_reuses_saved_homepage_url(monkeypatch):
    db = make_session()
    called = {}
    saved_url = "https://space.bilibili.com/351585377/favlist?spm_id_from=333.1007.0.0"

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.bilibili.com/video/BV1collect",
                title="B站直接采集视频",
                platform="bilibili",
                content_type="video",
                metadata={"source": "test"},
            )
        ]

    class FakeDoubaoExtractor:
        async def extract_content(self, url, content_type="auto", timeout_seconds=240):
            return SimpleNamespace(success=False, transcript="", text_content="", title="", error="skip")

        async def close(self):
            return None

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def import_items(self, connector, items, scan_run_id_prefix="import"):
            return SimpleNamespace(candidate_ids=[])

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"bilibili": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.connectors.bilibili_collector.extract_favorites", fake_extract)
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", FakeDoubaoExtractor)
    monkeypatch.setattr("app.api.routes.SyncService", FakeSyncService)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/collect-and-extract/bilibili", json={"limit": 5})

        assert response.status_code == 200
        assert called == {"url": saved_url, "limit": 5}
    finally:
        app.dependency_overrides.clear()


def test_collect_and_extract_xiaohongshu_reuses_saved_homepage_url(monkeypatch):
    db = make_session()
    called = {}
    saved_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="小红书直接采集笔记",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            )
        ]

    class FakeDoubaoExtractor:
        async def extract_content(self, url, content_type="auto", timeout_seconds=240):
            return SimpleNamespace(success=False, transcript="", text_content="", title="", error="skip")

        async def close(self):
            return None

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def import_items(self, connector, items, scan_run_id_prefix="import"):
            return SimpleNamespace(candidate_ids=[])

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"xiaohongshu": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", FakeDoubaoExtractor)
    monkeypatch.setattr("app.api.routes.SyncService", FakeSyncService)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/collect-and-extract/xiaohongshu", json={"limit": 5})

        assert response.status_code == 200
        assert called == {"url": saved_url, "limit": 5}
    finally:
        app.dependency_overrides.clear()


def test_bilibili_favorites_extract_reuses_saved_homepage_url(monkeypatch):
    db = make_session()
    called = {}
    saved_url = "https://space.bilibili.com/351585377/favlist?spm_id_from=333.1007.0.0"

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.bilibili.com/video/BV1entry",
                title="B站旧入口视频",
                platform="bilibili",
                content_type="video",
                metadata={"source": "test"},
            )
        ]

    class FakeScanResult:
        new_count = 1

        def as_dict(self):
            return {"status": "imported", "new_count": self.new_count}

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def import_items(self, connector, items, scan_run_id_prefix="import"):
            return FakeScanResult()

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"bilibili": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.connectors.bilibili_collector.extract_favorites", fake_extract)
    monkeypatch.setattr("app.api.routes.SyncService", FakeSyncService)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/bilibili/favorites/extract", json={"limit": 5})

        assert response.status_code == 200
        assert called == {"url": saved_url, "limit": 5}
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_favorites_extract_reuses_saved_homepage_url(monkeypatch):
    db = make_session()
    called = {}
    saved_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        called["url"] = url
        called["limit"] = limit
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="小红书旧入口笔记",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            )
        ]

    class FakeScanResult:
        new_count = 1

        def as_dict(self):
            return {"status": "imported", "new_count": self.new_count}

    class FakeSyncService:
        def __init__(self, db):
            self.db = db

        async def import_items(self, connector, items, scan_run_id_prefix="import"):
            return FakeScanResult()

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"xiaohongshu": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    monkeypatch.setattr("app.api.routes.SyncService", FakeSyncService)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/xiaohongshu/favorites/extract", json={"limit": 5})

        assert response.status_code == 200
        assert called == {"url": saved_url, "limit": 5}
    finally:
        app.dependency_overrides.clear()


def test_bilibili_collector_requires_explicit_user_favorites_url():
    from app.connectors.bilibili import BilibiliFavoritesCollector

    collector = BilibiliFavoritesCollector()
    try:
        asyncio.run(collector.extract_favorites())
    except ValueError as exc:
        assert "真实收藏页链接" in str(exc)
    else:
        raise AssertionError("B站 collector 不应在缺少用户真实收藏页 URL 时使用通用默认页")


def test_xiaohongshu_collector_requires_explicit_user_favorites_url():
    from app.connectors.xiaohongshu import XiaohongshuFavoritesCollector

    collector = XiaohongshuFavoritesCollector()
    try:
        asyncio.run(collector.extract_favorites())
    except ValueError as exc:
        assert "真实收藏页链接" in str(exc)
    else:
        raise AssertionError("小红书 collector 不应在缺少用户真实收藏页 URL 时使用通用默认页")


def test_scan_titles_returns_actionable_error_when_douyin_page_not_ready(monkeypatch):
    from app.connectors.douyin import DouyinPageNotReady

    db = make_session()

    def override_get_db():
        yield db

    async def fake_extract(*_args, **_kwargs):
        raise DouyinPageNotReady("未识别到收藏内容。请确认浏览器已登录并停留在收藏页面。")

    monkeypatch.setattr("app.api.routes.douyin_browser_collector.extract_visible_video_links", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/sync/scan-titles", json={"platform": "douyin", "limit": 5})

        assert response.status_code == 428
        assert response.json()["detail"]["code"] == "platform_page_not_ready"
        assert "未识别到收藏内容" in response.json()["detail"]["message"]
    finally:
        app.dependency_overrides.clear()


def test_source_setup_marks_homepage_input_for_scan_payload():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/source-setup/bilibili")

        assert response.status_code == 200
        assert "data-source-homepage-input" in response.text
    finally:
        app.dependency_overrides.clear()



def test_save_current_bilibili_favorites_page_detects_and_saves_unique_tab(monkeypatch):
    db = make_session()
    written = {}
    saved_url = "https://space.bilibili.com/351585377/favlist?fid=277411877&ftype=create"

    def override_get_db():
        yield db

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def list_targets(self):
            return [
                {"url": "http://127.0.0.1:8002/ui/source-setup/bilibili", "title": "StarMind"},
                {"url": saved_url, "title": "我的收藏夹"},
            ]

    def fake_write_json(path, payload):
        written["payload"] = payload

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.api.routes.write_json", fake_write_json)
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/source-connections/bilibili/save-current-page", json={})

        assert response.status_code == 200
        assert response.json()["connection"]["homepage_url"] == saved_url
        assert written["payload"]["connections"]["bilibili"]["homepage_url"] == saved_url
    finally:
        app.dependency_overrides.clear()


def test_save_current_xiaohongshu_favorites_page_detects_and_saves_unique_tab(monkeypatch):
    db = make_session()
    written = {}
    saved_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"

    def override_get_db():
        yield db

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def list_targets(self):
            return [
                {"url": "http://127.0.0.1:8002/ui/source-setup/xiaohongshu", "title": "StarMind"},
                {"url": saved_url, "title": "我的收藏"},
            ]

    def fake_write_json(path, payload):
        written["payload"] = payload

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.api.routes.write_json", fake_write_json)
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/source-connections/xiaohongshu/save-current-page", json={})

        assert response.status_code == 200
        assert response.json()["connection"]["homepage_url"] == saved_url
        assert written["payload"]["connections"]["xiaohongshu"]["homepage_url"] == saved_url
    finally:
        app.dependency_overrides.clear()


def test_save_current_favorites_page_returns_actionable_error_when_no_tab_matches(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def list_targets(self):
            return [
                {"url": "http://127.0.0.1:8002/ui/source-setup/bilibili", "title": "StarMind"},
                {"url": "https://www.bilibili.com", "title": "B站首页"},
            ]

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/source-connections/bilibili/save-current-page", json={})

        assert response.status_code == 428
        assert response.json()["detail"]["code"] == "favorites_page_not_found"
        assert "没有在当前浏览器标签页中找到真实收藏页" in response.json()["detail"]["message"]
    finally:
        app.dependency_overrides.clear()


def test_save_current_favorites_page_returns_cdp_error_when_browser_missing(monkeypatch):
    from app.connectors.cdp_proxy import CDPConnectionError

    db = make_session()

    def override_get_db():
        yield db

    class FakeCDPProxy:
        async def connect(self):
            raise CDPConnectionError("CDP Proxy 未运行")

    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/source-connections/bilibili/save-current-page", json={})

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "cdp_proxy_error"
        assert "CDP Proxy 未运行" in response.json()["detail"]["message"]
    finally:
        app.dependency_overrides.clear()


def test_save_current_favorites_page_html_redirects_when_browser_missing(monkeypatch):
    from app.connectors.cdp_proxy import CDPConnectionError

    db = make_session()

    def override_get_db():
        yield db

    class FakeCDPProxy:
        async def connect(self):
            raise CDPConnectionError("CDP Proxy 未运行")

    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/source-connections/bilibili/save-current-page",
            data={},
            headers={"accept": "text/html"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/ui/source-setup/bilibili?saved=browser-missing"
    finally:
        app.dependency_overrides.clear()


def test_save_current_favorites_page_returns_candidates_when_multiple_tabs_match(monkeypatch):
    db = make_session()
    first = "https://space.bilibili.com/351585377/favlist?fid=1&ftype=create"
    second = "https://space.bilibili.com/351585377/favlist?fid=2&ftype=create"

    def override_get_db():
        yield db

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def list_targets(self):
            return [{"url": first, "title": "收藏夹 1"}, {"url": second, "title": "收藏夹 2"}]

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/source-connections/bilibili/save-current-page", json={})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "multiple_favorites_pages_found"
        assert response.json()["detail"]["candidates"] == [first, second]
    finally:
        app.dependency_overrides.clear()


def test_open_bilibili_browser_opens_official_entry_with_cdp(monkeypatch):
    calls = {"opened": [], "navigated": [], "waited": []}

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def new_tab(self, url):
            calls["opened"].append(url)
            return SimpleNamespace(tab_id="target-1", url=url)

        async def navigate(self, tab, url):
            calls["navigated"].append(url)
            tab.url = url

        async def wait_for_load(self, tab):
            calls["waited"].append(tab.tab_id)

        async def get_info(self, tab):
            return {"url": tab.url, "title": ""}

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)

    client = TestClient(app)
    response = client.post("/bilibili/browser/open", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "opened"
    assert response.json()["current_url"] == "https://www.bilibili.com"
    assert calls["opened"] == ["https://www.bilibili.com"]
    assert calls["navigated"] == []
    assert calls["waited"] == ["target-1"]


def test_open_xiaohongshu_browser_opens_official_entry_with_cdp(monkeypatch):
    calls = {"opened": [], "navigated": [], "waited": []}

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def new_tab(self, url):
            calls["opened"].append(url)
            return SimpleNamespace(tab_id="target-1", url=url)

        async def navigate(self, tab, url):
            calls["navigated"].append(url)
            tab.url = url

        async def wait_for_load(self, tab):
            calls["waited"].append(tab.tab_id)

        async def get_info(self, tab):
            return {"url": tab.url, "title": ""}

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {}})
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)

    client = TestClient(app)
    response = client.post("/xiaohongshu/browser/open", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "opened"
    assert response.json()["current_url"] == "https://www.xiaohongshu.com/explore"
    assert calls["opened"] == ["https://www.xiaohongshu.com/explore"]
    assert calls["navigated"] == []
    assert calls["waited"] == ["target-1"]


def test_open_bilibili_browser_navigates_saved_user_favorites_url(monkeypatch):
    calls = {"opened": [], "navigated": [], "waited": []}
    saved_url = "https://space.bilibili.com/351585377/favlist?fid=277411877&ftype=create"

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def new_tab(self, url):
            calls["opened"].append(url)
            return SimpleNamespace(tab_id="target-1", url=url)

        async def navigate(self, tab, url):
            calls["navigated"].append(url)
            tab.url = url

        async def wait_for_load(self, tab):
            calls["waited"].append(tab.tab_id)

        async def get_info(self, tab):
            return {"url": tab.url, "title": ""}

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"bilibili": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)

    client = TestClient(app)
    response = client.post("/bilibili/browser/open", json={})

    assert response.status_code == 200
    assert response.json()["current_url"] == saved_url
    assert calls["opened"] == ["https://www.bilibili.com"]
    assert calls["navigated"] == [saved_url]


def test_open_xiaohongshu_browser_navigates_saved_user_favorites_url(monkeypatch):
    calls = {"opened": [], "navigated": [], "waited": []}
    saved_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"

    class FakeCDPProxy:
        async def connect(self):
            return True

        async def new_tab(self, url):
            calls["opened"].append(url)
            return SimpleNamespace(tab_id="target-1", url=url)

        async def navigate(self, tab, url):
            calls["navigated"].append(url)
            tab.url = url

        async def wait_for_load(self, tab):
            calls["waited"].append(tab.tab_id)

        async def get_info(self, tab):
            return {"url": tab.url, "title": ""}

    monkeypatch.setattr("app.api.routes.get_source_connections", lambda: {"connections": {"xiaohongshu": {"homepage_url": saved_url}}})
    monkeypatch.setattr("app.api.routes.cdp_proxy", FakeCDPProxy(), raising=False)

    client = TestClient(app)
    response = client.post("/xiaohongshu/browser/open", json={})

    assert response.status_code == 200
    assert response.json()["current_url"] == saved_url
    assert calls["opened"] == ["https://www.xiaohongshu.com/explore"]
    assert calls["navigated"] == [saved_url]


def test_douyin_open_uses_login_page_before_collection_navigation(monkeypatch):
    opened = []
    navigated = []

    async def fake_check_proxy(self):
        return None

    async def fake_request(self, method, path, body=None):
        if path == "/health":
            return {"connected": True}
        if method == "POST" and path == "/new":
            opened.append(body)
            return {"targetId": "target-1"}
        if method == "POST" and path.startswith("/navigate"):
            navigated.append(body)
            return {}
        return {}

    async def fake_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(DouyinBrowserCollector, "_check_proxy", fake_check_proxy)
    monkeypatch.setattr(DouyinBrowserCollector, "_request", fake_request)
    monkeypatch.setattr("app.connectors.douyin.asyncio.sleep", fake_sleep)

    collector = DouyinBrowserCollector()
    state = asyncio.run(collector.open())

    assert state.opened is True
    assert opened == ["https://www.douyin.com/user/self"]
    assert navigated == ["https://www.douyin.com/user/self?showTab=favorite_collection"]
