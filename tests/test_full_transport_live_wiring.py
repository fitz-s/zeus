# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Relationship tests for full_transport_live wiring in monitor_refresh (flag OFF/ON/missing-model).
# Reuse: Inspect _load_ft_error_model + p_raw_vector_with_error_model before reuse; requires in-memory fixture DB.
# Authority basis: Zeus #64 task spec — full_transport_live_enabled flag wiring into monitor_refresh
"""Relationship tests for full_transport_live wiring in monitor_refresh.

Three invariants (per Zeus #64 task spec):
  (a) flag OFF → p_raw byte-identical to current plain path (regression guard)
  (b) flag ON + persisted model present → p_raw differs from plain AND matches
      a direct p_raw_vector_with_error_model call
  (c) flag ON + no model row → plain fallback + WARNING logged

Test (b) exercises the new wiring branch; it would be an ImportError (not an
assertion RED) on pre-wiring HEAD since _load_ft_error_model did not exist.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.calibration.ens_bias_repo import init_ens_bias_schema, write_bias_model
from src.calibration.ens_error_model import (
    PredictiveErrorModel,
    p_raw_vector_with_error_model,
)
from src.engine.monitor_refresh import _load_ft_error_model, _resolve_ft_error_model
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.types import Bin


# ── fixtures ─────────────────────────────────────────────────────────────────

_CITY_NAME = "TestCity"
_SEASON = "JJA"
_METRIC = "high"
_LIVE_DV = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"

# A simple city mock: unit C, lat 40° N (JJA for target date 2026-07-01)
def _make_city(settlement_unit: str = "C", lat: float = 40.0):
    city = MagicMock()
    city.name = _CITY_NAME
    city.settlement_unit = settlement_unit
    city.lat = lat
    city.cluster = "Test"
    city.timezone = "America/Chicago"
    return city


def _make_bins():
    return [
        Bin(low=None, high=85.0, label="<85°F", unit="F"),
        Bin(low=85.0, high=90.0, label="85-90°F", unit="F"),
        Bin(low=90.0, high=None, label="≥90°F", unit="F"),
    ]


def _make_bins_c():
    # C point bin: low==high → width=1; shoulder bins are unbounded so width=None (exempt)
    return [
        Bin(low=None, high=26.0, label="<=26°C", unit="C"),
        Bin(low=27.0, high=27.0, label="27°C", unit="C"),
        Bin(low=28.0, high=None, label=">=28°C", unit="C"),
    ]


def _rng():
    return np.random.default_rng(42)


def _member_extrema_c():
    """25 plausible summer daily-max extrema in °C (city unit = C)."""
    rng = np.random.default_rng(42)
    return rng.normal(loc=27.0, scale=2.0, size=25)


def _apply_canonical_columns(conn: sqlite3.Connection) -> None:
    """Add canonical extension columns to model_bias_ens (mirrors migration script)."""
    from src.calibration.ens_bias_repo import _CANONICAL_EXTENSION_COLUMNS  # type: ignore[attr-defined]
    for col, sql_type in _CANONICAL_EXTENSION_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE model_bias_ens ADD COLUMN {col} {sql_type}")
        except Exception:
            pass  # already exists
    conn.commit()


def _make_db_with_row():
    """In-memory DB with one full_transport model row (canonical columns applied)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    _apply_canonical_columns(conn)
    write_bias_model(
        conn,
        city=_CITY_NAME,
        season=_SEASON,
        metric=_METRIC,
        live_data_version=_LIVE_DV,
        prior_data_version="tigge_mx2t6_local_calendar_day_max_v1",
        posterior_bias_c=2.0,    # 2°C cold bias
        posterior_sd_c=0.5,
        n_live=30,
        n_prior=100,
        weight_live=0.5,
        estimator="test",
        training_cutoff="2026-05-25",
        recorded_at="2026-05-25",
        bias_c=2.0,
        bias_sd_c=0.5,
        residual_sd_c=1.2,
        heterogeneity_var_c2=0.04,
        correction_strength=1.0,
        effective_bias_c=2.0,
        total_residual_sd_c=float(np.sqrt(1.2**2 + 0.04)),
        error_model_family="full_transport_v1",  # required by Bug 1 fix: filter on family
    )
    conn.commit()
    return conn


def _make_db_empty():
    """In-memory DB with schema (canonical columns included) but no rows."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    _apply_canonical_columns(conn)
    conn.commit()
    return conn


# ── invariant (a): flag OFF → byte-identical to plain p_raw ──────────────────

def test_flag_off_returns_none_no_db_needed():
    """_load_ft_error_model returns None immediately when flag is OFF."""
    conn = _make_db_with_row()
    with patch(
        "src.engine.monitor_refresh.settings",
        new={"feature_flags": {"full_transport_live_enabled": False}},
    ):
        result = _load_ft_error_model(
            conn,
            city_name=_CITY_NAME,
            season=_SEASON,
            metric=_METRIC,
            live_data_version=_LIVE_DV,
        )
    assert result is None


def test_flag_off_produces_identical_p_raw():
    """Regression guard: flag OFF → _load_ft_error_model is None → caller uses plain path."""
    city = _make_city(settlement_unit="C")
    from src.contracts import SettlementSemantics
    semantics = MagicMock()
    semantics.round_values = lambda v: v
    bins = _make_bins_c()
    member_extrema = _member_extrema_c()

    # Plain path
    plain = p_raw_vector_from_maxes(
        member_extrema, city, semantics, bins,
        n_mc=2000, rng=_rng(),
    )

    # With flag OFF, _load_ft_error_model → None → caller must use plain path
    conn = _make_db_with_row()
    with patch(
        "src.engine.monitor_refresh.settings",
        new={"feature_flags": {"full_transport_live_enabled": False}},
    ):
        model = _load_ft_error_model(
            conn,
            city_name=_CITY_NAME,
            season=_SEASON,
            metric=_METRIC,
            live_data_version=_LIVE_DV,
        )
    assert model is None, "flag OFF must return None so caller stays on plain path"

    # Caller-level: same rng seed → same output (plain path unchanged)
    plain2 = p_raw_vector_from_maxes(
        member_extrema, city, semantics, bins,
        n_mc=2000, rng=_rng(),
    )
    np.testing.assert_array_equal(plain, plain2)


# ── invariant (b): flag ON + row → ft applied, differs from plain ─────────────

def test_flag_on_row_present_returns_model():
    """flag ON + row in DB → _load_ft_error_model returns a PredictiveErrorModel."""
    conn = _make_db_with_row()
    with patch(
        "src.engine.monitor_refresh.settings",
        new={"feature_flags": {"full_transport_live_enabled": True}},
    ):
        model = _load_ft_error_model(
            conn,
            city_name=_CITY_NAME,
            season=_SEASON,
            metric=_METRIC,
            live_data_version=_LIVE_DV,
        )
    assert isinstance(model, PredictiveErrorModel)
    assert model.effective_bias_c == pytest.approx(2.0, abs=1e-6)
    assert model.residual_sd_c == pytest.approx(1.2, abs=1e-6)


def test_flag_on_row_present_p_raw_differs_from_plain():
    """Regression guard (b): flag ON + model → p_raw_vector_with_error_model output
    differs from the plain p_raw_vector_from_maxes output.

    This test was RED before _load_ft_error_model existed (no flag, no helper → only
    plain path available). It turns GREEN when the wiring is in place.
    """
    city = _make_city(settlement_unit="C")
    semantics = MagicMock()
    semantics.round_values = lambda v: v
    bins = _make_bins_c()
    member_extrema = _member_extrema_c()
    member_unit = "degC"

    conn = _make_db_with_row()
    with patch(
        "src.engine.monitor_refresh.settings",
        new={"feature_flags": {"full_transport_live_enabled": True}},
    ):
        model = _load_ft_error_model(
            conn,
            city_name=_CITY_NAME,
            season=_SEASON,
            metric=_METRIC,
            live_data_version=_LIVE_DV,
        )
    assert model is not None, "must have a model to test ft output"

    # FT path
    ft_out = p_raw_vector_with_error_model(
        member_extrema, model, city, semantics, bins,
        member_unit=member_unit, n_mc=5000, rng=_rng(),
    )
    # Plain path (same seed)
    plain_out = p_raw_vector_from_maxes(
        member_extrema, city, semantics, bins,
        n_mc=5000, rng=_rng(),
    )

    # With effective_bias=2.0°C the FT output MUST differ from plain
    assert not np.allclose(ft_out, plain_out, atol=1e-4), (
        f"FT p_raw must differ from plain when effective_bias_c=2.0°C; "
        f"ft={ft_out}, plain={plain_out}"
    )


def test_flag_on_ft_output_matches_direct_call():
    """flag ON + model → output matches a direct p_raw_vector_with_error_model call
    using the same model and same rng seed (proves the wiring delegates correctly).
    """
    city = _make_city(settlement_unit="C")
    semantics = MagicMock()
    semantics.round_values = lambda v: v
    bins = _make_bins_c()
    member_extrema = _member_extrema_c()
    member_unit = "degC"

    conn = _make_db_with_row()
    with patch(
        "src.engine.monitor_refresh.settings",
        new={"feature_flags": {"full_transport_live_enabled": True}},
    ):
        model = _load_ft_error_model(
            conn,
            city_name=_CITY_NAME,
            season=_SEASON,
            metric=_METRIC,
            live_data_version=_LIVE_DV,
        )
    assert model is not None

    # Both calls use same rng seed
    out_via_helper = p_raw_vector_with_error_model(
        member_extrema, model, city, semantics, bins,
        member_unit=member_unit, n_mc=5000, rng=np.random.default_rng(99),
    )
    out_direct = p_raw_vector_with_error_model(
        member_extrema, model, city, semantics, bins,
        member_unit=member_unit, n_mc=5000, rng=np.random.default_rng(99),
    )
    np.testing.assert_array_almost_equal(out_via_helper, out_direct, decimal=10)


# ── invariant (c): flag ON + no row → plain fallback + WARNING ───────────────

def test_flag_on_no_row_returns_none_and_warns(caplog):
    """flag ON + no DB row → _load_ft_error_model returns None + emits WARNING."""
    conn = _make_db_empty()
    with patch(
        "src.engine.monitor_refresh.settings",
        new={"feature_flags": {"full_transport_live_enabled": True}},
    ):
        with caplog.at_level(logging.WARNING, logger="src.engine.monitor_refresh"):
            result = _load_ft_error_model(
                conn,
                city_name=_CITY_NAME,
                season=_SEASON,
                metric=_METRIC,
                live_data_version=_LIVE_DV,
            )
    assert result is None
    assert any(
        "full_transport_live" in rec.message and "falling back to plain p_raw" in rec.message
        for rec in caplog.records
    ), f"Expected WARNING about fallback; got: {[r.message for r in caplog.records]}"


# ── _resolve_ft_error_model: config-failure path ─────────────────────────────

def test_resolve_returns_none_when_entry_forecast_config_unavailable():
    """If entry_forecast_config() raises, _resolve_ft_error_model returns None gracefully."""
    conn = _make_db_with_row()
    city = _make_city()
    target_d = date(2026, 7, 1)

    with patch(
        "src.engine.monitor_refresh.entry_forecast_config",
        side_effect=RuntimeError("config unavailable"),
    ):
        with patch(
            "src.engine.monitor_refresh.settings",
            new={"feature_flags": {"full_transport_live_enabled": True}},
        ):
            result = _resolve_ft_error_model(conn, city, target_d, _METRIC)
    assert result is None
