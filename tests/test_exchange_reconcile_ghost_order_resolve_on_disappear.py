# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19-2.md P1-5 (proof-backed resolution)
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — run_reconcile_sweep MUST resolve exchange_ghost_order
#          findings only when backed by at least one proof (terminal point-order,
#          linked trade fact, no token exposure, or no matching trade in enumeration).
#          Disappearance alone is NOT sufficient — a read-miss must not arm the
#          kill-switch resolve.
"""Antibody: ghost_order finding proof-backed resolution on order disappearance.

Root cause (2026-05-19 live-trade dead): four `exchange_ghost_order` findings
were recorded by `run_reconcile_sweep` at 21:03:25 (context=ws_gap). The
operator subsequently cancelled all four orders via the CLOB SDK.
However, `run_reconcile_sweep` had no code path to resolve the four findings.
`governor` keeps `kill_switch_armed=True` while any `exchange_ghost_order`
remains `resolved_at IS NULL`, so every entry candidate the
`opening_hunt` / `imminent_open_capture` cycles discovered was vetoed by the
kill-switch gate. 0 entry orders shipped for hours.

Stricter fix (codereview-may19-2.md P1-5): disappearance from open-orders is
necessary but not sufficient. At least one proof must fire before resolution:
  (a) get_order(id) returns a terminal status (CANCELLED/EXPIRED/REJECTED/FILLED)
  (c) venue_trade_facts has a row with venue_order_id = subject_id
  (d) position_current shows no shares exposure for order_id = subject_id
  (b) get_trades enumeration finds no matching trade for the order_id

If NONE fire, the finding stays unresolved (kill-switch stays armed, fail-closed).

Antibody contracts (sed-flip verifiable):
  T1: Ghost finding absent from open_order_ids + adapter provides CANCELLED
      point-order → resolved; resolution encodes terminal status.
  T2: Ghost finding STILL PRESENT in open_order_ids → stays open (unchanged).
  T3: position_drift finding NOT touched by ghost-order path (unchanged).
  T4: Mixed batch — absent ones WITH proof resolve; counter correct.

Sed-flip: comment out the `_resolve_disappeared_ghost_order_findings` call in
`run_reconcile_sweep` → T1 + T4 fail (resolved_at stays None).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.execution.exchange_reconcile import (
    _resolve_disappeared_ghost_order_findings,
    init_exchange_reconcile_schema,
    record_finding,
    run_reconcile_sweep,
)
from src.state.db import init_schema


NOW = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_exchange_reconcile_schema(db)
    yield db
    db.close()


class _StubAdapter:
    """Minimal adapter exposing only what `run_reconcile_sweep` calls."""

    def __init__(self, open_orders=None, trades=None, positions=None, point_orders=None):
        self._open = list(open_orders or [])
        self._trades = list(trades or [])
        self._positions = list(positions or [])
        # point_orders: dict[order_id -> {"status": "CANCELLED"/"FILLED"/...}]
        self._point_orders: dict = dict(point_orders or {})
        # Reconciler freshness contract: value can be True or
        # {"ok": True, "fresh": True} per surface.
        self.read_freshness = {
            "open_orders": True,
            "trades": True,
            "positions": True,
        }

    def get_open_orders(self):
        return self._open

    def get_trades(self):
        return self._trades

    def get_positions(self):
        return self._positions

    def get_order(self, order_id: str):
        return self._point_orders.get(str(order_id))


def _seed_ghost_finding(conn, subject_id: str, *, context: str = "ws_gap"):
    return record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=subject_id,
        context=context,
        evidence={"exchange_order": {"id": subject_id}, "reason": "test_seed"},
        recorded_at=NOW,
    )


def test_t1_ghost_finding_resolves_when_subject_absent_with_terminal_proof(conn):
    """T1: subject_id NOT in live open_order_ids AND point-order returns CANCELLED
    → finding resolved; resolution string encodes terminal status.

    Stricter contract (P1-5): disappearance alone is not sufficient — adapter
    must supply at least one proof. Here proof (a): get_order returns CANCELLED.
    """
    seeded = _seed_ghost_finding(conn, "0xORDER_CANCELED")
    open_order_ids: set[str] = set()
    adapter = _StubAdapter(
        point_orders={"0xORDER_CANCELED": {"status": "CANCELLED"}},
    )

    resolved = _resolve_disappeared_ghost_order_findings(
        adapter, conn, open_order_ids, observed_at=NOW
    )

    assert resolved == 1, f"expected 1 resolved, got {resolved}"
    row = conn.execute(
        "SELECT resolved_at, resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (seeded.finding_id,),
    ).fetchone()
    assert row["resolved_at"] is not None, (
        "T1 FAIL: ghost_order finding stayed unresolved — kill_switch would "
        "stay armed forever after cancellation with terminal proof."
    )
    assert row["resolution"] == "exchange_ghost_order_terminal_point_order_cancelled", (
        f"T1 FAIL: unexpected resolution {row['resolution']!r}"
    )
    assert row["resolved_by"] == "src.execution.exchange_reconcile"


def test_t2_ghost_finding_stays_open_when_subject_still_present(conn):
    """T2: subject_id STILL in open_order_ids → finding stays open."""
    seeded = _seed_ghost_finding(conn, "0xORDER_STILL_LIVE")
    open_order_ids = {"0xORDER_STILL_LIVE"}
    adapter = _StubAdapter(
        point_orders={"0xORDER_STILL_LIVE": {"status": "CANCELLED"}},
    )

    resolved = _resolve_disappeared_ghost_order_findings(
        adapter, conn, open_order_ids, observed_at=NOW
    )

    assert resolved == 0, f"T2 FAIL: expected 0 resolved, got {resolved}"
    row = conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id = ?",
        (seeded.finding_id,),
    ).fetchone()
    assert row["resolved_at"] is None, (
        "T2 FAIL: live ghost order was incorrectly resolved — would mask a "
        "real ghost-order signal."
    )


def test_t3_position_drift_finding_not_touched_by_ghost_resolution(conn):
    """T3: the new resolution path scopes to kind='exchange_ghost_order';
    other kinds (position_drift, unrecorded_trade) MUST be untouched."""
    drift = record_finding(
        conn,
        kind="position_drift",
        subject_id="token_42",
        context="periodic",
        evidence={"exchange_size": "1.0", "journal_size": "0.0"},
        recorded_at=NOW,
    )
    open_order_ids: set[str] = set()
    adapter = _StubAdapter()

    _resolve_disappeared_ghost_order_findings(
        adapter, conn, open_order_ids, observed_at=NOW
    )

    row = conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id = ?",
        (drift.finding_id,),
    ).fetchone()
    assert row["resolved_at"] is None, (
        "T3 FAIL: position_drift finding was resolved by the ghost-order path — "
        "separation of concerns violated."
    )


def test_t4_multiple_findings_mixed_presence(conn):
    """T4: mixed batch — only absent ones WITH proof resolve; counter is correct.

    Absent orders 0xABSENT_1 and 0xABSENT_2 have get_order returning CANCELLED;
    present ones (0xPRESENT_1/2) are skipped because they are still in open_order_ids.
    """
    seeded = [
        _seed_ghost_finding(conn, "0xABSENT_1"),
        _seed_ghost_finding(conn, "0xPRESENT_1"),
        _seed_ghost_finding(conn, "0xABSENT_2"),
        _seed_ghost_finding(conn, "0xPRESENT_2"),
    ]
    open_order_ids = {"0xPRESENT_1", "0xPRESENT_2"}
    adapter = _StubAdapter(
        point_orders={
            "0xABSENT_1": {"status": "CANCELLED"},
            "0xABSENT_2": {"status": "EXPIRED"},
        }
    )

    resolved = _resolve_disappeared_ghost_order_findings(
        adapter, conn, open_order_ids, observed_at=NOW
    )

    assert resolved == 2, f"T4 FAIL: expected 2 resolved, got {resolved}"
    by_subject = {
        row["subject_id"]: row["resolved_at"]
        for row in conn.execute(
            "SELECT subject_id, resolved_at FROM exchange_reconcile_findings"
        ).fetchall()
    }
    assert by_subject["0xABSENT_1"] is not None
    assert by_subject["0xABSENT_2"] is not None
    assert by_subject["0xPRESENT_1"] is None
    assert by_subject["0xPRESENT_2"] is None


def test_t5_end_to_end_sweep_resolves_ghost_after_order_disappears(conn):
    """T5 (end-to-end): seed a ghost finding for an order. Run the full sweep
    with an adapter that no longer reports that order in open_orders AND supplies
    a CANCELLED point-order → finding must be resolved by the sweep itself.

    This is the CRITICAL contract because the daemon calls run_reconcile_sweep,
    not the helper directly. Sed-flip: comment out the
    `_resolve_disappeared_ghost_order_findings` call inside run_reconcile_sweep
    → this test goes RED.

    Proof path used: (a) get_order returns CANCELLED — terminal status.
    """
    seeded = _seed_ghost_finding(conn, "0xCANCELED_BY_OPERATOR")
    adapter = _StubAdapter(
        open_orders=[],
        trades=[],
        positions=[],
        point_orders={"0xCANCELED_BY_OPERATOR": {"status": "CANCELLED"}},
    )

    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    row = conn.execute(
        "SELECT resolved_at, resolution FROM exchange_reconcile_findings WHERE finding_id = ?",
        (seeded.finding_id,),
    ).fetchone()
    assert row["resolved_at"] is not None, (
        "T5 FAIL: end-to-end run_reconcile_sweep did NOT resolve the disappeared "
        "ghost_order finding. Daemon would keep kill_switch_armed forever after "
        "operator cancels. The resolve helper must be wired into the sweep."
    )
    assert row["resolution"] == "exchange_ghost_order_terminal_point_order_cancelled", (
        f"T5 FAIL: unexpected resolution {row['resolution']!r}"
    )
