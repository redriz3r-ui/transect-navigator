"""Portable scalar summary/settings and lossless CSV serialization."""
import csv
import json
from math import isfinite
from pathlib import Path
from typing import Mapping
from ..qaqc.events import EventOptions
from ..navigation.waypoints import validate_spacing
from .csv_utils import read_generated_csv
from ..analysis.observations import parse_timestamp

Scalar = str | int | float | None


def completed_summary(values: Mapping[str, Scalar], *, spacing_m: float,
                      event_options: Mapping, event_detection: Mapping,
                      analysis_time: str, plugin_version: str) -> dict[str, Scalar]:
    """Append the actual completed-run settings; never recalculate measurements."""
    validate_spacing(spacing_m)
    options = EventOptions(**event_options)
    timestamp, _ = parse_timestamp(analysis_time)
    if timestamp is None:
        raise ValueError('Analysis timestamp requires a valid ISO timestamp with explicit timezone')
    extra = {
        'Waypoint_Spacing_m': spacing_m,
        'Event_Threshold_m': options.threshold_m,
        'Event_Min_Duration_s': options.minimum_duration_s,
        'Event_Min_Station_Span_m': options.minimum_station_span_m,
        'Event_Max_Time_Gap_s': options.maximum_time_gap_s,
        'Event_Max_Spatial_Gap_m': options.maximum_spatial_gap_m,
        'Event_Count': event_detection['event_count'],
        'Event_Excluded_Groups': len(event_detection['excluded_groups']),
        'Event_Detection_JSON': json.dumps(event_detection, sort_keys=True, allow_nan=False),
        'Analysis_Time_UTC': timestamp.isoformat(),
        'Export_Schema_Version': '1',
    }
    if set(extra) & set(values):
        raise ValueError('Summary already contains analysis export fields')
    result = dict(values)
    result.update(extra)
    result['Plugin_Version'] = plugin_version
    # Reject NaN/Infinity and unsupported field values before writing anything.
    csv_record(result)
    return result


def csv_record(values: Mapping[str, Scalar]) -> dict[str, str]:
    """Use full Python float precision, empty NULLs and exact text (including IDs)."""
    if not values or any(not isinstance(key, str) or not key for key in values):
        raise ValueError('Summary needs named columns')
    result = {}
    for key, value in values.items():
        if value is None:
            result[key] = ''
        elif isinstance(value, str):
            result[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not isfinite(value):
                raise ValueError(f'Summary field {key} is not finite')
            result[key] = str(value)
        else:
            raise ValueError(f'Unsupported summary value for {key}')
    return result


def write_summary_csv(path: Path, values: Mapping[str, Scalar]) -> None:
    """Create a UTF-8 CSV exclusively, then reopen and compare all fields exactly."""
    expected = csv_record(values)
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(expected), lineterminator='\n')
        writer.writeheader()
        writer.writerow(expected)
    validate_summary_csv(path, values)


def validate_summary_csv(path: Path, values: Mapping[str, Scalar]) -> None:
    expected = csv_record(values)
    fields, rows = read_generated_csv(path)
    if fields != list(expected) or rows != [expected]:
        raise ValueError('Summary CSV failed round-trip validation')
