from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.connectors import MockConnector
from app.connectors.base import BaseConnector, ConnectorItem
from app.models import CandidateItem, Connector, RawSource, ScanLog, SyncLedgerItem
from app.services.statuses import PENDING_CLASSIFICATION
from app.services.url_normalizer import normalize_url


@dataclass
class ScanResult:
    connector_id: int
    scan_run_id: str
    scanned_count: int = 0
    new_count: int = 0
    duplicate_in_run_count: int = 0
    boundary_hit: bool = False
    boundary_url: str | None = None
    first_new_url: str | None = None
    candidate_ids: list[int] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "scan_run_id": self.scan_run_id,
            "scanned_count": self.scanned_count,
            "new_count": self.new_count,
            "duplicate_in_run_count": self.duplicate_in_run_count,
            "boundary_hit": self.boundary_hit,
            "boundary_url": self.boundary_url,
            "first_new_url": self.first_new_url,
            "candidate_ids": self.candidate_ids,
            "messages": self.messages,
        }


class SyncService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def scan_connector(self, connector_id: int) -> ScanResult:
        connector = self.db.get(Connector, connector_id)
        if connector is None:
            raise ValueError(f"Connector {connector_id} not found")

        connector_impl = self._connector_impl(connector)
        if not await connector_impl.login_check():
            raise ValueError(f"Connector {connector.name} login check failed")

        scan_run_id = f"sync_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        result = ScanResult(connector_id=connector_id, scan_run_id=scan_run_id)
        self._log(connector.id, scan_run_id, "info", f"Started scan for {connector.name}")

        items = await connector_impl.scan_until_boundary(
            {
                "last_boundary_url": connector.last_boundary_url,
                "last_boundary_external_id": connector.last_boundary_external_id,
                "last_top_url": connector.last_top_url,
                "max_scan_pages": connector.max_scan_pages,
            }
        )

        existing_external_keys = self._existing_external_keys()
        existing_canonical_urls = self._existing_canonical_urls()
        existing_raw_external_keys = self._existing_raw_external_keys()
        existing_raw_canonical_urls = self._existing_raw_canonical_urls()
        seen_external_keys: set[tuple[str, str]] = set()
        seen_canonical_urls: set[str] = set()

        for item in items:
            result.scanned_count += 1
            normalized = normalize_url(item.raw_url, item.platform)
            external_key = (normalized.platform, normalized.external_item_id)

            if external_key in seen_external_keys or normalized.canonical_url in seen_canonical_urls:
                result.duplicate_in_run_count += 1
                message = f"Skipped duplicate in current scan: {normalized.canonical_url}"
                result.messages.append(message)
                self._log(connector.id, scan_run_id, "info", message)
                continue

            boundary_hit = (
                external_key in existing_external_keys
                or normalized.canonical_url in existing_canonical_urls
                or external_key in existing_raw_external_keys
                or normalized.canonical_url in existing_raw_canonical_urls
            )
            if boundary_hit:
                result.boundary_hit = True
                result.boundary_url = normalized.canonical_url
                connector.last_boundary_url = normalized.canonical_url
                connector.last_boundary_external_id = normalized.external_item_id
                self._mark_boundary_seen(normalized.platform, normalized.external_item_id, normalized.canonical_url)
                message = f"Boundary hit, stopped at {normalized.canonical_url}"
                result.messages.append(message)
                self._log(connector.id, scan_run_id, "info", message)
                break

            seen_external_keys.add(external_key)
            seen_canonical_urls.add(normalized.canonical_url)
            candidate = self._create_candidate(connector, item, normalized)
            self.db.flush()
            ledger_item = SyncLedgerItem(
                connector_id=connector.id,
                platform=normalized.platform,
                external_item_id=normalized.external_item_id,
                canonical_url=normalized.canonical_url,
                raw_url=normalized.raw_url,
                scan_run_id=scan_run_id,
                candidate_id=candidate.id,
                is_boundary_hit=False,
            )
            self.db.add(ledger_item)
            result.new_count += 1
            result.candidate_ids.append(candidate.id)
            if result.first_new_url is None:
                result.first_new_url = normalized.canonical_url
                connector.last_top_url = normalized.canonical_url
            self._log(connector.id, scan_run_id, "info", f"Created candidate {candidate.id}: {candidate.title}")

        connector.last_successful_scan_at = datetime.now(timezone.utc)
        if not result.boundary_hit and result.new_count > 0:
            result.messages.append("No historical boundary was hit in this scan.")
            self._log(connector.id, scan_run_id, "warning", "No historical boundary was hit in this scan.")
        self.db.commit()
        return result

    async def import_items(self, connector: Connector, items: list[ConnectorItem], scan_run_id_prefix: str = "import") -> ScanResult:
        scan_run_id = f"{scan_run_id_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        result = ScanResult(connector_id=connector.id, scan_run_id=scan_run_id)
        self._log(connector.id, scan_run_id, "info", f"Started import for {connector.name}")
        existing_external_keys = self._existing_external_keys()
        existing_canonical_urls = self._existing_canonical_urls()
        seen_external_keys: set[tuple[str, str]] = set()
        seen_canonical_urls: set[str] = set()

        for item in items:
            result.scanned_count += 1
            normalized = normalize_url(item.raw_url, item.platform)
            external_key = (normalized.platform, normalized.external_item_id)
            if (
                external_key in existing_external_keys
                or normalized.canonical_url in existing_canonical_urls
                or external_key in seen_external_keys
                or normalized.canonical_url in seen_canonical_urls
            ):
                result.duplicate_in_run_count += 1
                continue
            seen_external_keys.add(external_key)
            seen_canonical_urls.add(normalized.canonical_url)
            candidate = self._create_candidate(connector, item, normalized)
            self.db.flush()
            self.db.add(
                SyncLedgerItem(
                    connector_id=connector.id,
                    platform=normalized.platform,
                    external_item_id=normalized.external_item_id,
                    canonical_url=normalized.canonical_url,
                    raw_url=normalized.raw_url,
                    scan_run_id=scan_run_id,
                    candidate_id=candidate.id,
                )
            )
            result.new_count += 1
            result.candidate_ids.append(candidate.id)
            if result.first_new_url is None:
                result.first_new_url = normalized.canonical_url
                connector.last_top_url = normalized.canonical_url
        connector.last_successful_scan_at = datetime.now(timezone.utc)
        self.db.commit()
        return result

    def _connector_impl(self, connector: Connector) -> BaseConnector:
        if connector.connector_type == "mock":
            return MockConnector()
        raise ValueError(f"Unsupported connector type: {connector.connector_type}")

    def _create_candidate(self, connector: Connector, item: ConnectorItem, normalized) -> CandidateItem:
        candidate = CandidateItem(
            source_type="active_connector",
            platform=normalized.platform,
            connector_id=connector.id,
            external_item_id=normalized.external_item_id,
            canonical_url=normalized.canonical_url,
            raw_url=normalized.raw_url,
            title=item.title,
            author=item.author,
            content_type=item.content_type,
            metadata_json=json.dumps(item.metadata, ensure_ascii=False),
            status=PENDING_CLASSIFICATION,
        )
        self.db.add(candidate)
        return candidate

    def _existing_external_keys(self) -> set[tuple[str, str]]:
        rows = self.db.query(SyncLedgerItem.platform, SyncLedgerItem.external_item_id).all()
        return {(platform, external_item_id) for platform, external_item_id in rows}

    def _existing_canonical_urls(self) -> set[str]:
        rows = self.db.query(SyncLedgerItem.canonical_url).all()
        return {row[0] for row in rows}

    def _existing_raw_external_keys(self) -> set[tuple[str, str]]:
        rows = self.db.query(RawSource.platform, RawSource.external_item_id).all()
        return {(platform, external_item_id) for platform, external_item_id in rows}

    def _existing_raw_canonical_urls(self) -> set[str]:
        rows = self.db.query(RawSource.canonical_url).all()
        return {row[0] for row in rows}

    def _mark_boundary_seen(self, platform: str, external_item_id: str, canonical_url: str) -> None:
        ledger_item = (
            self.db.query(SyncLedgerItem)
            .filter(
                or_(
                    (SyncLedgerItem.platform == platform) & (SyncLedgerItem.external_item_id == external_item_id),
                    SyncLedgerItem.canonical_url == canonical_url,
                )
            )
            .first()
        )
        if ledger_item:
            ledger_item.last_seen_at = datetime.now(timezone.utc)
            ledger_item.is_boundary_hit = True

    def _log(self, connector_id: int | None, scan_run_id: str, level: str, message: str) -> None:
        self.db.add(
            ScanLog(
                connector_id=connector_id,
                scan_run_id=scan_run_id,
                level=level,
                message=message,
            )
        )
