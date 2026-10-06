"""Presentation of detached analysis outputs; source layers never enter this service."""
from __future__ import annotations
import json
from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsMemoryProviderUtils,
                       QgsWkbTypes, QgsMarkerSymbol, QgsLineSymbol, QgsRuleBasedRenderer,
                       QgsSingleSymbolRenderer, QgsVectorLayer, QgsProject, QgsLayerTreeGroup)
from .analyze import IngestionResult
from .comparison import ComparisonResult
from .statistics import SummaryResult
from .events import EventResult
from .prepare import prepare_navigation
from .recorded import write_verified
from ..presentation.results import class_rules, quoted_field, NOTICE


def navigation_for_result(planned: QgsVectorLayer, id_field: str, spacing_m: float,
                          project: QgsProject) -> QgsVectorLayer:
    """Generate XY navigation from a private copy, retaining Z/M in the displayed plan."""
    flat = QgsMemoryProviderUtils.createMemoryLayer('Navigation input', planned.fields(),
        QgsWkbTypes.LineString, planned.crs())
    features = []
    for source in planned.getFeatures():
        geometry = source.geometry()
        if (geometry.isNull() or geometry.isEmpty() or
                QgsWkbTypes.flatType(geometry.wkbType()) != QgsWkbTypes.LineString):
            raise ValueError('Result navigation requires a nonempty single-part straight-segment line')
        feature = QgsFeature(flat.fields())
        feature.setAttributes(source.attributes())
        feature.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(vertex) for vertex in geometry.vertices()]))
        features.append(feature)
    write_verified(flat, flat.fields(), features)
    result = prepare_navigation(flat, id_field, spacing_m, project=project).waypoints
    result.setReadOnly(True)
    result.setCustomProperty('transect_navigator/waypoint_spacing_m', spacing_m)
    result.setCustomProperty('transect_navigator/data_role', 'navigation_waypoints')
    metadata = json.loads(result.customProperty('transect_navigator/provenance'))
    metadata['planned_snapshot_provenance'] = json.loads(
        planned.customProperty('transect_navigator/provenance', '{}'))
    metadata['visualization_input'] = 'private XY copy of the selected official planned snapshot; original direction'
    result.setCustomProperty('transect_navigator/provenance', json.dumps(metadata, sort_keys=True))
    return result


def present_fields(layer: QgsVectorLayer, names: tuple[str, ...]) -> None:
    """Prioritize useful fields in the native attribute table; retain every raw field."""
    for name in names:
        index = layer.fields().indexFromName(name)
        if index >= 0:
            layer.setFieldAlias(index, name.removeprefix('QA_').replace('_', ' '))
    available = [name for name in names if layer.fields().indexFromName(name) >= 0]
    if available:
        layer.setDisplayExpression(quoted_field(available[0]))
    config = layer.attributeTableConfig()
    config.update(layer.fields())
    rank = {name: index for index, name in enumerate(available)}
    columns = config.columns()
    columns.sort(key=lambda column: rank.get(column.name, len(rank)))
    config.setColumns(columns)
    layer.setAttributeTableConfig(config)


def style_qa(layer: QgsVectorLayer, thresholds: tuple[float, float, float]) -> None:
    mapping = json.loads(layer.customProperty('transect_navigator/provenance'))['qa_field_mapping']
    field = mapping['QA_Deviation_m']
    root = QgsRuleBasedRenderer.Rule(None)
    for rule in class_rules(field, thresholds):
        symbol = QgsMarkerSymbol.createSimple({'name': rule.shape, 'color': rule.color,
            'outline_color': '#333333', 'outline_width': '0.2', 'size': '2.8'})
        root.appendChild(QgsRuleBasedRenderer.Rule(symbol, filterExp=rule.expression,
            label=rule.label, description=NOTICE))
    root.appendChild(QgsRuleBasedRenderer.Rule(QgsMarkerSymbol.createSimple(
        {'color': '#777777', 'name': 'cross', 'size': '3'}),
        label='Missing / invalid deviation', elseRule=True))
    layer.setRenderer(QgsRuleBasedRenderer(root))
    present_fields(layer, tuple(mapping[name] for name in (
        'QA_Transect_ID', 'QA_Station_m', 'QA_Deviation_m', 'QA_Signed_m',
        'QA_Time_UTC', 'QA_Accuracy_Raw', 'QA_Sign_Status', 'QA_Progress_pct')))
    for name, alias in (('QA_Signed_m', 'Signed deviation (m; + left / − right)'),
                        ('QA_Accuracy_Raw', 'Reported GNSS accuracy (source units)'),
                        ('QA_Station_m', 'Distance along planned transect (m)')):
        layer.setFieldAlias(layer.fields().indexFromName(mapping[name]), alias)
    layer.setCustomProperty('transect_navigator/class_notice', NOTICE)
    layer.setCustomProperty('transect_navigator/visualization_thresholds_m', json.dumps(thresholds))


def publish_analysis(ingested: IngestionResult, comparison: ComparisonResult,
                     summary: SummaryResult, events: EventResult, *, id_field: str,
                     spacing_m: float, project: QgsProject,
                     waypoints: QgsVectorLayer | None = None) -> QgsLayerTreeGroup:
    """Style fresh result copies and publish atomically into a uniquely named group."""
    if waypoints is None:
        waypoints = navigation_for_result(ingested.planned, id_field, spacing_m, project)
    principal = [('Deviation Events', events.layer), ('QA Observations', comparison.observations),
        ('Raw GPS Observations', ingested.raw_observations), ('Navigation Waypoints', waypoints)]
    if ingested.raw_track is not None:
        principal.append(('Raw GPS Track', ingested.raw_track))
    principal.append(('Planned Transect', ingested.planned))
    diagnostics = [ingested.normalized, ingested.validation, comparison.nearest_points,
                   comparison.connectors, events.validation]
    all_layers = [layer for _, layer in principal] + [summary.table] + diagnostics
    if any(project.mapLayer(layer.id()) is not None for layer in all_layers):
        raise ValueError('Presentation requires unpublished result copies')
    style_qa(comparison.observations, summary.summary.options.thresholds_m)
    for name, layer in principal:
        layer.setName(name)
        if layer is comparison.observations:
            continue
        if name in ('Planned Transect', 'Raw GPS Track', 'Deviation Events'):
            color, width = {'Planned Transect': ('#222222', '0.6'),
                'Raw GPS Track': ('#888888', '0.35'), 'Deviation Events': ('#AA3377', '1.4')}[name]
            symbol = QgsLineSymbol.createSimple({'line_color': color, 'line_width': width})
        else:
            symbol = QgsMarkerSymbol.createSimple({'name': 'circle',
                'color': '#FFFFFF' if name == 'Navigation Waypoints' else '#999999',
                'outline_color': '#444444', 'size': '1.6'})
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    present_fields(waypoints, ('Waypoint_ID', 'Sequence', 'Distance_Along_m', 'Bearing_Next_deg'))
    present_fields(events.layer, ('Event_ID', 'Transect_ID', 'Start_Distance_Along',
        'End_Distance_Along', 'Event_Length', 'Start_Time', 'End_Time', 'Duration',
        'Mean_Deviation', 'Maximum_Deviation', 'Mean_GNSS_Accuracy', 'Observation_Count', 'Geometry_Status'))
    root = project.layerTreeRoot()
    base = f'Transect Navigator — {summary.summary.transect_id}'
    name, suffix = base, 2
    while root.findGroup(name) is not None:
        name, suffix = f'{base} ({suffix})', suffix + 1
    group = root.addGroup(name)
    try:
        for _, layer in principal:
            if project.addMapLayer(layer, False) is None:
                raise ValueError(f'Could not add {layer.name()} to the project')
            group.addLayer(layer)
        if project.addMapLayer(summary.table, False) is None:
            raise ValueError('Could not add QA Summary to the project')
        group.addLayer(summary.table)
        debug = group.addGroup('Diagnostics — normalized data and nearest locations')
        for layer in diagnostics:
            if project.addMapLayer(layer, False) is None:
                raise ValueError(f'Could not add {layer.name()} to the project')
            debug.addLayer(layer)
        debug.setItemVisibilityChecked(False)
        debug.setExpanded(False)
    except Exception:
        project.removeMapLayers([layer.id() for layer in all_layers])
        root.removeChildNode(group)
        raise
    return group
