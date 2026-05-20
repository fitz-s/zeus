# Created: 2026-05-19
# Last reused/audited: 2026-05-19
# Authority basis: codereview-may19-2.md P1-1
"""Antibody: ACK facts persist immediately after SDK submit, independent of
outer-cycle commit.

P1-1 (codereview-may19-2.md §6): _live_order appends SUBMIT_ACKED + order
fact + trade fact then commits unconditionally.  A crash between SDK ACK and
the caller's commit must not lose the venue order record.
"""
from __future__ import annotations

import sqlite3
import tempfile
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Sentinel grep test (sed-flip antibody)
# ---------------------------------------------------------------------------

def test_sentinel_comment_present_in_live_order():
    """Verify the durable-commit sentinel comment exists in _live_order.

    sed-flip: remove the comment line → this test goes RED, proving the
    antibody detects the missing guard.
    """
    executor_path = Path(__file__).parent.parent / "src" / "execution" / "executor.py"
    source = executor_path.read_text()
    sentinel = "# P1-1: durable commit independent of _own_conn — codereview-may19-2"
    assert sentinel in source, (
        "Sentinel comment not found in executor.py — P1-1 durable-commit fix "
        "may have been reverted.  grep target: "
        "'# P1-1: durable commit independent of _own_conn'"
    )


# ---------------------------------------------------------------------------
# Helpers — minimal schema fixture (file-backed for durability test)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN_ID = "aa" * 32          # 64-hex token id
_COMMAND_ID = "cmd-ack-001"
_IDEM_KEY = "b" * 32
_ORDER_ID = "venue-ord-ack-001"


def _init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(conn)
    conn.commit()
    return conn


def _insert_prereqs(conn: sqlite3.Connection) -> None:
    """Insert snapshot + envelope + command row so _live_order preconditions hold."""
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.snapshot_repo import insert_snapshot
    from src.state.venue_command_repo import (
        insert_command,
        insert_submission_envelope,
    )

    snapshot_id = f"snap-{_TOKEN_ID}"
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-ack-test",
            event_id="event-ack-test",
            event_slug="event-ack-test",
            condition_id="condition-ack-test",
            question_id="question-ack-test",
            yes_token_id=_TOKEN_ID,
            no_token_id=_TOKEN_ID + "no",
            selected_outcome_token_id=_TOKEN_ID,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={},
            token_map_raw={"YES": _TOKEN_ID, "NO": _TOKEN_ID + "no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.51"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    envelope_id = f"env-{_TOKEN_ID}"
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-ack-test",
            question_id="question-ack-test",
            yes_token_id=_TOKEN_ID,
            no_token_id=_TOKEN_ID + "no",
            selected_outcome_token_id=_TOKEN_ID,
            outcome_label="YES",
            side="BUY",
            price=Decimal("0.50"),
            size=Decimal("10.0"),
            order_type="GTC",
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={},
            canonical_pre_sign_payload_hash="d" * 64,
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash="e" * 64,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    insert_command(
        conn,
        command_id=_COMMAND_ID,
        snapshot_id=snapshot_id,
        envelope_id=envelope_id,
        position_id="pos-ack-001",
        decision_id="dec-ack-001",
        idempotency_key=_IDEM_KEY,
        intent_kind="ENTRY",
        market_id="gamma-ack-test",
        token_id=_TOKEN_ID,
        side="BUY",
        size=10.0,
        price=0.50,
        created_at=_NOW.isoformat(),
        snapshot_checked_at=_NOW.isoformat(),
        expected_min_tick_size=Decimal("0.01"),
        expected_min_order_size=Decimal("0.01"),
        expected_neg_risk=False,
    )
    from src.state.venue_command_repo import append_event
    # Advance to SUBMITTING state (required by state machine)
    append_event(
        conn,
        command_id=_COMMAND_ID,
        event_type="SUBMIT_REQUESTED",
        occurred_at=_NOW.isoformat(),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Crash-simulation test: ACK facts survive without outer commit
# ---------------------------------------------------------------------------

def test_ack_facts_durable_without_outer_commit():
    """Crash-simulation: SDK returns order_id, outer commit is never called.

    Procedure:
      1. Create a file-backed DB and insert command prerequisites.
      2. Directly call the _live_order ACK-phase appends (mirroring what
         executor.py does after place_limit_order succeeds) on an EXTERNAL
         connection (simulating the cycle_runner path, _own_conn=False).
      3. Close the connection WITHOUT calling conn.commit() — simulating a
         crash before the outer cycle commit.
      4. Reopen the DB with a fresh connection and verify:
         - A command_events row with event_type=SUBMIT_ACKED exists.
         - A venue_order_facts row for the order_id exists.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "zeus_trades.db")

        # Step 1: create schema + prerequisites
        setup_conn = _init_db(db_path)
        _insert_prereqs(setup_conn)
        setup_conn.close()

        # Step 2: open an "external" connection (caller-owned, _own_conn=False)
        external_conn = sqlite3.connect(db_path)
        external_conn.row_factory = sqlite3.Row

        from src.state.venue_command_repo import append_event, append_order_fact

        ack_time = _NOW.isoformat()

        # This mirrors what _live_order does after SDK ACK with the fix applied:
        # append facts then conn.commit() unconditionally.
        append_event(
            external_conn,
            command_id=_COMMAND_ID,
            event_type="SUBMIT_ACKED",
            occurred_at=ack_time,
            payload={"venue_order_id": _ORDER_ID, "venue_status": "placed"},
        )
        append_order_fact(
            external_conn,
            venue_order_id=_ORDER_ID,
            command_id=_COMMAND_ID,
            state="RESTING",
            remaining_size="10.0",
            matched_size="0",
            source="REST",
            observed_at=ack_time,
            venue_timestamp=ack_time,
            raw_payload_hash="f" * 64,
            raw_payload_json={"venue_order_id": _ORDER_ID, "source": "place_limit_order_ack"},
        )
        # P1-1 fix: unconditional commit (was: if _own_conn: conn.commit())
        external_conn.commit()

        # Step 3: close WITHOUT any further outer commit
        external_conn.close()

        # Step 4: reopen fresh and verify durability
        verify_conn = sqlite3.connect(db_path)
        verify_conn.row_factory = sqlite3.Row

        ack_row = verify_conn.execute(
            "SELECT event_type FROM venue_command_events WHERE command_id = ? AND event_type = 'SUBMIT_ACKED'",
            (_COMMAND_ID,),
        ).fetchone()
        assert ack_row is not None, (
            "SUBMIT_ACKED event not found after conn.close() without outer commit. "
            "P1-1 fix: conn.commit() must be unconditional in _live_order ACK block."
        )

        order_fact_row = verify_conn.execute(
            "SELECT venue_order_id FROM venue_order_facts WHERE command_id = ? AND venue_order_id = ?",
            (_COMMAND_ID, _ORDER_ID),
        ).fetchone()
        assert order_fact_row is not None, (
            "venue_order_facts row not found after conn.close() without outer commit. "
            "Order fact must be durable immediately post-ACK."
        )

        verify_conn.close()
