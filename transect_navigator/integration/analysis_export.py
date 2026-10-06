"""Capture one completed analysis and publish a self-contained, verified GIS package."""
from __future__ import annotations
import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from qgis.PyQt.QtCore import QMetaType
from qgis.core import NULL, QgsCoordinateTransformContext, QgsProject, QgsVectorLayer, QgsWkbTypes
from .analyze import IngestionResult, _table
from .comparison import ComparisonResult
from .statistics import SummaryResult
from .events import EventResult
from .recorded import snapshot
from .export import write_geopackage_layers
from ..io.analysis_summary import Scalar, completed_summary, write_summary_csv
from ..io.package import publish_package


@dataclass(frozen=True)
class AnalysisExport:
    """Private read-only layer copies; never register these with the QGIS project."""
    layers: tuple[tuple[str, QgsVectorLayer], ...]
    summary: tuple[tuple[str, Scalar], ...]
    analysis_time: str
    context: QgsCoordinateTransformContext


def _scalar_table(name: str, values: dict[str, Scalar]) -> QgsVectorLayer:
    columns = tuple((key, QMetaType.Type.QString if isinstance(value, str) else
        QMetaType.Type.Int if isinstance(value, int) else QMetaType.Type.Double)
        for key, value in values.items())
    return _table(name, columns, [list(values.values())])


def capture_analysis(ingested: IngestionResult, compared: ComparisonResult,
                     summary: SummaryResult, events: EventResult, waypoints: QgsVectorLayer,
                     *, project: QgsProject, analysis_time: str | None = None) -> AnalysisExport:
    """Freeze actual layers and parameters before publication; reject mixed result runs."""
    time = analysis_time or datetime.now(timezone.utc).isoformat()
    qa_metadata = json.loads(compared.observations.customProperty('transect_navigator/provenance'))
    event_metadata = json.loads(events.layer.customProperty('transect_navigator/provenance'))
    summary_metadata = json.loads(summary.table.customProperty('transect_navigator/provenance'))
    waypoint_metadata = json.loads(waypoints.customProperty('transect_navigator/provenance'))
    if (summary_metadata['qa_observation_layer_id'] != compared.observations.id() or
            event_metadata.get('qa_observation_layer_id') != compared.observations.id() or
            qa_metadata.get('raw_observation_layer_id') != ingested.raw_observations.id() or
            qa_metadata.get('planned_snapshot_layer_id') != ingested.planned.id() or
            event_metadata.get('qa_field_mapping') != qa_metadata['qa_field_mapping'] or
            event_metadata.get('planned_id') != summary.summary.transect_id):
        raise ValueError('Analysis export requires results from the same completed comparison')
    # Provenance carries the complete input and algorithm context into the package.
    for key, value in qa_metadata.items():
        if event_metadata.get(key) != value or summary_metadata.get(key) != value:
            raise ValueError('Analysis provenance differs between result layers')
    if (compared.observations.featureCount() != summary.summary.observation_count or
            ingested.raw_observations.featureCount() != summary.summary.observation_count or
            events.layer.featureCount() != len(events.detection.events)):
        raise ValueError('Analysis result counts changed; compare the observations again')
    if waypoint_metadata.get('planned_snapshot_provenance') != json.loads(
            ingested.planned.customProperty('transect_navigator/provenance')):
        raise ValueError('Navigation waypoints do not belong to this planned snapshot')
    features = list(summary.table.getFeatures())
    if len(features) != 1:
        raise ValueError('Analysis export needs exactly one summary record')
    values = {name: None if features[0][name] == NULL else features[0][name]
              for name in summary.table.fields().names()}
    values = completed_summary(values, spacing_m=waypoint_metadata['spacing_m'],
        event_options=event_metadata['event_options'], event_detection=event_metadata['event_detection'],
        analysis_time=time, plugin_version=qa_metadata['plugin_version'])
    layers = [('planned_transect', ingested.planned), ('navigation_waypoints', waypoints),
              ('raw_gps_observations', ingested.raw_observations)]
    if ingested.raw_track is not None:
        layers.append(('raw_track', ingested.raw_track))
    layers.extend([('qa_observations', compared.observations), ('deviation_events', events.layer),
        ('normalized_observations', ingested.normalized), ('ingestion_validation', ingested.validation),
        ('qa_nearest_locations', compared.nearest_points), ('qa_connectors', compared.connectors),
        ('event_validation', events.validation)])
    completed = _scalar_table('qa_summary', values)
    completed.setCustomProperty('transect_navigator/provenance', json.dumps({
        **summary_metadata, 'completed_analysis': values}, sort_keys=True, allow_nan=False))
    layers.append(('qa_summary', completed))
    copies, metadata_rows = [], []
    for name, layer in layers:
        # These are already bounded, validated outputs. Diagnostic tables can have
        # several notices per observation, so the raw-input feature cap is inapplicable.
        copy = snapshot(layer, name, list(layer.getFeatures()))
        copy.setReadOnly(True)
        copies.append((name, copy))
        provenance = json.loads(layer.customProperty('transect_navigator/provenance', '{}'))
        # GPX ingestion retains document-level metadata/extensions in a custom property.
        # OGR does not export custom properties, so explicitly preserve that text here.
        document = layer.customProperty('transect_navigator/raw_gpx_document', None)
        if document is not None:
            provenance['raw_gpx_document'] = document
        origin = layer.customProperty('transect_navigator/track_origin', None)
        if origin is not None:
            provenance['track_origin'] = origin
        metadata_rows.append([name, 1, copy.featureCount(), copy.crs().toWkt(),
            QgsWkbTypes.displayString(copy.wkbType()),
            json.dumps(copy.fields().names(), ensure_ascii=False),
            json.dumps(provenance, sort_keys=True, allow_nan=False),
            layer.customProperty('transect_navigator/data_role', 'derived_qa_qc_data'),
            values['Analysis_Time_UTC'], 'Preserved copy; no reprojection at export'])
    if ingested.raw_track is None:
        metadata_rows.append(['raw_track', 0, 0, '', '', '[]', '{}', 'raw_track',
            values['Analysis_Time_UTC'], 'Unavailable; no track fabricated from unknown chronology'])
    string, integer = QMetaType.Type.QString, QMetaType.Type.Int
    metadata = _table('analysis_metadata', (
        ('Layer_Name',string), ('Included',integer), ('Feature_Count',integer), ('CRS_WKT',string),
        ('Geometry_Type',string), ('Fields_JSON',string), ('Provenance_JSON',string),
        ('Data_Role',string), ('Analysis_Time_UTC',string), ('Notes',string)), metadata_rows)
    metadata.setReadOnly(True)
    copies.append(('analysis_metadata', metadata))
    return AnalysisExport(tuple(copies), tuple(values.items()), values['Analysis_Time_UTC'],
                          QgsCoordinateTransformContext(project.transformContext()))


def export_analysis_package(analysis: AnalysisExport, parent: Path, name: str) -> Path:
    """Publish a new folder only after GeoPackage and CSV round-trip verification."""
    def write(stage: Path) -> None:
        write_geopackage_layers(stage/'analysis.gpkg', list(analysis.layers), analysis.context,
                                null_geometry_keys={'deviation_events': 'Event_ID'})
        write_summary_csv(stage/'qa_summary.csv', dict(analysis.summary))
        (stage/'README.txt').write_text(
            'Transect Navigator completed analysis\n'
            f'Analysis time (UTC): {analysis.analysis_time}\n\n'
            'analysis.gpkg is self-contained. Spatial layers retain their individual CRS.\n'
            'qa_summary holds measurements and run settings; qa_summary.csv is its scalar companion.\n'
            'analysis_metadata holds per-layer CRS WKT, fields, counts and JSON provenance.\n'
            'If raw_track is unavailable, its metadata row explains the omission.\n'
            'Diagnostics and validation tables retain ingestion and chronology notices.\n'
            'Use preserved source-ID fields for joins; GeoPackage row IDs are not source IDs.\n'
            'Raw geometry/attributes are preserved as supplied to the analysis. Retain original device files.\n'
            'NULL summary values are blank in CSV; optional NULL event minima are disabled.\n'
            'CSV strings are exact text. Import IDs/text columns as text, not spreadsheet formulas.\n'
            'QA thresholds are user-configurable, not regulatory or scientific compliance standards.\n'
            'GNSS uncertainty is separate from observed deviation and retains source units.\n'
            'QGIS styles and the Results dialog are not embedded; values and settings are preserved.\n',
            encoding='utf-8')
    try:
        return publish_package(parent, name, write)
    except (OSError, sqlite3.Error, csv.Error) as error:
        raise ValueError(f'Analysis export failed: {error}. Check permissions, space and file locks; '
                         'existing packages were not overwritten.') from error
