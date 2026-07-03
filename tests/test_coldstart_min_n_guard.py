# Created: 2026-06-22
# Last reused/audited: 2026-06-28
# Authority basis: 2026-06-28 operator directive: no-data adds a source at prior
#   precision; thin history must not hard-ban a model, but its raw_m2 is EB-shrunk
#   toward the equal-precision prior until enough settled rows exist.
"""Tests for low-n prior weighting in the RAW center.

The old cold-start guard set weight=0 for n < MIN_SETTLED_N. That prevented one
Denver-style bad new model from contaminating the center, but it also banned new
sources from live math until a fixed sample count. The live law is now continuous:
low-n sources enter with EB shrink-to-equal prior weighting, then sharpen as
settlements accrue.
"""

from __future__ import annotations

import numpy as np

from src.forecast.bayes_precision_fusion import KAPPA, LOWN_INFLATE, SIGMA_FLOOR
from src.forecast.center import MIN_SETTLED_N, raw_precision_center, raw_second_moment_weights


def _m2n(raw_m2: float | None, n: int) -> tuple[float | None, int]:
    return (raw_m2, n)


def _expected_low_n_m2(raw_m2: float, n: int) -> float:
    lam = n / (n + KAPPA)
    equal_m2 = (SIGMA_FLOOR * LOWN_INFLATE) ** 2
    return lam * raw_m2 + (1.0 - lam) * equal_m2


def test_low_n_model_is_prior_weighted_not_excluded() -> None:
    weights = raw_second_moment_weights(
        {
            "mature": _m2n(1.0, MIN_SETTLED_N),
            "low_n": _m2n(1.0, 8),
        }
    )

    assert weights["low_n"] > 0.0
    assert weights["low_n"] < weights["mature"]

    low_n_m2 = _expected_low_n_m2(1.0, 8)
    expected_precisions = np.array([
        1.0 / max(1.0, SIGMA_FLOOR * SIGMA_FLOOR),
        1.0 / max(low_n_m2, SIGMA_FLOOR * SIGMA_FLOOR),
    ])
    expected = expected_precisions / expected_precisions.sum()
    assert np.allclose([weights["mature"], weights["low_n"]], expected, atol=1e-12)


def test_threshold_minus_one_is_still_prior_weighted() -> None:
    weights = raw_second_moment_weights(
        {
            "n29": _m2n(0.5, MIN_SETTLED_N - 1),
            "n30": _m2n(0.5, MIN_SETTLED_N),
        }
    )

    assert weights["n29"] > 0.0
    assert weights["n29"] < weights["n30"]


def test_mature_models_keep_raw_second_moment_ordering() -> None:
    weights = raw_second_moment_weights(
        {
            "best": _m2n(1.00, MIN_SETTLED_N),
            "mid": _m2n(1.50, MIN_SETTLED_N + 20),
            "worst": _m2n(2.25, MIN_SETTLED_N + 30),
        }
    )

    assert weights["best"] > weights["mid"] > weights["worst"]
    assert abs(sum(weights.values()) - 1.0) < 1e-12


def test_no_history_collapses_to_equal_prior() -> None:
    weights = raw_second_moment_weights(
        {
            "a": _m2n(None, 0),
            "b": _m2n(None, 0),
            "c": _m2n(None, 0),
        }
    )

    assert all(abs(weight - 1.0 / 3.0) < 1e-12 for weight in weights.values())


def test_denver_outlier_is_damped_not_banned() -> None:
    mature_z = {
        "ecmwf_ifs": 29.78,
        "icon_global": 29.10,
        "ukmo": 30.92,
        "ncep_nbm_conus": 29.27,
    }
    m2n = {
        "ecmwf_ifs": _m2n(0.20, 87),
        "icon_global": _m2n(0.45, 88),
        "ukmo": _m2n(0.35, 84),
        "ncep_nbm_conus": _m2n(0.25, 84),
        "gfs_hrrr": _m2n(0.15, 8),
    }
    z = {**mature_z, "gfs_hrrr": 33.54}

    weights, mu = raw_precision_center(m2n, z)

    assert weights["gfs_hrrr"] > 0.0
    assert weights["gfs_hrrr"] < 1.0 / len(z)
    assert mu < max(mature_z.values())

    contaminated_equal = sum(z.values()) / len(z)
    mature_only_mu = raw_precision_center(
        {k: v for k, v in m2n.items() if k != "gfs_hrrr"},
        mature_z,
    )[1]
    assert mature_only_mu <= mu < contaminated_equal
    assert contaminated_equal - mu > 0.05


def test_low_n_rule_is_model_name_independent() -> None:
    for model_name in ("gfs_hrrr", "icon_d2", "ncep_nbm_conus", "totally_new_model_2026"):
        weights = raw_second_moment_weights(
            {
                model_name: _m2n(0.5, 8),
                "mature": _m2n(0.5, 50),
            }
        )
        assert weights[model_name] > 0.0
        assert weights[model_name] < weights["mature"]


def test_single_low_n_model_still_gets_full_convex_weight() -> None:
    weights = raw_second_moment_weights({"only_model": _m2n(0.5, 1)})

    assert abs(weights["only_model"] - 1.0) < 1e-12


def test_min_settled_n_constant() -> None:
    assert MIN_SETTLED_N == 30
