"""
routes/inventory.py
Fruit inventory — what is currently in storage, when it arrived,
and how long it has been there.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import FruitSession

router = APIRouter()


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


@router.get("/")
async def get_inventory(db: AsyncSession = Depends(get_db)):
    """All fruits currently in storage, ordered by arrival time."""
    stmt = (
        select(FruitSession)
        .where(FruitSession.is_active == True)   # noqa: E712
        .order_by(FruitSession.first_seen)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/summary")
async def inventory_summary(db: AsyncSession = Depends(get_db)):
    """Total count + breakdown by alert level."""
    stmt = (
        select(FruitSession.last_alert_level, func.count(FruitSession.id))
        .where(FruitSession.is_active == True)   # noqa: E712
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