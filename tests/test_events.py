"""Known contiguous exceedance runs, gap splitting and chronology rejection."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import unittest
from transect_navigator.qaqc.events import EventObservation,EventOptions,detect_events
from transect_navigator.qaqc.statistics import SummaryObservation
from transect_navigator.geometry.planar import Point2D


def samples(deviations, stations=None, seconds=None, group='survey', prefix='p'):
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    return [EventObservation(SummaryObservation(f'{prefix}{i}',group,d,
            stations[i] if stations is not None else i*10,
            (start+timedelta(seconds=seconds[i] if seconds is not None else i*5)).isoformat(),
            '4.5'),Point2D(stations[i] if stations is not None else i*10,d if d is not None else 0))
            for i,d in enumerate(deviations)]


class EventTests(unittest.TestCase):
    def test_isolated_bad_point_rejected(self):
        result = detect_events('T04',samples([0,20,0]),EventOptions(minimum_duration_s=None))
        self.assertEqual(result.events,())
        self.assertEqual(result.rejected_run_count,1)

    def test_sustained_known_event(self):
        result = detect_events('T04',samples([0,6,8,10,0]))
        self.assertEqual(len(result.events),1)
        event = result.events[0]
        self.assertEqual(event.event_id,'T04_E001')
        self.assertEqual(event.source_ids,('p1','p2','p3'))
        self.assertEqual((event.start_distance_along_m,event.end_distance_along_m,event.event_length_m),(10,30,20))
        self.assertEqual(event.duration_s,10)
        self.assertEqual(event.mean_deviation_m,8)
        self.assertEqual(event.maximum_deviation_m,10)
        self.assertEqual(event.mean_gnss_accuracy_raw,4.5)
        self.assertEqual(event.observation_count,3)

    def test_two_events_with_return(self):
        result = detect_events('T',samples([6,6,0,7,7]))
        self.assertEqual([e.source_ids for e in result.events],[('p0','p1'),('p3','p4')])
        self.assertEqual([e.event_id for e in result.events],['T_E001','T_E002'])

    def test_threshold_boundary_breaks_run(self):
        result = detect_events('T',samples([6,6,5,6,6]))
        self.assertEqual(len(result.events),2)
        self.assertFalse(any('p2' in e.source_ids for e in result.events))

    def test_incomplete_track_flushes_final_run(self):
        event = detect_events('T',samples([0,6,8])).events[0]
        self.assertEqual(event.end_time,'2026-01-01T00:00:10Z')
        self.assertEqual(event.duration_s,5)

    def test_large_time_gap_splits(self):
        result = detect_events('T',samples([6]*4,seconds=[0,5,100,105]))
        self.assertEqual(len(result.events),2)
        self.assertEqual(result.temporal_gap_count,1)
        self.assertEqual([e.duration_s for e in result.events],[5,5])

    def test_large_spatial_gap_splits_even_same_station(self):
        points = samples([6]*4,stations=[0,10,10,20])
        points[2] = replace(points[2],position=Point2D(1000,6))
        points[3] = replace(points[3],position=Point2D(1010,6))
        result = detect_events('T',points)
        self.assertEqual(len(result.events),2)
        self.assertEqual(result.spatial_gap_count,1)

    def test_large_station_jump_splits_even_close_xy(self):
        points = samples([6]*4,stations=[0,10,100,110])
        points = [replace(p,position=Point2D(i,6)) for i,p in enumerate(points)]
        result = detect_events('T',points)
        self.assertEqual(len(result.events),2)
        self.assertEqual(result.spatial_gap_count,1)

    def test_exact_gap_limits_are_allowed(self):
        result = detect_events('T',samples([6,6],stations=[0,50],seconds=[0,60]))
        self.assertEqual(len(result.events),1)

    def test_missing_timestamp_excludes_entire_group(self):
        points = samples([6]*4)
        points[2] = replace(points[2],observation=replace(points[2].observation,timestamp=None))
        result = detect_events('T',points)
        self.assertEqual(result.events,())
        self.assertEqual(result.excluded_groups,('survey',))
        self.assertEqual(result.eligible_groups,())

    def test_duplicate_instants_in_different_offsets_excluded(self):
        points = samples([6,6])
        points[1] = replace(points[1],observation=replace(points[1].observation,timestamp='2025-12-31T16:00:00-08:00'))
        self.assertEqual(detect_events('T',points).excluded_groups,('survey',))

    def test_optional_minima_and_logic(self):
        points = samples([6,6],stations=[0,10],seconds=[0,5])
        self.assertEqual(len(detect_events('T',points,EventOptions(minimum_duration_s=5,minimum_station_span_m=10)).events),1)
        for options in [EventOptions(minimum_duration_s=6,minimum_station_span_m=10),
                        EventOptions(minimum_duration_s=5,minimum_station_span_m=11)]:
            self.assertEqual(detect_events('T',points,options).events,())
        self.assertEqual(len(detect_events('T',points,EventOptions(minimum_duration_s=None,minimum_station_span_m=None)).events),1)

    def test_short_irregular_run_not_duration_by_count(self):
        self.assertEqual(detect_events('T',samples([6]*4,seconds=[0,.1,.2,.3])).events,())
        self.assertEqual(len(detect_events('T',samples([6,6],seconds=[0,10])).events),1)

    def test_backtracking_span_not_cumulative_length(self):
        event = detect_events('T',samples([6]*3,stations=[10,30,10])).events[0]
        self.assertEqual((event.start_distance_along_m,event.end_distance_along_m,event.event_length_m),(10,10,20))

    def test_stationary_duration_event(self):
        event = detect_events('T',samples([6,6],stations=[10,10])).events[0]
        self.assertEqual(event.event_length_m,0)
        self.assertEqual(event.duration_s,5)

    def test_missing_deviation_breaks_without_bridging(self):
        result = detect_events('T',samples([6,6,None,6,6]))
        self.assertEqual(len(result.events),2)

    def test_gnss_missing_and_bad_values_not_corrected(self):
        points = samples([6,10])
        points[0] = replace(points[0],observation=replace(points[0].observation,accuracy_raw=None))
        points[1] = replace(points[1],observation=replace(points[1].observation,accuracy_raw='bad'))
        event = detect_events('T',points).events[0]
        self.assertIsNone(event.mean_gnss_accuracy_raw)
        self.assertEqual(event.gnss_valid_count,0)
        self.assertEqual(event.mean_deviation_m,8)

    def test_groups_do_not_merge_and_ids_deterministic(self):
        points = samples([6,6],group='a')+samples([6,6],group='b',prefix='b')
        a,b = detect_events('T',points),detect_events('T',points[::-1])
        self.assertEqual(a,b)
        self.assertEqual(len(a.events),2)

    def test_one_unevaluable_group_does_not_hide_valid_group(self):
        points = samples([6,6])+[EventObservation(SummaryObservation('b','other',10,0,None),Point2D(0,10))]
        result = detect_events('T',points)
        self.assertEqual(len(result.events),1)
        self.assertEqual(result.excluded_groups,('other',))

    def test_invalid_options_and_identifiers(self):
        for kwargs in [dict(threshold_m=-1),dict(threshold_m=float('nan')),dict(minimum_duration_s=-1),
                       dict(minimum_station_span_m=float('inf')),dict(maximum_time_gap_s=0),dict(maximum_spatial_gap_m=0)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): EventOptions(**kwargs)
        with self.assertRaises(ValueError): detect_events('',samples([6,6]))
        p = samples([6])[0]
        with self.assertRaises(ValueError): detect_events('T',[p,p])

    def test_empty_and_all_below(self):
        self.assertEqual(detect_events('T',[]).events,())
        self.assertEqual(detect_events('T',samples([0,5,4])).events,())
