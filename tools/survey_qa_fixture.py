"""Independent Stage 5 fixture authoring; no Transect Navigator imports.

Equatorial WGS84 analytic placement: longitude=x/a; latitude=y/M(0).
At these ~100 m extents, fixed 0.02 m tolerance covers the local-frame/reference
approximation. Statistical truth uses authored offsets, not production output.
"""
import csv
from datetime import datetime, timedelta, timezone
import json
from math import degrees, sqrt
from pathlib import Path

SAMPLE = Path(__file__).resolve().parents[1] / 'samples/survey_qa'
A = 6378137.0
M0 = A * (1 - 1 / 298.257223563) ** 2
OFFSETS = (0, 1, -1, 1, 12, 12, 12, 1, -1, 1, 0)


def coordinate(x, y):
    return degrees(x / A), degrees(y / M0)


def generate(folder=SAMPLE):
    folder.mkdir(parents=True, exist_ok=True)
    plans = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'Transect_ID': name},
         'geometry': {'type': 'LineString', 'coordinates': [coordinate(0, y), coordinate(100, y)]}}
        for name, y in (('T01', 0), ('T02', 100))]}
    (folder / 'planned.geojson').write_text(json.dumps(plans, indent=2) + '\n')
    # Continuous unlabeled A: first line, transition, reverse second line, unresolved fixes.
    a = [(10 * i, y) for i, y in enumerate(OFFSETS)] + [(100, 40), (100, 70)]
    a += [(x, 100 + (1 if 0 < x < 100 else 0)) for x in range(100, -1, -10)]
    a += [(30, 50), (40, 50), (500, 500)]
    sources = [('A.csv', a, 0), ('B.csv', [(x, 0) for x in range(0, 61, 10)], 1000),
               ('C.csv', [(x, 0) for x in range(100, 49, -10)], 2000)]
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for name, points, offset in sources:
        with (folder / name).open('w', newline='') as stream:
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(['x', 'y', 'time', 'accuracy', 'hdop'])
            for i, point in enumerate(points):
                lon, lat = coordinate(*point)
                writer.writerow([f'{lon:.18f}', f'{lat:.18f}',
                    (start + timedelta(seconds=offset + i * 10)).isoformat(),
                    12 if name == 'A.csv' and 4 <= i <= 6 else 2, 1])
    # These actions are analyst truth; they are NEVER read by production association.
    actions = [
        dict(file='A.csv', rows=list(range(1, 12)), status='ASSIGNED', plan='T01', direction='FORWARD'),
        dict(file='A.csv', rows=[12, 13], status='UNASSIGNED', role='TRANSITION'),
        dict(file='A.csv', rows=list(range(14, 25)), status='ASSIGNED', plan='T02', direction='REVERSE'),
        dict(file='A.csv', rows=[25, 26], status='AMBIGUOUS'),
        dict(file='A.csv', rows=[27], status='UNASSIGNED'),
        dict(file='B.csv', rows=list(range(1, 8)), status='ASSIGNED', plan='T01', direction='FORWARD'),
        dict(file='C.csv', rows=list(range(1, 7)), status='ASSIGNED', plan='T01', direction='REVERSE')]
    expected = {'raw_count': 40, 'assigned': 35, 'ambiguous': 2, 'unassigned': 3,
        'transition': 2, 'visit_count': 4, 'tolerance_m': .02, 'actions': actions,
        'T01': {'observation_count': 24, 'visit_count': 3, 'length_m': 100,
            'mean_m': 42 / 24, 'median_m': 0, 'rmse_m': sqrt(438 / 24), 'p95_m': 12,
            'maximum_m': 12, 'within_percent': [87.5, 87.5, 87.5],
            'recorded_length_m': 2 * sqrt(101) + 4 * sqrt(104) + 2 * sqrt(221) + 130,
            'progress_m': 100, 'corridor_m': 100},
        'T02': {'observation_count': 11, 'visit_count': 1, 'length_m': 100,
            'mean_m': 9 / 11, 'median_m': 1, 'rmse_m': sqrt(9 / 11), 'p95_m': 1,
            'maximum_m': 1, 'within_percent': [100, 100, 100],
            'recorded_length_m': 2 * sqrt(101) + 80, 'progress_m': 100, 'corridor_m': 100},
        'event': {'count': 1, 'observations': 3, 'duration_s': 20, 'span_m': 20,
                  'mean_m': 12, 'maximum_m': 12},
        'complementary_only': {'observation_count': 13, 'visit_count': 2, 'track_m': 110,
                               'progress_m': 100, 'corridor_m': 100},
        'first_visit': {'progress_m': 100, 'corridor_m': 60}}
    (folder / 'expected.json').write_text(json.dumps(expected, indent=2) + '\n')


if __name__ == '__main__':
    generate()
