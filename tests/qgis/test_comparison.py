"""True PyQGIS comparison tests; geographic fixtures use independent WGS84 formulae."""
import importlib.util
import json
from math import degrees
import unittest

QGIS_AVAILABLE = importlib.util.find_spec('qgis') is not None
A = 6378137.0
F = 1 / 298.257223563
M0 = A * (1-F)**2  # Meridional radius of curvature at the equator.


@unittest.skipUnless(QGIS_AVAILABLE, 'PyQGIS unavailable; QA CRS/layer/UI tests not executed')
class ComparisonRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def layer(self, wkts, kind, crs='EPSG:4326', accuracy='4.5'):
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
        layer = QgsVectorLayer(f'{kind}?crs={crs}&field=tid:string&field=time:string&field=accuracy:string', 'fixture', 'memory')
        self.assertTrue(layer.isValid())
        features = []
        for index,wkt in enumerate(wkts):
            f = QgsFeature(layer.fields())
            f.setGeometry(QgsGeometry.fromWkt(wkt))
            f.setAttributes(['T04',f'2026-01-01T00:00:{index:02d}Z',accuracy])
            features.append(f)
        self.assertTrue(layer.dataProvider().addFeatures(features)[0])
        return layer

    def plan(self, reverse=False):
        pair = ['0 0',f'{degrees(100/A):.16f} 0']
        return self.layer(['LINESTRING(' + ','.join(pair[::-1] if reverse else pair) + ')'],'LineString')

    def points(self, xy, accuracy='4.5'):
        # For tiny equatorial northings, y/M0 is accurate well beyond micrometres.
        return self.layer([f'POINT({degrees(x/A):.16f} {degrees(y/M0):.16f})' for x,y in xy], 'Point', accuracy=accuracy)

    def ingest(self, source, plan=None):
        from transect_navigator.integration.analyze import ingest_recording
        plan = plan if plan is not None else self.plan()
        return ingest_recording(plan,'tid',next(plan.getFeatures()).id(),source,
                                timestamp_field='time',accuracy_field='accuracy')

    def compare(self, ingested):
        from transect_navigator.integration.comparison import compare_ingested
        return compare_ingested(ingested)

    def test_geographic_known_distances_signs_station_and_progress(self):
        result = self.compare(self.ingest(self.points([(40,0),(40,1),(40,-1),(40,5)])))
        for row, expected in zip(result.rows,[0,1,-1,5]):
            self.assertAlmostEqual(row.absolute_m,abs(expected),places=5)
            self.assertAlmostEqual(row.signed_m,expected,places=5)
            self.assertAlmostEqual(row.distance_along_m,40,places=5)
            self.assertAlmostEqual(row.progress_pct,40,places=5)
            self.assertAlmostEqual(row.total_length_m,100,places=5)
            self.assertAlmostEqual(row.nearest.y,0,places=7)
        self.assertEqual(result.observations.crs().authid(),'EPSG:4326')
        self.assertFalse(result.nearest_points.crs().isGeographic())

    def test_geographic_reversal_uses_same_origin(self):
        a = self.compare(self.ingest(self.points([(40,1)])))
        b = self.compare(self.ingest(self.points([(40,1)]), self.plan(reverse=True)))
        self.assertEqual(a.nearest_points.crs(),b.nearest_points.crs())
        self.assertAlmostEqual(b.rows[0].signed_m,-1,places=5)
        self.assertAlmostEqual(b.rows[0].distance_along_m,60,places=5)
        self.assertAlmostEqual(a.rows[0].nearest.x,b.rows[0].nearest.x,places=5)

    def test_geographic_vertex_consistent_side(self):
        plan = self.layer([f'LINESTRING(0 0,{degrees(10/A)} 0,{degrees(10/A)} {degrees(10/M0)})'],'LineString')
        result = self.compare(self.ingest(self.points([(11,-1),(10,0)]),plan))
        self.assertAlmostEqual(result.rows[0].absolute_m,2**.5,places=5)
        self.assertAlmostEqual(result.rows[0].signed_m,-2**.5,places=5)
        self.assertAlmostEqual(result.rows[0].distance_along_m,10,places=5)
        self.assertAlmostEqual(result.rows[1].absolute_m,0,places=5)

    def test_geographic_endpoints_345_distance(self):
        result = self.compare(self.ingest(self.points([(-3,4),(103,4)])))
        for row,station in zip(result.rows,[0,100]):
            self.assertAlmostEqual(row.absolute_m,5,places=5)
            self.assertAlmostEqual(row.distance_along_m,station,places=5)
            self.assertIsNone(row.signed_m)

    def test_raw_geometry_attributes_timestamp_and_accuracy_preserved(self):
        source = self.points([(40,1),(40,-1)])
        before = [(f.attributes(),bytes(f.geometry().asWkb())) for f in source.getFeatures()]
        ingested = self.ingest(source)
        raw_before = [(f.attributes(),bytes(f.geometry().asWkb())) for f in ingested.raw_observations.getFeatures()]
        result = self.compare(ingested)
        rows = list(result.observations.getFeatures())
        for index,f in enumerate(rows):
            self.assertEqual(f.attributes()[:3],before[index][0])
            self.assertEqual(bytes(f.geometry().asWkb()),before[index][1])
            self.assertEqual(f['QA_Accuracy_Raw'],'4.5')
            self.assertEqual(f['QA_Time_UTC'],f'2026-01-01T00:00:0{index}.000000Z')
        self.assertEqual(before,[(f.attributes(),bytes(f.geometry().asWkb())) for f in source.getFeatures()])
        self.assertEqual(raw_before,[(f.attributes(),bytes(f.geometry().asWkb())) for f in ingested.raw_observations.getFeatures()])

    def test_missing_gnss_accuracy_is_null_and_not_corrected(self):
        from qgis.core import NULL
        result = self.compare(self.ingest(self.points([(40,5)], accuracy=None)))
        self.assertEqual(next(result.observations.getFeatures())['QA_Accuracy_Raw'],NULL)
        self.assertAlmostEqual(result.rows[0].absolute_m,5,places=5)
        other = self.compare(self.ingest(self.points([(40,5)], accuracy='100')))
        self.assertAlmostEqual(other.rows[0].absolute_m,result.rows[0].absolute_m)

    def test_missing_timestamps_still_compares_every_point(self):
        source = self.points([(40,1),(40,1)])
        for f in source.getFeatures():
            source.dataProvider().changeAttributeValues({f.id():{1:None}})
        result = self.compare(self.ingest(source))
        self.assertEqual(len(result.rows),2)
        self.assertAlmostEqual(result.rows[0].absolute_m,1,places=5)

    def test_projected_feet_matches_geographic(self):
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject
        plan = self.layer(['LINESTRING(-73.99 40.75,-73.989 40.75)'],'LineString')
        source = self.layer(['POINT(-73.9895 40.75001)'],'Point')
        expected = self.compare(self.ingest(source,plan))
        transform = QgsCoordinateTransform(source.crs(),QgsCoordinateReferenceSystem('EPSG:2263'),QgsProject.instance().transformContext())
        geometry = QgsGeometry(next(source.getFeatures()).geometry())
        geometry.transform(transform)
        feet = self.layer([geometry.asWkt(16)],'Point','EPSG:2263')
        result = self.compare(self.ingest(feet,plan))
        self.assertEqual(result.observations.crs().authid(),'EPSG:2263')
        self.assertAlmostEqual(result.rows[0].absolute_m,expected.rows[0].absolute_m,places=3)
        self.assertAlmostEqual(result.rows[0].distance_along_m,expected.rows[0].distance_along_m,places=3)

    def test_source_zm_is_preserved_not_used_for_horizontal_distance(self):
        source = self.layer([f'POINT ZM({degrees(40/A)} {degrees(1/M0)} 123 456)'],'PointZM')
        result = self.compare(self.ingest(source))
        geometry = next(result.observations.getFeatures()).geometry()
        self.assertEqual(bytes(geometry.asWkb()),bytes(next(source.getFeatures()).geometry().asWkb()))
        self.assertAlmostEqual(result.rows[0].absolute_m,1,places=5)

    def test_connectors_match_numerical_distance(self):
        result = self.compare(self.ingest(self.points([(40,5),(20,-1)])))
        self.assertEqual(result.nearest_points.featureCount(),2)
        for feature,row in zip(result.connectors.getFeatures(),result.rows):
            self.assertAlmostEqual(feature.geometry().length(),row.absolute_m,places=10)
            end = feature.geometry().asPolyline()[-1]
            self.assertAlmostEqual(end.x(),row.nearest.x,places=10)
            self.assertAlmostEqual(end.y(),row.nearest.y,places=10)

    def test_out_of_scope_extent_and_polar_rejected(self):
        with self.assertRaisesRegex(ValueError,'5 km'):
            self.compare(self.ingest(self.points([(6000,0)])))
        plan = self.layer(['LINESTRING(0 86,.001 86)'],'LineString')
        source = self.layer(['POINT(.0005 86)'],'Point')
        with self.assertRaisesRegex(ValueError,'85 degrees'):
            self.compare(self.ingest(source,plan))

    def test_self_crossing_plan_rejected(self):
        plan = self.layer(['LINESTRING(0 0,.001 .001,0 .001,.001 0)'],'LineString')
        with self.assertRaisesRegex(ValueError,'crosses'):
            self.compare(self.ingest(self.points([(1,1)]),plan))

    def test_field_collision_keeps_original_and_records_mapping(self):
        from qgis.PyQt.QtCore import QMetaType
        from qgis.core import QgsField
        source = self.points([(40,1)])
        source.dataProvider().addAttributes([QgsField('QA_Signed_m',QMetaType.Type.QString)])
        source.updateFields()
        source.dataProvider().changeAttributeValues({next(source.getFeatures()).id():{3:'original'}})
        result = self.compare(self.ingest(source))
        row = next(result.observations.getFeatures())
        self.assertEqual(row['QA_Signed_m'],'original')
        self.assertAlmostEqual(row['QA_Signed_m_'],1,places=5)
        meta = json.loads(result.observations.customProperty('transect_navigator/provenance'))
        self.assertEqual(meta['qa_field_mapping']['QA_Signed_m'],'QA_Signed_m_')

    def test_changed_raw_geometry_rejected(self):
        from qgis.core import QgsGeometry
        ingested = self.ingest(self.points([(40,1)]))
        feature = next(ingested.raw_observations.getFeatures())
        ingested.raw_observations.dataProvider().changeGeometryValues({feature.id():QgsGeometry.fromWkt('POINT(0 0)')})
        with self.assertRaisesRegex(ValueError,'changed'):
            self.compare(ingested)

    def test_ui_comparison_publishes_complete_group(self):
        from qgis.core import QgsProject
        from transect_navigator.ui.analyze_panel import AnalyzePanel
        project = QgsProject.instance()
        before = set(project.mapLayers())
        group, panel = None, None
        try:
            plan,source = self.plan(),self.points([(40,1),(40,5)])
            project.addMapLayer(plan)
            project.addMapLayer(source)
            panel = AnalyzePanel()
            panel.planned_layer.setLayer(plan)
            panel.id_field.setField('tid')
            panel.feature.setCurrentIndex(1)
            panel.recorded_layer.setLayer(source)
            panel.timestamp.setField('time')
            panel.accuracy.setField('accuracy')
            panel.compare_data()
            group = project.layerTreeRoot().findGroup('Transect Navigator — T04')
            self.assertIsNotNone(group,panel.status.text())
            self.assertEqual(len(group.findLayers()),12)
            count = len(project.mapLayers())
            panel.feature.setCurrentIndex(0)
            panel.compare_data()
            self.assertEqual(len(project.mapLayers()),count)
            self.assertTrue(panel.compare.isEnabled())
        finally:
            if panel is not None:
                panel.deleteLater()
            project.removeMapLayers(list(set(project.mapLayers())-before))
            if group is not None:
                project.layerTreeRoot().removeChildNode(group)
