# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 — day0 evidence lane repair. The day0
#   shadow lane accumulated 1785 receipts with the fill/outcome enrichment layer
#   (later_outcome / would_have_won / hypothetical_fill_*) at 0% population (NO writer).
#   docs/evidence/day0/2026-06-11_day0_shadow_accuracy_profitability.md §1.4/§5.
#   Grading authority: src.contracts.graded_receipt.grade_receipt (Direction Law +
#   HK preimage + unit antibody). Settlement truth: forecasts.settlement_outcomes
#   (authority='VERIFIED'). Fee law: 0.05·p·(1−p)·shares.
"""RELATIONSHIP tests across the boundary

    day0 receipt (no_trade_regret_events, candidate content)
      -> settlement truth (forecasts.settlement_outcomes VERIFIED)
      -> grade_receipt (Direction Law)
      -> later_outcome / would_have_won enrichment write.

The cross-module invariant: a day0 receipt that is candidate-bearing AND whose
(city,target_date,metric) target has a VERIFIED settlement MUST receive a
grading-correct later_outcome + would_have_won; a settled buy_no on a bin the
target did NOT settle into is would_have_won=True (Direction Law). The fill
half: a candidate-bearing receipt with an executable ask gets a
hypothetical_fill_price = ask with the taker fee law applied.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.analysis.day0_shadow_enrichment import (
    grade_day0_receipt_outcome,
    hypothetical_taker_fill,
)


# ---------------------------------------------------------------------------
# Grading-correctness golden tests on settled fixtures (Direction Law).
# ---------------------------------------------------------------------------

def test_buy_no_wins_when_target_settles_outside_bin():
    """buy_no on '23°C' when the target settled at 30°C -> NO wins (Direction Law:
    buy_no WIN iff settled_bin != traded_bin)."""
    graded = grade_day0_receipt_outcome(
        bin_label="Will the highest temperature in Paris be 23°C on June 11?",
        direction="buy_no",
        settlement_value=30.0,
        settlement_unit="C",
    )
    assert graded is not None
    assert graded.won is True
    assert graded.settled_in_bin is False


def test_buy_no_loses_when_target_settles_inside_bin():
    """buy_no on '30°C' when the target settled at 30°C -> NO loses (settled IN bin)."""
    graded = grade_day0_receipt_outcome(
        bin_label="Will the highest temperature in Paris be 30°C on June 11?",
        direction="buy_no",
        settlement_value=30.0,
        settlement_unit="C",
    )
    assert graded is not None
    assert graded.won is False
    assert graded.settled_in_bin is True


def test_buy_yes_wins_when_target_settles_inside_bin():
    """buy_yes on '30°C' when target settled at 30°C -> YES wins."""
    graded = grade_day0_receipt_outcome(
        bin_label="Will the highest temperature in Paris be 30°C on June 11?",
        direction="buy_yes",
        settlement_value=30.0,
        settlement_unit="C",
    )
    assert graded is not None
    assert graded.won is True


def test_unparseable_bin_returns_none_never_crashes():
    graded = grade_day0_receipt_outcome(
        bin_label="not a temperature bin at all",
        direction="buy_no",
        settlement_value=30.0,
        settlement_unit="C",
    )
    assert graded is None


# ---------------------------------------------------------------------------
# Fee law: taker fill at ask, fee = 0.05 * p * (1-p) * shares.
# ---------------------------------------------------------------------------

def test_hypothetical_taker_fill_applies_fee_law():
    """A taker fill at ask=0.40 over 1 share: c_fee_adjusted = 0.40 + 0.05*0.4*0.6*1."""
    fill = hypothetical_taker_fill(ask=0.40, shares=1.0)
    assert fill is not None
    assert fill["hypothetical_fill_price"] == pytest.approx(0.40)
    assert fill["hypothetical_fill_status"] == "FILLED_AT_ASK"
    assert fill["hypothetical_order_type"] == "taker"
    expected_fee = 0.05 * 0.40 * 0.60 * 1.0
    assert fill["c_fee_adjusted"] == pytest.approx(0.40 + expected_fee)


def test_hypothetical_taker_fill_none_when_no_ask():
    assert hypothetical_taker_fill(ask=None, shares=1.0) is None
    # Degenerate ask (no liquidity) -> UNFILLABLE, not a fabricated fill.
    none_fill = hypothetical_taker_fill(ask=0.0, shares=1.0)
    assert none_fill is None


# ---------------------------------------------------------------------------
# END-TO-END relationship: receipt row -> settlement join -> enrichment write.
# ---------------------------------------------------------------------------

def _world_conn_with_receipt_and_settlement() -> sqlite3.Connection:
    """Build an in-memory world DB with no_trade_regret_events + an ATTACH-shaped
    forecasts.settlement_outcomes so the enrichment join is exercised end-to-end."""
    from src.state.schema.no_trade_regret_events_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    # Stand-in for the ATTACHed forecasts.settlement_outcomes (same column shape).
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, settlement_unit TEXT, authority TEXT
        )
        """
    )
    return conn


def test_enrich_settled_day0_receipt_writes_grading_correct_outcome():
    """RELATIONSHIP: a candidate-bearing day0 receipt whose target SETTLED gets a
    grading-correct later_outcome + would_have_won from settlement truth."""
    from src.analysis.day0_shadow_enrichment import enrich_settled_day0_receipts
    from src.strategy.live_inference.no_trade_regret import (
        NoTradeRegretEvent,
        NoTradeRegretLedger,
    )

    conn = _world_conn_with_receipt_and_settlement()
    ledger = NoTradeRegretLedger(conn)
    # Candidate-bearing day0 shadow receipt: buy_no on '23°C' Paris high 06-11.
    regret_id = ledger.insert_idempotent(
        NoTradeRegretEvent(
            event_id="evt-paris-1",
            rejection_stage="TRADE_SCORE",
            rejection_reason="DAY0_SCOPE_SHADOW_ONLY",
            regret_bucket="WOULD_HAVE_LOST",
            city="Paris",
            target_date="2026-06-11",
            metric="high",
            bin_label="Will the highest temperature in Paris be 23°C on June 11?",
            direction="buy_no",
            q_live=0.99,
            q_lcb_5pct=0.5,
            trade_score=0.1,
        )
    )
    # VERIFIED settlement: Paris high 06-11 settled at 30°C (outside the 23°C bin).
    conn.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        ("Paris", "2026-06-11", "high", 30.0, "C", "VERIFIED"),
    )

    n = enrich_settled_day0_receipts(conn, settlement_table="settlement_outcomes")
    assert n == 1

    row = conn.execute(
        "SELECT later_outcome, would_have_won FROM no_trade_regret_events WHERE regret_event_id=?",
        (regret_id,),
    ).fetchone()
    assert row["later_outcome"] is not None
    # buy_no on 23°C, settled at 30°C (outside) -> NO wins.
    assert row["would_have_won"] == 1


def test_enrich_skips_receipt_without_candidate_content():
    """A bare scope-gate receipt (no direction/bin_label) is NOT graded — there is
    nothing to grade, and enrichment never fabricates content."""
    from src.analysis.day0_shadow_enrichment import enrich_settled_day0_receipts
    from src.strategy.live_inference.no_trade_regret import (
        NoTradeRegretEvent,
        NoTradeRegretLedger,
    )

    conn = _world_conn_with_receipt_and_settlement()
    ledger = NoTradeRegretLedger(conn)
    ledger.insert_idempotent(
        NoTradeRegretEvent(
            event_id="evt-bare-1",
            rejection_stage="EXECUTOR_EXPRESSIBILITY",
            rejection_reason="DAY0_SCOPE_SHADOW_ONLY",
            regret_bucket="QUOTE_UNAVAILABLE",
            city="Tokyo",
            target_date="2026-06-11",
            metric="high",
            # no bin_label / direction — bare receipt
        )
    )
    conn.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        ("Tokyo", "2026-06-11", "high", 30.0, "C", "VERIFIED"),
    )
    n = enrich_settled_day0_receipts(conn, settlement_table="settlement_outcomes")
    assert n == 0  # nothing gradeable; no enrichment


def test_enrich_idempotent_second_run_no_change():
    """A second enrichment pass over already-graded receipts writes nothing new."""
    from src.analysis.day0_shadow_enrichment import enrich_settled_day0_receipts
    from src.strategy.live_inference.no_trade_regret import (
        NoTradeRegretEvent,
        NoTradeRegretLedger,
    )

    conn = _world_conn_with_receipt_and_settlement()
    ledger = NoTradeRegretLedger(conn)
    ledger.insert_idempotent(
        NoTradeRegretEvent(
            event_id="evt-paris-2",
            rejection_stage="TRADE_SCORE",
            rejection_reason="DAY0_SCOPE_SHADOW_ONLY",
            regret_bucket="WOULD_HAVE_LOST",
            city="Paris",
            target_date="2026-06-11",
            metric="high",
            bin_label="Will the highest temperature in Paris be 23°C on June 11?",
            direction="buy_no",
            q_live=0.99,
            q_lcb_5pct=0.5,
            trade_score=0.1,
        )
    )
    conn.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        ("Paris", "2026-06-11", "high", 30.0, "C", "VERIFIED"),
    )
    first = enrich_settled_day0_receipts(conn, settlement_table="settlement_outcomes")
    second = enrich_settled_day0_receipts(conn, settlement_table="settlement_outcomes")
    assert first == 1
    assert second == 0
