"""Report rendering: rich console output, JSON export, and Leaflet HTML maps.

HTML maps support multiple routes (compare / fleet) and an optional live
radar overlay (RainViewer tiles, fetched client-side in the browser).
"""

import datetime as dt
import html as html_mod
import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import hazards, units
from .vehicles import PROFILES

SCORE_COLORS = {"green": "#1a9850", "yellow": "#e6b800", "orange": "#f46d43", "red": "#d73027"}
VERDICT_STYLE = {"GO": "green", "CAUTION": "yellow", "NO-GO": "red"}


def _trip_title(trip: dict) -> str:
    return (f"{trip['origin']['name'].split(',')[0]}  →  "
            f"{trip['destination']['name'].split(',')[0]}")


def _trip_subtitle(trip: dict, imperial: bool) -> str:
    dur = units.fmt_duration(trip["total_duration_s"])
    adj = trip.get("adjusted_duration_s", trip["total_duration_s"])
    if adj - trip["total_duration_s"] > 300:
        dur += f" nominal, {units.fmt_duration(adj)} in this weather"
    via = f" via {trip['route_name']}" if trip.get("route_name") else ""
    vehicle = PROFILES[trip.get("vehicle", "car")].label.lower()
    return (f"{units.fmt_distance(trip['total_distance_m'], imperial)}{via} · "
            f"{dur} · {vehicle} · departing "
            f"{units.fmt_local_time(trip['depart_utc'], trip['origin_utc_offset_s'])}")


# ---------------------------------------------------------------- console

def print_console(trip: dict, imperial: bool) -> None:
    console = Console()
    points = trip["points"]

    console.print()
    console.print(Panel.fit(
        f"[bold]{_trip_title(trip)}[/bold]\n{_trip_subtitle(trip, imperial)}",
        title="Route Weather", border_style="cyan",
    ))

    table = Table(show_lines=False, pad_edge=False)
    table.add_column("Mile" if imperial else "Km", justify="right")
    table.add_column("ETA (local)")
    table.add_column("Conditions")
    table.add_column("Temp", justify="right")
    table.add_column("Feels", justify="right")
    table.add_column("Precip", justify="right")
    table.add_column("Wind (gust)", justify="right")
    table.add_column("Vis", justify="right")
    table.add_column("Risk", justify="center")

    for p in points:
        w = p.weather
        label, color = hazards.level_for(p.score)
        wind = (f"{units.fmt_speed(w.get('wind_speed_10m'), imperial)} "
                f"{units.compass(w.get('wind_direction_10m'))} "
                f"({units.fmt_speed(w.get('wind_gusts_10m'), imperial)})")
        precip_prob = w.get("precipitation_probability")
        precip = units.fmt_precip(w.get("precipitation"), imperial)
        if precip_prob is not None:
            precip = f"{precip} {precip_prob:.0f}%"
        table.add_row(
            f"{p.distance_m / (1609.344 if imperial else 1000):.0f}",
            units.fmt_local_time(w["eta_utc"], w["utc_offset_s"]),
            hazards.describe_code(w.get("weather_code")),
            units.fmt_temp(w.get("temperature_2m"), imperial),
            units.fmt_temp(w.get("apparent_temperature"), imperial),
            precip,
            wind,
            units.fmt_visibility(w.get("visibility"), imperial),
            f"[{color}]{p.score} {label}[/{color}]",
        )
    console.print(table)

    flagged = [p for p in points if p.hazards]
    if flagged:
        console.print("\n[bold]Hazards along the way:[/bold]")
        for p in flagged:
            label, color = hazards.level_for(p.score)
            console.print(
                f"  [{color}]•[/{color}] "
                f"{units.fmt_distance(p.distance_m, imperial)} in "
                f"({units.fmt_local_time(p.weather['eta_utc'], p.weather['utc_offset_s'])}): "
                + "; ".join(p.hazards)
            )
    else:
        console.print("\n[green]No notable weather hazards flagged along this route.[/green]")

    if trip["alerts"]:
        console.print("\n[bold]Active NWS alerts on route (current, not forecast):[/bold]")
        for a in trip["alerts"]:
            sev_color = {"Extreme": "red", "Severe": "red", "Moderate": "orange3"}.get(a["severity"], "yellow")
            console.print(f"  [{sev_color}]⚠ {a['event']} ({a['severity']})[/{sev_color}]")
            if a["headline"]:
                console.print(f"    {a['headline']}")

    if "ev" in trip:
        e = trip["ev"]
        console.print(
            f"\n[bold]EV range in this weather:[/bold] "
            f"{units.fmt_distance(e['effective_range_km'] * 1000, imperial)} effective "
            f"(rated {units.fmt_distance(e['rated_range_km'] * 1000, imperial)}, "
            f"{e['avg_efficiency']:.0%} efficiency, worst {e['worst_efficiency']:.0%})"
        )
        if e["charge_stops"]:
            console.print(f"  Plan ~{e['n_stops']} charging stop(s):")
            for s in e["charge_stops"]:
                console.print(
                    f"  ⚡ around {units.fmt_distance(s['distance_m'], imperial)} in "
                    f"({units.fmt_duration(s['eta_offset_s'])} of driving)")
        else:
            console.print("  No charging stops needed.")

    worst = max(points, key=lambda p: p.score)
    label, color = hazards.level_for(worst.score)
    console.print(
        f"\n[bold]Trip risk:[/bold] [{color}]{label}[/{color}] "
        f"(peak {worst.score}/10 at {units.fmt_distance(worst.distance_m, imperial)} in)"
    )

    if "verdict" in trip:
        v = trip["verdict"]
        style = VERDICT_STYLE[v["verdict"]]
        lines = [f"[bold {style}]{v['verdict']}[/bold {style}]"]
        lines += [f"[red]✗ {r}[/red]" for r in v["violations"]]
        lines += [f"[yellow]! {r}[/yellow]" for r in v["warnings"]]
        console.print(Panel("\n".join(lines), title="Go / no-go rules",
                            border_style=style))
    console.print()


def print_optimizer(results: list, best: dict, trip_info: dict,
                    imperial: bool) -> None:
    console = Console()
    offset = trip_info["origin_utc_offset_s"]
    console.print()
    console.print(Panel.fit(
        f"[bold]{_trip_title(trip_info)}[/bold]\n"
        f"Scored {len(results)} departure slots",
        title="Departure optimizer", border_style="cyan"))

    table = Table(pad_edge=False)
    table.add_column("Depart (local)")
    table.add_column("Peak", justify="right")
    table.add_column("Avg", justify="right")
    table.add_column("Drive time", justify="right")
    table.add_column("Worst conditions en route")
    for r in results:
        label, color = hazards.level_for(r["peak_score"])
        mark = " [bold cyan]◀ best[/bold cyan]" if r is best else ""
        table.add_row(
            units.fmt_local_time(r["depart_utc"], offset),
            f"[{color}]{r['peak_score']}[/{color}]",
            f"{r['mean_score']:.1f}",
            units.fmt_duration(r["adjusted_duration_s"]),
            r["worst"] + mark,
        )
    console.print(table)
    label, color = hazards.level_for(best["peak_score"])
    console.print(
        f"\n[bold]Recommendation:[/bold] leave "
        f"[bold]{units.fmt_local_time(best['depart_utc'], offset)}[/bold] — "
        f"peak risk [{color}]{best['peak_score']}/10 ({label})[/{color}], "
        f"about {units.fmt_duration(best['adjusted_duration_s'])} of driving.\n")


def print_compare(trips: list, imperial: bool) -> None:
    console = Console()
    console.print()
    console.print(Panel.fit(
        f"[bold]{_trip_title(trips[0])}[/bold]\n"
        f"{len(trips)} route option(s)",
        title="Route comparison", border_style="cyan"))
    table = Table(pad_edge=False)
    table.add_column("Route")
    table.add_column("Distance", justify="right")
    table.add_column("Drive time", justify="right")
    table.add_column("Peak risk", justify="center")
    table.add_column("Avg", justify="right")
    table.add_column("Worst hazards")
    for t in trips:
        worst = max(t["points"], key=lambda p: p.score)
        label, color = hazards.level_for(t["peak_score"])
        table.add_row(
            t.get("route_name") or "route",
            units.fmt_distance(t["total_distance_m"], imperial),
            units.fmt_duration(t["adjusted_duration_s"]),
            f"[{color}]{t['peak_score']} {label}[/{color}]",
            f"{t['mean_score']:.1f}",
            "; ".join(worst.hazards) or "—",
        )
    console.print(table)
    best = min(trips, key=lambda t: (t["peak_score"], t["mean_score"],
                                     t["adjusted_duration_s"]))
    console.print(f"\n[bold]Recommendation:[/bold] "
                  f"{best.get('route_name') or 'the first route'} "
                  f"(lowest weather risk).\n")


def print_fleet(trips: list, imperial: bool) -> None:
    console = Console()
    console.print()
    console.print(Panel.fit(
        f"[bold]{len(trips)} active trip(s)[/bold] — sorted by risk",
        title="Fleet weather", border_style="cyan"))
    table = Table(pad_edge=False)
    table.add_column("Trip")
    table.add_column("Route")
    table.add_column("Departs")
    table.add_column("Peak risk", justify="center")
    table.add_column("Alerts", justify="right")
    table.add_column("Worst hazards")
    for t in sorted(trips, key=lambda t: -t["peak_score"]):
        worst = max(t["points"], key=lambda p: p.score)
        label, color = hazards.level_for(t["peak_score"])
        table.add_row(
            t.get("trip_name", ""),
            _trip_title(t).replace("  →  ", " → "),
            units.fmt_local_time(t["depart_utc"], t["origin_utc_offset_s"]),
            f"[{color}]{t['peak_score']} {label}[/{color}]",
            str(len(t["alerts"])) if t["alerts"] else "—",
            "; ".join(worst.hazards) or "—",
        )
    console.print(table)
    console.print()


def print_climate(stats: list, month: int, years: int, trip_info: dict,
                  imperial: bool) -> None:
    console = Console()
    month_name = dt.date(2000, month, 1).strftime("%B")
    console.print()
    console.print(Panel.fit(
        f"[bold]{_trip_title(trip_info)}[/bold]\n"
        f"Typical {month_name} conditions (last {years} years)",
        title="Route climatology", border_style="cyan"))
    table = Table(pad_edge=False)
    table.add_column("Mile" if imperial else "Km", justify="right")
    table.add_column("Avg hi/lo", justify="right")
    table.add_column("Records", justify="right")
    table.add_column("Wet days", justify="right")
    table.add_column("Snow days", justify="right")
    table.add_column("Windy days", justify="right")
    table.add_column("Max gust", justify="right")
    for s in stats:
        table.add_row(
            f"{s['distance_m'] / (1609.344 if imperial else 1000):.0f}",
            f"{units.fmt_temp(s['avg_high_c'], imperial)}/"
            f"{units.fmt_temp(s['avg_low_c'], imperial)}",
            f"{units.fmt_temp(s['record_high_c'], imperial)}/"
            f"{units.fmt_temp(s['record_low_c'], imperial)}",
            f"{s['precip_day_pct']:.0f}%",
            f"{s['snow_day_pct']:.0f}%",
            f"{s['high_wind_day_pct']:.0f}%",
            units.fmt_speed(s["max_gust_kmh"], imperial),
        )
    console.print(table)
    console.print()


# ---------------------------------------------------------------- JSON

def to_json(trip: dict) -> str:
    def _clean(o):
        if isinstance(o, dt.datetime):
            return o.isoformat()
        return o

    payload = {
        k: trip[k] for k in (
            "origin", "destination", "vehicle", "route_name",
            "total_distance_m", "total_duration_s", "adjusted_duration_s",
            "peak_score", "mean_score")
        if k in trip
    }
    payload["depart_utc"] = trip["depart_utc"].isoformat()
    payload["points"] = [
        {
            "lat": p.lat, "lon": p.lon,
            "eta_offset_s": p.eta_offset_s, "distance_m": p.distance_m,
            "road_bearing": p.road_bearing,
            "score": p.score, "hazards": p.hazards, "alerts": p.alerts,
            "weather": {k: _clean(v) for k, v in p.weather.items()},
        }
        for p in trip["points"]
    ]
    payload["nws_alerts"] = trip["alerts"]
    for extra in ("ev", "verdict"):
        if extra in trip:
            payload[extra] = trip[extra]
    return json.dumps(payload, indent=2, default=str)


# ---------------------------------------------------------------- HTML maps

def _point_popup(p, imperial: bool) -> str:
    w = p.weather
    label, color = hazards.level_for(p.score)
    return (
        f"<b>{units.fmt_distance(p.distance_m, imperial)} in · "
        f"{units.fmt_local_time(w['eta_utc'], w['utc_offset_s'])}</b><br>"
        f"{hazards.describe_code(w.get('weather_code'))}, "
        f"{units.fmt_temp(w.get('temperature_2m'), imperial)} "
        f"(feels {units.fmt_temp(w.get('apparent_temperature'), imperial)})<br>"
        f"Wind {units.fmt_speed(w.get('wind_speed_10m'), imperial)} "
        f"{units.compass(w.get('wind_direction_10m'))}, gusts "
        f"{units.fmt_speed(w.get('wind_gusts_10m'), imperial)}<br>"
        f"Precip {units.fmt_precip(w.get('precipitation'), imperial)} "
        f"({w.get('precipitation_probability') or 0:.0f}%), "
        f"visibility {units.fmt_visibility(w.get('visibility'), imperial)}<br>"
        f"<b style='color:{SCORE_COLORS[color]}'>Risk {p.score}/10 — {label}</b>"
        + ("<br>" + "; ".join(p.hazards) if p.hazards else "")
        + ("<br>⚠ " + "; ".join(sorted(set(p.alerts))) if p.alerts else "")
    )


def _route_layer(trip: dict, imperial: bool, line_color: str,
                 name: str = "") -> dict:
    markers = []
    for p in trip["points"]:
        _, color = hazards.level_for(p.score)
        markers.append({
            "lat": p.lat, "lon": p.lon,
            "color": SCORE_COLORS[color],
            "popup": _point_popup(p, imperial),
            "kind": "checkpoint",
        })
    for s in trip.get("ev", {}).get("charge_stops", []):
        markers.append({
            "lat": s["lat"], "lon": s["lon"], "color": "#2166ac",
            "popup": (f"⚡ <b>Suggested charging stop</b><br>"
                      f"~{units.fmt_distance(s['distance_m'], imperial)} in"),
            "kind": "charge",
        })
    return {
        "coords": [[lat, lon] for lat, lon in trip["route_coords"]],
        "color": line_color,
        "name": name or trip.get("route_name") or "route",
        "markers": markers,
    }


def _map_page(title: str, subtitle: str, layers: list, extra_html: str = "",
              show_route_legend: bool = False) -> str:
    layers_json = json.dumps(layers)
    route_legend = ""
    if show_route_legend:
        route_legend = "<p class='legend'>" + " ".join(
            f"<span><i class='dot' style='background:{l['color']}'></i>"
            f"{html_mod.escape(l['name'])}</span>" for l in layers) + "</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_mod.escape(title)}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; color: #1c2733; }}
  header {{ padding: 14px 20px; background: #10334f; color: #fff; }}
  header h1 {{ margin: 0; font-size: 1.2rem; }}
  header p {{ margin: 4px 0 0; opacity: .85; font-size: .9rem; }}
  #map {{ height: 62vh; }}
  main {{ padding: 16px 20px; max-width: 1100px; margin: 0 auto; }}
  .legend span {{ display: inline-block; margin-right: 14px; font-size: .85rem; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
         margin-right: 4px; vertical-align: middle; }}
  table {{ border-collapse: collapse; font-size: .9rem; }}
  td, th {{ padding: 4px 10px; border-bottom: 1px solid #dde3ea; text-align: left; }}
</style>
</head>
<body>
<header>
  <h1>{html_mod.escape(title)}</h1>
  <p>{html_mod.escape(subtitle)}</p>
</header>
<div id="map"></div>
<main>
  <p class="legend">
    <span><i class="dot" style="background:#1a9850"></i>Good (0–2)</span>
    <span><i class="dot" style="background:#e6b800"></i>Caution (3–4)</span>
    <span><i class="dot" style="background:#f46d43"></i>Hazardous (5–7)</span>
    <span><i class="dot" style="background:#d73027"></i>Severe (8–10)</span>
    <span><i class="dot" style="background:#2166ac"></i>⚡ charging stop</span>
    — click a checkpoint for the forecast at your arrival time
  </p>
  {route_legend}
  {extra_html}
</main>
<script>
  const layers = {layers_json};
  const map = L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors | radar &copy; RainViewer'
  }}).addTo(map);

  const bounds = [];
  layers.forEach(layer => {{
    const line = L.polyline(layer.coords, {{
      color: layer.color, weight: 4, opacity: .85
    }}).addTo(map).bindTooltip(layer.name);
    bounds.push(line.getBounds());
    layer.markers.forEach(m => {{
      L.circleMarker([m.lat, m.lon], {{
        radius: m.kind === 'charge' ? 11 : 9,
        color: '#fff', weight: 2, fillColor: m.color, fillOpacity: 1
      }}).addTo(map).bindPopup(m.popup);
    }});
  }});
  let all = bounds[0];
  bounds.slice(1).forEach(b => all = all.extend(b));
  map.fitBounds(all, {{padding: [30, 30]}});

  // Live radar overlay (RainViewer, current conditions) with a toggle.
  fetch('https://api.rainviewer.com/public/weather-maps.json')
    .then(r => r.json())
    .then(data => {{
      const frames = (data.radar && data.radar.past) || [];
      if (!frames.length) return;
      const latest = frames[frames.length - 1];
      const radar = L.tileLayer(
        data.host + latest.path + '/256/{{z}}/{{x}}/{{y}}/2/1_1.png',
        {{opacity: 0.6}});
      L.control.layers(null, {{'Live radar (now)': radar}},
                       {{collapsed: false}}).addTo(map);
    }})
    .catch(() => {{}});
</script>
</body>
</html>
"""


def to_html(trip: dict, imperial: bool) -> str:
    extra = ""
    if "verdict" in trip:
        v = trip["verdict"]
        color = SCORE_COLORS[{"GO": "green", "CAUTION": "yellow",
                              "NO-GO": "red"}[v["verdict"]]]
        items = "".join(f"<li>✗ {html_mod.escape(r)}</li>" for r in v["violations"])
        items += "".join(f"<li>! {html_mod.escape(r)}</li>" for r in v["warnings"])
        extra += (f"<h2 style='color:{color}'>Rules verdict: {v['verdict']}</h2>"
                  + (f"<ul>{items}</ul>" if items else ""))
    if trip["alerts"]:
        rows = "".join(
            f"<li><b>{html_mod.escape(a['event'])}</b> ({a['severity']}) — "
            f"{html_mod.escape(a['headline'])}</li>"
            for a in trip["alerts"])
        extra += f"<h2>⚠ Active NWS alerts on route</h2><ul>{rows}</ul>"
    layer = _route_layer(trip, imperial, "#456")
    return _map_page(f"Route Weather: {_trip_title(trip)}",
                     _trip_subtitle(trip, imperial), [layer], extra)


ROUTE_PALETTE = ["#2b6cb0", "#6b46c1", "#0f766e", "#b7791f", "#9b2c2c"]


def compare_html(trips: list, imperial: bool) -> str:
    layers = []
    rows = []
    for i, t in enumerate(trips):
        color = ROUTE_PALETTE[i % len(ROUTE_PALETTE)]
        name = t.get("route_name") or f"Route {i + 1}"
        layers.append(_route_layer(t, imperial, color, name))
        label, _ = hazards.level_for(t["peak_score"])
        worst = max(t["points"], key=lambda p: p.score)
        rows.append(
            f"<tr><td><i class='dot' style='background:{color}'></i>"
            f"{html_mod.escape(name)}</td>"
            f"<td>{units.fmt_distance(t['total_distance_m'], imperial)}</td>"
            f"<td>{units.fmt_duration(t['adjusted_duration_s'])}</td>"
            f"<td>{t['peak_score']} {label}</td>"
            f"<td>{html_mod.escape('; '.join(worst.hazards) or '—')}</td></tr>")
    extra = ("<h2>Options</h2><table><tr><th>Route</th><th>Distance</th>"
             "<th>Drive time</th><th>Peak risk</th><th>Worst hazards</th></tr>"
             + "".join(rows) + "</table>")
    return _map_page(f"Route comparison: {_trip_title(trips[0])}",
                     f"{len(trips)} options scored for weather risk",
                     layers, extra, show_route_legend=True)


def fleet_html(trips: list, imperial: bool) -> str:
    layers = []
    rows = []
    for t in sorted(trips, key=lambda t: -t["peak_score"]):
        label, color_key = hazards.level_for(t["peak_score"])
        color = SCORE_COLORS[color_key]
        name = t.get("trip_name") or _trip_title(t)
        layers.append(_route_layer(t, imperial, color, name))
        worst = max(t["points"], key=lambda p: p.score)
        rows.append(
            f"<tr><td><i class='dot' style='background:{color}'></i>"
            f"{html_mod.escape(name)}</td>"
            f"<td>{html_mod.escape(_trip_title(t).replace('  →  ', ' → '))}</td>"
            f"<td>{units.fmt_local_time(t['depart_utc'], t['origin_utc_offset_s'])}</td>"
            f"<td>{t['peak_score']} {label}</td>"
            f"<td>{len(t['alerts']) or '—'}</td>"
            f"<td>{html_mod.escape('; '.join(worst.hazards) or '—')}</td></tr>")
    extra = ("<h2>Trips by risk</h2><table><tr><th>Trip</th><th>Route</th>"
             "<th>Departs</th><th>Peak risk</th><th>Alerts</th>"
             "<th>Worst hazards</th></tr>" + "".join(rows) + "</table>")
    return _map_page("Fleet weather dashboard",
                     f"{len(trips)} active trips — routes colored by peak risk",
                     layers, extra)
