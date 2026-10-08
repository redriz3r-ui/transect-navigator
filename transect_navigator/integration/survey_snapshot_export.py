"""Auditable standard GeoPackage/CSV Survey export with standalone verification.

The GeoPackage is authoritative for records and geometries. JSON is a portable
manifest; CSV files are deliberately flat conveniences, never substitute records.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from qgis.PyQt.QtCore import QMetaType
from qgis.core import (QgsCoordinateReferenceSystem, QgsFeature, QgsField,
    QgsFields, QgsGeometry, QgsMemoryProviderUtils, QgsPointXY, QgsVectorLayer,
    QgsWkbTypes)

from .. import __version__
from ..io.package import package_destination, publish_package
from ..survey.intake import portable_id
from ..survey.model import canonical_json, digest, utc_time
from ..survey.results import STATUSES
from .analyze import _table
from .export import write_geopackage_layers
from .survey_intake_storage import intake_manifest
from .survey_prepare import _native_value, crs_roundtrip_equivalent, validate_navigation_snapshot
from .survey_results import assert_results_current, status_layer
from .survey_review import validate_review_intake

EXPORT_SCHEMA = 'survey-snapshot-1'


def _hash_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _record_layer(name: str, columns: tuple[str, ...], rows: list[dict]) -> QgsVectorLayer:
    """Portable IDs and JSON keep native types/nulls without field-name collisions."""
    return _table(name, tuple((column, QMetaType.Type.QString) for column in (*columns, 'record_json')),
                  [[str(row[column]) if row[column] is not None else None for column in columns] +
                   [canonical_json(row)] for row in rows])


def _geometry_layer(name: str, fields: tuple[str, ...], rows: list[tuple[dict, QgsGeometry | None]],
                    wkb_type: QgsWkbTypes.Type, crs_wkt: str) -> QgsVectorLayer:
    schema = QgsFields()
    for field in (*fields, 'record_json'):
        schema.append(QgsField(field, QMetaType.Type.QString))
    crs = QgsCoordinateReferenceSystem(crs_wkt)
    if not crs.isValid():
        raise ValueError(f'{name}: original/analysis CRS is invalid')
    layer = QgsMemoryProviderUtils.createMemoryLayer(name, schema, wkb_type, crs)
    features = []
    for row, geometry in rows:
        feature = QgsFeature(schema)
        feature.setAttributes([str(row[key]) if row[key] is not None else None for key in fields] + [canonical_json(row)])
        if geometry is not None:
            feature.setGeometry(geometry)
        features.append(feature)
    if features and not layer.dataProvider().addFeatures(features)[0]:
        raise ValueError(f'{name}: could not construct geometry snapshot')
    layer.updateExtents()
    return layer


def _planned_layer(survey) -> QgsVectorLayer:
    first = next((p for p in survey.plans if not p.geometry_is_null), None)
    if first is None:
        wkb = QgsWkbTypes.Type.LineString
    else:
        original = QgsGeometry()
        original.fromWkb(bytes.fromhex(first.geometry_wkb_hex))
        wkb = original.wkbType()
    rows = []
    for plan in survey.plans:
        geometry = None
        if not plan.geometry_is_null:
            geometry = QgsGeometry()
            geometry.fromWkb(bytes.fromhex(plan.geometry_wkb_hex))
        rows.append(({'Survey_ID':survey.survey_id, 'Transect_ID':plan.transect_id,
                      'source_content_key':plan.source_content_key,
                      'original_attributes_json':plan.attributes_json,
                      'official_direction':plan.official_direction,
                      'plan_revision':plan.plan_revision}, geometry))
    return _geometry_layer('planned_transects', ('Survey_ID','Transect_ID'), rows, wkb, survey.source_crs_wkt)


def _table_payloads(survey, intake, run, batch, results, navigation):
    """One canonical record per identity. Membership edges remain explicit rows."""
    review = run.snapshot()
    intake_data = intake_manifest(intake)
    header = asdict(survey)
    del header['plans']
    table = {
        'surveys': (('Survey_ID',), [dict(header, Survey_ID=survey.survey_id)]),
        'plan_manifest': (('Survey_ID','Transect_ID'),
            [dict(asdict(p), Survey_ID=survey.survey_id, Transect_ID=p.transect_id) for p in survey.plans]),
        'surveyors': (('Survey_ID','Surveyor_ID'),
            [{'Survey_ID':survey.survey_id,'Surveyor_ID':key} for key in sorted(
                {r.surveyor_id for r in intake.model.recordings.values() if r.surveyor_id})]),
        'sources': (('Source_ID',),
            [dict(row, Source_ID=row['source_id']) for row in intake_data['sources']]),
        'recordings': (('Recording_ID','Source_ID'),
            [dict(row, Recording_ID=row['recording_id'], Source_ID=row['source_id'])
             for row in intake_data['recordings']]),
        'raw_observation_index': (('Observation_ID','Recording_ID','Source_ID'),
            [dict(row, Observation_ID=row['observation_id'], Recording_ID=row['recording_id'],
                  Source_ID=row['source_id']) for row in intake_data['raw_observations']]),
        'normalized_observations': (('Observation_ID',),
            [dict(row, Observation_ID=row['observation_id']) for row in intake_data['normalized_observations']]),
        'import_attempts': (('Attempt_ID',),
            [dict(row, Attempt_ID=row['attempt_id']) for row in intake_data['import_attempts']]),
        'continuity_segments': (('Segment_ID','Recording_ID'),
            [dict(row, Segment_ID=row['segment_id'], Recording_ID=row['recording_id'])
             for row in review['segments']]),
        'assignment_membership': (('Observation_ID',),
            [dict(row, Observation_ID=row['observation_id']) for row in review['membership']]),
        'assignment_decisions': (('Decision_ID',),
            [dict(row, Decision_ID=row['decision_id']) for row in review['decisions']]),
        'visits': (('Visit_ID','Transect_ID','Recording_ID'),
            [dict(row, Visit_ID=row['visit_id'], Transect_ID=row['transect_id'],
                  Recording_ID=row['recording_id']) for row in review['visits']]),
        'visit_qa': (('Visit_ID',),
            [dict({key:value for key,value in asdict(v).items() if key not in ('samples','events')},
                  Visit_ID=v.visit.visit_id) for v in batch.visits]),
        'transect_qa': (('Transect_ID',),
            [dict(asdict(row), Transect_ID=row.transect_id) for row in batch.transects]),
        'transect_results': (('Transect_ID','Survey_ID'),
            [dict(asdict(row), Transect_ID=row.transect_id, Survey_ID=row.survey_id) for row in results.transects]),
        'survey_summary': (('Survey_ID',), [dict(asdict(results.summary), Survey_ID=survey.survey_id)]),
        'completion_policy': (('Policy_ID',),
            [dict(asdict(results.policy), Policy_ID=results.policy.policy_id)]),
        'evidence_scopes': (('Scope_Revision',),
            [dict(asdict(row), Scope_Revision=str(row.revision)) for row in results.evidence_history]),
    }
    for name, member, key in (('segment_membership','segments','segment_id'),
                              ('decision_membership','decisions','decision_id'),
                              ('visit_membership','visits','visit_id')):
        table[name] = (('Membership_ID','Owner_ID','Ordinal','Observation_ID'),
            [{'Membership_ID':portable_id('MEM',[name,row[key],index,observation_id]),
              'Owner_ID':row[key], 'Ordinal':str(index), 'Observation_ID':observation_id}
             for row in review[member] for index, observation_id in enumerate(row['observation_ids'])])
    if navigation is not None:
        table['navigation_run'] = (('Survey_ID',), [{'Survey_ID':survey.survey_id,
            'spacing_m':navigation.spacing_m,'reverse':navigation.reverse,
            'source_digest':navigation.source_digest,'waypoint_digest':navigation.waypoint_digest,
            'exclusions':navigation.exclusions}])
    return table, review, intake_data


def _qa_layers(batch, results, run):
    """QA points in WGS84; event geometry in its own Visit's metric CRS."""
    qa_rows, point_rows, event_rows, event_layers, null_keys = [], [], [], [], {}
    for visit in batch.visits:
        key = visit.visit.visit_id
        for sample in visit.samples:
            row = dict(asdict(sample), Visit_ID=key, Transect_ID=visit.visit.transect_id,
                       Recording_ID=visit.visit.recording_id, Source_ID=visit.visit.source_id,
                       Observation_ID=sample.observation_id,
                       analysis_crs_wkt=visit.analysis_crs_wkt)
            qa_rows.append(row)
            point_rows.append((row, QgsGeometry.fromPointXY(QgsPointXY(sample.longitude,sample.latitude))))
        if visit.events:
            name = 'qa_event_geometry_' + digest(key)[:16]
            metric_rows = []
            for event in visit.events:
                row = dict(asdict(event), Event_UID=event.event_uid, Visit_ID=key,
                           Transect_ID=visit.visit.transect_id, geometry_layer=name,
                           analysis_crs_wkt=visit.analysis_crs_wkt)
                event_rows.append(row)
                geometry = None
                if event.geometry_wkb_hex is not None:
                    geometry = QgsGeometry()
                    geometry.fromWkb(bytes.fromhex(event.geometry_wkb_hex))
                    if geometry.isNull() or geometry.isEmpty() or QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.GeometryType.LineGeometry:
                        raise ValueError('Moving QA event needs valid LineString geometry')
                metric_rows.append((row, geometry))
            layer = _geometry_layer(name, ('Event_UID','Visit_ID'), metric_rows,
                                    QgsWkbTypes.Type.LineString, visit.analysis_crs_wkt)
            event_layers.append((name, layer))
            null_keys[name] = 'Event_UID'
    point_layer = _geometry_layer('qa_observations', ('Observation_ID','Visit_ID','Transect_ID'),
                                  point_rows, QgsWkbTypes.Type.Point,
                                  QgsCoordinateReferenceSystem('EPSG:4326').toWkt())
    return qa_rows, point_layer, event_rows, event_layers, null_keys


def _safe_source_name(record) -> str:
    original = re.split(r'[/\\]', record.original_name or record.source_file)[-1]
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', original).strip('._')[:80] or 'source'
    return f"{record.sha256}_{safe}"


def _source_manifest(intake) -> list[dict]:
    rows = []
    for source in sorted(intake.model.sources.values(), key=lambda row: row.source_id):
        recorders = [r for r in intake.model.recordings.values() if r.source_id == source.source_id]
        rows.append({'Source_ID':source.source_id, 'Survey_ID':source.survey_id,
            'Original_Name':source.original_name, 'Original_Path':source.original_path,
            'Exported_File':f'sources/{_safe_source_name(source)}' if source.sha256 else '',
            'Original_Bytes_State':'PRESERVED' if source.sha256 else 'UNAVAILABLE_LOADED_LAYER',
            'SHA256':source.sha256 or '', 'Snapshot_Digest':source.snapshot_digest or '',
            'Source_Kind':source.source_kind, 'Format':source.source_format,
            'Import_Status':source.import_status,
            'Surveyor_IDs':','.join(sorted({r.surveyor_id for r in recorders if r.surveyor_id})),
            'Unknown_Surveyor_Recording_Count':sum(r.surveyor_id is None for r in recorders),
            'Warnings_JSON':canonical_json(source.warnings)})
    return rows


def _write_csv(path: Path, rows: list[dict], columns: tuple[str, ...]) -> None:
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def _csv_rows(results, source_rows, metadata):
    summary = results.summary
    counts = dict(summary.status_counts)
    survey_row = {'Survey_ID':results.survey_id, 'Snapshot_ID':metadata['snapshot_id'],
        'Snapshot_State':'CURRENT', 'Planned_Transects':summary.planned_count,
        **{state:counts[state] for state in STATUSES},
        'Planned_Length_m':summary.total_planned_length_m,
        'Known_Planned_Length_m':summary.known_planned_length_m,
        'Unknown_Length_Count':summary.unknown_length_count,
        'Accepted_Track_m':summary.accepted_track_length_m,
        'Known_Track_m':summary.known_track_length_m,
        'Observed_Progress_m':summary.confirmed_progress_m,
        'QA_Corridor_m':summary.confirmed_corridor_m,
        'Project_Progress_pct':summary.progress_lower_bound_percent,
        'Project_QA_Corridor_pct':summary.corridor_lower_bound_percent,
        'Unresolved_Observations':summary.unresolved_observation_count,
        'Ambiguous_Observations':summary.ambiguous_observation_count,
        'Unassigned_Observations':summary.unassigned_observation_count,
        'Processing_State':summary.processing_state}
    rows = []
    for result in results.transects:
        qa, summary_qa = result.qa, result.qa.summary
        deviation = summary_qa.observed_deviation if summary_qa else None
        rows.append({'Survey_ID':results.survey_id,'Transect_ID':result.transect_id,
            'Status':result.final_status,'Status_Basis':'; '.join(result.status_basis),
            'Planned_Length_m':qa.planned_length_m,'Visit_Count':len(qa.visit_ids),
            'Accepted_Observations':qa.accepted_observation_count,
            'Evaluated_Observations':qa.evaluated_observation_count,
            'Accepted_Track_m':summary_qa.recorded_length_m if summary_qa else None,
            'Known_Track_m':qa.known_track_length_m,
            'Progress_m':result.confirmed_progress_m,'QA_Corridor_m':result.confirmed_corridor_m,
            'Progress_pct':qa.progress_support.supported_percent if qa.progress_support else None,
            'QA_Corridor_pct':summary_qa.coverage.supported_percent if summary_qa else None,
            'Mean_Deviation_m':deviation.mean if deviation else None,
            'Median_Deviation_m':deviation.median if deviation else None,
            'RMSE_m':deviation.rmse if deviation else None,'P95_m':deviation.p95 if deviation else None,
            'Maximum_Deviation_m':deviation.maximum if deviation else None,
            'Event_Count':len(qa.event_uids),
            'Unresolved_Count':len(result.unresolved_observation_ids),
            'QA_State':result.qa_state,'Source_IDs':','.join(qa.source_ids),
            'Surveyor_IDs':','.join(value or 'UNKNOWN' for value in qa.surveyor_ids)})
    return survey_row, rows, source_rows


def _validate_relationships(records: dict[str, dict[str, dict]], metadata: dict,
                            geometries: dict[str, dict[str, bytes]]) -> None:
    """Reject orphans and count drift from reopened GeoPackage records."""
    ids = {table:set(rows) for table,rows in records.items()}
    source, recording, raw, normal = (ids[key] for key in
        ('sources','recordings','raw_observation_index','normalized_observations'))
    plan = ids['plan_manifest']
    if len(ids['surveys']) != 1 or len(plan) != len(ids['transect_results']) or plan != ids['transect_results']:
        raise ValueError('Survey/plan/result identity reconciliation failed')
    if raw != normal or raw != ids['assignment_membership']:
        raise ValueError('Raw, normalized and assignment Observation_ID sets differ')
    if sum(len(row['observation_ids']) for row in records['continuity_segments'].values()) != len(raw):
        raise ValueError('Continuity segments do not partition raw observations')
    for row in records['recordings'].values():
        if row['source_id'] not in source:
            raise ValueError('Recording references absent Source_ID')
    for row in records['raw_observation_index'].values():
        if row['source_id'] not in source or row['recording_id'] not in recording or records['recordings'][row['recording_id']]['source_id'] != row['source_id']:
            raise ValueError('Raw observation source/recording provenance is orphaned')
    decision, visit, segments = (ids[key] for key in ('assignment_decisions','visits','continuity_segments'))
    for row in records['assignment_decisions'].values():
        if not set(row['observation_ids']) <= raw:
            raise ValueError('Decision references absent observations')
        if row['transect_id'] is not None and row['transect_id'] not in plan:
            raise ValueError('Decision references absent Transect_ID')
    for row in records['assignment_membership'].values():
        if row['decision_id'] is not None and row['decision_id'] not in decision:
            raise ValueError('Disposition references absent Decision_ID')
        if row['transect_id'] is not None and row['transect_id'] not in plan:
            raise ValueError('Disposition references absent Transect_ID')
    all_segment_members = []
    for row in records['continuity_segments'].values():
        if row['recording_id'] not in recording or not set(row['observation_ids']) <= raw:
            raise ValueError('Segment references absent recording or observation')
        all_segment_members.extend(row['observation_ids'])
    if Counter(all_segment_members) != Counter(raw):
        raise ValueError('Continuity partition has lost or duplicated observations')
    for row in records['visits'].values():
        if row['transect_id'] not in plan or row['recording_id'] not in recording or row['segment_id'] not in segments or row['decision_id'] not in decision:
            raise ValueError('Visit has orphaned plan, recording, segment or decision')
        if not set(row['observation_ids']) <= raw:
            raise ValueError('Visit has orphaned observations')
        for key in row['observation_ids']:
            member = records['assignment_membership'][key]
            if (member['assignment_status'],member['activity_role'],member['transect_id'],member['decision_id']) != ('ASSIGNED','SURVEY',row['transect_id'],row['decision_id']):
                raise ValueError('Visit membership differs from effective review')
    for membership_table, owner_table in (('segment_membership','continuity_segments'),
                                           ('decision_membership','assignment_decisions'),
                                           ('visit_membership','visits')):
        expected_edges = {(owner, str(position), observation_id) for owner,row in records[owner_table].items()
                          for position,observation_id in enumerate(row['observation_ids'])}
        actual_edges = {(row['Owner_ID'],row['Ordinal'],row['Observation_ID'])
                        for row in records[membership_table].values()}
        if len(actual_edges) != len(records[membership_table]) or actual_edges != expected_edges:
            raise ValueError(f'{membership_table}: ordered membership differs from owner records')
    accepted = Counter(key for row in records['visits'].values() for key in row['observation_ids'])
    if accepted != Counter(key for key,row in records['assignment_membership'].items() if row['assignment_status']=='ASSIGNED'):
        raise ValueError('Accepted Visit/assignment membership does not reconcile')
    evaluated = {observation_id for row in records['visit_qa'].values()
                 if row['processing_state'] == 'SUCCESS'
                 for observation_id in row['visit']['observation_ids']}
    if set(records['qa_observations']) != evaluated:
        raise ValueError('Successful Visit observations and QA observation IDs differ')
    for key,row in records['qa_observations'].items():
        if key not in accepted or row['Visit_ID'] not in visit or row['Recording_ID'] not in recording or row['Source_ID'] not in source or row['Transect_ID'] not in plan:
            raise ValueError('QA observation provenance is orphaned')
        parent = records['visits'][row['Visit_ID']]
        original = records['raw_observation_index'][key]
        normal_record = records['normalized_observations'][key]
        if (key not in parent['observation_ids'] or
                (row['Source_ID'],row['Recording_ID'],row['Transect_ID']) !=
                (original['source_id'],original['recording_id'],parent['transect_id']) or
                row['original_key'] != original['original_observation_key'] or
                row['source_order'] != original['source_order'] or
                row['original_timestamp'] != normal_record['original_timestamp']):
            raise ValueError('QA observation source, Visit or timestamp provenance differs from raw data')
    for row in records['qa_events'].values():
        if (row['Visit_ID'] not in visit or row['Transect_ID'] not in plan or
                row['visit_id'] != row['Visit_ID'] or
                records['visits'][row['Visit_ID']]['transect_id'] != row['Transect_ID'] or
                not set(row['result']['source_ids']) <= set(records['visits'][row['Visit_ID']]['observation_ids'])):
            raise ValueError('QA event provenance is orphaned')
        if row['geometry_layer'] not in records or row['Event_UID'] not in records[row['geometry_layer']]:
            raise ValueError('QA event metric geometry is absent')
        if row['analysis_crs_wkt'] != records[row['geometry_layer']][row['Event_UID']]['analysis_crs_wkt']:
            raise ValueError('QA event metric CRS differs from its spatial layer record')
        actual_wkb = geometries[row['geometry_layer']][row['Event_UID']]
        if (actual_wkb.hex() if actual_wkb else None) != row['geometry_wkb_hex']:
            raise ValueError('QA event geometry differs from metric WKB/NULL record')
    for row in records['visit_qa'].values():
        if row['Visit_ID'] not in visit or row['visit']['visit_id'] != row['Visit_ID']:
            raise ValueError('Visit QA has an orphaned Visit')
    if set(records['visit_qa']) != visit:
        raise ValueError('Visit QA count/identity differs from reviewed Visits')
    if set(records['transect_qa']) != plan or set(records['transect_results']) != plan:
        raise ValueError('Not every plan has QA and operational results')
    for key,row in records['plan_manifest'].items():
        if geometries['planned_transects'][key].hex() != row['geometry_wkb_hex']:
            raise ValueError('Official plan geometry differs from plan manifest')
        if geometries['project_status'][key] != geometries['planned_transects'][key]:
            raise ValueError('Project status copy differs from official plan geometry')
    disposition = Counter(row['assignment_status'] for row in records['assignment_membership'].values())
    if (len(source),len(recording),len(raw),disposition['AMBIGUOUS'],disposition['UNASSIGNED']) != (
            metadata['counts']['sources'],metadata['counts']['recordings'],metadata['counts']['raw_observations'],
            metadata['counts']['ambiguous'],metadata['counts']['unassigned']):
        raise ValueError('Intake and disposition totals differ from export metadata')
    if Counter(row['final_status'] for row in records['transect_results'].values()) != Counter(metadata['status_counts']):
        raise ValueError('Operational status counts differ from export metadata')
    survey_summary = next(iter(records['survey_summary'].values()))
    for field in ('planned_count','confirmed_progress_m','confirmed_corridor_m','unresolved_observation_count'):
        if survey_summary[field] != metadata['summary'][field]:
            raise ValueError(f'Survey summary {field} differs from metadata')


def verify_survey_snapshot(folder: Path) -> dict:
    """Standalone, read-only verification; returns the verified metadata manifest."""
    folder = Path(folder)
    metadata = json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    if metadata.get('schema') != EXPORT_SCHEMA or metadata.get('state') != 'CURRENT':
        raise ValueError('Unsupported or non-current Survey export metadata')
    for relative, checksum in metadata['file_sha256'].items():
        path = (folder/relative).resolve()
        if folder.resolve() not in path.parents or not path.is_file() or _hash_file(path) != checksum:
            raise ValueError(f'{relative}: exported file missing or checksum mismatch')
    gpkg = folder/'survey_analysis.gpkg'
    with sqlite3.connect(gpkg.resolve().as_uri()+'?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',) or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Survey GeoPackage integrity check failed')
        registered = {row[0] for row in db.execute('SELECT table_name FROM gpkg_contents')}
    specs = metadata['layers']
    if not set(specs) <= registered:
        raise ValueError('Required Survey GeoPackage layers/tables are missing')
    records, geometries = {}, {}
    for name,spec in specs.items():
        layer = QgsVectorLayer(f'{gpkg}|layername={name}', name, 'ogr')
        if not layer.isValid() or layer.featureCount() != spec['count']:
            raise ValueError(f'{name}: GeoPackage reopen/count verification failed')
        if spec['crs_wkt'] and not crs_roundtrip_equivalent(layer.crs(),QgsCoordinateReferenceSystem(spec['crs_wkt'])):
            raise ValueError(f'{name}: CRS meaning changed')
        if not set(spec['fields']) <= set(layer.fields().names()):
            raise ValueError(f'{name}: expected fields missing')
        if 'record_json' in spec['fields']:
            idfield = spec['key_field']
            values = {}
            for feature in layer.getFeatures():
                key = str(feature[idfield])
                if key in values:
                    raise ValueError(f'{name}: duplicate portable identity')
                record = json.loads(feature['record_json'])
                if not isinstance(record, dict) or any(field not in record for field in spec['key_columns']):
                    raise ValueError(f'{name}: malformed record JSON')
                if any((str(record[field]) if record[field] is not None else None) != feature[field]
                       for field in spec['key_columns']):
                    raise ValueError(f'{name}: key fields disagree with record JSON')
                values[key] = record
            if len(values) != spec['count'] or digest(values) != spec['record_digest']:
                raise ValueError(f'{name}: records changed on GeoPackage round trip')
            records[name] = values
        if spec.get('null_geometry_keys'):
            expected_null = set(spec['null_geometry_keys'])
            actual_null = {feature[spec['key_field']] for feature in layer.getFeatures()
                           if feature.geometry().isNull()}
            if expected_null != actual_null:
                raise ValueError(f'{name}: NULL geometry semantics changed')
        if spec.get('geometry_digest'):
            geoms = {str(feature[spec['key_field']]):bytes(feature.geometry().asWkb())
                     for feature in layer.getFeatures()}
            if digest({key:value.hex() for key,value in geoms.items()}) != spec['geometry_digest']:
                raise ValueError(f'{name}: geometry changed on GeoPackage round trip')
            geometries[name] = geoms
    _validate_relationships(records,metadata,geometries)
    for key,row in records['qa_observations'].items():
        expected = QgsGeometry.fromPointXY(QgsPointXY(row['longitude'],row['latitude']))
        if geometries['qa_observations'][key] != bytes(expected.asWkb()):
            raise ValueError('QA point geometry differs from its preserved observation coordinates')
    status_map = QgsVectorLayer(f'{gpkg}|layername=project_status','project status','ogr')
    for feature in status_map.getFeatures():
        row = records['transect_results'][feature['Transect_ID']]
        if (feature['Survey_ID'] != row['survey_id'] or feature['Status'] != row['final_status'] or
                feature['Policy_ID'] != row['completion_policy_id'] or
                feature['Progress_m'] != row['confirmed_progress_m'] or
                feature['Corridor_m'] != row['confirmed_corridor_m'] or
                feature['Unresolved_Count'] != len(row['unresolved_observation_ids'])):
            raise ValueError('Project status map attributes differ from authoritative results')
    # Native provider fields/FIDs are traces, not portable keys. Reconcile their
    # full geometry/attribute multiset to the authoritative Observation_ID index.
    for recording in records['recordings'].values():
        if recording['import_status'] != 'SUCCESS':
            continue
        name = recording['raw_observation_layer']
        layer = QgsVectorLayer(f'{gpkg}|layername={name}',name,'ogr')
        expected_fields = [item[0] for item in json.loads(recording['original_schema_json'])['fields']]
        if (not layer.isValid() or not set(expected_fields) <= set(layer.fields().names()) or
                not crs_roundtrip_equivalent(layer.crs(),QgsCoordinateReferenceSystem(recording['original_crs_wkt']))):
            raise ValueError(f'{name}: native schema/CRS changed')
        actual = Counter((hashlib.sha256(bytes(feature.geometry().asWkb())).hexdigest(),
                          digest([_native_value(feature[field]) for field in expected_fields]))
                         for feature in layer.getFeatures())
        expected = Counter((row['raw_geometry_digest'],row['raw_attributes_digest'])
                           for row in records['raw_observation_index'].values()
                           if row['recording_id']==recording['recording_id'])
        if actual != expected:
            raise ValueError(f'{name}: native geometry/attributes differ from raw Observation_ID index')
    with (folder/'source_manifest.csv').open(encoding='utf-8',newline='') as stream:
        manifest = list(csv.DictReader(stream))
    if len(manifest) != len(records['sources']) or {row['Source_ID'] for row in manifest} != set(records['sources']):
        raise ValueError('Source manifest does not reconcile to Source registry')
    for row in manifest:
        source = records['sources'][row['Source_ID']]
        linked = [record for record in records['recordings'].values()
                  if record['source_id'] == row['Source_ID']]
        expected_manifest = {
            'Survey_ID':source['survey_id'], 'Original_Name':source['original_name'],
            'Original_Path':source['original_path'], 'SHA256':source['sha256'] or '',
            'Snapshot_Digest':source['snapshot_digest'] or '',
            'Source_Kind':source['source_kind'], 'Format':source['source_format'],
            'Import_Status':source['import_status'],
            'Surveyor_IDs':','.join(sorted({record['surveyor_id'] for record in linked
                                           if record['surveyor_id']})),
            'Unknown_Surveyor_Recording_Count':str(sum(record['surveyor_id'] is None for record in linked)),
            'Warnings_JSON':canonical_json(source['warnings'])}
        if any(row[key] != ('' if value is None else str(value))
               for key,value in expected_manifest.items()):
            raise ValueError('Source manifest provenance disagrees with GeoPackage')
        if row['Original_Bytes_State'] == 'PRESERVED':
            exported = (folder/row['Exported_File']).resolve()
            if folder.resolve() not in exported.parents or _hash_file(exported) != row['SHA256']:
                raise ValueError('Archived original source bytes missing or changed')
        elif row['Original_Bytes_State'] != 'UNAVAILABLE_LOADED_LAYER' or row['Exported_File']:
            raise ValueError('Unknown original source byte state')
    for name,key in (('survey_summary.csv','Survey_ID'),('transect_summary.csv','Transect_ID')):
        with (folder/name).open(encoding='utf-8',newline='') as stream:
            rows = list(csv.DictReader(stream))
        expected = records['survey_summary'] if name.startswith('survey_') else records['transect_results']
        if len(rows) != len(expected) or {row[key] for row in rows} != set(expected):
            raise ValueError(f'{name}: row IDs/counts disagree with GeoPackage')
        for row in rows:
            source = expected[row[key]]
            if name.startswith('survey_'):
                fields = {'Planned_Transects':'planned_count', 'Planned_Length_m':'total_planned_length_m',
                    'Known_Planned_Length_m':'known_planned_length_m', 'Unknown_Length_Count':'unknown_length_count',
                    'Accepted_Track_m':'accepted_track_length_m', 'Known_Track_m':'known_track_length_m',
                    'Observed_Progress_m':'confirmed_progress_m', 'QA_Corridor_m':'confirmed_corridor_m',
                    'Project_Progress_pct':'progress_lower_bound_percent',
                    'Project_QA_Corridor_pct':'corridor_lower_bound_percent',
                    'Unresolved_Observations':'unresolved_observation_count',
                    'Ambiguous_Observations':'ambiguous_observation_count',
                    'Unassigned_Observations':'unassigned_observation_count',
                    'Processing_State':'processing_state'}
                values = {column:source[field] for column,field in fields.items()}
                values.update(dict(source['status_counts']))
                values.update({'Snapshot_ID':metadata['snapshot_id'], 'Snapshot_State':metadata['state']})
            else:
                qa = source['qa']
                summary = qa['summary']
                deviation = summary['observed_deviation'] if summary else None
                values = {'Status':source['final_status'], 'Planned_Length_m':qa['planned_length_m'],
                    'Visit_Count':len(qa['visit_ids']), 'Accepted_Observations':qa['accepted_observation_count'],
                    'Evaluated_Observations':qa['evaluated_observation_count'],
                    'Accepted_Track_m':summary['recorded_length_m'] if summary else None,
                    'Known_Track_m':qa['known_track_length_m'],
                    'Progress_m':source['confirmed_progress_m'], 'QA_Corridor_m':source['confirmed_corridor_m'],
                    'Progress_pct':qa['progress_support']['supported_percent'] if qa['progress_support'] else None,
                    'QA_Corridor_pct':summary['coverage']['supported_percent'] if summary else None,
                    'Mean_Deviation_m':deviation['mean'] if deviation else None,
                    'Median_Deviation_m':deviation['median'] if deviation else None,
                    'RMSE_m':deviation['rmse'] if deviation else None,
                    'P95_m':deviation['p95'] if deviation else None,
                    'Maximum_Deviation_m':deviation['maximum'] if deviation else None,
                    'Event_Count':len(qa['event_uids']), 'Unresolved_Count':len(source['unresolved_observation_ids']),
                    'QA_State':source['qa_state'], 'Survey_ID':source['survey_id'],
                    'Status_Basis':'; '.join(source['status_basis']),
                    'Source_IDs':','.join(qa['source_ids']),
                    'Surveyor_IDs':','.join(value or 'UNKNOWN' for value in qa['surveyor_ids'])}
            if any(row[column] != ('' if value is None else str(value)) for column,value in values.items()):
                raise ValueError(f'{name}: summary values disagree with GeoPackage')
    return metadata


def export_survey_snapshot(results, batch, run, intake, parent: Path, name: str, *, navigation=None,
                           created_utc: str | None = None) -> Path:
    """Export only current Stage 6 results; reject stale dependencies before staging."""
    package_destination(parent,name)
    assert_results_current(results,batch,run,intake,results.policy,results.evidence_history)
    validate_review_intake(intake)
    if navigation is not None:
        if navigation.setup.survey != intake.survey:
            raise ValueError('Navigation belongs to another Survey revision')
        validate_navigation_snapshot(navigation)
    timestamp = utc_time(created_utc)
    snapshot_id = portable_id('SNP',[EXPORT_SCHEMA,results.dependency_fingerprint,timestamp])
    survey = intake.survey
    tables, review, intake_data = _table_payloads(survey,intake,run,batch,results,navigation)
    qa_rows, qa_points, events, event_layers, null_keys = _qa_layers(batch,results,run)
    tables['qa_events'] = (('Event_UID','Visit_ID','Transect_ID'),events)
    tables['analysis_runs'] = (('Batch_ID',),[dict(batch.as_dict(), Batch_ID=batch.batch_id,
                                               review_policy=review['policy'])])
    tables['export_runs'] = (('Snapshot_ID',),[{'Snapshot_ID':snapshot_id,'Survey_ID':survey.survey_id,
        'created_utc':timestamp,'schema':EXPORT_SCHEMA,'plugin_version':__version__,
        'state':'CURRENT','results_dependency':results.dependency_fingerprint,
        'review_revision':batch.review_revision,'qa_dependency':batch.dependency_fingerprint}])
    source_rows = _source_manifest(intake)
    layers = [('planned_transects',_planned_layer(survey)),('project_status',status_layer(results,survey))]
    if navigation is not None:
        layers.append(('navigation_waypoints',navigation.prepared.waypoints))
    for table,(columns,rows) in tables.items():
        layers.append((table,_record_layer(table,columns,rows)))
    layers.append(('qa_observations',qa_points))
    for recording_id,native in sorted(intake.native_recordings.items()):
        recording = intake.model.recordings[recording_id]
        layers.append((recording.raw_observation_layer,native.observations))
        if native.tracks is not None:
            layers.append((recording.raw_track_layer,native.tracks))
    layers.extend(event_layers)
    if len({name.casefold() for name,_ in layers}) != len(layers):
        raise ValueError('Survey export layer names collide')
    null_keys.update({'planned_transects':'Transect_ID','project_status':'Transect_ID'})
    specs = {}
    for layer_name, layer in layers:
        fields = layer.fields().names()
        key_field = ('Transect_ID' if layer_name in ('planned_transects','project_status') else
                     'Observation_ID' if layer_name=='qa_observations' else
                     'Event_UID' if layer_name.startswith('qa_event_geometry_') else
                     'Transect_ID' if layer_name=='plan_manifest' else
                     'Surveyor_ID' if layer_name=='surveyors' else
                     tables[layer_name][0][0] if layer_name in tables else '')
        spec = {'count':layer.featureCount(),'fields':fields,'crs_wkt':layer.crs().toWkt(),
                'key_field':key_field,'key_columns':list(tables[layer_name][0]) if layer_name in tables else []}
        if 'record_json' in fields:
            values = {str(feature[key_field]):json.loads(feature['record_json']) for feature in layer.getFeatures()}
            if len(values) != spec['count']:
                raise ValueError(f'{layer_name}: portable identities collide')
            spec['record_digest'] = digest(values)
        if key_field and (layer_name in ('planned_transects','project_status','qa_observations') or
                          layer_name.startswith('qa_event_geometry_')):
            geometry = {str(feature[key_field]):bytes(feature.geometry().asWkb()).hex() for feature in layer.getFeatures()}
            if len(geometry) != spec['count']:
                raise ValueError(f'{layer_name}: geometry identity collision')
            spec['geometry_digest'] = digest(geometry)
            spec['null_geometry_keys'] = sorted(str(feature[key_field]) for feature in layer.getFeatures() if feature.geometry().isNull())
        specs[layer_name] = spec
    metadata = {'schema':EXPORT_SCHEMA,'software_version':__version__,'snapshot_id':snapshot_id,
        'created_utc':timestamp,'state':'CURRENT','survey_id':survey.survey_id,
        'survey_schema':survey.schema_version,'survey_plan_digest':survey.plan_snapshot_digest,
        'plan_revision':survey.plan_revision,'intake_digest':review['input_digest'],
        'review_run_id':run.run_id,'review_revision':len(run.history),
        'visit_fingerprint':review['qa_input_digest'],'qa_batch_id':batch.batch_id,
        'qa_dependency':batch.dependency_fingerprint,'results_dependency':results.dependency_fingerprint,
        'qa_context_json':batch.context_json,'qa_options':asdict(batch.options),
        'review_policy':review['policy'],'completion_policy':asdict(results.policy),
        'evidence_scope_revision':len(results.evidence_history),
        'counts':{'plans':len(survey.plans),'sources':len(intake.model.sources),
            'recordings':len(intake.model.recordings),'raw_observations':len(intake.model.raw_observations),
            'qa_observations':len(qa_rows),'events':len(events),
            'ambiguous':results.summary.ambiguous_observation_count,
            'unassigned':results.summary.unassigned_observation_count,
            'transitions':dict(batch.disposition_counts)['TRANSITION']},
        'status_counts':dict(results.summary.status_counts),'summary':asdict(results.summary),
        'source_manifest':source_rows,'layers':specs,'file_sha256':{}}
    survey_row,transect_rows,_ = _csv_rows(results,source_rows,metadata)

    def write(stage: Path) -> None:
        stage.joinpath('sources').mkdir()
        for row in source_rows:
            if row['Original_Bytes_State'] != 'PRESERVED':
                continue
            data = intake.source_bytes[row['Source_ID']]
            if hashlib.sha256(data).hexdigest() != row['SHA256']:
                raise ValueError('Original source bytes differ from intake checksum')
            path = stage/row['Exported_File']
            path.write_bytes(data)
            if _hash_file(path) != row['SHA256']:
                raise ValueError('Copied original source bytes failed verification')
        write_geopackage_layers(stage/'survey_analysis.gpkg',layers,intake.project.transformContext(),
            null_geometry_keys=null_keys,crs_equivalent=crs_roundtrip_equivalent)
        _write_csv(stage/'survey_summary.csv',[survey_row],tuple(survey_row))
        _write_csv(stage/'transect_summary.csv',transect_rows,tuple(transect_rows[0]))
        _write_csv(stage/'source_manifest.csv',source_rows,tuple(source_rows[0]) if source_rows else (
            'Source_ID','Survey_ID','Original_Name','Original_Path','Exported_File',
            'Original_Bytes_State','SHA256','Snapshot_Digest','Source_Kind','Format','Import_Status',
            'Surveyor_IDs','Unknown_Surveyor_Recording_Count','Warnings_JSON'))
        (stage/'README.txt').write_text(
            'Transect Navigator reviewed Survey snapshot\n'
            f'Survey_ID: {survey.survey_id}\nSnapshot_ID: {snapshot_id}\nUTC export: {timestamp}\n'
            'State: CURRENT at export; only current reviewed dependencies can be exported.\n\n'
            'survey_analysis.gpkg is authoritative: all official plan geometry/metadata, native raw\n'
            'recording layers, source/observation indexes, normalized working data, continuity,\n'
            'assignment decisions/membership, Visits, QA observations/events, results and run identity.\n'
            'plan_manifest retains original typed attributes; planned_transects is the native-CRS\n'
            'spatial copy. project_status is derived. qa_observations is a WGS84 derived view.\n'
            'qa_event_geometry_* layers are in each Visit local metric CRS; stationary events have\n'
            'SQL NULL geometry. qa_events holds the globally unique event and its metric WKB/CRS.\n'
            'Source bytes in sources/ are exact archived originals when available; a loaded QGIS\n'
            'layer has only an honest native snapshot, never invented device file bytes.\n'
            'source_manifest.csv and metadata.json contain SHA-256 checksums. CSV summaries are\n'
            'convenience views; nested exact records and portable IDs in GeoPackage are authoritative.\n'
            'COMPLETE/INCOMPLETE/REVIEW/NOT_SURVEYED describe operational evidence only.\n'
            'Completion policy: '+canonical_json(asdict(results.policy))+'\n'
            'Accepted track is within-Visit measured distance; observed progress is gap-limited\n'
            'station union; QA corridor adds configurable deviation classes. They are distinct.\n'
            'Ambiguous, unassigned, transition and invalid raw observations remain in the package.\n'
            'Status thresholds are NOT regulatory/scientific compliance standards. GNSS uncertainty\n'
            'is retained separately and can affect apparent deviation. No automated assignment,\n'
            'real-time field navigation, or scientific protocol determination is supplied.\n',
            encoding='utf-8')
        for path in (stage/'survey_analysis.gpkg',stage/'survey_summary.csv',
                     stage/'transect_summary.csv',stage/'source_manifest.csv',stage/'README.txt',
                     *stage.joinpath('sources').iterdir()):
            metadata['file_sha256'][str(path.relative_to(stage))] = _hash_file(path)
        (stage/'metadata.json').write_text(canonical_json(metadata)+'\n',encoding='utf-8')
        verify_survey_snapshot(stage)
        assert_results_current(results,batch,run,intake,results.policy,results.evidence_history)
        validate_review_intake(intake)
    try:
        return publish_package(parent,name,write)
    except (OSError, sqlite3.Error, csv.Error, json.JSONDecodeError) as error:
        raise ValueError(f'Survey snapshot export failed safely: {error}') from error
