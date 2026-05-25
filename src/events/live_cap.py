"""EDLI live canary cap reservation ledger."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.decision_kernel.canonicalization import stable_hash
from src.state.schema.edli_live_cap_usage_schema import ensure_table


@dataclass(frozen=True)
class LiveCapReservation:
    usage_id: str
    event_id: str
    decision_time: datetime
    cap_scope: str
    max_notional_usd: float
    max_orders_per_day: int
    reserved_notional_usd: float
    order_count: int
    reservation_status: str
    final_intent_id: str | None = None
    execution_command_id: str | None = None

    def certificate_payload(self) -> dict:
        return {
            "usage_id": self.usage_id,
            "event_id": self.event_id,
            "decision_time": _dt(self.decision_time),
            "cap_scope": self.cap_scope,
            "max_notional_usd": self.max_notional_usd,
            "max_orders_per_day": self.max_orders_per_day,
            "reserved_notional_usd": self.reserved_notional_usd,
            "order_count": self.order_count,
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
        max_notional_usd: float,
        max_orders_per_day: int,
        final_intent_id: str | None = None,
        execution_command_id: str | None = None,
    ) -> LiveCapReservation:
        if requested_notional_usd <= 0:
            raise LiveCapError("requested_notional_usd must be positive")
        if requested_notional_usd > max_notional_usd:
            raise LiveCapError("requested_notional_usd exceeds max_notional_usd")
        if max_orders_per_day <= 0:
            raise LiveCapError("max_orders_per_day must be positive")
        usage_id = self._usage_id(event_id, cap_scope)
        created_at = _dt(datetime.now(timezone.utc))
        decision_text = _dt(decision_time)
        with self.conn:
            existing = self.conn.execute(
                """
                SELECT *
                FROM edli_live_cap_usage
                WHERE event_id = ? AND cap_scope = ?
                """,
                (event_id, cap_scope),
            ).fetchone()
            if existing is not None:
                reservation = _reservation_from_row(existing)
                if (
                    reservation.max_notional_usd != float(max_notional_usd)
                    or reservation.max_orders_per_day != int(max_orders_per_day)
                    or reservation.reserved_notional_usd != float(requested_notional_usd)
                    or (final_intent_id is not None and reservation.final_intent_id != final_intent_id)
                    or (execution_command_id is not None and reservation.execution_command_id != execution_command_id)
                ):
                    raise LiveCapError("live cap reservation drift for event/cap_scope")
                return reservation
            used = self.conn.execute(
                """
                SELECT COUNT(*)
                FROM edli_live_cap_usage
                WHERE cap_scope = ?
                  AND substr(decision_time, 1, 10) = substr(?, 1, 10)
                  AND reservation_status IN ('RESERVED','CONSUMED')
                """,
                (cap_scope, decision_text),
            ).fetchone()[0]
            if int(used) >= max_orders_per_day:
                raise LiveCapError("live cap max_orders_per_day exhausted")
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
                    float(max_notional_usd),
                    int(max_orders_per_day),
                    float(requested_notional_usd),
                    int(used) + 1,
                    final_intent_id,
                    execution_command_id,
                    created_at,
                ),
            )
        return self.get(usage_id)

    def release(self, usage_id: str, reason: str | None = None) -> None:
        del reason
        with self.conn:
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

    def consume(self, usage_id: str, *, final_intent_id: str, execution_command_id: str) -> None:
        if not final_intent_id or not execution_command_id:
            raise LiveCapError("consume requires final_intent_id and execution_command_id")
        with self.conn:
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
        max_notional_usd=float(getter("max_notional_usd") if isinstance(row, sqlite3.Row) else row[4]),
        max_orders_per_day=int(getter("max_orders_per_day") if isinstance(row, sqlite3.Row) else row[5]),
        reserved_notional_usd=float(getter("reserved_notional_usd") if isinstance(row, sqlite3.Row) else row[6]),
        order_count=int(getter("order_count") if isinstance(row, sqlite3.Row) else row[7]),
        reservation_status=str(getter("reservation_status") if isinstance(row, sqlite3.Row) else row[8]),
        final_intent_id=getter("final_intent_id") if isinstance(row, sqlite3.Row) else row[9],
        execution_command_id=getter("execution_command_id") if isinstance(row, sqlite3.Row) else row[10],
    )


def _dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
