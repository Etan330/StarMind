from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from app.connectors.cdp_proxy import CDPConnectionError, CDPProxy, CDPTab, cdp_proxy
from app.connectors.extract_pacing import CHALLENGE_DETECT_JS, is_low_quality_extract


DOUBAO_URL = "https://www.doubao.com"

# 提取所用 prompt 的版本号。每次实质改动 UNIVERSAL_PROMPT 时 bump，
# 写入 candidate/RawSource.metadata 的 extract_prompt_version，便于追溯/对比提取效果。
DOUBAO_PROMPT_VERSION = "doubao_v1"

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

    async def _challenge_state(self, tab: CDPTab) -> dict[str, Any]:
        """检测人机验证（滑块/拼图/captcha）。返回 {challenge_required, kind, message}。

        与点点 extractor 共用 CHALLENGE_DETECT_JS，保证两平台检测口径一致。
        kind=login 表示只是登录弹窗（交给 _login_state 走 doubao_login_required），
        kind=human_verification 才是需要用户手动过的人机验证。
        """
        try:
            raw = await self._proxy.eval_script(tab, CHALLENGE_DETECT_JS)
        except Exception:
            return {"challenge_required": False, "kind": "none", "message": ""}
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def start_new_conversation(self) -> dict[str, Any]:
        """开新对话窗口（反爬节流：清空上下文、推迟人机验证出现）。

        两段式回退：① 点页面「新对话」入口（reactClickable + click），用消息计数归零确认；
        ② 回退到 location.assign(DOUBAO_URL)（实测对 doubao 重开会话比 navigate 可靠）。
        换窗后下一条 extract 会重取 _message_state 作 before，计数天然对齐。
        """
        tab = await self._ensure_tab()
        before = await self._message_state(tab)
        before_count = int(before.get("count") or 0)

        # ① 点「新对话」入口
        click_script = r"""
        (() => {
            const isVisible = (node) => {
                if (!node) return false;
                const rect = node.getBoundingClientRect?.();
                const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                return (!rect || (rect.width > 0 && rect.height > 0)) && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
            };
            const hasReactOnClick = (node) => {
                if (!node || !node.tagName) return false;
                const key = Object.keys(node).find((k) => k.startsWith('__reactProps'));
                return Boolean(key && typeof node[key]?.onClick === 'function');
            };
            const reactClickable = (node) => {
                if (!node) return null;
                if (hasReactOnClick(node)) return node;
                const btn = node.closest?.('button, [role="button"], a');
                if (btn && hasReactOnClick(btn)) return btn;
                return btn || node;
            };
            const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, [class*="new" i]'));
            const target = candidates.find((node) => {
                if (!isVisible(node)) return false;
                const label = (node.innerText || '') + ' ' + (node.getAttribute('aria-label') || '') + ' ' + (node.getAttribute('title') || '');
                return /新对话|新建对话|开启新对话|new chat|new conversation/i.test(label);
            });
            if (!target) return JSON.stringify({clicked: false});
            const clickable = reactClickable(target);
            let react_click_attempted = false;
            try {
                const key = Object.keys(clickable).find((k) => k.startsWith('__reactProps'));
                const props = key ? clickable[key] : null;
                if (props && typeof props.onClick === 'function') { react_click_attempted = true; props.onClick(); }
            } catch (_e) {}
            clickable.click?.();
            return JSON.stringify({clicked: true, react_click_attempted});
        })()
        """
        try:
            raw = await self._proxy.eval_script(tab, click_script)
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            payload = {"clicked": False}

        if payload.get("clicked"):
            # 等消息区清零确认换窗成功
            for _ in range(5):
                await asyncio.sleep(1)
                state = await self._message_state(tab)
                if int(state.get("count") or 0) < before_count or int(state.get("count") or 0) == 0:
                    return {"success": True, "method": "new_chat_button"}

        # ② 回退：location.assign 重开会话
        try:
            await self._proxy.eval_script(tab, f"location.assign({json.dumps(DOUBAO_URL)})")
            await self._proxy.wait_for_load(tab)
        except Exception:
            pass
        for _ in range(8):
            await asyncio.sleep(1)
            login_state = await self._login_state(tab)
            if login_state.get("has_input"):
                return {"success": True, "method": "location_assign"}
        return {"success": False, "method": "location_assign"}

    async def extract_content(self, url: str, content_type: str = "auto", timeout_seconds: int = 240) -> ExtractResult:
        prompt = UNIVERSAL_PROMPT.format(url=url)
        started_at = monotonic()

        tab = await self._ensure_tab()
        await asyncio.sleep(2)

        try:
            before = await self._message_state(tab)
            send_result = await self._send_prompt(tab, prompt)
            if not send_result.get("success"):
                error = send_result.get("error", "发送失败")
                # 发送未确认时，区分是不是人机验证拦住了（区别于普通发送失败）。
                if error != "doubao_login_required":
                    challenge = await self._challenge_state(tab)
                    if challenge.get("challenge_required"):
                        error = "doubao_human_verification_required"
                return ExtractResult(
                    url=url,
                    transcript="",
                    text_content="",
                    title="",
                    success=False,
                    error=error,
                    prompt=prompt,
                    elapsed_seconds=monotonic() - started_at,
                )

            content = await self._wait_for_response_complete(tab, int(before.get("assistant_count", 0)), timeout_seconds, prompt)
            if not content:
                # 超时无回复：可能是中途弹了人机验证，把检测结果反映到 error 上。
                error = "豆包未返回完整内容（超时）"
                challenge = await self._challenge_state(tab)
                if challenge.get("challenge_required"):
                    error = "doubao_human_verification_required"
                return ExtractResult(
                    url=url,
                    transcript="",
                    text_content="",
                    title="",
                    success=False,
                    error=error,
                    prompt=prompt,
                    elapsed_seconds=monotonic() - started_at,
                )

            # 低质量回绝（无法访问/请登录/无内容这类短回复）不算成功，避免噪声入库。
            if is_low_quality_extract(content):
                return ExtractResult(
                    url=url,
                    transcript="",
                    text_content="",
                    title="",
                    success=False,
                    error="doubao_low_quality_response",
                    prompt=prompt,
                    elapsed_seconds=monotonic() - started_at,
                )

            return ExtractResult(
                url=url,
                transcript=content,
                text_content=content,
                # 标题由扫描收藏页阶段提供（已写入 candidate.title），豆包只负责抽正文。
                # 不要用 url.split('/')[-1] 编造视频ID当标题，否则会覆盖掉扫描拿到的真实标题。
                title="",
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
            const urlMatch = prompt.match(/https?:\\/\\/\\S+/);
            const url = urlMatch ? urlMatch[0].replace(/[，。\\s]+$/, '') : '';
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
                '[class*="Editor"][contenteditable="true"]',
                '[class*="input"] [contenteditable="true"]',
                '[class*="composer"] [contenteditable="true"]',
                '[class*="chat"] [contenteditable="true"]'
            ];
            const inputCandidates = inputSelectors
                .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                .filter((node) => isVisible(node) && (node.matches?.('textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror') || node.isContentEditable));
            const inputScore = (node) => {{
                const rect = node.getBoundingClientRect();
                const hint = [node.getAttribute('placeholder'), node.getAttribute('aria-label'), node.className, node.closest('[class*="composer"], [class*="input"], [class*="chat"]')?.className].join(' ');
                let score = 0;
                if (/发消息|按住空格|说话|输入|message|chat/i.test(hint)) score += 80;
                if (/composer|input|chat/i.test(hint)) score += 60;
                if (rect.top > window.innerHeight * 0.45) score += 40;
                if (rect.width > 200) score += 20;
                return score;
            }};
            const input = inputCandidates.sort((a, b) => inputScore(b) - inputScore(a))[0];
            if (!input) return JSON.stringify({{success: false, error: 'chat_input_not_ready'}});
            const readText = () => (input.value || input.innerText || input.textContent || '').trim();
            const verifyWritten = () => {{
                const written = readText();
                if (url) return written.includes(url);
                return written.includes(prompt.slice(0, 40));
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
                try {{
                    const reactPropsKey = Object.keys(input).find((key) => key.startsWith('__reactProps'));
                    const reactProps = reactPropsKey ? input[reactPropsKey] : null;
                    const event = {{target: input, currentTarget: input, type: 'change', nativeEvent: {{}}}};
                    if (reactProps && typeof reactProps.onChange === 'function') reactProps.onChange(event);
                    if (reactProps && typeof reactProps.onInput === 'function') reactProps.onInput(event);
                }} catch (_error) {{}}
            }};
            const pastePrompt = () => {{
                try {{
                    selectAllContent();
                    const dataTransfer = new DataTransfer();
                    dataTransfer.setData('text/plain', prompt);
                    const pasteEvent = new ClipboardEvent('paste', {{bubbles: true, cancelable: true, clipboardData: dataTransfer}});
                    input.dispatchEvent(pasteEvent);
                    dispatchTextEvents();
                    return verifyWritten();
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

            if ((url && !written.includes(url)) || (!url && !written.includes(prompt.slice(0, 40)))) {{
                return JSON.stringify({{success: false, error: 'prompt_input_not_applied', input_text: written, url}});
            }}
            const inputRect = input.getBoundingClientRect();
            // hasReactOnClick: 元素自身是否挂了 React onClick handler。
            const hasReactOnClick = (node) => {{
                if (!node || !node.tagName) return false;
                const key = Object.keys(node).find((k) => k.startsWith('__reactProps'));
                return Boolean(key && typeof node[key]?.onClick === 'function');
            }};
            // reactClickable: 把任意入口（svg / wrapper / button）归一到「带 React onClick 的真实可点元素」。
            // 豆包发送区是 <div.send-btn-wrapper>(onClick) > <button#flow-end-msg-send>(onClick) > <svg.size-18>。
            // 旧逻辑会选中内部 svg（无 onClick）或被 closest('[class*=send]') 命中 wrapper 后又按映射元素调用，
            // 导致 sendButton.click() / reactProps.onClick 都打空。这里确保最终拿到的是真正可点的 button/wrapper。
            const reactClickable = (node) => {{
                if (!node) return null;
                if (node.tagName === 'BUTTON' && hasReactOnClick(node)) return node;
                const btn = node.closest?.('button#flow-end-msg-send, button');
                if (btn && hasReactOnClick(btn)) return btn;
                const wrap = node.closest?.('[class*="send-btn-wrapper"], [class*="send"]');
                if (wrap && hasReactOnClick(wrap)) return wrap;
                return btn || node;
            }};
            const useHref = (button) => Array.from(button.querySelectorAll?.('use') || []).map((use) => use.getAttribute('xlink:href') || use.getAttribute('href') || '').join(' ');
            const signal = (button) => [button.innerText, button.getAttribute('aria-label'), button.getAttribute('title'), button.className?.baseVal || button.className, button.querySelector?.('svg')?.getAttribute('aria-label'), button.querySelector?.('svg')?.className?.baseVal, useHref(button)].join(' ');
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
            let sendButton = null;
            // ① 第一优先级：稳定 id 锚点 #flow-end-msg-send（实测可靠）。
            const anchorBtn = document.getElementById('flow-end-msg-send');
            if (anchorBtn && isVisible(anchorBtn) && !anchorBtn.disabled && anchorBtn.getAttribute('aria-disabled') !== 'true') {{
                sendButton = reactClickable(anchorBtn);
            }}
            // ② 次优先：send-btn-wrapper（取其内部 button，否则 wrapper 自身）。
            if (!sendButton) {{
                const wrapper = Array.from(document.querySelectorAll('[class*="send-btn-wrapper"]')).find(isVisible);
                if (wrapper) sendButton = reactClickable(wrapper.querySelector('button') || wrapper);
            }}
            // ③ 兜底：保留原 buttonScore 打分逻辑，最终 pick 包一层 reactClickable，杜绝选中内部 svg。
            let buttons = [];
            if (!sendButton) {{
                const buttonScope = input.closest('form, [class*="chat"], [class*="input"], [class*="composer"], [class*="Editor"], [class*="editor"]') || document;
                const candidateSelector = 'button, [role="button"], [class*="send"], [class*="submit"], [class*="Send"], [class*="Submit"], svg, svg use';
                const buttonCandidates = Array.from(buttonScope.querySelectorAll(candidateSelector)).concat(Array.from(document.querySelectorAll(candidateSelector)));
                buttons = Array.from(new Set(buttonCandidates)).map((node) => {{
                    if (node.tagName === 'BUTTON' || node.getAttribute?.('role') === 'button') return node;
                    return node.closest?.('button, [role="button"], [class*="send"], [class*="submit"], [class*="Send"], [class*="Submit"]') || node.querySelector?.('button, [role="button"]') || node;
                }}).filter(isVisible);
                const enabledButtons = Array.from(new Set(buttons)).filter((button) => !button.disabled && button.getAttribute('aria-disabled') !== 'true');
                const rightmostNearButtons = enabledButtons
                    .filter(isNearInputRight)
                    .sort((a, b) => buttonScore(b) - buttonScore(a));
                const explicitSendButtons = enabledButtons
                    .filter((button) => /send-msg-btn|g-send-msg-btn|bg-g-send-msg-btn-bg|发送|send|submit|arrow|paper-plane|plane/i.test(signal(button)))
                    .sort((a, b) => buttonScore(b) - buttonScore(a));
                sendButton = reactClickable(rightmostNearButtons[0] || explicitSendButtons[0]);
            }}
            if (!sendButton) {{
                const disabled = buttons.find((button) => /send-msg-btn|g-send-msg-btn|bg-g-send-msg-btn-bg|发送|send|submit|arrow|paper-plane|plane/i.test(signal(button)));
                return JSON.stringify({{success: false, error: disabled ? 'send_button_disabled' : 'send_button_not_found'}});
            }}
            const rect = sendButton.getBoundingClientRect();
            let react_click_attempted = false;
            try {{
                const reactPropsKey = Object.keys(sendButton).find((key) => key.startsWith('__reactProps'));
                const reactProps = reactPropsKey ? sendButton[reactPropsKey] : null;
                if (reactProps && typeof reactProps.onClick === 'function') {{
                    react_click_attempted = true;
                    reactProps.onClick();
                }}
            }} catch (_error) {{}}
            sendButton.click();
            const promptHead = prompt.slice(0, 40);
            const confirmSent = () => {{
                const inputText = readText();
                const messages = Array.from(document.querySelectorAll('[class*="message"], [class*="markdown"], [data-testid*="message"], [class*="answer"], [class*="chat-content"], [class*="ChatContent"]'))
                    .map((node) => (node.innerText || '').trim())
                    .filter(Boolean);
                return !inputText.includes(promptHead) || messages.some((text) => text.includes(promptHead) || (url && text.includes(url)));
            }};
            const confirmed = confirmSent();
            return JSON.stringify({{
                success: confirmed,
                error: confirmed ? undefined : 'doubao_send_not_confirmed',
                url,
                input_text: written,
                before_count: 0,
                react_click_attempted,
                send_button_class: String(sendButton.className?.baseVal || sendButton.className || ''),
                picked_id: String(sendButton.id || ''),
                picked_has_onclick: hasReactOnClick(sendButton),
                click_x: rect.left + rect.width / 2,
                click_y: rect.top + rect.height / 2
            }});
        }})()
        """
        result = await self._proxy.eval_script(tab, send_script)
        payload = json.loads(result) if isinstance(result, str) else dict(result or {})
        await asyncio.sleep(2)
        login_state = await self._login_state(tab)
        if login_state.get("login_required"):
            return {"success": False, "error": "doubao_login_required", "message": "检测到豆包登录弹窗"}
        # 脚本内的 confirmSent() 是同步执行的，紧跟在 onClick 之后——此时 React 还没把
        # 新消息渲染进 DOM，所以它几乎必然返回 success:false。绝不能据此提前 return，
        # 否则会跳过下面基于「消息数增加」的 Python 端确认重试（_message_state 轮询）。
        # 只有「点击根本没发生」的硬失败才提前返回：脚本既没找到/可点按钮，也没尝试任何点击。
        attempted_click = bool(
            payload.get("react_click_attempted")
            or payload.get("picked_has_onclick")
            or isinstance(payload.get("click_x"), (int, float))
            and isinstance(payload.get("click_y"), (int, float))
        )
        if not payload.get("success") and not attempted_click:
            return payload
        click_x = payload.get("click_x")
        click_y = payload.get("click_y")
        before_count = int(before.get("count") or payload.get("before_count") or 0)
        before_assistant = int(before.get("assistant_count") or 0)
        url = str(payload.get("url") or "")
        prompt_head = prompt.strip()[:40]

        async def confirm_sent() -> dict[str, Any]:
            state = await self._message_state(tab)
            after_count = int(state.get("count") or 0)
            after_assistant = int(state.get("assistant_count") or 0)
            input_text = str(state.get("input_text") or "")
            input_was_reported = "input_text" in state
            # input_cleared 只在输入框确实报告过、且原 prompt/url 已不在框内时才成立。
            input_cleared = bool(input_was_reported and prompt_head and prompt_head not in input_text and (not url or url not in input_text))
            # 发送成功的唯一可信信号是消息数增加（新增了一条用户消息 / 助手开始回复）。
            # 不再用 page_text 是否含 url 判定——未发送的 prompt 仍停留在输入框时，
            # 它的 url 也会出现在 page_text 里，会造成「没发出去却报成功」的假阳性。
            if after_assistant > before_assistant:
                return {"success": True, "confirmed_by": "assistant_count", "after_count": after_count}
            if after_count > before_count:
                return {"success": True, "confirmed_by": "message_count", "after_count": after_count}
            # input_cleared 仅作为消息计数尚未刷新出来时的弱补充信号。
            if input_cleared and after_count >= before_count:
                return {"success": True, "confirmed_by": "input_cleared", "after_count": after_count}
            return {
                "success": False,
                "error": "doubao_send_not_confirmed",
                "url": url,
                "click_x": click_x,
                "click_y": click_y,
                "input_text": str(payload.get("input_text") or input_text),
                "before_count": before_count,
                "after_count": after_count,
                "confirmed_by": None,
            }

        if payload.get("react_click_attempted"):
            await asyncio.sleep(1)
            react_confirmation = await confirm_sent()
            if react_confirmation.get("success"):
                return react_confirmation

        if isinstance(click_x, (int, float)) and isinstance(click_y, (int, float)) and hasattr(self._proxy, "click_at"):
            try:
                await self._proxy.click_at(tab, float(click_x), float(click_y))
                await asyncio.sleep(1)
                click_confirmation = await confirm_sent()
                if click_confirmation.get("success"):
                    return click_confirmation
            except Exception:
                pass

        if hasattr(self._proxy, "key"):
            try:
                await self._proxy.key(tab, "Enter", code="Enter", windows_virtual_key_code=13)
                await asyncio.sleep(1)
                key_confirmation = await confirm_sent()
                if key_confirmation.get("success"):
                    return key_confirmation
            except Exception:
                pass

        return await confirm_sent()

    async def _message_state(self, tab: CDPTab) -> dict[str, Any]:
        raw = await self._proxy.eval_script(tab, """
        (() => {
            // 豆包真实消息节点是 [data-message-id]。角色判定不能看内部是否含
            // send-msg-bubble-bg——实测助手回复容器里也会嵌套命中该类的子节点，
            // 会把助手消息误判成用户消息，assistant_count 永远为 0，导致
            // _wait_for_response_complete 永远等不到新回复而超时。
            // 稳定锚点：用户消息外层容器类含 justify-end（右对齐气泡），
            // 助手消息外层容器类含 grid-cols（左侧 markdown 网格布局）。
            const containerClass = (node) => String(node.className?.baseVal || node.className || '');
            const isUserMessage = (node) => {
                const cls = containerClass(node);
                if (/justify-end/.test(cls)) return true;
                if (/grid-cols/.test(cls)) return false;
                // 兜底：没有命中布局锚点时，回退到「直接子级气泡」判定（顶层而非深层后代）。
                return Boolean(node.querySelector(':scope > * [class*="send-msg-bubble-bg"]'));
            };
            const messageNodes = Array.from(document.querySelectorAll('[data-message-id]'));
            const records = messageNodes.map((node) => ({
                id: node.getAttribute('data-message-id') || '',
                is_user: isUserMessage(node),
                text: (node.innerText || '').trim(),
            }));
            const assistantRecords = records.filter((record) => !record.is_user && record.text);
            // 实测助手回复会被拆成多个 [data-message-id]：真正的解析正文 + 一条很短的
            // 「搜索 N 个关键词，参考 M 篇资料」状态行；且正文节点开头也会粘上这条状态行。
            // 取「最长」的助手记录作为正文（短状态行天然被排除），再剥掉开头的检索状态行。
            const stripSearchPrefix = (text) =>
                text.replace(/^\\s*(?:正在)?搜索\\s*\\d+\\s*个关键词[，,]?\\s*参考\\s*\\d+\\s*篇资料\\s*\\n?/, '').trim();
            const lastAssistant = assistantRecords.length
                ? assistantRecords.reduce((best, cur) => (cur.text.length > best.text.length ? cur : best))
                : null;
            const lastAssistantText = lastAssistant ? stripSearchPrefix(lastAssistant.text) : '';

            const pageText = document.body?.innerText || '';
            const inputs = Array.from(document.querySelectorAll('textarea:not([readonly]), input[type="text"]:not([readonly]), [contenteditable="true"], [role="textbox"], .ProseMirror'));
            const input = inputs.find((node) => {
                const rect = node.getBoundingClientRect?.();
                const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                return rect && rect.width > 0 && rect.height > 0 && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
            });
            const inputText = input ? (input.value || input.innerText || input.textContent || '').trim() : '';

            // 生成中检测：豆包发送后右下角按钮会从「发送箭头」切换为「停止」(square)。
            // 优先用 send 按钮的 data-loading / svg data-dbx-name，再兜底文本/类名扫描。
            const sendBtn = Array.from(document.querySelectorAll('button')).find((btn) => {
                const cls = String(btn.className?.baseVal || btn.className || '');
                return /send-msg-btn|g-send-msg-btn/i.test(cls);
            });
            let generating = false;
            if (sendBtn) {
                const svgName = sendBtn.querySelector('svg')?.getAttribute('data-dbx-name') || '';
                const dataName = sendBtn.getAttribute('data-dbx-name') || '';
                const aria = sendBtn.getAttribute('aria-label') || '';
                if (/stop|square|pause|停止|生成中/i.test(svgName + ' ' + dataName + ' ' + aria)) generating = true;
                if (sendBtn.getAttribute('data-loading') === 'true') generating = true;
            }
            if (!generating) {
                const controls = Array.from(document.querySelectorAll('button, [role="button"]'));
                generating = controls.some((node) => /停止生成|停止回答|正在生成|生成中|思考中/i.test((node.innerText || '') + ' ' + (node.getAttribute('aria-label') || '')));
            }

            return JSON.stringify({
                count: records.length,
                assistant_count: assistantRecords.length,
                text: lastAssistantText,
                last_assistant_id: lastAssistant ? lastAssistant.id : '',
                page_text: pageText.slice(-4000),
                input_text: inputText,
                generating: generating,
            });
        })()
        """)
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})

    async def _wait_for_response_complete(self, tab: CDPTab, previous_assistant_count: int, timeout_seconds: int, prompt: str = "") -> str:
        # previous_assistant_count: 发送前的助手消息数。等到出现新的助手消息后，
        # 再等其文本连续稳定且 generating=False 才视为完成。
        deadline = monotonic() + max(30, timeout_seconds)
        stable_rounds = 0
        last_text = ""
        prompt_head = str(prompt or "").strip()[:80]
        while monotonic() < deadline:
            await asyncio.sleep(2)
            state = await self._message_state(tab)
            text = str(state.get("text") or "").strip()
            assistant_count = int(state.get("assistant_count") or 0)
            generating = bool(state.get("generating"))
            is_user_prompt = bool(prompt_head and prompt_head in text)
            # 还没有新的助手回复（text 来自旧消息或用户消息），继续等。
            if assistant_count <= previous_assistant_count or len(text) < 2 or is_user_prompt:
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
        if prompt_head and prompt_head in last_text:
            return ""
        return last_text if len(last_text) >= 2 else ""

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
