"""Independent Stage 7 export inputs/oracle; does not import plugin calculations."""
import csv
from datetime import datetime, timedelta, timezone
import json
from math import degrees
from pathlib import Path
import shutil

from .survey_results_fixture import A, M0, SAMPLE as BASE

SAMPLE = Path(__file__).resolve().parents[1] / 'samples/survey_snapshot'


def generate(folder=SAMPLE):
    folder.mkdir(parents=True, exist_ok=True)
    for source in BASE.iterdir():
        if source.suffix in ('.csv','.geojson'):
            shutil.copyfile(source,folder/source.name)
    start = datetime(2026,10,2,tzinfo=timezone.utc)
    with (folder/'stationary.csv').open('w',newline='') as output:
        writer = csv.writer(output,lineterminator='\n')
        writer.writerow(['longitude','latitude','time','accuracy','hdop'])
        for index in range(3):
            writer.writerow([f'{degrees(3030/A):.18f}',f'{degrees(20/M0):.18f}',
                             (start+timedelta(seconds=index*10)).isoformat(),2,1])
    with (folder/'transitions.csv').open('w',newline='') as output:
        writer = csv.writer(output,lineterminator='\n')
        writer.writerow(['longitude','latitude','time','accuracy','hdop'])
        for index,x in enumerate((10000,10010,10500,10510)):
            writer.writerow([f'{degrees(x/A):.18f}','0',
                (start+timedelta(seconds=100+index*10)).isoformat() if index<3 else '',
                '',1])
    expected = {'planned_count':8,'waypoint_count':108,'file_source_count':10,
        'loaded_snapshot_count':1,'source_count':11,'recording_count':11,
        'raw_count':94,'assigned_count':85,'ambiguous_count':3,'unassigned_count':6,
        'transition_count':2,'qa_observation_count':85,'event_count':2,
        'stationary_event_count':1,'moving_event_count':1,
        'status_counts':{'COMPLETE':4,'INCOMPLETE':2,'REVIEW':1,'NOT_SURVEYED':1},
        'planned_m':1000,'accepted_track_m':740,'progress_m':640,'corridor_m':540,
        'progress_percent':64,'corridor_percent':54,'loaded_source_original_bytes':'UNAVAILABLE_LOADED_LAYER',
        'loaded_point_z':[0.1,0.0,9.0]}
    (folder/'expected.json').write_text(json.dumps(expected,indent=2)+'\n')


if __name__=='__main__':
    generate()
