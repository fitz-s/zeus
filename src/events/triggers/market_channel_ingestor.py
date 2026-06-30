# Created: prior to 2026-05-24
# Last reused or audited: 2026-06-04
# Authority basis: EDLI v1 §10 online MarketChannelIngestor contract.
#   2026-06-04: 5th-instance WAL-bloat fix — pre-capture pattern for on_connect
#   REST orderbook fetch; seed_from_rest gains pre_cached kwarg; on_connect gains
#   pre_captured_books kwarg; run_websocket_forever pre-captures BEFORE
#   with _world_mutex (fixes 488→601 MB zeus-world.db WAL lock starvation).
"""Online Polymarket market-channel ingestor for EDLI quote/book evidence."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator

from src.events.event_coalescer import EventCoalescer
from src.events.event_writer import EventWriter, EventWriteResult
from src.events.opportunity_event import MarketBookEventPayload, OpportunityEvent, make_opportunity_event
from src.events.idempotency import stable_event_id

UTC = timezone.utc
MARKET_CHANNEL_WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST_SEED_COMMIT_CHUNK_SIZE = 16
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
RestOrderbookBatchFetch = Callable[[list[str]], dict[str, dict]]


class MarketChannelIngestor:
    """Public market-data channel ingestor; never fill/order-state authority."""

    def __init__(
        self,
        writer: EventWriter,
        *,
        active_token_ids: set[str],
        token_metadata: dict[str, MarketTokenMetadata] | None = None,
        feasibility_conn: sqlite3.Connection | None = None,
        feasibility_schema: str = "",
        quote_cache: QuoteCache | None = None,
        coalescer: EventCoalescer | None = None,
        market_event_sink: Callable[[list[OpportunityEvent]], None] | None = None,
    ) -> None:
        self._writer = writer
        self._feasibility_conn = feasibility_conn or writer.conn
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
        self._deferred_market_event_sink_depth = 0
        self._deferred_market_event_sink_events: list[OpportunityEvent] = []
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
        if self._market_top_of_book_unchanged(event):
            self._cache_event_payload(event)
            return None
        self._cache_event_payload(event)
        return self._write_market_event(event)

    def flush_coalesced(
        self,
        *,
        market_budget: int | None = None,
    ) -> list[EventWriteResult | MarketChannelQuoteResult]:
        if self._coalescer is None:
            return []
        events = self._coalescer.drain(market_budget=market_budget)
        results = []
        for event in events:
            result = self._commit_market_event(event)
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

    def seed_from_rest(
        self,
        fetch_orderbook: RestOrderbookFetch,
        *,
        received_at: str,
        pre_cached: "dict[str, dict] | None" = None,
        token_ids: Iterable[str] | None = None,
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
            if event is None:
                continue
            self._cache_event_payload(event)
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
        return _event_from_payload(payload, source="polymarket_market_channel", received_at=received_at)

    def _bba_event(self, message: dict[str, Any], *, received_at: str) -> OpportunityEvent | None:
        token_id = _message_token_id(message)
        if not token_id and message.get("price_changes"):
            token_id = str(message["price_changes"][0].get("asset_id") or "")
        if token_id not in self.active_token_ids_open_at(token_ids=(token_id,)):
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
                self._feasibility_conn,
                feasibility_evidence_from_quote(event, direction=direction),
                schema=self._feasibility_schema,
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

    @contextlib.contextmanager
    def defer_market_event_sink(self) -> Iterator[None]:
        self._deferred_market_event_sink_depth += 1
        try:
            yield
        except BaseException:
            if self._deferred_market_event_sink_depth == 1:
                self._deferred_market_event_sink_events.clear()
            raise
        finally:
            self._deferred_market_event_sink_depth -= 1

    def flush_deferred_market_event_sink(self) -> None:
        if self._market_event_sink is None or not self._deferred_market_event_sink_events:
            self._deferred_market_event_sink_events.clear()
            return
        events = list(self._deferred_market_event_sink_events)
        self._deferred_market_event_sink_events.clear()
        try:
            self._market_event_sink(events)
        except Exception as exc:  # noqa: BLE001 - post-commit derived sink failure.
            _logger.warning(
                "market-channel post-commit sink failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )


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
        predicates.append(f"(market_end_at IS NULL OR market_end_at > '{now_iso}')")
    market_end_expr = "market_end_at" if "market_end_at" in columns else "NULL AS market_end_at"
    where_clause = "WHERE " + " AND ".join(predicates) if predicates else ""
    # Latest snapshot per market (condition_id). Without a captured_at column we
    # cannot rank temporally, so fall back to one row per condition by rowid.
    order_expr = "captured_at DESC, rowid DESC" if has_captured_at else "rowid DESC"
    latest_per_condition = f"""
        SELECT snapshot_id, condition_id, yes_token_id, no_token_id,
               min_tick_size, min_order_size, neg_risk, {market_end_expr},
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
) -> dict[str, MarketTokenMetadata]:
    """Read latest executable snapshot metadata for a bounded token set.

    This is the live priority path for held/recently-decided tokens. It avoids the
    full latest-per-condition window scan used by the broad market-channel
    universe, so the first REST seed for held positions is not blocked behind
    thousands of stale/irrelevant weather tokens.
    """

    tokens = [str(token_id) for token_id in token_ids if str(token_id or "").strip()]
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
    if "market_end_at" in columns:
        now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        predicates.append("(market_end_at IS NULL OR market_end_at > ?)")
    extra_where = (" AND " + " AND ".join(predicates)) if predicates else ""
    extra_params = [now_iso] if "market_end_at" in columns else []
    order_value_expr = "captured_at" if "captured_at" in columns else "rowid"
    order_expr = "captured_at DESC, rowid DESC" if "captured_at" in columns else "rowid DESC"
    market_end_expr = "market_end_at" if "market_end_at" in columns else "NULL AS market_end_at"

    metadata: dict[str, MarketTokenMetadata] = {}
    for token_id in dict.fromkeys(tokens):
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
        snapshot_id, condition_id, yes_token_id, no_token_id, min_tick_size, min_order_size, neg_risk, market_end_at, _ = row
        if str(yes_token_id) == token_id:
            outcome_label = "YES"
        elif str(no_token_id) == token_id:
            outcome_label = "NO"
        else:
            continue
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
    refresh_snapshot: Callable[[MarketChannelAction], None] | None = None
    connected: bool = False
    gap_start: str | None = None
    refresh_action_count: int = 0
    refresh_action_dropped_count: int = 0
    refresh_window_action_count: int = 0
    max_refresh_actions_per_window: int = 5
    refresh_window_seconds: float = 60.0
    seed_first_token_ids: set[str] = field(default_factory=set)
    rest_seed_backpressure_count: int = 0
    rest_seed_backpressure_reason: str | None = None
    _refresh_window_start: datetime | None = None
    _refresh_action_keys: set[tuple[str, str, str]] = field(default_factory=set)

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
        world_mutex: Any,
        commit: Callable[[], None] | None,
        logger: Any | None = None,
        chunk_size: int = REST_SEED_COMMIT_CHUNK_SIZE,
        deadline_monotonic: float | None = None,
    ) -> int:
        """Fetch REST books off-lock and commit evidence in bounded chunks.

        The old connect path pre-captured the entire active universe before one
        DB commit. With 100+ weather tokens that let held-position quote evidence
        age past the live preflight/redecision SLA while the thread was still
        fetching. This method keeps the no-I/O-under-world-mutex invariant but
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
            try:
                pre_captured_books = self._fetch_rest_seed_books(
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
            if not pre_captured_books:
                continue
            with self.ingestor.defer_market_event_sink():
                try:
                    with world_mutex:
                        results = self.ingestor.seed_from_rest(
                            self.fetch_orderbook,
                            received_at=received_at,
                            pre_cached=pre_captured_books,
                            token_ids=pre_captured_books.keys(),
                        )
                        if commit is not None:
                            commit()
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
                self.ingestor.flush_deferred_market_event_sink()
            written += len(results)
            if logger is not None:
                logger.debug(
                    "EDLI market-channel REST seed committed chunk: tokens=%d events=%d",
                    len(pre_captured_books),
                    len(results),
                )
        return written

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
                return {
                    str(token_id): dict(book)
                    for token_id, book in books.items()
                    if str(token_id) in wanted and isinstance(book, dict)
                }

        pre_captured_books: dict[str, dict] = {}
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
                pre_captured_books[token_id] = self.fetch_orderbook(token_id)
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
        world_mutex: Any,
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
                with world_mutex:
                    results = self.on_reconnect(
                        received_at=received_at,
                        pre_captured_books=pre_captured_books,
                        token_ids=pre_captured_books.keys(),
                        gap_start=gap_start_captured,
                    )
                    if commit is not None:
                        commit()
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
        world_mutex: Any | None = None,
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
        _world_mutex = world_mutex if world_mutex is not None else _world_write_mutex()

        while stop_event is None or not stop_event.is_set():
            received_at = datetime.now(UTC).isoformat()
            try:
                active_token_ids = self.ingestor.active_token_ids_open_at()
                seed_first = sorted(
                    str(token_id)
                    for token_id in self.seed_first_token_ids
                    if str(token_id) in active_token_ids
                )
                if self.fetch_orderbook is not None:
                    if seed_first:
                        self.seed_rest_books_in_chunks(
                            token_ids=seed_first,
                            received_at=received_at,
                            world_mutex=_world_mutex,
                            commit=commit,
                            logger=logger,
                            chunk_size=REST_SEED_COMMIT_CHUNK_SIZE,
                        )
                    remaining = sorted(active_token_ids - set(seed_first))
                    self.seed_rest_books_in_chunks(
                        token_ids=remaining,
                        received_at=received_at,
                        world_mutex=_world_mutex,
                        commit=commit,
                        logger=logger,
                    )
                self.connected = True
                self.gap_start = None
                async with websockets.connect(endpoint, ping_interval=20, ping_timeout=20) as ws:
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
                    if logger is not None:
                        logger.info(
                            "EDLI market-channel connected for %d active weather tokens",
                            len(active_token_ids),
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
                        with self.ingestor.defer_market_event_sink():
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
                            self.ingestor.flush_deferred_market_event_sink()
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
                        if rollback is not None:
                            rollback()
                        else:
                            self.ingestor._writer.conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(reconnect_delay_seconds)
                try:
                    _reconnect_at = datetime.now(UTC).isoformat()
                    if self.fetch_orderbook is not None:
                        self.reconnect_rest_books_in_chunks(
                            token_ids=self.ingestor.active_token_ids_open_at(),
                            received_at=_reconnect_at,
                            world_mutex=_world_mutex,
                            commit=commit,
                            logger=logger,
                        )
                    self.connected = True
                    self.gap_start = None
                except Exception as seed_exc:  # noqa: BLE001
                    # Reconnect seed failed (e.g. REST 404). Rollback any partial
                    # transaction from the seed attempt for the same reason above.
                    if commit is not None:
                        try:
                            if rollback is not None:
                                rollback()
                            else:
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
    rollback: Callable[[], None] | None = None,
    world_mutex: Any | None = None,
) -> None:
    asyncio.run(
        service.run_websocket_forever(
            endpoint=endpoint,
            stop_event=stop_event,
            logger=logger,
            commit=commit,
            rollback=rollback,
            world_mutex=world_mutex,
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
) -> None:
    assert_market_channel_not_fill_authority(source=str(row.get("fill_truth_source", "")))
    if schema not in _FEASIBILITY_EVIDENCE_ALLOWED_SCHEMAS:
        raise ValueError(
            f"insert_execution_feasibility_evidence: disallowed schema {schema!r}"
        )
    table = "execution_feasibility_evidence" if not schema else f"{schema}.execution_feasibility_evidence"
    latest_table = "execution_feasibility_latest" if not schema else f"{schema}.execution_feasibility_latest"
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
        values,
    )
    try:
        conn.execute(
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
            """,
            values,
        )
    except sqlite3.OperationalError as exc:
        if "execution_feasibility_latest" not in str(exc):
            raise


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
