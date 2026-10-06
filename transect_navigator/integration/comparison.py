"""QGIS boundary for local-metre QA observations and detached diagnostic geometry."""
from dataclasses import dataclass
import json
from .. import __version__
from math import ceil, hypot
from qgis.PyQt.QtCore import QMetaType
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
    QgsPointXY, QgsGeometry, QgsFeature, QgsFields, QgsField, QgsMemoryProviderUtils, QgsVectorLayer, QgsWkbTypes, Qgis)
from .analyze import IngestionResult, _transform, _xy
from .crs import require_projected_metres
from .geodesy import Wgs84Geodesic
from .recorded import unique_field, write_verified
from ..geometry.planar import Point2D
from ..qaqc.deviation import DirectedTransect, Deviation, MAX_SEGMENTS

RADIUS_M = 5000.0
GEODESIC_STEP_M = 25.0


@dataclass(frozen=True)
class ComparisonResult:
    """QA points retain native source geometry; diagnostics use the working metre CRS."""
    observations: QgsVectorLayer
    nearest_points: QgsVectorLayer
    connectors: QgsVectorLayer
    rows: tuple[Deviation, ...]

    def layers(self) -> list[QgsVectorLayer]:
        return [self.observations, self.nearest_points, self.connectors]


def _strict_transform(source: QgsCoordinateReferenceSystem, destination: QgsCoordinateReferenceSystem,
                      project: QgsProject) -> QgsCoordinateTransform:
    transform = QgsCoordinateTransform(source,destination,project.transformContext())
    transform.setAllowFallbackTransforms(False)
    transform.setBallparkTransformsAreAppropriate(False)
    return transform


def _project(coordinate: tuple[float,float], transform: QgsCoordinateTransform) -> Point2D:
    if abs(coordinate[1]) > 85:
        raise ValueError('Analysis above 85 degrees latitude is outside v0.1 scope')
    point = transform.transform(QgsPointXY(*coordinate))
    if transform.fallbackOperationOccurred():
        raise ValueError('Analysis transform used an unsupported fallback')
    result = Point2D(point.x(),point.y())
    if hypot(result.x,result.y) > RADIUS_M:
        raise ValueError('Analysis is limited to a 5 km radius around the reproducible local origin; split this survey')
    return result


def _working_path(ingested: IngestionResult, project: QgsProject) -> tuple[
        DirectedTransect, QgsCoordinateReferenceSystem, QgsCoordinateTransform, tuple[float,float]]:
    """Densify the PREPARE geodesic path only in a private working representation."""
    features = list(ingested.planned.getFeatures())
    if len(features) != 1:
        raise ValueError('Analysis requires exactly one ingested planned feature')
    geometry = features[0].geometry()
    if geometry.isNull() or geometry.isEmpty() or QgsWkbTypes.flatType(geometry.wkbType()) != Qgis.WkbType.LineString:
        raise ValueError('Analysis requires a nonempty single-part linear planned transect')
    transform = _transform(ingested.planned,project)
    geographic = [_xy(p,ingested.planned,transform) for p in features[0].geometry().vertices()]
    if len(geographic) < 2:
        raise ValueError('Planned transect has insufficient vertices')
    # Independent of traversal direction, so reversal uses exactly the same frame.
    anchor = min(geographic)
    if any(abs(lat) > 85 for _,lat in geographic):
        raise ValueError('Analysis above 85 degrees latitude is outside v0.1 scope')
    working = QgsCoordinateReferenceSystem()
    if not working.createFromProj(f'+proj=aeqd +lat_0={anchor[1]:.16g} +lon_0={anchor[0]:.16g} +datum=WGS84 +units=m +no_defs'):
        raise ValueError('Could not create the local WGS84 azimuthal-equidistant CRS')
    require_projected_metres(working)
    to_local = _strict_transform(QgsCoordinateReferenceSystem('EPSG:4326'),working,project)
    metric = Wgs84Geodesic(project.transformContext())
    for coordinate in geographic:
        _project(coordinate,to_local)
    vertices = [_project(geographic[0],to_local)]
    for start,end in zip(geographic,geographic[1:]):
        length = metric.distance(start,end)
        if length <= 1e-7:
            raise ValueError('Planned transect has a duplicate or zero-length source segment')
        count = max(1,ceil(length/GEODESIC_STEP_M))
        if len(vertices)+count > MAX_SEGMENTS+1:
            raise ValueError('Planned transect exceeds 2,000 working segments at 25 m subdivision')
        for index in range(1,count+1):
            coordinate = end if index == count else metric.interpolate(start,end,length*index/count)
            vertices.append(_project(coordinate,to_local))
    return DirectedTransect(vertices),working,to_local,anchor


def compare_ingested(ingested: IngestionResult, *, project: QgsProject | None = None) -> ComparisonResult:
    """Compare all preserved observations, regardless of missing or duplicate timestamps.

    No source geometry is transformed in place. All outputs are detached until the
    caller publishes the complete result. A spatial ambiguity aborts the whole run.
    """
    project = project if project is not None else QgsProject.instance()
    transect,working,to_local,anchor = _working_path(ingested,project)
    provenance = json.loads(ingested.raw_observations.customProperty('transect_navigator/provenance'))
    id_field = provenance['raw_source_id_field']
    source = ingested.raw_observations
    raw_by_id = {str(f[id_field]): f for f in source.getFeatures()}
    normalized = ingested.ordering.observations
    if len(raw_by_id) != source.featureCount() or set(raw_by_id) != {r.raw.source_id for r in normalized}:
        raise ValueError('Raw observation identifiers no longer match ingestion; ingest again')
    raw_transform = _transform(source,project)
    for observation in normalized:
        geometry = raw_by_id[observation.raw.source_id].geometry()
        if geometry.isNull() or geometry.isEmpty() or QgsWkbTypes.flatType(geometry.wkbType()) != Qgis.WkbType.Point:
            raise ValueError('Raw observation geometry changed; ingest again')
        coordinate = _xy(next(geometry.vertices()),source,raw_transform)
        current = _project(coordinate,to_local)
        captured = _project((observation.raw.longitude,observation.raw.latitude),to_local)
        if hypot(current.x-captured.x,current.y-captured.y) > 1e-7:
            raise ValueError('Raw observation geometry or CRS changed since ingestion; ingest again')
    working_points = [_project((r.raw.longitude,r.raw.latitude),to_local) for r in normalized]
    rows = transect.compare_many(working_points)
    string, number = QMetaType.Type.QString, QMetaType.Type.Double
    columns = (('QA_Transect_ID',string),('QA_Source_ID',string),('QA_Deviation_m',number),
        ('QA_Signed_m',number),('QA_Station_m',number),('QA_Progress_pct',number),
        ('QA_Plan_Length_m',number),('QA_Time_UTC',string),('QA_Accuracy_Raw',string),
        ('QA_Sign_Status',string),('QA_Nearest_X_m',number),('QA_Nearest_Y_m',number))
    fields = QgsFields(source.fields())
    mapping = {}
    for name,kind in columns:
        actual = unique_field(fields,name)
        fields.append(QgsField(actual,kind))
        mapping[name] = actual
    qa = QgsMemoryProviderUtils.createMemoryLayer('QA Observations',fields,source.wkbType(),source.crs())
    diagnostic_fields = QgsFields()
    for name in ('Source_ID','Transect_ID'):
        diagnostic_fields.append(QgsField(name,string))
    nearest = QgsMemoryProviderUtils.createMemoryLayer('QA Nearest Locations',diagnostic_fields,Qgis.WkbType.Point,working)
    connectors = QgsMemoryProviderUtils.createMemoryLayer('QA Observation Connectors',diagnostic_fields,Qgis.WkbType.LineString,working)
    qa_features, nearest_features, connector_features = [], [], []
    for observation,point,row in zip(normalized,working_points,rows):
        original = raw_by_id[observation.raw.source_id]
        feature = QgsFeature(fields)
        feature.setGeometry(QgsGeometry(original.geometry()))
        feature.setAttributes(original.attributes()+[provenance['planned_id'],observation.raw.source_id,
            row.absolute_m,row.signed_m,row.distance_along_m,row.progress_pct,row.total_length_m,
            observation.timestamp_utc,observation.raw.accuracy,row.sign_status,row.nearest.x,row.nearest.y])
        qa_features.append(feature)
        nearest_feature = QgsFeature(diagnostic_fields)
        nearest_feature.setAttributes([observation.raw.source_id,provenance['planned_id']])
        nearest_feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(row.nearest.x,row.nearest.y)))
        nearest_features.append(nearest_feature)
        # Zero-distance observations already have a nearest point; no degenerate line.
        if row.absolute_m > 0:
            connector = QgsFeature(diagnostic_fields)
            connector.setAttributes(nearest_feature.attributes())
            connector.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(point.x,point.y),QgsPointXY(row.nearest.x,row.nearest.y)]))
            connector_features.append(connector)
    write_verified(qa,fields,qa_features)
    write_verified(nearest,diagnostic_fields,nearest_features)
    write_verified(connectors,diagnostic_fields,connector_features)
    provenance.update({'qa_metrics_computed':True,'plugin_version':__version__,
        'raw_observation_layer_id':ingested.raw_observations.id(),
        'planned_snapshot_layer_id':ingested.planned.id(),
        'analysis_model':'local AEQD planar nearest point on subdivided WGS84 geodesic path',
        'analysis_crs_wkt':working.toWkt(),'analysis_origin_lonlat':anchor,
        'analysis_radius_m':RADIUS_M,'geodesic_subdivision_m':GEODESIC_STEP_M,
        'analysis_coordinate_operation':to_local.instantiatedCoordinateOperationDetails().proj,
        'signed_convention':'positive left, negative right; endpoint/ambiguous vertex NULL',
        'qa_field_mapping':mapping,'gnss_accuracy_usage':'preserved only; no correction or pass/fail'})
    for layer in (qa,nearest,connectors):
        layer.setCustomProperty('transect_navigator/data_role','derived_qa_qc_data')
        layer.setCustomProperty('transect_navigator/provenance',json.dumps(provenance,sort_keys=True))
        layer.setReadOnly(True)
    return ComparisonResult(qa,nearest,connectors,rows)
