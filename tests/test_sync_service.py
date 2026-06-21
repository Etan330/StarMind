import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connectors.base import ConnectorItem
from app.api.routes import build_douyin_items
from app.database import Base
from app.models import CandidateItem, Connector, RawSource, SyncLedgerItem, WikiLog, WikiPage
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


def test_douyin_import_items_preserve_supplied_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    connector = Connector(name="抖音收藏夹", platform="douyin", connector_type="browser_douyin")
    db.add(connector)
    db.commit()
    db.refresh(connector)

    items = build_douyin_items(
        [
            {
                "href": "https://www.douyin.com/video/7380000112235",
                "title": "带逐字稿的抖音视频",
                "transcript": "这是 ASR 得到的完整逐字稿，应该进入原始资料。",
            }
        ],
        limit=1,
    )

    result = asyncio.run(SyncService(db).import_items(connector, items, "douyin_visible"))
    raw_source = RawSourceService(db).ingest_candidate(result.candidate_ids[0])
    transcript = open(raw_source.transcript_path, encoding="utf-8").read()

    assert "provided" in raw_source.metadata_json
    assert "这是 ASR 得到的完整逐字稿" in transcript


def test_manual_idea_content_is_provided_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    candidate = CandidateItem(
        source_type="manual_idea",
        platform="手动录入",
        external_item_id="manual-skill",
        canonical_url="starmind://idea/manual-skill",
        raw_url="starmind://idea/manual-skill",
        title="产品运营 Skill 设计",
        content_type="note",
        metadata_json=json.dumps({"content": "产品运营 Skill 应该覆盖拉新、激活、留存和复盘。"}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add(candidate)
    db.commit()

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    transcript = open(raw_source.transcript_path, encoding="utf-8").read()

    assert "provided" in raw_source.metadata_json
    assert "产品运营 Skill 应该覆盖拉新" in transcript


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
    assert page.status == "needs_review"
    assert db.query(WikiLog).count() == 1


def test_duplicate_raw_source_ingest_links_without_overwriting_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    first = CandidateItem(
        source_type="passive_link",
        platform="example",
        external_item_id="first",
        canonical_url="https://example.com/same",
        raw_url="https://example.com/same?utm_source=first",
        title="Original title",
        content_type="link",
        metadata_json=json.dumps({"page_text": "Original fact text"}, ensure_ascii=False),
        status="pending_classification",
    )
    second = CandidateItem(
        source_type="passive_link",
        platform="example",
        external_item_id="second",
        canonical_url="https://example.com/same",
        raw_url="https://example.com/same?utm_source=second",
        title="New title should not overwrite",
        content_type="link",
        metadata_json=json.dumps({"page_text": "New text should not overwrite"}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add_all([first, second])
    db.flush()
    db.add(
        SyncLedgerItem(
            platform="example",
            external_item_id="second",
            canonical_url=second.canonical_url,
            raw_url=second.raw_url,
            scan_run_id="duplicate",
            candidate_id=second.id,
        )
    )
    db.commit()

    service = RawSourceService(db)
    raw_source = service.ingest_candidate(first.id)
    transcript_path = raw_source.transcript_path
    with open(transcript_path, "w", encoding="utf-8") as handle:
        handle.write("USER PRESERVED RAW SOURCE")

    duplicate = service.ingest_candidate(second.id)

    assert duplicate.id == raw_source.id
    assert db.get(CandidateItem, second.id).status == "ingested"
    assert db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == second.id).one().raw_source_id == raw_source.id
    assert open(transcript_path, encoding="utf-8").read() == "USER PRESERVED RAW SOURCE"


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
