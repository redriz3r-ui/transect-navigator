"""Real QGIS export, round-trip and UI validation tests; never fake the runtime."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

QGIS_AVAILABLE = importlib.util.find_spec('qgis') is not None


@unittest.skipUnless(QGIS_AVAILABLE, 'PyQGIS unavailable; GeoPackage and UI export tests not executed')
class ExportRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def source(self, kind='LineString', populate=True):
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
        source=QgsVectorLayer(f'{kind}?crs=EPSG:32611&field=tid:string&field=fid:string&field=note:string', 'source','memory')
        self.assertTrue(source.isValid())
        if populate:
            feature=QgsFeature(source.fields())
            feature.setAttributes(['T04','source-fid','Original & unchanged'])
            feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(500000 4000000,500100 4000000)'
                                                    if kind=='LineString' else 'POINT(500000 4000000)'))
            self.assertTrue(source.dataProvider().addFeatures([feature])[0])
        return source

    def test_complete_package_reopens_and_preserves_native_source(self):
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.prepare import prepare_navigation
        from transect_navigator.integration.export import export_navigation_package
        source=self.source()
        original=next(source.getFeatures())
        prepared=prepare_navigation(source,'tid',10)
        with tempfile.TemporaryDirectory() as folder:
            result=export_navigation_package(prepared,Path(folder),'survey')
            self.assertEqual({p.name for p in result.iterdir()}, {'navigation.gpkg','navigation.gpx',
                'navigation_waypoints.csv','planned_transects.csv','navigation_waypoints.geojson',
                'planned_transects.geojson','README.txt'})
            native=QgsVectorLayer(f'{result}/navigation.gpkg|layername=source_transects','native','ogr')
            self.assertTrue(native.isValid())
            copy=next(native.getFeatures())
            self.assertEqual(native.crs(),source.crs())
            self.assertEqual(bytes(copy.geometry().asWkb()),bytes(original.geometry().asWkb()))
            for name in source.fields().names():
                self.assertEqual(copy[name],original[name])
            points=QgsVectorLayer(f'{result}/navigation.gpkg|layername=navigation_waypoints','points','ogr')
            self.assertEqual(points.featureCount(),len(prepared.rows))
            self.assertEqual(points.crs().authid(),'EPSG:4326')
            with self.assertRaises(ValueError):
                export_navigation_package(prepared,Path(folder),'survey')
            # Close file-backed providers before TemporaryDirectory cleanup on Windows.
            native=None
            points=None

    def test_failure_cleanup_and_snapshot_isolation(self):
        from transect_navigator.integration.prepare import prepare_navigation
        from transect_navigator.integration.export import export_navigation_package
        source=self.source()
        prepared=prepare_navigation(source,'tid',10)
        feature=next(source.getFeatures())
        source.dataProvider().changeAttributeValues({feature.id():{0:'CHANGED'}})
        self.assertEqual(next(prepared.source.getFeatures())['tid'],'T04')
        with tempfile.TemporaryDirectory() as folder:
            with patch('transect_navigator.integration.export._write_geopackage',side_effect=ValueError('writer failed')):
                with self.assertRaisesRegex(ValueError,'writer failed'):
                    export_navigation_package(prepared,Path(folder),'survey')
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_validation_errors_for_required_input_cases(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.prepare import prepare_navigation
        source=self.source()
        invalid_crs=self.source()
        invalid_crs.setCrs(QgsCoordinateReferenceSystem())
        cases=[(None,'tid',10),(self.source('Point'),'tid',10),
               (source,'missing',10),(source,'tid',0),(self.source(populate=False),'tid',10),
               (invalid_crs,'tid',10)]
        for layer,field,spacing in cases:
            with self.subTest(field=field,spacing=spacing), self.assertRaises(ValueError):
                prepare_navigation(layer,field,spacing)

    def test_ui_invalid_input_is_inline_and_buttons_recover(self):
        from qgis.core import QgsProject
        from transect_navigator.ui.main_dialog import MainDialog
        before=set(QgsProject.instance().mapLayers())
        dialog=MainDialog()
        try:
            dialog.layer_combo.setLayer(None)
            dialog.generate_waypoints()
            self.assertIn('Cannot generate',dialog.status.text())
            self.assertTrue(dialog.generate.isEnabled())
            self.assertTrue(dialog.export.isEnabled())
            self.assertEqual(before,set(QgsProject.instance().mapLayers()))
            dialog.destination.clear()
            dialog.export_package()
            self.assertIn('Choose an output folder',dialog.status.text())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_ui_export_uses_current_settings_without_prior_generation(self):
        from qgis.core import QgsProject
        from transect_navigator.ui.main_dialog import MainDialog
        project=QgsProject.instance()
        source=self.source()
        before=set(project.mapLayers())
        dialog=None
        try:
            project.addMapLayer(source)
            dialog=MainDialog()
            dialog.layer_combo.setLayer(source)
            dialog.field_combo.setField('tid')
            dialog.spacing.setValue(20)
            with tempfile.TemporaryDirectory() as folder:
                dialog.destination.setText(folder)
                dialog.package_name.setText('twenty_metre_run')
                dialog.export_package()
                self.assertIn('Exported',dialog.status.text())
                path=Path(folder)/'twenty_metre_run'/'navigation_waypoints.geojson'
                points=json.loads(path.read_text())['features']
                self.assertEqual(points[1]['properties']['Distance_Along_m'],20)
        finally:
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
            for identifier in set(project.mapLayers())-before:
                project.removeMapLayer(identifier)
