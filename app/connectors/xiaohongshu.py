from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.connectors.base import ConnectorItem
from app.connectors.cdp_proxy import CDPProxy, CDPTab, cdp_proxy


XIAOHONGSHU_FAVORITES_URL = "https://www.xiaohongshu.com/user/profile/self?tab=fav"

EVAL_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "extension" / "xiaohongshu_eval.js"


class XiaohongshuFavoritesCollector:
    def __init__(self, proxy: CDPProxy | None = None) -> None:
        self._proxy = proxy or cdp_proxy

    async def extract_favorites(self, url: str = XIAOHONGSHU_FAVORITES_URL, limit: int | None = None) -> list[ConnectorItem]:
        await self._proxy.connect()
        tab = await self._proxy.new_tab(url)
        try:
            await self._proxy.wait_for_load(tab)
            target = limit or 200
            for _ in range(min(target // 10, 20)):
                await self._proxy.scroll(tab)

            script = EVAL_SCRIPT_PATH.read_text(encoding="utf-8") if EVAL_SCRIPT_PATH.exists() else self._inline_eval()
            raw = await self._proxy.eval_script(tab, script)
            items_data = json.loads(raw) if isinstance(raw, str) else (raw or [])
        finally:
            await self._proxy.close_tab(tab)

        items: list[ConnectorItem] = []
        for item in items_data[: limit or 1000]:
            href = item.get("url") or ""
            if not href:
                continue
            items.append(ConnectorItem(
                raw_url=href,
                title=item.get("title") or href,
                platform="xiaohongshu",
                author=item.get("author"),
                content_type="note",
                metadata={"source": "xiaohongshu_cdp_favorites"},
            ))
        return items

    @staticmethod
    def _inline_eval() -> str:
        return """(() => {
            const anchors = Array.from(document.querySelectorAll('a[href]'));
            const seen = new Set();
            const items = [];
            for (const a of anchors) {
                try {
                    const url = new URL(a.getAttribute('href'), location.href);
                    if (!/(^|\\.)xiaohongshu\\.com$/.test(url.hostname)) continue;
                    if (!/\\/(explore|discovery\\/item)\\/[a-f0-9]+/.test(url.pathname)) continue;
                    const key = url.pathname;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    items.push({url: url.href, title: (a.getAttribute('title') || a.innerText || '').trim().split('\\n')[0].slice(0, 180), author: null});
                } catch(_) {}
            }
            return JSON.stringify(items);
        })()"""


xiaohongshu_collector = XiaohongshuFavoritesCollector()
