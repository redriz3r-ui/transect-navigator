"""QGIS-side preconditions for future planar metre-based analysis."""
from qgis.core import Qgis, QgsCoordinateReferenceSystem


def requires_coordinate_epoch(crs: QgsCoordinateReferenceSystem) -> bool:
    """Distinguish an epoch-dependent datum from the ordinary WGS 84 ensemble.

    QGIS can report the WGS 84 ensemble as dynamic because its constituent
    realizations are dynamic. EPSG:4326 and CRSs based on EPSG:6326 describe the
    ensemble, not one time-specific realization. Keep rejecting other dynamic CRSs.
    """
    if not crs.isDynamic():
        return False
    if crs.authid().upper() == 'EPSG:4326':
        return False
    ensemble = crs.datumEnsemble()
    return not (ensemble.isValid() and ensemble.authority().upper() == 'EPSG'
                and ensemble.code() == '6326')


def require_projected_metres(crs: QgsCoordinateReferenceSystem) -> None:
    """Reject invalid, non-projected, or non-metre analysis CRS.

    This does not establish local projection suitability or transform geometry.
    Callers must separately assess distortion and area of use.
    """
    if not crs.isValid():
        raise ValueError("A valid analysis CRS is required")
    if crs.type() != Qgis.CrsType.Projected:
        raise ValueError("Analysis requires a projected CRS")
    if crs.mapUnits() != Qgis.DistanceUnit.Meters:
        raise ValueError("Analysis CRS must use metres")
