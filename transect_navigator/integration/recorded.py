"""Non-destructive native snapshots and practical recorded-data file adapters."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from qgis.PyQt.QtCore import QMetaType
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFields,
                       QgsGeometry, QgsMemoryProviderUtils, QgsPoint, QgsPointXY,
                       QgsProviderRegistry, QgsVectorLayer, QgsWkbTypes)
from ..analysis.observations import Issue, MAX_OBSERVATIONS
from ..io.recorded import FileRecording, read_csv, read_gpx, validate_attribute_table


@dataclass
class RecordingSource:
    """Detached raw snapshots plus explicit links to their original source."""
    points: QgsVectorLayer
    tracks: QgsVectorLayer | None = None
    source_id_field: str = ''
    segment_field: str = ''
    default_time_field: str = ''
    geometry_orders: tuple[tuple[str, tuple[str, ...]], ...] = ()
    issues: tuple[Issue, ...] = ()
    file_recording: FileRecording | None = None
    vertex_observations: bool = False
    original_layer_id: str = ''
    original_layer_name: str = ''
    gpx_provider_segments: bool = False
    track_id_field: str = ''


def unique_field(fields: QgsFields, base: str) -> str:
    """Never overwrite an input field to add provenance."""
    names = {name.casefold() for name in fields.names()}
    while base.casefold() in names:
        base += '_'
    return base


def write_verified(layer: QgsVectorLayer, fields: QgsFields,
                   features: list[QgsFeature]) -> None:
    """Refuse provider truncation/coercion instead of silently losing raw data."""
    expected = Counter((bytes(f.geometry().asWkb()), tuple(repr(a) for a in f.attributes()))
                       for f in features)
    if not layer.isValid() or (features and not layer.dataProvider().addFeatures(
            [QgsFeature(f) for f in features])[0]):
        raise ValueError(f'Could not preserve {layer.name()}')
    actual = Counter((bytes(f.geometry().asWkb()), tuple(repr(a) for a in f.attributes()))
                     for f in layer.getFeatures())
    if fields.names() != layer.fields().names() or expected != actual:
        raise ValueError(f'{layer.name()}: snapshot would change geometry or field values; unsupported input fields')
    layer.updateExtents()


def snapshot(layer: QgsVectorLayer, name: str, features: list[QgsFeature] | None = None) -> QgsVectorLayer:
    """Copy native geometry/values and check for memory-provider coercion."""
    if features is None:
        features = []
        for feature in layer.getFeatures():
            features.append(QgsFeature(feature))
            if len(features) > MAX_OBSERVATIONS:
                raise ValueError('Input exceeds 100,000 features; choose a smaller survey layer')
    copy = QgsMemoryProviderUtils.createMemoryLayer(name, layer.fields(), layer.wkbType(), layer.crs())
    write_verified(copy, layer.fields(), features)
    return copy


def from_file_records(records: FileRecording, crs: QgsCoordinateReferenceSystem) -> RecordingSource:
    """Expose raw strings as fields; original file and optional GPX document remain preserved."""
    if not crs.isValid():
        raise ValueError('Choose a valid explicit CSV CRS')
    fields = QgsFields()
    names = sorted({key for p in records.points for key,_ in p.attributes})
    validate_attribute_table(len(names), len(records.points))
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Input field names collide case-insensitively; rename fields in a copy')
    for name in names:
        fields.append(QgsField(name,QMetaType.Type.QString))
    id_name = unique_field(fields,'TN_Source_ID')
    fields.append(QgsField(id_name,QMetaType.Type.QString))
    group_name = unique_field(fields,'TN_Segment')
    fields.append(QgsField(group_name,QMetaType.Type.QString))
    points = QgsMemoryProviderUtils.createMemoryLayer('Imported raw observations',fields,Qgis.WkbType.Point,crs)
    features = []
    for point in records.points:
        feature = QgsFeature(fields)
        attrs = dict(point.attributes)
        feature.setAttributes([attrs.get(name) for name in names]+[point.source_id,point.group])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*point.coordinate)))
        features.append(feature)
    write_verified(points, fields, features)
    tracks = None
    if records.tracks:
        track_fields = QgsFields()
        for name in ('Segment','Raw_XML'):
            track_fields.append(QgsField(name,QMetaType.Type.QString))
        tracks = QgsMemoryProviderUtils.createMemoryLayer('Imported GPX raw tracks',track_fields,Qgis.WkbType.LineString,crs)
        lookup = {point.source_id:point.coordinate for point in records.points}
        features = []
        for track in records.tracks:
            if len(track.point_ids) >= 2:
                feature = QgsFeature(track_fields)
                feature.setAttributes([track.group,track.raw_xml])
                feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*lookup[i]) for i in track.point_ids]))
                features.append(feature)
        write_verified(tracks, track_fields, features)
    return RecordingSource(points,tracks,id_name,group_name,'time' if records.raw_document is not None else '',
                           tuple((t.group,t.point_ids) for t in records.tracks),records.issues,records)


def file_sublayers(path: Path) -> list[tuple[str, str]]:
    """Ask QGIS for vector sublayers instead of choosing an arbitrary GeoPackage table."""
    if path.suffix.lower() not in {'.gpkg','.geojson','.json'} or not path.is_file():
        raise ValueError('Choose a GeoPackage or GeoJSON file')
    return [(detail.name(),detail.uri()) for detail in QgsProviderRegistry.instance().querySublayers(str(path))
            if detail.type() == Qgis.LayerType.Vector]


def load_recorded_file(path: Path, *, uri: str = '', x_field: str = '', y_field: str = '',
                       csv_crs: str = '') -> RecordingSource | QgsVectorLayer:
    """Read files privately; opening input never adds or edits a project layer."""
    suffix = path.suffix.lower()
    if suffix == '.gpx':
        return from_file_records(read_gpx(path),QgsCoordinateReferenceSystem('EPSG:4326'))
    if suffix == '.csv':
        return from_file_records(read_csv(path,x_field,y_field),QgsCoordinateReferenceSystem(csv_crs))
    choices = file_sublayers(path)
    if uri not in {value for _,value in choices}:
        if len(choices) != 1:
            raise ValueError('Choose the recorded-data layer from this file')
        uri = choices[0][1]
    layer = QgsVectorLayer(uri,path.stem,'ogr')
    if not layer.isValid():
        raise ValueError('QGIS could not open the recorded input')
    return recording_from_layer(layer)


def recording_from_layer(layer: QgsVectorLayer) -> RecordingSource:
    """Accept individual points or linear tracks, keeping native Z/M and multipart breaks."""
    if layer is None or not isinstance(layer,QgsVectorLayer) or not layer.isValid():
        raise ValueError('Choose a valid recorded GPS layer')
    validate_attribute_table(len(layer.fields()), 0)
    flat = QgsWkbTypes.flatType(layer.wkbType())
    if flat == Qgis.WkbType.Point:
        original = []
        for feature in layer.getFeatures():
            original.append(QgsFeature(feature))
            validate_attribute_table(len(layer.fields()), len(original))
            if len(original) > MAX_OBSERVATIONS:
                raise ValueError('Input exceeds 100,000 observations')
        fields = QgsFields(layer.fields())
        id_name = unique_field(fields,'TN_Source_ID')
        fields.append(QgsField(id_name,QMetaType.Type.QString))
        points = QgsMemoryProviderUtils.createMemoryLayer('RAW GPS OBSERVATIONS',fields,layer.wkbType(),layer.crs())
        features = []
        for f in original:
            feature = QgsFeature(fields)
            feature.setAttributes(f.attributes()+[str(f.id())])
            feature.setGeometry(QgsGeometry(f.geometry()))
            features.append(feature)
        write_verified(points, fields, features)
        # Only trust GDAL GPX structural columns for a GPX provider source.
        source_path = QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source()).get('path', '')
        gpx_segments = (layer.providerType() == 'ogr' and Path(source_path).suffix.lower() == '.gpx'
                        and {'track_fid', 'track_seg_id'} <= set(fields.names()))
        return RecordingSource(points, source_id_field=id_name, original_layer_id=layer.id(),
                               original_layer_name=layer.name(), gpx_provider_segments=gpx_segments)

    if flat not in (Qgis.WkbType.LineString,Qgis.WkbType.MultiLineString):
        raise ValueError('Recorded input must be individual Point or linear LineString/MultiLineString geometry; curves and multipoints are unsupported')
    original = []
    for feature in layer.getFeatures():
        original.append(QgsFeature(feature))
        validate_attribute_table(len(layer.fields()), len(original))
        if len(original) > MAX_OBSERVATIONS:
            raise ValueError('Input exceeds 100,000 track features')
    track_fields = QgsFields(layer.fields())
    track_id_name = unique_field(track_fields, 'TN_Track_ID')
    track_fields.append(QgsField(track_id_name, QMetaType.Type.QString))
    tracks = QgsMemoryProviderUtils.createMemoryLayer('RAW TRACK', track_fields, layer.wkbType(), layer.crs())
    track_features = []
    for original_feature in original:
        track_feature = QgsFeature(track_fields)
        track_feature.setAttributes(original_feature.attributes() + [str(original_feature.id())])
        track_feature.setGeometry(QgsGeometry(original_feature.geometry()))
        track_features.append(track_feature)
    write_verified(tracks, track_fields, track_features)
    fields = QgsFields(layer.fields())
    id_name = unique_field(fields,'TN_Source_ID')
    fields.append(QgsField(id_name,QMetaType.Type.QString))
    group_name = unique_field(fields,'TN_Segment')
    fields.append(QgsField(group_name,QMetaType.Type.QString))
    vertex_name = unique_field(fields,'TN_Vertex_Order')
    fields.append(QgsField(vertex_name,QMetaType.Type.Int))
    point_type = Qgis.WkbType.Point
    if QgsWkbTypes.hasZ(layer.wkbType()):
        point_type = QgsWkbTypes.addZ(point_type)
    if QgsWkbTypes.hasM(layer.wkbType()):
        point_type = QgsWkbTypes.addM(point_type)
    points = QgsMemoryProviderUtils.createMemoryLayer('RAW GPS OBSERVATIONS — track vertices',fields,point_type,layer.crs())
    features, orders = [], []
    for f in original:
        geometry = f.geometry()
        if geometry.isNull() or geometry.isEmpty():
            raise ValueError(f'Feature {f.id()}: empty recorded track')
        parts = geometry.asGeometryCollection() if geometry.isMultipart() else [geometry]
        for part_index,part in enumerate(parts,1):
            group = f'feature_{f.id()}/part_{part_index}'
            ids = []
            for index,point in enumerate(part.vertices(),1):
                identifier = f'{group}/vertex_{index}'
                feature = QgsFeature(fields)
                feature.setAttributes(f.attributes()+[identifier,group,index])
                feature.setGeometry(QgsGeometry(point.clone()))
                features.append(feature)
                validate_attribute_table(len(layer.fields()), len(features))
                ids.append(identifier)
                if len(features) > MAX_OBSERVATIONS:
                    raise ValueError('Track input exceeds 100,000 vertices; split the survey explicitly')
            if not ids:
                raise ValueError(f'{group}: empty track part')
            orders.append((group,tuple(ids)))
    if not features:
        raise ValueError('Recorded layer contains no track observations')
    write_verified(points, fields, features)
    return RecordingSource(points,tracks,id_name,group_name,geometry_orders=tuple(orders),vertex_observations=True,
                           original_layer_id=layer.id(),original_layer_name=layer.name(),track_id_field=track_id_name,
                           issues=(Issue('TRACK_VERTEX_ORDER','Vertex order is recorded geometry order, not inferred timestamp chronology; parent attributes are retained on each vertex.'),))
