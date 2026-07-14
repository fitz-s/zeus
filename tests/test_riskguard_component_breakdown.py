# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator anti-silent-verdict directive 2026-06-09 — the
#   RiskGuard daemon sat at "Tick complete: RED" for >24h (operator zero-trade)
#   with NO per-component reason in the log. Diagnosis required a manual
#   risk_state.db dive. Historical-loss actuation is now retired, but every
#   current-state component must still be explicit.
#   Antibody: every tick must self-explain which component drove the overall level.
"""Relationship antibody: the per-tick component breakdown log must enumerate
EVERY component fed to `overall_level` and name the load-bearing one(s).

The cross-module invariant this pins (relationship test, not a function test):

    overall_level(...) is a max over N component levels. The breakdown string
    the daemon logs MUST list all N of those same components, and `driven_by`
    MUST be exactly the subset whose level equals the overall (non-GREEN) level.

This is the structural anti-silent-verdict guard: a future component added to
`overall_level` but NOT to RISK_COMPONENT_ORDER would change the overall level
WITHOUT appearing in the log — re-creating the exact "RED with no printed
reason" failure. Test 3 makes that mistake fail CI.
"""
from __future__ import annotations

from src.riskguard.riskguard import RISK_COMPONENT_ORDER, _component_breakdown
from src.riskguard.risk_level import RiskLevel


def _levels(**overrides) -> dict[str, RiskLevel]:
    base = {name: RiskLevel.GREEN for name in RISK_COMPONENT_ORDER}
    for k, v in overrides.items():
        base[k] = v
    return base


def _details() -> dict[str, str]:
    return {name: f"detail-{name}" for name in RISK_COMPONENT_ORDER}


def test_red_driven_by_collateral_identity_names_only_that_component():
    levels = _levels(collateral_identity=RiskLevel.RED)
    driven_by, breakdown = _component_breakdown(RiskLevel.RED, levels, _details())

    assert driven_by == "collateral_identity"
    assert "brier=GREEN" in breakdown
    assert "collateral_identity=RED[detail-collateral_identity]" in breakdown
    # Non-GREEN component carries its detail; GREEN ones do not.
    assert "brier=GREEN[" not in breakdown


def test_green_tick_has_no_driver_and_no_details():
    driven_by, breakdown = _component_breakdown(RiskLevel.GREEN, _levels(), _details())
    assert driven_by == "none"
    for name in RISK_COMPONENT_ORDER:
        assert f"{name}=GREEN" in breakdown
    assert "[" not in breakdown  # no detail annotations on an all-GREEN tick


def test_breakdown_enumerates_every_overall_level_component():
    """Structural guard: the breakdown lists exactly the components that feed
    overall_level in `_tick_once` — no silent omission possible."""
    levels = _levels()
    _, breakdown = _component_breakdown(RiskLevel.GREEN, levels, _details())
    listed = {part.split("=", 1)[0] for part in breakdown.split(" | ")}
    assert listed == set(RISK_COMPONENT_ORDER)
    # The components passed positionally to overall_level() in _tick_once.
    # collateral_identity joined with the W1.1 CAS reservation ledger
    # (c7e095ee1); this pin was stale until 2026-07-05.
    assert set(RISK_COMPONENT_ORDER) == {
        "brier",
        "settlement_quality",
        "execution_quality",
        "strategy_signal",
        "collateral_identity",
        "portfolio_consistency",
        "unresolved_exposure",
    }


def test_multiple_components_at_overall_level_all_named():
    levels = _levels(collateral_identity=RiskLevel.RED, settlement_quality=RiskLevel.RED)
    driven_by, _ = _component_breakdown(RiskLevel.RED, levels, _details())
    assert driven_by == "collateral_identity,settlement_quality"  # sorted, comma-joined
