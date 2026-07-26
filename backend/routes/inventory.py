"""
routes/inventory.py
Fruit inventory — what is currently in storage, when it arrived,
and how long it has been there.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import FruitSession
from ..device_state import device_state

router = APIRouter()

# A session is "active" if seen within this window. Kept short because the
# camera captures every 30s-1min — this allows for one or two missed
# cycles before treating a fruit as no longer physically present, rather
# than the old 10-minute window which let stale entries linger far past
# when the camera had actually stopped confirming them.
ACTIVE_WINDOW_MINUTES = 2

ONLINE_WINDOW_SECONDS = 90  # no reading in this window = device offline


def _to_dict(s: FruitSession) -> dict:
    now              = datetime.utcnow()
    total_seconds    = int((now - s.first_seen).total_seconds())
    hours            = total_seconds // 3600
    minutes          = (total_seconds % 3600) // 60

    return {
        "id":                      s.id,
        "device_id":               s.device_id,
        "fruit_type":              s.fruit_type,
        "first_seen":              s.first_seen.isoformat(),
        "last_seen":               s.last_seen.isoformat(),
        "reading_count":           s.reading_count,
        "last_freshness":          s.last_freshness,
        "last_days_remaining":     round(s.last_days_remaining, 1),
        "last_alert_level":        s.last_alert_level,
        "time_in_storage_hours":   hours,
        "time_in_storage_minutes": minutes,
        "time_in_storage_str":     f"{hours}h {minutes}m" if hours >= 1 else f"{minutes}m",
        "is_active":               s.is_active,
    }


async def _close_stale_sessions(db: AsyncSession, cutoff: datetime) -> None:
    """
    Formally closes any session the camera hasn't reconfirmed since the
    cutoff — not just hides it from one query's results, actually flips
    is_active so it stops counting as 'in storage' everywhere. Shared by
    both routes below so they can never disagree with each other.
    """
    stmt = (
        select(FruitSession)
        .where(FruitSession.is_active == True)   # noqa: E712
        .where(FruitSession.last_seen < cutoff)
    )
    stale_rows = (await db.execute(stmt)).scalars().all()
    for row in stale_rows:
        row.is_active = False
    if stale_rows:
        await db.commit()


@router.get("/")
async def get_inventory(db: AsyncSession = Depends(get_db)):
    """Fruits currently in storage (seen within the active window)."""
    cutoff = datetime.utcnow() - timedelta(minutes=ACTIVE_WINDOW_MINUTES)
    await _close_stale_sessions(db, cutoff)

    stmt = (
        select(FruitSession)
        .where(FruitSession.is_active == True)   # noqa: E712
        .where(FruitSession.last_seen >= cutoff)
        .order_by(FruitSession.first_seen)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/summary")
async def inventory_summary(db: AsyncSession = Depends(get_db)):
    """Count of fruits seen recently (currently active), by alert level."""
    cutoff = datetime.utcnow() - timedelta(minutes=ACTIVE_WINDOW_MINUTES)
    await _close_stale_sessions(db, cutoff)

    stmt = (
        select(FruitSession.last_alert_level, func.count(FruitSession.id))
        .where(FruitSession.is_active == True)   # noqa: E712
        .where(FruitSession.last_seen >= cutoff)
        .group_by(FruitSession.last_alert_level)
    )
    rows   = (await db.execute(stmt)).all()
    counts = {level: count for level, count in rows}
    for lvl in ("ok", "warning", "critical", "spoiled"):
        counts.setdefault(lvl, 0)
    return {"total": sum(counts.values()), "by_alert_level": counts}


@router.get("/history")
async def session_history(db: AsyncSession = Depends(get_db)):
    """All sessions including removed ones — full storage history."""
    stmt = select(FruitSession).order_by(FruitSession.first_seen.desc())
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dict(r) for r in rows]


@router.delete("/{session_id}")
async def remove_fruit(session_id: int, db: AsyncSession = Depends(get_db)):
    """Mark a fruit as removed from storage — closes the session."""
    stmt = select(FruitSession).where(FruitSession.id == session_id)
    row  = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    row.is_active = False
    await db.commit()
    return {"id": session_id, "fruit_type": row.fruit_type, "removed": True}


@router.get("/device-status")
async def get_device_status():
    """Latest paused/resumed status per device."""
    return device_state.get_all()


@router.get("/system-status")
async def system_status(db: AsyncSession = Depends(get_db)):
    stmt = select(func.max(FruitSession.last_seen))
    last_seen = (await db.execute(stmt)).scalar_one_or_none()

    online = False
    if last_seen:
        now = datetime.utcnow()
        last_seen_naive = last_seen.replace(tzinfo=None) if last_seen.tzinfo else last_seen
        online = (now - last_seen_naive).total_seconds() <= ONLINE_WINDOW_SECONDS

    return {
        "online": online,
        "last_seen": last_seen.isoformat() if last_seen else None,
    }