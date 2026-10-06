"""Ordered stationing independent of QGIS, Qt, and the chosen distance model."""
from __future__ import annotations
from bisect import bisect_right
from dataclasses import dataclass
from math import floor, fsum, isfinite
from typing import Protocol, Sequence

Coordinate = tuple[float, float]
MAX_WAYPOINTS = 100_000


class DistanceModel(Protocol):
    """Coordinate-aware metric; lengths are metres and bearings are degrees."""
    def distance(self, start: Coordinate, end: Coordinate) -> float: ...
    def interpolate(self, start: Coordinate, end: Coordinate, distance_m: float) -> Coordinate: ...
    def bearing(self, start: Coordinate, end: Coordinate) -> float: ...


@dataclass(frozen=True)
class Transect:
    """One directed, single-part path with a unique nonblank textual identifier."""
    transect_id: str
    vertices: tuple[Coordinate, ...]


@dataclass(frozen=True)
class Waypoint:
    """A station in the supplied model's coordinates, with nullable last bearing."""
    transect_id: str
    waypoint_id: str
    sequence: int
    distance_along_m: float
    total_length_m: float
    coordinate: Coordinate
    bearing_next_deg: float | None


def validate_spacing(spacing_m: float) -> None:
    """Reject zero, negative, boolean and nonfinite intervals."""
    if isinstance(spacing_m, bool) or not isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError('Waypoint spacing must be a finite number greater than zero metres')


def station_distances(length_m: float, spacing_m: float, limit: int = MAX_WAYPOINTS) -> tuple[float, ...]:
    """Include 0 and exact end once, including a short final interval.

    A station within 0.1 micrometre of the end is replaced by the exact end.
    This prevents transform rounding from creating almost-duplicate endpoints.
    Integer multiples avoid accumulated interval-addition errors.
    """
    validate_spacing(spacing_m)
    if not isfinite(length_m) or length_m <= 0:
        raise ValueError('Transect length must be finite and greater than zero')
    ratio = length_m / spacing_m
    if not isfinite(ratio) or ratio > limit:
        raise ValueError(f'Output exceeds the {limit:,} remaining waypoint limit; increase spacing')
    count = floor(ratio)
    tolerance = min(1e-7, spacing_m * 1e-6, length_m * 1e-6)
    interior_count = count
    if count and length_m - count * spacing_m <= tolerance:
        interior_count -= 1
    if interior_count + 2 > limit:
        raise ValueError(f'Output exceeds the {limit:,} remaining waypoint limit; increase spacing')
    return (0.0, *(i * spacing_m for i in range(1, interior_count + 1)), length_m)


def generate_waypoints(transects: Sequence[Transect], spacing_m: float,
                       model: DistanceModel, reverse: bool = False) -> list[Waypoint]:
    """Generate an atomic batch sorted by ID, preserving each path's vertex order.

    Reversal anchors spacing at the opposite endpoint. When length is not a
    multiple of spacing, reversed interior positions consequently differ.
    Zero-length legs are rejected; no silent geometry repair occurs.
    """
    validate_spacing(spacing_m)
    if not transects:
        raise ValueError('No input transects')
    identifiers = [t.transect_id for t in transects]
    if any(not isinstance(i, str) or not i.strip() for i in identifiers):
        raise ValueError('Every transect needs a nonblank ID')
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Transect IDs must be unique within the input batch')
    output: list[Waypoint] = []
    for transect in sorted(transects, key=lambda t: t.transect_id):
        try:
            vertices = tuple(reversed(transect.vertices)) if reverse else transect.vertices
            if len(vertices) < 2:
                raise ValueError('Empty geometry or fewer than two vertices')
            if any(len(p) != 2 or not all(isfinite(v) for v in p) for p in vertices):
                raise ValueError('Coordinates must be finite two-dimensional pairs')
            lengths = [model.distance(a, b) for a, b in zip(vertices, vertices[1:])]
            if any(not isfinite(d) or d <= 0 for d in lengths):
                raise ValueError('Zero-length or invalid leg; remove duplicate vertices explicitly')
            cumulative = [0.0]
            for distance in lengths:
                cumulative.append(cumulative[-1] + distance)
            total = fsum(lengths)
            cumulative[-1] = total
            stations = station_distances(total, spacing_m, MAX_WAYPOINTS - len(output))
            positions = []
            for distance in stations:
                if distance == 0:
                    point = vertices[0]
                elif distance == total:
                    point = vertices[-1]
                else:
                    leg = min(bisect_right(cumulative, distance) - 1, len(lengths) - 1)
                    point = model.interpolate(vertices[leg], vertices[leg + 1], distance - cumulative[leg])
                if not all(isfinite(v) for v in point):
                    raise ValueError('Interpolation returned a nonfinite coordinate')
                positions.append(point)
            for index, (distance, point) in enumerate(zip(stations, positions)):
                bearing = None
                if index + 1 < len(positions):
                    following = positions[index + 1]
                    # Loops may revisit the same place: bearing is undefined there.
                    if model.distance(point, following) > 1e-8:
                        bearing = model.bearing(point, following)
                        if not isfinite(bearing):
                            raise ValueError('Nonfinite forward bearing')
                        bearing %= 360.0
                sequence = index + 1
                output.append(Waypoint(transect.transect_id, f'{transect.transect_id}_{sequence:03d}',
                                       sequence, distance, total, point, bearing))
        except ValueError as error:
            raise ValueError(f'Transect {transect.transect_id!r}: {error}') from error
    return output
