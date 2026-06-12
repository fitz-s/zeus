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
#   2026-06-09 P1 follow-up (operator-accepted review finding): the data-api
#   curPrice prefilter (cur_price > min_cur_price) was a SECOND data-api veto on
#   chain truth — a stale/zero/wrong mutable market-data field could skip the
#   chain probe entirely and strand a real winner (the exact $857 category this
#   module was built to kill). curPrice is now demoted to telemetry / priority /
#   post-chain dust heuristic; CHAIN balance is the only veto.
"""Inventory-truth redeem sweep: enqueue redeems from CHAIN holdings.

K=1 structural decision: the redeem trigger is the Safe's ACTUAL on-chain
inventory, never the internal portfolio ledger (the ledger is PnL attribution
only). Pipeline per candidate:

  data-api positions (STRUCTURAL prefilter only: redeemable, negRisk,
                       condition_id, asset, size > 0 — NO curPrice veto)
    -> chain-truth verification (adapter.get_negrisk_winning_position_balance:
       derived positionId MUST equal the API asset id AND live balance > 0)
    -> request_redeem (system path; idempotent against the active-row unique
       index — an active command for the same condition returns its id)
    -> the existing _redeem_submitter_cycle drives submit_redeem (chain-truth
       pre-flight + live-balance self-heal)
    -> redeemed USDC.e proceeds are picked up by the existing balance-driven
       wrap flow (enqueue_wrap_if_balance_above_threshold) -> usable pUSD.

curPrice is a MUTABLE market-data field, NOT redemption truth. It MUST NOT be a
prefilter veto: a stale/zero/wrong curPrice on a real winner would skip the
chain probe entirely and strand collateral (the 2026-06-09 $857 incident
category). curPrice is therefore demoted to three NON-veto roles:
  (a) telemetry — a below-threshold curPrice on a chain-confirmed winner is the
      data-api LYING; WARN-log it.
  (b) priority ordering — chain probes are RPC calls under a per-tick budget;
      higher-curPrice rows probe first (likely winners first), but EVERY row is
      eventually probed across ticks (rotation), so a curPrice=0 jackpot is
      never permanently skipped.
  (c) dust heuristic — applied ONLY AFTER the chain balance is known. Dust =
      tiny CHAIN value (balance_micro x price), never price alone. A curPrice=0
      winner with a large chain balance is the JACKPOT case, never dust.

Honesty contract: a candidate is enqueued ONLY when chain truth confirms a
nonzero winning-position balance under the exact API asset id. API lag after a
successful redeem is harmless twice over: the probe reads live balance 0 (no
enqueue), and even if a row slipped through, submit_redeem's pre-flight
terminates it with provenance instead of broadcasting.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_API_POSITIONS_URL = "https://data-api.polymarket.com/positions"

# curPrice is NOT a redemption-truth source — see module docstring. This
# threshold is retained ONLY as the telemetry boundary (below it on a
# chain-confirmed winner = the data-api is lying; WARN) and the priority hint.
# It is NEVER a prefilter veto.
DEFAULT_MIN_CUR_PRICE = 0.5

# Per-tick chain-probe budget: each probe is an RPC call, so an unbounded sweep
# could flood the node when the data-api lists many resolved-but-not-redeemed
# rows. We probe at most this many structurally-eligible rows per tick, ordered
# by curPrice (likely winners first) for telemetry, then rotate the start cursor
# so EVERY eligible row is eventually probed across successive ticks — liveness
# (no row permanently skipped, including curPrice=0 jackpots) without RPC flood.
DEFAULT_MAX_PROBES_PER_TICK = 25

# Post-chain dust floor (USD): a chain-confirmed winner worth less than this in
# ACTUAL redeemable value (live balance x curPrice, or live balance alone when
# curPrice is unusable) is not worth an on-chain redeem gas spend. Applied ONLY
# after the chain balance is known — never as a price-alone prefilter.
DEFAULT_MIN_REDEEM_VALUE_USD = 0.01

# Rotation cursor per Safe address (module state; advanced each tick so the
# probe window slides over the full eligible set across ticks).
_rotation_cursor: dict[str, int] = {}


def reset_rotation_for_tests() -> None:
    """Clear the per-Safe rotation cursor. Tests only."""
    _rotation_cursor.clear()


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
    max_probes_per_tick: int = DEFAULT_MAX_PROBES_PER_TICK,
    min_redeem_value_usd: float = DEFAULT_MIN_REDEEM_VALUE_USD,
) -> list[str]:
    """Enqueue redeem commands for every chain-verified redeemable winner.

    positions: pre-fetched data-api payload (tests inject here); fetched live
    when None. Returns the list of enqueued/active command ids.

    Truth model (2026-06-09 P1 fix): the data-api curPrice is a MUTABLE
    market-data field, NOT redemption truth, so it is NEVER a prefilter veto.
    The structural prefilter is `redeemable and condition_id and asset and
    size > 0` (negRisk routing aside); CHAIN balance is the only veto. curPrice
    is demoted to telemetry (WARN when it lied), priority ordering, and a
    POST-chain dust heuristic. See module docstring.

    This function NEVER reads position_current or any portfolio table — that
    is the antibody against the ledger-as-trigger defect. Chain truth only.
    """
    from src.execution.settlement_commands import request_redeem

    if positions is None:
        positions = fetch_safe_redeemable_positions(safe_address)
    if positions is None:
        return []

    negrisk_probe_fn = getattr(adapter, "get_negrisk_winning_position_balance", None)
    standard_probe_fn = getattr(adapter, "get_standard_ctf_winning_position_balance", None)
    if not callable(negrisk_probe_fn) and not callable(standard_probe_fn):
        logger.warning("[INVENTORY_SWEEP_NO_PROBE] adapter lacks balance probe; skipping")
        return []

    # STRUCTURAL prefilter only (no curPrice veto): collect every row that could
    # possibly be a redeemable chain holding. curPrice is carried along solely as
    # a telemetry/priority hint.
    eligible: list[dict[str, Any]] = []
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
        # Structural-only veto: identity + nonzero size + resolved. curPrice is
        # NOT consulted here — a curPrice=0 winner with a large chain balance is
        # the jackpot case and MUST reach the chain probe.
        if not (redeemable and condition_id and asset and size > 0):
            continue
        if outcome not in ("yes", "no"):
            continue
        # neg_risk routing is a per-candidate fact, NOT a structural veto. Both
        # negRisk and standard-CTF winners are swept; only the chain-truth probe
        # (and the redeem lane downstream) differ. The standard-CTF lane was
        # added 2026-06-10 (operator redeem directive — $19 stuck on a
        # non-negRisk NO winner the negRisk-only sweep skipped forever).
        eligible.append({
            "cur_price": cur_price,
            "condition_id": condition_id,
            "asset": asset,
            "outcome": outcome,
            "size": size,
            "neg_risk": neg_risk,
            "title": str(p.get("title") or "")[:60],
        })

    if not eligible:
        return []

    # Priority ordering: probe likely-winners (higher curPrice) first within the
    # per-tick budget. Then rotate the start cursor so the remaining rows are
    # covered on subsequent ticks — no eligible row is permanently skipped.
    eligible.sort(key=lambda c: c["cur_price"], reverse=True)
    n = len(eligible)
    cap = max_probes_per_tick if max_probes_per_tick > 0 else n
    start = _rotation_cursor.get(safe_address, 0) % n if n else 0
    window = [eligible[(start + i) % n] for i in range(min(cap, n))]
    # Advance the cursor so the next tick begins where this one stopped.
    _rotation_cursor[safe_address] = (start + len(window)) % n if n else 0

    command_ids: list[str] = []
    for cand in window:
        condition_id = cand["condition_id"]
        asset = cand["asset"]
        outcome = cand["outcome"]
        cur_price = cand["cur_price"]
        cand_neg_risk = bool(cand["neg_risk"])
        zeus_index = 2 if outcome == "yes" else 1

        # Probe routing: negRisk positions derive their ERC1155 id via
        # NegRiskAdapter.wcol(); standard-CTF positions derive directly from
        # USDC.e collateral. Each lane has its OWN chain-truth probe; the redeem
        # command downstream (submit_redeem) routes on the same neg_risk fact.
        probe_fn = negrisk_probe_fn if cand_neg_risk else standard_probe_fn
        if not callable(probe_fn):
            logger.warning(
                "[INVENTORY_SWEEP_NO_PROBE_FOR_LANE] condition_id=%s neg_risk=%s "
                "— adapter lacks the matching balance probe; skipped",
                condition_id, cand_neg_risk,
            )
            continue

        # Chain-truth verification: derived winning positionId must equal the
        # API asset id AND the live balance must be positive. This is the ONLY
        # veto — every structurally-eligible row is probed regardless of price.
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
            # Chain veto: already redeemed (API lag) or empty — nothing to
            # collect. Chain truth says zero -> no enqueue.
            continue
        if str(probe.get("position_id")) != asset:
            logger.warning(
                "[INVENTORY_SWEEP_POSITION_ID_MISMATCH] condition_id=%s "
                "api_asset=%s derived=%s — not swept (fail-closed)",
                condition_id, asset, probe.get("position_id"),
            )
            continue

        # curPrice telemetry (role a): a below-threshold curPrice on a
        # chain-CONFIRMED winner means the data-api mutable field is LYING about
        # a real holding. Log it (we still enqueue — chain truth wins).
        if cur_price <= min_cur_price:
            logger.warning(
                "[INVENTORY_SWEEP_DATA_API_PRICE_DISAGREES] condition_id=%s "
                "asset=%s curPrice=%.4f<=%.4f but chain balance=%d>0 — data-api "
                "price is stale/wrong; enqueuing on CHAIN truth.",
                condition_id, asset, cur_price, min_cur_price, live_micro,
            )

        # Dust heuristic (role c): applied ONLY now that the chain balance is
        # known. Redeemable value = chain balance x curPrice (or chain balance
        # alone when curPrice is unusable, so a price=0 jackpot is NEVER dust).
        live_units = live_micro / 1_000_000
        redeem_value_usd = live_units * cur_price if cur_price > 0.0 else live_units
        if redeem_value_usd < min_redeem_value_usd:
            logger.info(
                "[INVENTORY_SWEEP_CHAIN_DUST] condition_id=%s asset=%s "
                "live_micro=%d curPrice=%.4f redeem_value_usd=%.6f<%.6f — "
                "not worth redeem gas; skipped (chain value, not price alone).",
                condition_id, asset, live_micro, cur_price,
                redeem_value_usd, min_redeem_value_usd,
            )
            continue

        command_id = request_redeem(
            condition_id,
            "pUSD",
            market_id=condition_id,
            pusd_amount_micro=live_micro,
            token_amounts={asset: live_units},
            winning_index_set=json.dumps([str(zeus_index)]),
            conn=conn,
        )
        command_ids.append(command_id)
        logger.info(
            "[INVENTORY_SWEEP_ENQUEUED] command_id=%s condition_id=%s "
            "outcome=%s live_micro=%d cur_price=%.4f title=%r",
            command_id, condition_id, outcome, live_micro, cur_price,
            cand["title"],
        )
    return command_ids
