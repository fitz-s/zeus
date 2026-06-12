# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator shared-wallet manual unwind incident 2026-06-10 —
#   operator placed a resting in-Zeus-domain SELL (66.25 YES @0.016, Milan high
#   2026-06-11, size_matched=0) to unwind a Zeus position at cost on the shared
#   proxy wallet, then resolved the resulting exchange_ghost_order finding
#   (resolved_by='session_operator_confirmed', resolution prefix
#   'operator_manual_unwind_shared_wallet:'). The sweep RE-RECORDED a fresh
#   unresolved ghost for the same subject minutes later, freezing the engine:
#   risk_allocator reconcile_finding_threshold + the WS two-proofs M5
#   zero-findings latch both saw an unresolved finding. Unlike a foreign-wallet
#   order this market IS in Zeus's domain, so the foreign-wallet classifier
#   correctly does not apply — a separate operator-acknowledged antibody is needed.
"""RELATIONSHIP tests: reconcile sweep -> governor kill-switch boundary.

Cross-module invariant (run_reconcile_sweep -> count_open_reconcile_findings AND
run_reconcile_sweep's returned findings list / list_unresolved_findings):
  An IN-DOMAIN resting (size_matched=0) ghost order that the operator has
  EXPLICITLY acknowledged (a prior resolved finding marked operator_manual* /
  session_operator_confirmed) must never raise the governor's unresolved count nor
  appear as a fresh sweep finding — it is record-and-resolved in the same sweep.
  The moment any matched size appears (the order starts filling) the acknowledgment
  is VOID and a fresh unresolved finding is recorded (fail-closed, mirroring the
  foreign-wallet matched-size tripwire). An un-acknowledged in-domain ghost is
  unchanged: it stays unresolved and arms the kill switch.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.execution.exchange_reconcile import (
    _OPERATOR_ACK_GHOST_RESOLUTION,
    init_exchange_reconcile_schema,
    list_unresolved_findings,
    record_finding,
    resolve_finding,
    run_reconcile_sweep,
)
from src.risk_allocator.governor import count_open_reconcile_findings

NOW = datetime(2026, 6, 10, 7, 0, tzinfo=timezone.utc)
ZEUS_MARKET = "0x" + "a1" * 32
ACK_SUBJECT = "0xd4e9048fb989b3d9d9295d0fd4af7cdca8c47a93e1df1a49322b284545e02617"


class _SnapshotAdapter:
    """Pre-captured read-only snapshot adapter (no live I/O), sweep-shaped."""

    def __init__(self, open_orders):
        self._open_orders = open_orders
        self.read_freshness = {"open_orders": True}

    def get_open_orders(self):
        return list(self._open_orders)


def _order(order_id: str, market: str, *, size_matched: str = "0") -> dict:
    return {
        "id": order_id,
        "market": market,
        "asset_id": "9" * 20,
        "order_type": "GTC",
        "original_size": "66.25",
        "outcome": "Yes",
        "price": "0.016",
        "side": "SELL",
        "size_matched": size_matched,
        "status": "LIVE",
        "maker_address": "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f",
    }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_exchange_reconcile_schema(conn)
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY, market_id TEXT, token_id TEXT,
            state TEXT, venue_order_id TEXT, updated_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE TABLE executable_market_snapshots (snapshot_id TEXT, condition_id TEXT)"
    )
    # The market IS in Zeus's domain (Zeus traded it): the foreign-wallet classifier
    # MUST NOT apply here.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap1', ?)", (ZEUS_MARKET,)
    )
    return conn


def _sweep(conn, open_orders):
    return run_reconcile_sweep(
        _SnapshotAdapter(open_orders), conn, context="periodic", observed_at=NOW
    )


def _acknowledge(conn, subject_id: str, *, resolved_by: str, resolution: str) -> None:
    """Record then resolve a ghost finding the way the operator did live."""

    finding = record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=subject_id,
        context="ws_gap",
        evidence={
            "exchange_order": _order(subject_id, ZEUS_MARKET),
            "reason": "exchange_open_order_absent_from_venue_commands",
        },
        recorded_at=NOW,
    )
    resolve_finding(
        conn,
        finding.finding_id,
        resolution=resolution,
        resolved_by=resolved_by,
        resolved_at=NOW,
    )


# ---- RELATIONSHIP: acknowledged unfilled order never arms the governor ----------------
def test_acknowledged_unfilled_order_does_not_raise_governor_count() -> None:
    conn = _conn()
    _acknowledge(
        conn,
        ACK_SUBJECT,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_shared_wallet: SELL 66.25 YES @0.016",
    )
    # Live re-record: the order is still resting on the venue.
    _sweep(conn, [_order(ACK_SUBJECT, ZEUS_MARKET)])
    assert count_open_reconcile_findings(conn) == 0, (
        "an operator-acknowledged unfilled in-domain resting order must not arm the "
        "reconcile kill switch — the 2026-06-10 shared-wallet unwind freeze"
    )
    assert list_unresolved_findings(conn) == [], (
        "the M5 ws-gap zero-findings latch must see no unresolved findings"
    )
    # Audit trail kept: a resolved row with the rollforward resolution exists.
    rows = conn.execute(
        "SELECT resolution, resolved_at, evidence_json FROM exchange_reconcile_findings "
        "WHERE subject_id=? AND resolution=?",
        (ACK_SUBJECT, _OPERATOR_ACK_GHOST_RESOLUTION),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["resolved_at"] is not None
    assert (
        json.loads(rows[0]["evidence_json"])["classification"]
        == "operator_acknowledged_ghost_order"
    )


def test_acknowledged_via_operator_manual_prefix_only() -> None:
    # The live row used resolved_by='session_operator_confirmed'; the prefix path is
    # exercised independently (resolved_by some other marker, resolution operator_manual*).
    conn = _conn()
    _acknowledge(
        conn,
        ACK_SUBJECT,
        resolved_by="src.execution.exchange_reconcile",
        resolution="operator_manual_unwind_shared_wallet: SELL leg",
    )
    _sweep(conn, [_order(ACK_SUBJECT, ZEUS_MARKET)])
    assert count_open_reconcile_findings(conn) == 0


# ---- RELATIONSHIP: a fill voids the acknowledgment (fail-closed) ----------------------
def test_acknowledged_order_that_starts_filling_records_fresh_finding() -> None:
    conn = _conn()
    _acknowledge(
        conn,
        ACK_SUBJECT,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_shared_wallet: SELL 66.25 YES @0.016",
    )
    # The operator's order started matching — acknowledgment is VOID.
    _sweep(conn, [_order(ACK_SUBJECT, ZEUS_MARKET, size_matched="5")])
    assert count_open_reconcile_findings(conn) == 1, (
        "any matched size on the shared wallet voids the acknowledgment — a fill is "
        "never auto-suppressed (mirror foreign-wallet strictness)"
    )
    unresolved = list_unresolved_findings(conn)
    assert len(unresolved) == 1
    assert unresolved[0].subject_id == ACK_SUBJECT


# ---- RELATIONSHIP: un-acknowledged in-domain ghost is unchanged -----------------------
def test_unacknowledged_in_domain_ghost_still_arms_governor() -> None:
    conn = _conn()
    _sweep(conn, [_order("0xneveracked", ZEUS_MARKET)])
    assert count_open_reconcile_findings(conn) == 1, (
        "an in-domain ghost the operator never acknowledged is the original disease "
        "and must stay fail-closed"
    )


# ---- MIGRATION: a re-recorded unresolved row for an acknowledged subject resolves -----
def test_existing_unresolved_rerecorded_finding_resolved_by_sweep() -> None:
    conn = _conn()
    _acknowledge(
        conn,
        ACK_SUBJECT,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_shared_wallet: SELL 66.25 YES @0.016",
    )
    # The whack-a-mole row: a fresh unresolved ghost re-recorded after the manual ack.
    record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=ACK_SUBJECT,
        context="ws_gap",
        evidence={
            "exchange_order": _order(ACK_SUBJECT, ZEUS_MARKET),
            "reason": "exchange_open_order_absent_from_venue_commands",
        },
        recorded_at=NOW,
    )
    assert count_open_reconcile_findings(conn) == 1
    # The order is still resting on the venue (would re-ghost without the antibody).
    _sweep(conn, [_order(ACK_SUBJECT, ZEUS_MARKET)])
    assert count_open_reconcile_findings(conn) == 0
    assert list_unresolved_findings(conn) == []


def test_refresh_path_resolves_acknowledged_findings_without_venue_reads() -> None:
    # The 1-minute runtime refresh (refresh_unresolved_reconcile_findings) must clear
    # the re-recorded acknowledged finding from local evidence alone — no venue read,
    # no waiting for the next full ws-gap sweep.
    from src.execution.exchange_reconcile import refresh_unresolved_reconcile_findings

    conn = _conn()
    _acknowledge(
        conn,
        ACK_SUBJECT,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_shared_wallet: SELL 66.25 YES @0.016",
    )
    record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=ACK_SUBJECT,
        context="ws_gap",
        evidence={
            "exchange_order": _order(ACK_SUBJECT, ZEUS_MARKET),
            "reason": "exchange_open_order_absent_from_venue_commands",
        },
        recorded_at=NOW,
    )
    assert count_open_reconcile_findings(conn) == 1

    class _NoReadAdapter:  # any venue read would explode — none must happen
        def __getattr__(self, name):
            raise AssertionError(f"venue read attempted: {name}")

    result = refresh_unresolved_reconcile_findings(_NoReadAdapter(), conn, observed_at=NOW)
    assert result["status"] == "not_required"
    assert result["resolved"] == 1
    assert count_open_reconcile_findings(conn) == 0


def test_rerecorded_finding_with_fill_is_not_resolved_by_migration() -> None:
    # If the re-recorded evidence shows the order already filled, the migration pass
    # must NOT resolve it — a fill voids the acknowledgment even on the local-evidence
    # resolve path.
    conn = _conn()
    _acknowledge(
        conn,
        ACK_SUBJECT,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_shared_wallet: SELL 66.25 YES @0.016",
    )
    record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=ACK_SUBJECT,
        context="ws_gap",
        evidence={
            "exchange_order": _order(ACK_SUBJECT, ZEUS_MARKET, size_matched="5"),
            "reason": "exchange_open_order_absent_from_venue_commands",
        },
        recorded_at=NOW,
    )
    # No live open order this sweep; only the migration pass runs over the recorded row.
    _sweep(conn, [])
    assert count_open_reconcile_findings(conn) == 1, (
        "a recorded fill must keep the finding unresolved even on the migration path"
    )


def test_repeated_sweeps_do_not_churn_duplicate_audit_rows() -> None:
    conn = _conn()
    _acknowledge(
        conn,
        ACK_SUBJECT,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_shared_wallet: SELL 66.25 YES @0.016",
    )
    for _ in range(3):
        _sweep(conn, [_order(ACK_SUBJECT, ZEUS_MARKET)])
    n = conn.execute(
        "SELECT COUNT(*) FROM exchange_reconcile_findings "
        "WHERE subject_id=? AND resolution=?",
        (ACK_SUBJECT, _OPERATOR_ACK_GHOST_RESOLUTION),
    ).fetchone()[0]
    assert n == 1, "one rollforward audit row per acknowledged order, not one per sweep"
