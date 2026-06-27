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


async def auto_distill_job() -> None:
    """Distill pending RawSources into wiki pages every 30 minutes."""
    db = SessionLocal()
    try:
        from app.services.auto_distill_service import AutoDistillService
        pages = await AutoDistillService(db).distill_pending(limit=5)
        if pages:
            logger.info(f"Auto-distill: created {len(pages)} wiki pages")
    except Exception as e:
        logger.warning(f"Auto-distill failed: {e}")
    finally:
        db.close()


async def push_check_job() -> None:
    """Check every minute if it's time to push."""
    from datetime import datetime
    db = SessionLocal()
    try:
        from app.models import PushSettings
        settings = db.query(PushSettings).first()
        if not settings or settings.is_paused or not settings.push_time:
            return
        now = datetime.now()
        current_day = now.isoweekday()  # 1=Mon, 7=Sun
        current_time = now.strftime("%H:%M")
        # Check if today is a push day
        push_days = [int(d) for d in (settings.push_days or "").split(",") if d.isdigit()]
        if current_day not in push_days:
            return
        # Check if current time matches any push time
        push_times = [t.strip() for t in (settings.push_time or "").split(",") if t.strip()]
        if current_time not in push_times:
            return
        # Generate and store push
        from app.services.push_scheduler_service import PushSchedulerService
        items = await PushSchedulerService(db).generate_push_items()
        if items:
            logger.info(f"Push triggered at {current_time}: {len(items)} items")
    except Exception as e:
        logger.warning(f"Push check failed: {e}")
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
    scheduler.add_job(auto_distill_job, "interval", minutes=30, id="auto_distill", replace_existing=True)
    scheduler.add_job(push_check_job, "interval", minutes=1, id="push_check", replace_existing=True)
    _register_connector_schedules()
    scheduler.start()


def shutdown_scheduler() -> None:
    scheduler.shutdown(wait=False)


def _register_connector_schedules() -> None:
    """Register per-connector sync jobs based on their auto_sync_cron."""
    db = SessionLocal()
    try:
        connectors = db.query(Connector).filter(Connector.auto_sync_enabled == True).all()
        for conn in connectors:
            register_connector_job(conn.id, conn.auto_sync_cron)
    finally:
        db.close()


def register_connector_job(connector_id: int, cron_expr: str | None) -> None:
    """Register or update a single connector's sync schedule."""
    if not cron_expr:
        return
    parts = cron_expr.split()
    if len(parts) != 5:
        return
    minute, hour, dom, month, dow = parts
    job_id = f"sync_connector_{connector_id}"
    try:
        scheduler.add_job(
            _sync_single_connector,
            "cron",
            minute=minute,
            hour=hour,
            day=dom,
            month=month,
            day_of_week=dow,
            args=[connector_id],
            id=job_id,
            replace_existing=True,
        )
    except Exception:
        pass


async def _sync_single_connector(connector_id: int) -> None:
    db = SessionLocal()
    try:
        await SyncService(db).scan_connector(connector_id)
        logger.info(f"Scheduled sync success: connector {connector_id}")
    except Exception as e:
        logger.warning(f"Scheduled sync failed: connector {connector_id}: {e}")
    finally:
        db.close()
