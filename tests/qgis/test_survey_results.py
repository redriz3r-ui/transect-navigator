"""Independent Stage 6 oracle through real QGIS intake, review, QA and Qt controls."""
from collections import Counter
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

SAMPLE = Path(__file__).resolve().parents[2] / 'samples/survey_results'


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class SurveyResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from qgis.core import QgsProject, QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey
        from transect_navigator.integration.survey_intake import SurveyIntake, ImportOptions
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.survey.intake import FieldMappings
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.qa import QaOptions
        from transect_navigator.survey.results import CompletionPolicy, scope_evidence
        self.started = time.perf_counter()
        self.project = QgsProject()
        self.plan = QgsVectorLayer(str(SAMPLE/'planned.geojson'), 'official', 'ogr')
        self.setup = setup_survey(self.plan, 'Transect_ID', survey_id='STAGE6', name='Stage 6 oracle',
            registry=SurveyRegistry(), project=self.project, created_utc='2026-10-01T00:00:00Z')
        self.intake = SurveyIntake(self.setup.survey, project=self.project)
        self.expected = json.loads((SAMPLE/'expected.json').read_text())
        for action in self.expected['actions']:
            imported = self.intake.import_file(SAMPLE/action['file'], ImportOptions('longitude','latitude','EPSG:4326',
                FieldMappings(timestamp='time', accuracy='accuracy', hdop='hdop'), action['surveyor']),
                imported_utc='2026-10-01T12:00:00Z')
            self.assertEqual(imported.status, 'SUCCESS')
        self.run = start_review(self.intake, ContinuityPolicy(60,30), created_utc='2026-10-01T13:00:00Z')
        for action in self.expected['actions']:
            kwargs = dict(assignment_status=action['status'])
            if action['status'] == 'ASSIGNED':
                kwargs.update(activity_role='SURVEY', transect_id=action['plan'], direction=action['direction'])
            self.run.decide(self.ids(action['file']), reviewer='Independent fixture reviewer',
                reason='Authored exact membership, not inferred from GPS', utc='2026-10-01T14:00:00Z', **kwargs)
        self.options = QaOptions()
        self.batch = run_batch_qa(self.run, self.intake, self.options, created_utc='2026-10-01T15:00:00Z')
        self.policy = CompletionPolicy('S6-full-ends',100,0,.02,'Fixture reviewer','Synthetic operational test only',
            '2026-10-01T16:00:00Z')
        self.scopes = scope_evidence((), self.batch, self.run.membership, self.ids('ambiguous.csv'), ('T05',),
            reviewer='Fixture reviewer',reason='Independent field truth links uncertainty to T05',utc='2026-10-01T16:00:00Z')

    def ids(self, filename):
        return tuple(r.observation_id for r in sorted(self.intake.model.raw_observations.values(), key=lambda r:r.source_order)
                     if self.intake.model.sources[r.source_id].source_file == filename)

    def result(self, **kwargs):
        from transect_navigator.integration.survey_results import build_results
        return build_results(self.batch,self.run,self.intake,kwargs.pop('policy',self.policy),
            kwargs.pop('scopes',self.scopes),created_utc='2026-10-01T17:00:00Z',**kwargs)

    def snapshot(self):
        return (asdict(self.intake.survey), self.run.snapshot(), self.batch.as_dict(),
            {k:[(f.attributes(),bytes(f.geometry().asWkb())) for f in v.observations.getFeatures()]
                for k,v in self.intake.native_recordings.items()},
            [(f.attributes(),bytes(f.geometry().asWkb())) for f in self.plan.getFeatures()],
            self.plan.renderer().dump())

    def test_full_independent_oracle_and_count_reconciliation(self):
        result = self.result()
        self.assertEqual(len(self.intake.model.raw_observations),85)
        self.assertEqual(dict(self.batch.disposition_counts)['ASSIGNED'],82)
        self.assertEqual(len(result.transects),8)
        self.assertEqual(len({r.transect_id for r in result.transects}),8)
        self.assertEqual({r.transect_id:r.final_status for r in result.transects},self.expected['statuses'])
        self.assertEqual(dict(result.summary.status_counts),self.expected['status_counts'])
        self.assertEqual(sum(dict(result.summary.status_counts).values()),result.summary.planned_count)
        for row in result.transects:
            self.assertAlmostEqual(row.confirmed_progress_m,self.expected['progress_by_plan'][row.transect_id],delta=.02)

    def test_independent_weighted_totals_not_average_percentages(self):
        s = self.result().summary
        for actual, expected in ((s.total_planned_length_m,1000),(s.confirmed_progress_m,640),
                (s.confirmed_corridor_m,540),(s.accepted_track_length_m,740)):
            self.assertAlmostEqual(actual,expected,delta=.02)
        self.assertAlmostEqual(s.progress_lower_bound_percent,64,delta=.002)
        self.assertAlmostEqual(s.corridor_lower_bound_percent,54,delta=.002)
        self.assertNotAlmostEqual(s.progress_lower_bound_percent,self.expected['naive_mean_percent'],places=1)
        self.assertEqual(s.unresolved_observation_count,3)
        self.assertEqual(s.unscoped_observation_count,0)
        self.assertEqual(s.processing_state,'FINISHED_WITH_REVIEW')

    def test_direction_and_repeated_visit_cap(self):
        rows = {r.transect_id:r for r in self.result().transects}
        self.assertEqual(rows['T02'].final_status,'COMPLETE')
        self.assertEqual(rows['T06'].final_status,'COMPLETE')
        self.assertEqual(len(rows['T06'].qa.visit_ids),2)
        self.assertAlmostEqual(rows['T06'].qa.known_track_length_m,200,delta=.02)
        self.assertAlmostEqual(rows['T06'].confirmed_progress_m,100,delta=.02)

    def test_completion_independent_of_quality(self):
        rows = {r.transect_id:r for r in self.result().transects}
        self.assertEqual(rows['T03'].final_status,'INCOMPLETE')
        self.assertLess(rows['T03'].qa.summary.observed_deviation.maximum,.02)
        poor = rows['T07']
        self.assertEqual(poor.final_status,'COMPLETE')
        self.assertAlmostEqual(poor.qa.summary.observed_deviation.mean,20,delta=.02)
        self.assertAlmostEqual(poor.confirmed_corridor_m,0,delta=.02)
        self.assertAlmostEqual(poor.confirmed_progress_m,100,delta=.02)
        self.assertTrue(poor.start_supported and poor.end_supported)

    def test_gap_and_unsurveyed_remain_visible(self):
        rows = {r.transect_id:r for r in self.result().transects}
        self.assertEqual(len(rows['T04'].qa.visit_ids),2)
        self.assertEqual(rows['T04'].final_status,'INCOMPLETE')
        self.assertAlmostEqual(rows['T04'].confirmed_progress_m,80,delta=.02)
        self.assertEqual(rows['T08'].final_status,'NOT_SURVEYED')
        self.assertEqual(rows['T08'].qa.accepted_observation_count,0)

    def test_unscoped_uncertainty_never_silently_ignored(self):
        result = self.result(scopes=())
        self.assertEqual(dict(result.summary.status_counts)['REVIEW'],8)
        self.assertEqual(result.summary.unscoped_observation_count,3)
        self.assertTrue(all(len(r.unresolved_observation_ids)==3 for r in result.transects))
        self.assertEqual(result.summary.unresolved_observation_count,3)  # Not 24.

    def test_scope_revision_is_append_only_and_cannot_dismiss_ambiguity(self):
        from transect_navigator.survey.results import scope_evidence
        revised = scope_evidence(self.scopes,self.batch,self.run.membership,self.ids('ambiguous.csv'),('T05','T08'),
            reviewer='Reviewer two',reason='Both plans require review')
        self.assertEqual(revised[0],self.scopes[0])
        self.assertEqual(revised[1].revision,2)
        self.assertEqual(dict(self.result(scopes=revised).summary.status_counts)['REVIEW'],2)
        with self.assertRaisesRegex(ValueError,'cannot be dismissed'):
            scope_evidence((),self.batch,self.run.membership,self.ids('ambiguous.csv'),(),reviewer='R',reason='No')

    def test_source_plan_review_and_qa_unchanged(self):
        from transect_navigator.integration.survey_results import status_layer
        before = self.snapshot()
        layer = status_layer(self.result(),self.intake.survey)
        self.assertEqual(before,self.snapshot())
        self.assertFalse(self.project.mapLayers())
        self.assertEqual(layer.featureCount(),8)

    def test_status_map_geometry_crs_counts_and_styles(self):
        from transect_navigator.integration.survey_results import status_layer
        layer = status_layer(self.result(),self.intake.survey)
        expected = {f['Transect_ID']:bytes(f.geometry().asWkb()) for f in self.plan.getFeatures()}
        self.assertEqual({f['Transect_ID']:bytes(f.geometry().asWkb()) for f in layer.getFeatures()},expected)
        self.assertEqual(layer.crs(),self.plan.crs())
        self.assertEqual(Counter(f['Status'] for f in layer.getFeatures()),self.expected['status_counts'])
        self.assertEqual(layer.renderer().classAttribute(),'Status')
        self.assertEqual(len(layer.renderer().categories()),4)
        self.assertTrue(layer.readOnly())

    def test_unsupported_null_plan_retained_in_actual_map_and_totals(self):
        from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey
        from transect_navigator.integration.survey_intake import SurveyIntake
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results, status_layer
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.survey.review import ContinuityPolicy
        layer = QgsVectorLayer('LineString?crs=EPSG:3857&field=Transect_ID:string','projected plans','memory')
        good,bad = QgsFeature(layer.fields()),QgsFeature(layer.fields())
        good.setAttributes(['valid'])
        good.setGeometry(QgsGeometry.fromWkt('LINESTRING(0 0,100 0)'))
        bad.setAttributes(['null'])
        self.assertTrue(layer.dataProvider().addFeatures([good,bad])[0])
        setup = setup_survey(layer,'Transect_ID',survey_id='null-case',name='Unknown length',registry=SurveyRegistry(),project=self.project)
        intake = SurveyIntake(setup.survey,project=self.project)
        run = start_review(intake,ContinuityPolicy(60,30))
        batch = run_batch_qa(run,intake)
        result = build_results(batch,run,intake,self.policy)
        mapped = status_layer(result,intake.survey)
        self.assertEqual(mapped.featureCount(),2)
        self.assertEqual(mapped.crs().authid(),'EPSG:3857')
        rows = {f['Transect_ID']:f for f in mapped.getFeatures()}
        self.assertTrue(rows['null'].geometry().isNull())
        self.assertEqual(rows['null']['Status'],'REVIEW')
        self.assertEqual(bytes(rows['valid'].geometry().asWkb()),bytes(good.geometry().asWkb()))
        self.assertEqual(result.summary.unknown_length_count,1)
        self.assertAlmostEqual(result.summary.known_planned_length_m,100,delta=.02)
        self.assertIsNone(result.summary.total_planned_length_m)
        self.assertIsNone(result.summary.progress_lower_bound_percent)

    def test_changed_review_rejects_current_result(self):
        from transect_navigator.integration.survey_results import assert_results_current
        result = self.result()
        self.run.decide(self.ids('forward.csv')[:1],reviewer='R',reason='Revise',assignment_status='AMBIGUOUS')
        with self.assertRaisesRegex(ValueError,'Stale QA'):
            assert_results_current(result,self.batch,self.run,self.intake,self.policy,self.scopes)

    def test_changed_plan_rejects_results(self):
        from transect_navigator.integration.survey_results import assert_results_current
        result = self.result()
        self.intake.survey = replace(self.intake.survey,plan_revision='new')
        with self.assertRaises(ValueError):
            assert_results_current(result,self.batch,self.run,self.intake,self.policy,self.scopes)

    def test_policy_change_recomputes_without_point_qa(self):
        from transect_navigator.integration.survey_results import assert_results_current
        result = self.result()
        policy = replace(self.policy,required_support_percent=60,endpoint_tolerance_m=40,policy_id='changed')
        with self.assertRaisesRegex(ValueError,'Stale Survey'):
            assert_results_current(result,self.batch,self.run,self.intake,policy,self.scopes)
        with patch('transect_navigator.integration.survey_qa.run_batch_qa',side_effect=AssertionError('QA rerun')):
            changed = self.result(policy=policy)
        self.assertEqual(next(r for r in changed.transects if r.transect_id=='T03').final_status,'COMPLETE')
        self.assertEqual([r.qa for r in result.transects],[r.qa for r in changed.transects])

    def test_changed_qa_options_rejected(self):
        from transect_navigator.integration.survey_results import assert_results_current
        with self.assertRaisesRegex(ValueError,'Stale QA'):
            assert_results_current(self.result(),self.batch,self.run,self.intake,self.policy,self.scopes,
                options=replace(self.options,statistics=replace(self.options.statistics,thresholds_m=(1,2,3))))

    def test_save_reopen_and_no_overwrite(self):
        from transect_navigator.integration.survey_results_storage import save_results, load_results
        result = self.result()
        with TemporaryDirectory() as folder:
            path = save_results(result,self.batch,self.run,self.intake,Path(folder),'results')
            loaded, batch = load_results(path,self.run,self.intake,self.options)
            self.assertEqual(loaded,result)
            self.assertEqual(batch,self.batch)
            before = (path/'results.sqlite').read_bytes()
            with self.assertRaisesRegex(ValueError,'already exists'):
                save_results(result,self.batch,self.run,self.intake,Path(folder),'results')
            self.assertEqual(before,(path/'results.sqlite').read_bytes())

    def test_saved_results_modified_checksum_cannot_hide_wrong_summary(self):
        import sqlite3
        from transect_navigator.integration.survey_results_storage import save_results, load_results
        from transect_navigator.survey.model import canonical_json, digest
        with TemporaryDirectory() as folder:
            path = save_results(self.result(),self.batch,self.run,self.intake,Path(folder),'results')
            with sqlite3.connect(path/'results.sqlite') as db:
                data = json.loads(db.execute('SELECT json FROM results').fetchone()[0])
                data['summary']['confirmed_progress_m'] = 999
                db.execute('UPDATE results SET json=?,sha256=?',(canonical_json(data),digest(data)))
            with self.assertRaisesRegex(ValueError,'do not reproduce'):
                load_results(path,self.run,self.intake,self.options)

    def panel(self):
        from transect_navigator.ui.survey_qa_panel import SurveyQaPanel
        from transect_navigator.ui.survey_results_panel import SurveyResultsPanel
        qa = SurveyQaPanel(self.intake,lambda:self.run)
        qa.batch = self.batch
        qa._populate()
        panel = SurveyResultsPanel(qa)
        panel.policy, panel.evidence_history = self.policy,self.scopes
        panel.recompute()
        self.addCleanup(panel.close)
        self.addCleanup(qa.close)
        return qa,panel

    def test_ui_filters_inspection_and_map_without_qa_rerun(self):
        qa,panel = self.panel()
        self.assertIn('CURRENT',panel.status.text())
        self.assertEqual(panel.table.rowCount(),8)
        with patch('transect_navigator.integration.survey_qa.run_batch_qa',side_effect=AssertionError('QA rerun')):
            panel.status_filter.setCurrentText('COMPLETE')
            self.assertEqual(panel.table.rowCount(),4)
            panel.id_filter.setText('T07')
            self.assertEqual(panel.table.rowCount(),1)
            panel.inspect(0,0)
            self.assertIn('events',panel.detail.toPlainText())
            self.assertEqual(qa.visits.rowCount(),1)
            panel.id_filter.clear()
            panel.status_filter.setCurrentText('All statuses')
            panel.review_only.setChecked(True)
            self.assertEqual(panel.table.rowCount(),1)
            panel.review_only.setChecked(False)
            panel.surveyor_filter.setCurrentIndex(panel.surveyor_filter.findData('S02'))
            self.assertEqual(panel.table.rowCount(),3)
        self.assertEqual(len(panel.canvas.layers()),1)
        self.assertFalse(self.project.mapLayers())

    def test_ui_stale_clears_current_totals_and_map(self):
        qa,panel = self.panel()
        qa.controls['c'].setValue(11)
        panel.refresh_validity()
        self.assertIn('STALE',panel.status.text())
        self.assertIn('unavailable',panel.summary.text())
        self.assertEqual(panel.table.rowCount(),0)
        self.assertFalse(panel.canvas.layers())
        self.assertFalse(panel.save_button.isEnabled())

    def test_ui_policy_must_be_selected_explicitly(self):
        qa,panel = self.panel()
        panel.support.setValue(95)
        self.assertIsNone(panel.policy)
        panel.recompute()
        self.assertIn('Select a completion policy explicitly',panel.status.text())

    def test_ui_policy_and_relevance_buttons(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest
        qa,panel = self.panel()
        panel.policy_id.setText('analyst-policy')
        panel.reviewer.setText('Analyst')
        panel.reason.setText('Explicit full-support project policy')
        QTest.mouseClick(panel.apply_policy,Qt.MouseButton.LeftButton)
        self.assertEqual(panel.policy.policy_id,'analyst-policy')
        panel.unresolved.selectAll()
        panel.plans.item(7).setSelected(True)
        panel.scope_reason.setText('Revised relevance to T08')
        QTest.mouseClick(panel.scope_button,Qt.MouseButton.LeftButton)
        self.assertEqual(len(panel.evidence_history),2)
        QTest.mouseClick(panel.build_button,Qt.MouseButton.LeftButton)
        rows = {r.transect_id:r for r in panel.result.transects}
        self.assertEqual(rows['T08'].final_status,'REVIEW')
        self.assertEqual(rows['T05'].final_status,'NOT_SURVEYED')

    def test_ui_open_snapshot_restores_policy_history_and_totals(self):
        from transect_navigator.integration.survey_results_storage import save_results
        qa,panel = self.panel()
        with TemporaryDirectory() as folder:
            path = save_results(self.result(),self.batch,self.run,self.intake,Path(folder),'results')
            with patch('transect_navigator.ui.survey_results_panel.QFileDialog.getExistingDirectory',return_value=str(path)):
                panel._open()
            self.assertEqual(panel.result,self.result())
            self.assertEqual(panel.policy,self.policy)
            self.assertEqual(panel.evidence_history,self.scopes)
            self.assertEqual(panel.table.rowCount(),8)
            self.assertIn('CURRENT',panel.status.text())

    def test_qa_parent_notifies_results_on_review_change(self):
        from transect_navigator.ui.survey_qa_panel import SurveyQaPanel
        qa = SurveyQaPanel(self.intake,lambda:self.run)
        self.addCleanup(qa.close)
        qa.batch = self.batch
        qa._populate()
        qa._open_results()
        panel = qa._results_panel
        panel.policy,panel.evidence_history = self.policy,self.scopes
        panel.recompute()
        self.run.decide(self.ids('forward.csv')[:1],reviewer='R',reason='Revise',assignment_status='AMBIGUOUS')
        qa.refresh_validity()
        self.assertIn('STALE',panel.status.text())
        self.assertFalse(panel.canvas.layers())
        qa._results_window.close()

    def test_reproducible_independent_fixture_files(self):
        from tools.survey_results_fixture import generate
        with TemporaryDirectory() as folder:
            generate(Path(folder))
            for path in Path(folder).iterdir():
                self.assertEqual(path.read_bytes(),(SAMPLE/path.name).read_bytes())

    def test_preparation_unchanged(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        before = self.snapshot()
        navigation = prepare_survey_navigation(self.setup,spacing_m=10,project=self.project)
        # 1,000 m / 10 + one start for each of 8 lines.
        self.assertEqual(navigation.prepared.waypoints.featureCount(),108)
        self.result()
        self.assertEqual(before,self.snapshot())
