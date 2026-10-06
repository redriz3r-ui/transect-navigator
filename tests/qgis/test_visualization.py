"""Real PyQGIS/Qt presentation tests, skipped rather than emulated without QGIS."""
import importlib.util
import json
import unittest
from unittest.mock import patch
from . import test_comparison


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable; visualization not executed')
class VisualizationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from qgis.core import QgsProject
        self.project = QgsProject()
        self.fixture = test_comparison.ComparisonRuntimeTests()

    def tearDown(self):
        self.project.clear()

    def results(self):
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.integration.events import detect_comparison_events
        ingested = self.fixture.ingest(self.fixture.points([(0,0),(10,1),(20,7),(30,8),(40,0)]))
        comparison = self.fixture.compare(ingested)
        summary = summarize_comparison(ingested, comparison, project=self.project)
        events = detect_comparison_events(ingested, comparison, project=self.project)
        return ingested, comparison, summary, events

    def publish(self, results):
        from transect_navigator.integration.visualization import publish_analysis
        return publish_analysis(*results, id_field='tid', spacing_m=10, project=self.project)

    def test_complete_group_styles_and_diagnostics(self):
        from qgis.core import QgsRuleBasedRenderer
        results = self.results()
        group = self.publish(results)
        self.assertEqual(group.name(), 'Transect Navigator — T04')
        names = {node.name() for node in group.findLayers()}
        self.assertTrue({'Planned Transect', 'Navigation Waypoints', 'Raw GPS Track',
            'Raw GPS Observations', 'QA Observations', 'Deviation Events'} <= names)
        self.assertEqual(len(group.findLayers()), 12)
        debug = group.findGroup('Diagnostics — normalized data and nearest locations')
        self.assertFalse(debug.itemVisibilityChecked())
        self.assertIsInstance(results[1].observations.renderer(), QgsRuleBasedRenderer)
        nav = next(node.layer() for node in group.findLayers() if node.name() == 'Navigation Waypoints')
        self.assertEqual(nav.featureCount(), 11)
        self.assertAlmostEqual(max(f['Distance_Along_m'] for f in nav.getFeatures()), 100, places=5)
        self.assertTrue(nav.readOnly())

    def test_publication_does_not_change_geometry_attributes_or_sources(self):
        from transect_navigator.integration.analyze import ingest_recording
        from transect_navigator.integration.comparison import compare_ingested
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.integration.events import detect_comparison_events
        plan, source = self.fixture.plan(), self.fixture.points([(10,1),(20,2)])
        def records(layer):
            return [(bytes(f.geometry().asWkb()), f.attributes()) for f in layer.getFeatures()]
        originals = records(plan), records(source), plan.renderer().dump(), source.renderer().dump()
        ingested = ingest_recording(plan,'tid',next(plan.getFeatures()).id(),source,timestamp_field='time')
        comparison = compare_ingested(ingested)
        summary = summarize_comparison(ingested,comparison)
        events = detect_comparison_events(ingested,comparison)
        raw = records(ingested.raw_observations)
        qa = records(comparison.observations)
        self.publish((ingested,comparison,summary,events))
        self.assertEqual(originals, (records(plan),records(source),plan.renderer().dump(),source.renderer().dump()))
        self.assertEqual(raw, records(ingested.raw_observations))
        self.assertEqual(qa, records(comparison.observations))

    def test_renderer_matches_inclusive_boundaries(self):
        from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature
        results = self.results()
        self.publish(results)
        layer = results[1].observations
        mapping = json.loads(layer.customProperty('transect_navigator/provenance'))['qa_field_mapping']
        rules = layer.renderer().rootRule().children()[:-1]
        context = QgsExpressionContext()
        context.setFields(layer.fields())
        for value, expected in ((0,0),(3,0),(3.00001,1),(5,1),(5.00001,2),(10,2),(11,3)):
            feature = QgsFeature(layer.fields())
            feature[mapping['QA_Deviation_m']] = value
            context.setFeature(feature)
            matching = [i for i,r in enumerate(rules) if QgsExpression(r.filterExpression()).evaluate(context)]
            self.assertEqual(matching, [expected])

    def test_colliding_qa_fields_use_provenance_mapping(self):
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType
        from transect_navigator.integration.visualization import style_qa
        source = self.fixture.points([(10,1)])
        source.dataProvider().addAttributes([QgsField('QA_Deviation_m', QMetaType.Type.Double)])
        source.updateFields()
        compared = self.fixture.compare(self.fixture.ingest(source))
        style_qa(compared.observations, (3,5,10))
        mapping = json.loads(compared.observations.customProperty('transect_navigator/provenance'))['qa_field_mapping']
        self.assertNotEqual(mapping['QA_Deviation_m'], 'QA_Deviation_m')
        expression = compared.observations.renderer().rootRule().children()[0].filterExpression()
        self.assertIn('"'+mapping['QA_Deviation_m']+'"', expression)

    def test_repeat_runs_keep_previous_group(self):
        first = self.publish(self.results())
        ids = {node.layerId() for node in first.findLayers()}
        second = self.publish(self.results())
        self.assertEqual(second.name(), 'Transect Navigator — T04 (2)')
        self.assertTrue(ids <= set(self.project.mapLayers()))
        self.assertEqual(len(self.project.mapLayers()), 24)

    def test_failure_rolls_back_only_new_layers(self):
        sentinel = self.fixture.plan()
        self.project.addMapLayer(sentinel)
        actual = self.project.addMapLayer
        calls = 0
        def add(layer, legend=True):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise ValueError('Injected provider publication failure')
            return actual(layer, legend)
        with patch.object(self.project, 'addMapLayer', side_effect=add):
            with self.assertRaisesRegex(ValueError, 'Injected'):
                self.publish(self.results())
        self.assertEqual(set(self.project.mapLayers()), {sentinel.id()})
        self.assertIsNone(self.project.layerTreeRoot().findGroup('Transect Navigator — T04'))

    def test_private_xy_navigation_preserves_z_plan(self):
        from transect_navigator.integration.visualization import navigation_for_result
        plan = self.fixture.layer(['LINESTRING Z (0 0 5, 0.0008983152841195 0 8)'], 'LineStringZ')
        before = bytes(next(plan.getFeatures()).geometry().asWkb())
        nav = navigation_for_result(plan, 'tid', 10, self.project)
        self.assertEqual(nav.featureCount(), 11)
        self.assertEqual(bytes(next(plan.getFeatures()).geometry().asWkb()), before)

    def test_results_snapshot_and_profile_survive_project_removal(self):
        from transect_navigator.ui.results_panel import ResultsPanel
        results = self.results()
        group = self.publish(results)
        panel = ResultsPanel()
        try:
            panel.set_result({'summary':results[2].summary, 'events':results[3].detection,
                'rows':results[1].rows, 'spacing_m':10, 'group':group.name(),
                'has_track':True, 'time':'2026-01-01T00:00:00Z'})
            self.project.clear()
            self.assertGreater(panel.table.rowCount(), 15)
            self.assertEqual(len(panel.profile.points), 5)
            panel.mode.setCurrentIndex(1)
            panel.resize(800,800)
            panel.show()
            self.app.processEvents()
            self.assertFalse(panel.grab().isNull())
            self.assertIn('not regulatory', panel.note.text())
        finally:
            panel.close()
            panel.deleteLater()

    def test_missing_chronology_does_not_create_raw_track(self):
        from transect_navigator.integration.analyze import ingest_recording
        from transect_navigator.integration.comparison import compare_ingested
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.integration.events import detect_comparison_events
        plan = self.fixture.plan()
        ingested = ingest_recording(plan,'tid',next(plan.getFeatures()).id(), self.fixture.points([(10,1)]))
        comparison = compare_ingested(ingested)
        group = self.publish((ingested,comparison,summarize_comparison(ingested,comparison),
                              detect_comparison_events(ingested,comparison)))
        self.assertNotIn('Raw GPS Track', {node.name() for node in group.findLayers()})
