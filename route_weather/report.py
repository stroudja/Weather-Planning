"""Report rendering: rich console table, JSON export, and Leaflet HTML map."""

import datetime as dt
import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import hazards, units

SCORE_COLORS = {"green": "#1a9850", "yellow": "#e6b800", "orange": "#f46d43", "red": "#d73027"}


def print_console(trip: dict, imperial: bool) -> None:
    console = Console()
    points = trip["points"]

    console.print()
    console.print(Panel.fit(
        f"[bold]{trip['origin']['name'].split(',')[0]}  →  "
        f"{trip['destination']['name'].split(',')[0]}[/bold]\n"
        f"{units.fmt_distance(trip['total_distance_m'], imperial)} · "
        f"{units.fmt_duration(trip['total_duration_s'])} driving · "
        f"departing {units.fmt_local_time(trip['depart_utc'], trip['origin_utc_offset_s'])}",
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

    # Hazard callouts
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

    # NWS alerts
    if trip["alerts"]:
        console.print("\n[bold]Active NWS alerts on route (current, not forecast):[/bold]")
        for a in trip["alerts"]:
            sev_color = {"Extreme": "red", "Severe": "red", "Moderate": "orange3"}.get(a["severity"], "yellow")
            console.print(f"  [{sev_color}]⚠ {a['event']} ({a['severity']})[/{sev_color}]")
            if a["headline"]:
                console.print(f"    {a['headline']}")

    worst = max(points, key=lambda p: p.score)
    label, color = hazards.level_for(worst.score)
    console.print(
        f"\n[bold]Trip risk:[/bold] [{color}]{label}[/{color}] "
        f"(peak {worst.score}/10 at {units.fmt_distance(worst.distance_m, imperial)} in)\n"
    )


def to_json(trip: dict) -> str:
    def _clean(o):
        if isinstance(o, dt.datetime):
            return o.isoformat()
        return o

    payload = {
        "origin": trip["origin"],
        "destination": trip["destination"],
        "depart_utc": trip["depart_utc"].isoformat(),
        "total_distance_m": trip["total_distance_m"],
        "total_duration_s": trip["total_duration_s"],
        "points": [
            {
                "lat": p.lat, "lon": p.lon,
                "eta_offset_s": p.eta_offset_s, "distance_m": p.distance_m,
                "score": p.score, "hazards": p.hazards, "alerts": p.alerts,
                "weather": {k: _clean(v) for k, v in p.weather.items()},
            }
            for p in trip["points"]
        ],
        "nws_alerts": trip["alerts"],
    }
    return json.dumps(payload, indent=2, default=str)


def to_html(trip: dict, imperial: bool) -> str:
    """Self-contained HTML report with a Leaflet map (tiles/JS from CDN)."""
    points = trip["points"]
    markers = []
    for p in points:
        w = p.weather
        label, color = hazards.level_for(p.score)
        popup = (
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
        markers.append({
            "lat": p.lat, "lon": p.lon,
            "color": SCORE_COLORS[color],
            "popup": popup,
        })

    alerts_html = ""
    if trip["alerts"]:
        rows = "".join(
            f"<li><b>{a['event']}</b> ({a['severity']}) — {a['headline']}</li>"
            for a in trip["alerts"]
        )
        alerts_html = f"<h2>⚠ Active NWS alerts on route</h2><ul>{rows}</ul>"

    route_coords = json.dumps([[lat, lon] for lat, lon in trip["route_coords"]])
    markers_json = json.dumps(markers)
    title = (f"{trip['origin']['name'].split(',')[0]} → "
             f"{trip['destination']['name'].split(',')[0]}")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Route Weather: {title}</title>
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
</style>
</head>
<body>
<header>
  <h1>Route Weather: {title}</h1>
  <p>{units.fmt_distance(trip['total_distance_m'], imperial)} ·
     {units.fmt_duration(trip['total_duration_s'])} driving ·
     departing {units.fmt_local_time(trip['depart_utc'], trip['origin_utc_offset_s'])}</p>
</header>
<div id="map"></div>
<main>
  <p class="legend">
    <span><i class="dot" style="background:#1a9850"></i>Good (0–2)</span>
    <span><i class="dot" style="background:#e6b800"></i>Caution (3–4)</span>
    <span><i class="dot" style="background:#f46d43"></i>Hazardous (5–7)</span>
    <span><i class="dot" style="background:#d73027"></i>Severe (8–10)</span>
    — click a checkpoint for the forecast at your arrival time
  </p>
  {alerts_html}
</main>
<script>
  const route = {route_coords};
  const markers = {markers_json};
  const map = L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors'
  }}).addTo(map);
  const line = L.polyline(route, {{color: '#456', weight: 4, opacity: .8}}).addTo(map);
  markers.forEach(m => {{
    L.circleMarker([m.lat, m.lon], {{
      radius: 9, color: '#fff', weight: 2, fillColor: m.color, fillOpacity: 1
    }}).addTo(map).bindPopup(m.popup);
  }});
  map.fitBounds(line.getBounds(), {{padding: [30, 30]}});
</script>
</body>
</html>
"""
