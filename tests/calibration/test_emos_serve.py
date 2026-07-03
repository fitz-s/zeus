# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS predictive calibration serve logic.
#   EMOS predictive model: mu=a+b*xbar, sigma2=exp(c+d*logS2+e*lead_days).
"""RED tests for EMOS calibrator serve logic (src/calibration/emos.py).

Four invariants:
(a) predictive math for an emos cell returns correct (mu_c, sigma_c).
(b) None for a served=raw cell ("Seattle|DJF").
(c) None for a missing cell.
(d) bin_probability: finite bins + open shoulders sum to ~1 over a MECE family.
(e) unit handling: F-city converts mu_c→F before bin_prob on degF bins.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# (a) predictive math for an emos cell
# ---------------------------------------------------------------------------

class TestEmosPredictive:

    def test_emos_cell_math(self):
        """Given known params and members, emos_predictive returns correct (mu_c, sigma_c)."""
        from src.calibration.emos import emos_predictive

        # Amsterdam|DJF params: [a, b, c, d, e]
        # a=-0.26296, b=1.09351, c=0.59283, d=0.63219, e=0.00771
        a, b, c, d, e = -0.26296, 1.09351, 0.59283, 0.63219, 0.00771
        members_c = np.array([5.0, 6.0, 7.0, 8.0, 9.0], dtype=float)
        xbar = float(np.mean(members_c))
        s2 = float(np.var(members_c, ddof=1))
        lead_days = 3.0

        expected_mu = a + b * xbar
        expected_sigma = math.sqrt(math.exp(c + d * math.log(s2) + e * lead_days))

        result = emos_predictive("Amsterdam", "DJF", lead_days, members_c)

        assert result is not None, "emos cell must return (mu_c, sigma_c), not None"
        mu_c, sigma_c = result
        assert abs(mu_c - expected_mu) < 1e-9, f"mu_c mismatch: {mu_c} vs {expected_mu}"
        assert abs(sigma_c - expected_sigma) < 1e-9, f"sigma_c mismatch: {sigma_c} vs {expected_sigma}"

    def test_emos_cell_positive_sigma(self):
        """sigma must be positive (exp(...) > 0 always)."""
        from src.calibration.emos import emos_predictive

        members_c = np.linspace(10.0, 20.0, 51)
        result = emos_predictive("Amsterdam", "JJA", lead_days=5.0, members_c=members_c)
        assert result is not None
        _, sigma_c = result
        assert sigma_c > 0.0


# ---------------------------------------------------------------------------
# (b) None for a served=raw cell
# ---------------------------------------------------------------------------

class TestEmosRawCellReturnsNone:

    def test_seattle_djf_is_raw_returns_none(self):
        """Seattle|DJF is served='raw' — emos_predictive must return None."""
        from src.calibration.emos import emos_predictive

        members_c = np.array([2.0, 3.0, 4.0, 5.0], dtype=float)
        result = emos_predictive("Seattle", "DJF", lead_days=2.0, members_c=members_c)
        assert result is None, "served=raw cell must return None"


# ---------------------------------------------------------------------------
# (c) None for a missing cell
# ---------------------------------------------------------------------------

class TestEmosMissingCellReturnsNone:

    def test_missing_city_returns_none(self):
        """City not in table → None."""
        from src.calibration.emos import emos_predictive

        members_c = np.array([20.0, 21.0, 22.0], dtype=float)
        result = emos_predictive("NonExistentCity", "JJA", lead_days=1.0, members_c=members_c)
        assert result is None

    def test_missing_season_for_known_city_returns_none(self):
        """Known city but absent season key → None (fail-closed)."""
        from src.calibration.emos import emos_predictive

        # Amsterdam exists but "XYZ" is not a valid season key
        members_c = np.array([10.0, 11.0], dtype=float)
        result = emos_predictive("Amsterdam", "XYZ", lead_days=1.0, members_c=members_c)
        assert result is None


# ---------------------------------------------------------------------------
# (d) bin_probability over MECE family sums to ~1
# ---------------------------------------------------------------------------

class TestBinProbabilityMECE:

    def _celsius_mece_bins(self):
        """5 bins: (-inf,10), [10,15), [15,20), [20,25), [25,+inf)."""
        return [
            (None, 10.0),
            (10.0, 15.0),
            (15.0, 20.0),
            (20.0, 25.0),
            (25.0, None),
        ]

    def test_mece_family_sums_to_one(self):
        """bin_probability over a MECE 5-bin family must sum to 1.0."""
        from src.calibration.emos import bin_probability

        mu, sigma = 18.0, 3.0
        bins = self._celsius_mece_bins()
        total = sum(bin_probability(mu, sigma, lo, hi) for lo, hi in bins)
        assert abs(total - 1.0) < 1e-9, f"MECE family sum={total} not ~1"

    def test_open_low_shoulder(self):
        """open-low shoulder: low=None → Φ((high-mu)/sigma) - 0."""
        from src.calibration.emos import bin_probability
        from scipy.stats import norm

        mu, sigma = 18.0, 3.0
        p = bin_probability(mu, sigma, None, 10.0)
        expected = float(norm.cdf((10.0 - mu) / sigma))
        assert abs(p - expected) < 1e-12

    def test_open_high_shoulder(self):
        """open-high shoulder: high=None → 1 - Φ((low-mu)/sigma)."""
        from src.calibration.emos import bin_probability
        from scipy.stats import norm

        mu, sigma = 18.0, 3.0
        p = bin_probability(mu, sigma, 25.0, None)
        expected = float(1.0 - norm.cdf((25.0 - mu) / sigma))
        assert abs(p - expected) < 1e-12

    def test_finite_bin(self):
        """Finite bin: Φ((high-mu)/sigma) - Φ((low-mu)/sigma)."""
        from src.calibration.emos import bin_probability
        from scipy.stats import norm

        mu, sigma = 18.0, 3.0
        lo, hi = 15.0, 20.0
        p = bin_probability(mu, sigma, lo, hi)
        expected = float(norm.cdf((hi - mu) / sigma) - norm.cdf((lo - mu) / sigma))
        assert abs(p - expected) < 1e-12


# ---------------------------------------------------------------------------
# bin_probability_settlement: settlement-aware bin probability (wmo_half_up)
# Antibody for the degenerate point-bin bug (bin_low==bin_high→ empty interval→0).
# ---------------------------------------------------------------------------

class TestBinProbabilitySettlement:
    """bin_probability_settlement expands point bins to [X−0.5, X+0.5) before
    integrating, matching the WMO round-half-up convention used by the live MC path.

    Critical invariant: a MECE family of settlement bins (integer-labeled point bins
    with open shoulders) must SUM to ~1.0.  Before this fix, every interior bin
    produced 0 because bin_probability([X,X]) = Φ(X) - Φ(X) = 0.
    """

    def test_interior_point_bin_nonzero(self):
        """Interior point bin [21, 21] must produce non-zero probability."""
        from src.calibration.emos import bin_probability_settlement

        # SãoPaulo fixture: raw_mu_c≈21.5, sigma≈2.0, bin=21°C
        p = bin_probability_settlement(21.5, 2.0, 21.0, 21.0)
        assert p > 0.0, f"Interior point bin must be non-zero, got {p}"
        assert p < 1.0

    def test_interior_bin_matches_expansion(self):
        """[X, X] → integrates [X−0.5, X+0.5), matching manual expansion."""
        from src.calibration.emos import bin_probability_settlement
        from scipy.stats import norm

        mu, sigma = 21.5, 2.0
        X = 21.0
        expected = float(norm.cdf((X + 0.5 - mu) / sigma) - norm.cdf((X - 0.5 - mu) / sigma))
        got = bin_probability_settlement(mu, sigma, X, X)
        assert abs(got - expected) < 1e-12, f"expected={expected}, got={got}"

    def test_mece_point_bins_sum_to_one(self):
        """MECE family of settlement point bins + shoulders must sum to ~1.0.

        This is the ANTIBODY for the degenerate-bin category:
        before the fix, interior bins all returned 0 → sum ≈ 0.
        """
        from src.calibration.emos import bin_probability_settlement

        # Realistic settlement family: shoulder(≤18), 19, 20, 21, 22, 23, shoulder(≥24)
        mu, sigma = 21.5, 2.0
        # Bins: open-low shoulder = (None, 18), interior = [X,X] for X in 19..23, open-high shoulder = (24, None)
        bins = [
            (None, 18.0),   # open-low: all x rounding to ≤ 18
            (19.0, 19.0),
            (20.0, 20.0),
            (21.0, 21.0),
            (22.0, 22.0),
            (23.0, 23.0),
            (24.0, None),   # open-high: all x rounding to ≥ 24
        ]
        total = sum(bin_probability_settlement(mu, sigma, lo, hi) for lo, hi in bins)
        assert abs(total - 1.0) < 5e-3, (
            f"MECE settlement family must sum to ~1.0, got {total:.6f}. "
            f"If this fails, interior bins are returning 0 (degenerate-bin bug)."
        )

    def test_open_low_shoulder_settlement(self):
        """Open-low shoulder (None, X): integrates (−∞, X+0.5)."""
        from src.calibration.emos import bin_probability_settlement
        from scipy.stats import norm

        mu, sigma = 21.5, 2.0
        X = 18.0
        expected = float(norm.cdf((X + 0.5 - mu) / sigma))
        got = bin_probability_settlement(mu, sigma, None, X)
        assert abs(got - expected) < 1e-12

    def test_open_high_shoulder_settlement(self):
        """Open-high shoulder (X, None): integrates [X−0.5, +∞)."""
        from src.calibration.emos import bin_probability_settlement
        from scipy.stats import norm

        mu, sigma = 21.5, 2.0
        X = 24.0
        expected = float(1.0 - norm.cdf((X - 0.5 - mu) / sigma))
        got = bin_probability_settlement(mu, sigma, X, None)
        assert abs(got - expected) < 1e-12

    def test_saopaulao_bin21_fixture(self):
        """SãoPaulo fixture: bin=21°C, mu≈21.50°C → emos_q≈0.140 (not 0)."""
        from src.calibration.emos import bin_probability_settlement

        # raw_mu_c=21.50°C (causal would be 28.06 but for unit-testing the formula,
        # we test the math directly with a μ near the bin)
        p = bin_probability_settlement(21.5, 2.0, 21.0, 21.0)
        # Should be roughly Φ(0.25) − Φ(−0.25) ≈ 0.197
        assert p > 0.10, f"SãoPaulo bin=21 near μ=21.5 must be substantial, got {p:.4f}"
        assert p < 0.50

    def test_never_zero_for_moderate_sigma(self):
        """No bin in a family should produce exactly 0 for reasonable σ."""
        from src.calibration.emos import bin_probability_settlement

        mu, sigma = 21.5, 2.0
        bins = [(None, 18.0), (19.0, 19.0), (20.0, 20.0), (21.0, 21.0),
                (22.0, 22.0), (23.0, 23.0), (24.0, None)]
        for lo, hi in bins:
            p = bin_probability_settlement(mu, sigma, lo, hi)
            assert p > 0.0, f"bin [{lo}, {hi}] must be > 0 for σ=2, got {p}"


# ---------------------------------------------------------------------------
# (e) unit handling: F-city bin_probability on °F bins
# ---------------------------------------------------------------------------

class TestEmosFCityUnitConversion:
    """For an F-city: emos_predictive returns (mu_c, sigma_c) in °C.
    bin_probability on °F bins requires converting mu_c→F, sigma_c * 1.8.
    Caller is responsible for the conversion; emos_predictive is unit-agnostic.
    We test the expected caller pattern: bins in °F, convert mu/sigma to °F.
    """

    def test_f_city_bin_prob_on_degF_bins_sums_to_one(self):
        """For an F-city: convert mu_c and sigma_c to °F before calling bin_probability
        on °F bins — sum must be ~1 over MECE bins in °F.
        """
        from src.calibration.emos import bin_probability

        # Simulate emos_predictive returning (mu_c=20.0°C, sigma_c=2.0°C)
        mu_c, sigma_c = 20.0, 2.0
        mu_f = mu_c * 9.0 / 5.0 + 32.0   # = 68.0°F
        sigma_f = sigma_c * 9.0 / 5.0     # = 3.6°F

        # MECE bins in °F: (-inf,65), [65,70), [70,75), [75,+inf)
        bins_f = [
            (None, 65.0),
            (65.0, 70.0),
            (70.0, 75.0),
            (75.0, None),
        ]
        total = sum(bin_probability(mu_f, sigma_f, lo, hi) for lo, hi in bins_f)
        assert abs(total - 1.0) < 1e-9, f"F-city MECE sum={total}"

    def test_f_city_seattle_djf_is_raw_pattern(self):
        """Seattle|DJF is raw so the caller falls back to raw ensemble.
        Confirm emos_predictive returns None, signalling raw fallback.
        """
        from src.calibration.emos import emos_predictive

        # San Francisco or Seattle are F cities in raw service
        members_c = np.array([10.0, 11.0, 12.0], dtype=float)
        result = emos_predictive("Seattle", "DJF", lead_days=3.0, members_c=members_c)
        assert result is None, "Seattle|DJF served=raw → caller must use raw ensemble"


# ---------------------------------------------------------------------------
# season_for helper
# ---------------------------------------------------------------------------

class TestSeasonFor:

    def test_june_is_jja_nh(self):
        from src.calibration.emos import season_for
        from datetime import date
        assert season_for(date(2026, 6, 15)) == "JJA"

    def test_january_is_djf_nh(self):
        from src.calibration.emos import season_for
        from datetime import date
        assert season_for(date(2026, 1, 10)) == "DJF"

    def test_april_is_mam_nh(self):
        from src.calibration.emos import season_for
        from datetime import date
        assert season_for(date(2026, 4, 5)) == "MAM"

    def test_october_is_son_nh(self):
        from src.calibration.emos import season_for
        from datetime import date
        assert season_for(date(2026, 10, 20)) == "SON"


# ---------------------------------------------------------------------------
# Regression: live EMOS path reads snapshot members_json
# ---------------------------------------------------------------------------

class TestEmosHookMemberSource:
    """Regression coverage for the event-snapshot member extraction path."""

    def _make_snapshot_with_members(self, members_c: list[float], unit: str = "C") -> dict:
        """Build a minimal snapshot dict that _snapshot_members() can parse."""
        import json
        return {
            "members_json": json.dumps(members_c),
            "members_unit": unit,
            "lead_hours": 72,
        }

    def test_snapshot_members_path_returns_correct_array(self):
        """_snapshot_members(snapshot) returns the raw array."""
        import numpy as np
        import json

        members = [18.5, 19.0, 20.1, 21.5, 22.0, 19.8, 20.3]
        snapshot = {"members_json": json.dumps(members)}

        # Import the actual function used by the hook after the fix
        from src.engine.event_reactor_adapter import _snapshot_members
        result = _snapshot_members(snapshot)
        assert result.size == len(members), f"Expected {len(members)} members, got {result.size}"
        np.testing.assert_allclose(result, members, rtol=1e-12)

    def test_emos_q_is_finite_with_snapshot_members(self):
        """Given members_json and an EMOS cell, the bin probability is finite."""
        import json
        import math
        import numpy as np
        from src.calibration.emos import emos_predictive, bin_probability

        # Amsterdam|JJA is a served=emos cell
        city = "Amsterdam"
        season = "JJA"
        lead_days = 3.0

        # Realistic 51-member ensemble in °C for Amsterdam summer
        rng = np.random.default_rng(42)
        members_c = rng.normal(22.0, 3.0, 51).tolist()
        snapshot = {
            "members_json": json.dumps(members_c),
            "members_unit": "C",
            "lead_hours": 72,
        }

        # Simulate what the fixed hook does: read from snapshot
        from src.engine.event_reactor_adapter import _snapshot_members
        members_from_snapshot = _snapshot_members(snapshot)

        assert members_from_snapshot.size > 0, "members_from_snapshot must be non-empty"

        # emos_predictive must return a valid result
        result = emos_predictive(city, season, lead_days, members_from_snapshot)
        assert result is not None, (
            "emos_predictive must return (mu_c, sigma_c) for Amsterdam|JJA — "
            "got None, which means members were empty (the old bug)"
        )
        mu_c, sigma_c = result
        assert math.isfinite(mu_c), f"mu_c must be finite, got {mu_c}"
        assert math.isfinite(sigma_c) and sigma_c > 0, f"sigma_c must be finite positive, got {sigma_c}"

        # bin_probability must give a finite value (emos_q)
        emos_q = bin_probability(mu_c, sigma_c, 18.0, 25.0)
        assert math.isfinite(emos_q) and 0.0 < emos_q < 1.0, (
            f"emos_q must be finite in (0,1), got {emos_q}"
        )

    def test_emos_q_matches_direct_emos_predictive(self):
        """emos_q from snapshot_members path equals emos_predictive(members) to 1e-9.

        Cross-verifies the end-to-end consistency between:
          - direct emos_predictive(city, season, lead, members_c) call
          - the hook's snapshot-read path: _snapshot_members → emos_predictive
        """
        import json
        import numpy as np
        from src.calibration.emos import emos_predictive, bin_probability
        from src.engine.event_reactor_adapter import _snapshot_members

        rng = np.random.default_rng(7)
        members_c = rng.normal(21.0, 2.5, 51)
        snapshot = {"members_json": json.dumps(members_c.tolist())}

        members_via_snapshot = _snapshot_members(snapshot)
        direct_result = emos_predictive("Amsterdam", "JJA", 2.0, members_c)
        hook_result = emos_predictive("Amsterdam", "JJA", 2.0, members_via_snapshot)

        assert direct_result is not None, "direct emos_predictive must not be None"
        assert hook_result is not None, "hook-path emos_predictive must not be None"

        mu_direct, sigma_direct = direct_result
        mu_hook, sigma_hook = hook_result

        assert abs(mu_direct - mu_hook) < 1e-9, (
            f"mu_c mismatch: direct={mu_direct}, hook={mu_hook}"
        )
        assert abs(sigma_direct - sigma_hook) < 1e-9, (
            f"sigma_c mismatch: direct={sigma_direct}, hook={sigma_hook}"
        )


# ---------------------------------------------------------------------------
# Regression: EMOS gated on metric == "high" (LOW metric → emos_q=None)
# ---------------------------------------------------------------------------

class TestEmosMetricGating:
    """EMOS calibration is HIGH-metric only.

    fit_emos_calibration.py fits on temperature_metric='high' rows only.
    Applying HIGH params to LOW members produces garbage emos_q.
    The hook must gate EMOS computation on family.metric == 'high'.

    These tests exercise the metric-gating logic by simulating the two
    code paths that the hook takes based on family_metric:
      - is_high_metric=True  → emos_predictive called → emos_q finite
      - is_high_metric=False → emos_predictive NOT called → emos_q=None
    """

    def _simulate_hook_metric_gate(self, family_metric: str, members_c, city="Amsterdam",
                                   season="JJA", lead_days=3.0):
        """Replicate the hook's metric-gating logic in isolation.

        Returns (emos_mu_c, emos_sigma_c, served_status) mirroring the hook.
        """
        from src.calibration.emos import emos_predictive, load_emos_table

        is_high_metric = (family_metric.lower() == "high")
        emos_mu_c = None
        emos_sigma_c = None
        served_status = "missing"

        if is_high_metric:
            result = emos_predictive(city, season, lead_days, members_c)
            if result is not None:
                emos_mu_c, emos_sigma_c = result
                served_status = "emos"
            else:
                tbl = load_emos_table()
                cell = tbl.get("cells", {}).get(f"{city}|{season}")
                served_status = str(cell.get("served", "missing")) if cell else "missing"
        else:
            served_status = "not_high_metric"

        return emos_mu_c, emos_sigma_c, served_status

    def test_high_metric_produces_finite_emos_q(self):
        """metric='high' + emos cell → emos_mu_c/sigma_c finite, served='emos'.

        Amsterdam|JJA is a served=emos cell. With metric='high' the hook
        calls emos_predictive and gets a valid (mu_c, sigma_c).
        """
        import math
        import numpy as np

        rng = np.random.default_rng(42)
        members_c = rng.normal(22.0, 3.0, 51)

        mu_c, sigma_c, served = self._simulate_hook_metric_gate("high", members_c)

        assert served == "emos", f"Expected served='emos' for high+emos cell, got '{served}'"
        assert mu_c is not None, "emos_mu_c must not be None for high metric"
        assert sigma_c is not None, "emos_sigma_c must not be None for high metric"
        assert math.isfinite(mu_c), f"emos_mu_c={mu_c} must be finite"
        assert math.isfinite(sigma_c) and sigma_c > 0, f"emos_sigma_c={sigma_c} must be finite positive"

    def test_low_metric_produces_none_emos_q(self):
        """metric='low' → emos_mu_c=None, emos_sigma_c=None, served='not_high_metric'.

        EMOS table is HIGH-only fit. A LOW-metric family must NOT get EMOS
        applied — applying HIGH params to LOW members produces garbage emos_q.
        """
        import numpy as np

        rng = np.random.default_rng(42)
        members_c = rng.normal(12.0, 2.0, 51)  # plausible LOW members

        mu_c, sigma_c, served = self._simulate_hook_metric_gate("low", members_c)

        assert mu_c is None, (
            f"emos_mu_c must be None for low metric (got {mu_c}) — "
            "HIGH params applied to LOW members is garbage"
        )
        assert sigma_c is None, f"emos_sigma_c must be None for low metric, got {sigma_c}"
        assert served == "not_high_metric", (
            f"served must be 'not_high_metric' for low metric, got '{served}'"
        )

    def test_unknown_metric_produces_none_emos_q(self):
        """metric='' → EMOS fields None."""
        import numpy as np

        rng = np.random.default_rng(0)
        members_c = rng.normal(20.0, 3.0, 51)

        mu_c, sigma_c, served = self._simulate_hook_metric_gate("", members_c)

        assert mu_c is None, f"emos_mu_c must be None for unknown metric, got {mu_c}"
        assert served == "not_high_metric", (
            f"served must be 'not_high_metric' for unknown metric, got '{served}'"
        )
