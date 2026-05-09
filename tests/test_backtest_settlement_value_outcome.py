"""Replay settlement-value outcome antibodies."""
# Lifecycle: created=2026-04-25; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Lock WU settlement-value replay scoring against winning-bin and market-event fixture drift.
# Reuse: Pair with tests/test_run_replay_cli.py when changing replay settlement queries.
# Authority basis: P3 usage-path residual guards packet; replay settlement-value outcome antibodies.
from __future__ import annotations
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.contracts.execution_price import polymarket_fee
from src.engine import replay as replay_module
from src.engine.replay import derive_outcome_from_settlement_value
from src.state.db import get_connection, init_backtest_schema, init_schema
from src.types import Bin


def _mark_settlements_verified(conn) -> None:
    conn.execute("UPDATE settlements SET authority = 'VERIFIED'")


def _seed_market_event(conn, *, city: str, target_date: str, range_label: str) -> None:
    conn.execute(
        """
        INSERT INTO market_events
        (market_slug, city, target_date, condition_id, token_id, range_label)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            f"{city.lower()}-{target_date}-{range_label}",
            city,
            target_date,
            f"condition-{city.lower()}-{target_date}-{range_label}",
            f"token-{city.lower()}-{target_date}-{range_label}",
            range_label,
        ),
    )


def _run_wu_sweep(tmp_path, monkeypatch, *, winning_bin: str | None, value: float):
    trade_db = tmp_path / "trade.db"
    world_db = tmp_path / "world.db"
    backtest_db = tmp_path / "backtest.db"

    world = get_connection(world_db)
    init_schema(world)
    world.execute(
        """
        INSERT INTO settlements
        (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('NYC', '2026-04-03', ?, ?, 'high')
        """,
        (winning_bin, value),
    )
    if winning_bin is not None:
        _mark_settlements_verified(world)
    _seed_market_event(
        world,
        city="NYC",
        target_date="2026-04-03",
        range_label=winning_bin or "39-40°F",
    )
    world.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES
        ('NYC', '2026-04-03', '39-40°F', 0.5, 1, 1.0, 'MAM', 'US-Northeast', '2026-04-02T08:00:00Z', ?),
        ('NYC', '2026-04-03', '41-42°F', 0.5, 0, 1.0, 'MAM', 'US-Northeast', '2026-04-02T08:00:00Z', ?)
        """,
        (value, value),
    )
    world.commit()
    world.close()

    trade = get_connection(trade_db)
    init_schema(trade)
    trade.close()

    def _trade_with_world():
        conn = get_connection(trade_db)
        conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
        return conn

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", _trade_with_world)
    monkeypatch.setattr(replay_module, "get_backtest_connection", lambda: get_connection(backtest_db))

    summary = replay_module.run_replay(
        "2026-04-03",
        "2026-04-03",
        mode="wu_settlement_sweep",
    )
    conn = get_connection(backtest_db)
    row = conn.execute("SELECT * FROM backtest_outcome_comparison").fetchone()
    conn.close()
    return summary, row


def _patch_connections(monkeypatch, trade_db, world_db, backtest_db) -> None:
    def _trade_with_world():
        conn = get_connection(trade_db)
        conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
        return conn

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", _trade_with_world)
    monkeypatch.setattr(replay_module, "get_backtest_connection", lambda: get_connection(backtest_db))


def test_wu_sweep_rejects_null_winning_bin_without_verified_authority(tmp_path, monkeypatch):
    summary, row = _run_wu_sweep(tmp_path, monkeypatch, winning_bin=None, value=40.0)

    assert summary.n_replayed == 0
    assert row is None


def test_wu_outcome_ignores_wrong_winning_bin(tmp_path, monkeypatch):
    _, row = _run_wu_sweep(tmp_path, monkeypatch, winning_bin="45-46°F", value=40.0)

    assert row["derived_wu_outcome"] == 1
    assert "45-46" in row["evidence_json"]


def test_wu_sweep_reports_forecast_skill_not_pnl(tmp_path, monkeypatch):
    summary, row = _run_wu_sweep(tmp_path, monkeypatch, winning_bin="45-46°F", value=40.0)

    skill = summary.limitations["forecast_skill"]
    assert summary.limitations["pnl_available"] is False
    assert summary.limitations["lane_goal"] == "forecast_skill_not_pnl"
    assert skill["forecast_skill_rows"] == 2
    assert skill["actual_yes_rows"] == 1
    assert skill["actual_no_rows"] == 1
    assert skill["threshold_hits"] == 1
    assert skill["threshold_total"] == 2
    assert skill["brier"] == 0.25
    assert skill["log_loss"] == 0.693147
    assert skill["accuracy_at_0_5"] == 0.5
    assert skill["positive_predictions"] == 2
    assert skill["positive_prediction_hits"] == 1
    assert skill["top_bin_hits"] == 1
    assert skill["top_bin_total"] == 1
    assert skill["top_bin_accuracy"] == 1.0
    assert skill["top3_bin_hits"] == 1
    assert skill["top3_bin_total"] == 1
    assert skill["top3_bin_accuracy"] == 1.0
    assert skill["primary_multiclass_metrics_interpretable"] is True
    assert skill["probability_group_integrity"]["valid_probability_groups"] == 1
    assert skill["probability_group_integrity"]["invalid_probability_groups"] == 0
    assert skill["valid_group_forecast_skill"]["forecast_skill_rows"] == 2
    assert skill["valid_group_forecast_skill"]["brier"] == 0.25
    assert skill["calibration_buckets"] == [
        {
            "bucket": "0.5-0.6",
            "n": 2,
            "mean_p": 0.5,
            "actual_rate": 0.5,
            "brier": 0.25,
        }
    ]

    evidence = json.loads(row["evidence_json"])
    assert evidence["p_raw"] == 0.5
    assert evidence["stored_outcome_matches_settlement_value"] is True


def test_wu_sweep_flags_invalid_probability_groups(tmp_path, monkeypatch):
    trade_db = tmp_path / "trade.db"
    world_db = tmp_path / "world.db"
    backtest_db = tmp_path / "backtest.db"

    world = get_connection(world_db)
    init_schema(world)
    world.execute(
        """
        INSERT INTO settlements
        (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('NYC', '2026-04-03', '39-40°F', 40.0, 'high')
        """
    )
    _mark_settlements_verified(world)
    _seed_market_event(
        world,
        city="NYC",
        target_date="2026-04-03",
        range_label="39-40°F",
    )
    world.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES
        ('NYC', '2026-04-03', '39-40°F', 0.6, 1, 1.0, 'MAM', 'US-Northeast', '2026-04-02T08:00:00Z', 40.0),
        ('NYC', '2026-04-03', '39-40°F', 0.4, 1, 1.0, 'MAM', 'US-Northeast', '2026-04-02T08:00:00Z', 40.0)
        """
    )
    world.commit()
    world.close()

    trade = get_connection(trade_db)
    init_schema(trade)
    trade.close()

    def _trade_with_world():
        conn = get_connection(trade_db)
        conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
        return conn

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", _trade_with_world)
    monkeypatch.setattr(replay_module, "get_backtest_connection", lambda: get_connection(backtest_db))

    summary = replay_module.run_replay(
        "2026-04-03",
        "2026-04-03",
        mode="wu_settlement_sweep",
    )

    skill = summary.limitations["forecast_skill"]
    integrity = skill["probability_group_integrity"]
    assert skill["primary_multiclass_metrics_interpretable"] is False
    assert integrity["valid_probability_groups"] == 0
    assert integrity["invalid_probability_groups"] == 1
    assert integrity["invalid_probability_group_reasons"] == {
        "duplicate_labels": 1,
        "yes_count_not_one": 1,
    }
    assert skill["top_bin_total"] == 0
    assert skill["top_bin_accuracy"] is None
    assert skill["valid_group_forecast_skill"]["forecast_skill_rows"] == 0


def test_replay_pnl_uses_fee_adjusted_execution_cost_for_share_count(tmp_path, monkeypatch):
    db_path = tmp_path / "replay-fee-adjusted-pnl.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('NYC', '2026-04-03', '39-40°F', 40.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (81, 'NYC', '2026-04-03', '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T08:00:00Z', '2026-04-02T08:05:00Z', 24.0,
                '[39.0, 42.0]', '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('NYC', '2026-04-03', '39-40°F', 0.9, 1, 1.0, 'MAM', 'US-Northeast',
         '2026-04-02T08:00:00Z', 40.0),
        ('NYC', '2026-04-03', '41-42°F', 0.1, 0, 1.0, 'MAM', 'US-Northeast',
         '2026-04-02T08:00:00Z', 40.0)
        """
    )
    _seed_market_event(conn, city="NYC", target_date="2026-04-03", range_label="39-40°F")
    _seed_market_event(conn, city="NYC", target_date="2026-04-03", range_label="41-42°F")
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "opening_hunt",
            "2026-04-02T08:10:00+00:00",
            "2026-04-02T08:11:00+00:00",
            """{
              "trade_cases": [{
                "trade_id": "replay-fee-cost",
                "city": "NYC",
                "target_date": "2026-04-03",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "decision_snapshot_id": "81",
                "bin_labels": ["39-40°F", "41-42°F"],
                "p_raw_vector": [0.9, 0.1],
                "p_cal_vector": [0.9, 0.1],
                "p_market_vector": [0.5, 0.5],
                "alpha": 0.0,
                "market_hours_open": 1.0,
                "agreement": "AGREE",
                "timestamp": "2026-04-02T08:10:00+00:00"
              }]
            }""",
            "2026-04-02T08:11:00+00:00",
            "live",
        ),
    )
    conn.commit()
    conn.close()

    import src.state.db as db_module
    import src.strategy.fdr_filter as fdr_module
    import src.strategy.kelly as kelly_module
    import src.strategy.market_analysis as market_analysis_module

    class FakeMarketAnalysis:
        def __init__(self, *args, **kwargs):
            self.bins = kwargs["bins"]

        def find_edges(self, n_bootstrap):
            return [
                SimpleNamespace(
                    bin=self.bins[0],
                    direction="buy_yes",
                    edge=0.40,
                    p_posterior=0.90,
                    entry_price=0.50,
                    ci_lower=0.20,
                    ci_upper=0.60,
                )
            ]

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_analysis_module, "MarketAnalysis", FakeMarketAnalysis)
    monkeypatch.setattr(fdr_module, "fdr_filter", lambda edges: edges)
    monkeypatch.setattr(kelly_module, "dynamic_kelly_mult", lambda **kwargs: 1.0)

    summary = replay_module.run_replay(
        "2026-04-03",
        "2026-04-03",
        mode="audit",
        sizing_bankroll=100.0,
    )

    decision = summary.outcomes[0].replay_decisions[0]
    fee_adjusted_price = 0.50 + polymarket_fee(0.50, fee_rate=0.05)
    expected_size = (0.90 - fee_adjusted_price) / (1 - fee_adjusted_price) * 100.0
    expected_pnl = expected_size / fee_adjusted_price - expected_size
    bare_price_pnl = expected_size / 0.50 - expected_size

    assert decision.should_trade is True
    assert decision.size_usd == pytest.approx(expected_size, abs=0.0001)
    assert summary.replay_total_pnl == pytest.approx(round(expected_pnl, 2))
    assert summary.replay_total_pnl != pytest.approx(round(bare_price_pnl, 2))
    assert "replay_execution_cost_fee_adjusted" in decision.applied_validations


def test_trade_history_audit_uses_position_metric_for_settlement_match(tmp_path, monkeypatch):
    trade_db = tmp_path / "trade.db"
    world_db = tmp_path / "world.db"
    backtest_db = tmp_path / "backtest.db"

    world = get_connection(world_db)
    init_schema(world)
    world.execute(
        """
        INSERT INTO settlements
        (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('NYC', '2026-04-03', '100°F+', 100.0, 'high')
        """
    )
    world.execute(
        """
        INSERT INTO settlements
        (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('NYC', '2026-04-03', '39-40°F', 40.0, 'low')
        """
    )
    _mark_settlements_verified(world)
    world.commit()
    world.close()

    trade = get_connection(trade_db)
    init_schema(trade)
    trade.execute(
        """
        INSERT INTO position_current
        (position_id, phase, trade_id, market_id, city, cluster, target_date,
         bin_label, direction, unit, size_usd, shares, cost_basis_usd,
         entry_price, p_posterior, last_monitor_prob, last_monitor_edge,
         last_monitor_market_price, decision_snapshot_id, entry_method,
         strategy_key, edge_source, discovery_mode, chain_state, order_id,
         order_status, updated_at, temperature_metric)
        VALUES ('pos-low', 'settled', 'pos-low', 'mkt', 'NYC',
                'US-Northeast', '2026-04-03', '39-40°F', 'buy_yes', 'F',
                5.0, 10.0, 5.0, 0.5, 0.6, 0.6, 0.1, 0.5, 'snap-low',
                'entry', 'center_buy', 'edge', 'opening_hunt', 'on_chain',
                'ord-low', 'filled', '2026-04-02T00:00:00Z', 'low')
        """
    )
    trade.execute(
        """
        INSERT INTO outcome_fact
        (position_id, strategy_key, settled_at, decision_snapshot_id, pnl, outcome)
        VALUES ('pos-low', 'center_buy', '2026-04-04T00:00:00Z',
                'snap-low', -5.0, 0)
        """
    )
    trade.commit()
    trade.close()
    _patch_connections(monkeypatch, trade_db, world_db, backtest_db)

    summary = replay_module.run_replay(
        "2026-04-03",
        "2026-04-03",
        mode="trade_history_audit",
    )

    conn = get_connection(backtest_db)
    row = conn.execute("SELECT * FROM backtest_outcome_comparison").fetchone()
    conn.close()
    assert summary.n_settlements == 1
    assert row["settlement_value"] == 40.0
    assert row["derived_wu_outcome"] == 1
    assert row["divergence_status"] == "wu_win_trade_loss"


def test_closed_bin_scores_from_settlement_value():
    bin = Bin(low=39, high=40, unit="F", label="39-40°F")

    assert derive_outcome_from_settlement_value(40.0, bin, "F") is True
    assert derive_outcome_from_settlement_value(41.0, bin, "F") is False


def test_open_ended_bins_score_from_settlement_value():
    below = Bin(low=None, high=40, unit="F", label="40°F or below")
    above = Bin(low=41, high=None, unit="F", label="41°F or above")

    assert derive_outcome_from_settlement_value(39.0, below, "F") is True
    assert derive_outcome_from_settlement_value(41.0, below, "F") is False
    assert derive_outcome_from_settlement_value(42.0, above, "F") is True
    assert derive_outcome_from_settlement_value(40.0, above, "F") is False


def test_celsius_and_fahrenheit_units_preserved():
    c_bin = Bin(low=12, high=12, unit="C", label="12°C")
    f_bin = Bin(low=39, high=40, unit="F", label="39-40°F")

    assert derive_outcome_from_settlement_value(12.0, c_bin, "C") is True
    assert derive_outcome_from_settlement_value(40.0, f_bin, "F") is True
    try:
        derive_outcome_from_settlement_value(12.0, c_bin, "F")
    except ValueError as exc:
        assert "unit mismatch" in str(exc)
    else:
        raise AssertionError("unit mismatch should fail")
