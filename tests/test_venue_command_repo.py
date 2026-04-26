# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S1
"""Tests for src/state/venue_command_repo.py (P1.S1 — INV-28 / NC-18)."""
from __future__ import annotations

import ast
import glob
import sqlite3
import unittest.mock
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory DB with full schema (via init_schema)."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _insert(c, *, command_id="cmd-001", position_id="pos-001",
            decision_id="dec-001", idempotency_key="idem-001",
            intent_kind="ENTRY", market_id="mkt-001", token_id="tok-001",
            side="BUY", size=10.0, price=0.5,
            created_at="2026-04-26T00:00:00Z"):
    from src.state.venue_command_repo import insert_command
    insert_command(
        c,
        command_id=command_id,
        position_id=position_id,
        decision_id=decision_id,
        idempotency_key=idempotency_key,
        intent_kind=intent_kind,
        market_id=market_id,
        token_id=token_id,
        side=side,
        size=size,
        price=price,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Test 1: insert_command atomicity
# ---------------------------------------------------------------------------

class TestInsertCommandAtomicWithIntentCreatedEvent:
    def test_both_rows_inserted(self, conn):
        from src.state.venue_command_repo import insert_command, list_events, get_command

        _insert(conn)

        cmd = get_command(conn, "cmd-001")
        assert cmd is not None
        assert cmd["state"] == "INTENT_CREATED"
        assert cmd["command_id"] == "cmd-001"
        assert cmd["idempotency_key"] == "idem-001"

        events = list_events(conn, "cmd-001")
        assert len(events) == 1
        assert events[0]["event_type"] == "INTENT_CREATED"
        assert events[0]["state_after"] == "INTENT_CREATED"
        assert events[0]["sequence_no"] == 1

        # last_event_id must point to the INTENT_CREATED event
        assert cmd["last_event_id"] == events[0]["event_id"]

    def test_rollback_on_mid_transaction_failure(self, conn):
        """If the events INSERT fails, the command INSERT must also roll back."""
        from src.state.venue_command_repo import insert_command

        # Sabotage: drop the events table so the second INSERT raises
        conn.execute("DROP TABLE venue_command_events")
        conn.commit()

        with pytest.raises(Exception):
            insert_command(
                conn,
                command_id="cmd-fail",
                position_id="pos-001",
                decision_id="dec-001",
                idempotency_key="idem-fail",
                intent_kind="ENTRY",
                market_id="mkt-001",
                token_id="tok-001",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-04-26T00:00:00Z",
            )

        # The command row must NOT exist
        row = conn.execute(
            "SELECT command_id FROM venue_commands WHERE command_id = 'cmd-fail'"
        ).fetchone()
        assert row is None, "command row should have been rolled back"


# ---------------------------------------------------------------------------
# Test 2: append_event state transition grammar
# ---------------------------------------------------------------------------

class TestAppendEventStateTransitionIsGrammarChecked:
    # --- legal transitions ---

    def test_intent_created_to_submitting(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        assert get_command(conn, "cmd-001")["state"] == "SUBMITTING"

    def test_submitting_to_acked(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")
        assert get_command(conn, "cmd-001")["state"] == "ACKED"

    def test_submitting_to_rejected(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REJECTED",
                     occurred_at="2026-04-26T00:02:00Z")
        assert get_command(conn, "cmd-001")["state"] == "REJECTED"

    def test_submitting_to_unknown(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_UNKNOWN",
                     occurred_at="2026-04-26T00:02:00Z")
        assert get_command(conn, "cmd-001")["state"] == "UNKNOWN"

    def test_acked_to_partial(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")
        append_event(conn, command_id="cmd-001", event_type="PARTIAL_FILL_OBSERVED",
                     occurred_at="2026-04-26T00:03:00Z")
        assert get_command(conn, "cmd-001")["state"] == "PARTIAL"

    def test_acked_to_filled(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")
        append_event(conn, command_id="cmd-001", event_type="FILL_CONFIRMED",
                     occurred_at="2026-04-26T00:03:00Z")
        assert get_command(conn, "cmd-001")["state"] == "FILLED"

    def test_cancel_pending_to_cancelled(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-001", event_type="CANCEL_REQUESTED",
                     occurred_at="2026-04-26T00:02:00Z")
        append_event(conn, command_id="cmd-001", event_type="CANCEL_ACKED",
                     occurred_at="2026-04-26T00:03:00Z")
        assert get_command(conn, "cmd-001")["state"] == "CANCELLED"

    def test_intent_created_to_review_required(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="REVIEW_REQUIRED",
                     occurred_at="2026-04-26T00:01:00Z")
        assert get_command(conn, "cmd-001")["state"] == "REVIEW_REQUIRED"

    # --- illegal transitions ---

    @pytest.mark.parametrize("from_state,event_type,setup_events", [
        # From INTENT_CREATED: only SUBMIT_REQUESTED and REVIEW_REQUIRED are legal
        ("INTENT_CREATED", "SUBMIT_ACKED", []),
        ("INTENT_CREATED", "SUBMIT_REJECTED", []),
        ("INTENT_CREATED", "SUBMIT_UNKNOWN", []),
        ("INTENT_CREATED", "FILL_CONFIRMED", []),
        ("INTENT_CREATED", "CANCEL_REQUESTED", []),
        ("INTENT_CREATED", "CANCEL_ACKED", []),
        ("INTENT_CREATED", "EXPIRED", []),
        ("INTENT_CREATED", "PARTIAL_FILL_OBSERVED", []),
        # From SUBMITTING: SUBMIT_ACKED, SUBMIT_REJECTED, SUBMIT_UNKNOWN,
        # CANCEL_REQUESTED, REVIEW_REQUIRED are legal; others illegal
        ("SUBMITTING", "INTENT_CREATED", ["SUBMIT_REQUESTED"]),
        ("SUBMITTING", "FILL_CONFIRMED", ["SUBMIT_REQUESTED"]),
        ("SUBMITTING", "PARTIAL_FILL_OBSERVED", ["SUBMIT_REQUESTED"]),
        ("SUBMITTING", "CANCEL_ACKED", ["SUBMIT_REQUESTED"]),
        ("SUBMITTING", "EXPIRED", ["SUBMIT_REQUESTED"]),
        # From ACKED: fill/cancel/expire/review legal; submit events illegal
        ("ACKED", "SUBMIT_REQUESTED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "SUBMIT_ACKED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "SUBMIT_REJECTED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "SUBMIT_UNKNOWN", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "CANCEL_ACKED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        # From FILLED: only REVIEW_REQUIRED legal
        ("FILLED", "SUBMIT_REQUESTED",
         ["SUBMIT_REQUESTED", "SUBMIT_ACKED", "FILL_CONFIRMED"]),
        ("FILLED", "CANCEL_REQUESTED",
         ["SUBMIT_REQUESTED", "SUBMIT_ACKED", "FILL_CONFIRMED"]),
        ("FILLED", "FILL_CONFIRMED",
         ["SUBMIT_REQUESTED", "SUBMIT_ACKED", "FILL_CONFIRMED"]),
        # From CANCEL_PENDING: only CANCEL_ACKED, EXPIRED, REVIEW_REQUIRED legal
        ("CANCEL_PENDING", "SUBMIT_ACKED",
         ["SUBMIT_REQUESTED", "CANCEL_REQUESTED"]),
        ("CANCEL_PENDING", "FILL_CONFIRMED",
         ["SUBMIT_REQUESTED", "CANCEL_REQUESTED"]),
    ])
    def test_illegal_transition_raises_value_error(
            self, conn, from_state, event_type, setup_events):
        from src.state.venue_command_repo import append_event
        _insert(conn)
        for evt in setup_events:
            append_event(conn, command_id="cmd-001", event_type=evt,
                         occurred_at="2026-04-26T00:00:00Z")
        with pytest.raises(ValueError, match="Illegal command-event grammar"):
            append_event(conn, command_id="cmd-001", event_type=event_type,
                         occurred_at="2026-04-26T00:10:00Z")

    def test_unknown_command_id_raises_value_error(self, conn):
        from src.state.venue_command_repo import append_event
        with pytest.raises(ValueError, match="Unknown command_id"):
            append_event(conn, command_id="nonexistent", event_type="SUBMIT_REQUESTED",
                         occurred_at="2026-04-26T00:00:00Z")


# ---------------------------------------------------------------------------
# Test 3: idempotency key uniqueness
# ---------------------------------------------------------------------------

class TestIdempotencyKeyUniquenessEnforced:
    def test_duplicate_key_raises_integrity_error(self, conn):
        from src.state.venue_command_repo import insert_command
        _insert(conn, command_id="cmd-001", idempotency_key="same-key")

        with pytest.raises(sqlite3.IntegrityError):
            insert_command(
                conn,
                command_id="cmd-002",
                position_id="pos-002",
                decision_id="dec-002",
                idempotency_key="same-key",  # same key
                intent_kind="ENTRY",
                market_id="mkt-001",
                token_id="tok-001",
                side="BUY",
                size=5.0,
                price=0.6,
                created_at="2026-04-26T00:01:00Z",
            )

    def test_different_keys_succeed(self, conn):
        from src.state.venue_command_repo import insert_command, get_command
        _insert(conn, command_id="cmd-001", idempotency_key="key-A")
        insert_command(
            conn,
            command_id="cmd-002",
            position_id="pos-002",
            decision_id="dec-002",
            idempotency_key="key-B",
            intent_kind="EXIT",
            market_id="mkt-001",
            token_id="tok-001",
            side="SELL",
            size=5.0,
            price=0.6,
            created_at="2026-04-26T00:01:00Z",
        )
        assert get_command(conn, "cmd-001") is not None
        assert get_command(conn, "cmd-002") is not None


# ---------------------------------------------------------------------------
# Test 4: find_unresolved_commands returns only in-flight
# ---------------------------------------------------------------------------

class TestFindUnresolvedCommandsReturnsOnlyInFlight:
    def test_returns_only_submitting_unknown_review(self, conn):
        from src.state.venue_command_repo import append_event, find_unresolved_commands

        # ACKED (terminal-ish, not in unresolved set)
        _insert(conn, command_id="cmd-acked", idempotency_key="key-acked")
        append_event(conn, command_id="cmd-acked", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z")
        append_event(conn, command_id="cmd-acked", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:01:00Z")

        # SUBMITTING
        _insert(conn, command_id="cmd-submitting", idempotency_key="key-sub")
        append_event(conn, command_id="cmd-submitting", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z")

        # UNKNOWN
        _insert(conn, command_id="cmd-unknown", idempotency_key="key-unk")
        append_event(conn, command_id="cmd-unknown", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z")
        append_event(conn, command_id="cmd-unknown", event_type="SUBMIT_UNKNOWN",
                     occurred_at="2026-04-26T00:01:00Z")

        # FILLED (resolved, should not appear)
        _insert(conn, command_id="cmd-filled", idempotency_key="key-filled")
        append_event(conn, command_id="cmd-filled", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z")
        append_event(conn, command_id="cmd-filled", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-filled", event_type="FILL_CONFIRMED",
                     occurred_at="2026-04-26T00:02:00Z")

        # REVIEW_REQUIRED
        _insert(conn, command_id="cmd-review", idempotency_key="key-rev")
        append_event(conn, command_id="cmd-review", event_type="REVIEW_REQUIRED",
                     occurred_at="2026-04-26T00:01:00Z")

        unresolved = list(find_unresolved_commands(conn))
        ids = {r["command_id"] for r in unresolved}
        assert ids == {"cmd-submitting", "cmd-unknown", "cmd-review"}
        assert "cmd-acked" not in ids
        assert "cmd-filled" not in ids


# ---------------------------------------------------------------------------
# Test 5: list_events ordered by sequence_no
# ---------------------------------------------------------------------------

class TestListEventsOrderedBySequenceNo:
    def test_three_events_in_order(self, conn):
        from src.state.venue_command_repo import append_event, list_events

        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")

        events = list_events(conn, "cmd-001")
        # Should have: INTENT_CREATED (1), SUBMIT_REQUESTED (2), SUBMIT_ACKED (3)
        assert len(events) == 3
        assert events[0]["sequence_no"] == 1
        assert events[0]["event_type"] == "INTENT_CREATED"
        assert events[1]["sequence_no"] == 2
        assert events[1]["event_type"] == "SUBMIT_REQUESTED"
        assert events[2]["sequence_no"] == 3
        assert events[2]["event_type"] == "SUBMIT_ACKED"

    def test_empty_for_unknown_command(self, conn):
        from src.state.venue_command_repo import list_events
        assert list_events(conn, "nonexistent") == []


# ---------------------------------------------------------------------------
# Test 6: NC-18 — no module outside repo writes events (AST walk)
# ---------------------------------------------------------------------------

class TestNoModuleOutsideRepoWritesEvents:
    def test_no_direct_venue_command_events_mutation_outside_repo(self):
        """AST-walk all src/**/*.py for direct mutations on venue_command_events
        or venue_commands UPDATE/DELETE outside the repo module.
        Only src/state/venue_command_repo.py is allowed.
        """
        forbidden_patterns = [
            "INSERT INTO venue_command_events",
            "UPDATE venue_command_events",
            "DELETE FROM venue_command_events",
            "UPDATE venue_commands",
            "DELETE FROM venue_commands",
        ]
        repo_path = str(ROOT / "src/state/venue_command_repo.py")
        violations = []

        src_files = glob.glob(str(ROOT / "src/**/*.py"), recursive=True)
        for filepath in src_files:
            if filepath == repo_path:
                continue
            try:
                source = Path(filepath).read_text()
            except OSError:
                continue
            for pattern in forbidden_patterns:
                if pattern.lower() in source.lower():
                    violations.append(f"{filepath}: contains {pattern!r}")

        assert not violations, (
            "The following files contain direct venue_command_events / venue_commands "
            "mutations outside the repo module:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Test 7: find_command_by_idempotency_key
# ---------------------------------------------------------------------------

class TestFindCommandByIdempotencyKey:
    def test_finds_existing_command(self, conn):
        from src.state.venue_command_repo import find_command_by_idempotency_key
        _insert(conn, command_id="cmd-001", idempotency_key="find-me")
        result = find_command_by_idempotency_key(conn, "find-me")
        assert result is not None
        assert result["command_id"] == "cmd-001"

    def test_returns_none_for_missing_key(self, conn):
        from src.state.venue_command_repo import find_command_by_idempotency_key
        assert find_command_by_idempotency_key(conn, "no-such-key") is None


# ---------------------------------------------------------------------------
# Test 8: payload_json round-trip
# ---------------------------------------------------------------------------

class TestAppendEventPayloadRoundTrip:
    def test_payload_stored_as_json(self, conn):
        import json
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)
        payload = {"venue_order_id": "ord-abc", "status": "ok"}
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z", payload=payload)
        events = list_events(conn, "cmd-001")
        evt = events[1]  # sequence_no=2
        assert evt["payload_json"] is not None
        assert json.loads(evt["payload_json"]) == payload

    def test_none_payload_stored_as_null(self, conn):
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z", payload=None)
        events = list_events(conn, "cmd-001")
        assert events[1]["payload_json"] is None
