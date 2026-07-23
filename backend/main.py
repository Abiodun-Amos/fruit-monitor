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
from .routes import alerts, export, fruits, inventory, predictions
from . import periodic_alerts


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

    periodic_alerts.start()

    yield

    periodic_alerts.stop()
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
app.include_router(export.router,      prefix="/api/export",       tags=["Export"])


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "service": "Fruit Monitor API"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "version": "1.0.0"}

from fastapi import UploadFile, File

@app.post("/api/infer", tags=["Inference"])
async def infer(file: UploadFile = File(...)):
    """Classify an uploaded fruit image. Test endpoint for camera path."""
    from .inference import classify_image
    image_bytes = await file.read()
    try:
        result = classify_image(image_bytes)
        return result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Inference failed")
        return {"error": str(exc)}

@app.post("/api/infer-multi", tags=["Inference"])
async def infer_multi(file: UploadFile = File(...)):
    """Test endpoint: run the full detect -> crop -> classify pipeline on an uploaded image."""
    from .detector import detect_fruits, crop_box
    from .inference import classify_image
    image_bytes = await file.read()
    try:
        boxes = detect_fruits(image_bytes)
        results = []
        for box in boxes:
            crop = crop_box(image_bytes, box["box"])
            classified = classify_image(crop)
            results.append({"detector_said": box["fruit_type"],
                            "classifier_said": classified["ml_label"],
                            "confidence": classified["confidence"]})
        return {"fruit_count": len(boxes), "results": results}
    except Exception as exc:
        return {"error": str(exc)}

app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")