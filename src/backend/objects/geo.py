"""Geographic points and great-circle distance."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .base import DomainRuleError

EARTH_RADIUS_KM = 6371.0088  # mean radius (IUGG)


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        for name, value, limit in (
            ("latitude", self.latitude, 90.0),
            ("longitude", self.longitude, 180.0),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DomainRuleError(f"{name} must be a number")
            if not math.isfinite(value) or abs(value) > limit:
                raise DomainRuleError(f"{name} must be between -{limit:g} and {limit:g}")

    def distance_km(self, other: GeoPoint) -> float:
        return haversine_km(self, other)


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance in km. Numerically safe for identical and
    antipodal points (the inner value is clamped to [0, 1])."""
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    h = min(1.0, max(0.0, h))
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))
