# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: EDLI_EXECUTION_STRATEGY_DESIGN_2026_05_31.md §4 items 5+6 (Dimension 3 + 6) +
#   PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md SD-6/SD-7. RED-first relationship tests for the
#   EXIT + re-price-cadence half of the execution strategy. SHADOW semantics; no real orders.
"""Relationship tests for the EXIT + re-price cadence (src.events.continuous_redecision).

These pin the ANTI-TWITCH invariant as a cross-module property, not a single function's return:
  - A bare price wiggle (belief unchanged) must NEVER trigger a cancel, a re-price, OR an exit.
  - Only an EVIDENCE-backed belief move (new snapshot_id / CI-separated reversal) acts.
  - A quote priced off a dead book (stale) is pulled regardless of belief (it's not a belief move,
    it's a "this order's price is meaningless now" cancel — re-decide next cycle on fresh price).

§4.5 (screen_reprice + stale cancel) and §4.6 6b (select_exit_order_mode) are LIVE and covered
below. §4.6's CI-separation / EVIDENCE_UNAVAILABLE / screen_exit_cancel exit-discriminator
hardening never landed — screen_exit/screen_exit_cancel were deleted as dead code in W3 (#133)
before those tests could go green; that permanently-skipped coverage was removed in the
gate-stack simplification (Phase 1, 2026-07-06) rather than carried forward indefinitely.
"""
from __future__ import annotations

import sqlite3

import pytest

cr = pytest.importorskip(
    "src.events.continuous_redecision",
    reason="continuous_redecision module not yet authored — relationship contract is RED",
)


def _mem_world() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cr.ensure_belief_cache_schema(conn)
    return conn


def _cache_yes_belief(conn, *, p_posterior_yes: float, recorded_at: str, snapshot_id: str = "snap1"):
    """Cache a 2-bin belief; YES-prob of b30."""
    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id=snapshot_id,
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        p_posterior_vec=[0.001, p_posterior_yes],
        recorded_at=recorded_at,
    )


# ===========================================================================
# §4.5 — Re-price / cancel wiring (Dimension 3, anti-twitch core)
# ===========================================================================

# --- Belief-WORSENING re-price: evidence-backed belief decay → cancel+replace -----------------
def test_belief_worsening_reprice_fires_cancel_replace():
    """A resting BUY_NO order whose belief (evidence) has DECAYED past belief_reprice_delta gets
    pulled — symmetric to the existing edge-IMPROVEMENT re-fire. The move is EVIDENCE-backed: a new
    snapshot_id (new FSR/day0/obs), not a bare price tick."""
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    # New evidence (snap2): YES-belief decays 0.90 -> 0.80 (Δ0.10 ≥ belief_reprice_delta 0.03).
    _cache_yes_belief(conn, p_posterior_yes=0.80, recorded_at="2026-05-31T06:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_yes",
        resting_posterior=0.90,            # belief the resting order was priced at
        resting_snapshot_id="snap1",       # the evidence the resting order was priced on
        belief_reprice_delta=0.03,
    )
    assert decision is not None, "evidence-backed belief decay must pull the stale-favorable resting order"
    assert decision.action == "CANCEL_REPLACE"
    assert decision.reason == "BELIEF_WORSENING"


def test_bare_price_wiggle_does_not_reprice():
    """ANTI-TWITCH: the belief is UNCHANGED (same snapshot_id) — only the price moved. screen_reprice
    must return None (HOLD). A bare price wiggle is not evidence and must never cancel/re-price."""
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    # No new snapshot written. The resting order's evidence (snap1) is still the latest belief.
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_yes",
        resting_posterior=0.90,
        resting_snapshot_id="snap1",
        belief_reprice_delta=0.03,
    )
    assert decision is None, "unchanged belief (bare price move) must NOT re-price (anti-twitch)"


def test_sub_delta_belief_move_does_not_reprice():
    """A NEW snapshot exists but the belief barely moved (Δ0.01 < belief_reprice_delta) → HOLD.
    Evidence changed but not MATERIALLY → no twitchy re-price."""
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_yes_belief(conn, p_posterior_yes=0.89, recorded_at="2026-05-31T06:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_yes",
        resting_posterior=0.90,
        resting_snapshot_id="snap1",
        belief_reprice_delta=0.03,
    )
    assert decision is None, "sub-delta belief move must not re-price"


def test_belief_improving_does_not_pull_order():
    """Belief moved in our FAVOR (YES-belief 0.90 -> 0.95) on new evidence. screen_reprice (the
    WORSENING trigger) must NOT pull the order — improvement is the existing IMPROVE_DELTA re-fire's
    job, not a cancel. This guards against re-pricing a more-favorable resting order off the book."""
    conn = _mem_world()
    _cache_yes_belief(conn, p_posterior_yes=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_yes_belief(conn, p_posterior_yes=0.95, recorded_at="2026-05-31T06:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_yes",
        resting_posterior=0.90,
        resting_snapshot_id="snap1",
        belief_reprice_delta=0.03,
    )
    assert decision is None, "favorable belief move must not trigger a WORSENING cancel"


# ===========================================================================
# §4.6 6b — exit mechanics route through the ENTRY-WAVE order-mode machinery
# ===========================================================================
def test_exit_order_mode_reuses_entry_select_edli_order_mode():
    """6b: an exit is an entry into the OPPOSITE side. select_exit_order_mode must route through the
    SAME governor maker/taker + EV machinery the entry spine uses (reuse _select_edli_order_mode),
    NOT a duplicated exit-only selector. We prove reuse by patching the entry selector and asserting
    the exit path delegates to it (and flips the side / caps at the exit reservation)."""
    import src.engine.event_reactor_adapter as era

    captured: dict = {}

    def _fake_select(*, actionable_payload, **kwargs):
        captured["direction"] = actionable_payload.get("direction")
        captured["c_fee_adjusted"] = actionable_payload.get("c_fee_adjusted")
        return "TAKER"

    orig = era._select_edli_order_mode
    era._select_edli_order_mode = _fake_select
    try:
        mode = cr.select_exit_order_mode(
            held_side="buy_no",                 # we HELD buy_no -> exit enters buy_yes
            exit_reservation=0.42,
            actionable_payload={"direction": "buy_no", "c_fee_adjusted": 0.30},
            quote_payload={},
            best_bid=0.40,
            best_ask=0.45,
            executable_snapshot=None,
        )
    finally:
        era._select_edli_order_mode = orig

    assert mode == "TAKER", "exit order mode must come from the reused entry selector"
    assert captured["direction"] == "buy_yes", "an exit of buy_no enters the OPPOSITE side (buy_yes)"
    assert captured["c_fee_adjusted"] == 0.42, "exit must be capped at the exit reservation (no panic-dump)"
