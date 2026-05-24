# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: operator grid-transport adjudication 2026-05-24
#   (0.25 OpenData live signal kept; 0.5 TIGGE prior transported, not naked-applied;
#    b25 = b50 + E[Δ], σ25² = σ50² + Var(Δ) + κ·σ50·σ_Δ; Δ = F25 - F50; universal).
"""TDD for the universal grid/product transport of a TIGGE (0.5/F50) prior to the
live OpenData (0.25/F25) lineage.

The 0.5 TIGGE calibration cannot be applied losslessly to the 0.25 OpenData product.
The exact bridge transports the prior mean by E[Δ] (Δ = F25 - F50 from paired live
snapshots — no settlement needed) and inflates the prior variance for transport
uncertainty. The result is a #334 ``BiasPrior`` in the F25 lineage, fed to the same
posterior pipeline. No cell swapping, no target relabel — Δ is a deterministic
paired-lineage feature.
"""

from __future__ import annotations

import math

import pytest

from src.calibration.ens_bias_model import BiasPrior, transport_bias_prior


def test_transport_shifts_prior_mean_by_expected_delta():
    # b50 = -2.0 cold; Δ (F25 - F50) averages +1.3 (0.25 cell warmer/inland) -> b25 = -0.7
    delta = [1.2, 1.3, 1.4, 1.3, 1.3]
    prior = transport_bias_prior(b50=-2.0, sd50=0.5, delta_samples=delta)
    assert isinstance(prior, BiasPrior)
    assert prior.mu_t == pytest.approx(-2.0 + 1.3, abs=0.05)


def test_transport_inflates_variance():
    delta = [0.5, 1.5, 1.0, 2.0, 0.0, 1.0]  # spread > 0
    prior = transport_bias_prior(b50=-2.0, sd50=0.5, delta_samples=delta)
    assert prior.v0 > 0.5 ** 2, "transport uncertainty must inflate the prior variance"


def test_transport_kappa_controls_covariance_allowance():
    delta = [0.5, 1.5, 1.0, 2.0, 0.0, 1.0]
    indep = transport_bias_prior(b50=-2.0, sd50=0.5, delta_samples=delta, kappa=0.0)
    worst = transport_bias_prior(b50=-2.0, sd50=0.5, delta_samples=delta, kappa=2.0)
    assert worst.v0 > indep.v0, "higher kappa (more assumed correlation) widens the prior"
    # kappa=0 is the quadrature sum sd50^2 + var(Δ)
    import statistics
    var_d = statistics.variance(delta)
    assert indep.v0 == pytest.approx(0.5 ** 2 + var_d, rel=1e-6)


def test_transport_empty_delta_falls_back_to_prior_unchanged():
    # No paired Δ -> cannot transport -> keep the 0.5 prior as-is (safe fallback).
    prior = transport_bias_prior(b50=-2.0, sd50=0.5, delta_samples=[])
    assert prior.mu_t == pytest.approx(-2.0)
    assert prior.v0 == pytest.approx(0.5 ** 2)


def test_transport_robust_to_outlier_delta():
    delta = [1.0] * 20 + [12.0]  # one gross paired outlier
    prior = transport_bias_prior(b50=-2.0, sd50=0.5, delta_samples=delta)
    # robust mean keeps the shift near +1.0, not dragged toward the outlier
    assert prior.mu_t == pytest.approx(-1.0, abs=0.3)
