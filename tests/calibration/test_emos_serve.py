# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS shadow-ledger task; PIECE 1 spec (emos.py + emos_ledger.py);
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
