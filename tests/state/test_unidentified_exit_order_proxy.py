# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Authority basis: native weak-order-proxy conservation RED and bounded repair.
"""No anonymous order aggregate may become a second economic execution."""
from __future__ import annotations
from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.state import fill_dedup as fills
from src.execution import command_recovery as recovery
from src.execution import exit_lifecycle

AT = "2026-10-01T18:01:42+00:00"
MARKER = "UNAVAILABLE_EXIT_ORDER_FACT_PROXY:"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def case():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript('''
      CREATE TABLE venue_commands(command_id TEXT PRIMARY KEY, position_id TEXT, intent_kind TEXT,
        token_id TEXT, side TEXT, size REAL, price REAL, venue_order_id TEXT, envelope_id TEXT,
        state TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE venue_order_facts(fact_id INTEGER PRIMARY KEY, command_id TEXT, venue_order_id TEXT,
        state TEXT, matched_size TEXT, source TEXT, observed_at TEXT, raw_payload_json TEXT);
      CREATE TABLE venue_submission_envelopes(envelope_id TEXT PRIMARY KEY, order_id TEXT,
        selected_outcome_token_id TEXT, side TEXT, condition_id TEXT, signed_order_hash TEXT,
        raw_request_hash TEXT, canonical_pre_sign_payload_hash TEXT, raw_response_json TEXT,
        trade_ids_json TEXT, transaction_hashes_json TEXT, captured_at TEXT);
      CREATE TABLE venue_trade_facts(trade_fact_id INTEGER PRIMARY KEY, command_id TEXT,
        trade_id TEXT, venue_order_id TEXT, state TEXT, filled_size TEXT, fill_price TEXT,
        source TEXT, observed_at TEXT, venue_timestamp TEXT, ingested_at TEXT,
        local_sequence INTEGER, tx_hash TEXT, raw_payload_json TEXT);
    ''')
    c.execute("INSERT INTO venue_commands VALUES ('cmd','position','EXIT','token','SELL',12.5,.07,'order','pre','ACKED',?,?)", (AT, AT))
    response = dict(orderID="order", status="MATCHED", success=True, makingAmount="12.5", takingAmount="0.875",
                    _v2_matched_size="12.5", _v2_fill_price="0.07",
                    _venue_response_contract="POLYMARKET_CLOB_V2_HUMAN_SUBMIT_AMOUNTS")
    envelope = dict(schema_version=1, order_id="order", side="SELL", selected_outcome_token_id="token",
                    condition_id="condition", signed_order_hash="a"*64, raw_request_hash="b"*64,
                    canonical_pre_sign_payload_hash="c"*64, captured_at=AT, raw_response_json=_json(response),
                    trade_ids=[], transaction_hashes=[])
    for name in ("pre", "response"):
        c.execute("INSERT INTO venue_submission_envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            name, "order" if name=="response" else None, "token", "SELL", "condition", "a"*64,
            "b"*64, "c"*64, _json(response) if name=="response" else None, "[]", "[]", AT))
    point = dict(response, _venue_submission_envelope=envelope)
    order = dict(source="place_limit_order_ack", submit_result=point, venue_order_id="order", proof_class=None)
    c.execute("INSERT INTO venue_order_facts VALUES (1,'cmd','order','MATCHED','12.5','REST',?,?)", (AT, _json(order)))
    proxy = dict(reason="exit_order_fact_matched_missing_trade_fact_repair",
                 proof_class="matched_exit_order_fact_with_fill_economics", command_id="cmd",
                 venue_order_id="order", order_fact_id=1, order_fact_state="MATCHED",
                 matched_size="12.5", fill_price="0.07", tx_hash="", point_order=point)
    _fact(c, "order_fact:1", "12.5", raw=proxy, source="REST", state="MATCHED", tx=None)
    yield SimpleNamespace(conn=c, point=point, proxy=proxy, order=order)
    c.close()


def _fact(c, trade_id, quantity, *, raw=None, command="cmd", order="order", source="WS_USER", state="CONFIRMED", tx="tx", sequence=1):
    raw = raw or dict(event_type="trade", id=trade_id, status=state, asset_id="token", side="SELL",
                      taker_order_id=order, size=quantity, price="0.07")
    c.execute("INSERT INTO venue_trade_facts VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        command, trade_id, order, state, quantity, "0.07", source, AT, AT, AT, sequence, tx, _json(raw)))


def _quantity(case, position="position"):
    return sum((row.quantity for row in fills.economic_exit_fills_for_position(case.conn, position)), Decimal(0))


def _recovery_quantity(case, command="cmd", order="order"):
    return Decimal(recovery._positive_fill_trade_fact_summary(case.conn, command, venue_order_id=order)["filled_size"])


def test_proxy_is_non_economic_before_native_and_partials_remain_distinct(case):
    original = tuple(case.conn.execute("SELECT * FROM venue_trade_facts WHERE trade_id='order_fact:1'").fetchone())
    assert _quantity(case) == _recovery_quantity(case) == 0
    _fact(case.conn, "native-5", "5")
    assert _quantity(case) == _recovery_quantity(case) == 5
    _fact(case.conn, "native-7.5", "7.5")
    assert _quantity(case) == _recovery_quantity(case) == Decimal("12.5")
    _fact(case.conn, "native-5", "5", state="MATCHED", sequence=2)
    _fact(case.conn, "native-7.5", "7.5", sequence=2)
    assert _quantity(case) == _recovery_quantity(case) == Decimal("12.5")
    assert tuple(case.conn.execute("SELECT * FROM venue_trade_facts WHERE trade_id='order_fact:1'").fetchone()) == original


@pytest.mark.parametrize("mutation", ["reason", "command", "order", "token", "side", "quantity", "price",
                                     "link", "envelope", "native_id", "tx", "malformed"])
def test_ambiguous_proxy_is_unavailable_in_both_readers(case, mutation):
    p = case.proxy
    if mutation=="reason": p["reason"]="unknown"
    elif mutation=="command": p["command_id"]="other"
    elif mutation=="order": p["venue_order_id"]="other"
    elif mutation=="token": p["point_order"]["_venue_submission_envelope"]["selected_outcome_token_id"]="other"
    elif mutation=="side": p["point_order"]["_venue_submission_envelope"]["side"]="BUY"
    elif mutation=="quantity": p["matched_size"]="5"
    elif mutation=="price": p["fill_price"]="0.08"
    elif mutation=="link": p["order_fact_id"]=2
    elif mutation=="envelope": p["point_order"]["_venue_submission_envelope"]["signed_order_hash"]="x"*64
    elif mutation=="native_id": p["point_order"]["trade_ids"]=["possible-real"]
    elif mutation=="tx": p["tx_hash"]="possible-tx"
    value = "bad json" if mutation=="malformed" else _json(p)
    case.conn.execute("UPDATE venue_trade_facts SET raw_payload_json=? WHERE trade_id='order_fact:1'", (value,))
    with pytest.raises(fills.PartialExitEconomicDebtError, match=MARKER): _quantity(case)
    with pytest.raises(sqlite3.OperationalError, match=MARKER): _recovery_quantity(case)


def test_native_trade_named_like_proxy_is_not_discarded(case):
    _fact(case.conn, "order_fact:provider-identity", "5")
    assert _quantity(case) == _recovery_quantity(case) == 5


def test_bad_proxy_does_not_poison_unrelated_position_or_command(case):
    case.conn.execute("UPDATE venue_trade_facts SET raw_payload_json='bad json'")
    case.conn.execute("INSERT INTO venue_commands VALUES ('healthy','healthy-position','EXIT','token','SELL',5,.07,'healthy-order','pre','ACKED',?,?)", (AT, AT))
    _fact(case.conn, "native-healthy", "5", command="healthy", order="healthy-order")
    assert _quantity(case, "healthy-position") == 5
    assert _recovery_quantity(case, "healthy", "healthy-order") == 5


def test_named_ambiguity_is_not_swallowed_by_exit_lifecycle(case):
    case.conn.execute("UPDATE venue_trade_facts SET raw_payload_json='bad json'")
    position = SimpleNamespace(trade_id="position")
    for reader in (exit_lifecycle._exit_trade_fact_close_candidate, exit_lifecycle._exit_trade_fact_confirmation_pending_candidate):
        case.conn.execute("UPDATE venue_trade_facts SET state=?", (
            "CONFIRMED" if reader is exit_lifecycle._exit_trade_fact_close_candidate else "MATCHED",
        ))
        with pytest.raises(fills.PartialExitEconomicDebtError, match=MARKER): reader(case.conn, position)


def test_pending_scan_defers_ambiguous_position_without_retry_or_close(case, monkeypatch):
    case.conn.execute("UPDATE venue_trade_facts SET raw_payload_json='bad json'")
    pos = SimpleNamespace(trade_id="position", exit_state="retry_pending")
    before = dict(vars(pos))
    monkeypatch.setattr(exit_lifecycle, "_rotated_pending_exit_scan_positions", lambda *_args, **_kwargs: [pos])
    monkeypatch.setattr(exit_lifecycle, "_commit_exit_write_boundary", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(exit_lifecycle, "check_pending_retries", lambda *_args, **_kwargs: pytest.fail("ambiguous fill retried"))
    result = exit_lifecycle.check_pending_exits(object(), object(), case.conn, max_positions=1)
    assert result["filled"] == result["retried"] == 0
    assert result["unchanged"] == 1
    assert result["pending_exit_economic_authority_unavailable"][0]["position_id"] == "position"
    assert vars(pos) == before


def test_attached_schema_and_asof_proxy_classification(case):
    c = case.conn
    c.execute("ATTACH DATABASE ':memory:' AS trades")
    for table in ("venue_commands", "venue_order_facts", "venue_submission_envelopes", "venue_trade_facts"):
        c.execute(f"CREATE TABLE trades.{table} AS SELECT * FROM main.{table}")
    # Main truth cannot complete or poison an attached proxy's authority.
    c.execute("UPDATE main.venue_trade_facts SET raw_payload_json='bad json'")
    canonical=fills.canonical_trade_fact_cte(source_schema="trades",source_clause_sql="WHERE fact.observed_at <= ?")
    economic=fills.economic_trade_fact_cte(source_schema="trades",source_clause_sql="AND source_fact.observed_at <= ?")
    assert c.execute(f"WITH {canonical},{economic} SELECT * FROM economic_trade_fact",(AT,AT)).fetchall()==[]
    c.execute("DELETE FROM trades.venue_order_facts")
    with pytest.raises(sqlite3.OperationalError,match=MARKER):
        c.execute(f"WITH {canonical},{economic} SELECT * FROM economic_trade_fact",(AT,AT)).fetchall()


def test_lightweight_reader_refuses_proxy_without_claiming_zero(case):
    canonical=fills.canonical_trade_fact_cte()
    economic=fills.economic_trade_fact_cte(proxy_provenance_available=False)
    for table in ("venue_commands", "venue_order_facts", "venue_submission_envelopes"):
        case.conn.execute(f"DROP TABLE {table}")
    _fact(case.conn,"native-only","5")
    case.conn.execute("ALTER TABLE venue_trade_facts DROP COLUMN fill_price")
    with pytest.raises(sqlite3.OperationalError,match=MARKER):
        case.conn.execute(f"WITH {canonical},{economic} SELECT * FROM economic_trade_fact").fetchall()
    case.conn.execute("DELETE FROM venue_trade_facts WHERE trade_id='order_fact:1'")
    assert len(case.conn.execute(f"WITH {canonical},{economic} SELECT * FROM economic_trade_fact").fetchall())==1


def test_writer_without_native_identity_never_creates_a_proxy(case,monkeypatch):
    case.conn.execute("DELETE FROM venue_trade_facts")
    monkeypatch.setattr(recovery,"append_trade_fact",lambda *_args,**_kwargs:pytest.fail("anonymous proxy was written"))
    monkeypatch.setattr(recovery,"append_event",lambda *_args,**_kwargs:pytest.fail("unsupported fill command authority"))
    candidate=dict(command_id="cmd",venue_order_id="order",order_fact_id=1,order_fact_state="MATCHED",
                   order_fact_source="REST",order_fact_matched_size="12.5",side="SELL",token_id="token",price=.07,size=12.5,
                   state="ACKED",order_fact_raw_payload_json=_json(case.order))
    assert recovery._repair_exit_matched_order_fact_projection(case.conn,candidate=candidate,occurred_at=AT) is False
    assert case.conn.execute("SELECT count(*) FROM venue_trade_facts").fetchone()[0]==0


@pytest.mark.parametrize("proof,expected", [("transaction", True), ("multiple_ids", False), ("limit_only", False), ("wrong_order", False)])
def test_writer_preserves_only_bound_native_economics(case, monkeypatch, proof, expected):
    case.conn.execute("DELETE FROM venue_trade_facts")
    point=json.loads(_json(case.point))
    point["transactionsHashes"]=["tx"]
    if proof=="multiple_ids":
        point["transactionsHashes"]=[]
        point["tradeIDs"]=["a","b"]
    elif proof=="limit_only":
        point.pop("makingAmount");point.pop("takingAmount")
        point["price"]="0.07"
    elif proof=="wrong_order": point["orderID"]="other"
    written=[]
    monkeypatch.setattr(recovery,"append_trade_fact",lambda *_args,**kwargs:written.append(kwargs))
    recovery._append_missing_exit_trade_fact_from_order_fact(
        case.conn,candidate=dict(command_id="cmd",venue_order_id="order",token_id="token",side="SELL",order_fact_id=1),
        point_order=point,matched_size="12.5",fill_price="0.07",observed_at=AT,
    )
    assert bool(written) is expected
    if written:
        assert written[0]["trade_id"] == "tx" and written[0]["state"] == "MATCHED"


def test_late_response_without_ids_does_not_block_existing_native_facts(case,monkeypatch):
    _fact(case.conn,"native","12.5")
    monkeypatch.setattr(recovery,"append_trade_fact",lambda *_args,**_kwargs:pytest.fail("native fill duplicated"))
    recovery._append_missing_exit_trade_fact_from_order_fact(
        case.conn,candidate=dict(command_id="cmd",venue_order_id="order"),point_order=case.point,
        matched_size="12.5",fill_price="0.07",observed_at=AT,
    )
    assert _quantity(case) == _recovery_quantity(case) == Decimal("12.5")

@pytest.mark.parametrize('quantity,taking', [('0.2','0.014'),('1.3','0.091')])
def test_normalized_decimal_economics_are_not_recomputed_with_real_division(case,quantity,taking):
    p=case.proxy
    p['matched_size']=quantity
    point=p['point_order']
    point['makingAmount']=point['_v2_matched_size']=quantity
    point['takingAmount']=taking
    response={key:value for key,value in point.items() if key!='_venue_submission_envelope'}
    point['_venue_submission_envelope']['raw_response_json']=_json(response)
    case.order['submit_result']=point
    case.conn.execute('UPDATE venue_trade_facts SET filled_size=?,raw_payload_json=?',(quantity,_json(p)))
    case.conn.execute('UPDATE venue_order_facts SET matched_size=?,raw_payload_json=?',(quantity,_json(case.order)))
    case.conn.execute("UPDATE venue_submission_envelopes SET raw_response_json=? WHERE envelope_id='response'",(_json(response),))
    assert _quantity(case)==_recovery_quantity(case)==0
    _fact(case.conn,'native-exact-decimal',quantity)
    assert _quantity(case)==_recovery_quantity(case)==Decimal(quantity)

@pytest.mark.parametrize('field,value',[('asset_id','other'),('market','other'),('side','BUY')])
def test_contradictory_raw_native_fields_refuse_proxy_classification(case,field,value):
    case.proxy['point_order'][field]=value
    case.order['submit_result']=case.proxy['point_order']
    case.conn.execute('UPDATE venue_trade_facts SET raw_payload_json=?',(_json(case.proxy),))
    case.conn.execute('UPDATE venue_order_facts SET raw_payload_json=?',(_json(case.order),))
    with pytest.raises(fills.PartialExitEconomicDebtError,match=MARKER):_quantity(case)
    with pytest.raises(sqlite3.OperationalError,match=MARKER):_recovery_quantity(case)

@pytest.mark.parametrize('payload',[{}, {'unrelated':'object'}])
def test_stripped_proxy_prefix_is_unavailable_not_guessed_native(case,payload):
    case.conn.execute('UPDATE venue_trade_facts SET raw_payload_json=?',(_json(payload),))
    with pytest.raises(fills.PartialExitEconomicDebtError,match=MARKER):_quantity(case)
    with pytest.raises(sqlite3.OperationalError,match=MARKER):_recovery_quantity(case)

@pytest.mark.parametrize('field,value',[('captured_at','2027-01-01T00:00:00+00:00'),
    ('selected_outcome_token_id','other'),('side','BUY'),('condition_id','other')])
def test_presign_provenance_must_match_original_response_and_clock(case,field,value):
    case.conn.execute(f"UPDATE venue_submission_envelopes SET {field}=? WHERE envelope_id='pre'",(value,))
    with pytest.raises(fills.PartialExitEconomicDebtError,match=MARKER):_quantity(case)
    with pytest.raises(sqlite3.OperationalError,match=MARKER):_recovery_quantity(case)

@pytest.mark.parametrize('field,value',[('captured_at','2027-01-01T00:00:00+00:00'),
    ('selected_outcome_token_id','other'),('side','BUY'),('condition_id','other'),
    ('canonical_pre_sign_payload_hash','different')])
def test_writer_requires_causal_matching_presign(case,monkeypatch,field,value):
    case.conn.execute('DELETE FROM venue_trade_facts')
    case.conn.execute(f"UPDATE venue_submission_envelopes SET {field}=? WHERE envelope_id='pre'",(value,))
    point=json.loads(_json(case.point));point['transactionsHashes']=['tx']
    monkeypatch.setattr(recovery,'append_trade_fact',lambda *_args,**_kwargs:pytest.fail('unbound presign promoted'))
    recovery._append_missing_exit_trade_fact_from_order_fact(
        case.conn,candidate=dict(command_id='cmd',venue_order_id='order',token_id='token',side='SELL',order_fact_id=1),
        point_order=point,matched_size='12.5',fill_price='0.07',observed_at=AT,
    )

@pytest.mark.parametrize('field,value',[('asset_id','other'),('side','BUY'),('market','other'),('condition_id','other'),('taker_order_id','other')])
def test_native_prefix_requires_canonical_command_identity(case,field,value):
    case.conn.execute('DELETE FROM venue_trade_facts')
    raw=dict(event_type='trade',id='order_fact:provider-identity',status='CONFIRMED',asset_id='token',side='SELL',
             taker_order_id='order',size='5',price='0.07')
    raw[field]=value
    _fact(case.conn,'order_fact:provider-identity','5',raw=raw)
    with pytest.raises(fills.PartialExitEconomicDebtError,match=MARKER):_quantity(case)
    with pytest.raises(sqlite3.OperationalError,match=MARKER):_recovery_quantity(case)
