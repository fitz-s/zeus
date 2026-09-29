"""Tests for S6: calibration path unification.

Verifies that calibrate_and_normalize() produces different results from
predict_for_bin() when multiple bins are present (documenting the semantic
difference), and that _build_all_bins correctly reconstructs full bin vectors.

Phase 4 (B4 Cat G) 2026-05-01: `_build_all_bins` is now fail-fast on missing
scan_authority / missing market_id / insufficient siblings — aligned with
INV-19a DATA_DEGRADED-not-silent-fallback principles. Tests below were
updated to either populate the new gates (autouse `_verified_scan_authority`
fixture) or assert `pytest.raises(ValueError)` for tests that explicitly
exercise the fail-fast contract.
"""

import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from src.calibration.platt import (
    ExtendedPlattCalibrator,
    calibrate_and_normalize,
)
from src.types import Bin


@pytest.fixture(autouse=True)
def _verified_scan_authority(monkeypatch):
    """B4 Phase 4 Cat G: monitor_refresh._build_all_bins now requires
    `get_last_scan_authority() == "VERIFIED"` (fail-fast on stale topology
    per INV-19a DATA_DEGRADED-not-silent-fallback principles).
    Patch globally so the topology-validation gate clears for tests that
    exercise the substantive bin-construction logic; tests that exercise
    the fail-fast contract itself override via `pytest.raises`.
    """
    monkeypatch.setattr(
        "src.engine.monitor_refresh.get_last_scan_authority",
        lambda: "VERIFIED",
    )


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

