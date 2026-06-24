# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: docs/evidence/live_order_pathology/fix_implementation.md
#   (live order pathology 2026-06-21 — honest q-SHAPE settlement-frequency temper).
#   The served predictive σ IS the realized walk-forward settlement RMSE
#   (src/forecast/sigma_authority.py), but a Normal with the right VARIANCE still
#   over-states the SHAPE of the far / open-shoulder tail vs realized settlement
#   frequency. build_joint_q applies a FITTED temper q_adj = q ** gamma (γ=1.0
#   IDENTITY when no fitted artifact) as part of the ONE q transform, pulling the
#   over-stated tail toward center to match realized frequency WITHOUT crushing the
#   near-center ring edge and WITHOUT a cap/haircut (operator no-caps law).
"""RED-on-revert antibodies for the honest q-SHAPE settlement-frequency temper.

Category killed: a bare-Normal q whose far / open-shoulder tail over-states realized
settlement frequency by 3-7x, which the sub-cent edge rule then mass-buys. The temper
(q ** gamma, γ FITTED to realized frequency, INERT γ=1.0 when no artifact) makes the
impossible tail bin carry an HONEST ≈0 q so it fails the existing edge_lcb>0 gate
naturally — no new filter, no cap.

Invariants proven here:
  1. Missing / unfitted / malformed artifact → γ=1.0: q BYTE-IDENTICAL to the
     un-tempered Normal (the temper is inert by default).
  2. Fitted C artifact (γ>1) on the Milan-shaped case: the far tail bin (q(40°C),
     d4) is pulled DOWN toward its realized frequency while the near-center ring
     (d1-2 — the only real edge) is PRESERVED; the open shoulder is NOT inflated.
  3. The temper propagates to the JointQBand (each band draw is a build_joint_q call),
     so the edge_lcb gate's per-bin band samples ALSO see the honest tail.
  4. γ<1 / non-finite is clamped to 1.0 (a temper may only pull the tail IN, never
     FATTEN it — γ<1 would re-create the pathology).
  5. RED-on-revert: deleting the `q = np.power(q, _gamma)` line makes the fitted-γ q
     equal to the un-tempered Normal — test 2's tail-shrink assertion then fails.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pytest

from src.config import City
from src.forecast.day0_conditioner import Day0Conditioning
from src.probability.event_resolution import EventResolution, event_resolution_for_city
import src.probability.joint_q as jq_mod
from src.probability.joint_q import build_joint_q
from src.probability.joint_q_band import build_joint_q_band
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)


# ---------------------------------------------------------------------------
# Minimal predictive-distribution double (mirrors tests/probability/test_joint_q.py).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _PD:
    mu_native: float
    sigma_native: float
    distribution_family: str
    day0: Day0Conditioning
    live_eligible: bool = True
    ineligibility_reason: Optional[str] = None
    identity_hash: str = "pd-temper-test"
    # JointQBand reads sigma_components.center_parameter_se_native + realized_floor_native.
    sigma_components: object = None


@dataclass(frozen=True)
class _SigmaComponents:
    center_parameter_se_native: float
    realized_floor_native: float


def _inactive_day0(center: float) -> Day0Conditioning:
    return Day0Conditioning(
        active=False,
        observed_extreme_native=None,
        support_lower_native=None,
        support_upper_native=None,
        center_before_native=center,
        center_after_native=center,
        status="NO_DAY0",
    )


# ---------------------------------------------------------------------------
# A Milan-shaped HIGH family: μ*≈36°C, σ≈2.0°C, 1°C interior bins 31..41 + shoulders.
# ---------------------------------------------------------------------------

def _resolution() -> EventResolution:
    city = City(
        name="Milan",
        lat=45.46,
        lon=9.19,
        timezone="Europe/Rome",
        settlement_unit="C",
        cluster="europe",
        wu_station="LIML",
        settlement_source_type="wu_icao",
    )
    return event_resolution_for_city(city, date(2026, 6, 23), "high")


def _bin(bin_id, lo, hi, label, rule, *, executable=True) -> OutcomeBin:
    return OutcomeBin(
        bin_id=bin_id,
        condition_id=f"cond-{bin_id}",
        label=label,
        lower_native=lo,
        upper_native=hi,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        executable=executable,
        rounding_rule=rule,
    )


def _milan_space() -> OutcomeSpace:
    res = _resolution()
    rule = res.rounding_rule
    bins = [_bin("b_low", None, 30.0, "30°C or below", rule, executable=False)]
    for t in range(31, 42):
        bins.append(_bin(f"b{t}", float(t), float(t), f"{t}°C", rule))
    bins.append(_bin("b_high", 42.0, None, "42°C or above", rule, executable=False))
    bins = tuple(bins)
    space = OutcomeSpace(
        family_id="milan-high",
        resolution=res,
        bins=bins,
        topology_hash=compute_topology_hash("milan-high", res, bins),
    )
    space.validate()
    return space


def _milan_pd() -> _PD:
    return _PD(
        mu_native=36.0,
        sigma_native=2.0,
        distribution_family="NORMAL",
        day0=_inactive_day0(36.0),
        sigma_components=_SigmaComponents(
            center_parameter_se_native=0.3, realized_floor_native=2.0
        ),
    )


def _write_temper(tmp_path, monkeypatch, families: dict) -> None:
    path = tmp_path / "q_shape_temper_fit.json"
    path.write_text(json.dumps({"_meta": {"authority": "q_shape_temper_v1_mle"}, "families": families}))
    monkeypatch.setattr(jq_mod, "_Q_SHAPE_TEMPER_FIT_PATH", str(path))


def _no_temper(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(jq_mod, "_Q_SHAPE_TEMPER_FIT_PATH", str(tmp_path / "absent.json"))


# ---------------------------------------------------------------------------
# 1. Missing / unfitted / malformed artifact → γ=1.0 INERT (byte-identical).
# ---------------------------------------------------------------------------

def test_missing_artifact_is_inert(monkeypatch, tmp_path):
    _no_temper(tmp_path, monkeypatch)
    assert jq_mod._q_shape_temper_lookup("C") == 1.0
    q = build_joint_q(_milan_pd(), _milan_space())
    # Bare-Normal N(36,2) values (the documented un-tempered q), unchanged.
    assert q.q_by_bin_id["b36"] == pytest.approx(0.1974, abs=2e-3)
    assert q.q_by_bin_id["b40"] == pytest.approx(0.0278, abs=2e-3)
    assert abs(float(q.q.sum()) - 1.0) <= 1e-9


def test_unfitted_and_malformed_are_inert(monkeypatch, tmp_path):
    _write_temper(tmp_path, monkeypatch, {"C": {"fitted": False, "gamma": 1.6}})
    assert jq_mod._q_shape_temper_lookup("C") == 1.0
    path = tmp_path / "q_shape_temper_fit.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(jq_mod, "_Q_SHAPE_TEMPER_FIT_PATH", str(path))
    assert jq_mod._q_shape_temper_lookup("C") == 1.0


def test_gamma_below_one_or_nonfinite_clamped_to_inert(monkeypatch, tmp_path):
    """A temper may only pull the tail IN (γ>=1); γ<1 would FATTEN it (the pathology)."""
    _write_temper(tmp_path, monkeypatch, {
        "C": {"fitted": True, "gamma": 0.5},      # would inflate the tail → refused
        "F": {"fitted": True, "gamma": float("nan")},
    })
    assert jq_mod._q_shape_temper_lookup("C") == 1.0
    assert jq_mod._q_shape_temper_lookup("F") == 1.0


# ---------------------------------------------------------------------------
# 2. Fitted C (γ>1): far tail pulled to honest ≈realized; near-ring PRESERVED.
#    This is THE pathology fix and the RED-on-revert guard.
# ---------------------------------------------------------------------------

def test_fitted_gamma_pulls_tail_to_honest_zero_without_crushing_ring(monkeypatch, tmp_path):
    space = _milan_space()
    pd = _milan_pd()

    # Baseline: inert (the over-stating bare Normal).
    _no_temper(tmp_path, monkeypatch)
    q0 = build_joint_q(pd, space).q_by_bin_id

    # Fitted C temper γ=1.3 (the per-cell-verified value; FITTED in production).
    _write_temper(tmp_path, monkeypatch, {"C": {"fitted": True, "gamma": 1.3}})
    assert jq_mod._q_shape_temper_lookup("C") == pytest.approx(1.3)
    q1 = build_joint_q(pd, space).q_by_bin_id

    # (a) The far interior tail bin (40°C, d4) is pulled DOWN toward realized (~0.5-1.7%).
    assert q1["b40"] < q0["b40"], "temper must pull the over-stated far tail bin DOWN"
    assert q1["b40"] < 0.02, "q(40°C) must be an HONEST near-zero after the temper"
    # The bare Normal over-stated it (the pathology the sub-cent edge rule bought).
    assert q0["b40"] > 0.025

    # (b) The OPEN-SHOULDER catch-all is NOT inflated (monotone-conservative invariant).
    assert q1["b_high"] <= q0["b_high"] + 1e-9, "open shoulder must never be inflated by the temper"

    # (c) The near-center RING edge (d1-2: 37/38°C and 34/35°C) is PRESERVED — it must
    #     NOT be crushed. The mode/near-ring keep (or gain) relative mass.
    assert q1["b36"] >= q0["b36"], "center mode must not be crushed by the temper"
    # The near ring stays a real, tradeable mass (the only alpha) — not driven to ~0.
    assert q1["b38"] > 0.08, "near-ring (38°C, d2) edge must survive (not crushed)"
    assert q1["b35"] > 0.08, "near-ring (35°C, d1) edge must survive (not crushed)"

    # (d) Still a coherent distribution.
    assert abs(sum(q1.values()) - 1.0) <= 1e-9

    # (e) RED-on-revert: with the temper applied, the tail bin moved. If the
    #     `q = np.power(q, _gamma)` line is deleted, q1 == q0 and (a) fails.
    assert q1["b40"] != pytest.approx(q0["b40"], abs=1e-6)


# ---------------------------------------------------------------------------
# 3. The temper propagates to the JointQBand (edge_lcb band samples see honest tail).
# ---------------------------------------------------------------------------

def test_temper_propagates_to_band_qlcb(monkeypatch, tmp_path):
    space = _milan_space()
    pd = _milan_pd()

    _no_temper(tmp_path, monkeypatch)
    band0 = build_joint_q_band(pd, space, n_draws=1500, alpha=0.05)
    i40 = [b.bin_id for b in space.bins].index("b40")
    ucb0_40 = float(band0.q_ucb[i40])

    _write_temper(tmp_path, monkeypatch, {"C": {"fitted": True, "gamma": 1.3}})
    band1 = build_joint_q_band(pd, space, n_draws=1500, alpha=0.05)
    ucb1_40 = float(band1.q_ucb[i40])

    # The band is built from build_joint_q draws, so the temper pulls the far tail
    # bin's UPPER band down too — the edge_lcb gate no longer sees an over-stated
    # tail it can buy at sub-cent prices.
    assert ucb1_40 < ucb0_40, "temper must propagate into the band's far-tail q_ucb"
    # Every band row is still a coherent simplex.
    band1.assert_valid()


# ---------------------------------------------------------------------------
# 4. F-unit family read independently (defense-in-depth; absent F → inert).
# ---------------------------------------------------------------------------

def test_f_family_lookup_independent(monkeypatch, tmp_path):
    _write_temper(tmp_path, monkeypatch, {"C": {"fitted": True, "gamma": 1.3}})
    assert jq_mod._q_shape_temper_lookup("C") == pytest.approx(1.3)
    assert jq_mod._q_shape_temper_lookup("F") == 1.0  # F absent → inert
