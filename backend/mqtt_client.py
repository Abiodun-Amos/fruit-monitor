"""
mqtt_client.py
Subscribes to HiveMQ Cloud (TLS), runs the spoilage engine on
every incoming reading, saves to DB, and dispatches alerts.

Expected MQTT payload from ESP32 (topic: fruit/sensor/data):
{
    "device_id":  "esp32-s3-01",
    "ml_label":   "freshbanana",   <- full class-map label
    "confidence": 0.97,
    "temperature": 25.5,           <- °C
    "humidity":   65.2,            <- %RH
    "gas_raw":    1500,            <- MQ-135 ADC value (0-4095)
    "ts":         1718187600       <- Unix timestamp (optional)
}
"""
import asyncio
import json
import logging
import ssl
from datetime import datetime

import httpx
import paho.mqtt.client as mqtt
from sqlalchemy import and_, select as sa_select

from .camera_state import camera_state
from .config import settings
from .database import AsyncSessionLocal
from .device_state import device_state
from .inference import classify_image
from .models import Alert, CameraLastLabel, FruitReading, FruitSession, Prediction

# Absolute imports — works when uvicorn runs from fruit_monitor/
from spoilage_engine.baselines import is_fresh_label
from spoilage_engine.engine import (
    SensorReading as EngineSensorReading,
    SpoilagePredictor,
)
from alerts.notifier import AlertNotifier

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────
GAS_ADC_MAX = 4095.0

# One shared predictor — stateless, safe to reuse across messages
_predictor = SpoilagePredictor(
    warning_days=settings.ALERT_DAYS_THRESHOLD,
    critical_days=settings.CRITICAL_DAYS_THRESHOLD,
)


# ── Helpers ───────────────────────────────────────────────────

def _normalise_gas(raw: float) -> float:
    """MQ-135 raw ADC (0–4095) → 0.0–1.0."""
    return max(0.0, min(float(raw) / GAS_ADC_MAX, 1.0))


def _parse_ml_label(ml_label: str):
    """
    'freshbanana' → ('banana', 'fresh')
    'rottenguava' → ('guava',  'rotten')
    """
    label = ml_label.lower().strip()
    fruit_type = label.replace("fresh", "").replace("rotten", "")
    freshness  = "fresh" if is_fresh_label(label) else "rotten"
    return fruit_type, freshness


# ── MQTT Client ───────────────────────────────────────────────

class MQTTClient:
    """
    Paho-mqtt wrapped with an asyncio queue so the MQTT thread
    and FastAPI's event loop never conflict.
    """

    def __init__(self):
        self._client = mqtt.Client(client_id="fruit-monitor-backend", clean_session=True)
        self._queue: asyncio.Queue        = asyncio.Queue()
        self._loop:  asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task   | None = None
        self._notifier = AlertNotifier(settings)

        self._configure_tls()
        self._bind_callbacks()

    # ── Setup ─────────────────────────────────────────────────

    def _configure_tls(self):
        ctx = ssl.create_default_context()
        self._client.tls_set_context(ctx)
        self._client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

    def _bind_callbacks(self):
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

    # ── Paho callbacks (paho's thread) ────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected → %s", settings.MQTT_BROKER_HOST)
            client.subscribe(settings.MQTT_TOPIC_SENSOR, qos=1)
            logger.info("Subscribed to %s", settings.MQTT_TOPIC_SENSOR)
            client.subscribe(settings.MQTT_TOPIC_CAMERA, qos=1)
            logger.info("Subscribed to %s", settings.MQTT_TOPIC_CAMERA)
            client.subscribe(settings.MQTT_TOPIC_DEVICE_STATUS, qos=1)
            logger.info("Subscribed to %s", settings.MQTT_TOPIC_DEVICE_STATUS)
        else:
            logger.error("MQTT connection refused (rc=%d). Check credentials.", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly (rc=%d)", rc)

    def _on_message(self, client, userdata, msg):
        """Decode JSON and route to the async queue with topic context."""
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("Bad payload on %s: %s", msg.topic, exc)
            return

        envelope = {"topic": msg.topic, "payload": payload}
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._queue.put(envelope), self._loop)

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Called inside FastAPI lifespan (async context)."""
        self._loop = asyncio.get_running_loop()
        self._loop.create_task(self._restore_camera_state())
        self._client.connect_async(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT)
        self._client.loop_start()
        self._worker_task = self._loop.create_task(self._process_queue())
        logger.info("MQTT client started")

    async def _restore_camera_state(self):
        """On startup, reload the last known camera label so we don't
        fall back to the hardcoded default just because the backend
        process restarted."""
        async with AsyncSessionLocal() as db:
            try:
                rows = (await db.execute(sa_select(CameraLastLabel))).scalars().all()
                for row in rows:
                    camera_state.update(row.ml_label, row.confidence)
                    logger.info("Restored camera state → %s (%.2f) from %s",
                                row.ml_label, row.confidence, row.updated_at)
            except Exception as exc:
                logger.warning("Could not restore camera state (fine on first run): %s", exc)
    # ── Async pipeline ────────────────────────────────────────

    async def _process_queue(self):
        """Drain the queue, routing EACH message to exactly ONE handler."""
        while True:
            try:
                envelope = await self._queue.get()
                topic   = envelope.get("topic", "")
                payload = envelope.get("payload", {})

                if topic == settings.MQTT_TOPIC_CAMERA:
                    await self._handle_camera(payload)
                elif topic == settings.MQTT_TOPIC_DEVICE_STATUS:
                    self._handle_status(payload)
                else:
                    await self._handle_message(payload)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Queue processor error: %s", exc)

    async def _handle_message(self, payload: dict):
        """validate → normalise → engine → DB → publish → alert."""

        # ── 1. Validate ───────────────────────────────────────
        required = {"device_id", "ml_label", "confidence", "temperature", "humidity", "gas_raw"}
        missing  = required - set(payload.keys())
        if missing:
            logger.warning("Dropping message — missing fields: %s", missing)
            return

        # ── 2. Parse & normalise ──────────────────────────────
        # Option A: prefer the latest camera classification if available;
        # fall back to the device's own label (useful before camera is live)
        cam_label, cam_conf = camera_state.get()
        if cam_label:
            ml_label      = cam_label
            ml_confidence = cam_conf
        else:
            ml_label      = str(payload["ml_label"])
            ml_confidence = float(payload["confidence"])

        fruit_type, freshness = _parse_ml_label(ml_label)
        gas_raw               = float(payload["gas_raw"])
        gas_normalised        = _normalise_gas(gas_raw)

        try:
            engine_reading = EngineSensorReading(
                temperature_c  = float(payload["temperature"]),
                humidity_pct   = float(payload["humidity"]),
                gas_normalised = gas_normalised,
                ml_label       = ml_label,
                ml_confidence  = ml_confidence,
                timestamp      = datetime.utcnow(),
            )
        except (ValueError, TypeError) as exc:
            logger.error("Invalid SensorReading values: %s | payload=%s", exc, payload)
            return

        # ── 3. Run spoilage engine ────────────────────────────
        try:
            result = _predictor.predict(engine_reading)
        except Exception as exc:
            logger.error("SpoilagePredictor error: %s", exc)
            return

        logger.info(
            "[%s] %s/%s → %.1f days | %s | conf=%.2f",
            payload["device_id"], fruit_type, freshness,
            result.days_remaining, result.alert_level.value, result.confidence,
        )

        # ── 4. Persist to DB (and work out alert-worthiness) ──
        needs_alert = False
        async with AsyncSessionLocal() as db:
            try:
                reading_row = FruitReading(
                    device_id       = str(payload["device_id"]),
                    fruit_type      = fruit_type,
                    freshness_label = freshness,
                    ml_confidence   = float(payload["confidence"]),
                    temperature     = float(payload["temperature"]),
                    humidity        = float(payload["humidity"]),
                    gas_level       = gas_raw,
                    received_at     = datetime.utcnow(),
                )
                db.add(reading_row)
                await db.flush()      # get reading_row.id without committing

                db.add(Prediction(
                    reading_id        = reading_row.id,
                    days_remaining    = result.days_remaining,
                    freshness_state   = result.freshness_state.value,
                    alert_level       = result.alert_level.value,
                    engine_confidence = result.confidence,
                ))

                # previous_alert_level is the state BEFORE this reading —
                # None if this is a brand new session
                previous_alert_level = await self._upsert_session(
                    db, str(payload["device_id"]), fruit_type, freshness, result
                )

                # Alert on ANY state change (including into "ok") — but
                # never repeat the same state twice in a row
                needs_alert = result.alert_level.value != previous_alert_level

                if needs_alert:
                    db.add(Alert(
                        reading_id = reading_row.id,
                        alert_type = result.alert_level.value,
                        message    = result.message,
                    ))

                await db.commit()

            except Exception as exc:
                await db.rollback()
                logger.exception("DB write failed: %s", exc)
                return   # don't send downstream notifications if DB failed

        # ── 5. Publish prediction back to MQTT ────────────────
        self._publish_prediction(result, payload["device_id"])

        # ── 6. Send alerts (Telegram + Email) ─────────────────
        if needs_alert:
            await self._notifier.send(
                message        = result.message,
                alert_level    = result.alert_level.value,
                fruit_name     = result.fruit_name,
                days_remaining = result.days_remaining,
            )

    async def _handle_camera(self, payload: dict):
        """
        Camera message: { "device_id": "...", "image_url": "https://..." }
        Download the image, run inference, cache the result for the
        sensor pipeline to use (Option A).
        """
        image_url = payload.get("image_url")
        if not image_url:
            logger.warning("Camera message missing image_url: %s", payload)
            return

        logger.info("Camera image received: %s", image_url)

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                image_bytes = resp.content
        except Exception as exc:
            logger.error("Failed to download camera image: %s", exc)
            return

        try:
            loop   = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, classify_image, image_bytes)
        except Exception as exc:
            logger.error("Camera inference failed: %s", exc)
            return

        MIN_CONFIDENCE = 0.60   # tune this once you have real fruit test data

        if result["confidence"] < MIN_CONFIDENCE:
            logger.info("Low-confidence camera result ignored → %s (%.2f)",
                        result["ml_label"], result["confidence"])
            return

        camera_state.update(result["ml_label"], result["confidence"])
        logger.info("Camera classified → %s (%.2f)",
                    result["ml_label"], result["confidence"])
        
        # Persist so this survives a backend restart
        async with AsyncSessionLocal() as db:
            try:
                stmt = sa_select(CameraLastLabel).where(
                    CameraLastLabel.device_id == payload.get("device_id", "esp32-cam-01")
                )
                row = (await db.execute(stmt)).scalar_one_or_none()
                if row:
                    row.ml_label   = result["ml_label"]
                    row.confidence = result["confidence"]
                    row.updated_at = datetime.utcnow()
                else:
                    db.add(CameraLastLabel(
                        device_id  = payload.get("device_id", "esp32-cam-01"),
                        ml_label   = result["ml_label"],
                        confidence = result["confidence"],
                    ))
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("Failed to persist camera label: %s", exc)

    def _handle_status(self, payload: dict):
        device_id = payload.get("device_id", "unknown")
        status    = payload.get("status", "unknown")
        device_state.update(device_id, status)
        logger.info("Device %s status → %s", device_id, status)

    # ── Outbound ──────────────────────────────────────────────

    def _publish_prediction(self, result, device_id: str):
        try:
            data = result.to_dict()
            data["device_id"] = device_id
            self._client.publish(
                settings.MQTT_TOPIC_PREDICTION,
                json.dumps(data, default=str),
                qos=1,
            )
        except Exception as exc:
            logger.error("Prediction publish failed: %s", exc)

    async def _upsert_session(self, db, device_id: str, fruit_type: str, freshness: str, result) -> str | None:
        """
        Create or update the FruitSession row for this device + fruit
        combination. Returns the PREVIOUS alert_level (None for a brand
        new session) so the caller can tell whether this reading is a
        genuinely NEW state, not a repeat of the last one.
        """
        stmt = sa_select(FruitSession).where(
            and_(
                FruitSession.device_id  == device_id,
                FruitSession.fruit_type == fruit_type,
                FruitSession.is_active  == True,
            )
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        now = datetime.utcnow()

        previous_alert_level = row.last_alert_level if row else None

        if row:
            row.last_seen            = now
            row.reading_count        += 1
            row.last_freshness       = freshness
            row.last_days_remaining  = result.days_remaining
            row.last_alert_level     = result.alert_level.value
        else:
            db.add(FruitSession(
                device_id            = device_id,
                fruit_type            = fruit_type,
                first_seen            = now,
                last_seen             = now,
                reading_count         = 1,
                last_freshness        = freshness,
                last_days_remaining   = result.days_remaining,
                last_alert_level      = result.alert_level.value,
            ))

        return previous_alert_level