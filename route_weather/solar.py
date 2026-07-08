"""Sun position (elevation/azimuth) — NOAA solar calculator, pure python.

Used to flag night driving and low-sun glare in the direction of travel.
Accuracy is a fraction of a degree, plenty for driving heuristics.
"""

import datetime as dt
import math


def sun_position(when_utc: dt.datetime, lat: float, lon: float):
    """Return (elevation_deg, azimuth_deg) of the sun. Azimuth: 0=N, 90=E."""
    ts = when_utc.timestamp()
    # Julian day / century
    jd = ts / 86400.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0

    geom_mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)

    m_rad = math.radians(geom_mean_anom)
    eq_ctr = (math.sin(m_rad) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
              + math.sin(2 * m_rad) * (0.019993 - 0.000101 * jc)
              + math.sin(3 * m_rad) * 0.000289)
    true_long = geom_mean_long + eq_ctr
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    mean_obliq = (23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60)
    obliq_corr = mean_obliq + 0.00256 * math.cos(math.radians(omega))

    decl = math.degrees(math.asin(
        math.sin(math.radians(obliq_corr)) * math.sin(math.radians(app_long))))

    var_y = math.tan(math.radians(obliq_corr / 2)) ** 2
    l_rad = math.radians(geom_mean_long)
    eq_time = 4 * math.degrees(
        var_y * math.sin(2 * l_rad)
        - 2 * ecc * math.sin(m_rad)
        + 4 * ecc * var_y * math.sin(m_rad) * math.cos(2 * l_rad)
        - 0.5 * var_y ** 2 * math.sin(4 * l_rad)
        - 1.25 * ecc ** 2 * math.sin(2 * m_rad))

    minutes_utc = (ts % 86400) / 60.0
    true_solar_min = (minutes_utc + eq_time + 4 * lon) % 1440
    hour_angle = true_solar_min / 4 - 180 if true_solar_min / 4 >= 0 else true_solar_min / 4 + 180

    lat_r, decl_r, ha_r = map(math.radians, (lat, decl, hour_angle))
    zenith = math.degrees(math.acos(
        max(-1, min(1, math.sin(lat_r) * math.sin(decl_r)
                    + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r)))))
    elevation = 90 - zenith

    denom = math.cos(lat_r) * math.sin(math.radians(zenith))
    if abs(denom) < 1e-9:
        azimuth = 0.0
    else:
        cos_az = (math.sin(lat_r) * math.cos(math.radians(zenith))
                  - math.sin(decl_r)) / denom
        az = math.degrees(math.acos(max(-1, min(1, cos_az))))
        azimuth = (az + 180) % 360 if hour_angle > 0 else (180 - az) % 360

    return elevation, azimuth


def is_night(when_utc: dt.datetime, lat: float, lon: float) -> bool:
    """True when the sun is below civil twilight (-6°)."""
    elevation, _ = sun_position(when_utc, lat, lon)
    return elevation < -6


def glare_risk(when_utc: dt.datetime, lat: float, lon: float, road_bearing) -> bool:
    """True when driving roughly into a low sun (within 25° of heading,
    sun between the horizon and 15° elevation)."""
    if road_bearing is None:
        return False
    elevation, azimuth = sun_position(when_utc, lat, lon)
    if not (0 < elevation < 15):
        return False
    diff = abs((azimuth - road_bearing + 180) % 360 - 180)
    return diff < 25
