# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 2 — covariance rows must be aligned by target_date (not positional index); fuse_u0r_posterior must use the date-aligned common window, not equal-length same-index assumption.
# Reuse: Run with pytest; update if residual matrix date-alignment logic in fuse_u0r_posterior changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §2 T2 fusion (covariance estimated over the COMMON
#   estimation window), §4 algorithm (residual matrix over dates where ALL selected models
#   are present). Fitz Constraint: relationship test — when Module A (U0RHistoryProvider /
#   capture) emits per-model residual vectors of EQUAL LENGTH but DIFFERENT target_dates,
#   Module B (fuse_u0r_posterior) must NOT place them in the same covariance row. The cross-
#   module invariant: covariance rows are aligned by target_date, NEVER by positional index.
"""BLOCKER 2 (the critical stat bug) — covariance must use the date-aligned COMMON window.

Before the fix, fuse_u0r_posterior built the covariance matrix M from per-instrument residual
vectors whose ONLY check was equal len(train_residuals). Residuals from DIFFERENT target_dates
got stacked into the same covariance row -> a FAKE covariance -> Sigma^-1 fusion weights wrong.

The fix threads date-indexed residuals (ModelInstrument.residuals_by_date) and builds M ONLY
over the INTERSECTION of target_dates across the selected instruments. This test proves the
covariance is computed on the date-intersection, not the positional stack.
"""
from __future__ import annotations

import numpy as np

from src.forecast.u0r_bayes import (
    ModelInstrument,
    fuse_u0r_posterior,
    shrink_cov,
)


def _inst(model: str, z: float, residuals_by_date: dict[str, float]) -> ModelInstrument:
    return ModelInstrument(
        model=model,
        z=z,
        train_residuals=tuple(residuals_by_date.values()),
        residuals_by_date=dict(residuals_by_date),
        n_train=len(residuals_by_date),
    )


def test_covariance_built_over_target_date_intersection_only() -> None:
    """Two instruments share dates d1..d6 but instrument A also has d0 and instrument B also
    has d7 (each non-overlapping). The common window is d1..d6. The fused Sigma MUST be the
    Ledoit-Wolf shrink of the residual matrix over d1..d6 ONLY — the d0/d7 rows are excluded
    because the OTHER instrument has no residual on those dates."""
    common = {
        "2026-05-01": (0.5, -0.4),
        "2026-05-02": (-0.3, 0.6),
        "2026-05-03": (0.8, -0.7),
        "2026-05-04": (-0.6, 0.5),
        "2026-05-05": (0.2, -0.1),
        "2026-05-06": (-0.9, 0.9),
    }
    a_dates = {d: ab[0] for d, ab in common.items()}
    a_dates["2026-04-30"] = 5.0  # A-only date (must be excluded from the common window)
    b_dates = {d: ab[1] for d, ab in common.items()}
    b_dates["2026-05-07"] = -5.0  # B-only date (must be excluded)

    ins_a = _inst("gfs_global", z=0.1, residuals_by_date=a_dates)
    ins_b = _inst("icon_global", z=-0.1, residuals_by_date=b_dates)

    fused = fuse_u0r_posterior(
        anchor_z=0.0, anchor_tau0=1.0, likelihood=[ins_a, ins_b], use_covariance=True
    )
    assert fused.method == "T2_BAYES"

    # Reconstruct the EXPECTED Sigma over the date-intersection only.
    common_dates = sorted(set(a_dates) & set(b_dates))
    M_expected = np.array(
        [[a_dates[d] for d in common_dates], [b_dates[d] for d in common_dates]]
    ).T  # rows=dates, cols=models
    Sigma_expected = shrink_cov(M_expected)

    # The fused posterior must be reproducible from the date-aligned Sigma (NOT the positional
    # stack that would include the 5.0/-5.0 outliers and blow the off-diagonal up).
    from src.forecast.u0r_bayes import bayes_fuse

    mu_exp, sd_exp = bayes_fuse(
        np.array([ins_a.z, ins_b.z]), Sigma_expected, 0.0, 1.0, 0.0
    )
    assert abs(fused.mu - mu_exp) < 1e-9, (
        f"fused mu {fused.mu} must match the date-intersection Sigma fusion {mu_exp}"
    )
    assert abs(fused.sd - sd_exp) < 1e-9


def test_positional_stack_of_misaligned_dates_would_differ() -> None:
    """Guard: the positional (WRONG) stack — which pairs A's d0 outlier with B's d1 — would
    produce a materially different Sigma. This test pins that the implementation does NOT use
    the positional stack by showing the two answers diverge, so the date-alignment is load-
    bearing (not a no-op rename)."""
    a_dates = {
        "2026-04-30": 5.0,  # A-only outlier
        "2026-05-01": 0.5,
        "2026-05-02": -0.3,
        "2026-05-03": 0.8,
        "2026-05-04": -0.6,
        "2026-05-05": 0.2,
    }
    b_dates = {
        "2026-05-01": -0.4,
        "2026-05-02": 0.6,
        "2026-05-03": -0.7,
        "2026-05-04": 0.5,
        "2026-05-05": -0.1,
        "2026-05-07": -5.0,  # B-only outlier
    }
    ins_a = _inst("gfs_global", z=0.1, residuals_by_date=a_dates)
    ins_b = _inst("icon_global", z=-0.1, residuals_by_date=b_dates)

    fused = fuse_u0r_posterior(
        anchor_z=0.0, anchor_tau0=1.0, likelihood=[ins_a, ins_b], use_covariance=True
    )

    # WRONG positional stack (what the old equal-length code did): pairs index-by-index.
    M_positional = np.array(
        [list(a_dates.values()), list(b_dates.values())]
    ).T
    Sigma_positional = shrink_cov(M_positional)
    from src.forecast.u0r_bayes import bayes_fuse

    mu_pos, _ = bayes_fuse(np.array([ins_a.z, ins_b.z]), Sigma_positional, 0.0, 1.0, 0.0)

    assert abs(fused.mu - mu_pos) > 1e-6, (
        "the date-aligned fusion must DIFFER from the positional-stack fusion (the outliers "
        "at non-overlapping dates must not enter the same covariance row)"
    )
