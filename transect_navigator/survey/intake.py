"""Pure, persistent records for immutable multi-source Survey intake.

This module deliberately contains no QGIS imports.  Native geometry and attribute
snapshots live at the integration boundary; these records provide portable identity,
provenance, validation and audit state for those snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import Counter
from typing import Iterable

from .model import digest, required_text

INTAKE_SCHEMA_VERSION = 'survey-intake-1'
SOURCE_ID_VERSION = 'survey-source-content-sha256-1'
RECORDING_ID_VERSION = 'survey-source-recording-sha256-1'
OBSERVATION_ID_VERSION = 'survey-recording-original-key-sha256-1'

MAX_SURVEY_SOURCES = 100
MAX_SURVEY_RECORDINGS = 1_000
MAX_SURVEY_OBSERVATIONS = 1_000_000
MAX_SURVEY_SOURCE_BYTES = 500 * 1024 * 1024


def portable_id(prefix: str, payload: object) -> str:
    """Return a readable, deterministic identity from canonical typed content."""
    return f'{prefix}_{digest(payload)[:32]}'


def source_id(survey_id: str, content_identity: str) -> str:
    required_text(survey_id, 'Survey_ID')
    required_text(content_identity, 'Source content identity')
    return portable_id('SRC', [SOURCE_ID_VERSION, survey_id, content_identity])


def recording_id(source_identifier: str, recording_key: str) -> str:
    required_text(source_identifier, 'Source_ID')
    required_text(recording_key, 'Recording identity')
    return portable_id('REC', [RECORDING_ID_VERSION, source_identifier, recording_key])


def observation_id(recording_identifier: str, original_key: str) -> str:
    required_text(recording_identifier, 'Recording_ID')
    required_text(original_key, 'Original observation identity')
    return portable_id('OBS', [OBSERVATION_ID_VERSION, recording_identifier, original_key])


@dataclass(frozen=True)
class FieldMappings:
    """Explicit user mappings; blanks mean unknown, never guessed units."""

    timestamp: str = ''
    timezone: str = ''
    accuracy: str = ''
    hdop: str = ''
    pdop: str = ''
    satellites: str = ''
    fix: str = ''
    elevation: str = ''


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    survey_id: str
    source_file: str
    original_path: str
    original_name: str
    content_identity: str
    sha256: str | None
    snapshot_digest: str | None
    source_kind: str
    source_format: str
    imported_utc: str
    original_crs_json: str
    original_schema_json: str
    archived_reference: str
    import_status: str
    validation_error: str
    byte_size: int | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordingRecord:
    recording_id: str
    source_id: str
    recording_key: str
    original_identity: str
    surveyor_id: str | None
    mappings: FieldMappings
    structural_segments: tuple[str, ...]
    original_crs_wkt: str
    original_crs_authid: str
    original_schema_json: str
    raw_observation_layer: str
    raw_track_layer: str
    observation_count: int
    start_time_utc: str | None
    end_time_utc: str | None
    import_status: str
    validation_error: str = ''
    raw_track_schema_json: str = ''
    raw_track_digest: str = ''


@dataclass(frozen=True)
class RawObservationRecord:
    observation_id: str
    source_id: str
    recording_id: str
    original_observation_key: str
    original_trace_key: str
    source_order: int
    structural_segment: str
    raw_layer: str
    raw_attribute_reference: str
    raw_geometry_reference: str
    raw_attributes_digest: str
    raw_geometry_digest: str


@dataclass(frozen=True)
class NormalizedObservationRecord:
    observation_id: str
    longitude: float | None
    latitude: float | None
    original_timestamp: str | None
    timestamp_utc: str | None
    time_status: str
    position_status: str
    source_order: int
    accuracy_raw: str | None
    hdop_raw: str | None
    pdop_raw: str | None
    satellites_raw: str | None
    fix_raw: str | None
    elevation_raw: str | None


@dataclass(frozen=True)
class ImportAttempt:
    attempt_id: str
    survey_id: str
    requested_source: str
    requested_name: str
    imported_utc: str
    status: str
    source_id: str | None
    duplicate_of_source_id: str | None
    message: str


@dataclass(frozen=True)
class IntakeTotals:
    state: str
    source_count: int
    recording_count: int
    observation_count: int
    source_bytes: int
    successful_source_count: int
    partial_source_count: int
    failed_attempt_count: int
    duplicate_attempt_count: int
    overlap_warning_count: int
    unknown_surveyor_recording_count: int


class IntakeModel:
    """Mutable registry of immutable records with deterministic reconciliation."""

    def __init__(self, survey_id: str) -> None:
        self.survey_id = required_text(survey_id, 'Survey_ID')
        self.sources: dict[str, SourceRecord] = {}
        self.recordings: dict[str, RecordingRecord] = {}
        self.raw_observations: dict[str, RawObservationRecord] = {}
        self.normalized_observations: dict[str, NormalizedObservationRecord] = {}
        self.attempts: list[ImportAttempt] = []

    def set_surveyor(self, recording_identifier: str, surveyor_id: str | None) -> None:
        """Set only explicit user-supplied identity; blank is stored as unknown/NULL."""
        if recording_identifier not in self.recordings:
            raise KeyError(recording_identifier)
        value = None if surveyor_id is None or not surveyor_id.strip() else required_text(
            surveyor_id, 'Surveyor_ID')
        self.recordings[recording_identifier] = replace(
            self.recordings[recording_identifier], surveyor_id=value)

    def totals(self) -> IntakeTotals:
        failures = sum(a.status == 'FAILED' for a in self.attempts)
        duplicates = sum(a.status == 'DUPLICATE' for a in self.attempts)
        successful = sum(s.import_status == 'SUCCESS' for s in self.sources.values())
        partial = sum(s.import_status == 'PARTIAL' for s in self.sources.values())
        accepted = successful + partial
        state = ('FAILED' if failures and not accepted else
                 'PARTIAL' if failures or partial else
                 'SUCCESS' if accepted else 'EMPTY')
        return IntakeTotals(
            state=state,
            source_count=len(self.sources),
            recording_count=len(self.recordings),
            observation_count=len(self.raw_observations),
            source_bytes=sum(s.byte_size or 0 for s in self.sources.values()),
            successful_source_count=successful,
            partial_source_count=partial,
            failed_attempt_count=failures,
            duplicate_attempt_count=duplicates,
            overlap_warning_count=sum('SUSPECTED_OVERLAP' in s.warnings
                                      for s in self.sources.values()),
            unknown_surveyor_recording_count=sum(r.surveyor_id is None
                                                 for r in self.recordings.values()),
        )

    def validate(self) -> None:
        """Prove every retained observation has one complete provenance chain."""
        if len(self.sources) > MAX_SURVEY_SOURCES:
            raise ValueError(f'Survey exceeds {MAX_SURVEY_SOURCES} accepted sources')
        if len(self.recordings) > MAX_SURVEY_RECORDINGS:
            raise ValueError(f'Survey exceeds {MAX_SURVEY_RECORDINGS:,} recordings')
        if len(self.raw_observations) > MAX_SURVEY_OBSERVATIONS:
            raise ValueError(f'Survey exceeds {MAX_SURVEY_OBSERVATIONS:,} observations')
        if sum(s.byte_size or 0 for s in self.sources.values()) > MAX_SURVEY_SOURCE_BYTES:
            raise ValueError('Survey source snapshots exceed 500 MiB')
        for source in self.sources.values():
            if source.survey_id != self.survey_id or source.source_id != source_id(
                    self.survey_id, source.content_identity):
                raise ValueError('Source identity or Survey_ID is inconsistent')
            if source.sha256 is not None and source.content_identity != f'file-sha256:{source.sha256}':
                raise ValueError('File source checksum and content identity are inconsistent')
        for recording in self.recordings.values():
            if recording.source_id not in self.sources:
                raise ValueError('Recording references an absent Source_ID')
            if recording.recording_id != recording_id(recording.source_id,
                                                       recording.recording_key):
                raise ValueError('Recording identity is inconsistent')
        for key, raw in self.raw_observations.items():
            if raw.recording_id not in self.recordings or raw.source_id not in self.sources:
                raise ValueError('Raw observation provenance chain is incomplete')
            if key != raw.observation_id or key != observation_id(
                    raw.recording_id, raw.original_observation_key):
                raise ValueError('Observation identity is inconsistent')
            if raw.source_id != self.recordings[raw.recording_id].source_id:
                raise ValueError('Observation Source_ID disagrees with its Recording_ID')
            if key not in self.normalized_observations:
                raise ValueError('Raw observation has no normalized working record')
            normalized = self.normalized_observations[key]
            if normalized.observation_id != key or normalized.source_order != raw.source_order:
                raise ValueError('Normalized observation link or source order is inconsistent')
        if set(self.normalized_observations) != set(self.raw_observations):
            raise ValueError('Normalized observations must correspond exactly to raw observations')
        counts = Counter(row.recording_id for row in self.raw_observations.values())
        for recording in self.recordings.values():
            if recording.observation_count != counts[recording.recording_id]:
                raise ValueError('Recording observation count does not reconcile')
        if any(attempt.survey_id != self.survey_id for attempt in self.attempts):
            raise ValueError('Import attempt belongs to a different Survey_ID')

    def all_records(self) -> Iterable[object]:
        yield from self.sources.values()
        yield from self.recordings.values()
        yield from self.raw_observations.values()
        yield from self.normalized_observations.values()
        yield from self.attempts
