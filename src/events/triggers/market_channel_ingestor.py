"""Online Polymarket market-channel ingestor for EDLI quote/book evidence."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from src.events.event_writer import EventWriter, EventWriteResult
from src.events.opportunity_event import MarketBookEventPayload, OpportunityEvent, make_opportunity_event
from src.events.idempotency import stable_event_id

UTC = timezone.utc
MARKET_CHANNEL_WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class MarketChannelAuthorityError(ValueError):
    pass


@dataclass(frozen=True)
class MarketChannelAction:
    refresh_snapshot: bool = False
    reason: str = ""


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
        quote_cache: QuoteCache | None = None,
    ) -> None:
        self._writer = writer
        self._active_token_ids = active_token_ids
        self.quote_cache = quote_cache or QuoteCache()

    def handle_message(self, message: dict[str, Any], *, received_at: str) -> EventWriteResult | MarketChannelAction | None:
        event_type = str(message.get("event_type") or message.get("type") or "")
        if event_type == "tick_size_change":
            return MarketChannelAction(refresh_snapshot=True, reason="tick_size_change")
        if event_type == "market_resolved":
            return MarketChannelAction(refresh_snapshot=True, reason="market_resolved")
        event = self.event_from_message(message, received_at=received_at)
        if event is None:
            return None
        self._cache_event_payload(event)
        result = self._writer.write(event)
        self._write_feasibility_evidence(event)
        return result

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
            message = dict(fetch_orderbook(token_id))
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
        token_id = str(message.get("asset_id") or "")
        if token_id not in self._active_token_ids:
            return None
        payload = MarketBookEventPayload(
            condition_id=str(message.get("market") or message.get("condition_id") or ""),
            token_id=token_id,
            outcome_label=str(message.get("outcome_label") or "YES"),  # type: ignore[arg-type]
            event_type="BOOK_SNAPSHOT",
            quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at,
            book_hash=str(message.get("hash") or ""),
            best_bid=_best_price(message.get("bids"), best="bid"),
            best_ask=_best_price(message.get("asks"), best="ask"),
            depth_json=json.dumps({"bids": message.get("bids", []), "asks": message.get("asks", [])}, sort_keys=True),
            gap_start=gap_start if gap_marked else None,
            gap_recovered_at=received_at if gap_marked else None,
        )
        return _event_from_payload(payload, source="polymarket_market_channel", received_at=received_at)

    def _bba_event(self, message: dict[str, Any], *, received_at: str) -> OpportunityEvent | None:
        token_id = str(message.get("asset_id") or "")
        if not token_id and message.get("price_changes"):
            token_id = str(message["price_changes"][0].get("asset_id") or "")
        if token_id not in self._active_token_ids:
            return None
        change = message.get("price_changes", [{}])[0] if message.get("price_changes") else message
        payload = MarketBookEventPayload(
            condition_id=str(message.get("market") or ""),
            token_id=token_id,
            outcome_label=str(message.get("outcome_label") or "YES"),  # type: ignore[arg-type]
            event_type="BEST_BID_ASK_CHANGED",
            quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at,
            book_hash=str(change.get("hash") or ""),
            best_bid=_float_or_none(change.get("best_bid")),
            best_ask=_float_or_none(change.get("best_ask")),
            depth_json=None,
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
                outcome_label=str(payload.get("outcome_label") or "YES"),  # type: ignore[arg-type]
                event_type=event.event_type,  # type: ignore[arg-type]
                quote_seen_at=str(payload.get("quote_seen_at") or event.available_at),
                book_hash=payload.get("book_hash"),
                best_bid=_float_or_none(payload.get("best_bid")),
                best_ask=_float_or_none(payload.get("best_ask")),
                depth_json=payload.get("depth_json"),
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
        else:
            directions = ("buy_yes", "sell_yes")
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
        payload = MarketBookEventPayload(
            condition_id=str(message.get("condition_id") or message.get("market") or ""),
            token_id=token_id,
            outcome_label="YES",
            event_type="NEW_MARKET_DISCOVERED",
            quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at,
            depth_json=json.dumps(message, sort_keys=True, default=str),
        )
        return _event_from_payload(payload, source="polymarket_market_channel", received_at=received_at)


def active_weather_token_ids_from_snapshots(conn: sqlite3.Connection, *, limit: int = 500) -> set[str]:
    """Read active YES/NO token ids from executable snapshot truth."""

    if not _table_exists(conn, "executable_market_snapshots"):
        return set()
    columns = _table_columns(conn, "executable_market_snapshots")
    if not {"yes_token_id", "no_token_id"} <= columns:
        return set()
    predicates = []
    if "active" in columns:
        predicates.append("COALESCE(active, 0) = 1")
    if "closed" in columns:
        predicates.append("COALESCE(closed, 0) = 0")
    if "event_slug" in columns:
        predicates.append("(LOWER(COALESCE(event_slug, '')) LIKE '%weather%' OR LOWER(COALESCE(event_slug, '')) LIKE '%temperature%')")
    where_clause = "WHERE " + " AND ".join(predicates) if predicates else ""
    rows = conn.execute(
        f"""
        SELECT yes_token_id, no_token_id
        FROM executable_market_snapshots
        {where_clause}
        ORDER BY captured_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    token_ids: set[str] = set()
    for yes_token_id, no_token_id in rows:
        if yes_token_id:
            token_ids.add(str(yes_token_id))
        if no_token_id:
            token_ids.add(str(no_token_id))
    return token_ids


@dataclass
class MarketChannelOnlineService:
    """Coordinator for connect/reconnect seed and public-channel deltas."""

    ingestor: MarketChannelIngestor
    fetch_orderbook: RestOrderbookFetch | None = None
    connected: bool = False
    gap_start: str | None = None

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
            message = dict(self.fetch_orderbook(token_id))
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

        while stop_event is None or not stop_event.is_set():
            received_at = datetime.now(UTC).isoformat()
            try:
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
                        for message in _parse_channel_messages(raw_message):
                            self.ingestor.handle_message(
                                message,
                                received_at=datetime.now(UTC).isoformat(),
                            )
                        if commit is not None:
                            commit()
            except Exception as exc:  # noqa: BLE001 - network loop must retry
                gap_start = datetime.now(UTC).isoformat()
                self.on_disconnect(gap_start=gap_start)
                if logger is not None:
                    logger.warning("EDLI market-channel disconnected: %s", exc, exc_info=True)
                await asyncio.sleep(reconnect_delay_seconds)
                try:
                    self.on_reconnect(received_at=datetime.now(UTC).isoformat())
                    if commit is not None:
                        commit()
                except Exception as seed_exc:  # noqa: BLE001
                    if logger is not None:
                        logger.warning(
                            "EDLI market-channel reconnect seed failed: %s",
                            seed_exc,
                            exc_info=True,
                        )


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


def assert_market_channel_not_fill_authority(*, source: str) -> None:
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
