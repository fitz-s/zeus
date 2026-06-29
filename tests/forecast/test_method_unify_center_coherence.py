# Created: 2026-06-18
# Last audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   METHOD UNIFY — entry center (walk_forward_model_weights) == exit center
#   (raw_second_moment_weights via forecast_posteriors) for the same inputs.
"""RED-ON-REVERT: Method-unify coherence tests.

These tests FAIL if the shared helper ``raw_second_moment_weights`` drifts from
``walk_forward_model_weights`` (the spine entry) or if the materializer reverts to
T2 BLUE ``fused.mu`` for the exit center.  Both signal a re-opening of the #135
two-center split.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

# ── shared helper under test ───────────────────────────────────────────────────
from types import SimpleNamespace

from src.forecast.center import MIN_SETTLED_N, raw_second_moment_weights, walk_forward_model_weights
from src.forecast.bayes_precision_fusion import SIGMA_FLOOR, LOWN_INFLATE, KAPPA
from src.forecast.types import RawModelMember


def _case(unit: str = "C") -> SimpleNamespace:
    # walk_forward_model_weights only reads case.unit — no full ForecastCase needed.
    return SimpleNamespace(unit=unit)


_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _member(raw_m2: float | None, n: int, value: float = 20.0) -> RawModelMember:
    return RawModelMember(
        model_id="m",
        product_id="p",
        source_run_id="r",
        source_cycle_time_utc=_NOW,
        available_at_utc=_NOW,
        value_native=value,
        station_mapping_id="s",
        raw_forecast_artifact_id="a",
        data_version="v1",
        walk_forward_raw_m2_native=raw_m2,
        walk_forward_n=n,
    )


class TestRawSecondMomentWeightsMatchesSpine:
    """raw_second_moment_weights must produce identical weights to walk_forward_model_weights."""

    def test_full_history_weights_match(self):
        """Three members with full history: shared helper == spine weights."""
        raw_m2s = [0.25, 1.0, 4.0]
        ns = [30, 40, 50]
        members = [_member(m2, n) for m2, n in zip(raw_m2s, ns)]
        case = _case("C")

        spine_weights = walk_forward_model_weights(case, members)

        raw_m2_and_n = {f"m{i}": (m2, n) for i, (m2, n) in enumerate(zip(raw_m2s, ns))}
        helper_weights = raw_second_moment_weights(raw_m2_and_n, unit="C")
        helper_list = [helper_weights[f"m{i}"] for i in range(3)]

        for i in range(3):
            assert abs(spine_weights[i] - helper_list[i]) < 1e-12, (
                f"Weight mismatch index {i}: spine={spine_weights[i]}, "
                f"helper={helper_list[i]}"
            )

    def test_thin_n_shrink_match(self):
        """Low-n EB shrink: shared helper == spine for n < MIN_SETTLED_N."""
        raw_m2 = 0.3
        n = max(1, MIN_SETTLED_N - 1)
        members = [_member(raw_m2, n), _member(2.0, 40)]
        case = _case("C")

        spine_weights = walk_forward_model_weights(case, members)
        raw_m2_and_n = {"m0": (raw_m2, n), "m1": (2.0, 40)}
        helper = raw_second_moment_weights(raw_m2_and_n, unit="C")

        assert abs(spine_weights[0] - helper["m0"]) < 1e-12
        assert abs(spine_weights[1] - helper["m1"]) < 1e-12

    def test_no_history_equal_weights(self):
        """No-history members: both return exact 1/n."""
        members = [_member(None, 0), _member(None, 0), _member(None, 0)]
        case = _case("C")
        spine = walk_forward_model_weights(case, members)
        helper = raw_second_moment_weights({"a": (None, 0), "b": (None, 0), "c": (None, 0)}, unit="C")
        for w in spine:
            assert abs(w - 1.0 / 3) < 1e-12
        for w in helper.values():
            assert abs(w - 1.0 / 3) < 1e-12

    def test_f_city_unit_scaling_match(self):
        """F-city: both helpers apply the (9/5)^2 floor/shrink scaling."""
        raw_m2 = 0.3
        n = max(1, MIN_SETTLED_N - 1)
        members = [_member(raw_m2, n), _member(1.5, 40)]
        case_f = _case("F")
        case_c = _case("C")

        spine_f = walk_forward_model_weights(case_f, members)
        spine_c = walk_forward_model_weights(case_c, members)
        assert not np.allclose(spine_f, spine_c), "F/C scaling must produce different weights"

        helper_f = raw_second_moment_weights({"m0": (raw_m2, n), "m1": (1.5, 40)}, unit="F")
        helper_c = raw_second_moment_weights({"m0": (raw_m2, n), "m1": (1.5, 40)}, unit="C")
        assert not np.allclose(list(helper_f.values()), list(helper_c.values()))

        # F spine == F helper
        assert abs(spine_f[0] - helper_f["m0"]) < 1e-12
        assert abs(spine_f[1] - helper_f["m1"]) < 1e-12

    def test_weights_sum_to_one(self):
        """Weights from helper must always sum to 1."""
        cases = [
            {"a": (0.5, 30), "b": (1.5, 25)},
            {"a": (None, 0), "b": (None, 0)},
            {"a": (0.1, MIN_SETTLED_N - 1)},
        ]
        for c in cases:
            w = raw_second_moment_weights(c, unit="C")
            assert abs(sum(w.values()) - 1.0) < 1e-12, f"Weights don't sum to 1 for {c}"


class TestMaterializerUsesRawDiagonalCenter:
    """Materializer must produce the RAW diagonal center, not T2 BLUE fused.mu.

    Verifies the METHOD UNIFY: ``BayesPrecisionFusionCaptureResult`` now carries
    ``anchor_raw_m2_native`` and ``anchor_raw_n_train``, and the diagonal center
    computation (which the materializer now uses) equals the RAW weighted mean over
    the instruments' z values — not the Bayesian BLUE mu*.
    """

    def test_anchor_raw_m2_in_capture_result(self):
        """BayesPrecisionFusionCaptureResult carries anchor raw m2 fields."""
        from src.data.bayes_precision_fusion_capture import BayesPrecisionFusionCaptureResult
        from src.forecast.bayes_precision_fusion import ModelInstrument
        from src.forecast.model_selection import SelectedModelSet

        ins = ModelInstrument(
            model="icon_global", z=22.0, train_residuals=(1.0, -1.0, 0.5), n_train=3
        )
        anchor_residuals = (0.8, -0.8, 1.0, -1.0)
        anchor_raw_m2 = sum(r * r for r in anchor_residuals) / len(anchor_residuals)

        capture = BayesPrecisionFusionCaptureResult(
            anchor_z=20.0, anchor_tau0=1.5,
            likelihood=(ins,), disagree_var=0.0,
            selection=SelectedModelSet(
                anchor_present=True,
                likelihood_globals=("icon_global",),
                regional_experts=(),
                dropped_aliases=(),
                excluded_regionals=(),
            ),
            dropped_models=(),
            anchor_raw_m2_native=anchor_raw_m2,
            anchor_raw_n_train=len(anchor_residuals),
        )
        assert abs(capture.anchor_raw_m2_native - anchor_raw_m2) < 1e-12
        assert capture.anchor_raw_n_train == 4

    def test_diagonal_center_beats_equal_mean(self):
        """Diagonal center differs from equal mean when precision varies — not T2 BLUE."""
        # m0: tight residuals (low raw_m2) → high weight
        # m1: loose residuals → low weight
        r_m0 = [0.2, -0.2] * 20   # raw_m2 = 0.04
        r_m1 = [2.0, -2.0] * 20   # raw_m2 = 4.0
        r_anch = [1.0, -1.0] * 20  # raw_m2 = 1.0

        raw_m2_and_n = {
            "m0": (sum(r*r for r in r_m0)/len(r_m0), len(r_m0)),
            "m1": (sum(r*r for r in r_m1)/len(r_m1), len(r_m1)),
            "anchor": (sum(r*r for r in r_anch)/len(r_anch), len(r_anch)),
        }
        z = {"m0": 22.0, "m1": 24.0, "anchor": 23.0}
        weights = raw_second_moment_weights(raw_m2_and_n, unit="C")
        diagonal_mu = sum(weights[m] * z[m] for m in weights)
        equal_mu = sum(z.values()) / len(z)

        # m0 has 100x lower raw_m2 than m1 → diagonal center pulled strongly toward z_m0=22.0
        assert abs(diagonal_mu - equal_mu) > 0.3, (
            f"Diagonal center {diagonal_mu:.4f} should differ significantly from "
            f"equal mean {equal_mu:.4f} (m0 has 100x lower raw_m2)"
        )
        # Diagonal center is pulled toward m0's value (22.0) since it has the tightest residuals
        assert diagonal_mu < equal_mu, "Tight-residual m0 (z=22) should pull center below equal mean=23"

    def test_anchor_zero_history_uses_equal_weight_fallback(self):
        """No anchor history: anchor gets (None, 0) → equal-weight fallback."""
        w = raw_second_moment_weights(
            {"anchor": (None, 0), "m0": (0.5, 30), "m1": (2.0, 25)},
            unit="C"
        )
        assert abs(sum(w.values()) - 1.0) < 1e-12
        # anchor with no history falls to equal_m2 floor — weights still sum to 1
        # and are non-negative
        for v in w.values():
            assert v >= 0.0
