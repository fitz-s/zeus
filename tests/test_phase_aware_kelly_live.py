# Lifecycle: created=2026-05-04; last_reviewed=2026-07-24; last_reused=2026-07-24
# Purpose: pin the one-law Kelly resolver — GLOBAL_KELLY_FRACTION for every
#   known key, fail-closed 0.0 on unknown identity / non-trading phase /
#   hard oracle veto, and the ABSENCE of the retired A6 multiplier stages
#   (per-strategy, per-phase, observed-day-fraction, phase-source, city, CI).
# Reuse: run on any change to src/strategy/kelly.py or the entry-sizing path.
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/
#   COLLISION.md group B + FINAL_SPEC.md §What remains of strategy_profile_registry
#   (supersedes the A6 four-source resolver this file previously pinned).
"""One-law Kelly resolver antibodies.

The A6 resolver (strategy_phase × oracle × observed_fraction × phase_source)
is retired: labels no longer own economics, and elapsed wall-clock is not
observed information (the Day0-conditioned posterior already carries the
remaining-day distribution). What the resolver still owes:

1. identity fail-closed — unknown strategy_key → 0.0 (mis-routing bug);
2. lifecycle validity — pre_trading/post_trading/resolved phases → 0.0
   (a universal market-state fact, formerly enforced only by per-key
   phase-override zeros in the registry);
3. hard oracle veto — penalty 0.0 = settlement truth UNAVAILABLE → 0.0;
   soft penalties (0<m<1) NO LONGER scale size;
4. otherwise exactly GLOBAL_KELLY_FRACTION, for every known key, every
   tradeable phase, every city, every clock time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from src.strategy import oracle_penalty, strategy_profile
from src.strategy.kelly import (
    GLOBAL_KELLY_FRACTION,
    phase_aware_kelly_multiplier,
    strategy_kelly_multiplier,
)


@dataclass
class _City:
    name: str
    timezone: str


_NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
_TARGET = date(2026, 7, 24)


@pytest.fixture(autouse=True)
def _force_fresh_registry_and_oracle(monkeypatch, tmp_path):
    strategy_profile._reload_for_test()
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    oracle_penalty._reset_for_test()
    yield
    strategy_profile._reload_for_test()
    oracle_penalty._reset_for_test()


def _resolve(key: str, phase: str | None, city: _City | None = None) -> float:
    return phase_aware_kelly_multiplier(
        strategy_key=key,
        market_phase=phase,
        city=city or _City("NYC", "America/New_York"),
        temperature_metric="high",
        decision_time_utc=_NOW,
        target_local_date=_TARGET,
        phase_source=None,
    )


def test_known_keys_all_resolve_to_global_fraction():
    """Every live registry key sizes at GLOBAL_KELLY_FRACTION in a tradeable
    phase — the label carries no economics."""
    for key in ("settlement_capture", "day0_nowcast_entry", "forecast_qkernel_entry"):
        assert _resolve(key, "settlement_day") == pytest.approx(GLOBAL_KELLY_FRACTION), key
    for key in ("center_buy", "forecast_qkernel_entry", "opening_inertia"):
        assert _resolve(key, "pre_settlement_day") == pytest.approx(GLOBAL_KELLY_FRACTION), key


def test_unknown_key_fails_closed():
    assert _resolve("nonexistent_strategy", "settlement_day") == 0.0
    assert strategy_kelly_multiplier("nonexistent_strategy") == 0.0
    assert strategy_kelly_multiplier("") == 0.0
    assert strategy_kelly_multiplier(None) == 0.0


def test_known_key_flat_multiplier():
    assert strategy_kelly_multiplier("settlement_capture") == pytest.approx(
        GLOBAL_KELLY_FRACTION
    )
    assert strategy_kelly_multiplier("opening_inertia") == pytest.approx(
        GLOBAL_KELLY_FRACTION
    )


def test_non_trading_phases_zero_for_every_key():
    """Lifecycle validity is universal: no key can enter pre_trading /
    post_trading / resolved."""
    for key in ("settlement_capture", "center_buy", "forecast_qkernel_entry",
                "opening_inertia", "day0_nowcast_entry"):
        for phase in ("pre_trading", "post_trading", "resolved"):
            assert _resolve(key, phase) == 0.0, (key, phase)


def test_phase_none_falls_soft_to_global_fraction():
    """phase=None (legacy fixtures / pre-evidence callers) is not a
    non-trading phase — resolves to the global fraction, preserving the
    pre-A5 fail-soft path."""
    assert _resolve("forecast_qkernel_entry", None) == pytest.approx(
        GLOBAL_KELLY_FRACTION
    )


def test_hard_oracle_veto_zeroes(monkeypatch):
    """A 0.0 oracle penalty (settlement truth unavailable) zeroes size —
    an authority failure, not a haircut. The resolver reads only
    penalty_multiplier, so a minimal stub suffices."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.strategy.oracle_penalty.get_oracle_info",
        lambda _city, _metric: SimpleNamespace(penalty_multiplier=0.0),
    )
    assert _resolve("settlement_capture", "settlement_day") == 0.0


def test_soft_oracle_penalty_no_longer_scales(monkeypatch):
    """A soft oracle penalty (0<m<1) does NOT scale size any more — that
    uncertainty belongs in the probability authority, not the sizer."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "src.strategy.oracle_penalty.get_oracle_info",
        lambda _city, _metric: SimpleNamespace(penalty_multiplier=0.5),
    )
    assert _resolve("settlement_capture", "settlement_day") == pytest.approx(
        GLOBAL_KELLY_FRACTION
    )


def test_retired_stages_are_gone():
    """The A6 machinery is deleted, not dormant: no observed-fraction, no
    phase-source haircut, no per-key floors remain importable."""
    import src.strategy.kelly as kelly_mod

    for gone in (
        "observed_target_day_fraction",
        "OBSERVED_FRACTION_STRATEGY_KEYS",
        "OBSERVED_FRACTION_MIN",
        "FALLBACK_F1_HAIRCUT",
        "_observed_fraction_multiplier",
    ):
        assert not hasattr(kelly_mod, gone), gone


def test_clock_and_city_do_not_move_the_fraction():
    """Same key+phase resolves identically across cities and clock times —
    elapsed local day and geography are not sizing inputs."""
    early = datetime(2026, 7, 24, 0, 30, tzinfo=timezone.utc)
    late = datetime(2026, 7, 24, 23, 30, tzinfo=timezone.utc)
    for when in (early, late):
        for c in (_City("Wellington", "Pacific/Auckland"), _City("LA", "America/Los_Angeles")):
            got = phase_aware_kelly_multiplier(
                strategy_key="settlement_capture",
                market_phase="settlement_day",
                city=c,
                temperature_metric="high",
                decision_time_utc=when,
                target_local_date=_TARGET,
                phase_source="fallback_f1",  # retired input — must not haircut
            )
            assert got == pytest.approx(GLOBAL_KELLY_FRACTION), (when, c.name)
