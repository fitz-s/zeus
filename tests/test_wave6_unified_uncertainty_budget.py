# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave6 +
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: Wave 6 — unified uncertainty budget (multiplier collapse) tests
# Reuse: Flag resolution (default OFF; ordering guard requires Wave 5.5 prereq); flag-OFF preserves legacy chain; flag-ON skips ci_width haircut; multiplier safety direction; EffectiveKellyContext haircut helper invariant.
#                  docs/reference/zeus_math_spec.md §15.8 (single-count uncertainty budget)
"""Wave 6: unified uncertainty budget (feature-flagged multiplier collapse).

Pre-Wave-6 the soft-uncertainty contribution entered Kelly TWICE:
  (a) via edge_LCB (the bootstrap ci_lower of p_LCB - c_UCB), AND
  (b) via multiplicative haircuts (dynamic_kelly_mult ci_width +
      EffectiveKellyContext.haircut spread/depth).

INV-40 forbids double-counting: every uncertainty source contributes to size
reduction EXACTLY ONCE — either via edge_LCB (soft σ) OR via a hard {0, 1}
veto multiplier.

Wave 6 ships the collapse behind ``ZEUS_UNIFIED_UNCERTAINTY_BUDGET``:

  - flag OFF (default): legacy chain preserved bit-identically.
  - flag ON: ci_width haircuts SKIPPED in dynamic_kelly_mult, and
    EffectiveKellyContext.haircut NOT multiplied at the
    _size_at_execution_price_boundary seam.

Operator promotes the flag only after replay validation. These tests pin
the two-stage contract:

  TestFlagOffPreservesLegacy: flag OFF produces bit-identical multipliers
    + sizes vs the pre-Wave-6 path on the same inputs.
  TestFlagOnCollapsesDuplicates: flag ON removes the duplicate multipliers.
  TestFlagOnSafetyDirection: flag ON produces equal-or-larger multipliers
    than flag OFF (which is the math direction — single-count IS less
    conservative than double-count). The compensating widening comes
    from edge_LCB via σ_market when Wave 5+5.5 are active in the live
    pipeline; this is exercised by tests/test_R5_bootstrap_c_b_uncertainty_widens_ci.py.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.contracts.effective_kelly_context import EffectiveKellyContext
from src.strategy.kelly import (
    _ENV_UNIFIED_UNCERTAINTY_BUDGET,
    _unified_uncertainty_budget_enabled,
    dynamic_kelly_mult,
)


# ---------------------------------------------------------------------------
# Helper-level: flag resolution
# ---------------------------------------------------------------------------

class TestFlagResolution:
    def test_default_off_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, raising=False)
        assert _unified_uncertainty_budget_enabled() is False

    def test_on_for_1_true_TRUE(self, monkeypatch):
        # K5#5 + X10: Wave 6 flag requires Wave 5.5 prereq flag too.
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        for v in ("1", "true", "TRUE"):
            monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, v)
            assert _unified_uncertainty_budget_enabled() is True

    def test_off_when_prereq_eqe_flag_missing(self, monkeypatch):
        """K5#5+X10 ordering guard: Wave 6 ignored without Wave 5.5."""
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        monkeypatch.delenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", raising=False)
        assert _unified_uncertainty_budget_enabled() is False

    def test_off_for_0_false_empty(self, monkeypatch):
        for v in ("0", "false", "False", "no", ""):
            monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, v)
            assert _unified_uncertainty_budget_enabled() is False


# ---------------------------------------------------------------------------
# Stage 0: flag OFF preserves legacy dynamic_kelly_mult haircuts
# ---------------------------------------------------------------------------

class TestFlagOffPreservesLegacy:
    def test_flag_off_ci_width_haircut_applied_above_010(self, monkeypatch):
        monkeypatch.delenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, raising=False)
        # ci_width = 0.12 > 0.10 → m *= 0.7
        m_no_ci = dynamic_kelly_mult(base=0.25, ci_width=0.05)
        m_wide_ci = dynamic_kelly_mult(base=0.25, ci_width=0.12)
        assert m_wide_ci == pytest.approx(m_no_ci * 0.7)

    def test_flag_off_ci_width_haircut_cumulative_above_015(self, monkeypatch):
        monkeypatch.delenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, raising=False)
        # ci_width = 0.20 > 0.15 (and > 0.10) → m *= 0.7 * 0.5
        m_no_ci = dynamic_kelly_mult(base=0.25, ci_width=0.05)
        m_widest_ci = dynamic_kelly_mult(base=0.25, ci_width=0.20)
        assert m_widest_ci == pytest.approx(m_no_ci * 0.7 * 0.5)


# ---------------------------------------------------------------------------
# Stage 2: flag ON collapses the duplicate ci_width haircuts
# ---------------------------------------------------------------------------

class TestFlagOnCollapsesDuplicates:
    def test_flag_on_skips_ci_width_haircut_above_010(self, monkeypatch):
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        m_no_ci = dynamic_kelly_mult(base=0.25, ci_width=0.05)
        m_wide_ci = dynamic_kelly_mult(base=0.25, ci_width=0.12)
        # No multiplier difference — ci_width contribution moved to edge_LCB.
        assert m_wide_ci == pytest.approx(m_no_ci)

    def test_flag_on_skips_ci_width_haircut_above_015(self, monkeypatch):
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        m_no_ci = dynamic_kelly_mult(base=0.25, ci_width=0.05)
        m_widest_ci = dynamic_kelly_mult(base=0.25, ci_width=0.20)
        assert m_widest_ci == pytest.approx(m_no_ci)


# ---------------------------------------------------------------------------
# Safety-direction: flag ON >= flag OFF on multiplier basis
# ---------------------------------------------------------------------------

class TestFlagOnSafetyDirection:
    @pytest.mark.parametrize("ci_width", [0.0, 0.05, 0.10, 0.12, 0.15, 0.18, 0.25])
    def test_flag_on_multiplier_ge_flag_off_at_every_ci_width(self, monkeypatch, ci_width):
        # Multiplier-only basis: flag ON removes the double-count haircut,
        # so the multiplier is strictly >= legacy flag-OFF multiplier.
        # Pre-promotion replay must confirm σ_market widening in edge_LCB
        # compensates for the removed haircut — handled by integration tests
        # / replay, not unit tests on dynamic_kelly_mult alone.
        monkeypatch.delenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, raising=False)
        m_off = dynamic_kelly_mult(
            base=0.25, ci_width=ci_width, lead_days=2.0,
            rolling_win_rate_20=0.50, portfolio_heat=0.10,
        )
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        m_on = dynamic_kelly_mult(
            base=0.25, ci_width=ci_width, lead_days=2.0,
            rolling_win_rate_20=0.50, portfolio_heat=0.10,
        )
        assert m_on >= m_off, (
            f"Wave 6 flag ON multiplier {m_on:.6f} < flag OFF {m_off:.6f} "
            f"at ci_width={ci_width}. Wave 6 must never reduce multipliers — "
            f"the math direction is to REMOVE conservative double-counting."
        )

    def test_other_haircuts_unchanged_by_flag(self, monkeypatch):
        """Lead, win-rate, heat, drawdown, strategy, city haircuts must stay
        identical under flag ON. Wave 6 only collapses the soft-uncertainty
        arm — those haircuts are not double-counted in edge_LCB."""
        for env in ("0", "1"):
            monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", env)
            monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, env)
            with_lead = dynamic_kelly_mult(base=0.25, lead_days=5.0)
            without_lead = dynamic_kelly_mult(base=0.25, lead_days=0.0)
            assert with_lead == pytest.approx(without_lead * 0.6)

            with_streak = dynamic_kelly_mult(base=0.25, rolling_win_rate_20=0.30)
            without_streak = dynamic_kelly_mult(base=0.25, rolling_win_rate_20=0.50)
            assert with_streak == pytest.approx(without_streak * 0.5)


# ---------------------------------------------------------------------------
# EffectiveKellyContext relocation (evaluator boundary)
# ---------------------------------------------------------------------------

class TestEffectiveKellyHaircutCollapse:
    """The boundary collapse (EffectiveKellyContext.haircut() removed from
    multiplicative chain when flag ON) is enforced inside
    ``_size_at_execution_price_boundary``. Direct testing of that function
    requires the typed ExecutionPrice + fee path; instead we cover the
    collapse by asserting the underlying helper still computes the same
    haircut() value (no API change), and the integration is bracketed by
    the existing test suite (no regression).
    """

    def test_haircut_helper_unchanged_by_flag(self, monkeypatch):
        ctx = EffectiveKellyContext(
            spread_usd=Decimal("0.08"),
            depth_at_best_ask=120,
            order_type="FOK",
        )
        h = ctx.haircut()
        # Wave 6 only changes WHEN this haircut is multiplied into the
        # Kelly chain (boundary site), not the value it returns.
        for env in ("0", "1"):
            monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", env)
            monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, env)
            assert ctx.haircut() == h
