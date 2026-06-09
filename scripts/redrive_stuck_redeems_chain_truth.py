#!/usr/bin/env python3
# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 (re-drive the 6
#   REDEEM_OPERATOR_REQUIRED / GS013 rows through the chain-truth-fixed
#   submit_redeem path). Operator-authorized on-chain redeem for these 6.
# Purpose: One-shot re-drive. For each stuck command: reseat
#   REDEEM_OPERATOR_REQUIRED -> REDEEM_RETRYING (atomic), then invoke the FIXED
#   submit_redeem (same code the daemon uses). Zero-balance positions terminate
#   as REDEEM_CONFIRMED with chain provenance (no broadcast); nonzero positions
#   self-heal to the live balance and submit (requires ZEUS_AUTONOMOUS_REDEEM_ENABLED).
#
# Usage:
#   PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python \
#       scripts/redrive_stuck_redeems_chain_truth.py [--apply]
#
# Without --apply: dry diagnosis only (probe balances, print intended action).
# With --apply: reseat + submit_redeem through the fixed path.
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger("redrive_stuck_redeems")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Perform reseat + submit_redeem.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from src.data.polymarket_client import (
        resolve_polymarket_credentials,
        _resolve_clob_v2_signature_type,
    )
    from src.execution.settlement_commands import (
        SettlementState,
        _atomic_transition,
        submit_redeem,
    )
    from src.state.db import get_trade_connection_with_world_required
    from src.venue.polymarket_v2_adapter import (
        DEFAULT_POLYGON_RPC_URL,
        DEFAULT_V2_HOST,
        PolymarketV2Adapter,
    )

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

    rows = conn.execute(
        "SELECT command_id, condition_id, winning_index_set, pusd_amount_micro, state "
        "FROM settlement_commands WHERE state IN (?, ?) ORDER BY requested_at",
        (
            SettlementState.REDEEM_OPERATOR_REQUIRED.value,
            SettlementState.REDEEM_RETRYING.value,
        ),
    ).fetchall()

    print(f"Found {len(rows)} stuck rows (OPERATOR_REQUIRED|RETRYING).")
    outcomes = []
    for r in rows:
        cmd = r["command_id"]
        cid = r["condition_id"]
        idx = int(json.loads(r["winning_index_set"])[0]) if r["winning_index_set"] else None
        probe = adapter.get_negrisk_winning_position_balance(cid, idx) if idx in (1, 2) else {"ok": False, "errorCode": "NO_INDEX_SET"}
        bal = probe.get("balance_micro") if probe.get("ok") else None
        print(f"\ncommand={cmd[:12]} cid={cid[:14]} win_idx={idx} "
              f"probe_ok={probe.get('ok')} live_balance_micro={bal} recorded_micro={r['pusd_amount_micro']}")

        if not args.apply:
            if probe.get("ok") and bal == 0:
                print("  [DRY] would terminate REDEEM_CONFIRMED (already-redeemed/empty, chain provenance)")
            elif probe.get("ok") and bal and bal > 0:
                print("  [DRY] would self-heal to live balance and submit (needs ZEUS_AUTONOMOUS_REDEEM_ENABLED)")
            else:
                print("  [DRY] probe failed -> NOT_RESOLVED/unknown; would leave pending (no submit)")
            outcomes.append({"command_id": cmd, "condition_id": cid, "action": "dry", "live_balance_micro": bal})
            continue

        # Fail-closed: if the probe failed entirely, do NOT reseat/submit — leave pending.
        if not probe.get("ok"):
            print("  probe failed -> leaving pending (no reseat, no submit). "
                  f"errorCode={probe.get('errorCode')}")
            outcomes.append({"command_id": cmd, "condition_id": cid, "action": "left_pending_probe_failed",
                             "errorCode": probe.get("errorCode")})
            continue

        # Reseat OPERATOR_REQUIRED -> RETRYING (atomic, disjoint from scheduler).
        # A row already in RETRYING (from a prior partial run) is submittable as-is.
        if r["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value:
            moved = _atomic_transition(
                conn, cmd,
                from_state=SettlementState.REDEEM_OPERATOR_REQUIRED,
                to_state=SettlementState.REDEEM_RETRYING,
                payload={"reason": "operator_redrive_chain_truth_2026_06_09",
                         "live_balance_micro": bal},
                error_payload=None,
            )
            if not moved:
                print("  reseat rowcount=0 (state changed concurrently) -> skip")
                outcomes.append({"command_id": cmd, "condition_id": cid, "action": "reseat_raced"})
                continue
            conn.commit()

        result = submit_redeem(cmd, adapter, object(), conn=conn)
        conn.commit()
        final = conn.execute(
            "SELECT state, tx_hash FROM settlement_commands WHERE command_id = ?", (cmd,)
        ).fetchone()
        print(f"  -> result.state={result.state.value} db_state={final['state']} tx_hash={final['tx_hash']}")
        outcomes.append({"command_id": cmd, "condition_id": cid, "action": "submitted",
                         "result_state": result.state.value, "db_state": final["state"],
                         "tx_hash": final["tx_hash"], "live_balance_micro": bal})

    conn.close()
    print("\n=== SUMMARY ===")
    print(json.dumps(outcomes, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
