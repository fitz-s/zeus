# Created: 2026-07-03
# Last reused/audited: 2026-08-27
# Authority basis: W4.2 relocation of the persisted-cancel journal engine out of the retired
#   src/execution/maker_rest_escalation.py into src/execution/venue_cancel_journal.py (still used
#   by main._edli_boot_invalid_pending_entry_authority_cancel_once,
#   main._edli_continuous_redecision_screen_cycle, and main._c3_staleness_cancel_cycle's carried-over
#   invalid-entry-authority lane). Retains the generic journaling-contract coverage from the retired
#   tests/execution/test_maker_rest_escalation.py (TestFailSoft/TestPersistedRestCancel) — the
#   deadline/TTL-classification tests (find_expired_resting_entries, run_cancels_for_expired_rests,
#   run_maker_rest_escalation_cycle) retired WITH the deleted module; that classification now lives
#   in tests/test_order_state_predicates.py and tests/execution/test_staleness_cancel.py.
"""Relationship tests for the shared persisted-cancel command-journal executor."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.execution.venue_cancel_journal import (
    claim_screen_redecision_cancel_obligation,
    dispatch_screen_redecision_cancel_obligations,
    finalize_screen_redecision_cancel_obligation,
    find_screen_redecision_cancel_obligations,
    persist_screen_redecision_cancel_obligations,
    run_persisted_cancels_for_expired_rests,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 3, 22, 0, 0, tzinfo=UTC)


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
    # append_event's terminal dispatch (SCH-W1.1-CAS-LEDGER terminalization-centrality
    # invariant) unconditionally reads this table for any command reaching a CANCELLED-
    # class terminal state — must exist (empty is fine; these fixtures never reserve).
    conn.execute(
        """CREATE TABLE collateral_reservations (
            command_id TEXT PRIMARY KEY,
            reservation_type TEXT NOT NULL,
            token_id TEXT,
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            released_at TEXT,
            release_reason TEXT,
            converted_amount INTEGER NOT NULL DEFAULT 0
        )"""
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
):
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            command_id, intent_kind, "m1", "t1", "BUY", 10.0, 0.5,
            venue_order_id, command_state, None,
            created_at.isoformat(), created_at.isoformat(),
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
        ) VALUES (?, ?, 2, 'SUBMIT_ACKED', ?, NULL, 'ACKED')""",
        (f"{command_id}-acked", command_id, created_at.isoformat()),
    )


def _entry(command_id: str, venue_order_id: str, **overrides) -> dict:
    base = {
        "command_id": command_id,
        "venue_order_id": venue_order_id,
        "created_at": (NOW - timedelta(minutes=30)).isoformat(),
        "fact_state": "LIVE",
        "matched_size": "0",
        "cancel_reason": "TEST_CANCEL",
        "cancel_action": "CANCEL_REPLACE",
        "cancel_detail": {"trigger": "test"},
    }
    base.update(overrides)
    return base


def _witness(status: str, matched_size: str) -> dict:
    return {
        "status": status,
        "matched_size": matched_size,
        "source": "authenticated_point_order",
        "captured_at": datetime.now(UTC).isoformat(),
    }


def test_screen_cancel_obligation_persists_marker_without_venue_call(monkeypatch):
    conn = _db()
    _add_order(conn, command_id="screen-1", venue_order_id="order-1")
    conn.commit()
    stats = persist_screen_redecision_cancel_obligations(
        [_entry("screen-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=__import__("time").monotonic() + 1.0,
        close_connections=False,
    )
    assert stats == {"queued": 1, "deferred": 0, "terminal": 0, "errors": 0}
    row = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'screen-1'"
    ).fetchone()
    assert row[0] == "CANCEL_PENDING"
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM venue_command_events WHERE command_id = 'screen-1' "
        "AND event_type = 'CANCEL_REQUESTED'"
    ).fetchone()[0])
    assert payload["cancel_request_kind"] == "screen_redecision_v1"
    assert payload["dispatch_owner"] == "command_recovery"
    conn.close()


def test_deadline_expired_cancel_does_not_start_venue_call():
    import time

    conn = _db()
    _add_order(conn, command_id="expired-1", venue_order_id="order-1")
    conn.commit()
    client = MagicMock()
    stats = run_persisted_cancels_for_expired_rests(
        [_entry("expired-1", "order-1")],
        client,
        conn_factory=lambda: conn,
        close_connections=False,
        deadline_monotonic=time.monotonic() - 0.001,
    )
    assert stats["cancelled"] == 0
    client.cancel_order.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM venue_command_events WHERE event_type='CANCEL_REQUESTED'").fetchone()[0] == 0
    conn.close()


def test_screen_obligation_selector_ignores_legacy_cancel_requested():
    conn = _db()
    _add_order(conn, command_id="legacy-1", venue_order_id="order-1")
    from src.state.venue_command_repo import append_event

    append_event(
        conn,
        command_id="legacy-1",
        event_type="CANCEL_REQUESTED",
        occurred_at=NOW.isoformat(),
        payload={"venue_order_id": "order-1", "source": "maker_rest_escalation"},
    )
    conn.commit()
    assert find_screen_redecision_cancel_obligations(conn) == []
    conn.close()


def test_screen_lease_claim_requires_fresh_witness_and_stale_finalize_is_fenced():
    import time

    conn = _db()
    _add_order(conn, command_id="lease-1", venue_order_id="order-1")
    persist_screen_redecision_cancel_obligations(
        [_entry("lease-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    result = {}
    assert claim_screen_redecision_cancel_obligation(
        conn,
        command_id="lease-1",
        venue_order_id="order-1",
        owner="boot-a:11",
        generation=1,
        attempt_id="attempt-a",
        expires_at=(NOW + timedelta(seconds=5)).isoformat(),
        fresh_witness=_witness("LIVE", "0"),
        result=result,
    )
    assert result["action"] == "dispatch"
    assert result["generation"] == 1
    assert not finalize_screen_redecision_cancel_obligation(
        conn,
        command_id="lease-1",
        venue_order_id="order-1",
        attempt_id="attempt-old",
        expected_last_event_id=result["event_id"],
        event_type="CANCEL_ACKED",
        payload={"venue_order_id": "order-1"},
    )
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id='lease-1'").fetchone()[0] == "CANCEL_PENDING"
    conn.close()


def test_screen_lease_terminal_witness_acks_without_cancel_side_effect():
    import time

    conn = _db()
    _add_order(conn, command_id="terminal-1", venue_order_id="order-1")
    persist_screen_redecision_cancel_obligations(
        [_entry("terminal-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    result = {}
    assert claim_screen_redecision_cancel_obligation(
        conn,
        command_id="terminal-1",
        venue_order_id="order-1",
        owner="boot-a:11",
        generation=1,
        attempt_id="attempt-a",
        expires_at=(NOW + timedelta(seconds=5)).isoformat(),
        fresh_witness=_witness("FILLED", "10"),
        result=result,
    )
    assert result["action"] == "finalized"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id='terminal-1'").fetchone()[0] == "CANCELLED"
    conn.close()


def test_screen_dispatch_cancel_success_and_unknown_are_attempt_fenced():
    import time

    conn = _db()
    conn.execute("PRAGMA busy_timeout = 321")
    _add_order(conn, command_id="dispatch-1", venue_order_id="order-1")
    persist_screen_redecision_cancel_obligations(
        [_entry("dispatch-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    clob = _PointOrderClob(matched_size="0")
    factory_deadlines = []

    def deadline_conn_factory(*, deadline_monotonic):
        factory_deadlines.append(deadline_monotonic)
        return conn

    deadline = time.monotonic() + 1
    stats = dispatch_screen_redecision_cancel_obligations(
        find_screen_redecision_cancel_obligations(conn),
        clob,
        conn_factory=deadline_conn_factory,
        deadline_monotonic=deadline,
        owner="boot-a:11",
        close_connections=False,
    )
    assert stats["cancelled"] == 1
    assert clob.cancelled == ["order-1"]
    assert factory_deadlines and all(value == deadline for value in factory_deadlines)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 321
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id='dispatch-1'").fetchone()[0] == "CANCELLED"
    conn.close()


def test_screen_registered_owner_runs_marker_witness_claim_then_signed_delete(monkeypatch):
    """The registered recovery owner must bind the marker to the signed cancel path."""
    import time
    from types import SimpleNamespace

    import httpx

    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    class RecordingTransport:
        calls = []

        def __init__(self, *, timeout, **_kwargs):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, **kwargs):
            self.__class__.calls.append(kwargs)
            return httpx.Response(200, json={"canceled": ["registered-order"]})

    monkeypatch.setattr(httpx, "Client", RecordingTransport)
    sdk_client = SimpleNamespace(
        host="https://clob.example",
        use_server_time=False,
        signer=Signer(),
        creds=Creds(),
        assert_level_2_auth=lambda: None,
    )
    adapter = PolymarketV2Adapter(
        host="https://clob.example",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=None,
        client_factory=lambda **_kwargs: sdk_client,
    )
    adapter.prepare_order_truth_reader()

    calls = []

    class RegisteredOwnerClient:
        def get_order(self, order_id, *, deadline_monotonic):
            calls.append(("get_order", order_id))
            assert deadline_monotonic > time.monotonic()
            return {
                "orderID": order_id,
                "status": "LIVE",
                "original_size": "26.5",
                "size_matched": "0",
                "captured_at": datetime.now(UTC).isoformat(),
                "source": "authenticated_point_order",
            }

        def cancel_order(self, order_id, *, deadline_monotonic):
            calls.append(("cancel_order", order_id))
            result = adapter.cancel(order_id, deadline_monotonic=deadline_monotonic)
            return {
                "orderID": result.order_id,
                "status": result.status,
                "raw_response_json": result.raw_response_json,
            }

    conn = _db()
    _add_order(conn, command_id="registered-1", venue_order_id="registered-order")
    persist_screen_redecision_cancel_obligations(
        [_entry("registered-1", "registered-order")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    stats = dispatch_screen_redecision_cancel_obligations(
        find_screen_redecision_cancel_obligations(conn),
        RegisteredOwnerClient(),
        conn_factory=lambda *, deadline_monotonic: conn,
        deadline_monotonic=time.monotonic() + 1,
        owner="boot-registered:11",
        close_connections=False,
    )

    assert stats["cancelled"] == 1, (stats, calls)
    assert calls == [("get_order", "registered-order"), ("cancel_order", "registered-order")]
    assert RecordingTransport.calls[0]["method"] == "DELETE"
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM venue_command_events "
            "WHERE command_id = 'registered-1' AND event_type = 'CANCEL_ACKED'"
        ).fetchone()[0]
    )
    assert payload["obligation_id"] == "screen_redecision_v1:registered-1:registered-order"
    assert payload["obligation_kind"] == "screen_redecision_cancel_v1"
    assert payload["owner"] == "command_recovery"
    conn.close()


def test_edli_recovery_cycle_uses_prepared_registered_owner_for_screen_cancel(monkeypatch):
    """The scheduled owner must route its selector and cancel through the prepared client."""
    import time
    from types import SimpleNamespace

    import httpx

    import src.main as main_module
    import src.execution.command_recovery as recovery_module
    import src.state.db as state_db
    from src.data.polymarket_client import PolymarketClient
    from src.venue.polymarket_v2_adapter import OrderState, PolymarketV2Adapter

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    class RecordingTransport:
        calls = []

        def __init__(self, *, timeout, **_kwargs):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, **kwargs):
            self.__class__.calls.append(kwargs)
            return httpx.Response(200, json={"canceled": ["cycle-order"]})

    monkeypatch.setattr(httpx, "Client", RecordingTransport)
    sdk_client = SimpleNamespace(
        host="https://clob.example",
        use_server_time=False,
        signer=Signer(),
        creds=Creds(),
        assert_level_2_auth=lambda: None,
    )
    adapter = PolymarketV2Adapter(
        host="https://clob.example",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=None,
        client_factory=lambda **_kwargs: sdk_client,
    )
    adapter._client = sdk_client
    witness_calls = []

    def prepared_get_order(order_id, *, deadline_monotonic=None):
        witness_calls.append((order_id, deadline_monotonic))
        return OrderState(
            order_id=order_id,
            status="LIVE",
            raw={
                "orderID": order_id,
                "status": "LIVE",
                "original_size": "26.5",
                "size_matched": "0",
                "captured_at": datetime.now(UTC).isoformat(),
                "source": "authenticated_point_order",
            },
        )

    adapter.get_order = prepared_get_order
    conn = _db()
    _add_order(conn, command_id="cycle-1", venue_order_id="cycle-order")
    persist_screen_redecision_cancel_obligations(
        [_entry("cycle-1", "cycle-order")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )

    class OwnedConn:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def set_progress_handler(self, *_args):
            return None

        def close(self):
            return None

    owned_conn = OwnedConn(conn)
    observed = {}
    def fake_reconcile(**kwargs):
        observed.update(kwargs)
        stats = dispatch_screen_redecision_cancel_obligations(
            find_screen_redecision_cancel_obligations(conn),
            kwargs["client"],
            conn_factory=lambda **_kwargs: owned_conn,
            deadline_monotonic=kwargs["deadline_monotonic"],
            owner="cycle-owner:11",
            close_connections=False,
        )
        return {
            "scope": kwargs["scope"],
            "scanned": stats["scanned"],
            "advanced": stats["cancelled"],
            "stayed": stats["deferred"],
            "errors": stats["errors"] + stats["journal_failed"],
        }

    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_consume_live_control_commands", lambda: None)
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda _name: False)
    monkeypatch.setattr(main_module, "_edli_command_recovery_full_bucket", lambda: 11)
    monkeypatch.setattr(main_module, "_EDLI_COMMAND_RECOVERY_LAST_FULL_BUCKET", 11)
    monkeypatch.setattr(main_module, "_venue_heartbeat_adapter", adapter)
    monkeypatch.setattr(main_module, "_consume_edli_command_recovery_summary", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(recovery_module, "scheduled_recovery_budget_seconds", lambda: 1.0)
    monkeypatch.setattr(recovery_module, "capital_blocking_command_count", lambda _conn: 0)
    monkeypatch.setattr(recovery_module, "reconcile_unresolved_commands", fake_reconcile)
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_read_only",
        lambda *, deadline_monotonic: owned_conn,
    )

    main_module._edli_command_recovery_cycle.__wrapped__()

    assert observed["scope"] == "live_tick"
    assert isinstance(observed["client"], PolymarketClient)
    assert observed["client"]._v2_adapter is adapter
    assert witness_calls and witness_calls[0][1] > time.monotonic()
    assert RecordingTransport.calls[0]["method"] == "DELETE"
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cycle-1'"
    ).fetchone()[0] == "CANCELLED"
    conn.close()


def test_edli_recovery_cycle_defers_screen_debt_without_prepared_adapter(monkeypatch):
    import src.main as main_module
    import src.execution.command_recovery as recovery_module
    import src.state.db as state_db

    class FakeConn:
        def set_progress_handler(self, *_args):
            return None

        def close(self):
            return None

    calls = []
    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_consume_live_control_commands", lambda: None)
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda _name: False)
    monkeypatch.setattr(main_module, "_venue_heartbeat_adapter", None)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda *, deadline_monotonic: FakeConn())
    monkeypatch.setattr(recovery_module, "capital_blocking_command_count", lambda _conn: 0)
    monkeypatch.setattr(
        recovery_module,
        "reconcile_unresolved_commands",
        lambda **_kwargs: calls.append(True),
    )
    from src.execution import venue_cancel_journal as journal_module

    monkeypatch.setattr(
        journal_module,
        "find_screen_redecision_cancel_obligations",
        lambda _conn: [{"command_id": "screen-debt", "venue_order_id": "order-debt"}],
    )

    main_module._edli_command_recovery_cycle.__wrapped__()

    assert calls == []


def test_edli_recovery_cycle_screen_cancel_debt_bypasses_monitor_yield(monkeypatch):
    from types import SimpleNamespace

    import src.main as main_module
    import src.execution.command_recovery as recovery_module
    import src.state.db as state_db
    from src.execution import venue_cancel_journal as journal_module

    class FakeConn:
        def set_progress_handler(self, *_args):
            return None

        def close(self):
            return None

    recovery_calls = []
    defer_calls = []

    def fake_reconcile(**kwargs):
        recovery_calls.append(kwargs)
        return {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}

    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_consume_live_control_commands", lambda: None)
    monkeypatch.setattr(
        main_module,
        "_defer_for_held_position_monitor",
        lambda name: defer_calls.append(name) or True,
    )
    monkeypatch.setattr(
        main_module,
        "_held_position_monitor_active",
        SimpleNamespace(is_set=lambda: True),
    )
    monkeypatch.setattr(
        main_module,
        "_held_position_monitor_canonical_debt",
        SimpleNamespace(is_set=lambda: True),
    )
    monkeypatch.setattr(main_module, "_venue_heartbeat_adapter", SimpleNamespace(_client=object()))
    monkeypatch.setattr(main_module, "_edli_command_recovery_full_bucket", lambda: 11)
    monkeypatch.setattr(main_module, "_EDLI_COMMAND_RECOVERY_LAST_FULL_BUCKET", 11)
    monkeypatch.setattr(
        main_module,
        "_consume_edli_command_recovery_summary",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(recovery_module, "scheduled_recovery_budget_seconds", lambda: 1.0)
    monkeypatch.setattr(recovery_module, "capital_blocking_command_count", lambda _conn: 0)
    monkeypatch.setattr(recovery_module, "reconcile_unresolved_commands", fake_reconcile)
    monkeypatch.setattr(
        journal_module,
        "find_screen_redecision_cancel_obligations",
        lambda _conn: [{"command_id": "screen-debt", "venue_order_id": "order-debt"}],
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_read_only",
        lambda *, deadline_monotonic: FakeConn(),
    )

    main_module._edli_command_recovery_cycle.__wrapped__()

    assert [call["scope"] for call in recovery_calls] == ["live_tick"]
    assert defer_calls == []


def test_edli_recovery_cycle_selector_uses_absolute_deadline_and_typed_defer(monkeypatch):
    import sqlite3

    import src.main as main_module
    import src.execution.command_recovery as recovery_module
    import src.state.db as state_db

    observed = {}
    calls = []

    def bounded_readonly(*, deadline_monotonic):
        observed["deadline"] = deadline_monotonic
        raise sqlite3.OperationalError("DB_CONNECTION_DEADLINE_EXPIRED")

    monkeypatch.setattr(main_module, "get_mode", lambda: "live")
    monkeypatch.setattr(main_module, "_consume_live_control_commands", lambda: None)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", bounded_readonly)
    monkeypatch.setattr(recovery_module, "scheduled_recovery_budget_seconds", lambda: 0.1)
    monkeypatch.setattr(
        recovery_module,
        "reconcile_unresolved_commands",
        lambda **_kwargs: calls.append(True),
    )

    main_module._edli_command_recovery_cycle.__wrapped__()

    assert observed["deadline"] > 0
    assert calls == []


def test_screen_active_lease_defers_and_expired_lease_reclaims_next_generation():
    import time

    now = datetime.now(UTC)
    conn = _db()
    _add_order(conn, command_id="lease-2", venue_order_id="order-2")
    persist_screen_redecision_cancel_obligations(
        [_entry("lease-2", "order-2")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    first = {}
    assert claim_screen_redecision_cancel_obligation(
        conn,
        command_id="lease-2",
        venue_order_id="order-2",
        owner="boot-a:11",
        generation=1,
        attempt_id="attempt-a",
        expires_at=(now + timedelta(seconds=60)).isoformat(),
        fresh_witness=_witness("LIVE", "0"),
        result=first,
    )
    active = {}
    assert not claim_screen_redecision_cancel_obligation(
        conn,
        command_id="lease-2",
        venue_order_id="order-2",
        owner="boot-b:22",
        generation=2,
        attempt_id="attempt-b",
        expires_at=(now + timedelta(seconds=61)).isoformat(),
        fresh_witness=_witness("LIVE", "0"),
        result=active,
    )
    assert active["action"] == "active_lease"
    conn.execute(
        "UPDATE venue_command_events SET payload_json = ? WHERE event_type = 'CANCEL_DISPATCH_STARTED'",
        (json.dumps({
            "schema_version": 1,
            "obligation_id": "screen_redecision_v1:lease-2:order-2",
            "obligation_kind": "screen_redecision_cancel_v1",
            "owner": "command_recovery",
            "owner_boot_id": "boot-a",
            "owner_pid": "11",
            "generation": 1,
            "attempt_id": "attempt-a",
            "expires_at": (now - timedelta(seconds=1)).isoformat(),
            "venue_order_id": "order-2",
        }),),
    )
    conn.commit()
    expired = {}
    assert claim_screen_redecision_cancel_obligation(
        conn,
        command_id="lease-2",
        venue_order_id="order-2",
        owner="boot-b:22",
        generation=2,
        attempt_id="attempt-b",
        expires_at=(now + timedelta(seconds=61)).isoformat(),
        fresh_witness=_witness("LIVE", "0"),
        result=expired,
    )
    assert expired["generation"] == 2
    conn.close()


def test_screen_two_connection_claims_are_single_flight(tmp_path):
    import time

    path = tmp_path / "trades.db"
    first_conn = sqlite3.connect(path)
    second_conn = sqlite3.connect(path)
    first_conn.row_factory = sqlite3.Row
    second_conn.row_factory = sqlite3.Row
    schema = _db()
    for statement in schema.iterdump():
        if statement.startswith("BEGIN") or statement.startswith("COMMIT"):
            continue
        first_conn.execute(statement)
    schema.close()
    _add_order(first_conn, command_id="race-1", venue_order_id="order-1")
    first_conn.commit()
    persist_screen_redecision_cancel_obligations(
        [_entry("race-1", "order-1")],
        conn_factory=lambda: first_conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    first_conn.commit()
    entry = find_screen_redecision_cancel_obligations(second_conn)[0]
    first = {}
    second = {}
    assert claim_screen_redecision_cancel_obligation(
        first_conn,
        command_id="race-1",
        venue_order_id="order-1",
        owner="boot-a:11",
        generation=1,
        attempt_id="attempt-a",
        expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        fresh_witness=_witness("LIVE", "0"),
        result=first,
    )
    assert not claim_screen_redecision_cancel_obligation(
        second_conn,
        command_id=entry["command_id"],
        venue_order_id=entry["venue_order_id"],
        owner="boot-b:22",
        generation=1,
        attempt_id="attempt-b",
        expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        fresh_witness=_witness("LIVE", "0"),
        result=second,
    )
    assert second["action"] == "active_lease"
    first_conn.close()
    second_conn.close()


def test_screen_witness_age_and_incompatible_client_are_fail_closed():
    import time

    conn = _db()
    _add_order(conn, command_id="age-1", venue_order_id="order-1")
    persist_screen_redecision_cancel_obligations(
        [_entry("age-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    stale = _witness("LIVE", "0")
    stale["captured_at"] = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    result = {}
    assert not claim_screen_redecision_cancel_obligation(
        conn,
        command_id="age-1",
        venue_order_id="order-1",
        owner="boot-a:11",
        generation=1,
        attempt_id="attempt-a",
        expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        fresh_witness=stale,
        result=result,
    )
    assert result["action"] == "witness_stale"

    class NoDeadlineClient:
        def get_order(self, _order_id):
            return {"orderID": "order-1", "status": "LIVE", "size_matched": "0"}

    stats = dispatch_screen_redecision_cancel_obligations(
        find_screen_redecision_cancel_obligations(conn),
        NoDeadlineClient(),
        conn_factory=lambda *, deadline_monotonic: conn,
        deadline_monotonic=time.monotonic() + 1,
        owner="boot-a:11",
        close_connections=False,
    )
    assert stats["deferred"] == 1
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id='age-1'").fetchone()[0] == "CANCEL_PENDING"
    conn.close()


def test_screen_post_deadline_cancel_keeps_started_debt():
    import time

    conn = _db()
    _add_order(conn, command_id="late-1", venue_order_id="order-1")
    persist_screen_redecision_cancel_obligations(
        [_entry("late-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )

    class LateCancel(_PointOrderClob):
        def cancel_order(self, order_id: str, *, deadline_monotonic=None):
            time.sleep(0.02)
            raise TimeoutError("deadline")

    deadline = time.monotonic() + 0.01
    stats = dispatch_screen_redecision_cancel_obligations(
        find_screen_redecision_cancel_obligations(conn),
        LateCancel(matched_size="0"),
        conn_factory=lambda *, deadline_monotonic: conn,
        deadline_monotonic=deadline,
        owner="boot-a:11",
        close_connections=False,
    )
    assert stats["journal_failed"] == 1
    row = conn.execute(
        "SELECT state, event_type FROM venue_commands JOIN venue_command_events "
        "ON venue_commands.last_event_id = venue_command_events.event_id WHERE venue_commands.command_id='late-1'"
    ).fetchone()
    assert tuple(row) == ("CANCEL_PENDING", "CANCEL_DISPATCH_STARTED")
    conn.close()


def test_screen_cancel_without_deadline_contract_never_falls_back_to_http():
    import time

    conn = _db()
    _add_order(conn, command_id="compat-1", venue_order_id="order-1")
    persist_screen_redecision_cancel_obligations(
        [_entry("compat-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )

    class LateCallClient:
        def get_order(self, _order_id, *, deadline_monotonic=None):
            return {"orderID": "order-1", "status": "LIVE", "size_matched": "0"}

        def cancel_order(self, _order_id):
            raise AssertionError("deadline-incompatible cancel must not reach HTTP")

    stats = dispatch_screen_redecision_cancel_obligations(
        find_screen_redecision_cancel_obligations(conn),
        LateCallClient(),
        conn_factory=lambda *, deadline_monotonic: conn,
        deadline_monotonic=time.monotonic() + 1,
        owner="boot-a:11",
        close_connections=False,
    )
    assert stats["journal_failed"] == 1
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id='compat-1'").fetchone()[0] == "CANCEL_PENDING"
    conn.close()


def test_screen_claim_lock_contention_is_deadline_deferred(tmp_path):
    import threading
    import time

    path = tmp_path / "locked-trades.db"
    first_conn = sqlite3.connect(path)
    second_conn = sqlite3.connect(path, check_same_thread=False)
    first_conn.row_factory = sqlite3.Row
    second_conn.row_factory = sqlite3.Row
    schema = _db()
    for statement in schema.iterdump():
        if statement.startswith("BEGIN") or statement.startswith("COMMIT"):
            continue
        first_conn.execute(statement)
    schema.close()
    _add_order(first_conn, command_id="lock-1", venue_order_id="order-1")
    first_conn.commit()
    persist_screen_redecision_cancel_obligations(
        [_entry("lock-1", "order-1")],
        conn_factory=lambda: first_conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    first_conn.commit()
    first_conn.execute("BEGIN IMMEDIATE")
    result = {}
    worker = threading.Thread(
        target=claim_screen_redecision_cancel_obligation,
        kwargs={
            "conn": second_conn,
            "command_id": "lock-1",
            "venue_order_id": "order-1",
            "owner": "boot-b:22",
            "generation": 1,
            "attempt_id": "attempt-b",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
            "fresh_witness": _witness("LIVE", "0"),
            "result": result,
            "deadline_monotonic": time.monotonic() + 0.03,
        },
    )
    worker.start()
    worker.join(timeout=1)
    first_conn.rollback()
    assert not worker.is_alive()
    assert result["action"] == "sqlite_busy_deferred"
    first_conn.close()
    second_conn.close()


def test_screen_expired_deadline_does_not_open_claim_connection():
    import time

    conn = _db()
    _add_order(conn, command_id="expired-open-1", venue_order_id="order-1")
    persist_screen_redecision_cancel_obligations(
        [_entry("expired-open-1", "order-1")],
        conn_factory=lambda: conn,
        deadline_monotonic=time.monotonic() + 1,
        close_connections=False,
    )
    opened = []

    def forbidden_factory(*, deadline_monotonic):
        opened.append(deadline_monotonic)
        raise AssertionError("expired deadline must not create a DB connection")

    stats = dispatch_screen_redecision_cancel_obligations(
        find_screen_redecision_cancel_obligations(conn),
        _PointOrderClob(matched_size="0"),
        conn_factory=forbidden_factory,
        deadline_monotonic=time.monotonic() - 0.001,
        owner="boot-a:11",
        close_connections=False,
    )
    assert stats["deferred"] == 1
    assert opened == []
    conn.close()


class _FakeClob:
    def __init__(self, fail_on: set[str] | None = None):
        self.cancelled: list[str] = []
        self._fail_on = fail_on or set()

    def cancel_order(self, order_id: str, *, deadline_monotonic=None):
        if order_id in self._fail_on:
            raise RuntimeError("venue cancel error")
        self.cancelled.append(order_id)
        return {"canceled": [order_id]}


class _PointOrderClob(_FakeClob):
    def __init__(self, *, matched_size: str):
        super().__init__()
        self.matched_size = matched_size

    def get_order(self, order_id: str, *, deadline_monotonic=None):
        return {
            "orderID": order_id,
            "status": "LIVE",
            "original_size": "26.5",
            "size_matched": self.matched_size,
        }


class TestPersistedRestCancel:
    def test_current_zero_or_executable_fill_keeps_normal_cancel_path(self):
        for matched_size in ("0", "5", "8"):
            conn = _db()
            _add_order(conn, command_id="c1", venue_order_id="o1")
            clob = _PointOrderClob(matched_size=matched_size)

            stats = run_persisted_cancels_for_expired_rests(
                [_entry("c1", "o1", min_order_size="5")],
                clob,
                conn_factory=lambda: conn,
                close_connections=False,
            )

            assert stats["cancelled"] == 1
            assert stats["cancel_failed"] == 0
            assert clob.cancelled == ["o1"]

    def test_current_partial_fill_below_venue_min_defers_cancel_before_side_effect(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        conn.execute(
            """CREATE TABLE venue_order_facts (
                venue_order_id TEXT, command_id TEXT, state TEXT,
                matched_size TEXT, local_sequence INTEGER)"""
        )
        conn.execute(
            """CREATE TABLE venue_trade_facts (
                trade_id TEXT, venue_order_id TEXT, command_id TEXT, state TEXT,
                filled_size TEXT, local_sequence INTEGER)"""
        )
        conn.execute(
            "INSERT INTO venue_order_facts VALUES ('o1','c1','LIVE','0',1)"
        )
        conn.execute(
            "INSERT INTO venue_trade_facts VALUES ('t1','o1','c1','CONFIRMED','1.724135',2)"
        )
        clob = _PointOrderClob(matched_size="1.724135")

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1", min_order_size="5")],
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
        )

        assert stats == {
            "scanned": 1, "cancelled": 0, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        assert clob.cancelled == []
        assert [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            )
        ] == ["INTENT_CREATED", "SUBMIT_ACKED"]

    def test_current_fill_truth_failure_does_not_cancel_blind(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")

        class FailingPointOrderClob(_FakeClob):
            def get_order(self, _order_id: str, *, deadline_monotonic=None):
                raise TimeoutError("point-order timeout")

        clob = FailingPointOrderClob()
        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1", min_order_size="5")],
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
        )

        assert stats == {
            "scanned": 1, "cancelled": 0, "cancel_failed": 1, "cancel_journal_failed": 0,
        }
        assert clob.cancelled == []

    def test_persisted_cancel_records_command_terminal_state_before_harvest(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        collected: list[dict] = []
        clob = _FakeClob()

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")],
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
            collect_cancelled=collected,
        )

        assert stats == {
            "scanned": 1, "cancelled": 1, "cancel_failed": 0, "cancel_journal_failed": 0,
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

    def test_cancel_unknown_event_carries_recovery_semantics(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        clob = _FakeClob(fail_on={"o1"})

        run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], clob, conn_factory=lambda: conn, close_connections=False,
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

    def test_cancel_not_canceled_is_recoverable_unknown_not_cancel_failed(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")

        class NotCanceledClob:
            def cancel_order(self, _order_id: str):
                return {
                    "orderID": "o1",
                    "status": "NOT_CANCELED",
                    "errorMessage": "order still live after cancel request",
                }

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], NotCanceledClob(),
            conn_factory=lambda: conn, close_connections=False,
        )

        events = [
            (row["event_type"], json.loads(row["payload_json"] or "{}"))
            for row in conn.execute(
                "SELECT event_type, payload_json FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            )
        ]
        assert stats == {
            "scanned": 1, "cancelled": 0, "cancel_failed": 1, "cancel_journal_failed": 0,
        }
        assert events[-1][0] == "CANCEL_REPLACE_BLOCKED"
        assert "CANCEL_FAILED" not in [event_type for event_type, _ in events]
        assert events[-1][1]["semantic_cancel_status"] == "CANCEL_UNKNOWN"
        assert events[-1][1]["requires_m5_reconcile"] is True

    def test_ambiguous_already_canceled_or_matched_is_recoverable_unknown(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")

        class AmbiguousClob:
            def cancel_order(self, _order_id: str):
                return {
                    "orderID": "o1",
                    "status": "NOT_CANCELED",
                    "errorMessage": "order can't be found - already canceled or matched",
                }

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], AmbiguousClob(),
            conn_factory=lambda: conn, close_connections=False,
        )

        events = [
            (row["event_type"], json.loads(row["payload_json"] or "{}"))
            for row in conn.execute(
                "SELECT event_type, payload_json FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            )
        ]
        assert stats == {
            "scanned": 1, "cancelled": 0, "cancel_failed": 1, "cancel_journal_failed": 0,
        }
        assert events[-1][0] == "CANCEL_REPLACE_BLOCKED"
        assert "CANCEL_ACKED" not in [event_type for event_type, _ in events]
        assert events[-1][1]["semantic_cancel_status"] == "CANCEL_UNKNOWN"
        assert events[-1][1]["requires_m5_reconcile"] is True

    def test_terminal_command_race_does_not_append_cancel_replace_blocked(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")

        class RaceClob:
            def cancel_order(self, _order_id: str):
                conn.execute(
                    "UPDATE venue_commands SET state = 'CANCELLED' WHERE command_id = 'c1'"
                )
                conn.commit()
                raise RuntimeError("matched orders can't be canceled")

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], RaceClob(),
            conn_factory=lambda: conn, close_connections=False,
        )

        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            ).fetchall()
        ]
        assert stats == {
            "scanned": 1, "cancelled": 0, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        assert event_types[-1] == "CANCEL_REQUESTED"
        assert "CANCEL_REPLACE_BLOCKED" not in event_types

    def test_pre_cancel_journal_lock_retry_is_idempotent_after_request_committed(self, monkeypatch):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        collected: list[dict] = []
        clob = _FakeClob()

        import src.state.venue_command_repo as command_repo

        real_append_event = command_repo.append_event
        calls = {"count": 0}

        def lock_after_cancel_requested_committed(conn, *, command_id, event_type, occurred_at, payload):
            calls["count"] += 1
            event_id = real_append_event(
                conn, command_id=command_id, event_type=event_type,
                occurred_at=occurred_at, payload=payload,
            )
            if event_type == "CANCEL_REQUESTED" and calls["count"] == 1:
                conn.commit()
                raise sqlite3.OperationalError("database is locked")
            return event_id

        monkeypatch.setattr(command_repo, "append_event", lock_after_cancel_requested_committed)

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], clob,
            conn_factory=lambda: conn, close_connections=False, collect_cancelled=collected,
        )

        assert stats == {
            "scanned": 1, "cancelled": 1, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        assert clob.cancelled == ["o1"]
        assert [entry["command_id"] for entry in collected] == ["c1"]
        events = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            ).fetchall()
        ]
        assert events.count("CANCEL_REQUESTED") == 1
        assert events[-2:] == ["CANCEL_REQUESTED", "CANCEL_ACKED"]

    def test_persisted_cancel_immediately_voids_zero_fill_pending_entry_projection(
        self, monkeypatch
    ):
        import src.execution.command_recovery as command_recovery
        import src.execution.venue_cancel_journal as cancel_journal

        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.state.collateral_ledger import init_collateral_schema
        from src.state.db import init_schema
        from src.state.entry_exposure_obligation import open_entry_exposure_obligation
        from src.state.schema.entry_exposure_obligations_schema import (
            ensure_table as ensure_entry_exposure_obligations_table,
        )
        from tests.test_command_recovery import (
            _advance_to_acked,
            _append_order_fact,
            _insert,
            _insert_decision_log_trade_case_for_recovery,
        )

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        init_collateral_schema(conn)
        ensure_entry_exposure_obligations_table(conn)
        _insert(conn, size=13.45, price=0.68)
        _advance_to_acked(conn, venue_order_id="ord-live")
        _append_order_fact(
            conn, order_id="ord-live", state="LIVE",
            matched_size="0", remaining_size="13.45", source="REST",
        )
        open_entry_exposure_obligation(
            conn,
            command_id="cmd-001",
            owner_domain="trade",
            token_id="tok-001",
            shares=13.45,
            cost_basis_usd=9.146,
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        mock_client = MagicMock(
            spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info", "v2_preflight"]
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        live_summary = reconcile_unresolved_commands(conn, mock_client)
        assert live_summary["live_entry_projection_repair"]["advanced"] == 1
        assert conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()[0] == "pending_entry"

        real_terminal_reconcile = command_recovery.reconcile_terminal_order_facts
        terminal_calls = {"count": 0}

        def locked_once(*args, **kwargs):
            terminal_calls["count"] += 1
            if terminal_calls["count"] == 1:
                return {"scanned": 1, "advanced": 0, "stayed": 0, "errors": 1}
            return real_terminal_reconcile(*args, **kwargs)

        monkeypatch.setattr(command_recovery, "reconcile_terminal_order_facts", locked_once)
        monkeypatch.setattr(cancel_journal.time, "sleep", lambda _seconds: None)

        clob = _FakeClob()
        stats = run_persisted_cancels_for_expired_rests(
            [
                {
                    "command_id": "cmd-001",
                    "venue_order_id": "ord-live",
                    "token_id": "tok-001",
                    "market_id": "mkt-001",
                    "created_at": "2026-04-26T00:00:00Z",
                    "fact_state": "LIVE",
                    "matched_size": "0",
                    "cancel_reason": "CONFIRMED_VALUE_REFRESH",
                    "cancel_action": "CANCEL_REPLACE",
                }
            ],
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
        )

        assert stats == {
            "scanned": 1, "cancelled": 1, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        assert terminal_calls["count"] == 2
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "voided", "shares": 0.0, "cost_basis_usd": 0.0, "order_status": "canceled",
        }
        events = conn.execute(
            """
            SELECT event_type
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [row["event_type"] for row in events] == [
            "POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_VOIDED",
        ]
        obligation = conn.execute(
            "SELECT status, resolved_at FROM entry_exposure_obligations "
            "WHERE command_id = 'cmd-001'"
        ).fetchone()
        assert obligation["status"] == "RESOLVED"
        assert obligation["resolved_at"] is not None


class TestDeadlineMinutesLogFallback:
    """None of the 4 production call sites pass deadline_minutes to
    run_persisted_cancels_for_expired_rests. The CANCEL_ACKED "cancelled expired
    rest" branch (entries with an empty cancel_reason -- the code path this
    function's own deadline= log field was written for) must fall back to the
    live TTL owner's operating value, not a hardcoded stand-in that would
    misreport "deadline=0min" to on-call."""

    def test_no_deadline_minutes_arg_logs_the_bootstrap_ttl_value(self, caplog):
        from src.state.order_state_predicates import bootstrap_rest_deadline_minutes

        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        clob = _FakeClob()

        with caplog.at_level("INFO", logger="zeus.venue_cancel_journal"):
            run_persisted_cancels_for_expired_rests(
                [_entry("c1", "o1", cancel_reason="")],
                clob, conn_factory=lambda: conn, close_connections=False,
            )

        [record] = [r for r in caplog.records if "cancelled expired rest" in r.getMessage()]
        expected = f"deadline={bootstrap_rest_deadline_minutes():.0f}min"
        assert expected in record.getMessage()
        assert "deadline=0min" not in record.getMessage()
