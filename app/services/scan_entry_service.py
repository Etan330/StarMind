"""ScanEntryService — 扫描+分类结果的持久化读写。

DB（scan_entries 表）为权威源；前端 localStorage 仅作离线缓存/断点续跑。
一条收藏从「扫描展示 → 勾选建 candidate → 提取建 RawSource」三个阶段都靠
(platform, external_item_id) 这把唯一键在这里串起来。

历史/新增切分：
- 首次扫描（connector.first_scan_done == False / 不存在）⇒ 全部条目算「历史」，
  扫完置 first_scan_done=True 并记最新一条为 history_boundary_external_id。
- 之后扫描（first_scan_done == True）⇒ 算「新增」(incremental)，用 ScanEntry+SyncLedger
  全集 (platform, external_item_id) 过滤掉已见条目（不靠「遇到第一个旧的就 break」，
  兼容 DOM 乱序/新增穿插，跨天去重天然满足）。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.base import ConnectorItem
from app.models import CandidateItem, Connector, RawSource, ScanEntry, SyncLedgerItem
from app.services.url_normalizer import normalize_url


HISTORY = "history"
INCREMENTAL = "incremental"


class ScanEntryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- 历史/新增判定 ----

    def _connector(self, platform: str) -> Connector | None:
        return (
            self.db.query(Connector)
            .filter(Connector.platform == platform)
            .order_by(Connector.id.asc())
            .first()
        )

    def _ensure_connector(self, platform: str) -> Connector:
        """缺失则建一行最小 connector（仿 record_boundary，供 set_history_saved 等需要落 flag 的场景）。"""
        connector = self._connector(platform)
        if connector is None:
            connector = Connector(
                name=f"{platform} 收藏夹",
                platform=platform,
                connector_type=f"browser_{platform}",
                status="active",
                auth_method="browser",
            )
            self.db.add(connector)
            self.db.flush()
        return connector

    def determine_kind(self, platform: str) -> str:
        connector = self._connector(platform)
        if connector is None or not bool(getattr(connector, "first_scan_done", False)):
            return HISTORY
        return INCREMENTAL

    # ---- 历史「采集一次保存」状态 ----

    def is_history_saved(self, platform: str) -> bool:
        connector = self._connector(platform)
        return bool(getattr(connector, "history_saved", False)) if connector else False

    def set_history_saved(self, platform: str, saved: bool) -> None:
        """翻 history_saved flag。saved=True 时缺 connector 则建一行最小 connector。"""
        if not saved:
            connector = self._connector(platform)
            if connector is None:
                return
        else:
            connector = self._ensure_connector(platform)
        connector.history_saved = bool(saved)
        self.db.commit()

    def reset_history(self, platform: str) -> None:
        """「重新扫描历史」：清 history_saved、first_scan_done 和旧历史条目。

        关键——只清 history_saved 不清 first_scan_done 的话，determine_kind 仍返回 incremental，
        下次扫描会被 filter_incremental 把已见条目全滤掉 → "0 new"，违背「重新扫描历史」。
        清掉历史 ScanEntry 后，用户重新扫描时看到的是新的干净历史集合。
        """
        connector = self._connector(platform)
        if connector is None:
            return
        connector.history_saved = False
        connector.first_scan_done = False
        connector.history_boundary_external_id = None
        self.db.query(ScanEntry).filter(
            ScanEntry.platform == platform,
            ScanEntry.collection_kind == HISTORY,
        ).delete(synchronize_session=False)
        self.db.commit()

    def _seen_external_ids(self, platform: str) -> set[str]:
        """该平台所有「已见」的 external_item_id 全集：ScanEntry ∪ SyncLedger。"""
        seen: set[str] = set()
        for (ext,) in self.db.query(ScanEntry.external_item_id).filter(ScanEntry.platform == platform).all():
            if ext:
                seen.add(ext)
        for (ext,) in self.db.query(SyncLedgerItem.external_item_id).filter(SyncLedgerItem.platform == platform).all():
            if ext:
                seen.add(ext)
        return seen

    def filter_incremental(self, platform: str, items: list[ConnectorItem]) -> list[ConnectorItem]:
        """新增扫描：从最新往旧扫，遇到已见条目即停止。"""
        kept, _boundary_hit = self.filter_incremental_until_boundary(platform, items)
        return kept

    def filter_incremental_until_boundary(self, platform: str, items: list[ConnectorItem]) -> tuple[list[ConnectorItem], bool]:
        """新增扫描：只返回边界之前的新条目，遇到历史/已提取条目即停止。

        收藏页通常按新到旧排列。首次遇到已见 external_item_id 代表后面都是历史或上次已扫过的
        内容，不再继续提取或展示，避免新增 Tab 混入历史收藏。
        """
        seen = self._seen_external_ids(platform)
        kept: list[ConnectorItem] = []
        for item in items:
            normalized = normalize_url(item.raw_url, platform)
            if normalized.external_item_id in seen:
                return kept, True
            kept.append(item)
        return kept, False

    # ---- upsert ----

    def upsert_from_items(
        self,
        platform: str,
        items: list[ConnectorItem],
        kind: str,
        scan_run_id: str,
    ) -> list[dict[str, Any]]:
        """按 (platform, external_item_id) upsert，返回带 scan_entry_id 的字典列表（供端点回传前端）。

        已存在的条目只更新 last_seen_at/title/published_at 等展示字段，
        绝不覆盖已有的 usefulness/extracted（保住已分类/已提取状态）。
        """
        out: list[dict[str, Any]] = []
        now = datetime.utcnow()
        for item in items:
            normalized = normalize_url(item.raw_url, platform)
            metadata = item.metadata or {}
            published = str(metadata.get("publish_time") or "").strip() or None
            entry = (
                self.db.query(ScanEntry)
                .filter(
                    ScanEntry.platform == platform,
                    ScanEntry.external_item_id == normalized.external_item_id,
                )
                .first()
            )
            if entry is None:
                entry = ScanEntry(
                    platform=platform,
                    external_item_id=normalized.external_item_id,
                    canonical_url=normalized.canonical_url,
                    raw_url=item.raw_url,
                    title=item.title or "",
                    author=item.author,
                    content_type=item.content_type,
                    collection_kind=kind,
                    published_at=published,
                    scan_run_id=scan_run_id,
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                    first_seen_at=now,
                    last_seen_at=now,
                )
                self.db.add(entry)
                self.db.flush()
            else:
                entry.last_seen_at = now
                if item.title:
                    entry.title = item.title
                if item.author:
                    entry.author = item.author
                if item.content_type:
                    entry.content_type = item.content_type
                if published and not entry.published_at:
                    entry.published_at = published
                if scan_run_id:
                    entry.scan_run_id = scan_run_id
                # 不动 collection_kind / usefulness / extracted
            out.append(self._to_dict(entry))
        self.db.commit()
        return out

    def record_boundary(self, platform: str, items: list[ConnectorItem]) -> None:
        """首次（历史）扫描完成：标记 first_scan_done 并记最新一条为分界锚点。

        connector 在「勾选提取」(prepare_selected) 才建，但首扫只到「扫描展示」阶段、还没勾选，
        此时可能尚无 connector 行。为把 first_scan_done 落住（决定下次是 history 还是 incremental），
        缺失则建一行最小 connector。
        """
        connector = self._ensure_connector(platform)
        boundary_id = None
        if items:
            boundary_id = normalize_url(items[0].raw_url, platform).external_item_id
        connector.first_scan_done = True
        if boundary_id and not getattr(connector, "history_boundary_external_id", None):
            connector.history_boundary_external_id = boundary_id
        self.db.commit()

    # ---- 分类回写 ----

    def apply_classification(self, entries: list[dict[str, Any]]) -> int:
        """按 scan_entry_id 回写分类结果（usefulness/subcategory/reason/confidence/classified_at）。"""
        updated = 0
        now = datetime.utcnow()
        for payload in entries:
            entry_id = payload.get("scan_entry_id")
            if not entry_id:
                continue
            entry = self.db.get(ScanEntry, int(entry_id))
            if entry is None:
                continue
            entry.usefulness = payload.get("usefulness") or entry.usefulness
            entry.subcategory = payload.get("subcategory") or entry.subcategory
            if payload.get("reason"):
                entry.reason = str(payload.get("reason"))
            conf = payload.get("confidence")
            if conf is not None:
                try:
                    entry.confidence = float(conf)
                except (TypeError, ValueError):
                    pass
            entry.classified_at = now
            updated += 1
        if updated:
            self.db.commit()
        return updated

    # ---- candidate / extract 回填 ----

    def link_candidate(self, platform: str, candidate_id: int) -> None:
        """勾选提取时，把 candidate_id 回填到对应 ScanEntry（按 candidate 的 external_item_id 匹配）。"""
        candidate = self.db.get(CandidateItem, candidate_id)
        if candidate is None:
            return
        entry = (
            self.db.query(ScanEntry)
            .filter(
                ScanEntry.platform == (candidate.platform or platform),
                ScanEntry.external_item_id == candidate.external_item_id,
            )
            .first()
        )
        if entry is not None and entry.candidate_id != candidate_id:
            entry.candidate_id = candidate_id
            self.db.commit()

    def mark_extracted(self, candidate_id: int, raw_source_id: int) -> None:
        """提取成功后置 extracted=True + raw_source_id（按 candidate_id 反查 ScanEntry）。"""
        entry = self.db.query(ScanEntry).filter(ScanEntry.candidate_id == candidate_id).first()
        if entry is None:
            candidate = self.db.get(CandidateItem, candidate_id)
            if candidate is not None:
                entry = (
                    self.db.query(ScanEntry)
                    .filter(
                        ScanEntry.platform == candidate.platform,
                        ScanEntry.external_item_id == candidate.external_item_id,
                    )
                    .first()
                )
        if entry is not None:
            entry.extracted = True
            entry.raw_source_id = raw_source_id
            if entry.candidate_id is None:
                entry.candidate_id = candidate_id
            self.db.commit()

    # ---- 列表（含存量 backfill） ----

    def list_entries(self, platform: str, kind: str | None = None) -> list[dict[str, Any]]:
        """分类页加载：返回该平台（可选 kind）所有 ScanEntry。

        首次上线时存量 candidate 尚无 ScanEntry，这里按 candidate+ledger 一次性生成影子条目，
        保证「进历史分类页能看到已提取」。
        """
        self._backfill_from_candidates(platform)
        query = self.db.query(ScanEntry).filter(ScanEntry.platform == platform)
        if kind:
            query = query.filter(ScanEntry.collection_kind == kind)
        entries = query.order_by(ScanEntry.first_seen_at.asc(), ScanEntry.id.asc()).all()
        return [self._to_dict(e) for e in entries]

    def _backfill_from_candidates(self, platform: str) -> None:
        existing_ext = {
            ext for (ext,) in self.db.query(ScanEntry.external_item_id).filter(ScanEntry.platform == platform).all() if ext
        }
        candidates = self.db.query(CandidateItem).filter(CandidateItem.platform == platform).all()
        created = False
        for candidate in candidates:
            if candidate.external_item_id in existing_ext:
                continue
            metadata = json.loads(candidate.metadata_json or "{}")
            raw_source = self.db.query(RawSource).filter(RawSource.candidate_id == candidate.id).first()
            extracted = (
                metadata.get("doubao_extracted") is True
                or metadata.get("xiaohongshu_diandian_extracted") is True
                or raw_source is not None
            )
            entry = ScanEntry(
                platform=candidate.platform,
                external_item_id=candidate.external_item_id,
                canonical_url=candidate.canonical_url,
                raw_url=candidate.raw_url,
                title=candidate.title or "",
                author=candidate.author,
                content_type=candidate.content_type,
                collection_kind=HISTORY,
                usefulness=str(metadata.get("filter_usefulness")) if metadata.get("filter_usefulness") else None,
                subcategory=str(metadata.get("filter_subcategory")) if metadata.get("filter_subcategory") else None,
                reason=str(metadata.get("filter_reason") or ""),
                published_at=str(metadata.get("publish_time") or "").strip() or None,
                extracted=extracted,
                candidate_id=candidate.id,
                raw_source_id=raw_source.id if raw_source else None,
                metadata_json=candidate.metadata_json or "{}",
            )
            self.db.add(entry)
            existing_ext.add(candidate.external_item_id)
            created = True
        if created:
            self.db.commit()

    # ---- 序列化 ----

    @staticmethod
    def _to_dict(entry: ScanEntry) -> dict[str, Any]:
        try:
            metadata = json.loads(entry.metadata_json) if entry.metadata_json else {}
        except (TypeError, ValueError):
            metadata = {}
        return {
            "scan_entry_id": entry.id,
            "url": entry.raw_url,
            "canonical_url": entry.canonical_url,
            "external_item_id": entry.external_item_id,
            "title": entry.title,
            "author": entry.author,
            "platform": entry.platform,
            "content_type": entry.content_type,
            "collection_kind": entry.collection_kind,
            "usefulness": entry.usefulness,
            "subcategory": entry.subcategory,
            "reason": entry.reason,
            "confidence": entry.confidence,
            "published_at": entry.published_at,
            "extracted": bool(entry.extracted),
            "candidate_id": entry.candidate_id,
            "raw_source_id": entry.raw_source_id,
            "metadata": metadata,
        }
