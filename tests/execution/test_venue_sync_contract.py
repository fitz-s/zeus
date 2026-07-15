# Created: 2026-06-11
# Lifecycle: created=2026-06-11
# Purpose: ANTIBODY for the dependency_db_locked category — pin that the EDLI
#   command-recovery sweep never holds a DB connection across venue/network I/O
#   and never threads one connection across multiple passes, while preserving
#   byte-identical reconciliation events vs the legacy long-connection path.
# Reuse: Run when command_recovery orchestration, venue_sync_contract, or the
#   scheduled _edli_command_recovery_cycle connection topology changes.
# Last reused/audited: 2026-07-15
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
import contextlib
import json
import sqlite3
import threading
import time
from decimal import Decimal
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


def _make_conn_factory(db_path: Path, recorder: _Recorder, *, attach_world_path: Path | None = None):
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    class _RecordingConnection(sqlite3.Connection):
        def close(self):
            recorder.on_close(self)
            return super().close()

    def factory():
        c = sqlite3.connect(str(db_path), factory=_RecordingConnection)
        c.row_factory = sqlite3.Row
        init_schema(c)
        init_collateral_schema(c)
        if attach_world_path is not None:
            c.execute("ATTACH DATABASE ? AS world", (str(attach_world_path),))
        recorder.on_open(c, "factory")
        return c

    return factory


class _RecordingClient:
    """Venue client whose every method records the connections open at call time."""

    _NETWORK = ("get_order", "get_open_orders", "get_trades",
                "find_order_by_idempotency_key", "get_clob_market_info")

    def __init__(self, recorder: _Recorder, *, orders=None, open_orders=None, trades=None):
        self._recorder = recorder
        self._orders = orders or {}
        self._open_orders = list(open_orders or [])
        self._trades = list(trades or [])
        self.venue_reads_are_complete = True

    def get_order(self, order_id):
        self._recorder.on_client_call("get_order")
        return self._orders.get(str(order_id))

    def get_open_orders(self):
        self._recorder.on_client_call("get_open_orders")
        return list(self._open_orders)

    def get_trades(self):
        self._recorder.on_client_call("get_trades")
        return list(self._trades)

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


def test_capture_snapshot_retries_transient_account_read_once():
    from src.execution import venue_sync_contract as vsc

    class Client:
        def __init__(self):
            self.trade_reads = 0

        def get_open_orders(self):
            return []

        def get_trades(self):
            self.trade_reads += 1
            if self.trade_reads == 1:
                raise ConnectionError("incomplete response body")
            return [{"id": "confirmed-trade"}]

    client = Client()
    snapshot = vsc.capture_venue_read_snapshot(client, order_ids=[])

    assert snapshot.get_trades() == [{"id": "confirmed-trade"}]
    assert client.trade_reads == 2


def test_capture_snapshot_preserves_point_order_timeout_as_unknown():
    from src.execution import venue_sync_contract as vsc

    class Client:
        def get_open_orders(self):
            return []

        def get_trades(self):
            return []

        def get_order(self, _order_id):
            raise TimeoutError("transient timeout")

    snapshot = vsc.capture_venue_read_snapshot(Client(), order_ids=["order-1"])

    with pytest.raises(vsc.SnapshotMissError, match="transient timeout"):
        snapshot.get_order("order-1")


def test_capture_snapshot_normalizes_exact_empty_point_order_shape_to_not_found():
    from src.execution import venue_sync_contract as vsc
    from src.venue.response_contracts import VenueResponseShapeError

    class Client:
        def get_open_orders(self):
            return []

        def get_trades(self):
            return []

        def get_order(self, _order_id):
            raise VenueResponseShapeError("get_order", {}, "missing status")

    snapshot = vsc.capture_venue_read_snapshot(Client(), order_ids=["order-1"])

    assert snapshot.get_order("order-1") is None


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
    from src.state.collateral_ledger import init_collateral_schema
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
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


def test_live_tick_scope_runs_light_partial_remainder_recovery(monkeypatch, tmp_path):
    """The order-daemon cadence must not defer stale partial remainder release."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-tick.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
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
    assert summary["partial_remainders"] == {
        "scanned": 0,
        "advanced": 0,
        "stayed": 0,
        "errors": 0,
    }
    assert "recorded_maker_fill_economics" in summary


def test_live_tick_scope_projects_acked_entry_order_before_full_sweep(monkeypatch, tmp_path):
    """ACKED live entry orders must enter position_current on the high-cadence lane."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-entry-projection.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(
        seed_conn,
        command_id="cmd-live-projection",
        position_id="pos-live-projection",
        decision_id="dec-live-projection",
        token_id="tok-yes",
        no_token_id="tok-no",
        selected_token_id="tok-no",
        outcome_label="NO",
        size=13.45,
        price=0.74,
    )
    h._advance_to_acked(
        seed_conn,
        command_id="cmd-live-projection",
        venue_order_id="vord-live-projection",
    )
    h._append_order_fact(
        seed_conn,
        command_id="cmd-live-projection",
        order_id="vord-live-projection",
        state="LIVE",
        matched_size="0",
        remaining_size="13.45",
        source="REST",
    )
    h._insert_decision_log_trade_case_for_recovery(
        seed_conn,
        decision_id="dec-live-projection",
        trade_id="pos-live-projection",
        token_id="tok-yes",
        no_token_id="tok-no",
        direction="buy_no",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={"vord-live-projection": {"orderID": "vord-live-projection", "status": "LIVE"}},
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["scope"] == "live_tick"
    assert summary["live_entry_projection_repair"]["advanced"] == 1
    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        current = verify.execute(
            """
            SELECT phase, direction, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-live-projection'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_entry",
            "direction": "buy_no",
            "order_id": "vord-live-projection",
            "order_status": "pending",
        }
    finally:
        verify.close()


def test_live_tick_scope_projects_filled_entry_order_before_full_sweep(monkeypatch, tmp_path):
    """FILLED live entry orders must enter active monitoring on the high-cadence lane."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract
    from src.state.venue_command_repo import append_event

    db_path = tmp_path / "recovery-live-filled-entry-projection.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    event_id = "edli_evt_live_tick_filled_entry"
    final_intent_id = f"edli_intent:{event_id}:tok-yes"
    decision_id = (
        f"edli_exec_cmd:{event_id}:{final_intent_id}:"
        "tok-yes:tok-yes:buy_yes"
    )
    aggregate_id = f"{event_id}:{final_intent_id}"
    h._insert(
        seed_conn,
        command_id="cmd-live-filled",
        position_id="pos-live-filled",
        decision_id=decision_id,
        token_id="tok-yes",
        no_token_id="tok-no",
        selected_token_id="tok-yes",
        outcome_label="YES",
        size=40.25,
        price=0.44,
    )
    h._advance_to_acked(
        seed_conn,
        command_id="cmd-live-filled",
        venue_order_id="vord-live-filled",
    )
    append_event(
        seed_conn,
        command_id="cmd-live-filled",
        event_type="FILL_CONFIRMED",
        occurred_at="2026-07-02T02:18:17+00:00",
        payload={"venue_order_id": "vord-live-filled", "venue_status": "MATCHED"},
    )
    h._append_trade_fact(
        seed_conn,
        command_id="cmd-live-filled",
        order_id="vord-live-filled",
        trade_id="trade-live-filled",
        state="MATCHED",
        filled_size="40.25",
        fill_price="0.44",
    )
    h._insert_edli_live_order_event(
        seed_conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="DecisionProofAccepted",
        payload={
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "decision_audit": {
                "event_id": event_id,
                "event_type": "DAY0_EXTREME_UPDATED",
                "final_intent_id": final_intent_id,
                "actual_bin_label": "Will the highest temperature in Manila be 32°C on July 2?",
                "actual_condition_id": "condition-test",
                "actual_direction": "buy_yes",
                "actual_token_id": "tok-yes",
                "city": "Manila",
                "target_date": "2026-07-02",
                "metric": "high",
                "strategy_key": "day0_nowcast_entry",
                "q_live": 0.9614944294185659,
                "q_lcb_5pct": 0.96,
                "opportunity_book": {
                    "cache_summary": {
                        "selected_qkernel_execution_economics": {
                            "source": "qkernel_spine",
                            "side": "YES",
                            "candidate_id": "YES:bin-32:DIRECT_YES:bin-32@proof",
                            "route_id": "DIRECT_YES:bin-32@proof",
                            "bin_id": "bin-32",
                            "payoff_q_point": 0.9614944294185659,
                            "payoff_q_lcb": 0.96,
                            "cost": 0.44,
                            "edge_lcb": 0.52,
                            "optimal_delta_u": 0.52,
                            "false_edge_rate": 0.01,
                            "direction_law_ok": True,
                            "coherence_allows": True,
                        },
                    },
                },
            },
        },
        occurred_at="2026-07-02T02:17:51+00:00",
    )
    h._insert_edli_live_order_event(
        seed_conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="PreSubmitRevalidated",
        payload={
            "event_id": event_id,
            "event_type": "DAY0_EXTREME_UPDATED",
            "final_intent_id": final_intent_id,
            "condition_id": "condition-test",
            "token_id": "tok-yes",
            "direction": "buy_yes",
            "city": "Manila",
            "target_date": "2026-07-02",
            "metric": "high",
            "unit": "C",
            "strategy_key": "day0_nowcast_entry",
            "bin_label": "Will the highest temperature in Manila be 32°C on July 2?",
            "q_live": 0.9614944294185659,
            "q_lcb_5pct": 0.96,
            "limit_price": 0.44,
            "size": 40.25,
        },
        occurred_at="2026-07-02T02:18:08+00:00",
    )
    h._insert_edli_live_order_event(
        seed_conn,
        aggregate_id=aggregate_id,
        sequence=3,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "execution_command_id": decision_id,
        },
        occurred_at="2026-07-02T02:18:09+00:00",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={"vord-live-filled": {"orderID": "vord-live-filled", "status": "MATCHED"}},
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["scope"] == "live_tick"
    assert summary["filled_entry_projection_repair"]["advanced"] == 1
    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        current = verify.execute(
            """
            SELECT phase, direction, shares, entry_price, order_id, order_status,
                   entry_method, strategy_key
              FROM position_current
             WHERE position_id = 'pos-live-filled'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "direction": "buy_yes",
            "shares": pytest.approx(40.25),
            "entry_price": pytest.approx(0.44),
            "order_id": "vord-live-filled",
            "order_status": "filled",
                "entry_method": "venue_fact_recovery",
            "strategy_key": "day0_nowcast_entry",
        }
    finally:
        verify.close()


def test_restart_preflight_scope_projects_acked_entry_order_before_preflight(monkeypatch, tmp_path):
    """Restart recovery must clear ACKED/LIVE entry projection gaps before preflight."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-restart-entry-projection.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(
        seed_conn,
        command_id="cmd-restart-projection",
        position_id="pos-restart-projection",
        decision_id="dec-restart-projection",
        token_id="tok-yes",
        no_token_id="tok-no",
        selected_token_id="tok-no",
        outcome_label="NO",
        size=29.14,
        price=0.73,
    )
    h._advance_to_acked(
        seed_conn,
        command_id="cmd-restart-projection",
        venue_order_id="vord-restart-projection",
    )
    h._append_order_fact(
        seed_conn,
        command_id="cmd-restart-projection",
        order_id="vord-restart-projection",
        state="LIVE",
        matched_size="0",
        remaining_size="29.14",
        source="REST",
    )
    h._insert_decision_log_trade_case_for_recovery(
        seed_conn,
        decision_id="dec-restart-projection",
        trade_id="pos-restart-projection",
        token_id="tok-yes",
        no_token_id="tok-no",
        direction="buy_no",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={"vord-restart-projection": {"orderID": "vord-restart-projection", "status": "LIVE"}},
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="restart_preflight",
    )

    assert summary["scope"] == "restart_preflight"
    assert summary["restart_preflight_narrow"] is True
    assert summary["live_entry_projection_repair"]["advanced"] == 1
    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        current = verify.execute(
            """
            SELECT phase, direction, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-restart-projection'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_entry",
            "direction": "buy_no",
            "order_id": "vord-restart-projection",
            "order_status": "pending",
        }
    finally:
        verify.close()


def test_live_tick_releases_post_submit_unknown_no_command_before_broad_snapshot(
    monkeypatch,
    tmp_path,
):
    """No-command EDLI unknowns must not wait behind historical venue reads."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-tick-post-submit-unknown.db"
    world_path = tmp_path / "empty-world-with-legacy-edli-tables.db"
    aggregate_id = "event-fast:intent-fast:token-fast"
    execution_command_id = "edli_exec_cmd:event-fast:intent-fast:token-fast:buy_no"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert_edli_live_order_event(
        seed_conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="SubmitPlanBuilt",
        payload={
            "event_id": "event-fast",
            "final_intent_id": "intent-fast",
            "condition_id": "condition-fast",
            "token_id": "token-fast",
            "direction": "buy_no",
        },
        occurred_at="2026-04-26T00:01:00+00:00",
    )
    h._insert_edli_live_order_event(
        seed_conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="VenueSubmitAttempted",
        payload={
            "event_id": "event-fast",
            "final_intent_id": "intent-fast",
            "execution_command_id": execution_command_id,
            "idempotency_key": "idem-fast",
        },
        occurred_at="2026-04-26T00:02:00+00:00",
    )
    h._insert_edli_live_order_event(
        seed_conn,
        aggregate_id=aggregate_id,
        sequence=3,
        event_type="SubmitUnknown",
        payload={
            "event_id": "event-fast",
            "final_intent_id": "intent-fast",
            "execution_command_id": execution_command_id,
            "execution_receipt_hash": "receipt-fast",
            "reason_code": "EXECUTOR_SUBMIT_UNKNOWN:deployment_freshness_mismatch",
            "submit_status": "POST_SUBMIT_UNKNOWN",
            "reconciliation_followup_required": True,
            "side_effect_known": False,
            "venue_call_started": True,
        },
        occurred_at="2026-04-26T00:03:00+00:00",
    )
    seed_conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        ) VALUES (?, 'event-fast', 'intent-fast', 'PENDING_RECONCILE',
                  3, 'SubmitUnknown', 'hash-fast', 1, NULL,
                  '2026-04-26T00:03:00+00:00', 1)
        """,
        (aggregate_id,),
    )
    seed_conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, decision_time, cap_scope, max_notional_usd,
            max_orders_per_day, reserved_notional_usd, order_count,
            reservation_status, final_intent_id, execution_command_id,
            created_at, schema_version
        ) VALUES ('cap-fast', 'event-fast', '2026-04-26T00:02:00+00:00',
                  'tiny-live', 100.0, 100, 0.18, 1, 'RESERVED',
                  'intent-fast', ?, '2026-04-26T00:02:00+00:00', 1)
        """,
        (execution_command_id,),
    )
    seed_conn.commit()
    seed_conn.close()
    world_conn = sqlite3.connect(str(world_path))
    from src.state.db import init_schema as init_world_schema
    init_world_schema(world_conn)
    world_conn.commit()
    world_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder, attach_world_path=world_path)
    client = _RecordingClient(recorder, open_orders=[], trades=[])
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    def _block_broad_snapshot(*args, **kwargs):
        raise RuntimeError("broad snapshot blocked")

    monkeypatch.setattr(venue_sync_contract, "capture_venue_read_snapshot", _block_broad_snapshot)

    with pytest.raises(RuntimeError, match="broad snapshot blocked"):
        command_recovery.reconcile_unresolved_commands(
            conn=None,
            client=client,
            scope="live_tick",
        )

    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    projection = verify.execute(
        """
        SELECT current_state, pending_reconcile
        FROM edli_live_order_projection
        WHERE aggregate_id = ?
        """,
        (aggregate_id,),
    ).fetchone()
    cap = verify.execute(
        "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = 'cap-fast'"
    ).fetchone()
    reconcile_payload = verify.execute(
        """
        SELECT payload_json
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = 'Reconciled'
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()

    assert projection["current_state"] == "CAP_TRANSITIONED"
    assert bool(projection["pending_reconcile"]) is False
    assert cap["reservation_status"] == "RELEASED"
    assert json.loads(reconcile_payload["payload_json"])["required_predicates"][
        "no_venue_command"
    ] is True
    for method, open_ids, open_labels in recorder.client_calls:
        assert not open_ids, (
            f"venue call {method} occurred while {len(open_ids)} DB connection(s) "
            f"were open: {open_labels}"
        )


def test_boot_fast_scope_skips_historical_fill_maintenance(monkeypatch, tmp_path):
    """Boot recovery must clear submit locks without blocking scheduler start on
    historical maker-fill economics or partial-remainder maintenance.
    """
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-boot-fast.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-boot-fast")
    h._advance_to_submitting(seed_conn, command_id="cmd-boot-fast", venue_order_id="vord-boot-fast")
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={"vord-boot-fast": {"orderID": "vord-boot-fast", "status": "LIVE"}},
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="boot_fast",
    )

    assert summary["scope"] == "boot_fast"
    assert summary["deferred_full_sweep"] is True
    assert recorder.client_calls == []
    assert "partial_remainders" not in summary
    assert "recorded_maker_fill_economics" not in summary


@pytest.mark.parametrize("scope", ["boot_fast", "restart_preflight", "live_tick"])
def test_scoped_recovery_persists_execution_fact_repair(monkeypatch, tmp_path, scope):
    """Every production recovery scope must persist allocator-critical fill facts."""
    from src.execution import command_recovery, venue_sync_contract
    from src.state.db import init_schema

    db_path = tmp_path / f"recovery-execution-fact-{scope}.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    init_schema(seed_conn)
    seed_conn.execute("CREATE TABLE recovery_probe (scope TEXT PRIMARY KEY)")
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    def _persist_probe(conn):
        conn.execute("INSERT INTO recovery_probe (scope) VALUES (?)", (scope,))
        return {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}

    monkeypatch.setattr(
        command_recovery,
        "reconcile_filled_entry_execution_fact_repairs",
        _persist_probe,
    )

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=_RecordingClient(recorder),
        scope=scope,
    )

    assert summary["filled_entry_execution_fact_repair"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    verify = sqlite3.connect(str(db_path))
    try:
        persisted = verify.execute("SELECT scope FROM recovery_probe").fetchone()
    finally:
        verify.close()
    assert persisted == (scope,)


def test_boot_fast_scope_projects_confirmed_exit_fill_without_full_maker_scan(monkeypatch, tmp_path):
    """Boot-fast may repair closed exit projection debt without running full maker-fill maintenance."""
    import tests.test_exchange_reconcile as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-boot-fast-exit-fill.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    token = "boot-fast-exit-fill-token"
    h.seed_position_baseline(
        seed_conn,
        position_id="pos-boot-fast-exit-fill",
        order_id="ord-boot-fast-entry",
    )
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase = 'economically_closed',
               chain_state = 'synced',
               token_id = ?,
               shares = 21.42,
               chain_shares = 21.42,
               cost_basis_usd = 12.85,
               chain_cost_basis_usd = 12.85,
               entry_price = 0.60,
               order_status = 'sell_filled',
               exit_price = 0.57,
               updated_at = ?
         WHERE position_id = 'pos-boot-fast-exit-fill'
        """,
        (token, h.NOW.isoformat()),
    )
    h.seed_command(
        seed_conn,
        command_id="cmd-boot-fast-exit-fill",
        venue_order_id="ord-boot-fast-exit-fill",
        position_id="pos-boot-fast-exit-fill",
        token_id=token,
        side="SELL",
        size=21.42,
        price=0.57,
        state="FILLED",
    )
    h.append_trade_fact(
        seed_conn,
        command_id="cmd-boot-fast-exit-fill",
        venue_order_id="ord-boot-fast-exit-fill",
        token_id=token,
        trade_id="trade-boot-fast-exit-fill",
        size="21.42",
        fill_price="0.57",
        state="CONFIRMED",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(recorder)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="boot_fast",
    )

    assert summary["scope"] == "boot_fast"
    assert summary["recorded_exit_fill_projection"]["projected"] == 1
    assert "recorded_maker_fill_economics" not in summary
    assert recorder.client_calls == []
    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    projection = check_conn.execute(
        """
        SELECT phase, order_status, shares, chain_shares, chain_avg_price,
               chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-boot-fast-exit-fill'
        """
    ).fetchone()
    check_conn.close()
    assert dict(projection) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "shares": 21.42,
        "chain_shares": 0.0,
        "chain_avg_price": 0.0,
        "chain_cost_basis_usd": 0.0,
    }


def test_live_tick_scope_projects_live_order_positive_matched_size(monkeypatch, tmp_path):
    """Boot/live cadence must ingest partial maker fills before redecision."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-tick-live-partial.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-live-partial", size=10.58, price=0.67)
    h._advance_to_acked(
        seed_conn,
        command_id="cmd-live-partial",
        venue_order_id="vord-live-partial",
    )
    h._seed_pending_entry_projection(
        seed_conn,
        command_id="cmd-live-partial",
        order_id="vord-live-partial",
    )
    h._append_order_fact(
        seed_conn,
        command_id="cmd-live-partial",
        order_id="vord-live-partial",
        state="LIVE",
        matched_size="0",
        remaining_size="10.58",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={
            "vord-live-partial": {
                "orderID": "vord-live-partial",
                "status": "LIVE",
                "size_matched": "4.484847",
                "original_size": "10.58",
                "price": "0.67",
                "associate_trades": ["trade-live-partial"],
            }
        },
        open_orders=[
            {
                "orderID": "vord-live-partial",
                "status": "LIVE",
                "size_matched": "4.484847",
                "original_size": "10.58",
                "price": "0.67",
            }
        ],
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["scope"] == "live_tick"
    assert summary["matched_order_facts"]["advanced"] == 1
    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        command = verify.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-live-partial'"
        ).fetchone()
        fact = verify.execute(
            """
            SELECT state, matched_size, remaining_size
              FROM venue_order_facts
             WHERE command_id = 'cmd-live-partial'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        position = verify.execute(
            """
            SELECT phase, shares, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
    finally:
        verify.close()
    assert command["state"] == "PARTIAL"
    assert dict(fact) == {
        "state": "PARTIALLY_MATCHED",
        "matched_size": "4.484847",
        "remaining_size": "6.095153",
    }
    assert position["phase"] == "active"
    assert str(position["shares"]) == "4.484847"
    assert position["order_status"] == "partial"


def test_live_tick_scope_terminalizes_cancelled_partial_remainder(monkeypatch, tmp_path):
    """A cancelled maker remainder must not wait for the deferred full sweep."""

    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-tick-partial-remainder.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-partial", size=5.0)
    h._advance_to_partial(seed_conn, command_id="cmd-partial", venue_order_id="vord-partial")
    h._append_confirmed_trade_fact(
        seed_conn,
        command_id="cmd-partial",
        order_id="vord-partial",
        filled_size="1.25",
        fill_price="0.50",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={"vord-partial": {"orderID": "vord-partial", "status": "CANCELED"}},
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["partial_remainders"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    try:
        state = check_conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-partial'"
        ).fetchone()["state"]
    finally:
        check_conn.close()
    assert state == "EXPIRED"


def test_live_tick_scope_projects_confirmed_exit_fills(monkeypatch, tmp_path):
    """Live cadence must consume confirmed exit facts; otherwise closed old legs
    stay quarantined and close-before-open redecision never progresses."""
    import tests.test_exchange_reconcile as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-tick-exit-fill.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    token = "live-tick-exit-fill-token"
    h.seed_position_baseline(
        seed_conn,
        position_id="pos-live-tick-exit-fill",
        order_id="ord-live-tick-entry",
    )
    seed_conn.execute(
        """
        UPDATE position_current
               SET phase = 'active',
                   chain_state = 'synced',
               token_id = ?,
               shares = 11.09,
               chain_shares = 11.09,
               cost_basis_usd = 6.10,
               chain_cost_basis_usd = 6.10,
               entry_price = 0.55,
               order_status = 'filled',
               updated_at = ?
         WHERE position_id = 'pos-live-tick-exit-fill'
        """,
        (token, h.NOW.isoformat()),
    )
    h.seed_command(
        seed_conn,
        command_id="cmd-live-tick-exit-fill",
        venue_order_id="ord-live-tick-exit-fill",
        position_id="pos-live-tick-exit-fill",
        token_id=token,
        side="SELL",
        size=11.09,
        price=0.53,
        state="FILLED",
    )
    h.append_trade_fact(
        seed_conn,
        command_id="cmd-live-tick-exit-fill",
        venue_order_id="ord-live-tick-exit-fill",
        token_id=token,
        trade_id="trade-live-tick-exit-fill",
        size="11.09",
        fill_price="0.54",
        state="CONFIRMED",
    )
    seed_conn.execute(
        """
        INSERT INTO family_rebalance_intents (
            intent_id, family_key, operation, held_position_id, held_token_id,
            held_bin_id, selected_token_id, selected_bin_id, status, generation,
            created_at, updated_at, schema_version
        ) VALUES (
            'intent-live-tick-shift', 'live|Tokyo|2026-06-27|low', 'SHIFT_BIN',
            'pos-live-tick-exit-fill', ?, '22C', 'new-token', '23C',
            'EXIT_SUBMITTED', 1, ?, ?, 1
        )
        """,
        (token, h.NOW.isoformat(), h.NOW.isoformat()),
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(recorder)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["recorded_exit_fill_projection"]["projected"] == 1
    assert summary["closed_shift_bin_exit_leases"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    projection = check_conn.execute(
        """
        SELECT phase, exit_reason
          FROM position_current
         WHERE position_id = 'pos-live-tick-exit-fill'
        """
    ).fetchone()
    assert dict(projection) == {
        "phase": "economically_closed",
        "exit_reason": "M5_EXCHANGE_RECONCILE",
    }
    lease = check_conn.execute(
        """
        SELECT status, abort_reason
          FROM family_rebalance_intents
         WHERE intent_id = 'intent-live-tick-shift'
        """
    ).fetchone()
    assert dict(lease) == {
        "status": "EXIT_ONLY_COMPLETE",
        "abort_reason": "SHIFT_BIN_OLD_LEG_ECONOMICALLY_CLOSED_BY_COMMAND_RECOVERY",
    }
    check_conn.close()


def test_restart_preflight_scope_projects_confirmed_exit_fills_before_preflight(monkeypatch, tmp_path):
    """Deploy restart recovery must clear closed sell projection debt before read-only preflight."""
    import tests.test_exchange_reconcile as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-restart-exit-fill.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    token = "restart-exit-fill-token"
    h.seed_position_baseline(
        seed_conn,
        position_id="pos-restart-exit-fill",
        order_id="ord-restart-entry",
    )
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase = 'economically_closed',
               chain_state = 'synced',
               token_id = ?,
               shares = 33.15,
               chain_shares = 33.15,
               cost_basis_usd = 19.89,
               chain_cost_basis_usd = 19.89,
               entry_price = 0.60,
               order_status = 'sell_filled',
               exit_price = 0.55,
               updated_at = ?
         WHERE position_id = 'pos-restart-exit-fill'
        """,
        (token, h.NOW.isoformat()),
    )
    h.seed_command(
        seed_conn,
        command_id="cmd-restart-exit-fill",
        venue_order_id="ord-restart-exit-fill",
        position_id="pos-restart-exit-fill",
        token_id=token,
        side="SELL",
        size=33.15,
        price=0.55,
        state="FILLED",
    )
    h.append_trade_fact(
        seed_conn,
        command_id="cmd-restart-exit-fill",
        venue_order_id="ord-restart-exit-fill",
        token_id=token,
        trade_id="trade-restart-exit-fill",
        size="33.15",
        fill_price="0.55",
        state="CONFIRMED",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(recorder)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="restart_preflight",
    )

    assert summary["scope"] == "restart_preflight"
    assert summary["restart_preflight_narrow"] is True
    assert summary["recorded_exit_fill_projection"]["projected"] == 1
    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    projection = check_conn.execute(
        """
        SELECT phase, order_status, shares, chain_shares, chain_avg_price,
               chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-restart-exit-fill'
        """
    ).fetchone()
    check_conn.close()
    assert dict(projection) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "shares": 33.15,
        "chain_shares": 0.0,
        "chain_avg_price": 0.0,
        "chain_cost_basis_usd": 0.0,
    }


def _confirmed_maker_trade(*, trade_id: str, order_id: str, token: str, size: str) -> dict:
    return {
        "id": trade_id,
        "status": "CONFIRMED",
        "market": token,
        "match_time": "1783770674",
        "transaction_hash": f"tx-{trade_id}",
        "maker_orders": [
            {
                "order_id": order_id,
                "asset_id": token,
                "side": "SELL",
                "price": "0.29",
                "matched_amount": size,
            }
        ],
    }


def test_restart_preflight_backfills_all_settled_exit_fills_before_terminalizing(
    monkeypatch,
    tmp_path,
):
    """A stale terminal EXIT closes only after local facts equal authenticated fills."""
    import tests.test_exchange_reconcile as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-restart-settled-exit-partial.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    token = "restart-settled-exit-partial-token"
    h.seed_position_baseline(
        seed_conn,
        position_id="pos-restart-settled-exit-partial",
        order_id="ord-restart-entry",
    )
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               chain_state = 'synced',
               token_id = ?,
               shares = 12.86818,
               chain_shares = 0.00818,
               cost_basis_usd = 7.72,
               chain_cost_basis_usd = 0.00237,
               entry_price = 0.60,
               order_status = 'settled',
               updated_at = ?
         WHERE position_id = 'pos-restart-settled-exit-partial'
        """,
        (token, h.NOW.isoformat()),
    )
    h.seed_command(
        seed_conn,
        command_id="cmd-restart-settled-exit-partial",
        venue_order_id="ord-restart-settled-exit-partial",
        position_id="pos-restart-settled-exit-partial",
        token_id=token,
        side="SELL",
        size=383.6,
        price=0.29,
        state="PARTIAL",
    )
    order_id = "ord-restart-settled-exit-partial"
    local_trades = (("trade-1", "10"), ("trade-2", "12.86"))
    for trade_id, size in local_trades:
        h.append_trade_fact(
            seed_conn,
            command_id="cmd-restart-settled-exit-partial",
            venue_order_id=order_id,
            token_id=token,
            trade_id=trade_id,
            size=size,
            fill_price="0.29",
            state="CONFIRMED",
            tx_hash=f"tx-{trade_id}",
        )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    venue_trades = [
        _confirmed_maker_trade(trade_id="trade-3", order_id=order_id, token=token, size="8.43"),
        _confirmed_maker_trade(trade_id="trade-1", order_id=order_id, token=token, size="10"),
        _confirmed_maker_trade(trade_id="trade-2", order_id=order_id, token=token, size="12.86"),
        _confirmed_maker_trade(trade_id="trade-4", order_id=order_id, token=token, size="5"),
    ]
    client = _RecordingClient(recorder, orders={}, open_orders=[], trades=venue_trades)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="restart_preflight",
    )

    assert summary["terminal_exit_partial_remainders"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    second = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="restart_preflight",
    )
    assert second["terminal_exit_partial_remainders"] == {
        "scanned": 0,
        "advanced": 0,
        "stayed": 0,
        "errors": 0,
    }
    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        command = verify.execute(
            "SELECT state FROM venue_commands WHERE command_id = ?",
            ("cmd-restart-settled-exit-partial",),
        ).fetchone()
        position = verify.execute(
            "SELECT phase, chain_shares FROM position_current WHERE position_id = ?",
            ("pos-restart-settled-exit-partial",),
        ).fetchone()
        fact = verify.execute(
            """
            SELECT state, matched_size, remaining_size
              FROM venue_order_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            ("cmd-restart-settled-exit-partial",),
        ).fetchone()
        trade_facts = verify.execute(
            """
            SELECT trade_id, state, filled_size
              FROM venue_trade_facts
             WHERE command_id = ?
             ORDER BY trade_id
            """,
            ("cmd-restart-settled-exit-partial",),
        ).fetchall()
        terminal_fact_count = verify.execute(
            """
            SELECT COUNT(*)
              FROM venue_order_facts
             WHERE command_id = ? AND state = 'EXPIRED'
            """,
            ("cmd-restart-settled-exit-partial",),
        ).fetchone()[0]
    finally:
        verify.close()
    assert command["state"] == "EXPIRED"
    assert dict(position) == {"phase": "settled", "chain_shares": 0.00818}
    assert dict(fact) == {
        "state": "EXPIRED",
        "matched_size": "36.29",
        "remaining_size": "0",
    }
    assert {row["trade_id"] for row in trade_facts} == {
        "trade-1",
        "trade-2",
        "trade-3",
        "trade-4",
    }
    assert all(row["state"] == "CONFIRMED" for row in trade_facts)
    assert sum((Decimal(row["filled_size"]) for row in trade_facts), Decimal("0")) == Decimal("36.29")
    assert terminal_fact_count == 1
    for method, open_ids, open_labels in recorder.client_calls:
        assert not open_ids, (
            f"venue call {method} occurred while DB connections were open: {open_labels}"
        )


def test_live_tick_closes_pending_exit_remainder_from_complete_account_trades(
    monkeypatch,
    tmp_path,
):
    """A pending EXIT cannot keep full sell collateral after its order is gone."""
    import hashlib

    import tests.test_exchange_reconcile as h
    from src.execution import command_recovery, venue_sync_contract
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema
    from src.state.venue_command_repo import append_order_fact

    db_path = tmp_path / "recovery-live-pending-exit-partial.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    command_id = "cmd-live-pending-exit-partial"
    order_id = "ord-live-pending-exit-partial"
    position_id = "pos-live-pending-exit-partial"
    token = "live-pending-exit-partial-token"
    condition_id = "condition-m5"
    h.seed_position_baseline(seed_conn, position_id=position_id, order_id="ord-entry")
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit', chain_state = 'synced',
               shares = 0.006614, chain_shares = 0.006614,
               order_status = 'sell_pending_confirmation', updated_at = ?
         WHERE position_id = ?
        """,
        (h.NOW.isoformat(), position_id),
    )
    h.seed_command(
        seed_conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=token,
        side="SELL",
        size=37.2,
        price=0.99,
        state="PARTIAL",
    )
    first_tx = "0xfirst-fill"
    h.append_trade_fact(
        seed_conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=token,
        trade_id=first_tx,
        size="10",
        fill_price="0.9905",
        state="MATCHED",
        tx_hash=first_tx,
    )
    append_order_fact(
        seed_conn,
        venue_order_id=order_id,
        command_id=command_id,
        state="PARTIALLY_MATCHED",
        remaining_size="27.2",
        matched_size="10",
        source="REST",
        observed_at=h.NOW,
        raw_payload_hash=hashlib.sha256(b"live-pending-exit-partial").hexdigest(),
        raw_payload_json={"orderID": order_id, "status": "MATCHED"},
    )
    seed_conn.execute(
        """
        INSERT INTO collateral_reservations (
            command_id, reservation_type, token_id, amount,
            converted_amount, created_at
        ) VALUES (?, 'CTF_SELL', ?, 37200000, 0, ?)
        """,
        (command_id, token, h.NOW.isoformat()),
    )
    seed_conn.commit()
    seed_conn.close()

    venue_trades = [
        {
            "id": "venue-trade-first",
            "status": "CONFIRMED",
            "market": condition_id,
            "order_id": order_id,
            "asset_id": token,
            "side": "SELL",
            "price": "0.991",
            "size": "10",
            "match_time": "1783941027",
            "transaction_hash": first_tx,
        },
        {
            "id": "venue-trade-second",
            "status": "CONFIRMED",
            "market": condition_id,
            "match_time": "1783941029",
            "transaction_hash": "0xsecond-fill",
            "maker_orders": [{
                "order_id": order_id,
                "asset_id": token,
                "side": "SELL",
                "price": "0.9899999553453086",
                "matched_amount": "20.423386",
            }],
        },
        {
            "id": "venue-trade-third",
            "status": "CONFIRMED",
            "market": condition_id,
            "match_time": "1783941032",
            "transaction_hash": "0xthird-fill",
            "maker_orders": [{
                "order_id": order_id,
                "asset_id": token,
                "side": "SELL",
                "price": "0.99",
                "matched_amount": "6.77",
            }],
        },
    ]
    recorder = _Recorder()
    monkeypatch.setattr(
        venue_sync_contract,
        "default_trade_conn_factory",
        _make_conn_factory(db_path, recorder),
    )
    client = _RecordingClient(
        recorder,
        orders={},
        open_orders=[],
        trades=venue_trades,
    )

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["partial_remainders"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        command = verify.execute(
            "SELECT state FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        fact = verify.execute(
            """
            SELECT state, matched_size, remaining_size
              FROM venue_order_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        trades = verify.execute(
            """
            WITH latest AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY trade_id ORDER BY local_sequence DESC
                ) AS rn
                  FROM venue_trade_facts
                 WHERE command_id = ?
            )
            SELECT trade_id, state, filled_size, fill_price, tx_hash
              FROM latest WHERE rn = 1
            """,
            (command_id,),
        ).fetchall()
        reservation = verify.execute(
            """
            SELECT converted_amount, released_at, release_reason
              FROM collateral_reservations
             WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        position = verify.execute(
            """
            SELECT phase, order_status, shares, chain_shares
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
    finally:
        verify.close()
    assert command["state"] == "EXPIRED"
    assert dict(fact) == {
        "state": "EXPIRED",
        "matched_size": "37.193386",
        "remaining_size": "0",
    }
    exact_trades = [row for row in trades if row["trade_id"] != row["tx_hash"]]
    assert len(trades) == 4
    assert len(exact_trades) == 3
    assert all(row["state"] == "CONFIRMED" for row in exact_trades)
    assert sum(
        (Decimal(row["filled_size"]) for row in exact_trades),
        Decimal("0"),
    ) == Decimal("37.193386")
    assert reservation["converted_amount"] == 37_193_386
    assert reservation["released_at"] is not None
    assert reservation["release_reason"] == "CONVERTED_ON_FILL"
    assert dict(position) == {
        "phase": "day0_window",
        "order_status": "filled",
        "shares": 0.006614,
        "chain_shares": 0.006614,
    }
    for method, open_ids, open_labels in recorder.client_calls:
        assert not open_ids, (
            f"venue call {method} occurred while DB connections were open: {open_labels}"
        )


@pytest.mark.parametrize(
    "failure_mode",
    ("local_conflict", "missing_market", "mismatched_market", "point_timeout"),
)
def test_restart_preflight_keeps_settled_exit_partial_when_proof_is_incomplete(
    monkeypatch,
    tmp_path,
    failure_mode,
):
    """Any identity, economics, or point-read gap fails closed with zero writes."""
    import tests.test_exchange_reconcile as h
    from src.execution import command_recovery, venue_sync_contract
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema

    db_path = tmp_path / f"recovery-restart-settled-exit-{failure_mode}.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    token = "restart-settled-exit-conflict-token"
    command_id = "cmd-restart-settled-exit-conflict"
    order_id = "ord-restart-settled-exit-conflict"
    position_id = "pos-restart-settled-exit-conflict"
    h.seed_position_baseline(seed_conn, position_id=position_id, order_id="ord-entry")
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled', chain_state = 'synced', token_id = ?,
               shares = 12.86818, chain_shares = 0.00818,
               order_status = 'settled', updated_at = ?
         WHERE position_id = ?
        """,
        (token, h.NOW.isoformat(), position_id),
    )
    h.seed_command(
        seed_conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=token,
        side="SELL",
        size=383.6,
        price=0.29,
        state="PARTIAL",
    )
    local_first_size = "9" if failure_mode == "local_conflict" else "10"
    for trade_id, size in (("trade-1", local_first_size), ("trade-2", "12.86")):
        h.append_trade_fact(
            seed_conn,
            command_id=command_id,
            venue_order_id=order_id,
            token_id=token,
            trade_id=trade_id,
            size=size,
            fill_price="0.29",
            state="CONFIRMED",
            tx_hash=f"tx-{trade_id}",
        )
    seed_conn.commit()
    seed_conn.close()

    venue_trades = [
        _confirmed_maker_trade(trade_id="trade-3", order_id=order_id, token=token, size="8.43"),
        _confirmed_maker_trade(trade_id="trade-1", order_id=order_id, token=token, size="10"),
        _confirmed_maker_trade(trade_id="trade-2", order_id=order_id, token=token, size="12.86"),
        _confirmed_maker_trade(trade_id="trade-4", order_id=order_id, token=token, size="5"),
    ]
    if failure_mode == "missing_market":
        venue_trades[0].pop("market")
    elif failure_mode == "mismatched_market":
        venue_trades[0]["market"] = "other-market"
    recorder = _Recorder()
    monkeypatch.setattr(
        venue_sync_contract,
        "default_trade_conn_factory",
        _make_conn_factory(db_path, recorder),
    )
    client = _RecordingClient(recorder, orders={}, open_orders=[], trades=venue_trades)
    if failure_mode == "point_timeout":
        def _timeout_get_order(_order_id):
            recorder.on_client_call("get_order")
            raise TimeoutError("transient timeout")

        client.get_order = _timeout_get_order
    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="restart_preflight",
    )

    assert summary["terminal_exit_partial_remainders"] == {
        "scanned": 1,
        "advanced": 0,
        "stayed": 0,
        "errors": 1,
    }
    verify = sqlite3.connect(str(db_path))
    try:
        assert verify.execute(
            "SELECT state FROM venue_commands WHERE command_id = ?", (command_id,)
        ).fetchone()[0] == "PARTIAL"
        assert verify.execute(
            "SELECT COUNT(*) FROM venue_trade_facts WHERE command_id = ?", (command_id,)
        ).fetchone()[0] == 2
        assert verify.execute(
            "SELECT COUNT(*) FROM venue_order_facts WHERE command_id = ? AND state = 'EXPIRED'",
            (command_id,),
        ).fetchone()[0] == 0
        assert verify.execute(
            "SELECT phase FROM position_current WHERE position_id = ?", (position_id,)
        ).fetchone()[0] == "settled"
    finally:
        verify.close()


def test_restart_preflight_projects_matched_exit_fills_before_preflight(monkeypatch, tmp_path):
    """Matched full exit fills are terminal enough to clear stale local exposure."""
    import tests.test_exchange_reconcile as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-restart-matched-exit-fill.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    token = "restart-matched-exit-fill-token"
    h.seed_position_baseline(
        seed_conn,
        position_id="pos-restart-matched-exit-fill",
        order_id="ord-restart-entry",
    )
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase = 'economically_closed',
               chain_state = 'synced',
               token_id = ?,
               shares = 33.15,
               chain_shares = 33.15,
               cost_basis_usd = 19.89,
               chain_cost_basis_usd = 19.89,
               entry_price = 0.60,
               order_status = 'sell_filled',
               exit_price = 0.55,
               updated_at = ?
         WHERE position_id = 'pos-restart-matched-exit-fill'
        """,
        (token, h.NOW.isoformat()),
    )
    h.seed_command(
        seed_conn,
        command_id="cmd-restart-matched-exit-fill",
        venue_order_id="ord-restart-matched-exit-fill",
        position_id="pos-restart-matched-exit-fill",
        token_id=token,
        side="SELL",
        size=33.15,
        price=0.55,
        state="FILLED",
    )
    h.append_trade_fact(
        seed_conn,
        command_id="cmd-restart-matched-exit-fill",
        venue_order_id="ord-restart-matched-exit-fill",
        token_id=token,
        trade_id="trade-restart-matched-exit-fill",
        size="33.15",
        fill_price="0.55",
        state="MATCHED",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(recorder)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="restart_preflight",
    )

    assert summary["scope"] == "restart_preflight"
    assert summary["restart_preflight_narrow"] is True
    assert summary["recorded_exit_fill_projection"]["projected"] == 1
    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    projection = check_conn.execute(
        """
        SELECT phase, order_status, shares, chain_shares, chain_avg_price,
               chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-restart-matched-exit-fill'
        """
    ).fetchone()
    check_conn.close()
    assert dict(projection) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "shares": 33.15,
        "chain_shares": 0.0,
        "chain_avg_price": 0.0,
        "chain_cost_basis_usd": 0.0,
    }


def test_closed_shift_bin_release_reads_attached_world_table(tmp_path):
    """Production keeps the rebalance lease in world.db attached to the trade conn."""
    from src.execution.command_recovery import release_closed_shift_bin_exit_leases
    from src.state.schema.family_rebalance_intents_schema import ensure_table

    trade_path = tmp_path / "trade.db"
    world_path = tmp_path / "world.db"
    world_conn = sqlite3.connect(str(world_path))
    world_conn.row_factory = sqlite3.Row
    ensure_table(world_conn)
    world_conn.execute(
        """
        INSERT INTO family_rebalance_intents (
            intent_id, family_key, operation, held_position_id, held_token_id,
            held_bin_id, selected_token_id, selected_bin_id, status, generation,
            created_at, updated_at, schema_version
        ) VALUES (
            'intent-attached-world', 'live|Tokyo|2026-06-27|low', 'SHIFT_BIN',
            'pos-attached-world', 'tok-old', '22C', 'tok-new', '23C',
            'EXIT_SUBMITTED', 1, 't0', 't0', 1
        )
        """
    )
    world_conn.commit()
    world_conn.close()

    trade_conn = sqlite3.connect(str(trade_path))
    trade_conn.row_factory = sqlite3.Row
    trade_conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT, phase TEXT, token_id TEXT, no_token_id TEXT,
            chain_cost_basis_usd REAL, cost_basis_usd REAL, size_usd REAL,
            updated_at TEXT
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, chain_cost_basis_usd,
            cost_basis_usd, size_usd, updated_at
        ) VALUES (
            'pos-attached-world', 'economically_closed', 'tok-old', '',
            6.10, 6.10, 6.10, 't1'
        )
        """
    )
    trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))

    summary = release_closed_shift_bin_exit_leases(trade_conn, observed_at="t2")

    assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
    row = trade_conn.execute(
        """
        SELECT status, abort_reason
          FROM world.family_rebalance_intents
         WHERE intent_id = 'intent-attached-world'
        """
    ).fetchone()
    assert dict(row) == {
        "status": "EXIT_ONLY_COMPLETE",
        "abort_reason": "SHIFT_BIN_OLD_LEG_ECONOMICALLY_CLOSED_BY_COMMAND_RECOVERY",
    }
    trade_conn.close()


def test_stale_rebalance_entry_release_reads_attached_world_table(tmp_path):
    """Canceled counter-entry orders must not keep rebalance leases active."""
    from src.execution.command_recovery import release_stale_rebalance_entry_leases
    from src.state.schema.family_rebalance_intents_schema import ensure_table

    trade_path = tmp_path / "trade.db"
    world_path = tmp_path / "world.db"
    old_time = "2026-06-26T04:00:00+00:00"
    now_time = "2026-06-26T05:00:00+00:00"

    world_conn = sqlite3.connect(str(world_path))
    world_conn.row_factory = sqlite3.Row
    ensure_table(world_conn)
    world_conn.execute(
        """
        INSERT INTO family_rebalance_intents (
            intent_id, event_id, family_key, operation, held_position_id,
            held_token_id, held_bin_id, selected_token_id, selected_bin_id,
            status, generation, created_at, updated_at, schema_version
        ) VALUES (
            'intent-shift-entry-cancelled', 'evt-shift-entry',
            'live|Tokyo|2026-06-27|low', 'SHIFT_BIN',
            'pos-old', 'tok-old', '22C', 'tok-new', '23C',
            'ENTRY_SUBMITTED', 1, ?, ?, 1
        )
        """,
        (old_time, old_time),
    )
    world_conn.execute(
        """
        INSERT INTO family_rebalance_intents (
            intent_id, event_id, family_key, operation, held_position_id,
            held_token_id, held_bin_id, selected_token_id, selected_bin_id,
            status, generation, created_at, updated_at, schema_version
        ) VALUES (
            'intent-fill-up-planned', 'evt-fill-up',
            'live|Osaka|2026-06-27|low', 'FILL_UP',
            'pos-old', 'tok-old', '22C', 'tok-old', '22C',
            'PLANNED', 1, ?, ?, 1
        )
        """,
        (old_time, old_time),
    )
    world_conn.commit()
    world_conn.close()

    trade_conn = sqlite3.connect(str(trade_path))
    trade_conn.row_factory = sqlite3.Row
    trade_conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT, phase TEXT, token_id TEXT, no_token_id TEXT,
            chain_cost_basis_usd REAL, cost_basis_usd REAL, size_usd REAL,
            updated_at TEXT
        )
        """
    )
    trade_conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY, decision_id TEXT, intent_kind TEXT,
            token_id TEXT, state TEXT, created_at TEXT, updated_at TEXT
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, decision_id, intent_kind, token_id, state, created_at, updated_at
        ) VALUES (
            'cmd-shift-entry-cancelled', 'edli_exec_cmd:evt-shift-entry:intent:tok-new:buy_yes',
            'ENTRY', 'tok-new', 'CANCELLED', ?, ?
        )
        """,
        (old_time, old_time),
    )
    trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))

    summary = release_stale_rebalance_entry_leases(trade_conn, observed_at=now_time)

    assert summary == {
        "advanced": 2,
        "stayed": 0,
        "planned_fill_up_released": 1,
        "shift_entry_scanned": 1,
        "shift_entry_advanced": 1,
        "shift_entry_stayed": 0,
        "errors": 0,
    }
    rows = {
        row["intent_id"]: dict(row)
        for row in trade_conn.execute(
            """
            SELECT intent_id, status, abort_reason
              FROM world.family_rebalance_intents
             ORDER BY intent_id
            """
        ).fetchall()
    }
    assert rows["intent-fill-up-planned"] == {
        "intent_id": "intent-fill-up-planned",
        "status": "ABORTED",
        "abort_reason": "FILL_UP_PLANNED_STALE_NO_DURABLE_COMMAND_RECOVERED",
    }
    assert rows["intent-shift-entry-cancelled"] == {
        "intent_id": "intent-shift-entry-cancelled",
        "status": "ABORTED",
        "abort_reason": "SHIFT_BIN_ENTRY_TERMINAL_NO_POSITION_BY_COMMAND_RECOVERY:state=CANCELLED",
    }
    trade_conn.close()


def test_live_tick_releases_stale_rebalance_before_broad_venue_snapshot(monkeypatch):
    """Family redecision locks must clear before the slow venue snapshot phase."""
    from src.execution import command_recovery
    from src.execution import exchange_reconcile
    from src.execution import venue_sync_contract

    events: list[str] = []

    class StopAfterCapture(RuntimeError):
        pass

    def empty_summary(label: str):
        def _inner(*_args, **_kwargs):
            events.append(label)
            return {
                "scanned": 0,
                "advanced": 0,
                "projected": 0,
                "stayed": 0,
                "errors": 0,
            }

        return _inner

    class FakeTracked:
        def __enter__(self):
            return object()

        def __exit__(self, *_exc):
            return False

    def fake_run_db_only_pass(apply, *, conn_factory=None, label=None):
        return apply(object())

    def fake_run_three_phase(
        snapshot,
        network,
        apply,
        *,
        conn_factory=None,
        snapshot_conn_factory=None,
        label=None,
    ):
        snap = snapshot(object())
        payload = network(snap)
        return apply(object(), payload)

    def fake_capture_venue_read_snapshot(*_args, **_kwargs):
        events.append("capture_venue_snapshot")
        raise StopAfterCapture("stop after ordering proof")

    monkeypatch.setattr(venue_sync_contract, "run_db_only_pass", fake_run_db_only_pass)
    monkeypatch.setattr(venue_sync_contract, "run_three_phase", fake_run_three_phase)
    monkeypatch.setattr(venue_sync_contract, "open_tracked", lambda *_a, **_k: FakeTracked())
    monkeypatch.setattr(
        venue_sync_contract,
        "capture_venue_read_snapshot",
        fake_capture_venue_read_snapshot,
    )
    monkeypatch.setattr(
        command_recovery,
        "_collect_recovery_priming_keys",
        lambda *_a, **_k: {
            "order_ids": set(),
            "idempotency_keys": set(),
            "condition_ids": set(),
        },
    )
    monkeypatch.setattr(
        command_recovery,
        "_edli_post_submit_unknown_absence_candidates",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        command_recovery,
        "reconcile_review_required_exit_mutex_releases",
        empty_summary("review_required_exit_mutex_release"),
    )
    monkeypatch.setattr(
        exchange_reconcile,
        "reconcile_recorded_exit_fill_projections",
        empty_summary("recorded_exit_fill_projection"),
    )
    monkeypatch.setattr(
        command_recovery,
        "reconcile_cancel_ack_terminal_no_fill_facts",
        empty_summary("cancel_ack_terminal_no_fill_facts"),
    )
    monkeypatch.setattr(
        command_recovery,
        "reconcile_terminal_order_facts",
        empty_summary("terminal_order_facts"),
    )
    monkeypatch.setattr(
        command_recovery,
        "release_closed_shift_bin_exit_leases",
        empty_summary("closed_shift_bin_exit_leases"),
    )
    monkeypatch.setattr(
        command_recovery,
        "release_stale_rebalance_entry_leases",
        empty_summary("stale_rebalance_entry_leases"),
    )

    with pytest.raises(StopAfterCapture):
        command_recovery.reconcile_unresolved_commands(client=object(), scope="live_tick")

    assert events.index("closed_shift_bin_exit_leases") < events.index("capture_venue_snapshot")
    assert events.index("stale_rebalance_entry_leases") < events.index("capture_venue_snapshot")


def test_live_tick_scope_still_clears_cancel_acked_zero_fill_pending_entry(monkeypatch, tmp_path):
    """Live cadence may defer heavy client sweeps, but must not leave confirmed
    cancel/no-fill pending-entry ghosts in the money path."""
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract
    from src.state.venue_command_repo import append_event

    db_path = tmp_path / "recovery-live-tick-cancelled.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema
    init_schema(seed_conn)
    init_collateral_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-cancelled", size=10.35, price=0.60)
    h._advance_to_acked(
        seed_conn,
        command_id="cmd-cancelled",
        venue_order_id="vord-cancelled",
    )
    h._seed_pending_entry_projection(
        seed_conn,
        position_id="pos-001",
        order_id="vord-cancelled",
    )
    h._append_order_fact(
        seed_conn,
        command_id="cmd-cancelled",
        order_id="vord-cancelled",
        state="LIVE",
        matched_size="0",
        remaining_size="10.35",
        source="REST",
    )
    append_event(
        seed_conn,
        command_id="cmd-cancelled",
        event_type="CANCEL_REQUESTED",
        occurred_at="2026-04-26T00:04:00Z",
        payload={"venue_order_id": "vord-cancelled"},
    )
    append_event(
        seed_conn,
        command_id="cmd-cancelled",
        event_type="CANCEL_ACKED",
        occurred_at="2026-04-26T00:05:00Z",
        payload={"venue_order_id": "vord-cancelled", "venue_status": "CANCELED"},
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(recorder)
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["scope"] == "live_tick"
    assert summary["deferred_full_sweep"] is True
    assert summary["cancel_ack_terminal_no_fill_facts"]["advanced"] == 1
    assert summary["terminal_order_facts"]["advanced"] == 1
    check = sqlite3.connect(str(db_path))
    check.row_factory = sqlite3.Row
    try:
        current = check.execute(
            "SELECT phase, shares, cost_basis_usd, order_status "
            "FROM position_current WHERE position_id='pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_status": "canceled",
        }
    finally:
        check.close()


def test_live_tick_scope_clears_terminal_point_order_zero_fill_pending_entry(monkeypatch, tmp_path):
    """A venue-canceled ACKED maker rest must not wait for the full sweep.

    This pins the Jeddah-shaped failure: command ACKED, latest order fact LIVE,
    pending-entry projection has zero exposure, and CLOB point-order truth says
    CANCELED with zero matched size. Live tick must append terminal no-fill
    truth and immediately void the pending entry, without network under a DB
    connection.
    """
    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract

    db_path = tmp_path / "recovery-live-tick-terminal-point.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema

    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-terminal-point", size=21.99, price=0.98)
    h._advance_to_acked(
        seed_conn,
        command_id="cmd-terminal-point",
        venue_order_id="vord-terminal-point",
    )
    h._seed_pending_entry_projection(
        seed_conn,
        command_id="cmd-terminal-point",
        order_id="vord-terminal-point",
    )
    h._append_order_fact(
        seed_conn,
        command_id="cmd-terminal-point",
        order_id="vord-terminal-point",
        state="LIVE",
        matched_size="0",
        remaining_size="21.99",
        source="REST",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={
            "vord-terminal-point": {
                "orderID": "vord-terminal-point",
                "status": "CANCELED",
                "original_size": "21.99",
                "size_matched": "0",
            }
        },
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["scope"] == "live_tick"
    assert summary["deferred_full_sweep"] is True
    assert summary["terminal_point_orders"]["advanced"] == 1
    assert summary["terminal_order_facts"]["advanced"] == 1
    assert any(call[0] == "get_order" for call in recorder.client_calls)
    for method, open_ids, open_labels in recorder.client_calls:
        assert not open_ids, (
            f"venue call {method} occurred while DB connections were open: {open_labels}"
        )

    check = sqlite3.connect(str(db_path))
    check.row_factory = sqlite3.Row
    try:
        current = check.execute(
            "SELECT phase, shares, cost_basis_usd, order_status "
            "FROM position_current WHERE position_id='pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_status": "canceled",
        }
        fact = check.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id='cmd-terminal-point'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(fact) == {
            "state": "CANCEL_CONFIRMED",
            "remaining_size": "0",
            "matched_size": "0",
            "source": "REST",
        }
    finally:
        check.close()


def test_live_tick_scope_releases_terminal_point_order_zero_fill_pending_exit(monkeypatch, tmp_path):
    """A terminal no-fill EXIT order must not strand a held position in pending_exit."""

    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract
    from src.state.db import init_schema

    db_path = tmp_path / "recovery-live-tick-terminal-exit.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-entry", position_id="pos-001")
    h._advance_to_acked(seed_conn, command_id="cmd-entry", venue_order_id="vord-entry")
    h._seed_pending_entry_projection(
        seed_conn,
        command_id="cmd-entry",
        order_id="vord-entry",
    )
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase='pending_exit',
               shares=10.0,
               cost_basis_usd=5.0,
               chain_shares=10.0,
               chain_state='synced',
               order_id='vord-exit',
               order_status='sell_pending_confirmation',
               target_date='2026-05-17',
               updated_at='2026-05-18T00:00:00+00:00'
         WHERE position_id='pos-001'
        """
    )
    h._insert(
        seed_conn,
        command_id="cmd-exit",
        position_id="pos-001",
        intent_kind="EXIT",
        side="SELL",
    )
    h._advance_to_acked(seed_conn, command_id="cmd-exit", venue_order_id="vord-exit")
    h._append_order_fact(
        seed_conn,
        command_id="cmd-exit",
        order_id="vord-exit",
        state="LIVE",
        matched_size="0",
        remaining_size="10",
        source="REST",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={
            "vord-exit": {
                "orderID": "vord-exit",
                "status": "UNKNOWN",
            }
        },
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["terminal_point_orders"]["advanced"] == 1
    for method, open_ids, open_labels in recorder.client_calls:
        assert not open_ids, (
            f"venue call {method} occurred while DB connections were open: {open_labels}"
        )

    check = sqlite3.connect(str(db_path))
    check.row_factory = sqlite3.Row
    try:
        current = check.execute(
            "SELECT phase, order_status, order_id, exit_reason "
            "FROM position_current WHERE position_id='pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "day0_window",
            "order_status": "filled",
            "order_id": None,
            "exit_reason": "EXIT_ORDER_TERMINAL_NO_FILL_RELEASED",
        }
        command = check.execute(
            "SELECT state FROM venue_commands WHERE command_id='cmd-exit'"
        ).fetchone()
        assert command["state"] == "EXPIRED"
        event = check.execute(
            """
            SELECT event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id='pos-001'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(event) == {
            "event_type": "EXIT_ORDER_VOIDED",
            "phase_before": "pending_exit",
            "phase_after": "day0_window",
            "command_id": "cmd-exit",
            "order_id": "vord-exit",
        }
    finally:
        check.close()


def test_live_tick_scope_closes_pending_exit_from_unknown_point_confirmed_maker_trade(monkeypatch, tmp_path):
    """A point-order UNKNOWN response is not no-fill when user trades prove our maker leg filled."""

    import tests.test_command_recovery as h
    from src.execution import command_recovery, venue_sync_contract
    from src.state.db import init_schema

    db_path = tmp_path / "recovery-live-tick-terminal-exit-confirmed-trade.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    init_schema(seed_conn)
    h._insert(seed_conn, command_id="cmd-entry", position_id="pos-001")
    h._advance_to_acked(seed_conn, command_id="cmd-entry", venue_order_id="vord-entry")
    h._seed_pending_entry_projection(
        seed_conn,
        command_id="cmd-entry",
        order_id="vord-entry",
    )
    seed_conn.execute(
        """
        UPDATE position_current
           SET phase='pending_exit',
               shares=10.0,
               cost_basis_usd=5.0,
               chain_shares=10.0,
               chain_state='synced',
               order_id='vord-exit',
               order_status='sell_pending_confirmation',
               target_date='2026-05-17',
               updated_at='2026-05-18T00:00:00+00:00'
         WHERE position_id='pos-001'
        """
    )
    h._insert(
        seed_conn,
        command_id="cmd-exit",
        position_id="pos-001",
        intent_kind="EXIT",
        side="SELL",
        size=10.0,
        price=0.49,
        token_id="tok-001",
    )
    h._advance_to_acked(seed_conn, command_id="cmd-exit", venue_order_id="vord-exit")
    h._append_order_fact(
        seed_conn,
        command_id="cmd-exit",
        order_id="vord-exit",
        state="LIVE",
        matched_size="0",
        remaining_size="10",
        source="REST",
    )
    seed_conn.commit()
    seed_conn.close()

    recorder = _Recorder()
    factory = _make_conn_factory(db_path, recorder)
    client = _RecordingClient(
        recorder,
        orders={
            "vord-exit": {
                "orderID": "vord-exit",
                "status": "UNKNOWN",
            }
        },
        trades=[
            {
                "id": "trade-exit-001",
                "status": "CONFIRMED",
                "market": "condition-test",
                "asset_id": "tok-yes",
                "side": "SELL",
                "price": "0.50",
                "size": "50",
                "match_time": "2026-05-18T00:01:00+00:00",
                "maker_orders": [
                    {
                        "order_id": "vord-exit",
                        "asset_id": "tok-001",
                        "side": "SELL",
                        "price": "0.49",
                        "matched_amount": "10",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", factory)

    summary = command_recovery.reconcile_unresolved_commands(
        conn=None,
        client=client,
        scope="live_tick",
    )

    assert summary["terminal_point_orders"]["advanced"] == 1
    for method, open_ids, open_labels in recorder.client_calls:
        assert not open_ids, (
            f"venue call {method} occurred while DB connections were open: {open_labels}"
        )

    check = sqlite3.connect(str(db_path))
    check.row_factory = sqlite3.Row
    try:
        command = check.execute(
            "SELECT state FROM venue_commands WHERE command_id='cmd-exit'"
        ).fetchone()
        assert command["state"] == "FILLED"
        current = check.execute(
            "SELECT phase, order_status, exit_price FROM position_current WHERE position_id='pos-001'"
        ).fetchone()
        assert current["phase"] == "economically_closed"
        assert current["order_status"] == "sell_filled"
        assert current["exit_price"] == pytest.approx(0.49)
        trade_fact = check.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price
              FROM venue_trade_facts
             WHERE command_id='cmd-exit'
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-exit-001",
            "venue_order_id": "vord-exit",
            "state": "CONFIRMED",
            "filled_size": "10",
            "fill_price": "0.49",
        }
        order_fact = check.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id='cmd-exit'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(order_fact) == {
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "10",
        }
    finally:
        check.close()


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


def test_three_phase_uses_distinct_snapshot_and_write_factories():
    """Slow read snapshots must never open the writer-flocked factory."""
    from src.execution import venue_sync_contract as vsc

    events = []

    def _read_factory():
        events.append("read")
        return sqlite3.connect(":memory:")

    def _write_factory():
        events.append("write")
        return sqlite3.connect(":memory:")

    result = vsc.run_three_phase(
        lambda conn: conn.execute("SELECT 1").fetchone()[0],
        lambda snap: events.append("network") or snap,
        lambda conn, payload: conn.execute("SELECT ?", (payload,)).fetchone()[0],
        conn_factory=_write_factory,
        snapshot_conn_factory=_read_factory,
        label="test.distinct_factories",
    )

    assert result == 1
    assert events == ["read", "network", "write"]


def test_default_factory_holds_canonical_cross_db_flocks_until_close(monkeypatch):
    """Recovery cannot expose TRADE-main lock order to WORLD-main writers."""
    from src.execution import venue_sync_contract as vsc
    from src.state import db

    events = []

    @contextlib.contextmanager
    def _flocked(*, write_class, blocking=True):
        events.append(("enter", write_class, blocking))
        conn = sqlite3.connect(":memory:")
        try:
            yield conn
        finally:
            conn.close()
            events.append(("exit", write_class, blocking))

    monkeypatch.setattr(db, "trade_connection_with_world_flocked", _flocked)

    conn = vsc.default_trade_conn_factory()
    assert events == [("enter", "live", True)]
    assert conn.execute("SELECT 1").fetchone()[0] == 1

    conn.close()
    conn.close()
    assert events == [("enter", "live", True), ("exit", "live", True)]


def test_default_factory_can_yield_without_flock_or_sqlite_wait(monkeypatch):
    from src.execution import venue_sync_contract as vsc
    from src.state import db

    calls = []

    @contextlib.contextmanager
    def _flocked(*, write_class, blocking=True):
        calls.append((write_class, blocking))
        conn = sqlite3.connect(":memory:")
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(db, "trade_connection_with_world_flocked", _flocked)

    conn = vsc.default_trade_conn_factory(blocking=False, busy_timeout_ms=0)
    try:
        busy_timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert calls == [("live", False)]
    assert busy_timeout_ms == 0


def test_default_read_factory_does_not_take_writer_flocks(monkeypatch):
    """A slow recovery snapshot must not exclude live world writers."""
    from src.execution import venue_sync_contract as vsc
    from src.state import db

    calls = []
    conn = sqlite3.connect(":memory:")

    def _required(*, write_class):
        calls.append(write_class)
        return conn

    monkeypatch.setattr(db, "get_trade_connection_with_world_required", _required)

    read_conn = vsc.default_trade_read_conn_factory()

    assert read_conn is conn
    assert calls == [None]


def test_recovery_waits_before_taking_trade_when_world_main_writer_is_active(
    monkeypatch, tmp_path
):
    """Canonical flocks prevent the observed WORLD-held/TRADE-held inversion."""
    from src.execution import venue_sync_contract as vsc
    from src.state import db

    world_path = tmp_path / "zeus-world.db"
    trade_path = tmp_path / "zeus_trades.db"
    for path, ddl in ((world_path, "CREATE TABLE w (v INTEGER)"), (trade_path, "CREATE TABLE t (v INTEGER)")):
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(ddl)
        conn.commit()
        conn.close()

    monkeypatch.setattr(db, "ZEUS_WORLD_DB_PATH", world_path)
    monkeypatch.setattr(db, "_zeus_trade_db_path", lambda: trade_path)

    world_written = threading.Event()
    release_world = threading.Event()
    recovery_opened = threading.Event()
    errors = []

    def _world_main_writer():
        mutex = db.world_write_mutex()
        acquired = mutex.acquire(timeout=1.0)
        try:
            assert acquired
            conn = sqlite3.connect(world_path, timeout=2.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("ATTACH DATABASE ? AS trades", (str(trade_path),))
            conn.execute("INSERT INTO w VALUES (1)")
            world_written.set()
            assert release_world.wait(2.0)
            conn.execute("INSERT INTO trades.t VALUES (1)")
            conn.commit()
            conn.close()
        except BaseException as exc:  # noqa: BLE001 - surface thread failures
            errors.append(exc)
        finally:
            if acquired:
                mutex.release()

    def _trade_main_recovery():
        try:
            assert world_written.wait(2.0)
            conn = vsc.default_trade_conn_factory()
            recovery_opened.set()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO t VALUES (2)")
            conn.commit()
            conn.close()
        except BaseException as exc:  # noqa: BLE001 - surface thread failures
            errors.append(exc)

    world_thread = threading.Thread(target=_world_main_writer, daemon=True)
    recovery_thread = threading.Thread(target=_trade_main_recovery, daemon=True)
    world_thread.start()
    assert world_written.wait(2.0)
    recovery_thread.start()
    time.sleep(0.1)
    try:
        assert not recovery_opened.is_set(), (
            "recovery opened TRADE while the inverse WORLD-main writer held WORLD"
        )
    finally:
        release_world.set()
        world_thread.join(3.0)
        recovery_thread.join(3.0)

    assert not world_thread.is_alive() and not recovery_thread.is_alive()
    assert errors == []
    assert recovery_opened.is_set()


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
