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


def test_settlement_sigma_floor_required_missing_artifact_raises(monkeypatch, tmp_path):
    importlib.reload(emos_mod)
    missing = tmp_path / "missing-settlement_sigma_floor.json"
    monkeypatch.setattr(emos_mod, "_SIGMA_FLOOR_PATH", missing, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", None, raising=False)

    assert emos_mod.settlement_sigma_floor("C", "JJA", "high") is None
    with pytest.raises(emos_mod.SettlementSigmaFloorError, match="MISSING_ARTIFACT"):
        emos_mod.settlement_sigma_floor("C", "JJA", "high", required=True)


def test_settlement_sigma_floor_required_malformed_artifact_raises(monkeypatch, tmp_path):
    importlib.reload(emos_mod)
    p = tmp_path / "settlement_sigma_floor.json"
    p.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(emos_mod, "_SIGMA_FLOOR_PATH", p, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", None, raising=False)

    assert emos_mod.settlement_sigma_floor("C", "JJA", "high") is None
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", None, raising=False)
    with pytest.raises(emos_mod.SettlementSigmaFloorError, match="MALFORMED_ARTIFACT"):
        emos_mod.settlement_sigma_floor("C", "JJA", "high", required=True)


def test_settlement_sigma_floor_required_missing_cell_raises(floor_table):
    assert emos_mod.settlement_sigma_floor("Tel Aviv", "JJA", "low") is None
    with pytest.raises(emos_mod.SettlementSigmaFloorError, match="MISSING_CELL:Tel Aviv\\|JJA\\|low"):
        emos_mod.settlement_sigma_floor("Tel Aviv", "JJA", "low", required=True)


def test_settlement_sigma_floor_required_non_positive_floor_raises(monkeypatch):
    table = {
        "_meta": {"k_default": 0.8},
        "cells": {"X|JJA|high": {"sigma_floor_c": 0.0, "n": 20, "window": "w"}},
    }
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", table, raising=False)

    assert emos_mod.settlement_sigma_floor("X", "JJA", "high") is None
    with pytest.raises(emos_mod.SettlementSigmaFloorError, match="NON_POSITIVE"):
        emos_mod.settlement_sigma_floor("X", "JJA", "high", required=True)


# ----------------------------------------------------------------------------
# ITEM 3 — PATH PROVENANCE. _SIGMA_FLOOR_PATH must resolve to the SAME runtime live
#   state dir the rest of the daemon uses (src.config.state_path / STATE_DIR), NOT a
#   module-local recomputed dir. Provenance: the loader must reuse the single canonical
#   resolver so a future state-dir relocation moves the floor file with the daemon. And
#   it must FAIL-LOUD (warn) when the floor file is absent rather than silently returning
#   0 cells (which makes the q_lcb floor inert).
# ----------------------------------------------------------------------------
def test_sigma_floor_path_resolves_via_canonical_state_resolver():
    # The module's floor path MUST equal the canonical runtime state path, proving the
    # loader reuses src.config.state_path (the daemon's single state-dir resolver) and is
    # NOT a worktree-relative recomputed dir that silently diverges from runtime state.
    importlib.reload(emos_mod)
    from src.config import state_path as canonical_state_path

    expected = canonical_state_path("settlement_sigma_floor.json")
    assert emos_mod._SIGMA_FLOOR_PATH == expected, (
        "settlement σ-floor path must resolve via the canonical state_path resolver "
        f"(got {emos_mod._SIGMA_FLOOR_PATH}, expected {expected})"
    )
    # The EMOS table path must use the same resolver (one canonical state dir, no parallel
    # path computation) so the provenance fix covers every artifact the module loads.
    assert emos_mod._EMOS_TABLE_PATH == canonical_state_path("emos_calibration.json")


def test_sigma_floor_path_follows_canonical_resolver_relocation(monkeypatch, tmp_path):
    # STRUCTURAL proof of REUSE (not coincidence): if the canonical resolver is repointed,
    # the module's floor path follows it after reload. A module that recomputed its own
    # __file__-relative dir would NOT follow — this test would fail for that implementation.
    import src.config as config_mod

    relocated = tmp_path / "relocated_state"
    monkeypatch.setattr(config_mod, "STATE_DIR", relocated, raising=True)
    importlib.reload(emos_mod)
    try:
        assert emos_mod._SIGMA_FLOOR_PATH == relocated / "settlement_sigma_floor.json", (
            "floor path must follow the canonical resolver's relocation (proves reuse, "
            "not a recomputed __file__-relative path)"
        )
    finally:
        # Restore the real resolver so later tests see the canonical path.
        monkeypatch.undo()
        importlib.reload(emos_mod)


def test_sigma_floor_loader_warns_loud_on_absent_file(monkeypatch, tmp_path, caplog):
    # A MISSING floor file silently returning {} makes the q_lcb floor inert (0 cells).
    # The legacy (required=False) loader must STILL emit a loud WARNING (not a quiet
    # debug) when the artifact is absent so an operator sees the floor is disabled.
    import logging

    importlib.reload(emos_mod)
    missing = tmp_path / "settlement_sigma_floor.json"
    monkeypatch.setattr(emos_mod, "_SIGMA_FLOOR_PATH", missing, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", None, raising=False)

    with caplog.at_level(logging.WARNING, logger=emos_mod.logger.name):
        table = emos_mod.load_sigma_floor_table()
    assert table == {}
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("settlement_sigma_floor" in r.getMessage() for r in warned), (
        "absent σ-floor file must warn LOUD (q_lcb floor would be inert), not log quietly"
    )
