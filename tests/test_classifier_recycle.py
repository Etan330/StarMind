import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CandidateItem, KnowledgeClassification, RawSource, RecycleBinItem
from app.services.classifier_service import ClassifierService
from app.services.recycle_service import RecycleService
from app.services.statuses import ARCHIVED_RECOVERABLE, CLASSIFIED_KNOWLEDGE, PENDING_CLASSIFICATION


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload

    async def json_chat(self, messages, model, schema=None):
        return self.payload


def add_candidate(db, title="AI Agent 教程", status=PENDING_CLASSIFICATION):
    candidate = CandidateItem(
        source_type="active_connector",
        platform="douyin",
        external_item_id=title,
        canonical_url=f"https://example.com/{title}",
        raw_url=f"https://example.com/{title}",
        title=title,
        metadata_json=json.dumps({"page_text": title}, ensure_ascii=False),
        status=status,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def test_classifier_marks_high_confidence_knowledge(monkeypatch):
    db = make_session()
    candidate = add_candidate(db)
    monkeypatch.setattr(
        "app.services.classifier_service.get_provider_runtime",
        lambda provider_id=None, model=None: (
            FakeProvider(
                {
                    "is_knowledge": True,
                    "label": "knowledge",
                    "confidence": 0.91,
                    "knowledge_type": ["教程"],
                    "reason": "包含可复用方法。",
                    "decision": "ingest_to_raw_sources",
                }
            ),
            "fake-model",
            {},
        ),
    )

    result = asyncio.run(ClassifierService(db).classify_candidate(candidate.id))

    assert result.status == CLASSIFIED_KNOWLEDGE
    assert db.get(CandidateItem, candidate.id).status == CLASSIFIED_KNOWLEDGE
    assert db.query(KnowledgeClassification).count() == 1


def test_non_knowledge_goes_to_recoverable_recycle_and_can_restore(monkeypatch):
    db = make_session()
    candidate = add_candidate(db, title="搞笑抽奖视频")
    monkeypatch.setattr(
        "app.services.classifier_service.get_provider_runtime",
        lambda provider_id=None, model=None: (
            FakeProvider(
                {
                    "is_knowledge": False,
                    "label": "non_knowledge",
                    "confidence": 0.92,
                    "knowledge_type": [],
                    "reason": "低信息量娱乐内容。",
                    "decision": "archive_to_recycle_bin",
                }
            ),
            "fake-model",
            {},
        ),
    )

    result = asyncio.run(ClassifierService(db).classify_candidate(candidate.id))
    recycle_item = db.query(RecycleBinItem).one()

    assert result.status == ARCHIVED_RECOVERABLE
    assert recycle_item.status == ARCHIVED_RECOVERABLE

    restored = RecycleService(db).restore(recycle_item.id)

    recycle_item_id = recycle_item.id

    assert restored.status == PENDING_CLASSIFICATION
    assert db.get(RecycleBinItem, recycle_item_id) is None


def test_restore_candidate_rebuilds_missing_original_candidate_as_raw_source():
    db = make_session()
    recycle_item = RecycleBinItem(
        item_type="candidate",
        candidate_id=999,
        canonical_url="https://example.com/deleted-candidate",
        external_item_id="deleted-candidate",
        title="原始候选已丢失",
        platform="douyin",
        reason="user_recycled",
        status=ARCHIVED_RECOVERABLE,
    )
    db.add(recycle_item)
    db.commit()
    recycle_item_id = recycle_item.id

    restored = RecycleService(db).restore(recycle_item_id)

    assert isinstance(restored, RawSource)
    assert restored.candidate_id is None
    assert restored.canonical_url == "https://example.com/deleted-candidate"
    assert restored.title == "原始候选已丢失"
    assert db.get(RecycleBinItem, recycle_item_id) is None


def test_archive_raw_source_deletes_source_and_creates_recycle_item():
    db = make_session()
    source = RawSource(
        candidate_id=None,
        platform="douyin",
        source_url="https://example.com/raw-source",
        canonical_url="https://example.com/raw-source",
        external_item_id="raw-source",
        source_type="link",
        title="待删除原始资料",
        author="作者",
        metadata_json='{"kind":"raw"}',
    )
    db.add(source)
    db.commit()
    source_id = source.id

    recycle_item = RecycleService(db).archive_raw_source(source_id)

    assert db.get(RawSource, source_id) is None
    assert recycle_item.item_type == "raw_source"
    assert recycle_item.source_label == "原始资料"
    assert recycle_item.title == "待删除原始资料"
    assert recycle_item.status == "archived"
    snap = json.loads(recycle_item.raw_source_snapshot_json)
    assert snap["canonical_url"] == "https://example.com/raw-source"
    assert snap["metadata_json"] == '{"kind":"raw"}'


def test_restore_raw_source_removes_recycle_record():
    db = make_session()
    recycle_item = RecycleBinItem(
        item_type="raw_source",
        candidate_id=None,
        canonical_url="https://example.com/raw-source",
        external_item_id="raw-source",
        title="待恢复原始资料",
        platform="douyin",
        reason="user_deleted",
        raw_source_snapshot_json=json.dumps(
            {
                "candidate_id": None,
                "platform": "douyin",
                "source_url": "https://example.com/raw-source",
                "canonical_url": "https://example.com/raw-source",
                "external_item_id": "raw-source",
                "source_type": "link",
                "title": "待恢复原始资料",
                "author": None,
                "metadata_json": "{}",
            },
            ensure_ascii=False,
        ),
    )
    db.add(recycle_item)
    db.commit()
    recycle_item_id = recycle_item.id

    restored = RecycleService(db).restore(recycle_item_id)

    assert isinstance(restored, RawSource)
    assert restored.canonical_url == "https://example.com/raw-source"
    assert db.get(RecycleBinItem, recycle_item_id) is None
