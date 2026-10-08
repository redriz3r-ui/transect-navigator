"""Current-dependency boundary and detached operational status map."""
from dataclasses import asdict
import hashlib
from pathlib import Path

from qgis.PyQt.QtCore import QMetaType
from qgis.PyQt.QtGui import QColor
from qgis.core import (QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
    QgsFeature, QgsField, QgsGeometry, QgsRendererCategory, QgsSymbol, QgsVectorLayer,
    QgsWkbTypes)

from ..survey.model import digest
from ..survey.results import aggregate_results, STATUSES
from .survey_qa import assert_batch_current
from .survey_prepare import crs_roundtrip_equivalent


def results_dependency(batch, policy, evidence_history):
    root = Path(__file__).resolve().parents[1]
    return digest({'batch': batch.as_dict(), 'policy': asdict(policy),
        'evidence': [asdict(item) for item in evidence_history],
        'rules': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                  for name in ('survey/results.py', 'integration/survey_results.py')}})


def build_results(batch, run, intake, policy, evidence_history=(), *, options=None, created_utc=None):
    """No per-point QA rerun: validate the batch then consume its existing values."""
    assert_batch_current(batch, run, intake, options if options is not None else batch.options)
    return aggregate_results(intake.survey, batch, run.membership, policy, evidence_history,
        dependency_fingerprint=results_dependency(batch, policy, evidence_history), created_utc=created_utc,
        source_count=len(intake.model.sources),
        surveyor_count=len({r.surveyor_id for r in intake.model.recordings.values() if r.surveyor_id is not None}))


def assert_results_current(result, batch, run, intake, policy, evidence_history, *, options=None):
    assert_batch_current(batch, run, intake, options if options is not None else batch.options)
    if result.dependency_fingerprint != results_dependency(batch, policy, evidence_history):
        raise ValueError('Stale Survey results: QA snapshot, completion policy or evidence review changed')


def status_layer(result, survey):
    """One native-geometry copy per official plan, including NULL/unsupported plans.

    No project registration or styling of source layers. The caller owns this copy.
    Preserve native CRS rather than performing unnecessary display transformations.
    """
    if {r.transect_id for r in result.transects} != {p.transect_id for p in survey.plans}:
        raise ValueError('Status map does not match official plans')
    # The official snapshot originates from one layer, with one CRS and WKB type.
    first_geometry = QgsGeometry()
    for plan in survey.plans:
        if plan.geometry_wkb_hex:
            first_geometry.fromWkb(bytes.fromhex(plan.geometry_wkb_hex))
            if not first_geometry.isNull():
                break
    wkb = first_geometry.wkbType() if not first_geometry.isNull() else QgsWkbTypes.Type.LineString
    layer = QgsVectorLayer(QgsWkbTypes.displayString(wkb), 'Survey operational status', 'memory')
    crs = QgsCoordinateReferenceSystem(survey.source_crs_wkt)
    layer.setCrs(crs)
    if not layer.isValid() or not crs.isValid() or not crs_roundtrip_equivalent(crs, layer.crs()):
        raise ValueError('Cannot create native CRS status map')
    columns = [('Survey_ID', str), ('Transect_ID', str), ('Status', str), ('Status_Basis', str),
        ('Planned_m', float), ('Visit_Count', int), ('Observation_Count', int), ('Progress_m', float),
        ('Corridor_m', float), ('Progress_pct', float), ('Track_m', float), ('Unresolved_Count', int),
        ('QA_State', str), ('Policy_ID', str), ('Source_IDs', str), ('Surveyor_IDs', str)]
    kinds = {str: QMetaType.Type.QString, float: QMetaType.Type.Double, int: QMetaType.Type.Int}
    provider = layer.dataProvider()
    if not provider.addAttributes([QgsField(name, kinds[kind]) for name, kind in columns]):
        raise ValueError('Cannot create status attributes')
    layer.updateFields()
    plans = {p.transect_id: p for p in survey.plans}
    features = []
    for row in result.transects:
        plan, qa = plans[row.transect_id], row.qa
        feature = QgsFeature(layer.fields())
        if not plan.geometry_is_null:
            geometry = QgsGeometry()
            geometry.fromWkb(bytes.fromhex(plan.geometry_wkb_hex))
            feature.setGeometry(geometry)
        feature.setAttributes([survey.survey_id, row.transect_id, row.final_status, '; '.join(row.status_basis),
            qa.planned_length_m, len(qa.visit_ids), qa.accepted_observation_count, row.confirmed_progress_m,
            row.confirmed_corridor_m, qa.progress_support.supported_percent if qa.progress_support else None,
            qa.summary.recorded_length_m if qa.summary else None, len(row.unresolved_observation_ids),
            row.qa_state, row.completion_policy_id, ', '.join(qa.source_ids),
            ', '.join(s or 'UNKNOWN' for s in qa.surveyor_ids)])
        features.append(feature)
    if not provider.addFeatures(features)[0] or layer.featureCount() != len(result.transects):
        raise ValueError('Status map could not preserve every plan')
    layer.updateExtents()
    colors = ('#2166ac', '#e6ab02', '#b2188b', '#8a8a8a')
    categories = []
    for state, color in zip(STATUSES, colors):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(color))
        categories.append(QgsRendererCategory(state, symbol, state + ' (operational)'))
    layer.setRenderer(QgsCategorizedSymbolRenderer('Status', categories))
    layer.setCustomProperty('transect_navigator/results_dependency', result.dependency_fingerprint)
    layer.setCustomProperty('transect_navigator/interpretation', 'Operational workflow status; not compliance')
    layer.setReadOnly(True)
    return layer
