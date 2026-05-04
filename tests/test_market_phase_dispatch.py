# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §6.P3 + §8 T6 (mode-default preservation post-D-B).
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
    should_fetch_settlement_day_observation,
)
from src.strategy.market_phase import MarketPhase


def _candidate(*, discovery_mode: str = "", market_phase=None) -> SimpleNamespace:
    return SimpleNamespace(discovery_mode=discovery_mode, market_phase=market_phase)


# ---------------------------------------------------------------------- #
# Flag reader
# ---------------------------------------------------------------------- #


def test_flag_default_post_a6_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A6 cutover (PLAN.md §A6 + operator directive 2026-05-04
    "做就做到位"): the live default flips to ON. ``delenv`` (i.e., the
    flag unset entirely) resolves to True post-A6 — the migration is
    complete and the legacy branch is the kill-switch path now, not
    the default path.
    """
    monkeypatch.delenv("ZEUS_MARKET_PHASE_DISPATCH", raising=False)
    assert market_phase_dispatch_enabled() is True


def test_flag_explicit_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can still flip OFF via explicit ``"0"`` env override —
    A6 keeps the legacy branch as a kill-switch."""
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    assert market_phase_dispatch_enabled() is False


@pytest.mark.parametrize("value,expected", [
    # Empty string is treated as "unset" -> default (post-A6 = True).
    ("", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
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
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    cand = _candidate(discovery_mode=DiscoveryMode.DAY0_CAPTURE.value, market_phase=None)
    assert is_settlement_day_dispatch(cand) is True


def test_t6_flag_off_legacy_path_for_opening_hunt_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 invariant: flag OFF + OPENING_HUNT candidate ⇒ False (legacy).
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
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
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
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
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
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


# ---------------------------------------------------------------------- #
# Site 4 — cycle_runtime obs-fetch gate (critic R4 A7-M2 fix)
# ---------------------------------------------------------------------- #


def test_obs_fetch_gate_flag_off_day0_capture_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 byte-equal at site 4: flag OFF + mode=DAY0_CAPTURE ⇒ True
    regardless of market_phase. Byte-equal to pre-P3 ``mode ==
    DAY0_CAPTURE`` short-circuit.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    for phase in [None, MarketPhase.PRE_TRADING, MarketPhase.SETTLEMENT_DAY,
                  MarketPhase.POST_TRADING]:
        assert should_fetch_settlement_day_observation(
            mode=DiscoveryMode.DAY0_CAPTURE, market_phase=phase
        ) is True


def test_obs_fetch_gate_flag_off_other_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 byte-equal at site 4: flag OFF + non-DAY0_CAPTURE mode ⇒ False
    regardless of market_phase tagging.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    for mode in [DiscoveryMode.OPENING_HUNT, DiscoveryMode.UPDATE_REACTION]:
        for phase in [None, MarketPhase.SETTLEMENT_DAY, MarketPhase.PRE_SETTLEMENT_DAY]:
            assert should_fetch_settlement_day_observation(
                mode=mode, market_phase=phase
            ) is False, (
                f"flag OFF should not fetch obs for mode={mode}, phase={phase}"
            )


def test_obs_fetch_gate_flag_on_routes_on_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON + market_phase=SETTLEMENT_DAY ⇒ True regardless of mode.
    This is the production behavior P3 enables once the operator flips
    the flag: dispatch flips from cycle-axis to market-axis.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    for mode in [DiscoveryMode.OPENING_HUNT, DiscoveryMode.UPDATE_REACTION,
                 DiscoveryMode.DAY0_CAPTURE]:
        assert should_fetch_settlement_day_observation(
            mode=mode, market_phase=MarketPhase.SETTLEMENT_DAY
        ) is True

    for phase in [MarketPhase.PRE_TRADING, MarketPhase.PRE_SETTLEMENT_DAY,
                  MarketPhase.POST_TRADING, MarketPhase.RESOLVED]:
        assert should_fetch_settlement_day_observation(
            mode=DiscoveryMode.DAY0_CAPTURE,  # mode says DAY0 but phase doesn't
            market_phase=phase,
        ) is False


def test_obs_fetch_gate_flag_on_untagged_falls_back_to_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: flag ON + market_phase=None (Gamma parse error /
    off-cycle) falls back to legacy mode-axis. Without this, a single
    Gamma payload tz error during a DAY0_CAPTURE cycle would silently
    skip every observation fetch.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    assert should_fetch_settlement_day_observation(
        mode=DiscoveryMode.DAY0_CAPTURE, market_phase=None
    ) is True
    assert should_fetch_settlement_day_observation(
        mode=DiscoveryMode.OPENING_HUNT, market_phase=None
    ) is False


# ---------------------------------------------------------------------- #
# Critic R4 A4-M1 — attribution_drift defers when phase-axis dispatch ON
# ---------------------------------------------------------------------- #


def test_attribution_drift_defers_inference_when_phase_dispatch_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critic R4 A4-M1: when ZEUS_MARKET_PHASE_DISPATCH=1, the legacy
    drift detector at attribution_drift._infer_strategy_from_signature
    cannot reliably re-apply the entry-time rule (entry now reads
    market_phase, but trade_decisions row carries discovery_mode only).
    The function must return None to avoid emitting false-positive
    drift verdicts.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    from src.state.attribution_drift import (
        AttributionSignature,
        _infer_strategy_from_signature,
    )

    # Construct a signature that, under the legacy mode-axis rule, would
    # confidently return "settlement_capture". With the flag ON, the
    # detector must defer (return None) because phase-axis dispatch may
    # have written something different at entry time.
    sig = AttributionSignature(
        position_id="pos-1",
        label_strategy="settlement_capture",
        inferred_strategy=None,
        bin_topology="point",
        direction="buy_yes",
        discovery_mode="day0_capture",  # legacy says settlement_capture
        bin_label="0-1",
        is_label_inferable=False,
    )

    assert _infer_strategy_from_signature(sig) is None, (
        "with flag ON, _infer_strategy_from_signature must defer to "
        "insufficient_signal — legacy mode-axis re-application is "
        "unreliable when entry uses phase-axis"
    )


def test_attribution_drift_uses_legacy_inference_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF: attribution_drift behavior is unchanged. Same legacy
    mode-axis inference that pre-P3 callers depend on.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    from src.state.attribution_drift import (
        AttributionSignature,
        _infer_strategy_from_signature,
    )

    sig = AttributionSignature(
        position_id="pos-1",
        label_strategy="settlement_capture",
        inferred_strategy=None,
        bin_topology="point",
        direction="buy_yes",
        discovery_mode="day0_capture",
        bin_label="0-1",
        is_label_inferable=False,
    )

    assert _infer_strategy_from_signature(sig) == "settlement_capture", (
        "flag OFF must preserve legacy mode-axis inference"
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
