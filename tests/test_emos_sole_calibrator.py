# Created: 2026-06-04
# Lifecycle: created=2026-06-04; last_reviewed=2026-06-04; last_reused=2026-06-04
# Purpose: Relationship invariants (INV-1..5) for the ONE-calibrator program (#110 / ELEVATION S2).
#   These lock the cross-module contract that the live traded q is produced by EXACTLY one
#   calibrator (EMOS), that the point q carries the predictive sigma (kills under-dispersion),
#   and that lead-tail skill-loss travels in sigma not the mean. Written RED-first: INV-1/INV-5
#   FAIL until the seam q-builder + maze deletion land (Phase 2/3); INV-2/INV-3 lock the
#   calibrator math and are GREEN now.
# Reuse: update when src/calibration/emos.py, src/calibration/emos_q_builder.py, or the q seam
#   src/engine/event_reactor_adapter.py:_market_analysis_from_event_snapshot change.
# Authority basis: plan compiled-foraging-quail.md (one ensemble->settlement calibrator); the
#   universal-correlation decision (operator 2026-06-04). Models: tests/test_qlcb_coverage_flag_and_armgate.py
#   (flag-OFF==legacy), tests/test_wave3_rt_exit_kelly.py (wire-or-delete).
from __future__ import annotations

import importlib

import numpy as np
import pytest

from src.calibration import emos as emos_mod


# --- synthetic EMOS cell injected so the math invariants are deterministic ---
# params = [a, b, c, d, e]:  mu = a + b*xbar ;  sigma2 = exp(c + d*log(S2) + e*lead_days)
# b=1.0 (no mean stretch), e>0 (sigma grows with lead) — the structural shape.
_SYNTH_CELL = {"params": [0.5, 1.0, 0.0, 1.0, 0.20], "n": 500, "served": "emos"}


@pytest.fixture
def emos_table(monkeypatch):
    table = {"_meta": {"metric": "multi"}, "cells": {"TestCity|JJA|high": dict(_SYNTH_CELL)}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    return table


# ----------------------------------------------------------------------------
# INV-2 — lead-tail skill-loss travels in VARIANCE, not the MEAN.
#   This is the structural cold-shift fix: the maze bakes lead-variance into a
#   lead-agnostic mean (manufacturing the 3-5C artifact); EMOS puts it in sigma.
# ----------------------------------------------------------------------------
def test_inv2_mu_is_lead_invariant_for_fixed_members(emos_table):
    members = np.array([20.0, 21.0, 22.0, 23.0, 24.0], dtype=float)
    mu1, _ = emos_mod.emos_predictive("TestCity", "JJA", lead_days=1.0, members_c=members)
    mu5, _ = emos_mod.emos_predictive("TestCity", "JJA", lead_days=5.0, members_c=members)
    # Same ensemble -> same mean correction regardless of lead. The lead term is sigma-only.
    assert mu1 == pytest.approx(mu5), "mu must not depend on lead (lead belongs in sigma)"


def test_inv2_sigma_monotone_nondecreasing_in_lead(emos_table):
    members = np.array([20.0, 21.0, 22.0, 23.0, 24.0], dtype=float)
    sigmas = [emos_mod.emos_predictive("TestCity", "JJA", lead_days=L, members_c=members)[1]
              for L in (0.0, 1.0, 3.0, 5.0, 7.0)]
    for a, b in zip(sigmas, sigmas[1:]):
        assert b >= a - 1e-9, f"sigma must be non-decreasing in lead, got {sigmas}"
    assert sigmas[-1] > sigmas[0], "sigma must actually widen across the lead range"


# ----------------------------------------------------------------------------
# INV-3 — the POINT distribution carries the predictive sigma.
#   The live maze point q (p_cal) is a deterministic pass with NO predictive sigma
#   (instrument-only) -> over-confident -> the far-OTM buy_no flood. The EMOS point
#   bin-prob integrates N(mu, sigma) so an interior bin gets real, sigma-scaled mass.
# ----------------------------------------------------------------------------
def test_inv3_point_bin_prob_uses_sigma(emos_table):
    # Wider sigma must spread mass off the modal bin (the under-dispersion fix).
    mu = 22.0
    narrow = emos_mod.bin_probability_settlement(mu, 0.5, 22.0, 22.0)   # interior point bin [22,22]
    wide = emos_mod.bin_probability_settlement(mu, 3.0, 22.0, 22.0)
    assert 0.0 < wide < narrow, "wider sigma must reduce modal-bin mass (mass spreads to neighbours)"
    # And an off-modal interior bin gains mass as sigma widens.
    off_narrow = emos_mod.bin_probability_settlement(mu, 0.5, 25.0, 25.0)
    off_wide = emos_mod.bin_probability_settlement(mu, 3.0, 25.0, 25.0)
    assert off_wide > off_narrow, "wider sigma must move mass into off-modal bins"


def test_inv3_interior_point_bin_never_degenerate(emos_table):
    # The settlement preimage expansion (+-0.5) guarantees an interior bin is never zero-width.
    p = emos_mod.bin_probability_settlement(22.3, 1.0, 22.0, 22.0)
    assert p > 0.0, "interior settlement bin must carry non-zero mass for a non-degenerate sigma"


# ----------------------------------------------------------------------------
# INV-1 — SOLE SOURCE. The seam q-builder produces q from EMOS only; flag-OFF is
#   byte-identical to the current path. (RED until Phase 2: emos_q_builder lands.)
# ----------------------------------------------------------------------------
def test_inv1_emos_q_builder_module_exists():
    # RED-first: the dedicated one-calibrator module must exist with the documented seam API.
    mod = importlib.import_module("src.calibration.emos_q_builder")
    assert hasattr(mod, "build_emos_q"), "emos_q_builder.build_emos_q is the single q seam"


def test_inv1_build_emos_q_returns_full_distribution(emos_table):
    mod = importlib.import_module("src.calibration.emos_q_builder")
    # A served=emos cell + valid members -> a normalized per-bin q vector + native sigma,
    # built ONLY from emos_predictive (no bias shift, no separate Platt).
    bins = [(None, 20.0), (21.0, 21.0), (22.0, 22.0), (23.0, 23.0), (24.0, None)]
    out = mod.build_emos_q(
        city="TestCity", season="JJA", metric="high", lead_days=3.0,
        members_native=np.array([20.0, 21.0, 22.0, 23.0, 24.0], dtype=float),
        unit="C", bins=bins,
    )
    assert out is not None, "served=emos cell must produce a distribution"
    q_vec, mu_native, sigma_native = out
    assert len(q_vec) == len(bins)
    assert abs(float(np.sum(q_vec)) - 1.0) < 1e-6, "q vector must be normalized"
    assert sigma_native > 0.0, "the point/lcb sigma must travel with the distribution"
    assert np.isfinite(mu_native), "mu must travel out so the lcb bootstrap can sample N(mu,sigma)"


def test_inv1_served_raw_returns_none_for_honest_fallback(monkeypatch):
    # served=raw cell -> None so the caller uses the honest raw analytic, NOT the bias maze.
    mod = importlib.import_module("src.calibration.emos_q_builder")
    table = {"_meta": {}, "cells": {"RawCity|JJA|high": {"params": [0, 1, 0, 1, 0.2], "n": 99, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    out = mod.build_emos_q(city="RawCity", season="JJA", metric="high", lead_days=3.0,
                           members_native=np.array([20.0, 21.0, 22.0], dtype=float),
                           unit="C", bins=[(None, 21.0), (22.0, None)])
    assert out is None, "served=raw must fall back (None), never silently apply HIGH EMOS or bias"


# ----------------------------------------------------------------------------
# σ-FLOOR (residual under-dispersion fix, counterfactual 2026-06-05) — a served=raw cell keeps
#   the do-no-harm raw MEAN but FLOORS its dispersion at the calibrated EMOS lead-aware σ, so the
#   raw-cell under-dispersion (Singapore-class q_no≈1.0 → expensive-NO-on-the-winner loss) is killed.
# ----------------------------------------------------------------------------
def test_emos_sigma_model_ignores_served_gate(monkeypatch):
    # served=raw -> emos_predictive returns None (the gate kept the raw MEAN), but emos_sigma_model
    # STILL returns the calibrated σ (the floor source) — the σ params were fit even for raw cells.
    table = {"_meta": {}, "cells": {"RawCity|JJA|high": {"params": [0.0, 1.0, 0.5, 0.5, 0.30], "n": 99, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    members = np.array([22.0, 22.3, 22.6, 22.9], dtype=float)
    assert emos_mod.emos_predictive("RawCity", "JJA", 3.0, members, metric="high") is None, \
        "served=raw -> emos_predictive None (do-no-harm keeps the raw mean)"
    sig = emos_mod.emos_sigma_model("RawCity", "JJA", 3.0, members, metric="high")
    assert sig is not None and sig > 0.0, "emos_sigma_model must expose the calibrated σ even for served=raw"


def test_honest_raw_q_floors_dispersion_on_raw_cell(monkeypatch):
    mod = importlib.import_module("src.calibration.emos_q_builder")
    table = {"_meta": {}, "cells": {"RawCity|JJA|high": {"params": [0.0, 1.0, 0.5, 0.5, 0.30], "n": 99, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    members = np.array([22.0, 22.3, 22.6, 22.9], dtype=float)  # tight: raw sd ~0.39 (under-dispersed)
    raw_sd = float(np.std(members, ddof=1))
    bins = [(None, 21.0), (22.0, 22.0), (23.0, None)]
    out = mod.build_honest_raw_q(city="RawCity", season="JJA", metric="high", lead_days=5.0,
                                 members_native=members, unit="C", bins=bins)
    assert out is not None, "a raw cell with a calibrated σ must produce a floored honest-raw dist"
    q, mu, sigma = out
    assert abs(mu - float(np.mean(members))) < 1e-6, "honest-raw keeps the do-no-harm raw MEAN"
    assert sigma > raw_sd, "dispersion must be FLOORED above the tight raw σ (only widens, never tightens)"
    assert abs(float(np.sum(q)) - 1.0) < 1e-6, "q normalized"


def test_honest_raw_q_none_when_cell_absent(monkeypatch):
    # Truly-absent cell -> no calibrated floor -> None (caller uses the pure raw analytic).
    mod = importlib.import_module("src.calibration.emos_q_builder")
    monkeypatch.setattr(emos_mod, "_emos_table_cache", {"_meta": {}, "cells": {}}, raising=False)
    out = mod.build_honest_raw_q(city="Nowhere", season="JJA", metric="high", lead_days=3.0,
                                 members_native=np.array([20.0, 21.0, 22.0], dtype=float),
                                 unit="C", bins=[(None, 21.0), (22.0, None)])
    assert out is None, "no calibrated σ-model -> None -> caller uses the pure raw analytic"


def test_inv_canonical_season_is_nh_month_no_hemisphere_flip():
    # SEASON-CROSSING ANTIBODY (critic 2026-06-04): EMOS cells are NH-month-keyed
    # (fit_emos_calibration.season()). A hemisphere-aware season SH-flips and serves the
    # OPPOSITE-season cell for SH cities — the twin of the metric-crossing bug. The ONE
    # canonical emos_season() must be NH-month-only for ALL callers (seam, shadow, CI, boot).
    from src.calibration.emos import emos_season, emos_cell_key
    # December = DJF in BOTH hemispheres under NH-month keying (an SH city in its summer
    # must still resolve the DJF cell, because that is how the fit stored it).
    assert emos_season("2026-12-15") == "DJF"
    assert emos_season("2026-06-15") == "JJA"
    # Accepts date objects too.
    import datetime as _dt
    assert emos_season(_dt.date(2026, 1, 10)) == "DJF"
    # Contrast: the hemisphere-aware season WOULD flip an SH city to the opposite label.
    from src.contracts.season import season_from_date
    assert season_from_date("2026-12-15", lat=-33.0) != emos_season("2026-12-15"), \
        "the OLD season_from_date(lat<0) flips SH summer to JJA — exactly the bug; callers must use emos_season"
    # 3-key helper is the only correct cell address.
    assert emos_cell_key("Sao Paulo", "DJF", "high") == "Sao Paulo|DJF|high"


def test_inv1_metric_keyed_no_crossing_and_low_serves(monkeypatch):
    # METRIC ANTIBODY (2026-06-04): cells are keyed city|season|metric. HIGH and LOW are
    # physically different quantities (daily max vs min); a LOW lookup resolves ONLY a LOW
    # cell. Proves: (1) a LOW market is NEVER served the HIGH fit; (2) when a LOW cell exists
    # the LOW path serves from it (LOW is calibrated, not quarantined).
    mod = importlib.import_module("src.calibration.emos_q_builder")
    members = np.array([10.0, 11.0, 12.0, 13.0], dtype=float)
    bins = [(None, 11.0), (12.0, 12.0), (13.0, None)]

    # HIGH-only table: low lookup misses -> None (no cross-metric serve).
    table_high_only = {"_meta": {"metric": "multi"},
                       "cells": {"X|JJA|high": {"params": [0, 1, 0, 1, 0.2], "n": 99, "served": "emos"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table_high_only, raising=False)
    assert mod.build_emos_q(city="X", season="JJA", metric="high", lead_days=3.0,
                            members_native=members, unit="C", bins=bins) is not None
    assert mod.build_emos_q(city="X", season="JJA", metric="low", lead_days=3.0,
                            members_native=members, unit="C", bins=bins) is None, \
        "LOW market must NOT receive the HIGH EMOS fit — metric key isolates them"

    # Add a LOW cell with a DISTINCT mean shift -> LOW now serves from its OWN cell.
    table_both = {"_meta": {"metric": "multi"},
                  "cells": {"X|JJA|high": {"params": [0, 1, 0, 1, 0.2], "n": 99, "served": "emos"},
                            "X|JJA|low": {"params": [-2.0, 1, 0, 1, 0.2], "n": 99, "served": "emos"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table_both, raising=False)
    hi = mod.build_emos_q(city="X", season="JJA", metric="high", lead_days=3.0,
                          members_native=members, unit="C", bins=bins)
    lo = mod.build_emos_q(city="X", season="JJA", metric="low", lead_days=3.0,
                          members_native=members, unit="C", bins=bins)
    assert hi is not None and lo is not None, "both metrics serve from their own cells"
    # The LOW cell's a=-2 mean-shift makes its mu distinct from HIGH's -> not the same fit.
    assert abs(hi[1] - lo[1]) > 1.5, "LOW must use the LOW cell's params, not the HIGH cell's"


# ----------------------------------------------------------------------------
# EMPIRICAL SETTLEMENT σ-FLOOR (loop-breaker, investigation 2026-06-05) — the EMOS σ-model is
#   SYSTEMICALLY under-dispersed (median σ_emos/σ_settled = 0.49). The correct floor is the
#   DETRENDED trailing-window settlement std per (city, season, metric): σ_eff =
#   max(model_σ, k·σ_settled_floor), k=0.8, applied UNIVERSALLY (EMOS-served AND raw-served).
#   Flag-gated via the builders' apply_settlement_floor param (seam reads
#   edli_v1.edli_settlement_sigma_floor_enabled). Flag OFF ⇒ byte-identical. max() only WIDENS σ →
#   lower q_lcb → fewer overconfident bets; can NEVER tighten or create a wrong-side trade (iron rule 5).
# ----------------------------------------------------------------------------
def _floor_cell(city, season, metric, sigma_floor_c, k=0.8):
    return {
        "_meta": {"k_default": k},
        "cells": {f"{city}|{season}|{str(metric).lower()}":
                  {"sigma_floor_c": sigma_floor_c, "n": 30, "window": "45d"}},
    }


def test_settlement_floor_flag_off_is_byte_identical_emos(monkeypatch):
    # Flag OFF (default) ⇒ build_emos_q output is identical with/without a floor table present.
    mod = importlib.import_module("src.calibration.emos_q_builder")
    table = {"_meta": {"metric": "multi"},
             "cells": {"TelAviv|JJA|high": {"params": [0.0, 1.0, -0.4, 0.5, 0.0], "n": 99, "served": "emos"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    # a floor table that WOULD widen if applied
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _floor_cell("TelAviv", "JJA", "high", 2.9), raising=False)
    members = np.array([26.6, 27.0, 27.4, 27.0], dtype=float)  # tight raw spread
    bins = [(None, 30.0), (31.0, 31.0), (32.0, 32.0), (33.0, None)]
    kw = dict(city="TelAviv", season="JJA", metric="high", lead_days=3.0,
              members_native=members, unit="C", bins=bins)
    off_default = mod.build_emos_q(**kw)                         # default param OFF
    off_explicit = mod.build_emos_q(apply_settlement_floor=False, **kw)
    assert off_default is not None and off_explicit is not None
    assert np.allclose(off_default[0], off_explicit[0]) and off_default[2] == off_explicit[2], \
        "flag OFF (default vs explicit-False) must be byte-identical — no floor applied"


def test_settlement_floor_flag_off_is_byte_identical_honest_raw(monkeypatch):
    mod = importlib.import_module("src.calibration.emos_q_builder")
    table = {"_meta": {}, "cells": {"RawCity|JJA|high": {"params": [0.0, 1.0, -0.4, 0.5, 0.0], "n": 99, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _floor_cell("RawCity", "JJA", "high", 2.9), raising=False)
    members = np.array([22.0, 22.3, 22.6, 22.9], dtype=float)
    bins = [(None, 21.0), (22.0, 22.0), (23.0, None)]
    kw = dict(city="RawCity", season="JJA", metric="high", lead_days=5.0,
              members_native=members, unit="C", bins=bins)
    off_default = mod.build_honest_raw_q(**kw)
    off_explicit = mod.build_honest_raw_q(apply_settlement_floor=False, **kw)
    assert off_default is not None and off_explicit is not None
    assert np.allclose(off_default[0], off_explicit[0]) and off_default[2] == off_explicit[2], \
        "honest-raw flag OFF must be byte-identical (keeps existing emos_sigma_model floor only)"


def test_settlement_floor_on_widens_sigma_emos(monkeypatch):
    # Flag ON + model σ (~0.83) < k·σ_settled (0.8·2.9 = 2.32) ⇒ sigma_native ≥ 2.32.
    mod = importlib.import_module("src.calibration.emos_q_builder")
    # params chosen so EMOS σ ≈ 0.83 for this member spread: sqrt(exp(c + d*log(S2))).
    table = {"_meta": {"metric": "multi"},
             "cells": {"TelAviv|JJA|high": {"params": [0.0, 1.0, -0.4, 0.5, 0.0], "n": 99, "served": "emos"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _floor_cell("TelAviv", "JJA", "high", 2.9), raising=False)
    members = np.array([26.6, 27.0, 27.4, 27.0], dtype=float)
    bins = [(None, 30.0), (31.0, 31.0), (32.0, 32.0), (33.0, None)]
    off = mod.build_emos_q(city="TelAviv", season="JJA", metric="high", lead_days=3.0,
                           members_native=members, unit="C", bins=bins, apply_settlement_floor=False)
    on = mod.build_emos_q(city="TelAviv", season="JJA", metric="high", lead_days=3.0,
                          members_native=members, unit="C", bins=bins, apply_settlement_floor=True)
    assert off is not None and on is not None
    assert off[2] < 1.5, "model σ must be the tight under-dispersed value (floor source: investigation)"
    assert on[2] >= 0.8 * 2.9 - 1e-6, "floor ON must widen sigma to ≥ k·σ_settled = 2.32"
    assert on[2] > off[2], "floor only WIDENS, never tightens"


def test_settlement_floor_on_requires_floor_cell_emos(monkeypatch):
    # Reviewer-facing contract: missing floor cells are legacy fail-soft only while the
    # settlement-floor flag is OFF. With apply_settlement_floor=True, the same miss is hard.
    mod = importlib.import_module("src.calibration.emos_q_builder")
    table = {"_meta": {"metric": "multi"},
             "cells": {"TelAviv|JJA|high": {"params": [0.0, 1.0, -0.4, 0.5, 0.0],
                                             "n": 99, "served": "emos"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", {"_meta": {"k_default": 0.8}, "cells": {}}, raising=False)
    kw = dict(city="TelAviv", season="JJA", metric="high", lead_days=3.0,
              members_native=np.array([26.6, 27.0, 27.4, 27.0], dtype=float),
              unit="C", bins=[(None, 30.0), (31.0, None)])

    assert mod.build_emos_q(apply_settlement_floor=False, **kw) is not None
    with pytest.raises(emos_mod.SettlementSigmaFloorError, match="MISSING_CELL"):
        mod.build_emos_q(apply_settlement_floor=True, **kw)


def test_settlement_floor_on_requires_positive_floor_honest_raw(monkeypatch):
    mod = importlib.import_module("src.calibration.emos_q_builder")
    table = {"_meta": {}, "cells": {"RawCity|JJA|high": {"params": [0.0, 1.0, -0.4, 0.5, 0.0],
                                                         "n": 99, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    monkeypatch.setattr(
        emos_mod,
        "_sigma_floor_cache",
        {"_meta": {"k_default": 0.8},
         "cells": {"RawCity|JJA|high": {"sigma_floor_c": -1.0, "n": 30, "window": "45d"}}},
        raising=False,
    )
    kw = dict(city="RawCity", season="JJA", metric="high", lead_days=5.0,
              members_native=np.array([22.0, 22.3, 22.6, 22.9], dtype=float),
              unit="C", bins=[(None, 21.0), (22.0, None)])

    assert mod.build_honest_raw_q(apply_settlement_floor=False, **kw) is not None
    with pytest.raises(emos_mod.SettlementSigmaFloorError, match="NON_POSITIVE"):
        mod.build_honest_raw_q(apply_settlement_floor=True, **kw)


def test_settlement_floor_defangs_telaviv_degenerate_q(monkeypatch):
    # Tel Aviv reproduction: model σ≈0.81 (matches investigation ≈0.83), σ_settled≈2.9, μ≈27.
    # The "33°C or higher" open-high far bin: floor-OFF gets LITERALLY ZERO probability mass
    # (degenerate q_yes=0.000 ⇒ q_no=1.000, the overconfident expensive-NO-on-the-winner trap);
    # floor-ON it receives materially > 0 mass (q_no de-fanged below the degenerate 1.000). The
    # nearer "32" interior bin's q_no drops well under 0.99 — the investigation's q_no≈0.96 read.
    mod = importlib.import_module("src.calibration.emos_q_builder")
    table = {"_meta": {"metric": "multi"},
             "cells": {"TelAviv|JJA|high": {"params": [0.0, 1.0, -0.4, 0.5, 0.0], "n": 99, "served": "emos"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _floor_cell("TelAviv", "JJA", "high", 2.9), raising=False)
    members = np.array([25.8, 27.0, 28.2, 27.0], dtype=float)  # μ=27, S2≈0.96 ⇒ σ_emos≈0.81
    # bins: (None,30) shoulder, 31, 32 interior, 33+ open-high ("33°C or higher")
    bins = [(None, 30.0), (31.0, 31.0), (32.0, 32.0), (33.0, None)]
    far_idx = len(bins) - 1   # the 33+ bin
    near_idx = 2              # the 32 interior bin

    off = mod.build_emos_q(city="TelAviv", season="JJA", metric="high", lead_days=3.0,
                           members_native=members, unit="C", bins=bins, apply_settlement_floor=False)
    on = mod.build_emos_q(city="TelAviv", season="JJA", metric="high", lead_days=3.0,
                          members_native=members, unit="C", bins=bins, apply_settlement_floor=True)
    assert off is not None and on is not None
    assert off[2] == pytest.approx(0.81, abs=0.05), "model σ must reproduce the investigation's ~0.83"
    # FAR bin (33+): degenerate-zero under OFF → materially positive under ON.
    q_far_off, q_far_on = float(off[0][far_idx]), float(on[0][far_idx])
    assert q_far_off < 1e-6, f"OFF: far-bin q_yes is degenerate-zero (q_no=1.000), got {q_far_off:.7f}"
    assert (1.0 - q_far_on) < 0.999, f"ON: far-bin q_no de-fanged below degenerate 1.000, got {1 - q_far_on:.5f}"
    assert q_far_on > q_far_off, "ON must move real mass onto the previously-starved far bin"
    # NEAR bin (32): the investigation's q_no≈0.96 read — buy_no on 32 is no longer near-certain.
    q_no_near_off, q_no_near_on = 1.0 - float(off[0][near_idx]), 1.0 - float(on[0][near_idx])
    assert q_no_near_off > 0.999, f"OFF: near-bin q_no degenerate, got {q_no_near_off:.5f}"
    assert q_no_near_on < 0.99, f"ON: near-bin q_no de-fanged below 0.99, got {q_no_near_on:.5f}"


# ----------------------------------------------------------------------------
# INV-5 — WIRE-OR-DELETE. After Phase 3 the maze mean-correction sites are gone.
#   (RED until deletion; documents the target so a future session cannot re-add them.)
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# INV-6 — ANTIBODY (the loop-breaker). The set of mean-correction mechanisms in the q seam
#   may only SHRINK toward {EMOS}, never grow. A NEW correction function added to the seam
#   fails this test in CI — so the next session cannot silently re-add a parallel mechanism
#   (the exact regression that recurred every life). Models the AST forbidden-call guard
#   src/state/table_registry.py:assert_no_raw_find_weather_markets_in_daemon_callers.
#   When Phase 3 deletes the maze, the EXPECTED set shrinks to the EMOS-only frozen set and
#   this test is the explicit, reviewed record of that deletion.
_Q_SEAM_FN = "_market_analysis_from_event_snapshot"
# Every calibration mechanism currently wired into the q seam. The ratchet: this set is the
# WHOLE registry; adding a name not here (a new parallel corrector) breaks CI; removing one
# (Phase-3 deletion) requires editing this list in the same reviewed diff.
_ALLOWED_Q_SEAM_CORRECTORS = frozenset({
    "_build_emos_q", "_make_emos_bootstrap_sampler",          # the ONE calibrator (target end-state)
    "_maybe_apply_edli_bias_correction",                      # maze (off by flag; never deleted)
    "_maybe_apply_grid_representativeness_correction",        # maze (off by flag; never deleted)
    "_edli_representativeness_sigma_native",                  # maze (off by flag; never deleted)
    "_assert_single_temperature_mean_correction",            # double-count GUARD (not a corrector)
})


def _q_seam_corrector_calls() -> set:
    import ast
    import pathlib
    import re
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "engine" / "event_reactor_adapter.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == _Q_SEAM_FN), None)
    assert fn is not None, f"{_Q_SEAM_FN} not found — the q seam moved; re-anchor the antibody"
    # Widened name pattern (critic m1: the old regex missed _apply_warm_shift / _recenter / etc.).
    pat = re.compile(r"bias|grid|representativ|emos|shift|offset|recenter|anchor|climatolog|"
                     r"correct|warm|cold|calibrat|platt|adjust|debias")
    names: set = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname and pat.search(fname.lower()):
                names.add(fname)
        # CATEGORY-SCOPED (critic m1): ANY call whose result is assigned to the `members` binding is
        # a member-mutator — caught regardless of its name. This is the true structural invariant.
        if isinstance(node, ast.Assign):
            tnames: set = set()
            for t in node.targets:
                if isinstance(t, ast.Name):
                    tnames.add(t.id)
                elif isinstance(t, ast.Tuple):
                    tnames.update(e.id for e in t.elts if isinstance(e, ast.Name))
            if "members" in tnames:
                v = node.value
                for cand in (v.elts if isinstance(v, ast.Tuple) else [v]):
                    if isinstance(cand, ast.Call):
                        cn = getattr(cand.func, "id", None) or getattr(cand.func, "attr", None)
                        if cn:
                            names.add(cn)
    return names


def test_inv_seam_season_matches_fit_season_all_months():
    # C1 (critic 2026-06-04) — fit<->seam boundary. The seam keys the EMOS lookup by NH month-season;
    # the fit (fit_emos_calibration.season) keys cells by NH month-season. They MUST agree for EVERY
    # month, including SH dates — else SH cities (Wellington/Sao Paulo/...) are served the
    # OPPOSITE-season cell (the silent wrong-season corruption the program exists to kill). A prior
    # seam used season_from_date(lat) (hemisphere-flipped) and broke this. Cross the real boundary.
    from scripts.fit_emos_calibration import season as fit_season

    def seam_season(m):  # the inline logic in event_reactor_adapter.py EMOS branch
        return ("DJF" if m in (12, 1, 2) else "MAM" if m in (3, 4, 5)
                else "JJA" if m in (6, 7, 8) else "SON")

    for m in range(1, 13):
        assert seam_season(m) == fit_season(m), (
            f"month {m}: seam keys {seam_season(m)} but fit built {fit_season(m)} — SH wrong-season"
        )


def test_inv6_q_seam_correctors_do_not_grow():
    found = _q_seam_corrector_calls()
    extra = found - _ALLOWED_Q_SEAM_CORRECTORS
    assert not extra, (
        f"NEW calibration corrector(s) {sorted(extra)} wired into the q seam. The one-calibrator "
        f"antibody forbids parallel mechanisms — route through build_emos_q or delete. If this is "
        f"an intentional Phase-3 deletion, update _ALLOWED_Q_SEAM_CORRECTORS in the same diff."
    )


@pytest.mark.xfail(strict=True, reason="Phase 3: maze deletion lands only after EMOS is "
                   "settlement-proven per-city + operator sign. Flips to ENFORCED (xpass=fail) "
                   "once the mean-correction sites are deleted — the wire-or-delete antibody.")
def test_inv5_maze_mean_correction_deleted():
    adapter = importlib.import_module("src.engine.event_reactor_adapter")
    for fn in ("_maybe_apply_edli_bias_correction", "_maybe_apply_grid_representativeness_correction"):
        assert not hasattr(adapter, fn), (
            f"{fn} is a deleted maze mechanism; the one-calibrator antibody forbids re-adding it"
        )
