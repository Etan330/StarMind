from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.connectors.base import ConnectorItem
from app.connectors.cdp_proxy import CDPProxy, CDPTab, cdp_proxy


EVAL_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "extension" / "xiaohongshu_eval.js"
NOTE_ID_RE = re.compile(r"[a-f0-9]{12,}", re.IGNORECASE)

# 滚动累积护栏：收藏页是虚拟化网格，滚过的卡片从 DOM 卸载，必须边滚边 eval、跨快照按 note_id 累积。
MAX_SCROLLS = 60          # 硬护栏，保证 limit=None（"所有"）也会终止
STALL_ROUNDS = 3          # 连续 N 次唯一计数不增长则停（单次抖动不误停）
ALL_CAP = 1000            # limit is None（"所有"）时的唯一条目上限


class XiaohongshuFavoritesCollector:
    def __init__(self, proxy: CDPProxy | None = None) -> None:
        self._proxy = proxy or cdp_proxy

    async def _scroll_and_collect(self, tab: CDPTab, script: str, limit: int | None) -> list[dict[str, Any]]:
        """边滚边 eval，按 _dedupe_key 跨快照累积（首胜，XHS JS 已选好标题）。
        按唯一 key 计数控制滚动停止，但返回时按 cap 宽切，最终精确裁剪交给 build 循环
        （某行可能过 dedup 却被 _is_noise_title 滤掉，早切会欠量）。"""
        effective_cap = limit if limit is not None else ALL_CAP
        by_key: dict[str, dict[str, Any]] = {}
        ordered: list[dict[str, Any]] = []

        def absorb(snapshot: Any) -> None:
            rows = json.loads(snapshot) if isinstance(snapshot, str) else (snapshot or [])
            for item in rows:
                href = str(item.get("url") or "").strip()
                if not href:
                    continue
                key = self._dedupe_key(item, href)
                if not key or key in by_key:
                    continue
                by_key[key] = item
                ordered.append(item)

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
        return ordered[:effective_cap]

    async def extract_favorites(self, url: str | None = None, limit: int | None = None) -> list[ConnectorItem]:
        target_url = str(url or "").strip()
        if not target_url:
            raise ValueError("小红书收藏页绑定用户 profile id，请先提供真实收藏页链接。")
        await self._proxy.connect()
        tab = await self._proxy.new_tab(target_url)
        try:
            await self._proxy.wait_for_load(tab)
            script = EVAL_SCRIPT_PATH.read_text(encoding="utf-8") if EVAL_SCRIPT_PATH.exists() else self._inline_eval()
            items_data = await self._scroll_and_collect(tab, script, limit)
        finally:
            await self._proxy.close_tab(tab)

        items: list[ConnectorItem] = []
        seen_keys: set[str] = set()
        for item in items_data:
            href = str(item.get("url") or "").strip()
            if not href:
                continue
            key = self._dedupe_key(item, href)
            if not key or key in seen_keys:
                continue
            title = str(item.get("title") or "").strip()
            if self._is_noise_title(title):
                continue
            seen_keys.add(key)
            metadata = {"source": "xiaohongshu_cdp_favorites"}
            if item.get("note_id"):
                metadata["xiaohongshu_note_id"] = item.get("note_id")
            if item.get("xsec_token"):
                metadata["xiaohongshu_xsec_token"] = item.get("xsec_token")
            if item.get("share_url"):
                metadata["xiaohongshu_share_url"] = item.get("share_url")
            if item.get("share_text"):
                metadata["xiaohongshu_share_text"] = item.get("share_text")
            if item.get("publish_time"):
                metadata["publish_time"] = str(item.get("publish_time"))[:40]
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
            if limit and len(items) >= limit:
                break
        return items

    @classmethod
    def _dedupe_key(cls, item: dict[str, Any], href: str) -> str:
        note_id = str(item.get("note_id") or "").strip()
        if note_id:
            return f"note:{note_id.lower()}"
        share_note_id = cls._note_id_from_url(str(item.get("share_url") or ""))
        if share_note_id:
            return f"note:{share_note_id.lower()}"
        href_note_id = cls._note_id_from_url(href)
        if href_note_id:
            return f"note:{href_note_id.lower()}"
        parsed = urlparse(href)
        if parsed.scheme and parsed.netloc and parsed.path:
            return f"url:{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        return ""

    @staticmethod
    def _note_id_from_url(url: str) -> str:
        try:
            parsed = urlparse(url)
        except Exception:
            return ""
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "explore" and NOTE_ID_RE.fullmatch(parts[1]):
            return parts[1]
        if len(parts) >= 3 and parts[0] == "discovery" and parts[1] == "item" and NOTE_ID_RE.fullmatch(parts[2]):
            return parts[2]
        if len(parts) >= 4 and parts[0] == "user" and parts[1] == "profile" and NOTE_ID_RE.fullmatch(parts[3]):
            return parts[3]
        return ""

    @staticmethod
    def _is_noise_title(title: str) -> bool:
        value = re.sub(r"\s+", " ", str(title or "")).strip()
        if not value:
            return False
        if len(value) < 2:
            return True
        if re.fullmatch(r"\[[^\]]{0,3}", value):
            return True
        if re.fullmatch(r"(我|我的|首页|发现|消息|通知|登录|注册|搜索|发布|购物|直播|更多|展开|收起|打开|关闭|关注|已关注|小红书|xiaohongshu)", value, re.IGNORECASE):
            return True
        return False

    @staticmethod
    def _inline_eval() -> str:
        return EVAL_SCRIPT_PATH.read_text(encoding="utf-8")


xiaohongshu_collector = XiaohongshuFavoritesCollector()
