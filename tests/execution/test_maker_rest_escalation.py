# Created: 2026-06-10
# Last reused or audited: 2026-06-19
# Authority basis: docs/operations/consolidated_systemic_overhaul_2026-06-11.md K4.0
"""Relationship tests for the K4.0 maker-rest escalation job.

The escalation job is the DEADLINE owner of the rest-then-cross plan: it cancels
post_only GTC entry rests older than the measured deadline, and NOTHING else.
The cross decision itself never happens here — the next certified reactor
decision owns it (TAKER_ESCALATED_AFTER_REST lane).
"""

import json
from datetime import datetime, timedelta, timezone

from src.execution.maker_rest_escalation import (
    find_expired_resting_entries,
    run_cancels_for_expired_rests,
    run_maker_rest_escalation_cycle,
    run_persisted_cancels_for_expired_rests,
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
            venue_order_id TEXT, state TEXT, last_event_id TEXT,
            created_at TEXT, updated_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT,
            state_after TEXT NOT NULL,
            UNIQUE (command_id, sequence_no)
        )"""
    )
    conn.execute(
        """CREATE TABLE provenance_envelope_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            payload_json TEXT,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            venue_timestamp TEXT,
            local_sequence INTEGER NOT NULL,
            UNIQUE (subject_type, subject_id, local_sequence)
        )"""
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
    command_state: str = "ACKED",
    venue_order_id: str | None = None,
    created_at: datetime = NOW - timedelta(minutes=180),
    fact_states: tuple[str, ...] = ("LIVE",),
    matched: str = "0",
):
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            command_id,
            intent_kind,
            "m1",
            "t1",
            "BUY",
            10.0,
            0.5,
            venue_order_id,
            command_state,
            None,
            created_at.isoformat(),
            created_at.isoformat(),
        ),
    )
    conn.execute(
        """INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES (?, ?, 1, 'INTENT_CREATED', ?, NULL, 'INTENT_CREATED')""",
        (f"{command_id}-intent", command_id, created_at.isoformat()),
    )
    conn.execute(
        """INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES (?, ?, 2, 'SUBMIT_REQUESTED', ?, NULL, 'SUBMITTING')""",
        (f"{command_id}-requested", command_id, created_at.isoformat()),
    )
    conn.execute(
        """INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES (?, ?, 3, 'SUBMIT_ACKED', ?, NULL, 'ACKED')""",
        (f"{command_id}-acked", command_id, created_at.isoformat()),
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
            created_at=NOW - timedelta(minutes=10),  # < 20-min deadline (2026-06-16)
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

    def test_review_required_open_fact_is_recovery_owned_not_cancelled(self):
        conn = _db()
        _add_order(
            conn,
            command_id="c1",
            venue_order_id="o1",
            command_state="REVIEW_REQUIRED",
            fact_states=("LIVE",),
        )
        clob = _FakeClob()
        stats = run_maker_rest_escalation_cycle(conn, clob, now=NOW)
        assert clob.cancelled == []
        assert stats["scanned"] == 0

    def test_stuck_submitting_without_order_id_is_skipped(self):
        """No venue_order_id (lost ack) -> command recovery owns it, not this job."""
        conn = _db()
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
                None,
                (NOW - timedelta(minutes=300)).isoformat(),
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

    def test_cancel_unknown_event_carries_recovery_semantics(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        clob = _FakeClob(fail_on={"o1"})
        expired = find_expired_resting_entries(conn, now=NOW)

        run_persisted_cancels_for_expired_rests(
            expired,
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
        )

        event = conn.execute(
            """
            SELECT event_type, payload_json
              FROM venue_command_events
             WHERE command_id = 'c1'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        assert event["event_type"] == "CANCEL_REPLACE_BLOCKED"
        payload = json.loads(event["payload_json"])
        assert payload["reason"] == "post_cancel_unknown_possible_side_effect"
        assert payload["semantic_cancel_status"] == "CANCEL_UNKNOWN"
        assert payload["requires_m5_reconcile"] is True


class TestEscalationRedecisionHarvest:
    """collect_cancelled out-parameter (redecide-block fix 2026-06-16): the cancel
    path harvests EXACTLY the CONFIRMED-cancelled entries — the caller emits ONE
    Tier-0 EDLI_REDECISION_PENDING per harvested family. A cancel_failed entry is
    NEVER harvested (no re-decision for a family whose rest was not actually pulled).
    """

    def test_one_harvest_per_confirmed_cancel(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        _add_order(conn, command_id="c2", venue_order_id="o2")
        expired = find_expired_resting_entries(conn, now=NOW)
        collected: list[dict] = []
        clob = _FakeClob()
        stats = run_cancels_for_expired_rests(expired, clob, collect_cancelled=collected)
        assert stats == {"scanned": 2, "cancelled": 2, "cancel_failed": 0}
        # Exactly one harvested entry per confirmed cancel, each carrying the
        # family-recovery handles (token_id / market_id / command_id).
        assert len(collected) == 2
        assert {e["command_id"] for e in collected} == {"c1", "c2"}
        assert all(e.get("token_id") for e in collected)

    def test_cancel_failed_is_not_harvested(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        _add_order(conn, command_id="c2", venue_order_id="o2")
        expired = find_expired_resting_entries(conn, now=NOW)
        collected: list[dict] = []
        clob = _FakeClob(fail_on={"o1"})
        stats = run_cancels_for_expired_rests(expired, clob, collect_cancelled=collected)
        assert stats == {"scanned": 2, "cancelled": 1, "cancel_failed": 1}
        # ONLY the confirmed cancel (c2) is harvested; the failed one (c1) is not.
        assert [e["command_id"] for e in collected] == ["c2"]

    def test_no_collect_list_preserves_byte_identical_stats(self):
        """Default (collect_cancelled=None): stats and behavior are byte-identical
        to the pre-fix contract — the existing exact-equality callers/tests hold."""
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        expired = find_expired_resting_entries(conn, now=NOW)
        clob = _FakeClob()
        stats = run_cancels_for_expired_rests(expired, clob)
        assert stats == {"scanned": 1, "cancelled": 1, "cancel_failed": 0}


class TestPersistedRestCancel:
    def test_persisted_cancel_records_command_terminal_state_before_harvest(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        expired = find_expired_resting_entries(conn, now=NOW)
        collected: list[dict] = []
        clob = _FakeClob()

        stats = run_persisted_cancels_for_expired_rests(
            expired,
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
            collect_cancelled=collected,
        )

        assert stats == {
            "scanned": 1,
            "cancelled": 1,
            "cancel_failed": 0,
            "cancel_journal_failed": 0,
        }
        assert clob.cancelled == ["o1"]
        assert [entry["command_id"] for entry in collected] == ["c1"]
        assert conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'c1'"
        ).fetchone()[0] == "CANCELLED"
        events = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            ).fetchall()
        ]
        assert events[-2:] == ["CANCEL_REQUESTED", "CANCEL_ACKED"]


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
