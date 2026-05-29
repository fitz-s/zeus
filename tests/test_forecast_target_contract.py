# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (ForecastObject/SettlementObject contract);
#   CRITIC_SYNTHESIS_2026-05-29 §2 (lineage collapse, unit mis-scale, target-equality
#   completeness Cons-SEV-2-E). Relationship tests precede implementation per
#   ~/.claude/CLAUDE.md ("relationship tests -> implementation -> function tests").
"""Relationship contract: a residual binds a forecast random variable to a
settlement outcome ONLY when their targets are identical across every dimension
that defines the random variable.

This is the antibody for the mixed-random-variable bug family the redesign exists
to kill: TIGGE-6h residuals paired to the wrong (or absent) settlement, product
lineage collapse, and degC/degF unit mis-scale. The invariant is cross-module
(forecast side vs settlement side), so it is expressed as a relationship test
before any implementation.
"""

from __future__ import annotations

import pytest

from src.contracts.forecast_target import (
    ForecastTarget,
    ForecastTargetMismatchError,
    assert_same_target,
)


def _high_target(**overrides) -> ForecastTarget:
    base = dict(
        city="Chicago",
        metric="HIGH",
        target_local_date="2026-05-20",
        settlement_station="KORD",
        settlement_unit="degF",
        settlement_authority="wu_icao_history_v1",
    )
    base.update(overrides)
    return ForecastTarget(**base)


def test_identical_targets_pair_without_error():
    """Forecast and settlement describing the same RV may form a residual."""
    forecast_side = _high_target()
    settlement_side = _high_target()
    # Must not raise; returns the shared target.
    shared = assert_same_target(forecast_side, settlement_side)
    assert shared == forecast_side


def test_metric_mismatch_is_unconstructable():
    """A HIGH forecast paired to a LOW settlement is a different RV -> raise."""
    forecast_side = _high_target(metric="HIGH")
    settlement_side = _high_target(metric="LOW")
    with pytest.raises(ForecastTargetMismatchError) as exc:
        assert_same_target(forecast_side, settlement_side)
    assert "metric" in str(exc.value)


def test_target_local_date_mismatch_is_unconstructable():
    """Same city/metric but different target day is a different RV -> raise."""
    forecast_side = _high_target(target_local_date="2026-05-20")
    settlement_side = _high_target(target_local_date="2026-05-21")
    with pytest.raises(ForecastTargetMismatchError) as exc:
        assert_same_target(forecast_side, settlement_side)
    assert "target_local_date" in str(exc.value)


def test_unit_mismatch_is_unconstructable():
    """degC forecast paired to a degF settlement would silently mis-scale by 1.8x
    (Cons-SEV-1.C) -> the target gate refuses it."""
    forecast_side = _high_target(settlement_unit="degC")
    settlement_side = _high_target(settlement_unit="degF")
    with pytest.raises(ForecastTargetMismatchError) as exc:
        assert_same_target(forecast_side, settlement_side)
    assert "unit" in str(exc.value)


def test_station_mismatch_is_unconstructable():
    """Same city name but different settlement station = different payout truth
    (Cons-SEV-2-E: station identity must be in the target tuple) -> raise."""
    forecast_side = _high_target(settlement_station="KORD")
    settlement_side = _high_target(settlement_station="KMDW")
    with pytest.raises(ForecastTargetMismatchError) as exc:
        assert_same_target(forecast_side, settlement_side)
    assert "station" in str(exc.value)


def test_authority_mismatch_is_unconstructable():
    """Different settlement source authority = different truth basis -> raise."""
    forecast_side = _high_target(settlement_authority="wu_icao_history_v1")
    settlement_side = _high_target(settlement_authority="ogimet_metar_v1")
    with pytest.raises(ForecastTargetMismatchError) as exc:
        assert_same_target(forecast_side, settlement_side)
    assert "authority" in str(exc.value)


def test_target_is_frozen():
    """The target identity is immutable once constructed."""
    t = _high_target()
    with pytest.raises(Exception):
        t.metric = "LOW"  # type: ignore[misc]
