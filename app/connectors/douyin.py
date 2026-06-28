from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.connectors.base import ConnectorItem


class BrowserDependencyMissing(RuntimeError):
    pass


class DouyinPageNotReady(RuntimeError):
    pass


@dataclass
class BrowserState:
    opened: bool
    current_url: str | None = None
    message: str = ""


# Web Access CDP Proxy on port 3456
CDP_PROXY = "http://localhost:3456"

# 滚动累积护栏：收藏页是虚拟化网格，滚过的卡片从 DOM 卸载，必须边滚边 eval、跨快照按 url.pathname 累积。
MAX_SCROLLS = 60          # 硬护栏，保证 limit=None（"所有"）也会终止
STALL_ROUNDS = 3          # 连续 N 次唯一计数不增长则停（单次抖动不误停）
ALL_CAP = 1000            # limit is None（"所有"）时的唯一条目上限


class DouyinBrowserCollector:
    """Uses Web Access CDP Proxy (port 3456) to connect to user's existing Chrome.
    No new window. Opens a background tab, extracts data, closes the tab.
    """

    def __init__(self) -> None:
        self._target_id: str | None = None
        self._last_diagnostics: dict[str, Any] = {}

    async def _request(self, method: str, path: str, body: str | None = None) -> Any:
        async with httpx.AsyncClient(timeout=15) as client:
            if method == "GET":
                r = await client.get(f"{CDP_PROXY}{path}")
            else:
                r = await client.post(f"{CDP_PROXY}{path}", content=body)
        if r.status_code != 200:
            raise BrowserDependencyMissing(f"CDP Proxy error: {r.text[:200]}")
        return r.json()

    async def _check_proxy(self) -> None:
        try:
            data = await self._request("GET", "/health")
            if not data.get("connected"):
                raise BrowserDependencyMissing(
                    "CDP Proxy 未连接浏览器。请运行: node ~/.claude/skills/web-access/scripts/check-deps.mjs"
                )
        except httpx.ConnectError:
            raise BrowserDependencyMissing(
                "CDP Proxy 未运行。请运行: node ~/.claude/skills/web-access/scripts/check-deps.mjs"
            )

    async def open(self, url: str = "https://www.douyin.com/user/self?showTab=favorite_collection") -> BrowserState:
        await self._check_proxy()
        login_url = "https://www.douyin.com/user/self"
        data = await self._request("POST", "/new", login_url)
        self._target_id = data["targetId"]
        await asyncio.sleep(2)
        if url != login_url:
            await self._request("POST", f"/navigate?target={self._target_id}", url)
            await asyncio.sleep(2)
        return BrowserState(opened=True, current_url=url, message="已在浏览器后台打开抖音我的页，并尝试进入收藏页。")

    async def _scroll_and_collect(self, target: str, script: str, limit: int | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """边滚边 eval，按 url.pathname（复刻 JS 的 dedup key）跨快照累积去重。
        同时保留最后一次快照的 meta（count 外的字段），供登录/收藏页 guard 判定。"""
        effective_cap = limit if limit is not None else ALL_CAP
        by_key: dict[str, dict[str, Any]] = {}
        last_meta: dict[str, Any] = {}

        async def snapshot() -> None:
            nonlocal last_meta
            raw = await self._request("POST", f"/eval?target={target}", script)
            result = json.loads(raw.get("value", "{}")) if isinstance(raw.get("value"), str) else {}
            last_meta = {k: v for k, v in result.items() if k != "items"}
            for item in result.get("items", []):
                href = str(item.get("href") or "").strip()
                if not href:
                    continue
                try:
                    key = urlparse(href).path or href
                except Exception:
                    key = href
                if key in by_key:
                    continue
                by_key[key] = item

        # 首窗先抓一次，再进入滚动循环
        await snapshot()
        stalls = 0
        for _ in range(MAX_SCROLLS):
            if len(by_key) >= effective_cap:
                break
            await self._request("GET", f"/scroll?target={target}&y=1500")
            await asyncio.sleep(0.8)
            before = len(by_key)
            await snapshot()
            if len(by_key) <= before:
                stalls += 1
                if stalls >= STALL_ROUNDS:
                    break
            else:
                stalls = 0
        return list(by_key.values()), last_meta

    async def extract_visible_video_links(self, limit: int | None = 10, require_collection_page: bool = True) -> list[ConnectorItem]:
        if self._target_id is None:
            await self.open()

        target = self._target_id
        effective_limit = limit if limit is not None else ALL_CAP

        # Extract links via eval
        script = '''
(() => {
  const validUrl = (raw) => {
    try {
      const url = new URL(raw, location.href);
      if (!/(^|\\.)douyin\\.com$|(^|\\.)iesdouyin\\.com$/.test(url.hostname)) return null;
      if (!/(\\/video\\/|\\/note\\/|\\/share\\/video\\/|\\/share\\/note\\/)/.test(url.pathname)) return null;
      return url;
    } catch (_) { return null; }
  };
  const badLine = /^(\\d[\\d.]*\\s*(万|亿|w|k)?|首页|推荐|关注|朋友|我的|登录|注册|搜索|收藏)$/i;
  // 尽力从卡片附近抓发布时间：先 <time> 的 datetime/innerText，再正则匹配常见文案；抓不到留空。
  const datePattern = /(\\d{4}[-/年.]\\d{1,2}[-/月.]\\d{1,2}|\\d{1,2}[-/月.]\\d{1,2}日?|\\d+\\s*(天|小时|分钟|周|个月|月|年)前|昨天|前天|今天)/;
  const findPublishTime = (anchor) => {
    let node = anchor;
    for (let depth = 0; depth < 4 && node; depth++) {
      const timeEl = node.querySelector ? node.querySelector("time") : null;
      if (timeEl) {
        const dt = (timeEl.getAttribute("datetime") || timeEl.innerText || "").trim();
        if (dt) return dt.slice(0, 40);
      }
      const text = (node.innerText || "").trim();
      const matched = text.match(datePattern);
      if (matched) return matched[0].slice(0, 40);
      node = node.parentElement;
    }
    return "";
  };
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const seen = new Set();
  const items = [];
  for (const a of anchors) {
    const url = validUrl(a.getAttribute("href"));
    if (!url) continue;
    const key = url.pathname;
    if (seen.has(key)) continue;
    seen.add(key);
    const text = (a.innerText || "").trim();
    const lines = text.split("\\n").map(l => l.trim()).filter(l => l.length >= 4 && !badLine.test(l));
    const title = lines[0] || "";
    const kind = /\\/note\\//.test(url.pathname) ? "note" : "video";
    if (title) items.push({href: url.href, title: title.slice(0, 180), kind, publishTime: findPublishTime(a)});
  }
  const bodyText = (document.body?.innerText || "").slice(0, 3000);
  const looksLikeCollectionPage = /收藏|喜欢|favorite|collection/i.test(bodyText) || /user\\/self/.test(location.href);
  const isLoggedIn = !/登录.*注册/.test(bodyText.slice(0, 300));
  return JSON.stringify({count: items.length, items: items, looksLikeCollectionPage, isLoggedIn, url: location.href});
})()
'''
        items, result = await self._scroll_and_collect(target, script, limit)

        self._last_diagnostics = dict(result)

        if not result.get("isLoggedIn", True):
            await self._close_tab()
            raise DouyinPageNotReady("抖音未登录。请在浏览器中登录抖音后重试。")

        if require_collection_page and not result.get("looksLikeCollectionPage"):
            await self._close_tab()
            raise DouyinPageNotReady("当前页面不像收藏夹页面。请确认浏览器已登录并进入收藏页。")

        if not items:
            await self._close_tab()
            raise DouyinPageNotReady("未识别到收藏内容。请确认浏览器已登录并停留在收藏页面。")

        # Close tab after extraction
        await self._close_tab()

        return [
            ConnectorItem(
                raw_url=item["href"],
                title=item.get("title") or item["href"],
                platform="douyin",
                content_type=item.get("kind") or "video",
                metadata={"source": "douyin_web_access_cdp", "page_text": "", "publish_time": item.get("publishTime") or ""},
            )
            for item in items[:effective_limit]
        ]

    async def _close_tab(self) -> None:
        if self._target_id:
            try:
                await self._request("GET", f"/close?target={self._target_id}")
            except Exception:
                pass
            self._target_id = None

    def diagnostics(self) -> dict[str, Any]:
        return dict(self._last_diagnostics)

    async def export_cookies(self, output_path: Path | None = None) -> Path:
        """Export cookies via CDP proxy eval."""
        from app.config import BROWSER_DATA_DIR
        if self._target_id is None:
            await self.open("https://www.douyin.com")
        raw = await self._request("POST", f"/eval?target={self._target_id}", "document.cookie")
        cookie_str = raw.get("value", "") if isinstance(raw.get("value"), str) else ""
        output_path = output_path or BROWSER_DATA_DIR / "douyin_cookies.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(cookie_str, encoding="utf-8")
        await self._close_tab()
        return output_path

    async def close(self) -> None:
        await self._close_tab()


douyin_browser_collector = DouyinBrowserCollector()
