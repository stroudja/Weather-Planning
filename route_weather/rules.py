"""Go / no-go rule engine.

Users define hard limits in a JSON file; every checkpoint is tested and the
trip gets a single verdict: GO, CAUTION (within 85% of a limit), or NO-GO.

Example rules.json (all keys optional, metric units):
{
    "max_score": 7,
    "max_gust_kmh": 80,
    "max_crosswind_kmh": 50,
    "min_visibility_m": 400,
    "max_snowfall_cm_h": 1.0,
    "max_precip_mm_h": 10,
    "min_temp_c": -25,
    "max_temp_c": 45,
    "block_ice": true,
    "block_events": ["Tornado Warning", "Blizzard Warning", "Ice Storm Warning"]
}
"""

import json

from . import hazards

KNOWN_KEYS = {
    "max_score", "max_gust_kmh", "max_crosswind_kmh", "min_visibility_m",
    "max_snowfall_cm_h", "max_precip_mm_h", "min_temp_c", "max_temp_c",
    "block_ice", "block_events",
}

DEFAULT_BLOCK_EVENTS = ["Tornado Warning", "Blizzard Warning", "Ice Storm Warning"]


def load_rules(path: str) -> dict:
    with open(path) as f:
        rules = json.load(f)
    unknown = set(rules) - KNOWN_KEYS
    if unknown:
        raise ValueError(f"Unknown rule keys: {sorted(unknown)}. "
                         f"Valid keys: {sorted(KNOWN_KEYS)}")
    return rules


def _checks(rules: dict, point) -> list:
    """Yield (violated, near_limit, reason) for one checkpoint."""
    w = point.weather
    out = []

    def limit(value, threshold, kind, reason, invert=False):
        if value is None or threshold is None:
            return
        if invert:
            out.append((value < threshold, value < threshold * 1.15, reason))
        else:
            out.append((value > threshold, value > threshold * 0.85, reason))

    limit(point.score, rules.get("max_score"), "score",
          f"risk score {point.score}")
    limit(w.get("wind_gusts_10m"), rules.get("max_gust_kmh"), "gust",
          f"gusts {w.get('wind_gusts_10m') or 0:.0f} km/h")
    cross = hazards.crosswind_component(
        w.get("wind_gusts_10m"), w.get("wind_direction_10m"), point.road_bearing)
    limit(cross, rules.get("max_crosswind_kmh"), "crosswind",
          f"crosswind {cross or 0:.0f} km/h")
    limit(w.get("visibility"), rules.get("min_visibility_m"), "visibility",
          f"visibility {(w.get('visibility') or 0):.0f} m", invert=True)
    limit(w.get("snowfall"), rules.get("max_snowfall_cm_h"), "snow",
          f"snowfall {w.get('snowfall') or 0:.1f} cm/h")
    limit(w.get("precipitation"), rules.get("max_precip_mm_h"), "precip",
          f"precip {w.get('precipitation') or 0:.1f} mm/h")
    limit(w.get("temperature_2m"), rules.get("min_temp_c"), "cold",
          f"temp {w.get('temperature_2m') or 0:.0f} °C", invert=True)
    limit(w.get("temperature_2m"), rules.get("max_temp_c"), "heat",
          f"temp {w.get('temperature_2m') or 0:.0f} °C")

    if rules.get("block_ice"):
        icy = any("ice risk" in h.lower() or "freezing" in h.lower()
                  for h in point.hazards)
        out.append((icy, icy, "icy conditions"))

    events = rules.get("block_events", DEFAULT_BLOCK_EVENTS)
    for alert in point.alerts:
        if alert in events:
            out.append((True, True, f"active alert: {alert}"))
    return out


def evaluate(rules: dict, points: list) -> dict:
    """Return {"verdict": "GO"|"CAUTION"|"NO-GO", "violations": [...],
    "warnings": [...]} across all checkpoints."""
    violations, warnings = [], []
    for p in points:
        where = f"{p.distance_m / 1000:.0f} km in"
        for violated, near, reason in _checks(rules, p):
            if violated:
                violations.append(f"{where}: {reason}")
            elif near:
                warnings.append(f"{where}: {reason} (near limit)")

    if violations:
        verdict = "NO-GO"
    elif warnings:
        verdict = "CAUTION"
    else:
        verdict = "GO"
    return {"verdict": verdict, "violations": violations, "warnings": warnings}
