"""Route climatology: what this drive is *usually* like in a given month.

Uses the Open-Meteo historical archive (ERA5 reanalysis, free, no key) to
aggregate the last N years of daily conditions at each checkpoint.
"""

import datetime as dt

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "wind_gusts_10m_max",
]


def fetch_climatology(points: list, month: int, years: int = 10) -> list:
    """Return per-checkpoint stats for `month` over the last `years` years.

    [{"lat", "lon", "distance_m", "avg_high_c", "avg_low_c", "record_low_c",
      "record_high_c", "precip_day_pct", "snow_day_pct", "high_wind_day_pct",
      "max_gust_kmh", "n_days"} ...]
    """
    end_year = dt.date.today().year - 1
    start = dt.date(end_year - years + 1, 1, 1)
    end = dt.date(end_year, 12, 31)

    resp = requests.get(
        ARCHIVE_URL,
        params={
            "latitude": ",".join(f"{p.lat:.4f}" for p in points),
            "longitude": ",".join(f"{p.lon:.4f}" for p in points),
            "daily": ",".join(DAILY_VARS),
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    locations = data if isinstance(data, list) else [data]

    stats = []
    for point, loc in zip(points, locations):
        daily = loc["daily"]
        dates = daily["time"]  # ISO date strings
        idx = [i for i, d in enumerate(dates) if int(d[5:7]) == month]

        def vals(var):
            arr = daily.get(var) or []
            return [arr[i] for i in idx if i < len(arr) and arr[i] is not None]

        highs = vals("temperature_2m_max")
        lows = vals("temperature_2m_min")
        precip = vals("precipitation_sum")
        snow = vals("snowfall_sum")
        gusts = vals("wind_gusts_10m_max")
        n = max(len(highs), 1)

        stats.append({
            "lat": point.lat, "lon": point.lon,
            "distance_m": point.distance_m,
            "avg_high_c": sum(highs) / n if highs else None,
            "avg_low_c": sum(lows) / max(len(lows), 1) if lows else None,
            "record_low_c": min(lows) if lows else None,
            "record_high_c": max(highs) if highs else None,
            "precip_day_pct": 100 * sum(1 for v in precip if v >= 1) / max(len(precip), 1),
            "snow_day_pct": 100 * sum(1 for v in snow if v >= 1) / max(len(snow), 1),
            "high_wind_day_pct": 100 * sum(1 for v in gusts if v >= 60) / max(len(gusts), 1),
            "max_gust_kmh": max(gusts) if gusts else None,
            "n_days": len(highs),
        })
    return stats
