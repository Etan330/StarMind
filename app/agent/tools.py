from __future__ import annotations

from dataclasses import dataclass
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

    def run(self, query: str, limit: int = 6) -> ToolResult:
        tokens = [token.strip().lower() for token in query.replace("，", " ").replace("。", " ").split() if token.strip()]
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
        return ToolResult(self.name, content or "没有找到相关本地资料。", {"count": len(snippets), "items": snippets})

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
