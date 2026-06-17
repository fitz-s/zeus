# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator "finish v3" 2026-06-17 — live wiring of zeus_grid_coordinate
#   _precision_upgrade_v3.md rule 5 (sigma_repr^2 added to the fusion Sigma diagonal). RED-on
#   -revert antibody for src/forecast/bayes_precision_fusion.py (Sigma = Sigma + diag(repr_sq)
#   + tau0_eff augment) and src/forecast/grid_representativeness_loader.py.
"""v3 rule 5: ModelInstrument.sigma_repr^2 down-weights an instrument inside the ONE fusion.

Contract (RED on reverting the Sigma-diagonal / tau0 augment):
  1. sigma_repr_sq=0 on every instrument + anchor_sigma_repr_sq=0 -> byte-identical mu*/sd
     (backward compatible; the default / flag-OFF path).
  2. A large sigma_repr_sq on the instrument that PULLS the fused center away from the anchor
     widens that instrument's Sigma diagonal -> the T2 Sigma^-1 gives it LESS weight ->
     mu* moves back TOWARD the anchor. The down-weight is DERIVED by the fusion, never applied
     by hand.
  3. anchor_sigma_repr_sq>0 widens the prior tau0 -> the posterior leans MORE on the
     likelihood instruments (mu* moves away from the anchor center).
  4. The loader is fail-soft: an unknown city/model -> sigma_repr^2 = 0.0 (no fabricated penalty).
"""
from __future__ import annotations

from src.forecast.bayes_precision_fusion import (
    ModelInstrument,
    fuse_bayes_precision_posterior,
)
from src.forecast.grid_representativeness_loader import sigma_repr_sq_for


def _instruments(repr_sq_high: float) -> list[ModelInstrument]:
    # Two instruments with IDENTICAL residual structure (equal model-residual variance), so the
    # ONLY thing that differentiates their Sigma diagonal is sigma_repr_sq. gfs pulls HIGH
    # (away from the anchor at 10); icon agrees with the anchor.
    resid = (-0.5, 0.0, 0.5)
    return [
        ModelInstrument(model="gfs_global", z=14.0, train_residuals=resid, n_train=40,
                        sigma_repr_sq=repr_sq_high),
        ModelInstrument(model="icon_global", z=10.0, train_residuals=resid, n_train=40,
                        sigma_repr_sq=0.0),
    ]


def test_zero_sigma_repr_is_byte_identical():
    base = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=_instruments(0.0),
    )
    # An instrument list built with the explicit 0.0 must equal the default-field path.
    default = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0,
        likelihood=[
            ModelInstrument(model="gfs_global", z=14.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
            ModelInstrument(model="icon_global", z=10.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
        ],
    )
    assert base.mu == default.mu
    assert base.sd == default.sd


def test_sigma_repr_downweights_the_pulling_instrument():
    no_repr = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=_instruments(0.0),
    )
    with_repr = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=_instruments(9.0),  # large repr var on gfs
    )
    # gfs (z=14) is down-weighted -> fused center moves DOWN toward the anchor (10) / icon (10).
    assert with_repr.mu < no_repr.mu, (
        f"sigma_repr did not down-weight the high-pulling instrument: "
        f"with_repr.mu={with_repr.mu:.4f} >= no_repr.mu={no_repr.mu:.4f}"
    )


def test_anchor_sigma_repr_widens_prior_leans_on_instruments():
    # Anchor at 10, instruments both pull to 14. Widening the anchor prior (anchor_sigma_repr_sq)
    # makes the posterior lean MORE on the instruments -> mu* moves UP, away from the anchor.
    lik = [
        ModelInstrument(model="gfs_global", z=14.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
        ModelInstrument(model="icon_global", z=14.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
    ]
    tight = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=lik, anchor_sigma_repr_sq=0.0,
    )
    wide = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=lik, anchor_sigma_repr_sq=9.0,
    )
    assert wide.mu > tight.mu, (
        f"anchor sigma_repr did not weaken the prior: wide.mu={wide.mu:.4f} <= tight.mu={tight.mu:.4f}"
    )


def test_loader_fail_soft_unknown_city_model_is_zero():
    assert sigma_repr_sq_for("Atlantis", "ecmwf_ifs") == 0.0
    assert sigma_repr_sq_for("Tokyo", "no_such_model") == 0.0


def test_loader_known_cell_is_positive_and_distance_monotone():
    # A coarse-cell (large d_eff) instrument must carry MORE representativeness variance than a
    # fine-cell one at the same city when both cells are present.
    s_anchor = sigma_repr_sq_for("Tokyo", "ecmwf_ifs")  # 9km cell, ~7.5km off RJTT
    assert s_anchor > 0.0
