"""Stage 8 independently authored 1,008-fix, five-source Survey integration oracle."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import time
import unittest

SAMPLE = Path(__file__).resolve().parents[2] / 'samples/survey_full'


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class FullSurveyIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    @staticmethod
    def _options(filename: str, surveyor: str):
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        if filename.endswith('.csv'):
            return ImportOptions('longitude','latitude','EPSG:4326',
                FieldMappings(timestamp='time',accuracy='accuracy',hdop='hdop',elevation='elevation'),surveyor)
        if filename.endswith('.gpx'):
            return ImportOptions(mappings=FieldMappings(timestamp='time',hdop='hdop',elevation='ele'),
                                 surveyor_id=surveyor)
        return ImportOptions(mappings=FieldMappings(timestamp='time',accuracy='accuracy',
                            hdop='hdop',elevation='elevation'),surveyor_id=surveyor)

    @staticmethod
    def _ids(intake,filename: str,start: int,end: int) -> tuple[str,...]:
        source=next(row for row in intake.model.sources.values() if row.source_file==filename)
        raw=sorted((row for row in intake.model.raw_observations.values()
                    if row.source_id==source.source_id),key=lambda row:row.source_order)
        assert len(raw)>=end, (filename,len(raw),end)
        return tuple(row.observation_id for row in raw[start-1:end])

    def test_complete_reviewed_survey_pipeline(self):
        from qgis.core import QgsProject,QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey,prepare_survey_navigation
        from transect_navigator.integration.survey_export import export_survey_navigation
        from transect_navigator.integration.survey_intake import SurveyIntake
        from transect_navigator.integration.survey_review import start_review,save_review,load_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_qa import assert_batch_current
        from transect_navigator.integration.survey_results import build_results,status_layer
        from transect_navigator.integration.survey_snapshot_export import export_survey_snapshot,verify_survey_snapshot
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.qa import QaOptions
        from transect_navigator.survey.results import CompletionPolicy,scope_evidence
        from transect_navigator.qaqc.statistics import StatisticsOptions
        from transect_navigator.qaqc.events import EventOptions

        expected=json.loads((SAMPLE/'expected.json').read_text())
        times={}
        began=time.perf_counter()
        project=QgsProject()
        planned=QgsVectorLayer(str(SAMPLE/'planned.geojson'),'official plan','ogr')
        self.assertTrue(planned.isValid())
        self.assertEqual(planned.featureCount(),12)
        site=QgsVectorLayer(str(SAMPLE/'site_boundary.geojson'),'declared 10 km site','ogr')
        self.assertTrue(site.isValid())
        self.assertEqual(site.featureCount(),1)
        self.assertEqual(next(site.getFeatures())['Declared_Width_m'],10000)
        setup=setup_survey(planned,'Transect_ID',survey_id=expected['survey_id'],
            name='Independent Stage 8 survey',registry=SurveyRegistry(),project=project,
            created_utc='2026-10-03T00:00:00Z')
        navigation=prepare_survey_navigation(setup,spacing_m=10,project=project)
        self.assertEqual(len(navigation.prepared.rows),expected['waypoint_count'])
        times['prepare']=time.perf_counter()-began

        began_intake=time.perf_counter()
        intake=SurveyIntake(setup.survey,project=project)
        for filename,surveyor in (('A1.csv','S01'),('B1.gpx','S02'),('B2.gpkg','S02'),
                                  ('C1.geojson','S03'),('C2.csv','S03')):
            attempt=intake.import_file(SAMPLE/filename,self._options(filename,surveyor),
                                       imported_utc='2026-10-03T12:00:00Z')
            self.assertEqual(attempt.status,'SUCCESS',f'{filename}: {attempt.message}')
            count=sum(row.source_id==attempt.source_id for row in intake.model.raw_observations.values())
            self.assertEqual(count,expected['source_counts'][filename])
        self.assertEqual(len(intake.model.sources),5)
        self.assertEqual(len(intake.model.recordings),5)
        self.assertEqual(len(intake.model.raw_observations),expected['raw_count'])
        self.assertEqual({r.surveyor_id for r in intake.model.recordings.values()},{'S01','S02','S03'})
        self.assertTrue(all('Transect_ID' not in native.observations.fields().names()
                            for native in intake.native_recordings.values()))
        b1_at_400,b1_at_500=self._ids(intake,'B1.gpx',41,42)
        first=datetime.fromisoformat(intake.model.normalized_observations[b1_at_400].timestamp_utc)
        second=datetime.fromisoformat(intake.model.normalized_observations[b1_at_500].timestamp_utc)
        self.assertEqual((second-first).total_seconds(),500)
        times['intake']=time.perf_counter()-began_intake

        with TemporaryDirectory() as folder:
            folder=Path(folder)
            original={name:hashlib.sha256((SAMPLE/name).read_bytes()).hexdigest()
                      for name in expected['source_counts']}
            before=(len(intake.model.sources),len(intake.model.recordings),
                    len(intake.model.raw_observations))
            duplicate=intake.import_file(SAMPLE/'B2.gpkg',self._options('B2.gpkg','S02'))
            self.assertEqual(duplicate.status,'DUPLICATE')
            renamed=folder/'renamed_B2.gpkg'
            shutil.copyfile(SAMPLE/'B2.gpkg',renamed)
            self.assertEqual(intake.import_file(renamed,self._options('B2.gpkg','S02')).status,'DUPLICATE')
            self.assertEqual(before,(len(intake.model.sources),len(intake.model.recordings),
                                     len(intake.model.raw_observations)))

            began_review=time.perf_counter()
            run=start_review(intake,ContinuityPolicy(30,30),created_utc='2026-10-03T13:00:00Z')
            for action in expected['review_actions']:
                ids=self._ids(intake,action['file'],*action['rows'])
                kwargs={'assignment_status':action['status']}
                if action['status']=='ASSIGNED':
                    kwargs.update(activity_role='SURVEY',transect_id=action['plan'],
                                  direction=action['direction'])
                elif action.get('role')=='TRANSITION':
                    kwargs['activity_role']='TRANSITION'
                run.decide(ids,reviewer='Independent oracle reviewer',
                           reason='Authored exact range; no automatic association',
                           utc='2026-10-03T14:00:00Z',**kwargs)
            dispositions=Counter(d.assignment_status for d in run.membership.values())
            self.assertEqual(dict(dispositions),{'ASSIGNED':719,'AMBIGUOUS':202,'UNASSIGNED':87})
            self.assertEqual(sum(d.activity_role=='TRANSITION' for d in run.membership.values()),75)
            self.assertEqual(len(run.membership),1008)
            self.assertEqual(len(run.visits),expected['visit_count'])
            self.assertEqual(len({key for visit in run.visits for key in visit.observation_ids}),719)
            self.assertEqual(sorted(len(v.observation_ids) for v in run.visits),
                             sorted((101,101,61,41,51,101,101,101,61)))
            self.assertEqual(len([v for v in run.visits if v.transect_id=='T04']),2)
            self.assertTrue(any('TIME_GAP' in segment.boundary_reasons and
                                'SPATIAL_GAP' in segment.boundary_reasons for segment in run.segments))
            times['review']=time.perf_counter()-began_review

            began_persistence=time.perf_counter()
            review_folder=save_review(run,intake,folder,'review')
            reopened_review=load_review(review_folder,intake)
            self.assertEqual(reopened_review.snapshot(),run.snapshot())
            times['review_save_reopen']=time.perf_counter()-began_persistence

            began_qa=time.perf_counter()
            options=QaOptions(
                StatisticsOptions((3,5,10),30,30),
                EventOptions(5,30,20,30,30))
            batch=run_batch_qa(run,intake,options,created_utc='2026-10-03T15:00:00Z')
            self.assertEqual(dict(batch.disposition_counts)['ASSIGNED'],719)
            self.assertEqual(dict(batch.disposition_counts)['TRANSITION'],75)
            self.assertEqual(sum(len(v.samples) for v in batch.visits),719)
            self.assertTrue(all(v.processing_state=='SUCCESS' for v in batch.visits),
                            [(v.visit.transect_id,v.error) for v in batch.visits if v.error])
            times['qa']=time.perf_counter()-began_qa

            began_results=time.perf_counter()
            policy=CompletionPolicy('SYN02-full-ends',100,0,.02,'Independent oracle reviewer',
                                    'Synthetic operational rule only','2026-10-03T16:00:00Z')
            scopes=()
            for file,start,end,plans in (('B1.gpx',108,208,('T05','T06')),
                                         ('C2.csv',77,177,('T11','T12')),
                                         ('C2.csv',178,189,())):
                scopes=scope_evidence(scopes,batch,run.membership,self._ids(intake,file,start,end),plans,
                    reviewer='Independent oracle reviewer',reason='Authored relevance review',
                    utc='2026-10-03T16:01:00Z')
            result=build_results(batch,run,intake,policy,scopes,
                                 created_utc='2026-10-03T17:00:00Z')
            by_plan={row.transect_id:row for row in result.transects}
            self.assertEqual({key:row.final_status for key,row in by_plan.items()},expected['statuses'])
            self.assertEqual(dict(result.summary.status_counts),expected['status_counts'])
            self.assertAlmostEqual(result.summary.total_planned_length_m,12000,delta=.1)
            self.assertAlmostEqual(result.summary.confirmed_progress_m,6100,delta=.1)
            self.assertAlmostEqual(result.summary.progress_lower_bound_percent,100*6100/12000,delta=.001)
            for name,truth in expected['support_m'].items():
                self.assertAlmostEqual(by_plan[name].confirmed_progress_m,truth,delta=.1,msg=name)
            self.assertEqual(len(by_plan['T07'].qa.visit_ids),2)
            self.assertAlmostEqual(by_plan['T07'].confirmed_progress_m,1000,delta=.1)
            t02_samples=[sample for visit in batch.visits if visit.visit.transect_id=='T02'
                         for sample in visit.samples]
            self.assertEqual(len(t02_samples),101)
            self.assertTrue(all(sample.deviation.signed_m>0 for sample in t02_samples[1:-1]))
            self.assertEqual(by_plan['T09'].qa.accepted_observation_count,0)
            self.assertEqual(status_layer(result,setup.survey).featureCount(),12)
            times['results']=time.perf_counter()-began_results

            t08=by_plan['T08'].qa.summary.observed_deviation
            truth=expected['t08']
            self.assertAlmostEqual(t08.mean,truth['mean_m'],delta=.02)
            self.assertAlmostEqual(t08.rmse,truth['rmse_m'],delta=.02)
            self.assertAlmostEqual(t08.median,truth['median_m'],delta=.02)
            self.assertAlmostEqual(t08.p95,truth['p95_m'],delta=.02)
            self.assertAlmostEqual(t08.maximum,truth['maximum_m'],delta=.02)
            event=[e for v in batch.visits if v.visit.transect_id=='T08' for e in v.events]
            self.assertEqual(len(event),1)
            self.assertEqual(event[0].result.observation_count,11)
            self.assertAlmostEqual(event[0].result.duration_s,100,delta=.001)
            self.assertAlmostEqual(event[0].result.event_length_m,100,delta=.02)
            self.assertAlmostEqual(event[0].result.mean_gnss_accuracy_raw,
                                   truth['event_mean_accuracy_m'],delta=.00001)
            samples=[sample for v in batch.visits if v.visit.transect_id=='T08' for sample in v.samples]
            self.assertEqual(sum(sample.accuracy_raw=='12' for sample in samples),3)
            self.assertTrue(all(sample.deviation.signed_m>0 for sample in samples
                                if 400 <= sample.deviation.distance_along_m <= 500 and
                                sample.deviation.absolute_m>5))
            self.assertEqual(intake.import_file(SAMPLE/'B2.gpkg',self._options('B2.gpkg','S02')).status,
                             'DUPLICATE')
            assert_batch_current(batch,run,intake,options)
            self.assertEqual(len(run.visits),9)
            self.assertAlmostEqual(result.summary.confirmed_progress_m,6100,delta=.1)
            self.assertEqual(dict(result.summary.status_counts),expected['status_counts'])

            began_export=time.perf_counter()
            nav_folder=export_survey_navigation(navigation,folder,'navigation')
            times['navigation_export']=time.perf_counter()-began_export
            began_snapshot=time.perf_counter()
            package=export_survey_snapshot(result,batch,run,intake,folder,'analysis',
                navigation=navigation,created_utc='2026-10-03T18:00:00Z')
            metadata=verify_survey_snapshot(package)
            times['survey_export_verify']=time.perf_counter()-began_snapshot
            self.assertEqual(metadata['counts']['raw_observations'],1008)
            self.assertEqual(metadata['counts']['qa_observations'],719)
            self.assertEqual(metadata['counts']['ambiguous'],202)
            self.assertEqual(metadata['counts']['unassigned'],87)
            self.assertEqual(metadata['counts']['transitions'],75)
            self.assertEqual(metadata['layers']['navigation_waypoints']['count'],1212)
            self.assertEqual(metadata['status_counts'],expected['status_counts'])
            self.assertAlmostEqual(metadata['summary']['confirmed_progress_m'],6100,delta=.1)
            self.assertEqual(metadata['layers']['planned_transects']['count'],12)
            self.assertEqual(metadata['layers']['project_status']['count'],12)
            self.assertEqual(len(metadata['source_manifest']),5)
            def records(name,key):
                layer=QgsVectorLayer(f'{package}/survey_analysis.gpkg|layername={name}',name,'ogr')
                self.assertTrue(layer.isValid())
                return {feature[key]:json.loads(feature['record_json']) for feature in layer.getFeatures()}
            exported_results=records('transect_results','Transect_ID')
            exported_visits=records('visits','Visit_ID')
            exported_events=records('qa_events','Event_UID')
            self.assertEqual({key:row['final_status'] for key,row in exported_results.items()},
                             expected['statuses'])
            self.assertEqual(sum(row['transect_id']=='T07' for row in exported_visits.values()),2)
            self.assertEqual(exported_results['T09']['qa']['accepted_observation_count'],0)
            for name in ('T05','T06','T11','T12'):
                self.assertEqual(exported_results[name]['confirmed_progress_m'],0)
                self.assertEqual(exported_results[name]['final_status'],'REVIEW')
            self.assertEqual(len(exported_events),1)
            exported_t08=exported_results['T08']['qa']['summary']['observed_deviation']
            self.assertAlmostEqual(exported_t08['mean'],truth['mean_m'],delta=.02)
            self.assertAlmostEqual(exported_t08['rmse'],truth['rmse_m'],delta=.02)
            self.assertEqual(next(iter(exported_events.values()))['result']['observation_count'],11)
            exported_member=records('assignment_membership','Observation_ID')
            self.assertEqual(Counter(row['assignment_status'] for row in exported_member.values()),
                             {'ASSIGNED':719,'AMBIGUOUS':202,'UNASSIGNED':87})
            exported_qa=records('qa_observations','Observation_ID')
            self.assertEqual(len(exported_qa),719)
            self.assertEqual(sum(row['accuracy_raw']=='12' and row['Transect_ID']=='T08'
                                 for row in exported_qa.values()),3)
            for row in metadata['source_manifest']:
                self.assertEqual(row['SHA256'],original[row['Original_Name']])
                self.assertEqual(hashlib.sha256((package/row['Exported_File']).read_bytes()).hexdigest(),
                                 row['SHA256'])
            before_export=(package/'survey_analysis.gpkg').read_bytes()
            with self.assertRaisesRegex(ValueError,'already exists'):
                export_survey_snapshot(result,batch,run,intake,folder,'analysis',navigation=navigation)
            self.assertEqual((package/'survey_analysis.gpkg').read_bytes(),before_export)
            self.assertTrue((nav_folder/'survey_navigation.gpkg').is_file())
            times['export']=time.perf_counter()-began_export
            size=sum(path.stat().st_size for path in package.rglob('*') if path.is_file())
            for name,checksum in original.items():
                self.assertEqual(hashlib.sha256((SAMPLE/name).read_bytes()).hexdigest(),checksum)
            self.assertEqual(len(intake.model.raw_observations),1008)
            times['total']=time.perf_counter()-began
            print('\nSTAGE8_TIMINGS',json.dumps(times,sort_keys=True),'PACKAGE_BYTES',size)
            print('STAGE8_ACTUAL',json.dumps({'support_m':result.summary.confirmed_progress_m,
                'planned_m':result.summary.total_planned_length_m,
                'coverage_pct':result.summary.progress_lower_bound_percent,
                't08_mean':t08.mean,'t08_rmse':t08.rmse,'t08_event_span':event[0].result.event_length_m},sort_keys=True))


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None, 'PyQGIS unavailable')
class FullSurveyEdgeVariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app=start_app()

    def _setup(self, lines: dict[str,list[tuple[float,float]]]):
        from qgis.core import QgsFeature,QgsGeometry,QgsPointXY,QgsProject,QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey
        from transect_navigator.integration.survey_intake import SurveyIntake
        from transect_navigator.survey.model import SurveyRegistry
        layer=QgsVectorLayer('LineString?crs=EPSG:4326&field=Transect_ID:string',
                             'independent edge plans','memory')
        for name,coords in lines.items():
            feature=QgsFeature(layer.fields())
            feature.setAttributes([name])
            feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*point) for point in coords]))
            self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        project=QgsProject()
        setup=setup_survey(layer,'Transect_ID',survey_id='SYN02-EDGE',name='Isolated edge',
                           registry=SurveyRegistry(),project=project,
                           created_utc='2026-10-04T00:00:00Z')
        return setup,SurveyIntake(setup.survey,project=project)

    @staticmethod
    def _write(path: Path, rows: list[tuple[float,float,str|None,str|None]]):
        with path.open('w',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream,lineterminator='\n')
            writer.writerow(('longitude','latitude','time','accuracy'))
            for row in rows:
                writer.writerow(row)
        return path

    def _import(self,intake,path: Path,surveyor=None):
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        attempt=intake.import_file(path,ImportOptions('longitude','latitude','EPSG:4326',
            FieldMappings(timestamp='time',accuracy='accuracy'),surveyor),
            imported_utc='2026-10-04T01:00:00Z')
        self.assertEqual(attempt.status,'SUCCESS',attempt.message)
        return tuple(row.observation_id for row in sorted(intake.model.raw_observations.values(),
                         key=lambda raw:raw.source_order) if row.source_id==attempt.source_id)

    @staticmethod
    def _plan(start='T01',reverse=False):
        from tools.survey_full_fixture import station_point
        points=[station_point(start,station) for station in (0,1000)]
        return list(reversed(points)) if reverse else points

    def test_crossing_lines_remain_ambiguous_without_reviewer_assignment(self):
        from tools.survey_full_fixture import direct,station_point
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.results import CompletionPolicy,scope_evidence
        center=station_point('T01',500)
        north=direct(center,0,500)
        south=direct(center,180,500)
        _,intake=self._setup({'EAST':self._plan(),'NORTH':[south,north]})
        with TemporaryDirectory() as folder:
            rows=[(*station_point('T01',station),f'2026-10-04T00:00:{i*10:02d}Z','')
                  for i,station in enumerate((490,500,510))]
            ids=self._import(intake,self._write(Path(folder)/'crossing.csv',rows))
            run=start_review(intake,ContinuityPolicy(30,30))
            run.decide(ids,reviewer='Fixture analyst',reason='Crossing has two plausible lines',
                       assignment_status='AMBIGUOUS')
            self.assertEqual(len(run.visits),0)
            batch=run_batch_qa(run,intake)
            policy=CompletionPolicy('crossing-review',100,0,.02,'Fixture analyst',
                                    'Synthetic operational rule','2026-10-04T02:00:00Z')
            scope=scope_evidence((),batch,run.membership,ids,('EAST','NORTH'),
                reviewer='Fixture analyst',reason='Both lines relevant')
            result=build_results(batch,run,intake,policy,scope)
            self.assertEqual({row.final_status for row in result.transects},{'REVIEW'})
            self.assertEqual(result.summary.confirmed_progress_m,0)

    def test_missing_duplicate_clock_reversal_unknown_accuracy_and_overlap(self):
        from tools.survey_full_fixture import station_point
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.survey.review import ContinuityPolicy
        _,intake=self._setup({'T01':self._plan()})
        rows=[(*station_point('T01',station),timestamp,'') for station,timestamp in (
            (0,'2026-10-04T00:00:00Z'),(10,'2026-10-04T00:00:10Z'),
            (20,'2026-10-04T00:00:10Z'),(30,'2026-10-04T00:00:05Z'),(40,''))]
        with TemporaryDirectory() as folder:
            first=self._write(Path(folder)/'first.csv',rows)
            ids=self._import(intake,first)
            second=self._write(Path(folder)/'overlap.csv',rows[:2]+[
                (*station_point('T01',50),'2026-10-04T00:00:50Z','')])
            more=self._import(intake,second)
            self.assertEqual(len(ids)+len(more),8)
            self.assertEqual(len(intake.model.raw_observations),8)
            self.assertTrue(all('SUSPECTED_OVERLAP' in source.warnings
                                for source in intake.model.sources.values()))
            normal=[intake.model.normalized_observations[key] for key in ids]
            self.assertEqual(sum(row.time_status=='duplicate' for row in normal),2)
            self.assertEqual(sum(row.time_status=='missing' for row in normal),1)
            self.assertTrue(all(row.accuracy_raw is None for row in normal))
            self.assertEqual({r.surveyor_id for r in intake.model.recordings.values()},{None})
            run=start_review(intake,ContinuityPolicy(30,30))
            reasons={reason for segment in run.segments for reason in segment.boundary_reasons}
            flags={flag for segment in run.segments for flag in segment.flags}
            self.assertIn('NONINCREASING_TIME',reasons)
            self.assertIn('TIME_VALIDITY_BOUNDARY',reasons)
            self.assertTrue({'DUPLICATE_TIME','CLOCK_REVERSAL','MISSING_OR_INVALID_TIME'} <= flags)
            self.assertEqual(len(run.membership),8)

    def test_stationary_null_and_moving_event_unknown_surveyor(self):
        from tools.survey_full_fixture import station_point
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results
        from transect_navigator.integration.survey_snapshot_export import export_survey_snapshot,verify_survey_snapshot
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.qa import QaOptions
        from transect_navigator.survey.results import CompletionPolicy
        from transect_navigator.qaqc.statistics import StatisticsOptions
        from transect_navigator.qaqc.events import EventOptions
        setup,intake=self._setup({'T01':self._plan()})
        with TemporaryDirectory() as folder:
            folder=Path(folder)
            stationary=[(*station_point('T01',20,20),f'2026-10-04T00:00:{i*10:02d}Z','')
                        for i in range(3)]
            moving=[(*station_point('T01',i*10,20),f'2026-10-04T01:00:{i*10:02d}Z','2')
                    for i in range(3)]
            fixed=self._import(intake,self._write(folder/'stop.csv',stationary))
            walked=self._import(intake,self._write(folder/'walk.csv',moving),'S02')
            run=start_review(intake,ContinuityPolicy(30,30))
            for ids in (fixed,walked):
                run.decide(ids,reviewer='Fixture analyst',reason='Known fixture membership',
                           assignment_status='ASSIGNED',activity_role='SURVEY',transect_id='T01')
            options=QaOptions(StatisticsOptions((3,5,10),30,30),
                              EventOptions(5,20,None,30,30))
            batch=run_batch_qa(run,intake,options)
            events=[event for visit in batch.visits for event in visit.events]
            self.assertEqual(len(events),2)
            self.assertEqual(sum(event.geometry_wkb_hex is None for event in events),1)
            self.assertTrue(any(event.geometry_wkb_hex for event in events))
            self.assertIn(None,{visit.visit.surveyor_id for visit in batch.visits})
            policy=CompletionPolicy('stationary',100,0,.02,'Fixture analyst',
                                    'Synthetic operational rule','2026-10-04T02:00:00Z')
            results=build_results(batch,run,intake,policy)
            package=export_survey_snapshot(results,batch,run,intake,folder,'events')
            meta=verify_survey_snapshot(package)
            self.assertEqual(meta['counts']['events'],2)
            self.assertEqual(meta['counts']['raw_observations'],6)

    def test_review_edit_staleness_reanalysis_and_storage_order(self):
        from tools.survey_full_fixture import station_point
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa,assert_batch_current
        from transect_navigator.survey.review import ContinuityPolicy
        _,intake=self._setup({'T01':self._plan()})
        with TemporaryDirectory() as folder:
            rows=[(*station_point('T01',i*10),f'2026-10-04T00:00:{i*10:02d}Z','2')
                  for i in range(4)]
            ids=self._import(intake,self._write(Path(folder)/'review.csv',rows),'S01')
            run=start_review(intake,ContinuityPolicy(30,30),created_utc='2026-10-04T01:00:00Z')
            run.decide(ids,reviewer='Fixture analyst',reason='First decision',
                       assignment_status='ASSIGNED',activity_role='SURVEY',transect_id='T01')
            batch=run_batch_qa(run,intake)
            before=run.history
            run.decide(ids[1:2],reviewer='Fixture analyst',reason='Revised uncertainty',
                       assignment_status='AMBIGUOUS')
            self.assertEqual(run.history[0],before[0])
            self.assertEqual(run.history[-1].superseded_decision_ids,(before[0].decision_id,))
            with self.assertRaisesRegex(ValueError,'Stale QA'):
                assert_batch_current(batch,run,intake,batch.options)
            revised=run_batch_qa(run,intake)
            self.assertEqual(sum(len(visit.samples) for visit in revised.visits),3)
            snapshot=run.snapshot()
            for name in ('sources','recordings','raw_observations','normalized_observations'):
                table=getattr(intake.model,name)
                setattr(intake.model,name,dict(reversed(list(table.items()))))
            self.assertEqual(run.snapshot(),snapshot)
            self.assertEqual(start_review(intake,ContinuityPolicy(30,30),
                             created_utc='2026-10-04T01:00:00Z').input_digest,run.input_digest)

    def test_official_geometry_reversal_changes_sign_only(self):
        from tools.survey_full_fixture import station_point
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.survey.review import ContinuityPolicy
        rows=[(*station_point('T01',station,1),f'2026-10-04T00:00:{index*10:02d}Z','2')
              for index,station in enumerate((100,110,120))]
        with TemporaryDirectory() as folder:
            source=self._write(Path(folder)/'same_points.csv',rows)
            outcomes=[]
            for reverse in (False,True):
                _,intake=self._setup({'T01':self._plan(reverse=reverse)})
                ids=self._import(intake,source,'S01')
                run=start_review(intake,ContinuityPolicy(30,30))
                run.decide(ids,reviewer='Fixture analyst',reason='Same GPS, opposite official geometry',
                    assignment_status='ASSIGNED',activity_role='SURVEY',transect_id='T01',
                    direction='REVERSE' if reverse else 'FORWARD')
                batch=run_batch_qa(run,intake)
                outcomes.append(batch)
            a,b=outcomes
            left=[sample.deviation for visit in a.visits for sample in visit.samples]
            right=[sample.deviation for visit in b.visits for sample in visit.samples]
            self.assertEqual(len(left),len(right))
            for first,second in zip(left,right):
                self.assertAlmostEqual(first.absolute_m,second.absolute_m,delta=.02)
                self.assertAlmostEqual(first.signed_m,-second.signed_m,delta=.02)
            self.assertAlmostEqual(a.transects[0].progress_support.supported_length_m,
                                   b.transects[0].progress_support.supported_length_m,delta=.02)

    def test_large_excursion_reduces_qa_corridor_without_changing_completion(self):
        from tools.survey_full_fixture import station_point
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.results import CompletionPolicy
        _,intake=self._setup({'T01':self._plan()})
        rows=[]
        for index in range(101):
            station=index*10
            offset=0 if station in (0,1000) else 12 if 400<=station<=500 else (-1 if index%2 else 1)
            lon,lat=station_point('T01',station,offset)
            rows.append((lon,lat,f'2026-10-04T00:{index//6:02d}:{index%6*10:02d}Z','2'))
        with TemporaryDirectory() as folder:
            ids=self._import(intake,self._write(Path(folder)/'excursion.csv',rows),'S01')
            run=start_review(intake,ContinuityPolicy(30,30))
            run.decide(ids,reviewer='Fixture analyst',reason='Known full visit with excursion',
                assignment_status='ASSIGNED',activity_role='SURVEY',transect_id='T01')
            batch=run_batch_qa(run,intake)
            policy=CompletionPolicy('full-progress',100,0,.02,'Fixture analyst',
                                    'Synthetic operational rule','2026-10-04T02:00:00Z')
            row=build_results(batch,run,intake,policy).transects[0]
            self.assertEqual(row.final_status,'COMPLETE')
            self.assertAlmostEqual(row.confirmed_progress_m,1000,delta=.1)
            self.assertLess(row.confirmed_corridor_m,900)
            self.assertGreater(row.qa.summary.observed_deviation.maximum,10)

    def test_invalid_file_and_missing_geometry_remain_visible(self):
        from qgis.core import QgsFeature,QgsVectorLayer
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.survey.intake import FieldMappings
        from transect_navigator.survey.review import ContinuityPolicy
        _,intake=self._setup({'T01':self._plan()})
        with TemporaryDirectory() as folder:
            bad=Path(folder)/'broken.gpx'
            bad.write_text('<gpx')
            attempt=intake.import_file(bad)
            self.assertEqual(attempt.status,'FAILED')
            self.assertEqual(len(intake.model.raw_observations),0)
            loaded=QgsVectorLayer('Point?crs=EPSG:4326&field=time:string',
                                   'null geometry recording','memory')
            feature=QgsFeature(loaded.fields())
            feature.setAttributes(['2026-10-04T00:00:00Z'])
            self.assertTrue(loaded.dataProvider().addFeatures([feature])[0])
            accepted=intake.import_layer(loaded,source_file='null_position',
                options=ImportOptions(mappings=FieldMappings(timestamp='time')))
            self.assertEqual(accepted.status,'SUCCESS')
            self.assertEqual(len(intake.model.raw_observations),1)
            raw=next(iter(intake.model.raw_observations.values()))
            self.assertEqual(intake.model.normalized_observations[raw.observation_id].position_status,
                             'missing_geometry')
            run=start_review(intake,ContinuityPolicy(30,30))
            with self.assertRaisesRegex(ValueError,'Invalid-position'):
                run.decide((raw.observation_id,),reviewer='Fixture analyst',reason='Cannot accept null point',
                    assignment_status='ASSIGNED',activity_role='SURVEY',transect_id='T01')
            self.assertEqual(run.membership[raw.observation_id].assignment_status,'UNASSIGNED')
            self.assertEqual(len(intake.model.attempts),2)

    def test_projected_companion_and_import_order_invariance(self):
        from qgis.core import QgsProject,QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey,prepare_survey_navigation
        from transect_navigator.integration.survey_intake import SurveyIntake
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.survey.review import ContinuityPolicy
        projected=QgsVectorLayer(f'{SAMPLE}/planned_projected.gpkg|layername=planned',
                                 'projected companion','ogr')
        self.assertTrue(projected.isValid())
        self.assertEqual(projected.crs().authid(),'EPSG:32631')
        project=QgsProject()
        setup=setup_survey(projected,'Transect_ID',survey_id='SYN02',name='Companion',
            registry=SurveyRegistry(),project=project,created_utc='2026-10-03T00:00:00Z')
        nav=prepare_survey_navigation(setup,spacing_m=10,project=project)
        self.assertEqual(len(nav.prepared.rows),1212)
        geographic=QgsVectorLayer(str(SAMPLE/'planned.geojson'),'geographic','ogr')
        primary=setup_survey(geographic,'Transect_ID',survey_id='SYN02',name='Companion',
            registry=SurveyRegistry(),project=QgsProject(),created_utc='2026-10-03T00:00:00Z')
        instances=[]
        for order in (('A1.csv','B2.gpkg'),('B2.gpkg','A1.csv')):
            intake=SurveyIntake(primary.survey,project=QgsProject())
            for filename in order:
                surveyor='S01' if filename=='A1.csv' else 'S02'
                attempt=intake.import_file(SAMPLE/filename,
                    FullSurveyIntegrationTests._options(filename,surveyor),
                    imported_utc='2026-10-03T12:00:00Z')
                self.assertEqual(attempt.status,'SUCCESS',attempt.message)
            instances.append((set(intake.model.raw_observations),
                start_review(intake,ContinuityPolicy(30,30),
                             created_utc='2026-10-03T13:00:00Z').snapshot()))
        self.assertEqual(instances[0],instances[1])
