# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v2.md §6.P3 + §8 T6 (mode-default preservation post-D-B).
"""D-B mode→phase migration tests (PLAN_v3 §6.P3).

The ``ZEUS_MARKET_PHASE_DISPATCH`` flag default OFF preserves byte-equal
legacy dispatch (T6). With the flag ON, dispatch reads
``candidate.market_phase`` instead of ``candidate.discovery_mode``.

Sites under test (per-candidate; cycle-axis sites in
``cycle_runner.py:_classify_edge_source`` + freshness short-circuit are
explicitly NOT migrated by P3 — see ``src/engine/dispatch.py``):

1. ``evaluator._edge_source_for(candidate, edge)``
2. ``evaluator._strategy_key_for(candidate, edge)``
3. ``evaluator._strategy_key_for_hypothesis(candidate, hypothesis)``
4. ``cycle_runtime.execute_discovery_phase`` obs-fetch gate
   (covered by integration test scaffolds; this file exercises the
   helper invariants).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.discovery_mode import DiscoveryMode
from src.engine.dispatch import (
    is_settlement_day_dispatch,
    market_phase_dispatch_enabled,
    settlement_day_dispatch_for_mode,
)
from src.strategy.market_phase import MarketPhase


def _candidate(*, discovery_mode: str = "", market_phase=None) -> SimpleNamespace:
    return SimpleNamespace(discovery_mode=discovery_mode, market_phase=market_phase)


# ---------------------------------------------------------------------- #
# Flag reader
# ---------------------------------------------------------------------- #


def test_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZEUS_MARKET_PHASE_DISPATCH", raising=False)
    assert market_phase_dispatch_enabled() is False


@pytest.mark.parametrize("value,expected", [
    ("0", False), ("", False), ("false", False), ("no", False), ("off", False),
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
])
def test_flag_truthy_recognized(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", value)
    assert market_phase_dispatch_enabled() is expected


# ---------------------------------------------------------------------- #
# T6 — flag-OFF byte-equal preservation (THE merge gate)
# ---------------------------------------------------------------------- #


def test_t6_flag_off_legacy_path_for_day0_capture_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 invariant: flag OFF + DAY0_CAPTURE candidate ⇒ True (legacy).
    """
    monkeypatch.delenv("ZEUS_MARKET_PHASE_DISPATCH", raising=False)
    cand = _candidate(discovery_mode=DiscoveryMode.DAY0_CAPTURE.value, market_phase=None)
    assert is_settlement_day_dispatch(cand) is True


def test_t6_flag_off_legacy_path_for_opening_hunt_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 invariant: flag OFF + OPENING_HUNT candidate ⇒ False (legacy).
    """
    monkeypatch.delenv("ZEUS_MARKET_PHASE_DISPATCH", raising=False)
    cand = _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value, market_phase=None)
    assert is_settlement_day_dispatch(cand) is False


def test_t6_flag_off_ignores_market_phase_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 invariant: even when candidate.market_phase IS tagged with
    SETTLEMENT_DAY, flag OFF ⇒ helper reads discovery_mode only.
    Pre-flag-flip safety: tagging without flipping must not change
    dispatch behavior anywhere.
    """
    monkeypatch.delenv("ZEUS_MARKET_PHASE_DISPATCH", raising=False)
    cand = _candidate(
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
        market_phase=MarketPhase.SETTLEMENT_DAY,
    )
    assert is_settlement_day_dispatch(cand) is False, (
        "flag OFF must ignore market_phase tag — T6 byte-equal invariant"
    )


# ---------------------------------------------------------------------- #
# Flag-ON behavior
# ---------------------------------------------------------------------- #


def test_flag_on_routes_settlement_day_market_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    cand = _candidate(
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,  # "wrong" mode
        market_phase=MarketPhase.SETTLEMENT_DAY,
    )
    assert is_settlement_day_dispatch(cand) is True, (
        "flag ON must route on market_phase, not discovery_mode"
    )


def test_flag_on_does_not_route_non_settlement_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    for phase in [
        MarketPhase.PRE_TRADING,
        MarketPhase.PRE_SETTLEMENT_DAY,
        MarketPhase.POST_TRADING,
        MarketPhase.RESOLVED,
    ]:
        cand = _candidate(
            discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,  # "wrong" mode
            market_phase=phase,
        )
        assert is_settlement_day_dispatch(cand) is False, (
            f"flag ON + market_phase={phase!r} must NOT route to "
            f"settlement_capture even when discovery_mode says DAY0_CAPTURE"
        )


def test_flag_on_falls_back_to_legacy_when_market_phase_untagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: untagged candidate with flag ON ⇒ legacy
    discovery_mode logic. Test fixtures and off-cycle paths produce
    untagged candidates; the migration must not turn them into hard
    failures.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    cand = _candidate(
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
        market_phase=None,
    )
    assert is_settlement_day_dispatch(cand) is True, (
        "untagged candidate must fall back to legacy discovery_mode "
        "logic with flag ON — fail-soft contract"
    )


# ---------------------------------------------------------------------- #
# Cycle-axis fallback (NOT migrated by P3)
# ---------------------------------------------------------------------- #


def test_settlement_day_dispatch_for_mode_uses_legacy_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """settlement_day_dispatch_for_mode is the cycle-level fallback
    (cycle_runner._classify_edge_source / freshness short-circuit). It
    intentionally does NOT consult the flag — those sites operate
    before per-candidate phase is available, and PLAN_v3 §6.P3 leaves
    them on the cycle axis.
    """
    for value in ("0", "1"):
        monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", value)
        assert settlement_day_dispatch_for_mode(DiscoveryMode.DAY0_CAPTURE) is True
        assert settlement_day_dispatch_for_mode(DiscoveryMode.OPENING_HUNT) is False


# ---------------------------------------------------------------------- #
# Evaluator-site integration
# ---------------------------------------------------------------------- #


def test_evaluator_edge_source_flag_off_preserves_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 byte-equal invariant exercised through the evaluator helper.
    Pre-P3 behavior: ``_edge_source_for(candidate, edge)`` returned
    "settlement_capture" iff candidate.discovery_mode == DAY0_CAPTURE.
    Post-P3 with flag OFF: same.
    """
    monkeypatch.delenv("ZEUS_MARKET_PHASE_DISPATCH", raising=False)
    from src.engine.evaluator import _edge_source_for

    bin_stub = SimpleNamespace(is_shoulder=False)
    edge_stub = SimpleNamespace(direction="buy_yes", bin=bin_stub)

    # DAY0_CAPTURE → settlement_capture (legacy)
    cand_day0 = _candidate(
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
        market_phase=MarketPhase.PRE_SETTLEMENT_DAY,  # tag intentionally "wrong"
    )
    assert _edge_source_for(cand_day0, edge_stub) == "settlement_capture"

    # OPENING_HUNT + buy_yes/center → opening_inertia (legacy precedence)
    cand_opening = _candidate(
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
        market_phase=MarketPhase.SETTLEMENT_DAY,  # tag intentionally "wrong"
    )
    assert _edge_source_for(cand_opening, edge_stub) == "opening_inertia"


def test_evaluator_edge_source_flag_on_routes_on_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    from src.engine.evaluator import _edge_source_for

    bin_stub = SimpleNamespace(is_shoulder=False)
    edge_stub = SimpleNamespace(direction="buy_yes", bin=bin_stub)

    # OPENING_HUNT mode but market_phase=SETTLEMENT_DAY → settlement_capture
    cand = _candidate(
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
        market_phase=MarketPhase.SETTLEMENT_DAY,
    )
    assert _edge_source_for(cand, edge_stub) == "settlement_capture", (
        "flag ON should route on market_phase, not discovery_mode"
    )


def test_evaluator_strategy_key_three_sites_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 3 evaluator sites (`_edge_source_for`, `_strategy_key_for`,
    `_strategy_key_for_hypothesis`) must agree on dispatch given the
    same candidate. This guards against a partial migration where one
    site flipped to phase-axis but a sibling stayed on mode-axis.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    from src.engine.evaluator import (
        _edge_source_for,
        _strategy_key_for,
        _strategy_key_for_hypothesis,
    )

    bin_stub = SimpleNamespace(is_shoulder=False)
    edge_stub = SimpleNamespace(direction="buy_yes", bin=bin_stub)
    hypothesis_stub = SimpleNamespace(direction="buy_yes", is_shoulder=False)

    cand = _candidate(
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
        market_phase=MarketPhase.SETTLEMENT_DAY,
    )

    assert _edge_source_for(cand, edge_stub) == "settlement_capture"
    assert _strategy_key_for(cand, edge_stub) == "settlement_capture"
    assert _strategy_key_for_hypothesis(cand, hypothesis_stub) == "settlement_capture"
