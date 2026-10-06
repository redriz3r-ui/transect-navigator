"""WGS84 ellipsoidal distances, direct interpolation and true bearings via QGIS."""
from math import degrees, isfinite
from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsDistanceArea, QgsPointXY
from ..navigation.waypoints import Coordinate


class Wgs84Geodesic:
    """Coordinates are (longitude, latitude); consecutive vertices define geodesics."""

    def __init__(self, context: QgsCoordinateTransformContext) -> None:
        self.measure = QgsDistanceArea()
        self.measure.setSourceCrs(QgsCoordinateReferenceSystem('EPSG:4326'), context)
        if not self.measure.setEllipsoid('WGS84') or not self.measure.willUseEllipsoid():
            raise ValueError('QGIS could not initialize the WGS84 ellipsoid')
        if self.measure.lengthUnits() != Qgis.DistanceUnit.Meters:
            raise ValueError('QGIS ellipsoidal distance units are not metres')

    @staticmethod
    def validate(point: Coordinate) -> None:
        """Reject invalid geographic coordinates and exact poles (undefined north)."""
        lon, lat = point
        if not (isfinite(lon) and isfinite(lat) and -180 <= lon <= 180 and -90 < lat < 90):
            raise ValueError('Invalid WGS84 coordinate or unsupported exact pole')

    def distance(self, start: Coordinate, end: Coordinate) -> float:
        self.validate(start)
        self.validate(end)
        length = self.measure.measureLine(QgsPointXY(*start), QgsPointXY(*end))
        if not isfinite(length) or length < 0:
            raise ValueError('Ellipsoidal distance calculation failed')
        # Scope deliberately excludes global/near-antipodal survey legs.
        if length > 1_000_000:
            raise ValueError('Legs over 1,000 km are unsupported; provide explicit intermediate vertices')
        return length

    def interpolate(self, start: Coordinate, end: Coordinate, distance_m: float) -> Coordinate:
        azimuth = self.measure.bearing(QgsPointXY(*start), QgsPointXY(*end))
        point = self.measure.computeSpheroidProject(QgsPointXY(*start), distance_m, azimuth)
        result = ((point.x() + 180) % 360 - 180, point.y())
        self.validate(result)
        return result

    def bearing(self, start: Coordinate, end: Coordinate) -> float:
        return degrees(self.measure.bearing(QgsPointXY(*start), QgsPointXY(*end))) % 360.0
