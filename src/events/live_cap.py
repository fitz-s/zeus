"""EDLI live-order reservation ledger.

2026-06-08 operator directive: the ``tiny_live`` mechanism — a $5 special-case
per-order notional cap plus per-day / per-window order-count caps — is DELETED.
Order size is governed SOLELY by the structural multi-layer fractional-Kelly
sizing in ``src/events/money_path_adapters.py::evaluate_kelly``. This ledger no
longer rejects or clamps based on any ``max_notional_usd`` or order-count limit.

What REMAINS load-bearing and is preserved:
  - Exactly-once reservation keyed by ``(event_id, cap_scope)`` via the
    ``edli_live_cap_usage`` UNIQUE index + the existing-row return below. A
    re-reserve of the same event returns the same row; it never creates a second
    reservation, so a live order can never be double-submitted.
  - Reserved-notional drift detection while a row is still RESERVED or CONSUMED.
    A RELEASED row is pre-venue/no-side-effect truth and may be re-opened by a
    fresh redecision of the same event.
  - The LIVE_CAP certificate record that chains the execution-command cert.

The ledger records the (uncapped) Kelly notional; it caps NOTHING.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.decision_kernel.canonicalization import stable_hash
from src.state.schema.edli_live_cap_usage_schema import ensure_table

# Inert provenance constants written into the durable row so the legacy schema's
# CHECK (max_orders_per_day > 0) / (max_notional_usd >= 0) constraints are still
# satisfied. They are NOT caps — nothing reads them as a limit anymore.
_RECORDED_ORDER_COUNT = 1
_RECORDED_MAX_ORDERS_PER_DAY = 1


@dataclass(frozen=True)
class LiveCapReservation:
    usage_id: str
    event_id: str
    decision_time: datetime
    cap_scope: str
    reserved_notional_usd: float
    reservation_status: str
    final_intent_id: str | None = None
    execution_command_id: str | None = None

    def certificate_payload(self) -> dict:
        # ``max_notional_usd`` mirrors the reserved Kelly notional purely as a
        # durable record (and to keep the column non-null); it is NOT a cap and
        # nothing compares an order against it. ``order_count`` /
        # ``max_orders_per_day`` are inert provenance constants.
        return {
            "usage_id": self.usage_id,
            "event_id": self.event_id,
            "decision_time": _dt(self.decision_time),
            "cap_scope": self.cap_scope,
            "max_notional_usd": self.reserved_notional_usd,
            "max_orders_per_day": _RECORDED_MAX_ORDERS_PER_DAY,
            "reserved_notional_usd": self.reserved_notional_usd,
            "order_count": _RECORDED_ORDER_COUNT,
            "reservation_status": self.reservation_status,
            "final_intent_id": self.final_intent_id,
            "execution_command_id": self.execution_command_id,
        }


class LiveCapError(ValueError):
    """Raised when EDLI live-cap reservation law is violated."""


class LiveCapLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        ensure_table(conn)

    def reserve(
        self,
        *,
        event_id: str,
        decision_time: datetime,
        cap_scope: str,
        requested_notional_usd: float,
        final_intent_id: str | None = None,
        execution_command_id: str | None = None,
    ) -> LiveCapReservation:
        """Record an exactly-once reservation of the Kelly-sized notional.

        This caps NOTHING. It records the requested (Kelly) notional, dedupes by
        ``(event_id, cap_scope)`` while a reservation is active/consumed, and
        raises on reserved-notional drift unless the prior row was RELEASED.
        RELEASED means no live side effect owns the reservation anymore, so a
        fresh redecision can re-open the same usage id with fresh sizing. The only
        rejection is the basic sanity floor: a non-positive notional.
        """
        requested = float(requested_notional_usd)
        if requested <= 0:
            raise LiveCapError("requested_notional_usd must be positive")
        usage_id = self._usage_id(event_id, cap_scope)
        created_at = _dt(datetime.now(timezone.utc))
        decision_text = _dt(decision_time)
        existing = self.conn.execute(
            """
            SELECT *
            FROM edli_live_cap_usage
            WHERE event_id = ? AND cap_scope = ?
            """,
            (event_id, cap_scope),
        ).fetchone()
        if existing is not None:
            # Exactly-once while live: a re-reserve of the same active event
            # returns the SAME row. Drift guard: a changed reserved notional /
            # final_intent_id / execution_command_id for an active or consumed row
            # is a defect, not a silent overwrite.
            reservation = _reservation_from_row(existing)
            if reservation.reservation_status.upper() == "RELEASED":
                self.conn.execute(
                    """
                    UPDATE edli_live_cap_usage
                    SET decision_time = ?,
                        max_notional_usd = ?,
                        reserved_notional_usd = ?,
                        reservation_status = 'RESERVED',
                        final_intent_id = ?,
                        execution_command_id = ?,
                        created_at = ?
                    WHERE usage_id = ?
                    """,
                    (
                        decision_text,
                        requested,
                        requested,
                        final_intent_id,
                        execution_command_id,
                        created_at,
                        reservation.usage_id,
                    ),
                )
                return LiveCapReservation(
                    usage_id=reservation.usage_id,
                    event_id=event_id,
                    decision_time=decision_time,
                    cap_scope=cap_scope,
                    reserved_notional_usd=requested,
                    reservation_status="RESERVED",
                    final_intent_id=final_intent_id,
                    execution_command_id=execution_command_id,
                )
            if (
                reservation.reserved_notional_usd != requested
                or (final_intent_id is not None and reservation.final_intent_id != final_intent_id)
                or (execution_command_id is not None and reservation.execution_command_id != execution_command_id)
            ):
                raise LiveCapError("live cap reservation drift for event/cap_scope")
            return reservation
        self.conn.execute(
            """
            INSERT INTO edli_live_cap_usage (
                usage_id, event_id, decision_time, cap_scope,
                max_notional_usd, max_orders_per_day, reserved_notional_usd,
                order_count, reservation_status, final_intent_id,
                execution_command_id, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?, ?, 1)
            """,
            (
                usage_id,
                event_id,
                decision_text,
                cap_scope,
                # max_notional_usd mirrors the reserved notional as a durable
                # record only (keeps the legacy column non-null); not a cap.
                requested,
                _RECORDED_MAX_ORDERS_PER_DAY,
                requested,
                _RECORDED_ORDER_COUNT,
                final_intent_id,
                execution_command_id,
                created_at,
            ),
        )
        return LiveCapReservation(
            usage_id=usage_id,
            event_id=event_id,
            decision_time=decision_time,
            cap_scope=cap_scope,
            reserved_notional_usd=requested,
            reservation_status="RESERVED",
            final_intent_id=final_intent_id,
            execution_command_id=execution_command_id,
        )

    def release(self, usage_id: str, reason: str | None = None) -> None:
        del reason
        row = self.conn.execute(
            "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if row is None:
            raise LiveCapError("live cap reservation not found")
        if row[0] == "CONSUMED":
            raise LiveCapError("consumed live cap reservation cannot be released")
        self.conn.execute(
            """
            UPDATE edli_live_cap_usage
            SET reservation_status = 'RELEASED'
            WHERE usage_id = ?
            """,
            (usage_id,),
        )
        self.conn.execute("DELETE FROM edli_live_cap_day_slots WHERE usage_id = ?", (usage_id,))
        self.conn.execute("DELETE FROM edli_live_cap_rate_window WHERE usage_id = ?", (usage_id,))

    def consume(self, usage_id: str, *, final_intent_id: str, execution_command_id: str) -> None:
        if not final_intent_id or not execution_command_id:
            raise LiveCapError("consume requires final_intent_id and execution_command_id")
        row = self.conn.execute(
            "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if row is None:
            raise LiveCapError("live cap reservation not found")
        if row[0] != "RESERVED":
            raise LiveCapError("only RESERVED live cap reservations can be consumed")
        self.conn.execute(
            """
            UPDATE edli_live_cap_usage
            SET reservation_status = 'CONSUMED',
                final_intent_id = ?,
                execution_command_id = ?
            WHERE usage_id = ?
            """,
            (final_intent_id, execution_command_id, usage_id),
        )

    def get(self, usage_id: str) -> LiveCapReservation:
        row = self.conn.execute(
            "SELECT * FROM edli_live_cap_usage WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if row is None:
            raise LiveCapError("live cap reservation not found")
        return _reservation_from_row(row)

    @staticmethod
    def _usage_id(event_id: str, cap_scope: str) -> str:
        return "edli_live_cap:" + stable_hash({"event_id": event_id, "cap_scope": cap_scope})[:32]


def _reservation_from_row(row) -> LiveCapReservation:
    getter = row.__getitem__
    return LiveCapReservation(
        usage_id=str(getter("usage_id") if isinstance(row, sqlite3.Row) else row[0]),
        event_id=str(getter("event_id") if isinstance(row, sqlite3.Row) else row[1]),
        decision_time=datetime.fromisoformat(str(getter("decision_time") if isinstance(row, sqlite3.Row) else row[2])),
        cap_scope=str(getter("cap_scope") if isinstance(row, sqlite3.Row) else row[3]),
        reserved_notional_usd=float(getter("reserved_notional_usd") if isinstance(row, sqlite3.Row) else row[6]),
        reservation_status=str(getter("reservation_status") if isinstance(row, sqlite3.Row) else row[8]),
        final_intent_id=getter("final_intent_id") if isinstance(row, sqlite3.Row) else row[9],
        execution_command_id=getter("execution_command_id") if isinstance(row, sqlite3.Row) else row[10],
    )


def _dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
