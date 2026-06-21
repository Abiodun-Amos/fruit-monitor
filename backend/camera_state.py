"""
camera_state.py
Holds the most recent camera classification in memory so the
sensor pipeline (Option A) can pair it with incoming readings.
Thread-safe for the MQTT callback + async access.
"""
import threading
from datetime import datetime
from typing import Optional


class CameraState:
    def __init__(self):
        self._lock = threading.Lock()
        self._ml_label: Optional[str]   = None
        self._confidence: float         = 0.0
        self._updated_at: Optional[datetime] = None

    def update(self, ml_label: str, confidence: float):
        with self._lock:
            self._ml_label   = ml_label
            self._confidence = confidence
            self._updated_at = datetime.utcnow()

    def get(self):
        """Returns (ml_label, confidence) or (None, 0.0) if no camera data yet."""
        with self._lock:
            return self._ml_label, self._confidence

    @property
    def has_data(self) -> bool:
        with self._lock:
            return self._ml_label is not None


# Single shared instance
camera_state = CameraState()