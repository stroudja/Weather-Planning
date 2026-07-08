"""Tests for the newer subsystems: solar, crosswind, speed factors, EV,
rules, optimizer, compare, fleet, watch, climate."""

import datetime as dt
import json

import pytest

from route_weather import cli, ev, hazards, solar, trip as trip_mod, weather
from route_weather.routing import Route, RoutePoint
from route_weather.vehicles import PROFILES

DENVER = (39.74, -104.99)


# ------------------------------------------------------------------ solar

def test_sun_high_at_summer_noon():
    when = dt.datetime(2026, 6, 21, 19, 0, tzinfo=dt.timezone.utc)  # 13:00 MDT
    elevation, _ = solar.sun_position(when, *DENVER)
    assert elevation > 60


def test_night_at_local_1am():
    when = dt.datetime(2026, 6, 21, 7, 0, tzinfo=dt.timezone.utc)  # 01:00 MDT
    assert solar.is_night(when, *DENVER)


def test_glare_needs_low_sun_and_alignment():
    when = dt.datetime(2026, 6, 21, 19, 0, tzinfo=dt.timezone.utc)  # sun high
    assert not solar.glare_risk(when, *DENVER, road_bearing=180)


# ------------------------------------------------- crosswind & speed factor

def test_crosswind_component_geometry():
    # Wind from the east (90°), driving north (0°) -> full crosswind
    assert hazards.crosswind_component(60, 90, 0) == pytest.approx(60)
    # Wind straight down the road -> none
    assert hazards.crosswind_component(60, 0, 0) == pytest.approx(0)


def test_truck_flags_crosswind_car_does_not():
    w = {"weather_code": 0, "temperature_2m": 15, "wind_speed_10m": 40,
         "wind_gusts_10m": 45, "wind_direction_10m": 90, "visibility": 20000,
         "precipitation": 0, "snowfall": 0}
    car_score, car_flags = hazards.assess(w, PROFILES["car"], road_bearing=0)
    truck_score, truck_flags = hazards.assess(w, PROFILES["truck"], road_bearing=0)
    assert not any("rosswind" in f for f in car_flags)
    assert any("rosswind" in f for f in truck_flags)
    assert truck_score > car_score


def test_speed_factors():
    assert hazards.speed_factor({"weather_code": 0}) == 1.0
    assert hazards.speed_factor({"weather_code": 75}) <= 0.6   # heavy snow
    assert hazards.speed_factor({"weather_code": 67}) <= 0.5   # frz rain
    assert hazards.speed_factor({"weather_code": 0, "visibility": 300}) <= 0.6


# ---------------------------------------------------------------------- EV

def test_ev_temp_curve():
    assert ev.temp_efficiency(20) == pytest.approx(1.0)
    assert ev.temp_efficiency(-10) == pytest.approx(0.70, abs=0.01)
    assert ev.temp_efficiency(-40) == pytest.approx(0.50)  # clamped


def test_headwind_hurts_tailwind_helps():
    assert ev.headwind_efficiency(30, 0, 0) < 1.0    # wind from ahead
    assert ev.headwind_efficiency(30, 180, 0) > 1.0  # wind from behind


# ------------------------------------------------------------- optimizer

def _fake_series_points(storm_until_h):
    """3 checkpoints, hourly series: thunderstorms until storm_until_h
    hours from now, then clear."""
    now = int(dt.datetime.now(dt.timezone.utc).timestamp() // 3600 * 3600)
    times = [now + h * 3600 for h in range(0, 48)]
    points = []
    for i in range(3):
        series = {"time": times, "utc_offset_s": 0}
        for var in weather.HOURLY_VARS:
            series[var] = [0.0] * len(times)
        series["temperature_2m"] = [20.0] * len(times)
        series["visibility"] = [20000.0] * len(times)
        series["weather_code"] = [95 if h < storm_until_h else 0
                                  for h in range(len(times))]
        p = RoutePoint(lat=39 + i, lon=-104, eta_offset_s=i * 1800.0,
                       distance_m=i * 40000.0)
        p.series = series
        points.append(p)
    return points


def test_optimizer_picks_departure_after_storm(monkeypatch):
    monkeypatch.setattr(weather, "fetch_series", lambda *a, **k: None)
    points = _fake_series_points(storm_until_h=6)
    route = Route(coords=[(39, -104), (41, -104)], cum_duration=[0, 3600],
                  cum_distance=[0, 80000], total_duration=3600,
                  total_distance=80000)
    now = dt.datetime.now(dt.timezone.utc)
    results = trip_mod.evaluate_departures(
        route, points, now, now + dt.timedelta(hours=12), 2 * 3600,
        PROFILES["car"])
    best = trip_mod.best_departure(results)
    hours_out = (best["depart_utc"] - now).total_seconds() / 3600
    assert hours_out >= 6          # waits out the storm
    assert best["peak_score"] == 0
    assert results[0]["peak_score"] >= 7   # leaving now is bad


# ------------------------------------------------------------ CLI: compare

def test_compare_lists_both_routes(fake_http, tmp_path, capsys):
    html = tmp_path / "cmp.html"
    rc = cli.main(["compare", "Denver, CO", "Kansas City, MO",
                   "--no-alerts", "--html", str(html)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "I 70" in out and "US 36" in out
    assert "Recommendation" in out
    assert "US 36" in html.read_text()


# -------------------------------------------------------------- CLI: fleet

def test_fleet_dashboard(fake_http, tmp_path, capsys):
    trips = tmp_path / "trips.json"
    trips.write_text(json.dumps({"trips": [
        {"name": "Truck 12", "origin": "Denver, CO",
         "destination": "Kansas City, MO", "vehicle": "truck"},
        {"name": "Van 3", "origin": "Limon, CO",
         "destination": "Salina, KS", "depart": "+2h"},
    ]}))
    html = tmp_path / "fleet.html"
    rc = cli.main(["fleet", str(trips), "--no-alerts", "--html", str(html)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Truck 12" in out and "Van 3" in out
    assert "Truck 12" in html.read_text()


# -------------------------------------------------------------- CLI: watch

def test_watch_first_run_then_worsened(fake_http, tmp_path, capsys):
    state = tmp_path / "state.json"
    argv = ["watch", "Denver, CO", "Kansas City, MO",
            "--no-alerts", "--state", str(state)]
    assert cli.main(argv) == 0
    assert "first check recorded" in capsys.readouterr().out

    # Same forecast -> no change
    assert cli.main(argv) == 0
    assert "no significant change" in capsys.readouterr().out

    # Pretend the last check was calm -> now it's worse -> exit 2
    data = json.loads(state.read_text())
    key = next(iter(data))
    data[key]["peak_score"] = 0
    state.write_text(json.dumps(data))
    assert cli.main(argv) == 2
    assert "WORSENED" in capsys.readouterr().out


# ------------------------------------------------------------ CLI: climate

def test_climate_table(fake_http, capsys):
    rc = cli.main(["climate", "Denver, CO", "Kansas City, MO",
                   "--month", "1", "--years", "2", "--units", "metric"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "January" in out
    # constant fixtures: highs 1°C / lows -9°C, wet every 4th day (~26%
    # of January days in the generated 2-year window)
    assert "1°C/-9°C" in out
    assert "26%" in out
