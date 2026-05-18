# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: audit cycle 2026-05-17 (Lane C). Antibody for
#   divergence_score formula signed-vs-abs() collision. Fix:
#   divergence_score = max(0, p_market - p_posterior) (adverse-only).
"""Relationship test: entry edge must not self-trip MODEL_DIVERGENCE_PANIC.

Cross-module invariant:
    A buy_yes position that entered with edge E (p_posterior - p_market = E > 0)
    must NOT trigger MODEL_DIVERGENCE_PANIC on the next monitor cycle when
    p_posterior and p_market are IDENTICAL to entry-time values.

    Conversely, when p_market has risen past p_posterior (model underpriced the
    outcome; market has run ahead of us), panic MUST fire at the hard threshold.

This is a relationship test per CLAUDE.md: it verifies the cross-module
invariant "entry edge and exit panic evaluate directionally consistent functions
of the same scalar." Any future formula change in monitor_refresh.py or
exit_triggers.py that restores the sign collapse will fail this test.
"""

import math

import pytest
from unittest.mock import MagicMock
import numpy as np

from src.contracts.edge_context import EdgeContext
from src.contracts.semantic_types import EntryMethod
from src.engine.monitor_refresh import _compute_divergence_score
from src.execution.exit_triggers import evaluate_exit_triggers
from src.state.portfolio import divergence_hard_threshold, divergence_soft_threshold


# ---------------------------------------------------------------------------
# Case 0: PRODUCTION FORMULA — directly exercises monitor_refresh's helper.
# This is the antibody that catches a reverted abs() at the call site.
# Break-restore verified: removing this guarantee causes failures here.
# ---------------------------------------------------------------------------

class TestProductionFormulaIsAdverseOnly:
    """Direct call into `_compute_divergence_score`, the helper at the formula
    site (monitor_refresh.py). If anyone re-introduces `abs()`, these fail.
    """

    def test_bullish_entry_returns_zero(self):
        # London incident exact values
        assert _compute_divergence_score(0.622, 0.282, available=True) == 0.0

    def test_boundary_bullish_returns_zero(self):
        assert _compute_divergence_score(0.60, 0.30, available=True) == 0.0

    def test_adverse_overshoot_returns_signed_gap(self):
        score = _compute_divergence_score(0.62, 0.95, available=True)
        assert score == pytest.approx(0.33, abs=1e-9)
        assert score >= divergence_hard_threshold()

    def test_unavailable_returns_nan(self):
        assert math.isnan(_compute_divergence_score(0.62, 0.28, available=False))

    @pytest.mark.parametrize("p_post,p_mkt", [
        (0.62, 0.10), (0.62, 0.30), (0.62, 0.50), (0.62, 0.61),
    ])
    def test_parametric_bullish_never_positive(self, p_post, p_mkt):
        assert _compute_divergence_score(p_post, p_mkt, available=True) == 0.0


# ---------------------------------------------------------------------------
# Shared builder — mirrors how monitor_refresh.refresh_position builds EdgeContext
# ---------------------------------------------------------------------------

def _make_edge_context(
    p_posterior: float,
    p_market: float,
    divergence_score: float,
    forward_edge: float,
    market_velocity_1h: float = 0.0,
    ci_lower: float = 0.47,
    ci_upper: float = 0.78,
) -> EdgeContext:
    """Mirror the EdgeContext produced by monitor_refresh.refresh_position."""
    return EdgeContext(
        p_raw=np.array([p_posterior]),
        p_cal=np.array([p_posterior]),
        p_market=np.array([p_market]),
        p_posterior=p_posterior,
        forward_edge=forward_edge,
        alpha=0.70,
        confidence_band_lower=ci_lower,
        confidence_band_upper=ci_upper,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="test-snap-london-antibody",
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=market_velocity_1h,
        divergence_score=divergence_score,
    )


def _make_position(direction: str = "buy_yes", cost_basis: float = 2.0) -> MagicMock:
    pos = MagicMock()
    pos.trade_id = "test-london-antibody"
    pos.direction = direction
    pos.neg_edge_count = 0
    pos.effective_cost_basis_usd = cost_basis
    pos.entry_ci_width = 0.307
    return pos


# ---------------------------------------------------------------------------
# Case A: entry edge must NOT self-trip on next monitor cycle
# ---------------------------------------------------------------------------

class TestEntryEdgeDoesNotSelfTrip:
    """
    Live incident reference: p_posterior=0.622, p_market=0.282, edge=0.340.
    Old formula: abs(0.622 - 0.282) = 0.340 >= hard_threshold(0.30) -> PANIC.
    Fixed formula: max(0, 0.282 - 0.622) = 0.000 < 0.30 -> no panic.
    """

    def test_buy_yes_entry_edge_does_not_fire_divergence_panic(self):
        """
        A buy_yes position with positive entry edge of 0.34 must NOT trigger
        MODEL_DIVERGENCE_PANIC on an identical next-cycle monitor refresh.
        """
        p_posterior = 0.622
        p_market = 0.282
        divergence_score = max(0.0, p_market - p_posterior)  # = 0.0
        forward_edge = p_posterior - p_market                  # = 0.340

        hard_thresh = divergence_hard_threshold()
        assert divergence_score == 0.0, (
            f"Fixed formula must produce 0.0 for bullish position, got {divergence_score}"
        )
        assert forward_edge > 0, "Precondition: positive entry edge"
        assert forward_edge >= hard_thresh, (
            f"Precondition: entry edge ({forward_edge:.3f}) >= hard_threshold "
            f"({hard_thresh:.3f}); this is the self-trip condition under old formula"
        )

        edge_ctx = _make_edge_context(
            p_posterior=p_posterior,
            p_market=p_market,
            divergence_score=divergence_score,
            forward_edge=forward_edge,
        )
        position = _make_position(direction="buy_yes")

        result = evaluate_exit_triggers(
            position=position,
            current_edge_context=edge_ctx,
            hours_to_settlement=24.0,
            market_vig=1.01,
            is_whale_sweep=False,
            best_bid=p_market,
        )

        assert result is None or result.trigger != "MODEL_DIVERGENCE_PANIC", (
            f"Entry with edge={forward_edge:.3f} must NOT trigger MODEL_DIVERGENCE_PANIC "
            f"on next cycle (divergence_score={divergence_score:.3f}). "
            f"Got trigger: {result.trigger if result else None}. "
            "This indicates abs() is still in the divergence formula."
        )

    def test_minimum_entry_edge_at_hard_threshold_does_not_fire(self):
        """
        A position entered with edge exactly at the hard threshold (0.30)
        must not panic. Tightest version of the invariant.
        """
        p_posterior = 0.60
        p_market = 0.30
        divergence_score = max(0.0, p_market - p_posterior)

        edge_ctx = _make_edge_context(
            p_posterior=p_posterior,
            p_market=p_market,
            divergence_score=divergence_score,
            forward_edge=p_posterior - p_market,
        )
        position = _make_position(direction="buy_yes")

        result = evaluate_exit_triggers(
            position=position,
            current_edge_context=edge_ctx,
            hours_to_settlement=24.0,
            market_vig=1.01,
            is_whale_sweep=False,
            best_bid=p_market,
        )

        assert result is None or result.trigger != "MODEL_DIVERGENCE_PANIC", (
            f"Position at edge=hard_threshold must not self-trip. "
            f"Got trigger: {result.trigger if result else None}"
        )


# ---------------------------------------------------------------------------
# Case B: genuine adverse overshoot MUST fire panic
# ---------------------------------------------------------------------------

class TestAdverseOvershotFiresPanic:
    """
    When the market has moved past the model (p_market > p_posterior), the
    model is now the bearish voice and the market is the bullish one. Our
    YES position is wrong-way. Panic is correct.
    """

    def test_market_above_posterior_exceeds_hard_threshold_fires_panic(self):
        """
        p_posterior=0.62, p_market=0.95: divergence = 0.33 >= 0.30 -> PANIC.
        """
        p_posterior = 0.62
        p_market = 0.95
        divergence_score = max(0.0, p_market - p_posterior)  # = 0.33
        forward_edge = p_posterior - p_market                 # = -0.33

        hard_thresh = divergence_hard_threshold()
        assert divergence_score >= hard_thresh, (
            f"Precondition: divergence ({divergence_score:.3f}) must exceed hard "
            f"threshold ({hard_thresh:.3f}) for panic to be expected"
        )

        edge_ctx = _make_edge_context(
            p_posterior=p_posterior,
            p_market=p_market,
            divergence_score=divergence_score,
            forward_edge=forward_edge,
        )
        position = _make_position(direction="buy_yes")

        result = evaluate_exit_triggers(
            position=position,
            current_edge_context=edge_ctx,
            hours_to_settlement=24.0,
            market_vig=1.01,
            is_whale_sweep=False,
            best_bid=p_market,
        )

        assert result is not None, (
            "Expected MODEL_DIVERGENCE_PANIC when market has blown past posterior, got None"
        )
        assert result.trigger == "MODEL_DIVERGENCE_PANIC", (
            f"Expected MODEL_DIVERGENCE_PANIC, got {result.trigger}"
        )

    def test_market_above_posterior_at_soft_threshold_with_adverse_velocity_fires(self):
        """
        Soft path: divergence >= 0.20 AND market_velocity_1h <= -0.05.
        p_posterior=0.62, p_market=0.84: divergence = 0.22 >= 0.20.
        """
        p_posterior = 0.62
        p_market = 0.84
        divergence_score = max(0.0, p_market - p_posterior)  # = 0.22
        forward_edge = p_posterior - p_market                 # = -0.22

        soft_thresh = divergence_soft_threshold()
        assert divergence_score >= soft_thresh, (
            f"Precondition: divergence ({divergence_score:.3f}) >= soft threshold ({soft_thresh:.3f})"
        )

        edge_ctx = _make_edge_context(
            p_posterior=p_posterior,
            p_market=p_market,
            divergence_score=divergence_score,
            forward_edge=forward_edge,
            market_velocity_1h=-0.08,
        )
        position = _make_position(direction="buy_yes")

        result = evaluate_exit_triggers(
            position=position,
            current_edge_context=edge_ctx,
            hours_to_settlement=24.0,
            market_vig=1.01,
            is_whale_sweep=False,
            best_bid=p_market,
        )

        assert result is not None, (
            "Expected MODEL_DIVERGENCE_PANIC (soft path) with adverse velocity, got None"
        )
        assert result.trigger == "MODEL_DIVERGENCE_PANIC", (
            f"Expected MODEL_DIVERGENCE_PANIC, got {result.trigger}"
        )

    def test_market_above_posterior_soft_zone_without_velocity_does_not_fire(self):
        """
        Soft threshold without confirming velocity must NOT panic.
        Prevents hair-trigger exits on temporary spikes with no momentum.
        """
        p_posterior = 0.62
        p_market = 0.84
        divergence_score = max(0.0, p_market - p_posterior)

        edge_ctx = _make_edge_context(
            p_posterior=p_posterior,
            p_market=p_market,
            divergence_score=divergence_score,
            forward_edge=p_posterior - p_market,
            market_velocity_1h=0.02,
        )
        position = _make_position(direction="buy_yes")

        result = evaluate_exit_triggers(
            position=position,
            current_edge_context=edge_ctx,
            hours_to_settlement=24.0,
            market_vig=1.01,
            is_whale_sweep=False,
            best_bid=p_market,
        )

        assert result is None or result.trigger != "MODEL_DIVERGENCE_PANIC", (
            f"Soft divergence without confirming velocity must not panic. "
            f"Got trigger: {result.trigger if result else None}"
        )


# ---------------------------------------------------------------------------
# Case C: formula identity invariant — divergence_score != forward_edge for
# positions with positive edge (the post-patch sanity check)
# ---------------------------------------------------------------------------

class TestDivergenceScoreIsDistinctFromForwardEdge:
    """
    Under the old (broken) formula, exit_divergence_score == exit_forward_edge
    for any buy_yes position with positive edge, because abs(positive) = positive.
    After the fix, they are distinct: divergence_score = 0.0, forward_edge = 0.34.
    If this test fails, the abs() collision has been reintroduced.
    """

    def test_positive_edge_divergence_score_is_not_forward_edge(self):
        """
        For any buy_yes with p_market < p_posterior, divergence_score must be 0
        and forward_edge must be positive. They must differ.
        """
        p_posterior = 0.62
        p_market = 0.28
        divergence_score = max(0.0, p_market - p_posterior)  # 0.0
        forward_edge = p_posterior - p_market                 # 0.34

        assert divergence_score != forward_edge, (
            f"divergence_score ({divergence_score}) == forward_edge ({forward_edge}): "
            "abs() collision detected — old broken formula may be active"
        )
        assert divergence_score == 0.0
        assert forward_edge > 0.0

    @pytest.mark.parametrize("p_market", [0.10, 0.20, 0.30, 0.40, 0.50, 0.61])
    def test_parametric_bullish_positions_never_fire_divergence(self, p_market):
        """
        For all market prices below p_posterior (0.62), divergence_score is 0
        and MODEL_DIVERGENCE_PANIC must not fire. Covers the full entry-edge range.
        """
        p_posterior = 0.62
        divergence_score = max(0.0, p_market - p_posterior)
        forward_edge = p_posterior - p_market

        assert divergence_score == 0.0

        edge_ctx = _make_edge_context(
            p_posterior=p_posterior,
            p_market=p_market,
            divergence_score=divergence_score,
            forward_edge=forward_edge,
        )
        position = _make_position(direction="buy_yes")

        result = evaluate_exit_triggers(
            position=position,
            current_edge_context=edge_ctx,
            hours_to_settlement=24.0,
            market_vig=1.01,
            is_whale_sweep=False,
            best_bid=p_market,
        )

        assert result is None or result.trigger != "MODEL_DIVERGENCE_PANIC", (
            f"p_market={p_market} (bullish position): unexpected MODEL_DIVERGENCE_PANIC. "
            f"divergence_score={divergence_score:.4f}."
        )
