"""Real QGIS fixtures: geographic metres, projected feet, attributes and source safety."""
import importlib.util
import json
from math import degrees
import unittest

QGIS_AVAILABLE = importlib.util.find_spec('qgis') is not None


@unittest.skipUnless(QGIS_AVAILABLE, 'PyQGIS unavailable; geographic/adapter tests not executed')
class PrepareRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def layer(self, wkts, ids=None, crs='EPSG:4326', kind='LineString'):
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
        layer = QgsVectorLayer(f'{kind}?crs={crs}&field=tid:string', 'test', 'memory')
        self.assertTrue(layer.isValid())
        features = []
        for index, wkt in enumerate(wkts):
            feature = QgsFeature(layer.fields())
            feature.setAttributes([ids[index] if ids is not None else f'T{index+1:02d}'])
            feature.setGeometry(QgsGeometry.fromWkt(wkt))
            features.append(feature)
        self.assertTrue(layer.dataProvider().addFeatures(features)[0])
        return layer

    def equator(self, length=100):
        # Exact analytic WGS84 equatorial arc length, independent of QGIS inverse.
        return f'LINESTRING(0 0, {degrees(length / 6378137.0):.16f} 0)'

    def prepare(self, layer, **kwargs):
        from transect_navigator.integration.prepare import prepare_waypoint_layer
        return prepare_waypoint_layer(layer, 'tid', **kwargs)

    def test_geographic_100m_attributes_and_preservation(self):
        from qgis.core import NULL, QgsProject
        layer = self.layer([self.equator()], ids=['T04'])
        before = [(f.id(), f.attributes(), bytes(f.geometry().asWkb())) for f in layer.getFeatures()]
        project_layers = set(QgsProject.instance().mapLayers())
        result = self.prepare(layer)
        rows = sorted(result.getFeatures(), key=lambda f: f['Sequence'])
        self.assertEqual(len(rows), 11)
        self.assertEqual(result.crs().authid(), 'EPSG:4326')
        self.assertEqual(result.fields().names(), ['Transect_ID','Waypoint_ID','Sequence','Distance_Along_m',
                                                  'Total_Length_m','Latitude','Longitude','Bearing_Next_deg'])
        for index, feature in enumerate(rows):
            self.assertEqual(feature['Waypoint_ID'], f'T04_{index+1:03d}')
            self.assertAlmostEqual(feature['Distance_Along_m'], index*10, places=6)
            self.assertAlmostEqual(feature['Total_Length_m'], 100, places=6)
            self.assertAlmostEqual(feature['Latitude'], 0, places=9)
            self.assertAlmostEqual(feature['Longitude'], degrees(index*10/6378137), places=9)
            self.assertAlmostEqual(feature.geometry().asPoint().x(), feature['Longitude'])
            if index < 10:
                self.assertAlmostEqual(feature['Bearing_Next_deg'], 90, places=6)
        self.assertEqual(rows[-1]['Bearing_Next_deg'], NULL)
        self.assertEqual(before, [(f.id(),f.attributes(),bytes(f.geometry().asWkb())) for f in layer.getFeatures()])
        self.assertEqual(project_layers, set(QgsProject.instance().mapLayers()))
        provenance = json.loads(result.customProperty('transect_navigator/provenance'))
        self.assertEqual(provenance['ellipsoid'], 'WGS84')

    def test_exact_integer_station_values_match_declared_double_fields(self):
        """Provider float coercion must not make strict waypoint verification reject valid output."""
        from transect_navigator.integration.prepare import prepare_navigation
        layer = self.layer(['LINESTRING(0 0, 0.001 0)'])
        prepared = prepare_navigation(layer, 'tid', 10)
        rows = sorted(prepared.waypoints.getFeatures(), key=lambda feature: feature['Sequence'])
        self.assertGreater(len(rows), 2)
        self.assertEqual(rows[1]['Distance_Along_m'], 10.0)
        self.assertIsInstance(rows[1]['Distance_Along_m'], float)

    def test_geographic_remainder_reverse_and_selected(self):
        layer = self.layer([self.equator(105), self.equator(20)])
        features = list(layer.getFeatures())
        layer.selectByIds([features[0].id()])
        result = self.prepare(layer, selected_only=True, reverse=True)
        rows = sorted(result.getFeatures(), key=lambda f: f['Sequence'])
        self.assertEqual(len(rows),12)
        self.assertAlmostEqual(rows[-1]['Distance_Along_m'],105,places=6)
        self.assertAlmostEqual(rows[0]['Bearing_Next_deg'],270,places=6)
        self.assertAlmostEqual(rows[0]['Longitude'], degrees(105/6378137), places=9)
        self.assertAlmostEqual(rows[-1]['Longitude'],0,places=9)
        self.assertEqual(layer.selectedFeatureIds(), [features[0].id()])
        self.assertEqual(self.prepare(layer).featureCount(),15)

    def test_projected_feet_matches_geographic(self):
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsGeometry
        geographic = self.layer(['LINESTRING(-73.99 40.75, -73.989 40.75)'])
        transform = QgsCoordinateTransform(geographic.crs(), QgsCoordinateReferenceSystem('EPSG:2263'),
                                           QgsProject.instance().transformContext())
        geometry = QgsGeometry(next(geographic.getFeatures()).geometry())
        geometry.transform(transform)
        feet = self.layer([geometry.asWkt(16)], crs='EPSG:2263')
        geographic_output = self.prepare(geographic)
        feet_output = self.prepare(feet)
        first = sorted(geographic_output.getFeatures(), key=lambda f:f['Sequence'])
        second = sorted(feet_output.getFeatures(), key=lambda f:f['Sequence'])
        self.assertEqual(len(first), len(second))
        for a,b in zip(first,second):
            self.assertAlmostEqual(a['Longitude'],b['Longitude'],places=7)
            self.assertAlmostEqual(a['Latitude'],b['Latitude'],places=7)
            self.assertAlmostEqual(a['Total_Length_m'],b['Total_Length_m'],places=3)

    def test_empty_duplicate_multipart_and_curved_fail(self):
        cases = [self.layer(['LINESTRING EMPTY']),
                 self.layer([self.equator(),self.equator()],ids=['A','A']),
                 self.layer([self.equator()], ids=[None]),
                 self.layer(['MULTILINESTRING((0 0, .001 0),(.001 0,.002 0))'],kind='MultiLineString'),
                 self.layer(['CIRCULARSTRING(0 0,.001 .001,.002 0)'],kind='CircularString'),
                 self.layer(['LINESTRING Z(0 0 0,.001 0 1)'],kind='LineStringZ')]
        for layer in cases:
            with self.subTest(wkb=layer.wkbType()), self.assertRaises(ValueError):
                self.prepare(layer)

    def test_no_selection_and_invalid_spacing(self):
        layer = self.layer([self.equator()])
        with self.assertRaises(ValueError):
            self.prepare(layer, selected_only=True)
        with self.assertRaises(ValueError):
            self.prepare(layer, spacing_m=0)

    def test_antimeridian(self):
        from qgis.core import QgsProject
        from transect_navigator.integration.geodesy import Wgs84Geodesic
        metric = Wgs84Geodesic(QgsProject.instance().transformContext())
        point = metric.interpolate((179.999,0),(-179.999,0),100)
        self.assertTrue(-180 <= point[0] <= 180)
        self.assertAlmostEqual(metric.distance((179.999,0), point), 100, places=5)
        self.assertAlmostEqual(metric.bearing((179.999,0),(-179.999,0)),90,places=6)

    def test_dialog_generates_and_adds_layer(self):
        from qgis.core import QgsProject
        from transect_navigator.ui.main_dialog import MainDialog
        project = QgsProject.instance()
        before = set(project.mapLayers())
        source = self.layer([self.equator()])
        dialog = None
        try:
            project.addMapLayer(source)
            dialog = MainDialog()
            dialog.layer_combo.setLayer(source)
            dialog.field_combo.setField('tid')
            self.assertEqual(dialog.spacing.value(), 10)
            dialog.generate_waypoints()
            new_ids = set(project.mapLayers()) - before - {source.id()}
            self.assertEqual(len(new_ids), 1)
            self.assertEqual(project.mapLayer(next(iter(new_ids))).featureCount(), 11)
            self.assertTrue(dialog.generate.isEnabled())
        finally:
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
            for identifier in set(project.mapLayers()) - before:
                project.removeMapLayer(identifier)
