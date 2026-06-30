import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CandidateItem, RawSource, ScanEntry, SyncLedgerItem, WikiPage


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


def test_diandian_extract_selected_creator_candidate_auto_updates_creator_wiki(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    FakeDiandianExtractor.results = []
    FakeDiandianExtractor.close_calls = []

    class Provider:
        provider_name = "mock"

        async def chat(self, messages, model, temperature=0.2):
            return "## 人设\n小红书博主人设分析\n\n## 最新与高赞差异\n高赞更偏案例"

    monkeypatch.setattr(
        "app.services.wiki_service.get_provider_runtime",
        lambda provider_id=None, model=None: (Provider(), "mock-model", {"api_style": "mock"}),
    )
    db = make_session()
    candidate = create_candidate(
        db,
        metadata={"creator_key": "xiaohongshu:creator-1", "creator_name": "孙沐晏", "creator_bucket": "latest"},
        title="小红书博主作品",
    )
    candidate.source_type = "distill_profile"
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/xiaohongshu/diandian/extract-selected", json={"candidate_ids": [candidate.id]})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["items"][0]["wiki_page_id"]
        assert body["creator_wiki_pages"] == [{"creator_key": "xiaohongshu:creator-1", "wiki_page_id": body["items"][0]["wiki_page_id"], "creator_name": "孙沐晏"}]
        assert db.query(RawSource).count() == 1
        page = db.query(WikiPage).one()
        assert page.page_type == "creator"
        assert "小红书博主人设分析" in open(page.markdown_path, encoding="utf-8").read()
    finally:
        app.dependency_overrides.clear()



def test_xiaohongshu_diandian_extract_selected_creates_raw_source(tmp_path, monkeypatch):
    share_url = "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 " + share_url
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    db = make_session()
    candidate = create_candidate(db, {"xiaohongshu_share_url": share_url, "xiaohongshu_share_text": share_text})
    # 预置已 link 的 ScanEntry，提取成功后应被置 extracted=True + raw_source_id。
    db.add(
        ScanEntry(
            platform="xiaohongshu",
            external_item_id=candidate.external_item_id,
            canonical_url=candidate.canonical_url,
            raw_url=candidate.raw_url,
            title=candidate.title,
            collection_kind="history",
            candidate_id=candidate.id,
        )
    )
    db.commit()

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
        assert body["items"][0]["title"] == "Anthropic博客的Agent Eval实践心得"
        assert "点点返回的小红书笔记正文" in body["items"][0]["preview"]["content"]
        raw_source = db.query(RawSource).one()
        transcript = open(raw_source.transcript_path, encoding="utf-8").read()
        metadata = json.loads(db.get(CandidateItem, candidate.id).metadata_json)
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert "点点返回的小红书笔记正文" in transcript
        assert metadata["xiaohongshu_diandian_extracted"] is True
        assert metadata["xiaohongshu_diandian_share_text"] == share_text
        assert metadata["xiaohongshu_diandian_share_url"] == share_url
        assert ledger.classification_label == "knowledge"

        # ScanEntry 被回填：extracted=True + raw_source_id。
        scan_entry = db.query(ScanEntry).filter(ScanEntry.external_item_id == candidate.external_item_id).one()
        assert scan_entry.extracted is True
        assert scan_entry.raw_source_id == raw_source.id
    finally:
        app.dependency_overrides.clear()


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
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


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
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


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
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


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
    finally:
        app.dependency_overrides.clear()


def test_xiaohongshu_diandian_extract_selected_failure_does_not_block_later_items(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", FakeDiandianExtractor)
    FakeDiandianExtractor.calls = []
    FakeDiandianExtractor.close_calls = []
    FakeDiandianExtractor.results = [
        FakeDiandianResult(success=False, error="low_quality_response", attempts=2, retried=True),
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
        assert body["items"][0]["error"] == "low_quality_response"
        assert body["items"][1]["success"] is True
        metadata = json.loads(db.get(CandidateItem, first.id).metadata_json)
        assert metadata["xiaohongshu_diandian_error"] == "low_quality_response"
    finally:
        app.dependency_overrides.clear()


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
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


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
    finally:
        app.dependency_overrides.clear()


# --- 点点对称：换窗节流 + 人机验证暂停续跑（plan C / D / E1） ---


class PacingTrackingDiandianExtractor:
    calls = []
    new_conversation_calls = 0
    close_calls = []

    async def check_ready(self):
        return True

    async def extract_content(self, share_text, url="", content_type="note", timeout_seconds=240):
        type(self).calls.append(url)
        return FakeDiandianResult(url=url)

    async def start_new_conversation(self):
        type(self).new_conversation_calls += 1
        return {"success": True, "method": "location_assign"}

    async def close(self, close_tab=True):
        type(self).close_calls.append(close_tab)


class HumanVerificationDiandianExtractor:
    succeed_before_challenge = 1
    calls = []
    new_conversation_calls = 0
    close_calls = []

    async def check_ready(self):
        return True

    async def extract_content(self, share_text, url="", content_type="note", timeout_seconds=240):
        type(self).calls.append(url)
        if len(type(self).calls) <= type(self).succeed_before_challenge:
            return FakeDiandianResult(url=url, success=True)
        return FakeDiandianResult(url=url, success=False, error="xiaohongshu_diandian_human_verification_required")

    async def start_new_conversation(self):
        type(self).new_conversation_calls += 1
        return {"success": True, "method": "location_assign"}

    async def close(self, close_tab=True):
        type(self).close_calls.append(close_tab)


def test_diandian_extract_selected_switches_conversation_every_n_items(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", PacingTrackingDiandianExtractor)
    PacingTrackingDiandianExtractor.calls = []
    PacingTrackingDiandianExtractor.new_conversation_calls = 0
    PacingTrackingDiandianExtractor.close_calls = []

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.api.routes.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.api.routes.random.uniform", lambda lo, hi: (lo + hi) / 2)

    db = make_session()
    candidates = [
        create_candidate(db, title=f"标题 {idx}", external_item_id=f"6a338bc10000000021014be{idx}")
        for idx in range(4)
    ]

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/xiaohongshu/diandian/extract-selected",
            json={
                "candidate_ids": [c.id for c in candidates],
                "switch_every": 2,
                "item_delay_min": 1,
                "item_delay_max": 3,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["success_count"] == 4
        assert PacingTrackingDiandianExtractor.new_conversation_calls == 1
        assert len(slept) == 3
        assert all(value == 2.0 for value in slept)
    finally:
        app.dependency_overrides.clear()


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
    finally:
        app.dependency_overrides.clear()


def test_diandian_extract_selected_pauses_and_resumes_on_human_verification(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("app.api.routes.asyncio.sleep", fake_sleep)

    db = make_session()
    first = create_candidate(db, title="完成", external_item_id="6a338bc10000000021014bf1")
    second = create_candidate(db, title="验证", external_item_id="6a338bc10000000021014bf2")
    third = create_candidate(db, title="未到", external_item_id="6a338bc10000000021014bf3")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", HumanVerificationDiandianExtractor)
        HumanVerificationDiandianExtractor.calls = []
        HumanVerificationDiandianExtractor.new_conversation_calls = 0
        HumanVerificationDiandianExtractor.close_calls = []
        HumanVerificationDiandianExtractor.succeed_before_challenge = 1

        first_response = client.post(
            "/api/xiaohongshu/diandian/extract-selected",
            json={"candidate_ids": [first.id, second.id, third.id], "switch_every": 99},
        )
        first_body = first_response.json()
        assert first_response.status_code == 200
        assert first_body["status"] == "paused"
        assert first_body["reason"] == "human_verification"
        assert first_body["success_count"] == 1
        assert first_body["pending_remaining"] == 2
        assert db.query(RawSource).count() == 1
        assert HumanVerificationDiandianExtractor.close_calls[-1] is False
        job_id = first_body["job_id"]

        # 续跑：全成功 extractor 接管，带 job_id 重 POST。
        monkeypatch.setattr("app.connectors.xiaohongshu_diandian_extractor.XiaohongshuDiandianExtractor", PacingTrackingDiandianExtractor)
        PacingTrackingDiandianExtractor.calls = []
        PacingTrackingDiandianExtractor.new_conversation_calls = 0
        PacingTrackingDiandianExtractor.close_calls = []

        resume_response = client.post(
            "/api/xiaohongshu/diandian/extract-selected",
            json={"candidate_ids": [first.id, second.id, third.id], "job_id": job_id, "switch_every": 99},
        )
        resume_body = resume_response.json()
        assert resume_response.status_code == 200
        assert resume_body["status"] == "completed"
        assert resume_body["job_id"] == job_id
        assert resume_body["success_count"] == 2
        assert len(PacingTrackingDiandianExtractor.calls) == 2
        assert db.query(RawSource).count() == 3
        for candidate in (first, second, third):
            meta = json.loads(db.get(CandidateItem, candidate.id).metadata_json)
            assert meta["xiaohongshu_diandian_extracted"] is True
    finally:
        app.dependency_overrides.clear()


def test_link_extract_prepares_xiaohongshu_candidate_for_diandian():
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/intake/link-extract",
            json={
                "url": "https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?xsec_token=abc",
                "title": "单条小红书笔记",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "prepared"
        assert body["platform"] == "xiaohongshu"
        assert body["extract_endpoint"] == "/api/xiaohongshu/diandian/extract-selected"
        candidate = db.get(CandidateItem, body["candidate_ids"][0])
        assert candidate.source_type == "passive_link"
        assert candidate.platform == "xiaohongshu"
        metadata = json.loads(candidate.metadata_json)
        assert metadata["source"] == "passive_link"
        assert metadata["xiaohongshu_share_url"].startswith("https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8")
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()
        assert ledger.classification_label == "knowledge_selected"
    finally:
        app.dependency_overrides.clear()
