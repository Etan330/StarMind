from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import KnowledgeClassification, PushHistory, PushSettings, RawSource, UserPreference


class PushService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _get_settings(self) -> PushSettings | None:
        return self.db.query(PushSettings).first()

    def _in_time_window(self, settings: PushSettings) -> bool:
        now = datetime.now().strftime("%H:%M")
        return settings.start_time <= now <= settings.end_time

    async def generate_push(self) -> list[dict[str, Any]]:
        settings = self._get_settings()
        if settings and (settings.is_paused or not self._in_time_window(settings)):
            return []

        items_per_push = settings.items_per_push if settings else 3
        prefs = {p.domain: p.score for p in self.db.query(UserPreference).all()}

        # Get recent push IDs to exclude
        recent_ids = {h.raw_source_id for h in self.db.query(PushHistory).order_by(PushHistory.pushed_at.desc()).limit(50).all()}

        # Get all raw sources with classifications
        sources = self.db.query(RawSource).all()
        candidates: list[tuple[RawSource, float]] = []
        for src in sources:
            if src.id in recent_ids:
                continue
            # Find classification domain
            cls = self.db.query(KnowledgeClassification).filter(KnowledgeClassification.candidate_id == src.candidate_id).first()
            domain = cls.label if cls else "未分类"
            weight = prefs.get(domain, 50) / 100.0
            if weight < 0.05:
                continue
            candidates.append((src, weight))

        if not candidates:
            return []

        # Weighted random selection
        selected: list[RawSource] = []
        pool = list(candidates)
        for _ in range(min(items_per_push, len(pool))):
            weights = [w for _, w in pool]
            chosen = random.choices(pool, weights=weights, k=1)[0]
            selected.append(chosen[0])
            pool.remove(chosen)

        # Record push history
        result = []
        for src in selected:
            self.db.add(PushHistory(raw_source_id=src.id))
            result.append({"push_id": src.id, "title": src.title, "platform": src.platform, "source_url": src.source_url})
        self.db.commit()
        return result

    async def handle_feedback(self, push_id: int, feedback: str) -> None:
        history = self.db.query(PushHistory).filter(PushHistory.raw_source_id == push_id).order_by(PushHistory.pushed_at.desc()).first()
        if history:
            history.feedback = feedback
            history.feedback_at = datetime.now()

        # Find domain for this source
        src = self.db.get(RawSource, push_id)
        if not src:
            self.db.commit()
            return

        cls = self.db.query(KnowledgeClassification).filter(KnowledgeClassification.candidate_id == src.candidate_id).first()
        domain = cls.label if cls else None
        if not domain:
            self.db.commit()
            return

        pref = self.db.query(UserPreference).filter(UserPreference.domain == domain).first()
        if not pref:
            pref = UserPreference(domain=domain, score=50)
            self.db.add(pref)

        if feedback == "like":
            pref.score = min(100, pref.score + 2)
        elif feedback == "unlike":
            pref.score = max(0, pref.score - 3)

        # Check consecutive feedback for same domain
        recent = (
            self.db.query(PushHistory)
            .join(RawSource, PushHistory.raw_source_id == RawSource.id)
            .filter(PushHistory.feedback.isnot(None))
            .order_by(PushHistory.feedback_at.desc())
            .limit(5)
            .all()
        )
        consecutive_unlike = 0
        consecutive_like = 0
        for h in recent:
            if h.feedback == "unlike":
                consecutive_unlike += 1
                consecutive_like = 0
            elif h.feedback == "like":
                consecutive_like += 1
                consecutive_unlike = 0
            else:
                break

        if consecutive_unlike >= 3:
            pref.score = min(pref.score, 10)
        if consecutive_like >= 5:
            pref.score = max(pref.score, 90)

        self.db.commit()
