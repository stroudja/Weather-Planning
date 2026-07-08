"""Time-matched point forecasts from Open-Meteo (free, no API key).

All values are fetched in metric units; display conversion happens in the
report layer so hazard scoring always sees consistent units.
"""

import datetime as dt

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARS = [
    "temperature_2m",          # °C
    "apparent_temperature",    # °C
    "dew_point_2m",            # °C
    "relative_humidity_2m",    # %
    "precipitation_probability",  # %
    "precipitation",           # mm
    "rain",                    # mm
    "snowfall",                # cm
    "snow_depth",              # m
    "weather_code",            # WMO code
    "cloud_cover",             # %
    "visibility",              # m
    "wind_speed_10m",          # km/h
    "wind_gusts_10m",          # km/h
    "wind_direction_10m",      # degrees
    "uv_index",
]

MAX_FORECAST_DAYS = 16


def get_utc_offset(lat: float, lon: float) -> int:
    """Seconds east of UTC for a location's local timezone (via Open-Meteo)."""
    resp = requests.get(
        FORECAST_URL,
        params={"latitude": lat, "longitude": lon, "timezone": "auto",
                "forecast_days": 1},
        timeout=30,
    )
    resp.raise_for_status()
    return int(resp.json().get("utc_offset_seconds", 0))


def fetch_weather(points: list, depart_utc: dt.datetime) -> None:
    """Attach a time-matched forecast dict to each RoutePoint in `points`.

    Each point gets point.weather = {var: value, ..., "eta_utc": datetime,
    "utc_offset_s": int}. Values are the forecast for the hour nearest the
    point's ETA.
    """
    etas = [depart_utc + dt.timedelta(seconds=p.eta_offset_s) for p in points]
    horizon = etas[-1] - dt.datetime.now(dt.timezone.utc)
    if horizon > dt.timedelta(days=MAX_FORECAST_DAYS):
        raise ValueError(
            f"Arrival is {horizon.days} days out; forecasts only cover "
            f"{MAX_FORECAST_DAYS} days ahead."
        )

    start_date = min(etas[0], dt.datetime.now(dt.timezone.utc)).date()
    end_date = etas[-1].date() + dt.timedelta(days=1)

    resp = requests.get(
        FORECAST_URL,
        params={
            "latitude": ",".join(f"{p.lat:.4f}" for p in points),
            "longitude": ",".join(f"{p.lon:.4f}" for p in points),
            "hourly": ",".join(HOURLY_VARS),
            "timezone": "auto",
            "timeformat": "unixtime",
            "start_date": start_date.isoformat(),
            "end_date": min(end_date, start_date + dt.timedelta(days=MAX_FORECAST_DAYS)).isoformat(),
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    locations = data if isinstance(data, list) else [data]
    if len(locations) != len(points):
        raise RuntimeError(
            f"Weather API returned {len(locations)} locations for {len(points)} points"
        )

    for point, eta, loc in zip(points, etas, locations):
        hourly = loc["hourly"]
        times = hourly["time"]  # unix timestamps (UTC)
        eta_ts = eta.timestamp()
        idx = min(range(len(times)), key=lambda i: abs(times[i] - eta_ts))
        weather = {var: (hourly.get(var) or [None])[idx] if hourly.get(var) else None
                   for var in HOURLY_VARS}
        weather["eta_utc"] = eta
        weather["utc_offset_s"] = int(loc.get("utc_offset_seconds", 0))
        point.weather = weather
