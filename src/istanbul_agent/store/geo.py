"""Great-circle distance, shared by station matching and travel-time estimates.

Station matching uses it to confirm that two similarly-named records are the
same place (decision 0003), and `get_rail_travel_time` uses it to estimate a
duration for stations the GTFS feed does not contain.

Distances here are straight-line, never along the track: a metro between two
stations covers more ground than the chord between them. Anything deriving a
travel time from this has to apply a route factor and report
``estimated: true``.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

__all__ = ["EARTH_RADIUS_M", "haversine_m", "parse_coordinate"]

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in metres."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def parse_coordinate(value: object) -> float | None:
    """Read a latitude or longitude that may arrive as a string.

    The Metro API returns coordinates as strings inside ``DetailInfo``, and
    some rows carry an empty string or a zero placeholder. Returns ``None``
    for anything that is not a usable coordinate, so callers can skip the
    record instead of computing a distance from the Gulf of Guinea.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number == 0.0 or not -180.0 <= number <= 180.0:
        return None
    return number
