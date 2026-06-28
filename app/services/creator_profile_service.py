from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


@dataclass
class CreatorProfileResolution:
    platform: str
    input_type: str
    profile_url: str = ""
    creator_key: str = ""
    creator_name: str = ""
    status: str = "resolved"
    ambiguous_options: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "platform": self.platform,
            "input_type": self.input_type,
            "profile_url": self.profile_url,
            "creator_key": self.creator_key,
            "creator_name": self.creator_name,
            "ambiguous_options": self.ambiguous_options,
            "message": self.message,
        }


class CreatorProfileService:
    @staticmethod
    def normalize_creator_input(
        platform: str,
        value: str,
        *,
        search_results_count: int | None = None,
        resolved_profile_url: str | None = None,
        ambiguous_options: list[dict[str, Any]] | None = None,
    ) -> CreatorProfileResolution:
        return normalize_creator_input(
            platform=platform,
            value=value,
            search_results_count=search_results_count,
            resolved_profile_url=resolved_profile_url,
            ambiguous_options=ambiguous_options,
        )

    @staticmethod
    def select_creator_works(
        works: list[dict[str, Any]],
        latest_limit: int = 10,
        top_liked_limit: int = 10,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return select_creator_works(works, latest_limit=latest_limit, top_liked_limit=top_liked_limit)

    async def scan_profile(self, platform: str, profile_url: str) -> dict[str, Any]:
        normalized = normalize_creator_input(platform, profile_url)
        if normalized.input_type != "direct_link":
            return normalized.to_dict()

        works = await collect_creator_profile_works(platform, normalized.profile_url)
        selected, snapshot = select_creator_works(works, latest_limit=10, top_liked_limit=10)
        snapshot.update(
            {
                "creator_key": normalized.creator_key,
                "profile_url": normalized.profile_url,
                "captured_count": len(works),
                "scanned_at": datetime.now(timezone.utc).isoformat(),
                "selection_rule": "latest 10 + non-overlapping top-liked 10; extend top-liked when overlapping",
            }
        )
        return {
            "status": "ok",
            "creator": {
                "creator_key": normalized.creator_key,
                "creator_name": "",
                "platform": platform,
                "profile_url": normalized.profile_url,
            },
            "snapshot": snapshot,
            "items": selected,
        }


def normalize_creator_input(
    platform: str,
    value: str,
    *,
    search_results_count: int | None = None,
    resolved_profile_url: str | None = None,
    ambiguous_options: list[dict[str, Any]] | None = None,
) -> CreatorProfileResolution:
    platform = str(platform or "").strip()
    value = str(value or "").strip()
    ambiguous_options = ambiguous_options or []

    if search_results_count is not None and search_results_count != 1:
        return CreatorProfileResolution(
            platform=platform,
            input_type="ambiguous",
            status="ambiguous",
            creator_name=value,
            ambiguous_options=ambiguous_options,
            message="搜索结果不唯一，请补充博主主页链接。",
        )

    if resolved_profile_url:
        value = str(resolved_profile_url).strip()

    profile_url = _extract_profile_url(platform, value)
    if profile_url:
        profile_id = _profile_id_from_url(platform, profile_url) or profile_url
        return CreatorProfileResolution(
            platform=platform,
            input_type="direct_link",
            profile_url=profile_url,
            creator_key=f"{platform}:{profile_id}",
            creator_name="",
        )

    return CreatorProfileResolution(
        platform=platform,
        input_type="lookup_required",
        creator_key=f"{platform}:{value}" if value else "",
        creator_name=value,
        message="需要在平台内搜索博主；若结果不唯一，请补充主页链接。",
    )


async def collect_creator_profile_works(platform: str, profile_url: str) -> list[dict[str, Any]]:
    from app.connectors.cdp_proxy import cdp_proxy

    await cdp_proxy.connect()
    tab = await cdp_proxy.new_tab(profile_url)
    try:
        await cdp_proxy.wait_for_load(tab, timeout=12)
        by_url: dict[str, dict[str, Any]] = {}
        for _ in range(8):
            raw_items = await cdp_proxy.eval_script(tab, _creator_work_extract_script(platform))
            if isinstance(raw_items, str):
                try:
                    import json

                    items = json.loads(raw_items)
                except Exception:
                    items = []
            else:
                items = raw_items or []
            for item in items:
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                by_url[url] = normalize_creator_work(platform, item)
            await cdp_proxy.scroll(tab, 1400)
            await asyncio.sleep(0.4)
        if not by_url:
            raise RuntimeError("未扫描到博主作品，请确认主页链接可访问且已登录对应平台。")
        return list(by_url.values())
    finally:
        await cdp_proxy.close_tab(tab)


def select_creator_works(
    works: list[dict[str, Any]],
    latest_limit: int = 10,
    top_liked_limit: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized = [normalize_creator_work("", work) for work in works]
    latest = sorted(normalized, key=lambda item: str(item.get("published_at") or ""), reverse=True)[:latest_limit]
    latest_ids = {_work_key(item) for item in latest}
    top_liked_candidates = sorted(normalized, key=lambda item: int(item.get("like_count") or 0), reverse=True)
    top_liked: list[dict[str, Any]] = []
    skipped_overlap = 0
    for item in top_liked_candidates:
        if _work_key(item) in latest_ids:
            skipped_overlap += 1
            continue
        top_liked.append(item)
        if len(top_liked) >= top_liked_limit:
            break

    selected_by_key: dict[str, dict[str, Any]] = {}
    for item in latest:
        copy = dict(item)
        copy["bucket"] = "latest"
        selected_by_key[_work_key(copy)] = copy
    for item in top_liked:
        key = _work_key(item)
        copy = dict(item)
        if key in selected_by_key:
            selected_by_key[key]["bucket"] = "both"
        else:
            copy["bucket"] = "top_liked"
            selected_by_key[key] = copy

    snapshot = {
        "latest_limit": latest_limit,
        "top_liked_limit": top_liked_limit,
        "overlap_count": skipped_overlap,
        "top_liked_extension_count": len(top_liked),
        "selected_count": len(selected_by_key),
    }
    return list(selected_by_key.values()), snapshot


def normalize_creator_work(platform: str, item: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("url") or item.get("href") or "").strip()
    title = str(item.get("title") or item.get("desc") or url or "未命名作品").strip()
    work_id = str(item.get("id") or item.get("work_id") or _work_id_from_url(url) or url).strip()
    return {
        "id": work_id,
        "work_id": work_id,
        "title": title,
        "url": url,
        "platform": item.get("platform") or platform,
        "published_at": str(item.get("published_at") or item.get("publishTime") or ""),
        "like_count": _int_value(item.get("like_count") or item.get("likes") or item.get("likeCount")),
        "comment_count": _int_value(item.get("comment_count") or item.get("comments") or item.get("commentCount")),
        "collect_count": _int_value(item.get("collect_count") or item.get("collects") or item.get("collectCount")),
        "cover_url": str(item.get("cover_url") or item.get("cover") or ""),
        "bucket": str(item.get("bucket") or ""),
        "scan_status": str(item.get("scan_status") or "ok"),
    }


def _creator_work_extract_script(platform: str) -> str:
    host_pattern = "douyin.com" if platform == "douyin" else "xiaohongshu.com"
    path_pattern = r"/(video|note)/" if platform == "douyin" else r"/explore/|/discovery/item/"
    return f'''
(() => {{
  const hostPattern = {host_pattern!r};
  const pathPattern = new RegExp({path_pattern!r});
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const seen = new Set();
  const items = [];
  const parseCount = (text) => {{
    const raw = String(text || "").replace(/,/g, "").trim();
    const matched = raw.match(/([0-9]+(?:\\.[0-9]+)?)\\s*(万|亿|w|k)?/i);
    if (!matched) return 0;
    const base = Number(matched[1] || 0);
    const unit = (matched[2] || "").toLowerCase();
    if (unit === "亿") return Math.round(base * 100000000);
    if (unit === "万" || unit === "w") return Math.round(base * 10000);
    if (unit === "k") return Math.round(base * 1000);
    return Math.round(base);
  }};
  for (const a of anchors) {{
    let url;
    try {{ url = new URL(a.getAttribute("href"), location.href); }} catch (_) {{ continue; }}
    if (!url.hostname.includes(hostPattern)) continue;
    if (!pathPattern.test(url.pathname)) continue;
    const key = url.pathname;
    if (seen.has(key)) continue;
    seen.add(key);
    let node = a;
    let text = "";
    for (let depth = 0; depth < 4 && node; depth++) {{
      text = [text, node.innerText || ""].join("\\n").trim();
      node = node.parentElement;
    }}
    const lines = text.split("\\n").map(line => line.trim()).filter(Boolean);
    const title = lines.find(line => line.length > 4 && !/^\\d/.test(line)) || a.innerText || url.href;
    const countText = lines.join(" ");
    const img = a.querySelector("img") || a.closest("div")?.querySelector("img");
    items.push({{
      id: key,
      url: url.href,
      title: title.slice(0, 160),
      like_count: parseCount(countText),
      comment_count: 0,
      collect_count: 0,
      cover_url: img?.src || "",
      published_at: ""
    }});
  }}
  return JSON.stringify(items);
}})()
'''


def _work_key(item: dict[str, Any]) -> str:
    return str(item.get("work_id") or item.get("id") or item.get("url") or "")


def _work_id_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = [part for part in path.split("/") if part]
    return parts[-1] if parts else ""


def _int_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _extract_profile_url(platform: str, value: str) -> str:
    if platform == "douyin":
        match = re.search(r"https?://v\.douyin\.com/[^\s]+", value, re.IGNORECASE)
        if match:
            return match.group(0).strip().rstrip("，。,. ")
        match = re.search(r"https?://(?:www\.)?douyin\.com/user/[^\s?&#]+", value, re.IGNORECASE)
        if match:
            return match.group(0).strip().rstrip("，。,. ")
    if platform == "xiaohongshu":
        match = re.search(r"https?://www\.xiaohongshu\.com/user/profile/[^\s?&#]+", value, re.IGNORECASE)
        if match:
            return match.group(0).strip().rstrip("，。,. ")
    return ""


def _profile_id_from_url(platform: str, profile_url: str) -> str:
    parsed = urlparse(profile_url)
    path = parsed.path.strip("/")
    if platform == "xiaohongshu":
        match = re.search(r"user/profile/([^/]+)", path)
        return match.group(1) if match else ""
    if platform == "douyin":
        parts = [part for part in path.split("/") if part]
        return parts[-1] if parts else ""
    return ""
