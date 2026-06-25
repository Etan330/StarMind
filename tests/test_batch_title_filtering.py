import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.services.classifier_service import ClassifierService


class FakeBatchProvider:
    async def json_chat(self, messages, model, schema=None):
        return {
            "items": [
                {
                    "index": 1,
                    "usefulness": "useful",
                    "subcategory": "AI/大模型",
                    "confidence": 0.93,
                    "reason": "可复用教程",
                },
                {
                    "index": 2,
                    "usefulness": "useless",
                    "subcategory": "娱乐消遣",
                    "confidence": 0.88,
                    "reason": "低信息密度",
                },
            ]
        }


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_batch_classify_titles_groups_by_usefulness_and_subcategory(monkeypatch):
    db = make_session()
    monkeypatch.setattr(
        "app.services.classifier_service.get_provider_runtime",
        lambda provider_id=None, model=None: (FakeBatchProvider(), "fake-model", {}),
    )

    result = asyncio.run(
        ClassifierService(db).batch_classify_titles(
            [
                {"url": "https://example.com/ai", "title": "AI Agent 教程"},
                {"url": "https://example.com/fun", "title": "搞笑日常"},
            ]
        )
    )

    assert result["summary"] == {"useful_count": 1, "useless_count": 1}
    assert result["groups"][0]["usefulness"] == "useful"
    assert result["groups"][0]["subcategory"] == "AI/大模型"
    assert result["groups"][0]["items"][0]["reason"] == "可复用教程"
    assert result["groups"][1]["usefulness"] == "useless"
    assert result["groups"][1]["subcategory"] == "娱乐消遣"
    assert result["categories"][0]["domain"] == "AI/大模型"


def test_batch_classify_titles_fallback_marks_low_value_titles_useless(monkeypatch):
    db = make_session()

    class FailingProvider:
        async def json_chat(self, messages, model, schema=None):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(
        "app.services.classifier_service.get_provider_runtime",
        lambda provider_id=None, model=None: (FailingProvider(), "fake-model", {}),
    )

    result = asyncio.run(
        ClassifierService(db).batch_classify_titles(
            [
                {"url": "https://example.com/ai", "title": "AI Agent RAG 架构教程"},
                {"url": "https://example.com/fun", "title": "搞笑抽奖明星娱乐视频"},
            ]
        )
    )

    useful_items = [item for group in result["groups"] if group["usefulness"] == "useful" for item in group["items"]]
    useless_items = [item for group in result["groups"] if group["usefulness"] == "useless" for item in group["items"]]
    assert useful_items[0]["subcategory"] == "AI/大模型"
    assert useless_items[0]["subcategory"] == "娱乐消遣"
