"""Tests for database schema initialization."""

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
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
        "trade_decisions", "shadow_signals", "chronicle", "position_events", "solar_daily",
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


def test_log_trade_entry_emits_position_event(tmp_path):
    from src.state.db import log_trade_entry, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-entry",
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
        decision_snapshot_id="snap-1",
        strategy="center_buy",
        edge_source="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        order_posted_at="2026-04-01T01:00:00Z",
        order_id="o1",
        order_status="pending",
        state="pending_tracked",
    )

    log_trade_entry(conn, pos)
    conn.commit()

    events = query_position_events(conn, "rt-entry")
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "POSITION_ENTRY_RECORDED"
    assert events[0]["position_state"] == "pending_tracked"
    assert events[0]["decision_snapshot_id"] == "snap-1"
    assert events[0]["details"]["status"] == "pending_tracked"
    assert events[0]["details"]["entry_method"] == "ens_member_counting"



def test_log_trade_exit_persists_exit_reason_and_strategy(tmp_path):
    from src.state.db import log_trade_exit, query_position_events
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
    events = query_position_events(conn, "t2")
    conn.close()

    assert row["forecast_snapshot_id"] == 456
    assert any(event["event_type"] == "POSITION_EXIT_RECORDED" for event in events)
    exit_event = next(event for event in events if event["event_type"] == "POSITION_EXIT_RECORDED")
    assert exit_event["details"]["exit_reason"] == "EDGE_REVERSAL"
    assert exit_event["details"]["status"] == "exited"
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


def test_update_trade_lifecycle_emits_position_event(tmp_path):
    from src.state.db import log_trade_entry, query_position_events, update_trade_lifecycle
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-life",
        market_id="m3",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=15.0,
        entry_price=0.41,
        p_posterior=0.61,
        edge=0.20,
        decision_snapshot_id="snap-life",
        strategy="center_buy",
        edge_source="center_buy",
        order_id="o-life",
        order_status="pending",
        order_posted_at="2026-04-01T01:00:00Z",
        state="pending_tracked",
    )
    log_trade_entry(conn, pos)

    pos.state = "entered"
    pos.entry_order_id = "o-life"
    pos.entry_fill_verified = True
    pos.entered_at = "2026-04-01T01:05:00Z"
    pos.order_status = "filled"
    pos.chain_state = "synced"
    update_trade_lifecycle(conn, pos)
    conn.commit()

    events = query_position_events(conn, "rt-life")
    conn.close()

    lifecycle_events = [event for event in events if event["event_type"] == "POSITION_LIFECYCLE_UPDATED"]
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0]["details"]["status"] == "entered"
    assert lifecycle_events[0]["details"]["entry_order_id"] == "o-life"
    assert lifecycle_events[0]["details"]["entry_fill_verified"] is True
    assert lifecycle_events[0]["details"]["order_status"] == "filled"
    assert lifecycle_events[0]["details"]["chain_state"] == "synced"


def test_log_execution_report_emits_fill_telemetry(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_report, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-exec",
        market_id="m4",
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
        order_posted_at="2026-04-01T01:00:00Z",
        order_status="filled",
        state="entered",
    )
    result = OrderResult(
        trade_id="rt-exec",
        status="filled",
        fill_price=0.42,
        filled_at="2026-04-01T01:00:05Z",
        submitted_price=0.40,
        shares=25.0,
        timeout_seconds=60,
    )

    log_execution_report(conn, pos, result)
    conn.commit()

    events = query_position_events(conn, "rt-exec")
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "ORDER_FILLED"
    assert events[0]["details"]["submitted_price"] == pytest.approx(0.40)
    assert events[0]["details"]["fill_price"] == pytest.approx(0.42)
    assert events[0]["details"]["fill_quality"] == pytest.approx(0.05)


def test_log_execution_report_emits_rejected_entry_event(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_report, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-exec-rejected",
        market_id="m4",
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
        order_status="rejected",
    )
    result = OrderResult(
        trade_id="rt-exec-rejected",
        status="rejected",
        submitted_price=0.40,
        reason="insufficient_liquidity",
    )

    log_execution_report(conn, pos, result)
    conn.commit()

    events = query_position_events(conn, "rt-exec-rejected")
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "ORDER_REJECTED"
    assert events[0]["details"]["status"] == "rejected"
    assert events[0]["details"]["reason"] == "insufficient_liquidity"


def test_log_settlement_event_emits_durable_record(tmp_path):
    from src.state.db import log_settlement_event, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-settle",
        market_id="m5",
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
        decision_snapshot_id="snap1",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )

    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    conn.commit()

    events = query_position_events(conn, "rt-settle")
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "POSITION_SETTLED"
    assert events[0]["details"]["winning_bin"] == "39-40°F"
    assert events[0]["details"]["won"] is True
    assert events[0]["details"]["outcome"] == 1
    assert events[0]["details"]["contract_version"] == "position_settled.v1"
    assert events[0]["details"]["p_posterior"] == pytest.approx(0.60)
    assert events[0]["details"]["exit_price"] == pytest.approx(1.0)
    assert events[0]["details"]["pnl"] == pytest.approx(15.0)
    assert events[0]["details"]["exit_reason"] == "SETTLEMENT"


def test_query_authoritative_settlement_rows_prefers_position_events(tmp_path):
    from src.state.db import log_settlement_event, query_authoritative_settlement_rows
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-settle-auth",
        market_id="m6",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        edge=0.21,
        decision_snapshot_id="snap-auth",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )

    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    conn.commit()

    rows = query_authoritative_settlement_rows(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "rt-settle-auth"
    assert rows[0]["source"] == "position_events"
    assert rows[0]["authority_level"] == "durable_event"
    assert rows[0]["contract_version"] == "position_settled.v1"
    assert rows[0]["canonical_payload_complete"] is True
    assert rows[0]["contract_missing_fields"] == []
    assert rows[0]["learning_snapshot_ready"] is True
    assert rows[0]["p_posterior"] == pytest.approx(0.61)
    assert rows[0]["outcome"] == 1
    assert rows[0]["pnl"] == pytest.approx(15.0)
    assert rows[0]["winning_bin"] == "39-40°F"
    assert rows[0]["exit_reason"] == "SETTLEMENT"


def test_query_authoritative_settlement_rows_falls_back_to_decision_log(tmp_path):
    from src.state.db import query_authoritative_settlement_rows
    from src.state.decision_chain import SettlementRecord, store_settlement_records

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    store_settlement_records(
        conn,
        [
            SettlementRecord(
                trade_id="legacy-settle",
                city="NYC",
                target_date="2026-04-01",
                range_label="39-40°F",
                direction="buy_yes",
                p_posterior=0.58,
                outcome=1,
                pnl=12.5,
                decision_snapshot_id="legacy-snap",
                edge_source="center_buy",
                strategy="center_buy",
                settled_at="2026-04-01T23:00:00Z",
            )
        ],
    )

    rows = query_authoritative_settlement_rows(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "legacy-settle"
    assert rows[0]["source"] == "decision_log"
    assert rows[0]["authority_level"] == "legacy_decision_log_fallback"
    assert rows[0]["is_degraded"] is True
    assert rows[0]["canonical_payload_complete"] is False
    assert {
        "winning_bin",
        "position_bin",
        "won",
        "exit_price",
        "exit_reason",
    }.issubset(set(rows[0]["contract_missing_fields"]))
    assert rows[0]["outcome"] == 1
    assert rows[0]["pnl"] == pytest.approx(12.5)


def test_query_authoritative_settlement_rows_marks_malformed_position_event(tmp_path):
    from src.state.db import (
        log_position_event,
        query_authoritative_settlement_rows,
        query_authoritative_settlement_source,
    )
    from src.state.decision_chain import SettlementRecord, store_settlement_records
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    malformed_pos = Position(
        trade_id="rt-malformed",
        market_id="m7",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        edge=0.21,
        decision_snapshot_id="snap-missing-posterior",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    log_position_event(
        conn,
        "POSITION_SETTLED",
        malformed_pos,
        details={
            "contract_version": "position_settled.v1",
            "winning_bin": "39-40°F",
            "position_bin": "39-40°F",
            "won": True,
            "outcome": 1,
            # p_posterior intentionally omitted: malformed canonical payload
            "exit_price": 1.0,
            "pnl": 15.0,
            "exit_reason": "SETTLEMENT",
        },
        timestamp="2026-04-01T23:00:00Z",
        source="settlement",
    )

    store_settlement_records(
        conn,
        [
            SettlementRecord(
                trade_id="legacy-fallback",
                city="NYC",
                target_date="2026-04-01",
                range_label="39-40°F",
                direction="buy_yes",
                p_posterior=0.58,
                outcome=1,
                pnl=12.5,
                decision_snapshot_id="legacy-snap",
                edge_source="center_buy",
                strategy="center_buy",
                settled_at="2026-04-01T23:00:00Z",
            )
        ],
    )

    rows = query_authoritative_settlement_rows(conn, limit=10)
    assert query_authoritative_settlement_source(conn) == "position_events"
    conn.close()

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "rt-malformed"
    assert rows[0]["source"] == "position_events"
    assert rows[0]["authority_level"] == "durable_event_malformed"
    assert rows[0]["metric_ready"] is False
    assert "p_posterior" in rows[0]["required_missing_fields"]


def test_query_authoritative_settlement_rows_filters_by_env(tmp_path):
    from src.state.db import log_settlement_event, query_authoritative_settlement_rows
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    paper_pos = Position(
        trade_id="paper-settle",
        market_id="m-paper",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.6,
        edge=0.2,
        exit_price=1.0,
        pnl=6.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
        env="paper",
    )
    live_pos = Position(
        trade_id="live-settle",
        market_id="m-live",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="41-42°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.7,
        edge=0.3,
        exit_price=1.0,
        pnl=7.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
        env="live",
    )
    log_settlement_event(conn, paper_pos, winning_bin="39-40°F", won=True, outcome=1)
    log_settlement_event(conn, live_pos, winning_bin="41-42°F", won=True, outcome=1)
    conn.commit()

    paper_rows = query_authoritative_settlement_rows(conn, limit=10, env="paper")
    live_rows = query_authoritative_settlement_rows(conn, limit=10, env="live")
    conn.close()

    assert [row["trade_id"] for row in paper_rows] == ["paper-settle"]
    assert [row["trade_id"] for row in live_rows] == ["live-settle"]


def test_query_legacy_settlement_records_filters_by_env(tmp_path):
    from src.state.decision_chain import query_legacy_settlement_records

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    paper_artifact = {
        "mode": "settlement",
        "settlements": [
            {
                "trade_id": "paper-legacy",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "p_posterior": 0.6,
                "outcome": 1,
                "pnl": 6.0,
                "settled_at": "2026-04-01T23:00:00Z",
            }
        ],
    }
    live_artifact = {
        "mode": "settlement",
        "settlements": [
            {
                "trade_id": "live-legacy",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "p_posterior": 0.7,
                "outcome": 1,
                "pnl": 7.0,
                "settled_at": "2026-04-01T23:00:00Z",
            }
        ],
    }
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("settlement", "2026-04-01T23:00:00Z", "2026-04-01T23:00:00Z", json.dumps(paper_artifact), "2026-04-01T23:00:00Z", "paper"),
    )
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("settlement", "2026-04-01T23:00:00Z", "2026-04-01T23:00:00Z", json.dumps(live_artifact), "2026-04-01T23:00:00Z", "live"),
    )
    conn.commit()

    paper_rows = query_legacy_settlement_records(conn, limit=10, env="paper")
    live_rows = query_legacy_settlement_records(conn, limit=10, env="live")
    conn.close()

    assert [row["trade_id"] for row in paper_rows] == ["paper-legacy"]
    assert [row["trade_id"] for row in live_rows] == ["live-legacy"]


def test_query_execution_event_summary_groups_entry_and_exit_events(tmp_path):
    from src.state.db import log_position_event, query_execution_event_summary
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="exec-summary-1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        strategy="center_buy",
        edge_source="center_buy",
        env="paper",
    )
    log_position_event(conn, "ORDER_ATTEMPTED", pos, details={"status": "pending"}, source="execution")
    log_position_event(conn, "ORDER_FILLED", pos, details={"status": "filled"}, source="execution")
    log_position_event(conn, "EXIT_ORDER_ATTEMPTED", pos, details={"status": "placed"}, source="exit_lifecycle")
    log_position_event(conn, "EXIT_RETRY_SCHEDULED", pos, details={"status": "retry"}, source="exit_lifecycle")
    conn.commit()

    summary = query_execution_event_summary(conn, env="paper")
    conn.close()

    assert summary["event_sample_size"] == 4
    assert summary["overall"]["entry_attempted"] == 1
    assert summary["overall"]["entry_filled"] == 1
    assert summary["overall"]["exit_attempted"] == 1
    assert summary["overall"]["exit_retry_scheduled"] == 1
    assert summary["by_strategy"]["center_buy"]["entry_filled"] == 1


def test_query_no_trade_cases_filters_by_env(tmp_path):
    from src.state.decision_chain import query_no_trade_cases

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    paper_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "paper-1",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "rejection_stage": "EDGE_INSUFFICIENT",
                "rejection_reasons": ["small"],
            }
        ]
    }
    live_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "live-1",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "rejection_stage": "RISK_REJECTED",
                "rejection_reasons": ["risk"],
            }
        ]
    }
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("opening_hunt", now, now, json.dumps(paper_artifact), now, "paper"),
    )
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("opening_hunt", now, now, json.dumps(live_artifact), now, "live"),
    )
    conn.commit()

    paper_cases = query_no_trade_cases(conn, hours=24, env="paper")
    live_cases = query_no_trade_cases(conn, hours=24, env="live")
    conn.close()

    assert [case["decision_id"] for case in paper_cases] == ["paper-1"]
    assert [case["decision_id"] for case in live_cases] == ["live-1"]


def test_query_learning_surface_summary_combines_settlement_no_trade_and_execution(tmp_path):
    from src.state.db import log_position_event, log_settlement_event
    from src.state.decision_chain import query_learning_surface_summary
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            now,
            now,
            json.dumps(
                {
                    "no_trade_cases": [
                        {
                            "decision_id": "nt1",
                            "city": "NYC",
                            "target_date": "2026-04-01",
                            "range_label": "39-40°F",
                            "direction": "buy_yes",
                            "strategy": "center_buy",
                            "edge_source": "center_buy",
                            "rejection_stage": "EDGE_INSUFFICIENT",
                            "rejection_reasons": ["small"],
                        }
                    ]
                }
            ),
            now,
            "paper",
        ),
    )
    pos = Position(
        trade_id="learn-1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        strategy="center_buy",
        edge_source="center_buy",
        decision_snapshot_id="snap1",
        exit_price=1.0,
        pnl=5.0,
        exit_reason="SETTLEMENT",
        last_exit_at=now,
        state="settled",
        env="paper",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.7,
        edge=0.2,
    )
    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    log_position_event(conn, "ORDER_REJECTED", pos, details={"status": "rejected"}, source="execution")
    conn.commit()

    summary = query_learning_surface_summary(conn, env="paper")
    conn.close()

    assert summary["settlement_sample_size"] == 1
    assert summary["settlement_degraded_count"] == 0
    assert summary["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert summary["execution"]["overall"]["entry_rejected"] == 1
    assert summary["by_strategy"]["center_buy"]["settlement_count"] == 1
    assert summary["by_strategy"]["center_buy"]["no_trade_count"] == 1
    assert summary["by_strategy"]["center_buy"]["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert summary["by_strategy"]["center_buy"]["entry_rejected"] == 1


def test_query_no_trade_cases_filters_recent_rows_by_real_timestamp(monkeypatch, tmp_path):
    import src.state.decision_chain as decision_chain_module

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 2, 23, 30, tzinfo=timezone.utc)

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    older_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "older",
                "city": "NYC",
                "target_date": "2026-04-02",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "rejection_stage": "EDGE_INSUFFICIENT",
                "rejection_reasons": ["small"],
            }
        ]
    }
    newer_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "newer",
                "city": "NYC",
                "target_date": "2026-04-02",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "rejection_stage": "RISK_REJECTED",
                "rejection_reasons": ["risk"],
            }
        ]
    }
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            "2026-04-02T00:00:00+00:00",
            "2026-04-02T00:01:00+00:00",
            json.dumps(older_artifact),
            "2026-04-02T00:30:00+00:00",
            "paper",
        ),
    )
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            "2026-04-02T23:00:00+00:00",
            "2026-04-02T23:01:00+00:00",
            json.dumps(newer_artifact),
            "2026-04-02T23:15:00+00:00",
            "paper",
        ),
    )
    conn.commit()

    monkeypatch.setattr(decision_chain_module, "datetime", FrozenDatetime)
    cases = decision_chain_module.query_no_trade_cases(conn, hours=1, env="paper")
    conn.close()

    assert [case["decision_id"] for case in cases] == ["newer"]


def test_exit_lifecycle_event_helpers_emit_sell_side_events(tmp_path):
    from src.state.db import (
        log_exit_attempt_event,
        log_exit_fill_event,
        log_exit_retry_event,
        log_pending_exit_recovery_event,
        log_pending_exit_status_event,
        query_position_events,
    )
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-exit-events",
        market_id="m7",
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
        exit_reason="EDGE_REVERSAL",
        state="holding",
        exit_state="sell_pending",
        shares=25.0,
        last_exit_order_id="sell-1",
        exit_retry_count=2,
        next_exit_retry_at="2026-04-01T01:10:00Z",
        last_monitor_market_price=0.43,
    )

    log_exit_attempt_event(
        conn,
        pos,
        order_id="sell-1",
        status="placed",
        current_market_price=0.44,
        best_bid=0.43,
        shares=25.0,
    )
    log_pending_exit_status_event(conn, pos, status="OPEN")
    log_exit_retry_event(conn, pos, reason="SELL_REJECTED", error="REJECTED")
    log_pending_exit_recovery_event(
        conn,
        pos,
        event_type="EXIT_INTENT_RECOVERED",
        reason="STRANDED_EXIT_INTENT",
        error="exception_during_sell",
    )
    pos.last_exit_at = "2026-04-01T01:05:00Z"
    log_exit_fill_event(
        conn,
        pos,
        order_id="sell-1",
        fill_price=0.43,
        current_market_price=0.43,
        best_bid=0.43,
        timestamp=pos.last_exit_at,
    )
    conn.commit()

    events = query_position_events(conn, "rt-exit-events")
    conn.close()

    event_types = [event["event_type"] for event in events]
    assert "EXIT_ORDER_ATTEMPTED" in event_types
    assert "EXIT_FILL_CHECKED" in event_types
    assert "EXIT_RETRY_SCHEDULED" in event_types
    assert "EXIT_INTENT_RECOVERED" in event_types
    assert "EXIT_ORDER_FILLED" in event_types

    retry_event = next(event for event in events if event["event_type"] == "EXIT_RETRY_SCHEDULED")
    assert retry_event["details"]["error"] == "REJECTED"
    assert retry_event["details"]["retry_count"] == 2

    fill_event = next(event for event in events if event["event_type"] == "EXIT_ORDER_FILLED")
    assert fill_event["order_id"] == "sell-1"
    assert fill_event["details"]["fill_price"] == pytest.approx(0.43)


def test_log_exit_retry_event_uses_backoff_exhausted_type(tmp_path):
    from src.state.db import log_exit_retry_event, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-exit-backoff",
        market_id="m8",
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
        state="holding",
        exit_state="backoff_exhausted",
        exit_retry_count=10,
    )

    log_exit_retry_event(conn, pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
    conn.commit()

    events = query_position_events(conn, "rt-exit-backoff")
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "EXIT_BACKOFF_EXHAUSTED"
    assert events[0]["details"]["error"] == "3_consecutive_unknown"


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
