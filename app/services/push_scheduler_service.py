from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import PushHistory, PushSettings, UserPreference, WikiCategory, WikiPage

logger = logging.getLogger("starmind.push")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PushSchedulerService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create_settings(self) -> PushSettings:
        settings = self.db.query(PushSettings).first()
        if not settings:
            settings = PushSettings()
            self.db.add(settings)
            self.db.commit()
            self.db.refresh(settings)
        return settings

    async def generate_push_items(self) -> list[dict]:
        """Select wiki pages weighted by category preferences."""
        settings = self.get_or_create_settings()
        if settings.is_paused:
            return []

        prefs = {p.domain: p.score for p in self.db.query(UserPreference).all()}
        if not prefs:
            return []

        # Get active wiki pages with categories (include needs_review for freshly distilled)
        # Exclude blacklisted pages (category = "知识黑名单")
        pages = (
            self.db.query(WikiPage, WikiCategory.name)
            .outerjoin(WikiCategory, WikiPage.category_id == WikiCategory.id)
            .filter(WikiPage.status.in_(["active", "needs_review"]))
            .filter((WikiCategory.name != "知识黑名单") | (WikiPage.category_id == None))  # noqa: E711
            .all()
        )
        if not pages:
            return []

        # Filter already pushed — only exclude recently pushed (last 7 days)
        from datetime import timedelta
        recent_cutoff = _utcnow() - timedelta(days=7)
        recently_pushed_ids = {
            h.wiki_page_id for h in
            self.db.query(PushHistory.wiki_page_id).filter(PushHistory.pushed_at > recent_cutoff).all()
            if h.wiki_page_id
        }
        candidates = [(p, cat) for p, cat in pages if p.id not in recently_pushed_ids]
        if not candidates:
            # All pushed recently — pick from least recently pushed
            all_pushed_ids = {h.wiki_page_id for h in self.db.query(PushHistory.wiki_page_id).all() if h.wiki_page_id}
            never_pushed = [(p, cat) for p, cat in pages if p.id not in all_pushed_ids]
            candidates = never_pushed if never_pushed else pages

        # Step 1: Pick ONE category based on preference proportion
        # e.g. A=50, B=100 → total=150 → A has 1/3 chance, B has 2/3 chance
        cat_weights = {}
        for page, cat_name in candidates:
            if cat_name not in cat_weights:
                cat_weights[cat_name] = prefs.get(cat_name, 50)
        total_pref = sum(cat_weights.values()) or 1
        cat_list = list(cat_weights.keys())
        cat_probs = [cat_weights[c] / total_pref for c in cat_list]
        chosen_cat = random.choices(cat_list, weights=cat_probs, k=1)[0]

        # Step 2: Random pick ONE page from that category
        cat_pages = [p for p, cn in candidates if cn == chosen_cat]
        if not cat_pages:
            return []
        chosen_page = random.choice(cat_pages)

        # Record push history
        settings.total_push_count += 1
        history = PushHistory(
            raw_source_id=self._get_raw_source_id(chosen_page),
            wiki_page_id=chosen_page.id,
            category_name=chosen_cat,
        )
        self.db.add(history)
        self.db.flush()

        show_feedback = (settings.total_push_count % 5 == 0)
        results = [{
            "push_id": history.id,
            "title": chosen_page.title,
            "summary": self._read_summary(chosen_page),
            "category": chosen_cat,
            "show_feedback": show_feedback,
        }]

        self.db.commit()
        return results


    def handle_feedback(self, push_history_id: int, feedback: str) -> dict:
        """Process like/unlike feedback. Adjust preference ±10. Unlike → move to blacklist."""
        history = self.db.get(PushHistory, push_history_id)
        if not history:
            return {"error": "not_found"}

        history.feedback = feedback
        history.feedback_at = _utcnow()

        cat_name = history.category_name
        if cat_name:
            pref = self.db.query(UserPreference).filter(UserPreference.domain == cat_name).first()
            if pref:
                if feedback == "like":
                    pref.score = min(100, pref.score + 10)
                elif feedback == "unlike":
                    pref.score = max(0, pref.score - 10)

        if feedback == "unlike" and history.wiki_page_id:
            # Move the page to "知识黑名单" category
            from app.services.auto_distill_service import AutoDistillService
            svc = AutoDistillService(self.db)
            blacklist_cat = svc._get_or_create_category("知识黑名单")
            # Ensure blacklist preference is 0
            bl_pref = self.db.query(UserPreference).filter(UserPreference.domain == "知识黑名单").first()
            if bl_pref:
                bl_pref.score = 0
            else:
                self.db.add(UserPreference(domain="知识黑名单", score=0))
            # Move the page
            page = self.db.get(WikiPage, history.wiki_page_id)
            if page:
                page.category_id = blacklist_cat.id

        self.db.commit()
        new_score = None
        if cat_name:
            pref = self.db.query(UserPreference).filter(UserPreference.domain == cat_name).first()
            new_score = pref.score if pref else None
        return {"ok": True, "new_score": new_score, "blacklisted": feedback == "unlike"}

    def save_preferences(self, preferences: dict[str, int]) -> None:
        """Batch save category preferences."""
        for domain, score in preferences.items():
            pref = self.db.query(UserPreference).filter(UserPreference.domain == domain).first()
            if pref:
                pref.score = max(0, min(100, score))
            else:
                self.db.add(UserPreference(domain=domain, score=max(0, min(100, score))))
        self.db.commit()

    def save_schedule(self, days: list[int], times: list[str] | str | None = None, time: str | None = None) -> None:
        """Save push schedule. times is a list of HH:MM strings."""
        settings = self.get_or_create_settings()
        settings.push_days = ",".join(str(d) for d in sorted(days))
        if times is not None:
            settings.push_time = ",".join(times) if isinstance(times, list) else times
        elif time is not None:
            settings.push_time = time
        self.db.commit()

    def _get_raw_source_id(self, page: WikiPage) -> int:
        import json
        refs = json.loads(page.source_refs_json or "[]")
        return refs[0]["raw_source_id"] if refs else 0

    def _read_summary(self, page: WikiPage) -> str:
        """Extract the 一句话总结 section from the wiki page markdown."""
        from pathlib import Path
        try:
            text = Path(page.markdown_path).read_text(encoding="utf-8")
            # Find "一句话总结" section
            lines = text.split('\n')
            capture = False
            summary_lines = []
            for line in lines:
                if '一句话总结' in line:
                    capture = True
                    continue
                if capture:
                    if line.startswith('## '):
                        break
                    if line.strip():
                        summary_lines.append(line.strip())
            if summary_lines:
                return ' '.join(summary_lines)[:300]
            # Fallback: first non-header non-empty line after title
            for line in lines[3:]:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and not stripped.startswith('>'):
                    return stripped[:300]
            return page.title
        except Exception:
            return page.title
