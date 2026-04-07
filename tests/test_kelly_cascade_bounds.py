"""Tests for Kelly multiplicative cascade bounds. §P9.7.

Verifies that worst-case products of ALL adjustments in dynamic_kelly_mult
stay within [0.001, 1.0] — i.e., the cascade cannot kill all sizing (→0)
or produce leverage (>1.0 × base).

These tests use the REAL dynamic_kelly_mult function with extreme inputs.
"""
import pytest

from src.strategy.kelly import dynamic_kelly_mult, kelly_size

# Extreme parametrize cases: (ci_width, lead_days, win_rate, portfolio_heat, drawdown_pct, max_drawdown)
EXTREME_CASES = [
    # All worst-case simultaneously
    pytest.param(0.30, 10, 0.20, 0.80, 0.19, 0.20, id="all_worst_case"),
    # Wide CI only
    pytest.param(0.30, 0,  0.50, 0.00, 0.00, 0.20, id="wide_ci_only"),
    # Long lead only
    pytest.param(0.00, 10, 0.50, 0.00, 0.00, 0.20, id="long_lead_only"),
    # Losing streak only
    pytest.param(0.00, 0,  0.20, 0.00, 0.00, 0.20, id="losing_streak_only"),
    # High heat only
    pytest.param(0.00, 0,  0.50, 0.80, 0.00, 0.20, id="high_heat_only"),
    # Near full drawdown
    pytest.param(0.00, 0,  0.50, 0.00, 0.18, 0.20, id="near_full_drawdown"),
    # Mixed severe
    pytest.param(0.20, 7,  0.35, 0.60, 0.10, 0.20, id="mixed_severe"),
    # Minimal stress (baseline)
    pytest.param(0.05, 1,  0.55, 0.10, 0.00, 0.20, id="mild_conditions"),
]

BASE = 0.25


class TestKellyCascadeProductBounded:
    """Worst-case product of ALL multiplicative adjustments stays in [0.001, 1.0]."""

    @pytest.mark.parametrize(
        "ci_width,lead_days,win_rate,portfolio_heat,drawdown_pct,max_drawdown",
        [case.values if hasattr(case, 'values') else case
         for case in EXTREME_CASES],
        ids=[c.id for c in EXTREME_CASES],
    )
    def test_cascade_product_lower_bound(
        self, ci_width, lead_days, win_rate, portfolio_heat, drawdown_pct, max_drawdown
    ):
        """Result / base ≥ 0.001 — cascade cannot reduce to near-zero."""
        m = dynamic_kelly_mult(
            base=BASE,
            ci_width=ci_width,
            lead_days=lead_days,
            rolling_win_rate_20=win_rate,
            portfolio_heat=portfolio_heat,
            drawdown_pct=drawdown_pct,
            max_drawdown=max_drawdown,
        )
        ratio = m / BASE if BASE > 0 else m
        assert ratio >= 0.001 or m >= 0.001, (
            f"Cascade product ratio={ratio:.6f} fell below 0.001 floor. "
            f"Inputs: ci_width={ci_width}, lead_days={lead_days}, "
            f"win_rate={win_rate}, heat={portfolio_heat}, "
            f"drawdown={drawdown_pct}/{max_drawdown}. "
            "The cascade must not destroy all sizing."
        )

    @pytest.mark.parametrize(
        "ci_width,lead_days,win_rate,portfolio_heat,drawdown_pct,max_drawdown",
        [case.values if hasattr(case, 'values') else case
         for case in EXTREME_CASES],
        ids=[c.id for c in EXTREME_CASES],
    )
    def test_cascade_product_upper_bound(
        self, ci_width, lead_days, win_rate, portfolio_heat, drawdown_pct, max_drawdown
    ):
        """Result ≤ base — cascade cannot increase beyond base (no leverage beyond full Kelly)."""
        m = dynamic_kelly_mult(
            base=BASE,
            ci_width=ci_width,
            lead_days=lead_days,
            rolling_win_rate_20=win_rate,
            portfolio_heat=portfolio_heat,
            drawdown_pct=drawdown_pct,
            max_drawdown=max_drawdown,
        )
        assert m <= BASE + 1e-9, (
            f"Cascade result={m:.6f} exceeded base={BASE}. "
            "No adjustment should increase sizing above base Kelly."
        )


class TestKellyCascadeMinimumNotZero:
    """Cascade cannot produce exactly 0 — that would kill all sizing permanently."""

    def test_all_adjustments_extreme_nonzero(self):
        """Even worst-case inputs cannot collapse the multiplier to exactly 0."""
        m = dynamic_kelly_mult(
            base=BASE,
            ci_width=0.30,      # triggers both CI reductions
            lead_days=10,        # triggers lead reduction
            rolling_win_rate_20=0.20,  # triggers win-rate reduction
            portfolio_heat=0.80,       # max heat reduction
            drawdown_pct=0.19,         # near-full drawdown (not at 1.0)
            max_drawdown=0.20,
        )
        assert m > 0.0, (
            f"dynamic_kelly_mult returned exactly 0.0 with extreme inputs. "
            "Zero multiplier kills all future sizing."
        )

    def test_zero_drawdown_ratio_has_floor(self):
        """drawdown_pct == max_drawdown → spec §P9.7 floor of 0.001 applied."""
        m = dynamic_kelly_mult(
            base=BASE,
            drawdown_pct=0.20,
            max_drawdown=0.20,
        )
        # Spec §P9.7: cascade product bounded in [0.001, 1.0] — floor prevents zero
        assert m == pytest.approx(0.001), (
            "Full drawdown must return floor 0.001 per spec §P9.7, not 0.0."
        )

    def test_near_full_drawdown_has_floor(self):
        """98% drawdown retains nonzero multiplier (cascade floor)."""
        m = dynamic_kelly_mult(
            base=BASE,
            drawdown_pct=0.196,   # 98% of max
            max_drawdown=0.20,
        )
        assert m > 0.0, "98% drawdown should still produce nonzero multiplier"


class TestKellyCascadeMaximumBounded:
    """Cascade cannot exceed 1.0 regardless of base."""

    @pytest.mark.parametrize("base", [0.10, 0.25, 0.50, 0.75, 1.0])
    def test_default_params_does_not_exceed_base(self, base):
        """With default (benign) inputs, multiplier == base (no upward drift)."""
        m = dynamic_kelly_mult(base=base)
        assert m == pytest.approx(base), (
            f"With default inputs, dynamic_kelly_mult should return base unchanged. "
            f"Got {m} for base={base}"
        )

    @pytest.mark.parametrize("base", [0.10, 0.25, 0.50, 1.0])
    def test_no_inputs_exceed_base(self, base):
        """All valid input combinations keep multiplier ≤ base."""
        for ci in [0.0, 0.12, 0.25]:
            for lead in [0, 3, 7]:
                for wr in [0.30, 0.50, 0.70]:
                    m = dynamic_kelly_mult(
                        base=base, ci_width=ci, lead_days=lead,
                        rolling_win_rate_20=wr
                    )
                    assert m <= base + 1e-9, (
                        f"Multiplier {m} exceeded base {base} for "
                        f"ci={ci}, lead={lead}, wr={wr}"
                    )


class TestKellyFullCascadeWithSize:
    """Integration: size from kelly_size × dynamic_kelly_mult stays sensible."""

    def test_worst_case_size_is_bounded(self):
        """Worst-case inputs still produce a computable (nonzero, bounded) size."""
        bankroll = 1000.0
        mult = dynamic_kelly_mult(
            base=BASE,
            ci_width=0.25,
            lead_days=8,
            rolling_win_rate_20=0.30,
            portfolio_heat=0.50,
            drawdown_pct=0.15,
            max_drawdown=0.20,
        )
        size = kelly_size(
            p_posterior=0.60,
            entry_price=0.40,
            bankroll=bankroll,
            kelly_mult=mult,
        )
        assert size >= 0.0, "Size must be non-negative"
        assert size < bankroll, "Size must be less than bankroll"
