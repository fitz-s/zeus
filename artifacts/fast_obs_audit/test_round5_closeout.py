"""Standard-library deterministic tests. No production DB or weather calls."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone

PATH=Path(__file__).with_name('round5_closeout.py')
spec=importlib.util.spec_from_file_location('round5_closeout',PATH)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)


def observation(**changes):
    row=dict(id=1,city='Tokyo',station_id='RJTT',source_channel='jma_amedas_temperature',
             publish_ts_utc='2026-10-05T08:00:00Z',value_native=20.,unit='C',
             fetched_at_utc='2026-10-05T08:02:00.123456Z',raw_report='{"value_native":20}')
    row.update(changes);return row


def command(**changes):
    row=dict(command_id='c1',intent_kind='ENTRY',state='FILLED',command_created_at='2026-09-25T00:00:00Z',
        position_id='p1',position_phase='settled',confirmed_shares='39.6',confirmed_trade_count=2,
        fill_evidence='CONFIRMED_TRADE_FACT',fill_evidence_conflict=False,evidence_flags=[])
    row.update(changes);return row


class AuditTests(unittest.TestCase):
    def test_naive_db_timestamp_rejected(self):
        with self.assertRaises(ValueError):a.instant('2026-10-05T01:00:00')
    def test_cdt_log_converted(self):
        self.assertEqual(a.instant('2026-10-05 04:00:00,123',local_log=True).isoformat(),'2026-10-05T09:00:00.123000+00:00')
    def test_dst_fold_rejected(self):
        with self.assertRaises(ValueError):a.instant('2026-11-01 01:30:00',local_log=True)
    def test_nonfinite_rejected(self):
        for value in ('NaN','Infinity','-Infinity'):
            with self.subTest(value=value),self.assertRaises(ValueError):a.decimal(value)
    def test_fractional_millis_no_float_roundtrip(self):
        self.assertEqual(a.millis('1970-01-01T00:00:00.123999Z'),123)
    def test_quantiles_and_invalid(self):
        s=a.stats([0,10,20,30,40,-1,float('nan')])
        self.assertEqual(s['n'],5);self.assertEqual(s['p50_ms'],20);self.assertEqual(s['p90_ms'],36)
        self.assertEqual(s['discarded_negative_or_nonfinite'],2)
    def test_empty_quantile_not_zero(self):self.assertIsNone(a.stats([])['p50_ms'])
    def test_revision_changes_on_a_b_a(self):
        aa=observation();bb=observation(id=2,value_native=21.,fetched_at_utc='2026-10-05T08:03:00Z')
        ac=observation(id=3,fetched_at_utc='2026-10-05T08:04:00Z')
        self.assertEqual(len({a.observation_ref(x)['identity'] for x in (aa,bb,ac)}),3)
    def test_equivalent_utc_spelling_same_reference(self):
        r=observation();s=observation(publish_ts_utc='2026-10-05T10:00:00+02:00')
        self.assertEqual(a.observation_ref(r),a.observation_ref(s))
    def test_millisecond_collision_does_not_merge_revision(self):
        r=observation();s=observation(id=2,fetched_at_utc='2026-10-05T08:02:00.123457Z')
        self.assertNotEqual(a.observation_ref(r),a.observation_ref(s))
    def test_zero_order_snapshot_not_position_arithmetic(self):
        self.assertEqual(a.class_command(command(fill_evidence_conflict=True)),'FILL_VS_TERMINAL_ZERO_CONFLICT')
    def test_partial_cancel_is_not_no_fill(self):
        self.assertEqual(a.class_command(command(state='CANCELLED',latest_order_states=['CANCEL_CONFIRMED'])),'CONFIRMED_EXECUTION_VENUE_TERMINAL_REMAINDER')
    def test_local_cancellation_is_not_venue_terminal_proof(self):
        self.assertEqual(a.class_command(command(state='CANCELLED',latest_order_states=['LIVE'])),'CONFIRMED_EXECUTION_REMAINDER_REQUIRES_PROOF')
    def test_residual_shares_not_called_missing_cash(self):
        s=a.summarize_commands([command()]);self.assertIn('residual',s['cash_closure'])
    def test_post_cutoff_voided_detected(self):
        s=a.summarize_commands([command(position_phase='voided'),command(command_id='c0',position_phase='voided',command_created_at='2026-06-01T00:00:00Z')])
        self.assertEqual(s['post_cutoff_commands'],1);self.assertEqual(len(s['post_cutoff_confirmed_fill_voided_rows']),1)
    def test_duplicate_command_rejected(self):
        with self.assertRaises(ValueError):a.summarize_commands([command(),command()])
    def test_command_vs_position_grain(self):
        s=a.summarize_commands([command(),command(command_id='c2')]);self.assertEqual(s['commands'],2);self.assertEqual(s['distinct_bound_positions'],1)
    def test_latest_trade_fact_not_sum_versions(self):
        rr=[dict(trade_id='t',venue_order_id='v',observed_at='2026-06-01T00:00:00Z',local_sequence=1,trade_fact_id=1,state='MATCHED'),dict(trade_id='t',venue_order_id='v',observed_at='2026-06-01T00:01:00Z',local_sequence=2,trade_fact_id=2,state='CONFIRMED')]
        self.assertEqual(len(a.latest_facts(rr,('trade_id','venue_order_id'),'trade_fact_id')),1)
    def test_read_only_connection_cannot_insert(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'db';c=sqlite3.connect(p);c.execute('CREATE TABLE x(a)');c.commit();c.close()
            with a.open_ro(p) as c:
                with self.assertRaises(sqlite3.OperationalError):c.execute('INSERT INTO x VALUES(1)')
    def test_half_open_window_iso_and_sqlite_spellings(self):
        c=sqlite3.connect(':memory:');c.execute('CREATE TABLE prints(t TEXT)')
        c.executemany('INSERT INTO prints VALUES(?)',[('2026-10-04 08:59:59',),('2026-10-04T09:00:00+00:00',),('2026-10-05 08:59:59',),('2026-10-05T09:00:00Z',)])
        n=c.execute('SELECT COUNT(*) FROM prints WHERE julianday(t)>=julianday(?) AND julianday(t)<julianday(?)',('2026-10-04T09:00:00Z','2026-10-05T09:00:00Z')).fetchone()[0]
        self.assertEqual(n,2);c.close()
    def test_credential_scrub(self):
        x=a.scrub({'authorization':'x','token_id':'123','payload':'{"private_key":"x"}','url':'https://e/x?apiKey=abc&v=1'})
        self.assertEqual(x['token_id'],'123');self.assertNotIn('abc',x['url']);self.assertEqual(x['payload']['private_key'],'[REDACTED]')
    def test_no_legacy_publish_time_pair(self):
        r=observation();base=a.millis(r['fetched_at_utc'])
        es=[{'stage':'SOURCE_COMMITTED','city':'Tokyo','input_identity':{'source':r['source_channel'],'observed_at_utc':r['publish_ts_utc'],'value_native':20},'world_committed_at_ms':base+1}]
        report=a.trace_distributions([r],es,[])
        self.assertEqual(report['full_ack_chains'],0);self.assertEqual(report['residual_counts']['MISSING_EXACT_REVISION_SOURCE_COMMIT'],1)
    def test_full_exact_chain_and_canonical_ack(self):
        r=observation();ref=a.observation_ref(r);b=a.millis(r['fetched_at_utc'])
        es=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+1,recorded_at_ms=b+1),dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash='q',readiness_id='r1',posterior_ready_at_ms=b+10,recorded_at_ms=b+10),dict(stage='WAKE_RECEIVED',wake_id='w1',posterior_identity_hash='q',wake_received_at_ms=b+12,recorded_at_ms=b+12),dict(stage='Q_SERVED',posterior_identity_hash='q',q_served_at_ms=b+20,recorded_at_ms=b+20),dict(stage='VENUE_ACK_OBSERVED',q_version='q',command_id='c',event_id='ack',recorded_at_ms=b+30)]
        ack=[dict(event_id='ack',command_id='c',event_type='SUBMIT_ACKED',occurred_at='2026-10-05T08:02:00.153Z')]
        report=a.trace_distributions([r],es,ack)
        self.assertEqual(report['full_ack_chains'],1);self.assertEqual(report['hops']['receipt_to_ack']['p50_ms'],30)
        self.assertEqual(report['hops']['posterior_to_wake']['p50_ms'],2)
        self.assertEqual(a.trace_distributions([r],es,[])['full_ack_chains'],0)
    def test_wake_without_identity_cannot_complete_chain(self):
        r=observation();ref=a.observation_ref(r);b=a.millis(r['fetched_at_utc'])
        es=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+1,recorded_at_ms=b+1),dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash='q',readiness_id='r1',posterior_ready_at_ms=b+10,recorded_at_ms=b+10),dict(stage='WAKE_RECEIVED',posterior_identity_hash='q',wake_received_at_ms=b+12,recorded_at_ms=b+12),dict(stage='Q_SERVED',posterior_identity_hash='q',q_served_at_ms=b+20,recorded_at_ms=b+20),dict(stage='VENUE_ACK_OBSERVED',q_version='q',command_id='c',event_id='ack',recorded_at_ms=b+30)]
        ack=[dict(event_id='ack',command_id='c',event_type='SUBMIT_ACKED',occurred_at='2026-10-05T08:02:00.153Z')]
        result=a.trace_distributions([r],es,ack)
        self.assertEqual(result['ack_lineages'],1);self.assertEqual(result['full_ack_chains'],0)
        self.assertEqual(result['hops']['posterior_to_wake']['n'],0)
    def test_unlinked_ack_is_not_outage_or_keep(self):
        r=observation();ref=a.observation_ref(r);b=a.millis(r['fetched_at_utc'])
        es=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+1,recorded_at_ms=b+1),dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash='q',readiness_id='r1',posterior_ready_at_ms=b+10,recorded_at_ms=b+10),dict(stage='Q_SERVED',posterior_identity_hash='q',q_served_at_ms=b+20,recorded_at_ms=b+20)]
        result=a.trace_distributions([r],es,[])
        self.assertEqual(result['residual_counts']['Q_SERVED_NO_CANONICAL_ACK_OR_NO_ACTION_PROOF'],1)
    def test_log_gzip_dedicated_marker(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'logs').mkdir()
            event=dict(stage='Q_SERVED',recorded_at_ms=a.millis('2026-10-05T09:00:00Z'))
            with gzip.open(root/'logs/zeus-ingest.log.1.gz','wt') as f:f.write('2026-10-05 04:00:00 INFO OBSERVATION_REACTION_TRACE '+json.dumps(event)+'\n')
            es,coverage=a.read_logs(root,a.instant('2026-10-05T08:00:00Z'),a.instant('2026-10-05T10:00:00Z'))
            self.assertEqual(len(es),1);self.assertEqual(coverage[0]['malformed_trace_rows'],0)
    def test_legacy_exact_receipt_refuses_prior_a_b_a_revision(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE observation_prints(id INTEGER,city TEXT,station_id TEXT,source_channel TEXT,publish_ts_utc TEXT,value_native REAL,unit TEXT,fetched_at_utc TEXT,raw_report TEXT)')
        original=observation();again=observation(id=3,fetched_at_utc='2026-10-05T08:04:00Z')
        for row in (original,again):c.execute('INSERT INTO observation_prints VALUES (?,?,?,?,?,?,?,?,?)',tuple(row.values()))
        identity={'source':original['source_channel'],'observed_at_utc':original['publish_ts_utc'],'value_native':20.}
        es=[dict(stage='SOURCE_COMMITTED',city='Tokyo',station_id='RJTT',input_identity=identity,response_received_at_ms=a.millis(again['fetched_at_utc'])),dict(stage='POSTERIOR_READY',city='Tokyo',input_identity=identity,posterior_ready_at_ms=a.millis('2026-10-05T08:05:00Z'))]
        report=a.reconstruct_legacy_references(c,[again],es)
        self.assertEqual(es[0]['observation_ref']['id'],3)
        self.assertNotIn('observation_ref',es[1]);self.assertEqual(report['AMBIGUOUS_LEGACY_REVISION'],1)
        c.close()
    def test_legacy_unique_full_row_can_be_reconstructed(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE observation_prints(id INTEGER,city TEXT,station_id TEXT,source_channel TEXT,publish_ts_utc TEXT,value_native REAL,unit TEXT,fetched_at_utc TEXT,raw_report TEXT)')
        row=observation();c.execute('INSERT INTO observation_prints VALUES (?,?,?,?,?,?,?,?,?)',tuple(row.values()))
        es=[dict(stage='POSTERIOR_READY',city='Tokyo',input_identity={'source':row['source_channel'],'observed_at_utc':row['publish_ts_utc'],'value_native':20.},posterior_ready_at_ms=a.millis('2026-10-05T08:05:00Z'))]
        report=a.reconstruct_legacy_references(c,[row],es)
        self.assertEqual(es[0]['observation_ref']['id'],1);c.close()
    def test_read_only_native_fact_capture_preserves_source(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'trade.db';c=sqlite3.connect(p)
            c.executescript("""
            CREATE TABLE venue_commands(command_id TEXT,snapshot_id TEXT,position_id TEXT,intent_kind TEXT,state TEXT,created_at TEXT,side TEXT,market_id TEXT,token_id TEXT);
            CREATE TABLE venue_command_events(command_id TEXT,event_id TEXT,event_type TEXT,occurred_at TEXT,sequence_no INTEGER);
            CREATE TABLE venue_trade_facts(trade_fact_id INTEGER,command_id TEXT,trade_id TEXT,venue_order_id TEXT,state TEXT,filled_size TEXT,observed_at TEXT,local_sequence INTEGER);
            CREATE TABLE venue_order_facts(fact_id INTEGER,command_id TEXT,venue_order_id TEXT,state TEXT,matched_size TEXT,remaining_size TEXT,observed_at TEXT,local_sequence INTEGER);
            CREATE TABLE position_current(position_id TEXT,phase TEXT,shares REAL,chain_shares REAL);
            CREATE TABLE position_events(position_id TEXT,event_id TEXT,event_type TEXT,phase_after TEXT,sequence_no INTEGER);
            CREATE TABLE executable_market_snapshots(snapshot_id TEXT,condition_id TEXT,selected_outcome_token_id TEXT);
            CREATE TABLE settlement_commands(command_id TEXT,condition_id TEXT,state TEXT);
            INSERT INTO venue_commands VALUES('086d130a613546f2','s','p','ENTRY','CANCELLED','2026-06-01T00:00:00Z','BUY','m','tok');
            INSERT INTO venue_trade_facts VALUES(1,'086d130a613546f2','t','v','CONFIRMED','2.5','2026-07-13T22:02:00Z',1);
            INSERT INTO venue_order_facts VALUES(1,'086d130a613546f2','v','CANCEL_CONFIRMED','0','0','2026-06-01T00:01:00Z',1);
            INSERT INTO position_current VALUES('p','voided',0,NULL);
            INSERT INTO position_events VALUES('p','e','FIXTURE','voided',1);
            INSERT INTO executable_market_snapshots VALUES('s','real-condition','tok');
            INSERT INTO settlement_commands VALUES('redeem','real-condition','REDEEM_CONFIRMED');
            """);c.commit();c.close();before=p.read_bytes()
            with a.open_ro(p) as conn:rows,details=a.live_command_rows(conn)
            self.assertEqual(p.read_bytes(),before);self.assertEqual(rows[0]['confirmed_shares'],'2.5')
            self.assertTrue(rows[0]['fill_evidence_conflict']);self.assertEqual(len(details['positions']),1)
            self.assertEqual(details['settlement_commands'][0]['command_id'],'redeem')
            self.assertEqual(details['settlement_binding_residuals'],[])
    def test_wake_receipt_joins_only_through_its_own_publication(self):
        r=observation();ref=a.observation_ref(r);b=a.millis(r['fetched_at_utc'])
        base=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+1,recorded_at_ms=b+1),dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash='q',readiness_id='r1',posterior_ready_at_ms=b+10,recorded_at_ms=b+10),dict(stage='WAKE_RECEIVED',wake_id='w1',wake_received_at_ms=b+12,recorded_at_ms=b+12),dict(stage='Q_SERVED',posterior_identity_hash='q',q_served_at_ms=b+20,recorded_at_ms=b+20),dict(stage='VENUE_ACK_OBSERVED',q_version='q',command_id='c',event_id='ack',recorded_at_ms=b+30)]
        ack=[dict(event_id='ack',command_id='c',event_type='SUBMIT_ACKED',occurred_at='2026-10-05T08:02:00.153Z')]
        self.assertEqual(a.trace_distributions([r],copy.deepcopy(base),ack)['full_ack_chains'],0)
        own=base+[dict(stage='WAKE_PUBLISHED',wake_id='w1',posterior_identity_hash='q',recorded_at_ms=b+11)]
        report=a.trace_distributions([r],own,ack)
        self.assertEqual(report['full_ack_chains'],1);self.assertEqual(report['hops']['posterior_to_wake']['p50_ms'],2)
        foreign=base+[dict(stage='WAKE_PUBLISHED',wake_id='w1',posterior_identity_hash='later',recorded_at_ms=b+11)]
        self.assertEqual(a.trace_distributions([r],foreign,ack)['full_ack_chains'],0)
        both=own+[dict(stage='WAKE_PUBLISHED',wake_id='w1',posterior_identity_hash='later',recorded_at_ms=b+11)]
        self.assertEqual(a.trace_distributions([r],both,ack)['full_ack_chains'],0)
    def test_indexed_legacy_reconstruction_matches_full_scan(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE observation_prints(id INTEGER,city TEXT,station_id TEXT,source_channel TEXT,publish_ts_utc TEXT,value_native REAL,unit TEXT,fetched_at_utc TEXT,raw_report TEXT)')
        rows=[observation(),observation(id=2,value_native=21.,fetched_at_utc='2026-10-05T08:03:00Z'),
              observation(id=3,fetched_at_utc='2026-10-05T08:04:00Z'),observation(id=4,publish_ts_utc='2026-10-05T10:00:00+02:00',value_native=22.,fetched_at_utc='2026-10-05T08:05:00Z'),
              observation(id=5,city='Osaka',fetched_at_utc='2026-10-05T08:06:00Z')]
        for row in rows:c.execute('INSERT INTO observation_prints VALUES (?,?,?,?,?,?,?,?,?)',tuple(row.values()))
        def ident(v,at='2026-10-05T08:00:00Z'):return {'source':'jma_amedas_temperature','observed_at_utc':at,'value_native':v}
        cases=[(20.,'2026-10-05T08:02:30Z'),(20.,'2026-10-05T08:05:00Z'),(21.,'2026-10-05T08:03:30Z'),(22.,'2026-10-05T08:06:00Z'),(23.,'2026-10-05T08:06:00Z'),(21.,'2026-10-05T08:02:59Z')]
        es=[dict(stage='POSTERIOR_READY',city='Tokyo',input_identity=ident(v),posterior_ready_at_ms=a.millis(at)) for v,at in cases]
        es+=[dict(stage='SOURCE_COMMITTED',city=x['city'],station_id='RJTT',input_identity=ident(x['value_native'],x['publish_ts_utc']),response_received_at_ms=a.millis(x['fetched_at_utc'])) for x in rows]
        report=a.reconstruct_legacy_references(c,rows,es)
        def full(e):
            i=e['input_identity'];obs=a.instant(i['observed_at_utc']);v=a.decimal(i['value_native'])
            if e['stage']=='SOURCE_COMMITTED':
                m=[x for x in rows if x['city']==e['city'] and x['station_id']==e['station_id'] and x['source_channel']==i['source'] and a.instant(x['publish_ts_utc'])==obs and a.decimal(x['value_native'])==v and a.millis(x['fetched_at_utc'])==e['response_received_at_ms']]
            else:
                m=[x for x in rows if x['city']==e['city'] and x['source_channel']==i['source'] and a.instant(x['publish_ts_utc'])==obs and a.decimal(x['value_native'])==v and a.millis(x['fetched_at_utc'])<=e['posterior_ready_at_ms']]
            return m[0]['id'] if len(m)==1 else None
        self.assertEqual([e.get('observation_ref',{}).get('id') for e in es],[full(e) for e in es])
        self.assertEqual(report['AMBIGUOUS_LEGACY_REVISION'],1)
        c.close()
    def test_nonfinite_evidence_value_is_named_not_a_dump_failure(self):
        with tempfile.TemporaryDirectory() as t:
            a.dump(Path(t)/'x.json',{'payload_json':'{"last_monitor_edge": NaN}','v':float('inf')})
            out=json.loads((Path(t)/'x.json').read_text())
        self.assertEqual(out['payload_json']['last_monitor_edge'],{'nonfinite_float':'nan'})
        self.assertEqual(out['v'],{'nonfinite_float':'inf'})
    def test_kma_event_joins_only_its_named_posterior(self):
        kev=dict(event_id='k1',source='day0_extreme_updated_trigger',available_at='2026-10-05T08:00:52Z',received_at='2026-10-05T08:00:56Z')
        derived=dict(kev,event_id='k2',source='day0_posterior_advanced')
        a0=a.millis(kev['available_at'])
        es=[dict(stage='POSTERIOR_READY',input_ref={'kma_event_id':'k1'},posterior_identity_hash='q',readiness_id='r',posterior_ready_at_ms=a0+9000,recorded_at_ms=a0+9000),
            dict(stage='WAKE_PUBLISHED',wake_id='w',posterior_identity_hash='q',recorded_at_ms=a0+9001),
            dict(stage='WAKE_RECEIVED',wake_id='w',wake_received_at_ms=a0+9002,recorded_at_ms=a0+9002),
            dict(stage='Q_SERVED',posterior_identity_hash='q',q_served_at_ms=a0+9010,recorded_at_ms=a0+9010),
            dict(stage='VENUE_ACK_OBSERVED',q_version='q',command_id='c',event_id='ack',recorded_at_ms=a0+9020),
            # Content alone, or a derived re-dispatch, never binds a KMA posterior.
            dict(stage='POSTERIOR_READY',input_ref={'kma_event_id':'k2'},posterior_identity_hash='z',posterior_ready_at_ms=a0+1,recorded_at_ms=a0+1)]
        ack=[dict(event_id='ack',command_id='c',event_type='SUBMIT_ACKED',occurred_at='2026-10-05T08:01:01.020Z')]
        r=a.trace_distributions([],es,ack,[kev,derived])
        self.assertEqual(r['kma_source_events'],1);self.assertEqual(r['kma_events_with_exact_posterior'],1)
        self.assertEqual(r['hops']['kma_available_to_received']['p50_ms'],4000)
        self.assertEqual(r['hops']['kma_available_to_posterior']['p50_ms'],9000)
        self.assertEqual(r['hops']['receipt_to_ack']['p50_ms'],9020)
        self.assertEqual(r['hops']['receipt_to_world']['n'],0)
        self.assertEqual(r['full_ack_chains'],1);self.assertEqual(r['paired'][0]['input_kind'],'KMA_EVENT')
        none=a.trace_distributions([],es[:1],[],[dict(kev,event_id='k9')])
        self.assertEqual(none['residual_counts'],{'KMA_EVENT_NO_EXACT_POSTERIOR_READY':1})
    def test_wrong_sql_identifier_rejected(self):
        with self.assertRaises(ValueError):a.identifier('x; DROP TABLE y')


if __name__=='__main__':unittest.main(verbosity=2)
