# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: docs/archive/2026-Q2/operations_historical/k1_final_snapshot_authority_plan_2026-06-11.md §4 STAGE 1, §5.2
"""K=1 STAGE 1 antibody tests — persist the fresh submit-time JIT book (R8) as a
first-class ``executable_market_snapshots`` row tagged ``source=JIT_PRESUBMIT``,
BEFORE it is consumed.  Pure additive persistence + provenance; the flag
``k1_persist_presubmit_snapshot_enabled`` (default OFF) gates the write so flag
OFF is byte-identical.

Relationship invariants (the row IS the witness book it was built from):
  - test_presubmit_snapshot_row_matches_witness_book: row best_bid/best_ask ==
    authority_witness.current_best_* (R8 -> row identity).
  - test_presubmit_snapshot_provenance_jit: row source/authority tag ==
    JIT_PRESUBMIT, captured_at == fetch instant (provenance envelope, Fitz #4).
  - test_persist_off_is_byte_identical: flag OFF -> zero new rows.
  - test_persist_failure_never_blocks_submit: a failing persist returns falsy and
    NEVER raises (fail-soft pin — persist is substrate, not a gate).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
from src.engine.event_reactor_adapter import (
    PreSubmitAuthorityWitness,
    JIT_PRESUBMIT_PROVENANCE_SOURCE,
    build_presubmit_snapshot_row,
    persist_presubmit_jit_snapshot,
)
from src.state.snapshot_repo import (
    SNAPSHOT_TABLE,
    get_snapshot,
    init_snapshot_schema,
    insert_snapshot,
)

_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
_FETCH_INSTANT = datetime(2026, 6, 11, 12, 0, 5, tzinfo=timezone.utc)


def _elected_snapshot(*, snapshot_id: str = "snap-elected") -> ExecutableMarketSnapshot:
    """An elected DB snapshot row (the family-identity carrier the JIT row mirrors)."""

    return ExecutableMarketSnapshot(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-test",
        event_id="event-test",
        event_slug="event-test",
        condition_id="condition-test",
        question_id="question-test",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id="no-token",
        outcome_label="NO",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=None,
        market_end_at=None,
        market_close_at=None,
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("1.0"),
        fee_details={},
        token_map_raw={"YES": "yes-token", "NO": "no-token"},
        rfqe=None,
        neg_risk=False,
        # Stale DB book prices — must be OVERWRITTEN by the fresh witness book.
        orderbook_top_bid=Decimal("0.40"),
        orderbook_top_ask=Decimal("0.42"),
        orderbook_depth_jsonb="{}",
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        captured_at=_NOW,
        freshness_deadline=_NOW + timedelta(seconds=30),
    )


def _witness(*, best_bid: float = 0.58, best_ask: float = 0.61) -> PreSubmitAuthorityWitness:
    """A JIT fresh-book witness (R8): fetch instant anchored to our observation."""

    fetch_iso = _FETCH_INSTANT.isoformat()
    return PreSubmitAuthorityWitness(
        quote_seen_at=fetch_iso,
        book_hash="venue-book-hash-abc123",
        current_best_bid=best_bid,
        current_best_ask=best_ask,
        tick_size=0.01,
        min_order_size=1.0,
        neg_risk=False,
        heartbeat_status="OK",
        user_ws_status="OK",
        venue_connectivity_status="OK",
        balance_allowance_status="OK",
        book_authority_id="clob_jit_book",
        book_captured_at=fetch_iso,
        heartbeat_authority_id="heartbeat_supervisor",
        heartbeat_checked_at=fetch_iso,
        user_ws_authority_id="ws_gap_guard",
        user_ws_checked_at=fetch_iso,
        venue_connectivity_authority_id="polymarket_v2_preflight",
        venue_connectivity_checked_at=fetch_iso,
        balance_allowance_authority_id="collateral",
        balance_allowance_checked_at=fetch_iso,
        checked_at=fetch_iso,
        max_quote_age_ms=1000,
    )


def _trade_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_snapshot_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# build_presubmit_snapshot_row — pure (no I/O) identity + provenance
# ---------------------------------------------------------------------------

def test_presubmit_snapshot_row_matches_witness_book() -> None:
    """The persisted row's best_bid/best_ask == the witness book values (R8->row)."""

    elected = _elected_snapshot()
    witness = _witness(best_bid=0.58, best_ask=0.61)

    row = build_presubmit_snapshot_row(
        elected,
        witness=witness,
        decision_time=_FETCH_INSTANT,
    )

    # The fresh witness book — NOT the stale elected DB book — is what was persisted.
    assert row.orderbook_top_bid == Decimal("0.58")
    assert row.orderbook_top_ask == Decimal("0.61")
    assert row.orderbook_top_bid != elected.orderbook_top_bid
    assert row.orderbook_top_ask != elected.orderbook_top_ask
    # Family identity is inherited from the elected snapshot (mirror minimally).
    assert row.condition_id == elected.condition_id
    assert row.yes_token_id == elected.yes_token_id
    assert row.no_token_id == elected.no_token_id
    assert row.selected_outcome_token_id == elected.selected_outcome_token_id
    # A new immutable identity (never collides with the elected row).
    assert row.snapshot_id != elected.snapshot_id


def test_presubmit_snapshot_provenance_jit() -> None:
    """Row source/authority tag == JIT_PRESUBMIT, captured_at == fetch instant."""

    elected = _elected_snapshot()
    witness = _witness()

    row = build_presubmit_snapshot_row(
        elected,
        witness=witness,
        decision_time=_FETCH_INSTANT,
    )

    # captured_at is the fetch instant (the witness observation time), NOT the
    # elected snapshot's stale captured_at.
    assert row.captured_at == _FETCH_INSTANT
    assert row.captured_at != elected.captured_at
    # The provenance envelope honestly records JIT_PRESUBMIT.
    assert row.tradeability_status is not None
    status_json = row.tradeability_status.to_json_dict()
    assert status_json.get("provenance_source") == JIT_PRESUBMIT_PROVENANCE_SOURCE
    assert JIT_PRESUBMIT_PROVENANCE_SOURCE == "JIT_PRESUBMIT"
    # The fresh JIT book is a live CLOB read — authority_tier honestly says CLOB.
    assert row.authority_tier == "CLOB"


def test_presubmit_snapshot_row_is_insertable() -> None:
    """The built row passes the contract and the append-only writer (single authority)."""

    conn = _trade_conn()
    elected = _elected_snapshot()
    insert_snapshot(conn, elected)
    conn.commit()

    row = build_presubmit_snapshot_row(
        elected,
        witness=_witness(),
        decision_time=_FETCH_INSTANT,
    )
    insert_snapshot(conn, row)
    conn.commit()

    fetched = get_snapshot(conn, row.snapshot_id)
    assert fetched is not None
    assert fetched.orderbook_top_bid == Decimal("0.58")
    assert fetched.orderbook_top_ask == Decimal("0.61")
    assert fetched.tradeability_status.to_json_dict().get("provenance_source") == "JIT_PRESUBMIT"


# ---------------------------------------------------------------------------
# persist_presubmit_jit_snapshot — flag gate + fail-soft I/O
# ---------------------------------------------------------------------------

def test_persist_writes_one_row_when_enabled() -> None:
    conn = _trade_conn()
    elected = _elected_snapshot()
    insert_snapshot(conn, elected)
    conn.commit()

    before = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}").fetchone()[0]
    snapshot_id = persist_presubmit_jit_snapshot(
        conn,
        elected,
        witness=_witness(),
        decision_time=_FETCH_INSTANT,
        enabled=True,
    )
    after = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}").fetchone()[0]

    assert snapshot_id is not None
    assert after == before + 1
    persisted = get_snapshot(conn, snapshot_id)
    assert persisted is not None
    assert persisted.orderbook_top_bid == Decimal("0.58")


def test_persist_skips_inside_active_submit_transaction() -> None:
    conn = _trade_conn()
    elected = _elected_snapshot()
    insert_snapshot(conn, elected)
    conn.commit()

    before = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}").fetchone()[0]
    conn.execute("SAVEPOINT live_order_build")
    try:
        snapshot_id = persist_presubmit_jit_snapshot(
            conn,
            elected,
            witness=_witness(),
            decision_time=_FETCH_INSTANT,
            enabled=True,
        )
        during = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}").fetchone()[0]
    finally:
        conn.execute("RELEASE SAVEPOINT live_order_build")
    after = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}").fetchone()[0]

    assert snapshot_id is None
    assert during == before
    assert after == before


def test_persist_off_is_byte_identical() -> None:
    """Flag OFF (default) -> zero new rows, no behavior change."""

    conn = _trade_conn()
    elected = _elected_snapshot()
    insert_snapshot(conn, elected)
    conn.commit()

    before = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}").fetchone()[0]
    result = persist_presubmit_jit_snapshot(
        conn,
        elected,
        witness=_witness(),
        decision_time=_FETCH_INSTANT,
        enabled=False,
    )
    after = conn.execute(f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE}").fetchone()[0]

    assert result is None
    assert after == before  # zero new rows


def test_persist_failure_never_blocks_submit() -> None:
    """A failing persist returns falsy and NEVER raises (fail-soft pin)."""

    class _ExplodingConn:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("simulated DB failure")

        def commit(self):
            raise sqlite3.OperationalError("simulated commit failure")

    # Must not raise even though every DB op blows up.
    result = persist_presubmit_jit_snapshot(
        _ExplodingConn(),
        _elected_snapshot(),
        witness=_witness(),
        decision_time=_FETCH_INSTANT,
        enabled=True,
    )
    assert result is None


def test_persist_with_none_conn_is_failsoft() -> None:
    """A missing trade_conn never raises — persist is observability substrate."""

    result = persist_presubmit_jit_snapshot(
        None,
        _elected_snapshot(),
        witness=_witness(),
        decision_time=_FETCH_INSTANT,
        enabled=True,
    )
    assert result is None
