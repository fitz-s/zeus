"""Public market deliveries, not finalized fills or Zeus accounting facts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Mapping
from uuid import uuid4


def _json(value: object) -> str:
    # Preserve even a non-finite decoded vendor field as unqualified raw input.
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _source_time(message: Mapping[str, object]) -> str | None:
    value = message.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not text.isascii() or not text.isdigit():
        return None
    try:
        return (
            datetime(1970, 1, 1, tzinfo=timezone.utc)
            + timedelta(milliseconds=int(text))
        ).isoformat()
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class PublicTradeObservation:
    observer_id: str
    sequence: int
    end_sequence: int
    connection_id: str
    kind: str
    received_at: str
    source_observed_at: str | None
    token_id: str
    condition_id: str
    payload_json: str
    payload_hash: str

    @property
    def observation_id(self) -> str:
        return f"{self.observer_id}:{self.sequence}"


class PublicTradeBuffer:
    """Bounded delivery FIFO; only a successful transaction acknowledges rows.

    Sequence identity deduplicates local retries, never public executions.
    Identical prints remain separate deliveries until chain log reconciliation.
    A websocket has no source sequence: even a locally gapless stream cannot
    establish a complete execution cohort or authorize an entry.
    """

    def __init__(self, *, max_pending: int) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.max_pending = max_pending
        self.observer_id = uuid4().hex
        self.connection_id = ""
        self._sequence = 0
        self._rows: deque[PublicTradeObservation] = deque()
        self._gap: PublicTradeObservation | None = None
        self._flush_started = False

    @property
    def pending(self) -> bool:
        return bool(self._rows or self._gap)

    @property
    def ready_to_flush(self) -> bool:
        # Retain initial coverage markers until there is a trade to accompany
        # them; an empty tape must not consume extra startup quote leases.
        return self.pending and self._flush_started

    def record(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        received_at: str,
        metadata: Mapping[str, object] | None = None,
        cached_book: Mapping[str, object] | None = None,
    ) -> PublicTradeObservation:
        if kind not in {"TRADE", "STREAM_OPEN", "STREAM_CLOSED"}:
            raise ValueError("invalid public observation kind")
        parsed = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("received_at requires timezone")
        if kind == "STREAM_OPEN":
            self.connection_id = uuid4().hex
        if kind == "TRADE":
            self._flush_started = True
        self._sequence += 1
        body = _json(
            {
                "raw": dict(payload),
                "token_metadata": dict(metadata) if metadata else None,
                # Cache state at delivery is NOT a proven pre-execution book.
                "book_at_delivery": dict(cached_book) if cached_book else None,
            }
        )
        row = PublicTradeObservation(
            observer_id=self.observer_id,
            sequence=self._sequence,
            end_sequence=self._sequence,
            connection_id=self.connection_id,
            kind=kind,
            received_at=received_at,
            source_observed_at=_source_time(payload) if kind == "TRADE" else None,
            token_id=str(payload.get("asset_id") or payload.get("token_id") or ""),
            condition_id=str(
                payload.get("market") or payload.get("condition_id") or ""
            ),
            payload_json=body,
            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
        # A gap is ordered before every subsequent accepted delivery. Retain
        # its complete local sequence range even while the database is busy.
        if self._gap is not None and len(self._rows) < self.max_pending:
            self._rows.append(self._gap)
            self._gap = None
        if len(self._rows) < self.max_pending:
            self._rows.append(row)
        else:
            self._flush_started = True
            start = self._gap.sequence if self._gap else row.sequence
            body = _json(
                {"reason": "QUEUE_OVERFLOW", "dropped_count": row.sequence - start + 1}
            )
            self._gap = PublicTradeObservation(
                observer_id=self.observer_id,
                sequence=start,
                end_sequence=row.sequence,
                connection_id="",
                kind="GAP",
                received_at=received_at,
                source_observed_at=None,
                token_id="",
                condition_id="",
                payload_json=body,
                payload_hash=hashlib.sha256(body.encode()).hexdigest(),
            )
        return row

    def peek(self, limit: int) -> tuple[PublicTradeObservation, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        # Freeze an overflow range before write/retry, so an id never changes
        # content after an uncertain commit. Extra memory is one gap marker.
        if self._gap is not None and len(self._rows) < self.max_pending:
            self._rows.append(self._gap)
            self._gap = None
        return tuple(islice(self._rows, limit))

    def acknowledge(self, rows: tuple[PublicTradeObservation, ...]) -> None:
        if tuple(islice(self._rows, len(rows))) != rows:
            raise ValueError("public observation acknowledgement is not FIFO")
        for _ in rows:
            self._rows.popleft()
        if not self.pending:
            self._flush_started = False


def write_public_trade_observations(
    conn: sqlite3.Connection,
    rows: tuple[PublicTradeObservation, ...],
    *,
    schema: str = "",
) -> None:
    """Append under the caller's TRADE lease; the caller owns commit/rollback."""

    if schema not in {"", "trades"}:
        raise ValueError("invalid public observation schema")
    if schema:
        table = f"{schema}.public_market_trade_observations"
    else:
        from src.state.owner_routed_write import owner_write_target

        table = owner_write_target(conn, "public_market_trade_observations")
        if table is None:
            raise ValueError("public observation TRADE owner unavailable")
    conn.executemany(
        f"""INSERT INTO {table} (
            observation_id, observer_id, sequence, end_sequence, connection_id,
            kind, received_at, source_observed_at, token_id, condition_id,
            payload_json, payload_hash, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_id) DO NOTHING""",
        [
            (
                row.observation_id,
                row.observer_id,
                row.sequence,
                row.end_sequence,
                row.connection_id,
                row.kind,
                row.received_at,
                row.source_observed_at,
                row.token_id,
                row.condition_id,
                row.payload_json,
                row.payload_hash,
                datetime.now(timezone.utc).isoformat(),
            )
            for row in rows
        ],
    )
