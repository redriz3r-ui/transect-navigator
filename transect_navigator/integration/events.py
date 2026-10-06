"""Detached derived event geometry and explicit evaluation diagnostics."""
from dataclasses import asdict, dataclass
import json
from .. import __version__
from qgis.PyQt.QtCore import QMetaType
from qgis.core import (Qgis,QgsCoordinateReferenceSystem,QgsFeature,QgsField,QgsFields,
                       QgsGeometry,QgsMemoryProviderUtils,QgsPointXY,QgsProject,QgsVectorLayer)
from .analyze import IngestionResult, _table
from .comparison import ComparisonResult, _strict_transform, _project
from .recorded import write_verified
from ..qaqc.statistics import SummaryObservation
from ..qaqc.events import EventOptions, EventDetection, EventObservation, detect_events


@dataclass(frozen=True)
class EventResult:
    detection: EventDetection
    layer: QgsVectorLayer
    validation: QgsVectorLayer


def detect_comparison_events(ingested: IngestionResult, compared: ComparisonResult, *,
                             options: EventOptions = EventOptions(),
                             project: QgsProject | None = None) -> EventResult:
    """Event lines connect participating raw fixes in the QA frame, never nearest points."""
    project = project if project is not None else QgsProject.instance()
    metadata = json.loads(compared.observations.customProperty('transect_navigator/provenance'))
    observations = ingested.ordering.observations
    if len(observations) != len(compared.rows):
        raise ValueError('Event detection needs matching ingestion/comparison records')
    id_field = metadata['qa_field_mapping']['QA_Source_ID']
    if [str(f[id_field]) for f in compared.observations.getFeatures()] != [o.raw.source_id for o in observations]:
        raise ValueError('Comparison order changed; compare the observations again')
    crs = QgsCoordinateReferenceSystem()
    if not crs.createFromWkt(metadata['analysis_crs_wkt']):
        raise ValueError('Event detection requires the comparison metre CRS')
    transform = _strict_transform(QgsCoordinateReferenceSystem('EPSG:4326'),crs,project)
    samples = [EventObservation(SummaryObservation(o.raw.source_id,o.raw.group,r.absolute_m,
                   r.distance_along_m,o.timestamp_utc,o.raw.accuracy),
                   _project((o.raw.longitude,o.raw.latitude),transform))
               for o,r in zip(observations,compared.rows)]
    detection = detect_events(metadata['planned_id'],samples,options)
    lookup = {s.observation.source_id:s.position for s in samples}
    string,number,integer = QMetaType.Type.QString,QMetaType.Type.Double,QMetaType.Type.Int
    columns = (('Event_ID',string),('Transect_ID',string),('Group_ID',string),
        ('Start_Distance_Along',number),('End_Distance_Along',number),('Event_Length',number),
        ('Start_Time',string),('End_Time',string),('Duration',number),('Mean_Deviation',number),
        ('Maximum_Deviation',number),('Mean_GNSS_Accuracy',number),('GNSS_Valid_Count',integer),
        ('Observation_Count',integer),('Source_IDs',string),('Geometry_Status',string),('GNSS_Units',string))
    fields = QgsFields()
    for name,kind in columns:
        fields.append(QgsField(name,kind))
    layer = QgsMemoryProviderUtils.createMemoryLayer('Deviation Events',fields,Qgis.WkbType.LineString,crs)
    features = []
    for e in detection.events:
        feature = QgsFeature(fields)
        points = [lookup[i] for i in e.source_ids]
        stationary = all(p == points[0] for p in points)
        feature.setAttributes([e.event_id,e.transect_id,e.group,e.start_distance_along_m,e.end_distance_along_m,
            e.event_length_m,e.start_time,e.end_time,e.duration_s,e.mean_deviation_m,e.maximum_deviation_m,
            e.mean_gnss_accuracy_raw,e.gnss_valid_count,e.observation_count,json.dumps(e.source_ids),
            'stationary; see source observations' if stationary else 'observation line','source units; not inferred'])
        if not stationary:
            feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(p.x,p.y) for p in points]))
        features.append(feature)
    write_verified(layer,fields,features)
    notices = [('UNKNOWN_CHRONOLOGY',group,'Missing/invalid/duplicate timestamps: this entire group was not evaluated.')
               for group in detection.excluded_groups]
    validation = _table('Deviation Event Validation',(('Code',string),('Group_ID',string),('Message',string)),
                        [list(row) for row in notices])
    metadata.update({'plugin_version':__version__,'event_options':asdict(options),
        'qa_observation_layer_id':compared.observations.id(),
        'event_detection':{'eligible_groups':detection.eligible_groups,'excluded_groups':detection.excluded_groups,
          'event_count':len(detection.events),'rejected_runs':detection.rejected_run_count,
          'temporal_splits':detection.temporal_gap_count,'spatial_splits':detection.spatial_gap_count},
        'event_length_definition':'maximum station minus minimum station; metres; not walked length',
        'event_duration_definition':'last above-threshold observation minus first; seconds; no boundary interpolation',
        'event_geometry':'unsmoothed participating observation XY in local QA CRS; stationary runs have NULL line geometry and explicit Geometry_Status',
        'event_gnss_units':'source units, not inferred; GNSS uncertainty is not subtracted',
        'event_claim':'descriptive exceedance runs, not proof of walking error or compliance'})
    for output in (layer,validation):
        output.setCustomProperty('transect_navigator/data_role','derived_qa_qc_data')
        output.setCustomProperty('transect_navigator/provenance',json.dumps(metadata,sort_keys=True,allow_nan=False))
        output.setReadOnly(True)
    return EventResult(detection,layer,validation)
