# ============================================================
#  spoilage_engine/simulator.py
#  Generates realistic sensor data for testing the engine
#  without physical hardware.
#
#  FruitSimulator produces SensorReading sequences that mimic
#  how a fruit decays over time:
#    - temperature drifts around an ambient mean, with spikes
#    - humidity varies around an ambient mean
#    - gas rises from ~0 to ~1, accelerating during the
#      ripening → decay transition (ethylene burst)
#    - the ML label switches fresh → rotten at a set point
#
#  Usage:
#      sim = FruitSimulator("banana")
#      for frac, reading in sim.generate(days=7, readings_per_day=24):
#          result = predictor.predict(reading)
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generator, Optional, Tuple
import random

from .baselines import FruitBaseline, get_baseline
from .engine import SensorReading


@dataclass
class SimulationConfig:
    """Controls how the simulator generates data."""
    base_temp:          float = 22.0
    temp_noise:         float = 1.5
    temp_spike_chance:  float = 0.02
    temp_spike_size:    float = 6.0
    base_humidity:      float = 60.0
    humidity_noise:     float = 5.0
    gas_rise_rate:      float = 0.12   # fraction of curve per day
    label_switch_at:    float = 0.75   # fraction of shelf life elapsed
    label_confidence:   float = 0.93
    seed:               Optional[int] = 42


class FruitSimulator:
    """Generates realistic SensorReading sequences for one fruit."""

    def __init__(
        self,
        fruit_name: str,
        config: Optional[SimulationConfig] = None,
        initial_freshness: float = 1.0,
    ):
        self.fruit_name        = fruit_name.lower().strip()
        self._baseline         = get_baseline(self.fruit_name)
        self.config            = config or SimulationConfig()
        self.initial_freshness = max(0.0, min(initial_freshness, 1.0))
        self._rng              = random.Random(self.config.seed)

    @property
    def baseline(self) -> FruitBaseline:
        return self._baseline

    # ── Full sequence generator ───────────────────────────────
    def generate(
        self,
        days: float,
        readings_per_day: int = 24,
        start_time: Optional[datetime] = None,
    ) -> Generator[Tuple[float, SensorReading], None, None]:
        total = int(round(days * readings_per_day))
        if total < 1:
            total = 1
        start = start_time or datetime.now()
        interval = timedelta(hours=24.0 / readings_per_day)

        for i in range(total):
            day_fraction = i / (total - 1) if total > 1 else 1.0
            ts = start + interval * i
            yield day_fraction, self.single_reading(day_fraction, timestamp=ts)

    # ── Single reading at a point in the decay curve ─────────
    def single_reading(
        self,
        day_fraction: float,
        timestamp: Optional[datetime] = None,
    ) -> SensorReading:
        frac = max(0.0, min(day_fraction, 1.0))

        # Temperature: ambient + noise + occasional spike
        temp = self._add_noise(self.config.base_temp, self.config.temp_noise)
        if self._rng.random() < self.config.temp_spike_chance:
            temp += self._rng.uniform(0, self.config.temp_spike_size)

        # Humidity: ambient + noise, clamped to valid range
        humidity = self._add_noise(self.config.base_humidity, self.config.humidity_noise)
        humidity = max(0.0, min(humidity, 100.0))

        # Gas + ML label
        gas = self._compute_gas(frac)
        ml_label, confidence = self._compute_ml_label(frac)

        return SensorReading(
            temperature_c=round(temp, 2),
            humidity_pct=round(humidity, 2),
            gas_normalised=round(gas, 4),
            ml_label=ml_label,
            ml_confidence=round(confidence, 3),
            timestamp=timestamp or datetime.now(),
        )

    # ── Gas curve ─────────────────────────────────────────────
    def _compute_gas(self, day_fraction: float) -> float:
        """
        Gas rises slowly while fresh, then bursts during the
        ripening → decay transition (around label_switch_at),
        then saturates. Modelled as a logistic S-curve centred
        on the switch point.
        """
        switch = self.config.label_switch_at
        k = 8.0                                   # burst sharpness
        x = (day_fraction - switch) * k
        logistic = 1.0 / (1.0 + pow(2.718281828, -x))
        base_rise = day_fraction * 0.15           # small linear floor
        gas = base_rise + (1.0 - 0.15) * logistic
        gas += (1.0 - self.initial_freshness) * 0.3   # less fresh → starts higher
        gas += self._rng.uniform(-0.02, 0.02)     # tiny noise
        return max(0.0, min(gas, 1.0))

    # ── ML label curve ────────────────────────────────────────
    def _compute_ml_label(self, day_fraction: float) -> Tuple[str, float]:
        """
        Before switch → fresh label.
        After switch  → rotten label.
        Near switch   → confidence dips (model is uncertain).
        """
        switch = self.config.label_switch_at
        base_conf = self.config.label_confidence

        distance = abs(day_fraction - switch)
        if distance < 0.1:
            conf = base_conf - (0.1 - distance) * 2.5
        else:
            conf = base_conf
        conf = max(0.5, min(conf, 1.0))

        if day_fraction < switch:
            return self._baseline.fresh_label, conf
        return self._baseline.rotten_label, conf

    # ── Helpers ───────────────────────────────────────────────
    def _add_noise(self, value: float, noise: float) -> float:
        return max(0.0, value + self._rng.gauss(0, noise))

    def summary(self) -> str:
        b = self._baseline
        c = self.config
        return (
            f"{b.display_name} simulator | "
            f"ambient {c.base_temp}°C / {c.base_humidity}% RH | "
            f"shelf life {b.base_shelf_life_days}d | "
            f"label switch at {c.label_switch_at:.0%}"
        )


# ============================================================
#  QUICK SELF-TEST
#  Run:  python -m spoilage_engine.simulator
# ============================================================
# ============================================================
#  QUICK SELF-TEST
#  Run:  python -m spoilage_engine.simulator
# ============================================================
if __name__ == "__main__":
    from .engine import SpoilagePredictor

    predictor = SpoilagePredictor()

    cfg = SimulationConfig(base_temp=29.0, base_humidity=62.0, seed=7)
    sim = FruitSimulator("banana", config=cfg)

    print("=" * 80)
    print("  FruitSimulator — self-test")
    print("=" * 80)
    print(f"\n  {sim.summary()}\n")

    print(f"  {'Day':>4} {'Temp':>6} {'Hum':>6} {'Gas':>6} {'Label':>14} "
          f"{'Days left':>10} {'State':>9} {'Alert':>9}")
    print("  " + "-" * 74)

    readings = list(sim.generate(days=7, readings_per_day=24))
    for i, (frac, reading) in enumerate(readings):
        if i % 24 == 0 or i == len(readings) - 1:
            result = predictor.predict(reading)
            day = frac * 7
            print(f"  {day:>4.1f} {reading.temperature_c:>6.1f} "
                  f"{reading.humidity_pct:>6.1f} {reading.gas_normalised:>6.2f} "
                  f"{reading.ml_label:>14} {result.days_remaining:>10.1f} "
                  f"{result.freshness_state.value:>9} "
                  f"{result.alert_level.value:>9}")

    gases  = [r.gas_normalised for _, r in readings]
    labels = [r.ml_label for _, r in readings]
    print(f"\n  Gas start → end : {gases[0]:.2f} → {gases[-1]:.2f}")
    print(f"  First label     : {labels[0]}")
    print(f"  Last label      : {labels[-1]}")
    print(f"  Total readings  : {len(readings)}  (expected 168)")

    print("\n  --- All 7 fruits smoke test ---")
    from .baselines import list_fruits
    for fruit in list_fruits():
        s = FruitSimulator(fruit, config=SimulationConfig(seed=1))
        rs = list(s.generate(days=3, readings_per_day=8))
        first = predictor.predict(rs[0][1])
        last  = predictor.predict(rs[-1][1])
        print(f"    {fruit:<12} {len(rs):>3} readings | "
              f"days left {first.days_remaining:>5.1f} → {last.days_remaining:>4.1f} | "
              f"{first.freshness_state.value} → {last.freshness_state.value}")

    print("\n✔ Self-test complete.\n")