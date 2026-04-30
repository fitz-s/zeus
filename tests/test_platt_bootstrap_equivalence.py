# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Lifecycle: created=2026-04-29; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Prove Platt group-bootstrap optimization remains algebraically equivalent to the legacy implementation.
# Reuse: Run for calibration Platt bootstrap, CI parameter, or optimization changes.
# Authority basis: docs/reference/zeus_calibration_weighting_authority.md (perf antibody);
#                  refit_platt_v2 hot-loop optimization 2026-04-29 — replace O(n_eff × N)
#                  object-dtype string equality with O(N) integer code lookup.
"""Equivalence test: ExtendedPlattCalibrator group-bootstrap optimization.

Asserts the int-coded group-bootstrap (post-2026-04-29) produces bit-precise
identical (A, B, C, bootstrap_params) compared to the legacy string-equality
implementation, given the same fixed seed.

The optimization replaces:
  unique_groups = sorted({str(g) for g in group_ids})  # object-dtype array
  for _ in range(n_bootstrap):
      sampled = rng.choice(unique_groups, n_eff, replace=True)
      idx = np.concatenate([flatnonzero(group_ids == g) for g in sampled])

with:
  unique_groups, inverse = np.unique(group_strs, return_inverse=True)
  group_to_indices = [flatnonzero(inverse == k) for k in range(n_groups)]
  for _ in range(n_bootstrap):
      codes = rng.choice(n_groups, size=n_groups, replace=True)
      idx = np.concatenate([group_to_indices[k] for k in codes])

Both consume identical RNG state because numpy Generator.choice(array, size) and
Generator.choice(int, size) both reduce to the same integers(0, n, size) draw.

If this test fails, the optimization has drifted from algebraic equivalence and
must NOT be deployed — it would silently change Platt parameter CI estimates
across the entire training pipeline.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.calibration.platt import ExtendedPlattCalibrator


def _legacy_fit(
    p_raw: np.ndarray,
    lead_days: np.ndarray,
    outcomes: np.ndarray,
    decision_group_ids: np.ndarray,
    *,
    n_bootstrap: int,
    seed: int,
    regularization_C: float = 1.0,
) -> ExtendedPlattCalibrator:
    """Reference implementation matching pre-2026-04-29 behavior."""
    from sklearn.linear_model import LogisticRegression
    from src.calibration.platt import (
        WIDTH_NORMALIZED_SPACE,
        RAW_PROBABILITY_SPACE,
        logit_safe,
    )

    cal = ExtendedPlattCalibrator()

    rng = np.random.default_rng(seed)
    group_ids = np.asarray(decision_group_ids, dtype=object)
    unique_groups = np.array(
        sorted({str(g) for g in group_ids}), dtype=object
    )

    X = ExtendedPlattCalibrator._build_features(p_raw, lead_days, bin_widths=None)
    cal.input_space = RAW_PROBABILITY_SPACE

    lr = LogisticRegression(C=regularization_C, solver="lbfgs", max_iter=1000)
    lr.fit(X, outcomes)
    cal.A = float(lr.coef_[0][0])
    cal.B = float(lr.coef_[0][1])
    cal.C = float(lr.intercept_[0])
    cal.n_samples = len(unique_groups)
    cal.fitted = True

    cal.bootstrap_params = []
    for _ in range(n_bootstrap):
        sampled_groups = rng.choice(
            unique_groups, len(unique_groups), replace=True
        )
        idx = np.concatenate(
            [
                np.flatnonzero(group_ids == group_id)
                for group_id in sampled_groups
            ]
        )
        try:
            lr_b = LogisticRegression(
                C=regularization_C, solver="lbfgs", max_iter=1000
            )
            lr_b.fit(X[idx], outcomes[idx])
            cal.bootstrap_params.append(
                (
                    float(lr_b.coef_[0][0]),
                    float(lr_b.coef_[0][1]),
                    float(lr_b.intercept_[0]),
                )
            )
        except Exception:
            continue
    return cal


@pytest.fixture
def synthetic_dataset():
    """50 distinct decision groups × 30 bins each = 1500 pairs."""
    rng = np.random.default_rng(seed=20260429)
    n_groups = 50
    bins_per_group = 30
    n = n_groups * bins_per_group

    p_raw = rng.uniform(0.01, 0.99, size=n)
    lead_days = rng.uniform(0.5, 7.5, size=n)
    # Outcome correlated with p_raw + lead noise so the LR fit is non-degenerate
    logits = 1.5 * np.log(p_raw / (1 - p_raw)) - 0.05 * lead_days
    probs = 1.0 / (1.0 + np.exp(-logits))
    outcomes = (rng.uniform(0, 1, size=n) < probs).astype(int)

    group_ids = np.repeat(
        [f"group_{i:03d}" for i in range(n_groups)], bins_per_group
    )
    return p_raw, lead_days, outcomes, group_ids


def test_primary_fit_bit_precise(synthetic_dataset):
    """Primary (A, B, C) point estimate is RNG-independent and must match exactly."""
    p_raw, lead_days, outcomes, group_ids = synthetic_dataset

    legacy = _legacy_fit(
        p_raw, lead_days, outcomes, group_ids,
        n_bootstrap=5, seed=42,
    )
    new = ExtendedPlattCalibrator()
    new.fit(
        p_raw, lead_days, outcomes,
        decision_group_ids=group_ids,
        n_bootstrap=5,
        rng=np.random.default_rng(42),
    )
    assert new.A == legacy.A, f"A mismatch: {new.A} vs {legacy.A}"
    assert new.B == legacy.B, f"B mismatch: {new.B} vs {legacy.B}"
    assert new.C == legacy.C, f"C mismatch: {new.C} vs {legacy.C}"
    assert new.n_samples == legacy.n_samples


def test_bootstrap_params_bit_precise(synthetic_dataset):
    """Bootstrap params must match BIT-PRECISELY when same seed feeds both impls.

    rng.choice(array, size) and rng.choice(int, size) consume identical RNG state.
    Combined with deterministic LogisticRegression(solver=lbfgs), each bootstrap
    iteration must produce the same (A_i, B_i, C_i).
    """
    p_raw, lead_days, outcomes, group_ids = synthetic_dataset

    legacy = _legacy_fit(
        p_raw, lead_days, outcomes, group_ids,
        n_bootstrap=20, seed=12345,
    )
    new = ExtendedPlattCalibrator()
    new.fit(
        p_raw, lead_days, outcomes,
        decision_group_ids=group_ids,
        n_bootstrap=20,
        rng=np.random.default_rng(12345),
    )

    assert len(new.bootstrap_params) == len(legacy.bootstrap_params), (
        f"length mismatch: new={len(new.bootstrap_params)} legacy={len(legacy.bootstrap_params)}"
    )
    for i, (n_p, l_p) in enumerate(zip(new.bootstrap_params, legacy.bootstrap_params)):
        assert n_p == l_p, (
            f"bootstrap[{i}] mismatch: new={n_p} legacy={l_p}"
        )


def test_unsorted_group_ids_still_match(synthetic_dataset):
    """Group order in input must not affect output (np.unique sorts internally)."""
    p_raw, lead_days, outcomes, group_ids = synthetic_dataset

    perm = np.random.default_rng(seed=1).permutation(len(p_raw))
    p_raw_p = p_raw[perm]
    lead_days_p = lead_days[perm]
    outcomes_p = outcomes[perm]
    group_ids_p = group_ids[perm]

    legacy_perm = _legacy_fit(
        p_raw_p, lead_days_p, outcomes_p, group_ids_p,
        n_bootstrap=10, seed=999,
    )
    new_perm = ExtendedPlattCalibrator()
    new_perm.fit(
        p_raw_p, lead_days_p, outcomes_p,
        decision_group_ids=group_ids_p,
        n_bootstrap=10,
        rng=np.random.default_rng(999),
    )
    for i, (n_p, l_p) in enumerate(zip(new_perm.bootstrap_params, legacy_perm.bootstrap_params)):
        assert n_p == l_p, f"perm bootstrap[{i}] mismatch"


def test_minimum_15_groups_still_enforced():
    """Maturity gate (n_eff < 15) must still raise."""
    rng = np.random.default_rng(7)
    p_raw = rng.uniform(0.01, 0.99, size=42)
    lead_days = rng.uniform(0.5, 7.5, size=42)
    outcomes = rng.integers(0, 2, size=42)
    group_ids = np.repeat([f"g{i}" for i in range(14)], 3)

    cal = ExtendedPlattCalibrator()
    with pytest.raises(ValueError, match="n_eff=14 < 15"):
        cal.fit(
            p_raw, lead_days, outcomes,
            decision_group_ids=group_ids, n_bootstrap=5,
        )
