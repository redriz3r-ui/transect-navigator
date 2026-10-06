"""Synthetic file ingestion fixtures: no claim of device interoperability."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from transect_navigator.io.recorded import read_gpx, read_csv, csv_headers
from transect_navigator.analysis.observations import Observation, normalize_observations

FIXTURES = Path(__file__).parent / 'fixtures'


class RecordedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def write(self, content, name='survey.gpx'):
        path = self.folder / name
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        return path

    def gpx(self, body, version='1.1'):
        return f'<gpx xmlns="http://www.topografix.com/GPX/{version.replace(".", "/")}" version="{version}" creator="synthetic test">{body}</gpx>'

    def test_gpx_fields_extensions_segments_and_original(self):
        path = FIXTURES / 'recorded_synthetic.gpx'
        before = path.read_bytes()
        result = read_gpx(path)
        self.assertEqual(len(result.points), 4)
        self.assertEqual([len(t.point_ids) for t in result.tracks], [3, 1, 0])
        attrs = dict(result.points[0].attributes)
        for field, value in {'time': '2026-01-01T00:00:02Z', 'ele': '12.50', 'hdop': '0.8',
                             'pdop': '1.2', 'sat': '11', 'fix': '3d'}.items():
            self.assertEqual(attrs[field], value)
        self.assertIn('4.5', [v for k, v in attrs.items() if k.startswith('extensions/')])
        self.assertIn('units="m"', attrs['raw_point_xml'])
        self.assertEqual(result.raw_document.encode(), before)
        self.assertEqual(result.sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(path.read_bytes(), before)
        self.assertIn('EMPTY_TRACK', [i.code for i in result.issues])

    def test_gpx_to_normalization_end_to_end(self):
        source = read_gpx(FIXTURES / 'recorded_synthetic.gpx')
        result = normalize_observations([Observation(p.source_id, p.group, *p.coordinate,
                                dict(p.attributes)['time']) for p in source.points])
        self.assertEqual(result.ordered_groups[0][1], (
            'track_1/segment_1/point_2', 'track_1/segment_1/point_3', 'track_1/segment_1/point_1'))
        self.assertEqual(len(result.observations), 4)
        self.assertIn('SINGLE_OBSERVATION', [i.code for i in result.issues])
        self.assertIn('MISSING_TIMESTAMPS', [i.code for i in result.issues])

    @unittest.skipUnless(shutil.which('xmllint'), 'xmllint unavailable for GPX XSD fixture validation')
    def test_synthetic_gpx_matches_official_schema(self):
        schema = Path(__file__).parent / 'schemas/gpx-1.1.xsd'
        result = subprocess.run(['xmllint', '--nonet', '--noout', '--schema', str(schema),
                                 str(FIXTURES / 'recorded_synthetic.gpx')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gpx_10_synthetic(self):
        result = read_gpx(self.write(self.gpx('<trk><trkseg><trkpt lat="45" lon="-120"/></trkseg></trk>', '1.0')))
        self.assertEqual(result.points[0].coordinate, (-120, 45))

    def test_gpx_empty_or_route_only_rejected(self):
        for body in ['', '<trk/>', '<trk><trkseg/></trk>', '<rte><rtept lat="0" lon="0"/></rte>']:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'no recorded trackpoints'):
                read_gpx(self.write(self.gpx(body)))

    def test_gpx_non_track_content_notice(self):
        result = read_gpx(self.write(self.gpx('<wpt lat="0" lon="0"/><trk><trkseg><trkpt lat="0" lon="0"/></trkseg></trk>')))
        self.assertIn('NON_TRACK_CONTENT', [i.code for i in result.issues])
        self.assertEqual(len(result.points), 1)

    def test_gpx_missing_or_impossible_coordinates_rejected(self):
        for attrs in ['lat="0"', 'lat="91" lon="0"', 'lat="0" lon="180"', 'lat="nan" lon="0"']:
            with self.subTest(attrs=attrs), self.assertRaisesRegex(ValueError, 'coordinates'):
                read_gpx(self.write(self.gpx(f'<trk><trkseg><trkpt {attrs}/></trkseg></trk>')))

    def test_gpx_malformed_namespace_and_entities(self):
        for document in ['<gpx', '<gpx version="1.1"/>', '<!DOCTYPE gpx [<!ENTITY a "x">]><gpx/>']:
            with self.subTest(document=document), self.assertRaises(ValueError):
                read_gpx(self.write(document))

    def test_gpx_encoding_and_depth_limits(self):
        for content in [b'\xff', '<gpx/>'.encode('utf-16'),
                        '<?xml version="1.0" encoding="ISO-8859-1"?><gpx/>',
                        self.gpx('<a>' * 70 + '</a>' * 70)]:
            with self.subTest(content=str(content)[:60]), self.assertRaises(ValueError):
                read_gpx(self.write(content))

    def test_gpx_point_limit(self):
        with patch('transect_navigator.io.recorded.MAX_OBSERVATIONS', 1), self.assertRaises(ValueError):
            read_gpx(FIXTURES / 'recorded_synthetic.gpx')

    def test_csv_raw_strings_quoted_cells_and_order(self):
        path = self.write('lon,lat,time,accuracy,note\n-120,45,2026-01-01T00:00:02Z,004.50,"a,b"\n-119.999,45,2026-01-01T00:00:00Z,,"line\nbreak"\n', 'survey.csv')
        before = path.read_bytes()
        self.assertEqual(csv_headers(path), ('lon','lat','time','accuracy','note'))
        source = read_csv(path, 'lon', 'lat')
        self.assertEqual(dict(source.points[0].attributes)['accuracy'], '004.50')
        self.assertEqual(dict(source.points[1].attributes)['note'], 'line\nbreak')
        self.assertEqual(source.points[1].source_id, 'row_3')
        ordered = normalize_observations([Observation(p.source_id, p.group, *p.coordinate,
                                         dict(p.attributes)['time']) for p in source.points])
        self.assertEqual(ordered.ordered_groups, (('survey', ('row_3', 'row_2')),))
        self.assertEqual(source.sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(before, path.read_bytes())

    def test_csv_reads_one_coherent_snapshot(self):
        content = b'x,y\n1,2\n'
        with patch('transect_navigator.io.recorded.read_bytes', return_value=content) as reader:
            result = read_csv(self.folder / 'not_needed.csv', 'x', 'y')
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(result.sha256, hashlib.sha256(content).hexdigest())

    def test_csv_coordinate_mapping_and_projected_values(self):
        path = self.write('north,east\n5000000,500000\n', 'survey.csv')
        self.assertEqual(read_csv(path, 'east', 'north').points[0].coordinate, (500000, 5000000))
        for xy in [('east','east'), ('unknown','north')]:
            with self.assertRaises(ValueError):
                read_csv(path, *xy)

    def test_csv_invalid_headers(self):
        for content in ['', 'x,x\n', 'X,x\n', 'x, \n', 'x,\x00y\n', b'\xff']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                csv_headers(self.write(content, 'bad.csv'))

    def test_csv_invalid_rows_and_coordinates(self):
        for body in ['', '1\n', '1,2,3\n', 'NaN,2\n', ',2\n', '1,Infinity\n', '1,"unclosed\n']:
            with self.subTest(body=body), self.assertRaises(ValueError):
                read_csv(self.write('x,y\n' + body, 'bad.csv'), 'x', 'y')

    def test_csv_byte_order_mark(self):
        result = read_csv(self.write(b'\xef\xbb\xbfx,y\r\n1,2\r\n', 'bom.csv'), 'x', 'y')
        self.assertEqual(result.points[0].coordinate, (1, 2))

    def test_csv_oversized_cell_fails_clearly(self):
        path = self.write('x,y,note\n1,2,' + 'a' * 200000, 'large.csv')
        with self.assertRaisesRegex(ValueError, 'oversized'):
            read_csv(path, 'x', 'y')

    def test_csv_point_and_file_limits(self):
        path = self.write('x,y\n1,2\n3,4\n', 'many.csv')
        with patch('transect_navigator.io.recorded.MAX_OBSERVATIONS', 1), self.assertRaises(ValueError):
            read_csv(path, 'x', 'y')
        with patch('transect_navigator.io.recorded.MAX_FILE_BYTES', 3), self.assertRaises(ValueError):
            read_csv(path, 'x', 'y')
