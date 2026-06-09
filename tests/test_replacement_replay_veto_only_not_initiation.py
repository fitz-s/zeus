from dataclasses import replace
from datetime import datetime, timezone

from src.data.replacement_forecast_replay import ReplacementForecastSameClobReplayInput, score_replacement_forecast_same_clob_replay


UTC = timezone.utc


def _row(**overrides):
    base = ReplacementForecastSameClobReplayInput(
        city="Tokyo",
        target_date="2026-06-08",
        temperature_metric="low",
        condition_id="cond",
        token_id="no-token",
        yes_token_id="yes-token",
        no_token_id="no-token",
        baseline_market_snapshot_id="snap",
        replacement_market_snapshot_id="snap",
        decision_time=datetime(2026, 6, 7, 4, tzinfo=UTC),
        baseline_would_trade=True,
        replacement_allows_trade=False,
        direction="buy_no",
        entry_price=0.50,
        fee_per_share=0.01,
        slippage_per_share=0.01,
        requested_notional_usd=10.0,
        available_depth_shares=100.0,
        fill_probability=1.0,
        min_order_usd=1.0,
        tick_size=0.01,
        exit_liquidity_available_shares=100.0,
        exit_fill_probability=1.0,
        exit_slippage_per_share=0.01,
        settlement_token_wins=True,
        truth_authority="VERIFIED",
        source_available_at_by_role={r: datetime(2026, 6, 7, 2, tzinfo=UTC) for r in ("baseline_b0", "aifs_sampled_2t", "openmeteo_ifs9_anchor", "soft_anchor_posterior")},
        processed_at_by_role={r: datetime(2026, 6, 7, 3, tzinfo=UTC) for r in ("baseline_b0", "aifs_sampled_2t", "openmeteo_ifs9_anchor", "soft_anchor_posterior")},
        derived_posterior_available_at=datetime(2026, 6, 7, 3, tzinfo=UTC),
        q_b0=0.6,
        q_replacement=0.4,
        q_lcb_b0=0.55,
        q_lcb_replacement=0.35,
        veto_reason="test",
    )
    return replace(base, **overrides)


def test_replay_scores_veto_only_and_does_not_create_initiation_pnl() -> None:
    result = score_replacement_forecast_same_clob_replay(_row(baseline_would_trade=False, replacement_allows_trade=True))

    assert result.scored is True
    assert result.baseline_after_cost_pnl == 0.0
    assert result.replacement_after_cost_pnl == 0.0
