# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §3
#                  + docs/reference/zeus_strategy_spec.md §13
"""Relationship tests: stale_quote_detector → FOK information-delay arbitrage.

These tests encode the cross-module invariants between
  (info-event feed) → (MicrostructureMetrics) → (StaleQuoteDetector) → (shadow log)
as demanded by STRATEGY_TAXONOMY_DIRECTIVE.md §3 and zeus_strategy_spec.md §13.5.

Written BEFORE implementation per operator methodology. They fail until the
implementation in stale_quote_detector.py and MicrostructureMetrics is complete.

Core invariants verified:
  R1: book hash stasis alone does NOT imply edge; posterior jump required.
  R2: info_event_observed=False → no_trade (data-gated).
  R3: p_after_lower_bound=None → no_trade (data-gated).
  R4: stale_quote_price=None → no_trade (data-gated).
  R5: book hash transitioned within threshold → no_trade (quote responded).
  R6: edge = p1 - a0 - phi(1, a0, fee_rate) ≤ 0 → no_trade.
  R7: all inputs present, stale, depth>0, edge>0 → enter; shadow row carries
      computed edge (not placeholder 0.02).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import SCHEMA_VERSION
from src.strategy.candidates import (
    CandidateContext,
    StaleQuoteDetector,
)


# ---------------------------------------------------------------------------
# Shared schema / fixtures (mirror test_phase4_t2_candidates.py patterns)
# ---------------------------------------------------------------------------

_DECISION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    condition_id        TEXT,
    decision_event_id   TEXT,
    decision_time       TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL DEFAULT '',
    polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'unknown_legacy',
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL,
    source         TEXT NOT NULL,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_NO_TRADE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    schema_compatibility TEXT NOT NULL DEFAULT 'current',
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_NO_TRADE_EVENTS_DDL)
    conn.commit()
    return conn


def _make_context(
    conn: sqlite3.Connection,
    analysis: Any,
    *,
    market_slug: str = "test-market-NYC-high-2026-06-15",
    temperature_metric: str = "high",
    target_date: str = "2026-06-15",
    observation_time: str = "2026-06-15T10:00:00+00:00",
    observed_at: str = "2026-06-15T10:00:00+00:00",
) -> CandidateContext:
    nk = make_decision_natural_key(
        market_slug=market_slug,
        temperature_metric=temperature_metric,  # type: ignore[arg-type]
        target_date=target_date,
        observation_time=observation_time,
        decision_seq=0,
    )
    return CandidateContext(
        natural_key=nk,
        observed_at=observed_at,
        analysis=analysis,
    )


def _make_metrics(**kwargs: Any) -> SimpleNamespace:
    """Build a MicrostructureMetrics-like object covering the FOK arb fields.

    FOK arb new fields (all default to data-gated / None):
      info_event_observed:  bool — True iff canonical InfoEvent known.
      p_after_lower_bound:  Optional[Decimal] — posterior lower bound p1.
      stale_quote_price:    Optional[Decimal] — executable stale ask a0.
    """
    defaults = dict(
        snapshot_id="hash-abc123",
        event_slug="test-slug",
        condition_id="0xabc",
        captured_at_iso="2026-06-15T09:00:00+00:00",
        wide_spread_display_substitution=False,
        spread_observed_window_ms=None,
        depth_at_best_ask=5,
        polymarket_end_anchor_source="gamma_explicit",
        bin_grid_id=None,
        bin_schema_version=None,
        raw_orderbook_hash_transition_delta_ms=None,
        # FOK arb fields
        info_event_observed=False,
        p_after_lower_bound=None,
        stale_quote_price=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


_DECISION_TIME = datetime(2026, 6, 15, 10, 0, 0)

# Concrete numeric inputs for edge-positive scenario.
# p1=0.65, a0=0.50, fee_rate=0.05 → phi(1, 0.50, 0.05) = 0.05*0.50*0.50 = 0.0125
# edge = 0.65 - 0.50 - 0.0125 = 0.1375 > 0.
_P1 = Decimal("0.65")
_A0 = Decimal("0.50")
_FEE_RATE = Decimal("0.05")
_EXPECTED_EDGE = _P1 - _A0 - (_FEE_RATE * _A0 * (Decimal("1") - _A0))  # 0.1375


# ---------------------------------------------------------------------------
# R1: book hash stasis alone does NOT imply edge — posterior jump required
# ---------------------------------------------------------------------------

def test_r1_book_hash_stasis_alone_is_no_trade() -> None:
    """R1: book hash stale but no InfoEvent → no_trade, not enter.

    Invariant: staleness of the book is a necessary condition, not sufficient.
    Without a known InfoEvent supplying p1, there is no edge theorem.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    metrics = _make_metrics(
        info_event_observed=False,          # no InfoEvent
        p_after_lower_bound=None,           # no posterior
        stale_quote_price=_A0,              # ask present
        depth_at_best_ask=10,
        raw_orderbook_hash_transition_delta_ms=None,  # book hash stale
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade", (
        f"R1 violated: book stasis alone must not produce enter; got {decision.outcome}"
    )
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R2: info_event_observed=False → no_trade (data-gated)
# ---------------------------------------------------------------------------

def test_r2_no_info_event_is_no_trade() -> None:
    """R2: info_event_observed=False → no_trade regardless of other fields.

    Invariant: without a canonical InfoEvent the theorem does not apply.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    metrics = _make_metrics(
        info_event_observed=False,
        p_after_lower_bound=_P1,
        stale_quote_price=_A0,
        depth_at_best_ask=10,
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade"
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R3: p_after_lower_bound=None → no_trade (data-gated)
# ---------------------------------------------------------------------------

def test_r3_missing_p_after_is_no_trade() -> None:
    """R3: p_after_lower_bound=None → no_trade.

    Invariant: without p1 we cannot compute edge; FOK theorem doesn't apply.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    metrics = _make_metrics(
        info_event_observed=True,
        p_after_lower_bound=None,           # data-gated
        stale_quote_price=_A0,
        depth_at_best_ask=10,
        raw_orderbook_hash_transition_delta_ms=None,
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade"
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R4: stale_quote_price=None → no_trade (data-gated)
# ---------------------------------------------------------------------------

def test_r4_missing_stale_quote_price_is_no_trade() -> None:
    """R4: stale_quote_price=None → no_trade.

    Invariant: without executable a0 we cannot compute edge.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    metrics = _make_metrics(
        info_event_observed=True,
        p_after_lower_bound=_P1,
        stale_quote_price=None,             # data-gated
        depth_at_best_ask=10,
        raw_orderbook_hash_transition_delta_ms=None,
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade"
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R5: book hash transitioned (fresh) → no_trade (stale-quote edge gone)
# ---------------------------------------------------------------------------

def test_r5_fresh_book_hash_is_no_trade() -> None:
    """R5: book hash responded to info event (delta_ms < threshold) → no_trade.

    Invariant: quote is only stale while book hash has not reacted.
    Once the hash transitions, the resting quote is no longer stale.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    metrics = _make_metrics(
        info_event_observed=True,
        p_after_lower_bound=_P1,
        stale_quote_price=_A0,
        depth_at_best_ask=10,
        raw_orderbook_hash_transition_delta_ms=1000,  # 1s < 120s threshold → fresh
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade"
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R6: edge ≤ 0 (p1 - a0 - phi ≤ 0) → no_trade
# ---------------------------------------------------------------------------

def test_r6_non_positive_edge_is_no_trade() -> None:
    """R6: computed edge = p1 - a0 - phi(1,a0,fee_rate) ≤ 0 → no_trade.

    Invariant: FOK info-delay arb requires strictly positive EV per share.
    A stale quote at a0 ≥ p1 − phi means no positive EV theorem.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    # Set a0 = 0.65, p1 = 0.60 → edge = 0.60 - 0.65 - phi < 0
    a0_high = Decimal("0.65")
    p1_low = Decimal("0.60")

    metrics = _make_metrics(
        info_event_observed=True,
        p_after_lower_bound=p1_low,
        stale_quote_price=a0_high,
        depth_at_best_ask=10,
        raw_orderbook_hash_transition_delta_ms=None,
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade"
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R7: all inputs present, stale, depth>0, edge>0 → enter; edge stored (not 0.02)
# ---------------------------------------------------------------------------

def test_r7_positive_edge_stale_book_writes_enter_with_computed_edge() -> None:
    """R7: full happy path → outcome='enter', shadow row edge = p1 - a0 - phi (not 0.02).

    Invariant: the logged edge must be the real computed theorem value,
    not a placeholder constant. This is the antibody against _SHADOW_EDGE=0.02.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    metrics = _make_metrics(
        info_event_observed=True,
        p_after_lower_bound=_P1,
        stale_quote_price=_A0,
        depth_at_best_ask=10,
        raw_orderbook_hash_transition_delta_ms=None,
        polymarket_end_anchor_source="gamma_explicit",
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "enter", f"Expected enter, got {decision.outcome}"
    assert decision.side == "buy_yes"

    row = conn.execute(
        "SELECT strategy_key, edge, source FROM decision_events WHERE market_slug=?",
        (ctx.natural_key[0],),
    ).fetchone()
    assert row is not None, "Expected a decision_events row"
    assert row["strategy_key"] == "stale_quote_detector"
    assert row["source"] == "shadow_decision"

    # Core antibody: edge must be the theorem value, not 0.02 placeholder.
    stored_edge = float(row["edge"])
    expected_edge = float(_EXPECTED_EDGE)
    assert abs(stored_edge - expected_edge) < 1e-9, (
        f"Edge must be theorem value {expected_edge:.6f}, "
        f"got {stored_edge:.6f} (0.02 placeholder would be caught here)"
    )


# ---------------------------------------------------------------------------
# R8: depth_at_best_ask = 0 → no_trade (quote consumed)
# ---------------------------------------------------------------------------

def test_r8_zero_depth_is_no_trade() -> None:
    """R8: stale book with depth_at_best_ask=0 → no_trade.

    Invariant: no executable quote → no fill → no FOK opportunity.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    metrics = _make_metrics(
        info_event_observed=True,
        p_after_lower_bound=_P1,
        stale_quote_price=_A0,
        depth_at_best_ask=0,                # consumed
        raw_orderbook_hash_transition_delta_ms=None,
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade"
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R9: metrics unavailable → no_trade (existing guard)
# ---------------------------------------------------------------------------

def test_r9_no_metrics_is_no_trade() -> None:
    """R9: metrics=None → no_trade (MicrostructureMetrics unavailable guard)."""
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    ctx = _make_context(conn, SimpleNamespace(metrics=None))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade"
    assert decision.reason == NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE


# ---------------------------------------------------------------------------
# R10: fee_rate gate — stale_quote_price at boundary (edge exactly 0) → no_trade
# ---------------------------------------------------------------------------

def test_r10_zero_edge_at_boundary_is_no_trade() -> None:
    """R10: edge = p1 - a0 - phi exactly = 0 → no_trade (requires strictly positive edge).

    Boundary: set a0 = p1 - phi so that edge = 0.
    phi(1, 0.50, 0.05) = 0.0125. So p1=0.5125, a0=0.50 → edge = 0.
    """
    conn = _make_conn()
    candidate = StaleQuoteDetector()

    fee_rate = Decimal("0.05")
    a0 = Decimal("0.50")
    phi_val = fee_rate * a0 * (Decimal("1") - a0)   # 0.0125
    p1_zero_edge = a0 + phi_val                      # 0.5125 → edge = 0

    metrics = _make_metrics(
        info_event_observed=True,
        p_after_lower_bound=p1_zero_edge,
        stale_quote_price=a0,
        depth_at_best_ask=10,
        raw_orderbook_hash_transition_delta_ms=None,
    )
    ctx = _make_context(conn, SimpleNamespace(metrics=metrics))
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade", (
        f"Edge=0 must produce no_trade; got {decision.outcome}"
    )
