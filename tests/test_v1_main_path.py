import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connectors.base import ConnectorItem
from app.database import Base, get_db
from app.main import app
from app.models import CandidateItem, KnowledgeClassification, ProductEvent, RawSource, SyncLedgerItem, WikiLog, WikiPage
from app.services import RawSourceService
from app.services.douyin_transcript_service import DouyinTranscriptError


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


class FakeWikiProvider:
    async def chat(self, messages, model, temperature=0.2):
        return "## 核心观点\n\n- 这是一条可审核的知识页草稿。\n"


def test_html_link_to_review_to_confirm_main_path(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/passive/link",
          data={"url": "https://example.com/article?utm_source=test", "title": "Main path article"},
          headers={"accept": "text/html"},
          follow_redirects=False,
      )

      assert response.status_code == 303
      assert "/ui/task/candidate/" in response.headers["location"]
      candidate = db.query(CandidateItem).one()
      ledger = db.query(SyncLedgerItem).one()
      assert ledger.candidate_id == candidate.id
      assert candidate.status == "pending_classification"

      confirm = client.post(
          f"/candidates/{candidate.id}/confirm",
          headers={"accept": "text/html"},
          follow_redirects=False,
      )
      assert confirm.status_code == 303
      raw_source = db.query(RawSource).one()
      assert f"/ui/task/candidate/{candidate.id}" in confirm.headers["location"]
      assert db.get(CandidateItem, candidate.id).status == "ingested"
      audit = db.query(KnowledgeClassification).filter(KnowledgeClassification.candidate_id == candidate.id).one()
      assert audit.label == "skipped"

      create_page = client.post(
          f"/agent/raw-sources/{raw_source.id}/create-page",
          data={"page_type": "knowledge", "force": "1"},
          headers={"accept": "text/html"},
          follow_redirects=False,
      )
      assert create_page.status_code == 303
      assert "/ui/review/" in create_page.headers["location"]
      page = db.query(WikiPage).one()
      assert page.status == "needs_review"
      assert json.loads(page.source_refs_json)[0]["raw_source_id"] == raw_source.id
      assert db.query(WikiLog).count() == 1

      review = client.get(create_page.headers["location"])
      assert review.status_code == 200
      assert "结果确认" in review.text
      assert "原文内容（可编辑）" in review.text
      assert "Main path article" in review.text
      assert "result-preview-grid compact" in review.text
      assert review.text.index("完整结果（可编辑）") < review.text.index("原文内容（可编辑）")
      markdown = open(page.markdown_path, encoding="utf-8").read()
      assert review.text.index("完整结果（可编辑）") < review.text.rindex("原文内容（可编辑）") < review.text.rindex("这是一条可审核的知识页草稿")

      done = client.post(
          f"/wiki/pages/{page.page_id}/confirm",
          data={"markdown": markdown, "original_text": "用户修订后的原文"},
          headers={"accept": "text/html"},
          follow_redirects=False,
      )
      assert done.status_code == 303
      assert f"/ui/review/{page.page_id}" in done.headers["location"]
      assert db.query(WikiPage).one().status == "active"
      assert "用户修订后的原文" in open(raw_source.transcript_path, encoding="utf-8").read()
      assert db.query(ProductEvent).filter(ProductEvent.event_name == "result_confirmed").count() == 1
    finally:
      app.dependency_overrides.clear()


def test_create_page_endpoint_accepts_skill_page_type(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/manual/idea",
          data={
              "title": "产品运营 Skill",
              "content": "设计一个产品运营 Skill，覆盖用户分层、激活、留存和复盘。",
          },
          headers={"accept": "application/json"},
      )
      assert response.status_code == 200
      candidate = db.query(CandidateItem).one()

      raw_source = RawSourceService(db).ingest_candidate(candidate.id)
      create_page = client.post(
          f"/agent/raw-sources/{raw_source.id}/create-page",
          data={"page_type": "skill", "force": "1"},
          headers={"accept": "application/json"},
      )

      assert create_page.status_code == 200
      page = db.query(WikiPage).one()
      assert page.page_type == "skill"
      assert page.title.startswith("Skill：")
    finally:
      app.dependency_overrides.clear()


def test_passive_douyin_link_import_enriches_transcript_metadata(monkeypatch):
    db = make_session()

    class FakeTranscriptService:
        def enrich_item(self, item, require_transcript=True):
            metadata = {
                **(item.metadata or {}),
                "transcript": "这是链接导入时自动生成的抖音逐字稿。",
                "transcript_status": "provided",
            }
            return ConnectorItem(
                raw_url=item.raw_url,
                title="自动转写的抖音视频",
                author="李厂长来了",
                platform=item.platform,
                content_type=item.content_type,
                metadata=metadata,
            )

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.DouyinTranscriptService", lambda **kwargs: FakeTranscriptService())
    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/passive/link",
          data={"url": "https://www.douyin.com/video/7380000112237", "transcribe": "1"},
          headers={"accept": "application/json"},
      )

      assert response.status_code == 200
      candidate = db.query(CandidateItem).one()
      metadata = json.loads(candidate.metadata_json)
      assert metadata["transcript"] == "这是链接导入时自动生成的抖音逐字稿。"
      assert candidate.title == "自动转写的抖音视频"
      assert candidate.author == "李厂长来了"
    finally:
      app.dependency_overrides.clear()


def test_passive_link_can_process_directly_to_wiki(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    class FakeTranscriptService:
        def enrich_item(self, item, require_transcript=True):
            return ConnectorItem(
                raw_url=item.raw_url,
                title="AI Agent 讲解",
                author="李厂长来了",
                platform=item.platform,
                content_type=item.content_type,
                metadata={
                    **(item.metadata or {}),
                    "transcript": "这是导入链接时生成的逐字稿，讨论 AI Agent。",
                    "transcript_status": "provided",
                },
            )

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.DouyinTranscriptService", lambda **kwargs: FakeTranscriptService())
    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/passive/link",
          data={
              "url": "https://www.douyin.com/video/7380000112240",
              "process_now": "1",
          },
          headers={"accept": "application/json"},
      )

      assert response.status_code == 200
      payload = response.json()
      assert payload["raw_source_id"]
      assert payload["wiki_page_id"]
      assert db.query(RawSource).count() == 1
      assert db.query(WikiPage).count() == 1
    finally:
      app.dependency_overrides.clear()


def test_passive_douyin_jingxuan_link_transcribes_canonical_video_url(monkeypatch):
    db = make_session()
    seen_urls = []

    class FakeTranscriptService:
        def enrich_item(self, item, require_transcript=True):
            seen_urls.append(item.raw_url)
            return ConnectorItem(
                raw_url=item.raw_url,
                title="AI Agent 讲解",
                author="李厂长来了",
                platform=item.platform,
                content_type=item.content_type,
                metadata={
                    **(item.metadata or {}),
                    "transcript": "这是 canonical 视频 URL 得到的逐字稿。",
                    "transcript_status": "provided",
                },
            )

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.DouyinTranscriptService", lambda **kwargs: FakeTranscriptService())
    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/passive/link",
          data={"url": "https://www.douyin.com/jingxuan?modal_id=7648123596673550565"},
          headers={"accept": "application/json"},
      )

      assert response.status_code == 200
      assert seen_urls == ["https://www.douyin.com/video/7648123596673550565"]
    finally:
      app.dependency_overrides.clear()


def test_passive_douyin_short_link_dedupes_after_transcript_resolution(monkeypatch):
    db = make_session()

    class FakeTranscriptService:
        def enrich_item(self, item, require_transcript=True):
            return ConnectorItem(
                raw_url="https://www.douyin.com/video/7648123596673550565",
                title="AI Agent 讲解",
                author="李厂长来了",
                platform=item.platform,
                content_type=item.content_type,
                metadata={
                    **(item.metadata or {}),
                    "transcript": "这是同一个抖音视频的逐字稿。",
                    "transcript_status": "provided",
                    "yt_dlp_id": "7648123596673550565",
                },
            )

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.DouyinTranscriptService", lambda **kwargs: FakeTranscriptService())
    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      first = client.post(
          "/passive/link",
          data={"url": "https://www.douyin.com/jingxuan?modal_id=7648123596673550565"},
          headers={"accept": "application/json"},
      )
      second = client.post(
          "/passive/link",
          data={"url": "https://v.douyin.com/52QJVFO5h5A/"},
          headers={"accept": "application/json"},
      )

      assert first.status_code == 200
      assert second.status_code == 200
      assert second.json()["status"] == "duplicate"
      assert db.query(CandidateItem).count() == 1
      assert db.query(SyncLedgerItem).count() == 1
      candidate = db.query(CandidateItem).one()
      assert candidate.canonical_url == "https://www.douyin.com/video/7648123596673550565"
      assert candidate.external_item_id == "7648123596673550565"
    finally:
      app.dependency_overrides.clear()


def test_douyin_import_items_enriches_and_processes_to_wiki(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    class FakeTranscriptService:
        def enrich_items(self, items, limit=None, require_transcript=True):
            enriched = []
            for item in items:
                enriched.append(
                    ConnectorItem(
                        raw_url=item.raw_url,
                        title=item.title,
                        author="李厂长来了",
                        platform=item.platform,
                        content_type=item.content_type,
                        metadata={
                            **(item.metadata or {}),
                            "transcript": "这是收藏同步时自动生成的抖音逐字稿，包含 AI Agent 教程。",
                            "transcript_status": "provided",
                        },
                    )
                )
            return enriched

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.DouyinTranscriptService", lambda **kwargs: FakeTranscriptService())
    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/douyin/favorites/import-items",
          json={
              "items": [
                  {
                      "href": "https://www.douyin.com/video/7380000112238",
                      "title": "AI Agent 教程",
                  }
              ],
              "process_first_ten": "1",
          },
      )

      assert response.status_code == 200
      assert db.query(RawSource).count() == 1
      assert db.query(WikiPage).count() == 1
      raw_source = db.query(RawSource).one()
      assert "provided" in raw_source.metadata_json
      assert "AI Agent 教程" in open(raw_source.transcript_path, encoding="utf-8").read()
    finally:
      app.dependency_overrides.clear()


def test_douyin_import_items_skips_failed_transcripts_and_processes_rest(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    class FakeTranscriptService:
        def enrich_item(self, item, require_transcript=True):
            if "fail" in item.raw_url:
                raise DouyinTranscriptError("ASR returned an empty transcript")
            return ConnectorItem(
                raw_url=item.raw_url,
                title=item.title,
                author="李厂长来了",
                platform=item.platform,
                content_type=item.content_type,
                metadata={
                    **(item.metadata or {}),
                    "transcript": "这是成功转写的收藏逐字稿。",
                    "transcript_status": "provided",
                },
            )

    def override_get_db():
        yield db

    monkeypatch.setattr("app.api.routes.DouyinTranscriptService", lambda **kwargs: FakeTranscriptService())
    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/douyin/favorites/import-items",
          data={
              "items": json.dumps(
                  [
                      {"href": "https://www.douyin.com/video/fail", "title": "无语音视频"},
                      {"href": "https://www.douyin.com/video/7380000112242", "title": "成功视频"},
                  ]
              ),
              "process_first_ten": "1",
          },
          headers={"accept": "application/json"},
      )

      assert response.status_code == 200
      payload = response.json()
      assert payload["new_count"] == 1
      assert payload["processed_count"] == 1
      assert payload["transcript_failure_count"] == 1
      assert db.query(RawSource).count() == 1
      assert db.query(WikiPage).count() == 1
    finally:
      app.dependency_overrides.clear()


def test_douyin_creator_profile_imports_visible_videos_to_wiki(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.api.routes.DISTILL_REQUESTS_PATH", tmp_path / "distill_requests.json")
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    class FakeCollector:
        async def open(self, url):
            self.url = url

        async def extract_visible_video_links(self, limit=10, require_collection_page=True):
            return [
                ConnectorItem(
                    raw_url="https://www.douyin.com/video/7380000112239",
                    title="李厂长 AI Agent 教程",
                    author="李厂长来了",
                    platform="douyin",
                    content_type="video",
                    metadata={"source": "creator_profile"},
                )
            ]

    class FakeTranscriptService:
        def enrich_items(self, items, limit=None, require_transcript=True):
            return [
                ConnectorItem(
                    raw_url=item.raw_url,
                    title=item.title,
                    author=item.author,
                    platform=item.platform,
                    content_type=item.content_type,
                    metadata={
                        **(item.metadata or {}),
                        "transcript": "这是博主主页同步得到的逐字稿，讨论 AI Agent 产品。",
                        "transcript_status": "provided",
                    },
                )
                for item in items
            ]

    def override_get_db():
        yield db

    fake_collector = FakeCollector()
    monkeypatch.setattr("app.api.routes.douyin_browser_collector", fake_collector)
    monkeypatch.setattr("app.api.routes.DouyinTranscriptService", lambda **kwargs: FakeTranscriptService())
    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/distill/profile",
          data={
              "platform": "抖音",
              "target_name": "李厂长来了",
              "profile_url": "https://www.douyin.com/user/MS4wLjABAAAAg1p3rsQiU_xONYgbpGHtfXr8xPYZyV_TfbDYZvzQm1U?from_tab_name=main&vid=7648123596673550565",
              "limit": "1",
          },
          headers={"accept": "application/json"},
      )

      assert response.status_code == 200
      assert fake_collector.url == "https://www.douyin.com/user/MS4wLjABAAAAg1p3rsQiU_xONYgbpGHtfXr8xPYZyV_TfbDYZvzQm1U"
      assert db.query(RawSource).count() == 1
      assert db.query(WikiPage).count() == 1
      assert "provided" in db.query(RawSource).one().metadata_json
    finally:
      app.dependency_overrides.clear()


def test_manual_idea_can_process_directly_as_skill_page(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/manual/idea",
          data={
              "title": "产品运营 Skill",
              "content": "我想做一个产品运营 Skill，用来诊断拉新、激活、留存和复盘。",
              "page_type": "skill",
              "process_now": "1",
          },
          headers={"accept": "application/json"},
      )

      assert response.status_code == 200
      payload = response.json()
      assert payload["raw_source_id"]
      assert payload["wiki_page_id"]
      page = db.query(WikiPage).one()
      assert page.page_type == "skill"
      assert page.title.startswith("Skill：")
    finally:
      app.dependency_overrides.clear()


def test_manual_idea_from_sync_mode_defaults_to_temporary_idea(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.services.wiki_service.get_provider_runtime", lambda: (FakeWikiProvider(), "fake-model", {}))
    db = make_session()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
      client = TestClient(app)
      response = client.post(
          "/manual/idea",
          data={
              "content": "这是一条从同步页写下的想法。",
              "process_now": "1",
          },
          headers={"accept": "application/json"},
      )

      assert response.status_code == 200
      candidate = db.query(CandidateItem).one()
      assert candidate.source_type == "manual_idea"
      assert candidate.title == "未命名想法"
      raw_source = db.query(RawSource).one()
      assert raw_source.source_type == "manual_idea"
      assert raw_source.platform == "手动录入"
    finally:
      app.dependency_overrides.clear()
