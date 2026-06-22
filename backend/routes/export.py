"""
routes/export.py
CSV export of all readings + predictions for reporting and analysis.
"""
import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import Prediction

router = APIRouter()


@router.get("/csv")
async def export_csv(db: AsyncSession = Depends(get_db)):
    """Download all readings + predictions as a CSV file."""
    stmt = (
        select(Prediction)
        .options(selectinload(Prediction.reading))
        .order_by(Prediction.created_at)
    )
    rows = (await db.execute(stmt)).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "timestamp", "device_id", "fruit_type", "freshness",
        "ml_confidence", "temperature_c", "humidity_pct", "gas_raw",
        "days_remaining", "freshness_state", "alert_level", "engine_confidence",
    ])

    for p in rows:
        r = p.reading
        writer.writerow([
            p.created_at.isoformat() if p.created_at else "",
            r.device_id if r else "",
            r.fruit_type if r else "",
            r.freshness_label if r else "",
            r.ml_confidence if r else "",
            r.temperature if r else "",
            r.humidity if r else "",
            r.gas_level if r else "",
            p.days_remaining,
            p.freshness_state,
            p.alert_level,
            p.engine_confidence,
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fruit_monitor_data.csv"},
    )