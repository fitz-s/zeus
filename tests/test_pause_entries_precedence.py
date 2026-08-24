# Created: 2026-05-18
# Last reused/audited: 2026-08-21
# Authority basis: active finite-evidence plan restart-guard SCOPE/DRAIN/RESET; PRECEDENCE-1
"""Antibody tests for PRECEDENCE-1: pause_entries operator precedence guard.

Tests verify that system_auto_pause cannot overwrite an operator indefinite
freeze, that resume_entries is callable by control_plane/operator, and that
the precedence skip emits the required log warning (Option C: log-only audit).

All tests run through the TI-1 autouse redirect fixture so no live DB is
touched; writes go to per-test tmp mirrors.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import src.control.control_plane as cp
from src.control.control_plane import AUTO_PAUSE_OVERRIDE_ID
from src.events.event_store import EventStore
from src.events.opportunity_event import make_opportunity_event
from src.state.db import (
    DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    apply_architecture_kernel_schema,
    get_world_connection,
    init_schema,
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

    # PRECEDENCE-1 in-memory restore: after skip, _control_state source/reason
    # must reflect the operator freeze, NOT the attempted auto-pause values.
    # If refresh_control_state() is removed from the skip path, entries_pause_source
    # will be "auto_exception" and this assertion fails.
    assert cp._control_state.get("entries_pause_source") == "manual_command", (
        f"PRECEDENCE-1 FAIL: in-memory entries_pause_source was "
        f"{cp._control_state.get('entries_pause_source')!r}; expected 'manual_command'. "
        "refresh_control_state() must be called in the precedence-skip path to restore "
        "operator source/reason after the pre-skip mutation at L284-286."
    )

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
    expected_issued_at = state_before["entries_pause_issued_at"]
    conn.close()

    # Call public resume
    cp.resume_entries(
        "test_clear",
        issued_by="control_plane",
        expected_override_issued_at=expected_issued_at,
        expected_override_reason="manual operator pause",
        expected_override_issued_by="control_plane",
    )

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
        cp.resume_entries(
            "x",
            issued_by="system_auto_pause",
            expected_override_issued_at="2026-08-12T00:00:00+00:00",
        )


def test_resume_entries_requires_cas_for_operator_pause():
    conn = get_world_connection()
    _seed_operator_row(conn)
    conn.close()

    with pytest.raises(ValueError, match="requires expected_override_issued_at"):
        cp.resume_entries("stale_legacy_resume", issued_by="control_plane")

    conn = get_world_connection()
    assert query_control_override_state(conn)["entries_paused"] is True
    conn.close()


def test_resume_entries_rejects_issued_at_only_legacy_deploy_release():
    conn = get_world_connection()
    _seed_operator_row(conn)
    state = query_control_override_state(conn)
    conn.close()

    with pytest.raises(ValueError, match="expected_override_reason"):
        cp.resume_entries(
            "legacy_deploy_release",
            issued_by="control_plane",
            expected_override_issued_at=state["entries_pause_issued_at"],
        )

    conn = get_world_connection()
    preserved = query_control_override_state(conn)
    conn.close()
    assert preserved["entries_paused"] is True
    assert preserved["entries_pause_reason"] == "manual operator pause"


def test_resume_entries_preserves_newer_pause_after_stale_observation():
    conn = get_world_connection()
    _seed_operator_row(conn)
    stale_issued_at = query_control_override_state(conn)["entries_pause_issued_at"]
    newer_issued_at = datetime.now(timezone.utc).isoformat()
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="control_plane",
        issued_at=newer_issued_at,
        reason="newer operator pause",
        effective_until=None,
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="resume_entries override changed"):
        cp.resume_entries(
            "stale_resume",
            issued_by="control_plane",
            expected_override_issued_at=stale_issued_at,
            expected_override_reason="manual operator pause",
            expected_override_issued_by="control_plane",
        )

    conn = get_world_connection()
    state = query_control_override_state(conn)
    assert state["entries_paused"] is True
    assert state["entries_pause_reason"] == "newer operator pause"
    conn.close()


def test_entries_pause_read_is_db_authoritative_when_memory_is_stale_false():
    """Submit gating must not miss an active durable pause because memory is stale."""
    conn = get_world_connection()
    _seed_operator_row(conn)
    conn.close()

    cp._control_state["entries_paused"] = False
    cp._control_state["entries_pause_source"] = None
    cp._control_state["entries_pause_reason"] = None

    assert cp.is_entries_paused() is True
    assert cp.get_entries_pause_source() == "manual_command"
    assert cp.get_entries_pause_reason() == "manual operator pause"
    evidence = cp.get_entries_pause_evidence()
    assert evidence["source"] == "manual_command"
    assert evidence["reason"] == "manual operator pause"
    assert evidence["issued_by"] == "control_plane"
    assert evidence["issued_at"]
    assert evidence["effective_until"] is None


def test_codex_containment_is_not_reported_as_operator_command():
    """A Codex-imposed pause must remain attributable to Codex, not the user."""
    conn = get_world_connection()
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="control_plane",
        issued_at=datetime.now(timezone.utc).isoformat(),
        reason="codex_live_money_containment_after_bad_orders",
        effective_until=None,
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()

    state = query_control_override_state(conn)
    conn.close()

    assert state["entries_paused"] is True
    assert state["entries_pause_source"] == "codex_containment"
    assert state["entries_pause_reason"] == "codex_live_money_containment_after_bad_orders"
    assert state["entries_pause_issued_by"] == "control_plane"


def test_entries_pause_read_clears_stale_memory_when_db_unpaused():
    """A stale in-memory pause must not outlive the durable DB view."""
    now = datetime.now(timezone.utc)
    conn = get_world_connection()
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="system_auto_pause",
        issued_at=(now - timedelta(minutes=30)).isoformat(),
        reason="auto_pause:expired",
        effective_until=(now - timedelta(minutes=15)).isoformat(),
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()
    conn.close()

    cp._control_state["entries_paused"] = True
    cp._control_state["entries_pause_source"] = "auto_exception"
    cp._control_state["entries_pause_reason"] = "auto_pause:expired"

    assert cp.is_entries_paused() is False
    assert cp.get_entries_pause_source() is None
    assert cp.get_entries_pause_reason() is None
    evidence = cp.get_entries_pause_evidence()
    assert evidence == {
        "issued_at": None,
        "effective_until": None,
        "issued_by": None,
        "source": None,
        "reason": None,
    }


def test_live_submit_pause_gates_ignore_trade_legacy_archived_control_override():
    """Executor/EDLI gates must not consume the legacy trade DB control ghost.

    ``control_overrides`` in zeus_trades.db is registered as legacy_archived.
    A stale row there must not override the world control plane after resume.
    """
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.row_factory = sqlite3.Row
    trade_conn.execute(
        """
        CREATE TABLE control_overrides (
            override_id TEXT,
            target_type TEXT,
            target_key TEXT,
            action_type TEXT,
            value TEXT,
            issued_by TEXT,
            issued_at TEXT,
            effective_until TEXT,
            reason TEXT,
            precedence INTEGER
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO control_overrides (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence
        ) VALUES (
            'control_plane:global:entries_paused', 'global', 'entries',
            'gate', 'true', 'codex',
            '2026-06-25T15:44:10.786360+00:00', NULL,
            'operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix', 100
        )
        """
    )
    trade_conn.commit()

    from src.engine.event_reactor_adapter import _entry_pause_blocks_live_submit
    from src.execution.executor import _entry_control_pause_component

    try:
        component = _entry_control_pause_component(trade_conn)
        assert component["allowed"] is True
        assert component["authority_schema"] == "world"
        assert _entry_pause_blocks_live_submit(trade_conn) is None
    finally:
        trade_conn.close()


def test_live_submit_pause_gate_refreshes_a_stale_inline_pause(monkeypatch):
    """An expired pause in a pinned transaction cannot veto current entry truth."""

    import src.state.db as state_db
    from src.engine.event_reactor_adapter import _entry_pause_blocks_live_submit

    inline = object()

    class FreshWorld:
        def close(self):
            pass

    fresh = FreshWorld()
    seen = []

    def _state(conn):
        seen.append(conn)
        if conn is inline:
            return {
                "status": "ok",
                "entries_paused": True,
                "entries_pause_reason": "deploy_live_restart_guard",
                "entries_pause_issued_by": "control_plane",
            }
        assert conn is fresh
        return {
            "status": "ok",
            "entries_paused": False,
            "entries_pause_reason": None,
            "entries_pause_issued_by": None,
        }

    monkeypatch.setattr(state_db, "get_world_connection", lambda: fresh)
    monkeypatch.setattr(state_db, "query_control_override_state", _state)

    assert _entry_pause_blocks_live_submit(inline) is None
    assert seen == [inline, fresh]


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


def test_lower_precedence_resume_cannot_clear_stronger_pause():
    """A later routine resume must not pierce a stronger live-money pause."""

    conn = get_world_connection()
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="control_plane",
        issued_at=datetime.now(timezone.utc).isoformat(),
        reason="forward_capital_proof_required",
        effective_until=None,
        precedence=1000,
    )
    conn.commit()
    conn.close()

    ok, reason = cp._apply_command("resume", {"precedence": 100})

    assert ok is True
    assert reason.startswith("ignored_lower_precedence:")
    conn = get_world_connection()
    assert query_control_override_state(conn)["entries_paused"] is True
    latest = conn.execute(
        "SELECT operation, precedence FROM control_overrides_history "
        "WHERE override_id=? ORDER BY history_id DESC LIMIT 1",
        (AUTO_PAUSE_OVERRIDE_ID,),
    ).fetchone()
    assert latest["operation"] == "upsert"
    assert latest["precedence"] == 1000
    conn.close()


def test_lower_precedence_strategy_enable_cannot_clear_stronger_gate():
    """A routine enable must not replace a stronger disabled-strategy gate."""

    strategy = "forecast_qkernel_entry"
    override_id = f"control_plane:strategy:{strategy}:gate"
    conn = get_world_connection()
    upsert_control_override(
        conn,
        override_id=override_id,
        target_type="strategy",
        target_key=strategy,
        action_type="gate",
        value="true",
        issued_by="operator",
        issued_at=datetime.now(timezone.utc).isoformat(),
        reason="forward_capital_proof_required",
        effective_until=None,
        precedence=1000,
    )
    conn.commit()
    conn.close()

    ok, reason = cp._apply_command(
        "set_strategy_gate",
        {"strategy": strategy, "enabled": True, "precedence": 100},
    )

    assert ok is True
    assert reason.startswith("ignored_lower_precedence:")
    conn = get_world_connection()
    state = query_control_override_state(conn)
    assert state["strategy_gates"][strategy]["enabled"] is False
    latest = conn.execute(
        "SELECT value, precedence FROM control_overrides_history "
        "WHERE override_id=? ORDER BY history_id DESC LIMIT 1",
        (override_id,),
    ).fetchone()
    assert latest["value"] == "true"
    assert latest["precedence"] == 1000
    conn.close()


def test_equal_precedence_resume_requires_current_override_cas():
    """A delayed same-authority resume cannot clear a newer max-priority pause."""

    issued_at = datetime.now(timezone.utc).isoformat()
    conn = get_world_connection()
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="control_plane",
        issued_at=issued_at,
        reason="forward_capital_proof_required",
        effective_until=None,
        precedence=1000,
    )
    conn.commit()
    conn.close()

    ok, reason = cp._apply_command("resume", {"precedence": 1000})
    assert ok is True
    assert reason.startswith("ignored_equal_precedence_without_cas:")

    conn = get_world_connection()
    assert query_control_override_state(conn)["entries_paused"] is True
    conn.close()

    ok, reason = cp._apply_command(
        "resume",
        {
            "precedence": 1000,
            "expected_override_issued_at": issued_at,
        },
    )
    assert ok is True
    assert reason == ""

    conn = get_world_connection()
    assert query_control_override_state(conn)["entries_paused"] is False
    conn.close()


def test_equal_precedence_strategy_enable_requires_current_override_cas():
    """A same-priority enable must name the exact disabled gate it supersedes."""

    strategy = "forecast_qkernel_entry"
    override_id = f"control_plane:strategy:{strategy}:gate"
    issued_at = datetime.now(timezone.utc).isoformat()
    conn = get_world_connection()
    upsert_control_override(
        conn,
        override_id=override_id,
        target_type="strategy",
        target_key=strategy,
        action_type="gate",
        value="true",
        issued_by="operator",
        issued_at=issued_at,
        reason="forward_capital_proof_required",
        effective_until=None,
        precedence=1000,
    )
    conn.commit()
    conn.close()

    ok, reason = cp._apply_command(
        "set_strategy_gate",
        {"strategy": strategy, "enabled": True, "precedence": 1000},
    )
    assert ok is True
    assert reason.startswith("ignored_equal_precedence_without_cas:")

    conn = get_world_connection()
    assert query_control_override_state(conn)["strategy_gates"][strategy]["enabled"] is False
    conn.close()

    ok, reason = cp._apply_command(
        "set_strategy_gate",
        {
            "strategy": strategy,
            "enabled": True,
            "precedence": 1000,
            "expected_override_issued_at": issued_at,
        },
    )
    assert ok is True
    assert reason == ""

    conn = get_world_connection()
    assert query_control_override_state(conn)["strategy_gates"][strategy]["enabled"] is True
    conn.close()

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


def _mock_restart_guard_proof_inputs(monkeypatch, *, loaded_sha: str):
    class ReadOnlyConnection:
        in_transaction = True

        def close(self):
            return None

    monkeypatch.setattr(
        cp,
        "_read_loaded_identity",
        lambda: (loaded_sha, datetime(2026, 8, 8, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr(cp, "get_trade_connection_read_only", ReadOnlyConnection)
    monkeypatch.setattr(cp, "get_world_connection_read_only", ReadOnlyConnection)

    import src.ops.monitor_cadence as monitor_cadence

    monkeypatch.setattr(
        monitor_cadence,
        "collect_monitor_cadence_evidence",
        lambda _conn, **_kwargs: {
            "open_position_count": 1,
            "monitored_position_ids": ["healthy-pos"],
            "fresh_position_count": 1,
            "stale_or_missing_position_count": 0,
            "future_monitor_event_count": 0,
        },
    )


def test_restart_guard_allows_healthy_open_exposure_and_resets_once(monkeypatch):
    issued_at = "2026-08-08T00:00:00+00:00"
    expected_sha = "a" * 40
    armed = cp.arm_deploy_live_restart_guard(expected_sha, issued_at=issued_at)
    witness = armed["witness"]

    _mock_restart_guard_proof_inputs(monkeypatch, loaded_sha=expected_sha)
    import src.ops.edli_queue as edli_queue

    collector_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal collector_called
        collector_called = True
        raise AssertionError("restart guard proof must not scan EDLI history aggregate")

    monkeypatch.setattr(edli_queue, "collect_edli_queue_evidence", fail_if_called)
    monkeypatch.setattr(
        cp,
        "_restart_guard_queue_evidence",
        lambda *_args, **_kwargs: {
            "stale_processing": False,
            "claimable_pending": True,
            "post_issued_progress": False,
            "green": True,
        },
    )
    proof = cp.prove_deploy_live_restart_guard(witness)
    assert proof["green"] is True
    assert proof["monitor"]["open_position_count"] == 1
    assert proof["queue"]["claimable_pending"] is True
    assert proof["queue"]["post_issued_progress"] is False
    result = cp.reset_deploy_live_restart_guard(witness, proof=proof)
    assert result["status"] == "reset"
    assert cp.reset_deploy_live_restart_guard(witness, proof=proof)["status"] == "noop"
    assert collector_called is False


def test_restart_guard_preserves_operator_and_newer_invocation(monkeypatch):
    first = cp.arm_deploy_live_restart_guard(
        "b" * 40,
        issued_at="2026-08-08T00:00:00+00:00",
    )["witness"]
    second = cp.arm_deploy_live_restart_guard(
        "c" * 40,
        issued_at="2026-08-08T00:00:01+00:00",
    )["witness"]

    green = {"green": True, "witness": first}
    assert cp.reset_deploy_live_restart_guard(first, proof=green)["status"] == "noop"
    active = cp.get_active_deploy_live_restart_guard()
    assert active is not None
    assert active.expected_sha == second["expected_sha"]

    conn = get_world_connection()
    upsert_control_override(
        conn,
        override_id=AUTO_PAUSE_OVERRIDE_ID,
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="control_plane",
        issued_at="2026-08-08T00:00:02+00:00",
        reason="operator_hold",
        effective_until=None,
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()
    conn.close()
    assert cp.reset_deploy_live_restart_guard(second, proof={"green": True, "witness": second})["status"] == "noop"
    state = query_control_override_state(get_world_connection())
    assert state["entries_pause_reason"] == "operator_hold"


def test_restart_guard_proof_refuses_sha_monitor_or_queue_debt(monkeypatch):
    expected_sha = "d" * 40
    witness = cp.arm_deploy_live_restart_guard(
        expected_sha,
        issued_at="2026-08-08T00:00:00+00:00",
    )["witness"]
    _mock_restart_guard_proof_inputs(monkeypatch, loaded_sha="e" * 40)
    monkeypatch.setattr(
        cp,
        "_restart_guard_queue_evidence",
        lambda *_args, **_kwargs: {
            "stale_processing": False,
            "claimable_pending": True,
            "post_issued_progress": True,
            "green": True,
        },
    )
    assert cp.prove_deploy_live_restart_guard(witness)["green"] is False

    monkeypatch.setattr(
        cp,
        "_read_loaded_identity",
        lambda: (expected_sha, datetime(2026, 8, 8, tzinfo=timezone.utc)),
    )
    import src.ops.monitor_cadence as monitor_cadence

    monkeypatch.setattr(
        monitor_cadence,
        "collect_monitor_cadence_evidence",
        lambda _conn, **_kwargs: {
            "stale_or_missing_position_count": 1,
            "future_monitor_event_count": 0,
        },
    )
    assert cp.prove_deploy_live_restart_guard(witness)["green"] is False

    monkeypatch.setattr(
        monitor_cadence,
        "collect_monitor_cadence_evidence",
        lambda _conn, **_kwargs: {
            "stale_or_missing_position_count": 0,
            "future_monitor_event_count": 0,
        },
    )
    monkeypatch.setattr(
        cp,
        "_restart_guard_queue_evidence",
        lambda *_args, **_kwargs: {
            "stale_processing": True,
            "claimable_pending": True,
            "post_issued_progress": True,
            "green": False,
        },
    )
    assert cp.prove_deploy_live_restart_guard(witness)["green"] is False


def test_restart_guard_accepts_post_boot_probability_degraded_monitor_attempt(
    monkeypatch,
):
    expected_sha = "9" * 40
    witness = cp.arm_deploy_live_restart_guard(
        expected_sha,
        issued_at="2026-08-08T00:00:00+00:00",
    )["witness"]
    _mock_restart_guard_proof_inputs(monkeypatch, loaded_sha=expected_sha)
    monkeypatch.setattr(
        cp,
        "_restart_guard_queue_evidence",
        lambda *_args, **_kwargs: {
            "stale_processing": False,
            "claimable_pending": True,
            "post_issued_progress": True,
            "green": True,
        },
    )
    import src.ops.monitor_cadence as monitor_cadence

    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["probability-degraded"],
        "fresh_position_count": 0,
        "stale_or_missing_position_count": 1,
        "stale_or_missing_positions": [
            {
                "position_id": "probability-degraded",
                "issue": "monitor_probability_stale",
            }
        ],
        "blocking_stale_position_count": 1,
        "blocking_stale_positions": [
            {
                "position_id": "probability-degraded",
                "issue": "monitor_probability_stale",
            }
        ],
        "quote_only_stale_position_count": 0,
        "quote_only_stale_positions": [],
        "probability_only_stale_position_count": 1,
        "probability_only_stale_positions": [
            {
                "position_id": "probability-degraded",
                "issue": "monitor_probability_stale",
            }
        ],
        "future_monitor_event_count": 0,
    }
    monkeypatch.setattr(
        monitor_cadence,
        "collect_monitor_cadence_evidence",
        lambda _conn, **_kwargs: evidence,
    )

    proof = cp.prove_deploy_live_restart_guard(witness)
    assert proof["green"] is True
    assert proof["monitor"]["restart_blocking_stale_position_count"] == 0
    assert proof["monitor"]["blocking_stale_position_count"] == 1


def test_restart_guard_ignores_quote_only_debt_but_blocks_decision_debt(monkeypatch):
    expected_sha = "f" * 40
    witness = cp.arm_deploy_live_restart_guard(
        expected_sha,
        issued_at="2026-08-08T00:00:00+00:00",
    )["witness"]
    _mock_restart_guard_proof_inputs(monkeypatch, loaded_sha=expected_sha)
    monkeypatch.setattr(
        cp,
        "_restart_guard_queue_evidence",
        lambda *_args, **_kwargs: {
            "stale_processing": False,
            "claimable_pending": True,
            "post_issued_progress": True,
            "green": True,
        },
    )
    import src.ops.monitor_cadence as monitor_cadence

    evidence = {
        "open_position_count": 1,
        "monitored_position_ids": ["quote-only"],
        "stale_or_missing_position_count": 1,
        "stale_or_missing_positions": [{"position_id": "quote-only"}],
        "quote_only_stale_position_count": 1,
        "quote_only_stale_positions": [{"position_id": "quote-only"}],
        "blocking_stale_position_count": 0,
        "blocking_stale_positions": [],
        "future_monitor_event_count": 0,
    }
    monkeypatch.setattr(
        monitor_cadence,
        "collect_monitor_cadence_evidence",
        lambda _conn, **_kwargs: evidence,
    )
    receipt_kwargs = {}

    def complete_receipt(_conn, **kwargs):
        receipt_kwargs.update(kwargs)
        return (7, 42, 4)

    monkeypatch.setattr(
        monitor_cadence,
        "latest_complete_global_auction_receipt",
        complete_receipt,
    )
    proof = cp.prove_deploy_live_restart_guard(witness)
    assert proof["green"] is True
    assert proof["monitor"]["quote_only_stale_position_count"] == 1
    assert proof["monitor"]["blocking_stale_position_count"] == 0
    assert proof["monitor"]["complete_held_auction_receipt"] == (7, 42, 4)
    assert receipt_kwargs["require_held_position_ids"] == ("quote-only",)

    evidence.pop("monitored_position_ids")
    assert cp.prove_deploy_live_restart_guard(witness)["green"] is False
    evidence["monitored_position_ids"] = ["quote-only"]

    monkeypatch.setattr(
        monitor_cadence,
        "latest_complete_global_auction_receipt",
        lambda _conn, **_kwargs: None,
    )
    assert cp.prove_deploy_live_restart_guard(witness)["green"] is False

    monkeypatch.setattr(
        monitor_cadence,
        "latest_complete_global_auction_receipt",
        lambda _conn, **_kwargs: (8, 42, 4),
    )

    evidence["blocking_stale_position_count"] = 1
    evidence["blocking_stale_positions"] = [{"position_id": "decision-stale"}]
    assert cp.prove_deploy_live_restart_guard(witness)["green"] is False


def test_restart_guard_recovery_runs_after_reactor_failure_return(monkeypatch):
    import src.events.reactor as reactor
    import src.main as main_module

    calls = []
    monkeypatch.setattr(main_module, "_consume_live_control_commands", lambda: None)
    monkeypatch.setattr(main_module, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(main_module, "_settings_section", lambda *_args: {})
    monkeypatch.setattr(
        main_module,
        "_edli_live_entry_readiness_block",
        lambda _settings: ("deploy_live_restart_guard", {}),
    )
    monkeypatch.setattr(
        reactor,
        "run_edli_event_reactor_cycle",
        lambda **_kwargs: calls.append("reactor") or False,
    )
    monkeypatch.setattr(
        main_module,
        "_unowned_day0_urgent_wake_pending",
        lambda: False,
    )
    monkeypatch.setattr(
        cp,
        "recover_deploy_live_restart_guard",
        lambda: calls.append("recover") or {"status": "reset"},
    )

    assert main_module._edli_event_reactor_cycle() is False
    assert calls == ["reactor", "recover"]


def test_restart_guard_queue_probe_is_indexed_and_bounded():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    issued_at = "2026-08-08T00:00:00+00:00"
    conn.execute(
        """INSERT INTO opportunity_event_processing
           (consumer_name, event_id, processing_status, claimed_at, processed_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("edli_reactor_v1", "event-1", "processed", None, issued_at, issued_at),
    )

    evidence = cp._restart_guard_queue_evidence(
        conn,
        issued_at=issued_at,
        now=datetime.fromisoformat("2026-08-08T00:01:00+00:00"),
    )
    assert evidence == {
        "stale_processing": False,
        "claimable_pending": False,
        "post_issued_progress": True,
        "green": True,
    }
    stale_plan = conn.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT 1 FROM opportunity_event_processing
             INDEXED BY idx_opportunity_event_processing_stale_claim
        WHERE consumer_name = 'edli_reactor_v1'
          AND processing_status = 'processing'
          AND claimed_at IS NOT NULL
        LIMIT 1
        """
    ).fetchall()
    status_plan = conn.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT 1 FROM opportunity_event_processing
             INDEXED BY idx_opportunity_event_processing_status
        WHERE consumer_name = 'edli_reactor_v1'
          AND processing_status = 'processed'
          AND updated_at >= '2026-08-08T00:00:00+00:00'
        LIMIT 1
        """
    ).fetchall()
    assert "idx_opportunity_event_processing_stale_claim" in str(stale_plan)
    assert "idx_opportunity_event_processing_status" in str(status_plan)
    conn.close()


def test_restart_guard_queue_probe_accepts_a_truly_idle_queue():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)

    evidence = cp._restart_guard_queue_evidence(
        conn,
        issued_at="2026-08-08T00:00:00+00:00",
        now=datetime.fromisoformat("2026-08-08T00:01:00+00:00"),
    )

    assert evidence == {
        "stale_processing": False,
        "claimable_pending": False,
        "post_issued_progress": False,
        "green": True,
    }
    conn.close()


def test_restart_guard_queue_evidence_reports_current_claim_without_reset_ratchet():
    """Paused claimable entries are telemetry; stale ownership alone blocks reset."""
    now = datetime.fromisoformat("2026-08-08T00:01:00+00:00")
    conn = get_world_connection()
    init_schema(conn)
    store = EventStore(conn)

    def event(name: str, target_date: str, *, expires_at: str | None = None):
        return make_opportunity_event(
            event_type="FORECAST_SNAPSHOT_READY",
            entity_key=f"Chicago|{target_date}|high|{name}",
            source="restart-guard-queue-evidence",
            observed_at="2026-08-07T20:00:00+00:00",
            available_at="2026-08-07T20:00:00+00:00",
            received_at="2026-08-07T20:00:00+00:00",
            causal_snapshot_id=name,
            payload={"city": "Chicago", "target_date": target_date, "metric": "high"},
            expires_at=expires_at,
        )

    expired = event("expired", "2026-08-07", expires_at="2026-08-08T00:00:00+00:00")
    past_target = event("past-target", "2026-08-06")
    past_window = event("past-window", "2026-08-08", expires_at="2026-08-09T00:00:00+00:00")
    for historical in (expired, past_target, past_window):
        assert store.insert_or_ignore(historical)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET last_error = ?
         WHERE consumer_name = ?
           AND event_id = ?
        """,
        (
            "EVENT_BOUND_MARKET_PHASE_CLOSED:selection_deadline=2026-08-08T00:00:00+00:00",
            "edli_reactor_v1",
            past_window.event_id,
        ),
    )
    conn.commit()

    historical_evidence = cp._restart_guard_queue_evidence(
        conn,
        issued_at="2026-08-08T00:00:00+00:00",
        now=now,
    )
    assert historical_evidence == {
        "stale_processing": False,
        "claimable_pending": False,
        "post_issued_progress": False,
        "green": True,
    }

    current = event("current", "2026-08-08", expires_at="2026-08-09T00:00:00+00:00")
    assert store.insert_or_ignore(current)
    conn.commit()

    current_evidence = cp._restart_guard_queue_evidence(
        conn,
        issued_at="2026-08-08T00:00:00+00:00",
        now=now,
    )
    assert current_evidence == {
        "stale_processing": False,
        "claimable_pending": True,
        "post_issued_progress": False,
        "green": True,
    }
    conn.close()
