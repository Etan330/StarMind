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
from app.models import CandidateItem, Connector, RawSource, ScanEntry, SyncLedgerItem


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_scan_titles_douyin_requires_collection_page(monkeypatch):
    db = make_session()
    called = {}

    def override_get_db():
        yield db

    async def fake_extract(limit=None, require_collection_page=True):
        called["limit"] = limit
        called["require_collection_page"] = require_collection_page
        return [
            ConnectorItem(
                raw_url="https://www.douyin.com/video/7380000112233",
                title="真实收藏视频",
                platform="douyin",
                content_type="video",
            )
        ]

    monkeypatch.setattr("app.api.routes.douyin_browser_collector.extract_visible_video_links", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/sync/scan-titles",
            json={"platform": "douyin", "limit": "all", "collection_kind": "incremental"},
        )

        assert response.status_code == 200
        assert called == {"limit": None, "require_collection_page": True}
    finally:
        app.dependency_overrides.clear()



def test_scan_titles_incremental_filters_existing_history_even_without_connector_state(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    db.add(
        ScanEntry(
            platform="douyin",
            external_item_id="7380000112233",
            canonical_url="https://www.douyin.com/video/7380000112233",
            raw_url="https://www.douyin.com/video/7380000112233",
            title="历史里已有的收藏",
            content_type="video",
            collection_kind="history",
            metadata_json="{}",
        )
    )
    db.commit()

    async def fake_extract(limit=None, require_collection_page=True):
        return [
            ConnectorItem(
                raw_url="https://www.douyin.com/video/7380000112233",
                title="历史里已有的收藏",
                platform="douyin",
                content_type="video",
                metadata={"source": "test"},
            )
        ]

    monkeypatch.setattr("app.api.routes.douyin_browser_collector.extract_visible_video_links", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/sync/scan-titles",
            json={"platform": "douyin", "limit": "all", "scan_mode": "new", "collection_kind": "incremental"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["new_count"] == 0
        assert body["skipped_existing_count"] == 1
        assert body["all_duplicates"] is True
        assert "没有新增收藏" in body["message"]
    finally:
        app.dependency_overrides.clear()



def test_scan_titles_incremental_new_stops_at_first_seen_item(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    connector = Connector(platform="xiaohongshu", name="小红书收藏夹", connector_type="browser_xiaohongshu")
    connector.first_scan_done = True
    connector.history_saved = True
    db.add(connector)
    db.add(
        ScanEntry(
            platform="xiaohongshu",
            external_item_id="65fabc1234567890abcdef12",
            canonical_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
            raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
            title="旧收藏",
            content_type="note",
            collection_kind="history",
            metadata_json="{}",
        )
    )
    db.commit()

    async def fake_extract(url=None, limit=None):
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef13",
                title="新增收藏 1",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="旧收藏",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef14",
                title="旧收藏后面的内容不属于新增批次",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
        ]

    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/sync/scan-titles",
            json={
                "platform": "xiaohongshu",
                "limit": "all",
                "scan_mode": "new",
                "collection_kind": "incremental",
                "homepage_url": "https://www.xiaohongshu.com/user/profile/abc?tab=fav&subTab=note",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert [item["title"] for item in body["items"]] == ["新增收藏 1"]
        assert body["saved_to_history"] is True
        assert body["new_count"] == 1
        assert body["skipped_existing_count"] == 1
        assert body["boundary_hit"] is True
        assert db.query(ScanEntry).filter(ScanEntry.title == "新增收藏 1").count() == 1
        assert db.query(ScanEntry).filter(ScanEntry.title == "旧收藏后面的内容不属于新增批次").count() == 0
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_incremental_all_scans_full_page_but_returns_only_unseen(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    connector = Connector(platform="xiaohongshu", name="小红书收藏夹", connector_type="browser_xiaohongshu")
    connector.first_scan_done = True
    connector.history_saved = True
    db.add(connector)
    db.add(
        ScanEntry(
            platform="xiaohongshu",
            external_item_id="65fabc1234567890abcdef12",
            canonical_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
            raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
            title="旧收藏",
            content_type="note",
            collection_kind="history",
            metadata_json="{}",
        )
    )
    db.commit()

    async def fake_extract(url=None, limit=None):
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef13",
                title="新增收藏 1",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="旧收藏",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef14",
                title="新增收藏 2",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
        ]

    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/sync/scan-titles",
            json={
                "platform": "xiaohongshu",
                "limit": "all",
                "scan_mode": "all",
                "collection_kind": "incremental",
                "homepage_url": "https://www.xiaohongshu.com/user/profile/abc?tab=fav&subTab=note",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert [item["title"] for item in body["items"]] == ["新增收藏 1", "新增收藏 2"]
        assert body["saved_to_history"] is True
        assert body["new_count"] == 2
        assert body["skipped_existing_count"] == 1
        assert body["boundary_hit"] is False
        assert db.query(ScanEntry).filter(ScanEntry.title.in_(["新增收藏 1", "新增收藏 2"])).count() == 2
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_after_clear_history_does_not_apply_incremental_boundary(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    raw_source = RawSource(
        title="旧入库资料",
        source_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
        canonical_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
        external_item_id="65fabc1234567890abcdef12",
        platform="xiaohongshu",
        source_type="favorite",
        transcript_path="/tmp/old.md",
        metadata_json="{}",
    )
    db.add(raw_source)
    db.flush()
    connector = Connector(platform="xiaohongshu", name="小红书收藏夹", connector_type="browser_xiaohongshu")
    connector.first_scan_done = False
    connector.history_saved = False
    db.add(connector)
    db.add(
        SyncLedgerItem(
            connector_id=1,
            platform="xiaohongshu",
            external_item_id="65fabc1234567890abcdef12",
            canonical_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
            raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                scan_run_id="old_ingested",
                raw_source_id=raw_source.id,
        )
    )
    db.commit()

    async def fake_extract(url=None, limit=None):
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="清空后重新扫描的收藏",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            )
        ]

    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/sync/scan-titles",
            json={
                "platform": "xiaohongshu",
                "limit": 50,
                "collection_kind": "incremental",
                "homepage_url": "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "清空后重新扫描的收藏"
        assert body["boundary_hit"] is False
    finally:
        app.dependency_overrides.clear()



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


def test_source_setup_renders_dual_collection_tabs_and_filter_toolbar():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/source-setup/douyin")

        assert response.status_code == 200
        text = response.text
        # 历史 / 新增双 Tab
        assert 'data-collection-tab="history"' in text
        assert 'data-collection-tab="incremental"' in text
        # 两个面板，分别带 collection_kind
        assert 'data-collection-kind="history"' in text
        assert 'data-collection-kind="incremental"' in text
        # 筛选器（发布时间筛选已隐藏，但有用性/类别保留）
        assert "data-filter-usefulness" in text
        assert "data-filter-category" in text
        # 历史 Tab 不再有保存/重扫状态机，只保留清空列表和补提取能力
        assert "data-filter-save-history" not in text
        assert "data-filter-rescan-history" not in text
        assert "data-filter-clear-history" in text
        assert "清空当前收藏列表" in text
        # 已入库筛选（仅历史 Tab）
        assert "data-filter-ingested" in text
        # 新增 Tab 带采集数量下拉，含「新增」与「全部」选项
        assert '<option value="new" selected>新增</option>' in text
        assert '<option value="all">全部</option>' in text
        # 发布时间筛选下拉已隐藏：整块包在 HTML 注释里（模板保留代码以后放出），
        # 但注释开始一定在 data-filter-time 之前，确认它不是活动控件。
        assert "<select data-filter-time>" in text
        assert "<!-- 发布时间筛选" in text
        assert text.index("<!-- 发布时间筛选") < text.index("data-filter-time")
    finally:
        app.dependency_overrides.clear()


def test_source_setup_incremental_panel_has_limit_dropdown_and_renamed_scan_button():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/source-setup/douyin")

        assert response.status_code == 200
        import re

        text = response.text
        # 新增面板：保留采集数量下拉、扫描按钮改名「采集收藏」
        incremental_panel = re.search(
            r'data-collection-kind="incremental".*?(?=data-collection-kind="|</section>)',
            text,
            flags=re.S,
        )
        assert incremental_panel is not None
        panel_html = incremental_panel.group(0)
        assert "data-filter-limit" in panel_html
        assert '<option value="new" selected>新增</option>' in panel_html
        assert "采集收藏" in panel_html
        assert "采集新增收藏" not in panel_html
        # 已入库筛选只在历史，新增面板不应有
        assert "data-filter-ingested" not in panel_html

        # 历史面板：不再提供扫描/分类/保存控件，只用于筛选和补提取
        history_panel = re.search(
            r'data-collection-kind="history".*?(?=</section>)',
            text,
            flags=re.S,
        )
        assert history_panel is not None
        assert "data-filter-limit" not in history_panel.group(0)
        assert "data-filter-scan" not in history_panel.group(0)
        assert "data-filter-classify" not in history_panel.group(0)
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


def test_scan_titles_persists_scan_entries_and_returns_collection_kind(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="小红书收藏笔记",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test", "publish_time": "2024-05-01"},
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
        body = response.json()
        # 首次扫描 → 全部算历史
        assert body["collection_kind"] == "history"
        assert body["total"] == 1
        item = body["items"][0]
        assert item["scan_entry_id"]
        assert item["collection_kind"] == "history"
        assert item["published_at"] == "2024-05-01"
        assert item["extracted"] is False

        # 落库 + connector 置 first_scan_done
        entries = db.query(ScanEntry).filter(ScanEntry.platform == "xiaohongshu").all()
        assert len(entries) == 1
        connector = db.query(Connector).filter(Connector.platform == "xiaohongshu").first()
        assert connector is not None and connector.first_scan_done is True
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_history_request_can_extend_existing_history_scan(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    items = [
        ConnectorItem(
            raw_url=f"https://www.xiaohongshu.com/explore/{index:024x}",
            title=f"历史笔记{index}",
            platform="xiaohongshu",
            content_type="note",
            metadata={"source": "test"},
        )
        for index in range(20)
    ]
    calls = []

    async def fake_extract(url=None, limit=None):
        calls.append(limit)
        return items[:limit]

    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    favorites_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"
    try:
        client = TestClient(app)
        first = client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 10, "homepage_url": favorites_url, "collection_kind": "history"},
        )
        assert first.status_code == 200
        assert first.json()["collection_kind"] == "history"
        assert first.json()["total"] == 10

        second = client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 20, "homepage_url": favorites_url, "collection_kind": "history"},
        )
        body = second.json()
        assert second.status_code == 200
        assert calls == [10, 20]
        assert body["collection_kind"] == "history"
        assert body["total"] == 20
        assert db.query(ScanEntry).filter(ScanEntry.platform == "xiaohongshu", ScanEntry.collection_kind == "history").count() == 20
    finally:
        app.dependency_overrides.clear()



def test_scan_titles_second_scan_is_incremental_and_dedups(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    item_a = ConnectorItem(
        raw_url="https://www.xiaohongshu.com/explore/aaaaaaaaaaaaaaaaaaaaaaaa",
        title="历史笔记A",
        platform="xiaohongshu",
        content_type="note",
        metadata={"source": "test"},
    )
    item_b = ConnectorItem(
        raw_url="https://www.xiaohongshu.com/explore/bbbbbbbbbbbbbbbbbbbbbbbb",
        title="新增笔记B",
        platform="xiaohongshu",
        content_type="note",
        metadata={"source": "test"},
    )

    state = {"items": [item_a]}

    async def fake_extract(url=None, limit=None):
        return state["items"]

    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    favorites_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"
    try:
        client = TestClient(app)
        # 首次：历史，A 落库
        first = client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url},
        )
        assert first.json()["collection_kind"] == "history"

        # 第二次：真实收藏页从最新到最旧返回 B+A；遇到 A 这个已见边界后停止，只保留 B。
        # 采集即并入历史：增量扫描仍走「遇已见即停」去重，但落库 collection_kind 恒 history。
        state["items"] = [item_b, item_a]
        second = client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url, "collection_kind": "incremental"},
        )
        body = second.json()
        assert body["collection_kind"] == "history"
        assert body["scan_run_id"]
        assert body["total"] == 1
        assert body["items"][0]["title"] == "新增笔记B"
        assert body["items"][0]["collection_kind"] == "history"
        assert body["boundary_hit"] is True

        # 第三次：B 也已见，扫描到 B 就停止，不再展示 B 或更旧的历史 A。
        third = client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url, "collection_kind": "incremental"},
        )
        third_body = third.json()
        assert third_body["collection_kind"] == "history"
        assert third_body["total"] == 0
        assert third_body["items"] == []
        assert third_body["boundary_hit"] is True

        # DB 现在两条，且都落 history（不再有 incremental 行）。
        entries = db.query(ScanEntry).filter(ScanEntry.platform == "xiaohongshu").all()
        assert {e.external_item_id for e in entries} and len(entries) == 2
        assert all(e.collection_kind == "history" for e in entries)
    finally:
        app.dependency_overrides.clear()


def test_list_scan_entries_endpoint_returns_persisted_entries(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
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
    favorites_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"
    try:
        client = TestClient(app)
        client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url},
        )
        # GET 恢复
        resp = client.get("/api/sync/scan-entries", params={"platform": "xiaohongshu"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "小红书收藏笔记"
        assert body["items"][0]["collection_kind"] == "history"
        # 历史保存 flag：保存前为 False
        assert body["history_saved"] is False

        # kind 过滤
        resp_inc = client.get("/api/sync/scan-entries", params={"platform": "xiaohongshu", "kind": "incremental"})
        assert resp_inc.json()["total"] == 0
    finally:
        app.dependency_overrides.clear()


def test_save_history_endpoint_sets_flag_and_returns_count(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
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
    favorites_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"
    try:
        client = TestClient(app)
        # 先扫描，落一条历史
        client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url},
        )

        resp = client.post("/api/sync/save-history", json={"platform": "xiaohongshu"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "saved"
        assert body["history_saved"] is True
        assert body["history_count"] == 1

        # connector flag 落库
        connector = db.query(Connector).filter(Connector.platform == "xiaohongshu").first()
        assert connector is not None and connector.history_saved is True

        # list 端点现在回 history_saved=True
        listed = client.get("/api/sync/scan-entries", params={"platform": "xiaohongshu"})
        assert listed.json()["history_saved"] is True
    finally:
        app.dependency_overrides.clear()


def test_reset_history_endpoint_clears_flag_first_scan_done_and_history_rows(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
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
    favorites_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"
    try:
        client = TestClient(app)
        client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url},
        )
        client.post("/api/sync/save-history", json={"platform": "xiaohongshu"})

        before_rows = db.query(ScanEntry).filter(ScanEntry.platform == "xiaohongshu").count()
        assert before_rows == 1

        resp = client.post("/api/sync/reset-history", json={"platform": "xiaohongshu"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "reset"
        assert body["history_saved"] is False

        # flag + first_scan_done 都被清；历史 ScanEntry 行清空，重新扫描从干净历史集合开始
        db.expire_all()
        connector = db.query(Connector).filter(Connector.platform == "xiaohongshu").first()
        assert connector is not None
        assert connector.history_saved is False
        assert connector.first_scan_done is False
        assert db.query(ScanEntry).filter(ScanEntry.platform == "xiaohongshu", ScanEntry.collection_kind == "history").count() == 0
    finally:
        app.dependency_overrides.clear()


def test_clear_history_list_removes_current_list_and_uningested_scan_candidates(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=None):
        return [
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
                title="未入库收藏",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
            ConnectorItem(
                raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef34",
                title="已入库收藏",
                platform="xiaohongshu",
                content_type="note",
                metadata={"source": "test"},
            ),
        ]

    monkeypatch.setattr("app.connectors.xiaohongshu_collector.extract_favorites", fake_extract)
    app.dependency_overrides[get_db] = override_get_db
    favorites_url = "https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33?tab=fav&subTab=note"
    try:
        client = TestClient(app)
        client.post(
            "/api/sync/scan-titles",
            json={"platform": "xiaohongshu", "limit": 5, "homepage_url": favorites_url},
        )
        client.post(
            "/api/sync/prepare-selected",
            json={
                "platform": "xiaohongshu",
                "selected_items": [{"url": "https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12"}],
                "skipped_items": [],
            },
        )
        client.post(
            "/api/sync/prepare-selected",
            json={
                "platform": "xiaohongshu",
                "selected_items": [{"url": "https://www.xiaohongshu.com/explore/65fabc1234567890abcdef34"}],
                "skipped_items": [],
            },
        )
        ingested_candidate = db.query(CandidateItem).filter(CandidateItem.external_item_id == "65fabc1234567890abcdef34").one()
        ingested_ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == ingested_candidate.id).one()
        raw_source = RawSource(
            candidate_id=ingested_candidate.id,
            platform="xiaohongshu",
            source_url=ingested_candidate.raw_url,
            canonical_url=ingested_candidate.canonical_url,
            external_item_id=ingested_candidate.external_item_id,
            source_type="favorite",
            title=ingested_candidate.title,
        )
        db.add(raw_source)
        db.flush()
        ingested_ledger.raw_source_id = raw_source.id
        db.commit()

        resp = client.post("/api/sync/clear-history", json={"platform": "xiaohongshu"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cleared"
        assert body["scan_entry_count"] == 2
        assert body["candidate_count"] == 1
        assert body["ledger_count"] == 1
        assert db.query(ScanEntry).filter(ScanEntry.platform == "xiaohongshu").count() == 0
        assert db.query(CandidateItem).filter(CandidateItem.external_item_id == "65fabc1234567890abcdef12").count() == 0
        assert db.query(SyncLedgerItem).filter(SyncLedgerItem.external_item_id == "65fabc1234567890abcdef12").count() == 0
        assert db.query(CandidateItem).filter(CandidateItem.external_item_id == "65fabc1234567890abcdef34").count() == 1
        assert db.query(SyncLedgerItem).filter(SyncLedgerItem.external_item_id == "65fabc1234567890abcdef34").count() == 1
        assert db.query(RawSource).count() == 1
        connector = db.query(Connector).filter(Connector.platform == "xiaohongshu").first()
        assert connector is not None
        assert connector.history_saved is False
        assert connector.first_scan_done is False
    finally:
        app.dependency_overrides.clear()


def test_prepare_selected_reuses_unextracted_history_candidate_id(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        connector = Connector(platform="xiaohongshu", name="小红书收藏夹", connector_type="browser_xiaohongshu")
        db.add(connector)
        db.flush()
        candidate = CandidateItem(
            connector_id=connector.id,
            source_type="active_connector",
            platform="xiaohongshu",
            external_item_id="65fabc1234567890abcdef12",
            canonical_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
            raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12?xsec_token=abc",
            title="上次没提取的收藏",
            author="作者",
            content_type="note",
            metadata_json="{}",
            status="pending_classification",
        )
        db.add(candidate)
        db.flush()
        db.add(
            ScanEntry(
                platform="xiaohongshu",
                external_item_id="65fabc1234567890abcdef12",
                canonical_url=candidate.canonical_url,
                raw_url=candidate.raw_url,
                title=candidate.title,
                author=candidate.author,
                collection_kind="history",
                usefulness="useful",
                subcategory="AI Agent",
                candidate_id=candidate.id,
                extracted=False,
            )
        )
        db.add(
            SyncLedgerItem(
                connector_id=connector.id,
                platform="xiaohongshu",
                external_item_id=candidate.external_item_id,
                canonical_url=candidate.canonical_url,
                raw_url=candidate.raw_url,
                scan_run_id="previous_selected",
                classification_label="knowledge_selected",
                candidate_id=candidate.id,
            )
        )
        db.commit()

        class FakeResult:
            candidate_ids = []

            def as_dict(self):
                return {"candidate_ids": self.candidate_ids, "new_count": 0, "updated_count": 0, "skipped_count": 0}

        class FakeSyncService:
            def __init__(self, db):
                self.db = db

            async def import_items(self, connector, items, scan_run_id_prefix="import"):
                return FakeResult()

        monkeypatch.setattr("app.api.routes.SyncService", FakeSyncService)
        resp = client.post(
            "/api/sync/prepare-selected",
            json={
                "platform": "xiaohongshu",
                "selected_items": [
                    {
                        "url": candidate.raw_url,
                        "title": candidate.title,
                        "author": candidate.author,
                        "usefulness": "useful",
                        "subcategory": "AI Agent",
                    }
                ],
                "skipped_items": [],
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["candidate_ids"] == [candidate.id]
    finally:
        app.dependency_overrides.clear()



def test_prepare_selected_creates_candidate_for_previously_skipped_history_item():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        connector = Connector(platform="xiaohongshu", name="小红书收藏夹", connector_type="browser_xiaohongshu")
        db.add(connector)
        db.flush()
        raw_url = "https://www.xiaohongshu.com/explore/65fabc1234567890abcdef13?xsec_token=abc"
        canonical_url = "https://www.xiaohongshu.com/explore/65fabc1234567890abcdef13"
        db.add(
            ScanEntry(
                platform="xiaohongshu",
                external_item_id="65fabc1234567890abcdef13",
                canonical_url=canonical_url,
                raw_url=raw_url,
                title="上次保存但没入库的收藏",
                author="作者",
                collection_kind="history",
                usefulness="useful",
                subcategory="AI Agent",
                candidate_id=None,
                extracted=False,
            )
        )
        db.add(
            SyncLedgerItem(
                connector_id=connector.id,
                platform="xiaohongshu",
                external_item_id="65fabc1234567890abcdef13",
                canonical_url=canonical_url,
                raw_url=raw_url,
                scan_run_id="user_skipped_previous",
                classification_label="user_skipped",
                candidate_id=None,
            )
        )
        db.commit()

        resp = client.post(
            "/api/sync/prepare-selected",
            json={
                "platform": "xiaohongshu",
                "selected_items": [
                    {
                        "url": raw_url,
                        "title": "上次保存但没入库的收藏",
                        "author": "作者",
                        "usefulness": "useful",
                        "subcategory": "AI Agent",
                    }
                ],
                "skipped_items": [],
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["candidate_ids"]) == 1
        candidate_id = body["candidate_ids"][0]
        candidate = db.get(CandidateItem, candidate_id)
        assert candidate is not None
        assert candidate.title == "上次保存但没入库的收藏"
        entry = db.query(ScanEntry).filter(ScanEntry.external_item_id == "65fabc1234567890abcdef13").first()
        assert entry.candidate_id == candidate_id
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.external_item_id == "65fabc1234567890abcdef13").first()
        assert ledger.candidate_id == candidate_id
        assert ledger.classification_label == "knowledge_selected"
    finally:
        app.dependency_overrides.clear()



def test_prepare_selected_accepts_existing_candidate_id_without_reimport(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        connector = Connector(platform="xiaohongshu", name="小红书收藏夹", connector_type="browser_xiaohongshu")
        db.add(connector)
        db.flush()
        candidate = CandidateItem(
            connector_id=connector.id,
            source_type="active_connector",
            platform="xiaohongshu",
            external_item_id="65fabc1234567890abcdef12",
            canonical_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12",
            raw_url="https://www.xiaohongshu.com/explore/65fabc1234567890abcdef12?xsec_token=abc",
            title="上次没提取的收藏",
            content_type="note",
            metadata_json="{}",
            status="pending_classification",
        )
        db.add(candidate)
        db.commit()

        class FakeSyncService:
            def __init__(self, db):
                self.db = db

            async def import_items(self, connector, items, scan_run_id_prefix="import"):
                raise AssertionError("existing candidate_id should not be reimported")

        monkeypatch.setattr("app.api.routes.SyncService", FakeSyncService)
        resp = client.post(
            "/api/sync/prepare-selected",
            json={"platform": "xiaohongshu", "selected_items": [{"candidate_id": candidate.id}], "skipped_items": []},
        )

        assert resp.status_code == 200
        assert resp.json()["candidate_ids"] == [candidate.id]
    finally:
        app.dependency_overrides.clear()



def test_save_history_endpoint_rejects_unknown_platform():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        resp = client.post("/api/sync/save-history", json={"platform": "not_a_platform"})
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()
