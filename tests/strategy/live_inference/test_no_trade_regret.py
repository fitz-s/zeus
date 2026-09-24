# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §14 NoTradeRegretLedger contract.
from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema
import src.strategy.live_inference.no_trade_regret as no_trade_regret_module
from src.strategy.live_inference.no_trade_regret import (
    NoTradeRegretHindsightError,
    NoTradeRegretEvent,
    NoTradeRegretLedger,
    classify_fillable_bucket,
)


def _ledger():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, NoTradeRegretLedger(conn)


@pytest.fixture(autouse=True)
def _historic_protocol_cut_for_isolated_fixtures(monkeypatch):
    from datetime import datetime, timezone

    # Production fixes this bound at module import; synthetic historical
    # tests can only change the internal bound, never an insert API argument.
    monkeypatch.setattr(
        no_trade_regret_module, "_PROSPECTIVE_CAPTURE_START",
        datetime(2026, 9, 23, 0, tzinfo=timezone.utc),
    )


def test_insert_idempotent():
    conn, ledger = _ledger()
    event = NoTradeRegretEvent("event-1", "FDR", "FDR_REJECTED", "FDR_REJECTED")
    ledger.insert_idempotent(event)
    ledger.insert_idempotent(event)
    assert conn.execute("SELECT COUNT(*) FROM no_trade_regret_events").fetchone()[0] == 1


def test_existing_no_trade_event_compatibility_written_when_natural_key_exists():
    conn, ledger = _ledger()
    ledger.insert_idempotent(
        NoTradeRegretEvent(
            "event-1",
            "KELLY",
            "KELLY_TOO_SMALL",
            "KELLY_TOO_SMALL",
            market_slug="slug",
            metric="high",
            target_date="2026-05-24",
            observation_time="2026-05-24T18:00:00+00:00",
            decision_seq=7,
        )
    )
    assert conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0] == 1


def test_existing_no_trade_event_compatibility_skipped_without_natural_key():
    conn, ledger = _ledger()
    ledger.insert_idempotent(
        NoTradeRegretEvent("event-1", "KELLY", "KELLY_TOO_SMALL", "KELLY_TOO_SMALL", market_slug="slug")
    )
    assert conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0] == 0


def test_event_without_market_slug_still_writes_regret_ledger():
    conn, ledger = _ledger()
    ledger.insert_idempotent(NoTradeRegretEvent("event-1", "SOURCE_TRUTH", "blocked", "SOURCE_WRONG"))
    assert conn.execute("SELECT COUNT(*) FROM no_trade_regret_events").fetchone()[0] == 1


def test_later_outcome_join_after_settlement_only():
    conn, ledger = _ledger()
    ledger.insert_idempotent(
        NoTradeRegretEvent("event-1", "EXECUTABLE_QUOTE", "NO_DEPTH", "NO_DEPTH")
    )
    ledger.enrich_after_settlement(
        event_id="event-1",
        rejection_stage="EXECUTABLE_QUOTE",
        rejection_reason="NO_DEPTH",
        later_outcome="WIN",
        would_have_won=True,
        would_have_filled=False,
        settlement_proof="settlement-row-1",
    )
    row = conn.execute("SELECT later_outcome FROM no_trade_regret_events").fetchone()
    assert row[0] == "WIN"


def test_live_insert_denies_hindsight_fields():
    _conn, ledger = _ledger()
    with pytest.raises(NoTradeRegretHindsightError, match="live no-trade regret insert"):
        ledger.insert_idempotent(
            NoTradeRegretEvent(
                "event-1",
                "EXECUTABLE_QUOTE",
                "NO_DEPTH",
                "WOULD_HAVE_WON_BUT_UNFILLABLE",
                later_outcome="WIN",
                would_have_won=True,
                would_have_filled=False,
            )
        )


def test_live_reader_denies_outcome_columns():
    _conn, ledger = _ledger()
    ledger.insert_idempotent(NoTradeRegretEvent("event-1", "EXECUTABLE_QUOTE", "NO_DEPTH", "NO_DEPTH"))
    row = ledger.live_reader_rows()[0]
    assert "later_outcome" not in row
    assert "would_have_won" not in row
    assert "regret_bucket" not in row


def test_fillable_vs_unfillable_bucket():
    assert classify_fillable_bucket(would_have_won=True, would_have_filled=True) == "WOULD_HAVE_WON_AND_FILLABLE"
    assert classify_fillable_bucket(would_have_won=True, would_have_filled=False) == "WOULD_HAVE_WON_BUT_UNFILLABLE"
    assert classify_fillable_bucket(would_have_won=False, would_have_filled=True) == "WOULD_HAVE_LOST"


def test_regret_ledger_records_q_cost_fill_score_context():
    conn, ledger = _ledger()
    ledger.insert_idempotent(
        NoTradeRegretEvent(
            "event-1",
            "TRADE_SCORE",
            "TRADE_SCORE_BLOCKED",
            "FEE_ERASED_EDGE",
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            family_id="family-1",
            bin_label="74-75",
            direction="buy_yes",
            q_live=0.61,
            q_lcb_5pct=0.57,
            c_fee_adjusted=0.56,
            c_cost_95pct=0.60,
            p_fill_lcb=0.25,
            trade_score=-0.01,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
            hypothetical_order_type="GTC",
            hypothetical_fill_status="UNFILLABLE",
            hypothetical_fill_price=None,
            causal_snapshot_id="forecast-snap-1",
            executable_snapshot_id="exec-snap-1",
        )
    )

    row = conn.execute(
        """
        SELECT city, family_id, q_live, c_fee_adjusted, p_fill_lcb, trade_score,
               native_quote_available, causal_snapshot_id, executable_snapshot_id
        FROM no_trade_regret_events
        """
    ).fetchone()
    assert row == ("Chicago", "family-1", 0.61, 0.56, 0.25, -0.01, 1, "forecast-snap-1", "exec-snap-1")


# The v8 source-clock alpha trail remains a one-in-flight, immutable feedback
# chain. These tests use only in-memory or tmp SQLite and synthetic finalized
# proof mappings; no settlement or venue service is opened.
def _v8_event(city: str, decision_at: str, *, metric: str = "high", strategy: str = "day0_nowcast_entry", cut_at: str | None = None):
    import json
    from src.contracts.global_auction_receipt import CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION

    revision = "day0-current-revision"
    selection = CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
    event_id = (
        "market-relative-alpha-shadow-v8-causal-brier:"
        f"{strategy}:{selection}:{revision}:{metric}:{city}:2026-09-23"
    )
    envelope = {
        "schema_version": 3, "strategy_key": strategy,
        "global_selection_revision": selection,
        "probability_semantics_revision": revision,
        "metric": metric, "city": city, "target_date": "2026-09-23",
        "side": "YES", "condition_id": f"condition-{city}",
        "token_id": f"yes-{city}",
        "q": 0.87, "raw_min_order_vwap": 0.35,
        "decision_at_utc": decision_at,
        "selection_cut_at_utc": cut_at or decision_at,
        "decision_law_id": "executable_min_order_capital_gain_v2",
        "selection_rule": (
            "earliest_complete_global_cut_exact_global_posterior_mean_"
            "expected_growth_winner_v3"
        ),
    }
    return NoTradeRegretEvent(
        event_id=event_id, rejection_stage="RISK_GUARD",
        rejection_reason=f"MARKET_RELATIVE_ALPHA_SHADOW:{strategy}",
        regret_bucket="RISK_CAP", decision_time=decision_at,
        city=city, target_date="2026-09-23", metric=metric,
        condition_id=f"condition-{city}", token_id=f"yes-{city}",
        direction="buy_yes", envelope_json=json.dumps(envelope, sort_keys=True),
    )


def _v8_record(conn, event_id):
    import json
    raw, feedback = conn.execute(
        "SELECT envelope_json,alpha_feedback_json FROM no_trade_regret_events "
        "WHERE regret_event_id=?", (event_id,)
    ).fetchone()
    return json.loads(raw), (None if feedback is None else json.loads(feedback))


def _v8_proof(conn, event_id, city: str, *, yes_wins=True):
    import hashlib
    raw = conn.execute(
        "SELECT envelope_json FROM no_trade_regret_events WHERE regret_event_id=?",
        (event_id,),
    ).fetchone()[0]
    winner = 1 if yes_wins else 0
    return {
        "condition_id": f"condition-{city}", "token_id": f"yes-{city}",
        "side": "YES", "envelope_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "outcome": winner, "yes_token_id": f"yes-{city}",
        "no_token_id": f"no-{city}",
        "payout_rows": [
            {"id": 11 + index, "condition_id": f"condition-{city}",
             "outcome_index": index, "payout_numerator": winner if index == 0 else 1-winner,
             "payout_denominator": 1,
             "state": "RESOLVED_NONZERO" if (winner if index == 0 else 1-winner) else "RESOLVED_ZERO",
             "source": "chain_rpc_finalized_v1", "block_number": 100,
             "block_hash": "0x" + "a" * 64}
            for index in (0, 1)
        ],
    }


def test_v8_pending_feedback_and_strict_later_cut_preserve_one_winner():
    from datetime import datetime, timedelta, timezone

    conn, ledger = _ledger()
    conn.commit()
    first = _v8_event("Chicago", "2026-09-23T12:00:00+00:00")
    first_id = ledger.insert_idempotent(first)
    assert isinstance(first_id, str) and conn.in_transaction
    conn.commit()
    assert ledger.insert_idempotent(_v8_event("Milan", "2026-09-23T12:01:00+00:00")) is None
    assert conn.execute("SELECT count(*) FROM no_trade_regret_events").fetchone()[0] == 1
    proof = _v8_proof(conn, first_id, "Chicago")
    before = datetime.now(timezone.utc)
    assert ledger.acknowledge_alpha_settlement(first_id, settlement_proof=proof) is True
    after = datetime.now(timezone.utc)
    first_envelope, feedback = _v8_record(conn, first_id)
    feedback_time = datetime.fromisoformat(feedback["observed_at"])
    assert before <= feedback_time <= after
    assert first_envelope["alpha_protocol"]["slot"] == 1
    assert first_envelope["alpha_protocol"]["previous_event_id"] is None
    assert first_envelope["alpha_protocol"]["previous_feedback_hash"] is None
    assert feedback["settlement_proof"] == proof
    assert ledger.insert_idempotent(_v8_event("Milan", feedback["observed_at"])) is None
    after_feedback = (feedback_time + timedelta(microseconds=1)).isoformat()
    assert ledger.insert_idempotent(_v8_event("Milan", after_feedback, cut_at=feedback["observed_at"])) is None
    second = _v8_event("Milan", after_feedback)
    second_id = ledger.insert_idempotent(second)
    assert isinstance(second_id, str)
    second_envelope, second_feedback = _v8_record(conn, second_id)
    assert second_feedback is None
    assert second_envelope["alpha_protocol"]["slot"] == 2
    assert second_envelope["alpha_protocol"]["previous_event_id"] == first_id
    assert second_envelope["alpha_protocol"]["previous_feedback_hash"] == feedback["feedback_hash"]
    assert ledger.insert_idempotent(second) == second_id
    assert _v8_record(conn, second_id)[0] == second_envelope
    conn.close()


def test_v8_ack_is_idempotent_but_conflicting_proof_cannot_rewrite_feedback():
    conn, ledger = _ledger()
    event_id = ledger.insert_idempotent(_v8_event("Chicago", "2026-09-23T12:00:00+00:00"))
    proof = _v8_proof(conn, event_id, "Chicago")
    assert ledger.acknowledge_alpha_settlement(event_id, settlement_proof=proof)
    original = conn.execute("SELECT alpha_feedback_json FROM no_trade_regret_events").fetchone()[0]
    assert not ledger.acknowledge_alpha_settlement(event_id, settlement_proof={
        **proof, "payout_rows": list(reversed(proof["payout_rows"]))
    })
    assert conn.execute("SELECT alpha_feedback_json FROM no_trade_regret_events").fetchone()[0] == original
    with pytest.raises(ValueError, match="conflicting"):
        ledger.acknowledge_alpha_settlement(event_id, settlement_proof=_v8_proof(
            conn, event_id, "Chicago", yes_wins=False
        ))
    assert conn.execute("SELECT alpha_feedback_json FROM no_trade_regret_events").fetchone()[0] == original
    conn.close()


@pytest.mark.parametrize("tamper", ["condition_id", "token_id", "side", "envelope_sha256", "outcome", "block_hash", "block_number", "row_id", "source"])
def test_v8_ack_requires_exact_finalized_pair_and_frozen_metadata(tamper):
    import copy
    conn, ledger = _ledger()
    event_id = ledger.insert_idempotent(_v8_event("Chicago", "2026-09-23T12:00:00+00:00"))
    proof = copy.deepcopy(_v8_proof(conn, event_id, "Chicago"))
    if tamper == "condition_id":
        proof["condition_id"] = "other"
    if tamper == "token_id":
        proof["token_id"] = "other"
    if tamper == "side":
        proof["side"] = "NO"
    if tamper == "envelope_sha256":
        proof["envelope_sha256"] = "0" * 64
    if tamper == "outcome":
        proof["outcome"] = 0
    if tamper == "block_hash":
        proof["payout_rows"][1]["block_hash"] = "0x" + "b" * 64
    if tamper == "block_number":
        proof["payout_rows"][1]["block_number"] = 101
    if tamper == "row_id":
        proof["payout_rows"][1]["id"] = proof["payout_rows"][0]["id"]
    if tamper == "source":
        proof["payout_rows"][1]["source"] = "unfinalized"
    with pytest.raises(ValueError):
        ledger.acknowledge_alpha_settlement(event_id, settlement_proof=proof)
    assert _v8_record(conn, event_id)[1] is None
    conn.close()


def test_v8_rollback_retry_and_old_v7_remain_unchanged():
    from src.events.idempotency import stable_event_id

    conn, ledger = _ledger()
    conn.commit()
    event = _v8_event("Chicago", "2026-09-23T12:00:00+00:00")
    first_id = ledger.insert_idempotent(event)
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM no_trade_regret_events").fetchone()[0] == 0
    assert ledger.insert_idempotent(event) == first_id
    assert _v8_record(conn, first_id)[0]["alpha_protocol"]["slot"] == 1
    conn.commit()
    proof = _v8_proof(conn, first_id, "Chicago")
    assert ledger.acknowledge_alpha_settlement(first_id, settlement_proof=proof)
    conn.rollback()
    assert _v8_record(conn, first_id)[1] is None
    legacy = NoTradeRegretEvent(
        event_id="market-relative-alpha-shadow-v7-acting-probability:legacy",
        rejection_stage="RISK_GUARD", rejection_reason="legacy",
        regret_bucket="RISK_CAP", envelope_json='{"frozen":true}',
    )
    legacy_id = ledger.insert_idempotent(legacy)
    assert legacy_id == stable_event_id(legacy.event_id, legacy.rejection_stage, legacy.rejection_reason)
    assert _v8_record(conn, legacy_id) == ({"frozen": True}, None)
    with pytest.raises(ValueError, match="requires a v8"):
        ledger.acknowledge_alpha_settlement(legacy_id, settlement_proof=proof)
    conn.close()


def test_v8_bad_tail_cannot_restart_sequence():
    from datetime import datetime, timedelta

    conn, ledger = _ledger()
    first_id = ledger.insert_idempotent(_v8_event("Chicago", "2026-09-23T12:00:00+00:00"))
    ledger.acknowledge_alpha_settlement(first_id, settlement_proof=_v8_proof(conn, first_id, "Chicago"))
    observed = datetime.fromisoformat(_v8_record(conn, first_id)[1]["observed_at"])
    next_event = _v8_event("Milan", (observed + timedelta(microseconds=1)).isoformat())
    conn.execute("UPDATE no_trade_regret_events SET alpha_feedback_json=? WHERE regret_event_id=?",
                 ('{"corrupt":true}', first_id))
    with pytest.raises(ValueError, match="invalid alpha feedback"):
        ledger.insert_idempotent(next_event)
    assert conn.execute("SELECT count(*) FROM no_trade_regret_events").fetchone()[0] == 1
    conn.close()


def test_alpha_feedback_nullable_schema_migration_preserves_old_row():
    from src.state.schema.no_trade_regret_events_schema import CREATE_TABLE_SQL, ensure_table

    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_TABLE_SQL.replace("    alpha_feedback_json TEXT,\n", ""))
    conn.execute(
        "INSERT INTO no_trade_regret_events "
        "(regret_event_id,event_id,rejection_stage,rejection_reason,regret_bucket,"
        "envelope_json,created_at,schema_version) VALUES "
        "('old','old','FDR','legacy','RISK_CAP','{\"frozen\":true}',"
        "'2026-09-23T00:00:00Z',1)"
    )
    ensure_table(conn)
    assert "alpha_feedback_json" in {
        r[1] for r in conn.execute("PRAGMA table_info(no_trade_regret_events)")
    }
    assert conn.execute(
        "SELECT envelope_json,alpha_feedback_json FROM no_trade_regret_events "
        "WHERE regret_event_id='old'"
    ).fetchone() == ('{"frozen":true}', None)
    conn.close()


def test_v8_two_writers_can_claim_only_one_next_slot(tmp_path):
    import json
    import threading
    from datetime import datetime, timedelta
    from src.state.schema.no_trade_regret_events_schema import ensure_table

    db = tmp_path / "v8-two-writers.db"
    setup = sqlite3.connect(db, timeout=3)
    ensure_table(setup)
    ledger = NoTradeRegretLedger(setup)
    first_id = ledger.insert_idempotent(_v8_event("Chicago", "2026-09-23T12:00:00+00:00"))
    assert ledger.acknowledge_alpha_settlement(
        first_id, settlement_proof=_v8_proof(setup, first_id, "Chicago")
    )
    feedback_at = datetime.fromisoformat(_v8_record(setup, first_id)[1]["observed_at"])
    next_at = (feedback_at + timedelta(microseconds=1)).isoformat()
    setup.commit()
    setup.close()
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def writer(city):
        conn = sqlite3.connect(db, timeout=3)
        try:
            barrier.wait(timeout=2)
            result = NoTradeRegretLedger(conn).insert_idempotent(_v8_event(city, next_at))
            conn.commit()
            results.append(result)
        except Exception as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=writer, args=(city,)) for city in ("Milan", "London")]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2 and sum(result is None for result in results) == 1
        check = sqlite3.connect(db)
        slots = sorted(json.loads(row[0])["alpha_protocol"]["slot"] for row in check.execute(
            "SELECT envelope_json FROM no_trade_regret_events"
        ))
        assert slots == [1, 2]
        check.close()
    finally:
        for thread in threads:
            thread.join(timeout=3)


def test_v8_autocommit_and_shared_pure_feedback_verifier(tmp_path):
    import json
    from src.state.schema.no_trade_regret_events_schema import ensure_table
    from src.strategy.live_inference.no_trade_regret import validated_alpha_feedback

    db = tmp_path / "v8-autocommit.db"
    conn = sqlite3.connect(db, isolation_level=None)
    ensure_table(conn)
    ledger = NoTradeRegretLedger(conn)
    event = _v8_event("Chicago", "2026-09-23T12:00:00+00:00")
    eid = ledger.insert_idempotent(event)
    assert not conn.in_transaction
    proof = _v8_proof(conn, eid, "Chicago")
    frozen_envelope = conn.execute(
        "SELECT envelope_json FROM no_trade_regret_events WHERE regret_event_id=?",
        (eid,),
    ).fetchone()[0]
    assert ledger.acknowledge_alpha_settlement(eid, settlement_proof=proof)
    assert not conn.in_transaction
    retry_envelope = json.loads(event.envelope_json)
    retry_envelope["q"] = 0.01
    assert ledger.insert_idempotent(
        __import__("dataclasses").replace(event, envelope_json=json.dumps(retry_envelope))
    ) == eid
    second_conn = sqlite3.connect(db)
    raw_envelope, raw_feedback = second_conn.execute(
        "SELECT envelope_json,alpha_feedback_json FROM no_trade_regret_events "
        "WHERE regret_event_id=?", (eid,)
    ).fetchone()
    assert raw_envelope == frozen_envelope
    assert validated_alpha_feedback(
        raw_feedback, regret_event_id=eid, envelope_json=raw_envelope,
        event_id=event.event_id, condition_id=event.condition_id,
        token_id=event.token_id, direction=event.direction,
        city=event.city, target_date=event.target_date, metric=event.metric,
    )["settlement_proof"] == proof
    tampered = json.loads(raw_feedback)
    tampered["feedback_hash"] = "0" * 64
    with pytest.raises(ValueError):
        validated_alpha_feedback(
            json.dumps(tampered), regret_event_id=eid, envelope_json=raw_envelope,
            event_id=event.event_id, condition_id=event.condition_id,
            token_id=event.token_id, direction=event.direction,
            city=event.city, target_date=event.target_date, metric=event.metric,
        )
    second_conn.close()
    conn.close()


def test_v8_historical_v7_relabel_cannot_start_prospective_sequence(monkeypatch):
    from datetime import datetime, timezone

    conn, ledger = _ledger()
    event = _v8_event("Chicago", "2026-09-23T12:00:00+00:00")
    monkeypatch.setattr(
        no_trade_regret_module, "_PROSPECTIVE_CAPTURE_START", datetime.now(timezone.utc)
    )
    assert ledger.insert_idempotent(event) is None
    assert conn.execute("SELECT count(*) FROM no_trade_regret_events").fetchone()[0] == 0
    monkeypatch.setattr(
        no_trade_regret_module, "_PROSPECTIVE_CAPTURE_START",
        datetime(2026, 9, 23, tzinfo=timezone.utc),
    )
    event_id = ledger.insert_idempotent(event)
    conn.commit()
    monkeypatch.setattr(
        no_trade_regret_module, "_PROSPECTIVE_CAPTURE_START", datetime.now(timezone.utc)
    )
    assert ledger.insert_idempotent(event) == event_id
    assert conn.execute("SELECT count(*) FROM no_trade_regret_events").fetchone()[0] == 1
    conn.close()


def test_v8_empty_helper_transaction_releases_world_writer_but_real_append_remains_caller_owned(tmp_path):
    from src.state.schema.no_trade_regret_events_schema import ensure_table

    db = tmp_path / "alpha-empty-lock.db"
    conn = sqlite3.connect(db, timeout=1)
    ensure_table(conn)
    conn.commit()
    ledger = NoTradeRegretLedger(conn)
    first_id = ledger.insert_idempotent(_v8_event("Chicago", "2026-09-23T12:00:00+00:00"))
    assert conn.in_transaction
    conn.commit()
    pending = _v8_event("Milan", "2026-09-23T12:01:00+00:00")
    assert ledger.insert_idempotent(pending) is None
    assert not conn.in_transaction
    competing = sqlite3.connect(db, timeout=1)
    competing.execute("BEGIN IMMEDIATE")
    competing.rollback()
    proof = _v8_proof(conn, first_id, "Chicago")
    assert ledger.acknowledge_alpha_settlement(first_id, settlement_proof=proof)
    assert conn.in_transaction
    conn.rollback()
    assert _v8_record(conn, first_id)[1] is None
    assert ledger.acknowledge_alpha_settlement(first_id, settlement_proof=proof)
    conn.commit()
    assert ledger.acknowledge_alpha_settlement(first_id, settlement_proof=proof) is False
    assert not conn.in_transaction
    competing.execute("BEGIN IMMEDIATE")
    competing.rollback()
    competing.close()
    conn.close()


def test_v8_empty_helper_never_ends_preexisting_outer_or_autocommit(tmp_path):
    from src.state.schema.no_trade_regret_events_schema import ensure_table

    db = tmp_path / "alpha-outer-lock.db"
    setup = sqlite3.connect(db)
    ensure_table(setup)
    setup.commit()
    setup.close()
    conn = sqlite3.connect(db)
    ledger = NoTradeRegretLedger(conn)
    first_id = ledger.insert_idempotent(_v8_event("Chicago", "2026-09-23T12:00:00+00:00"))
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    assert ledger.insert_idempotent(_v8_event("Milan", "2026-09-23T12:01:00+00:00")) is None
    assert conn.in_transaction
    conn.rollback()
    proof = _v8_proof(conn, first_id, "Chicago")
    assert ledger.acknowledge_alpha_settlement(first_id, settlement_proof=proof)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    assert ledger.acknowledge_alpha_settlement(first_id, settlement_proof=proof) is False
    assert conn.in_transaction
    conn.rollback()
    conn.close()
    autocommit = sqlite3.connect(db, isolation_level=None)
    autocommit_ledger = NoTradeRegretLedger(autocommit)
    assert autocommit_ledger.acknowledge_alpha_settlement(
        first_id, settlement_proof=proof
    ) is False
    assert not autocommit.in_transaction
    assert autocommit_ledger.insert_idempotent(
        _v8_event("Milan", "2026-09-23T12:01:00+00:00")
    ) is None
    assert not autocommit.in_transaction
    autocommit.close()
