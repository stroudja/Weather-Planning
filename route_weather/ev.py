"""EV range modeling: temperature and headwind adjusted, with charging stops.

The efficiency curve approximates aggregate fleet data (Geotab/Recurrent
studies): range peaks near 20 °C, drops hard in deep cold (cabin heating,
battery chemistry) and moderately in extreme heat (A/C).
"""

import math

# (air temp °C, fraction of rated range)
TEMP_CURVE = [(-30, 0.50), (-20, 0.58), (-10, 0.70), (0, 0.79),
              (10, 0.90), (20, 1.00), (30, 0.95), (40, 0.84)]

USABLE_FRACTION = 0.80   # plan charging stops before the battery is empty


def temp_efficiency(temp_c) -> float:
    if temp_c is None:
        return 1.0
    pts = TEMP_CURVE
    if temp_c <= pts[0][0]:
        return pts[0][1]
    for (t1, f1), (t2, f2) in zip(pts, pts[1:]):
        if temp_c <= t2:
            return f1 + (f2 - f1) * (temp_c - t1) / (t2 - t1)
    return pts[-1][1]


def headwind_efficiency(wind_kmh, wind_from_deg, road_bearing_deg) -> float:
    """Penalty for the headwind component (≈0.4% range per km/h headwind);
    tailwinds give half that back."""
    if wind_kmh is None or wind_from_deg is None or road_bearing_deg is None:
        return 1.0
    headwind = wind_kmh * math.cos(math.radians(wind_from_deg - road_bearing_deg))
    if headwind >= 0:
        return max(0.7, 1.0 - 0.004 * headwind)
    return min(1.1, 1.0 - 0.002 * headwind)


def plan_charging(points: list, rated_range_km: float) -> dict:
    """Estimate effective range per segment and where charging is needed.

    Returns {"effective_range_km", "worst_efficiency", "charge_stops":
    [{"distance_m", "lat", "lon", "eta_offset_s"}...], "n_stops"}.
    """
    efficiencies = []
    for p in points:
        w = p.weather
        eff = (temp_efficiency(w.get("temperature_2m"))
               * headwind_efficiency(w.get("wind_speed_10m"),
                                     w.get("wind_direction_10m"),
                                     p.road_bearing))
        efficiencies.append(eff)

    stops = []
    budget_km = rated_range_km * USABLE_FRACTION * efficiencies[0]
    used_km = 0.0
    for prev, cur, eff in zip(points, points[1:], efficiencies):
        seg_km = (cur.distance_m - prev.distance_m) / 1000
        used_km += seg_km / max(eff, 0.3)   # weather-inflated consumption
        if used_km >= budget_km * 0.95:
            stops.append({
                "distance_m": cur.distance_m,
                "lat": cur.lat, "lon": cur.lon,
                "eta_offset_s": cur.eta_offset_s,
            })
            used_km = 0.0
            budget_km = rated_range_km * USABLE_FRACTION * eff

    avg_eff = sum(efficiencies) / len(efficiencies)
    return {
        "effective_range_km": rated_range_km * avg_eff,
        "worst_efficiency": min(efficiencies),
        "avg_efficiency": avg_eff,
        "charge_stops": stops,
        "n_stops": len(stops),
    }
