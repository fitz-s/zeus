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
from src.engine.cycle_runner import _execute_force_exit_sweep
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


def _snapshot() -> ExecutableMarketSnapshotV2:
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
        captured_at=NOW,
        freshness_deadline=NOW + timedelta(seconds=30),
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
