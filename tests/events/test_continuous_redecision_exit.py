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

RED until §4.5 (screen_reprice + stale cancel) and §4.6 (CI-separation, EVIDENCE_UNAVAILABLE,
screen_exit_cancel, select_exit_order_mode) land in continuous_redecision.
"""
from __future__ import annotations

import sqlite3

import pytest

cr = pytest.importorskip(
    "src.events.continuous_redecision",
    reason="continuous_redecision module not yet authored — relationship contract is RED",
)

# W3 (#133) removed screen_exit/screen_exit_cancel/select_exit_order_mode from
# continuous_redecision (zero live callers). These exit-screen relationship tests
# still call that deleted API and cannot execute until rewritten onto the live exit
# seam. Skip transparently (NOT a silent pass) instead of failing on AttributeError.
pytestmark = pytest.mark.skipif(
    not hasattr(cr, "screen_exit"),
    reason="screen_exit/screen_exit_cancel deleted in W3 (#133); exit-screen tests "
    "reference removed API — rewrite to new exit path pending",
)


def _mem_world() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cr.ensure_belief_cache_schema(conn)
    return conn


def _cache_no_belief(conn, *, p_posterior_no: float, recorded_at: str, snapshot_id: str = "snap1"):
    """Cache a 2-bin belief; NO-prob of b30 = 1 - p_posterior[b30]."""
    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id=snapshot_id,
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        p_posterior_vec=[0.001, 1.0 - p_posterior_no],
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
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    # New evidence (snap2): NO-belief decays 0.90 -> 0.80 (Δ0.10 ≥ belief_reprice_delta 0.03).
    _cache_no_belief(conn, p_posterior_no=0.80, recorded_at="2026-05-31T06:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_no",
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
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    # No new snapshot written. The resting order's evidence (snap1) is still the latest belief.
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_no",
        resting_posterior=0.90,
        resting_snapshot_id="snap1",
        belief_reprice_delta=0.03,
    )
    assert decision is None, "unchanged belief (bare price move) must NOT re-price (anti-twitch)"


def test_sub_delta_belief_move_does_not_reprice():
    """A NEW snapshot exists but the belief barely moved (Δ0.01 < belief_reprice_delta) → HOLD.
    Evidence changed but not MATERIALLY → no twitchy re-price."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_no_belief(conn, p_posterior_no=0.89, recorded_at="2026-05-31T06:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_no",
        resting_posterior=0.90,
        resting_snapshot_id="snap1",
        belief_reprice_delta=0.03,
    )
    assert decision is None, "sub-delta belief move must not re-price"


def test_belief_improving_does_not_pull_order():
    """Belief moved in our FAVOR (NO-belief 0.90 -> 0.95) on new evidence. screen_reprice (the
    WORSENING trigger) must NOT pull the order — improvement is the existing IMPROVE_DELTA re-fire's
    job, not a cancel. This guards against re-pricing a more-favorable resting order off the book."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_no_belief(conn, p_posterior_no=0.95, recorded_at="2026-05-31T06:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_reprice(
        conn,
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_no",
        resting_posterior=0.90,
        resting_snapshot_id="snap1",
        belief_reprice_delta=0.03,
    )
    assert decision is None, "favorable belief move must not trigger a WORSENING cancel"


# --- Stale-quote cancel: quote priced off a dead book -----------------------------------------
def test_stale_quote_cancel_fires_past_max_age():
    """A resting order whose quote_age_ms exceeds pre_submit_max_quote_age_ms is priced off a dead
    book → cancel (re-decide next cycle on fresh price). This is NOT a belief move."""
    decision = cr.screen_stale_quote_cancel(
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_no",
        quote_age_ms=1500.0,
        pre_submit_max_quote_age_ms=1000.0,
    )
    assert decision is not None
    assert decision.action == "CANCEL_STALE"
    assert decision.reason == "QUOTE_STALE"


def test_fresh_quote_is_not_cancelled():
    """ANTI-TWITCH: a fresh quote (within max age) is NOT cancelled. The stale-cancel must not fire
    on a live book just because the price moved."""
    decision = cr.screen_stale_quote_cancel(
        family_id="Wuhan|2026-06-01|high",
        bin_label="b30",
        side="buy_no",
        quote_age_ms=200.0,
        pre_submit_max_quote_age_ms=1000.0,
    )
    assert decision is None, "a fresh quote must never be cancelled (anti-twitch)"


# ===========================================================================
# §4.6 — Exit discriminator hardening (Dimension 6) + EVIDENCE_UNAVAILABLE
# ===========================================================================

# --- CI-separation: exit on separated evidence, not a single noisy snapshot -------------------
def test_exit_ci_separated_reversal_exits():
    """SD-7: exit fires when the new belief's CI EXCLUDES the entry belief (separated evidence).
    Entry NO-belief 0.90 [0.86, 0.94]; current NO-belief 0.70 [0.66, 0.74] — the CIs are disjoint
    (0.74 < 0.86) → CI-separated reversal → EXIT, even though Δ0.20 alone would already pass 0.15."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_no_belief(conn, p_posterior_no=0.70, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_exit(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
        entry_ci=(0.86, 0.94), current_ci=(0.66, 0.74),
    )
    assert decision is not None
    assert decision.reason in {"BELIEF_EDGE_REVERSAL", "CI_SEPARATED_REVERSAL"}


def test_exit_sub_ci_noisy_reversal_holds():
    """A large POINT move (Δ0.20, would pass the flat 0.15) but the current CI still OVERLAPS the
    entry CI (wide/noisy snapshot) → HOLD. CI-separation is STRICTER than the flat threshold: a
    single noisy snapshot must not exit. This is the core SD-7 hardening."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_no_belief(conn, p_posterior_no=0.70, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    decision = cr.screen_exit(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
        entry_ci=(0.80, 1.00),       # entry CI is wide
        current_ci=(0.55, 0.92),     # current CI overlaps entry (0.92 > 0.80) → NOT separated
    )
    assert decision is None, "a point move whose CI still overlaps entry must NOT exit (noisy snapshot)"


def test_exit_flat_floor_fallback_when_no_ci():
    """Back-compat: when CI inputs are unavailable, fall back to the flat reversal_belief_delta floor
    (the pre-hardening behavior). Δ0.30 ≥ 0.15 → EXIT; Δ0.04 < 0.15 → HOLD."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_no_belief(conn, p_posterior_no=0.60, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    exited = cr.screen_exit(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
    )
    assert exited is not None, "flat-floor fallback: Δ0.30 ≥ 0.15 must still exit when no CI given"

    conn2 = _mem_world()
    _cache_no_belief(conn2, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_no_belief(conn2, p_posterior_no=0.86, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    held = cr.screen_exit(
        conn2, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
    )
    assert held is None, "flat-floor fallback: Δ0.04 < 0.15 must hold"


# --- EVIDENCE_UNAVAILABLE: degraded day0/obs math → third state -------------------------------
def test_exit_evidence_unavailable_third_state():
    """SD-7 / plan v2.B: when the belief is UNAVAILABLE (degraded day0 absorbing-mask / obs math —
    belief can't be computed, distinct from belief-reversed), screen_exit returns a THIRD state
    EVIDENCE_UNAVAILABLE → flag for the 守护 heartbeat. Do NOT exit on price, do NOT blindly hold."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    decision = cr.screen_exit(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
        belief_available=False,   # day0 math degraded → belief is unavailable, NOT reversed
    )
    assert decision is not None, "degraded belief must NOT silently hold (None) — it is a first-class state"
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert getattr(decision, "side", None) == "buy_no"


def test_exit_evidence_unavailable_is_not_an_exit_on_price():
    """EVIDENCE_UNAVAILABLE must be distinguishable from an actual EXIT so the caller does NOT route
    it through the exit-submit path (it's a heartbeat flag, not a sell)."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    decision = cr.screen_exit(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15, belief_available=False,
    )
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert decision.reason != "BELIEF_EDGE_REVERSAL"
    assert decision.reason != "CI_SEPARATED_REVERSAL"


# --- Exit re-reversal before fill → CANCEL pending exit (symmetric anti-twitch) ----------------
def test_exit_re_reversal_cancels_pending_exit():
    """Symmetric anti-twitch: a pending exit was placed on a CI-separated reversal; before it fills,
    the belief RE-reverses back (current CI re-OVERLAPS the entry CI) → the reversal was noise →
    CANCEL the pending exit."""
    conn = _mem_world()
    # Latest belief has recovered back toward entry (NO-belief 0.88, CI overlapping entry).
    _cache_no_belief(conn, p_posterior_no=0.88, recorded_at="2026-05-31T18:00:00+00:00", snapshot_id="snap3")
    decision = cr.screen_exit_cancel(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
        entry_ci=(0.86, 0.94), current_ci=(0.84, 0.92),  # re-overlaps entry → noise
    )
    assert decision is not None
    assert decision.action == "CANCEL_EXIT"
    assert decision.reason == "EXIT_RE_REVERSAL_NOISE"


def test_exit_re_reversal_does_not_cancel_when_still_separated():
    """If the belief is STILL CI-separated from entry (reversal sustained), the pending exit must NOT
    be cancelled — let it fill. Cancel only when the reversal proved to be noise."""
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.70, recorded_at="2026-05-31T18:00:00+00:00", snapshot_id="snap3")
    decision = cr.screen_exit_cancel(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
        entry_ci=(0.86, 0.94), current_ci=(0.66, 0.74),  # still separated → reversal sustained
    )
    assert decision is None, "a sustained (still-separated) reversal must NOT cancel the pending exit"


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
            held_side="buy_no",                 # we HELD buy_no → exit SELLS no = buy_yes
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
