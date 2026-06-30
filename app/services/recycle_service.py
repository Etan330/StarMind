from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CandidateItem, RecycleBinItem, WikiPage
from app.services.statuses import CLASSIFIED_KNOWLEDGE, PENDING_CLASSIFICATION


class RecycleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def restore(self, recycle_item_id: int, target: str = "review"):
        item = self.db.get(RecycleBinItem, recycle_item_id)
        if item is None:
            raise ValueError(f"RecycleBinItem {recycle_item_id} not found")

        if item.item_type == "wiki_page":
            return self._restore_wiki_page(item)

        return self._restore_candidate(item, target)

    def _restore_wiki_page(self, item: RecycleBinItem) -> WikiPage:
        if not item.page_id:
            raise ValueError("RecycleBinItem has no page_id for wiki_page restore")
        page = self.db.query(WikiPage).filter(WikiPage.page_id == item.page_id).first()
        if page is None:
            raise ValueError(f"WikiPage {item.page_id} not found")
        page.status = "active"
        item.status = "restored"
        self.db.commit()
        self.db.refresh(page)
        return page

    def _restore_candidate(self, item: RecycleBinItem, target: str) -> CandidateItem:
        candidate = self.db.get(CandidateItem, item.candidate_id) if item.candidate_id else None
        if candidate is None:
            raise ValueError("Original candidate is missing")
        candidate.status = CLASSIFIED_KNOWLEDGE if target == "knowledge" else PENDING_CLASSIFICATION
        item.status = "restored"
        self.db.commit()
        self.db.refresh(candidate)
        return candidate
