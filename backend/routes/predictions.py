"""
routes/predictions.py  —  spoilage prediction endpoints
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import Prediction

router = APIRouter()


def _to_dict(p: Prediction) -> dict:
    d = {
        "id":                p.id,
        "reading_id":        p.reading_id,
        "days_remaining":    p.days_remaining,
        "freshness_state":   p.freshness_state,
        "alert_level":       p.alert_level,
        "engine_confidence": p.engine_confidence,
        "created_at":        p.created_at.isoformat(),
    }
    if p.reading:
        d.update({
            "fruit_type":      p.reading.fruit_type,
            "freshness_label": p.reading.freshness_label,
            "temperature":     p.reading.temperature,
            "humidity":        p.reading.humidity,
            "gas_level":       p.reading.gas_level,
        })
    return d


@router.get("/")
async def list_predictions(
    limit:       int           = Query(50, ge=1, le=500),
    alert_level: Optional[str] = Query(None, description="ok | warning | critical | spoiled"),
    db:          AsyncSession  = Depends(get_db),
):
    stmt = (
        select(Prediction)
        .options(selectinload(Prediction.reading))
        .order_by(desc(Prediction.created_at))
        .limit(limit)
    )
    if alert_level:
        stmt = stmt.where(Prediction.alert_level == alert_level.lower().strip())
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dict(p) for p in rows]


@router.get("/latest")
async def latest_prediction(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Prediction)
        .options(selectinload(Prediction.reading))
        .order_by(desc(Prediction.created_at))
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    return _to_dict(row) if row else {"message": "No predictions yet"}


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    """Count of predictions grouped by alert level."""
    stmt   = select(Prediction.alert_level, func.count(Prediction.id)).group_by(Prediction.alert_level)
    rows   = (await db.execute(stmt)).all()
    counts = {level: count for level, count in rows}
    for lvl in ("ok", "warning", "critical", "spoiled"):
        counts.setdefault(lvl, 0)
    return counts


@router.get("/{prediction_id}")
async def get_prediction(prediction_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Prediction)
        .options(selectinload(Prediction.reading))
        .where(Prediction.id == prediction_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Prediction {prediction_id} not found")
    return _to_dict(row)