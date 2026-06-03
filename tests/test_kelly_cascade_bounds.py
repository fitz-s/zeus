# Created: 2026-04-07
# Last reused/audited: 2026-06-02
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel)
# Wave 3 (2026-06-02): removed rolling_win_rate_20/drawdown_pct/max_drawdown (deleted from
#   dynamic_kelly_mult). Tests repointed to surviving factors (ci_width, lead_days, portfolio_heat).
"""Tests for Kelly multiplicative cascade bounds. §P9.7.

Verifies that worst-case products of ALL surviving adjustments in dynamic_kelly_mult
stay within [0.001, 1.0] — i.e., the cascade cannot kill all sizing (→0)
or produce leverage (>1.0 × base).

These tests use the REAL dynamic_kelly_mult function with extreme inputs.
"""
import pytest

from src.strategy.kelly import dynamic_kelly_mult, kelly_size

# Extreme parametrize cases: (ci_width, lead_days, portfolio_heat)
# rolling_win_rate_20/drawdown_pct/max_drawdown deleted Wave 3 (zero live callers).
EXTREME_CASES = [
    # All worst-case simultaneously
    pytest.param(0.30, 10, 0.80, id="all_worst_case"),
    # Wide CI only
    pytest.param(0.30, 0,  0.00, id="wide_ci_only"),
    # Long lead only
    pytest.param(0.00, 10, 0.00, id="long_lead_only"),
    # High heat only
    pytest.param(0.00, 0,  0.80, id="high_heat_only"),
    # Mixed severe
    pytest.param(0.20, 7,  0.60, id="mixed_severe"),
    # Minimal stress (baseline)
    pytest.param(0.05, 1,  0.10, id="mild_conditions"),
]

BASE = 0.25


class TestKellyCascadeProductBounded:
    """Worst-case product of ALL multiplicative adjustments stays in [0.001, 1.0]."""

    @pytest.mark.parametrize(
        "ci_width,lead_days,portfolio_heat",
        [case.values if hasattr(case, 'values') else case
         for case in EXTREME_CASES],
        ids=[c.id for c in EXTREME_CASES],
    )
    def test_cascade_product_lower_bound(
        self, ci_width, lead_days, portfolio_heat
    ):
        """Result / base ≥ 0.001 — cascade cannot reduce to near-zero."""
        m = dynamic_kelly_mult(
            base=BASE,
            ci_width=ci_width,
            lead_days=lead_days,
            portfolio_heat=portfolio_heat,
        )
        ratio = m / BASE if BASE > 0 else m
        assert ratio >= 0.001 or m >= 0.001, (
            f"Cascade product ratio={ratio:.6f} fell below 0.001 floor. "
            f"Inputs: ci_width={ci_width}, lead_days={lead_days}, "
            f"heat={portfolio_heat}. "
            "The cascade must not destroy all sizing."
        )

    @pytest.mark.parametrize(
        "ci_width,lead_days,portfolio_heat",
        [case.values if hasattr(case, 'values') else case
         for case in EXTREME_CASES],
        ids=[c.id for c in EXTREME_CASES],
    )
    def test_cascade_product_upper_bound(
        self, ci_width, lead_days, portfolio_heat
    ):
        """Result ≤ base — cascade cannot increase beyond base (no leverage beyond full Kelly)."""
        m = dynamic_kelly_mult(
            base=BASE,
            ci_width=ci_width,
            lead_days=lead_days,
            portfolio_heat=portfolio_heat,
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
            portfolio_heat=0.80,       # max heat reduction
        )
        assert m > 0.0, (
            f"dynamic_kelly_mult returned exactly 0.0 with extreme inputs. "
            "Zero multiplier kills all future sizing."
        )


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
                for heat in [0.0, 0.20, 0.50]:
                    m = dynamic_kelly_mult(
                        base=base, ci_width=ci, lead_days=lead,
                        portfolio_heat=heat
                    )
                    assert m <= base + 1e-9, (
                        f"Multiplier {m} exceeded base {base} for "
                        f"ci={ci}, lead={lead}, heat={heat}"
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
            portfolio_heat=0.50,
        )
        from src.contracts.execution_price import ExecutionPrice
        ep = ExecutionPrice(
            value=0.40, price_type="fee_adjusted", fee_deducted=True,
            currency="probability_units",
        )
        size = kelly_size(
            p_posterior=0.60,
            entry_price=ep,
            bankroll=bankroll,
            kelly_mult=mult,
        )
        assert size >= 0.0, "Size must be non-negative"
        assert size < bankroll, "Size must be less than bankroll"
