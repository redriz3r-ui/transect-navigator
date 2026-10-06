"""Real PyQGIS-only adapter tests. No fake CRS or provider substitutes."""
import importlib.util
import json
from math import degrees
from pathlib import Path
import tempfile
import unittest

QGIS_AVAILABLE = importlib.util.find_spec('qgis') is not None
FIXTURE = Path(__file__).parents[1] / 'fixtures/recorded_synthetic.gpx'


@unittest.skipUnless(QGIS_AVAILABLE, 'PyQGIS unavailable; ingestion runtime not executed')
class IngestionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def layer(self, wkts, rows=None, kind='Point', crs='EPSG:4326'):
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
        result = QgsVectorLayer(f'{kind}?crs={crs}&field=tid:string&field=time:string&field=accuracy:string&field=hdop:double&field=group:string', 'input', 'memory')
        self.assertTrue(result.isValid())
        features = []
        for index, wkt in enumerate(wkts):
            feature = QgsFeature(result.fields())
            feature.setAttributes(rows[index] if rows is not None else ['T04', f'2026-01-01T00:00:{index:02d}Z', '4.50', .8, 'one'])
            if wkt is not None:
                feature.setGeometry(QgsGeometry.fromWkt(wkt))
            features.append(feature)
        self.assertTrue(result.dataProvider().addFeatures(features)[0])
        return result

    def planned(self):
        return self.layer([f'LINESTRING(0 0,{degrees(100/6378137):.16f} 0)'], kind='LineString')

    def ingest(self, source, plan=None, **kwargs):
        from transect_navigator.integration.analyze import ingest_recording
        plan = plan if plan is not None else self.planned()
        fid = next(plan.getFeatures()).id()
        return ingest_recording(plan, 'tid', fid, source, **kwargs)

    def records(self, layer):
        return [(f.id(), f.attributes(), bytes(f.geometry().asWkb())) for f in layer.getFeatures()]

    def codes(self, result):
        return {issue.code for issue in result.issues}

    def test_order_raw_fields_and_source_unchanged(self):
        from qgis.core import QgsProject
        source = self.layer(['POINT(.0002 0)', 'POINT(0 0)', 'POINT(.0001 0)'], [
            ['T04','2026-01-01T00:00:02Z','9.5',1.2,'one'],
            ['T04','2026-01-01T00:00:00Z',None,.8,'one'],
            ['T04','2026-01-01T00:00:01Z','4.5',.9,'one']])
        plan = self.planned()
        source.selectByIds([next(source.getFeatures()).id()])
        before, plan_before = self.records(source), self.records(plan)
        selected = source.selectedFeatureIds()
        project_before = set(QgsProject.instance().mapLayers())
        result = self.ingest(source, plan, timestamp_field='time', accuracy_field='accuracy')
        self.assertEqual(result.raw_observations.featureCount(), 3)
        self.assertEqual([f.attributes()[:5] for f in result.raw_observations.getFeatures()], [r[1] for r in before])
        self.assertEqual([bytes(f.geometry().asWkb()) for f in result.raw_observations.getFeatures()], [r[2] for r in before])
        line = next(result.raw_track.getFeatures()).geometry().asPolyline()
        self.assertEqual([p.x() for p in line], [0, .0001, .0002])
        self.assertEqual([f['Sequence'] for f in result.normalized.getFeatures()], [1,2,3])
        self.assertEqual(self.records(source), before)
        self.assertEqual(self.records(plan), plan_before)
        self.assertEqual(source.selectedFeatureIds(), selected)
        self.assertEqual(set(QgsProject.instance().mapLayers()), project_before)
        self.assertEqual(result.raw_observations.customProperty('transect_navigator/data_role'), 'raw_gps_observations')
        provenance = json.loads(result.normalized.customProperty('transect_navigator/provenance'))
        self.assertEqual(provenance['source_layer_id'], source.id())
        self.assertFalse(provenance['qa_metrics_computed'])

    def test_missing_and_duplicate_times_do_not_construct_track(self):
        for times in [(None, '2026-01-01T00:00:01Z'), ('2026-01-01T00:00:00Z',)*2]:
            source = self.layer(['POINT(0 0)', 'POINT(.001 0)'], [
                ['T04', time, None, None, 'one'] for time in times])
            result = self.ingest(source, timestamp_field='time')
            self.assertIsNone(result.raw_track)
            self.assertEqual(result.raw_observations.featureCount(), 2)
            self.assertTrue(all(r.sequence is None for r in result.ordering.observations))

    def test_no_mapping_retains_original_timestamp_fields(self):
        result = self.ingest(self.layer(['POINT(0 0)', 'POINT(.001 0)']))
        self.assertIsNone(result.raw_track)
        self.assertIn('time', result.raw_observations.fields().names())
        self.assertIn('MISSING_TIMESTAMPS', self.codes(result))

    def test_single_observation(self):
        result = self.ingest(self.layer(['POINT(0 0)']), timestamp_field='time')
        self.assertIn('SINGLE_OBSERVATION', self.codes(result))
        self.assertIsNone(result.raw_track)

    def test_missing_geometry_empty_layer_and_impossible_coordinates(self):
        for source in [self.layer([]), self.layer([None]), self.layer(['POINT EMPTY']), self.layer(['POINT(181 0)'])]:
            with self.subTest(count=source.featureCount()), self.assertRaises(ValueError):
                self.ingest(source)

    def test_invalid_fields_plan_and_spacing_screen(self):
        from transect_navigator.integration.analyze import ingest_recording
        source, plan = self.layer(['POINT(0 0)']), self.planned()
        for kwargs in [{'timestamp_field':'missing'}, {'accuracy_field':'missing'}, {'far_threshold_m':0}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.ingest(source, **kwargs)
        for planned in [None, self.layer(['LINESTRING(0 0,0 0)'], kind='LineString'),
                        self.layer(['MULTILINESTRING((0 0,1 1),(1 1,2 2))'], kind='MultiLineString')]:
            with self.assertRaises(ValueError):
                ingest_recording(planned, 'tid', next(planned.getFeatures()).id() if planned is not None else None, source)
        with self.assertRaises(ValueError):
            ingest_recording(plan, 'missing', next(plan.getFeatures()).id(), source)

    def test_crs_mismatch_projected_metres_numeric(self):
        source = self.layer(['POINT(0 0)', 'POINT(100 0)'], crs='EPSG:3857')
        result = self.ingest(source, timestamp_field='time')
        self.assertEqual(result.raw_observations.crs().authid(), 'EPSG:3857')
        self.assertIn('CRS_MISMATCH', self.codes(result))
        normalized = list(result.normalized.getFeatures())
        # Web Mercator equatorial x = R * longitude (radians), independent expected coordinate.
        self.assertAlmostEqual(normalized[1].geometry().asPoint().x(), degrees(100/6378137), places=10)
        self.assertAlmostEqual(normalized[1].geometry().asPoint().y(), 0, places=10)
        self.assertEqual(next(result.raw_track.getFeatures()).geometry().asPolyline()[1].x(), 100)

    def test_crs_mismatch_projected_feet(self):
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject
        source = self.layer(['POINT(-73.99 40.75)', 'POINT(-73.989 40.75)'])
        target = QgsCoordinateReferenceSystem('EPSG:2263')
        transform = QgsCoordinateTransform(source.crs(), target, QgsProject.instance().transformContext())
        wkts = []
        for f in source.getFeatures():
            geometry = QgsGeometry(f.geometry())
            geometry.transform(transform)
            wkts.append(geometry.asWkt(16))
        result = self.ingest(self.layer(wkts, crs='EPSG:2263'), timestamp_field='time')
        self.assertEqual(result.raw_observations.crs(), target)
        for original, normalized in zip(source.getFeatures(), result.normalized.getFeatures()):
            self.assertAlmostEqual(original.geometry().asPoint().x(), normalized.geometry().asPoint().x(), places=7)
            self.assertAlmostEqual(original.geometry().asPoint().y(), normalized.geometry().asPoint().y(), places=7)

    def test_unknown_crs_rejected(self):
        from qgis.core import QgsCoordinateReferenceSystem
        source = self.layer(['POINT(0 0)'])
        source.setCrs(QgsCoordinateReferenceSystem())
        with self.assertRaisesRegex(ValueError, 'CRS'):
            self.ingest(source)

    def test_far_screen_geodesic_boundary(self):
        # Planned length 100 m, warning 10,000 m: lower bound >10,000 beyond 10,100 from anchor.
        source = self.layer([f'POINT({degrees(x/6378137):.16f} 0)' for x in [100,10101]])
        result = self.ingest(source, timestamp_field='time')
        self.assertIn('OBSERVATIONS_VERY_FAR', self.codes(result))
        rows = list(result.normalized.getFeatures())
        self.assertEqual(rows[1]['Position_Warning'], 'definitely_far')
        self.assertNotEqual(rows[0]['Position_Warning'], 'definitely_far')

    def test_multipart_zm_track_preservation(self):
        source = self.layer(['MULTILINESTRING ZM((0 0 3 7,.001 0 4 8),(.002 0 5 9,.003 0 6 10))'], kind='MultiLineStringZM')
        before = self.records(source)
        result = self.ingest(source, timestamp_field='time', accuracy_field='accuracy')
        self.assertTrue(all(r.raw.accuracy is None for r in result.ordering.observations))
        self.assertIn('FEATURE_ACCURACY_NOT_VERTEX_ACCURACY', self.codes(result))
        self.assertEqual(bytes(next(result.raw_track.getFeatures()).geometry().asWkb()), before[0][2])
        points = [next(f.geometry().vertices()) for f in result.raw_observations.getFeatures()]
        self.assertEqual([(p.z(), p.m()) for p in points], [(3,7),(4,8),(5,9),(6,10)])
        self.assertEqual(len({r.raw.group for r in result.ordering.observations}), 2)
        self.assertTrue(all(r.sequence is None for r in result.ordering.observations))
        self.assertIn('FEATURE_TIME_NOT_VERTEX_TIME', self.codes(result))

    def test_point_zm_constructed_track(self):
        source = self.layer(['POINT ZM(0 0 3 7)', 'POINT ZM(.001 0 4 8)'], kind='PointZM')
        result = self.ingest(source, timestamp_field='time')
        points = list(next(result.raw_track.getFeatures()).geometry().vertices())
        self.assertEqual([(p.z(), p.m()) for p in points], [(3,7),(4,8)])

    def test_qgis_4_point_copy_runtime_regression(self):
        """QgsPoint(point) rejects a QgsPoint from vertices() in QGIS 4.2.3."""
        point_source = self.layer(['POINT(0 0)', 'POINT(.001 0)'])
        point_result = self.ingest(point_source, timestamp_field='time')
        self.assertEqual(point_result.raw_observations.featureCount(), 2)
        self.assertEqual(len(list(next(point_result.raw_track.getFeatures()).geometry().vertices())), 2)
        track_source = self.layer(['LINESTRING ZM(0 0 3 7,.001 0 4 8)'], kind='LineStringZM')
        track_result = self.ingest(track_source)
        vertices = [next(f.geometry().vertices()) for f in track_result.raw_observations.getFeatures()]
        self.assertEqual([(p.z(), p.m()) for p in vertices], [(3,7),(4,8)])

    def test_gpx_segments_time_conflict_and_full_document(self):
        from transect_navigator.integration.recorded import load_recorded_file
        source = load_recorded_file(FIXTURE)
        result = self.ingest(source, timestamp_field='time')
        self.assertEqual(result.raw_observations.featureCount(), 4)
        self.assertEqual(result.raw_track.featureCount(), 1)
        self.assertIn('TIME_ORDER_CONFLICT', self.codes(result))
        self.assertIn('EMPTY_TRACK', self.codes(result))
        self.assertEqual(result.raw_observations.customProperty('transect_navigator/raw_gpx_document'), FIXTURE.read_text())
        self.assertEqual([p.x() for p in next(result.raw_track.getFeatures()).geometry().asPolyline()], [-120, -120.0002, -120.0001])

    def test_explicit_groups_do_not_connect(self):
        source = self.layer(['POINT(0 0)', 'POINT(.001 0)', 'POINT(.002 0)'], [
            ['T04','2026-01-01T00:00:00Z',None,None,'a'],
            ['T04','2026-01-01T00:00:01Z',None,None,'a'],
            ['T04','2026-01-01T00:00:02Z',None,None,'b']])
        result = self.ingest(source, timestamp_field='time', group_field='group')
        self.assertEqual(result.raw_track.featureCount(), 1)
        self.assertEqual(len(next(result.raw_track.getFeatures()).geometry().asPolyline()), 2)

    def test_field_name_collision_preserves_original(self):
        from qgis.PyQt.QtCore import QMetaType
        from qgis.core import QgsField
        from transect_navigator.integration.recorded import recording_from_layer
        source = self.layer(['POINT(0 0)'])
        source.dataProvider().addAttributes([QgsField('TN_Source_ID', QMetaType.Type.QString)])
        source.updateFields()
        fid = next(source.getFeatures()).id()
        source.dataProvider().changeAttributeValues({fid:{5:'keep-me'}})
        recorded = recording_from_layer(source)
        self.assertEqual(recorded.source_id_field, 'TN_Source_ID_')
        self.assertEqual(next(recorded.points.getFeatures())['TN_Source_ID'], 'keep-me')

    def test_csv_requires_explicit_crs_and_preserves_strings(self):
        from transect_navigator.integration.recorded import load_recorded_file
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.csv'
            path.write_text('x,y,time,accuracy\n0,0,2026-01-01T00:00:00Z,04.50\n100,0,2026-01-01T00:00:01Z,\n')
            with self.assertRaises(ValueError):
                load_recorded_file(path, x_field='x', y_field='y')
            source = load_recorded_file(path, x_field='x', y_field='y', csv_crs='EPSG:3857')
            result = self.ingest(source, timestamp_field='time', accuracy_field='accuracy')
            self.assertEqual(next(result.raw_observations.getFeatures())['accuracy'], '04.50')
            self.assertEqual(result.raw_observations.crs().authid(), 'EPSG:3857')

    def test_geopackage_geojson_reopen_and_detached_snapshot(self):
        from qgis.core import QgsProject, QgsVectorFileWriter, QgsVectorLayer
        from transect_navigator.integration.recorded import load_recorded_file, file_sublayers
        source = self.layer(['POINT(0 0)', 'POINT(.001 0)'])
        with tempfile.TemporaryDirectory() as folder:
            for driver, suffix in [('GPKG','gpkg'), ('GeoJSON','geojson')]:
                path = Path(folder) / f'observations.{suffix}'
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = driver
                options.layerName = 'gps_observations'
                options.fileEncoding = 'UTF-8'
                status = QgsVectorFileWriter.writeAsVectorFormatV3(source, str(path), QgsProject.instance().transformContext(), options)
                self.assertEqual(status[0], QgsVectorFileWriter.WriterError.NoError, str(status))
                before = path.read_bytes()
                choices = file_sublayers(path)
                self.assertEqual(len(choices), 1)
                reopened = QgsVectorLayer(choices[0][1], 'reference source', 'ogr')
                self.assertTrue(reopened.isValid())
                reference = list(reopened.getFeatures())
                recorded = load_recorded_file(path, uri=choices[0][1])
                result = self.ingest(recorded, timestamp_field='time')
                self.assertEqual(result.raw_observations.featureCount(), 2)
                for original, raw in zip(reference, result.raw_observations.getFeatures()):
                    self.assertEqual(bytes(original.geometry().asWkb()), bytes(raw.geometry().asWkb()))
                    for field in ('tid','time','accuracy','hdop','group'):
                        self.assertEqual(original[field], raw[field])
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(recorded.points.providerType(), 'memory')

    def test_analyze_panel_adds_group_and_invalid_input_is_atomic(self):
        from qgis.core import QgsProject
        from transect_navigator.ui.main_dialog import MainDialog
        project = QgsProject.instance()
        before = set(project.mapLayers())
        group = None
        dialog = None
        try:
            plan = self.planned()
            source = self.layer(['POINT(0 0)', 'POINT(.001 0)'])
            project.addMapLayer(plan)
            project.addMapLayer(source)
            dialog = MainDialog()
            panel = dialog.analyze_panel
            panel.planned_layer.setLayer(plan)
            panel.id_field.setField('tid')
            panel.feature.setCurrentIndex(1)
            panel.recorded_layer.setLayer(source)
            panel.timestamp.setField('time')
            panel.ingest_data()
            new_ids = set(project.mapLayers()) - before - {plan.id(),source.id()}
            self.assertEqual(len(new_ids), 5)
            group = project.layerTreeRoot().findGroup('Transect Navigator — ANALYZE inputs')
            self.assertIsNotNone(group)
            self.assertEqual(len(group.findLayers()), 5)
            panel.feature.setCurrentIndex(0)
            count = len(project.mapLayers())
            panel.ingest_data()
            self.assertEqual(len(project.mapLayers()), count)
            self.assertIn('Cannot ingest', panel.status.text())
            self.assertTrue(panel.ingest.isEnabled())
        finally:
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
            project.removeMapLayers(list(set(project.mapLayers()) - before))
            if group is not None:
                project.layerTreeRoot().removeChildNode(group)

    def test_file_path_change_clears_previous_options(self):
        from transect_navigator.ui.analyze_panel import AnalyzePanel
        panel = AnalyzePanel()
        try:
            panel.x_field.addItem('old longitude','lon')
            panel.sublayer.addItem('old layer','old uri')
            panel.path.setText('/new/input.csv')
            self.assertEqual(panel.x_field.count(), 0)
            self.assertEqual(panel.sublayer.count(), 0)
            self.assertIsNone(panel.loaded_input)
        finally:
            panel.deleteLater()
