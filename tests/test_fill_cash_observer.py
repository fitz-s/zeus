# Created: 2026-09-10
# Last reused/audited: 2026-09-10
"""Bounded receipt collection, fair drainage and atomic cash-proof persistence."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

import pytest

from src.ingest.fill_cash_observer import (
    SOURCE, _advance_cash_cursor, collect_cash_proofs, select_cash_batch,
)
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


def attempted_proofs(rows):
    class AttemptedProof(dict):
        pass

    proofs = []
    for row in rows:
        proof = AttemptedProof(tx_hash=row["tx_hash"])
        proof.receipt_requested = True
        proofs.append(proof)
    return proofs


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
            assert state["after"] == iteration * 2
            save_cursor(conn, _advance_cash_cursor(cursor, attempted_proofs(rows)))
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
        assert json.loads(cursor) == {
            "after": 0, "ceiling": 100,
            "history": [{"trade_fact_id": 1, "tx_hash": f"0x{1:064x}"},
                        {"trade_fact_id": 2, "tx_hash": f"0x{2:064x}"}],
            "history_exhausted": False, "recent_first": True,
        }
        assert json.loads(_advance_cash_cursor(cursor, attempted_proofs(rows))) == {
            "after": 2, "ceiling": 100, "recent_first": False,
        }
    finally:
        conn.close()


def test_proven_recent_rows_do_not_consume_pending_transaction_slots():
    from src.state.venue_command_repo import append_fill_cash_fact
    from src.venue.fill_cash_proof import decode_fill_cash_proof
    from tests.test_fill_cash_proof import (
        COLLATERAL, EXCHANGE, TX, WALLET, _buy_logs, _valid_receipt,
    )

    conn = database()
    try:
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(i, f"0x{i:064x}") for i in range(1, 9)] + [(9, TX), (10, TX)])
        receipt, header, finalized, after = _valid_receipt(_buy_logs())
        proof = dict(chain_id=137, tx_hash=TX, wallet=WALLET, receipt=receipt,
                     header=header, finalized_header=finalized, header_after=after,
                     collateral_by_exchange={EXCHANGE: {"address": COLLATERAL, "decimals": 6}})
        proof["decoded"] = decode_fill_cash_proof(**proof)
        assert proof["decoded"]["status"] == "PROVEN"
        proof.update(rpc_chain_id=137, observed_at="2026-09-10T00:00:00Z")
        append_fill_cash_fact(conn, proof=proof)
        rows, cursor = select_cash_batch(conn, limit=4, wallet=WALLET.upper())
        assert {row["trade_fact_id"] for row in rows} == {1, 2, 7, 8}
        assert json.loads(_advance_cash_cursor(cursor, attempted_proofs(rows))) == {
            "after": 2, "ceiling": 10, "recent_first": False,
        }
        other_rows, _ = select_cash_batch(conn, limit=4, wallet="0x" + "12" * 20)
        assert TX in {row["tx_hash"] for row in other_rows}
    finally:
        conn.close()


def test_transaction_refreshes_share_one_batch_slot_and_normalized_identity():
    conn = database()
    try:
        tx = "0x" + "ab" * 32
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(1, "old"), (2, "middle"), (3, "new"), (4, tx), (5, tx.upper())])
        rows, cursor = select_cash_batch(conn, limit=4, wallet="wallet")
        assert [row["tx_hash"] for row in rows] == [tx, "new", "old", "middle"]
        assert json.loads(_advance_cash_cursor(cursor, attempted_proofs(rows))) == {
            "after": 2, "ceiling": 5, "recent_first": False,
        }
    finally:
        conn.close()


def test_history_cursor_does_not_jump_over_transactions_between_refreshes():
    conn = database()
    try:
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(1, "repeated"), (2, "middle"), (100, "repeated")])
        first, cursor = select_cash_batch(conn, limit=1, wallet="wallet")
        assert first == [{"trade_fact_id": 100, "tx_hash": "repeated"}]
        assert json.loads(_advance_cash_cursor(cursor, attempted_proofs(first))) == {
            "after": 0, "ceiling": 100, "recent_first": False,
        }
        save_cursor(conn, _advance_cash_cursor(cursor, attempted_proofs(first)))
        second, cursor = select_cash_batch(conn, limit=1, wallet="wallet")
        assert second == [{"trade_fact_id": 1, "tx_hash": "repeated"}]
        save_cursor(conn, _advance_cash_cursor(cursor, attempted_proofs(second)))
        third, cursor = select_cash_batch(conn, limit=1, wallet="wallet")
        assert third == [{"trade_fact_id": 100, "tx_hash": "repeated"}]
        save_cursor(conn, _advance_cash_cursor(cursor, attempted_proofs(third)))
        fourth, cursor = select_cash_batch(conn, limit=1, wallet="wallet")
        assert fourth == [{"trade_fact_id": 2, "tx_hash": "middle"}]
        assert json.loads(_advance_cash_cursor(cursor, attempted_proofs(fourth)))["after"] == 2
    finally:
        conn.close()


def test_recent_and_history_overlap_does_not_duplicate_rpc_work():
    conn = database()
    try:
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(1, "a"), (2, "b"), (3, "a")])
        rows, cursor = select_cash_batch(conn, limit=4, wallet="wallet")
        assert len(rows) == len({row["tx_hash"] for row in rows}) == 2
        assert json.loads(_advance_cash_cursor(cursor, attempted_proofs(rows))) == {
            "after": 3, "ceiling": 3, "recent_first": False,
        }
    finally:
        conn.close()


@pytest.mark.parametrize("rpc_workers", [1, 2])
@pytest.mark.parametrize("rpc_batch_items", [1, 2, 3])
def test_actual_receipt_attempt_frontier_rotates_past_slow_recent_prefix(
        monkeypatch, rpc_workers, rpc_batch_items):
    """A deadline-spent recent lane cannot persistently skip frozen history."""
    from src.ingest import fill_cash_observer as observer

    conn = database()
    try:
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(i, f"0x{i:064x}") for i in range(1, 17)])
        save_cursor(conn, '{"after":0,"ceiling":16,"recent_first":true}')
        now, phase, requested = [0.0], ["recent"], []
        monkeypatch.setattr(observer.time, "monotonic", lambda: now[0])

        def rpc(url, calls, **kwargs):
            method = calls[0][0]
            if method == "eth_chainId":
                return ["0x89" if item_method == "eth_chainId"
                        else {"number": "0x20", "hash": "0x" + "22" * 32}
                        for item_method, _ in calls]
            if method == "eth_getBlockByNumber":
                return [{"number": "0x20", "hash": "0x" + "22" * 32} for _ in calls]
            assert method == "eth_getTransactionReceipt"
            txs = [params[0] for _, params in calls]
            requested.extend(txs)
            if phase[0] == "recent" and any(int(tx, 16) >= 9 for tx in txs):
                now[0] = 13.0
                raise TimeoutError("slow recent receipt")
            if phase[0] == "history_failure":
                phase[0] = "history"
                raise RuntimeError("invalid historical receipt")
            return [None] * len(calls)

        rows, cursor = select_cash_batch(conn, limit=16, wallet="wallet")
        proofs = collect_cash_proofs(
            tx_hashes=[row["tx_hash"] for row in rows], wallet="wallet", rpc_url="unused",
            budget_seconds=12, rpc_batch=rpc, rpc_batch_items=rpc_batch_items,
            rpc_workers=rpc_workers,
        )
        assert requested and all(int(tx, 16) >= 9 for tx in requested)
        next_cursor = _advance_cash_cursor(cursor, proofs)
        assert json.loads(next_cursor) == {"after": 0, "ceiling": 16, "recent_first": False}

        save_cursor(conn, next_cursor)
        now[0], phase[0], requested[:] = 0.0, "history_failure", []
        rows, cursor = select_cash_batch(conn, limit=16, wallet="wallet")
        proofs = collect_cash_proofs(
            tx_hashes=[row["tx_hash"] for row in rows], wallet="wallet", rpc_url="unused",
            budget_seconds=12, rpc_batch=rpc, rpc_batch_items=rpc_batch_items,
            rpc_workers=rpc_workers,
        )
        assert requested and all(int(tx, 16) <= 8 for tx in requested[:rpc_batch_items])
        assert json.loads(_advance_cash_cursor(cursor, proofs))["after"] == 8
    finally:
        conn.close()


def test_old_cursor_migrates_to_attempt_frontier_without_skipping_history():
    conn = database()
    try:
        conn.executemany("INSERT INTO venue_trade_facts VALUES (?,?,'CONFIRMED')",
                         [(i, str(i)) for i in range(1, 5)])
        save_cursor(conn, '{"after":0,"ceiling":4}')
        rows, cursor = select_cash_batch(conn, limit=2, wallet="wallet")
        assert json.loads(cursor)["recent_first"] is True
        assert json.loads(_advance_cash_cursor(cursor, [])) == {
            "after": 0, "ceiling": 4, "recent_first": False,
        }
        assert json.loads(_advance_cash_cursor(cursor, attempted_proofs(rows))) == {
            "after": 1, "ceiling": 4, "recent_first": False,
        }
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
    assert proofs[0]["decoded"]["reason"] == "receipt_invalid"


def test_wrong_rpc_chain_is_retained_as_unknown_without_further_requests():
    calls_seen = []

    def rpc(url, calls, **kwargs):
        calls_seen.append(calls)
        return ["0x1", {}]

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
        assert "receipt_requested" not in json.loads(stored[0])
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


def test_rpc_collection_preserves_input_order_and_limits_chunks_and_workers(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()
    receipt_barrier = threading.Barrier(2)
    seen = []
    txs = [f"0x{i:064x}" for i in range(5)]

    def rpc(url, calls, **kwargs):
        nonlocal active, peak
        assert 0 < kwargs["timeout_seconds"] <= 5
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(tuple(method for method, _ in calls))
        if calls[0][0] == "eth_getTransactionReceipt":
            receipt_barrier.wait(timeout=1)
        try:
            if calls[0][0] == "eth_chainId":
                return ["0x89", {"number": "0x20", "hash": "0x" + "22" * 32}]
            result = []
            for method, params in calls:
                if method == "eth_getTransactionReceipt":
                    result.append(None)
                elif method == "eth_getBlockByNumber":
                    result.append({"number": params[0], "hash": "0x" + "11" * 32})
                else:
                    result.append(None)
            return result
        finally:
            with lock:
                active -= 1

    proofs = collect_cash_proofs(
        tx_hashes=[txs[2], txs[0], txs[2], txs[1], txs[3], txs[4]],
        wallet="0x" + "12" * 20, rpc_url="unused", budget_seconds=5,
        rpc_batch=rpc, rpc_batch_items=3, rpc_workers=2,
    )
    assert [proof["tx_hash"] for proof in proofs] == [txs[2], txs[0], txs[1], txs[3], txs[4]]
    assert all(len(methods) <= 3 for methods in seen)
    assert peak <= 2


def test_control_failure_keeps_actual_rpc_chain_and_skips_receipts(monkeypatch):
    calls = []

    def rpc(url, batch, **kwargs):
        calls.append(batch)
        return ["0x1", {"number": "0x20", "hash": "0x" + "22" * 32}]

    proof = collect_cash_proofs(
        tx_hashes=["0x" + "ab" * 32], wallet="0x" + "12" * 20,
        rpc_url="unused", budget_seconds=5, rpc_batch=rpc,
    )[0]
    assert len(calls) == 1
    assert proof["chain_id"] == 137
    assert proof["rpc_chain_id"] == 1
    assert proof["decoded"]["status"] == "UNKNOWN"


def _cash_rpc_fixture(count=4):
    from copy import deepcopy
    from tests.test_fill_cash_proof import COLLATERAL, _buy_logs, _valid_receipt

    receipts, headers = {}, {}
    for i in range(count):
        tx, block_hash, block = f"0x{i + 101:064x}", f"0x{i + 201:064x}", hex(i + 16)
        receipt, header, _, _ = _valid_receipt(deepcopy(_buy_logs()))
        receipt.update(transactionHash=tx, blockHash=block_hash, blockNumber=block)
        for log in receipt["logs"]:
            log.update(transactionHash=tx, blockHash=block_hash, blockNumber=block)
        receipts[tx] = receipt
        headers[block] = dict(header, hash=block_hash, number=block)
    header_reads = {}

    def respond(method, params):
        if method == "eth_chainId":
            return "0x89"
        if method == "eth_getTransactionReceipt":
            return receipts[params[0]]
        if method == "eth_getBlockByNumber":
            if params[0] == "finalized":
                return {"hash": "0x" + "ee" * 32, "number": "0x100"}
            header_reads[params[0]] = header_reads.get(params[0], 0) + 1
            return headers[params[0]]
        assert method == "eth_call"
        assert params[1]["requireCanonical"] is True
        assert params[1]["blockHash"] in {h["hash"] for h in headers.values()}
        return (f"0x{6:064x}" if params[0]["data"] == "0x313ce567"
                else "0x" + "0" * 24 + COLLATERAL[2:])

    return receipts, headers, header_reads, respond


@pytest.mark.parametrize("failed_stage", ["receipt", "header", "asset", "decimals", "header_after"])
def test_failed_chunk_preserves_other_transaction_proven_cash(failed_stage):
    from tests.test_fill_cash_proof import WALLET

    receipts, _, header_reads, respond = _cash_rpc_fixture()
    txs = list(receipts)
    failed = False

    def rpc(url, calls, **kwargs):
        nonlocal failed
        assert len(calls) <= 3
        method, params = calls[0]
        stage = None
        if method == "eth_call":
            stage = "decimals" if params[0]["data"] == "0x313ce567" else "asset"
        elif method == "eth_getTransactionReceipt":
            stage = "receipt"
        if method == "eth_getBlockByNumber" and params[0] != "finalized":
            stage = "header_after" if header_reads.get(params[0], 0) else "header"
        if stage == failed_stage and not failed:
            failed = True
            raise TimeoutError("private endpoint token")
        return [respond(method, params) for method, params in calls]

    proofs = collect_cash_proofs(tx_hashes=txs, wallet=WALLET, rpc_url="unused",
                                budget_seconds=10, rpc_batch=rpc, rpc_workers=1)
    assert failed
    assert any(p["decoded"]["status"] == "UNKNOWN" for p in proofs)
    surviving = [p for p in proofs if p["decoded"]["status"] == "PROVEN"]
    assert surviving
    assert all(p["decoded"]["collateral_delta_atoms"] == -2_825_550 for p in surviving)
    assert "private endpoint" not in json.dumps(proofs)


def test_only_observed_exchange_is_queried_and_legacy_receipt_stays_unknown():
    from tests.test_fill_cash_proof import EXCHANGE, WALLET

    receipts, _, _, respond = _cash_rpc_fixture(2)
    txs = list(receipts)
    receipts[txs[0]]["logs"] = []
    asset_requests = []

    def rpc(url, calls, **kwargs):
        for method, params in calls:
            if method == "eth_call" and params[0]["data"] != "0x313ce567":
                asset_requests.append(params[0]["to"])
        return [respond(method, params) for method, params in calls]

    proofs = collect_cash_proofs(tx_hashes=txs, wallet=WALLET, rpc_url="unused",
                                budget_seconds=10, rpc_batch=rpc)
    assert asset_requests == [EXCHANGE]
    assert [p["decoded"]["status"] for p in proofs] == ["UNKNOWN", "PROVEN"]


def test_global_deadline_keeps_completed_proofs_and_stops_new_rpc(monkeypatch):
    from src.ingest import fill_cash_observer as observer
    from tests.test_fill_cash_proof import WALLET

    receipts, _, header_reads, respond = _cash_rpc_fixture(4)
    now = [0.0]
    monkeypatch.setattr(observer.time, "monotonic", lambda: now[0])
    requests_after_deadline = []

    def rpc(url, calls, **kwargs):
        if now[0] >= 10:
            requests_after_deadline.append(calls)
        assert kwargs["timeout_seconds"] == 10 - now[0]
        is_after = calls[0][0] == "eth_getBlockByNumber" and header_reads.get(calls[0][1][0], 0)
        result = [respond(method, params) for method, params in calls]
        if is_after:
            now[0] = 11.0
        return result

    proofs = collect_cash_proofs(tx_hashes=list(receipts), wallet=WALLET, rpc_url="unused",
                                budget_seconds=10, rpc_batch=rpc, rpc_workers=1)
    assert not requests_after_deadline
    assert [p["decoded"]["status"] for p in proofs] == ["PROVEN"] * 3 + ["UNKNOWN"]


@pytest.mark.parametrize("batch_items", [1, 2, 3])
def test_configured_wire_batch_limit_includes_control(batch_items):
    from tests.test_fill_cash_proof import WALLET

    receipts, _, _, respond = _cash_rpc_fixture(1)

    def rpc(url, calls, **kwargs):
        assert len(calls) <= batch_items
        return [respond(method, params) for method, params in calls]

    proofs = collect_cash_proofs(tx_hashes=list(receipts), wallet=WALLET, rpc_url="unused",
                                budget_seconds=10, rpc_batch=rpc, rpc_batch_items=batch_items)
    assert proofs[0]["decoded"]["status"] == "PROVEN"
