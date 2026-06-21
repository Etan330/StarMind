from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.models import RawSource, WikiPage


@dataclass
class ContentQuality:
    generation_status: str
    quality_level: str
    transcript_status: str
    source_refs_count: int
    body_length: int
    warnings: list[str]
    suggested_action: str

    def tone(self) -> str:
        if self.quality_level == "ready":
            return "success"
        if self.quality_level in {"needs_source_check", "asr_pending", "metadata_only", "fallback"}:
            return "warning"
        return "danger"


QUALITY_LABELS = {
    "ready": "质量良好",
    "needs_source_check": "需要核对来源",
    "asr_pending": "需要补全文本",
    "metadata_only": "仅有元数据",
    "fallback": "使用本地兜底",
    "failed": "生成失败",
}

GENERATION_LABELS = {
    "model_success": "模型生成成功",
    "fallback": "使用本地兜底",
    "failed": "生成失败",
}

TRANSCRIPT_LABELS = {
    "provided": "已有正文/逐字稿",
    "page_text_draft": "页面文本草稿",
    "audio_asr_pending": "ASR 待补全",
    "unknown": "文本状态未知",
}


def safe_json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def source_refs(raw_value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw_value or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def compute_page_quality(page: WikiPage, markdown: str, source_map: dict[int, RawSource]) -> ContentQuality:
    refs = source_refs(page.source_refs_json)
    sources = [source_map.get(int(ref.get("raw_source_id") or 0)) for ref in refs]
    sources = [source for source in sources if source is not None]
    primary_source = sources[0] if sources else None
    return compute_quality(markdown, refs_count=len(refs), raw_source=primary_source)


def compute_quality(markdown: str, refs_count: int, raw_source: RawSource | None = None) -> ContentQuality:
    body = markdown.strip()
    body_length = len(body)
    metadata = safe_json(raw_source.metadata_json if raw_source else "{}")
    transcript_status = str(metadata.get("transcript_status") or "unknown")
    generation_status = "model_success"
    if "模型未成功返回" in body or "本地兜底模板" in body:
        generation_status = "fallback"
    if "处理失败" in body:
        generation_status = "failed"

    warnings: list[str] = []
    quality_level = "ready"
    suggested_action = "可以审核后保存。"

    if refs_count <= 0:
        quality_level = "needs_source_check"
        warnings.append("没有来源引用，不能保存为正式知识页。")
        suggested_action = "返回原始资料并重新生成。"
    elif generation_status == "failed":
        quality_level = "failed"
        warnings.append("生成失败，当前草稿不可直接保存。")
        suggested_action = "重新生成或返回原始资料。"
    elif generation_status == "fallback":
        quality_level = "fallback"
        warnings.append("模型未成功返回，当前草稿来自本地兜底模板。")
        suggested_action = "配置模型后重新生成，或谨慎保存。"
    elif transcript_status == "audio_asr_pending":
        quality_level = "asr_pending"
        warnings.append("音频或视频文本尚未补全，内容可能只基于标题和链接。")
        suggested_action = "补全文本后再保存，或保存为待后续修订页面。"
    elif body_length < 500:
        quality_level = "metadata_only"
        warnings.append("正文较短，可能只有元数据或摘要。")
        suggested_action = "补充正文或重新生成后再保存。"
    elif transcript_status == "page_text_draft":
        quality_level = "needs_source_check"
        warnings.append("当前使用页面可见文本草稿，建议核对来源和关键结论。")
        suggested_action = "审核关键观点和来源后保存。"

    return ContentQuality(
        generation_status=generation_status,
        quality_level=quality_level,
        transcript_status=transcript_status,
        source_refs_count=refs_count,
        body_length=body_length,
        warnings=warnings,
        suggested_action=suggested_action,
    )


def quality_label(level: str) -> str:
    return QUALITY_LABELS.get(level, level)


def generation_label(status: str) -> str:
    return GENERATION_LABELS.get(status, status)


def transcript_label(status: str) -> str:
    return TRANSCRIPT_LABELS.get(status, status)


def markdown_summary(markdown: str, max_chars: int = 420) -> str:
    text = re.sub(r"```.*?```", "", markdown, flags=re.S)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"[*_>`#-]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def markdown_key_points(markdown: str, limit: int = 5) -> list[str]:
    lines = markdown.splitlines()
    key_points: list[str] = []
    in_key_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if in_key_section and key_points:
                continue
            continue
        if re.match(r"^#{1,4}\s*(关键观点|关键要点|要点|核心观点)", line):
            in_key_section = True
            continue
        if in_key_section and line.startswith("#"):
            break
        if in_key_section:
            match = re.match(r"^[-*]\s+(.+)$|^\d+[.)、]\s*(.+)$", line)
            if match:
                point = (match.group(1) or match.group(2) or "").strip()
                if point:
                    key_points.append(point)
            if len(key_points) >= limit:
                break

    if key_points:
        return key_points[:limit]

    summary = markdown_summary(markdown, max_chars=700)
    sentences = [item.strip(" ，。；;") for item in re.split(r"[。；;]\s*", summary) if item.strip()]
    return sentences[:limit]


def suggested_questions(title: str) -> list[dict[str, str]]:
    clean_title = title.replace("SOP：", "").replace("方法论：", "").strip()
    return [
        {"type": "principle", "question": f"{clean_title} 的核心观点是什么？"},
        {"type": "application", "question": f"我应该如何把 {clean_title} 用到当前工作里？"},
        {"type": "checklist", "question": f"基于 {clean_title}，下一步可以做哪些行动？"},
    ]
