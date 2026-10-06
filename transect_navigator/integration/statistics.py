"""Build a detached machine-readable QA summary table from one coherent comparison."""
from dataclasses import dataclass
import json
from .. import __version__
from qgis.PyQt.QtCore import QMetaType
from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer
from .analyze import IngestionResult, _table, _transform, _xy
from .comparison import ComparisonResult, _strict_transform, _project
from ..qaqc.statistics import QaSummary, SummaryObservation, StatisticsOptions, summarize, track_length


@dataclass(frozen=True)
class SummaryResult:
    summary: QaSummary
    table: QgsVectorLayer


def summarize_comparison(ingested: IngestionResult, compared: ComparisonResult, *,
                         options: StatisticsOptions = StatisticsOptions(),
                         project: QgsProject | None = None) -> SummaryResult:
    """Keep native GNSS units distinct from metres; unknown track length stays NULL."""
    project = project if project is not None else QgsProject.instance()
    metadata = json.loads(compared.observations.customProperty('transect_navigator/provenance'))
    observations = ingested.ordering.observations
    if len(observations) != len(compared.rows) or not compared.rows:
        raise ValueError('Comparison and ingestion observations must match')
    mapping = metadata['qa_field_mapping']
    qa_ids = [str(f[mapping['QA_Source_ID']]) for f in compared.observations.getFeatures()]
    if qa_ids != [r.raw.source_id for r in observations]:
        raise ValueError('Comparison observation order changed; run comparison again')
    length = compared.rows[0].total_length_m
    if any(r.total_length_m != length for r in compared.rows):
        raise ValueError('Comparison contains inconsistent planned lengths')
    parts = None
    if ingested.raw_track is not None and ingested.raw_track.featureCount():
        working = QgsCoordinateReferenceSystem()
        if not working.createFromWkt(metadata['analysis_crs_wkt']):
            raise ValueError('Summary requires the comparison analysis CRS')
        to_wgs = _transform(ingested.raw_track,project)
        to_local = _strict_transform(QgsCoordinateReferenceSystem('EPSG:4326'),working,project)
        parts = []
        for feature in ingested.raw_track.getFeatures():
            geometry = feature.geometry()
            geometries = geometry.asGeometryCollection() if geometry.isMultipart() else [geometry]
            for part in geometries:
                parts.append([_project(_xy(point,ingested.raw_track,to_wgs),to_local) for point in part.vertices()])
    inputs = [SummaryObservation(obs.raw.source_id,obs.raw.group,row.absolute_m,row.distance_along_m,
                                obs.timestamp_utc,obs.raw.accuracy)
              for obs,row in zip(observations,compared.rows)]
    result = summarize(metadata['planned_id'],length,inputs,recorded_length_m=track_length(parts),options=options)
    d,g,c = result.observed_deviation,result.reported_gnss,result.coverage
    track_vertex_count = sum(len(part) for part in parts) if parts else 0
    track_status = 'unavailable' if parts is None else 'partial recorded representation' if track_vertex_count < len(observations) else 'available recorded representation'
    values = {
        'Transect_ID':result.transect_id,'Planned_Length_m':length,'Recorded_Length_m':result.recorded_length_m,
        'Observation_Count':result.observation_count,'Deviation_Valid_Count':d.count,
        'Deviation_Missing_Count':d.missing_count,'Deviation_Invalid_Count':d.invalid_count,
        'Mean_Deviation_m':d.mean,'Median_Deviation_m':d.median,'RMSE_m':d.rmse,
        'Maximum_Deviation_m':d.maximum,'P95_Deviation_m':d.p95,
        'GNSS_Valid_Count':g.count,'GNSS_Missing_Count':g.missing_count,'GNSS_Invalid_Count':g.invalid_count,
        'GNSS_Mean_Raw':g.mean,'GNSS_Median_Raw':g.median,'GNSS_Maximum_Raw':g.maximum,'GNSS_P95_Raw':g.p95,
        'GNSS_Units':'source units; not inferred','Coverage_Support_m':c.supported_length_m,
        'Coverage_Support_pct':c.supported_percent,'Coverage_Eligible_Groups':c.eligible_group_count,
        'Coverage_Excluded_Groups':c.excluded_group_count,'Coverage_Accepted_Pairs':c.accepted_pair_count,
        'Coverage_Rejected_Pairs':c.rejected_pair_count,
        'Coverage_Max_Station_Gap_m':options.coverage_max_station_gap_m,
        'Coverage_Max_Time_Gap_s':options.coverage_max_time_gap_s,
        'Coverage_Corridor_m':options.thresholds_m[2],
        'Threshold_Interpretation':'User-configurable QA/QC thresholds; not compliance standards',
        'Statistic_Weighting':'equal weight per valid observation',
        'Percentile_Method':'linear type 7; index (n-1)*0.95',
        'Track_Vertex_Count':track_vertex_count,'Track_Length_Status':track_status,
        'Track_Length_Model':'sum local AEQD vertex chords per raw part; no joins between parts',
        'Track_Origin':ingested.raw_track.customProperty('transect_navigator/track_origin','unknown') if ingested.raw_track is not None else 'unavailable',
        'Coverage_Method':'union of short station intervals from complete unique-time groups within threshold C',
        'Plugin_Version':__version__}
    for label,threshold,percent in zip('ABC',options.thresholds_m,result.within_percent):
        values[f'Threshold_{label}_m'] = threshold
        values[f'Within_{label}_pct'] = percent
    columns = tuple((name,QMetaType.Type.QString if isinstance(value,str) else
                    QMetaType.Type.Int if isinstance(value,int) else QMetaType.Type.Double)
                    for name,value in values.items())
    table = _table('QA Summary',columns,[list(values.values())])
    metadata.update({'plugin_version':__version__,'statistics':result.as_dict(),
                     'gnss_units':'source units; not inferred','thresholds_are_compliance_standards':False,
                     'qa_observation_layer_id':compared.observations.id()})
    table.setCustomProperty('transect_navigator/provenance',json.dumps(metadata,sort_keys=True,allow_nan=False))
    table.setCustomProperty('transect_navigator/data_role','derived_qa_qc_data')
    table.setReadOnly(True)
    return SummaryResult(result,table)
