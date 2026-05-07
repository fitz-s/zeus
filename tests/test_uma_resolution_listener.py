# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: architecture/script_manifest.yaml backfill_uma_resolution_2026.py
#                  docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A5
"""Unit tests for src/state/uma_resolution_listener.py.

Tests the INSERT path via a fake RPC client and in-memory SQLite, asserting
that parse_settle_event + record_resolution correctly write uma_resolution rows.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.uma_resolution_listener import (
    ResolvedMarket,
    UmaRpcClient,
    init_uma_resolution_schema,
    lookup_resolution,
    parse_settle_event,
    poll_uma_resolutions,
    record_resolution,
)


# ---------------------------------------------------------------------------
# Fake RPC client for tests
# ---------------------------------------------------------------------------


class _FakeRpcClient(UmaRpcClient):
    """Returns a fixed list of synthetic log entries."""

    def __init__(self, logs: list[dict]) -> None:
        self._logs = logs

    def get_logs(self, *, contract_address, topic0, condition_ids, from_block, to_block=None):
        return list(self._logs)


def _make_log(
    *,
    condition_id: str = "0xabc123",
    tx_hash: str = "0xdeadbeef",
    block_number: int = 1_000_000,
    block_timestamp: int = 1_700_000_000,
    resolved_value_hex: str = "0000000000000000000000000000000000000000000000000000000000000001",
) -> dict:
    """Build a synthetic eth_getLogs entry."""
    padded_cond = condition_id.replace("0x", "").zfill(64)
    return {
        "address": "0x5945Bae9c5a6b2a6F5f9b06e9Ee6E0bD3aC3df57",
        "topics": [
            "0x0000000000000000000000000000000000000000000000000000000000000000",
            f"0x{padded_cond}",
        ],
        "data": f"0x{resolved_value_hex}",
        "blockNumber": hex(block_number),
        "transactionHash": tx_hash,
        "blockTimestamp": block_timestamp,
    }


@pytest.fixture()
def mem_conn():
    """In-memory SQLite connection with uma_resolution schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_uma_resolution_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# parse_settle_event tests
# ---------------------------------------------------------------------------


def test_parse_settle_event_basic():
    log = _make_log(
        condition_id="0xabc123",
        tx_hash="0xdeadbeef",
        block_number=999,
        block_timestamp=1_700_000_000,
        resolved_value_hex="0000000000000000000000000000000000000000000000000000000000000001",
    )
    rm = parse_settle_event(log)
    assert rm.condition_id == "0x" + "abc123".zfill(64)
    assert rm.tx_hash == "0xdeadbeef"
    assert rm.block_number == 999
    assert rm.resolved_value == 1
    assert rm.resolved_at_utc == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_parse_settle_event_missing_tx_raises():
    log = _make_log()
    log.pop("transactionHash")
    with pytest.raises(ValueError, match="transactionHash missing"):
        parse_settle_event(log)


def test_parse_settle_event_missing_topics_raises():
    log = _make_log()
    log["topics"] = ["0xsig"]  # only 1 topic — no condition_id
    with pytest.raises(ValueError, match="at least 2 topics"):
        parse_settle_event(log)


def test_parse_settle_event_hex_block_timestamp():
    log = _make_log(block_timestamp=1_700_000_000)
    log["blockTimestamp"] = hex(1_700_000_000)
    rm = parse_settle_event(log)
    assert rm.resolved_at_utc == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# record_resolution + lookup_resolution tests
# ---------------------------------------------------------------------------


def test_record_resolution_writes_row(mem_conn):
    rm = ResolvedMarket(
        condition_id="0xabc",
        resolved_value=1,
        tx_hash="0xtx1",
        block_number=100,
        resolved_at_utc=datetime(2026, 3, 1, tzinfo=timezone.utc),
        raw_log={"test": True},
    )
    record_resolution(mem_conn, rm)
    mem_conn.commit()

    row = mem_conn.execute(
        "SELECT condition_id, tx_hash, resolved_value FROM uma_resolution"
    ).fetchone()
    assert row is not None
    assert row["condition_id"] == "0xabc"
    assert row["tx_hash"] == "0xtx1"
    assert row["resolved_value"] == 1


def test_record_resolution_idempotent(mem_conn):
    """INSERT OR IGNORE: inserting the same (condition_id, tx_hash) twice is a no-op."""
    rm = ResolvedMarket(
        condition_id="0xabc",
        resolved_value=1,
        tx_hash="0xtx1",
        block_number=100,
        resolved_at_utc=datetime(2026, 3, 1, tzinfo=timezone.utc),
        raw_log={},
    )
    record_resolution(mem_conn, rm)
    record_resolution(mem_conn, rm)
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM uma_resolution").fetchone()[0]
    assert count == 1


def test_lookup_resolution_returns_none_when_missing(mem_conn):
    result = lookup_resolution(mem_conn, "0xnothere")
    assert result is None


def test_lookup_resolution_returns_most_recent(mem_conn):
    for block, tx in [(100, "0xtx1"), (200, "0xtx2")]:
        rm = ResolvedMarket(
            condition_id="0xabc",
            resolved_value=block,
            tx_hash=tx,
            block_number=block,
            resolved_at_utc=datetime(2026, 3, 1, tzinfo=timezone.utc),
            raw_log={},
        )
        record_resolution(mem_conn, rm)
    mem_conn.commit()

    result = lookup_resolution(mem_conn, "0xabc")
    assert result is not None
    assert result.block_number == 200  # most recent by block_number DESC


# ---------------------------------------------------------------------------
# poll_uma_resolutions tests
# ---------------------------------------------------------------------------


def test_poll_with_none_rpc_client_returns_empty(mem_conn):
    result = poll_uma_resolutions(
        condition_ids=["0xabc"],
        contract_address="0xcontract",
        rpc_client=None,
        conn=mem_conn,
    )
    assert result == []
    count = mem_conn.execute("SELECT COUNT(*) FROM uma_resolution").fetchone()[0]
    assert count == 0


def test_poll_with_fake_client_writes_row(mem_conn):
    log = _make_log(
        condition_id="0xfeed",
        tx_hash="0xfeedtx",
        block_number=500,
        block_timestamp=1_710_000_000,
        resolved_value_hex="0000000000000000000000000000000000000000000000000000000000000001",
    )
    client = _FakeRpcClient([log])

    resolutions = poll_uma_resolutions(
        condition_ids=["0x" + "feed".zfill(64)],
        contract_address="0xcontract",
        rpc_client=client,
        conn=mem_conn,
        from_block=0,
    )
    mem_conn.commit()

    assert len(resolutions) == 1
    assert resolutions[0].tx_hash == "0xfeedtx"
    assert resolutions[0].resolved_value == 1

    count = mem_conn.execute("SELECT COUNT(*) FROM uma_resolution").fetchone()[0]
    assert count == 1


def test_poll_skips_malformed_log(mem_conn):
    """A log with missing transactionHash is skipped; other logs still written."""
    good_log = _make_log(condition_id="0xgood", tx_hash="0xgoodtx", block_number=1)
    bad_log = _make_log(condition_id="0xbad", tx_hash="0xbadtx", block_number=2)
    bad_log.pop("transactionHash")

    client = _FakeRpcClient([good_log, bad_log])
    resolutions = poll_uma_resolutions(
        condition_ids=["0xgood", "0xbad"],
        contract_address="0xcontract",
        rpc_client=client,
        conn=mem_conn,
    )
    mem_conn.commit()

    # Only the good log should be written
    assert len(resolutions) == 1
    assert resolutions[0].tx_hash == "0xgoodtx"


def test_poll_empty_condition_ids(mem_conn):
    client = _FakeRpcClient([_make_log()])
    result = poll_uma_resolutions(
        condition_ids=[],
        contract_address="0xcontract",
        rpc_client=client,
        conn=mem_conn,
    )
    assert result == []
