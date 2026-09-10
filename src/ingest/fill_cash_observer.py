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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from src.venue.polymarket_v2_adapter import (
    POLYGON_EXCHANGE_V2_ADDRESS,
    POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS,
    _json_rpc_batch_call_hard_deadline,
)

SOURCE = "polygon_finalized_fill_cash_v1"
EXCHANGES = (POLYGON_EXCHANGE_V2_ADDRESS.lower(), POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS.lower())


class _CollectedCashProof(dict):
    """RPC-local attempt metadata must not become immutable chain evidence."""

    __slots__ = ("receipt_requested",)


def select_cash_batch(conn: sqlite3.Connection, *, limit: int, wallet: str) -> tuple[list[dict], str]:
    """Select bounded rotating recent/history lanes under one frozen ceiling."""
    if type(limit) is not int or limit < 1:
        raise ValueError("fill cash batch size must be a positive integer")
    row = conn.execute("SELECT cursor FROM fill_sync_watermarks WHERE source=?", (SOURCE,)).fetchone()
    cursor = json.loads(row[0]) if row and row[0] else {"after": 0, "ceiling": 0}
    after, ceiling = cursor.get("after"), cursor.get("ceiling")
    recent_first = cursor.get("recent_first", True)
    if type(after) is not int or type(ceiling) is not int or not 0 <= after <= ceiling:
        raise ValueError("invalid fill cash sweep cursor")
    if type(recent_first) is not bool:
        raise ValueError("invalid fill cash sweep cursor")
    if after == ceiling:
        ceiling = conn.execute("SELECT COALESCE(MAX(trade_fact_id),0) FROM venue_trade_facts").fetchone()[0]
        after = 0
    history_limit = limit // 2 + (1 if not recent_first and limit % 2 else 0)
    recent_limit = limit - history_limit
    pending_filter = """
        state IN ('MATCHED','MINED','CONFIRMED') AND tx_hash IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM venue_fill_cash_facts cash
            WHERE cash.chain_id=137 AND cash.wallet=?
              AND cash.tx_hash=LOWER(venue_trade_facts.tx_hash) AND cash.status='PROVEN'
        )
    """
    recent = conn.execute(f"""
        SELECT MAX(trade_fact_id) AS trade_fact_id, LOWER(tx_hash) AS tx_hash
        FROM venue_trade_facts WHERE {pending_filter}
        GROUP BY LOWER(tx_hash) ORDER BY trade_fact_id DESC LIMIT ?
    """, (wallet.lower(), recent_limit))
    recent_rows = [dict(zip((c[0] for c in recent.description), row)) for row in recent.fetchall()]
    # The lane queries intentionally overlap.  Deduplication happens only when
    # composing RPC work, while the exact historical lane remains available to
    # advance the cursor only after an actual receipt request.
    result = conn.execute(f"""
        SELECT MIN(trade_fact_id) AS trade_fact_id, LOWER(tx_hash) AS tx_hash
        FROM venue_trade_facts
        WHERE trade_fact_id > ? AND trade_fact_id <= ?
          AND {pending_filter}
        GROUP BY LOWER(tx_hash) ORDER BY trade_fact_id LIMIT ?
    """, (after, ceiling, wallet.lower(), history_limit + 1))
    history_candidates = [dict(zip((c[0] for c in result.description), row)) for row in result.fetchall()]
    history_rows = history_candidates[:history_limit]
    lanes = (recent_rows, history_rows) if recent_first else (history_rows, recent_rows)
    rows, selected_txs = [], set()
    for lane in lanes:
        for candidate in lane:
            if candidate["tx_hash"] not in selected_txs:
                selected_txs.add(candidate["tx_hash"])
                rows.append(candidate)
    plan = {
        "after": after,
        "ceiling": ceiling,
        "history": history_rows,
        "history_exhausted": len(history_candidates) <= history_limit,
        "recent_first": recent_first,
    }
    return rows, json.dumps(plan, sort_keys=True)


def _advance_cash_cursor(cursor: str, proofs: list[dict[str, Any]]) -> str:
    """Advance only through the contiguous historical receipt-attempt frontier."""
    plan = json.loads(cursor)
    after, ceiling = plan.get("after"), plan.get("ceiling")
    history = plan.get("history", [])
    if (type(after) is not int or type(ceiling) is not int or not 0 <= after <= ceiling
            or type(plan.get("recent_first")) is not bool or type(plan.get("history_exhausted")) is not bool
            or not isinstance(history, list)):
        raise ValueError("invalid fill cash sweep cursor")
    attempted = {proof.get("tx_hash") for proof in proofs if getattr(proof, "receipt_requested", False)}
    next_after = after
    for row in history:
        if not isinstance(row, dict) or type(row.get("trade_fact_id")) is not int or not isinstance(row.get("tx_hash"), str):
            raise ValueError("invalid fill cash sweep cursor")
        if row["tx_hash"] not in attempted:
            break
        next_after = row["trade_fact_id"]
    else:
        if plan["history_exhausted"]:
            next_after = ceiling
    return json.dumps({"after": next_after, "ceiling": ceiling,
                       "recent_first": not plan["recent_first"]}, sort_keys=True)


def collect_cash_proofs(*, tx_hashes: list[str], wallet: str, rpc_url: str,
                        budget_seconds: float, rpc_batch=None,
                        rpc_batch_items: int = 3, rpc_workers: int = 2) -> list[dict[str, Any]]:
    """Capture receipts and historical assets under one bounded RPC budget."""
    from eth_utils import keccak

    if isinstance(budget_seconds, bool) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("fill cash RPC budget must be finite and positive")
    if type(rpc_batch_items) is not int or not 1 <= rpc_batch_items <= 3:
        raise ValueError("fill cash RPC batch size must be an integer in [1, 3]")
    if type(rpc_workers) is not int or not 1 <= rpc_workers <= 2:
        raise ValueError("fill cash RPC workers must be an integer in [1, 2]")
    txs = list(dict.fromkeys(str(value).lower() for value in tx_hashes))
    if not txs:
        return []
    from src.venue.fill_cash_proof import decode_fill_cash_proof
    batch = rpc_batch or _json_rpc_batch_call_hard_deadline
    deadline = time.monotonic() + budget_seconds
    control_errors: list[str] = []

    def call(calls, *, before_rpc=None):
        if not calls:
            return []
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("rpc deadline")
        if before_rpc is not None:
            before_rpc()
        result = batch(rpc_url, calls, timeout_seconds=remaining)
        if not isinstance(result, list) or len(result) != len(calls):
            raise ValueError("partial response")
        return result

    def chunks(items):
        for start in range(0, len(items), rpc_batch_items):
            yield items[start:start + rpc_batch_items]

    rpc_chain_id = None
    finalized_header = None
    try:
        control = [value for part in chunks([
            ("eth_chainId", []), ("eth_getBlockByNumber", ["finalized", False])
        ]) for value in call(part)]
        if not isinstance(control[0], str) or not control[0].startswith("0x"):
            raise ValueError("control identity")
        rpc_chain_id = int(control[0], 16)
        if rpc_chain_id != 137 or not isinstance(control[1], dict):
            raise ValueError("control identity")
        finalized_header = control[1]
    except Exception as exc:  # noqa: BLE001 - safe type only
        control_errors.append(type(exc).__name__)

    def collect_group(txs):
        errors = list(control_errors)

        attempted_receipts: set[str] = set()

        def mapped_calls(calls_with_keys, *, receipt_attempts: bool = False):
            results = {}
            for calls, keys in calls_with_keys:
                try:
                    values = call(
                        calls,
                        before_rpc=(lambda keys=keys: attempted_receipts.update(keys)) if receipt_attempts else None,
                    )
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    continue
                results.update(zip(keys, values))
            return results

        receipt_results = mapped_calls([
            ([("eth_getTransactionReceipt", [tx]) for tx in part], part)
            for part in chunks(txs)
        ], receipt_attempts=True) if rpc_chain_id == 137 else {}
        receipts = {tx: receipt_results.get(tx) for tx in txs}
        blocks = list(dict.fromkeys(
            receipt.get("blockNumber") for receipt in receipts.values()
            if isinstance(receipt, dict) and isinstance(receipt.get("blockNumber"), str)
        ))
        header_results = mapped_calls([
            ([("eth_getBlockByNumber", [block, False]) for block in part], part)
            for part in chunks(blocks)
        ]) if rpc_chain_id == 137 else {}
        headers = {block: header_results.get(block) for block in blocks}
        known_keys = []
        for tx in txs:
            receipt = receipts.get(tx)
            if not isinstance(receipt, dict):
                continue
            block = receipt.get("blockNumber")
            if not isinstance(block, str):
                continue
            logs = receipt.get("logs")
            for log in logs if isinstance(logs, list) else []:
                exchange = str(log.get("address", "")).lower() if isinstance(log, dict) else ""
                if exchange in EXCHANGES and (block, exchange) not in known_keys:
                    known_keys.append((block, exchange))
        selector = "0x" + keccak(text="getCollateral()")[:4].hex()
        asset_results = mapped_calls([
            ([("eth_call", [{"to": exchange, "data": selector},
                             {"blockHash": headers[block]["hash"], "requireCanonical": True}])
              for block, exchange in part], part)
            for part in chunks([key for key in known_keys if isinstance(headers.get(key[0]), dict) and headers[key[0]].get("hash")])
        ]) if rpc_chain_id == 137 else {}
        assets: dict[tuple[str, str], dict[str, Any]] = {}
        for key, word in asset_results.items():
            if (isinstance(word, str) and len(word) == 66 and word.startswith("0x")
                    and all(c in "0123456789abcdefABCDEF" for c in word[2:])
                    and int(word[2:26], 16) == 0 and int(word, 16) != 0):
                assets[key] = {"address": "0x" + word[-40:].lower()}
        decimal_results = mapped_calls([
            ([("eth_call", [{"to": assets[key]["address"], "data": "0x313ce567"},
                             {"blockHash": headers[key[0]]["hash"], "requireCanonical": True}])
              for key in part], part)
            for part in chunks(list(assets))
        ]) if rpc_chain_id == 137 else {}
        for key, word in decimal_results.items():
            if (isinstance(word, str) and len(word) == 66 and word.startswith("0x")
                    and all(c in "0123456789abcdefABCDEF" for c in word[2:])):
                value = int(word, 16)
                if 0 <= value <= 255:
                    assets[key]["decimals"] = value
        header_after_results = mapped_calls([
            ([("eth_getBlockByNumber", [block, False]) for block in part], part)
            for part in chunks([block for block in blocks if isinstance(headers.get(block), dict) and headers[block].get("hash")])
        ]) if rpc_chain_id == 137 else {}
        observed = datetime.now(timezone.utc).isoformat()
        proofs = []
        for tx in txs:
            receipt = receipts.get(tx)
            block = receipt.get("blockNumber") if isinstance(receipt, dict) else None
            block = block if isinstance(block, str) else None
            proof = {"revision": SOURCE, "chain_id": 137, "rpc_chain_id": rpc_chain_id, "tx_hash": tx,
                     "rpc_endpoint_hash": hashlib.sha256(rpc_url.encode()).hexdigest(),
                     "wallet": wallet.lower(), "receipt": receipt,
                     "header": headers.get(block), "finalized_header": finalized_header,
                     "header_after": header_after_results.get(block),
                     "collateral_by_exchange": {ex: assets[(block, ex)] for b, ex in assets if b == block},
                     "rpc_errors": sorted(set(errors))}
            try:
                decoded = decode_fill_cash_proof(**{key: proof[key] for key in (
                    "chain_id", "tx_hash", "wallet", "receipt", "header", "finalized_header",
                    "collateral_by_exchange", "header_after")})
            except Exception as exc:  # noqa: BLE001 - safe type only
                decoded = {"status": "UNKNOWN", "reason": "DECODER_" + type(exc).__name__, "events": [],
                           "collateral_delta_atoms": None, "collateral": None, "decimals": None}
            collected = _CollectedCashProof(proof, decoded=decoded, observed_at=observed)
            collected.receipt_requested = tx in attempted_receipts
            proofs.append(collected)
        return proofs

    # Finish each bounded transaction group before spending its worker on the
    # next group. A slow historical tail cannot consume every header recheck.
    proofs_by_tx = {}
    with ThreadPoolExecutor(max_workers=rpc_workers) as executor:
        futures = [executor.submit(collect_group, group) for group in chunks(txs)]
        for future in as_completed(futures):
            for proof in future.result():
                proofs_by_tx[proof["tx_hash"]] = proof
    return [proofs_by_tx[tx] for tx in txs]


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
        budget_seconds=config["fill_cash_rpc_budget_seconds"],
        rpc_batch_items=config["fill_cash_rpc_batch_items"],
        rpc_workers=config["fill_cash_rpc_workers"])
    cursor = _advance_cash_cursor(cursor, proofs)
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
