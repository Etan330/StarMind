from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CandidateItem, RecycleBinItem
from app.services.statuses import CLASSIFIED_KNOWLEDGE, PENDING_CLASSIFICATION


class RecycleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def restore(self, recycle_item_id: int, target: str = "review") -> CandidateItem:
        item = self.db.get(RecycleBinItem, recycle_item_id)
        if item is None:
            raise ValueError(f"RecycleBinItem {recycle_item_id} not found")
        candidate = self.db.get(CandidateItem, item.candidate_id) if item.candidate_id else None
        if candidate is None:
            raise ValueError("Original candidate is missing")
        candidate.status = CLASSIFIED_KNOWLEDGE if target == "knowledge" else PENDING_CLASSIFICATION
        item.status = "restored"
        self.db.commit()
        self.db.refresh(candidate)
        return candidate
