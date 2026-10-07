"""QGIS-independent continuity checks with independently specified metre distances."""
from types import SimpleNamespace
import unittest

from transect_navigator.survey.review import ContinuityPolicy, continuity_segments


class ContinuityTests(unittest.TestCase):
    def model(self, times, positions=None, structural=None):
        positions = positions or ['valid'] * len(times)
        structural = structural or ['segment1'] * len(times)
        raw, normalized = {}, {}
        for index, (time, position, group) in enumerate(zip(times, positions, structural)):
            key = str(index)
            raw[key] = SimpleNamespace(observation_id=key, recording_id='R1', source_order=index + 1,
                                       structural_segment=group)
            normalized[key] = SimpleNamespace(timestamp_utc=time, time_status='valid', position_status=position)
        return SimpleNamespace(raw_observations=raw, normalized_observations=normalized,
                               recordings={'R1': SimpleNamespace(source_id='SRC1', surveyor_id=None)})

    def test_policy_rejects_invalid_limits(self):
        for value in (0, -1, float('inf'), float('nan'), True):
            for args in ((value, 100), (60, value)):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    ContinuityPolicy(*args)

    def test_limits_are_strictly_greater_not_equal(self):
        model = self.model(['2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', '2026-01-01T00:02:01Z'])
        segments = continuity_segments(model, ContinuityPolicy(60, 100), lambda a, b: 100)
        self.assertEqual([s.observation_ids for s in segments], [('0', '1'), ('2',)])
        self.assertEqual(segments[-1].boundary_reasons, ('TIME_GAP',))

    def test_spatial_gap_and_missing_time_preserve_unknown_order(self):
        segments = continuity_segments(self.model([None, None, None]), ContinuityPolicy(60, 100),
            lambda a, b: 101 if b == '2' else 1)
        self.assertEqual([s.observation_ids for s in segments], [('0', '1'), ('2',)])
        self.assertTrue(all(s.start_utc is None and s.ordering_basis == 'SOURCE_ORDER_ONLY' for s in segments))
        self.assertIn('SPATIAL_GAP', segments[-1].boundary_reasons)

    def test_recurring_structural_key_does_not_rejoin(self):
        model = self.model([None, None, None], structural=['a', 'b', 'a'])
        result = continuity_segments(model, ContinuityPolicy(60, 100), lambda a, b: 0)
        self.assertEqual([s.observation_ids for s in result], [('0',), ('1',), ('2',)])

    def test_invalid_distance_fails_rather_than_hiding_gap(self):
        model = self.model([None, None])
        for distance in (-1, float('nan'), float('inf')):
            with self.subTest(distance=distance), self.assertRaises(ValueError):
                continuity_segments(model, ContinuityPolicy(60, 100), lambda a, b: distance)

    def test_invalid_position_isolated_and_flagged(self):
        model = self.model([None] * 3, positions=['valid', 'missing_geometry', 'valid'])
        result = continuity_segments(model, ContinuityPolicy(60, 100), lambda a, b: self.fail('Cannot measure missing geometry'))
        self.assertEqual([s.observation_ids for s in result], [('0',), ('1',), ('2',)])
        self.assertIn('INVALID_POSITION', result[1].flags)

    def test_duplicate_source_order_rejected_without_tie_break(self):
        model = self.model([None, None])
        model.raw_observations['1'].source_order = 1
        with self.assertRaisesRegex(ValueError, 'Source order'):
            continuity_segments(model, ContinuityPolicy(60, 100), lambda a, b: 0)

    def test_empty_input_is_an_empty_partition(self):
        self.assertEqual(continuity_segments(self.model([]), ContinuityPolicy(60, 100), lambda a, b: 0), ())
