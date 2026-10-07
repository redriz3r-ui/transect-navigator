"""Stage 4 known-answer manual review and real PyQGIS persistence/UI checks."""
from collections import Counter
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

SAMPLE = Path(__file__).resolve().parents[2] / 'samples/survey_review'


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class SurveyReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from qgis.core import QgsProject, QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey
        from transect_navigator.integration.survey_intake import SurveyIntake, ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.survey.review import ContinuityPolicy
        self.project = QgsProject()
        self.plan = QgsVectorLayer(str(SAMPLE / 'planned.geojson'), 'plans', 'ogr')
        setup = setup_survey(self.plan, 'Transect_ID', survey_id='REVIEW01', name='Review fixture',
            registry=SurveyRegistry(), project=self.project, created_utc='2026-10-01T12:00:00Z')
        self.intake = SurveyIntake(setup.survey, project=self.project)
        self.expected = json.loads((SAMPLE / 'expected.json').read_text())
        for filename, surveyor in (('A.csv', 'S01'), ('B.gpx', 'S02'), ('C.csv', None)):
            options = (ImportOptions(mappings=FieldMappings(timestamp='time', hdop='hdop'), surveyor_id=surveyor)
                if filename.endswith('.gpx') else ImportOptions('x', 'y', 'EPSG:4326',
                    FieldMappings(timestamp='time', accuracy='accuracy'), surveyor))
            result = self.intake.import_file(SAMPLE / filename, options, imported_utc='2026-10-01T12:00:00Z')
            self.assertIn(result.status, ('SUCCESS', 'PARTIAL'))
        self.policy = ContinuityPolicy(**self.expected['policy'])
        self.run = start_review(self.intake, self.policy, created_utc='2026-10-01T13:00:00Z')

    def ids(self, filename, rows, track=1):
        recordings = sorted((r for r in self.intake.model.recordings.values()
            if self.intake.model.sources[r.source_id].source_file == filename), key=lambda r: r.recording_key)
        rec = recordings[track - 1]
        by_order = {r.source_order: r.observation_id for r in self.intake.model.raw_observations.values()
                    if r.recording_id == rec.recording_id}
        return [by_order[row] for row in rows]

    def decide(self, ids, **kwargs):
        return self.run.decide(ids, reviewer='reviewer@example.test', reason='Independent fixture field notes',
                               utc='2026-10-01T14:00:00Z', **kwargs)

    def assign(self, ids, direction='FORWARD', plan='T01'):
        return self.decide(ids, assignment_status='ASSIGNED', activity_role='SURVEY',
                           transect_id=plan, direction=direction)

    def reviewed(self):
        for action in self.expected['actions']:
            self.decide(self.ids(action['file'], action['rows'], action['track']),
                assignment_status=action['status'], activity_role=action['role'],
                transect_id=action.get('plan'), direction=action.get('direction', 'UNKNOWN'))
        return self.run

    def native(self):
        return {key: (native.observations.crs().toWkt(), native.observations.fields().names(),
                     [(f.id(), f.attributes(), bytes(f.geometry().asWkb())) for f in native.observations.getFeatures()])
                for key, native in self.intake.native_recordings.items()}

    def test_known_answer_dispositions_and_visits(self):
        run = self.reviewed()
        counts = Counter(d.assignment_status for d in run.membership.values())
        self.assertEqual(counts, {'ASSIGNED': 15, 'AMBIGUOUS': 5, 'UNASSIGNED': 4})
        for name in ('assigned', 'ambiguous', 'unassigned'):
            self.assertEqual(counts[name.upper()], self.expected[name])
        self.assertEqual(len(run.membership), self.expected['raw_count'])
        self.assertEqual(len(run.visits), self.expected['visits'])
        self.assertEqual(run.unresolved_count, self.expected['unresolved'])
        self.assertEqual(sum(d.activity_role == 'TRANSITION' for d in run.membership.values()), 2)
        self.assertEqual(Counter(v.direction for v in run.visits), {'FORWARD': 4, 'REVERSE': 2})
        accepted = [key for visit in run.visits for key in visit.observation_ids]
        self.assertEqual(len(accepted), len(set(accepted)))
        self.assertEqual(set(accepted), {key for key, d in run.membership.items() if d.assignment_status == 'ASSIGNED'})
        self.assertEqual({v.surveyor_id for v in run.visits}, {'S01', 'S02'})

    def test_known_answer_continuity_and_complete_partition(self):
        self.assertEqual(len(self.run.segments), self.expected['segments'])
        keys = [key for s in self.run.segments for key in s.observation_ids]
        self.assertEqual(Counter(keys), Counter(self.intake.model.raw_observations.keys()))
        for s in self.run.segments:
            raws = [self.intake.model.raw_observations[key] for key in s.observation_ids]
            self.assertEqual({r.source_id for r in raws}, {s.source_id})
            self.assertEqual({r.recording_id for r in raws}, {s.recording_id})
            self.assertEqual({r.structural_segment for r in raws}, {s.structural_segment})
        boundaries = {reason for s in self.run.segments for reason in s.boundary_reasons}
        self.assertTrue({'SPATIAL_GAP', 'TIME_GAP', 'STRUCTURAL_BOUNDARY', 'NONINCREASING_TIME'} <= boundaries)

    def test_missing_duplicate_times_never_invent_chronology(self):
        ids = self.ids('C.csv', [1, 2, 3])
        self.assign(ids)
        for visit in self.run.visits:
            self.assertEqual(visit.ordering_basis, 'SOURCE_ORDER_ONLY')
            self.assertIsNone(visit.start_utc)
            self.assertIsNone(visit.end_utc)
        flags = {f for s in self.run.segments if set(s.observation_ids) & set(ids) for f in s.flags}
        self.assertTrue({'MISSING_OR_INVALID_TIME', 'DUPLICATE_TIME'} <= flags)

    def test_storage_order_invariance(self):
        from transect_navigator.integration.survey_review import start_review
        for name in ('sources', 'recordings', 'raw_observations', 'normalized_observations'):
            rows = getattr(self.intake.model, name)
            setattr(self.intake.model, name, dict(reversed(list(rows.items()))))
        restored = start_review(self.intake, self.policy, created_utc=self.run.created_utc)
        self.assertEqual(restored.snapshot(), self.run.snapshot())

    def test_exact_noncontiguous_selection_does_not_bridge_holes(self):
        ids = self.ids('A.csv', [1, 3])
        self.assign(ids)
        self.assertEqual([v.observation_ids for v in self.run.visits], [(ids[0],), (ids[1],)])
        self.assertEqual(self.run.membership[self.ids('A.csv', [2])[0]].assignment_status, 'UNASSIGNED')

    def test_single_decision_never_bridges_structural_or_source_boundaries(self):
        ids = self.ids('B.gpx', [1, 2, 3, 4]) + self.ids('A.csv', [1, 2, 3, 12, 13, 14])
        self.assign(ids)
        self.assertEqual(len(self.run.visits), 4)
        self.assertEqual(sorted(len(v.observation_ids) for v in self.run.visits), [2, 2, 3, 3])

    def test_transition_revision_splits_visit_and_preserves_history(self):
        ids = self.ids('A.csv', [1, 2, 3])
        first = self.assign(ids)
        token = self.run.qa_input_digest
        revision = self.decide([ids[1]], activity_role='TRANSITION')
        self.assertEqual(revision.superseded_decision_ids, (first.decision_id,))
        self.assertEqual(self.run.history[0], first)
        self.assertEqual(len(self.run.visits), 2)
        self.assertNotEqual(self.run.qa_input_digest, token)
        self.assertEqual(self.run.membership[ids[1]].transect_id, None)

    def test_reviewed_boundary_splits_visit_without_changing_dispositions(self):
        ids = self.ids('A.csv', [1, 2, 3])
        self.assign(ids)
        before = self.run.membership
        self.decide([ids[1]], action='SPLIT_BEFORE')
        self.assertEqual(self.run.membership, before)
        self.assertEqual(sorted(len(v.observation_ids) for v in self.run.visits), [1, 2])
        self.assertTrue(any('REVIEWED_BOUNDARY' in s.boundary_reasons for s in self.run.segments))

    def test_partial_repeat_reverse_and_mixed_direction_are_explicit(self):
        self.assign(self.ids('A.csv', [1, 2, 3]), direction='REVERSE')
        self.assign(self.ids('A.csv', [12, 13]), direction='MIXED')
        self.assertEqual({v.direction for v in self.run.visits}, {'MIXED', 'REVERSE'})
        self.assertEqual(sorted(len(v.observation_ids) for v in self.run.visits), [2, 3])

    def test_atomic_invalid_actions(self):
        ids = self.ids('A.csv', [1])
        before = self.run.snapshot()
        bad = [dict(observation_ids=[]), dict(observation_ids=ids + ids),
               dict(observation_ids=['absent']), dict(observation_ids=ids, transect_id='T01'),
               dict(observation_ids=ids, assignment_status='ASSIGNED', activity_role='SURVEY', transect_id='absent'),
               dict(observation_ids=ids, assignment_status='AMBIGUOUS', activity_role='TRANSITION'),
               dict(observation_ids=ids, direction='REVERSE')]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError):
                options = dict(args)
                self.decide(options.pop('observation_ids'), **options)
            self.assertEqual(self.run.snapshot(), before)

    def test_reviewer_and_reason_required(self):
        ids = self.ids('A.csv', [1])
        for reviewer, reason in (('', 'reason'), ('name', ''), (' name ', 'reason')):
            with self.assertRaises(ValueError):
                self.run.decide(ids, reviewer=reviewer, reason=reason)
        self.assertFalse(self.run.history)

    def test_surveryor_change_stales_run(self):
        rec = self.intake.model.raw_observations[self.ids('A.csv', [1])[0]].recording_id
        self.intake.set_surveyor(rec, 'S03')
        with self.assertRaisesRegex(ValueError, 'Stale review'):
            self.assign(self.ids('A.csv', [1]))

    def test_mapping_and_observation_change_stales_run(self):
        key = self.ids('A.csv', [1])[0]
        old = self.intake.model.normalized_observations[key]
        self.intake.model.normalized_observations[key] = replace(old, timestamp_utc=None)
        with self.assertRaisesRegex(ValueError, 'Stale review'):
            self.run.snapshot()

    def test_plan_revision_stales_restore(self):
        from transect_navigator.survey.review import ReviewRun
        from transect_navigator.integration.survey_review import review_distance
        with self.assertRaisesRegex(ValueError, 'Stale review'):
            ReviewRun.restore(self.run.snapshot(), replace(self.intake.survey, plan_revision='2'),
                              self.intake.model, review_distance(self.intake))

    def test_tampered_history_and_membership_rejected(self):
        from transect_navigator.survey.review import ReviewRun
        from transect_navigator.integration.survey_review import review_distance
        self.reviewed()
        for key in ('decisions', 'membership', 'visits', 'segments'):
            state = json.loads(json.dumps(self.run.snapshot()))
            state[key].pop()
            with self.subTest(key=key), self.assertRaises(ValueError):
                ReviewRun.restore(state, self.intake.survey, self.intake.model, review_distance(self.intake))

    def test_save_reopen_edit_overwrite_protection_and_raw_integrity(self):
        from transect_navigator.integration.survey_review import save_review, load_review
        from transect_navigator.survey.model import canonical_json
        before = self.native()
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in SAMPLE.iterdir() if p.is_file()}
        self.reviewed()
        self.decide(self.ids('A.csv', [2]), action='SPLIT_BEFORE')
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            result = save_review(self.run, self.intake, folder, 'r1')
            restored = load_review(result, self.intake)
            self.assertEqual(canonical_json(restored.snapshot()), canonical_json(self.run.snapshot()))
            hashes = {p.relative_to(result): hashlib.sha256(p.read_bytes()).hexdigest() for p in result.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError, 'already exists'):
                save_review(self.run, self.intake, folder, 'r1')
            self.assertEqual(hashes, {p.relative_to(result): hashlib.sha256(p.read_bytes()).hexdigest() for p in result.rglob('*') if p.is_file()})
            restored.decide(self.ids('A.csv', [11]), reviewer='other', reason='Remain ambiguous', assignment_status='AMBIGUOUS')
            save_review(restored, self.intake, folder, 'r2')
            self.assertEqual(len(load_review(result, self.intake).history), len(self.run.history))
        self.assertEqual(before, self.native())
        self.assertEqual(files, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in SAMPLE.iterdir() if p.is_file()})
        self.intake.validate()
        self.assertFalse(self.project.mapLayers())

    def test_persisted_membership_corruption_rejected(self):
        from transect_navigator.integration.survey_review import save_review, load_review
        self.reviewed()
        with TemporaryDirectory() as folder:
            output = save_review(self.run, self.intake, Path(folder), 'review')
            with sqlite3.connect(output / 'review.sqlite') as connection:
                connection.execute('DELETE FROM visits_membership WHERE rowid=(SELECT min(rowid) FROM visits_membership)')
            with self.assertRaisesRegex(ValueError, 'membership'):
                load_review(output, self.intake)

    def test_invalid_position_retained_not_accepted(self):
        from transect_navigator.integration.survey_review import start_review
        key = self.ids('A.csv', [2])[0]
        self.intake.model.normalized_observations[key] = replace(self.intake.model.normalized_observations[key],
            longitude=None, latitude=None, position_status='missing_geometry')
        self.run = start_review(self.intake, self.policy)
        self.assertIn(key, self.run.membership)
        with self.assertRaisesRegex(ValueError, 'Invalid-position'):
            self.assign([key])
        self.assertEqual([s.observation_ids for s in self.run.segments if key in s.observation_ids], [(key,)])

    def test_clock_reversal_not_silently_sorted(self):
        from transect_navigator.integration.survey_review import start_review
        ids = self.ids('A.csv', [1, 2, 3])
        old = self.intake.model.normalized_observations[ids[1]]
        self.intake.model.normalized_observations[ids[1]] = replace(old, timestamp_utc='2026-09-30T23:59:59Z')
        run = start_review(self.intake, self.policy)
        segments = [s for s in run.segments if set(ids) & set(s.observation_ids)]
        self.assertTrue(all('CLOCK_REVERSAL' in s.flags for s in segments))
        self.assertEqual([key for s in segments for key in s.observation_ids if key in ids], ids)
        self.assertTrue(all(s.start_utc is None for s in segments))

    def test_geographic_distance_is_measured_in_metres(self):
        from transect_navigator.integration.survey_review import review_distance
        ids = self.ids('A.csv', [1, 2])
        self.assertAlmostEqual(review_distance(self.intake)(*ids), 55.6597453966, places=6)

    def test_unknown_surveyor_retained_and_ambiguity_indefinite(self):
        self.reviewed()
        key = self.ids('C.csv', [1])[0]
        raw = self.intake.model.raw_observations[key]
        self.assertIsNone(self.intake.model.recordings[raw.recording_id].surveyor_id)
        self.assertEqual(self.run.membership[key].assignment_status, 'AMBIGUOUS')
        self.assertIsNone(self.run.membership[key].transect_id)

    def test_ui_exact_selection_assignment_revision_and_map_isolation(self):
        from qgis.PyQt.QtCore import Qt, QItemSelectionModel
        from qgis.PyQt.QtTest import QTest
        from transect_navigator.ui.survey_review_dialog import SurveyReviewDialog
        before = self.native()
        dialog = SurveyReviewDialog(self.intake, self.policy)
        try:
            key = self.ids('A.csv', [1])[0]
            rec = self.intake.model.raw_observations[key].recording_id
            dialog.recording.setCurrentIndex(dialog.recording.findData(rec))
            dialog.reviewer.setText('UI tester')
            dialog.reason.setText('Confirmed from external field notes')
            dialog.plan.setCurrentIndex(dialog.plan.findData('T01'))
            for row in (0, 2):
                dialog.table.selectionModel().select(dialog.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            self.assertEqual(dialog.selected_ids(), self.ids('A.csv', [1, 3]))
            QTest.mouseClick(dialog.buttons['ASSIGNED'], Qt.MouseButton.LeftButton)
            self.assertEqual(len(dialog.run.visits), 2)
            dialog.table.selectRow(0)
            QTest.mouseClick(dialog.buttons['TRANSITION'], Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.run.membership[key].activity_role, 'TRANSITION')
            self.assertEqual(len(dialog.run.history), 2)
            self.assertEqual(before, self.native())
            self.assertFalse(self.project.mapLayers())
            self.assertEqual(dialog.points.featureCount(), 24)
        finally:
            dialog._saved_revision = len(dialog.run.history)
            dialog.canvas.setLayers([])
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_native_crs_change_rejected(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.survey_review import start_review
        native = next(iter(self.intake.native_recordings.values()))
        native.observations.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        with self.assertRaisesRegex(ValueError, 'CRS changed'):
            start_review(self.intake, self.policy)

    def test_mapping_change_stales_run(self):
        key = next(iter(self.intake.model.recordings))
        old = self.intake.model.recordings[key]
        self.intake.model.recordings[key] = replace(old, mappings=replace(old.mappings, timezone='UTC'))
        with self.assertRaisesRegex(ValueError, 'Stale review'):
            self.run.assert_current()

    def test_duplicate_attempt_does_not_stale_retained_review(self):
        from transect_navigator.integration.survey_intake import ImportOptions
        result = self.intake.import_file(SAMPLE / 'A.csv', ImportOptions())
        self.assertEqual(result.status, 'DUPLICATE')
        self.run.assert_current()
        self.assertEqual(len(self.run.membership), 24)

    def test_source_bytes_corruption_prevents_save(self):
        from transect_navigator.integration.survey_review import save_review
        key = next(iter(self.intake.source_bytes))
        self.intake.source_bytes[key] += b'altered'
        with TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                save_review(self.run, self.intake, Path(folder), 'review')
            self.assertFalse(list(Path(folder).iterdir()))

    def test_ui_save_close_reopen_through_controls(self):
        from unittest.mock import patch
        from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox
        from transect_navigator.ui.survey_review_dialog import SurveyReviewDialog
        from transect_navigator.survey.model import canonical_json
        dialog = SurveyReviewDialog(self.intake, self.policy)
        dialog.run = self.reviewed()
        with TemporaryDirectory() as folder:
            with patch.object(QFileDialog, 'getExistingDirectory', return_value=folder), \
                    patch.object(QInputDialog, 'getText', return_value=('review', True)):
                dialog._save()
            self.assertIn('Saved and verified', dialog.status.text())
            state = canonical_json(dialog.run.snapshot())
            dialog.close()
            other = SurveyReviewDialog(self.intake, self.policy)
            try:
                with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(Path(folder) / 'review')):
                    other._open()
                self.assertEqual(canonical_json(other.run.snapshot()), state)
                other.run.decide(self.ids('A.csv', [11]), reviewer='tester', reason='reviewed uncertainty', assignment_status='AMBIGUOUS')
                with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
                    self.assertFalse(other._confirm_unsaved())
            finally:
                for widget in (dialog, other):
                    widget._saved_revision = len(widget.run.history)
                    widget.canvas.setLayers([])
                    widget.close()
                    widget.deleteLater()
                self.app.processEvents()
