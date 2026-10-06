"""Full real-PyQGIS survey workflow; never substitute fake QGIS for missing runtime.

Run with QGIS's Python: python3 -m tools.qgis_synthetic_survey --output NEW_FOLDER
Creates verified navigation/analysis packages and a file-backed QGIS project.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
from .synthetic_survey import (SAMPLE, FIELDS, TRANSECT_ID, ANALYSIS_TIME, EVENT_OPTIONS,
    check_dataset, expected, geographic, near, observations, verify_measurements)


def run_qgis_workflow(sample: Path, destination: Path,
                      recorded_path: Path | None = None) -> Path:
    """Actual provider ingestion, transformations, services, renderers and GPKG exports."""
    if not __debug__:
        raise RuntimeError('Validation requires assertions; do not run Python with -O')
    from qgis.core import (QgsProject,QgsVectorLayer,QgsCoordinateReferenceSystem,
                           Qgis,QgsRectangle)
    from transect_navigator.integration.prepare import prepare_navigation
    from transect_navigator.integration.export import export_navigation_package
    from transect_navigator.integration.recorded import load_recorded_file
    from transect_navigator.integration.analyze import ingest_recording
    from transect_navigator.integration.comparison import compare_ingested
    from transect_navigator.integration.statistics import summarize_comparison
    from transect_navigator.integration.events import detect_comparison_events
    from transect_navigator.integration.visualization import navigation_for_result,publish_analysis
    from transect_navigator.integration.analysis_export import capture_analysis,export_analysis_package
    from transect_navigator.io.package import package_destination
    from transect_navigator.io.exchange import validate_exchange_files
    from transect_navigator.analysis.observations import parse_timestamp
    check_dataset(sample)
    recorded_path=recorded_path or sample/'gps_observations.csv'
    destination=package_destination(destination.parent,destination.name)
    # Deliberately reserve, not overwrite. Failed runs retain an incomplete directory
    # for inspection; only a successful run writes validation.json at the end.
    destination.mkdir()
    inputs=(sample/'planned_transect.geojson',recorded_path)
    original_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    project=QgsProject()
    disk_project=QgsProject()
    reloaded=QgsProject()
    try:
        plan=QgsVectorLayer(str(inputs[0]),'Synthetic planned transect','ogr')
        assert plan.isValid() and plan.featureCount()==1
        before=bytes(next(plan.getFeatures()).geometry().asWkb())
        prepared=prepare_navigation(plan,'Transect_ID',10,project=project)
        assert len(prepared.rows)==31
        for index,row in enumerate(prepared.rows):
            assert row.sequence==index+1 and row.waypoint_id==f'SYN01_{index+1:03d}'
            near(row.distance_along_m,index*10)
            near(row.total_length_m,300)
            near(row.coordinate[0],geographic(index*10,0)[0],1e-11)
            near(row.coordinate[1],0,1e-11)
            if index<30:
                near(row.bearing_next_deg,90,.00001)
            else:
                assert row.bearing_next_deg is None
        nav_folder=export_navigation_package(prepared,destination,'navigation')
        validate_exchange_files(nav_folder,prepared.transects,prepared.rows,{'CSV','GPX','GeoJSON'})
        with sqlite3.connect(nav_folder/'navigation.gpkg') as db:
            nav=db.execute('SELECT Sequence,Distance_Along_m,Bearing_Next_deg FROM navigation_waypoints ORDER BY Sequence').fetchall()
            assert len(nav)==31
            for index,(sequence,station,bearing) in enumerate(nav):
                assert sequence==index+1
                near(station,index*10)
                if index<30:
                    near(bearing,90,.00001)
                else:
                    assert bearing is None
        source=load_recorded_file(recorded_path,x_field='Longitude',y_field='Latitude',csv_crs='EPSG:4326')
        source_fields=source.points.fields().names()
        source_records=Counter((bytes(f.geometry().asWkb()),tuple(repr(f[key]) for key in source_fields))
                               for f in source.points.getFeatures())
        ingested=ingest_recording(plan,'Transect_ID',next(plan.getFeatures()).id(),source,
            timestamp_field='Timestamp',accuracy_field='Horizontal_Accuracy_m',project=project)
        assert len(ingested.ordering.observations)==61 and not ingested.issues
        assert ingested.raw_track is not None and ingested.raw_track.featureCount()==1
        wanted=observations()
        actual={f['Observation_ID']:{key:str(f[key]) for key in FIELDS if key!='Timestamp'}
                for f in ingested.raw_observations.getFeatures()}
        assert actual=={row['Observation_ID']:{key:row[key] for key in FIELDS if key!='Timestamp'} for row in wanted}
        assert Counter((bytes(f.geometry().asWkb()),tuple(repr(f[key]) for key in source_fields))
                       for f in ingested.raw_observations.getFeatures())==source_records
        for normalized,row in zip(ingested.ordering.observations,wanted):
            assert parse_timestamp(normalized.timestamp_utc)[0]==parse_timestamp(row['Timestamp'])[0]
        vertices=list(next(ingested.raw_track.getFeatures()).geometry().vertices())
        assert len(vertices)==61
        for vertex,row in zip(vertices,wanted):
            near(vertex.x(),float(row['Longitude']),1e-14)
            near(vertex.y(),float(row['Latitude']),1e-14)
        compared=compare_ingested(ingested,project=project)
        summary=summarize_comparison(ingested,compared,project=project)
        events=detect_comparison_events(ingested,compared,options=EVENT_OPTIONS,project=project)
        verify_measurements(compared.rows,summary.summary,events.detection)
        raw_before=Counter((bytes(f.geometry().asWkb()),tuple(str(f[key]) for key in FIELDS))
                           for f in ingested.raw_observations.getFeatures())
        assert Counter((bytes(f.geometry().asWkb()),tuple(str(f[key]) for key in FIELDS))
                       for f in compared.observations.getFeatures())==raw_before
        navigation=navigation_for_result(ingested.planned,'Transect_ID',10,project)
        captured=capture_analysis(ingested,compared,summary,events,navigation,
                                 project=project,analysis_time=ANALYSIS_TIME)
        group=publish_analysis(ingested,compared,summary,events,id_field='Transect_ID',
            spacing_m=10,project=project,waypoints=navigation)
        assert group.name()=='Transect Navigator — SYN01'
        assert len(group.findLayers())==12
        assert len(compared.observations.renderer().rootRule().children())==5
        analysis_folder=export_analysis_package(captured,destination,'analysis')
        gpkg=analysis_folder/'analysis.gpkg'
        with sqlite3.connect(gpkg) as db:
            assert db.execute('PRAGMA quick_check').fetchone()==('ok',)
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
            tables={r[0] for r in db.execute('SELECT table_name FROM gpkg_contents')}
            assert tables=={name for name,_ in captured.layers}
            assert db.execute('SELECT COUNT(*) FROM qa_observations').fetchone()[0]==61
            assert db.execute('SELECT COUNT(*) FROM deviation_events').fetchone()[0]==1
            row=db.execute('SELECT Mean_Deviation_m,RMSE_m,Coverage_Support_pct,Event_Count,'
                'Waypoint_Spacing_m,Event_Min_Duration_s,Event_Min_Station_Span_m FROM qa_summary').fetchone()
            for actual_value,wanted_value in zip(row,(expected()['mean_absolute_m'],expected()['rmse_m'],90,1,10,15,15)):
                near(actual_value,wanted_value)
            event=db.execute('SELECT Start_Distance_Along,End_Distance_Along,Event_Length,Duration,'
                             'Observation_Count,Mean_GNSS_Accuracy FROM deviation_events').fetchone()
            for actual_value,wanted_value in zip(event,(155,210,55,55,12,62/12)):
                near(actual_value,wanted_value)
            metadata=json.loads(db.execute("SELECT Provenance_JSON FROM analysis_metadata WHERE Layer_Name='qa_observations'").fetchone()[0])
            assert metadata['qa_field_mapping']['QA_Deviation_m']=='QA_Deviation_m'
            assert metadata['analysis_crs_wkt']
        exported=QgsVectorLayer(f'{gpkg}|layername=raw_gps_observations','exported raw','ogr')
        assert exported.isValid() and exported.crs().authid()=='EPSG:4326'
        assert Counter((bytes(f.geometry().asWkb()),tuple(str(f[key]) for key in FIELDS))
                       for f in exported.getFeatures())==raw_before
        exported=None
        with (analysis_folder/'qa_summary.csv').open(encoding='utf-8',newline='') as stream:
            csv_summary=next(csv.DictReader(stream))
        near(float(csv_summary['Mean_Deviation_m']),expected()['mean_absolute_m'])
        near(float(csv_summary['Coverage_Support_pct']),90)
        assert csv_summary['Analysis_Time_UTC']==ANALYSIS_TIME and csv_summary['Event_Count']=='1'
        assert bytes(next(plan.getFeatures()).geometry().asWkb())==before
        for path in inputs:
            assert hashlib.sha256(path.read_bytes()).hexdigest()==original_hashes[str(path)]
        # Save a relocatable project using exported disk layers, not temporary memory layers.
        display_names={'planned_transect':'Planned Transect','navigation_waypoints':'Navigation Waypoints',
            'raw_gps_observations':'Raw GPS Observations','raw_track':'Raw GPS Track',
            'qa_observations':'QA Observations','deviation_events':'Deviation Events'}
        styled={node.layer().name():node.layer() for node in group.findLayers()}
        disk_group=disk_project.layerTreeRoot().addGroup(group.name())
        for table in ('deviation_events','qa_observations','raw_gps_observations','navigation_waypoints',
                      'raw_track','planned_transect','qa_summary','analysis_metadata'):
            title=display_names.get(table,table)
            layer=QgsVectorLayer(f'{gpkg}|layername={table}',title,'ogr')
            assert layer.isValid()
            if title in styled and styled[title].renderer() is not None:
                layer.setRenderer(styled[title].renderer().clone())
            assert disk_project.addMapLayer(layer,False) is not None
            disk_group.addLayer(layer)
        disk_project.setCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        disk_project.setFilePathStorage(Qgis.FilePathType.Relative)
        from qgis.core import QgsReferencedRectangle
        disk_project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(
            QgsRectangle(-.00015,-.00015,.00285,.00025),disk_project.crs()))
        project_path=destination/'synthetic_survey.qgz'
        assert disk_project.write(str(project_path))
        disk_project.clear()
        assert reloaded.read(str(project_path))
        assert len(reloaded.mapLayers())==8
        assert all(layer.isValid() for layer in reloaded.mapLayers().values())
        reloaded.clear()
        (destination/'validation.json').write_text(json.dumps({'qgis_runtime_executed':True,
            'qgis_version':Qgis.QGIS_VERSION,'verified':True,'analysis_time':ANALYSIS_TIME,
            'expected':expected()},indent=2)+'\n',encoding='utf-8')
        return destination
    finally:
        project.clear()
        disk_project.clear()
        reloaded.clear()


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample',type=Path,default=SAMPLE)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from qgis.core import QgsApplication
    app=QgsApplication([],False)
    app.initQgis()
    try:
        print(run_qgis_workflow(args.sample,args.output))
    finally:
        app.exitQgis()


if __name__=='__main__':
    main()
