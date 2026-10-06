"""Real PyQGIS CRS checks. Missing QGIS is a visible skip, not a fake pass."""
import importlib.util
import unittest

QGIS_AVAILABLE = importlib.util.find_spec('qgis') is not None


@unittest.skipUnless(QGIS_AVAILABLE, 'PyQGIS is not installed in this interpreter')
class CrsRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def test_accept_projected_metres(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.crs import require_projected_metres
        require_projected_metres(QgsCoordinateReferenceSystem('EPSG:32611'))

    def test_reject_unsuitable_types_and_units(self):
        from qgis.core import QgsCoordinateReferenceSystem
        from transect_navigator.integration.crs import require_projected_metres
        for code in ('', 'EPSG:4326', 'EPSG:4978', 'EPSG:2263'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                require_projected_metres(QgsCoordinateReferenceSystem(code))
