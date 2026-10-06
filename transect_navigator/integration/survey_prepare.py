"""Native survey snapshot/preflight and additive orchestration of batch PREPARE."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from qgis.PyQt.QtCore import QByteArray, QDate, QDateTime, QTime, Qt
from qgis.core import (NULL, QgsCoordinateReferenceSystem, QgsCsException, QgsFeature,
                       QgsProject, QgsVectorLayer)

from .comparison import _working_path
from .prepare import PreparedNavigation, prepare_navigation
from .recorded import snapshot
from ..navigation.identifiers import transect_identifier
from ..survey.model import (Survey, PlannedTransect, SurveyRegistry, SCHEMA_VERSION,
                            canonical_json, digest, required_text, utc_time)


@dataclass(frozen=True)
class SurveySetup:
    survey: Survey
    source: QgsVectorLayer


@dataclass(frozen=True)
class SurveyNavigation:
    setup: SurveySetup
    prepared: PreparedNavigation
    exclusions: tuple[tuple[str, str], ...]
    spacing_m: float
    reverse: bool
    source_digest: str
    waypoint_digest: str


class SurveyPreflightError(ValueError):
    """Invalid identity blocks registration but retains every plan's diagnostics."""

    def __init__(self, message: str, plans: tuple[PlannedTransect, ...] = ()) -> None:
        super().__init__(message)
        self.plans = plans


def _native_value(value: object) -> object:
    """Encode exact supported attribute types for deterministic snapshot identity."""
    if value is None or value == NULL:
        return ['null']
    if isinstance(value, bool):
        return ['bool', value]
    if isinstance(value, int):
        return ['int', str(value)]
    if isinstance(value, float):
        return ['float', value.hex()]
    if isinstance(value, str):
        return ['text', value]
    if isinstance(value, (bytes, QByteArray)):
        return ['bytes', bytes(value).hex()]
    if isinstance(value, QDateTime):
        return ['datetime', value.toString(Qt.DateFormat.ISODateWithMs),
                value.timeSpec().name, bytes(value.timeZone().id()).hex()]
    if isinstance(value, QDate):
        return ['date', value.toString(Qt.DateFormat.ISODate)]
    if isinstance(value, QTime):
        return ['time', value.toString(Qt.DateFormat.ISODateWithMs)]
    if isinstance(value, (tuple, list)):
        return ['list', [_native_value(v) for v in value]]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return ['map', {k: _native_value(v) for k, v in value.items()}]
    raise ValueError(f'Unsupported native attribute type for survey fingerprint: {type(value).__name__}')


def _schema(layer: QgsVectorLayer) -> list:
    return [[f.name(), int(f.type()), int(f.subType()), f.length(), f.precision()]
            for f in layer.fields()]


def _feature_content(feature: QgsFeature) -> dict:
    geometry = feature.geometry()
    return {'wkb': bytes(geometry.asWkb()).hex(), 'null': geometry.isNull(),
            'attributes': [_native_value(a) for a in feature.attributes()]}


def crs_semantic_definition(crs: QgsCoordinateReferenceSystem) -> str:
    """Return QGIS/PROJ's normalized operation definition for fingerprinting.

    A custom CRS may lose optional nested authority annotations when QGIS builds a
    memory-layer CRS. QGIS emits the same PROJ definition on both sides except that
    a reconstructed CRS may add the metadata-only ``+type=crs`` token. Projection,
    datum, units and operation parameters remain represented. QGIS CRS equality is
    still authoritative for a live comparison; this definition supports stable
    snapshot digests. Exact input WKT is stored separately and never reconstructed.
    """
    if not crs.isValid():
        return ''
    return ' '.join(token for token in crs.toProj().split() if token != '+type=crs')


def crs_semantically_equivalent(left: QgsCoordinateReferenceSystem,
                                right: QgsCoordinateReferenceSystem) -> bool:
    """Use QGIS/PROJ CRS equivalence, never display names or authority text alone."""
    if left.isValid() != right.isValid():
        return False
    return not left.isValid() or left == right


def crs_roundtrip_equivalent(left: QgsCoordinateReferenceSystem,
                             right: QgsCoordinateReferenceSystem) -> bool:
    """Accept provider-normalized CRS only when operational definitions still match.

    GeoPackage can attach/remove nested authority metadata such that QGIS ``==``
    returns false although both CRS objects emit the identical normalized PROJ
    definition. The fallback also requires matching CRS type, units and axis order.
    It is used for export verification only; exact official WKT remains in metadata.
    """
    if crs_semantically_equivalent(left, right):
        return True
    return (left.isValid() and right.isValid()
            and left.type() == right.type()
            and left.mapUnits() == right.mapUnits()
            and left.hasAxisInverted() == right.hasAxisInverted()
            and crs_semantic_definition(left) == crs_semantic_definition(right))


def _layer_content_digest(layer: QgsVectorLayer) -> str:
    """Hash schema and the feature-content multiset, excluding CRS serialization."""
    return digest({'fields': _schema(layer), 'geometry_type': int(layer.wkbType()),
                   'features': sorted(digest(_feature_content(f)) for f in layer.getFeatures())})


def layer_digest(layer: QgsVectorLayer) -> str:
    """Hash native content and semantic CRS; ignore names, mutable FIDs and order."""
    if not layer.isValid():
        raise ValueError('Survey snapshot is no longer valid')
    return digest({'crs_valid': layer.crs().isValid(),
                   'crs_semantic_definition': crs_semantic_definition(layer.crs()),
                   'content_digest': _layer_content_digest(layer)})


def layer_serialization_digest(layer: QgsVectorLayer) -> str:
    """Hash exact QGIS WKT serialization separately from semantic identity."""
    if not layer.isValid():
        raise ValueError('Survey snapshot is no longer valid')
    return digest({'crs_wkt': layer.crs().toWkt(),
                   'content_digest': _layer_content_digest(layer)})


def assert_snapshot_integrity(original: QgsVectorLayer, copied: QgsVectorLayer) -> None:
    """Verify complete content plus CRS meaning across a detached snapshot.

    Provider FIDs are retained separately as trace keys and are intentionally not
    portable identities. Feature multiplicity, schema, geometry and exact typed
    attributes are protected by the content multiset and portable content keys.
    """
    if not crs_semantically_equivalent(original.crs(), copied.crs()):
        raise ValueError('Snapshot changed the CRS definition')
    if _layer_content_digest(original) != _layer_content_digest(copied):
        raise ValueError('Snapshot changed geometry, attributes, schema or feature count')


def _native_snapshot(layer: QgsVectorLayer, name: str, features: list[QgsFeature]) -> QgsVectorLayer:
    """Copy native features and verify CRS semantics after provider serialization.

    QGIS may normalize optional WKT authority labels when creating a memory layer.
    Exact official WKT is therefore captured on Survey/PlannedTransect records,
    while this copy is accepted only when QGIS/PROJ reports equivalent CRS meaning.
    """
    copy = snapshot(layer, name, features)
    # snapshot()/write_verified() already compares this exact feature list with
    # the copy. The source layer may contain additional features for subset copies.
    if not crs_semantically_equivalent(layer.crs(), copy.crs()):
        raise ValueError('Snapshot changed the CRS definition')
    return copy


@dataclass(frozen=True)
class _PlanView:
    """Minimal planned-layer interface for the unchanged v0.1 model preflight."""
    planned: QgsVectorLayer


def setup_survey(layer: QgsVectorLayer, id_field: str, *, survey_id: str, name: str,
                 registry: SurveyRegistry, plan_revision: str = '1',
                 created_utc: str | None = None, display_crs: QgsCoordinateReferenceSystem | None = None,
                 project: QgsProject | None = None) -> SurveySetup:
    """Snapshot ALL features and retain explicit navigation/QA eligibility per plan.

    Invalid/duplicate identifiers prevent registration and expose every record on
    SurveyPreflightError.plans. Unsupported geometries/CRSs remain registered as
    unsupported; PREPARE requires recorded exclusions before omitting them.
    Selection is ignored: the layer is the official survey scope.
    """
    required_text(survey_id, 'Survey_ID')
    required_text(name, 'Survey name')
    required_text(plan_revision, 'Plan revision')
    created = utc_time(created_utc)
    if layer is None or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        raise SurveyPreflightError('Choose a valid official planned layer')
    if layer.fields().indexFromName(id_field) < 0:
        raise SurveyPreflightError('Choose an existing Transect_ID field')
    if display_crs is not None and not display_crs.isValid():
        raise SurveyPreflightError('Display CRS metadata must specify a valid CRS')
    project = project if project is not None else QgsProject.instance()
    source_crs_wkt = layer.crs().toWkt()
    source_crs_semantic_definition = crs_semantic_definition(layer.crs())
    source_crs_authid = layer.crs().authid()
    originals = []
    for feature in layer.getFeatures():
        originals.append(QgsFeature(feature))
        if len(originals) > 100_000:
            raise SurveyPreflightError('Survey exceeds 100,000 planned features')
    if not originals:
        raise SurveyPreflightError('Official planned layer is empty')
    source = _native_snapshot(layer, 'Official planned transects — survey snapshot', originals)
    source.setReadOnly(True)
    try:
        assert_snapshot_integrity(layer, source)
    except ValueError as error:
        raise SurveyPreflightError(f'Official layer changed during snapshot: {error}') from error
    identifiers = []
    for feature in originals:
        try:
            identifiers.append(transect_identifier(None if feature[id_field] == NULL else feature[id_field]))
        except ValueError:
            identifiers.append(None)
    counts = Counter(identifiers)
    records = []
    for feature, identifier in zip(originals, identifiers):
        content = _feature_content(feature)
        key = digest({'crs_semantic_definition': source_crs_semantic_definition,
                      'fields': _schema(source), 'feature': content})
        nav_status, nav_reason, nav_length = 'SUPPORTED', '', None
        qa_status, qa_reason, qa_length, qa_wkt = 'NOT_EVALUATED', '', None, ''
        if identifier is None or counts[identifier] > 1:
            nav_status = 'INVALID_ID'
            nav_reason = 'Blank/NULL/invalid Transect_ID' if identifier is None else 'Duplicate normalized Transect_ID'
        single = _native_snapshot(source, 'Preflight planned feature', [feature])
        try:
            # Two endpoints suffice for validation/ellipsoidal length. Actual batch
            # generation below uses the requested interval and legacy batch cap.
            nav = prepare_navigation(single, id_field, 1e100, project=project)
            nav_length = nav.rows[0].total_length_m
        except (ValueError, QgsCsException) as error:
            if nav_status != 'INVALID_ID':
                nav_status, nav_reason = 'UNSUPPORTED', str(error)
        if nav_status == 'SUPPORTED':
            try:
                path, working, _, _ = _working_path(_PlanView(single), project)
                qa_status, qa_length, qa_wkt = 'SUPPORTED', path.total_length_m, working.toWkt()
            except (ValueError, QgsCsException) as error:
                qa_status, qa_reason = 'UNSUPPORTED', str(error)
        records.append(PlannedTransect(
            survey_id, identifier, key, str(feature.id()), content['wkb'], content['null'],
            source_crs_wkt, source_crs_semantic_definition, canonical_json(content['attributes']),
            'SOURCE_VERTEX_ORDER',
            plan_revision, nav_status, nav_reason, nav_length, qa_status, qa_reason, qa_length, qa_wkt))
    plans = tuple(sorted(records, key=lambda p: (p.transect_id or '', p.source_content_key)))
    if any(p.navigation_status == 'INVALID_ID' for p in plans):
        raise SurveyPreflightError('Survey registration rejected: nonblank unique Transect_ID values are required', plans)
    survey = Survey(survey_id, name, SCHEMA_VERSION, created, layer_digest(source),
                    layer_serialization_digest(layer),
                    display_crs.toWkt() if display_crs is not None else '',
                    crs_semantic_definition(display_crs) if display_crs is not None else '',
                    source_crs_wkt, source_crs_semantic_definition, source_crs_authid,
                    canonical_json(_schema(source)), id_field, plan_revision, plans)
    registry.register(survey)
    return SurveySetup(survey, source)


def prepare_survey_navigation(setup: SurveySetup, spacing_m: float = 10.0, *,
                              reverse: bool = False, exclusions: Mapping[str, str] | None = None,
                              project: QgsProject | None = None) -> SurveyNavigation:
    """Generate all included plans atomically; every exclusion requires a reason."""
    if layer_digest(setup.source) != setup.survey.plan_snapshot_digest:
        raise ValueError('Official survey snapshot changed; register a new revision')
    exclusions = dict(exclusions or {})
    plans = {p.transect_id: p for p in setup.survey.plans}
    if not set(exclusions) <= set(plans):
        raise ValueError('Exclusion names an unknown Transect_ID')
    for reason in exclusions.values():
        required_text(reason, 'Exclusion reason')
    unsupported = [p.transect_id for p in plans.values()
                   if p.navigation_status != 'SUPPORTED' and p.transect_id not in exclusions]
    if unsupported:
        raise ValueError(f'Unsupported plans require explicit exclusion reasons: {unsupported}')
    included = [f for f in setup.source.getFeatures()
                if transect_identifier(f[setup.survey.id_field]) not in exclusions]
    if not included:
        raise ValueError('No included transects remain for navigation')
    selected = _native_snapshot(setup.source, 'Included official transects', included)
    prepared = prepare_navigation(selected, setup.survey.id_field, spacing_m,
                                  reverse=reverse, project=project)
    return SurveyNavigation(setup, prepared, tuple(sorted(exclusions.items())), spacing_m,
                            reverse, layer_digest(prepared.source), layer_digest(prepared.waypoints))


def validate_navigation_snapshot(result: SurveyNavigation) -> None:
    """Reject post-prepare native layer edits before exporting a coherent snapshot."""
    if (layer_digest(result.setup.source) != result.setup.survey.plan_snapshot_digest or
            layer_digest(result.prepared.source) != result.source_digest or
            layer_digest(result.prepared.waypoints) != result.waypoint_digest):
        raise ValueError('Survey navigation snapshot changed; prepare again before export')
