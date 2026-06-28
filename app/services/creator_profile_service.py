"""
Creator Profile Input Normalization Service.

Normalizes various creator input formats:
- Douyin share text with short URLs (v.douyin.com)
- Xiaohongshu profile URLs
- Account IDs / nicknames (requiring lookup)
- Ambiguous search results handling

Key principles (from plan):
- When searching by ID/nickname, if results are not unique, do NOT auto-select.
  Require the user to provide the profile URL.
- Internal unique key uses (platform + profile_id), not nickname.
- Nickname is only used for display and grouping titles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class CreatorInputResult:
    """Result of normalizing creator input."""

    platform: str
    input_type: str  # direct_link | lookup_required | ambiguous
    profile_url: Optional[str] = None
    message: Optional[str] = None
    raw_value: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "platform": self.platform,
            "input_type": self.input_type,
            "profile_url": self.profile_url,
            "message": self.message,
        }


# Platform-specific URL patterns
PLATFORM_PATTERNS = {
    "douyin": {
        "short_url": re.compile(r"https?://v\.douyin\.com/[a-zA-Z0-9_-]+"),
        "profile_url": re.compile(r"https?://(?:www\.)?douyin\.com/user/([A-Za-z0-9_-]+)"),
    },
    "xiaohongshu": {
        "profile_url": re.compile(r"https?://(?:www\.)?xiaohongshu\.com/user/profile/([A-Za-z0-9_-]+)"),
        "note_url": re.compile(r"https?://(?:www\.)?xiaohongshu\.com/(?:discovery/item|explore)/([A-Za-z0-9_-]+)"),
    },
}


def extract_url_from_text(text: str) -> str | None:
    """Extract the first URL from text (for share text scenarios)."""
    if not text:
        return None
    # Use a simple regex to find URLs - handles share text with embedded URLs
    match = re.search(r"https?://[^\s<>\"'）)]+", text)
    if match:
        url = match.group(0)
        # Clean up trailing punctuation
        url = re.sub(r"[.,;:!?)\]}>\"'）]+$", "", url)
        return url
    return None


def looks_like_url(value: str) -> bool:
    """Check if value looks like a URL."""
    if not value:
        return False
    if value.startswith("www."):
        return True
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_account_id(value: str) -> bool:
    """Check if value looks like a pure account ID (numeric)."""
    stripped = value.strip()
    # Pure numeric account ID
    if stripped.isdigit():
        return True
    return False


def is_nickname_like(value: str) -> bool:
    """Check if value looks like a nickname (short text without URL indicators)."""
    stripped = value.strip()
    if not stripped:
        return False
    # Starts with @ (common handle prefix)
    if stripped.startswith("@"):
        return True
    # Looks like URL - not a nickname
    if looks_like_url(stripped):
        return False
    # Short text that could be a handle or nickname
    if len(stripped) <= 50 and not " " in stripped and not "." in stripped:
        return True
    return False


def infer_platform_from_url(url: str) -> str | None:
    """Infer platform from URL."""
    host = url.lower()
    if "douyin.com" in host or "iesdouyin.com" in host:
        return "douyin"
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "xiaohongshu"
    if "bilibili.com" in host:
        return "bilibili"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "weibo.com" in host:
        return "weibo"
    return None


def _process_url(url_to_check: str, raw_value: str, platform: str) -> CreatorInputResult | None:
    """Helper to process a URL and return result, or None if not a match."""
    if not url_to_check:
        return None

    if platform == "douyin":
        # Check for short URL (v.douyin.com)
        short_match = re.search(PLATFORM_PATTERNS["douyin"]["short_url"], url_to_check)
        if short_match:
            return CreatorInputResult(
                platform="douyin",
                input_type="direct_link",
                profile_url=short_match.group(0).rstrip("/") + "/",
                raw_value=raw_value,
            )

        # Check for profile URL
        profile_match = re.search(PLATFORM_PATTERNS["douyin"]["profile_url"], url_to_check)
        if profile_match:
            profile_id = profile_match.group(1)
            return CreatorInputResult(
                platform="douyin",
                input_type="direct_link",
                profile_url=f"https://www.douyin.com/user/{profile_id}",
                raw_value=raw_value,
            )

        # Check for any douyin.com URL
        if "douyin.com" in url_to_check.lower():
            url = url_to_check.rstrip("/")
            return CreatorInputResult(
                platform="douyin",
                input_type="direct_link",
                profile_url=url,
                raw_value=raw_value,
            )

    elif platform == "xiaohongshu":
        # Check for profile URL (user/profile/xxx)
        profile_match = re.search(PLATFORM_PATTERNS["xiaohongshu"]["profile_url"], url_to_check)
        if profile_match:
            profile_id = profile_match.group(1)
            return CreatorInputResult(
                platform="xiaohongshu",
                input_type="direct_link",
                profile_url=f"https://www.xiaohongshu.com/user/profile/{profile_id}",
                raw_value=raw_value,
            )

        # Check for note URL - extract profile from it
        note_match = re.search(PLATFORM_PATTERNS["xiaohongshu"]["note_url"], url_to_check)
        if note_match:
            # Note URLs don't directly give profile, but we can still recognize the platform
            url = url_to_check
            return CreatorInputResult(
                platform="xiaohongshu",
                input_type="direct_link",
                profile_url=url,
                message="这是笔记链接而非主页链接，如需定位博主请提供主页链接",
                raw_value=raw_value,
            )

        # Check for any xiaohongshu.com URL
        if "xiaohongshu.com" in url_to_check.lower():
            url = url_to_check.rstrip("/")
            return CreatorInputResult(
                platform="xiaohongshu",
                input_type="direct_link",
                profile_url=url,
                raw_value=raw_value,
            )

    return None


def normalize_douyin_input(value: str) -> CreatorInputResult:
    """Normalize Douyin creator input."""
    raw_value = value.strip()

    # Always try to extract URL first (handles share text scenarios)
    extracted_url = extract_url_from_text(raw_value)

    # Try extracted URL first
    if extracted_url:
        result = _process_url(extracted_url, raw_value, "douyin")
        if result:
            return result

    # Try as direct URL
    if looks_like_url(raw_value):
        result = _process_url(raw_value, raw_value, "douyin")
        if result:
            return result

    # Case 2: Account ID or nickname - requires lookup
    if is_account_id(raw_value) or is_nickname_like(raw_value):
        return CreatorInputResult(
            platform="douyin",
            input_type="lookup_required",
            message="请提供博主主页链接进行确认",
            raw_value=raw_value,
        )

    # Case 3: Short text that might be a handle
    return CreatorInputResult(
        platform="douyin",
        input_type="lookup_required",
        message="请提供博主主页链接进行确认",
        raw_value=raw_value,
    )


def normalize_xiaohongshu_input(value: str) -> CreatorInputResult:
    """Normalize Xiaohongshu creator input."""
    raw_value = value.strip()

    # Always try to extract URL first (handles share text scenarios)
    extracted_url = extract_url_from_text(raw_value)

    # Try extracted URL first
    if extracted_url:
        result = _process_url(extracted_url, raw_value, "xiaohongshu")
        if result:
            return result

    # Try as direct URL
    if looks_like_url(raw_value):
        result = _process_url(raw_value, raw_value, "xiaohongshu")
        if result:
            return result

    # Case 2: Account ID or nickname - requires lookup
    if is_account_id(raw_value) or is_nickname_like(raw_value):
        return CreatorInputResult(
            platform="xiaohongshu",
            input_type="lookup_required",
            message="请提供博主主页链接进行确认",
            raw_value=raw_value,
        )

    # Case 3: Short text
    return CreatorInputResult(
        platform="xiaohongshu",
        input_type="lookup_required",
        message="请提供博主主页链接进行确认",
        raw_value=raw_value,
    )


def normalize_creator_input(
    platform: str,
    value: str,
    search_results_count: int | None = None,
    resolved_profile_url: str | None = None,
) -> CreatorInputResult:
    """
    Normalize creator input based on platform and value.

    Args:
        platform: The platform name (douyin, xiaohongshu, etc.)
        value: The input value (URL, account ID, nickname, or share text)
        search_results_count: Number of results from searching by ID/nickname (for ambiguous detection)
        resolved_profile_url: If search was unique, the resolved profile URL

    Returns:
        CreatorInputResult with:
        - platform: normalized platform name
        - input_type: direct_link | lookup_required | ambiguous
        - profile_url: resolved profile URL (if available)
        - message: human-readable message (for ambiguous or lookup_required)

    Key rules:
    1. If value is a URL, extract and normalize it
    2. If value is an account ID or nickname, mark as lookup_required
    3. If search_results_count > 1, mark as ambiguous (don't auto-select)
    4. If search_results_count == 1 and resolved_profile_url provided, return as direct_link
    """
    if not value:
        return CreatorInputResult(
            platform=platform or "unknown",
            input_type="lookup_required",
            message="输入为空，请提供博主主页链接或账号",
            raw_value=value,
        )

    raw_value = value.strip()
    platform_lower = (platform or "").lower().strip()

    # Handle search result ambiguity BEFORE normalization
    if search_results_count is not None and search_results_count > 1:
        # Multiple search results - user must provide profile URL
        return CreatorInputResult(
            platform=platform_lower or "unknown",
            input_type="ambiguous",
            message="搜索到多个匹配结果，请补全博主主页链接确认具体是哪个",
            raw_value=raw_value,
        )

    # Handle unique search result
    if search_results_count == 1 and resolved_profile_url:
        # Single result - return as direct link
        return CreatorInputResult(
            platform=platform_lower or "unknown",
            input_type="direct_link",
            profile_url=resolved_profile_url,
            raw_value=raw_value,
        )

    # Platform-specific normalization
    if platform_lower == "douyin":
        return normalize_douyin_input(raw_value)

    if platform_lower in ("xiaohongshu", "xhs", "RED", "redbook"):
        return normalize_xiaohongshu_input(raw_value)

    # Unknown platform - try to infer from URL
    extracted_url = extract_url_from_text(raw_value)
    url_to_check = extracted_url or (raw_value if looks_like_url(raw_value) else None)

    if url_to_check:
        inferred = infer_platform_from_url(url_to_check)
        if inferred == "douyin":
            return normalize_douyin_input(raw_value)
        if inferred == "xiaohongshu":
            return normalize_xiaohongshu_input(raw_value)

        # Generic URL handling
        return CreatorInputResult(
            platform=inferred or "unknown",
            input_type="direct_link",
            profile_url=url_to_check,
            raw_value=raw_value,
        )

    # No recognizable pattern - require lookup
    return CreatorInputResult(
        platform=platform_lower or "unknown",
        input_type="lookup_required",
        message="无法识别输入格式，请提供博主主页链接",
        raw_value=raw_value,
    )
