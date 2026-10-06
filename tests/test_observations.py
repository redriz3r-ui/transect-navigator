"""Chronology and conservative spatial validation without QGIS substitutes."""
from dataclasses import replace
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from transect_navigator.analysis.observations import (
    Observation, normalize_observations, parse_timestamp, definitely_far, validate_lonlat)


class ObservationTests(unittest.TestCase):
    def observation(self, identifier='a', time='2026-01-01T00:00:00Z', group='survey'):
        return Observation(identifier, group, -120.0, 45.0, time, '4.50')

    def test_offsets_are_compared_as_instants(self):
        rows = [self.observation('later', '2026-01-01T00:00:01Z'),
                self.observation('earlier', '2025-12-31T16:00:00-08:00')]
        result = normalize_observations(rows)
        self.assertEqual([r.raw.source_id for r in result.observations], ['earlier', 'later'])
        self.assertEqual([r.sequence for r in result.observations], [1, 2])
        self.assertEqual(result.observations[0].timestamp_utc, '2026-01-01T00:00:00.000000Z')
        self.assertEqual(rows[0].source_id, 'later')
        self.assertEqual(result.observations[0].raw.accuracy, '4.50')

    def test_duplicate_instants_preserve_both_without_order(self):
        rows = [self.observation('a'), self.observation('b', '2025-12-31T16:00:00-08:00')]
        result = normalize_observations(rows)
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(result.ordered_groups, ())
        self.assertTrue(all(r.sequence is None and r.time_status == 'duplicate' for r in result.observations))
        self.assertIn('DUPLICATE_TIMESTAMPS', [i.code for i in result.issues])

    def test_missing_time_invalidates_entire_group_order(self):
        result = normalize_observations([self.observation('a'), self.observation('b', None)])
        self.assertEqual(result.ordered_groups, ())
        self.assertEqual([r.sequence for r in result.observations], [None, None])
        self.assertIn('MISSING_TIMESTAMPS', [i.code for i in result.issues])

    def test_invalid_time_is_retained(self):
        result = normalize_observations([self.observation(time='yesterday')])
        self.assertEqual(result.observations[0].raw.timestamp, 'yesterday')
        self.assertIsNone(result.observations[0].timestamp_utc)
        self.assertIn('INVALID_TIMESTAMPS', [i.code for i in result.issues])

    def test_repeated_coordinates_are_not_deduplicated(self):
        result = normalize_observations([self.observation('a'), self.observation('b', '2026-01-01T00:00:01Z')])
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(len(result.ordered_groups), 1)

    def test_groups_never_join_or_share_timestamp_ambiguity(self):
        rows = [self.observation('a', group='one'), self.observation('b', '2026-01-01T00:00:01Z', 'one'),
                self.observation('c', group='two'), self.observation('d', None, 'two')]
        result = normalize_observations(rows)
        self.assertEqual(result.ordered_groups, (('one', ('a', 'b')),))
        self.assertEqual([r.sequence for r in result.observations], [1, 2, None, None])

    def test_order_is_deterministic_under_input_permutation(self):
        rows = [self.observation('c', None), self.observation('a'), self.observation('b')]
        self.assertEqual(normalize_observations(rows), normalize_observations(rows[::-1]))

    def test_single_observation_has_no_track(self):
        result = normalize_observations([self.observation()])
        self.assertEqual(result.ordered_groups, ())
        self.assertIn('SINGLE_OBSERVATION', [i.code for i in result.issues])

    def test_empty_input_rejected(self):
        with self.assertRaisesRegex(ValueError, 'No GPS'):
            normalize_observations([])

    def test_identifiers_and_group_are_required(self):
        for rows in ([self.observation(), self.observation()], [self.observation('')], [self.observation(group='')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                normalize_observations(rows)

    def test_limit_is_enforced(self):
        with patch('transect_navigator.analysis.observations.MAX_OBSERVATIONS', 1):
            with self.assertRaises(ValueError):
                normalize_observations([self.observation('a'), self.observation('b')])

    def test_absent_accuracy_is_not_filled(self):
        result = normalize_observations([replace(self.observation(), accuracy=None)])
        self.assertIsNone(result.observations[0].raw.accuracy)

    def test_explicit_microseconds_and_offsets(self):
        parsed, status = parse_timestamp('2026-01-01T01:30:00.123456+01:30')
        self.assertEqual(parsed, datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc))
        self.assertEqual(status, 'valid')

    def test_ambiguous_or_invalid_timestamps(self):
        for value in ['2026-01-01', '2026-01-01T00:00:00', '1735689600',
                      '2026-01-01T00:00:00-00:00', '2026-01-01T00:00:00+01:99',
                      '2026-01-01T00:00:00+24:00', '2026-01-01T00:00:60Z',
                      '2026-02-30T00:00:00Z', '2026-01-01T00:00:00.1234567Z']:
            with self.subTest(value=value):
                self.assertIsNone(parse_timestamp(value)[0])

    def test_empty_timestamp(self):
        for value in [None, '', '   ']:
            self.assertEqual(parse_timestamp(value), (None, 'missing'))

    def test_impossible_coordinates(self):
        for xy in [(181, 0), (0, 91), (float('nan'), 0), (0, float('inf'))]:
            with self.subTest(xy=xy), self.assertRaises(ValueError):
                validate_lonlat(*xy)
        validate_lonlat(-180, -90)
        validate_lonlat(180, 90)

    def test_far_screen_triangle_inequality_and_boundary(self):
        self.assertTrue(definitely_far(11001, 1000, 10000))
        self.assertFalse(definitely_far(11000, 1000, 10000))
        self.assertFalse(definitely_far(999, 1000, 10000))

    def test_far_screen_invalid_numbers(self):
        for args in [(-1, 0, 1), (1, -1, 1), (1, 0, 0), (float('inf'), 0, 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                definitely_far(*args)
