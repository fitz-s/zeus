# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 2 — when the date-intersection window is too short, fuse_bayes_precision_posterior must collapse to diagonal C0 rather than attempt Ledoit-Wolf on an unreliable short window.
# Reuse: Run with pytest; update if the covariance-reliability threshold or diagonal-collapse logic in fuse_bayes_precision_posterior changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §2/§4 PARSIMONY rule (covariance-aware ONLY where Sigma
#   is reliably estimable; collapse to diagonal C0 when the common window is too short). The
#   threshold mirrors the proof's "M.shape[0] >= 5 -> shrink_cov else diag_cov".
"""BLOCKER 2 — when the date-intersection is too short, fall back to a diagonal Sigma.

Even when instruments share SOME dates, if the count of common target_dates is below the
covariance-reliability threshold, the fusion must NOT attempt a Ledoit-Wolf shrink of a 2-row
matrix (an unreliable off-diagonal). It must collapse to the diagonal C0 over the common
window. This pins the fallback boundary on the COMMON-DATE count, not the raw vector length.
"""
from __future__ import annotations

import numpy as np

from src.forecast.bayes_precision_fusion import (
    ModelInstrument,
    diag_cov,
    fuse_bayes_precision_posterior,
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


def test_low_common_date_count_uses_diag_cov_not_shrink_cov() -> None:
    """Each instrument has 20 residuals, but they share only 3 target_dates (below the >=5
    shrink threshold). The fusion MUST use diag_cov over the 3 common dates, NOT shrink_cov."""
    common = {
        "2026-05-10": (0.5, -0.4),
        "2026-05-11": (-0.3, 0.6),
        "2026-05-12": (0.8, -0.7),
    }  # only 3 shared dates
    a = {d: ab[0] for d, ab in common.items()}
    b = {d: ab[1] for d, ab in common.items()}
    # Pad each with many NON-shared dates so the raw lengths are large (>=5) but the COMMON
    # window stays at 3.
    for i in range(17):
        a[f"2026-04-{i + 1:02d}"] = 0.1 * (i % 3)
        b[f"2026-06-{i + 1:02d}"] = -0.1 * (i % 3)

    ins_a = _inst("gfs_global", z=0.1, residuals_by_date=a)
    ins_b = _inst("icon_global", z=-0.1, residuals_by_date=b)

    fused = fuse_bayes_precision_posterior(
        anchor_z=0.0, anchor_tau0=1.0, likelihood=[ins_a, ins_b], use_covariance=True
    )
    assert fused.method == "T2_BAYES"

    common_dates = sorted(set(a) & set(b))
    assert len(common_dates) == 3
    M = np.array([[a[d] for d in common_dates], [b[d] for d in common_dates]]).T
    lown = [ins_a.n_train < 25, ins_b.n_train < 25]
    Sigma_diag = diag_cov(M, lown)
    Sigma_shrink = shrink_cov(M)

    from src.forecast.bayes_precision_fusion import bayes_fuse

    mu_diag, sd_diag = bayes_fuse(np.array([ins_a.z, ins_b.z]), Sigma_diag, 0.0, 1.0, 0.0)
    mu_shrink, _ = bayes_fuse(np.array([ins_a.z, ins_b.z]), Sigma_shrink, 0.0, 1.0, 0.0)

    assert abs(fused.mu - mu_diag) < 1e-9, (
        f"with only {len(common_dates)} common dates (<5) the fusion must use diag_cov "
        f"({mu_diag}), not shrink_cov ({mu_shrink}); got {fused.mu}"
    )


def test_empty_common_window_falls_back_to_diagonal() -> None:
    """Zero common dates -> the covariance cannot be estimated at all -> diagonal fallback.
    The fused posterior must still be finite (fail-soft) and method T2_BAYES (anchor + extras)."""
    ins_a = _inst("gfs_global", z=0.1, residuals_by_date={"2026-04-01": 0.5, "2026-04-02": -0.5})
    ins_b = _inst("icon_global", z=-0.1, residuals_by_date={"2026-05-01": 0.4, "2026-05-02": -0.4})
    fused = fuse_bayes_precision_posterior(
        anchor_z=0.0, anchor_tau0=1.0, likelihood=[ins_a, ins_b], use_covariance=True
    )
    assert fused.method == "T2_BAYES"
    assert np.isfinite(fused.mu) and fused.sd > 0.0
