"""
routes/fruits.py  —  sensor reading endpoints
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import FruitReading

router = APIRouter()


def _to_dict(r: FruitReading) -> dict:
    return {
        "id":              r.id,
        "device_id":       r.device_id,
        "fruit_type":      r.fruit_type,
        "freshness_label": r.freshness_label,
        "ml_confidence":   r.ml_confidence,
        "temperature":     r.temperature,
        "humidity":        r.humidity,
        "gas_level":       r.gas_level,
        "received_at":     r.received_at.isoformat(),
    }


@router.get("/")
async def list_readings(
    limit: int           = Query(50, ge=1, le=500),
    fruit: Optional[str] = Query(None, description="e.g. 'banana'"),
    db:    AsyncSession  = Depends(get_db),
):
    """Recent sensor readings, newest first."""
    stmt = select(FruitReading).order_by(desc(FruitReading.received_at)).limit(limit)
    if fruit:
        stmt = stmt.where(FruitReading.fruit_type == fruit.lower().strip())
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/latest")
async def latest_reading(db: AsyncSession = Depends(get_db)):
    """The single most recent reading."""
    stmt = select(FruitReading).order_by(desc(FruitReading.received_at)).limit(1)
    row  = (await db.execute(stmt)).scalar_one_or_none()
    return _to_dict(row) if row else {"message": "No readings yet"}


@router.get("/{reading_id}")
async def get_reading(reading_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(FruitReading).where(FruitReading.id == reading_id)
    row  = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Reading {reading_id} not found")
    return _to_dict(row)