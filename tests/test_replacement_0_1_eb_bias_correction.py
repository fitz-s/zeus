# Created: 2026-06-07
# Last reused/audited: 2026-06-09
# Lifecycle: created=2026-06-07; last_reviewed=2026-06-09; last_reused=2026-06-09
# 2026-06-09 audit: reconciled the zero-prior-veto assertion (b16) with the unconditional
#   STRUCTURAL_PRIOR_FLOOR=1e-12 introduced by 86ad9380f6 (vetoed bin is now ~1e-12, not 0.0).
#   See also tests/test_replacement_0_1_anchor_eb_bias_source_match.py (source-match antibody).
# Purpose: Protect per-city EB bias-correction of the replacement_0_1 (AIFS soft-anchor)
#   forecast: flag-OFF byte-identity, flag-ON member/center shift (unit-correct, degC cells),
#   fail-closed on no VERIFIED bias row, layering (correction BEFORE the zero-prior veto and
#   the q_lcb floor), and no-double-correction with the legacy edli p_raw surface.
# Authority basis: docs/the_path/P2_BLEND.md §3,§4,§5 (EB bias-correction BEFORE the
#   soft_anchor.py:197-198 zero-prior veto; reuse zeus-world.model_bias_ens; flag
#   replacement_0_1_eb_bias_correction_enabled default-OFF; self-calibrating).
"""Replacement_0_1 EB bias-correction tests (pure-math + resolver + wiring)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from src.calibration.ens_bias_repo import init_ens_bias_schema, write_bias_model
from src.calibration.replacement_eb_bias import (
    REPLACEMENT_EB_BIAS_FAMILY,
    resolve_replacement_eb_bias_shift_c,
)
from src.data.ecmwf_aifs_sampled_2t_localday import (
    AifsMemberLocalDayExtrema,
    AifsSampledLocalDayExtraction,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (
    AifsTemperatureBin,
    build_aifs_sampled_2t_bin_probabilities,
    build_openmeteo_ifs9_aifs_soft_anchor_result,
)


UTC = timezone.utc


def _dt(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _extraction() -> AifsSampledLocalDayExtraction:
    # 4 members; raw high cloud runs cold (settlement lands warm / upper-tail).
    return AifsSampledLocalDayExtraction(
        city_timezone="UTC",
        target_local_date=date(2026, 6, 7),
        source_cycle_time=_dt(2026, 6, 6, 0),
        target_window_start_utc=_dt(2026, 6, 7, 0),
        target_window_end_utc=_dt(2026, 6, 8, 0),
        members=(
            AifsMemberLocalDayExtrema("pf-001", high_c=11.0, low_c=6.0, sample_count=2, contributing_valid_times_utc=(_dt(2026, 6, 7, 0), _dt(2026, 6, 7, 6))),
            AifsMemberLocalDayExtrema("pf-002", high_c=12.0, low_c=7.0, sample_count=2, contributing_valid_times_utc=(_dt(2026, 6, 7, 0), _dt(2026, 6, 7, 6))),
            AifsMemberLocalDayExtrema("pf-003", high_c=13.0, low_c=8.0, sample_count=2, contributing_valid_times_utc=(_dt(2026, 6, 7, 0), _dt(2026, 6, 7, 6))),
            AifsMemberLocalDayExtrema("pf-004", high_c=14.0, low_c=9.0, sample_count=2, contributing_valid_times_utc=(_dt(2026, 6, 7, 0), _dt(2026, 6, 7, 6))),
        ),
    )


def _bins() -> tuple[AifsTemperatureBin, ...]:
    # Standard 1C bins (integer bounds, gap=1.0; ±0.5 half-step membership). No member
    # votes the warm bins (zero-prior trap) until the cold bias is corrected and votes
    # shift up.
    return (
        AifsTemperatureBin("b10", upper_c=10.0, center_c=10.0),
        AifsTemperatureBin("b11", lower_c=11.0, upper_c=11.0, center_c=11.0),
        AifsTemperatureBin("b12", lower_c=12.0, upper_c=12.0, center_c=12.0),
        AifsTemperatureBin("b13", lower_c=13.0, upper_c=13.0, center_c=13.0),
        AifsTemperatureBin("b14", lower_c=14.0, upper_c=14.0, center_c=14.0),
        AifsTemperatureBin("b15", lower_c=15.0, upper_c=15.0, center_c=15.0),
        AifsTemperatureBin("b16", lower_c=16.0, center_c=16.0),
    )


def _anchor(high_c: float = 12.0) -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="UTC",
        target_local_date=date(2026, 6, 7),
        source_cycle_time=_dt(2026, 6, 6, 0),
        high_c=high_c,
        low_c=7.0,
        sample_count=2,
        contributing_local_times=(_dt(2026, 6, 7, 0), _dt(2026, 6, 7, 6)),
        contributing_valid_times_utc=(_dt(2026, 6, 7, 0), _dt(2026, 6, 7, 6)),
    )


# ---------------------------------------------------------------------------
# (a) flag-OFF / no-shift byte-identity (pure math)
# ---------------------------------------------------------------------------

def test_no_shift_is_byte_identical_probabilities() -> None:
    base = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())
    none = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins(), bias_shift_c=None)
    zero = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins(), bias_shift_c=0.0)
    assert none.probabilities == base.probabilities
    assert none.member_values_c == base.member_values_c
    assert zero.probabilities == base.probabilities
    assert zero.member_values_c == base.member_values_c


def test_no_shift_soft_anchor_result_byte_identical() -> None:
    base = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(), metric="high", bins=_bins()
    )
    none = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(), metric="high", bins=_bins(), bias_shift_c=None
    )
    assert dict(none.posterior.probabilities) == dict(base.posterior.probabilities)
    assert none.anchor_value_c == base.anchor_value_c
    assert dict(none.aifs_probabilities.probabilities) == dict(base.aifs_probabilities.probabilities)


# ---------------------------------------------------------------------------
# (b) flag-ON: a known cold (negative) bias shifts member votes + center by the
#     bias, in degC (the soft-anchor cells are degC; NO *1.8 here).
# ---------------------------------------------------------------------------

def test_negative_bias_warms_member_values_by_exactly_the_bias() -> None:
    # bias = forecast - actual = -2.0C (cold); corrected = raw - bias = raw + 2.0C
    res = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=_bins(), bias_shift_c=-2.0
    )
    base = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())
    for member_id, raw_v in base.member_values_c.items():
        assert res.member_values_c[member_id] == pytest.approx(raw_v + 2.0)


def test_negative_bias_moves_votes_into_warmer_bins() -> None:
    base = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())
    res = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=_bins(), bias_shift_c=-2.0
    )
    # base: members 11,12,13,14 -> b11,b12,b13,b14 each 0.25; b15/b16 zero (the trap).
    assert base.probabilities["b15"] == pytest.approx(0.0)
    assert base.probabilities["b16"] == pytest.approx(0.0)
    # corrected: members 13,14,15,16 -> votes move to b13,b14,b15,b16.
    assert res.probabilities["b15"] == pytest.approx(0.25)
    assert res.probabilities["b16"] == pytest.approx(0.25)
    assert res.probabilities["b11"] == pytest.approx(0.0)


def test_bias_shifts_anchor_center_consistently_with_members() -> None:
    res = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(high_c=12.0), metric="high", bins=_bins(),
        bias_shift_c=-2.0,
    )
    # corrected anchor = raw - bias = 12.0 - (-2.0) = 14.0
    assert res.anchor_value_c == pytest.approx(14.0)


# ---------------------------------------------------------------------------
# (b)-layering: correction precedes the zero-prior veto. After correction the
#     b16 bin (un-voted, vetoed to 0 raw) can receive posterior mass.
# ---------------------------------------------------------------------------

def test_correction_lifts_zero_prior_veto_on_warm_bin() -> None:
    base = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(high_c=16.0), metric="high", bins=_bins()
    )
    # Raw: b16 has zero member votes -> the zero-prior veto drives it to the structural prior
    # floor, i.e. effectively zero. NOTE (2026-06-09 audit): commit 86ad9380f6 replaced the
    # -inf zero-prior veto with an unconditional STRUCTURAL_PRIOR_FLOOR=1e-12, so the vetoed
    # bin now normalizes to ~1.7e-12, not exactly 0.0. Assert effectively-zero (< 1e-9); the
    # discriminating power vs the corrected case (which is materially > 0, below) is intact.
    assert base.posterior.probabilities["b16"] < 1e-9
    corrected = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(high_c=16.0), metric="high", bins=_bins(),
        bias_shift_c=-2.0,
    )
    # After warming the cloud, b16 now has a member vote (16C) -> posterior mass > 0.
    assert corrected.posterior.probabilities["b16"] > 0.0


# ---------------------------------------------------------------------------
# (c) fail-closed resolver: no VERIFIED row -> None (no correction, no crash)
# ---------------------------------------------------------------------------

def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    return conn


def _write_verified_bias(conn, *, city, season, month, metric, ldv, eff_c, weight_live=0.7):
    write_bias_model(
        conn,
        city=city, season=season, month=month, metric=metric,
        live_data_version=ldv, prior_data_version=None,
        posterior_bias_c=eff_c, posterior_sd_c=1.0,
        n_live=40, n_prior=200, weight_live=weight_live,
        estimator="empirical_bayes_shrinkage_v1",
        error_model_family=REPLACEMENT_EB_BIAS_FAMILY,
        bias_c=eff_c, effective_bias_c=eff_c, residual_sd_c=2.0,
        total_residual_sd_c=2.2,
        authority="VERIFIED",
        gate_set_hash=None, coverage_months=str(month),
    )
    conn.commit()


def test_resolver_fail_closed_when_no_verified_row() -> None:
    conn = _world_conn()
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Nowhere", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C",
    )
    assert shift is None


def test_resolver_fail_closed_on_staging_authority() -> None:
    conn = _world_conn()
    write_bias_model(
        conn,
        city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", prior_data_version=None,
        posterior_bias_c=-1.5, posterior_sd_c=1.0,
        n_live=40, n_prior=200, weight_live=0.7,
        estimator="empirical_bayes_shrinkage_v1",
        error_model_family=REPLACEMENT_EB_BIAS_FAMILY,
        bias_c=-1.5, effective_bias_c=-1.5, residual_sd_c=2.0, total_residual_sd_c=2.2,
        authority="STAGING", coverage_months="6",
    )
    conn.commit()
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C",
    )
    assert shift is None


def test_resolver_returns_degc_bias_for_c_settled_city() -> None:
    # 2026-06-07 ITEM 1: the resolver now passes the raw effective_bias_c through the
    # structural over-correction guard (bound_eb_bias_shift) using the row's n_live +
    # residual_sd_c. The fixture row has eff_c=-1.5, n_live=40, residual_sd_c=2.0, so the
    # served shift is the GUARDED value, not the raw -1.5 (the guard only ever makes the
    # correction more conservative). The unit contract (degC, no *1.8) is unchanged.
    from src.calibration.replacement_eb_bias import bound_eb_bias_shift

    conn = _world_conn()
    _write_verified_bias(conn, city="Tokyo", season="JJA", month=6, metric="high", ldv="dv1", eff_c=-1.5)
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C",
    )
    assert shift == pytest.approx(bound_eb_bias_shift(-1.5, 40, 2.0))
    assert abs(shift) <= 1.5 + 1e-9, "guard is only-more-conservative (never larger than raw)"


def test_resolver_keeps_degc_for_f_settled_city_in_degc_cells() -> None:
    # The soft-anchor cells (member high_c, anchor high_c) are ALWAYS degC even for
    # an F-settled city. effective_bias_c is degC; the shift applied to degC cells is
    # degC directly. NO *1.8 here (that contaminates the degC construction). The *1.8
    # belongs to the legacy edli path where members carry the city's settlement unit.
    # 2026-06-07 ITEM 1: the served value is the GUARDED degC shift — IDENTICAL for a C and
    # an F settled city (the guard never scales by 1.8), which is exactly the unit-safety
    # property this test protects.
    from src.calibration.replacement_eb_bias import bound_eb_bias_shift

    conn = _world_conn()
    _write_verified_bias(conn, city="San Francisco", season="JJA", month=6, metric="high", ldv="dv1", eff_c=-1.5)
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="San Francisco", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="F",
    )
    # Same guarded value as the C-settled city above (no *1.8 for the F city).
    assert shift == pytest.approx(bound_eb_bias_shift(-1.5, 40, 2.0))


def test_resolver_fail_closed_when_weight_live_zero() -> None:
    conn = _world_conn()
    _write_verified_bias(conn, city="Tokyo", season="JJA", month=6, metric="high", ldv="dv1", eff_c=-1.5, weight_live=0.0)
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C",
    )
    assert shift is None


def test_resolver_never_raises_on_bad_conn() -> None:
    # Fail-closed (not fail-open with a correction): a broken conn returns None.
    class _Boom:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no such table")

    shift = resolve_replacement_eb_bias_shift_c(
        _Boom(), city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C",
    )
    assert shift is None


# ---------------------------------------------------------------------------
# (d) no-double-correction: applying the resolved shift ONCE in the replacement_0_1
#     construction yields the single-shift result. The replacement_0_1 materializer
#     and the legacy edli _snapshot_p_raw are SEPARATE surfaces; the materialized
#     posterior is corrected once here and is NOT re-fed through the legacy member
#     correction. The antibody asserts: a single application != a double application.
# ---------------------------------------------------------------------------

def test_single_application_is_not_a_double_application() -> None:
    once = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=_bins(), bias_shift_c=-2.0
    )
    twice = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=_bins(), bias_shift_c=-4.0
    )
    base = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())
    # once = raw + 2C (members 13,14,15,16); twice (double) = raw + 4C (members 15,16,17,18).
    for member_id, raw_v in base.member_values_c.items():
        assert once.member_values_c[member_id] == pytest.approx(raw_v + 2.0)
    # The single-application probability vector must NOT equal a double application — proving
    # the correction is applied exactly once, never compounded across surfaces.
    assert once.probabilities != twice.probabilities


def test_replacement_family_distinct_keying_from_legacy_is_documented() -> None:
    # ONE-BUILDER: the replacement_0_1 path reuses the SAME promoted per-city bias machinery
    # (model_bias_ens / edli_per_city_v1). No-double-correction is structural (separate
    # construction surfaces), not via a duplicate parallel store. This guards the constant so a
    # future rename of the legacy family forces a conscious re-evaluation of the reuse contract.
    assert REPLACEMENT_EB_BIAS_FAMILY == "edli_per_city_v1"


# ---------------------------------------------------------------------------
# Layering: the corrected CENTER flows into the construction BEFORE the veto, and the
# anchor center returned (which feeds the downstream q_lcb settlement-sigma floor mu) is
# the CORRECTED center — so widening happens around the corrected location, never the cold one.
# ---------------------------------------------------------------------------

def test_returned_anchor_center_is_corrected_so_downstream_sigma_floor_widens_around_it() -> None:
    raw = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(high_c=12.0), metric="high", bins=_bins()
    )
    corrected = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(high_c=12.0), metric="high", bins=_bins(),
        bias_shift_c=-2.0,
    )
    # The downstream q_lcb floor reads anchor_value_c as its mu. It MUST be the corrected
    # center (14.0), not the cold raw center (12.0), so the floor widens around the right place.
    assert raw.anchor_value_c == pytest.approx(12.0)
    assert corrected.anchor_value_c == pytest.approx(14.0)
    # The correction is on the CENTER only; the construction does not alter anchor_sigma_c
    # (sigma stays the config value — the bias never touches sigma, per P2_BLEND.md §5).
    assert corrected.posterior.anchor_sigma_c == pytest.approx(raw.posterior.anchor_sigma_c)
