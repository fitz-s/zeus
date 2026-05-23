# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Relationship tests for CenterBuyCalibratedShadow — §5 calibrated multinomial EV
# Reuse: synthetic data only; no external dependencies; safe to run in isolation
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §5
"""Relationship tests for src/strategy/candidates/center_buy_calibrated_shadow.py.

§5 (center_buy): Trade i* = argmax_i [ p⁻_i − a_i − ϕ(a_i) ]
  p⁻_i = inf{ p_i : p_i in calibrated confidence/conformal set }
  Enter only if p⁻_{i*} − a_{i*} − ϕ(a_{i*}) > 0.
  Calibration unavailable → no_trade (correct behavior, not conservative gate).

These tests are RELATIONSHIP tests, written BEFORE implementation (TDD required).
Order: relationship tests → implementation → function tests. Not reversible.

Four relationships verified:
  R1: edge uses p⁻ not p̂ — calibrated EV ≤ naive EV (because p⁻ ≤ p̂).
  R2: argmax selects the bin with max p⁻_i − a_i − ϕ, not max p̂_i.
  R3: calibration unavailable → CandidateDecision(outcome='no_trade').
  R4: LogScore and Brier proper-scoring rules match closed-form on synthetic.
"""

from __future__ import annotations

import random
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# DB helper — minimal schema for no_trade_events (shadow writes)
# ---------------------------------------------------------------------------

_DECISION_DDL = """
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

_NO_TRADE_DDL = """
CREATE TABLE IF NOT EXISTS no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    reason_detail       TEXT,
    strategy_key        TEXT,
    event_source        TEXT,
    shadow_runtime      INTEGER,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    schema_compatibility TEXT NOT NULL DEFAULT 'current',
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_NO_TRADE_DDL)
    conn.execute(_DECISION_DDL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Fixtures — synthetic calibration data and context builder
# ---------------------------------------------------------------------------

def _make_cal_data(n: int = 300, seed: int = 42) -> tuple[list[float], list[int]]:
    """Perfectly-calibrated synthetic (p_hat, outcome) pairs."""
    rng = random.Random(seed)
    p_hats = [rng.uniform(0.05, 0.95) for _ in range(n)]
    outcomes = [1 if rng.random() < p else 0 for p in p_hats]
    return p_hats, outcomes


def _make_context(analysis: SimpleNamespace) -> "CandidateContext":  # type: ignore[name-defined]
    from src.strategy.candidates import CandidateContext
    from src.contracts.decision_natural_key import make_decision_natural_key

    nk = make_decision_natural_key(
        market_slug="test-cb-NYC-high-2026-06-20",
        temperature_metric="high",
        target_date="2026-06-20",
        observation_time="2026-06-20T12:00:00+00:00",
        decision_seq=0,
    )
    return CandidateContext(
        natural_key=nk,
        observed_at="2026-06-20T12:00:00+00:00",
        analysis=analysis,
    )


# ---------------------------------------------------------------------------
# R1: edge uses p⁻ not p̂ — calibrated EV ≤ naive EV
# ---------------------------------------------------------------------------

def test_r1_calibrated_edge_uses_p_lower_not_p_hat() -> None:
    """R1: edge = p⁻ − a − ϕ ≤ p̂ − a − ϕ (naive EV) because p⁻ ≤ p̂.

    Relationship: §5 requires the calibrated lower bound p⁻, not the raw
    posterior p̂. If the implementation uses p̂ instead, it violates the
    §5 theorem and overstates edge confidence (anti-conservative error).

    Verification: build a single-bin scenario. The candidate's reported edge
    must equal p⁻ − a − ϕ, not p̂ − a − ϕ. Because p⁻ ≤ p̂ always holds,
    calibrated_edge ≤ naive_edge with strict inequality whenever p⁻ < p̂.
    """
    from src.strategy.candidates.center_buy_calibrated_shadow import CenterBuyCalibratedShadow
    from src.calibration.bounds import calibrated_bounds
    from src.strategy.fees import phi

    cal_p_hats, cal_outcomes = _make_cal_data(n=300, seed=1)

    # Single winning bin with clear posterior
    p_hat = 0.70
    ask = 0.60
    fee_rate = Decimal("0.02")
    fee = phi(Decimal("1"), Decimal(str(ask)), fee_rate)

    p_lo, _p_hi = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.10)
    naive_ev = p_hat - ask - float(fee)
    calibrated_ev = p_lo - ask - float(fee)

    # p⁻ ≤ p̂ always (ordering invariant from bounds.py)
    assert p_lo <= p_hat, f"bounds violated: p⁻={p_lo} > p̂={p_hat}"

    # Under the theorem, calibrated_ev ≤ naive_ev (strict when p⁻ < p̂)
    assert calibrated_ev <= naive_ev + 1e-9, (
        f"Calibrated EV {calibrated_ev:.4f} > naive EV {naive_ev:.4f}; "
        "implementation must use p⁻, not p̂"
    )

    # Now run the candidate and verify its reported edge matches calibrated_ev
    analysis = SimpleNamespace(
        multinomial_bins=[
            {"p_hat": p_hat, "ask": ask, "token_id": "0xTOK1"},
        ],
        cal_p_hats=cal_p_hats,
        cal_outcomes=cal_outcomes,
        fee_rate=fee_rate,
        alpha=0.10,
    )
    conn = _make_conn()
    ctx = _make_context(analysis)
    candidate = CenterBuyCalibratedShadow()
    from datetime import datetime, timezone
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=datetime.now(timezone.utc))

    if decision.outcome == "enter":
        reported_edge = float(decision.edge)
        # edge must be p⁻ − a − ϕ, not p̂ − a − ϕ
        assert abs(reported_edge - calibrated_ev) < 1e-6, (
            f"Reported edge={reported_edge:.6f} does not match "
            f"calibrated EV (p⁻-a-ϕ)={calibrated_ev:.6f}. "
            "Edge must use p⁻, not p̂."
        )
        # strict: calibrated_edge < naive_edge (p⁻ strictly < p̂ for finite n)
        assert reported_edge < naive_ev - 1e-9 or p_lo == p_hat, (
            "Edge should be strictly below naive EV unless p⁻ == p̂"
        )


# ---------------------------------------------------------------------------
# R2: argmax selects max calibrated-EV bin, not max-posterior bin
# ---------------------------------------------------------------------------

def test_r2_argmax_selects_max_calibrated_ev_bin() -> None:
    """R2: i* = argmax_i [ p⁻_i − a_i − ϕ(a_i) ] — calibrated EV rank, not posterior rank.

    Relationship: the argmax rule from §5 is over calibrated EV, not raw
    posterior. A bin with the highest p̂ but also the highest ask may have
    lower calibrated EV than a cheaper bin. If the implementation picks by
    p̂ rank, it selects the wrong bin.

    Calibration data: 200 near-perfect predictions (p̂=0.99, outcome=1),
    yielding q_alpha ≈ 0.01, so p⁻ ≈ p̂ − 0.02 (tight conformal bound).
    Both bins have positive calibrated EV; B (lower p̂, cheaper ask) beats A.

    Bin A: p̂=0.99, ask=0.975 → p⁻≈0.98, EV≈0.005 (higher posterior, lower EV)
    Bin B: p̂=0.95, ask=0.920 → p⁻≈0.94, EV≈0.019 (lower posterior, higher EV)
    argmax must select B.
    """
    from src.strategy.candidates.center_buy_calibrated_shadow import CenterBuyCalibratedShadow
    from src.calibration.bounds import calibrated_bounds
    from src.strategy.fees import phi

    # Tight calibration: near-perfect predictions → low nonconformity → tight p⁻
    cal_p_hats = [0.99] * 200
    cal_outcomes = [1] * 200
    fee_rate = Decimal("0.02")

    p_hat_a, ask_a = 0.99, 0.975
    p_hat_b, ask_b = 0.95, 0.920

    p_lo_a, _ = calibrated_bounds(p_hat_a, cal_p_hats, cal_outcomes, alpha=0.10)
    p_lo_b, _ = calibrated_bounds(p_hat_b, cal_p_hats, cal_outcomes, alpha=0.10)

    fee_a = float(phi(Decimal("1"), Decimal(str(ask_a)), fee_rate))
    fee_b = float(phi(Decimal("1"), Decimal(str(ask_b)), fee_rate))

    ev_a = p_lo_a - ask_a - fee_a
    ev_b = p_lo_b - ask_b - fee_b

    # Verify the scenario is as expected: B beats A by calibrated EV, both positive
    assert ev_a > 0, f"Test scenario malformed: Bin A EV={ev_a:.4f} must be > 0"
    assert ev_b > ev_a, (
        f"Test scenario malformed: calibrated EV of Bin B ({ev_b:.4f}) "
        f"must exceed Bin A ({ev_a:.4f}). Adjust parameters."
    )
    # And A beats B by raw posterior (scenario is meaningful)
    assert p_hat_a > p_hat_b, "Scenario requires p̂_A > p̂_B"

    analysis = SimpleNamespace(
        multinomial_bins=[
            {"p_hat": p_hat_a, "ask": ask_a, "token_id": "0xBIN_A"},
            {"p_hat": p_hat_b, "ask": ask_b, "token_id": "0xBIN_B"},
        ],
        cal_p_hats=cal_p_hats,
        cal_outcomes=cal_outcomes,
        fee_rate=fee_rate,
        alpha=0.10,
    )
    conn = _make_conn()
    ctx = _make_context(analysis)
    candidate = CenterBuyCalibratedShadow()
    from datetime import datetime, timezone
    decision = candidate.evaluate(context=ctx, conn=conn, decision_time=datetime.now(timezone.utc))

    assert decision.outcome == "enter", (
        f"Expected enter (both bins have positive calibrated EV={ev_a:.4f}/{ev_b:.4f}), "
        f"got no_trade: {getattr(decision, 'reason_detail', '')}"
    )
    # The candidate puts the winning token_id in 'side' field
    assert decision.side == "0xBIN_B", (
        f"argmax must select Bin B (calibrated EV={ev_b:.4f} > Bin A EV={ev_a:.4f}), "
        f"got side={decision.side}. "
        "Implementation must use calibrated EV rank, not posterior rank."
    )


# ---------------------------------------------------------------------------
# R3: calibration unavailable → no_trade
# ---------------------------------------------------------------------------

def test_r3_calibration_unavailable_produces_no_trade() -> None:
    """R3: absent cal_p_hats / cal_outcomes → CandidateDecision(outcome='no_trade').

    Relationship: §5 requires p⁻ from split conformal calibration. When the
    calibration set is unavailable (None or empty), p⁻ is undefined. The
    correct behavior per §5 is no_trade — not a fallback to naive posterior.

    Verification: three sub-cases.
      (a) cal_p_hats=None, cal_outcomes=None → no_trade.
      (b) cal_p_hats=[], cal_outcomes=[] (empty) → no_trade.
      (c) multinomial_bins=None (no bin data at all) → no_trade.
    """
    from src.strategy.candidates.center_buy_calibrated_shadow import CenterBuyCalibratedShadow
    from datetime import datetime, timezone

    candidate = CenterBuyCalibratedShadow()
    dt = datetime.now(timezone.utc)

    # (a) None calibration
    analysis_none = SimpleNamespace(
        multinomial_bins=[{"p_hat": 0.70, "ask": 0.60, "token_id": "0xTOK"}],
        cal_p_hats=None,
        cal_outcomes=None,
        fee_rate=Decimal("0.02"),
        alpha=0.10,
    )
    conn = _make_conn()
    decision_a = candidate.evaluate(context=_make_context(analysis_none), conn=conn, decision_time=dt)
    assert decision_a.outcome == "no_trade", (
        "cal_p_hats=None must produce no_trade; calibration is required by §5."
    )

    # (b) Empty calibration
    analysis_empty = SimpleNamespace(
        multinomial_bins=[{"p_hat": 0.70, "ask": 0.60, "token_id": "0xTOK"}],
        cal_p_hats=[],
        cal_outcomes=[],
        fee_rate=Decimal("0.02"),
        alpha=0.10,
    )
    conn2 = _make_conn()
    decision_b = candidate.evaluate(context=_make_context(analysis_empty), conn=conn2, decision_time=dt)
    assert decision_b.outcome == "no_trade", (
        "cal_p_hats=[] must produce no_trade; empty calibration set is insufficient."
    )

    # (c) No bin data
    analysis_no_bins = SimpleNamespace(
        multinomial_bins=None,
        cal_p_hats=[0.5, 0.6],
        cal_outcomes=[1, 0],
        fee_rate=Decimal("0.02"),
        alpha=0.10,
    )
    conn3 = _make_conn()
    decision_c = candidate.evaluate(context=_make_context(analysis_no_bins), conn=conn3, decision_time=dt)
    assert decision_c.outcome == "no_trade", (
        "multinomial_bins=None must produce no_trade; bin data required."
    )


# ---------------------------------------------------------------------------
# R4: LogScore and Brier proper-scoring rules match closed-form on synthetic
# ---------------------------------------------------------------------------

def test_r4_proper_scoring_closed_form() -> None:
    """R4: LogScore = −log p_winner; Brier = Σ(p_i−y_i)² match closed-form.

    Relationship: §5 adds multinomial proper-scoring backtest to validate
    calibration quality. The scoring functions must implement the exact
    closed-form:
      LogScore(event i, outcome=winner j): −log p_j   (natural log, bits or nats)
      Brier(event i, outcome=winner j):   Σ_k (p_k − 1[k==j])²

    Verification: two synthetic multinomial cases with known scores.

    Case 1: K=3, winner=bin 1, p=[0.7, 0.2, 0.1].
      LogScore = −log(0.7) ≈ 0.3567
      Brier    = (0.7−1)²+(0.2−0)²+(0.1−0)² = 0.09+0.04+0.01 = 0.14

    Case 2: K=2 binary, winner=bin 0, p=[0.3, 0.7].
      LogScore = −log(0.3) ≈ 1.2040
      Brier    = (0.3−1)²+(0.7−0)² = 0.49+0.49 = 0.98
    """
    import math
    from src.calibration.scoring import log_score, brier_score

    # Case 1
    p1 = [0.7, 0.2, 0.1]
    winner1 = 0
    ls1 = log_score(p1, winner1)
    bs1 = brier_score(p1, winner1)
    assert abs(ls1 - (-math.log(0.7))) < 1e-9, (
        f"LogScore case 1: got {ls1:.6f}, expected {-math.log(0.7):.6f}"
    )
    assert abs(bs1 - 0.14) < 1e-9, (
        f"Brier case 1: got {bs1:.6f}, expected 0.14"
    )

    # Case 2
    p2 = [0.3, 0.7]
    winner2 = 0
    ls2 = log_score(p2, winner2)
    bs2 = brier_score(p2, winner2)
    assert abs(ls2 - (-math.log(0.3))) < 1e-9, (
        f"LogScore case 2: got {ls2:.6f}, expected {-math.log(0.3):.6f}"
    )
    assert abs(bs2 - 0.98) < 1e-9, (
        f"Brier case 2: got {bs2:.6f}, expected 0.98"
    )
