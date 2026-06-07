from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import ProbabilityBin, SoftAnchorConfig, build_soft_anchor_posterior


def test_soft_anchor_open_ended_bins_use_tail_integral_not_center_point() -> None:
    posterior = build_soft_anchor_posterior(
        aifs_probabilities={"low_tail": 0.5, "high_tail": 0.5},
        bins=(ProbabilityBin("low_tail", upper_c=0.0, center_c=-100.0), ProbabilityBin("high_tail", lower_c=1.0, center_c=100.0)),
        anchor_c=0.0,
        config=SoftAnchorConfig(anchor_weight=1.0, anchor_sigma_c=1.0),
    )

    assert posterior.anchor_likelihood["low_tail"] > posterior.anchor_likelihood["high_tail"]
    assert posterior.anchor_likelihood["low_tail"] > 0.4
