"""Tests for model agreement (ECMWF vs GFS conflict detection).

Covers:
1. Happy path: identical distributions → AGREE
2. Edge case: shifted by 4 bins → CONFLICT
3. Failure mode: mismatched vector lengths
"""

import numpy as np
import pytest

from src.signal.model_agreement import model_agreement, compute_jsd


def _make_peaked(n_bins: int, peak_idx: int, sharpness: float = 0.1) -> np.ndarray:
    """Create a probability vector peaked at peak_idx."""
    p = np.full(n_bins, sharpness / n_bins)
    p[peak_idx] = 1.0
    p = p / p.sum()
    return p


class TestModelAgreement:
    def test_identical_distributions_agree(self):
        """Identical P vectors → AGREE."""
        p = np.array([0.05, 0.1, 0.3, 0.3, 0.1, 0.05, 0.03, 0.02,
                       0.02, 0.02, 0.01])
        assert model_agreement(p, p.copy()) == "AGREE"

    def test_nearly_identical_agree(self):
        """Very similar distributions → AGREE."""
        p1 = np.array([0.05, 0.10, 0.30, 0.30, 0.10, 0.05, 0.03, 0.02,
                        0.02, 0.02, 0.01])
        p2 = np.array([0.05, 0.10, 0.29, 0.31, 0.10, 0.05, 0.03, 0.02,
                        0.02, 0.02, 0.01])
        assert model_agreement(p1, p2) == "AGREE"

    def test_shifted_one_bin_soft_disagree(self):
        """Shifted by 1 bin with moderate JSD → SOFT_DISAGREE (mode_gap=1)."""
        p1 = _make_peaked(11, 3)
        p2 = _make_peaked(11, 4)
        result = model_agreement(p1, p2)
        # mode_gap=1 → at most SOFT_DISAGREE (can't be CONFLICT with gap=1)
        assert result in ("AGREE", "SOFT_DISAGREE")

    def test_shifted_four_bins_conflict(self):
        """Shifted by 4 bins → CONFLICT (mode_gap=4 ≥ 2, JSD likely > 0.08)."""
        p1 = _make_peaked(11, 2, sharpness=0.01)
        p2 = _make_peaked(11, 6, sharpness=0.01)
        result = model_agreement(p1, p2)
        assert result == "CONFLICT"

    def test_flat_vs_peaked_soft_disagree(self):
        """Flat distribution vs peaked → SOFT_DISAGREE or CONFLICT."""
        flat = np.ones(11) / 11
        peaked = _make_peaked(11, 5, sharpness=0.01)
        result = model_agreement(flat, peaked)
        assert result in ("SOFT_DISAGREE", "CONFLICT")

    def test_mismatched_lengths_raises(self):
        """Different vector lengths → ValueError."""
        p1 = np.ones(11) / 11
        p2 = np.ones(9) / 9
        with pytest.raises(ValueError, match="length mismatch"):
            model_agreement(p1, p2)


class TestComputeJSD:
    def test_identical_is_zero(self):
        p = np.array([0.2, 0.3, 0.5])
        assert compute_jsd(p, p) == pytest.approx(0.0, abs=1e-10)

    def test_symmetric(self):
        p = np.array([0.1, 0.4, 0.5])
        q = np.array([0.3, 0.3, 0.4])
        assert compute_jsd(p, q) == pytest.approx(compute_jsd(q, p))

    def test_bounded(self):
        """JSD is bounded in [0, ln(2)]."""
        p = np.array([1.0, 0.0, 0.0])
        q = np.array([0.0, 0.0, 1.0])
        jsd = compute_jsd(p, q)
        assert 0 <= jsd <= np.log(2) + 0.001
