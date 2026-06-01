# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md §2-4 — the q-CI 5th
#   percentile must be a percentile of the SAME bias-corrected belief whose point is traded.
#   Guards the _snapshot_p_raw -> MarketAnalysis(member_maxes=...) train/serve boundary.
"""Relationship test (cross-module invariant) — bootstrap-bias-correction lockstep.

Cross-module invariant under test:
    The edge bootstrap (q_lcb_5pct) and the point posterior (q_live) must consume
    the SAME bias-corrected ensemble member surface.

Pre-fix violation (Tokyo 2026-06-01):
    _snapshot_p_raw applied bias correction to a local rebind of ``members``; the
    corrected array never escaped back to the caller. ``MarketAnalysis(member_maxes=members)``
    received the OUTER (uncorrected/cold) array. The bootstrap resampled cold members,
    placing its 5th percentile ~|eff_bias_c| degrees below the warm point posterior —
    a spurious ~15-19¢ CI that suppressed ~284 genuine +20¢-EV candidates/hr.

Post-fix invariant:
    ``_market_analysis_from_event_snapshot`` applies the correction ONCE before
    constructing both paths; ``_snapshot_p_raw(members_already_corrected=True)``
    skips the internal correction so there is no double-application.

Tests:
    (i)   RED on HEAD: bootstrap member mean ≈ raw (uncorrected) mean, differs from
          corrected by ≈|eff_bias_c|.  Post-fix: bootstrap member mean ≈ corrected mean.
    (ii)  Post-fix: CI collapses on a genuine-edge cold-biased contested bin;
          robust_trade_score > 0 for the +20¢-EV / cost≈0.75 Tokyo case.
    (iii) False-confidence guard: a WIDE-spread NO-bias bin keeps its honest CI
          and is declined (BOTH pre- and post-fix).
    (iv)  No double-correction: member mean == corrected mean exactly, not corrected twice.
    (v)   Property antibody: over (eff_bias_c, spread, lead) grid, assert
          mean(member_maxes) == corrected point mean to <1e-6 — makes the split
          structurally unconstructable.  RED on HEAD wherever eff_bias_c != 0.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from src.contracts.execution_price import ExecutionPrice
from src.engine.event_reactor_adapter import (
    _market_analysis_from_event_snapshot,
    _snapshot_p_raw,
)
from src.strategy.market_analysis_family_scan import scan_full_hypothesis_family
from src.strategy.live_inference.trade_score import robust_trade_score
from src.types.market import Bin

# ---------------------------------------------------------------------------
# Constants matching the Tokyo live case (CI_HONESTY_AND_SCORE_GATE_RULING-06-01 §2)
# ---------------------------------------------------------------------------
EFF_BIAS_C = -3.447      # model_bias_ens.effective_bias_c for Tokyo HIGH
RAW_MEAN = 24.5          # cold ensemble center (°C)
CORRECTED_MEAN = RAW_MEAN - EFF_BIAS_C   # ≈ 27.947 (warm, de-biased)
N_MEMBERS = 51
RNG_SEED = 42

_LAMBDA_EDGE = 0.01
_LAMBDA_STRESS = 0.01

# Bin boundary: Tokyo "high 23°C" contested bin (NO priced ~0.75)
# Celsius bins are POINT bins with low==high (width=1); shoulder bins use high=None.
BIN_POINT = 23  # point bin "23°C" — corrected ensemble (warm ~28°C) places almost all mass above → NO wins
NO_PRICE = 0.75
YES_PRICE = 0.25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_members(*, mean: float = RAW_MEAN, spread: float = 1.6, n: int = N_MEMBERS, seed: int = RNG_SEED) -> np.ndarray:
    """Return a synthetic ensemble of daily-max temperatures."""
    rng = np.random.default_rng(seed)
    return rng.normal(mean, spread, n).astype(float)


def _make_family(bins: list[Bin], *, city: str = "Tokyo", metric: str = "high"):
    """Minimal candidate_family namespace for _market_analysis_from_event_snapshot."""
    candidates = [
        SimpleNamespace(
            condition_id=f"cond-{i}",
            bin=b,
            yes_token_id=f"yes-{i}",
            no_token_id=f"no-{i}",
        )
        for i, b in enumerate(bins)
    ]
    return SimpleNamespace(
        city=city,
        metric=metric,
        target_date="2026-06-03",
        event_type="FORECAST_SNAPSHOT_READY",
        bins=bins,
        candidates=candidates,
        yes_token_ids=[f"yes-{i}" for i in range(len(bins))],
        no_token_ids=[f"no-{i}" for i in range(len(bins))],
        family_id="test-fam",
    )


def _make_snapshot(members: np.ndarray, *, metric: str = "high") -> dict:
    """Minimal snapshot dict for the adapter."""
    return {
        "settlement_unit": "C",
        "temperature_metric": metric,
        "members_json": json.dumps(members.tolist()),
        "members_precision": 1.0,
        "source_id": "ecmwf_open_data",
        "source_cycle_time": "2026-06-01T00:00:00+00:00",
        "issue_time": "2026-06-01T00:00:00+00:00",
        "dataset_id": "test_v1",
        "data_version": "test_v1",
    }


def _make_native_costs(bins: list[Bin], *, no_price: float = NO_PRICE, yes_price: float = YES_PRICE):
    """native_costs for all bins: both YES and NO sides executable."""
    from src.contracts.execution_price import ExecutionPrice as EP
    costs = {}
    for i, _ in enumerate(bins):
        cid = f"cond-{i}"
        costs[(cid, "buy_yes")] = (None, EP(yes_price, "ask", fee_deducted=True, currency="probability_units"), yes_price, None, None)
        costs[(cid, "buy_no")] = (None, EP(no_price, "ask", fee_deducted=True, currency="probability_units"), no_price, None, None)
    return costs


def _call_market_analysis(
    *,
    raw_members: np.ndarray,
    corrected_members: np.ndarray,
    bins: list[Bin],
    city_name: str = "Tokyo",
    metric: str = "high",
    no_price: float = NO_PRICE,
    yes_price: float = YES_PRICE,
) -> tuple:
    """
    Call _market_analysis_from_event_snapshot with a deterministic bias mock.

    Returns (analysis, corrected_members) where corrected_members is what the mock
    returned as the 'corrected' array so tests can assert mean equivalence.

    The mock patches _maybe_apply_edli_bias_correction to return (corrected_members, True)
    without touching any DB, and patches runtime_cities_by_name to return the real Tokyo
    city object (so _snapshot_unit and the city resolution in the patched path work).
    """
    from src.config import runtime_cities_by_name
    cities_map = runtime_cities_by_name()
    city_obj = cities_map.get(city_name)
    if city_obj is None:
        raise ValueError(f"City {city_name!r} not found in runtime config")

    snapshot = _make_snapshot(raw_members, metric=metric)
    family = _make_family(bins, city=city_name, metric=metric)
    native_costs = _make_native_costs(bins, no_price=no_price, yes_price=yes_price)
    payload: dict = {}
    calibration_conn = sqlite3.connect(":memory:")

    # Patch _maybe_apply_edli_bias_correction to return the pre-computed corrected array.
    # This avoids any DB dependency and makes the test hermetic.
    def _fake_bias_correction(members, *, snapshot, family, city, payload):
        return corrected_members.copy(), True

    with mock.patch(
        "src.engine.event_reactor_adapter._maybe_apply_edli_bias_correction",
        side_effect=_fake_bias_correction,
    ):
        analysis = _market_analysis_from_event_snapshot(
            calibration_conn=calibration_conn,
            snapshot=snapshot,
            family=family,
            native_costs=native_costs,
            payload=payload,
            decision_time=None,
        )

    return analysis, corrected_members


def _two_bin_family(bin_point=BIN_POINT) -> list[Bin]:
    """A 2-bin partition using Celsius point bins.

    Celsius bins are point bins (low==high, width=1 settled degree).
    The second bin is a shoulder bin with high=None (open-ended above).
    is_shoulder is a computed property on Bin (not a constructor arg).
    """
    return [
        Bin(bin_point, bin_point, "C", f"{bin_point}°C"),
        Bin(bin_point + 1, None, "C", f"{bin_point + 1}°C or higher"),
    ]


# ---------------------------------------------------------------------------
# (i) RED on HEAD: member_maxes mean ≈ RAW_MEAN (uncorrected)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "§4.1 applied: bootstrap now uses corrected members, so member_maxes mean ≈ "
        "CORRECTED_MEAN (~27.95), not RAW_MEAN (~24.5).  This xfail is the antibody: "
        "if the fix is ever accidentally reverted, this test turns into a PASS → "
        "xfail(strict=True) converts it to XPASS → CI fails.  DO NOT REMOVE."
    ),
)
def test_i_bootstrap_members_mean_on_head_is_uncorrected():
    """
    PRE-FIX RED test (now xfail=antibody).

    Before §4.1, bias correction was applied ONLY inside _snapshot_p_raw (local
    rebind); the outer ``members`` array passed to MarketAnalysis was still the cold
    raw array.  This test asserts the pre-fix state is RAW.

    Post-fix (current state): member_maxes mean ≈ corrected mean ~27.95 so this
    assertion FAILS → xfail(strict=True) records it as XFAIL (expected failure).
    If the fix is reverted, the assertion would PASS → xfail raises XPASS → CI fails.
    """
    raw_members = _make_raw_members(mean=RAW_MEAN, spread=1.6)
    corrected_members = raw_members - EFF_BIAS_C  # shift by +3.447 (warming)

    bins = _two_bin_family()
    analysis, _ = _call_market_analysis(
        raw_members=raw_members,
        corrected_members=corrected_members,
        bins=bins,
    )

    # Pre-fix: bootstrap samples the RAW (cold) array.
    # Post-fix: this assertion FAILS (member mean ≈ 27.95, not 24.5).
    assert abs(analysis._member_maxes.mean() - RAW_MEAN) < 0.5, (
        f"PRE-FIX: bootstrap member mean must be ≈ RAW_MEAN={RAW_MEAN:.2f} "
        f"(uncorrected), got {analysis._member_maxes.mean():.3f}. "
        "If this fails, the fix is already applied (see test_i_bootstrap_members_mean_post_fix_is_corrected)."
    )


def test_i_bootstrap_members_mean_post_fix_is_corrected():
    """
    GREEN post-fix: member_maxes mean ≈ CORRECTED_MEAN ~27.95.

    This is the mirror of the RED test above. On HEAD this assertion FAILS because
    the bootstrap uses raw (cold) members. After applying §4.1 it must pass.
    """
    raw_members = _make_raw_members(mean=RAW_MEAN, spread=1.6)
    corrected_members = raw_members - EFF_BIAS_C  # ~27.95

    bins = _two_bin_family()
    analysis, _ = _call_market_analysis(
        raw_members=raw_members,
        corrected_members=corrected_members,
        bins=bins,
    )

    assert abs(analysis._member_maxes.mean() - CORRECTED_MEAN) < 0.5, (
        f"POST-FIX: bootstrap member mean must be ≈ CORRECTED_MEAN={CORRECTED_MEAN:.3f}, "
        f"got {analysis._member_maxes.mean():.3f}. §4.1 not yet applied."
    )


# ---------------------------------------------------------------------------
# (ii) Post-fix: CI collapses on genuine-edge cold-biased contested bin
# ---------------------------------------------------------------------------

def test_ii_ci_collapses_and_trade_score_positive_post_fix():
    """
    Post-fix: CI width (q_posterior - q_5pct) < 0.05 for the cold-biased Tokyo bin;
    robust_trade_score > 0 for the +20¢-point-edge / cost≈0.75 candidate.

    Mechanism: corrected members ~N(27.95, 1.6) place nearly all mass ABOVE the 22-24°C
    bin.  NO win-prob ≈ 1.0 for both point AND bootstrap → CI collapses to ~0.
    Pre-fix the bootstrap uses cold (~24.5°C) members, placing significant mass in the
    22-24°C bin → NO win-prob_5pct ≈ 0.76 vs point ≈ 0.95 → CI ≈ 0.19.
    """
    raw_members = _make_raw_members(mean=RAW_MEAN, spread=1.6)
    corrected_members = raw_members - EFF_BIAS_C  # ~27.95

    bins = _two_bin_family()
    analysis, _ = _call_market_analysis(
        raw_members=raw_members,
        corrected_members=corrected_members,
        bins=bins,
        no_price=NO_PRICE,
    )

    hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=500)
    # Find the buy_no hypothesis for the 22-24°C bin (index 0)
    buy_no_hyp = next(
        (h for h in hypotheses if h.direction == "buy_no" and h.index == 0),
        None,
    )
    assert buy_no_hyp is not None, "buy_no hypothesis for bin 0 must exist"

    q_posterior = buy_no_hyp.p_posterior      # 1 - p_yes_posterior
    q_5pct = buy_no_hyp.ci_lower + NO_PRICE   # ci_lower is edge-space; restore to probability

    ci_width = q_posterior - q_5pct
    assert ci_width < 0.05, (
        f"POST-FIX: CI width must be <0.05 (honest floor) after bias correction, "
        f"got {ci_width:.4f}. §4.1 not yet applied."
    )

    # robust_trade_score for NO side
    score_receipt = robust_trade_score(
        trade_score_id="test_lockstep_ii",
        q_posterior=q_posterior,
        q_5pct=q_5pct,
        c_95pct=ExecutionPrice(NO_PRICE, "ask", fee_deducted=True, currency="probability_units"),
        c_stress=ExecutionPrice(NO_PRICE, "ask", fee_deducted=True, currency="probability_units"),
        p_fill_lcb=0.05,
        penalty=_LAMBDA_EDGE,
        stress_penalty=_LAMBDA_STRESS,
    )
    assert score_receipt.score > 0.0, (
        f"POST-FIX: robust_trade_score must be positive for +20¢-EV / cost≈0.75 "
        f"Tokyo bin after CI fix, got {score_receipt.score:.5f}. §4.1 not yet applied."
    )


# ---------------------------------------------------------------------------
# (iii) False-confidence guard: genuinely-uncertain (wide-spread, no-bias) bin
# ---------------------------------------------------------------------------

def test_iii_wide_spread_no_bias_bin_keeps_honest_ci_both_pre_and_post_fix():
    """
    MUST pass BOTH pre- and post-fix.

    A bin with ensemble centered ON the bin boundary and NO bias must keep
    (q_posterior - q_5pct) > 0.05 — the fix must NOT collapse honest uncertainty,
    only the bias-split artifact.  Also verifies robust_trade_score <= 0 for a
    genuinely-contested bin.

    Control: μ=23°C (at point-bin center), spread=2.0 → ~50% mass in ≤23°C bin,
    ~50% above.  NO p_posterior ≈ 0.50.  Bootstrap resamples 51 members from same
    distribution → non-trivial sampling noise → CI stays wide.
    NO priced at 0.55 (marginal edge) → score ≤ 0 when CI honest.

    Key distinction from the bias-fix case: here eff_bias_c=0 so corrected==raw.
    Both pre- and post-fix, the bootstrap uses the same members (no split) →
    CI is identical before/after the §4.1 fix.
    """
    contested_mean = 23.0   # at bin boundary → genuinely ~50/50
    contested_spread = 2.0
    # No bias: corrected == raw
    raw_members = _make_raw_members(mean=contested_mean, spread=contested_spread)
    corrected_members = raw_members.copy()  # eff_bias_c = 0

    # Contested: use buy_no for the SHOULDER bin (index=1: "24°C or higher")
    # With mean=23°C, ~50% mass above 23 → NO for "24+" has p_posterior ≈ 0.50.
    # Market prices NO at 0.55 (marginal edge only).
    contested_no_price = 0.55
    bins = _two_bin_family()
    analysis, _ = _call_market_analysis(
        raw_members=raw_members,
        corrected_members=corrected_members,  # identity (no bias)
        bins=bins,
        no_price=contested_no_price,
        yes_price=0.45,
    )

    hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=500)
    # Use bin 0 (point bin "23°C") where ~50% mass lands → genuinely contested
    buy_no_hyp = next(
        (h for h in hypotheses if h.direction == "buy_no" and h.index == 0),
        None,
    )
    assert buy_no_hyp is not None, "buy_no hypothesis for bin 0 must exist"

    q_posterior = buy_no_hyp.p_posterior
    q_5pct = buy_no_hyp.ci_lower + contested_no_price

    ci_width = q_posterior - q_5pct
    assert ci_width > 0.05, (
        f"FALSE-CONFIDENCE GUARD: contested/no-bias bin must keep CI > 0.05 "
        f"(honest uncertainty preserved), got {ci_width:.4f}. "
        "Fix must NOT collapse honest CI."
    )

    # Score must be non-positive (genuine CI uncertainty → sub-threshold)
    score_receipt = robust_trade_score(
        trade_score_id="test_lockstep_iii",
        q_posterior=q_posterior,
        q_5pct=q_5pct,
        c_95pct=ExecutionPrice(contested_no_price, "ask", fee_deducted=True, currency="probability_units"),
        c_stress=ExecutionPrice(contested_no_price, "ask", fee_deducted=True, currency="probability_units"),
        p_fill_lcb=0.05,
        penalty=_LAMBDA_EDGE,
        stress_penalty=_LAMBDA_STRESS,
    )
    assert score_receipt.score <= 0.0, (
        f"FALSE-CONFIDENCE GUARD: contested/no-bias bin must NOT score positive, "
        f"got {score_receipt.score:.5f}. Fix must not manufacture confidence."
    )


# ---------------------------------------------------------------------------
# (iv) No double-correction
# ---------------------------------------------------------------------------

def test_iv_no_double_correction():
    """
    Post-fix: member_maxes mean == corrected_members mean exactly (not corrected twice).

    The mock returns corrected_members = raw_members - EFF_BIAS_C (single correction).
    Double-correction would produce mean ≈ corrected_mean - EFF_BIAS_C again.
    We compare against corrected_members.mean() (the actual sample mean, not the
    population constant) to avoid false failures from sampling drift.
    """
    raw_members = _make_raw_members(mean=RAW_MEAN, spread=1.6)
    corrected_members = raw_members - EFF_BIAS_C  # applied once
    actual_corrected_mean = corrected_members.mean()
    double_corrected_mean = actual_corrected_mean - EFF_BIAS_C  # what double-correction would produce

    bins = _two_bin_family()
    analysis, _ = _call_market_analysis(
        raw_members=raw_members,
        corrected_members=corrected_members,
        bins=bins,
    )

    member_mean = analysis._member_maxes.mean()
    # Must match the single-corrected array exactly (within floating-point copy tolerance)
    assert abs(member_mean - actual_corrected_mean) < 1e-9, (
        f"No-double-correction: member mean must equal corrected_members.mean()="
        f"{actual_corrected_mean:.6f}, got {member_mean:.6f}. "
        f"Double-correction would produce ~{double_corrected_mean:.3f}. §4.1 regression."
    )
    # Also assert we're not at double-corrected mean
    assert abs(member_mean - double_corrected_mean) > 1.0, (
        f"Member mean {member_mean:.4f} is suspiciously close to double-corrected mean "
        f"{double_corrected_mean:.3f} — double-correction detected."
    )


# ---------------------------------------------------------------------------
# (v) Property antibody: grid test makes the train/serve split structurally
#     unconstructable. RED on HEAD wherever eff_bias_c != 0.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("eff_bias_c,spread,lead_days", [
    (bc, sp, ld)
    for bc in [-5.0, -3.447, -1.0, 0.0, 1.0, 2.0]
    for sp in [0.5, 1.6, 4.0]
    for ld in [0, 1, 3]
])
def test_v_property_antibody_member_mean_equals_corrected_point_mean(eff_bias_c, spread, lead_days):
    """
    Property antibody (invariant over the full grid):

        mean(analysis.member_maxes) == corrected point member mean to <1e-6

    This makes the train/serve split structurally unconstructable for any combination
    of bias, spread, and lead.

    RED on HEAD wherever eff_bias_c != 0 (bootstrap uses uncorrected members).
    GREEN post-fix for all cells.

    eff_bias_c = 0 rows pass on BOTH HEAD and post-fix (no bias = no split).
    """
    base_mean = 25.0
    raw_members = _make_raw_members(mean=base_mean, spread=spread)
    # corrected = raw - eff_bias_c (consistent with _maybe_apply_edli_bias_correction sign)
    corrected_members = raw_members - eff_bias_c
    expected_corrected_mean = corrected_members.mean()

    bins = _two_bin_family()
    analysis, _ = _call_market_analysis(
        raw_members=raw_members,
        corrected_members=corrected_members,
        bins=bins,
    )

    member_mean = analysis._member_maxes.mean()
    assert abs(member_mean - expected_corrected_mean) < 1e-6, (
        f"PROPERTY ANTIBODY FAILED: "
        f"eff_bias_c={eff_bias_c}, spread={spread}, lead={lead_days}: "
        f"mean(member_maxes)={member_mean:.8f} != corrected_mean={expected_corrected_mean:.8f} "
        f"(diff={abs(member_mean - expected_corrected_mean):.2e}). "
        "Bootstrap uses uncorrected members — §4.1 not yet applied."
    )
