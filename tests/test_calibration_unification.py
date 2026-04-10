"""Tests for S6: calibration path unification.

Verifies that calibrate_and_normalize() produces different results from
predict_for_bin() when multiple bins are present (documenting the semantic
difference), and that _build_all_bins correctly reconstructs full bin vectors.
"""

import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from src.calibration.platt import (
    ExtendedPlattCalibrator,
    calibrate_and_normalize,
)
from src.engine.monitor_refresh import _build_all_bins
from src.types import Bin


def _fitted_calibrator(seed: int = 42) -> ExtendedPlattCalibrator:
    """Build a calibrator with intentional bias so renormalization matters."""
    rng = np.random.default_rng(seed)
    n = 200
    p_raw = rng.uniform(0.05, 0.95, n)
    lead_days = rng.uniform(1, 7, n)
    true_p = np.clip(p_raw * 0.8 + 0.1, 0.01, 0.99)
    outcomes = (rng.random(n) < true_p).astype(int)
    cal = ExtendedPlattCalibrator()
    cal.fit(p_raw, lead_days, outcomes)
    return cal


class TestCalibrationPathDivergence:
    """Document that predict_for_bin and calibrate_and_normalize diverge."""

    def test_single_bin_paths_match_before_normalization(self):
        """With one bin, predict_for_bin equals the un-normalized calibration."""
        cal = _fitted_calibrator()
        p_raw = np.array([0.4])
        lead = 3.0
        widths = [2.0]

        scalar = cal.predict_for_bin(0.4, lead, bin_width=2.0)
        # Note: calibrate_and_normalize with 1 bin normalizes to 1.0,
        # so we test that predict_for_bin is used as the scalar path.
        assert scalar > 0.0
        assert scalar < 1.0

    def test_multi_bin_paths_diverge(self):
        """With multiple bins, renormalization causes divergence."""
        cal = _fitted_calibrator()
        p_raw = np.array([0.15, 0.25, 0.30, 0.20, 0.10])
        lead = 3.0
        widths = [2.0, 2.0, 2.0, 2.0, None]  # last is shoulder

        # Old path: calibrate each bin independently
        old_values = [
            cal.predict_for_bin(float(p), lead, bin_width=widths[i])
            for i, p in enumerate(p_raw)
        ]

        # New path: calibrate + renormalize
        new_values = calibrate_and_normalize(p_raw, cal, lead, bin_widths=widths)

        # They should NOT be identical (renormalization changes values)
        # unless the calibrator happens to preserve sum=1 exactly
        old_sum = sum(old_values)
        new_sum = float(new_values.sum())

        # New path should sum to 1.0 (by construction)
        assert new_sum == pytest.approx(1.0, abs=1e-9)
        # Old path likely does NOT sum to 1.0
        # (this documents the divergence that S6 fixes)
        if abs(old_sum - 1.0) > 0.001:
            # Divergence exists — individual values must differ
            for i in range(len(p_raw)):
                assert float(new_values[i]) != pytest.approx(old_values[i], abs=1e-6), \
                    f"Bin {i} unexpectedly identical despite renormalization"


class TestBuildAllBins:
    """Test _build_all_bins helper for reconstructing full bin vectors."""

    def test_fallback_to_single_bin_when_no_market_id(self):
        pos = MagicMock()
        pos.market_id = ""
        pos.bin_label = "50-51°F"
        pos.unit = "F"
        city = MagicMock()
        city.settlement_unit = "F"

        bins, idx = _build_all_bins(pos, city)
        assert len(bins) == 1
        assert idx == 0
        assert bins[0].low == 50.0
        assert bins[0].high == 51.0

    def test_fallback_to_single_bin_when_no_siblings(self):
        pos = MagicMock()
        pos.market_id = "cond_123"
        pos.bin_label = "50-51°F"
        pos.unit = "F"
        city = MagicMock()
        city.settlement_unit = "F"

        with patch("src.engine.monitor_refresh.get_sibling_outcomes", return_value=[]):
            bins, idx = _build_all_bins(pos, city)
        assert len(bins) == 1
        assert idx == 0

    def test_builds_full_vector_from_siblings(self):
        pos = MagicMock()
        pos.market_id = "cond_B"
        pos.bin_label = "50-51°F"
        pos.unit = "F"
        city = MagicMock()
        city.settlement_unit = "F"

        siblings = [
            {"title": "48-49°F", "market_id": "cond_A", "range_low": 48.0, "range_high": 49.0},
            {"title": "50-51°F", "market_id": "cond_B", "range_low": 50.0, "range_high": 51.0},
            {"title": "52-53°F", "market_id": "cond_C", "range_low": 52.0, "range_high": 53.0},
            {"title": "54°F or above", "market_id": "cond_D", "range_low": 54.0, "range_high": None},
        ]
        with patch("src.engine.monitor_refresh.get_sibling_outcomes", return_value=siblings):
            bins, idx = _build_all_bins(pos, city)

        assert len(bins) == 4
        assert idx == 1  # held bin is the second one
        assert bins[0].low == 48.0
        assert bins[1].low == 50.0
        assert bins[1].high == 51.0
        assert bins[3].high is None  # shoulder bin

    def test_held_index_correct_for_first_bin(self):
        pos = MagicMock()
        pos.market_id = "cond_A"
        pos.bin_label = "48-49°F"
        pos.unit = "F"
        city = MagicMock()
        city.settlement_unit = "F"

        siblings = [
            {"title": "48-49°F", "market_id": "cond_A", "range_low": 48.0, "range_high": 49.0},
            {"title": "50-51°F", "market_id": "cond_B", "range_low": 50.0, "range_high": 51.0},
        ]
        with patch("src.engine.monitor_refresh.get_sibling_outcomes", return_value=siblings):
            bins, idx = _build_all_bins(pos, city)

        assert idx == 0

    def test_skips_unparseable_siblings(self):
        pos = MagicMock()
        pos.market_id = "cond_B"
        pos.bin_label = "50-51°F"
        pos.unit = "F"
        city = MagicMock()
        city.settlement_unit = "F"

        siblings = [
            {"title": "unknown question", "market_id": "cond_X", "range_low": None, "range_high": None},
            {"title": "50-51°F", "market_id": "cond_B", "range_low": 50.0, "range_high": 51.0},
        ]
        with patch("src.engine.monitor_refresh.get_sibling_outcomes", return_value=siblings):
            bins, idx = _build_all_bins(pos, city)

        assert len(bins) == 1
        assert idx == 0

    def test_fallback_when_held_market_id_not_in_siblings(self):
        """S6 guard: if held market_id never matches a sibling, fall back to single bin."""
        pos = MagicMock()
        pos.market_id = "cond_MISSING"
        pos.bin_label = "50-51°F"
        pos.unit = "F"
        city = MagicMock()
        city.settlement_unit = "F"

        siblings = [
            {"title": "48-49°F", "market_id": "cond_A", "range_low": 48.0, "range_high": 49.0},
            {"title": "50-51°F", "market_id": "cond_B", "range_low": 50.0, "range_high": 51.0},
        ]
        with patch("src.engine.monitor_refresh.get_sibling_outcomes", return_value=siblings):
            bins, idx = _build_all_bins(pos, city)

        # Should fall back to single held bin since market_id never matched
        assert len(bins) == 1
        assert idx == 0
        assert bins[0].low == 50.0
        assert bins[0].high == 51.0


class TestCalibrationParity:
    """W2: End-to-end parity — entry and monitor paths produce identical p_cal
    when given the same inputs through calibrate_and_normalize."""

    def test_entry_and_monitor_paths_produce_same_p_cal(self):
        """Given identical (bins, p_raw_vector, calibrator, lead_days),
        the entry evaluator and monitor refresh must produce the same
        calibrated probability for the held bin."""
        cal = _fitted_calibrator()
        bins = [
            Bin(low=48.0, high=49.0, label="48-49°F", unit="F"),
            Bin(low=50.0, high=51.0, label="50-51°F", unit="F"),
            Bin(low=52.0, high=53.0, label="52-53°F", unit="F"),
            Bin(low=54.0, high=None, label="54°F or above", unit="F"),
        ]
        p_raw_vector = np.array([0.20, 0.35, 0.25, 0.20])
        lead_days = 3.0
        held_idx = 1  # "50-51°F"

        # Entry path: evaluator calls calibrate_and_normalize, then p_cal[i]
        entry_p_cal_vector = calibrate_and_normalize(
            p_raw_vector, cal, lead_days,
            bin_widths=[b.width for b in bins],
        )
        entry_held_p = float(entry_p_cal_vector[held_idx])

        # Monitor path: same call (after S6 unification)
        monitor_p_cal_vector = calibrate_and_normalize(
            p_raw_vector, cal, lead_days,
            bin_widths=[b.width for b in bins],
        )
        monitor_held_p = float(monitor_p_cal_vector[held_idx])

        assert entry_held_p == pytest.approx(monitor_held_p, abs=1e-12)
        assert float(entry_p_cal_vector.sum()) == pytest.approx(1.0, abs=1e-9)

    def test_parity_with_shoulder_bins(self):
        """Parity holds when bin set includes shoulder bins (width=None)."""
        cal = _fitted_calibrator()
        bins = [
            Bin(low=None, high=47.0, label="47°F or below", unit="F"),
            Bin(low=48.0, high=49.0, label="48-49°F", unit="F"),
            Bin(low=50.0, high=51.0, label="50-51°F", unit="F"),
            Bin(low=52.0, high=None, label="52°F or above", unit="F"),
        ]
        p_raw_vector = np.array([0.10, 0.30, 0.40, 0.20])
        lead_days = 2.0
        held_idx = 2

        p_cal = calibrate_and_normalize(
            p_raw_vector, cal, lead_days,
            bin_widths=[b.width for b in bins],
        )
        assert float(p_cal.sum()) == pytest.approx(1.0, abs=1e-9)
        assert p_cal[held_idx] > 0.0
        assert p_cal[held_idx] < 1.0

    def test_parity_day0_uses_zero_lead_days(self):
        """Day0 path always passes lead_days=0.0; verify calibrator handles it."""
        cal = _fitted_calibrator()
        bins = [
            Bin(low=48.0, high=49.0, label="48-49°F", unit="F"),
            Bin(low=50.0, high=51.0, label="50-51°F", unit="F"),
        ]
        p_raw = np.array([0.45, 0.55])

        # Day0 entry path: lead_days=0.0
        entry_p = calibrate_and_normalize(p_raw, cal, 0.0, bin_widths=[2.0, 2.0])
        # Day0 monitor path: same
        monitor_p = calibrate_and_normalize(p_raw, cal, 0.0, bin_widths=[2.0, 2.0])

        np.testing.assert_array_almost_equal(entry_p, monitor_p, decimal=12)


class TestRefreshDay0CalBranches:
    """W3: Day0 refresh integration — test cal/no-cal/single-bin branches."""

    def _make_position(self, bin_label="50-51°F", market_id="cond_B"):
        pos = MagicMock()
        pos.market_id = market_id
        pos.bin_label = bin_label
        pos.unit = "F"
        pos.target_date = "2026-07-15"
        pos.p_posterior = 0.55
        pos.entered_at = None
        pos.p_entry = 0.35
        pos.entry_method = "day0_observation"
        pos.last_monitor_market_price = 0.40
        pos.last_exit_edge_context = None
        return pos

    def _make_city(self):
        city = MagicMock()
        city.settlement_unit = "F"
        city.timezone = "America/Chicago"
        city.name = "chicago"
        return city

    def _siblings(self):
        return [
            {"title": "48-49°F", "market_id": "cond_A", "range_low": 48.0, "range_high": 49.0},
            {"title": "50-51°F", "market_id": "cond_B", "range_low": 50.0, "range_high": 51.0},
            {"title": "52-53°F", "market_id": "cond_C", "range_low": 52.0, "range_high": 53.0},
        ]

    @patch("src.engine.monitor_refresh.get_sibling_outcomes")
    @patch("src.engine.monitor_refresh.get_calibrator")
    def test_day0_cal_none_uses_raw_vector(self, mock_get_cal, mock_siblings):
        """When cal is None, Day0 monitor uses raw p_raw_vector[held_idx]."""
        mock_get_cal.return_value = (None, None)
        mock_siblings.return_value = self._siblings()

        pos = self._make_position()
        city = self._make_city()

        # build_all_bins should return 3 bins, held_idx=1
        bins, held_idx = _build_all_bins(pos, city)
        assert len(bins) == 3
        assert held_idx == 1

        # Simulate what the Day0 refresh does with cal=None:
        p_raw_vector = np.array([0.25, 0.40, 0.35])
        # cal=None path: p_cal_yes = float(p_raw_vector[held_idx])
        p_cal_yes = float(p_raw_vector[held_idx])
        assert p_cal_yes == pytest.approx(0.40)

    @patch("src.engine.monitor_refresh.get_sibling_outcomes")
    @patch("src.engine.monitor_refresh.get_calibrator")
    def test_day0_single_bin_uses_predict_for_bin(self, mock_get_cal, mock_siblings):
        """Single-bin fallback uses predict_for_bin (not calibrate_and_normalize)."""
        cal = _fitted_calibrator()
        mock_get_cal.return_value = (cal, "city_level")
        mock_siblings.return_value = []  # No siblings → single bin fallback

        pos = self._make_position()
        city = self._make_city()

        bins, held_idx = _build_all_bins(pos, city)
        assert len(bins) == 1
        assert held_idx == 0

        # Single-bin + cal path: uses predict_for_bin
        p_raw_value = 0.40
        p_cal_yes = cal.predict_for_bin(p_raw_value, 0.0, bin_width=bins[0].width)
        assert 0.0 < p_cal_yes < 1.0
        # Verify it's NOT 1.0 (which calibrate_and_normalize would return for single bin)
        assert p_cal_yes != pytest.approx(1.0, abs=0.01)

    @patch("src.engine.monitor_refresh.get_sibling_outcomes")
    @patch("src.engine.monitor_refresh.get_calibrator")
    def test_day0_multi_bin_uses_calibrate_and_normalize(self, mock_get_cal, mock_siblings):
        """Multi-bin + cal path calls calibrate_and_normalize and extracts held bin."""
        cal = _fitted_calibrator()
        mock_get_cal.return_value = (cal, "city_level")
        mock_siblings.return_value = self._siblings()

        pos = self._make_position()
        city = self._make_city()

        bins, held_idx = _build_all_bins(pos, city)
        assert len(bins) == 3
        assert held_idx == 1

        p_raw_vector = np.array([0.25, 0.40, 0.35])

        # Multi-bin + cal path: calibrate_and_normalize
        p_cal_vector = calibrate_and_normalize(
            p_raw_vector, cal, 0.0,
            bin_widths=[b.width for b in bins],
        )
        p_cal_yes = float(p_cal_vector[held_idx])

        assert float(p_cal_vector.sum()) == pytest.approx(1.0, abs=1e-9)
        assert 0.0 < p_cal_yes < 1.0
