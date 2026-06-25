# Created: 2026-06-11
# Lifecycle: created=2026-06-11
# Purpose: ANTIBODY for the dependency_db_locked category — pin that the EDLI
#   command-recovery sweep never holds a DB connection across venue/network I/O
#   and never threads one connection across multiple passes, while preserving
#   byte-identical reconciliation events vs the legacy long-connection path.
# Reuse: Run when command_recovery orchestration, venue_sync_contract, or the
#   scheduled _edli_command_recovery_cycle connection topology changes.
# Last reused/audited: 2026-06-17
# Authority basis: operator directive 2026-06-11 ("cleanest STRUCTURAL fix") +
#   the dependency_db_locked live incident (riskguard DATA_DEGRADED since ~03:36Z).
"""Relationship tests for the three-phase venue/DB sync contract.

THE CROSS-MODULE INVARIANT THESE TESTS PIN
------------------------------------------
When ``reconcile_unresolved_commands`` runs on the scheduled-job lane
(``conn is None``), the boundary between the DB-connection module (SQLite write
lock) and the venue-client module (blocking REST I/O) must satisfy:

  (R1) No venue client call occurs while ANY DB connection is open.
  (R2) No single DB connection spans more than one reconcile sub-pass.
  (R3) The reconciliation events written are byte-identical to the legacy
       caller-owned-connection path on the same seeded fixture.

R1 + R2 are the structural properties that make the dependency_db_locked
category unconstructable; R3 proves the connection-topology refactor changed no
reconciliation semantics.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Instrumentation: a connection factory that records open/close spans and a
# client that records, at each call, the set of connections open at that moment.
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.events: list[tuple] = []          # ("open"|"close", conn_id, label)
        self.client_calls: list[tuple] = []     # (method, open_conn_ids_at_call_time)
        self._open: dict[int, str] = {}
        self._seq = 0

    def on_open(self, conn, label):
        cid = id(conn)
        self._open[cid] = label
        self.events.append(("open", cid, label))

    def on_close(self, conn):
        cid = id(conn)
        self._open.pop(cid, None)
        self.events.append(("close", cid, None))

    def on_client_call(self, method):
        self.client_calls.append((method, set(self._open.keys()), dict(self._open)))


def _make_conn_factory(db_path: Path, recorder: _Recorder):
    from src.state.db import init_schema

    class _RecordingConnection(sqlite3.Connection):
        def close(self):
            recorder.on_close(self)
            return super().close()

    def factory():
        c = sqlite3.connect(str(db_path), factory=_RecordingConnection)
        c.row_factory = sqlite3.Row
        init_schema(c)
        recorder.on_open(c, "factory")
        return c

    return factory


class _RecordingClient:
    """Venue client whose every method records the connections open at call time."""

    _NETWORK = ("get_order", "get_open_orders", "get_trades",
                "find_order_by_idempotency_key", "get_clob_market_info")

    def __init__(self, recorder: _Recorder, *, orders=None):
        self._recorder = recorder
        self._orders = orders or {}

    def get_order(self, order_id):
        self._recorder.on_client_call("get_order")
        return self._orders.get(str(order_id))

    def get_open_orders(self):
        self._recorder.on_client_call("get_open_orders")
        return []

    def get_trades(self):
        self._recorder.on_client_call("get_trades")
        return []

    def find_order_by_idempotency_key(self, key):
        self._recorder.on_client_call("find_order_by_idempotency_key")
        return None

    def get_clob_market_info(self, condition_id):
        self._recorder.on_client_call("get_clob_market_info")
        return {}


def test_capture_snapshot_reads_account_surfaces_from_v2_adapter_when_outer_client_lacks_trades():
    from src.execution import venue_sync_contract as vsc

    class Adapter:
        def get_open_orders(self):
            return [{"id": "adapter-order"}]

        def get_trades(self):
            return [{"id": "adapter-trade"}]

    class OuterClient:
        def __init__(self):
            self.adapter = Adapter()

        def get_open_orders(self):
            return [{"id": "outer-order"}]

        def _ensure_v2_adapter(self):
            return self.adapter

    snapshot = vsc.capture_venue_read_snapshot(OuterClient(), order_ids=[])

    assert snapshot.get_open_orders() == [{"id": "outer-order"}]
    assert snapshot.get_trades() == [{"id": "adapter-trade"}]


# ---------------------------------------------------------------------------
# R1 + R2: runtime interleaving
# ---------------------------------------------------------------------------

def test_no_client_call_while_any_connection_open(monkeypatch, tmp_path):
    """R1: zero venue client calls occur while any DB connection is open.

    Drives the scheduled-job lane (conn=None) with an instrumented connection
    factory and recording client. A SUBMITTING command with a venue_order_id is
    seeded so the in-flight scan would, in the diseased shape, do get_order while
    holding the write connection.
    """
    import tests.test_command_recovery as h  # reuse the INV-31 seeding helpers
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-fixture.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-iface")
    h._advance_to_submitting(seed_conn, command_id="cmd-iface", venue_order_id="vord-iface")
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(recorder, orders={"vord-iface": {"orderID": "vord-iface", "status": "LIVE"}})

    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    command_recovery.reconcile_unresolved_commands(conn=None, client=client)

    # At least one venue call must have happened (the seeded SUBMITTING lookup),
    # otherwise the test would vacuously pass.
    assert recorder.client_calls, "expected at least one venue client call to exercise the seam"
    for method, open_ids, open_labels in recorder.client_calls:
        assert not open_ids, (
            f"venue call {method} occurred while {len(open_ids)} DB connection(s) "
            f"were open: {open_labels} — connection held across network I/O "
            f"(dependency_db_locked category)"
        )


def test_live_tick_scope_defers_heavy_recovery_passes(monkeypatch, tmp_path):
    """The order-daemon cadence reconciles in-flight commands without full sweep work."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-tick.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-live-tick")
    h._advance_to_submitting(seed_conn, command_id="cmd-live-tick", venue_order_id="vord-live-tick")
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={"vord-live-tick": {"orderID": "vord-live-tick", "status": "LIVE"}},
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["scope"] == "live_tick"
    assert summary["deferred_full_sweep"] is True
    assert summary["scanned"] == 1
    assert "partial_remainders" not in summary
    assert "recorded_maker_fill_economics" not in summary


def test_no_connection_spans_more_than_one_pass(monkeypatch, tmp_path):
    """R2: every connection's open..close window contains at most one sub-pass.

    We approximate "sub-pass" by counting: a connection that is opened and later
    closed defines one span; no client call may straddle two spans, and the
    snapshot/apply connections must each be distinct short-lived objects (never
    one connection reused across passes). We assert that no connection id is
    opened, closed, and then opened AGAIN (reuse across passes), and that opens
    and closes are balanced (every connection is closed).
    """
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-span.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-span")
    h._advance_to_submitting(seed_conn, command_id="cmd-span", venue_order_id="vord-span")
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(recorder, orders={"vord-span": {"orderID": "vord-span", "status": "LIVE"}})
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    command_recovery.reconcile_unresolved_commands(conn=None, client=client)

    # Spans must be strictly SEQUENTIAL and non-overlapping: the event stream is
    # open, close, open, close, ... with depth never exceeding 1. A depth > 1
    # would mean one connection was still open when another was opened — i.e. a
    # connection threaded across (at least) the boundary into the next pass.
    # (Connection `id()` values may be recycled by the allocator after close, so
    # depth — not id-uniqueness — is the reliable invariant.)
    depth = 0
    max_depth = 0
    n_opens = 0
    for kind, _cid, _label in recorder.events:
        if kind == "open":
            depth += 1
            n_opens += 1
        else:
            depth -= 1
        max_depth = max(max_depth, depth)
    assert depth == 0, "every recovery connection must be closed (no leak holding the write lock)"
    assert max_depth == 1, (
        f"connection nesting depth reached {max_depth} — a connection was still "
        f"open when another was opened, i.e. a connection spanned into another "
        f"pass (dependency_db_locked category). Required: strictly sequential "
        f"per-pass short connections (max depth 1)."
    )
    # There must be MORE than one short-lived connection (proves per-pass short
    # conns, not one long connection threaded through the whole sweep).
    assert n_opens > 1, "expected multiple short-lived per-pass connections"


# ---------------------------------------------------------------------------
# Structural / AST: the orchestration never passes a live connection into a
# client-taking call inside the network phase, and the contract's assertion is
# wired at the network boundary.
# ---------------------------------------------------------------------------

def test_contract_network_phase_asserts_no_open_connection():
    """run_three_phase must assert no connection is open before the network phase."""
    from src.execution import venue_sync_contract

    src = (ROOT / "src/execution/venue_sync_contract.py").read_text()
    tree = ast.parse(src)
    run_three_phase = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_three_phase"
    )
    body_src = ast.get_source_segment(src, run_three_phase)
    assert "assert_no_open_connection" in body_src, (
        "run_three_phase must call assert_no_open_connection before the network phase"
    )
    # The assertion must appear BEFORE the network() call in source order.
    assert body_src.index("assert_no_open_connection") < body_src.index("network("), (
        "assert_no_open_connection must precede the network phase invocation"
    )


def test_capture_snapshot_runs_off_connection_at_runtime():
    """assert_no_open_connection raises if a tracked connection is open."""
    from src.execution import venue_sync_contract as vsc

    factory = lambda: sqlite3.connect(":memory:")  # noqa: E731
    # No connection open -> does not raise.
    vsc.assert_no_open_connection("test.clean")
    # A tracked open connection -> capture must refuse.
    with vsc.open_tracked(factory, label="test.held"):
        with pytest.raises(vsc.ConnectionHeldAcrossIOError):
            vsc.assert_no_open_connection("test.during_hold")
        with pytest.raises(vsc.ConnectionHeldAcrossIOError):
            vsc.capture_venue_read_snapshot(
                _RecordingClient(_Recorder()),
                order_ids=["x"],
            )


# ---------------------------------------------------------------------------
# R3: golden regression — scheduled lane vs legacy lane produce identical events
# ---------------------------------------------------------------------------

def _seed_recovery_scenario(conn):
    """Seed a SUBMITTING+venue_order_id command (the canonical recovery case)."""
    import tests.test_command_recovery as h

    h._insert(conn, command_id="cmd-gold")
    h._advance_to_submitting(conn, command_id="cmd-gold", venue_order_id="vord-gold")
    conn.commit()


def _all_command_events(conn, command_id):
    from src.state.venue_command_repo import list_events

    rows = list_events(conn, command_id)
    # Normalise to (event_type, payload_json) tuples; drop volatile ids/timestamps
    # that legitimately differ (event_id, occurred_at are wall-clock).
    out = []
    for r in rows:
        m = r if isinstance(r, dict) else dict(r)
        out.append((m.get("event_type"), m.get("payload_json")))
    return out


def test_golden_scheduled_lane_matches_legacy_lane(monkeypatch, tmp_path):
    """R3: the scheduled (conn=None) lane writes the same events as the legacy lane.

    Run 1 (LEGACY): seed fixture A, call reconcile_unresolved_commands(connA, client)
    Run 2 (SCHEDULED): seed identical fixture B, call reconcile_unresolved_commands(
        conn=None, client) with default_trade_conn_factory pointed at B.
    Compare the (event_type, payload_json) sequence for the reconciled command.
    """
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract
    from src.state.db import init_schema

    order_payload = {"orderID": "vord-gold", "status": "LIVE"}

    # -- Run 1: legacy caller-owned-connection lane -------------------------
    legacy_path = tmp_path / "legacy.db"
    conn_a = sqlite3.connect(str(legacy_path))
    conn_a.row_factory = sqlite3.Row
    init_schema(conn_a)
    _seed_recovery_scenario(conn_a)
    legacy_client = _RecordingClient(_Recorder(), orders={"vord-gold": order_payload})
    command_recovery.reconcile_unresolved_commands(conn_a, legacy_client)
    conn_a.commit()
    legacy_events = _all_command_events(conn_a, "cmd-gold")
    legacy_state = h._get_state(conn_a, "cmd-gold")
    conn_a.close()

    # -- Run 2: scheduled short-connection lane -----------------------------
    sched_path = tmp_path / "scheduled.db"
    conn_b = sqlite3.connect(str(sched_path))
    conn_b.row_factory = sqlite3.Row
    init_schema(conn_b)
    _seed_recovery_scenario(conn_b)
    conn_b.close()

    recorder = _Recorder()
    factory = _make_conn_factory(sched_path, recorder)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)
    sched_client = _RecordingClient(_Recorder(), orders={"vord-gold": order_payload})
    command_recovery.reconcile_unresolved_commands(conn=None, client=sched_client)

    verify_conn = sqlite3.connect(str(sched_path))
    verify_conn.row_factory = sqlite3.Row
    sched_events = _all_command_events(verify_conn, "cmd-gold")
    sched_state = h._get_state(verify_conn, "cmd-gold")
    verify_conn.close()

    assert sched_state == legacy_state == "ACKED", (
        f"state mismatch: legacy={legacy_state} scheduled={sched_state}"
    )
    assert sched_events == legacy_events, (
        "scheduled-lane reconciliation events diverged from the legacy lane:\n"
        f"legacy   = {legacy_events}\n"
        f"scheduled= {sched_events}"
    )
