# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect Open-Meteo ECMWF IFS 9km + AIFS sampled-2t soft-anchor posterior semantics.
# Reuse: Run before wiring or changing replacement soft-anchor posterior logic.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t blocked-candidate integration.
"""Soft-anchor posterior tests."""

from __future__ import annotations

import pytest

from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import (
    PRODUCT_ID,
    SOURCE_ID,
    ProbabilityBin,
    SoftAnchorConfig,
    anchor_sigma_to_celsius,
    build_source_disagreement_sigma_widening,
    build_soft_anchor_posterior,
    selected_bin,
)


def _bins() -> tuple[ProbabilityBin, ...]:
    return (
        ProbabilityBin("cold", lower_c=10.0, upper_c=12.0),
        ProbabilityBin("middle", lower_c=13.0, upper_c=15.0),
        ProbabilityBin("warm", lower_c=16.0, upper_c=18.0),
    )


def test_soft_anchor_posterior_uses_aifs_prior_and_anchor_likelihood() -> None:
    posterior = build_soft_anchor_posterior(
        aifs_probabilities={"cold": 0.25, "middle": 0.40, "warm": 0.35},
        bins=_bins(),
        anchor_c=17.0,
        config=SoftAnchorConfig(anchor_weight=0.80, anchor_sigma_c=3.0),
    )

    assert posterior.source_id == SOURCE_ID
    assert posterior.product_id == PRODUCT_ID
    assert posterior.trade_authority_status == "BLOCKED"
    assert posterior.training_allowed is False
    assert sum(posterior.probabilities.values()) == pytest.approx(1.0)
    assert posterior.anchor_likelihood["warm"] > posterior.anchor_likelihood["middle"] > posterior.anchor_likelihood["cold"]
    assert posterior.probabilities["warm"] > 0.35
    assert posterior.probabilities["cold"] < 0.25
    assert selected_bin(posterior.probabilities) == "warm"


def test_soft_anchor_zero_weight_is_normalized_aifs_prior() -> None:
    posterior = build_soft_anchor_posterior(
        aifs_probabilities={"cold": 2.0, "middle": 5.0, "warm": 3.0},
        bins=_bins(),
        anchor_c=17.0,
        config=SoftAnchorConfig(anchor_weight=0.0, anchor_sigma_c=3.0),
    )

    assert posterior.probabilities == pytest.approx({"cold": 0.20, "middle": 0.50, "warm": 0.30})


def test_soft_anchor_sigma_unit_conversion_is_explicit_for_temperature_deltas() -> None:
    assert anchor_sigma_to_celsius(3.0, "C") == pytest.approx(3.0)
    assert anchor_sigma_to_celsius(3.0, "K") == pytest.approx(3.0)
    assert anchor_sigma_to_celsius(5.4, "F") == pytest.approx(3.0)

    config = SoftAnchorConfig.from_sigma(anchor_weight=0.80, anchor_sigma=5.4, sigma_unit="fahrenheit")
    assert config.anchor_sigma_c == pytest.approx(3.0)

    with pytest.raises(ValueError, match="unit"):
        anchor_sigma_to_celsius(3.0, "rankine")


def test_soft_anchor_floors_zero_prior_bin_to_negligible_not_meaningful_mass() -> None:
    # Structural invariant (Fault A fix): a zero-prior bin is NEVER literal-zero / un-hittable --
    # the unconditional structural floor keeps it strictly positive and normalizable. But the floor
    # adds only NEGLIGIBLE mass: the soft anchor (alone, smoothing OFF) does NOT manufacture
    # trade-relevant mass on a 0-vote bin. The MEANINGFUL economic mass arrives only through the
    # flag-gated member_vote_smoothing_alpha (covered by the smoothing suite). This asserts the
    # floor/alpha SEPARATION -- one mechanism, two regimes -- not the old bug-as-law `== 0.0`.
    posterior = build_soft_anchor_posterior(
        aifs_probabilities={"cold": 0.50, "middle": 0.50, "warm": 0.0},
        bins=_bins(),
        anchor_c=17.0,
    )

    assert posterior.anchor_likelihood["warm"] == pytest.approx(1.0)
    # Strictly positive (never structurally un-hittable) ...
    assert posterior.probabilities["warm"] > 0.0
    # ... but negligible: orders of magnitude below the flag-gated smoothing mass (~1e-3+), so the
    # floor is a normalizability guarantee, NOT a trading-behavior change (iron rule #2/#6).
    assert posterior.probabilities["warm"] < 1e-9
    # The posterior remains a proper distribution.
    assert sum(posterior.probabilities.values()) == pytest.approx(1.0)


def test_source_disagreement_widens_anchor_sigma_instead_of_tightening_confidence() -> None:
    probabilities = {"cold": 0.70, "middle": 0.20, "warm": 0.10}
    diagnostic = build_source_disagreement_sigma_widening(
        aifs_probabilities=probabilities,
        bins=_bins(),
        anchor_c=17.0,
        baseline_anchor_sigma_c=3.0,
    )

    assert diagnostic.aifs_mean_c == pytest.approx(12.2)
    assert diagnostic.disagreement_c == pytest.approx(4.8)
    assert diagnostic.widened_anchor_sigma_c > 3.0
    assert diagnostic.sigma_widened is True
    assert diagnostic.reason_codes == ("SOFT_ANCHOR_SOURCE_DISAGREEMENT_SIGMA_WIDENED",)
    assert diagnostic.promotion_allowed is False

    fixed_sigma = build_soft_anchor_posterior(
        aifs_probabilities=probabilities,
        bins=_bins(),
        anchor_c=17.0,
        config=SoftAnchorConfig(anchor_weight=0.80, anchor_sigma_c=3.0),
    )
    widened_sigma = build_soft_anchor_posterior(
        aifs_probabilities=probabilities,
        bins=_bins(),
        anchor_c=17.0,
        config=diagnostic.as_config(anchor_weight=0.80),
    )

    assert widened_sigma.anchor_sigma_c == pytest.approx(diagnostic.widened_anchor_sigma_c)
    assert widened_sigma.probabilities["warm"] < fixed_sigma.probabilities["warm"]
    assert widened_sigma.probabilities["cold"] > fixed_sigma.probabilities["cold"]


def test_source_disagreement_zero_distance_does_not_tighten_anchor_sigma() -> None:
    diagnostic = build_source_disagreement_sigma_widening(
        aifs_probabilities={"cold": 0.0, "middle": 1.0, "warm": 0.0},
        bins=_bins(),
        anchor_c=14.0,
        baseline_anchor_sigma_c=3.0,
    )

    assert diagnostic.aifs_mean_c == pytest.approx(14.0)
    assert diagnostic.widened_anchor_sigma_c == pytest.approx(3.0)
    assert diagnostic.sigma_widened is False
    assert diagnostic.reason_codes == ("SOFT_ANCHOR_SOURCE_DISAGREEMENT_NO_WIDENING",)


def test_soft_anchor_rejects_bad_inputs_and_transcript_shorthand() -> None:
    with pytest.raises(ValueError, match="same bin ids"):
        build_soft_anchor_posterior(
            aifs_probabilities={"cold": 1.0},
            bins=_bins(),
            anchor_c=17.0,
        )

    with pytest.raises(ValueError, match="open-ended"):
        build_soft_anchor_posterior(
            aifs_probabilities={"open": 1.0},
            bins=(ProbabilityBin("open", lower_c=20.0),),
            anchor_c=21.0,
        )

    for identifier in (SOURCE_ID, PRODUCT_ID):
        assert ("h" + "3") not in identifier.lower()
