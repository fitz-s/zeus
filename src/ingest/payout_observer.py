# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T1
#   (GATED verdict, §Consult 裁决 2026-07-13) + docs/rebuild/census_local_ledger/
#   census_chain_sources.md ("Resolution payouts — NEEDS NEW INGESTER, RPC
#   plumbing exists").
"""Read-only-on-chain ConditionalTokens payout observer (LX-T1).

Reads on-chain ``payoutDenominator(conditionId)`` / ``payoutNumerators(conditionId,
outcomeIndex)`` for conditions with current money risk or unresolved payout truth,
and for a bounded rotating slice of forecasted market families whose city-local
target date has ended,
via the SAME urllib JSON-RPC seam
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
    Historical learning is bounded and observational; it does not choose rows
    by trade, q, or realized return.
  - Reorg-safe: every read is pinned to one explicit finalized block (fetched
    first, then used as the block tag for every eth_call in that condition
    read) so terminal pruning cannot preserve a shallow-fork resolution.

Table shape and immutability/supersession invariants are owned by
src.state.schema.payout_observations_schema (see that module's docstring).
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from src.venue.polymarket_v2_adapter import (
    DEFAULT_POLYGON_RPC_URL,
    POLYGON_CTF_ADDRESS,
    V2AdapterError,
    _json_rpc_call,
    _json_rpc_batch_call_hard_deadline,
    _normalize_condition_id_bytes32,
)

logger = logging.getLogger(__name__)

RpcCall = Callable[[str, str, list[Any]], Any]

# ConditionalTokens public-mapping getter selectors:
#   mapping(bytes32 => uint256[]) public payoutNumerators;   -> payoutNumerators(bytes32,uint256)
#   mapping(bytes32 => uint256)   public payoutDenominator;  -> payoutDenominator(bytes32)
# Verified locally via eth_utils.keccak — same methodology already pinned for
# the adapter's read-only CTF selectors, and covered by an antibody test mirroring
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
FINALIZED_SOURCE = "chain_rpc_finalized_v1"
LEGACY_FINALITY_UPGRADE_BATCH_SIZE = 16
PAYOUT_LEARNING_CAP = 128
PAYOUT_LEARNING_CHUNK_SIZE = 32
PAYOUT_LEARNING_BUDGET_SECONDS = 60.0
PAYOUT_LEARNING_RPC_TIMEOUT_SECONDS = 10.0
PAYOUT_LEARNING_SLOT_SECONDS = 600


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if not math.isfinite(value) or value <= 0.0:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    return value


def _learning_now(now: datetime | str | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    value = now if isinstance(now, datetime) else datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _target_date_has_ended(
    city: str,
    target_date: str,
    now: datetime,
    *,
    city_configs: dict[str, object] | None = None,
) -> bool:
    try:
        target = datetime.fromisoformat(str(target_date)).date()
        if city_configs is None:
            from src.config import runtime_cities_by_name

            city_configs = runtime_cities_by_name()
        city_config = city_configs.get(str(city))
        timezone_name = str(getattr(city_config, "timezone", "") or "")
        if not timezone_name:
            return False
        return now.astimezone(ZoneInfo(timezone_name)).date() > target
    except (TypeError, ValueError, KeyError):
        return False


def _latest_pairs_bulk(
    conn: sqlite3.Connection,
    condition_ids: set[str],
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Read every current payout slot once, avoiding one query per condition."""
    pairs = {condition_id: [] for condition_id in condition_ids}
    if not condition_ids:
        return pairs
    if deadline_monotonic is not None:
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() >= deadline_monotonic else 0,
            1000,
        )
    try:
        rows = conn.execute(
            "WITH latest AS ("
            "  SELECT id, condition_id, outcome_index, payout_numerator, payout_denominator, "
            "         state, source, block_number, block_hash, "
            "         ROW_NUMBER() OVER ("
            "           PARTITION BY condition_id, outcome_index ORDER BY id DESC"
            "         ) AS row_rank "
            "  FROM payout_observations WHERE outcome_index IN (0, 1)"
            ") SELECT id, condition_id, outcome_index, payout_numerator, payout_denominator, "
            "         state, source, block_number, block_hash "
            "  FROM latest WHERE row_rank = 1"
        ).fetchall()
    finally:
        if deadline_monotonic is not None:
            conn.set_progress_handler(None, 0)
    fields = (
        "id", "condition_id", "outcome_index", "payout_numerator",
        "payout_denominator", "state", "source", "block_number", "block_hash",
    )
    for row in rows:
        item = dict(zip(fields, row))
        condition_id = str(item["condition_id"])
        if condition_id in pairs:
            pairs[condition_id].append(item)
    return pairs


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
    """Fetch the finalized block's (number, hash) for subsequent eth_call reads.

    Pinning payoutDenominator + payoutNumerators reads to ONE explicit block tag
    (rather than each independently hitting a moving ``latest``) means the
    block_number/block_hash recorded alongside an observation is exactly the
    irreversible state the payout numbers were read against. Polygon PoS exposes
    deterministic milestone finality through the standard ``finalized`` tag.
    """
    result = rpc_call(rpc_url, "eth_getBlockByNumber", ["finalized", False])
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
    block_marker: Optional[tuple[int, str]] = None,
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
        if block_marker is None:
            block_number, block_hash = _get_pinned_block_marker(rpc_call, rpc_url)
        else:
            block_number, block_hash = block_marker
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
) -> Optional[tuple[int, Optional[int], Optional[int], str, str, Optional[int], Optional[str]]]:
    row = conn.execute(
        "SELECT id, payout_numerator, payout_denominator, state, source, block_number, block_hash "
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
    source: str = FINALIZED_SOURCE,
    refresh_block: bool = False,
) -> Optional[int]:
    """Append one observation row, superseding the prior row iff the fact changed.

    Returns the new row id, or ``None`` if the classified fact and source
    provenance are unchanged from the latest existing observation for this
    (condition_id, outcome_index) — no-op, keeps the append-only log from
    bloating under a sustained RPC outage (repeated UNKNOWN) or a long-settled condition
    (repeated identical RESOLVED_*).
    """
    if state not in VALID_STATES:
        raise ValueError(f"invalid payout_observations state: {state!r}")

    prior = _latest_observation(conn, condition_id, outcome_index)
    if prior is not None:
        prior_id, prior_numerator, prior_denominator, prior_state, prior_source, prior_block, prior_hash = prior
        if (
            prior_state == state
            and prior_numerator == payout_numerator
            and prior_denominator == payout_denominator
            and prior_source == source
            and (not refresh_block or (prior_block, prior_hash) == (block_number, block_hash))
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



def _coherent_finalized_pair(rows: list[dict[str, Any]]) -> bool:
    if len(rows) != 2 or {row["outcome_index"] for row in rows} != {0, 1}:
        return False
    for row in rows:
        numerator, denominator, block = (
            row["payout_numerator"], row["payout_denominator"], row["block_number"]
        )
        if any(type(value) is not int for value in (numerator, denominator, block)):
            return False
        if not (denominator > 0 and 0 <= numerator <= denominator and block >= 0):
            return False
        if row.get("source") != FINALIZED_SOURCE:
            return False
        if row["state"] != (STATE_RESOLVED_ZERO if numerator == 0 else STATE_RESOLVED_NONZERO):
            return False
        if not isinstance(row["block_hash"], str) or not row["block_hash"].strip():
            return False
    a, b = rows
    return (
        a["payout_denominator"] == b["payout_denominator"]
        and a["payout_numerator"] + b["payout_numerator"] == a["payout_denominator"]
        and (a["block_number"], a["block_hash"]) == (b["block_number"], b["block_hash"])
    )


def _latest_pair(conn: sqlite3.Connection, condition_id: str) -> list[dict[str, Any]]:
    rows = []
    fields = ("id", "payout_numerator", "payout_denominator", "state", "source", "block_number", "block_hash")
    for index in (0, 1):
        row = _latest_observation(conn, condition_id, index)
        if row is not None:
            rows.append({**dict(zip(fields, row)), "outcome_index": index})
    return rows

def conditions_to_observe(conn: sqlite3.Connection) -> list[str]:
    """Return current or unresolved condition ids that still need a chain read.

    Sourced from position_current + settlement_commands — both trade-DB
    tables on the SAME connection (no cross-DB join / ATTACH needed). A binary
    condition whose latest finalized rows prove both outcomes resolved is
    immutable payout history, not recurring work. Current-money-risk positions
    are always retained; legacy terminal rows are upgraded in bounded batches.
    """
    rows = conn.execute(
        "SELECT condition_id FROM position_current "
        "WHERE condition_id IS NOT NULL AND condition_id != '' "
        "UNION "
        "SELECT condition_id FROM settlement_commands "
        "WHERE condition_id IS NOT NULL AND condition_id != ''"
    ).fetchall()
    candidates = {str(row[0]) for row in rows}
    if not candidates:
        return []

    position_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
    }
    if "phase" in position_columns:
        current_rows = conn.execute(
            "SELECT DISTINCT condition_id FROM position_current "
            "WHERE condition_id IS NOT NULL AND condition_id != '' "
            "AND phase IN ('pending_entry', 'active', 'day0_window', 'pending_exit')"
        ).fetchall()
        current_risk = {str(row[0]) for row in current_rows}
    else:
        # Compatibility for pre-lifecycle/test projections: without a phase
        # column there is no proof a position is terminal, so keep it current.
        current_risk = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT condition_id FROM position_current "
                "WHERE condition_id IS NOT NULL AND condition_id != ''"
            ).fetchall()
        }

    latest_ids: dict[str, int] = {}
    latest_rows = conn.execute(
        "WITH latest AS ("
        "  SELECT id, condition_id, outcome_index, payout_numerator, payout_denominator, state, source, block_number, block_hash,"
        "         ROW_NUMBER() OVER ("
        "           PARTITION BY condition_id, outcome_index ORDER BY id DESC"
        "         ) AS row_rank "
        "  FROM payout_observations WHERE outcome_index IN (0, 1)"
        ") "
        "SELECT id, condition_id, outcome_index, payout_numerator, payout_denominator, state, source, block_number, block_hash FROM latest WHERE row_rank = 1"
    ).fetchall()
    latest_pairs: dict[str, list[dict[str, Any]]] = {}
    fields = ("id", "condition_id", "outcome_index", "payout_numerator", "payout_denominator", "state", "source", "block_number", "block_hash")
    for row in latest_rows:
        item = dict(zip(fields, row))
        condition = str(item["condition_id"])
        latest_ids[condition] = max(latest_ids.get(condition, 0), int(item["id"]))
        latest_pairs.setdefault(condition, []).append(item)

    finalized_fact_counts: dict[str, int] = {}
    finalized_unresolved_conditions: set[str] = set()
    finalized_rows = conn.execute(
        "WITH latest AS ("
        "  SELECT condition_id, outcome_index, state,"
        "         ROW_NUMBER() OVER ("
        "           PARTITION BY condition_id, outcome_index ORDER BY id DESC"
        "         ) AS row_rank "
        "  FROM payout_observations "
        "  WHERE outcome_index IN (0, 1) AND source = ? AND state != 'UNKNOWN'"
        ") "
        "SELECT condition_id, outcome_index, state FROM latest WHERE row_rank = 1",
        (FINALIZED_SOURCE,),
    ).fetchall()
    for condition_id, outcome_index, state in finalized_rows:
        condition = str(condition_id)
        bit = 1 << int(outcome_index)
        finalized_fact_counts.setdefault(condition, 0)
        finalized_fact_counts[condition] |= bit
        if str(state) == STATE_UNRESOLVED:
            finalized_unresolved_conditions.add(condition)

    historical_terminal_counts: dict[str, int] = {}
    for condition_id, outcome_index in conn.execute(
        "SELECT condition_id, outcome_index FROM payout_observations "
        "WHERE outcome_index IN (0, 1) "
        "AND state IN ('RESOLVED_ZERO', 'RESOLVED_NONZERO')"
    ).fetchall():
        condition = str(condition_id)
        historical_terminal_counts.setdefault(condition, 0)
        historical_terminal_counts[condition] |= 1 << int(outcome_index)

    required = set(current_risk)
    finality_retry: list[str] = []
    for condition in candidates - current_risk:
        facts = finalized_fact_counts.get(condition, 0)
        # SCOPE: this condition. DRAIN: bounded observer retry. RESET: both
        # actual latest slots form one complete finalized block/payout pair.
        if _coherent_finalized_pair(latest_pairs.get(condition, [])):
            continue
        if (
            facts == 0b11 and condition in finalized_unresolved_conditions
        ) or historical_terminal_counts.get(condition, 0) != 0b11:
            required.add(condition)
        else:
            finality_retry.append(condition)
    # Upgrade old latest-block observations without recreating the former
    # all-history RPC fanout in a single scheduler cycle. A failed upgrade
    # remains in this bounded retry class instead of expanding required work.
    finality_retry.sort(key=lambda condition: (latest_ids.get(condition, 0), condition))
    required.update(finality_retry[:LEGACY_FINALITY_UPGRADE_BATCH_SIZE])
    return sorted(required)


def forecast_conditions_to_observe(
    trade_conn: sqlite3.Connection,
    forecast_conn: sqlite3.Connection,
    *,
    now: datetime | str | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Select a rotating bounded slice of every completed forecast market family.

    Forecast truth is read from the forecast-class connection only.  A discovered
    market family is eligible once its city-local target date has ended; posterior
    coverage is reported separately and never gates the chain label.  Its
    condition is removed only when the trade DB already contains one coherent
    finalized pair.  The selection cursor advances by a ten-minute UTC slot so a
    repeatedly failing condition cannot freeze the same prefix.
    """
    current = _learning_now(now)
    if deadline_monotonic is not None:
        forecast_conn.set_progress_handler(
            lambda: 1 if time.monotonic() >= deadline_monotonic else 0,
            1000,
        )
    try:
        rows = forecast_conn.execute(
            "WITH forecast_families AS ("
            "  SELECT DISTINCT city, target_date, temperature_metric "
            "  FROM forecast_posteriors"
            ") SELECT DISTINCT market.condition_id, market.city, market.target_date, "
            "       market.temperature_metric, "
            "       CASE WHEN forecast_families.city IS NULL THEN 0 ELSE 1 END "
            "         AS has_forecast "
            "FROM market_events AS market "
            "LEFT JOIN forecast_families "
            "  ON forecast_families.city = market.city "
            " AND forecast_families.target_date = market.target_date "
            " AND forecast_families.temperature_metric = market.temperature_metric "
            "WHERE market.condition_id IS NOT NULL AND TRIM(market.condition_id) != '' "
            "ORDER BY market.condition_id"
        ).fetchall()
    finally:
        if deadline_monotonic is not None:
            forecast_conn.set_progress_handler(None, 0)
    universe = [
        {
            "condition_id": str(row[0]),
            "city": str(row[1]),
            "target_date": str(row[2]),
            "temperature_metric": str(row[3]),
            "has_forecast": bool(row[4]),
        }
        for row in rows
    ]
    from src.config import runtime_cities_by_name

    city_configs = runtime_cities_by_name()
    ended = [
        item for item in universe
        if _target_date_has_ended(
            item["city"], item["target_date"], current, city_configs=city_configs
        )
    ]
    latest_pairs = _latest_pairs_bulk(
        trade_conn,
        {item["condition_id"] for item in ended},
        deadline_monotonic=deadline_monotonic,
    )
    pending = [
        item for item in ended
        if not _coherent_finalized_pair(latest_pairs[item["condition_id"]])
    ]
    pending.sort(key=lambda item: item["condition_id"])
    count = len(pending)
    cap = _positive_int_env("ZEUS_POST_TRADE_PAYOUT_LEARNING_CAP", PAYOUT_LEARNING_CAP)
    slot = int(current.timestamp()) // PAYOUT_LEARNING_SLOT_SECONDS
    stride = cap
    # A budget-limited sweep may process only a selected prefix. A stride
    # sharing a factor with N would then strand the same suffix forever.
    while count > 1 and math.gcd(stride, count) != 1:
        stride += 1
    offset = (slot * stride) % count if count else 0
    selected = [
        pending[(offset + index) % count]
        for index in range(min(cap, count))
    ] if count else []
    return {
        "universe": universe,
        "forecasted": sum(1 for item in universe if item["has_forecast"]),
        "forecast_missing": sum(1 for item in universe if not item["has_forecast"]),
        "ended_universe": ended,
        "pending": pending,
        "selected": selected,
        "slot": slot,
        "stride": stride,
        "offset": offset,
    }


def _decode_uint_result(raw: Any) -> int:
    text = str(raw)
    if not text.startswith("0x") or len(text) <= 2:
        raise V2AdapterError(f"eth_call returned unparsable/empty result: {raw!r}")
    return int(text, 16)


def _payout_call_data(
    condition_id: str,
    *,
    numerator_index: int | None = None,
    block_tag: str = "finalized",
) -> tuple[str, list[Any]]:
    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    if numerator_index is None:
        return "eth_call", [{"to": POLYGON_CTF_ADDRESS, "data": PAYOUT_DENOMINATOR_SELECTOR + condition_bytes.hex()}, block_tag]
    return "eth_call", [{
        "to": POLYGON_CTF_ADDRESS,
        "data": PAYOUT_NUMERATORS_SELECTOR + condition_bytes.hex() + format(int(numerator_index), "064x"),
    }, block_tag]


def _learning_rpc_batch(
    rpc_url: str,
    calls: list[tuple[str, list[Any]]],
    *,
    rpc_call: RpcCall,
    timeout_seconds: float,
) -> list[Any]:
    if rpc_call is _json_rpc_call:
        return _json_rpc_batch_call_hard_deadline(
            rpc_url, calls, timeout_seconds=timeout_seconds
        )
    return [rpc_call(rpc_url, method, params) for method, params in calls]


def _learning_block_marker(
    rpc_url: str,
    *,
    rpc_call: RpcCall,
    timeout_seconds: float,
) -> tuple[int, str]:
    """Pin the learning sweep block without exceeding its RPC budget."""
    if rpc_call is not _json_rpc_call:
        return _get_pinned_block_marker(rpc_call, rpc_url)
    values = _json_rpc_batch_call_hard_deadline(
        rpc_url,
        [("eth_getBlockByNumber", ["finalized", False])],
        timeout_seconds=max(0.01, timeout_seconds),
    )
    if len(values) != 1 or not isinstance(values[0], dict):
        raise V2AdapterError("learning block marker response invalid")
    marker = values[0]
    number_hex = marker.get("number")
    block_hash = marker.get("hash")
    if not number_hex or not block_hash:
        raise V2AdapterError("learning block marker missing number/hash")
    return int(str(number_hex), 16), str(block_hash)


def _append_learning_chunk(
    conn: sqlite3.Connection,
    rows: list[tuple[str, list[dict[str, Any]]]],
    *,
    observed_at: str,
) -> tuple[int, int, int]:
    appended = unchanged = unknown = 0
    if not conn.in_transaction:
        conn.execute("BEGIN")
    for condition_id, results in rows:
        incoming = [{**row, "source": FINALIZED_SOURCE} for row in results]
        refresh_block = (
            _coherent_finalized_pair(incoming)
            and not _coherent_finalized_pair(_latest_pair(conn, condition_id))
        )
        conn.execute("SAVEPOINT payout_learning_condition")
        try:
            for result in results:
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
                    refresh_block=refresh_block,
                )
                if new_id is None:
                    unchanged += 1
                else:
                    appended += 1
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT payout_learning_condition")
            conn.execute("RELEASE SAVEPOINT payout_learning_condition")
            conn.rollback()
            raise
        conn.execute("RELEASE SAVEPOINT payout_learning_condition")
        if any(result["state"] == STATE_UNKNOWN for result in results):
            unknown += 1
    conn.commit()
    return appended, unchanged, unknown


def _learning_sweep(
    trade_conn: sqlite3.Connection,
    forecast_conn: sqlite3.Connection,
    *,
    rpc_url: str,
    rpc_call: RpcCall,
    now: datetime | str | None,
) -> dict[str, Any]:
    budget = _positive_float_env(
        "ZEUS_POST_TRADE_PAYOUT_LEARNING_BUDGET_SECONDS",
        PAYOUT_LEARNING_BUDGET_SECONDS,
    )
    chunk_size = _positive_int_env(
        "ZEUS_POST_TRADE_PAYOUT_LEARNING_CHUNK_SIZE",
        PAYOUT_LEARNING_CHUNK_SIZE,
    )
    started = time.monotonic()
    deadline = started + budget
    selection = forecast_conditions_to_observe(
        trade_conn,
        forecast_conn,
        now=now,
        deadline_monotonic=deadline,
    )
    selected = selection["selected"]
    processed = unknown = deferred = appended = unchanged = 0
    block_marker: tuple[int, str] | None = None
    if selected:
        try:
            block_marker = _learning_block_marker(
                rpc_url,
                rpc_call=rpc_call,
                timeout_seconds=min(
                    PAYOUT_LEARNING_RPC_TIMEOUT_SECONDS,
                    max(0.01, deadline - time.monotonic()),
                ),
            )
        except Exception as exc:  # noqa: BLE001 — every selected family stays UNKNOWN.
            logger.warning("payout learning block marker failed: %s", exc)
    for start in range(0, len(selected), chunk_size):
        chunk = selected[start:start + chunk_size]
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        if block_marker is None:
            rows = [
                (item["condition_id"], [
                    {"outcome_index": idx, "payout_numerator": None, "payout_denominator": None,
                     "state": STATE_UNKNOWN, "block_number": None, "block_hash": None}
                    for idx in DEFAULT_OUTCOME_INDICES
                ]) for item in chunk
            ]
        else:
            block_tag = hex(block_marker[0])
            denominator_calls = [
                _payout_call_data(item["condition_id"], block_tag=block_tag)
                for item in chunk
            ]
            try:
                denominator_values = _learning_rpc_batch(
                    rpc_url,
                    denominator_calls,
                    rpc_call=rpc_call,
                    timeout_seconds=min(PAYOUT_LEARNING_RPC_TIMEOUT_SECONDS, max(0.01, deadline - time.monotonic())),
                )
            except Exception as exc:  # noqa: BLE001 — partial/error is UNKNOWN.
                logger.warning("payout learning denominator batch failed: %s", exc)
                denominator_values = []
            resolved: list[tuple[int, int]] = []
            rows_by_condition: dict[str, list[dict[str, Any]]] = {}
            for index, item in enumerate(chunk):
                try:
                    denominator = _decode_uint_result(denominator_values[index])
                except (IndexError, TypeError, ValueError, V2AdapterError):
                    denominator = None
                if denominator is None:
                    rowset = [{"outcome_index": idx, "payout_numerator": None, "payout_denominator": None,
                               "state": STATE_UNKNOWN, "block_number": block_marker[0], "block_hash": block_marker[1]}
                              for idx in DEFAULT_OUTCOME_INDICES]
                elif denominator == 0:
                    rowset = [{"outcome_index": idx, "payout_numerator": None, "payout_denominator": 0,
                               "state": STATE_UNRESOLVED, "block_number": block_marker[0], "block_hash": block_marker[1]}
                              for idx in DEFAULT_OUTCOME_INDICES]
                else:
                    rowset = [{"outcome_index": idx, "payout_numerator": None, "payout_denominator": denominator,
                               "state": STATE_UNKNOWN, "block_number": block_marker[0], "block_hash": block_marker[1]}
                              for idx in DEFAULT_OUTCOME_INDICES]
                    for idx in DEFAULT_OUTCOME_INDICES:
                        resolved.append((index, idx))
                rows_by_condition[item["condition_id"]] = rowset
            numerator_calls = [
                _payout_call_data(
                    chunk[index]["condition_id"],
                    numerator_index=outcome_index,
                    block_tag=block_tag,
                )
                for index, outcome_index in resolved
            ]
            if numerator_calls and time.monotonic() < deadline:
                try:
                    numerator_values = _learning_rpc_batch(
                        rpc_url,
                        numerator_calls,
                        rpc_call=rpc_call,
                        timeout_seconds=min(PAYOUT_LEARNING_RPC_TIMEOUT_SECONDS, max(0.01, deadline - time.monotonic())),
                    )
                except Exception as exc:  # noqa: BLE001 — partial/error is UNKNOWN.
                    logger.warning("payout learning numerator batch failed: %s", exc)
                    numerator_values = []
                for result_index, (condition_index, outcome_index) in enumerate(resolved):
                    row = rows_by_condition[chunk[condition_index]["condition_id"]][outcome_index]
                    try:
                        numerator = _decode_uint_result(numerator_values[result_index])
                    except (IndexError, TypeError, ValueError, V2AdapterError):
                        numerator = None
                    if numerator is not None:
                        row["payout_numerator"] = numerator
                        row["state"] = STATE_RESOLVED_ZERO if numerator == 0 else STATE_RESOLVED_NONZERO
            rows = [(item["condition_id"], rows_by_condition[item["condition_id"]]) for item in chunk]
        observed_at = datetime.now(timezone.utc).isoformat()
        chunk_appended, chunk_unchanged, chunk_unknown = _append_learning_chunk(
            trade_conn, rows, observed_at=observed_at
        )
        processed += len(chunk)
        appended += chunk_appended
        unchanged += chunk_unchanged
        unknown += chunk_unknown
    if processed < len(selected):
        deferred += len(selected) - processed
    elapsed = max(0.0, time.monotonic() - started)
    return {
        "universe": len(selection["universe"]),
        "forecasted": selection["forecasted"],
        "forecast_missing": selection["forecast_missing"],
        "ended_universe": len(selection["ended_universe"]),
        "pending": len(selection["pending"]),
        "selected": len(selected),
        "processed": processed,
        "unknown": unknown,
        "deferred": deferred,
        "appended": appended,
        "unchanged": unchanged,
        "slot": selection["slot"],
        "stride": selection["stride"],
        "offset": selection["offset"],
        "budget_seconds": budget,
        "elapsed_seconds": elapsed,
        "remaining_seconds": max(0.0, deadline - time.monotonic()),
    }


def sweep_and_record(
    conn: sqlite3.Connection,
    *,
    rpc_url: str,
    rpc_call: RpcCall,
    outcome_indices: tuple[int, ...] = DEFAULT_OUTCOME_INDICES,
    now: Optional[str] = None,
) -> dict[str, int]:
    """Sweep every current-risk or unresolved condition and append observations.

    All RPC reads finish before the first DML statement.  This ordering is
    load-bearing: one sweep can take many minutes, while the append phase is a
    short local transaction.  Reversing the order holds the trades-DB WAL
    writer lock across hundreds of network calls and prevents order receipts,
    collateral releases, and command recovery from committing.

    Caller owns the append transaction (commit/rollback), per INV-37.
    """
    condition_ids = conditions_to_observe(conn)
    if not condition_ids:
        return {"conditions": 0, "appended": 0, "unchanged": 0}
    # One finalized snapshot makes the whole sweep one causal chain cut and
    # removes one block-header RPC per condition. Failure aborts before DML.
    block_marker = _get_pinned_block_marker(rpc_call, rpc_url)
    observations: list[tuple[str, list[dict[str, Any]]]] = []
    for condition_id in condition_ids:
        observations.append((condition_id, read_condition_payout(
            condition_id,
            rpc_url=rpc_url,
            rpc_call=rpc_call,
            outcome_indices=outcome_indices,
            block_marker=block_marker,
        )))

    observed_at = now or datetime.now(timezone.utc).isoformat()
    appended = 0
    unchanged = 0
    if not conn.in_transaction:
        conn.execute("BEGIN")
    for condition_id, results in observations:
        incoming = [{**row, "source": FINALIZED_SOURCE} for row in results]
        refresh_block = (
            _coherent_finalized_pair(incoming)
            and not _coherent_finalized_pair(_latest_pair(conn, condition_id))
        )
        conn.execute("SAVEPOINT payout_observer_condition")
        try:
            for result in results:
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
                    refresh_block=refresh_block,
                )
                if new_id is None:
                    unchanged += 1
                else:
                    appended += 1
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT payout_observer_condition")
            conn.execute("RELEASE SAVEPOINT payout_observer_condition")
            raise
        conn.execute("RELEASE SAVEPOINT payout_observer_condition")

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
    forecast_conn: Optional[sqlite3.Connection] = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Scheduler entry point (post_trade_capital_daemon, ~10-min cadence).

    Read-only on chain: it has no signing or transaction-broadcast capability.
    Locally it opens trades-DB (unless injected for testing), completes every
    RPC read before beginning the append transaction, then commits.  The
    current-risk sweep is committed before the bounded forecast-family learning
    sweep begins, so a learning failure cannot roll back primary risk evidence.
    A forecast connection is read-only and injectable for tests.
    """
    from src.state.db import get_trade_connection

    own_conn = conn is None
    # An injected trade connection is the explicit primary-only test/runtime
    # seam.  Open the forecast reader by default only for the real daemon-owned
    # trade connection or when the caller explicitly injects one.
    own_forecast_conn = forecast_conn is None and own_conn
    if own_conn:
        conn = get_trade_connection(write_class="live")
    resolved_rpc_call: RpcCall = rpc_call if rpc_call is not None else _json_rpc_call
    resolved_rpc_url = rpc_url or os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)

    primary_result: dict[str, Any]
    try:
        result = sweep_and_record(conn, rpc_url=resolved_rpc_url, rpc_call=resolved_rpc_call)
        conn.commit()
        primary_result = dict(result)
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        finally:
            if own_conn:
                conn.close()
        raise
    learning_result: dict[str, Any]
    try:
        if own_forecast_conn:
            from src.state.db import get_forecasts_connection_read_only

            forecast_conn = get_forecasts_connection_read_only()
        if forecast_conn is None:
            learning_result = {
                "universe": 0,
                "forecasted": 0,
                "forecast_missing": 0,
                "ended_universe": 0,
                "pending": 0,
                "selected": 0,
                "processed": 0,
                "unknown": 0,
                "deferred": 0,
                "appended": 0,
                "unchanged": 0,
                "budget_seconds": 0.0,
                "elapsed_seconds": 0.0,
                "remaining_seconds": 0.0,
                "status": "skipped_no_forecast_connection",
            }
        else:
            learning_result = _learning_sweep(
                conn,
                forecast_conn,
                rpc_url=resolved_rpc_url,
                rpc_call=resolved_rpc_call,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001 — primary commit remains durable.
        logger.exception("payout learning sweep failed after primary commit")
        learning_result = {
            "universe": None,
            "forecasted": None,
            "forecast_missing": None,
            "ended_universe": None,
            "pending": None,
            "selected": None,
            "processed": None,
            "unknown": None,
            "deferred": None,
            "appended": None,
            "unchanged": None,
            "budget_seconds": None,
            "elapsed_seconds": None,
            "remaining_seconds": None,
            "error": f"{type(exc).__name__}:{exc}",
        }
    finally:
        if own_forecast_conn and forecast_conn is not None:
            forecast_conn.close()
        if own_conn and conn is not None:
            conn.close()
    combined = {
        **primary_result,
        "universe": learning_result.get("universe", 0),
        "forecasted": learning_result.get("forecasted", 0),
        "forecast_missing": learning_result.get("forecast_missing", 0),
        "ended_universe": learning_result.get("ended_universe", 0),
        "pending": learning_result.get("pending", 0),
        "selected": learning_result.get("selected", 0),
        "processed": learning_result.get("processed", 0),
        "unknown": learning_result.get("unknown", 0),
        "deferred": learning_result.get("deferred", 0),
        "learning": learning_result,
    }
    logger.info(
        "payout_observer_cycle: primary_conditions=%d primary_appended=%d "
        "learning_universe=%s pending=%s selected=%s processed=%s unknown=%s deferred=%s",
        primary_result["conditions"], primary_result["appended"],
        combined["universe"], combined["pending"], combined["selected"],
        combined["processed"], combined["unknown"], combined["deferred"],
    )
    return combined
