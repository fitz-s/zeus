# Lifecycle: created=2026-04-25; last_reviewed=2026-04-30; last_reused=2026-05-06
# Purpose: Lock replay CLI and market-events preflight behavior against unsafe diagnostic fallback.
# Reuse: Run when replay preflight, WU settlement sweep, or replay CLI error handling changes.
# Authority basis: POST_AUDIT_HANDOFF 4.2.C market-events preflight packet
import json
from types import SimpleNamespace
import sys

import pytest

from src.engine.replay import (
    ReplayPreflightError,
    ReplayContext,
    _assert_market_events_ready_for_replay,
    _market_price_linkage_limitations,
    run_replay,
)
from src.state.db import get_connection, init_schema
import scripts.run_replay as cli_module
from scripts.run_replay import _format_total_pnl, _pnl_available


def _seed_market_events(conn, city: str, target_date: str, labels: tuple[str, ...]) -> None:
    for index, label in enumerate(labels, start=1):
        conn.execute(
            """
            INSERT INTO market_events
            (market_slug, city, target_date, condition_id, token_id, range_label)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"{city.lower()}-{target_date}-{index}",
                city,
                target_date,
                f"condition-{city.lower()}-{target_date}-{index}",
                f"token-{city.lower()}-{target_date}-{index}",
                label,
            ),
        )


def _mark_settlements_verified(conn) -> None:
    conn.execute("UPDATE settlements SET authority = 'VERIFIED'")


def _patch_trade_history_connections(monkeypatch, trade_db, world_db, backtest_db) -> None:
    import src.engine.replay as replay_module
    import src.state.db as db_module

    def _trade_with_world():
        conn = db_module.get_connection(trade_db)
        conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
        return conn

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", _trade_with_world)
    monkeypatch.setattr(replay_module, "get_backtest_connection", lambda: db_module.get_connection(backtest_db))


def _seed_trade_history_fixture(
    tmp_path,
    *,
    position_decision_snapshot_id: str = "snap-1",
    outcome_decision_snapshot_id: str | None = "snap-1",
    outcome_settled_at: str | None = "2026-04-04T00:00:00Z",
):
    trade_db = tmp_path / "trade-history-trade.db"
    world_db = tmp_path / "trade-history-world.db"
    backtest_db = tmp_path / "trade-history-backtest.db"

    world = get_connection(world_db)
    init_schema(world)
    world.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('NYC', '2026-04-03', '39-40°F', 40.0, 'high')
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
        VALUES ('pos-1', 'settled', 'pos-1', 'mkt', 'NYC', 'US-Northeast',
                '2026-04-03', '39-40°F', 'buy_yes', 'F', 5.0, 10.0, 5.0,
                0.5, 0.6, 0.6, 0.1, 0.5, ?, 'entry', 'center_buy',
                'edge', 'opening_hunt', 'on_chain', 'ord-1', 'filled',
                '2026-04-02T00:00:00Z', 'high')
        """,
        (position_decision_snapshot_id,),
    )
    trade.execute(
        """
        INSERT INTO outcome_fact
        (position_id, strategy_key, settled_at, decision_snapshot_id, pnl, outcome)
        VALUES ('pos-1', 'center_buy', ?, ?, -5.0, 0)
        """,
        (outcome_settled_at, outcome_decision_snapshot_id),
    )
    trade.commit()
    trade.close()
    return trade_db, world_db, backtest_db


def test_run_replay_allows_snapshot_only_reference_opt_in(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Paris', '2026-04-03', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (31, 'Paris', '2026-04-03', '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T08:00:00Z', '2026-04-02T08:05:00Z', 24.0, '[12.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES ('Paris', '2026-04-03', '12°C', 1.0, 1, 1.0, 'MAM', 'Paris', '2026-04-02T08:00:00Z', 12.0)
        """
    )
    _seed_market_events(conn, "London", "2026-04-03", ("12°C",))
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    original_get_connection = replay_module.get_trade_connection_with_world
    try:
        replay_module.get_trade_connection_with_world = lambda: db_module.get_connection(db_path)
        with pytest.raises(ReplayPreflightError, match="no_market_events"):
            run_replay("2026-04-03", "2026-04-03", mode="audit")
        relaxed = run_replay(
            "2026-04-03",
            "2026-04-03",
            mode="audit",
            allow_snapshot_only_reference=True,
        )
    finally:
        replay_module.get_trade_connection_with_world = original_get_connection

    assert relaxed.n_replayed >= 1


def test_counterfactual_replay_does_not_auto_enable_snapshot_only_reference(tmp_path, monkeypatch):
    db_path = tmp_path / "counterfactual-strict.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Paris', '2026-04-03', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (32, 'Paris', '2026-04-03', '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T08:00:00Z', '2026-04-02T08:05:00Z', 24.0, '[12.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES ('Paris', '2026-04-03', '12°C', 1.0, 1, 1.0, 'MAM', 'Paris', '2026-04-02T08:00:00Z', 12.0)
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    original_get_connection = replay_module.get_trade_connection_with_world
    try:
        replay_module.get_trade_connection_with_world = lambda: db_module.get_connection(db_path)
        with pytest.raises(ReplayPreflightError, match="no_market_events"):
            run_replay("2026-04-03", "2026-04-03", mode="counterfactual")
        relaxed = run_replay(
            "2026-04-03",
            "2026-04-03",
            mode="counterfactual",
            allow_snapshot_only_reference=True,
        )
    finally:
        replay_module.get_trade_connection_with_world = original_get_connection

    assert relaxed.n_replayed >= 1
    assert relaxed.limitations["forecast_rows_fallback"] is True
    assert relaxed.limitations["promotion_authority"] is False


def test_run_replay_snapshot_only_can_fallback_to_forecast_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "forecast-fallback.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Ankara', '2026-04-03', '20°C', 20.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES
        ('Ankara', '2026-04-03', '20°C', 0.5, 1, 1.0, 'MAM', 'Ankara', '2026-04-02T08:00:00Z', 20.0),
        ('Ankara', '2026-04-03', '21°C', 0.5, 0, 1.0, 'MAM', 'Ankara', '2026-04-02T08:00:00Z', 20.0)
        """
    )
    conn.execute(
        """
        INSERT INTO forecasts
        (city, target_date, source, forecast_basis_date, lead_days, forecast_high, temp_unit)
        VALUES
        ('Ankara', '2026-04-03', 'ecmwf_previous_runs', '2026-04-02', 1, 20.0, 'C'),
        ('Ankara', '2026-04-03', 'gfs_previous_runs', '2026-04-02', 1, 21.0, 'C')
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    original_get_connection = replay_module.get_trade_connection_with_world
    try:
        replay_module.get_trade_connection_with_world = lambda: db_module.get_connection(db_path)
        with pytest.raises(ReplayPreflightError, match="no_market_events"):
            run_replay("2026-04-03", "2026-04-03", mode="audit")
        relaxed = run_replay(
            "2026-04-03",
            "2026-04-03",
            mode="audit",
            allow_snapshot_only_reference=True,
        )
    finally:
        replay_module.get_trade_connection_with_world = original_get_connection

    assert relaxed.n_replayed == 1
    assert relaxed.outcomes[0].snapshot_id.startswith("forecast_rows:Ankara")
    assert relaxed.limitations["decision_reference_source_counts"] == {"forecasts_table_synthetic": 1}
    assert relaxed.limitations["diagnostic_replay_subjects"] == 1
    assert relaxed.limitations["diagnostic_replay_subject_rate"] == 1.0
    assert any(
        "diagnostic_reference" in decision.applied_validations
        for outcome in relaxed.outcomes
        for decision in outcome.replay_decisions
    )


def test_run_replay_shadow_signal_fallback_uses_legacy_diagnostic_source(tmp_path, monkeypatch):
    db_path = tmp_path / "shadow-signal-fallback.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Dallas', '2026-04-05', '41-42°F', 42.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (51, 'Dallas', '2026-04-05', '2026-04-04T00:00:00Z', '2026-04-05T00:00:00Z',
                '2026-04-04T08:00:00Z', '2026-04-04T08:05:00Z', 24.0,
                '[39.0, 42.0]', '[0.1, 0.9]', 2.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Dallas', '2026-04-05', '39-40°F', 0.1, 0, 1.0, 'MAM', 'Dallas',
         '2026-04-04T08:00:00Z', 42.0),
        ('Dallas', '2026-04-05', '41-42°F', 0.9, 1, 1.0, 'MAM', 'Dallas',
         '2026-04-04T08:00:00Z', 42.0)
        """
    )
    conn.execute(
        """
        INSERT INTO shadow_signals
        (city, target_date, timestamp, decision_snapshot_id, p_raw_json, p_cal_json, edges_json, lead_hours)
        VALUES ('Dallas', '2026-04-05', '2026-04-04T10:00:00+00:00', '51',
                '[0.1, 0.9]', '[0.15, 0.85]',
                '[{\"bin_label\":\"39-40°F\"},{\"bin_label\":\"41-42°F\"}]',
                14.0)
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))

    summary = run_replay(
        "2026-04-05",
        "2026-04-05",
        mode="audit",
        allow_snapshot_only_reference=True,
    )

    assert summary.n_replayed == 1
    assert summary.limitations["authority_scope"] == "diagnostic_non_promotion"
    assert summary.limitations["decision_reference_source_counts"] == {
        "legacy_shadow_signal_diagnostic": 1,
    }
    assert summary.limitations["diagnostic_replay_subjects"] == 1
    assert summary.outcomes[0].decision_reference_source == "legacy_shadow_signal_diagnostic"
    validations = [
        validation
        for outcome in summary.outcomes
        for decision in outcome.replay_decisions
        for validation in decision.applied_validations
    ]
    assert "diagnostic_reference" in validations
    assert "authority_scope:diagnostic_non_promotion" in validations
    assert "decision_reference_storage_source:shadow_signals" in validations


def test_wu_settlement_sweep_requires_market_events_for_strict_subjects(tmp_path, monkeypatch):
    db_path = tmp_path / "wu-preflight.db"
    conn = get_connection(db_path)
    init_schema(conn)
    # D1: wu_settlement_sweep now reads settlements_v2; fixture updated accordingly
    conn.execute(
        """
        INSERT INTO settlements_v2
            (city, target_date, temperature_metric, winning_bin, settlement_value, authority)
        VALUES ('Paris', '2026-04-03', 'high', '12°C', 12.0, 'VERIFIED')
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    monkeypatch.setattr(
        replay_module,
        "get_trade_connection_with_world",
        lambda: db_module.get_connection(db_path),
    )

    with pytest.raises(ReplayPreflightError, match="no_market_events"):
        run_replay("2026-04-03", "2026-04-03", mode="wu_settlement_sweep")


def test_wu_settlement_sweep_rejects_wrong_market_event_label(tmp_path, monkeypatch):
    db_path = tmp_path / "wu-wrong-label.db"
    conn = get_connection(db_path)
    init_schema(conn)
    # D1: wu_settlement_sweep now reads settlements_v2; fixture updated accordingly
    conn.execute(
        """
        INSERT INTO settlements_v2
            (city, target_date, temperature_metric, winning_bin, settlement_value, authority)
        VALUES ('Paris', '2026-04-03', 'high', '12°C', 12.0, 'VERIFIED')
        """
    )
    _seed_market_events(conn, "Paris", "2026-04-03", ("99°C",))
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    monkeypatch.setattr(
        replay_module,
        "get_trade_connection_with_world",
        lambda: db_module.get_connection(db_path),
    )

    with pytest.raises(ReplayPreflightError, match="Paris:2026-04-03:12°C"):
        run_replay("2026-04-03", "2026-04-03", mode="wu_settlement_sweep")


def test_market_events_preflight_matches_bins_semantically(tmp_path):
    db_path = tmp_path / "semantic-market-label.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Paris', '2026-04-03', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    _seed_market_events(
        conn,
        "Paris",
        "2026-04-03",
        ("Will the temperature in Paris be 12°C on April 3?",),
    )
    rows = conn.execute(
        """
        SELECT city, target_date, settlement_value, winning_bin
        FROM settlements
        WHERE target_date = '2026-04-03'
        """
    ).fetchall()

    _assert_market_events_ready_for_replay(
        ReplayContext(conn),
        rows,
        start_date="2026-04-03",
        end_date="2026-04-03",
        lane="wu_settlement_sweep",
    )


def test_replay_without_market_price_linkage_cannot_generate_pnl(tmp_path, monkeypatch):
    db_path = tmp_path / "unpriced-replay.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Paris', '2026-04-04', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (41, 'Paris', '2026-04-04', '2026-04-03T00:00:00Z', '2026-04-04T00:00:00Z',
                '2026-04-03T08:00:00Z', '2026-04-03T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-04', '12°C', 0.9, 1, 1.0, 'MAM', 'Paris',
         '2026-04-03T08:00:00Z', 12.0),
        ('Paris', '2026-04-04', '13°C', 0.1, 0, 1.0, 'MAM', 'Paris',
         '2026-04-03T08:00:00Z', 12.0)
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
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

    summary = run_replay(
        "2026-04-04",
        "2026-04-04",
        mode="audit",
        allow_snapshot_only_reference=True,
    )

    assert summary.n_replayed == 1
    assert summary.n_would_trade == 0
    assert summary.replay_total_pnl == 0.0
    assert summary.replay_win_rate == 0.0
    assert summary.limitations["market_price_unavailable_subjects"] == 1
    assert summary.limitations["pnl_requires_market_price_linkage"] is True

    decision = summary.outcomes[0].replay_decisions[0]
    assert decision.should_trade is False
    assert decision.rejection_stage == "MARKET_PRICE_UNAVAILABLE"
    assert decision.size_usd == 0.0
    assert "market_price_unavailable" in decision.applied_validations


def test_replay_alpha_uses_trade_decision_market_hours_open(tmp_path, monkeypatch):
    db_path = tmp_path / "market-hours-open.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Paris', '2026-04-05', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (51, 'Paris', '2026-04-05', '2026-04-04T00:00:00Z', '2026-04-05T00:00:00Z',
                '2026-04-04T08:00:00Z', '2026-04-04T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-05', '12°C', 0.9, 1, 1.0, 'MAM', 'Paris',
         '2026-04-04T08:00:00Z', 12.0),
        ('Paris', '2026-04-05', '13°C', 0.1, 0, 1.0, 'MAM', 'Paris',
         '2026-04-04T08:00:00Z', 12.0)
        """
    )
    _seed_market_events(conn, "Paris", "2026-04-05", ("12°C", "13°C"))
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status,
         edge_source, runtime_trade_id, market_hours_open, env)
        VALUES ('mkt', '12°C', 'buy_yes', 5.0, 0.4, '2026-04-04T08:10:00+00:00', 51,
                0.9, 0.9, 0.5, 0.2, 0.6, 0.0, 'entered',
                'center_buy', 'pos-1', 2.5, 'live')
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module
    import src.strategy.market_fusion as market_fusion_module

    captured = {}

    def _compute_alpha(**kwargs):
        captured["hours_since_open"] = kwargs["hours_since_open"]
        return SimpleNamespace(value=0.5)

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_fusion_module, "compute_alpha", _compute_alpha)

    run_replay(
        "2026-04-05",
        "2026-04-05",
        mode="audit",
    )

    assert captured["hours_since_open"] == 2.5


def test_replay_alpha_uses_no_trade_market_hours_open(tmp_path, monkeypatch):
    db_path = tmp_path / "no-trade-market-hours-open.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Paris', '2026-04-06', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (61, 'Paris', '2026-04-06', '2026-04-05T00:00:00Z', '2026-04-06T00:00:00Z',
                '2026-04-05T08:00:00Z', '2026-04-05T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-06', '12°C', 0.9, 1, 1.0, 'MAM', 'Paris',
         '2026-04-05T08:00:00Z', 12.0),
        ('Paris', '2026-04-06', '13°C', 0.1, 0, 1.0, 'MAM', 'Paris',
         '2026-04-05T08:00:00Z', 12.0)
        """
    )
    _seed_market_events(conn, "Paris", "2026-04-06", ("12°C", "13°C"))
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "opening_hunt",
            "2026-04-05T08:10:00+00:00",
            "2026-04-05T08:11:00+00:00",
            """{
              "trade_cases": [],
              "no_trade_cases": [{
                "decision_id": "nt-1",
                "city": "Paris",
                "target_date": "2026-04-06",
                "range_label": "12°C",
                "direction": "buy_yes",
                "rejection_stage": "FDR_FILTERED",
                "decision_snapshot_id": "61",
                "bin_labels": ["12°C", "13°C"],
                "p_raw_vector": [0.9, 0.1],
                "p_cal_vector": [0.9, 0.1],
                "p_market_vector": [],
                "alpha": 0.0,
                "market_hours_open": 3.5,
                "agreement": "AGREE",
                "timestamp": "2026-04-05T08:10:00+00:00"
              }]
            }""",
            "2026-04-05T08:11:00+00:00",
            "live",
        ),
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module
    import src.strategy.market_fusion as market_fusion_module

    captured = {}

    def _compute_alpha(**kwargs):
        captured["hours_since_open"] = kwargs["hours_since_open"]
        return SimpleNamespace(value=0.5)

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_fusion_module, "compute_alpha", _compute_alpha)

    run_replay(
        "2026-04-06",
        "2026-04-06",
        mode="audit",
    )

    assert captured["hours_since_open"] == 3.5


def test_replay_alpha_legacy_no_trade_without_market_hours_uses_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-no-trade-market-hours.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES ('Paris', '2026-04-07', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES (71, 'Paris', '2026-04-07', '2026-04-06T00:00:00Z', '2026-04-07T00:00:00Z',
                '2026-04-06T08:00:00Z', '2026-04-06T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-07', '12°C', 0.9, 1, 1.0, 'MAM', 'Paris',
         '2026-04-06T08:00:00Z', 12.0),
        ('Paris', '2026-04-07', '13°C', 0.1, 0, 1.0, 'MAM', 'Paris',
         '2026-04-06T08:00:00Z', 12.0)
        """
    )
    _seed_market_events(conn, "Paris", "2026-04-07", ("12°C", "13°C"))
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "opening_hunt",
            "2026-04-06T08:10:00+00:00",
            "2026-04-06T08:11:00+00:00",
            """{
              "trade_cases": [],
              "no_trade_cases": [{
                "decision_id": "nt-legacy",
                "city": "Paris",
                "target_date": "2026-04-07",
                "range_label": "12°C",
                "direction": "buy_yes",
                "rejection_stage": "FDR_FILTERED",
                "decision_snapshot_id": "71",
                "bin_labels": ["12°C", "13°C"],
                "p_raw_vector": [0.9, 0.1],
                "p_cal_vector": [0.9, 0.1],
                "p_market_vector": [],
                "alpha": 0.0,
                "agreement": "AGREE",
                "timestamp": "2026-04-06T08:10:00+00:00"
              }]
            }""",
            "2026-04-06T08:11:00+00:00",
            "live",
        ),
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module
    import src.strategy.market_fusion as market_fusion_module

    captured = {}

    def _compute_alpha(**kwargs):
        captured["hours_since_open"] = kwargs["hours_since_open"]
        return SimpleNamespace(value=0.5)

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_fusion_module, "compute_alpha", _compute_alpha)

    run_replay(
        "2026-04-07",
        "2026-04-07",
        mode="audit",
    )

    assert captured["hours_since_open"] == 48.0


def test_replay_records_provenance_counts_and_hours_since_open_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "replay-provenance.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value, temperature_metric)
        VALUES
        ('Paris', '2026-04-08', '12°C', 12.0, 'high'),
        ('Paris', '2026-04-09', '12°C', 12.0, 'high')
        """
    )
    _mark_settlements_verified(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version, temperature_metric)
        VALUES
        (81, 'Paris', '2026-04-08', '2026-04-07T00:00:00Z', '2026-04-08T00:00:00Z',
         '2026-04-07T08:00:00Z', '2026-04-07T08:05:00Z', 24.0, '[12.0, 13.0]',
         '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1', 'high'),
        (82, 'Paris', '2026-04-09', '2026-04-08T00:00:00Z', '2026-04-09T00:00:00Z',
         '2026-04-08T08:00:00Z', '2026-04-08T08:05:00Z', 24.0, '[12.0, 13.0]',
         '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1', 'high')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-08', '12°C', 0.9, 1, 1.0, 'MAM', 'Paris',
         '2026-04-07T08:00:00Z', 12.0),
        ('Paris', '2026-04-08', '13°C', 0.1, 0, 1.0, 'MAM', 'Paris',
         '2026-04-07T08:00:00Z', 12.0),
        ('Paris', '2026-04-09', '12°C', 0.9, 1, 1.0, 'MAM', 'Paris',
         '2026-04-08T08:00:00Z', 12.0),
        ('Paris', '2026-04-09', '13°C', 0.1, 0, 1.0, 'MAM', 'Paris',
         '2026-04-08T08:00:00Z', 12.0)
        """
    )
    _seed_market_events(conn, "Paris", "2026-04-08", ("12°C", "13°C"))
    _seed_market_events(conn, "Paris", "2026-04-09", ("12°C", "13°C"))
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status,
         edge_source, runtime_trade_id, market_hours_open, env)
        VALUES ('mkt-provenance-1', '12°C', 'buy_yes', 5.0, 0.4, '2026-04-07T08:10:00+00:00', 81,
                0.9, 0.9, 0.5, 0.2, 0.6, 0.0, 'entered',
                'center_buy', 'pos-provenance-1', 2.5, 'live')
        """
    )
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "opening_hunt",
            "2026-04-08T08:10:00+00:00",
            "2026-04-08T08:11:00+00:00",
            """{
              "trade_cases": [],
              "no_trade_cases": [{
                "decision_id": "nt-provenance",
                "city": "Paris",
                "target_date": "2026-04-09",
                "range_label": "12°C",
                "direction": "buy_yes",
                "rejection_stage": "FDR_FILTERED",
                "decision_snapshot_id": "82",
                "bin_labels": ["12°C", "13°C"],
                "p_raw_vector": [0.9, 0.1],
                "p_cal_vector": [0.9, 0.1],
                "p_market_vector": [0.5, 0.5],
                "alpha": 0.0,
                "agreement": "AGREE",
                "timestamp": "2026-04-08T08:10:00+00:00"
              }]
            }""",
            "2026-04-08T08:11:00+00:00",
            "live",
        ),
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))

    summary = run_replay(
        "2026-04-08",
        "2026-04-09",
        mode="audit",
    )

    assert summary.n_replayed == 2
    assert summary.limitations["decision_reference_source_counts"] == {
        "decision_log.no_trade_cases": 1,
        "trade_decisions": 1,
    }
    assert summary.limitations["hours_since_open_source_counts"] == {
        "fallback_48.0": 1,
        "market_hours_open": 1,
    }
    assert summary.limitations["hours_since_open_fallback_subjects"] == 1
    assert summary.limitations["hours_since_open_fallback_rate"] == 0.5
    assert summary.limitations["diagnostic_replay_subjects"] == 0
    assert summary.limitations["diagnostic_replay_subject_rate"] == 0.0

    sources = {outcome.decision_reference_source for outcome in summary.outcomes}
    assert sources == {"trade_decisions", "decision_log.no_trade_cases"}
    assert any(outcome.hours_since_open_fallback for outcome in summary.outcomes)
    assert any(
        "decision_reference_source:trade_decisions" in decision.applied_validations
        for outcome in summary.outcomes
        for decision in outcome.replay_decisions
    )
    assert any(
        "hours_since_open_source:fallback_48.0" in decision.applied_validations
        for outcome in summary.outcomes
        for decision in outcome.replay_decisions
    )
    assert any(
        "hours_since_open_fallback=48.0" in decision.applied_validations
        for outcome in summary.outcomes
        for decision in outcome.replay_decisions
    )


def test_cli_formats_unpriced_replay_pnl_as_unavailable():
    summary = SimpleNamespace(
        replay_total_pnl=0.0,
        n_replayed=298,
        limitations={
            "pnl_available": False,
            "pnl_unavailable_reason": "market_price_unavailable",
            "market_price_unavailable_subjects": 298,
        },
    )

    assert _format_total_pnl(summary) == "N/A (market price unavailable for 298/298 replayed subjects)"


def test_cli_prints_replay_provenance_counts(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli-provenance.db"

    def _run_replay(*, start_date, end_date, mode, overrides=None, allow_snapshot_only_reference=False, temperature_metric="high"):
        return SimpleNamespace(
            run_id="run-2",
            n_settlements=2,
            n_replayed=2,
            coverage_pct=100.0,
            n_would_trade=1,
            replay_win_rate=0.0,
            replay_total_pnl=9.0,
            limitations={
                "pnl_available": False,
                "pnl_requires_market_price_linkage": True,
                "pnl_unavailable_reason": "partial_market_price_linkage",
                "market_price_linked_subjects": 1,
                "market_price_unavailable_subjects": 1,
                "decision_reference_source_counts": {
                    "decision_log.no_trade_cases": 1,
                    "trade_decisions": 1,
                },
                "diagnostic_replay_subjects": 1,
                "hours_since_open_source_counts": {
                    "fallback_48.0": 1,
                    "market_hours_open": 1,
                },
                "hours_since_open_fallback_subjects": 1,
            },
            per_city={
                "Paris": {"n_dates": 2, "n_trades": 1, "total_pnl": 9.0, "win_rate": 0.0},
            },
            outcomes=[],
        )

    import src.engine.replay as replay_module

    monkeypatch.setattr(cli_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(replay_module, "run_replay", _run_replay)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_replay.py",
            "--mode",
            "audit",
            "--start",
            "2026-04-03",
            "--end",
            "2026-04-04",
        ],
    )

    cli_module.main()
    output = capsys.readouterr().out

    assert "Replay provenance:" in output
    assert "decision reference sources: decision_log.no_trade_cases=1, trade_decisions=1" in output
    assert "hours-since-open sources: fallback_48.0=1, market_hours_open=1" in output
    assert "diagnostic replay references: 1/2 replayed subjects" in output
    assert "hours-since-open fallback: 1/2 replayed subjects" in output


def test_cli_surfaces_replay_preflight_failure(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli-preflight.db"

    def _run_replay(**kwargs):
        raise ReplayPreflightError("no_market_events: missing=Paris:2026-04-03")

    import src.engine.replay as replay_module

    monkeypatch.setattr(cli_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(replay_module, "run_replay", _run_replay)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_replay.py",
            "--mode",
            "audit",
            "--start",
            "2026-04-03",
            "--end",
            "2026-04-03",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Replay preflight failed:" in captured.err
    assert "no_market_events" in captured.err


def test_replay_market_price_linkage_limitations_distinguish_full_partial_none():
    full = _market_price_linkage_limitations(
        n_replayed=2,
        market_price_linked_subjects=2,
        market_price_unavailable_subjects=0,
    )
    assert full["market_price_linkage_state"] == "full"
    assert full["pnl_available"] is True
    assert full["pnl_subject_scope"] == "all_replayed_subjects"
    assert full["pnl_unavailable_reason"] == ""

    partial = _market_price_linkage_limitations(
        n_replayed=3,
        market_price_linked_subjects=1,
        market_price_unavailable_subjects=2,
    )
    assert partial["market_price_linkage_state"] == "partial"
    assert partial["pnl_available"] is False
    assert partial["pnl_subject_scope"] == "partial_market_price_linkage"
    assert partial["pnl_unavailable_reason"] == "partial_market_price_linkage"

    none = _market_price_linkage_limitations(
        n_replayed=3,
        market_price_linked_subjects=0,
        market_price_unavailable_subjects=3,
    )
    assert none["market_price_linkage_state"] == "none"
    assert none["pnl_available"] is False
    assert none["pnl_subject_scope"] == "no_market_price_linkage"
    assert none["pnl_unavailable_reason"] == "market_price_unavailable"


def test_replay_limitations_include_missing_parity_dimensions():
    """ZDM-03: replay summaries must report which parity dimensions are missing."""
    from src.engine.replay import _missing_parity_dimensions

    # With full linkage → only sizing and selection parity missing
    missing = _missing_parity_dimensions(full_linkage=True)
    assert "market_price_linkage" not in missing
    assert "active_sizing_parity" in missing
    assert "selection_family_parity" in missing
    assert len(missing) == 2

    # With no linkage → all three missing
    missing_all = _missing_parity_dimensions(full_linkage=False)
    assert "market_price_linkage" in missing_all
    assert len(missing_all) == 3


def test_cli_formats_partial_market_linkage_pnl_as_unavailable():
    summary = SimpleNamespace(
        replay_total_pnl=12.5,
        n_replayed=4,
        limitations={
            "pnl_available": False,
            "pnl_requires_market_price_linkage": True,
            "pnl_unavailable_reason": "partial_market_price_linkage",
            "market_price_linked_subjects": 1,
            "market_price_unavailable_subjects": 3,
        },
    )

    assert _pnl_available(summary) is False
    assert _format_total_pnl(summary) == "N/A (market price linked for 1/4 replayed subjects; partial linkage)"


def test_cli_prints_partial_market_linkage_as_na(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli.db"

    def _run_replay(*, start_date, end_date, mode, overrides=None, allow_snapshot_only_reference=False, temperature_metric="high"):
        return SimpleNamespace(
            run_id="run-1",
            n_settlements=2,
            n_replayed=2,
            coverage_pct=100.0,
            n_would_trade=1,
            replay_win_rate=0.0,
            replay_total_pnl=9.0,
            limitations={
                "pnl_available": False,
                "pnl_requires_market_price_linkage": True,
                "pnl_unavailable_reason": "partial_market_price_linkage",
                "market_price_linked_subjects": 1,
                "market_price_unavailable_subjects": 1,
            },
            per_city={
                "Paris": {"n_dates": 2, "n_trades": 1, "total_pnl": 9.0, "win_rate": 0.0},
            },
            outcomes=[],
        )

    import src.engine.replay as replay_module

    monkeypatch.setattr(cli_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(replay_module, "run_replay", _run_replay)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_replay.py",
            "--mode",
            "audit",
            "--start",
            "2026-04-03",
            "--end",
            "2026-04-04",
        ],
    )

    cli_module.main()
    output = capsys.readouterr().out

    assert "Total PnL:    N/A (market price linked for 1/2 replayed subjects; partial linkage)" in output
    assert "Paris" in output
    assert "N/A" in output
    assert "until all replay subjects have decision-time market price linkage" in output


def test_cli_trade_history_audit_routes_to_backtest_lane(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli.db"
    called = {}

    def _run_replay(*, start_date, end_date, mode, overrides=None, allow_snapshot_only_reference=False, temperature_metric="high"):
        called.update(
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            overrides=overrides,
            allow_snapshot_only_reference=allow_snapshot_only_reference,
        )
        return SimpleNamespace(
            run_id="run-1",
            n_settlements=1,
            n_replayed=0,
            coverage_pct=0.0,
            n_would_trade=0,
            replay_win_rate=0.0,
            replay_total_pnl=0.0,
            limitations={
                "pnl_available": False,
                "pnl_unavailable_reason": "trade_history_audit_reports_actual_trade_pnl_rows_not_simulated_strategy_pnl",
            },
            per_city={},
            outcomes=[],
        )

    import src.engine.replay as replay_module

    monkeypatch.setattr(cli_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(replay_module, "run_replay", _run_replay)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_replay.py",
            "--mode",
            "trade_history_audit",
            "--start",
            "2026-04-03",
            "--end",
            "2026-04-03",
        ],
    )

    cli_module.main()
    output = capsys.readouterr().out

    assert called == {
        "start_date": "2026-04-03",
        "end_date": "2026-04-03",
        "mode": "trade_history_audit",
        "overrides": None,
        "allow_snapshot_only_reference": False,
    }
    assert "TRADE_HISTORY_AUDIT" in output
    assert "Results stored in zeus_backtest.db" in output


def test_trade_history_audit_labels_outcome_fact_as_legacy_non_promotion(tmp_path, monkeypatch):
    trade_db, world_db, backtest_db = _seed_trade_history_fixture(tmp_path)
    _patch_trade_history_connections(monkeypatch, trade_db, world_db, backtest_db)

    summary = run_replay("2026-04-03", "2026-04-03", mode="trade_history_audit")

    conn = get_connection(backtest_db)
    row = conn.execute("SELECT * FROM backtest_outcome_comparison").fetchone()
    conn.close()
    evidence = json.loads(row["evidence_json"])
    assert summary.limitations["actual_trade_outcome_source"] == "outcome_fact_legacy_lifecycle_projection"
    assert summary.limitations["actual_outcome_learning_eligible"] is False
    assert summary.limitations["actual_outcome_promotion_eligible"] is False
    assert row["truth_source"] == "verified_settlement_vs_legacy_outcome_fact"
    assert row["authority_scope"] == "diagnostic_non_promotion"
    assert row["actual_trade_outcome"] == 0
    assert row["actual_pnl"] == -5.0
    assert evidence["actual_trade_outcome_source"] == "outcome_fact_legacy_lifecycle_projection"
    assert evidence["actual_outcome_evidence_class"] == "legacy_lifecycle_projection_not_settlement_authority"
    assert evidence["actual_outcome_authority_scope"] == "diagnostic_non_promotion"
    assert evidence["actual_outcome_learning_eligible"] is False
    assert evidence["actual_outcome_promotion_eligible"] is False
    assert evidence["outcome_fact_consumed_as_actual_trade_evidence"] is True


def test_trade_history_audit_rejects_unlinked_outcome_fact_as_actual_trade_evidence(tmp_path, monkeypatch):
    trade_db, world_db, backtest_db = _seed_trade_history_fixture(
        tmp_path,
        outcome_decision_snapshot_id=None,
    )
    _patch_trade_history_connections(monkeypatch, trade_db, world_db, backtest_db)

    summary = run_replay("2026-04-03", "2026-04-03", mode="trade_history_audit")

    conn = get_connection(backtest_db)
    row = conn.execute("SELECT * FROM backtest_outcome_comparison").fetchone()
    conn.close()
    evidence = json.loads(row["evidence_json"])
    missing = json.loads(row["missing_reason_json"])
    assert summary.n_actual_traded == 0
    assert row["actual_trade_outcome"] is None
    assert row["actual_pnl"] is None
    assert row["divergence_status"] == "trade_unresolved"
    assert "outcome_fact_missing_decision_snapshot_id" in missing
    assert evidence["actual_trade_outcome_source"] == "none"
    assert evidence["actual_pnl_source"] == "none"
    assert evidence["outcome_fact_required_linkage_ok"] is False
    assert evidence["outcome_fact_consumed_as_actual_trade_evidence"] is False


def test_trade_history_audit_rejects_snapshot_mismatched_outcome_fact(tmp_path, monkeypatch):
    trade_db, world_db, backtest_db = _seed_trade_history_fixture(
        tmp_path,
        position_decision_snapshot_id="snap-1",
        outcome_decision_snapshot_id="snap-stale",
    )
    _patch_trade_history_connections(monkeypatch, trade_db, world_db, backtest_db)

    summary = run_replay("2026-04-03", "2026-04-03", mode="trade_history_audit")

    conn = get_connection(backtest_db)
    row = conn.execute("SELECT * FROM backtest_outcome_comparison").fetchone()
    conn.close()
    evidence = json.loads(row["evidence_json"])
    missing = json.loads(row["missing_reason_json"])
    assert summary.n_actual_traded == 0
    assert row["actual_trade_outcome"] is None
    assert row["actual_pnl"] is None
    assert row["divergence_status"] == "trade_unresolved"
    assert "outcome_fact_decision_snapshot_mismatch" in missing
    assert evidence["expected_decision_snapshot_id"] == "snap-1"
    assert evidence["outcome_fact_decision_snapshot_id"] == "snap-stale"
    assert evidence["outcome_fact_decision_snapshot_matches_position"] is False
    assert evidence["outcome_fact_consumed_as_actual_trade_evidence"] is False


# ---------------------------------------------------------------------------
# T6 — wu_settlement_sweep v2 regression antibody (D1 backward-compat gate)
# Ensures run_wu_settlement_sweep reads calibration_pairs_v2 + settlements_v2.
# If the SQL is accidentally reverted to bare calibration_pairs / settlements,
# this test returns n_settlements=0 (v1 tables are empty on main) and fails.
# ---------------------------------------------------------------------------
def test_wu_settlement_sweep_v2_corpus_produces_settlements(tmp_path, monkeypatch):
    """T6: wu_settlement_sweep reads settlements_v2 + calibration_pairs_v2 (D1 antibody).

    Fixture inserts one VERIFIED settlement into settlements_v2 and a matching
    calibration_pairs_v2 row. Asserts n_settlements > 0 and mode == 'wu_settlement_sweep'.
    A revert to bare settlements/calibration_pairs tables would yield n_settlements=0
    (those tables are empty) and the assertion would catch the regression.
    """
    db_path = tmp_path / "wu-v2-antibody.db"
    conn = get_connection(db_path)
    init_schema(conn)

    # Seed settlements_v2 — one VERIFIED HIGH row for Paris
    conn.execute(
        """
        INSERT INTO settlements_v2
            (city, target_date, temperature_metric, winning_bin, settlement_value, authority)
        VALUES ('Paris', '2026-04-10', 'high', '12°C', 12.0, 'VERIFIED')
        """
    )

    # Seed calibration_pairs_v2 — matching forecast row for the same city/date
    conn.execute(
        """
        INSERT INTO calibration_pairs_v2
            (city, target_date, temperature_metric, observation_field,
             range_label, p_raw, outcome, lead_days, season, cluster,
             forecast_available_at, data_version, bias_corrected, authority)
        VALUES ('Paris', '2026-04-10', 'high', 'high_temp',
                '12°C', 0.85, 1, 1.0, 'MAM', 'Paris',
                '2026-04-09T08:00:00Z', 'v2', 0, 'VERIFIED')
        """
    )

    # Seed market_events so the preflight check passes
    _seed_market_events(conn, "Paris", "2026-04-10", ("12°C",))
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    monkeypatch.setattr(
        replay_module,
        "get_trade_connection_with_world",
        lambda: db_module.get_connection(db_path),
    )

    summary = run_replay("2026-04-10", "2026-04-10", mode="wu_settlement_sweep")

    assert summary.mode == "wu_settlement_sweep", f"unexpected mode: {summary.mode!r}"
    assert summary.n_settlements > 0, (
        "n_settlements=0: wu_settlement_sweep returned no rows — "
        "likely SQL still reads from bare settlements/calibration_pairs (v1 empty tables). "
        "Verify D1 port to settlements_v2 + calibration_pairs_v2."
    )
