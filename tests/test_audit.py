"""Independent regression expectations for defects found in the v0.1.8 audit."""
import csv
from math import sin, cos, radians
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from transect_navigator.geometry.planar import Point2D
from transect_navigator.qaqc.deviation import DirectedTransect
from transect_navigator.qaqc.statistics import StatisticsOptions
from transect_navigator.navigation.identifiers import transect_identifier
from transect_navigator.io.analysis_summary import write_summary_csv, validate_summary_csv
from transect_navigator.io.csv_utils import read_generated_csv


class AuditRegressionTests(unittest.TestCase):
    def test_near_subdivision_vertex_is_not_ambiguous(self):
        path=DirectedTransect([Point2D(x,0) for x in (0,25,50,75,100)])
        for x in (24.9999,25.0001,49.9999,50.0001):
            for y in (-5,5):
                result=path.compare(Point2D(x,y))
                self.assertAlmostEqual(result.distance_along_m,x,places=9)
                self.assertEqual(result.absolute_m,5)
                self.assertEqual(result.signed_m,y)

    def test_near_vertex_rotation_translation_and_reversal(self):
        for angle in (0,37,90,155,225):
            u,v=cos(radians(angle)),sin(radians(angle))
            vertices=[Point2D(101+x*u,-50+x*v) for x in (0,25,100)]
            x,y=24.9999,5
            point=Point2D(101+x*u-y*v,-50+x*v+y*u)
            for reverse in (False,True):
                result=DirectedTransect(vertices[::-1] if reverse else vertices).compare(point)
                self.assertAlmostEqual(result.distance_along_m,100-x if reverse else x,places=9)
                self.assertAlmostEqual(result.signed_m,-5 if reverse else 5,places=9)

    def test_near_bend_with_unique_foot_remains_valid(self):
        path=DirectedTransect([Point2D(0,0),Point2D(25,0),Point2D(25,25)])
        result=path.compare(Point2D(24.9999,-5))
        self.assertEqual(result.signed_m,-5)
        self.assertAlmostEqual(result.distance_along_m,24.9999)

    def test_real_equal_distance_stations_still_rejected(self):
        path=DirectedTransect([Point2D(0,0),Point2D(25,0),Point2D(25,25)])
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            path.compare(Point2D(24,1))

    def test_near_final_endpoint_uses_unique_interior_projection(self):
        path=DirectedTransect([Point2D(0,0),Point2D(99,0),Point2D(100,0)])
        result=path.compare(Point2D(98.9999,5))
        self.assertAlmostEqual(result.distance_along_m,98.9999)
        self.assertEqual(result.signed_m,5)

    def test_identifier_normalization_keeps_text_identity(self):
        self.assertEqual(transect_identifier('  T04 \t'),'T04')
        self.assertEqual(transect_identifier('004'),'004')
        self.assertEqual(transect_identifier(4),'4')
        self.assertEqual(transect_identifier('nan'),'nan')  # Text ID, not a numeric NaN.

    def test_identifier_rejects_null_blank_and_numeric_nonfinite(self):
        for value in (None,'',' \t',float('nan'),float('inf'),float('-inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):
                transect_identifier(value)

    def test_large_summary_json_cell_round_trips(self):
        import json
        previous=csv.field_size_limit()
        values={'Transect_ID':'T04','Event_Detection_JSON':json.dumps({'excluded_groups':['x'*150000]})}
        with TemporaryDirectory() as directory:
            path=Path(directory)/'summary.csv'
            write_summary_csv(path,values)
            validate_summary_csv(path,values)
            self.assertEqual(read_generated_csv(path)[1],[values])
        self.assertEqual(csv.field_size_limit(),previous)

    def test_csv_reader_restores_global_limit_on_failure(self):
        previous=csv.field_size_limit()
        with TemporaryDirectory() as directory:
            path=Path(directory)/'invalid.csv'
            path.write_text('field\n"unterminated'+('x'*150000))
            with self.assertRaises(csv.Error):
                read_generated_csv(path)
        self.assertEqual(csv.field_size_limit(),previous)

    def test_boolean_and_text_statistics_options_are_not_distances(self):
        for value in (True,False,'3',None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    StatisticsOptions((value,5,10))
                with self.assertRaises(ValueError):
                    StatisticsOptions(coverage_max_station_gap_m=value)
                with self.assertRaises(ValueError):
                    StatisticsOptions(coverage_max_time_gap_s=value)

    def test_analysis_export_does_not_normalize_invalid_timestamp_offsets(self):
        from dataclasses import asdict
        from transect_navigator.qaqc.events import EventOptions
        from transect_navigator.io.analysis_summary import completed_summary
        for timestamp in ('2026-01-01T00:00:00+01:99', '2026-01-01T00:00:00-00:00',
                          '2026-01-01T00:00:00.1234567Z'):
            with self.assertRaises(ValueError):
                completed_summary({'Transect_ID':'T'},spacing_m=10,event_options=asdict(EventOptions()),
                    event_detection={'event_count':0,'excluded_groups':[]},analysis_time=timestamp,plugin_version='test')

    def test_dense_attribute_expansion_is_bounded(self):
        from transect_navigator.io.recorded import validate_attribute_table
        validate_attribute_table(50,100000)
        validate_attribute_table(512,1)
        with self.assertRaisesRegex(ValueError,'fields'):
            validate_attribute_table(513,1)
        with self.assertRaisesRegex(ValueError,'attribute cells'):
            validate_attribute_table(51,100000)

    def test_csv_field_union_limit_before_rows(self):
        from transect_navigator.io.recorded import read_csv
        with TemporaryDirectory() as directory:
            path=Path(directory)/'wide.csv'
            path.write_text(','.join(f'f{i}' for i in range(513))+'\n')
            with self.assertRaisesRegex(ValueError,'fields'):
                read_csv(path,'f0','f1')
