"""Directed nearest-point analysis in an established local Cartesian metre frame.

Positive signed distance is LEFT of traversal, negative is RIGHT. No QGIS imports,
CRS guessing, data smoothing, temporal ordering or GNSS uncertainty corrections.
"""
from dataclasses import dataclass
from math import fsum, hypot
from typing import Sequence
from ..geometry.planar import Point2D, project_to_segment

TOLERANCE_M = 1e-7
MAX_SEGMENTS = 2000
MAX_PROJECTIONS = 2_000_000


@dataclass(frozen=True)
class Deviation:
    nearest: Point2D
    absolute_m: float
    signed_m: float | None
    distance_along_m: float
    total_length_m: float
    progress_pct: float
    sign_status: str


def _cross(a: Point2D, b: Point2D, p: Point2D) -> float:
    """Signed perpendicular distance to the directed infinite supporting line."""
    length = hypot(b.x-a.x, b.y-a.y)
    return ((b.x-a.x)/length)*(p.y-a.y) - ((b.y-a.y)/length)*(p.x-a.x)


def _intersects(a: Point2D, b: Point2D, c: Point2D, d: Point2D) -> bool:
    """Reject crossings and near-touching nonadjacent segments, including overlaps."""
    e = TOLERANCE_M
    if (max(a.x,b.x)+e < min(c.x,d.x) or max(c.x,d.x)+e < min(a.x,b.x)
            or max(a.y,b.y)+e < min(c.y,d.y) or max(c.y,d.y)+e < min(a.y,b.y)):
        return False
    if min(project_to_segment(p, u, v).distance for p,u,v in
           ((a,c,d),(b,c,d),(c,a,b),(d,a,b))) <= e:
        return True
    return _cross(a,b,c)*_cross(a,b,d) < 0 and _cross(c,d,a)*_cross(c,d,b) < 0


class DirectedTransect:
    """Validated simple open polyline; station is distance from the first vertex.

    Closed, crossing, touching, overlapping, duplicate-vertex and backtracking paths
    are rejected. Equal-distance matches at distinct stations fail per observation.
    All distances, including station and length, use this same planar metre model.
    """

    def __init__(self, vertices: Sequence[Point2D]) -> None:
        self.vertices = tuple(vertices)
        if not 2 <= len(self.vertices) <= MAX_SEGMENTS+1:
            raise ValueError(f'Planned transect needs 2–{MAX_SEGMENTS+1} working vertices')
        if any(abs(p.x) > 1_000_000 or abs(p.y) > 1_000_000 for p in vertices):
            raise ValueError('Use a local metre frame; working coordinates exceed numerical scope')
        self.segments = tuple(zip(self.vertices,self.vertices[1:]))
        lengths = tuple(hypot(b.x-a.x,b.y-a.y) for a,b in self.segments)
        if any(length <= TOLERANCE_M for length in lengths):
            raise ValueError('Planned transect contains a duplicate or numerically zero-length segment')
        for a,b,c in zip(self.vertices,self.vertices[1:],self.vertices[2:]):
            if abs(_cross(a,b,c)) <= TOLERANCE_M and (b.x-a.x)*(c.x-b.x)+(b.y-a.y)*(c.y-b.y) < 0:
                raise ValueError('Planned transect backtracks or overlaps at a vertex')
        for i,(a,b) in enumerate(self.segments):
            for c,d in self.segments[i+2:]:
                if _intersects(a,b,c,d):
                    raise ValueError('Planned transect crosses, touches, closes or overlaps itself')
        self.lengths = lengths
        self.starts = tuple(fsum(lengths[:i]) for i in range(len(lengths)))
        self.total_length_m = fsum(lengths)

    def compare(self, point: Point2D) -> Deviation:
        """Nearest closed-segment projection, preserving defensible sign only.

        Endpoint-clamped distances include along-track overshoot and have NULL sign.
        At an interior vertex, sign is assigned only if both incident directed
        segments agree strictly on side. A point on the path has signed zero.
        """
        projections = [project_to_segment(point,a,b) for a,b in self.segments]
        minimum = min(p.distance for p in projections)
        candidates = []
        for i, projection in enumerate(projections):
            if projection.distance-minimum > TOLERANCE_M:
                continue
            # A clamped vertex is not a local minimum if moving onto an incident
            # segment reduces distance. Near a subdivision vertex its distance may
            # round to the same value as the true interior projection: distance-only
            # tolerance then falsely reports two stations on a straight transect.
            if projection.fraction == 0 and i > 0 and projections[i-1].fraction < 1:
                continue
            if projection.fraction == 1 and i+1 < len(projections) and projections[i+1].fraction > 0:
                continue
            candidates.append(i)
        stations = [self.starts[i]+projections[i].fraction*self.lengths[i] for i in candidates]
        if max(stations)-min(stations) > TOLERANCE_M:
            raise ValueError('Observation has ambiguous nearest locations at different transect stations')
        i = min(candidates, key=lambda j: projections[j].distance)
        projection = projections[i]
        station = self.starts[i]+projection.fraction*self.lengths[i]
        distance = projection.distance
        if distance <= TOLERANCE_M:
            signed, status = 0.0, 'on_transect'
        elif station <= TOLERANCE_M or self.total_length_m-station <= TOLERANCE_M:
            signed, status = None, 'endpoint_no_lateral_sign'
        else:
            incident = [i]
            if projection.fraction*self.lengths[i] <= TOLERANCE_M and i > 0:
                incident.append(i-1)
            if (1-projection.fraction)*self.lengths[i] <= TOLERANCE_M and i+1 < len(self.segments):
                incident.append(i+1)
            crosses = [_cross(*self.segments[j],point) for j in incident]
            if all(c > TOLERANCE_M for c in crosses):
                signed, status = distance, 'left'
            elif all(c < -TOLERANCE_M for c in crosses):
                signed, status = -distance, 'right'
            else:
                signed, status = None, 'vertex_side_ambiguous'
        return Deviation(projection.point,distance,signed,station,self.total_length_m,
                         100*station/self.total_length_m,status)

    def compare_many(self, points: Sequence[Point2D]) -> tuple[Deviation, ...]:
        """Bound synchronous work and annotate errors without returning a partial batch."""
        if not points:
            raise ValueError('No observations to compare')
        if len(points)*len(self.segments) > MAX_PROJECTIONS:
            raise ValueError('Analysis exceeds 2,000,000 point/segment comparisons; split the survey')
        rows = []
        for index,point in enumerate(points,1):
            try:
                rows.append(self.compare(point))
            except ValueError as error:
                raise ValueError(f'Observation {index}: {error}') from error
        return tuple(rows)
