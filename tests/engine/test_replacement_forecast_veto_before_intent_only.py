from src.engine.replacement_forecast_veto import ReplacementForecastVetoDecision


def test_veto_artifact_is_shadow_veto_only_before_intent() -> None:
    decision = ReplacementForecastVetoDecision(
        posterior_id=1,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        baseline_direction="buy_yes:bin",
        candidate_direction="buy_yes:bin",
        allowed_direction="buy_yes:bin",
        baseline_q_posterior=0.7,
        candidate_q_posterior=0.8,
        allowed_q_posterior=0.7,
        baseline_q_lcb=0.6,
        candidate_q_lcb=0.5,
        allowed_q_lcb=0.5,
        baseline_kelly_fraction=0.01,
        candidate_kelly_fraction=0.02,
        allowed_kelly_fraction=0.01,
        veto=True,
        reasons=("SOFT_ANCHOR_LOWER_Q_LCB",),
        market_snapshot_id="snap",
        condition_id="cond",
        token_id="tok",
        decision_time="2026-06-07T00:00:00+00:00",
        dependency_source_run_ids={},
        provenance={},
    )

    assert decision.trade_authority_status == "SHADOW_VETO_ONLY"
    assert decision.allowed_direction == decision.baseline_direction
