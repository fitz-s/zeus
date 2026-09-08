"""Passive, bounded public CLOB /book observer for explicit capture protocols."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from src.state import post_fill_book_repo as repo
from src.state.post_fill_book_repo import validate_book as _validate_book

BOOK_ENDPOINT = "https://clob.polymarket.com/book"
_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)
_KNOWN_REASONS = {
    "BOOK_BODY_TOO_LARGE",
    "BOOK_CONDITION_MISMATCH",
    "BOOK_JSON_INVALID",
    "BOOK_LEVELS_MALFORMED",
    "BOOK_LEVEL_MALFORMED",
    "BOOK_NOT_OBJECT",
    "BOOK_QUOTE_MALFORMED",
    "BOOK_QUOTE_NONFINITE",
    "BOOK_QUOTE_OUT_OF_RANGE",
    "BOOK_TOKEN_MISMATCH",
    "CLOCK_BEFORE_DUE",
    "CLOCK_ROLLBACK",
    "HTTP_ERROR",
    "HTTP_OVERALL_TIMEOUT",
    "REQUEST_WINDOW_INVALID",
    "POST_FILL_CURSOR_MISSING",
}


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_seconds(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return seconds


def classify_source_fact(
    row: Mapping[str, Any], protocol: Mapping[str, Any]
) -> tuple[str, str | None, str | None, str | None]:
    if str(row.get("source") or "") == "FAKE_VENUE":
        return "FAKE_VENUE_EXCLUDED", None, None, None
    identity_fields = (
        "command_id",
        "trade_id",
        "venue_order_id",
        "condition_id",
        "token_id",
        "side",
        "envelope_token_id",
        "envelope_side",
    )
    if any(not row.get(field) for field in identity_fields):
        return "COMMAND_OR_ENVELOPE_IDENTITY_MISSING", None, None, None
    if (
        row.get("token_id") != row.get("envelope_token_id")
        or str(row.get("side") or "").upper()
        != str(row.get("envelope_side") or "").upper()
    ):
        return "COMMAND_ENVELOPE_IDENTITY_MISMATCH", None, None, None
    if str(row.get("side") or "").upper() not in {"BUY", "SELL"}:
        return "COMMAND_SIDE_INVALID", None, None, None
    try:
        size = Decimal(str(row.get("filled_size")))
        price = Decimal(str(row.get("fill_price")))
    except (InvalidOperation, ValueError):
        return "SOURCE_ECONOMICS_INVALID", None, None, None
    if (
        not size.is_finite()
        or not price.is_finite()
        or size <= 0
        or not (Decimal(0) <= price <= Decimal(1))
    ):
        return "SOURCE_ECONOMICS_INVALID", None, None, None
    if str(row.get("state") or "").upper() not in repo.ELIGIBLE_STATES:
        return "STATE_NOT_ECONOMIC", None, None, None
    fill_time = _utc(row.get("venue_timestamp"))
    observed_at = _utc(row.get("observed_at"))
    registered_at = _utc(protocol["registered_at"])
    if fill_time is None:
        return "VENUE_TIMESTAMP_INVALID", None, None, None
    if observed_at is None or fill_time > observed_at:
        return "VENUE_TIMESTAMP_AFTER_OBSERVED", fill_time.isoformat(), None, None
    if registered_at is None or fill_time < registered_at:
        return "HISTORICAL_PRE_REGISTRATION", fill_time.isoformat(), None, None
    due = fill_time + timedelta(seconds=int(protocol["horizon_seconds"]))
    end = due + timedelta(seconds=int(protocol["window_seconds"]))
    return "SCHEDULED", fill_time.isoformat(), due.isoformat(), end.isoformat()


async def _public_book_fetch_async(
    token_id: str, *, timeout_seconds: float, max_body_bytes: int
) -> tuple[int, bytes]:
    import httpx

    url = f"{BOOK_ENDPOINT}?{urlencode({'token_id': token_id})}"
    timeout = httpx.Timeout(timeout_seconds)
    async with asyncio.timeout(timeout_seconds):
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "GET", url, headers={"Accept": "application/json"}
            ) as response:
                chunks: list[bytes] = []
                observed_size = 0
                async for chunk in response.aiter_bytes():
                    observed_size += len(chunk)
                    if observed_size > max_body_bytes:
                        raise ValueError("BOOK_BODY_TOO_LARGE")
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)


async def _fetch_with_overall_deadline(
    token_id: str, *, timeout_seconds: float, max_body_bytes: int
) -> tuple[int, bytes]:
    async with asyncio.timeout(timeout_seconds):
        return await _public_book_fetch_async(
            token_id,
            timeout_seconds=timeout_seconds,
            max_body_bytes=max_body_bytes,
        )


def public_book_fetch(
    token_id: str, *, timeout_seconds: float, max_body_bytes: int
) -> tuple[int, bytes]:
    """Read one public endpoint with bounded body and whole-operation deadline."""
    _positive_seconds(timeout_seconds, "timeout_seconds")
    _positive_int(max_body_bytes, "max_body_bytes")
    try:
        return asyncio.run(
            _fetch_with_overall_deadline(
                token_id,
                timeout_seconds=float(timeout_seconds),
                max_body_bytes=max_body_bytes,
            )
        )
    except TimeoutError as exc:
        raise ValueError("HTTP_OVERALL_TIMEOUT") from exc


def _registered_protocol_ids(conn, protocol_id: str | None) -> list[str]:
    if protocol_id is not None:
        if not isinstance(protocol_id, str) or not protocol_id:
            raise ValueError("invalid post-fill protocol id")
        exists = conn.execute(
            "SELECT 1 FROM post_fill_book_protocols WHERE protocol_id=?",
            (protocol_id,),
        ).fetchone()
        if exists is None:
            raise ValueError("unknown post-fill protocol")
        return [protocol_id]
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT protocol_id FROM post_fill_book_protocols ORDER BY registered_at, protocol_id"
        )
    ]


def _open_observation_connection(deadline_monotonic: float):
    from src.state.db import get_trade_connection

    if time.monotonic() >= deadline_monotonic:
        raise TimeoutError("POST_FILL_CYCLE_DEADLINE")
    conn = get_trade_connection(
        write_class="bulk",
        busy_timeout_ms=0,
        deadline_monotonic=deadline_monotonic,
    )
    try:
        if time.monotonic() >= deadline_monotonic:
            raise TimeoutError("POST_FILL_CYCLE_DEADLINE")
        conn.set_progress_handler(
            lambda: int(time.monotonic() >= deadline_monotonic), 1000
        )
        return conn
    except BaseException:
        conn.close()
        raise


def _append_terminal_or_error(
    *,
    protocol_id: str,
    request: Mapping[str, Any],
    event_type: str,
    reason: str,
    observed_at: str,
    fields: Mapping[str, Any],
    deadline_monotonic: float,
) -> None:
    conn = _open_observation_connection(deadline_monotonic)
    try:
        repo.append_result(
            conn,
            request_id=int(request["request_id"]),
            protocol_id=protocol_id,
            event_type=event_type,
            reason=reason,
            observed_at=observed_at,
            **dict(fields),
        )
    finally:
        conn.close()


def _error_reason(exc: Exception) -> str:
    if isinstance(exc, ValueError) and str(exc) in _KNOWN_REASONS:
        return str(exc)
    return type(exc).__name__


def _source_rows(
    conn: sqlite3.Connection, protocol_id: str, fact_limit: int
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT last_trade_fact_id FROM post_fill_book_cursors WHERE protocol_id=?",
        (protocol_id,),
    ).fetchone()
    if cursor is None:
        raise ValueError("POST_FILL_CURSOR_MISSING")
    return [
        dict(row)
        for row in conn.execute(
            """SELECT
                tf.trade_fact_id, tf.trade_id, tf.venue_order_id, tf.command_id,
                tf.state, tf.filled_size, tf.fill_price, tf.fee_paid_micro,
                tf.tx_hash, tf.source, tf.observed_at, tf.venue_timestamp,
                tf.raw_payload_hash, vc.token_id, vc.side, e.condition_id,
                e.selected_outcome_token_id AS envelope_token_id,
                e.side AS envelope_side
              FROM venue_trade_facts AS tf
              LEFT JOIN venue_commands AS vc ON vc.command_id=tf.command_id
              LEFT JOIN venue_submission_envelopes AS e ON e.envelope_id=vc.envelope_id
              WHERE tf.trade_fact_id > ?
              ORDER BY tf.trade_fact_id
              LIMIT ?""",
            (cursor[0], fact_limit),
        )
    ]


def _result_fields(
    *,
    started: datetime,
    finished: datetime,
    status: int | None,
    raw_body: bytes | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "fetch_started_at": started.isoformat(),
        "fetch_finished_at": finished.isoformat(),
        "endpoint": BOOK_ENDPOINT,
        "http_status": status,
    }
    if raw_body is not None:
        fields["raw_body"] = raw_body
        fields["raw_body_sha256"] = hashlib.sha256(raw_body).hexdigest()
    return fields


def _mark_missed(
    request: Mapping[str, Any],
    reason: str,
    clock: datetime,
    *,
    deadline_monotonic: float,
) -> None:
    _append_terminal_or_error(
        protocol_id=str(request["protocol_id"]),
        request=request,
        event_type="MISSED_WINDOW",
        reason=reason,
        observed_at=clock.isoformat(),
        fields={},
        deadline_monotonic=deadline_monotonic,
    )


def run_cycle(
    *,
    protocol_id: str | None = None,
    fetch: Callable[..., tuple[int, bytes]] = public_book_fetch,
    utc_clock: Callable[[], datetime] = _utc_now,
    fact_limit: int = 100,
    request_limit: int = 10,
    expired_limit: int = 100,
    timeout_seconds: float = 5.0,
    max_body_bytes: int = 262144,
    cycle_deadline_seconds: float = 20.0,
) -> dict[str, int]:
    """Consume registered protocols; no DB connection spans a public HTTP call."""
    _positive_int(fact_limit, "fact_limit")
    _positive_int(request_limit, "request_limit")
    _positive_int(expired_limit, "expired_limit")
    _positive_int(max_body_bytes, "max_body_bytes")
    _positive_seconds(timeout_seconds, "timeout_seconds")
    _positive_seconds(cycle_deadline_seconds, "cycle_deadline_seconds")
    if not _LOCK.acquire(blocking=False):
        return {"skipped_locked": 1}
    lock_handle = None
    try:
        import fcntl
        from src.config import state_path

        def read_clock() -> datetime:
            value = utc_clock()
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError("post-fill observer clock must be aware UTC")
            return value.astimezone(timezone.utc)

        lock_handle = open(
            state_path("post-fill-book-observer.lock"), "a+", encoding="utf-8"
        )
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"skipped_locked": 1}
        cycle_started = time.monotonic()
        deadline_monotonic = cycle_started + cycle_deadline_seconds
        read_clock()
        conn = _open_observation_connection(deadline_monotonic)
        try:
            protocols = _registered_protocol_ids(conn, protocol_id)
        finally:
            conn.close()
        result = {
            "protocols": len(protocols),
            "source_observed": 0,
            "captured": 0,
            "errors": 0,
            "missed": 0,
            "protocol_failures": 0,
        }

        # Each protocol gets its own bounded source scan. A fault records this
        # cycle's protocol failure but does not suppress existing due requests.
        for current_protocol in protocols:
            if time.monotonic() >= deadline_monotonic:
                break
            conn = None
            try:
                conn = _open_observation_connection(deadline_monotonic)
                rows = _source_rows(conn, current_protocol, fact_limit)
                result["source_observed"] += repo.observe_source_facts(
                    conn,
                    protocol_id=current_protocol,
                    rows=rows,
                    observed_at=read_clock().isoformat(),
                    classify=classify_source_fact,
                )
            except Exception as exc:
                result["protocol_failures"] += 1
                _LOG.warning(
                    "post-fill source scan failed protocol=%s error=%s",
                    current_protocol,
                    _error_reason(exc),
                )
            finally:
                if conn is not None:
                    conn.close()

        if time.monotonic() >= deadline_monotonic:
            return result
        now = read_clock()
        conn = _open_observation_connection(deadline_monotonic)
        try:
            conn.row_factory = sqlite3.Row
            active = repo.active_due_requests(
                conn,
                now=now.isoformat(),
                limit=request_limit,
                protocol_ids=protocols,
            )
            expired = repo.expired_due_requests(
                conn,
                now=now.isoformat(),
                limit=expired_limit,
                protocol_ids=protocols,
            )
        finally:
            conn.close()

        for request in active:
            if time.monotonic() - cycle_started >= cycle_deadline_seconds:
                break
            request_clock = read_clock()
            due = _utc(request["due_at"])
            window_end = _utc(request["window_end_at"])
            if due is None or window_end is None:
                _mark_missed(
                    request,
                    "REQUEST_WINDOW_INVALID",
                    request_clock,
                    deadline_monotonic=deadline_monotonic,
                )
                result["missed"] += 1
                continue
            if request_clock > window_end:
                _mark_missed(
                    request,
                    "WINDOW_EXPIRED",
                    request_clock,
                    deadline_monotonic=deadline_monotonic,
                )
                result["missed"] += 1
                continue
            if request_clock < due:
                _append_terminal_or_error(
                    protocol_id=str(request["protocol_id"]),
                    request=request,
                    event_type="FETCH_ERROR",
                    reason="CLOCK_BEFORE_DUE",
                    observed_at=request_clock.isoformat(),
                    fields={},
                    deadline_monotonic=deadline_monotonic,
                )
                result["errors"] += 1
                continue
            started = request_clock
            raw_body: bytes | None = None
            http_status: int | None = None
            try:
                remaining_window = (window_end - started).total_seconds()
                remaining_cycle = cycle_deadline_seconds - (
                    time.monotonic() - cycle_started
                )
                timeout = min(float(timeout_seconds), remaining_window, remaining_cycle)
                _positive_seconds(timeout, "remaining post-fill fetch deadline")
                http_status, raw_body = fetch(
                    str(request["token_id"]),
                    timeout_seconds=timeout,
                    max_body_bytes=max_body_bytes,
                )
                finished = read_clock()
                fields = _result_fields(
                    started=started,
                    finished=finished,
                    status=http_status,
                    raw_body=raw_body,
                )
                if http_status != 200:
                    raise ValueError("HTTP_ERROR")
                parsed, provider_timestamp = _validate_book(raw_body, request)
                fields.update(
                    provider_timestamp_raw=provider_timestamp,
                    provider_asset_id=str(parsed["asset_id"]),
                    provider_market=str(parsed["market"]),
                )
                if finished < started:
                    raise ValueError("CLOCK_ROLLBACK")
                if finished > window_end:
                    _append_terminal_or_error(
                        protocol_id=str(request["protocol_id"]),
                        request=request,
                        event_type="MISSED_WINDOW",
                        reason="FETCH_FINISHED_AFTER_WINDOW",
                        observed_at=finished.isoformat(),
                        fields=fields,
                        deadline_monotonic=deadline_monotonic,
                    )
                    result["missed"] += 1
                else:
                    _append_terminal_or_error(
                        protocol_id=str(request["protocol_id"]),
                        request=request,
                        event_type="CAPTURED",
                        reason="IDENTITY_VALID_RAW_HTTP",
                        observed_at=finished.isoformat(),
                        fields=fields,
                        deadline_monotonic=deadline_monotonic,
                    )
                    result["captured"] += 1
            except Exception as exc:
                finished = read_clock()
                fields = _result_fields(
                    started=started,
                    finished=finished,
                    status=http_status,
                    raw_body=raw_body,
                )
                if finished > window_end:
                    _append_terminal_or_error(
                        protocol_id=str(request["protocol_id"]),
                        request=request,
                        event_type="MISSED_WINDOW",
                        reason="FETCH_FAILED_AFTER_WINDOW",
                        observed_at=finished.isoformat(),
                        fields=fields,
                        deadline_monotonic=deadline_monotonic,
                    )
                    result["missed"] += 1
                else:
                    _append_terminal_or_error(
                        protocol_id=str(request["protocol_id"]),
                        request=request,
                        event_type="FETCH_ERROR",
                        reason=_error_reason(exc),
                        observed_at=finished.isoformat(),
                        fields=fields,
                        deadline_monotonic=deadline_monotonic,
                    )
                    result["errors"] += 1

        # Expired rows are intentionally drained after active capture slots and
        # never consume the active request limit.
        for request in expired:
            if time.monotonic() >= deadline_monotonic:
                break
            _mark_missed(
                request,
                "WINDOW_EXPIRED",
                read_clock(),
                deadline_monotonic=deadline_monotonic,
            )
            result["missed"] += 1
        return result
    finally:
        if lock_handle is not None:
            try:
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()
        _LOCK.release()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="explicit passive post-fill book protocol registration"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    register = subcommands.add_parser("register")
    register.add_argument("--protocol-id", required=True)
    register.add_argument("--caller", required=True)
    register.add_argument("--horizon-seconds", required=True, type=int)
    register.add_argument("--window-seconds", required=True, type=int)
    args = parser.parse_args(argv)
    from src.state.db import get_trade_connection

    conn = get_trade_connection(write_class="bulk")
    try:
        repo.register_protocol(
            conn,
            protocol_id=args.protocol_id,
            caller=args.caller,
            horizon_seconds=args.horizon_seconds,
            window_seconds=args.window_seconds,
        )
    finally:
        conn.close()
    print("registered", args.protocol_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
