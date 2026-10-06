"""Conservative contiguous exceedance events; no smoothing or claims about true walking error."""
from dataclasses import dataclass
from math import hypot, isfinite
from typing import Sequence
from ..analysis.observations import parse_timestamp
from ..geometry.planar import Point2D
from .statistics import SummaryObservation, describe, _numeric


@dataclass(frozen=True)
class EventOptions:
    threshold_m: float = 5.0
    minimum_duration_s: float | None = 5.0
    minimum_station_span_m: float | None = None
    maximum_time_gap_s: float = 60.0
    maximum_spatial_gap_m: float = 50.0

    def __post_init__(self) -> None:
        for name,value in (('Deviation threshold',self.threshold_m),
                           ('Minimum duration',self.minimum_duration_s),
                           ('Minimum station span',self.minimum_station_span_m)):
            if value is None and name != 'Deviation threshold':
                continue
            if value is None or isinstance(value,bool) or not isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative (optional minima may be disabled)')
        for value in (self.maximum_time_gap_s,self.maximum_spatial_gap_m):
            if isinstance(value,bool) or not isfinite(value) or value <= 0:
                raise ValueError('Event gap limits must be finite and positive')


@dataclass(frozen=True)
class EventObservation:
    observation: SummaryObservation
    position: Point2D  # Same local metre frame used for QA.


@dataclass(frozen=True)
class DeviationEvent:
    event_id: str
    transect_id: str
    group: str
    source_ids: tuple[str,...]
    start_distance_along_m: float
    end_distance_along_m: float
    event_length_m: float  # Station span, not distance walked or cumulative backtracking.
    start_time: str
    end_time: str
    duration_s: float
    mean_deviation_m: float
    maximum_deviation_m: float
    mean_gnss_accuracy_raw: float | None
    gnss_valid_count: int
    observation_count: int


@dataclass(frozen=True)
class EventDetection:
    events: tuple[DeviationEvent,...]
    eligible_groups: tuple[str,...]
    excluded_groups: tuple[str,...]
    rejected_run_count: int
    temporal_gap_count: int
    spatial_gap_count: int


def detect_events(transect_id: str, samples: Sequence[EventObservation],
                  options: EventOptions = EventOptions()) -> EventDetection:
    """Strict exceedance; evaluate complete unique-time groups independently.

    A qualifying run has >=2 samples and satisfies every enabled minimum. No
    crossing-time interpolation, skip-over of bad samples or gap bridging occurs.
    """
    if not transect_id.strip():
        raise ValueError('Event detection requires a Transect ID')
    ids = [s.observation.source_id for s in samples]
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        raise ValueError('Event observations need unique, nonblank source IDs')
    groups: dict[str,list[EventObservation]] = {}
    for sample in samples:
        if not sample.observation.group:
            raise ValueError('Event observations require explicit nonblank groups')
        groups.setdefault(sample.observation.group,[]).append(sample)
    events, eligible, excluded = [], [], []
    rejected = temporal = spatial = 0
    for group,members in sorted(groups.items()):
        parsed = [(parse_timestamp(s.observation.timestamp)[0],s) for s in members]
        times = [t for t,_ in parsed]
        if None in times or len(set(times)) != len(times):
            excluded.append(group)
            continue
        eligible.append(group)
        ordered = sorted(parsed,key=lambda pair:pair[0])
        run = []

        def finish() -> None:
            nonlocal rejected
            if not run:
                return
            first_time,first = run[0]
            last_time,last = run[-1]
            duration = (last_time-first_time).total_seconds()
            stations = [float(s.observation.station_m) for _,s in run]
            span = max(stations)-min(stations)
            if (len(run) < 2 or options.minimum_duration_s is not None and duration < options.minimum_duration_s
                    or options.minimum_station_span_m is not None and span < options.minimum_station_span_m):
                rejected += 1
                return
            deviations = describe([s.observation.deviation_m for _,s in run])
            accuracy = describe([s.observation.accuracy_raw for _,s in run])
            events.append(DeviationEvent(f'{transect_id}_E{len(events)+1:03d}',transect_id,group,
                tuple(s.observation.source_id for _,s in run),stations[0],stations[-1],span,
                first_time.isoformat().replace('+00:00','Z'),last_time.isoformat().replace('+00:00','Z'),
                duration,deviations.mean,deviations.maximum,accuracy.mean,accuracy.count,len(run)))

        for time,sample in ordered:
            deviation,_ = _numeric(sample.observation.deviation_m)
            station,_ = _numeric(sample.observation.station_m)
            if deviation is None or station is None or deviation <= options.threshold_m:
                finish()
                run = []
                continue
            if run:
                previous_time,previous = run[-1]
                time_gap = (time-previous_time).total_seconds() > options.maximum_time_gap_s
                spatial_gap = (hypot(sample.position.x-previous.position.x,sample.position.y-previous.position.y)
                               > options.maximum_spatial_gap_m or
                               abs(station-float(previous.observation.station_m)) > options.maximum_spatial_gap_m)
                if time_gap or spatial_gap:
                    temporal += int(time_gap)
                    spatial += int(spatial_gap)
                    finish()
                    run = []
            run.append((time,sample))
        finish()  # A survey ending off-path still has an observed, right-censored run.
    return EventDetection(tuple(events),tuple(eligible),tuple(excluded),rejected,temporal,spatial)
