# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis:
#   docs/evidence/qkernel_rebuild/cold_center_bias_fix_2026-06-16.md (the fix),
#   docs/evidence/qkernel_rebuild/modal_buyyes_drag_rootcause_2026-06-16.md,
#   src/forecast/settlement_residual_debias.py, src/forecast/debias_authority.py.
"""Tests for the settlement-residual cold-center-bias de-bias provider.

The provider fits per-(city, metric) trailing settlement-residual artifacts and
feeds them to DebiasAuthority on the product-agnostic ``city_station_representa-
tiveness`` basis. These tests pin the load-bearing properties of the fix:

  * WALK-FORWARD / no leakage: a case is fit ONLY on settlements strictly before
    its target date; a case never sees its own (or any future) outcome.
  * COLD -> WARM correction with the right sign: a cell whose reconstructed
    consensus runs COLD (consensus < realized => negative residual) yields a shift
    that, subtracted from the members, moves the center UP (warmer).
  * SIGN-SYMMETRY (no reverse-bias disease): a genuinely WARM cell yields a
    downward (cooling) correction, so the provider is not a one-way warm injector.
  * THIN-cell suppression: a cell with fewer than MIN_CELL_N trailing residuals
    publishes NO artifact (no shift) rather than a small-n guess.
  * ACTIVATION: the emitted artifact APPLIES through DebiasAuthority on a member
    set whose model_set_hash / station_mapping differ from the artifact (the
    WILDCARD representativeness match), and the served shift is the realized band
    center.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, time

import numpy as np
import pytest

from src.forecast.debias_authority import DebiasAuthority
from src.forecast.settlement_residual_debias import (
    MIN_CELL_N,
    SettlementResidualDebiasProvider,
    _Resid,
)
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import EventResolution, SEMANTICS_VERSION


def _resolution(station_id: str = "RJTT", unit: str = "C") -> EventResolution:
    return EventResolution(
        city="Tokyo", station_id=station_id, settlement_source_type="wu_icao",
        resolution_source=f"WU_{station_id}", target_local_date=date(2026, 6, 15),
        settlement_timezone="Asia/Tokyo", metric="high",
        measurement_unit=unit,  # type: ignore[arg-type]
        settlement_step_native=1.0, precision=1.0,
        rounding_rule="wmo_half_up",  # type: ignore[arg-type]
        finalization_local_time=time(12, 0, 0), semantics_version=SEMANTICS_VERSION,
    )


def _case(target: date, *, city: str = "Tokyo", metric: str = "high") -> ForecastCase:
    issue = datetime(target.year, target.month, target.day) - timedelta(days=1)
    return ForecastCase(
        city=city, city_id=city.lower(), station_id="RJTT",
        settlement_source_type="wu_icao", target_local_date=target, metric=metric,
        issue_time_utc=issue, lead_hours=24.0, season="summer", regime_key="zonal",
        unit="C", resolution=_resolution(), family_id=f"{city}_{metric}_{target}",
        source_cycle_time_utc=issue,
    )


def _series(base_target: date, residuals: list[float]) -> list[_Resid]:
    """A residual series on consecutive days ending the day BEFORE base_target."""
    out = []
    for i, r in enumerate(residuals):
        td = base_target - timedelta(days=len(residuals) - i)
        out.append(_Resid(target_date=td, residual_native=r))
    return out


def _member_set(case: ForecastCase, values_c: list[float]) -> FreshModelSet:
    members = tuple(
        RawModelMember(
            model_id=f"m{i}", product_id=f"m{i}", source_run_id=f"m{i}",
            source_cycle_time_utc=case.source_cycle_time_utc,
            available_at_utc=case.issue_time_utc, value_native=v,
            # mapping/hash that DO NOT match the artifact => only WILDCARD can apply
            station_mapping_id="some_other_mapping",
            raw_forecast_artifact_id="x", data_version="x",
        )
        for i, v in enumerate(values_c)
    )
    arr = np.asarray(values_c, dtype=float)
    return FreshModelSet(
        case=case, members=members, member_values_native=arr,
        min_native=float(arr.min()), max_native=float(arr.max()),
        model_set_hash="member_set_hash_that_does_not_match_artifact",
    )


def test_cold_cell_yields_upward_warming_shift():
    """A cold cell (consensus < realized => residual<0) corrects the center UP."""
    target = date(2026, 6, 15)
    # consistently cold by ~1.0C: residual = consensus - realized = -1.0
    series = _series(target, [-1.0] * (MIN_CELL_N + 5))
    prov = SettlementResidualDebiasProvider({("Tokyo", "high"): series})
    arts = prov.artifacts_for(_case(target))
    assert len(arts) == 1
    art = arts[0]
    # The shift is negative (the realized residual median); subtracting it warms μ.
    assert art.residual_mean_native < 0
    case = _case(target)
    corrected, applied = DebiasAuthority(arts).apply(case, _member_set(case, [20.0, 21.0, 22.0]))
    assert applied.activation_status == "APPLIED"
    # corrected = members - shift; shift<0 => corrected > members (warmer).
    assert float(np.mean(corrected)) > 21.0


def test_warm_cell_yields_downward_cooling_shift_sign_symmetry():
    """A genuinely warm cell corrects DOWN — the provider is not a warm injector."""
    target = date(2026, 6, 15)
    series = _series(target, [+1.2] * (MIN_CELL_N + 5))
    prov = SettlementResidualDebiasProvider({("Tokyo", "high"): series})
    art = prov.artifacts_for(_case(target))[0]
    assert art.residual_mean_native > 0
    case = _case(target)
    corrected, applied = DebiasAuthority((art,)).apply(case, _member_set(case, [30.0, 31.0, 32.0]))
    assert applied.activation_status == "APPLIED"
    assert float(np.mean(corrected)) < 31.0  # cooled


def test_walk_forward_excludes_own_and_future_settlements():
    """The case's own date and all future dates are excluded from the fit."""
    target = date(2026, 6, 15)
    # Past = cold (-1.0), but inject a huge WARM residual ON the target date and AFTER.
    past = _series(target, [-1.0] * (MIN_CELL_N + 2))
    future = [
        _Resid(target_date=target, residual_native=+50.0),               # own day
        _Resid(target_date=target + timedelta(days=1), residual_native=+50.0),  # future
    ]
    prov = SettlementResidualDebiasProvider({("Tokyo", "high"): past + future})
    art = prov.artifacts_for(_case(target))[0]
    # If leakage occurred the median would be pulled positive; it must stay ~ -1.0.
    assert art.residual_mean_native == pytest.approx(-1.0, abs=0.25)


def test_thin_cell_publishes_no_artifact():
    """Below MIN_CELL_N trailing residuals: no artifact, no shift."""
    target = date(2026, 6, 15)
    series = _series(target, [-1.0] * (MIN_CELL_N - 1))
    prov = SettlementResidualDebiasProvider({("Tokyo", "high"): series})
    assert prov.artifacts_for(_case(target)) == ()


def test_thin_cell_shrinks_toward_pooled_not_overfit():
    """A thin-ish cell is pulled toward the metric-pooled median (anti-overfit)."""
    target = date(2026, 6, 15)
    # Target cell barely meets MIN_CELL_N with a noisy -2.0 median; pooled is -0.3.
    cell = _series(target, [-2.0] * MIN_CELL_N)
    # A large pooled population at -0.3 across other cities, same metric.
    pooled_other = {
        (f"City{j}", "high"): _series(target, [-0.3] * 60) for j in range(5)
    }
    cells = {("Tokyo", "high"): cell, **pooled_other}
    prov = SettlementResidualDebiasProvider(cells)
    art = prov.artifacts_for(_case(target))[0]
    # Shrunk strictly between the cell median (-2.0) and the pooled (-0.3).
    assert -2.0 < art.residual_mean_native < -0.3


def test_served_shift_is_realized_band_center():
    """proposed_shift == residual_mean so DebiasAuthority admits (band center)."""
    target = date(2026, 6, 15)
    series = _series(target, [-0.8] * (MIN_CELL_N + 10))
    prov = SettlementResidualDebiasProvider({("Tokyo", "high"): series})
    art = prov.artifacts_for(_case(target))[0]
    assert art.proposed_shift_native == art.residual_mean_native
    assert art.n >= MIN_CELL_N
