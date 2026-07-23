# Created: 2026-06-20
# Last reused/audited: 2026-06-20
# Authority basis: B6 live-submit observability proof (2026-06-20).
#   Antibody: _check_armed_live_no_submit_receipts feeds consistency_issues
#   → infrastructure_level="RED" when armed-live + zero submit receipts.
#   Reverting the Part-B production edit must flip the NEGATIVE test to FAIL.
"""B6 antibody: infrastructure_level goes RED when armed-live and no submit receipts.

NEGATIVE test: allow_submit=True, final_intents_built>=1, zero submit receipts
  → infrastructure_level == "RED" and "armed_live_no_recent_submit_receipts"
    in infrastructure_issues.

POSITIVE companion: identical but with one SUBMIT_REQUESTED receipt in-window
  → infrastructure_level stays GREEN (signal is specific to dead-submit, not
    a blanket alarm).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Relative to real wall-clock so the in-window/out-of-window logic is deterministic
# regardless of the calendar date the suite runs on. The function under test compares
# against `datetime.now(timezone.utc)`, so a hardcoded date would go stale (the "recent"
# receipt falls outside the 30-min window once real-now advances past it).
_NOW = datetime.now(timezone.utc)
_RECENT_TS = (_NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
_OLD_TS = (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_in_memory_db_with_submit_events(
    event_types: list[str],
    occurred_at: str,
) -> sqlite3.Connection:
    """Return an in-memory DB with schema + optional submit events seeded."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from src.state.db import init_schema, init_schema_trade_only
    init_schema(conn)
    init_schema_trade_only(conn)

    for i, event_type in enumerate(event_types):
        # Insert a minimal command row to satisfy FK, then the event.
        cmd_id = f"test-cmd-{i:03d}"
        _seed_minimal_command(conn, command_id=cmd_id)
        from src.state.venue_command_repo import append_event
        append_event(
            conn,
            command_id=cmd_id,
            event_type=event_type,
            occurred_at=occurred_at,
        )
    conn.commit()
    return conn


def _seed_minimal_command(conn: sqlite3.Connection, command_id: str) -> None:
    """Insert the minimal rows required for venue_command_events FK."""
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.snapshot_repo import get_snapshot, insert_snapshot
    from src.state.venue_command_repo import insert_command, insert_submission_envelope
    from decimal import Decimal

    token_id = "ee" * 32
    snapshot_id = f"snap-obs-{command_id}"
    if get_snapshot(conn, snapshot_id) is None:
        insert_snapshot(
            conn,
            ExecutableMarketSnapshot(
                snapshot_id=snapshot_id,
                gamma_market_id="gamma-obs",
                event_id="event-obs",
                event_slug="event-obs",
                condition_id="condition-obs",
                question_id="question-obs",
                yes_token_id=token_id,
                no_token_id=f"{token_id}-no",
                selected_outcome_token_id=token_id,
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
                token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
                rfqe=None,
                neg_risk=False,
                orderbook_top_bid=Decimal("0.44"),
                orderbook_top_ask=Decimal("0.46"),
                orderbook_depth_jsonb="{}",
                raw_gamma_payload_hash="a" * 64,
                raw_clob_market_info_hash="b" * 64,
                raw_orderbook_hash="c" * 64,
                authority_tier="CLOB",
                captured_at=_NOW,
                freshness_deadline=_NOW + timedelta(days=365),
            ),
        )
    import hashlib
    idem = hashlib.md5(command_id.encode()).hexdigest()
    env_id = f"env-obs-{command_id}"

    if not conn.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?", (env_id,)
    ).fetchone():
        insert_submission_envelope(
            conn,
            VenueSubmissionEnvelope(
                sdk_package="py-clob-client-v2",
                sdk_version="test",
                host="https://clob-v2.polymarket.com",
                chain_id=137,
                funder_address="0xfunder",
                condition_id="condition-obs",
                question_id="question-obs",
                yes_token_id=token_id,
                no_token_id=f"{token_id}-no",
                selected_outcome_token_id=token_id,
                outcome_label="YES",
                side="BUY",
                price=Decimal("0.45"),
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
            envelope_id=env_id,
        )
    if not conn.execute(
        "SELECT 1 FROM venue_commands WHERE command_id = ?", (command_id,)
    ).fetchone():
        insert_command(
            conn,
            command_id=command_id,
            snapshot_id=snapshot_id,
            envelope_id=env_id,
            position_id=f"pos-obs-{command_id}",
            decision_id=f"dec-obs-{command_id}",
            idempotency_key=idem,
            intent_kind="ENTRY",
            market_id="gamma-obs",
            token_id=token_id,
            side="BUY",
            size=10.0,
            price=0.45,
            created_at=_NOW.isoformat(),
            snapshot_checked_at=_NOW.isoformat(),
            expected_min_tick_size=Decimal("0.01"),
            expected_min_order_size=Decimal("0.01"),
            expected_neg_risk=False,
        )
    conn.commit()


class _NoCloseConn:
    """Thin wrapper: delegates everything to a real sqlite3.Connection but
    swallows close() so the test conn stays alive after _check_... returns."""

    def __init__(self, real_conn: sqlite3.Connection) -> None:
        self._c = real_conn

    def __getattr__(self, name: str):  # noqa: ANN001
        return getattr(self._c, name)

    def close(self) -> None:  # noqa: D401
        pass  # intentionally swallowed


def _call_check(
    *,
    global_allow_submit: bool,
    final_intents_built: int,
    conn: sqlite3.Connection,
    window_seconds: int = 1800,
) -> bool:
    """Call _check_armed_live_no_submit_receipts with controlled inputs."""
    from src.observability.status_summary import _check_armed_live_no_submit_receipts

    # Build the status dict the way _refresh_pulse_infrastructure_status sees it.
    status: dict = {
        "execution_capability": {
            "entry": {
                "global_allow_submit": global_allow_submit,
            }
        }
    }
    cycle: dict = {"final_intents_built": final_intents_built}

    no_close = _NoCloseConn(conn)

    # Patch get_trade_connection_with_world to return our no-close wrapper.
    with patch(
        "src.observability.status_summary.get_trade_connection_with_world",
        return_value=no_close,
    ):
        result = _check_armed_live_no_submit_receipts(
            status=status,
            cycle=cycle,
            window_seconds=window_seconds,
        )
    return result


# ---------------------------------------------------------------------------
# NEGATIVE: armed-live + zero submit receipts → RED
# ---------------------------------------------------------------------------

def test_infrastructure_red_when_armed_live_and_no_submit_receipts():
    """NEGATIVE: armed-live with no in-window submit receipt → RED.

    Antibody property: reverting the B6 Part-B edit to status_summary.py
    removes the consistency_issues.append call, making this test fail.
    """
    # No submit events seeded.
    db = _make_in_memory_db_with_submit_events([], occurred_at=_RECENT_TS)

    armed = _call_check(
        global_allow_submit=True,
        final_intents_built=1,
        conn=db,
    )
    assert armed is True, (
        "_check_armed_live_no_submit_receipts must return True "
        "when armed-live and zero recent submit receipts"
    )

    # Verify the full infrastructure_level path via _refresh_pulse_infrastructure_status.
    from src.observability.status_summary import _refresh_pulse_infrastructure_status

    status: dict = {
        "execution_capability": {
            "entry": {"global_allow_submit": True},
        },
        "execution": {},
    }
    cycle_summary: dict = {"final_intents_built": 1}

    no_close_db = _NoCloseConn(db)
    with patch(
        "src.observability.status_summary.get_trade_connection_with_world",
        return_value=no_close_db,
    ), patch(
        "src.observability.status_summary._get_risk_level", return_value="GREEN"
    ), patch(
        "src.observability.status_summary._get_risk_details", return_value={}
    ):
        _refresh_pulse_infrastructure_status(status, cycle_summary)

    risk = status.get("risk", {})
    assert risk.get("infrastructure_level") == "RED", (
        f"infrastructure_level must be RED when armed-live + no submit receipts, "
        f"got {risk.get('infrastructure_level')!r}"
    )
    assert "armed_live_no_recent_submit_receipts" in risk.get("infrastructure_issues", []), (
        f"'armed_live_no_recent_submit_receipts' must appear in infrastructure_issues, "
        f"got {risk.get('infrastructure_issues')}"
    )
    db.close()


# ---------------------------------------------------------------------------
# POSITIVE companion: submit receipt present → stays GREEN
# ---------------------------------------------------------------------------

def test_infrastructure_stays_green_when_recent_submit_receipt_present():
    """POSITIVE companion: one SUBMIT_REQUESTED in-window → GREEN (not a blanket alarm)."""
    db = _make_in_memory_db_with_submit_events(
        ["SUBMIT_REQUESTED"], occurred_at=_RECENT_TS
    )

    armed = _call_check(
        global_allow_submit=True,
        final_intents_built=1,
        conn=db,
    )
    assert armed is False, (
        "_check_armed_live_no_submit_receipts must return False "
        "when a recent SUBMIT_REQUESTED exists in the window"
    )

    from src.observability.status_summary import _refresh_pulse_infrastructure_status

    status: dict = {
        "execution_capability": {
            "entry": {"global_allow_submit": True},
        },
        "execution": {},
    }
    cycle_summary: dict = {"final_intents_built": 1}

    no_close_db = _NoCloseConn(db)
    with patch(
        "src.observability.status_summary.get_trade_connection_with_world",
        return_value=no_close_db,
    ), patch(
        "src.observability.status_summary._get_risk_level", return_value="GREEN"
    ), patch(
        "src.observability.status_summary._get_risk_details", return_value={}
    ):
        _refresh_pulse_infrastructure_status(status, cycle_summary)

    risk = status.get("risk", {})
    assert risk.get("infrastructure_level") == "GREEN", (
        f"infrastructure_level must stay GREEN when SUBMIT_REQUESTED is present, "
        f"got {risk.get('infrastructure_level')!r}"
    )
    assert "armed_live_no_recent_submit_receipts" not in risk.get("infrastructure_issues", []), (
        f"'armed_live_no_recent_submit_receipts' must NOT appear when submit receipt exists, "
        f"got {risk.get('infrastructure_issues')}"
    )
    db.close()


# ---------------------------------------------------------------------------
# Edge: not armed-live (allow_submit=False) → no alarm even with no receipts
# ---------------------------------------------------------------------------

def test_no_alarm_when_not_armed_live():
    """allow_submit=False → armed_live=False → no alarm regardless of receipts."""
    db = _make_in_memory_db_with_submit_events([], occurred_at=_RECENT_TS)

    armed = _call_check(
        global_allow_submit=False,
        final_intents_built=5,
        conn=db,
    )
    assert armed is False, (
        "_check_armed_live_no_submit_receipts must return False when allow_submit=False"
    )
    db.close()


# ---------------------------------------------------------------------------
# Edge: zero final_intents_built → not armed → no alarm
# ---------------------------------------------------------------------------

def test_no_alarm_when_no_final_intents_built():
    """final_intents_built=0 → armed_live=False → no alarm."""
    db = _make_in_memory_db_with_submit_events([], occurred_at=_RECENT_TS)

    armed = _call_check(
        global_allow_submit=True,
        final_intents_built=0,
        conn=db,
    )
    assert armed is False, (
        "_check_armed_live_no_submit_receipts must return False when no intents were built"
    )
    db.close()


# ---------------------------------------------------------------------------
# B6 FAIL-CLOSED: armed-live + receipt query UNREADABLE → RED (not false-green)
# ---------------------------------------------------------------------------

def test_red_when_armed_live_and_receipt_query_unreadable():
    """B6 antibody: if the receipt query RAISES under armed-live, the detector must
    FAIL CLOSED (return True → RED), not be silenced by its own query failure.

    RED-on-revert: reverting the except-branch in status_summary.py to `return False`
    flips ``armed`` to False and drops the RED issue → this test FAILS.
    """
    from src.observability.status_summary import (
        _check_armed_live_no_submit_receipts,
        _refresh_pulse_infrastructure_status,
    )

    status: dict = {
        "execution_capability": {"entry": {"global_allow_submit": True}},
        "execution": {},
    }
    cycle: dict = {"final_intents_built": 1}

    with patch(
        "src.observability.status_summary.get_trade_connection_with_world",
        side_effect=sqlite3.OperationalError("database is locked"),
    ):
        armed = _check_armed_live_no_submit_receipts(
            status=status, cycle=cycle, window_seconds=1800
        )
    assert armed is True, (
        "armed-live + unreadable receipt query must FAIL CLOSED (return True), "
        "not be silenced by the query failure"
    )

    with patch(
        "src.observability.status_summary.get_trade_connection_with_world",
        side_effect=sqlite3.OperationalError("database is locked"),
    ), patch(
        "src.observability.status_summary._get_risk_level", return_value="GREEN"
    ), patch(
        "src.observability.status_summary._get_risk_details", return_value={}
    ):
        _refresh_pulse_infrastructure_status(status, cycle)

    risk = status.get("risk", {})
    assert risk.get("infrastructure_level") == "RED", (
        f"unreadable receipt query under armed-live must drive RED, "
        f"got {risk.get('infrastructure_level')!r}"
    )
    assert "armed_live_no_recent_submit_receipts" in risk.get("infrastructure_issues", [])


def test_not_armed_live_with_unreadable_query_stays_clean():
    """Edge: not-armed-live short-circuits BEFORE the query, so even an unreadable
    receipt table stays clean (the fail-closed is scoped to armed-live)."""
    from src.observability.status_summary import _check_armed_live_no_submit_receipts

    status: dict = {
        "execution_capability": {"entry": {"global_allow_submit": False}},
    }
    cycle: dict = {"final_intents_built": 5}
    with patch(
        "src.observability.status_summary.get_trade_connection_with_world",
        side_effect=sqlite3.OperationalError("database is locked"),
    ):
        armed = _check_armed_live_no_submit_receipts(
            status=status, cycle=cycle, window_seconds=1800
        )
    assert armed is False, (
        "not-armed-live must short-circuit before the query → stays clean even if "
        "the receipt table is unreadable"
    )
