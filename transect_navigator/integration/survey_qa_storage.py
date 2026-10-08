"""Verified additive QA snapshots, with reviewed/raw input archive and no overwrite."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import sqlite3
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from ..io.package import publish_package
from ..survey.model import canonical_json, digest
from ..survey.qa import BatchQa
from .survey_qa import assert_batch_current
from .survey_review import save_review, load_review


def _decode(kind, value):
    """Decode only this module's declared result types; never import serialized code."""
    origin, args = get_origin(kind), get_args(kind)
    if origin is UnionType:
        if value is None and type(None) in args:
            return None
        return _decode(next(arg for arg in args if arg is not type(None)), value)
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError('Invalid QA tuple representation')
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item) for item in value)
        if len(args) != len(value):
            raise ValueError('Invalid QA tuple length')
        return tuple(_decode(k, v) for k, v in zip(args, value))
    if is_dataclass(kind):
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(kind)}:
            raise ValueError('Invalid QA record fields')
        types = get_type_hints(kind)
        return kind(**{key: _decode(types[key], item) for key, item in value.items()})
    if kind is float and type(value) in (int, float):
        return value
    if type(value) is not kind:
        raise ValueError('Invalid QA scalar type')
    return value


def _tables(data: dict) -> dict[str, list[tuple[str, str]]]:
    return {
        'visit_qa': [(v['visit']['visit_id'], canonical_json(v)) for v in data['visits']],
        'transect_qa': [(t['transect_id'], canonical_json(t)) for t in data['transects']],
        'qa_observations': [(s['observation_id'], canonical_json({'visit_id': v['visit']['visit_id'], **s}))
                            for v in data['visits'] for s in v['samples']],
        'qa_events': [(e['event_uid'], canonical_json(e)) for v in data['visits'] for e in v['events']],
    }


def _validate_links(batch, run) -> None:
    visits = {v.visit_id: v for v in run.visits}
    if len(batch.visits) != len(visits) or {v.visit.visit_id for v in batch.visits} != set(visits):
        raise ValueError('QA Visit membership no longer matches review')
    for result in batch.visits:
        if result.visit != visits[result.visit.visit_id]:
            raise ValueError('QA Visit provenance differs from review')
        if result.processing_state not in ('SUCCESS', 'FAILED'):
            raise ValueError('Invalid Visit processing state')
        if result.processing_state == 'SUCCESS':
            if tuple(s.observation_id for s in result.samples) != result.visit.observation_ids:
                raise ValueError('QA sample membership differs from Visit')
            if result.summary is None or result.summary.observation_count != len(result.samples):
                raise ValueError('QA sample count differs from summary')
        elif result.samples or not result.error:
            raise ValueError('Failed Visit must retain error without partial sample output')
        for event in result.events:
            if event.visit_id != result.visit.visit_id or not set(event.result.source_ids) <= set(result.visit.observation_ids):
                raise ValueError('QA event references another Visit')


def save_batch_qa(batch: BatchQa, run, intake, parent: Path, name: str, *, options=None) -> Path:
    """Save current results only; old packages and raw source bytes remain immutable."""
    assert_batch_current(batch, run, intake, options if options is not None else batch.options)
    _validate_links(batch, run)
    data = batch.as_dict()

    def write(stage: Path) -> None:
        save_review(run, intake, stage, 'review')
        with sqlite3.connect(stage / 'qa.sqlite') as connection:
            connection.execute('CREATE TABLE analysis_run(id INTEGER PRIMARY KEY,json TEXT NOT NULL,sha256 TEXT NOT NULL)')
            connection.execute('INSERT INTO analysis_run VALUES(1,?,?)', (canonical_json(data), digest(data)))
            for name, rows in _tables(data).items():
                connection.execute(f'CREATE TABLE {name}(id TEXT PRIMARY KEY,json TEXT NOT NULL)')
                connection.executemany(f'INSERT INTO {name} VALUES(?,?)', rows)
        restored = load_batch_qa(stage, run, intake, batch.options)
        if canonical_json(restored.as_dict()) != canonical_json(data):
            raise ValueError('QA snapshot failed exact round-trip verification')
        (stage / 'README.txt').write_text(
            f'Transect Navigator reviewed Visit QA snapshot\nSurvey: {batch.survey_id}\n'
            f'Batch: {batch.batch_id}\nReview revision: {batch.review_revision}\n'
            'qa.sqlite contains typed Visit/transect summaries, samples, events and dependency identity.\n'
            'review/ contains the exact decisions, planned snapshot and authoritative raw intake archive.\n'
            'This is internal QA persistence, not the completed Survey export or a completion assessment.\n'
            'Event WKB is in its Visit analysis CRS; NULL means stationary/no path.\n'
            'Thresholds are user QA settings, not scientific or regulatory standards.\n', encoding='utf-8')
    try:
        return publish_package(parent, name, write)
    except sqlite3.Error as error:
        raise ValueError(f'QA snapshot database write failed: {error}') from error


def load_batch_qa(folder: Path, run, intake, options) -> BatchQa:
    """Refuse stale dependencies before allowing saved results to be used."""
    try:
        with sqlite3.connect((folder / 'qa.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as connection:
            text, checksum = connection.execute('SELECT json,sha256 FROM analysis_run WHERE id=1').fetchone()
            data = json.loads(text)
            if digest(data) != checksum:
                raise ValueError('QA payload checksum mismatch')
            for name, expected in _tables(data).items():
                actual = dict(connection.execute(f'SELECT id,json FROM {name}'))
                if actual != dict(expected):
                    raise ValueError(f'{name}: QA records differ from snapshot')
        batch = _decode(BatchQa, data)
        assert_batch_current(batch, run, intake, options)
        archived = load_review(folder / 'review', intake)
        if archived.qa_input_digest != run.qa_input_digest:
            raise ValueError('Saved review differs from current QA dependency')
        _validate_links(batch, run)
        return batch
    except (sqlite3.Error, KeyError, TypeError, IndexError, json.JSONDecodeError) as error:
        raise ValueError(f'Invalid QA snapshot: {error}') from error
