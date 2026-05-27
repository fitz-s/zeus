# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave6 + INV-40
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: R3 — relationship test antibody for INV-40 (spread/depth single-count)
# Reuse: Flag OFF preserves legacy double-count chain bit-identically; flag ON skips both dynamic_kelly_mult.ci_width haircut AND EffectiveKellyContext.haircut multiplication.
"""R3: spread/depth uncertainty must appear ONCE in the sizing chain.

Pre-Wave-6 the EffectiveKellyContext.haircut() spread/depth signal multiplied
the Kelly multiplier AND the same spread information also reached
dynamic_kelly_mult.ci_width as a haircut — double-count INV-40 forbids.

Wave 6 ships the collapse behind ``ZEUS_UNIFIED_UNCERTAINTY_BUDGET``:

  - flag OFF (default): legacy double-count chain preserved bit-identically
    (operator-promotion-only safety; no behavioural change for live capital
    until operator explicitly flips the flag).
  - flag ON: dynamic_kelly_mult skips ci_width haircuts; the boundary site
    ``_size_at_execution_price_boundary`` skips the EffectiveKellyContext
    multiplication. Soft-spread/depth uncertainty enters Kelly EXACTLY ONCE
    via edge_LCB (via σ_market from Wave 5 + EntryQuoteEvidence).

Antibody for INV-40 (uncertainty_single_count) — Wave 6 arm.
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


def _ci_width_legacy_haircut(ci_width: float) -> float:
    """Reproduce dynamic_kelly_mult's pre-Wave-6 ci_width contribution."""
    h = 1.0
    if ci_width > 0.10:
        h *= 0.7
    if ci_width > 0.15:
        h *= 0.5
    return h


class TestFlagOffPreservesLegacyDoubleCount:
    """Default behaviour: ci_width haircut + EKC.haircut both fire.

    This test PINS the legacy chain so an accidental Wave-6 default-on flip
    (or a partial collapse) is caught immediately.
    """

    def test_flag_off_dynamic_mult_applies_ci_width_haircut(self, monkeypatch):
        monkeypatch.delenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, raising=False)
        m_baseline = dynamic_kelly_mult(base=0.25, ci_width=0.05)
        m_wide = dynamic_kelly_mult(base=0.25, ci_width=0.20)
        # Pre-Wave-6 chain: ×0.7 (>0.10) × ×0.5 (>0.15) = ×0.35
        assert m_wide == pytest.approx(m_baseline * 0.35)


class TestFlagOnSingleCountEnforced:
    """Wave 6 ON: ci_width haircut is REMOVED from dynamic_kelly_mult, and
    EffectiveKellyContext.haircut() is NOT multiplied at the boundary."""

    def test_flag_on_dynamic_mult_skips_ci_width_haircut(self, monkeypatch):
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        assert _unified_uncertainty_budget_enabled() is True
        # K1 (PR #348 P0-1): per-edge market_uncertainty_in_lcb required.
        m_baseline = dynamic_kelly_mult(base=0.25, ci_width=0.05, market_uncertainty_in_lcb=True)
        m_wide = dynamic_kelly_mult(base=0.25, ci_width=0.20, market_uncertainty_in_lcb=True)
        assert m_wide == pytest.approx(m_baseline), (
            "Wave 6 flag ON + per-edge evidence must remove the multiplicative ci_width haircut "
            f"(legacy chain returned {m_baseline * 0.35:.6f}, Wave 6 should keep {m_baseline:.6f})"
        )

    def test_flag_on_effective_kelly_haircut_helper_unchanged(self, monkeypatch):
        """The haircut value computed by EffectiveKellyContext.haircut() itself
        is identical under both flag values — the collapse is about WHERE the
        haircut is APPLIED, not what it returns. The boundary site
        ``_size_at_execution_price_boundary`` is responsible for skipping the
        multiplication when the flag is ON (covered in test_wave6_*.py)."""
        ctx = EffectiveKellyContext(
            spread_usd=Decimal("0.10"),
            depth_at_best_ask=50,
            order_type="FOK",
        )
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        h_on = ctx.haircut()
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "0")
        h_off = ctx.haircut()
        assert h_on == h_off
        assert 0.0 < h_on <= 1.0


class TestNoLegacyDoubleCountWithFlagOn:
    """When the flag is ON and BOTH spread (via EKC.haircut) and ci_width
    appear simultaneously, the multiplicative chain must reflect only ONE of
    them. Today (flag OFF) the double-count is the load-bearing legacy chain;
    flag ON removes the ci_width side, leaving EKC.haircut to do its job at
    the boundary (which is now skipped too — so the duplicate is dropped on
    BOTH sides, and the compensating widening lives in edge_LCB / σ_market).
    """

    def test_flag_on_ci_width_and_ekc_haircut_are_both_inactive(self, monkeypatch):
        monkeypatch.setenv("ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED", "1")
        monkeypatch.setenv(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "1")
        # K1 (PR #348 P0-1): per-edge evidence required for collapse.
        # ci_width haircut removed → multiplier unchanged regardless of ci_width
        m_no_ci = dynamic_kelly_mult(base=0.25, ci_width=0.0, market_uncertainty_in_lcb=True)
        m_with_ci = dynamic_kelly_mult(base=0.25, ci_width=0.20, market_uncertainty_in_lcb=True)
        assert m_no_ci == pytest.approx(m_with_ci)

        # EKC.haircut() helper itself still returns its bucket value (it's
        # informational; the boundary site no longer multiplies it).
        ctx = EffectiveKellyContext(
            spread_usd=Decimal("0.10"),
            depth_at_best_ask=50,
            order_type="FOK",
        )
        assert ctx.haircut() < 1.0  # informational only under flag ON
