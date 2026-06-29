# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: Option C raw-precision representativeness center warming
#   (consult REQ-20260621-033315; forecast-gap-is-data-precision). Kelly-no-double-
#   count BLOCKER guard: grid-representativeness enters the RAW MEAN weights ONLY
#   (mu_served / weights / center / q-via-μ). It must NOT touch the predictive σ /
#   Kelly width inputs (predictive_sigma_c, anchor_sigma_c = fused.sd). Threading
#   repr into fused.sd / predictive_sigma_c would turn these RED.
"""RED-on-revert — representativeness must not widen the served predictive σ / Kelly.

The served center ``_mu_diagonal`` is reweighted by repr (raw_precision_center), but
the predictive point width ``predictive_sigma_c = served_predictive_sigma_c(σ_resid)``
and the center uncertainty ``anchor_sigma_c = fused.sd`` are produced by DIFFERENT
paths. The center repr channel never touches either width path. These tests pin
that decoupling.
"""
from __future__ import annotations

import inspect

import numpy as np

from src.forecast.center import raw_precision_center, raw_second_moment_weights


# ============================================================================
# Unit-level: toggling repr changes weights/center but is a pure-mean operation —
# it produces no σ / width output at all (the helper returns weights + Σwz only).
# ============================================================================
class TestReprIsMeanOnly:
    def test_repr_changes_weights_and_center(self):
        d = {"far": (1.0, 40), "near": (1.0, 40)}
        z = {"far": 28.0, "near": 31.0}
        w_base, mu_base = raw_precision_center(d, z, unit="C")
        w_repr, mu_repr = raw_precision_center(
            d, z, unit="C", repr_m2_by_model={"far": 4.0}
        )
        # The mean side MOVES (this is the intended q-via-μ change).
        assert mu_repr != mu_base
        assert w_repr["far"] != w_base["far"]

    def test_helper_returns_only_weights_and_mu_no_sigma(self):
        """raw_precision_center returns (weights, mu) — there is no σ channel to widen."""
        out = raw_precision_center({"a": (1.0, 40)}, {"a": 30.0}, unit="C")
        assert isinstance(out, tuple) and len(out) == 2
        weights, mu = out
        assert isinstance(weights, dict)
        assert isinstance(mu, float)


# ============================================================================
# Structural: the predictive_sigma_c formula in the materializer depends ONLY on
# the realized residual series helper — NOT on _sigma_repr_by_model /
# raw_precision_center, and NOT on fused.sd. fused.sd is center uncertainty
# carried as anchor_sigma_c, not point width. A source-level antibody: if a future
# edit feeds repr or fused.sd into the point-width path, this fails.
# ============================================================================
class TestPredictiveSigmaDecoupledFromRepr:
    def test_predictive_sigma_c_does_not_read_repr_symbols(self):
        import src.data.replacement_forecast_materializer as mat

        src = inspect.getsource(mat._replacement_bayes_precision_fusion_override)
        # Locate the predictive_sigma_c assignment line.
        sigma_lines = [
            ln for ln in src.splitlines() if "predictive_sigma_c = served_predictive_sigma_c" in ln
        ]
        assert sigma_lines, "predictive_sigma_c assignment not found"
        sigma_line = sigma_lines[0]
        # It must be built from the realized residual std helper, NOT from repr or fused.sd.
        assert "_sigma_resid" in sigma_line
        for forbidden in ("fused.sd", "_sigma_repr_by_model", "repr_m2", "representativeness"):
            assert forbidden not in sigma_line, (
                f"predictive_sigma_c must not reference {forbidden} (Kelly double-count)"
            )

    def test_anchor_sigma_c_is_pure_fused_sd(self):
        import src.data.replacement_forecast_materializer as mat

        src = inspect.getsource(mat._replacement_bayes_precision_fusion_override)
        anchor_sigma_lines = [
            ln.strip() for ln in src.splitlines() if "anchor_sigma_c=" in ln
        ]
        assert anchor_sigma_lines, "anchor_sigma_c constructor arg not found"
        # anchor_sigma_c=float(fused.sd) — never the repr channel.
        assert any("fused.sd" in ln for ln in anchor_sigma_lines)
        for ln in anchor_sigma_lines:
            assert "repr" not in ln

    def test_repr_dict_only_feeds_raw_precision_center(self):
        """_sigma_repr_by_model must be passed ONLY to raw_precision_center (the mean), and
        never to fuse_bayes_precision_posterior (the covariance/width)."""
        import src.data.replacement_forecast_materializer as mat

        src = inspect.getsource(mat._replacement_bayes_precision_fusion_override)
        # Every use of the repr dict is on a raw_precision_center call, never a fuse call.
        for ln in src.splitlines():
            if "_sigma_repr_by_model" in ln and "=" not in ln.split("_sigma_repr_by_model")[0][-3:]:
                # a USE (not the assignment): must be the center call, not the fuse call.
                if "fuse_bayes_precision_posterior" in ln:
                    raise AssertionError(
                        "repr dict fed into fuse_bayes_precision_posterior (Kelly double-count)"
                    )
        # And the capture's apply_grid_representativeness width switch must NOT be enabled
        # by this center fix (the WIDTH path that DOES feed fused.sd).
        assert "apply_grid_representativeness=True" not in src, (
            "the center fix must not enable the WIDTH-path repr switch (double-count)"
        )


# ============================================================================
# Capture WIDTH path stays off: enabling apply_grid_representativeness is the
# documented double-count seam (it feeds ModelInstrument.sigma_repr_sq -> Bayes
# covariance -> fused.sd). Confirm the center fix did not flip its default.
# ============================================================================
class TestWidthPathSwitchUnchanged:
    def test_apply_grid_representativeness_default_off(self):
        import inspect as _inspect

        import src.data.bayes_precision_fusion_capture as cap

        # The capture builder's apply_grid_representativeness parameter must default False
        # (the center fix does not touch the width path).
        fn = None
        for name, obj in _inspect.getmembers(cap, _inspect.isfunction):
            sig = _inspect.signature(obj)
            if "apply_grid_representativeness" in sig.parameters:
                fn = obj
                default = sig.parameters["apply_grid_representativeness"].default
                assert default is False, (
                    f"{name}: apply_grid_representativeness must default False, got {default}"
                )
        assert fn is not None, "no builder with apply_grid_representativeness found"
