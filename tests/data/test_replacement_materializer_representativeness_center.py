# Created: 2026-06-21
# Last audited: 2026-09-29
# Authority basis: replacement_final_form_2026_06_09.md §1d; typed native geometry
# proof, plus preservation of the existing pure precision-center formula.
"""Legacy target DEM cannot authorize a native penalty.

The pure formula fixtures below preserve its existing mathematics for offline
proved native inputs; they do not declare legacy DEM valid or supply live q law.
"""
from __future__ import annotations

from src.data.replacement_forecast_materializer import _build_sigma_repr_by_model
from src.forecast.center import raw_precision_center


# ============================================================================
# _build_sigma_repr_by_model — the EXIT-seam repr dict builder (fail-soft).
# ============================================================================
class TestBuildSigmaReprByModel:
    def test_absent_city_returns_empty(self):
        """A city absent from the grid table → empty dict (byte-identical center)."""
        out = _build_sigma_repr_by_model(
            "NOPLACE_XYZ_ABSENT", ["ecmwf_ifs", "gfs_global"], anchor_model="ecmwf_ifs"
        )
        assert out == {}

    def test_legacy_target_dem_cannot_supply_native_penalty(self):
        """A known legacy cell is unproven, not a certified native-height delta."""
        from src.forecast.grid_representativeness_loader import read_grid_representativeness

        proof = read_grid_representativeness("Tokyo", "ecmwf_ifs")
        assert proof.status == "UNPROVEN" and proof.variance_c2 is None
        out = _build_sigma_repr_by_model("Tokyo", ["ecmwf_ifs"], anchor_model="ecmwf_ifs")
        assert out == {}

    def test_only_positive_entries_kept(self):
        """Zero/absent-cell models are omitted (0.0 == absence == byte-identical)."""
        out = _build_sigma_repr_by_model(
            "NOPLACE_XYZ_ABSENT", ["m_absent_1", "m_absent_2"], anchor_model="x"
        )
        for v in out.values():
            assert v > 0.0


# ============================================================================
# EXIT center warming — the served _mu_diagonal warms when repr penalizes a cold
# coarse member. Uses raw_precision_center directly (the exact functional the EXIT
# seam calls) to assert the warming contract without a full DB+request fixture.
# ============================================================================
class TestExitCenterWarming:
    def test_anchor_value_warms_with_repr(self):
        """Cold coarse-far member penalized by repr ⇒ _mu_diagonal (anchor_value_c) warms."""
        # EXIT basis: train_residuals are degC, so raw_m2 + repr are both degC².
        raw_m2_and_n = {"coarse_far": (0.5, 40), "fine_near": (1.0, 40)}
        z = {"coarse_far": 28.0, "fine_near": 31.0}  # far cell colder (the cold-center symptom)
        repr_by = {"coarse_far": 4.0, "fine_near": 0.0}  # far cell coarse/distant

        _, mu_base = raw_precision_center(raw_m2_and_n, z, unit="C")
        _, mu_warm = raw_precision_center(
            raw_m2_and_n, z, unit="C", repr_m2_by_model=repr_by
        )
        assert mu_warm > mu_base, f"served center must warm: {mu_warm} !> {mu_base}"
        # The warming is bounded by the member envelope (no invented value).
        assert mu_warm <= max(z.values())

    def test_absent_repr_byte_identical_center(self):
        """No repr (absent grid cell) ⇒ identical center to pre-Option-C."""
        raw_m2_and_n = {"a": (0.5, 40), "b": (1.0, 40)}
        z = {"a": 28.0, "b": 31.0}
        _, mu_none = raw_precision_center(raw_m2_and_n, z, unit="C")
        _, mu_empty = raw_precision_center(
            raw_m2_and_n, z, unit="C", repr_m2_by_model={}
        )
        assert mu_none == mu_empty

    def test_warming_magnitude_nonzero_on_hot_city_fixture(self):
        """Document the measured warming for a representative hot-city cold-far fixture."""
        # AIFS-style coarse global far from a hot airport (cold), vs a fine nearby member.
        raw_m2_and_n = {"aifs_coarse": (0.5, 40), "hrrr_fine": (1.0, 40)}
        z = {"aifs_coarse": 28.0, "hrrr_fine": 31.0}
        repr_by = {"aifs_coarse": 4.0, "hrrr_fine": 0.0}
        _, mu_base = raw_precision_center(raw_m2_and_n, z, unit="C")
        _, mu_warm = raw_precision_center(
            raw_m2_and_n, z, unit="C", repr_m2_by_model=repr_by
        )
        warming = mu_warm - mu_base
        assert warming > 0.5, f"expected meaningful warming, got {warming:.4f}°C"
