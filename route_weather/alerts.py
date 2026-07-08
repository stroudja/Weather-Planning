"""Active severe-weather alerts from the US National Weather Service.

Covers US territory only; points outside NWS coverage are silently skipped,
so international routes still work (they just won't have official alerts).
"""

import requests

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "route-weather/0.1 (weather-planning tool)"

# Rough bounding box for NWS coverage (CONUS + AK + HI + PR)
_US_LAT = (17.5, 71.5)
_US_LON = (-180.0, -64.5)

SEVERITY_ORDER = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}


def _in_us(lat: float, lon: float) -> bool:
    return _US_LAT[0] <= lat <= _US_LAT[1] and _US_LON[0] <= lon <= _US_LON[1]


def fetch_alerts(points: list) -> list:
    """Attach active NWS alerts to each RoutePoint; return deduped list.

    Each unique alert appears once in the returned list with the checkpoint
    indices it affects. Note: these are *currently active* alerts — for
    departures far in the future they indicate today's situation, not a
    forecast.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    seen = {}

    for i, point in enumerate(points):
        if not _in_us(point.lat, point.lon):
            continue
        try:
            resp = session.get(
                NWS_ALERTS_URL,
                params={"point": f"{point.lat:.4f},{point.lon:.4f}"},
                timeout=30,
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
        except requests.RequestException:
            continue  # alerts are best-effort; don't fail the whole report

        for feat in features:
            props = feat.get("properties", {})
            alert_id = feat.get("id") or props.get("id")
            if alert_id not in seen:
                seen[alert_id] = {
                    "id": alert_id,
                    "event": props.get("event", "Unknown alert"),
                    "severity": props.get("severity", "Unknown"),
                    "headline": props.get("headline") or "",
                    "onset": props.get("onset"),
                    "ends": props.get("ends") or props.get("expires"),
                    "point_indices": [],
                }
            seen[alert_id]["point_indices"].append(i)
            point.alerts.append(seen[alert_id]["event"])

    alerts = sorted(
        seen.values(),
        key=lambda a: SEVERITY_ORDER.get(a["severity"], 4),
    )
    return alerts
