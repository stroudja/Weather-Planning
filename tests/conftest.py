"""Shared fixtures: a fake HTTP layer so the whole pipeline runs offline."""

import datetime as dt

import pytest
import requests

from route_weather.weather import HOURLY_VARS


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def osrm_payload(alternatives=False):
    # 5-point geometry, 4 segments of 30 min / 40 km each (2h, 160 km total)
    coords = [[-104.99, 39.74], [-104.0, 39.5], [-103.0, 39.3],
              [-102.0, 39.2], [-101.0, 39.1]]
    route = {
        "geometry": {"coordinates": coords},
        "duration": 7200.0,
        "distance": 160000.0,
        "legs": [{
            "summary": "I 70",
            "annotation": {"duration": [1800.0] * 4, "distance": [40000.0] * 4},
        }],
    }
    routes = [route]
    if alternatives:
        alt = {
            "geometry": {"coordinates": [[-104.99, 39.74], [-103.5, 40.2],
                                         [-101.0, 39.1]]},
            "duration": 9000.0,
            "distance": 180000.0,
            "legs": [{
                "summary": "US 36",
                "annotation": {"duration": [4500.0] * 2,
                               "distance": [90000.0] * 2},
            }],
        }
        routes.append(alt)
    return {"code": "Ok", "routes": routes}


def forecast_payload(n_locations):
    """Clear everywhere except a thunderstorm parked over the last location."""
    now = int(dt.datetime.now(dt.timezone.utc).timestamp() // 3600 * 3600)
    times = [now + h * 3600 for h in range(-2, 24)]
    locations = []
    for i in range(n_locations):
        hourly = {"time": times}
        for var in HOURLY_VARS:
            hourly[var] = [0.0] * len(times)
        hourly["temperature_2m"] = [22.0] * len(times)
        hourly["visibility"] = [20000.0] * len(times)
        if i == n_locations - 1:  # thunderstorm at the destination
            hourly["weather_code"] = [95] * len(times)
            hourly["wind_gusts_10m"] = [70.0] * len(times)
        locations.append({"hourly": hourly, "utc_offset_seconds": -21600})
    return locations


def nws_payload():
    return {"features": [{
        "id": "alert-1",
        "properties": {
            "event": "Severe Thunderstorm Warning",
            "severity": "Severe",
            "headline": "Severe thunderstorms expected",
            "onset": "2026-07-08T12:00:00-06:00",
            "ends": "2026-07-08T20:00:00-06:00",
        },
    }]}


def archive_payload(n_locations, start_date, end_date):
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    dates = []
    d = start
    while d <= end:
        dates.append(d.isoformat())
        d += dt.timedelta(days=1)
    n = len(dates)
    locations = []
    for _ in range(n_locations):
        locations.append({
            "daily": {
                "time": dates,
                "temperature_2m_max": [1.0] * n,
                "temperature_2m_min": [-9.0] * n,
                # wet every 4th day, snow every 10th, gusty every 5th
                "precipitation_sum": [2.0 if i % 4 == 0 else 0.0 for i in range(n)],
                "snowfall_sum": [3.0 if i % 10 == 0 else 0.0 for i in range(n)],
                "wind_gusts_10m_max": [70.0 if i % 5 == 0 else 30.0 for i in range(n)],
            },
        })
    return locations


@pytest.fixture
def fake_http(monkeypatch):
    def fake_get(url, params=None, **kwargs):
        params = params or {}
        if "nominatim" in url:
            return FakeResponse([{"lat": "39.74", "lon": "-104.99",
                                  "display_name": params["q"]}])
        if "project-osrm" in url:
            return FakeResponse(osrm_payload(params.get("alternatives") == "true"))
        if "archive-api" in url:
            n = len(str(params["latitude"]).split(","))
            payload = archive_payload(n, params["start_date"], params["end_date"])
            return FakeResponse(payload if n > 1 else payload[0])
        if "open-meteo" in url:
            if "hourly" not in params:  # utc-offset probe
                return FakeResponse({"utc_offset_seconds": -21600})
            n = len(str(params["latitude"]).split(","))
            payload = forecast_payload(n)
            return FakeResponse(payload if n > 1 else payload[0])
        if "weather.gov" in url:
            return FakeResponse(nws_payload())
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests.Session, "get",
                        lambda self, url, **kw: fake_get(url, **kw))
    monkeypatch.setattr("route_weather.geocode.time.sleep", lambda s: None)
