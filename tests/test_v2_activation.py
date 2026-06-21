from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CandidateItem, ProductEvent, RawSource, SyncLedgerItem, WikiPage


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_demo_result_is_read_only_and_tracked():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/ui/demo?demo_id=second-brain")

        assert response.status_code == 200
        assert "示例结果" in response.text
        assert db.query(CandidateItem).count() == 0
        assert db.query(RawSource).count() == 0
        assert db.query(WikiPage).count() == 0
        assert db.query(ProductEvent).filter(ProductEvent.event_name == "demo_viewed").count() == 1
    finally:
        app.dependency_overrides.clear()


def test_duplicate_link_redirects_to_existing_task():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        first = client.post(
            "/passive/link",
            data={"url": "https://example.com/post?utm_source=one", "title": "Duplicate source"},
            headers={"accept": "text/html"},
            follow_redirects=False,
        )
        assert first.status_code == 303
        candidate = db.query(CandidateItem).one()
        ledger = db.query(SyncLedgerItem).one()
        assert ledger.candidate_id == candidate.id

        second = client.post(
            "/passive/link",
            data={"url": "https://example.com/post?utm_source=two", "title": "Duplicate source"},
            headers={"accept": "text/html"},
            follow_redirects=False,
        )

        assert second.status_code == 303
        assert "duplicate=link" in second.headers["location"]
        assert f"existing_candidate_id={candidate.id}" in second.headers["location"]
        assert db.query(CandidateItem).count() == 1
        assert db.query(ProductEvent).filter(ProductEvent.event_name == "duplicate_detected").count() == 1
    finally:
        app.dependency_overrides.clear()


def test_event_export_uses_temporary_adapter():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        client.get("/")
        response = client.get("/settings/events/export")

        assert response.status_code == 200
        payload = response.json()
        assert payload["temporary_adapter"] is True
        assert any(event["event_name"] == "page_viewed" for event in payload["events"])
    finally:
        app.dependency_overrides.clear()
