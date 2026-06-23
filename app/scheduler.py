from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import SessionLocal
from app.models import Connector, ScanLog
from app.services.sync_service import SyncService

logger = logging.getLogger("starmind.scheduler")

scheduler = AsyncIOScheduler()

_MAX_RETRIES = 3


async def daily_sync_job() -> None:
    """Sync all connectors with auto_sync_enabled at midnight."""
    db = SessionLocal()
    try:
        connectors = db.query(Connector).filter(Connector.auto_sync_enabled == True).all()
        for connector in connectors:
            try:
                await SyncService(db).scan_connector(connector.id)
                logger.info(f"Auto-sync success: connector {connector.id} ({connector.platform})")
            except Exception as e:
                logger.warning(f"Auto-sync failed: connector {connector.id}: {e}")
                db.add(ScanLog(connector_id=connector.id, scan_run_id=f"auto_retry_{connector.id}", level="error", message=str(e)[:500]))
                db.commit()
                # Schedule retry
                _schedule_retry(connector.id, attempt=1)
    finally:
        db.close()


def _schedule_retry(connector_id: int, attempt: int) -> None:
    if attempt > _MAX_RETRIES:
        return
    from datetime import datetime
    run_date = datetime.now() + timedelta(minutes=30 * attempt)
    job_id = f"retry_sync_{connector_id}_{attempt}"
    try:
        scheduler.add_job(
            _retry_sync,
            "date",
            run_date=run_date,
            args=[connector_id, attempt],
            id=job_id,
            replace_existing=True,
        )
    except Exception:
        pass


async def _retry_sync(connector_id: int, attempt: int) -> None:
    db = SessionLocal()
    try:
        await SyncService(db).scan_connector(connector_id)
        logger.info(f"Retry sync success: connector {connector_id} (attempt {attempt})")
    except Exception as e:
        logger.warning(f"Retry sync failed: connector {connector_id} (attempt {attempt}): {e}")
        if attempt < _MAX_RETRIES:
            _schedule_retry(connector_id, attempt + 1)
        else:
            connector = db.get(Connector, connector_id)
            if connector:
                connector.status = "sync_failed"
                db.commit()
    finally:
        db.close()


def init_scheduler() -> None:
    scheduler.add_job(daily_sync_job, "cron", hour=0, minute=0, id="daily_sync", replace_existing=True)
    scheduler.start()


def shutdown_scheduler() -> None:
    scheduler.shutdown(wait=False)
