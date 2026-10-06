"""GeoPackage preservation and coordinated navigation package export."""
from __future__ import annotations
from collections import Counter
import json
import csv
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
from typing import Callable
from qgis.PyQt.QtCore import QMetaType
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsFeature, QgsField, QgsFields,
                       QgsGeometry, QgsMemoryProviderUtils, QgsPointXY, QgsVectorFileWriter,
                       QgsVectorLayer, QgsWkbTypes)
from .prepare import PreparedNavigation
from ..io.exchange import FORMATS, write_exchange_files, validate_exchange_files
from ..io.package import publish_package, package_destination


def _planned_layer(prepared: PreparedNavigation) -> QgsVectorLayer:
    """Normalized IDs linked to the original-direction WGS84 planned vertices."""
    fields = QgsFields()
    fields.append(QgsField('Transect_ID', QMetaType.Type.QString))
    layer = QgsMemoryProviderUtils.createMemoryLayer('Planned transects', fields, QgsWkbTypes.LineString,
                                                    QgsCoordinateReferenceSystem('EPSG:4326'))
    features = []
    for transect in sorted(prepared.transects, key=lambda t:t.transect_id):
        feature = QgsFeature(fields)
        feature.setAttributes([transect.transect_id])
        feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in transect.vertices]))
        features.append(feature)
    if not layer.dataProvider().addFeatures(features)[0]:
        raise ValueError('Could not construct planned transect export layer')
    layer.updateExtents()
    return layer


def _verify_layer(path: Path, name: str, original: QgsVectorLayer,
                  crs_equivalent: Callable[[QgsCoordinateReferenceSystem,
                                            QgsCoordinateReferenceSystem], bool] | None = None) -> None:
    """Reopen through OGR and verify geometry and all original field values."""
    reopened = QgsVectorLayer(f'{path}|layername={name}', name, 'ogr')
    same_crs = ((lambda left, right: left == right) if crs_equivalent is None
                else crs_equivalent)
    if (not reopened.isValid() or reopened.featureCount() != original.featureCount()
            or not same_crs(reopened.crs(), original.crs())):
        raise ValueError(f'GeoPackage verification failed for {name}: count, CRS or layer could not be preserved')
    names = original.fields().names()
    if not set(names) <= set(reopened.fields().names()):
        raise ValueError(f'GeoPackage lost fields from {name}')
    def records(layer: QgsVectorLayer) -> Counter:
        # repr retains type distinctions; complex unsupported conversions fail rather than silently lose data.
        return Counter((bytes(f.geometry().asWkb()), tuple(repr(f[n]) for n in names)) for f in layer.getFeatures())
    if records(original) != records(reopened):
        raise ValueError(f'GeoPackage changed geometry or attributes in {name}; check field types before export')


def _restore_null_geometries(path: Path, name: str, layer: QgsVectorLayer,
                             key_field: str) -> None:
    """Keep absent event lines NULL when QGIS/GDAL writes them as EMPTY lines."""
    if layer.fields().indexFromName(key_field) < 0:
        raise ValueError(f'{name} has no stable key for NULL geometry preservation')
    features = list(layer.getFeatures())
    keys = [feature[key_field] for feature in features]
    if any(key is None or not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError(f'{name} needs unique nonempty text keys for NULL geometry preservation')
    null_keys = [feature[key_field] for feature in features if feature.geometry().isNull()]
    if not null_keys:
        return
    exported = QgsVectorLayer(f'{path}|layername={name}', name, 'ogr')
    if not exported.isValid() or exported.featureCount() != len(features):
        raise ValueError(f'{name}: could not reopen events for NULL geometry preservation')
    matches: dict[str, list[QgsFeature]] = {}
    for feature in exported.getFeatures():
        matches.setdefault(feature[key_field], []).append(feature)
    changes = {}
    for key in null_keys:
        rows = matches.get(key, [])
        if len(rows) != 1 or not rows[0].geometry().isEmpty():
            raise ValueError(f'{name}: could not identify the empty event geometry {key!r} after writing')
        changes[rows[0].id()] = QgsGeometry()
    if not exported.dataProvider().changeGeometryValues(changes):
        raise ValueError(f'{name}: could not preserve NULL event geometry in GeoPackage')
    exported.reload()
    restored = {feature[key_field]: feature.geometry().isNull() for feature in exported.getFeatures()}
    if any(not restored.get(key, False) for key in null_keys):
        raise ValueError(f'{name}: NULL event geometry could not be verified')


def write_geopackage_layers(path: Path, layers: list[tuple[str, QgsVectorLayer]],
                           context: QgsCoordinateTransformContext, *,
                           null_geometry_keys: dict[str, str] | None = None,
                           crs_equivalent: Callable[[QgsCoordinateReferenceSystem,
                                                     QgsCoordinateReferenceSystem], bool] | None = None) -> None:
    """Write a new staged GeoPackage and verify every geometry/attribute/CRS on reopen."""
    if path.exists() or path.is_symlink():
        raise ValueError('GeoPackage destination already exists; nothing will be overwritten')
    if not layers or len({name.casefold() for name, _ in layers}) != len(layers):
        raise ValueError('GeoPackage needs uniquely named layers')
    null_geometry_keys = null_geometry_keys or {}
    if not set(null_geometry_keys) <= {name for name, _ in layers}:
        raise ValueError('NULL-geometry preservation names an absent GeoPackage layer')
    for index, (name, layer) in enumerate(layers):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'GPKG'
        options.fileEncoding = 'UTF-8'
        options.layerName = name
        options.actionOnExistingFile = (QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile if index == 0
                                        else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
        # Avoid stealing any original field (including a user's fid/geom field).
        used = {n.lower() for n in layer.fields().names()}
        fid, geom = '_tn_rowid', '_tn_geometry'
        while fid.lower() in used:
            fid += '_'
        while geom.lower() in used:
            geom += '_'
        options.layerOptions = [f'FID={fid}', f'GEOMETRY_NAME={geom}']
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(path), context, options)
        if result[0] != QgsVectorFileWriter.WriterError.NoError:
            raise ValueError(f'Could not export {name} to GeoPackage: {result[1]}')
        if name in null_geometry_keys:
            _restore_null_geometries(path, name, layer, null_geometry_keys[name])
    for name, layer in layers:
        _verify_layer(path, name, layer, crs_equivalent)
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as connection:
        if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('GeoPackage integrity check failed')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('GeoPackage foreign-key integrity check failed')
        tables = {r[0] for r in connection.execute('SELECT table_name FROM gpkg_contents')}
        if not {name for name,_ in layers} <= tables:
            raise ValueError('GeoPackage layer registration is incomplete')


def _write_geopackage(path: Path, prepared: PreparedNavigation) -> None:
    layers = [('source_transects', prepared.source), ('planned_transects', _planned_layer(prepared)),
              ('navigation_waypoints', prepared.waypoints)]
    write_geopackage_layers(path, layers, prepared.context)


def export_navigation_package(prepared: PreparedNavigation, parent: Path, name: str,
                              formats: set[str] | frozenset[str] = frozenset({'CSV','GeoJSON','GPX'})) -> Path:
    """Publish a new folder containing a complete GeoPackage and chosen companions.

    The input is one run's snapshot, never a reread of a potentially changed source.
    Source CRS geometry and original attributes survive in source_transects. GPX,
    GeoJSON and CSV carry WGS84 plans and navigation fields. Nothing is overwritten.
    """
    package_destination(parent, name)
    if not formats <= FORMATS:
        raise ValueError('Unsupported navigation export format')
    def write(stage: Path) -> None:
        _write_geopackage(stage/'navigation.gpkg', prepared)
        write_exchange_files(stage, prepared.transects, prepared.rows, formats)
        validate_exchange_files(stage, prepared.transects, prepared.rows, formats)
        provenance = json.loads(prepared.waypoints.customProperty('transect_navigator/provenance'))
        lines = [
            'Transect Navigator navigation package',
            '',
            'navigation.gpkg is the authoritative GIS copy:',
            '  source_transects: original selected/input geometry, CRS and attributes.',
            '  planned_transects: normalized IDs and WGS84 vertices in original direction.',
            '  navigation_waypoints: generated WGS84 points and all navigation attributes.',
            '',
            'Optional files use WGS84 longitude/latitude. Sort waypoints by Transect_ID and Sequence.',
            'GPX routes are navigation points; GPX tracks are PLANNED transects, not recorded GPS.',
            'GPX applications may ignore survey extensions or truncate IDs. Retain the GeoPackage.',
            'Final or coincident-next waypoint bearing is NULL (blank in CSV; omitted in GPX).',
            'CSV IDs are exact text; import ID columns as text in spreadsheet applications.',
            'Geodesic spacing follows WGS84 legs between input vertices, not projected straight lines.',
            '', 'Run settings and provenance:',
        ]
        lines.extend(f'{key}: {value}' for key,value in sorted(provenance.items()))
        (stage/'README.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    try:
        return publish_package(parent, name, write)
    except (OSError, sqlite3.Error, ET.ParseError, csv.Error) as error:
        raise ValueError(f'Export failed: {error}. Check folder permissions, available space, and file locks. No existing package was overwritten.') from error
