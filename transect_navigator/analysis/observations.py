"""Pure ingestion validation. Storage order is never presented as chronology."""
from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
import re
from typing import Sequence

MAX_OBSERVATIONS = 100_000


class DataRole(str, Enum):
    PLANNED_TRANSECT = 'planned_transect'
    RAW_OBSERVATIONS = 'raw_gps_observations'
    RAW_TRACK = 'raw_track'
    DERIVED = 'derived_qa_qc_data'


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    group: str = ''
    source_id: str = ''


@dataclass(frozen=True)
class Observation:
    """Working WGS84 coordinates; original geometry/attributes remain in raw layers."""
    source_id: str
    group: str
    longitude: float
    latitude: float
    timestamp: str | None = None
    accuracy: str | None = None


@dataclass(frozen=True)
class NormalizedObservation:
    raw: Observation
    timestamp_utc: str | None
    sequence: int | None
    time_status: str


@dataclass(frozen=True)
class Normalization:
    observations: tuple[NormalizedObservation, ...]
    ordered_groups: tuple[tuple[str, tuple[str, ...]], ...]
    issues: tuple[Issue, ...]


def validate_lonlat(longitude: float, latitude: float) -> None:
    """Validate geographic degrees, never wrap/clamp impossible input values."""
    if not (isfinite(longitude) and isfinite(latitude) and -180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise ValueError('Impossible WGS84 longitude/latitude (expected -180…180 and -90…90)')


def parse_timestamp(value: str | None) -> tuple[datetime | None, str]:
    """Accept explicit-offset ISO datetimes to microsecond precision; never infer a timezone.

    Numeric epochs, date-only values, naive local times, leap seconds and finer
    fractional precision remain raw but are not used to manufacture chronology.
    """
    if value is None or not str(value).strip():
        return None, 'missing'
    text = str(value).strip()
    if not re.fullmatch(r'\d{4}-\d\d-\d\d[Tt ]\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:[Zz]|[+-]\d\d:\d\d)', text):
        return None, 'invalid_or_timezone_missing'
    if text.endswith(('-00:00', '+24:00', '-24:00')):
        return None, 'unknown_or_invalid_offset'
    try:
        # datetime accepts offsets such as +01:99; explicitly reject those.
        if text[-6:-5] in ('+', '-') and (int(text[-5:-3]) > 23 or int(text[-2:]) > 59):
            return None, 'invalid_offset'
        parsed = datetime.fromisoformat(text.replace('z','Z').replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc), 'valid'
    except (ValueError, OverflowError):
        return None, 'invalid_timestamp'


def normalize_observations(observations: Sequence[Observation]) -> Normalization:
    """Sort chronologically only for complete, unique timestamps within each group.

    In ambiguous groups, every sequence is NULL, including points with valid times.
    Source-ID sorting is just stable presentation; it is not a fabricated time order.
    Groups are never connected to one another.
    """
    if not observations:
        raise ValueError('No GPS observations were found')
    if len(observations) > MAX_OBSERVATIONS:
        raise ValueError(f'Input exceeds {MAX_OBSERVATIONS:,} observations; split the survey explicitly')
    ids = [o.source_id for o in observations]
    if len(set(ids)) != len(ids):
        raise ValueError('Observation source identifiers must be unique')
    groups: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        if not isinstance(observation.source_id, str) or not observation.source_id.strip():
            raise ValueError('Observation source identifiers must be nonblank strings')
        if not isinstance(observation.group, str) or not observation.group.strip():
            raise ValueError('Observation groups must be nonblank strings')
        validate_lonlat(observation.longitude, observation.latitude)
        groups[observation.group].append(observation)
    issues: list[Issue] = []
    result: list[NormalizedObservation] = []
    ordered_groups = []
    for group, members in sorted(groups.items()):
        parsed = {o.source_id: parse_timestamp(o.timestamp) for o in members}
        times = [value for value,status in parsed.values() if value is not None]
        duplicates = {time for time,count in Counter(times).items() if count > 1}
        missing = sum(status == 'missing' for _,status in parsed.values())
        invalid = len(members) - len(times) - missing
        if missing:
            issues.append(Issue('MISSING_TIMESTAMPS', f'{missing} observations have no timestamp; chronology is undetermined.', group))
        if invalid:
            issues.append(Issue('INVALID_TIMESTAMPS', f'{invalid} timestamps lack a valid explicit timezone/ISO time; raw values are retained.', group))
        if duplicates:
            issues.append(Issue('DUPLICATE_TIMESTAMPS', f'{len(duplicates)} repeated instants; no tie-breaking chronology is invented.', group))
        reliable = len(times) == len(members) and not duplicates
        ordered = sorted(members, key=lambda o: parsed[o.source_id][0]) if reliable else sorted(members,key=lambda o:o.source_id)
        if len(members) == 1:
            issues.append(Issue('SINGLE_OBSERVATION', 'One observation cannot define a track segment.', group))
        if reliable and len(members) >= 2:
            ordered_groups.append((group, tuple(o.source_id for o in ordered)))
        for index, observation in enumerate(ordered, 1):
            instant, status = parsed[observation.source_id]
            if instant in duplicates:
                status = 'duplicate'
            utc = instant.isoformat(timespec='microseconds').replace('+00:00','Z') if instant else None
            result.append(NormalizedObservation(observation, utc, index if reliable else None, status))
    return Normalization(tuple(result),tuple(ordered_groups),tuple(issues))


def definitely_far(anchor_distance_m: float, planned_length_m: float, threshold_m: float) -> bool:
    """Conservative triangle-inequality input screen; not a deviation measurement.

    A geodesic polyline of length L lies within L of its first vertex. Thus distance
    to anchor minus L is a lower bound on distance to the entire planned path.
    """
    if not all(isfinite(v) and v >= 0 for v in (anchor_distance_m,planned_length_m,threshold_m)) or threshold_m == 0:
        raise ValueError('Distance-screen values must be finite and threshold positive')
    return anchor_distance_m - planned_length_m > threshold_m
