"""Real QGIS event-layer and UI checks, never replaced by fake GIS implementations."""
import importlib.util
import json
import unittest


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None,'PyQGIS unavailable; event layers/UI not executed')
class EventRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def fixture(self,xy,missing_time=False):
        from tests.qgis import test_comparison
        helper = test_comparison.ComparisonRuntimeTests()
        source = helper.points(xy)
        for index,feature in enumerate(source.getFeatures()):
            source.dataProvider().changeAttributeValues({feature.id():{1:None if missing_time else f'2026-01-01T00:00:{index*5:02d}Z'}})
        ingested = helper.ingest(source)
        return ingested,helper.compare(ingested)

    def test_event_attributes_and_geometry_are_original_fix_locations(self):
        from transect_navigator.integration.events import detect_comparison_events
        ingested,compared = self.fixture([(10,6),(20,8),(30,0)])
        before = [(f.attributes(),bytes(f.geometry().asWkb())) for f in ingested.raw_observations.getFeatures()]
        result = detect_comparison_events(ingested,compared)
        self.assertEqual(result.layer.featureCount(),1)
        f = next(result.layer.getFeatures())
        self.assertEqual(f['Event_ID'],'T04_E001')
        self.assertAlmostEqual(f['Mean_Deviation'],7,places=5)
        self.assertAlmostEqual(f['Event_Length'],10,places=5)
        self.assertEqual(f['Duration'],5)
        self.assertEqual(f['Observation_Count'],2)
        self.assertEqual(f['Mean_GNSS_Accuracy'],4.5)
        vertices = f.geometry().asPolyline()
        self.assertAlmostEqual(vertices[0].y(),6,places=5)
        self.assertAlmostEqual(vertices[1].y(),8,places=5)
        self.assertEqual(before,[(f.attributes(),bytes(f.geometry().asWkb())) for f in ingested.raw_observations.getFeatures()])
        metadata = json.loads(result.layer.customProperty('transect_navigator/provenance'))
        self.assertEqual(metadata['event_options']['threshold_m'],5)

    def test_unknown_chronology_is_visible_not_a_false_zero_result(self):
        from transect_navigator.integration.events import detect_comparison_events
        ingested,compared = self.fixture([(10,6),(20,6)],True)
        result = detect_comparison_events(ingested,compared)
        self.assertEqual(result.layer.featureCount(),0)
        self.assertEqual(result.validation.featureCount(),1)
        self.assertEqual(next(result.validation.getFeatures())['Code'],'UNKNOWN_CHRONOLOGY')

    def test_stationary_event_retains_attributes_without_invalid_line(self):
        from transect_navigator.integration.events import detect_comparison_events
        ingested,compared = self.fixture([(10,6),(10,6)])
        result = detect_comparison_events(ingested,compared)
        feature = next(result.layer.getFeatures())
        self.assertTrue(feature.geometry().isNull())
        self.assertIn('stationary',feature['Geometry_Status'])
        self.assertEqual(feature['Observation_Count'],2)

    def test_no_events_is_a_valid_empty_layer(self):
        from transect_navigator.integration.events import detect_comparison_events
        ingested,compared = self.fixture([(10,0),(20,0)])
        result = detect_comparison_events(ingested,compared)
        self.assertTrue(result.layer.isValid())
        self.assertEqual(result.layer.featureCount(),0)
        self.assertEqual(result.validation.featureCount(),0)

    def test_ui_event_controls(self):
        from transect_navigator.ui.analyze_panel import AnalyzePanel
        panel = AnalyzePanel()
        try:
            self.assertEqual(panel.event_threshold.value(),5)
            self.assertTrue(panel.event_min_duration_enabled.isChecked())
            self.assertFalse(panel.event_min_distance_enabled.isChecked())
            panel.event_min_distance_enabled.setChecked(True)
            self.assertTrue(panel.event_min_distance.isEnabled())
            panel.event_min_duration_enabled.setChecked(False)
            self.assertFalse(panel.event_min_duration.isEnabled())
        finally:
            panel.deleteLater()
