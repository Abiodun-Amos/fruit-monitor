"""
camera_state.py
Holds the most recent CONFIRMED detections — one slot per fruit type
currently believed to be in the box, not a single shared value.

Two separate kinds of confirmation, for two different reasons:

1. A brand NEW fruit type must be seen `confirm_streak` times in a row
   before a session is created for it — protects against a stray
   misread spawning a phantom fruit that was never really there.

2. Once a fruit type is established, a CHANGE in its freshness state
   must repeat `freshness_confirm_streak` times across separate frames
   before it's believed — protects against frame-to-frame classifier
   noise flapping the dashboard back and forth. This bar is
   deliberately lower than (1): real spoilage still needs to be caught
   quickly, just not on the strength of one possibly-wrong frame.

Multiple crops of the SAME fruit type within one frame are resolved
by majority vote first, so a single frame's internal disagreement
never reaches the streak logic in a confused state.
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _fruit_type_of(label: str) -> str:
    return label.replace("fresh", "").replace("rotten", "")


def _freshness_of(label: str) -> str:
    return "fresh" if label.startswith("fresh") else "rotten"


class CameraState:
    def __init__(
        self,
        confirm_streak: int = 3,
        freshness_confirm_streak: int = 2,
        expire_seconds: int = 180,
    ):
        self._lock = threading.Lock()
        self._slots = {}
        self._candidates = {}
        self._freshness_candidates = {}   # fruit_type -> (candidate_freshness, streak)
        self._confirm_streak = confirm_streak
        self._freshness_confirm_streak = freshness_confirm_streak
        self._expire_seconds = expire_seconds

    def _resolve_same_frame_duplicates(self, detections: list) -> list:
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

            logger.info(
                "Multiple %s crops in one frame (%d fresh, %d rotten) -> majority verdict: %s (%.2f)",
                ftype, len(fresh_votes), len(rotten_votes), best["ml_label"], best["confidence"],
            )
            resolved.append(best)

        return resolved

    def update(self, detections: list):
        with self._lock:
            detections = self._resolve_same_frame_duplicates(detections)

            seen_types = set()
            for det in detections:
                ftype = det["fruit_type"]
                seen_types.add(ftype)
                new_freshness = _freshness_of(det["ml_label"])

                if ftype in self._slots:
                    current_freshness = _freshness_of(self._slots[ftype]["ml_label"])

                    if new_freshness == current_freshness:
                        # Agrees with what's already believed — accept
                        # immediately, nothing is actually changing.
                        self._slots[ftype] = {**det, "updated_at": datetime.utcnow()}
                        self._freshness_candidates.pop(ftype, None)
                    else:
                        # Trying to CHANGE state — require a repeat.
                        cand_freshness, streak = self._freshness_candidates.get(ftype, (None, 0))
                        if new_freshness == cand_freshness:
                            streak += 1
                        else:
                            cand_freshness, streak = new_freshness, 1

                        if streak >= self._freshness_confirm_streak:
                            self._slots[ftype] = {**det, "updated_at": datetime.utcnow()}
                            self._freshness_candidates.pop(ftype, None)
                            logger.info(
                                "%s freshness change to %s CONFIRMED (%d/%d) — updating",
                                ftype, new_freshness, streak, self._freshness_confirm_streak,
                            )
                        else:
                            self._freshness_candidates[ftype] = (cand_freshness, streak)
                            logger.info(
                                "%s freshness change to %s not yet confirmed (%d/%d) — keeping %s",
                                ftype, new_freshness, streak, self._freshness_confirm_streak, current_freshness,
                            )
                else:
                    # Brand new fruit type — the stronger, existing bar.
                    self._candidates[ftype] = self._candidates.get(ftype, 0) + 1
                    if self._candidates[ftype] >= self._confirm_streak:
                        self._slots[ftype] = {**det, "updated_at": datetime.utcnow()}
                        self._candidates.pop(ftype, None)

            for ftype in list(self._candidates):
                if ftype not in seen_types:
                    self._candidates.pop(ftype)
            for ftype in list(self._freshness_candidates):
                if ftype not in seen_types:
                    self._freshness_candidates.pop(ftype)

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