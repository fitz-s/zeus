# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect Open-Meteo ECMWF IFS 9km + AIFS sampled-2t soft-anchor posterior and shadow veto semantics.
# Reuse: Run before wiring or changing replacement soft-anchor posterior logic.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow integration.
"""Soft-anchor posterior and guardrail tests."""

from __future__ import annotations

import pytest

from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import (
    PRODUCT_ID,
    SOURCE_ID,
    ProbabilityBin,
    SoftAnchorConfig,
    anchor_sigma_to_celsius,
    apply_shadow_veto_guardrail,
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
    assert posterior.trade_authority_status == "SHADOW_ONLY"
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


def test_soft_anchor_dirichlet_floor_can_rescue_zero_prior_bin() -> None:
    posterior = build_soft_anchor_posterior(
        aifs_probabilities={"cold": 0.50, "middle": 0.50, "warm": 0.0},
        bins=_bins(),
        anchor_c=17.0,
    )

    assert posterior.anchor_likelihood["warm"] > posterior.anchor_likelihood["middle"]
    assert posterior.probabilities["warm"] > 0.0
    assert posterior.probabilities["warm"] < posterior.probabilities["middle"]


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


def test_shadow_guardrail_never_raises_q_lcb_kelly_or_flips_direction() -> None:
    guardrail = apply_shadow_veto_guardrail(
        baseline_direction="buy_yes:cold",
        candidate_direction="buy_yes:warm",
        baseline_q_lcb=0.62,
        candidate_q_lcb=0.70,
        baseline_kelly_fraction=0.04,
        candidate_kelly_fraction=0.10,
    )

    assert guardrail.allowed_direction == "buy_yes:cold"
    assert guardrail.allowed_q_lcb == pytest.approx(0.62)
    assert guardrail.allowed_kelly_fraction == pytest.approx(0.04)
    assert guardrail.veto is True
    assert guardrail.reasons == ("SOFT_ANCHOR_DIRECTION_DISAGREEMENT",)


def test_shadow_guardrail_reduces_confidence_when_candidate_is_weaker() -> None:
    guardrail = apply_shadow_veto_guardrail(
        baseline_direction="buy_yes:middle",
        candidate_direction="buy_yes:middle",
        baseline_q_lcb=0.66,
        candidate_q_lcb=0.55,
        baseline_kelly_fraction=0.06,
        candidate_kelly_fraction=0.02,
    )

    assert guardrail.allowed_direction == "buy_yes:middle"
    assert guardrail.allowed_q_lcb == pytest.approx(0.55)
    assert guardrail.allowed_kelly_fraction == pytest.approx(0.02)
    assert guardrail.veto is True
    assert guardrail.reasons == ("SOFT_ANCHOR_LOWER_Q_LCB", "SOFT_ANCHOR_LOWER_KELLY")


def test_soft_anchor_rejects_bad_inputs_and_transcript_shorthand() -> None:
    with pytest.raises(ValueError, match="same bin ids"):
        build_soft_anchor_posterior(
            aifs_probabilities={"cold": 1.0},
            bins=_bins(),
            anchor_c=17.0,
        )

    open_posterior = build_soft_anchor_posterior(
        aifs_probabilities={"open": 1.0},
        bins=(ProbabilityBin("open", lower_c=20.0),),
        anchor_c=21.0,
    )
    assert open_posterior.anchor_likelihood["open"] > 0.0
    assert open_posterior.probabilities["open"] == pytest.approx(1.0)

    for identifier in (SOURCE_ID, PRODUCT_ID):
        assert ("h" + "3") not in identifier.lower()
