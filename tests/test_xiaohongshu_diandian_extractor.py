import asyncio
import json
from types import SimpleNamespace

from app.connectors.xiaohongshu_diandian_extractor import DIANDIAN_URL, XiaohongshuDiandianExtractor, is_unhelpful_diandian_response


class FakeProxy:
    def __init__(self, targets=None, states=None):
        self.targets = targets or []
        self.states = list(states or [])
        self.opened_urls = []
        self.closed_tabs = []
        self.clicked = []
        self.scripts = []

    async def connect(self):
        return True

    async def list_targets(self):
        return self.targets

    async def new_tab(self, url):
        self.opened_urls.append(url)
        return SimpleNamespace(tab_id="tab-new", url=url, title="点点")

    async def wait_for_load(self, tab):
        return None

    async def eval_script(self, tab, script):
        self.scripts.append(script)
        if self.states:
            state = self.states.pop(0)
        else:
            state = {"count": 1, "text": "点点返回的小红书正文内容，包含方法步骤和图片文字。", "generating": False}
        return json.dumps(state)

    async def click_at(self, tab, x, y):
        self.clicked.append((x, y))

    async def close_tab(self, tab):
        self.closed_tabs.append(tab.tab_id)


def test_ensure_tab_reuses_existing_diandian_tab():
    proxy = FakeProxy(targets=[{"id": "tab-existing", "url": "https://www.xiaohongshu.com/ai_chat", "title": "点点"}])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)

    tab = asyncio.run(extractor._ensure_tab())

    assert tab.tab_id == "tab-existing"
    assert proxy.opened_urls == []


def test_ensure_tab_opens_fixed_diandian_url_when_missing():
    proxy = FakeProxy(targets=[])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)

    tab = asyncio.run(extractor._ensure_tab())

    assert tab.tab_id == "tab-new"
    assert proxy.opened_urls == [DIANDIAN_URL]


def test_check_ready_false_when_input_missing():
    proxy = FakeProxy(states=[{"has_input": False, "login_required": False}])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)

    assert asyncio.run(extractor.check_ready()) is False


def test_extract_content_sends_prompt_with_share_text_and_returns_response():
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    proxy = FakeProxy(states=[
        {"count": 0, "text": "", "generating": False},
        {"success": True, "click_x": 12, "click_y": 34},
        {"login_required": False, "has_input": True},
        {"sent": True, "input_text": "", "count": 1, "text": share_text},
        {"count": 1, "text": share_text, "page_text": share_text, "generating": True},
        {"count": 2, "text": "点点返回的小红书正文内容，包含方法步骤和图片文字。", "generating": False},
        {"count": 2, "text": "点点返回的小红书正文内容，包含方法步骤和图片文字。", "generating": False},
        {"count": 2, "text": "点点返回的小红书正文内容，包含方法步骤和图片文字。", "generating": False},
    ])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)

    result = asyncio.run(extractor.extract_content(share_text=share_text, url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8", timeout_seconds=30))

    assert result.success is True
    assert "点点返回的小红书正文内容" in result.transcript
    assert share_text in result.prompt
    assert "小红书分享内容" in result.prompt
    assert any("6a338bc10000000021014bc8" in script for script in proxy.scripts)
    # click_at is no longer called (double-click removed); JS click() is sufficient
    assert proxy.clicked == []


def test_extract_content_retries_once_when_diandian_returns_unhelpful_response():
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    valid_reply = "点点第二次返回的小红书正文内容，包含方法步骤、图片文字和作者结论。"
    proxy = FakeProxy(states=[
        {"count": 0, "text": "", "generating": False},
        {"success": True, "click_x": 1177, "click_y": 343},
        {"login_required": False, "has_input": True},
        {"sent": True, "input_text": "", "count": 1, "text": share_text},
        {"count": 2, "text": "这个问题我暂时还没有好的思路，换个问题试试吧", "generating": False},
        {"count": 2, "text": "这个问题我暂时还没有好的思路，换个问题试试吧", "generating": False},
        {"count": 2, "text": "这个问题我暂时还没有好的思路，换个问题试试吧", "generating": False},
        {"count": 2, "text": "这个问题我暂时还没有好的思路，换个问题试试吧", "generating": False},
        {"success": True, "click_x": 1177, "click_y": 343},
        {"login_required": False, "has_input": True},
        {"sent": True, "input_text": "", "count": 3, "text": share_text},
        {"count": 4, "text": valid_reply, "generating": False},
        {"count": 4, "text": valid_reply, "generating": False},
        {"count": 4, "text": valid_reply, "generating": False},
    ])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)

    result = asyncio.run(extractor.extract_content(share_text=share_text, url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8", timeout_seconds=30))

    assert result.success is True
    assert result.transcript == valid_reply
    assert result.attempts == 2
    assert result.retried is True
    assert len(proxy.clicked) == 0


def test_extract_content_fails_after_two_unhelpful_responses():
    share_text = "【Anthropic博客的Agent Eval实践心得 | 小红书 - 你的生活兴趣社区】 https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8?source=webshare&xhsshare=pc_web&xsec_source=pc_share"
    unhelpful = "这个问题我暂时还没有好的思路，换个问题试试吧"
    proxy = FakeProxy(states=[
        {"count": 0, "text": "", "generating": False},
        {"success": True, "click_x": 1177, "click_y": 343},
        {"login_required": False, "has_input": True},
        {"sent": True, "input_text": "", "count": 1, "text": share_text},
        {"count": 2, "text": unhelpful, "generating": False},
        {"count": 2, "text": unhelpful, "generating": False},
        {"count": 2, "text": unhelpful, "generating": False},
        {"count": 2, "text": unhelpful, "generating": False},
        {"success": True, "click_x": 1177, "click_y": 343},
        {"login_required": False, "has_input": True},
        {"sent": True, "input_text": "", "count": 3, "text": share_text},
        {"count": 4, "text": unhelpful, "generating": False},
        {"count": 4, "text": unhelpful, "generating": False},
        {"count": 4, "text": unhelpful, "generating": False},
        {"count": 4, "text": unhelpful, "generating": False},
    ])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)

    result = asyncio.run(extractor.extract_content(share_text=share_text, url="https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8", timeout_seconds=30))

    assert result.success is False
    assert result.error == "xiaohongshu_diandian_unhelpful_response"
    assert result.attempts == 2
    assert result.retried is True
    assert len(proxy.clicked) == 0


def test_is_unhelpful_diandian_response_detects_retryable_short_answer():
    assert is_unhelpful_diandian_response("这个问题我暂时还没有好的思路，换个问题试试吧") is True
    assert is_unhelpful_diandian_response("点点返回的小红书正文内容，包含方法步骤、图片文字和作者结论。") is False


def test_send_prompt_fails_when_send_is_not_confirmed():
    # Provide enough states for the 12-iteration confirmation loop.
    # The prompt remains in the input box, so it should NOT be considered sent.
    prompt_text = "仍停留在输入框里的 prompt 文本内容足够长以匹配前20字符"
    confirm_state = {"sent": False, "input_text": prompt_text, "count": 0, "text": ""}
    proxy = FakeProxy(states=[
        {"success": True, "click_x": 12, "click_y": 34},
        {"login_required": False, "has_input": True},
    ] + [confirm_state] * 14)
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)
    tab = SimpleNamespace(tab_id="tab-existing", url=DIANDIAN_URL, title="点点")

    result = asyncio.run(extractor._send_prompt(tab, prompt_text))

    assert result["success"] is False
    assert result["error"] == "xiaohongshu_diandian_send_not_confirmed"


def test_send_prompt_script_targets_arrow_top_submit_wrapper_not_add_button():
    proxy = FakeProxy(states=[
        {"success": True, "click_x": 1177, "click_y": 343, "target_use": "#arrow_top", "target_class": "submit-button-wrapper"},
        {"login_required": False, "has_input": True},
        {"sent": True, "input_text": "", "count": 1, "text": "测试 prompt"},
    ])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)
    tab = SimpleNamespace(tab_id="tab-existing", url=DIANDIAN_URL, title="点点")

    result = asyncio.run(extractor._send_prompt(tab, "测试 prompt"))
    send_script = proxy.scripts[0]

    assert result["success"] is True
    # click_at no longer called (JS click() is used instead)
    assert ".submit-button-wrapper" in send_script
    assert "#arrow_top" in send_script
    assert "#addM" in send_script
    assert "ai-input-action-btn" in send_script


def test_wait_for_response_ignores_user_prompt_until_diandian_reply_is_stable():
    prompt = "请打开并解析下面这条小红书笔记分享内容\n小红书分享内容：https://www.xiaohongshu.com/discovery/item/6a338bc10000000021014bc8"
    reply = "点点返回的小红书正文内容，包含方法步骤、图片文字和作者结论。"
    proxy = FakeProxy(states=[
        {"count": 1, "text": prompt, "generating": False},
        {"count": 2, "text": reply, "generating": True},
        {"count": 2, "text": reply, "generating": False},
        {"count": 2, "text": reply, "generating": False},
    ])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)
    tab = SimpleNamespace(tab_id="tab-existing", url=DIANDIAN_URL, title="点点")

    result = asyncio.run(extractor._wait_for_response_complete(tab, previous_count=0, timeout_seconds=30, prompt=prompt))

    assert result == reply


def test_close_closes_managed_tab():
    proxy = FakeProxy(targets=[])
    extractor = XiaohongshuDiandianExtractor(proxy=proxy)
    asyncio.run(extractor._ensure_tab())

    asyncio.run(extractor.close())

    assert proxy.closed_tabs == ["tab-new"]
