# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §9
#                  + docs/reference/zeus_strategy_spec.md §10
"""Relationship tests: imminent_open_capture → short-horizon posterior-collapse arbitrage.

Cross-module invariants between
  (calibrated_bounds) → (ImminentOpenCapturePosteriorCollapse) → (shadow log)
per STRATEGY_TAXONOMY_DIRECTIVE.md §9 and zeus_strategy_spec.md §10.

Written BEFORE implementation per operator methodology. They fail until the
implementation in imminent_open_capture_posterior_collapse.py is complete.

Theorem (§9): T* = μ_t + η_t, Var(η_t) = σ²(τ) ↓ 0 as τ ↓ 0.
  YES iff p⁻_i(t) − a_i − phi(1, a_i, fee_rate) > 0
  NO  iff 1 − p⁺_i(t) − b_i − phi(1, b_i, fee_rate) > 0

Core invariants:
  R1: YES entry uses p⁻ (calibrated lower bound), NOT raw p_hat.
  R2: NO entry uses p⁺ (calibrated upper bound), NOT raw p_hat.
  R3: Variance collapse: calibration interval narrows as τ → 0 (tighter q_alpha
      → smaller [p⁻, p⁺] width). More residual spread in cal set = wider bound.
  R4: YES gate: p⁻ − ask − phi > 0 required; zero or negative edge → no_trade.
  R5: NO gate: 1 − p⁺ − bid − phi > 0 required; non-positive edge → no_trade.
  R6: Calibration unavailable (empty cal set) → no_trade (fail-closed).
  R7: All inputs present, edge > 0 → enter; shadow row carries computed edge (not placeholder).
  R8: p_hat/ask present but both YES and NO gates negative → no_trade.
  R9: Analysis missing (None) → no_trade (data-gated guard).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import SCHEMA_VERSION
from src.strategy.candidates import (
    CandidateContext,
)
from src.strategy.candidates.imminent_open_capture_posterior_collapse import (
    ImminentOpenCapturePosteriorCollapse,
)


# ---------------------------------------------------------------------------
# Schema / fixtures
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


def _make_analysis(
    p_hat: float = 0.65,
    ask: float = 0.50,        # YES ask (a_i)
    bid: float = 0.40,        # NO ask (b_i)
    cal_p_hats: Sequence[float] = (0.6, 0.7, 0.5, 0.8, 0.4),
    cal_outcomes: Sequence[int] = (1, 1, 0, 1, 0),
    hours_to_resolution: float = 8.0,
    **overrides: Any,
) -> SimpleNamespace:
    """Build a minimal analysis object for the imminent posterior-collapse candidate.

    Fields the candidate reads:
      p_hat: point probability estimate.
      ask: YES ask price a_i.
      bid: NO ask price b_i.
      cal_p_hats: calibration set probability estimates (for calibrated_bounds).
      cal_outcomes: calibration set binary outcomes.
      hours_to_resolution: τ (time to resolution in hours); affects σ²(τ).
    """
    defaults = dict(
        p_hat=p_hat,
        ask=ask,
        bid=bid,
        cal_p_hats=list(cal_p_hats),
        cal_outcomes=list(cal_outcomes),
        hours_to_resolution=hours_to_resolution,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_DECISION_TIME = datetime(2026, 6, 15, 10, 0, 0)

# Fee rate matching Polymarket weather (§0 STRATEGY_TAXONOMY_DIRECTIVE)
_FEE_RATE = Decimal("0.05")

# Concrete YES-entry scenario: p_hat=0.80, ask=0.50 — wide gap makes p⁻ > ask+phi easy.
# Tight cal set (all scores ~0.0) → small q_alpha → p⁻ ≈ p_hat.
_TIGHT_CAL_P_HATS = [0.80, 0.80, 0.80, 0.80, 0.80]
_TIGHT_CAL_OUTCOMES = [1, 1, 1, 1, 1]  # perfect calibration → scores = |1-0.80| = 0.20 each

# phi(1, 0.50, 0.05) = 0.05 * 0.50 * 0.50 = 0.0125
# p⁻ = max(0, 0.80 - q_alpha); with all scores = 0.20, q_alpha = 0.20 → p⁻ = 0.60
# edge_YES = 0.60 - 0.50 - 0.0125 = 0.0875 > 0  ✓


# ---------------------------------------------------------------------------
# R1: YES entry uses p⁻ (calibrated lower bound), NOT raw p_hat
# ---------------------------------------------------------------------------

def test_r1_yes_entry_uses_p_lower_not_raw_p_hat() -> None:
    """R1: candidate uses p⁻ = calibrated_bounds(p_hat, ...)[0] for YES gate.

    Invariant: if raw p_hat would pass the YES gate but p⁻ would not, outcome
    must be no_trade (the calibrated lower bound is the binding constraint).
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    # p_hat=0.70, ask=0.65: raw gap 0.05; phi(0.65)=0.05*0.65*0.35≈0.0114
    # raw YES edge ≈ 0.70 - 0.65 - 0.0114 = 0.0386 > 0  (raw p_hat would enter)
    # Cal set: scores all = |1 - 0.70| = 0.30 → q_alpha = 0.30 → p⁻ = max(0, 0.40)
    # p⁻ YES edge = 0.40 - 0.65 - 0.0114 < 0  → must be no_trade
    analysis = _make_analysis(
        p_hat=0.70,
        ask=0.65,
        bid=0.20,        # NO gate also infeasible (1-p⁺-bid-phi: p⁺=1.0, edge≤0)
        cal_p_hats=[0.70, 0.70, 0.70, 0.70, 0.70],
        cal_outcomes=[1, 1, 1, 1, 1],   # all outcomes 1 → scores = 0.30 each
        hours_to_resolution=8.0,
    )
    ctx = _make_context(conn, analysis)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade", (
        f"R1: p⁻ << ask; must be no_trade but got {decision.outcome}. "
        "Candidate appears to be using raw p_hat instead of calibrated p⁻."
    )


# ---------------------------------------------------------------------------
# R2: NO entry uses p⁺ (calibrated upper bound), NOT raw p_hat
# ---------------------------------------------------------------------------

def test_r2_no_entry_uses_p_upper_not_raw_p_hat() -> None:
    """R2: candidate uses p⁺ = calibrated_bounds(p_hat, ...)[1] for NO gate.

    Invariant: if raw 1-p_hat would pass the NO gate but 1-p⁺ would not, outcome
    must be no_trade.
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    # p_hat=0.30, bid=0.25: raw NO gap = 1-0.30-0.25 = 0.45; phi(0.25)=0.05*0.25*0.75=0.009
    # raw NO edge ≈ 0.45 - 0.009 > 0  (would enter if using raw p_hat)
    # Cal set: scores all = |1 - 0.30| = 0.70 → q_alpha = 0.70 → p⁺ = min(1, 0.30+0.70)=1.0
    # p⁺ NO edge = 1 - 1.0 - 0.25 - phi = -0.25 - phi < 0  → must be no_trade
    analysis = _make_analysis(
        p_hat=0.30,
        ask=0.80,        # YES gate: p⁻=max(0,0.30-0.70)=0 < ask → no_trade
        bid=0.25,
        cal_p_hats=[0.30, 0.30, 0.30, 0.30, 0.30],
        cal_outcomes=[1, 1, 1, 1, 1],   # scores = |1-0.30| = 0.70
        hours_to_resolution=8.0,
    )
    ctx = _make_context(conn, analysis)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade", (
        f"R2: 1-p⁺ << bid; must be no_trade but got {decision.outcome}. "
        "Candidate appears to be using raw 1-p_hat instead of calibrated 1-p⁺."
    )


# ---------------------------------------------------------------------------
# R3: Variance collapse — tighter cal residuals → narrower [p⁻, p⁺] width
# ---------------------------------------------------------------------------

def test_r3_variance_collapse_tighter_residuals_produce_narrower_bounds() -> None:
    """R3: σ²(τ) ↓ 0 as τ ↓ 0 encoded as: smaller cal residuals → narrower bounds.

    Invariant: the conformal interval width p⁺ - p⁻ is proportional to the
    calibration quantile q_alpha. Perfect calibration set has scores ≈ 0 → width ≈ 0.
    Wide residuals (high uncertainty, large τ) → q_alpha large → wide interval.

    This test verifies that the SAME p_hat with different calibration residual
    magnitudes produces systematically narrower vs wider bounds, encoding
    the Var(η_t)=σ²(τ)↓0 theorem.
    """
    from src.calibration.bounds import calibrated_bounds

    p_hat = 0.70
    alpha = 0.10

    # Wide residuals: simulate far-from-resolution forecast (large τ → large σ²)
    # Scores ≈ 0.40 → wide q_alpha
    cal_p_hats_wide = [0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70]
    cal_outcomes_wide = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]  # mixed → scores = 0.30

    # Tight residuals: simulate near-resolution (small τ → small σ²)
    # Near-perfect calibration → scores ≈ 0.05
    cal_p_hats_tight = [0.95, 0.96, 0.94, 0.95, 0.95, 0.96, 0.94, 0.95, 0.95, 0.96]
    cal_outcomes_tight = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]  # all correct → scores small

    lo_wide, hi_wide = calibrated_bounds(
        p_hat, cal_p_hats_wide, cal_outcomes_wide, alpha=alpha
    )
    lo_tight, hi_tight = calibrated_bounds(
        p_hat, cal_p_hats_tight, cal_outcomes_tight, alpha=alpha
    )

    width_wide = hi_wide - lo_wide
    width_tight = hi_tight - lo_tight

    assert width_tight < width_wide, (
        f"R3 violated: tight-residual bound width ({width_tight:.4f}) must be < "
        f"wide-residual bound width ({width_wide:.4f}). "
        "σ²(τ)↓0 as τ↓0 not encoded by calibration interval."
    )


# ---------------------------------------------------------------------------
# R4: YES gate — p⁻ − ask − phi > 0 required; negative edge → no_trade
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ask,expected_outcome", [
    # ask=0.90: p⁻ ≈ 0.60 (with tight cal), edge = 0.60 - 0.90 - phi < 0 → no_trade
    (0.90, "no_trade"),
    # ask=0.50: p⁻ ≈ 0.60, edge = 0.60 - 0.50 - 0.0125 = 0.0875 > 0 → enter
    (0.50, "enter"),
])
def test_r4_yes_gate_edge_sign_determines_outcome(ask: float, expected_outcome: str) -> None:
    """R4: YES gate strictly requires p⁻ − ask − phi > 0.

    Boundary: ask=0.90 → negative edge → no_trade. ask=0.50 → positive → enter.
    Both with p_hat=0.80 and tight cal so p⁻ ≈ 0.60.
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    # Tight cal: scores = |1-0.80| = 0.20 → q_alpha = 0.20 → p⁻ = max(0, 0.60)
    analysis = _make_analysis(
        p_hat=0.80,
        ask=ask,
        bid=0.10,    # NO gate: 1-p⁺=1-min(1,0.80+0.20)=0 → no_trade on NO path regardless
        cal_p_hats=[0.80, 0.80, 0.80, 0.80, 0.80],
        cal_outcomes=[1, 1, 1, 1, 1],
        hours_to_resolution=8.0,
    )
    ctx = _make_context(conn, analysis)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == expected_outcome, (
        f"R4: ask={ask}, expected {expected_outcome}, got {decision.outcome}. "
        f"YES gate p⁻−ask−phi must be strictly positive for enter."
    )


# ---------------------------------------------------------------------------
# R5: NO gate — 1 − p⁺ − bid − phi > 0 required; non-positive → no_trade
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bid,expected_outcome", [
    # bid=0.60: 1-p⁺-bid-phi = 1-0.60-0.60-phi < 0 → no_trade
    (0.60, "no_trade"),
    # bid=0.10: 1-p⁺=1-0.60=0.40, edge=0.40-0.10-phi(0.10)>0 → enter
    (0.10, "enter"),
])
def test_r5_no_gate_edge_sign_determines_outcome(bid: float, expected_outcome: str) -> None:
    """R5: NO gate strictly requires 1 − p⁺ − bid − phi > 0.

    p_hat=0.40, tight cal → p⁺ ≈ 0.60.
    bid=0.60: 1-0.60-0.60-phi < 0 → no_trade.
    bid=0.10: 1-0.60-0.10-phi(0.10) > 0 → enter.
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    # Tight cal: scores = |0-0.40| = 0.60 → q_alpha=0.60 → p⁺=min(1,0.40+0.60)=1.0
    # Wait: we need p⁺ to be manageable. Use p_hat=0.40, outcomes=0 (all fail):
    # scores = |0-0.40| = 0.40 each → q_alpha=0.40 → p⁺=min(1,0.40+0.40)=0.80
    # YES gate: p⁻=max(0,0.40-0.40)=0.0 < ask=0.90 → no_trade on YES regardless
    # NO gate (bid=0.10): 1-0.80-0.10-phi(1,0.10,0.05)=0.10-0.004=0.096>0 → enter
    # NO gate (bid=0.60): 1-0.80-0.60-phi(0.60) = -0.40-phi < 0 → no_trade
    analysis = _make_analysis(
        p_hat=0.40,
        ask=0.90,    # YES gate always no_trade (p⁻=0.0)
        bid=bid,
        cal_p_hats=[0.40, 0.40, 0.40, 0.40, 0.40],
        cal_outcomes=[0, 0, 0, 0, 0],   # all Y=0, scores = |0-0.40| = 0.40
        hours_to_resolution=8.0,
    )
    ctx = _make_context(conn, analysis)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == expected_outcome, (
        f"R5: bid={bid}, expected {expected_outcome}, got {decision.outcome}. "
        f"NO gate 1−p⁺−bid−phi must be strictly positive for enter."
    )
    if expected_outcome == "enter":
        assert decision.side == "buy_no", (
            f"R5: NO entry must have side='buy_no', got {decision.side}"
        )


# ---------------------------------------------------------------------------
# R6: Calibration unavailable (empty cal set) → no_trade (fail-closed)
# ---------------------------------------------------------------------------

def test_r6_empty_calibration_set_is_no_trade() -> None:
    """R6: empty cal_p_hats → calibrated_bounds raises ValueError → no_trade (fail-closed).

    Invariant: without a calibration set, p⁻/p⁺ cannot be computed → no theorem
    applies → fail-closed no_trade. Never raises; never returns enter.
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    analysis = _make_analysis(
        p_hat=0.80,
        ask=0.50,
        bid=0.10,
        cal_p_hats=[],       # empty → calibrated_bounds raises ValueError
        cal_outcomes=[],
        hours_to_resolution=8.0,
    )
    ctx = _make_context(conn, analysis)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade", (
        f"R6: empty calibration set must produce no_trade; got {decision.outcome}"
    )
    assert decision.reason == NoTradeReason.IMMINENT_CALIBRATION_UNAVAILABLE, (
        f"R6: reason must be IMMINENT_CALIBRATION_UNAVAILABLE, got {decision.reason}"
    )


# ---------------------------------------------------------------------------
# R7: Happy path — all inputs present, YES edge > 0 → enter with computed edge
# ---------------------------------------------------------------------------

def test_r7_positive_yes_edge_writes_enter_with_computed_edge() -> None:
    """R7: full YES happy path → outcome='enter', side='buy_yes', edge = p⁻ − ask − phi.

    Antibody: edge must be the theorem value, not a placeholder constant.
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    # p_hat=0.80, scores=0.20 each → q_alpha=0.20 → p⁻=max(0,0.60)=0.60
    # phi(1, 0.50, 0.05) = 0.05*0.50*0.50 = 0.0125
    # edge = 0.60 - 0.50 - 0.0125 = 0.0875
    analysis = _make_analysis(
        p_hat=0.80,
        ask=0.50,
        bid=0.10,    # NO gate: 1-p⁺=1-min(1,1.0)=0 → no YES wins by positive edge
        cal_p_hats=[0.80, 0.80, 0.80, 0.80, 0.80],
        cal_outcomes=[1, 1, 1, 1, 1],
        hours_to_resolution=8.0,
    )
    ctx = _make_context(conn, analysis)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "enter", f"R7: expected enter, got {decision.outcome}"
    assert decision.side == "buy_yes", f"R7: expected buy_yes, got {decision.side}"

    row = conn.execute(
        "SELECT strategy_key, edge, source FROM decision_events WHERE market_slug=?",
        (ctx.natural_key[0],),
    ).fetchone()
    assert row is not None, "R7: expected a decision_events row"
    assert row["strategy_key"] == "imminent_open_capture_posterior_collapse"
    assert row["source"] == "shadow_decision"

    # Antibody: edge must be theorem value, not a constant placeholder.
    stored_edge = float(row["edge"])
    # p⁻ = max(0, 0.80 - 0.20) = 0.60; phi(1,0.50,0.05) = 0.0125; edge = 0.0875
    expected_edge = 0.60 - 0.50 - 0.0125
    assert abs(stored_edge - expected_edge) < 1e-6, (
        f"R7: edge must be theorem value {expected_edge:.6f}, got {stored_edge:.6f}. "
        "Any placeholder constant would be caught here."
    )


# ---------------------------------------------------------------------------
# R8: Both YES and NO gates negative → no_trade
# ---------------------------------------------------------------------------

def test_r8_both_gates_negative_is_no_trade() -> None:
    """R8: when NEITHER YES gate (p⁻ − ask − phi > 0) NOR NO gate (1−p⁺−bid−phi > 0)
    is met, outcome must be no_trade.

    No edge theorem applies → no trade. This is the baseline case for many markets.
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    # p_hat=0.50 with tight cal → p⁻≈0.30, p⁺≈0.70
    # ask=0.80: YES edge = 0.30-0.80-phi < 0 → no
    # bid=0.40: NO edge = 1-0.70-0.40-phi < 0 → no
    analysis = _make_analysis(
        p_hat=0.50,
        ask=0.80,
        bid=0.40,
        cal_p_hats=[0.50, 0.50, 0.50, 0.50, 0.50],
        cal_outcomes=[1, 0, 1, 0, 1],   # scores = 0.50 → q_alpha=0.50 → [0.0, 1.0]
        hours_to_resolution=8.0,
    )
    ctx = _make_context(conn, analysis)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade", (
        f"R8: both gates negative must produce no_trade, got {decision.outcome}"
    )


# ---------------------------------------------------------------------------
# R9: Analysis missing → no_trade (data-gated guard)
# ---------------------------------------------------------------------------

def test_r9_missing_analysis_is_no_trade() -> None:
    """R9: analysis=None → no_trade (all fields absent, data-gated).

    Guard ensures evaluate() never raises even if caller passes None analysis.
    """
    conn = _make_conn()
    candidate = ImminentOpenCapturePosteriorCollapse()

    ctx = _make_context(conn, None)
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=_DECISION_TIME)

    assert decision.outcome == "no_trade", (
        f"R9: None analysis must produce no_trade, got {decision.outcome}"
    )
    assert decision.reason == NoTradeReason.IMMINENT_CALIBRATION_UNAVAILABLE
