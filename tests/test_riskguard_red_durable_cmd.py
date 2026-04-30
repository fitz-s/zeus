# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: M1 antibodies for RED force-exit durable command proxy and NC-NEW-D function-scope ownership.
# Reuse: Run when cycle_runner RED sweep, venue command persistence, or riskguard actuation changes.
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M1.yaml
"""RED force-exit durable command proxy tests."""

from __future__ import annotations

import ast
import inspect
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
from src.engine import cycle_runner
from src.engine.cycle_runner import _execute_force_exit_sweep
from src.engine.discovery_mode import DiscoveryMode
from src.riskguard.risk_level import RiskLevel
from src.state.db import init_schema
from src.state.portfolio import PortfolioState, Position
from src.state.snapshot_repo import insert_snapshot
from src.state.venue_command_repo import get_command, list_events

NOW = datetime(2026, 4, 27, 13, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    insert_snapshot(conn, _snapshot())
    return conn


def _file_conn(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _snapshot(captured_at: datetime | None = None) -> ExecutableMarketSnapshotV2:
    captured = captured_at or NOW
    return ExecutableMarketSnapshotV2(
        snapshot_id="snap-red",
        gamma_market_id="gamma-red",
        event_id="event-red",
        event_slug="weather-red",
        condition_id="condition-red",
        question_id="question-red",
        yes_token_id="yes-red",
        no_token_id="no-red",
        selected_outcome_token_id="yes-red",
        outcome_label="YES",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=NOW + timedelta(hours=1),
        market_end_at=NOW + timedelta(days=1),
        market_close_at=NOW + timedelta(days=1, hours=1),
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        fee_details={"bps": 0},
        token_map_raw={"YES": "yes-red", "NO": "no-red"},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.49"),
        orderbook_top_ask=Decimal("0.51"),
        orderbook_depth_jsonb='{"asks":[["0.51","100"]],"bids":[["0.49","100"]]}',
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        captured_at=captured,
        freshness_deadline=captured + timedelta(seconds=30),
    )


def _position(**overrides) -> Position:
    payload = dict(
        trade_id="trade-red",
        market_id="condition-red",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        state="holding",
        exit_reason="",
        order_id="venue-order-red",
        decision_snapshot_id="snap-red",
        token_id="yes-red",
        no_token_id="no-red",
        condition_id="condition-red",
        shares=10.0,
        entry_price=0.50,
        last_monitor_best_bid=0.50,
    )
    payload.update(overrides)
    return Position(**payload)


def _red_command(conn):
    return conn.execute(
        "SELECT * FROM venue_commands WHERE intent_kind='CANCEL' AND decision_id LIKE 'red_force_exit_proxy:%'"
    ).fetchone()


def test_red_emits_cancel_command_within_same_cycle():
    conn = _conn()
    portfolio = PortfolioState(positions=[_position()])

    summary = _execute_force_exit_sweep(portfolio, conn=conn, now=NOW)

    assert summary["attempted"] == 1
    assert summary["cancel_commands_inserted"] == 1
    row = _red_command(conn)
    assert row is not None
    assert row["state"] == "CANCEL_PENDING"
    assert row["intent_kind"] == "CANCEL"
    assert row["venue_order_id"] == "venue-order-red"
    assert row["envelope_id"].startswith("pre-submit:red-cancel-")
    assert portfolio.positions[0].exit_reason == "red_force_exit"
    assert [event["event_type"] for event in list_events(conn, row["command_id"])] == [
        "INTENT_CREATED",
        "CANCEL_REQUESTED",
    ]


def test_red_emit_grammar_bound_to_cancel_or_derisk_only():
    conn = _conn()
    _execute_force_exit_sweep(PortfolioState(positions=[_position()]), conn=conn, now=NOW)

    row = _red_command(conn)
    assert row["intent_kind"] in {"CANCEL", "DERISK"}
    assert row["decision_id"].startswith("red_force_exit_proxy:")


def test_red_emit_satisfies_inv_30_persist_before_sdk():
    source = inspect.getsource(_execute_force_exit_sweep)
    assert "insert_command(" in source
    assert ".place_limit_order(" not in source
    assert ".cancel_order(" not in source


def test_red_emit_satisfies_nc_19_idempotency_lookup():
    source = inspect.getsource(_execute_force_exit_sweep)
    assert source.index("find_command_by_idempotency_key") < source.index("insert_command(")

    conn = _conn()
    pos = _position()
    first = _execute_force_exit_sweep(PortfolioState(positions=[pos]), conn=conn, now=NOW)
    pos.exit_reason = ""
    second = _execute_force_exit_sweep(PortfolioState(positions=[pos]), conn=conn, now=NOW)

    assert first["cancel_commands_inserted"] == 1
    assert second["cancel_commands_existing"] == 1
    assert conn.execute("SELECT COUNT(*) FROM venue_commands WHERE intent_kind='CANCEL'").fetchone()[0] == 1


def test_red_emit_passes_through_command_recovery():
    from src.execution.command_recovery import reconcile_unresolved_commands

    conn = _conn()
    _execute_force_exit_sweep(PortfolioState(positions=[_position()]), conn=conn, now=NOW)
    row = _red_command(conn)
    client = Mock()
    client.get_order.return_value = None

    result = reconcile_unresolved_commands(conn, client)

    assert result["advanced"] == 1
    assert get_command(conn, row["command_id"])["state"] == "CANCELLED"


def test_run_cycle_red_risk_level_triggers_durable_sweep(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus-red.db"
    conn = _file_conn(db_path)
    init_schema(conn)
    insert_snapshot(conn, _snapshot(datetime.now(timezone.utc)))
    conn.commit()
    conn.close()
    portfolio = PortfolioState(positions=[_position()])

    class DummyClob:
        def get_balance(self):
            return 100.0

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.RED)
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: _file_conn(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", lambda: DummyClob())
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: object())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: False)
    monkeypatch.setattr(
        cycle_runner,
        "_reconcile_pending_positions",
        lambda *args, **kwargs: {
            "entered": 0,
            "voided": 0,
            "dirty": False,
            "tracker_dirty": False,
        },
    )
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda *args, **kwargs: ({}, True))
    monkeypatch.setattr(cycle_runner, "_cleanup_orphan_open_orders", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        cycle_runner,
        "_entry_bankroll_for_cycle",
        lambda *args, **kwargs: (100.0, {"portfolio_initial_bankroll_usd": 100.0}),
    )
    monitor_seen = {}

    def _monitor_after_red_sweep(_conn, _clob, monitored_portfolio, *_args, **_kwargs):
        monitor_seen["exit_reason"] = monitored_portfolio.positions[0].exit_reason
        return False, False

    monkeypatch.setattr(cycle_runner, "_execute_monitoring_phase", _monitor_after_red_sweep)
    monkeypatch.setattr(
        cycle_runner,
        "_execute_discovery_phase",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("RED risk must block entries")
        ),
    )
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.execution.command_recovery.reconcile_unresolved_commands", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "src.control.heartbeat_supervisor.summary",
        lambda: {"health": "OK", "entry": {"allow_submit": True}},
    )
    monkeypatch.setattr(
        "src.control.ws_gap_guard.summary",
        lambda: {
            "subscription_state": "CONNECTED",
            "gap_reason": "",
            "m5_reconcile_required": False,
            "entry": {"allow_submit": True},
        },
    )
    monkeypatch.setattr(
        "src.risk_allocator.refresh_global_allocator",
        lambda *args, **kwargs: {"entry": {"allow_submit": True}},
    )
    monkeypatch.setattr(
        cycle_runner.cutover_guard,
        "summary",
        lambda: {"state": "NORMAL", "entry": {"allow_submit": True}},
    )
    monkeypatch.setattr("src.runtime.posture.read_runtime_posture", lambda: "NORMAL")
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda *args, **kwargs: None)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["risk_level"] == RiskLevel.RED.value
    assert summary["force_exit_review_scope"] == "sweep_active_positions"
    assert summary["force_exit_sweep_trigger"] == "risk_level_red"
    assert summary["force_exit_sweep"]["attempted"] == 1
    assert summary["force_exit_sweep"]["cancel_commands_inserted"] == 1
    assert summary["entries_blocked_reason"] == "risk_level=RED"
    assert portfolio.positions[0].exit_reason == "red_force_exit"
    assert monitor_seen["exit_reason"] == "red_force_exit"

    conn = _file_conn(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE intent_kind='CANCEL' "
            "AND decision_id LIKE 'red_force_exit_proxy:%'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_red_emit_sole_caller_is_cycle_runner_force_exit_block():
    tree = ast.parse((ROOT / "src/engine/cycle_runner.py").read_text())
    owners = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                if isinstance(func, ast.Name) and func.id == "insert_command":
                    text = ast.get_source_segment((ROOT / "src/engine/cycle_runner.py").read_text(), child) or ""
                    if "red_force_exit_proxy" in text or "IntentKind.CANCEL" in text:
                        owners.append(node.name)
    assert owners == ["_execute_force_exit_sweep"]


def test_riskguard_does_NOT_call_insert_command_directly():
    riskguard_source = (ROOT / "src/riskguard/riskguard.py").read_text()
    assert "insert_command" not in riskguard_source
