"""Independent arithmetic expectations for the pure Visit aggregation boundary."""
from dataclasses import replace
import math
import unittest

from transect_navigator.geometry.planar import Point2D
from transect_navigator.qaqc.deviation import Deviation
from transect_navigator.qaqc.statistics import StatisticsOptions, summarize
from transect_navigator.survey.qa import (QaSample, VisitQa, aggregate_transect,
                                         progress_support, summary_rows)
from transect_navigator.survey.review import Visit


class SurveyQaAggregationTests(unittest.TestCase):
    options = StatisticsOptions(coverage_max_station_gap_m=100)

    def visit(self, name, stations, deviations, *, chronological=True):
        ids = tuple(f'{name}-{i}' for i in range(len(stations)))
        visit = Visit(name, 'T', name, name, None, name, name, ids, 'UNKNOWN',
                      'UTC_SOURCE_ORDER' if chronological else 'SOURCE_ORDER_ONLY', None, None)
        samples = tuple(QaSample(key, str(i), i, f'2026-01-01T00:00:{i*10:02d}Z',
            f'2026-01-01T00:00:{i*10:02d}Z', f'2026-01-01T00:00:{i*10:02d}Z' if chronological else None,
            None, 0, 0, '', Deviation(Point2D(x,0), d, d, x, 100, x, 'left'))
            for i,(key,x,d) in enumerate(zip(ids,stations,deviations)))
        result = VisitQa(visit, 'SUCCESS', '', samples)
        rows = summary_rows((result,))
        summary = summarize('T',100,rows,recorded_length_m=abs(stations[-1]-stations[0]) if chronological else None,options=self.options)
        return replace(result, summary=summary, progress_support=progress_support(rows,100,self.options))

    def test_weighted_observations_not_visit_means(self):
        a = self.visit('A',[0,10],[10,10])
        b = self.visit('B',[20,30,40],[0,0,0])
        result = aggregate_transect('T',100,100,(a,b),self.options)
        self.assertEqual(result.summary.observation_count,5)
        self.assertEqual(result.summary.observed_deviation.mean,4)
        self.assertEqual(result.summary.observed_deviation.median,0)
        self.assertAlmostEqual(result.summary.observed_deviation.rmse,math.sqrt(40))
        self.assertEqual(result.summary.within_percent,(60,60,100))
        self.assertEqual(result.summary.reported_gnss.missing_count,5)

    def test_complementary_union_and_repetition(self):
        a = self.visit('A',[0,60],[0,0])
        b = self.visit('B',[100,50],[0,0])
        c = self.visit('C',[0,100],[0,0])
        result = aggregate_transect('T',100,100,(a,b,c),self.options)
        self.assertEqual(result.summary.recorded_length_m,210)
        self.assertEqual(result.summary.coverage.supported_length_m,100)
        self.assertEqual(result.progress_support.supported_length_m,100)

    def test_unknown_track_is_not_reported_as_zero_or_joined(self):
        a = self.visit('A',[0,10],[0,0])
        b = self.visit('B',[50,60],[0,0],chronological=False)
        result = aggregate_transect('T',100,100,(a,b),self.options)
        self.assertIsNone(result.summary.recorded_length_m)
        self.assertEqual(result.known_track_length_m,10)
        self.assertEqual(result.unknown_track_visit_count,1)
        self.assertEqual(result.summary.coverage.supported_length_m,10)
        self.assertEqual(result.summary.coverage.excluded_group_count,1)

    def test_off_corridor_samples_remain_in_qa_and_progress(self):
        a = self.visit('A',[0,50,100],[20,20,20])
        result = aggregate_transect('T',100,100,(a,),self.options)
        self.assertEqual(result.summary.observed_deviation.mean,20)
        self.assertEqual(result.summary.coverage.supported_length_m,0)
        self.assertEqual(result.progress_support.supported_length_m,100)

    def test_nearby_transect_contamination_rejected(self):
        with self.assertRaisesRegex(ValueError,'Neighboring transect'):
            aggregate_transect('OTHER',100,100,(self.visit('A',[0,10],[0,0]),),self.options)
