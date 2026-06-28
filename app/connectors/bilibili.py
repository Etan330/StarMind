from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.connectors.base import ConnectorItem
from app.connectors.cdp_proxy import CDPConnectionError, CDPProxy, CDPTab, cdp_proxy


EVAL_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "extension" / "bilibili_eval.js"

# 滚动累积护栏：收藏页是虚拟化网格，滚过的卡片从 DOM 卸载，必须边滚边 eval、跨快照按 bvid 累积。
MAX_SCROLLS = 60          # 硬护栏，保证 limit=None（"所有"）也会终止
STALL_ROUNDS = 3          # 连续 N 次唯一计数不增长则停（单次抖动不误停）
ALL_CAP = 1000            # limit is None（"所有"）时的唯一条目上限


class BilibiliFavoritesCollector:
    def __init__(self, proxy: CDPProxy | None = None) -> None:
        self._proxy = proxy or cdp_proxy

    async def _scroll_and_collect(self, tab: CDPTab, script: str, limit: int | None) -> list[dict[str, Any]]:
        """边滚边 eval，按 bvid（无则 url）跨快照累积去重。空 title 可被非空替换。"""
        effective_cap = limit if limit is not None else ALL_CAP
        by_key: dict[str, dict[str, Any]] = {}

        def absorb(snapshot: Any) -> None:
            rows = json.loads(snapshot) if isinstance(snapshot, str) else (snapshot or [])
            for item in rows:
                key = str(item.get("bvid") or item.get("url") or "").strip()
                if not key:
                    continue
                existing = by_key.get(key)
                if existing is None:
                    by_key[key] = item
                elif not str(existing.get("title") or "").strip() and str(item.get("title") or "").strip():
                    by_key[key] = item

        # 首窗先抓一次，再进入滚动循环
        absorb(await self._proxy.eval_script(tab, script))
        stalls = 0
        for _ in range(MAX_SCROLLS):
            if len(by_key) >= effective_cap:
                break
            await self._proxy.scroll(tab)
            before = len(by_key)
            absorb(await self._proxy.eval_script(tab, script))
            if len(by_key) <= before:
                stalls += 1
                if stalls >= STALL_ROUNDS:
                    break
            else:
                stalls = 0
        return list(by_key.values())

    async def extract_favorites(self, url: str | None = None, limit: int | None = None) -> list[ConnectorItem]:
        target_url = str(url or "").strip()
        if not target_url:
            raise ValueError("B站收藏页绑定用户 space id / favlist id，请先提供真实收藏页链接。")
        await self._proxy.connect()
        tab = await self._proxy.new_tab(target_url)
        try:
            await self._proxy.wait_for_load(tab)
            script = EVAL_SCRIPT_PATH.read_text(encoding="utf-8") if EVAL_SCRIPT_PATH.exists() else self._inline_eval()
            items_data = await self._scroll_and_collect(tab, script, limit)
        finally:
            await self._proxy.close_tab(tab)

        items: list[ConnectorItem] = []
        for item in items_data[: limit or 1000]:
            href = item.get("url") or ""
            if not href:
                continue
            title = str(item.get("title") or "").strip()
            metadata = {"source": "bilibili_cdp_favorites", "bvid": item.get("bvid")}
            if item.get("publish_time"):
                metadata["publish_time"] = str(item.get("publish_time"))[:40]
            if not title:
                title = "未识别标题"
                metadata["title_missing"] = True
            items.append(ConnectorItem(
                raw_url=href,
                title=title,
                platform="bilibili",
                author=item.get("author"),
                content_type="video",
                metadata=metadata,
            ))
        return items

    @staticmethod
    def _inline_eval() -> str:
        return EVAL_SCRIPT_PATH.read_text(encoding="utf-8")


bilibili_collector = BilibiliFavoritesCollector()
