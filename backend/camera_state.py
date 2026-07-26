"""
camera_state.py
Holds the most recent CONFIRMED detections — one slot per fruit type
currently believed to be in the box, not a single shared value.

When a single frame contains MULTIPLE crops of the SAME fruit type
(several tomatoes in one photo, for instance), they are resolved by
majority vote before anything is accepted — otherwise whichever crop
happened to be processed last would arbitrarily overwrite the others,
even if it was the one disagreeing with the rest.

A change in FRUIT TYPE must repeat `confirm_streak` times across
separate frames before it's believed — a single stray misread can't
spawn a phantom session. A freshness change (fresh -> rotten) on an
already-established fruit type is still accepted immediately once it
wins that frame's majority vote, so real spoilage is never delayed.
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

    def _resolve_same_frame_duplicates(self, detections: list) -> list:
        """
        Collapses multiple crops of the same fruit type within one
        frame into a single verdict via majority vote (fresh/rotten),
        keeping the most confident reading on the winning side.
        """
        grouped = {}
        for det in detections:
            grouped.setdefault(det["fruit_type"], []).append(det)

        resolved = []
        for ftype, dets in grouped.items():
            if len(dets) == 1:
                resolved.append(dets[0])
                continue

            fresh_votes  = [d for d in dets if d["ml_label"].startswith("fresh")]
            rotten_votes = [d for d in dets if d["ml_label"].startswith("rotten")]
            winning_side = fresh_votes if len(fresh_votes) >= len(rotten_votes) else rotten_votes
            best = max(winning_side, key=lambda d: d["confidence"])

            import logging
            logging.getLogger(__name__).info(
                "Multiple %s crops in one frame (%d fresh, %d rotten) -> majority verdict: %s (%.2f)",
                ftype, len(fresh_votes), len(rotten_votes), best["ml_label"], best["confidence"],
            )
            resolved.append(best)

        return resolved

    def update(self, detections: list):
        """
        detections: list of {"fruit_type": str, "ml_label": str, "confidence": float},
        one per box the detector found this frame.
        """
        with self._lock:
            detections = self._resolve_same_frame_duplicates(detections)

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