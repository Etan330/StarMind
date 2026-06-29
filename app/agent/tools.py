from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import LOCAL_DATA_DIR
from app.models import RawSource, WikiPage


@dataclass
class ToolResult:
    name: str
    content: str
    metadata: dict[str, Any]


class KnowledgeSearchTool:
    name = "knowledge_search"

    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, query: str, limit: int = 6, creator_key: str | None = None) -> ToolResult:
        tokens = [token.strip().lower() for token in query.replace("，", " ").replace("。", " ").split() if token.strip()]
        if creator_key:
            marker = f"creator:{creator_key}"
            pages = [
                page for page in self.db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).all()
                if marker in self._json_list(page.tags_json)
            ][:30]
            raw_sources = [
                source for source in self.db.query(RawSource).order_by(RawSource.created_at.desc()).all()
                if self._json_dict(source.metadata_json).get("creator_key") == creator_key
            ][:30]
        else:
            pages = self.db.query(WikiPage).order_by(WikiPage.last_updated_at.desc()).limit(30).all()
            raw_sources = self.db.query(RawSource).order_by(RawSource.created_at.desc()).limit(30).all()
        snippets: list[dict[str, str]] = []

        for page in pages:
            text = self._read_local_text(page.markdown_path)
            haystack = f"{page.title}\n{text}".lower()
            if not tokens or any(token in haystack for token in tokens):
                snippets.append({"type": "wiki", "title": page.title, "text": text[:1200]})
            if len(snippets) >= limit:
                break

        for source in raw_sources:
            text = self._read_local_text(source.transcript_path) or self._read_local_text(source.clean_text_path)
            haystack = f"{source.title}\n{text}".lower()
            if not tokens or any(token in haystack for token in tokens):
                snippets.append({"type": "raw_source", "title": source.title, "text": text[:1200], "url": source.canonical_url})
            if len(snippets) >= limit:
                break

        content = "\n\n".join(f"[{item['type']}] {item['title']}\n{item['text']}" for item in snippets)
        metadata = {"count": len(snippets), "items": snippets}
        if creator_key:
            metadata["creator_key"] = creator_key
        return ToolResult(self.name, content or "没有找到相关本地资料。", metadata)

    def _json_dict(self, raw_value: str | None) -> dict[str, Any]:
        try:
            value = json.loads(raw_value or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _json_list(self, raw_value: str | None) -> list[str]:
        try:
            value = json.loads(raw_value or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item) for item in value] if isinstance(value, list) else []

    def _read_local_text(self, path_value: str | None) -> str:
        if not path_value:
            return ""
        path = Path(path_value)
        try:
            resolved = path.resolve()
            if not str(resolved).startswith(str(LOCAL_DATA_DIR.resolve())):
                return ""
            if not resolved.exists():
                return ""
            return resolved.read_text(encoding="utf-8")[:8000]
        except Exception:
            return ""
