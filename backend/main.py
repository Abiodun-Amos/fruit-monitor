"""
main.py
FastAPI application — entry point.

Run from fruit_monitor/ with:
    uvicorn backend.main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .mqtt_client import MQTTClient
from .routes import alerts, fruits, inventory, predictions


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    logger.info("Starting Fruit Monitor API...")

    await init_db()
    logger.info("Database tables ready")

    mqtt = MQTTClient()
    mqtt.start()
    app.state.mqtt_client = mqtt

    yield

    # ── Shutdown ─────────────────────────────────────────────
    logger.info("Shutting down...")
    mqtt.stop()


app = FastAPI(
    title="Fruit Monitor API",
    description="Smart AI Food Freshness Detection — Backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fruits.router,      prefix="/api/fruits",       tags=["Fruit Readings"])
app.include_router(predictions.router, prefix="/api/predictions",  tags=["Predictions"])
app.include_router(alerts.router,      prefix="/api/alerts",       tags=["Alerts"])
app.include_router(inventory.router,   prefix="/api/inventory",    tags=["Inventory"])


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "service": "Fruit Monitor API"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "version": "1.0.0"}

app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")