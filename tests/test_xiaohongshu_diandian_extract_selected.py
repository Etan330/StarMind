import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CandidateItem, RawSource, SyncLedgerItem


class FakeDiandianResult:
    def __init__(self, url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8", success=True, error=None, attempts=1, retried=False):
        self.url = url
        self.transcript = "这是点点返回的小红书笔记正文，应该写入 RawSource。" if success else ""
        self.text_content = self.transcript
        self.title = "Anthropic博客的Agent Eval实践心得" if success else ""
        self.success = success
        self.error = error
        self.elapsed_seconds = 10.5
        self.prompt = "请打开并解析下面这条小红书笔记分享内容"
        self.attempts = attempts
        self.retried = retried


class FakeDiandianExtractor:
    calls = []
    results = []
    close_calls = []

    async def check_ready(self):
        return True

    async def extract_content(self, share_text, url="", content_type="note", timeout_seconds=240):
        self.calls.append({"share_text": share_text, "url": url, "content_type": content_type, "timeout_seconds": timeout_seconds})
        if self.results:
            return self.results.pop(0)
        return FakeDiandianResult(url=url)

    async def close(self, close_tab=True):
        self.close_calls.append(close_tab)
        return None


class NotReadyDiandianExtractor:
    async def check_ready(self):
        return False

    async def close(self, close_tab=True):
        return None


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def create_candidate(db, metadata=None, raw_url=None, title="Anthropic博客的Agent Eval实践心得", external_item_id="6a338bc10000000021014bc8"):
    raw_url = raw_url or f"https://www.xiaohongshu.com/user/profile/5fb234c4000000000101db33/{external_item_id}?xsec_token=AB2T5L_LjI-8h03NF7itAak6_gIB-MT1CkA7CyPjF_Jo0=&xsec_source=pc_collect"
    candidate = CandidateItem(
        source_type="active_connector",
        platform="xiaohongshu",
        external_item_id=external_item_id,
        canonical_url=f"https://www.xiaohongshu.com/discovery/item/{external_item_id}",
        raw_url=raw_url,
        title=title,
        author="孙沐晏",
        content_type="note",
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add(candidate)
    db.flush()
    db.add(
        SyncLedgerItem(
            platform="xiaohongshu",
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


def test_xiaohongshu_diandian_extract_selected_creates_raw_source(tmp_path, monkeypatch):
    share_url = "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 " + share_url
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    db = make_session()
    candidate = create_candidate(db, {"xiaohongshu_share_url": share_url, "xiaohongshu_share_text": share_text})

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/xiaohongshu/diandian/extract-selected", json={"candidate_ids": [candidate.id], "per_item_timeout_seconds": 240})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["success_count"] == 1
        assert body["failed_count"] == 0
        raw_source = db.query(RawSource).one()
        transcript = open(raw_source.transcript_path, encoding="utf-8").read()
        metadata = json.loads(db.get(CandidateItem, candidate.id).metadata_json)
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert "点点返回的小红书笔记正文" in transcript
        assert metadata["xiaohongshu_diandian_extracted"] is True
        assert metadata["xiaohongshu_diandian_share_text"] == share_text
        assert metadata["xiaohongshu_diandian_share_url"] == share_url
        assert ledger.classification_label == "knowledge"
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_diandian_uses_share_text_not_profile_raw_url(tmp_path, monkeypatch):
    share_url = "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 " + share_url
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    db = make_session()
    candidate = create_candidate(db, {"xiaohongshu_share_url": share_url, "xiaohongshu_share_text": share_text})

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/xiaohongshu/diandian/extract-selected", json={"candidate_ids": [candidate.id]})
        assert response.status_code == 200
        assert FakeDiandianExtractor.calls[0]["share_text"] == share_text
        assert "user/profile" not in FakeDiandianExtractor.calls[0]["share_text"]
        assert FakeDiandianExtractor.calls[0]["url"] == share_url
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_diandian_fallback_builds_discovery_share_text(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    db = make_session()
    candidate = create_candidate(db, {})

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/xiaohongshu/diandian/extract-selected", json={"candidate_ids": [candidate.id]})
        assert response.status_code == 200
        call = FakeDiandianExtractor.calls[0]
        assert "Anthropic博客的Agent Eval实践心得" in call["share_text"]
        assert "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8" in call["share_text"]
        assert "source=webshare" in call["share_text"]
        assert "xhsshare=pc_web" in call["share_text"]
        assert "xsec_source=pc_share" in call["share_text"]
        assert "user/profile" not in call["share_text"]
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_diandian_extract_selected_processes_four_items_with_retry_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    FakeDiandianExtractor.close_calls = []
    FakeDiandianExtractor.results = [
        FakeDiandianResult(url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc1", attempts=2, retried=True),
        FakeDiandianResult(url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc2"),
        FakeDiandianResult(url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc3"),
        FakeDiandianResult(url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc4"),
    ]
    db = make_session()
    candidates = [
        create_candidate(db, title=f"测试标题 {idx}", external_item_id=f"6a338bc10000000021014bc{idx}")
        for idx in range(1, 5)
    ]

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/xiaohongshu/diandian/extract-selected", json={"candidate_ids": [candidate.id for candidate in candidates]})
        body = response.json()

        assert response.status_code == 200
        assert body["success_count"] == 4
        assert body["failed_count"] == 0
        assert len(FakeDiandianExtractor.calls) == 4
        assert len(db.query(RawSource).all()) == 4
        assert body["items"][0]["attempts"] == 2
        assert body["items"][0]["retried"] is True
        assert body["items"][3]["candidate_id"] == candidates[3].id
        metadata = json.loads(db.get(CandidateItem, candidates[0].id).metadata_json)
        assert metadata["xiaohongshu_diandian_attempts"] == 2
        assert metadata["xiaohongshu_diandian_retried"] is True
        assert FakeDiandianExtractor.close_calls == [True]
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_diandian_extract_selected_failure_does_not_block_later_items(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    FakeDiandianExtractor.close_calls = []
    FakeDiandianExtractor.results = [
        FakeDiandianResult(success=False, error="xiaohongshu_diandian_unhelpful_response", attempts=2, retried=True),
        FakeDiandianResult(url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bd2"),
    ]
    db = make_session()
    first = create_candidate(db, title="失败标题", external_item_id="6a338bc10000000021014bd1")
    second = create_candidate(db, title="后续标题", external_item_id="6a338bc10000000021014bd2")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/xiaohongshu/diandian/extract-selected", json={"candidate_ids": [first.id, second.id]})
        body = response.json()

        assert response.status_code == 200
        assert body["success_count"] == 1
        assert body["failed_count"] == 1
        assert len(FakeDiandianExtractor.calls) == 2
        assert db.query(RawSource).count() == 1
        assert body["items"][0]["attempts"] == 2
        assert body["items"][0]["retried"] is True
        assert body["items"][0]["error"] == "xiaohongshu_diandian_unhelpful_response"
        assert body["items"][1]["success"] is True
        metadata = json.loads(db.get(CandidateItem, first.id).metadata_json)
        assert metadata["xiaohongshu_diandian_error"] == "xiaohongshu_diandian_unhelpful_response"
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_diandian_not_ready_returns_error(monkeypatch):
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", NotReadyDiandianExtractor)
    db = make_session()
    candidate = create_candidate(db)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/xiaohongshu/diandian/extract-selected", json={"candidate_ids": [candidate.id]})
        assert response.status_code == 428
        assert response.json()["detail"]["code"] == "xiaohongshu_diandian_not_ready"
    finally:
        app.dependency_overrides.clear()
