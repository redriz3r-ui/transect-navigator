"""One identifier contract for preparation and analysis; raw attributes stay untouched."""
from math import isfinite


def transect_identifier(value: object) -> str:
    """Represent an ID as trimmed text; reject NULL, blank and nonfinite numeric IDs."""
    if value is None:
        raise ValueError('Transect ID is NULL')
    if isinstance(value, float) and not isfinite(value):
        raise ValueError('Transect ID is nonfinite')
    identifier = str(value).strip()
    if not identifier:
        raise ValueError('Transect ID is blank')
    return identifier
