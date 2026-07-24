# Created: 2026-06-03
# Last audited: 2026-06-03
# Authority basis: D2 bias-family unify / wiring verdict 2026-06-03
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Relationship tests for the D2 bias-family entry/exit live unify gate.
# Reuse: Inspect monitor_refresh._resolve_unified_exit_bias_native +
#        evaluator._resolve_unified_entry_bias_native + event_reactor_adapter._EDLI_BIAS_FAMILY
#        before reuse; requires an in-memory fixture DB (no live DB dependency).
"""Relationship tests for the D2 bias-family entry/exit live unify gate.

The defect (D2): the LIVE EDLI reactor ENTRY bias-corrects p_raw from
``error_model_family='edli_per_city_v1'`` (71 VERIFIED rows — the populated family),
but the cycle evaluator FT path and the EXIT monitor read ``error_model_family=
'full_transport_v1'`` (ZERO rows), gated by ``full_transport_live_enabled``. Net: entry
corrected, exit/monitor uncorrected AND the exit FT route is permanently 0-row-dead.

The fix: ``feature_flags.exit_bias_family_unify_enabled`` lets evaluator + monitor read
the SAME populated VERIFIED family the reactor entry
uses, with the reactor's EXACT read shape, and apply the A4 lockstep (bias-shift +
residual widening + identity-Platt) so EXIT belief matches ENTRY belief.

D2 residual-widening fix (2026-07-24): the resolvers now return ``(shift, σ)`` — the shift
alone under-shrunk the high tail because the ``total_residual_sd_c`` widening the reactor
entry folds (``_edli_representativeness_sigma_native``) was silently dropped. σ is folded
into q_lcb via ``MarketAnalysis(representativeness_sigma=...)`` on both entry and exit.

Invariants pinned here (relationship-level, cross-module):
  (a) flag OFF  → both resolvers return None (legacy path untouched, byte-identical).
  (b) flag ON   → both resolvers return the edli_per_city_v1 (bias shift, residual σ) for a
                  corrected city (a row the LEGACY ft read shape 0-row-missed on the SAME DB).
  (c) consistency → under flag ON, entry and exit resolve the SAME family
                    (``event_reactor_adapter._EDLI_BIAS_FAMILY``) AND return the SAME
                    (shift, σ) pair.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.calibration.ens_bias_repo import (
    honest_residual_sigma_native,
    init_ens_bias_schema,
    read_bias_model,
    write_bias_model,
)
from src.engine.event_reactor_adapter import _EDLI_BIAS_FAMILY
from src.engine.evaluator import _resolve_unified_entry_bias_native
from src.engine.monitor_refresh import _resolve_unified_exit_bias_native

# ── fixture constants ─────────────────────────────────────────────────────────

_CITY_NAME = "TestCity"
_SEASON = "JJA"          # NH summer; target 2026-06-15, lat 40°N → JJA
_METRIC = "high"
_TARGET = date(2026, 6, 15)
_TMONTH = 6
_LIVE_DV = "ecmwf_opendata_mx2t3_local_calendar_day_max"
_EFF_BIAS_C = -2.5       # cold forecast → negative bias_c → members warmed by +2.5°C
_RESIDUAL_SD_C = 1.2     # in-sample forecast/station residual scale (degC)
_TOTAL_RESIDUAL_SD_C = 1.3  # full forward-predictive σ (σ_resid·sqrt(1+1/n)), the widening term


def _make_city(settlement_unit: str = "C", lat: float = 40.0):
    city = MagicMock()
    city.name = _CITY_NAME
    city.settlement_unit = settlement_unit
    city.lat = lat
    city.cluster = "Test"
    return city


def _apply_canonical_columns(conn: sqlite3.Connection) -> None:
    """Add canonical extension columns to model_bias_ens (mirrors migration script)."""
    from src.calibration.ens_bias_repo import _CANONICAL_EXTENSION_COLUMNS  # type: ignore[attr-defined]
    for col, sql_type in _CANONICAL_EXTENSION_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE model_bias_ens ADD COLUMN {col} {sql_type}")
        except Exception:
            pass  # already exists
    conn.commit()


def _make_db_with_edli_row(
    *,
    weight_live: float = 0.5,
    residual_sd_c: "float | None" = _RESIDUAL_SD_C,
    total_residual_sd_c: "float | None" = _TOTAL_RESIDUAL_SD_C,
) -> sqlite3.Connection:
    """In-memory DB with one edli_per_city_v1 VERIFIED row — mirroring the live rows:
    lead_bucket='LEGACY_POOLED', month=target_month, coverage covers the target month.
    residual_sd_c / total_residual_sd_c parametrize the widening term the D2 unify folds."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    _apply_canonical_columns(conn)
    write_bias_model(
        conn,
        city=_CITY_NAME,
        season=_SEASON,
        metric=_METRIC,
        month=_TMONTH,
        live_data_version=_LIVE_DV,
        prior_data_version="tigge_mx2t6_local_calendar_day_max",
        posterior_bias_c=_EFF_BIAS_C,
        posterior_sd_c=0.5,
        n_live=30,
        n_prior=100,
        weight_live=weight_live,
        estimator="test",
        training_cutoff="2026-06-03",
        recorded_at="2026-06-03",
        bias_c=_EFF_BIAS_C,
        bias_sd_c=0.5,
        residual_sd_c=residual_sd_c,
        heterogeneity_var_c2=0.04,
        correction_strength=1.0,
        effective_bias_c=_EFF_BIAS_C,
        total_residual_sd_c=total_residual_sd_c,
        error_model_family=_EDLI_BIAS_FAMILY,   # 'edli_per_city_v1'
        authority="VERIFIED",
        coverage_months=str(_TMONTH),           # covers the target month
        lead_bucket="LEGACY_POOLED",            # the LIVE stored bucket
    )
    conn.commit()
    return conn


# A settings dict that satisfies entry_forecast_config()-independent code paths is
# heavy to build; the resolvers only consult settings["feature_flags"][...] and then
# entry_forecast_config()/track_for_metric/data_version_for_track. We patch the latter
# trio to return the fixture's live_data_version deterministically.
_FF_ON = {"feature_flags": {"exit_bias_family_unify_enabled": True}}
_FF_OFF = {"feature_flags": {"exit_bias_family_unify_enabled": False}}


def _patch_dv(module: str):
    """Patch the (entry_forecast_config, track_for_metric, data_version_for_track) trio
    in the target module so the resolver resolves to the fixture's _LIVE_DV."""
    return (
        patch(f"{module}.entry_forecast_config", return_value=MagicMock()),
        patch(f"{module}.track_for_metric", return_value="high_track"),
        patch(f"{module}.data_version_for_track", return_value=_LIVE_DV),
    )


# ── invariant (a): flag OFF → resolvers return None (legacy path untouched) ────

def test_flag_off_entry_resolver_returns_none():
    conn = _make_db_with_edli_row()
    city = _make_city()
    with patch("src.engine.evaluator.settings", new=_FF_OFF):
        out = _resolve_unified_entry_bias_native(conn, city, _TARGET.isoformat(), _METRIC)
    assert out is None, "flag OFF must return None so the legacy entry path is byte-identical"


def test_flag_off_exit_resolver_returns_none():
    conn = _make_db_with_edli_row()
    city = _make_city()
    with patch("src.engine.monitor_refresh.settings", new=_FF_OFF):
        out = _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC)
    assert out is None, "flag OFF must return None so the legacy exit path is byte-identical"


def test_flag_off_does_no_db_read_byte_identity():
    """Byte-identity proof: flag OFF short-circuits on the FIRST flag check — it never reads
    the bias model. So the OFF path adds zero observable behavior vs pre-fix HEAD."""
    conn = _make_db_with_edli_row()
    city = _make_city()
    with patch("src.engine.monitor_refresh.settings", new=_FF_OFF), \
         patch("src.engine.monitor_refresh.read_bias_model") as _rbm_exit:
        assert _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC) is None
        _rbm_exit.assert_not_called()
    with patch("src.engine.evaluator.settings", new=_FF_OFF), \
         patch("src.engine.evaluator._read_bias_model_for_entry") as _rbm_entry:
        assert _resolve_unified_entry_bias_native(conn, city, _TARGET.isoformat(), _METRIC) is None
        _rbm_entry.assert_not_called()


# ── invariant (b): flag ON → resolvers return the edli_per_city_v1 bias shift ──
# (the SAME DB returns None under the LEGACY ft read shape → proves the 0-row miss is fixed)

def test_legacy_ft_read_shape_zero_row_misses_on_same_db():
    """Antibody: the LEGACY exit ft read (month=0, lead_bucket=computed,
    family='full_transport_v1') returns None on a DB that HAS the edli row. This is the
    pre-fix dead route the unify flag repairs."""
    conn = _make_db_with_edli_row()
    # full_transport_v1 family → zero rows
    assert read_bias_model(
        conn, city=_CITY_NAME, season=_SEASON, metric=_METRIC,
        live_data_version=_LIVE_DV, month=0,
        error_model_family="full_transport_v1", lead_bucket="L24_48",
    ) is None
    # even the edli family misses under the legacy month=0 + computed-lead_bucket shape
    assert read_bias_model(
        conn, city=_CITY_NAME, season=_SEASON, metric=_METRIC,
        live_data_version=_LIVE_DV, month=0,
        error_model_family=_EDLI_BIAS_FAMILY, lead_bucket="L24_48",
    ) is None


def test_flag_on_exit_resolver_returns_edli_bias():
    conn = _make_db_with_edli_row()
    city = _make_city(settlement_unit="C")
    p1, p2, p3 = _patch_dv("src.engine.monitor_refresh")
    with patch("src.engine.monitor_refresh.settings", new=_FF_ON), p1, p2, p3:
        out = _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC)
    assert out is not None, "flag ON must resolve the edli_per_city_v1 row (was a 0-row miss)"
    shift, sigma = out
    assert shift == pytest.approx(_EFF_BIAS_C, abs=1e-9), "C-city: native shift == effective_bias_c"
    # D2 fix: the widening term (max(total, residual)=total here) is now returned, not dropped.
    assert sigma == pytest.approx(_TOTAL_RESIDUAL_SD_C, abs=1e-9), "C-city: native σ == total_residual_sd_c"


def test_flag_on_entry_resolver_returns_edli_bias():
    conn = _make_db_with_edli_row()
    city = _make_city(settlement_unit="C")
    p1, p2, p3 = _patch_dv("src.engine.evaluator")
    with patch("src.engine.evaluator.settings", new=_FF_ON), p1, p2, p3:
        out = _resolve_unified_entry_bias_native(conn, city, _TARGET.isoformat(), _METRIC)
    assert out is not None, "flag ON must resolve the edli_per_city_v1 row (was a 0-row miss)"
    shift, sigma = out
    assert shift == pytest.approx(_EFF_BIAS_C, abs=1e-9)
    assert sigma == pytest.approx(_TOTAL_RESIDUAL_SD_C, abs=1e-9)


def test_flag_on_f_city_unit_conversion():
    """F-settled cities: degC effective_bias_c AND σ must be ×1.8 (members carry settlement unit)."""
    conn = _make_db_with_edli_row()
    city = _make_city(settlement_unit="F")
    p1, p2, p3 = _patch_dv("src.engine.monitor_refresh")
    with patch("src.engine.monitor_refresh.settings", new=_FF_ON), p1, p2, p3:
        out = _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC)
    shift, sigma = out
    assert shift == pytest.approx(_EFF_BIAS_C * 1.8, abs=1e-9)
    assert sigma == pytest.approx(_TOTAL_RESIDUAL_SD_C * 1.8, abs=1e-9)


def test_flag_on_weight_live_zero_fails_closed():
    """weight_live<=0 → not a promoted correction → resolver returns None (fail-closed)."""
    conn = _make_db_with_edli_row(weight_live=0.0)
    city = _make_city()
    p1, p2, p3 = _patch_dv("src.engine.monitor_refresh")
    with patch("src.engine.monitor_refresh.settings", new=_FF_ON), p1, p2, p3:
        out = _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC)
    assert out is None


# ── D2 residual-widening gate (2026-07-24): the SHIFT must never ship without the σ ──
# The D2 under-shrink was the unified path applying the bias SHIFT while silently DROPPING
# the total_residual_sd_c widening the reactor entry folds. These pin the restored fold.

def test_widening_present_shift_carries_sigma():
    """Cell WITH a stamped widening term → resolver returns (shift, σ>0). The regression
    was σ≡0 here; the fix reads max(total, residual) native-scaled from the SAME row."""
    conn = _make_db_with_edli_row(residual_sd_c=1.2, total_residual_sd_c=1.7)
    city = _make_city(settlement_unit="C")
    p1, p2, p3 = _patch_dv("src.engine.evaluator")
    with patch("src.engine.evaluator.settings", new=_FF_ON), p1, p2, p3:
        out = _resolve_unified_entry_bias_native(conn, city, _TARGET.isoformat(), _METRIC)
    shift, sigma = out
    assert shift == pytest.approx(_EFF_BIAS_C, abs=1e-9)
    assert sigma == pytest.approx(1.7, abs=1e-9), "prefers total_residual_sd_c when it exceeds residual"


def test_no_widening_row_shift_only_unify_intact():
    """Cell WITHOUT any widening term (both σ NULL) → bias SHIFT still applied, σ=0.0.
    Proves the unify path stays intact (shift-only) for a σ-less row — the reactor's own
    byte-identical-MC outcome — rather than fail-closing the whole treatment."""
    conn = _make_db_with_edli_row(residual_sd_c=None, total_residual_sd_c=None)
    city = _make_city(settlement_unit="C")
    p1, p2, p3 = _patch_dv("src.engine.monitor_refresh")
    with patch("src.engine.monitor_refresh.settings", new=_FF_ON), p1, p2, p3:
        out = _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC)
    assert out is not None, "a σ-less row must still apply the bias shift (unify intact)"
    shift, sigma = out
    assert shift == pytest.approx(_EFF_BIAS_C, abs=1e-9)
    assert sigma == 0.0, "no stamped σ → no widening (shift-only), never a fabricated one"


def test_widening_falls_back_to_residual_sd_c_for_pre_89_rows():
    """total_residual_sd_c NULL (pre-#89 row) → σ falls back to residual_sd_c, preserving
    today's behaviour exactly for legacy rows."""
    conn = _make_db_with_edli_row(residual_sd_c=1.2, total_residual_sd_c=None)
    city = _make_city(settlement_unit="C")
    p1, p2, p3 = _patch_dv("src.engine.evaluator")
    with patch("src.engine.evaluator.settings", new=_FF_ON), p1, p2, p3:
        out = _resolve_unified_entry_bias_native(conn, city, _TARGET.isoformat(), _METRIC)
    shift, sigma = out
    assert sigma == pytest.approx(1.2, abs=1e-9)


def test_read_error_fails_closed_to_none():
    """A read error inside the resolver → None (fail-closed to plain p_raw + real Platt)."""
    conn = _make_db_with_edli_row()
    city = _make_city()
    p1, p2, p3 = _patch_dv("src.engine.monitor_refresh")
    with patch("src.engine.monitor_refresh.settings", new=_FF_ON), p1, p2, p3, \
         patch("src.engine.monitor_refresh.read_bias_model", side_effect=RuntimeError("db boom")):
        out = _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC)
    assert out is None


# ── honest_residual_sigma_native: the shared pure σ-selector (three-site lockstep) ──

class _Row(dict):
    """Minimal sqlite3.Row stand-in: keys() + __getitem__ over a plain dict."""


def test_helper_prefers_total_and_floors_at_residual():
    # total > residual → total; total < residual → floored up to residual.
    assert honest_residual_sigma_native(_Row(total_residual_sd_c=1.7, residual_sd_c=1.2), "C") == pytest.approx(1.7)
    assert honest_residual_sigma_native(_Row(total_residual_sd_c=0.8, residual_sd_c=1.2), "C") == pytest.approx(1.2)


def test_helper_zero_when_no_usable_sigma():
    assert honest_residual_sigma_native(_Row(total_residual_sd_c=None, residual_sd_c=None), "C") == 0.0
    assert honest_residual_sigma_native(_Row(total_residual_sd_c=0.0, residual_sd_c=-1.0), "C") == 0.0


def test_helper_scales_f_cities():
    assert honest_residual_sigma_native(_Row(total_residual_sd_c=1.3, residual_sd_c=1.2), "F") == pytest.approx(1.3 * 1.8)


# ── invariant (c): entry/exit family + shift CONSISTENCY (cross-module) ────────

def test_entry_exit_resolve_same_family():
    """Cross-module invariant: BOTH resolvers read event_reactor_adapter._EDLI_BIAS_FAMILY.
    Proven structurally: a DB whose ONLY edli rows are under _EDLI_BIAS_FAMILY yields a hit
    on both; a DB renamed to a different family yields a miss on both."""
    conn = _make_db_with_edli_row()
    city = _make_city(settlement_unit="C")
    pe = _patch_dv("src.engine.evaluator")
    px = _patch_dv("src.engine.monitor_refresh")
    with patch("src.engine.evaluator.settings", new=_FF_ON), pe[0], pe[1], pe[2]:
        entry = _resolve_unified_entry_bias_native(conn, city, _TARGET.isoformat(), _METRIC)
    with patch("src.engine.monitor_refresh.settings", new=_FF_ON), px[0], px[1], px[2]:
        exit_ = _resolve_unified_exit_bias_native(conn, city, _TARGET, _METRIC)
    assert entry is not None and exit_ is not None
    assert entry[0] == pytest.approx(exit_[0], abs=1e-12), (
        "entry and exit must apply the SAME bias shift (one consistent treatment)"
    )
    assert entry[1] == pytest.approx(exit_[1], abs=1e-12), (
        "entry and exit must apply the SAME residual widening (one consistent treatment)"
    )


def test_constant_is_the_populated_family():
    """The constant the resolvers reuse is the populated VERIFIED family, not the dead one."""
    assert _EDLI_BIAS_FAMILY == "edli_per_city_v1"
    assert _EDLI_BIAS_FAMILY != "full_transport_v1"
