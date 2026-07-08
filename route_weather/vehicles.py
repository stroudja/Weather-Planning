"""Vehicle profiles — different vehicles care about different weather.

Thresholds are in metric (km/h, mm, cm) to match internal units.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleProfile:
    key: str
    label: str
    # Wind gust thresholds (km/h): caution / high / damaging
    gust_caution: float
    gust_high: float
    gust_damaging: float
    # Crosswind component (km/h) that adds hazard; None = not sensitive
    crosswind_limit: float | None
    # Extra score added when precipitation exceeds these rates (mm/h)
    rain_sensitive: bool      # two-wheelers: any real rain is a hazard
    cold_limit_c: float       # "extreme cold" threshold
    heat_limit_c: float       # "extreme heat" threshold
    is_ev: bool = False


PROFILES = {
    "car": VehicleProfile(
        key="car", label="Car",
        gust_caution=40, gust_high=60, gust_damaging=80,
        crosswind_limit=None, rain_sensitive=False,
        cold_limit_c=-18, heat_limit_c=38,
    ),
    "truck": VehicleProfile(
        key="truck", label="Truck / high-profile",
        gust_caution=30, gust_high=48, gust_damaging=65,
        crosswind_limit=40, rain_sensitive=False,
        cold_limit_c=-18, heat_limit_c=38,
    ),
    "van": VehicleProfile(
        key="van", label="Van / RV / trailer",
        gust_caution=32, gust_high=50, gust_damaging=70,
        crosswind_limit=45, rain_sensitive=False,
        cold_limit_c=-18, heat_limit_c=38,
    ),
    "motorcycle": VehicleProfile(
        key="motorcycle", label="Motorcycle",
        gust_caution=30, gust_high=45, gust_damaging=60,
        crosswind_limit=35, rain_sensitive=True,
        cold_limit_c=4, heat_limit_c=38,
    ),
    "bicycle": VehicleProfile(
        key="bicycle", label="Bicycle",
        gust_caution=25, gust_high=40, gust_damaging=55,
        crosswind_limit=30, rain_sensitive=True,
        cold_limit_c=0, heat_limit_c=35,
    ),
    "ev": VehicleProfile(
        key="ev", label="Electric vehicle",
        gust_caution=40, gust_high=60, gust_damaging=80,
        crosswind_limit=None, rain_sensitive=False,
        cold_limit_c=-18, heat_limit_c=38, is_ev=True,
    ),
}


def get_profile(key: str) -> VehicleProfile:
    return PROFILES[key]
