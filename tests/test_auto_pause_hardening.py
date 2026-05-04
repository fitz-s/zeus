# Created: 2026-05-01
# Last reused/audited: 2026-05-04
# Authority basis: live-blockers session 2026-05-01 — harden auto_pause to
#                  prevent permanent lock-out on transient failures.
# RETIRED 2026-05-04: auto_pause_streak module deleted in gate-purge Stage 2.
# All tests in this file are skipped; file preserved as history.
"""Antibody tests for the 2026-05-01 auto-pause hardening.

RETIRED 2026-05-04: The streak-based auto-pause machinery was removed in the
gate-purge Stage 2 commit.  These tests are preserved as history but are
skipped via pytest.mark.skip.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skip(reason="auto-pause streak retired 2026-05-04")

import src.control.control_plane as cp
from src.state.db import (
    apply_architecture_kernel_schema,
    get_connection,
    query_control_override_state,
)

# auto_pause_streak module deleted 2026-05-04 — import guard for history preservation
try:
    import src.control.auto_pause_streak as streak  # type: ignore[import]
except ModuleNotFoundError:
    streak = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_control_state(monkeypatch, tmp_path):
    """Each test gets a fresh streak file path and an empty control state."""
    monkeypatch.setattr(streak, "_streak_path", lambda: tmp_path / streak.STREAK_FILE)
    monkeypatch.setattr(cp, "alert_auto_pause", lambda r: None)
    cp._control_state.clear()
    cp._control_state["entries_paused"] = False
    yield
    cp._control_state.clear()


@pytest.fixture
def world_db(monkeypatch, tmp_path):
    """A real on-disk SQLite world DB with the control_overrides_history schema."""
    db_path = tmp_path / "world.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    conn.close()

    def _factory():
        return get_connection(db_path)

    monkeypatch.setattr(cp, "get_world_connection", _factory)
    monkeypatch.setattr(cp, "state_path", lambda name: tmp_path / name)
    return db_path


# ---------------------------------------------------------------------------
# Fix 1 antibodies
# ---------------------------------------------------------------------------


def test_auto_pause_has_15min_expiry(world_db):
    """pause_entries inserts override with effective_until ~= now + 15min,
    NOT effective_until=None."""
    before = datetime.now(timezone.utc)
    cp.pause_entries("auto_pause:ValueError")
    after = datetime.now(timezone.utc)

    conn = sqlite3.connect(world_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT issued_by, reason, value, effective_until, operation
        FROM control_overrides_history
        WHERE override_id = 'control_plane:global:entries_paused'
        ORDER BY history_id DESC
        LIMIT 1
        """
    ).fetchall()
    conn.close()

    assert len(rows) == 1, "exactly one history row should be written"
    row = rows[0]
    assert row["issued_by"] == "system_auto_pause"
    assert row["reason"] == "auto_pause:ValueError"
    assert row["value"] == "true"
    assert row["operation"] == "upsert"
    assert row["effective_until"] is not None, (
        "auto_pause must NOT write effective_until=NULL anymore — that was "
        "the permanent-lock-out bug fixed 2026-05-01"
    )
    eff = datetime.fromisoformat(row["effective_until"])
    expected_low = before + timedelta(minutes=14, seconds=30)
    expected_high = after + timedelta(minutes=15, seconds=30)
    assert expected_low <= eff <= expected_high, (
        f"effective_until={eff} should be ~15min from now, "
        f"window=[{expected_low}, {expected_high}]"
    )


def test_auto_pause_idempotent_same_reason(world_db):
    """Calling pause_entries twice in a row with the same reason_code inserts
    only the FIRST history row; the second call is a no-op while the first
    override is still active."""
    cp.pause_entries("auto_pause:ValueError")
    cp.pause_entries("auto_pause:ValueError")
    cp.pause_entries("auto_pause:ValueError")

    conn = sqlite3.connect(world_db)
    rows = conn.execute(
        """
        SELECT history_id, operation, reason
        FROM control_overrides_history
        WHERE override_id = 'control_plane:global:entries_paused'
          AND operation = 'upsert'
        ORDER BY history_id ASC
        """
    ).fetchall()
    conn.close()

    assert len(rows) == 1, (
        f"three identical pause_entries calls should produce exactly one "
        f"upsert row (idempotent), got {len(rows)}: {rows}"
    )

    # In-memory state still reflects the pause regardless.
    assert cp.is_entries_paused() is True
    assert cp.get_entries_pause_reason() == "auto_pause:ValueError"


def test_auto_pause_streak_threshold(monkeypatch, tmp_path):
    """A single ValueError does NOT trigger pause_entries; three within the
    window DO."""
    pause_calls = []
    monkeypatch.setattr(
        cp, "pause_entries", lambda *a, **kw: pause_calls.append((a, kw))
    )

    # First failure — streak=1, no pause.
    count = streak.record_failure("auto_pause:ValueError")
    assert count == 1
    assert not streak.threshold_reached(count)
    if streak.threshold_reached(count):
        cp.pause_entries("auto_pause:ValueError")
    assert pause_calls == []

    # Second failure — streak=2, still no pause.
    count = streak.record_failure("auto_pause:ValueError")
    assert count == 2
    if streak.threshold_reached(count):
        cp.pause_entries("auto_pause:ValueError")
    assert pause_calls == []

    # Third failure — streak=3, pause fires.
    count = streak.record_failure("auto_pause:ValueError")
    assert count == 3
    assert streak.threshold_reached(count)
    if streak.threshold_reached(count):
        cp.pause_entries("auto_pause:ValueError")
    assert len(pause_calls) == 1


def test_auto_pause_streak_resets_on_success(monkeypatch, tmp_path):
    """After 2 ValueErrors then 1 successful entry path, the streak resets.
    One more ValueError must NOT trigger pause."""
    pause_calls = []
    monkeypatch.setattr(
        cp, "pause_entries", lambda *a, **kw: pause_calls.append((a, kw))
    )

    streak.record_failure("auto_pause:ValueError")
    streak.record_failure("auto_pause:ValueError")
    # Streak=2, on the brink. A successful cycle clears it.
    streak.clear_streak()

    # One more ValueError — must come back at count=1, not count=3.
    count = streak.record_failure("auto_pause:ValueError")
    assert count == 1, (
        "clear_streak() must zero the counter; new failure should restart at 1"
    )
    if streak.threshold_reached(count):
        cp.pause_entries("auto_pause:ValueError")
    assert pause_calls == [], (
        "single failure after a clean cycle must not trigger pause_entries"
    )


def test_existing_callers_still_work(world_db):
    """The manual operator path (issued_by='control_plane' via _apply_command
    pause_entries) is UNAFFECTED — it can still issue indefinite pauses with
    effective_until=None."""
    # Direct call mirroring control_plane._apply_command pause_entries branch:
    # it uses upsert_control_override directly with effective_until passed by
    # the operator (None for indefinite).
    from src.state.db import upsert_control_override, DEFAULT_CONTROL_OVERRIDE_PRECEDENCE
    conn = cp.get_world_connection()
    upsert_control_override(
        conn,
        override_id="control_plane:global:entries_paused",
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="control_plane",
        issued_at=datetime.now(timezone.utc).isoformat(),
        reason="manual operator pause",
        effective_until=None,  # indefinite — operator power
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()
    conn.close()

    state = query_control_override_state(cp.get_world_connection())
    assert state["entries_paused"] is True
    assert state["entries_pause_source"] == "manual_command"
    assert state["entries_pause_reason"] == "manual operator pause"

    # Verify the override carries effective_until=NULL — operator pauses are
    # still allowed to be indefinite (the auto-pause expiry rule applies
    # ONLY when issued_by='system_auto_pause' AND effective_until omitted).
    conn = sqlite3.connect(world_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT issued_by, effective_until
        FROM control_overrides_history
        WHERE override_id='control_plane:global:entries_paused'
          AND operation='upsert'
        ORDER BY history_id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert row["issued_by"] == "control_plane"
    assert row["effective_until"] is None, (
        "manual operator pauses must remain capable of being indefinite — "
        "the 15-min default applies only to system_auto_pause"
    )
