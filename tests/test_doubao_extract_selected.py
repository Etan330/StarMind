import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.connectors.doubao_extractor import PROMPTS, DoubaoExtractor, normalize_content_type
from app.models import CandidateItem, RawSource, ScanEntry, SyncLedgerItem


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


class FakeExtractResult:
    def __init__(self, success=True, error=None):
        self.url = "https://www.douyin.com/video/7380000112233"
        self.transcript = "这是豆包返回的完整逐字稿，应该写入 RawSource。" if success else ""
        self.text_content = self.transcript
        # 真实 extractor 成功路径返回空 title（标题由扫描收藏页阶段提供），Fake 对齐该行为，
        # 这样测试能覆盖「result.title 为空 → 保留扫描时的 candidate.title」的回退逻辑。
        self.title = ""
        self.success = success
        self.error = error
        self.elapsed_seconds = 12.5
        self.prompt = "请帮我提取这个视频链接的完整逐字稿"


class FakeDoubaoExtractor:
    async def check_login(self):
        return True

    async def extract_content(self, url, content_type="auto", timeout_seconds=240):
        return FakeExtractResult()

    async def close(self, close_tab=True):
        return None


class SequencedDoubaoExtractor:
    results = []
    calls = []

    async def check_login(self):
        return True

    async def extract_content(self, url, content_type="auto", timeout_seconds=240):
        self.calls.append(url)
        return self.results.pop(0)

    async def close(self, close_tab=True):
        return None


class LoginRequiredDoubaoExtractor:
    closed_with = []

    async def check_login(self):
        return False

    async def close(self, close_tab=True):
        self.closed_with.append(close_tab)


class LoginRequiredDuringExtractDoubaoExtractor:
    closed_with = []

    async def check_login(self):
        return True

    async def extract_content(self, url, content_type="auto", timeout_seconds=240):
        return FakeExtractResult(success=False, error="doubao_login_required")

    async def close(self, close_tab=True):
        self.closed_with.append(close_tab)


class CloseTrackingDoubaoExtractor:
    closed_with = []

    async def check_login(self):
        return True

    async def extract_content(self, url, content_type="auto", timeout_seconds=240):
        return FakeExtractResult()

    async def close(self, close_tab=True):
        self.closed_with.append(close_tab)


def test_doubao_prompts_use_one_universal_template():
    assert normalize_content_type("video") == "video"
    assert normalize_content_type("note") == "note"
    assert normalize_content_type("image") == "note"
    assert normalize_content_type("article") == "article"
    assert normalize_content_type("") == "auto"
    assert PROMPTS["video"] == PROMPTS["note"] == PROMPTS["article"] == PROMPTS["auto"]
    universal_prompt = PROMPTS["auto"]
    assert "完整逐字稿" in universal_prompt
    assert "图片中的文字" in universal_prompt and "正文" in universal_prompt
    assert "纯文字" in universal_prompt or "文章" in universal_prompt
    assert "不要只" in universal_prompt
    assert "链接：{url}" in universal_prompt


def test_extract_selected_uses_doubao_result_to_create_raw_source(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", FakeDoubaoExtractor)
    db = make_session()
    candidate = CandidateItem(
        source_type="active_connector",
        platform="douyin",
        external_item_id="7380000112233",
        canonical_url="https://www.douyin.com/video/7380000112233",
        raw_url="https://www.douyin.com/video/7380000112233",
        title="AI Agent 教程",
        author="老师",
        content_type="video",
        metadata_json=json.dumps(
            {
                "filter_usefulness": "useful",
                "filter_subcategory": "AI/大模型",
                "filter_reason": "可复用教程",
                "filter_confidence": 0.91,
            },
            ensure_ascii=False,
        ),
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
            scan_run_id="selected",
            classification_label="knowledge_selected",
            candidate_id=candidate.id,
        )
    )
    # 预置一条已 link 到该 candidate 的 ScanEntry，提取成功后应被置 extracted=True + raw_source_id。
    db.add(
        ScanEntry(
            platform="douyin",
            external_item_id="7380000112233",
            canonical_url=candidate.canonical_url,
            raw_url=candidate.raw_url,
            title="AI Agent 教程",
            collection_kind="history",
            candidate_id=candidate.id,
        )
    )
    db.commit()
    db.refresh(candidate)

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/doubao/extract-selected",
            json={"candidate_ids": [candidate.id], "per_item_timeout_seconds": 240},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["success_count"] == 1
        assert body["failed_count"] == 0
        assert body["items"][0]["raw_source_id"]

        raw_source = db.query(RawSource).one()
        transcript = open(raw_source.transcript_path, encoding="utf-8").read()
        metadata = json.loads(db.get(CandidateItem, candidate.id).metadata_json)
        ledger = db.query(SyncLedgerItem).filter(SyncLedgerItem.candidate_id == candidate.id).one()

        assert "这是豆包返回的完整逐字稿" in transcript
        # 豆包成功提取后不得用视频ID覆盖扫描阶段拿到的真实标题。
        assert db.get(CandidateItem, candidate.id).title == "AI Agent 教程"
        assert raw_source.title == "AI Agent 教程"
        assert transcript.startswith("# AI Agent 教程\n")
        assert metadata["doubao_extracted"] is True
        assert metadata["doubao_error"] is None
        assert metadata["doubao_response_length"] == len("这是豆包返回的完整逐字稿，应该写入 RawSource。")
        assert metadata["filter_subcategory"] == "AI/大模型"
        assert ledger.classification_label == "knowledge"

        # ScanEntry 被回填：extracted=True + raw_source_id 指向新建的 RawSource。
        scan_entry = db.query(ScanEntry).filter(ScanEntry.external_item_id == "7380000112233").one()
        assert scan_entry.extracted is True
        assert scan_entry.raw_source_id == raw_source.id
    finally:
        app.dependency_overrides.clear()


def test_prepare_selected_reuses_existing_unextracted_candidate(monkeypatch):
    db = make_session()
    candidate = CandidateItem(
        source_type="active_connector",
        platform="xiaohongshu",
        external_item_id="abc",
        canonical_url="https://www.xiaohongshu.com/explore/abc",
        raw_url="https://www.xiaohongshu.com/explore/abc",
        title="图文笔记",
        content_type="note",
        metadata_json=json.dumps({"filter_usefulness": "useful"}, ensure_ascii=False),
        status="pending_classification",
    )
    db.add(candidate)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/sync/prepare-selected",
            json={
                "platform": "xiaohongshu",
                "selected_items": [
                    {
                        "url": "https://www.xiaohongshu.com/explore/abc",
                        "title": "图文笔记",
                        "content_type": "note",
                    }
                ],
                "skipped_items": [],
            },
        )

        assert response.status_code == 200
        assert response.json()["candidate_ids"] == [candidate.id]
    finally:
        app.dependency_overrides.clear()


def test_doubao_ensure_tab_reuses_existing_open_tab():
    class FakeProxy:
        async def connect(self):
            return True

        async def list_targets(self):
            return [{"id": "tab-existing", "url": "https://www.doubao.com/chat/abc", "title": "豆包"}]

        async def new_tab(self, url):
            raise AssertionError("should reuse existing doubao tab")

    extractor = DoubaoExtractor(proxy=FakeProxy())

    tab = asyncio.run(extractor._ensure_tab())

    assert tab.tab_id == "tab-existing"
    assert tab.url == "https://www.doubao.com/chat/abc"


def test_doubao_message_state_script_uses_data_message_id_and_distinguishes_roles():
    class FakeProxy:
        script = ""

        async def eval_script(self, tab, script):
            self.script = script
            return json.dumps({"count": 0, "assistant_count": 0, "text": "", "page_text": "", "generating": False})

    proxy = FakeProxy()
    extractor = DoubaoExtractor(proxy=proxy)

    state = asyncio.run(extractor._message_state(SimpleNamespace(tab_id="tab", url="https://www.doubao.com/chat/")))

    assert state["count"] == 0
    assert state["assistant_count"] == 0
    # 真实豆包消息节点是 [data-message-id]；用户用 send-msg-bubble-bg 气泡，助手用 markdown 容器。
    assert "data-message-id" in proxy.script
    assert "send-msg-bubble-bg" in proxy.script
    assert "assistant_count" in proxy.script
    assert "const pageText = document.body?.innerText || '';" in proxy.script
    assert "page_text: pageText.slice(-4000)" in proxy.script
    # 旧的脆弱 selector 不应再出现
    assert "page_text: text.slice(-4000)" not in proxy.script
    assert '[class*="answer"]' not in proxy.script


def test_doubao_check_login_does_not_treat_unknown_as_ready():
    class FakeProxy:
        async def connect(self):
            return True

        async def list_targets(self):
            return []

        async def new_tab(self, url):
            return SimpleNamespace(tab_id="tab-new", url=url)

        async def wait_for_load(self, tab):
            return None

        async def eval_script(self, tab, script):
            return json.dumps({"login_required": False, "has_input": False, "unknown": True})

    extractor = DoubaoExtractor(proxy=FakeProxy())

    assert asyncio.run(extractor.check_login()) is False


def test_extract_content_uses_universal_prompt_for_all_content_types():
    class FakeProxy:
        async def connect(self):
            return True

        async def list_targets(self):
            return [{"id": "tab-existing", "url": "https://www.doubao.com/chat/abc", "title": "豆包"}]

        async def eval_script(self, tab, script):
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({"success": False, "error": "stop_after_prompt"})
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

    prompts = []
    original_send_prompt = DoubaoExtractor._send_prompt

    async def capture_prompt(self, tab, prompt):
        prompts.append(prompt)
        return {"success": False, "error": "stop_after_prompt"}

    DoubaoExtractor._send_prompt = capture_prompt
    try:
        extractor = DoubaoExtractor(proxy=FakeProxy())
        for content_type in ["video", "note", "article", "auto"]:
            asyncio.run(extractor.extract_content("https://example.com/item", content_type=content_type, timeout_seconds=30))
    finally:
        DoubaoExtractor._send_prompt = original_send_prompt

    assert len(prompts) == 4
    normalized = [prompt.replace("https://example.com/item", "{url}") for prompt in prompts]
    assert normalized[0] == normalized[1] == normalized[2] == normalized[3]
    assert all("链接：https://example.com/item" in prompt for prompt in prompts)


def test_doubao_send_prompt_reports_input_not_ready():
    class FakeProxy:
        async def eval_script(self, tab, script):
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({"success": False, "error": "chat_input_not_ready"})
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

    extractor = DoubaoExtractor(proxy=FakeProxy())

    result = asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    assert result["success"] is False
    assert result["error"] == "chat_input_not_ready"


def test_doubao_send_prompt_script_uses_expanded_input_and_send_confirmation_logic():
    captured_scripts = []

    class FakeProxy:
        async def eval_script(self, tab, script):
            captured_scripts.append(script)
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({"success": False, "error": "prompt_input_not_applied"})
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

    extractor = DoubaoExtractor(proxy=FakeProxy())

    result = asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    send_script = next(script for script in captured_scripts if "const prompt =" in script)
    assert "ClipboardEvent" in send_script
    assert "DataTransfer" in send_script
    assert "paste" in send_script
    assert "[class*=\"input\"] [contenteditable=\"true\"]" in send_script
    assert "[class*=\"composer\"] [contenteditable=\"true\"]" in send_script
    assert "candidateSelector" in send_script
    assert "svg use" in send_script
    assert "input_text" in send_script
    assert "before_count" in send_script
    assert "before?.count" not in send_script
    assert "doubao_send_not_confirmed" in send_script
    assert "promptHead" in send_script
    assert result["success"] is False
    assert result["error"] == "prompt_input_not_applied"


def test_doubao_send_prompt_uses_click_at_when_coordinates_returned():
    class FakeProxy:
        def __init__(self):
            self.clicked_at = None
            self.message_calls = 0

        async def eval_script(self, tab, script):
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({"success": True, "url": "https://example.com", "click_x": 1214, "click_y": 664})
            self.message_calls += 1
            if self.message_calls >= 2:
                return json.dumps({"count": 1, "text": "测试 prompt https://example.com", "page_text": "测试 prompt https://example.com", "generating": False})
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

        async def click_at(self, tab, x, y):
            self.clicked_at = (x, y)

    proxy = FakeProxy()
    extractor = DoubaoExtractor(proxy=proxy)

    result = asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    assert proxy.clicked_at == (1214, 664)
    assert result["success"] is True


def test_doubao_send_script_prefers_rightmost_highlighted_send_button_over_more_button():
    captured_scripts = []

    class FakeProxy:
        async def eval_script(self, tab, script):
            captured_scripts.append(script)
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({"success": False, "error": "stop_after_script_capture"})
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

    extractor = DoubaoExtractor(proxy=FakeProxy())

    asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    send_script = next(script for script in captured_scripts if "const prompt =" in script)
    assert "buttonScore" in send_script
    assert "bg-g-send-msg-btn-bg" in send_script
    assert "rightmostNearButtons" in send_script
    assert "更多" in send_script


def test_doubao_send_prompt_reports_click_without_effect():
    class FakeProxy:
        async def eval_script(self, tab, script):
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({
                    "success": True,
                    "url": "https://example.com",
                    "click_x": 1214,
                    "click_y": 664,
                    "input_text": "测试 prompt https://example.com",
                    "before_count": 0,
                })
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

        async def click_at(self, tab, x, y):
            return None

    extractor = DoubaoExtractor(proxy=FakeProxy())

    result = asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    assert result["success"] is False
    assert result["error"] == "doubao_send_not_confirmed"


class FakeProxyForUnconfirmedDoubaoSend:
    def __init__(self):
        self.clicked_at = []
        self.pressed_keys = []
        self.message_calls = 0

    async def eval_script(self, tab, script):
        if "login_required" in script:
            return json.dumps({"login_required": False, "has_input": True})
        if "const prompt =" in script:
            return json.dumps({
                "success": True,
                "url": "https://example.com",
                "click_x": 1214,
                "click_y": 664,
                "input_text": "测试 prompt https://example.com",
                "before_count": 0,
            })
        self.message_calls += 1
        return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

    async def click_at(self, tab, x, y):
        self.clicked_at.append((x, y))

    async def key(self, tab, key, code=None, windows_virtual_key_code=None, modifiers=0):
        self.pressed_keys.append((key, code, windows_virtual_key_code, modifiers))


def test_doubao_send_prompt_returns_diagnostics_when_real_send_is_unconfirmed():
    proxy = FakeProxyForUnconfirmedDoubaoSend()
    extractor = DoubaoExtractor(proxy=proxy)

    result = asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    assert result["success"] is False
    assert result["error"] == "doubao_send_not_confirmed"
    assert result["input_text"] == "测试 prompt https://example.com"
    assert result["click_x"] == 1214
    assert result["click_y"] == 664
    assert result["before_count"] == 0
    assert result["after_count"] == 0
    assert result["confirmed_by"] is None
    assert proxy.clicked_at == [(1214, 664)]
    assert any(key[0] == "Enter" for key in proxy.pressed_keys)


def test_doubao_send_prompt_confirms_success_when_input_clears_after_key_send():
    class FakeProxy(FakeProxyForUnconfirmedDoubaoSend):
        async def eval_script(self, tab, script):
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({
                    "success": True,
                    "url": "https://example.com",
                    "click_x": 1214,
                    "click_y": 664,
                    "input_text": "测试 prompt https://example.com",
                    "before_count": 0,
                })
            self.message_calls += 1
            if self.pressed_keys:
                return json.dumps({"count": 1, "text": "测试 prompt https://example.com", "page_text": "测试 prompt https://example.com", "input_text": "", "generating": False})
            return json.dumps({"count": 0, "text": "", "page_text": "", "input_text": "测试 prompt https://example.com", "generating": False})

    proxy = FakeProxy()
    extractor = DoubaoExtractor(proxy=proxy)

    result = asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    assert result["success"] is True
    assert result["confirmed_by"] in {"input_cleared", "message_count", "page_text"}
    assert any(key[0] == "Enter" for key in proxy.pressed_keys)


def test_doubao_send_prompt_reports_success_after_verified_send():
    class FakeProxy:
        def __init__(self):
            self.message_calls = 0

        async def eval_script(self, tab, script):
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({"success": True, "url": "https://example.com"})
            # 第一次（before）消息数为 1；发送后助手新增一条 -> assistant_count 增加，
            # 这才是真正发出去的可信信号（page_text 含 url 已不再算成功）。
            self.message_calls += 1
            if self.message_calls <= 1:
                return json.dumps({"count": 1, "assistant_count": 0, "text": "", "page_text": "", "input_text": "测试 prompt https://example.com", "generating": False})
            return json.dumps({"count": 3, "assistant_count": 1, "text": "回复内容", "page_text": "回复内容", "input_text": "", "generating": False})

    extractor = DoubaoExtractor(proxy=FakeProxy())

    result = asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    assert result["success"] is True
    assert result["confirmed_by"] in {"assistant_count", "message_count"}


def test_wait_for_response_requires_five_stable_rounds_before_returning():
    class FakeProxy:
        def __init__(self):
            self.calls = 0

        async def eval_script(self, tab, script):
            self.calls += 1
            return json.dumps({"count": 2, "assistant_count": 1, "text": "这是豆包已经输出完成的一段足够长的内容，可以安全抓取入库", "generating": False})

    async def no_sleep(_seconds):
        return None

    import app.connectors.doubao_extractor as module
    original_sleep = module.asyncio.sleep
    module.asyncio.sleep = no_sleep
    try:
        proxy = FakeProxy()
        extractor = DoubaoExtractor(proxy=proxy)
        text = asyncio.run(extractor._wait_for_response_complete(SimpleNamespace(tab_id="tab"), 0, 30))

        assert text == "这是豆包已经输出完成的一段足够长的内容，可以安全抓取入库"
        assert proxy.calls >= 6
    finally:
        module.asyncio.sleep = original_sleep


def test_wait_for_response_ignores_reply_until_assistant_count_increases():
    """发送后只有用户消息时 assistant_count 不增加，必须继续等待，不能误返回旧助手文本。"""

    class FakeProxy:
        def __init__(self):
            self.calls = 0

        async def eval_script(self, tab, script):
            self.calls += 1
            # 前两轮还没有新助手回复（assistant_count 仍为 previous=1）
            if self.calls <= 2:
                return json.dumps({"count": 3, "assistant_count": 1, "text": "旧的助手回复", "generating": True})
            # 之后新助手回复出现并稳定
            return json.dumps({"count": 4, "assistant_count": 2, "text": "豆包对本次链接给出的全新完整回复内容", "generating": False})

    async def no_sleep(_seconds):
        return None

    import app.connectors.doubao_extractor as module
    original_sleep = module.asyncio.sleep
    module.asyncio.sleep = no_sleep
    try:
        proxy = FakeProxy()
        extractor = DoubaoExtractor(proxy=proxy)
        text = asyncio.run(extractor._wait_for_response_complete(SimpleNamespace(tab_id="tab"), 1, 60))

        assert text == "豆包对本次链接给出的全新完整回复内容"
    finally:
        module.asyncio.sleep = original_sleep


def test_wait_for_response_returns_last_text_when_timeout_has_enough_content():
    class FakeProxy:
        async def eval_script(self, tab, script):
            return json.dumps({"count": 2, "assistant_count": 1, "text": "这是豆包已经输出但还没稳定的一段足够长的内容", "generating": True})

    async def no_sleep(_seconds):
        return None

    import app.connectors.doubao_extractor as module
    original_sleep = module.asyncio.sleep
    original_monotonic = module.monotonic
    ticks = iter([0, 1, 2, 31])
    module.asyncio.sleep = no_sleep
    module.monotonic = lambda: next(ticks, 31)
    try:
        extractor = DoubaoExtractor(proxy=FakeProxy())
        text = asyncio.run(extractor._wait_for_response_complete(SimpleNamespace(tab_id="tab"), 0, 1))

        assert text == "这是豆包已经输出但还没稳定的一段足够长的内容"
    finally:
        module.asyncio.sleep = original_sleep
        module.monotonic = original_monotonic


def test_extract_selected_returns_structured_login_required_when_doubao_not_logged_in(monkeypatch):
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", LoginRequiredDoubaoExtractor)
    db = make_session()
    candidate = CandidateItem(
        source_type="active_connector",
        platform="xiaohongshu",
        external_item_id="note-1",
        canonical_url="https://www.xiaohongshu.com/explore/abc",
        raw_url="https://www.xiaohongshu.com/explore/abc",
        title="图文笔记",
        content_type="note",
        status="pending_classification",
    )
    db.add(candidate)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/doubao/extract-selected", json={"candidate_ids": [candidate.id]})

        assert response.status_code == 428
        assert response.json()["detail"]["code"] == "doubao_login_required"
        assert response.json()["detail"]["login_url"] == "https://www.doubao.com"
        assert "登录豆包" in response.json()["detail"]["message"]
        assert LoginRequiredDoubaoExtractor.closed_with[-1] is False
    finally:
        app.dependency_overrides.clear()


def test_extract_selected_stops_batch_when_login_modal_appears_during_extract(monkeypatch):
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", LoginRequiredDuringExtractDoubaoExtractor)
    db = make_session()
    candidate = CandidateItem(
        source_type="active_connector",
        platform="bilibili",
        external_item_id="BV1abc",
        canonical_url="https://www.bilibili.com/video/BV1abc",
        raw_url="https://www.bilibili.com/video/BV1abc",
        title="视频教程",
        content_type="video",
        status="pending_classification",
    )
    db.add(candidate)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/doubao/extract-selected", json={"candidate_ids": [candidate.id]})

        assert response.status_code == 428
        assert response.json()["detail"]["code"] == "doubao_login_required"
        assert response.json()["detail"]["login_url"] == "https://www.doubao.com"
        assert db.query(RawSource).count() == 0
        assert LoginRequiredDuringExtractDoubaoExtractor.closed_with[-1] is False
    finally:
        app.dependency_overrides.clear()


def create_doubao_candidate(db, external_item_id, url, title="视频教程"):
    candidate = CandidateItem(
        source_type="active_connector",
        platform="bilibili",
        external_item_id=external_item_id,
        canonical_url=url,
        raw_url=url,
        title=title,
        content_type="video",
        status="pending_classification",
    )
    db.add(candidate)
    db.flush()
    db.add(
        SyncLedgerItem(
            platform="bilibili",
            external_item_id=external_item_id,
            canonical_url=url,
            raw_url=url,
            scan_run_id="selected",
            classification_label="knowledge_selected",
            candidate_id=candidate.id,
        )
    )
    db.commit()
    db.refresh(candidate)
    return candidate


def test_extract_selected_records_doubao_failure_metadata_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", SequencedDoubaoExtractor)
    SequencedDoubaoExtractor.calls = []
    SequencedDoubaoExtractor.results = [
        FakeExtractResult(success=False, error="doubao_send_not_confirmed"),
        FakeExtractResult(success=True),
    ]
    db = make_session()
    first = create_doubao_candidate(db, "BVfail", "https://www.bilibili.com/video/BVfail", "失败视频")
    second = create_doubao_candidate(db, "BVok", "https://www.bilibili.com/video/BVok", "成功视频")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/doubao/extract-selected", json={"candidate_ids": [first.id, second.id]})

        assert response.status_code == 200
        body = response.json()
        assert body["success_count"] == 1
        assert body["failed_count"] == 1
        assert len(SequencedDoubaoExtractor.calls) == 2
        first_metadata = json.loads(db.get(CandidateItem, first.id).metadata_json)
        second_metadata = json.loads(db.get(CandidateItem, second.id).metadata_json)
        assert first_metadata["doubao_extracted"] is False
        assert first_metadata["doubao_error"] == "doubao_send_not_confirmed"
        assert first_metadata["doubao_prompt"] == "请帮我提取这个视频链接的完整逐字稿"
        assert second_metadata["doubao_extracted"] is True
        assert second_metadata["doubao_error"] is None
        assert db.query(RawSource).count() == 1
    finally:
        app.dependency_overrides.clear()


def test_extract_selected_closes_doubao_tab_after_success(tmp_path, monkeypatch):
    CloseTrackingDoubaoExtractor.closed_with = []
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", CloseTrackingDoubaoExtractor)
    db = make_session()
    candidate = CandidateItem(
        source_type="active_connector",
        platform="douyin",
        external_item_id="7380000112233",
        canonical_url="https://www.douyin.com/video/7380000112233",
        raw_url="https://www.douyin.com/video/7380000112233",
        title="AI Agent 教程",
        content_type="video",
        status="pending_classification",
    )
    db.add(candidate)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post("/api/doubao/extract-selected", json={"candidate_ids": [candidate.id]})

        assert response.status_code == 200
        assert CloseTrackingDoubaoExtractor.closed_with[-1] is True
    finally:
        app.dependency_overrides.clear()


# --- 发送按钮稳定锚点回归断言（plan A / E1） ---


def test_doubao_send_script_uses_stable_anchor_and_react_clickable():
    """send_script 必须含稳定锚点优先逻辑 + reactClickable + 诊断字段。

    实测豆包发送区是 <div.send-btn-wrapper>(onClick) > <button#flow-end-msg-send>(onClick)
    > <svg.size-18>；旧逻辑会选中内部 svg（无 onClick），点击打空导致 doubao_send_not_confirmed。
    """
    captured_scripts = []

    class FakeProxy:
        async def eval_script(self, tab, script):
            captured_scripts.append(script)
            if "login_required" in script:
                return json.dumps({"login_required": False, "has_input": True})
            if "const prompt =" in script:
                return json.dumps({"success": False, "error": "stop_after_script_capture"})
            return json.dumps({"count": 0, "text": "", "page_text": "", "generating": False})

    extractor = DoubaoExtractor(proxy=FakeProxy())
    asyncio.run(extractor._send_prompt(SimpleNamespace(tab_id="tab"), "测试 prompt https://example.com"))

    send_script = next(script for script in captured_scripts if "const prompt =" in script)
    assert "flow-end-msg-send" in send_script
    assert "send-btn-wrapper" in send_script
    assert "reactClickable" in send_script
    assert "hasReactOnClick" in send_script
    assert "picked_id" in send_script
    assert "picked_has_onclick" in send_script
    # 兜底打分逻辑必须保留（既有测试 + 反爬场景双保险）。
    assert "buttonScore" in send_script


# --- 节流 + 人机验证 + 断点续跑（plan B / C / E1） ---


class PacingTrackingDoubaoExtractor:
    """记录 start_new_conversation 调用次数 + extract 调用，用于断言换窗节流。"""

    calls = []
    new_conversation_calls = 0
    closed_with = []

    async def check_login(self):
        return True

    async def extract_content(self, url, content_type="auto", timeout_seconds=240):
        type(self).calls.append(url)
        return FakeExtractResult()

    async def start_new_conversation(self):
        type(self).new_conversation_calls += 1
        return {"success": True, "method": "new_chat_button"}

    async def close(self, close_tab=True):
        type(self).closed_with.append(close_tab)


class HumanVerificationDoubaoExtractor:
    """前 N 条成功，第 N+1 条返回人机验证 → 端点应 paused（200），不再继续。"""

    succeed_before_challenge = 1
    calls = []
    new_conversation_calls = 0
    closed_with = []

    async def check_login(self):
        return True

    async def extract_content(self, url, content_type="auto", timeout_seconds=240):
        type(self).calls.append(url)
        if len(type(self).calls) <= type(self).succeed_before_challenge:
            return FakeExtractResult(success=True)
        return FakeExtractResult(success=False, error="doubao_human_verification_required")

    async def start_new_conversation(self):
        type(self).new_conversation_calls += 1
        return {"success": True, "method": "new_chat_button"}

    async def close(self, close_tab=True):
        type(self).closed_with.append(close_tab)


def test_extract_selected_switches_conversation_every_n_items(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", PacingTrackingDoubaoExtractor)
    PacingTrackingDoubaoExtractor.calls = []
    PacingTrackingDoubaoExtractor.new_conversation_calls = 0
    PacingTrackingDoubaoExtractor.closed_with = []

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.api.routes.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.api.routes.random.uniform", lambda lo, hi: (lo + hi) / 2)

    db = make_session()
    candidates = [
        create_doubao_candidate(db, f"BV{idx}", f"https://www.bilibili.com/video/BV{idx}", f"视频 {idx}")
        for idx in range(4)
    ]

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/doubao/extract-selected",
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
        # 4 条、每 2 条换窗：i=2 触发一次（i=0 不换，i%2==0 且 i>0）。
        assert PacingTrackingDoubaoExtractor.new_conversation_calls == 1
        # 条间随机延时：4 条之间 3 次（最后一条不等）。
        assert len(slept) == 3
        assert all(value == 2.0 for value in slept)
    finally:
        app.dependency_overrides.clear()


def test_extract_selected_pauses_on_human_verification_and_preserves_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", HumanVerificationDoubaoExtractor)
    HumanVerificationDoubaoExtractor.calls = []
    HumanVerificationDoubaoExtractor.new_conversation_calls = 0
    HumanVerificationDoubaoExtractor.closed_with = []
    HumanVerificationDoubaoExtractor.succeed_before_challenge = 1

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("app.api.routes.asyncio.sleep", fake_sleep)

    db = make_session()
    first = create_doubao_candidate(db, "BVok1", "https://www.bilibili.com/video/BVok1", "成功1")
    second = create_doubao_candidate(db, "BVchallenge", "https://www.bilibili.com/video/BVchallenge", "触发验证")
    third = create_doubao_candidate(db, "BVlater", "https://www.bilibili.com/video/BVlater", "尚未处理")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/doubao/extract-selected",
            json={"candidate_ids": [first.id, second.id, third.id], "switch_every": 99},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "paused"
        assert body["reason"] == "human_verification"
        assert body["job_id"]
        assert body["success_count"] == 1
        assert body["pending_remaining"] == 2

        # 已完成的写了 RawSource、标记 doubao_extracted；未完成的不写、无标记。
        assert db.query(RawSource).count() == 1
        first_meta = json.loads(db.get(CandidateItem, first.id).metadata_json)
        third_meta = json.loads(db.get(CandidateItem, third.id).metadata_json)
        assert first_meta["doubao_extracted"] is True
        assert third_meta.get("doubao_extracted") is not True
        # 暂停时不能关掉豆包标签页（用户要在该页面手动验证）。
        assert HumanVerificationDoubaoExtractor.closed_with[-1] is False
    finally:
        app.dependency_overrides.clear()


def test_extract_selected_resumes_from_breakpoint_skipping_done(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.raw_source_service.LOCAL_DATA_DIR", tmp_path)

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("app.api.routes.asyncio.sleep", fake_sleep)

    db = make_session()
    first = create_doubao_candidate(db, "BVdone", "https://www.bilibili.com/video/BVdone", "已完成")
    second = create_doubao_candidate(db, "BVfail", "https://www.bilibili.com/video/BVfail", "首轮验证")
    third = create_doubao_candidate(db, "BVrest", "https://www.bilibili.com/video/BVrest", "首轮未到")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        # 第一轮：第一条成功后第二条触发人机验证 → paused。
        monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", HumanVerificationDoubaoExtractor)
        HumanVerificationDoubaoExtractor.calls = []
        HumanVerificationDoubaoExtractor.new_conversation_calls = 0
        HumanVerificationDoubaoExtractor.closed_with = []
        HumanVerificationDoubaoExtractor.succeed_before_challenge = 1

        first_response = client.post(
            "/api/doubao/extract-selected",
            json={"candidate_ids": [first.id, second.id, third.id], "switch_every": 99},
        )
        first_body = first_response.json()
        assert first_body["status"] == "paused"
        job_id = first_body["job_id"]
        assert db.query(RawSource).count() == 1

        # 用户完成验证后续跑：换成全成功 extractor，带 job_id 重 POST 原 candidate_ids。
        monkeypatch.setattr("app.connectors.doubao_extractor.DoubaoExtractor", PacingTrackingDoubaoExtractor)
        PacingTrackingDoubaoExtractor.calls = []
        PacingTrackingDoubaoExtractor.new_conversation_calls = 0
        PacingTrackingDoubaoExtractor.closed_with = []

        resume_response = client.post(
            "/api/doubao/extract-selected",
            json={
                "candidate_ids": [first.id, second.id, third.id],
                "job_id": job_id,
                "switch_every": 99,
            },
        )

        assert resume_response.status_code == 200
        resume_body = resume_response.json()
        assert resume_body["status"] == "completed"
        assert resume_body["job_id"] == job_id
        # 续跑只处理剩余 2 条（已完成的 first 被 already_extracted 跳过）。
        assert resume_body["success_count"] == 2
        assert PacingTrackingDoubaoExtractor.calls == [
            "https://www.bilibili.com/video/BVfail",
            "https://www.bilibili.com/video/BVrest",
        ]
        # 全部入库：第一轮 1 条 + 续跑 2 条 = 3 条，first 不重复写。
        assert db.query(RawSource).count() == 3
        for candidate in (first, second, third):
            meta = json.loads(db.get(CandidateItem, candidate.id).metadata_json)
            assert meta["doubao_extracted"] is True
    finally:
        app.dependency_overrides.clear()
