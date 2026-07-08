"""Driving hazard assessment from forecast values.

Scores each checkpoint 0-10 based on conditions that matter for ground
transportation: winter precip, ice risk, thunderstorms, fog/visibility,
high winds (especially relevant for high-profile vehicles), flooding rain,
and temperature extremes.
"""

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


def assess(weather: dict):
    """Return (score 0-10, [hazard strings]) for one checkpoint's forecast.

    Expects metric values as fetched by weather.fetch_weather.
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
    vis = weather.get("visibility")

    freezing_codes = {48, 56, 57, 66, 67}
    if (temp is not None and temp <= 1 and precip > 0
            and (code is None or int(code) not in freezing_codes)
            and snowfall == 0):
        score += 3
        hazards.append("Ice risk (precip near freezing)")

    if gusts >= 80:
        score += 4
        hazards.append(f"Damaging wind gusts ({gusts:.0f} km/h)")
    elif gusts >= 60:
        score += 3
        hazards.append(f"High wind gusts ({gusts:.0f} km/h)")
    elif gusts >= 40:
        score += 1
        hazards.append(f"Gusty winds ({gusts:.0f} km/h)")

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

    if temp is not None:
        if temp <= -18:
            score += 2
            hazards.append(f"Extreme cold ({temp:.0f} °C)")
        elif temp >= 38:
            score += 2
            hazards.append(f"Extreme heat ({temp:.0f} °C)")

    return min(int(round(score)), 10), hazards
