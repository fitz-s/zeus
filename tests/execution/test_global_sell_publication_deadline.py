# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Authority basis: isolated expired publication-claim producer prevention.
"""Expiry refuses publication admission; it never releases existing owners."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from tests.test_exit_safety import conn, _seed_canonical_position_identity  # noqa: F401


NOW=datetime(2026,10,1,18,7,11,tzinfo=timezone.utc)


@pytest.fixture
def publication(conn,monkeypatch):
    from src.execution import exit_lifecycle
    _seed_canonical_position_identity(conn,position_id='publication-position',
        token_id='publication-yes',no_token_id='publication-no',direction='buy_no',shares=2)
    conn.execute("UPDATE position_current SET condition_id='publication-condition' WHERE position_id='publication-position'")
    conn.commit()
    position=SimpleNamespace(trade_id='publication-position',direction='buy_no',
        token_id='publication-yes',no_token_id='publication-no',strategy_key='center_buy',
        state='day0_window',env='live',effective_exposure=lambda:SimpleNamespace(shares=2))
    obligation={'schema_version':4,'position_id':position.trade_id,'held_token_id':'publication-no',
        'scope_identity':'scope','generation':'generation','request_id':'request',
        'material_identity':'material','attempt_identity':'attempt',
        'selection_epoch_identity':'epoch','sell_book_witness_identity':'book',
        'debt_event_id':'debt','monitor_event_id':'monitor','state':'ARMED',
        'armed_at':NOW.isoformat(),'completion_deadline_at':(NOW+timedelta(seconds=30)).isoformat()}
    monkeypatch.setattr(exit_lifecycle,'_utcnow',lambda:NOW)
    return position,obligation


@pytest.mark.parametrize('deadline',[None,'','not-a-clock','2026-10-01T18:07:41',
    ' 2026-10-01T18:07:41+00:00','9999-12-31T23:59:59-12:00',
    NOW.isoformat(),(NOW-timedelta(seconds=2)).isoformat()])
def test_invalid_or_expired_attempt_never_creates_publication_claim(conn,publication,deadline):
    from src.execution import exit_lifecycle
    position,obligation=publication
    obligation['completion_deadline_at']=deadline
    assert not exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    assert conn.execute('SELECT COUNT(*) FROM position_events').fetchone()[0]==0
    assert conn.execute('SELECT COUNT(*) FROM venue_commands').fetchone()[0]==0


def test_deadline_is_rechecked_at_claim_append(conn,publication,monkeypatch):
    from src.execution import exit_lifecycle
    position,obligation=publication
    clock=iter((NOW,NOW+timedelta(seconds=30)))
    monkeypatch.setattr(exit_lifecycle,'_utcnow',lambda:next(clock))
    assert not exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    assert conn.execute('SELECT COUNT(*) FROM position_events').fetchone()[0]==0


@pytest.mark.parametrize('changed',['attempt_identity','request_id','generation',
    'selection_epoch_identity','sell_book_witness_identity','completion_deadline_at'])
def test_existing_publisher_cannot_be_borrowed_or_replaced(conn,publication,changed):
    from src.execution import exit_lifecycle
    from src.execution.exit_safety import global_sell_reauction_publish_claim_blocks_exit_command
    position,obligation=publication
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    conn.commit()
    before=[tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,dict(obligation))
    other={**obligation,changed:((NOW+timedelta(seconds=60)).isoformat()
        if changed=='completion_deadline_at' else 'another-'+changed)}
    assert not exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,other)
    assert before==[tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn,position.trade_id)


def test_expired_existing_publisher_stays_fenced_without_inactivity_proof(conn,publication,monkeypatch):
    from src.execution import exit_lifecycle
    from src.execution.exit_safety import global_sell_reauction_publish_claim_blocks_exit_command
    position,obligation=publication
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    conn.commit()
    before=[tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    monkeypatch.setattr(exit_lifecycle,'_utcnow',lambda:NOW+timedelta(minutes=1))
    assert not exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn,position.trade_id)
    assert before==[tuple(row) for row in conn.execute('SELECT * FROM position_events')]


def test_deadline_is_rechecked_before_idempotent_claim_reuse(conn,publication,monkeypatch):
    from src.execution import exit_lifecycle
    from src.execution.exit_safety import global_sell_reauction_publish_claim_blocks_exit_command
    position,obligation=publication
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    conn.commit()
    clock=iter((NOW,NOW+timedelta(seconds=30)))
    monkeypatch.setattr(exit_lifecycle,'_utcnow',lambda:next(clock))
    assert not exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    assert conn.execute('SELECT COUNT(*) FROM position_events').fetchone()[0]==1
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn,position.trade_id)


def test_expired_debt_requests_fresh_family_before_any_write_lease(conn,publication,monkeypatch):
    from src.engine import cycle_runtime
    from src.execution import executor,exit_lifecycle
    position,obligation=publication
    obligation['completion_deadline_at']=(NOW-timedelta(seconds=2)).isoformat()
    monkeypatch.setattr(exit_lifecycle,'needs_global_sell_snapshot_reauction',lambda *_args:True)
    monkeypatch.setattr(exit_lifecycle,'latest_held_sell_reauction_obligation',lambda *_args,**_kwargs:obligation)
    monkeypatch.setattr(exit_lifecycle,'_pending_exit_no_order_waits_for_liquidity',lambda *_args,**_kwargs:False)
    monkeypatch.setattr(executor,'_canonical_trade_write_lease',lambda *_args,**_kwargs:pytest.fail('expired attempt reached writer lease'))
    prepared=[]
    def prepare(observed):
        assert observed is position and not conn.in_transaction
        prepared.append(position.trade_id)
        return True
    monkeypatch.setattr(cycle_runtime,'_request_current_global_family_preparation',prepare)
    refusal=exit_lifecycle._recover_global_sell_snapshot_reauction_debt(position,conn=conn,
        requester=lambda *_args:pytest.fail('expired attempt reached its publisher'))
    assert refusal=='PUBLICATION_DEADLINE_EXPIRED_FAMILY_PREPARATION_REQUESTED'
    assert prepared==[position.trade_id]
    assert conn.execute('SELECT COUNT(*) FROM position_events').fetchone()[0]==0


def test_delayed_different_attempt_cannot_replace_an_owned_canonical_claim(conn,publication):
    from src.execution import exit_lifecycle
    position,obligation=publication
    delayed={**obligation,'attempt_identity':'delayed-old-attempt'}
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    conn.commit()
    assert not exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,delayed)
    current=json.loads(conn.execute("SELECT payload_json FROM position_events WHERE event_type='EXIT_RETRY_RELEASED' ORDER BY sequence_no DESC LIMIT 1").fetchone()[0])
    assert current['held_sell_reauction_obligation']['attempt_identity']=='attempt'


@pytest.mark.parametrize('damage',['array','wrong_token','wrong_schema','wrong_scope_type','missing_obligation'])
def test_ambiguous_existing_publisher_cannot_be_replaced(conn,publication,damage):
    from src.execution import exit_lifecycle
    from src.execution.exit_safety import global_sell_reauction_publish_claim_blocks_exit_command
    position,obligation=publication
    old=dict(obligation)
    if damage=='wrong_token':old['held_token_id']='another-token'
    elif damage=='wrong_schema':old['schema_version']=3
    elif damage=='wrong_scope_type':old['scope_identity']={'unknown':'scope'}
    payload={'global_sell_reauction_status':'publish_claimed',
        'release_reason':'GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED','held_sell_reauction_obligation':old}
    if damage=='array':payload=[]
    elif damage=='missing_obligation':payload.pop('held_sell_reauction_obligation')
    conn.execute('''INSERT INTO position_events
        (event_id,position_id,event_version,sequence_no,event_type,occurred_at,
         phase_before,phase_after,strategy_key,source_module,payload_json,env)
        VALUES ('ambiguous-claim',?,1,1,'EXIT_RETRY_RELEASED',?,'day0_window','day0_window',
            'center_buy','tests.execution.test_global_sell_publication_deadline',?,'live')''',
        (position.trade_id,NOW.isoformat(),json.dumps(payload)))
    conn.commit()
    before=[tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn,position.trade_id)
    assert not exit_lifecycle._record_global_sell_reauction_publish_claim(conn,position,obligation)
    assert before==[tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn,position.trade_id)


def test_unbound_ack_cannot_retire_an_existing_publisher(conn, publication):
    from src.execution import exit_lifecycle
    from src.execution.exit_safety import global_sell_reauction_publish_claim_blocks_exit_command
    position, obligation = publication
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn, position, obligation)
    conn.commit()
    before = [tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    assert not exit_lifecycle.record_global_sell_reauction_reserved(conn, position)
    assert before == [tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn, position.trade_id)


def test_exact_ack_cannot_retire_same_payload_successor_event(conn, publication):
    from src.execution import exit_lifecycle
    from src.execution.exit_safety import global_sell_reauction_publish_claim_blocks_exit_command
    position, obligation = publication
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn, position, obligation)
    conn.commit()
    original = conn.execute("SELECT event_id, payload_json FROM position_events ORDER BY sequence_no DESC LIMIT 1").fetchone()
    conn.execute("""INSERT INTO position_events
        (event_id,position_id,event_version,sequence_no,event_type,occurred_at,
         phase_before,phase_after,strategy_key,source_module,payload_json,env)
        VALUES ('successor-claim',?,1,2,'EXIT_RETRY_RELEASED',?,'day0_window','day0_window',
            'center_buy','tests.execution.test_global_sell_publication_deadline',?,'live')""",
        (position.trade_id, NOW.isoformat(), original['payload_json']))
    conn.commit()
    assert not exit_lifecycle.record_global_sell_reauction_reserved(
        conn, position, expected_claim_event_id=original['event_id'], expected_obligation=obligation,
    )
    assert conn.execute('SELECT count(*) FROM position_events').fetchone()[0] == 2
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn, position.trade_id)


def test_failed_prepared_publication_preserves_exact_owner(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.execution.exit_safety import global_sell_reauction_publish_claim_blocks_exit_command
    from tests.test_exit_safety import _seed_pending_lineage_debt, _prepared_reauction_requester_for_test
    position = _seed_pending_lineage_debt(conn, lineage={
        'selection_epoch_identity': 'epoch', 'sell_book_witness_identity': 'book',
    })
    requested = []
    requester = _prepared_reauction_requester_for_test(
        conn, monkeypatch, lambda *_args: requested.append(True) and False,
    )
    assert not exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position, conn=conn, requester=requester,
    )
    assert requested == [True]
    assert global_sell_reauction_publish_claim_blocks_exit_command(conn, position.trade_id)
    assert not exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position, conn=conn, requester=lambda *_args, **_kwargs: pytest.fail('owned publisher was borrowed'),
    )
    assert conn.execute("SELECT count(*) FROM position_events WHERE venue_status='publish_claimed'").fetchone()[0] == 1


@pytest.mark.parametrize('changed', ['token', 'direction'])
def test_exact_ack_rechecks_canonical_held_identity(conn, publication, changed):
    from src.execution import exit_lifecycle
    position, obligation = publication
    assert exit_lifecycle._record_global_sell_reauction_publish_claim(conn, position, obligation)
    conn.commit()
    claim_event_id = conn.execute('SELECT event_id FROM position_events ORDER BY sequence_no DESC LIMIT 1').fetchone()[0]
    if changed == 'token':
        conn.execute("UPDATE position_current SET no_token_id='successor-token' WHERE position_id=?", (position.trade_id,))
    else:
        conn.execute("UPDATE position_current SET direction='buy_yes' WHERE position_id=?", (position.trade_id,))
    conn.commit()
    assert not exit_lifecycle.record_global_sell_reauction_reserved(
        conn, position, expected_claim_event_id=claim_event_id, expected_obligation=obligation,
    )
    assert conn.execute('SELECT count(*) FROM position_events').fetchone()[0] == 1


@pytest.mark.parametrize('owned', [False, True])
def test_pending_lineage_cannot_retire_invalid_armed_or_ambiguous_owner(conn, monkeypatch, owned):
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle
    from tests.test_exit_safety import _seed_pending_lineage_debt, _seed_post_debt_monitor
    position = _seed_pending_lineage_debt(conn)
    original = conn.execute('SELECT payload_json FROM position_events ORDER BY sequence_no DESC LIMIT 1').fetchone()[0]
    payload = json.loads(original)
    payload['held_sell_reauction_obligation']['state'] = 'ARMED'
    if owned:
        payload['global_sell_reauction_status'] = 'publish_claimed'
        payload['held_sell_reauction_obligation']['scope_identity'] = {'invalid': 'scope'}
    conn.execute("""INSERT INTO position_events
        (event_id,position_id,event_version,sequence_no,event_type,occurred_at,
         phase_before,phase_after,strategy_key,source_module,payload_json,env)
        VALUES ('invalid-pending-owner',?,1,2,'EXIT_RETRY_RELEASED',?,'day0_window','day0_window',
            'center_buy','tests.execution.test_global_sell_publication_deadline',?,'live')""",
        (position.trade_id, NOW.isoformat(), json.dumps(payload)))
    conn.commit()
    _seed_post_debt_monitor(conn, position.trade_id)
    before = [tuple(row) for row in conn.execute('SELECT * FROM position_events')]
    monkeypatch.setattr(cycle_runtime, '_request_current_global_family_preparation',
                        lambda *_args: pytest.fail('invalid owner reached family preparation'))
    assert not exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position, conn=conn, requester=lambda *_args, **_kwargs: pytest.fail('invalid owner reached preparation'),
    )
    assert before == [tuple(row) for row in conn.execute('SELECT * FROM position_events')]


def test_unclaimed_monitor_ack_checks_owner_inside_write_transaction(conn, publication, monkeypatch):
    from src.execution import exit_lifecycle, exit_safety
    position, obligation = publication
    position._held_sell_reauction_obligation = dict(obligation)
    conn.execute("""INSERT INTO position_events
        (event_id,position_id,event_version,sequence_no,event_type,occurred_at,
         phase_before,phase_after,strategy_key,source_module,payload_json,env)
        VALUES ('unclaimed-monitor',?,1,1,'MONITOR_REFRESHED',?,'day0_window','day0_window',
            'center_buy','tests.execution.test_global_sell_publication_deadline',?,'live')""",
        (position.trade_id, NOW.isoformat(), json.dumps({'held_sell_reauction_obligation': obligation})))
    conn.commit()
    seen = []
    guard = exit_safety.global_sell_reauction_publish_claim_blocks_exit_command
    def transaction_guard(read_conn, position_id):
        seen.append(read_conn.in_transaction)
        return guard(read_conn, position_id)
    monkeypatch.setattr(exit_safety, 'global_sell_reauction_publish_claim_blocks_exit_command', transaction_guard)
    assert exit_lifecycle.record_global_sell_reauction_reserved(conn, position)
    assert seen == [True]
    assert not conn.in_transaction
    payload = json.loads(conn.execute('SELECT payload_json FROM position_events ORDER BY sequence_no DESC LIMIT 1').fetchone()[0])
    assert payload['global_sell_reauction_status'] == 'durable_wake_reserved'


@pytest.mark.parametrize('successor', [False, True])
def test_unclaimed_ack_binds_published_attempt_across_monitor_refresh(conn, publication, successor):
    from src.execution import exit_lifecycle
    position, published_obligation = publication
    current = dict(published_obligation)
    if successor:
        current.update(attempt_identity='new-attempt', request_id='new-request',
                       completion_deadline_at=(NOW + timedelta(minutes=1)).isoformat())
    position._held_sell_reauction_obligation = current
    conn.execute("""INSERT INTO position_events
        (event_id,position_id,event_version,sequence_no,event_type,occurred_at,
         phase_before,phase_after,strategy_key,source_module,payload_json,env)
        VALUES ('latest-monitor',?,1,1,'MONITOR_REFRESHED',?,'day0_window','day0_window',
            'center_buy','tests.execution.test_global_sell_publication_deadline',?,'live')""",
        (position.trade_id, NOW.isoformat(), json.dumps({'held_sell_reauction_obligation': current})))
    conn.commit()
    assert exit_lifecycle.record_global_sell_reauction_reserved(
        conn, position, expected_obligation=published_obligation,
    ) is (not successor)
    assert not conn.in_transaction
    if successor:
        assert conn.execute('SELECT COUNT(*) FROM position_events').fetchone()[0] == 1
        assert exit_lifecycle.latest_held_sell_reauction_obligation(conn, position) == current
    else:
        row = conn.execute('SELECT payload_json FROM position_events ORDER BY sequence_no DESC LIMIT 1').fetchone()
        assert json.loads(row[0])['global_sell_reauction_status'] == 'durable_wake_reserved'
