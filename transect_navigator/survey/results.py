"""Operational Survey results over immutable Stage 5 QA; no new QA mathematics."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from math import fsum, isfinite
from typing import Mapping, Sequence

from .model import Survey, required_text, utc_time
from .qa import BatchQa, TransectQa, VisitQa, progress_support, summary_rows
from .review import Disposition
from ..qaqc.statistics import StatisticsOptions

RESULTS_SCHEMA = 'survey-results-1'
STATUSES = ('COMPLETE', 'INCOMPLETE', 'REVIEW', 'NOT_SURVEYED')


@dataclass(frozen=True)
class CompletionPolicy:
    """Explicit selection; support fraction and endpoints, never deviation quality.

    Numerical tolerance is in model metres, not a permitted walking deviation.
    The caller must supply all settings and who selected them.
    """
    policy_id: str
    required_support_percent: float
    endpoint_tolerance_m: float
    numerical_tolerance_m: float
    selected_by: str
    reason: str
    selected_utc: str
    version: str = 'progress-and-endpoints-1'

    def __post_init__(self):
        for key in ('policy_id', 'selected_by', 'reason'):
            required_text(getattr(self, key), key)
        if self.version != 'progress-and-endpoints-1':
            raise ValueError('Unsupported completion policy version')
        for value in (self.required_support_percent, self.endpoint_tolerance_m, self.numerical_tolerance_m):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError('Completion parameters must be finite numbers')
        if not 0 < self.required_support_percent <= 100 or self.endpoint_tolerance_m < 0 or not 0 <= self.numerical_tolerance_m <= .1:
            raise ValueError('Invalid completion parameters (numerical tolerance must be 0–0.1 m)')
        object.__setattr__(self, 'selected_utc', utc_time(self.selected_utc))


@dataclass(frozen=True)
class EvidenceScope:
    """Append-only relevance review; does NOT assign or remove observations.

    Empty plan membership explicitly means unrelated to this survey's plans.
    It is allowed only for UNASSIGNED evidence, never for AMBIGUOUS evidence.
    Each revision supersedes preceding scopes for its exact observations.
    """
    revision: int
    review_fingerprint: str
    observation_ids: tuple[str, ...]
    transect_ids: tuple[str, ...]
    reviewer: str
    reason: str
    utc: str


def scope_evidence(history: tuple[EvidenceScope, ...], batch: BatchQa,
                   membership: Mapping[str, Disposition], observation_ids: Sequence[str],
                   transect_ids: Sequence[str], *, reviewer: str, reason: str,
                   utc: str | None = None) -> tuple[EvidenceScope, ...]:
    """Record a human relevance decision tied to this exact assignment revision."""
    record = EvidenceScope(len(history) + 1, batch.review_fingerprint,
        tuple(sorted(set(observation_ids))), tuple(sorted(set(transect_ids))),
        required_text(reviewer, 'Reviewer'), required_text(reason, 'Reason'), utc_time(utc))
    result = tuple(history) + (record,)
    unresolved_by_plan(batch, membership, result)  # Validate before accepting history.
    return result


def unresolved_by_plan(batch: BatchQa, membership: Mapping[str, Disposition],
                       history: tuple[EvidenceScope, ...]) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...], tuple[str, ...]]:
    """Unscoped uncertainty blocks every line; scoped uncertainty blocks named lines.

    No nearest-line inference. Survey unresolved totals always retain all evidence,
    including explicitly unrelated observations. Per-line counts are nonadditive.
    """
    plans = {t.transect_id for t in batch.transects}
    unresolved = {key for key, d in membership.items()
                  if d.assignment_status != 'ASSIGNED' and d.activity_role != 'TRANSITION'}
    effective = {}
    for revision, record in enumerate(history, 1):
        if record.revision != revision:
            raise ValueError('Invalid evidence-scope revision')
        required_text(record.review_fingerprint, 'Review fingerprint')
        required_text(record.reviewer, 'Reviewer')
        required_text(record.reason, 'Reason')
        utc_time(record.utc)
        # Preserve historical relevance decisions, but never migrate them across
        # assignment revisions. Changed review requires explicit new scoping.
        if record.review_fingerprint != batch.review_fingerprint:
            continue
        if (not record.observation_ids or len(set(record.observation_ids)) != len(record.observation_ids)
                or len(set(record.transect_ids)) != len(record.transect_ids)
                or not set(record.observation_ids) <= unresolved or not set(record.transect_ids) <= plans):
            raise ValueError('Evidence scope must reference exact unresolved observations and registered plans')
        if not record.transect_ids and any(membership[key].assignment_status == 'AMBIGUOUS' for key in record.observation_ids):
            raise ValueError('Ambiguous evidence cannot be dismissed as unrelated')
        for key in record.observation_ids:
            effective[key] = record.transect_ids
    unscoped = unresolved - set(effective)
    by_plan = {name: tuple(sorted(unscoped | {key for key, values in effective.items() if name in values}))
               for name in plans}
    return by_plan, tuple(sorted(unresolved)), tuple(sorted(unscoped))


@dataclass(frozen=True)
class TransectResult:
    survey_id: str
    transect_id: str
    suggested_status: str
    final_status: str
    status_basis: tuple[str, ...]
    reviewer: str
    completion_policy_id: str
    qa_state: str
    unresolved_observation_ids: tuple[str, ...]
    start_supported: bool
    end_supported: bool
    confirmed_progress_m: float
    confirmed_corridor_m: float
    qa: TransectQa


@dataclass(frozen=True)
class SurveySummary:
    planned_count: int
    modeled_count: int
    unknown_length_count: int
    status_counts: tuple[tuple[str, int], ...]
    total_planned_length_m: float | None
    known_planned_length_m: float
    accepted_track_length_m: float | None
    known_track_length_m: float
    confirmed_progress_m: float
    confirmed_corridor_m: float
    progress_lower_bound_percent: float | None
    corridor_lower_bound_percent: float | None
    unknown_support_count: int
    unresolved_observation_count: int
    unscoped_observation_count: int
    ambiguous_observation_count: int
    unassigned_observation_count: int
    source_count: int
    surveyor_count: int
    processing_state: str


@dataclass(frozen=True)
class SurveyResults:
    schema: str
    survey_id: str
    plan_revision: str
    dependency_fingerprint: str
    batch_id: str
    review_revision: int
    created_utc: str
    policy: CompletionPolicy
    evidence_history: tuple[EvidenceScope, ...]
    transects: tuple[TransectResult, ...]
    summary: SurveySummary

    def as_dict(self):
        return asdict(self)


def _endpoints(visits: tuple[VisitQa, ...], length: float, policy: CompletionPolicy,
               options: StatisticsOptions) -> tuple[bool, bool]:
    """Ask the existing support engine about endpoint pairs, never bridge Visits.

    Only pairs from wholly eligible chronology groups can support an endpoint.
    Isolated endpoint fixes and rejected gap pairs cannot satisfy this condition.
    """
    start = end = False
    tolerance = policy.endpoint_tolerance_m + policy.numerical_tolerance_m
    for visit in visits:
        if not visit.progress_support or not visit.progress_support.eligible_group_count:
            continue
        rows = summary_rows((visit,))
        for a, b in zip(rows, rows[1:]):
            near_start = min(a.station_m, b.station_m) <= tolerance
            near_end = max(a.station_m, b.station_m) >= length - tolerance
            if not (near_start or near_end):
                continue
            supported = progress_support((a, b), length, options)
            if supported.accepted_pair_count and supported.supported_length_m > 0:
                start |= near_start
                end |= near_end
    return start, end


def aggregate_results(survey: Survey, batch: BatchQa, membership: Mapping[str, Disposition], policy: CompletionPolicy,
                      evidence_history: tuple[EvidenceScope, ...] = (), *, dependency_fingerprint: str = '',
                      created_utc: str | None = None, source_count: int = 0, surveyor_count: int = 0) -> SurveyResults:
    """Aggregate one CURRENT batch; caller must validate external dependencies.

    Unknown measurements remain NULL in Stage 5 records. Confirmed lower bounds
    contribute zero where nothing has been established, with unknown counts shown.
    Unknown planned lengths make whole-survey percentages unavailable.
    """
    expected = {p.transect_id for p in survey.plans}
    if len(expected) != len(survey.plans) or None in expected or {t.transect_id for t in batch.transects} != expected or len(batch.transects) != len(expected):
        raise ValueError('Results require exactly one QA row per official planned Transect_ID')
    if survey.survey_id != batch.survey_id:
        raise ValueError('QA belongs to a different Survey')
    relevant, unresolved, unscoped = unresolved_by_plan(batch, membership, evidence_history)
    results = []
    for qa in sorted(batch.transects, key=lambda t: t.transect_id):
        length = qa.planned_length_m
        if length is not None and (not isfinite(length) or length <= 0):
            raise ValueError('Invalid modeled planned length')
        progress = qa.progress_support.supported_length_m if qa.progress_support else None
        corridor = qa.summary.coverage.supported_length_m if qa.summary else None
        for value in (progress, corridor):
            if value is not None and (length is None or not isfinite(value) or value < 0 or value > length + 1e-8):
                raise ValueError('Support exceeds modeled planned length or is invalid')
        visits = tuple(v for v in batch.visits if v.visit.transect_id == qa.transect_id)
        ends = _endpoints(visits, length, policy, batch.options.statistics) if length is not None else (False, False)
        reasons = []
        if length is None or qa.errors:
            reasons.append('UNSUPPORTED_OR_FAILED_PROCESSING')
        if relevant[qa.transect_id]:
            reasons.append('UNRESOLVED_RELEVANT_EVIDENCE')
        if qa.visit_ids and (progress is None or qa.unknown_track_visit_count or
                            (qa.progress_support and qa.progress_support.excluded_group_count)):
            reasons.append('UNKNOWN_CHRONOLOGY_OR_INSUFFICIENT_SUPPORT_EVIDENCE')
        if reasons:
            status = 'REVIEW'
        elif not qa.visit_ids:
            status, reasons = 'NOT_SURVEYED', ['NO_ACCEPTED_VISIT_IN_IMPORTED_DATA']
        elif progress + policy.numerical_tolerance_m >= length * policy.required_support_percent / 100 and all(ends):
            status, reasons = 'COMPLETE', ['EXPLICIT_PROGRESS_AND_ENDPOINT_POLICY_SATISFIED']
        else:
            status, reasons = 'INCOMPLETE', ['PROGRESS_OR_ENDPOINT_POLICY_NOT_SATISFIED']
        results.append(TransectResult(survey.survey_id, qa.transect_id, status, status, tuple(reasons),
            policy.selected_by, policy.policy_id, 'CURRENT', relevant[qa.transect_id], *ends,
            min(progress or 0, length or 0), min(corridor or 0, length or 0), qa))
    counts = Counter(t.final_status for t in results)
    missing = sum(t.qa.planned_length_m is None for t in results)
    planned = fsum(t.qa.planned_length_m or 0 for t in results)
    track = fsum(t.qa.known_track_length_m for t in results)
    progress = fsum(t.confirmed_progress_m for t in results)
    corridor = fsum(t.confirmed_corridor_m for t in results)
    unknown = sum(bool(t.qa.visit_ids) and (t.qa.progress_support is None or
        t.qa.progress_support.supported_length_m is None or t.qa.progress_support.excluded_group_count > 0 or bool(t.qa.errors)) for t in results)
    percent = lambda value: 100 * value / planned if not missing and planned > 0 else None
    summary = SurveySummary(len(results), len(results)-missing, missing,
        tuple((s, counts[s]) for s in STATUSES), None if missing else planned, planned,
        None if any(t.qa.unknown_track_visit_count for t in results) else track, track,
        progress, corridor, percent(progress), percent(corridor), unknown, len(unresolved), len(unscoped),
        sum(d.assignment_status == 'AMBIGUOUS' for d in membership.values()),
        sum(d.assignment_status == 'UNASSIGNED' for d in membership.values()), source_count, surveyor_count,
        'PARTIAL' if missing or unknown or any(t.qa.errors for t in results) else
        'FINISHED_WITH_REVIEW' if counts['REVIEW'] or unresolved else 'FINISHED')
    return SurveyResults(RESULTS_SCHEMA, survey.survey_id, survey.plan_revision, dependency_fingerprint,
        batch.batch_id, batch.review_revision, utc_time(created_utc), policy, tuple(evidence_history), tuple(results), summary)
