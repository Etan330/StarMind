from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.connectors.cdp_proxy import CDPProxy, CDPTab, cdp_proxy
from app.connectors.extract_pacing import CHALLENGE_DETECT_JS


DIANDIAN_URL = "https://www.xiaohongshu.com/ai_chat"

XIAOHONGSHU_DIANDIAN_PROMPT = """请打开并解析下面这条小红书笔记分享内容，尽可能提取原始信息，不要只做摘要。

请重点提取：
1. 笔记标题
2. 正文内容
3. 图片中的文字、图表信息或截图文字
4. 作者明确表达的步骤、方法、经验、清单和结论
5. 如果无法访问或内容不可见，请明确说明原因

输出要求：
- 保留原文信息，尽量完整；
- 按标题、正文/OCR、要点、内容类型判断组织；
- 不要编造页面不可见内容。

小红书分享内容：
{share_text}"""


def is_unhelpful_diandian_response(text: str) -> bool:
    value = " ".join(str(text or "").split())
    if not value:
        return False
    patterns = [
        "暂时还没有好的思路",
        "换个问题试试",
        "暂时无法回答",
        "没有好的思路",
        "无法回答这个问题",
    ]
    return len(value) <= 80 and any(pattern in value for pattern in patterns)


@dataclass
class DiandianExtractResult:
    url: str
    transcript: str
    text_content: str
    title: str
    success: bool
    error: str | None = None
    prompt: str = ""
    elapsed_seconds: float | None = None
    attempts: int = 1
    retried: bool = False


class XiaohongshuDiandianExtractor:
    """Extract Xiaohongshu note content through the Xiaohongshu Diandian chat page."""

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
            if "xiaohongshu.com/ai_chat" not in url:
                continue
            tab_id = str(target.get("id") or target.get("targetId") or target.get("tab_id") or "")
            if not tab_id:
                continue
            self._tab = CDPTab(tab_id=tab_id, url=url, title=str(target.get("title") or ""))
            return self._tab
        self._tab = await self._proxy.new_tab(DIANDIAN_URL)
        await self._proxy.wait_for_load(self._tab)
        return self._tab

    async def check_ready(self) -> bool:
        tab = await self._ensure_tab()
        state = await self._ready_state(tab)
        return bool(state.get("has_input")) and not bool(state.get("login_required"))

    async def _ready_state(self, tab: CDPTab) -> dict[str, Any]:
        raw = await self._proxy.eval_script(tab, """
        (() => {
            const text = document.body?.innerText || '';
            const visibleText = text.slice(0, 2000);
            const hasLoginText = /登录|注册|扫码登录|手机号登录|验证码|未登录|login|sign in/i.test(visibleText);
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
                '[class*="input"] textarea',
                '[class*="chat"] textarea',
                '[class*="editor"][contenteditable="true"]',
                '[class*="Editor"][contenteditable="true"]'
            ];
            const input = inputSelectors
                .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                .find((node) => isVisible(node) && (node.matches?.('textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror') || node.isContentEditable));
            const modal = Array.from(document.querySelectorAll('[role="dialog"], [class*="modal"], [class*="login"], [class*="Login"]')).find((node) => /登录|注册|扫码|手机号|验证码|login|sign/i.test(node.innerText || ''));
            return JSON.stringify({login_required: Boolean(modal || (!input && hasLoginText)), has_input: Boolean(input)});
        })()
        """)
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def extract_content(self, share_text: str, url: str = "", content_type: str = "note", timeout_seconds: int = 240, max_attempts: int = 2) -> DiandianExtractResult:
        prompt = XIAOHONGSHU_DIANDIAN_PROMPT.format(share_text=share_text)
        started_at = monotonic()
        tab = await self._ensure_tab()
        await asyncio.sleep(1)
        attempts_limit = max(1, min(int(max_attempts or 1), 2))
        last_error = "xiaohongshu_diandian_timeout"
        try:
            for attempt in range(1, attempts_limit + 1):
                before = await self._message_state(tab)
                send_result = await self._send_prompt(tab, prompt)
                if not send_result.get("success"):
                    error = send_result.get("error", "xiaohongshu_diandian_send_failed")
                    challenge = await self._challenge_state(tab)
                    if challenge.get("challenge_required"):
                        error = "xiaohongshu_diandian_human_verification_required"
                    return DiandianExtractResult(
                        url=url,
                        transcript="",
                        text_content="",
                        title="",
                        success=False,
                        error=error,
                        prompt=prompt,
                        elapsed_seconds=monotonic() - started_at,
                        attempts=attempt,
                        retried=attempt > 1,
                    )
                content = await self._wait_for_response_complete(tab, int(before.get("count") or 0), timeout_seconds, prompt=prompt)
                if content and not is_unhelpful_diandian_response(content):
                    return DiandianExtractResult(
                        url=url,
                        transcript=content,
                        text_content=content,
                        title=url.split("/")[-1][:60] if url else "",
                        success=True,
                        prompt=prompt,
                        elapsed_seconds=monotonic() - started_at,
                        attempts=attempt,
                        retried=attempt > 1,
                    )
                last_error = "xiaohongshu_diandian_unhelpful_response" if content else "xiaohongshu_diandian_timeout"
                if attempt < attempts_limit:
                    await asyncio.sleep(1)
            return DiandianExtractResult(
                url=url,
                transcript="",
                text_content="",
                title="",
                success=False,
                error=last_error,
                prompt=prompt,
                elapsed_seconds=monotonic() - started_at,
                attempts=attempts_limit,
                retried=attempts_limit > 1,
            )
        except Exception as exc:
            return DiandianExtractResult(
                url=url,
                transcript="",
                text_content="",
                title="",
                success=False,
                error=str(exc),
                prompt=prompt,
                elapsed_seconds=monotonic() - started_at,
                attempts=1,
                retried=False,
            )

    async def _send_prompt(self, tab: CDPTab, prompt: str) -> dict[str, Any]:
        send_script = f"""
        (() => {{
            const prompt = {json.dumps(prompt)};
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
                '[class*="input"] textarea',
                '[class*="chat"] textarea',
                '[class*="editor"][contenteditable="true"]',
                '[class*="Editor"][contenteditable="true"]'
            ];
            const inputCandidates = inputSelectors
                .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                .filter((node) => isVisible(node) && (node.matches?.('textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror') || node.isContentEditable));
            const input = inputCandidates[0];
            if (!input) return JSON.stringify({{success: false, error: 'xiaohongshu_diandian_not_ready'}});
            const readText = () => (input.value || input.innerText || input.textContent || '').trim();
            input.focus();
            if ('value' in input) {{
                const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set;
                if (setter) setter.call(input, prompt);
                else input.value = prompt;
            }} else {{
                input.textContent = '';
                const inserted = document.execCommand && document.execCommand('insertText', false, prompt);
                if (!inserted) input.textContent = prompt;
            }}
            input.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: prompt}}));
            input.dispatchEvent(new Event('change', {{bubbles: true}}));
            if (!readText().includes(prompt.slice(0, 20))) return JSON.stringify({{success: false, error: 'prompt_input_not_applied'}});
            const inputRect = input.getBoundingClientRect();
            const scope = input.closest('form, [class*="chat"], [class*="input"], [class*="composer"], [class*="Editor"], [class*="editor"], .wendian-wrapper, .textarea-container') || document;
            const candidateSelector = 'button, [role="button"], .submit-button-wrapper, .submit-button, .bottom-box-right, .bottom-box-right-submit-button, svg.submit-button';
            const candidates = Array.from(new Set(Array.from(scope.querySelectorAll(candidateSelector)).concat(Array.from(document.querySelectorAll(candidateSelector))))).filter(isVisible);
            const enabled = candidates.filter((node) => !node.disabled && node.getAttribute('aria-disabled') !== 'true');
            const useHref = (node) => Array.from(node.querySelectorAll('use')).map((use) => use.getAttribute('xlink:href') || use.getAttribute('href') || '').join(' ');
            const classText = (node) => String(node.className?.baseVal || node.className || '');
            const signal = (node) => [node.innerText, node.getAttribute('aria-label'), node.getAttribute('title'), classText(node), useHref(node), node.querySelector('svg')?.getAttribute('aria-label'), node.querySelector('svg')?.className?.baseVal].join(' ');
            const isRightSubmitArea = (node) => {{
                const rect = node.getBoundingClientRect();
                return rect.left >= inputRect.right - 120 && rect.left <= inputRect.right + 40 && rect.top >= inputRect.bottom - 10 && rect.top <= inputRect.bottom + 80;
            }};
            const isLeftAddArea = (node) => {{
                const rect = node.getBoundingClientRect();
                return rect.left <= inputRect.left + 80 && rect.top >= inputRect.bottom - 10 && rect.top <= inputRect.bottom + 80;
            }};
            const score = (node) => {{
                const rect = node.getBoundingClientRect();
                const hint = signal(node);
                let value = 0;
                if (/#arrow_top/.test(hint)) value += 1000;
                if (/submit-button-wrapper|submit-button|bottom-box-right/.test(hint)) value += 500;
                if (isRightSubmitArea(node)) value += 300;
                if (/发送|send|submit|arrow|paper-plane|plane/i.test(hint)) value += 90;
                if (rect.width >= 16 && rect.width <= 64 && rect.height >= 16 && rect.height <= 64) value += 40;
                if (/#addM/.test(hint) || /ai-input-action-btn|bottom-box-left/.test(hint) || isLeftAddArea(node)) value -= 2000;
                if (/更多|上传|附件|图片|语音/.test(node.innerText || '')) value -= 120;
                return value;
            }};
            const ranked = enabled.map((node) => ({{node, score: score(node), hint: signal(node)}})).sort((a, b) => b.score - a.score);
            const best = ranked[0];
            if (!best || best.score < 500 || /#addM|ai-input-action-btn|bottom-box-left/.test(best.hint)) return JSON.stringify({{success: false, error: 'send_button_not_found'}});
            const sendButton = best.node.closest?.('.submit-button-wrapper') || best.node;
            const rect = sendButton.getBoundingClientRect();
            sendButton.click();
            return JSON.stringify({{success: true, click_x: rect.left + rect.width / 2, click_y: rect.top + rect.height / 2, target_use: useHref(sendButton), target_class: classText(sendButton)}});
        }})()
        """
        raw = await self._proxy.eval_script(tab, send_script)
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        await asyncio.sleep(1)
        state = await self._ready_state(tab)
        if state.get("login_required") or not state.get("has_input"):
            return {"success": False, "error": "xiaohongshu_diandian_not_ready"}
        if not payload.get("success"):
            return payload
        click_x = payload.get("click_x")
        click_y = payload.get("click_y")
        if isinstance(click_x, (int, float)) and isinstance(click_y, (int, float)) and hasattr(self._proxy, "click_at"):
            try:
                await self._proxy.click_at(tab, float(click_x), float(click_y))
            except Exception:
                pass
        for _ in range(6):
            await asyncio.sleep(0.5)
            raw_confirm = await self._proxy.eval_script(tab, f"""
            (() => {{
                const prompt = {json.dumps(prompt)};
                const isVisible = (node) => {{
                    if (!node) return false;
                    const rect = node.getBoundingClientRect?.();
                    const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                    return (!rect || (rect.width > 0 && rect.height > 0)) && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
                }};
                const input = Array.from(document.querySelectorAll('textarea:not([readonly]), input[type="text"]:not([readonly]), [contenteditable="true"], [role="textbox"], .ProseMirror'))
                    .find((node) => isVisible(node));
                const inputText = (input?.value || input?.innerText || input?.textContent || '').trim();
                const nodes = Array.from(document.querySelectorAll('[class*="message"], [class*="markdown"], [data-testid*="message"], [class*="answer"], [class*="chat-content"], [class*="ChatContent"]'));
                const visible = nodes.map((node) => (node.innerText || '').trim()).filter(Boolean);
                const promptHead = prompt.slice(0, 40);
                const sent = !inputText.includes(promptHead) && (visible.some((text) => text.includes(promptHead)) || visible.length > 0);
                return JSON.stringify({{sent, input_text: inputText, count: visible.length, text: visible[visible.length - 1] || ''}});
            }})()
            """)
            confirm = json.loads(raw_confirm) if isinstance(raw_confirm, str) else dict(raw_confirm or {})
            if confirm.get("sent"):
                return {"success": True}
        return {"success": False, "error": "xiaohongshu_diandian_send_not_confirmed"}

    async def _message_state(self, tab: CDPTab) -> dict[str, Any]:
        raw = await self._proxy.eval_script(tab, """
        (() => {
            const nodes = Array.from(document.querySelectorAll('[class*="message"], [class*="markdown"], [data-testid*="message"], [class*="answer"], [class*="chat-content"], [class*="ChatContent"]'));
            const visible = nodes.map((node) => (node.innerText || '').trim()).filter(Boolean);
            const last = visible.length ? visible[visible.length - 1] : '';
            const controls = Array.from(document.querySelectorAll('button, [role="button"], [class*="loading"], [class*="stop"], [class*="spinner"], [class*="generat"]'));
            const generating = controls.some((node) => /停止|stop|生成中|思考中|正在生成|loading|spinner/i.test(node.innerText || node.getAttribute('aria-label') || node.className || ''));
            const canCopyOrRegenerate = controls.some((node) => /复制|copy|重新生成|regenerate/i.test(node.innerText || node.getAttribute('aria-label') || node.className || ''));
            return JSON.stringify({count: visible.length, text: last, page_text: (document.body?.innerText || '').slice(-4000), generating: generating && !canCopyOrRegenerate});
        })()
        """)
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def _challenge_state(self, tab: CDPTab) -> dict[str, Any]:
        """检测人机验证（滑块/拼图/captcha）。与豆包 extractor 共用 CHALLENGE_DETECT_JS。"""
        try:
            raw = await self._proxy.eval_script(tab, CHALLENGE_DETECT_JS)
        except Exception:
            return {"challenge_required": False, "kind": "none", "message": ""}
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def start_new_conversation(self) -> dict[str, Any]:
        """开新对话窗口（反爬节流）。点点直接 location.assign(DIANDIAN_URL) 重开会话。"""
        tab = await self._ensure_tab()
        try:
            await self._proxy.eval_script(tab, f"location.assign({json.dumps(DIANDIAN_URL)})")
            await self._proxy.wait_for_load(tab)
        except Exception:
            pass
        for _ in range(8):
            await asyncio.sleep(1)
            ready = await self._ready_state(tab)
            if ready.get("has_input"):
                return {"success": True, "method": "location_assign"}
        return {"success": False, "method": "location_assign"}

    async def _wait_for_response_complete(self, tab: CDPTab, previous_count: int, timeout_seconds: int, prompt: str = "") -> str:
        deadline = monotonic() + max(30, timeout_seconds)
        stable_rounds = 0
        last_text = ""
        prompt_head = str(prompt or "").strip()[:80]
        while monotonic() < deadline:
            await asyncio.sleep(2)
            state = await self._message_state(tab)
            text = str(state.get("text") or "").strip()
            count = int(state.get("count") or 0)
            generating = bool(state.get("generating"))
            is_user_prompt = bool(prompt_head and prompt_head in text)
            if count <= previous_count or len(text) < 20 or is_user_prompt:
                stable_rounds = 0
                last_text = text
                continue
            if text == last_text and not generating:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_text = text
            if stable_rounds >= 2:
                return text
        if prompt_head and prompt_head in last_text:
            return ""
        return last_text if len(last_text) >= 20 else ""

    async def close(self, close_tab: bool = True) -> None:
        if not self._tab:
            return
        if close_tab:
            await self._proxy.close_tab(self._tab)
        self._tab = None
