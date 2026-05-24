"""EDLI promotion and live-cap ledger helpers.

Promotion decisions are deliberately read-model evidence in EDLI v1. No live
strategy is promoted from no-trade regret data in this package.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.events.idempotency import stable_event_id

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
    """Durable per-day live-cap usage for EDLI pilot trades."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def check_day0(
        self,
        *,
        event_id: str,
        decision_time: datetime,
        max_orders_per_day: int,
        max_notional_usd: float,
    ) -> LiveCapDecision:
        usage_date = decision_time.astimezone(UTC).date().isoformat()
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS orders, COALESCE(SUM(notional_usd), 0.0) AS notional
            FROM edli_live_cap_usage
            WHERE cap_name = 'day0_hard_fact'
              AND usage_date = ?
            """,
            (usage_date,),
        ).fetchone()
        existing_orders = int(row[0] or 0)
        existing_notional = float(row[1] or 0.0)
        if existing_orders >= max_orders_per_day:
            return LiveCapDecision(False, "DAY0_TINY_ORDER_CAP_BLOCKED", existing_orders, existing_notional)
        if existing_notional + max_notional_usd > max_notional_usd:
            return LiveCapDecision(False, "DAY0_TINY_NOTIONAL_CAP_BLOCKED", existing_orders, existing_notional)
        if self._already_reserved(event_id=event_id, usage_date=usage_date):
            return LiveCapDecision(False, "DAY0_TINY_CAP_ALREADY_USED_BY_EVENT", existing_orders, existing_notional)
        return LiveCapDecision(True, "ALLOWED", existing_orders, existing_notional)

    def reserve_day0(
        self,
        *,
        event_id: str,
        decision_time: datetime,
        notional_usd: float,
    ) -> None:
        usage_date = decision_time.astimezone(UTC).date().isoformat()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO edli_live_cap_usage (
                usage_id, cap_name, usage_date, event_id,
                notional_usd, created_at, schema_version
            ) VALUES (?, 'day0_hard_fact', ?, ?, ?, ?, 1)
            """,
            (
                stable_event_id("day0_hard_fact", usage_date, event_id),
                usage_date,
                event_id,
                float(notional_usd),
                decision_time.astimezone(UTC).isoformat(),
            ),
        )

    def _already_reserved(self, *, event_id: str, usage_date: str) -> bool:
        return (
            self._conn.execute(
                """
                SELECT 1
                FROM edli_live_cap_usage
                WHERE cap_name = 'day0_hard_fact'
                  AND usage_date = ?
                  AND event_id = ?
                LIMIT 1
                """,
                (usage_date, event_id),
            ).fetchone()
            is not None
        )
