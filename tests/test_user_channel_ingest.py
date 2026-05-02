# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: R3 M3 Polymarket user-channel WS ingest and fail-closed gap guard antibodies.
# Reuse: Run when user WebSocket ingest, U2 venue facts, or submit gap guards change.
# Last reused/audited: 2026-04-30
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M3.yaml
"""M3: user-channel WS messages become U2 facts; gaps block new submit."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
from src.control import ws_gap_guard
from src.ingest.polymarket_user_channel import PolymarketUserChannelIngestor, WSAuth
from src.state.db import init_schema
from src.state.snapshot_repo import insert_snapshot
from src.state.venue_command_repo import (
    append_event,
    insert_command,
    insert_submission_envelope,
    load_calibration_trade_facts,
)

NOW = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    ws_gap_guard.clear_for_test(observed_at=NOW)
    _seed_acknowledged_command(c)
    yield c
    c.close()
    ws_gap_guard.clear_for_test(observed_at=NOW)


def _snapshot(snapshot_id: str = "snap-ws") -> ExecutableMarketSnapshotV2:
    return ExecutableMarketSnapshotV2(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-ws",
        event_id="event-ws",
        event_slug="weather-ws-high",
        condition_id="condition-ws",
        question_id="question-ws",
        yes_token_id="yes-token-ws",
        no_token_id="no-token-ws",
        selected_outcome_token_id="yes-token-ws",
        outcome_label="YES",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=NOW + timedelta(hours=1),
        market_end_at=NOW + timedelta(days=1),
        market_close_at=NOW + timedelta(days=1, hours=1),
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        fee_details={"bps": 0, "source": "test"},
        token_map_raw={"YES": "yes-token-ws", "NO": "no-token-ws"},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.49"),
        orderbook_top_ask=Decimal("0.51"),
        orderbook_depth_jsonb='{"asks":[["0.51","100"]],"bids":[["0.49","100"]]}',
        raw_gamma_payload_hash=HASH_A,
        raw_clob_market_info_hash=HASH_B,
        raw_orderbook_hash=HASH_C,
        authority_tier="CLOB",
        captured_at=NOW,
        freshness_deadline=NOW + timedelta(seconds=30),
    )


def _envelope(
    *,
    side: str = "BUY",
    price: Decimal = Decimal("0.50"),
    size: Decimal = Decimal("10"),
) -> VenueSubmissionEnvelope:
    return VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="1.0.0",
        host="https://clob-v2.polymarket.com",
        chain_id=137,
        funder_address="0xfunder",
        condition_id="condition-ws",
        question_id="question-ws",
        yes_token_id="yes-token-ws",
        no_token_id="no-token-ws",
        selected_outcome_token_id="yes-token-ws",
        outcome_label="YES",
        side=side,
        price=price,
        size=size,
        order_type="GTC",
        post_only=False,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        fee_details={"bps": 0},
        canonical_pre_sign_payload_hash=HASH_D,
        signed_order=b"fake-signed-order",
        signed_order_hash=HASH_E,
        raw_request_hash=HASH_A,
        raw_response_json=json.dumps({"orderID": "ord-ws", "status": "live"}, sort_keys=True),
        order_id="ord-ws",
        trade_ids=("trade-ws",),
        transaction_hashes=("0xtx",),
        error_code=None,
        error_message=None,
        captured_at=NOW.isoformat(),
    )


def _seed_acknowledged_command(c) -> None:
    insert_snapshot(c, _snapshot())
    insert_submission_envelope(c, _envelope(), envelope_id="env-ws")
    insert_command(
        c,
        command_id="cmd-ws",
        snapshot_id="snap-ws",
        envelope_id="env-ws",
        position_id="1",
        decision_id="dec-ws",
        idempotency_key="idem-cmd-ws",
        intent_kind="ENTRY",
        market_id="condition-ws",
        token_id="yes-token-ws",
        side="BUY",
        size=10.0,
        price=0.50,
        created_at=NOW.isoformat(),
        snapshot_checked_at=NOW,
        expected_min_tick_size=Decimal("0.01"),
        expected_min_order_size=Decimal("5"),
        expected_neg_risk=False,
        venue_order_id="ord-ws",
    )
    append_event(c, command_id="cmd-ws", event_type="SUBMIT_REQUESTED", occurred_at=NOW.isoformat())
    append_event(c, command_id="cmd-ws", event_type="SUBMIT_ACKED", occurred_at=NOW.isoformat())
    c.commit()


def _ingestor(c, gaps: list | None = None) -> PolymarketUserChannelIngestor:
    return PolymarketUserChannelIngestor(
        adapter=object(),
        condition_ids=["condition-ws"],
        auth=WSAuth("key", "secret", "pass"),
        conn_factory=lambda: c,
        own_connection=False,
        on_gap=(gaps.append if gaps is not None else None),
    )


def _order_message(**overrides):
    msg = {
        "event_type": "order",
        "type": "PLACEMENT",
        "id": "ord-ws",
        "market": "condition-ws",
        "size": "10",
        "size_matched": "0",
        "timestamp": NOW.isoformat(),
        "apiKey": "must-redact",
        "secret": "must-redact",
        "passphrase": "must-redact",
    }
    msg.update(overrides)
    return msg


def _trade_message(status: str = "MATCHED", **overrides):
    msg = {
        "event_type": "trade",
        "status": status,
        "id": "trade-ws",
        "taker_order_id": "ord-ws",
        "market": "condition-ws",
        "size": "5",
        "price": "0.50",
        "timestamp": NOW.isoformat(),
    }
    msg.update(overrides)
    return msg


def _rows(c, table: str) -> list[sqlite3.Row]:
    return c.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def _command_state(c) -> str:
    return c.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-ws'").fetchone()["state"]


def test_ws_message_parsed_to_order_fact(conn):
    result = _ingestor(conn).handle_message(_order_message())

    assert result and result["order_fact_id"]
    row = _rows(conn, "venue_order_facts")[-1]
    assert row["venue_order_id"] == "ord-ws"
    assert row["state"] == "LIVE"
    assert row["source"] == "WS_USER"
    raw = json.loads(row["raw_payload_json"])
    assert raw["apiKey"] == raw["secret"] == raw["passphrase"] == "***"


def test_ws_message_parsed_to_trade_fact(conn):
    result = _ingestor(conn).handle_message(_trade_message("MATCHED"))

    assert result and result["trade_fact_id"]
    row = _rows(conn, "venue_trade_facts")[-1]
    assert row["trade_id"] == "trade-ws"
    assert row["state"] == "MATCHED"
    assert row["source"] == "WS_USER"
    assert _command_state(conn) == "PARTIAL"


def test_matched_event_does_not_final_close_lot(conn):
    _ingestor(conn).handle_message(_trade_message("MATCHED"))

    states = [r["state"] for r in _rows(conn, "position_lots")]
    assert states == ["OPTIMISTIC_EXPOSURE"]
    assert load_calibration_trade_facts(conn) == []


def test_mined_event_is_optimistic_exposure_not_finality(conn):
    result = _ingestor(conn).handle_message(
        _trade_message("MINED", transaction_hash="0xmined", confirmation_count=0)
    )

    assert result["command_event"] == "PARTIAL_FILL_OBSERVED"
    assert [r["state"] for r in _rows(conn, "venue_trade_facts")] == ["MINED"]
    assert [r["state"] for r in _rows(conn, "position_lots")] == ["OPTIMISTIC_EXPOSURE"]
    assert load_calibration_trade_facts(conn) == []
    assert _command_state(conn) == "PARTIAL"


def test_matched_then_mined_does_not_duplicate_optimistic_exposure(conn):
    ingestor = _ingestor(conn)
    ingestor.handle_message(_trade_message("MATCHED"))
    ingestor.handle_message(_trade_message("MINED", transaction_hash="0xmined", confirmation_count=0))

    assert [r["state"] for r in _rows(conn, "venue_trade_facts")] == ["MATCHED", "MINED"]
    assert [r["state"] for r in _rows(conn, "position_lots")] == ["OPTIMISTIC_EXPOSURE"]
    assert load_calibration_trade_facts(conn) == []


def test_confirmed_event_finalizes_trade_and_permits_canonical_pnl(conn):
    ingestor = _ingestor(conn)
    ingestor.handle_message(_trade_message("MATCHED"))
    ingestor.handle_message(_trade_message("CONFIRMED", transaction_hash="0xconfirmed", confirmation_count=3))

    lot_states = [r["state"] for r in _rows(conn, "position_lots")]
    assert lot_states == ["OPTIMISTIC_EXPOSURE", "CONFIRMED_EXPOSURE"]
    confirmed = load_calibration_trade_facts(conn)
    assert [r["state"] for r in confirmed] == ["CONFIRMED"]
    assert _command_state(conn) == "FILLED"


def test_exit_sell_confirmed_trade_does_not_mint_positive_exposure_lot(conn):
    """EXIT/SELL WS trade facts confirm venue side effects but are not entries."""
    conn.execute(
        """
        UPDATE venue_commands
           SET intent_kind = 'EXIT', side = 'SELL'
         WHERE command_id = 'cmd-ws'
        """
    )
    conn.commit()

    result = _ingestor(conn).handle_message(
        _trade_message("CONFIRMED", transaction_hash="0xexit", confirmation_count=3)
    )

    assert result["command_event"] == "FILL_CONFIRMED"
    assert [r["state"] for r in _rows(conn, "venue_trade_facts")] == ["CONFIRMED"]
    assert _rows(conn, "position_lots") == []
    assert _command_state(conn) == "FILLED"


def test_failed_after_matched_quarantines_or_reverses_optimistic_projection(conn):
    ingestor = _ingestor(conn)
    ingestor.handle_message(_trade_message("MATCHED"))
    ingestor.handle_message(_trade_message("FAILED"))

    assert [r["state"] for r in _rows(conn, "position_lots")] == ["OPTIMISTIC_EXPOSURE", "QUARANTINED"]
    assert [r["state"] for r in _rows(conn, "venue_trade_facts")] == ["MATCHED", "FAILED"]


def test_failed_after_mined_quarantines_optimistic_projection(conn):
    ingestor = _ingestor(conn)
    ingestor.handle_message(_trade_message("MINED", transaction_hash="0xmined", confirmation_count=0))
    ingestor.handle_message(_trade_message("FAILED"))

    assert [r["state"] for r in _rows(conn, "position_lots")] == ["OPTIMISTIC_EXPOSURE", "QUARANTINED"]
    assert [r["state"] for r in _rows(conn, "venue_trade_facts")] == ["MINED", "FAILED"]


def test_websocket_disconnect_triggers_reconcile_sweep_marker_and_blocks_submit(conn):
    gaps = []
    status = _ingestor(conn, gaps).mark_disconnect()

    assert gaps and gaps[-1] == status
    assert status.m5_reconcile_required is True
    assert ws_gap_guard.summary()["entry"]["allow_submit"] is False
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")


def test_first_subscribed_message_after_not_configured_clears_m5_reconcile_required(conn):
    """RELATIONSHIP test (live-blockers 2026-05-01): boot transition must unlatch.

    Function-level dry tests passed for record_message preserving
    m5_reconcile_required (correct in isolation), AND for the module-init
    value being True (correct in isolation). But no test asserted the
    cross-module invariant: 'after a clean boot + first SUBSCRIBED message,
    is the WS gate UNLATCHED enough for entries to flow?' Result was a
    daemon that booted healthy, connected fine, received messages — and
    still permanently blocked entries because m5_reconcile_required was
    never cleared in any production path.

    This is the relationship test that should have existed from day one.
    """
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=None,
            subscription_state="DISCONNECTED",
            gap_reason="not_configured",
            m5_reconcile_required=True,
            updated_at=NOW - timedelta(seconds=10),
        )
    )

    status = ws_gap_guard.record_message(
        observed_at=NOW,
        subscription_state="SUBSCRIBED",
    )

    assert status.m5_reconcile_required is False, (
        "first SUBSCRIBED message after a clean (not_configured) boot must "
        "clear m5_reconcile_required — there's no missed-orders risk because "
        "the daemon never had a connection to lose messages from"
    )
    assert status.connected is True
    assert status.subscription_state == "SUBSCRIBED"
    summary = ws_gap_guard.summary(now=NOW + timedelta(seconds=1))
    assert summary["entry"]["allow_submit"] is True, (
        "after clean-boot subscribe, ws_user_channel.entry.allow_submit must "
        "be True — otherwise reduce_only stays latched and orders never flow"
    )


def test_mid_run_reconnect_after_real_disconnect_preserves_m5_reconcile_required(conn):
    """Counter-test: genuine mid-run reconnect must NOT auto-clear.

    If WS dropped during runtime (ConnectionClosedError, auth_failed, etc.),
    we may have missed fills that landed in the gap window. The m5_reconcile_required
    flag is the marker that REST reconciliation must run before we trust the WS
    again. Only the not_configured boot path is safe to auto-clear.
    """
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=NOW - timedelta(minutes=2),
            subscription_state="DISCONNECTED",
            gap_reason="websocket_disconnect:ConnectionClosedError",
            m5_reconcile_required=True,
            updated_at=NOW - timedelta(seconds=10),
        )
    )

    status = ws_gap_guard.record_message(
        observed_at=NOW,
        subscription_state="SUBSCRIBED",
    )

    assert status.m5_reconcile_required is True, (
        "mid-run reconnect after a real disconnect must preserve "
        "m5_reconcile_required so REST reconciliation runs before entries resume"
    )


def test_subscribe_reconnect_with_empty_local_surface_clears_m5_requirement():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=NOW - timedelta(minutes=2),
            subscription_state="DISCONNECTED",
            gap_reason="websocket_disconnect:ConnectionClosedError",
            m5_reconcile_required=True,
            updated_at=NOW - timedelta(seconds=10),
        )
    )
    try:
        status = PolymarketUserChannelIngestor(
            adapter=object(),
            condition_ids=["condition-ws"],
            auth=WSAuth("key", "secret", "pass"),
            conn_factory=lambda: c,
            own_connection=False,
        )._record_subscribed_message(observed_at=NOW)

        assert status.m5_reconcile_required is False
        assert status.gap_reason == "message_received_no_local_side_effects"
        assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is True
    finally:
        c.close()
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_subscribe_reconnect_with_local_command_preserves_m5_requirement(conn):
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=NOW - timedelta(minutes=2),
            subscription_state="DISCONNECTED",
            gap_reason="websocket_disconnect:ConnectionClosedError",
            m5_reconcile_required=True,
            updated_at=NOW - timedelta(seconds=10),
        )
    )

    status = _ingestor(conn)._record_subscribed_message(observed_at=NOW)

    assert status.m5_reconcile_required is True
    assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is False


def test_not_configured_default_blocks_submit_until_user_channel_truth_exists(conn):
    """M3 is live-truth-gated; absent WS configuration is not an implicit PASS."""
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=None,
            subscription_state="DISCONNECTED",
            gap_reason="not_configured",
            m5_reconcile_required=True,
            updated_at=NOW - timedelta(hours=1),
        )
    )

    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked, match="not_configured"):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")
    assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is False


def test_explicit_test_clear_remains_allowed_for_unit_harness(conn):
    ws_gap_guard.clear_for_test(observed_at=NOW - timedelta(hours=1))

    ws_gap_guard.assert_ws_allows_submit("condition-ws")
    assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is True


def test_ws_guard_test_reset_helpers_are_rejected_outside_test_runtime(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(ws_gap_guard, "_test_runtime_enabled", lambda: False)

        with pytest.raises(RuntimeError, match="clear_for_test"):
            ws_gap_guard.clear_for_test(observed_at=NOW)
        with pytest.raises(RuntimeError, match="configure_status"):
            ws_gap_guard.configure_status(ws_gap_guard.WSGapStatus())


def test_stale_last_message_triggers_gap_event(conn):
    gaps = []
    ingestor = _ingestor(conn, gaps)
    ws_gap_guard.record_message(observed_at=NOW - timedelta(seconds=31), stale_after_seconds=30)

    status = ingestor.check_stale(now=NOW)

    assert status.gap_reason == "stale_last_message"
    assert status.m5_reconcile_required is True
    assert gaps[-1].gap_reason == "stale_last_message"


def test_stale_guard_path_sets_m5_reconcile_required_without_manual_check(conn):
    ws_gap_guard.record_message(observed_at=NOW - timedelta(seconds=31), stale_after_seconds=30)

    summary = ws_gap_guard.summary(now=NOW)

    assert summary["stale"] is True
    assert summary["m5_reconcile_required"] is True
    assert summary["entry"]["allow_submit"] is False
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked, match="m5_reconcile_required=True"):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")


def test_subscription_auth_failure_blocks_new_submit(conn):
    _ingestor(conn).handle_message({"error": "auth failed", "market": "condition-ws"})

    current = ws_gap_guard.status()
    assert current.subscription_state == "AUTH_FAILED"
    assert current.m5_reconcile_required is True
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")


def test_market_subscription_mismatch_blocks_all_new_submit_until_m5(conn):
    _ingestor(conn).handle_message(_trade_message("MATCHED", market="condition-other"))

    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-other")
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")
    assert ws_gap_guard.summary()["entry"]["allow_submit"] is False


def test_market_subscription_mismatch_stays_global_block_after_later_valid_message(conn):
    ingestor = _ingestor(conn)
    ingestor.handle_message(_trade_message("MATCHED", market="condition-other"))
    ingestor.handle_message(_order_message())

    assert ws_gap_guard.status().m5_reconcile_required is True
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-other")
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")
    assert ws_gap_guard.summary()["entry"]["allow_submit"] is False


def test_maker_order_trade_fact_uses_matched_zeus_order_id(conn):
    _ingestor(conn).handle_message(
        _trade_message(
            "MATCHED",
            taker_order_id="foreign-taker-order",
            maker_orders=[{"order_id": "ord-ws"}],
        )
    )

    row = _rows(conn, "venue_trade_facts")[-1]
    assert row["venue_order_id"] == "ord-ws"
    assert row["command_id"] == "cmd-ws"


def test_ws_path_emits_equivalent_command_events_when_enabled(conn):
    ingestor = _ingestor(conn)
    ingestor.handle_message(_trade_message("MATCHED"))
    ingestor.handle_message(_trade_message("CONFIRMED"))

    events = [
        r["event_type"]
        for r in conn.execute(
            "SELECT event_type FROM venue_command_events WHERE command_id = 'cmd-ws' ORDER BY sequence_no"
        )
    ]
    assert events == ["INTENT_CREATED", "SUBMIT_REQUESTED", "SUBMIT_ACKED", "PARTIAL_FILL_OBSERVED", "FILL_CONFIRMED"]
    assert _command_state(conn) == "FILLED"


def test_executor_runtime_position_id_falls_back_to_numeric_decision_id_for_lots(conn):
    conn.execute(
        """
        UPDATE venue_commands
           SET position_id = ?, decision_id = ?
         WHERE command_id = 'cmd-ws'
        """,
        ("runtime-trade-id", "42"),
    )

    _ingestor(conn).handle_message(_trade_message("CONFIRMED"))

    rows = _rows(conn, "position_lots")
    assert [row["position_id"] for row in rows] == [42]
    assert rows[0]["state"] == "CONFIRMED_EXPOSURE"


def test_resubscribe_recovery_records_messages_but_does_not_clear_m5_sweep_requirement(conn):
    ingestor = _ingestor(conn)
    ingestor.mark_disconnect()
    ingestor.handle_message(_trade_message("CONFIRMED"))

    status = ws_gap_guard.status()
    assert status.connected is True
    assert status.m5_reconcile_required is True
    with pytest.raises(ws_gap_guard.WSGapSubmitBlocked):
        ws_gap_guard.assert_ws_allows_submit("condition-ws")
    assert [r["state"] for r in _rows(conn, "venue_trade_facts")] == ["CONFIRMED"]
