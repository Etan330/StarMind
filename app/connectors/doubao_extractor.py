from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from app.connectors.cdp_proxy import CDPConnectionError, CDPProxy, CDPTab, cdp_proxy


DOUBAO_URL = "https://www.doubao.com"

UNIVERSAL_PROMPT = """请打开并解析下面这个链接，尽可能提取原始内容，不要只做摘要。

请先判断内容类型，并按对应规则处理：
1. 如果是视频内容：提取完整逐字稿/字幕/口播内容，尽量保留原始表达和时间顺序；如果无法获取逐字稿，请说明原因，并提取标题、简介、页面可见文字和关键评论线索。
2. 如果是图文、笔记或图片内容：提取标题、正文、图片中的文字（按图片顺序整理）、小标题、列表和页面可见文字。
3. 如果是文章、网页或纯文字内容：提取标题和正文，保留段落结构、关键引用和列表，去掉无关导航、广告和推荐内容。

输出要求：
- 保留原文信息，尽量完整，不要只总结。
- 如果有无法访问、无法识别或需要登录的内容，请明确说明。
- 最后给出一个简短的“内容类型判断”。

链接：{url}"""

PROMPTS = {
    "video": UNIVERSAL_PROMPT,
    "note": UNIVERSAL_PROMPT,
    "article": UNIVERSAL_PROMPT,
    "auto": UNIVERSAL_PROMPT,
}


def normalize_content_type(content_type: str | None) -> str:
    normalized = str(content_type or "").strip().lower()
    if normalized == "video":
        return "video"
    if normalized in {"note", "image", "gallery", "post"}:
        return "note"
    if normalized in {"article", "text"}:
        return "article"
    return "auto"

DOUBAO_CHAT_SCRIPT = Path(__file__).resolve().parents[1] / "extension" / "doubao_chat.js"


@dataclass
class ExtractResult:
    url: str
    transcript: str
    text_content: str
    title: str
    success: bool
    error: str | None = None
    prompt: str = ""
    elapsed_seconds: float | None = None


class DoubaoExtractor:
    """Extract content via doubao.com web interface using CDP."""

    def __init__(self, proxy: CDPProxy | None = None) -> None:
        self._proxy = proxy or cdp_proxy
        self._tab: CDPTab | None = None

    async def _ensure_tab(self) -> CDPTab:
        await self._proxy.connect()
        if self._tab is not None:
            return self._tab
        try:
            targets = await self._proxy.list_targets()
        except Exception:
            targets = []
        for target in targets:
            url = str(target.get("url") or "")
            if "doubao.com" not in url:
                continue
            tab_id = str(target.get("id") or target.get("targetId") or target.get("tab_id") or "")
            if not tab_id:
                continue
            self._tab = CDPTab(tab_id=tab_id, url=url, title=str(target.get("title") or ""))
            return self._tab
        self._tab = await self._proxy.new_tab(DOUBAO_URL)
        await self._proxy.wait_for_load(self._tab)
        return self._tab

    async def check_login(self) -> bool:
        tab = await self._ensure_tab()
        state = await self._login_state(tab)
        if state.get("login_required"):
            return False
        return bool(state.get("has_input"))

    async def _login_state(self, tab: CDPTab) -> dict[str, Any]:
        raw = await self._proxy.eval_script(tab, """
        (() => {
            const text = document.body?.innerText || '';
            const visibleText = text.slice(0, 2000);
            const hasLoginText = /登录|注册|扫码登录|手机号登录|验证码|未登录|login|sign in/i.test(visibleText);
            const hasLoggedInSignal = /退出|头像|消息|新对话|历史记录/i.test(visibleText);
            const isVisible = (node) => {
                if (!node) return false;
                const rect = node.getBoundingClientRect?.();
                const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                return (!rect || (rect.width > 0 && rect.height > 0)) && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
            };
            const inputSelectors = [
                'textarea:not([readonly])',
                'input[type="text"]:not([readonly])',
                '[contenteditable="true"]',
                '[role="textbox"]',
                '.ProseMirror',
                '.chat-input textarea',
                '[class*="input"] textarea',
                '[data-testid*="input"] textarea',
                '[data-testid*="chat"] textarea',
                '[class*="editor"][contenteditable="true"]',
                '[class*="Editor"][contenteditable="true"]'
            ];
            const input = inputSelectors
                .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                .find((node) => isVisible(node) && (node.matches?.('textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror') || node.isContentEditable));
            const loginButtons = Array.from(document.querySelectorAll('button, [role="button"], a')).filter((node) => /登录|注册|login|sign/i.test(node.innerText || node.getAttribute('aria-label') || ''));
            const modal = Array.from(document.querySelectorAll('[role="dialog"], [class*="modal"], [class*="login"], [class*="Login"]')).find((node) => /登录|注册|扫码|手机号|验证码|login|sign/i.test(node.innerText || ''));
            const loginRequired = Boolean(modal || (!input && hasLoginText && loginButtons.length));
            return JSON.stringify({
                login_required: loginRequired,
                has_input: Boolean(input),
                has_login_modal: Boolean(modal),
                has_login_text: hasLoginText,
                has_logged_in_signal: hasLoggedInSignal,
                unknown: !loginRequired && !input,
                message: loginRequired ? '检测到豆包登录状态' : ''
            });
        })()
        """)
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def extract_content(self, url: str, content_type: str = "auto", timeout_seconds: int = 240) -> ExtractResult:
        prompt = UNIVERSAL_PROMPT.format(url=url)
        started_at = monotonic()

        tab = await self._ensure_tab()
        await asyncio.sleep(2)

        try:
            before = await self._message_state(tab)
            send_result = await self._send_prompt(tab, prompt)
            if not send_result.get("success"):
                return ExtractResult(
                    url=url,
                    transcript="",
                    text_content="",
                    title="",
                    success=False,
                    error=send_result.get("error", "发送失败"),
                    prompt=prompt,
                    elapsed_seconds=monotonic() - started_at,
                )

            content = await self._wait_for_response_complete(tab, before.get("count", 0), timeout_seconds)
            if not content:
                return ExtractResult(
                    url=url,
                    transcript="",
                    text_content="",
                    title="",
                    success=False,
                    error="豆包未返回完整内容（超时）",
                    prompt=prompt,
                    elapsed_seconds=monotonic() - started_at,
                )

            return ExtractResult(
                url=url,
                transcript=content,
                text_content=content,
                title=url.split("/")[-1][:60],
                success=True,
                prompt=prompt,
                elapsed_seconds=monotonic() - started_at,
            )

        except Exception as e:
            return ExtractResult(
                url=url,
                transcript="",
                text_content="",
                title="",
                success=False,
                error=str(e),
                prompt=prompt,
                elapsed_seconds=monotonic() - started_at,
            )

    async def _send_prompt(self, tab: CDPTab, prompt: str) -> dict[str, Any]:
        login_state = await self._login_state(tab)
        if login_state.get("login_required"):
            return {"success": False, "error": "doubao_login_required", "message": "检测到豆包登录弹窗"}
        before = await self._message_state(tab)
        send_script = f"""
        (() => {{
            const prompt = {json.dumps(prompt)};
            const urlMatch = prompt.match(/https?:\/\/\S+/);
            const url = urlMatch ? urlMatch[0].replace(/[，。\s]+$/, '') : '';
            const isVisible = (node) => {{
                if (!node) return false;
                const rect = node.getBoundingClientRect?.();
                const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                return (!rect || (rect.width > 0 && rect.height > 0)) && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
            }};
            const inputSelectors = [
                'textarea:not([readonly])',
                'input[type="text"]:not([readonly])',
                '[contenteditable="true"]',
                '[role="textbox"]',
                '.ProseMirror',
                '.chat-input textarea',
                '[class*="input"] textarea',
                '[data-testid*="input"] textarea',
                '[data-testid*="chat"] textarea',
                '[class*="editor"][contenteditable="true"]',
                '[class*="Editor"][contenteditable="true"]'
            ];
            const inputCandidates = inputSelectors
                .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                .filter((node) => isVisible(node) && (node.matches?.('textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror') || node.isContentEditable));
            const input = inputCandidates.find((node) => /发消息|按住空格|说话/.test(node.getAttribute('placeholder') || '')) || inputCandidates[0];
            if (!input) return JSON.stringify({{success: false, error: 'chat_input_not_ready'}});
            const readText = () => (input.value || input.innerText || input.textContent || '').trim();
            const verifyWritten = () => {{
                const written = readText();
                return written.includes(prompt.slice(0, 20)) || (url && written.includes(url));
            }};
            const selectAllContent = () => {{
                input.focus();
                if ('select' in input) {{
                    input.select();
                    return;
                }}
                const selection = window.getSelection?.();
                if (selection && document.createRange) {{
                    const range = document.createRange();
                    range.selectNodeContents(input);
                    selection.removeAllRanges();
                    selection.addRange(range);
                }}
            }};
            const dispatchTextEvents = () => {{
                input.dispatchEvent(new InputEvent('beforeinput', {{bubbles: true, cancelable: true, inputType: 'insertText', data: prompt}}));
                input.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: prompt}}));
                input.dispatchEvent(new Event('change', {{bubbles: true}}));
                input.dispatchEvent(new CompositionEvent('compositionend', {{bubbles: true, data: prompt}}));
                input.dispatchEvent(new KeyboardEvent('keydown', {{bubbles: true, key: 'Process'}}));
                input.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true, key: 'Process'}}));
            }};
            const pastePrompt = () => {{
                try {{
                    selectAllContent();
                    const dataTransfer = new DataTransfer();
                    dataTransfer.setData('text/plain', prompt);
                    const pasteEvent = new ClipboardEvent('paste', {{bubbles: true, cancelable: true, clipboardData: dataTransfer}});
                    const notPrevented = input.dispatchEvent(pasteEvent);
                    dispatchTextEvents();
                    return verifyWritten() || !notPrevented;
                }} catch (_error) {{
                    return false;
                }}
            }};
            const writeByDom = () => {{
                selectAllContent();
                if ('value' in input) {{
                    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set;
                    if (setter) setter.call(input, prompt);
                    else input.value = prompt;
                }} else {{
                    input.textContent = '';
                    selectAllContent();
                    const inserted = document.execCommand && document.execCommand('insertText', false, prompt);
                    if (!inserted) input.textContent = prompt;
                }}
                dispatchTextEvents();
                return verifyWritten();
            }};
            pastePrompt();
            if (!verifyWritten()) writeByDom();
            const written = readText();

            if (!written.includes(prompt.slice(0, 20)) && !(url && written.includes(url))) {{
                return JSON.stringify({{success: false, error: 'prompt_input_not_applied'}});
            }}
            const inputRect = input.getBoundingClientRect();
            const buttonScope = input.closest('form, [class*="chat"], [class*="input"], [class*="composer"], [class*="Editor"], [class*="editor"]') || document;
            const buttonCandidates = Array.from(buttonScope.querySelectorAll('button, [role="button"]')).concat(Array.from(document.querySelectorAll('button, [role="button"]')));
            const buttons = Array.from(new Set(buttonCandidates)).filter(isVisible);
            const enabledButtons = buttons.filter((button) => !button.disabled && button.getAttribute('aria-disabled') !== 'true');
            const signal = (button) => [button.innerText, button.getAttribute('aria-label'), button.getAttribute('title'), button.className, button.querySelector('svg')?.getAttribute('aria-label'), button.querySelector('svg')?.className?.baseVal].join(' ');
            const isNearInputRight = (button) => {{
                const rect = button.getBoundingClientRect();
                return rect.left >= inputRect.left && rect.left <= inputRect.right + 80 && rect.top >= inputRect.top - 120 && rect.top <= inputRect.bottom + 120 && rect.left > inputRect.right - 220;
            }};
            const buttonScore = (button) => {{
                const rect = button.getBoundingClientRect();
                const text = (button.innerText || '').trim();
                const hint = signal(button);
                let score = 0;
                if (isNearInputRight(button)) score += 100;
                if (/send-msg-btn|g-send-msg-btn|bg-g-send-msg-btn-bg|text-dbx-text-static-white-primary/i.test(hint)) score += 120;
                if (/发送|send|submit|arrow|paper-plane|plane/i.test(hint)) score += 80;
                if (rect.width >= 28 && rect.width <= 48 && rect.height >= 28 && rect.height <= 48) score += 40;
                if (!text) score += 20;
                if (/更多/.test(text)) score -= 200;
                if (/快速|图像|翻译|深度|搜索|附件|上传/.test(text)) score -= 80;
                score += Math.max(0, rect.left - inputRect.left) / 10;
                return score;
            }};
            const rightmostNearButtons = enabledButtons
                .filter(isNearInputRight)
                .sort((a, b) => buttonScore(b) - buttonScore(a));
            const explicitSendButtons = enabledButtons
                .filter((button) => /send-msg-btn|g-send-msg-btn|bg-g-send-msg-btn-bg|发送|send|submit|arrow|paper-plane|plane/i.test(signal(button)))
                .sort((a, b) => buttonScore(b) - buttonScore(a));
            const sendButton = rightmostNearButtons[0] || explicitSendButtons[0];
            if (!sendButton) {{
                const disabled = buttons.find((button) => /send-msg-btn|g-send-msg-btn|bg-g-send-msg-btn-bg|发送|send|submit|arrow|paper-plane|plane/i.test(signal(button)));
                return JSON.stringify({{success: false, error: disabled ? 'send_button_disabled' : 'send_button_not_found'}});
            }}
            const rect = sendButton.getBoundingClientRect();
            sendButton.click();
            return JSON.stringify({{success: true, url, click_x: rect.left + rect.width / 2, click_y: rect.top + rect.height / 2}});
        }})()
        """
        result = await self._proxy.eval_script(tab, send_script)
        payload = json.loads(result) if isinstance(result, str) else dict(result or {})
        await asyncio.sleep(2)
        login_state = await self._login_state(tab)
        if login_state.get("login_required"):
            return {"success": False, "error": "doubao_login_required", "message": "检测到豆包登录弹窗"}
        if not payload.get("success"):
            return payload
        click_x = payload.get("click_x")
        click_y = payload.get("click_y")
        if isinstance(click_x, (int, float)) and isinstance(click_y, (int, float)) and hasattr(self._proxy, "click_at"):
            try:
                await self._proxy.click_at(tab, float(click_x), float(click_y))
                await asyncio.sleep(1)
            except Exception:
                pass
        after = await self._message_state(tab)
        before_count = int(before.get("count") or 0)
        after_count = int(after.get("count") or 0)
        page_text = str(after.get("page_text") or after.get("text") or "")
        url = str(payload.get("url") or "")
        if after_count <= before_count and (not url or url not in page_text):
            return {"success": False, "error": "send_click_no_effect"}
        return {"success": True}

    async def _message_state(self, tab: CDPTab) -> dict[str, Any]:
        raw = await self._proxy.eval_script(tab, """
        (() => {
            const nodes = Array.from(document.querySelectorAll('[class*="message"], [class*="markdown"], [data-testid*="message"], [class*="answer"]'));
            const visible = nodes.map((node) => (node.innerText || '').trim()).filter(Boolean);
            const last = visible.length ? visible[visible.length - 1] : '';
            const controls = Array.from(document.querySelectorAll('button, [role="button"], [class*="loading"], [class*="stop"], [class*="spinner"], [class*="generat"]'));
            const generating = controls.some((node) => /停止|stop|生成中|思考中|正在生成|loading|spinner/i.test(node.innerText || node.getAttribute('aria-label') || node.className || ''));
            const canCopyOrRegenerate = controls.some((node) => /复制|copy|重新生成|regenerate/i.test(node.innerText || node.getAttribute('aria-label') || node.className || ''));
            return JSON.stringify({count: visible.length, text: last, page_text: text.slice(-4000), generating: generating && !canCopyOrRegenerate});
        })()
        """)
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def _wait_for_response_complete(self, tab: CDPTab, previous_count: int, timeout_seconds: int) -> str:
        deadline = monotonic() + max(30, timeout_seconds)
        stable_rounds = 0
        last_text = ""
        while monotonic() < deadline:
            await asyncio.sleep(2)
            state = await self._message_state(tab)
            text = str(state.get("text") or "").strip()
            count = int(state.get("count") or 0)
            generating = bool(state.get("generating"))
            if count <= previous_count or len(text) < 20:
                stable_rounds = 0
                last_text = text
                continue
            if text == last_text and not generating:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_text = text
            if stable_rounds >= 5:
                return text
        return last_text if len(last_text) >= 20 else ""

    async def batch_extract(self, urls: list[str], content_type: str = "auto", timeout_seconds: int = 240) -> list[ExtractResult]:
        results = []
        for url in urls:
            result = await self.extract_content(url, content_type, timeout_seconds=timeout_seconds)
            results.append(result)
            if not result.success:
                continue
            await asyncio.sleep(2)  # Rate limit between requests
        return results

    async def close(self, close_tab: bool = True) -> None:
        if not self._tab:
            return
        if close_tab:
            await self._proxy.close_tab(self._tab)
        self._tab = None
