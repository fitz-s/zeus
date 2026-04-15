from __future__ import annotations

import json
import sqlite3

from src.engine import replay as replay_module
from src.engine.replay import derive_outcome_from_settlement_value
from src.state.db import get_connection, init_backtest_schema, init_schema
from src.types import Bin


def _run_wu_sweep(tmp_path, monkeypatch, *, winning_bin: str | None, value: float):
    trade_db = tmp_path / "trade.db"
    world_db = tmp_path / "world.db"
    backtest_db = tmp_path / "backtest.db"

    world = get_connection(world_db)
    init_schema(world)
    world.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('NYC', '2026-04-03', ?, ?)
        """,
        (winning_bin, value),
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


def test_wu_outcome_ignores_null_winning_bin(tmp_path, monkeypatch):
    summary, row = _run_wu_sweep(tmp_path, monkeypatch, winning_bin=None, value=40.0)

    assert summary.n_replayed == 1
    assert row["derived_wu_outcome"] == 1
    assert row["truth_source"] == "wu_settlement_value"


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
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('NYC', '2026-04-03', '39-40°F', 40.0)
        """
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
