# Created: prior to 2026-05-24
# Last reused or audited: 2026-06-04
# Authority basis: EDLI v1 §10 online MarketChannelIngestor contract.
#   2026-06-04: 5th-instance WAL-bloat fix — pre-capture pattern for on_connect
#   REST orderbook fetch; seed_from_rest gains pre_cached kwarg; on_connect gains
#   pre_captured_books kwarg; REST fetches stay outside _world_mutex
#   (fixes 488→601 MB zeus-world.db WAL lock starvation).
"""Online Polymarket market-channel ingestor for EDLI quote/book evidence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Iterator, Literal

from src.events.event_coalescer import EventCoalescer
from src.events.event_writer import EventWriter, EventWriteResult
from src.events.opportunity_event import MarketBookEventPayload, OpportunityEvent, make_opportunity_event
from src.events.idempotency import stable_event_id

UTC = timezone.utc
MARKET_CHANNEL_WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST_SEED_COMMIT_CHUNK_SIZE = 16
REST_SEED_FETCH_BATCH_SIZE = 128
MARKET_CHANNEL_QUOTE_FLUSH_BATCH_SIZE = 128
MARKET_CHANNEL_INITIAL_BOOK_GRACE_SECONDS = 1.0
MARKET_CHANNEL_CONTINUITY_PUBLISH_INTERVAL_SECONDS = 0.25
MARKET_CHANNEL_QUOTE_MIN_COMMIT_INTERVAL_SECONDS = 0.01
MARKET_CHANNEL_QUOTE_FLUSH_RETRY_SECONDS = 0.05
MARKET_CHANNEL_QUOTE_FLUSH_RETRY_MAX_SECONDS = 1.0
MARKET_CHANNEL_DEPTH_REPAIR_DEBOUNCE_SECONDS = 0.05
MARKET_CHANNEL_DEPTH_REPAIR_RETRY_SECONDS = 1.0
MARKET_CHANNEL_REFRESH_ACTION_RETRY_BASE_SECONDS = 0.05
MARKET_CHANNEL_REFRESH_ACTION_RETRY_MAX_SECONDS = 1.0
_logger = logging.getLogger(__name__)

RefreshSnapshotResult = Literal["completed", "deferred"]
_RefreshActionStatus = Literal["completed", "deferred", "dropped"]


def _is_sqlite_write_contention(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "locked" in message or "busy" in message


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
class _PendingRefreshAction:
    """One queued snapshot repair; invalidation is durable before refresh retries."""

    action: MarketChannelAction
    generation: int
    invalidated: bool = False
    retry_count: int = 0
    not_before_monotonic: float = 0.0


@dataclass(frozen=True)
class MarketChannelQuoteResult:
    """Result for quote evidence that intentionally is not a world opportunity row."""

    event_id: str
    event_type: str
    inserted: bool
    duplicate: bool
    evidence_written: bool
    opportunity_event_persisted: bool = False


@dataclass(frozen=True)
class MarketTokenMetadata:
    condition_id: str
    token_id: str
    outcome_label: str
    min_tick_size: str
    min_order_size: str
    neg_risk: bool
    executable_snapshot_id: str
    market_end_at: str | None = None


@dataclass(frozen=True)
class MarketTokenUniverse:
    """One atomic subscription and money-path repair-priority snapshot."""

    token_metadata: dict[str, MarketTokenMetadata]
    seed_first_token_ids: tuple[str, ...]
    depth_repair_token_ids: tuple[str, ...]


@dataclass
class QuoteCache:
    """In-memory online quote cache seeded by REST and refreshed by channel events."""

    by_token_id: dict[str, MarketBookEventPayload] = field(default_factory=dict)
    reconnect_gap_count: int = 0

    def update(self, payload: MarketBookEventPayload) -> bool:
        previous = self.by_token_id.get(payload.token_id)
        if previous is not None and _quote_instant(payload.quote_seen_at) < _quote_instant(
            previous.quote_seen_at
        ):
            return False
        self.by_token_id[payload.token_id] = payload
        return True

    def get(self, token_id: str) -> MarketBookEventPayload | None:
        return self.by_token_id.get(token_id)


RestOrderbookFetch = Callable[[str], dict[str, Any]]
RestOrderbookBatchFetch = Callable[[list[str]], dict[str, dict]]
TokenMetadataReload = Callable[
    [], dict[str, MarketTokenMetadata] | MarketTokenUniverse
]


class MarketChannelIngestor:
    """Public market-data channel ingestor; never fill/order-state authority."""

    def __init__(
        self,
        writer: EventWriter | None,
        *,
        active_token_ids: set[str],
        token_metadata: dict[str, MarketTokenMetadata] | None = None,
        feasibility_conn: sqlite3.Connection | None = None,
        feasibility_schema: str = "",
        quote_cache: QuoteCache | None = None,
        coalescer: EventCoalescer | None = None,
        market_event_sink: Callable[[list[OpportunityEvent]], None] | None = None,
        market_event_sink_independently_coordinated: bool = False,
    ) -> None:
        self._writer = writer
        if feasibility_conn is None:
            if writer is None:
                raise ValueError("quote-only market ingestor requires feasibility_conn")
            feasibility_conn = writer.conn
        self._feasibility_conn = feasibility_conn
        # INV-37 (PR415 B5): when feasibility writes share the EventWriter's
        # connection (single-connection attached cross-DB path, feasibility_conn=None or
        # the same conn), that connection is world-MAIN with zeus_trades.db ATTACHed as
        # 'trades', so the feasibility insert must be schema-qualified 'trades' to reach
        # the runtime-read table and never the world ghost copy. Default "" (own trade
        # connection, unqualified) preserves every other caller.
        self._feasibility_schema = feasibility_schema
        self._active_token_ids = active_token_ids
        self._token_metadata = token_metadata or {}
        self.quote_cache = quote_cache or QuoteCache()
        self._coalescer = coalescer
        self._market_event_sink = market_event_sink
        self._market_event_sink_independently_coordinated = bool(
            market_event_sink_independently_coordinated
        )
        self._deferred_market_event_sink_depth = 0
        self._deferred_market_event_sink_events: list[OpportunityEvent] = []
        self._deferred_market_event_sink_indexes: dict[tuple[str, ...], int] = {}
        self._deferred_market_event_sink_limit = max(
            128,
            len(self._active_token_ids | set(self._token_metadata)) + 32,
        )
        self.deferred_market_event_sink_retry_count = 0
        self.deferred_market_event_sink_coalesced_count = 0
        self.deferred_market_event_sink_overflow_count = 0
        self._deferred_market_event_sink_retry_not_before = 0.0
        self._seen_quote_event_ids: set[str] = set()
        self._seen_quote_event_order: deque[str] = deque()
        self._seen_quote_event_limit = 20_000

    def _token_is_open_at(self, token_id: str, *, now: datetime | None = None) -> bool:
        metadata = self._token_metadata.get(str(token_id))
        if metadata is None or not metadata.market_end_at:
            return True
        try:
            end_at = datetime.fromisoformat(str(metadata.market_end_at).replace("Z", "+00:00"))
        except ValueError:
            return True
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=UTC)
        as_of = (now or datetime.now(UTC)).astimezone(UTC)
        return end_at.astimezone(UTC) > as_of

    def active_token_ids_open_at(
        self,
        *,
        now: datetime | None = None,
        token_ids: Iterable[str] | None = None,
    ) -> set[str]:
        base = (
            set(self._active_token_ids)
            if token_ids is None
            else {str(token_id) for token_id in token_ids if str(token_id) in self._active_token_ids}
        )
        return {token_id for token_id in base if self._token_is_open_at(token_id, now=now)}

    def replace_token_metadata(
        self,
        token_metadata: dict[str, MarketTokenMetadata],
    ) -> set[str]:
        """Replace the live universe and return token IDs open now."""

        self._token_metadata = dict(token_metadata)
        self._active_token_ids = set(token_metadata)
        self._deferred_market_event_sink_limit = max(
            self._deferred_market_event_sink_limit,
            len(self._active_token_ids) + 32,
        )
        return self.active_token_ids_open_at()

    def handle_message(
        self,
        message: dict[str, Any],
        *,
        received_at: str,
    ) -> EventWriteResult | MarketChannelAction | MarketChannelQuoteResult | None:
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
        if self._market_quote_is_older(event):
            return None
        if self._market_top_of_book_unchanged(event):
            self._cache_event_payload(event)
            return None
        self._cache_event_payload(event)
        return self._write_market_event(event)

    def flush_coalesced(
        self,
        *,
        market_budget: int | None = None,
        commit: Callable[[], None] | None = None,
        rollback: Callable[[], None] | None = None,
    ) -> list[EventWriteResult | MarketChannelQuoteResult]:
        if self._coalescer is None:
            return []
        events = self._coalescer.drain(market_budget=market_budget)
        quote_events: list[OpportunityEvent] = []
        results: list[EventWriteResult | MarketChannelQuoteResult] = []
        try:
            for event in events:
                if event.event_type not in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
                    results.append(self._commit_market_event(event))
                    continue
                if self._quote_event_seen(event.event_id):
                    results.append(
                        MarketChannelQuoteResult(
                            event_id=event.event_id,
                            event_type=event.event_type,
                            inserted=False,
                            duplicate=True,
                            evidence_written=False,
                        )
                    )
                    continue
                quote_events.append(event)
                results.append(
                    MarketChannelQuoteResult(
                        event_id=event.event_id,
                        event_type=event.event_type,
                        inserted=True,
                        duplicate=False,
                        evidence_written=True,
                    )
                )
            self._write_feasibility_evidence_batch(quote_events)
            self._notify_market_event_sink(quote_events)
            if not self._market_event_sink_independently_coordinated:
                self.flush_deferred_market_event_sink()
            if commit is not None:
                commit()
        except BaseException:
            try:
                if rollback is not None:
                    rollback()
            finally:
                for event in events:
                    self._coalescer.enqueue(event)
            raise
        for event in quote_events:
            self._remember_quote_event(event.event_id)
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
        if event is not None and not self._market_quote_is_older(event):
            self.quote_cache.reconnect_gap_count += 1
            self._cache_event_payload(event)
            return event
        return None

    def seed_from_rest(
        self,
        fetch_orderbook: RestOrderbookFetch,
        *,
        received_at: str,
        pre_cached: "dict[str, dict] | None" = None,
        token_ids: Iterable[str] | None = None,
        coalesce_market_events: bool = False,
    ) -> list[EventWriteResult | MarketChannelQuoteResult]:
        """REST-seed current books on connect/reconnect before channel deltas.

        LOCK DISCIPLINE (2026-06-04): this method MUST NOT be called while the
        process-global zeus-world.db write mutex is held, UNLESS every active
        token is already present in ``pre_cached`` (i.e. the I/O happened
        off-lock). The fallback-fetch branch (``pre_cached`` absent for a
        token) asserts the mutex is NOT held via
        ``assert_no_world_mutex_held_for_io`` so any under-mutex regression
        raises ``WorldMutexIOViolation`` immediately at this callsite rather
        than wedging the daemon (WAL-lock starvation).

        ``pre_cached`` is a {token_id: book_dict} map built by the caller
        BEFORE acquiring any world-DB write mutex (STEP-7 / pre-capture
        pattern). When present, the cached dict is used instead of calling
        ``fetch_orderbook`` — zero I/O under the lock.  Tokens absent from
        ``pre_cached`` (or when ``pre_cached`` is ``None``) fall back to
        calling ``fetch_orderbook`` directly; this path is only safe (and
        only reached in practice) when the world mutex is NOT held.
        """
        from src.state.db import assert_no_world_mutex_held_for_io

        target_token_ids = self.active_token_ids_open_at(token_ids=token_ids)
        results: list[EventWriteResult | MarketChannelQuoteResult] = []
        for token_id in sorted(target_token_ids):
            try:
                if pre_cached is not None and token_id in pre_cached:
                    message = dict(pre_cached[token_id])
                else:
                    # Fallback: live fetch.  STRUCTURALLY FORBIDDEN under the
                    # world mutex — assert here so any regression raises at
                    # this exact site rather than wedging the daemon deeper in
                    # the call stack at get_orderbook_snapshot.
                    assert_no_world_mutex_held_for_io("seed_from_rest.fallback_fetch")
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
            if event is None or self._market_quote_is_older(event):
                continue
            self._cache_event_payload(event)
            if coalesce_market_events:
                result = self._write_market_event(event)
                if result is None:
                    result = MarketChannelQuoteResult(
                        event_id=event.event_id,
                        event_type=event.event_type,
                        inserted=True,
                        duplicate=False,
                        evidence_written=False,
                    )
            else:
                result = self._commit_market_event(event)
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
        if token_id not in self.active_token_ids_open_at(token_ids=(token_id,)):
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
        return _event_from_payload(
            payload,
            source="polymarket_market_channel",
            received_at=received_at,
        )

    def _bba_event(self, message: dict[str, Any], *, received_at: str) -> OpportunityEvent | None:
        source_event_type = str(message.get("event_type") or message.get("type") or "")
        token_id = _message_token_id(message)
        if not token_id and message.get("price_changes"):
            token_id = str(message["price_changes"][0].get("asset_id") or "")
        if token_id not in self.active_token_ids_open_at(token_ids=(token_id,)):
            return None
        changes = [
            change
            for change in message.get("price_changes") or []
            if isinstance(change, dict)
        ]
        if not changes:
            changes = [message]
        change = changes[-1]
        metadata = self._metadata_for_message({**message, **change}, token_id=token_id)
        if metadata is None:
            return None
        previous = self.quote_cache.get(token_id)
        depth_json = (
            _apply_price_changes_depth(
                previous.depth_json if previous is not None else None,
                changes,
            )
            if source_event_type == "price_change"
            else None
        )
        payload = MarketBookEventPayload(
            condition_id=metadata.condition_id,
            token_id=token_id,
            outcome_label=metadata.outcome_label,  # type: ignore[arg-type]
            event_type="BOOK_SNAPSHOT" if depth_json is not None else "BEST_BID_ASK_CHANGED",
            quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at,
            book_hash=str(change.get("hash") or ""),
            best_bid=_float_or_none(change.get("best_bid")),
            best_ask=_float_or_none(change.get("best_ask")),
            depth_json=depth_json,
            tick_size=metadata.min_tick_size,
            min_order_size=metadata.min_order_size,
            neg_risk=metadata.neg_risk,
            executable_snapshot_id=metadata.executable_snapshot_id,
        )
        event = _event_from_payload(
            payload,
            source="polymarket_market_channel",
            received_at=received_at,
        )
        return event

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

    def _market_top_of_book_unchanged(self, event: OpportunityEvent) -> bool:
        """Suppress append-only BBA rows when the executable touch did not move."""

        if event.event_type != "BEST_BID_ASK_CHANGED":
            return False
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        token_id = str(payload.get("token_id") or "")
        if not token_id:
            return False
        previous = self.quote_cache.get(token_id)
        if previous is None:
            return False

        def _same(a: object, b: object) -> bool:
            av = _float_or_none(a)
            bv = _float_or_none(b)
            if av is None or bv is None:
                return av is None and bv is None
            return abs(av - bv) <= 1e-12

        return _same(payload.get("best_bid"), previous.best_bid) and _same(
            payload.get("best_ask"), previous.best_ask
        )

    def _market_quote_is_older(self, event: OpportunityEvent) -> bool:
        if event.event_type not in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
            return False
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        token_id = str(payload.get("token_id") or "")
        previous = self.quote_cache.get(token_id)
        if previous is None:
            return False
        return _quote_instant(
            str(payload.get("quote_seen_at") or event.available_at)
        ) < _quote_instant(previous.quote_seen_at)

    def _write_feasibility_evidence(self, event: OpportunityEvent) -> None:
        self._write_feasibility_evidence_batch([event])

    def _write_feasibility_evidence_batch(
        self,
        events: Iterable[OpportunityEvent],
    ) -> None:
        rows: list[dict[str, Any]] = []
        for event in events:
            if event.event_type not in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
                continue
            payload = json.loads(event.payload_json)
            outcome_label = str(payload.get("outcome_label") or "").upper()
            if outcome_label == "NO":
                directions = ("buy_no", "sell_no")
            elif outcome_label == "YES":
                directions = ("buy_yes", "sell_yes")
            else:
                raise MarketChannelAuthorityError(
                    "market-channel token lacks canonical YES/NO outcome metadata"
                )
            rows.extend(
                feasibility_evidence_from_quote(event, direction=direction)
                for direction in directions
            )
        insert_execution_feasibility_evidence_batch(
            self._feasibility_conn,
            rows,
            schema=self._feasibility_schema,
            append_evidence=False,
        )

    def _new_market_event(self, message: dict[str, Any], *, received_at: str) -> OpportunityEvent | None:
        token_ids = [str(token) for token in message.get("clob_token_ids") or message.get("assets_ids") or []]
        active_token_ids = self.active_token_ids_open_at()
        if active_token_ids and not (set(token_ids) & active_token_ids):
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
        return self._commit_market_event(event)

    def _commit_market_event(self, event: OpportunityEvent) -> EventWriteResult | MarketChannelQuoteResult:
        if event.event_type in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
            if self._quote_event_seen(event.event_id):
                return MarketChannelQuoteResult(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    inserted=False,
                    duplicate=True,
                    evidence_written=False,
                )
            self._remember_quote_event(event.event_id)
            self._write_feasibility_evidence(event)
            self._notify_market_event_sink([event])
            return MarketChannelQuoteResult(
                event_id=event.event_id,
                event_type=event.event_type,
                inserted=True,
                duplicate=False,
                evidence_written=True,
            )
        if self._writer is None:
            raise MarketChannelAuthorityError(
                "quote-only market ingestor cannot persist WORLD events"
            )
        result = self._writer.write(event)
        if result.inserted:
            self._write_feasibility_evidence(event)
            self._notify_market_event_sink([event])
        return result

    def _quote_event_seen(self, event_id: str) -> bool:
        return event_id in self._seen_quote_event_ids

    def _remember_quote_event(self, event_id: str) -> None:
        self._seen_quote_event_ids.add(event_id)
        self._seen_quote_event_order.append(event_id)
        while len(self._seen_quote_event_order) > self._seen_quote_event_limit:
            expired = self._seen_quote_event_order.popleft()
            self._seen_quote_event_ids.discard(expired)

    def _notify_market_event_sink(self, events: list[OpportunityEvent]) -> None:
        if self._market_event_sink is None or not events:
            return
        if self._deferred_market_event_sink_depth > 0:
            if self._market_event_sink_independently_coordinated:
                self._coalesce_deferred_market_event_sink_events(events)
            else:
                self._deferred_market_event_sink_events.extend(events)
            return
        try:
            self._market_event_sink(events)
        except Exception as exc:  # noqa: BLE001 - derived sink must not poison ingest.
            _logger.warning(
                "market-channel post-write sink failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )

    @staticmethod
    def _deferred_market_event_sink_key(event: OpportunityEvent) -> tuple[str, ...]:
        event_type = str(getattr(event, "event_type", "") or "")
        if event_type in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED"}:
            try:
                payload = json.loads(str(getattr(event, "payload_json", "") or "{}"))
            except Exception:
                payload = {}
            token_id = str(payload.get("token_id") or "").strip()
            if token_id:
                return ("token", token_id)
        entity_key = str(getattr(event, "entity_key", "") or "").strip()
        if entity_key:
            return ("entity", event_type, entity_key)
        return ("event", str(getattr(event, "event_id", "") or ""))

    def _coalesce_deferred_market_event_sink_events(
        self,
        events: list[OpportunityEvent],
    ) -> None:
        for event in events:
            key = self._deferred_market_event_sink_key(event)
            index = self._deferred_market_event_sink_indexes.get(key)
            if index is None:
                self._deferred_market_event_sink_indexes[key] = len(
                    self._deferred_market_event_sink_events
                )
                self._deferred_market_event_sink_events.append(event)
            else:
                self._deferred_market_event_sink_events[index] = event
                self.deferred_market_event_sink_coalesced_count += 1

        limit = self._deferred_market_event_sink_limit
        if len(self._deferred_market_event_sink_events) <= limit:
            return
        active_keys = {("token", str(token_id)) for token_id in self._active_token_ids}
        keyed = [
            (self._deferred_market_event_sink_key(event), event)
            for event in self._deferred_market_event_sink_events
        ]
        active = [(key, event) for key, event in keyed if key in active_keys]
        other = [(key, event) for key, event in keyed if key not in active_keys]
        available = max(0, limit - len(active))
        kept = active + other[-available:] if available else active
        overflow = max(0, len(keyed) - len(kept))
        if overflow:
            self.deferred_market_event_sink_overflow_count += overflow
            _logger.warning(
                "market-channel deferred sink coalescing overflow: dropped_nonactive=%d "
                "backlog=%d limit=%d active=%d total_overflow=%d",
                overflow,
                len(kept),
                limit,
                len(active),
                self.deferred_market_event_sink_overflow_count,
            )
        if overflow:
            self._deferred_market_event_sink_events = [event for _key, event in kept]
            self._deferred_market_event_sink_indexes = {
                key: index for index, (key, _event) in enumerate(kept)
            }

    @contextlib.contextmanager
    def defer_market_event_sink(self) -> Iterator[None]:
        self._deferred_market_event_sink_depth += 1
        try:
            yield
        except BaseException:
            if self._deferred_market_event_sink_depth == 1:
                self._deferred_market_event_sink_events.clear()
                self._deferred_market_event_sink_indexes.clear()
            raise
        finally:
            self._deferred_market_event_sink_depth -= 1

    def flush_deferred_market_event_sink(self) -> None:
        """Flush retryable derived work without discarding a failed batch.

        A reconnect always REST-seeds the active universe, so process loss reconstructs
        current quote triggers; within one process a failed sink stays buffered until the
        next seed chunk or websocket message retries it.
        """
        if self._market_event_sink is None or not self._deferred_market_event_sink_events:
            self._deferred_market_event_sink_events.clear()
            self._deferred_market_event_sink_indexes.clear()
            return
        if time.monotonic() < self._deferred_market_event_sink_retry_not_before:
            return
        events = list(self._deferred_market_event_sink_events)
        try:
            self._market_event_sink(events)
        except Exception as exc:  # noqa: BLE001 - derived sink must not poison ingest.
            self.deferred_market_event_sink_retry_count += 1
            delay = min(
                30.0,
                float(2 ** min(self.deferred_market_event_sink_retry_count - 1, 5)),
            )
            self._deferred_market_event_sink_retry_not_before = time.monotonic() + delay
            _logger.warning(
                "market-channel deferred sink failed; backlog=%d retry_count=%d "
                "retry_after_seconds=%.1f coalesced=%d overflow=%d: %s: %s",
                len(events),
                self.deferred_market_event_sink_retry_count,
                delay,
                self.deferred_market_event_sink_coalesced_count,
                self.deferred_market_event_sink_overflow_count,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return
        del self._deferred_market_event_sink_events[: len(events)]
        self._deferred_market_event_sink_indexes = {
            self._deferred_market_event_sink_key(event): index
            for index, event in enumerate(self._deferred_market_event_sink_events)
        }
        self.deferred_market_event_sink_retry_count = 0
        self._deferred_market_event_sink_retry_not_before = 0.0


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
    latest_table = "executable_market_snapshot_latest"
    latest_columns = (
        _table_columns(conn, latest_table)
        if _table_exists(conn, latest_table)
        else set()
    )
    use_latest_projection = (
        {"condition_id", "snapshot_id"} <= latest_columns
        and conn.execute(f"SELECT 1 FROM {latest_table} LIMIT 1").fetchone()
        is not None
    )
    prefix = "snapshot." if use_latest_projection else ""
    predicates = []
    if "active" in columns:
        predicates.append(f"COALESCE({prefix}active, 0) = 1")
    if "closed" in columns:
        predicates.append(f"COALESCE({prefix}closed, 0) = 0")
    if "event_slug" in columns:
        predicates.append(
            f"(LOWER(COALESCE({prefix}event_slug, '')) LIKE '%weather%' "
            f"OR LOWER(COALESCE({prefix}event_slug, '')) LIKE '%temperature%')"
        )
    # SETTLED-EXCLUSION (2026-06-04 candidate-flow root): EMS active/closed lifecycle
    # flags are never maintained (live rows all show active=1/closed=0), so the two
    # predicates above exclude nothing — settled weather markets stay in the universe.
    # The only honest tradeability signal is market_end_at vs now: a market whose
    # market_end_at is in the PAST cannot be a tradeable candidate and must NOT enter
    # the live subscription / REST-seed universe (else the persistent channel thread
    # drowns its reseed in 404 dead tokens and live BEST_BID_ASK emission starves).
    # NULL market_end_at is KEPT (coverage-safe: cannot prove settled → Blocker #52).
    # now_iso is a self-generated UTC ISO-8601 string (no single quote → injection-safe).
    #
    # STEP 5 (consolidated timeliness fix): this SQL `market_end_at > now` is the
    # bulk-set expression of the ONE canonical POST_TRADING-boundary authority,
    # ``src.strategy.market_phase.market_open_at_decision`` (= as_of <
    # polymarket_end_utc, the exact complement of market_phase_for_decision's
    # POST_TRADING transition). executable_market_snapshots carries no
    # city/target_date, so the per-row forecast-phase form cannot run here; the
    # end-boundary IS the shared authority and a relationship test pins SQL ≡
    # predicate so the universe filter and the phase axis cannot diverge. NOT the
    # forecast_only-admission predicate — the universe legitimately keeps
    # SETTLEMENT_DAY markets (day0/exit); only POST_TRADING is excluded.
    if "market_end_at" in columns:
        now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        predicates.append(
            f"({prefix}market_end_at IS NULL OR {prefix}market_end_at > '{now_iso}')"
        )
    market_end_expr = (
        f"{prefix}market_end_at"
        if "market_end_at" in columns
        else "NULL AS market_end_at"
    )
    where_clause = "WHERE " + " AND ".join(predicates) if predicates else ""
    # The current projection bounds this ranking to O(current markets). The
    # append-only fallback preserves fixtures and recovery databases that have
    # not materialized the projection yet.
    source = (
        f"""
        FROM {latest_table} AS latest
        JOIN executable_market_snapshots AS snapshot
          ON snapshot.snapshot_id = latest.snapshot_id
        """
        if use_latest_projection
        else "FROM executable_market_snapshots"
    )
    order_expr = (
        f"{prefix}captured_at DESC, {prefix}rowid DESC"
        if has_captured_at
        else f"{prefix}rowid DESC"
    )
    latest_per_condition = f"""
        SELECT {prefix}snapshot_id AS snapshot_id,
               {prefix}condition_id AS condition_id,
               {prefix}yes_token_id AS yes_token_id,
               {prefix}no_token_id AS no_token_id,
               {prefix}min_tick_size AS min_tick_size,
               {prefix}min_order_size AS min_order_size,
               {prefix}neg_risk AS neg_risk,
               {market_end_expr},
               ROW_NUMBER() OVER (
                   PARTITION BY {prefix}condition_id ORDER BY {order_expr}
               ) AS _rn
        {source}
        {where_clause}
    """
    rows = conn.execute(
        f"""
        WITH latest AS ({latest_per_condition})
        SELECT snapshot_id, condition_id, yes_token_id, no_token_id,
               min_tick_size, min_order_size, neg_risk, market_end_at
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
    for (
        snapshot_id,
        condition_id,
        yes_token_id,
        no_token_id,
        min_tick_size,
        min_order_size,
        neg_risk,
        market_end_at,
    ) in selected:
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
                    market_end_at=str(market_end_at) if market_end_at is not None else None,
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
                    market_end_at=str(market_end_at) if market_end_at is not None else None,
                ),
            )
    return metadata


def active_weather_token_metadata_for_tokens(
    conn: sqlite3.Connection,
    *,
    token_ids: Iterable[str],
    now: datetime | None = None,
    purpose: Literal["entry", "exit"] = "entry",
) -> dict[str, MarketTokenMetadata]:
    """Read latest executable snapshot metadata for a bounded token set.

    Entry metadata follows the nominal market end boundary. Exit metadata keeps
    held exposure observable past that boundary until the venue explicitly
    disables the order book or stops accepting orders.
    """

    if purpose not in {"entry", "exit"}:
        raise ValueError(f"unsupported token metadata purpose: {purpose}")
    tokens = list(
        dict.fromkeys(
            str(token_id).strip()
            for token_id in token_ids
            if str(token_id or "").strip()
        )
    )
    if not tokens or not _table_exists(conn, "executable_market_snapshots"):
        return {}
    columns = _table_columns(conn, "executable_market_snapshots")
    required = {
        "snapshot_id",
        "condition_id",
        "yes_token_id",
        "no_token_id",
        "min_tick_size",
        "min_order_size",
        "neg_risk",
    }
    if not required <= columns:
        return {}

    predicates = []
    if "active" in columns:
        predicates.append("COALESCE(active, 0) = 1")
    if "closed" in columns:
        predicates.append("COALESCE(closed, 0) = 0")
    if "event_slug" in columns:
        predicates.append(
            "(LOWER(COALESCE(event_slug, '')) LIKE '%weather%' "
            "OR LOWER(COALESCE(event_slug, '')) LIKE '%temperature%')"
        )
    extra_params: list[object] = []
    if purpose == "entry" and "market_end_at" in columns:
        now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        predicates.append("(market_end_at IS NULL OR market_end_at > ?)")
        extra_params.append(now_iso)
    elif purpose == "exit":
        if "enable_orderbook" in columns:
            predicates.append("COALESCE(enable_orderbook, 0) = 1")
        if "accepting_orders" in columns:
            predicates.append("COALESCE(accepting_orders, 1) = 1")
    extra_where = (" AND " + " AND ".join(predicates)) if predicates else ""
    order_value_expr = "captured_at" if "captured_at" in columns else "rowid"
    order_expr = "captured_at DESC, rowid DESC" if "captured_at" in columns else "rowid DESC"
    market_end_expr = "market_end_at" if "market_end_at" in columns else "NULL AS market_end_at"

    metadata: dict[str, MarketTokenMetadata] = {}
    latest_covered: set[str] = set()

    def _record(token_id: str, row: sqlite3.Row | tuple) -> None:
        (
            snapshot_id,
            condition_id,
            yes_token_id,
            no_token_id,
            min_tick_size,
            min_order_size,
            neg_risk,
            market_end_at,
        ) = row[:8]
        if str(yes_token_id) == token_id:
            outcome_label = "YES"
        elif str(no_token_id) == token_id:
            outcome_label = "NO"
        else:
            return
        metadata[token_id] = MarketTokenMetadata(
            condition_id=str(condition_id),
            token_id=token_id,
            outcome_label=outcome_label,
            min_tick_size=str(min_tick_size),
            min_order_size=str(min_order_size),
            neg_risk=bool(neg_risk),
            executable_snapshot_id=str(snapshot_id),
            market_end_at=str(market_end_at) if market_end_at is not None else None,
        )

    if _table_exists(conn, "executable_market_snapshot_latest"):
        latest_columns = _table_columns(conn, "executable_market_snapshot_latest")
        latest_required = {
            "snapshot_id",
            "yes_token_id",
            "no_token_id",
            "captured_at",
        }
        if latest_required <= latest_columns:
            eligibility = " AND ".join(predicates) if predicates else "1"
            # Keep each statement below SQLite's legacy 999-variable ceiling.
            for offset in range(0, len(tokens), 400):
                batch = tokens[offset : offset + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"""
                    SELECT s.snapshot_id, s.condition_id, s.yes_token_id, s.no_token_id,
                           s.min_tick_size, s.min_order_size, s.neg_risk, s.market_end_at,
                           s._eligible, l.captured_at AS _order_value
                      FROM executable_market_snapshot_latest AS l
                      JOIN (
                            SELECT snapshot_id, condition_id, yes_token_id, no_token_id,
                                   min_tick_size, min_order_size, neg_risk,
                                   {market_end_expr},
                                   CASE WHEN {eligibility} THEN 1 ELSE 0 END AS _eligible
                              FROM executable_market_snapshots
                           ) AS s
                        ON s.snapshot_id = l.snapshot_id
                     WHERE l.yes_token_id IN ({placeholders})
                        OR l.no_token_id IN ({placeholders})
                     ORDER BY l.captured_at DESC
                    """,
                    (*extra_params, *batch, *batch),
                ).fetchall()
                batch_set = set(batch)
                for row in rows:
                    row_tokens = {
                        str(row[2]),
                        str(row[3]),
                    } & batch_set
                    latest_covered.update(row_tokens)
                    if not bool(row[8]):
                        continue
                    for token_id in row_tokens:
                        if token_id not in metadata:
                            _record(token_id, row)

    # Compatibility fallback for databases whose compact latest projection has
    # not yet seen this token. A latest row that is closed/inactive is coverage,
    # not permission to resurrect an older tradeable append row.
    for token_id in tokens:
        if token_id in latest_covered:
            continue
        rows = []
        for token_column in ("yes_token_id", "no_token_id"):
            rows.extend(
                conn.execute(
                    f"""
                    SELECT snapshot_id, condition_id, yes_token_id, no_token_id,
                           min_tick_size, min_order_size, neg_risk, {market_end_expr}, {order_value_expr} AS _order_value
                    FROM executable_market_snapshots
                    WHERE {token_column} = ?{extra_where}
                    ORDER BY {order_expr}
                    LIMIT 1
                    """,
                    (token_id, *extra_params),
                ).fetchall()
            )
        if not rows:
            continue
        row = max(rows, key=lambda r: str(r[-1] or ""))
        _record(token_id, row)
    return metadata


def invalidate_executable_snapshots_for_market_channel_action(
    conn: sqlite3.Connection,
    action: MarketChannelAction,
    *,
    invalidated_at: datetime,
) -> int:
    """Append an invalidation fact after public venue changes.

    Public market-channel messages are not fill truth, but tick-size and market
    lifecycle changes are executable-quote authority changes. Snapshot evidence
    is append-only, so invalidation is a separate fact consumed by live readers,
    never an UPDATE to historical rows.
    """

    if not action.refresh_snapshot:
        return 0

    from src.state.snapshot_repo import record_snapshot_invalidation

    return record_snapshot_invalidation(
        conn,
        condition_id=action.condition_id,
        token_id=action.token_id,
        reason=action.reason,
        invalidated_at=invalidated_at.astimezone(UTC),
    )


@dataclass
class MarketChannelOnlineService:
    """Coordinator for connect/reconnect seed and public-channel deltas."""

    ingestor: MarketChannelIngestor
    fetch_orderbook: RestOrderbookFetch | None = None
    fetch_orderbooks: RestOrderbookBatchFetch | None = None
    invalidate_snapshot: Callable[[MarketChannelAction], None] | None = None
    refresh_snapshot: Callable[
        [MarketChannelAction], RefreshSnapshotResult | None
    ] | None = None
    reload_token_metadata: TokenMetadataReload | None = None
    universe_refresh_interval_seconds: float = 15.0
    continuity_sink: Callable[[dict[str, Any]], None] | None = None
    connected: bool = False
    gap_start: str | None = None
    refresh_action_count: int = 0
    refresh_action_dropped_count: int = 0
    refresh_window_action_count: int = 0
    max_refresh_actions_per_window: int = 5
    refresh_window_seconds: float = 60.0
    seed_first_token_ids: Iterable[str] = field(default_factory=tuple)
    depth_repair_token_ids: Iterable[str] | None = None
    initial_book_grace_seconds: float = MARKET_CHANNEL_INITIAL_BOOK_GRACE_SECONDS
    continuity_publish_interval_seconds: float = (
        MARKET_CHANNEL_CONTINUITY_PUBLISH_INTERVAL_SECONDS
    )
    rest_seed_backpressure_count: int = 0
    rest_seed_backpressure_reason: str | None = None
    _connected_at: str | None = None
    _refresh_window_start: datetime | None = None
    _current_generation_depth_tokens: set[str] = field(default_factory=set)
    _missing_depth_tokens: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
    )
    _depth_repair_inflight_tokens: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
    )
    _depth_repair_quote_seen_at: dict[str, str] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _continuity_last_publish_monotonic: float | None = field(default=None, init=False)
    _continuity_last_connected: bool | None = field(default=None, init=False)
    _refresh_worker_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _pending_refresh_actions: dict[
        tuple[str, str], _PendingRefreshAction
    ] = field(default_factory=dict, init=False, repr=False)
    _refresh_action_generation: int = field(default=0, init=False, repr=False)
    _refresh_worker_running: bool = field(default=False, init=False, repr=False)
    _refresh_worker_idle: threading.Event = field(
        default_factory=threading.Event,
        init=False,
        repr=False,
    )
    _quote_projection_pump_active: bool = field(default=False, init=False, repr=False)
    refresh_action_coalesced_count: int = field(default=0, init=False)
    subscription_add_count: int = field(default=0, init=False)
    subscription_remove_count: int = field(default=0, init=False)
    universe_refresh_error_count: int = field(default=0, init=False)
    quote_projection_backpressure_count: int = field(default=0, init=False)
    depth_repair_fetch_count: int = field(default=0, init=False)
    depth_repair_failure_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._replace_seed_first_token_ids(self.seed_first_token_ids)
        repair_tokens = (
            self.seed_first_token_ids
            if self.depth_repair_token_ids is None
            else self.depth_repair_token_ids
        )
        self._replace_depth_repair_token_ids(repair_tokens)
        self._refresh_worker_idle.set()

    def _replace_seed_first_token_ids(self, token_ids: Iterable[str]) -> None:
        self.seed_first_token_ids = tuple(
            dict.fromkeys(
                str(token_id)
                for token_id in token_ids
                if str(token_id or "").strip()
            )
        )
    def _replace_depth_repair_token_ids(self, token_ids: Iterable[str]) -> None:
        self.depth_repair_token_ids = tuple(
            dict.fromkeys(
                str(token_id)
                for token_id in token_ids
                if str(token_id or "").strip()
            )
        )
        priority = set(self.depth_repair_token_ids)
        self._missing_depth_tokens.intersection_update(priority)
        self._depth_repair_inflight_tokens.intersection_update(priority)
        self._depth_repair_quote_seen_at = {
            token_id: quote_seen_at
            for token_id, quote_seen_at in self._depth_repair_quote_seen_at.items()
            if token_id in priority
        }

    def _record_current_generation_depth(self, message: dict[str, Any]) -> None:
        event_type = str(message.get("event_type") or message.get("type") or "")
        if event_type not in {"book", "best_bid_ask", "price_change"}:
            return
        token_id = _message_token_id(message)
        cached = self.ingestor.quote_cache.get(token_id)
        if cached is not None and cached.depth_json not in (None, ""):
            self._current_generation_depth_tokens.add(token_id)
            return
        self._current_generation_depth_tokens.discard(token_id)
        if (
            token_id in self.depth_repair_token_ids
            and token_id in self.ingestor.active_token_ids_open_at(token_ids=(token_id,))
        ):
            self._missing_depth_tokens.add(token_id)

    def _publish_continuity(
        self,
        *,
        connected: bool,
        observed_at: str,
        active_token_count: int,
        logger: Any | None = None,
    ) -> None:
        if self.continuity_sink is None:
            return
        connected_state = bool(connected)
        now_monotonic = time.monotonic()
        interval = max(0.0, float(self.continuity_publish_interval_seconds))
        if (
            self._continuity_last_connected is connected_state
            and self._continuity_last_publish_monotonic is not None
            and now_monotonic - self._continuity_last_publish_monotonic < interval
        ):
            return
        try:
            self.continuity_sink(
                {
                    "schema_version": 1,
                    "channel": "market_channel",
                    "connected": connected_state,
                    "connected_at": self._connected_at,
                    "observed_at": observed_at,
                    "active_token_count": max(0, int(active_token_count)),
                }
            )
            self._continuity_last_publish_monotonic = now_monotonic
            self._continuity_last_connected = connected_state
        except Exception as exc:  # noqa: BLE001 - proof loss falls back to REST
            if logger is not None:
                logger.warning(
                    "EDLI market-channel continuity publish failed: %s",
                    exc,
                    exc_info=True,
                )

    def on_connect(
        self,
        *,
        received_at: str,
        pre_captured_books: "dict[str, dict] | None" = None,
    ) -> list[EventWriteResult | MarketChannelQuoteResult]:
        """Seed the book cache on connect.

        ``pre_captured_books`` is an optional {token_id: book_dict} map
        captured BEFORE the caller acquired any world-DB write mutex (STEP-7
        / pre-capture pattern).  Passing it here avoids re-fetching under the
        lock and prevents WAL-lock starvation (the 5th instance fix
        2026-06-04).  When ``None`` the legacy path calls ``fetch_orderbook``
        directly — only valid when the world mutex is NOT held.
        """
        self.connected = True
        self.gap_start = None
        insert_market_channel_connectivity_event(
            self.ingestor._feasibility_conn,
            channel="market_channel",
            transition="connected",
            occurred_at=received_at,
            schema=self.ingestor._feasibility_schema,
        )
        if self.fetch_orderbook is None:
            return []
        return self.ingestor.seed_from_rest(
            self.fetch_orderbook,
            received_at=received_at,
            pre_cached=pre_captured_books,
        )

    def seed_rest_books_in_chunks(
        self,
        *,
        token_ids: Iterable[str],
        received_at: str,
        write_gate: Any,
        commit: Callable[[], None] | None,
        logger: Any | None = None,
        chunk_size: int = REST_SEED_COMMIT_CHUNK_SIZE,
        deadline_monotonic: float | None = None,
        pre_captured_books: dict[str, dict] | None = None,
    ) -> int:
        """Fetch REST books off-lock and commit evidence in bounded chunks.

        The old connect path pre-captured the entire active universe before one
        DB commit. With 100+ weather tokens that let held-position quote evidence
        age past the live preflight/redecision SLA while the thread was still
        fetching. This method keeps network I/O outside the DB write gate but
        commits every small batch, so fresh held/candidate evidence reaches the
        live monitor continuously.
        """

        if self.fetch_orderbook is None:
            return 0
        self.rest_seed_backpressure_count = 0
        self.rest_seed_backpressure_reason = None
        size = max(1, int(chunk_size or REST_SEED_COMMIT_CHUNK_SIZE))
        raw_token_ids = [str(token_id) for token_id in token_ids]
        if isinstance(token_ids, (set, frozenset)):
            raw_token_ids = sorted({str(token_id) for token_id in token_ids})
        open_token_ids = self.ingestor.active_token_ids_open_at(token_ids=raw_token_ids)
        ordered = [
            token_id
            for token_id in dict.fromkeys(raw_token_ids)
            if token_id in open_token_ids
        ]
        written = 0
        for offset in range(0, len(ordered), size):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if logger is not None:
                    logger.warning(
                        "EDLI market-channel REST seed budget exhausted before chunk: "
                        "written=%d remaining=%d",
                        written,
                        max(0, len(ordered) - offset),
                    )
                break
            chunk = ordered[offset: offset + size]
            captured = (
                {
                    token_id: dict(pre_captured_books[token_id])
                    for token_id in chunk
                    if token_id in pre_captured_books
                }
                if pre_captured_books is not None
                else None
            )
            if captured is None:
                try:
                    captured = self._fetch_rest_seed_books(
                        chunk,
                        deadline_monotonic=deadline_monotonic,
                        logger=logger,
                        log_prefix="market_channel: REST seed",
                    )
                except TimeoutError as exc:
                    self.rest_seed_backpressure_count += 1
                    self.rest_seed_backpressure_reason = str(exc)
                    if logger is not None:
                        logger.warning(
                            "EDLI market-channel REST seed fetch budget exhausted: "
                            "written=%d remaining=%d reason=%s",
                            written,
                            max(0, len(ordered) - offset),
                            exc,
                        )
                    break
            if not captured:
                continue
            if self._quote_projection_pump_active:
                results = self.ingestor.seed_from_rest(
                    self.fetch_orderbook,
                    received_at=received_at,
                    pre_cached=captured,
                    token_ids=captured.keys(),
                    coalesce_market_events=True,
                )
                written += len(results)
                continue
            with self.ingestor.defer_market_event_sink():
                try:
                    with write_gate:
                        results = self.ingestor.seed_from_rest(
                            self.fetch_orderbook,
                            received_at=received_at,
                            pre_cached=captured,
                            token_ids=captured.keys(),
                        )
                        if not self.ingestor._market_event_sink_independently_coordinated:
                            self.ingestor.flush_deferred_market_event_sink()
                        if commit is not None:
                            commit()
                    if self.ingestor._market_event_sink_independently_coordinated:
                        self.ingestor.flush_deferred_market_event_sink()
                except TimeoutError as exc:
                    self.rest_seed_backpressure_count += 1
                    self.rest_seed_backpressure_reason = str(exc)
                    if logger is not None:
                        logger.warning(
                            "EDLI market-channel REST seed write backpressure: "
                            "written=%d remaining=%d reason=%s",
                            written,
                            max(0, len(ordered) - offset),
                            exc,
                        )
                    break
            written += len(results)
            if logger is not None:
                logger.debug(
                    "EDLI market-channel REST seed committed chunk: tokens=%d events=%d",
                    len(captured),
                    len(results),
                )
        return written

    async def seed_rest_books_after_subscribe(
        self,
        *,
        token_ids: Iterable[str],
        write_gate: Any,
        commit: Callable[[], None] | None,
        logger: Any | None = None,
        chunk_size: int = REST_SEED_COMMIT_CHUNK_SIZE,
        fetch_batch_size: int = REST_SEED_FETCH_BATCH_SIZE,
        skip_current_generation_depth: bool = False,
    ) -> int:
        """Fetch wide network batches off-loop, then commit bounded DB chunks."""

        if self.fetch_orderbook is None:
            return 0
        raw_token_ids = [str(token_id) for token_id in token_ids]
        if isinstance(token_ids, (set, frozenset)):
            raw_token_ids = sorted(set(raw_token_ids))
        open_token_ids = self.ingestor.active_token_ids_open_at(token_ids=raw_token_ids)
        ordered = [
            token_id
            for token_id in dict.fromkeys(raw_token_ids)
            if token_id in open_token_ids
        ]
        size = max(1, int(chunk_size or REST_SEED_COMMIT_CHUNK_SIZE))
        fetch_size = max(size, int(fetch_batch_size or REST_SEED_FETCH_BATCH_SIZE))
        written = 0
        for offset in range(0, len(ordered), fetch_size):
            chunk = ordered[offset : offset + fetch_size]
            if skip_current_generation_depth:
                chunk = [
                    token_id
                    for token_id in chunk
                    if token_id not in self._current_generation_depth_tokens
                ]
            if not chunk:
                continue
            captured = await asyncio.to_thread(
                self._fetch_rest_seed_books,
                chunk,
                logger=logger,
                log_prefix="market_channel: subscribed REST seed",
            )
            if skip_current_generation_depth:
                captured = {
                    token_id: book
                    for token_id, book in captured.items()
                    if token_id not in self._current_generation_depth_tokens
                }
            if not captured:
                continue
            written += self.seed_rest_books_in_chunks(
                token_ids=chunk,
                received_at=datetime.now(UTC).isoformat(),
                write_gate=write_gate,
                commit=commit,
                logger=logger,
                chunk_size=size,
                pre_captured_books=captured,
            )
        return written

    async def _seed_subscribed_books(
        self,
        *,
        active_token_ids: set[str],
        write_gate: Any,
        commit: Callable[[], None] | None,
        logger: Any | None,
    ) -> int:
        if self.fetch_orderbook is None:
            return 0
        seed_first = tuple(
            dict.fromkeys(
                str(token_id)
                for token_id in self.seed_first_token_ids
                if str(token_id) in active_token_ids
            )
        )
        written = 0
        if seed_first:
            written += await self.seed_rest_books_after_subscribe(
                token_ids=seed_first,
                write_gate=write_gate,
                commit=commit,
                logger=logger,
            )
        await asyncio.sleep(max(0.0, float(self.initial_book_grace_seconds)))
        rest_fallback = (
            active_token_ids
            - set(seed_first)
            - self._current_generation_depth_tokens
        )
        if logger is not None:
            logger.info(
                "EDLI market-channel initial book coverage: ws=%d priority_rest=%d "
                "rest_fallback=%d",
                len(self._current_generation_depth_tokens),
                len(seed_first),
                len(rest_fallback),
            )
        written += await self.seed_rest_books_after_subscribe(
            token_ids=rest_fallback,
            write_gate=write_gate,
            commit=commit,
            logger=logger,
            skip_current_generation_depth=True,
        )
        return written

    async def _sync_subscription_universe(
        self,
        ws: Any,
        *,
        subscribed_token_ids: set[str],
        write_gate: Any,
        commit: Callable[[], None] | None,
        logger: Any | None,
    ) -> None:
        if self.reload_token_metadata is None:
            return
        try:
            reloaded = await asyncio.to_thread(self.reload_token_metadata)
        except Exception as exc:  # noqa: BLE001 - retain the current proven subscription
            self.universe_refresh_error_count += 1
            if logger is not None:
                logger.warning("EDLI market-channel universe refresh failed: %s", exc)
            return
        if isinstance(reloaded, MarketTokenUniverse):
            token_metadata = reloaded.token_metadata
            seed_first_token_ids: Iterable[str] | None = (
                reloaded.seed_first_token_ids
            )
            depth_repair_token_ids: Iterable[str] | None = (
                reloaded.depth_repair_token_ids
            )
        else:
            token_metadata = reloaded
            seed_first_token_ids = None
            depth_repair_token_ids = None
        if not token_metadata and subscribed_token_ids:
            self.universe_refresh_error_count += 1
            if logger is not None:
                logger.warning(
                    "EDLI market-channel universe refresh returned empty; "
                    "retaining %d subscriptions",
                    len(subscribed_token_ids),
                )
            return

        desired_token_ids = self.ingestor.replace_token_metadata(token_metadata)
        if seed_first_token_ids is not None:
            self._replace_seed_first_token_ids(seed_first_token_ids)
        if depth_repair_token_ids is not None:
            self._replace_depth_repair_token_ids(depth_repair_token_ids)
        added = desired_token_ids - subscribed_token_ids
        removed = subscribed_token_ids - desired_token_ids
        if not added and not removed:
            return
        if added:
            await ws.send(
                json.dumps(
                    {"operation": "subscribe", "assets_ids": sorted(added)},
                    separators=(",", ":"),
                )
            )
        if removed:
            await ws.send(
                json.dumps(
                    {"operation": "unsubscribe", "assets_ids": sorted(removed)},
                    separators=(",", ":"),
                )
            )
        subscribed_token_ids.clear()
        subscribed_token_ids.update(desired_token_ids)
        self._current_generation_depth_tokens.difference_update(removed)
        self._missing_depth_tokens.difference_update(removed)
        self._depth_repair_inflight_tokens.difference_update(removed)
        for token_id in removed:
            self._depth_repair_quote_seen_at.pop(token_id, None)
        self.subscription_add_count += len(added)
        self.subscription_remove_count += len(removed)
        if logger is not None:
            logger.info(
                "EDLI market-channel subscription universe updated: active=%d added=%d removed=%d",
                len(subscribed_token_ids),
                len(added),
                len(removed),
            )
        if added:
            await self._seed_subscribed_books(
                active_token_ids=added,
                write_gate=write_gate,
                commit=commit,
                logger=logger,
            )

    async def _refresh_subscription_universe_forever(
        self,
        ws: Any,
        *,
        subscribed_token_ids: set[str],
        connection_done: asyncio.Event,
        stop_event: Any | None,
        write_gate: Any,
        commit: Callable[[], None] | None,
        logger: Any | None,
    ) -> None:
        if self.reload_token_metadata is None:
            return
        interval = max(0.01, float(self.universe_refresh_interval_seconds))
        while not connection_done.is_set() and (
            stop_event is None or not stop_event.is_set()
        ):
            try:
                await asyncio.wait_for(connection_done.wait(), timeout=interval)
            except TimeoutError:
                pass
            if connection_done.is_set() or (
                stop_event is not None and stop_event.is_set()
            ):
                return
            await self._sync_subscription_universe(
                ws,
                subscribed_token_ids=subscribed_token_ids,
                write_gate=write_gate,
                commit=commit,
                logger=logger,
            )

    async def _flush_quote_projection_forever(
        self,
        *,
        wake: asyncio.Event,
        connection_done: asyncio.Event,
        initial_seed_done: asyncio.Event,
        active_token_ids: set[str],
        write_gate: Any,
        commit: Callable[[], None] | None,
        rollback: Callable[[], None] | None,
        logger: Any | None,
        depth_repair_wake: asyncio.Event | None = None,
    ) -> None:
        if self.ingestor._coalescer is None:
            return
        final_contention_attempts = 0
        last_commit_monotonic: float | None = None
        retry_seconds = MARKET_CHANNEL_QUOTE_FLUSH_RETRY_SECONDS
        while True:
            if not wake.is_set():
                try:
                    await asyncio.wait_for(
                        wake.wait(),
                        timeout=MARKET_CHANNEL_QUOTE_FLUSH_RETRY_SECONDS,
                    )
                except TimeoutError:
                    pass
            wake.clear()
            queued = self.ingestor._coalescer.pending_counts()
            if not (queued["lossless"] or queued["market"]):
                if connection_done.is_set() and initial_seed_done.is_set():
                    return
                continue
            if last_commit_monotonic is not None and not connection_done.is_set():
                remaining = MARKET_CHANNEL_QUOTE_MIN_COMMIT_INTERVAL_SECONDS - (
                    time.monotonic() - last_commit_monotonic
                )
                if remaining > 0.0:
                    await asyncio.sleep(remaining)
            try:
                with self.ingestor.defer_market_event_sink():
                    with write_gate:
                        self.ingestor.flush_coalesced(
                            market_budget=MARKET_CHANNEL_QUOTE_FLUSH_BATCH_SIZE,
                            commit=commit,
                            rollback=rollback,
                        )
                    if self.ingestor._market_event_sink_independently_coordinated:
                        self.ingestor.flush_deferred_market_event_sink()
            except (TimeoutError, sqlite3.OperationalError) as exc:
                if not _is_sqlite_write_contention(exc):
                    raise
                self.quote_projection_backpressure_count += 1
                if rollback is not None:
                    rollback()
                if logger is not None:
                    logger.warning(
                        "EDLI market-channel quote projection backpressure; "
                        "socket retained pending=%s: %s",
                        self.ingestor._coalescer.pending_counts(),
                        exc,
                    )
                if connection_done.is_set():
                    final_contention_attempts += 1
                    if final_contention_attempts >= 2:
                        return
                await asyncio.sleep(retry_seconds)
                retry_seconds = min(
                    MARKET_CHANNEL_QUOTE_FLUSH_RETRY_MAX_SECONDS,
                    retry_seconds * 2.0,
                )
                wake.set()
                continue

            last_commit_monotonic = time.monotonic()
            final_contention_attempts = 0
            retry_seconds = MARKET_CHANNEL_QUOTE_FLUSH_RETRY_SECONDS
            self._publish_continuity(
                connected=True,
                observed_at=datetime.now(UTC).isoformat(),
                active_token_count=len(active_token_ids),
                logger=logger,
            )
            queued = self.ingestor._coalescer.pending_counts()
            if not queued["market"]:
                self._clear_durable_missing_depth_tokens(logger=logger)
                self._depth_repair_inflight_tokens.clear()
            if self._missing_depth_tokens and depth_repair_wake is not None:
                depth_repair_wake.set()
            if queued["lossless"] or queued["market"]:
                wake.set()
            elif connection_done.is_set() and initial_seed_done.is_set():
                return

    async def _repair_missing_depth_once(
        self,
        *,
        active_token_ids: set[str],
        write_gate: Any,
        commit: Callable[[], None] | None,
        quote_flush_wake: asyncio.Event,
        logger: Any | None,
    ) -> int:
        for token_id in tuple(self._missing_depth_tokens):
            if token_id not in active_token_ids:
                self._missing_depth_tokens.discard(token_id)
                self._depth_repair_inflight_tokens.discard(token_id)
                self._depth_repair_quote_seen_at.pop(token_id, None)
        batch = sorted(
            self._missing_depth_tokens - self._depth_repair_inflight_tokens
        )[:REST_SEED_FETCH_BATCH_SIZE]
        if not batch:
            return 0
        self.depth_repair_fetch_count += 1
        written = await self.seed_rest_books_after_subscribe(
            token_ids=batch,
            write_gate=write_gate,
            commit=commit,
            logger=logger,
        )
        batch_set = set(batch)
        accepted_scope = (
            batch_set
            & set(self.depth_repair_token_ids)
            & set(active_token_ids)
        )
        accepted_scope &= self.ingestor.active_token_ids_open_at(
            token_ids=accepted_scope
        )
        for token_id in batch_set - accepted_scope:
            self._missing_depth_tokens.discard(token_id)
            self._depth_repair_inflight_tokens.discard(token_id)
            self._depth_repair_quote_seen_at.pop(token_id, None)
        accepted: set[str] = set()
        for token_id in accepted_scope:
            cached = self.ingestor.quote_cache.get(token_id)
            if cached is not None and cached.depth_json not in (None, ""):
                accepted.add(token_id)
                self._depth_repair_quote_seen_at[token_id] = cached.quote_seen_at
        self._depth_repair_inflight_tokens.update(accepted)
        if written:
            quote_flush_wake.set()
            if not self._quote_projection_pump_active:
                self._clear_durable_missing_depth_tokens(logger=logger)
        return written

    async def _repair_missing_depth_forever(
        self,
        *,
        wake: asyncio.Event,
        connection_done: asyncio.Event,
        initial_seed_done: asyncio.Event,
        active_token_ids: set[str],
        write_gate: Any,
        commit: Callable[[], None] | None,
        quote_flush_wake: asyncio.Event,
        logger: Any | None,
    ) -> None:
        await initial_seed_done.wait()
        while not connection_done.is_set():
            if not wake.is_set():
                try:
                    await asyncio.wait_for(wake.wait(), timeout=0.25)
                except TimeoutError:
                    continue
            wake.clear()
            await asyncio.sleep(MARKET_CHANNEL_DEPTH_REPAIR_DEBOUNCE_SECONDS)
            if connection_done.is_set():
                return
            try:
                written = await self._repair_missing_depth_once(
                    active_token_ids=active_token_ids,
                    write_gate=write_gate,
                    commit=commit,
                    quote_flush_wake=quote_flush_wake,
                    logger=logger,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - repair cannot own socket liveness
                self.depth_repair_failure_count += 1
                if logger is not None:
                    logger.warning(
                        "EDLI market-channel depth repair failed: %s: %s",
                        type(exc).__name__,
                        exc,
                    )
                written = 0
            pending_fetch = (
                self._missing_depth_tokens - self._depth_repair_inflight_tokens
            )
            if pending_fetch:
                if not written:
                    await asyncio.sleep(MARKET_CHANNEL_DEPTH_REPAIR_RETRY_SECONDS)
                wake.set()

    def _clear_durable_missing_depth_tokens(self, *, logger: Any | None) -> int:
        pending = sorted(
            self._missing_depth_tokens & self._depth_repair_quote_seen_at.keys()
        )
        if not pending:
            return 0
        schema = self.ingestor._feasibility_schema
        if schema:
            latest_table = f"{schema}.execution_feasibility_latest"
        else:
            from src.state.owner_routed_write import owner_write_target

            latest_table = owner_write_target(
                self.ingestor._feasibility_conn,
                "execution_feasibility_latest",
            )
        if latest_table is None:
            return 0
        durable: set[str] = set()
        try:
            for offset in range(0, len(pending), REST_SEED_FETCH_BATCH_SIZE):
                batch = pending[offset: offset + REST_SEED_FETCH_BATCH_SIZE]
                placeholders = ",".join("?" for _ in batch)
                rows = self.ingestor._feasibility_conn.execute(
                    f"""
                    SELECT DISTINCT token_id, quote_seen_at
                      FROM {latest_table}
                     WHERE token_id IN ({placeholders})
                       AND direction IN ('buy_yes', 'buy_no')
                       AND depth_before_json IS NOT NULL
                       AND depth_before_json != ''
                    """,
                    batch,
                ).fetchall()
                for row in rows:
                    token_id = str(row[0])
                    quote_seen_at = str(row[1])
                    cached = self.ingestor.quote_cache.get(token_id)
                    repair_quote_seen_at = self._depth_repair_quote_seen_at.get(
                        token_id
                    )
                    if (
                        cached is None
                        or cached.depth_json in (None, "")
                        or repair_quote_seen_at is None
                        or _quote_instant(cached.quote_seen_at)
                        != _quote_instant(quote_seen_at)
                        or _quote_instant(quote_seen_at)
                        < _quote_instant(repair_quote_seen_at)
                    ):
                        continue
                    durable.add(token_id)
        except sqlite3.Error as exc:
            self.depth_repair_failure_count += 1
            if logger is not None:
                logger.warning(
                    "EDLI market-channel depth repair durability probe failed: %s: %s",
                    type(exc).__name__,
                    exc,
                )
            return 0
        self._missing_depth_tokens.difference_update(durable)
        self._depth_repair_inflight_tokens.difference_update(durable)
        for token_id in durable:
            self._depth_repair_quote_seen_at.pop(token_id, None)
        return len(durable)

    def _fetch_rest_seed_books(
        self,
        token_ids: list[str],
        *,
        deadline_monotonic: float | None = None,
        logger: Any | None = None,
        log_prefix: str = "market_channel: REST seed",
    ) -> dict[str, dict]:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return {}
        if self.fetch_orderbooks is not None:
            batch_request_started_at = datetime.now(UTC).isoformat()
            try:
                books = self.fetch_orderbooks(token_ids)
            except TimeoutError:
                raise
            except Exception as exc:
                _logger.warning(
                    "%s batch pre-fetch failed for %d tokens: %s: %s",
                    log_prefix,
                    len(token_ids),
                    type(exc).__name__,
                    exc,
                )
                books = {}
            if isinstance(books, dict) and books:
                wanted = set(token_ids)
                pre_captured_books = {}
                for token_id, book in books.items():
                    if str(token_id) not in wanted or not isinstance(book, dict):
                        continue
                    captured = dict(book)
                    venue_timestamp = captured.get("timestamp")
                    if venue_timestamp not in (None, ""):
                        captured.setdefault("venue_timestamp", venue_timestamp)
                    # /books is a current-state observation.  Its timestamp can
                    # be the last mutation time, so it cannot identify when we
                    # observed an unchanged book.  Request start is the latest
                    # conservative cut that cannot overtake an in-flight WS delta.
                    captured["timestamp"] = batch_request_started_at
                    pre_captured_books[str(token_id)] = captured
                if len(pre_captured_books) == len(wanted):
                    return pre_captured_books
                if logger is not None and pre_captured_books:
                    logger.warning(
                        "%s batch pre-fetch returned partial books: captured=%d missing=%d",
                        log_prefix,
                        len(pre_captured_books),
                        max(0, len(wanted) - len(pre_captured_books)),
                    )
                token_ids = [token_id for token_id in token_ids if str(token_id) not in pre_captured_books]
            else:
                pre_captured_books = {}
        else:
            pre_captured_books = {}

        for token_id in token_ids:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if logger is not None:
                    logger.warning(
                        "%s budget exhausted inside chunk: captured=%d remaining=%d",
                        log_prefix,
                        len(pre_captured_books),
                        max(0, len(token_ids) - len(pre_captured_books)),
                    )
                break
            try:
                request_started_at = datetime.now(UTC).isoformat()
                captured = dict(self.fetch_orderbook(token_id))
                venue_timestamp = captured.get("timestamp")
                if venue_timestamp not in (None, ""):
                    captured.setdefault("venue_timestamp", venue_timestamp)
                captured["timestamp"] = request_started_at
                pre_captured_books[token_id] = captured
            except TimeoutError:
                raise
            except Exception as exc:
                _logger.warning(
                    "%s pre-fetch failed for token %s (will skip seed for this token): %s: %s",
                    log_prefix,
                    token_id,
                    type(exc).__name__,
                    exc,
                )
        return pre_captured_books

    def on_disconnect(self, *, gap_start: str) -> None:
        self.connected = False
        self.gap_start = gap_start
        insert_market_channel_connectivity_event(
            self.ingestor._feasibility_conn,
            channel="market_channel",
            transition="disconnected",
            occurred_at=gap_start,
            schema=self.ingestor._feasibility_schema,
        )

    def on_reconnect(
        self,
        *,
        received_at: str,
        pre_captured_books: "dict[str, dict] | None" = None,
        token_ids: Iterable[str] | None = None,
        gap_start: str | None = None,
    ) -> list[EventWriteResult | MarketChannelQuoteResult]:
        """Seed gap-close books on reconnect.

        ``pre_captured_books`` is an optional {token_id: book_dict} map
        captured BEFORE the caller acquired any world-DB write mutex (STEP-7
        / pre-capture pattern — 5th-instance fix 2026-06-04).  When provided,
        no network I/O happens inside this method.  When ``None``, the legacy
        per-token direct-fetch path is used — ONLY valid when the world mutex
        is NOT held (enforced structurally via the assert inside
        ``reconnect_gap_snapshot`` → ``seed_from_rest`` fallback branch).
        """
        self.connected = True
        insert_market_channel_connectivity_event(
            self.ingestor._feasibility_conn,
            channel="market_channel",
            transition="connected",
            occurred_at=received_at,
            schema=self.ingestor._feasibility_schema,
        )
        if self.fetch_orderbook is None:
            self.gap_start = None
            return []
        results = []
        from src.state.db import assert_no_world_mutex_held_for_io

        gap_start_captured = gap_start or self.gap_start or received_at
        active_token_ids = self.ingestor.active_token_ids_open_at(token_ids=token_ids)
        for token_id in sorted(active_token_ids):
            try:
                if pre_captured_books is not None and token_id in pre_captured_books:
                    message = dict(pre_captured_books[token_id])
                else:
                    # Fallback: live fetch — STRUCTURALLY FORBIDDEN under the world
                    # mutex.  Assert here (earlier than the get_orderbook_snapshot
                    # guard) so the violation fires at this callsite with context.
                    assert_no_world_mutex_held_for_io("on_reconnect.fallback_fetch")
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
                gap_start=gap_start_captured,
                received_at=received_at,
            )
            if event is not None:
                result = self.ingestor._commit_market_event(event)
                results.append(result)
        self.gap_start = None
        return results

    def reconnect_rest_books_in_chunks(
        self,
        *,
        token_ids: Iterable[str],
        received_at: str,
        write_gate: Any,
        commit: Callable[[], None] | None,
        logger: Any | None = None,
        chunk_size: int = REST_SEED_COMMIT_CHUNK_SIZE,
    ) -> int:
        """Fetch reconnect gap books off-lock and commit in bounded chunks."""

        if self.fetch_orderbook is None:
            return 0
        gap_start_captured = self.gap_start or received_at
        size = max(1, int(chunk_size or REST_SEED_COMMIT_CHUNK_SIZE))
        ordered = sorted(
            self.ingestor.active_token_ids_open_at(
                token_ids={str(token_id) for token_id in token_ids}
            )
        )
        written = 0
        for offset in range(0, len(ordered), size):
            chunk = ordered[offset: offset + size]
            pre_captured_books = self._fetch_rest_seed_books(
                chunk,
                logger=logger,
                log_prefix="market_channel: reconnect REST seed",
            )
            if not pre_captured_books:
                continue
            with self.ingestor.defer_market_event_sink():
                with write_gate:
                    results = self.on_reconnect(
                        received_at=received_at,
                        pre_captured_books=pre_captured_books,
                        token_ids=pre_captured_books.keys(),
                        gap_start=gap_start_captured,
                    )
                    if not self.ingestor._market_event_sink_independently_coordinated:
                        self.ingestor.flush_deferred_market_event_sink()
                    if commit is not None:
                        commit()
                if self.ingestor._market_event_sink_independently_coordinated:
                    self.ingestor.flush_deferred_market_event_sink()
            written += len(results)
            if logger is not None:
                logger.debug(
                    "EDLI market-channel reconnect REST seed committed chunk: tokens=%d events=%d",
                    len(pre_captured_books),
                    len(results),
                )
        self.gap_start = None
        return written

    async def run_websocket_forever(
        self,
        *,
        endpoint: str = MARKET_CHANNEL_WS_ENDPOINT,
        reconnect_delay_seconds: float = 5.0,
        stop_event: Any | None = None,
        logger: Any | None = None,
        commit: Callable[[], None] | None = None,
        rollback: Callable[[], None] | None = None,
        quote_write_gate: Any | None = None,
        world_event_write_gate: Any | None = None,
        world_event_commit: Callable[[], None] | None = None,
        world_event_rollback: Callable[[], None] | None = None,
    ) -> None:
        """Run the public market channel online.

        This is market-data only. It subscribes by active token IDs, seeds REST
        books on connect, and never writes fill/order state.
        """

        import websockets

        # Quote projection and world-event truth are independent write units.
        # Production passes a TRADE-only gate for quote evidence and a WORLD-only
        # gate for NEW_MARKET_DISCOVERED.
        _quote_write_gate = (
            quote_write_gate
            if quote_write_gate is not None
            else _world_write_mutex()
        )
        _world_event_write_gate = (
            world_event_write_gate
            if world_event_write_gate is not None
            else _quote_write_gate
        )
        _world_event_commit = world_event_commit or commit
        _world_event_rollback = world_event_rollback or rollback

        while stop_event is None or not stop_event.is_set():
            active_token_ids: set[str] = set()
            try:
                active_token_ids = self.ingestor.active_token_ids_open_at()
                async with websockets.connect(
                    endpoint,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=max(1024, len(active_token_ids) * 8),
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "assets_ids": sorted(active_token_ids),
                                "type": "market",
                                "custom_feature_enabled": True,
                            },
                            separators=(",", ":"),
                        )
                    )
                    connected_at = datetime.now(UTC).isoformat()
                    self.connected = True
                    self.gap_start = None
                    self._connected_at = connected_at
                    self._current_generation_depth_tokens.clear()
                    self._missing_depth_tokens.clear()
                    self._depth_repair_inflight_tokens.clear()
                    self._depth_repair_quote_seen_at.clear()
                    with _quote_write_gate:
                        insert_market_channel_connectivity_event(
                            self.ingestor._feasibility_conn,
                            channel="market_channel",
                            transition="connected",
                            occurred_at=connected_at,
                            schema=self.ingestor._feasibility_schema,
                        )
                        if commit is not None:
                            commit()
                    self._publish_continuity(
                        connected=True,
                        observed_at=connected_at,
                        active_token_count=len(active_token_ids),
                        logger=logger,
                    )
                    if logger is not None:
                        logger.info(
                            "EDLI market-channel connected for %d active weather tokens",
                            len(active_token_ids),
                        )
                    pending_world_messages: list[dict[str, Any]] = []
                    connection_done = asyncio.Event()
                    initial_seed_done = asyncio.Event()
                    quote_flush_wake = asyncio.Event()
                    depth_repair_wake = asyncio.Event()
                    self._quote_projection_pump_active = (
                        self.ingestor._coalescer is not None
                    )

                    async def _seed_initial_books() -> None:
                        try:
                            await self._seed_subscribed_books(
                                active_token_ids=active_token_ids,
                                write_gate=_quote_write_gate,
                                commit=commit,
                                logger=logger,
                            )
                        finally:
                            initial_seed_done.set()
                            quote_flush_wake.set()

                    async with asyncio.TaskGroup() as tasks:
                        tasks.create_task(_seed_initial_books())
                        tasks.create_task(
                            self._refresh_subscription_universe_forever(
                                ws,
                                subscribed_token_ids=active_token_ids,
                                connection_done=connection_done,
                                stop_event=stop_event,
                                write_gate=_quote_write_gate,
                                commit=commit,
                                logger=logger,
                            )
                        )
                        tasks.create_task(
                            self._flush_quote_projection_forever(
                                wake=quote_flush_wake,
                                connection_done=connection_done,
                                initial_seed_done=initial_seed_done,
                                active_token_ids=active_token_ids,
                                write_gate=_quote_write_gate,
                                commit=commit,
                                rollback=rollback,
                                logger=logger,
                                depth_repair_wake=depth_repair_wake,
                            )
                        )
                        depth_repair_task = tasks.create_task(
                            self._repair_missing_depth_forever(
                                wake=depth_repair_wake,
                                connection_done=connection_done,
                                initial_seed_done=initial_seed_done,
                                active_token_ids=active_token_ids,
                                write_gate=_quote_write_gate,
                                commit=commit,
                                quote_flush_wake=quote_flush_wake,
                                logger=logger,
                            )
                        )
                        async for raw_message in ws:
                            pending_actions: list[MarketChannelAction] = []
                            quote_messages: list[dict[str, Any]] = []
                            world_messages: list[dict[str, Any]] = []
                            for message in _parse_channel_messages(raw_message):
                                event_type = str(
                                    message.get("event_type") or message.get("type") or ""
                                )
                                if event_type == "new_market":
                                    world_messages.append(message)
                                elif event_type in {"tick_size_change", "market_resolved"}:
                                    action = self.ingestor.handle_message(
                                        message,
                                        received_at=datetime.now(UTC).isoformat(),
                                    )
                                    if isinstance(action, MarketChannelAction):
                                        pending_actions.append(action)
                                else:
                                    quote_messages.append(message)

                            quote_projection_durable = True
                            if self.ingestor._coalescer is not None:
                                for message in quote_messages:
                                    self.ingestor.handle_message(
                                        message,
                                        received_at=datetime.now(UTC).isoformat(),
                                    )
                                    self._record_current_generation_depth(message)
                                queued = self.ingestor._coalescer.pending_counts()
                                should_flush_quotes = bool(
                                    queued["lossless"] or queued["market"]
                                )
                                if should_flush_quotes:
                                    quote_flush_wake.set()
                                should_flush_quotes = False
                            else:
                                should_flush_quotes = bool(quote_messages)

                            if should_flush_quotes:
                                try:
                                    with self.ingestor.defer_market_event_sink():
                                        with _quote_write_gate:
                                            if self.ingestor._coalescer is not None:
                                                self.ingestor.flush_coalesced(
                                                    market_budget=MARKET_CHANNEL_QUOTE_FLUSH_BATCH_SIZE,
                                                    commit=commit,
                                                    rollback=rollback,
                                                )
                                            else:
                                                for message in quote_messages:
                                                    self.ingestor.handle_message(
                                                        message,
                                                        received_at=datetime.now(UTC).isoformat(),
                                                    )
                                                    self._record_current_generation_depth(message)
                                                if not self.ingestor._market_event_sink_independently_coordinated:
                                                    self.ingestor.flush_deferred_market_event_sink()
                                                if commit is not None:
                                                    commit()
                                        if self.ingestor._market_event_sink_independently_coordinated:
                                            self.ingestor.flush_deferred_market_event_sink()
                                    self._clear_durable_missing_depth_tokens(logger=logger)
                                except (TimeoutError, sqlite3.OperationalError) as exc:
                                    if not _is_sqlite_write_contention(exc):
                                        raise
                                    quote_projection_durable = False
                                    if rollback is not None:
                                        rollback()
                                    if logger is not None:
                                        logger.warning(
                                            "EDLI market-channel quote projection backpressure; "
                                            "socket retained pending=%s: %s",
                                            (
                                                self.ingestor._coalescer.pending_counts()
                                                if self.ingestor._coalescer is not None
                                                else {"lossless": 0, "market": len(quote_messages)}
                                            ),
                                            exc,
                                        )

                            if self._missing_depth_tokens:
                                depth_repair_wake.set()

                            pending_world_messages.extend(world_messages)
                            if pending_world_messages:
                                try:
                                    with self.ingestor.defer_market_event_sink():
                                        with _world_event_write_gate:
                                            for message in pending_world_messages:
                                                event = self.ingestor.event_from_message(
                                                    message,
                                                    received_at=datetime.now(UTC).isoformat(),
                                                )
                                                if event is not None:
                                                    self.ingestor._commit_market_event(event)
                                            if not self.ingestor._market_event_sink_independently_coordinated:
                                                self.ingestor.flush_deferred_market_event_sink()
                                            if _world_event_commit is not None:
                                                _world_event_commit()
                                        if self.ingestor._market_event_sink_independently_coordinated:
                                            self.ingestor.flush_deferred_market_event_sink()
                                except (TimeoutError, sqlite3.OperationalError) as exc:
                                    if not _is_sqlite_write_contention(exc):
                                        raise
                                    if _world_event_rollback is not None:
                                        _world_event_rollback()
                                    if logger is not None:
                                        logger.warning(
                                            "EDLI market-channel world-event backpressure; "
                                            "socket retained pending=%d: %s",
                                            len(pending_world_messages),
                                            exc,
                                        )
                                else:
                                    pending_world_messages.clear()

                            if (
                                self.ingestor._coalescer is None
                                and quote_projection_durable
                            ):
                                self._publish_continuity(
                                    connected=True,
                                    observed_at=datetime.now(UTC).isoformat(),
                                    active_token_count=len(active_token_ids),
                                    logger=logger,
                                )
                            for _action in pending_actions:
                                self._enqueue_refresh_action(_action)
                        connection_done.set()
                        depth_repair_task.cancel()
                        quote_flush_wake.set()
                    self._quote_projection_pump_active = False
                    if stop_event is None or not stop_event.is_set():
                        raise ConnectionError("market channel stream ended")
            except Exception as exc:  # noqa: BLE001 - network loop must retry
                gap_start = datetime.now(UTC).isoformat()
                # ROLLBACK-ON-DISCONNECT (2026-05-31): if on_connect/seed_from_rest or
                # the WS message loop raised mid-transaction (e.g. 404 on the first
                # REST-seed token), Python's sqlite3 implicit-BEGIN may have left an
                # open write transaction on the world_conn, holding the WAL write lock
                # indefinitely across the reconnect sleep. Any other writer (the reactor
                # claim(), CollateralLedger heartbeat) then blocks for the full 30s
                # busy_timeout, crashing the reactor cycle. Rollback here releases the
                # lock immediately so the sleep period is lock-free.
                try:
                    with _quote_write_gate:
                        if rollback is not None:
                            rollback()
                        else:
                            self.ingestor._writer.conn.rollback()
                        self.on_disconnect(gap_start=gap_start)
                        if commit is not None:
                            commit()
                    if _world_event_rollback is not None:
                        _world_event_rollback()
                    self._publish_continuity(
                        connected=False,
                        observed_at=gap_start,
                        active_token_count=len(active_token_ids),
                        logger=logger,
                    )
                except Exception as persist_exc:  # noqa: BLE001
                    try:
                        if rollback is not None:
                            rollback()
                        else:
                            self.ingestor._writer.conn.rollback()
                        if _world_event_rollback is not None:
                            _world_event_rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    if logger is not None:
                        logger.error(
                            "EDLI market-channel disconnect transition failed: %s",
                            persist_exc,
                            exc_info=True,
                        )
                if logger is not None:
                    logger.warning("EDLI market-channel disconnected: %s", exc, exc_info=True)
                await asyncio.sleep(reconnect_delay_seconds)

    @staticmethod
    def _refresh_action_key(action: MarketChannelAction) -> tuple[str, str]:
        condition_id = str(action.condition_id or "").strip()
        token_id = str(action.token_id or "").strip()
        if condition_id or token_id:
            return condition_id, token_id
        return f"reason:{str(action.reason or '').strip()}", ""

    def _enqueue_refresh_action(self, action: MarketChannelAction) -> None:
        if not action.refresh_snapshot:
            return
        start = False
        key = self._refresh_action_key(action)
        with self._refresh_worker_lock:
            previous = self._pending_refresh_actions.get(key)
            if previous is not None:
                self.refresh_action_coalesced_count += 1
            self._refresh_action_generation += 1
            self._pending_refresh_actions[key] = _PendingRefreshAction(
                action=action,
                generation=self._refresh_action_generation,
                invalidated=(
                    previous.invalidated
                    if previous is not None
                    and previous.action.reason == action.reason
                    else False
                ),
            )
            if not self._refresh_worker_running:
                self._refresh_worker_running = True
                self._refresh_worker_idle.clear()
                start = True
        if start:
            threading.Thread(
                target=self._drain_refresh_actions,
                name="market-channel-snapshot-refresh",
                daemon=True,
            ).start()

    def _drain_refresh_actions(self) -> None:
        while True:
            with self._refresh_worker_lock:
                if not self._pending_refresh_actions:
                    self._refresh_worker_running = False
                    self._refresh_worker_idle.set()
                    return
                key, pending = min(
                    self._pending_refresh_actions.items(),
                    key=lambda item: (
                        item[1].not_before_monotonic,
                        item[1].generation,
                    ),
                )
                wait_seconds = pending.not_before_monotonic - time.monotonic()
                if wait_seconds <= 0.0:
                    self._pending_refresh_actions.pop(key)
            if wait_seconds > 0.0:
                time.sleep(min(wait_seconds, 0.05))
                continue
            try:
                status, pending = self._attempt_refresh_action(pending)
            except Exception as exc:  # noqa: BLE001 - refresh failure must not kill quote ingest
                _logger.warning(
                    "market-channel snapshot refresh failed off socket loop: %s: %s",
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                status = "deferred"
            if status != "deferred":
                continue
            retry_count = pending.retry_count + 1
            retry_seconds = min(
                MARKET_CHANNEL_REFRESH_ACTION_RETRY_MAX_SECONDS,
                MARKET_CHANNEL_REFRESH_ACTION_RETRY_BASE_SECONDS
                * (2 ** min(retry_count - 1, 10)),
            )
            retried = _PendingRefreshAction(
                action=pending.action,
                generation=pending.generation,
                invalidated=pending.invalidated,
                retry_count=retry_count,
                not_before_monotonic=max(
                    pending.not_before_monotonic,
                    time.monotonic() + retry_seconds,
                ),
            )
            with self._refresh_worker_lock:
                current = self._pending_refresh_actions.get(key)
                if current is None or current.generation < retried.generation:
                    self._pending_refresh_actions[key] = retried

    def wait_refresh_idle(self, timeout: float | None = None) -> bool:
        return self._refresh_worker_idle.wait(timeout)

    def _attempt_refresh_action(
        self,
        pending: _PendingRefreshAction,
    ) -> tuple[_RefreshActionStatus, _PendingRefreshAction]:
        action = pending.action
        if not action.refresh_snapshot:
            return "dropped", pending
        if not pending.invalidated and self.invalidate_snapshot is not None:
            self.invalidate_snapshot(action)
            pending = _PendingRefreshAction(
                action=action,
                generation=pending.generation,
                invalidated=True,
                retry_count=pending.retry_count,
                not_before_monotonic=pending.not_before_monotonic,
            )
        now = datetime.now(UTC)
        if (
            self._refresh_window_start is None
            or (now - self._refresh_window_start) >= timedelta(seconds=max(1.0, self.refresh_window_seconds))
        ):
            self._refresh_window_start = now
            self.refresh_window_action_count = 0
        if self.refresh_window_action_count >= max(1, self.max_refresh_actions_per_window):
            assert self._refresh_window_start is not None
            window_deadline = self._refresh_window_start + timedelta(
                seconds=max(1.0, self.refresh_window_seconds)
            )
            pending = _PendingRefreshAction(
                action=action,
                generation=pending.generation,
                invalidated=pending.invalidated,
                retry_count=pending.retry_count,
                not_before_monotonic=max(
                    pending.not_before_monotonic,
                    time.monotonic()
                    + max(0.0, (window_deadline - now).total_seconds()),
                ),
            )
            return "deferred", pending
        self.refresh_window_action_count += 1
        self.refresh_action_count += 1
        if self.refresh_snapshot is not None:
            try:
                result = self.refresh_snapshot(action)
            except Exception as exc:  # noqa: BLE001 - retain invalidation and retry
                _logger.warning(
                    "market-channel snapshot refresh attempt deferred: %s: %s",
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                return "deferred", pending
            if result == "deferred":
                return "deferred", pending
            if result not in {None, "completed"}:
                raise ValueError(f"invalid refresh snapshot result: {result!r}")
        return "completed", pending

    def _handle_action(self, action: MarketChannelAction) -> _RefreshActionStatus:
        """Run one synchronous attempt; the background queue owns retries."""

        self._refresh_action_generation += 1
        status, _pending = self._attempt_refresh_action(
            _PendingRefreshAction(
                action=action,
                generation=self._refresh_action_generation,
            )
        )
        return status


def run_market_channel_service_forever(
    service: MarketChannelOnlineService,
    *,
    endpoint: str = MARKET_CHANNEL_WS_ENDPOINT,
    stop_event: Any | None = None,
    logger: Any | None = None,
    commit: Callable[[], None] | None = None,
    rollback: Callable[[], None] | None = None,
    quote_write_gate: Any | None = None,
    world_event_write_gate: Any | None = None,
    world_event_commit: Callable[[], None] | None = None,
    world_event_rollback: Callable[[], None] | None = None,
) -> None:
    asyncio.run(
        service.run_websocket_forever(
            endpoint=endpoint,
            stop_event=stop_event,
            logger=logger,
            commit=commit,
            rollback=rollback,
            quote_write_gate=quote_write_gate,
            world_event_write_gate=world_event_write_gate,
            world_event_commit=world_event_commit,
            world_event_rollback=world_event_rollback,
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


# INV-37 (PR415 B5, 2026-06-20): the schemas this insert may target. When the
# write runs on a world-MAIN connection with zeus_trades.db ATTACHed as 'trades'
# (the price-channel attached cross-DB path), the caller passes schema='trades' so
# the row lands in the runtime-read trades.execution_feasibility_evidence and NEVER
# the populated-but-not-read world ghost table. Allowlisted (never interpolate a
# caller string into SQL) and defaulted to "" = unqualified for all other callers.
_FEASIBILITY_EVIDENCE_ALLOWED_SCHEMAS = {"", "trades", "main"}


def insert_execution_feasibility_evidence(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    schema: str = "",
    append_evidence: bool = True,
) -> None:
    insert_execution_feasibility_evidence_batch(
        conn,
        [row],
        schema=schema,
        append_evidence=append_evidence,
    )


def insert_execution_feasibility_evidence_batch(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
    *,
    schema: str = "",
    append_evidence: bool = True,
) -> None:
    if schema not in _FEASIBILITY_EVIDENCE_ALLOWED_SCHEMAS:
        raise ValueError(
            f"insert_execution_feasibility_evidence: disallowed schema {schema!r}"
        )
    values_rows: list[dict[str, Any]] = []
    for row in rows:
        assert_market_channel_not_fill_authority(
            source=str(row.get("fill_truth_source", ""))
        )
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
        values_rows.append(values)
    if not values_rows:
        return
    if schema:
        table = f"{schema}.execution_feasibility_evidence"
        latest_table = f"{schema}.execution_feasibility_latest"
    else:
        # Owner-routed (2026-07-01): both tables are trade-owned; the world copy is an unread ghost (12.9M
        # stray rows). Route to the owner (trades) when reachable, else SKIP so a world-rooted caller never
        # writes the ghost. Non-canonical (:memory:/test) conns keep the legacy bare behavior.
        from src.state.owner_routed_write import owner_write_target
        table = owner_write_target(conn, "execution_feasibility_evidence")
        latest_table = owner_write_target(conn, "execution_feasibility_latest")
        if table is None or latest_table is None:
            return
    latest_row_table = latest_table.rsplit(".", 1)[-1]
    if append_evidence:
        conn.executemany(
            f"""
            INSERT INTO {table} (
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
            ON CONFLICT(evidence_id) DO UPDATE SET
                book_hash_before = excluded.book_hash_before,
                best_bid_before = excluded.best_bid_before,
                best_ask_before = excluded.best_ask_before,
                depth_before_json = excluded.depth_before_json,
                maker_cancel_before_submit = excluded.maker_cancel_before_submit,
                would_have_edge_after_fee = excluded.would_have_edge_after_fee,
                created_at = excluded.created_at,
                schema_version = excluded.schema_version
            """,
            values_rows,
        )
    try:
        conn.executemany(
            f"""
            INSERT INTO {latest_table} (
                token_id, direction, evidence_id, event_id, condition_id, outcome_label,
                quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
                depth_before_json, created_at, schema_version
            ) VALUES (
                :token_id, :direction, :evidence_id, :event_id, :condition_id, :outcome_label,
                :quote_seen_at, :book_hash_before, :best_bid_before, :best_ask_before,
                :depth_before_json, :created_at, :schema_version
            )
            ON CONFLICT(token_id, direction) DO UPDATE SET
                evidence_id = excluded.evidence_id,
                event_id = excluded.event_id,
                condition_id = excluded.condition_id,
                outcome_label = excluded.outcome_label,
                quote_seen_at = excluded.quote_seen_at,
                book_hash_before = excluded.book_hash_before,
                best_bid_before = excluded.best_bid_before,
                best_ask_before = excluded.best_ask_before,
                depth_before_json = excluded.depth_before_json,
                created_at = excluded.created_at,
                schema_version = excluded.schema_version
            WHERE excluded.quote_seen_at >= {latest_row_table}.quote_seen_at
            """,
            values_rows,
        )
    except sqlite3.OperationalError as exc:
        if "execution_feasibility_latest" not in str(exc):
            raise


# W0.2 blind-window metric (docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
# §1 "input->q latency SLA (A2, 'THE metric')"). Same schema-routing allowlist discipline as
# execution_feasibility_evidence: never interpolate a caller string into SQL.
_CONNECTIVITY_EVENT_ALLOWED_SCHEMAS = {"", "trades", "main"}


def insert_market_channel_connectivity_event(
    conn: sqlite3.Connection,
    *,
    channel: str,
    transition: str,
    occurred_at: str,
    schema: str = "",
) -> None:
    """Durably record a WS connect/disconnect/reconnect transition.

    Measure-only (W0): no gate, no enforcement. Idempotent — event_id is a stable
    hash of (channel, transition, occurred_at), so a retried call at the same
    timestamp is a no-op rather than a duplicate row. See
    src/state/schema/market_channel_connectivity_schema.py for BLIND_WINDOW_QUERY,
    the dashboard read side.
    """
    if transition not in ("connected", "disconnected"):
        raise ValueError(f"insert_market_channel_connectivity_event: bad transition {transition!r}")
    if schema not in _CONNECTIVITY_EVENT_ALLOWED_SCHEMAS:
        raise ValueError(f"insert_market_channel_connectivity_event: disallowed schema {schema!r}")
    if schema:
        table = f"{schema}.market_channel_connectivity_events"
    else:
        from src.state.owner_routed_write import owner_write_target
        table = owner_write_target(conn, "market_channel_connectivity_events")
        if table is None:
            return
    event_id = stable_event_id("market_channel_connectivity", channel, transition, occurred_at)
    conn.execute(
        f"""
        INSERT INTO {table} (
            event_id, channel, transition, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(event_id) DO NOTHING
        """,
        (event_id, channel, transition, occurred_at, datetime.now(UTC).isoformat()),
    )


def feasibility_evidence_from_quote(
    event: OpportunityEvent,
    *,
    direction: str,
    order_intent_time: str | None = None,
) -> dict[str, Any]:
    payload = json.loads(event.payload_json)
    # The exchange book is token-scoped, so buy/sell directions for the same
    # token would otherwise duplicate the same depth JSON on every quote tick.
    depth_before_json = payload.get("depth_json") if str(direction).lower().startswith("buy_") else None
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
        "depth_before_json": depth_before_json,
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


def _apply_price_changes_depth(
    depth_json: str | None,
    changes: Iterable[dict[str, Any]],
) -> str | None:
    if not depth_json:
        return None
    try:
        depth = json.loads(depth_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(depth, dict)
        or not isinstance(depth.get("bids"), list)
        or not isinstance(depth.get("asks"), list)
    ):
        return None

    zero = Decimal("0")
    one = Decimal("1")
    levels_by_side: dict[str, dict[Decimal, dict[str, str]]] = {}
    for key in ("bids", "asks"):
        levels: dict[Decimal, dict[str, str]] = {}
        for level in depth[key]:
            if not isinstance(level, dict):
                return None
            try:
                level_price = Decimal(str(level.get("price")))
                level_size = Decimal(str(level.get("size")))
            except (InvalidOperation, TypeError, ValueError):
                return None
            if (
                not zero < level_price <= one
                or level_size <= zero
            ):
                return None
            levels[level_price] = {
                "price": str(level.get("price")),
                "size": str(level.get("size")),
            }
        levels_by_side[key] = levels

    for change in changes:
        try:
            side = str(change.get("side") or "").upper()
            price = Decimal(str(change.get("price")))
            size = Decimal(str(change.get("size")))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if side not in {"BUY", "SELL"} or not zero < price <= one or size < zero:
            return None
        key = "bids" if side == "BUY" else "asks"
        if size == zero:
            levels_by_side[key].pop(price, None)
        else:
            levels_by_side[key][price] = {
                "price": str(change.get("price")),
                "size": str(change.get("size")),
            }
    projected = {
        "bids": [
            levels_by_side["bids"][level_price]
            for level_price in sorted(levels_by_side["bids"], reverse=True)
        ],
        "asks": [
            levels_by_side["asks"][level_price]
            for level_price in sorted(levels_by_side["asks"])
        ],
    }
    return json.dumps(projected, sort_keys=True, separators=(",", ":"))


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


def _quote_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _message_token_id(message: dict[str, Any]) -> str:
    return str(message.get("asset_id") or message.get("token_id") or "")


def _parse_channel_messages(raw_message: object) -> list[dict[str, Any]]:
    if isinstance(raw_message, (bytes, bytearray)):
        raw_message = raw_message.decode("utf-8")
    parsed = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
    if isinstance(parsed, dict):
        if str(parsed.get("event_type") or parsed.get("type") or "") == "price_change":
            changes = parsed.get("price_changes")
            if isinstance(changes, list) and changes:
                outer = {key: value for key, value in parsed.items() if key != "price_changes"}
                grouped: dict[str, list[dict[str, Any]]] = {}
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    grouped.setdefault(
                        str(change.get("asset_id") or ""), []
                    ).append(change)
                return [
                    {**outer, **token_changes[-1], "price_changes": token_changes}
                    for token_changes in grouped.values()
                ]
        return [parsed]
    if isinstance(parsed, list):
        messages: list[dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict):
                messages.extend(_parse_channel_messages(item))
        return messages
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
