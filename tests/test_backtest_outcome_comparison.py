from __future__ import annotations

from src.engine import replay as replay_module
from src.state.db import get_connection, init_schema


def _patch_connections(monkeypatch, trade_db, world_db, backtest_db):
    def _trade_with_world():
        conn = get_connection(trade_db)
        conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
        return conn

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", _trade_with_world)
    monkeypatch.setattr(replay_module, "get_backtest_connection", lambda: get_connection(backtest_db))


def _init_trade_world(tmp_path):
    trade_db = tmp_path / "trade.db"
    world_db = tmp_path / "world.db"
    backtest_db = tmp_path / "backtest.db"
    trade = get_connection(trade_db)
    init_schema(trade)
    world = get_connection(world_db)
    init_schema(world)
    world.execute(
        """
        INSERT INTO settlements (city, target_date, settlement_value)
        VALUES ('NYC', '2026-04-03', 40.0)
        """
    )
    world.commit()
    world.close()
    return trade, trade_db, world_db, backtest_db


def _insert_position_and_outcome(trade):
    trade.execute(
        """
        INSERT INTO position_current
        (position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
         direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
         last_monitor_prob, last_monitor_edge, last_monitor_market_price,
         decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
         chain_state, order_id, order_status, updated_at)
        VALUES ('pos-1', 'settled', 'pos-1', 'mkt', 'NYC', 'US-Northeast', '2026-04-03',
                '39-40°F', 'buy_yes', 'F', 5.0, 10.0, 5.0, 0.5, 0.6,
                0.6, 0.1, 0.5, 'snap-1', 'entry', 'center_buy', 'edge',
                'opening_hunt', 'on_chain', 'ord-1', 'filled', '2026-04-02T00:00:00Z')
        """
    )
    trade.execute(
        """
        INSERT INTO outcome_fact
        (position_id, strategy_key, settled_at, decision_snapshot_id, pnl, outcome)
        VALUES ('pos-1', 'center_buy', '2026-04-04T00:00:00Z', 'snap-1', -5.0, 0)
        """
    )
    trade.commit()


def test_divergence_row_does_not_mutate_settlement_or_trade_truth(tmp_path, monkeypatch):
    trade, trade_db, world_db, backtest_db = _init_trade_world(tmp_path)
    _insert_position_and_outcome(trade)
    before_trade = trade.execute("SELECT COUNT(*) FROM outcome_fact").fetchone()[0]
    trade.close()
    world = get_connection(world_db)
    before_world = world.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    world.close()
    _patch_connections(monkeypatch, trade_db, world_db, backtest_db)

    replay_module.run_replay("2026-04-03", "2026-04-03", mode="trade_history_audit")

    trade = get_connection(trade_db)
    world = get_connection(world_db)
    backtest = get_connection(backtest_db)
    row = backtest.execute("SELECT * FROM backtest_outcome_comparison").fetchone()
    assert trade.execute("SELECT COUNT(*) FROM outcome_fact").fetchone()[0] == before_trade
    assert world.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == before_world
    assert row["divergence_status"] == "wu_win_trade_loss"
    trade.close()
    world.close()
    backtest.close()


def test_no_live_trades_reports_zero_trade_history_coverage(tmp_path, monkeypatch):
    trade, trade_db, world_db, backtest_db = _init_trade_world(tmp_path)
    trade.close()
    _patch_connections(monkeypatch, trade_db, world_db, backtest_db)

    summary = replay_module.run_replay("2026-04-03", "2026-04-03", mode="trade_history_audit")

    assert summary.n_settlements == 0
    assert summary.n_replayed == 0


def test_trade_history_audit_reports_orphan_runtime_trade_id(tmp_path, monkeypatch):
    trade, trade_db, world_db, backtest_db = _init_trade_world(tmp_path)
    trade.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status,
         edge_source, runtime_trade_id, env)
        VALUES ('mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-03-31T12:00:00+00:00', NULL,
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered',
                'center_buy', 'orphan-pos', 'live')
        """
    )
    trade.commit()
    trade.close()
    _patch_connections(monkeypatch, trade_db, world_db, backtest_db)

    summary = replay_module.run_replay("2026-04-03", "2026-04-03", mode="trade_history_audit")

    conn = get_connection(backtest_db)
    row = conn.execute("SELECT * FROM backtest_outcome_comparison").fetchone()
    conn.close()
    assert summary.n_settlements == 1
    assert summary.n_replayed == 0
    assert row["divergence_status"] == "orphan_trade_decision"
    assert "orphan_trade_decision" in row["missing_reason_json"]


def test_replay_fidelity_counts_orphan_runtime_trade_id_subjects(tmp_path, monkeypatch):
    trade, trade_db, world_db, _ = _init_trade_world(tmp_path)
    trade.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status,
         edge_source, runtime_trade_id, env)
        VALUES ('mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-03-31T12:00:00+00:00', NULL,
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered',
                'center_buy', 'orphan-pos', 'live')
        """
    )
    trade.commit()
    trade.close()

    import scripts.audit_replay_fidelity as audit_mod

    def _trade_with_world():
        conn = get_connection(trade_db)
        conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))
        return conn

    monkeypatch.setattr(audit_mod, "get_connection", _trade_with_world)

    result = audit_mod.run_audit()

    assert result["lane_readiness"]["trade_history_audit"]["trade_history_subjects"] == 1


def test_backtest_rows_have_diagnostic_non_promotion_authority(tmp_path, monkeypatch):
    trade, trade_db, world_db, backtest_db = _init_trade_world(tmp_path)
    _insert_position_and_outcome(trade)
    trade.close()
    _patch_connections(monkeypatch, trade_db, world_db, backtest_db)

    replay_module.run_replay("2026-04-03", "2026-04-03", mode="trade_history_audit")

    conn = get_connection(backtest_db)
    row = conn.execute("SELECT authority_scope FROM backtest_outcome_comparison").fetchone()
    conn.close()
    assert row["authority_scope"] == "diagnostic_non_promotion"


def test_new_lanes_write_to_zeus_backtest_not_replay_results(tmp_path, monkeypatch):
    trade, trade_db, world_db, backtest_db = _init_trade_world(tmp_path)
    trade.close()
    _patch_connections(monkeypatch, trade_db, world_db, backtest_db)

    replay_module.run_replay("2026-04-03", "2026-04-03", mode="wu_settlement_sweep")

    trade = get_connection(trade_db)
    backtest = get_connection(backtest_db)
    assert trade.execute("SELECT COUNT(*) FROM replay_results").fetchone()[0] == 0
    assert backtest.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 1
    trade.close()
    backtest.close()
