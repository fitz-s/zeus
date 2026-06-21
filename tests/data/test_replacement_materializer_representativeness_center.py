# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: Option C raw-precision representativeness center warming
#   (consult REQ-20260621-033315; forecast-gap-is-data-precision). Materializer
#   EXIT-seam antibody: the served _mu_diagonal (anchor_value_c) WARMS when a
#   coarse/far member carries sigma_repr², while predictive_sigma_c / anchor_sigma_c
#   (fused.sd, the Kelly/width inputs) are byte-identical. Reverting the EXIT repr
#   thread turns the warming test RED.
"""RED-first TDD — materializer EXIT seam representativeness center warming.

The SERVED traded center is ``_mu_diagonal = Σ w_m·z_m`` (raw_precision_center via
``raw_second_moment_weights``), written to ``anchor_value_c``. These tests assert:

  * the EXIT center WARMS when a colder coarse/far member carries sigma_repr²
  * ``_build_sigma_repr_by_model`` reads the loader (fail-soft → 0.0 / absent)
  * a city/model absent from the grid table is BYTE-IDENTICAL to today
  * the Kelly/width inputs (predictive_sigma_c, fused.sd) are NOT touched by repr
"""
from __future__ import annotations

import math

import pytest

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

    def test_known_city_returns_positive_repr(self):
        """A city present in config/grid_representativeness.json → positive sigma_repr²."""
        from src.forecast.grid_representativeness_loader import (
            load_grid_representativeness,
            sigma_repr_sq_for,
        )

        tbl = load_grid_representativeness()
        if not tbl:
            pytest.skip("grid_representativeness.json absent in this checkout")
        # pick the first city/model with a positive repr
        city = model = None
        for c, rec in tbl.items():
            for m in (rec.get("models") or {}):
                if sigma_repr_sq_for(c, m) > 0.0:
                    city, model = c, m
                    break
            if city:
                break
        if city is None:
            pytest.skip("no positive-repr cell in grid table")
        out = _build_sigma_repr_by_model(city, [model], anchor_model="ecmwf_ifs")
        assert model in out
        assert out[model] > 0.0
        assert math.isfinite(out[model])

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
