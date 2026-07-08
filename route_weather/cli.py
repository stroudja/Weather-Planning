"""Command-line entry point.

Subcommands (plain `route-weather ORIGIN DEST` implies `check`):

    check     weather along one route, time-matched to your ETAs
    optimize  score every departure slot in a window, recommend the best
    compare   score alternate routes between the same two places
    fleet     one dashboard for many trips (from a JSON file)
    watch     re-check a trip and diff against the last check (for cron)
    climate   what this route is usually like in a given month
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys

from . import climate as climate_mod
from . import report, routing, rules as rules_mod, trip as trip_mod, weather
from .vehicles import PROFILES

SUBCOMMANDS = {"check", "optimize", "compare", "fleet", "watch", "climate"}
DEFAULT_EV_RANGE_KM = 435.0   # ~270 mi, a typical modern EV


def parse_depart(text: str, origin_utc_offset_s: int) -> dt.datetime:
    """Parse a departure time into an aware UTC datetime.

    Accepts "now", relative offsets like "+45m" / "+2h" / "+1.5h", or a
    naive "YYYY-MM-DD HH:MM" interpreted as local time at the origin.
    """
    text = text.strip().lower()
    now = dt.datetime.now(dt.timezone.utc)
    if text == "now":
        return now
    m = re.fullmatch(r"\+(\d+(?:\.\d+)?)\s*([hm])", text)
    if m:
        amount, unit = float(m.group(1)), m.group(2)
        return now + dt.timedelta(hours=amount if unit == "h" else amount / 60)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            naive = dt.datetime.strptime(text, fmt)
            tz = dt.timezone(dt.timedelta(seconds=origin_utc_offset_s))
            return naive.replace(tzinfo=tz).astimezone(dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError(
        f"Can't parse departure time {text!r}. "
        'Use "now", "+2h", "+45m", or "YYYY-MM-DD HH:MM" (origin local time).'
    )


def parse_step(text: str) -> float:
    """'2h' / '90m' -> seconds."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hm])", text.strip().lower())
    if not m:
        raise ValueError(f"Can't parse step {text!r}; use e.g. '2h' or '30m'.")
    amount, unit = float(m.group(1)), m.group(2)
    return amount * 3600 if unit == "h" else amount * 60


def _add_common(p: argparse.ArgumentParser, with_depart=True):
    p.add_argument("origin", help='Start, e.g. "Denver, CO"')
    p.add_argument("destination", help='End, e.g. "Kansas City, MO"')
    p.add_argument("--via", action="append", default=[], metavar="PLACE",
                   help="Intermediate stop (repeatable)")
    if with_depart:
        p.add_argument("--depart", default="now",
                       help='Departure: "now" (default), "+2h", "+45m", or '
                            '"YYYY-MM-DD HH:MM" in origin local time')
    p.add_argument("--interval", type=float, default=30, metavar="MINUTES",
                   help="Checkpoint spacing in minutes of driving (default 30)")
    p.add_argument("--units", choices=["imperial", "metric"], default="imperial")
    p.add_argument("--vehicle", choices=sorted(PROFILES), default="car",
                   help="Vehicle profile: adjusts wind/rain/temp thresholds, "
                        "crosswind checks, EV range planning")
    p.add_argument("--ev-range", type=float, metavar="DIST",
                   help="Rated EV range in display units (miles or km); "
                        "implies charging-stop planning for --vehicle ev")
    p.add_argument("--rules", metavar="RULES.json",
                   help="Go/no-go thresholds file (see route_weather/rules.py)")
    p.add_argument("--html", metavar="PATH", help="Write interactive map report")
    p.add_argument("--json", metavar="PATH", help="Write machine-readable report")
    p.add_argument("--no-alerts", action="store_true",
                   help="Skip NWS severe-weather alert lookups")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="route-weather",
        description="Check weather along an entire driving route, "
                    "time-matched to when you'll actually be at each point.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    _add_common(sub.add_parser("check", help="Weather along one route"))

    opt = sub.add_parser("optimize", help="Find the best departure time")
    _add_common(opt, with_depart=False)
    opt.add_argument("--window", required=True, metavar="START..END",
                     help='e.g. "+0h..+36h" or '
                          '"2026-07-09 06:00..2026-07-10 12:00" (origin local)')
    opt.add_argument("--step", default="2h", help="Slot spacing (default 2h)")

    _add_common(sub.add_parser(
        "compare", help="Score alternate routes (no --via; OSRM alternates "
                        "need exactly two endpoints)"))

    fleet = sub.add_parser("fleet", help="Dashboard for many trips")
    fleet.add_argument("trips_file", metavar="TRIPS.json",
                       help='{"trips": [{"name", "origin", "destination", '
                            '"via"?, "depart"?, "vehicle"?, "ev_range"?}...]}')
    fleet.add_argument("--interval", type=float, default=30)
    fleet.add_argument("--units", choices=["imperial", "metric"], default="imperial")
    fleet.add_argument("--html", metavar="PATH")
    fleet.add_argument("--no-alerts", action="store_true")

    watch = sub.add_parser(
        "watch", help="Re-check a trip; exit 2 if the forecast worsened "
                      "(cron-friendly)")
    _add_common(watch)
    watch.add_argument("--state", default=".route-weather-state.json",
                       help="Where to remember the previous check")

    clim = sub.add_parser("climate", help="Typical conditions for a month")
    clim.add_argument("origin")
    clim.add_argument("destination")
    clim.add_argument("--via", action="append", default=[], metavar="PLACE")
    clim.add_argument("--month", type=int, required=True, choices=range(1, 13),
                      metavar="1-12")
    clim.add_argument("--years", type=int, default=10)
    clim.add_argument("--interval", type=float, default=45)
    clim.add_argument("--units", choices=["imperial", "metric"], default="imperial")

    return p


def _status(msg):
    print(msg, file=sys.stderr)


def _setup(args):
    """Geocode, route, resolve departure/profile/rules. Returns a dict."""
    _status(f"Geocoding and routing {args.origin!r} -> {args.destination!r} ...")
    prep = trip_mod.prepare(args.origin, args.destination,
                            getattr(args, "via", []),
                            alternatives=args.command == "compare")
    origin_offset = weather.get_utc_offset(prep["origin"]["lat"],
                                           prep["origin"]["lon"])
    profile = PROFILES[getattr(args, "vehicle", "car")]

    ev_range_km = None
    if profile.is_ev:
        ev_range_km = getattr(args, "ev_range", None)
        if ev_range_km is not None and args.units == "imperial":
            ev_range_km *= 1.609344
        ev_range_km = ev_range_km or DEFAULT_EV_RANGE_KM

    rules = None
    if getattr(args, "rules", None):
        rules = rules_mod.load_rules(args.rules)

    return {"prep": prep, "origin_offset": origin_offset, "profile": profile,
            "ev_range_km": ev_range_km, "rules": rules}


def _run_one(args, setup, route: routing.Route, depart_utc) -> dict:
    _status(f"Fetching forecasts along {route.name or 'route'} ...")
    return trip_mod.run(
        route, setup["prep"]["origin"], setup["prep"]["destination"],
        depart_utc, setup["origin_offset"],
        interval_min=args.interval, profile=setup["profile"],
        ev_range_km=setup["ev_range_km"], no_alerts=args.no_alerts,
        rules=setup["rules"],
    )


def _write_outputs(args, trip, imperial):
    if getattr(args, "html", None):
        with open(args.html, "w") as f:
            f.write(report.to_html(trip, imperial))
        print(f"Map report written to {args.html}")
    if getattr(args, "json", None):
        with open(args.json, "w") as f:
            f.write(report.to_json(trip))
        print(f"JSON written to {args.json}")


def cmd_check(args) -> int:
    setup = _setup(args)
    depart_utc = parse_depart(args.depart, setup["origin_offset"])
    trip = _run_one(args, setup, setup["prep"]["routes"][0], depart_utc)
    report.print_console(trip, args.units == "imperial")
    _write_outputs(args, trip, args.units == "imperial")
    return 0 if trip.get("verdict", {}).get("verdict") != "NO-GO" else 3


def cmd_optimize(args) -> int:
    setup = _setup(args)
    try:
        start_s, end_s = args.window.split("..", 1)
    except ValueError:
        raise ValueError('--window must look like "+0h..+36h" or '
                         '"2026-07-09 06:00..2026-07-10 12:00"')
    start = parse_depart(start_s, setup["origin_offset"])
    end = parse_depart(end_s, setup["origin_offset"])
    if end <= start:
        raise ValueError("--window end must be after start")
    step_s = parse_step(args.step)

    route = setup["prep"]["routes"][0]
    points = routing.sample_route(route, args.interval * 60)
    n_slots = int((end - start).total_seconds() // step_s) + 1
    _status(f"Scoring {n_slots} departure slots ...")
    results = trip_mod.evaluate_departures(route, points, start, end, step_s,
                                           setup["profile"])
    best = trip_mod.best_departure(results)

    trip_info = {
        "origin": setup["prep"]["origin"],
        "destination": setup["prep"]["destination"],
        "origin_utc_offset_s": setup["origin_offset"],
    }
    report.print_optimizer(results, best, trip_info, args.units == "imperial")

    # Full report + outputs for the recommended slot
    if args.html or args.json:
        trip = _run_one(args, setup, route, best["depart_utc"])
        _write_outputs(args, trip, args.units == "imperial")
    return 0


def cmd_compare(args) -> int:
    setup = _setup(args)
    depart_utc = parse_depart(args.depart, setup["origin_offset"])
    routes = setup["prep"]["routes"]
    if len(routes) == 1:
        _status("Only one route available between these points.")
    trips = [_run_one(args, setup, r, depart_utc) for r in routes]
    imperial = args.units == "imperial"
    report.print_compare(trips, imperial)
    if args.html:
        with open(args.html, "w") as f:
            f.write(report.compare_html(trips, imperial))
        print(f"Comparison map written to {args.html}")
    if args.json:
        with open(args.json, "w") as f:
            f.write(json.dumps([json.loads(report.to_json(t)) for t in trips],
                               indent=2))
        print(f"JSON written to {args.json}")
    return 0


def cmd_fleet(args) -> int:
    with open(args.trips_file) as f:
        spec = json.load(f)
    entries = spec.get("trips", [])
    if not entries:
        raise ValueError(f"No trips found in {args.trips_file}")

    trips = []
    for entry in entries:
        name = entry.get("name") or f"{entry['origin']} → {entry['destination']}"
        _status(f"[{name}] planning ...")
        prep = trip_mod.prepare(entry["origin"], entry["destination"],
                                entry.get("via", []))
        origin_offset = weather.get_utc_offset(prep["origin"]["lat"],
                                               prep["origin"]["lon"])
        depart_utc = parse_depart(entry.get("depart", "now"), origin_offset)
        profile = PROFILES[entry.get("vehicle", "car")]
        ev_range_km = entry.get("ev_range") if profile.is_ev else None
        if profile.is_ev:
            ev_range_km = ev_range_km or DEFAULT_EV_RANGE_KM
        t = trip_mod.run(prep["routes"][0], prep["origin"],
                         prep["destination"], depart_utc, origin_offset,
                         interval_min=args.interval, profile=profile,
                         ev_range_km=ev_range_km, no_alerts=args.no_alerts)
        t["trip_name"] = name
        trips.append(t)

    imperial = args.units == "imperial"
    report.print_fleet(trips, imperial)
    if args.html:
        with open(args.html, "w") as f:
            f.write(report.fleet_html(trips, imperial))
        print(f"Fleet dashboard written to {args.html}")
    return 0


def _watch_key(args) -> str:
    raw = json.dumps([args.origin, args.destination, args.via, args.vehicle,
                      args.depart], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cmd_watch(args) -> int:
    setup = _setup(args)
    depart_utc = parse_depart(args.depart, setup["origin_offset"])
    trip = _run_one(args, setup, setup["prep"]["routes"][0], depart_utc)
    imperial = args.units == "imperial"
    report.print_console(trip, imperial)

    key = _watch_key(args)
    try:
        with open(args.state) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    prev = state.get(key)

    current = {
        "peak_score": trip["peak_score"],
        "mean_score": round(trip["mean_score"], 2),
        "alert_ids": sorted(a["id"] for a in trip["alerts"]),
        "verdict": trip.get("verdict", {}).get("verdict"),
        "checked_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    state[key] = current
    with open(args.state, "w") as f:
        json.dump(state, f, indent=2)

    if prev is None:
        print("Watch: first check recorded — run again later to see changes.")
        return 0

    worsened = (
        current["peak_score"] > prev["peak_score"]
        or len(set(current["alert_ids"]) - set(prev["alert_ids"])) > 0
        or (prev.get("verdict") in (None, "GO", "CAUTION")
            and current.get("verdict") == "NO-GO")
    )
    if worsened:
        print(f"Watch: CONDITIONS WORSENED since {prev['checked_utc']} "
              f"(peak {prev['peak_score']} -> {current['peak_score']}, "
              f"{len(current['alert_ids'])} alert(s)).")
        return 2
    if current["peak_score"] < prev["peak_score"]:
        print(f"Watch: improved since {prev['checked_utc']} "
              f"(peak {prev['peak_score']} -> {current['peak_score']}).")
    else:
        print("Watch: no significant change.")
    return 0


def cmd_climate(args) -> int:
    _status(f"Geocoding and routing {args.origin!r} -> {args.destination!r} ...")
    prep = trip_mod.prepare(args.origin, args.destination, args.via)
    route = prep["routes"][0]
    points = routing.sample_route(route, args.interval * 60)
    _status(f"Fetching {args.years} years of history for {len(points)} "
            "checkpoints ...")
    stats = climate_mod.fetch_climatology(points, args.month, args.years)
    trip_info = {"origin": prep["origin"], "destination": prep["destination"]}
    report.print_climate(stats, args.month, args.years, trip_info,
                         args.units == "imperial")
    return 0


COMMANDS = {
    "check": cmd_check,
    "optimize": cmd_optimize,
    "compare": cmd_compare,
    "fleet": cmd_fleet,
    "watch": cmd_watch,
    "climate": cmd_climate,
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Back-compat: `route-weather ORIGIN DEST` == `route-weather check ...`
    if argv and argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        argv.insert(0, "check")
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
