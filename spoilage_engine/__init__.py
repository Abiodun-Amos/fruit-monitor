# ============================================================
#  spoilage_engine/__init__.py
#  Public API for the spoilage prediction module.
#
#  Usage:
#      from spoilage_engine import SpoilagePredictor, SensorReading
#      from spoilage_engine import get_baseline, list_fruits
# ============================================================

from .baselines import (
    FruitBaseline,
    FRUIT_BASELINES,
    get_baseline,
    get_baseline_from_label,
    is_fresh_label,
    list_fruits,
)

from .engine import (
    SensorReading,
    PredictionResult,
    SpoilagePredictor,
)

from .simulator import FruitSimulator

__all__ = [
    # baselines
    "FruitBaseline",
    "FRUIT_BASELINES",
    "get_baseline",
    "get_baseline_from_label",
    "is_fresh_label",
    "list_fruits",
    # engine
    "SensorReading",
    "PredictionResult",
    "SpoilagePredictor",
    # simulator
    "FruitSimulator",
]
