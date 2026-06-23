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


def test_v3_homepage_is_input_first():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        assert "别让收藏夹吃灰，把碎片内容变成可追问的知识" in response.text
        assert "问问你的知识库" in response.text
        assert "同步收藏夹" in response.text
        assert "导入链接" in response.text
        assert "蒸馏博主" in response.text
        assert "实时记录 Idea" in response.text
        assert "导入后会发生什么" in response.text
        assert "V2 Local Knowledge Workbench" not in response.text
        assert db.query(ProductEvent).filter(ProductEvent.event_name == "v3_home_viewed").count() == 1
    finally:
        app.dependency_overrides.clear()


def test_v3_empty_home_input_redirects_to_recoverable_error():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/ui/v3/input", data={"content": "", "entry_mode": "link"}, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/?input_error=empty&entry_mode=link"
        assert db.query(ProductEvent).filter(ProductEvent.event_name == "v3_task_create_failed").count() == 1
    finally:
        app.dependency_overrides.clear()


def test_v3_home_input_routes_link_to_confirmation_page():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/ui/v3/input",
            data={"content": "https://example.com/article", "entry_mode": "link"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith("/ui/create?")
        assert "mode=link" in location
        assert "input_type=link" in location
        assert "prefill=https%3A%2F%2Fexample.com%2Farticle" in location
        assert db.query(ProductEvent).filter(ProductEvent.event_name == "v3_primary_input_submitted").count() == 1

        confirmation = client.get(location)
        assert confirmation.status_code == 200
        assert "确认你的输入与处理方式" in confirmation.text
        assert "来源证据" in confirmation.text
        assert "确认并开始蒸馏" in confirmation.text
    finally:
        app.dependency_overrides.clear()


def test_v3_ui_event_adapter_records_safe_event():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/events/v3",
            data={"event_name": "v3_entry_clicked", "entry_mode": "idea", "entry": "记录 Idea"},
        )

        assert response.status_code == 200
        event = db.query(ProductEvent).filter(ProductEvent.event_name == "v3_entry_clicked").one()
        assert "记录 Idea" in event.properties_json
        assert "idea" in event.properties_json
    finally:
        app.dependency_overrides.clear()
