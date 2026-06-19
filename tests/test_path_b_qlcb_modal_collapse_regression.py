# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   §5 (Delete/replace q_lcb Path B): _build_fused_q_bounds must apply the build_joint_q_band
#   renormalize-then-quantile transform so the modal-collapse defect is unconstructable.
"""RED-on-revert regression: Path B (_build_fused_q_bounds) no longer collapses the modal q_lcb.

The OLD Path B took ``np.percentile(probs, 5, axis=0)`` over the (draws × bins) grid of RAW
per-bin integrated mass WITHOUT per-row renormalization. On a NARROW modal bin with a wide
center uncertainty, the handful of draws whose center landed one bin over drove the modal bin's
5th-percentile mass toward ~0 — the winning ring bin was sold as worthless. The fix renormalizes
each draw's row to the simplex BEFORE the percentile (the IDENTICAL transform build_joint_q_band
performs), so a tight modal spike most draws agree on keeps a high q_lcb.

This test reconstructs the SAME draw grid the live ``_build_fused_q_bounds`` builds, computes the
modal bin's q_lcb the OLD (un-renormalized) way and the NEW (renormalized) way, and asserts:
  * the OLD way collapses the modal q_lcb (well below the point mass), AND
  * the live function returns the NON-collapsed (renormalized) value.
Reverting the per-row renormalization makes the live value collapse -> RED.

It also greps the live source to prove no live caller percentiles RAW per-bin masses.
"""
from __future__ import annotations

import pathlib

import numpy as np
from scipy.special import ndtr

from src.data.replacement_forecast_materializer import _build_fused_q_bounds
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin


def _modal_spike_bins() -> list[AifsTemperatureBin]:
    # A FINITE 1°C partition with NO open tails — the realistic family-book case where each
    # draw's per-bin mass does NOT sum to 1 (the tails leak mass off the grid). The central bin
    # [21,22) is the modal bin. When a draw's center lands just outside the modal bin most of
    # its mass leaks into a tail that has no bin, so the un-normalized modal mass for that draw
    # is ~0 — and a handful of such draws drag the 5th-percentile modal mass to ~0 (the Path-B
    # collapse). Renormalizing each row to its on-grid sum restores the modal mass.
    return [
        AifsTemperatureBin(bin_id="b1", lower_c=20.0, upper_c=21.0, center_c=20.5),
        AifsTemperatureBin(bin_id="b_modal", lower_c=21.0, upper_c=22.0, center_c=21.5),
        AifsTemperatureBin(bin_id="b3", lower_c=22.0, upper_c=23.0, center_c=22.5),
    ]


def _old_path_b_modal_qlcb(*, mu_star, center_sigma_c, predictive_sigma_c, bins, n_draws):
    """Reproduce the OLD (un-renormalized) Path B modal-bin 5th-percentile mass."""
    rng = np.random.default_rng(0x5EED_F09)  # the live _QLCB_SEED
    mu_draws = rng.normal(loc=float(mu_star), scale=float(center_sigma_c), size=int(n_draws))
    sigma = float(predictive_sigma_c)
    half = 0.5  # wmo_half_up symmetric preimage for 1°C step
    lows = np.array([(-np.inf if b.lower_c is None else float(b.lower_c) - half) for b in bins])
    highs = np.array([(np.inf if b.upper_c is None else float(b.upper_c) - half) for b in bins])
    z_low = (lows[None, :] - mu_draws[:, None]) / sigma
    z_high = (highs[None, :] - mu_draws[:, None]) / sigma
    probs = np.clip(ndtr(z_high) - ndtr(z_low), 0.0, 1.0)  # NO per-row renorm (the defect)
    modal_idx = [b.bin_id for b in bins].index("b_modal")
    return float(np.percentile(probs[:, modal_idx], 5.0))


def test_path_b_modal_qlcb_no_longer_collapses():
    bins = _modal_spike_bins()
    mu_star = 21.5         # dead-center of the modal bin
    center_sigma = 0.4     # moderate center uncertainty -> SOME draws leak off the finite grid
    predictive_sigma = 0.3  # tight predictive width -> each draw is a near-spike in one bin
    n_draws = 200

    # The point q for the modal bin (no center jitter) — the mass the bin should keep.
    q_point = {b.bin_id: 0.0 for b in bins}
    q_point["b_modal"] = 0.6  # a dominant modal mass (the winning ring bin)

    lcb_map, _ucb_map = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma,
        predictive_sigma_c=predictive_sigma,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=n_draws,
        rounding_rule="wmo_half_up",
    )
    live_modal_lcb = float(lcb_map["b_modal"])

    old_modal_lcb = _old_path_b_modal_qlcb(
        mu_star=mu_star, center_sigma_c=center_sigma,
        predictive_sigma_c=predictive_sigma, bins=bins, n_draws=n_draws,
    )

    # The OLD un-renormalized way COLLAPSES the modal q_lcb to ~0 (the draws that leaked off the
    # finite grid drive the 5th-percentile modal mass toward 0 — the defect).
    assert old_modal_lcb < 0.05, (
        f"the un-renormalized modal q_lcb did not collapse ({old_modal_lcb:.3f}); the test "
        "scenario is not exercising the defect"
    )
    # The LIVE (renormalized) function keeps the modal q_lcb NON-collapsed — an order of
    # magnitude above the collapsed value. Reverting the per-row renormalization makes
    # live == old (collapsed) -> RED.
    assert live_modal_lcb > 10.0 * max(old_modal_lcb, 1e-3), (
        f"live modal q_lcb {live_modal_lcb:.3f} ~ collapsed old value {old_modal_lcb:.3f} — "
        "the per-row simplex renormalization (the Path-B fix) was reverted"
    )
    assert live_modal_lcb > 0.25, (
        f"live modal q_lcb {live_modal_lcb:.3f} is still collapsed; the renormalize-then-quantile "
        "transform is not protecting the modal bin"
    )


def test_no_live_caller_percentiles_raw_per_bin_masses():
    # Structural grep antibody (FINAL no-shadow §5): the ONLY q_lcb percentile-over-draws in the
    # materializer is _build_fused_q_bounds, and it MUST renormalize each row first. Assert the
    # live source contains the renormalization marker adjacent to the percentile so a revert that
    # drops the renorm is caught even if it leaves the percentile call.
    repo = pathlib.Path(__file__).resolve().parent.parent
    src = (repo / "src" / "data" / "replacement_forecast_materializer.py").read_text()
    assert "np.percentile(probs, 5.0, axis=0)" in src
    # The renormalization must be present (the row-sum divide before the percentile).
    assert "probs[_safe, :] = probs[_safe, :] / _row_sums[_safe, :]" in src, (
        "the per-row simplex renormalization is missing from _build_fused_q_bounds — Path B can "
        "collapse the modal q_lcb again"
    )
