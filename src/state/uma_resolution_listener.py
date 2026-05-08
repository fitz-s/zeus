# Created: 2026-05-04
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A5 (UMA on-chain Settle listener) + Bug review Finding F (phase observability boundary, distinguish onchain_resolved from heuristic POST_TRADING).
# Parser fix 2026-05-07: condition_id derived from ancillaryData+requester via CTF keccak formula (topics[1] is requester addr, not conditionId). blockTimestamp fetched via eth_getBlockByNumber (not in eth_getLogs response).
"""UMA Optimistic Oracle resolution listener.

Pre-A5 the cycle runtime collapsed POST_TRADING and RESOLVED into one
phase: any market whose ``endDate`` had passed was treated as
RESOLVED-equivalent for dispatch. That collapse is wrong on two axes:

1. UMA OO settles ~14h after endDate (proposePrice + 7200s liveness +
   network confirmations + voter proposal), not at endDate exactly.
   A market in the [endDate, UMA-Settle] window is POST_TRADING but
   NOT yet RESOLVED — settlement bin and final value are still
   ambiguous.
2. UMA disputes can re-open settlement; "resolved" is the fact of the
   on-chain Settle event, not a guess from the wall clock.

This module subscribes to the UMA OO ``SettlementResolved`` event
(or equivalent — see ``UMA_OO_SETTLE_EVENT`` constant for the actual
topic hash) for tracked condition_ids and writes resolutions to a
local ``uma_resolution`` table. Cycle runtime reads that table to
distinguish RESOLVED from POST_TRADING in MarketPhaseEvidence.

Architecture
------------
The listener is intentionally split into:

  - **Pluggable RPC client** (``UmaRpcClient`` ABC). Production wires a
    real httpx-based JSON-RPC client; tests inject a fake client that
    returns synthetic event logs.
  - **Pure parser** (``parse_settle_event(log_entry) -> ResolvedMarket``)
    that decodes a raw log entry into a typed record.
  - **Persistence** (``record_resolution(conn, resolution)``) that writes
    to ``uma_resolution`` table via atomic upsert.
  - **Poller** (``poll_uma_resolutions(condition_ids, *, rpc_client, conn)``)
    that ties the three together and is callable from cron, the daemon
    boot path, or a future supervisor task.

Default behavior with no RPC client wired
-----------------------------------------
``poll_uma_resolutions(..., rpc_client=None)`` returns ``[]`` and writes
no resolutions. This is the **default production state today**: the
listener exists structurally, MarketPhaseEvidence's ``onchain_resolved``
status remains reachable through the API, and the dispatch path is
correct — but no resolutions are observed until ops wires the RPC
client.

Why this is acceptable: POST_TRADING already carries
``kelly_phase_overrides[post_trading] = 0.0`` in the StrategyProfile
registry (PLAN.md §A4). Distinguishing RESOLVED from POST_TRADING is
a refinement — both block live entries — so missing on-chain truth is
observability-only, not live-affecting. The follow-up packet that wires
the real RPC client only needs to update settings.json and inject a
``UmaHttpRpcClient`` instance into the boot path.

Wiring path for production (operator action)
--------------------------------------------
1. Set ``settings["uma"]["polygon_rpc_url"]`` to a Polygon RPC endpoint.
2. Set ``settings["uma"]["oo_contract_address"]`` to the Polymarket UMA
   OO instance (or v2/v3 successor) at the time of cutover.
3. Verify the event signature matches ``UMA_OO_SETTLE_EVENT_SIGNATURE``
   below; if Polymarket migrates to a v3 OO with renamed event, update
   the constant + tests in the same packet.
4. Add a cron entry calling ``poll_uma_resolutions`` once per minute
   (UMA settle latency is ~14h after endDate; per-minute polling is
   conservative, not load-pinning).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)


def _keccak256(data: bytes) -> bytes:
    """keccak256 hash via eth_hash (installed in venv).

    Raises ``ValueError`` if eth_hash is not installed. The wrapper converts
    the underlying ``ImportError`` so callers (e.g. ``derive_condition_id``)
    have a single failure mode to catch — see the docstring of
    ``derive_condition_id`` which documents ``ValueError`` only.
    """
    try:
        from eth_hash.auto import keccak  # type: ignore[import]
    except ImportError as exc:
        raise ValueError(
            "eth_hash unavailable; cannot derive UMA condition_id keccak256 hash"
        ) from exc
    return keccak(data)


def derive_condition_id(requester: str, ancillary_data: bytes) -> str:
    """Derive the Polymarket conditionId from a UMA Settle event.

    Formula (CTF Adapter):
        questionId  = keccak256(ancillaryData)
        conditionId = keccak256(abi.encodePacked(requester_addr_20, questionId_32, uint256(2)_32))

    Parameters
    ----------
    requester:
        The requester address from topics[1] of the Settle event (20-byte hex, with or
        without 0x prefix).  This is the CTF adapter address, not a condition_id.
    ancillary_data:
        Raw ancillaryData bytes decoded from the ABI-encoded ``data`` field of the log.

    Returns
    -------
    str
        Lower-case hex condition_id prefixed with "0x".

    Raises
    ------
    ValueError
        If requester is malformed or eth_hash is unavailable.
    """
    # Normalise requester to 20 raw bytes
    addr = requester.lower().removeprefix("0x")
    if len(addr) == 64:
        # Full 32-byte padded topic (e.g. "0x000000000000000000000000<20-byte-addr>")
        addr = addr[-40:]
    if len(addr) != 40:
        raise ValueError(f"derive_condition_id: requester hex is {len(addr)} chars, expected 40: {requester!r}")
    requester_bytes = bytes.fromhex(addr)

    question_id = _keccak256(ancillary_data)  # 32 bytes
    outcome_slot_count = (2).to_bytes(32, "big")  # uint256(2)
    packed = requester_bytes + question_id + outcome_slot_count
    condition_id_bytes = _keccak256(packed)
    return "0x" + condition_id_bytes.hex()


def decode_ancillary_data(data_hex: str) -> bytes:
    """Decode the ancillaryData bytes from an ABI-encoded UMA Settle ``data`` field.

    The Settle event non-indexed payload is ABI-encoded as:
        (bytes32 identifier, uint256 timestamp, bytes ancillaryData, int256 price, uint256 payout)

    Slot layout (each slot = 32 bytes = 64 hex chars):
        [0]  identifier  (bytes32)
        [1]  timestamp   (uint256)
        [2]  offset to ancillaryData (uint256, relative to start of data)
        [3]  price       (int256)
        [4]  payout      (uint256)
        [offset/32] length of ancillaryData
        [offset/32 + 1 .. ] ancillaryData bytes (padded to 32-byte boundary)

    Parameters
    ----------
    data_hex:
        Full ``data`` field value from the eth_getLogs entry (with or without "0x").

    Returns
    -------
    bytes
        Raw ancillaryData bytes.  Returns b"" on any decode failure (caller
        should log + skip rather than propagate).
    """
    try:
        hex_str = data_hex[2:] if data_hex.startswith("0x") else data_hex
        if len(hex_str) < 320:  # Need at least 5 slots
            return b""
        offset = int(hex_str[128:192], 16)  # slot [2] = byte offset of ancillaryData
        ad_pos = offset * 2  # convert byte offset to hex-char offset
        if len(hex_str) < ad_pos + 64:
            return b""
        ad_len = int(hex_str[ad_pos:ad_pos + 64], 16)
        if ad_len == 0:
            return b""
        ad_hex = hex_str[ad_pos + 64: ad_pos + 64 + ad_len * 2]
        if len(ad_hex) < ad_len * 2:
            return b""
        return bytes.fromhex(ad_hex)
    except Exception:
        return b""


# UMA Optimistic Oracle V2 ``Settle`` event signature.
# Verified 2026-05-07 via eth_getLogs on Polygon + UMA Finder contract:
#   Finder(0x09aea4b2242abC8bb4BB78D537A67a245A7bEC64).getImplementationAddress("OptimisticOracleV2")
#   => 0xee3afe347d5c74317041e2618c49534daf887c24  (OO V2 on Polygon)
# Event: Settle(address indexed requester, address indexed proposer,
#               address indexed disputer, bytes32 identifier,
#               uint256 timestamp, bytes ancillaryData, int256 price, uint256 payout)
# keccak256("Settle(address,address,address,bytes32,uint256,bytes,int256,uint256)")
#   = 0x3f384afb4bd9f0aef0298c80399950011420eb33b0e1a750b20966270247b9a0
# Confirmed live: 5700+ Wunderground weather market settlements found in 90-day scan.
# NOTE: topics[0]=sig, topics[1]=requester (CTF adapter), topics[2]=proposer,
#       topics[3]=disputer. condition_id is NOT in topics — derived from
#       ancillaryData + requester via CTF adapter's internal questionId mapping.
#       Backfill condition_id extraction requires that mapping layer (see PLAN §A5).
# IF Polymarket migrates to OO V3 or renames the event, update HERE +
# the matching test in tests/test_uma_resolution_listener.py.
UMA_OO_SETTLE_EVENT_NAME: str = "Settle"
UMA_OO_SETTLE_EVENT_SIGNATURE: str = (
    "0x3f384afb4bd9f0aef0298c80399950011420eb33b0e1a750b20966270247b9a0"
)


# ── data types ─────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class ResolvedMarket:
    """A single observed UMA resolution. Persisted as one
    ``uma_resolution`` row."""
    condition_id: str
    resolved_value: int
    """UMA returns the proposePrice as an integer encoded value;
    Polymarket maps {0,1} → {NO,YES} for binary markets, but weather
    markets use an integer temperature payload. Caller decodes per
    market type."""
    tx_hash: str
    block_number: int
    resolved_at_utc: datetime
    """Block timestamp of the Settle event, in UTC."""
    raw_log: dict
    """Verbatim log entry. Stored so a future schema migration can
    re-derive fields without re-querying the chain."""


# ── pluggable RPC client ───────────────────────────────────────────── #


class UmaRpcClient(ABC):
    """Abstract interface for the JSON-RPC layer.

    Production: ``UmaHttpRpcClient`` (Polygon RPC over httpx) — landed
    when operator wires the listener.
    Tests: ``FakeUmaRpcClient`` — returns a fixed list of synthetic
    log entries.
    """

    @abstractmethod
    def get_logs(
        self,
        *,
        contract_address: str,
        topic0: str,
        condition_ids: Sequence[str],
        from_block: int,
        to_block: Optional[int] = None,
    ) -> list[dict]:
        """Return raw log entries matching the filter.

        Returns an empty list when the window contains no matching logs
        (legitimate empty result — caller must not advance cursor on raise).
        Raises RuntimeError on RPC transport errors or malformed responses so
        the caller can distinguish fetch failure from a legitimately empty
        block window (PR #84 Codex P1: cursor must not advance after error).
        """

    @abstractmethod
    def get_block_timestamp(self, block_number: int) -> int:
        """Return the Unix timestamp (seconds) for ``block_number``.

        Production: eth_getBlockByNumber RPC call.
        Tests: return a synthetic value.
        Must NOT raise — return 0 on failure (caller treats as unknown).
        """


class UmaHttpRpcClient(UmaRpcClient):
    """Production Polygon JSON-RPC client using httpx.

    Fetches UMA OO Settle events via eth_getLogs and block timestamps
    via eth_getBlockByNumber (Tenderly/public RPC does not include
    blockTimestamp in eth_getLogs responses).

    Block timestamps are cached by block number to minimise RPC round-trips
    when many events share the same block (common for batch Settle calls).
    """

    def __init__(self, rpc_url: str, *, timeout: float = 30.0) -> None:
        self._rpc_url = rpc_url
        self._timeout = timeout
        self._block_ts_cache: dict[int, int] = {}

    def _post(self, payload: dict) -> dict:
        import httpx  # type: ignore[import]
        resp = httpx.post(self._rpc_url, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def get_logs(
        self,
        *,
        contract_address: str,
        topic0: str,
        condition_ids: Sequence[str],
        from_block: int,
        to_block: Optional[int] = None,
    ) -> list[dict]:
        """eth_getLogs for the Settle event over [from_block, to_block].

        ``condition_ids`` is unused at the RPC level — Polymarket Settle events
        do not index condition_id as a topic. The caller (poll or backfill)
        must filter by matching derived condition_ids after parse_settle_event().
        """
        params: dict = {
            "address": contract_address,
            "topics": [topic0],
            "fromBlock": hex(from_block),
        }
        if to_block is not None:
            params["toBlock"] = hex(to_block)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [params],
        }
        try:
            result = self._post(payload)
            logs = result.get("result") or []
            if not isinstance(logs, list):
                logger.warning("UmaHttpRpcClient.get_logs: unexpected result type %s", type(logs))
                raise RuntimeError(
                    f"UmaHttpRpcClient.get_logs: unexpected result type {type(logs)}"
                )
            return logs
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("UmaHttpRpcClient.get_logs failed: %s", exc)
            raise RuntimeError(f"UmaHttpRpcClient.get_logs RPC error: {exc}") from exc

    def get_block_timestamp(self, block_number: int) -> int:
        """Fetch block timestamp via eth_getBlockByNumber, cached by block."""
        if block_number in self._block_ts_cache:
            return self._block_ts_cache[block_number]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getBlockByNumber",
            "params": [hex(block_number), False],
        }
        try:
            result = self._post(payload)
            block = result.get("result") or {}
            ts_raw = block.get("timestamp", "0x0")
            ts = int(ts_raw, 16) if isinstance(ts_raw, str) else int(ts_raw)
            self._block_ts_cache[block_number] = ts
            return ts
        except Exception as exc:
            logger.warning("UmaHttpRpcClient.get_block_timestamp(%d) failed: %s", block_number, exc)
            return 0


# ── parser ─────────────────────────────────────────────────────────── #


def parse_settle_event(
    log_entry: dict,
    *,
    block_timestamp: Optional[int] = None,
) -> ResolvedMarket:
    """Decode a raw eth_getLogs entry into a ``ResolvedMarket``.

    Bug fixes (2026-05-07):
    1. condition_id is derived from ancillaryData + requester (topics[1] is the
       requester address, NOT the condition_id — the CTF adapter stores condition_id
       as keccak256(encodePacked(requester, questionId, 2))).
    2. blockTimestamp is NOT returned by eth_getLogs on Polygon/Tenderly — caller
       must supply it via the ``block_timestamp`` parameter (fetched separately
       via eth_getBlockByNumber).

    Parameters
    ----------
    log_entry:
        Raw dict from eth_getLogs.
    block_timestamp:
        Unix timestamp (seconds) of the block, fetched via eth_getBlockByNumber.
        Required — raises ValueError if None and not present in log_entry
        (kept as optional so tests that pre-inject blockTimestamp still pass).

    Raises ``ValueError`` on shape mismatch — caller should log + skip
    rather than fabricate fields.
    """
    if not isinstance(log_entry, dict):
        raise ValueError(f"log_entry must be dict, got {type(log_entry).__name__}")

    topics = log_entry.get("topics") or []
    if not topics or len(topics) < 2:
        raise ValueError(
            f"log_entry must have at least 2 topics (sig + indexed requester); "
            f"got {len(topics)}"
        )

    # topics[1] = requester address (CTF adapter), padded to 32 bytes.
    # condition_id is derived via CTF formula: keccak256(encodePacked(requester, questionId, 2))
    requester_topic = str(topics[1])
    data_field = str(log_entry.get("data") or "0x")
    ancillary_data = decode_ancillary_data(data_field)
    try:
        condition_id = derive_condition_id(requester_topic, ancillary_data)
    except (ValueError, ImportError) as exc:
        raise ValueError(f"condition_id derivation failed: {exc}") from exc

    # Resolved value: price field, slot [3] in the ABI-encoded data (int256).
    # For weather markets, price is the temperature bin boundary encoded as
    # a scaled integer; for binary YES/NO markets it is 0 or 1e18.
    try:
        hex_str = data_field[2:] if data_field.startswith("0x") else data_field
        if len(hex_str) >= 256:
            # slot [3] = chars 192..256
            price_hex = hex_str[192:256]
            raw_int = int(price_hex, 16)
            # int256 sign handling: if high bit set, it's negative
            if raw_int >= (1 << 255):
                raw_int -= (1 << 256)
            resolved_value = raw_int
        elif hex_str:
            tail = hex_str[-64:].zfill(64)
            resolved_value = int(tail, 16)
        else:
            resolved_value = 0
    except (TypeError, ValueError) as exc:
        raise ValueError(f"resolved_value decode failed: {exc}") from exc

    block_number_raw = log_entry.get("blockNumber", "0x0")
    if isinstance(block_number_raw, str) and block_number_raw.startswith("0x"):
        block_number = int(block_number_raw, 16)
    elif isinstance(block_number_raw, int):
        block_number = block_number_raw
    else:
        raise ValueError(f"blockNumber unparseable: {block_number_raw!r}")

    tx_hash = str(log_entry.get("transactionHash") or "")
    if not tx_hash:
        raise ValueError("transactionHash missing")

    # blockTimestamp: prefer explicit parameter, then log_entry field (for test compatibility),
    # then raise (eth_getLogs does NOT return blockTimestamp on Polygon/Tenderly).
    ts_source = block_timestamp
    if ts_source is None:
        block_ts = log_entry.get("blockTimestamp")
        if isinstance(block_ts, int):
            ts_source = block_ts
        elif isinstance(block_ts, str):
            try:
                ts_source = int(block_ts, 16) if block_ts.startswith("0x") else int(block_ts)
            except ValueError as exc:
                raise ValueError(f"blockTimestamp unparseable: {block_ts!r}") from exc
        else:
            raise ValueError(
                "blockTimestamp not available: eth_getLogs does not return blockTimestamp "
                "on Polygon. Caller must fetch via eth_getBlockByNumber and pass as "
                "block_timestamp parameter."
            )
    resolved_at_utc = datetime.fromtimestamp(ts_source, tz=timezone.utc)

    return ResolvedMarket(
        condition_id=condition_id,
        resolved_value=resolved_value,
        tx_hash=tx_hash,
        block_number=block_number,
        resolved_at_utc=resolved_at_utc,
        raw_log=dict(log_entry),
    )


# ── persistence ────────────────────────────────────────────────────── #


def init_uma_resolution_schema(conn: sqlite3.Connection) -> None:
    """Create the ``uma_resolution`` table if missing. Idempotent —
    safe to call from boot path or test setup."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uma_resolution (
            condition_id TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            block_number INTEGER NOT NULL,
            resolved_value INTEGER NOT NULL,
            resolved_at_utc TEXT NOT NULL,
            raw_log_json TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            PRIMARY KEY (condition_id, tx_hash)
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_uma_resolution_condition "
        "ON uma_resolution(condition_id);"
    )


def record_resolution(conn: sqlite3.Connection, resolution: ResolvedMarket) -> None:
    """Upsert a resolution row. (condition_id, tx_hash) is the unique
    key — the same Settle event will be observed multiple times if the
    listener polls overlapping windows; the OR IGNORE keeps the first
    row stable."""
    init_uma_resolution_schema(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO uma_resolution
            (condition_id, tx_hash, block_number, resolved_value,
             resolved_at_utc, raw_log_json, observed_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolution.condition_id,
            resolution.tx_hash,
            resolution.block_number,
            resolution.resolved_value,
            resolution.resolved_at_utc.isoformat(),
            json.dumps(resolution.raw_log, sort_keys=True, default=str),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


# ── cursor: last-scanned block ─────────────────────────────────────── #
#
# eth_getLogs from genesis on every tick is O(chain) and gets rate-limited /
# crashes RPC nodes. We persist a per-(contract_address) cursor so the daemon
# tick only scans the new range since the previous successful poll.


def init_uma_cursor_schema(conn: sqlite3.Connection) -> None:
    """Create the ``uma_resolution_cursor`` table if missing. Idempotent."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uma_resolution_cursor (
            contract_address TEXT PRIMARY KEY,
            last_scanned_block INTEGER NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        """
    )


def get_last_scanned_block(
    conn: sqlite3.Connection, contract_address: str
) -> Optional[int]:
    """Return the last successfully-scanned block for ``contract_address``,
    or ``None`` if no cursor row exists yet (caller decides initial value)."""
    init_uma_cursor_schema(conn)
    row = conn.execute(
        "SELECT last_scanned_block FROM uma_resolution_cursor WHERE contract_address = ?",
        (contract_address.lower(),),
    ).fetchone()
    if row is None:
        return None
    return int(row[0])


def set_last_scanned_block(
    conn: sqlite3.Connection, contract_address: str, block_number: int
) -> None:
    """Upsert the last-scanned block cursor. Caller commits the connection."""
    init_uma_cursor_schema(conn)
    conn.execute(
        """
        INSERT INTO uma_resolution_cursor (contract_address, last_scanned_block, updated_at_utc)
        VALUES (?, ?, ?)
        ON CONFLICT(contract_address) DO UPDATE SET
            last_scanned_block = excluded.last_scanned_block,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            contract_address.lower(),
            int(block_number),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def lookup_resolution(
    conn: sqlite3.Connection, condition_id: str
) -> Optional[ResolvedMarket]:
    """Return the most recent observed resolution for ``condition_id``,
    or ``None`` if the listener has not seen a Settle event for this
    market. Cycle runtime calls this at decision time to distinguish
    RESOLVED from POST_TRADING."""
    init_uma_resolution_schema(conn)
    row = conn.execute(
        """
        SELECT condition_id, tx_hash, block_number, resolved_value,
               resolved_at_utc, raw_log_json
        FROM uma_resolution
        WHERE condition_id = ?
        ORDER BY block_number DESC
        LIMIT 1
        """,
        (condition_id,),
    ).fetchone()
    if row is None:
        return None
    return ResolvedMarket(
        condition_id=row[0],
        tx_hash=row[1],
        block_number=int(row[2]),
        resolved_value=int(row[3]),
        resolved_at_utc=datetime.fromisoformat(row[4]),
        raw_log=json.loads(row[5]),
    )


# ── poller ─────────────────────────────────────────────────────────── #


def poll_uma_resolutions(
    *,
    condition_ids: Iterable[str],
    contract_address: str,
    rpc_client: Optional[UmaRpcClient] = None,
    conn: Optional[sqlite3.Connection] = None,
    from_block: int = 0,
    to_block: Optional[int] = None,
) -> list[ResolvedMarket]:
    """Poll the UMA OO Settle event for tracked condition_ids and persist
    new resolutions.

    ``rpc_client=None`` (default today): returns ``[]`` and writes
    nothing. The listener is structurally present so cycle_runtime can
    call ``lookup_resolution`` without conditional logic, but until ops
    wires a real RPC client production sees no resolutions and falls
    back to heuristic POST_TRADING — which still blocks live entries
    via the StrategyProfile registry's ``kelly_phase_overrides``
    (POST_TRADING == 0.0). No live behavior change.

    ``rpc_client`` supplied: pulls logs, parses each into a
    ``ResolvedMarket``, persists via ``record_resolution``, and returns
    the list of resolutions observed in this poll cycle. Caller decides
    whether to fan-out events (e.g., trigger an exit re-eval).
    """
    if rpc_client is None:
        return []

    cond_list = list(condition_ids)
    if not cond_list:
        return []

    raw_logs = rpc_client.get_logs(
        contract_address=contract_address,
        topic0=UMA_OO_SETTLE_EVENT_SIGNATURE,
        condition_ids=cond_list,
        from_block=from_block,
        to_block=to_block,
    )
    resolutions: list[ResolvedMarket] = []
    for log_entry in raw_logs:
        try:
            # Fetch block timestamp separately — eth_getLogs on Polygon/Tenderly
            # does not include blockTimestamp in the response (bug fix 2026-05-07).
            block_number_raw = log_entry.get("blockNumber", "0x0")
            if isinstance(block_number_raw, str) and block_number_raw.startswith("0x"):
                blk = int(block_number_raw, 16)
            elif isinstance(block_number_raw, int):
                blk = block_number_raw
            else:
                blk = 0
            block_ts = rpc_client.get_block_timestamp(blk) if blk else 0

            resolution = parse_settle_event(log_entry, block_timestamp=block_ts or None)

            # Filter: only keep logs whose derived condition_id is tracked
            if cond_list and resolution.condition_id not in cond_list:
                continue
        except ValueError as exc:
            logger.warning(
                "uma_resolution_listener: skipped malformed log entry: %s "
                "(log=%r)",
                exc,
                log_entry,
            )
            continue
        resolutions.append(resolution)
        if conn is not None:
            try:
                record_resolution(conn, resolution)
            except sqlite3.Error as exc:
                logger.warning(
                    "uma_resolution_listener: persistence failed for tx=%s: %s",
                    resolution.tx_hash,
                    exc,
                )
    return resolutions
