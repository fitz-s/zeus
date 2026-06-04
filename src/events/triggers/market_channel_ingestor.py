"""Online Polymarket market-channel ingestor for EDLI quote/book evidence."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.events.event_coalescer import EventCoalescer
from src.events.event_writer import EventWriter, EventWriteResult
from src.events.opportunity_event import MarketBookEventPayload, OpportunityEvent, make_opportunity_event
from src.events.idempotency import stable_event_id

UTC = timezone.utc
MARKET_CHANNEL_WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_logger = logging.getLogger(__name__)


def _world_write_mutex():
    """Lazily resolve the process-global zeus-world.db write mutex.

    Imported lazily (not at module top) to avoid an import cycle with
    ``src.state.db``. EDLI live-canary contention fix (2026-05-31): the
    market-channel ingestor and the EDLI reactor are two in-process WAL writers
    on zeus-world.db; serializing each write+commit unit under this mutex
    guarantees they never hold the SQLite write lock concurrently, so a contended
    write waits cleanly on the Python mutex instead of crashing on a 30 s
    busy_timeout "database is locked".
    """
    from src.state.db import world_write_mutex

    return world_write_mutex()


class MarketChannelAuthorityError(ValueError):
    pass


@dataclass(frozen=True)
class MarketChannelAction:
    refresh_snapshot: bool = False
    create_live_trade: bool = False
    write_fill_truth: bool = False
    reason: str = ""
    token_id: str | None = None
    condition_id: str | None = None


@dataclass(frozen=True)
class MarketTokenMetadata:
    condition_id: str
    token_id: str
    outcome_label: str
    min_tick_size: str
    min_order_size: str
    neg_risk: bool
    executable_snapshot_id: str


@dataclass
class QuoteCache:
    """In-memory online quote cache seeded by REST and refreshed by channel events."""

    by_token_id: dict[str, MarketBookEventPayload] = field(default_factory=dict)
    reconnect_gap_count: int = 0

    def update(self, payload: MarketBookEventPayload) -> None:
        self.by_token_id[payload.token_id] = payload

    def get(self, token_id: str) -> MarketBookEventPayload | None:
        return self.by_token_id.get(token_id)


RestOrderbookFetch = Callable[[str], dict[str, Any]]


class MarketChannelIngestor:
    """Public market-data channel ingestor; never fill/order-state authority."""

    def __init__(
        self,
        writer: EventWriter,
        *,
        active_token_ids: set[str],
        token_metadata: dict[str, MarketTokenMetadata] | None = None,
        quote_cache: QuoteCache | None = None,
        coalescer: EventCoalescer | None = None,
    ) -> None:
        self._writer = writer
        self._active_token_ids = active_token_ids
        self._token_metadata = token_metadata or {}
        self.quote_cache = quote_cache or QuoteCache()
        self._coalescer = coalescer

    def handle_message(self, message: dict[str, Any], *, received_at: str) -> EventWriteResult | MarketChannelAction | None:
        event_type = str(message.get("event_type") or message.get("type") or "")
        if event_type == "tick_size_change":
            return MarketChannelAction(
                refresh_snapshot=True,
                reason="tick_size_change",
                token_id=_message_token_id(message),
                condition_id=str(message.get("market") or message.get("condition_id") or "") or None,
            )
        if event_type == "market_resolved":
            return MarketChannelAction(
                refresh_snapshot=True,
                reason="market_resolved",
                token_id=_message_token_id(message),
                condition_id=str(message.get("market") or message.get("condition_id") or "") or None,
            )
        event = self.event_from_message(message, received_at=received_at)
        if event is None:
            return None
        self._cache_event_payload(event)
        return self._write_market_event(event)

    def flush_coalesced(self, *, market_budget: int | None = None) -> list[EventWriteResult]:
        if self._coalescer is None:
            return []
        events = self._coalescer.drain(market_budget=market_budget)
        results = []
        for event in events:
            result = self._writer.write(event)
            if result.inserted:
                self._write_feasibility_evidence(event)
            results.append(result)
        return results

    def event_from_message(self, message: dict[str, Any], *, received_at: str) -> OpportunityEvent | None:
        event_type = str(message.get("event_type") or message.get("type") or "")
        if event_type == "book":
            return self._book_event(message, received_at=received_at, gap_marked=False)
        if event_type in {"best_bid_ask", "price_change"}:
            return self._bba_event(message, received_at=received_at)
        if event_type == "new_market":
            return self._new_market_event(message, received_at=received_at)
        return None

    def reconnect_gap_snapshot(self, book_message: dict[str, Any], *, gap_start: str, received_at: str) -> OpportunityEvent | None:
        event = self._book_event(book_message, received_at=received_at, gap_marked=True, gap_start=gap_start)
        if event is not None:
            self.quote_cache.reconnect_gap_count += 1
            self._cache_event_payload(event)
        return event

    def seed_from_rest(self, fetch_orderbook: RestOrderbookFetch, *, received_at: str) -> list[EventWriteResult]:
        """REST-seed current books on connect/reconnect before channel deltas."""

        results: list[EventWriteResult] = []
        for token_id in sorted(self._active_token_ids):
            try:
                message = dict(fetch_orderbook(token_id))
            except Exception as exc:
                _logger.warning(
                    "seed_from_rest: skipping token %s — fetch failed (%s: %s)",
                    token_id,
                    type(exc).__name__,
                    exc,
                )
                continue
            message.setdefault("event_type", "book")
            message.setdefault("asset_id", token_id)
            message.setdefault("timestamp", received_at)
            event = self._book_event(message, received_at=received_at, gap_marked=False)
            if event is None:
                continue
            self._cache_event_payload(event)
            result = self._writer.write(event)
            self._write_feasibility_evidence(event)
            results.append(result)
        return results

    def _book_event(
        self,
        message: dict[str, Any],
        *,
        received_at: str,
        gap_marked: bool,
        gap_start: str | None = None,
    ) -> OpportunityEvent | None:
        token_id = _message_token_id(message)
        if token_id not in self._active_token_ids:
            return None
        metadata = self._metadata_for_message(message, token_id=token_id)
        if metadata is None:
            return None
        payload = MarketBookEventPayload(
            condition_id=metadata.condition_id,
            token_id=token_id,
            outcome_label=metadata.outcome_label,  # type: ignore[arg-type]
            event_type="BOOK_SNAPSHOT",
            quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at,
            book_hash=str(message.get("hash") or ""),
            best_bid=_best_price(message.get("bids"), best="bid"),
            best_ask=_best_price(message.get("asks"), best="ask"),
            depth_json=json.dumps({"bids": message.get("bids", []), "asks": message.get("asks", [])}, sort_keys=True),
            tick_size=metadata.min_tick_size,
            min_order_size=metadata.min_order_size,
            neg_risk=metadata.neg_risk,
            executable_snapshot_id=metadata.executable_snapshot_id,
            gap_start=gap_start if gap_marked else None,
            gap_recovered_at=received_at if gap_marked else None,
        )
        return _event_from_payload(payload, source="polymarket_market_channel", received_at=received_at)

    def _bba_event(self, message: dict[str, Any], *, received_at: str) -> OpportunityEvent | None:
        token_id = _message_token_id(message)
        if not token_id and message.get("price_changes"):
            token_id = str(message["price_changes"][0].get("asset_id") or "")
        if token_id not in self._active_token_ids:
            return None
        change = message.get("price_changes", [{}])[0] if message.get("price_changes") else message
        metadata = self._metadata_for_message({**message, **change}, token_id=token_id)
        if metadata is None:
            return None
        payload = MarketBookEventPayload(
            condition_id=metadata.condition_id,
            token_id=token_id,
            outcome_label=metadata.outcome_label,  # type: ignore[arg-type]
            event_type="BEST_BID_ASK_CHANGED",
            quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at,
            book_hash=str(change.get("hash") or ""),
            best_bid=_float_or_none(change.get("best_bid")),
            best_ask=_float_or_none(change.get("best_ask")),
            depth_json=None,
            tick_size=metadata.min_tick_size,
            min_order_size=metadata.min_order_size,
            neg_risk=metadata.neg_risk,
            executable_snapshot_id=metadata.executable_snapshot_id,
        )
        return _event_from_payload(payload, source="polymarket_market_channel", received_at=received_at)

    def _cache_event_payload(self, event: OpportunityEvent) -> None:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if event.event_type not in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
            return
        self.quote_cache.update(
            MarketBookEventPayload(
                condition_id=str(payload.get("condition_id") or ""),
                token_id=str(payload.get("token_id") or ""),
                outcome_label=str(payload.get("outcome_label") or ""),  # type: ignore[arg-type]
                event_type=event.event_type,  # type: ignore[arg-type]
                quote_seen_at=str(payload.get("quote_seen_at") or event.available_at),
                book_hash=payload.get("book_hash"),
                best_bid=_float_or_none(payload.get("best_bid")),
                best_ask=_float_or_none(payload.get("best_ask")),
                depth_json=payload.get("depth_json"),
                tick_size=payload.get("tick_size"),
                min_order_size=payload.get("min_order_size"),
                neg_risk=payload.get("neg_risk"),
                executable_snapshot_id=payload.get("executable_snapshot_id"),
                gap_start=payload.get("gap_start"),
                gap_recovered_at=payload.get("gap_recovered_at"),
            )
        )

    def _write_feasibility_evidence(self, event: OpportunityEvent) -> None:
        if event.event_type not in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
            return
        payload = json.loads(event.payload_json)
        outcome_label = str(payload.get("outcome_label") or "").upper()
        if outcome_label == "NO":
            directions = ("buy_no", "sell_no")
        elif outcome_label == "YES":
            directions = ("buy_yes", "sell_yes")
        else:
            raise MarketChannelAuthorityError("market-channel token lacks canonical YES/NO outcome metadata")
        for direction in directions:
            insert_execution_feasibility_evidence(
                self._writer.conn,
                feasibility_evidence_from_quote(event, direction=direction),
            )

    def _new_market_event(self, message: dict[str, Any], *, received_at: str) -> OpportunityEvent | None:
        token_ids = [str(token) for token in message.get("clob_token_ids") or message.get("assets_ids") or []]
        if self._active_token_ids and not (set(token_ids) & self._active_token_ids):
            return None
        token_id = token_ids[0] if token_ids else str(message.get("asset_id") or "")
        metadata = self._token_metadata.get(token_id)
        outcome_label = str(getattr(metadata, "outcome_label", "") or message.get("outcome_label") or "").upper()
        if outcome_label not in {"YES", "NO"}:
            return None
        payload = MarketBookEventPayload(
            condition_id=str(message.get("condition_id") or message.get("market") or ""),
            token_id=token_id,
            outcome_label=outcome_label,  # type: ignore[arg-type]
            event_type="NEW_MARKET_DISCOVERED",
            quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at,
            depth_json=json.dumps(message, sort_keys=True, default=str),
        )
        return _event_from_payload(payload, source="polymarket_market_channel", received_at=received_at)

    def _metadata_for_message(self, message: dict[str, Any], *, token_id: str) -> MarketTokenMetadata | None:
        metadata = self._token_metadata.get(token_id)
        condition_id = str(message.get("market") or message.get("condition_id") or "")
        if metadata is not None:
            if condition_id and condition_id != metadata.condition_id:
                return None
            return metadata
        outcome_label = str(message.get("outcome_label") or message.get("outcome") or "").upper()
        if outcome_label not in {"YES", "NO"}:
            return None
        return MarketTokenMetadata(
            condition_id=condition_id,
            token_id=token_id,
            outcome_label=outcome_label,
            min_tick_size=str(message.get("tick_size") or message.get("min_tick_size") or ""),
            min_order_size=str(message.get("min_order_size") or message.get("min_order") or ""),
            neg_risk=bool(message.get("neg_risk") or message.get("negRisk") or False),
            executable_snapshot_id=str(message.get("executable_snapshot_id") or message.get("snapshot_id") or ""),
        )

    def _write_market_event(self, event: OpportunityEvent) -> EventWriteResult | None:
        if self._coalescer is not None:
            self._coalescer.enqueue(event)
            return None
        result = self._writer.write(event)
        self._write_feasibility_evidence(event)
        return result


def active_weather_token_ids_from_snapshots(
    conn: sqlite3.Connection,
    *,
    limit: int = 2000,
    priority_token_ids: set[str] | None = None,
) -> set[str]:
    """Read active YES/NO token ids from executable snapshot truth."""

    return set(
        active_weather_token_metadata_from_snapshots(
            conn, limit=limit, priority_token_ids=priority_token_ids
        )
    )


def active_weather_token_metadata_from_snapshots(
    conn: sqlite3.Connection,
    *,
    limit: int = 2000,
    priority_token_ids: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, MarketTokenMetadata]:
    """Read active weather token metadata from executable snapshot truth.

    Coverage contract (EDLI live canary, Blocker #52 — 2026-05-31)
    ---------------------------------------------------------------
    The market-channel ingestor subscribes to / REST-seeds books for EVERY
    token returned here. Those books become the ``execution_feasibility_evidence``
    rows the pre-submit witness (``_edli_latest_pre_submit_book_row``) reads.

    The MUST-HAVE invariant is: **every token that can become an EDLI candidate
    must be in this set**, so it receives a fresh evidence row before the reactor
    decides on it. A global ``ORDER BY captured_at DESC LIMIT N`` on ROWS violated
    that invariant — it covered only the ~66 distinct tokens whose snapshot rows
    happened to be most-recent, silently excluding ~3,956 active-weather tokens
    (incl. live candidates whose snapshots were slightly older) → empty witness
    quote → ``EDLI_LIVE_CERTIFICATE_BUILD_FAILED:QUOTE_FEASIBILITY_BID_ASK_REQUIRED``.

    The fix selects the LATEST snapshot **per market** (window over condition_id),
    so every distinct active condition's YES/NO tokens are covered — distinct
    tokens, not distinct rows. ``priority_token_ids`` (the candidate universe —
    tokens the reactor has live opportunity families for) are pinned into the set
    unconditionally and never dropped by the ``limit`` cap; the remaining
    (non-priority) markets fill up to ``limit`` newest-first (round-robin the rest)
    to bound the connect-time REST seed / WS subscription against venue rate limits.
    """

    if not _table_exists(conn, "executable_market_snapshots"):
        return {}
    columns = _table_columns(conn, "executable_market_snapshots")
    required = {"snapshot_id", "condition_id", "yes_token_id", "no_token_id", "min_tick_size", "min_order_size", "neg_risk"}
    if not required <= columns:
        return {}
    has_captured_at = "captured_at" in columns
    predicates = []
    if "active" in columns:
        predicates.append("COALESCE(active, 0) = 1")
    if "closed" in columns:
        predicates.append("COALESCE(closed, 0) = 0")
    if "event_slug" in columns:
        predicates.append("(LOWER(COALESCE(event_slug, '')) LIKE '%weather%' OR LOWER(COALESCE(event_slug, '')) LIKE '%temperature%')")
    # SETTLED-EXCLUSION (2026-06-04 candidate-flow root): EMS active/closed lifecycle
    # flags are never maintained (live rows all show active=1/closed=0), so the two
    # predicates above exclude nothing — settled weather markets stay in the universe.
    # The only honest tradeability signal is market_end_at vs now: a market whose
    # market_end_at is in the PAST cannot be a tradeable candidate and must NOT enter
    # the live subscription / REST-seed universe (else the persistent channel thread
    # drowns its reseed in 404 dead tokens and live BEST_BID_ASK emission starves).
    # NULL market_end_at is KEPT (coverage-safe: cannot prove settled → Blocker #52).
    # now_iso is a self-generated UTC ISO-8601 string (no single quote → injection-safe).
    if "market_end_at" in columns:
        now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        predicates.append(f"(market_end_at IS NULL OR market_end_at > '{now_iso}')")
    where_clause = "WHERE " + " AND ".join(predicates) if predicates else ""
    # Latest snapshot per market (condition_id). Without a captured_at column we
    # cannot rank temporally, so fall back to one row per condition by rowid.
    order_expr = "captured_at DESC, rowid DESC" if has_captured_at else "rowid DESC"
    latest_per_condition = f"""
        SELECT snapshot_id, condition_id, yes_token_id, no_token_id,
               min_tick_size, min_order_size, neg_risk,
               ROW_NUMBER() OVER (
                   PARTITION BY condition_id ORDER BY {order_expr}
               ) AS _rn
        FROM executable_market_snapshots
        {where_clause}
    """
    rows = conn.execute(
        f"""
        WITH latest AS ({latest_per_condition})
        SELECT snapshot_id, condition_id, yes_token_id, no_token_id,
               min_tick_size, min_order_size, neg_risk
        FROM latest
        WHERE _rn = 1
        ORDER BY snapshot_id
        """
    ).fetchall()

    priority = {str(t) for t in (priority_token_ids or set()) if t}
    capped_limit = max(1, int(limit))
    # Partition markets: candidate-bearing markets are pinned (always captured);
    # the rest are bounded by the limit. Sort the non-priority markets newest-first
    # so the bounded slice prefers freshly-captured markets (round-robin the tail).
    priority_rows: list = []
    other_rows: list = []
    for row in rows:
        _snap, _cond, yes_token_id, no_token_id, *_ = row
        is_priority = (
            (yes_token_id and str(yes_token_id) in priority)
            or (no_token_id and str(no_token_id) in priority)
        )
        (priority_rows if is_priority else other_rows).append(row)

    selected = list(priority_rows)
    if len(selected) < capped_limit:
        selected.extend(other_rows[: capped_limit - len(selected)])

    metadata: dict[str, MarketTokenMetadata] = {}
    for snapshot_id, condition_id, yes_token_id, no_token_id, min_tick_size, min_order_size, neg_risk in selected:
        if yes_token_id:
            metadata.setdefault(
                str(yes_token_id),
                MarketTokenMetadata(
                    condition_id=str(condition_id),
                    token_id=str(yes_token_id),
                    outcome_label="YES",
                    min_tick_size=str(min_tick_size),
                    min_order_size=str(min_order_size),
                    neg_risk=bool(neg_risk),
                    executable_snapshot_id=str(snapshot_id),
                ),
            )
        if no_token_id:
            metadata.setdefault(
                str(no_token_id),
                MarketTokenMetadata(
                    condition_id=str(condition_id),
                    token_id=str(no_token_id),
                    outcome_label="NO",
                    min_tick_size=str(min_tick_size),
                    min_order_size=str(min_order_size),
                    neg_risk=bool(neg_risk),
                    executable_snapshot_id=str(snapshot_id),
                ),
            )
    return metadata


def invalidate_executable_snapshots_for_market_channel_action(
    conn: sqlite3.Connection,
    action: MarketChannelAction,
    *,
    invalidated_at: datetime,
) -> int:
    """Force event-bound executable snapshots stale after public venue changes.

    Public market-channel messages are not fill truth, but tick-size and market
    lifecycle changes are executable-quote authority changes. Until the REST
    snapshot refresh succeeds, any previously captured snapshot for the affected
    condition/token must fail the freshness gate.
    """

    if not action.refresh_snapshot or not _table_exists(conn, "executable_market_snapshots"):
        return 0
    columns = _table_columns(conn, "executable_market_snapshots")
    if "freshness_deadline" not in columns:
        return 0
    predicates: list[str] = []
    params: list[object] = []
    if action.condition_id and "condition_id" in columns:
        predicates.append("condition_id = ?")
        params.append(action.condition_id)
    if action.token_id:
        token_predicates = []
        if "yes_token_id" in columns:
            token_predicates.append("yes_token_id = ?")
            params.append(action.token_id)
        if "no_token_id" in columns:
            token_predicates.append("no_token_id = ?")
            params.append(action.token_id)
        if token_predicates:
            predicates.append("(" + " OR ".join(token_predicates) + ")")
    if not predicates:
        return 0
    stale_deadline = (invalidated_at.astimezone(UTC) - timedelta(seconds=1)).isoformat()
    cur = conn.execute(
        f"""
        UPDATE executable_market_snapshots
           SET freshness_deadline = ?
         WHERE {' OR '.join(predicates)}
        """,
        (stale_deadline, *params),
    )
    return int(cur.rowcount or 0)


@dataclass
class MarketChannelOnlineService:
    """Coordinator for connect/reconnect seed and public-channel deltas."""

    ingestor: MarketChannelIngestor
    fetch_orderbook: RestOrderbookFetch | None = None
    invalidate_snapshot: Callable[[MarketChannelAction], None] | None = None
    refresh_snapshot: Callable[[MarketChannelAction], None] | None = None
    connected: bool = False
    gap_start: str | None = None
    refresh_action_count: int = 0
    refresh_action_dropped_count: int = 0
    refresh_window_action_count: int = 0
    max_refresh_actions_per_window: int = 5
    refresh_window_seconds: float = 60.0
    _refresh_window_start: datetime | None = None
    _refresh_action_keys: set[tuple[str, str, str]] = field(default_factory=set)

    def on_connect(self, *, received_at: str) -> list[EventWriteResult]:
        self.connected = True
        self.gap_start = None
        if self.fetch_orderbook is None:
            return []
        return self.ingestor.seed_from_rest(self.fetch_orderbook, received_at=received_at)

    def on_disconnect(self, *, gap_start: str) -> None:
        self.connected = False
        self.gap_start = gap_start

    def on_reconnect(self, *, received_at: str) -> list[EventWriteResult]:
        self.connected = True
        if self.fetch_orderbook is None:
            self.gap_start = None
            return []
        results = []
        for token_id in sorted(self.ingestor._active_token_ids):
            try:
                message = dict(self.fetch_orderbook(token_id))
            except Exception as exc:
                _logger.warning(
                    "on_reconnect: skipping token %s — fetch failed (%s: %s)",
                    token_id,
                    type(exc).__name__,
                    exc,
                )
                continue
            message.setdefault("event_type", "book")
            message.setdefault("asset_id", token_id)
            event = self.ingestor.reconnect_gap_snapshot(
                message,
                gap_start=self.gap_start or received_at,
                received_at=received_at,
            )
            if event is not None:
                result = self.ingestor._writer.write(event)
                self.ingestor._write_feasibility_evidence(event)
                results.append(result)
        self.gap_start = None
        return results

    async def run_websocket_forever(
        self,
        *,
        endpoint: str = MARKET_CHANNEL_WS_ENDPOINT,
        reconnect_delay_seconds: float = 5.0,
        stop_event: Any | None = None,
        logger: Any | None = None,
        commit: Callable[[], None] | None = None,
    ) -> None:
        """Run the public market channel online.

        This is market-data only. It subscribes by active token IDs, seeds REST
        books on connect, and never writes fill/order state.
        """

        import websockets

        # EDLI live-canary contention fix (2026-05-31): serialize every world-DB
        # write+commit unit in this loop against the EDLI reactor via the
        # process-global world-DB write mutex. Held ONLY around the DB
        # write+commit (never across the WS recv / network I/O), so it stays
        # short and the reactor's per-event writes are never lock-starved.
        _world_mutex = _world_write_mutex()

        while stop_event is None or not stop_event.is_set():
            received_at = datetime.now(UTC).isoformat()
            try:
                # Seed-on-connect write unit (REST book seed → event/feasibility rows).
                with _world_mutex:
                    self.on_connect(received_at=received_at)
                    if commit is not None:
                        commit()
                async with websockets.connect(endpoint, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "assets_ids": sorted(self.ingestor._active_token_ids),
                                "type": "market",
                                "custom_feature_enabled": True,
                            },
                            separators=(",", ":"),
                        )
                    )
                    if logger is not None:
                        logger.info(
                            "EDLI market-channel connected for %d active weather tokens",
                            len(self.ingestor._active_token_ids),
                        )
                    async for raw_message in ws:
                        # Hold the world-DB write mutex ONLY around the DB
                        # write+commit unit for this message batch — never across
                        # the ``async for`` recv (network I/O) nor across
                        # _handle_action (which runs refresh/invalidate callbacks
                        # doing HTTP + zeus_trades.db writes, a DIFFERENT DB / K1
                        # split). Actions are COLLECTED under the lock and executed
                        # AFTER release so the world mutex stays short and never
                        # spans a venue fetch.
                        pending_actions: list[MarketChannelAction] = []
                        with _world_mutex:
                            for message in _parse_channel_messages(raw_message):
                                action_or_result = self.ingestor.handle_message(
                                    message,
                                    received_at=datetime.now(UTC).isoformat(),
                                )
                                if isinstance(action_or_result, MarketChannelAction):
                                    pending_actions.append(action_or_result)
                            self.ingestor.flush_coalesced(market_budget=100)
                            if commit is not None:
                                commit()
                        for _action in pending_actions:
                            self._handle_action(_action)
            except Exception as exc:  # noqa: BLE001 - network loop must retry
                gap_start = datetime.now(UTC).isoformat()
                self.on_disconnect(gap_start=gap_start)
                if logger is not None:
                    logger.warning("EDLI market-channel disconnected: %s", exc, exc_info=True)
                # ROLLBACK-ON-DISCONNECT (2026-05-31): if on_connect/seed_from_rest or
                # the WS message loop raised mid-transaction (e.g. 404 on the first
                # REST-seed token), Python's sqlite3 implicit-BEGIN may have left an
                # open write transaction on the world_conn, holding the WAL write lock
                # indefinitely across the reconnect sleep. Any other writer (the reactor
                # claim(), CollateralLedger heartbeat) then blocks for the full 30s
                # busy_timeout, crashing the reactor cycle. Rollback here releases the
                # lock immediately so the sleep period is lock-free.
                if commit is not None:
                    try:
                        self.ingestor._writer.conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(reconnect_delay_seconds)
                try:
                    # Reconnect seed write unit — serialized against the reactor.
                    # Rare error-path; on_reconnect does per-token REST seeds but
                    # this path is not the steady-state hot loop, so guarding the
                    # whole seed+commit keeps the in-process serialization simple.
                    with _world_mutex:
                        self.on_reconnect(received_at=datetime.now(UTC).isoformat())
                        if commit is not None:
                            commit()
                except Exception as seed_exc:  # noqa: BLE001
                    # Reconnect seed failed (e.g. REST 404). Rollback any partial
                    # transaction from the seed attempt for the same reason above.
                    if commit is not None:
                        try:
                            self.ingestor._writer.conn.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                    if logger is not None:
                        logger.warning(
                            "EDLI market-channel reconnect seed failed: %s",
                            seed_exc,
                            exc_info=True,
                        )

    def _handle_action(self, action: MarketChannelAction) -> None:
        if not action.refresh_snapshot:
            return
        if self.invalidate_snapshot is not None:
            self.invalidate_snapshot(action)
        now = datetime.now(UTC)
        if (
            self._refresh_window_start is None
            or (now - self._refresh_window_start) >= timedelta(seconds=max(1.0, self.refresh_window_seconds))
        ):
            self._refresh_window_start = now
            self.refresh_window_action_count = 0
            self._refresh_action_keys.clear()
        key = (str(action.reason or ""), str(action.condition_id or ""), str(action.token_id or ""))
        if key in self._refresh_action_keys:
            self.refresh_action_dropped_count += 1
            return
        if self.refresh_window_action_count >= max(1, self.max_refresh_actions_per_window):
            self.refresh_action_dropped_count += 1
            return
        self._refresh_action_keys.add(key)
        self.refresh_window_action_count += 1
        self.refresh_action_count += 1
        if self.refresh_snapshot is not None:
            self.refresh_snapshot(action)


def run_market_channel_service_forever(
    service: MarketChannelOnlineService,
    *,
    endpoint: str = MARKET_CHANNEL_WS_ENDPOINT,
    stop_event: Any | None = None,
    logger: Any | None = None,
    commit: Callable[[], None] | None = None,
) -> None:
    asyncio.run(
        service.run_websocket_forever(
            endpoint=endpoint,
            stop_event=stop_event,
            logger=logger,
            commit=commit,
        )
    )


def handle_public_market_message(event_type: str) -> MarketChannelAction:
    if event_type in {"tick_size_change", "market_resolved"}:
        return MarketChannelAction(refresh_snapshot=True, reason=event_type)
    return MarketChannelAction(refresh_snapshot=False, reason=event_type)


def assert_market_channel_not_fill_authority(action: MarketChannelAction | None = None, *, source: str | None = None) -> None:
    if action is not None and action.write_fill_truth:
        raise MarketChannelAuthorityError("public market channel cannot write fill truth")
    if source == "polymarket_market_channel":
        raise MarketChannelAuthorityError("public market channel cannot write fill truth")


def assert_user_channel_fill_authority(*, source: str) -> None:
    if source not in {"polymarket_user_channel", "venue_reconcile"}:
        raise MarketChannelAuthorityError("user channel or reconcile is required for fill truth")


def insert_execution_feasibility_evidence(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    assert_market_channel_not_fill_authority(source=str(row.get("fill_truth_source", "")))
    values = dict(row)
    values.setdefault("schema_version", 1)
    values.setdefault("created_at", datetime.now(UTC).isoformat())
    values.setdefault(
        "evidence_id",
        stable_event_id(
            str(values.get("event_id")),
            str(values.get("token_id")),
            str(values.get("quote_seen_at")),
            str(values.get("direction")),
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO execution_feasibility_evidence (
            evidence_id, event_id, condition_id, token_id, outcome_label, direction,
            quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
            depth_before_json, order_intent_time, submit_time, accepted_or_rejected,
            venue_order_id, fok_full_fill, fak_partial_fill, filled_shares,
            fill_price, cancel_remainder_status, book_hash_after, latency_ms,
            maker_cancel_before_submit, would_have_edge_after_fee, created_at, schema_version
        ) VALUES (
            :evidence_id, :event_id, :condition_id, :token_id, :outcome_label, :direction,
            :quote_seen_at, :book_hash_before, :best_bid_before, :best_ask_before,
            :depth_before_json, :order_intent_time, :submit_time, :accepted_or_rejected,
            :venue_order_id, :fok_full_fill, :fak_partial_fill, :filled_shares,
            :fill_price, :cancel_remainder_status, :book_hash_after, :latency_ms,
            :maker_cancel_before_submit, :would_have_edge_after_fee, :created_at, :schema_version
        )
        """,
        values,
    )


def feasibility_evidence_from_quote(
    event: OpportunityEvent,
    *,
    direction: str,
    order_intent_time: str | None = None,
) -> dict[str, Any]:
    payload = json.loads(event.payload_json)
    return {
        "event_id": event.event_id,
        "condition_id": payload["condition_id"],
        "token_id": payload["token_id"],
        "outcome_label": payload["outcome_label"],
        "direction": direction,
        "quote_seen_at": payload["quote_seen_at"],
        "book_hash_before": payload.get("book_hash"),
        "best_bid_before": payload.get("best_bid"),
        "best_ask_before": payload.get("best_ask"),
        "depth_before_json": payload.get("depth_json"),
        "order_intent_time": order_intent_time,
        "submit_time": None,
        "accepted_or_rejected": None,
        "venue_order_id": None,
        "fok_full_fill": None,
        "fak_partial_fill": None,
        "filled_shares": None,
        "fill_price": None,
        "cancel_remainder_status": None,
        "book_hash_after": None,
        "latency_ms": None,
        "maker_cancel_before_submit": None,
        "would_have_edge_after_fee": None,
        "fill_truth_source": "evidence_only",
    }


def _event_from_payload(payload: MarketBookEventPayload, *, source: str, received_at: str) -> OpportunityEvent:
    return make_opportunity_event(
        event_type=payload.event_type,
        entity_key=f"{payload.condition_id}|{payload.token_id}|{payload.event_type}",
        source=source,
        observed_at=payload.quote_seen_at,
        available_at=payload.quote_seen_at,
        received_at=received_at,
        payload=payload,
        causal_snapshot_id=payload.book_hash,
    )


def _best_price(levels: object, *, best: str) -> float | None:
    if not levels:
        return None
    parsed = [_float_or_none(level.get("price") if isinstance(level, dict) else level[0]) for level in levels]  # type: ignore[index]
    parsed = [value for value in parsed if value is not None]
    if not parsed:
        return None
    return max(parsed) if best == "bid" else min(parsed)


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _timestamp_ms_to_iso(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and ("T" in value or value.endswith("+00:00")):
        return value
    raw = int(value)
    if raw > 10_000_000_000:
        raw = raw // 1000
    return datetime.fromtimestamp(raw, tz=UTC).isoformat()


def _message_token_id(message: dict[str, Any]) -> str:
    return str(message.get("asset_id") or message.get("token_id") or "")


def _parse_channel_messages(raw_message: object) -> list[dict[str, Any]]:
    if isinstance(raw_message, (bytes, bytearray)):
        raw_message = raw_message.decode("utf-8")
    parsed = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
