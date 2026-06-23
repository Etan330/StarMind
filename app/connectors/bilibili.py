from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.connectors.base import ConnectorItem
from app.connectors.cdp_proxy import CDPConnectionError, CDPProxy, CDPTab, cdp_proxy


BILIBILI_FAVORITES_URL = "https://space.bilibili.com/ajax/fav/getList"
BILIBILI_SPACE_URL = "https://www.bilibili.com/account/favorite"

EVAL_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "extension" / "bilibili_eval.js"


class BilibiliFavoritesCollector:
    def __init__(self, proxy: CDPProxy | None = None) -> None:
        self._proxy = proxy or cdp_proxy

    async def extract_favorites(self, url: str = BILIBILI_SPACE_URL, limit: int | None = None) -> list[ConnectorItem]:
        await self._proxy.connect()
        tab = await self._proxy.new_tab(url)
        try:
            await self._proxy.wait_for_load(tab)
            # Scroll to load more items
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
                platform="bilibili",
                author=item.get("author"),
                content_type="video",
                metadata={"source": "bilibili_cdp_favorites", "bvid": item.get("bvid")},
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
                    if (!/(^|\\.)bilibili\\.com$/.test(url.hostname)) continue;
                    const m = url.pathname.match(/\\/video\\/(BV[a-zA-Z0-9]+)/);
                    if (!m) continue;
                    if (seen.has(m[1])) continue;
                    seen.add(m[1]);
                    items.push({url: url.href, title: (a.getAttribute('title') || a.innerText || '').trim().slice(0, 180), author: null, bvid: m[1]});
                } catch(_) {}
            }
            return JSON.stringify(items);
        })()"""


bilibili_collector = BilibiliFavoritesCollector()
