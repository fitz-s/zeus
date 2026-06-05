# Created: 2026-06-05
# Lifecycle: created=2026-06-05; last_reviewed=2026-06-05; last_reused=2026-06-05
# Last audited: 2026-06-05
# Authority basis: issue #390 — emos-seam σ-floor relationship test, deferred from PR #389.
# Purpose: RELATIONSHIP TEST (cross-module, LIVE call path) for the honest-raw σ-FLOOR seam
#   boundary (#388/#389). The unit test test_honest_raw_q_floors_dispersion_on_raw_cell proves
#   build_honest_raw_q floors σ in ISOLATION; it does NOT prove the floored σ PROPAGATES through
#   the seam (_market_analysis_from_event_snapshot in src/engine/event_reactor_adapter.py) into
#   BOTH the point-q AND the N(mu,sigma) lcb bootstrap sampler (_make_emos_bootstrap_sampler).
#   That cross-module boundary — emos_q_builder -> event_reactor_adapter — is exactly where an
#   "everyone knows about the floor but nobody wired it through" bug lives (Fitz constraint #2:
#   design intent has ~20% cross-session survival; only a relationship test pins it).
#
#   The seam path under test (the served=raw / EMOS-miss branch under the one-calibrator regime):
#   build_honest_raw_q returns (q, mu, FLOORED σ); the seam unpacks it to (_hrq, _hr_mu, _hr_sigma)
#   and installs _make_emos_bootstrap_sampler(_hr_mu, _hr_sigma) on
#   analysis._bootstrap_probability_sampler. The floored σ is the sampler's `sigma_native` free
#   variable. This test asserts the σ that LANDS in the sampler is the FLOORED value, not the tight
#   raw sample sd — proving the floor crosses the seam boundary into the q_lcb draw N(mu, σ).
#
#   The cell here is served=RAW (not emos) with a TIGHT raw member spread (sd ~0.08 °C) and an EMOS
#   σ-model fitted to 1.7 °C, so the floor max(raw_σ, emos_σ)=1.7 >> raw_σ is observable and exact.
#
# Reuse: update when src/engine/event_reactor_adapter.py:_market_analysis_from_event_snapshot,
#   _make_emos_bootstrap_sampler, src/calibration/emos_q_builder.build_honest_raw_q, or
#   src/calibration/emos.emos_sigma_model change.
# Models: tests/engine/test_emos_seam_serve_loud.py (the real-seam harness this reuses);
#   tests/test_emos_sole_calibrator.py::test_honest_raw_q_floors_dispersion_on_raw_cell (the
#   unit-isolation sibling this extends to the seam boundary).
from __future__ import annotations

import math

import numpy as np
import pytest

from src.config import settings  # noqa: F401  (imported for parity with the harness module)
from src.calibration import emos as emos_mod
import src.calibration.emos_q_builder as qb

# Reuse the REAL-seam harness verbatim (issue #390: "THE HARNESS ALREADY EXISTS — reuse it").
import tests.engine.test_emos_seam_serve_loud as harness
from tests.engine.test_emos_seam_serve_loud import (
    _run_seam,
    _served_city_unit_c,
)


# A served=RAW cell. EMOS did NOT generalize its MEAN here, so the do-no-harm gate serves raw —
# but the σ-model (c, d, e) was still fitted and is what the floor reaches for. params=[a,b,c,d,e]:
#   sigma_c = sqrt(exp(c + d*log(S2) + e*lead_days)).
# d=0, e=0 makes the σ-model output INDEPENDENT of the (tight) raw S2 and the lead, so the floor is
# a CLEAN, DETERMINISTIC constant: c = ln(1.7^2) -> sigma_c = 1.7 °C, regardless of the members.
_FLOOR_SIGMA_C = 1.7
_RAW_CELL_WITH_FLOOR = {
    "params": [0.0, 1.0, math.log(_FLOOR_SIGMA_C ** 2), 0.0, 0.0],
    "n": 500,
    "served": "raw",  # do-no-harm: EMOS mean did not generalize; raw MEAN kept, σ FLOORED.
}

# TIGHT raw members (sd ~0.077 °C, S2 ~0.006) so the UN-floored raw σ is ~22x below the EMOS
# σ-model (1.7 °C). The floor max(raw_σ, emos_σ)=1.7 therefore dominates and is observable to the
# last digit — exactly the Singapore-class under-dispersion the floor exists to kill (#388).
_TIGHT_MEMBERS_C = np.array(
    [24.00, 24.10, 24.20, 24.00, 24.10, 24.15, 24.05, 24.20, 24.10, 24.00], dtype=float
)


class _FixedRng:
    """Minimal rng stand-in: .normal(...) returns the pinned tight member array (length-matched)."""

    def __init__(self, members_c: np.ndarray):
        self._members = np.asarray(members_c, dtype=float)

    def normal(self, _loc, _scale, size):
        n = int(size)
        if n == self._members.size:
            return self._members.copy()
        # The harness asks for 51 members; tile/truncate the tight cluster to that length so the
        # raw sd stays ~0.08 °C (well below the 1.7 °C floor) regardless of the requested count.
        reps = int(np.ceil(n / self._members.size))
        return np.tile(self._members, reps)[:n]


def _sampler_sigma(analysis) -> float:
    """Extract the σ that the seam installed into the N(mu, sigma) lcb bootstrap sampler.

    _make_emos_bootstrap_sampler closes over (mu_native, sigma_native); the inner _sampler draws
    N(mu_native, sigma_native). Reading the closure cell named 'sigma_native' is a direct,
    non-statistical observation of the σ that PROPAGATED through the seam into the q_lcb path.
    """
    s = analysis._bootstrap_probability_sampler
    assert s is not None, "the honest-raw branch must install the N(mu,sigma) lcb bootstrap sampler"
    freevars = s.__code__.co_freevars
    assert "sigma_native" in freevars, (
        f"sampler closure lost its sigma_native free var (got {freevars!r}) — the σ propagation "
        "contract changed; this relationship test must be re-audited"
    )
    return float(s.__closure__[freevars.index("sigma_native")].cell_contents)


def _install_floor_cell(monkeypatch, members_c: np.ndarray) -> str:
    """Make the REAL floor engage end-to-end (NOT mocked) for a served=RAW cell:

    ``_run_seam`` (the harness) builds the EMOS table from the harness module-level ``_SYNTH_CELL``
    and OVERWRITES ``emos._emos_table_cache`` with it, then draws members via
    ``np.random.default_rng(7).normal(...)``. To drive a 1.7 °C floor with genuinely tight members we
    therefore patch BOTH harness inputs at the source:
      - ``harness._SYNTH_CELL`` -> our served=RAW cell whose σ-model is a clean 1.7 °C (d=e=0); this is
        the params ``_run_seam`` writes into the cache, so it survives the harness's cache overwrite.
      - ``harness.np.random.default_rng`` -> a fixed rng yielding the TIGHT cluster, so the raw sample
        sd (~0.08 °C) is far below the floor and the floor max(raw_σ, emos_σ)=1.7 dominates.
    Nothing in src/ is mocked: build_honest_raw_q computes the real max() over the real members.
    """
    city = _served_city_unit_c()
    monkeypatch.setattr(harness, "_SYNTH_CELL", dict(_RAW_CELL_WITH_FLOOR), raising=True)
    monkeypatch.setattr(
        harness.np.random, "default_rng",
        lambda *_a, **_k: _FixedRng(members_c), raising=True,
    )
    return city


# ---------------------------------------------------------------------------
# (A) THE RELATIONSHIP — the REAL floor propagates through the seam into the lcb sampler.
#     NON-MOCKED build_honest_raw_q: drives genuinely tight members + a fitted σ-model so the
#     floor max(raw_σ, emos_σ)=1.7 °C engages for real, and asserts the σ that LANDS in the
#     sampler is the FLOORED 1.7, not the tight raw sd (~0.08). This is the seam boundary the
#     unit test cannot see: emos_q_builder's floored σ -> event_reactor_adapter's q_lcb bootstrap.
# ---------------------------------------------------------------------------
def test_floored_sigma_propagates_into_seam_lcb_sampler(monkeypatch):
    city = _install_floor_cell(monkeypatch, _TIGHT_MEMBERS_C)

    payload, analysis = _run_seam(monkeypatch=monkeypatch, emos_serves=False, city=city)

    # served=raw under the regime routes to honest-raw (the σ-floor branch), not the bias maze.
    assert payload.get("_edli_q_source") == "raw_honest", (
        f"served=raw cell must route to honest-raw (got {payload.get('_edli_q_source')!r})"
    )

    raw_sd = float(np.std(_TIGHT_MEMBERS_C, ddof=1))
    landed_sigma = _sampler_sigma(analysis)

    # THE CROSS-MODULE INVARIANT: the σ in the seam's lcb sampler is the FLOORED σ (1.7 °C),
    # NOT the tight raw sample sd. Proves build_honest_raw_q's max(raw_σ, emos_σ) crossed the
    # emos_q_builder -> event_reactor_adapter boundary into BOTH the point-q and the q_lcb draw.
    assert landed_sigma == pytest.approx(_FLOOR_SIGMA_C, rel=1e-6), (
        f"the FLOORED σ ({_FLOOR_SIGMA_C} °C) must land in the lcb bootstrap sampler, but the seam "
        f"installed σ={landed_sigma} — the floor did NOT propagate across the seam boundary"
    )
    # And it is unambiguously the floor, not the raw sd (the antibody's discriminating margin):
    assert landed_sigma > raw_sd * 10.0, (
        f"sampler σ={landed_sigma} is near the tight raw sd ({raw_sd}); the un-floored raw σ leaked "
        "through the seam — the σ-floor relationship is broken"
    )


# ---------------------------------------------------------------------------
# (B) THE ANTIBODY (acceptance #1, #3, #4) — monkeypatch build_honest_raw_q per the issue, and
#     PROVE the assertion discriminates floored vs un-floored. Two sub-cases share one assertion:
#       (b1) builder returns FLOORED σ (1.7) -> the seam installs 1.7 -> assertion PASSES;
#       (b2) builder returns UN-FLOORED σ (== raw sd) -> the seam installs the tight sd -> the
#            SAME assertion FAILS. This demonstrates the test is NOT vacuous: it genuinely catches
#            a seam that fails to carry the floored σ (i.e. removing/bypassing the floor branch).
# ---------------------------------------------------------------------------
def _run_with_patched_builder(monkeypatch, *, returned_sigma_c: float) -> float:
    """Monkeypatch build_honest_raw_q to return a controlled (q, mu, sigma) and drive the seam.

    sigma is forced to ``returned_sigma_c`` (°C, the city is °C so native==C). q/mu are arbitrary
    but valid. Returns the σ the seam installed into the lcb sampler.
    """
    city = _served_city_unit_c()

    def _controlled_build_hr(
        *,
        city,
        season,
        metric,
        lead_days,
        members_native,
        unit,
        bins,
        apply_settlement_floor=False,
    ):
        n = len(bins)
        q = np.full(n, 1.0 / n, dtype=float)  # arbitrary valid normalized point-q
        mu_native = float(np.mean(np.asarray(members_native, dtype=float)))
        return q, mu_native, float(returned_sigma_c)

    monkeypatch.setattr(qb, "build_honest_raw_q", _controlled_build_hr, raising=True)

    # A served=raw cell so the seam takes the honest-raw branch (which calls build_honest_raw_q).
    season = "JJA"
    table = {"_meta": {"metric": "multi"},
             "cells": {f"{city}|{season}|high": {"params": [0.0, 1.0, 0.0, 1.0, 0.0],
                                                 "n": 500, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)

    _payload, analysis = _run_seam(monkeypatch=monkeypatch, emos_serves=False, city=city)
    return _sampler_sigma(analysis)


def test_monkeypatched_floored_sigma_propagates_and_discriminates(monkeypatch):
    # (b1) FLOORED build -> floored σ must propagate to the sampler (acceptance #1 + #3).
    floored = 1.7
    landed = _run_with_patched_builder(monkeypatch, returned_sigma_c=floored)
    assert landed == pytest.approx(floored, rel=1e-6), (
        "the σ returned by build_honest_raw_q must be the σ installed in the seam's lcb sampler"
    )


def test_unfloored_sigma_fails_the_floored_assertion(monkeypatch):
    """Acceptance #4 antibody: with the builder returning the UN-floored raw sd, the floored-σ
    assertion from (b1) must FAIL — proving test (A)/(b1) is not vacuous and genuinely catches a
    seam that carries the un-floored σ (i.e. the floor branch removed/bypassed).
    """
    raw_sd = float(np.std(_TIGHT_MEMBERS_C, ddof=1))  # the un-floored, tight σ (~0.077 °C)
    landed = _run_with_patched_builder(monkeypatch, returned_sigma_c=raw_sd)
    # The seam faithfully installs whatever σ the builder returned: here the UN-floored sd.
    assert landed == pytest.approx(raw_sd, rel=1e-6)
    # Therefore the floored-σ assertion (would-be 1.7) does NOT hold — the discriminator works.
    assert landed != pytest.approx(1.7, rel=1e-6), (
        "un-floored σ must NOT match the floored value — otherwise the relationship assertion "
        "would pass vacuously and could not catch a bypassed σ-floor"
    )


# ---------------------------------------------------------------------------
# (C) BRANCH-PRESENCE — the seam must CALL build_honest_raw_q on a served=raw cell. If the
#     honest-raw branch is deleted from the reactor, build_honest_raw_q is never invoked and this
#     test fails (acceptance #4: "Fails if the build_honest_raw_q branch in the reactor is removed").
# ---------------------------------------------------------------------------
def test_seam_invokes_build_honest_raw_q_on_served_raw_cell(monkeypatch):
    city = _served_city_unit_c()
    season = "JJA"
    table = {"_meta": {"metric": "multi"},
             "cells": {f"{city}|{season}|high": {"params": [0.0, 1.0, 0.0, 1.0, 0.0],
                                                 "n": 500, "served": "raw"}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)

    calls: list[dict] = []
    _orig = qb.build_honest_raw_q

    def _spy(**kwargs):
        calls.append(kwargs)
        return _orig(**kwargs)

    monkeypatch.setattr(qb, "build_honest_raw_q", _spy, raising=True)

    _payload, _analysis = _run_seam(monkeypatch=monkeypatch, emos_serves=False, city=city)

    assert calls, (
        "the seam MUST call build_honest_raw_q on a served=raw cell under the one-calibrator "
        "regime; if the honest-raw branch is removed from event_reactor_adapter this is never "
        "invoked (the σ-floor can no longer reach the q seam)"
    )
    assert calls[0]["unit"] == "C" and calls[0]["metric"] == "high", (
        "the seam must forward the settlement-asserted unit + market metric into build_honest_raw_q"
    )
