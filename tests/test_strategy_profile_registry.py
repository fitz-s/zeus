# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A4 (StrategyProfile registry + 4-site cutover invariants).
"""StrategyProfile registry regression antibodies.

These tests pin the contracts that A4 ships:

1. The registry is the SINGLE source of truth for per-strategy authority.
   Five hardcoded lists pre-A4 (STRATEGY_KELLY_MULTIPLIERS,
   LIVE_SAFE_STRATEGIES, _LIVE_ALLOWED_STRATEGIES, KNOWN_STRATEGIES, plus
   the implicit edge-source whitelist) now derive from the same YAML
   file. A regression that re-introduces a hardcoded set anywhere is
   caught by these tests.

2. The pre-A4 LIVE_SAFE / _LIVE_ALLOWED divergence (Bug review §E:
   shoulder_sell in LIVE_SAFE but not _LIVE_ALLOWED) is RESOLVED by the
   live_status field. The current registry preserves the divergence
   semantically (shoulder_sell is shadow → boots but doesn't enter)
   but the two sets now derive from one source — un-driftable.

3. Schema enforcement at load time. A typo in a constraint field
   (e.g., ``allowed_market_phazes``) raises RegistrySchemaError at
   boot — silent muting of constraints is the failure mode A4 closes.

4. Behavior-equivalence with the pre-A4 hardcoded lists. The 6
   strategy keys, their Kelly defaults, and their LIVE_SAFE /
   _LIVE_ALLOWED membership all match the pre-A4 values verbatim.
   The §A6 phase-aware Kelly resolver layers on top of these defaults
   — A4 itself does not change live sizing.

5. Fail-closed behavior on unknown keys: ``get(unknown)`` raises;
   ``kelly_default_multiplier(unknown)`` returns 0.0; everywhere
   else the typed exception lets the caller decide.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.strategy import strategy_profile as sp
from src.strategy.strategy_profile import (
    ProfileNotFound,
    RegistrySchemaError,
    StrategyProfile,
)


@pytest.fixture(autouse=True)
def _force_fresh_registry():
    """Each test gets a freshly loaded registry from the canonical YAML
    so tests that monkeypatch the env or registry don't bleed cache."""
    sp._reload_for_test()
    yield
    sp._reload_for_test()


# ── pre-A4 behavior equivalence ─────────────────────────────────────── #


def test_all_six_strategies_are_registered():
    keys = sp.all_keys()
    assert keys == frozenset({
        "settlement_capture",
        "center_buy",
        "opening_inertia",
        "shoulder_sell",
        "shoulder_buy",
        "center_sell",
    })


def test_kelly_defaults_match_pre_A4_STRATEGY_KELLY_MULTIPLIERS_verbatim():
    """Pre-A4 dict:
        settlement_capture=1.0, center_buy=1.0, opening_inertia=0.5,
        shoulder_sell=0.0, shoulder_buy=0.0, center_sell=0.0
    A4 must not change live sizing — only relocate the dict.
    """
    expected = {
        "settlement_capture": 1.0,
        "center_buy": 1.0,
        "opening_inertia": 0.5,
        "shoulder_sell": 0.0,
        "shoulder_buy": 0.0,
        "center_sell": 0.0,
    }
    for key, value in expected.items():
        assert sp.kelly_default_multiplier(key) == value, (
            f"{key} kelly default drifted from pre-A4: "
            f"got {sp.kelly_default_multiplier(key)}, expected {value}"
        )


def test_kelly_default_for_unknown_key_is_zero():
    """Fail-closed: unknown strategy → 0.0 (matches pre-A4 dict.get default)."""
    assert sp.kelly_default_multiplier("nonexistent") == 0.0
    assert sp.kelly_default_multiplier("") == 0.0


def test_live_safe_keys_match_pre_A4_LIVE_SAFE_STRATEGIES():
    """Pre-A4 LIVE_SAFE_STRATEGIES = {opening_inertia, center_buy,
    settlement_capture, shoulder_sell}. Boot-allowable = live OR shadow.
    """
    assert sp.live_safe_keys() == frozenset({
        "opening_inertia",
        "center_buy",
        "settlement_capture",
        "shoulder_sell",
    })


def test_live_allowed_keys_match_pre_A4__LIVE_ALLOWED_STRATEGIES():
    """Pre-A4 _LIVE_ALLOWED_STRATEGIES = {settlement_capture, center_buy,
    opening_inertia}. Runtime-entry = live ONLY.
    """
    assert sp.live_allowed_keys() == frozenset({
        "settlement_capture",
        "center_buy",
        "opening_inertia",
    })


def test_live_allowed_is_strict_subset_of_live_safe():
    """The §A4 cutover invariant: every runtime-live strategy is also
    boot-allowed. The pre-A4 hardcoded sets had no machine-checked
    relation; A4 makes it derivable.
    """
    assert sp.live_allowed_keys() <= sp.live_safe_keys()


def test_shoulder_sell_resolves_pre_A4_divergence():
    """Bug review §E: shoulder_sell was in LIVE_SAFE but not _LIVE_ALLOWED
    (a divergence between two hardcoded sets that nominally meant the
    same thing). Post-A4 the two sets derive from live_status; shoulder_sell
    is ``shadow`` — boots OK, runtime entry blocked. The divergence is
    resolved into a single semantic state.
    """
    profile = sp.get("shoulder_sell")
    assert profile.live_status == "shadow"
    assert profile.is_boot_allowed() is True
    assert profile.is_runtime_live() is False


# ── control_plane / cycle_runner backward compat ───────────────────── #


def test_control_plane_LIVE_SAFE_STRATEGIES_lazy_attr_returns_registry_set():
    """Pre-A4 callers import LIVE_SAFE_STRATEGIES as a module attribute.
    Post-A4 the attribute is resolved through __getattr__ each access,
    delegating to ``strategy_profile.live_safe_keys``. This test pins
    the lazy resolution + verifies the exposed set matches the registry.
    """
    from src.control import control_plane

    assert control_plane.LIVE_SAFE_STRATEGIES == sp.live_safe_keys()
    assert control_plane._LIVE_ALLOWED_STRATEGIES == sp.live_allowed_keys()


def test_control_plane_unknown_attr_still_raises_AttributeError():
    """The PEP 562 __getattr__ must not swallow real attribute errors —
    a typo'd attribute should still raise so callers don't silently
    no-op on misspellings.
    """
    from src.control import control_plane

    with pytest.raises(AttributeError, match="LIVE_SAFE_TYPO"):
        _ = control_plane.LIVE_SAFE_TYPO


def test_cycle_runner_KNOWN_STRATEGIES_matches_live_safe_keys():
    """Pre-A4 KNOWN_STRATEGIES = {settlement_capture, shoulder_sell,
    center_buy, opening_inertia}. Post-A4 it derives from live_safe_keys
    (boot-allowable set). Both sets are identical for the initial registry.
    """
    from src.engine import cycle_runner

    assert cycle_runner.KNOWN_STRATEGIES == sp.live_safe_keys()


def test_is_strategy_enabled_blocks_shoulder_sell_runtime_entry():
    """shoulder_sell is shadow → boots OK but runtime entry blocked.
    The cutover preserves the pre-A4 ``shoulder_sell not in
    _LIVE_ALLOWED_STRATEGIES`` semantics through is_strategy_enabled.
    """
    from src.control.control_plane import is_strategy_enabled

    assert is_strategy_enabled("shoulder_sell") is False
    assert is_strategy_enabled("settlement_capture") is True
    assert is_strategy_enabled("center_buy") is True
    assert is_strategy_enabled("opening_inertia") is True
    assert is_strategy_enabled("shoulder_buy") is False
    assert is_strategy_enabled("center_sell") is False
    assert is_strategy_enabled("nonexistent_strategy") is False


# ── kelly.py cutover ───────────────────────────────────────────────── #


def test_kelly_strategy_kelly_multiplier_routes_through_registry():
    """The pre-A4 dict literal is gone; ``strategy_kelly_multiplier``
    delegates to the registry. Behavior is identical for known keys.
    """
    from src.strategy.kelly import strategy_kelly_multiplier

    assert strategy_kelly_multiplier("settlement_capture") == 1.0
    assert strategy_kelly_multiplier("opening_inertia") == 0.5
    assert strategy_kelly_multiplier("shoulder_sell") == 0.0
    assert strategy_kelly_multiplier("nonexistent") == 0.0
    assert strategy_kelly_multiplier(None) == 0.0
    assert strategy_kelly_multiplier("") == 0.0
    # Whitespace handling preserved from pre-A4.
    assert strategy_kelly_multiplier("  settlement_capture  ") == 1.0


# ── ProfileNotFound contract ───────────────────────────────────────── #


def test_get_unknown_strategy_raises_ProfileNotFound():
    with pytest.raises(ProfileNotFound, match="unknown"):
        sp.get("not_a_strategy")


def test_get_empty_strategy_raises_ProfileNotFound():
    with pytest.raises(ProfileNotFound, match="empty"):
        sp.get("")


def test_try_get_unknown_strategy_returns_None():
    assert sp.try_get("not_a_strategy") is None


def test_ProfileNotFound_is_a_KeyError():
    """Backward compat: existing ``try/except KeyError`` blocks in
    legacy code keep their semantics."""
    assert issubclass(ProfileNotFound, KeyError)


# ── phase override resolution (A6 preview) ──────────────────────────── #


def test_settlement_capture_phase_overrides_match_PLAN_A6():
    """PLAN.md §A6 phase-aware Kelly values, pinned by registry."""
    profile = sp.get("settlement_capture")
    assert profile.kelly_for_phase("pre_trading") == 0.0
    assert profile.kelly_for_phase("pre_settlement_day") == 0.5
    assert profile.kelly_for_phase("settlement_day") == 1.0
    assert profile.kelly_for_phase("post_trading") == 0.0
    assert profile.kelly_for_phase("resolved") == 0.0


def test_kelly_for_phase_falls_back_to_default_for_unknown_phase():
    """A made-up phase name should not crash; should return the default."""
    profile = sp.get("settlement_capture")
    assert profile.kelly_for_phase("not_a_real_phase") == profile.kelly_default_multiplier


def test_kelly_for_phase_None_returns_default():
    """Pre-A6 callers that don't yet pass market_phase get the legacy
    per-strategy default — no behavior change from the pre-A6 path."""
    profile = sp.get("settlement_capture")
    assert profile.kelly_for_phase(None) == profile.kelly_default_multiplier


# ── schema enforcement ─────────────────────────────────────────────── #


def test_schema_load_rejects_unknown_field(tmp_path: Path):
    """A typo in the YAML (e.g. ``allowed_market_phazes``) must not
    silently mute the constraint — load must raise."""
    bad = tmp_path / "bad_registry.yaml"
    bad.write_text(
        "settlement_capture:\n"
        "  thesis: ok\n"
        "  live_status: live\n"
        "  allowed_market_phazes: [settlement_day]\n"  # TYPO
        "  allowed_discovery_modes: [day0_capture]\n"
        "  allowed_directions: [buy_yes]\n"
        "  allowed_bin_topology: [point]\n"
        "  metric_support: {high: live, low: blocked}\n"
        "  kelly_default_multiplier: 1.0\n"
        "  kelly_phase_overrides: {}\n"
        "  min_shadow_decisions: 0\n"
        "  min_settled_decisions: 0\n"
        "  promotion_evidence_ref: null\n"
    )
    with pytest.raises(RegistrySchemaError, match="unexpected fields"):
        sp._reload_for_test(bad)


def test_schema_load_rejects_invalid_live_status(tmp_path: Path):
    bad = tmp_path / "bad_registry.yaml"
    bad.write_text(
        "settlement_capture:\n"
        "  thesis: ok\n"
        "  live_status: maybe\n"  # INVALID
        "  allowed_market_phases: [settlement_day]\n"
        "  allowed_discovery_modes: [day0_capture]\n"
        "  allowed_directions: [buy_yes]\n"
        "  allowed_bin_topology: [point]\n"
        "  metric_support: {high: live, low: blocked}\n"
        "  kelly_default_multiplier: 1.0\n"
        "  kelly_phase_overrides: {}\n"
        "  min_shadow_decisions: 0\n"
        "  min_settled_decisions: 0\n"
        "  promotion_evidence_ref: null\n"
    )
    with pytest.raises(RegistrySchemaError, match="live_status"):
        sp._reload_for_test(bad)


def test_schema_load_rejects_kelly_default_out_of_range(tmp_path: Path):
    bad = tmp_path / "bad_registry.yaml"
    bad.write_text(
        "settlement_capture:\n"
        "  thesis: ok\n"
        "  live_status: live\n"
        "  allowed_market_phases: [settlement_day]\n"
        "  allowed_discovery_modes: [day0_capture]\n"
        "  allowed_directions: [buy_yes]\n"
        "  allowed_bin_topology: [point]\n"
        "  metric_support: {high: live, low: blocked}\n"
        "  kelly_default_multiplier: 1.5\n"  # > 1.0
        "  kelly_phase_overrides: {}\n"
        "  min_shadow_decisions: 0\n"
        "  min_settled_decisions: 0\n"
        "  promotion_evidence_ref: null\n"
    )
    with pytest.raises(RegistrySchemaError, match="kelly_default_multiplier"):
        sp._reload_for_test(bad)


def test_schema_load_rejects_kelly_phase_override_out_of_range(tmp_path: Path):
    bad = tmp_path / "bad_registry.yaml"
    bad.write_text(
        "settlement_capture:\n"
        "  thesis: ok\n"
        "  live_status: live\n"
        "  allowed_market_phases: [settlement_day]\n"
        "  allowed_discovery_modes: [day0_capture]\n"
        "  allowed_directions: [buy_yes]\n"
        "  allowed_bin_topology: [point]\n"
        "  metric_support: {high: live, low: blocked}\n"
        "  kelly_default_multiplier: 1.0\n"
        "  kelly_phase_overrides: {settlement_day: 2.0}\n"  # > 1.0
        "  min_shadow_decisions: 0\n"
        "  min_settled_decisions: 0\n"
        "  promotion_evidence_ref: null\n"
    )
    with pytest.raises(RegistrySchemaError, match="kelly_phase_overrides"):
        sp._reload_for_test(bad)


def test_schema_load_rejects_invalid_metric_support(tmp_path: Path):
    bad = tmp_path / "bad_registry.yaml"
    bad.write_text(
        "settlement_capture:\n"
        "  thesis: ok\n"
        "  live_status: live\n"
        "  allowed_market_phases: [settlement_day]\n"
        "  allowed_discovery_modes: [day0_capture]\n"
        "  allowed_directions: [buy_yes]\n"
        "  allowed_bin_topology: [point]\n"
        "  metric_support: {high: maybe, low: blocked}\n"  # INVALID
        "  kelly_default_multiplier: 1.0\n"
        "  kelly_phase_overrides: {}\n"
        "  min_shadow_decisions: 0\n"
        "  min_settled_decisions: 0\n"
        "  promotion_evidence_ref: null\n"
    )
    with pytest.raises(RegistrySchemaError, match="metric_support"):
        sp._reload_for_test(bad)


def test_schema_load_rejects_missing_required_field(tmp_path: Path):
    bad = tmp_path / "bad_registry.yaml"
    bad.write_text(
        "settlement_capture:\n"
        "  thesis: ok\n"
        "  live_status: live\n"
        # missing every constraint field
    )
    with pytest.raises(RegistrySchemaError, match="missing required fields"):
        sp._reload_for_test(bad)


# ── parametrized matrix: registry-driven dispatch matches pre-A4 ───── #


@pytest.mark.parametrize(
    "key,direction,topology,phase,expected_allowed",
    [
        ("settlement_capture", "buy_yes", "point",         "settlement_day",      True),
        ("settlement_capture", "buy_yes", "finite_range",  "settlement_day",      True),
        ("settlement_capture", "buy_no",  "point",         "settlement_day",      False),  # wrong direction
        ("settlement_capture", "buy_yes", "open_shoulder", "settlement_day",      False),  # wrong topology
        ("settlement_capture", "buy_yes", "point",         "post_trading",        False),  # wrong phase
        ("center_buy",         "buy_yes", "finite_range",  "pre_settlement_day",  True),
        ("center_buy",         "buy_yes", "finite_range",  "post_trading",        False),
        ("opening_inertia",    "buy_yes", "open_shoulder", "pre_settlement_day",  True),
        ("opening_inertia",    "buy_no",  "open_shoulder", "pre_settlement_day",  True),
        ("shoulder_sell",      "buy_no",  "open_shoulder", "settlement_day",      True),  # phase OK; live_status=shadow blocks runtime, not classifier
        ("shoulder_buy",       "buy_yes", "open_shoulder", "settlement_day",      False),  # blocked
        ("center_sell",        "buy_no",  "finite_range",  "settlement_day",      False),  # blocked
    ],
)
def test_dispatch_matrix_per_strategy_constraint_combinations(
    key, direction, topology, phase, expected_allowed
):
    """All (direction, topology, phase) combinations resolve coherently
    for every strategy. A regression that flips one constraint silently
    would surface here as a parametrize-row mismatch."""
    profile = sp.get(key)
    direction_ok = profile.is_direction_allowed(direction)
    topology_ok = profile.is_bin_topology_allowed(topology)
    phase_ok = profile.is_phase_allowed(phase)
    actual_allowed = direction_ok and topology_ok and phase_ok
    assert actual_allowed is expected_allowed, (
        f"{key} (dir={direction}, topo={topology}, phase={phase}): "
        f"direction_ok={direction_ok}, topology_ok={topology_ok}, "
        f"phase_ok={phase_ok}; combined={actual_allowed}, expected={expected_allowed}"
    )
