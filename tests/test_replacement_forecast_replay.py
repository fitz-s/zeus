# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast same-CLOB after-cost replay evidence from unit-PnL overclaims.
# Reuse: Run before using replacement forecast replay output as promotion evidence.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast same-CLOB after-cost replay tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.replacement_forecast_replay import (
    ReplacementForecastSameClobReplayInput,
    score_replacement_forecast_same_clob_replay,
)


UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _source_times(**overrides: datetime) -> dict[str, datetime]:
    values = {
        "baseline_b0": _dt(2),
        "aifs_sampled_2t": _dt(2, 15),
        "openmeteo_ifs9_anchor": _dt(2, 30),
        "soft_anchor_posterior": _dt(3),
    }
    values.update(overrides)
    return values


def _processed_times(**overrides: datetime) -> dict[str, datetime]:
    values = {
        "baseline_b0": _dt(2, 5),
        "aifs_sampled_2t": _dt(2, 20),
        "openmeteo_ifs9_anchor": _dt(2, 40),
        "soft_anchor_posterior": _dt(3, 10),
    }
    values.update(overrides)
    return values


def _row(**overrides: object) -> ReplacementForecastSameClobReplayInput:
    values = {
        "city": "Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "condition_id": "condition-1",
        "token_id": "yes-token-1",
        "yes_token_id": "yes-token-1",
        "no_token_id": "no-token-1",
        "baseline_market_snapshot_id": "clob-snapshot-1",
        "replacement_market_snapshot_id": "clob-snapshot-1",
        "decision_time": _dt(4),
        "baseline_would_trade": True,
        "replacement_allows_trade": False,
        "direction": "buy_yes",
        "entry_price": 0.40,
        "fee_per_share": 0.02,
        "slippage_per_share": 0.01,
        "requested_notional_usd": 40.0,
        "available_depth_shares": 80.0,
        "fill_probability": 0.50,
        "min_order_usd": 1.0,
        "tick_size": 0.01,
        "exit_liquidity_available_shares": 100.0,
        "exit_fill_probability": 1.0,
        "exit_slippage_per_share": 0.0,
        "settlement_token_wins": False,
        "truth_authority": "VERIFIED",
        "source_available_at_by_role": _source_times(),
        "processed_at_by_role": _processed_times(),
        "derived_posterior_available_at": _dt(3, 15),
        "q_b0": 0.70,
        "q_replacement": 0.45,
        "q_lcb_b0": 0.63,
        "q_lcb_replacement": 0.40,
        "veto_reason": "replacement_q_lcb_below_baseline",
    }
    values.update(overrides)
    return ReplacementForecastSameClobReplayInput(**values)  # type: ignore[arg-type]


def test_replacement_replay_scores_veto_after_cost_on_same_clob_snapshot() -> None:
    result = score_replacement_forecast_same_clob_replay(_row())

    assert result.scored is True
    assert result.same_clob_snapshot is True
    assert result.market_snapshot_id == "clob-snapshot-1"
    assert result.all_in_price == pytest.approx(0.43)
    assert result.filled_shares == pytest.approx(40.0)
    assert result.exit_liquidity_available_shares == pytest.approx(100.0)
    assert result.exit_fill_probability == pytest.approx(1.0)
    assert result.baseline_after_cost_pnl == pytest.approx(-17.2)
    assert result.replacement_after_cost_pnl == pytest.approx(0.0)
    assert result.replacement_delta_after_cost_pnl == pytest.approx(17.2)
    assert result.veto_applied is True
    assert result.truth_authority == "VERIFIED"
    assert result.source_available_at_max == "2026-06-06T03:00:00+00:00"
    assert result.processed_at_max == "2026-06-06T03:10:00+00:00"
    assert result.derived_posterior_available_at == "2026-06-06T03:15:00+00:00"


def test_replacement_replay_preserves_regret_when_veto_blocks_winning_trade() -> None:
    result = score_replacement_forecast_same_clob_replay(_row(settlement_token_wins=True))

    assert result.scored is True
    assert result.baseline_after_cost_pnl == pytest.approx(22.8)
    assert result.replacement_after_cost_pnl == pytest.approx(0.0)
    assert result.replacement_delta_after_cost_pnl == pytest.approx(-22.8)


def test_replacement_replay_blocks_non_same_clob_or_unverified_truth() -> None:
    snapshot_mismatch = score_replacement_forecast_same_clob_replay(_row(replacement_market_snapshot_id="different"))
    assert snapshot_mismatch.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_NOT_SAME_CLOB_SNAPSHOT" in snapshot_mismatch.reason_codes
    assert snapshot_mismatch.market_snapshot_id is None

    provisional_truth = score_replacement_forecast_same_clob_replay(_row(truth_authority="UNVERIFIED"))
    assert provisional_truth.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_REQUIRES_OFFICIAL_VERIFIED_TRUTH" in provisional_truth.reason_codes


def test_replacement_replay_blocks_source_after_decision_and_missing_roles() -> None:
    late = score_replacement_forecast_same_clob_replay(
        _row(source_available_at_by_role=_source_times(soft_anchor_posterior=_dt(5)))
    )
    assert late.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_SOURCE_AFTER_DECISION_TIME" in late.reason_codes

    missing = _source_times()
    missing.pop("aifs_sampled_2t")
    missing_role = score_replacement_forecast_same_clob_replay(_row(source_available_at_by_role=missing))
    assert missing_role.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_SOURCE_AVAILABILITY_MISSING" in missing_role.reason_codes


def test_replacement_replay_blocks_processing_or_derived_posterior_after_decision() -> None:
    late_processing = score_replacement_forecast_same_clob_replay(
        _row(processed_at_by_role=_processed_times(openmeteo_ifs9_anchor=_dt(5)))
    )
    assert late_processing.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_PROCESSED_AFTER_DECISION_TIME" in late_processing.reason_codes

    late_posterior = score_replacement_forecast_same_clob_replay(_row(derived_posterior_available_at=_dt(5)))
    assert late_posterior.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_DERIVED_POSTERIOR_AFTER_DECISION_TIME" in late_posterior.reason_codes


def test_replacement_replay_blocks_derived_posterior_before_dependencies_ready() -> None:
    before_source = score_replacement_forecast_same_clob_replay(
        _row(derived_posterior_available_at=_dt(2, 45))
    )
    assert before_source.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_DERIVED_POSTERIOR_BEFORE_SOURCE_READY" in before_source.reason_codes

    before_processing = score_replacement_forecast_same_clob_replay(
        _row(source_available_at_by_role=_source_times(soft_anchor_posterior=_dt(2)), derived_posterior_available_at=_dt(3, 5))
    )
    assert before_processing.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_DERIVED_POSTERIOR_BEFORE_PROCESSING_READY" in before_processing.reason_codes


def test_replacement_replay_blocks_unit_pnl_when_fees_depth_or_fill_are_absent() -> None:
    no_depth = score_replacement_forecast_same_clob_replay(_row(available_depth_shares=0.0))
    assert no_depth.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_DEPTH_REQUIRED" in no_depth.reason_codes

    no_fill = score_replacement_forecast_same_clob_replay(_row(fill_probability=0.0))
    assert no_fill.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_FILL_PROBABILITY_REQUIRED" in no_fill.reason_codes

    no_exit_liquidity = score_replacement_forecast_same_clob_replay(_row(exit_liquidity_available_shares=0.0))
    assert no_exit_liquidity.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_EXIT_LIQUIDITY_REQUIRED" in no_exit_liquidity.reason_codes

    no_exit_fill = score_replacement_forecast_same_clob_replay(_row(exit_fill_probability=0.0))
    assert no_exit_fill.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_EXIT_FILL_PROBABILITY_REQUIRED" in no_exit_fill.reason_codes

    below_min = score_replacement_forecast_same_clob_replay(_row(requested_notional_usd=0.50, min_order_usd=1.0))
    assert below_min.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_MIN_ORDER_NOT_MET" in below_min.reason_codes


def test_replacement_replay_applies_exit_slippage_and_liquidity_to_after_cost_pnl() -> None:
    result = score_replacement_forecast_same_clob_replay(
        _row(
            settlement_token_wins=True,
            exit_liquidity_available_shares=10.0,
            exit_fill_probability=0.50,
            exit_slippage_per_share=0.02,
        )
    )

    assert result.scored is True
    assert result.all_in_price == pytest.approx(0.45)
    assert result.filled_shares == pytest.approx(5.0)
    assert result.baseline_after_cost_pnl == pytest.approx(2.75)
    assert result.replacement_delta_after_cost_pnl == pytest.approx(-2.75)


def test_replacement_replay_requires_native_token_for_direction_and_tick_price() -> None:
    with pytest.raises(ValueError, match="native token"):
        _row(direction="buy_no", token_id="yes-token-1")

    no_side = _row(direction="buy_no", token_id="no-token-1")
    assert no_side.token_id == "no-token-1"

    off_tick = score_replacement_forecast_same_clob_replay(_row(entry_price=0.405, tick_size=0.01))
    assert off_tick.status == "BLOCKED"
    assert "REPLACEMENT_REPLAY_ENTRY_PRICE_NOT_ON_TICK" in off_tick.reason_codes
