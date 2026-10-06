"""Real file serialization, independent read-back, GPX XSD and publication tests."""
import csv
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from transect_navigator.navigation.waypoints import Transect, Waypoint
from transect_navigator.io.exchange import (GPX_NS, TN_NS, WAYPOINT_FIELDS,
    write_exchange_files, validate_exchange_files)
from transect_navigator.io.package import publish_package, package_destination

SCHEMA = Path(__file__).parent/'schemas/gpx-1.1.xsd'


def fixture():
    """Explicit geographic records: serializer tests do not claim geodesic validation."""
    identifier = 'T04 & "Wetland", café'
    plan = Transect(identifier, ((-120.0,40.0),(-119.999,40.0)))
    rows = (Waypoint(identifier,identifier+'_001',1,0.0,85.0,plan.vertices[0],89.9997),
            Waypoint(identifier,identifier+'_002',2,85.0,85.0,plan.vertices[1],None))
    return (plan,), rows


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.plans, self.rows = fixture()

    def write(self, formats={'CSV','GeoJSON','GPX'}):
        return write_exchange_files(self.folder,self.plans,self.rows,formats)

    def test_all_formats_read_back(self):
        paths = self.write()
        self.assertEqual(len(paths),5)
        validate_exchange_files(self.folder,self.plans,self.rows,{'CSV','GeoJSON','GPX'})

    def test_csv_values_quoting_coordinates_and_blank_bearing(self):
        self.write({'CSV'})
        with (self.folder/'navigation_waypoints.csv').open(newline='',encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(tuple(reader.fieldnames),WAYPOINT_FIELDS)
            rows = list(reader)
        self.assertEqual(rows[0]['Transect_ID'],self.plans[0].transect_id)
        self.assertEqual(rows[0]['Sequence'],'1')
        self.assertEqual(rows[1]['Bearing_Next_deg'],'')
        self.assertEqual(float(rows[0]['Longitude']),-120)
        self.assertEqual(float(rows[0]['Latitude']),40)
        with (self.folder/'planned_transects.csv').open(newline='',encoding='utf-8') as handle:
            plan = next(csv.DictReader(handle))
        self.assertEqual(plan['CRS'],'EPSG:4326')
        self.assertTrue(plan['WKT'].startswith('LINESTRING (-120 40,'))

    def test_geojson_standard_geometry_and_attributes(self):
        self.write({'GeoJSON'})
        document=json.loads((self.folder/'navigation_waypoints.geojson').read_text())
        self.assertEqual(document['type'],'FeatureCollection')
        self.assertNotIn('crs',document)
        self.assertEqual(document['features'][0]['geometry'], {'type':'Point','coordinates':[-120,40]})
        self.assertIsNone(document['features'][1]['properties']['Bearing_Next_deg'])
        self.assertEqual(document['features'][1]['properties']['Distance_Along_m'],85)
        plans=json.loads((self.folder/'planned_transects.geojson').read_text())
        self.assertEqual(plans['features'][0]['geometry']['type'],'LineString')

    def test_gpx_routes_tracks_extensions_and_no_fake_observations(self):
        self.write({'GPX'})
        root=ET.parse(self.folder/'navigation.gpx').getroot()
        ns={'g':GPX_NS,'tn':TN_NS}
        from transect_navigator import __version__
        self.assertEqual(root.attrib,{'version':'1.1','creator':f'Transect Navigator {__version__}'})
        self.assertEqual([node.tag.split('}')[1] for node in root],['metadata','rte','trk'])
        points=root.findall('g:rte/g:rtept',ns)
        self.assertEqual([p.findtext('g:name',namespaces=ns) for p in points], [r.waypoint_id for r in self.rows])
        self.assertEqual(points[0].findtext('g:extensions/tn:Sequence',namespaces=ns),'1')
        self.assertEqual(points[0].findtext('g:extensions/tn:Bearing_Next_deg',namespaces=ns),'89.9997')
        self.assertIsNone(points[-1].find('g:extensions/tn:Bearing_Next_deg',ns))
        self.assertFalse(root.findall('.//g:time',ns))
        self.assertFalse(root.findall('.//g:ele',ns))
        self.assertEqual(len(root.findall('g:trk/g:trkseg/g:trkpt',ns)),2)

    @unittest.skipUnless(shutil.which('xmllint'), 'xmllint not installed; GPX XSD check unavailable')
    def test_gpx_validates_against_official_xsd(self):
        self.write({'GPX'})
        result=subprocess.run(['xmllint','--nonet','--noout','--schema',str(SCHEMA),str(self.folder/'navigation.gpx')],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('xmllint'), 'xmllint not installed')
    def test_gpx_positive_180_and_near_boundary_are_schema_valid(self):
        self.plans=(Transect('Boundary',((180.0,0),(179.99999999999997,0))),)
        self.rows=(Waypoint('Boundary','Boundary_001',1,0,1,(180.0,0),270),
                   Waypoint('Boundary','Boundary_002',2,1,1,(179.99999999999997,0),None))
        self.write({'GPX'})
        validate_exchange_files(self.folder,self.plans,self.rows,{'GPX'})
        result=subprocess.run(['xmllint','--nonet','--noout','--schema',str(SCHEMA),str(self.folder/'navigation.gpx')],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_multiple_transects_sequence_and_deterministic_bytes(self):
        extra=Transect('A',((-121,40),(-121,41)))
        self.plans=(*self.plans,extra)
        self.rows=(*reversed(self.rows),Waypoint('A','A_002',2,50,50,(-121,41),None),
                   Waypoint('A','A_001',1,0,50,(-121,40),0))
        paths=self.write()
        first={p.name:p.read_bytes() for p in paths}
        self.plans=tuple(reversed(self.plans))
        self.rows=tuple(reversed(self.rows))
        self.write()
        self.assertEqual(first,{p.name:p.read_bytes() for p in paths})
        validate_exchange_files(self.folder,self.plans,self.rows,{'CSV','GeoJSON','GPX'})

    def test_bad_coordinate_control_id_sequence_and_nan_rejected(self):
        variants=[replace(self.rows[0],coordinate=(181,40)),replace(self.rows[0],waypoint_id='bad\x00id'),
                  replace(self.rows[0],distance_along_m=float('nan')),replace(self.rows[0],sequence=3),
                  replace(self.rows[0],bearing_next_deg=float('inf'))]
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                write_exchange_files(self.folder,self.plans,(bad,self.rows[1]),{'GPX'})
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_long_planned_wkt_csv_roundtrip_restores_field_limit(self):
        plan = self.plans[0]
        vertices = tuple((-120 + i/10_000_000,40.0) for i in range(20_001))
        self.plans = (replace(plan, vertices=vertices),)
        self.rows = (replace(self.rows[0],coordinate=vertices[0]),
                     replace(self.rows[1],coordinate=vertices[-1]))
        previous = csv.field_size_limit()
        self.write({'CSV'})
        self.assertGreater((self.folder/'planned_transects.csv').stat().st_size,131072)
        validate_exchange_files(self.folder,self.plans,self.rows,{'CSV'})
        self.assertEqual(csv.field_size_limit(),previous)

    def test_unknown_format_and_empty_input(self):
        with self.assertRaises(ValueError):
            self.write({'KML'})
        with self.assertRaises(ValueError):
            write_exchange_files(self.folder,(),(),{'CSV'})

    def test_tampered_export_detected(self):
        self.write({'CSV','GeoJSON','GPX'})
        path=self.folder/'navigation_waypoints.csv'
        path.write_text(path.read_text().replace('89.9997','42'))
        with self.assertRaisesRegex(ValueError,'CSV waypoint'):
            validate_exchange_files(self.folder,self.plans,self.rows,{'CSV'})
        self.write({'CSV'})
        path=self.folder/'planned_transects.csv'
        path.write_text(path.read_text().replace('LINESTRING (-120 40','LINESTRING (-100 40'))
        with self.assertRaisesRegex(ValueError,'CSV transect'):
            validate_exchange_files(self.folder,self.plans,self.rows,{'CSV'})
        path=self.folder/'navigation.gpx'
        path.write_text(path.read_text().replace('89.9997','42'))
        with self.assertRaisesRegex(ValueError,'GPX attribute'):
            validate_exchange_files(self.folder,self.plans,self.rows,{'GPX'})


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent=Path(self.temp.name)

    def test_success_and_no_overwrite(self):
        def writer(folder):
            (folder/'data.csv').write_text('x,y\n1,2\n')
        result=publish_package(self.parent,'Survey 01',writer)
        self.assertEqual((result/'data.csv').read_text(),'x,y\n1,2\n')
        with self.assertRaises(ValueError):
            publish_package(self.parent,'Survey 01',writer)
        self.assertEqual([p.resolve() for p in self.parent.iterdir()],[result])

    def test_failed_writer_leaves_no_package_or_staging(self):
        def writer(folder):
            (folder/'partial.csv').write_text('incomplete')
            raise OSError('disk full')
        with self.assertRaises(OSError):
            publish_package(self.parent,'Survey',writer)
        self.assertEqual(list(self.parent.iterdir()),[])

    def test_publish_failure_removes_only_empty_reservation(self):
        operation = 'os.rename' if os.name == 'nt' else 'os.replace'
        with patch(f'transect_navigator.io.package.{operation}',side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                publish_package(self.parent,'Survey',lambda folder:(folder/'data.csv').write_text('x'))
        self.assertEqual(list(self.parent.iterdir()),[])

    def test_concurrent_destination_is_preserved(self):
        def writer(folder):
            (folder/'data.csv').write_text('new')
            (self.parent/'Survey').mkdir()
            (self.parent/'Survey'/'existing.csv').write_text('keep')
        with self.assertRaises(FileExistsError):
            publish_package(self.parent,'Survey',writer)
        self.assertEqual((self.parent/'Survey'/'existing.csv').read_text(),'keep')

    def test_bad_names_missing_parent_and_symlink(self):
        for name in ('','../outside','/absolute','a/b','a\\b','.','CON','name.'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package_destination(self.parent,name)
        with self.assertRaises(ValueError):
            package_destination(self.parent/'missing','Survey')
        (self.parent/'link').symlink_to(self.parent/'missing')
        with self.assertRaises(ValueError):
            package_destination(self.parent,'link')
