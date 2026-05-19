# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: .omc/plans/2026-05-19-kill-switch-and-misroute-antibody-fix.md (defect #1)
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — run_reconcile_sweep MUST resolve exchange_ghost_order
#          findings once their subject_id disappears from the open-orders snapshot
#          (canceled, filled, expired). Without this, kill_switch stays armed
#          forever and entry orders are blocked.
"""Antibody: ghost_order finding auto-resolution on order disappearance.

Root cause (2026-05-19 live-trade dead): four `exchange_ghost_order` findings
were recorded by `run_reconcile_sweep` at 21:03:25 (context=ws_gap). The
operator subsequently cancelled all four orders via the CLOB SDK
(`sdk.cancel_orders(...)` returned `canceled: [...4 ids], not_canceled: {}`).
However, `run_reconcile_sweep` had no code path to resolve the four findings.
`governor` keeps `kill_switch_armed=True` while any `exchange_ghost_order`
remains `resolved_at IS NULL`, so every entry candidate the
`opening_hunt` / `imminent_open_capture` cycles discovered was vetoed by the
kill-switch gate. 0 entry orders shipped for hours.

Fix: at the end of `run_reconcile_sweep`, for each open ghost_order finding
whose `subject_id` is no longer in the live `open_order_ids` snapshot, call
`resolve_finding(...)` with resolution
`exchange_ghost_order_no_longer_in_open_orders`. Disappearance from the
open-orders enumeration is the truthful resolution signal: an order can only
leave that set by being canceled, filled, or expired.

Antibody contracts (sed-flip verifiable):
  T1: Ghost finding with subject_id ABSENT from current open_order_ids →
      resolved_at IS NOT NULL after sweep; resolution string matches.
  T2: Ghost finding with subject_id STILL PRESENT in open_order_ids →
      resolved_at IS NULL after sweep (still open).
  T3: Ghost finding for a DIFFERENT kind (position_drift) → NOT touched by
      the new ghost-order resolution path (separation of concerns).
  T4: Multiple ghost findings, mixed presence → only the absent ones resolve;
      counter return value matches.

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

    def __init__(self, open_orders=None, trades=None, positions=None):
        self._open = list(open_orders or [])
        self._trades = list(trades or [])
        self._positions = list(positions or [])
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


def _seed_ghost_finding(conn, subject_id: str, *, context: str = "ws_gap"):
    return record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=subject_id,
        context=context,
        evidence={"exchange_order": {"id": subject_id}, "reason": "test_seed"},
        recorded_at=NOW,
    )


def test_t1_ghost_finding_resolves_when_subject_absent(conn):
    """T1: subject_id NOT in live open_order_ids → finding resolved."""
    seeded = _seed_ghost_finding(conn, "0xORDER_CANCELED")
    open_order_ids: set[str] = set()

    resolved = _resolve_disappeared_ghost_order_findings(
        conn, open_order_ids, observed_at=NOW
    )

    assert resolved == 1, f"expected 1 resolved, got {resolved}"
    row = conn.execute(
        "SELECT resolved_at, resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (seeded.finding_id,),
    ).fetchone()
    assert row["resolved_at"] is not None, (
        "T1 FAIL: ghost_order finding stayed unresolved — kill_switch would "
        "stay armed forever after cancellation."
    )
    assert row["resolution"] == "exchange_ghost_order_no_longer_in_open_orders"
    assert row["resolved_by"] == "src.execution.exchange_reconcile"


def test_t2_ghost_finding_stays_open_when_subject_still_present(conn):
    """T2: subject_id STILL in open_order_ids → finding stays open."""
    seeded = _seed_ghost_finding(conn, "0xORDER_STILL_LIVE")
    open_order_ids = {"0xORDER_STILL_LIVE"}

    resolved = _resolve_disappeared_ghost_order_findings(
        conn, open_order_ids, observed_at=NOW
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

    _resolve_disappeared_ghost_order_findings(
        conn, open_order_ids, observed_at=NOW
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
    """T4: mixed batch — only absent ones resolve; counter is correct."""
    seeded = [
        _seed_ghost_finding(conn, "0xABSENT_1"),
        _seed_ghost_finding(conn, "0xPRESENT_1"),
        _seed_ghost_finding(conn, "0xABSENT_2"),
        _seed_ghost_finding(conn, "0xPRESENT_2"),
    ]
    open_order_ids = {"0xPRESENT_1", "0xPRESENT_2"}

    resolved = _resolve_disappeared_ghost_order_findings(
        conn, open_order_ids, observed_at=NOW
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
    with an adapter that no longer reports that order in open_orders →
    finding must be resolved by the sweep itself, not just the helper.

    This is the CRITICAL contract because the daemon calls run_reconcile_sweep,
    not the helper directly. Sed-flip: comment out the
    `_resolve_disappeared_ghost_order_findings` call inside run_reconcile_sweep
    → this test goes RED.
    """
    seeded = _seed_ghost_finding(conn, "0xCANCELED_BY_OPERATOR")
    adapter = _StubAdapter(open_orders=[], trades=[], positions=[])

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
    assert row["resolution"] == "exchange_ghost_order_no_longer_in_open_orders"
