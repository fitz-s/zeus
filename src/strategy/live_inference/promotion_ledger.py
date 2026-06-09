"""EDLI promotion and live-cap ledger helpers.

Promotion decisions are deliberately read-model evidence in EDLI v1. No live
strategy is promoted from no-trade regret data in this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import sqlite3

from src.events.live_cap import LiveCapError, LiveCapLedger

UTC = timezone.utc


def no_regret_training_enabled() -> bool:
    return False


@dataclass(frozen=True)
class LiveCapDecision:
    allowed: bool
    reason: str
    existing_orders: int
    existing_notional_usd: float


class EdliLiveCapLedger:
    """Compatibility facade over the canonical EDLI live-cap ledger.

    EDLI live-cap truth is owned by ``src.events.live_cap.LiveCapLedger`` and
    the ``edli_live_cap_usage`` schema. This wrapper remains only for older
    read-model callers; it must not define its own table grammar.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._ledger = LiveCapLedger(conn)
        self._conn = conn

    def check_day0(
        self,
        *,
        event_id: str,
        decision_time: datetime,
        max_orders_per_day: int,
        max_notional_usd: float,
    ) -> LiveCapDecision:
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS orders, COALESCE(SUM(reserved_notional_usd), 0.0) AS reserved
            FROM edli_live_cap_usage
            WHERE cap_scope = 'day0_hard_fact'
              AND substr(decision_time, 1, 10) = ?
              AND reservation_status IN ('RESERVED','CONSUMED')
            """,
            (decision_time.astimezone(UTC).date().isoformat(),),
        ).fetchone()
        existing_orders = int(row[0] or 0)
        existing_reserved = float(row[1] or 0.0)
        if existing_orders >= max_orders_per_day:
            return LiveCapDecision(False, "DAY0_TINY_ORDER_CAP_BLOCKED", existing_orders, existing_reserved)
        if existing_reserved + max_notional_usd > max_notional_usd:
            return LiveCapDecision(False, "DAY0_TINY_NOTIONAL_CAP_BLOCKED", existing_orders, existing_reserved)
        if self._already_reserved(event_id=event_id):
            return LiveCapDecision(False, "DAY0_TINY_CAP_ALREADY_USED_BY_EVENT", existing_orders, existing_reserved)
        return LiveCapDecision(True, "ALLOWED", existing_orders, existing_reserved)

    def reserve_day0(
        self,
        *,
        event_id: str,
        decision_time: datetime,
        notional_usd: float,
    ) -> None:
        try:
            # 2026-06-08: the underlying LiveCapLedger.reserve no longer takes any
            # notional/order-count cap args (tiny_live caps deleted). This facade's
            # own day0 cap accounting lives in check_day0; the reserve here only
            # records the exactly-once reservation of the requested notional.
            self._ledger.reserve(
                event_id=event_id,
                decision_time=decision_time,
                cap_scope="day0_hard_fact",
                requested_notional_usd=float(notional_usd),
            )
        except LiveCapError:
            return

    def _already_reserved(self, *, event_id: str) -> bool:
        return (
            self._conn.execute(
                """
                SELECT 1
                FROM edli_live_cap_usage
                WHERE cap_scope = 'day0_hard_fact'
                  AND event_id = ?
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()
            is not None
        )
