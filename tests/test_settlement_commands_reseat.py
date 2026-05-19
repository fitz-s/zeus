# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: fix/redeem-reseat-stub-deferred plan; post-PR-#183 autonomous redeem path
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody tests for reseat_stub_deferred_rows_for_autonomous_retry — state-guard race, truthy env parity, idempotency.
# Reuse: Inspect settlement_commands.py reseat function and SettlementState enum before running.
"""Antibody tests for reseat_stub_deferred_rows_for_autonomous_retry.

Sed-break meta-verify: removing the `autonomous_enabled` check causes
test_reseat_no_op_when_autonomous_disabled to fail (promoted=1 instead of 0).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.execution.settlement_commands import (
    SettlementState,
    reseat_stub_deferred_rows_for_autonomous_retry,
)
from src.state.db import init_schema


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


def _insert_operator_required(conn: sqlite3.Connection, command_id: str, error_code: str | None) -> None:
    error_payload = json.dumps({"errorCode": error_code}) if error_code else None
    conn.execute(
        """
        INSERT INTO settlement_commands
          (command_id, state, condition_id, market_id, payout_asset, requested_at, error_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            SettlementState.REDEEM_OPERATOR_REQUIRED.value,
            "0xcond1",
            "0xmarket1",
            "USDC",
            "2026-05-19T00:00:00Z",
            error_payload,
        ),
    )
    conn.commit()


def test_reseat_promotes_stub_deferred_to_retrying_when_autonomous_enabled(conn, monkeypatch):
    """Rows with errorCode=REDEEM_DEFERRED_TO_R1 are promoted to RETRYING when env is set."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    _insert_operator_required(conn, "cmd-001", "REDEEM_DEFERRED_TO_R1")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 1
    row = conn.execute(
        "SELECT state, terminal_at FROM settlement_commands WHERE command_id = ?",
        ("cmd-001",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_RETRYING.value
    assert row["terminal_at"] is None


def test_reseat_no_op_when_autonomous_disabled(conn, monkeypatch):
    """Without ZEUS_AUTONOMOUS_REDEEM_ENABLED, row stays in OPERATOR_REQUIRED and returns 0."""
    monkeypatch.delenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", raising=False)
    _insert_operator_required(conn, "cmd-002", "REDEEM_DEFERRED_TO_R1")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 0
    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("cmd-002",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value


def test_reseat_skips_non_stub_operator_required(conn, monkeypatch):
    """Rows with a different errorCode are not promoted even when autonomous is enabled."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    _insert_operator_required(conn, "cmd-003", "REDEEM_SAFE_VERSION_UNSUPPORTED")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 0
    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("cmd-003",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value


def test_reseat_handles_malformed_error_payload(conn, monkeypatch):
    """Rows with empty, null, or invalid JSON error_payload are skipped gracefully."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")

    # null payload
    _insert_operator_required(conn, "cmd-004", None)
    # empty string payload — insert directly to bypass helper
    conn.execute(
        """
        INSERT INTO settlement_commands
          (command_id, state, condition_id, market_id, payout_asset, requested_at, error_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-005",
            SettlementState.REDEEM_OPERATOR_REQUIRED.value,
            "0xcond2",
            "0xmarket2",
            "USDC",
            "2026-05-19T00:00:01Z",
            "not-valid-json",
        ),
    )
    conn.commit()

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 0
    for cid in ("cmd-004", "cmd-005"):
        row = conn.execute(
            "SELECT state FROM settlement_commands WHERE command_id = ?",
            (cid,),
        ).fetchone()
        assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value


def test_reseat_idempotent(conn, monkeypatch):
    """Calling twice promotes 0 on second call (row already in RETRYING)."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    _insert_operator_required(conn, "cmd-006", "REDEEM_DEFERRED_TO_R1")

    first = reseat_stub_deferred_rows_for_autonomous_retry(conn)
    conn.commit()
    second = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert first == 1
    assert second == 0


def test_reseat_state_guard_skips_concurrent_transition(conn, monkeypatch):
    """State guard prevents clobbering a row that transitioned out of OPERATOR_REQUIRED
    between the SELECT and UPDATE (e.g. operator CLI set it to REDEEM_TX_HASHED)."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    _insert_operator_required(conn, "cmd-007", "REDEEM_DEFERRED_TO_R1")
    # Simulate concurrent operator transition before reseat runs its UPDATE.
    conn.execute(
        "UPDATE settlement_commands SET state = ? WHERE command_id = ?",
        (SettlementState.REDEEM_TX_HASHED.value, "cmd-007"),
    )
    conn.commit()

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 0
    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("cmd-007",),
    ).fetchone()
    # Row must remain in TX_HASHED — not clobbered back to RETRYING.
    assert row["state"] == SettlementState.REDEEM_TX_HASHED.value


def test_reseat_truthy_env_on_value(conn, monkeypatch):
    """ZEUS_AUTONOMOUS_REDEEM_ENABLED=on is accepted (parity with polymarket_v2_adapter)."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "on")
    _insert_operator_required(conn, "cmd-008", "REDEEM_DEFERRED_TO_R1")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)

    assert promoted == 1


def test_reseat_appends_event_on_promotion(conn, monkeypatch):
    """A stub_deferred_reseat_autonomous event is appended when a row is promoted."""
    monkeypatch.setenv("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1")
    _insert_operator_required(conn, "cmd-009", "REDEEM_DEFERRED_TO_R1")

    promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)
    conn.commit()

    assert promoted == 1
    events = conn.execute(
        "SELECT event_type, payload_json FROM settlement_command_events WHERE command_id = ?",
        ("cmd-009",),
    ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == SettlementState.REDEEM_RETRYING.value
    payload = json.loads(events[0]["payload_json"])
    assert payload.get("reason") == "stub_deferred_reseat_autonomous"
