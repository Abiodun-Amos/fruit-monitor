# ============================================================
#  spoilage_engine/baselines.py
#  Fruit baseline data for spoilage prediction.
#
#  All shelf-life values are calibrated for AMBIENT monitoring
#  (~20°C, 50-65% RH) — the realistic condition for a box or
#  small room monitored by an ESP32 + DHT22.
#
#  Temperature sensitivity uses the Q10 Arrhenius model:
#    rate_multiplier = Q10 ** ((T - T_ideal) / 10)
#  meaning spoilage rate doubles for every 10°C above ideal.
#
#  Sources: food science literature, Scopus cluster analysis
#  (2024-2026), USDA postharvest guidelines.
# ============================================================

from dataclasses import dataclass, field
from typing import Tuple, Dict


@dataclass(frozen=True)
class FruitBaseline:
    """
    Immutable baseline parameters for one fruit type.
    Used by SpoilagePredictor to compute days-remaining estimates.
    """

    # ── Identity ─────────────────────────────────────────────
    name: str                       # canonical name, e.g. "apple"
    display_name: str               # human label, e.g. "Apple"

    # ── ML model class labels ────────────────────────────────
    # These must exactly match the keys in class_map_v2.json
    fresh_label: str                # e.g. "freshapple"
    rotten_label: str               # e.g. "rottenapple"

    # ── Shelf life ───────────────────────────────────────────
    # Days remaining when fruit is visually FRESH and sensors
    # are at ideal conditions. This is the starting clock.
    base_shelf_life_days: float

    # ── Ideal storage conditions ─────────────────────────────
    ideal_temp_min: float           # °C  lower bound
    ideal_temp_max: float           # °C  upper bound
    ideal_humidity_min: float       # %RH lower bound
    ideal_humidity_max: float       # %RH upper bound

    # ── Temperature sensitivity (Q10 Arrhenius factor) ───────
    # Spoilage rate doubles every (10 / temp_q10) °C above ideal.
    # Most climacteric fruits: Q10 ≈ 2.0–2.5
    # Highly perishable (strawberry, tomato): Q10 ≈ 2.5–3.0
    temp_q10: float = 2.0

    # ── Humidity sensitivity (0 = insensitive, 1 = very sensitive)
    # High humidity → mould; low humidity → dehydration.
    # Applied as an additive penalty on both sides of ideal range.
    humidity_sensitivity: float = 0.3

    # ── Gas sensor thresholds (normalised ADC 0.0–1.0) ───────
    # These are starting points — fine-tune after hardware testing.
    # Below warning  → normal decay rate
    # Warning–critical → accelerated (× gas_penalty_factor)
    # Above critical  → rapid spoilage imminent
    gas_threshold_warning: float = 0.35
    gas_threshold_critical: float = 0.65

    # Rate multiplier applied when gas is in warning zone
    gas_penalty_warning: float = 1.3
    # Rate multiplier applied when gas is in critical zone
    gas_penalty_critical: float = 2.0

    # ── Freshness score → decay factor mapping ───────────────
    # When ML model says "rotten", how far through the decay
    # curve do we assume the fruit already is? (0.0–1.0)
    # 0.0 = just turned, 1.0 = completely gone
    rotten_decay_assumed: float = 0.80


# ============================================================
#  FRUIT BASELINES REGISTRY
#  7 fruits matching the v2 ML model training classes.
# ============================================================

FRUIT_BASELINES: Dict[str, FruitBaseline] = {

    "apple": FruitBaseline(
        name="apple",
        display_name="Apple",
        fresh_label="freshapple",
        rotten_label="rottenapple",

        # At room temp (~20°C): 10–21 days depending on variety.
        # Using 14 days as a conservative middle estimate.
        base_shelf_life_days=14.0,

        # Cold storage ideal: 0–4°C, 85–95% RH.
        # At room temp, 18–22°C is typical — still within tolerance.
        ideal_temp_min=2.0,
        ideal_temp_max=22.0,
        ideal_humidity_min=80.0,
        ideal_humidity_max=95.0,

        # Climacteric fruit — moderate ethylene producer.
        temp_q10=2.0,
        humidity_sensitivity=0.25,

        gas_threshold_warning=0.30,
        gas_threshold_critical=0.60,
        gas_penalty_warning=1.3,
        gas_penalty_critical=1.9,
        rotten_decay_assumed=0.80,
    ),

    "banana": FruitBaseline(
        name="banana",
        display_name="Banana",
        fresh_label="freshbanana",
        rotten_label="rottenbanana",

        # At room temp: 5–8 days once ripe (yellow).
        # One of the fastest-decaying fruits in the dataset.
        base_shelf_life_days=6.0,

        # Bananas are chilling-sensitive — below 12°C causes
        # blackening. Upper limit ~28°C before rapid softening.
        ideal_temp_min=13.0,
        ideal_temp_max=25.0,
        ideal_humidity_min=80.0,
        ideal_humidity_max=90.0,

        # High ethylene producer — ripening hormone is prominent.
        # Q10=2.5 reflects faster than average thermal response.
        temp_q10=2.5,
        humidity_sensitivity=0.30,

        gas_threshold_warning=0.25,   # ethylene rises early
        gas_threshold_critical=0.55,
        gas_penalty_warning=1.4,
        gas_penalty_critical=2.2,
        rotten_decay_assumed=0.85,
    ),

    "orange": FruitBaseline(
        name="orange",
        display_name="Orange",
        fresh_label="freshorange",
        rotten_label="rottenorange",

        # At room temp: 14–21 days. Citrus is relatively hardy.
        base_shelf_life_days=18.0,

        ideal_temp_min=4.0,
        ideal_temp_max=23.0,
        ideal_humidity_min=85.0,
        ideal_humidity_max=95.0,

        # Non-climacteric — lower ethylene; mould is main threat.
        temp_q10=1.8,
        humidity_sensitivity=0.40,   # mould-sensitive

        gas_threshold_warning=0.35,
        gas_threshold_critical=0.65,
        gas_penalty_warning=1.2,
        gas_penalty_critical=1.8,
        rotten_decay_assumed=0.75,
    ),

    "tomato": FruitBaseline(
        name="tomato",
        display_name="Tomato",
        fresh_label="freshtomato",
        rotten_label="rottentomato",

        # At room temp (ripe): 5–7 days. Very perishable.
        base_shelf_life_days=6.0,

        # Chilling-sensitive below 12°C (loses flavour/texture).
        ideal_temp_min=12.0,
        ideal_temp_max=24.0,
        ideal_humidity_min=85.0,
        ideal_humidity_max=95.0,

        # High climacteric — strong ethylene and CO2 signal.
        temp_q10=2.5,
        humidity_sensitivity=0.35,

        gas_threshold_warning=0.28,   # active respirer
        gas_threshold_critical=0.58,
        gas_penalty_warning=1.5,
        gas_penalty_critical=2.3,
        rotten_decay_assumed=0.82,
    ),

    "grape": FruitBaseline(
        name="grape",
        display_name="Grape",
        fresh_label="freshgrape",
        rotten_label="rottengrape",

        # At room temp: 5–7 days. Cold storage extends to weeks.
        base_shelf_life_days=6.0,

        ideal_temp_min=0.0,
        ideal_temp_max=22.0,
        ideal_humidity_min=85.0,
        ideal_humidity_max=95.0,

        # Non-climacteric but botrytis/mould is the main risk.
        # High humidity sensitivity.
        temp_q10=2.0,
        humidity_sensitivity=0.50,   # very mould prone

        gas_threshold_warning=0.32,
        gas_threshold_critical=0.62,
        gas_penalty_warning=1.3,
        gas_penalty_critical=2.0,
        rotten_decay_assumed=0.78,
    ),

    "strawberry": FruitBaseline(
        name="strawberry",
        display_name="Strawberry",
        fresh_label="freshstrawberry",
        rotten_label="rottenstrawberry",

        # Most perishable fruit in the dataset.
        # At room temp: 1–3 days. This is a real-world constraint.
        base_shelf_life_days=2.5,

        ideal_temp_min=0.0,
        ideal_temp_max=18.0,
        ideal_humidity_min=88.0,
        ideal_humidity_max=96.0,

        # Very high Q10 — every degree matters at room temp.
        temp_q10=3.0,
        humidity_sensitivity=0.45,

        gas_threshold_warning=0.22,   # fires fast
        gas_threshold_critical=0.50,
        gas_penalty_warning=1.6,
        gas_penalty_critical=2.5,
        rotten_decay_assumed=0.88,
    ),

    "guava": FruitBaseline(
        name="guava",
        display_name="Guava",
        fresh_label="freshguava",
        rotten_label="rottenguava",

        # At room temp: 4–7 days once ripe. Tropical fruit.
        base_shelf_life_days=5.0,

        # Tropical — damaged below 8°C. Optimal 8–12°C.
        ideal_temp_min=8.0,
        ideal_temp_max=26.0,
        ideal_humidity_min=80.0,
        ideal_humidity_max=92.0,

        # Moderate climacteric.
        temp_q10=2.2,
        humidity_sensitivity=0.30,

        gas_threshold_warning=0.30,
        gas_threshold_critical=0.60,
        gas_penalty_warning=1.35,
        gas_penalty_critical=2.0,
        rotten_decay_assumed=0.80,
    ),
}


# ============================================================
#  CONVENIENCE HELPERS
# ============================================================

def get_baseline(fruit_name: str) -> FruitBaseline:
    """
    Return baseline for a fruit name (case-insensitive).
    Raises KeyError with a helpful message if not found.
    """
    key = fruit_name.lower().strip()
    if key not in FRUIT_BASELINES:
        available = ", ".join(FRUIT_BASELINES.keys())
        raise KeyError(
            f"No baseline for '{fruit_name}'. "
            f"Available fruits: {available}"
        )
    return FRUIT_BASELINES[key]


def get_baseline_from_label(ml_label: str) -> FruitBaseline:
    """
    Resolve a baseline from an ML model output label.
    e.g. 'freshapple' or 'rottenbanana' → FruitBaseline.

    Raises KeyError if no matching baseline is found.
    """
    label = ml_label.lower().strip()
    for baseline in FRUIT_BASELINES.values():
        if label in (baseline.fresh_label, baseline.rotten_label):
            return baseline
    raise KeyError(
        f"No baseline matches ML label '{ml_label}'. "
        f"Check class_map_v2.json and FRUIT_BASELINES labels."
    )


def is_fresh_label(ml_label: str) -> bool:
    """Return True if the ML label represents a fresh state."""
    return ml_label.lower().startswith("fresh")


def list_fruits() -> list:
    """Return a sorted list of all registered fruit names."""
    return sorted(FRUIT_BASELINES.keys())


# ============================================================
#  QUICK SELF-TEST
#  Run:  python -m spoilage_engine.baselines
# ============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  Fruit Baselines — self-test")
    print("=" * 55)

    print(f"\nRegistered fruits ({len(FRUIT_BASELINES)}): {list_fruits()}\n")

    header = f"{'Fruit':<14} {'Shelf life':>12} {'Ideal temp':>14} {'Ideal RH':>12} {'Q10':>6}"
    print(header)
    print("-" * 62)
    for b in FRUIT_BASELINES.values():
        temp_range = f"{b.ideal_temp_min}–{b.ideal_temp_max}°C"
        rh_range   = f"{b.ideal_humidity_min}–{b.ideal_humidity_max}%"
        print(
            f"  {b.display_name:<12} "
            f"{b.base_shelf_life_days:>9.1f} d "
            f"{temp_range:>14} "
            f"{rh_range:>12} "
            f"{b.temp_q10:>6.1f}"
        )

    print("\n--- Label resolution test ---")
    for label in ["freshapple", "rottenbanana", "freshstrawberry", "rottentomato"]:
        b = get_baseline_from_label(label)
        fresh = is_fresh_label(label)
        state = "fresh" if fresh else "rotten"
        print(f"  '{label}' → {b.display_name} ({state})")

    print("\n--- Edge case test ---")
    try:
        get_baseline("pineapple")
    except KeyError as e:
        print(f"  Expected KeyError: {e}")

    print("\n✔ All checks passed.\n")
