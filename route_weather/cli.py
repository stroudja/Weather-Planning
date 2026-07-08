"""Command-line entry point.

Example:
    python -m route_weather "Denver, CO" "Kansas City, MO" \\
        --depart "+2h" --interval 30 --html trip.html
"""

import argparse
import datetime as dt
import re
import sys

from . import alerts, geocode, hazards, report, routing, weather


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="route-weather",
        description="Check weather along an entire driving route, "
                    "time-matched to when you'll actually be at each point.",
    )
    p.add_argument("origin", help='Start, e.g. "Denver, CO"')
    p.add_argument("destination", help='End, e.g. "Kansas City, MO"')
    p.add_argument("--via", action="append", default=[], metavar="PLACE",
                   help="Intermediate stop (repeatable)")
    p.add_argument("--depart", default="now",
                   help='Departure: "now" (default), "+2h", "+45m", or '
                        '"YYYY-MM-DD HH:MM" in origin local time')
    p.add_argument("--interval", type=float, default=30, metavar="MINUTES",
                   help="Checkpoint spacing in minutes of driving (default 30)")
    p.add_argument("--units", choices=["imperial", "metric"], default="imperial")
    p.add_argument("--html", metavar="PATH", help="Write interactive map report to PATH")
    p.add_argument("--json", metavar="PATH", help="Write machine-readable report to PATH")
    p.add_argument("--no-alerts", action="store_true",
                   help="Skip NWS severe-weather alert lookups")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    imperial = args.units == "imperial"

    try:
        print(f"Geocoding {args.origin!r} ...", file=sys.stderr)
        origin = geocode.geocode(args.origin)
        vias = []
        for v in args.via:
            print(f"Geocoding {v!r} ...", file=sys.stderr)
            vias.append(geocode.geocode(v))
        print(f"Geocoding {args.destination!r} ...", file=sys.stderr)
        destination = geocode.geocode(args.destination)

        origin_offset = weather.get_utc_offset(origin["lat"], origin["lon"])
        depart_utc = parse_depart(args.depart, origin_offset)

        print("Routing ...", file=sys.stderr)
        route = routing.get_route([origin, *vias, destination])
        points = routing.sample_route(route, args.interval * 60)

        print(f"Fetching forecasts for {len(points)} checkpoints ...", file=sys.stderr)
        weather.fetch_weather(points, depart_utc)
        for p in points:
            p.score, p.hazards = hazards.assess(p.weather)

        nws_alerts = []
        if not args.no_alerts:
            print("Checking NWS alerts ...", file=sys.stderr)
            nws_alerts = alerts.fetch_alerts(points)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    trip = {
        "origin": origin,
        "destination": destination,
        "depart_utc": depart_utc,
        "origin_utc_offset_s": origin_offset,
        "total_distance_m": route.total_distance,
        "total_duration_s": route.total_duration,
        "route_coords": route.coords,
        "points": points,
        "alerts": nws_alerts,
    }

    report.print_console(trip, imperial)
    if args.html:
        with open(args.html, "w") as f:
            f.write(report.to_html(trip, imperial))
        print(f"Map report written to {args.html}")
    if args.json:
        with open(args.json, "w") as f:
            f.write(report.to_json(trip))
        print(f"JSON written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
