from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import CandidateItem, RawSource, RecycleBinItem, WikiCategory, WikiPage
from app.services.statuses import PENDING_CLASSIFICATION


class RecycleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _finish_restore(self, item: RecycleBinItem) -> None:
        self.db.delete(item)

    # ------------------------------------------------------------------
    # Archive (move to recycle bin)
    # ------------------------------------------------------------------

    def archive_raw_source(self, raw_source_id: int) -> RecycleBinItem:
        source = self.db.get(RawSource, raw_source_id)
        if source is None:
            raise ValueError(f"RawSource {raw_source_id} not found")

        snap = json.dumps({
            "candidate_id": source.candidate_id,
            "platform": source.platform,
            "source_url": source.source_url,
            "canonical_url": source.canonical_url,
            "external_item_id": source.external_item_id,
            "source_type": source.source_type,
            "title": source.title,
            "author": source.author,
            "metadata_json": source.metadata_json,
        }, ensure_ascii=False)

        item = RecycleBinItem(
            item_type="raw_source",
            candidate_id=source.candidate_id,
            canonical_url=source.canonical_url or "",
            external_item_id=source.external_item_id or "",
            title=source.title,
            platform=source.platform,
            reason="user_deleted",
            source_label="原始资料",
            raw_source_snapshot_json=snap,
        )
        self.db.add(item)
        self.db.delete(source)
        self.db.commit()
        self.db.refresh(item)
        return item

    def archive_wiki_page(self, page_id: str) -> RecycleBinItem:
        page = self.db.query(WikiPage).filter(WikiPage.page_id == page_id).first()
        if page is None:
            raise ValueError(f"WikiPage {page_id} not found")

        cat_name = None
        if page.category_id:
            cat = self.db.get(WikiCategory, page.category_id)
            cat_name = cat.name if cat else None
        source_label = f"知识库 › {cat_name}" if cat_name else "知识库（未分类）"
        snap = json.dumps({"category_id": page.category_id, "category_name": cat_name}, ensure_ascii=False)

        page.status = "deleted"
        item = RecycleBinItem(
            item_type="wiki_page",
            page_id=page.page_id,
            canonical_url="",
            external_item_id="",
            title=page.title,
            platform="wiki",
            reason="user_deleted",
            source_label=source_label,
            raw_source_snapshot_json=snap,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(self, recycle_item_id: int):
        item = self.db.get(RecycleBinItem, recycle_item_id)
        if item is None:
            raise ValueError(f"RecycleBinItem {recycle_item_id} not found")

        if item.item_type == "wiki_page":
            return self._restore_wiki_page(item)
        if item.item_type == "raw_source":
            return self._restore_raw_source(item)
        if item.item_type == "wiki_category":
            self._finish_restore(item)
            self.db.commit()
            return item
        return self._restore_candidate(item)

    def _restore_wiki_page(self, item: RecycleBinItem) -> WikiPage:
        if not item.page_id:
            raise ValueError("RecycleBinItem has no page_id")
        page = self.db.query(WikiPage).filter(WikiPage.page_id == item.page_id).first()
        if page is None:
            raise ValueError(f"WikiPage {item.page_id} not found")

        page.status = "active"
        if item.raw_source_snapshot_json:
            snap = json.loads(item.raw_source_snapshot_json)
            category_id = snap.get("category_id")
            if category_id and self.db.get(WikiCategory, category_id):
                page.category_id = category_id

        self._finish_restore(item)
        self.db.commit()
        self.db.refresh(page)
        return page

    def _restore_candidate(self, item: RecycleBinItem) -> CandidateItem | RawSource:
        candidate = self.db.get(CandidateItem, item.candidate_id) if item.candidate_id else None
        if candidate is None:
            return self._restore_raw_source(item)
        candidate.status = PENDING_CLASSIFICATION
        self._finish_restore(item)
        self.db.commit()
        self.db.refresh(candidate)
        return candidate

    def _restore_raw_source(self, item: RecycleBinItem) -> RawSource:
        # Check for existing duplicate first (by canonical_url if available)
        if item.canonical_url:
            existing = self.db.query(RawSource).filter(
                RawSource.canonical_url == item.canonical_url
            ).first()
            if existing:
                self._finish_restore(item)
                self.db.commit()
                return existing

        # Path 1: new-style items have a full snapshot
        if item.raw_source_snapshot_json:
            snap = json.loads(item.raw_source_snapshot_json)
            snapshot_candidate_id = snap.get("candidate_id")
            if snapshot_candidate_id and self.db.get(CandidateItem, snapshot_candidate_id):
                candidate_id = snapshot_candidate_id
            else:
                candidate_id = None
            raw_source = RawSource(
                candidate_id=candidate_id,
                platform=snap["platform"],
                source_url=snap.get("source_url", item.canonical_url),
                canonical_url=snap.get("canonical_url", item.canonical_url),
                external_item_id=snap.get("external_item_id") or item.external_item_id or "",
                source_type=snap.get("source_type", "link"),
                title=snap.get("title", item.title),
                author=snap.get("author"),
                metadata_json=snap.get("metadata_json", "{}"),
            )
            self.db.add(raw_source)
            self._finish_restore(item)
            self.db.commit()
            self.db.refresh(raw_source)
            return raw_source

        # Path 2: legacy items created from CandidateItem (no snapshot stored)
        # Try to rebuild a RawSource from whatever data we have on the RecycleBinItem itself
        candidate: CandidateItem | None = None
        if item.candidate_id:
            candidate = self.db.get(CandidateItem, item.candidate_id)

        if candidate is not None:
            raw_source = RawSource(
                candidate_id=candidate.id,
                platform=candidate.platform,
                source_url=candidate.raw_url or candidate.canonical_url,
                canonical_url=candidate.canonical_url,
                external_item_id=candidate.external_item_id or "",
                source_type="link",
                title=candidate.title,
                author=candidate.author,
                metadata_json=candidate.metadata_json or "{}",
            )
            self.db.add(raw_source)
            self._finish_restore(item)
            self.db.commit()
            self.db.refresh(raw_source)
            return raw_source

        # Path 3: last resort — rebuild from RecycleBinItem fields only
        if not item.canonical_url:
            raise ValueError("Cannot restore: no snapshot and no candidate data available")

        raw_source = RawSource(
            candidate_id=None,
            platform=item.platform,
            source_url=item.canonical_url,
            canonical_url=item.canonical_url,
            external_item_id=item.external_item_id or "",
            source_type="link",
            title=item.title,
            metadata_json="{}",
        )
        self.db.add(raw_source)
        self._finish_restore(item)
        self.db.commit()
        self.db.refresh(raw_source)
        return raw_source

    # ------------------------------------------------------------------
    # Permanent delete
    # ------------------------------------------------------------------

    def permanent_delete(self, recycle_item_id: int) -> None:
        item = self.db.get(RecycleBinItem, recycle_item_id)
        if item is None:
            raise ValueError(f"RecycleBinItem {recycle_item_id} not found")
        self.db.delete(item)
        self.db.commit()
