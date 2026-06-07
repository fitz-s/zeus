# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07
# Purpose: Protect downloaded replacement replay go-live payload evidence fields.
# Reuse: Run before changing replacement economic replay payload generation.
# Authority basis: Replacement live-authority switch must consume evidence derived from replay rows, not hard-coded placeholders.
"""Downloaded replacement replay payload evidence tests."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3

from scripts.replay_downloaded_replacement_economic import (
    FIXED_CONFIG_LABEL,
    FULL_STRATEGY_LABEL,
    ReplacementReplayRow,
    _empirical_q_lcb_coverage_for_rows,
    _nested_walk_forward_passed_for_rows,
    _posterior_q_lcb_for_selected_bin,
    _q_lcb_source_counts,
    run_current_holdings_order_time_counterfactual,
)


def _row(*, target: date, selected: int, winning: int, label: str = FIXED_CONFIG_LABEL) -> ReplacementReplayRow:
    return ReplacementReplayRow(
        product_label=label,
        city="Shanghai",
        target_date=target,
        metric="high",
        decision_time=datetime(2026, 6, 1, 8, tzinfo=timezone.utc),
        source_available_at=datetime(2026, 6, 1, 6, tzinfo=timezone.utc),
        source_availability_observed=True,
        source_availability_mode="observed",
        bin_labels=("cool", "warm"),
        probabilities=(0.30, 0.70),
        selected_bin_index=selected,
        winning_bin_index=winning,
        selected_q_lcb=0.70 if selected == 1 else 0.30,
        q_lcb_source="test_q_lcb_source",
        market_price=0.50,
        stake_usd=10.0,
        fees_usd=0.0,
        slippage_usd=0.0,
        fill_probability=1.0,
    )


def test_empirical_q_lcb_coverage_proxy_derives_from_settled_replay_rows() -> None:
    rows = (
        _row(target=date(2026, 6, 1), selected=1, winning=1),
        _row(target=date(2026, 6, 2), selected=1, winning=0),
        _row(target=date(2026, 6, 3), selected=0, winning=0),
        _row(target=date(2026, 6, 4), selected=0, winning=1),
    )

    coverage, covered, official = _empirical_q_lcb_coverage_for_rows(rows, product_label=FIXED_CONFIG_LABEL)

    assert official == 4
    assert covered == 2
    assert coverage == 0.5
    assert _q_lcb_source_counts(rows, product_label=FIXED_CONFIG_LABEL) == {"test_q_lcb_source": 4}


def test_nested_walk_forward_proxy_requires_minimum_days_rows_and_fixed_parameter() -> None:
    rows = tuple(
        _row(target=date(2026, 6, 1 + day), selected=1, winning=1)
        for day in range(5)
        for _ in range(50)
    )

    assert _nested_walk_forward_passed_for_rows(rows, product_label=FIXED_CONFIG_LABEL) is True
    assert _nested_walk_forward_passed_for_rows(rows[:-1], product_label=FIXED_CONFIG_LABEL) is False
    assert (
        _nested_walk_forward_passed_for_rows(
            rows,
            product_label=FIXED_CONFIG_LABEL,
            selected_anchor_weight=0.60,
        )
        is False
    )


def test_posterior_q_lcb_lookup_matches_city_date_metric_and_selected_bin() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            source_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            q_lcb_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, city, target_date, temperature_metric,
            source_available_at, computed_at, q_lcb_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            FULL_STRATEGY_LABEL,
            "Chicago",
            "2026-06-05",
            "high",
            "2026-06-05T06:00:00+00:00",
            "2026-06-05T06:30:00+00:00",
            json.dumps({"warm": 0.42}),
        ),
    )

    q_lcb, source = _posterior_q_lcb_for_selected_bin(
        conn,
        city="Chicago",
        target_date=date(2026, 6, 5),
        metric="high",
        selected_bin_label="warm",
        decision_time=datetime(2026, 6, 5, 8, tzinfo=timezone.utc),
    )

    assert q_lcb == 0.42
    assert source == "forecast_posteriors_q_lcb_json:posterior:1"


def test_current_holding_counterfactual_blocks_future_replacement_posterior() -> None:
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.row_factory = sqlite3.Row
    forecast_conn = sqlite3.connect(":memory:")
    forecast_conn.row_factory = sqlite3.Row
    trade_conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            size_usd REAL,
            entry_price REAL,
            strategy_key TEXT,
            updated_at TEXT
        )
        """
    )
    trade_conn.execute(
        """
        CREATE TABLE venue_commands (
            position_id TEXT,
            snapshot_id TEXT,
            created_at TEXT
        )
        """
    )
    forecast_conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            source_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            q_json TEXT
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, bin_label, direction,
            size_usd, entry_price, strategy_key, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "pos-1",
            "active",
            "Istanbul",
            "2026-06-07",
            "Will the highest temperature in Istanbul be 23C on June 7?",
            "buy_no",
            10.0,
            0.75,
            "opening_inertia",
            "2026-06-06T19:00:00+00:00",
        ),
    )
    trade_conn.execute(
        "INSERT INTO venue_commands (position_id, snapshot_id, created_at) VALUES (?, ?, ?)",
        ("pos-1", "ems2-selected", "2026-06-06T19:12:00+00:00"),
    )
    forecast_conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, city, target_date, temperature_metric,
            source_available_at, computed_at, q_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            FULL_STRATEGY_LABEL,
            "Istanbul",
            "2026-06-07",
            "high",
            "2026-06-07T02:00:00+00:00",
            "2026-06-07T07:28:00+00:00",
            json.dumps(
                {
                    "Will the highest temperature in Istanbul be 23C on June 7?": 0.02,
                    "Will the highest temperature in Istanbul be 25C on June 7?": 0.42,
                }
            ),
        ),
    )

    result = run_current_holdings_order_time_counterfactual(
        forecast_conn=forecast_conn,
        trade_conn=trade_conn,
    )

    assert result.summary["positions_total"] == 1
    assert result.summary["full_choice_replayable"] == 0
    assert result.summary["selected_market_only_replayable"] == 0
    assert result.summary["blocked_by_replacement_data_timing"] == 1
    assert result.rows[0].status == "blocked_by_replacement_data_timing"
    assert result.rows[0].replacement_q_for_current_bin_unusable == 0.02
    assert result.rows[0].replacement_top_bin_unusable == "Will the highest temperature in Istanbul be 25C on June 7?"


def test_current_holding_counterfactual_does_not_call_selected_market_full_choice() -> None:
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.row_factory = sqlite3.Row
    forecast_conn = sqlite3.connect(":memory:")
    forecast_conn.row_factory = sqlite3.Row
    trade_conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            size_usd REAL,
            entry_price REAL,
            strategy_key TEXT,
            updated_at TEXT
        );
        CREATE TABLE venue_commands (
            position_id TEXT,
            snapshot_id TEXT,
            created_at TEXT
        );
        """
    )
    forecast_conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            source_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            q_json TEXT
        )
        """
    )
    trade_conn.execute(
        "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "pos-2",
            "active",
            "Tokyo",
            "2026-06-08",
            "Will the lowest temperature in Tokyo be 16C on June 8?",
            "buy_no",
            8.0,
            0.97,
            "opening_inertia",
            "2026-06-07T09:00:00+00:00",
        ),
    )
    trade_conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, ?)",
        ("pos-2", "ems2-selected", "2026-06-07T09:00:00+00:00"),
    )
    forecast_conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            5,
            FULL_STRATEGY_LABEL,
            "Tokyo",
            "2026-06-08",
            "low",
            "2026-06-07T02:00:00+00:00",
            "2026-06-07T07:28:00+00:00",
            json.dumps({"Will the lowest temperature in Tokyo be 16C on June 8?": 0.0}),
        ),
    )

    result = run_current_holdings_order_time_counterfactual(
        forecast_conn=forecast_conn,
        trade_conn=trade_conn,
    )

    assert result.summary["selected_market_only_replayable"] == 1
    assert result.summary["full_choice_replayable"] == 0
    assert result.rows[0].eligible_posterior_id == 5
    assert result.rows[0].reason_codes == ("CURRENT_HOLDING_COUNTERFACTUAL_CANDIDATE_FAMILY_NOT_RECONSTRUCTED",)
