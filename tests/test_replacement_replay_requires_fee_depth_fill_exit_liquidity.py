from tests.test_replacement_replay_veto_only_not_initiation import _row

from src.data.replacement_forecast_replay import score_replacement_forecast_same_clob_replay


def test_replay_requires_fee_depth_fill_and_exit_liquidity() -> None:
    result = score_replacement_forecast_same_clob_replay(
        _row(entry_price=0.99, fee_per_share=0.02, available_depth_shares=0.0, fill_probability=0.0, exit_liquidity_available_shares=0.0)
    )

    assert result.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_ALL_IN_PRICE_OUT_OF_RANGE" in result.reason_codes
    assert "REPLACEMENT_REPLAY_DEPTH_REQUIRED" in result.reason_codes
    assert "REPLACEMENT_REPLAY_FILL_PROBABILITY_REQUIRED" in result.reason_codes
    assert "REPLACEMENT_REPLAY_EXIT_LIQUIDITY_REQUIRED" in result.reason_codes
