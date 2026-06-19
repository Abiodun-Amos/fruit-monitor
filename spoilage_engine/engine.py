#  spoilage_engine/engine.py
#  Core spoilage prediction logic.
#
#  Takes live sensor readings + the ML model's freshness label
#  and outputs a structured prediction:
#    - days remaining until spoilage
#    - a decay rate multiplier (what is accelerating decay)
#    - an alert level (ok / warning / critical / spoiled)
#    - a confidence score
#
#  Pipeline:
#    1. compute_temp_multiplier()      — Arrhenius Q10 model
#    2. compute_humidity_multiplier()  — linear deviation penalty
#    3. compute_gas_multiplier()       — threshold step function
#    4. compute_freshness_multiplier() — from ML label + confidence
#    5. combine → days_remaining, alert_level, confidence

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List

from .baselines import FruitBaseline, get_baseline_from_label, is_fresh_label


# ── Tunable model constants ──────────────────────────────────
# Cold damage accumulates slower than heat spoilage, so the
# below-ideal temperature penalty is scaled down by this factor.
COLD_DAMAGE_FACTOR = 0.5

# Humidity deviation (in % points) that equals one full unit of
# humidity_sensitivity penalty. Larger = humidity matters less.
HUMIDITY_REFERENCE_DEVIATION = 20.0

# Low humidity (dehydration) is less damaging than high humidity
# (mould), so the dehydration side is scaled down by this factor.
LOW_HUMIDITY_FACTOR = 0.5

# Max multiplier the humidity stage can contribute.
HUMIDITY_MULTIPLIER_CAP = 2.0

# Number of recent readings (incl. current) used for smoothing.
SMOOTHING_WINDOW = 5


# ── Alert levels ─────────────────────────────────────────────
class AlertLevel(str, Enum):
    OK       = "ok"        # fruit is fresh, plenty of time
    WARNING  = "warning"   # spoilage within warning_days
    CRITICAL = "critical"  # spoilage imminent (< critical_days)
    SPOILED  = "spoiled"   # ML model indicates already rotten

# ── Freshness state (human-facing 3-tier label) ─────────────
class FreshnessState(str, Enum):
    GOOD     = "good"      # fresh + comfortable time remaining
    MARGINAL = "marginal"  # fresh but days_remaining <= marginal_days
    SPOILED  = "spoiled"   # ML label is rotten


# Boundary (days) between GOOD and MARGINAL.
DEFAULT_MARGINAL_DAYS = 2.0

# Default alert thresholds (days).
DEFAULT_WARNING_DAYS  = 2.0
DEFAULT_CRITICAL_DAYS = 1.0


# ── Input dataclass ──────────────────────────────────────────
@dataclass
class SensorReading:
    """One snapshot of environmental sensor data from the ESP32."""
    temperature_c:  float
    humidity_pct:   float
    gas_normalised: float                    # 0.0 – 1.0
    ml_label:       str
    ml_confidence:  float = 1.0
    timestamp:      datetime = field(default_factory=datetime.now)
    fruit_count:    Optional[int] = None

    def __post_init__(self):
        if not 0.0 <= self.gas_normalised <= 1.0:
            raise ValueError(
                f"gas_normalised must be 0.0–1.0, got {self.gas_normalised}"
            )
        if not 0.0 <= self.ml_confidence <= 1.0:
            raise ValueError(
                f"ml_confidence must be 0.0–1.0, got {self.ml_confidence}"
            )
        if not 0.0 <= self.humidity_pct <= 100.0:
            raise ValueError(
                f"humidity_pct must be 0–100, got {self.humidity_pct}"
            )


# ── Output dataclass ─────────────────────────────────────────
@dataclass
class PredictionResult:
    """Full output from SpoilagePredictor.predict()."""
    fruit_name:            str
    is_fresh:              bool
    days_remaining:        float
    alert_level:           AlertLevel
    freshness_state:       FreshnessState
    decay_rate_multiplier: float
    temp_multiplier:       float
    humidity_multiplier:   float
    gas_multiplier:        float
    freshness_multiplier:  float
    confidence:            float
    reading:               SensorReading
    message:               str = ""

    def to_dict(self) -> dict:
        """Serialise to a plain dict for an MQTT JSON payload."""
        return {
            "fruit_name":            self.fruit_name,
            "is_fresh":              self.is_fresh,
            "days_remaining":        self.days_remaining,
            "alert_level":           self.alert_level.value,
            "freshness_state":       self.freshness_state.value,
            "decay_rate_multiplier": self.decay_rate_multiplier,
            "temp_multiplier":       self.temp_multiplier,
            "humidity_multiplier":   self.humidity_multiplier,
            "gas_multiplier":        self.gas_multiplier,
            "freshness_multiplier":  self.freshness_multiplier,
            "confidence":            self.confidence,
            "message":               self.message,
            "temperature_c":         self.reading.temperature_c,
            "humidity_pct":          self.reading.humidity_pct,
            "gas_normalised":        self.reading.gas_normalised,
            "ml_label":              self.reading.ml_label,
            "fruit_count":           self.reading.fruit_count,
            "timestamp":             self.reading.timestamp.isoformat(),
        }


# ── Core predictor class ─────────────────────────────────────
class SpoilagePredictor:
    """Main spoilage prediction engine."""

    def __init__(
        self,
        warning_days:  float = DEFAULT_WARNING_DAYS,
        critical_days: float = DEFAULT_CRITICAL_DAYS,
        marginal_days: float = DEFAULT_MARGINAL_DAYS,
    ):
        self.warning_days  = warning_days
        self.critical_days = critical_days
        self.marginal_days = marginal_days

    # ── Public entry point ────────────────────────────────────
    def predict(
        self,
        reading: SensorReading,
        history: Optional[List[SensorReading]] = None,
    ) -> PredictionResult:
        # 1. Resolve fruit baseline + freshness state
        baseline = get_baseline_from_label(reading.ml_label)
        fresh    = is_fresh_label(reading.ml_label)

        # Optional smoothing using recent history
        temp, humidity, gas = reading.temperature_c, reading.humidity_pct, reading.gas_normalised
        if history:
            window = (history + [reading])[-SMOOTHING_WINDOW:]
            n = len(window)
            temp     = sum(r.temperature_c for r in window) / n
            humidity = sum(r.humidity_pct for r in window) / n
            gas      = sum(r.gas_normalised for r in window) / n

        # 2. Individual multipliers
        temp_mult      = self.compute_temp_multiplier(temp, baseline)
        humidity_mult  = self.compute_humidity_multiplier(humidity, baseline)
        gas_mult       = self.compute_gas_multiplier(gas, baseline)
        freshness_mult = self.compute_freshness_multiplier(
            reading.ml_label, reading.ml_confidence, baseline
        )

        # 3. Combine into total decay rate
        decay_rate = temp_mult * humidity_mult * gas_mult * freshness_mult

        # 4. Days remaining
        days_remaining = self._compute_days_remaining(baseline, decay_rate)

        # 5. Alert level
        alert_level = self._determine_alert_level(days_remaining, fresh)

        freshness_state = self._determine_freshness_state(days_remaining, fresh)

        # 6. Confidence
        confidence = self._compute_confidence(
            reading.ml_confidence, gas, decay_rate
        )

        # Build human-readable message
        message = self._build_message({
            "fruit_name":      baseline.display_name,
            "days_remaining":  days_remaining,
            "alert_level":     alert_level,
            "temp_multiplier": temp_mult,
            "gas_multiplier":  gas_mult,
        })

        return PredictionResult(
            fruit_name=baseline.display_name,
            is_fresh=fresh,
            days_remaining=round(days_remaining, 2),
            alert_level=alert_level,
            freshness_state=freshness_state,
            decay_rate_multiplier=round(decay_rate, 3),
            temp_multiplier=round(temp_mult, 3),
            humidity_multiplier=round(humidity_mult, 3),
            gas_multiplier=round(gas_mult, 3),
            freshness_multiplier=round(freshness_mult, 3),
            confidence=round(confidence, 3),
            reading=reading,
            message=message,
        )

    # ── Stage 1: Temperature multiplier ──────────────────────
    def compute_temp_multiplier(
        self, temperature_c: float, baseline: FruitBaseline,
    ) -> float:
        if baseline.ideal_temp_min <= temperature_c <= baseline.ideal_temp_max:
            return 1.0
        if temperature_c > baseline.ideal_temp_max:
            deviation = temperature_c - baseline.ideal_temp_max
            return baseline.temp_q10 ** (deviation / 10.0)
        # below ideal min — cold damage (gentler)
        deviation = baseline.ideal_temp_min - temperature_c
        return baseline.temp_q10 ** (deviation / 10.0 * COLD_DAMAGE_FACTOR)

    # ── Stage 2: Humidity multiplier ─────────────────────────
    def compute_humidity_multiplier(
        self, humidity_pct: float, baseline: FruitBaseline,
    ) -> float:
        if baseline.ideal_humidity_min <= humidity_pct <= baseline.ideal_humidity_max:
            return 1.0
        if humidity_pct > baseline.ideal_humidity_max:
            deviation = humidity_pct - baseline.ideal_humidity_max
            penalty = baseline.humidity_sensitivity * (deviation / HUMIDITY_REFERENCE_DEVIATION)
        else:
            deviation = baseline.ideal_humidity_min - humidity_pct
            penalty = (baseline.humidity_sensitivity
                       * (deviation / HUMIDITY_REFERENCE_DEVIATION)
                       * LOW_HUMIDITY_FACTOR)
        return min(1.0 + penalty, HUMIDITY_MULTIPLIER_CAP)

    # ── Stage 3: Gas multiplier ───────────────────────────────
    def compute_gas_multiplier(
        self, gas_normalised: float, baseline: FruitBaseline,
    ) -> float:
        if gas_normalised < baseline.gas_threshold_warning:
            return 1.0
        if gas_normalised < baseline.gas_threshold_critical:
            return baseline.gas_penalty_warning
        return baseline.gas_penalty_critical

    # ── Stage 4: Freshness multiplier ────────────────────────
    def compute_freshness_multiplier(
        self, ml_label: str, ml_confidence: float, baseline: FruitBaseline,
    ) -> float:
        if is_fresh_label(ml_label):
            return 1.0
        # rotten — fast-forward through the decay curve
        effective = (ml_confidence * baseline.rotten_decay_assumed
                     + (1.0 - ml_confidence) * 0.5)
        effective = min(effective, 0.95)   # guard against div-by-zero
        return 1.0 / (1.0 - effective)

    # ── Stage 5: Combine and classify ────────────────────────
    def _compute_days_remaining(
        self, baseline: FruitBaseline, decay_rate_multiplier: float,
    ) -> float:
        days = baseline.base_shelf_life_days / decay_rate_multiplier
        return max(days, 0.0)

    def _determine_alert_level(
        self, days_remaining: float, is_fresh: bool,
    ) -> AlertLevel:
        if not is_fresh:
            return AlertLevel.SPOILED
        if days_remaining <= self.critical_days:
            return AlertLevel.CRITICAL
        if days_remaining <= self.warning_days:
            return AlertLevel.WARNING
        return AlertLevel.OK
    
    def _determine_freshness_state(
        self, days_remaining: float, is_fresh: bool,
    ) -> FreshnessState:
        if not is_fresh:
            return FreshnessState.SPOILED
        if days_remaining <= self.marginal_days:
            return FreshnessState.MARGINAL
        return FreshnessState.GOOD

    def _compute_confidence(
        self, ml_confidence: float, gas_normalised: float,
        decay_rate_multiplier: float,
    ) -> float:
        # Gas signal quality: most informative mid-range, weaker at extremes
        gas_quality = 1.0 - abs(gas_normalised - 0.5) * 2 * 0.4   # 0.6–1.0
        # Stability: very high multipliers reduce reliability
        stability = 1.0 / (1.0 + 0.05 * max(decay_rate_multiplier - 1.0, 0.0))
        confidence = 0.6 * ml_confidence + 0.2 * gas_quality + 0.2 * stability
        return max(0.0, min(confidence, 1.0))

    def _build_message(self, d: dict) -> str:
        icons = {
            AlertLevel.OK:       "✓",
            AlertLevel.WARNING:  "⚠",
            AlertLevel.CRITICAL: "⛔",
            AlertLevel.SPOILED:  "✗",
        }
        icon  = icons.get(d["alert_level"], "")
        fruit = d["fruit_name"]

        if d["alert_level"] == AlertLevel.SPOILED:
            return f"{fruit} · {icon} already spoiled — discard"

        parts = [fruit, f"{icon} {d['days_remaining']:.1f} days remaining"]
        if d["temp_multiplier"] > 1.2:
            parts.append(f"temp {d['temp_multiplier']:.1f}×")
        if d["gas_multiplier"] > 1.0:
            parts.append("gas elevated")
        return " · ".join(parts)


# ============================================================
#  QUICK SELF-TEST
#  Run:  python -m spoilage_engine.engine
# ============================================================
if __name__ == "__main__":
    predictor = SpoilagePredictor()

    scenarios = [
        ("Fresh banana, ideal room",   SensorReading(20.0, 65.0, 0.05, "freshbanana", 0.97)),
        ("Fresh banana, hot Lagos day",SensorReading(32.0, 70.0, 0.20, "freshbanana", 0.95)),
        ("Fresh strawberry, 30°C",     SensorReading(30.0, 60.0, 0.15, "freshstrawberry", 0.96)),
        ("Fresh apple, cool store",    SensorReading(8.0,  88.0, 0.05, "freshapple", 0.99)),
        ("Fresh tomato, gas warning",  SensorReading(26.0, 80.0, 0.40, "freshtomato", 0.93)),
        ("Rotten banana, high gas",    SensorReading(30.0, 85.0, 0.75, "rottenbanana", 0.91)),
        ("Fresh orange, ideal",        SensorReading(18.0, 90.0, 0.08, "freshorange", 0.99)),
    ]

    print("=" * 78)
    print("  SpoilagePredictor — self-test")
    print("=" * 78)
    print(f"\n{'Scenario':<30} {'Days':>6} {'Alert':>9} {'Decay×':>8} {'Conf':>6}")
    print("-" * 78)
    for label, reading in scenarios:
        r = predictor.predict(reading)
        print(f"  {label:<28} {r.days_remaining:>6.1f} {r.alert_level.value:>9} "
              f"{r.decay_rate_multiplier:>8.2f} {r.confidence:>6.2f}")

    print("\n--- Detailed breakdown: 'Fresh strawberry, 30°C' ---")
    r = predictor.predict(SensorReading(30.0, 60.0, 0.15, "freshstrawberry", 0.96))
    print(f"  Message       : {r.message}")
    print(f"  Days remaining: {r.days_remaining}")
    print(f"  Temp mult     : {r.temp_multiplier}  (30°C vs ideal max 18°C)")
    print(f"  Humidity mult : {r.humidity_multiplier}  (60% vs ideal min 88%)")
    print(f"  Gas mult      : {r.gas_multiplier}")
    print(f"  Freshness mult: {r.freshness_multiplier}")
    print(f"  TOTAL decay   : {r.decay_rate_multiplier}×")

    print("\n--- MQTT payload (to_dict) ---")
    import json
    print(json.dumps(r.to_dict(), indent=2, default=str))

    print("\n✔ Self-test complete.\n")
