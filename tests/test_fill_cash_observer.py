# Created: 2026-09-10
# Last reused/audited: 2026-09-10
"""Bounded receipt collection, fair drainage and atomic cash-proof persistence."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from src.ingest.fill_cash_observer import SOURCE, collect_cash_proofs, select_cash_batch
from src.state.schema.fill_sync_watermarks_schema import ensure_table as ensure_watermark
from src.state.schema.venue_fill_cash_facts_schema import ensure_table


def database():
    conn = sqlite3.connect(":memory:")
    ensure_watermark(conn)
    ensure_table(conn)
    conn.execute("CREATE TABLE venue_trade_facts (trade_fact_id INTEGER PRIMARY KEY,tx_hash TEXT,state TEXT)")
    return conn


def save_cursor(conn, cursor):
    conn.execute("INSERT OR REPLACE INTO fill_sync_watermarks VALUES (?,?,?,?,?)",
                 (SOURCE, "2026-09-10T00:00:00Z", cursor, "2026-09-10T00:00:00Z", "test"))


def test_missing_cash_table_requires_normal_synchronizer_bootstrap():
    from src.ingest import fill_synchronizer

    conn = sqlite3.connect(":memory:")
    try:
        fill_synchronizer.ensure_watermark_table(conn)
        fill_synchronizer.ensure_wallet_fill_observations_table(conn)
        assert not fill_synchronizer._fill_sync_schema_ready(conn)
        fill_synchronizer.ensure_fill_cash_facts_table(conn)
        assert fill_synchronizer._fill_sync_schema_ready(conn)
    finally:
        conn.close()


def test_sweep_ceiling_survives_new_tail_and_wraps():
    conn = database()
    try:
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(i, f"0x{i:064x}") for i in range(1, 9)])
        seen = set()
        for iteration in range(4):
            rows, cursor = select_cash_batch(conn, limit=4, wallet="0x" + "12" * 20)
            assert len(rows) <= 4
            seen.update(row["trade_fact_id"] for row in rows)
            state = json.loads(cursor)
            assert state["ceiling"] == 8
            assert state["after"] == (iteration + 1) * 2
            save_cursor(conn, cursor)
            conn.execute("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         (9 + iteration, f"0x{9 + iteration:064x}"))
        assert set(range(1, 9)) <= seen
        _, cursor = select_cash_batch(conn, limit=4, wallet="0x" + "12" * 20)
        assert json.loads(cursor)["ceiling"] == 12
    finally:
        conn.close()


def test_recent_fills_do_not_wait_for_historical_sweep():
    conn = database()
    try:
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(i, f"0x{i:064x}") for i in range(1, 101)])
        rows, cursor = select_cash_batch(conn, limit=4, wallet="0x" + "12" * 20)
        assert {r["trade_fact_id"] for r in rows} == {1, 2, 99, 100}
        assert json.loads(cursor) == {"after": 2, "ceiling": 100}
    finally:
        conn.close()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_batch_size(limit):
    conn = database()
    try:
        with pytest.raises(ValueError):
            select_cash_batch(conn, limit=limit, wallet="0x" + "12" * 20)
    finally:
        conn.close()


def test_rpc_failure_is_unknown_and_does_not_leak_endpoint():
    def rpc(url, calls, **kwargs):
        raise RuntimeError("secret endpoint token")

    proofs = collect_cash_proofs(tx_hashes=["0x" + "ab" * 32] * 2,
        wallet="0x" + "12" * 20, rpc_url="secret endpoint", budget_seconds=1, rpc_batch=rpc)
    assert len(proofs) == 1
    assert proofs[0]["decoded"]["status"] == "UNKNOWN"
    assert proofs[0]["decoded"]["collateral_delta_atoms"] is None
    assert "secret" not in json.dumps(proofs)
    assert datetime.fromisoformat(proofs[0]["observed_at"]).utcoffset() is not None


def test_partial_rpc_result_cannot_become_zero_cash_proof():
    proofs = collect_cash_proofs(tx_hashes=["0x" + "ab" * 32],
        wallet="0x" + "12" * 20, rpc_url="unused", budget_seconds=1,
        rpc_batch=lambda *args, **kwargs: [])
    assert proofs[0]["decoded"]["status"] == "UNKNOWN"
    assert proofs[0]["decoded"]["reason"] == "RPC_EVIDENCE_UNAVAILABLE:ValueError"


def test_wrong_rpc_chain_is_retained_as_unknown_without_further_requests():
    calls_seen = []

    def rpc(url, calls, **kwargs):
        calls_seen.append(calls)
        return ["0x1", {}, {}]

    proof = collect_cash_proofs(tx_hashes=["0x" + "ab" * 32],
        wallet="0x" + "12" * 20, rpc_url="unused", budget_seconds=1, rpc_batch=rpc)[0]
    assert len(calls_seen) == 1
    assert proof["chain_id"] == 137
    assert proof["rpc_chain_id"] == 1
    assert proof["decoded"]["status"] == "UNKNOWN"
    assert proof["decoded"]["collateral_delta_atoms"] is None


@pytest.mark.parametrize("chain_id", [1, True, 137.0, None])
def test_writer_rejects_wrong_request_chain_without_appending(chain_id):
    from src.state.venue_command_repo import append_fill_cash_fact

    conn = database()
    try:
        conn.execute("BEGIN")
        with pytest.raises(ValueError, match="identify Polygon"):
            append_fill_cash_fact(conn, proof={"chain_id": chain_id})
        assert conn.execute("SELECT COUNT(*) FROM venue_fill_cash_facts").fetchone()[0] == 0
    finally:
        conn.close()


def test_rpc_receipt_to_append_uses_historical_asset_and_exact_cash():
    from src.state.venue_command_repo import append_fill_cash_fact
    from tests.test_fill_cash_proof import COLLATERAL, TX, WALLET, _buy_logs, _valid_receipt

    receipt, header, finalized, _ = _valid_receipt(_buy_logs())
    calls_seen = []

    def rpc(url, calls, **kwargs):
        assert 0 < kwargs["timeout_seconds"] <= 10
        calls_seen.extend(calls)
        result = []
        for method, params in calls:
            if method == "eth_chainId":
                result.append("0x89")
            elif method == "eth_getTransactionReceipt":
                result.append(receipt)
            elif method == "eth_getBlockByNumber":
                result.append(finalized if params[0] == "finalized" else header)
            else:
                assert method == "eth_call"
                assert params[1] == {"blockHash": header["hash"], "requireCanonical": True}
                result.append("0x" + f"{6:064x}" if params[0]["data"] == "0x313ce567"
                              else "0x" + "0" * 24 + COLLATERAL[2:])
        return result

    proof = collect_cash_proofs(tx_hashes=[TX], wallet=WALLET, rpc_url="unused",
                                budget_seconds=10, rpc_batch=rpc)[0]
    assert proof["decoded"]["status"] == "PROVEN"
    assert proof["decoded"]["collateral_delta_atoms"] == -2_825_550
    assert all(method not in {"eth_sendRawTransaction", "eth_sendTransaction"} for method, _ in calls_seen)
    conn = database()
    try:
        conn.execute("BEGIN")
        first = append_fill_cash_fact(conn, proof=proof)
        assert append_fill_cash_fact(conn, proof=proof) == first
        conn.commit()
        row = conn.execute("SELECT status,proof_json FROM venue_fill_cash_facts").fetchone()
        assert row[0] == "PROVEN"
        assert json.loads(row[1])["receipt"] == receipt
    finally:
        conn.close()


def test_real_batch_transport_accepts_chain_and_pending_receipt(monkeypatch):
    from io import BytesIO
    from src.venue import polymarket_v2_adapter as adapter

    response = [{"jsonrpc": "2.0", "id": 2, "result": None},
                {"jsonrpc": "2.0", "id": 1, "result": "0x89"}]
    monkeypatch.setattr(adapter.urllib.request, "urlopen",
                        lambda *args, **kwargs: BytesIO(json.dumps(response).encode()))
    assert adapter._json_rpc_batch_call("https://unused.test", [
        ("eth_chainId", []), ("eth_getTransactionReceipt", ["0x" + "ab" * 32])]) == ["0x89", None]


@pytest.mark.parametrize("method,result", [("eth_chainId", True), ("eth_chainId", "0x"),
                                           ("eth_getTransactionReceipt", [])])
def test_real_batch_transport_rejects_invalid_new_read_shapes(monkeypatch, method, result):
    from io import BytesIO
    from src.venue import polymarket_v2_adapter as adapter

    response = [{"jsonrpc": "2.0", "id": 1, "result": result}]
    monkeypatch.setattr(adapter.urllib.request, "urlopen",
                        lambda *args, **kwargs: BytesIO(json.dumps(response).encode()))
    with pytest.raises(adapter.V2AdapterError):
        adapter._json_rpc_batch_call("https://unused.test", [(method, [])])


def test_batch_transport_rejects_financial_method_before_network(monkeypatch):
    from src.venue import polymarket_v2_adapter as adapter

    def forbidden(*args, **kwargs):
        raise AssertionError("financial RPC reached network")

    monkeypatch.setattr(adapter.urllib.request, "urlopen", forbidden)
    with pytest.raises(adapter.V2AdapterError, match="unsupported read method"):
        adapter._json_rpc_batch_call("https://unused.test", [("eth_sendRawTransaction", [])])


def test_append_idempotency_and_rollback_preserve_observation_and_cursor_together():
    from src.state.venue_command_repo import append_fill_cash_fact

    proof = collect_cash_proofs(tx_hashes=["0x" + "ab" * 32],
        wallet="0x" + "12" * 20, rpc_url="unused", budget_seconds=1,
        rpc_batch=lambda *args, **kwargs: [])
    conn = database()
    try:
        conn.commit()
        with pytest.raises(ValueError, match="caller transaction"):
            append_fill_cash_fact(conn, proof=proof[0])
        conn.execute("BEGIN")
        first = append_fill_cash_fact(conn, proof=proof[0])
        repeated = dict(proof[0], observed_at="2026-09-11T00:00:00+00:00")
        assert append_fill_cash_fact(conn, proof=repeated) == first
        save_cursor(conn, '{"after":1,"ceiling":1}')
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM venue_fill_cash_facts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fill_sync_watermarks").fetchone()[0] == 0
        conn.execute("BEGIN")
        append_fill_cash_fact(conn, proof=proof[0])
        conn.commit()
        stored = conn.execute("SELECT proof_json,observed_at FROM venue_fill_cash_facts").fetchone()
        assert "observed_at" not in json.loads(stored[0])
        assert stored[1] == proof[0]["observed_at"]
    finally:
        conn.close()


def test_cash_failure_preserves_completed_clob_synchronization(monkeypatch):
    from src.data.polymarket_client import PolymarketClient
    from src.ingest import fill_cash_observer, fill_synchronizer

    monkeypatch.setattr(PolymarketClient, "__init__", lambda self: None)
    monkeypatch.setattr(PolymarketClient, "_ensure_v2_adapter", lambda self: object())
    monkeypatch.setattr(fill_synchronizer, "_sync_fills_coordinated", lambda adapter: {"appended": 3})

    def failed(adapter):
        raise TimeoutError("private endpoint")

    monkeypatch.setattr(fill_cash_observer, "sync_cash_proofs", failed)
    result = fill_synchronizer.fill_synchronizer_cycle()
    assert result["appended"] == 3
    assert result["scheduler_failure_reason"] == "fill_cash_sync_failed"
    assert result["chain_cash"]["reason"] == "TimeoutError"
