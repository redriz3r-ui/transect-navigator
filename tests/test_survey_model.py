"""Portable survey identity and UTC contracts need no QGIS runtime."""
from dataclasses import replace
import unittest
from transect_navigator.survey.model import (
    Survey, SurveyRegistry, SCHEMA_VERSION, digest, required_text, utc_time)


class SurveyModelTests(unittest.TestCase):
    def test_canonical_hash_respects_types_but_not_mapping_order(self):
        self.assertEqual(digest({'x':1,'y':2}), digest({'y':2,'x':1}))
        self.assertNotEqual(digest(['int','1']), digest(['text','1']))
        with self.assertRaises(ValueError):
            digest(float('nan'))

    def test_ids_are_explicit_nonblank_text(self):
        self.assertEqual(required_text('S01', 'Survey_ID'), 'S01')
        for value in ('', ' ', ' S01', 'S01 ', 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                required_text(value, 'Survey_ID')

    def test_creation_time_requires_timezone_and_normalizes_utc(self):
        self.assertEqual(utc_time('2026-10-06T05:00:00-07:00'), '2026-10-06T12:00:00+00:00')
        with self.assertRaisesRegex(ValueError, 'explicit timezone'):
            utc_time('2026-10-06T12:00:00')

    def test_registry_cannot_replace_another_survey_under_same_id(self):
        survey = Survey('S01', 'Survey', SCHEMA_VERSION, '2026-10-06T12:00:00+00:00',
                        'abc', 'def', '', '', '', '', '', '[]', 'Transect_ID', '1', ())
        registry = SurveyRegistry()
        registry.register(survey)
        registry.register(survey)
        with self.assertRaisesRegex(ValueError, 'already registered'):
            registry.register(replace(survey, name='Different'))
        self.assertIs(registry.get('S01'), survey)
