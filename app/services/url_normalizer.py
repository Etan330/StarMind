from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "spm_id_from",
    "share_source",
    "timestamp",
    "t",
    "fbclid",
    "gclid",
}


@dataclass(frozen=True)
class NormalizedURL:
    raw_url: str
    canonical_url: str
    platform: str
    external_item_id: str


def infer_platform(parsed) -> str:
    host = parsed.netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "bilibili.com" in host:
        return "bilibili"
    if "douyin.com" in host or "iesdouyin.com" in host:
        return "douyin"
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    return "web"


def normalize_url(raw_url: str, platform: str | None = None) -> NormalizedURL:
    parsed = urlparse(raw_url.strip())
    if not parsed.scheme:
        parsed = urlparse(f"https://{raw_url.strip()}")

    inferred_platform = platform or infer_platform(parsed)
    scheme = "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path) or "/"
    query_pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in TRACKING_PARAMS]

    if inferred_platform == "youtube":
        video_id = _youtube_id(parsed)
        canonical = f"https://www.youtube.com/watch?v={video_id}" if video_id else _generic_canonical(scheme, netloc, path, query_pairs)
        external_id = video_id or _hash_id(canonical)
    elif inferred_platform == "bilibili":
        bilibili_id = _bilibili_id(path)
        canonical = f"https://www.bilibili.com/video/{bilibili_id}" if bilibili_id else _generic_canonical(scheme, netloc, path, query_pairs)
        external_id = bilibili_id or _hash_id(canonical)
    elif inferred_platform == "github":
        repo_id = _github_repo_id(path)
        canonical = f"https://github.com/{repo_id}" if repo_id else _generic_canonical(scheme, netloc, path, query_pairs)
        external_id = repo_id or _hash_id(canonical)
    elif inferred_platform == "douyin":
        douyin_id = _douyin_id(path)
        canonical = f"https://www.douyin.com/video/{douyin_id}" if douyin_id else _generic_canonical(scheme, netloc, path, query_pairs)
        external_id = douyin_id or _hash_id(canonical)
    else:
        canonical = _generic_canonical(scheme, netloc, path, query_pairs)
        external_id = _hash_id(canonical)

    return NormalizedURL(
        raw_url=raw_url,
        canonical_url=canonical,
        platform=inferred_platform,
        external_item_id=external_id,
    )


def _generic_canonical(scheme: str, netloc: str, path: str, query_pairs: list[tuple[str, str]]) -> str:
    query = urlencode(sorted(query_pairs))
    return urlunparse((scheme, netloc, path, "", query, ""))


def _youtube_id(parsed) -> str | None:
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0] or None
    query = dict(parse_qsl(parsed.query))
    if "v" in query:
        return query["v"]
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"shorts", "embed"}:
        return parts[1]
    return None


def _bilibili_id(path: str) -> str | None:
    match = re.search(r"/video/((BV|av)[A-Za-z0-9]+)", path)
    return match.group(1) if match else None


def _github_repo_id(path: str) -> str | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _douyin_id(path: str) -> str | None:
    match = re.search(r"/(?:video|note)/([A-Za-z0-9_-]+)", path)
    return match.group(1) if match else None


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
