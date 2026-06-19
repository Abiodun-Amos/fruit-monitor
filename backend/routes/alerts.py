"""
routes/alerts.py  —  alert endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Alert

router = APIRouter()


def _to_dict(a: Alert) -> dict:
    return {
        "id":           a.id,
        "reading_id":   a.reading_id,
        "alert_type":   a.alert_type,
        "message":      a.message,
        "sent_at":      a.sent_at.isoformat(),
        "acknowledged": a.acknowledged,
    }


@router.get("/")
async def list_alerts(
    limit:               int  = 50,
    unacknowledged_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Alert).order_by(desc(Alert.sent_at)).limit(limit)
    if unacknowledged_only:
        stmt = stmt.where(Alert.acknowledged == False)  # noqa: E712
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_dict(a) for a in rows]


@router.patch("/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    """Mark an alert as seen."""
    stmt = select(Alert).where(Alert.id == alert_id)
    row  = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    row.acknowledged = True
    await db.commit()
    return {"id": alert_id, "acknowledged": True}