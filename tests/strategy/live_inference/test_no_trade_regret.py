# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §14 NoTradeRegretLedger contract.
from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema
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
