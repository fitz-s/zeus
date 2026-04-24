# Created: 2026-04-24
# Last reused/audited: 2026-04-24
# Authority basis: T6.3 VigTreatment sparse-impute provenance (midstream fix plan 2026-04-23)
"""Contract-level tests for VigTreatment impute provenance (T6.3).

Covers:
- Complete-market path: imputation_source='none', imputed_bins=()
- Sparse path with p_cal fallback: imputation_source='p_cal_fallback',
  imputed_bins records zero positions, clean_prices is the imputed vector
  (no devig — mixed-semantics vector per module docstring)
- Sparse path with sibling_market: imputation_source='sibling_market'
- Validation failures: missing imputation_source, empty imputed_bins with
  non-'none' source, non-'none' source with empty imputed_bins, shape
  mismatch on sibling_snapshot, sibling_snapshot finite/non-negative.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.contracts.vig_treatment import VigTreatment, VigOrderError


class TestVigTreatmentFromRawComplete:
    """Complete-market path — backwards-compatible with pre-T6.3 callers."""

    def test_no_sibling_snapshot_complete_vector(self):
        raw = np.array([0.45, 0.50])  # vig 0.95
        t = VigTreatment.from_raw(raw)
        assert t.imputation_source == "none"
        assert t.imputed_bins == ()
        np.testing.assert_allclose(t.vig_factor, 0.95)
        np.testing.assert_allclose(t.clean_prices, raw / 0.95)

    def test_sibling_snapshot_ignored_when_no_zeros(self):
        raw = np.array([0.45, 0.50])
        sib = np.array([0.30, 0.70])
        # sibling_snapshot supplied but raw has no zeros → impute is a no-op;
        # treatment reduces to complete-market devig.
        t = VigTreatment.from_raw(raw, sibling_snapshot=sib, imputation_source="p_cal_fallback")
        assert t.imputation_source == "none"
        assert t.imputed_bins == ()


class TestVigTreatmentFromRawSparse:
    """Sparse path — impute provenance is typed-visible on the record."""

    def test_p_cal_fallback_records_provenance(self):
        raw = np.array([0.0, 0.95, 0.0])
        p_cal = np.array([0.20, 0.50, 0.30])
        t = VigTreatment.from_raw(raw, sibling_snapshot=p_cal, imputation_source="p_cal_fallback")
        assert t.imputation_source == "p_cal_fallback"
        assert t.imputed_bins == (0, 2)
        # clean_prices keeps imputed values at zero positions; does NOT devig
        # (see module docstring on mixed vig semantics).
        np.testing.assert_allclose(t.clean_prices, np.array([0.20, 0.95, 0.30]))
        assert t.vig_factor == 1.0  # sentinel: no vig applied to mixed vector

    def test_sibling_market_source_label(self):
        raw = np.array([0.0, 0.45, 0.0])
        sib = np.array([0.25, 0.40, 0.35])
        t = VigTreatment.from_raw(raw, sibling_snapshot=sib, imputation_source="sibling_market")
        assert t.imputation_source == "sibling_market"
        assert t.imputed_bins == (0, 2)
        np.testing.assert_allclose(t.clean_prices, np.array([0.25, 0.45, 0.35]))

    def test_impute_rejects_undeclared_source(self):
        raw = np.array([0.0, 0.95, 0.0])
        p_cal = np.array([0.20, 0.50, 0.30])
        # sibling_snapshot provided with zeros to impute but source defaulted
        # to 'none' — caller must declare the reference semantic.
        with pytest.raises(ValueError, match="imputation_source='none'"):
            VigTreatment.from_raw(raw, sibling_snapshot=p_cal)

    def test_impute_rejects_invalid_source_label(self):
        raw = np.array([0.0, 0.95, 0.0])
        p_cal = np.array([0.20, 0.50, 0.30])
        with pytest.raises(ValueError, match="imputation_source"):
            VigTreatment.from_raw(raw, sibling_snapshot=p_cal, imputation_source="bogus_label")

    def test_sibling_snapshot_shape_mismatch(self):
        raw = np.array([0.0, 0.95, 0.0])
        sib = np.array([0.30, 0.50])  # wrong length
        with pytest.raises(ValueError, match="shape"):
            VigTreatment.from_raw(raw, sibling_snapshot=sib, imputation_source="p_cal_fallback")

    def test_sibling_snapshot_non_finite_rejected(self):
        raw = np.array([0.0, 0.95, 0.0])
        sib = np.array([0.3, np.inf, 0.3])
        with pytest.raises(ValueError, match="sibling_snapshot must be finite"):
            VigTreatment.from_raw(raw, sibling_snapshot=sib, imputation_source="p_cal_fallback")

    def test_sibling_snapshot_negative_rejected(self):
        raw = np.array([0.0, 0.95, 0.0])
        sib = np.array([0.3, 0.5, -0.01])
        with pytest.raises(ValueError, match="sibling_snapshot must be non-negative"):
            VigTreatment.from_raw(raw, sibling_snapshot=sib, imputation_source="p_cal_fallback")

    def test_zero_for_zero_substitution_not_claimed_as_imputed(self):
        """MED finding post-edit: when sibling_snapshot[i]=0 at a raw-zero
        position, no real imputation happened — imputed_bins must NOT record
        that index, otherwise auditors are misled."""
        raw = np.array([0.0, 0.95, 0.0])
        # Sibling has zero at index 0 (no repair possible); has value at index 2
        sib = np.array([0.0, 0.50, 0.3])
        t = VigTreatment.from_raw(raw, sibling_snapshot=sib, imputation_source="sibling_market")
        assert t.imputed_bins == (2,), (
            f"Expected only index 2 recorded (zero-for-zero at 0 is no-op); got {t.imputed_bins}"
        )
        # Index 0 remains a raw zero — no silent false provenance claim
        assert t.clean_prices[0] == 0.0
        # Index 2 properly imputed from sibling
        assert t.clean_prices[2] == 0.3

    def test_all_zero_for_zero_falls_back_to_none_source(self):
        """If every zero position has a zero sibling, no actual impute
        happened — treatment reduces to complete-market devig with
        imputation_source='none' (typed record claims no imputation)."""
        raw = np.array([0.0, 0.95, 0.0])
        sib = np.array([0.0, 0.50, 0.0])
        # Declared p_cal_fallback, but zero-for-zero at every eligible index
        # means no effective impute. Contract: source demoted to "none".
        # Note: sum(raw) > 0 so vig_factor computation succeeds.
        t = VigTreatment.from_raw(raw, sibling_snapshot=sib, imputation_source="p_cal_fallback")
        assert t.imputation_source == "none"
        assert t.imputed_bins == ()


class TestVigTreatmentPostInitInvariants:
    """__post_init__ enforces provenance consistency between imputed_bins and imputation_source."""

    def test_empty_imputed_bins_with_non_none_source_rejected(self):
        # Construct bypassing from_raw — simulates a caller inconsistency.
        with pytest.raises(ValueError, match="imputed_bins is empty"):
            VigTreatment(
                raw_market_prices=np.array([0.45, 0.50]),
                vig_factor=0.95,
                clean_prices=np.array([0.45, 0.50]) / 0.95,
                applied_before_blend=True,
                imputed_bins=(),
                imputation_source="p_cal_fallback",
            )

    def test_non_empty_imputed_bins_with_none_source_rejected(self):
        with pytest.raises(ValueError, match="imputed_bins=.+ is non-empty"):
            VigTreatment(
                raw_market_prices=np.array([0.0, 0.95]),
                vig_factor=1.0,
                clean_prices=np.array([0.30, 0.95]),
                applied_before_blend=True,
                imputed_bins=(0,),
                imputation_source="none",
            )

    def test_sparse_imputed_vector_may_sum_above_one(self):
        # The mixed observed+prior vector does not need to sum to 1.0; final
        # normalization happens in compute_posterior. Validator must accept.
        t = VigTreatment(
            raw_market_prices=np.array([0.0, 0.95, 0.0]),
            vig_factor=1.0,
            clean_prices=np.array([0.20, 0.95, 0.30]),  # sums to 1.45
            applied_before_blend=True,
            imputed_bins=(0, 2),
            imputation_source="p_cal_fallback",
        )
        assert float(np.sum(t.clean_prices)) > 1.0

    def test_complete_vector_sum_invariant_still_enforced(self):
        # Non-imputed path — validator still requires clean_prices sum to ~1.0.
        with pytest.raises(ValueError, match="clean_prices must sum to ~1.0"):
            VigTreatment(
                raw_market_prices=np.array([0.45, 0.50]),
                vig_factor=0.95,
                clean_prices=np.array([0.45, 0.50]),  # sums to 0.95, not ~1.0
                applied_before_blend=True,
                imputed_bins=(),
                imputation_source="none",
            )

    def test_applied_before_blend_false_still_rejected(self):
        with pytest.raises(VigOrderError):
            VigTreatment(
                raw_market_prices=np.array([0.45, 0.50]),
                vig_factor=0.95,
                clean_prices=np.array([0.45, 0.50]) / 0.95,
                applied_before_blend=False,
                imputed_bins=(),
                imputation_source="none",
            )
