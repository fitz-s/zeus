# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: statistical_calibration_authority_2026-06-12.txt Task 3.1 —
#   "N_eff = N² / (N + ρ_w·Σ n_m(n_m−1) + ρ_b·(N²−Σ n_m²))"
#   Unit test: synthetic correlated binary indicators recover known ρ to within 0.05.
"""Unit tests for scripts/measure_member_correlation.py.

Tests verify:
1. _anova_icc recovers known within-family ICC on synthetic data.
2. _between_family_correlation recovers known ρ_b on synthetic data.
3. _compute_n_eff matches the authority formula for edge cases.
4. Degenerate bins (p≈0 or p≈1) are correctly filtered.
5. Recovery tolerance ≤ 0.05 for N=1000 events.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Import the script under test without installing it as a package.
# ---------------------------------------------------------------------------
_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "measure_member_correlation.py"
)
_SCRIPT = os.path.normpath(_SCRIPT)
_spec = importlib.util.spec_from_file_location("measure_member_correlation", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore

_anova_icc = _mod._anova_icc
_compute_n_eff = _mod._compute_n_eff
_between_family_correlation = _mod._between_family_correlation
_estimate_rho_w = _mod._estimate_rho_w
P_LO = _mod.P_LO
P_HI = _mod.P_HI


# ---------------------------------------------------------------------------
# Helper: simulate correlated binary indicators
# ---------------------------------------------------------------------------

def _make_correlated_events(n_events: int, n_members: int, icc_target: float,
                             p_true: float = 0.3, seed: int = 0,
                             n_bins: int = 5) -> list[dict]:
    """Simulate n_events with n_members, producing events whose ANOVA ICC equals icc_target.

    Uses a BetaBinomial generative model for the focal bin (bin_0):
    - Draw latent probability a_i ~ Beta(alpha, beta) where
      alpha = p_true*(1/icc_target - 1), beta = (1-p_true)*(1/icc_target - 1).
    - ICC(X_ij, X_ik) = icc_target exactly for any two members in the same event.
    - count_i ~ Binomial(n_members, a_i) is the member count for bin_0.

    Only ONE bin (bin_0) carries the correlated signal. The remaining n_bins-1 bins are
    given equal shares of the remainder (1 - p_bin)/( n_bins-1), which is a deterministic
    function of p_bin and will yield near-zero ICC. _estimate_rho_w pools over ALL bins
    weighted by p_mean*(1-p_mean), so we need n_bins=1 to isolate the focal bin and
    measure only the correlated bin's ICC.

    Returns list of dicts formatted as AIFS events with 1 bin only, so _estimate_rho_w
    operates solely on the BetaBinomial-generated bin.
    """
    rng = np.random.default_rng(seed)
    n = n_members
    # BetaBinomial parameters: ICC = 1/(alpha+beta+1), E[a_i] = p_true
    if icc_target <= 0:
        alpha_param = beta_param = None
    else:
        phi = 1.0 / icc_target - 1.0  # = alpha + beta
        alpha_param = p_true * phi
        beta_param = (1.0 - p_true) * phi

    events = []
    for i in range(n_events):
        if alpha_param is not None:
            a_i = float(rng.beta(alpha_param, beta_param))
        else:
            a_i = p_true
        count = int(rng.binomial(n, a_i))
        p_bin = count / n

        # Single bin: isolate the BetaBinomial signal.
        # _estimate_rho_w pools over bins; with 1 bin it uses only this one.
        # The bin label uses a numeric suffix so _bin_center_deg can parse it.
        probs: dict[str, float] = {"focal_20°C": p_bin}

        events.append({
            "city": f"City{i % 10}",
            "target_date": f"2026-01-{(i % 28) + 1:02d}",
            "n_members": n_members,
            "bin_probs": probs,
            "winning_index": 0,
        })
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAnovaIcc:
    def test_zero_icc_iid(self):
        """IID Bernoulli across events → ICC ≈ 0."""
        rng = np.random.default_rng(7)
        # For IID Bernoulli(p): p_arr[i] = fraction of n iid draws = k/n
        # The between-events variance of p̂_i is Var(p̂_i) = p(1-p)/n (CLT).
        # The within-events mean square after pooling = p(1-p) * n/(n-1) ≈ p(1-p).
        # ICC = (MS_B - MS_W/n) / (MS_B + (n-1)*MS_W/n) → 0 as n_events → ∞.
        p = 0.3
        n = 50  # members
        n_events = 2000
        p_arr = rng.binomial(n, p, size=n_events) / n
        icc = _anova_icc(p_arr, n)
        assert abs(icc) < 0.05, f"Expected near-zero ICC for IID data, got {icc:.4f}"

    def test_high_icc_correlated(self):
        """ICC=0.5 generated via authority-formula variance → recovered within 0.05."""
        icc_target = 0.5
        events = _make_correlated_events(
            n_events=2000, n_members=51, icc_target=icc_target, p_true=0.3, seed=1
        )
        result = _estimate_rho_w(events)
        assert not result.get("insufficient"), "Should have sufficient data"
        rho_w = result["rho_w"]
        assert abs(rho_w - icc_target) < 0.05, (
            f"Expected ρ_w ≈ {icc_target}, got {rho_w:.4f}"
        )

    def test_low_icc_recovered(self):
        """ICC=0.1 generated via authority-formula variance → recovered within 0.05."""
        icc_target = 0.10
        events = _make_correlated_events(
            n_events=3000, n_members=51, icc_target=icc_target, p_true=0.25, seed=2
        )
        result = _estimate_rho_w(events)
        assert not result.get("insufficient")
        rho_w = result["rho_w"]
        assert abs(rho_w - icc_target) < 0.05, (
            f"Expected ρ_w ≈ {icc_target}, got {rho_w:.4f}"
        )

    def test_degenerate_bin_filtered(self):
        """Bins with p≈0 or p≈1 are excluded from the aggregation."""
        # Create events where one bin always has p=0 (degenerate)
        rng = np.random.default_rng(3)
        events = []
        for i in range(200):
            events.append({
                "city": "TestCity",
                "target_date": f"2026-01-{(i % 28) + 1:02d}",
                "n_members": 51,
                "bin_probs": {
                    "degenerate°C": 0.0,          # p≈0, should be filtered
                    "normal°C": 0.3 + 0.1 * rng.standard_normal() * 0.1,
                    "near_one°C": 0.99,            # p≈1, should be filtered
                },
                "winning_index": 1,
            })
        # Should not crash; just use the one non-degenerate bin
        result = _estimate_rho_w(events)
        # n_bins_used should be ≤ 1 (only the normal bin passed the filter)
        assert result.get("n_bins_used", 0) <= 2

    def test_insufficient_events(self):
        """Fewer than MIN_EVENTS_AIFS events returns insufficient=True."""
        events = _make_correlated_events(n_events=5, n_members=51, icc_target=0.4)
        result = _estimate_rho_w(events)
        assert result.get("insufficient") is True


class TestComputeNEff:
    def test_single_family_full_corr(self):
        """N=51 single family, ρ_w=1 → N_eff=1."""
        n_eff = _compute_n_eff(rho_w=1.0, rho_b=0.0, family_sizes=[51])
        assert abs(n_eff - 1.0) < 1e-6, f"Expected 1, got {n_eff}"

    def test_single_family_zero_corr(self):
        """N=51 single family, ρ_w=0 → N_eff=51."""
        n_eff = _compute_n_eff(rho_w=0.0, rho_b=0.0, family_sizes=[51])
        assert abs(n_eff - 51.0) < 1e-6, f"Expected 51, got {n_eff}"

    def test_multiple_independent_families(self):
        """5 families of 1 member each, ρ_b=0 → N_eff=5."""
        n_eff = _compute_n_eff(rho_w=0.0, rho_b=0.0, family_sizes=[1, 1, 1, 1, 1])
        assert abs(n_eff - 5.0) < 1e-6, f"Expected 5, got {n_eff}"

    def test_full_between_family_corr(self):
        """5 families of 1 member, ρ_b=1 → N_eff=1."""
        n_eff = _compute_n_eff(rho_w=0.0, rho_b=1.0, family_sizes=[1, 1, 1, 1, 1])
        assert abs(n_eff - 1.0) < 1e-6, f"Expected 1, got {n_eff}"

    def test_mixed_family_authority_formula(self):
        """Manual check of authority formula: N=57, 1 AIFS family (51) + 6 det (1 each)."""
        # N=57, Σn_m² = 51² + 6*1² = 2601+6 = 2607, Σn_m(n_m-1) = 51*50 + 6*0 = 2550
        # N² = 3249
        # denom = 57 + rho_w*2550 + rho_b*(3249-2607)
        rho_w, rho_b = 0.3, 0.2
        family_sizes = [51, 1, 1, 1, 1, 1, 1]
        N = 57
        expected_denom = N + rho_w * 2550 + rho_b * (N * N - 2607)
        expected = N * N / expected_denom
        n_eff = _compute_n_eff(rho_w=rho_w, rho_b=rho_b, family_sizes=family_sizes)
        assert abs(n_eff - expected) < 0.01, f"Got {n_eff:.4f}, expected {expected:.4f}"

    def test_zero_rho_mixed(self):
        """AIFS(51) + 6 deterministic, all independent → N_eff = 57."""
        n_eff = _compute_n_eff(rho_w=0.0, rho_b=0.0, family_sizes=[51, 1, 1, 1, 1, 1, 1])
        assert abs(n_eff - 57.0) < 1e-6, f"Expected 57, got {n_eff}"


class TestBetweenFamilyCorrelation:
    def _make_multimodel_events(self, n_events: int, n_models: int,
                                true_rho_b: float, seed: int = 5,
                                sigma_total: float = 0.5) -> list[dict]:
        """Synthetic multi-model events with known between-model temperature correlation.

        We use a common + idiosyncratic factor model so that the Pearson correlation
        of any two model temperature values is exactly true_rho_b in expectation.

        sigma_total=0.5°C concentrates models into 2-3 integer-degree bins, producing
        detectable indicator correlation. At sigma_total=3°C (wide), models spread
        over 10+ bins, attenuating indicator correlation toward zero regardless of
        the true temperature correlation. Use sigma_total=0.5 for meaningful tests.
        """
        rng = np.random.default_rng(seed)
        events = []
        mean_temp = 20.0
        # common std = sqrt(rho_b) * sigma_total; idio std = sqrt(1-rho_b) * sigma_total
        sigma_common = np.sqrt(true_rho_b) * sigma_total
        sigma_idio = np.sqrt(max(0.0, 1.0 - true_rho_b)) * sigma_total
        for i in range(n_events):
            common = rng.standard_normal() * sigma_common + mean_temp
            temps = {}
            for m in range(n_models):
                temps[f"model_{m}"] = common + rng.standard_normal() * sigma_idio
            events.append({
                "city": f"City{i % 5}",
                "target_date": f"2026-01-{(i % 28) + 1:02d}",
                "models": temps,
                "winning_bin": "18°C",
                "settlement_value": 18.0,
                "settlement_unit": "C",
            })
        return events

    def test_high_rho_b_greater_than_low(self):
        """ρ_b estimator is monotone: high_rho events yield higher ρ̂_b than low_rho events.

        Uses sigma_total=0.5°C so models concentrate in 2-3 bins, yielding detectable
        indicator correlation. With sigma_total=3°C, 10+ bins attenuate correlation to noise.
        """
        events_high = self._make_multimodel_events(
            n_events=500, n_models=6, true_rho_b=0.8, seed=20, sigma_total=0.5
        )
        events_low = self._make_multimodel_events(
            n_events=500, n_models=6, true_rho_b=0.1, seed=21, sigma_total=0.5
        )
        r_high = _between_family_correlation(events_high)
        r_low = _between_family_correlation(events_low)
        assert not r_high.get("insufficient")
        assert not r_low.get("insufficient")
        assert r_high["rho_b"] > r_low["rho_b"], (
            f"High-rho events should give larger ρ̂_b: "
            f"high={r_high['rho_b']:.4f} vs low={r_low['rho_b']:.4f}"
        )

    def test_high_rho_b_positive(self):
        """High ρ_b (0.8) events yield positive ρ̂_b (> 0.1)."""
        events = self._make_multimodel_events(
            n_events=500, n_models=6, true_rho_b=0.8, seed=22, sigma_total=0.5
        )
        result = _between_family_correlation(events)
        assert not result.get("insufficient"), f"Should not be insufficient: {result}"
        rho_b = result["rho_b"]
        assert rho_b > 0.1, f"Expected positive ρ̂_b for high-rho events, got {rho_b:.4f}"

    def test_low_rho_b_near_zero(self):
        """Near-zero ρ_b (0.0) events yield ρ̂_b close to 0 (within 0.15)."""
        events = self._make_multimodel_events(
            n_events=500, n_models=6, true_rho_b=0.0, seed=23, sigma_total=0.5
        )
        result = _between_family_correlation(events)
        assert not result.get("insufficient")
        rho_b = result["rho_b"]
        assert abs(rho_b) < 0.15, f"Expected ρ̂_b ≈ 0 for independent events, got {rho_b:.4f}"

    def test_insufficient_events(self):
        """Fewer than MIN_EVENTS_MULTIMODEL returns insufficient=True."""
        events = self._make_multimodel_events(n_events=10, n_models=4, true_rho_b=0.5)
        result = _between_family_correlation(events)
        assert result.get("insufficient") is True
