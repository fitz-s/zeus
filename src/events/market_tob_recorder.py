# Created: 2026-10-01
# Last audited: 2026-10-01
# Authority basis: adverse-selection study capture request 2026-10-01 -- every
#   top-of-book transition of every subscribed token; capture only, no gate.
"""Top-of-book transition capture for the market channel.

Maintains its own price->size ladder per token from ``book`` snapshots and
``price_change`` deltas, and records a row only when (best bid, bid size, best
ask, ask size) differs from the previous row of that token. Rows buffer in
memory and are appended in one batch per flush under the caller's TRADE lease;
the caller owns commit/rollback. Never a quote, fill or admission input.
"""

from __future__ import annotations

import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

PRICE_SCALE = 10_000
SIZE_SCALE = 100
# 2 weeks of study data plus one week of slack for a late or repeated read.
RETENTION_DAYS = 21
MAX_PENDING = 500_000
RETENTION_CHUNK_ROWS = 5_000

Row = tuple[int, str, int, int | None, int | None, int | None, int | None]


def _ticks(value: object) -> int:
    return round(float(value) * PRICE_SCALE)  # type: ignore[arg-type]


def _centi(value: object) -> int:
    return round(float(value) * SIZE_SCALE)  # type: ignore[arg-type]


def _source_ms(message: Mapping[str, Any], fallback_ms: int) -> int:
    raw = message.get("timestamp")
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback_ms
    return value if value > 10_000_000_000 else fallback_ms


class TobRecorder:
    def __init__(self, *, max_pending: int = MAX_PENDING) -> None:
        self._max_pending = max_pending
        self._ladders: dict[str, tuple[dict[int, int], dict[int, int]]] = {}
        self._last: dict[str, tuple[int | None, int | None, int | None, int | None]] = {}
        self._pending: deque[Row] = deque()
        self._seq = 0
        self._refs: dict[str, int] = {}
        self._next_retention = 0.0
        self._batch = 0
        self._new_refs: dict[str, int] = {}
        self.dropped = 0

    @property
    def pending(self) -> int:
        return len(self._pending)

    def observe(self, message: Mapping[str, Any], *, received_ms: int) -> None:
        kind = message.get("event_type") or message.get("type")
        if kind == "book":
            token = str(message.get("asset_id") or "")
            if not token:
                return
            self._ladders[token] = (
                {_ticks(x["price"]): _centi(x["size"]) for x in message.get("bids") or []},
                {_ticks(x["price"]): _centi(x["size"]) for x in message.get("asks") or []},
            )
        elif kind == "price_change":
            changes = [c for c in message.get("price_changes") or [] if isinstance(c, dict)]
            if not changes:
                return
            token = str(changes[-1].get("asset_id") or "")
            ladder = self._ladders.get(token)
            if ladder is None:
                return  # no snapshot yet: sizes would be fiction
            for change in changes:
                side = ladder[0] if str(change.get("side")).upper() == "BUY" else ladder[1]
                price, size = _ticks(change["price"]), _centi(change["size"])
                if size == 0:
                    side.pop(price, None)
                else:
                    side[price] = size
        else:
            return
        bids, asks = self._ladders[token]
        bid = max(bids) if bids else None
        ask = min(asks) if asks else None
        top = (
            bid,
            bids[bid] if bid is not None else None,
            ask,
            asks[ask] if ask is not None else None,
        )
        if self._last.get(token) == top:
            return
        self._last[token] = top
        if len(self._pending) >= self._max_pending:
            self._pending.popleft()
            self.dropped += 1
        self._seq += 1
        self._pending.append((_source_ms(message, received_ms), token, self._seq, *top))

    def write_pending(
        self, conn: sqlite3.Connection, *, schema: str = "", batch: int = 5_000
    ) -> int:
        """Append up to ``batch`` rows under the caller's lease; ack after commit."""

        rows = [self._pending[i] for i in range(min(batch, len(self._pending)))]
        if not rows:
            return 0
        prefix = f"{schema}." if schema else ""
        new = {r[1] for r in rows} - self._refs.keys()
        if new:
            conn.executemany(
                f"INSERT OR IGNORE INTO {prefix}market_tob_tokens(token_id) VALUES (?)",
                [(t,) for t in sorted(new)],
            )
            marks = ",".join("?" for _ in new)
            self._new_refs = dict(
                conn.execute(
                    f"SELECT token_id, token_ref FROM {prefix}market_tob_tokens "
                    f"WHERE token_id IN ({marks})",
                    sorted(new),
                ).fetchall()
            )
        else:
            self._new_refs = {}
        refs = self._refs | self._new_refs
        conn.executemany(
            f"INSERT OR IGNORE INTO {prefix}market_tob_transitions"
            "(observed_ms, token_ref, seq, best_bid, best_bid_size, best_ask, best_ask_size)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r[0], refs[r[1]], r[2], *r[3:]) for r in rows],
        )
        self._batch = len(rows)
        return len(rows)

    def acknowledge(self) -> None:
        """Advance only after the batch's commit succeeded."""

        for _ in range(self._batch):
            self._pending.popleft()
        self._refs |= self._new_refs
        self._batch = 0
        self._new_refs = {}

    def trim_expired(
        self,
        conn: sqlite3.Connection,
        *,
        schema: str = "",
        now: datetime | None = None,
        chunk: int = RETENTION_CHUNK_ROWS,
    ) -> int:
        """Delete one bounded head chunk older than the retention window."""

        if time.monotonic() < self._next_retention:
            return 0
        prefix = f"{schema}." if schema else ""
        table = f"{prefix}market_tob_transitions"
        cutoff = int(
            ((now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)).timestamp()
            * 1000
        )
        edge = conn.execute(
            f"SELECT observed_ms FROM {table} ORDER BY observed_ms LIMIT 1 OFFSET ?",
            (chunk,),
        ).fetchone()
        bound = cutoff if edge is None else min(cutoff, edge[0])
        deleted = conn.execute(f"DELETE FROM {table} WHERE observed_ms < ?", (bound,)).rowcount
        # Back off once the head is inside the window; keep draining while behind.
        self._next_retention = time.monotonic() + (0.0 if deleted >= chunk else 60.0)
        return int(deleted)
