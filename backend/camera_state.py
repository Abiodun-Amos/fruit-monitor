"""
camera_state.py
Holds the most recent CONFIRMED detections — one slot per fruit type
currently believed to be in the box, not a single shared value.
"""
import threading
from datetime import datetime, timedelta
from typing import Optional


def _fruit_type_of(label: str) -> str:
    return label.replace("fresh", "").replace("rotten", "")


class CameraState:
    def __init__(self, confirm_streak: int = 3, expire_seconds: int = 180):
        self._lock = threading.Lock()
        self._slots = {}
        self._candidates = {}
        self._confirm_streak = confirm_streak
        self._expire_seconds = expire_seconds

    def update(self, detections: list):
        """
        detections: list of {"fruit_type": str, "ml_label": str, "confidence": float},
        one per box the detector found this frame.
        """
        with self._lock:
            seen_types = set()
            for det in detections:
                ftype = det["fruit_type"]
                seen_types.add(ftype)

                if ftype in self._slots:
                    self._slots[ftype] = {**det, "updated_at": datetime.utcnow()}
                    self._candidates.pop(ftype, None)
                else:
                    self._candidates[ftype] = self._candidates.get(ftype, 0) + 1
                    if self._candidates[ftype] >= self._confirm_streak:
                        self._slots[ftype] = {**det, "updated_at": datetime.utcnow()}
                        self._candidates.pop(ftype, None)

            for ftype in list(self._candidates):
                if ftype not in seen_types:
                    self._candidates.pop(ftype)

            cutoff = datetime.utcnow() - timedelta(seconds=self._expire_seconds)
            for ftype in list(self._slots):
                if self._slots[ftype]["updated_at"] < cutoff:
                    self._slots.pop(ftype)

    def restore(self, fruit_type: str, ml_label: str, confidence: float, updated_at: Optional[datetime] = None):
        """
        Directly populate a slot from persisted state on startup —
        bypasses confirm_streak since this was already trusted before
        the backend restarted.
        """
        with self._lock:
            self._slots[fruit_type] = {
                "fruit_type": fruit_type,
                "ml_label":   ml_label,
                "confidence": confidence,
                "updated_at": updated_at or datetime.utcnow(),
            }

    def get_all(self) -> list:
        with self._lock:
            return [dict(v, fruit_type=k) for k, v in self._slots.items()]

    @property
    def has_data(self) -> bool:
        with self._lock:
            return len(self._slots) > 0


camera_state = CameraState()