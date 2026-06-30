"""ScanEntryService 单测：history/incremental kind 切换、去重、boundary、分类回写、mark_extracted、backfill。

全部用内存 SQLite（create_all 每次全新），不触真库。
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connectors.base import ConnectorItem
from app.database import Base
from app.models import CandidateItem, Connector, RawSource, ScanEntry, SyncLedgerItem
from app.services.scan_entry_service import HISTORY, INCREMENTAL, ScanEntryService


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _item(ext: str, title: str = "标题", publish: str | None = None):
    raw_url = f"https://www.douyin.com/video/{ext}"
    meta = {"source": "test"}
    if publish is not None:
        meta["publish_time"] = publish
    return ConnectorItem(
        raw_url=raw_url,
        title=title,
        platform="douyin",
        content_type="video",
        metadata=meta,
    )


def test_determine_kind_history_when_no_connector():
    db = make_session()
    assert ScanEntryService(db).determine_kind("douyin") == HISTORY


def test_determine_kind_incremental_after_first_scan_done():
    db = make_session()
    db.add(Connector(name="抖音收藏夹", platform="douyin", first_scan_done=True))
    db.commit()
    assert ScanEntryService(db).determine_kind("douyin") == INCREMENTAL


def test_record_boundary_sets_first_scan_done_and_anchor():
    db = make_session()
    connector = Connector(name="抖音收藏夹", platform="douyin")
    db.add(connector)
    db.commit()
    svc = ScanEntryService(db)
    items = [_item("7001"), _item("7002")]
    svc.record_boundary("douyin", items)
    db.refresh(connector)
    assert connector.first_scan_done is True
    assert connector.history_boundary_external_id == "7001"


def test_is_history_saved_defaults_false_without_connector():
    db = make_session()
    assert ScanEntryService(db).is_history_saved("douyin") is False


def test_set_history_saved_creates_connector_when_missing_and_flips():
    db = make_session()
    svc = ScanEntryService(db)
    # 缺 connector：saved=True 时仿 record_boundary 建一行最小 connector
    svc.set_history_saved("douyin", True)
    connector = db.query(Connector).filter(Connector.platform == "douyin").first()
    assert connector is not None
    assert connector.history_saved is True
    assert svc.is_history_saved("douyin") is True
    # 翻回 False
    svc.set_history_saved("douyin", False)
    db.refresh(connector)
    assert connector.history_saved is False
    assert svc.is_history_saved("douyin") is False


def test_set_history_saved_false_without_connector_is_noop():
    db = make_session()
    svc = ScanEntryService(db)
    # saved=False 且无 connector：不建行，直接返回
    svc.set_history_saved("douyin", False)
    assert db.query(Connector).filter(Connector.platform == "douyin").count() == 0


def test_reset_history_clears_flag_first_scan_done_and_history_entries():
    db = make_session()
    svc = ScanEntryService(db)
    # 扫描落两条历史 + 一条新增 + 标记 first_scan_done + history_saved
    svc.upsert_from_items("douyin", [_item("7001"), _item("7002")], HISTORY, "run1")
    svc.upsert_from_items("douyin", [_item("8001")], INCREMENTAL, "run2")
    svc.record_boundary("douyin", [_item("7001"), _item("7002")])
    svc.set_history_saved("douyin", True)
    connector = db.query(Connector).filter(Connector.platform == "douyin").first()
    assert connector.first_scan_done is True
    assert connector.history_saved is True
    assert db.query(ScanEntry).filter(ScanEntry.platform == "douyin", ScanEntry.collection_kind == HISTORY).count() == 2

    svc.reset_history("douyin")

    db.refresh(connector)
    # 关键：first_scan_done 也被清，否则下次扫描 determine_kind→incremental→全滤掉
    assert connector.first_scan_done is False
    assert connector.history_saved is False
    assert connector.history_boundary_external_id is None
    # 重新扫描历史要给用户一个干净的历史集合；新增记录不属于历史重扫范围，保留。
    assert db.query(ScanEntry).filter(ScanEntry.platform == "douyin", ScanEntry.collection_kind == HISTORY).count() == 0
    assert db.query(ScanEntry).filter(ScanEntry.platform == "douyin", ScanEntry.collection_kind == INCREMENTAL).count() == 1
    # 清完后 determine_kind 重新回到 history
    assert svc.determine_kind("douyin") == HISTORY


def test_reset_history_without_connector_is_noop():
    db = make_session()
    # 无 connector 时不报错
    ScanEntryService(db).reset_history("douyin")
    assert db.query(Connector).filter(Connector.platform == "douyin").count() == 0


def test_upsert_persists_and_returns_scan_entry_id():
    db = make_session()
    svc = ScanEntryService(db)
    entries = svc.upsert_from_items("douyin", [_item("7001", publish="2024-05-01")], HISTORY, "run1")
    assert len(entries) == 1
    assert entries[0]["scan_entry_id"]
    assert entries[0]["collection_kind"] == HISTORY
    assert entries[0]["published_at"] == "2024-05-01"
    # _to_dict 回传原始 metadata（含 publish_time/source），供前端/旧契约消费
    assert entries[0]["metadata"]["publish_time"] == "2024-05-01"
    assert entries[0]["metadata"]["source"] == "test"
    row = db.query(ScanEntry).filter(ScanEntry.external_item_id == "7001").first()
    assert row is not None and row.platform == "douyin"


def test_upsert_idempotent_does_not_duplicate_or_clobber_classification():
    db = make_session()
    svc = ScanEntryService(db)
    svc.upsert_from_items("douyin", [_item("7001")], HISTORY, "run1")
    entry = db.query(ScanEntry).filter(ScanEntry.external_item_id == "7001").first()
    entry.usefulness = "useful"
    entry.extracted = True
    db.commit()
    # 第二次扫描同一条：不新建、不覆盖 usefulness/extracted
    svc.upsert_from_items("douyin", [_item("7001", title="新标题")], INCREMENTAL, "run2")
    rows = db.query(ScanEntry).filter(ScanEntry.external_item_id == "7001").all()
    assert len(rows) == 1
    assert rows[0].usefulness == "useful"
    assert rows[0].extracted is True
    assert rows[0].collection_kind == HISTORY  # 不被改成 incremental
    assert rows[0].title == "新标题"  # 展示字段可更新


def test_filter_incremental_stops_at_first_already_seen_boundary():
    db = make_session()
    svc = ScanEntryService(db)
    svc.upsert_from_items("douyin", [_item("7001")], HISTORY, "run1")
    # ledger 里也算已见
    db.add(
        SyncLedgerItem(
            platform="douyin",
            external_item_id="7002",
            canonical_url="https://www.douyin.com/video/7002",
            raw_url="https://www.douyin.com/video/7002",
            scan_run_id="x",
        )
    )
    db.commit()
    kept = svc.filter_incremental("douyin", [_item("7003"), _item("7001"), _item("7002")])
    kept_ext = {i.raw_url.rsplit("/", 1)[-1] for i in kept}
    assert kept_ext == {"7003"}


def test_apply_classification_writes_back_by_scan_entry_id():
    db = make_session()
    svc = ScanEntryService(db)
    entries = svc.upsert_from_items("douyin", [_item("7001")], HISTORY, "run1")
    sid = entries[0]["scan_entry_id"]
    updated = svc.apply_classification(
        [{"scan_entry_id": sid, "usefulness": "useful", "subcategory": "AI/大模型", "confidence": 0.9, "reason": "含 AI"}]
    )
    assert updated == 1
    row = db.get(ScanEntry, sid)
    assert row.usefulness == "useful"
    assert row.subcategory == "AI/大模型"
    assert row.confidence == 0.9
    assert row.reason == "含 AI"
    assert row.classified_at is not None


def test_link_candidate_and_mark_extracted():
    db = make_session()
    svc = ScanEntryService(db)
    svc.upsert_from_items("douyin", [_item("7001")], HISTORY, "run1")
    candidate = CandidateItem(
        platform="douyin",
        external_item_id="7001",
        canonical_url="https://www.douyin.com/video/7001",
        raw_url="https://www.douyin.com/video/7001",
        title="标题",
    )
    db.add(candidate)
    db.commit()
    svc.link_candidate("douyin", candidate.id)
    entry = db.query(ScanEntry).filter(ScanEntry.external_item_id == "7001").first()
    assert entry.candidate_id == candidate.id

    raw = RawSource(
        candidate_id=candidate.id,
        platform="douyin",
        source_url="https://www.douyin.com/video/7001",
        canonical_url="https://www.douyin.com/video/7001",
        external_item_id="7001",
        source_type="video",
        title="标题",
    )
    db.add(raw)
    db.commit()
    svc.mark_extracted(candidate.id, raw.id)
    db.refresh(entry)
    assert entry.extracted is True
    assert entry.raw_source_id == raw.id


def test_mark_extracted_falls_back_to_external_id_when_not_linked():
    db = make_session()
    svc = ScanEntryService(db)
    svc.upsert_from_items("douyin", [_item("7001")], HISTORY, "run1")
    candidate = CandidateItem(
        platform="douyin",
        external_item_id="7001",
        canonical_url="https://www.douyin.com/video/7001",
        raw_url="https://www.douyin.com/video/7001",
        title="标题",
    )
    db.add(candidate)
    db.commit()
    raw = RawSource(
        candidate_id=candidate.id,
        platform="douyin",
        source_url="https://www.douyin.com/video/7001",
        canonical_url="https://www.douyin.com/video/7001",
        external_item_id="7001",
        source_type="video",
        title="标题",
    )
    db.add(raw)
    db.commit()
    # 没先 link_candidate，mark_extracted 仍能按 external_item_id 反查命中
    svc.mark_extracted(candidate.id, raw.id)
    entry = db.query(ScanEntry).filter(ScanEntry.external_item_id == "7001").first()
    assert entry.extracted is True
    assert entry.candidate_id == candidate.id
    assert entry.raw_source_id == raw.id


def test_list_entries_backfills_shadow_entries_from_existing_candidates():
    db = make_session()
    candidate = CandidateItem(
        platform="douyin",
        external_item_id="9001",
        canonical_url="https://www.douyin.com/video/9001",
        raw_url="https://www.douyin.com/video/9001",
        title="存量候选",
        metadata_json=json.dumps({"doubao_extracted": True, "filter_usefulness": "useful"}, ensure_ascii=False),
    )
    db.add(candidate)
    db.commit()
    raw = RawSource(
        candidate_id=candidate.id,
        platform="douyin",
        source_url="https://www.douyin.com/video/9001",
        canonical_url="https://www.douyin.com/video/9001",
        external_item_id="9001",
        source_type="video",
        title="存量候选",
    )
    db.add(raw)
    db.commit()

    svc = ScanEntryService(db)
    entries = svc.list_entries("douyin")
    assert len(entries) == 1
    assert entries[0]["external_item_id"] == "9001"
    assert entries[0]["extracted"] is True
    assert entries[0]["usefulness"] == "useful"
    assert entries[0]["raw_source_id"] == raw.id
    # 幂等：再次 list 不重复建影子
    entries2 = svc.list_entries("douyin")
    assert len(entries2) == 1
