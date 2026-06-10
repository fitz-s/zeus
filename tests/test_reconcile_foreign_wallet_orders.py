# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: .omc/plans/2026-06-09-foreign-wallet-ghost-classification.md —
#   2026-06-09 kill-switch incident: operator manual GTC orders on AI-themed markets
#   (shared proxy wallet) recorded as exchange_ghost_order findings, tripping
#   reconcile_finding_threshold (limit=0) and freezing ALL Zeus entries (reduce_only).
#   Zeus's exclusive-wallet assumption is false; domain membership (condition_id in
#   executable_market_snapshots OR market_id in venue_commands) defines whether an
#   open venue order can be a lost Zeus side effect.
"""RELATIONSHIP tests: reconcile sweep -> governor kill-switch boundary.

Cross-module invariant (run_reconcile_sweep -> count_open_reconcile_findings):
  A resting (size_matched=0) open order on a market entirely OUTSIDE Zeus's domain
  must never raise the governor's unresolved-finding count — it is recorded for
  audit and resolved in the same sweep. Every strict case (Zeus-domain market,
  matched size > 0, unprovable domain) must still raise the count (fail-closed).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.execution.exchange_reconcile import (
    _FOREIGN_WALLET_GHOST_RESOLUTION,
    init_exchange_reconcile_schema,
    record_finding,
    run_reconcile_sweep,
)
from src.risk_allocator.governor import count_open_reconcile_findings

NOW = datetime(2026, 6, 9, 23, 0, tzinfo=timezone.utc)
ZEUS_MARKET = "0x" + "a1" * 32
FOREIGN_MARKET = "0x" + "b2" * 32


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
        "original_size": "10",
        "outcome": "Yes",
        "price": "0.30",
        "side": "BUY",
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
    # Zeus's domain is non-empty: the weather market is discovered.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap1', ?)", (ZEUS_MARKET,)
    )
    return conn


def _sweep(conn, open_orders):
    return run_reconcile_sweep(
        _SnapshotAdapter(open_orders), conn, context="periodic", observed_at=NOW
    )


# ---- RELATIONSHIP: foreign resting order never arms the governor ----------------------
def test_foreign_resting_order_does_not_raise_governor_count() -> None:
    conn = _conn()
    _sweep(conn, [_order("0xforeign1", FOREIGN_MARKET)])
    assert count_open_reconcile_findings(conn) == 0, (
        "an operator manual order on a market outside Zeus's domain must not arm "
        "the reconcile kill switch — the 2026-06-09 entry-freeze incident"
    )
    # Audit trail kept: the finding exists, resolved with the foreign resolution.
    row = conn.execute(
        "SELECT resolution, evidence_json FROM exchange_reconcile_findings WHERE subject_id='0xforeign1'"
    ).fetchone()
    assert row is not None
    assert row["resolution"] == _FOREIGN_WALLET_GHOST_RESOLUTION
    assert json.loads(row["evidence_json"])["classification"] == "foreign_wallet_order"


def test_zeus_domain_ghost_still_arms_governor() -> None:
    conn = _conn()
    _sweep(conn, [_order("0xghost1", ZEUS_MARKET)])
    assert count_open_reconcile_findings(conn) == 1, (
        "a ghost order on a Zeus-domain market is the original disease and must "
        "stay fail-closed"
    )


def test_foreign_order_with_matched_size_stays_strict() -> None:
    # Money moved on the shared wallet outside Zeus's domain: credential-compromise
    # tripwire — must arm.
    conn = _conn()
    _sweep(conn, [_order("0xfilled1", FOREIGN_MARKET, size_matched="5")])
    assert count_open_reconcile_findings(conn) == 1


def test_empty_snapshot_table_cannot_prove_foreign_stays_strict() -> None:
    conn = _conn()
    conn.execute("DELETE FROM executable_market_snapshots")
    _sweep(conn, [_order("0xunknown1", FOREIGN_MARKET)])
    assert count_open_reconcile_findings(conn) == 1, (
        "with no discovered-market surface, domain membership is unprovable — "
        "classification must fail closed to the strict ghost path"
    )


def test_commanded_market_counts_as_zeus_domain() -> None:
    conn = _conn()
    other = "0x" + "c3" * 32
    conn.execute(
        "INSERT INTO venue_commands VALUES ('cmd1', ?, 'tok', 'FILLED', '0xold', ?)",
        (other, NOW.isoformat()),
    )
    _sweep(conn, [_order("0xghost2", other)])
    assert count_open_reconcile_findings(conn) == 1


# ---- MIGRATION: pre-classification unresolved foreign findings get resolved -----------
def test_existing_unresolved_foreign_findings_resolved_by_sweep() -> None:
    conn = _conn()
    # Simulate the 2026-06-09 incident rows: recorded before classification existed.
    record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id="0xincident1",
        context="ws_gap",
        evidence={
            "exchange_order": _order("0xincident1", FOREIGN_MARKET),
            "reason": "exchange_open_order_absent_from_venue_commands",
        },
        recorded_at=NOW,
    )
    assert count_open_reconcile_findings(conn) == 1
    # The order is still LIVE on the venue (would re-ghost without classification).
    _sweep(conn, [_order("0xincident1", FOREIGN_MARKET)])
    assert count_open_reconcile_findings(conn) == 0
    row = conn.execute(
        "SELECT resolution FROM exchange_reconcile_findings WHERE subject_id='0xincident1' ORDER BY recorded_at LIMIT 1"
    ).fetchone()
    assert row["resolution"] == _FOREIGN_WALLET_GHOST_RESOLUTION


def test_refresh_path_resolves_foreign_findings_without_venue_reads() -> None:
    # The 1-minute runtime refresh (refresh_unresolved_reconcile_findings) must clear
    # foreign findings from local evidence alone — the live kill switch should not wait
    # for the next full ws-gap sweep.
    from src.execution.exchange_reconcile import refresh_unresolved_reconcile_findings

    conn = _conn()
    record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id="0xincident2",
        context="ws_gap",
        evidence={
            "exchange_order": _order("0xincident2", FOREIGN_MARKET),
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


def test_repeated_sweeps_do_not_churn_duplicate_audit_rows() -> None:
    conn = _conn()
    for _ in range(3):
        _sweep(conn, [_order("0xforeign1", FOREIGN_MARKET)])
    n = conn.execute(
        "SELECT COUNT(*) FROM exchange_reconcile_findings WHERE subject_id='0xforeign1'"
    ).fetchone()[0]
    assert n == 1, "one audit row per foreign order, not one per sweep"
