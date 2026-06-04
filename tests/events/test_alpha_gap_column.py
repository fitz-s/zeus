# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: PR-4 B2 alpha-gap column brief; edge-axis = q_live - c_fee_adjusted.
"""
Relationship tests for alpha_gap column (B2, edge-axis plumbing).

Tests (all RED before implementation):
  1. test_alpha_gap_persisted_correctly
     alpha_gap persisted to DB == q_live - c_fee_adjusted for a receipt with both fields.

  2. test_alpha_gap_null_when_fee_null
     When c_fee_adjusted is NULL the persisted alpha_gap is NULL (fail-closed: gap
     cannot be measured without a market price; the gate in Phase 2 will handle this).

  3. test_alpha_gap_in_receipt_json
     alpha_gap is included in receipt_json (the serialised blob) so backfill can
     recompute it and round-trip probes work.

  4. test_marketprice_newtype_prevents_cost_confusion
     MarketPrice wraps the executable-snapshot c_fee_adjusted (q - market_price edge),
     and is TYPE-INCOMPATIBLE with C95Price (q - c_cost_95pct, trade-score stress edge).
     Passing a C95Price where MarketPrice is required raises TypeError at construction,
     making q_vs_c95pct-as-alpha_gap unwritable.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
from src.events.reactor import EventSubmissionReceipt
from src.state.schema.edli_no_submit_receipts_schema import ensure_table


# ---------------------------------------------------------------------------
# Helper: minimal proof-accepted NO_SUBMIT receipt
# ---------------------------------------------------------------------------

def _make_receipt(
    *,
    event_id: str = "evt-alpha-gap-test-001",
    q_live: float | None = 0.65,
    c_fee_adjusted: float | None = 0.52,
    c_cost_95pct: float | None = 0.58,
) -> EventSubmissionReceipt:
    return EventSubmissionReceipt(
        submitted=False,
        event_id=event_id,
        causal_snapshot_id="snap-001",
        city="Warsaw",
        target_date="2026-06-10",
        metric="high",
        condition_id="cond-001",
        token_id="tok-yes-001",
        outcome_label="YES",
        candidate_id="cand-001",
        executable_snapshot_id="es-001",
        family_id="fam-001",
        bin_label="26-27°C",
        direction="buy_yes",
        q_live=q_live,
        q_lcb_5pct=0.60,
        c_fee_adjusted=c_fee_adjusted,
        c_cost_95pct=c_cost_95pct,
        p_fill_lcb=0.70,
        trade_score=0.05,
        native_quote_available=True,
        source_status="MATCH",
        family_complete=True,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-fam-001",
        fdr_hypothesis_count=5,
        kelly_pass=True,
        kelly_size_usd=1.50,
        kelly_cost_basis_id="cost-001",
        kelly_decision_id="kdec-001",
        risk_decision_id="rdec-001",
        final_intent_id="intent-001",
        side_effect_status="NO_SUBMIT",
        reason="event_bound_final_intent_no_submit",
        proof_accepted=True,
    )


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    return conn


# ---------------------------------------------------------------------------
# Test 1: alpha_gap persisted correctly
# ---------------------------------------------------------------------------

def test_alpha_gap_persisted_correctly():
    """
    RELATIONSHIP TEST (B2 plumbing):
    For a receipt where both q_live and c_fee_adjusted are present,
    the persisted alpha_gap column must equal q_live - c_fee_adjusted.

    This is the edge-axis measurement: positive means our estimate is
    above the executable market price.
    """
    q_live = 0.65
    c_fee_adjusted = 0.52
    expected_gap = q_live - c_fee_adjusted  # 0.13

    conn = _make_conn()
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(
        event_id="evt-alpha-gap-correctness",
        q_live=q_live,
        c_fee_adjusted=c_fee_adjusted,
    )
    receipt_id = ledger.insert_idempotent(
        receipt, decision_time=datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    )

    row = conn.execute(
        "SELECT alpha_gap FROM edli_no_submit_receipts WHERE receipt_id = ?",
        (receipt_id,),
    ).fetchone()
    assert row is not None, "receipt not found in DB"
    stored_gap = row["alpha_gap"]
    assert stored_gap is not None, (
        "alpha_gap is NULL — column not populated (did you add alpha_gap field + INSERT?)"
    )
    assert abs(stored_gap - expected_gap) < 1e-9, (
        f"alpha_gap mismatch: expected {expected_gap}, got {stored_gap}"
    )


# ---------------------------------------------------------------------------
# Test 2: alpha_gap is NULL when c_fee_adjusted is NULL
# ---------------------------------------------------------------------------

def test_alpha_gap_null_when_fee_null():
    """
    FAIL-CLOSED semantics: when c_fee_adjusted is NULL (no executable market
    price), alpha_gap must be persisted as NULL, not 0 or q_live.

    NULL means 'unmeasured', not 'zero edge'. The Phase-2 gate will handle
    NULL gracefully (skip / PASS_THROUGH). Persisting 0 would be a silent lie.
    """
    conn = _make_conn()
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(
        event_id="evt-alpha-gap-null-fee",
        q_live=0.60,
        c_fee_adjusted=None,   # No executable market price
    )
    receipt_id = ledger.insert_idempotent(
        receipt, decision_time=datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    )

    row = conn.execute(
        "SELECT alpha_gap FROM edli_no_submit_receipts WHERE receipt_id = ?",
        (receipt_id,),
    ).fetchone()
    assert row is not None, "receipt not found in DB"
    assert row["alpha_gap"] is None, (
        f"alpha_gap should be NULL when c_fee_adjusted is NULL, got {row['alpha_gap']}"
    )


# ---------------------------------------------------------------------------
# Test 3: alpha_gap appears in receipt_json (for backfill + audit)
# ---------------------------------------------------------------------------

def test_alpha_gap_in_receipt_json():
    """
    alpha_gap must be serialised into the receipt_json blob so the backfill
    function can recover it from the JSON on existing NULL-gap rows, and so
    that inspection / audit tooling can read it without needing the column.
    """
    q_live = 0.70
    c_fee_adjusted = 0.55
    expected_gap = q_live - c_fee_adjusted

    conn = _make_conn()
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(
        event_id="evt-alpha-gap-json",
        q_live=q_live,
        c_fee_adjusted=c_fee_adjusted,
    )
    receipt_id = ledger.insert_idempotent(
        receipt, decision_time=datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
    )

    row = conn.execute(
        "SELECT receipt_json FROM edli_no_submit_receipts WHERE receipt_id = ?",
        (receipt_id,),
    ).fetchone()
    payload = json.loads(row["receipt_json"])
    assert "alpha_gap" in payload, (
        "alpha_gap missing from receipt_json — add it in _receipt_json()"
    )
    assert abs(payload["alpha_gap"] - expected_gap) < 1e-9, (
        f"receipt_json alpha_gap mismatch: expected {expected_gap}, got {payload['alpha_gap']}"
    )


# ---------------------------------------------------------------------------
# Test 4: MarketPrice newtype prevents cost confusion
# ---------------------------------------------------------------------------

def test_marketprice_newtype_prevents_cost_confusion():
    """
    ANTIBODY (anti-creep): MarketPrice and C95Price are distinct newtypes.
    Passing a C95Price where MarketPrice is required must raise TypeError,
    making the confusion q_live - c_cost_95pct-as-alpha_gap unconstructable.

    Four cost-like quantities exist in the system:
      c_fee_adjusted  — executable snapshot ask + taker fee (THIS is the market price)
      c_cost_95pct    — trade-score stress-edge (Kelly worst-case cost, NOT a market price)
      entry_cost_mean — mean fill cost (market_analysis.py BinEdge)
      c_95pct         — a.k.a. c_cost_95pct (alias in older code)

    The newtype makes it structurally impossible to pass the stress-edge cost
    c_cost_95pct where the executable market price c_fee_adjusted is required.
    """
    from src.types.market_price import MarketPrice, C95Price  # noqa: PLC0415

    # Valid construction
    mp = MarketPrice(0.52)
    assert mp.value == 0.52

    c95 = C95Price(0.58)
    assert c95.value == 0.58

    # MarketPrice and C95Price are distinct types — they must NOT be interchangeable.
    # The alpha_gap helper uses the runtime newtype guard from market_price.py.
    from src.types.market_price import compute_alpha_gap_from_market_price  # noqa: PLC0415

    # Valid call
    gap = compute_alpha_gap_from_market_price(0.65, mp)
    assert abs(gap - 0.13) < 1e-9

    # Passing C95Price where MarketPrice is expected must raise TypeError
    # (runtime isinstance check makes the error category unconstructable).
    with pytest.raises(TypeError):
        compute_alpha_gap_from_market_price(0.65, c95)  # type: ignore[arg-type]
