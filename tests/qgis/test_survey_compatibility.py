"""Actual PyQGIS equivalence, independent SYN01 oracle and unchanged input guards."""
import importlib.util
import unittest

from tools.synthetic_survey import SAMPLE, EVENT_OPTIONS, verify_measurements


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class SingleSurveyCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from qgis.core import QgsProject, QgsVectorLayer
        from transect_navigator.integration.recorded import load_recorded_file
        from transect_navigator.integration.analyze import ingest_recording
        self.project = QgsProject()
        self.plan = QgsVectorLayer(str(SAMPLE/'planned_transect.geojson'), 'plan', 'ogr')
        source = load_recorded_file(SAMPLE/'gps_observations.csv', x_field='Longitude',
                                   y_field='Latitude', csv_crs='EPSG:4326')
        self.ingested = ingest_recording(
            self.plan, 'Transect_ID', next(self.plan.getFeatures()).id(), source,
            timestamp_field='Timestamp', accuracy_field='Horizontal_Accuracy_m',
            project=self.project)

    @staticmethod
    def snapshot(layer):
        return (layer.crs().toWkt(), layer.wkbType(),
                [(f.name(), f.type(), f.length(), f.precision()) for f in layer.fields()],
                [(bytes(f.geometry().asWkb()), f.attributes()) for f in layer.getFeatures()])

    def compare_paths(self, statistics_options=None, event_options=EVENT_OPTIONS):
        from transect_navigator.integration.comparison import compare_ingested
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.integration.events import detect_comparison_events
        from transect_navigator.integration.survey import analyze_single_transect_survey
        from transect_navigator.qaqc.statistics import StatisticsOptions
        options = statistics_options if statistics_options is not None else StatisticsOptions()
        inputs = [self.plan] + self.ingested.layers()
        before = [self.snapshot(layer) for layer in inputs]
        fixture_before = {p.name: p.read_bytes() for p in SAMPLE.iterdir() if p.is_file()}
        compared = compare_ingested(self.ingested, project=self.project)
        summary = summarize_comparison(self.ingested, compared, options=options, project=self.project)
        events = detect_comparison_events(self.ingested, compared, options=event_options, project=self.project)
        result = analyze_single_transect_survey(
            'SURVEY-SYN01', self.ingested, statistics_options=options,
            event_options=event_options, project=self.project)
        self.assertEqual(result.survey_id, 'SURVEY-SYN01')
        self.assertIs(result.accepted_recording, self.ingested)
        self.assertEqual(result.comparison.rows, compared.rows)
        self.assertEqual(result.summary.summary, summary.summary)
        self.assertEqual(result.events.detection, events.detection)
        direct_layers = compared.layers() + [summary.table, events.layer, events.validation]
        adapted_layers = result.comparison.layers() + [result.summary.table, result.events.layer, result.events.validation]
        self.assertEqual([self.snapshot(x) for x in adapted_layers],
                         [self.snapshot(x) for x in direct_layers])
        self.assertEqual(before, [self.snapshot(layer) for layer in inputs])
        self.assertEqual(fixture_before, {p.name: p.read_bytes() for p in SAMPLE.iterdir() if p.is_file()})
        self.assertEqual(len(self.project.mapLayers()), 0)
        return result

    def test_syn01_exact_equivalence_and_independent_known_answers(self):
        result = self.compare_paths()
        verify_measurements(result.comparison.rows, result.summary.summary, result.events.detection)
        self.assertEqual(result.accepted_recording.raw_observations.featureCount(), 61)
        self.assertEqual(result.accepted_recording.raw_track.featureCount(), 1)

    def test_explicit_options_are_forwarded_unchanged(self):
        from transect_navigator.qaqc.statistics import StatisticsOptions
        from transect_navigator.qaqc.events import EventOptions
        self.compare_paths(StatisticsOptions(thresholds_m=(1, 4, 8),
            coverage_max_station_gap_m=15, coverage_max_time_gap_s=12),
            EventOptions(threshold_m=10, minimum_duration_s=5,
                minimum_station_span_m=5, maximum_time_gap_s=15, maximum_spatial_gap_m=15))

    def test_invalid_survey_identity_rejected_without_mutation(self):
        from transect_navigator.integration.survey import analyze_single_transect_survey
        before = [self.snapshot(x) for x in self.ingested.layers()]
        for identity in ('', '  ', None, 123):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, 'Survey_ID'):
                analyze_single_transect_survey(identity, self.ingested, project=self.project)
        self.assertEqual(before, [self.snapshot(x) for x in self.ingested.layers()])
        self.assertFalse(self.project.mapLayers())

    def test_existing_analysis_extent_guard_is_preserved(self):
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
        from transect_navigator.integration.analyze import ingest_recording
        from transect_navigator.integration.comparison import compare_ingested
        from transect_navigator.integration.survey import analyze_single_transect_survey
        far = QgsVectorLayer('Point?crs=EPSG:4326', 'distant recording', 'memory')
        feature = QgsFeature(far.fields())
        feature.setGeometry(QgsGeometry.fromWkt('POINT (0.06 0)'))
        self.assertTrue(far.dataProvider().addFeatures([feature])[0])
        ingested = ingest_recording(self.plan, 'Transect_ID', next(self.plan.getFeatures()).id(),
                                    far, project=self.project)
        before = [self.snapshot(x) for x in ingested.layers()]
        with self.assertRaisesRegex(ValueError, '5 km radius') as direct:
            compare_ingested(ingested, project=self.project)
        with self.assertRaises(ValueError) as adapted:
            analyze_single_transect_survey('SURVEY-FAR', ingested, project=self.project)
        self.assertEqual(str(direct.exception), str(adapted.exception))
        self.assertEqual(before, [self.snapshot(x) for x in ingested.layers()])
        self.assertFalse(self.project.mapLayers())
