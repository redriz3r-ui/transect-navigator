"""Actual GeoPackage/Qt integration, explicitly skipped when PyQGIS is absent."""
import csv
import importlib.util
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from . import test_comparison


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable; analysis GeoPackage/UI export not executed')
class AnalysisExportRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def make(self, points=None, timestamps=True, minimum_duration=None, projected_z=False):
        from qgis.core import QgsProject, QgsGeometry, QgsCoordinateReferenceSystem, QgsCoordinateTransform
        from transect_navigator.integration.analyze import ingest_recording
        from transect_navigator.integration.comparison import compare_ingested
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.integration.events import detect_comparison_events
        from transect_navigator.integration.visualization import navigation_for_result
        from transect_navigator.integration.analysis_export import capture_analysis
        from transect_navigator.qaqc.events import EventOptions
        fixture = test_comparison.ComparisonRuntimeTests()
        plan = fixture.plan()
        source = fixture.points(points if points is not None else [(0,0),(10,1),(20,7),(30,8),(40,0)])
        project = QgsProject.instance()
        if projected_z:
            source = fixture.layer(['POINT Z (0.0001 0.00001 12)',
                                    'POINT Z (0.0002 0.00002 13)'], 'PointZ')
            target = QgsCoordinateReferenceSystem('EPSG:32631')
            for layer in (plan, source):
                transform = QgsCoordinateTransform(layer.crs(),target,project)
                geometries = {}
                for feature in layer.getFeatures():
                    geometry=QgsGeometry(feature.geometry())
                    geometry.transform(transform)
                    geometries[feature.id()]=geometry
                self.assertTrue(layer.dataProvider().changeGeometryValues(geometries))
                layer.setCrs(target)
        ingested = ingest_recording(plan,'tid',next(plan.getFeatures()).id(),source,
            timestamp_field='time' if timestamps else '',accuracy_field='accuracy')
        compared = compare_ingested(ingested,project=project)
        summary = summarize_comparison(ingested,compared,project=project)
        events = detect_comparison_events(ingested,compared,
            options=EventOptions(minimum_duration_s=minimum_duration),project=project)
        navigation = navigation_for_result(ingested.planned,'tid',10,project)
        package = capture_analysis(ingested,compared,summary,events,navigation,project=project,
                                   analysis_time='2026-01-01T12:00:00Z')
        return package, ingested, compared, summary, events, navigation

    def test_complete_geopackage_and_csv_have_known_values(self):
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.analysis_export import export_analysis_package
        package, *_ = self.make()
        with TemporaryDirectory() as folder:
            destination = export_analysis_package(package,Path(folder),'completed')
            self.assertEqual({p.name for p in destination.iterdir()}, {'analysis.gpkg','qa_summary.csv','README.txt'})
            gpkg = destination/'analysis.gpkg'
            with sqlite3.connect(gpkg) as database:
                tables = {row[0] for row in database.execute('SELECT table_name FROM gpkg_contents')}
                self.assertEqual(tables,{name for name,_ in package.layers})
                self.assertEqual(database.execute('PRAGMA quick_check').fetchone(),('ok',))
                row = database.execute('SELECT Observation_Count, Mean_Deviation_m, Median_Deviation_m, '
                    'P95_Deviation_m, Event_Count, Waypoint_Spacing_m, Event_Min_Duration_s, '
                    'Analysis_Time_UTC FROM qa_summary').fetchone()
                self.assertEqual(row[0],5)
                self.assertAlmostEqual(row[1],3.2,places=5)
                self.assertAlmostEqual(row[2],1,places=5)
                self.assertAlmostEqual(row[3],7.8,places=5)
                self.assertEqual(row[4:7],(1,10,None))
                self.assertEqual(row[7],'2026-01-01T12:00:00+00:00')
                self.assertEqual(database.execute('SELECT COUNT(*) FROM navigation_waypoints').fetchone()[0],11)
                info = database.execute("SELECT Provenance_JSON FROM analysis_metadata WHERE Layer_Name='qa_observations'").fetchone()[0]
                self.assertIn('analysis_crs_wkt',json.loads(info))
                self.assertIn('qa_field_mapping',json.loads(info))
                criteria = json.loads(database.execute("SELECT Provenance_JSON FROM analysis_metadata WHERE Layer_Name='deviation_events'").fetchone()[0])
                self.assertIsNone(criteria['event_options']['minimum_duration_s'])
            reopened = QgsVectorLayer(f'{gpkg}|layername=raw_gps_observations','raw','ogr')
            self.assertTrue(reopened.isValid())
            self.assertEqual(reopened.crs().authid(),'EPSG:4326')
            self.assertEqual(next(reopened.getFeatures())['accuracy'],'4.5')
            reopened = None
            with (destination/'qa_summary.csv').open(newline='',encoding='utf-8') as stream:
                values = next(csv.DictReader(stream))
            self.assertEqual(values['Event_Count'],'1')
            self.assertAlmostEqual(float(values['Mean_Deviation_m']),3.2,places=5)
            self.assertEqual(values['Analysis_Time_UTC'],'2026-01-01T12:00:00+00:00')

    def test_snapshot_survives_later_layer_edits(self):
        from transect_navigator.integration.analysis_export import export_analysis_package
        package, ingested, compared, *_ = self.make()
        feature = next(compared.observations.getFeatures())
        mapping = json.loads(compared.observations.customProperty('transect_navigator/provenance'))['qa_field_mapping']
        column = compared.observations.fields().indexFromName(mapping['QA_Deviation_m'])
        compared.observations.dataProvider().changeAttributeValues({feature.id():{column:999}})
        ingested.raw_observations.dataProvider().deleteFeatures([next(ingested.raw_observations.getFeatures()).id()])
        with TemporaryDirectory() as folder:
            destination = export_analysis_package(package,Path(folder),'snapshot')
            with sqlite3.connect(destination/'analysis.gpkg') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM raw_gps_observations').fetchone()[0],5)
                self.assertLess(db.execute('SELECT MAX(QA_Deviation_m) FROM qa_observations').fetchone()[0],9)

    def test_projected_z_raw_observations_round_trip_without_reprojection(self):
        from qgis.core import QgsVectorLayer, QgsWkbTypes
        from transect_navigator.integration.analysis_export import export_analysis_package
        package, ingested, *_ = self.make(projected_z=True)
        before=[bytes(f.geometry().asWkb()) for f in ingested.raw_observations.getFeatures()]
        with TemporaryDirectory() as folder:
            destination=export_analysis_package(package,Path(folder),'projected_z')
            layer=QgsVectorLayer(f'{destination}/analysis.gpkg|layername=raw_gps_observations','raw','ogr')
            self.assertEqual(layer.crs().authid(),'EPSG:32631')
            self.assertTrue(QgsWkbTypes.hasZ(layer.wkbType()))
            self.assertEqual([bytes(f.geometry().asWkb()) for f in layer.getFeatures()],before)
            layer=None

    def test_unknown_track_and_empty_events_are_explicit(self):
        from transect_navigator.integration.analysis_export import export_analysis_package
        package, *_ = self.make([(10,1)],timestamps=False)
        with TemporaryDirectory() as folder:
            destination = export_analysis_package(package,Path(folder),'no_chronology')
            with sqlite3.connect(destination/'analysis.gpkg') as db:
                tables={r[0] for r in db.execute('SELECT table_name FROM gpkg_contents')}
                self.assertNotIn('raw_track',tables)
                self.assertEqual(db.execute("SELECT Included FROM analysis_metadata WHERE Layer_Name='raw_track'").fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM deviation_events').fetchone()[0],0)
                self.assertIsNone(db.execute('SELECT Recorded_Length_m FROM qa_summary').fetchone()[0])

    def test_stationary_event_null_geometry_and_attributes_survive(self):
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.analysis_export import export_analysis_package
        package, *_ = self.make([(10,7),(10,7)])
        with TemporaryDirectory() as folder:
            destination=export_analysis_package(package,Path(folder),'stationary')
            layer=QgsVectorLayer(f'{destination}/analysis.gpkg|layername=deviation_events','events','ogr')
            self.assertEqual(layer.featureCount(),1)
            event=next(layer.getFeatures())
            self.assertTrue(event.geometry().isNull())
            self.assertEqual(event['Observation_Count'],2)
            self.assertIn('stationary',event['Geometry_Status'])
            with sqlite3.connect(destination/'analysis.gpkg') as database:
                self.assertEqual(database.execute(
                    'SELECT COUNT(*) FROM deviation_events WHERE _tn_geometry IS NULL').fetchone()[0], 1)
            layer=None

    def test_stationary_null_event_does_not_erase_nonstationary_event(self):
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.analysis_export import export_analysis_package
        package, *_ = self.make([(0,0),(10,7),(10,7),(20,0),(30,7),(40,7)])
        with TemporaryDirectory() as folder:
            destination = export_analysis_package(package, Path(folder), 'mixed_events')
            layer = QgsVectorLayer(f'{destination}/analysis.gpkg|layername=deviation_events', 'events', 'ogr')
            self.assertEqual(layer.featureCount(), 2)
            events = {feature['Event_ID']: feature for feature in layer.getFeatures()}
            self.assertTrue(events['T04_E001'].geometry().isNull())
            self.assertFalse(events['T04_E002'].geometry().isNull())
            self.assertEqual(len(list(events['T04_E002'].geometry().vertices())), 2)
            with sqlite3.connect(destination/'analysis.gpkg') as database:
                self.assertEqual(database.execute(
                    'SELECT COUNT(*) FROM deviation_events WHERE _tn_geometry IS NULL').fetchone()[0], 1)

    def test_no_overwrite_and_writer_failure_cleanup(self):
        from transect_navigator.integration.analysis_export import export_analysis_package
        package, *_ = self.make()
        with TemporaryDirectory() as folder:
            parent=Path(folder)
            original=export_analysis_package(package,parent,'existing')
            contents=(original/'analysis.gpkg').read_bytes()
            with self.assertRaises(ValueError):
                export_analysis_package(package,parent,'existing')
            self.assertEqual((original/'analysis.gpkg').read_bytes(),contents)
            with patch('transect_navigator.integration.analysis_export.write_geopackage_layers', side_effect=ValueError('injected failure')):
                with self.assertRaisesRegex(ValueError,'injected'):
                    export_analysis_package(package,parent,'failed')
            self.assertEqual({p.name for p in parent.iterdir()},{'existing'})

    def test_corrupt_geopackage_fails_verification(self):
        from transect_navigator.integration.export import write_geopackage_layers, _verify_layer
        package, *_ = self.make()
        layer=dict(package.layers)['qa_summary']
        with TemporaryDirectory() as folder:
            path=Path(folder)/'summary.gpkg'
            write_geopackage_layers(path,[('qa_summary',layer)],package.context)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE qa_summary SET Mean_Deviation_m=999')
            with self.assertRaisesRegex(ValueError,'changed'):
                _verify_layer(path,'qa_summary',layer)

    def test_mixed_summary_run_is_rejected(self):
        from qgis.core import QgsProject
        from transect_navigator.integration.analysis_export import capture_analysis
        _, ingested, compared, _, events, navigation = self.make()
        other = self.make()[3]
        with self.assertRaisesRegex(ValueError,'same completed'):
            capture_analysis(ingested,compared,other,events,navigation,project=QgsProject.instance())

    def test_capture_does_not_apply_raw_input_cap_to_derived_tables(self):
        from qgis.core import QgsProject
        from transect_navigator.integration.analysis_export import capture_analysis
        _, ingested, compared, summary, events, navigation = self.make()
        with patch('transect_navigator.integration.recorded.MAX_OBSERVATIONS',1):
            package=capture_analysis(ingested,compared,summary,events,navigation,project=QgsProject.instance())
        self.assertEqual(dict(package.layers)['raw_gps_observations'].featureCount(),5)

    def test_mixed_events_or_raw_snapshot_are_rejected(self):
        from qgis.core import QgsProject
        from transect_navigator.integration.analysis_export import capture_analysis
        _, ingested, compared, summary, events, navigation = self.make()
        _, other_ingested, _, _, other_events, _ = self.make()
        for raw, detected in ((other_ingested,events),(ingested,other_events)):
            with self.assertRaisesRegex(ValueError,'same completed'):
                capture_analysis(raw,compared,summary,detected,navigation,project=QgsProject.instance())

    def test_retained_gpx_document_text_survives_geopackage(self):
        from qgis.core import QgsProject
        from transect_navigator.integration.analysis_export import capture_analysis, export_analysis_package
        _, ingested, compared, summary, events, navigation = self.make()
        document='<gpx version="1.1"><metadata><name>Raw &amp; unmodified</name></metadata></gpx>'
        ingested.raw_observations.setCustomProperty('transect_navigator/raw_gpx_document',document)
        package=capture_analysis(ingested,compared,summary,events,navigation,project=QgsProject.instance())
        with TemporaryDirectory() as folder:
            destination=export_analysis_package(package,Path(folder),'gpx_metadata')
            with sqlite3.connect(destination/'analysis.gpkg') as db:
                metadata=db.execute("SELECT Provenance_JSON FROM analysis_metadata WHERE Layer_Name='raw_gps_observations'").fetchone()[0]
            self.assertEqual(json.loads(metadata)['raw_gpx_document'],document)

    def test_ui_action_exports_snapshot_and_reports_overwrite(self):
        from transect_navigator.ui.analysis_export_panel import AnalysisExportPanel
        panel=AnalysisExportPanel()
        try:
            self.assertFalse(panel.export.isEnabled())
            package, *_ = self.make()
            panel.set_analysis(package)
            self.assertTrue(panel.export.isEnabled())
            with TemporaryDirectory() as folder:
                panel.folder.setText(folder)
                panel.name.setText('ui_results')
                panel.export_results()
                self.assertTrue((Path(folder)/'ui_results/analysis.gpkg').is_file(),panel.status.text())
                self.assertIn('verified',panel.status.text())
                panel.export_results()
                self.assertIn('already exists',panel.status.text())
                self.assertTrue(panel.export.isEnabled())
        finally:
            panel.deleteLater()
