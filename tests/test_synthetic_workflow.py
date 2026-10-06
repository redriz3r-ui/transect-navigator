"""Portable end-to-end synthetic workflow with independently fixed numeric oracles."""
import csv
from fractions import Fraction
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.synthetic_survey import (SAMPLE,OFFSETS,FIELDS,observations,expected,check_dataset,
    dataset_files,generate,run_portable,fixture_metres,near)
from transect_navigator.analysis.observations import Observation,normalize_observations
from transect_navigator.io.recorded import read_csv


class SyntheticWorkflowTests(unittest.TestCase):
    def test_optimized_python_cannot_report_unchecked_success(self):
        for module,function in (('synthetic_survey','run_portable'),
                                ('qgis_synthetic_survey','run_qgis_workflow')):
            code=(f'from tools.{module} import {function}; '
                  f'{function}(None,None)')
            result=subprocess.run([sys.executable,'-B','-O','-c',code],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Validation requires assertions',result.stderr)

    def test_committed_dataset_is_deterministic(self):
        check_dataset(SAMPLE)
        with TemporaryDirectory() as folder:
            first=generate(Path(folder)/'first')
            second=generate(Path(folder)/'second')
            for name,content in dataset_files().items():
                self.assertEqual((first/name).read_bytes(),content)
                self.assertEqual((second/name).read_bytes(),content)

    def test_oracle_is_independently_checkable_from_offsets(self):
        values=[abs(Fraction(str(v))) for v in OFFSETS]
        self.assertEqual(len(values),61)
        self.assertEqual(sum(values),Fraction(3473,20))
        self.assertEqual(sum(v*v for v in values),Fraction(569317,400))
        self.assertEqual(sorted(values)[30],Fraction(3,4))
        self.assertEqual(sorted(values)[57],12)
        self.assertEqual([sum(v<=t for v in values) for t in (3,5,10)],[42,49,56])
        self.assertEqual([i for i,v in enumerate(values) if v>5],list(range(31,43)))
        self.assertEqual([i for i,v in enumerate(values) if v>10],list(range(34,39)))
        self.assertEqual(expected()['event_options']['minimum_duration_s'],15)

    def test_portable_pipeline_outputs_numeric_results_and_preserves_input(self):
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SAMPLE.iterdir() if p.is_file()}
        with TemporaryDirectory() as folder:
            output=run_portable(SAMPLE,Path(folder)/'run')
            report=json.loads((output/'validation.json').read_text())
            self.assertFalse(report['qgis_runtime_executed'])
            self.assertTrue(report['verified'])
            self.assertAlmostEqual(report['summary']['observed_deviation']['mean'],3473/1220)
            self.assertEqual(report['events']['events'][0]['observation_count'],12)
            self.assertAlmostEqual(report['summary']['coverage']['supported_percent'],90)
            raw=json.loads((output/'raw_gps_observations.geojson').read_text())['features']
            qa=json.loads((output/'qa_observations.geojson').read_text())['features']
            self.assertEqual(len(raw),61)
            for raw_point,qa_point,row in zip(raw,qa,observations()):
                self.assertEqual(raw_point['properties'],row)
                self.assertEqual(qa_point['geometry'],raw_point['geometry'])
                self.assertEqual(qa_point['properties']['Horizontal_Accuracy_m'],row['Horizontal_Accuracy_m'])
            event=json.loads((output/'deviation_events.geojson').read_text())['features'][0]
            self.assertEqual(event['properties']['event_id'],'SYN01_E001')
            self.assertEqual(len(event['geometry']['coordinates']),12)
            self.assertEqual(event['geometry']['coordinates'],
                             [point['geometry']['coordinates'] for point in raw[31:43]])
            with (output/'portable_summary.csv').open(newline='') as stream:
                summary=next(csv.DictReader(stream))
            self.assertAlmostEqual(float(summary['RMSE_m']),expected()['rmse_m'])
            self.assertEqual(summary['Event_Min_Duration_s'],'15')
            self.assertEqual(summary['Event_Min_Station_Span_m'],'15')
        self.assertEqual(hashes,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SAMPLE.iterdir() if p.is_file()})

    def test_timestamp_order_not_storage_order(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'reordered.csv'
            with path.open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(observations()[::-1])
            loaded=read_csv(path,'Longitude','Latitude')
            attrs={p.source_id:dict(p.attributes) for p in loaded.points}
            self.assertEqual(attrs[loaded.points[0].source_id]['Observation_ID'],'OBS061')
            normalized=normalize_observations([Observation(p.source_id,p.group,*p.coordinate,
                attrs[p.source_id]['Timestamp'],attrs[p.source_id]['Horizontal_Accuracy_m']) for p in loaded.points])
            ids=[attrs[r.raw.source_id]['Observation_ID'] for r in normalized.observations]
            self.assertEqual(ids,[f'OBS{i:03d}' for i in range(1,62)])
            self.assertEqual(loaded.points[0].source_id,'row_2')

    def test_corrupted_source_or_output_is_not_treated_as_success(self):
        with TemporaryDirectory() as folder:
            sample=generate(Path(folder)/'sample')
            (sample/'gps_observations.csv').write_text('corrupted')
            with self.assertRaisesRegex(ValueError,'dataset changed'):
                run_portable(sample,Path(folder)/'run')
            self.assertFalse((Path(folder)/'run').exists())
        for value in (None,float('nan'),float('inf'),10):
            with self.assertRaises(AssertionError):
                near(value,0)

    def test_no_overwrite_of_synthetic_runs(self):
        with TemporaryDirectory() as folder:
            path=run_portable(SAMPLE,Path(folder)/'run')
            before=(path/'validation.json').read_bytes()
            with self.assertRaises(ValueError):
                run_portable(SAMPLE,path)
            self.assertEqual((path/'validation.json').read_bytes(),before)

    def test_navigation_export_count_order_bearing_and_spacing(self):
        with TemporaryDirectory() as folder:
            output=run_portable(SAMPLE,Path(folder)/'run')
            with (output/'navigation/navigation_waypoints.csv').open(newline='') as stream:
                points=list(csv.DictReader(stream))
            self.assertEqual(len(points),31)
            for index,row in enumerate(points):
                self.assertEqual(row['Waypoint_ID'],f'SYN01_{index+1:03d}')
                self.assertEqual(int(row['Sequence']),index+1)
                self.assertEqual(float(row['Distance_Along_m']),10*index)
                if index<30:
                    self.assertEqual(float(row['Bearing_Next_deg']),90)
                else:
                    self.assertEqual(row['Bearing_Next_deg'],'')
                position=fixture_metres(float(row['Longitude']),float(row['Latitude']))
                self.assertAlmostEqual(position.x,index*10)
                self.assertEqual(position.y,0)
