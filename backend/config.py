"""
config.py
Loads all settings from the .env file in the project root.
"""
import os
from dotenv import load_dotenv

# python-dotenv searches CWD upward — finds fruit_monitor/.env when you
# run uvicorn from the fruit_monitor/ directory
load_dotenv()


class Settings:
    # ── MQTT ─────────────────────────────────────────────────────
    MQTT_BROKER_HOST: str = os.getenv("MQTT_BROKER_HOST", "")
    MQTT_BROKER_PORT: int = int(os.getenv("MQTT_BROKER_PORT", "8883"))
    MQTT_USERNAME: str    = os.getenv("MQTT_USERNAME", "")
    MQTT_PASSWORD: str    = os.getenv("MQTT_PASSWORD", "")

    # ── MQTT Topics ───────────────────────────────────────────────
    MQTT_TOPIC_SENSOR: str     = os.getenv("MQTT_TOPIC_SENSOR",     "fruit/sensor/data")
    MQTT_TOPIC_PREDICTION: str = os.getenv("MQTT_TOPIC_PREDICTION", "fruit/prediction/result")
    MQTT_TOPIC_ALERT: str      = os.getenv("MQTT_TOPIC_ALERT",      "fruit/alert/spoilage")
    MQTT_TOPIC_COUNT: str      = os.getenv("MQTT_TOPIC_COUNT",      "fruit/count/update")
    MQTT_TOPIC_CAMERA: str     = os.getenv("MQTT_TOPIC_CAMERA", "fruit/camera/image")

    # ── Backend ───────────────────────────────────────────────────
    BACKEND_HOST: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", "8000"))
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./fruit_monitor.db")

    # ── Alerts ────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str   = os.getenv("TELEGRAM_CHAT_ID",   "")

    EMAIL_FROM:         str = os.getenv("EMAIL_FROM",         "")
    EMAIL_TO:           str = os.getenv("EMAIL_TO",           "")
    EMAIL_APP_PASSWORD: str = os.getenv("EMAIL_APP_PASSWORD", "")

    # ── Spoilage Thresholds ───────────────────────────────────────
    ALERT_DAYS_THRESHOLD: int    = int(os.getenv("ALERT_DAYS_THRESHOLD",    "2"))
    CRITICAL_DAYS_THRESHOLD: int = int(os.getenv("CRITICAL_DAYS_THRESHOLD", "1"))


settings = Settings()