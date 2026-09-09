# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: W4.2 (SCH-W1.2-ORDER-STATE cross-reference, docs/rebuild/schema_packets/
#   w1_2_order_state_extension_schema_packet_2026-07-02.md:197-198) — relocated verbatim out of
#   src/execution/maker_rest_escalation.py as part of that module's TTL-ownership handover to
#   src.state.order_state_predicates.rest_deadline_exceeded + src.execution.staleness_cancel.
"""Durable command-journal cancel executor, shared by every "cancel a snapshotted list of
already-open orders" caller.

This is GENERIC infrastructure, not staleness/TTL-specific: it turns a list of already-classified
cancel candidates (``{command_id, venue_order_id, cancel_reason, cancel_action, cancel_detail,
...}``) into durable ``CANCEL_REQUESTED``/``CANCEL_ACKED``/``CANCEL_REPLACE_BLOCKED`` command-journal
events around the venue cancel call. It originated inside ``maker_rest_escalation.py`` (the K4.0
GTC-deadline job) but other callers already depended on it before that module's deletion:
``main._edli_boot_invalid_pending_entry_authority_cancel_once`` (boot-time authority cancel),
``main._edli_continuous_redecision_screen_cycle`` (§4.5 rest-pull cancel), and (as of W4.2)
``main._c3_staleness_cancel_cycle``'s own carried-over invalid-entry-authority lane. None of those
are staleness/TTL classification — they each build their own ``expired``-shaped entry list and hand
it to this executor. Relocating this function here (byte-identical body) lets
``maker_rest_escalation.py`` be deleted without breaking any of them.
"""

from __future__ import annotations

import logging
import json
import math
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from src.contracts.canonical_lifecycle import is_cancel_confirmed_status

logger = logging.getLogger("zeus.venue_cancel_journal")

UTC = timezone.utc

SCREEN_CANCEL_OBLIGATION_KIND = "screen_redecision_cancel_v1"
SCREEN_CANCEL_OBLIGATION_OWNER = "command_recovery"
SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION = 1
SCREEN_CANCEL_WITNESS_MAX_AGE_SECONDS = 2.0
SCREEN_CANCEL_POST_SIDE_EFFECT_JOURNAL_RESERVE_SECONDS = 0.5
_SCREEN_CANCEL_LIVE_STATUSES = frozenset({"LIVE", "OPEN", "RESTING"})
_SCREEN_CANCEL_TERMINAL_STATUSES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "FILLED"}
)

TERMINAL_COMMAND_STATES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "REJECTED", "SUBMIT_REJECTED"}
)


class _TerminalCommandNoop(RuntimeError):
    def __init__(self, command_id: str, state: str, event_type: str) -> None:
        super().__init__(
            f"terminal command {command_id} already {state}; skipping {event_type}"
        )
        self.command_id = command_id
        self.state = state
        self.event_type = event_type


def _finite_nonnegative(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else None


def _current_cancel_matched_size(
    entry: dict[str, Any],
    clob: Any,
    *,
    conn_factory: Callable[..., sqlite3.Connection],
    close_connections: bool,
    deadline_monotonic: float | None = None,
) -> tuple[float | None, str]:
    """Rebind a cancel candidate to current cumulative fill truth.

    The rest screen is selection-time evidence. A user-channel trade may land
    before the cancel side effect while the latest order fact still says
    ``matched_size=0``. Read both the venue point order and canonical distinct
    trade facts immediately before cancel; the maximum is the only safe
    cumulative fill witness.
    """

    command_id = str(entry.get("command_id") or "")
    order_id = str(entry.get("venue_order_id") or "")
    values = [
        value
        for value in (_finite_nonnegative(entry.get("matched_size")),)
        if value is not None
    ]
    sources = ["screen"] if values else []

    get_order = getattr(clob, "get_order", None)
    if callable(get_order):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return None, "deadline_exhausted"
        try:
            point_order = get_order(order_id, deadline_monotonic=deadline_monotonic)
        except Exception as exc:  # noqa: BLE001 - stale truth may not authorize cancel.
            logger.warning(
                "venue_cancel_journal: pre-cancel point-order refresh failed "
                "command=%s order=%s: %r",
                command_id,
                order_id,
                exc,
            )
            return None, "venue_point_order_unavailable"
        if point_order is None:
            return None, "venue_point_order_absent"
        raw = getattr(point_order, "raw", point_order)
        if not isinstance(raw, dict):
            return None, "venue_point_order_shape_invalid"
        point_matched = next(
            (
                parsed
                for key in ("_v2_matched_size", "size_matched", "sizeMatched")
                if (parsed := _finite_nonnegative(raw.get(key))) is not None
            ),
            None,
        )
        if point_matched is None:
            return None, "venue_point_order_matched_missing"
        values.append(point_matched)
        sources.append("venue_point_order")

    conn = conn_factory()
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "venue_order_facts" in tables:
            row = conn.execute(
                """
                SELECT matched_size
                  FROM venue_order_facts
                 WHERE command_id = ? AND venue_order_id = ?
                 ORDER BY local_sequence DESC
                 LIMIT 1
                """,
                (command_id, order_id),
            ).fetchone()
            local_order_matched = _finite_nonnegative(row[0]) if row else None
            if local_order_matched is not None:
                values.append(local_order_matched)
                sources.append("order_fact")
        if "venue_trade_facts" in tables:
            row = conn.execute(
                """
                WITH latest_trade AS (
                    SELECT filled_size, state,
                           ROW_NUMBER() OVER (
                               PARTITION BY trade_id ORDER BY local_sequence DESC
                           ) AS rn
                      FROM venue_trade_facts
                     WHERE command_id = ? AND venue_order_id = ?
                )
                SELECT COALESCE(SUM(CAST(filled_size AS REAL)), 0)
                  FROM latest_trade
                 WHERE rn = 1
                   AND state IN ('MATCHED', 'MINED', 'CONFIRMED')
                """,
                (command_id, order_id),
            ).fetchone()
            trade_matched = _finite_nonnegative(row[0]) if row else None
            if trade_matched is not None:
                values.append(trade_matched)
                sources.append("trade_fact")
    except sqlite3.Error as exc:
        logger.warning(
            "venue_cancel_journal: pre-cancel canonical fill refresh failed "
            "command=%s order=%s: %r",
            command_id,
            order_id,
            exc,
        )
        return None, "canonical_fill_unavailable"
    finally:
        _close_conn_if_needed(conn, close=close_connections)

    if not values:
        return None, "matched_size_unavailable"
    return max(values), "+".join(sources)


def _close_conn_if_needed(conn: sqlite3.Connection, *, close: bool) -> None:
    if not close:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text or "busy" in text


def _screen_cancel_obligation_id(command_id: str, venue_order_id: str) -> str:
    return f"screen_redecision_v1:{command_id}:{venue_order_id}"


def _screen_cancel_marker_payload(
    *,
    command_id: str,
    venue_order_id: str,
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the versioned marker consumed only by screen recovery."""

    return {
        "schema_version": SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION,
        "obligation_id": _screen_cancel_obligation_id(command_id, venue_order_id),
        "obligation_kind": SCREEN_CANCEL_OBLIGATION_KIND,
        "owner": SCREEN_CANCEL_OBLIGATION_OWNER,
        # These aliases are retained as descriptive provenance for old tooling;
        # selector authority is the exact tuple above, never an alias alone.
        "cancel_request_kind": "screen_redecision_v1",
        "dispatch_owner": SCREEN_CANCEL_OBLIGATION_OWNER,
        "venue_order_id": venue_order_id,
        "source": "continuous_redecision_screen",
        "dispatch_status": "queued",
        "cancel_reason": str(entry.get("cancel_reason") or ""),
        "cancel_action": str(entry.get("cancel_action") or ""),
        "cancel_detail": entry.get("cancel_detail"),
        "min_order_size": entry.get("min_order_size"),
    }


def _screen_cancel_marker_is_exact(
    payload: object,
    *,
    command_id: str,
    venue_order_id: str,
) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("schema_version") == SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION
        and payload.get("obligation_id") == _screen_cancel_obligation_id(command_id, venue_order_id)
        and payload.get("obligation_kind") == SCREEN_CANCEL_OBLIGATION_KIND
        and payload.get("owner") == SCREEN_CANCEL_OBLIGATION_OWNER
        and str(payload.get("venue_order_id") or "") == venue_order_id
    )


def _screen_cancel_json(payload_json: object) -> dict[str, Any] | None:
    try:
        payload = json.loads(str(payload_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _screen_cancel_iso_expired(value: object, *, now: datetime | None = None) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return True
    current = now or datetime.now(UTC)
    return parsed <= current


def _screen_cancel_configure_deadline(
    conn: sqlite3.Connection,
    deadline_monotonic: float | None,
) -> bool:
    """Bind SQLite lock waiting and progress abort to one absolute deadline."""

    if deadline_monotonic is None:
        return True
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0.0:
        return False
    conn.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
    conn.set_progress_handler(
        lambda: int(time.monotonic() >= deadline_monotonic),
        100,
    )
    return True


def _screen_cancel_previous_busy_timeout(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.Error:
        return None


def _screen_cancel_restore_deadline(
    conn: sqlite3.Connection,
    previous_busy_timeout: int | None,
) -> None:
    try:
        conn.set_progress_handler(None, 0)
        if previous_busy_timeout is not None:
            conn.execute(f"PRAGMA busy_timeout = {previous_busy_timeout}")
    except sqlite3.Error:
        logger.debug("screen cancel deadline state restore failed", exc_info=True)


def _screen_cancel_witness_is_fresh(
    witness: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: float = SCREEN_CANCEL_WITNESS_MAX_AGE_SECONDS,
) -> bool:
    if witness.get("source") != "authenticated_point_order":
        return False
    try:
        captured_at = datetime.fromisoformat(
            str(witness.get("captured_at") or "").replace("Z", "+00:00")
        )
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=UTC)
        age = (now or datetime.now(UTC)) - captured_at
    except (TypeError, ValueError):
        return False
    return timedelta(0) <= age <= timedelta(seconds=max_age_seconds)


def _screen_cancel_point_witness(
    clob: Any,
    order_id: str,
    *,
    deadline_monotonic: float,
) -> dict[str, Any] | None:
    """Read one authenticated point order without holding a DB connection."""

    if time.monotonic() >= deadline_monotonic:
        return None
    get_order = getattr(clob, "get_order", None)
    if not callable(get_order):
        return None
    try:
        raw = get_order(order_id, deadline_monotonic=deadline_monotonic)
    except Exception as exc:  # noqa: BLE001 - missing fresh truth defers safely
        logger.warning("screen cancel point-order read failed order=%s: %r", order_id, exc)
        return None
    if raw is None:
        return {
            "order_id": order_id,
            "status": "ABSENT",
            "matched_size": "0",
            "source": "authenticated_point_order",
            "captured_at": datetime.now(UTC).isoformat(),
        }
    data = getattr(raw, "raw", raw)
    if not isinstance(data, dict):
        return None
    observed_id = str(
        data.get("orderID") or data.get("orderId") or data.get("order_id") or data.get("id") or ""
    )
    if observed_id and observed_id != order_id:
        return None
    status = str(data.get("status") or data.get("state") or "").strip().upper()
    if status.startswith("ORDER_STATUS_"):
        status = status.removeprefix("ORDER_STATUS_")
    if status not in _SCREEN_CANCEL_LIVE_STATUSES | _SCREEN_CANCEL_TERMINAL_STATUSES:
        return None
    matched = next(
        (
            parsed
            for key in ("_v2_matched_size", "size_matched", "sizeMatched", "matched_size", "matchedSize")
            if (parsed := _finite_nonnegative(data.get(key))) is not None
        ),
        None,
    )
    if matched is None:
        return None
    return {
        "order_id": order_id,
        "status": status,
        "matched_size": str(matched),
        "remaining_size": data.get("remaining_size") or data.get("remainingSize"),
        "source": "authenticated_point_order",
        "captured_at": datetime.now(UTC).isoformat(),
    }


def _cancel_journal_event_already_persisted(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    event_type: str,
    venue_order_id: str,
) -> bool:
    try:
        rows = conn.execute(
            """
            SELECT payload_json
              FROM venue_command_events
             WHERE command_id = ?
               AND event_type = ?
             ORDER BY sequence_no DESC
            """,
            (command_id, event_type),
        ).fetchall()
    except Exception:
        return False
    import json

    for row in rows:
        try:
            raw = row["payload_json"]
        except Exception:
            raw = row[0]
        try:
            payload = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and str(payload.get("venue_order_id") or "") == venue_order_id:
            return True
    return False


def _append_cancel_journal_event(
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    command_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
    close_connections: bool,
    deadline_monotonic: float | None = None,
) -> None:
    from src.state.venue_command_repo import append_event

    venue_order_id = str(payload.get("venue_order_id") or "")
    for attempt in range(1, 4):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError("screen_cancel_obligation_deadline_exhausted")
        conn = conn_factory()
        try:
            row = conn.execute(
                "SELECT state FROM venue_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if row is not None:
                current_state = str(row["state"] if isinstance(row, sqlite3.Row) else row[0]).upper()
                if current_state in TERMINAL_COMMAND_STATES:
                    raise _TerminalCommandNoop(command_id, current_state, event_type)
            if venue_order_id and _cancel_journal_event_already_persisted(
                conn,
                command_id=command_id,
                event_type=event_type,
                venue_order_id=venue_order_id,
            ):
                return
            append_event(
                conn,
                command_id=command_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            )
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise TimeoutError("screen_cancel_obligation_deadline_exhausted")
            conn.commit()
            return
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            if isinstance(exc, _TerminalCommandNoop):
                raise
            if not _is_sqlite_lock_error(exc) or attempt == 3:
                raise
            logger.warning(
                "venue_cancel_journal: retrying %s journal command=%s order=%s "
                "after sqlite lock (attempt %d/3): %s",
                event_type,
                command_id,
                venue_order_id,
                attempt,
                exc,
            )
            delay = 0.25 * attempt
            if (
                deadline_monotonic is not None
                and time.monotonic() + delay >= deadline_monotonic
            ):
                raise TimeoutError("screen_cancel_obligation_deadline_exhausted")
            time.sleep(delay)
        finally:
            _close_conn_if_needed(conn, close=close_connections)


def persist_screen_redecision_cancel_obligations(
    entries: list[dict[str, Any]],
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    deadline_monotonic: float,
    close_connections: bool = True,
) -> dict[str, int]:
    """Persist exact screen cancel debt; this function never performs venue I/O.

    SCOPE is one command/order pair.  DRAIN is the bounded command-recovery
    lane; RESET is its later CANCEL_ACKED/CANCELLED terminal evidence.
    """

    stats = {"queued": 0, "deferred": 0, "terminal": 0, "errors": 0}
    for entry in entries:
        if time.monotonic() >= deadline_monotonic:
            stats["deferred"] += 1
            break
        command_id = str(entry.get("command_id") or "")
        venue_order_id = str(entry.get("venue_order_id") or "")
        if not command_id or not venue_order_id:
            stats["errors"] += 1
            continue
        marker = _screen_cancel_marker_payload(
            command_id=command_id,
            venue_order_id=venue_order_id,
            entry=entry,
        )
        try:
            # Read the command and all cancel markers in this same short
            # connection. A legacy marker is typed deferral, never an upgrade;
            # an exact marker is idempotent and must not be counted as queued.
            conn = conn_factory()
            try:
                command = conn.execute(
                    "SELECT state, venue_order_id FROM venue_commands WHERE command_id = ?",
                    (command_id,),
                ).fetchone()
                if command is None:
                    stats["errors"] += 1
                    continue
                current_order = str((command["venue_order_id"] if isinstance(command, sqlite3.Row) else command[1]) or "")
                if current_order and current_order != venue_order_id:
                    stats["errors"] += 1
                    continue
                existing = conn.execute(
                    "SELECT payload_json FROM venue_command_events WHERE command_id = ? AND event_type = 'CANCEL_REQUESTED'",
                    (command_id,),
                ).fetchall()
                decoded = [_screen_cancel_json(row[0]) for row in existing]
                if any(_screen_cancel_marker_is_exact(p, command_id=command_id, venue_order_id=venue_order_id) for p in decoded):
                    continue
                if existing:
                    stats["deferred"] += 1
                    continue
            finally:
                _close_conn_if_needed(conn, close=close_connections)
            _append_cancel_journal_event(
                conn_factory,
                command_id=command_id,
                event_type="CANCEL_REQUESTED",
                occurred_at=datetime.now(UTC).isoformat(),
                payload=marker,
                close_connections=close_connections,
                deadline_monotonic=deadline_monotonic,
            )
            stats["queued"] += 1
        except _TerminalCommandNoop:
            stats["terminal"] += 1
        except TimeoutError:
            stats["deferred"] += 1
            break
        except Exception:
            logger.exception("venue_cancel_journal: screen cancel obligation failed command=%s", command_id)
            stats["errors"] += 1
    return stats


def find_screen_redecision_cancel_obligations(
    conn: sqlite3.Connection,
    *,
    deadline_monotonic: float | None = None,
) -> list[dict[str, Any]]:
    """Return exact screen obligations, including a prior dispatch lease.

    Marker identity is deliberately stricter than the historical
    ``CANCEL_REQUESTED`` shape. Legacy or malformed rows are invisible and are
    left to the established recovery semantics.
    """

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return []

    rows = conn.execute(
        """
        SELECT cmd.command_id, cmd.venue_order_id, cmd.state, cmd.last_event_id,
               cmd.created_at, marker.payload_json, marker.sequence_no,
               latest.event_id, latest.event_type, latest.payload_json,
               latest.sequence_no
          FROM venue_commands cmd
          JOIN venue_command_events marker
            ON marker.command_id = cmd.command_id
           AND marker.event_type = 'CANCEL_REQUESTED'
          JOIN venue_command_events latest
            ON latest.command_id = cmd.command_id
           AND latest.sequence_no = (
               SELECT MAX(sequence_no)
                 FROM venue_command_events
                WHERE command_id = cmd.command_id
           )
         WHERE cmd.state = 'CANCEL_PENDING'
           AND (
               SELECT COUNT(*)
                 FROM venue_command_events marker_count
                WHERE marker_count.command_id = cmd.command_id
                  AND marker_count.event_type = 'CANCEL_REQUESTED'
           ) = 1
         ORDER BY cmd.updated_at ASC, cmd.command_id ASC, marker.sequence_no DESC
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            break
        command_id = str(row[0] or "")
        if not command_id or command_id in seen:
            continue
        venue_order_id = str(row[1] or "")
        payload = _screen_cancel_json(row[5])
        if not _screen_cancel_marker_is_exact(
            payload,
            command_id=command_id,
            venue_order_id=venue_order_id,
        ):
            continue
        latest_payload = _screen_cancel_json(row[9])
        if str(row[8] or "") == "CANCEL_DISPATCH_STARTED" and not isinstance(latest_payload, dict):
            continue
        if str(row[8] or "") == "CANCEL_DISPATCH_STARTED" and (
            latest_payload.get("obligation_id") != payload.get("obligation_id")
            or latest_payload.get("obligation_kind") != payload.get("obligation_kind")
        ):
            continue
        seen.add(command_id)
        out.append({
            "command_id": command_id,
            "venue_order_id": venue_order_id,
            "created_at": str(row[4] or ""),
            "fact_state": "SCREEN_CANCEL_OBLIGATION",
            "matched_size": "0",
            "min_order_size": payload.get("min_order_size"),
            "cancel_reason": str(payload.get("cancel_reason") or ""),
            "cancel_action": str(payload.get("cancel_action") or ""),
            "cancel_detail": payload.get("cancel_detail"),
            "obligation_id": payload["obligation_id"],
            "obligation_kind": payload["obligation_kind"],
            "obligation_owner": payload["owner"],
            "latest_event_id": str(row[7] or ""),
            "latest_event_type": str(row[8] or ""),
            "latest_event_payload": latest_payload or {},
            "latest_sequence_no": int(row[10] or 0),
        })
    return out


def claim_screen_redecision_cancel_obligation(
    conn: sqlite3.Connection, *, command_id: str, venue_order_id: str,
    owner: str, generation: int, attempt_id: str, expires_at: str,
    fresh_witness: Mapping[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    deadline_monotonic: float | None = None,
) -> bool:
    """Atomically compare, claim, or terminalize one screen obligation.

    The venue witness is captured before this transaction starts. The
    transaction itself holds only SQLite state, so no DB connection crosses the
    network boundary. An expired lease is reclaimable only with a new
    authenticated point-order witness.
    """
    from src.state.venue_command_repo import append_event

    def finish(**values: Any) -> bool:
        if result is not None:
            result.update(values)
        return bool(values.get("claimed", False))

    previous_busy_timeout = (
        _screen_cancel_previous_busy_timeout(conn)
        if deadline_monotonic is not None
        else None
    )
    if not _screen_cancel_configure_deadline(conn, deadline_monotonic):
        return finish(action="deadline_deferred")
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        _screen_cancel_restore_deadline(conn, previous_busy_timeout)
        return finish(action="deadline_deferred")
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_sqlite_lock_error(exc):
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return finish(action="sqlite_busy_deferred")
        raise
    try:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            conn.rollback()
            return finish(action="deadline_deferred")
        command = conn.execute(
            "SELECT state, venue_order_id, last_event_id FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if command is None:
            conn.rollback()
            return finish(action="missing")
        state = str(command[0] or "")
        command_order_id = str(command[1] or "")
        if state != "CANCEL_PENDING" or command_order_id != venue_order_id:
            conn.rollback()
            return finish(action="state_or_order_mismatch")
        events = conn.execute(
            "SELECT event_id, event_type, payload_json, sequence_no FROM venue_command_events "
            "WHERE command_id = ? ORDER BY sequence_no DESC",
            (command_id,),
        ).fetchall()
        if not events:
            conn.rollback()
            return finish(action="missing_events")
        latest = events[0]
        marker_payloads = [
            _screen_cancel_json(row[2])
            for row in events
            if str(row[1] or "") == "CANCEL_REQUESTED"
        ]
        exact_markers = [
            p for p in marker_payloads
            if _screen_cancel_marker_is_exact(p, command_id=command_id, venue_order_id=venue_order_id)
        ]
        if len(exact_markers) != 1 or len(marker_payloads) != 1:
            conn.rollback()
            return finish(action="legacy_or_malformed_marker")
        marker = exact_markers[0]
        if str(latest[1] or "") == "CANCEL_DISPATCH_STARTED":
            lease = _screen_cancel_json(latest[2])
            if not isinstance(lease, dict):
                conn.rollback()
                return finish(action="malformed_lease")
            if not _screen_cancel_iso_expired(lease.get("expires_at") or lease.get("lease_expires_at")):
                conn.rollback()
                return finish(action="active_lease")
            # Expired leases must be fenced by new point truth. A caller that
            # skips the witness cannot reclaim the generation.
            if fresh_witness is None:
                conn.rollback()
                return finish(action="expired_lease_without_witness")
            previous_generation = int(lease.get("generation") or 0)
            generation = max(int(generation), previous_generation + 1)
        elif str(latest[1] or "") != "CANCEL_REQUESTED":
            conn.rollback()
            return finish(action="unexpected_latest_event")
        if not isinstance(fresh_witness, Mapping):
            conn.rollback()
            return finish(action="fresh_witness_missing")
        if not _screen_cancel_witness_is_fresh(fresh_witness):
            conn.rollback()
            return finish(action="witness_stale")
        witness_status = str(fresh_witness.get("status") or "").upper()
        if witness_status not in _SCREEN_CANCEL_LIVE_STATUSES | _SCREEN_CANCEL_TERMINAL_STATUSES | {"ABSENT"}:
            conn.rollback()
            return finish(action="fresh_witness_invalid")
        if witness_status in _SCREEN_CANCEL_LIVE_STATUSES:
            try:
                min_size = float(marker.get("min_order_size")) if marker.get("min_order_size") is not None else 0.0
                matched = float(fresh_witness.get("matched_size") or 0.0)
            except (TypeError, ValueError):
                conn.rollback()
                return finish(action="fresh_witness_invalid")
            if min_size > 0 and matched > 0 and matched < min_size:
                conn.rollback()
                return finish(action="sub_minimum")
            occurred_at = datetime.now(UTC).isoformat()
            event_id = append_event(
                conn,
                command_id=command_id,
                event_type="CANCEL_DISPATCH_STARTED",
                occurred_at=occurred_at,
                payload={
                    "schema_version": SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION,
                    "obligation_id": marker["obligation_id"],
                    "obligation_kind": marker["obligation_kind"],
                    "owner": marker["owner"],
                    "owner_boot_id": owner.split(":", 1)[0],
                    "owner_pid": owner.split(":", 1)[1] if ":" in owner else "",
                    "generation": generation,
                    "attempt_id": attempt_id,
                    "expires_at": expires_at,
                    "venue_order_id": venue_order_id,
                    "fresh_point_order_witness": dict(fresh_witness),
                },
            )
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                conn.rollback()
                return finish(action="deadline_deferred")
            conn.commit()
            return finish(
                claimed=True,
                action="dispatch",
                event_id=event_id,
                generation=generation,
                attempt_id=attempt_id,
            )

        # Authenticated terminal/absent truth means the requested cancel has
        # already taken effect. Persist ACK without emitting a cancel side
        # effect; this is also safe after a worker crashed after STARTED.
        occurred_at = datetime.now(UTC).isoformat()
        event_id = append_event(
            conn,
            command_id=command_id,
            event_type="CANCEL_ACKED",
            occurred_at=occurred_at,
            payload={
                "schema_version": SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION,
                "obligation_id": marker["obligation_id"],
                "obligation_kind": marker["obligation_kind"],
                "owner": marker["owner"],
                "venue_order_id": venue_order_id,
                "reason": "fresh_point_order_terminal_or_absent",
                "fresh_point_order_witness": dict(fresh_witness),
            },
        )
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            conn.rollback()
            return finish(action="deadline_deferred")
        conn.commit()
        return finish(claimed=True, action="finalized", event_id=event_id)
    except BaseException:
        conn.rollback()
        raise
    finally:
        if deadline_monotonic is not None:
            _screen_cancel_restore_deadline(conn, previous_busy_timeout)


def finalize_screen_redecision_cancel_obligation(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    venue_order_id: str,
    attempt_id: str,
    expected_last_event_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    deadline_monotonic: float | None = None,
    result: dict[str, Any] | None = None,
) -> bool:
    """Append post-cancel ACK/unknown only for the current lease generation."""

    from src.state.venue_command_repo import append_event

    def finish(ok: bool, action: str) -> bool:
        if result is not None:
            result["action"] = action
        return ok

    previous_busy_timeout = (
        _screen_cancel_previous_busy_timeout(conn)
        if deadline_monotonic is not None
        else None
    )
    if not _screen_cancel_configure_deadline(conn, deadline_monotonic):
        return finish(False, "deadline_deferred")
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        _screen_cancel_restore_deadline(conn, previous_busy_timeout)
        return finish(False, "deadline_deferred")
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_sqlite_lock_error(exc):
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return finish(False, "sqlite_busy_deferred")
        raise
    try:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            conn.rollback()
            return finish(False, "deadline_deferred")
        row = conn.execute(
            "SELECT state, last_event_id FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if row is None or str(row[0] or "") != "CANCEL_PENDING" or str(row[1] or "") != expected_last_event_id:
            conn.rollback()
            return finish(False, "cas_stale")
        latest = conn.execute(
            "SELECT event_id, event_type, payload_json FROM venue_command_events "
            "WHERE command_id = ? ORDER BY sequence_no DESC LIMIT 1",
            (command_id,),
        ).fetchone()
        lease = _screen_cancel_json(latest[2]) if latest is not None else None
        if latest is None or str(latest[1] or "") != "CANCEL_DISPATCH_STARTED" or not isinstance(lease, dict):
            conn.rollback()
            return finish(False, "lease_mismatch")
        obligation_id = _screen_cancel_obligation_id(command_id, venue_order_id)
        if (
            lease.get("schema_version") != SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION
            or lease.get("obligation_id") != obligation_id
            or lease.get("obligation_kind") != SCREEN_CANCEL_OBLIGATION_KIND
            or lease.get("owner") != SCREEN_CANCEL_OBLIGATION_OWNER
            or lease.get("attempt_id") != attempt_id
            or lease.get("venue_order_id") != venue_order_id
        ):
            conn.rollback()
            return finish(False, "identity_mismatch")
        terminal_payload = dict(payload)
        if (
            terminal_payload.get("schema_version") != SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION
            or terminal_payload.get("obligation_id") != obligation_id
            or terminal_payload.get("obligation_kind") != SCREEN_CANCEL_OBLIGATION_KIND
            or terminal_payload.get("owner") != SCREEN_CANCEL_OBLIGATION_OWNER
            or terminal_payload.get("venue_order_id") != venue_order_id
        ):
            conn.rollback()
            return finish(False, "identity_mismatch")
        event_id = append_event(
            conn,
            command_id=command_id,
            event_type=event_type,
            occurred_at=datetime.now(UTC).isoformat(),
            payload=terminal_payload,
        )
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            conn.rollback()
            return finish(False, "deadline_deferred")
        conn.commit()
        return finish(bool(event_id), "finalized")
    except BaseException:
        conn.rollback()
        raise
    finally:
        if deadline_monotonic is not None:
            _screen_cancel_restore_deadline(conn, previous_busy_timeout)


def dispatch_screen_redecision_cancel_obligations(
    entries: list[dict[str, Any]],
    clob: Any,
    *,
    conn_factory: Callable[..., sqlite3.Connection],
    deadline_monotonic: float,
    owner: str,
    lease_seconds: float = 5.0,
    close_connections: bool = True,
) -> dict[str, int]:
    """Drain claimed screen obligations without crossing DB and HTTP I/O."""

    stats = {
        "scanned": len(entries),
        "cancelled": 0,
        "deferred": 0,
        "errors": 0,
        "journal_failed": 0,
    }
    for entry in entries:
        if time.monotonic() >= deadline_monotonic:
            stats["deferred"] += 1
            continue
        side_effect_deadline = (
            deadline_monotonic
            - SCREEN_CANCEL_POST_SIDE_EFFECT_JOURNAL_RESERVE_SECONDS
        )
        if time.monotonic() >= side_effect_deadline:
            stats["deferred"] += 1
            continue
        command_id = str(entry.get("command_id") or "")
        order_id = str(entry.get("venue_order_id") or "")
        if not command_id or not order_id:
            stats["errors"] += 1
            continue
        latest_payload = entry.get("latest_event_payload") or {}
        if entry.get("latest_event_type") == "CANCEL_DISPATCH_STARTED" and not _screen_cancel_iso_expired(
            latest_payload.get("expires_at") or latest_payload.get("lease_expires_at")
        ):
            stats["deferred"] += 1
            continue
        witness = _screen_cancel_point_witness(
            clob,
            order_id,
            deadline_monotonic=side_effect_deadline,
        )
        if witness is None:
            stats["deferred"] += 1
            continue
        claim_result: dict[str, Any] = {}
        attempt_id = str(uuid.uuid4())
        expires_at = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        if time.monotonic() >= deadline_monotonic:
            stats["deferred"] += 1
            continue
        claim_conn = conn_factory(deadline_monotonic=deadline_monotonic)
        try:
            claim_ok = claim_screen_redecision_cancel_obligation(
                claim_conn,
                command_id=command_id,
                venue_order_id=order_id,
                owner=owner,
                generation=int(entry.get("latest_event_payload", {}).get("generation") or 0) + 1,
                attempt_id=attempt_id,
                expires_at=expires_at,
                fresh_witness=witness,
                result=claim_result,
                deadline_monotonic=deadline_monotonic,
            )
        finally:
            _close_conn_if_needed(claim_conn, close=close_connections)
        if not claim_ok:
            if claim_result.get("action") in {
                "active_lease",
                "sub_minimum",
                "expired_lease_without_witness",
                "witness_stale",
                "deadline_deferred",
                "sqlite_busy_deferred",
            }:
                stats["deferred"] += 1
            else:
                stats["errors"] += 1
            continue
        if claim_result.get("action") == "finalized":
            stats["cancelled"] += 1
            continue
        if claim_result.get("action") != "dispatch":
            stats["deferred"] += 1
            continue
        if time.monotonic() >= deadline_monotonic:
            stats["deferred"] += 1
            continue
        try:
            raw = clob.cancel_order(
                order_id,
                deadline_monotonic=side_effect_deadline,
            )
            from src.execution.exit_safety import parse_cancel_response

            outcome = parse_cancel_response(raw)
            if outcome is not None and is_cancel_confirmed_status(outcome.status):
                event_type = "CANCEL_ACKED"
                payload = {
                    "schema_version": SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION,
                    "obligation_id": entry["obligation_id"],
                    "obligation_kind": entry["obligation_kind"],
                    "owner": entry["obligation_owner"],
                    "venue_order_id": order_id,
                    "cancel_outcome": outcome.raw_response,
                    "source": "screen_redecision_cancel_dispatch",
                }
                stats_key = "cancelled"
            else:
                event_type = "CANCEL_REPLACE_BLOCKED"
                payload = {
                    "schema_version": SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION,
                    "obligation_id": entry["obligation_id"],
                    "obligation_kind": entry["obligation_kind"],
                    "owner": entry["obligation_owner"],
                    "venue_order_id": order_id,
                    "reason": "post_cancel_unknown_possible_side_effect",
                    "requires_m5_reconcile": True,
                    "semantic_cancel_status": "CANCEL_UNKNOWN",
                    "cancel_outcome": getattr(outcome, "raw_response", raw),
                    "source": "screen_redecision_cancel_dispatch",
                }
                stats_key = "deferred"
        except TypeError:
            # A client that cannot accept the deadline contract was never
            # allowed to cross the HTTP boundary; keep STARTED debt for a
            # compatible recovery worker instead of fabricating unknown venue
            # side effect truth.
            stats["journal_failed"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 - side effect boundary is ambiguous
            event_type = "CANCEL_REPLACE_BLOCKED"
            payload = {
                "schema_version": SCREEN_CANCEL_OBLIGATION_SCHEMA_VERSION,
                "obligation_id": entry["obligation_id"],
                "obligation_kind": entry["obligation_kind"],
                "owner": entry["obligation_owner"],
                "venue_order_id": order_id,
                "reason": "post_cancel_unknown_possible_side_effect",
                "requires_m5_reconcile": True,
                "semantic_cancel_status": "CANCEL_UNKNOWN",
                "cancel_outcome": {"exception_type": type(exc).__name__, "exception_message": str(exc)},
                "source": "screen_redecision_cancel_dispatch",
            }
            stats_key = "deferred"
        # Reserve a short, bounded post-side-effect journal window. If this
        # write fails, STARTED/CANCEL_PENDING remains recovery-visible.
        reserve_deadline = min(
            deadline_monotonic,
            time.monotonic()
            + SCREEN_CANCEL_POST_SIDE_EFFECT_JOURNAL_RESERVE_SECONDS,
        )
        if time.monotonic() >= deadline_monotonic:
            stats["journal_failed"] += 1
            continue
        journal_conn = conn_factory(deadline_monotonic=deadline_monotonic)
        finalize_result: dict[str, Any] = {}
        try:
            finalized = finalize_screen_redecision_cancel_obligation(
                journal_conn,
                command_id=command_id,
                venue_order_id=order_id,
                attempt_id=attempt_id,
                expected_last_event_id=str(claim_result.get("event_id") or ""),
                event_type=event_type,
                payload=payload,
                deadline_monotonic=reserve_deadline,
                result=finalize_result,
            )
        except Exception:
            finalized = False
            logger.exception("screen cancel post-side-effect journal failed command=%s", command_id)
        finally:
            _close_conn_if_needed(journal_conn, close=close_connections)
        if not finalized:
            stats["journal_failed"] += 1
            continue
        if stats_key == "cancelled":
            stats["cancelled"] += 1
        else:
            stats["deferred"] += 1
    return stats


def run_persisted_cancels_for_expired_rests(
    expired: list[dict[str, Any]],
    clob: Any,
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    close_connections: bool = True,
    deadline_minutes: float | None = None,
    collect_cancelled: list[dict[str, Any]] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, int]:
    """Cancel each already-snapshotted candidate with durable command-journal truth
    around the venue side effect.

    ``deadline_minutes`` is accepted only for the log line's benefit (callers whose
    candidates were built by a deadline classifier pass it through); this function
    itself does no deadline reasoning — it is a pure "cancel what you were told to
    cancel, durably" executor.

    1. append CANCEL_REQUESTED and commit,
    2. close the connection before the HTTP cancel,
    3. append CANCEL_ACKED / CANCEL_REPLACE_BLOCKED and commit.

    A command whose pre-side-effect journal write fails is not sent to the venue.
    A successful venue cancel whose post-side-effect journal write fails is not
    harvested for redecision; command recovery must resolve the CANCEL_PENDING row.
    ``NOT_CANCELED`` is cancel-unknown, not terminal failure: the venue may still
    have a live order, or the cancel may have raced with a match/cancel. M5
    recovery owns the next proof read.
    """
    from src.execution.exit_safety import parse_cancel_response

    stats = {
        "scanned": len(expired),
        "cancelled": 0,
        "cancel_failed": 0,
        "cancel_journal_failed": 0,
    }
    for entry in expired:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            stats["cancel_failed"] += 1
            break
        command_id = str(entry.get("command_id") or "")
        order_id = str(entry.get("venue_order_id") or "")
        cancel_reason = str(entry.get("cancel_reason") or "").strip()
        cancel_action = str(entry.get("cancel_action") or "").strip()
        cancel_detail = entry.get("cancel_detail")
        min_order_size = _finite_nonnegative(entry.get("min_order_size"))
        if min_order_size is not None and min_order_size > 0.0:
            # SCOPE: this maker rest only. DRAIN: the next redecision cycle
            # retries after point/canonical truth recovers or the rest fills to
            # an executable size. RESET: current matched size is zero or at
            # least the venue minimum, so ordinary cancel/reprice resumes.
            matched_size, matched_source = _current_cancel_matched_size(
                entry,
                clob,
                conn_factory=conn_factory,
                close_connections=close_connections,
                deadline_monotonic=deadline_monotonic,
            )
            if matched_size is None:
                stats["cancel_failed"] += 1
                logger.warning(
                    "venue_cancel_journal: deferred cancel without current fill truth "
                    "command=%s order=%s reason=%s",
                    command_id,
                    order_id,
                    matched_source,
                )
                continue
            entry["matched_size"] = matched_size
            if 0.0 < matched_size < min_order_size:
                logger.warning(
                    "venue_cancel_journal: deferred cancel to preserve sub-min partial "
                    "command=%s order=%s matched=%.6f min_order=%.6f source=%s",
                    command_id,
                    order_id,
                    matched_size,
                    min_order_size,
                    matched_source,
                )
                continue
        now = datetime.now(UTC).isoformat()
        try:
            _append_cancel_journal_event(
                conn_factory,
                command_id=command_id,
                event_type="CANCEL_REQUESTED",
                occurred_at=now,
                payload={
                    "venue_order_id": order_id,
                    "source": "maker_rest_escalation",
                    "cancel_reason": cancel_reason,
                    "cancel_action": cancel_action,
                    "cancel_detail": cancel_detail,
                },
                close_connections=close_connections,
                deadline_monotonic=deadline_monotonic,
            )
        except _TerminalCommandNoop as exc:
            logger.info(
                "venue_cancel_journal: skipped terminal command before cancel "
                "command=%s order=%s state=%s",
                command_id,
                order_id,
                exc.state,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            stats["cancel_failed"] += 1
            logger.error(
                "venue_cancel_journal: pre-cancel journal failed command=%s order=%s: %r",
                command_id,
                order_id,
                exc,
            )
            continue

        try:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                stats["cancel_failed"] += 1
                continue
            raw = clob.cancel_order(order_id)
            outcome = parse_cancel_response(raw)
        except Exception as exc:  # noqa: BLE001 — possible side effect; record unknown
            outcome = None
            raw = {"exception_type": type(exc).__name__, "exception_message": str(exc)}

        if outcome is not None and is_cancel_confirmed_status(outcome.status):
            event_type = "CANCEL_ACKED"
            payload = {
                "venue_order_id": order_id,
                "cancel_outcome": outcome.raw_response,
                "source": "maker_rest_escalation",
                "cancel_reason": cancel_reason,
                "cancel_action": cancel_action,
                "cancel_detail": cancel_detail,
            }
        elif outcome is not None and outcome.status == "NOT_CANCELED":
            event_type = "CANCEL_REPLACE_BLOCKED"
            payload = {
                "venue_order_id": order_id,
                "reason": "post_cancel_unknown_possible_side_effect",
                "requires_m5_reconcile": True,
                "semantic_cancel_status": "CANCEL_UNKNOWN",
                "cancel_outcome": outcome.raw_response,
                "source": "maker_rest_escalation",
                "cancel_reason": cancel_reason,
                "cancel_action": cancel_action,
                "cancel_detail": cancel_detail,
            }
        else:
            event_type = "CANCEL_REPLACE_BLOCKED"
            payload = {
                "venue_order_id": order_id,
                "reason": "post_cancel_unknown_possible_side_effect",
                "requires_m5_reconcile": True,
                "semantic_cancel_status": "CANCEL_UNKNOWN",
                "cancel_outcome": raw,
                "source": "maker_rest_escalation",
                "cancel_reason": cancel_reason,
                "cancel_action": cancel_action,
                "cancel_detail": cancel_detail,
            }

        try:
            # Once the venue boundary started, ACK/unknown evidence must be
            # durable even if the recovery budget elapsed while it returned.
            _append_cancel_journal_event(
                conn_factory,
                command_id=command_id,
                event_type=event_type,
                occurred_at=datetime.now(UTC).isoformat(),
                payload=payload,
                close_connections=close_connections,
            )
        except _TerminalCommandNoop as exc:
            logger.info(
                "venue_cancel_journal: skipped post-cancel journal for terminal command "
                "command=%s order=%s event=%s state=%s",
                command_id,
                order_id,
                event_type,
                exc.state,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            stats["cancel_journal_failed"] += 1
            logger.error(
                "venue_cancel_journal: post-cancel journal failed command=%s order=%s "
                "event=%s: %r",
                command_id,
                order_id,
                event_type,
                exc,
            )
            continue

        if event_type == "CANCEL_ACKED":
            stats["cancelled"] += 1
            if deadline_monotonic is None or time.monotonic() < deadline_monotonic:
                _reconcile_terminal_no_fill_after_cancel_ack(
                conn_factory,
                command_id=command_id,
                order_id=order_id,
                close_connections=close_connections,
                )
            if collect_cancelled is not None:
                collect_cancelled.append(entry)
        else:
            stats["cancel_failed"] += 1

        if event_type == "CANCEL_ACKED" and cancel_reason:
            logger.info(
                "venue_cancel_journal: cancelled screened rest command=%s order=%s "
                "reason=%s action=%s detail=%s rested_since=%s fact_state=%s matched=%s",
                command_id,
                order_id,
                cancel_reason,
                entry.get("cancel_action"),
                entry.get("cancel_detail"),
                entry.get("created_at"),
                entry.get("fact_state"),
                entry.get("matched_size"),
            )
        elif event_type == "CANCEL_ACKED":
            if deadline_minutes is not None:
                logged_deadline_minutes = float(deadline_minutes)
            else:
                # No caller-supplied deadline: fall back to the live TTL owner's
                # operating value (src.state.order_state_predicates, the
                # successor to this module's own retired deadline read) rather
                # than a hardcoded stand-in, so this log line stays truthful for
                # on-call even when a caller (e.g. the invalid-entry-authority
                # lanes) never had a deadline to pass in the first place.
                from src.state.order_state_predicates import bootstrap_rest_deadline_minutes

                logged_deadline_minutes = bootstrap_rest_deadline_minutes()
            logger.info(
                "venue_cancel_journal: cancelled expired rest command=%s order=%s "
                "rested_since=%s fact_state=%s matched=%s (deadline=%.0fmin)",
                command_id,
                order_id,
                entry.get("created_at"),
                entry.get("fact_state"),
                entry.get("matched_size"),
                logged_deadline_minutes,
            )
    return stats


def _reconcile_terminal_no_fill_after_cancel_ack(
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    command_id: str,
    order_id: str,
    close_connections: bool,
) -> None:
    """Immediately consume zero-fill cancel truth after a confirmed cancel.

    The full INV-31 command-recovery sweep can be delayed by authenticated venue
    reads. A confirmed cancel already has enough durable local evidence for the
    DB-only terminal-no-fill reducers to clear a zero-exposure ``pending_entry``
    projection, so run those narrow reducers in the cancel path.
    """

    conn = conn_factory()
    try:
        required_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {
            "position_current",
            "venue_commands",
            "venue_command_events",
            "venue_order_facts",
        }.issubset(required_tables):
            return
        from src.execution.command_recovery import (
            reconcile_cancel_ack_terminal_no_fill_facts,
            reconcile_cancel_ack_terminal_partial_facts,
            reconcile_terminal_entry_exposure_obligations,
            reconcile_terminal_order_facts,
        )

        cancel_summary = reconcile_cancel_ack_terminal_no_fill_facts(conn)
        partial_summary = reconcile_cancel_ack_terminal_partial_facts(conn)
        conn.commit()
        terminal_summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
        for attempt in range(1, 4):
            terminal_summary = reconcile_terminal_order_facts(
                conn,
                command_ids=frozenset({command_id}),
            )
            if not int(terminal_summary.get("errors", 0) or 0):
                conn.commit()
                break
            conn.rollback()
            if attempt < 3:
                time.sleep(0.25 * attempt)
        obligation_summary = reconcile_terminal_entry_exposure_obligations(conn)
        conn.commit()
        advanced = int(cancel_summary.get("advanced", 0) or 0) + int(
            partial_summary.get("advanced", 0) or 0
        ) + int(
            terminal_summary.get("advanced", 0) or 0
        ) + int(
            obligation_summary.get("advanced", 0) or 0
        )
        if advanced:
            logger.info(
                "venue_cancel_journal: terminal no-fill reducers advanced command=%s "
                "order=%s cancel_ack=%s cancel_partial=%s terminal=%s obligation=%s",
                command_id,
                order_id,
                cancel_summary,
                partial_summary,
                terminal_summary,
                obligation_summary,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "venue_cancel_journal: terminal no-fill reducer deferred command=%s "
            "order=%s: %r",
            command_id,
            order_id,
            exc,
        )
    finally:
        _close_conn_if_needed(conn, close=close_connections)
