"""Unit-agnostic 2D Cartesian primitives, never geodesic calculations.

Callers must establish a common suitable projected CRS before supplying GIS
coordinates. Distances use input units; Z and M are deliberately not modeled.
"""
from dataclasses import dataclass
from math import hypot, isfinite


@dataclass(frozen=True)
class Point2D:
    """Immutable finite Cartesian coordinate pair."""
    x: float
    y: float

    def __post_init__(self) -> None:
        if not (isfinite(self.x) and isfinite(self.y)):
            raise ValueError("Coordinates must be finite")


@dataclass(frozen=True)
class SegmentProjection:
    """Closest point, fraction [0, 1], and unsigned Cartesian distance."""
    point: Point2D
    fraction: float
    distance: float


def project_to_segment(point: Point2D, start: Point2D, end: Point2D) -> SegmentProjection:
    """Project onto a closed segment; a zero-length segment is a point.

    Endpoints clamp the fraction. Reject numerical overflow instead of returning
    plausible-looking invalid distances. This is not a polyline QA algorithm.
    """
    dx, dy = end.x - start.x, end.y - start.y
    length = hypot(dx, dy)
    if not isfinite(length):
        raise ValueError("Segment exceeds numerical range")
    if length == 0:
        fraction = 0.0
        closest = start
    else:
        along = (point.x - start.x) * (dx / length) + (point.y - start.y) * (dy / length)
        if not isfinite(along):
            raise ValueError("Projection exceeds numerical range")
        fraction = min(1.0, max(0.0, along / length))
        closest = Point2D(start.x + fraction * dx, start.y + fraction * dy)
    distance = hypot(point.x - closest.x, point.y - closest.y)
    if not isfinite(distance):
        raise ValueError("Distance exceeds numerical range")
    return SegmentProjection(closest, fraction, distance)
