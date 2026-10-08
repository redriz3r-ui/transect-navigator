"""Visit-scoped orchestration around unchanged v0.1 comparison/statistics/events."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from qgis.core import Qgis, QgsCsException, QgsProjUtils

from .. import __version__
from ..survey.intake import portable_id
from ..survey.model import canonical_json, digest, utc_time
from ..survey.qa import (QA_SCHEMA, BatchQa, QaEvent, QaOptions, QaSample, VisitQa,
                         aggregate_transect, progress_support, summary_rows)
from ..survey.review import ReviewRun
from .comparison import compare_ingested, _working_path
from .events import detect_comparison_events
from .statistics import summarize_comparison
from .survey_prepare import _PlanView
from .survey_review import review_distance, validate_review_intake
from .survey_qa_input import native_lookup, plan_layer, visit_input


def engine_fingerprint() -> str:
    """Hash calculation/adaptation/identity code, not UI or packaging metadata."""
    root = Path(__file__).resolve().parents[1]
    modules = ('qaqc/deviation.py', 'qaqc/statistics.py', 'qaqc/events.py',
        'geometry/planar.py', 'analysis/observations.py', 'integration/comparison.py',
        'integration/analyze.py', 'integration/geodesy.py', 'integration/crs.py',
        'integration/statistics.py', 'integration/events.py', 'integration/recorded.py',
        'integration/survey_prepare.py', 'integration/survey_review.py',
        'integration/survey_qa_input.py', 'integration/survey_qa.py',
        'survey/qa.py', 'survey/review.py', 'survey/model.py')
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in modules})


def dependencies(run: ReviewRun, intake, options: QaOptions) -> dict:
    validate_review_intake(intake)
    run.assert_current()
    # Reject mismatched caller objects and inconsistent/corrupted in-memory review.
    ReviewRun.restore(run.snapshot(), intake.survey, intake.model, review_distance(intake))
    operations = intake.project.transformContext().coordinateOperations()
    context = {'qgis': Qgis.QGIS_VERSION,
        'proj': [QgsProjUtils.projVersionMajor(), QgsProjUtils.projVersionMinor()],
        'coordinate_operations': sorted([list(key) + [value] for key, value in operations.items()])}
    return {'review': run.qa_input_digest, 'engine': engine_fingerprint(),
            'context': canonical_json(context), 'options': asdict(options)}


def assert_batch_current(batch: BatchQa, run: ReviewRun, intake, options: QaOptions) -> None:
    if batch.schema != QA_SCHEMA or digest(dependencies(run, intake, options)) != batch.dependency_fingerprint:
        raise ValueError('Stale QA: review, source, plan, QA options, engine or transform context changed; rerun QA')


def _analyze_visit(intake, run, visit, planned, lookup, options, dependency) -> VisitQa:
    ingested = visit_input(intake, run, visit, planned, lookup)
    compared = compare_ingested(ingested, project=intake.project)
    summary = summarize_comparison(ingested, compared, options=options.statistics, project=intake.project).summary
    detected = detect_comparison_events(ingested, compared, options=options.events, project=intake.project)
    metadata = json.loads(compared.observations.customProperty('transect_navigator/provenance'))
    samples = []
    for key, normalized, deviation in zip(visit.observation_ids, ingested.ordering.observations, compared.rows):
        raw, normal = intake.model.raw_observations[key], intake.model.normalized_observations[key]
        original = lookup[(raw.raw_geometry_digest, raw.raw_attributes_digest)]
        samples.append(QaSample(key, raw.original_observation_key, raw.source_order,
            normal.original_timestamp, normal.timestamp_utc, normalized.timestamp_utc, normal.accuracy_raw,
            normal.longitude, normal.latitude, bytes(original.geometry().asWkb()).hex(), deviation))
    geometry = {f['Event_ID']: None if f.geometry().isNull() else bytes(f.geometry().asWkb()).hex()
                for f in detected.layer.getFeatures()}
    events = tuple(QaEvent(portable_id('QAE', [dependency, visit.visit_id, e.event_id]), visit.visit_id,
                          e, geometry[e.event_id]) for e in detected.detection.events)
    flags = []
    if visit.ordering_basis != 'UTC_SOURCE_ORDER':
        flags.append('UNKNOWN_CHRONOLOGY_POINT_QA_ONLY')
    if len(samples) == 1:
        flags.append('SINGLE_OBSERVATION_NO_TRACK')
    result = VisitQa(visit, 'SUCCESS', '', tuple(samples), summary, None, events, tuple(flags),
        ingested.raw_observations.crs().toWkt(), metadata['analysis_crs_wkt'],
        metadata['analysis_coordinate_operation'])
    from dataclasses import replace
    return replace(result, progress_support=progress_support(summary_rows((result,)),
                    summary.planned_length_m, options.statistics))


def run_batch_qa(run: ReviewRun, intake, options: QaOptions = QaOptions(), *,
                 created_utc: str | None = None) -> BatchQa:
    """Independent Visit transactions; failures remain visible, never filtered out.

    Plans with no Visits also receive a modeled length/explicit model error. No
    operational completion policy or project coverage/status is calculated here.
    """
    dependency = dependencies(run, intake, options)
    fingerprint = digest(dependency)
    results, summaries = [], []
    visits = run.visits
    for plan in sorted(intake.survey.plans, key=lambda p: p.transect_id or ''):
        members = tuple(v for v in visits if v.transect_id == plan.transect_id)
        planned, length, plan_error = None, None, ''
        try:
            if plan.qa_model_status != 'SUPPORTED':
                raise ValueError(plan.qa_model_reason or 'Official plan is unsupported by the QA model')
            planned = plan_layer(plan)
            path, _, _, _ = _working_path(_PlanView(planned), intake.project)
            length = path.total_length_m
        except (ValueError, QgsCsException, RuntimeError) as error:
            plan_error = str(error)
        plan_results = []
        for visit in members:
            try:
                if plan_error:
                    raise ValueError(plan_error)
                lookup = native_lookup(intake, visit.recording_id)
                result = _analyze_visit(intake, run, visit, planned, lookup, options, fingerprint)
            except (ValueError, KeyError, QgsCsException, RuntimeError) as error:
                result = VisitQa(visit, 'FAILED', f'{type(error).__name__}: {error}')
            plan_results.append(result)
        results.extend(plan_results)
        summaries.append(aggregate_transect(plan.transect_id, length, plan.navigation_length_m,
                                           tuple(plan_results), options.statistics, plan_error))
    if {v.visit.visit_id for v in results} != {v.visit_id for v in visits}:
        raise ValueError('A confirmed Visit has no official planned transect')
    counts = Counter(d.assignment_status for d in run.membership.values())
    counts['TRANSITION'] = sum(d.activity_role == 'TRANSITION' for d in run.membership.values())
    # There is no silent cache reuse; all outputs are derived in this call.
    return BatchQa(QA_SCHEMA, portable_id('BQA', fingerprint), intake.survey.survey_id,
        run.run_id, len(run.history), dependency['review'], dependency['engine'], dependency['context'],
        options, fingerprint, utc_time(created_utc), __version__, tuple(results), tuple(summaries),
        tuple(sorted(counts.items())))
