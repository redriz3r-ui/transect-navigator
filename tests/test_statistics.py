"""Independently calculated statistics and conservative sampled-progress support."""
from math import sqrt
import json
import unittest
from transect_navigator.qaqc.statistics import (SummaryObservation,StatisticsOptions,summarize,
                                               describe,percentile,track_length)
from transect_navigator.geometry.planar import Point2D


def rows(deviations, stations=None, times=None, accuracy=None):
    return [SummaryObservation(str(i),'a',d,stations[i] if stations is not None else i*10,
        times[i] if times is not None else f'2026-01-01T00:00:{i:02d}Z',
        accuracy[i] if accuracy is not None else None) for i,d in enumerate(deviations)]


class StatisticsTests(unittest.TestCase):
    def test_known_statistics(self):
        s = summarize('T04',100,rows([0,1,3,5,11]),recorded_length_m=104)
        self.assertEqual(s.observation_count,5)
        self.assertEqual(s.planned_length_m,100)
        self.assertEqual(s.recorded_length_m,104)
        self.assertEqual(s.observed_deviation.mean,4)
        self.assertEqual(s.observed_deviation.median,3)
        self.assertAlmostEqual(s.observed_deviation.rmse,sqrt(156/5))
        self.assertEqual(s.observed_deviation.maximum,11)
        self.assertAlmostEqual(s.observed_deviation.p95,9.8)
        self.assertEqual(s.within_percent,(60,80,80))

    def test_thresholds_inclusive_and_configurable(self):
        s = summarize('T',10,rows([0,3,5,10]),options=StatisticsOptions((0,3,5)))
        self.assertEqual(s.within_percent,(25,50,75))

    def test_missing_and_invalid_denominators(self):
        s = summarize('T',100,rows([None,float('nan'),float('inf'),-1,0,10]))
        self.assertEqual(s.observation_count,6)
        self.assertEqual(s.observed_deviation.count,2)
        self.assertEqual(s.observed_deviation.missing_count,1)
        self.assertEqual(s.observed_deviation.invalid_count,3)
        self.assertEqual(s.observed_deviation.mean,5)
        self.assertEqual(s.within_percent,(50,50,100))

    def test_empty_all_missing_and_singleton(self):
        for observations in ([],rows([None,None])):
            s = summarize('T',100,observations)
            self.assertIsNone(s.observed_deviation.mean)
            self.assertEqual(s.within_percent,(None,None,None))
            self.assertEqual(s.coverage.supported_length_m,0 if observations else None)
        d = summarize('T',100,rows([5])).observed_deviation
        self.assertEqual((d.mean,d.median,d.rmse,d.maximum,d.p95),(5,5,5,5,5))

    def test_gnss_descriptions_are_independent(self):
        s = summarize('T',100,rows([5,5,5,5,5,5],accuracy=['2','4',None,'bad','-3','nan']))
        self.assertEqual(s.observed_deviation.mean,5)
        self.assertEqual(s.reported_gnss.mean,3)
        self.assertEqual(s.reported_gnss.count,2)
        self.assertEqual(s.reported_gnss.missing_count,1)
        self.assertEqual(s.reported_gnss.invalid_count,3)
        self.assertAlmostEqual(s.reported_gnss.p95,3.9)

    def test_outliers_retained(self):
        d = describe([0,0,0,100])
        self.assertEqual(d.mean,25)
        self.assertEqual(d.median,0)
        self.assertEqual(d.rmse,50)
        self.assertAlmostEqual(d.p95,85)

    def test_uneven_sampling_is_observation_weighted(self):
        a = summarize('T',100,rows([0,10]))
        b = summarize('T',100,rows([0]*9+[10]))
        self.assertEqual(a.observed_deviation.mean,5)
        self.assertEqual(b.observed_deviation.mean,1)
        self.assertEqual(a.within_percent[0],50)
        self.assertEqual(b.within_percent[0],90)

    def test_percentile_two_values_and_zero(self):
        self.assertEqual(percentile([0,100],.95),95)
        self.assertEqual(percentile([0,100],.5),50)
        self.assertEqual(percentile([0],.95),0)
        self.assertEqual(percentile([], .95),None)
        with self.assertRaises(ValueError): percentile([0],1.1)

    def test_large_finite_values_do_not_overflow_rms(self):
        d = describe([1e300,1e300])
        self.assertEqual(d.mean,1e300)
        self.assertEqual(d.rmse,1e300)

    def test_track_parts_do_not_bridge(self):
        parts = [[Point2D(0,0),Point2D(3,4)],[Point2D(100,0),Point2D(103,4)]]
        self.assertEqual(track_length(parts),10)
        self.assertIsNone(track_length(None))
        self.assertIsNone(track_length([]))
        self.assertEqual(track_length([[Point2D(0,0),Point2D(0,0)]]),0)
        with self.assertRaises(ValueError): track_length([[Point2D(0,0)]])

    def test_full_and_incomplete_coverage(self):
        for stations,expected in [([0,25,50,75,100],100),([20,40,60],40)]:
            c = summarize('T',100,rows([1]*len(stations),stations)).coverage
            self.assertEqual(c.supported_length_m,expected)
            self.assertEqual(c.supported_percent,expected)

    def test_reversal_and_backtracking_union(self):
        for stations in [[0,20,10,30],[30,10,20,0]]:
            c = summarize('T',100,rows([0]*4,stations)).coverage
            self.assertEqual(c.supported_length_m,30)
            self.assertEqual(c.accepted_pair_count,3)

    def test_far_from_plan_does_not_count_as_support(self):
        c = summarize('T',100,rows([0,11,0],[0,10,20])).coverage
        self.assertEqual(c.supported_length_m,0)
        self.assertEqual(c.rejected_pair_count,2)

    def test_spatial_gap_not_bridged(self):
        c = summarize('T',100,rows([0,0],[0,100])).coverage
        self.assertEqual(c.supported_length_m,0)
        self.assertEqual(c.rejected_pair_count,1)

    def test_temporal_gap_not_bridged(self):
        c = summarize('T',100,rows([0,0],[0,10],['2026-01-01T00:00:00Z','2026-01-01T00:01:01Z'])).coverage
        self.assertEqual(c.supported_length_m,0)

    def test_gap_boundaries_inclusive(self):
        c = summarize('T',100,rows([10,10],[0,25],['2026-01-01T00:00:00Z','2026-01-01T00:01:00Z'])).coverage
        self.assertEqual(c.supported_length_m,25)

    def test_missing_duplicate_times_unknown_coverage(self):
        for times in [[None,None],['2026-01-01T00:00:00Z']*2]:
            c = summarize('T',100,rows([0,0],[0,10],times)).coverage
            self.assertIsNone(c.supported_length_m)
            self.assertEqual(c.excluded_group_count,1)

    def test_bad_middle_sample_not_skipped_over(self):
        c = summarize('T',100,rows([0,None,0],[0,10,20])).coverage
        self.assertEqual(c.supported_length_m,0)
        self.assertEqual(c.rejected_pair_count,2)

    def test_disjoint_groups_and_overlap_union(self):
        points = rows([0,0],[0,20]) + [SummaryObservation('b0','b',0,10,'2026-01-01T00:00:00Z'),
                                     SummaryObservation('b1','b',0,30,'2026-01-01T00:00:01Z')]
        c = summarize('T',100,points).coverage
        self.assertEqual(c.supported_length_m,30)
        self.assertEqual(c.eligible_group_count,2)

    def test_stationary_and_short_plan(self):
        self.assertEqual(summarize('T',100,rows([0,0],[10,10])).coverage.supported_length_m,0)
        self.assertEqual(summarize('T',.1,rows([0,0],[0,.1])).coverage.supported_percent,100)

    def test_invalid_station_excluded_from_coverage_only(self):
        for station in [-1,101,None,float('nan')]:
            s = summarize('T',100,rows([1,1],[0,station]))
            self.assertEqual(s.coverage.supported_length_m,0)
            self.assertEqual(s.observed_deviation.count,2)

    def test_parameter_validation(self):
        for thresholds in [(5,3,10),(-1,5,10),(1,2),(1,2,float('inf'))]:
            with self.assertRaises(ValueError): StatisticsOptions(thresholds)
        for gap in [0,-1,float('nan')]:
            with self.assertRaises(ValueError): StatisticsOptions(coverage_max_time_gap_s=gap)
        for length in [0,-1,float('nan')]:
            with self.assertRaises(ValueError): summarize('T',length,[])
        with self.assertRaises(ValueError): summarize('T',100,[],recorded_length_m=-1)

    def test_machine_readable_no_nan(self):
        s = summarize('T',100,rows([None,float('nan')]))
        decoded = json.loads(json.dumps(s.as_dict(),allow_nan=False))
        self.assertEqual(decoded['observation_count'],2)
        self.assertIsNone(decoded['observed_deviation']['mean'])
