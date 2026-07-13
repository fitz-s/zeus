# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T4
#   ("continuous fill synchronizer + alias graph") — consult adjudication
#   §排序攻击 Attack A ("a fill lands after replay but before reader cutover" —
#   one-time replay is not enough).
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: unit tests for src.ingest.fill_synchronizer.sync_fills — watermark
#   resume, idempotent re-append rejection, foreign-fill handling, and the
#   advance-after-persist rollback contract.
# Reuse: run when fill_synchronizer.py changes, or when the exchange_reconcile
#   raw-trade parsing helpers it imports (_trade_id / _trade_order_ids / etc.)
#   change shape.
"""Tests for src.ingest.fill_synchronizer.sync_fills."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.ingest.fill_synchronizer import DEFAULT_SOURCE, get_watermark, sync_fills

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
YES_TOKEN = "yes-token-fill-sync"


@pytest.fixture
def conn():
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


def _seed_command(conn: sqlite3.Connection, *, command_id: str, venue_order_id: str) -> None:
    """Minimal venue_commands row (bypasses insert_command's business validation
    — these tests exercise sync_fills' attribution/idempotency/watermark
    contract, not command-lifecycle validation, which is exchange_reconcile's
    test suite's job)."""

    conn.execute(
        """
        INSERT OR IGNORE INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (?, 'snap', 'env', 'pos', 'dec', ?, 'ENTRY', ?, ?, 'BUY',
                  10.0, 0.5, ?, 'ACKED', ?, ?)
        """,
        (
            command_id,
            f"idem-{command_id}",
            YES_TOKEN,
            YES_TOKEN,
            venue_order_id,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    conn.commit()


def _trade(
    *,
    trade_id: str,
    order_id: str,
    size: str = "5",
    price: str = "0.50",
    status: str = "CONFIRMED",
    tx_hash: str | None = None,
) -> dict:
    payload = {
        "id": trade_id,
        "trade_id": trade_id,
        "orderID": order_id,
        "order_id": order_id,
        "size": size,
        "price": price,
        # _trade_fill_price (reused from exchange_reconcile) only resolves a
        # bare top-level "price" via the taker_order_id match path; an
        # explicit "fill_price" is what _first_explicit_fill_price reads for
        # a trade with no maker_orders/taker_order_id (mirrors
        # tests/test_exchange_reconcile.py's trade() helper).
        "fill_price": price,
        "status": status,
    }
    if tx_hash is not None:
        payload["transaction_hash"] = tx_hash
    return payload


class FakeSyncAdapter:
    def __init__(self, trades: list[dict]) -> None:
        self.trades = list(trades)
        self.since_calls: list[str | None] = []

    def get_trades(self, since: str | None = None) -> list[dict]:
        self.since_calls.append(since)
        return list(self.trades)


def _trade_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM venue_trade_facts ORDER BY trade_id").fetchall()


class TestBasicAttribution:
    def test_linkable_trade_is_appended_as_trade_fact(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter([_trade(trade_id="trade-1", order_id="ord-1")])

        result = sync_fills(conn, adapter, observed_at=NOW)

        assert result["appended"] == 1
        assert result["foreign_fill_count"] == 0
        rows = _trade_rows(conn)
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "trade-1"
        assert rows[0]["command_id"] == "cmd-1"

    def test_foreign_fill_is_skipped_and_counted_not_appended(self, conn):
        # No venue_commands row for ord-operator: this is a shared-wallet
        # operator fill, not a Zeus fill.
        adapter = FakeSyncAdapter(
            [_trade(trade_id="trade-foreign", order_id="ord-operator")]
        )

        result = sync_fills(conn, adapter, observed_at=NOW)

        assert result["appended"] == 0
        assert result["foreign_fill_count"] == 1
        assert _trade_rows(conn) == []

    def test_unattributable_trade_missing_state_is_counted_not_appended(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter(
            [_trade(trade_id="trade-1", order_id="ord-1", status="SOME_UNKNOWN_STATUS")]
        )

        result = sync_fills(conn, adapter, observed_at=NOW)

        assert result["appended"] == 0
        assert result["unattributable_count"] == 1
        assert _trade_rows(conn) == []


class TestIdempotentReappend:
    def test_running_the_same_batch_twice_appends_only_once(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter([_trade(trade_id="trade-1", order_id="ord-1")])

        first = sync_fills(conn, adapter, observed_at=NOW)
        second = sync_fills(
            conn, adapter, observed_at=NOW + timedelta(seconds=60)
        )

        assert first["appended"] == 1
        assert second["appended"] == 0
        assert second["skipped_idempotent"] == 1
        assert len(_trade_rows(conn)) == 1

    def test_a_genuinely_new_lifecycle_revision_is_still_appended(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        matched = FakeSyncAdapter(
            [_trade(trade_id="trade-1", order_id="ord-1", status="MATCHED")]
        )
        sync_fills(conn, matched, observed_at=NOW)

        confirmed = FakeSyncAdapter(
            [_trade(trade_id="trade-1", order_id="ord-1", status="CONFIRMED")]
        )
        result = sync_fills(conn, confirmed, observed_at=NOW + timedelta(seconds=60))

        assert result["appended"] == 1
        rows = _trade_rows(conn)
        assert len(rows) == 2
        assert {row["state"] for row in rows} == {"MATCHED", "CONFIRMED"}


class TestDurableCoverageWatermark:
    def test_watermark_is_absent_before_first_sync(self, conn):
        assert get_watermark(conn) is None

    def test_watermark_advances_after_first_sync_and_is_passed_to_next_call(
        self, conn
    ):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter([_trade(trade_id="trade-1", order_id="ord-1")])

        sync_fills(conn, adapter, observed_at=NOW)
        watermark = get_watermark(conn)
        assert watermark is not None
        assert watermark["source"] == DEFAULT_SOURCE
        assert watermark["watermark_ts"] == NOW.isoformat()

        adapter2 = FakeSyncAdapter([])
        sync_fills(conn, adapter2, observed_at=NOW + timedelta(seconds=60))
        # sync_fills passes the PRIOR watermark as `since` on the next cycle.
        assert adapter2.since_calls == [NOW.isoformat()]

        watermark_after = get_watermark(conn)
        assert watermark_after["watermark_ts"] == (NOW + timedelta(seconds=60)).isoformat()

    def test_watermark_does_not_advance_and_no_partial_facts_persist_on_failure(
        self, conn, monkeypatch
    ):
        import src.ingest.fill_synchronizer as fill_synchronizer_mod

        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        # trade-good would append cleanly; trade-bad simulates a lower-level
        # append_trade_fact failure (e.g. a DB constraint/IO fault) AFTER
        # trade-good's append has already executed in this same cycle. The
        # whole cycle must roll back — trade-good's row must NOT survive, and
        # the watermark must NOT advance (advance-after-persist contract).
        adapter = FakeSyncAdapter(
            [
                _trade(trade_id="trade-good", order_id="ord-1", size="5"),
                _trade(trade_id="trade-bad", order_id="ord-1", size="7"),
            ]
        )

        real_append = fill_synchronizer_mod.append_trade_fact

        def _fail_on_trade_bad(conn, *, trade_id, **kwargs):
            if trade_id == "trade-bad":
                raise RuntimeError("simulated append_trade_fact failure")
            return real_append(conn, trade_id=trade_id, **kwargs)

        monkeypatch.setattr(fill_synchronizer_mod, "append_trade_fact", _fail_on_trade_bad)

        with pytest.raises(RuntimeError, match="simulated append_trade_fact failure"):
            sync_fills(conn, adapter, observed_at=NOW)

        assert _trade_rows(conn) == [], (
            "trade-good's append must be rolled back along with the failed "
            "trade-bad append — a sync cycle is all-or-nothing"
        )
        assert get_watermark(conn) is None
