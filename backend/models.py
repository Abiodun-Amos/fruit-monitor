"""
models.py
SQLAlchemy ORM table definitions.

fruit_readings  — raw sensor + ML data from ESP32
predictions     — spoilage engine output (one per reading)
alerts          — every notification dispatched
"""
from datetime import datetime
from sqlalchemy import Boolean, Float, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class FruitReading(Base):
    __tablename__ = "fruit_readings"

    id: Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str]       = mapped_column(String(50),  nullable=False)
    fruit_type: Mapped[str]      = mapped_column(String(50),  nullable=False)
    freshness_label: Mapped[str] = mapped_column(String(20),  nullable=False)  # fresh | rotten
    ml_confidence: Mapped[float] = mapped_column(Float,       nullable=False)
    temperature: Mapped[float]   = mapped_column(Float,       nullable=False)  # °C
    humidity: Mapped[float]      = mapped_column(Float,       nullable=False)  # %
    gas_level: Mapped[float]     = mapped_column(Float,       nullable=False)  # MQ-135 value
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    prediction: Mapped["Prediction"] = relationship(
        "Prediction", back_populates="reading",
        uselist=False, cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        "Alert", back_populates="reading", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return (f"<FruitReading id={self.id} fruit={self.fruit_type} "
                f"label={self.freshness_label} temp={self.temperature}°C>")


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    reading_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fruit_readings.id", ondelete="CASCADE"),
        nullable=False, unique=True   # one prediction per reading
    )
    days_remaining: Mapped[float]    = mapped_column(Float,      nullable=False)
    freshness_state: Mapped[str]     = mapped_column(String(20), nullable=False)  # good | marginal | spoiled
    alert_level: Mapped[str]         = mapped_column(String(20), nullable=False)  # ok | warning | critical
    engine_confidence: Mapped[float] = mapped_column(Float,      nullable=False)
    created_at: Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    reading: Mapped["FruitReading"] = relationship("FruitReading", back_populates="prediction")

    def __repr__(self):
        return (f"<Prediction id={self.id} days={self.days_remaining:.1f} "
                f"state={self.freshness_state} alert={self.alert_level}>")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    reading_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fruit_readings.id", ondelete="CASCADE"), nullable=False
    )
    alert_type: Mapped[str]    = mapped_column(String(20), nullable=False)  # warning | critical
    message: Mapped[str]       = mapped_column(Text,       nullable=False)
    sent_at: Mapped[datetime]  = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    reading: Mapped["FruitReading"] = relationship("FruitReading", back_populates="alerts")

    def __repr__(self):
        return f"<Alert id={self.id} type={self.alert_type} sent={self.sent_at}>"
    
class FruitSession(Base):
    """
    One active row per fruit being monitored (device_id + fruit_type).
    Tracks inventory: when it arrived, latest status, how long it has been there.
    Marked inactive (is_active=False) when the fruit is removed from storage.
    """
    __tablename__ = "fruit_sessions"

    id: Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str]  = mapped_column(String(50),  nullable=False)
    fruit_type: Mapped[str] = mapped_column(String(50),  nullable=False)

    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen:  Mapped[datetime] = mapped_column(DateTime, nullable=False)

    reading_count: Mapped[int]   = mapped_column(Integer, default=1,    nullable=False)
    last_freshness: Mapped[str]  = mapped_column(String(20),            nullable=False)
    last_days_remaining: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_alert_level: Mapped[str]      = mapped_column(String(20), default="ok", nullable=False)
    is_active: Mapped[bool]            = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return (f"<FruitSession {self.fruit_type} device={self.device_id} "
                f"active={self.is_active} days={self.last_days_remaining:.1f}>")
    

class CameraLastLabel(Base):
    """
    Persists the most recent CONFIDENT camera classification so it
    survives backend restarts. Without this, every restart wipes the
    in-memory camera_state and the pipeline falls back to the
    DevKitC's hardcoded default label.
    """
    __tablename__ = "camera_last_label"

    id: Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str]    = mapped_column(String(50), nullable=False, unique=True)
    ml_label: Mapped[str]     = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)