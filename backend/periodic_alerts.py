"""
periodic_alerts.py
Sends a "still X, Y days remaining" status update every 30 minutes for
each currently-active fruit session — IN ADDITION to the immediate
state-change alerts already sent from mqtt_client.py.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from .config import settings
from .database import AsyncSessionLocal
from .models import FruitSession
from alerts.notifier import AlertNotifier

logger = logging.getLogger(__name__)

PERIODIC_INTERVAL_SECONDS = 30 * 60
ACTIVE_WINDOW_MINUTES     = 10   # only genuinely live sessions — avoids reviving old spam

_notifier = AlertNotifier(settings)
_task: asyncio.Task | None = None


async def _loop():
    while True:
        try:
            await asyncio.sleep(PERIODIC_INTERVAL_SECONDS)
            await _send_periodic_updates()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Periodic alert loop error: %s", exc)


async def _send_periodic_updates():
    cutoff = datetime.utcnow() - timedelta(minutes=ACTIVE_WINDOW_MINUTES)
    async with AsyncSessionLocal() as db:
        stmt = select(FruitSession).where(
            FruitSession.is_active == True,   # noqa: E712
            FruitSession.last_seen >= cutoff,
        )
        sessions = (await db.execute(stmt)).scalars().all()

    if not sessions:
        logger.info("Periodic update: no active sessions right now")
        return

    for s in sessions:
        if s.last_alert_level == "spoiled":
            message = f"{s.fruit_type.capitalize()} · already spoiled — discard"
        else:
            message = (f"{s.fruit_type.capitalize()} · still {s.last_freshness} · "
                       f"{s.last_days_remaining:.1f} days remaining")
        await _notifier.send(
            message        = message,
            alert_level    = s.last_alert_level,
            fruit_name     = s.fruit_type.capitalize(),
            days_remaining = s.last_days_remaining,
        )
    logger.info("Periodic update sent for %d active session(s)", len(sessions))


def start():
    global _task
    _task = asyncio.get_running_loop().create_task(_loop())
    logger.info("Periodic alert task started (every 30 min)")


def stop():
    if _task:
        _task.cancel()