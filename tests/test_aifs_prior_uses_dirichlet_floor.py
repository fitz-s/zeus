from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import ProbabilityBin, build_soft_anchor_posterior


def test_soft_anchor_prior_floor_prevents_zero_member_bin_from_becoming_impossible() -> None:
    posterior = build_soft_anchor_posterior(
        aifs_probabilities={"seen": 1.0, "zero_member_but_anchor_supported": 0.0},
        bins=(
            ProbabilityBin("seen", lower_c=20.0, upper_c=21.0),
            ProbabilityBin("zero_member_but_anchor_supported", lower_c=0.0, upper_c=1.0),
        ),
        anchor_c=0.5,
    )

    assert posterior.probabilities["zero_member_but_anchor_supported"] > 0.0
