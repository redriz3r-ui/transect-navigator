"""Conservative continuity and append-only human review, independent of QGIS.

Source order is evidence of storage order only. A structural run has chronology
only when every instant is valid, unique and strictly increasing in source order.
No spatial matching or QA mathematics belongs in this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import Callable

from .intake import IntakeModel, portable_id
from .model import Survey, digest, required_text, utc_time

REVIEW_SCHEMA = 'survey-review-1'


@dataclass(frozen=True)
class ContinuityPolicy:
    """Explicit operational break criteria, not survey compliance standards."""
    max_time_gap_s: float
    max_spatial_gap_m: float

    def __post_init__(self) -> None:
        for value in (self.max_time_gap_s, self.max_spatial_gap_m):
            if isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError('Continuity gap limits must be finite and positive')


@dataclass(frozen=True)
class Segment:
    segment_id: str
    recording_id: str
    source_id: str
    surveyor_id: str | None
    structural_segment: str
    observation_ids: tuple[str, ...]
    ordering_basis: str
    start_utc: str | None
    end_utc: str | None
    boundary_reasons: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class Disposition:
    observation_id: str
    assignment_status: str = 'UNASSIGNED'
    activity_role: str = 'UNKNOWN'
    confidence_class: str = 'INSUFFICIENT_EVIDENCE'
    transect_id: str | None = None
    direction: str = 'UNKNOWN'
    decision_id: str | None = None


@dataclass(frozen=True)
class Decision:
    decision_id: str
    revision: int
    action: str
    observation_ids: tuple[str, ...]
    assignment_status: str
    activity_role: str
    transect_id: str | None
    direction: str
    reviewer: str
    utc: str
    reason: str
    superseded_decision_ids: tuple[str, ...]
    run_id: str
    input_digest: str
    parameter_digest: str


@dataclass(frozen=True)
class Visit:
    visit_id: str
    transect_id: str
    recording_id: str
    source_id: str
    surveyor_id: str | None
    segment_id: str
    decision_id: str
    observation_ids: tuple[str, ...]
    direction: str
    ordering_basis: str
    start_utc: str | None
    end_utc: str | None


def input_digest(survey: Survey, model: IntakeModel) -> str:
    """Bind all plan/retained input content, mappings and externally supplied identity.

    Failed/duplicate attempts do not change retained data; import ledger remains in
    the intake archive. Dictionary/provider storage order is not part of identity.
    """
    return digest({'survey': asdict(survey), 'intake': {
        name: [asdict(rows[key]) for key in sorted(rows)] for name, rows in (
            ('sources', model.sources), ('recordings', model.recordings),
            ('raw', model.raw_observations), ('normalized', model.normalized_observations))}})


def _instant(value: str | None) -> datetime | None:
    try:
        instant = datetime.fromisoformat(value) if value else None
        return instant if instant and instant.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def continuity_segments(model: IntakeModel, policy: ContinuityPolicy,
                        distance_m: Callable[[str, str], float],
                        reviewed_before: frozenset[str] = frozenset()) -> tuple[Segment, ...]:
    """Partition all observations without crossing recording or structural runs.

    The injected distance must be ellipsoidal/metres, never longitude/latitude
    Euclidean distance. Unknown chronology is explicitly flagged, never sorted into
    a guessed walk. Invalid positions are isolated; they remain review-visible.
    """
    output = []
    by_recording: dict[str, list] = {}
    for raw in model.raw_observations.values():
        by_recording.setdefault(raw.recording_id, []).append(raw)
    for recording_id in sorted(by_recording):
        recording = model.recordings[recording_id]
        rows = sorted(by_recording[recording_id], key=lambda row: row.source_order)
        if len({r.source_order for r in rows}) != len(rows):
            raise ValueError('Source order is not unique within recording')
        runs: list[list] = []
        for row in rows:
            if not runs or runs[-1][-1].structural_segment != row.structural_segment:
                runs.append([])
            runs[-1].append(row)
        for run_index, run in enumerate(runs):
            normalized = [model.normalized_observations[r.observation_id] for r in run]
            times = [_instant(n.timestamp_utc) for n in normalized]
            flags = set()
            if any(t is None for t in times):
                flags.add('MISSING_OR_INVALID_TIME')
            known = [t for t in times if t is not None]
            if len(known) != len(set(known)) or any(n.time_status == 'duplicate' for n in normalized):
                flags.add('DUPLICATE_TIME')
            if any(a is not None and b is not None and b < a for a, b in zip(times, times[1:])):
                flags.add('CLOCK_REVERSAL')
            chronological = not flags
            chunk = []
            reasons = ['RECORDING_START' if run_index == 0 else 'STRUCTURAL_BOUNDARY']

            def emit() -> None:
                if not chunk:
                    return
                ids = tuple(r.observation_id for r in chunk)
                segment_flags = flags | ({'INVALID_POSITION'} if any(
                    model.normalized_observations[key].position_status != 'valid' for key in ids) else set())
                output.append(Segment(
                    portable_id('SEG', [asdict(policy), ids, reasons, sorted(segment_flags)]),
                    recording_id, recording.source_id, recording.surveyor_id,
                    chunk[0].structural_segment, ids,
                    'UTC_SOURCE_ORDER' if chronological else 'SOURCE_ORDER_ONLY',
                    model.normalized_observations[ids[0]].timestamp_utc if chronological else None,
                    model.normalized_observations[ids[-1]].timestamp_utc if chronological else None,
                    tuple(reasons), tuple(sorted(segment_flags))))

            for index, row in enumerate(run):
                boundaries = []
                current = normalized[index]
                if index:
                    previous = normalized[index - 1]
                    if current.position_status != 'valid' or previous.position_status != 'valid':
                        boundaries.append('INVALID_POSITION')
                    else:
                        distance = distance_m(run[index - 1].observation_id, row.observation_id)
                        if not isfinite(distance) or distance < 0:
                            raise ValueError('Invalid continuity distance')
                        if distance > policy.max_spatial_gap_m:
                            boundaries.append('SPATIAL_GAP')
                    a, b = times[index - 1:index + 1]
                    if a is not None and b is not None:
                        if b <= a:
                            boundaries.append('NONINCREASING_TIME')
                        elif (b - a).total_seconds() > policy.max_time_gap_s:
                            boundaries.append('TIME_GAP')
                    elif a is not None or b is not None:
                        boundaries.append('TIME_VALIDITY_BOUNDARY')
                if row.observation_id in reviewed_before:
                    boundaries.append('REVIEWED_BOUNDARY')
                if boundaries:
                    emit()
                    chunk = []
                    reasons = boundaries
                chunk.append(row)
            emit()
    return tuple(output)


class ReviewRun:
    """Human-confirmed dispositions with exact membership and immutable history.

    Mutations validate before publication. Restoring a run replays its complete
    history against current immutable inputs and compares every derived record.
    """
    def __init__(self, survey: Survey, model: IntakeModel, policy: ContinuityPolicy,
                 distance_m: Callable[[str, str], float], *, created_utc: str | None = None):
        model.validate()
        if model.survey_id != survey.survey_id:
            raise ValueError('Review and intake Survey_ID differ')
        self.survey, self.model, self.policy = survey, model, policy
        self._distance = distance_m
        self.created_utc = utc_time(created_utc)
        self.input_digest = input_digest(survey, model)
        self.parameter_digest = digest(asdict(policy))
        self.run_id = portable_id('RUN', [self.input_digest, self.parameter_digest, self.created_utc])
        self._history: list[Decision] = []
        self._membership = {key: Disposition(key) for key in sorted(model.raw_observations)}
        self._cuts: set[str] = set()
        self.segments = continuity_segments(model, policy, distance_m)
        self._order = {key: i for i, key in enumerate(
            key for segment in self.segments for key in segment.observation_ids)}

    @property
    def history(self) -> tuple[Decision, ...]:
        return tuple(self._history)

    @property
    def membership(self) -> dict[str, Disposition]:
        return dict(self._membership)

    @property
    def unresolved_count(self) -> int:
        """Transitions are resolved non-survey work; unknown/ambiguous remain visible."""
        return sum(d.assignment_status != 'ASSIGNED' and d.activity_role != 'TRANSITION'
                   for d in self._membership.values())

    def assert_current(self) -> None:
        if input_digest(self.survey, self.model) != self.input_digest:
            raise ValueError('Stale review: plan, intake, mappings or surveyor identity changed; start a new review run')

    def decide(self, observation_ids: list[str] | tuple[str, ...], *, reviewer: str,
               reason: str, assignment_status: str = 'UNASSIGNED',
               activity_role: str = 'UNKNOWN', transect_id: str | None = None,
               direction: str = 'UNKNOWN', utc: str | None = None,
               action: str = 'DISPOSITION') -> Decision:
        self.assert_current()
        required_text(reviewer, 'Reviewer')
        required_text(reason, 'Review reason')
        ids = tuple(observation_ids)
        if not ids or len(ids) != len(set(ids)) or not set(ids) <= self._membership.keys():
            raise ValueError('Select nonempty, unique, known Observation_ID membership')
        ids = tuple(sorted(ids, key=self._order.__getitem__))
        if action not in ('DISPOSITION', 'SPLIT_BEFORE'):
            raise ValueError('Unknown review action')
        if assignment_status not in ('ASSIGNED', 'AMBIGUOUS', 'UNASSIGNED'):
            raise ValueError('Invalid assignment status')
        if activity_role not in ('SURVEY', 'TRANSITION', 'UNKNOWN'):
            raise ValueError('Invalid activity role')
        if direction not in ('FORWARD', 'REVERSE', 'MIXED', 'UNKNOWN'):
            raise ValueError('Invalid traversal direction')
        if assignment_status == 'ASSIGNED':
            if transect_id not in {p.transect_id for p in self.survey.plans if p.transect_id}:
                raise ValueError('Select an official Transect_ID')
            if activity_role != 'SURVEY':
                raise ValueError('Accepted assignments must have SURVEY activity')
            if any(self.model.normalized_observations[key].position_status != 'valid' for key in ids):
                raise ValueError('Invalid-position observations cannot enter accepted Visits')
        elif transect_id is not None or direction != 'UNKNOWN':
            raise ValueError('Unresolved/transition observations cannot have an accepted transect or direction')
        if activity_role == 'TRANSITION' and assignment_status != 'UNASSIGNED':
            raise ValueError('Transitions must be UNASSIGNED')
        if action == 'SPLIT_BEFORE' and (assignment_status, activity_role) != ('UNASSIGNED', 'UNKNOWN'):
            raise ValueError('A split changes only boundaries, not dispositions')
        timestamp = utc_time(utc)
        superseded = tuple(sorted({self._membership[key].decision_id for key in ids
                                   if self._membership[key].decision_id})) if action == 'DISPOSITION' else ()
        payload = [self.run_id, len(self._history) + 1, action, ids, assignment_status,
                   activity_role, transect_id, direction, reviewer, timestamp, reason, superseded]
        decision = Decision(portable_id('DEC', payload), len(self._history) + 1,
                            action, ids, assignment_status, activity_role, transect_id,
                            direction, reviewer, timestamp, reason, superseded,
                            self.run_id, self.input_digest, self.parameter_digest)
        if action == 'SPLIT_BEFORE':
            cuts = self._cuts | set(ids)
            segments = continuity_segments(self.model, self.policy, self._distance, frozenset(cuts))
            self._cuts, self.segments = cuts, segments
        else:
            for key in ids:
                self._membership[key] = Disposition(key, assignment_status, activity_role,
                    'REVIEWER_CONFIRMED', transect_id, direction, decision.decision_id)
        self._history.append(decision)
        return decision

    @property
    def visits(self) -> tuple[Visit, ...]:
        result = []
        for segment in self.segments:
            groups: list[list[str]] = []
            previous = None
            for key in segment.observation_ids:
                disposition = self._membership[key]
                accepted = disposition.assignment_status == 'ASSIGNED' and disposition.activity_role == 'SURVEY'
                identity = disposition.decision_id if accepted else None
                if accepted:
                    if identity != previous:
                        groups.append([])
                    groups[-1].append(key)
                previous = identity
            for group in groups:
                ids = tuple(group)
                d = self._membership[ids[0]]
                chronology = segment.ordering_basis == 'UTC_SOURCE_ORDER'
                result.append(Visit(portable_id('VIS', [self.run_id, segment.segment_id, d.decision_id, ids]),
                    d.transect_id, segment.recording_id, segment.source_id, segment.surveyor_id,
                    segment.segment_id, d.decision_id, ids, d.direction, segment.ordering_basis,
                    self.model.normalized_observations[ids[0]].timestamp_utc if chronology else None,
                    self.model.normalized_observations[ids[-1]].timestamp_utc if chronology else None))
        return tuple(result)

    @property
    def qa_input_digest(self) -> str:
        """Future QA must store this token and reject results when it changes."""
        self.assert_current()
        return digest([self.input_digest, self.parameter_digest, len(self._history),
                       [asdict(d) for d in self._membership.values()],
                       [asdict(v) for v in self.visits]])

    def snapshot(self) -> dict:
        self.assert_current()
        return {'schema': REVIEW_SCHEMA, 'created_utc': self.created_utc,
                'survey_id': self.survey.survey_id, 'plan_revision': self.survey.plan_revision,
                'input_digest': self.input_digest, 'run_id': self.run_id,
                'policy': asdict(self.policy), 'qa_input_digest': self.qa_input_digest,
                'decisions': [asdict(d) for d in self.history],
                'membership': [asdict(d) for d in self._membership.values()],
                'segments': [asdict(s) for s in self.segments],
                'visits': [asdict(v) for v in self.visits]}

    @classmethod
    def restore(cls, snapshot: dict, survey: Survey, model: IntakeModel,
                distance_m: Callable[[str, str], float]) -> ReviewRun:
        from .model import canonical_json
        if snapshot.get('schema') != REVIEW_SCHEMA:
            raise ValueError('Unsupported review schema')
        run = cls(survey, model, ContinuityPolicy(**snapshot['policy']), distance_m,
                  created_utc=snapshot['created_utc'])
        if snapshot['input_digest'] != run.input_digest:
            raise ValueError('Stale review: Survey/plan/intake revision differs')
        for row in snapshot['decisions']:
            run.decide(row['observation_ids'], reviewer=row['reviewer'], reason=row['reason'],
                       assignment_status=row['assignment_status'], activity_role=row['activity_role'],
                       transect_id=row['transect_id'], direction=row['direction'], utc=row['utc'], action=row['action'])
        if canonical_json(run.snapshot()) != canonical_json(snapshot):
            raise ValueError('Review history, membership or derived records failed replay verification')
        return run
