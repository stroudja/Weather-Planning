"""End-to-end pipeline tests with mocked HTTP — no network required."""

import datetime as dt
import json

from route_weather import cli


def test_full_pipeline(fake_http, tmp_path, capsys):
    html_path = tmp_path / "trip.html"
    json_path = tmp_path / "trip.json"
    rc = cli.main([
        "Denver, CO", "Kansas City, MO",   # implicit `check`
        "--depart", "now", "--interval", "30",
        "--html", str(html_path), "--json", str(json_path),
    ])
    assert rc == 0

    out = capsys.readouterr().out
    assert "Thunderstorm" in out
    assert "Severe Thunderstorm Warning" in out

    html = html_path.read_text()
    assert "leaflet" in html and "circleMarker" in html
    assert "rainviewer" in html   # radar overlay wiring present

    data = json.loads(json_path.read_text())
    # 2h route at 30-min intervals -> checkpoints at 0, 30, 60, 90, 120 min
    assert len(data["points"]) == 5
    assert data["points"][-1]["score"] >= 7  # thunderstorm + gusts flagged
    assert data["nws_alerts"][0]["event"] == "Severe Thunderstorm Warning"
    assert data["total_distance_m"] == 160000.0
    # thunderstorm at the far end slows the last leg -> adjusted > nominal
    assert data["adjusted_duration_s"] > data["total_duration_s"]


def test_metric_units_and_no_alerts(fake_http, capsys):
    rc = cli.main(["check", "Denver, CO", "Limon, CO",
                   "--units", "metric", "--no-alerts"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "°C" in out
    assert "Severe Thunderstorm Warning" not in out


def test_truck_profile_scores_wind_harder(fake_http, capsys):
    rc = cli.main(["check", "Denver, CO", "Limon, CO",
                   "--vehicle", "truck", "--no-alerts"])
    assert rc == 0
    out = capsys.readouterr().out
    # 70 km/h gusts exceed the truck damaging threshold (65)
    assert "Damaging wind gusts" in out


def test_ev_charging_plan_in_output(fake_http, capsys):
    rc = cli.main(["check", "Denver, CO", "Kansas City, MO",
                   "--vehicle", "ev", "--ev-range", "60",  # tiny range, miles
                   "--no-alerts"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "EV range in this weather" in out
    assert "charging stop" in out


def test_rules_no_go_exit_code(fake_http, tmp_path, capsys):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"max_gust_kmh": 50}))
    rc = cli.main(["check", "Denver, CO", "Kansas City, MO",
                   "--rules", str(rules), "--no-alerts"])
    assert rc == 3   # NO-GO -> distinct exit code
    out = capsys.readouterr().out
    assert "NO-GO" in out


def test_bad_departure_time(fake_http):
    rc = cli.main(["check", "Denver, CO", "Limon, CO", "--depart", "sometime"])
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
