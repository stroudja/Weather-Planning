"""Forecasts from Open-Meteo (free, no API key).

All values are fetched in metric units; display conversion happens in the
report layer so hazard scoring always sees consistent units.

The full hourly series is fetched once per checkpoint (`fetch_series`) and
then time-matched locally (`assign_forecast`), so re-evaluating a different
departure time — the optimizer, weather-adjusted ETAs — costs no extra
API calls.
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


def fetch_series(points: list, start_utc: dt.datetime, end_utc: dt.datetime) -> None:
    """Fetch the hourly forecast series for every RoutePoint in one request.

    Attaches point.series = {"time": [unix...], <var>: [...], "utc_offset_s": int}.
    """
    now = dt.datetime.now(dt.timezone.utc)
    if end_utc - now > dt.timedelta(days=MAX_FORECAST_DAYS):
        raise ValueError(
            f"Trip extends {(end_utc - now).days} days out; forecasts only "
            f"cover {MAX_FORECAST_DAYS} days ahead."
        )

    start_date = min(start_utc, now).date() - dt.timedelta(days=1)
    end_date = min(end_utc.date() + dt.timedelta(days=1),
                   now.date() + dt.timedelta(days=MAX_FORECAST_DAYS))

    resp = requests.get(
        FORECAST_URL,
        params={
            "latitude": ",".join(f"{p.lat:.4f}" for p in points),
            "longitude": ",".join(f"{p.lon:.4f}" for p in points),
            "hourly": ",".join(HOURLY_VARS),
            "timezone": "auto",
            "timeformat": "unixtime",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
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

    for point, loc in zip(points, locations):
        series = dict(loc["hourly"])
        series["utc_offset_s"] = int(loc.get("utc_offset_seconds", 0))
        point.series = series


def forecast_at(series: dict, when_utc: dt.datetime) -> dict:
    """Nearest-hour forecast values from a fetched series."""
    times = series["time"]
    ts = when_utc.timestamp()
    idx = min(range(len(times)), key=lambda i: abs(times[i] - ts))
    weather = {}
    for var in HOURLY_VARS:
        values = series.get(var)
        weather[var] = values[idx] if values else None
    weather["eta_utc"] = when_utc
    weather["utc_offset_s"] = series["utc_offset_s"]
    return weather


def assign_forecast(points: list, depart_utc: dt.datetime) -> None:
    """Set point.weather for each point's current eta_offset_s."""
    for p in points:
        eta = depart_utc + dt.timedelta(seconds=p.eta_offset_s)
        p.weather = forecast_at(p.series, eta)


def fetch_weather(points: list, depart_utc: dt.datetime) -> None:
    """Convenience: fetch series covering the trip and assign forecasts."""
    if not points:
        return
    end = depart_utc + dt.timedelta(seconds=points[-1].eta_offset_s * 2.5 + 7200)
    fetch_series(points, depart_utc, end)
    assign_forecast(points, depart_utc)
