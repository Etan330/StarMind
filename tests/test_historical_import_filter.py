import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connectors.base import ConnectorItem
from app.database import Base, get_db
from app.main import app
from app.models import CandidateItem, SyncLedgerItem


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_prepare_selected_imports_only_kept_items_and_records_skipped(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        payload = {
            "platform": "douyin",
            "selected_items": [
                {
                    "url": "https://www.douyin.com/video/7380000112233?utm_source=share",
                    "title": "AI Agent 教程",
                    "author": "老师",
                    "content_type": "video",
                    "usefulness": "useful",
                    "subcategory": "AI/大模型",
                    "reason": "可复用教程",
                    "confidence": 0.91,
                }
            ],
            "skipped_items": [
                {
                    "url": "https://www.douyin.com/video/7380000112244?utm_source=share",
                    "title": "搞笑日常",
                    "author": "博主",
                    "content_type": "video",
                    "usefulness": "useless",
                    "subcategory": "娱乐消遣",
                    "reason": "低信息密度",
                    "confidence": 0.87,
                }
            ],
        }

        response = client.post("/api/sync/prepare-selected", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["selected_count"] == 1
        assert body["skipped_count"] == 1
        assert len(body["candidate_ids"]) == 1

        candidate = db.query(CandidateItem).one()
        metadata = json.loads(candidate.metadata_json)
        assert metadata["filter_usefulness"] == "useful"
        assert metadata["filter_subcategory"] == "AI/大模型"
        assert metadata["filter_reason"] == "可复用教程"
        assert candidate.content_type == "video"

        ledgers = db.query(SyncLedgerItem).order_by(SyncLedgerItem.raw_url).all()
        assert len(ledgers) == 2
        assert [ledger.classification_label for ledger in ledgers] == ["knowledge_selected", "user_skipped"]
        assert ledgers[0].candidate_id == candidate.id
        assert ledgers[1].candidate_id is None
    finally:
        app.dependency_overrides.clear()


def test_scan_titles_returns_content_type_and_metadata(monkeypatch):
    db = make_session()

    def override_get_db():
        yield db

    async def fake_extract(url=None, limit=500):
        assert url == "https://space.bilibili.com/351585377/favlist?spm_id_from=333.1007.0.0"
        return [
            ConnectorItem(
                raw_url="https://www.bilibili.com/video/BV1SM4y1K7ax?spm_id_from=333.999",
                title="B站教程",
                platform="bilibili",
                author="UP主",
                content_type="video",
                metadata={"source": "test"},
            )
        ]

    monkeypatch.setattr("app.connectors.bilibili_collector.extract_favorites", fake_extract)

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/sync/scan-titles",
            json={
                "platform": "bilibili",
                "limit": 1,
                "homepage_url": "https://space.bilibili.com/351585377/favlist?spm_id_from=333.1007.0.0",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["login_required"] is False
        assert body["items"][0]["content_type"] == "video"
        assert body["items"][0]["metadata"] == {"source": "test"}
    finally:
        app.dependency_overrides.clear()
