# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §4
#                  + docs/reference/zeus_strategy_spec.md §9 (opening_inertia)
"""Relationship tests: opening_inertia_relaxation — calibrated lower-bound EV gate.

Cross-module invariants encoded here (written BEFORE implementation):

  R1: edge uses p⁻ (calibrated lower bound) NOT raw p_hat.
      A scenario where p_hat − ask − fee > 0 but p⁻ − ask − fee ≤ 0
      MUST emit no_trade. Naive code (uses raw p_hat) emits enter; correct
      code routes through calibrated_bounds() → no_trade.

  R2: λ-estimator recovers known decay rate from synthetic ticks.
      m(t) = p + (m0−p)·e^{−λ₀·t} + 0 → |λ̂ − λ₀|/λ₀ < 5%.
      Returned t₁/₂ = ln2/λ̂ consistent with λ̂.

  R3: YES entry gate: buy YES iff p⁻ − ask − phi(ask) > 0; strict boundary
      (exact zero → no_trade).

  R4: NO entry gate: buy NO iff 1 − p⁺ − noAsk − phi(noAsk) > 0; strict
      boundary (exact zero → no_trade).

  R5: calibration-unavailable (empty cal set) → no_trade with
      reason=INSUFFICIENT_VERIFIED_CALIBRATION. No fallback to raw p_hat.

  R6: shadow CandidateDecision carries verifiable params (λ, σ_cal, m0_minus_p)
      in reason_detail / edge when enter; absent these, regret-decomposition
      cannot reconstruct the claim.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.candidates import CandidateDecision
from src.strategy.candidates.opening_inertia_relaxation import (
    OpeningInertiaRelaxation,
    estimate_lambda,
)
from src.strategy.fees import phi, venue_fee_rate

# ---------------------------------------------------------------------------
# Minimal in-memory DB schema (no world DB path check on in-memory)
# ---------------------------------------------------------------------------

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
    observed_at         TEXT,
    schema_version      INTEGER,
    schema_compatibility TEXT,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

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
    edge                TEXT,
    p_posterior         TEXT,
    target_price        TEXT,
    target_size_usd     TEXT,
    shadow_runtime      INTEGER DEFAULT 1,
    schema_version      INTEGER,
    schema_compatibility TEXT,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_SHADOW_DECISION_DDL = """
CREATE TABLE IF NOT EXISTS shadow_decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    strategy_key        TEXT,
    side                TEXT,
    edge                TEXT,
    p_posterior         TEXT,
    target_price        TEXT,
    reason_detail       TEXT,
    shadow_runtime      INTEGER DEFAULT 1,
    observed_at         TEXT,
    schema_version      INTEGER,
    schema_compatibility TEXT,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_NO_TRADE_DDL)
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_SHADOW_DECISION_DDL)
    conn.commit()
    return conn


def _make_context(
    *,
    p_hat: float,
    ask: float,
    no_ask: float,
    cal_p_hats: list[float],
    cal_outcomes: list[int],
    m0: float | None = None,
    opening_ticks: list[tuple[float, float]] | None = None,
) -> Any:
    """Build a minimal analysis namespace for OpeningInertiaRelaxation.evaluate()."""
    from types import SimpleNamespace
    from src.contracts.decision_natural_key import make_decision_natural_key

    analysis = SimpleNamespace(
        p_hat=p_hat,
        ask=ask,
        no_ask=no_ask,
        cal_p_hats=cal_p_hats,
        cal_outcomes=cal_outcomes,
        m0=m0,          # opening mid-price (None → λ estimation skipped)
        opening_ticks=opening_ticks,  # list of (t_seconds, mid_price) since open
    )
    nk = make_decision_natural_key(
        market_slug="test-slug",
        temperature_metric="high",
        target_date="2026-05-22",
        observation_time="06:00",
        decision_seq=0,
    )
    from src.strategy.candidates import CandidateContext
    return CandidateContext(
        natural_key=nk,
        observed_at="2026-05-22T06:00:00Z",
        analysis=analysis,
    )


# ---------------------------------------------------------------------------
# R1: edge uses p⁻ not raw p_hat
# ---------------------------------------------------------------------------

def test_r1_edge_uses_calibrated_lower_bound_not_raw_p_hat():
    """R1 — when raw p_hat > ask+fee but p⁻ ≤ ask+fee, result must be no_trade.

    This discriminates correct code (calibrated) from naive code (raw p_hat).
    """
    from src.calibration.bounds import calibrated_bounds

    # Set up a calibration set with large spread: q_alpha will be ~0.25
    # so p⁻ = p_hat − q_alpha will be below ask+fee even though p_hat is above.
    cal_p_hats = [0.5] * 20
    cal_outcomes = [1, 0] * 10  # 50% win rate → residuals |0.5-0| or |0.5-1| = 0.5

    p_hat = 0.70
    ask = 0.65
    fee_rate = Decimal("0.05")
    ask_d = Decimal(str(ask))
    fee = phi(Decimal("1"), ask_d, fee_rate)

    # Confirm raw p_hat would pass:
    assert p_hat - ask - float(fee) > 0, "precondition: raw p_hat has apparent edge"

    # Confirm calibrated lower bound would NOT pass:
    p_lo, _ = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.10)
    assert p_lo - ask - float(fee) <= 0, "precondition: p⁻ has no edge"

    candidate = OpeningInertiaRelaxation()
    ctx = _make_context(
        p_hat=p_hat,
        ask=ask,
        no_ask=0.40,
        cal_p_hats=cal_p_hats,
        cal_outcomes=cal_outcomes,
    )
    conn = _make_conn()
    result = candidate.evaluate(
        context=ctx,
        conn=conn,
        decision_time=datetime(2026, 5, 22, 6, 0),
    )
    assert isinstance(result, CandidateDecision)
    assert result.outcome == "no_trade", (
        f"R1 FAIL: expected no_trade (p⁻={p_lo:.4f} has no edge) "
        f"but got enter — naive code is using raw p_hat={p_hat}"
    )


# ---------------------------------------------------------------------------
# R2: λ-estimator recovers known decay rate
# ---------------------------------------------------------------------------

def test_r2_lambda_estimator_recovers_known_decay():
    """R2 — estimate_lambda() recovers λ₀ from synthetic m(t) ticks within 5%."""
    import math

    p = 0.50
    m0 = 0.75
    lambda0 = 0.30  # decay rate: half-life ≈ 2.3 seconds

    # Generate 8 ticks at t=0.5,1,2,3,4,5,6,7 seconds
    ticks = [(t, p + (m0 - p) * math.exp(-lambda0 * t)) for t in [0.5, 1, 2, 3, 4, 5, 6, 7]]

    result = estimate_lambda(ticks=ticks, p_target=p)
    assert result is not None, "estimate_lambda returned None on clean synthetic data"
    lambda_hat, sigma_cal, t_half = result

    assert lambda_hat > 0, "λ̂ must be positive"
    rel_error = abs(lambda_hat - lambda0) / lambda0
    assert rel_error < 0.05, f"λ̂={lambda_hat:.4f} too far from λ₀={lambda0} (rel_err={rel_error:.3f})"

    # t₁/₂ consistency
    expected_half = math.log(2) / lambda_hat
    assert abs(t_half - expected_half) < 1e-9, (
        f"t₁/₂={t_half} inconsistent with λ̂={lambda_hat}"
    )


def test_r2_lambda_insufficient_ticks_returns_none():
    """R2b — estimate_lambda returns None when <3 valid ticks (no estimation possible)."""
    result = estimate_lambda(ticks=[(1.0, 0.60)], p_target=0.50)
    assert result is None


# ---------------------------------------------------------------------------
# R3: YES buy gate — strict boundary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("edge_sign,expected_outcome", [
    ("positive", "enter"),
    ("zero",     "no_trade"),
    ("negative", "no_trade"),
])
def test_r3_yes_buy_gate_strict_boundary(edge_sign, expected_outcome):
    """R3 — buy YES iff p⁻ − ask − phi(ask) > 0 (strict); zero → no_trade."""
    # Use a 1-sample calibration set that produces p⁻ = p_hat exactly
    # (q_alpha = 0 when all cal residuals are 0).
    cal_p_hats = [0.80]
    cal_outcomes = [1]  # residual = |1 − 0.80| = 0.20; q_alpha with n=1 ≈ 0.20
    # Actually with n=1, q_alpha = sorted_scores[0] = 0.20 → p_lo = 0.80-0.20 = 0.60

    # Re-derive p_lo for this setup
    from src.calibration.bounds import calibrated_bounds
    p_hat = 0.80
    p_lo, _ = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.10)

    fee_rate = Decimal("0.05")

    if edge_sign == "positive":
        # ask such that p_lo - ask - phi(1,ask,fee_rate) > 0
        # phi(1,a,0.05) = 0.05*a*(1-a); max at a=0.5 is 0.0125
        # choose ask = p_lo - 0.05 (clear positive)
        ask = p_lo - 0.05
    elif edge_sign == "zero":
        # ask such that p_lo - ask - phi(1,ask,fee_rate) == 0 exactly
        # p_lo = ask + 0.05*ask*(1-ask) → solve iteratively
        # approximate: ask ≈ p_lo / (1 + 0.05*(1 - ask))
        # use Newton's method for exact zero
        a = p_lo / 1.05  # initial guess
        for _ in range(50):
            f = p_lo - a - 0.05 * a * (1 - a)
            df = -1 - 0.05 * (1 - 2 * a)
            a = a - f / df
        ask = a
    else:  # negative
        ask = p_lo + 0.05

    # Ensure ask in valid range
    ask = max(0.01, min(0.99, ask))
    no_ask = 0.30  # NO side irrelevant here (YES path)

    candidate = OpeningInertiaRelaxation()
    ctx = _make_context(
        p_hat=p_hat,
        ask=ask,
        no_ask=no_ask,
        cal_p_hats=cal_p_hats,
        cal_outcomes=cal_outcomes,
    )
    conn = _make_conn()
    result = candidate.evaluate(
        context=ctx,
        conn=conn,
        decision_time=datetime(2026, 5, 22, 6, 0),
    )
    assert isinstance(result, CandidateDecision)
    assert result.outcome == expected_outcome, (
        f"R3 FAIL for edge_sign={edge_sign!r}: "
        f"expected {expected_outcome!r}, got {result.outcome!r} "
        f"(p_lo={p_lo:.4f}, ask={ask:.4f})"
    )


# ---------------------------------------------------------------------------
# R4: NO buy gate — strict boundary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("no_edge_sign,expected_outcome", [
    ("positive", "enter"),
    ("zero",     "no_trade"),
    ("negative", "no_trade"),
])
def test_r4_no_buy_gate_strict_boundary(no_edge_sign, expected_outcome):
    """R4 — buy NO iff 1 − p⁺ − noAsk − phi(noAsk) > 0 (strict); zero → no_trade."""
    from src.calibration.bounds import calibrated_bounds

    cal_p_hats = [0.20]
    cal_outcomes = [0]  # residual = |0 − 0.20| = 0.20
    p_hat = 0.20
    _, p_hi = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.10)

    fee_rate = Decimal("0.05")
    # NO edge = 1 − p_hi − noAsk − phi(1, noAsk, fee_rate)
    # phi = 0.05 * noAsk * (1 - noAsk)

    if no_edge_sign == "positive":
        no_ask = (1 - p_hi) - 0.05
    elif no_edge_sign == "zero":
        # 1 - p_hi = noAsk + 0.05 * noAsk * (1 - noAsk)
        na = (1 - p_hi) / 1.05
        for _ in range(50):
            f = (1 - p_hi) - na - 0.05 * na * (1 - na)
            df = -1 - 0.05 * (1 - 2 * na)
            na = na - f / df
        no_ask = na
    else:
        no_ask = (1 - p_hi) + 0.05

    no_ask = max(0.01, min(0.99, no_ask))
    # Make YES side unprofitable so NO path is the relevant decision
    ask = 0.95  # p_lo clearly below ask

    candidate = OpeningInertiaRelaxation()
    ctx = _make_context(
        p_hat=p_hat,
        ask=ask,
        no_ask=no_ask,
        cal_p_hats=cal_p_hats,
        cal_outcomes=cal_outcomes,
    )
    conn = _make_conn()
    result = candidate.evaluate(
        context=ctx,
        conn=conn,
        decision_time=datetime(2026, 5, 22, 6, 0),
    )
    assert isinstance(result, CandidateDecision)
    assert result.outcome == expected_outcome, (
        f"R4 FAIL for no_edge_sign={no_edge_sign!r}: "
        f"expected {expected_outcome!r}, got {result.outcome!r} "
        f"(p_hi={p_hi:.4f}, no_ask={no_ask:.4f})"
    )


# ---------------------------------------------------------------------------
# R5: calibration unavailable → INSUFFICIENT_VERIFIED_CALIBRATION
# ---------------------------------------------------------------------------

def test_r5_calibration_unavailable_emits_no_trade():
    """R5 — empty calibration set → no_trade(INSUFFICIENT_VERIFIED_CALIBRATION).

    No fallback to raw p_hat is permitted.
    """
    candidate = OpeningInertiaRelaxation()
    ctx = _make_context(
        p_hat=0.80,
        ask=0.60,
        no_ask=0.35,
        cal_p_hats=[],    # empty — calibration unavailable
        cal_outcomes=[],
    )
    conn = _make_conn()
    result = candidate.evaluate(
        context=ctx,
        conn=conn,
        decision_time=datetime(2026, 5, 22, 6, 0),
    )
    assert isinstance(result, CandidateDecision)
    assert result.outcome == "no_trade"
    assert result.reason == NoTradeReason.INSUFFICIENT_VERIFIED_CALIBRATION, (
        f"Expected INSUFFICIENT_VERIFIED_CALIBRATION, got {result.reason!r}"
    )


# ---------------------------------------------------------------------------
# R6: enter decision carries verifiable params in reason_detail
# ---------------------------------------------------------------------------

def test_r6_enter_carries_verifiable_params():
    """R6 — enter CandidateDecision carries λ, σ_cal, m0_minus_p in reason_detail."""
    from src.calibration.bounds import calibrated_bounds

    # Use tight calibration (low residuals) so p⁻ ≈ p_hat
    cal_p_hats = [0.70] * 10
    cal_outcomes = [1] * 10  # residuals = |1 − 0.70| = 0.30 all equal

    p_hat = 0.70
    p_lo, _ = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.10)

    # choose ask small enough to guarantee edge
    ask = p_lo * 0.5
    ask = max(0.01, min(0.99, ask))

    # Provide opening ticks for λ estimation
    import math
    m0 = p_hat + 0.20
    lambda0 = 0.25
    ticks = [(t, p_hat + (m0 - p_hat) * math.exp(-lambda0 * t)) for t in [1, 2, 3, 4, 5]]

    candidate = OpeningInertiaRelaxation()
    ctx = _make_context(
        p_hat=p_hat,
        ask=ask,
        no_ask=0.90,  # NO side not profitable
        cal_p_hats=cal_p_hats,
        cal_outcomes=cal_outcomes,
        m0=m0,
        opening_ticks=ticks,
    )
    conn = _make_conn()
    result = candidate.evaluate(
        context=ctx,
        conn=conn,
        decision_time=datetime(2026, 5, 22, 6, 0),
    )
    assert isinstance(result, CandidateDecision)
    assert result.outcome == "enter", f"Expected enter, got no_trade: {result}"

    # edge must be p_lo − ask − phi
    assert result.edge is not None, "enter decision must carry edge"

    # reason_detail must carry verifiable params
    rd = result.reason_detail or ""
    assert "lambda" in rd.lower() or "λ" in rd, f"reason_detail missing λ: {rd!r}"
    assert "sigma_cal" in rd.lower() or "σ_cal" in rd.lower(), f"reason_detail missing σ_cal: {rd!r}"
    assert "m0_minus_p" in rd.lower() or "m(0)" in rd.lower(), f"reason_detail missing m(0)−p: {rd!r}"
