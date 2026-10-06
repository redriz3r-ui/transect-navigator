"""Observation-weighted spatial statistics, separate GNSS summaries and support coverage."""
from dataclasses import dataclass, asdict
from math import fsum, hypot, isfinite, sqrt
from typing import Sequence
from ..analysis.observations import parse_timestamp
from ..geometry.planar import Point2D


@dataclass(frozen=True)
class SummaryObservation:
    source_id: str
    group: str
    deviation_m: float | None
    station_m: float | None
    timestamp: str | None = None
    accuracy_raw: str | None = None


@dataclass(frozen=True)
class StatisticsOptions:
    """User QA/QC thresholds, never scientific or regulatory compliance standards."""
    thresholds_m: tuple[float,float,float] = (3.0,5.0,10.0)
    coverage_max_station_gap_m: float = 25.0
    coverage_max_time_gap_s: float = 60.0

    def __post_init__(self) -> None:
        object.__setattr__(self,'thresholds_m',tuple(self.thresholds_m))
        if len(self.thresholds_m) != 3 or not all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and isfinite(x) and x >= 0
                for x in self.thresholds_m):
            raise ValueError('Provide three finite, nonnegative QA/QC thresholds')
        if not self.thresholds_m[0] <= self.thresholds_m[1] <= self.thresholds_m[2]:
            raise ValueError('QA/QC thresholds must satisfy A ≤ B ≤ C')
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and isfinite(x) and x > 0
                   for x in (self.coverage_max_station_gap_m,self.coverage_max_time_gap_s)):
            raise ValueError('Coverage gap limits must be finite and positive')


@dataclass(frozen=True)
class Descriptive:
    count: int
    missing_count: int
    invalid_count: int
    mean: float | None
    median: float | None
    rmse: float | None
    maximum: float | None
    p95: float | None


def _numeric(value: object) -> tuple[float | None,str]:
    if value is None or isinstance(value,str) and not value.strip():
        return None,'missing'
    try:
        number = float(value)
    except (ValueError,TypeError,OverflowError):
        return None,'invalid'
    if isinstance(value,bool) or not isfinite(number) or number < 0:
        return None,'invalid'
    return number,'valid'


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Linear/type-7 quantile: interpolate at zero-based index (n−1)*fraction."""
    if not isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Quantile fraction must be within [0,1]')
    if not values:
        return None
    if any(not isfinite(v) or v < 0 for v in values):
        raise ValueError('Quantile values must be finite and nonnegative')
    ordered = sorted(values)
    index = (len(ordered)-1)*fraction
    lower = int(index)
    weight = index-lower
    return ordered[lower] + weight*(ordered[min(lower+1,len(ordered)-1)]-ordered[lower])


def describe(values: Sequence[object]) -> Descriptive:
    """Exclude invalid/missing values explicitly; stable scaled RMS avoids squaring overflow."""
    parsed = [_numeric(v) for v in values]
    valid = [v for v,status in parsed if status == 'valid']
    missing = sum(status == 'missing' for _,status in parsed)
    invalid = sum(status == 'invalid' for _,status in parsed)
    if not valid:
        return Descriptive(0,missing,invalid,None,None,None,None,None)
    maximum = max(valid)
    mean = fsum(v/len(valid) for v in valid)
    rms = maximum*sqrt(fsum((v/maximum)**2/len(valid) for v in valid)) if maximum else 0.0
    return Descriptive(len(valid),missing,invalid,mean,percentile(valid,.5),rms,maximum,percentile(valid,.95))


def track_length(parts: Sequence[Sequence[Point2D]] | None) -> float | None:
    """Sum unchanged recorded vertex chords per part; never join separate parts."""
    if parts is None or not parts:
        return None
    if any(len(part) < 2 for part in parts):
        raise ValueError('Recorded track parts require at least two vertices')
    try:
        result = fsum(hypot(b.x-a.x,b.y-a.y) for part in parts for a,b in zip(part,part[1:]))
    except OverflowError as error:
        raise ValueError('Track length exceeds numerical range') from error
    if not isfinite(result):
        raise ValueError('Track length exceeds numerical range')
    return result


@dataclass(frozen=True)
class Coverage:
    supported_length_m: float | None
    supported_percent: float | None
    eligible_group_count: int
    excluded_group_count: int
    accepted_pair_count: int
    rejected_pair_count: int


def coverage(rows: Sequence[SummaryObservation], length_m: float, options: StatisticsOptions) -> Coverage:
    """Union of short, near-plan station intervals in complete unique-time groups.

    This is sampled progress support, not proof of walked path or protocol coverage.
    Unknown group chronology excludes the entire group, never joins around bad data.
    """
    groups: dict[str,list[SummaryObservation]] = {}
    for row in rows:
        groups.setdefault(row.group,[]).append(row)
    eligible = excluded = accepted = rejected = 0
    intervals = []
    for members in groups.values():
        parsed = [(parse_timestamp(row.timestamp)[0],row) for row in members]
        instants = [time for time,_ in parsed]
        if len(members) < 2 or None in instants or len(set(instants)) != len(instants):
            excluded += 1
            continue
        eligible += 1
        ordered = sorted(parsed,key=lambda pair:pair[0])
        for (ta,a),(tb,b) in zip(ordered,ordered[1:]):
            da,_ = _numeric(a.deviation_m)
            db,_ = _numeric(b.deviation_m)
            sa,_ = _numeric(a.station_m)
            sb,_ = _numeric(b.station_m)
            if (None in (da,db,sa,sb) or sa > length_m or sb > length_m
                    or max(da,db) > options.thresholds_m[2]
                    or (tb-ta).total_seconds() > options.coverage_max_time_gap_s
                    or abs(sb-sa) > options.coverage_max_station_gap_m):
                rejected += 1
                continue
            accepted += 1
            intervals.append((min(sa,sb),max(sa,sb)))
    if not eligible:
        return Coverage(None,None,0,excluded,0,0)
    union = []
    for start,end in sorted(intervals):
        if union and start <= union[-1][1]:
            union[-1] = (union[-1][0],max(end,union[-1][1]))
        else:
            union.append((start,end))
    distance = fsum(end-start for start,end in union)
    return Coverage(distance,100*distance/length_m,eligible,excluded,accepted,rejected)


@dataclass(frozen=True)
class QaSummary:
    transect_id: str
    planned_length_m: float
    recorded_length_m: float | None
    observation_count: int
    observed_deviation: Descriptive
    within_percent: tuple[float | None,float | None,float | None]
    reported_gnss: Descriptive
    coverage: Coverage
    options: StatisticsOptions

    def as_dict(self) -> dict:
        """Structured standard-Python result suitable for JSON, tables and future reports."""
        return asdict(self)


def summarize(transect_id: str, planned_length_m: float, rows: Sequence[SummaryObservation],
              *, recorded_length_m: float | None = None,
              options: StatisticsOptions = StatisticsOptions()) -> QaSummary:
    """Each valid observation gets equal weight; no time/distance weighting or uncertainty subtraction."""
    if not isfinite(planned_length_m) or planned_length_m <= 0:
        raise ValueError('Planned length must be finite and positive')
    if recorded_length_m is not None and _numeric(recorded_length_m)[1] != 'valid':
        raise ValueError('Recorded length must be nonnegative finite metres or unknown')
    if len({r.source_id for r in rows}) != len(rows) or any(not r.source_id or not r.group for r in rows):
        raise ValueError('Observations need unique source IDs and nonblank groups')
    deviation = describe([r.deviation_m for r in rows])
    valid = [v for r in rows for v,status in [_numeric(r.deviation_m)] if status == 'valid']
    within = tuple(100*sum(v <= threshold for v in valid)/len(valid) if valid else None
                   for threshold in options.thresholds_m)
    return QaSummary(transect_id,planned_length_m,recorded_length_m,len(rows),deviation,within,
                     describe([r.accuracy_raw for r in rows]),coverage(rows,planned_length_m,options),options)
