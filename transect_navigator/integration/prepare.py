"""Read-only input adapter and atomic creation of a separate waypoint layer."""
from __future__ import annotations
import json
from .. import __version__
from dataclasses import dataclass
from math import isfinite
from qgis.PyQt.QtCore import QMetaType
from qgis.core import (NULL, Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCsException, QgsFeature, QgsField, QgsGeometry, QgsPointXY,
                       QgsProject, QgsVectorLayer, QgsWkbTypes, QgsMemoryProviderUtils,
                       QgsCoordinateTransformContext)
from .geodesy import Wgs84Geodesic
from .crs import requires_coordinate_epoch
from .recorded import write_verified
from ..navigation.waypoints import Transect, Waypoint, generate_waypoints, validate_spacing
from ..navigation.identifiers import transect_identifier
from ..analysis.observations import validate_lonlat

FIELDS = (
    ('Transect_ID', QMetaType.Type.QString), ('Waypoint_ID', QMetaType.Type.QString),
    ('Sequence', QMetaType.Type.Int), ('Distance_Along_m', QMetaType.Type.Double),
    ('Total_Length_m', QMetaType.Type.Double), ('Latitude', QMetaType.Type.Double),
    ('Longitude', QMetaType.Type.Double), ('Bearing_Next_deg', QMetaType.Type.Double),
)


@dataclass(frozen=True)
class PreparedNavigation:
    """A single run's source snapshot, WGS84 plans, waypoints and transform context."""
    source: QgsVectorLayer
    waypoints: QgsVectorLayer
    transects: tuple[Transect, ...]
    rows: tuple[Waypoint, ...]
    context: QgsCoordinateTransformContext


def prepare_navigation(layer: QgsVectorLayer, id_field: str, spacing_m: float = 10.0,
                       *, selected_only: bool = False, reverse: bool = False,
                       project: QgsProject | None = None) -> PreparedNavigation:
    """Return one coherent run snapshot; caller adds or exports it after success.

    Source features, edits, selection and geometries remain untouched. Any input
    error aborts the whole batch. No partial layer is added to the project.
    """
    validate_spacing(spacing_m)
    project = project if project is not None else QgsProject.instance()
    if layer is None or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        raise ValueError('Select a valid polyline layer')
    if layer.geometryType() != Qgis.GeometryType.Line:
        raise ValueError('Input must be a polyline layer')
    if layer.fields().indexFromName(id_field) < 0:
        raise ValueError('Select an existing Transect ID field')
    crs = layer.crs()
    if not crs.isValid() or crs.type() not in (Qgis.CrsType.Projected, Qgis.CrsType.Geographic2d):
        raise ValueError('Input needs a valid projected or 2D geographic CRS')
    if requires_coordinate_epoch(crs):
        raise ValueError('Dynamic CRS requires epoch handling and is not supported yet')
    if crs.isGeographic() and crs.mapUnits() != Qgis.DistanceUnit.Degrees:
        raise ValueError('Geographic input must use degrees; convert a documented copy first')
    if selected_only and layer.selectedFeatureCount() == 0:
        raise ValueError('Selected features only is enabled, but no transects are selected')
    context = project.transformContext()
    transform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem('EPSG:4326'), context)
    transform.setAllowFallbackTransforms(False)
    transform.setBallparkTransformsAreAppropriate(False)
    metric = Wgs84Geodesic(context)
    transects = []
    source_ids = {}
    source_features = []
    features = layer.getSelectedFeatures() if selected_only else layer.getFeatures()
    try:
        for feature in features:
            try:
                raw_id = feature[id_field]
                identifier = transect_identifier(None if raw_id == NULL else raw_id)
                geometry = feature.geometry()
                if geometry.isNull() or geometry.isEmpty():
                    raise ValueError('Empty geometry')
                if geometry.wkbType() != QgsWkbTypes.LineString:
                    raise ValueError('Only single-part 2D LineString is supported; multipart, curves, Z/M are rejected')
                vertices = []
                for vertex in geometry.asPolyline():
                    if not (isfinite(vertex.x()) and isfinite(vertex.y())):
                        raise ValueError('Nonfinite source coordinates')
                    if crs.isGeographic():
                        validate_lonlat(vertex.x(), vertex.y())
                    point = transform.transform(vertex)
                    if transform.fallbackOperationOccurred():
                        raise ValueError('Coordinate transform used an unsupported fallback operation')
                    coordinate = (point.x(), point.y())
                    metric.validate(coordinate)
                    vertices.append(coordinate)
                source_features.append(QgsFeature(feature))
                transects.append(Transect(identifier, tuple(vertices)))
                source_ids.setdefault(identifier, []).append(feature.id())
            except (ValueError, QgsCsException) as error:
                raise ValueError(f'Feature {feature.id()}: {error}') from error
    finally:
        features.close()
    if not transects:
        raise ValueError('The input layer contains no transects in the chosen scope')
    rows = generate_waypoints(transects, spacing_m, metric, reverse=reverse)
    source = QgsMemoryProviderUtils.createMemoryLayer('Source transects', layer.fields(), QgsWkbTypes.LineString, crs)
    write_verified(source, layer.fields(), source_features)
    source.setReadOnly(True)
    output = QgsVectorLayer('Point?crs=EPSG:4326', f'{layer.name()} — navigation waypoints', 'memory')
    provider = output.dataProvider()
    if not output.isValid() or not provider.addAttributes([QgsField(name, kind) for name, kind in FIELDS]):
        raise ValueError('Could not create waypoint layer fields')
    output.updateFields()
    result_features = []
    for row in rows:
        longitude, latitude = row.coordinate
        feature = QgsFeature(output.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(longitude, latitude)))
        feature.setAttributes([row.transect_id, row.waypoint_id, row.sequence, float(row.distance_along_m),
                               float(row.total_length_m), float(latitude), float(longitude),
                               None if row.bearing_next_deg is None else float(row.bearing_next_deg)])
        result_features.append(feature)
    write_verified(output, output.fields(), result_features)
    output.setCustomProperty('transect_navigator/provenance', json.dumps({
        'plugin_version': __version__, 'qgis_version': Qgis.QGIS_VERSION,
        'source_layer_id': layer.id(), 'source_crs_wkt': crs.toWkt(),
        'source_feature_ids': source_ids, 'id_field': id_field, 'spacing_m': spacing_m,
        'selected_only': selected_only, 'reverse': reverse, 'ellipsoid': 'WGS84',
        'path_model': 'shortest geodesic legs between transformed source vertices',
        'coordinate_operation': transform.instantiatedCoordinateOperationDetails().proj,
        'output_crs': 'EPSG:4326', 'bearing': 'initial true bearing to next waypoint; final NULL',
    }, sort_keys=True))
    return PreparedNavigation(source, output, tuple(transects), tuple(rows), QgsCoordinateTransformContext(context))


def prepare_waypoint_layer(layer: QgsVectorLayer, id_field: str, spacing_m: float = 10.0,
                           *, selected_only: bool = False, reverse: bool = False,
                           project: QgsProject | None = None) -> QgsVectorLayer:
    """Stable scripting entry point for callers that need only the point layer."""
    return prepare_navigation(layer, id_field, spacing_m, selected_only=selected_only,
                              reverse=reverse, project=project).waypoints
