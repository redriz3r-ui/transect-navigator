"""Stage 3 -> reviewed Visits -> unchanged v0.1 QA, with independent answers."""
from dataclasses import replace
import importlib.util
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from . import test_survey_review as review_fixture


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class SurveyQaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from transect_navigator.survey.qa import QaOptions
        from transect_navigator.qaqc.statistics import StatisticsOptions
        self.fixture = review_fixture.SurveyReviewTests()
        self.fixture.setUp()
        self.intake = self.fixture.intake
        self.run = self.fixture.reviewed()
        self.options = QaOptions(statistics=StatisticsOptions(coverage_max_station_gap_m=100))

    def batch(self):
        from transect_navigator.integration.survey_qa import run_batch_qa
        return run_batch_qa(self.run, self.intake, self.options, created_utc='2026-10-07T12:00:00Z')

    def test_stage4_fixture_exact_membership_and_independent_lengths(self):
        before = self.fixture.native()
        batch = self.batch()
        self.assertEqual(len(batch.visits), 6)
        self.assertTrue(all(v.processing_state == 'SUCCESS' for v in batch.visits), [v.error for v in batch.visits])
        self.assertEqual(len(batch.transects), 2)
        expected = {key for key, d in self.run.membership.items() if d.assignment_status == 'ASSIGNED'}
        actual = [s.observation_id for v in batch.visits for s in v.samples]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), 15)
        self.assertEqual(len(actual), len(set(actual)))
        for result in batch.visits:
            for sample in result.samples:
                raw = self.intake.model.raw_observations[sample.observation_id]
                self.assertEqual(raw.recording_id, result.visit.recording_id)
                self.assertEqual(raw.source_id, result.visit.source_id)
                self.assertEqual(raw.original_observation_key, sample.original_key)
        for t in batch.transects:
            count, track_degrees = (10, .0022) if t.transect_id == 'T01' else (5, .0014)
            self.assertEqual(t.accepted_observation_count, count)
            self.assertAlmostEqual(t.planned_length_m, math.pi * 6378137 / 180 * .001, delta=1e-6)
            self.assertAlmostEqual(t.summary.recorded_length_m, math.pi * 6378137 / 180 * track_degrees, delta=1e-6)
            self.assertAlmostEqual(t.summary.coverage.supported_percent, 100, delta=1e-6)
            self.assertLess(t.summary.observed_deviation.maximum, 1e-6)
            self.assertFalse(t.event_uids)
        self.assertEqual(before, self.fixture.native())
        self.assertFalse(self.intake.project.mapLayers())

    def test_unknown_chronology_does_not_recover_from_subset(self):
        ids = self.fixture.ids('C.csv', [2, 4])
        self.fixture.assign(ids)
        batch = self.batch()
        visits = [v for v in batch.visits if set(v.visit.observation_ids) & set(ids)]
        # The distant unrelated point is outside the legacy frame, visible as a failure.
        good = next(v for v in visits if ids[0] in v.visit.observation_ids)
        self.assertEqual(good.processing_state, 'SUCCESS')
        self.assertIsNotNone(good.samples[0].timestamp_utc)
        self.assertIsNone(good.samples[0].qa_timestamp_utc)
        self.assertIsNone(good.summary.recorded_length_m)
        self.assertIsNone(good.summary.coverage.supported_length_m)
        self.assertFalse(good.events)

    def test_review_edit_stales_result(self):
        from transect_navigator.integration.survey_qa import assert_batch_current
        batch = self.batch()
        self.fixture.decide(self.fixture.ids('A.csv', [2]), assignment_status='AMBIGUOUS')
        with self.assertRaisesRegex(ValueError, 'Stale QA'):
            assert_batch_current(batch, self.run, self.intake, self.options)

    def test_options_change_stales_but_same_options_remain_current(self):
        from transect_navigator.integration.survey_qa import assert_batch_current
        batch = self.batch()
        assert_batch_current(batch, self.run, self.intake, self.options)
        options = replace(self.options, statistics=replace(self.options.statistics, thresholds_m=(1,2,3)))
        with self.assertRaisesRegex(ValueError, 'Stale QA'):
            assert_batch_current(batch, self.run, self.intake, options)

    def test_failed_visit_isolated(self):
        from unittest.mock import patch
        from transect_navigator.integration import survey_qa
        original = survey_qa._analyze_visit
        bad_id = self.run.visits[0].visit_id
        def injected(intake, run, visit, *args):
            if visit.visit_id == bad_id:
                raise ValueError('Intentional unsupported Visit test')
            return original(intake, run, visit, *args)
        with patch.object(survey_qa, '_analyze_visit', side_effect=injected):
            batch = self.batch()
        self.assertEqual(sum(v.processing_state == 'FAILED' for v in batch.visits), 1)
        self.assertEqual(sum(v.processing_state == 'SUCCESS' for v in batch.visits), 5)
        self.assertTrue(any(t.errors for t in batch.transects))
        self.assertEqual(sum(t.accepted_observation_count for t in batch.transects), 15)

    def test_deterministic_storage_order(self):
        before = self.batch().as_dict()
        for name in ('sources', 'recordings', 'raw_observations', 'normalized_observations'):
            rows = getattr(self.intake.model, name)
            setattr(self.intake.model, name, dict(reversed(list(rows.items()))))
        self.assertEqual(self.batch().as_dict(), before)

    def complex_fixture(self):
        import json
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey
        from transect_navigator.integration.survey_intake import SurveyIntake, ImportOptions
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.survey.intake import FieldMappings
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.qa import QaOptions
        from transect_navigator.qaqc.events import EventOptions
        from transect_navigator.qaqc.statistics import StatisticsOptions
        from tools.survey_qa_fixture import SAMPLE
        plan = QgsVectorLayer(str(SAMPLE / 'planned.geojson'), 'plans', 'ogr')
        setup = setup_survey(plan, 'Transect_ID', survey_id='STAGE5', name='Stage 5', registry=SurveyRegistry(),
                            project=self.intake.project, created_utc='2026-10-01T12:00:00Z')
        self.intake = SurveyIntake(setup.survey, project=self.intake.project)
        self.oracle = json.loads((SAMPLE / 'expected.json').read_text())
        for name, surveyor in (('A.csv', 'S01'), ('B.csv', 'S02'), ('C.csv', None)):
            result = self.intake.import_file(SAMPLE / name, ImportOptions('x', 'y', 'EPSG:4326',
                FieldMappings(timestamp='time', accuracy='accuracy', hdop='hdop'), surveyor),
                imported_utc='2026-10-01T12:00:00Z')
            self.assertEqual(result.status, 'SUCCESS')
        self.run = start_review(self.intake, ContinuityPolicy(60, 60), created_utc='2026-10-01T13:00:00Z')
        for action in self.oracle['actions']:
            self.run.decide(self.complex_ids(action['file'], action['rows']), reviewer='Fixture analyst',
                reason='Independent authored truth', utc='2026-10-01T14:00:00Z',
                assignment_status=action['status'], activity_role=action.get('role', 'SURVEY' if action['status'] == 'ASSIGNED' else 'UNKNOWN'),
                transect_id=action.get('plan'), direction=action.get('direction', 'UNKNOWN'))
        self.options = QaOptions(StatisticsOptions(coverage_max_station_gap_m=30, coverage_max_time_gap_s=30),
            EventOptions(5, 15, 15, 30, 30))

    def complex_ids(self, filename, rows):
        raw = self.intake.model.raw_observations
        matches = {r.source_order: key for key, r in raw.items()
                   if self.intake.model.sources[r.source_id].source_file == filename}
        return [matches[row] for row in rows]

    def test_independent_complex_oracle(self):
        from tools.survey_qa_fixture import SAMPLE
        self.complex_fixture()
        before = {p.name: p.read_bytes() for p in SAMPLE.iterdir() if p.is_file()}
        batch = self.batch()
        self.assertEqual(len(self.intake.model.raw_observations), 40)
        self.assertEqual(dict(batch.disposition_counts), {'ASSIGNED': 35, 'AMBIGUOUS': 2, 'UNASSIGNED': 3, 'TRANSITION': 2})
        self.assertEqual(len(batch.visits), 4)
        self.assertTrue(all(v.processing_state == 'SUCCESS' for v in batch.visits), [v.error for v in batch.visits])
        for t in batch.transects:
            expected = self.oracle[t.transect_id]
            d = t.summary.observed_deviation
            self.assertEqual(t.evaluated_observation_count, expected['observation_count'])
            self.assertEqual(len(t.visit_ids), expected['visit_count'])
            self.assertAlmostEqual(t.planned_length_m, 100, delta=.02)
            for actual, key in ((d.mean, 'mean_m'), (d.median, 'median_m'), (d.rmse, 'rmse_m'),
                (d.p95, 'p95_m'), (d.maximum, 'maximum_m'), (t.summary.recorded_length_m, 'recorded_length_m'),
                (t.progress_support.supported_length_m, 'progress_m'), (t.summary.coverage.supported_length_m, 'corridor_m')):
                self.assertAlmostEqual(actual, expected[key], delta=.02, msg=f'{t.transect_id} {key}')
            self.assertEqual(list(t.summary.within_percent), expected['within_percent'])
        events = [e for v in batch.visits for e in v.events]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.result.observation_count, 3)
        self.assertEqual(event.result.duration_s, 20)
        self.assertAlmostEqual(event.result.event_length_m, 20, delta=.02)
        self.assertAlmostEqual(event.result.mean_deviation_m, 12, delta=.02)
        self.assertEqual(event.result.mean_gnss_accuracy_raw, 12)
        self.assertIsNotNone(event.geometry_wkb_hex)
        first = next(v for v in batch.visits if set(v.visit.observation_ids) == set(self.complex_ids('A.csv', range(1,12))))
        self.assertAlmostEqual(first.summary.coverage.supported_length_m, 60, delta=.02)
        self.assertAlmostEqual(first.progress_support.supported_length_m, 100, delta=.02)
        reverse = next(v for v in batch.visits if v.visit.transect_id == 'T02')
        self.assertTrue(all(s.deviation.signed_m > .99 for s in reverse.samples[1:-1]))
        self.assertGreater(reverse.samples[0].deviation.distance_along_m, reverse.samples[-1].deviation.distance_along_m)
        self.assertEqual(before, {p.name: p.read_bytes() for p in SAMPLE.iterdir() if p.is_file()})

    def test_complementary_partial_union_no_fake_track(self):
        self.complex_fixture()
        self.run.decide(self.complex_ids('A.csv', range(1,12)), reviewer='test', reason='exclude first visit', assignment_status='AMBIGUOUS')
        t = next(t for t in self.batch().transects if t.transect_id == 'T01')
        self.assertEqual(len(t.visit_ids), 2)
        self.assertEqual(t.evaluated_observation_count, 13)
        self.assertAlmostEqual(t.summary.recorded_length_m, 110, delta=.02)
        self.assertAlmostEqual(t.summary.coverage.supported_length_m, 100, delta=.02)
        self.assertAlmostEqual(t.progress_support.supported_length_m, 100, delta=.02)
        self.assertLess(t.summary.observed_deviation.maximum, .02)

    def test_partial_visit_adherence_separate_from_extent(self):
        self.complex_fixture()
        ids = set(self.complex_ids('B.csv', range(1,8)))
        v = next(v for v in self.batch().visits if set(v.visit.observation_ids) == ids)
        self.assertAlmostEqual(v.summary.coverage.supported_percent, 60, delta=.02)
        self.assertLess(v.summary.observed_deviation.mean, .02)
        self.assertEqual(v.processing_state, 'SUCCESS')

    def test_gap_and_excluded_interval_split_events(self):
        self.complex_fixture()
        self.assertEqual(sum(len(v.events) for v in self.batch().visits), 1)
        key = self.complex_ids('A.csv', [6])
        self.run.decide(key, reviewer='test', reason='reviewed gap', action='SPLIT_BEFORE')
        self.assertEqual(sum(len(v.events) for v in self.batch().visits), 0)
        # A transition is also a hard cut, even though neighboring timestamps are close.
        self.run.decide(key, reviewer='test', reason='transition evidence', activity_role='TRANSITION')
        batch = self.batch()
        self.assertFalse(any(s.observation_id in key for v in batch.visits for s in v.samples))
        self.assertEqual(sum(len(v.events) for v in batch.visits), 0)

    def test_aggregate_not_mean_of_visit_means_or_percentiles(self):
        self.complex_fixture()
        batch = self.batch()
        t = next(t for t in batch.transects if t.transect_id == 'T01')
        visit_means = [v.summary.observed_deviation.mean for v in batch.visits if v.visit.transect_id == 'T01']
        self.assertNotAlmostEqual(t.summary.observed_deviation.mean, sum(visit_means)/3, places=2)
        self.assertAlmostEqual(t.summary.observed_deviation.mean, 42/24, delta=.02)

    def test_save_reopen_and_refuse_overwrite(self):
        from transect_navigator.integration.survey_qa_storage import save_batch_qa, load_batch_qa
        from transect_navigator.survey.model import canonical_json
        self.complex_fixture()
        batch = self.batch()
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            output = save_batch_qa(batch, self.run, self.intake, folder, 'qa')
            restored = load_batch_qa(output, self.run, self.intake, self.options)
            self.assertEqual(canonical_json(restored.as_dict()), canonical_json(batch.as_dict()))
            before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError, 'already exists'):
                save_batch_qa(batch, self.run, self.intake, folder, 'qa')
            self.assertEqual(before, {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()})
            renamed = folder / 'moved'
            output.rename(renamed)
            self.assertEqual(load_batch_qa(renamed, self.run, self.intake, self.options), batch)

    def test_saved_stale_or_tampered_results_rejected(self):
        import sqlite3
        from transect_navigator.integration.survey_qa_storage import save_batch_qa, load_batch_qa
        batch = self.batch()
        with TemporaryDirectory() as folder:
            output = save_batch_qa(batch, self.run, self.intake, Path(folder), 'qa')
            options = replace(self.options, events=replace(self.options.events, threshold_m=8))
            with self.assertRaisesRegex(ValueError, 'Stale QA'):
                load_batch_qa(output, self.run, self.intake, options)
            with sqlite3.connect(output / 'qa.sqlite') as connection:
                connection.execute('DELETE FROM qa_observations')
            with self.assertRaisesRegex(ValueError, 'records differ'):
                load_batch_qa(output, self.run, self.intake, self.options)

    def test_engine_and_plan_changes_invalidate(self):
        from unittest.mock import patch
        from transect_navigator.integration.survey_qa import assert_batch_current
        batch = self.batch()
        with patch('transect_navigator.integration.survey_qa.engine_fingerprint', return_value='changed'):
            with self.assertRaisesRegex(ValueError, 'Stale QA'):
                assert_batch_current(batch, self.run, self.intake, self.options)
        self.intake.survey = replace(self.intake.survey, plan_revision='new')
        with self.assertRaisesRegex(ValueError, 'Stale review'):
            assert_batch_current(batch, self.run, self.intake, self.options)

    def test_source_mutation_invalidates_before_calculation(self):
        from transect_navigator.integration.survey_qa import assert_batch_current
        batch = self.batch()
        key = next(iter(self.intake.source_bytes))
        self.intake.source_bytes[key] += b'changed'
        with self.assertRaises(ValueError):
            assert_batch_current(batch, self.run, self.intake, self.options)
        with self.assertRaises(ValueError):
            self.batch()

    def test_cosmetic_layer_name_and_selection_do_not_invalidate(self):
        from transect_navigator.integration.survey_qa import assert_batch_current
        batch = self.batch()
        for native in self.intake.native_recordings.values():
            native.observations.setName('Display name changed')
            native.observations.selectAll()
        assert_batch_current(batch, self.run, self.intake, self.options)

    def test_unvisited_plan_is_retained_with_length(self):
        self.run.decide(self.fixture.ids('A.csv', [6,7,8]) + self.fixture.ids('B.gpx', [1,2], 2),
                        reviewer='test', reason='leave unresolved', assignment_status='AMBIGUOUS')
        t = next(t for t in self.batch().transects if t.transect_id == 'T02')
        self.assertEqual(t.visit_ids, ())
        self.assertGreater(t.planned_length_m, 0)
        self.assertEqual(t.summary.observation_count, 0)
        self.assertIsNone(t.summary.coverage.supported_length_m)
        self.assertIn('NO_ACCEPTED_VISITS', t.flags)

    def test_ui_run_inspect_and_stale_review(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest
        from transect_navigator.ui.survey_review_dialog import SurveyReviewDialog
        dialog = SurveyReviewDialog(self.intake, self.fixture.policy)
        try:
            # Regression: fromWkb populates geometry but returns None in this binding.
            self.assertEqual(len(dialog._map_layers), 2)
            self.assertTrue(all(layer.featureCount() == 1 for layer in dialog._map_layers))
            dialog.run = self.run
            dialog._open_qa()
            panel = dialog._qa_panel
            panel.controls['station_gap'].setValue(100)
            QTest.mouseClick(panel.run_button, Qt.MouseButton.LeftButton)
            self.assertIsNotNone(panel.batch)
            self.assertEqual(panel.transects.rowCount(), 2)
            self.assertEqual(panel.visits.rowCount(), 6)
            self.assertTrue(panel.save_button.isEnabled())
            panel._inspect_transect(0, 0)
            self.assertIn('reported_gnss', panel.detail.toPlainText())
            panel._inspect_visit(0, 0)
            self.assertIn('observation_ids', panel.detail.toPlainText())
            self.fixture.decide(self.fixture.ids('A.csv', [2]), assignment_status='AMBIGUOUS')
            dialog.refresh()
            self.assertIn('STALE', panel.status.text())
            self.assertFalse(panel.save_button.isEnabled())
        finally:
            dialog._saved_revision = len(dialog.run.history)
            dialog.canvas.setLayers([])
            if dialog._qa_window:
                dialog._qa_window.close()
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_invalid_planned_wkb_is_rejected_or_skipped_safely(self):
        from dataclasses import replace
        from transect_navigator.integration.survey_qa_input import plan_layer
        from transect_navigator.ui.survey_review_dialog import SurveyReviewDialog
        bad_plan = replace(self.intake.survey.plans[0], geometry_wkb_hex='00')
        with self.assertRaisesRegex(ValueError, 'geometry'):
            plan_layer(bad_plan)
        survey = replace(self.intake.survey,
                         plans=(bad_plan,) + self.intake.survey.plans[1:])
        self.intake.survey = survey
        dialog = SurveyReviewDialog(self.intake, self.fixture.policy)
        try:
            self.assertEqual(len(dialog._map_layers), 1)
            self.assertEqual(dialog._map_layers[0].name(), 'T02')
        finally:
            dialog._saved_revision = len(dialog.run.history)
            dialog.canvas.setLayers([])
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def add_points(self, points, *, start=0, crs='EPSG:4326'):
        from datetime import datetime, timedelta, timezone
        from qgis.core import (QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
                               QgsCoordinateTransform, QgsCoordinateReferenceSystem)
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        from tools.survey_qa_fixture import coordinate
        layer = QgsVectorLayer(f'Point?crs={crs}&field=time:string&field=accuracy:string', 'test points', 'memory')
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), layer.crs(), self.intake.project)
        for index, point in enumerate(points):
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(transform.transform(QgsPointXY(*coordinate(*point)))))
            feature.setAttributes([(datetime(2026,10,2,tzinfo=timezone.utc) + timedelta(seconds=start+index*10)).isoformat(), None])
            self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        attempt = self.intake.import_layer(layer, options=ImportOptions(mappings=FieldMappings(timestamp='time', accuracy='accuracy')))
        self.assertEqual(attempt.status, 'SUCCESS')
        return [r.observation_id for r in sorted(self.intake.model.raw_observations.values(), key=lambda r:r.source_order)
                if r.source_id == attempt.source_id]

    def review_only(self, groups):
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.survey.review import ContinuityPolicy
        self.run = start_review(self.intake, ContinuityPolicy(60,60))
        for ids, direction in groups:
            self.run.decide(ids, reviewer='test analyst', reason='independent known membership',
                assignment_status='ASSIGNED', activity_role='SURVEY', transect_id='T01', direction=direction)

    def test_equivalent_forward_reverse_and_repeated_full_support(self):
        self.complex_fixture()
        points = [(x, 1 if 0 < x < 100 else 0) for x in range(0,101,10)]
        forward = self.add_points(points)
        reverse = self.add_points(points[::-1], start=1000)
        self.review_only([(forward,'FORWARD'), (reverse,'REVERSE')])
        batch = self.batch()
        self.assertEqual(len(batch.visits), 2)
        f = next(v for v in batch.visits if v.visit.direction == 'FORWARD')
        r = next(v for v in batch.visits if v.visit.direction == 'REVERSE')
        self.assertEqual([s.deviation for s in f.samples], [s.deviation for s in r.samples][::-1])
        self.assertEqual(f.summary.observed_deviation, r.summary.observed_deviation)
        self.assertAlmostEqual(f.summary.recorded_length_m, r.summary.recorded_length_m, places=10)
        self.assertTrue(all(s.deviation.signed_m > .99 for s in r.samples[1:-1]))
        t = next(t for t in batch.transects if t.transect_id == 'T01')
        self.assertEqual(t.evaluated_observation_count, 22)
        self.assertAlmostEqual(t.summary.coverage.supported_percent, 100, delta=.00001)
        self.assertAlmostEqual(t.summary.recorded_length_m, 2 * (80 + 2*math.sqrt(101)), delta=.02)

    def test_mixed_native_crs_preserved(self):
        self.complex_fixture()
        ids = self.add_points([(0,0),(10,1),(20,1)], crs='EPSG:3857')
        self.review_only([(ids,'FORWARD')])
        v = self.batch().visits[0]
        self.assertEqual(v.processing_state, 'SUCCESS', v.error)
        from qgis.core import QgsCoordinateReferenceSystem
        self.assertEqual(QgsCoordinateReferenceSystem(v.native_crs_wkt).authid(), 'EPSG:3857')
        for sample in v.samples:
            raw = self.intake.model.raw_observations[sample.observation_id]
            import hashlib
            self.assertEqual(hashlib.sha256(bytes.fromhex(sample.native_geometry_wkb_hex)).hexdigest(), raw.raw_geometry_digest)
        self.assertAlmostEqual(v.summary.observed_deviation.mean, 2/3, delta=.02)

    def test_stationary_null_event_persistence_and_unique_event_keys(self):
        from transect_navigator.integration.survey_qa_storage import save_batch_qa, load_batch_qa
        self.complex_fixture()
        stationary = self.add_points([(20,12)] * 3)
        moving = self.add_points([(20,12),(30,12),(40,12)], start=1000)
        self.review_only([(stationary,'UNKNOWN'), (moving,'FORWARD')])
        self.options = replace(self.options, events=replace(self.options.events, minimum_station_span_m=None))
        batch = self.batch()
        events = [e for v in batch.visits for e in v.events]
        self.assertEqual(len(events), 2)
        self.assertEqual(len({e.event_uid for e in events}), 2)
        self.assertEqual({e.result.event_id for e in events}, {'T01_E001'})
        event = next(e for e in events if set(e.result.source_ids) == set(stationary))
        self.assertIsNone(event.geometry_wkb_hex)
        self.assertEqual(event.result.duration_s, 20)
        self.assertIsNotNone(next(e for e in events if set(e.result.source_ids) == set(moving)).geometry_wkb_hex)
        with TemporaryDirectory() as folder:
            output = save_batch_qa(batch, self.run, self.intake, Path(folder), 'events')
            restored = load_batch_qa(output, self.run, self.intake, self.options)
            self.assertEqual(restored, batch)

    def test_actual_far_visit_failure_does_not_remove_other_results(self):
        self.complex_fixture()
        far = self.add_points([(6000,0),(6010,0)])
        near = self.add_points([(10,1),(20,1)], start=1000)
        self.review_only([(far,'FORWARD'), (near,'FORWARD')])
        batch = self.batch()
        self.assertEqual(sorted(v.processing_state for v in batch.visits), ['FAILED','SUCCESS'])
        failed = next(v for v in batch.visits if v.processing_state == 'FAILED')
        self.assertIn('5 km radius', failed.error)
        t = next(t for t in batch.transects if t.transect_id == 'T01')
        self.assertEqual((t.accepted_observation_count,t.evaluated_observation_count), (4,2))
        self.assertIsNone(t.summary.recorded_length_m)
        self.assertAlmostEqual(t.known_track_length_m, 10, delta=.02)
        self.assertIn('PARTIAL_OR_FAILED_PROCESSING', t.flags)

    def test_syn01_exact_v01_equivalence(self):
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.integration.survey_intake import SurveyIntake, ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.qa import QaOptions
        from transect_navigator.integration.recorded import load_recorded_file
        from transect_navigator.integration.analyze import ingest_recording
        from transect_navigator.integration.comparison import compare_ingested
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.integration.events import detect_comparison_events
        from tools.synthetic_survey import SAMPLE, EVENT_OPTIONS, verify_measurements
        before = {p.name:p.read_bytes() for p in SAMPLE.iterdir() if p.is_file()}
        plan = QgsVectorLayer(str(SAMPLE/'planned_transect.geojson'), 'SYN01', 'ogr')
        setup = setup_survey(plan, 'Transect_ID', survey_id='SYN01-SURVEY', name='equivalence', registry=SurveyRegistry(), project=self.intake.project)
        self.intake = SurveyIntake(setup.survey, project=self.intake.project)
        self.intake.import_file(SAMPLE/'gps_observations.csv', ImportOptions('Longitude','Latitude','EPSG:4326',
            FieldMappings(timestamp='Timestamp',accuracy='Horizontal_Accuracy_m')))
        self.run = start_review(self.intake, ContinuityPolicy(60,60))
        self.run.decide(list(self.intake.model.raw_observations), reviewer='tester', reason='SYN01 reference',
            assignment_status='ASSIGNED', activity_role='SURVEY', transect_id='SYN01', direction='FORWARD')
        self.options = QaOptions(events=EVENT_OPTIONS)
        batch = self.batch()
        self.assertEqual(len(batch.visits), 1)
        result = batch.visits[0]
        self.assertEqual(result.processing_state, 'SUCCESS', result.error)
        source = load_recorded_file(SAMPLE/'gps_observations.csv',x_field='Longitude',y_field='Latitude',csv_crs='EPSG:4326')
        ingested = ingest_recording(plan,'Transect_ID',next(plan.getFeatures()).id(), source,
            timestamp_field='Timestamp',accuracy_field='Horizontal_Accuracy_m',project=self.intake.project)
        compared = compare_ingested(ingested,project=self.intake.project)
        summary = summarize_comparison(ingested,compared,project=self.intake.project).summary
        events = detect_comparison_events(ingested,compared,options=EVENT_OPTIONS,project=self.intake.project).detection
        self.assertEqual(tuple(s.deviation for s in result.samples), compared.rows)
        self.assertEqual(result.summary, summary)
        self.assertEqual(result.summary, batch.transects[0].summary)
        from dataclasses import asdict
        actual, expected = asdict(result.events[0].result), asdict(events.events[0])
        for key in ('group','source_ids'):
            actual.pop(key); expected.pop(key)
        self.assertEqual(actual, expected)
        verify_measurements(compared.rows, summary, events)
        self.assertEqual(before, {p.name:p.read_bytes() for p in SAMPLE.iterdir() if p.is_file()})
