"""Full actual QGIS/GDAL workflow; skip explicitly when runtime is unavailable."""
import csv
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.synthetic_survey import SAMPLE,FIELDS,observations


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None,
                    'PyQGIS unavailable; full synthetic GIS/project/GeoPackage workflow not executed')
class SyntheticQgisWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app=start_app()

    def test_complete_csv_survey_to_qgis_project_and_export(self):
        from tools.qgis_synthetic_survey import run_qgis_workflow
        with TemporaryDirectory() as folder:
            run_qgis_workflow(SAMPLE,Path(folder)/'run')

    def test_complete_geojson_survey_to_export(self):
        from tools.qgis_synthetic_survey import run_qgis_workflow
        with TemporaryDirectory() as folder:
            run_qgis_workflow(SAMPLE,Path(folder)/'run',SAMPLE/'gps_observations.geojson')

    def test_reversed_storage_order_preserves_raw_and_restores_chronology(self):
        from tools.qgis_synthetic_survey import run_qgis_workflow
        with TemporaryDirectory() as folder:
            path=Path(folder)/'reversed.csv'
            with path.open('w',newline='',encoding='utf-8') as stream:
                writer=csv.DictWriter(stream,fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(observations()[::-1])
            run_qgis_workflow(SAMPLE,Path(folder)/'run',path)
