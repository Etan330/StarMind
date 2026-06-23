from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.connectors.cdp_proxy import CDPConnectionError, CDPProxy, CDPTab, cdp_proxy


DOUBAO_URL = "https://www.doubao.com"

PROMPTS = {
    "video": "请帮我提取这个链接的完整逐字稿内容，保留原始表述，不要总结：{url}",
    "article": "请帮我提取这个链接中的所有文字内容，包括正文、标题和关键信息：{url}",
    "auto": "请帮我提取这个链接的全部内容，如果有视频请提取逐字稿，如果有图文请提取文字：{url}",
}

DOUBAO_CHAT_SCRIPT = Path(__file__).resolve().parents[1] / "extension" / "doubao_chat.js"


@dataclass
class ExtractResult:
    url: str
    transcript: str
    text_content: str
    title: str
    success: bool
    error: str | None = None


class DoubaoExtractor:
    """Extract content via doubao.com web interface using CDP."""

    def __init__(self, proxy: CDPProxy | None = None) -> None:
        self._proxy = proxy or cdp_proxy
        self._tab: CDPTab | None = None

    async def check_login(self) -> bool:
        await self._proxy.connect()
        tab = await self._proxy.new_tab(DOUBAO_URL)
        try:
            await self._proxy.wait_for_load(tab)
            logged_in = await self._proxy.eval_script(tab, """
                (() => {
                    const text = document.body?.innerText || '';
                    return !/登录|注册|login|sign/i.test(text.slice(0, 500)) || /退出|头像|消息/i.test(text.slice(0, 500));
                })()
            """)
            return bool(logged_in)
        finally:
            await self._proxy.close_tab(tab)

    async def extract_content(self, url: str, content_type: str = "auto") -> ExtractResult:
        prompt_template = PROMPTS.get(content_type, PROMPTS["auto"])
        prompt = prompt_template.format(url=url)

        await self._proxy.connect()
        if self._tab is None:
            self._tab = await self._proxy.new_tab(DOUBAO_URL)
            await self._proxy.wait_for_load(self._tab)
            await asyncio.sleep(2)

        try:
            # Type prompt and send
            send_script = f"""
            (() => {{
                const textarea = document.querySelector('textarea, [contenteditable="true"], .chat-input textarea');
                if (!textarea) return JSON.stringify({{success: false, error: "找不到输入框"}});
                textarea.focus();
                textarea.value = {json.dumps(prompt)};
                textarea.dispatchEvent(new Event('input', {{bubbles: true}}));
                setTimeout(() => {{
                    const btn = document.querySelector('button[class*="send"], button[aria-label*="发送"], [data-testid="send-button"]');
                    if (btn) btn.click();
                }}, 300);
                return JSON.stringify({{success: true}});
            }})()
            """
            result = await self._proxy.eval_script(self._tab, send_script)
            send_result = json.loads(result) if isinstance(result, str) else {"success": False}
            if not send_result.get("success"):
                return ExtractResult(url=url, transcript="", text_content="", title="", success=False, error=send_result.get("error", "发送失败"))

            # Wait for response (poll for generation complete)
            content = ""
            for _ in range(60):  # max 60s
                await asyncio.sleep(1)
                check = await self._proxy.eval_script(self._tab, """
                (() => {
                    const generating = document.querySelector('[class*="stop"], [class*="loading"], button[class*="regenerate"]');
                    const messages = document.querySelectorAll('[class*="message"], [class*="content"], [class*="markdown"]');
                    const last = messages.length > 0 ? messages[messages.length - 1] : null;
                    const text = last ? last.innerText : '';
                    const isGenerating = !!generating && generating.innerText.includes('停止');
                    return JSON.stringify({done: !isGenerating, text: text.slice(0, 10000)});
                })()
                """)
                status = json.loads(check) if isinstance(check, str) else {}
                if status.get("done") and status.get("text"):
                    content = status["text"]
                    break

            if not content:
                return ExtractResult(url=url, transcript="", text_content="", title="", success=False, error="豆包未返回内容（超时）")

            return ExtractResult(url=url, transcript=content, text_content=content, title=url.split("/")[-1][:60], success=True)

        except Exception as e:
            return ExtractResult(url=url, transcript="", text_content="", title="", success=False, error=str(e))

    async def batch_extract(self, urls: list[str], content_type: str = "auto") -> list[ExtractResult]:
        results = []
        for url in urls:
            result = await self.extract_content(url, content_type)
            results.append(result)
            if not result.success:
                continue
            await asyncio.sleep(2)  # Rate limit between requests
        return results

    async def close(self) -> None:
        if self._tab:
            await self._proxy.close_tab(self._tab)
            self._tab = None
