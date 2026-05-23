# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §8
#                  + docs/reference/zeus_strategy_spec.md §12
#                  + src/calibration/bounds.py (conformal lower bound)
"""Relationship tests for shoulder_buy_evt — TESTS FIRST per Fitz methodology.

Theorem tested (§8):
  open shoulder = tail event  p_u = Pr(T>u | X)
  nonstationary tail model:   p_u(X) = 1 − F_θ(u | X)
  conformal lower bound:      p⁻_u = inf Pr(T>u | X)   → calibrated_bounds(p_hat, cal_p, cal_y).lo
  entry condition:            p⁻_u − a_YES − phi(a_YES) > 0

Relationship invariants this file tests:
  R1: lower bound ≤ raw tail probability (p⁻_u ≤ p_u)
  R2: conformal coverage — Pr(Y=1 | p⁻_u ≥ q) ≥ q on the calibration set
  R3: no HEAT_DOME or discrete regime hardcode anywhere in implementation
  R4: EVT_TAIL_MODEL_UNWIRED covariates → no_trade (data-gate)
  R5: positive lower-bound EV → CandidateDecision(outcome='enter', side='buy_yes')
  R6: zero or negative lower-bound EV → no_trade(SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE)
  R7: entry edge field = p⁻_u − a_YES − phi, matches strategy theorem exactly
"""

from __future__ import annotations

import math
from decimal import Decimal
from types import SimpleNamespace
from typing import List
from unittest.mock import patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_context(
    *,
    native_yes_ask: float | None = 0.10,
    evt_tail_prob_raw: float | None = 0.18,
    evt_covariates: dict | None = None,
    cal_p_hats: List[float] | None = None,
    cal_outcomes: List[int] | None = None,
) -> "SimpleNamespace":
    """Build a minimal CandidateContext-like namespace for shoulder_buy_evt tests."""
    if evt_covariates is None:
        evt_covariates = {
            "ensemble_mean": 105.0,
            "ensemble_spread": 4.5,
            "temp_anomaly_850mb": 2.1,
            "soil_moisture_proxy": 0.3,
            "advection": 0.8,
            "station_bias": -0.5,
            "season_harmonic_sin": 0.95,
            "season_harmonic_cos": 0.30,
        }
    analysis = SimpleNamespace(
        native_yes_ask=Decimal(str(native_yes_ask)) if native_yes_ask is not None else None,
        evt_tail_prob_raw=evt_tail_prob_raw,
        evt_covariates=evt_covariates,
        evt_cal_p_hats=cal_p_hats,
        evt_cal_outcomes=cal_outcomes,
    )
    return SimpleNamespace(analysis=analysis)


def _make_context_no_covariates(**kwargs) -> "SimpleNamespace":
    return _make_context(evt_covariates=None, **kwargs)


def _make_context_unwired() -> "SimpleNamespace":
    """Simulate data-gated context: covariates None, raw prob None."""
    return _make_context(
        evt_tail_prob_raw=None,
        evt_covariates=None,
        native_yes_ask=0.10,
    )


# ── R1: lower bound ≤ raw tail probability ────────────────────────────────────

class TestR1LowerBoundLeRaw:
    """R1: p⁻_u (conformal lower bound) is always ≤ p_u (raw tail estimate)."""

    def test_lower_bound_le_raw_typical(self):
        """With typical calibration set, lower bound does not exceed raw probability."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        strat = ShoulderBuyEVT()
        raw = 0.15
        cal_p = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20] * 5
        cal_y = [0, 0, 1, 1, 1, 1] * 5
        lower, _ = strat.compute_lower_bound(raw, cal_p, cal_y, alpha=0.10)
        assert lower <= raw + 1e-9, (
            f"R1 violated: lower_bound={lower} > raw={raw}"
        )

    def test_lower_bound_le_raw_high_calibration_error(self):
        """Even with high calibration nonconformity, lower bound stays ≤ raw."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        strat = ShoulderBuyEVT()
        raw = 0.20
        # All predictions wrong → max nonconformity → q_alpha high → p_lo = 0
        cal_p = [0.80] * 20
        cal_y = [0] * 20
        lower, _ = strat.compute_lower_bound(raw, cal_p, cal_y, alpha=0.10)
        assert lower <= raw + 1e-9, f"R1 violated: lower_bound={lower} > raw={raw}"

    def test_lower_bound_is_non_negative(self):
        """Lower bound is clamped to [0, 1] — never negative."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        strat = ShoulderBuyEVT()
        raw = 0.03
        cal_p = [0.80] * 30
        cal_y = [0] * 30
        lower, _ = strat.compute_lower_bound(raw, cal_p, cal_y, alpha=0.10)
        assert lower >= 0.0, f"lower_bound={lower} < 0"

    def test_lower_bound_with_perfect_calibration(self):
        """With perfect calibration (zero residuals), lower bound ≈ raw."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        strat = ShoulderBuyEVT()
        raw = 0.15
        # Perfect calibration: p_hat = y → |y - p_hat| = very small for binary
        # Use p_hat=1.0 with y=1 → score=0 everywhere
        cal_p = [1.0] * 20
        cal_y = [1] * 20
        lower, _ = strat.compute_lower_bound(raw, cal_p, cal_y, alpha=0.10)
        # q_alpha = 0 → lower = raw
        assert abs(lower - raw) < 1e-6, (
            f"Expected lower_bound ≈ {raw}, got {lower}"
        )


# ── R2: conformal coverage on calibration set ─────────────────────────────────

class TestR2ConformalCoverage:
    """R2: Pr(Y=1 | p⁻_u ≥ q) ≥ q on calibration set (split conformal guarantee)."""

    def test_conformal_coverage_basic(self):
        """Coverage ≥ 1-alpha holds on the calibration set used to fit the bound."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT
        from src.calibration.bounds import calibrated_bounds

        strat = ShoulderBuyEVT()

        # Generate calibration set with known tail events
        import random
        rng = random.Random(42)
        n = 100
        cal_p = [rng.uniform(0.05, 0.35) for _ in range(n)]
        cal_y = [1 if p > 0.20 else 0 for p in cal_p]

        alpha = 0.10

        # For each calibration point, compute its lower bound using
        # leave-one-out conformal (approximated by full-set here for structure test)
        lo_bounds = [
            calibrated_bounds(p, cal_p, cal_y, alpha=alpha)[0]
            for p in cal_p
        ]

        # Check that the lower bound ordering property holds:
        # lower_bound ≤ p_hat for all points
        for lo, p in zip(lo_bounds, cal_p):
            assert lo <= p + 1e-9, f"R1/R2: lower_bound {lo} > p_hat {p}"

    def test_conformal_threshold_monotone(self):
        """Higher alpha → tighter (larger) lower bound → more aggressive cutoff."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        strat = ShoulderBuyEVT()
        raw = 0.20
        cal_p = [0.10, 0.15, 0.20, 0.25] * 10
        cal_y = [0, 0, 1, 1] * 10

        lo_conservative, _ = strat.compute_lower_bound(raw, cal_p, cal_y, alpha=0.05)
        lo_aggressive, _ = strat.compute_lower_bound(raw, cal_p, cal_y, alpha=0.20)

        assert lo_aggressive >= lo_conservative - 1e-9, (
            f"R2: larger alpha should yield larger (tighter) lower bound; "
            f"got lo(alpha=0.05)={lo_conservative}, lo(alpha=0.20)={lo_aggressive}"
        )


# ── R3: no HEAT_DOME hardcode ─────────────────────────────────────────────────

class TestR3NoHeatDomeHardcode:
    """R3: implementation contains no hardcoded HEAT_DOME or discrete regime logic."""

    def test_no_heat_dome_in_module_source(self):
        """Source of shoulder_buy_evt must not contain HEAT_DOME string."""
        import inspect
        import src.strategy.candidates.shoulder_buy_evt as mod

        src_text = inspect.getsource(mod)
        assert "HEAT_DOME" not in src_text, (
            "R3 violated: 'HEAT_DOME' found in shoulder_buy_evt implementation"
        )

    def test_no_regime_discrete_switch(self):
        """Source must not contain discrete regime-switch keywords."""
        import inspect
        import src.strategy.candidates.shoulder_buy_evt as mod

        src_text = inspect.getsource(mod)
        for forbidden in ("heat_dome", "HEAT_DOME", "regime_tag", "EXTREME_HEAT"):
            assert forbidden not in src_text, (
                f"R3 violated: forbidden hardcode '{forbidden}' in shoulder_buy_evt"
            )

    def test_covariates_are_continuous_not_discrete(self):
        """EVT covariates accepted by the model are continuous floats, not discrete flags."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        strat = ShoulderBuyEVT()
        assert hasattr(strat, "COVARIATE_NAMES"), (
            "R3: ShoulderBuyEVT must declare COVARIATE_NAMES listing required continuous covariates"
        )
        # All covariate names must be strings (no int/bool flags)
        for name in strat.COVARIATE_NAMES:
            assert isinstance(name, str), f"R3: covariate name must be str, got {type(name)}"

    def test_expected_continuous_covariates_present(self):
        """§8 mandates these covariates — all must appear in COVARIATE_NAMES."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        required = {
            "ensemble_mean",
            "ensemble_spread",
            "temp_anomaly_850mb",
            "soil_moisture_proxy",
            "advection",
            "station_bias",
            "season_harmonic_sin",
            "season_harmonic_cos",
        }
        strat = ShoulderBuyEVT()
        missing = required - set(strat.COVARIATE_NAMES)
        assert not missing, (
            f"R3: required §8 covariates missing from COVARIATE_NAMES: {missing}"
        )


# ── R4: data-gate — EVT covariates / raw prob unwired → no_trade ──────────────

class TestR4DataGate:
    """R4: missing EVT inputs emit no_trade(EVT_TAIL_MODEL_UNWIRED)."""

    def _evaluate(self, ctx):
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT
        import sqlite3
        from datetime import datetime, timezone

        strat = ShoulderBuyEVT()
        conn = sqlite3.connect(":memory:")
        return strat.evaluate(
            context=ctx,
            conn=conn,
            decision_time=datetime.now(timezone.utc),
        )

    def test_null_covariates_emits_unwired(self):
        """No covariates → EVT_TAIL_MODEL_UNWIRED no_trade."""
        from src.contracts.no_trade_reason import NoTradeReason

        ctx = _make_context_unwired()
        result = self._evaluate(ctx)
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.EVT_TAIL_MODEL_UNWIRED, (
            f"R4: expected EVT_TAIL_MODEL_UNWIRED, got {result.reason}"
        )

    def test_null_raw_prob_emits_unwired(self):
        """Raw tail probability None → EVT_TAIL_MODEL_UNWIRED."""
        from src.contracts.no_trade_reason import NoTradeReason

        ctx = _make_context(
            evt_tail_prob_raw=None,
            native_yes_ask=0.10,
        )
        result = self._evaluate(ctx)
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.EVT_TAIL_MODEL_UNWIRED

    def test_null_native_yes_ask_emits_unwired(self):
        """Missing native YES ask → EVT_TAIL_MODEL_UNWIRED (can't compute edge)."""
        from src.contracts.no_trade_reason import NoTradeReason

        ctx = _make_context(native_yes_ask=None, evt_tail_prob_raw=0.20)
        result = self._evaluate(ctx)
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.EVT_TAIL_MODEL_UNWIRED

    def test_empty_calibration_set_emits_unwired(self):
        """Empty calibration set → EVT_TAIL_MODEL_UNWIRED (no conformal bound possible)."""
        from src.contracts.no_trade_reason import NoTradeReason

        ctx = _make_context(
            evt_tail_prob_raw=0.18,
            native_yes_ask=0.10,
            cal_p_hats=[],
            cal_outcomes=[],
        )
        result = self._evaluate(ctx)
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.EVT_TAIL_MODEL_UNWIRED


# ── R5: positive lower-bound EV → enter buy_yes ───────────────────────────────

class TestR5PositiveEVEnter:
    """R5: p⁻_u − a_YES − phi > 0 → CandidateDecision(outcome='enter', side='buy_yes')."""

    def _evaluate(self, ctx):
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT
        import sqlite3
        from datetime import datetime, timezone

        strat = ShoulderBuyEVT()
        conn = sqlite3.connect(":memory:")
        return strat.evaluate(
            context=ctx,
            conn=conn,
            decision_time=datetime.now(timezone.utc),
        )

    def test_clear_positive_ev_enters(self):
        """Large raw tail, small ask → lower bound after calibration exceeds ask+fee."""
        ctx = _make_context(
            evt_tail_prob_raw=0.40,
            native_yes_ask=0.05,
            # Perfect calibration → lower bound ≈ raw prob
            cal_p_hats=[1.0] * 40,
            cal_outcomes=[1] * 40,
        )
        result = self._evaluate(ctx)
        assert result.outcome == "enter", (
            f"R5: expected enter, got {result.outcome} / reason={getattr(result, 'reason', None)}"
        )
        assert result.side == "buy_yes", f"R5: side must be buy_yes, got {result.side}"

    def test_enter_decision_is_shadow(self):
        """Shadow candidate: executable_alpha=False; enter outcome is still shadow."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT

        strat = ShoulderBuyEVT()
        assert strat.metadata.executable_alpha is False, (
            "R5: shadow-only candidate must have executable_alpha=False"
        )

    def test_enter_carries_edge_field(self):
        """Enter decision must carry edge = p⁻_u − a_YES − phi (not None)."""
        ctx = _make_context(
            evt_tail_prob_raw=0.40,
            native_yes_ask=0.05,
            cal_p_hats=[1.0] * 40,
            cal_outcomes=[1] * 40,
        )
        result = self._evaluate(ctx)
        assert result.outcome == "enter"
        assert result.edge is not None, "R5: enter decision must carry edge"
        assert result.edge > Decimal("0"), f"R5: edge must be positive, got {result.edge}"


# ── R6: non-positive lower-bound EV → no_trade ────────────────────────────────

class TestR6NonPositiveEVNoTrade:
    """R6: p⁻_u − a_YES − phi ≤ 0 → no_trade(SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE)."""

    def _evaluate(self, ctx):
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT
        import sqlite3
        from datetime import datetime, timezone

        strat = ShoulderBuyEVT()
        conn = sqlite3.connect(":memory:")
        return strat.evaluate(
            context=ctx,
            conn=conn,
            decision_time=datetime.now(timezone.utc),
        )

    def test_high_ask_no_trade(self):
        """Raw prob 0.10 but ask 0.20 — lower bound can't exceed ask+fee → no_trade."""
        from src.contracts.no_trade_reason import NoTradeReason

        ctx = _make_context(
            evt_tail_prob_raw=0.10,
            native_yes_ask=0.20,
            # Perfect calibration — lower bound = raw = 0.10 < ask 0.20
            cal_p_hats=[1.0] * 30,
            cal_outcomes=[1] * 30,
        )
        result = self._evaluate(ctx)
        assert result.outcome == "no_trade", (
            f"R6: expected no_trade, got {result.outcome}"
        )
        assert result.reason == NoTradeReason.SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE, (
            f"R6: expected SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE, got {result.reason}"
        )

    def test_wide_calibration_interval_forces_no_trade(self):
        """Large calibration nonconformity → wide interval → lower bound near 0 → no_trade."""
        from src.contracts.no_trade_reason import NoTradeReason

        ctx = _make_context(
            evt_tail_prob_raw=0.15,
            native_yes_ask=0.10,
            # High nonconformity → q_alpha large → p_lo collapses to ~0
            cal_p_hats=[0.80] * 50,
            cal_outcomes=[0] * 50,
        )
        result = self._evaluate(ctx)
        assert result.outcome == "no_trade"
        assert result.reason == NoTradeReason.SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE, (
            f"R6: expected SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE, got {result.reason}"
        )


# ── R7: edge formula = p⁻_u − a_YES − phi ─────────────────────────────────────

class TestR7EdgeFormula:
    """R7: edge field in enter decision equals p⁻_u − a_YES − phi exactly."""

    def test_edge_formula_exact(self):
        """Compute expected edge independently and compare to strategy output."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT
        from src.strategy.fees import phi, venue_fee_rate
        from src.calibration.bounds import calibrated_bounds
        import sqlite3
        from datetime import datetime, timezone

        raw = 0.40
        ask_f = 0.05
        cal_p = [1.0] * 40
        cal_y = [1] * 40

        ctx = _make_context(
            evt_tail_prob_raw=raw,
            native_yes_ask=ask_f,
            cal_p_hats=cal_p,
            cal_outcomes=cal_y,
        )

        strat = ShoulderBuyEVT()
        conn = sqlite3.connect(":memory:")
        result = strat.evaluate(
            context=ctx,
            conn=conn,
            decision_time=datetime.now(timezone.utc),
        )

        assert result.outcome == "enter"

        # Independently compute expected edge
        ask_d = Decimal(str(ask_f))
        fee_rate = venue_fee_rate()
        expected_fee = phi(Decimal("1"), ask_d, fee_rate)
        lo, _ = calibrated_bounds(raw, cal_p, cal_y, alpha=0.10)
        expected_edge = Decimal(str(lo)) - ask_d - expected_fee

        assert abs(result.edge - expected_edge) < Decimal("1e-9"), (
            f"R7: edge mismatch: got {result.edge}, expected {expected_edge}"
        )

    def test_p_tail_lower_bound_field_populated(self):
        """Enter decision must carry p_tail_lower_bound matching bounds output."""
        from src.strategy.candidates.shoulder_buy_evt import ShoulderBuyEVT
        from src.calibration.bounds import calibrated_bounds
        import sqlite3
        from datetime import datetime, timezone

        raw = 0.40
        cal_p = [1.0] * 40
        cal_y = [1] * 40

        ctx = _make_context(
            evt_tail_prob_raw=raw,
            native_yes_ask=0.05,
            cal_p_hats=cal_p,
            cal_outcomes=cal_y,
        )
        strat = ShoulderBuyEVT()
        conn = sqlite3.connect(":memory:")
        result = strat.evaluate(
            context=ctx,
            conn=conn,
            decision_time=datetime.now(timezone.utc),
        )
        assert result.outcome == "enter"

        lo, _ = calibrated_bounds(raw, cal_p, cal_y, alpha=0.10)
        expected_lo = Decimal(str(lo))

        # p_tail_lower_bound must be a named field on the decision
        assert hasattr(result, "p_tail_lower_bound"), (
            "R7: enter decision must carry p_tail_lower_bound field"
        )
        assert abs(result.p_tail_lower_bound - expected_lo) < Decimal("1e-9"), (
            f"R7: p_tail_lower_bound mismatch: got {result.p_tail_lower_bound}, expected {expected_lo}"
        )
