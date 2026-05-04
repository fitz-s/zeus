# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A5 (UMA on-chain Settle listener) + Bug review Finding F (phase observability boundary, distinguish onchain_resolved from heuristic POST_TRADING).
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


# UMA Optimistic Oracle V2 ``SettlementResolved`` event signature.
# Hex prefix is what eth_getLogs filters on (topic[0]).
# IF Polymarket migrates to OO V3 or renames the event, update HERE +
# the matching test in tests/test_uma_resolution_listener.py.
UMA_OO_SETTLE_EVENT_NAME: str = "SettlementResolved"
UMA_OO_SETTLE_EVENT_SIGNATURE: str = (
    # Placeholder — operator MUST verify against the live UMA OO contract
    # before flipping the listener on. The signature is keccak256 of
    # the canonical event signature; computed offline.
    # Tracked as a config knob so a v3 migration doesn't require a code
    # patch + redeploy.
    "0x0000000000000000000000000000000000000000000000000000000000000000"
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
        """Return raw log entries matching the filter. Must NOT raise on
        empty result; an empty list is the no-resolutions case."""


# ── parser ─────────────────────────────────────────────────────────── #


def parse_settle_event(log_entry: dict) -> ResolvedMarket:
    """Decode a raw eth_getLogs entry into a ``ResolvedMarket``.

    Expects the canonical Polygon log shape::

        {
          "address": "0x...",
          "topics": ["0x<sig>", "0x<condition_id_indexed>", ...],
          "data": "0x<encoded payload>",
          "blockNumber": "0x<hex>",
          "transactionHash": "0x...",
          "blockTimestamp": <int seconds>,
        }

    Raises ``ValueError`` on shape mismatch — caller should log + skip
    rather than fabricate fields.
    """
    if not isinstance(log_entry, dict):
        raise ValueError(f"log_entry must be dict, got {type(log_entry).__name__}")

    topics = log_entry.get("topics") or []
    if not topics or len(topics) < 2:
        raise ValueError(
            f"log_entry must have at least 2 topics (sig + indexed condition_id); "
            f"got {len(topics)}"
        )

    # topics[1] is the indexed condition_id (32-byte left-padded hex).
    condition_id = str(topics[1]).lower()
    if not condition_id.startswith("0x"):
        condition_id = "0x" + condition_id

    data_field = log_entry.get("data", "0x")
    # Resolved value is in the trailing 32 bytes of the data field for
    # binary markets; for weather markets it's a 256-bit signed int.
    # The exact decoding depends on the Polymarket OO version — caller
    # MUST verify against the production contract before relying on
    # this parser. Today we extract the trailing 32 bytes as int.
    try:
        if isinstance(data_field, str) and data_field.startswith("0x"):
            data_hex = data_field[2:]
        else:
            data_hex = str(data_field)
        if not data_hex:
            resolved_value = 0
        else:
            # Last 32 bytes (= 64 hex chars).
            tail = data_hex[-64:].zfill(64)
            resolved_value = int(tail, 16)
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

    block_ts = log_entry.get("blockTimestamp")
    if isinstance(block_ts, int):
        resolved_at_utc = datetime.fromtimestamp(block_ts, tz=timezone.utc)
    elif isinstance(block_ts, str):
        # Hex (Polygon-format) or decimal — try both.
        try:
            ts_int = int(block_ts, 16) if block_ts.startswith("0x") else int(block_ts)
        except ValueError as exc:
            raise ValueError(f"blockTimestamp unparseable: {block_ts!r}") from exc
        resolved_at_utc = datetime.fromtimestamp(ts_int, tz=timezone.utc)
    else:
        raise ValueError(
            f"blockTimestamp missing or wrong type: {type(block_ts).__name__}"
        )

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
            resolution = parse_settle_event(log_entry)
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
