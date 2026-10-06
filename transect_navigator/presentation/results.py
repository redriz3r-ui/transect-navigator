"""Shared map/profile classes and plain-text result summaries."""
from dataclasses import dataclass
from math import isfinite
from typing import Sequence
from ..qaqc.deviation import Deviation
from ..qaqc.events import EventDetection
from ..qaqc.statistics import QaSummary, StatisticsOptions

COLORS = ('#0072B2', '#56B4E9', '#E69F00', '#D55E00')
SHAPES = ('circle', 'square', 'triangle', 'diamond')
NOTICE = 'User-configurable QA/QC classes; not regulatory or scientific compliance standards.'


@dataclass(frozen=True)
class ClassRule:
    label: str
    expression: str
    color: str
    shape: str


def quoted_field(name: str) -> str:
    """Quote a QGIS expression field reference, including embedded quote characters."""
    return '"' + name.replace('"', '""') + '"'


def deviation_class(value: float | None, thresholds: tuple[float, float, float]) -> int | None:
    if value is None or not isfinite(value) or value < 0:
        return None
    for index, threshold in enumerate(thresholds):
        if value <= threshold:
            return index
    return 3


def class_rules(field: str, thresholds: tuple[float, float, float]) -> tuple[ClassRule, ...]:
    """Nonoverlapping inclusive-upper-bound rules, matching statistics exactly."""
    StatisticsOptions(thresholds)  # Reuse the established validation contract.
    column = quoted_field(field)
    rules = []
    for index, upper in enumerate(thresholds):
        lower = thresholds[index-1] if index else 0.0
        if index and upper == lower:
            continue  # An equal-threshold interval is empty, not a new class.
        expression = f'{column} {">" if index else ">="} {lower:.17g} AND {column} <= {upper:.17g}'
        label = f'{lower:.12g} < deviation ≤ {upper:.12g} m' if index else f'0 ≤ deviation ≤ {upper:.12g} m'
        rules.append(ClassRule(label, expression, COLORS[index], SHAPES[index]))
    rules.append(ClassRule(f'Deviation > {thresholds[2]:.12g} m',
                           f'{column} > {thresholds[2]:.17g}', COLORS[3], SHAPES[3]))
    return tuple(rules)


@dataclass(frozen=True)
class ProfilePoint:
    station_m: float
    deviation_m: float
    class_index: int


def profile_points(rows: Sequence[Deviation], thresholds: tuple[float, float, float],
                   signed: bool = False) -> tuple[ProfilePoint, ...]:
    """Show all defined measurements; never connect, smooth or replace NULL signs."""
    StatisticsOptions(thresholds)
    result = []
    for row in rows:
        value = row.signed_m if signed else row.absolute_m
        category = deviation_class(row.absolute_m, thresholds)
        if value is not None and isfinite(value) and isfinite(row.distance_along_m) and category is not None:
            result.append(ProfilePoint(row.distance_along_m, value, category))
    return tuple(result)


def profile_bounds(points: Sequence[ProfilePoint], planned_length_m: float) -> tuple[float, float, float]:
    """Return nondegenerate x maximum and y bounds, including zero as a reference."""
    xmax = max(1e-6, planned_length_m, *(p.station_m for p in points))
    low = min(0.0, *(p.deviation_m for p in points)) if points else 0.0
    high = max(0.0, *(p.deviation_m for p in points)) if points else 0.0
    margin = max((high-low)*0.08, 0.1)
    return xmax, low-margin if low < 0 else 0.0, high+margin


def number(value: float | None, unit: str = '') -> str:
    if value is None or not isfinite(value):
        return 'Unavailable'
    return f'{value:,.3f}{(" " + unit) if unit else ""}'


def summary_rows(summary: QaSummary, events: EventDetection,
                 waypoint_spacing_m: float) -> tuple[tuple[str, str], ...]:
    """Format the existing result without recalculating statistics or hiding missing data."""
    d, c, g = summary.observed_deviation, summary.coverage, summary.reported_gnss
    result = [
        ('Transect ID', summary.transect_id),
        ('Planned length (local QA model)', number(summary.planned_length_m, 'm')),
        ('Recorded track length (available parts)', number(summary.recorded_length_m, 'm')),
        ('GPS observations', str(summary.observation_count)),
        ('Valid deviation observations', str(d.count)),
        ('Mean absolute deviation', number(d.mean, 'm')),
        ('Median absolute deviation', number(d.median, 'm')),
        ('RMSE', number(d.rmse, 'm')),
        ('95th percentile absolute deviation', number(d.p95, 'm')),
        ('Maximum absolute deviation', number(d.maximum, 'm')),
    ]
    for label, threshold, percent in zip('ABC', summary.options.thresholds_m, summary.within_percent):
        result.append((f'Within threshold {label} (≤ {threshold:.12g} m)', number(percent, '%')))
    result.extend([
        ('Detected deviation events', f'{len(events.events)}; {len(events.excluded_groups)} groups unevaluated'),
        ('Coverage estimate — sampled progress support', number(c.supported_percent, '%')),
        ('Supported planned distance', number(c.supported_length_m, 'm')),
        ('Coverage groups', f'{c.eligible_group_count} eligible; {c.excluded_group_count} excluded'),
        ('Reported GNSS accuracy mean (source units)', number(g.mean)),
        ('GNSS values', f'{g.count} valid; {g.missing_count} missing; {g.invalid_count} invalid'),
        ('Result navigation waypoint spacing', number(waypoint_spacing_m, 'm')),
    ])
    return tuple(result)
