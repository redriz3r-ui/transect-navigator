"""Real PyQGIS tests for Stage 3 multi-source immutable intake and persistence."""
import importlib.util
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class SurveyIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from qgis.core import QgsProject
        from transect_navigator.survey.model import Survey
        self.project = QgsProject()
        self.survey = Survey('SURVEY-A', 'Stage 3 test', 'survey-navigation-1',
            '2026-10-06T12:00:00+00:00', 'plan-digest', 'serialization-digest',
            '', '', 'source-wkt', 'source-proj', 'EPSG:4326', '[]', 'Transect_ID',
            '1', ())

    def intake(self):
        from transect_navigator.integration.survey_intake import SurveyIntake
        return SurveyIntake(self.survey, project=self.project)

    @staticmethod
    def csv(path: Path, rows, header=('x', 'y', 'time', 'accuracy', 'hdop', 'pdop',
                                      'sat', 'fix', 'ele', 'note')):
        import csv
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    @staticmethod
    def csv_options(*, surveyor=None, timezone=''):
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        return ImportOptions('x', 'y', 'EPSG:4326', FieldMappings(
            timestamp='time', timezone=timezone, accuracy='accuracy', hdop='hdop',
            pdop='pdop', satellites='sat', fix='fix', elevation='ele'), surveyor)

    @staticmethod
    def rows(start=0, times=None):
        times = times or ('2026-01-01T00:00:00Z', '2026-01-01T00:00:10Z')
        return [(start, 0, times[0], '2.5', '0.8', '1.2', '9', '3d', '100', 'a'),
                (start + .0001, 0, times[1], '', '0.9', '', '8', '3d', '101', 'b')]

    def test_multiple_files_surveyors_unknown_and_exact_duplicate_rename(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            first = self.csv(folder/'a.csv', self.rows())
            second = self.csv(folder/'b.csv', self.rows(.001))
            renamed = folder/'renamed.csv'; shutil.copyfile(first, renamed)
            intake = self.intake()
            a = intake.import_file(first, self.csv_options(surveyor='S01'),
                                   imported_utc='2026-10-06T12:01:00Z')
            b = intake.import_file(second, self.csv_options(),
                                   imported_utc='2026-10-06T12:02:00Z')
            duplicate = intake.import_file(renamed, self.csv_options(surveyor='S99'),
                                           imported_utc='2026-10-06T12:03:00Z')
            self.assertEqual((a.status, b.status, duplicate.status),
                             ('SUCCESS', 'SUCCESS', 'DUPLICATE'))
            self.assertEqual(duplicate.duplicate_of_source_id, a.source_id)
            totals = intake.model.totals()
            self.assertEqual((totals.source_count, totals.recording_count,
                              totals.observation_count, totals.duplicate_attempt_count),
                             (2, 2, 4, 1))
            surveyors = {row.surveyor_id for row in intake.model.recordings.values()}
            self.assertEqual(surveyors, {'S01', None})

    def test_overlapping_nonidentical_flagged_but_not_merged(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            rows = self.rows()
            a = self.csv(folder/'a.csv', rows)
            b = self.csv(folder/'b.csv', rows + [(.002, 0, '2026-01-01T00:00:20Z',
                '3', '', '', '', '', '', 'extra')])
            intake = self.intake()
            intake.import_file(a, self.csv_options())
            intake.import_file(b, self.csv_options())
            self.assertEqual(intake.model.totals().observation_count, 5)
            self.assertTrue(all('SUSPECTED_OVERLAP' in source.warnings
                                for source in intake.model.sources.values()))

    def test_equal_positions_at_different_times_are_legitimate(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            a = self.csv(folder/'a.csv', self.rows())
            b = self.csv(folder/'b.csv', self.rows(times=(
                '2026-01-02T00:00:00Z', '2026-01-02T00:00:10Z')))
            intake = self.intake()
            intake.import_file(a, self.csv_options())
            intake.import_file(b, self.csv_options())
            self.assertFalse(any('SUSPECTED_OVERLAP' in source.warnings
                                 for source in intake.model.sources.values()))
            self.assertEqual(intake.model.totals().observation_count, 4)

    def test_failed_source_is_auditable_and_does_not_erase_valid_source(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            valid = self.csv(folder/'valid.csv', self.rows())
            invalid = folder/'invalid.csv'; invalid.write_text('x,y\nno,coordinate\n')
            intake = self.intake()
            intake.import_file(valid, self.csv_options())
            failed = intake.import_file(invalid, self.csv_options())
            missing = intake.import_file(folder/'missing.gpx')
            self.assertEqual((failed.status, missing.status), ('FAILED', 'FAILED'))
            self.assertEqual(intake.model.totals().state, 'PARTIAL')
            self.assertEqual(intake.model.totals().observation_count, 2)
            self.assertEqual(intake.model.totals().failed_attempt_count, 2)
            self.assertTrue(any(source.import_status == 'FAILED'
                                for source in intake.model.sources.values()))
            duplicate_bad = intake.import_file(invalid, self.csv_options())
            self.assertEqual(duplicate_bad.status, 'DUPLICATE')

    def test_timestamp_timezone_duplicate_missing_and_gnss_preservation(self):
        with TemporaryDirectory() as folder:
            path = self.csv(Path(folder)/'times.csv', [
                (0, 0, '2026-01-01T12:00:00', '4.5', '1.1', '1.7', '7', '2d', '20', 'raw'),
                (.0001, 0, '2026-01-01T12:00:00', '', '', '', '', '', '', 'duplicate'),
                (.0002, 0, '', '', '', '', '', '', '', 'missing')])
            intake = self.intake()
            intake.import_file(path, self.csv_options(timezone='America/Los_Angeles'))
            values = list(intake.model.normalized_observations.values())
            self.assertEqual([row.original_timestamp for row in values],
                             ['2026-01-01T12:00:00', '2026-01-01T12:00:00', ''])
            self.assertEqual([row.time_status for row in values],
                             ['duplicate', 'duplicate', 'missing'])
            self.assertEqual(values[0].timestamp_utc, '2026-01-01T20:00:00Z')
            self.assertEqual((values[0].accuracy_raw, values[0].hdop_raw,
                              values[0].pdop_raw, values[0].satellites_raw,
                              values[0].fix_raw, values[0].elevation_raw),
                             ('4.5', '1.1', '1.7', '7', '2d', '20'))
            self.assertIsNone(values[1].accuracy_raw)

    def test_timezone_mapping_does_not_choose_ambiguous_or_nonexistent_local_time(self):
        from transect_navigator.integration.survey_intake import _timestamp
        self.assertEqual(_timestamp('2026-11-01T01:30:00', 'America/Los_Angeles'),
                         (None, 'ambiguous_local_time'))
        self.assertEqual(_timestamp('2026-03-08T02:30:00', 'America/Los_Angeles'),
                         (None, 'nonexistent_local_time'))

    def test_gpx_multiple_tracks_and_structural_segments(self):
        gpx = '''<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
<trk><name>one</name><trkseg>
<trkpt lat="0" lon="0"><ele>1</ele><time>2026-01-01T00:00:00Z</time><hdop>0.8</hdop></trkpt>
<trkpt lat="0" lon="0.001"><time>2026-01-01T00:00:10Z</time></trkpt>
</trkseg><trkseg><trkpt lat="0" lon="0.002"><time>2026-01-01T00:00:20Z</time></trkpt></trkseg></trk>
<trk><name>two</name><trkseg><trkpt lat="0.01" lon="0"><time>2026-01-02T00:00:00Z</time></trkpt></trkseg></trk>
</gpx>'''
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.integration.survey_intake_storage import (
            load_survey_intake, save_survey_intake)
        from transect_navigator.survey.intake import FieldMappings
        with TemporaryDirectory() as folder:
            path = Path(folder)/'multi.gpx'; path.write_text(gpx, encoding='utf-8')
            intake = self.intake()
            result = intake.import_file(path, ImportOptions(mappings=FieldMappings(
                timestamp='time', hdop='hdop', elevation='ele')))
            self.assertEqual(result.status, 'SUCCESS')
            self.assertEqual((len(intake.model.recordings),
                              len(intake.model.raw_observations)), (2, 4))
            groups = sorted(len(record.structural_segments)
                            for record in intake.model.recordings.values())
            self.assertEqual(groups, [1, 2])
            # The singleton segment is retained in the raw GPX bytes and structural
            # registry but cannot be represented as a valid LineString.
            self.assertEqual(sum(native.tracks.featureCount()
                                 for native in intake.native_recordings.values()), 1)
            output = save_survey_intake(intake, Path(folder), 'gpx-intake')
            reopened = load_survey_intake(output, self.survey, project=self.project)
            self.assertEqual(len(reopened.model.raw_observations), 4)
            self.assertEqual(sum(native.tracks.featureCount()
                                 for native in reopened.native_recordings.values()), 1)

    def _point_layer(self, crs='EPSG:3857', z=False, null=False):
        from qgis.PyQt.QtCore import QMetaType
        from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer
        kind = 'PointZ' if z else 'Point'
        layer = QgsVectorLayer(f'{kind}?crs={crs}', 'loaded observations', 'memory')
        layer.dataProvider().addAttributes([QgsField('time', QMetaType.Type.QString),
                                            QgsField('custom', QMetaType.Type.QString)])
        layer.updateFields()
        features = []
        for index, wkt in enumerate(('POINT Z (0 0 7)' if z else 'POINT (0 0)',
                                     'POINT Z (10 0 8)' if z else 'POINT (10 0)')):
            feature = QgsFeature(layer.fields())
            feature.setAttributes([f'2026-01-01T00:00:{index}0Z', f'value-{index}'])
            if not (null and index == 1):
                feature.setGeometry(QgsGeometry.fromWkt(wkt))
            features.append(feature)
        self.assertTrue(layer.dataProvider().addFeatures(features)[0])
        return layer

    def test_loaded_layer_mixed_crs_null_geometry_and_z_are_preserved(self):
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        intake = self.intake()
        layer = self._point_layer(z=True, null=True)
        before = [(f.attributes(), bytes(f.geometry().asWkb())) for f in layer.getFeatures()]
        result = intake.import_layer(layer, options=ImportOptions(
            mappings=FieldMappings(timestamp='time')))
        self.assertEqual(result.status, 'SUCCESS')
        native = next(iter(intake.native_recordings.values())).observations
        after = [(f.attributes()[:2], bytes(f.geometry().asWkb())) for f in native.getFeatures()]
        self.assertEqual(before, after)
        normalized = list(intake.model.normalized_observations.values())
        self.assertEqual([row.position_status for row in normalized],
                         ['valid', 'missing_geometry'])
        self.assertTrue(native.wkbType().name.endswith('Z'))
        with TemporaryDirectory() as folder:
            csv_path = self.csv(Path(folder)/'degrees.csv', self.rows())
            intake.import_file(csv_path, self.csv_options())
        self.assertEqual(len({record.original_crs_authid for record in
                              intake.model.recordings.values()}), 2)

    def test_loaded_zm_geometry_and_snapshot_identity_are_stable(self):
        from qgis.PyQt.QtCore import QMetaType
        from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer, QgsWkbTypes
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.integration.survey_intake_storage import (
            load_survey_intake, save_survey_intake)
        from transect_navigator.survey.intake import FieldMappings
        identifiers = []
        expected_wkb = None
        for name in ('first name', 'renamed layer'):
            layer = QgsVectorLayer('PointZM?crs=EPSG:4326', name, 'memory')
            layer.dataProvider().addAttributes([QgsField('time', QMetaType.Type.QString)])
            layer.updateFields()
            feature = QgsFeature(layer.fields())
            feature.setAttributes(['2026-01-01T00:00:00Z'])
            feature.setGeometry(QgsGeometry.fromWkt('POINT ZM (1 2 30 400)'))
            self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
            expected_wkb = bytes(feature.geometry().asWkb())
            intake = self.intake()
            result = intake.import_layer(layer, options=ImportOptions(
                mappings=FieldMappings(timestamp='time')))
            identifiers.append(result.source_id)
            native = next(iter(intake.native_recordings.values())).observations
            saved = next(native.getFeatures()).geometry()
            self.assertEqual(bytes(saved.asWkb()), expected_wkb)
            self.assertTrue(QgsWkbTypes.hasZ(native.wkbType()))
            self.assertTrue(QgsWkbTypes.hasM(native.wkbType()))
        self.assertEqual(identifiers[0], identifiers[1])
        with TemporaryDirectory() as folder:
            output = save_survey_intake(intake, Path(folder), 'zm')
            reopened = load_survey_intake(output, self.survey, project=self.project)
            native = next(iter(reopened.native_recordings.values())).observations
            self.assertEqual(bytes(next(native.getFeatures()).geometry().asWkb()), expected_wkb)
            self.assertTrue(QgsWkbTypes.hasZ(native.wkbType()))
            self.assertTrue(QgsWkbTypes.hasM(native.wkbType()))

    def test_source_recording_observation_ids_and_order_are_stable(self):
        with TemporaryDirectory() as folder:
            path = self.csv(Path(folder)/'stable.csv', self.rows())
            first, second = self.intake(), self.intake()
            first.import_file(path, self.csv_options())
            second.import_file(path, self.csv_options())
            self.assertEqual(set(first.model.sources), set(second.model.sources))
            self.assertEqual(set(first.model.recordings), set(second.model.recordings))
            self.assertEqual(set(first.model.raw_observations), set(second.model.raw_observations))
            rows = list(first.model.raw_observations.values())
            self.assertEqual([row.source_order for row in rows], [1, 2])
            self.assertEqual([row.original_observation_key for row in rows], ['row_2', 'row_3'])

    def test_geojson_and_multilayer_geopackage_inputs(self):
        from qgis.PyQt.QtCore import QMetaType
        from qgis.core import (QgsFeature, QgsField, QgsGeometry, QgsVectorLayer)
        from transect_navigator.integration.export import write_geopackage_layers
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            geojson = folder/'points.geojson'
            geojson.write_text(json.dumps({'type':'FeatureCollection','features':[
                {'type':'Feature','properties':{'time':'2026-01-01T00:00:00Z'},
                 'geometry':{'type':'Point','coordinates':[0,0]}}]}))
            intake = self.intake()
            self.assertEqual(intake.import_file(geojson).status, 'SUCCESS')
            point = self._point_layer(crs='EPSG:4326')
            line = QgsVectorLayer('LineString?crs=EPSG:4326', 'track', 'memory')
            line.dataProvider().addAttributes([QgsField('device', QMetaType.Type.QString)])
            line.updateFields()
            feature = QgsFeature(line.fields()); feature.setAttributes(['D1'])
            feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(0 0, .001 0, .002 0)'))
            self.assertTrue(line.dataProvider().addFeatures([feature])[0])
            gpkg = folder/'multi.gpkg'
            write_geopackage_layers(gpkg, [('fixes', point), ('track', line)],
                                    self.project.transformContext())
            result = intake.import_file(gpkg)
            self.assertEqual(result.status, 'SUCCESS')
            source_recordings = [record for record in intake.model.recordings.values()
                                 if record.source_id == result.source_id]
            self.assertEqual(len(source_recordings), 2)
            self.assertEqual(sorted(r.observation_count for r in source_recordings), [2, 3])

    def test_partial_multilayer_source_retains_success_and_failure(self):
        from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer
        from transect_navigator.integration.export import write_geopackage_layers
        with TemporaryDirectory() as folder:
            point = self._point_layer(crs='EPSG:4326')
            polygon = QgsVectorLayer('Polygon?crs=EPSG:4326', 'unsupported', 'memory')
            feature = QgsFeature(); feature.setGeometry(QgsGeometry.fromWkt(
                'POLYGON((0 0,1 0,1 1,0 0))'))
            self.assertTrue(polygon.dataProvider().addFeatures([feature])[0])
            path = Path(folder)/'partial.gpkg'
            write_geopackage_layers(path, [('points', point), ('polygon', polygon)],
                                    self.project.transformContext())
            intake = self.intake()
            result = intake.import_file(path)
            self.assertEqual(result.status, 'PARTIAL')
            self.assertEqual(intake.model.totals().partial_source_count, 1)
            self.assertEqual(sorted(r.import_status for r in intake.model.recordings.values()),
                             ['FAILED', 'SUCCESS'])
            self.assertEqual(intake.model.totals().observation_count, 2)

    def test_save_reopen_round_trip_and_no_overwrite(self):
        from transect_navigator.integration.survey_intake_storage import (
            load_survey_intake, save_survey_intake)
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            source = self.csv(folder/'source.csv', self.rows())
            intake = self.intake()
            intake.import_file(source, self.csv_options(surveyor='S01'))
            intake.import_file(folder/'missing.gpx')
            output = save_survey_intake(intake, folder, 'saved')
            before = {record.recording_id: [
                (f.attributes(), bytes(f.geometry().asWkb()))
                for f in intake.native_recordings[record.recording_id].observations.getFeatures()]
                for record in intake.model.recordings.values() if record.import_status == 'SUCCESS'}
            reopened = load_survey_intake(output, self.survey, project=self.project)
            self.assertEqual(reopened.model.totals(), intake.model.totals())
            self.assertEqual(set(reopened.model.raw_observations),
                             set(intake.model.raw_observations))
            after = {record.recording_id: [
                (f.attributes(), bytes(f.geometry().asWkb()))
                for f in reopened.native_recordings[record.recording_id].observations.getFeatures()]
                for record in reopened.model.recordings.values() if record.import_status == 'SUCCESS'}
            self.assertEqual(before, after)
            self.assertTrue((output/'survey_intake.gpkg').is_file())
            self.assertEqual((output/'sources'/next(iter(intake.model.sources.values())).archived_reference.split('/')[-1]).read_bytes(),
                             source.read_bytes())
            with self.assertRaisesRegex(ValueError, 'already exists'):
                save_survey_intake(intake, folder, 'saved')
            native = next(iter(reopened.native_recordings.values())).observations
            feature = next(native.getFeatures())
            from qgis.core import QgsGeometry
            native.dataProvider().changeGeometryValues(
                {feature.id(): QgsGeometry.fromWkt('POINT(9 9)')})
            with self.assertRaisesRegex(ValueError, 'geometry or attributes changed'):
                reopened.validate()

    def test_ui_lists_sources_and_edits_explicit_surveyor(self):
        from transect_navigator.ui.survey_recordings_panel import SurveyRecordingsPanel
        with TemporaryDirectory() as folder:
            source = self.csv(Path(folder)/'source.csv', self.rows())
            intake = self.intake()
            intake.import_file(source, self.csv_options())
            panel = SurveyRecordingsPanel(intake)
            self.assertEqual(panel.table.rowCount(), 1)
            self.assertEqual(panel.table.item(0, 0).text(), 'source.csv')
            panel.table.item(0, 7).setText('S02')
            self.assertEqual(next(iter(intake.model.recordings.values())).surveyor_id, 'S02')
            self.assertIn('2 observations', panel.status.text())
            intake.import_file(source, self.csv_options())
            panel.refresh()
            self.assertEqual(panel.table.rowCount(), 2)
            self.assertEqual(panel.table.item(1, 2).text(), 'DUPLICATE')


if __name__ == '__main__':
    unittest.main()
