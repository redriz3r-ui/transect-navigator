"""Regression for ordinary WGS 84 being reported dynamic by QGIS/PROJ."""
import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None,
                     'PyQGIS unavailable; CRS epoch regression not executed')
class CrsEpochRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def layer(self, kind, wkt, crs='EPSG:4326'):
        from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer
        layer = QgsVectorLayer(f'{kind}?crs={crs}&field=tid:string', 'epoch fixture', 'memory')
        self.assertTrue(layer.isValid())
        feature = QgsFeature(layer.fields())
        feature.setAttributes(['T01'])
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        return layer

    def test_wgs84_ensemble_is_not_treated_as_epoch_dependent(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.crs import requires_coordinate_epoch
        self.assertFalse(requires_coordinate_epoch(QgsCoordinateReferenceSystem('EPSG:4326')))
        for code in ('EPSG:3857', 'EPSG:32631'):
            crs = QgsCoordinateReferenceSystem(code)
            self.assertTrue(crs.isValid())
            self.assertFalse(requires_coordinate_epoch(crs))

    def test_prepare_and_ingest_accept_epsg4326_without_an_epoch(self):
        from transect_navigator.integration.prepare import prepare_navigation
        from transect_navigator.integration.analyze import ingest_recording
        planned = self.layer('LineString', 'LINESTRING(0 0,0.001 0)')
        recorded = self.layer('Point', 'POINT(0.0005 0)')
        prepared = prepare_navigation(planned, 'tid', 10)
        self.assertGreater(len(prepared.rows), 2)
        result = ingest_recording(planned, 'tid', next(planned.getFeatures()).id(), recorded)
        self.assertEqual(result.raw_observations.featureCount(), 1)

    def test_real_dynamic_realization_still_requires_epoch(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.crs import requires_coordinate_epoch
        from transect_navigator.integration.prepare import prepare_navigation
        from transect_navigator.integration.analyze import ingest_recording
        dynamic = QgsCoordinateReferenceSystem('EPSG:9053')  # WGS 84 (G730)
        if not dynamic.isValid() or not dynamic.isDynamic():
            self.skipTest('EPSG:9053 dynamic realization unavailable in this QGIS/PROJ build')
        self.assertTrue(requires_coordinate_epoch(dynamic))
        planned = self.layer('LineString', 'LINESTRING(0 0,0.001 0)', 'EPSG:9053')
        recorded = self.layer('Point', 'POINT(0.0005 0)')
        with self.assertRaisesRegex(ValueError, 'Dynamic CRS'):
            prepare_navigation(planned, 'tid', 10)
        planned_static = self.layer('LineString', 'LINESTRING(0 0,0.001 0)')
        recorded.setCrs(dynamic)
        with self.assertRaisesRegex(ValueError, 'Dynamic CRS'):
            ingest_recording(planned_static, 'tid', next(planned_static.getFeatures()).id(), recorded)
