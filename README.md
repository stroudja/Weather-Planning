# Route Weather 🌦️🚚

Check the weather along an **entire ground-transportation route** — not just at
the start and end. The tool figures out *when you'll actually be at each point*
along the drive and pulls the forecast for that place **at that time**, then
flags driving hazards and active severe-weather alerts.

Works anywhere in the world, uses only free APIs, and needs **no API keys**.

```
╭─────────────── Route Weather ────────────────╮
│ Denver  →  Kansas City                       │
│ 602 mi · 8h 41m driving · departing Wed 06:00│
╰──────────────────────────────────────────────╯
 Mile  ETA (local)  Conditions      Temp  Feels  Precip     Wind (gust)        Vis      Risk
    0  Wed 06:00    Clear sky       68°F  67°F   0.00" 0%   8 mph W (14 mph)   15.0 mi  0 Good
   38  Wed 06:30    Partly cloudy   66°F  65°F   0.00" 5%   10 mph NW (18 mph) 15.0 mi  0 Good
  ...
  512  Wed 13:30    Thunderstorm    84°F  91°F   0.31" 70%  22 mph S (44 mph)  4.1 mi   8 Severe
```

## What's included

- **Time-matched forecasts** — a 6 PM forecast for the town you reach at 6 PM,
  not the noon forecast for your whole trip
- **Full conditions** at every checkpoint: temperature, feels-like, dew point,
  humidity, precipitation amount + probability, rain vs. snow, snow depth,
  cloud cover, visibility, sustained wind, gusts, wind direction, UV index
- **Severe weather**: thunderstorms, hail, freezing rain, snow, fog — plus
  **active NWS watches/warnings** (US routes) intersecting the route
- **Driving hazard score (0–10)** per checkpoint: ice-risk detection
  (precipitation near freezing), high-profile-vehicle wind thresholds,
  low visibility, flooding-rate rain, extreme heat/cold
- **Interactive HTML map** (`--html`) — color-coded checkpoints on your route,
  click any point for its arrival-time forecast
- **JSON export** (`--json`) for feeding dispatch systems or other scripts
- Multi-stop trips (`--via`), future departures (`--depart`), metric/imperial

## Install & run

```bash
pip install -r requirements.txt

# Leave now
python -m route_weather "Denver, CO" "Kansas City, MO"

# Tomorrow 6 AM (origin local time), checkpoint every 20 min of driving,
# with a stop in Salina, plus map + JSON output
python -m route_weather "Denver, CO" "Kansas City, MO" \
    --via "Salina, KS" \
    --depart "2026-07-09 06:00" \
    --interval 20 \
    --html trip.html --json trip.json

# Relative departure and metric units
python -m route_weather "Munich" "Vienna" --depart "+3h" --units metric
```

| Option | Meaning |
|---|---|
| `--depart` | `now` (default), `+2h`, `+45m`, or `YYYY-MM-DD HH:MM` in origin local time |
| `--interval` | Minutes of driving between weather checkpoints (default 30) |
| `--via` | Add an intermediate stop; repeatable |
| `--units` | `imperial` (default) or `metric` |
| `--html PATH` | Write an interactive Leaflet map report |
| `--json PATH` | Write machine-readable trip data |
| `--no-alerts` | Skip NWS alert lookups |

## How it works

1. **Geocode** origin/stops/destination — OpenStreetMap Nominatim
2. **Route** the drive — OSRM public server (distance, duration, full geometry
   with per-segment travel times)
3. **Sample** checkpoints every N minutes of *driving time* along the geometry
4. **Forecast** each checkpoint for its ETA — Open-Meteo hourly forecast
   (up to 16 days out), fetched in one batched request
5. **Assess** each checkpoint with the hazard rules in
   `route_weather/hazards.py`
6. **Alerts** — active NWS watches/warnings for US checkpoints, deduplicated
7. **Report** — console table, optional HTML map, optional JSON

### Hazard scoring

| Score | Level | Examples |
|---|---|---|
| 0–2 | 🟢 Good | clear, light rain, light drizzle |
| 3–4 | 🟡 Caution | light snow, fog, gusty winds, reduced visibility |
| 5–7 | 🟠 Hazardous | heavy rain, moderate snow, thunderstorms, high gusts, ice risk |
| 8–10 | 🔴 Severe | freezing rain, heavy snow, hail, damaging winds, near-zero visibility |

Modifiers stack on the base condition: ice risk (precip at ≤34°F), gust
thresholds (25/37/50 mph), visibility bands, downpour rate, extreme temps.

### Data sources (all free, no keys)

| Service | Used for | Notes |
|---|---|---|
| [Open-Meteo](https://open-meteo.com/) | Hourly forecasts | Global, 16-day horizon |
| [OSRM demo server](http://project-osrm.org/) | Driving routes | Fair-use public instance |
| [Nominatim](https://nominatim.org/) | Geocoding | Rate-limited to 1 req/s |
| [NWS api.weather.gov](https://www.weather.gov/documentation/services-web-api) | Severe weather alerts | US only; skipped elsewhere |

**Limitations to know about:** ETAs assume typical traffic (OSRM has no live
traffic); NWS alerts are *currently active* ones, so for departures days out
they describe today, not your travel day; forecasts cap at 16 days; the public
OSRM/Nominatim servers are fair-use — swap in your own instances for heavy use.

## Ideas / where this could go next

Things that would make this genuinely powerful, roughly ordered by bang-for-buck:

**Smarter trip decisions**
- **Departure-time optimizer** — score the same route departing every hour
  across a window ("leave between Fri 6 AM and Sat noon") and recommend the
  safest/driest slot. The scoring machinery already exists; this is a loop.
- **Route comparison** — OSRM can return alternate routes; score each and
  recommend "I-70 vs I-80 through the storm" with a risk-per-route summary.
- **Weather-aware ETA adjustment** — slow the projected speed through heavy
  snow/rain segments (e.g., FHWA speed-reduction factors), which shifts every
  downstream ETA and can change what weather you hit later.
- **Go / no-go rule engine** — user-defined thresholds ("never route me
  through gusts > 45 mph with an empty trailer") producing a single verdict.

**Better data**
- Road-surface temperature and pavement frost modeling (Open-Meteo exposes
  soil temperature layers — a good proxy for bridge icing)
- NWS *forecast* products (watches vs. warnings timing) matched to your ETA,
  not just currently-active alerts; outside the US, MeteoAlarm for Europe
- Live radar overlay on the HTML map (RainViewer tiles are free)
- Traffic-aware routing (Google/HERE/TomTom APIs) for realistic ETAs
- Sunrise/sunset per checkpoint — flag night driving through hazards, and
  sun-glare warnings when driving into a low sun azimuth

**Different vehicles & modes**
- **Trucking mode**: high-profile crosswind risk by heading (wind *direction
  relative to the road bearing* matters more than speed), chain-law alerts for
  mountain passes, hours-of-service-aware rest-stop suggestions timed to let
  storms pass
- **Motorcycle/bicycle mode**: lower wind & rain thresholds, wind-chill at
  riding speed
- **EV mode**: temperature-adjusted range estimates and charging-stop planning
- Rail/transit variant: GTFS feeds instead of OSRM

**Delivery & experience**
- A small web UI (FastAPI + the existing HTML map) or mobile PWA
- Scheduled monitoring: re-check a saved trip every few hours and
  email/text/Slack when the forecast for your departure worsens
- Fleet dashboard: many active routes on one map, sorted by risk
- Voice/LLM summary: "You'll hit freezing rain west of Salina around 9 PM —
  leaving 3 hours earlier avoids it entirely"
- Historical mode: "what's this route usually like in January?" using
  Open-Meteo's climate archive

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest tests/
```

The test suite includes a fully mocked end-to-end pipeline run, so it passes
without network access.
