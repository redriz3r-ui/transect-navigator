"""Typed reviewed-QA results and aggregation using the unchanged v0.1 statistics.

No QGIS objects, assignment inference, completion policy or survey statuses.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import fsum

from .review import Visit
from ..qaqc.deviation import Deviation
from ..qaqc.events import DeviationEvent, EventOptions
from ..qaqc.statistics import (Coverage, QaSummary, StatisticsOptions,
                               SummaryObservation, coverage, summarize)

QA_SCHEMA = 'survey-visit-qa-1'


@dataclass(frozen=True)
class QaOptions:
    statistics: StatisticsOptions = StatisticsOptions()
    events: EventOptions = EventOptions()


@dataclass(frozen=True)
class QaSample:
    observation_id: str
    original_key: str
    source_order: int
    original_timestamp: str | None
    timestamp_utc: str | None
    qa_timestamp_utc: str | None
    accuracy_raw: str | None
    longitude: float
    latitude: float
    native_geometry_wkb_hex: str
    deviation: Deviation


@dataclass(frozen=True)
class QaEvent:
    event_uid: str
    visit_id: str
    result: DeviationEvent
    geometry_wkb_hex: str | None  # Stationary events remain NULL, never empty lines.


@dataclass(frozen=True)
class VisitQa:
    visit: Visit
    processing_state: str
    error: str
    samples: tuple[QaSample, ...] = ()
    summary: QaSummary | None = None
    progress_support: Coverage | None = None
    events: tuple[QaEvent, ...] = ()
    flags: tuple[str, ...] = ()
    native_crs_wkt: str = ''
    analysis_crs_wkt: str = ''
    coordinate_operation: str = ''


@dataclass(frozen=True)
class TransectQa:
    transect_id: str
    planned_length_m: float | None
    navigation_length_m: float | None
    visit_ids: tuple[str, ...]
    accepted_observation_count: int
    evaluated_observation_count: int
    summary: QaSummary | None
    progress_support: Coverage | None
    known_track_length_m: float
    unknown_track_visit_count: int
    source_ids: tuple[str, ...]
    recording_ids: tuple[str, ...]
    surveyor_ids: tuple[str | None, ...]
    event_uids: tuple[str, ...]
    errors: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class BatchQa:
    schema: str
    batch_id: str
    survey_id: str
    review_run_id: str
    review_revision: int
    review_fingerprint: str
    engine_fingerprint: str
    context_json: str
    options: QaOptions
    dependency_fingerprint: str
    created_utc: str
    plugin_version: str
    visits: tuple[VisitQa, ...]
    transects: tuple[TransectQa, ...]
    disposition_counts: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict:
        return asdict(self)


def summary_rows(visits: tuple[VisitQa, ...]) -> tuple[SummaryObservation, ...]:
    """Exact eligible samples; Visit_ID is a hard analytical group boundary."""
    return tuple(SummaryObservation(sample.observation_id, result.visit.visit_id,
            sample.deviation.absolute_m, sample.deviation.distance_along_m,
            sample.qa_timestamp_utc, sample.accuracy_raw)
        for result in visits if result.processing_state == 'SUCCESS' for sample in result.samples)


def progress_support(rows: tuple[SummaryObservation, ...], length: float,
                     options: StatisticsOptions) -> Coverage:
    """Reuse v0.1 interval union with only its corridor gate disabled.

    Zero offsets in this PRIVATE support-only view make every finite valid QA
    sample eligible for the corridor test; actual deviations are never changed.
    Time/station gaps and chronology are exactly the existing coverage rules.
    """
    return coverage(tuple(replace(row, deviation_m=0.0) for row in rows), length, options)


def aggregate_transect(transect_id: str, planned_length_m: float | None,
                       navigation_length_m: float | None, visits: tuple[VisitQa, ...],
                       options: StatisticsOptions, plan_error: str = '') -> TransectQa:
    """Union independent groups and summarize observations, never Visit means."""
    if any(v.visit.transect_id != transect_id for v in visits):
        raise ValueError('Neighboring transect cannot enter aggregation')
    rows = summary_rows(visits)
    lengths = [v.summary.recorded_length_m if v.summary else None for v in visits]
    known = fsum(length for length in lengths if length is not None)
    unknown = sum(length is None for length in lengths)
    errors = ((plan_error,) if plan_error else ()) + tuple(
        f'{v.visit.visit_id}: {v.error}' for v in visits if v.processing_state != 'SUCCESS')
    flags = set(flag for v in visits for flag in v.flags)
    if errors:
        flags.add('PARTIAL_OR_FAILED_PROCESSING')
    if unknown:
        flags.add('TRACK_LENGTH_INCOMPLETE')
    if not visits:
        flags.add('NO_ACCEPTED_VISITS')
    summary = (summarize(transect_id, planned_length_m, rows,
        recorded_length_m=None if unknown or not visits else known, options=options)
        if planned_length_m is not None else None)
    support = progress_support(rows, planned_length_m, options) if planned_length_m is not None else None
    return TransectQa(transect_id, planned_length_m, navigation_length_m,
        tuple(v.visit.visit_id for v in visits), sum(len(v.visit.observation_ids) for v in visits),
        len(rows), summary, support, known, unknown,
        tuple(sorted({v.visit.source_id for v in visits})),
        tuple(sorted({v.visit.recording_id for v in visits})),
        tuple(sorted({v.visit.surveyor_id for v in visits}, key=lambda value: (value is not None, value or ''))),
        tuple(e.event_uid for v in visits for e in v.events), errors, tuple(sorted(flags)))
