"""ANALYZE ingestion services: raw preservation, chronology and input sanity checks only."""
from __future__ import annotations
from dataclasses import dataclass
import json
from .. import __version__
from math import fsum, isfinite
from qgis.PyQt.QtCore import QMetaType, Qt, QDateTime
from qgis.core import (NULL, Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCsException, QgsDistanceArea, QgsFeature, QgsField, QgsFields,
                       QgsGeometry, QgsMemoryProviderUtils, QgsPoint, QgsPointXY,
                       QgsProject, QgsVectorLayer, QgsWkbTypes)
from .recorded import RecordingSource, recording_from_layer, snapshot, write_verified
from .crs import requires_coordinate_epoch
from ..navigation.identifiers import transect_identifier
from ..analysis.observations import (DataRole, Issue, Observation, Normalization,
                                     normalize_observations, definitely_far, validate_lonlat, MAX_OBSERVATIONS)


@dataclass(frozen=True)
class IngestionResult:
    planned: QgsVectorLayer
    raw_observations: QgsVectorLayer
    raw_track: QgsVectorLayer | None
    normalized: QgsVectorLayer
    validation: QgsVectorLayer
    ordering: Normalization
    issues: tuple[Issue, ...]

    def layers(self) -> list[QgsVectorLayer]:
        """Return only actual outputs; no empty QA/QC metric layer is fabricated."""
        return [self.planned,self.raw_observations]+([self.raw_track] if self.raw_track is not None else [])+[self.normalized,self.validation]


def _text(value: object) -> str | None:
    if value is None or value == NULL:
        return None
    if isinstance(value,QDateTime):
        # QGIS provider time parsing may already have lost source lexical precision.
        # Qt LocalTime has no reliable declared source zone: retain it as naive text.
        pattern = "yyyy-MM-dd'T'HH:mm:ss.zzz"
        if value.timeSpec() == Qt.TimeSpec.LocalTime:
            return value.toString(pattern)
        return value.toString(Qt.DateFormat.ISODateWithMs)
    return str(value)


def _crs(layer: QgsVectorLayer) -> None:
    crs = layer.crs()
    if not crs.isValid() or crs.type() not in (Qgis.CrsType.Projected,Qgis.CrsType.Geographic2d):
        raise ValueError(f'{layer.name()}: a valid projected or 2D geographic CRS must be declared')
    if crs.isGeographic() and crs.mapUnits() != Qgis.DistanceUnit.Degrees:
        raise ValueError('Geographic input must use degrees; convert a documented copy first')
    if requires_coordinate_epoch(crs):
        raise ValueError('Dynamic CRS epoch handling is not yet supported; use a documented static-CRS copy')


def _transform(layer: QgsVectorLayer, project: QgsProject) -> QgsCoordinateTransform:
    _crs(layer)
    transform = QgsCoordinateTransform(layer.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),project.transformContext())
    transform.setAllowFallbackTransforms(False)
    transform.setBallparkTransformsAreAppropriate(False)
    return transform


def _xy(point: QgsPoint, layer: QgsVectorLayer, transform: QgsCoordinateTransform) -> tuple[float,float]:
    if not (isfinite(point.x()) and isfinite(point.y())):
        raise ValueError('Missing/nonfinite XY coordinate')
    if layer.crs().isGeographic():
        validate_lonlat(point.x(),point.y())
    result = transform.transform(QgsPointXY(point.x(),point.y()))
    if transform.fallbackOperationOccurred():
        raise ValueError('Coordinate transformation used a forbidden fallback')
    coordinate = (result.x(),result.y())
    validate_lonlat(*coordinate)
    return coordinate


def _table(name: str, columns: tuple[tuple[str,object],...], rows: list[list[object]],
           coordinates: list[tuple[float,float]] | None = None) -> QgsVectorLayer:
    fields = QgsFields()
    for field, kind in columns:
        fields.append(QgsField(field,kind))
    layer = QgsMemoryProviderUtils.createMemoryLayer(name,fields,
                Qgis.WkbType.Point if coordinates is not None else Qgis.WkbType.NoGeometry,
                QgsCoordinateReferenceSystem('EPSG:4326') if coordinates is not None else QgsCoordinateReferenceSystem())
    features = []
    for index,values in enumerate(rows):
        feature = QgsFeature(fields)
        feature.setAttributes(values)
        if coordinates is not None:
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*coordinates[index])))
        features.append(feature)
    write_verified(layer, fields, features)
    return layer


def ingest_recording(planned_layer: QgsVectorLayer, id_field: str, feature_id: int,
                     recorded: QgsVectorLayer | RecordingSource, *, timestamp_field: str = '',
                     accuracy_field: str = '', group_field: str = '', create_track: bool = True,
                     far_threshold_m: float = 10_000, project: QgsProject | None = None) -> IngestionResult:
    """Create detached raw/derived layers; no source editing or project insertion.

    Timestamp anomalies are retained with NULL chronology, not discarded. Spatial
    failures abort the batch. The far screen is a conservative geodesic lower bound
    to a selected planned path; it is explicitly not cross-track deviation.
    """
    project = project if project is not None else QgsProject.instance()
    if planned_layer is None or not isinstance(planned_layer,QgsVectorLayer) or not planned_layer.isValid():
        raise ValueError('Choose a valid planned transect layer')
    if planned_layer.fields().indexFromName(id_field) < 0:
        raise ValueError('Choose the planned Transect ID field')
    if feature_id is None:
        raise ValueError('Choose one planned transect feature')
    feature = planned_layer.getFeature(feature_id)
    if not feature.isValid() or feature.geometry().isNull() or feature.geometry().isEmpty():
        raise ValueError('Selected planned feature is missing or has empty geometry')
    if QgsWkbTypes.flatType(feature.geometry().wkbType()) != Qgis.WkbType.LineString:
        raise ValueError('Choose one single-part linear planned transect; multipart/curves are ambiguous')
    raw_id = feature[id_field]
    planned_id = transect_identifier(None if raw_id == NULL else raw_id)
    if not isfinite(far_threshold_m) or far_threshold_m <= 0:
        raise ValueError('Far-observation screening threshold must be positive metres')
    source = recorded if isinstance(recorded,RecordingSource) else recording_from_layer(recorded)
    plan_transform = _transform(planned_layer,project)
    input_transform = _transform(source.points,project)
    plan_coordinates = []
    for point in feature.geometry().vertices():
        plan_coordinates.append(_xy(point, planned_layer, plan_transform))
        if len(plan_coordinates) > MAX_OBSERVATIONS:
            raise ValueError('Planned transect exceeds 100,000 vertices')
    if len(plan_coordinates) < 2:
        raise ValueError('Planned transect needs at least two vertices')
    for name in (timestamp_field,accuracy_field,group_field):
        if name and source.points.fields().indexFromName(name) < 0:
            raise ValueError(f'Recorded field {name!r} is missing; reload the input and choose its mapping')
    issues = list(source.issues)
    if source.points.crs() != planned_layer.crs():
        issues.append(Issue('CRS_MISMATCH','Input CRS differs from planned CRS. Only working coordinate copies were transformed to WGS84; verify both declared CRSs.'))
    if source.vertex_observations and timestamp_field:
        issues.append(Issue('FEATURE_TIME_NOT_VERTEX_TIME','Track feature timestamps are retained as parent attributes, not assigned as per-vertex observation times.'))
    if source.vertex_observations and accuracy_field:
        issues.append(Issue('FEATURE_ACCURACY_NOT_VERTEX_ACCURACY','Track feature accuracy is retained as a parent attribute, not inferred as per-vertex GNSS accuracy.'))
    observations, originals = [], {}
    # Preserve structural GPX groups even if the user also chooses a grouping field.
    structural_gpx = source.gpx_provider_segments
    if structural_gpx and not source.segment_field:
        issues.append(Issue('GPX_STRUCTURAL_GROUPS','track_fid and track_seg_id are used as segment boundaries; no cross-segment joins.'))
    for point_feature in source.points.getFeatures():
        identifier = _text(point_feature[source.source_id_field]) if source.source_id_field else str(point_feature.id())
        try:
            geometry = point_feature.geometry()
            if geometry.isNull() or geometry.isEmpty() or QgsWkbTypes.flatType(geometry.wkbType()) != Qgis.WkbType.Point:
                raise ValueError('Missing or unsupported observation point geometry')
            point = next(geometry.vertices())
            coordinate = _xy(point,source.points,input_transform)
            parts = []
            if source.segment_field:
                parts.append(_text(point_feature[source.segment_field]))
            elif structural_gpx:
                parts.extend(_text(point_feature[n]) for n in ('track_fid','track_seg_id'))
            if group_field and group_field != source.segment_field:
                parts.append(_text(point_feature[group_field]))
            if any(value is None or not value.strip() for value in parts):
                raise ValueError('Track/segment grouping value is missing; cannot join observations safely')
            group = json.dumps(parts,ensure_ascii=False) if parts else 'survey'
            time = None if source.vertex_observations or not timestamp_field else _text(point_feature[timestamp_field])
            accuracy = _text(point_feature[accuracy_field]) if accuracy_field and not source.vertex_observations else None
            observations.append(Observation(identifier,group,*coordinate,time,accuracy))
            originals[identifier] = point.clone()
            if len(observations) > MAX_OBSERVATIONS:
                raise ValueError('Input exceeds 100,000 observations')
        except (ValueError,QgsCsException) as error:
            raise ValueError(f'Observation {identifier}: {error}') from error
    ordering = normalize_observations(observations)
    issues.extend(ordering.issues)
    by_id = {row.raw.source_id:row for row in ordering.observations}
    for group,ids in source.geometry_orders:
        if len(ids) >= 2 and all(by_id[i].timestamp_utc is not None for i in ids):
            times = [by_id[i].timestamp_utc for i in ids]
            if any(a >= b for a,b in zip(times,times[1:])):
                issues.append(Issue('TIME_ORDER_CONFLICT','Recorded vertex order and timestamps conflict; raw track order has been preserved.',group))
    measure = QgsDistanceArea()
    measure.setSourceCrs(QgsCoordinateReferenceSystem('EPSG:4326'),project.transformContext())
    if not measure.setEllipsoid('WGS84') or not measure.willUseEllipsoid():
        raise ValueError('Could not initialize the ellipsoidal input screen')
    lengths = [measure.measureLine(QgsPointXY(*a),QgsPointXY(*b)) for a,b in zip(plan_coordinates,plan_coordinates[1:])]
    if not all(isfinite(v) and v >= 0 for v in lengths) or fsum(lengths) <= 0:
        raise ValueError('Selected planned transect has no finite positive length')
    total = fsum(lengths)
    far_ids = set()
    for observation in observations:
        distance = measure.measureLine(QgsPointXY(*plan_coordinates[0]),QgsPointXY(observation.longitude,observation.latitude))
        if definitely_far(distance,total,far_threshold_m):
            far_ids.add(observation.source_id)
    if far_ids:
        issues.append(Issue('OBSERVATIONS_VERY_FAR',f'{len(far_ids)} observations are definitely farther than {far_threshold_m:g} m from the selected planned path. Check input, CRS and transect choice; this is not a QA/QC metric.'))
    planned = snapshot(planned_layer,'PLANNED TRANSECT',[feature])
    raw = snapshot(source.points,'RAW GPS OBSERVATIONS')
    raw_track = snapshot(source.tracks,'RAW TRACK — original geometry') if source.tracks is not None and source.tracks.featureCount() else None
    if source.tracks is None and create_track and ordering.ordered_groups:
        fields = QgsFields()
        fields.append(QgsField('Group_ID',QMetaType.Type.QString))
        fields.append(QgsField('Order_Basis',QMetaType.Type.QString))
        wkb = Qgis.WkbType.LineString
        if QgsWkbTypes.hasZ(raw.wkbType()):
            wkb = QgsWkbTypes.addZ(wkb)
        if QgsWkbTypes.hasM(raw.wkbType()):
            wkb = QgsWkbTypes.addM(wkb)
        raw_track = QgsMemoryProviderUtils.createMemoryLayer('RAW TRACK — timestamp-connected observations',fields,wkb,raw.crs())
        track_features = []
        for group,ids in ordering.ordered_groups:
            track_feature = QgsFeature(fields)
            track_feature.setAttributes([group,'explicit UTC timestamps; no smoothing'])
            track_feature.setGeometry(QgsGeometry.fromPolyline([originals[i].clone() for i in ids]))
            track_features.append(track_feature)
        write_verified(raw_track, fields, track_features)
        raw_track.setCustomProperty('transect_navigator/track_origin','constructed_from_observations')
    elif raw_track is not None:
        raw_track.setCustomProperty('transect_navigator/track_origin','recorded_geometry')
    if create_track and source.tracks is None and not ordering.ordered_groups:
        issues.append(Issue('NO_TRACK_CONSTRUCTED','No group has two or more observations with complete, unique timestamps. Raw points were retained without a fabricated track.'))
    string = QMetaType.Type.QString
    normalized = _table('DERIVED — normalized observations',
        (('Source_ID',string),('Group_ID',string),('Timestamp_Original',string),('Timestamp_UTC',string),
         ('Sequence',QMetaType.Type.Int),('Time_Status',string),('Accuracy_Raw',string),('Position_Warning',string)),
        [[r.raw.source_id,r.raw.group,r.raw.timestamp,r.timestamp_utc,r.sequence,r.time_status,r.raw.accuracy,
          'definitely_far' if r.raw.source_id in far_ids else None] for r in ordering.observations],
        [(r.raw.longitude,r.raw.latitude) for r in ordering.observations])
    validation = _table('DERIVED — ingestion validation',
        (('Code',string),('Message',string),('Group_ID',string),('Source_ID',string)),
        [[i.code,i.message,i.group,i.source_id] for i in issues])
    provenance = {'plugin_version':__version__,'qgis_version':Qgis.QGIS_VERSION,'planned_id':planned_id,
        'planned_feature_id':feature_id,'source_layer_id':source.original_layer_id or source.points.id(),
        'source_layer_name':source.original_layer_name or source.points.name(),
        'raw_source_id_field':source.source_id_field,'raw_track_id_field':source.track_id_field,'source_crs_wkt':source.points.crs().toWkt(),
        'planned_crs_wkt':planned_layer.crs().toWkt(),'timestamp_field':timestamp_field,'accuracy_field':accuracy_field,
        'group_field':group_field,'accuracy_units':'not inferred','far_screen_m':far_threshold_m,
        'far_screen_method':'anchor geodesic distance minus planned geodesic length (conservative lower bound)',
        'recorded_coordinate_operation':input_transform.instantiatedCoordinateOperationDetails().proj,
        'planned_coordinate_operation':plan_transform.instantiatedCoordinateOperationDetails().proj,
        'chronology':'only complete, unique explicit-offset timestamps per group','qa_metrics_computed':False}
    if source.file_recording is not None:
        provenance.update({'source_path':source.file_recording.source_path,'source_sha256':source.file_recording.sha256})
        if source.file_recording.raw_document is not None:
            raw.setCustomProperty('transect_navigator/raw_gpx_document',source.file_recording.raw_document)
    for layer,role in ((planned,DataRole.PLANNED_TRANSECT),(raw,DataRole.RAW_OBSERVATIONS),
                       (raw_track,DataRole.RAW_TRACK),(normalized,DataRole.DERIVED),(validation,DataRole.DERIVED)):
        if layer is not None:
            layer.setCustomProperty('transect_navigator/data_role',role.value)
            layer.setCustomProperty('transect_navigator/provenance',json.dumps(provenance,sort_keys=True))
            layer.setReadOnly(True)
    return IngestionResult(planned,raw,raw_track,normalized,validation,ordering,tuple(issues))
