# Created: 2026-10-01
# Last audited: 2026-10-01
# Authority basis: adverse-selection study capture request 2026-10-01.
import asyncio
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest

from src.events.market_tob_recorder import RETENTION_DAYS, TobRecorder
from src.events.triggers.market_channel_ingestor import (
    MarketChannelIngestor,
    MarketChannelOnlineService,
)
from src.state.schema.market_tob_transitions_schema import ensure_table

T = "tok"
BOOK = {
    "event_type": "book",
    "asset_id": T,
    "timestamp": "1789178400000",
    "bids": [{"price": "0.40", "size": "10"}, {"price": "0.39", "size": "5"}],
    "asks": [{"price": "0.43", "size": "7"}],
}


def delta(price, size, side, ts="1789178401000"):
    return {
        "event_type": "price_change",
        "timestamp": ts,
        "price_changes": [
            {"asset_id": T, "price": price, "size": size, "side": side}
        ],
    }


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    ensure_table(c)
    c.commit()
    yield c
    c.close()


def rows(conn):
    return conn.execute(
        "SELECT t.token_id, r.observed_ms, r.best_bid, r.best_bid_size, "
        "r.best_ask, r.best_ask_size FROM market_tob_transitions r "
        "JOIN market_tob_tokens t USING(token_ref) ORDER BY r.observed_ms, r.seq"
    ).fetchall()


def flush(rec, conn):
    n = rec.write_pending(conn)
    conn.commit()
    rec.acknowledge()
    return n


def test_transition_writes_one_row_in_fixed_point(conn):
    rec = TobRecorder()
    rec.observe(BOOK, received_ms=1)
    assert flush(rec, conn) == 1
    assert rows(conn) == [(T, 1789178400000, 4000, 1000, 4300, 700)]


def test_unchanged_top_writes_nothing(conn):
    rec = TobRecorder()
    rec.observe(BOOK, received_ms=1)
    rec.observe(delta("0.39", "9", "BUY"), received_ms=2)  # below the touch
    rec.observe(delta("0.50", "3", "SELL"), received_ms=3)  # behind the ask
    rec.observe(BOOK, received_ms=4)  # identical re-snapshot
    assert rec.pending == 1


def test_size_only_change_is_a_transition_and_removal_promotes_next_level(conn):
    rec = TobRecorder()
    rec.observe(BOOK, received_ms=1)
    rec.observe(delta("0.40", "4", "BUY"), received_ms=2)
    rec.observe(delta("0.40", "0", "BUY", ts="1789178402000"), received_ms=3)
    flush(rec, conn)
    assert [r[2:4] for r in rows(conn)] == [(4000, 1000), (4000, 400), (3900, 500)]


def test_empty_side_is_null_and_delta_without_snapshot_is_ignored(conn):
    rec = TobRecorder()
    rec.observe(delta("0.40", "4", "BUY"), received_ms=1)
    assert rec.pending == 0
    rec.observe({**BOOK, "asks": []}, received_ms=2)
    flush(rec, conn)
    assert rows(conn)[0][4:] == (None, None)


def test_batches_ack_only_after_commit_and_failure_keeps_rows(conn):
    rec = TobRecorder()
    rec.observe(BOOK, received_ms=1)
    for i in range(2, 12):
        rec.observe(delta("0.40", str(i), "BUY"), received_ms=i)
    assert rec.pending == 11
    rec.write_pending(conn, batch=4)
    conn.rollback()  # failed commit: nothing acknowledged
    assert rec.pending == 11 and rows(conn) == []
    assert flush(rec, conn) == 11
    assert rec.pending == 0 and len(rows(conn)) == 11
    assert conn.execute("SELECT count(*) FROM market_tob_tokens").fetchone()[0] == 1


def test_overflow_drops_oldest_and_counts(conn):
    rec = TobRecorder(max_pending=2)
    rec.observe(BOOK, received_ms=1)
    for i in (5, 6, 7):
        rec.observe(delta("0.40", str(i), "BUY"), received_ms=i)
    assert rec.pending == 2 and rec.dropped == 2


def test_retention_head_deletes_only_expired_rows(conn):
    rec = TobRecorder()
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    old = int((now.timestamp() - (RETENTION_DAYS + 1) * 86400) * 1000)
    new = int((now.timestamp() - 86400) * 1000)
    rec.observe({**BOOK, "timestamp": str(old)}, received_ms=1)
    rec.observe(delta("0.40", "4", "BUY", ts=str(new)), received_ms=2)
    flush(rec, conn)
    assert rec.trim_expired(conn, now=now) == 1
    assert [r[1] for r in rows(conn)] == [new]


def test_service_flush_writes_batch_in_one_commit(conn):
    ingestor = MarketChannelIngestor(None, feasibility_conn=conn, active_token_ids={T})
    ingestor.tob.observe(BOOK, received_ms=1)
    ingestor.tob.observe(delta("0.41", "2", "BUY"), received_ms=2)
    service = MarketChannelOnlineService(ingestor=ingestor, fetch_orderbook=lambda _: {})
    commits = []

    def commit():
        commits.append(1)
        conn.commit()

    async def drain():
        done = asyncio.Event()
        done.set()
        await service._flush_tob_forever(
            connection_done=done,
            write_gate=nullcontext(),
            commit=commit,
            rollback=conn.rollback,
            logger=None,
        )

    asyncio.run(drain())
    assert len(rows(conn)) == 2 and commits == [1] and not ingestor.tob.pending
