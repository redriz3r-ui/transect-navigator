"""Internal resumable results snapshot, not the Stage 7 Survey export."""
import json
from pathlib import Path
import sqlite3

from ..io.package import publish_package
from ..survey.model import canonical_json, digest
from ..survey.results import SurveyResults
from .survey_qa_storage import _decode, load_batch_qa, save_batch_qa
from .survey_results import assert_results_current, build_results


def save_results(result, batch, run, intake, parent: Path, name: str):
    assert_results_current(result, batch, run, intake, result.policy, result.evidence_history)

    def write(stage):
        save_batch_qa(batch, run, intake, stage, 'qa')
        data = result.as_dict()
        with sqlite3.connect(stage / 'results.sqlite') as db:
            db.execute('CREATE TABLE results(id INTEGER PRIMARY KEY, json TEXT NOT NULL, sha256 TEXT NOT NULL)')
            db.execute('INSERT INTO results VALUES(1,?,?)', (canonical_json(data), digest(data)))
        restored, _ = load_results(stage, run, intake, batch.options)
        if restored != result:
            raise ValueError('Survey results round trip differs')
        (stage / 'README.txt').write_text('Internal Survey results snapshot. results.sqlite preserves the explicit '
            'completion policy and append-only relevance review. qa/ preserves Stage 5 QA and reviewed raw data.\n'
            'Operational statuses are not scientific or regulatory determinations.\n', encoding='utf-8')
    return publish_package(parent, name, write)


def load_results(folder: Path, run, intake, options):
    """Verify inputs and recalculate ONLY operational aggregation, never point QA."""
    try:
        with sqlite3.connect((folder / 'results.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT json,sha256 FROM results WHERE id=1').fetchone()
            data = json.loads(row[0])
            if digest(data) != row[1]:
                raise ValueError('Survey results checksum mismatch')
        saved = _decode(SurveyResults, data)
        batch = load_batch_qa(folder / 'qa', run, intake, options)
        actual = build_results(batch, run, intake, saved.policy, saved.evidence_history, created_utc=saved.created_utc)
        if actual != saved:
            raise ValueError('Survey results do not reproduce from archived QA/policy/review')
        return actual, batch
    except (sqlite3.Error, TypeError, KeyError, IndexError, json.JSONDecodeError) as error:
        raise ValueError(f'Invalid Survey results snapshot: {error}') from error
