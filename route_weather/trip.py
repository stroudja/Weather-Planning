"""Trip orchestration: the shared pipeline behind check/compare/fleet/watch.

geocode -> route -> sample checkpoints -> fetch hourly series (one request)
-> time-match forecasts -> hazard-assess -> weather-adjust ETAs -> re-match
-> alerts / EV / rules verdict.
"""

import datetime as dt

from . import alerts as alerts_mod
from . import ev as ev_mod
from . import geocode, hazards, routing, rules as rules_mod, weather
from .vehicles import PROFILES, VehicleProfile

ETA_ITERATIONS = 2   # re-match forecasts after slowing for weather, twice


def prepare(origin_q: str, dest_q: str, via_qs=(), alternatives=False) -> dict:
    """Geocode everything and fetch route(s)."""
    origin = geocode.geocode(origin_q)
    vias = [geocode.geocode(v) for v in via_qs]
    destination = geocode.geocode(dest_q)
    routes = routing.get_routes([origin, *vias, destination],
                                alternatives=alternatives)
    return {"origin": origin, "vias": vias, "destination": destination,
            "routes": routes}


def _assess_all(points, profile: VehicleProfile):
    for p in points:
        p.score, p.hazards = hazards.assess(
            p.weather, profile, p.road_bearing, p.lat, p.lon)


def _adjust_etas(points, nominal_offsets, depart_utc, profile):
    """Slow the trip through bad-weather segments and re-match forecasts.

    Sets point.eta_offset_s to weather-adjusted values. Returns the adjusted
    total duration (seconds).
    """
    for _ in range(ETA_ITERATIONS):
        factors = [hazards.speed_factor(p.weather) for p in points]
        adjusted = [nominal_offsets[0]]
        for i in range(1, len(points)):
            delta = nominal_offsets[i] - nominal_offsets[i - 1]
            # a segment is as slow as the worse of its two endpoints
            factor = min(factors[i - 1], factors[i])
            adjusted.append(adjusted[-1] + delta / factor)
        changed = any(abs(a - p.eta_offset_s) > 60
                      for a, p in zip(adjusted, points))
        for p, a in zip(points, adjusted):
            p.eta_offset_s = a
        weather.assign_forecast(points, depart_utc)
        _assess_all(points, profile)
        if not changed:
            break
    return points[-1].eta_offset_s


def run(route: routing.Route, origin: dict, destination: dict,
        depart_utc: dt.datetime, origin_utc_offset_s: int,
        interval_min: float = 30, profile: VehicleProfile = PROFILES["car"],
        ev_range_km: float | None = None, no_alerts: bool = False,
        rules: dict | None = None, adjust_etas: bool = True) -> dict:
    """Run the full weather pipeline for one route. Returns the trip dict."""
    points = routing.sample_route(route, interval_min * 60)
    nominal_offsets = [p.eta_offset_s for p in points]

    end = depart_utc + dt.timedelta(seconds=route.total_duration * 2.5 + 7200)
    weather.fetch_series(points, depart_utc, end)
    weather.assign_forecast(points, depart_utc)
    _assess_all(points, profile)

    adjusted_duration = route.total_duration
    if adjust_etas:
        adjusted_duration = _adjust_etas(points, nominal_offsets,
                                         depart_utc, profile)

    nws_alerts = [] if no_alerts else alerts_mod.fetch_alerts(points)

    trip = {
        "origin": origin,
        "destination": destination,
        "depart_utc": depart_utc,
        "origin_utc_offset_s": origin_utc_offset_s,
        "vehicle": profile.key,
        "route_name": route.name,
        "total_distance_m": route.total_distance,
        "total_duration_s": route.total_duration,
        "adjusted_duration_s": adjusted_duration,
        "route_coords": route.coords,
        "points": points,
        "alerts": nws_alerts,
        "peak_score": max(p.score for p in points),
        "mean_score": sum(p.score for p in points) / len(points),
    }

    if profile.is_ev and ev_range_km:
        trip["ev"] = ev_mod.plan_charging(points, ev_range_km)
        trip["ev"]["rated_range_km"] = ev_range_km

    if rules is not None:
        trip["verdict"] = rules_mod.evaluate(rules, points)

    return trip


def evaluate_departures(route: routing.Route, points: list,
                        window_start: dt.datetime, window_end: dt.datetime,
                        step_s: float, profile: VehicleProfile) -> list:
    """Score every departure slot in a window. One weather fetch total.

    `points` must already be sampled from `route`. Returns a list of
    {"depart_utc", "peak_score", "mean_score", "hazard_points",
    "adjusted_duration_s", "worst": str} sorted by departure time.
    """
    nominal_offsets = [p.eta_offset_s for p in points]
    horizon = window_end + dt.timedelta(seconds=route.total_duration * 2.5 + 7200)
    weather.fetch_series(points, window_start, horizon)

    results = []
    depart = window_start
    while depart <= window_end:
        for p, off in zip(points, nominal_offsets):
            p.eta_offset_s = off
        weather.assign_forecast(points, depart)
        _assess_all(points, profile)
        adjusted = _adjust_etas(points, nominal_offsets, depart, profile)
        worst = max(points, key=lambda p: p.score)
        results.append({
            "depart_utc": depart,
            "peak_score": worst.score,
            "mean_score": sum(p.score for p in points) / len(points),
            "hazard_points": sum(1 for p in points if p.score >= 5),
            "adjusted_duration_s": adjusted,
            "worst": "; ".join(worst.hazards) if worst.hazards else "clear",
        })
        depart += dt.timedelta(seconds=step_s)
    return results


def best_departure(results: list) -> dict:
    return min(results, key=lambda r: (r["peak_score"], r["mean_score"],
                                       r["adjusted_duration_s"]))
