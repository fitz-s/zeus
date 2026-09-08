"""Only trade-DB writer for passive post-fill book observation lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping

from src.state.schema.post_fill_book_observations_schema import ensure_table

ELIGIBLE_STATES = {"MATCHED", "MINED", "CONFIRMED"}


@contextmanager
def _atomic(conn: sqlite3.Connection, name: str):
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        conn.execute(f"RELEASE SAVEPOINT {name}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {name}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def validate_book(
    raw: bytes, request: Mapping[str, Any]
) -> tuple[dict[str, Any], str | None]:
    try:
        body = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BOOK_JSON_INVALID") from exc
    if not isinstance(body, dict):
        raise ValueError("BOOK_NOT_OBJECT")
    if str(body.get("asset_id") or "") != str(request["token_id"]):
        raise ValueError("BOOK_TOKEN_MISMATCH")
    if str(body.get("market") or "").lower() != str(request["condition_id"]).lower():
        raise ValueError("BOOK_CONDITION_MISMATCH")
    for side in ("bids", "asks"):
        levels = body.get(side)
        if not isinstance(levels, list):
            raise ValueError("BOOK_LEVELS_MALFORMED")
        for level in levels:
            if not isinstance(level, dict):
                raise ValueError("BOOK_LEVEL_MALFORMED")
            try:
                if isinstance(level["price"], bool) or isinstance(level["size"], bool):
                    raise ValueError("boolean quote")
                price = Decimal(str(level["price"]))
                size = Decimal(str(level["size"]))
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                raise ValueError("BOOK_QUOTE_MALFORMED") from exc
            if not price.is_finite() or not size.is_finite():
                raise ValueError("BOOK_QUOTE_NONFINITE")
            if price < 0 or price > 1 or size < 0:
                raise ValueError("BOOK_QUOTE_OUT_OF_RANGE")
    provider_timestamp = body.get("timestamp")
    return body, None if provider_timestamp is None else str(provider_timestamp)


def register_protocol(
    conn: sqlite3.Connection,
    *,
    protocol_id: str,
    caller: str,
    horizon_seconds: int,
    window_seconds: int,
    clock: Callable[[], datetime] = utc_now,
) -> None:
    ensure_table(conn)
    if (
        not isinstance(protocol_id, str)
        or not protocol_id.strip()
        or not isinstance(caller, str)
        or not caller.strip()
    ):
        raise ValueError("invalid explicit post-fill protocol identity")
    if (
        isinstance(horizon_seconds, bool)
        or isinstance(window_seconds, bool)
        or not isinstance(horizon_seconds, int)
        or not isinstance(window_seconds, int)
        or horizon_seconds <= 0
        or window_seconds <= 0
    ):
        raise ValueError("invalid explicit post-fill protocol registration")
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("post-fill registration clock must be aware")
    registration_time = now.astimezone(timezone.utc).isoformat()
    with _atomic(conn, "post_fill_book_register"):
        baseline = int(
            conn.execute(
                "SELECT COALESCE(MAX(trade_fact_id), 0) FROM venue_trade_facts"
            ).fetchone()[0]
        )
        existing = conn.execute(
            "SELECT caller,horizon_seconds,window_seconds FROM post_fill_book_protocols WHERE protocol_id=?",
            (protocol_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) == (caller, horizon_seconds, window_seconds):
                return
            raise ValueError("post-fill protocol registration conflict")
        content = {
            "protocol_id": protocol_id,
            "caller": caller,
            "horizon_seconds": horizon_seconds,
            "window_seconds": window_seconds,
            "registered_at": registration_time,
            "source_fact_baseline": baseline,
        }
        content_hash = canonical_json_hash(content)
        conn.execute(
            "INSERT INTO post_fill_book_protocols VALUES (?,?,?,?,?,?,?,?)",
            (
                protocol_id,
                caller,
                horizon_seconds,
                window_seconds,
                registration_time,
                baseline,
                content_hash,
                registration_time,
            ),
        )
        conn.execute(
            "INSERT INTO post_fill_book_cursors VALUES (?,?,?)",
            (protocol_id, baseline, registration_time),
        )


def protocol(conn: sqlite3.Connection, protocol_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM post_fill_book_protocols WHERE protocol_id=?", (protocol_id,)
    ).fetchone()
    if row is None:
        raise ValueError("unknown post-fill protocol")
    return row


def observe_source_facts(
    conn: sqlite3.Connection,
    *,
    protocol_id: str,
    rows: Iterable[Mapping[str, Any]],
    observed_at: str,
    classify,
) -> int:
    """Append every source fact and atomically advance cursor/create first request."""
    p = protocol(conn, protocol_id)
    count = 0
    max_id = None
    with _atomic(conn, "post_fill_book_source"):
        for row in rows:
            fact_id = int(row["trade_fact_id"])
            if fact_id <= int(p["source_fact_baseline"]):
                continue
            reason, fill_time, due_at, end_at = classify(row, p)
            conn.execute(
                """INSERT OR IGNORE INTO post_fill_book_observation_events
              (protocol_id,source_trade_fact_id,command_id,trade_id,event_type,reason,source_state,source_type,source_raw_payload_hash,source_venue_timestamp,source_filled_size,source_fill_price,source_fee_paid_micro,source_tx_hash,source_condition_id,source_token_id,source_side,clock_provenance_verified,fill_time_utc,due_at,window_end_at,observed_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    protocol_id,
                    fact_id,
                    row["command_id"],
                    row["trade_id"],
                    "SOURCE_OBSERVED",
                    reason,
                    row["state"],
                    row.get("source"),
                    row.get("raw_payload_hash"),
                    row.get("venue_timestamp"),
                    row["filled_size"],
                    row["fill_price"],
                    row.get("fee_paid_micro"),
                    row.get("tx_hash"),
                    row.get("condition_id"),
                    row.get("token_id"),
                    row.get("side"),
                    0,
                    fill_time,
                    due_at,
                    end_at,
                    observed_at,
                ),
            )
            if reason == "SCHEDULED":
                conn.execute(
                    """INSERT OR IGNORE INTO post_fill_book_requests
                  (protocol_id,command_id,trade_id,source_trade_fact_id,venue_order_id,condition_id,token_id,side,source_state,source_venue_timestamp,fill_time_utc,due_at,window_end_at,filled_size,fill_price,fee_paid_micro,tx_hash,created_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        protocol_id,
                        row["command_id"],
                        row["trade_id"],
                        fact_id,
                        row["venue_order_id"],
                        row["condition_id"],
                        row["token_id"],
                        row["side"],
                        row["state"],
                        row.get("venue_timestamp"),
                        fill_time,
                        due_at,
                        end_at,
                        row["filled_size"],
                        row["fill_price"],
                        row.get("fee_paid_micro"),
                        row.get("tx_hash"),
                        observed_at,
                    ),
                )
            max_id = fact_id if max_id is None else max(max_id, fact_id)
            count += 1
        if max_id is not None:
            conn.execute(
                "UPDATE post_fill_book_cursors SET last_trade_fact_id=?, updated_at=? WHERE protocol_id=? AND last_trade_fact_id <= ?",
                (max_id, observed_at, protocol_id, max_id),
            )
    return count


def _due_requests(
    conn: sqlite3.Connection,
    *,
    now: str,
    limit: int,
    active: bool,
    protocol_ids: Iterable[str] | None,
) -> list[sqlite3.Row]:
    if protocol_ids is not None:
        protocol_ids = tuple(protocol_ids)
        if not protocol_ids:
            return []
        protocol_filter = (
            f" AND r.protocol_id IN ({','.join('?' for _ in protocol_ids)})"
        )
    else:
        protocol_filter = ""
    window_filter = (
        "r.due_at <= ? AND r.window_end_at >= ?" if active else "r.window_end_at < ?"
    )
    params: tuple[Any, ...]
    if active:
        params = (now, now)
    else:
        params = (now,)
    params += tuple(protocol_ids or ()) + (limit,)
    return list(
        conn.execute(
            f"""SELECT r.* FROM post_fill_book_requests r
             WHERE NOT EXISTS (
                SELECT 1 FROM post_fill_book_observation_events e
                 WHERE e.request_id=r.request_id
                   AND e.event_type IN ('CAPTURED','MISSED_WINDOW')
             )
             AND {window_filter}{protocol_filter}
             ORDER BY r.window_end_at, r.due_at, r.request_id
             LIMIT ?""",
            params,
        )
    )


def active_due_requests(
    conn: sqlite3.Connection,
    *,
    now: str,
    limit: int,
    protocol_ids: Iterable[str] | None = None,
) -> list[sqlite3.Row]:
    return _due_requests(
        conn,
        now=now,
        limit=limit,
        active=True,
        protocol_ids=protocol_ids,
    )


def expired_due_requests(
    conn: sqlite3.Connection,
    *,
    now: str,
    limit: int,
    protocol_ids: Iterable[str] | None = None,
) -> list[sqlite3.Row]:
    return _due_requests(
        conn,
        now=now,
        limit=limit,
        active=False,
        protocol_ids=protocol_ids,
    )


def append_result(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    protocol_id: str,
    event_type: str,
    reason: str,
    observed_at: str,
    **fields: Any,
) -> None:
    if event_type not in {"CAPTURED", "FETCH_ERROR", "MISSED_WINDOW"}:
        raise ValueError("invalid capture event")
    request = conn.execute(
        "SELECT protocol_id FROM post_fill_book_requests WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if request is None or str(request[0]) != protocol_id:
        raise ValueError("post-fill result request/protocol mismatch")
    allowed = {
        "fetch_started_at",
        "fetch_finished_at",
        "endpoint",
        "http_status",
        "raw_body",
        "raw_body_sha256",
        "provider_timestamp_raw",
        "provider_asset_id",
        "provider_market",
    }
    if set(fields) - allowed:
        raise ValueError("post-fill result fields are not allowlisted")
    request_row = conn.execute(
        "SELECT token_id,condition_id,due_at,window_end_at,created_at FROM post_fill_book_requests WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if event_type == "CAPTURED":
        required = {
            "fetch_started_at",
            "fetch_finished_at",
            "endpoint",
            "http_status",
            "raw_body",
            "raw_body_sha256",
            "provider_asset_id",
            "provider_market",
        }
        if not required.issubset(fields):
            raise ValueError("captured post-fill result incomplete")
        body = fields["raw_body"]
        if (
            not isinstance(body, bytes)
            or hashlib.sha256(body).hexdigest() != fields["raw_body_sha256"]
        ):
            raise ValueError("captured post-fill raw body hash invalid")
        if (
            fields["endpoint"] != "https://clob.polymarket.com/book"
            or fields["http_status"] != 200
        ):
            raise ValueError("captured post-fill endpoint/status invalid")
        if (
            str(fields["provider_asset_id"]) != str(request_row[0])
            or str(fields["provider_market"]).lower() != str(request_row[1]).lower()
        ):
            raise ValueError("captured post-fill provider identity invalid")
        _, provider_timestamp = validate_book(
            body, {"token_id": request_row[0], "condition_id": request_row[1]}
        )
        if (
            "provider_timestamp_raw" in fields
            and fields["provider_timestamp_raw"] != provider_timestamp
        ):
            raise ValueError("captured post-fill provider timestamp invalid")
        fields["provider_timestamp_raw"] = provider_timestamp
        start = _parse_aware_utc(fields["fetch_started_at"])
        finish = _parse_aware_utc(fields["fetch_finished_at"])
        due = _parse_aware_utc(request_row[2])
        end = _parse_aware_utc(request_row[3])
        created = _parse_aware_utc(request_row[4])
        observed = _parse_aware_utc(observed_at)
        if (
            start is None
            or finish is None
            or due is None
            or end is None
            or created is None
            or observed is None
            or not (max(due, created) <= start <= finish <= end and finish <= observed)
        ):
            raise ValueError("captured post-fill window invalid")
    columns = [
        "protocol_id",
        "request_id",
        "event_type",
        "reason",
        "observed_at",
    ] + list(fields)
    values = [protocol_id, request_id, event_type, reason, observed_at] + list(
        fields.values()
    )
    try:
        with _atomic(conn, "post_fill_book_result"):
            if event_type in {"CAPTURED", "MISSED_WINDOW"}:
                terminal = conn.execute(
                    "SELECT 1 FROM post_fill_book_observation_events WHERE request_id=? AND event_type IN ('CAPTURED','MISSED_WINDOW')",
                    (request_id,),
                ).fetchone()
                if terminal is not None:
                    return
            conn.execute(
                f"INSERT INTO post_fill_book_observation_events ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                values,
            )
    except sqlite3.IntegrityError:
        if event_type not in {"CAPTURED", "MISSED_WINDOW"}:
            raise
        terminal = conn.execute(
            "SELECT 1 FROM post_fill_book_observation_events WHERE request_id=? AND event_type IN ('CAPTURED','MISSED_WINDOW')",
            (request_id,),
        ).fetchone()
        if terminal is None:
            raise


def _parse_aware_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
