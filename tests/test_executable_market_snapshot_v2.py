# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: U1 antibodies for ExecutableMarketSnapshotV2 persistence and freshness gate.
# Reuse: Run when executable snapshots, venue_commands gating, or V2 market preflight semantics change.
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/U1.yaml
"""U1 executable market snapshot and command freshness gate tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts.executable_market_snapshot_v2 import (
    ExecutableMarketSnapshotV2,
    MarketNotTradableError,
    MarketSnapshotMismatchError,
    StaleMarketSnapshotError,
    is_fresh,
)
from src.state.db import init_schema
from src.state.snapshot_repo import get_snapshot, insert_snapshot
from src.state.venue_command_repo import insert_command


NOW = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _snapshot(snapshot_id: str = "snap-u1", **overrides) -> ExecutableMarketSnapshotV2:
    payload = dict(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-1",
        event_id="event-1",
        event_slug="weather-nyc-high",
        condition_id="condition-1",
        question_id="question-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id="yes-token",
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
        min_order_size=Decimal("0.01"),
        fee_details={"bps": 0, "source": "test"},
        token_map_raw={"YES": "yes-token", "NO": "no-token"},
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
    payload.update(overrides)
    return ExecutableMarketSnapshotV2(**payload)


def _ensure_envelope(
    conn,
    *,
    token_id: str = "yes-token",
    envelope_id: str | None = None,
    price: str = "0.50",
    size: str = "10",
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    no_token_id = "no-token" if token_id == "yes-token" else f"{token_id}-no"
    envelope_id = envelope_id or f"env-{token_id}-{price}-{size}"
    if conn.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-1",
            question_id="question-1",
            yes_token_id=token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side="BUY",
            price=Decimal(str(price)),
            size=Decimal(str(size)),
            order_type="GTC",
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={},
            canonical_pre_sign_payload_hash=HASH_A,
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash=HASH_B,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def _insert_command(
    conn,
    *,
    snapshot_id: str = "snap-u1",
    token_id: str = "yes-token",
    price: float = 0.50,
    size: float = 10.0,
    expected_min_tick_size=Decimal("0.01"),
    expected_min_order_size=Decimal("0.01"),
    expected_neg_risk: bool | None = False,
    checked_at: datetime = NOW,
) -> None:
    insert_command(
        conn,
        command_id=f"cmd-{snapshot_id}-{token_id}-{price}-{size}",
        envelope_id=_ensure_envelope(conn, token_id=token_id, price=str(price), size=str(size)),
        snapshot_id=snapshot_id,
        position_id="pos-u1",
        decision_id="dec-u1",
        idempotency_key=(snapshot_id.replace("-", "") + "0" * 32)[:32],
        intent_kind="ENTRY",
        market_id="market-u1",
        token_id=token_id,
        side="BUY",
        size=size,
        price=price,
        created_at=checked_at.isoformat(),
        snapshot_checked_at=checked_at,
        expected_min_tick_size=expected_min_tick_size,
        expected_min_order_size=expected_min_order_size,
        expected_neg_risk=expected_neg_risk,
    )


def test_insert_snapshot_persists_all_fields(conn):
    snap = _snapshot(sports_start_at=NOW + timedelta(minutes=30))
    insert_snapshot(conn, snap)

    loaded = get_snapshot(conn, "snap-u1")

    assert loaded == snap
    assert loaded.sports_start_at == NOW + timedelta(minutes=30)
    assert loaded.fee_details == {"bps": 0, "source": "test"}
    assert loaded.token_map_raw == {"YES": "yes-token", "NO": "no-token"}


def test_update_snapshot_raises_via_trigger(conn):
    insert_snapshot(conn, _snapshot())

    with pytest.raises(sqlite3.IntegrityError, match="APPEND-ONLY"):
        conn.execute(
            "UPDATE executable_market_snapshots SET active = 0 WHERE snapshot_id = ?",
            ("snap-u1",),
        )


def test_delete_snapshot_raises_via_trigger(conn):
    insert_snapshot(conn, _snapshot())

    with pytest.raises(sqlite3.IntegrityError, match="APPEND-ONLY"):
        conn.execute(
            "DELETE FROM executable_market_snapshots WHERE snapshot_id = ?",
            ("snap-u1",),
        )


def test_freshness_check_fails_after_window(conn):
    snap = _snapshot(freshness_deadline=NOW + timedelta(seconds=1))

    assert is_fresh(snap, NOW + timedelta(seconds=1))
    assert not is_fresh(snap, NOW + timedelta(seconds=2))


def test_command_insertion_requires_fresh_snapshot(conn):
    with pytest.raises(StaleMarketSnapshotError, match="snapshot_id"):
        insert_command(
            conn,
            command_id="cmd-missing",
            snapshot_id=None,
            position_id="pos-u1",
            decision_id="dec-u1",
            idempotency_key="f" * 32,
            intent_kind="ENTRY",
            market_id="market-u1",
            token_id="yes-token",
            side="BUY",
            size=10.0,
            price=0.5,
            created_at=NOW.isoformat(),
        )

    insert_snapshot(conn, _snapshot())
    _insert_command(conn)
    row = conn.execute(
        "SELECT snapshot_id FROM venue_commands WHERE command_id LIKE 'cmd-snap-u1%'"
    ).fetchone()
    assert row["snapshot_id"] == "snap-u1"


def test_stale_snapshot_blocks_submit(conn):
    insert_snapshot(
        conn,
        _snapshot(
            snapshot_id="snap-stale",
            captured_at=NOW - timedelta(minutes=5),
            freshness_deadline=NOW - timedelta(minutes=4),
        ),
    )

    with pytest.raises(StaleMarketSnapshotError):
        _insert_command(conn, snapshot_id="snap-stale")


def test_enable_orderbook_false_blocks_submit(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-disabled", enable_orderbook=False))

    with pytest.raises(MarketNotTradableError, match="enable_orderbook=false"):
        _insert_command(conn, snapshot_id="snap-disabled")


def test_active_false_blocks_submit(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-inactive", active=False))

    with pytest.raises(MarketNotTradableError, match="active=false"):
        _insert_command(conn, snapshot_id="snap-inactive")


def test_closed_true_blocks_submit(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-closed", closed=True))

    with pytest.raises(MarketNotTradableError, match="closed=true"):
        _insert_command(conn, snapshot_id="snap-closed")


def test_tick_mismatch_blocks_before_signing(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-tick"))

    with pytest.raises(MarketSnapshotMismatchError, match="min_tick_size"):
        _insert_command(
            conn,
            snapshot_id="snap-tick",
            expected_min_tick_size=Decimal("0.001"),
        )

    with pytest.raises(MarketSnapshotMismatchError, match="not aligned"):
        _insert_command(conn, snapshot_id="snap-tick", price=0.333)


def test_min_order_size_mismatch_blocks_before_signing(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-min-size", min_order_size=Decimal("5")))

    with pytest.raises(MarketSnapshotMismatchError, match="min_order_size"):
        _insert_command(
            conn,
            snapshot_id="snap-min-size",
            expected_min_order_size=Decimal("0.01"),
        )

    with pytest.raises(MarketSnapshotMismatchError, match="below"):
        _insert_command(
            conn,
            snapshot_id="snap-min-size",
            size=1.0,
            expected_min_order_size=Decimal("5"),
        )


def test_sports_market_start_auto_cancel_represented_in_snapshot(conn):
    sports_start = NOW + timedelta(minutes=12)
    insert_snapshot(conn, _snapshot(snapshot_id="snap-sports", sports_start_at=sports_start))

    loaded = get_snapshot(conn, "snap-sports")

    assert loaded.sports_start_at == sports_start


def test_authority_tier_constraint_enforced(conn):
    with pytest.raises(ValueError, match="authority_tier"):
        _snapshot(snapshot_id="snap-bad-tier", authority_tier="BLOG")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
              snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
              question_id, yes_token_id, no_token_id, enable_orderbook,
              active, closed, min_tick_size, min_order_size, fee_details_json,
              token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
              orderbook_depth_json, raw_gamma_payload_hash,
              raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
              captured_at, freshness_deadline
            ) VALUES (
              'snap-bad-db', 'g', 'e', 'slug', 'c', 'q', 'y', 'n', 1, 1, 0,
              '0.01', '0.01', '{}', '{}', 0, '0.49', '0.51', '{}',
              ?, ?, ?, 'BLOG', ?, ?
            )
            """,
            (HASH_A, HASH_B, HASH_C, NOW.isoformat(), (NOW + timedelta(seconds=30)).isoformat()),
        )


def test_raw_payload_hashes_persisted_for_replay(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-hashes"))

    row = conn.execute(
        """
        SELECT raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash
        FROM executable_market_snapshots
        WHERE snapshot_id = 'snap-hashes'
        """
    ).fetchone()

    assert row["raw_gamma_payload_hash"] == HASH_A
    assert row["raw_clob_market_info_hash"] == HASH_B
    assert row["raw_orderbook_hash"] == HASH_C


def test_init_schema_migrates_legacy_venue_commands_snapshot_column():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_kind TEXT NOT NULL,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            price REAL NOT NULL,
            venue_order_id TEXT,
            state TEXT NOT NULL,
            last_event_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            review_required_reason TEXT
        )
        """
    )

    init_schema(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(venue_commands)")}
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(venue_commands)")}
    assert "snapshot_id" in columns
    assert "idx_venue_commands_snapshot" in indexes
