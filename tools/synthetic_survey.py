"""Deterministic survey fixture and portable calculation exercise (no QGIS emulation).

Run from the repository: python3 -m tools.synthetic_survey --help
The equatorial formulas below are a fixture construction/oracle only, never a
replacement for the production PyQGIS CRS/ellipsoid adapters.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from math import atan2, degrees, hypot, radians, sqrt, isfinite
from pathlib import Path
from transect_navigator import __version__
from transect_navigator.analysis.observations import Observation, normalize_observations
from transect_navigator.geometry.planar import Point2D
from transect_navigator.navigation.waypoints import Transect, generate_waypoints
from transect_navigator.qaqc.deviation import DirectedTransect
from transect_navigator.qaqc.statistics import SummaryObservation, summarize, track_length
from transect_navigator.qaqc.events import EventObservation, EventOptions, detect_events
from transect_navigator.io.recorded import read_csv
from transect_navigator.io.exchange import write_exchange_files, validate_exchange_files
from transect_navigator.io.analysis_summary import completed_summary, write_summary_csv
from transect_navigator.io.package import publish_package

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT/'samples'/'synthetic_survey'
TRANSECT_ID = 'SYN01'
START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ANALYSIS_TIME = '2026-01-01T12:06:00+00:00'
A = 6378137.0
F = 1/298.257223563
M0 = A*(1-F)**2
EVENT_OPTIONS = EventOptions(threshold_m=5, minimum_duration_s=15,
    minimum_station_span_m=15, maximum_time_gap_s=20, maximum_spatial_gap_m=20)
OFFSETS = (
    0,.25,-.25,.5,-.5,.75,-.75,.25,-.25,.5,-.5,.25,-.25,.75,-.75,.25,-.25,.5,-.5,0,
    .8,1.2,1.6,2,2.4,2.8,3.2,3.6,4,4.4,4.8,
    6,8,9,12,14,14,14,12,9,8,6,6,
    4.4,3.4,2.4,1.4,
    .5,-.5,.25,-.25,.75,-.75,.25,-.25,.5,-.5,.25,-.25,.25,0,
)
FIELDS = ('Observation_ID','Timestamp','Longitude','Latitude','Horizontal_Accuracy_m',
          'HDOP','PDOP','Satellites','Fix_Type','Elevation_m')


def geographic(x: float, y: float) -> tuple[float, float]:
    """Place a known local fixture at the WGS84 equator (northing is first order)."""
    return degrees(x/A), degrees(y/M0)


def fixture_metres(longitude: float, latitude: float) -> Point2D:
    """Invert fixture coordinates only; this is not a general geographic CRS transform."""
    return Point2D(radians(longitude)*A, radians(latitude)*M0)


def observations() -> list[dict[str, str]]:
    rows = []
    for index, y in enumerate(OFFSETS):
        x = index*5
        longitude, latitude = geographic(x,y)
        degraded = 34 <= index <= 38
        accuracy = (8,10,12,10,8)[index-34] if degraded else 2
        values = (f'OBS{index+1:03d}', (START+timedelta(seconds=index*5)).isoformat(),
                  f'{longitude:.18f}',f'{latitude:.18f}',str(accuracy),
                  '3.0' if degraded else '0.8', '4.2' if degraded else '1.2',
                  '5' if degraded else '12', '3d', '25.0')
        rows.append(dict(zip(FIELDS,values)))
    return rows


def expected() -> dict:
    """Frozen analytic expectations, independent of production statistic functions.

    Absolute offsets sum to 3473/20 m; squared offsets sum to 569317/400 m².
    Type-7 p95 is sorted index 57 (61 samples), exactly 12 m. The five offsets
    over 10 m reject six 5 m coverage intervals, from station 165 through 195.
    """
    return {
        'transect_id':TRANSECT_ID, 'planned_length_m':300, 'observation_count':61,
        'sampling_interval_s':5, 'waypoint_spacing_m':10, 'waypoint_count':31,
        'forward_bearing_deg':90, 'sum_absolute_m':3473/20,
        'sum_squared_m2':569317/400, 'mean_absolute_m':3473/1220,
        'median_absolute_m':.75, 'rmse_m':sqrt(569317/24400),
        'p95_absolute_m':12, 'maximum_absolute_m':14,
        'within_counts':[42,49,56], 'thresholds_m':[3,5,10],
        'within_percent':[100*n/61 for n in (42,49,56)],
        'recorded_track_length_m':306.8261303850714,
        'coverage_support_m':270, 'coverage_support_pct':90,
        'coverage_accepted_pairs':54, 'coverage_rejected_pairs':6,
        'event_count':1, 'event_start_station_m':155, 'event_end_station_m':210,
        'event_length_m':55, 'event_duration_s':55, 'event_observation_count':12,
        'event_mean_deviation_m':118/12, 'event_maximum_deviation_m':14,
        'event_start_time':'2026-01-01T12:02:35Z', 'event_end_time':'2026-01-01T12:03:30Z',
        'gnss_mean_accuracy_m':160/61, 'event_mean_accuracy_m':62/12,
        'event_options':asdict(EVENT_OPTIONS), 'distance_tolerance_m':.0001,
        'percentage_tolerance':.0001, 'bearing_tolerance_deg':.00001,
        'stations_m':[i*5 for i in range(61)], 'signed_offsets_m':list(OFFSETS),
    }


def collection(features: list[dict]) -> dict:
    return {'type':'FeatureCollection','features':features}


def point_feature(row: dict, properties: dict | None = None) -> dict:
    return {'type':'Feature','properties':dict(row) if properties is None else properties,
            'geometry':{'type':'Point','coordinates':[float(row['Longitude']),float(row['Latitude'])]}}


def encoded(value: object) -> bytes:
    return (json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')


def dataset_files() -> dict[str, bytes]:
    rows=observations()
    csv_text=io.StringIO(newline='')
    writer=csv.DictWriter(csv_text,fieldnames=FIELDS,lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    plan=collection([{'type':'Feature','properties':{'Transect_ID':TRANSECT_ID},
        'geometry':{'type':'LineString','coordinates':[geographic(0,0),geographic(300,0)]}}])
    return {'planned_transect.geojson':encoded(plan), 'gps_observations.csv':csv_text.getvalue().encode(),
        'gps_observations.geojson':encoded(collection([point_feature(row) for row in rows])),
        'expected.json':encoded(expected())}


def generate(destination: Path) -> Path:
    """Create a new dataset folder; never overwrite a prior dataset."""
    def write(stage: Path) -> None:
        for name,content in dataset_files().items():
            (stage/name).write_bytes(content)
    return publish_package(destination.parent,destination.name,write)


def check_dataset(folder: Path) -> None:
    for name,content in dataset_files().items():
        if (folder/name).read_bytes()!=content:
            raise ValueError(f'Synthetic dataset changed: {name}')


class FixtureMetric:
    """Cartesian metre oracle for core stationing, not a stand-in PyQGIS runtime."""
    def distance(self,a,b):
        return hypot(b[0]-a[0],b[1]-a[1])

    def interpolate(self,a,b,distance):
        f=distance/self.distance(a,b)
        return a[0]+f*(b[0]-a[0]),a[1]+f*(b[1]-a[1])

    def bearing(self,a,b):
        return degrees(atan2(b[0]-a[0],b[1]-a[1]))%360


def near(actual: float, wanted: float, tolerance: float = .0001) -> None:
    if actual is None or not isfinite(actual) or abs(actual-wanted)>tolerance:
        raise AssertionError(f'Expected {wanted} ± {tolerance}, got {actual}')


def verify_measurements(rows, summary, detection) -> None:
    """Check known numbers, directions and event boundaries, not file existence."""
    oracle=expected()
    assert len(rows)==61 and summary.observation_count==61
    for index,row in enumerate(rows):
        near(row.absolute_m,abs(OFFSETS[index]))
        near(row.signed_m,OFFSETS[index])
        near(row.distance_along_m,index*5)
        near(row.progress_pct,index*5/3)
        near(row.nearest.x,index*5)
        near(row.nearest.y,0)
    d=summary.observed_deviation
    for actual,key in ((summary.planned_length_m,'planned_length_m'),
            (summary.recorded_length_m,'recorded_track_length_m'),(d.mean,'mean_absolute_m'),
            (d.median,'median_absolute_m'),(d.rmse,'rmse_m'),(d.p95,'p95_absolute_m'),
            (d.maximum,'maximum_absolute_m'),(summary.reported_gnss.mean,'gnss_mean_accuracy_m'),
            (summary.coverage.supported_length_m,'coverage_support_m'),
            (summary.coverage.supported_percent,'coverage_support_pct')):
        near(actual,oracle[key])
    for actual,wanted in zip(summary.within_percent,oracle['within_percent']):
        near(actual,wanted)
    assert summary.coverage.accepted_pair_count==54 and summary.coverage.rejected_pair_count==6
    assert len(detection.events)==1 and not detection.excluded_groups
    event=detection.events[0]
    assert event.event_id=='SYN01_E001' and event.observation_count==12
    assert event.start_time==oracle['event_start_time'] and event.end_time==oracle['event_end_time']
    for actual,key in ((event.start_distance_along_m,'event_start_station_m'),
            (event.end_distance_along_m,'event_end_station_m'),(event.event_length_m,'event_length_m'),
            (event.duration_s,'event_duration_s'),(event.mean_deviation_m,'event_mean_deviation_m'),
            (event.maximum_deviation_m,'event_maximum_deviation_m'),
            (event.mean_gnss_accuracy_raw,'event_mean_accuracy_m')):
        near(actual,oracle[key])


def run_portable(sample: Path, destination: Path) -> Path:
    """Exercise real core/IO functions with analytic fixture metres; no GeoPackage claim."""
    if not __debug__:
        raise RuntimeError('Validation requires assertions; do not run Python with -O')
    check_dataset(sample)
    def write(stage: Path) -> None:
        navigation=generate_waypoints([Transect(TRANSECT_ID,((0,0),(300,0)))],10,FixtureMetric())
        assert len(navigation)==31
        for index,waypoint in enumerate(navigation):
            assert waypoint.sequence==index+1 and waypoint.waypoint_id==f'SYN01_{index+1:03d}'
            near(waypoint.distance_along_m,index*10)
            if index<30:
                near(waypoint.bearing_next_deg,90)
            else:
                assert waypoint.bearing_next_deg is None
        geographic_navigation=[replace(w,coordinate=geographic(*w.coordinate)) for w in navigation]
        plan=[Transect(TRANSECT_ID,(geographic(0,0),geographic(300,0)))]
        nav_folder=stage/'navigation'
        nav_folder.mkdir()
        write_exchange_files(nav_folder,plan,geographic_navigation,{'CSV','GPX','GeoJSON'})
        validate_exchange_files(nav_folder,plan,geographic_navigation,{'CSV','GPX','GeoJSON'})
        recording=read_csv(sample/'gps_observations.csv','Longitude','Latitude')
        attrs={p.source_id:dict(p.attributes) for p in recording.points}
        normalized=normalize_observations([Observation(p.source_id,p.group,*p.coordinate,
            attrs[p.source_id]['Timestamp'],attrs[p.source_id]['Horizontal_Accuracy_m']) for p in recording.points])
        assert not normalized.issues and len(normalized.ordered_groups)==1
        ordered=[r.raw for r in normalized.observations]
        assert [attrs[r.source_id]['Observation_ID'] for r in ordered]==[f'OBS{i:03d}' for i in range(1,62)]
        positions=[fixture_metres(r.longitude,r.latitude) for r in ordered]
        compared=DirectedTransect([Point2D(0,0),Point2D(300,0)]).compare_many(positions)
        inputs=[SummaryObservation(r.source_id,r.group,d.absolute_m,d.distance_along_m,r.timestamp,r.accuracy)
                for r,d in zip(ordered,compared)]
        summary=summarize(TRANSECT_ID,300,inputs,recorded_length_m=track_length([positions]))
        events=detect_events(TRANSECT_ID,[EventObservation(r,p) for r,p in zip(inputs,positions)],EVENT_OPTIONS)
        verify_measurements(compared,summary,events)
        raw=collection([point_feature(attrs[r.source_id]) for r in ordered])
        qa=collection([point_feature(attrs[r.source_id],{**attrs[r.source_id],
            'Absolute_Deviation_m':d.absolute_m,'Signed_Deviation_m':d.signed_m,'Station_m':d.distance_along_m})
            for r,d in zip(ordered,compared)])
        track=collection([{'type':'Feature','properties':{'Order_Basis':'explicit UTC timestamps'},
            'geometry':{'type':'LineString','coordinates':[[r.longitude,r.latitude] for r in ordered]}}])
        for name,value in (('raw_gps_observations',raw),('qa_observations',qa),('raw_track',track)):
            (stage/f'{name}.geojson').write_bytes(encoded(value))
            assert json.loads((stage/f'{name}.geojson').read_text())==value
        lookup={r.source_id:r for r in ordered}
        event=events.events[0]
        event_geojson=collection([{'type':'Feature','properties':asdict(event),
            'geometry':{'type':'LineString','coordinates':[[lookup[i].longitude,lookup[i].latitude] for i in event.source_ids]}}])
        (stage/'deviation_events.geojson').write_bytes(encoded(event_geojson))
        assert json.loads((stage/'deviation_events.geojson').read_text())==json.loads(encoded(event_geojson))
        values={'Transect_ID':TRANSECT_ID,'Observation_Count':61,'Planned_Length_m':300,
            'Recorded_Length_m':summary.recorded_length_m,'Mean_Deviation_m':summary.observed_deviation.mean,
            'Median_Deviation_m':summary.observed_deviation.median,'RMSE_m':summary.observed_deviation.rmse,
            'P95_Deviation_m':summary.observed_deviation.p95,'Maximum_Deviation_m':summary.observed_deviation.maximum,
            'Coverage_Support_pct':summary.coverage.supported_percent}
        for label,t,p in zip('ABC',summary.options.thresholds_m,summary.within_percent):
            values[f'Threshold_{label}_m']=t
            values[f'Within_{label}_pct']=p
        values=completed_summary(values,spacing_m=10,event_options=asdict(EVENT_OPTIONS),
            event_detection={'event_count':1,'excluded_groups':[]},analysis_time=ANALYSIS_TIME,plugin_version=__version__)
        write_summary_csv(stage/'portable_summary.csv',values)
        (stage/'validation.json').write_bytes(encoded({'mode':'portable analytic fixture; NOT PyQGIS',
            'verified':True,'qgis_runtime_executed':False,'summary':summary.as_dict(),
            'events':asdict(events),'plugin_version':__version__}))
    return publish_package(destination.parent,destination.name,write)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample',type=Path,default=SAMPLE)
    operation=parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--generate',type=Path,help='Create a NEW dataset directory')
    operation.add_argument('--check',action='store_true',help='Verify committed sample bytes')
    operation.add_argument('--portable',type=Path,help='Run portable validation into a NEW directory')
    args=parser.parse_args()
    if args.generate:
        print(generate(args.generate))
    elif args.check:
        check_dataset(args.sample)
        print('Synthetic dataset matches deterministic generator')
    else:
        print(run_portable(args.sample,args.portable))


if __name__=='__main__':
    main()
