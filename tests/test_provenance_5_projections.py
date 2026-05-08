# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-05-07; last_reused=2026-05-07
# Purpose: U2 antibodies for 5 raw provenance projections and CONFIRMED-only training.
# Reuse: Run when venue command, order fact, trade fact, position lot, settlement provenance, or calibration ingestion changes.
# Last reused/audited: 2026-05-08
# Authority basis: docs/operations/task_2026-05-08_object_invariance_wave27/PLAN.md
"""U2 raw provenance schema tests for five distinct projections."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts.executable_market_snapshot_v2 import (
    ExecutableMarketSnapshotV2,
    StaleMarketSnapshotError,
)
from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
from src.state.db import init_schema
from src.state.snapshot_repo import insert_snapshot
from src.state.venue_command_repo import (
    append_event,
    append_order_fact,
    append_position_lot,
    append_provenance_event,
    append_trade_fact,
    insert_command,
    insert_submission_envelope,
    load_calibration_trade_facts,
    rollback_optimistic_lot_for_failed_trade,
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
    yield c
    c.close()


def _snapshot(snapshot_id: str = "snap-u2") -> ExecutableMarketSnapshotV2:
    return ExecutableMarketSnapshotV2(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-u2",
        event_id="event-u2",
        event_slug="weather-nyc-high",
        condition_id="condition-u2",
        question_id="question-u2",
        yes_token_id="yes-token-u2",
        no_token_id="no-token-u2",
        selected_outcome_token_id="yes-token-u2",
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
        token_map_raw={"YES": "yes-token-u2", "NO": "no-token-u2"},
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
        condition_id="condition-u2",
        question_id="question-u2",
        yes_token_id="yes-token-u2",
        no_token_id="no-token-u2",
        selected_outcome_token_id="yes-token-u2",
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
        raw_response_json=json.dumps({"orderID": "ord-u2", "status": "live"}, sort_keys=True),
        order_id="ord-u2",
        trade_ids=("trade-u2",),
        transaction_hashes=("0xtx",),
        error_code=None,
        error_message=None,
        captured_at=NOW.isoformat(),
    )


def _seed_snapshot_envelope_command(conn) -> tuple[str, str, str]:
    snapshot_id = "snap-u2"
    envelope_id = "env-u2"
    command_id = "cmd-u2"
    insert_snapshot(conn, _snapshot(snapshot_id))
    insert_submission_envelope(conn, _envelope(), envelope_id=envelope_id)
    _insert_command(conn, command_id=command_id, snapshot_id=snapshot_id, envelope_id=envelope_id)
    return snapshot_id, envelope_id, command_id


def _insert_command(
    conn,
    *,
    command_id: str = "cmd-u2",
    snapshot_id: str | None = "snap-u2",
    envelope_id: str | None = "env-u2",
    token_id: str = "yes-token-u2",
    side: str = "BUY",
    size: float = 10.0,
    price: float = 0.50,
) -> None:
    insert_command(
        conn,
        command_id=command_id,
        envelope_id=envelope_id,
        snapshot_id=snapshot_id,
        position_id="pos-u2",
        decision_id="dec-u2",
        idempotency_key=f"idem-{command_id}",
        intent_kind="ENTRY",
        market_id="market-u2",
        token_id=token_id,
        side=side,
        size=size,
        price=price,
        created_at=NOW.isoformat(),
        snapshot_checked_at=NOW,
        expected_min_tick_size=Decimal("0.01"),
        expected_min_order_size=Decimal("5"),
        expected_neg_risk=False,
    )


def test_init_schema_creates_u2_projection_tables_and_command_envelope_column(conn):
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }

    assert "venue_submission_envelopes" in tables
    assert "venue_order_facts" in tables
    assert "venue_trade_facts" in tables
    assert "position_lots" in tables
    assert "provenance_envelope_events" in tables

    command_columns = {r["name"] for r in conn.execute("PRAGMA table_info(venue_commands)")}
    assert {"snapshot_id", "envelope_id"} <= command_columns


def test_command_insert_requires_envelope_and_snapshot(conn):
    insert_snapshot(conn, _snapshot("snap-u2"))
    insert_submission_envelope(conn, _envelope(), envelope_id="env-u2")

    with pytest.raises(StaleMarketSnapshotError, match="snapshot"):
        _insert_command(conn, command_id="cmd-no-snapshot", snapshot_id=None, envelope_id="env-u2")

    with pytest.raises(ValueError, match="envelope"):
        _insert_command(conn, command_id="cmd-no-envelope", snapshot_id="snap-u2", envelope_id=None)

    with pytest.raises(ValueError, match="envelope"):
        _insert_command(conn, command_id="cmd-missing-envelope", snapshot_id="snap-u2", envelope_id="missing-env")

    _insert_command(conn, command_id="cmd-ok", snapshot_id="snap-u2", envelope_id="env-u2")
    row = conn.execute(
        "SELECT snapshot_id, envelope_id FROM venue_commands WHERE command_id = ?",
        ("cmd-ok",),
    ).fetchone()
    assert dict(row) == {"snapshot_id": "snap-u2", "envelope_id": "env-u2"}


def test_command_insert_rejects_envelope_shape_mismatch(conn):
    insert_snapshot(conn, _snapshot("snap-u2"))
    insert_submission_envelope(conn, _envelope(side="SELL"), envelope_id="env-wrong-side")
    insert_submission_envelope(conn, _envelope(price=Decimal("0.51")), envelope_id="env-wrong-price")
    insert_submission_envelope(conn, _envelope(size=Decimal("99")), envelope_id="env-wrong-size")

    with pytest.raises(ValueError, match="side"):
        _insert_command(conn, command_id="cmd-wrong-side", envelope_id="env-wrong-side")

    with pytest.raises(ValueError, match="price"):
        _insert_command(conn, command_id="cmd-wrong-price", envelope_id="env-wrong-price")

    with pytest.raises(ValueError, match="size"):
        _insert_command(conn, command_id="cmd-wrong-size", envelope_id="env-wrong-size")


def test_order_facts_state_grammar_includes_RESTING_HEARTBEAT_CANCEL_SUSPECTED(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)

    append_order_fact(
        conn,
        venue_order_id="ord-u2",
        command_id=command_id,
        state="RESTING",
        remaining_size="10",
        matched_size="0",
        source="REST",
        observed_at=NOW.isoformat(),
        local_sequence=1,
        raw_payload_hash=HASH_A,
        raw_payload_json={"status": "RESTING"},
    )
    append_order_fact(
        conn,
        venue_order_id="ord-u2",
        command_id=command_id,
        state="HEARTBEAT_CANCEL_SUSPECTED",
        remaining_size="10",
        matched_size="0",
        source="OPERATOR",
        observed_at=(NOW + timedelta(seconds=1)).isoformat(),
        local_sequence=2,
        raw_payload_hash=HASH_B,
        raw_payload_json={"status": "HEARTBEAT_CANCEL_SUSPECTED"},
    )

    states = [
        r["state"]
        for r in conn.execute(
            "SELECT state FROM venue_order_facts WHERE command_id = ? ORDER BY local_sequence",
            (command_id,),
        )
    ]
    assert states == ["RESTING", "HEARTBEAT_CANCEL_SUSPECTED"]

    with pytest.raises(ValueError, match="order fact state"):
        append_order_fact(
            conn,
            venue_order_id="ord-u2",
            command_id=command_id,
            state="FILLED",
            source="REST",
            observed_at=NOW.isoformat(),
            raw_payload_hash=HASH_A,
            raw_payload_json={},
        )


def test_trade_facts_split_MATCHED_MINED_CONFIRMED_RETRYING_FAILED(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)

    for seq, state in enumerate(["MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"], start=1):
        append_trade_fact(
            conn,
            trade_id=f"trade-u2-{seq}",
            venue_order_id="ord-u2",
            command_id=command_id,
            state=state,
            filled_size="2",
            fill_price="0.50",
            fee_paid_micro=0,
            tx_hash=f"0xtx{seq}" if state in {"MINED", "CONFIRMED"} else None,
            block_number=100 + seq if state in {"MINED", "CONFIRMED"} else None,
            confirmation_count=12 if state == "CONFIRMED" else 0,
            source="CHAIN" if state in {"MINED", "CONFIRMED"} else "WS_USER",
            observed_at=(NOW + timedelta(seconds=seq)).isoformat(),
            local_sequence=seq,
            raw_payload_hash=HASH_B,
            raw_payload_json={"state": state},
        )

    states = [r["state"] for r in conn.execute("SELECT state FROM venue_trade_facts ORDER BY local_sequence")]
    assert states == ["MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"]

    with pytest.raises(ValueError, match="trade fact state"):
        append_trade_fact(
            conn,
            trade_id="trade-resting",
            venue_order_id="ord-u2",
            command_id=command_id,
            state="RESTING",
            filled_size="2",
            fill_price="0.50",
            source="REST",
            observed_at=NOW.isoformat(),
            raw_payload_hash=HASH_C,
            raw_payload_json={},
        )


def test_fill_progress_trade_facts_require_positive_finite_economics(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)

    for seq, state in enumerate(("MATCHED", "MINED", "CONFIRMED"), start=1):
        with pytest.raises(ValueError, match="positive finite fill economics"):
            append_trade_fact(
                conn,
                trade_id=f"trade-invalid-fill-{state.lower()}",
                venue_order_id="ord-u2",
                command_id=command_id,
                state=state,
                filled_size="0" if state != "CONFIRMED" else "NaN",
                fill_price="0.50",
                source="CHAIN" if state in {"MINED", "CONFIRMED"} else "WS_USER",
                observed_at=(NOW + timedelta(seconds=20 + seq)).isoformat(),
                raw_payload_hash=HASH_C,
                raw_payload_json={"state": state},
            )

    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0


def test_failed_and_retrying_trade_facts_allow_non_fill_economics(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)

    for seq, state in enumerate(("RETRYING", "FAILED"), start=1):
        append_trade_fact(
            conn,
            trade_id=f"trade-non-fill-{state.lower()}",
            venue_order_id="ord-u2",
            command_id=command_id,
            state=state,
            filled_size="0",
            fill_price="0",
            source="WS_USER",
            observed_at=(NOW + timedelta(seconds=30 + seq)).isoformat(),
            raw_payload_hash=HASH_D,
            raw_payload_json={"state": state},
        )

    states = [
        row["state"]
        for row in conn.execute("SELECT state FROM venue_trade_facts ORDER BY trade_fact_id")
    ]
    assert states == ["RETRYING", "FAILED"]


def test_position_lots_optimistic_vs_confirmed_split(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)
    matched_fact_id = append_trade_fact(
        conn,
        trade_id="trade-optimistic",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="MATCHED",
        filled_size="10",
        fill_price="0.50",
        source="WS_USER",
        observed_at=NOW.isoformat(),
        raw_payload_hash=HASH_A,
        raw_payload_json={"state": "MATCHED"},
    )
    confirmed_fact_id = append_trade_fact(
        conn,
        trade_id="trade-confirmed",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="CONFIRMED",
        filled_size="10",
        fill_price="0.50",
        tx_hash="0xconfirmed",
        block_number=123,
        confirmation_count=12,
        source="CHAIN",
        observed_at=(NOW + timedelta(seconds=1)).isoformat(),
        raw_payload_hash=HASH_B,
        raw_payload_json={"state": "CONFIRMED"},
    )

    append_position_lot(
        conn,
        position_id=1,
        state="OPTIMISTIC_EXPOSURE",
        shares=10,
        entry_price_avg="0.50",
        source_command_id=command_id,
        source_trade_fact_id=matched_fact_id,
        captured_at=NOW.isoformat(),
        state_changed_at=NOW.isoformat(),
    )
    append_position_lot(
        conn,
        position_id=1,
        state="CONFIRMED_EXPOSURE",
        shares=10,
        entry_price_avg="0.50",
        source_command_id=command_id,
        source_trade_fact_id=confirmed_fact_id,
        captured_at=(NOW + timedelta(seconds=2)).isoformat(),
        state_changed_at=(NOW + timedelta(seconds=2)).isoformat(),
    )

    rows = conn.execute(
        "SELECT state, source_command_id, source_trade_fact_id FROM position_lots ORDER BY lot_id"
    ).fetchall()
    assert [r["state"] for r in rows] == ["OPTIMISTIC_EXPOSURE", "CONFIRMED_EXPOSURE"]
    assert rows[0]["source_command_id"] == command_id
    assert rows[0]["source_trade_fact_id"] == matched_fact_id
    assert rows[1]["source_trade_fact_id"] == confirmed_fact_id


def test_position_lots_preserve_fractional_trade_fact_size_authority(conn):
    """position_lots.shares preserves the venue filled_size economic object."""
    shares_column = {
        row["name"]: row["type"]
        for row in conn.execute("PRAGMA table_info(position_lots)").fetchall()
    }["shares"]
    assert shares_column.upper() == "TEXT"

    _, _, command_id = _seed_snapshot_envelope_command(conn)
    matched_fact_id = append_trade_fact(
        conn,
        trade_id="trade-fractional-matched",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="MATCHED",
        filled_size="10.25",
        fill_price="0.50",
        source="WS_USER",
        observed_at=NOW.isoformat(),
        raw_payload_hash=HASH_A,
        raw_payload_json={"state": "MATCHED", "size": "10.25"},
    )

    append_position_lot(
        conn,
        position_id=1,
        state="OPTIMISTIC_EXPOSURE",
        shares="10.25",
        entry_price_avg="0.50",
        source_command_id=command_id,
        source_trade_fact_id=matched_fact_id,
        captured_at=NOW.isoformat(),
        state_changed_at=NOW.isoformat(),
    )

    row = conn.execute(
        """
        SELECT lot.shares, trade.filled_size
          FROM position_lots lot
          JOIN venue_trade_facts trade
            ON trade.trade_fact_id = lot.source_trade_fact_id
         WHERE lot.source_trade_fact_id = ?
        """,
        (matched_fact_id,),
    ).fetchone()
    assert Decimal(str(row["shares"])) == Decimal("10.25")
    assert Decimal(str(row["shares"])) == Decimal(row["filled_size"])

    with pytest.raises(ValueError, match="shares must equal"):
        append_position_lot(
            conn,
            position_id=1,
            state="OPTIMISTIC_EXPOSURE",
            shares=10,
            entry_price_avg="0.50",
            source_command_id=command_id,
            source_trade_fact_id=matched_fact_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )


def test_active_exposure_lots_require_state_compatible_trade_fact_authority(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)
    matched_fact_id = append_trade_fact(
        conn,
        trade_id="trade-authority-matched",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="MATCHED",
        filled_size="10",
        fill_price="0.50",
        source="WS_USER",
        observed_at=NOW.isoformat(),
        raw_payload_hash=HASH_A,
        raw_payload_json={"state": "MATCHED"},
    )
    confirmed_fact_id = append_trade_fact(
        conn,
        trade_id="trade-authority-confirmed",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="CONFIRMED",
        filled_size="10",
        fill_price="0.50",
        tx_hash="0xconfirmed",
        block_number=123,
        confirmation_count=12,
        source="CHAIN",
        observed_at=(NOW + timedelta(seconds=1)).isoformat(),
        raw_payload_hash=HASH_B,
        raw_payload_json={"state": "CONFIRMED"},
    )

    with pytest.raises(ValueError, match="requires source_trade_fact_id"):
        append_position_lot(
            conn,
            position_id=1,
            state="CONFIRMED_EXPOSURE",
            shares=10,
            entry_price_avg="0.50",
            source_command_id=command_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )

    with pytest.raises(ValueError, match="CONFIRMED_EXPOSURE requires trade fact state"):
        append_position_lot(
            conn,
            position_id=1,
            state="CONFIRMED_EXPOSURE",
            shares=10,
            entry_price_avg="0.50",
            source_command_id=command_id,
            source_trade_fact_id=matched_fact_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )

    with pytest.raises(ValueError, match="OPTIMISTIC_EXPOSURE requires trade fact state"):
        append_position_lot(
            conn,
            position_id=1,
            state="OPTIMISTIC_EXPOSURE",
            shares=10,
            entry_price_avg="0.50",
            source_command_id=command_id,
            source_trade_fact_id=confirmed_fact_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )

    _insert_command(conn, command_id="cmd-other")
    with pytest.raises(ValueError, match="source_command_id must match"):
        append_position_lot(
            conn,
            position_id=1,
            state="CONFIRMED_EXPOSURE",
            shares=10,
            entry_price_avg="0.50",
            source_command_id="cmd-other",
            source_trade_fact_id=confirmed_fact_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )

    bad_fact_id = conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, state, filled_size,
            fill_price, source, observed_at, local_sequence,
            raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trade-authority-bad-economics",
            "ord-u2",
            command_id,
            "CONFIRMED",
            "0",
            "0.50",
            "CHAIN",
            NOW.isoformat(),
            1,
            HASH_C,
            json.dumps({"state": "CONFIRMED"}),
        ),
    ).lastrowid
    with pytest.raises(ValueError, match="positive finite fill economics"):
        append_position_lot(
            conn,
            position_id=1,
            state="CONFIRMED_EXPOSURE",
            shares=10,
            entry_price_avg="0.50",
            source_command_id=command_id,
            source_trade_fact_id=int(bad_fact_id),
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )

    with pytest.raises(ValueError, match="shares must equal"):
        append_position_lot(
            conn,
            position_id=1,
            state="CONFIRMED_EXPOSURE",
            shares=999,
            entry_price_avg="0.50",
            source_command_id=command_id,
            source_trade_fact_id=confirmed_fact_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )

    with pytest.raises(ValueError, match="entry_price_avg must equal"):
        append_position_lot(
            conn,
            position_id=1,
            state="OPTIMISTIC_EXPOSURE",
            shares=10,
            entry_price_avg="0.99",
            source_command_id=command_id,
            source_trade_fact_id=matched_fact_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )

    conn.execute(
        "UPDATE venue_commands SET intent_kind = 'EXIT', side = 'SELL' WHERE command_id = ?",
        (command_id,),
    )
    with pytest.raises(ValueError, match="ENTRY BUY"):
        append_position_lot(
            conn,
            position_id=1,
            state="CONFIRMED_EXPOSURE",
            shares=10,
            entry_price_avg="0.50",
            source_command_id=command_id,
            source_trade_fact_id=confirmed_fact_id,
            captured_at=NOW.isoformat(),
            state_changed_at=NOW.isoformat(),
        )


def test_position_lots_schema_rejects_direct_active_exposure_without_trade_authority(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)

    with pytest.raises(sqlite3.IntegrityError, match="OPTIMISTIC_EXPOSURE requires"):
        conn.execute(
            """
            INSERT INTO position_lots (
                position_id, state, shares, entry_price_avg, source_command_id,
                captured_at, state_changed_at, source, observed_at,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "OPTIMISTIC_EXPOSURE",
                10,
                "0.50",
                command_id,
                NOW.isoformat(),
                NOW.isoformat(),
                "WS_USER",
                NOW.isoformat(),
                1,
                HASH_A,
                "{}",
            ),
        )

    confirmed_fact_id = append_trade_fact(
        conn,
        trade_id="trade-schema-confirmed",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="CONFIRMED",
        filled_size="10",
        fill_price="0.50",
        source="CHAIN",
        observed_at=NOW.isoformat(),
        raw_payload_hash=HASH_B,
        raw_payload_json={"state": "CONFIRMED"},
    )
    with pytest.raises(sqlite3.IntegrityError, match="OPTIMISTIC_EXPOSURE requires"):
        conn.execute(
            """
            INSERT INTO position_lots (
                position_id, state, shares, entry_price_avg, source_command_id,
                source_trade_fact_id, captured_at, state_changed_at, source,
                observed_at, local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "OPTIMISTIC_EXPOSURE",
                10,
                "0.50",
                command_id,
                confirmed_fact_id,
                NOW.isoformat(),
                NOW.isoformat(),
                "WS_USER",
                NOW.isoformat(),
                1,
                HASH_C,
                "{}",
            ),
        )

    with pytest.raises(sqlite3.IntegrityError, match="CONFIRMED_EXPOSURE requires"):
        conn.execute(
            """
            INSERT INTO position_lots (
                position_id, state, shares, entry_price_avg, source_command_id,
                source_trade_fact_id, captured_at, state_changed_at, source,
                observed_at, local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "CONFIRMED_EXPOSURE",
                999,
                "0.50",
                command_id,
                confirmed_fact_id,
                NOW.isoformat(),
                NOW.isoformat(),
                "CHAIN",
                NOW.isoformat(),
                1,
                HASH_A,
                "{}",
            ),
        )

    with pytest.raises(sqlite3.IntegrityError, match="CONFIRMED_EXPOSURE requires"):
        conn.execute(
            """
            INSERT INTO position_lots (
                position_id, state, shares, entry_price_avg, source_command_id,
                source_trade_fact_id, captured_at, state_changed_at, source,
                observed_at, local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "CONFIRMED_EXPOSURE",
                10,
                "0.99",
                command_id,
                confirmed_fact_id,
                NOW.isoformat(),
                NOW.isoformat(),
                "CHAIN",
                NOW.isoformat(),
                1,
                HASH_B,
                "{}",
            ),
        )

    bad_fact_id = conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, state, filled_size,
            fill_price, source, observed_at, local_sequence,
            raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trade-schema-bad-economics",
            "ord-u2",
            command_id,
            "MATCHED",
            "10bad",
            "0.50",
            "WS_USER",
            NOW.isoformat(),
            1,
            HASH_D,
            json.dumps({"state": "MATCHED"}),
        ),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="OPTIMISTIC_EXPOSURE requires"):
        conn.execute(
            """
            INSERT INTO position_lots (
                position_id, state, shares, entry_price_avg, source_command_id,
                source_trade_fact_id, captured_at, state_changed_at, source,
                observed_at, local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "OPTIMISTIC_EXPOSURE",
                10,
                "0.50",
                command_id,
                int(bad_fact_id),
                NOW.isoformat(),
                NOW.isoformat(),
                "WS_USER",
                NOW.isoformat(),
                1,
                HASH_E,
                "{}",
            ),
        )

    conn.execute(
        "UPDATE venue_commands SET intent_kind = 'EXIT', side = 'SELL' WHERE command_id = ?",
        (command_id,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="CONFIRMED_EXPOSURE requires"):
        conn.execute(
            """
            INSERT INTO position_lots (
                position_id, state, shares, entry_price_avg, source_command_id,
                source_trade_fact_id, captured_at, state_changed_at, source,
                observed_at, local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "CONFIRMED_EXPOSURE",
                10,
                "0.50",
                command_id,
                confirmed_fact_id,
                NOW.isoformat(),
                NOW.isoformat(),
                "CHAIN",
                NOW.isoformat(),
                1,
                HASH_C,
                "{}",
            ),
        )


def test_calibration_training_filters_for_CONFIRMED_only(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)
    for state in ("MATCHED", "MINED", "CONFIRMED"):
        append_trade_fact(
            conn,
            trade_id=f"trade-{state.lower()}",
            venue_order_id="ord-u2",
            command_id=command_id,
            state=state,
            filled_size="1",
            fill_price="0.50",
            source="CHAIN" if state in {"MINED", "CONFIRMED"} else "WS_USER",
            observed_at=NOW.isoformat(),
            raw_payload_hash=HASH_A,
            raw_payload_json={"state": state},
        )

    rows = load_calibration_trade_facts(conn)
    assert [r["trade_id"] for r in rows] == ["trade-confirmed"]
    assert all(r["state"] == "CONFIRMED" for r in rows)

    with pytest.raises(ValueError, match="CONFIRMED"):
        load_calibration_trade_facts(conn, states=["MATCHED", "MINED"])


def test_calibration_training_rejects_confirmed_without_fill_economics(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, state, filled_size,
            fill_price, source, observed_at, local_sequence,
            raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trade-confirmed-bad-economics",
            "ord-u2",
            command_id,
            "CONFIRMED",
            "0",
            "0.50",
            "CHAIN",
            NOW.isoformat(),
            1,
            HASH_E,
            json.dumps({"state": "CONFIRMED"}),
        ),
    )

    with pytest.raises(ValueError, match="positive finite fill economics"):
        load_calibration_trade_facts(conn)


def test_optimistic_exposure_rolled_back_on_FAILED_trade(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)
    matched_fact_id = append_trade_fact(
        conn,
        trade_id="trade-fail",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="MATCHED",
        filled_size="10",
        fill_price="0.50",
        source="WS_USER",
        observed_at=NOW.isoformat(),
        raw_payload_hash=HASH_A,
        raw_payload_json={"state": "MATCHED"},
    )
    append_position_lot(
        conn,
        position_id=1,
        state="OPTIMISTIC_EXPOSURE",
        shares=10,
        entry_price_avg="0.50",
        source_command_id=command_id,
        source_trade_fact_id=matched_fact_id,
        captured_at=NOW.isoformat(),
        state_changed_at=NOW.isoformat(),
    )
    failed_fact_id = append_trade_fact(
        conn,
        trade_id="trade-fail",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="FAILED",
        filled_size="10",
        fill_price="0.50",
        source="CHAIN",
        observed_at=(NOW + timedelta(seconds=2)).isoformat(),
        raw_payload_hash=HASH_B,
        raw_payload_json={"state": "FAILED"},
    )

    rollback_optimistic_lot_for_failed_trade(
        conn,
        source_trade_fact_id=matched_fact_id,
        failed_trade_fact_id=failed_fact_id,
        state_changed_at=(NOW + timedelta(seconds=3)).isoformat(),
    )

    states = [
        r["state"]
        for r in conn.execute(
            "SELECT state FROM position_lots WHERE position_id = 1 ORDER BY lot_id"
        )
    ]
    assert states == ["OPTIMISTIC_EXPOSURE", "QUARANTINED"]


def test_full_provenance_chain_reconstructable(conn):
    snapshot_id, envelope_id, command_id = _seed_snapshot_envelope_command(conn)
    trade_fact_id = append_trade_fact(
        conn,
        trade_id="trade-u2",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="CONFIRMED",
        filled_size="10",
        fill_price="0.50",
        tx_hash="0xabc",
        block_number=321,
        confirmation_count=12,
        source="CHAIN",
        observed_at=NOW.isoformat(),
        raw_payload_hash=HASH_B,
        raw_payload_json={"state": "CONFIRMED"},
    )
    lot_id = append_position_lot(
        conn,
        position_id=1,
        state="CONFIRMED_EXPOSURE",
        shares=10,
        entry_price_avg="0.50",
        source_command_id=command_id,
        source_trade_fact_id=trade_fact_id,
        captured_at=NOW.isoformat(),
        state_changed_at=NOW.isoformat(),
    )

    row = conn.execute(
        """
        SELECT
          lot.lot_id,
          trade.trade_id,
          trade.tx_hash,
          cmd.command_id,
          cmd.envelope_id,
          cmd.snapshot_id,
          env.signed_order_hash,
          snap.raw_gamma_payload_hash,
          snap.raw_clob_market_info_hash,
          snap.raw_orderbook_hash
        FROM position_lots lot
        JOIN venue_trade_facts trade ON trade.trade_fact_id = lot.source_trade_fact_id
        JOIN venue_commands cmd ON cmd.command_id = trade.command_id
        JOIN venue_submission_envelopes env ON env.envelope_id = cmd.envelope_id
        JOIN executable_market_snapshots snap ON snap.snapshot_id = cmd.snapshot_id
        WHERE lot.lot_id = ?
        """,
        (lot_id,),
    ).fetchone()

    assert row["trade_id"] == "trade-u2"
    assert row["command_id"] == command_id
    assert row["envelope_id"] == envelope_id
    assert row["snapshot_id"] == snapshot_id
    assert row["signed_order_hash"] == HASH_E
    assert row["raw_gamma_payload_hash"] == HASH_A
    assert row["raw_clob_market_info_hash"] == HASH_B
    assert row["raw_orderbook_hash"] == HASH_C


def test_local_sequence_monotonic_per_subject(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)

    append_order_fact(
        conn,
        venue_order_id="ord-u2",
        command_id=command_id,
        state="RESTING",
        source="REST",
        observed_at=NOW.isoformat(),
        local_sequence=1,
        raw_payload_hash=HASH_A,
        raw_payload_json={},
    )
    append_order_fact(
        conn,
        venue_order_id="ord-u2",
        command_id=command_id,
        state="MATCHED",
        source="WS_USER",
        observed_at=(NOW + timedelta(seconds=1)).isoformat(),
        local_sequence=2,
        raw_payload_hash=HASH_B,
        raw_payload_json={},
    )

    with pytest.raises(ValueError, match="local_sequence"):
        append_order_fact(
            conn,
            venue_order_id="ord-u2",
            command_id=command_id,
            state="PARTIALLY_MATCHED",
            source="WS_USER",
            observed_at=(NOW + timedelta(seconds=2)).isoformat(),
            local_sequence=2,
            raw_payload_hash=HASH_C,
            raw_payload_json={},
        )

    append_order_fact(
        conn,
        venue_order_id="ord-u2-other",
        command_id=command_id,
        state="RESTING",
        source="REST",
        observed_at=NOW.isoformat(),
        local_sequence=1,
        raw_payload_hash=HASH_D,
        raw_payload_json={},
    )

    auto_id = append_order_fact(
        conn,
        venue_order_id="ord-u2",
        command_id=command_id,
        state="CANCEL_REQUESTED",
        source="OPERATOR",
        observed_at=(NOW + timedelta(seconds=3)).isoformat(),
        raw_payload_hash=HASH_E,
        raw_payload_json={},
    )
    assert conn.execute(
        "SELECT local_sequence FROM venue_order_facts WHERE fact_id = ?",
        (auto_id,),
    ).fetchone()["local_sequence"] == 3


def test_source_field_required_on_every_event(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)

    with pytest.raises(ValueError, match="source"):
        append_order_fact(
            conn,
            venue_order_id="ord-u2",
            command_id=command_id,
            state="RESTING",
            source="",
            observed_at=NOW.isoformat(),
            raw_payload_hash=HASH_A,
            raw_payload_json={},
        )

    with pytest.raises(ValueError, match="observed_at"):
        append_trade_fact(
            conn,
            trade_id="trade-missing-time",
            venue_order_id="ord-u2",
            command_id=command_id,
            state="MATCHED",
            filled_size="1",
            fill_price="0.50",
            source="WS_USER",
            observed_at=None,
            raw_payload_hash=HASH_A,
            raw_payload_json={},
        )

    with pytest.raises(ValueError, match="raw_payload_hash"):
        append_order_fact(
            conn,
            venue_order_id="ord-u2",
            command_id=command_id,
            state="RESTING",
            source="REST",
            observed_at=NOW.isoformat(),
            raw_payload_hash="not-a-hash",
            raw_payload_json={},
        )

    with pytest.raises(ValueError, match="source"):
        append_provenance_event(
            conn,
            subject_type="order",
            subject_id="ord-u2",
            event_type="RESTING",
            payload_hash=HASH_B,
            payload_json={},
            source="TWITTER",
            observed_at=NOW.isoformat(),
        )


def test_command_events_are_mirrored_with_u2_provenance(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=(NOW + timedelta(seconds=1)).isoformat(),
        payload={"order_type": "GTC"},
    )

    rows = conn.execute(
        """
        SELECT event_type, source, observed_at, local_sequence, payload_hash
        FROM provenance_envelope_events
        WHERE subject_type = 'command' AND subject_id = ?
        ORDER BY local_sequence
        """,
        (command_id,),
    ).fetchall()

    assert [r["event_type"] for r in rows] == ["INTENT_CREATED", "SUBMIT_REQUESTED"]
    assert [r["local_sequence"] for r in rows] == [1, 2]
    assert all(r["source"] == "OPERATOR" for r in rows)
    assert all(r["observed_at"] for r in rows)
    assert all(len(r["payload_hash"]) == 64 for r in rows)


def test_redeem_can_be_traced_to_tx_hash_and_chain_receipt(conn):
    _, _, command_id = _seed_snapshot_envelope_command(conn)
    trade_fact_id = append_trade_fact(
        conn,
        trade_id="trade-settlement",
        venue_order_id="ord-u2",
        command_id=command_id,
        state="CONFIRMED",
        filled_size="10",
        fill_price="0.50",
        tx_hash="0xentry",
        block_number=123,
        confirmation_count=12,
        source="CHAIN",
        observed_at=NOW.isoformat(),
        raw_payload_hash=HASH_A,
        raw_payload_json={},
    )
    lot_id = append_position_lot(
        conn,
        position_id=1,
        state="SETTLED",
        shares=10,
        entry_price_avg="0.50",
        source_command_id=command_id,
        source_trade_fact_id=trade_fact_id,
        captured_at=NOW.isoformat(),
        state_changed_at=NOW.isoformat(),
    )

    append_provenance_event(
        conn,
        subject_type="settlement",
        subject_id=str(lot_id),
        event_type="REDEEM_CONFIRMED",
        payload_hash=HASH_D,
        payload_json={
            "tx_hash": "0xredeem",
            "block_number": 123456,
            "receipt_status": 1,
            "source_trade_fact_id": trade_fact_id,
            "command_id": command_id,
        },
        source="CHAIN",
        observed_at=NOW.isoformat(),
        local_sequence=1,
    )

    event = conn.execute(
        """
        SELECT payload_json, source
        FROM provenance_envelope_events
        WHERE subject_type = 'settlement' AND subject_id = ?
        """,
        (str(lot_id),),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["source"] == "CHAIN"
    assert payload["tx_hash"] == "0xredeem"
    assert payload["receipt_status"] == 1
    assert payload["source_trade_fact_id"] == trade_fact_id
    assert payload["command_id"] == command_id
