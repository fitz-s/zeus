"""Tests for database schema initialization."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.state.db import get_connection, init_schema


def test_init_schema_creates_all_tables():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row["name"] for row in cursor.fetchall()}

    expected = {
        "settlements", "observations", "market_events", "token_price_log",
        "ensemble_snapshots", "calibration_pairs", "platt_models",
        "trade_decisions", "shadow_signals", "chronicle", "solar_daily",
        "observation_instants", "diurnal_peak_prob"
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"
    conn.close()


def test_init_schema_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)
    init_schema(conn)  # Should not raise
    conn.close()


def test_ensemble_snapshots_unique_constraint():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)

    row = {
        "city": "NYC", "target_date": "2026-01-15",
        "issue_time": "2026-01-12T00:00:00Z",
        "valid_time": "2026-01-15T00:00:00Z",
        "available_at": "2026-01-12T06:00:00Z",
        "fetch_time": "2026-01-12T06:05:00Z",
        "lead_hours": 72.0,
        "members_json": "[50.0]",
        "model_version": "ecmwf_ifs025",
        "data_version": "v1"
    }

    conn.execute("""
        INSERT INTO ensemble_snapshots
        (city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version)
        VALUES (:city, :target_date, :issue_time, :valid_time, :available_at,
                :fetch_time, :lead_hours, :members_json, :model_version, :data_version)
    """, row)
    conn.commit()

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO ensemble_snapshots
            (city, target_date, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, model_version, data_version)
            VALUES (:city, :target_date, :issue_time, :valid_time, :available_at,
                    :fetch_time, :lead_hours, :members_json, :model_version, :data_version)
        """, row)

    conn.close()


def test_wal_mode_enabled():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()


def test_manual_portfolio_state_does_not_write_real_exit_audit(monkeypatch):
    from src.state.portfolio import PortfolioState, Position, close_position

    state = PortfolioState()
    state.positions.append(Position(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        unit="F",
    ))

    def _boom(*args, **kwargs):
        raise AssertionError("real zeus.db should not be touched from manual test state")

    monkeypatch.setattr("src.state.db.get_connection", _boom)

    closed = close_position(state, "t1", 1.0, "SETTLEMENT")
    assert closed is not None


def test_load_portfolio_enables_audit_logging(tmp_path):
    from src.state.portfolio import load_portfolio

    state = load_portfolio(tmp_path / "missing.json")
    assert state.audit_logging_enabled is True


def test_log_trade_entry_persists_replay_critical_fields(tmp_path):
    from src.state.db import log_trade_entry
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version)
        VALUES (123, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T01:00:00Z', '2026-03-31T01:00:00Z', 24.0, '[40.0]', 'ecmwf_ifs025', 'test')
        """
    )

    pos = Position(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        entry_ci_width=0.10,
        decision_snapshot_id="123",
        calibration_version="platt_v1",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="opening_hunt",
        market_hours_open=2.5,
        fill_quality=0.01,
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch", "platt_calibration"],
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T01:00:00Z"}',
        edge_context_json='{"forward_edge":0.2}',
        entered_at="2026-04-01T01:00:00Z",
    )

    log_trade_entry(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT forecast_snapshot_id, calibration_model_version, strategy, edge_source,
               discovery_mode, market_hours_open, fill_quality, entry_method,
               selected_method, applied_validations_json,
               settlement_semantics_json, epistemic_context_json, edge_context_json
        FROM trade_decisions
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row["forecast_snapshot_id"] == 123
    assert row["calibration_model_version"] == "platt_v1"
    assert row["strategy"] == "center_buy"
    assert row["edge_source"] == "center_buy"
    assert row["discovery_mode"] == "opening_hunt"
    assert row["market_hours_open"] == pytest.approx(2.5)
    assert row["fill_quality"] == pytest.approx(0.01)
    assert row["entry_method"] == "ens_member_counting"
    assert row["selected_method"] == "ens_member_counting"
    assert "platt_calibration" in row["applied_validations_json"]
    assert row["settlement_semantics_json"] == '{"measurement_unit":"F"}'
    assert row["epistemic_context_json"] == '{"decision_time_utc":"2026-04-01T01:00:00Z"}'
    assert row["edge_context_json"] == '{"forward_edge":0.2}'


def test_log_trade_exit_persists_exit_reason_and_strategy(tmp_path):
    from src.state.db import log_trade_exit
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version)
        VALUES (456, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T01:00:00Z', '2026-03-31T01:00:00Z', 24.0, '[40.0]', 'ecmwf_ifs025', 'test')
        """
    )

    pos = Position(
        trade_id="t2",
        market_id="m2",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_no",
        unit="F",
        size_usd=12.0,
        entry_price=0.70,
        p_posterior=0.82,
        edge=0.12,
        decision_snapshot_id="456",
        calibration_version="platt_v2",
        strategy="shoulder_sell",
        edge_source="shoulder_sell",
        discovery_mode="update_reaction",
        market_hours_open=14.0,
        fill_quality=-0.02,
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["risk_limits", "anti_churn"],
        exit_reason="EDGE_REVERSAL",
        admin_exit_reason="",
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T05:00:00Z"}',
        edge_context_json='{"forward_edge":0.12}',
        exit_price=0.55,
        pnl=-2.57,
        last_exit_at="2026-04-01T05:00:00Z",
    )

    log_trade_exit(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT forecast_snapshot_id, calibration_model_version, strategy, edge_source,
               discovery_mode, market_hours_open, fill_quality, entry_method,
               selected_method, applied_validations_json, exit_reason, admin_exit_reason,
               settlement_semantics_json, epistemic_context_json, edge_context_json
        FROM trade_decisions
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row["forecast_snapshot_id"] == 456
    assert row["calibration_model_version"] == "platt_v2"
    assert row["strategy"] == "shoulder_sell"
    assert row["edge_source"] == "shoulder_sell"
    assert row["discovery_mode"] == "update_reaction"
    assert row["market_hours_open"] == pytest.approx(14.0)
    assert row["fill_quality"] == pytest.approx(-0.02)
    assert row["entry_method"] == "ens_member_counting"
    assert row["selected_method"] == "ens_member_counting"
    assert "anti_churn" in row["applied_validations_json"]
    assert row["exit_reason"] == "EDGE_REVERSAL"
    assert row["settlement_semantics_json"] == '{"measurement_unit":"F"}'
    assert row["epistemic_context_json"] == '{"decision_time_utc":"2026-04-01T05:00:00Z"}'
    assert row["edge_context_json"] == '{"forward_edge":0.12}'


def test_log_trade_entry_persists_pending_lifecycle_state(tmp_path):
    from src.state.db import log_trade_entry
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="runtime-t1",
        market_id="m_pending",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="opening_hunt",
        market_hours_open=2.5,
        fill_quality=0.01,
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        state="pending_tracked",
        order_id="order-123",
        order_status="pending",
        order_posted_at="2026-04-01T01:00:00Z",
        chain_state="local_only",
    )

    log_trade_entry(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT status, timestamp, runtime_trade_id, order_id, order_status_text,
               order_posted_at, entered_at_ts, chain_state, fill_price
        FROM trade_decisions
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row["status"] == "pending_tracked"
    assert row["timestamp"] == "2026-04-01T01:00:00Z"
    assert row["runtime_trade_id"] == "runtime-t1"
    assert row["order_id"] == "order-123"
    assert row["order_status_text"] == "pending"
    assert row["order_posted_at"] == "2026-04-01T01:00:00Z"
    assert row["entered_at_ts"] == ""
    assert row["chain_state"] == "local_only"
    assert row["fill_price"] is None


def test_update_trade_lifecycle_promotes_pending_row_to_entered(tmp_path):
    from src.state.db import log_trade_entry, update_trade_lifecycle
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="runtime-t2",
        market_id="m_pending",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        state="pending_tracked",
        order_id="order-234",
        order_status="pending",
        order_posted_at="2026-04-01T01:00:00Z",
        chain_state="local_only",
    )
    log_trade_entry(conn, pos)

    pos.state = "entered"
    pos.entry_price = 0.41
    pos.order_status = "filled"
    pos.chain_state = "synced"
    pos.entered_at = "2026-04-01T01:05:00Z"
    update_trade_lifecycle(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT status, timestamp, fill_price, filled_at, entered_at_ts, chain_state, order_status_text
        FROM trade_decisions
        WHERE runtime_trade_id = 'runtime-t2'
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row["status"] == "entered"
    assert row["timestamp"] == "2026-04-01T01:05:00Z"
    assert row["fill_price"] == pytest.approx(0.41)
    assert row["filled_at"] == "2026-04-01T01:05:00Z"
    assert row["entered_at_ts"] == "2026-04-01T01:05:00Z"
    assert row["chain_state"] == "synced"
    assert row["order_status_text"] == "filled"


def test_backfill_trade_decision_attribution_updates_matching_rows(tmp_path):
    from scripts.backfill_trade_decision_attribution import run_backfill

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version)
        VALUES (123, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T01:00:00Z', '2026-03-31T01:00:00Z', 24.0, '[40.0]', 'ecmwf_ifs025', 'test')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, p_raw, p_posterior,
         edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env)
        VALUES ('real_mkt', '39-40°F', 'buy_yes', 10.0, 0.4, '2026-04-01T01:00:00Z',
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered', 'center_buy', 'paper')
        """
    )
    conn.commit()
    conn.close()

    positions_path = tmp_path / "positions-paper.json"
    positions_path.write_text(json.dumps({
        "positions": [{
            "trade_id": "t1",
            "market_id": "real_mkt",
            "city": "NYC",
            "cluster": "US-Northeast",
            "target_date": "2026-04-01",
            "bin_label": "39-40°F",
            "direction": "buy_yes",
            "unit": "F",
            "size_usd": 10.0,
            "entry_price": 0.4,
            "p_posterior": 0.6,
            "edge": 0.2,
            "entry_ci_width": 0.1,
            "decision_snapshot_id": "123",
            "strategy": "center_buy",
            "discovery_mode": "opening_hunt",
            "market_hours_open": 2.5,
            "fill_quality": 0.01,
            "entry_method": "ens_member_counting",
            "selected_method": "ens_member_counting",
            "applied_validations": ["ens_fetch"],
            "entered_at": "2026-04-01T01:00:00Z"
        }],
        "recent_exits": []
    }), encoding="utf-8")

    import scripts.backfill_trade_decision_attribution as backfill
    import src.state.db as db_module

    original_get_connection = backfill.get_connection
    try:
        backfill.get_connection = lambda: db_module.get_connection(db_path)
        result = run_backfill(positions_path)
    finally:
        backfill.get_connection = original_get_connection

    assert result["updated_rows"] == 1

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT strategy, discovery_mode, market_hours_open, fill_quality, entry_method, selected_method, applied_validations_json FROM trade_decisions LIMIT 1"
    ).fetchone()
    conn.close()

    assert row["strategy"] == "center_buy"
    assert row["discovery_mode"] == "opening_hunt"
    assert row["market_hours_open"] == pytest.approx(2.5)
    assert row["fill_quality"] == pytest.approx(0.01)
    assert row["entry_method"] == "ens_member_counting"
    assert row["selected_method"] == "ens_member_counting"


def test_backfill_recent_exits_attribution_updates_matching_rows(tmp_path):
    from scripts.backfill_recent_exits_attribution import run_backfill

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version)
        VALUES (123, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T01:00:00Z', '2026-03-31T01:00:00Z', 24.0, '[40.0]', 'ecmwf_ifs025', 'test')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, p_raw, p_posterior,
         edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env,
         forecast_snapshot_id, strategy, selected_method, market_hours_open, fill_quality,
         applied_validations_json, admin_exit_reason, settlement_semantics_json,
         epistemic_context_json, edge_context_json)
        VALUES
        ('real_mkt', '39-40°F', 'buy_yes', 10.0, 0.4, '2026-04-01T05:00:00Z',
         0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'exited', 'center_buy', 'paper',
         123, 'center_buy', 'ens_member_counting', 3.5, 0.01,
         '["ens_fetch"]', '', '{"station":"KNYC"}', '{"daylight":0.5}', '{"edge":0.2}')
        """
    )
    conn.commit()
    conn.close()

    positions_path = tmp_path / "positions-paper.json"
    positions_path.write_text(json.dumps({
        "positions": [],
        "recent_exits": [{
            "trade_id": "t1",
            "market_id": "real_mkt",
            "bin_label": "39-40°F",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "decision_snapshot_id": "123",
            "strategy": "center_buy",
            "exited_at": "2026-04-01T05:00:00Z",
        }],
    }), encoding="utf-8")

    import scripts.backfill_recent_exits_attribution as backfill

    original_get_connection = backfill.get_connection
    try:
        backfill.get_connection = lambda: get_connection(db_path)
        result = run_backfill(positions_path)
    finally:
        backfill.get_connection = original_get_connection

    assert result["updated_exits"] == 1
    payload = json.loads(positions_path.read_text())
    exit_row = payload["recent_exits"][0]
    assert exit_row["selected_method"] == "ens_member_counting"
    assert exit_row["market_hours_open"] == pytest.approx(3.5)
    assert exit_row["fill_quality"] == pytest.approx(0.01)
    assert exit_row["applied_validations"] == ["ens_fetch"]


def test_backfill_trade_decisions_recovers_market_hours_from_active_market_metadata(tmp_path, monkeypatch):
    from scripts.backfill_trade_decision_attribution import run_backfill
    import scripts.backfill_trade_decision_attribution as backfill
    import src.state.db as db_module
    import src.data.market_scanner as market_scanner

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, p_raw, p_posterior,
         edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env)
        VALUES ('cond-123', '39-40°F', 'buy_yes', 10.0, 0.4, '2026-04-01T12:00:00+00:00',
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered', 'center_buy', 'paper')
        """
    )
    conn.commit()
    conn.close()

    positions_path = tmp_path / "positions-paper.json"
    positions_path.write_text('{"positions":[],"recent_exits":[]}', encoding="utf-8")

    original_get_connection = backfill.get_connection
    original_get_active_events = market_scanner._get_active_events
    original_extract_outcomes = market_scanner._extract_outcomes
    try:
        backfill.get_connection = lambda: db_module.get_connection(db_path)
        monkeypatch.setattr(
            market_scanner,
            "_get_active_events",
            lambda: [{"createdAt": "2026-04-01T10:00:00+00:00", "markets": []}],
        )
        monkeypatch.setattr(
            market_scanner,
            "_extract_outcomes",
            lambda event: [{"market_id": "cond-123"}],
        )
        result = run_backfill(positions_path)
    finally:
        backfill.get_connection = original_get_connection
        market_scanner._get_active_events = original_get_active_events
        market_scanner._extract_outcomes = original_extract_outcomes

    assert result["recovered_market_hours_rows"] == 1
    assert result["remaining_null_market_hours_rows"] == 0

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT market_hours_open FROM trade_decisions WHERE market_id = 'cond-123'"
    ).fetchone()
    conn.close()
    assert row["market_hours_open"] == pytest.approx(2.0)
