from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


PROFILE_HOST_HINTS = (
    "space.bilibili.com",
    "bilibili.com/space",
    "youtube.com/@",
    "youtube.com/channel",
    "x.com/",
    "twitter.com/",
    "douyin.com/user",
    "xiaohongshu.com/user",
    "weibo.com/",
)


V3_ENTRY_MODES: dict[str, dict[str, str]] = {
    "favorites": {
        "mode": "favorites",
        "label": "同步收藏夹",
        "short_label": "收藏夹",
        "description": "从已有收藏里筛出值得沉淀的内容",
        "placeholder": "可以先不输入，直接确认本地可见收藏夹同步",
        "input_hint": "读取你打开的本地浏览器可见收藏，不绕过登录或平台限制。",
        "output_hint": "得到一组待确认的资料候选。",
    },
    "link": {
        "mode": "link",
        "label": "导入链接",
        "short_label": "链接",
        "description": "粘贴文章、视频或网页生成有来源摘要",
        "placeholder": "粘贴一篇文章、视频或网页链接...",
        "input_hint": "适合单条文章、视频、帖子或网页。",
        "output_hint": "得到摘要、关键观点、来源证据和可追问问题。",
    },
    "creator": {
        "mode": "creator",
        "label": "蒸馏博主",
        "short_label": "博主",
        "description": "输入主页或账号，提炼主题和观点框架",
        "placeholder": "粘贴博主主页，或输入账号名称...",
        "input_hint": "当前是实验能力，处理范围取决于可公开访问内容。",
        "output_hint": "得到主题地图、关键观点和代表性来源。",
    },
    "idea": {
        "mode": "idea",
        "label": "记录 Idea",
        "short_label": "Idea",
        "description": "把临时想法整理成笔记、SOP 或待办",
        "placeholder": "写下一个想法、问题、灵感或待整理材料...",
        "input_hint": "适合草稿、灵感、会议后想法和待整理文本。",
        "output_hint": "得到结构化草稿、行动清单和开放问题。",
    },
}


@dataclass(frozen=True)
class V3InputRoute:
    content: str
    entry_mode: str
    mode: str
    input_type: str
    title: str
    length_bucket: str
    is_empty: bool
    placeholder: str
    input_hint: str
    output_hint: str


def length_bucket(content: str) -> str:
    length = len(content.strip())
    if length == 0:
        return "empty"
    if length < 80:
        return "short"
    if length < 600:
        return "medium"
    return "long"


def looks_like_url(content: str) -> bool:
    value = content.strip()
    if value.startswith("www."):
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def looks_like_profile(content: str) -> bool:
    value = content.strip().lower()
    if value.startswith("@") and len(value) > 1:
        return True
    return any(hint in value for hint in PROFILE_HOST_HINTS)


def normalize_entry_mode(entry_mode: str | None) -> str:
    value = (entry_mode or "").strip().lower()
    aliases = {
        "bookmark": "favorites",
        "bookmarks": "favorites",
        "sync": "favorites",
        "profile": "creator",
        "creator_distill": "creator",
        "distill": "creator",
        "text": "idea",
        "note": "idea",
    }
    value = aliases.get(value, value)
    return value if value in V3_ENTRY_MODES else "link"


def classify_v3_input(content: str | None, entry_mode: str | None = None) -> V3InputRoute:
    clean_content = (content or "").strip()
    selected_mode = normalize_entry_mode(entry_mode)

    if selected_mode == "favorites":
        input_type = "favorites"
        mode = "favorites"
        title = "同步可见收藏夹"
    elif not clean_content:
        input_type = "empty"
        mode = selected_mode
        title = "等待输入"
    elif selected_mode == "creator" or looks_like_profile(clean_content):
        input_type = "profile"
        mode = "creator"
        title = clean_content[:80]
    elif selected_mode == "link" or looks_like_url(clean_content):
        input_type = "link" if looks_like_url(clean_content) else "text"
        mode = "link" if looks_like_url(clean_content) else "idea"
        title = clean_content[:80]
    else:
        input_type = "idea"
        mode = "idea"
        title = clean_content[:80]

    config = V3_ENTRY_MODES[mode]
    return V3InputRoute(
        content=clean_content,
        entry_mode=selected_mode,
        mode=mode,
        input_type=input_type,
        title=title or config["label"],
        length_bucket=length_bucket(clean_content),
        is_empty=not clean_content and mode != "favorites",
        placeholder=config["placeholder"],
        input_hint=config["input_hint"],
        output_hint=config["output_hint"],
    )

