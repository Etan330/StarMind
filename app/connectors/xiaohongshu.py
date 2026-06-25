from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.connectors.base import ConnectorItem
from app.connectors.cdp_proxy import CDPProxy, CDPTab, cdp_proxy


EVAL_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "extension" / "xiaohongshu_eval.js"


class XiaohongshuFavoritesCollector:
    def __init__(self, proxy: CDPProxy | None = None) -> None:
        self._proxy = proxy or cdp_proxy

    async def extract_favorites(self, url: str | None = None, limit: int | None = None) -> list[ConnectorItem]:
        target_url = str(url or "").strip()
        if not target_url:
            raise ValueError("小红书收藏页绑定用户 profile id，请先提供真实收藏页链接。")
        await self._proxy.connect()
        tab = await self._proxy.new_tab(target_url)
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
            title = str(item.get("title") or "").strip()
            metadata = {"source": "xiaohongshu_cdp_favorites"}
            if not title:
                title = "未识别标题"
                metadata["title_missing"] = True
            items.append(ConnectorItem(
                raw_url=href,
                title=title,
                platform="xiaohongshu",
                author=item.get("author"),
                content_type="note",
                metadata=metadata,
            ))
        return items

    @staticmethod
    def _inline_eval() -> str:
        return EVAL_SCRIPT_PATH.read_text(encoding="utf-8")


xiaohongshu_collector = XiaohongshuFavoritesCollector()
