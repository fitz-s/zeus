#!/usr/bin/env python3
# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 ("有需要redeem的redeem" —
#   redeem what is actually redeemable; on-chain submission authorized). Targets
#   verified via https://data-api.polymarket.com/positions?user=<safe> AND
#   re-verified against live CTF balanceOf before any enqueue.
# Purpose: One-shot. Enqueue the Safe's REAL unredeemed winners through the
#   system's own path (request_redeem) with correct condition ids + Zeus
#   winning_index_set labels (Yes->["2"], No->["1"]), then drive each through
#   the FIXED submit_redeem (chain-truth balance pre-flight + self-heal), wait
#   for each Safe tx to mine (Safe nonce sequencing), then reconcile to
#   REDEEM_CONFIRMED with payout proof.
#
# Usage (env must match the live daemon's gates):
#   ZEUS_AUTONOMOUS_REDEEM_ENABLED=1 ZEUS_PUSD_FX_CLASSIFIED=FX_LINE_ITEM \
#   PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python \
#     scripts/redeem_real_winners_2026_06_09.py --positions /tmp/safe_positions.json [--apply]
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

logger = logging.getLogger("redeem_real_winners")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positions", required=True, help="data-api positions JSON dump")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-price", type=float, default=0.5,
                        help="only positions with curPrice above this (value winners)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.apply:
        for env, want in (("ZEUS_AUTONOMOUS_REDEEM_ENABLED", "1"),
                          ("ZEUS_PUSD_FX_CLASSIFIED", "FX_LINE_ITEM")):
            if os.environ.get(env, "").strip() != want:
                print(f"FATAL: {env} must be {want!r} (daemon-parity gates). Refusing.")
                return 2
        if os.environ.get("ZEUS_AUTONOMOUS_REDEEM_DRY_RUN", "").strip():
            print("FATAL: ZEUS_AUTONOMOUS_REDEEM_DRY_RUN is set; unset for live broadcast.")
            return 2

    # Same dual-run lock the daemon's _redeem_submitter_cycle takes: while this
    # one-shot holds it, the live daemon's 2-minute submitter tick skips cleanly
    # instead of racing the freshly-enqueued INTENT_CREATED rows with stale code.
    from src.data.dual_run_lock import acquire_lock
    import contextlib
    lock_cm = acquire_lock("redeem_submitter") if args.apply else contextlib.nullcontext(True)
    with lock_cm as acquired:
        if not acquired:
            print("FATAL: redeem_submitter lock held (daemon mid-tick); rerun in a minute.")
            return 3
        return _run(args)


def _run(args) -> int:
    from src.data.polymarket_client import (
        resolve_polymarket_credentials,
        _resolve_clob_v2_signature_type,
    )
    from src.execution.settlement_commands import request_redeem, submit_redeem
    from src.state.db import get_trade_connection_with_world_required
    from src.venue.polymarket_v2_adapter import (
        DEFAULT_POLYGON_RPC_URL,
        DEFAULT_V2_HOST,
        PolymarketV2Adapter,
    )

    positions = json.load(open(args.positions))
    targets = [
        p for p in positions
        if p.get("redeemable") and float(p.get("curPrice") or 0) > args.min_price
        and p.get("negativeRisk")
    ]
    targets.sort(key=lambda p: -float(p["size"]))

    conn = get_trade_connection_with_world_required(write_class="live")
    creds = resolve_polymarket_credentials()
    adapter = PolymarketV2Adapter(
        host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
        funder_address=creds["funder_address"],
        signer_key=creds["private_key"],
        chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
        signature_type=_resolve_clob_v2_signature_type(),
        polygon_rpc_url=os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL),
        api_creds=creds.get("api_creds"),
    )

    def _wait_mined(tx_hash: str, timeout_s: float = 180.0) -> dict | None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            try:
                rcpt = adapter._rpc_call(
                    adapter.polygon_rpc_url, "eth_getTransactionReceipt", [tx_hash]
                )
            except Exception:
                rcpt = None
            if rcpt:
                return rcpt
            time.sleep(3.0)
        return None

    outcomes = []
    for p in targets:
        cid = p["conditionId"]
        zeus_idx = 2 if p["outcome"] == "Yes" else 1
        api_micro = round(float(p["size"]) * 1e6)
        # Re-verify chain truth immediately before any action (never trust the
        # API dump alone): derived positionId must equal the API asset AND the
        # live balance must be > 0.
        probe = adapter.get_negrisk_winning_position_balance(cid, zeus_idx)
        ok = (probe.get("ok") and str(probe.get("position_id")) == str(p["asset"])
              and int(probe.get("balance_micro") or 0) > 0)
        print(f"\n{cid[:12]} {p['outcome']} size={p['size']} live={probe.get('balance_micro')} "
              f"pid_match={str(probe.get('position_id'))==str(p['asset'])} :: {p['title'][:50]}")
        if not ok:
            print("  SKIP: chain truth does not confirm a redeemable winner here.")
            outcomes.append({"condition_id": cid, "action": "skipped_chain_mismatch",
                             "probe": {k: probe.get(k) for k in ("ok", "balance_micro", "position_id", "errorCode")}})
            continue
        if not args.apply:
            outcomes.append({"condition_id": cid, "action": "dry",
                             "live_balance_micro": probe["balance_micro"]})
            continue

        cmd_id = request_redeem(
            cid,
            "pUSD",
            market_id=cid,
            pusd_amount_micro=api_micro,
            token_amounts={str(p["asset"]): float(p["size"])},
            winning_index_set=json.dumps([str(zeus_idx)]),
            conn=conn,
        )
        conn.commit()
        result = submit_redeem(cmd_id, adapter, object(), conn=conn)
        conn.commit()
        rec = {"condition_id": cid, "command_id": cmd_id, "title": p["title"],
               "size": p["size"], "state": result.state.value, "tx_hash": result.tx_hash}
        if result.tx_hash:
            rcpt = _wait_mined(result.tx_hash)
            status = rcpt.get("status") if rcpt else None
            rec["mined_status"] = status
            print(f"  tx={result.tx_hash} mined_status={status}")
            if rcpt is None or str(status).lower() not in ("0x1", "1"):
                print("  WARNING: tx not confirmed clean; STOPPING batch (Safe nonce safety).")
                rec["aborted_batch"] = True
                outcomes.append(rec)
                break
        else:
            print(f"  no tx hash; state={result.state.value} err={result.error_payload}")
            rec["error_payload"] = result.error_payload
            print("  STOPPING batch for inspection.")
            outcomes.append(rec)
            break
        outcomes.append(rec)

    conn.close()
    print("\n=== SUMMARY ===")
    print(json.dumps(outcomes, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
