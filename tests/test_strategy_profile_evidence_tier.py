# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T1
"""T1 invariant tests: StrategyProfile.is_runtime_live() + evidence_tier extension."""
from __future__ import annotations

import pytest

from src.contracts.evidence_tier import EvidenceTier
from src.strategy.strategy_profile import (
    ProfileNotFound,
    RegistrySchemaError,
    StrategyProfile,
    _reload_for_test,
    get,
)


# ---------------------------------------------------------------------------
# Helpers to build minimal StrategyProfile instances in-memory
# ---------------------------------------------------------------------------

def _make_profile(
    live_status: str,
    evidence_tier: EvidenceTier,
    key: str = "test_strategy",
) -> StrategyProfile:
    return StrategyProfile(
        key=key,
        thesis="test",
        live_status=live_status,
        allowed_market_phases=frozenset(),
        allowed_discovery_modes=frozenset(),
        cycle_axis_dispatch_mode=None,
        allowed_directions=frozenset(),
        allowed_bin_topology=frozenset(),
        metric_support={},
        kelly_default_multiplier=0.0,
        kelly_phase_overrides={},
        min_shadow_decisions=0,
        min_settled_decisions=0,
        promotion_evidence_ref=None,
        evidence_tier=evidence_tier,
        evidence_tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
        promotion_blockers=(),
    )


# ---------------------------------------------------------------------------
# T1-4: is_runtime_live boundary tests
# ---------------------------------------------------------------------------

def test_t1_is_runtime_live_live_normal() -> None:
    """live_status=live + LIVE_NORMAL → is_runtime_live True."""
    p = _make_profile("live", EvidenceTier.LIVE_NORMAL)
    assert p.is_runtime_live() is True


def test_t1_is_runtime_live_live_pilot_tiny() -> None:
    """live_status=live + LIVE_PILOT_TINY → is_runtime_live True (minimum live tier)."""
    p = _make_profile("live", EvidenceTier.LIVE_PILOT_TINY)
    assert p.is_runtime_live() is True


def test_t1_is_runtime_live_limited_haircut() -> None:
    """live_status=live + LIVE_LIMITED_HAIRCUT → is_runtime_live True."""
    p = _make_profile("live", EvidenceTier.LIVE_LIMITED_HAIRCUT)
    assert p.is_runtime_live() is True


def test_t1_is_runtime_live_shadow_pass_blocked() -> None:
    """live_status=live + SHADOW_PASS (tier 3 < 5) → is_runtime_live False."""
    p = _make_profile("live", EvidenceTier.SHADOW_PASS)
    assert p.is_runtime_live() is False


def test_t1_is_runtime_live_idea_blocked() -> None:
    """live_status=live + IDEA (tier 0) → is_runtime_live False."""
    p = _make_profile("live", EvidenceTier.IDEA)
    assert p.is_runtime_live() is False


def test_t1_is_runtime_live_shadow_status_blocked() -> None:
    """live_status=shadow + LIVE_NORMAL → is_runtime_live False (live_status gate)."""
    p = _make_profile("shadow", EvidenceTier.LIVE_NORMAL)
    assert p.is_runtime_live() is False


def test_t1_is_runtime_live_shadow_status_with_shadow_pass() -> None:
    """live_status=shadow + SHADOW_PASS → is_runtime_live False."""
    p = _make_profile("shadow", EvidenceTier.SHADOW_PASS)
    assert p.is_runtime_live() is False


def test_t1_is_runtime_live_paper_cohort_blocked() -> None:
    """live_status=live + PAPER_COHORT (tier 4 < 5) → is_runtime_live False."""
    p = _make_profile("live", EvidenceTier.PAPER_COHORT)
    assert p.is_runtime_live() is False


# ---------------------------------------------------------------------------
# T1-5: ValueError on unknown tier string via loader
# ---------------------------------------------------------------------------

def test_t1_loader_unknown_tier_raises(tmp_path) -> None:
    """Loader raises RegistrySchemaError for unknown evidence_tier value."""
    yaml_content = """\
test_strategy:
  thesis: test
  live_status: shadow
  evidence_tier: BOGUS_TIER
  evidence_tier_required_for_live: LIVE_PILOT_TINY
  promotion_blockers: []
  allowed_market_phases: []
  allowed_discovery_modes: []
  cycle_axis_dispatch_mode: null
  allowed_directions: []
  allowed_bin_topology: []
  metric_support:
    high: blocked
    low:  blocked
  kelly_default_multiplier: 0.0
  kelly_phase_overrides: {}
  min_shadow_decisions: 0
  min_settled_decisions: 0
  promotion_evidence_ref: null
"""
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(yaml_content)
    with pytest.raises(RegistrySchemaError, match="BOGUS_TIER"):
        _reload_for_test(registry_path)


# ---------------------------------------------------------------------------
# T1-6: live registry loads cleanly (settlement_capture=LIVE_NORMAL)
# ---------------------------------------------------------------------------

def test_t1_live_registry_settlement_capture_live_normal() -> None:
    """settlement_capture in the live registry has evidence_tier=LIVE_NORMAL."""
    profile = get("settlement_capture")
    assert profile.evidence_tier == EvidenceTier.LIVE_NORMAL
    assert profile.is_runtime_live() is True


def test_t1_live_registry_shoulder_sell_shadow_pass() -> None:
    """shoulder_sell has evidence_tier=SHADOW_PASS and is_runtime_live=False."""
    profile = get("shoulder_sell")
    assert profile.evidence_tier == EvidenceTier.SHADOW_PASS
    assert profile.is_runtime_live() is False


def test_t1_live_registry_center_buy_live_normal() -> None:
    """center_buy has evidence_tier=LIVE_NORMAL."""
    profile = get("center_buy")
    assert profile.evidence_tier == EvidenceTier.LIVE_NORMAL
    assert profile.is_runtime_live() is True


def test_t1_live_registry_shoulder_buy_idea() -> None:
    """shoulder_buy has evidence_tier=IDEA (blocked)."""
    profile = get("shoulder_buy")
    assert profile.evidence_tier == EvidenceTier.IDEA
    assert profile.is_runtime_live() is False


def test_t1_live_registry_center_sell_idea() -> None:
    """center_sell has evidence_tier=IDEA (blocked)."""
    profile = get("center_sell")
    assert profile.evidence_tier == EvidenceTier.IDEA
    assert profile.is_runtime_live() is False
