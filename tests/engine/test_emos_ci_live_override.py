# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS-CI LIVE WIRING (Option B, /tmp/design_emos_ci.md §6);
#   event_reactor_adapter._maybe_override_lcb_with_emos_ci; operator CI-honesty law.
"""RED→GREEN tests for the EMOS-CI live override (Option B).

The override replaces the live MC q_5pct (lcb_by_direction) with the coverage-honest
EMOS analytic CI for LICENSED HIGH-metric cities only, gated on
edli_v1.edli_emos_ci_live_enabled (default OFF). DEFAULT OFF — no live decision change.

Cases (per task spec §5):
  (a) flag OFF → lcb_by_direction BYTE-IDENTICAL to the MC values (override never runs)
  (b) flag ON + city licensed + emos cell → lcb == emos_q_lcb(k_cov) for that city
  (c) flag ON + city NOT in license → MC lcb unchanged
  (d) flag ON + licensed city WITHOUT an emos cell → MC lcb (fail-closed)
  (e) buy_no override is INDEPENDENT (not 1 - yes_lcb)
plus the season-pin boot-guard drop-and-warn behavior.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.types.market import Bin
import src.engine.event_reactor_adapter as adapter
import src.calibration.emos as emos_mod
import src.calibration.emos_ci_license as lic_mod


# ---------------------------------------------------------------------------
# fixtures: a minimal family/snapshot/analysis + a licensed EMOS cell
# ---------------------------------------------------------------------------

CITY = "TestCityEmosCI"      # not in runtime_cities → lat defaults to 90.0 (NH)
TARGET_DATE = "2026-07-15"   # JJA in NH
SEASON = "JJA"

# EMOS NGR params: mu = a + b*xbar; sigma = sqrt(exp(c + d*log(S2) + e*lead)).
# Pick params so mu ~ xbar and sigma ~ a couple degrees C, deterministic.
# members (51 around 28°C) → xbar≈28, S2 small.
_PARAMS = [0.0, 1.0, 1.0, 0.0, 0.0]  # mu = xbar; sigma = sqrt(e^1) ≈ 1.6487°C


def _make_emos_table_file(tmp_path, served="emos"):
    p = tmp_path / "emos_calibration.json"
    p.write_text(json.dumps({
        "_meta": {"note": "test"},
        "cells": {f"{CITY}|{SEASON}": {"params": _PARAMS, "n": 1450, "served": served}},
    }))
    return p


def _make_license_file(tmp_path, cities):
    p = tmp_path / "emos_ci_license.json"
    p.write_text(json.dumps({"_meta": {"note": "test"}, "cities": cities}))
    return p


def _make_family(unit="C"):
    # Two candidate bins; widths must satisfy Bin's strict topology
    # (C non-shoulder width=1, F non-shoulder width=2, shoulders open).
    # bin0: open-low shoulder. bin1: a peaked unit bin straddling mu (~28.3°C).
    if unit == "F":
        # °F range bins cover 2 integers: high = low + 1; bin_probability sees [low, high).
        bins = [
            Bin(low=None, high=82.0, unit="F", label="bin0"),
            Bin(low=82.0, high=83.0, unit="F", label="bin1"),  # covers {82,83} ≈ 27.8-28.3°C, peaked
        ]
    else:
        # °C point bins: low == high (width 1). bin_probability_settlement expands
        # [X,X] → [X−0.5, X+0.5) so these produce non-zero mass.
        bins = [
            Bin(low=None, high=28.0, unit="C", label="bin0"),
            Bin(low=29.0, high=29.0, unit="C", label="bin1"),  # point bin at 29°C
        ]
    candidates = [
        SimpleNamespace(condition_id="cond0", bin=bins[0]),
        SimpleNamespace(condition_id="cond1", bin=bins[1]),
    ]
    return SimpleNamespace(
        city=CITY, target_date=TARGET_DATE, metric="high",
        candidates=candidates, family_id="fam-test",
    )


def _make_snapshot(unit="C"):
    if unit == "F":
        members = [82.4] * 25 + [83.0] * 26  # °F, ~28°C
    else:
        members = [28.0] * 25 + [28.6] * 26  # °C
    return {
        "members_json": json.dumps(members),
        "lead_hours": 24,
        "members_unit": "degF" if unit == "F" else "degC",
    }


def _make_analysis(unit="C"):
    return SimpleNamespace(_unit=unit)


def _make_native_costs():
    # native_costs is passed for signature parity; the override does not read it.
    return {}


def _base_lcb():
    # Arbitrary MC lcb values, deliberately distinct from any EMOS value so a no-op
    # is provable byte-for-byte.
    return {
        ("cond0", "buy_yes"): 0.111111,
        ("cond0", "buy_no"): 0.888888,
        ("cond1", "buy_yes"): 0.222222,
        ("cond1", "buy_no"): 0.777777,
    }


@pytest.fixture
def emos_env(tmp_path, monkeypatch):
    """Point emos + license modules at temp files and clear caches."""
    table_path = _make_emos_table_file(tmp_path, served="emos")
    monkeypatch.setattr(emos_mod, "_EMOS_TABLE_PATH", table_path, raising=True)
    monkeypatch.setattr(emos_mod, "_emos_table_cache", None, raising=False)
    # license path
    lic_path = tmp_path / "emos_ci_license.json"
    monkeypatch.setattr(lic_mod, "_LICENSE_PATH", lic_path, raising=True)
    lic_mod.reset_emos_ci_license_cache()
    yield SimpleNamespace(table_path=table_path, lic_path=lic_path, tmp_path=tmp_path)
    lic_mod.reset_emos_ci_license_cache()
    monkeypatch.setattr(emos_mod, "_emos_table_cache", None, raising=False)


def _set_flag(monkeypatch, enabled: bool):
    """Patch settings._data['edli_v1'] with the live flag, monkeypatch-restored.

    settings['edli_v1'] returns settings._data['edli_v1'] (a plain dict). We replace
    that dict with a shallow copy carrying the flag so the original config is untouched
    and monkeypatch restores it after the test.
    """
    from src.config import settings
    edli = dict(settings._data["edli_v1"])
    edli["edli_emos_ci_live_enabled"] = enabled
    monkeypatch.setitem(settings._data, "edli_v1", edli)


def _expected_emos_lcb(unit, k_cov):
    """Compute the expected (buy_yes, buy_no) EMOS lcb per bin, mirroring the helper.

    Uses bin_probability_settlement to match the live override path.
    """
    from src.calibration.emos import emos_predictive, bin_probability_settlement
    if unit == "F":
        members = np.array([82.4] * 25 + [83.0] * 26, dtype=float)
        members_c = (members - 32.0) * 5.0 / 9.0
    else:
        members_c = np.array([28.0] * 25 + [28.6] * 26, dtype=float)
    mu_c, sigma_c = emos_predictive(CITY, SEASON, 1.0, members_c)
    out = {}
    fam = _make_family(unit)
    for cand in fam.candidates:
        b = cand.bin
        if unit == "F":
            mu_n = mu_c * 9.0 / 5.0 + 32.0
            sig_n = sigma_c * 9.0 / 5.0
        else:
            mu_n, sig_n = mu_c, sigma_c
        emos_q = bin_probability_settlement(mu_n, sig_n, b.low, b.high)
        q_inf = bin_probability_settlement(mu_n, k_cov * sig_n, b.low, b.high)
        out[(cand.condition_id, "buy_yes")] = min(emos_q, q_inf)
        out[(cand.condition_id, "buy_no")] = min(1.0 - emos_q, 1.0 - q_inf)
    return out


def _run_override(monkeypatch, unit="C"):
    lcb = _base_lcb()
    adapter._maybe_override_lcb_with_emos_ci(
        family=_make_family(unit),
        snapshot=_make_snapshot(unit),
        analysis=_make_analysis(unit),
        native_costs=_make_native_costs(),
        payload={},
        lcb_by_direction=lcb,
    )
    return lcb


# ---------------------------------------------------------------------------
# (a) flag OFF → byte-identical
# ---------------------------------------------------------------------------

class TestFlagOffByteIdentical:
    def test_flag_off_lcb_byte_identical(self, emos_env, monkeypatch):
        """Flag OFF: override never runs; lcb_by_direction is unchanged byte-for-byte."""
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 1.5}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, False)

        before = copy.deepcopy(_base_lcb())
        after = _run_override(monkeypatch, unit="C")
        assert after == before, f"flag OFF mutated lcb: before={before} after={after}"

    def test_flag_off_even_with_license_and_cell(self, emos_env, monkeypatch):
        """Even with a valid license AND emos cell, flag OFF must be a no-op."""
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 2.0}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, False)
        after = _run_override(monkeypatch, unit="C")
        assert after == _base_lcb()


# ---------------------------------------------------------------------------
# (b) flag ON + city licensed + emos cell → lcb == emos_q_lcb(k_cov)
# ---------------------------------------------------------------------------

class TestFlagOnLicensedOverrides:
    @pytest.mark.parametrize("k_cov", [1.0, 1.5, 2.5])
    @pytest.mark.parametrize("unit", ["C", "F"])
    def test_override_equals_emos_q_lcb(self, emos_env, monkeypatch, k_cov, unit):
        """Licensed + flag ON + emos cell: every lcb key == emos_q_lcb(k_cov)."""
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": k_cov}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        after = _run_override(monkeypatch, unit=unit)
        expected = _expected_emos_lcb(unit, k_cov)
        for key, exp in expected.items():
            assert key in after
            assert abs(after[key] - exp) < 1e-12, (
                f"key={key} unit={unit} k_cov={k_cov}: got {after[key]}, expected {exp}"
            )

    def test_k_cov_clamped_below_1(self, emos_env, monkeypatch):
        """A license k_cov < 1.0 is clamped to 1.0 (never tightens sigma)."""
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 0.5}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        after = _run_override(monkeypatch, unit="C")
        expected = _expected_emos_lcb("C", 1.0)  # clamped to 1.0
        for key, exp in expected.items():
            assert abs(after[key] - exp) < 1e-12, f"k_cov<1 not clamped: {key}"


# ---------------------------------------------------------------------------
# (c) flag ON + city NOT in license → MC lcb unchanged
# ---------------------------------------------------------------------------

class TestFlagOnUnlicensedCityUnchanged:
    def test_unlicensed_city_no_override(self, emos_env, monkeypatch):
        """City absent from the license file: MC lcb stands even with flag ON + emos cell."""
        _make_license_file(emos_env.tmp_path, {"SomeOtherCity": {"k_cov": 1.5}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        after = _run_override(monkeypatch, unit="C")
        assert after == _base_lcb(), f"unlicensed city was overridden: {after}"

    def test_empty_license_no_override(self, emos_env, monkeypatch):
        """Empty license (no cities): MC lcb stands."""
        _make_license_file(emos_env.tmp_path, {})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)
        after = _run_override(monkeypatch, unit="C")
        assert after == _base_lcb()

    def test_absent_license_file_no_override(self, emos_env, monkeypatch):
        """No license file at all: fail-open empty → MC lcb stands."""
        # emos_env points _LICENSE_PATH at a non-existent file (never created here)
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)
        after = _run_override(monkeypatch, unit="C")
        assert after == _base_lcb()


# ---------------------------------------------------------------------------
# (d) flag ON + licensed city WITHOUT an emos cell → MC lcb (fail-closed)
# ---------------------------------------------------------------------------

class TestFlagOnLicensedNoEmosCell:
    def test_served_raw_cell_fail_closed(self, emos_env, monkeypatch):
        """Licensed city but emos cell served='raw' → emos_predictive None → MC lcb stands."""
        # Overwrite the table with served='raw' for this city
        emos_env.table_path.write_text(json.dumps({
            "_meta": {}, "cells": {f"{CITY}|{SEASON}": {"params": _PARAMS, "n": 1450, "served": "raw"}},
        }))
        monkeypatch.setattr(emos_mod, "_emos_table_cache", None, raising=False)
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 1.5}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        after = _run_override(monkeypatch, unit="C")
        assert after == _base_lcb(), f"served=raw should fail-closed: {after}"

    def test_missing_cell_fail_closed(self, emos_env, monkeypatch):
        """Licensed city but NO emos cell for the season → MC lcb stands."""
        emos_env.table_path.write_text(json.dumps({"_meta": {}, "cells": {}}))
        monkeypatch.setattr(emos_mod, "_emos_table_cache", None, raising=False)
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 1.5}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        after = _run_override(monkeypatch, unit="C")
        assert after == _base_lcb()

    def test_non_high_metric_fail_closed(self, emos_env, monkeypatch):
        """Licensed city with a valid emos cell but family.metric != 'high' → MC lcb stands."""
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 1.5}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        lcb = _base_lcb()
        fam = _make_family("C")
        fam.metric = "low"  # not high
        adapter._maybe_override_lcb_with_emos_ci(
            family=fam, snapshot=_make_snapshot("C"), analysis=_make_analysis("C"),
            native_costs={}, payload={}, lcb_by_direction=lcb,
        )
        assert lcb == _base_lcb(), f"non-high metric should fail-closed: {lcb}"


# ---------------------------------------------------------------------------
# (e) buy_no override is INDEPENDENT (not 1 - yes_lcb)
# ---------------------------------------------------------------------------

class TestBuyNoIndependent:
    def test_buy_no_is_not_one_minus_yes_lcb(self, emos_env, monkeypatch):
        """buy_no lcb must be the honest NO-mass lower bound, NOT 1 - yes_lcb (#106).

        With k_cov > 1 on a peaked bin: yes_lcb = min(emos_q, q_inf) = q_inf (< emos_q),
        but buy_no lcb = min(1-emos_q, 1-q_inf) = 1-emos_q (since q_inf < emos_q).
        Therefore buy_no lcb != 1 - yes_lcb whenever emos_q != q_inf.
        """
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 2.5}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        # Use °F bins: bin1 [82,83) straddles mu≈82.5°F → peaked, non-degenerate mass.
        after = _run_override(monkeypatch, unit="F")
        yes_lcb = after[("cond1", "buy_yes")]
        no_lcb = after[("cond1", "buy_no")]
        naive_complement = 1.0 - yes_lcb
        assert abs(no_lcb - naive_complement) > 1e-6, (
            f"buy_no lcb={no_lcb} equals naive 1-yes_lcb={naive_complement} — NOT independent"
        )
        # And it must equal the honest construction 1 - max(emos_q, q_inf)
        expected = _expected_emos_lcb("F", 2.5)
        assert abs(no_lcb - expected[("cond1", "buy_no")]) < 1e-12

    def test_buy_no_never_optimistic(self, emos_env, monkeypatch):
        """The NO lcb must never exceed the k=1 NO mass (never optimistic for NO)."""
        from src.calibration.emos import emos_predictive, bin_probability
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 3.0}})
        lic_mod.reset_emos_ci_license_cache()
        _set_flag(monkeypatch, True)

        after = _run_override(monkeypatch, unit="F")
        members = np.array([82.4] * 25 + [83.0] * 26, dtype=float)
        members_c = (members - 32.0) * 5.0 / 9.0
        mu_c, sigma_c = emos_predictive(CITY, SEASON, 1.0, members_c)
        mu_n = mu_c * 9.0 / 5.0 + 32.0
        sig_n = sigma_c * 9.0 / 5.0
        for cand in _make_family("F").candidates:
            b = cand.bin
            emos_q = bin_probability(mu_n, sig_n, b.low, b.high)
            no_k1 = 1.0 - emos_q
            assert after[(cand.condition_id, "buy_no")] <= no_k1 + 1e-12, (
                f"NO lcb optimistic for {cand.condition_id}"
            )


# ---------------------------------------------------------------------------
# season-pin boot guard: drop uncovered licensed cities, WARN, keep daemon up
# ---------------------------------------------------------------------------

class TestSeasonPinBootGuard:
    def test_uncovered_city_dropped_from_effective_license(self, emos_env, monkeypatch):
        """Boot guard drops a licensed city lacking an served==emos cell for today's season."""
        from src.main import _assert_emos_ci_license_seasonal_coverage
        # Table has NO cell for this city/season (empty cells) → uncovered.
        emos_env.table_path.write_text(json.dumps({"_meta": {}, "cells": {}}))
        monkeypatch.setattr(emos_mod, "_emos_table_cache", None, raising=False)
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 1.5}})
        lic_mod.reset_emos_ci_license_cache()

        edli_cfg = {"edli_emos_ci_live_enabled": True}
        _assert_emos_ci_license_seasonal_coverage(edli_cfg)  # must not raise
        # The cached license is mutated in place — uncovered city dropped.
        assert lic_mod.load_emos_ci_license() == {}, "uncovered city not dropped"

    def test_guard_noop_when_flag_off(self, emos_env, monkeypatch):
        """Flag OFF: boot guard is a no-op; license cache untouched."""
        from src.main import _assert_emos_ci_license_seasonal_coverage
        _make_license_file(emos_env.tmp_path, {CITY: {"k_cov": 1.5}})
        lic_mod.reset_emos_ci_license_cache()
        before = dict(lic_mod.load_emos_ci_license())
        _assert_emos_ci_license_seasonal_coverage({"edli_emos_ci_live_enabled": False})
        assert lic_mod.load_emos_ci_license() == before
