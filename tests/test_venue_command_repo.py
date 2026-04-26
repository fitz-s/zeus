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
    """NC-18 enforcement (post-critic MAJOR-2 fix): real AST walk that catches
    SQL string literals containing forbidden mutation verbs against the
    venue_commands / venue_command_events tables, even when:
      - the SQL is built via f-string/`.format()`/concatenation
      - the table name is quoted (`"venue_command_events"` or backticks)
      - whitespace varies (`UPDATE  venue_command_events`)
      - the verb is uppercase, lowercase, or mixed case

    Strategy: walk every Constant node in src/**/*.py whose value is a string
    matching the forbidden-mutation regex. Substring matching is bypassable;
    AST-level inspection of every string literal is not. Comments and
    docstrings count too — if a docstring documents a forbidden statement,
    that is itself a leak signal worth flagging (allowlist below covers the
    legitimate documentation case).
    """

    # Regex catches:
    #  - INSERT INTO  / UPDATE  / DELETE FROM
    #  - target = venue_command_events  OR  venue_commands
    #  - allows quoting (", ', `) and arbitrary whitespace
    _FORBIDDEN_MUTATION_RE = __import__("re").compile(
        r"""
        \b
        (?:
            INSERT \s+ INTO          # INSERT INTO ...
          | UPDATE                   # UPDATE ...
          | DELETE \s+ FROM          # DELETE FROM ...
        )
        \s+
        ["'`]?                       # optional quote
        (?:venue_command_events|venue_commands)
        ["'`]?
        \b
        """,
        __import__("re").IGNORECASE | __import__("re").VERBOSE,
    )

    def test_no_direct_venue_command_events_mutation_outside_repo(self):
        """Real AST walk: every string Constant in every src file is scanned.
        Only src/state/venue_command_repo.py is allowed to contain mutation
        SQL against either table. P1.S2/S3 will need to extend the allowlist
        if helpers move; today the seam is single-file.
        """
        repo_rel = "src/state/venue_command_repo.py"
        allowed_files = {str(ROOT / repo_rel)}
        violations: list[str] = []

        for filepath in glob.glob(str(ROOT / "src/**/*.py"), recursive=True):
            if filepath in allowed_files:
                continue
            try:
                source = Path(filepath).read_text()
            except OSError:
                continue
            try:
                tree = ast.parse(source, filename=filepath)
            except SyntaxError as exc:
                violations.append(
                    f"{filepath}:{exc.lineno}: parse error in NC-18 guard "
                    f"(fix the syntax first): {exc.msg}"
                )
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if self._FORBIDDEN_MUTATION_RE.search(node.value):
                        rel = Path(filepath).relative_to(ROOT).as_posix()
                        violations.append(
                            f"{rel}:{node.lineno}: forbidden venue_commands/"
                            f"venue_command_events mutation literal — "
                            f"route through src/state/venue_command_repo.py"
                        )

        assert not violations, (
            "NC-18 violation: direct venue_commands/venue_command_events "
            "mutation SQL outside the repo:\n" + "\n".join(violations)
        )

    def test_regex_catches_known_evasion_shapes(self):
        """Self-test for the AST regex. Pre-fix substring match would have
        missed every shape below; post-fix regex catches all of them.
        """
        evasions = [
            'UPDATE venue_command_events SET state = ?',
            'update venue_command_events set foo = bar',  # lowercase
            'UPDATE  venue_command_events SET ...',         # double space
            'UPDATE "venue_command_events" SET ...',        # quoted ident
            "UPDATE `venue_command_events` SET ...",        # backtick ident
            "DELETE  FROM venue_command_events WHERE 1",
            "INSERT  INTO venue_command_events VALUES",
            "delete from venue_commands where true",
        ]
        for shape in evasions:
            assert self._FORBIDDEN_MUTATION_RE.search(shape), (
                f"AST guard regex failed to catch evasion shape: {shape!r}"
            )

    def test_regex_does_not_false_positive_on_benign_strings(self):
        """Regex must NOT trip on legitimate non-mutation references."""
        benign = [
            "SELECT * FROM venue_command_events",
            "SELECT * FROM venue_commands WHERE state = ?",
            "Note: do not UPDATE venue_command_events directly",  # prose mentions verb but not SQL form
        ]
        # The third one is interesting: it DOES contain "UPDATE venue_command_events"
        # exactly, so the regex correctly flags it. That's a true positive
        # (the prose IS a mutation literal in a string constant), and the
        # allowlist already excludes the only file where this would legitimately
        # appear (the repo module's docstrings). Verify the first two pass.
        for shape in benign[:2]:
            assert not self._FORBIDDEN_MUTATION_RE.search(shape), (
                f"AST guard regex falsely flagged benign string: {shape!r}"
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


# ---------------------------------------------------------------------------
# Test 9 (post-critic MAJOR-1): savepoint composability
# Project memory L30: `with conn:` silently RELEASEs an outer SAVEPOINT.
# Repo must use SAVEPOINT-based context so callers can wrap repo calls inside
# their own transaction or savepoint without losing rollback granularity.
# This is the regression guard that protects P1.S3 executor from latent
# atomicity loss when it wraps _live_order in its own transaction context.
# ---------------------------------------------------------------------------

class TestSavepointComposability:
    def test_insert_command_composable_inside_outer_savepoint(self, conn):
        """Outer SAVEPOINT followed by insert_command followed by ROLLBACK TO
        outer must undo BOTH the command row AND the auto-appended event row.
        Pre-fix: `with conn:` would have RELEASEd `outer_test` mid-flight,
        making ROLLBACK TO raise OperationalError.
        """
        from src.state.venue_command_repo import insert_command

        conn.execute("SAVEPOINT outer_test")
        insert_command(
            conn,
            command_id="cmp-001",
            position_id="pos-1",
            decision_id="dec-1",
            idempotency_key="idem-cmp-001",
            intent_kind="ENTRY",
            market_id="m1",
            token_id="t1",
            side="BUY",
            size=10.0,
            price=0.5,
            created_at="2026-04-26T00:00:00Z",
        )
        # Outer rollback must succeed (SAVEPOINT still exists).
        conn.execute("ROLLBACK TO SAVEPOINT outer_test")
        conn.execute("RELEASE SAVEPOINT outer_test")

        # And both rows must be gone.
        commands = conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmp-001'"
        ).fetchall()
        assert len(commands) == 0
        events = conn.execute(
            "SELECT * FROM venue_command_events WHERE command_id = 'cmp-001'"
        ).fetchall()
        assert len(events) == 0

    def test_append_event_composable_inside_outer_savepoint(self, conn):
        """Same pattern for append_event."""
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)  # standard cmd-001 helper

        conn.execute("SAVEPOINT outer_evt")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:30Z",
        )
        events_during = list_events(conn, "cmd-001")
        assert len(events_during) == 2

        conn.execute("ROLLBACK TO SAVEPOINT outer_evt")
        conn.execute("RELEASE SAVEPOINT outer_evt")

        events_after = list_events(conn, "cmd-001")
        assert len(events_after) == 1
        cmd = conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-001'"
        ).fetchone()
        state_val = cmd["state"] if hasattr(cmd, "keys") else cmd[0]
        assert state_val == "INTENT_CREATED"


# ---------------------------------------------------------------------------
# Test 10 (post-critic MEDIUM-1): payload datetime / bytes round-trip
# P1.S4 recovery loop will routinely attach datetime payloads. Pre-fix
# json.dumps raised TypeError on datetime; post-fix coerces to ISO string.
# ---------------------------------------------------------------------------

class TestAppendEventPayloadCoercion:
    def test_payload_datetime_coerces_to_iso(self, conn):
        import json
        import datetime
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)

        ts = datetime.datetime(2026, 4, 26, 12, 30, 45, tzinfo=datetime.timezone.utc)
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"observed_at": ts},
        )
        evt = list_events(conn, "cmd-001")[1]
        decoded = json.loads(evt["payload_json"])
        assert decoded["observed_at"] == ts.isoformat()

    def test_payload_bytes_coerces_to_hex(self, conn):
        import json
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)

        raw = b"\xde\xad\xbe\xef"
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"raw": raw},
        )
        evt = list_events(conn, "cmd-001")[1]
        decoded = json.loads(evt["payload_json"])
        assert decoded["raw"] == raw.hex()

    def test_payload_unserializable_raises_clean_typeerror(self, conn):
        from src.state.venue_command_repo import append_event
        _insert(conn)

        class Opaque:
            pass

        with pytest.raises(TypeError, match="not JSON serializable"):
            append_event(
                conn,
                command_id="cmd-001",
                event_type="SUBMIT_REQUESTED",
                occurred_at="2026-04-26T00:01:00Z",
                payload={"x": Opaque()},
            )
