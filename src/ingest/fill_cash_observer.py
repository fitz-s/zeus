"""Finalized transaction cash evidence for the existing fill synchronizer.

RPC collection has no writer connection; persistence never performs network I/O.
Missing evidence is retried by a bounded sweep, independently of CLOB progress.
"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from src.venue.polymarket_v2_adapter import (
    POLYGON_EXCHANGE_V2_ADDRESS,
    POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS,
    _json_rpc_batch_call_hard_deadline,
)

SOURCE = "polygon_finalized_fill_cash_v1"
EXCHANGES = (POLYGON_EXCHANGE_V2_ADDRESS.lower(), POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS.lower())


def select_cash_batch(conn: sqlite3.Connection, *, limit: int, wallet: str) -> tuple[list[dict], str]:
    """Freeze each sweep's ceiling so a growing tail cannot starve old failures."""
    if type(limit) is not int or limit < 1:
        raise ValueError("fill cash batch size must be a positive integer")
    row = conn.execute("SELECT cursor FROM fill_sync_watermarks WHERE source=?", (SOURCE,)).fetchone()
    cursor = json.loads(row[0]) if row and row[0] else {"after": 0, "ceiling": 0}
    after, ceiling = cursor["after"], cursor["ceiling"]
    if type(after) is not int or type(ceiling) is not int or not 0 <= after <= ceiling:
        raise ValueError("invalid fill cash sweep cursor")
    if after == ceiling:
        ceiling = conn.execute("SELECT COALESCE(MAX(trade_fact_id),0) FROM venue_trade_facts").fetchone()[0]
        after = 0
    recent_limit = limit // 2
    recent = conn.execute("""
        SELECT trade_fact_id, tx_hash FROM venue_trade_facts
        WHERE state IN ('MATCHED','MINED','CONFIRMED') AND tx_hash IS NOT NULL
        ORDER BY trade_fact_id DESC LIMIT ?
    """, (recent_limit,))
    recent_rows = [dict(zip((c[0] for c in recent.description), row)) for row in recent.fetchall()]
    sweep_limit = limit - len(recent_rows)
    result = conn.execute("""
        SELECT trade_fact_id, tx_hash FROM venue_trade_facts
        WHERE trade_fact_id > ? AND trade_fact_id <= ?
          AND state IN ('MATCHED','MINED','CONFIRMED') AND tx_hash IS NOT NULL
        ORDER BY trade_fact_id LIMIT ?
    """, (after, ceiling, sweep_limit))
    rows = [dict(zip((c[0] for c in result.description), row)) for row in result.fetchall()]
    next_after = rows[-1]["trade_fact_id"] if len(rows) == sweep_limit else ceiling
    combined = {row["trade_fact_id"]: row for row in recent_rows + rows}
    pending = [row for row in combined.values() if not conn.execute("""
        SELECT 1 FROM venue_fill_cash_facts
        WHERE chain_id=137 AND wallet=? AND tx_hash=? AND status='PROVEN' LIMIT 1
    """, (wallet.lower(), str(row["tx_hash"]).lower())).fetchone()]
    return pending, json.dumps({"after": next_after, "ceiling": ceiling}, sort_keys=True)


def collect_cash_proofs(*, tx_hashes: list[str], wallet: str, rpc_url: str,
                        budget_seconds: float, rpc_batch=None) -> list[dict[str, Any]]:
    """Capture receipts and historical asset identity under one total RPC budget."""
    from eth_utils import keccak
    from src.venue.fill_cash_proof import decode_fill_cash_proof

    if isinstance(budget_seconds, bool) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("fill cash RPC budget must be finite and positive")
    txs = sorted(set(str(value).lower() for value in tx_hashes))
    if not txs:
        return []
    batch = rpc_batch or _json_rpc_batch_call_hard_deadline
    deadline = time.monotonic() + budget_seconds
    evidence: dict[str, Any] = {}
    error = None

    def call(calls):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("fill cash RPC deadline")
        result = batch(rpc_url, calls, timeout_seconds=remaining)
        if not isinstance(result, list) or len(result) != len(calls):
            raise ValueError("partial fill cash RPC batch")
        return result

    try:
        initial = call([("eth_chainId", []), ("eth_getBlockByNumber", ["finalized", False])]
                       + [("eth_getTransactionReceipt", [tx]) for tx in txs])
        evidence["chain_id"] = int(initial[0], 16)
        if evidence["chain_id"] != 137:
            raise ValueError("unexpected RPC chain identity")
        evidence["finalized_header"] = initial[1]
        evidence["receipts"] = dict(zip(txs, initial[2:]))
        blocks = sorted({r["blockNumber"] for r in initial[2:] if isinstance(r, dict) and r.get("blockNumber")})
        evidence["headers"] = dict(zip(blocks, call([("eth_getBlockByNumber", [b, False]) for b in blocks])))
        asset_keys = [(b, ex) for b in blocks for ex in EXCHANGES]
        selector = "0x" + keccak(text="getCollateral()")[:4].hex()
        asset_calls = [("eth_call", [{"to": ex, "data": selector},
                        {"blockHash": evidence["headers"][b]["hash"], "requireCanonical": True}])
                       for b, ex in asset_keys]
        addresses = call(asset_calls)
        assets = {}
        for key, word in zip(asset_keys, addresses):
            if (not isinstance(word, str) or len(word) != 66 or not word.startswith("0x")
                    or int(word[2:26], 16) != 0 or int(word, 16) == 0):
                raise ValueError("invalid historical collateral address")
            assets[key] = {"address": "0x" + word[-40:].lower()}
        decimal_calls = [("eth_call", [{"to": assets[(b, ex)]["address"], "data": "0x313ce567"},
                          {"blockHash": evidence["headers"][b]["hash"], "requireCanonical": True}])
                         for b, ex in asset_keys]
        decimals = call(decimal_calls)
        for key, word in zip(asset_keys, decimals):
            if not isinstance(word, str) or len(word) != 66 or not word.startswith("0x"):
                raise ValueError("invalid historical collateral decimals")
            value = int(word, 16)
            if not 0 <= value <= 255:
                raise ValueError("invalid historical collateral decimals")
            assets[key]["decimals"] = value
        evidence["assets"] = {b: {ex: assets[(b, ex)] for ex in EXCHANGES} for b in blocks}
        evidence["headers_after"] = dict(zip(blocks, call([("eth_getBlockByNumber", [b, False]) for b in blocks])))
    except Exception as exc:
        # Never store an endpoint, transport exception message or credential.
        error = "RPC_EVIDENCE_UNAVAILABLE:" + type(exc).__name__
    observed = datetime.now(timezone.utc).isoformat()
    proofs = []
    for tx in txs:
        receipt = evidence.get("receipts", {}).get(tx)
        block = receipt.get("blockNumber") if isinstance(receipt, dict) else None
        proof = {"revision": SOURCE, "chain_id": 137, "rpc_chain_id": evidence.get("chain_id"), "tx_hash": tx,
                 "rpc_endpoint_hash": hashlib.sha256(rpc_url.encode()).hexdigest(),
                 "wallet": wallet.lower(), "receipt": receipt,
                 "header": evidence.get("headers", {}).get(block),
                 "finalized_header": evidence.get("finalized_header"),
                 "header_after": evidence.get("headers_after", {}).get(block),
                 "collateral_by_exchange": evidence.get("assets", {}).get(block, {})}
        if error:
            decoded = {"status": "UNKNOWN", "reason": error, "events": [],
                       "collateral_delta_atoms": None, "collateral": None, "decimals": None}
        else:
            decoded = decode_fill_cash_proof(**{key: proof[key] for key in (
                "chain_id", "tx_hash", "wallet", "receipt", "header", "finalized_header",
                "collateral_by_exchange", "header_after")})
        proofs.append(dict(proof, decoded=decoded, observed_at=observed))
    return proofs


def sync_cash_proofs(adapter) -> dict[str, Any]:
    from src.config import settings
    from src.ingest.fill_synchronizer import (
        FILL_SYNC_DB_WRITE_LEASE_DEADLINE_MS, FILL_SYNC_DB_WRITE_MAX_HOLD_MS, _advance_watermark,
    )
    from src.state.db import get_trade_connection_read_only
    from src.state.venue_command_repo import append_fill_cash_fact
    from src.state.write_coordinator import DBIdentity, WritePriority, default_runtime_write_coordinator

    config = settings["edli"]
    reader = get_trade_connection_read_only()
    try:
        rows, cursor = select_cash_batch(reader, limit=config["fill_cash_batch_size"], wallet=adapter.funder_address)
    finally:
        reader.close()
    proofs = collect_cash_proofs(tx_hashes=[row["tx_hash"] for row in rows],
        wallet=adapter.funder_address, rpc_url=config["fill_cash_rpc_url"],
        budget_seconds=config["fill_cash_rpc_budget_seconds"])
    observed = datetime.now(timezone.utc).isoformat()
    coordinator = default_runtime_write_coordinator()
    with coordinator.transaction((DBIdentity.TRADE,), owner="fill_cash_observer",
            write_class="live", priority=WritePriority.RECOVERY_CRITICAL,
            deadline_ms=FILL_SYNC_DB_WRITE_LEASE_DEADLINE_MS,
            max_hold_ms=FILL_SYNC_DB_WRITE_MAX_HOLD_MS) as tx:
        for proof in proofs:
            append_fill_cash_fact(tx.connection, proof=proof)
        _advance_watermark(tx.connection, source=SOURCE, watermark_ts=observed,
            cursor=cursor, updated_at=observed, coverage_note="bounded finalized receipt sweep; unknowns retry")
    return {"attempted": len(proofs), "proven": sum(p["decoded"]["status"] == "PROVEN" for p in proofs),
            "unknown": sum(p["decoded"]["status"] == "UNKNOWN" for p in proofs)}
