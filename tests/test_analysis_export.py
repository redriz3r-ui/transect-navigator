"""Portable completed-run settings, CSV and atomic publication tests without QGIS."""
import csv
import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from transect_navigator.io.analysis_summary import completed_summary, write_summary_csv, validate_summary_csv
from transect_navigator.io.package import publish_package
from transect_navigator.qaqc.events import EventOptions


class AnalysisExportTests(unittest.TestCase):
    def summary(self, **overrides):
        arguments = dict(spacing_m=10, event_options=asdict(EventOptions(7, None, 12, 45, 30)),
            event_detection={'event_count':2,'excluded_groups':['no-time'], 'eligible_groups':['a']},
            analysis_time='2026-01-02T03:04:05-08:00', plugin_version='test-version')
        arguments.update(overrides)
        return completed_summary({'Transect_ID':'004', 'Mean_Deviation_m':2.125,
            'Recorded_Length_m':None, 'Threshold_A_m':3, 'Within_A_pct':75.0}, **arguments)

    def test_actual_settings_and_timestamp_are_preserved(self):
        values = self.summary()
        self.assertEqual(values['Waypoint_Spacing_m'], 10)
        self.assertEqual(values['Event_Threshold_m'], 7)
        self.assertIsNone(values['Event_Min_Duration_s'])
        self.assertEqual(values['Event_Min_Station_Span_m'],12)
        self.assertEqual(values['Event_Max_Time_Gap_s'],45)
        self.assertEqual(values['Event_Max_Spatial_Gap_m'],30)
        self.assertEqual(values['Analysis_Time_UTC'],'2026-01-02T11:04:05+00:00')
        self.assertEqual(values['Plugin_Version'],'test-version')
        self.assertEqual(values['Event_Count'],2)
        self.assertEqual(values['Event_Excluded_Groups'],1)
        self.assertEqual(json.loads(values['Event_Detection_JSON'])['excluded_groups'],['no-time'])
        self.assertEqual(values['Mean_Deviation_m'],2.125)

    def test_optional_zero_is_not_disabled(self):
        values = self.summary(event_options=asdict(EventOptions(0,0,0)))
        self.assertEqual(values['Event_Min_Duration_s'],0)
        self.assertEqual(values['Event_Min_Station_Span_m'],0)

    def test_invalid_settings_or_naive_timestamp_rejected(self):
        for overrides in ({'spacing_m':0}, {'analysis_time':'2026-01-01T00:00:00'},
                          {'event_options':asdict(EventOptions()) | {'threshold_m':float('nan')}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.summary(**overrides)

    def test_csv_values_precision_and_nulls(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'qa_summary.csv'
            values = self.summary()
            values['Mean_Deviation_m'] = 1.2345678901234567
            write_summary_csv(path,values)
            with path.open(newline='',encoding='utf-8') as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['Transect_ID'],'004')
            self.assertEqual(rows[0]['Recorded_Length_m'],'')
            self.assertEqual(float(rows[0]['Mean_Deviation_m']),1.2345678901234567)
            self.assertEqual(rows[0]['Event_Min_Duration_s'],'')
            self.assertEqual(json.loads(rows[0]['Event_Detection_JSON'])['event_count'],2)

    def test_csv_unicode_quotes_newlines_and_literal_text(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'summary.csv'
            values={'Transect_ID':'=literal, "雪"\nnext', 'Zero':0, 'Unknown':None}
            write_summary_csv(path,values)
            validate_summary_csv(path,values)
            with path.open(newline='',encoding='utf-8') as stream:
                self.assertEqual(next(csv.DictReader(stream))['Transect_ID'],values['Transect_ID'])

    def test_csv_never_overwrites(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'summary.csv'
            path.write_text('existing user file')
            with self.assertRaises(FileExistsError):
                write_summary_csv(path,self.summary())
            self.assertEqual(path.read_text(),'existing user file')

    def test_corrupt_csv_is_detected(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'summary.csv'
            values=self.summary()
            write_summary_csv(path,values)
            path.write_text(path.read_text().replace('2.125','9.125'))
            with self.assertRaisesRegex(ValueError,'round-trip'):
                validate_summary_csv(path,values)

    def test_nonfinite_and_unsupported_values_fail_before_creation(self):
        with TemporaryDirectory() as directory:
            for value in (float('nan'),float('inf'),[],{},True):
                path=Path(directory)/'summary.csv'
                with self.assertRaises(ValueError):
                    write_summary_csv(path,{'bad':value})
                self.assertFalse(path.exists())

    def test_existing_package_is_unchanged(self):
        with TemporaryDirectory() as directory:
            parent=Path(directory)
            writer=lambda stage: write_summary_csv(stage/'qa_summary.csv', self.summary())
            destination=publish_package(parent,'analysis',writer)
            before=(destination/'qa_summary.csv').read_bytes()
            with self.assertRaises(ValueError):
                publish_package(parent,'analysis',writer)
            self.assertEqual((destination/'qa_summary.csv').read_bytes(),before)

    def test_failed_validation_removes_staging(self):
        with TemporaryDirectory() as directory:
            parent=Path(directory)
            def fail(stage):
                write_summary_csv(stage/'qa_summary.csv',self.summary())
                raise ValueError('round-trip failure')
            with self.assertRaisesRegex(ValueError,'round-trip'):
                publish_package(parent,'analysis',fail)
            self.assertEqual(list(parent.iterdir()),[])

    def test_duplicate_settings_fields_rejected(self):
        with self.assertRaisesRegex(ValueError,'already contains'):
            completed_summary(self.summary(),spacing_m=10,event_options=asdict(EventOptions()),
                event_detection={'event_count':0,'excluded_groups':[]},
                analysis_time='2026-01-01T00:00:00Z',plugin_version='test')
