from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.llm import get_provider_runtime
from app.models import RawSource, WikiCategory, WikiPage
from app.services.wiki_service import WikiMaintenanceService

logger = logging.getLogger("starmind.auto_distill")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9一-鿿]+", "-", name.lower()).strip("-")
    return slug or "uncategorized"


class AutoDistillService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def distill_pending(self, limit: int = 5) -> list[WikiPage]:
        """Find undistilled RawSources, generate wiki pages, and classify."""
        pending = (
            self.db.query(RawSource)
            .filter(RawSource.is_distilled == False)  # noqa: E712
            .order_by(RawSource.created_at.desc())
            .limit(limit)
            .all()
        )
        if not pending:
            return []

        results = []
        wiki_svc = WikiMaintenanceService(self.db)
        for raw_source in pending:
            try:
                # 1. Classify by title → category
                category_name = await self._classify_title(raw_source.title)
                raw_source.preliminary_category = category_name

                # 2. Create wiki page
                page = await wiki_svc.create_page_from_raw_source(raw_source.id, page_type="knowledge")

                # 3. Assign category
                category = self._get_or_create_category(category_name)
                page.category_id = category.id

                # 4. Mark distilled
                raw_source.is_distilled = True
                raw_source.distilled_at = _utcnow()
                self.db.commit()
                results.append(page)
                logger.info(f"Distilled RawSource {raw_source.id} → WikiPage {page.page_id} [{category_name}]")
            except Exception as e:
                logger.warning(f"Failed to distill RawSource {raw_source.id}: {e}")
                self.db.rollback()
        return results

    async def _classify_title(self, title: str) -> str:
        """Use LLM to classify a title into a category name. Prioritizes existing categories."""
        existing_cats = [c.name for c in self.db.query(WikiCategory).all()]
        existing_str = ", ".join(existing_cats) if existing_cats else "暂无"

        if existing_cats:
            prompt = (
                f"现有知识库分类: [{existing_str}]\n"
                f"内容标题: {title}\n\n"
                "【重要规则】\n"
                "1. 优先从现有分类中选择最匹配的，哪怕只是大致相关也应归入现有分类\n"
                "2. 只有当内容与所有现有分类都完全不相关时，才建议一个新分类名（2-4个字）\n"
                "3. 新分类名不能与现有分类含义重复或相近（如\"技术\"和\"编程技术\"算重复）\n\n"
                "只回复分类名称，不要其他内容。"
            )
        else:
            prompt = (
                f"内容标题: {title}\n\n"
                "请为该内容建议一个简短的分类名（2-4个字）。只回复分类名称。"
            )
        try:
            provider, model, _ = get_provider_runtime()
            result = await provider.chat(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=0.1,
            )
            cat_name = result.strip().strip("\"'「」【】").strip()[:60]
            # Fuzzy match: if LLM returns a near-duplicate of existing, use existing
            if existing_cats and cat_name not in existing_cats:
                for existing in existing_cats:
                    if existing in cat_name or cat_name in existing:
                        return existing
            return cat_name
        except Exception:
            return "未分类"

    def _get_or_create_category(self, name: str) -> WikiCategory:
        """Get existing category by name or create a new one."""
        cat = self.db.query(WikiCategory).filter(WikiCategory.name == name).first()
        if cat:
            return cat
        max_order = self.db.query(WikiCategory.display_order).order_by(WikiCategory.display_order.desc()).first()
        cat = WikiCategory(
            name=name,
            slug=_slugify(name),
            display_order=(max_order[0] + 1) if max_order else 0,
        )
        self.db.add(cat)
        self.db.flush()
        return cat

    def get_distill_status(self) -> dict:
        pending = self.db.query(RawSource).filter(RawSource.is_distilled == False).count()  # noqa: E712
        distilled = self.db.query(RawSource).filter(RawSource.is_distilled == True).count()  # noqa: E712
        return {"pending": pending, "distilled": distilled}
