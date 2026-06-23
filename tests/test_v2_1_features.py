"""Tests for new V2.1 features: CDP proxy, graph, push, lint, onboarding, scheduler."""
import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Connector, CandidateItem, KnowledgeClassification, KnowledgeGraphEdge,
    OnboardingStatus, PushHistory, PushSettings, RawSource, UserPreference,
)


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_new_models_create():
    db = make_session()
    # OnboardingStatus
    db.add(OnboardingStatus(current_step=3, skipped=False))
    # UserPreference
    db.add(UserPreference(domain="AI/大模型", score=85))
    # PushSettings
    db.add(PushSettings(start_time="09:00", end_time="21:00", frequency_hours=2, items_per_push=3, is_paused=False))
    db.commit()

    assert db.query(OnboardingStatus).first().current_step == 3
    assert db.query(UserPreference).first().score == 85
    assert db.query(PushSettings).first().frequency_hours == 2


def test_knowledge_graph_edge():
    db = make_session()
    # Need raw sources first
    src1 = RawSource(platform="bilibili", source_url="https://bilibili.com/video/BV1", canonical_url="https://bilibili.com/video/BV1",
                     external_item_id="BV1", source_type="video", title="RAG 入门")
    src2 = RawSource(platform="bilibili", source_url="https://bilibili.com/video/BV2", canonical_url="https://bilibili.com/video/BV2",
                     external_item_id="BV2", source_type="video", title="向量数据库")
    db.add_all([src1, src2])
    db.commit()

    edge = KnowledgeGraphEdge(source_id=src1.id, target_id=src2.id, relation="topic_overlap", weight=0.85,
                              shared_concepts_json=json.dumps(["RAG", "向量"]))
    db.add(edge)
    db.commit()

    assert db.query(KnowledgeGraphEdge).count() == 1
    assert db.query(KnowledgeGraphEdge).first().weight == 0.85


def test_graph_service_detect_orphans():
    db = make_session()
    src1 = RawSource(platform="test", source_url="u1", canonical_url="u1", external_item_id="e1", source_type="video", title="A")
    src2 = RawSource(platform="test", source_url="u2", canonical_url="u2", external_item_id="e2", source_type="video", title="B")
    src3 = RawSource(platform="test", source_url="u3", canonical_url="u3", external_item_id="e3", source_type="video", title="C")
    db.add_all([src1, src2, src3])
    db.commit()

    # Only connect src1 and src2
    db.add(KnowledgeGraphEdge(source_id=src1.id, target_id=src2.id, relation="topic_overlap", weight=0.5))
    db.commit()

    from app.services.graph_service import GraphService
    orphans = GraphService(db).detect_orphans()
    assert src3.id in orphans
    assert src1.id not in orphans


def test_graph_service_get_graph_data():
    db = make_session()
    src1 = RawSource(platform="test", source_url="u1", canonical_url="u1", external_item_id="e1", source_type="video", title="Topic A",
                     metadata_json=json.dumps({"domain": "AI", "related_concepts": ["RAG"]}))
    src2 = RawSource(platform="test", source_url="u2", canonical_url="u2", external_item_id="e2", source_type="video", title="Topic B",
                     metadata_json=json.dumps({"domain": "AI", "related_concepts": ["RAG"]}))
    db.add_all([src1, src2])
    db.commit()
    db.add(KnowledgeGraphEdge(source_id=src1.id, target_id=src2.id, relation="topic_overlap", weight=0.8, shared_concepts_json='["RAG"]'))
    db.commit()

    from app.services.graph_service import GraphService
    data = GraphService(db).get_graph_data()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["weight"] == 0.8


def test_push_service_no_crash_empty():
    db = make_session()
    from app.services.push_service import PushService
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(PushService(db).generate_push())
    loop.close()
    assert result == []


def test_connector_auto_sync_fields():
    db = make_session()
    c = Connector(name="Test", platform="bilibili", connector_type="browser_bilibili", auto_sync_enabled=True, auto_sync_cron="0 0 * * *")
    db.add(c)
    db.commit()
    assert db.query(Connector).first().auto_sync_enabled is True
    assert db.query(Connector).first().auto_sync_cron == "0 0 * * *"


def test_lint_agent_runs_without_error():
    db = make_session()
    from app.agent.lint_agent import LintAgent
    loop = asyncio.new_event_loop()
    report = loop.run_until_complete(LintAgent(db).run_full_check())
    loop.close()
    assert "findings" in report
    assert report["total"] == 0  # empty DB = no findings
