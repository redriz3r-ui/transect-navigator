"""Actual QGIS statistics adapters; explicitly skipped without PyQGIS."""
import importlib.util
import unittest
import json


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None,'PyQGIS unavailable; statistics table/UI tests not executed')
class StatisticsRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def fixture(self, xy, accuracy='4.5'):
        from tests.qgis import test_comparison
        helper = test_comparison.ComparisonRuntimeTests()
        ingestion = helper.ingest(helper.points(xy,accuracy))
        comparison = helper.compare(ingestion)
        return ingestion,comparison

    def test_numeric_summary_and_native_track_length(self):
        from transect_navigator.integration.statistics import summarize_comparison
        ingested,compared = self.fixture([(10,0),(20,0),(30,0)])
        result = summarize_comparison(ingested,compared)
        self.assertAlmostEqual(result.summary.recorded_length_m,20,places=5)
        self.assertAlmostEqual(result.summary.planned_length_m,100,places=5)
        self.assertAlmostEqual(result.summary.coverage.supported_length_m,20,places=5)
        self.assertEqual(result.table.featureCount(),1)
        row = next(result.table.getFeatures())
        self.assertEqual(row['Observation_Count'],3)
        self.assertAlmostEqual(row['Mean_Deviation_m'],0,places=5)
        self.assertEqual(row['Within_A_pct'],100)
        metadata = json.loads(result.table.customProperty('transect_navigator/provenance'))
        self.assertFalse(metadata['thresholds_are_compliance_standards'])

    def test_gnss_separate_and_custom_thresholds(self):
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.qaqc.statistics import StatisticsOptions
        ingested,compared = self.fixture([(10,1),(20,5)],accuracy='100')
        result = summarize_comparison(ingested,compared,options=StatisticsOptions((2,6,10)))
        self.assertAlmostEqual(result.summary.observed_deviation.mean,3,places=5)
        self.assertEqual(result.summary.reported_gnss.mean,100)
        self.assertEqual(result.summary.within_percent,(50,100,100))
        self.assertEqual(next(result.table.getFeatures())['Threshold_A_m'],2)

    def test_single_point_unknown_track_and_coverage(self):
        from qgis.core import NULL
        from transect_navigator.integration.statistics import summarize_comparison
        ingested,compared = self.fixture([(10,5)],accuracy=None)
        result = summarize_comparison(ingested,compared)
        self.assertIsNone(result.summary.recorded_length_m)
        self.assertIsNone(result.summary.coverage.supported_percent)
        row = next(result.table.getFeatures())
        self.assertEqual(row['GNSS_Mean_Raw'],NULL)
        self.assertEqual(row['Recorded_Length_m'],NULL)

    def test_raw_sources_remain_unchanged(self):
        from transect_navigator.integration.statistics import summarize_comparison
        ingested,compared = self.fixture([(10,1),(20,5)])
        before = [(f.attributes(),bytes(f.geometry().asWkb())) for f in ingested.raw_observations.getFeatures()]
        summarize_comparison(ingested,compared)
        self.assertEqual(before,[(f.attributes(),bytes(f.geometry().asWkb())) for f in ingested.raw_observations.getFeatures()])

    def test_ui_thresholds_and_validation(self):
        from transect_navigator.ui.analyze_panel import AnalyzePanel
        panel = AnalyzePanel()
        try:
            self.assertEqual([x.value() for x in panel.thresholds],[3,5,10])
            panel.thresholds[0].setValue(50)
            panel.compare_data()
            self.assertIn('A ≤ B ≤ C',panel.status.text())
            self.assertTrue(panel.compare.isEnabled())
        finally:
            panel.deleteLater()
