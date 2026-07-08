"""End-to-end pipeline test with mocked HTTP — no network required."""

import datetime as dt
import json

import pytest
import requests

from route_weather import cli
from route_weather.weather import HOURLY_VARS


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _osrm_payload():
    # 5-point geometry, 4 segments of 30 min / 40 km each (2h, 160 km total)
    coords = [[-104.99, 39.74], [-104.0, 39.5], [-103.0, 39.3],
              [-102.0, 39.2], [-101.0, 39.1]]
    return {
        "code": "Ok",
        "routes": [{
            "geometry": {"coordinates": coords},
            "duration": 7200.0,
            "distance": 160000.0,
            "legs": [{"annotation": {
                "duration": [1800.0] * 4,
                "distance": [40000.0] * 4,
            }}],
        }],
    }


def _forecast_payload(n_locations):
    now = int(dt.datetime.now(dt.timezone.utc).timestamp() // 3600 * 3600)
    times = [now + h * 3600 for h in range(-2, 12)]
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


def _nws_payload():
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


@pytest.fixture
def fake_http(monkeypatch):
    def fake_get(url, params=None, **kwargs):
        params = params or {}
        if "nominatim" in url:
            return FakeResponse([{"lat": "39.74", "lon": "-104.99",
                                  "display_name": params["q"]}])
        if "project-osrm" in url:
            return FakeResponse(_osrm_payload())
        if "open-meteo" in url:
            if "hourly" not in params:  # utc-offset probe
                return FakeResponse({"utc_offset_seconds": -21600})
            n = len(str(params["latitude"]).split(","))
            payload = _forecast_payload(n)
            return FakeResponse(payload if n > 1 else payload[0])
        if "weather.gov" in url:
            return FakeResponse(_nws_payload())
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests.Session, "get",
                        lambda self, url, **kw: fake_get(url, **kw))
    monkeypatch.setattr("route_weather.geocode.time.sleep", lambda s: None)


def test_full_pipeline(fake_http, tmp_path, capsys):
    html_path = tmp_path / "trip.html"
    json_path = tmp_path / "trip.json"
    rc = cli.main([
        "Denver, CO", "Kansas City, MO",
        "--depart", "now", "--interval", "30",
        "--html", str(html_path), "--json", str(json_path),
    ])
    assert rc == 0

    out = capsys.readouterr().out
    assert "Thunderstorm" in out
    assert "Severe Thunderstorm Warning" in out

    html = html_path.read_text()
    assert "leaflet" in html and "circleMarker" in html

    data = json.loads(json_path.read_text())
    # 2h route at 30-min intervals -> checkpoints at 0, 30, 60, 90, 120 min
    assert len(data["points"]) == 5
    assert data["points"][-1]["score"] >= 7  # thunderstorm + gusts flagged
    assert data["nws_alerts"][0]["event"] == "Severe Thunderstorm Warning"
    assert data["total_distance_m"] == 160000.0


def test_metric_units_and_no_alerts(fake_http, capsys):
    rc = cli.main(["Denver, CO", "Limon, CO", "--units", "metric", "--no-alerts"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "°C" in out
    assert "Severe Thunderstorm Warning" not in out


def test_bad_departure_time(fake_http, capsys):
    rc = cli.main(["Denver, CO", "Limon, CO", "--depart", "sometime"])
    assert rc == 1


def test_parse_depart_relative():
    now = dt.datetime.now(dt.timezone.utc)
    t = cli.parse_depart("+2h", 0)
    assert abs((t - now).total_seconds() - 7200) < 5
    t = cli.parse_depart("+45m", 0)
    assert abs((t - now).total_seconds() - 2700) < 5


def test_parse_depart_local_naive():
    # 08:00 at UTC-6 == 14:00 UTC
    t = cli.parse_depart("2026-07-10 08:00", -21600)
    assert t == dt.datetime(2026, 7, 10, 14, 0, tzinfo=dt.timezone.utc)
