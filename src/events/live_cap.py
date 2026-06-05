"""EDLI live canary cap reservation ledger."""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.decision_kernel.canonicalization import stable_hash
from src.state.schema.edli_live_cap_usage_schema import ensure_table

# BUG #99 antibody: conservative canary default for the order-emission RATE
# limit. This is INDEPENDENT of the notional cap and of max_orders_per_day. When
# a caller omits max_orders_per_window we fail closed to a single order per
# window so that raising the notional cap can never silently uncap order
# frequency. The operator sets the live value via config (the daemon passes it
# through); the default here only governs absence.
DEFAULT_MAX_ORDERS_PER_WINDOW = 1

# PR-2 (C) N3 antibody: a HARD per-order notional ceiling that holds even when
# tiny_live_notional_cap_enabled is false. #380 removed BOTH the notional cap and
# the daily cap in a single commit, leaving fractional Kelly as the sole notional
# bound — one bad edit away from an unbounded order. This ceiling is a SEPARATE
# rail: the soft cap flag may TUNE max_notional_usd, but it can NEVER remove this
# ceiling. reserve() clamps every request down to it UNCONDITIONALLY (cap on or
# off), so a single-commit dual-cap removal still cannot uncap notional. It is a
# runaway backstop (a Kelly bug emitting thousands), set well above any legitimate
# canary/early-live order — not a routine sizing constraint.
HARD_NOTIONAL_CEILING_USD = 250.0


def cap_explicitly_disabled(value: object) -> bool:
    """Return True ONLY when ``value`` is the explicit-disable sentinel.

    2026-06-03 operator directive: the artificial notional + per-day caps may be
    removed, but unbounded must be DELIBERATE. The disable signal is the literal
    JSON boolean ``false`` (Python ``False``) and NOTHING else.

    FAIL-SAFE INVARIANT: a missing key (``None``), a malformed string
    (``"false"``, ``"0"``, ``"no"``), a number, an empty container, or any
    truthy value is NOT the sentinel and therefore does NOT disable the cap. The
    caller treats a non-disable result as "cap stays enabled" (fail closed). A
    config typo can never silently uncap — it can only leave the cap ON.

    Note we deliberately do NOT coerce strings here (unlike a permissive
    ``_coerce_bool``): a config that wrote the string ``"false"`` instead of the
    JSON literal ``false`` is treated as malformed and the cap stays enabled.
    Disabling a live risk-sizing limit must be unambiguous.
    """
    return value is False


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
        max_orders_per_window: int = DEFAULT_MAX_ORDERS_PER_WINDOW,
        notional_cap_enabled: bool = True,
        daily_order_cap_enabled: bool = True,
        final_intent_id: str | None = None,
        execution_command_id: str | None = None,
    ) -> LiveCapReservation:
        # 2026-06-03 operator directive: the artificial notional + per-day caps
        # may be EXPLICITLY disabled so fractional-Kelly sizing is the sole
        # notional constraint (alongside collateral + the flood-guard rate
        # window, both of which remain active). FAIL-SAFE: these flags default
        # to True; a caller that omits them (e.g. a malformed config that lost
        # the disable sentinel) gets the tight cap, never unbounded.
        if requested_notional_usd <= 0:
            raise LiveCapError("requested_notional_usd must be positive")
        # 2026-06-05 operator directive ("no caps, no trade-count limits"): the $250 hard ceiling is
        # a HARDCODED arbitrary number, not the real safety gate. The real bounds — fractional Kelly
        # (the forward-settlement risk allowance, iron rule 5) and the COLLATERAL ledger (you cannot
        # reserve more PUSD than the wallet holds) — are preserved and remain the sole notional
        # constraint. So the ceiling is now FLAG-GATED: it clamps ONLY when the notional cap is
        # enabled. Disabled (the operator's config) ⇒ no arbitrary clamp; Kelly + collateral bound
        # the size by your ACTUAL money rather than a fixed sentinel. Clamp (not reject) when on.
        if notional_cap_enabled and requested_notional_usd > HARD_NOTIONAL_CEILING_USD:
            requested_notional_usd = float(HARD_NOTIONAL_CEILING_USD)
        if notional_cap_enabled and requested_notional_usd > max_notional_usd:
            raise LiveCapError("requested_notional_usd exceeds max_notional_usd")
        # When the notional cap is disabled the persisted row must stay
        # self-consistent (reserved <= max, schema CHECK max >= 0): record the
        # actual requested notional as the row's max so it documents the
        # uncapped size rather than a stale ceiling.
        recorded_max_notional_usd = (
            float(max_notional_usd)
            if notional_cap_enabled
            else float(requested_notional_usd)
        )
        if max_orders_per_day <= 0:
            raise LiveCapError("max_orders_per_day must be positive")
        if max_orders_per_window <= 0:
            raise LiveCapError("max_orders_per_window must be positive")
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
            reservation = _reservation_from_row(existing)
            if (
                reservation.max_notional_usd != recorded_max_notional_usd
                or reservation.max_orders_per_day != int(max_orders_per_day)
                or reservation.reserved_notional_usd != float(requested_notional_usd)
                or (final_intent_id is not None and reservation.final_intent_id != final_intent_id)
                or (execution_command_id is not None and reservation.execution_command_id != execution_command_id)
            ):
                raise LiveCapError("live cap reservation drift for event/cap_scope")
            return reservation
        cap_date = decision_text[:10]
        # Per-day cap: when EXPLICITLY disabled the day-slot pool is bypassed so
        # an unbounded number of orders may be placed per day (Kelly + collateral
        # + flood-guard remain the only constraints). The day-slot table's unique
        # (event_id, cap_scope) index still provides per-event idempotency — that
        # is not a cap, so it is preserved. When enabled (the default) the bounded
        # slot pool caps per-day exactly as before.
        if daily_order_cap_enabled:
            slot = self._reserve_day_slot(
                usage_id=usage_id,
                event_id=event_id,
                cap_scope=cap_scope,
                cap_date=cap_date,
                max_orders_per_day=max_orders_per_day,
                created_at=created_at,
            )
        else:
            slot = self._reserve_uncapped_day_slot(
                usage_id=usage_id,
                event_id=event_id,
                cap_scope=cap_scope,
                cap_date=cap_date,
                created_at=created_at,
            )
        # 2026-06-05 operator directive ("no trade-count limit"): the #99 flood-guard rate window is
        # a per-day order-COUNT cap (window_key = the calendar date, max_orders_per_window) — with
        # max_orders_per_window=1 it is effectively a hidden 1-order/day cap even when the explicit
        # daily cap is "disabled". The operator now forbids ANY trade-count limit, overriding the
        # prior (2026-06-03) directive that kept the flood window active. So the window slot is
        # reserved ONLY when the daily order cap is ENABLED; disabled ⇒ no order-count cap at all
        # (Kelly + collateral + the per-event idempotency unique-index remain the only constraints).
        if daily_order_cap_enabled:
            try:
                self._reserve_window_slot(
                    usage_id=usage_id,
                    event_id=event_id,
                    cap_scope=cap_scope,
                    window_key=cap_date,
                    max_orders_per_window=max_orders_per_window,
                    created_at=created_at,
                )
            except Exception:
                with contextlib.suppress(Exception):
                    self.conn.execute("DELETE FROM edli_live_cap_day_slots WHERE usage_id = ?", (usage_id,))
                raise
        try:
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
                    recorded_max_notional_usd,
                    int(max_orders_per_day),
                    float(requested_notional_usd),
                    int(slot),
                    final_intent_id,
                    execution_command_id,
                    created_at,
                ),
            )
        except Exception:
            with contextlib.suppress(Exception):
                self.conn.execute("DELETE FROM edli_live_cap_day_slots WHERE usage_id = ?", (usage_id,))
            with contextlib.suppress(Exception):
                self.conn.execute("DELETE FROM edli_live_cap_rate_window WHERE usage_id = ?", (usage_id,))
            raise
        return self.get(usage_id)

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

    def _reserve_day_slot(
        self,
        *,
        usage_id: str,
        event_id: str,
        cap_scope: str,
        cap_date: str,
        max_orders_per_day: int,
        created_at: str,
    ) -> int:
        existing = self.conn.execute(
            """
            SELECT slot, usage_id
            FROM edli_live_cap_day_slots
            WHERE event_id = ? AND cap_scope = ?
            """,
            (event_id, cap_scope),
        ).fetchone()
        if existing is not None:
            slot = int(existing["slot"] if isinstance(existing, sqlite3.Row) else existing[0])
            existing_usage_id = str(existing["usage_id"] if isinstance(existing, sqlite3.Row) else existing[1])
            if existing_usage_id != usage_id:
                raise LiveCapError("live cap day slot drift for event/cap_scope")
            return slot
        for slot in range(1, int(max_orders_per_day) + 1):
            try:
                self.conn.execute(
                    """
                    INSERT INTO edli_live_cap_day_slots (
                        cap_scope, cap_date, slot, usage_id, event_id, created_at, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (cap_scope, cap_date, slot, usage_id, event_id, created_at),
                )
                return slot
            except sqlite3.IntegrityError:
                continue
        raise LiveCapError("live cap max_orders_per_day exhausted")

    def _reserve_uncapped_day_slot(
        self,
        *,
        usage_id: str,
        event_id: str,
        cap_scope: str,
        cap_date: str,
        created_at: str,
    ) -> int:
        """Reserve a day slot with NO per-day ceiling (2026-06-03 directive).

        Used only when the per-day cap is EXPLICITLY disabled. Unlike
        ``_reserve_day_slot`` there is no ``max_orders_per_day`` bound: a fresh
        slot is allocated by walking past collisions until a free integer is
        found, so the pool grows without limit. The unique (event_id, cap_scope)
        index is still honoured for per-event idempotency (NOT a cap), and the
        flood-guard rate window (reserved separately by the caller) remains the
        active order-frequency bound.
        """
        existing = self.conn.execute(
            """
            SELECT slot, usage_id
            FROM edli_live_cap_day_slots
            WHERE event_id = ? AND cap_scope = ?
            """,
            (event_id, cap_scope),
        ).fetchone()
        if existing is not None:
            slot = int(existing["slot"] if isinstance(existing, sqlite3.Row) else existing[0])
            existing_usage_id = str(existing["usage_id"] if isinstance(existing, sqlite3.Row) else existing[1])
            if existing_usage_id != usage_id:
                raise LiveCapError("live cap day slot drift for event/cap_scope")
            return slot
        row = self.conn.execute(
            "SELECT COALESCE(MAX(slot), 0) FROM edli_live_cap_day_slots WHERE cap_scope = ? AND cap_date = ?",
            (cap_scope, cap_date),
        ).fetchone()
        slot = int(row[0]) + 1
        while True:
            try:
                self.conn.execute(
                    """
                    INSERT INTO edli_live_cap_day_slots (
                        cap_scope, cap_date, slot, usage_id, event_id, created_at, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (cap_scope, cap_date, slot, usage_id, event_id, created_at),
                )
                return slot
            except sqlite3.IntegrityError:
                slot += 1

    def _reserve_window_slot(
        self,
        *,
        usage_id: str,
        event_id: str,
        cap_scope: str,
        window_key: str,
        max_orders_per_window: int,
        created_at: str,
    ) -> int:
        existing = self.conn.execute(
            """
            SELECT slot, usage_id
            FROM edli_live_cap_rate_window
            WHERE event_id = ? AND cap_scope = ? AND window_key = ?
            """,
            (event_id, cap_scope, window_key),
        ).fetchone()
        if existing is not None:
            slot = int(existing["slot"] if isinstance(existing, sqlite3.Row) else existing[0])
            existing_usage_id = str(existing["usage_id"] if isinstance(existing, sqlite3.Row) else existing[1])
            if existing_usage_id != usage_id:
                raise LiveCapError("live cap rate window slot drift for event/cap_scope")
            return slot
        for slot in range(1, int(max_orders_per_window) + 1):
            try:
                self.conn.execute(
                    """
                    INSERT INTO edli_live_cap_rate_window (
                        cap_scope, window_key, slot, usage_id, event_id, created_at, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (cap_scope, window_key, slot, usage_id, event_id, created_at),
                )
                return slot
            except sqlite3.IntegrityError:
                continue
        raise LiveCapError("live cap order-emission rate limit exhausted (max_orders_per_window)")


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
