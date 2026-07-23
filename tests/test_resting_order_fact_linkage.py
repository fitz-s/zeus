# Created: 2026-06-09
# Last reused/audited: 2026-06-09
# Authority basis: /tmp/fee_economics_study.md §4 (post_only GTC command_id join
#   yields NO_FACT); src/state/venue_command_repo.py append_order_fact contract.
"""FIX 2: resting post_only/GTC orders must land a LIVE venue_order_fact linked
by command_id.

Before this fix, a resting maker order that polled as LIVE/OPEN/RESTING never
reached any fact-writing branch (the fill/partial/cancel branches), so the
command_id join was NO_FACT for every post_only GTC order and the maker
fill-rate measurement loop was blind to resting/partial/cancel lifecycle.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

_NOW = datetime(2026, 6, 9, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    from src.state.db import init_schema, init_schema_trade_only

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_schema_trade_only(c)
    yield c
    c.close()


def _ensure_snapshot(c, *, token_id: str) -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = f"snap-{token_id}"
    if get_snapshot(c, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        c,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
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
    return snapshot_id


def _ensure_envelope(c, *, token_id: str) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    envelope = VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="0.0.0",
        host="https://clob.test",
        chain_id=137,
        funder_address="0xfunder",
        condition_id="condition-test",
        question_id="question-test",
        yes_token_id=token_id,
        no_token_id=f"{token_id}-no",
        selected_outcome_token_id=token_id,
        outcome_label="YES",
        side="BUY",
        price=Decimal("0.50"),
        size=Decimal("10"),
        order_type="GTC",
        post_only=True,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        neg_risk=False,
        fee_details={"fee_rate_fraction": 0.05, "source": "test"},
        canonical_pre_sign_payload_hash="d" * 64,
        signed_order=None,
        signed_order_hash=None,
        raw_request_hash="d" * 64,
        raw_response_json=None,
        order_id=None,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at=_NOW.isoformat(),
    )
    return insert_submission_envelope(c, envelope=envelope)


def _make_resting_command(c, *, command_id="cmd-rest", venue_order_id="ord-rest",
                          token_id="tok-rest") -> None:
    from src.state.venue_command_repo import insert_command

    insert_command(
        c,
        command_id=command_id,
        snapshot_id=_ensure_snapshot(c, token_id=token_id),
        envelope_id=_ensure_envelope(c, token_id=token_id),
        position_id="pos-rest",
        decision_id="dec-rest",
        idempotency_key=f"idem-{command_id}",
        intent_kind="ENTRY",
        market_id="mkt-rest",
        token_id=token_id,
        side="BUY",
        size=10.0,
        price=0.5,
        created_at="2026-06-09T00:00:00Z",
    )
    c.execute(
        "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
        (venue_order_id, command_id),
    )
    c.commit()


class _NoCloseConn:
    """Proxy over a shared sqlite3 connection whose close() is a no-op.

    The resting-fact writer commits then closes its connection handle in a
    finally block; for an in-memory DB a real close would destroy the database.
    sqlite3.Connection.close is read-only so it cannot be monkeypatched — wrap
    the connection instead and swallow close().
    """

    def __init__(self, conn):
        self._conn = conn

    def close(self):  # no-op: keep the shared in-memory DB alive
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _Deps:
    def __init__(self, conn):
        self._conn = conn

    def get_connection(self):
        return _NoCloseConn(self._conn)


def _order_facts_for_command(conn, command_id: str):
    return conn.execute(
        """
        SELECT state, source, remaining_size
          FROM venue_order_facts
         WHERE command_id = ?
         ORDER BY local_sequence, fact_id
        """,
        (command_id,),
    ).fetchall()


def test_resting_gtc_ack_writes_linked_live_order_fact(conn):
    from src.execution import fill_tracker

    _make_resting_command(conn, command_id="cmd-rest", venue_order_id="ord-rest")

    # Pre-condition: command_id join is NO_FACT before the resting fact lands.
    assert _order_facts_for_command(conn, "cmd-rest") == []

    pos = SimpleNamespace(entry_order_id="ord-rest", order_id="ord-rest", trade_id="trd-rest")
    payload = {"status": "LIVE", "order_id": "ord-rest"}

    fill_tracker._maybe_append_resting_order_fact(
        pos, payload, observed_at=_NOW, deps=_Deps(conn)
    )

    rows = _order_facts_for_command(conn, "cmd-rest")
    assert len(rows) == 1
    assert rows[0]["state"] == "LIVE"
    assert rows[0]["source"] == "REST"


def test_resting_fact_is_idempotent_across_polls(conn):
    from src.execution import fill_tracker

    _make_resting_command(conn, command_id="cmd-rest", venue_order_id="ord-rest")
    pos = SimpleNamespace(entry_order_id="ord-rest", order_id="ord-rest", trade_id="trd-rest")
    payload = {"status": "LIVE", "order_id": "ord-rest"}
    deps = _Deps(conn)

    # Poll three cycles while the order keeps resting LIVE.
    for _ in range(3):
        fill_tracker._maybe_append_resting_order_fact(pos, payload, observed_at=_NOW, deps=deps)

    # Exactly ONE LIVE fact — a resting order does not append a row every poll.
    rows = _order_facts_for_command(conn, "cmd-rest")
    assert len(rows) == 1
    assert rows[0]["state"] == "LIVE"


def test_resting_fact_skipped_when_no_command_linkage(conn):
    from src.execution import fill_tracker

    # No command carries this venue_order_id -> nothing to link, no fact written,
    # no raise into the poll loop.
    pos = SimpleNamespace(entry_order_id="ord-unknown", order_id="ord-unknown", trade_id="trd-x")
    fill_tracker._maybe_append_resting_order_fact(
        pos, {"status": "LIVE"}, observed_at=_NOW, deps=_Deps(conn)
    )
    assert _order_facts_for_command(conn, "cmd-rest") == []
