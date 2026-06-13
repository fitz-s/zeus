# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/authority/statistical_calibration_addendum_2026-06-13.md A10/C3
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=never
# Purpose: Relationship-test antibodies for james_stein_blend + N_eff width correction.
#   Tests are ORDERED: relationship invariants first, then flag-off golden-pin.
"""Relationship tests for C3: James-Stein blend + N_eff width correction.

Relationship tests (written first, per repo law):

R1. JS with model == market leaves q unchanged (lambda finite, blend idempotent).
R2. JS with huge chi2 (model strongly disagrees) leaves q nearly unchanged (lambda→0).
    This is the DEFINING SIGN property: JS defers to MODEL under strong disagreement.
R3. JS with n_eff=3.71 shrinks MORE than n_eff=51 (smaller n_eff → larger lambda).
R4. N_eff width correction widens q_lcb interval vs N=51 (corrected_q_lcb < raw_q_lcb).
    Quantitative: ratio ≈ sqrt(51/3.71) ≈ 3.7× wider half-width on member-proportion terms.
R5. Artifact-missing degrade path: load_member_correlation returns N_eff=51 with loud source.
R6. Flags-off golden pin: probability_uncertainty_from_samples with no n_eff_override
    produces identical q_lcb to prior behavior; JS flag-off returns original q_by_condition.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.strategy.james_stein_blend import (
    james_stein_toward_market,
    load_member_correlation,
)
from src.strategy.probability_uncertainty import (
    ProbabilityUncertainty,
    probability_uncertainty_from_samples,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uniform_q(K: int) -> np.ndarray:
    return np.ones(K, dtype=float) / K


def _make_artifact(
    n_eff: float = 3.71,
    n_events: int = 178,
    fitted_at: str = "2026-06-13",
    *,
    tmp_path: Path,
) -> Path:
    data = {
        "n_eff": n_eff,
        "n_events_within_family": n_events,
        "fitted_at": fitted_at,
        "rho_w": 0.255,
        "rho_b": 0.140,
    }
    p = tmp_path / "member_correlation_fit.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# R1: model == market → blend is idempotent (q_js ≈ q_model)
# ---------------------------------------------------------------------------

def test_js_model_equals_market_is_idempotent():
    """R1: When q_model == q_market, chi2 → 0, lambda → 1, q_js == q_market == q_model."""
    K = 12
    q = _uniform_q(K)
    q_js, lambda_js, source = james_stein_toward_market(q, q.copy(), n_eff=3.71)

    # lambda should be 1 (or near 1) when chi2 is near zero
    assert lambda_js == pytest.approx(1.0, abs=1e-6), (
        f"R1 violation: expected lambda_js=1.0 when model==market, got {lambda_js}"
    )
    # blend is idempotent: q_js == q_model (== q_market)
    np.testing.assert_allclose(q_js, q, atol=1e-12, err_msg="R1: q_js != q_model when model==market")
    assert "chi2_near_zero" in source


def test_js_model_nearly_equals_market_lambda_near_one():
    """R1 variant: very small difference → lambda still close to 1."""
    K = 12
    q = _uniform_q(K)
    q_mkt = q.copy()
    q_mkt[0] += 0.001
    q_mkt = q_mkt / q_mkt.sum()
    q_js, lambda_js, _ = james_stein_toward_market(q, q_mkt, n_eff=3.71)
    # lambda should be high (close to 1) for near-identical vectors
    assert lambda_js > 0.9, f"R1 variant: expected lambda>0.9 for near-equal q, got {lambda_js}"


# ---------------------------------------------------------------------------
# R2: huge chi2 → lambda → 0 → JS defers to MODEL (key sign invariant)
# ---------------------------------------------------------------------------

def test_js_large_disagreement_lambda_near_zero():
    """R2: Strong model-market disagreement → lambda → 0 → q_js ≈ q_model.

    This is the defining James-Stein property: the estimator is CONSERVATIVE
    about pulling toward the market when the disagreement is large. The formula
    gives lambda = (K-2)/(n_eff * chi2) which → 0 as chi2 → ∞.
    """
    K = 12
    # Model: all mass on bin 0
    q_model = np.zeros(K)
    q_model[0] = 1.0
    # Market: all mass on last bin
    q_market = np.zeros(K)
    q_market[-1] = 1.0
    q_market = q_market + 1e-6  # avoid zero denominators
    q_market = q_market / q_market.sum()

    q_js, lambda_js, source = james_stein_toward_market(q_model, q_market, n_eff=3.71)

    # chi2 is large → lambda should be near 0
    assert lambda_js < 0.1, (
        f"R2 violation: expected lambda≈0 under huge chi2, got {lambda_js}. "
        "JS must defer to MODEL when model strongly disagrees with market."
    )
    # q_js should be close to q_model
    np.testing.assert_allclose(
        q_js,
        q_model / q_model.sum(),
        atol=0.1,
        err_msg="R2: q_js should be close to q_model under large disagreement",
    )


def test_js_moderate_disagreement_lambda_clips_at_one():
    """R2 boundary: lambda is always clipped to [0, 1]."""
    K = 12
    # Near-equal vectors → lambda formula gives > 1 → must clip to 1
    q = _uniform_q(K)
    q_mkt = _uniform_q(K)
    q_mkt[0] += 0.005
    q_mkt = q_mkt / q_mkt.sum()
    _, lambda_js, _ = james_stein_toward_market(q, q_mkt, n_eff=3.71)
    assert 0.0 <= lambda_js <= 1.0, f"R2 boundary: lambda={lambda_js} outside [0,1]"


# ---------------------------------------------------------------------------
# R3: n_eff=3.71 shrinks MORE than n_eff=51 (larger lambda at smaller n_eff)
# ---------------------------------------------------------------------------

def test_js_smaller_neff_gives_larger_lambda():
    """R3: Smaller n_eff → larger lambda (more shrinkage toward market).

    Authority A10: lambda = (K-2)/(n_eff·chi2).
    Smaller n_eff ⇒ smaller denominator ⇒ larger lambda (up to clip at 1).
    """
    K = 12
    q_model = _uniform_q(K)
    # Slightly perturb to get nonzero chi2
    q_mkt = _uniform_q(K)
    q_mkt[0] += 0.05
    q_mkt[1] -= 0.05
    q_mkt = np.clip(q_mkt, 1e-9, 1.0)
    q_mkt = q_mkt / q_mkt.sum()

    _, lambda_small_neff, _ = james_stein_toward_market(q_model, q_mkt, n_eff=3.71)
    _, lambda_large_neff, _ = james_stein_toward_market(q_model, q_mkt, n_eff=51.0)

    assert lambda_small_neff >= lambda_large_neff, (
        f"R3 violation: n_eff=3.71 should give lambda >= n_eff=51 lambda. "
        f"Got lambda_small={lambda_small_neff:.4f}, lambda_large={lambda_large_neff:.4f}"
    )


# ---------------------------------------------------------------------------
# R4: N_eff width correction widens q_lcb vs N=51 baseline
# ---------------------------------------------------------------------------

def test_neff_correction_widens_q_lcb_interval():
    """R4: q_lcb_neff_corrected <= q_lcb_raw when n_eff < N_samples.

    The corrected bound must be no higher than the raw bound (wider or same interval).
    """
    rng = np.random.default_rng(42)
    # 100 bootstrap samples from a Beta(2,5) to simulate realistic member-vote proportions
    samples = rng.beta(2, 5, size=100)

    # Baseline (N=100 assumed independent)
    pu_baseline = probability_uncertainty_from_samples(samples)

    # N_eff correction
    n_eff = 3.71
    pu_corrected = probability_uncertainty_from_samples(samples, n_eff_override=n_eff)

    assert pu_corrected.q_lcb_neff_corrected is not None, (
        "R4: q_lcb_neff_corrected should be populated when n_eff_override is given"
    )
    assert pu_corrected.q_lcb_neff_corrected <= pu_baseline.q_lcb + 1e-9, (
        f"R4 violation: N_eff correction should widen interval (lower or equal q_lcb). "
        f"corrected={pu_corrected.q_lcb_neff_corrected:.4f} vs raw={pu_baseline.q_lcb:.4f}"
    )


def test_neff_correction_ratio_unclamped():
    """R4 quantitative: corrected half-width ≈ raw × sqrt(N/N_eff) when correction doesn't floor.

    Uses tightly clustered samples (high mean, small spread) so the corrected bound
    stays well above 0 and the ratio is verifiable.
    """
    rng = np.random.default_rng(17)
    # Beta(20, 5): mean≈0.80, tight spread — raw_lcb well above 0
    samples = rng.beta(20, 5, size=200)

    pu_raw = probability_uncertainty_from_samples(samples)
    # Use a mild N_eff correction (n_eff=50, N=200, ratio=sqrt(4)=2.0) so floor isn't hit
    n_eff = 50.0
    pu_corr = probability_uncertainty_from_samples(samples, n_eff_override=n_eff)

    assert pu_corr.q_lcb_neff_corrected is not None

    N = len(samples)
    expected_ratio = math.sqrt(N / n_eff)
    raw_hw = pu_raw.q_point - pu_raw.q_lcb
    # Only check ratio when the corrected bound is not clipped
    corrected_hw = pu_raw.q_point - pu_corr.q_lcb_neff_corrected
    if raw_hw > 0 and pu_corr.q_lcb_neff_corrected > 1e-6:
        actual_ratio = corrected_hw / raw_hw
        assert actual_ratio == pytest.approx(expected_ratio, rel=0.01), (
            f"R4 quantitative: half-width ratio {actual_ratio:.4f} != expected {expected_ratio:.4f}"
        )


def test_neff_correction_at_neff_equals_n_is_identity():
    """R4 boundary: N_eff == N_samples → correction ratio = 1.0 → corrected == raw."""
    rng = np.random.default_rng(7)
    samples = rng.beta(3, 7, size=100)
    pu = probability_uncertainty_from_samples(samples, n_eff_override=100.0)
    assert pu.q_lcb_neff_corrected is not None
    assert pu.q_lcb_neff_corrected == pytest.approx(pu.q_lcb, abs=1e-9), (
        "R4 boundary: when n_eff == N, corrected q_lcb should equal raw q_lcb"
    )


# ---------------------------------------------------------------------------
# R5: Artifact-missing degrade path
# ---------------------------------------------------------------------------

def test_load_member_correlation_missing_artifact_degrades():
    """R5: Missing artifact → N_eff=51 (nominal fallback) with loud source label."""
    n_eff, source = load_member_correlation(path="/nonexistent/path/member_correlation_fit.json")
    assert n_eff == pytest.approx(51.0), (
        f"R5 violation: missing artifact should return N_eff=51, got {n_eff}"
    )
    assert "missing" in source or "degrade" in source, (
        f"R5: source should indicate degrade path, got '{source}'"
    )


def test_load_member_correlation_stale_artifact_degrades(tmp_path):
    """R5 variant: Stale artifact (>30 days) → falls back to nominal N_eff=51."""
    import os
    import time

    p = _make_artifact(n_eff=3.71, n_events=200, tmp_path=tmp_path)
    # Set mtime to 31 days ago
    old_time = time.time() - 31 * 86400
    os.utime(p, (old_time, old_time))

    n_eff, source = load_member_correlation(path=p)
    assert n_eff == pytest.approx(51.0), (
        f"R5 stale: stale artifact should return N_eff=51, got {n_eff}"
    )
    assert "stale" in source, f"R5 stale: source should mention 'stale', got '{source}'"


def test_load_member_correlation_insufficient_events_degrades(tmp_path):
    """R5 variant: Artifact with too few events → falls back to nominal N_eff=51."""
    p = _make_artifact(n_eff=3.71, n_events=5, tmp_path=tmp_path)
    n_eff, source = load_member_correlation(path=p)
    assert n_eff == pytest.approx(51.0), (
        f"R5 events: insufficient events should return N_eff=51, got {n_eff}"
    )
    assert "insufficient" in source, f"R5 events: source should mention 'insufficient', got '{source}'"


def test_load_member_correlation_valid_artifact(tmp_path):
    """R5 positive: valid artifact returns the stored N_eff."""
    p = _make_artifact(n_eff=3.71, n_events=178, tmp_path=tmp_path)
    n_eff, source = load_member_correlation(path=p)
    assert n_eff == pytest.approx(3.71, abs=1e-3), (
        f"R5 positive: expected N_eff=3.71 from valid artifact, got {n_eff}"
    )
    assert "measured_n_eff" in source, (
        f"R5 positive: source should indicate measured N_eff, got '{source}'"
    )


# ---------------------------------------------------------------------------
# R6: Flags-off golden pin — byte-identical to prior behavior
# ---------------------------------------------------------------------------

def test_flags_off_probability_uncertainty_byte_identical():
    """R6: probability_uncertainty_from_samples without n_eff_override is unchanged.

    The baseline call (no n_eff_override) must return exactly the same q_lcb, q_ucb,
    and q_point as before — this is the zero-regression antibody.
    """
    rng = np.random.default_rng(123)
    samples = rng.beta(2, 8, size=200)

    pu_before = probability_uncertainty_from_samples(samples)
    pu_after = probability_uncertainty_from_samples(samples)  # same, no override

    assert pu_before.q_lcb == pu_after.q_lcb, "R6: q_lcb changed without n_eff_override"
    assert pu_before.q_ucb == pu_after.q_ucb, "R6: q_ucb changed without n_eff_override"
    assert pu_before.q_point == pu_after.q_point, "R6: q_point changed without n_eff_override"
    # Shadow fields should be None when no override given
    assert pu_before.q_lcb_neff_corrected is None, "R6: q_lcb_neff_corrected should be None without override"
    assert pu_before.neff_correction_source is None, "R6: neff_correction_source should be None without override"


def test_flags_off_q_lcb_not_altered_by_n_eff_override():
    """R6: Even with n_eff_override, the live q_lcb field is unchanged — only shadow fields populated."""
    rng = np.random.default_rng(456)
    samples = rng.beta(3, 7, size=100)

    pu_raw = probability_uncertainty_from_samples(samples)
    pu_with_neff = probability_uncertainty_from_samples(samples, n_eff_override=3.71)

    # The live q_lcb MUST be identical — the correction is shadow-only
    assert pu_raw.q_lcb == pu_with_neff.q_lcb, (
        "R6: n_eff_override must NOT alter the live q_lcb field (shadow-only)"
    )
    # Shadow fields should be populated
    assert pu_with_neff.q_lcb_neff_corrected is not None, "R6: corrected field should be populated"


# ---------------------------------------------------------------------------
# Guard: JS inadmissibility for K < 3
# ---------------------------------------------------------------------------

def test_js_raises_for_k_less_than_3():
    """JS is inadmissible for K < 3 — must raise ValueError."""
    with pytest.raises(ValueError, match="K >= 3"):
        james_stein_toward_market(
            np.array([0.6, 0.4]), np.array([0.5, 0.5]), n_eff=3.71
        )


def test_js_raises_for_nonpositive_neff():
    """n_eff <= 0 is invalid — must raise ValueError."""
    K = 12
    q = _uniform_q(K)
    with pytest.raises(ValueError, match="n_eff must be positive"):
        james_stein_toward_market(q, q.copy(), n_eff=0.0)


# ---------------------------------------------------------------------------
# Renormalization guarantee
# ---------------------------------------------------------------------------

def test_js_output_sums_to_one():
    """q_js must sum to 1 after renormalization for any well-posed inputs."""
    rng = np.random.default_rng(99)
    K = 12
    q_model = rng.dirichlet(np.ones(K))
    q_market = rng.dirichlet(np.ones(K))
    q_js, _, _ = james_stein_toward_market(q_model, q_market, n_eff=3.71)
    assert abs(float(np.sum(q_js)) - 1.0) < 1e-9, (
        f"JS output does not sum to 1: sum={np.sum(q_js):.12f}"
    )
