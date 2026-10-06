"""Actual QGIS multi-plan setup, legacy engine equivalence and package round trips."""
import hashlib
import importlib.util
import json
from math import degrees
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SAMPLE = Path(__file__).resolve().parents[2]/'samples/survey_prepare/planned_transects.geojson'


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class SurveyPrepareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from qgis.core import QgsProject, QgsVectorLayer
        from transect_navigator.survey.model import SurveyRegistry
        self.project = QgsProject()
        self.registry = SurveyRegistry()
        self.layer = QgsVectorLayer(str(SAMPLE), 'Official plans', 'ogr')
        self.assertTrue(self.layer.isValid())

    def setup(self, layer=None, **kwargs):
        from transect_navigator.integration.survey_prepare import setup_survey
        options = dict(survey_id='SURVEY-A', name='Simple survey', registry=self.registry,
                       created_utc='2026-10-06T12:00:00+00:00', project=self.project)
        options.update(kwargs)
        return setup_survey(layer if layer is not None else self.layer, 'Transect_ID', **options)

    @staticmethod
    def native(layer):
        return (layer.crs().toWkt(), layer.fields().names(),
                [(f.id(), f.attributes(), bytes(f.geometry().asWkb())) for f in layer.getFeatures()])

    def mutable(self):
        from transect_navigator.integration.recorded import snapshot
        return snapshot(self.layer, 'editable fixture copy')

    def test_twelve_plans_exact_legacy_equivalence_and_source_preservation(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        from transect_navigator.integration.prepare import prepare_navigation
        before = self.native(self.layer)
        self.layer.selectByIds([next(self.layer.getFeatures()).id()])
        selection = self.layer.selectedFeatureIds()
        setup = self.setup()
        result = prepare_survey_navigation(setup, project=self.project)
        direct = prepare_navigation(self.layer, 'Transect_ID', project=self.project)
        self.assertEqual(result.prepared.rows, direct.rows)
        self.assertEqual(len(result.prepared.rows), 132)
        self.assertEqual(len(setup.survey.plans), 12)
        self.assertEqual(before, self.native(self.layer))
        self.assertEqual(selection, self.layer.selectedFeatureIds())
        self.assertFalse(self.project.mapLayers())
        for i, plan in enumerate(setup.survey.plans):
            self.assertEqual(plan.navigation_status, 'SUPPORTED')
            self.assertEqual(plan.qa_model_status, 'SUPPORTED')
            self.assertAlmostEqual(plan.navigation_length_m, 100, places=6)
            self.assertAlmostEqual(plan.qa_length_m, 100, places=6)
            rows = [r for r in result.prepared.rows if r.transect_id == plan.transect_id]
            self.assertEqual([r.sequence for r in rows], list(range(1,12)))
            self.assertEqual([r.waypoint_id for r in rows], [f'T{i+1:02d}_{j:03d}' for j in range(1,12)])
            for j, row in enumerate(rows):
                self.assertAlmostEqual(row.distance_along_m, j*10, places=6)
                self.assertAlmostEqual(row.coordinate[0], degrees((i*200+j*10)/6378137), places=7)
                self.assertAlmostEqual(row.coordinate[1], 0, places=7)
                if j < 10:
                    self.assertAlmostEqual(row.bearing_next_deg, 90, places=7)
            self.assertIsNone(rows[-1].bearing_next_deg)
        self.assertEqual({f['Survey_ID'] for f in result.prepared.source.getFeatures()}, {'original-owner-value'})
        self.assertNotIn('Survey_ID', result.prepared.waypoints.fields().names())

    def test_reverse_is_explicit_and_does_not_reverse_official_plan(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        setup = self.setup()
        forward = prepare_survey_navigation(setup, project=self.project)
        reverse = prepare_survey_navigation(setup, reverse=True, project=self.project)
        self.assertEqual(self.native(forward.prepared.source), self.native(reverse.prepared.source))
        self.assertEqual(forward.prepared.transects, reverse.prepared.transects)
        for identifier in [p.transect_id for p in setup.survey.plans]:
            a = [r for r in forward.prepared.rows if r.transect_id == identifier]
            b = [r for r in reverse.prepared.rows if r.transect_id == identifier]
            self.assertEqual(a[0].coordinate, b[-1].coordinate)
            self.assertEqual(a[-1].coordinate, b[0].coordinate)
            self.assertEqual(b[0].sequence, 1)
            self.assertAlmostEqual(b[0].bearing_next_deg, 270, places=7)
            self.assertIsNone(b[-1].bearing_next_deg)

    def test_duplicate_trimmed_ids_rejected_with_full_diagnostics(self):
        from transect_navigator.integration.survey_prepare import SurveyPreflightError
        layer = self.mutable()
        features = list(layer.getFeatures())
        index = layer.fields().indexFromName('Transect_ID')
        layer.dataProvider().changeAttributeValues({features[1].id(): {index: ' T01 '}})
        with self.assertRaises(SurveyPreflightError) as caught:
            self.setup(layer)
        self.assertEqual(len(caught.exception.plans), 12)
        self.assertEqual(sum(p.navigation_status == 'INVALID_ID' for p in caught.exception.plans), 2)
        with self.assertRaises(KeyError):
            self.registry.get('SURVEY-A')

    def test_blank_and_null_ids_rejected(self):
        from transect_navigator.integration.survey_prepare import SurveyPreflightError
        for value in (' ', None):
            with self.subTest(value=value):
                layer = self.mutable()
                feature = next(layer.getFeatures())
                layer.dataProvider().changeAttributeValues({feature.id(): {0: value}})
                with self.assertRaises(SurveyPreflightError) as caught:
                    self.setup(layer)
                self.assertEqual(len(caught.exception.plans), 12)
                self.assertIsNone(caught.exception.plans[0].transect_id)

    def test_unsupported_geometry_visible_and_exclusion_explicit(self):
        from qgis.core import QgsGeometry
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        layer = self.mutable()
        feature = next(layer.getFeatures())
        layer.dataProvider().changeGeometryValues({feature.id(): QgsGeometry()})
        setup = self.setup(layer)
        self.assertEqual(len(setup.survey.plans), 12)
        self.assertEqual(setup.survey.plans[0].navigation_status, 'UNSUPPORTED')
        self.assertTrue(setup.survey.plans[0].geometry_is_null)
        with self.assertRaisesRegex(ValueError, 'explicit exclusion'):
            prepare_survey_navigation(setup, project=self.project)
        result = prepare_survey_navigation(setup, exclusions={'T01': 'Missing official geometry; review required'}, project=self.project)
        self.assertEqual(len(result.prepared.rows), 121)
        from transect_navigator.integration.survey_export import export_survey_navigation
        with TemporaryDirectory() as folder:
            output = export_survey_navigation(result, Path(folder), 'excluded')
            manifest = json.loads((output/'survey_manifest.json').read_text())
            self.assertEqual(len(manifest['plans']), 12)
            omitted = manifest['plans'][0]
            self.assertFalse(omitted['included'])
            self.assertTrue(omitted['geometry_is_null'])
            self.assertTrue(omitted['exclusion_reason'])
            self.assertEqual(manifest['navigation']['included_plan_count'], 11)

    def test_multipart_and_z_geometry_are_not_silently_repaired(self):
        from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
        for kind, wkt in [('MultiLineString','MULTILINESTRING((0 0, .001 0),(.002 0,.003 0))'),
                          ('LineStringZ','LINESTRING Z(0 0 1, .001 0 2)')]:
            with self.subTest(kind=kind):
                layer = QgsVectorLayer(f'{kind}?crs=EPSG:4326&field=Transect_ID:string', 'unsupported', 'memory')
                feature = QgsFeature(layer.fields());feature.setAttributes(['T01'])
                feature.setGeometry(QgsGeometry.fromWkt(wkt))
                self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
                from transect_navigator.survey.model import SurveyRegistry
                setup = self.setup(layer, registry=SurveyRegistry())
                self.assertEqual(setup.survey.plans[0].navigation_status, 'UNSUPPORTED')
                self.assertIn('single-part 2D', setup.survey.plans[0].navigation_reason)
                self.assertEqual(setup.survey.plans[0].geometry_wkb_hex, bytes(feature.geometry().asWkb()).hex())

    def test_invalid_crs_visible_and_blocks_prepare(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        layer = self.mutable(); layer.setCrs(QgsCoordinateReferenceSystem())
        setup = self.setup(layer)
        self.assertTrue(all(p.navigation_status == 'UNSUPPORTED' for p in setup.survey.plans))
        with self.assertRaisesRegex(ValueError, 'explicit exclusion'):
            prepare_survey_navigation(setup, project=self.project)

    def test_qa_extent_status_does_not_claim_navigation_length_is_qa_length(self):
        from qgis.core import QgsGeometry
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        layer = self.mutable(); feature = next(layer.getFeatures())
        layer.dataProvider().changeGeometryValues({feature.id(): QgsGeometry.fromWkt('LINESTRING(0 0, .06 0)')})
        setup = self.setup(layer)
        plan = setup.survey.plans[0]
        self.assertEqual(plan.navigation_status, 'SUPPORTED')
        self.assertEqual(plan.qa_model_status, 'UNSUPPORTED')
        self.assertIn('5 km radius', plan.qa_model_reason)
        self.assertIsNone(plan.qa_length_m)
        self.assertGreater(plan.navigation_length_m, 6000)
        self.assertGreater(len(prepare_survey_navigation(setup, project=self.project).prepared.rows), 132)

    def test_digest_independent_of_layer_name_fids_and_feature_order(self):
        from transect_navigator.integration.recorded import snapshot
        from transect_navigator.integration.survey_prepare import layer_digest
        from transect_navigator.survey.model import SurveyRegistry
        features = list(self.layer.getFeatures())[::-1]
        for i, f in enumerate(features):
            f.setId(9000+i)
        other = snapshot(self.layer, 'new mutable layer identity', features)
        a = self.setup()
        b = self.setup(other, registry=SurveyRegistry())
        self.assertEqual(a.survey.plan_snapshot_digest, b.survey.plan_snapshot_digest)
        self.assertEqual([p.source_content_key for p in a.survey.plans], [p.source_content_key for p in b.survey.plans])
        self.assertEqual(layer_digest(other), layer_digest(self.layer))
        self.assertNotEqual([p.original_feature_key for p in a.survey.plans], [p.original_feature_key for p in b.survey.plans])

    def test_conflicting_survey_registration_cannot_replace_existing(self):
        original = self.setup()
        with self.assertRaisesRegex(ValueError, 'already registered'):
            self.setup(name='different survey')
        self.assertIs(self.registry.get('SURVEY-A'), original.survey)

    def test_snapshot_is_detached_and_tampering_is_rejected(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        layer = self.mutable(); setup = self.setup(layer)
        feature = next(layer.getFeatures())
        layer.dataProvider().changeAttributeValues({feature.id(): {0: 'EDITED'}})
        result = prepare_survey_navigation(setup, project=self.project)
        self.assertEqual(result.prepared.rows[0].transect_id, 'T01')
        saved = next(setup.source.getFeatures())
        setup.source.dataProvider().changeAttributeValues({saved.id(): {0: 'TAMPERED'}})
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            prepare_survey_navigation(setup, project=self.project)

    def test_export_all_formats_reopens_and_preserves_values(self):
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        from transect_navigator.integration.survey_export import export_survey_navigation
        from transect_navigator.io.exchange import validate_exchange_files, FORMATS
        result = prepare_survey_navigation(self.setup(), project=self.project)
        before = self.native(self.layer)
        with TemporaryDirectory() as folder:
            output = export_survey_navigation(result, Path(folder), 'survey')
            validate_exchange_files(output, result.prepared.transects, result.prepared.rows, FORMATS)
            path = output/'survey_navigation.gpkg'
            counts = {'planned_transects':12, 'navigation_waypoints':132, 'surveys':1, 'plan_manifest':12, 'navigation_run':1}
            for name, count in counts.items():
                layer = QgsVectorLayer(f'{path}|layername={name}', name, 'ogr')
                self.assertTrue(layer.isValid(), name)
                self.assertEqual(layer.featureCount(), count)
            points = QgsVectorLayer(f'{path}|layername=navigation_waypoints', 'points', 'ogr')
            self.assertEqual(points.crs().authid(), 'EPSG:4326')
            rows = sorted(points.getFeatures(), key=lambda f:(f['Transect_ID'],f['Sequence']))
            for expected, actual in zip(result.prepared.rows, rows):
                self.assertEqual(actual['Transect_ID'], expected.transect_id)
                self.assertEqual(actual['Waypoint_ID'], expected.waypoint_id)
                self.assertEqual(actual['Sequence'], expected.sequence)
                self.assertAlmostEqual(actual['Distance_Along_m'], expected.distance_along_m, places=8)
            plans = QgsVectorLayer(f'{path}|layername=planned_transects', 'plans', 'ogr')
            self.assertEqual({f['Survey_ID'] for f in plans.getFeatures()}, {'original-owner-value'})
            self.assertEqual({f['Transect_ID'] for f in plans.getFeatures()}, {f'T{i:02d}' for i in range(1,13)})
            manifest = json.loads((output/'survey_manifest.json').read_text())
            self.assertEqual(manifest['survey']['survey_id'], 'SURVEY-A')
            self.assertEqual(manifest['survey']['plan_snapshot_digest'], result.setup.survey.plan_snapshot_digest)
            self.assertNotIn('source_layer_id', manifest['navigation']['provenance_json'])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('pragma quick_check').fetchone(), ('ok',))
                self.assertEqual(db.execute('select count(*) from plan_manifest').fetchone(), (12,))
            for filename, count in [('navigation_waypoints.geojson',132),
                                    ('planned_transects.geojson',12),
                                    ('navigation.gpx|layername=routes',12),
                                    ('navigation.gpx|layername=route_points',132)]:
                self.assertTrue((output/filename.split('|')[0]).exists())
                reopened = QgsVectorLayer(str(output/filename), filename, 'ogr')
                self.assertTrue(reopened.isValid(), filename)
                self.assertEqual(reopened.featureCount(), count, filename)
            for plan, exported in zip(result.setup.survey.plans, manifest['plans']):
                self.assertEqual(exported['geometry_wkb_hex'], plan.geometry_wkb_hex)
                self.assertEqual(exported['attributes_json'], plan.attributes_json)
                self.assertEqual(exported['source_content_key'], plan.source_content_key)
        self.assertEqual(before, self.native(self.layer))

    def test_existing_package_is_not_modified_and_failed_write_is_not_published(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        from transect_navigator.integration.survey_export import export_survey_navigation
        result = prepare_survey_navigation(self.setup(), project=self.project)
        with TemporaryDirectory() as folder:
            root = Path(folder); output = export_survey_navigation(result, root, 'survey')
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
            with self.assertRaisesRegex(ValueError, 'already exists'):
                export_survey_navigation(result, root, 'survey')
            self.assertEqual(before, {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()})
            with patch('transect_navigator.integration.survey_export.validate_exchange_files', side_effect=ValueError('injected verification failure')):
                with self.assertRaisesRegex(ValueError, 'verification failure'):
                    export_survey_navigation(result, root, 'failed')
            self.assertEqual({p.name for p in root.iterdir()}, {'survey'})

    def test_export_rejects_mutated_waypoints(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        from transect_navigator.integration.survey_export import export_survey_navigation
        result = prepare_survey_navigation(self.setup(), project=self.project)
        feature = next(result.prepared.waypoints.getFeatures())
        result.prepared.waypoints.dataProvider().changeAttributeValues({feature.id(): {0: 'WRONG'}})
        with TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, 'snapshot changed'):
            export_survey_navigation(result, Path(folder), 'bad')

    def test_invalid_setup_and_exclusion_inputs(self):
        from qgis.core import QgsVectorLayer
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        for layer in (QgsVectorLayer(), QgsVectorLayer('LineString?crs=EPSG:4326&field=Transect_ID:string','empty','memory')):
            with self.subTest(layer=layer.name()), self.assertRaises(ValueError):
                self.setup(layer)
        setup = self.setup()
        for exclusions in ({'T99':'not present'}, {'T01':''}, {p.transect_id:'omit' for p in setup.survey.plans}):
            with self.subTest(exclusions=exclusions), self.assertRaises(ValueError):
                prepare_survey_navigation(setup, exclusions=exclusions, project=self.project)
        for spacing in (0,-1,float('nan')):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                prepare_survey_navigation(setup, spacing, project=self.project)

    def test_survey_namespace_does_not_change_legacy_waypoint_ids(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        a = prepare_survey_navigation(self.setup(), project=self.project)
        b = prepare_survey_navigation(self.setup(survey_id='SURVEY-B'), project=self.project)
        self.assertEqual(a.prepared.rows, b.prepared.rows)
        self.assertEqual(a.setup.survey.plan_snapshot_digest, b.setup.survey.plan_snapshot_digest)
        self.assertNotEqual(a.setup.survey.survey_id, b.setup.survey.survey_id)

    def test_nonmetre_projected_source_keeps_native_geometry_and_measures_metres(self):
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform
        from transect_navigator.integration.survey_prepare import (
            crs_roundtrip_equivalent, crs_semantic_definition,
            layer_digest, layer_serialization_digest, prepare_survey_navigation)
        layer = self.mutable()
        feet = QgsCoordinateReferenceSystem()
        self.assertTrue(feet.createFromProj(
            '+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 '
            '+ellps=WGS84 +units=us-ft +no_defs'))
        transform = QgsCoordinateTransform(layer.crs(), feet, self.project)
        changes = {}
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            geometry.transform(transform)
            changes[feature.id()] = geometry
        self.assertTrue(layer.dataProvider().changeGeometryValues(changes))
        layer.setCrs(feet)
        before = self.native(layer)
        setup = self.setup(layer)
        self.assertEqual(setup.survey.source_crs_wkt, feet.toWkt())
        self.assertEqual(setup.survey.source_crs_semantic_definition,
                         crs_semantic_definition(feet))
        self.assertEqual(setup.survey.source_crs_authid, '')
        self.assertEqual(setup.source.crs(), feet)
        # QGIS 4.2.3's memory provider removes the optional nested EPSG:9122
        # angular-unit authority label without changing the CRS definition.
        self.assertNotEqual(setup.source.crs().toWkt(), feet.toWkt())
        self.assertEqual(crs_semantic_definition(setup.source.crs()),
                         crs_semantic_definition(feet))
        self.assertEqual(layer_digest(setup.source), layer_digest(layer))
        self.assertNotEqual(layer_serialization_digest(setup.source),
                            layer_serialization_digest(layer))
        self.assertEqual(setup.survey.plan_snapshot_serialization_digest,
                         layer_serialization_digest(layer))
        self.assertTrue(all(plan.crs_wkt == feet.toWkt() for plan in setup.survey.plans))
        self.assertTrue(all(plan.crs_semantic_definition == crs_semantic_definition(feet)
                            for plan in setup.survey.plans))
        result = prepare_survey_navigation(setup, project=self.project)
        self.assertEqual(len(result.prepared.rows), 132)
        for plan in setup.survey.plans:
            self.assertAlmostEqual(plan.navigation_length_m, 100, places=5)
        self.assertEqual(before, self.native(layer))
        from transect_navigator.integration.survey_export import export_survey_navigation
        from qgis.core import QgsVectorLayer
        with TemporaryDirectory() as folder:
            output = export_survey_navigation(result, Path(folder), 'feet')
            reopened = QgsVectorLayer(f'{output}/survey_navigation.gpkg|layername=planned_transects', 'native feet', 'ogr')
            self.assertTrue(crs_roundtrip_equivalent(reopened.crs(), feet))
            self.assertEqual(crs_semantic_definition(reopened.crs()),
                             crs_semantic_definition(feet))
            self.assertEqual([bytes(f.geometry().asWkb()) for f in reopened.getFeatures()],
                             [bytes(f.geometry().asWkb()) for f in result.prepared.source.getFeatures()])
            manifest = json.loads((output/'survey_manifest.json').read_text())
            self.assertEqual(manifest['survey']['source_crs_wkt'], feet.toWkt())
            self.assertEqual(manifest['survey']['source_crs_semantic_definition'],
                             crs_semantic_definition(feet))
            self.assertEqual(manifest['plans'][0]['crs_wkt'], feet.toWkt())
            self.assertEqual(manifest['survey']['plan_snapshot_serialization_digest'],
                             layer_serialization_digest(layer))

    def test_snapshot_rejects_genuinely_different_custom_crs(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.survey_prepare import (
            _native_snapshot, assert_snapshot_integrity, crs_roundtrip_equivalent,
            crs_semantically_equivalent, layer_digest)
        original = self.mutable()
        first = QgsCoordinateReferenceSystem()
        second = QgsCoordinateReferenceSystem()
        self.assertTrue(first.createFromProj(
            '+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs'))
        self.assertTrue(second.createFromProj(
            '+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=100 +y_0=0 +datum=WGS84 +units=m +no_defs'))
        original.setCrs(first)
        copied = _native_snapshot(original, 'semantic snapshot', list(original.getFeatures()))
        copied.setCrs(second)
        self.assertFalse(crs_semantically_equivalent(original.crs(), copied.crs()))
        self.assertFalse(crs_roundtrip_equivalent(original.crs(), copied.crs()))
        self.assertNotEqual(layer_digest(original), layer_digest(copied))
        with self.assertRaisesRegex(ValueError, 'changed the CRS definition'):
            assert_snapshot_integrity(original, copied)

    def test_epsg_snapshot_semantics_and_exact_provenance_are_unchanged(self):
        from transect_navigator.integration.survey_prepare import crs_semantic_definition
        before = self.native(self.layer)
        setup = self.setup()
        self.assertEqual(setup.source.crs(), self.layer.crs())
        self.assertEqual(setup.survey.source_crs_wkt, self.layer.crs().toWkt())
        self.assertEqual(setup.survey.source_crs_semantic_definition,
                         crs_semantic_definition(self.layer.crs()))
        self.assertEqual(setup.survey.source_crs_authid, 'EPSG:4326')
        self.assertEqual(before, self.native(self.layer))

    def test_digest_detects_attribute_geometry_schema_and_crs_changes(self):
        from qgis.PyQt.QtCore import QMetaType
        from qgis.core import QgsCoordinateReferenceSystem, QgsField, QgsGeometry
        from transect_navigator.integration.survey_prepare import layer_digest
        original = layer_digest(self.layer)
        for kind in ('attribute','geometry','schema','crs'):
            with self.subTest(kind=kind):
                layer = self.mutable(); feature = next(layer.getFeatures())
                if kind == 'attribute':
                    layer.dataProvider().changeAttributeValues({feature.id(): {0:'CHANGED'}})
                elif kind == 'geometry':
                    layer.dataProvider().changeGeometryValues({feature.id():QgsGeometry.fromWkt('LINESTRING(0 0,.001 0)')})
                elif kind == 'schema':
                    layer.dataProvider().addAttributes([QgsField('extra',QMetaType.Type.QString)])
                    layer.updateFields()
                else:
                    layer.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
                self.assertNotEqual(original, layer_digest(layer))

    def test_unsupported_coordinates_and_zero_length_are_visible(self):
        from qgis.core import QgsGeometry
        from transect_navigator.survey.model import SurveyRegistry
        for wkt in ('LINESTRING(0 91,.001 91)', 'LINESTRING(0 0,0 0)'):
            with self.subTest(wkt=wkt):
                layer = self.mutable(); feature = next(layer.getFeatures())
                layer.dataProvider().changeGeometryValues({feature.id():QgsGeometry.fromWkt(wkt)})
                setup = self.setup(layer, registry=SurveyRegistry())
                self.assertEqual(len(setup.survey.plans), 12)
                self.assertEqual(setup.survey.plans[0].navigation_status, 'UNSUPPORTED')
                self.assertTrue(setup.survey.plans[0].navigation_reason)

    def test_configured_spacing_keeps_short_final_interval_and_reverse_anchor(self):
        from transect_navigator.integration.survey_prepare import prepare_survey_navigation
        setup = self.setup()
        for reverse in (False, True):
            result = prepare_survey_navigation(setup, 30, reverse=reverse, project=self.project)
            self.assertEqual(len(result.prepared.rows), 60)
            for plan in setup.survey.plans:
                rows = [r for r in result.prepared.rows if r.transect_id == plan.transect_id]
                for expected, row in zip((0,30,60,90,100), rows):
                    self.assertAlmostEqual(row.distance_along_m, expected, places=6)
                self.assertEqual([r.sequence for r in rows], [1,2,3,4,5])
                self.assertIsNone(rows[-1].bearing_next_deg)
                self.assertAlmostEqual(rows[0].bearing_next_deg, 270 if reverse else 90, places=7)

    def test_tagged_attribute_identity_preserves_null_types_and_datetime(self):
        from qgis.PyQt.QtCore import QDateTime, Qt, QByteArray
        from transect_navigator.integration.survey_prepare import _native_value
        from transect_navigator.survey.model import digest
        values = [None, '', 1, '1', 1.0, True, QByteArray(b'abc'),
                  QDateTime.fromString('2026-10-06T12:00:00.123Z', Qt.DateFormat.ISODateWithMs)]
        tags = [_native_value(value) for value in values]
        self.assertEqual(len(set(digest(tag) for tag in tags)), len(values))
        self.assertEqual(tags[0], ['null'])
        self.assertEqual(tags[-1][0], 'datetime')
        self.assertIn('.123', tags[-1][1])
