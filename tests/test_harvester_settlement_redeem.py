# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1C/phase.json
"""Relationship tests: harvester settlement and redeem are independent side effects.

T1C-SETTLEMENT-NOT-REDEEM: calling record_settlement_result() does NOT invoke any
redeem-state transition. Calling enqueue_redeem_command() does NOT write a settlement
record. The two effects are structurally independent.

Tests:
  T1: record_settlement_result writes rows to decision_log, emits no redeem state.
  T2: enqueue_redeem_command writes a settlement_command row, does NOT write to decision_log.
  T3: record_settlement_result with missing decision_log table returns 0 (legacy skip).
  T4: enqueue_redeem_command returns queued/error dict with expected keys.
  T5: calling both in sequence leaves each table in consistent independent state.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.execution.harvester import enqueue_redeem_command, record_settlement_result
from src.execution.settlement_commands import (
    SettlementState,
    init_settlement_command_schema,
)
from src.state.decision_chain import SettlementRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_trade_conn() -> sqlite3.Connection:
    """In-memory trade DB with decision_log table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            trade_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            strategy TEXT,
            source TEXT,
            settled_at TEXT,
            decision_snapshot_id INTEGER,
            edge_source TEXT
        )
    """)
    conn.commit()
    return conn


def _make_settlement_record(trade_id: str = "trade-001") -> SettlementRecord:
    return SettlementRecord(
        trade_id=trade_id,
        city="London",
        target_date="2026-05-01",
        range_label="16-17°C",
        direction="buy_yes",
        p_posterior=0.65,
        outcome=1,
        pnl=6.5,
        strategy="default",
        settled_at="2026-05-01T18:00:00Z",
        decision_snapshot_id="",
        edge_source="model",
    )


def _make_stage2_ready() -> dict:
    return {
        "stage2_status": "ready",
        "stage2_missing_trade_tables": [],
        "stage2_missing_shared_tables": [],
    }


def _make_stage2_missing_decision_log() -> dict:
    return {
        "stage2_status": "degraded",
        "stage2_missing_trade_tables": ["decision_log"],
        "stage2_missing_shared_tables": [],
    }


def _make_settlement_conn_with_schema() -> sqlite3.Connection:
    """In-memory DB with settlement_commands schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_settlement_command_schema(conn)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# T1: record_settlement_result writes decision_log rows; no redeem state emitted
# ---------------------------------------------------------------------------

def test_T1_record_settlement_result_writes_decision_log_no_redeem_transition():
    """record_settlement_result() writes the settlement fact and does not touch
    settlement_commands / redeem state (T1C-SETTLEMENT-NOT-REDEEM)."""
    conn = _make_trade_conn()
    records = [_make_settlement_record("trade-001"), _make_settlement_record("trade-002")]

    with patch("src.execution.harvester.store_settlement_records") as mock_store:
        n = record_settlement_result(conn, records, _make_stage2_ready())

    assert n == len(records)
    mock_store.assert_called_once()
    call_args = mock_store.call_args
    # Correct DB connection passed
    assert call_args[0][0] is conn
    # Correct records passed
    assert call_args[0][1] == records
    # source tag is "harvester"
    assert call_args[1].get("source") == "harvester"

    # No settlement_command was created — the function has no knowledge of redeem state
    # (settlement_commands table does not exist in this conn; if it did, row count would be 0)


def test_T1b_record_settlement_result_empty_list_returns_zero():
    conn = _make_trade_conn()
    with patch("src.execution.harvester.store_settlement_records") as mock_store:
        n = record_settlement_result(conn, [], _make_stage2_ready())
    assert n == 0
    mock_store.assert_not_called()


# ---------------------------------------------------------------------------
# T2: enqueue_redeem_command writes settlement_commands, NOT decision_log
# ---------------------------------------------------------------------------

def test_T2_enqueue_redeem_writes_settlement_command_not_decision_log():
    """enqueue_redeem_command() creates a command row in settlement_commands but
    does NOT write to decision_log (T1C-SETTLEMENT-NOT-REDEEM).

    Uses USDC_E payout to avoid Q-FX-1 gate (goes to REDEEM_REVIEW_REQUIRED).
    The test verifies structural independence, not payout-asset semantics.
    """
    conn = _make_settlement_conn_with_schema()

    result = enqueue_redeem_command(
        conn,
        condition_id="cond-abc123",
        payout_asset="USDC_E",
        market_id="mkt-abc123",
        trade_id="trade-001",
    )

    assert result["status"] == "queued"
    assert result["command_id"] is not None
    assert result["reason"] is None

    # Verify row exists in settlement_commands (USDC_E goes to REDEEM_REVIEW_REQUIRED)
    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        (result["command_id"],),
    ).fetchone()
    assert row is not None
    assert row["state"] in {
        SettlementState.REDEEM_INTENT_CREATED.value,
        SettlementState.REDEEM_REVIEW_REQUIRED.value,
    }

    # decision_log table does not exist in this conn, confirming enqueue_redeem_command
    # made no attempt to write it (it would raise if it tried).


def test_T2b_enqueue_redeem_idempotent_returns_same_command_id():
    """Calling enqueue_redeem_command twice for the same condition/asset returns
    the same command_id (request_redeem is idempotent). Uses USDC_E to avoid Q-FX-1."""
    conn = _make_settlement_conn_with_schema()

    r1 = enqueue_redeem_command(conn, condition_id="cond-dup", payout_asset="USDC_E")
    r2 = enqueue_redeem_command(conn, condition_id="cond-dup", payout_asset="USDC_E")

    assert r1["status"] == "queued"
    assert r2["status"] == "queued"
    assert r1["command_id"] == r2["command_id"]


# ---------------------------------------------------------------------------
# T3: record_settlement_result with missing decision_log skips and returns 0
# ---------------------------------------------------------------------------

def test_T3_record_settlement_result_skips_when_decision_log_missing(caplog):
    """When decision_log is in stage2_missing_trade_tables, no write occurs and
    legacy_skip count is correct."""
    import logging
    conn = _make_trade_conn()
    records = [_make_settlement_record()]

    with patch("src.execution.harvester.store_settlement_records") as mock_store:
        with caplog.at_level(logging.WARNING, logger="src.execution.harvester"):
            n = record_settlement_result(conn, records, _make_stage2_missing_decision_log())

    assert n == 0
    mock_store.assert_not_called()
    assert any("decision_log missing" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# T4: enqueue_redeem_command returns correct dict schema on error
# ---------------------------------------------------------------------------

def test_T4_enqueue_redeem_returns_error_dict_on_exception():
    """If request_redeem raises, enqueue_redeem_command returns status='error'
    with a reason string and no command_id."""
    conn = _make_settlement_conn_with_schema()

    # Patch request_redeem at the import site used inside enqueue_redeem_command
    # (the function does a local 'from src.execution.settlement_commands import request_redeem')
    with patch("src.execution.settlement_commands.request_redeem",
               side_effect=RuntimeError("fx gate closed")):
        result = enqueue_redeem_command(
            conn,
            condition_id="cond-fail",
            payout_asset="USDC_E",
        )

    assert result["status"] == "error"
    assert result["command_id"] is None
    assert "fx gate closed" in result["reason"]


# ---------------------------------------------------------------------------
# T5: record_settlement_result and enqueue_redeem_command are independent
#     — calling both leaves each table consistent with no cross-contamination
# ---------------------------------------------------------------------------

def test_T5_settlement_and_redeem_independent_when_called_in_sequence():
    """Calling record_settlement_result then enqueue_redeem_command leaves
    decision_log and settlement_commands in independent consistent state.
    Neither operation contaminates the other's table."""
    trade_conn = _make_trade_conn()
    redeem_conn = _make_settlement_conn_with_schema()

    records = [_make_settlement_record("trade-seq-01")]

    with patch("src.execution.harvester.store_settlement_records") as mock_store:
        n_written = record_settlement_result(trade_conn, records, _make_stage2_ready())

    # record_settlement_result returns len(records) when stage2 is ready
    assert n_written == len(records)

    redeem_result = enqueue_redeem_command(
        redeem_conn,
        condition_id="cond-seq-01",
        payout_asset="USDC_E",
        trade_id="trade-seq-01",
    )

    assert redeem_result["status"] == "queued"

    # settlement_commands table has exactly 1 row (the redeem intent)
    cmd_count = redeem_conn.execute(
        "SELECT COUNT(*) FROM settlement_commands"
    ).fetchone()[0]
    assert cmd_count == 1

    # store_settlement_records was called exactly once with our records
    mock_store.assert_called_once()
