# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T4
#   ("continuous fill synchronizer + alias graph") — consult adjudication
#   §排序攻击 Attack A ("a fill lands after replay but before reader cutover" —
#   one-time replay is not enough). Packet I / wave-1.5 addition (2026-07-13,
#   §KEEP-spine 完备性补遗 "归属图+歧义证据 — foreign/ambiguous 留 observation
#   不丢"): durable wallet_fill_observations lane tests.
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-19; last_reused=2026-07-19
# Purpose: unit tests for src.ingest.fill_synchronizer.sync_fills — watermark
#   resume, idempotent re-append rejection, foreign-fill handling, the
#   advance-after-persist rollback contract, and (packet I / wave-1.5) the
#   durable wallet_fill_observations lane: every swept fill lands there
#   regardless of attribution, disposition is correct, it is idempotent, and
#   it is append-only at the DB level.
# Reuse: run when fill_synchronizer.py changes, or when the exchange_reconcile
#   raw-trade parsing helpers it imports (_trade_id / _trade_order_ids / etc.)
#   change shape.
"""Tests for src.ingest.fill_synchronizer.sync_fills."""
from __future__ import annotations

import json
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


def _observation_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM wallet_fill_observations ORDER BY id"
    ).fetchall()


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

    def test_zeus_fill_lands_in_both_lanes_with_consistent_economics(self, conn):
        """packet I / wave-1.5: a Zeus-attributed fill must land in BOTH
        venue_trade_facts (the existing lane) AND wallet_fill_observations
        (the new durable observation lane), with matching size/price, and
        disposition ZEUS_ATTRIBUTED."""

        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter(
            [_trade(trade_id="trade-1", order_id="ord-1", size="5", price="0.50")]
        )

        result = sync_fills(conn, adapter, observed_at=NOW)

        assert result["appended"] == 1
        assert result["observation_appended"] == 1

        fact_rows = _trade_rows(conn)
        obs_rows = _observation_rows(conn)
        assert len(fact_rows) == 1
        assert len(obs_rows) == 1
        assert obs_rows[0]["trade_id"] == "trade-1"
        assert obs_rows[0]["disposition"] == "ZEUS_ATTRIBUTED"
        assert obs_rows[0]["size"] == fact_rows[0]["filled_size"] == "5"
        assert obs_rows[0]["price"] == fact_rows[0]["fill_price"] == "0.50"
        assert json.loads(obs_rows[0]["order_ids"]) == ["ord-1"]

    def test_synchronizer_captures_venue_timestamp_as_iso_for_fold_ordering(self, conn):
        """Regression: a synchronizer-appended fill must carry venue_timestamp
        (venue match time, epoch -> ISO) in venue_trade_facts, so the economics
        reducer folds it in EXECUTION order. Without it, every synchronizer
        fill had a NULL execution time, sorted by ingestion time, and
        fabricated OversoldPositionError for settled positions whose entry the
        synchronizer re-swept (live-observed 2026-07-13)."""
        from datetime import datetime, timezone

        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        tr = _trade(trade_id="trade-1", order_id="ord-1")
        tr["match_time"] = 1783979998  # unix epoch seconds
        adapter = FakeSyncAdapter([tr])

        sync_fills(conn, adapter, observed_at=NOW)

        rows = _trade_rows(conn)
        assert len(rows) == 1
        expected = datetime.fromtimestamp(1783979998, tz=timezone.utc).isoformat()
        assert rows[0]["venue_timestamp"] == expected

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

    def test_foreign_fill_lands_in_observation_lane_as_foreign_never_in_facts(self, conn):
        """packet I / wave-1.5: the foreign fill dropped from venue_trade_facts
        must be durably retained in wallet_fill_observations with disposition
        FOREIGN — it must never appear in venue_trade_facts (that table
        structurally requires a Zeus command_id)."""

        adapter = FakeSyncAdapter(
            [_trade(trade_id="trade-foreign", order_id="ord-operator")]
        )

        result = sync_fills(conn, adapter, observed_at=NOW)

        assert result["observation_appended"] == 1
        assert _trade_rows(conn) == []

        obs_rows = _observation_rows(conn)
        assert len(obs_rows) == 1
        assert obs_rows[0]["trade_id"] == "trade-foreign"
        assert obs_rows[0]["disposition"] == "FOREIGN"
        assert json.loads(obs_rows[0]["order_ids"]) == ["ord-operator"]

    def test_trade_with_no_order_id_candidate_is_ambiguous_in_observation_lane(self, conn):
        """A raw trade with no order_id candidate at all (empty order_ids list)
        cannot even be attempted for attribution — AMBIGUOUS, distinct from a
        confirmed-foreign fill that DID carry an order_id."""

        adapter = FakeSyncAdapter([_trade(trade_id="trade-no-order", order_id="ord-unused")])
        # Strip every order-id-shaped key the _trade() helper set, leaving none
        # — a raw trade with no order_id candidate at all.
        raw = adapter.trades[0]
        for key in ("orderID", "order_id"):
            raw.pop(key, None)

        result = sync_fills(conn, adapter, observed_at=NOW)

        assert result["observation_appended"] == 1
        obs_rows = _observation_rows(conn)
        assert len(obs_rows) == 1
        assert obs_rows[0]["disposition"] == "AMBIGUOUS"
        assert json.loads(obs_rows[0]["order_ids"]) == []

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

    def test_replay_appends_nothing_new_to_the_observation_lane(self, conn):
        """packet I / wave-1.5: re-sweeping the identical venue response must
        not duplicate the wallet_fill_observations row either, for BOTH a
        Zeus-attributed and a foreign fill in the same batch."""

        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter(
            [
                _trade(trade_id="trade-zeus", order_id="ord-1"),
                _trade(trade_id="trade-foreign", order_id="ord-operator"),
            ]
        )

        first = sync_fills(conn, adapter, observed_at=NOW)
        second = sync_fills(conn, adapter, observed_at=NOW + timedelta(seconds=60))

        assert first["observation_appended"] == 2
        assert second["observation_appended"] == 0
        assert second["observation_skipped_idempotent"] == 2
        assert len(_observation_rows(conn)) == 2

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

    def test_replay_reserves_writer_only_after_idempotency_snapshot(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter([_trade(trade_id="trade-1", order_id="ord-1")])
        sync_fills(conn, adapter, observed_at=NOW)

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            sync_fills(conn, adapter, observed_at=NOW + timedelta(seconds=60))
        finally:
            conn.set_trace_callback(None)

        begin_index = statements.index("BEGIN IMMEDIATE")
        before_begin = statements[:begin_index]
        reserved_writer = statements[begin_index:]
        assert any("FROM wallet_fill_observations" in sql for sql in before_begin)
        assert any("FROM venue_trade_facts" in sql for sql in before_begin)
        assert not any(
            "FROM wallet_fill_observations" in sql
            or "FROM venue_trade_facts" in sql
            for sql in reserved_writer
        )
        assert any("INSERT INTO fill_sync_watermarks" in sql for sql in reserved_writer)


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
        assert _observation_rows(conn) == [], (
            "wallet_fill_observations inserts for the SAME failed cycle must "
            "roll back too — the observation lane shares the cycle's explicit "
            "transaction, it is not a separate commit"
        )


class TestWalletFillObservationsDbLevelInvariants:
    """packet I / wave-1.5: wallet_fill_observations is append-only and (save
    for the one-time superseded_by transition) immutable, enforced at the DB
    level regardless of which Python path writes the row."""

    def test_delete_is_blocked_at_the_db_level(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter([_trade(trade_id="trade-1", order_id="ord-1")])
        sync_fills(conn, adapter, observed_at=NOW)
        row_id = _observation_rows(conn)[0]["id"]

        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM wallet_fill_observations WHERE id = ?", (row_id,))

        assert len(_observation_rows(conn)) == 1

    def test_arbitrary_update_is_blocked_at_the_db_level(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter([_trade(trade_id="trade-1", order_id="ord-1")])
        sync_fills(conn, adapter, observed_at=NOW)
        row_id = _observation_rows(conn)[0]["id"]

        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "UPDATE wallet_fill_observations SET disposition = 'FOREIGN' WHERE id = ?",
                (row_id,),
            )

    def test_one_time_superseded_by_transition_is_allowed(self, conn):
        _seed_command(conn, command_id="cmd-1", venue_order_id="ord-1")
        adapter = FakeSyncAdapter([_trade(trade_id="trade-1", order_id="ord-1")])
        sync_fills(conn, adapter, observed_at=NOW)
        row_id = _observation_rows(conn)[0]["id"]

        conn.execute(
            """
            INSERT INTO wallet_fill_observations (
                trade_id, order_ids, observed_at, raw_payload_hash, disposition
            ) VALUES ('trade-1-corrected', '[]', ?, 'deadbeef', 'FOREIGN')
            """,
            (NOW.isoformat(),),
        )
        new_id = conn.execute(
            "SELECT id FROM wallet_fill_observations WHERE trade_id = 'trade-1-corrected'"
        ).fetchone()["id"]

        conn.execute(
            "UPDATE wallet_fill_observations SET superseded_by = ? WHERE id = ?",
            (new_id, row_id),
        )
        conn.commit()

        superseded = conn.execute(
            "SELECT superseded_by FROM wallet_fill_observations WHERE id = ?", (row_id,)
        ).fetchone()
        assert superseded["superseded_by"] == new_id

        # A second attempt to change the ALREADY-superseded row is rejected —
        # superseded_by only transitions once (NULL -> non-NULL).
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "UPDATE wallet_fill_observations SET superseded_by = ? WHERE id = ?",
                (new_id, row_id),
            )


def test_cycle_reports_failure_to_scheduler_health(monkeypatch):
    import src.data.polymarket_client as client_mod
    import src.ingest.fill_synchronizer as fill_synchronizer_mod
    import src.ingest.price_channel_ingest as price_channel_mod
    import src.state.db as db_mod

    conn = sqlite3.connect(":memory:")

    class FakeClient:
        def _ensure_v2_adapter(self):
            return object()

    monkeypatch.setattr(
        price_channel_mod,
        "_settings_section",
        lambda *_args, **_kwargs: {"fill_synchronizer_enabled": True},
    )
    monkeypatch.setattr(db_mod, "get_trade_connection", lambda **_kwargs: conn)
    monkeypatch.setattr(client_mod, "PolymarketClient", FakeClient)
    monkeypatch.setattr(
        fill_synchronizer_mod,
        "sync_fills",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )

    result = fill_synchronizer_mod.fill_synchronizer_cycle()

    assert result == {
        "status": "failed",
        "scheduler_failed": True,
        "scheduler_failure_reason": "fill_synchronizer_cycle_failed",
    }
