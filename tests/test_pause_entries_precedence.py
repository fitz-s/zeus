# Created: 2026-05-18
# Last reused/audited: 2026-05-18
# Authority basis: RESTART_READINESS_PLAN.md §3 PRECEDENCE-1; JOB fda4e853 audit_2026_05_17
"""Antibody tests for PRECEDENCE-1: pause_entries operator precedence guard.

Tests verify that system_auto_pause cannot overwrite an operator indefinite
freeze, that resume_entries is callable by control_plane/operator, and that
the precedence skip emits the required log warning (Option C: log-only audit).

All tests run through the TI-1 autouse redirect fixture so no live DB is
touched; writes go to per-test tmp mirrors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

import src.control.control_plane as cp
from src.control.control_plane import AUTO_PAUSE_OVERRIDE_ID
from src.state.db import (
    DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    apply_architecture_kernel_schema,
    get_world_connection,
    query_control_override_state,
    upsert_control_override,
)


@pytest.fixture(autouse=True)
def _bootstrap_world_schema():
    """Apply the full schema to the per-test world DB mirror before each test.

    The TI-1 autouse fixture (_ti1_redirect_live_db) redirects get_world_connection()
    to a per-test tmp path. That empty file has no tables until we apply the schema.
    """
    conn = get_world_connection()
    apply_architecture_kernel_schema(conn)
    conn.commit()
    conn.close()


def _seed_operator_row(conn) -> None:
    """Insert an indefinite operator freeze row (issued_by='control_plane')."""
    now_iso = datetime.now(timezone.utc).isoformat()
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="control_plane",
        issued_at=now_iso,
        reason="manual operator pause",
        effective_until=None,  # indefinite
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Test 1: auto-pause skips when operator indefinite row is active
# ---------------------------------------------------------------------------

def test_auto_pause_skips_when_operator_indefinite_active():
    """system_auto_pause must NOT insert a new upsert row when operator freeze is active.

    Option C: no audit row is written; the skip is log-only. Verify:
    1. history count is unchanged after pause_entries call.
    2. query_control_override_state still shows the operator row as active.
    3. cp._control_state['entries_paused'] is True (in-memory still set).
    """
    conn = get_world_connection()
    _seed_operator_row(conn)

    # Count history rows BEFORE the auto-pause attempt
    count_before = conn.execute(
        "SELECT COUNT(*) FROM control_overrides_history WHERE override_id=?",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()[0]

    # Attempt auto-pause — should be suppressed by PRECEDENCE-1 guard
    cp.pause_entries("auto_pause:ValueError", issued_by="system_auto_pause")

    # Count AFTER — must be unchanged (no new row written)
    count_after = conn.execute(
        "SELECT COUNT(*) FROM control_overrides_history WHERE override_id=?",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()[0]
    assert count_after == count_before, (
        f"PRECEDENCE-1 FAIL: expected no new history row (count={count_before}), "
        f"got {count_after} rows after auto-pause attempt"
    )

    # Operator row still projected as active via VIEW
    state = query_control_override_state(conn)
    assert state["entries_paused"] is True
    assert state["entries_pause_source"] == "manual_command"

    # In-memory state also paused (in-memory set happens before DB check)
    assert cp._control_state["entries_paused"] is True

    conn.close()


# ---------------------------------------------------------------------------
# Test 2: auto-pause writes when no operator row is active
# ---------------------------------------------------------------------------

def test_auto_pause_writes_when_no_operator_row_active():
    """When no operator row exists, pause_entries must write a new upsert row."""
    conn = get_world_connection()

    count_before = conn.execute(
        "SELECT COUNT(*) FROM control_overrides_history WHERE override_id=?",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()[0]

    cp.pause_entries("auto_pause:Timeout", issued_by="system_auto_pause")

    rows = conn.execute(
        "SELECT issued_by, operation FROM control_overrides_history WHERE override_id=? ORDER BY history_id DESC LIMIT 1",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()
    assert rows is not None, "Expected a new history row after auto-pause"
    assert rows["issued_by"] == "system_auto_pause"
    assert rows["operation"] == "upsert"

    count_after = conn.execute(
        "SELECT COUNT(*) FROM control_overrides_history WHERE override_id=?",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()[0]
    assert count_after == count_before + 1

    conn.close()


# ---------------------------------------------------------------------------
# Test 3: resume_entries clears operator row
# ---------------------------------------------------------------------------

def test_resume_entries_clears_operator_row():
    """cp.resume_entries should expire the operator freeze row."""
    conn = get_world_connection()
    _seed_operator_row(conn)

    # Confirm paused before resume
    state_before = query_control_override_state(conn)
    assert state_before["entries_paused"] is True
    conn.close()

    # Call public resume
    cp.resume_entries("test_clear", issued_by="control_plane")

    # Confirm expired after resume
    conn2 = get_world_connection()
    state_after = query_control_override_state(conn2)
    assert state_after["entries_paused"] is False, (
        f"Expected entries_paused=False after resume, got {state_after}"
    )

    # At least one 'expire' row should now exist in history
    expire_count = conn2.execute(
        "SELECT COUNT(*) FROM control_overrides_history WHERE override_id=? AND operation='expire'",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()[0]
    assert expire_count >= 1

    conn2.close()


# ---------------------------------------------------------------------------
# Test 4: resume_entries rejects non-operator caller
# ---------------------------------------------------------------------------

def test_resume_entries_rejects_non_operator_caller():
    """resume_entries must raise ValueError when called with system_auto_pause."""
    with pytest.raises(ValueError, match="resume_entries requires issued_by"):
        cp.resume_entries("x", issued_by="system_auto_pause")


# ---------------------------------------------------------------------------
# Test 5: operator can override a system_auto_pause row
# ---------------------------------------------------------------------------

def test_operator_can_override_system_auto_pause_row():
    """An operator _apply_command('pause_entries') must win over an auto_pause row.

    Operator authority is absolute — no precedence restriction in the operator
    direction.
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    conn = get_world_connection()

    # Seed a system_auto_pause row with 15-min expiry
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="system_auto_pause",
        issued_at=now.isoformat(),
        reason="auto_pause:ValueError",
        effective_until=(now + timedelta(minutes=15)).isoformat(),
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()
    conn.close()

    # Operator issues an indefinite pause via _apply_command
    ok, err = cp._apply_command("pause_entries", {"issued_by": "control_plane", "effective_until": None})
    assert ok, f"_apply_command failed: {err}"

    # Verify latest row is now operator-issued
    conn2 = get_world_connection()
    row = conn2.execute(
        "SELECT issued_by, effective_until, operation FROM control_overrides_history "
        "WHERE override_id=? ORDER BY history_id DESC LIMIT 1",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()
    assert row["issued_by"] == "control_plane"
    assert row["effective_until"] is None  # indefinite
    assert row["operation"] == "upsert"
    conn2.close()


# ---------------------------------------------------------------------------
# Test 6 (new, Option C): precedence skip emits warning log
# ---------------------------------------------------------------------------

def test_precedence_skip_logs_warning(caplog):
    """When system_auto_pause attempts to overwrite an operator indefinite row,
    a PRECEDENCE_SKIP warning must be logged (Option C: log-only audit)."""
    conn = get_world_connection()
    _seed_operator_row(conn)
    conn.close()

    with caplog.at_level(logging.WARNING, logger="src.control.control_plane"):
        cp.pause_entries("test_reason", issued_by="system_auto_pause")

    warning_found = any(
        "PRECEDENCE_SKIP_AUTO_PAUSE_OVER_OPERATOR_FREEZE" in r.getMessage()
        for r in caplog.records
    )
    assert warning_found, (
        "Expected PRECEDENCE_SKIP_AUTO_PAUSE_OVER_OPERATOR_FREEZE in log records. "
        f"Got: {[r.getMessage() for r in caplog.records]}"
    )

    # Verify DB unchanged: no new upsert row from the auto-pause attempt
    conn2 = get_world_connection()
    latest = conn2.execute(
        "SELECT issued_by, operation FROM control_overrides_history "
        "WHERE override_id=? ORDER BY history_id DESC LIMIT 1",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()
    assert latest["issued_by"] == "control_plane", (
        f"Expected operator row still latest, got issued_by={latest['issued_by']!r}"
    )
    assert latest["operation"] == "upsert"
    conn2.close()
