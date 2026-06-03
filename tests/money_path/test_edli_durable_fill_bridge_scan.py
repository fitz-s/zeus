# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: MF-1 / DEFECT-1 — durable self-healing EDLI fill -> position_current
#   bridge. Verified defect: the position bridge in src/main.py was driven SOLELY
#   by the transient in-memory set ``_edli_fill_bridge_aggregate_ids`` (populated
#   only from inbox rows that went PENDING->PROCESSED THIS cycle). A daemon death
#   OR a swallowed bridge exception between the world-conn commit (inbox marked
#   PROCESSED) and the separate bridge commit leaves a FILL_CONFIRMED aggregate
#   with NO position_current row; on restart the in-memory set is empty, so the
#   fill is orphaned forever (stuck capital, invisible to chain-reconcile / exit /
#   harvester / redeem). The fix is a DURABLE IDEMPOTENT SCAN that finds every
#   aggregate with a UserTradeObserved FILL_CONFIRMED whose
#   ``edli_bridge_position_id`` has no position_current row, and bridges each —
#   run EACH CYCLE and AT BOOT, independent of the transient set.
"""Relationship test: the durable fill -> position_current bridge scan.

Cross-module invariant under test (NOT a single-function unit test):

    For every aggregate in ``edli_live_order_events`` that carries a
    ``UserTradeObserved`` event with ``fill_authority_state == "FILL_CONFIRMED"``,
    a canonical ``position_current`` row keyed by ``edli_bridge_position_id``
    MUST exist after the bridge step runs — EVEN WHEN the transient in-memory
    set that previously gated the bridge is EMPTY (the post-restart /
    post-swallowed-exception state).

The seed inserts the exact ``edli_live_order_events`` rows the production
aggregate persists (PreSubmitRevalidated identity + UserTradeObserved fill
economics) and asserts the durable scan materialises the position with the in-
memory set never populated. This reproduces the ORPHAN WINDOW: the events are
durable on disk, but no in-cycle trigger fires for them.

Idempotency is asserted directly: running the scan twice yields exactly one
position_current row and no duplicate ENTRY position_events.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Seed helpers — write the exact rows the production EDLI aggregate persists.
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    """A single connection carrying both the trade-owned tables
    (position_current / position_events) and the EDLI events table.

    In production the bridge runs on a trade connection with ``world`` ATTACHed;
    the bridge's ``_edli_events_table`` resolves to ``world.edli_live_order_events``
    when ``world`` is attached and to the unqualified table on a single
    connection. A single ``init_schema`` connection that also owns
    ``edli_live_order_events`` exercises the identical bridge read/write path
    (same SQL, same canonical write helpers).
    """
    from src.state.db import init_schema
    from src.state.schema.edli_live_order_events_schema import ensure_tables as edli_ensure

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    edli_ensure(conn)
    return conn


def _insert_event(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    sequence: int,
    event_type: str,
    payload: dict,
    source_authority: str,
) -> None:
    payload_json = json.dumps(payload, sort_keys=True)
    event_hash = hashlib.sha256(
        f"{aggregate_id}|{sequence}|{event_type}|{payload_json}".encode("utf-8")
    ).hexdigest()
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            f"edli_live_order_event:{event_hash[:32]}",
            aggregate_id,
            sequence,
            event_type,
            None,
            event_hash,
            payload_json,
            payload_hash,
            source_authority,
            "2026-06-01T00:00:00+00:00",
            "2026-06-01T00:00:00+00:00",
        ),
    )


def _seed_confirmed_fill_aggregate(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    direction: str = "buy_no",
    token_id: str = "0xNOTOKEN",
    filled_size: float = 120.0,
    avg_fill_price: float = 0.42,
) -> None:
    """Persist a FILL_CONFIRMED aggregate WITHOUT a position_current row.

    This is exactly the durable state after a daemon death / swallowed bridge
    exception: the EDLI events are committed on disk, but the position was never
    materialised, and (post-restart) no in-memory trigger remembers it.
    """
    _insert_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="PreSubmitRevalidated",
        payload={
            "event_id": aggregate_id.split(":")[0],
            "final_intent_id": aggregate_id.split(":")[-1],
            "condition_id": "0xCONDITION",
            "token_id": token_id,
            "direction": direction,
            "city": "Tokyo",
            "target_date": "2026-06-02",
            "bin_label": "high_29_30",
            "metric": "high",
            "q_live": 0.62,
            "executable_snapshot_id": "snap-MF1",
            "market_id": "mkt-MF1",
        },
        source_authority="decision_kernel",
    )
    _insert_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="UserTradeObserved",
        payload={
            "event_id": aggregate_id.split(":")[0],
            "final_intent_id": aggregate_id.split(":")[-1],
            "fill_authority_state": "FILL_CONFIRMED",
            "trade_status": "CONFIRMED",
            "filled_size": filled_size,
            "avg_fill_price": avg_fill_price,
            "fees": 0.13,
            "venue_order_id": "vo-MF1",
        },
        source_authority="user_channel",
    )
    conn.commit()


# ---------------------------------------------------------------------------
# RED — the orphan reproduction.
# ---------------------------------------------------------------------------


class TestDurableFillBridgeScan:
    def test_orphaned_confirmed_fill_is_bridged_with_empty_in_memory_set(self):
        """ORPHAN WINDOW: a FILL_CONFIRMED aggregate with NO position_current row
        and NO in-memory trigger MUST still be materialised by the durable scan.

        Pre-fix this fails: the only bridge trigger was the transient
        ``_edli_fill_bridge_aggregate_ids`` set, never populated on restart for a
        fill that already passed the inbox PROCESSED commit. There is no durable
        scan, so position_current is never created -> capital orphaned.
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.main import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1:fiMF1"
        _seed_confirmed_fill_aggregate(conn, aggregate_id=aggregate_id)

        position_id = edli_bridge_position_id(aggregate_id)

        # Precondition: the orphan exists (events durable, position absent).
        before = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()[0]
        assert before == 0, "precondition: orphan must start with no position_current row"

        # The durable scan is the authoritative bridge trigger. NO in-memory set
        # is passed — this models the post-restart / post-exception state.
        bridged = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        conn.commit()

        # THE ORPHAN MUST BE HEALED.
        after = conn.execute(
            "SELECT position_id, phase, shares, cost_basis_usd, direction, "
            "no_token_id, fill_authority FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        assert after is not None, (
            "ORPHAN: durable scan did not materialise position_current for a "
            "FILL_CONFIRMED aggregate (capital stuck, invisible to exit/redeem)"
        )
        assert after["phase"] == "active"
        assert after["shares"] == pytest.approx(120.0)
        assert after["cost_basis_usd"] == pytest.approx(120.0 * 0.42)
        assert after["direction"] == "buy_no"
        # buy_no token must land on no_token_id so chain reconciliation matches.
        assert after["no_token_id"] == "0xNOTOKEN"
        assert after["fill_authority"] == "venue_confirmed_full"
        assert bridged >= 1, "scan must report the number of orphans it bridged"

    def test_durable_scan_is_idempotent(self):
        """Running the scan twice yields exactly ONE position_current row and NO
        duplicate ENTRY position_events (the bridge is provably idempotent via
        ON CONFLICT(position_id) + append-only UNIQUE(position_id, sequence_no)).
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.main import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1b:fiMF1b"
        _seed_confirmed_fill_aggregate(conn, aggregate_id=aggregate_id)
        position_id = edli_bridge_position_id(aggregate_id)

        first = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        conn.commit()
        second = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc))
        conn.commit()

        # First pass bridges the orphan; second pass finds nothing to bridge
        # (the position_current row already exists) -> the scan is self-quieting.
        assert first == 1
        assert second == 0

        pc_count = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()[0]
        assert pc_count == 1, "idempotency: exactly one position_current row"

        entry_events = conn.execute(
            """
            SELECT COUNT(*) FROM position_events
            WHERE position_id = ? AND event_type = 'POSITION_OPEN_INTENT'
            """,
            (position_id,),
        ).fetchone()[0]
        assert entry_events == 1, "idempotency: no duplicate ENTRY position_events"

    def test_non_confirmed_fill_is_not_bridged(self):
        """A MATCHED-only aggregate (no FILL_CONFIRMED) must NOT be bridged — the
        scan keys strictly on FILL_CONFIRMED so pending fills do not leak into
        position_current.
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.main import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1c:fiMF1c"
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="PreSubmitRevalidated",
            payload={
                "event_id": "evtMF1c",
                "final_intent_id": "fiMF1c",
                "condition_id": "0xCONDITION",
                "token_id": "0xYESTOKEN",
                "direction": "buy_yes",
                "city": "Paris",
                "target_date": "2026-06-02",
                "bin_label": "high_20_21",
                "metric": "high",
                "q_live": 0.5,
                "executable_snapshot_id": "snap-c",
                "market_id": "mkt-c",
            },
            source_authority="decision_kernel",
        )
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="UserTradeObserved",
            payload={
                "event_id": "evtMF1c",
                "final_intent_id": "fiMF1c",
                "fill_authority_state": "MATCHED_PENDING_FINALITY",
                "trade_status": "MATCHED",
                "filled_size": 50.0,
                "avg_fill_price": 0.30,
                "venue_order_id": "vo-c",
            },
            source_authority="user_channel",
        )
        conn.commit()

        bridged = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        conn.commit()

        position_id = edli_bridge_position_id(aggregate_id)
        row = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()[0]
        assert row == 0, "MATCHED-only fill must NOT be bridged"
        assert bridged == 0
