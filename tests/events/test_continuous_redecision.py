# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md (v2, critic-resolved) +
#   GOAL #36 expanded (continuous entry+exit, evidence-gated). RED-first relationship tests for the
#   continuous re-decision contract. These pin the cache (P1) + cheap-screen/enqueue (P2) API BEFORE
#   any live-core (event_reactor_adapter / reactor) edit. SHADOW semantics; no real orders.
"""Relationship tests R1/R2/R6/R7 for continuous re-decision (src.events.continuous_redecision).

The defect (commit 00b73fbbce): decisions are one-shot per FORECAST_SNAPSHOT_READY; price-move events
hard-reject NO_DIRECT_STALE_TRADE → between forecast cycles the system never re-evaluates. The fix:
each reactor cycle CHEAP-SCREENS every live (family, market) pair against a CACHED belief × a FRESH
price and ENQUEUES a synthetic re-decision event only when edge clears the bar (the full cert/kernel
then runs through the existing pending path). The cheap screen must NOT run the bootstrap-MC kernel
(p_posterior is the kernel's OUTPUT — it is read from the cache).

These are RELATIONSHIP tests: they assert the property that holds when a cached belief flows into the
screen against a price — not a single function's return. They are expected RED until
src.events.continuous_redecision is authored.
"""
from __future__ import annotations

import sqlite3

import pytest

# Intentionally import the not-yet-authored module: RED until P1+P2 land.
cr = pytest.importorskip(
    "src.events.continuous_redecision",
    reason="continuous_redecision module not yet authored (P1+P2) — relationship contract is RED",
)


def _mem_world() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # The cache uses the canonical probability_trace_fact columns the screen reads.
    cr.ensure_belief_cache_schema(conn)
    return conn


def _cache_no_belief(conn, *, p_posterior_no: float, recorded_at: str, snapshot_id: str = "snap1"):
    """Cache a 2-bin belief where the NO side of bin 'b30' has p_posterior_no."""
    cr.cache_belief(
        conn,
        family_id="Wuhan|2026-06-01|high",
        city="Wuhan",
        target_date="2026-06-01",
        snapshot_id=snapshot_id,
        calibrator_model_hash="identity",
        bin_labels=["b29", "b30"],
        # p_posterior is YES-prob per bin; NO-prob of b30 = 1 - p_posterior[b30].
        p_posterior_vec=[0.001, 1.0 - p_posterior_no],
        recorded_at=recorded_at,
    )


# ---------------------------------------------------------------------------
# R1 — entry: cached belief + open market + FRESH price, edge > min → enqueue
# ---------------------------------------------------------------------------
def test_R1_fresh_price_positive_edge_enqueues_redecision():
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.99, recorded_at="2026-05-31T00:00:00+00:00")
    # Market underprices NO: best NO cost 0.70 → edge = 0.99 - 0.70 - cost_fee ≈ +0.27.
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(
            price=0.70, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",  # before freshness_deadline → FRESH
        price_lookup=price_lookup,
        min_edge=0.01,
    )
    keys = {(e.family_id, e.bin_label, e.direction) for e in enqueued}
    assert ("Wuhan|2026-06-01|high", "b30", "buy_no") in keys
    assert all(e.event_type == "EDLI_REDECISION_PENDING" for e in enqueued)


# ---------------------------------------------------------------------------
# R2 — sub-edge: price move too small → NO enqueue, NO regret
# ---------------------------------------------------------------------------
def test_R2_sub_edge_does_not_enqueue():
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.72, recorded_at="2026-05-31T00:00:00+00:00")
    # NO cost 0.71 → edge ≈ +0.01 minus fee < min_edge → no action.
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(
            price=0.71, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn, decision_time="2026-05-31T00:30:00+00:00", price_lookup=price_lookup, min_edge=0.05
    )
    assert enqueued == []


# ---------------------------------------------------------------------------
# R6 — continuity (one-shot killer): cycle 2 price improves → second enqueue
# ---------------------------------------------------------------------------
def test_R6_second_cycle_price_improvement_reenqueues():
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00")
    acted: dict = {}  # in-memory dedup state, held across cycles (the reactor owns this live).
    # Cycle 1: NO cost 0.88 → edge ≈ +0.01 (clears min 0.01) → enqueue.
    q1 = {("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(price=0.88, freshness_deadline="2026-05-31T01:00:00+00:00")}
    e1 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T00:30:00+00:00", price_lookup=q1, min_edge=0.01, acted_state=acted)
    assert len(e1) == 1
    # Cycle 2: price improves to 0.80 → edge ≈ +0.09, materially better → re-enqueue (NOT deduped away).
    q2 = {("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(price=0.80, freshness_deadline="2026-05-31T02:00:00+00:00")}
    e2 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T01:30:00+00:00", price_lookup=q2, min_edge=0.01, acted_state=acted)
    assert len(e2) == 1, "price improved past delta → continuous re-decision must fire again (one-shot killed)"
    # Cycle 3: price unchanged (same edge) → deduped away (NOT a re-fire on noise).
    e3 = cr.enqueue_live_redecisions(conn, decision_time="2026-05-31T01:40:00+00:00", price_lookup=q2, min_edge=0.01, acted_state=acted)
    assert e3 == [], "unchanged edge must not re-fire (act-once-per-edge)"


# ---------------------------------------------------------------------------
# R7 — stale price (critic SEV-1): cached belief + STALE price → NO enqueue
# ---------------------------------------------------------------------------
def test_R7_stale_price_does_not_enqueue_phantom_edge():
    conn = _mem_world()
    _cache_no_belief(conn, p_posterior_no=0.99, recorded_at="2026-05-31T00:00:00+00:00")
    # Price looks like positive edge BUT its freshness_deadline is in the past at decision_time.
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(
            price=0.70, freshness_deadline="2026-05-31T00:10:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn,
        decision_time="2026-05-31T00:30:00+00:00",  # AFTER freshness_deadline → STALE
        price_lookup=price_lookup,
        min_edge=0.01,
    )
    assert enqueued == [], "stale price must not produce a phantom-edge re-decision (SHADOW cannot catch it downstream)"


# ---------------------------------------------------------------------------
# R3 — no cached belief → nothing enqueued (provenance: no belief = no decision)
# ---------------------------------------------------------------------------
def test_R3_no_belief_enqueues_nothing():
    conn = _mem_world()  # empty cache
    price_lookup = {
        ("Wuhan|2026-06-01|high", "b30", "buy_no"): cr.PriceQuote(
            price=0.50, freshness_deadline="2026-05-31T01:00:00+00:00"
        ),
    }
    enqueued = cr.enqueue_live_redecisions(
        conn, decision_time="2026-05-31T00:30:00+00:00", price_lookup=price_lookup, min_edge=0.01
    )
    assert enqueued == []


# ---------------------------------------------------------------------------
# R8 — exit fires on BELIEF reversal (evidence), NOT on a bare price move
# ---------------------------------------------------------------------------
def test_R8_exit_on_belief_reversal_not_price_noise():
    conn = _mem_world()
    # Entered a NO position on bin b30 when belief said NO=0.90.
    _cache_no_belief(conn, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")

    # Case A — belief later collapses to NO=0.60 (Δ0.30, material) → exit (reversal).
    _cache_no_belief(conn, p_posterior_no=0.60, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    exit_a = cr.screen_exit(
        conn, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
    )
    assert exit_a is not None and exit_a.reason == "BELIEF_EDGE_REVERSAL"

    # Case B — belief barely moves to NO=0.86 (Δ0.04, price-noise scale) → HOLD (no exit).
    conn2 = _mem_world()
    _cache_no_belief(conn2, p_posterior_no=0.90, recorded_at="2026-05-31T00:00:00+00:00", snapshot_id="snap1")
    _cache_no_belief(conn2, p_posterior_no=0.86, recorded_at="2026-05-31T12:00:00+00:00", snapshot_id="snap2")
    exit_b = cr.screen_exit(
        conn2, family_id="Wuhan|2026-06-01|high", bin_label="b30", side="buy_no",
        entry_posterior=0.90, reversal_belief_delta=0.15,
    )
    assert exit_b is None, "a non-material belief move (price-noise scale) must NOT trigger exit"
