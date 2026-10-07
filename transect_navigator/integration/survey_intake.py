"""Immutable, multi-source Survey recording intake before any plan association."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qgis.core import (NULL, Qgis, QgsCoordinateReferenceSystem, QgsCsException,
                       QgsFeature, QgsProject, QgsProviderRegistry, QgsVectorLayer)

from .analyze import _text, _transform, _xy
from .recorded import RecordingSource, from_file_records, recording_from_layer
from .survey_prepare import _native_value, _schema, layer_digest
from ..analysis.observations import parse_timestamp
from ..io.recorded import (FileRecording, InputTrack, read_bytes, read_csv, read_gpx)
from ..survey.intake import (FieldMappings, ImportAttempt, IntakeModel,
                             MAX_SURVEY_OBSERVATIONS, MAX_SURVEY_RECORDINGS,
                             MAX_SURVEY_SOURCE_BYTES, MAX_SURVEY_SOURCES,
                             NormalizedObservationRecord, RawObservationRecord,
                             RecordingRecord, SourceRecord, observation_id,
                             portable_id, recording_id, source_id)
from ..survey.model import Survey, canonical_json, digest, utc_time


@dataclass(frozen=True)
class NativeRecordingSnapshot:
    """Detached native layers for one recording; never a merged authoritative track."""

    recording_id: str
    observations: QgsVectorLayer
    tracks: QgsVectorLayer | None


@dataclass(frozen=True)
class ImportOptions:
    """Explicit mappings for a source; CSV coordinates and CRS are never guessed."""

    x_field: str = ''
    y_field: str = ''
    csv_crs: str = ''
    mappings: FieldMappings = FieldMappings()
    surveyor_id: str | None = None


def _source_format(path: Path) -> str:
    value = path.suffix.lower()
    formats = {'.gpx': 'GPX', '.csv': 'CSV', '.gpkg': 'GeoPackage',
               '.geojson': 'GeoJSON', '.json': 'GeoJSON'}
    if value not in formats:
        raise ValueError('Supported survey recordings are GPX, CSV, GeoPackage and GeoJSON')
    return formats[value]


def _schema_json(layer: QgsVectorLayer) -> str:
    return canonical_json({'geometry_type': int(layer.wkbType()), 'fields': _schema(layer)})


def _value(layer_feature: QgsFeature, field: str) -> str | None:
    if not field:
        return None
    index = layer_feature.fields().indexFromName(field)
    if index < 0:
        raise ValueError(f'Mapped field {field!r} is absent')
    return _text(layer_feature[index])


def _optional_value(layer_feature: QgsFeature, field: str) -> str | None:
    """Represent an explicitly blank optional mapped value as unknown, not zero."""
    value = _value(layer_feature, field)
    return None if value == '' else value


def _timestamp(value: str | None, timezone_name: str) -> tuple[str | None, str]:
    parsed, status = parse_timestamp(value)
    if parsed is not None:
        return parsed.isoformat().replace('+00:00', 'Z'), status
    if status == 'missing' or not timezone_name:
        return None, status
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return None, 'invalid_timezone_mapping'
    text = str(value).strip()
    try:
        local = datetime.fromisoformat(text)
    except (ValueError, OverflowError):
        return None, status
    if local.tzinfo is not None:
        return None, status
    first = local.replace(tzinfo=zone, fold=0)
    second = local.replace(tzinfo=zone, fold=1)
    first_utc = first.astimezone(timezone.utc)
    second_utc = second.astimezone(timezone.utc)
    first_valid = first_utc.astimezone(zone).replace(tzinfo=None) == local
    second_valid = second_utc.astimezone(zone).replace(tzinfo=None) == local
    if first_valid and second_valid and first.utcoffset() != second.utcoffset():
        return None, 'ambiguous_local_time'
    if not first_valid and not second_valid:
        return None, 'nonexistent_local_time'
    utc = first_utc if first_valid else second_utc
    return utc.isoformat().replace('+00:00', 'Z'), 'valid_timezone_mapped'


def _portable_loaded_keys(source: RecordingSource) -> dict[int, str]:
    """Build content/occurrence keys without mutable provider FIDs or layer IDs."""
    rows = []
    helper = source.points.fields().indexFromName(source.source_id_field)
    for feature in source.points.getFeatures():
        attrs = [_native_value(value) for index, value in enumerate(feature.attributes())
                 if index != helper]
        content = digest({'geometry': bytes(feature.geometry().asWkb()).hex(),
                          'attributes': attrs})
        rows.append((content, feature.id()))
    occurrences: Counter[str] = Counter()
    result = {}
    # Identical rows are intentionally indistinguishable; their occurrence keys form
    # a stable multiset even when a provider changes iteration order or FIDs.
    for content, feature_id in sorted(rows):
        occurrences[content] += 1
        result[feature_id] = f'content_{content}/occurrence_{occurrences[content]}'
    return result


class SurveyIntake:
    """Survey-level intake registry and detached raw snapshots.

    Import is atomic per source.  Failures and duplicate attempts are audit records;
    accepted sources, recordings and observations are not rolled back by another
    source's failure.
    """

    def __init__(self, survey: Survey, *, project: QgsProject | None = None) -> None:
        self.survey = survey
        self.project = project if project is not None else QgsProject.instance()
        self.model = IntakeModel(survey.survey_id)
        self.native_recordings: dict[str, NativeRecordingSnapshot] = {}
        self.source_bytes: dict[str, bytes] = {}

    def _attempt(self, requested: str, name: str, imported: str, status: str,
                 source_identifier: str | None, duplicate: str | None, message: str) -> ImportAttempt:
        attempt = ImportAttempt(
            portable_id('IMP', ['survey-import-attempt-1', self.survey.survey_id,
                                 len(self.model.attempts) + 1, imported, requested, status]),
            self.survey.survey_id, requested, name, imported, status,
            source_identifier, duplicate, message)
        self.model.attempts.append(attempt)
        return attempt

    def import_file(self, path: Path, options: ImportOptions = ImportOptions(), *,
                    imported_utc: str | None = None) -> ImportAttempt:
        """Capture one bounded byte snapshot, then parse only that immutable snapshot."""
        imported = utc_time(imported_utc)
        path = Path(path).expanduser()
        requested = str(path)
        name = path.name
        content = None
        sha = None
        try:
            source_format = _source_format(path)
            content = read_bytes(path)
            sha = hashlib.sha256(content).hexdigest()
            content_identity = f'file-sha256:{sha}'
            identifier = source_id(self.survey.survey_id, content_identity)
            if identifier in self.model.sources:
                return self._attempt(requested, name, imported, 'DUPLICATE', identifier,
                                     identifier, 'Exact source content was already imported; no records were duplicated')
            if len(self.model.sources) >= MAX_SURVEY_SOURCES:
                raise ValueError(f'Survey exceeds {MAX_SURVEY_SOURCES} accepted sources')
            if sum(len(value) for value in self.source_bytes.values()) + len(content) > MAX_SURVEY_SOURCE_BYTES:
                raise ValueError('Survey source snapshots exceed 500 MiB')
            sources = self._read_captured_file(content, path, source_format, options)
            return self._accept(identifier, content_identity, source_format, 'FILE', requested,
                                name, imported, sources, options, content, sha)
        except (ValueError, OSError, QgsCsException, RuntimeError) as error:
            identifier = None
            if content is not None and sha is not None:
                identifier = source_id(self.survey.survey_id, f'file-sha256:{sha}')
                if identifier not in self.model.sources and len(self.model.sources) < MAX_SURVEY_SOURCES:
                    self.model.sources[identifier] = SourceRecord(
                        identifier, self.survey.survey_id, name, requested, name,
                        f'file-sha256:{sha}', sha, None, 'FILE',
                        path.suffix.lower().lstrip('.').upper(), imported, '[]', '[]',
                        f'sources/{sha}{path.suffix.lower()}', 'FAILED', str(error), len(content))
                    self.source_bytes[identifier] = content
            return self._attempt(requested, name, imported, 'FAILED', identifier, None, str(error))

    def import_layer(self, layer: QgsVectorLayer, *, source_file: str = '',
                     options: ImportOptions = ImportOptions(),
                     imported_utc: str | None = None) -> ImportAttempt:
        """Capture a loaded layer by content; QGIS layer IDs/FIDs are trace-only."""
        imported = utc_time(imported_utc)
        requested = source_file or (layer.name() if layer is not None else 'loaded layer')
        name = Path(source_file).name if source_file else requested
        try:
            if layer is None or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
                raise ValueError('Choose a valid loaded recording layer')
            snapshot_digest = layer_digest(layer)
            content_identity = f'loaded-layer-snapshot-sha256:{snapshot_digest}'
            identifier = source_id(self.survey.survey_id, content_identity)
            if identifier in self.model.sources:
                return self._attempt(requested, name, imported, 'DUPLICATE', identifier,
                                     identifier, 'Exact loaded-layer snapshot was already imported; no records were duplicated')
            if len(self.model.sources) >= MAX_SURVEY_SOURCES:
                raise ValueError(f'Survey exceeds {MAX_SURVEY_SOURCES} accepted sources')
            recording = recording_from_layer(layer)
            return self._accept(identifier, content_identity, 'QGIS_LAYER', 'LOADED_LAYER',
                                requested, name, imported,
                                [(f'loaded:{snapshot_digest}', layer.name(), recording, True, '')],
                                options, None, None, snapshot_digest)
        except (ValueError, OSError, QgsCsException, RuntimeError) as error:
            return self._attempt(requested, name, imported, 'FAILED', None, None, str(error))

    def _read_captured_file(self, content: bytes, original: Path, source_format: str,
                            options: ImportOptions) -> list[tuple[str, str, RecordingSource | None, bool, str]]:
        """Parse a private one-read snapshot; never reread changing source bytes."""
        with TemporaryDirectory() as folder:
            captured = Path(folder) / f'source{original.suffix.lower()}'
            captured.write_bytes(content)
            if source_format == 'GPX':
                records = read_gpx(captured)
                tracks = sorted({point.group.split('/segment_', 1)[0] for point in records.points})
                result = []
                for track_key in tracks:
                    points = tuple(point for point in records.points
                                   if point.group.startswith(track_key + '/segment_'))
                    segments = tuple(track for track in records.tracks
                                     if track.group.startswith(track_key + '/segment_'))
                    subset = FileRecording(points, segments, records.issues, records.sha256,
                                           str(original.resolve()), records.raw_document)
                    result.append((f'gpx:{track_key}', track_key,
                                   from_file_records(subset, QgsCoordinateReferenceSystem('EPSG:4326')),
                                   False, ''))
                return result
            if source_format == 'CSV':
                records = read_csv(captured, options.x_field, options.y_field)
                records = replace(records, source_path=str(original.resolve()))
                source = from_file_records(records, QgsCoordinateReferenceSystem(options.csv_crs))
                return [('csv:rows', original.name, source, False, '')]
            details = sorted(
                (detail for detail in QgsProviderRegistry.instance().querySublayers(str(captured))
                 if detail.type() == Qgis.LayerType.Vector),
                key=lambda detail: (detail.name().casefold(), detail.name()))
            if not details:
                raise ValueError('QGIS found no vector recording layers in the source')
            if len({detail.name() for detail in details}) != len(details):
                raise ValueError('Source has duplicate vector sublayer names; recording identity is ambiguous')
            result = []
            for detail in details:
                key = f'ogr:{detail.name()}'
                try:
                    layer = QgsVectorLayer(detail.uri(), detail.name(), 'ogr')
                    result.append((key, detail.name(), recording_from_layer(layer), False, ''))
                except (ValueError, QgsCsException, RuntimeError) as error:
                    result.append((key, detail.name(), None, False, str(error)))
            return result

    def _accept(self, identifier: str, content_identity: str, source_format: str,
                source_kind: str, requested: str, name: str, imported: str,
                candidates: list[tuple[str, str, RecordingSource | None, bool, str]],
                options: ImportOptions, content: bytes | None, sha: str | None,
                snapshot_digest: str | None = None) -> ImportAttempt:
        local_recordings: dict[str, RecordingRecord] = {}
        local_raw: dict[str, RawObservationRecord] = {}
        local_normalized: dict[str, NormalizedObservationRecord] = {}
        local_native: dict[str, NativeRecordingSnapshot] = {}
        failures = []
        notices = []
        for key, original_identity, source, portable_keys, failure in candidates:
            rec_id = recording_id(identifier, key)
            if failure or source is None:
                failures.append(f'{original_identity}: {failure}')
                local_recordings[rec_id] = RecordingRecord(
                    rec_id, identifier, key, original_identity, options.surveyor_id,
                    options.mappings, (), '', '', '[]', '', '', 0, None, None,
                    'FAILED', failure)
                continue
            try:
                notices.extend(f'{issue.code}: {issue.message}' for issue in source.issues)
                record, raw, normalized = self._capture_recording(
                    identifier, rec_id, key, original_identity, source, options,
                    portable_keys)
                local_recordings[rec_id] = record
                local_raw.update(raw)
                local_normalized.update(normalized)
                local_native[rec_id] = NativeRecordingSnapshot(rec_id, source.points, source.tracks)
            except (ValueError, QgsCsException, RuntimeError) as error:
                failures.append(f'{original_identity}: {error}')
                local_recordings[rec_id] = RecordingRecord(
                    rec_id, identifier, key, original_identity, options.surveyor_id,
                    options.mappings, (), '', '', '[]', '', '', 0, None, None,
                    'FAILED', str(error))
        successes = [record for record in local_recordings.values()
                     if record.import_status == 'SUCCESS']
        status = 'SUCCESS' if successes and not failures else 'PARTIAL' if successes else 'FAILED'
        crs = [json.loads(value) for value in sorted({canonical_json(
            {'wkt': r.original_crs_wkt, 'authid': r.original_crs_authid})
            for r in successes})]
        schemas = [json.loads(value) for value in sorted(
            {r.original_schema_json for r in successes})]
        archived = (f'sources/{sha}{Path(name).suffix.lower()}' if content is not None and sha
                    else 'survey_intake.gpkg#native-recording-snapshots')
        source_record = SourceRecord(
            identifier, self.survey.survey_id, name, requested, name, content_identity,
            sha, snapshot_digest, source_kind, source_format, imported,
            canonical_json(crs), canonical_json(schemas), archived, status,
            '; '.join(failures), len(content) if content is not None else None,
            tuple(dict.fromkeys([*(f'IMPORT_WARNING:{message}' for message in failures),
                                 *notices])))
        projected_observations = len(self.model.raw_observations) + len(local_raw)
        if len(self.model.recordings) + len(local_recordings) > MAX_SURVEY_RECORDINGS:
            raise ValueError(f'Survey exceeds {MAX_SURVEY_RECORDINGS:,} recordings')
        if projected_observations > MAX_SURVEY_OBSERVATIONS:
            raise ValueError(f'Survey exceeds {MAX_SURVEY_OBSERVATIONS:,} observations')
        self._mark_duplicate_times_in(local_raw, local_normalized)
        candidate = IntakeModel(self.survey.survey_id)
        candidate.sources = dict(self.model.sources)
        candidate.recordings = dict(self.model.recordings)
        candidate.raw_observations = dict(self.model.raw_observations)
        candidate.normalized_observations = dict(self.model.normalized_observations)
        candidate.attempts = list(self.model.attempts)
        candidate.sources[identifier] = source_record
        candidate.recordings.update(local_recordings)
        candidate.raw_observations.update(local_raw)
        candidate.normalized_observations.update(local_normalized)
        candidate.validate()
        self.model = candidate
        self.native_recordings.update(local_native)
        if content is not None:
            self.source_bytes[identifier] = content
        self._mark_overlaps()
        self.model.validate()
        message = (f'Accepted {len(successes)} recording(s) and {len(local_raw)} observations'
                   + (f'; {len(failures)} recording(s) failed' if failures else ''))
        return self._attempt(requested, name, imported, status, identifier, None, message)

    def _capture_recording(self, source_identifier: str, rec_id: str, key: str,
                           original_identity: str, source: RecordingSource,
                           options: ImportOptions, portable_keys: bool) -> tuple[
                               RecordingRecord, dict[str, RawObservationRecord],
                               dict[str, NormalizedObservationRecord]]:
        mappings = options.mappings
        for field in (mappings.timestamp, mappings.accuracy, mappings.hdop,
                      mappings.pdop, mappings.satellites, mappings.fix,
                      mappings.elevation):
            if field and source.points.fields().indexFromName(field) < 0:
                raise ValueError(f'Mapped field {field!r} is absent')
        if mappings.timezone:
            try:
                ZoneInfo(mappings.timezone)
            except ZoneInfoNotFoundError as error:
                raise ValueError(f'Unknown timezone mapping {mappings.timezone!r}') from error
        transform = None
        transform_error = ''
        try:
            transform = _transform(source.points, self.project)
        except (ValueError, QgsCsException) as error:
            transform_error = str(error)
        portable = _portable_loaded_keys(source) if portable_keys else {}
        raw: dict[str, RawObservationRecord] = {}
        normalized: dict[str, NormalizedObservationRecord] = {}
        times = []
        for order, feature in enumerate(source.points.getFeatures(), 1):
            trace_key = _text(feature[source.source_id_field]) or f'source_order_{order}'
            original_key = portable.get(feature.id(), trace_key)
            obs_id = observation_id(rec_id, original_key)
            if obs_id in raw:
                raise ValueError('Original observation identities are not unique within recording')
            geometry = feature.geometry()
            geometry_bytes = bytes(geometry.asWkb())
            attributes = [_native_value(value) for value in feature.attributes()]
            segment = (_text(feature[source.segment_field]) if source.segment_field else None) or key
            raw[obs_id] = RawObservationRecord(
                obs_id, source_identifier, rec_id, original_key, trace_key, order,
                segment, f'raw_observations_{rec_id[4:16]}',
                f'{trace_key}:attributes', f'{trace_key}:geometry',
                digest(attributes), hashlib.sha256(geometry_bytes).hexdigest())
            longitude = latitude = None
            position_status = 'valid'
            if geometry.isNull() or geometry.isEmpty():
                position_status = 'missing_geometry'
            elif transform is None:
                position_status = 'invalid_crs:' + transform_error
            else:
                try:
                    point = geometry.constGet()
                    longitude, latitude = _xy(point, source.points, transform)
                    if not (isfinite(longitude) and isfinite(latitude)):
                        raise ValueError('nonfinite working coordinate')
                except (ValueError, QgsCsException, AttributeError, TypeError) as error:
                    longitude = latitude = None
                    position_status = f'invalid_position:{error}'
            original_time = _value(feature, mappings.timestamp)
            parsed_time, time_status = _timestamp(original_time, mappings.timezone)
            if parsed_time is not None:
                times.append(parsed_time)
            normalized[obs_id] = NormalizedObservationRecord(
                obs_id, longitude, latitude, original_time, parsed_time, time_status,
                position_status, order, _optional_value(feature, mappings.accuracy),
                _optional_value(feature, mappings.hdop), _optional_value(feature, mappings.pdop),
                _optional_value(feature, mappings.satellites), _optional_value(feature, mappings.fix),
                _optional_value(feature, mappings.elevation))
        if not raw:
            raise ValueError('Recording contains no observations')
        layer_name = f'raw_observations_{rec_id[4:16]}'
        track_name = f'raw_tracks_{rec_id[4:16]}' if source.tracks is not None else ''
        return (RecordingRecord(
            rec_id, source_identifier, key, original_identity,
            options.surveyor_id.strip() if options.surveyor_id and options.surveyor_id.strip() else None,
            mappings, tuple(group for group, _ in source.geometry_orders)
            or tuple(sorted({row.structural_segment for row in raw.values()})),
            source.points.crs().toWkt(), source.points.crs().authid(),
            _schema_json(source.points), layer_name, track_name, len(raw),
            min(times) if times else None, max(times) if times else None, 'SUCCESS', '',
            _schema_json(source.tracks) if source.tracks is not None else '',
            layer_digest(source.tracks) if source.tracks is not None else ''),
                raw, normalized)

    @staticmethod
    def _mark_duplicate_times_in(
            raw_rows: Mapping[str, RawObservationRecord],
            normalized_rows: dict[str, NormalizedObservationRecord]) -> None:
        by_recording: dict[str, list[str]] = defaultdict(list)
        for observation_id_value in normalized_rows:
            by_recording[raw_rows[observation_id_value].recording_id].append(
                observation_id_value)
        for members in by_recording.values():
            counts = Counter(normalized_rows[key].timestamp_utc
                             for key in members
                             if normalized_rows[key].timestamp_utc is not None)
            for key in members:
                row = normalized_rows[key]
                if row.timestamp_utc is not None and counts[row.timestamp_utc] > 1:
                    normalized_rows[key] = replace(row, time_status='duplicate')

    def _mark_overlaps(self) -> None:
        signatures: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        for key, raw in self.model.raw_observations.items():
            row = self.model.normalized_observations[key]
            if row.longitude is None or row.latitude is None or not row.original_timestamp:
                continue
            signatures[raw.source_id].add((row.longitude.hex(), row.latitude.hex(),
                                            row.original_timestamp))
        identifiers = sorted(signatures)
        overlapping: set[str] = set()
        for index, left in enumerate(identifiers):
            for right in identifiers[index + 1:]:
                common = signatures[left] & signatures[right]
                if common and (len(common) >= 2 or min(len(signatures[left]), len(signatures[right])) == 1):
                    overlapping.update((left, right))
        for identifier in overlapping:
            record = self.model.sources[identifier]
            if 'SUSPECTED_OVERLAP' not in record.warnings:
                self.model.sources[identifier] = replace(
                    record, warnings=record.warnings + ('SUSPECTED_OVERLAP',))

    def set_surveyor(self, recording_identifier: str, surveyor_id: str | None) -> None:
        self.model.set_surveyor(recording_identifier, surveyor_id)

    def validate(self) -> None:
        self.model.validate()
        accepted = {identifier for identifier, record in self.model.recordings.items()
                    if record.import_status == 'SUCCESS'}
        if accepted != set(self.native_recordings):
            raise ValueError('Accepted recording registry does not match native snapshots')
        expected_bytes = {identifier for identifier, source in self.model.sources.items()
                          if source.sha256 is not None}
        if set(self.source_bytes) != expected_bytes:
            raise ValueError('Archived source bytes do not match file Source records')
        for identifier, content in self.source_bytes.items():
            if hashlib.sha256(content).hexdigest() != self.model.sources[identifier].sha256:
                raise ValueError(f'{identifier}: archived source bytes changed')
        for recording_identifier, native in self.native_recordings.items():
            expected = Counter((row.raw_geometry_digest, row.raw_attributes_digest)
                               for row in self.model.raw_observations.values()
                               if row.recording_id == recording_identifier)
            actual = Counter((hashlib.sha256(bytes(feature.geometry().asWkb())).hexdigest(),
                              digest([_native_value(value) for value in feature.attributes()]))
                             for feature in native.observations.getFeatures())
            if expected != actual:
                raise ValueError(f'{recording_identifier}: native observation geometry or attributes changed')
            recording = self.model.recordings[recording_identifier]
            if recording.raw_track_digest:
                if native.tracks is None or layer_digest(native.tracks) != recording.raw_track_digest:
                    raise ValueError(f'{recording_identifier}: native raw track changed')
