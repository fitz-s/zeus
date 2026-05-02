# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M3.yaml
"""Polymarket authenticated user-channel ingest (R3 M3).

This module observes user WebSocket order/trade messages and appends U2 venue
facts. It does not define command grammar, lifecycle state, or M5 exchange
reconciliation. Gaps are recorded in ``src.control.ws_gap_guard`` so submits
fail closed until reconciliation evidence exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Iterable, Optional

from src.control import ws_gap_guard
from src.execution.command_bus import CommandState, IN_FLIGHT_STATES
from src.state.db import get_trade_connection_with_world
from src.state.venue_command_repo import (
    append_event,
    append_order_fact,
    append_position_lot,
    append_trade_fact,
    rollback_optimistic_lot_for_failed_trade,
)

logger = logging.getLogger(__name__)

USER_CHANNEL_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
PING_INTERVAL_SECONDS = 10
DEFAULT_STALE_AFTER_SECONDS = 30
# M5 clean-reconnect proof is stricter than command recovery scanning:
# recovery scans transient submit/cancel uncertainty, while the WS side-effect
# surface must also treat active venue-side orders as unresolved because a gap
# can hide fills/cancels after the venue acknowledged the order.
UNRESOLVED_COMMAND_STATES = tuple(sorted({
    *(state.value for state in IN_FLIGHT_STATES),
    CommandState.SIGNED_PERSISTED.value,
    CommandState.POST_ACKED.value,
    CommandState.ACKED.value,
    CommandState.PARTIAL.value,
}))
UNRESOLVED_LOT_STATES = (
    "OPTIMISTIC_EXPOSURE",
    "CONFIRMED_EXPOSURE",
    "EXIT_PENDING",
    "QUARANTINED",
)


class WSAuthMissing(RuntimeError):
    """Raised when user-channel L2 API credentials are absent."""


class WSDependencyMissing(RuntimeError):
    """Raised when the optional websocket runtime is unavailable."""


@dataclass(frozen=True)
class WSAuth:
    api_key: str
    secret: str
    passphrase: str

    @classmethod
    def from_env(cls) -> "WSAuth":
        api_key = os.environ.get("POLYMARKET_API_KEY", "").strip()
        secret = os.environ.get("POLYMARKET_API_SECRET", "").strip()
        passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "").strip()
        if not api_key or not secret or not passphrase:
            raise WSAuthMissing("POLYMARKET_API_KEY, POLYMARKET_API_SECRET, and POLYMARKET_API_PASSPHRASE are required")
        return cls(api_key=api_key, secret=secret, passphrase=passphrase)

    def as_subscription_auth(self) -> dict[str, str]:
        return {
            "apiKey": self.api_key,
            "secret": self.secret,
            "passphrase": self.passphrase,
        }


WSStatus = ws_gap_guard.WSGapStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_dt(value: Any, *, fallback: datetime | None = None) -> datetime:
    if value is None or value == "":
        return fallback or _utcnow()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback or _utcnow()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decimal_str(value: Any, default: str = "0") -> str:
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _int_shares(value: Any) -> int:
    try:
        return int(Decimal(str(value)).to_integral_value())
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _condition_id(message: dict[str, Any]) -> str:
    return str(message.get("market") or message.get("condition_id") or message.get("conditionId") or "")


def _event_family(message: dict[str, Any]) -> str:
    return str(message.get("event_type") or message.get("type") or "").lower()


def _trade_status(message: dict[str, Any]) -> str:
    return str(message.get("status") or message.get("type") or "").upper()


def _order_state(message: dict[str, Any]) -> str:
    typ = str(message.get("type") or message.get("status") or "").upper()
    if typ in {"CANCELLATION", "CANCELLED", "CANCELED"}:
        return "CANCEL_CONFIRMED"
    if typ in {"UPDATE", "MATCHED"}:
        original = Decimal(_decimal_str(message.get("original_size"), "0"))
        matched = Decimal(_decimal_str(message.get("size_matched") or message.get("matched_size"), "0"))
        if original > 0 and matched >= original:
            return "MATCHED"
        if matched > 0:
            return "PARTIALLY_MATCHED"
    if typ in {"PLACEMENT", "LIVE", "ORDER"}:
        return "LIVE"
    return "LIVE"


def _trade_order_candidates(message: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("taker_order_id", "order_id", "orderID", "orderId", "id"):
        value = message.get(key)
        if value:
            candidates.append(str(value))
    for maker in message.get("maker_orders") or []:
        if isinstance(maker, dict) and maker.get("order_id"):
            candidates.append(str(maker["order_id"]))
    # Preserve order while deduping.
    return list(dict.fromkeys(candidates))


def _lookup_command(conn, venue_order_ids: Iterable[str]) -> Optional[dict[str, Any]]:
    ids = [str(v) for v in venue_order_ids if str(v)]
    if not ids:
        return None
    q = ",".join("?" for _ in ids)
    row = conn.execute(
        f"SELECT * FROM venue_commands WHERE venue_order_id IN ({q}) ORDER BY updated_at DESC LIMIT 1",
        ids,
    ).fetchone()
    return dict(row) if row is not None else None


def _parse_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _position_id_from_command(command: dict[str, Any]) -> Optional[int]:
    """Resolve the canonical position id for a venue command row.

    Executor-created live commands store a short runtime trade id in
    ``position_id`` for operator correlation and thread the durable DB
    ``trade_decisions.trade_id`` through ``decision_id``.  U2 position lots are
    keyed by the canonical integer position id, so the user-channel projection
    must not silently skip lots when ``position_id`` is non-numeric.
    """

    for key in ("position_id", "decision_id"):
        parsed = _parse_positive_int(command.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_entry_buy_command(command: dict[str, Any]) -> bool:
    """Only ENTRY/BUY fills create positive exposure lots.

    User-channel trade facts also arrive for EXIT/SELL commands. Those facts
    confirm venue side effects, but they must not mint new active exposure in
    ``position_lots``; lifecycle/economic-close owners consume them separately.
    """

    return str(command.get("intent_kind") or "").upper() == "ENTRY" and str(command.get("side") or "").upper() == "BUY"


class PolymarketUserChannelIngestor:
    def __init__(
        self,
        adapter: Any,
        condition_ids: list[str],
        api_key: str | None = None,
        *,
        auth: WSAuth | None = None,
        secret: str | None = None,
        passphrase: str | None = None,
        conn_factory: Callable[[], Any] = get_trade_connection_with_world,
        websocket_connect: Callable[..., Any] | None = None,
        endpoint: str = USER_CHANNEL_ENDPOINT,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        on_gap: Callable[[ws_gap_guard.WSGapStatus], Any] | None = None,
        own_connection: bool = True,
    ) -> None:
        self.adapter = adapter
        self.condition_ids = [str(c) for c in condition_ids]
        self.auth = auth or self._auth_from_args(api_key, secret, passphrase)
        self.conn_factory = conn_factory
        self.websocket_connect = websocket_connect
        self.endpoint = endpoint
        self.stale_after_seconds = stale_after_seconds
        self.on_gap = on_gap
        self.own_connection = own_connection
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None

    @staticmethod
    def _auth_from_args(api_key: str | None, secret: str | None, passphrase: str | None) -> WSAuth:
        api_key = (api_key or "").strip()
        secret = (secret or "").strip()
        passphrase = (passphrase or "").strip()
        if not api_key or not secret or not passphrase:
            raise WSAuthMissing("user channel requires api_key, secret, and passphrase")
        return WSAuth(api_key=api_key, secret=secret, passphrase=passphrase)

    @classmethod
    def from_env(
        cls,
        adapter: Any,
        condition_ids: list[str],
        **kwargs: Any,
    ) -> "PolymarketUserChannelIngestor":
        return cls(adapter, condition_ids, auth=WSAuth.from_env(), **kwargs)

    def subscription_message(self) -> dict[str, Any]:
        return {
            "auth": self.auth.as_subscription_auth(),
            "markets": self.condition_ids,
            "type": "user",
        }

    def safe_subscription_summary(self) -> dict[str, Any]:
        return {
            "markets": list(self.condition_ids),
            "type": "user",
            "auth": {"apiKey": "***", "secret": "***", "passphrase": "***"},
        }

    def status(self) -> WSStatus:
        return ws_gap_guard.status()

    async def start(self) -> None:
        self._running = True
        connect = self.websocket_connect or _default_websocket_connect
        try:
            async with connect(self.endpoint) as ws:
                await ws.send(json.dumps(self.subscription_message()))
                self._record_subscribed_message()
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                async for raw in ws:
                    await self.handle_raw_message(raw)
        except WSAuthMissing:
            status = ws_gap_guard.record_gap("auth_missing", subscription_state="AUTH_FAILED")
            self._emit_gap(status)
            raise
        except Exception as exc:
            status = ws_gap_guard.record_gap(f"websocket_disconnect:{type(exc).__name__}")
            self._emit_gap(status)
            raise
        finally:
            self._running = False
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()

    async def _heartbeat_loop(self, ws: Any) -> None:
        while self._running:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            await ws.send("PING")

    async def handle_raw_message(self, raw: str | bytes | dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            if raw in {"PONG", "PING", "pong", "ping"}:
                self._record_subscribed_message()
                return None
            message = json.loads(raw)
        else:
            message = dict(raw)
        return self.handle_message(message)

    def _record_subscribed_message(self, *, observed_at: datetime | None = None) -> WSStatus:
        status = ws_gap_guard.record_message(
            observed_at=observed_at,
            subscription_state="SUBSCRIBED",
            stale_after_seconds=self.stale_after_seconds,
        )
        if status.m5_reconcile_required and self._local_side_effect_surface_empty():
            status = ws_gap_guard.clear_after_no_local_side_effects(
                observed_at=observed_at,
                stale_after_seconds=self.stale_after_seconds,
            )
            logger.info(
                "M3 user-channel gap latch cleared after reconnect: "
                "no unresolved local venue commands, position lots, or M5 findings exist"
            )
        return status

    def _local_side_effect_surface_empty(self) -> bool:
        conn = self.conn_factory()
        try:
            command_placeholders = ",".join("?" for _ in UNRESOLVED_COMMAND_STATES)
            lot_placeholders = ",".join("?" for _ in UNRESOLVED_LOT_STATES)
            checks: tuple[tuple[str, tuple[Any, ...]], ...] = (
                (
                    f"SELECT COUNT(*) FROM venue_commands WHERE state IN ({command_placeholders})",
                    UNRESOLVED_COMMAND_STATES,
                ),
                (
                    f"""
                    SELECT COUNT(*)
                      FROM position_lots lot
                      JOIN (
                        SELECT position_id, MAX(local_sequence) AS max_sequence
                          FROM position_lots
                         GROUP BY position_id
                      ) latest
                        ON latest.position_id = lot.position_id
                       AND latest.max_sequence = lot.local_sequence
                     WHERE lot.state IN ({lot_placeholders})
                    """,
                    UNRESOLVED_LOT_STATES,
                ),
                (
                    "SELECT COUNT(*) FROM exchange_reconcile_findings WHERE resolved_at IS NULL",
                    (),
                ),
            )
            return all(
                int(conn.execute(sql, params).fetchone()[0] or 0) == 0
                for sql, params in checks
            )
        except Exception as exc:
            logger.warning(
                "M3 user-channel clean-reconnect proof unavailable; preserving M5 latch: %s",
                exc,
            )
            return False
        finally:
            if self.own_connection:
                conn.close()

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if self._message_is_auth_failure(message):
            status = ws_gap_guard.record_gap("auth_failed", subscription_state="AUTH_FAILED")
            self._emit_gap(status)
            return None
        condition_id = _condition_id(message)
        if condition_id and self.condition_ids and condition_id not in self.condition_ids:
            status = ws_gap_guard.record_gap(
                "market_subscription_mismatch",
                subscription_state="MARKET_MISMATCH",
                affected_markets=[condition_id],
            )
            self._emit_gap(status)
            return None
        ws_gap_guard.record_message(subscription_state="SUBSCRIBED", stale_after_seconds=self.stale_after_seconds)
        family = _event_family(message)
        if family in {"order", "placement", "update", "cancellation"}:
            return {"order_fact_id": self._handle_order(message)}
        if family in {"trade"} or _trade_status(message) in {"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"}:
            return self._handle_trade(message)
        return None

    def mark_disconnect(self, reason: str = "websocket_disconnect") -> WSStatus:
        status = ws_gap_guard.record_gap(reason, subscription_state="DISCONNECTED")
        self._emit_gap(status)
        return status

    def check_stale(self, *, now: datetime | None = None) -> WSStatus:
        current = ws_gap_guard.status()
        if current.is_stale(now=now):
            status = ws_gap_guard.record_gap("stale_last_message", subscription_state="DISCONNECTED", observed_at=now)
            self._emit_gap(status)
            return status
        return current

    def _handle_order(self, message: dict[str, Any]) -> int:
        venue_order_id = str(message.get("id") or message.get("order_id") or message.get("orderID") or message.get("orderId") or "")
        if not venue_order_id:
            raise ValueError("user-channel order message missing order id")
        conn = self.conn_factory()
        try:
            command = _lookup_command(conn, [venue_order_id])
            if command is None:
                raise ValueError(f"no venue command found for order_id={venue_order_id}")
            state = _order_state(message)
            observed = _parse_dt(message.get("timestamp") or message.get("last_update"))
            fact_id = append_order_fact(
                conn,
                venue_order_id=venue_order_id,
                command_id=command["command_id"],
                state=state,
                remaining_size=_decimal_str(message.get("size") or message.get("remaining_size"), "0"),
                matched_size=_decimal_str(message.get("size_matched") or message.get("matched_size"), "0"),
                source="WS_USER",
                observed_at=observed,
                venue_timestamp=observed,
                raw_payload_hash=_payload_hash(message),
                raw_payload_json=_redacted_message(message),
            )
            if state in {"MATCHED", "PARTIALLY_MATCHED"}:
                self._append_command_event_if_legal(
                    conn,
                    command["command_id"],
                    "PARTIAL_FILL_OBSERVED",
                    observed,
                    {"source": "WS_USER", "venue_order_id": venue_order_id, "order_fact_id": fact_id},
                )
            conn.commit()
            return fact_id
        finally:
            if self.own_connection:
                conn.close()

    def _handle_trade(self, message: dict[str, Any]) -> dict[str, Any]:
        trade_id = str(message.get("id") or message.get("trade_id") or message.get("tradeId") or "")
        if not trade_id:
            raise ValueError("user-channel trade message missing trade id")
        status = _trade_status(message)
        if status not in {"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"}:
            raise ValueError(f"unsupported trade status={status!r}")
        candidates = _trade_order_candidates(message)
        conn = self.conn_factory()
        try:
            command = _lookup_command(conn, candidates)
            if command is None:
                raise ValueError(f"no venue command found for trade order ids={candidates}")
            venue_order_id = str(command.get("venue_order_id") or candidates[0])
            observed = _parse_dt(message.get("timestamp") or message.get("matchtime") or message.get("last_update"))
            fact_id = append_trade_fact(
                conn,
                trade_id=trade_id,
                venue_order_id=venue_order_id,
                command_id=command["command_id"],
                state=status,
                filled_size=_decimal_str(message.get("size"), "0"),
                fill_price=_decimal_str(message.get("price"), "0"),
                source="WS_USER",
                observed_at=observed,
                venue_timestamp=observed,
                raw_payload_hash=_payload_hash(message),
                raw_payload_json=_redacted_message(message),
                tx_hash=message.get("transaction_hash") or message.get("tx_hash"),
                block_number=message.get("block_number"),
                confirmation_count=message.get("confirmation_count"),
            )
            command_event = None
            if status in {"MATCHED", "MINED"}:
                command_event = "PARTIAL_FILL_OBSERVED"
                if self._optimistic_trade_fact_for_trade(conn, trade_id) is None:
                    self._append_position_lot(conn, command, fact_id, "OPTIMISTIC_EXPOSURE", message, observed)
            elif status == "CONFIRMED":
                command_event = "FILL_CONFIRMED"
                self._append_position_lot(conn, command, fact_id, "CONFIRMED_EXPOSURE", message, observed)
            elif status == "FAILED":
                self._rollback_failed_trade(conn, trade_id, fact_id, observed)
            if command_event:
                self._append_command_event_if_legal(
                    conn,
                    command["command_id"],
                    command_event,
                    observed,
                    {"source": "WS_USER", "trade_id": trade_id, "trade_fact_id": fact_id},
                )
            conn.commit()
            return {"trade_fact_id": fact_id, "command_event": command_event}
        finally:
            if self.own_connection:
                conn.close()

    def _optimistic_trade_fact_for_trade(self, conn: Any, trade_id: str) -> int | None:
        row = conn.execute(
            """
            SELECT lot.source_trade_fact_id
              FROM position_lots lot
              JOIN venue_trade_facts tf
                ON tf.trade_fact_id = lot.source_trade_fact_id
             WHERE tf.trade_id = ?
               AND lot.state = 'OPTIMISTIC_EXPOSURE'
             ORDER BY tf.local_sequence DESC, lot.lot_id DESC
             LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
        if row is None:
            return None
        return int(row["source_trade_fact_id"])

    def _append_position_lot(
        self,
        conn: Any,
        command: dict[str, Any],
        trade_fact_id: int,
        state: str,
        message: dict[str, Any],
        observed: datetime,
    ) -> int | None:
        if not _is_entry_buy_command(command):
            return None
        position_id = _position_id_from_command(command)
        if position_id is None:
            return None
        return append_position_lot(
            conn,
            position_id=position_id,
            state=state,
            shares=_int_shares(message.get("size")),
            entry_price_avg=_decimal_str(message.get("price"), "0"),
            source_command_id=command["command_id"],
            source_trade_fact_id=trade_fact_id,
            captured_at=observed,
            state_changed_at=observed,
            source="WS_USER",
            observed_at=observed,
            raw_payload_json=_redacted_message(message),
        )

    def _rollback_failed_trade(self, conn: Any, trade_id: str, failed_fact_id: int, observed: datetime) -> int | None:
        optimistic_fact_id = self._optimistic_trade_fact_for_trade(conn, trade_id)
        if optimistic_fact_id is None:
            return None
        return rollback_optimistic_lot_for_failed_trade(
            conn,
            source_trade_fact_id=optimistic_fact_id,
            failed_trade_fact_id=failed_fact_id,
            state_changed_at=observed,
        )

    def _append_command_event_if_legal(
        self,
        conn: Any,
        command_id: str,
        event_type: str,
        observed: datetime,
        payload: dict[str, Any],
    ) -> None:
        try:
            append_event(conn, command_id=command_id, event_type=event_type, occurred_at=observed, payload=payload)
        except ValueError as exc:
            # Facts are the authoritative U2 path for M3. Command events are an
            # equivalence bridge only when the M1 grammar permits them.
            logger.info("Skipping WS command event %s for %s: %s", event_type, command_id, exc)

    @staticmethod
    def _message_is_auth_failure(message: dict[str, Any]) -> bool:
        text = _canonical_json(message).lower()
        return "auth" in text and any(term in text for term in ("fail", "unauthorized", "invalid"))

    def _emit_gap(self, status: WSStatus) -> None:
        if self.on_gap is not None:
            self.on_gap(status)


def _redacted_message(message: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(message)
    redacted.pop("auth", None)
    for key in ("apiKey", "secret", "passphrase"):
        if key in redacted:
            redacted[key] = "***"
    return redacted


def _websocket_connect_accepts_proxy_kwarg(connect_fn: Any) -> bool:
    """Return True if `connect_fn` accepts a `proxy=` kwarg.

    websockets>=15 added the `proxy` parameter; 10.x/12.x do not have it. The
    repo does not pin `websockets` in `requirements.txt`, so an environment
    with an older release would otherwise raise TypeError before any WS
    session opens (PR #35 P1 review).

    Detection via signature inspection rather than version string keeps the
    check robust across vendored, backported, or test-mocked builds. A
    function with `**kwargs` is treated as supporting `proxy` because the
    upstream client will silently accept it.
    """
    try:
        import inspect
        sig = inspect.signature(connect_fn)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.name == "proxy":
            return True
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def _default_websocket_connect(endpoint: str) -> Any:
    try:
        import websockets  # type: ignore
    except Exception as exc:  # pragma: no cover - env-dependent optional import
        raise WSDependencyMissing("websockets package is required for live M3 user-channel ingest") from exc
    # websockets>=16 defaults to proxy=True and auto-detects HTTPS_PROXY from the
    # environment.  The daemon plist sets HTTPS_PROXY=localhost:7890 (used for REST
    # calls through the local proxy) but wss://ws-subscriptions-clob.polymarket.com
    # is intentionally excluded from that proxy — WebSocket keepalive traffic must
    # reach the server directly without CONNECT-tunnel overhead.  Pass proxy=None
    # to bypass the proxy for all WS connections regardless of env var state.
    #
    # Older websockets (10.x/12.x) do not accept the proxy kwarg; passing it
    # would raise TypeError before the connection opens. We probe the signature
    # and only forward proxy=None when the installed client supports it.
    # Antibodies:
    #   tests/test_user_channel_ws_auto_derive.py::test_ws_connect_bypasses_proxy
    #   tests/test_user_channel_ws_auto_derive.py::test_ws_connect_skips_proxy_kwarg_on_older_websockets
    if _websocket_connect_accepts_proxy_kwarg(websockets.connect):
        return websockets.connect(endpoint, proxy=None)
    return websockets.connect(endpoint)
