"""QGIS distance boundary and verified, portable review persistence."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from qgis.core import QgsCoordinateReferenceSystem, QgsDistanceArea, QgsPointXY

from ..io.package import publish_package
from ..survey.model import canonical_json
from ..survey.review import ContinuityPolicy, ReviewRun
from .survey_intake import SurveyIntake
from .survey_intake_storage import load_survey_intake, save_survey_intake
from .survey_prepare import crs_roundtrip_equivalent


def validate_review_intake(intake: SurveyIntake) -> None:
    """Protect the review coordinate frame as well as native attribute/geometry bytes."""
    intake.validate()
    for key, native in intake.native_recordings.items():
        record = intake.model.recordings[key]
        if not crs_roundtrip_equivalent(native.observations.crs(), QgsCoordinateReferenceSystem(record.original_crs_wkt)):
            raise ValueError('Native observation CRS changed; review requires the original intake snapshot')
        expected = [field[0] for field in json.loads(record.original_schema_json)['fields']]
        if native.observations.fields().names() != expected:
            raise ValueError('Native observation schema changed; review requires the original intake snapshot')


def review_distance(intake: SurveyIntake):
    """Use ellipsoidal WGS84 metres for normalized working points only."""
    measure = QgsDistanceArea()
    measure.setSourceCrs(QgsCoordinateReferenceSystem('EPSG:4326'), intake.project.transformContext())
    if not measure.setEllipsoid('WGS84'):
        raise ValueError('Cannot initialize WGS84 continuity distance')

    def distance(first: str, second: str) -> float:
        a, b = (intake.model.normalized_observations[key] for key in (first, second))
        return measure.measureLine(QgsPointXY(a.longitude, a.latitude), QgsPointXY(b.longitude, b.latitude))
    return distance


def start_review(intake: SurveyIntake, policy: ContinuityPolicy, *, created_utc: str | None = None) -> ReviewRun:
    validate_review_intake(intake)
    return ReviewRun(intake.survey, intake.model, policy, review_distance(intake), created_utc=created_utc)


def _read_state(path: Path) -> dict:
    """Read only, and verify relational exact membership against typed records."""
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        state = json.loads(connection.execute('SELECT json FROM review_state WHERE id=1').fetchone()[0])
        for name, key in (('decisions', 'decision_id'), ('membership', 'observation_id'),
                          ('segments', 'segment_id'), ('visits', 'visit_id')):
            rows = dict(connection.execute(f'SELECT id,json FROM {name}'))
            if rows != {row[key]: canonical_json(row) for row in state[name]}:
                raise ValueError(f'Review {name} table differs from saved state')
        for name, key in (('decisions', 'decision_id'), ('segments', 'segment_id'), ('visits', 'visit_id')):
            expected = {(row[key], i, obs) for row in state[name]
                        for i, obs in enumerate(row['observation_ids'])}
            actual = set(connection.execute(f'SELECT owner_id,ordinal,observation_id FROM {name}_membership'))
            if actual != expected:
                raise ValueError(f'Review {name} exact membership failed verification')
        return state


def save_review(run: ReviewRun, intake: SurveyIntake, parent: Path, name: str) -> Path:
    """Publish a new revision snapshot; never update or overwrite prior packages.

    SQLite is a standard relational review store, not an analysis export. The nested
    Stage 3 package retains every raw source and native snapshot independently.
    """
    validate_review_intake(intake)
    state = run.snapshot()
    # Catch a caller accidentally pairing a run with a different intake object.
    ReviewRun.restore(state, intake.survey, intake.model, review_distance(intake))

    def write(stage: Path) -> None:
        save_survey_intake(intake, stage, 'intake')
        with sqlite3.connect(stage / 'review.sqlite') as connection:
            connection.execute('CREATE TABLE review_state(id INTEGER PRIMARY KEY, json TEXT NOT NULL)')
            connection.execute('INSERT INTO review_state VALUES(1,?)', (canonical_json(state),))
            connection.execute('CREATE TABLE planned_snapshot(id INTEGER PRIMARY KEY, json TEXT NOT NULL)')
            connection.execute('INSERT INTO planned_snapshot VALUES(1,?)', (canonical_json(asdict(intake.survey)),))
            for table, key in (('decisions', 'decision_id'), ('membership', 'observation_id'),
                               ('segments', 'segment_id'), ('visits', 'visit_id')):
                connection.execute(f'CREATE TABLE {table}(id TEXT PRIMARY KEY,json TEXT NOT NULL)')
                connection.executemany(f'INSERT INTO {table} VALUES(?,?)',
                                      [(row[key], canonical_json(row)) for row in state[table]])
            for table, key in (('decisions', 'decision_id'), ('segments', 'segment_id'), ('visits', 'visit_id')):
                connection.execute(f'CREATE TABLE {table}_membership('
                                   'owner_id TEXT NOT NULL,ordinal INTEGER NOT NULL,observation_id TEXT NOT NULL,'
                                   'PRIMARY KEY(owner_id,ordinal),UNIQUE(owner_id,observation_id))')
                connection.executemany(f'INSERT INTO {table}_membership VALUES(?,?,?)',
                    [(row[key], index, obs) for row in state[table] for index, obs in enumerate(row['observation_ids'])])
        restored = load_review(stage, intake)
        if canonical_json(restored.snapshot()) != canonical_json(state):
            raise ValueError('Review did not survive persistence')
        (stage / 'README.txt').write_text(
            'Transect Navigator reviewed assignment snapshot\n'
            f'Survey: {intake.survey.survey_id}\nRun: {run.run_id}\nRevision: {len(run.history)}\n'
            'review.sqlite: plan snapshot, complete decision history, exact membership, segments and Visits.\n'
            'intake/: verified immutable input snapshots and available original source bytes.\n'
            'This is human review, not automatic matching or QA. SOURCE_ORDER_ONLY is not proven chronology.\n'
            'Restore requires matching Survey, plan and intake identities. Save edits to a new folder.\n', encoding='utf-8')
    return publish_package(parent, name, write)


def load_review(folder: Path, intake: SurveyIntake) -> ReviewRun:
    """Refuse stale inputs, corrupted archive, or edited history on resume."""
    validate_review_intake(intake)
    path = folder / 'review.sqlite'
    try:
        state = _read_state(path)
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
            plan = connection.execute('SELECT json FROM planned_snapshot WHERE id=1').fetchone()[0]
        if plan != canonical_json(asdict(intake.survey)):
            raise ValueError('Stale review: planned snapshot differs')
        archived = load_survey_intake(folder / 'intake', intake.survey, project=intake.project)
        ReviewRun.restore(state, archived.survey, archived.model, review_distance(archived))
        return ReviewRun.restore(state, intake.survey, intake.model, review_distance(intake))
    except (sqlite3.Error, KeyError, TypeError, IndexError, json.JSONDecodeError) as error:
        raise ValueError(f'Invalid review snapshot: {error}') from error
