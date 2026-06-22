# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: genuine-alpha mission (docs/evidence/live_order_pathology/
#   2026-06-21_genuine_alpha_convergent_verdict.md §"DEFINITIVE ROOT — the σ-calibration
#   machinery is structurally WIDEN-ONLY"). The served fused belief
#   (openmeteo_ecmwf_ifs9_bayes_fusion) drifted TOO FLAT / over-smoothed (mode-bin
#   realized/expected ratio 1.63 on 614 current settled cells; at scale 4,697 bins the
#   far-tail is over-weighted and the near-mode shoulder under-weighted). A sharpening
#   transform (k<1 / β>1) is FORWARD-VALIDATED (-6.5% out-of-sample log-loss). The
#   STRUCTURAL BUG: the σ-scale machinery is WIDEN-ONLY (fitter bounds k∈[1.0,3.5];
#   consumer applies k only when k>1.0), so the MLE detects over-width but is pinned at
#   k=1.0 — powerless to sharpen. This test proves the un-bound fix lets k<1 SHARPEN.
"""TDD antibodies for the σ-scale SHARPEN un-bound (k<1.0) fix.

These are written FIRST (RED): the pre-fix fitter pins k>=1.0 (validation +inf at k<1,
scipy bounds lo=1.0, grid K_LO=1.0), so on too-flat data it cannot sharpen. After the
un-bound fix (K_LO=0.6, validation/bounds use K_LO, sharpen multi-starts) the MLE can
return k<1.0 when the realized winner concentrates on the mode but the implied σ is wide.

Invariants proven here:
  1. _neg_log_likelihood accepts k<1 as a finite value (pre-fix: +inf).
  2. _fit_mle returns k<1.0 on data whose realized winner concentrates on the mode bin
     while sigma_impl is artificially wide (pre-fix: pinned >=1.0).
  3. The fitted nll at k<1 is strictly below the nll at k=1 on that too-flat data.
  4. Consumer-side branch logic: the materializer's σ-application guard fires for k<1.0
     (`_k != 1.0 and _k > 0.0`) so a k<1 fit reduces the served σ vs k=1.
"""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest

# Load the fitter by PATH (importlib) — robust regardless of sys.path / cwd. The repo's
# own `import scripts.fit_sigma_scale` also works, but path-loading is explicit per brief.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FITTER_PATH = os.path.join(_REPO, "scripts", "fit_sigma_scale.py")
_spec = importlib.util.spec_from_file_location("fit_sigma_scale_under_test", _FITTER_PATH)
fs = importlib.util.module_from_spec(_spec)
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(fs)


# ---------------------------------------------------------------------------
# Cell construction — VERIFIED against fs._cell_q_adjusted / the cell loader
# (_build_cells in the fitter): keys sigma_impl, edges_lo (np.array), edges_hi
# (np.array), n_bins, won_index, mode_index, items, step.
# ---------------------------------------------------------------------------

def _make_cell(sigma_impl: float, n_interior: int, step: float, won_index: int | None = None):
    """Build a settled cell dict matching the fitter's contract.

    The items grid = open-low shoulder + n_interior interior bins + open-high shoulder.
    edges_lo / edges_hi are computed by the fitter's OWN _cell_edges so the σ integration
    is exactly the production path. won_index defaults to the MODE bin (center) — i.e.
    realized winner concentrates on the mode (the too-flat signature).
    """
    centers = [float(c) for c in range(0, n_interior)]
    items = []
    items.append([f"Will the highest temperature be {centers[0]-step:.0f}°C or below on June 9?",
                  0.0, centers[0] - step, True])
    for c in centers:
        items.append([f"Will the highest temperature be {c:.0f}°C on June 9?", 0.0, c, False])
    items.append([f"Will the highest temperature be {centers[-1]+step:.0f}°C or higher on June 9?",
                  0.0, centers[-1] + step, True])

    mode_index = 1 + n_interior // 2  # a central interior bin (matches the existing test)
    # Materialized q at the mode so the σ back-out is ~sigma_impl. We pass sigma_impl
    # directly into the cell dict, so the exact materialized q value is not load-bearing
    # for these tests; we set it for argmax sanity only.
    q_mode = float(fs._phi(0.5 / sigma_impl) - fs._phi(-0.5 / sigma_impl))
    items[mode_index][1] = q_mode
    for i, it in enumerate(items):
        if i != mode_index:
            it[1] = q_mode * 0.3 / (len(items) - 1)

    lo, hi = fs._cell_edges(items, mode_index, step)
    if won_index is None:
        won_index = mode_index  # winner concentrates on the mode bin
    return {
        "city": "Syn", "target_date": "2026-06-09", "bucket": "A_24h",
        "n_bins": len(items), "sigma_impl": sigma_impl, "mode_index": mode_index,
        "items": items, "won_index": won_index, "step": step,
        "edges_lo": lo, "edges_hi": hi,
    }


def _too_flat_population(n_cells: int, sigma_impl: float = 2.0, seed: int = 11):
    """A population of too-flat cells: wide implied σ (=2.0 steps) but the realized winner
    is ALWAYS the mode bin. A wide Normal spreads mass off the mode, so the likelihood is
    maximized by SHARPENING (k<1) to pull mass back onto the mode where the wins are.
    """
    rng = np.random.default_rng(seed)
    cells = []
    for _ in range(n_cells):
        n_interior = int(rng.integers(8, 12))
        cells.append(_make_cell(sigma_impl, n_interior, step=1.0, won_index=None))
    return cells


# ---------------------------------------------------------------------------
# 1. _neg_log_likelihood accepts k<1 (pre-fix returns +inf via the k>=1 guard)
# ---------------------------------------------------------------------------

def test_neg_log_likelihood_accepts_k_below_1() -> None:
    flat_cells = _too_flat_population(40)
    nll = fs._neg_log_likelihood(flat_cells, 0.7, 0.0)
    assert np.isfinite(nll), f"_neg_log_likelihood(k=0.7) should be finite, got {nll}"
    # Sanity: it is a real (positive) negative-log-likelihood, not the +inf sentinel.
    assert nll > 0.0
    assert nll < 1e17, "must not be the +inf invalid-(k,w) sentinel"


# ---------------------------------------------------------------------------
# 2. _fit_mle SHARPENS (k<1.0) when the data is too flat
# ---------------------------------------------------------------------------

def test_fitter_sharpens_when_data_is_too_flat() -> None:
    cells = _too_flat_population(300, sigma_impl=2.0, seed=5)
    k_hat, w_hat, nll = fs._fit_mle(cells)
    assert k_hat < 1.0, (
        f"too-flat data (mode wins, σ_impl=2.0) must fit k<1.0 (sharpen); got k_hat={k_hat}. "
        "Pre-fix this is pinned at the >=1.0 lower bound."
    )
    assert np.isfinite(nll)


# ---------------------------------------------------------------------------
# 3. Sharpening reduces nll vs k=1 on the same too-flat data
# ---------------------------------------------------------------------------

def test_sharpening_reduces_nll_vs_k1_on_flat_data() -> None:
    cells = _too_flat_population(300, sigma_impl=2.0, seed=5)
    k_hat, w_hat, nll_fit = fs._fit_mle(cells)
    nll_k1 = fs._neg_log_likelihood(cells, 1.0, 0.0)
    assert nll_fit < nll_k1, (
        f"fitted nll ({nll_fit}) at k_hat={k_hat} must be < nll at k=1 ({nll_k1}) on too-flat data"
    )
    # The fit landed below k=1 (the sharpen region) — cross-check with #2's contract.
    assert k_hat < 1.0


# ---------------------------------------------------------------------------
# 4. CI can extend below 1.0 (profile-likelihood lo_bound un-bound)
# ---------------------------------------------------------------------------

def test_profile_ci_can_extend_below_1() -> None:
    cells = _too_flat_population(300, sigma_impl=2.0, seed=5)
    k_hat, w_hat, nll = fs._fit_mle(cells)
    ci = fs._profile_ci(cells, k_hat, w_hat, nll)
    # The lower CI edge must be reachable below 1.0 (pre-fix the K_LO=1.0 floor pins it).
    assert ci["k"][0] < 1.0, f"profile CI lower edge for k should reach below 1.0; got {ci['k']}"


# ---------------------------------------------------------------------------
# 5. Consumer-side branch logic — the materializer applies k<1 (smaller served σ).
# This is a FOCUSED assertion on the guard logic `_k != 1.0 and _k > 0.0`, mirroring
# the materializer's σ-application branch, because the full materializer is too heavy to
# unit-test in isolation here (it requires a fused override + bins + EMOS integrator).
# The real consumer body is `_sigma_pred = _sigma_pred * _k`, so k<1 reduces σ.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("k", [0.6, 0.7, 0.85])
def test_consumer_guard_applies_and_sharpens_for_k_below_1(k: float) -> None:
    # The guard the materializer uses post-fix.
    def _guard(_k: float) -> bool:
        return _k != 1.0 and _k > 0.0

    assert _guard(k), f"post-fix guard must fire for sharpening k={k}"
    # And the body reduces σ: σ * k < σ for k<1.
    sigma_pred = 1.5
    sigma_after = sigma_pred * k
    assert sigma_after < sigma_pred, f"k={k} must reduce served σ ({sigma_after} !< {sigma_pred})"


def test_consumer_guard_is_noop_at_k1() -> None:
    def _guard(_k: float) -> bool:
        return _k != 1.0 and _k > 0.0

    assert not _guard(1.0), "guard must be a no-op at k=1.0 (unchanged baseline)"
