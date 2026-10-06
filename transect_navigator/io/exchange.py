"""Standard CSV, GeoJSON and GPX writers with no QGIS dependency."""
from __future__ import annotations
import csv
import json
from .. import __version__
from math import isfinite
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET
from ..navigation.waypoints import Transect, Waypoint
from .csv_utils import read_generated_csv

GPX_NS = 'http://www.topografix.com/GPX/1/1'
TN_NS = 'urn:transect-navigator:1'
WAYPOINT_FIELDS = ('Transect_ID', 'Waypoint_ID', 'Sequence', 'Distance_Along_m',
                   'Total_Length_m', 'Latitude', 'Longitude', 'Bearing_Next_deg')
FORMATS = frozenset({'CSV', 'GeoJSON', 'GPX'})


def waypoint_attributes(row: Waypoint) -> dict[str, str | int | float | None]:
    """Shared field names and values across exchange formats."""
    return dict(zip(WAYPOINT_FIELDS, (row.transect_id, row.waypoint_id, row.sequence,
                row.distance_along_m, row.total_length_m, row.coordinate[1],
                row.coordinate[0], row.bearing_next_deg)))


def _validate(transects: Sequence[Transect], rows: Sequence[Waypoint]) -> None:
    """Reject malformed batches before serialization; never emit NaN or bad GPX text."""
    plans = {t.transect_id: t for t in transects}
    if not plans or len(plans) != len(transects) or not rows:
        raise ValueError('Export requires unique planned transects and generated waypoints')
    groups: dict[str, list[Waypoint]] = {identifier: [] for identifier in plans}
    names = set()
    for row in rows:
        if row.transect_id not in groups or row.waypoint_id in names:
            raise ValueError('Waypoint identifiers do not match the planned transects')
        groups[row.transect_id].append(row)
        names.add(row.waypoint_id)
        if not all(isfinite(n) for n in (row.distance_along_m, row.total_length_m)):
            raise ValueError('Waypoint distances must be finite')
        if row.bearing_next_deg is not None and not (isfinite(row.bearing_next_deg) and 0 <= row.bearing_next_deg < 360):
            raise ValueError('Forward bearing must be NULL or within [0, 360) degrees')
    for identifier, group in groups.items():
        ordered = sorted(group, key=lambda r: r.sequence)
        if len(ordered) < 2 or [r.sequence for r in ordered] != list(range(1, len(ordered)+1)):
            raise ValueError(f'Transect {identifier!r} has missing or duplicate waypoint sequences')
        total = ordered[0].total_length_m
        if (total <= 0 or ordered[0].distance_along_m != 0 or ordered[-1].distance_along_m != total
                or any(r.total_length_m != total for r in ordered)
                or any(a.distance_along_m >= b.distance_along_m for a,b in zip(ordered,ordered[1:]))
                or ordered[-1].bearing_next_deg is not None):
            raise ValueError(f'Transect {identifier!r} has inconsistent station distances or final bearing')
    for plan in transects:
        if len(plan.vertices) < 2:
            raise ValueError('Planned geometry must contain at least two vertices')
    coordinates = [p for t in transects for p in t.vertices] + [r.coordinate for r in rows]
    if any(not (isfinite(x) and isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90) for x,y in coordinates):
        raise ValueError('Exchange coordinates must be valid WGS84 longitude/latitude')
    texts = [t.transect_id for t in transects] + [r.waypoint_id for r in rows]
    for value in texts:
        if not value.strip() or any(not (c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF
                                      or 0xE000 <= ord(c) <= 0xFFFD
                                      or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
            raise ValueError('IDs contain blank or unsupported control characters')


def _gpx(transects: Sequence[Transect], rows: Sequence[Waypoint], path: Path) -> None:
    """GPX 1.1: ordered routes for navigation, tracks for original planned vertices.

    Survey attributes use GPX's standard foreign-namespace extensions mechanism.
    GPX has no native station-distance or forward-bearing fields. No timestamps,
    elevations or observed GPS fixes are fabricated.
    """
    ET.register_namespace('', GPX_NS)
    ET.register_namespace('tn', TN_NS)
    def tag(name: str) -> str:
        return f'{{{GPX_NS}}}{name}'
    def longitude_text(value: float) -> str:
        # GPX's longitudeType excludes +180. Normalize its equivalent to -180.
        return f'{((value + 180) % 360 - 180):.15f}'
    root = ET.Element(tag('gpx'), version='1.1', creator=f'Transect Navigator {__version__}')
    metadata = ET.SubElement(root, tag('metadata'))
    ET.SubElement(metadata, tag('name')).text = 'Planned transects and navigation waypoints'
    ET.SubElement(metadata, tag('desc')).text = 'Routes are navigation waypoints; tracks are planned geometry, not GPS observations.'
    groups = {t.transect_id: [] for t in transects}
    for row in rows:
        groups[row.transect_id].append(row)
    for identifier, group in groups.items():
        route = ET.SubElement(root, tag('rte'))
        ET.SubElement(route, tag('name')).text = identifier
        for row in group:
            lon, lat = row.coordinate
            point = ET.SubElement(route, tag('rtept'), lat=f'{lat:.12f}', lon=longitude_text(lon))
            ET.SubElement(point, tag('name')).text = row.waypoint_id
            attributes = waypoint_attributes(row)
            ET.SubElement(point, tag('desc')).text = '; '.join(f'{key}={value}' for key,value in attributes.items() if value is not None)
            extensions = ET.SubElement(point, tag('extensions'))
            for key, value in attributes.items():
                if value is not None:
                    ET.SubElement(extensions, f'{{{TN_NS}}}{key}').text = str(value)
    # GPX schema requires every route before every track.
    for plan in transects:
        track = ET.SubElement(root, tag('trk'))
        ET.SubElement(track, tag('name')).text = plan.transect_id
        ET.SubElement(track, tag('desc')).text = 'Planned transect in original vertex order; not a recorded GPS track.'
        segment = ET.SubElement(track, tag('trkseg'))
        for lon, lat in plan.vertices:
            ET.SubElement(segment, tag('trkpt'), lat=f'{lat:.12f}', lon=longitude_text(lon))
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def write_exchange_files(folder: Path, transects: Sequence[Transect], rows: Sequence[Waypoint],
                         formats: set[str] | frozenset[str]) -> list[Path]:
    """Write selected standard exchange files into a new/private staging directory.

    Public no-overwrite/rollback guarantees belong to publish_package. All coordinates
    here must already be WGS84. Source-CRS originals belong in the companion GeoPackage.
    """
    if not formats <= FORMATS:
        raise ValueError('Unsupported exchange format')
    _validate(transects, rows)
    transects = sorted(transects, key=lambda t:t.transect_id)
    rows = sorted(rows, key=lambda r:(r.transect_id, r.sequence))
    paths: list[Path] = []
    attributes = [waypoint_attributes(r) for r in rows]
    if 'CSV' in formats:
        path = folder / 'navigation_waypoints.csv'
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=WAYPOINT_FIELDS)
            writer.writeheader()
            writer.writerows(attributes)
        paths.append(path)
        path = folder / 'planned_transects.csv'
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('Transect_ID','CRS','WKT','Start_Longitude','Start_Latitude','End_Longitude','End_Latitude'))
            for t in transects:
                wkt = 'LINESTRING (' + ', '.join(f'{x:.17g} {y:.17g}' for x,y in t.vertices) + ')'
                writer.writerow((t.transect_id, 'EPSG:4326', wkt, *t.vertices[0], *t.vertices[-1]))
        paths.append(path)
    if 'GeoJSON' in formats:
        points = [{'type':'Feature','id':r.waypoint_id,'properties':a,
                   'geometry':{'type':'Point','coordinates':list(r.coordinate)}} for r,a in zip(rows,attributes)]
        lines = [{'type':'Feature','id':t.transect_id,'properties':{'Transect_ID':t.transect_id},
                  'geometry':{'type':'LineString','coordinates':t.vertices}} for t in transects]
        for name, features in (('navigation_waypoints',points),('planned_transects',lines)):
            path = folder / f'{name}.geojson'
            path.write_text(json.dumps({'type':'FeatureCollection','features':features},
                                       ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
            paths.append(path)
    if 'GPX' in formats:
        path = folder / 'navigation.gpx'
        _gpx(transects, rows, path)
        paths.append(path)
    return paths


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Allow long planned WKT cells without leaving the process CSV limit changed."""
    return read_generated_csv(path)[1]


def validate_exchange_files(folder: Path, transects: Sequence[Transect], rows: Sequence[Waypoint],
                            formats: set[str] | frozenset[str]) -> None:
    """Reopen generated files and check content, counts, IDs and numeric values.

    This validates our emitted structure, not arbitrary external GPX documents.
    The test suite additionally validates against the official GPX 1.1 XSD.
    """
    transects = sorted(transects, key=lambda t:t.transect_id)
    rows = sorted(rows, key=lambda r:(r.transect_id,r.sequence))
    expected = [waypoint_attributes(r) for r in rows]
    if 'CSV' in formats:
        result = _read_csv(folder/'navigation_waypoints.csv')
        textual = [{k:'' if v is None else str(v) for k,v in r.items()} for r in expected]
        if result != textual:
            raise ValueError('CSV waypoint verification failed')
        plans = _read_csv(folder/'planned_transects.csv')
        expected_plans = []
        for t in transects:
            wkt = 'LINESTRING (' + ', '.join(f'{x:.17g} {y:.17g}' for x,y in t.vertices) + ')'
            expected_plans.append(dict(zip(('Transect_ID','CRS','WKT','Start_Longitude','Start_Latitude','End_Longitude','End_Latitude'),
                                          map(str,(t.transect_id,'EPSG:4326',wkt,*t.vertices[0],*t.vertices[-1])))))
        if plans != expected_plans:
            raise ValueError('CSV transect verification failed')
    if 'GeoJSON' in formats:
        points = json.loads((folder/'navigation_waypoints.geojson').read_text(encoding='utf-8'))
        if ([f['properties'] for f in points['features']] != expected
                or [f['geometry']['coordinates'] for f in points['features']] != [list(r.coordinate) for r in rows]):
            raise ValueError('GeoJSON waypoint verification failed')
        plans = json.loads((folder/'planned_transects.geojson').read_text(encoding='utf-8'))['features']
        if [(f['properties']['Transect_ID'],f['geometry']['coordinates']) for f in plans] != [
                (t.transect_id,[list(p) for p in t.vertices]) for t in transects]:
            raise ValueError('GeoJSON transect verification failed')
    if 'GPX' in formats:
        root = ET.parse(folder/'navigation.gpx').getroot()
        ns = {'g':GPX_NS,'tn':TN_NS}
        if root.tag != f'{{{GPX_NS}}}gpx' or root.get('version') != '1.1' or not root.get('creator'):
            raise ValueError('GPX header verification failed')
        points = root.findall('g:rte/g:rtept', ns)
        if len(points) != len(rows):
            raise ValueError('GPX waypoint count verification failed')
        for point, row, attributes in zip(points, rows, expected):
            ext = point.find('g:extensions', ns)
            values = {} if ext is None else {e.tag.split('}')[1]: e.text for e in ext}
            if values != {k:str(v) for k,v in attributes.items() if v is not None}:
                raise ValueError('GPX attribute verification failed')
            if abs((float(point.get('lon'))-row.coordinate[0]+180)%360-180) > 1e-11 or abs(float(point.get('lat'))-row.coordinate[1]) > 1e-11:
                raise ValueError('GPX coordinate verification failed')
        tracks = root.findall('g:trk',ns)
        if len(tracks) != len(transects):
            raise ValueError('GPX planned transect count verification failed')
        for track, plan in zip(tracks,transects):
            vertices = track.findall('g:trkseg/g:trkpt',ns)
            if track.findtext('g:name',namespaces=ns) != plan.transect_id or len(vertices) != len(plan.vertices):
                raise ValueError('GPX planned transect verification failed')
            for vertex,(lon,lat) in zip(vertices,plan.vertices):
                if abs((float(vertex.get('lon'))-lon+180)%360-180)>1e-11 or abs(float(vertex.get('lat'))-lat)>1e-11:
                    raise ValueError('GPX planned geometry verification failed')
