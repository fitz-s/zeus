# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: docs/evidence/timing_audit/exec_submit_reject_breakdown_2026-06-16.md
#   (C-DBLOCK-UNKNOWN). The dominant CURRENT live-fill blocker is
#   EXECUTOR_SUBMIT_UNKNOWN:'database is locked' (13x Jun 12-16): a transient SQLite
#   lock while persisting a KNOWN-good venue ACK degrades the order to
#   unknown_side_effect, tripping the governor kill-switch (limit=0). The fix retries
#   the LOCAL persistence (rollback + re-run) on 'database is locked' only.
"""Unit tests for executor._retry_persist_on_db_lock.

Proves the post-side-effect persistence retry:
  - returns on first success (no rollback, no sleep),
  - retries a transient 'database is locked' (rollback between attempts) then succeeds,
  - exhausts and re-raises a persistent lock after `attempts`,
  - propagates a NON-lock OperationalError immediately (no retry, no rollback),
  - propagates a non-OperationalError (e.g. the ValueError append_event raises on an
    illegal grammar transition) immediately — never retried.
"""
from __future__ import annotations

import sqlite3

import pytest

import src.execution.executor as ex


class _FakeConn:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ex.time, "sleep", lambda *_a, **_k: None)


def test_returns_on_first_success() -> None:
    conn = _FakeConn()
    calls = []
    ex._retry_persist_on_db_lock(conn, lambda: calls.append(1), what="t")
    assert calls == [1]
    assert conn.rollbacks == 0


def test_retries_transient_lock_then_succeeds() -> None:
    conn = _FakeConn()
    state = {"n": 0}

    def persist() -> None:
        state["n"] += 1
        if state["n"] == 1:
            raise sqlite3.OperationalError("database is locked")

    ex._retry_persist_on_db_lock(conn, persist, what="t", attempts=4)
    assert state["n"] == 2  # failed once, succeeded on retry
    assert conn.rollbacks == 1  # rolled back before the retry


def test_persistent_lock_exhausts_and_raises() -> None:
    conn = _FakeConn()
    calls = {"n": 0}

    def persist() -> None:
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        ex._retry_persist_on_db_lock(conn, persist, what="t", attempts=3)
    assert calls["n"] == 3  # tried exactly `attempts` times
    assert conn.rollbacks == 2  # rolled back between the 3 attempts, not after the last


def test_non_lock_operationalerror_propagates_immediately() -> None:
    conn = _FakeConn()
    calls = {"n": 0}

    def persist() -> None:
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: venue_command_events")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        ex._retry_persist_on_db_lock(conn, persist, what="t", attempts=4)
    assert calls["n"] == 1  # NOT retried
    assert conn.rollbacks == 0


def test_grammar_valueerror_propagates_immediately() -> None:
    conn = _FakeConn()
    calls = {"n": 0}

    def persist() -> None:
        calls["n"] += 1
        raise ValueError("Illegal command-event grammar transition")

    with pytest.raises(ValueError, match="grammar"):
        ex._retry_persist_on_db_lock(conn, persist, what="t", attempts=4)
    assert calls["n"] == 1  # NOT retried (only 'database is locked' is)
    assert conn.rollbacks == 0
