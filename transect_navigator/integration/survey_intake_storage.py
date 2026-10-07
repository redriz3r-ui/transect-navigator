"""Verified standard GeoPackage persistence for Stage 3 Survey intake state."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, fields
import hashlib
import json
from pathlib import Path
import sqlite3

from qgis.PyQt.QtCore import QMetaType
from qgis.core import QgsProject, QgsVectorLayer

from .analyze import _table
from .export import write_geopackage_layers
from .recorded import snapshot
from .survey_intake import NativeRecordingSnapshot, SurveyIntake
from .survey_prepare import crs_roundtrip_equivalent
from ..io.package import package_destination, publish_package
from ..survey.intake import (FieldMappings, ImportAttempt, NormalizedObservationRecord,
                             RawObservationRecord, RecordingRecord, SourceRecord,
                             INTAKE_SCHEMA_VERSION, OBSERVATION_ID_VERSION,
                             RECORDING_ID_VERSION, SOURCE_ID_VERSION)
from ..survey.model import Survey, canonical_json


def _records_table(name: str, record_type: type, records: list[object]) -> QgsVectorLayer:
    names = [field.name for field in fields(record_type)]
    rows = []
    for record in records:
        values = asdict(record)
        rows.append([value if isinstance(value, str) else canonical_json(value)
                     for value in (values[name] for name in names)])
    return _table(name, tuple((field, QMetaType.Type.QString) for field in names), rows)


def intake_manifest(intake: SurveyIntake) -> dict:
    intake.validate()
    model = intake.model
    manifest = {
        'schema_version': INTAKE_SCHEMA_VERSION,
        'identity_versions': {
            'source': SOURCE_ID_VERSION,
            'recording': RECORDING_ID_VERSION,
            'observation': OBSERVATION_ID_VERSION,
        },
        'survey': {
            'Survey_ID': intake.survey.survey_id,
            'plan_snapshot_digest': intake.survey.plan_snapshot_digest,
            'plan_revision': intake.survey.plan_revision,
        },
        'sources': [asdict(row) for row in model.sources.values()],
        'recordings': [asdict(row) for row in model.recordings.values()],
        'raw_observations': [asdict(row) for row in model.raw_observations.values()],
        'normalized_observations': [asdict(row) for row in model.normalized_observations.values()],
        'import_attempts': [asdict(row) for row in model.attempts],
        'totals': asdict(model.totals()),
    }
    # Normalize tuples to JSON arrays before verification so the in-memory value is
    # exactly the portable representation that will be reopened.
    return json.loads(canonical_json(manifest))


def _metadata_layers(manifest: dict) -> list[tuple[str, QgsVectorLayer]]:
    def records(key: str, kind: type) -> list[object]:
        result = []
        for row in manifest[key]:
            if kind is RecordingRecord:
                row = dict(row)
                row['mappings'] = FieldMappings(**row['mappings'])
                row['structural_segments'] = tuple(row['structural_segments'])
            elif kind is SourceRecord:
                row = dict(row)
                row['warnings'] = tuple(row['warnings'])
            result.append(kind(**row))
        return result
    return [
        ('sources', _records_table('sources', SourceRecord, records('sources', SourceRecord))),
        ('recordings', _records_table('recordings', RecordingRecord,
                                      records('recordings', RecordingRecord))),
        ('raw_observation_index', _records_table(
            'raw_observation_index', RawObservationRecord,
            records('raw_observations', RawObservationRecord))),
        ('normalized_observations', _records_table(
            'normalized_observations', NormalizedObservationRecord,
            records('normalized_observations', NormalizedObservationRecord))),
        ('import_attempts', _records_table('import_attempts', ImportAttempt,
                                           records('import_attempts', ImportAttempt))),
    ]


def save_survey_intake(intake: SurveyIntake, parent: Path, name: str) -> Path:
    """Publish a self-contained intake snapshot after complete round-trip checks."""
    package_destination(parent, name)
    manifest = intake_manifest(intake)
    layers = _metadata_layers(manifest)
    for recording_id, native in sorted(intake.native_recordings.items()):
        record = intake.model.recordings[recording_id]
        layers.append((record.raw_observation_layer, native.observations))
        if native.tracks is not None:
            layers.append((record.raw_track_layer, native.tracks))
    if len({name.casefold() for name, _ in layers}) != len(layers):
        raise ValueError('Intake layer names collide; state was not saved')

    def write(stage: Path) -> None:
        sources = stage / 'sources'
        sources.mkdir()
        for source_identifier, content in intake.source_bytes.items():
            record = intake.model.sources[source_identifier]
            if not record.sha256 or hashlib.sha256(content).hexdigest() != record.sha256:
                raise ValueError(f'{record.source_file}: archived bytes no longer match Source_ID')
            destination = stage / record.archived_reference
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        write_geopackage_layers(stage / 'survey_intake.gpkg', layers,
                                intake.project.transformContext(),
                                crs_equivalent=crs_roundtrip_equivalent)
        manifest_path = stage / 'survey_intake_manifest.json'
        manifest_path.write_text(canonical_json(manifest) + '\n', encoding='utf-8')
        _validate_saved(stage, manifest)
        (stage / 'README.txt').write_text(
            'Transect Navigator v0.2 Survey recording intake snapshot\n\n'
            f"Survey_ID: {intake.survey.survey_id}\n"
            f"Sources: {manifest['totals']['source_count']}\n"
            f"Recordings: {manifest['totals']['recording_count']}\n"
            f"Observations: {manifest['totals']['observation_count']}\n"
            f"Intake state: {manifest['totals']['state']}\n\n"
            'survey_intake.gpkg contains the provenance registries and independent native\n'
            'raw observation/track snapshots. sources/ contains exact original bytes when\n'
            'a file was imported. Loaded QGIS layers are identified honestly as snapshots.\n'
            'survey_intake_manifest.json is the typed portable manifest. Raw observations\n'
            'are authoritative; normalized observations are derived working records.\n'
            'No observation-to-transect assignment or QA analysis is present in Stage 3.\n',
            encoding='utf-8')
    try:
        return publish_package(parent, name, write)
    except (OSError, sqlite3.Error, json.JSONDecodeError) as error:
        raise ValueError(f'Survey intake save failed: {error}; existing packages were not overwritten') from error


def _validate_saved(folder: Path, manifest: dict) -> None:
    parsed = json.loads((folder / 'survey_intake_manifest.json').read_text(encoding='utf-8'))
    if parsed != manifest:
        raise ValueError('Survey intake manifest failed exact round-trip verification')
    path = folder / 'survey_intake.gpkg'
    for name, expected_layer in _metadata_layers(manifest):
        layer = QgsVectorLayer(f'{path}|layername={name}', name, 'ogr')
        names = expected_layer.fields().names()
        if not layer.isValid() or layer.featureCount() != expected_layer.featureCount():
            raise ValueError(f'{name}: persisted count verification failed')
        if not set(names) <= set(layer.fields().names()):
            raise ValueError(f'{name}: persisted fields verification failed')
        expected = Counter(tuple(repr(feature[field]) for field in names)
                           for feature in expected_layer.getFeatures())
        actual = Counter(tuple(repr(feature[field]) for field in names)
                         for feature in layer.getFeatures())
        if expected != actual:
            raise ValueError(f'{name}: persisted value verification failed')
    for row in manifest['sources']:
        if row['sha256']:
            archived = _archived_path(folder, row['archived_reference'])
            if not archived.is_file() or hashlib.sha256(archived.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError(f"{row['source_file']}: archived source hash verification failed")


def _archived_path(folder: Path, reference: str) -> Path:
    """Confine manifest references to the movable package directory."""
    relative = Path(reference)
    if relative.is_absolute():
        raise ValueError('Archived source reference must be package-relative')
    root = folder.resolve()
    path = (folder / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError('Archived source reference escapes the intake package')
    return path


def load_survey_intake(folder: Path, survey: Survey, *,
                       project: QgsProject | None = None) -> SurveyIntake:
    """Reopen verified state without treating exported FIDs as portable identity."""
    folder = Path(folder)
    manifest = json.loads((folder / 'survey_intake_manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != INTAKE_SCHEMA_VERSION:
        raise ValueError('Unsupported Survey intake schema version')
    if manifest.get('identity_versions') != {
            'source': SOURCE_ID_VERSION, 'recording': RECORDING_ID_VERSION,
            'observation': OBSERVATION_ID_VERSION}:
        raise ValueError('Unsupported Survey intake identity version')
    if (manifest['survey']['Survey_ID'] != survey.survey_id
            or manifest['survey']['plan_snapshot_digest'] != survey.plan_snapshot_digest):
        raise ValueError('Intake snapshot belongs to a different Survey or plan snapshot')
    _validate_saved(folder, manifest)
    intake = SurveyIntake(survey, project=project)
    for row in manifest['sources']:
        value = dict(row)
        value['warnings'] = tuple(value['warnings'])
        record = SourceRecord(**value)
        intake.model.sources[record.source_id] = record
        if record.sha256:
            intake.source_bytes[record.source_id] = _archived_path(
                folder, record.archived_reference).read_bytes()
    for row in manifest['recordings']:
        value = dict(row)
        value['mappings'] = FieldMappings(**value['mappings'])
        value['structural_segments'] = tuple(value['structural_segments'])
        record = RecordingRecord(**value)
        intake.model.recordings[record.recording_id] = record
    for row in manifest['raw_observations']:
        record = RawObservationRecord(**row)
        intake.model.raw_observations[record.observation_id] = record
    for row in manifest['normalized_observations']:
        record = NormalizedObservationRecord(**row)
        intake.model.normalized_observations[record.observation_id] = record
    intake.model.attempts.extend(ImportAttempt(**row) for row in manifest['import_attempts'])
    gpkg = folder / 'survey_intake.gpkg'
    for record in intake.model.recordings.values():
        if record.import_status != 'SUCCESS':
            continue
        observations = QgsVectorLayer(
            f'{gpkg}|layername={record.raw_observation_layer}',
            record.raw_observation_layer, 'ogr')
        if not observations.isValid():
            raise ValueError(f'{record.recording_id}: raw observations could not be reopened')
        observations = snapshot(observations, record.raw_observation_layer)
        expected_fields = [row[0] for row in json.loads(record.original_schema_json)['fields']]
        extras = [index for index, name in enumerate(observations.fields().names())
                  if name not in expected_fields]
        if extras and not observations.dataProvider().deleteAttributes(extras):
            raise ValueError(f'{record.recording_id}: provider row ID could not be removed')
        observations.updateFields()
        if observations.fields().names() != expected_fields:
            raise ValueError(f'{record.recording_id}: raw observation schema changed on reopen')
        tracks = None
        if record.raw_track_layer:
            tracks = QgsVectorLayer(f'{gpkg}|layername={record.raw_track_layer}',
                                    record.raw_track_layer, 'ogr')
            if not tracks.isValid():
                raise ValueError(f'{record.recording_id}: raw tracks could not be reopened')
            tracks = snapshot(tracks, record.raw_track_layer)
            expected_track_fields = [row[0] for row in
                                     json.loads(record.raw_track_schema_json)['fields']]
            extras = [index for index, name in enumerate(tracks.fields().names())
                      if name not in expected_track_fields]
            if extras and not tracks.dataProvider().deleteAttributes(extras):
                raise ValueError(f'{record.recording_id}: provider track row ID could not be removed')
            tracks.updateFields()
            if tracks.fields().names() != expected_track_fields:
                raise ValueError(f'{record.recording_id}: raw track schema changed on reopen')
        intake.native_recordings[record.recording_id] = NativeRecordingSnapshot(
            record.recording_id, observations, tracks)
    intake.validate()
    if asdict(intake.model.totals()) != manifest['totals']:
        raise ValueError('Reopened intake totals differ from the saved snapshot')
    return intake
