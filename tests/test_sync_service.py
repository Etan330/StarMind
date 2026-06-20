import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connectors.base import ConnectorItem
from app.database import Base
from app.models import CandidateItem, Connector, RawSource, SyncLedgerItem, WikiPage
from app.services import RawSourceService, SyncService, WikiMaintenanceService


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory()


class FakeWikiProvider:
    async def chat(self, messages, model, temperature=0.2):
        return (
            "## Skill 名称\n\n"
            "抖音收藏处理 Skill\n\n"
            "## 可调用性评估\n\n"
            "- 分数：82 / 100\n"
            "- 结论：建议启用。\n"
        )


def test_mock_scan_creates_candidates_and_ledger_then_stops_on_boundary():
    db = make_session()
    connector = Connector(name="Mock Connector", platform="mock", connector_type="mock")
    db.add(connector)
    db.commit()
    db.refresh(connector)

    first_result = asyncio.run(SyncService(db).scan_connector(connector.id))

    assert first_result.new_count == 19
    assert first_result.duplicate_in_run_count == 1
    assert first_result.boundary_hit is False
    assert db.query(CandidateItem).count() == 19
    assert db.query(SyncLedgerItem).count() == 19

    second_result = asyncio.run(SyncService(db).scan_connector(connector.id))

    assert second_result.new_count == 0
    assert second_result.boundary_hit is True
    assert db.query(CandidateItem).count() == 19
    assert db.query(SyncLedgerItem).count() == 19


def test_import_douyin_visible_links_creates_candidates_and_dedupes():
    db = make_session()
    connector = Connector(name="抖音收藏夹", platform="douyin", connector_type="browser_douyin")
    db.add(connector)
    db.commit()
    db.refresh(connector)

    items = [
        ConnectorItem(
            raw_url="https://www.douyin.com/video/7380000112233?utm_source=share",
            title="抖音收藏视频",
            platform="douyin",
            content_type="video",
            metadata={"page_text": "这是页面上可见的文案"},
        ),
        ConnectorItem(
            raw_url="https://www.douyin.com/video/7380000112233?utm_source=share",
            title="重复视频",
            platform="douyin",
            content_type="video",
        ),
    ]

    first_result = asyncio.run(SyncService(db).import_items(connector, items, "douyin_visible"))
    second_result = asyncio.run(SyncService(db).import_items(connector, items, "douyin_visible"))

    assert first_result.scanned_count == 2
    assert first_result.new_count == 1
    assert first_result.duplicate_in_run_count == 1
    assert second_result.new_count == 0
    assert second_result.duplicate_in_run_count == 2
    assert db.query(CandidateItem).count() == 1
    assert db.query(SyncLedgerItem).count() == 1


def test_candidate_can_generate_raw_source_and_wiki_page(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()
    candidate = CandidateItem(
        source_type="active_connector",
        platform="douyin",
        external_item_id="7380000112233",
        canonical_url="https://www.douyin.com/video/7380000112233",
        raw_url="https://www.douyin.com/video/7380000112233",
        title="抖音收藏视频",
        content_type="video",
        metadata_json=json.dumps({"page_text": "这是页面上可见的文案"}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add(candidate)
    db.flush()
    db.add(
        SyncLedgerItem(
            platform="douyin",
            external_item_id="7380000112233",
            canonical_url=candidate.canonical_url,
            raw_url=candidate.raw_url,
            scan_run_id="test",
            candidate_id=candidate.id,
        )
    )
    db.commit()
    db.refresh(candidate)

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    page = asyncio.run(WikiMaintenanceService(db).create_page_from_raw_source(raw_source.id))

    assert db.query(RawSource).count() == 1
    assert db.query(WikiPage).count() == 1
    assert db.get(CandidateItem, candidate.id).status == "ingested"
    assert "page_text_draft" in raw_source.metadata_json
    assert "这是页面上可见的文案" in open(raw_source.transcript_path, encoding="utf-8").read()
    assert "抖音收藏视频" in open(page.markdown_path, encoding="utf-8").read()


def test_raw_source_can_generate_agent_skill(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()
    candidate = CandidateItem(
        source_type="active_connector",
        platform="douyin",
        external_item_id="7380000112234",
        canonical_url="https://www.douyin.com/video/7380000112234",
        raw_url="https://www.douyin.com/video/7380000112234",
        title="收藏处理教程",
        content_type="video",
        metadata_json=json.dumps({"page_text": "教你如何把收藏沉淀成 SOP"}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add(candidate)
    db.commit()

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    page = asyncio.run(WikiMaintenanceService(db).create_page_from_raw_source(raw_source.id, page_type="skill"))
    body = open(page.markdown_path, encoding="utf-8").read()

    assert page.page_type == "skill"
    assert page.title.startswith("Skill：")
    assert "可调用性评估" in body
