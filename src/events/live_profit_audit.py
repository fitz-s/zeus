"""EDLI live profit audit projection helpers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any

from src.state.schema.edli_live_profit_audit_schema import ensure_table


_AUDIT_FIELDS = (
    "event_id",
    "aggregate_id",
    "final_intent_id",
    "execution_command_id",
    "condition_id",
    "token_id",
    "direction",
    "side",
    "q_live",
    "q_lcb_5pct",
    "expected_cost_basis",
    "expected_edge",
    "kelly_size_usd",
    "live_cap_notional",
    "quote_seen_at",
    "quote_age_ms",
    "best_bid",
    "best_ask",
    "limit_price",
    "order_type",
    "time_in_force",
    "venue_order_id",
    "order_lifecycle_state",
    "avg_fill_price",
    "filled_size",
    "fees",
    "post_fill_mark",
    "settlement_outcome",
    "realized_edge",
    "pnl_usd",
    "reject_reason",
)


@dataclass(frozen=True)
class LiveProfitPromotionSummary:
    canary_count: int
    unresolved_unknowns: int
    realized_edge_bps: float

    def as_artifact(self) -> dict[str, Any]:
        return {
            "canary_count": self.canary_count,
            "unresolved_unknowns": self.unresolved_unknowns,
            "realized_edge_bps": self.realized_edge_bps,
        }


class LiveProfitAuditLedger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        ensure_table(conn)

    def insert_record(self, **record: Any) -> str:
        now = _utc_now_iso()
        created_at = str(record.get("created_at") or now)
        normalized = {field: record.get(field) for field in _AUDIT_FIELDS}
        missing = [
            field
            for field in ("event_id", "aggregate_id", "condition_id", "token_id", "order_lifecycle_state")
            if not normalized.get(field)
        ]
        if missing:
            raise ValueError("EDLI_LIVE_PROFIT_AUDIT_REQUIRED_FIELDS_MISSING:" + ",".join(missing))
        audit_id = str(
            record.get("audit_id")
            or _stable_audit_id(
                normalized["aggregate_id"],
                normalized.get("execution_command_id"),
                normalized["order_lifecycle_state"],
            )
        )
        self.conn.execute(
            """
            INSERT INTO edli_live_profit_audit (
                audit_id, event_id, aggregate_id, final_intent_id,
                execution_command_id, condition_id, token_id, direction, side,
                q_live, q_lcb_5pct, expected_cost_basis, expected_edge,
                kelly_size_usd, live_cap_notional, quote_seen_at, quote_age_ms,
                best_bid, best_ask, limit_price, order_type, time_in_force,
                venue_order_id, order_lifecycle_state, avg_fill_price,
                filled_size, fees, post_fill_mark, settlement_outcome,
                realized_edge, pnl_usd, reject_reason, created_at,
                schema_version
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                audit_id,
                *[normalized[field] for field in _AUDIT_FIELDS],
                created_at,
                int(record.get("schema_version") or 1),
            ),
        )
        return audit_id

    def promotion_summary(self) -> LiveProfitPromotionSummary:
        return promotion_summary(self.conn)


def promotion_summary(conn: sqlite3.Connection) -> LiveProfitPromotionSummary:
    ensure_table(conn)
    canary_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM edli_live_profit_audit
            WHERE order_lifecycle_state IN ('CONFIRMED', 'RECONCILED', 'TERMINAL_NO_FILL')
            """
        ).fetchone()[0]
        or 0
    )
    unresolved_unknowns = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM edli_live_profit_audit
            WHERE order_lifecycle_state IN ('TIMEOUT_UNKNOWN', 'POST_SUBMIT_UNKNOWN', 'PENDING_RECONCILE')
            """
        ).fetchone()[0]
        or 0
    )
    realized_edges = [
        float(row[0])
        for row in conn.execute(
            """
            SELECT realized_edge
            FROM edli_live_profit_audit
            WHERE realized_edge IS NOT NULL
            """
        ).fetchall()
    ]
    realized_edge_bps = float(median(realized_edges) * 10_000.0) if realized_edges else 0.0
    return LiveProfitPromotionSummary(
        canary_count=canary_count,
        unresolved_unknowns=unresolved_unknowns,
        realized_edge_bps=realized_edge_bps,
    )


def write_promotion_artifact(conn: sqlite3.Connection, path: str) -> LiveProfitPromotionSummary:
    summary = promotion_summary(conn)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary.as_artifact(), fh, sort_keys=True)
        fh.write("\n")
    return summary


def _stable_audit_id(aggregate_id: str, execution_command_id: Any, order_lifecycle_state: str) -> str:
    import hashlib

    material = f"{aggregate_id}|{execution_command_id or ''}|{order_lifecycle_state}"
    return "edli-live-profit-audit:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
