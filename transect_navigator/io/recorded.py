"""Read raw GPX and explicit-coordinate CSV without modifying the source file."""
from __future__ import annotations
import csv
from dataclasses import dataclass
import hashlib
import io
import re
from math import isfinite
from pathlib import Path
from typing import Iterator
import xml.etree.ElementTree as ET
from ..analysis.observations import Issue, MAX_OBSERVATIONS, validate_lonlat

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_FIELDS = 512
MAX_ATTRIBUTE_CELLS = 5_000_000


def validate_attribute_table(field_count: int, row_count: int) -> None:
    """Bound dense snapshot expansion, including sparse GPX extension unions."""
    if field_count > MAX_SOURCE_FIELDS:
        raise ValueError('Input exceeds 512 source fields; reduce fields in a documented copy')
    if field_count * row_count > MAX_ATTRIBUTE_CELLS:
        raise ValueError('Input exceeds 5,000,000 attribute cells; split the survey explicitly')


@dataclass(frozen=True)
class InputPoint:
    source_id: str
    group: str
    coordinate: tuple[float, float]
    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class InputTrack:
    group: str
    point_ids: tuple[str, ...]
    raw_xml: str


@dataclass(frozen=True)
class FileRecording:
    points: tuple[InputPoint, ...]
    tracks: tuple[InputTrack, ...]
    issues: tuple[Issue, ...]
    sha256: str
    source_path: str
    raw_document: str | None = None


def read_bytes(path: Path) -> bytes:
    """Bound memory before parsing user-controlled files."""
    with path.open('rb') as handle:
        content = handle.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('Input file exceeds the 50 MiB ingestion limit')
    return content


def read_gpx(path: Path) -> FileRecording:
    """Read GPX 1.0/1.1 trackpoints, retaining every point XML subtree and segment.

    Unknown device extensions are preserved, never assigned guessed accuracy units.
    Routes are not accepted as recorded tracks. GPX XML order is recorded geometry
    order, not evidence of timestamp chronology.
    """
    content = read_bytes(path)
    try:
        # Restrict to explicit UTF-8 input in v0.1; external entities/DTDs are not needed.
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValueError('GPX ingestion currently requires UTF-8; retain the original if converting a copy') from error
    if '\x00' in text:
        raise ValueError('GPX ingestion currently requires UTF-8 XML')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('GPX DTD/entity declarations are unsupported')
    declaration = re.match(r'<\?xml[^>]*encoding=[\"\']([^\"\']+)', text, re.IGNORECASE)
    if declaration and declaration[1].lower() not in {'utf-8', 'utf8'}:
        raise ValueError('GPX ingestion currently requires a UTF-8 XML declaration')
    try:
        # Bound nesting before subtree serialization or extension traversal.
        depth = 0
        parser = ET.iterparse(io.BytesIO(content), events=('start', 'end'))
        for event, node in parser:
            depth += 1 if event == 'start' else -1
            if depth > 64:
                raise ValueError('GPX nesting exceeds the supported depth of 64')
        root = parser.root
    except ET.ParseError as error:
        raise ValueError(f'Invalid GPX XML: {error}') from error
    version = root.get('version')
    namespace = {'1.0':'http://www.topografix.com/GPX/1/0','1.1':'http://www.topografix.com/GPX/1/1'}.get(version)
    if namespace is None or root.tag != f'{{{namespace}}}gpx':
        raise ValueError('Expected a namespace-qualified GPX 1.0 or 1.1 document')
    def tag(name: str) -> str:
        return f'{{{namespace}}}{name}'
    points, tracks, issues = [], [], []
    for track_index, track in enumerate(root.findall(tag('trk')), 1):
        segments = track.findall(tag('trkseg'))
        if not segments:
            issues.append(Issue('EMPTY_TRACK',f'Track {track_index} has no segments'))
        for segment_index, segment in enumerate(segments, 1):
            group = f'track_{track_index}/segment_{segment_index}'
            point_ids = []
            for index, node in enumerate(segment.findall(tag('trkpt')), 1):
                identifier = f'{group}/point_{index}'
                try:
                    coordinate = (float(node.attrib['lon']),float(node.attrib['lat']))
                    validate_lonlat(*coordinate)
                    if coordinate[0] == 180:
                        raise ValueError("GPX longitude must be less than 180 degrees")
                except (KeyError, ValueError) as error:
                    raise ValueError(f'{identifier}: missing or impossible GPX coordinates') from error
                attrs = {name:node.findtext(tag(name),default='') for name in
                         ('time','ele','hdop','pdop','vdop','sat','fix','name','cmt','desc')}
                extensions = node.find(tag('extensions'))
                if extensions is not None:
                    def leaves(parent: ET.Element, prefix: str) -> None:
                        for number, child in enumerate(parent, 1):
                            key = f'{prefix}/{child.tag}[{number}]'
                            if len(child):
                                leaves(child,key)
                            else:
                                attrs[key] = child.text or ''
                    leaves(extensions,'extensions')
                attrs.update({'track_name':track.findtext(tag('name'),default=''),
                              'raw_point_xml':ET.tostring(node,encoding='unicode')})
                points.append(InputPoint(identifier,group,coordinate,tuple(attrs.items())))
                point_ids.append(identifier)
                if len(points) > MAX_OBSERVATIONS:
                    raise ValueError('GPX exceeds 100,000 observations; split the survey explicitly')
            tracks.append(InputTrack(group,tuple(point_ids),ET.tostring(segment,encoding='unicode')))
            if not point_ids:
                issues.append(Issue('EMPTY_TRACK','Empty GPX segment retained in raw document; no line can be formed.',group))
    if root.findall(tag('rte')) or root.findall(tag('wpt')):
        issues.append(Issue('NON_TRACK_CONTENT', 'Routes/standalone waypoints are retained in the raw document but are not survey observations.'))
    if not points:
        raise ValueError('GPX contains no recorded trackpoints (routes/waypoints are not treated as tracks)')
    return FileRecording(tuple(points),tuple(tracks),tuple(issues),hashlib.sha256(content).hexdigest(),str(path.resolve()),text)


def _csv_reader(content: bytes) -> tuple[tuple[str, ...], Iterator[list[str]]]:
    """Parse a header and body from the same bounded byte snapshot."""
    try:
        reader = csv.reader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True)
        header = next(reader)
    except (UnicodeDecodeError, StopIteration, csv.Error) as error:
        raise ValueError('CSV needs a UTF-8 comma-separated header') from error
    if (not header or any(not name.strip() or '\x00' in name for name in header)
            or len({name.casefold() for name in header}) != len(header)):
        raise ValueError('CSV headers must be nonblank and unique (case insensitive)')
    validate_attribute_table(len(header), 0)
    return tuple(header), reader


def csv_headers(path: Path) -> tuple[str, ...]:
    """Inspect UTF-8 comma-separated headers without guessing CRS or delimiter."""
    return _csv_reader(read_bytes(path))[0]


def read_csv(path: Path, x_field: str, y_field: str) -> FileRecording:
    """Preserve raw cell strings; require explicit XY, with CRS validation in QGIS.

    Source bytes are captured once so coordinates, attributes and the SHA-256 refer
    to the same file contents. Oversize CSV cells fail clearly rather than truncate.
    """
    content = read_bytes(path)
    header, reader = _csv_reader(content)
    if x_field == y_field or x_field not in header or y_field not in header:
        raise ValueError('Choose distinct existing CSV X/longitude and Y/latitude columns')
    points = []
    try:
        for row_number, cells in enumerate(reader, 2):
            validate_attribute_table(len(header), len(points)+1)
            if len(cells) != len(header):
                raise ValueError(f'CSV record {row_number}: expected {len(header)} cells, found {len(cells)}')
            values = dict(zip(header, cells))
            try:
                coordinate = (float(values[x_field]), float(values[y_field]))
                if not all(isfinite(v) for v in coordinate):
                    raise ValueError('nonfinite')
            except ValueError as error:
                raise ValueError(f'CSV record {row_number}: invalid coordinates') from error
            points.append(InputPoint(f'row_{row_number}', 'survey', coordinate, tuple(values.items())))
            if len(points) > MAX_OBSERVATIONS:
                raise ValueError('CSV exceeds 100,000 observations; split the survey explicitly')
    except csv.Error as error:
        raise ValueError(f'Invalid CSV (including unsupported oversized cells): {error}') from error
    if not points:
        raise ValueError('CSV contains no observations')
    return FileRecording(tuple(points), (), (), hashlib.sha256(content).hexdigest(), str(path.resolve()))
