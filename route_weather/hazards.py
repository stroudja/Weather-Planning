"""Driving hazard assessment from forecast values.

Scores each checkpoint 0-10 based on conditions that matter for ground
transportation: winter precip, ice risk, thunderstorms, fog/visibility,
wind (including the crosswind component relative to the road heading for
high-profile vehicles), flooding rain, temperature extremes, night driving
through bad weather, and low-sun glare in the direction of travel.
"""

import math

from . import solar
from .vehicles import PROFILES, VehicleProfile

# WMO weather interpretation codes -> (description, base hazard score)
WMO_CODES = {
    0: ("Clear sky", 0),
    1: ("Mainly clear", 0),
    2: ("Partly cloudy", 0),
    3: ("Overcast", 0),
    45: ("Fog", 4),
    48: ("Freezing rime fog", 6),
    51: ("Light drizzle", 1),
    53: ("Drizzle", 1),
    55: ("Dense drizzle", 2),
    56: ("Light freezing drizzle", 6),
    57: ("Dense freezing drizzle", 8),
    61: ("Light rain", 1),
    63: ("Moderate rain", 2),
    65: ("Heavy rain", 5),
    66: ("Light freezing rain", 7),
    67: ("Heavy freezing rain", 9),
    71: ("Light snow", 3),
    73: ("Moderate snow", 5),
    75: ("Heavy snow", 8),
    77: ("Snow grains", 3),
    80: ("Light rain showers", 1),
    81: ("Moderate rain showers", 2),
    82: ("Violent rain showers", 6),
    85: ("Light snow showers", 4),
    86: ("Heavy snow showers", 7),
    95: ("Thunderstorm", 7),
    96: ("Thunderstorm w/ light hail", 8),
    99: ("Thunderstorm w/ heavy hail", 9),
}

FREEZING_CODES = {48, 56, 57, 66, 67}

LEVELS = [
    (0, 2, "Good", "green"),
    (3, 4, "Caution", "yellow"),
    (5, 7, "Hazardous", "orange"),
    (8, 10, "Severe", "red"),
]


def describe_code(code) -> str:
    if code is None:
        return "Unknown"
    return WMO_CODES.get(int(code), ("Unknown", 0))[0]


def level_for(score: int):
    """Return (label, color) for a 0-10 score."""
    for lo, hi, label, color in LEVELS:
        if lo <= score <= hi:
            return label, color
    return "Severe", "red"


def crosswind_component(wind_kmh, wind_from_deg, road_bearing_deg):
    """Wind component (km/h) perpendicular to the direction of travel."""
    if wind_kmh is None or wind_from_deg is None or road_bearing_deg is None:
        return None
    return abs(wind_kmh * math.sin(math.radians(wind_from_deg - road_bearing_deg)))


def assess(weather: dict, profile: VehicleProfile = PROFILES["car"],
           road_bearing=None, lat=None, lon=None):
    """Return (score 0-10, [hazard strings]) for one checkpoint's forecast.

    Expects metric values as fetched by weather.fetch_weather. `road_bearing`
    (degrees, direction of travel) enables crosswind and sun-glare checks;
    lat/lon + weather["eta_utc"] enable night/glare checks.
    """
    hazards = []
    code = weather.get("weather_code")
    desc, score = WMO_CODES.get(int(code), ("Unknown", 0)) if code is not None else ("Unknown", 0)
    if score >= 3:
        hazards.append(desc)

    temp = weather.get("temperature_2m")
    precip = weather.get("precipitation") or 0
    snowfall = weather.get("snowfall") or 0
    gusts = weather.get("wind_gusts_10m") or 0
    wind = weather.get("wind_speed_10m") or 0
    wind_dir = weather.get("wind_direction_10m")
    vis = weather.get("visibility")

    if (temp is not None and temp <= 1 and precip > 0
            and (code is None or int(code) not in FREEZING_CODES)
            and snowfall == 0):
        score += 3
        hazards.append("Ice risk (precip near freezing)")

    if gusts >= profile.gust_damaging:
        score += 4
        hazards.append(f"Damaging wind gusts ({gusts:.0f} km/h)")
    elif gusts >= profile.gust_high:
        score += 3
        hazards.append(f"High wind gusts ({gusts:.0f} km/h)")
    elif gusts >= profile.gust_caution:
        score += 1
        hazards.append(f"Gusty winds ({gusts:.0f} km/h)")

    if profile.crosswind_limit is not None:
        cross = crosswind_component(gusts, wind_dir, road_bearing)
        if cross is not None and cross >= profile.crosswind_limit:
            severe_cross = cross >= profile.crosswind_limit * 1.4
            score += 3 if severe_cross else 2
            hazards.append(
                f"{'Severe c' if severe_cross else 'C'}rosswind for "
                f"{profile.label.lower()} ({cross:.0f} km/h across the road)")

    if vis is not None:
        if vis < 200:
            score += 5
            hazards.append("Near-zero visibility")
        elif vis < 1000:
            score += 3
            hazards.append(f"Low visibility ({vis / 1000:.1f} km)")
        elif vis < 5000:
            score += 1
            hazards.append(f"Reduced visibility ({vis / 1000:.1f} km)")

    if precip >= 10:
        score += 2
        hazards.append(f"Downpour / flooding risk ({precip:.1f} mm/h)")
    elif profile.rain_sensitive and precip >= 2:
        score += 2
        hazards.append(f"Rain on two wheels ({precip:.1f} mm/h)")

    if temp is not None:
        if temp <= profile.cold_limit_c:
            score += 2
            hazards.append(f"Extreme cold for {profile.label.lower()} ({temp:.0f} °C)")
        elif temp >= profile.heat_limit_c:
            score += 2
            hazards.append(f"Extreme heat ({temp:.0f} °C)")

    # Sun/night checks need a time and place
    eta = weather.get("eta_utc")
    if eta is not None and lat is not None and lon is not None:
        if solar.is_night(eta, lat, lon):
            if score >= 3:
                score += 1
                hazards.append("At night")
        elif solar.glare_risk(eta, lat, lon, road_bearing):
            score += 1
            hazards.append("Low-sun glare in direction of travel")

    return min(int(round(score)), 10), hazards


def speed_factor(weather: dict) -> float:
    """Fraction of normal driving speed sustainable in these conditions.

    Loosely based on FHWA weather speed-reduction research. Used to produce
    weather-adjusted ETAs. 1.0 = full speed.
    """
    factor = 1.0
    code = weather.get("weather_code")
    code = int(code) if code is not None else 0
    temp = weather.get("temperature_2m")
    precip = weather.get("precipitation") or 0
    vis = weather.get("visibility")
    gusts = weather.get("wind_gusts_10m") or 0

    if code in (67, 57):                 # heavy freezing rain/drizzle
        factor = min(factor, 0.45)
    elif code in FREEZING_CODES:         # other freezing precip
        factor = min(factor, 0.55)
    elif code in (75, 86):               # heavy snow
        factor = min(factor, 0.55)
    elif code in (73, 85):               # moderate snow
        factor = min(factor, 0.70)
    elif code in (71, 77):               # light snow
        factor = min(factor, 0.85)
    elif code in (65, 82, 95, 96, 99):   # heavy rain / t-storms
        factor = min(factor, 0.75)
    elif code in (63, 81):               # moderate rain
        factor = min(factor, 0.90)

    if temp is not None and temp <= 1 and precip > 0:
        factor = min(factor, 0.60)       # possible ice
    if vis is not None:
        if vis < 400:
            factor = min(factor, 0.60)
        elif vis < 1500:
            factor = min(factor, 0.75)
        elif vis < 5000:
            factor = min(factor, 0.90)
    if gusts >= 80:
        factor = min(factor, 0.85)

    return max(factor, 0.40)
