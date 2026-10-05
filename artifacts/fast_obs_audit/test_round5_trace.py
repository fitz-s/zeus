"""Tests the exact proposed trace source, with no daemon import or venue access."""
import copy
from datetime import datetime,timezone
import importlib.util
import logging
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import json

PATH=Path(__file__).resolve().parents[2]/'src/runtime/observation_reaction_trace.py'
spec=importlib.util.spec_from_file_location('round5_trace',PATH)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def events():
    identity={'source':'x','observed_at_utc':'2026-10-05T08:00:00Z','value_native':20}
    return [dict(stage='SOURCE_COMMITTED',city='Tokyo',station_id='RJTT',source_channel='x',input_identity=identity,response_received_at_ms=1,world_committed_at_ms=2),
        dict(stage='POSTERIOR_READY',city='Tokyo',posterior_identity_hash='q',input_identity=identity,posterior_ready_at_ms=3),
        dict(stage='Q_SERVED',city='Tokyo',posterior_identity_hash='q',input_identity=identity,q_served_at_ms=4),
        dict(stage='VENUE_ACK_OBSERVED',q_version='q',command_id='c',event_id='ack',venue_ack_at_ms=5)]


class TraceTests(unittest.TestCase):
    def test_old_unique_trace_remains_observed_not_full(self):
        r=m.completed_trace(events(),posterior_identity_hash='q')
        self.assertEqual(r['status'],'OBSERVED_COMPLETE');self.assertFalse(r['all_hops_observed'])
    def test_two_same_content_commits_not_latest_wins(self):
        e=events();extra=copy.deepcopy(e[0]);extra.update(response_received_at_ms=0,world_committed_at_ms=1);e.append(extra)
        r=m.completed_trace(e,posterior_identity_hash='q')
        self.assertEqual(r['status'],'AMBIGUOUS_OBSERVATION_REVISION');self.assertIsNone(r['venue_ack_at_ms'])
    def test_rotated_duplicate_line_not_second_revision(self):
        e=events();e.append(copy.deepcopy(e[0]))
        self.assertEqual(m.completed_trace(e,posterior_identity_hash='q')['status'],'OBSERVED_COMPLETE')
    def test_receipt_after_commit_rejected(self):
        e=events();e[0]['response_received_at_ms']=9
        self.assertEqual(m.completed_trace(e,posterior_identity_hash='q')['status'],'SOURCE_CLOCK_ORDER_VIOLATION')
    def test_foreign_q_ack_rejected(self):
        e=events();e[3]['q_version']='other'
        self.assertIsNone(m.completed_trace(e,posterior_identity_hash='q')['venue_ack_at_ms'])
    def test_foreign_city_source_rejected(self):
        e=events();e[0]['city']='London'
        self.assertEqual(m.completed_trace(e,posterior_identity_hash='q')['status'],'INCOMPLETE')
    def test_future_source_not_pulled_back(self):
        e=events();e[0]['world_committed_at_ms']=8
        self.assertEqual(m.completed_trace(e,posterior_identity_hash='q')['status'],'INCOMPLETE')
    def test_all_hops_require_wake_readiness_and_revision(self):
        e=events();ref={'identity':'a'*64};e[0]['observation_ref']=ref;e[1]['observation_ref']=ref;e[1]['readiness_id']='r'
        e.append(dict(stage='WAKE_RECEIVED',wake_id='w',posterior_identity_hash='q',wake_received_at_ms=3))
        r=m.completed_trace(e,posterior_identity_hash='q');self.assertTrue(r['all_hops_observed'])
    def test_different_revision_never_binds_even_same_content(self):
        e=events();e[0]['observation_ref']={'identity':'b'*64};e[1]['observation_ref']={'identity':'a'*64}
        self.assertEqual(m.completed_trace(e,posterior_identity_hash='q')['status'],'INCOMPLETE')
    def test_telemetry_sink_failure_does_not_throw(self):
        with patch.object(m._LOG,'info',side_effect=RuntimeError('sink failed')):
            self.assertEqual(m.emit_stage('Q_SERVED')['stage'],'Q_SERVED')
    def test_stage_time_is_actual_not_caller_prediction(self):
        with patch.object(m.time,'time_ns',return_value=1700000000123456789):
            r=m.emit_stage('WAKE_RECEIVED',wake_received_at_ms=1)
        self.assertEqual(r['wake_received_at_ms'],1700000000123)
        self.assertEqual(r['trace_schema_version'],2)
    def test_missing_readiness_table_is_nonfatal(self):
        c=sqlite3.connect(':memory:')
        self.assertEqual(m._readiness_reference(c,('Tokyo','2026-10-05','high'),1),{});c.close()
    def test_exact_readiness_pointer_not_family_latest(self):
        c=sqlite3.connect(':memory:');c.execute('CREATE TABLE readiness_state(readiness_id TEXT,computed_at TEXT,dependency_json TEXT,city TEXT,target_local_date TEXT,temperature_metric TEXT,status TEXT)')
        for rid,pid in [('right',1),('wrong',2)]:
            c.execute('INSERT INTO readiness_state VALUES(?,?,?,?,?,?,?)',(rid,'2026-10-05T08:00:00Z',json.dumps({'dependencies':[{'role':'soft_anchor_posterior','posterior_id':pid}]}),'Tokyo','2026-10-05','high','READY'))
        self.assertEqual(m._readiness_reference(c,('Tokyo','2026-10-05','high'),1)['readiness_id'],'right');c.close()
    def test_print_ref_missing_world_is_explicit(self):
        c=sqlite3.connect(':memory:')
        r=m._unique_print_reference(c,city='Tokyo',state={},computed_at='2026-10-05T08:00:00Z')
        self.assertEqual(r,(None,'WORLD_NOT_ATTACHED'));c.close()
    def test_print_reference_asof_and_aba(self):
        c=sqlite3.connect(':memory:');c.execute("ATTACH DATABASE ':memory:' AS world")
        c.execute('CREATE TABLE world.observation_prints(id INTEGER,city TEXT,station_id TEXT,source_channel TEXT,publish_ts_utc TEXT,value_native REAL,unit TEXT,fetched_at_utc TEXT,raw_report TEXT)')
        base=(1,'Tokyo','RJTT','x','2026-10-05T08:00:00Z',20.,'C','2026-10-05T08:01:00Z','{}')
        c.execute('INSERT INTO world.observation_prints VALUES(?,?,?,?,?,?,?,?,?)',base)
        state={'source':'x','observed_at_utc':'2026-10-05T08:00:00Z','value_native':20.}
        ref,status=m._unique_print_reference(c,city='Tokyo',state=state,computed_at='2026-10-05T08:02:00Z')
        self.assertEqual(ref['id'],1)
        c.execute('INSERT INTO world.observation_prints VALUES(?,?,?,?,?,?,?,?,?)',(3,*base[1:7],'2026-10-05T08:03:00Z','{}'))
        self.assertEqual(m._unique_print_reference(c,city='Tokyo',state=state,computed_at='2026-10-05T08:04:00Z')[1],'AMBIGUOUS_INPUT_REVISION')
        self.assertEqual(m._unique_print_reference(c,city='Tokyo',state=state,computed_at='2026-10-05T08:02:00Z')[0]['id'],1)
        c.close()


if __name__=='__main__':unittest.main(verbosity=2)
