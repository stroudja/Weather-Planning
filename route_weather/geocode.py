"""Geocoding via OpenStreetMap Nominatim (free, no API key)."""

import time

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "route-weather/0.1 (weather-planning tool)"

_last_call = 0.0


def geocode(query: str) -> dict:
    """Resolve a place name to coordinates.

    Returns {"lat": float, "lon": float, "name": str}.
    Raises ValueError if the place can't be found.
    """
    global _last_call
    # Nominatim usage policy: max 1 request/second
    wait = 1.0 - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    _last_call = time.monotonic()
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not find a location for {query!r}")
    hit = results[0]
    return {
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
        "name": hit.get("display_name", query),
    }
