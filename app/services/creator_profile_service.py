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


CreatorInputResult = CreatorProfileResolution


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

        collected = await collect_creator_profile_works(platform, normalized.profile_url)
        if isinstance(collected, dict):
            works = collected.get("works") or []
            creator_name = str(collected.get("creator_name") or "").strip()
            follower_count = _int_value(collected.get("follower_count"))
            liked_count = _int_value(collected.get("liked_count"))
            profile_id = str(collected.get("profile_id") or "").strip()
        else:
            works = collected
            creator_name = ""
            follower_count = 0
            liked_count = 0
            profile_id = ""
        selected, snapshot = select_creator_works(works, latest_limit=10, top_liked_limit=10)
        snapshot.update(
            {
                "creator_key": normalized.creator_key,
                "profile_url": normalized.profile_url,
                "captured_count": len(works),
                "scanned_at": datetime.now(timezone.utc).isoformat(),
                "selection_rule": "top-liked 10 sorted by likes desc + latest 10; overlapping works marked both",
            }
        )
        return {
            "status": "ok",
            "creator": {
                "creator_key": normalized.creator_key,
                "creator_name": creator_name,
                "creator_profile_id": profile_id,
                "follower_count": follower_count,
                "liked_count": liked_count,
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

    if platform not in {"douyin", "xiaohongshu"}:
        lowered = value.lower()
        if "douyin.com" in lowered:
            platform = "douyin"
        elif "xiaohongshu.com" in lowered:
            platform = "xiaohongshu"

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


async def collect_creator_profile_works(platform: str, profile_url: str) -> dict[str, Any]:
    from app.connectors.cdp_proxy import cdp_proxy

    await cdp_proxy.connect()
    tab = await cdp_proxy.new_tab(profile_url)
    try:
        await cdp_proxy.wait_for_load(tab, timeout=12)
        profile_info = await _wait_for_creator_profile_info(tab, platform)
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
        if not profile_info.get("creator_name") and not profile_info.get("follower_count") and not profile_info.get("liked_count"):
            profile_info = await _wait_for_creator_profile_info(tab, platform)
        profile_info["works"] = list(by_url.values())
        return profile_info
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
    top_liked = sorted(normalized, key=lambda item: int(item.get("like_count") or 0), reverse=True)[:top_liked_limit]
    top_liked_ids = {_work_key(item) for item in top_liked}
    skipped_overlap = len(latest_ids & top_liked_ids)

    selected_by_key: dict[str, dict[str, Any]] = {}
    for item in top_liked:
        copy = dict(item)
        copy["bucket"] = "top_liked"
        selected_by_key[_work_key(copy)] = copy
    for item in latest:
        key = _work_key(item)
        copy = dict(item)
        if key in selected_by_key:
            selected_by_key[key]["bucket"] = "both"
        else:
            copy["bucket"] = "latest"
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
        "share_count": _int_value(item.get("share_count") or item.get("shares") or item.get("shareCount")),
        "cover_url": str(item.get("cover_url") or item.get("cover") or ""),
        "bucket": str(item.get("bucket") or ""),
        "scan_status": str(item.get("scan_status") or "ok"),
    }


def normalize_creator_profile_info(info: dict[str, Any]) -> dict[str, Any]:
    platform = str(info.get("platform") or "").strip()
    body_text = str(info.get("body_text") or "")
    name = _clean_creator_name(
        str(info.get("creator_name") or info.get("name") or info.get("nickname") or "").strip()
    )
    if not name:
        name = _extract_creator_name_from_body(body_text)
    follower_text = str(info.get("follower_text") or body_text)
    liked_text = str(info.get("liked_text") or body_text)
    follower_count = _count_value(info.get("follower_count"))
    if not follower_count:
        follower_count = _extract_labeled_count(follower_text, "粉丝", prefer="before" if platform == "xiaohongshu" else "after")
    liked_count = _count_value(info.get("liked_count"))
    if not liked_count:
        liked_count = _extract_labeled_count(liked_text, "获赞与收藏") or _extract_labeled_count(liked_text, "获赞")
    return {
        "creator_name": name,
        "follower_count": follower_count,
        "liked_count": liked_count,
        "profile_id": str(info.get("profile_id") or "").strip(),
    }


def _clean_creator_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    cleaned = re.split(r"\s*[-_|｜]\s*", cleaned, maxsplit=1)[0].strip()
    cleaned = re.sub(r"(的)?(抖音|小红书)?主页$", "", cleaned).strip()
    if cleaned in {"", "抖音", "小红书", "用户主页", "主页"}:
        return ""
    return cleaned[:80]


def _extract_creator_name_from_body(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    labels = {"关注", "粉丝", "获赞", "获赞与收藏", "作品", "喜欢", "收藏", "笔记", "私信", "分享主页"}
    for index, line in enumerate(lines):
        if re.match(r"^(抖音号|小红书号|IP属地)[：:]", line):
            break
        if line in labels or _count_value(line):
            continue
        if len(line) > 40:
            continue
        next_window = "\n".join(lines[index + 1 : index + 8])
        if "粉丝" in next_window or "获赞" in next_window:
            return _clean_creator_name(line)
    return ""


def _count_value(value: Any) -> int:
    text = str(value or "").replace(",", "").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(万|亿|w|k)?", text, re.IGNORECASE)
    if not match:
        return 0
    base = float(match.group(1) or 0)
    unit = (match.group(2) or "").lower()
    if unit == "亿":
        return round(base * 100000000)
    if unit in {"万", "w"}:
        return round(base * 10000)
    if unit == "k":
        return round(base * 1000)
    return round(base)


def _extract_labeled_count(text: str, label: str, prefer: str = "after") -> int:
    compact = re.sub(r"[：: ]+", " ", text or "")
    tokens = re.findall(r"关注|粉丝|获赞与收藏|获赞|[0-9][0-9,.]*(?:\.[0-9]+)?\s*(?:万|亿|w|k)?", compact, re.IGNORECASE)
    values: list[tuple[str, int]] = []
    for index, token in enumerate(tokens):
        if token != label:
            continue
        before = _count_value(tokens[index - 1]) if index > 0 else 0
        after = _count_value(tokens[index + 1]) if index + 1 < len(tokens) else 0
        previous_label = tokens[index - 2] if index > 1 else ""
        next_label = tokens[index + 2] if index + 2 < len(tokens) else ""
        if before and previous_label in {"关注", "粉丝", "获赞", "获赞与收藏"} and previous_label != label:
            before = 0
        if before:
            values.append(("before", before))
        if after:
            values.append(("after", after))
    for position in ([prefer, "after", "before"] if prefer == "after" else [prefer, "before", "after"]):
        for candidate_position, value in values:
            if candidate_position == position:
                return value
    return 0


def _creator_work_extract_script(platform: str) -> str:
    host_pattern = "douyin.com" if platform == "douyin" else "xiaohongshu.com"
    path_pattern = r"/(video|note)/" if platform == "douyin" else r"/user/profile/[^/]+/[0-9a-fA-F]+|/explore/[0-9a-fA-F]+|/discovery/item/[0-9a-fA-F]+"
    return f'''
(() => {{
  const platform = {platform!r};
  const hostPattern = {host_pattern!r};
  const pathPattern = new RegExp({path_pattern!r});
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const seen = new Set();
  const items = [];
  const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
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
  const normalizeXhsUrl = (anchor) => {{
    const card = anchor.closest("section.note-item") || anchor.closest(".note-item") || anchor.closest("section") || anchor.closest("div");
    const cardAnchors = Array.from(card?.querySelectorAll('a[href]') || [anchor]);
    const candidates = cardAnchors.map((link) => {{
      try {{ return new URL(link.getAttribute('href'), location.href); }} catch (_) {{ return null; }}
    }}).filter(Boolean).filter((url) => url.hostname.includes(hostPattern));
    return candidates.find((url) => /\/user\/profile\/[^/]+\/[0-9a-fA-F]+/.test(url.pathname))
      || candidates.find((url) => /\/discovery\/item\/[0-9a-fA-F]+/.test(url.pathname))
      || candidates.find((url) => /\/explore\/[0-9a-fA-F]+/.test(url.pathname));
  }};
  const canonicalKey = (url) => {{
    const matched = url.pathname.match(/([0-9a-fA-F]{{16,}})$/);
    return matched ? matched[1] : url.pathname;
  }};
  for (const a of anchors) {{
    let url;
    try {{ url = new URL(a.getAttribute("href"), location.href); }} catch (_) {{ continue; }}
    if (!url.hostname.includes(hostPattern)) continue;
    if (platform === "xiaohongshu") url = normalizeXhsUrl(a) || url;
    if (!pathPattern.test(url.pathname)) continue;
    if (platform === "xiaohongshu" && !/\/user\/profile\/[^/]+\/[0-9a-fA-F]+/.test(url.pathname)) continue;
    const key = canonicalKey(url);
    if (seen.has(key)) continue;
    seen.add(key);
    const card = a.closest("section.note-item") || a.closest(".note-item") || a.closest("section") || a.closest("div");
    let node = card || a;
    let text = "";
    for (let depth = 0; depth < 4 && node; depth++) {{
      text = [text, node.innerText || ""].join("\\n").trim();
      node = node.parentElement;
    }}
    const lines = text.split("\\n").map(line => line.trim()).filter(Boolean);
    const titleAnchor = Array.from(card?.querySelectorAll('a[href]') || []).find(link => clean(link.innerText).length > 4);
    const title = clean(titleAnchor?.innerText) || lines.find(line => line.length > 4 && !/^\\d/.test(line) && line !== "置顶") || clean(a.innerText) || url.href;
    const countText = lines.join(" ");
    const img = card?.querySelector("img") || a.querySelector("img") || a.closest("div")?.querySelector("img");
    items.push({{
      id: key,
      work_id: key,
      url: url.href,
      title: title.slice(0, 160),
      like_count: parseCount(countText),
      comment_count: 0,
      collect_count: 0,
      share_count: 0,
      cover_url: img?.src || "",
      published_at: ""
    }});
  }}
  return JSON.stringify(items);
}})()
'''


async def _wait_for_creator_profile_info(tab: Any, platform: str) -> dict[str, Any]:
    profile_info = {"creator_name": "", "follower_count": 0, "liked_count": 0, "profile_id": ""}
    for attempt in range(8):
        profile_info = await _extract_creator_profile_info(tab, platform)
        if profile_info.get("creator_name") and (profile_info.get("follower_count") or profile_info.get("liked_count")):
            return profile_info
        await asyncio.sleep(0.5)
    return profile_info


async def _extract_creator_profile_info(tab: Any, platform: str) -> dict[str, Any]:
    from app.connectors.cdp_proxy import cdp_proxy

    script = r'''
(() => {
  const text = document.body?.innerText || "";
  const title = document.title || "";
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const one = (selector, root = document) => root.querySelector(selector);
  const textOf = (selector, root = document) => clean(one(selector, root)?.innerText || one(selector, root)?.textContent || "");
  const parseCount = (value) => {
    const matched = clean(value).replace(/,/g, "").match(/([0-9]+(?:\.[0-9]+)?)\s*(万|亿|w|k)?/i);
    if (!matched) return 0;
    const base = Number(matched[1] || 0);
    const unit = (matched[2] || "").toLowerCase();
    if (unit === "亿") return Math.round(base * 100000000);
    if (unit === "万" || unit === "w") return Math.round(base * 10000);
    if (unit === "k") return Math.round(base * 1000);
    return Math.round(base);
  };
  const douyinRoot = one('[data-e2e="user-info"]');
  const douyinName = textOf('h1', douyinRoot || document);
  const douyinFans = textOf('[data-e2e="user-info-fans"]');
  const douyinLike = textOf('[data-e2e="user-info-like"]');
  const xhsName = textOf('.user-name') || textOf('.user-nickname');
  let xhsFans = 0;
  let xhsLiked = 0;
  const xhsDataTexts = [];
  for (const block of Array.from(document.querySelectorAll('div'))) {
    const label = textOf('.shows', block);
    const count = textOf('.count', block);
    if (!label || !count) continue;
    xhsDataTexts.push(`${count} ${label}`);
    if (label === '粉丝') xhsFans = parseCount(count);
    if (label === '获赞与收藏') xhsLiked = parseCount(count);
  }
  const selectors = [
    'h1',
    '[data-e2e="user-title"]',
    '[data-e2e="user-name"]',
    '.user-nickname',
    '.user-name',
    '[class*="user-name"]',
    '[class*="nickname"]'
  ];
  const nameCandidates = [douyinName, xhsName]
    .concat(selectors.map(selector => textOf(selector)))
    .concat([title])
    .map(clean)
    .filter(value => value && !/^(获赞|关注|粉丝|IP属地|私信|关注)$/i.test(value));
  const lines = text.split(/\n+/).map(clean).filter(Boolean);
  const neighborText = (pattern) => {
    const index = lines.findIndex(line => pattern.test(line));
    return index >= 0
      ? lines.slice(Math.max(0, index - 1), index + 3).join(" ")
      : lines.find(line => pattern.test(line)) || "";
  };
  const xhsBasic = textOf('.user-basic') || textOf('.basic-info');
  const xhsData = xhsDataTexts.join(' ') || textOf('.data-info') || textOf('.user-interactions');
  return JSON.stringify({
    creator_name: nameCandidates[0] || "",
    follower_count: xhsFans || 0,
    liked_count: xhsLiked || 0,
    follower_text: douyinFans || xhsData || neighborText(/粉丝|followers/i),
    liked_text: douyinLike || xhsData || neighborText(/获赞|获赞与收藏/i),
    body_text: [xhsBasic, xhsData, text].filter(Boolean).join("\n")
  });
})()
'''
    raw = await cdp_proxy.eval_script(tab, script)
    if isinstance(raw, str):
        try:
            import json

            parsed = json.loads(raw)
        except Exception:
            parsed = {}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        parsed = {}
    parsed["platform"] = platform
    return normalize_creator_profile_info(parsed)


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
