from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import ProductEvent


SAFE_PROPERTY_KEYS = {
    "audit_status",
    "candidate_id",
    "cta",
    "current_step",
    "demo_id",
    "demo_type",
    "duplicate",
    "entry",
    "entry_mode",
    "from_home",
    "generation_status",
    "has_sources",
    "has_history",
    "has_target_source",
    "has_title",
    "has_wiki_pages",
    "input_type",
    "length_bucket",
    "mode",
    "model_status",
    "page_id",
    "page_type",
    "pending_count",
    "platform",
    "position",
    "quality_level",
    "question_type",
    "raw_source_id",
    "reason",
    "source_count",
    "source_refs_count",
    "source_type",
    "steps_count",
    "time_to_activation",
    "transcript_status",
    "visitor_state",
    "viewport",
}

SENSITIVE_KEY_PARTS = ("api_key", "cookie", "secret", "token", "password", "authorization", "browser")


def safe_event_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        lowered = key.lower()
        if lowered not in SAFE_PROPERTY_KEYS:
            continue
        if any(part in lowered for part in SENSITIVE_KEY_PARTS):
            continue
        if isinstance(value, str):
            if any(part in value.lower() for part in ("sk-", "cookie=", "bearer ")):
                continue
            cleaned[key] = value[:500]
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[key] = value
        else:
            cleaned[key] = str(value)[:500]
    return cleaned


class TrackingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def track(
        self,
        event_name: str,
        properties: dict[str, Any] | None = None,
        *,
        candidate_id: int | None = None,
        raw_source_id: int | None = None,
        page_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        try:
            self.db.add(
                ProductEvent(
                    event_name=event_name,
                    properties_json=json.dumps(safe_event_properties(properties), ensure_ascii=False),
                    candidate_id=candidate_id,
                    raw_source_id=raw_source_id,
                    page_id=page_id,
                    session_id=session_id,
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()

    def recent(self, limit: int = 100) -> list[ProductEvent]:
        return (
            self.db.query(ProductEvent)
            .order_by(ProductEvent.created_at.desc())
            .limit(limit)
            .all()
        )

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.db.query(ProductEvent).all():
            counts[event.event_name] = counts.get(event.event_name, 0) + 1
        return counts
