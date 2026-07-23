"""
mqtt_client.py
Subscribes to HiveMQ Cloud (TLS), runs the spoilage engine once per
CONFIRMED fruit in view, saves to DB, and dispatches alerts.
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
from .detector import detect_fruits, crop_box
from .device_state import device_state
from .inference import classify_image
from .models import Alert, CameraLastLabel, FruitReading, FruitSession, Prediction

from spoilage_engine.baselines import is_fresh_label
from spoilage_engine.engine import (
    SensorReading as EngineSensorReading,
    SpoilagePredictor,
)
from alerts.notifier import AlertNotifier

logger = logging.getLogger(__name__)

GAS_ADC_MAX = 4095.0
MIN_CLASSIFY_CONFIDENCE = 0.70   # per-crop classification confidence floor

_predictor = SpoilagePredictor(
    warning_days=settings.ALERT_DAYS_THRESHOLD,
    critical_days=settings.CRITICAL_DAYS_THRESHOLD,
)


def _normalise_gas(raw: float) -> float:
    return max(0.0, min(float(raw) / GAS_ADC_MAX, 1.0))


def _parse_ml_label(ml_label: str):
    label = ml_label.lower().strip()
    fruit_type = label.replace("fresh", "").replace("rotten", "")
    freshness  = "fresh" if is_fresh_label(label) else "rotten"
    return fruit_type, freshness


class MQTTClient:

    def __init__(self):
        self._client = mqtt.Client(client_id="fruit-monitor-backend", clean_session=True)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop:  asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task | None = None
        self._notifier = AlertNotifier(settings)

        self._configure_tls()
        self._bind_callbacks()

    def _configure_tls(self):
        ctx = ssl.create_default_context()
        self._client.tls_set_context(ctx)
        self._client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

    def _bind_callbacks(self):
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected → %s", settings.MQTT_BROKER_HOST)
            client.subscribe(settings.MQTT_TOPIC_SENSOR, qos=1)
            client.subscribe(settings.MQTT_TOPIC_CAMERA, qos=1)
            client.subscribe(settings.MQTT_TOPIC_DEVICE_STATUS, qos=1)
            logger.info("Subscribed to sensor/camera/status topics")
        else:
            logger.error("MQTT connection refused (rc=%d). Check credentials.", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly (rc=%d)", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("Bad payload on %s: %s", msg.topic, exc)
            return
        envelope = {"topic": msg.topic, "payload": payload}
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._queue.put(envelope), self._loop)

    def start(self):
        self._loop = asyncio.get_running_loop()
        self._loop.create_task(self._restore_camera_state())
        self._client.connect_async(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT)
        self._client.loop_start()
        self._worker_task = self._loop.create_task(self._process_queue())
        logger.info("MQTT client started")

    async def _restore_camera_state(self):
        async with AsyncSessionLocal() as db:
            try:
                rows = (await db.execute(sa_select(CameraLastLabel))).scalars().all()
                for row in rows:
                    camera_state.restore(row.fruit_type, row.ml_label, row.confidence, row.updated_at)
                    logger.info("Restored camera state → %s (%.2f) from %s",
                                row.ml_label, row.confidence, row.updated_at)
            except Exception as exc:
                logger.warning("Could not restore camera state (fine on first run): %s", exc)

    async def _process_queue(self):
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
        """validate → one full prediction cycle PER currently-confirmed fruit."""
        required = {"device_id", "ml_label", "confidence", "temperature", "humidity", "gas_raw"}
        missing  = required - set(payload.keys())
        if missing:
            logger.warning("Dropping message — missing fields: %s", missing)
            return

        gas_raw        = float(payload["gas_raw"])
        gas_normalised = _normalise_gas(gas_raw)

        # Option A: use every currently-confirmed camera detection. Falls
        # back to the device's own self-reported label only when the
        # camera hasn't confirmed anything yet at all (e.g. fresh boot).
        slots = camera_state.get_all()
        if not slots:
            fallback_type, _ = _parse_ml_label(str(payload["ml_label"]))
            slots = [{
                "fruit_type": fallback_type,
                "ml_label":   str(payload["ml_label"]),
                "confidence": float(payload["confidence"]),
            }]

        for slot in slots:
            await self._process_one_fruit(payload, slot, gas_raw, gas_normalised)

    async def _process_one_fruit(self, payload: dict, slot: dict, gas_raw: float, gas_normalised: float):
        """Full engine -> DB -> publish -> alert cycle for ONE fruit slot."""
        ml_label      = slot["ml_label"]
        ml_confidence = slot["confidence"]
        fruit_type, freshness = _parse_ml_label(ml_label)
        device_id = str(payload["device_id"])

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

        try:
            result = _predictor.predict(engine_reading)
        except Exception as exc:
            logger.error("SpoilagePredictor error: %s", exc)
            return

        logger.info(
            "[%s] %s/%s → %.1f days | %s | conf=%.2f",
            device_id, fruit_type, freshness,
            result.days_remaining, result.alert_level.value, result.confidence,
        )

        needs_alert = False
        async with AsyncSessionLocal() as db:
            try:
                reading_row = FruitReading(
                    device_id       = device_id,
                    fruit_type      = fruit_type,
                    freshness_label = freshness,
                    ml_confidence   = ml_confidence,
                    temperature     = float(payload["temperature"]),
                    humidity        = float(payload["humidity"]),
                    gas_level       = gas_raw,
                    received_at     = datetime.utcnow(),
                )
                db.add(reading_row)
                await db.flush()

                db.add(Prediction(
                    reading_id        = reading_row.id,
                    days_remaining    = result.days_remaining,
                    freshness_state   = result.freshness_state.value,
                    alert_level       = result.alert_level.value,
                    engine_confidence = result.confidence,
                ))

                previous_alert_level = await self._upsert_session(
                    db, device_id, fruit_type, freshness, result
                )
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
                logger.exception("DB write failed for %s: %s", fruit_type, exc)
                return

        self._publish_prediction(result, device_id)

        if needs_alert:
            await self._notifier.send(
                message        = result.message,
                alert_level    = result.alert_level.value,
                fruit_name     = result.fruit_name,
                days_remaining = result.days_remaining,
            )

    async def _handle_camera(self, payload: dict):
        """
        Downloads the frame, detects EVERY fruit in it, classifies each
        detected crop independently, and feeds the confirmed set to
        camera_state — one slot per fruit type, not one shared value.
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

        loop = asyncio.get_running_loop()

        try:
            boxes = await loop.run_in_executor(None, detect_fruits, image_bytes)
        except Exception as exc:
            logger.error("Fruit detection failed: %s", exc)
            return

        if not boxes:
            logger.info("No fruit detected in frame")
            return

        logger.info("Detector found %d fruit(s) in frame", len(boxes))

        device_id = payload.get("device_id", "esp32-cam-01")
        confirmed_detections = []

        for box in boxes:
            try:
                crop_bytes = crop_box(image_bytes, box["box"])
                result = await loop.run_in_executor(None, classify_image, crop_bytes)
            except Exception as exc:
                logger.error("Crop classification failed for %s: %s", box["fruit_type"], exc)
                continue

            if result["confidence"] < MIN_CLASSIFY_CONFIDENCE:
                logger.info("Low-confidence crop ignored → %s (%.2f)",
                            result["ml_label"], result["confidence"])
                continue

            detected_fruit_type, _ = _parse_ml_label(result["ml_label"])
            if detected_fruit_type != box["fruit_type"]:
                logger.info(
                    "Detector/classifier disagree: detector=%s classifier=%s — using classifier",
                    box["fruit_type"], detected_fruit_type,
                )

            confirmed_detections.append({
                "fruit_type": detected_fruit_type,
                "ml_label":   result["ml_label"],
                "confidence": result["confidence"],
            })

        if not confirmed_detections:
            logger.info("All detections fell below classification confidence — nothing confirmed")
            return

        camera_state.update(confirmed_detections)
        logger.info("Camera state updated: %s",
                    ", ".join(d["ml_label"] for d in confirmed_detections))

        async with AsyncSessionLocal() as db:
            try:
                for det in confirmed_detections:
                    stmt = sa_select(CameraLastLabel).where(
                        and_(
                            CameraLastLabel.device_id  == device_id,
                            CameraLastLabel.fruit_type == det["fruit_type"],
                        )
                    )
                    row = (await db.execute(stmt)).scalar_one_or_none()
                    if row:
                        row.ml_label   = det["ml_label"]
                        row.confidence = det["confidence"]
                        row.updated_at = datetime.utcnow()
                    else:
                        db.add(CameraLastLabel(
                            device_id  = device_id,
                            fruit_type = det["fruit_type"],
                            ml_label   = det["ml_label"],
                            confidence = det["confidence"],
                        ))
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("Failed to persist camera labels: %s", exc)

    def _handle_status(self, payload: dict):
        device_id = payload.get("device_id", "unknown")
        status    = payload.get("status", "unknown")
        device_state.update(device_id, status)
        logger.info("Device %s status → %s", device_id, status)

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
            row.last_seen           = now
            row.reading_count       += 1
            row.last_freshness      = freshness
            row.last_days_remaining = result.days_remaining
            row.last_alert_level    = result.alert_level.value
        else:
            db.add(FruitSession(
                device_id           = device_id,
                fruit_type          = fruit_type,
                first_seen          = now,
                last_seen           = now,
                reading_count       = 1,
                last_freshness      = freshness,
                last_days_remaining = result.days_remaining,
                last_alert_level    = result.alert_level.value,
            ))

        return previous_alert_level