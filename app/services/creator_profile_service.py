from __future__ import annotations

import re
from dataclasses import dataclass, field
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
