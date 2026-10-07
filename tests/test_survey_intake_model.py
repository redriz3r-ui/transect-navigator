"""QGIS-independent Stage 3 identity and reconciliation tests."""
import unittest

from transect_navigator.survey.intake import (FieldMappings, ImportAttempt, IntakeModel,
    NormalizedObservationRecord, RawObservationRecord, RecordingRecord, SourceRecord,
    observation_id, recording_id, source_id)


class SurveyIntakeModelTests(unittest.TestCase):
    def records(self):
        sid = source_id('SURVEY-A', 'file-sha256:' + 'a' * 64)
        rid = recording_id(sid, 'csv:rows')
        oid = observation_id(rid, 'row_2')
        source = SourceRecord(sid, 'SURVEY-A', 'a.csv', '/tmp/a.csv', 'a.csv',
            'file-sha256:' + 'a' * 64, 'a' * 64, None, 'FILE', 'CSV',
            '2026-10-06T12:00:00+00:00', '[]', '[]', 'sources/a.csv', 'SUCCESS', '', 4)
        recording = RecordingRecord(rid, sid, 'csv:rows', 'a.csv', None,
            FieldMappings(timestamp='time'), ('survey',), 'wkt', 'EPSG:4326', '[]',
            'raw', '', 1, None, None, 'SUCCESS')
        raw = RawObservationRecord(oid, sid, rid, 'row_2', 'row_2', 1, 'survey',
            'raw', 'row_2:attributes', 'row_2:geometry', 'b' * 64, 'c' * 64)
        normalized = NormalizedObservationRecord(oid, 1.0, 2.0, None, None,
            'missing', 'valid', 1, None, None, None, None, None, None)
        return source, recording, raw, normalized

    def test_content_identities_ignore_filename_and_are_namespaced_by_survey(self):
        content = 'file-sha256:' + 'a' * 64
        self.assertEqual(source_id('SURVEY-A', content), source_id('SURVEY-A', content))
        self.assertNotEqual(source_id('SURVEY-A', content), source_id('SURVEY-B', content))
        source = source_id('SURVEY-A', content)
        self.assertNotEqual(recording_id(source, 'track:1'), recording_id(source, 'track:2'))
        recording = recording_id(source, 'track:1')
        self.assertNotEqual(observation_id(recording, 'point_1'),
                            observation_id(recording, 'point_2'))

    def test_complete_provenance_chain_and_totals(self):
        model = IntakeModel('SURVEY-A')
        source, recording, raw, normalized = self.records()
        model.sources[source.source_id] = source
        model.recordings[recording.recording_id] = recording
        model.raw_observations[raw.observation_id] = raw
        model.normalized_observations[normalized.observation_id] = normalized
        model.attempts.append(ImportAttempt('IMP_1', 'SURVEY-A', 'a.csv', 'a.csv',
            '2026-10-06T12:00:00+00:00', 'SUCCESS', source.source_id, None, 'ok'))
        model.validate()
        totals = model.totals()
        self.assertEqual((totals.state, totals.source_count, totals.recording_count,
                          totals.observation_count), ('SUCCESS', 1, 1, 1))
        self.assertEqual(totals.unknown_surveyor_recording_count, 1)

    def test_missing_normalized_or_recording_link_is_rejected(self):
        source, recording, raw, normalized = self.records()
        model = IntakeModel('SURVEY-A')
        model.sources[source.source_id] = source
        model.recordings[recording.recording_id] = recording
        model.raw_observations[raw.observation_id] = raw
        with self.assertRaisesRegex(ValueError, 'no normalized'):
            model.validate()
        model.normalized_observations[normalized.observation_id] = normalized
        model.recordings.clear()
        with self.assertRaisesRegex(ValueError, 'provenance chain'):
            model.validate()

    def test_surveyor_is_explicit_nullable_and_partial_state_reconciles(self):
        source, recording, raw, normalized = self.records()
        model = IntakeModel('SURVEY-A')
        model.sources[source.source_id] = source
        model.recordings[recording.recording_id] = recording
        model.raw_observations[raw.observation_id] = raw
        model.normalized_observations[normalized.observation_id] = normalized
        model.set_surveyor(recording.recording_id, 'S01')
        self.assertEqual(model.recordings[recording.recording_id].surveyor_id, 'S01')
        model.set_surveyor(recording.recording_id, '')
        self.assertIsNone(model.recordings[recording.recording_id].surveyor_id)
        model.attempts.append(ImportAttempt('IMP_1', 'SURVEY-A', 'bad', 'bad',
            '2026-10-06T12:00:00+00:00', 'FAILED', None, None, 'bad'))
        self.assertEqual(model.totals().state, 'PARTIAL')


if __name__ == '__main__':
    unittest.main()
