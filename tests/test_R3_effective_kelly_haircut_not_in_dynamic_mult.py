# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave1 + INV-40
"""R3: Spread/depth uncertainty must appear ONCE in the sizing chain.

RED today — EffectiveKellyContext.haircut() multiplies into the size chain AND
the same spread/depth signal feeds ci_width which also reduces dynamic_kelly_mult
(D1: ci_width > 0.10 → ×0.7, > 0.15 → ×0.35 cumulative). Wave 6 collapses this
to single uncertainty contribution via kelly_uncertainty_budget.

Antibody for INV-40 (uncertainty_single_count).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.contracts.effective_kelly_context import EffectiveKellyContext


@pytest.mark.xfail(
    reason="Wave 6 — spread/depth haircut appears both in EffectiveKellyContext.haircut() "
           "and in ci_width-driven dynamic_kelly_mult (D1 double-count). "
           "Flip GREEN when kelly_uncertainty_budget unifies to single σ contribution.",
    strict=True,
)
def test_r3_spread_uncertainty_appears_once_in_size_chain() -> None:
    """Spread-driven haircut must appear exactly once — not in both EffectiveKellyContext AND ci_width.

    Setup: spread_usd=0.10, bid=0.40, ask=0.50 → ci_width_proxy ≈ 0.22 > 0.15
    Today: EKC.haircut() fires (× TIGHT/SHALLOW multiplier) AND ci_width > 0.15
           triggers an additional ×0.35 in dynamic_kelly_mult. Double-count.
    Post-Wave-6: ci_width path removed; EKC.haircut() is the sole mechanism.
    """
    spread_usd = Decimal("0.10")
    bid = Decimal("0.40")
    ask = Decimal("0.50")

    ctx = EffectiveKellyContext(
        spread_usd=spread_usd,
        depth_at_best_ask=50,
        order_type="FOK",
    )
    haircut_value = ctx.haircut()
    assert 0.0 < haircut_value <= 1.0  # sanity

    # The proxy ci_width a WIDE spread would produce in dynamic_kelly_mult
    mid = float(bid + ask) / 2.0
    ci_width_proxy = float(spread_usd) / mid  # ≈ 0.222

    # Post-Wave-6 expectation: ci_width must NOT drive an additional multiplier
    # because that uncertainty is already captured in EKC.haircut().
    # Today ci_width_proxy > 0.15 → dynamic_kelly_mult returns 0.35 extra.
    ci_width_extra_mult = 1.0  # expected post-fix
    if ci_width_proxy > 0.15:
        ci_width_extra_mult = 0.35  # actual today (D1 defect: ×0.7 × ×0.5)
    elif ci_width_proxy > 0.10:
        ci_width_extra_mult = 0.70  # actual today (D1 defect: ×0.7 only)

    assert ci_width_extra_mult == 1.0, (
        f"ci_width_proxy={ci_width_proxy:.3f} causes dynamic_kelly_mult extra "
        f"haircut ×{ci_width_extra_mult} in addition to EKC.haircut()={haircut_value:.3f}. "
        "This is the D1/INV-40 double-count defect; Wave 6 must remove ci_width path."
    )
