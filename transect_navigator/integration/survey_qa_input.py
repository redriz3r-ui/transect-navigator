"""Exact Stage 4 Visit membership -> detached v0.1 analysis view.

The full immutable Stage 3 archive remains authoritative. These views neither
reingest entire recordings nor use provider FIDs as portable identities.
"""
from dataclasses import asdict
import hashlib
import json

from qgis.PyQt.QtCore import QMetaType
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsFeature, QgsField,
    QgsFields, QgsGeometry, QgsMemoryProviderUtils, QgsPointXY)

from ..analysis.observations import (MAX_OBSERVATIONS, Normalization,
                                     NormalizedObservation, Observation)
from ..survey.model import digest
from ..survey.review import Visit
from .analyze import IngestionResult, _table
from .recorded import unique_field, write_verified
from .survey_prepare import _native_value, crs_roundtrip_equivalent


def plan_layer(plan):
    """Reconstruct only a private geometry view; retain exact official WKB/CRS."""
    geometry = QgsGeometry()
    geometry.fromWkb(bytes.fromhex(plan.geometry_wkb_hex))
    if plan.geometry_is_null or geometry.isNull() or geometry.isEmpty():
        raise ValueError('Official planned geometry is missing or cannot be read')
    crs = QgsCoordinateReferenceSystem(plan.crs_wkt)
    fields = QgsFields()
    layer = QgsMemoryProviderUtils.createMemoryLayer('Official planned QA view', fields, geometry.wkbType(), crs)
    feature = QgsFeature(fields)
    feature.setGeometry(geometry)
    write_verified(layer, fields, [feature])
    if not crs_roundtrip_equivalent(crs, layer.crs()):
        raise ValueError('Official planned CRS changed in the analysis view')
    return layer


def native_lookup(intake, recording_id: str) -> dict:
    """Content identity lookup, independent of feature iteration/FID ordering."""
    layer = intake.native_recordings[recording_id].observations
    return {(hashlib.sha256(bytes(f.geometry().asWkb())).hexdigest(),
             digest([_native_value(value) for value in f.attributes()])): f
            for f in layer.getFeatures()}


def visit_input(intake, run, visit: Visit, planned, lookup: dict) -> IngestionResult:
    """Enforce exact confirmed membership and the Stage 4 chronology contract."""
    if not visit.observation_ids or len(visit.observation_ids) > MAX_OBSERVATIONS:
        raise ValueError(f'Visit must contain 1–{MAX_OBSERVATIONS:,} observations')
    if len(set(visit.observation_ids)) != len(visit.observation_ids):
        raise ValueError('Duplicate observation in Visit')
    source = intake.native_recordings[visit.recording_id].observations
    fields = QgsFields(source.fields())
    id_field = unique_field(fields, 'TN_QA_Observation_ID')
    fields.append(QgsField(id_field, QMetaType.Type.QString))
    raw_view = QgsMemoryProviderUtils.createMemoryLayer('Accepted Visit observation view', fields, source.wkbType(), source.crs())
    chronological = visit.ordering_basis == 'UTC_SOURCE_ORDER'
    features, normalized, points = [], [], []
    membership = run.membership
    for index, key in enumerate(visit.observation_ids, 1):
        disposition = membership[key]
        raw, normal = intake.model.raw_observations[key], intake.model.normalized_observations[key]
        if (disposition.assignment_status != 'ASSIGNED' or disposition.activity_role != 'SURVEY'
                or disposition.confidence_class != 'REVIEWER_CONFIRMED'
                or disposition.decision_id != visit.decision_id or disposition.transect_id != visit.transect_id
                or raw.recording_id != visit.recording_id or raw.source_id != visit.source_id
                or normal.position_status != 'valid'):
            raise ValueError('Observation is not an eligible confirmed member of this Visit')
        original = lookup[(raw.raw_geometry_digest, raw.raw_attributes_digest)]
        feature = QgsFeature(fields)
        feature.setGeometry(QgsGeometry(original.geometry()))
        feature.setAttributes(original.attributes() + [key])
        features.append(feature)
        # Effective QA times MUST stay NULL for source-order-only Visits, even if
        # this subset happens to contain valid times from an unreliable parent run.
        effective_time = normal.timestamp_utc if chronological else None
        normalized.append(NormalizedObservation(Observation(key, visit.visit_id,
            normal.longitude, normal.latitude, normal.original_timestamp, normal.accuracy_raw),
            effective_time, index if chronological else None,
            normal.time_status if chronological else 'review_chronology_unknown'))
        points.append(QgsPointXY(normal.longitude, normal.latitude))
    write_verified(raw_view, fields, features)
    raw_view.setCustomProperty('transect_navigator/provenance', json.dumps({
        'raw_source_id_field': id_field, 'planned_id': visit.transect_id,
        'survey_visit': asdict(visit), 'review_fingerprint': run.qa_input_digest,
        'view_role': 'derived accepted observation subset; full raw archive retained separately'}))
    track = None
    if chronological and len(points) >= 2:
        track = QgsMemoryProviderUtils.createMemoryLayer('Derived accepted Visit track', QgsFields(),
            Qgis.WkbType.LineString, QgsCoordinateReferenceSystem('EPSG:4326'))
        feature = QgsFeature(track.fields())
        feature.setGeometry(QgsGeometry.fromPolylineXY(points))
        write_verified(track, track.fields(), [feature])
        track.setCustomProperty('transect_navigator/track_origin', 'derived exact reviewed Visit; no cross-Visit joins')
    empty = _table('Unused ingestion view', (), [])
    ordered = ((visit.visit_id, visit.observation_ids),) if track is not None else ()
    return IngestionResult(planned, raw_view, track, empty, empty,
                           Normalization(tuple(normalized), ordered, ()), ())
