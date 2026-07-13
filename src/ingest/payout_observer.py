# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T1
#   (GATED verdict, §Consult 裁决 2026-07-13) + docs/rebuild/census_local_ledger/
#   census_chain_sources.md ("Resolution payouts — NEEDS NEW INGESTER, RPC
#   plumbing exists").
"""Read-only-on-chain ConditionalTokens payout observer (LX-T1).

Reads on-chain ``payoutDenominator(conditionId)`` / ``payoutNumerators(conditionId,
outcomeIndex)`` for conditions Zeus holds/held, via the SAME urllib JSON-RPC seam
every other on-chain read in Zeus uses (``_json_rpc_call`` /
``POLYGON_CTF_ADDRESS`` in src.venue.polymarket_v2_adapter — reused here, not
duplicated). Appends immutable observation rows to trades-DB
``payout_observations`` (src.state.schema.payout_observations_schema).

LAW (LX-T1 adjudication, non-negotiable):
  - 4-state classification: UNKNOWN / UNRESOLVED / RESOLVED_ZERO /
    RESOLVED_NONZERO. Any RPC timeout, empty response, partial read, or
    unparsable result classifies UNKNOWN — NEVER a fabricated zero payout.
    (This is why this module does NOT reuse polymarket_v2_adapter's
    ``_eth_call_uint``: that helper treats a missing/empty result as 0x0 -> 0,
    which is correct for its existing callers — a redeem-time balance veto
    that fails closed on the surrounding try/except regardless of the decoded
    value — but would silently mint a zero payout here. See
    ``_eth_call_uint_strict`` below.)
  - Read-only, NO signing capability: this module only ever calls
    ``eth_call`` / ``eth_getBlockByNumber`` over public Polygon RPC. It never
    imports a signer key, a wallet credential, py_clob_client_v2, web3, or
    PolymarketV2Adapter itself (which requires signer_key to construct).
  - NOT in the settlement-grading critical path this packet: nothing reads
    payout_observations for grading yet (SettlementSemantics / WU lane is
    untouched). Disagreement wiring to a DISPUTED lane is a later packet.
  - Reorg-aware: every read is pinned to one explicit block (fetched first,
    then used as the block tag for every eth_call in that sweep) so the
    recorded block_number/block_hash is exactly the state the payout numbers
    were read against.

Table shape and immutability/supersession invariants are owned by
src.state.schema.payout_observations_schema (see that module's docstring).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.venue.polymarket_v2_adapter import (
    DEFAULT_POLYGON_RPC_URL,
    POLYGON_CTF_ADDRESS,
    V2AdapterError,
    _json_rpc_call,
    _normalize_condition_id_bytes32,
)

logger = logging.getLogger(__name__)

RpcCall = Callable[[str, str, list[Any]], Any]

# ConditionalTokens public-mapping getter selectors:
#   mapping(bytes32 => uint256[]) public payoutNumerators;   -> payoutNumerators(bytes32,uint256)
#   mapping(bytes32 => uint256)   public payoutDenominator;  -> payoutDenominator(bytes32)
# Verified locally via eth_utils.keccak — same methodology already pinned for
# every other CTF selector in polymarket_v2_adapter.py (CTF_REDEEM_POSITIONS_SELECTOR
# et al.), and covered by an antibody test mirroring
# tests/test_polymarket_v2_adapter_balance_probe.py::test_selectors_are_canonical.
PAYOUT_DENOMINATOR_SELECTOR = "0xdd34de67"
PAYOUT_NUMERATORS_SELECTOR = "0x0504c814"

STATE_UNKNOWN = "UNKNOWN"
STATE_UNRESOLVED = "UNRESOLVED"
STATE_RESOLVED_ZERO = "RESOLVED_ZERO"
STATE_RESOLVED_NONZERO = "RESOLVED_NONZERO"

VALID_STATES = frozenset(
    {STATE_UNKNOWN, STATE_UNRESOLVED, STATE_RESOLVED_ZERO, STATE_RESOLVED_NONZERO}
)

# Binary-market-only (matches _zeus_index_set_to_ctf_bitmask elsewhere in the
# adapter — Zeus does not trade non-binary CTF markets). Outcome indices here
# are the raw CTF array slot (0/1), NOT the Zeus 1=NO/2=YES bitmask label used
# by the redeem balance probes — payoutNumerators is indexed by slot.
DEFAULT_OUTCOME_INDICES: tuple[int, ...] = (0, 1)


def _eth_call_uint_strict(
    rpc_call: RpcCall,
    rpc_url: str,
    *,
    to: str,
    data: str,
    block: str,
) -> int:
    """Decode an eth_call uint256 result, refusing to conflate "no answer" with 0.

    A NEW helper, not a modification of polymarket_v2_adapter._eth_call_uint
    (zero behavior change to existing adapter methods — see module docstring).
    """
    raw = rpc_call(rpc_url, "eth_call", [{"to": to, "data": data}, block])
    if raw is None:
        raise V2AdapterError(f"eth_call returned no result for to={to} data={data[:10]}")
    text = str(raw)
    if not text.startswith("0x") or len(text) <= 2:
        raise V2AdapterError(f"eth_call returned unparsable/empty result: {raw!r}")
    return int(text, 16)


def _get_pinned_block_marker(rpc_call: RpcCall, rpc_url: str) -> tuple[int, str]:
    """Fetch the latest block's (number, hash) to pin subsequent eth_call reads to.

    Pinning payoutDenominator + payoutNumerators reads to ONE explicit block tag
    (rather than each independently hitting a moving "latest") means the
    block_number/block_hash recorded alongside an observation is exactly the
    state the payout numbers were read against — the reorg-aware marker the
    LX-T1 adjudication requires.
    """
    result = rpc_call(rpc_url, "eth_getBlockByNumber", ["latest", False])
    if not isinstance(result, dict):
        raise V2AdapterError("eth_getBlockByNumber returned no block header")
    number_hex = result.get("number")
    block_hash = result.get("hash")
    if not number_hex or not block_hash:
        raise V2AdapterError("eth_getBlockByNumber response missing number/hash")
    return int(str(number_hex), 16), str(block_hash)


def classify_payout(denominator: Optional[int], numerator: Optional[int]) -> str:
    """Pure 4-state classifier. ``None`` means "the read failed" (never 0).

    ``denominator`` is checked FIRST and is authoritative for UNRESOLVED: a
    genuinely-unresolved condition has an EMPTY on-chain payoutNumerators
    array, so reading payoutNumerators(id, idx) on it reverts (out-of-bounds
    array getter) — that revert is an EXPECTED consequence of "unresolved",
    not a missing-data failure, and must not downgrade a confirmed
    denominator==0 read to UNKNOWN. Only once denominator confirms the
    condition IS resolved (>0) does a failed/missing numerator read count as
    a genuine UNKNOWN (partial-read failure).
    """
    if denominator is None:
        return STATE_UNKNOWN
    if denominator == 0:
        return STATE_UNRESOLVED
    if numerator is None:
        return STATE_UNKNOWN
    if numerator == 0:
        return STATE_RESOLVED_ZERO
    return STATE_RESOLVED_NONZERO


def read_condition_payout(
    condition_id: str,
    *,
    rpc_url: str,
    rpc_call: RpcCall,
    outcome_indices: tuple[int, ...] = DEFAULT_OUTCOME_INDICES,
) -> list[dict[str, Any]]:
    """Read payoutDenominator + payoutNumerators[idx] for one condition.

    Returns one dict per outcome_index: outcome_index, payout_numerator,
    payout_denominator, state, block_number, block_hash. Every failure mode
    (invalid condition_id, block-marker fetch failure, denominator read
    failure, numerator read failure) classifies the affected outcome_index(es)
    UNKNOWN and never raises — the caller always gets a full, well-formed
    result list back.
    """

    def _unknown_rows(block_number: Optional[int] = None, block_hash: Optional[str] = None):
        return [
            {
                "outcome_index": int(idx),
                "payout_numerator": None,
                "payout_denominator": None,
                "state": STATE_UNKNOWN,
                "block_number": block_number,
                "block_hash": block_hash,
            }
            for idx in outcome_indices
        ]

    try:
        condition_bytes = _normalize_condition_id_bytes32(condition_id)
    except ValueError as exc:
        logger.warning("payout_observer: invalid condition_id %r: %s", condition_id, exc)
        return _unknown_rows()

    try:
        block_number, block_hash = _get_pinned_block_marker(rpc_call, rpc_url)
        block_tag = hex(block_number)
    except Exception as exc:  # noqa: BLE001 — any failure to pin a block => UNKNOWN
        logger.warning(
            "payout_observer: block marker fetch failed for %s: %s", condition_id, exc
        )
        return _unknown_rows()

    denominator: Optional[int]
    try:
        denominator_data = PAYOUT_DENOMINATOR_SELECTOR + condition_bytes.hex()
        denominator = _eth_call_uint_strict(
            rpc_call, rpc_url, to=POLYGON_CTF_ADDRESS, data=denominator_data, block=block_tag,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "payout_observer: payoutDenominator read failed for %s: %s", condition_id, exc
        )
        denominator = None

    results: list[dict[str, Any]] = []
    for idx in outcome_indices:
        numerator: Optional[int]
        if denominator is None:
            # Denominator read itself failed — nothing to classify against.
            numerator = None
        elif denominator == 0:
            # Confirmed-unresolved: the on-chain payoutNumerators array is
            # EMPTY for this condition, so payoutNumerators(id, idx) would
            # revert (out-of-bounds array getter). Do not issue that call —
            # classify_payout only needs denominator==0 to return UNRESOLVED.
            numerator = None
        else:
            try:
                numerator_data = (
                    PAYOUT_NUMERATORS_SELECTOR
                    + condition_bytes.hex()
                    + format(int(idx), "064x")
                )
                numerator = _eth_call_uint_strict(
                    rpc_call, rpc_url, to=POLYGON_CTF_ADDRESS, data=numerator_data, block=block_tag,
                )
            except Exception as exc:  # noqa: BLE001 — partial failure: this outcome_index only
                logger.warning(
                    "payout_observer: payoutNumerators[%d] read failed for %s: %s",
                    idx, condition_id, exc,
                )
                numerator = None
        results.append(
            {
                "outcome_index": int(idx),
                "payout_numerator": numerator,
                "payout_denominator": denominator,
                "state": classify_payout(denominator, numerator),
                "block_number": block_number,
                "block_hash": block_hash,
            }
        )
    return results


def _latest_observation(
    conn: sqlite3.Connection, condition_id: str, outcome_index: int
) -> Optional[tuple[int, Optional[int], Optional[int], str]]:
    row = conn.execute(
        "SELECT id, payout_numerator, payout_denominator, state "
        "FROM payout_observations "
        "WHERE condition_id = ? AND outcome_index = ? "
        "ORDER BY id DESC LIMIT 1",
        (condition_id, int(outcome_index)),
    ).fetchone()
    return tuple(row) if row is not None else None  # type: ignore[return-value]


def append_observation(
    conn: sqlite3.Connection,
    *,
    condition_id: str,
    outcome_index: int,
    payout_numerator: Optional[int],
    payout_denominator: Optional[int],
    state: str,
    block_number: Optional[int],
    block_hash: Optional[str],
    observed_at: str,
    source: str = "chain_rpc",
) -> Optional[int]:
    """Append one observation row, superseding the prior row iff the fact changed.

    Returns the new row id, or ``None`` if the classified fact is unchanged
    from the latest existing observation for this (condition_id,
    outcome_index) — no-op, keeps the append-only log from bloating under a
    sustained RPC outage (repeated UNKNOWN) or a long-settled condition
    (repeated identical RESOLVED_*).
    """
    if state not in VALID_STATES:
        raise ValueError(f"invalid payout_observations state: {state!r}")

    prior = _latest_observation(conn, condition_id, outcome_index)
    if prior is not None:
        prior_id, prior_numerator, prior_denominator, prior_state = prior
        if (
            prior_state == state
            and prior_numerator == payout_numerator
            and prior_denominator == payout_denominator
        ):
            return None
    else:
        prior_id = None

    cur = conn.execute(
        "INSERT INTO payout_observations ("
        "  condition_id, outcome_index, payout_numerator, payout_denominator, state,"
        "  block_number, block_hash, observed_at, source"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            condition_id,
            int(outcome_index),
            payout_numerator,
            payout_denominator,
            state,
            block_number,
            block_hash,
            observed_at,
            source,
        ),
    )
    new_id = cur.lastrowid
    if prior_id is not None:
        conn.execute(
            "UPDATE payout_observations SET superseded_by = ? WHERE id = ?",
            (new_id, prior_id),
        )
    return new_id


def conditions_to_observe(conn: sqlite3.Connection) -> list[str]:
    """Distinct non-empty condition_id values Zeus holds/held.

    Sourced from position_current + settlement_commands — both trade-DB
    tables on the SAME connection (no cross-DB join / ATTACH needed).
    """
    rows = conn.execute(
        "SELECT condition_id FROM position_current "
        "WHERE condition_id IS NOT NULL AND condition_id != '' "
        "UNION "
        "SELECT condition_id FROM settlement_commands "
        "WHERE condition_id IS NOT NULL AND condition_id != ''"
    ).fetchall()
    return [str(r[0]) for r in rows]


def sweep_and_record(
    conn: sqlite3.Connection,
    *,
    rpc_url: str,
    rpc_call: RpcCall,
    outcome_indices: tuple[int, ...] = DEFAULT_OUTCOME_INDICES,
    now: Optional[str] = None,
) -> dict[str, int]:
    """Sweep every condition Zeus holds/held and append fresh observations.

    All RPC reads finish before the first DML statement.  This ordering is
    load-bearing: one sweep can take many minutes, while the append phase is a
    short local transaction.  Reversing the order holds the trades-DB WAL
    writer lock across hundreds of network calls and prevents order receipts,
    collateral releases, and command recovery from committing.

    Caller owns the append transaction (commit/rollback), per INV-37.
    """
    observed_at = now or datetime.now(timezone.utc).isoformat()
    condition_ids = conditions_to_observe(conn)
    observations: list[tuple[str, dict[str, Any]]] = []
    for condition_id in condition_ids:
        observations.extend(
            (condition_id, result)
            for result in read_condition_payout(
                condition_id,
                rpc_url=rpc_url,
                rpc_call=rpc_call,
                outcome_indices=outcome_indices,
            )
        )

    appended = 0
    unchanged = 0
    for condition_id, result in observations:
        new_id = append_observation(
            conn,
            condition_id=condition_id,
            outcome_index=result["outcome_index"],
            payout_numerator=result["payout_numerator"],
            payout_denominator=result["payout_denominator"],
            state=result["state"],
            block_number=result["block_number"],
            block_hash=result["block_hash"],
            observed_at=observed_at,
        )
        if new_id is None:
            unchanged += 1
        else:
            appended += 1
    return {
        "conditions": len(condition_ids),
        "appended": appended,
        "unchanged": unchanged,
    }


def payout_observer_cycle(
    *,
    rpc_url: Optional[str] = None,
    rpc_call: Optional[RpcCall] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, int]:
    """Scheduler entry point (post_trade_capital_daemon, ~10-min cadence).

    Read-only on chain: it has no signing or transaction-broadcast capability.
    Locally it opens trades-DB (unless injected for testing), completes every
    RPC read before beginning the append transaction, then commits. It never
    opens world-DB or forecasts-DB — payout_observations is trade-DB-only and
    this packet does not wire settlement-grading consumption.
    """
    from src.state.db import get_trade_connection

    own_conn = conn is None
    if own_conn:
        conn = get_trade_connection(write_class="live")
    resolved_rpc_call: RpcCall = rpc_call if rpc_call is not None else _json_rpc_call
    resolved_rpc_url = rpc_url or os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)

    try:
        result = sweep_and_record(conn, rpc_url=resolved_rpc_url, rpc_call=resolved_rpc_call)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        if own_conn:
            conn.close()
    logger.info(
        "payout_observer_cycle: conditions=%d appended=%d unchanged=%d",
        result["conditions"], result["appended"], result["unchanged"],
    )
    return result
