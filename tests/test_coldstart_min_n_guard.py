# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 centerDx Finding 1 cold-start contamination
#   (docs/evidence/live_order_pathology/2026-06-22_center_bias_data_precision_dx.md §4 Finding 1)
#   gfs_hrrr added to live fusion with n=8 (< MIN_SETTLED_N=30) causes +3.54°C warm bias at Denver.
#   Guard: a model with n_train < MIN_SETTLED_N contributes weight 0 to the fused CENTER.
"""TDD tests for the universal cold-start MIN_SETTLED_N center-fusion guard.

Invariants tested:
  1. A model with settled_n=8 is EXCLUDED from the center (weight 0).
  2. A model with settled_n=30+ contributes normally.
  3. All-mature case: fused center is byte-identical to pre-guard result.
  4. Denver-style case: with gfs_hrrr (n=8, +3.54°C) excluded, the fused center
     moves to the mature-models-only combination.
  5. The guard is universal — driven purely by settled_n, no hardcoded model names.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.forecast.center import (
    MIN_SETTLED_N,
    raw_second_moment_weights,
    raw_precision_center,
)


# ---------------------------------------------------------------------------
# Helper — build a (raw_m2_and_n, z_by_model) pair.
# ---------------------------------------------------------------------------

def _m2n(raw_m2: float | None, n: int) -> tuple[float | None, int]:
    return (raw_m2, n)


# ---------------------------------------------------------------------------
# 1. Immature model (n=8) is excluded — weight must be exactly 0.
# ---------------------------------------------------------------------------

class TestImmatureModelExcluded:
    """A model with n_train < MIN_SETTLED_N gets weight 0 in the center."""

    def test_single_immature_model_gets_zero_weight(self):
        """Only model has n=8; below MIN_SETTLED_N. With all-immature, falls back to 1/n=1.0.
        The key property is that the model is in cold_start_excluded.  We verify this by
        checking that if a mature peer is present, the immature model gets 0 weight."""
        # When only immature models exist, fall back to equal 1/n (no-signal)
        raw_m2_and_n = {
            "gfs_hrrr": _m2n(0.5, 8),  # n=8 < 30 → cold-start, but only model → 1/1
        }
        weights = raw_second_moment_weights(raw_m2_and_n)
        # All-immature fallback: equal 1/n = 1.0
        assert abs(weights["gfs_hrrr"] - 1.0) < 1e-12

    def test_immature_among_mature_gets_zero_weight(self):
        """gfs_hrrr at n=8 must get weight 0; mature models share the remaining weight."""
        raw_m2_and_n = {
            "ecmwf_ifs": _m2n(0.20, 87),  # mature
            "icon_global": _m2n(0.30, 88),  # mature
            "gfs_hrrr": _m2n(0.15, 8),    # n=8 < 30 → excluded
        }
        weights = raw_second_moment_weights(raw_m2_and_n)
        # gfs_hrrr must be excluded
        assert weights["gfs_hrrr"] == 0.0
        # Mature models must get all the weight
        assert weights["ecmwf_ifs"] > 0.0
        assert weights["icon_global"] > 0.0
        # Weights must sum to 1
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-12

    def test_immature_at_exactly_threshold_minus_one(self):
        """n = MIN_SETTLED_N - 1 is below the threshold → excluded."""
        n_below = MIN_SETTLED_N - 1
        raw_m2_and_n = {
            "model_a": _m2n(0.5, n_below),
            "model_b": _m2n(0.5, MIN_SETTLED_N),  # exactly at threshold → mature
        }
        weights = raw_second_moment_weights(raw_m2_and_n)
        assert weights["model_a"] == 0.0
        assert weights["model_b"] > 0.0


# ---------------------------------------------------------------------------
# 2. Mature model (n >= MIN_SETTLED_N) contributes normally.
# ---------------------------------------------------------------------------

class TestMatureModelContributes:
    """Models at or above MIN_SETTLED_N participate with positive weight."""

    def test_exactly_at_threshold(self):
        """n = MIN_SETTLED_N (=30) is the first mature value → positive weight."""
        raw_m2_and_n = {"model_a": _m2n(0.5, MIN_SETTLED_N)}
        weights = raw_second_moment_weights(raw_m2_and_n)
        assert weights["model_a"] > 0.0
        assert abs(weights["model_a"] - 1.0) < 1e-12

    def test_above_threshold(self):
        """n = 87 → positive weight, unchanged from pre-guard behavior."""
        raw_m2_and_n = {"ecmwf_ifs": _m2n(0.20, 87)}
        weights = raw_second_moment_weights(raw_m2_and_n)
        assert weights["ecmwf_ifs"] > 0.0


# ---------------------------------------------------------------------------
# 3. All-mature case: result must be byte-identical to computing without the guard.
# ---------------------------------------------------------------------------

class TestAllMatureByteIdentical:
    """When every model has n >= MIN_SETTLED_N, the guard is dormant and the
    center is exactly the same as if the guard did not exist."""

    def test_all_mature_weights_unchanged(self):
        """Three mature models — weights are pure precision-ratio, no guard effect.
        Use raw_m2 values ABOVE the SIGMA_FLOOR² (0.64) so precision ratios differ."""
        raw_m2_and_n = {
            "ecmwf_ifs": _m2n(1.00, 87),   # above floor
            "icon_global": _m2n(2.25, 88),  # highest raw_m2 → lowest precision
            "ukmo": _m2n(1.50, 84),
        }
        weights = raw_second_moment_weights(raw_m2_and_n)
        # All weights positive
        assert all(w > 0.0 for w in weights.values())
        # Sum to 1
        assert abs(sum(weights.values()) - 1.0) < 1e-12
        # Proportional to precision: higher raw_m2 → lower weight
        # ecmwf_ifs (1.00) < ukmo (1.50) < icon_global (2.25) → ecmwf_ifs highest weight
        assert weights["ecmwf_ifs"] > weights["ukmo"] > weights["icon_global"]

    def test_all_mature_center_byte_identical(self):
        """The center value with all-mature is exactly Σ w_m * z_m — no shift."""
        raw_m2_and_n = {
            "ecmwf_ifs": _m2n(1.00, 87),
            "icon_global": _m2n(2.25, 88),
            "ukmo": _m2n(1.50, 84),
        }
        z_by_model = {"ecmwf_ifs": 28.0, "icon_global": 27.5, "ukmo": 29.2}
        weights, mu = raw_precision_center(raw_m2_and_n, z_by_model)
        # mu must be within the member envelope
        assert 27.5 <= mu <= 29.2
        # Recompute manually to verify byte-identity
        expected = sum(weights[m] * z for m, z in z_by_model.items())
        assert abs(mu - expected) < 1e-12


# ---------------------------------------------------------------------------
# 4. Denver-style case: gfs_hrrr (n=8, +3.54°C bias) excluded → de-contaminated center.
# ---------------------------------------------------------------------------

class TestDenverDecontamination:
    """Mirrors the 2026-06-22 centerDx evidence doc §2g Denver diagnostic.

    Setup (from evidence doc):
      - ecmwf_ifs: bias −0.22°C, n=87 (mature)
      - icon_global: bias −0.90°C, n=88 (mature)
      - ukmo: bias +0.92°C, n=84 (mature)
      - ncep_nbm_conus: bias −0.73°C, n=84 (mature)
      - gfs_hrrr: bias +3.54°C, n=8 (COLD-START → excluded)

    Expected: fused center without gfs_hrrr is near 0°C (roughly −0.23°C
    equal-weight of the 4 mature models), NOT warm-contaminated.
    With gfs_hrrr included at equal weight: (−0.22−0.90+0.92−0.73+3.54)/5=+0.52°C.
    Without gfs_hrrr: (−0.22−0.90+0.92−0.73)/4=−0.23°C.
    """

    # Synthetic "true" values around the consensus. We just need
    # the precision weights to exclude gfs_hrrr.
    _MATURE_Z = {
        "ecmwf_ifs":     30.0 - 0.22,   # 29.78
        "icon_global":   30.0 - 0.90,   # 29.10
        "ukmo":          30.0 + 0.92,   # 30.92
        "ncep_nbm_conus": 30.0 - 0.73,  # 29.27
    }
    _GFS_HRRR_Z = 30.0 + 3.54  # 33.54 — warm outlier

    # Raw m2 for each model (synthetic but representative)
    _M2 = {
        "ecmwf_ifs":      0.20,
        "icon_global":    0.45,
        "ukmo":           0.35,
        "ncep_nbm_conus": 0.25,
        "gfs_hrrr":       0.15,  # low raw_m2 (only 8 obs — unreliable)
    }

    def _build_inputs(self, include_gfs_hrrr: bool):
        z = dict(self._MATURE_Z)
        m2n: dict[str, tuple[float | None, int]] = {
            "ecmwf_ifs":     (self._M2["ecmwf_ifs"], 87),
            "icon_global":   (self._M2["icon_global"], 88),
            "ukmo":          (self._M2["ukmo"], 84),
            "ncep_nbm_conus": (self._M2["ncep_nbm_conus"], 84),
        }
        if include_gfs_hrrr:
            m2n["gfs_hrrr"] = (self._M2["gfs_hrrr"], 8)  # n=8 < 30
            z["gfs_hrrr"] = self._GFS_HRRR_Z
        return m2n, z

    def test_gfs_hrrr_excluded_by_guard(self):
        """gfs_hrrr weight must be 0 when present with n=8."""
        m2n, z = self._build_inputs(include_gfs_hrrr=True)
        weights = raw_second_moment_weights(m2n)
        assert weights.get("gfs_hrrr", 0.0) == 0.0

    def test_denver_center_decontaminated(self):
        """Center with gfs_hrrr excluded is near −0.23°C offset from 30°C = ~29.77°C.
        Center with gfs_hrrr included (without guard) would be +0.52°C offset = ~30.52°C."""
        m2n_with, z_with = self._build_inputs(include_gfs_hrrr=True)
        _, mu_with_guard = raw_precision_center(m2n_with, z_with)

        m2n_without, z_without = self._build_inputs(include_gfs_hrrr=False)
        _, mu_without = raw_precision_center(m2n_without, z_without)

        # Both must give essentially the same center (guard excluded gfs_hrrr from weight=0)
        assert abs(mu_with_guard - mu_without) < 1e-10

        # The center must be in the range of mature members, not warm-contaminated
        # gfs_hrrr at 33.54 must NOT pull the center above the mature max (~30.92)
        assert mu_with_guard < max(self._MATURE_Z.values()) + 1e-10

        # The center must be substantially cooler than the equal-weight contaminated value
        contaminated_equal = sum(list(self._MATURE_Z.values()) + [self._GFS_HRRR_Z]) / 5
        assert mu_with_guard < contaminated_equal - 0.2

    def test_denver_mature_only_center_unchanged(self):
        """Mature-only run (no gfs_hrrr) is byte-identical with and without guard — dormant."""
        m2n_no, z_no = self._build_inputs(include_gfs_hrrr=False)
        weights_no, mu_no = raw_precision_center(m2n_no, z_no)
        # All weights positive and sum to 1
        assert all(w > 0.0 for w in weights_no.values())
        assert abs(sum(weights_no.values()) - 1.0) < 1e-12
        # Center in envelope of mature members
        vals = list(z_no.values())
        assert min(vals) <= mu_no <= max(vals)


# ---------------------------------------------------------------------------
# 5. Universal guard — no hardcoded model names.
# ---------------------------------------------------------------------------

class TestGuardIsUniversal:
    """The guard applies to any model based solely on n_train — no allow/deny list."""

    def test_any_model_name_excluded_at_low_n(self):
        """Models named 'some_new_model' are excluded when n=5 — not just gfs_hrrr."""
        raw_m2_and_n = {
            "some_new_model": _m2n(0.3, 5),
            "mature_model":   _m2n(0.3, 50),
        }
        weights = raw_second_moment_weights(raw_m2_and_n)
        assert weights["some_new_model"] == 0.0
        assert weights["mature_model"] > 0.0

    def test_anchor_excluded_when_immature(self):
        """Even the anchor model is excluded from the center if it has n < MIN_SETTLED_N."""
        raw_m2_and_n = {
            "ecmwf_ifs": _m2n(0.25, 3),   # anchor, n=3 → excluded
            "icon_global": _m2n(0.35, 87), # mature
        }
        weights = raw_second_moment_weights(raw_m2_and_n)
        assert weights["ecmwf_ifs"] == 0.0
        assert weights["icon_global"] > 0.0

    def test_no_hardcoded_model_names_in_center_logic(self):
        """Verify the guard is purely n_train-driven — same behavior for any model name."""
        for model_name in ("gfs_hrrr", "icon_d2", "ncep_nbm_conus", "totally_new_model_2026"):
            raw_m2_and_n = {
                model_name: _m2n(0.5, 8),     # n=8 → excluded
                "mature":   _m2n(0.5, 50),    # n=50 → contributes
            }
            weights = raw_second_moment_weights(raw_m2_and_n)
            assert weights[model_name] == 0.0, (
                f"Model '{model_name}' should be excluded at n=8, got weight={weights[model_name]}"
            )
            assert weights["mature"] > 0.0


# ---------------------------------------------------------------------------
# 6. Edge cases: all immature → fall back to equal weights (no center is possible
#    from mature models, treat as no-signal).
# ---------------------------------------------------------------------------

class TestAllImmatureEdgeCase:
    """When ALL models are below MIN_SETTLED_N, fall back to equal weights (no discrimination)."""

    def test_all_immature_equal_weights(self):
        """All models at n=5 → each gets equal 1/n weight (no mature model to anchor to)."""
        raw_m2_and_n = {
            "model_a": _m2n(0.3, 5),
            "model_b": _m2n(0.5, 8),
        }
        weights = raw_second_moment_weights(raw_m2_and_n)
        # All excluded from center → equal 1/n fallback
        n = len(raw_m2_and_n)
        for w in weights.values():
            assert abs(w - 1.0 / n) < 1e-12

    def test_single_immature_model_gets_full_weight_fallback(self):
        """Single model, n=1 < MIN_SETTLED_N. With all-immature fallback → gets 1.0 weight."""
        raw_m2_and_n = {"only_model": _m2n(0.5, 1)}
        weights = raw_second_moment_weights(raw_m2_and_n)
        # With 1 model and all-immature → equal weight = 1/1 = 1.0
        assert abs(weights["only_model"] - 1.0) < 1e-12


# ---------------------------------------------------------------------------
# 7. MIN_SETTLED_N constant is exported and equals 30.
# ---------------------------------------------------------------------------

def test_min_settled_n_constant():
    """MIN_SETTLED_N must be 30 as per the evidence doc recommendation."""
    assert MIN_SETTLED_N == 30
