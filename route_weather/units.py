"""Display-unit conversion. Internals are metric; users pick metric/imperial."""

import datetime as dt


def fmt_temp(celsius, imperial: bool) -> str:
    if celsius is None:
        return "—"
    if imperial:
        return f"{celsius * 9 / 5 + 32:.0f}°F"
    return f"{celsius:.0f}°C"


def fmt_speed(kmh, imperial: bool) -> str:
    if kmh is None:
        return "—"
    if imperial:
        return f"{kmh / 1.609344:.0f} mph"
    return f"{kmh:.0f} km/h"


def fmt_precip(mm, imperial: bool) -> str:
    if mm is None:
        return "—"
    if imperial:
        return f'{mm / 25.4:.2f}"'
    return f"{mm:.1f} mm"


def fmt_distance(meters, imperial: bool) -> str:
    if meters is None:
        return "—"
    if imperial:
        return f"{meters / 1609.344:.0f} mi"
    return f"{meters / 1000:.0f} km"


def fmt_visibility(meters, imperial: bool) -> str:
    if meters is None:
        return "—"
    if imperial:
        return f"{meters / 1609.344:.1f} mi"
    return f"{meters / 1000:.1f} km"


def fmt_duration(seconds) -> str:
    seconds = int(seconds)
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_local_time(utc: dt.datetime, utc_offset_s: int) -> str:
    """Format a UTC datetime as local time at the checkpoint."""
    local = utc + dt.timedelta(seconds=utc_offset_s)
    tz = dt.timezone(dt.timedelta(seconds=utc_offset_s))
    return local.strftime("%a %H:%M") + " " + (tz.tzname(None) or "")


def compass(degrees) -> str:
    if degrees is None:
        return ""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((degrees + 22.5) % 360 // 45)]
