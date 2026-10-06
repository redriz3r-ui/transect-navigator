"""Planar grid bearings; neither true nor magnetic bearings."""
from math import atan2, degrees, isfinite
from ..geometry.planar import Point2D


def grid_bearing(start: Point2D, end: Point2D) -> float:
    """Degrees clockwise from positive Y (grid north), in [0, 360).

    Coordinates must share a Cartesian frame with X east and Y north.
    Coincident points have no direction and raise ValueError.
    """
    dx, dy = end.x - start.x, end.y - start.y
    if not (isfinite(dx) and isfinite(dy)):
        raise ValueError("Displacement exceeds numerical range")
    if dx == 0 and dy == 0:
        raise ValueError("Coincident points have no bearing")
    return degrees(atan2(dx, dy)) % 360.0
