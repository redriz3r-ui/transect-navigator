"""Presentation boundaries, independently specified values and missing-data behavior."""
import unittest
from transect_navigator.presentation.results import (class_rules, deviation_class,
    quoted_field, profile_points, profile_bounds, summary_rows, number)
from transect_navigator.qaqc.deviation import Deviation
from transect_navigator.geometry.planar import Point2D
from transect_navigator.qaqc.statistics import summarize, SummaryObservation
from transect_navigator.qaqc.events import EventDetection


class PresentationTests(unittest.TestCase):
    def test_inclusive_threshold_boundaries(self):
        values = [0, 3, 3.0001, 5, 5.0001, 10, 10.0001]
        self.assertEqual([deviation_class(v, (3,5,10)) for v in values], [0,0,1,1,2,2,3])

    def test_missing_and_invalid_are_not_low_deviation(self):
        for value in (None, float('nan'), float('inf'), -1):
            self.assertIsNone(deviation_class(value, (3,5,10)))

    def test_equal_thresholds_have_no_duplicate_intervals(self):
        rules = class_rules('dev', (0,0,0))
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].expression, '"dev" >= 0 AND "dev" <= 0')
        self.assertEqual(rules[1].expression, '"dev" > 0')

    def test_invalid_thresholds_rejected(self):
        for thresholds in ((5,3,10), (-1,3,5), (3,float('nan'),10)):
            with self.assertRaises(ValueError):
                class_rules('dev', thresholds)

    def test_expression_quoting(self):
        self.assertEqual(quoted_field('a"b'), '"a""b"')
        self.assertIn('"a""b"', class_rules('a"b', (3,5,10))[0].expression)

    def test_profile_preserves_order_duplicates_and_defined_signs(self):
        rows = [Deviation(Point2D(0,0), abs(d), d, x,100,x,'defined')
                for x,d in ((20,-1),(10,5),(10,5))]
        rows.append(Deviation(Point2D(0,0), 2, None, 100,100,100,'endpoint'))
        absolute = profile_points(rows, (3,5,10))
        signed = profile_points(rows, (3,5,10), True)
        self.assertEqual([p.station_m for p in absolute], [20,10,10,100])
        self.assertEqual([p.deviation_m for p in signed], [-1,5,5])
        self.assertEqual([p.class_index for p in signed], [0,1,1])
        self.assertIsNone(rows[-1].signed_m)

    def test_profile_empty_and_zero_bounds(self):
        self.assertEqual(profile_bounds([], 100), (100,0,.1))
        xmax, low, high = profile_bounds([], 0)
        self.assertGreater(xmax, 0)
        self.assertGreater(high, low)

    def test_summary_known_statistics_and_missing_track(self):
        rows = [SummaryObservation(str(i),'a',d,i*10) for i,d in enumerate((0,1,3,5,11))]
        summary = summarize('T04',100,rows)
        events = EventDetection((),(),('a',),0,0,0)
        values = dict(summary_rows(summary, events, 10))
        self.assertEqual(values['Transect ID'], 'T04')
        self.assertEqual(values['Mean absolute deviation'], '4.000 m')
        self.assertEqual(values['95th percentile absolute deviation'], '9.800 m')
        self.assertEqual(values['Within threshold A (≤ 3 m)'], '60.000 %')
        self.assertEqual(values['Recorded track length (available parts)'], 'Unavailable')
        self.assertEqual(values['Reported GNSS accuracy mean (source units)'], 'Unavailable')
        self.assertIn('1 groups unevaluated', values['Detected deviation events'])

    def test_unknown_not_formatted_as_zero(self):
        for value in (None, float('nan'), float('inf')):
            self.assertEqual(number(value), 'Unavailable')
        self.assertEqual(number(0, 'm'), '0.000 m')
