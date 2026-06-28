import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CandidateItem, RawSource, SyncLedgerItem
from app.services.raw_source_service import RawSourceService


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def create_candidate(db, *, platform="xiaohongshu", title="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8", metadata=None):
    candidate = CandidateItem(
        source_type="active_connector",
        platform=platform,
        external_item_id="item-1",
        canonical_url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8",
        raw_url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8",
        title=title,
        author="作者",
        content_type="note",
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add(candidate)
    db.flush()
    db.add(
        SyncLedgerItem(
            platform=platform,
            external_item_id=candidate.external_item_id,
            canonical_url=candidate.canonical_url,
            raw_url=candidate.raw_url,
            scan_run_id="selected",
            classification_label="knowledge_selected",
            candidate_id=candidate.id,
        )
    )
    db.commit()
    db.refresh(candidate)
    return candidate


def test_raw_source_title_uses_xiaohongshu_share_text_when_candidate_title_is_url(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare"
    candidate = create_candidate(
        db,
        title="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8",
        metadata={"xiaohongshu_share_text": share_text, "transcript": "正文内容"},
    )

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    transcript = open(raw_source.transcript_path, encoding="utf-8").read()
    raw_text = open(raw_source.raw_content_path, encoding="utf-8").read()

    assert raw_source.title == "Anthropic博客的Agent Eval实践心得"
    assert transcript.startswith("# Anthropic博客的Agent Eval实践心得\n")
    assert raw_text.startswith("# 原始资料：Anthropic博客的Agent Eval实践心得\n")


def test_raw_source_title_uses_share_text_when_candidate_title_is_share_url_fragment(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    share_text = "【20分钟AI做微信小程序｜保姆级全流程✅ | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/69b3e95b0000000023021b9c?source=webshare&xhsshare=pc_web"
    candidate = create_candidate(
        db,
        title="69b3e95b0000000023021b9c?source=webshare&xhsshare=pc_web&xse",
        metadata={"xiaohongshu_diandian_share_text": share_text, "transcript": "正文内容"},
    )

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    transcript = open(raw_source.transcript_path, encoding="utf-8").read()

    assert raw_source.title == "20分钟AI做微信小程序｜保姆级全流程✅"
    assert transcript.startswith("# 20分钟AI做微信小程序｜保姆级全流程✅\n")


def test_raw_source_title_uses_share_text_when_candidate_title_is_note_query_fragment(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    share_text = "【真实的小红书笔记标题 | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/69b3e95b0000000023021b9c?source=webshare&xhsshare=pc_web"
    candidate = create_candidate(
        db,
        title="69b3e95b0000000023021b9c?source=webshare&xhsshare=pc_web&xse",
        metadata={"xiaohongshu_diandian_share_text": share_text, "transcript": "正文内容"},
    )

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    transcript = open(raw_source.transcript_path, encoding="utf-8").read()

    assert raw_source.title == "真实的小红书笔记标题"
    assert transcript.startswith("# 真实的小红书笔记标题\n")


def test_raw_source_title_uses_share_text_when_candidate_title_is_url_fragment(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    share_text = "【小红书真实标题 | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/69b3e95b0000000023021b9c?source=webshare&xhsshare=pc_web"
    candidate = create_candidate(
        db,
        title="69b3e95b0000000023021b9c?source=webshare&xhsshare=pc_web&xsec_token=abc",
        metadata={"xiaohongshu_diandian_share_text": share_text, "transcript": "正文内容"},
    )

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)
    transcript = open(raw_source.transcript_path, encoding="utf-8").read()

    assert raw_source.title == "小红书真实标题"
    assert transcript.startswith("# 小红书真实标题\n")


def test_raw_source_title_keeps_valid_video_title(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    candidate = create_candidate(
        db,
        platform="bilibili",
        title="一个关于 Agent 工作流的 B站视频",
        metadata={"transcript": "视频正文"},
    )

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)

    assert raw_source.title == "一个关于 Agent 工作流的 B站视频"
    assert db.query(RawSource).count() == 1


def test_is_bad_display_title_rejects_bare_douyin_numeric_id():
    bad = RawSourceService._is_bad_display_title
    # 抖音视频ID：18-19 位纯数字，裸 ID 与带 query 都应判坏。
    assert bad("7655353419527507263") is True
    assert bad("7402216928846023976?source=Baiduspider") is True
    assert bad("7402216928846023976?source=Baiduspider&x=1") is True
    # 正常标题与短数字串不应误判。
    assert bad("一个关于 Agent 工作流的 B站视频") is False
    assert bad("BV1xx411c7mD") is False
    assert bad("2026 年终总结") is False
    assert bad("123") is False


def test_raw_source_title_falls_back_to_url_when_candidate_title_is_bare_douyin_id(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    db = make_session()
    # 极端兜底：扫描偶发没拿到标题，candidate.title 退化成裸视频ID。
    # _display_title 应跳过坏标题、回退到 canonical_url，绝不把裸ID当标题展示。
    candidate = CandidateItem(
        source_type="active_connector",
        platform="douyin",
        external_item_id="7402216928846023976",
        canonical_url="https://www.douyin.com/video/7402216928846023976",
        raw_url="https://www.douyin.com/video/7402216928846023976?source=Baiduspider",
        title="7402216928846023976?source=Baiduspider",
        author="作者",
        content_type="video",
        metadata_json=json.dumps({"transcript": "视频正文"}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add(candidate)
    db.flush()
    db.add(
        SyncLedgerItem(
            platform="douyin",
            external_item_id=candidate.external_item_id,
            canonical_url=candidate.canonical_url,
            raw_url=candidate.raw_url,
            scan_run_id="selected",
            classification_label="knowledge_selected",
            candidate_id=candidate.id,
        )
    )
    db.commit()
    db.refresh(candidate)

    raw_source = RawSourceService(db).ingest_candidate(candidate.id)

    assert raw_source.title == "https://www.douyin.com/video/7402216928846023976"
    assert "7402216928846023976?source=Baiduspider" not in raw_source.title
