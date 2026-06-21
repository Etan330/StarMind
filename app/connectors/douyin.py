from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import BROWSER_DATA_DIR
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


class DouyinBrowserCollector:
    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None
        self._last_diagnostics: dict[str, Any] = {}

    async def open(self, url: str = "https://www.douyin.com/user/self?showTab=favorite_collection") -> BrowserState:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise BrowserDependencyMissing("Playwright 未安装，请先安装 requirements.txt 里的依赖。") from exc

        if self._context is None:
            BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(BROWSER_DATA_DIR / "douyin"),
                    channel="chrome",
                    headless=False,
                    viewport={"width": 1360, "height": 900},
                )
            except Exception:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(BROWSER_DATA_DIR / "douyin"),
                    headless=False,
                    viewport={"width": 1360, "height": 900},
                )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto(url, wait_until="domcontentloaded")
        return BrowserState(opened=True, current_url=self._page.url, message="浏览器已打开，请登录抖音并进入收藏页。")

    async def extract_visible_video_links(self, limit: int | None = 10, require_collection_page: bool = True) -> list[ConnectorItem]:
        if self._page is None:
            await self.open()
        assert self._page is not None
        target_count = limit or 1000
        stable_rounds = 0
        last_count = 0
        for _ in range(12):
            await self._page.wait_for_timeout(450)
            count = await self._page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a[href]')).filter((anchor) => {
                  try {
                    const url = new URL(anchor.getAttribute('href'), location.href);
                    return /(^|\\.)douyin\\.com$|(^|\\.)iesdouyin\\.com$/.test(url.hostname)
                      && /(\\/video\\/|\\/note\\/|\\/share\\/video\\/|\\/share\\/note\\/)/.test(url.pathname);
                  } catch (_error) {
                    return false;
                  }
                }).length
                """
            )
            if count >= target_count:
                break
            if count <= last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_count = count
            if stable_rounds >= 3:
                break
            await self._page.evaluate("() => window.scrollBy({ top: Math.floor(window.innerHeight * 0.82), behavior: 'smooth' })")
        payload: dict[str, Any] = await self._page.evaluate(
            """
            (limit) => {
              const bodyText = (document.body?.innerText || '').slice(0, 5000);
              const readySignals = [
                /收藏/.test(bodyText),
                /喜欢/.test(bodyText),
                /favorite|collection|like/i.test(location.href),
                /user\\/self/.test(location.href),
              ];
              const looksLikeCollectionPage = readySignals.some(Boolean);
              const badLine = /^(首页|推荐|关注|朋友|我的|登录|注册|消息|搜索|热门|游戏解说|游戏视频推荐|点赞|评论|分享|收藏)$/;
              const countLine = /^\\d+(\\.\\d+)?\\s*(万|亿|w|W|k|K)?$/;
              const cjkOrWord = /[\\u4e00-\\u9fa5A-Za-z0-9]/;

              const cleanLines = (text) => String(text || '')
                .replace(/\\u200b/g, '')
                .split(/\\n|\\r|\\t| {2,}/)
                .map((line) => line.trim())
                .filter(Boolean);

              const validUrl = (raw) => {
                try {
                  const url = new URL(raw, location.href);
                  if (!/(^|\\.)douyin\\.com$|(^|\\.)iesdouyin\\.com$/.test(url.hostname)) return null;
                  if (!/(\\/video\\/|\\/note\\/|\\/share\\/video\\/|\\/share\\/note\\/)/.test(url.pathname)) return null;
                  if (/Baiduspider/i.test(url.search)) return null;
                  return url;
                } catch (_error) {
                  return null;
                }
              };

              const bestContainer = (anchor) => {
                let node = anchor;
                let best = anchor;
                while (node && node !== document.body) {
                  const text = (node.innerText || '').trim();
                  if (text && text.length <= 700) best = node;
                  if (node.matches?.('li, article, [data-e2e], [class*="card"], [class*="Card"], [class*="video"], [class*="Video"], [class*="item"], [class*="Item"]')) {
                    if (text && text.length <= 700) return node;
                  }
                  node = node.parentElement;
                }
                return best;
              };

              const pickTitle = (anchor, container) => {
                const rawCandidates = [
                  anchor.getAttribute('title'),
                  anchor.getAttribute('aria-label'),
                  anchor.innerText,
                  container?.innerText,
                ];
                const lines = rawCandidates.flatMap(cleanLines);
                const candidates = lines
                  .map((line) => line.replace(/^#\\s*/, '').trim())
                  .filter((line) => line.length >= 4 && line.length <= 260)
                  .filter((line) => cjkOrWord.test(line))
                  .filter((line) => !countLine.test(line))
                  .filter((line) => !badLine.test(line))
                  .filter((line) => !/^热门[:：]/.test(line))
                  .filter((line) => !/^https?:\\/\\//.test(line));
                candidates.sort((a, b) => {
                  const score = (line) => line.length + (/[\\u4e00-\\u9fa5]/.test(line) ? 20 : 0) + (/#/.test(line) ? 4 : 0);
                  return score(b) - score(a);
                });
                return (candidates[0] || '').slice(0, 180);
              };

              const anchors = Array.from(document.querySelectorAll('a[href]'));
              const items = anchors.map((anchor) => {
                const url = validUrl(anchor.getAttribute('href'));
                if (!url) return null;
                const container = bestContainer(anchor);
                const pageText = cleanLines(container?.innerText || anchor.innerText).join('\\n').slice(0, 900);
                const title = pickTitle(anchor, container);
                const kind = /\\/note\\//.test(url.pathname) ? 'note' : 'video';
                return { href: url.href, title, pageText, kind };
              }).filter(Boolean).filter((item) => item.title);
              const seen = new Set();
              const uniqueItems = items.filter((item) => {
                const key = item.href.replace(/[?#].*$/, '');
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
              }).slice(0, limit || 1000);
              return {
                currentUrl: location.href,
                title: document.title,
                looksLikeCollectionPage,
                rawAnchorCount: anchors.length,
                extractedCount: uniqueItems.length,
                items: uniqueItems,
              };
            }
            """,
            limit,
        )
        self._last_diagnostics = {key: value for key, value in payload.items() if key != "items"}
        items = payload.get("items", [])
        if require_collection_page and not payload.get("looksLikeCollectionPage"):
            raise DouyinPageNotReady("当前页面不像收藏夹页面。请先在打开的抖音窗口进入收藏/喜欢列表，再提取链接。")
        if not items:
            raise DouyinPageNotReady("当前抖音页面没有识别到可导入的视频卡片。请确认浏览器已经登录，并停留在收藏/喜欢列表页面。")
        return [
            ConnectorItem(
                raw_url=item["href"],
                title=item.get("title") or item["href"],
                platform="douyin",
                content_type=item.get("kind") or "video",
                metadata={
                    "source": "douyin_browser_visible_favorites",
                    "page_text": item.get("pageText", ""),
                    "douyin_page_url": payload.get("currentUrl"),
                    "extractor": "visible_collection_cards_v2",
                },
            )
            for item in items
        ]

    def diagnostics(self) -> dict[str, Any]:
        return dict(self._last_diagnostics)

    async def export_cookies(self, output_path: Path | None = None) -> Path:
        if self._context is None:
            await self.open()
        assert self._context is not None
        cookies = await self._context.cookies(["https://www.douyin.com", "https://douyin.com"])
        output_path = output_path or BROWSER_DATA_DIR / "douyin_cookies.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Netscape HTTP Cookie File", "# Generated from StarMind Douyin browser session"]
        for cookie in cookies:
            domain = str(cookie.get("domain") or "")
            if "douyin.com" not in domain:
                continue
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            path = str(cookie.get("path") or "/")
            secure = "TRUE" if cookie.get("secure") else "FALSE"
            expires = int(cookie.get("expires") or 0)
            if expires < 0:
                expires = 0
            name = str(cookie.get("name") or "").replace("\t", " ").replace("\n", " ")
            value = str(cookie.get("value") or "").replace("\t", " ").replace("\n", " ")
            if name:
                lines.append("\t".join([domain, include_subdomains, path, secure, str(expires), name, value]))
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._context = None
        self._playwright = None
        self._page = None


douyin_browser_collector = DouyinBrowserCollector()
