"""Audit regressions requiring real QGIS/Qt, never emulated as passing without them."""
import importlib.util
import unittest
from unittest.mock import patch
from . import test_comparison


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable; audit integration/lifecycle checks not executed')
class AuditRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app=start_app()

    def test_prepare_rejects_impossible_native_geographic_coordinates(self):
        from transect_navigator.integration.prepare import prepare_navigation
        fixture=test_comparison.ComparisonRuntimeTests()
        for crs in ('EPSG:4326','EPSG:4269'):
            plan=fixture.layer(['LINESTRING(181 40,181.001 40)'],'LineString',crs)
            with self.assertRaisesRegex(ValueError,'Impossible'):
                prepare_navigation(plan,'tid')

    def test_prepare_rejects_geographic_grads(self):
        from transect_navigator.integration.prepare import prepare_navigation
        fixture=test_comparison.ComparisonRuntimeTests()
        plan=fixture.layer(['LINESTRING(0 50,0.001 50)'],'LineString','EPSG:4807')
        with self.assertRaisesRegex(ValueError,'degrees'):
            prepare_navigation(plan,'tid')

    def test_analysis_waypoints_and_summary_use_same_trimmed_id(self):
        from qgis.core import QgsProject
        from transect_navigator.integration.statistics import summarize_comparison
        from transect_navigator.integration.visualization import navigation_for_result
        fixture=test_comparison.ComparisonRuntimeTests()
        plan=fixture.plan()
        feature=next(plan.getFeatures())
        plan.dataProvider().changeAttributeValues({feature.id():{0:'  T04 \t'}})
        ingested=fixture.ingest(fixture.points([(10,1),(20,-1)]),plan)
        compared=fixture.compare(ingested)
        summary=summarize_comparison(ingested,compared)
        nav=navigation_for_result(ingested.planned,'tid',10,QgsProject.instance())
        self.assertEqual(summary.summary.transect_id,'T04')
        self.assertEqual({f['Transect_ID'] for f in nav.getFeatures()},{'T04'})
        self.assertEqual(next(ingested.planned.getFeatures())['tid'],'  T04 \t')
        self.assertEqual(next(plan.getFeatures())['tid'],'  T04 \t')

    def test_analysis_rejects_nonfinite_numeric_transect_id(self):
        from qgis.core import QgsVectorLayer,QgsFeature,QgsGeometry
        fixture=test_comparison.ComparisonRuntimeTests()
        layer=QgsVectorLayer('LineString?crs=EPSG:4326&field=tid:double','invalid ID','memory')
        feature=QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(0 0,0.0001 0)'))
        feature.setAttributes([float('inf')])
        self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        with self.assertRaisesRegex(ValueError,'nonfinite'):
            fixture.ingest(fixture.points([(10,1)]),layer)

    def test_ingestion_registration_failure_rolls_back(self):
        from qgis.core import QgsProject
        from transect_navigator.ui.analyze_panel import AnalyzePanel
        fixture=test_comparison.ComparisonRuntimeTests()
        project=QgsProject.instance()
        before=set(project.mapLayers())
        plan,source=fixture.plan(),fixture.points([(10,1),(20,1)])
        panel=None
        group_count=len(project.layerTreeRoot().children())
        try:
            project.addMapLayer(plan)
            project.addMapLayer(source)
            retained=set(project.mapLayers())
            panel=AnalyzePanel()
            panel.planned_layer.setLayer(plan)
            panel.id_field.setField('tid')
            panel.feature.setCurrentIndex(1)
            panel.recorded_layer.setLayer(source)
            panel.timestamp.setField('time')
            original=project.addMapLayer
            calls=0
            def fail(layer,legend=True):
                nonlocal calls
                calls+=1
                return None if calls==2 else original(layer,legend)
            with patch.object(project,'addMapLayer',side_effect=fail):
                panel.ingest_data()
            self.assertEqual(set(project.mapLayers()),retained)
            self.assertIn('Could not add',panel.status.text())
            self.assertTrue(panel.ingest.isEnabled())
        finally:
            if panel is not None:
                panel.deleteLater()
            project.removeMapLayers(list(set(project.mapLayers())-before))
        self.assertEqual(len(project.layerTreeRoot().children()),group_count)

    def test_prepare_does_not_report_success_when_registration_fails(self):
        from qgis.core import QgsProject
        from transect_navigator.ui.main_dialog import MainDialog
        from transect_navigator.integration.prepare import prepare_navigation
        fixture=test_comparison.ComparisonRuntimeTests()
        prepared=prepare_navigation(fixture.plan(),'tid')
        dialog=MainDialog()
        try:
            with patch.object(dialog,'_prepare',return_value=prepared), \
                    patch.object(QgsProject.instance(),'addMapLayer',return_value=None):
                dialog.generate_waypoints()
            self.assertIn('could not add',dialog.status.text())
            self.assertTrue(dialog.generate.isEnabled())
        finally:
            dialog.deleteLater()

    def test_real_qt_lifecycle_has_one_action_and_owned_dialog(self):
        from types import SimpleNamespace
        from qgis.PyQt.QtWidgets import QMainWindow,QMenu
        from qgis.PyQt.QtCore import QCoreApplication,QEvent
        from qgis.core import QgsProject
        from transect_navigator import classFactory
        window=QMainWindow()
        menu=QMenu(window)
        # Only the iface host is a harness; all plugin/QGIS/Qt objects are real.
        iface=SimpleNamespace(mainWindow=lambda:window,
            addPluginToMenu=lambda title,action:menu.addAction(action),
            removePluginMenu=lambda title,action:menu.removeAction(action))
        plugin=classFactory(iface)
        before=set(QgsProject.instance().mapLayers())
        try:
            for _ in range(2):
                plugin.initGui()
                plugin.initGui()
                self.assertEqual(len(menu.actions()),1)
                plugin.run()
                dialog=plugin.dialog
                dialog.close()
                plugin.run()
                self.assertIs(plugin.dialog,dialog)
                plugin.unload()
                plugin.unload()
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
                self.assertEqual(len(menu.actions()),0)
                self.assertIsNone(plugin.dialog)
            self.assertEqual(set(QgsProject.instance().mapLayers()),before)
        finally:
            plugin.unload()
            window.deleteLater()

    def test_sparse_gpx_extensions_cannot_expand_to_unbounded_dense_fields(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.io.recorded import InputPoint,FileRecording
        from transect_navigator.integration.recorded import from_file_records
        points=tuple(InputPoint(str(i),'survey',(0,0),((f'unique_{i}','value'),)) for i in range(513))
        records=FileRecording(points,(),(),'digest','source')
        with self.assertRaisesRegex(ValueError,'fields'):
            from_file_records(records,QgsCoordinateReferenceSystem('EPSG:4326'))
