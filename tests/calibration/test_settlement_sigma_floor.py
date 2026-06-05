# Created: 2026-06-05
# Last reused or audited: 2026-06-05
# Authority basis: q=1.000 investigation 2026-06-05; empirical settlement σ-floor, iron rule 5
#   (overconfidence = ruin). Relationship tests for the EMPIRICAL settlement σ-floor loop-breaker:
#   the EMOS σ-model is systemically under-dispersed (median σ_emos/σ_settled = 0.49); the correct
#   floor is the DETRENDED trailing-window settlement std per (city, season, metric), applied
#   UNIVERSALLY as σ_eff = max(model_σ, k·σ_settled_floor), k=0.8. Conservative: max() only WIDENS.
"""RED-first relationship tests for the empirical settlement σ-floor.

Two surfaces:
  1. The OFFLINE detrend math (scripts/fit_settlement_sigma_floor.py:detrended_std):
     proves the residual std of a trending series is the σ used, NOT the raw std — the
     investigation showed the naïve raw same-season std OVER-widens by conflating the
     intra-season warming trend.
  2. The RUNTIME accessor (src.calibration.emos.settlement_sigma_floor): loads
     state/settlement_sigma_floor.json, returns k_default·σ_floor_c (°C) or None when absent,
     cached + thread-safe like load_emos_table.
"""
from __future__ import annotations

import importlib
import json

import numpy as np
import pytest

from src.calibration import emos as emos_mod


# ----------------------------------------------------------------------------
# DETREND MATH — the residual std, not the raw std. A linearly-trending series
#   has a LARGE raw std (driven by the trend) but a SMALL residual std (the
#   day-to-day noise around the trend). The floor must use the residual std.
# ----------------------------------------------------------------------------
def test_detrended_std_recovers_residual_not_raw_std():
    script = importlib.import_module("scripts.fit_settlement_sigma_floor")
    rng = np.random.default_rng(42)
    n = 60
    days = np.arange(n, dtype=float)
    noise_sd = 1.5
    # strong linear warming trend (0.25°C/day) + small day-to-day noise
    trend = 10.0 + 0.25 * days
    noise = rng.normal(0.0, noise_sd, size=n)
    values = trend + noise

    raw_std = float(np.std(values, ddof=1))
    dt_std = script.detrended_std(days, values)

    # The raw std is inflated by the trend (range ~15°C over the window).
    assert raw_std > 4.0, "synthetic trend must inflate the raw std (sanity)"
    # The detrended residual std must recover the noise scale, NOT the inflated raw std.
    assert dt_std == pytest.approx(noise_sd, abs=0.6), (
        f"detrended std {dt_std:.3f} must recover the residual noise ~{noise_sd}, not raw {raw_std:.3f}"
    )
    assert dt_std < raw_std * 0.5, "detrend must materially shrink the trend-inflated raw std"


def test_detrended_std_equals_raw_when_no_trend():
    # No trend -> detrend removes (near) nothing; residual std ≈ raw std.
    script = importlib.import_module("scripts.fit_settlement_sigma_floor")
    rng = np.random.default_rng(7)
    n = 50
    days = np.arange(n, dtype=float)
    values = 20.0 + rng.normal(0.0, 2.0, size=n)
    raw_std = float(np.std(values, ddof=1))
    dt_std = script.detrended_std(days, values)
    assert dt_std == pytest.approx(raw_std, rel=0.12), (
        "with no trend the detrended std must match the raw std (no spurious shrink)"
    )


# ----------------------------------------------------------------------------
# RUNTIME ACCESSOR — settlement_sigma_floor(city, season, metric) returns
#   k_default·σ_floor_c, None when absent, cached.
# ----------------------------------------------------------------------------
@pytest.fixture
def floor_table(monkeypatch):
    table = {
        "_meta": {"created": "2026-06-05", "method": "detrended-45d", "k_default": 0.8},
        "cells": {
            "Tel Aviv|JJA|high": {"sigma_floor_c": 2.9, "n": 30, "window": "45d-cross-season"},
            "Singapore|JJA|low": {"sigma_floor_c": 0.7, "n": 30, "window": "45d-cross-season"},
        },
    }
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", table, raising=False)
    return table


def test_settlement_sigma_floor_returns_k_times_value(floor_table):
    # k_default=0.8, σ_floor_c=2.9 -> 0.8*2.9 = 2.32
    val = emos_mod.settlement_sigma_floor("Tel Aviv", "JJA", "high")
    assert val == pytest.approx(0.8 * 2.9), "must return k_default · sigma_floor_c"


def test_settlement_sigma_floor_none_when_absent(floor_table):
    assert emos_mod.settlement_sigma_floor("Nowhere", "JJA", "high") is None
    # present city but wrong metric -> absent cell -> None (metric-keyed, no crossing)
    assert emos_mod.settlement_sigma_floor("Tel Aviv", "JJA", "low") is None


def test_settlement_sigma_floor_metric_lowercased(floor_table):
    # callers may pass "HIGH"; the cell key lowercases the metric like emos_cell_key.
    assert emos_mod.settlement_sigma_floor("Tel Aviv", "JJA", "HIGH") == pytest.approx(0.8 * 2.9)


def test_settlement_sigma_floor_respects_meta_k(monkeypatch):
    # a different k_default in _meta must be honored.
    table = {
        "_meta": {"k_default": 0.5},
        "cells": {"X|DJF|high": {"sigma_floor_c": 4.0, "n": 20, "window": "w"}},
    }
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", table, raising=False)
    assert emos_mod.settlement_sigma_floor("X", "DJF", "high") == pytest.approx(0.5 * 4.0)


def test_settlement_sigma_floor_cached_loads_once(monkeypatch, tmp_path):
    # Mirrors load_emos_table: the file is read once; the cache satisfies later calls.
    importlib.reload(emos_mod)
    floor_json = {
        "_meta": {"k_default": 0.8},
        "cells": {"C|JJA|high": {"sigma_floor_c": 3.0, "n": 15, "window": "w"}},
    }
    p = tmp_path / "settlement_sigma_floor.json"
    p.write_text(json.dumps(floor_json), encoding="utf-8")
    monkeypatch.setattr(emos_mod, "_SIGMA_FLOOR_PATH", p, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", None, raising=False)

    first = emos_mod.settlement_sigma_floor("C", "JJA", "high")
    assert first == pytest.approx(0.8 * 3.0)
    # delete the file: a cached loader must still answer from cache.
    p.unlink()
    second = emos_mod.settlement_sigma_floor("C", "JJA", "high")
    assert second == pytest.approx(0.8 * 3.0), "loader must cache; second call must not re-read disk"
