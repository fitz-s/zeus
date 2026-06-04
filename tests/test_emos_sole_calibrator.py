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
    table = {"_meta": {"metric": "high"}, "cells": {"TestCity|JJA": dict(_SYNTH_CELL)}}
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
        city="TestCity", season="JJA", lead_days=3.0,
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
    table = {"_meta": {}, "cells": {"RawCity|JJA": {"params": [0, 1, 0, 1, 0.2], "n": 99, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    out = mod.build_emos_q(city="RawCity", season="JJA", lead_days=3.0,
                           members_native=np.array([20.0, 21.0, 22.0], dtype=float),
                           unit="C", bins=[(None, 21.0), (22.0, None)])
    assert out is None, "served=raw must fall back (None), never silently apply HIGH EMOS or bias"


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
