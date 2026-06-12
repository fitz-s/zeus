# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: docs/operations/consolidated_systemic_overhaul_2026-06-11.md K4.0
"""Relationship tests for the K4.0 maker-rest escalation job.

The escalation job is the DEADLINE owner of the rest-then-cross plan: it cancels
post_only GTC entry rests older than the measured deadline, and NOTHING else.
The cross decision itself never happens here — the next certified reactor
decision owns it (TAKER_ESCALATED_AFTER_REST lane).
"""

from datetime import datetime, timedelta, timezone

from src.execution.maker_rest_escalation import (
    find_expired_resting_entries,
    run_maker_rest_escalation_cycle,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 10, 22, 0, 0, tzinfo=UTC)


import sqlite3


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY, intent_kind TEXT, market_id TEXT,
            token_id TEXT, side TEXT, size REAL, price REAL,
            venue_order_id TEXT, state TEXT, created_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY, venue_order_id TEXT, command_id TEXT,
            state TEXT, remaining_size TEXT, matched_size TEXT,
            local_sequence INTEGER)"""
    )
    return conn


def _add_order(
    conn,
    *,
    command_id: str,
    intent_kind: str = "ENTRY",
    venue_order_id: str | None = None,
    created_at: datetime = NOW - timedelta(minutes=180),
    fact_states: tuple[str, ...] = ("LIVE",),
    matched: str = "0",
):
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            command_id,
            intent_kind,
            "m1",
            "t1",
            "BUY",
            10.0,
            0.5,
            venue_order_id,
            "ACKED",
            created_at.isoformat(),
        ),
    )
    for i, state in enumerate(fact_states):
        conn.execute(
            "INSERT INTO venue_order_facts VALUES (NULL,?,?,?,?,?,?)",
            (venue_order_id, command_id, state, "10", matched, i),
        )


class _FakeClob:
    def __init__(self, fail_on: set[str] | None = None):
        self.cancelled: list[str] = []
        self._fail_on = fail_on or set()

    def cancel_order(self, order_id: str):
        if order_id in self._fail_on:
            raise RuntimeError("venue cancel error")
        self.cancelled.append(order_id)
        return {"canceled": [order_id]}


class TestScopeGuards:
    def test_expired_open_entry_rest_is_cancelled(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        clob = _FakeClob()
        stats = run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == ["o1"]
        assert stats == {"scanned": 1, "cancelled": 1, "cancel_failed": 0}

    def test_young_rest_is_untouched(self):
        conn = _db()
        _add_order(
            conn,
            command_id="c1",
            venue_order_id="o1",
            created_at=NOW - timedelta(minutes=60),  # < 120-min deadline
        )
        clob = _FakeClob()
        stats = run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == []
        assert stats["scanned"] == 0

    def test_exit_orders_are_never_touched(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1", intent_kind="EXIT")
        clob = _FakeClob()
        run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == []

    def test_terminal_orders_are_never_touched(self):
        """Latest fact wins: a rest that later MATCHED/CANCELLED must not be cancelled."""
        conn = _db()
        _add_order(
            conn,
            command_id="c1",
            venue_order_id="o1",
            fact_states=("LIVE", "MATCHED"),
        )
        _add_order(
            conn,
            command_id="c2",
            venue_order_id="o2",
            fact_states=("LIVE", "CANCEL_CONFIRMED"),
        )
        clob = _FakeClob()
        run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == []

    def test_stuck_submitting_without_order_id_is_skipped(self):
        """No venue_order_id (lost ack) -> command recovery owns it, not this job."""
        conn = _db()
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "c1",
                "ENTRY",
                "m1",
                "t1",
                "BUY",
                10.0,
                0.5,
                None,
                "SUBMITTING",
                (NOW - timedelta(minutes=300)).isoformat(),
            ),
        )
        clob = _FakeClob()
        stats = run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == [] and stats["scanned"] == 0

    def test_partial_rest_remainder_is_cancelled(self):
        conn = _db()
        _add_order(
            conn,
            command_id="c1",
            venue_order_id="o1",
            fact_states=("PARTIALLY_MATCHED",),
            matched="4",
        )
        clob = _FakeClob()
        stats = run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == ["o1"]
        assert stats["cancelled"] == 1


class TestFailSoft:
    def test_cancel_error_continues_to_next_order(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        _add_order(conn, command_id="c2", venue_order_id="o2")
        clob = _FakeClob(fail_on={"o1"})
        stats = run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == ["o2"]
        assert stats == {"scanned": 2, "cancelled": 1, "cancel_failed": 1}


class TestDeadlineSource:
    def test_default_deadline_is_the_registry_constant(self):
        from src.strategy.live_inference.mode_consistent_ev import (
            MAKER_REST_ESCALATION_DEADLINE_MINUTES,
        )

        conn = _db()
        # Exactly one minute younger than the deadline: untouched.
        _add_order(
            conn,
            command_id="c1",
            venue_order_id="o1",
            created_at=NOW
            - timedelta(minutes=MAKER_REST_ESCALATION_DEADLINE_MINUTES - 1),
        )
        clob = _FakeClob()
        stats = run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert stats["scanned"] == 0
