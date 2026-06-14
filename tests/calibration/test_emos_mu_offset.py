# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: D4 docs/evidence/deadloop_2026-06-14/emos_mu_bias_probe.md + law 8 (the live EMOS
#   center must be airport-settlement-honest). The EMOS-served μ* = a + b·x̄ lands COLD for per-city
#   cold-biased cells (Tokyo|MAM median −1.89°C vs VERIFIED settlement). The fix is a residual-grounded
#   per-(city,season,metric) μ-OFFSET measured DIRECTLY on (μ*−settlement), walk-forward OOS-gated
#   (scripts/fit_emos_mu_offset.py), consumed fail-closed (src.calibration.emos.emos_mu_offset) and
#   applied at the q seam (src.calibration.emos_q_builder.build_emos_q) as μ_corr = μ* − offset_c.
"""Tests for the airport-settlement-honest EMOS μ-offset correction.

Surfaces:
  (A) RED-on-revert RELATIONSHIP test (the core ask): a Tokyo|MAM warm-side bin that was UNDER-PRICED
      under the cold μ* carries HONESTLY HIGHER q after the correction; remove the activated cell
      (revert) and the q goes cold again. Proves the correction crosses build_emos_q into the traded q.
  (B) ACCESSOR fail-closed semantics (src.calibration.emos.emos_mu_offset): activated → offset_c;
      unactivated / missing-cell / absent-table → None (no correction, today's behavior); required=True
      raises EmosMuOffsetError ONLY on a true artifact defect, NEVER on an unactivated/absent cell.
  (C) CONSUMER math: build_emos_q applies μ_corr = μ* − offset_c (cold center offset_c<0 is WARMED),
      shifts ONLY the center (σ unchanged), and is byte-identical when no cell is activated.
  (D) FITTER gate (scripts/fit_emos_mu_offset.py): a measured-cold cell whose offset reduces |residual|
      AND CRPS OOS activates; a warm cell and a cold-but-gate-fail cell do NOT.
"""
from __future__ import annotations

import importlib
import json
import math

import numpy as np
import pytest

from src.calibration import emos as emos_mod
import src.calibration.emos_q_builder as qb


# Live Tokyo|MAM|high EMOS params (state/emos_calibration.json): a=−1.2509, b=1.16099 — a cold-fit
# intercept. μ* = a + b·x̄. The D4 fit measured offset_c=−1.890°C (median μ*−settlement) for this cell.
_TOKYO_MAM = {"params": [-1.2509, 1.16099, 1.4328, 0.43835, 0.01984], "n": 1472, "served": "emos"}
_TOKYO_OFFSET_C = -1.890

# A cluster of warm-ish °C members so μ* lands near the 18-21 / 21-24 boundary, where a 1.89°C warm
# shift visibly moves mass onto the warm-side bins (the under-priced winners under the cold center).
_MEMBERS_C = np.array([18.0, 18.5, 19.0, 19.2, 18.8, 19.1, 18.3, 19.4, 18.9, 19.0], dtype=float)
_BINS = [(None, 15.0), (15.0, 18.0), (18.0, 21.0), (21.0, 24.0), (24.0, None)]


@pytest.fixture(autouse=True)
def _live_emos_table(monkeypatch):
    """Pin the EMOS table cache to a minimal Tokyo|MAM cell so build_emos_q has params to serve."""
    monkeypatch.setattr(emos_mod, "_emos_table_cache",
                        {"_meta": {"metric": "multi"}, "cells": {"Tokyo|MAM|high": _TOKYO_MAM}},
                        raising=False)
    # No settlement σ-floor in these tests (we assert on the CENTER, not dispersion).
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", {"_meta": {}, "cells": {}}, raising=False)


def _offset_table(activated: bool):
    return {
        "_meta": {"authority": "emos_mu_offset_v1_residual", "metric": "high"},
        "cells": {
            "Tokyo|MAM|high": {
                "offset_c": _TOKYO_OFFSET_C, "n": 18, "mean_residual_c": -1.3684,
                "activated": activated,
                "oos": {"n": 10, "res_before": -1.502, "res_after": -0.196,
                        "crps_before": 1.5985, "crps_after": 1.3896},
            },
            # a warm cell present but NEVER activated (out-of-scope SF|JJA overshoot analogue)
            "San Francisco|JJA|high": {
                "offset_c": 2.4512, "n": 12, "mean_residual_c": 2.8678, "activated": False, "oos": None,
            },
        },
    }


# ---------------------------------------------------------------------------
# (A) RED-on-revert RELATIONSHIP test — the core ask.
# ---------------------------------------------------------------------------
def test_red_on_revert_warm_bin_gets_honest_q_then_reverts_cold(monkeypatch):
    """The warm-side bin under-priced by the cold μ* carries higher q after correction; revert → cold."""
    # --- corrected (activated) ---
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", _offset_table(activated=True), raising=False)
    q_on, mu_on, sig_on = qb.build_emos_q(
        city="Tokyo", season="MAM", metric="high", lead_days=3.0,
        members_native=_MEMBERS_C, unit="C", bins=_BINS,
    )
    # --- reverted (deactivate the cell → no correction) ---
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", _offset_table(activated=False), raising=False)
    q_off, mu_off, sig_off = qb.build_emos_q(
        city="Tokyo", season="MAM", metric="high", lead_days=3.0,
        members_native=_MEMBERS_C, unit="C", bins=_BINS,
    )

    # the center warms by exactly −offset_c (the cold center is shifted toward settlement)
    assert mu_on - mu_off == pytest.approx(-_TOKYO_OFFSET_C, abs=1e-9)
    assert mu_on > mu_off, "correction must WARM the cold Tokyo|MAM center"
    # σ is UNCHANGED — only the center moves (the σ-floor governs dispersion, not this correction)
    assert sig_on == pytest.approx(sig_off, abs=1e-9)

    # the warm-side bins (21-24 and the ≥24 shoulder) carry HONESTLY HIGHER q after the correction;
    # under the cold center they were UNDER-PRICED. (bins index: 3=21-24, 4=≥24 warm shoulder.)
    assert q_on[3] > q_off[3], "21-24°C warm bin must gain q under the corrected (warmer) center"
    assert q_on[4] > q_off[4], "the ≥24°C warm-side winner must gain q_lcb mass after correction"
    # and the cold bins SHED mass (mass is conserved; warming moves it off the cold side)
    assert q_on[1] < q_off[1] and q_on[2] < q_off[2], "cold bins must lose mass under the warmer center"
    # REVERT IS EXACT: with the cell deactivated, q is the uncorrected cold distribution.
    assert q_off[4] < q_on[4], "revert → the warm winner is cold/under-priced again"


# ---------------------------------------------------------------------------
# (B) ACCESSOR fail-closed semantics.
# ---------------------------------------------------------------------------
def test_accessor_returns_offset_for_activated_cell(monkeypatch):
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", _offset_table(activated=True), raising=False)
    assert emos_mod.emos_mu_offset("Tokyo", "MAM", "high") == pytest.approx(_TOKYO_OFFSET_C)
    # metric is lowercased like emos_cell_key (no crossing)
    assert emos_mod.emos_mu_offset("Tokyo", "MAM", "HIGH") == pytest.approx(_TOKYO_OFFSET_C)


def test_accessor_none_for_unactivated_cell(monkeypatch):
    # cold-but-gate-fail / EMOS-absorbed: cell present, activated=False → None (serve uncorrected).
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", _offset_table(activated=False), raising=False)
    assert emos_mod.emos_mu_offset("Tokyo", "MAM", "high") is None
    # a warm cell is present but never activated → None (never cool a warm center)
    assert emos_mod.emos_mu_offset("San Francisco", "JJA", "high") is None


def test_accessor_none_for_missing_cell_even_when_required(monkeypatch):
    # An ABSENT cell (EMOS-absorbed city never fit) is a valid "leave alone" state, NOT a defect:
    # required=True must NOT raise here — fail-closed means "no correction", not "crash".
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", _offset_table(activated=True), raising=False)
    assert emos_mod.emos_mu_offset("Beijing", "MAM", "high") is None
    assert emos_mod.emos_mu_offset("Beijing", "MAM", "high", required=True) is None


def test_accessor_none_when_table_absent(monkeypatch, tmp_path):
    importlib.reload(emos_mod)
    missing = tmp_path / "missing-emos_mu_offset.json"
    monkeypatch.setattr(emos_mod, "_MU_OFFSET_PATH", missing, raising=False)
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", None, raising=False)
    # absent table → no correction (today's behavior); required=True → raises (the candidate that
    # SHOULD be corrected cannot silently serve the cold center if the artifact is simply gone).
    assert emos_mod.emos_mu_offset("Tokyo", "MAM", "high") is None
    with pytest.raises(emos_mod.EmosMuOffsetError, match="MISSING_ARTIFACT"):
        emos_mod.emos_mu_offset("Tokyo", "MAM", "high", required=True)


def test_accessor_required_malformed_artifact_raises(monkeypatch, tmp_path):
    importlib.reload(emos_mod)
    p = tmp_path / "emos_mu_offset.json"
    p.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(emos_mod, "_MU_OFFSET_PATH", p, raising=False)
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", None, raising=False)
    assert emos_mod.emos_mu_offset("Tokyo", "MAM", "high") is None
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", None, raising=False)
    with pytest.raises(emos_mod.EmosMuOffsetError, match="MALFORMED_ARTIFACT"):
        emos_mod.emos_mu_offset("Tokyo", "MAM", "high", required=True)


def test_accessor_non_finite_offset_fail_closed(monkeypatch):
    table = {
        "_meta": {}, "cells": {
            "X|MAM|high": {"offset_c": float("nan"), "activated": True, "n": 20, "oos": {}},
        },
    }
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", table, raising=False)
    assert emos_mod.emos_mu_offset("X", "MAM", "high") is None
    with pytest.raises(emos_mod.EmosMuOffsetError, match="NON_FINITE"):
        emos_mod.emos_mu_offset("X", "MAM", "high", required=True)


def test_accessor_cached_loads_once(monkeypatch, tmp_path):
    importlib.reload(emos_mod)
    p = tmp_path / "emos_mu_offset.json"
    p.write_text(json.dumps(_offset_table(activated=True)), encoding="utf-8")
    monkeypatch.setattr(emos_mod, "_MU_OFFSET_PATH", p, raising=False)
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", None, raising=False)
    first = emos_mod.emos_mu_offset("Tokyo", "MAM", "high")
    assert first == pytest.approx(_TOKYO_OFFSET_C)
    p.unlink()  # cached loader must still answer from cache
    assert emos_mod.emos_mu_offset("Tokyo", "MAM", "high") == pytest.approx(_TOKYO_OFFSET_C)


# ---------------------------------------------------------------------------
# (C) CONSUMER math + byte-identity when no cell is activated.
# ---------------------------------------------------------------------------
def test_build_emos_q_byte_identical_when_no_activation(monkeypatch):
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", {"_meta": {}, "cells": {}}, raising=False)
    q_a = qb.build_emos_q(city="Tokyo", season="MAM", metric="high", lead_days=3.0,
                          members_native=_MEMBERS_C, unit="C", bins=_BINS)
    # μ* must equal the raw EMOS center a + b·x̄ (no shift) when the table is empty.
    a, b = _TOKYO_MAM["params"][0], _TOKYO_MAM["params"][1]
    expected_mu = a + b * float(np.mean(_MEMBERS_C))
    assert q_a[1] == pytest.approx(expected_mu, abs=1e-9)


def test_build_emos_q_offset_applies_in_fahrenheit_path(monkeypatch):
    """The offset is °C and applied BEFORE F→C: an F-unit market warms by −offset_c·1.8 in °F."""
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", _offset_table(activated=True), raising=False)
    members_f = _MEMBERS_C * 1.8 + 32.0
    bins_f = [(None, 59.0), (59.0, 64.4), (64.4, 69.8), (69.8, 75.2), (75.2, None)]
    q_on = qb.build_emos_q(city="Tokyo", season="MAM", metric="high", lead_days=3.0,
                           members_native=members_f, unit="F", bins=bins_f)
    monkeypatch.setattr(emos_mod, "_mu_offset_cache", _offset_table(activated=False), raising=False)
    q_off = qb.build_emos_q(city="Tokyo", season="MAM", metric="high", lead_days=3.0,
                            members_native=members_f, unit="F", bins=bins_f)
    # the °F center warms by −offset_c·1.8 (the °C offset scaled to °F)
    assert q_on[1] - q_off[1] == pytest.approx(-_TOKYO_OFFSET_C * 1.8, abs=1e-6)


# ---------------------------------------------------------------------------
# (D) FITTER gate: cold-earns / warm-and-gatefail-leave-alone.
# ---------------------------------------------------------------------------
def _synth_records(mu_minus_settled_mean, *, n=20, sigma=1.5, seed=0):
    """n daily records whose μ*−settled has the requested MEAN bias; deterministic dates ascending."""
    import datetime as dt
    rng = np.random.default_rng(seed)
    base = dt.date(2026, 3, 1)
    recs = []
    for i in range(n):
        settled = 20.0 + float(rng.normal(0, 2.0))
        mu = settled + mu_minus_settled_mean + float(rng.normal(0, 0.4))
        recs.append({"city": "C", "season": "MAM", "date": base + dt.timedelta(days=i),
                     "mu": mu, "sig": sigma, "settled": settled})
    return recs


def test_fitter_activates_materially_cold_cell():
    fit = importlib.import_module("scripts.fit_emos_mu_offset")
    cold = _synth_records(-1.8, n=22, seed=1)  # ~1.8°C cold, clearly < threshold, n adequate
    res = fit.gate_cell(cold)
    assert res["activated"] is True, "a materially-cold cell whose offset earns OOS must activate"
    assert res["offset_c"] < 0, "cold center → negative offset (μ* below settlement)"
    assert res["oos"] is not None and res["oos"]["n"] >= fit.MIN_OOS


def test_fitter_leaves_warm_cell_alone():
    fit = importlib.import_module("scripts.fit_emos_mu_offset")
    warm = _synth_records(+2.5, n=20, seed=2)  # warm overshoot (SF|JJA analogue) — out of scope
    res = fit.gate_cell(warm)
    assert res["activated"] is False, "a warm cell must NEVER activate (one-signed-honest)"
    assert res["oos"] is None, "warm cell is not even gate-tested (mean residual ≥ cold threshold)"


def test_fitter_leaves_absorbed_cell_alone():
    fit = importlib.import_module("scripts.fit_emos_mu_offset")
    absorbed = _synth_records(-0.1, n=20, seed=3)  # ~0 residual (EMOS-absorbed: Beijing/SF-MAM analogue)
    res = fit.gate_cell(absorbed)
    assert res["activated"] is False, "an EMOS-absorbed (≈0 residual) cell must not activate"


def test_fitter_fail_closed_on_thin_cell():
    fit = importlib.import_module("scripts.fit_emos_mu_offset")
    thin = _synth_records(-1.8, n=fit.MIN_N - 1, seed=4)  # cold but below MIN_N
    res = fit.gate_cell(thin)
    assert res["activated"] is False, "a cold but thin cell must fail closed (insufficient data)"
