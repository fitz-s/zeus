# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 ("这是手动触发不是auto
#   collect" — the system must auto-collect with no operator hands). Root cause
#   evidence: the harvester's redeem enqueue keyed off the internal portfolio
#   ledger (position_current), which (a) enqueued phantoms (ledger said held,
#   chain empty -> GS013 purgatory), (b) missed real winners stuck in
#   pending_exit/admin_closed phases, and (c) could never see ledger-invisible
#   holdings (London-16C YES ~$798, a negRisk-conversion product with ZERO
#   position_current rows).
"""Inventory-truth redeem sweep: enqueue redeems from CHAIN holdings.

K=1 structural decision: the redeem trigger is the Safe's ACTUAL on-chain
inventory, never the internal portfolio ledger (the ledger is PnL attribution
only). Pipeline per candidate:

  data-api positions (redeemable, negRisk, curPrice > 0.5)
    -> chain-truth verification (adapter.get_negrisk_winning_position_balance:
       derived positionId MUST equal the API asset id AND live balance > 0)
    -> request_redeem (system path; idempotent against the active-row unique
       index — an active command for the same condition returns its id)
    -> the existing _redeem_submitter_cycle drives submit_redeem (chain-truth
       pre-flight + live-balance self-heal)
    -> redeemed USDC.e proceeds are picked up by the existing balance-driven
       wrap flow (enqueue_wrap_if_balance_above_threshold) -> usable pUSD.

Honesty contract: a candidate is enqueued ONLY when chain truth confirms a
nonzero winning-position balance under the exact API asset id. API lag after a
successful redeem is harmless twice over: the probe reads live balance 0 (no
enqueue), and even if a row slipped through, submit_redeem's pre-flight
terminates it with provenance instead of broadcasting.

Zero-value redeemables (curPrice ~ 0) are losing-side dust: the winning-side
balance is 0 and there is nothing of value to claim through redeemPositions'
winning path; they are intentionally not swept.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_API_POSITIONS_URL = "https://data-api.polymarket.com/positions"

# Value floor: only sweep holdings whose current price marks them as winners.
DEFAULT_MIN_CUR_PRICE = 0.5


def fetch_safe_redeemable_positions(
    safe_address: str, *, timeout_s: float = 10.0
) -> Optional[list[dict[str, Any]]]:
    """Fetch the Safe's positions from the Polymarket data API.

    Returns the raw list, or None on any transport/shape failure (fail-soft:
    the sweep skips this tick; never fabricates an empty inventory from an
    error — None and [] are distinct outcomes).
    """
    import httpx

    url = f"{DATA_API_POSITIONS_URL}?user={safe_address}&limit=500"
    try:
        resp = httpx.get(url, timeout=timeout_s)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — fail-soft, retried next tick
        logger.warning("[INVENTORY_SWEEP_FETCH_FAILED] safe=%s exc=%s", safe_address, exc)
        return None
    if not isinstance(payload, list):
        logger.warning(
            "[INVENTORY_SWEEP_NON_LIST_PAYLOAD] safe=%s type=%s",
            safe_address, type(payload).__name__,
        )
        return None
    return payload


def sweep_chain_inventory_for_redeems(
    conn: sqlite3.Connection,
    adapter: Any,
    safe_address: str,
    *,
    positions: Optional[list[dict[str, Any]]] = None,
    min_cur_price: float = DEFAULT_MIN_CUR_PRICE,
) -> list[str]:
    """Enqueue redeem commands for every chain-verified redeemable winner.

    positions: pre-fetched data-api payload (tests inject here); fetched live
    when None. Returns the list of enqueued/active command ids.

    This function NEVER reads position_current or any portfolio table — that
    is the antibody against the ledger-as-trigger defect. Chain truth only.
    """
    from src.execution.settlement_commands import request_redeem

    if positions is None:
        positions = fetch_safe_redeemable_positions(safe_address)
    if positions is None:
        return []

    probe_fn = getattr(adapter, "get_negrisk_winning_position_balance", None)
    if not callable(probe_fn):
        logger.warning("[INVENTORY_SWEEP_NO_PROBE] adapter lacks balance probe; skipping")
        return []

    command_ids: list[str] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        try:
            redeemable = bool(p.get("redeemable"))
            cur_price = float(p.get("curPrice") or 0.0)
            neg_risk = bool(p.get("negativeRisk"))
            condition_id = str(p.get("conditionId") or "")
            asset = str(p.get("asset") or "")
            outcome = str(p.get("outcome") or "").strip().lower()
            size = float(p.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
        if not (redeemable and cur_price > min_cur_price and condition_id and asset and size > 0):
            continue
        if outcome not in ("yes", "no"):
            continue
        if not neg_risk:
            # Standard-CTF winners are rare for Zeus (all temp markets are
            # negRisk); surface loudly instead of silently guessing a path.
            logger.warning(
                "[INVENTORY_SWEEP_NON_NEGRISK_WINNER] condition_id=%s asset=%s "
                "size=%s — not swept; needs standard CTF handling",
                condition_id, asset, size,
            )
            continue
        zeus_index = 2 if outcome == "yes" else 1

        # Chain-truth verification: derived winning positionId must equal the
        # API asset id AND the live balance must be positive.
        try:
            probe = probe_fn(condition_id, zeus_index)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[INVENTORY_SWEEP_PROBE_EXCEPTION] condition_id=%s exc=%s",
                condition_id, exc,
            )
            continue
        if not isinstance(probe, dict) or not probe.get("ok"):
            logger.warning(
                "[INVENTORY_SWEEP_PROBE_FAILED] condition_id=%s errorCode=%s",
                condition_id,
                probe.get("errorCode") if isinstance(probe, dict) else None,
            )
            continue
        live_micro = int(probe.get("balance_micro") or 0)
        if live_micro <= 0:
            # Already redeemed (API lag) or empty — nothing to collect.
            continue
        if str(probe.get("position_id")) != asset:
            logger.warning(
                "[INVENTORY_SWEEP_POSITION_ID_MISMATCH] condition_id=%s "
                "api_asset=%s derived=%s — not swept (fail-closed)",
                condition_id, asset, probe.get("position_id"),
            )
            continue

        command_id = request_redeem(
            condition_id,
            "pUSD",
            market_id=condition_id,
            pusd_amount_micro=live_micro,
            token_amounts={asset: live_micro / 1_000_000},
            winning_index_set=json.dumps([str(zeus_index)]),
            conn=conn,
        )
        command_ids.append(command_id)
        logger.info(
            "[INVENTORY_SWEEP_ENQUEUED] command_id=%s condition_id=%s "
            "outcome=%s live_micro=%d title=%r",
            command_id, condition_id, outcome, live_micro,
            str(p.get("title") or "")[:60],
        )
    return command_ids
