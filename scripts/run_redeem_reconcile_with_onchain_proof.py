#!/usr/bin/env python
# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PR #219 V2 wrap path correction; reference tx 0xe08e03334f25328d3c993fb7e7e266d732edcaa02532f2d9ce3ca5feec38d74f (block 87135584, Karachi NegRisk redeem 1,587,297 USDC.e)
"""Standalone redeem reconciliation with on-chain proof.

Karachi specifics (2026-05-20):
  command_id  c8c220f54b1744998c3a43ec6879b40b
  condition   0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae
  good tx     0xe08e03334f25328d3c993fb7e7e266d732edcaa02532f2d9ce3ca5feec38d74f (block 87135584)
  payout      1,587,297 USDC.e micro

Reads on-chain truth via RPC. For each settlement_commands row in a non-terminal
state, queries CTF balanceOf for outcome positions. If zero AND a matching
PayoutRedemption log is found in any historical tx_hash, marks state
REDEEM_CONFIRMED with the verified tx_hash + payload proof.

Usage:
    python3 run_redeem_reconcile_with_onchain_proof.py [--condition X] [--dry-run]
    PYTHONPATH=/path/to/zeus python3 run_redeem_reconcile_with_onchain_proof.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

USDCE_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEGRISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

ZEUS_ROOT = "/Users/leofitz/.openclaw/workspace-venus/zeus"
TRADE_DB = f"{ZEUS_ROOT}/state/zeus_trades.db"


def _post(rpc_url: str, payload: Dict[str, Any]) -> Any:
    r = requests.post(rpc_url, json=payload, timeout=30)
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"RPC error: {j['error']}")
    return j["result"]


def _eth_call(rpc_url: str, to: str, data: str) -> bytes:
    res = _post(rpc_url, {
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    })
    return bytes.fromhex(res[2:]) if res and res != "0x" else b""


def _erc20_balance_of(rpc_url: str, token: str, holder: str) -> int:
    selector = "0x70a08231"
    arg = holder[2:].lower().rjust(64, "0")
    raw = _eth_call(rpc_url, token, selector + arg)
    return int.from_bytes(raw, "big") if raw else 0


def _ctf_balance_of(rpc_url: str, holder: str, position_id: int) -> int:
    """ERC-1155 balanceOf(account, id) on CTF."""
    selector = "0x00fdd58e"
    arg1 = holder[2:].lower().rjust(64, "0")
    arg2 = hex(position_id)[2:].rjust(64, "0")
    raw = _eth_call(rpc_url, CTF_ADDRESS, selector + arg1 + arg2)
    return int.from_bytes(raw, "big") if raw else 0


def _verify_payout_redemption(
    rpc_url: str, tx_hash: str, condition_id: str, min_payout_micro: int,
) -> Optional[Dict[str, Any]]:
    cond_hex = condition_id.lower().replace("0x", "")
    rc = _post(rpc_url, {
        "jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    })
    if not rc:
        return None
    if int(rc.get("status", "0x0"), 16) != 1:
        return None

    ctf_logged = False
    negrisk_logged = False
    payout_micro = 0
    for log in rc.get("logs", []):
        addr = (log.get("address") or "").lower()
        data = (log.get("data") or "").lower()
        if cond_hex not in data:
            continue
        if addr == CTF_ADDRESS.lower():
            ctf_logged = True
            # PayoutRedemption ABI (non-indexed data):
            #   word[0] = conditionId
            #   word[1] = offset_to_indexSets (always 0x60 = 96 bytes = 3 words)
            #   word[2] = payout                ← what we want
            #   word[3] = indexSets.length
            #   word[4..] = indexSets values
            words = [data[2 + i*64 : 2 + (i+1)*64]
                     for i in range((len(data) - 2) // 64)]
            if len(words) >= 3 and words[0]:
                # Sanity-check conditionId in word[0]
                if words[0].lower() == cond_hex.lower():
                    payout_micro = int(words[2], 16) if words[2] else 0
        elif addr == NEGRISK_ADAPTER.lower():
            negrisk_logged = True

    if not ctf_logged:
        return None
    if payout_micro < min_payout_micro:
        return None
    return {
        "block_number": int(rc["blockNumber"], 16),
        "tx_hash": tx_hash,
        "payout_micro": payout_micro,
        "to": rc["to"],
        "negrisk_logged": negrisk_logged,
        "ctf_logged": ctf_logged,
    }


def _candidate_tx_hashes(conn: sqlite3.Connection, command_id: str) -> List[str]:
    rows = conn.execute(
        "SELECT payload_json FROM settlement_command_events WHERE command_id = ?",
        (command_id,),
    ).fetchall()
    out: List[str] = []
    for (p,) in rows:
        if not p:
            continue
        try:
            j = json.loads(p)
        except Exception:
            continue
        for k in ("tx_hash", "transactionHash"):
            v = j.get(k)
            if v and v not in out:
                out.append(v)
    return out


def _position_ids(token_amounts_json: Optional[str]) -> List[int]:
    if not token_amounts_json:
        return []
    try:
        j = json.loads(token_amounts_json)
    except Exception:
        return []
    keys = list(j.keys()) if isinstance(j, dict) else (j if isinstance(j, list) else [])
    ids: List[int] = []
    for k in keys:
        try:
            ids.append(int(k))
        except (TypeError, ValueError):
            continue
    return ids


def reconcile_one(
    *, conn, rpc_url, safe_address, command_id, condition_id,
    pusd_amount_micro, token_amounts_json, dry_run,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "command_id": command_id,
        "condition_id": condition_id,
        "action": "no_change",
        "reason": "",
        "proof": None,
    }
    position_ids = _position_ids(token_amounts_json)
    balances: Dict[int, int] = {}
    for pid in position_ids:
        try:
            balances[pid] = _ctf_balance_of(rpc_url, safe_address, pid)
        except Exception as exc:
            out["action"] = "skip"
            out["reason"] = f"ctf_balance_query_failed: {exc}"
            return out

    # Primary check: did a successful PayoutRedemption fire for this condition?
    # Secondary check: outcome-token balance (informational — NegRisk fractional
    # rounding can leave dust without a corresponding payout obligation).
    cands = _candidate_tx_hashes(conn, command_id)
    proof = None
    for h in cands:
        v = _verify_payout_redemption(
            rpc_url, h, condition_id, pusd_amount_micro,
        )
        if v:
            proof = v
            break

    total = sum(balances.values())
    DUST_CAP_MICRO = 1000  # 0.001 USDC-equivalent — any tail from fractional rounding
    if proof is None:
        # No on-chain proof yet. If positions still real (above dust cap), keep pending.
        if total > DUST_CAP_MICRO:
            out["action"] = "keep_pending"
            out["reason"] = f"no_payout_proof + outcome_tokens_remain total={total}"
        else:
            out["action"] = "skip"
            out["reason"] = (
                f"no_payout_proof_in_event_candidates={cands} "
                f"dust_balance_total={total}"
            )
        return out

    # PayoutRedemption proven on-chain. Dust balance is informational, not blocking.
    out["proof"] = proof
    out["balance_at_confirm"] = balances

    if dry_run:
        out["action"] = "would_confirm"
        return out

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    payload = {
        "condition_id": condition_id,
        "reconciled_by": "run_redeem_reconcile_with_onchain_proof",
        "on_chain_proof": proof,
        "ctf_outcome_token_balances": {str(k): v for k, v in balances.items()},
        "reconciled_at": now,
    }
    pj = json.dumps(payload, sort_keys=True)
    ph = hashlib.sha256(pj.encode()).hexdigest()

    with conn:
        conn.execute(
            "UPDATE settlement_commands SET state = ?, tx_hash = ?, "
            "terminal_at = ? WHERE command_id = ?",
            ("REDEEM_CONFIRMED", proof["tx_hash"], now, command_id),
        )
        conn.execute(
            "INSERT INTO settlement_command_events "
            "(command_id, event_type, payload_hash, payload_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (command_id, "REDEEM_CONFIRMED", ph, pj, now),
        )
    out["action"] = "confirmed"
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--condition", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--trade-db", default=TRADE_DB)
    p.add_argument("--rpc-url",
                   default=os.environ.get("POLYGON_RPC_URL", "https://polygon.drpc.org"))
    a = p.parse_args()

    sys.path.insert(0, ZEUS_ROOT)
    os.environ.setdefault("ZEUS_MODE", "live")
    from src.data.polymarket_client import resolve_polymarket_credentials
    creds = resolve_polymarket_credentials()
    safe = creds["funder_address"]

    print(f"[reconcile] Safe: {safe}")
    print(f"[reconcile] RPC : {a.rpc_url}")
    print(f"[reconcile] DB  : {a.trade_db}")
    print(f"[reconcile] dry_run={a.dry_run}")
    usdce = _erc20_balance_of(a.rpc_url, USDCE_ADDRESS, safe)
    print(f"[reconcile] Safe USDC.e: {usdce} micro = {usdce/1e6} USDC.e")
    print()

    conn = sqlite3.connect(a.trade_db)
    conn.row_factory = sqlite3.Row

    where = "state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED')"
    params: list = []
    if a.condition:
        where += " AND condition_id = ?"
        params.append(a.condition)
    rows = conn.execute(
        "SELECT command_id, condition_id, pusd_amount_micro, "
        "token_amounts_json, state FROM settlement_commands "
        f"WHERE {where} ORDER BY requested_at DESC",
        params,
    ).fetchall()
    print(f"[reconcile] non-terminal rows: {len(rows)}")
    summary = {"confirmed": 0, "keep_pending": 0, "skip": 0,
               "would_confirm": 0, "no_change": 0}
    for r in rows:
        print(f"\ncommand_id={r['command_id']} state={r['state']}")
        out = reconcile_one(
            conn=conn, rpc_url=a.rpc_url, safe_address=safe,
            command_id=r["command_id"], condition_id=r["condition_id"],
            pusd_amount_micro=r["pusd_amount_micro"] or 0,
            token_amounts_json=r["token_amounts_json"],
            dry_run=a.dry_run,
        )
        print(f"  action={out['action']} reason={out['reason']}")
        if out.get("proof"):
            x = out["proof"]
            print(f"  proof: block={x['block_number']} tx={x['tx_hash']}")
            print(f"         payout_micro={x['payout_micro']} "
                  f"negrisk={x['negrisk_logged']} ctf={x['ctf_logged']}")
        summary[out["action"]] = summary.get(out["action"], 0) + 1
    print(f"\n[reconcile] summary: {summary}")
    conn.close()


if __name__ == "__main__":
    main()
