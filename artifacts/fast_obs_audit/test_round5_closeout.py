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
    def test_boundary_crossing_completion_counted_then_censored(self):
        r=observation();ref=a.observation_ref(r);b=a.millis(r['fetched_at_utc']);end=b+1000
        es=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+500,recorded_at_ms=b+500),
            dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash='q',posterior_ready_at_ms=end+5000,recorded_at_ms=end+5000),
            dict(stage='Q_SERVED',posterior_identity_hash='q',q_served_at_ms=end+6000,recorded_at_ms=end+6000)]
        done=a.trace_distributions([r],copy.deepcopy(es),[],cohort_end_ms=end,follow_until_ms=end+7200000)
        self.assertEqual(done['hops']['world_to_posterior']['n'],1);self.assertEqual(done['hops']['world_to_posterior']['p50_ms'],end+4500-b)
        self.assertEqual(done['hops']['receipt_to_first_valid_q']['p50_ms'],end+6000-b)
        self.assertEqual(done['censored']['world_to_posterior']['n'],0)
        cut=a.trace_distributions([r],copy.deepcopy(es),[],cohort_end_ms=end,follow_until_ms=end+3000)
        self.assertEqual(cut['hops']['world_to_posterior']['n'],0);self.assertEqual(cut['censored']['world_to_posterior']['n'],1)
        self.assertEqual(cut['censored']['world_to_posterior']['outstanding_age_at_follow_until']['p50_ms'],end+3000-(b+500))
        self.assertEqual(cut['hops']['receipt_to_world']['n'],1)
        self.assertEqual(cut['censored_source_revisions_without_lineage']['n'],1)
        self.assertEqual(cut['censored_source_revisions_without_lineage']['outstanding_age_from_receipt_at_follow_until']['p50_ms'],end+3000-b)
        self.assertEqual(done['censored_source_revisions_without_lineage']['n'],0)
        # A canonical ACK before follow_until whose trace witness lands after it stays censored, and is counted as such.
        acked=es+[dict(stage='VENUE_ACK_OBSERVED',q_version='q',command_id='c',event_id='ack',recorded_at_ms=end+9000)]
        ack=[dict(event_id='ack',command_id='c',event_type='SUBMIT_ACKED',occurred_at=a.datetime.fromtimestamp((end+7000)/1000,a.UTC).isoformat())]
        late=a.trace_distributions([r],copy.deepcopy(acked),ack,cohort_end_ms=end,follow_until_ms=end+8000)
        self.assertEqual(late['hops']['q_to_ack']['n'],0);self.assertEqual(late['censored']['q_to_ack']['n'],1)
        self.assertEqual(late['ack_trace_witness'],{'canonical_acks_read':1,'with_venue_ack_observed_trace':0})
        self.assertEqual(a.trace_distributions([r],copy.deepcopy(acked),ack,cohort_end_ms=end,follow_until_ms=end+10000)['hops']['q_to_ack']['p50_ms'],1000)
        # Censored is not superseded: with no posterior rows read, the disposition says no family reads this city.
        later=a.trace_distributions([r],copy.deepcopy(es),[],cohort_end_ms=end,follow_until_ms=end+3000,posteriors=[],zones={'Tokyo':'Asia/Tokyo'})
        self.assertEqual(later['print_disposition_counts'],{'OUTSIDE_SCOPE':1})
    def test_fan_out_counts_revisions_and_lineages(self):
        p1=observation();p2=observation(id=2,value_native=21.,fetched_at_utc='2026-10-05T08:03:00Z');p3=observation(id=3,value_native=22.,fetched_at_utc='2026-10-05T08:04:00Z')
        es=[]
        for row,hashes in ((p1,('h1','h2','h2')),(p2,('h3',)),(p3,())):
            ref=a.observation_ref(row);b=a.millis(row['fetched_at_utc'])
            es.append(dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+1,recorded_at_ms=b+1))
            es+=[dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash=h,posterior_ready_at_ms=b+10+i,recorded_at_ms=b+10+i) for i,h in enumerate(hashes)]
        fan=a.trace_distributions([p1,p2,p3],es,[])['fan_out']['WORLD_PRINT']
        self.assertEqual(fan['source_revisions_with_lineage'],2);self.assertEqual(fan['posterior_lineages'],3)
        self.assertEqual(fan['lineages_per_source_revision'],{'1':1,'2':1})
    def test_absent_ready_is_not_supersession(self):
        zones={'Tokyo':'Asia/Tokyo'};p=observation();ref=a.observation_ref(p);b=a.millis(p['fetched_at_utc'])
        newer=observation(id=2,value_native=21.,fetched_at_utc='2026-10-05T08:12:00Z');older=observation(id=0,value_native=19.,fetched_at_utc='2026-10-05T07:52:00Z')
        es=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+100,recorded_at_ms=b+100)]
        def post(pid,print_id,cut,date='2026-10-05'):
            return dict(posterior_id=pid,posterior_identity_hash='h%d'%pid,city='Tokyo',target_date=date,temperature_metric='high',
                computed_at=a.datetime.fromtimestamp(cut/1000,a.UTC).isoformat(),day0_current_temperature_input_ref=json.dumps({'print_id':print_id}))
        def disposition(posts):
            r=a.trace_distributions([p],copy.deepcopy(es),[],posteriors=posts,prints=[newer,older],zones=zones,follow_until_ms=b+3600000)
            self.assertEqual(r['residual_counts']['NO_EXACT_REVISION_POSTERIOR_READY'],1)
            return r['print_dispositions'][0]
        # A newer print merely existing is not supersession; nor is a family reader that consumed an older revision.
        self.assertEqual(disposition([post(9,0,b+600000)])['disposition'],'UNEXPLAINED')
        # The newer print consumed by a reader whose cut precedes this commit could not have seen this print.
        self.assertEqual(disposition([post(9,2,b+99)])['disposition'],'PENDING')
        # The newer print consumed for another family date is not this family's successor.
        self.assertEqual(disposition([post(9,2,b+600000,'2026-10-06')])['disposition'],'OUTSIDE_SCOPE')
        hit=disposition([post(9,2,b+600000)])
        self.assertEqual((hit['disposition'],hit['successor_print_id'],hit['consuming_posterior_id']),('SUPERSEDED_BY',2,9))
        self.assertEqual(disposition([post(9,1,b+600000)])['disposition'],'CONSUMED_NO_EXACT_READY_EVENT')
        self.assertEqual(a.trace_distributions([p],copy.deepcopy(es),[])['print_dispositions'][0]['disposition'],'POSTERIOR_SIDECAR_UNAVAILABLE')
        # A cut equal to the commit millisecond does not prove the reader had this print.
        self.assertEqual(disposition([post(9,2,b+100)])['disposition'],'PENDING')
        # An earlier observation reader, then a later reader with no observation input: not PENDING.
        mixed=disposition([post(9,0,b+50),dict(post(10,0,b+700000),day0_current_temperature_input_ref=None)])
        self.assertEqual((mixed['disposition'],mixed['later_family_inputs']),('UNEXPLAINED',{'NO_OBSERVATION_INPUT':1}))
        # A higher rowid the reader ranks lower (a delayed older report) is not a successor.
        stale=observation(id=4,value_native=18.,publish_ts_utc='2026-10-05T07:40:00Z',fetched_at_utc='2026-10-05T08:12:00Z')
        r=a.trace_distributions([p],copy.deepcopy(es),[],posteriors=[post(9,4,b+600000)],prints=[stale],zones=zones)['print_dispositions'][0]
        self.assertEqual((r['disposition'],r['later_family_inputs']),('UNEXPLAINED',{'jma_amedas_temperature':1}))
        self.assertNotIn('successor_print_id',r)
        # The reader kept an earlier revision that outranks this late arrival: named, not superseded.
        ahead=observation(id=0,value_native=19.,publish_ts_utc='2026-10-05T08:10:00Z',fetched_at_utc='2026-10-05T08:01:00Z')
        r=a.trace_distributions([p],copy.deepcopy(es),[],posteriors=[post(9,0,b+600000)],prints=[ahead],zones=zones)['print_dispositions'][0]
        self.assertEqual((r['disposition'],r['reason'],r['outranking_print_id']),('UNEXPLAINED','OUTRANKED_BY_EARLIER_REVISION',0))
        # A METAR the reader cannot date has no family: never classified by publication date.
        metar=observation(source_channel='aviationweather_metar',raw_report='METAR RJTT AUTO 20/12')
        mref=a.observation_ref(metar)
        r=a.trace_distributions([metar],[dict(stage='SOURCE_COMMITTED',observation_ref=mref,world_committed_at_ms=b+100,recorded_at_ms=b+100)],[],
            posteriors=[post(9,2,b+600000)],prints=[newer],zones=zones)['print_dispositions'][0]
        self.assertEqual((r['disposition'],r['reason']),('UNEXPLAINED','READER_CLOCK_UNRESOLVED'))
    def test_wake_received_after_first_q_counts_only_unconditionally(self):
        r=observation();ref=a.observation_ref(r);b=a.millis(r['fetched_at_utc'])
        es=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+1,recorded_at_ms=b+1),
            dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash='q',readiness_id='r',posterior_ready_at_ms=b+10,recorded_at_ms=b+10),
            dict(stage='WAKE_PUBLISHED',wake_id='w1',posterior_identity_hash='q',wake_published_at_ms=b+11,recorded_at_ms=b+11),
            dict(stage='Q_SERVED',posterior_identity_hash='q',q_served_at_ms=b+20,recorded_at_ms=b+20),
            dict(stage='WAKE_RECEIVED',wake_id='w1',wake_received_at_ms=b+25,recorded_at_ms=b+25),
            dict(stage='WAKE_PUBLISHED',wake_id='w2',posterior_identity_hash='z',wake_published_at_ms=b+30,recorded_at_ms=b+30),
            dict(stage='WAKE_PUBLISHED',wake_id='w3',posterior_identity_hash='z',wake_published_at_ms=b+5000,recorded_at_ms=b+5000),
            dict(stage='WAKE_RECEIVED',wake_id='w9',wake_received_at_ms=b+40,recorded_at_ms=b+40)]
        rep=a.trace_distributions([r],es,[],cohort_end_ms=b+1000,follow_until_ms=b+9000)
        self.assertEqual(rep['hops']['posterior_to_wake_before_first_q']['n'],0);self.assertEqual(rep['hops']['posterior_to_wake']['n'],0)
        self.assertEqual(rep['residual_counts']['WAKE_RECEIPT_IDENTITY_OR_CLOCK_MISSING'],1)
        self.assertEqual(rep['hops']['wake_published_to_received']['n'],1);self.assertEqual(rep['hops']['wake_published_to_received']['p50_ms'],14)
        self.assertEqual(rep['wake_transport'],{'published_in_cohort':2,'received':2,'intersect':1,'published_after_cohort_end':1,'received_without_read_publication':1})
        self.assertEqual(rep['censored']['wake_published_to_received']['n'],1)
        self.assertEqual(rep['censored']['wake_published_to_received']['outstanding_age_at_follow_until']['p50_ms'],9000-30)
    def test_negative_input_cut_ordering_is_counted_not_dropped(self):
        rows=[observation(),observation(id=2,value_native=21.,fetched_at_utc='2026-10-05T08:03:00Z')];es=[];posts=[]
        for row,h,lag in ((rows[0],'h1',-400),(rows[1],'h2',300)):
            ref=a.observation_ref(row);b=a.millis(row['fetched_at_utc'])
            es+=[dict(stage='SOURCE_COMMITTED',observation_ref=ref,world_committed_at_ms=b+1000,recorded_at_ms=b+1000),
                dict(stage='POSTERIOR_READY',observation_ref=ref,posterior_identity_hash=h,posterior_ready_at_ms=b+5000,recorded_at_ms=b+5000)]
            posts.append(dict(posterior_id=len(posts)+1,posterior_identity_hash=h,city='Tokyo',target_date='2026-10-05',temperature_metric='high',
                computed_at=a.datetime.fromtimestamp((b+1000+lag)/1000,a.UTC).isoformat()))
        d=a.trace_distributions(rows,es,[],posteriors=posts)['computed_at_decomposition']
        self.assertEqual(d['lineages'],2)
        self.assertEqual(d['world_commit_to_effective_input_cut']['n'],2);self.assertEqual(d['world_commit_to_effective_input_cut']['negative'],1)
        self.assertEqual(d['world_commit_to_effective_input_cut']['min_ms'],-400)
        self.assertEqual(d['effective_input_cut_to_ready']['max_ms'],4400);self.assertEqual(d['effective_input_cut_to_ready']['negative'],0)
        self.assertEqual(d['ordering_violations'],{'cut_before_receipt':0,'cut_before_world_commit':1,'ready_before_cut':0})
    def test_recorded_at_bisection_selects_publication_not_computed_at(self):
        c=sqlite3.connect(':memory:');c.execute('CREATE TABLE forecast_posteriors(posterior_id INTEGER PRIMARY KEY,computed_at TEXT,recorded_at TEXT)')
        c.executemany('INSERT INTO forecast_posteriors VALUES(?,?,?)',[(10,'2026-10-05T07:00:00Z','2026-10-05 07:59:59'),
            (11,'2026-10-05T06:00:00Z','2026-10-05 08:00:00'),(13,'2026-10-05T08:30:00Z','2026-10-05 08:59:59'),(14,'2026-10-05T08:31:00Z','2026-10-05 09:00:00')])
        lo,hi=a.first_posterior_at(c,a.instant('2026-10-05T08:00:00Z')),a.first_posterior_at(c,a.instant('2026-10-05T09:00:00Z'))
        self.assertEqual((lo,hi),(11,14));self.assertEqual(a.first_posterior_at(c,a.instant('2026-10-06T00:00:00Z')),15);c.close()


if __name__=='__main__':unittest.main(verbosity=2)
