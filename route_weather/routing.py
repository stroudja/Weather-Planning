"""Driving route retrieval (OSRM) and time-based sampling of the route."""

import math
from dataclasses import dataclass, field

import requests

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/"


@dataclass
class Route:
    coords: list          # [(lat, lon), ...] full-resolution geometry
    cum_duration: list    # seconds of travel to reach coords[i]
    cum_distance: list    # meters of travel to reach coords[i]
    total_duration: float
    total_distance: float
    name: str = ""        # OSRM route summary, e.g. "I 70, I 435"


@dataclass
class RoutePoint:
    lat: float
    lon: float
    eta_offset_s: float   # seconds after departure (weather-adjusted later)
    distance_m: float     # meters from route start
    road_bearing: float | None = None   # direction of travel, degrees (0=N)
    label: str = ""
    weather: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)   # full hourly forecast arrays
    hazards: list = field(default_factory=list)
    score: int = 0
    alerts: list = field(default_factory=list)


def _haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def bearing_deg(a, b) -> float:
    """Initial bearing from point a to point b, degrees clockwise from north."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360


def _parse_route(route: dict) -> Route:
    coords = [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]

    # Per-segment durations/distances, concatenated across legs, align with
    # the full geometry (segments = vertices - 1).
    seg_dur, seg_dist = [], []
    for leg in route["legs"]:
        ann = leg.get("annotation", {})
        seg_dur.extend(ann.get("duration", []))
        seg_dist.extend(ann.get("distance", []))

    if len(seg_dur) == len(coords) - 1:
        cum_dur, cum_dist = [0.0], [0.0]
        for d, m in zip(seg_dur, seg_dist):
            cum_dur.append(cum_dur[-1] + d)
            cum_dist.append(cum_dist[-1] + m)
    else:
        # Fallback: distribute total duration proportionally to great-circle
        # distance along the geometry.
        cum_dist = [0.0]
        for i in range(1, len(coords)):
            cum_dist.append(cum_dist[-1] + _haversine_m(coords[i - 1], coords[i]))
        total = cum_dist[-1] or 1.0
        cum_dur = [route["duration"] * d / total for d in cum_dist]

    name = ", ".join(
        leg.get("summary", "") for leg in route["legs"] if leg.get("summary"))
    return Route(
        coords=coords,
        cum_duration=cum_dur,
        cum_distance=cum_dist,
        total_duration=route["duration"],
        total_distance=route["distance"],
        name=name,
    )


def get_routes(waypoints: list, alternatives: bool = False) -> list:
    """Fetch driving route(s) through waypoints [{"lat":..,"lon":..}, ...].

    Returns a list of Route (one unless alternatives=True; OSRM only offers
    alternatives for two-waypoint requests).
    """
    coord_str = ";".join(f"{w['lon']},{w['lat']}" for w in waypoints)
    resp = requests.get(
        OSRM_URL + coord_str,
        params={
            "overview": "full",
            "geometries": "geojson",
            "annotations": "duration,distance",
            "steps": "false",
            "alternatives": "true" if alternatives and len(waypoints) == 2 else "false",
        },
        headers={"User-Agent": "route-weather/0.1"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"Routing failed: {data.get('code')} {data.get('message', '')}")
    return [_parse_route(r) for r in data["routes"]]


def get_route(waypoints: list) -> Route:
    return get_routes(waypoints)[0]


def _bearing_at(route: Route, i: int) -> float | None:
    """Direction of travel at geometry vertex i, smoothed over ~the next km."""
    n = len(route.coords)
    if n < 2:
        return None
    j = i
    target = route.cum_distance[min(i, n - 1)] + 1000
    while j < n - 1 and route.cum_distance[j] < target:
        j += 1
    if j == i:
        j = min(i + 1, n - 1)
    if j == i:
        i = max(i - 1, 0)
    if route.coords[i] == route.coords[j]:
        return None
    return bearing_deg(route.coords[i], route.coords[j])


def sample_route(route: Route, interval_s: float) -> list:
    """Pick checkpoints roughly every `interval_s` seconds of travel time.

    Always includes the start and end of the route.
    """
    points = []
    target = 0.0
    i = 0
    n = len(route.coords)
    while target < route.total_duration:
        while i < n - 1 and route.cum_duration[i] < target:
            i += 1
        lat, lon = route.coords[i]
        points.append(RoutePoint(
            lat=lat, lon=lon,
            eta_offset_s=route.cum_duration[i],
            distance_m=route.cum_distance[i],
            road_bearing=_bearing_at(route, i),
        ))
        target += interval_s

    # Final point: route end (skip if the last sample is within 2 min of it)
    if not points or route.total_duration - points[-1].eta_offset_s > 120:
        lat, lon = route.coords[-1]
        points.append(RoutePoint(
            lat=lat, lon=lon,
            eta_offset_s=route.total_duration,
            distance_m=route.total_distance,
            road_bearing=_bearing_at(route, n - 1),
        ))
    return points
