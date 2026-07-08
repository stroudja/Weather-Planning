# Route Weather 🌦️🚚

Weather intelligence for **ground transportation routes**. The tool figures
out *when you'll actually be at each point* along a drive and pulls the
forecast for that place **at that time**, flags driving hazards, adjusts your
ETA for the weather itself, and can compare routes, pick your best departure
time, watch a trip for changes, and put a whole fleet on one dashboard.

Works anywhere in the world, uses only free APIs, and needs **no API keys**.

```
╭─────────────── Route Weather ────────────────╮
│ Denver  →  Kansas City                       │
│ 602 mi via I 70 · 8h 41m nominal, 9h 24m in  │
│ this weather · truck · departing Wed 06:00   │
╰──────────────────────────────────────────────╯
 Mile  ETA (local)  Conditions      Temp  Feels  Precip     Wind (gust)        Vis      Risk
    0  Wed 06:00    Clear sky       68°F  67°F   0.00" 0%   8 mph W (14 mph)   15.0 mi  0 Good
  ...
  512  Wed 13:52    Thunderstorm    84°F  91°F   0.31" 70%  22 mph S (44 mph)  4.1 mi   8 Severe
```

## Commands

| Command | What it does |
|---|---|
| `check` (default) | Weather along one route, time-matched to your ETAs |
| `optimize` | Score every departure slot in a window; recommend the safest |
| `compare` | Score alternate routes between the same two places |
| `fleet` | One risk-sorted dashboard for many trips (from a JSON file) |
| `watch` | Re-check a trip, diff against last check; exit 2 if worsened (cron-friendly) |
| `climate` | What this route is *usually* like in a given month (10-yr history) |

## Install & run

```bash
pip install -r requirements.txt

# Leave now (implies `check`)
python -m route_weather "Denver, CO" "Kansas City, MO"

# Tomorrow 6 AM, trucking profile, go/no-go rules, map + JSON out
python -m route_weather check "Denver, CO" "Kansas City, MO" \
    --via "Salina, KS" --depart "2026-07-09 06:00" --vehicle truck \
    --rules examples/rules.json --html trip.html --json trip.json

# When should I leave? Score every 2 hours across the next day and a half
python -m route_weather optimize "Denver, CO" "Kansas City, MO" \
    --window "+0h..+36h" --step 2h

# I-70 or I-80? Compare alternate routes through the weather
python -m route_weather compare "Denver, CO" "Salt Lake City, UT" --html cmp.html

# Everything the dispatcher needs on one page
python -m route_weather fleet examples/fleet.json --html fleet.html

# Cron: alert me if my Friday trip's forecast deteriorates
# 0 */3 * * * route-weather watch "Denver, CO" "Vail, CO" --depart "2026-07-10 07:00" || notify-send "Trip forecast worsened"
python -m route_weather watch "Denver, CO" "Vail, CO" --depart "+2h"

# What's this drive usually like in January?
python -m route_weather climate "Denver, CO" "Vail, CO" --month 1
```

### Common options

| Option | Meaning |
|---|---|
| `--depart` | `now` (default), `+2h`, `+45m`, or `YYYY-MM-DD HH:MM` in origin local time |
| `--interval` | Minutes of driving between weather checkpoints (default 30) |
| `--via` | Add an intermediate stop; repeatable |
| `--vehicle` | `car` (default), `truck`, `van`, `motorcycle`, `bicycle`, `ev` |
| `--ev-range` | Rated EV range (mi or km per `--units`); enables charging-stop planning |
| `--rules` | JSON file of hard limits → GO / CAUTION / NO-GO verdict (exit code 3 on NO-GO) |
| `--units` | `imperial` (default) or `metric` |
| `--html` / `--json` | Interactive Leaflet map report / machine-readable output |
| `--no-alerts` | Skip NWS alert lookups |

## What it checks

- **Full conditions** at every checkpoint: temperature, feels-like, dew point,
  humidity, precipitation amount + probability, rain vs. snow, snow depth,
  cloud cover, visibility, sustained wind, gusts, wind direction, UV index
- **Severe weather**: thunderstorms, hail, freezing rain, snow, fog — plus
  **active NWS watches/warnings** (US) intersecting the route
- **Driving hazard score (0–10)** per checkpoint with per-vehicle thresholds:
  - ice-risk detection (precipitation near freezing)
  - **crosswind component vs. your road heading** for high-profile vehicles —
    a 40 mph wind matters far more broadside than head-on
  - rain sensitivity for two-wheelers; wind-chill-relevant cold limits
  - **night driving** through hazards and **low-sun glare** in your direction
    of travel (sun position computed per checkpoint)
- **Weather-adjusted ETAs**: heavy snow/ice/fog slow the projected speed
  (FHWA-style factors), which shifts every downstream arrival time — and can
  change what weather you hit later, so forecasts are re-matched iteratively
- **EV range model** (`--vehicle ev`): temperature efficiency curve +
  headwind/tailwind penalty → effective range and suggested charging stops
- **Go/no-go rules**: your hard limits in JSON (`examples/rules.json`);
  violations produce a NO-GO verdict and exit code 3 for scripting

### The HTML maps

Every map report is a single self-contained page: the route line, color-coded
checkpoints (click for the arrival-time forecast), suggested EV charging
stops, and a **live radar overlay toggle** (RainViewer). `compare` draws all
route options; `fleet` draws every trip colored by its peak risk with a
risk-sorted table.

### Hazard scoring

| Score | Level | Examples |
|---|---|---|
| 0–2 | 🟢 Good | clear, light rain, light drizzle |
| 3–4 | 🟡 Caution | light snow, fog, gusty winds, reduced visibility |
| 5–7 | 🟠 Hazardous | heavy rain, moderate snow, thunderstorms, high gusts, ice risk, strong crosswinds |
| 8–10 | 🔴 Severe | freezing rain, heavy snow, hail, damaging winds, near-zero visibility |

### Data sources (all free, no keys)

| Service | Used for | Notes |
|---|---|---|
| [Open-Meteo](https://open-meteo.com/) | Hourly forecasts | Global, 16-day horizon, one batched request per trip |
| [Open-Meteo archive](https://open-meteo.com/en/docs/historical-weather-api) | `climate` history | ERA5 reanalysis |
| [OSRM demo server](http://project-osrm.org/) | Driving routes + alternates | Fair-use public instance |
| [Nominatim](https://nominatim.org/) | Geocoding | Rate-limited to 1 req/s |
| [NWS api.weather.gov](https://www.weather.gov/documentation/services-web-api) | Severe weather alerts | US only; skipped elsewhere |
| [RainViewer](https://www.rainviewer.com/api.html) | Radar tiles on maps | Loaded client-side in the browser |

**Limitations to know about:** ETAs assume typical traffic (OSRM has no live
traffic) before weather adjustment; NWS alerts are *currently active* ones, so
for departures days out they describe today, not your travel day; forecasts
cap at 16 days; the radar overlay shows conditions *now*, which is only
meaningful for imminent departures; the public OSRM/Nominatim servers are
fair-use — swap in your own instances for heavy production use.

## Ideas / where this could go next

- NWS *forecast* products (watch/warning timing) matched to ETAs, and
  MeteoAlarm for Europe
- Road-surface temperature via Open-Meteo soil layers (bridge-icing proxy)
- Traffic-aware routing (HERE/TomTom) feeding the same weather pipeline
- Chain-law / mountain-pass status feeds (state DOT APIs)
- Hours-of-service-aware rest stops timed so storms pass while you sleep
- Push notifications (email/SMS/Slack) on `watch` worsening — today you get
  the exit code for cron to act on
- A small FastAPI web UI wrapping these same modules; GTFS transit variant

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest tests/
```

The test suite mocks all HTTP, so it passes without network access — including
full CLI runs of every subcommand.
