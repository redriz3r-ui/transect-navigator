"""Independently authored Stage 8 Survey input and review oracle.

Uses the published Vincenty direct ellipsoid formula and GDAL only to serialize
standard fixture files. It imports no Transect Navigator production code.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from math import atan, atan2, cos, degrees, radians, sin, sqrt, tan
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

from osgeo import ogr, osr

osr.UseExceptions()

SAMPLE = Path(__file__).resolve().parents[1] / 'samples/survey_full'
A = 6378137.0
F = 1 / 298.257223563
B = A * (1 - F)
SITE_ORIGIN = (0.0, 0.0)
START = datetime(2026, 10, 3, 8, tzinfo=timezone.utc)
PLACEMENTS = {
    'T01':(500,500), 'T02':(500,700), 'T03':(500,900),
    'T04':(4000,3000), 'T05':(4000,3300), 'T06':(4000,3312),
    'T07':(7500,6500), 'T08':(7500,6800), 'T09':(7500,7100),
    'T10':(500,9000), 'T11':(500,9300), 'T12':(500,9300),
}


def direct(point: tuple[float, float], azimuth_deg: float, distance_m: float) -> tuple[float, float]:
    """WGS84 Vincenty direct solution; test data never call plugin geometry code."""
    longitude, latitude = map(radians, point)
    azimuth = radians(azimuth_deg)
    reduced = atan((1 - F) * tan(latitude))
    sigma1 = atan2(tan(reduced), cos(azimuth))
    sin_alpha = cos(reduced) * sin(azimuth)
    cos2_alpha = 1 - sin_alpha * sin_alpha
    u2 = cos2_alpha * (A * A - B * B) / (B * B)
    big_a = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    big_b = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    sigma = distance_m / (B * big_a)
    for _ in range(30):
        twice = 2 * sigma1 + sigma
        delta = big_b * sin(sigma) * (cos(twice) + big_b / 4 *
            (cos(sigma) * (-1 + 2 * cos(twice) ** 2) - big_b / 6 * cos(twice) *
             (-3 + 4 * sin(sigma) ** 2) * (-3 + 4 * cos(twice) ** 2)))
        following = distance_m / (B * big_a) + delta
        if abs(following - sigma) < 1e-14:
            sigma = following
            break
        sigma = following
    else:
        raise ValueError('Independent Vincenty reference did not converge')
    sin_u, cos_u = sin(reduced), cos(reduced)
    sin_s, cos_s = sin(sigma), cos(sigma)
    latitude2 = atan2(sin_u * cos_s + cos_u * sin_s * cos(azimuth),
                      (1-F) * sqrt(sin_alpha**2 + (sin_u*sin_s - cos_u*cos_s*cos(azimuth))**2))
    lam = atan2(sin_s * sin(azimuth), cos_u*cos_s - sin_u*sin_s*cos(azimuth))
    c = F / 16 * cos2_alpha * (4 + F * (4 - 3*cos2_alpha))
    correction = (1-c) * F * sin_alpha * (sigma + c*sin_s *
        (cos(2*sigma1+sigma) + c*cos_s*(-1+2*cos(2*sigma1+sigma)**2)))
    return (degrees(longitude + lam - correction), degrees(latitude2))


def plan_start(name: str) -> tuple[float, float]:
    x, y = PLACEMENTS[name]
    return direct(direct(SITE_ORIGIN, 90, x), 0, y)


def station_point(name: str, station: float, offset: float = 0) -> tuple[float, float]:
    point = direct(plan_start(name), 90, station)
    return direct(point, 0 if offset >= 0 else 180, abs(offset)) if offset else point


def walk(name: str, stations: list[int], start: datetime, *, special: bool = False,
         gap_after_400: bool = False) -> list[dict]:
    rows = []
    for index, station in enumerate(stations):
        offset = (0 if station in (0,1000) else
            8 if special and 400 <= station <= 500 else
            1 if name == 'T02' else (-1 if station//10 % 2 else 1))
        # The 400 m fix is at +400 s; the next retained 500 m fix is at +900 s.
        timestamp = start + timedelta(seconds=index*10 + (490 if gap_after_400 and station >= 500 else 0))
        lon, lat = station_point(name, station, offset)
        rows.append({'longitude':lon,'latitude':lat,'time':timestamp.isoformat().replace('+00:00','Z'),
                     'accuracy':12 if special and station in (410,450,490) else 2,
                     'hdop':1,'elevation':100.0})
    return rows


def transition(first: dict, following: tuple[float, float], start: datetime,
               interval_s: int) -> list[dict]:
    """Fifteen interior fixes; the endpoint fixes belong to adjacent Visits."""
    rows = []
    for index in range(1,16):
        fraction = index / 16
        rows.append({'longitude':first['longitude'] + fraction*(following[0]-first['longitude']),
                     'latitude':first['latitude'] + fraction*(following[1]-first['latitude']),
                     'time':(start+timedelta(seconds=index*interval_s)).isoformat().replace('+00:00','Z'),
                     'accuracy':3,'hdop':1,'elevation':100.0})
    return rows


def _append_trip(parts: list[dict], next_name: str, start_station: int, when: datetime,
                 interval_s: int) -> datetime:
    points = transition(parts[-1], station_point(next_name,start_station), when, interval_s)
    parts.extend(points)
    return when + timedelta(seconds=16*interval_s)


def rows_by_source() -> dict[str, list[dict]]:
    a = walk('T01',list(range(0,1001,10)),START)
    when = _append_trip(a,'T02',1000,START+timedelta(seconds=1000),10)
    a.extend(walk('T02',list(range(1000,-1,-10)),when))
    when = _append_trip(a,'T03',0,when+timedelta(seconds=1000),10)
    a.extend(walk('T03',list(range(0,601,10)),when))

    b1start = START + timedelta(hours=3)
    b1 = walk('T04',list(range(0,401,10))+list(range(500,1001,10)),b1start,
              gap_after_400=True)
    when = _append_trip(b1,'T05',0,b1start+timedelta(seconds=1400),60)
    shared = [dict(row) for row in walk('T05',list(range(0,1001,10)),when)]
    for row in shared:
        row['longitude'],row['latitude'] = direct((row['longitude'],row['latitude']),0,6)
    b1.extend(shared)

    b2 = walk('T07',list(range(0,1001,10)),START+timedelta(hours=6))

    c1start = START+timedelta(hours=8)
    c1 = walk('T07',list(range(1000,-1,-10)),c1start)
    when = _append_trip(c1,'T08',0,c1start+timedelta(seconds=1000),20)
    c1.extend(walk('T08',list(range(0,1001,10)),when,special=True))

    c2start = START+timedelta(hours=11)
    c2 = walk('T10',list(range(400,1001,10)),c2start)
    when = _append_trip(c2,'T11',0,c2start+timedelta(seconds=600),60)
    c2.extend(walk('T11',list(range(0,1001,10)),when))
    unrelated_start = when+timedelta(seconds=1500)
    for index in range(12):
        point = direct(direct(SITE_ORIGIN,90,9500+index*10),0,9500)
        c2.append({'longitude':point[0],'latitude':point[1],
                   'time':(unrelated_start+timedelta(seconds=index*10)).isoformat().replace('+00:00','Z'),
                   'accuracy':None,'hdop':None,'elevation':100.0})
    return {'A1.csv':a,'B1.gpx':b1,'B2.gpkg':b2,'C1.geojson':c1,'C2.csv':c2}


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open('w',newline='',encoding='utf-8') as stream:
        writer = csv.DictWriter(stream,fieldnames=('longitude','latitude','time','accuracy','hdop','elevation'),
                                lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _write_gpx(path: Path, rows: list[dict]) -> None:
    namespace = 'http://www.topografix.com/GPX/1/1'
    ET.register_namespace('',namespace)
    root = ET.Element(f'{{{namespace}}}gpx',{'version':'1.1','creator':'Stage 8 independent fixture'})
    track = ET.SubElement(root,f'{{{namespace}}}trk')
    ET.SubElement(track,f'{{{namespace}}}name').text = 'Continuous unlabeled recording'
    segment = ET.SubElement(track,f'{{{namespace}}}trkseg')
    for row in rows:
        node=ET.SubElement(segment,f'{{{namespace}}}trkpt',{'lat':repr(row['latitude']),
                                                               'lon':repr(row['longitude'])})
        for name,value in (('ele',row['elevation']),('time',row['time']),('hdop',row['hdop'])):
            ET.SubElement(node,f'{{{namespace}}}{name}').text=str(value)
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)


def _spatial_reference(epsg: int) -> osr.SpatialReference:
    srs=osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def _write_gpkg(path: Path, rows: list[dict]) -> None:
    driver=ogr.GetDriverByName('GPKG')
    if path.exists():
        path.unlink()
    database=driver.CreateDataSource(str(path))
    layer=database.CreateLayer('observations',_spatial_reference(4326),ogr.wkbPoint)
    for name,kind in (('time',ogr.OFTString),('accuracy',ogr.OFTReal),('hdop',ogr.OFTReal),
                      ('elevation',ogr.OFTReal)):
        layer.CreateField(ogr.FieldDefn(name,kind))
    for row in rows:
        feature=ogr.Feature(layer.GetLayerDefn())
        geometry=ogr.Geometry(ogr.wkbPoint)
        geometry.AddPoint_2D(row['longitude'],row['latitude'])
        feature.SetGeometry(geometry)
        for field in ('time','accuracy','hdop','elevation'):
            feature.SetField(field,row[field])
        if layer.CreateFeature(feature) != 0:
            raise ValueError('Could not write independent B2 GeoPackage fixture')
    database=None
    _freeze_gpkg(path)


def _freeze_gpkg(path: Path) -> None:
    """Remove GDAL's wall-clock last_change so fixture bytes are reproducible."""
    with sqlite3.connect(path) as database:
        database.execute("UPDATE gpkg_contents SET last_change='2026-10-03T00:00:00.000Z'")


def _write_geojson(path: Path, rows: list[dict]) -> None:
    data={'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{key:value for key,value in row.items()
                                         if key not in ('longitude','latitude')},
         'geometry':{'type':'Point','coordinates':[row['longitude'],row['latitude']]}}
        for row in rows]}
    path.write_text(json.dumps(data,separators=(',',':'))+'\n',encoding='utf-8')


def _write_plans(folder: Path) -> None:
    site_corners=[SITE_ORIGIN, direct(SITE_ORIGIN,90,10000),
        direct(direct(SITE_ORIGIN,90,10000),0,10000), direct(SITE_ORIGIN,0,10000)]
    boundary={'type':'FeatureCollection','features':[{'type':'Feature',
        'properties':{'Site_ID':'SYN02_SITE','Declared_Width_m':10000,'Declared_Height_m':10000},
        'geometry':{'type':'Polygon','coordinates':[[*site_corners,SITE_ORIGIN]]}}]}
    (folder/'site_boundary.geojson').write_text(json.dumps(boundary,indent=2)+'\n',encoding='utf-8')
    plans={'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'Transect_ID':name,'Site_Block':
            'SW' if index < 3 else 'CENTRAL' if index < 6 else 'NE' if index < 9 else 'NW'},
         'geometry':{'type':'LineString','coordinates':[station_point(name,0),station_point(name,1000)]}}
        for index,name in enumerate(PLACEMENTS)]}
    (folder/'planned.geojson').write_text(json.dumps(plans,indent=2)+'\n',encoding='utf-8')
    source=_spatial_reference(4326)
    target=_spatial_reference(32631)
    transform=osr.CoordinateTransformation(source,target)
    path=folder/'planned_projected.gpkg'
    if path.exists():
        path.unlink()
    database=ogr.GetDriverByName('GPKG').CreateDataSource(str(path))
    layer=database.CreateLayer('planned',target,ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn('Transect_ID',ogr.OFTString))
    for name in PLACEMENTS:
        feature=ogr.Feature(layer.GetLayerDefn())
        feature.SetField('Transect_ID',name)
        line=ogr.Geometry(ogr.wkbLineString)
        for station in (0,1000):
            lon,lat=station_point(name,station)
            x,y,_=transform.TransformPoint(lon,lat,0)
            line.AddPoint_2D(x,y)
        feature.SetGeometry(line)
        if layer.CreateFeature(feature) != 0:
            raise ValueError('Could not write projected plan companion')
    database=None
    _freeze_gpkg(path)


def _review_oracle() -> list[dict]:
    return [
        {'file':'A1.csv','rows':[1,101],'status':'ASSIGNED','plan':'T01','direction':'FORWARD'},
        {'file':'A1.csv','rows':[102,116],'status':'UNASSIGNED','role':'TRANSITION'},
        {'file':'A1.csv','rows':[117,217],'status':'ASSIGNED','plan':'T02','direction':'REVERSE'},
        {'file':'A1.csv','rows':[218,232],'status':'UNASSIGNED','role':'TRANSITION'},
        {'file':'A1.csv','rows':[233,293],'status':'ASSIGNED','plan':'T03','direction':'FORWARD'},
        {'file':'B1.gpx','rows':[1,92],'status':'ASSIGNED','plan':'T04','direction':'FORWARD'},
        {'file':'B1.gpx','rows':[93,107],'status':'UNASSIGNED','role':'TRANSITION'},
        {'file':'B1.gpx','rows':[108,208],'status':'AMBIGUOUS','relevant':['T05','T06']},
        {'file':'B2.gpkg','rows':[1,101],'status':'ASSIGNED','plan':'T07','direction':'FORWARD'},
        {'file':'C1.geojson','rows':[1,101],'status':'ASSIGNED','plan':'T07','direction':'REVERSE'},
        {'file':'C1.geojson','rows':[102,116],'status':'UNASSIGNED','role':'TRANSITION'},
        {'file':'C1.geojson','rows':[117,217],'status':'ASSIGNED','plan':'T08','direction':'FORWARD'},
        {'file':'C2.csv','rows':[1,61],'status':'ASSIGNED','plan':'T10','direction':'FORWARD'},
        {'file':'C2.csv','rows':[62,76],'status':'UNASSIGNED','role':'TRANSITION'},
        {'file':'C2.csv','rows':[77,177],'status':'AMBIGUOUS','relevant':['T11','T12']},
        {'file':'C2.csv','rows':[178,189],'status':'UNASSIGNED','role':'UNKNOWN','relevant':[]},
    ]


def generate(folder: Path = SAMPLE) -> None:
    folder.mkdir(parents=True,exist_ok=True)
    _write_plans(folder)
    sources=rows_by_source()
    assert {key:len(value) for key,value in sources.items()} == {
        'A1.csv':293,'B1.gpx':208,'B2.gpkg':101,'C1.geojson':217,'C2.csv':189}
    _write_csv(folder/'A1.csv',sources['A1.csv'])
    _write_gpx(folder/'B1.gpx',sources['B1.gpx'])
    _write_gpkg(folder/'B2.gpkg',sources['B2.gpkg'])
    _write_geojson(folder/'C1.geojson',sources['C1.geojson'])
    _write_csv(folder/'C2.csv',sources['C2.csv'])
    oracle={'survey_id':'SYN02','site_extent_m':[0,0,10000,10000],
        'source_counts':{key:len(value) for key,value in sources.items()},
        'raw_count':1008,'assigned':719,'ambiguous':202,'unassigned':87,
        'transition':75,'unrelated':12,'visit_count':9,'waypoint_count':1212,
        'status_counts':{'COMPLETE':4,'INCOMPLETE':3,'REVIEW':4,'NOT_SURVEYED':1},
        'statuses':{'T01':'COMPLETE','T02':'COMPLETE','T03':'INCOMPLETE',
                    'T04':'INCOMPLETE','T05':'REVIEW','T06':'REVIEW','T07':'COMPLETE',
                    'T08':'COMPLETE','T09':'NOT_SURVEYED','T10':'INCOMPLETE',
                    'T11':'REVIEW','T12':'REVIEW'},
        'support_m':{'T01':1000,'T02':1000,'T03':600,'T04':900,'T05':0,'T06':0,
                     'T07':1000,'T08':1000,'T09':0,'T10':600,'T11':0,'T12':0},
        'planned_m':12000,'project_support_m':6100,'project_support_percent':100*6100/12000,
        't08':{'count':101,'mean_m':176/101,'rmse_m':sqrt(792/101),
               'median_m':1,'p95_m':8,'maximum_m':8,'event_count':1,
               'event_observations':11,'event_duration_s':100,'event_span_m':100,
               'high_accuracy_count':3,'high_accuracy_m':12,
               'event_mean_accuracy_m':(3*12+8*2)/11},
        'review_actions':_review_oracle()}
    (folder/'expected.json').write_text(json.dumps(oracle,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    generate()
