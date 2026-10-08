"""Operational rules tested independently of QGIS/providers and UI."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from tests import test_survey_qa as qa_fixture
from transect_navigator.survey.qa import BatchQa, QaOptions, aggregate_transect
from transect_navigator.survey.review import Disposition
from transect_navigator.survey.results import (CompletionPolicy, aggregate_results, scope_evidence)


class SurveyResultsDomainTests(unittest.TestCase):
    def setUp(self):
        self.helper = qa_fixture.SurveyQaAggregationTests()
        self.survey = SimpleNamespace(survey_id='S',plan_revision='1',plans=(SimpleNamespace(transect_id='T'),))
        self.policy = CompletionPolicy('full',100,0,.001,'R','Explicit test policy','2026-01-01T00:00:00Z')

    def batch(self,visits):
        qa = aggregate_transect('T',100,100,tuple(visits),self.helper.options)
        return BatchQa('survey-visit-qa-1','B','S','R',1,'fingerprint','engine','{}',
            QaOptions(statistics=self.helper.options),'dependency','2026-01-01T00:00:00Z','unchanged',tuple(visits),(qa,),())

    def result(self,batch,policy=None):
        return aggregate_results(self.survey,batch,{},policy or self.policy)

    def test_explicit_policy_validates_finite_ranges_and_identity(self):
        for changes in ({'policy_id':''},{'selected_by':''},{'reason':''},{'selected_utc':'2026-01-01'},
                {'required_support_percent':0},{'required_support_percent':101},
                {'endpoint_tolerance_m':-1},{'numerical_tolerance_m':1},
                {'required_support_percent':True},{'endpoint_tolerance_m':float('nan')},
                {'version':'invented'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                replace(self.policy,**changes)

    def test_full_endpoint_support_and_reverse(self):
        for stations in ([0,50,100],[100,50,0]):
            result = self.result(self.batch([self.helper.visit('a',stations,[0]*3)]))
            self.assertEqual(result.transects[0].final_status,'COMPLETE')
            self.assertTrue(result.transects[0].start_supported)
            self.assertTrue(result.transects[0].end_supported)

    def test_endpoint_fixes_separated_by_rejected_gap_do_not_count(self):
        visit = self.helper.visit('a',[0,50,100],[0]*3)
        options = replace(self.helper.options,coverage_max_station_gap_m=20)
        batch = replace(self.batch([visit]),options=QaOptions(statistics=options))
        # Deliberately retain an otherwise high support value to isolate endpoint gate.
        result = self.result(batch)
        self.assertFalse(result.transects[0].start_supported)
        self.assertFalse(result.transects[0].end_supported)
        self.assertEqual(result.transects[0].final_status,'INCOMPLETE')

    def test_midline_support_cannot_satisfy_endpoints(self):
        batch = self.batch([self.helper.visit('a',[10,50,90],[0]*3)])
        result = self.result(batch,replace(self.policy,required_support_percent=80))
        self.assertEqual(result.transects[0].final_status,'INCOMPLETE')

    def test_unknown_chronology_is_review_and_not_measured_zero(self):
        batch = self.batch([self.helper.visit('a',[0,100],[0,0],chronological=False)])
        result = self.result(batch)
        self.assertEqual(result.transects[0].final_status,'REVIEW')
        self.assertIsNone(result.transects[0].qa.progress_support.supported_length_m)
        self.assertIsNone(result.summary.accepted_track_length_m)
        self.assertEqual(result.summary.confirmed_progress_m,0)
        self.assertEqual(result.summary.unknown_support_count,1)
        self.assertEqual(result.summary.processing_state,'PARTIAL')

    def test_unknown_visit_does_not_hide_behind_known_full_visit(self):
        batch = self.batch([self.helper.visit('a',[0,100],[0,0]),
                           self.helper.visit('b',[0,50],[0,0],chronological=False)])
        result = self.result(batch)
        self.assertEqual(result.transects[0].final_status,'REVIEW')
        self.assertEqual(result.summary.confirmed_progress_m,100)
        self.assertEqual(result.summary.unknown_support_count,1)

    def test_unsupported_length_does_not_shrink_denominator(self):
        batch = self.batch([])
        qa = replace(batch.transects[0],planned_length_m=None,summary=None,progress_support=None,errors=('unsupported',))
        result = self.result(replace(batch,transects=(qa,)))
        self.assertEqual(result.transects[0].final_status,'REVIEW')
        self.assertIsNone(result.summary.total_planned_length_m)
        self.assertIsNone(result.summary.progress_lower_bound_percent)
        self.assertIsNone(result.summary.corridor_lower_bound_percent)
        self.assertEqual(result.summary.unknown_length_count,1)

    def test_processing_failure_takes_precedence_over_complete_support(self):
        batch = self.batch([self.helper.visit('a',[0,100],[0,0])])
        qa = replace(batch.transects[0],errors=('failed second visit',))
        result = self.result(replace(batch,transects=(qa,)))
        self.assertEqual(result.transects[0].final_status,'REVIEW')

    def test_no_visits_is_not_surveyed_with_known_denominator(self):
        result = self.result(self.batch([]))
        self.assertEqual(result.transects[0].final_status,'NOT_SURVEYED')
        self.assertEqual(result.summary.total_planned_length_m,100)
        self.assertEqual(result.summary.progress_lower_bound_percent,0)
        self.assertEqual(result.summary.unknown_support_count,0)

    def test_duplicate_missing_or_foreign_plan_rows_rejected(self):
        batch = self.batch([])
        for rows in ((), batch.transects*2, (replace(batch.transects[0],transect_id='other'),)):
            with self.assertRaisesRegex(ValueError,'exactly one'):
                self.result(replace(batch,transects=rows))

    def test_corrupt_support_above_plan_rejected(self):
        batch = self.batch([self.helper.visit('a',[0,100],[0,0])])
        qa = batch.transects[0]
        for number in (101,-1,float('inf')):
            with self.assertRaisesRegex(ValueError,'Support exceeds'):
                self.result(replace(batch,transects=(replace(qa,progress_support=replace(qa.progress_support,supported_length_m=number)),)))

    def test_unrelated_unassigned_remains_in_survey_unresolved_ledger(self):
        batch = self.batch([])
        membership = {'O':Disposition('O')}
        history = scope_evidence((),batch,membership,['O'],[],reviewer='R',reason='Unrelated recording')
        result = aggregate_results(self.survey,batch,membership,self.policy,history)
        self.assertEqual(result.transects[0].final_status,'NOT_SURVEYED')
        self.assertEqual(result.summary.unresolved_observation_count,1)
        self.assertEqual(result.summary.unscoped_observation_count,0)
        self.assertEqual(result.summary.processing_state,'FINISHED_WITH_REVIEW')

    def test_scope_requires_existing_unresolved_exact_membership_and_revision(self):
        batch = self.batch([])
        membership = {'O':Disposition('O'),'A':Disposition('A','ASSIGNED','SURVEY',transect_id='T')}
        for ids,plans in (([],['T']),(['missing'],['T']),(['A'],['T']),(['O'],['other'])):
            with self.assertRaises(ValueError):
                scope_evidence((),batch,membership,ids,plans,reviewer='R',reason='Review')
        history = scope_evidence((),batch,membership,['O'],['T'],reviewer='R',reason='Review')
        with self.assertRaisesRegex(ValueError,'Invalid evidence-scope'):
            aggregate_results(self.survey,batch,membership,self.policy,(replace(history[0],revision=2),))

    def test_old_scope_history_retained_without_automatic_migration(self):
        batch = self.batch([])
        membership = {'O':Disposition('O')}
        history = scope_evidence((),batch,membership,['O'],[],reviewer='R',reason='Unrelated')
        new_batch = replace(batch,review_fingerprint='new review')
        result = aggregate_results(self.survey,new_batch,membership,self.policy,history)
        self.assertEqual(result.transects[0].final_status,'REVIEW')
        self.assertEqual(result.evidence_history,history)
        updated = scope_evidence(history,new_batch,membership,['O'],[],reviewer='R',reason='Reconfirmed unrelated')
        self.assertEqual(updated[0],history[0])
        self.assertEqual(aggregate_results(self.survey,new_batch,membership,self.policy,updated).transects[0].final_status,'NOT_SURVEYED')

    def test_transition_does_not_trigger_unresolved_review(self):
        result = aggregate_results(self.survey,self.batch([]),{'O':Disposition('O',activity_role='TRANSITION')},self.policy)
        self.assertEqual(result.transects[0].final_status,'NOT_SURVEYED')
        self.assertEqual(result.summary.unassigned_observation_count,1)
        self.assertEqual(result.summary.unresolved_observation_count,0)
