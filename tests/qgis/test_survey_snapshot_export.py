"""Stage 7 known-answer export through QGIS providers and standalone reopen."""
from collections import Counter
from dataclasses import asdict, replace
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

SAMPLE = Path(__file__).resolve().parents[2]/'samples/survey_snapshot'
BASE = Path(__file__).resolve().parents[2]/'samples/survey_results'


@unittest.skipUnless(importlib.util.find_spec('qgis') is not None,'PyQGIS unavailable')
class SurveySnapshotExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing import start_app
        cls.app = start_app()

    def setUp(self):
        from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey,prepare_survey_navigation
        from transect_navigator.integration.survey_intake import SurveyIntake,ImportOptions
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.survey.intake import FieldMappings
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.qa import QaOptions
        from transect_navigator.survey.results import CompletionPolicy,scope_evidence
        self.started = time.perf_counter()
        self.expected = json.loads((SAMPLE/'expected.json').read_text())
        self.actions = json.loads((BASE/'expected.json').read_text())['actions']
        self.project = QgsProject()
        self.planned = QgsVectorLayer(str(SAMPLE/'planned.geojson'),'official plan','ogr')
        self.setup = setup_survey(self.planned,'Transect_ID',survey_id='STAGE7',name='Export fixture',
            registry=SurveyRegistry(),project=self.project,created_utc='2026-10-02T00:00:00Z')
        self.intake = SurveyIntake(self.setup.survey,project=self.project)
        for action in self.actions:
            status = self.intake.import_file(SAMPLE/action['file'],self.options(action['surveyor']),
                imported_utc='2026-10-02T12:00:00Z')
            self.assertEqual(status.status,'SUCCESS')
        for name,surveyor in (('stationary.csv','S03'),('transitions.csv',None)):
            status = self.intake.import_file(SAMPLE/name,self.options(surveyor),
                imported_utc='2026-10-02T12:00:00Z')
            self.assertEqual(status.status,'SUCCESS')
        loaded = QgsVectorLayer('PointZ?crs=EPSG:4326&field=time:string&field=fix:string&field=elevation:double',
                                'loaded GPS, no original file','memory')
        for i in range(2):
            feature = QgsFeature(loaded.fields())
            feature.setGeometry(QgsGeometry.fromWkt(f'POINT Z({.1 + i*.0001} 0 9)'))
            feature.setAttributes([f'2026-10-03T00:00:{i*10:02d}Z','3D',9.0])
            self.assertTrue(loaded.dataProvider().addFeatures([feature])[0])
        self.loaded = loaded
        imported = self.intake.import_layer(loaded,source_file='loaded_GNSS',
            options=ImportOptions(mappings=FieldMappings(timestamp='time',fix='fix',elevation='elevation')),
            imported_utc='2026-10-02T13:00:00Z')
        self.assertEqual(imported.status,'SUCCESS')
        self.run = start_review(self.intake,ContinuityPolicy(60,30),created_utc='2026-10-02T14:00:00Z')
        for action in self.actions:
            kwargs = {'assignment_status':action['status']}
            if action['status']=='ASSIGNED':
                kwargs.update(activity_role='SURVEY',transect_id=action['plan'],direction=action['direction'])
            self.run.decide(self.ids(action['file']),reviewer='Oracle reviewer',reason='Authored exact membership',
                utc='2026-10-02T15:00:00Z',**kwargs)
        self.run.decide(self.ids('stationary.csv'),reviewer='Oracle reviewer',reason='Stationary accepted Visit',
            utc='2026-10-02T15:01:00Z',assignment_status='ASSIGNED',activity_role='SURVEY',
            transect_id='T07',direction='UNKNOWN')
        self.run.decide(self.ids('transitions.csv')[:2],reviewer='Oracle reviewer',reason='Transition leg',
            utc='2026-10-02T15:02:00Z',assignment_status='UNASSIGNED',activity_role='TRANSITION')
        self.options_qa = QaOptions()
        self.batch = run_batch_qa(self.run,self.intake,self.options_qa,created_utc='2026-10-02T16:00:00Z')
        self.policy = CompletionPolicy('ORACLE-7',100,0,.02,'Oracle reviewer','Synthetic operational policy',
            '2026-10-02T16:01:00Z')
        history = scope_evidence((),self.batch,self.run.membership,self.ids('ambiguous.csv'),('T05',),
            reviewer='Oracle reviewer',reason='Independent ambiguity truth',utc='2026-10-02T16:02:00Z')
        remaining = (*self.ids('transitions.csv')[2:],*self.ids('loaded_GNSS'))
        history = scope_evidence(history,self.batch,self.run.membership,remaining,(),
            reviewer='Oracle reviewer',reason='Reviewed unrelated retained fixes',utc='2026-10-02T16:03:00Z')
        self.results = build_results(self.batch,self.run,self.intake,self.policy,history,
                                     created_utc='2026-10-02T17:00:00Z')
        self.navigation = prepare_survey_navigation(self.setup,spacing_m=10,project=self.project)

    @staticmethod
    def options(surveyor):
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        return ImportOptions('longitude','latitude','EPSG:4326',
            FieldMappings(timestamp='time',accuracy='accuracy',hdop='hdop'),surveyor)

    def ids(self,filename):
        return tuple(row.observation_id for row in sorted(self.intake.model.raw_observations.values(),key=lambda r:r.source_order)
            if self.intake.model.sources[row.source_id].source_file==filename)

    def export(self,parent,name='survey',navigation=True):
        from transect_navigator.integration.survey_snapshot_export import export_survey_snapshot
        return export_survey_snapshot(self.results,self.batch,self.run,self.intake,Path(parent),name,
            navigation=self.navigation if navigation else None,created_utc='2026-10-02T18:00:00Z')

    def native_before(self):
        return ({key:(v.observations.crs().toWkt(),v.observations.fields().names(),
            [(f.attributes(),bytes(f.geometry().asWkb())) for f in v.observations.getFeatures()])
            for key,v in self.intake.native_recordings.items()},
            [(f.attributes(),bytes(f.geometry().asWkb())) for f in self.planned.getFeatures()],
            self.run.snapshot(),self.batch.as_dict(),self.results.as_dict())

    def test_independent_oracle_state_before_export(self):
        e=self.expected
        self.assertEqual(len(self.intake.model.sources),e['source_count'])
        self.assertEqual(len(self.intake.model.recordings),e['recording_count'])
        self.assertEqual(len(self.intake.model.raw_observations),e['raw_count'])
        self.assertEqual(dict(self.batch.disposition_counts)['ASSIGNED'],e['assigned_count'])
        self.assertEqual(dict(self.batch.disposition_counts)['AMBIGUOUS'],e['ambiguous_count'])
        self.assertEqual(dict(self.batch.disposition_counts)['UNASSIGNED'],e['unassigned_count'])
        self.assertEqual(dict(self.batch.disposition_counts)['TRANSITION'],e['transition_count'])
        self.assertEqual(dict(self.results.summary.status_counts),e['status_counts'])
        self.assertAlmostEqual(self.results.summary.confirmed_progress_m,e['progress_m'],delta=.02)
        self.assertAlmostEqual(self.results.summary.confirmed_corridor_m,e['corridor_m'],delta=.02)
        events=[event for visit in self.batch.visits for event in visit.events]
        self.assertEqual(len(events),e['event_count'])
        self.assertEqual(sum(event.geometry_wkb_hex is None for event in events),1)
        self.assertEqual(self.navigation.prepared.waypoints.featureCount(),e['waypoint_count'])

    def test_export_reopen_counts_status_and_csv(self):
        from transect_navigator.integration.survey_snapshot_export import verify_survey_snapshot
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            metadata=verify_survey_snapshot(target)
            self.assertEqual(metadata['counts']['raw_observations'],94)
            self.assertEqual(metadata['counts']['qa_observations'],85)
            self.assertEqual(metadata['counts']['events'],2)
            self.assertEqual(metadata['status_counts'],self.expected['status_counts'])
            self.assertEqual(metadata['counts']['transitions'],2)
            self.assertEqual(metadata['state'],'CURRENT')
            self.assertEqual(metadata['completion_policy'],asdict(self.policy))
            self.assertEqual(metadata['qa_dependency'],self.batch.dependency_fingerprint)
            self.assertEqual(metadata['results_dependency'],self.results.dependency_fingerprint)
            self.assertEqual(metadata['review_revision'],len(self.run.history))
            self.assertEqual(len(metadata['layers']),42)
            self.assertEqual(metadata['layers']['navigation_waypoints']['count'],108)
            self.assertEqual(metadata['layers']['planned_transects']['count'],8)
            self.assertEqual(metadata['layers']['project_status']['count'],8)
            self.assertEqual(metadata['layers']['qa_observations']['count'],85)
            self.assertEqual(metadata['layers']['assignment_membership']['count'],94)
            self.assertEqual(metadata['layers']['visit_membership']['count'],85)
            self.assertEqual(metadata['layers']['qa_events']['count'],2)
            with (target/'survey_summary.csv').open() as stream:
                summary=list(csv.DictReader(stream))[0]
            self.assertEqual(summary['COMPLETE'],'4')
            self.assertAlmostEqual(float(summary['Project_Progress_pct']),64,delta=.002)
            with (target/'transect_summary.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),8)
            self.assertEqual({r['Transect_ID']:r['Status'] for r in rows},
                             {'T01':'COMPLETE','T02':'COMPLETE','T03':'INCOMPLETE','T04':'INCOMPLETE',
                              'T05':'REVIEW','T06':'COMPLETE','T07':'COMPLETE','T08':'NOT_SURVEYED'})

    def test_every_state_and_exact_membership_provenance_reopened(self):
        from qgis.core import QgsVectorLayer
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            gpkg=target/'survey_analysis.gpkg'
            def rows(name,key):
                layer=QgsVectorLayer(f'{gpkg}|layername={name}',name,'ogr')
                return {f[key]:json.loads(f['record_json']) for f in layer.getFeatures()}
            raw=rows('raw_observation_index','Observation_ID')
            normal=rows('normalized_observations','Observation_ID')
            member=rows('assignment_membership','Observation_ID')
            self.assertEqual(set(raw),set(normal))
            self.assertEqual(set(raw),set(member))
            self.assertEqual(Counter(row['assignment_status'] for row in member.values()),
                {'ASSIGNED':85,'AMBIGUOUS':3,'UNASSIGNED':6})
            self.assertEqual(sum(row['activity_role']=='TRANSITION' for row in member.values()),2)
            self.assertTrue(any(row['time_status']=='missing' for row in normal.values()))
            visits=rows('visits','Visit_ID')
            self.assertEqual(Counter(key for v in visits.values() for key in v['observation_ids']),
                             Counter(key for key,row in member.items() if row['assignment_status']=='ASSIGNED'))
            self.assertEqual(len(rows('assignment_decisions','Decision_ID')),len(self.run.history))
            self.assertEqual(set(rows('qa_observations','Observation_ID')),
                             set(key for key,row in member.items() if row['assignment_status']=='ASSIGNED'))
            self.assertEqual(set(rows('transect_results','Transect_ID')),
                             {f'T{index:02d}' for index in range(1,9)})

    def test_event_metric_geometry_null_and_moving_line(self):
        from qgis.core import QgsVectorLayer,QgsWkbTypes
        from transect_navigator.integration.survey_prepare import crs_roundtrip_equivalent
        from qgis.core import QgsCoordinateReferenceSystem
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            gpkg=target/'survey_analysis.gpkg'
            events=QgsVectorLayer(f'{gpkg}|layername=qa_events','events','ogr')
            self.assertEqual(events.featureCount(),2)
            null_count=0
            for feature in events.getFeatures():
                row=json.loads(feature['record_json'])
                layer=QgsVectorLayer(f"{gpkg}|layername={row['geometry_layer']}",'metric event','ogr')
                matched=[item for item in layer.getFeatures() if item['Event_UID']==row['Event_UID']]
                self.assertEqual(len(matched),1)
                self.assertTrue(crs_roundtrip_equivalent(layer.crs(),
                    QgsCoordinateReferenceSystem(row['analysis_crs_wkt'])))
                geom=matched[0].geometry()
                if row['geometry_wkb_hex'] is None:
                    null_count+=1
                    self.assertTrue(geom.isNull())
                    with sqlite3.connect(gpkg) as db:
                        self.assertIsNone(db.execute(
                            f'SELECT _tn_geometry FROM "{row["geometry_layer"]}" WHERE Event_UID=?',
                            (row['Event_UID'],)).fetchone()[0])
                else:
                    self.assertFalse(geom.isEmpty())
                    self.assertEqual(QgsWkbTypes.geometryType(geom.wkbType()),QgsWkbTypes.GeometryType.LineGeometry)
                    self.assertEqual(bytes(geom.asWkb()).hex(),row['geometry_wkb_hex'])
            self.assertEqual(null_count,1)

    def test_source_copies_checksum_loaded_snapshot_and_z(self):
        from qgis.core import QgsVectorLayer,QgsWkbTypes
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            gpkg=target/'survey_analysis.gpkg'
            with (target/'source_manifest.csv').open() as stream:
                manifest=list(csv.DictReader(stream))
            self.assertEqual(len(manifest),11)
            copied=[r for r in manifest if r['Original_Bytes_State']=='PRESERVED']
            missing=[r for r in manifest if r['Original_Bytes_State']=='UNAVAILABLE_LOADED_LAYER']
            self.assertEqual((len(copied),len(missing)),(10,1))
            for row in copied:
                original=SAMPLE/row['Original_Name']
                self.assertEqual((target/row['Exported_File']).read_bytes(),original.read_bytes())
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),row['SHA256'])
            self.assertEqual(missing[0]['Exported_File'],'')
            self.assertTrue(missing[0]['Snapshot_Digest'])
            rec=next(r for r in self.intake.model.recordings.values()
                     if r.source_id==missing[0]['Source_ID'])
            loaded=QgsVectorLayer(f'{gpkg}|layername={rec.raw_observation_layer}','native Z','ogr')
            self.assertEqual(loaded.featureCount(),2)
            self.assertTrue(QgsWkbTypes.hasZ(loaded.wkbType()))
            self.assertEqual([f['fix'] for f in loaded.getFeatures()],['3D','3D'])

    def test_projected_and_geographic_native_crs_round_trip(self):
        from qgis.core import QgsCoordinateReferenceSystem,QgsFeature,QgsGeometry,QgsVectorLayer
        from transect_navigator.integration.survey_prepare import crs_roundtrip_equivalent
        # Stage 7 native raw snapshots can be mixed CRS in one Survey.
        projected=QgsVectorLayer('Point?crs=EPSG:3857&field=time:string','projected source','memory')
        f=QgsFeature(projected.fields()); f.setGeometry(QgsGeometry.fromWkt('POINT(12000 100)'))
        f.setAttributes(['2026-10-03T00:00:00Z']); projected.dataProvider().addFeatures([f])
        from transect_navigator.integration.survey_intake import ImportOptions
        from transect_navigator.survey.intake import FieldMappings
        imported=self.intake.import_layer(projected,source_file='projected_GNSS',
            options=ImportOptions(mappings=FieldMappings(timestamp='time')))
        self.assertEqual(imported.status,'SUCCESS')
        self.run=self._rebuild_review_after_new_source()
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            record=next(r for r in self.intake.model.recordings.values()
                        if self.intake.model.sources[r.source_id].source_file=='projected_GNSS')
            layer=QgsVectorLayer(f'{target}/survey_analysis.gpkg|layername={record.raw_observation_layer}',
                                 'reopened projected','ogr')
            self.assertTrue(crs_roundtrip_equivalent(layer.crs(),QgsCoordinateReferenceSystem('EPSG:3857')))

    def test_custom_projected_plan_and_unsupported_null_plan_round_trip(self):
        from qgis.core import QgsCoordinateReferenceSystem,QgsFeature,QgsGeometry,QgsVectorLayer
        from transect_navigator.integration.survey_prepare import setup_survey,crs_roundtrip_equivalent
        from transect_navigator.integration.survey_intake import SurveyIntake
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results
        from transect_navigator.integration.survey_snapshot_export import export_survey_snapshot,verify_survey_snapshot
        from transect_navigator.survey.model import SurveyRegistry
        from transect_navigator.survey.review import ContinuityPolicy
        feet=QgsCoordinateReferenceSystem()
        self.assertTrue(feet.createFromProj(
            '+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=us-ft +no_defs'))
        plans=QgsVectorLayer('LineString?field=Transect_ID:string','custom projected','memory')
        plans.setCrs(feet)
        for key,wkt in (('modeled','LINESTRING(0 0,328.0839895 0)'),('unsupported',None)):
            feature=QgsFeature(plans.fields())
            feature.setAttributes([key])
            if wkt:
                feature.setGeometry(QgsGeometry.fromWkt(wkt))
            self.assertTrue(plans.dataProvider().addFeatures([feature])[0])
        setup=setup_survey(plans,'Transect_ID',survey_id='CUSTOM-NULL',name='Custom CRS and null plan',
            registry=SurveyRegistry(),project=self.project)
        intake=SurveyIntake(setup.survey,project=self.project)
        run=start_review(intake,ContinuityPolicy(60,30))
        batch=run_batch_qa(run,intake)
        results=build_results(batch,run,intake,self.policy)
        with TemporaryDirectory() as folder:
            target=export_survey_snapshot(results,batch,run,intake,Path(folder),'custom')
            metadata=verify_survey_snapshot(target)
            self.assertEqual(metadata['counts']['plans'],2)
            self.assertEqual(metadata['counts']['raw_observations'],0)
            self.assertEqual(metadata['status_counts']['REVIEW'],1)
            layer=QgsVectorLayer(f'{target}/survey_analysis.gpkg|layername=planned_transects',
                                 'reopened custom plan','ogr')
            self.assertTrue(crs_roundtrip_equivalent(layer.crs(),feet))
            geom={f['Transect_ID']:f.geometry() for f in layer.getFeatures()}
            self.assertTrue(geom['unsupported'].isNull())
            self.assertEqual(bytes(geom['modeled'].asWkb()),
                bytes(next(f.geometry() for f in plans.getFeatures() if f['Transect_ID']=='modeled').asWkb()))

    def _rebuild_review_after_new_source(self):
        from transect_navigator.integration.survey_review import start_review
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results
        from transect_navigator.survey.review import ContinuityPolicy
        from transect_navigator.survey.results import scope_evidence
        old=self.run.snapshot()
        run=start_review(self.intake,ContinuityPolicy(60,30),created_utc=old['created_utc'])
        for row in old['decisions']:
            run.decide(row['observation_ids'],reviewer=row['reviewer'],reason=row['reason'],utc=row['utc'],
                assignment_status=row['assignment_status'],activity_role=row['activity_role'],
                transect_id=row['transect_id'],direction=row['direction'],action=row['action'])
        self.run=run
        self.batch=run_batch_qa(run,self.intake,self.options_qa,created_utc='2026-10-02T16:00:00Z')
        scopes=scope_evidence((),self.batch,run.membership,self.ids('ambiguous.csv'),('T05',),
            reviewer='R',reason='Ambiguous known',utc='2026-10-02T16:02:00Z')
        extra=[key for key,d in run.membership.items()
               if d.assignment_status=='UNASSIGNED' and d.activity_role!='TRANSITION']
        scopes=scope_evidence(scopes,self.batch,run.membership,extra,(),
            reviewer='R',reason='Unrelated known',utc='2026-10-02T16:03:00Z')
        self.results=build_results(self.batch,run,self.intake,self.policy,scopes,
            created_utc='2026-10-02T17:00:00Z')
        return run

    def test_no_overwrite_and_failed_export_cleanup(self):
        from transect_navigator.integration import survey_snapshot_export as module
        with TemporaryDirectory() as folder:
            first=self.export(folder)
            old=(first/'survey_analysis.gpkg').read_bytes()
            with self.assertRaisesRegex(ValueError,'already exists'):
                self.export(folder)
            self.assertEqual((first/'survey_analysis.gpkg').read_bytes(),old)
            with patch.object(module,'_write_csv',side_effect=OSError('injected output failure')):
                with self.assertRaisesRegex(ValueError,'injected output failure'):
                    self.export(folder,'failed')
            self.assertFalse((Path(folder)/'failed').exists())
            self.assertEqual((first/'survey_analysis.gpkg').read_bytes(),old)

    def test_stale_qa_review_or_policy_rejected_before_package_creation(self):
        from transect_navigator.integration.survey_snapshot_export import export_survey_snapshot
        with TemporaryDirectory() as folder:
            changed=replace(self.results,policy=replace(self.policy,required_support_percent=90))
            with self.assertRaisesRegex(ValueError,'Stale Survey'):
                export_survey_snapshot(changed,self.batch,self.run,self.intake,Path(folder),'stale_policy')
            self.run.decide(self.ids('forward.csv')[:1],reviewer='R',reason='Revise',
                            assignment_status='AMBIGUOUS')
            with self.assertRaisesRegex(ValueError,'Stale QA'):
                export_survey_snapshot(self.results,self.batch,self.run,self.intake,Path(folder),'stale_review')
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_failed_visit_retains_membership_and_explicit_processing_error(self):
        from transect_navigator.integration import survey_qa
        from transect_navigator.integration.survey_qa import run_batch_qa
        from transect_navigator.integration.survey_results import build_results
        from transect_navigator.integration.survey_snapshot_export import export_survey_snapshot,verify_survey_snapshot
        failed_id=self.run.visits[0].visit_id
        original=survey_qa._analyze_visit
        def fail_one(intake,run,visit,*args):
            if visit.visit_id==failed_id:
                raise ValueError('Authored processing failure')
            return original(intake,run,visit,*args)
        with patch.object(survey_qa,'_analyze_visit',side_effect=fail_one):
            failed_batch=run_batch_qa(self.run,self.intake,self.options_qa)
        failed_results=build_results(failed_batch,self.run,self.intake,self.policy,
            self.results.evidence_history)
        with TemporaryDirectory() as folder:
            target=export_survey_snapshot(failed_results,failed_batch,self.run,self.intake,
                Path(folder),'failed_visit')
            metadata=verify_survey_snapshot(target)
            self.assertEqual(metadata['counts']['raw_observations'],94)
            self.assertEqual(metadata['layers']['visit_membership']['count'],85)
            self.assertLess(metadata['counts']['qa_observations'],85)
            self.assertTrue(any(t.qa.errors for t in failed_results.transects))
            from qgis.core import QgsVectorLayer
            layer=QgsVectorLayer(f'{target}/survey_analysis.gpkg|layername=visit_qa','visit QA','ogr')
            row=next(json.loads(f['record_json']) for f in layer.getFeatures() if f['Visit_ID']==failed_id)
            self.assertEqual(row['processing_state'],'FAILED')
            self.assertIn('Authored processing failure',row['error'])

    def test_duplicate_renamed_file_does_not_duplicate_exported_observations(self):
        with TemporaryDirectory() as folder:
            renamed=Path(folder)/'renamed_same_bytes.csv'
            shutil.copyfile(SAMPLE/'forward.csv',renamed)
            attempt=self.intake.import_file(renamed,self.options('S99'))
            self.assertEqual(attempt.status,'DUPLICATE')
            target=self.export(folder)
            with (target/'source_manifest.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))),11)
            self.assertEqual(json.loads((target/'metadata.json').read_text())['counts']['raw_observations'],94)
            from qgis.core import QgsVectorLayer
            layer=QgsVectorLayer(f'{target}/survey_analysis.gpkg|layername=import_attempts','attempts','ogr')
            self.assertTrue(any(json.loads(f['record_json'])['status']=='DUPLICATE' for f in layer.getFeatures()))

    def test_corruption_is_detected_on_standalone_reopen(self):
        from transect_navigator.integration.survey_snapshot_export import verify_survey_snapshot
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            (target/'source_manifest.csv').write_text('corrupt\n')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                verify_survey_snapshot(target)
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            source=next((target/'sources').iterdir())
            source.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                verify_survey_snapshot(target)
        with TemporaryDirectory() as folder:
            target=self.export(folder)
            with sqlite3.connect(target/'survey_analysis.gpkg') as db:
                db.execute('UPDATE assignment_membership SET record_json=? WHERE Observation_ID=(SELECT Observation_ID FROM assignment_membership LIMIT 1)',
                           ('{}',))
            manifest=json.loads((target/'metadata.json').read_text())
            manifest['file_sha256']['survey_analysis.gpkg']=hashlib.sha256(
                (target/'survey_analysis.gpkg').read_bytes()).hexdigest()
            (target/'metadata.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                verify_survey_snapshot(target)
        with TemporaryDirectory() as folder:
            from qgis.core import QgsVectorLayer
            target=self.export(folder)
            layer=QgsVectorLayer(f'{target}/survey_analysis.gpkg|layername=project_status',
                                 'tampered status map','ogr')
            feature=next(layer.getFeatures())
            wrong='REVIEW' if feature['Status']!='REVIEW' else 'COMPLETE'
            self.assertTrue(layer.dataProvider().changeAttributeValues({
                feature.id():{layer.fields().indexFromName('Status'):wrong}}))
            layer=None
            manifest=json.loads((target/'metadata.json').read_text())
            manifest['file_sha256']['survey_analysis.gpkg']=hashlib.sha256(
                (target/'survey_analysis.gpkg').read_bytes()).hexdigest()
            (target/'metadata.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'status map attributes'):
                verify_survey_snapshot(target)

    def test_results_ui_exports_only_current_snapshot(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtTest import QTest
        from transect_navigator.ui.survey_qa_panel import SurveyQaPanel
        from transect_navigator.ui.survey_results_panel import SurveyResultsPanel
        qa=SurveyQaPanel(self.intake,lambda:self.run)
        qa.batch=self.batch
        qa._populate()
        panel=SurveyResultsPanel(qa)
        self.addCleanup(panel.close)
        self.addCleanup(qa.close)
        panel.policy,panel.evidence_history=self.policy,self.results.evidence_history
        panel.recompute()
        self.assertTrue(panel.export_button.isEnabled())
        with TemporaryDirectory() as folder:
            with patch('transect_navigator.ui.survey_results_panel.QFileDialog.getExistingDirectory',return_value=folder), \
                 patch('transect_navigator.ui.survey_results_panel.QInputDialog.getText',return_value=('ui_export',True)):
                QTest.mouseClick(panel.export_button,Qt.MouseButton.LeftButton)
            self.assertTrue((Path(folder)/'ui_export'/'survey_analysis.gpkg').is_file())
            self.assertIn('reopened verified',panel.status.text())
        self.run.decide(self.ids('forward.csv')[:1],reviewer='R',reason='Revised',
                        assignment_status='AMBIGUOUS')
        panel.refresh_validity()
        self.assertFalse(panel.export_button.isEnabled())

    def test_source_geometry_attributes_decisions_qa_results_unmodified(self):
        before=self.native_before()
        with TemporaryDirectory() as folder:
            self.export(folder)
        self.assertEqual(before,self.native_before())
        self.assertFalse(self.project.mapLayers())

    def test_optional_navigation_absent_is_explicit(self):
        from transect_navigator.integration.survey_snapshot_export import verify_survey_snapshot
        with TemporaryDirectory() as folder:
            target=self.export(folder,navigation=False)
            meta=verify_survey_snapshot(target)
            self.assertNotIn('navigation_waypoints',meta['layers'])
            self.assertNotIn('navigation_run',meta['layers'])

    def test_export_runtime_and_size_fixture(self):
        with TemporaryDirectory() as folder:
            start=time.perf_counter()
            target=self.export(folder)
            elapsed=time.perf_counter()-start
            size=sum(path.stat().st_size for path in target.rglob('*') if path.is_file())
            sources=sum(path.stat().st_size for path in (target/'sources').iterdir())
            self.assertGreater(size,sources)
            self.assertLess(elapsed,60)
            print(f'\nStage7 export fixture: {elapsed:.3f}s, {size} bytes total, {sources} source bytes')
