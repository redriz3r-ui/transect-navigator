"""Independent Stage 6 construction; no production plugin imports/calculations.

Equatorial longitude = distance / WGS84 semi-major axis. Northing uses M(0).
Fixed 0.02 m tolerance covers local-frame approximation over <=200 m lines.
Truth is authored membership and elementary lengths, not generated QA results.
"""
import csv
from datetime import datetime, timedelta, timezone
import json
from math import degrees
from pathlib import Path

SAMPLE = Path(__file__).resolve().parents[1] / 'samples/survey_results'
A = 6378137.0
M0 = A * (1 - 1 / 298.257223563) ** 2


def generate(folder=SAMPLE):
    folder.mkdir(parents=True, exist_ok=True)
    lengths = [100, 200, 100, 100, 100, 100, 100, 200]
    plans = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'Transect_ID': f'T{i+1:02d}', 'Original_note': 'retain'},
         'geometry': {'type': 'LineString', 'coordinates': [[degrees(x/A), 0] for x in (i*500, i*500+length)]}}
        for i, length in enumerate(lengths)]}
    (folder / 'planned.geojson').write_text(json.dumps(plans, indent=2)+'\n')
    sources = [
        ('forward.csv', 0, list(range(0,101,10)), 0, 'S01'),
        ('reverse.csv', 1, list(range(200,-1,-10)), 0, 'S02'),
        ('partial.csv', 2, list(range(0,61,10)), 0, 'S01'),
        ('gap.csv', 3, list(range(0,41,10))+list(range(60,101,10)), 0, 'S01'),
        ('ambiguous.csv', 4, [40,50,60], 0, None),
        ('repeat_a.csv', 5, list(range(0,101,10)), 0, 'S01'),
        ('repeat_b.csv', 5, list(range(100,-1,-10)), 0, 'S02'),
        ('poor_qa.csv', 6, list(range(0,101,10)), 20, 'S02')]
    actions = []
    start = datetime(2026,10,1,tzinfo=timezone.utc)
    for index, (name, plan, stations, offset, surveyor) in enumerate(sources):
        with (folder/name).open('w', newline='') as stream:
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(['longitude','latitude','time','accuracy','hdop'])
            for j, station in enumerate(stations):
                seconds = index*3600+j*10+(600 if name=='gap.csv' and station>=60 else 0)
                writer.writerow([f'{degrees((plan*500+station)/A):.18f}', f'{degrees(offset/M0):.18f}',
                    (start+timedelta(seconds=seconds)).isoformat(), 12 if offset else 2, 1])
        actions.append({'file':name,'plan':f'T{plan+1:02d}','surveyor':surveyor,
            'status':'AMBIGUOUS' if name=='ambiguous.csv' else 'ASSIGNED',
            'direction':'REVERSE' if stations[-1]<stations[0] else 'FORWARD'})
    expected = {'actions':actions,'tolerance_m':.02,'raw_count':85,'assigned_count':82,'ambiguous_count':3,
        'planned_m':1000,'progress_m':640,'corridor_m':540,'track_m':740,
        'progress_percent':64,'corridor_percent':54,'naive_mean_percent':67.5,
        'status_counts':{'COMPLETE':4,'INCOMPLETE':2,'REVIEW':1,'NOT_SURVEYED':1},
        'statuses':dict(zip([f'T{i:02d}' for i in range(1,9)],
            ['COMPLETE','COMPLETE','INCOMPLETE','INCOMPLETE','REVIEW','COMPLETE','COMPLETE','NOT_SURVEYED'])),
        'progress_by_plan':dict(zip([f'T{i:02d}' for i in range(1,9)],[100,200,60,80,0,100,100,0]))}
    (folder/'expected.json').write_text(json.dumps(expected,indent=2)+'\n')


if __name__ == '__main__':
    generate()
