"""EDLI NoTradeRegretLedger."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from src.events.idempotency import stable_event_id

UTC = timezone.utc

RejectionStage = Literal[
    "EVENT_FILTER",
    "CAUSAL_STATE",
    "SOURCE_TRUTH",
    "FORECAST_COMPLETENESS",
    "FAMILY_TOPOLOGY",
    "INFERENCE",
    "EXECUTABLE_QUOTE",
    "TRADE_SCORE",
    "FDR",
    "KELLY",
    "RISK_GUARD",
    "EXECUTOR_EXPRESSIBILITY",
    "LIVE_CAP",
    "UNKNOWN_REVIEW_REQUIRED",
]

RegretBucket = Literal[
    "MODEL_WRONG",
    "SOURCE_WRONG",
    "QUOTE_UNAVAILABLE",
    "FEE_ERASED_EDGE",
    "NO_DEPTH",
    "UNFILLABLE",
    "FDR_REJECTED",
    "KELLY_TOO_SMALL",
    "RISK_CAP",
    "SHOULDER_TAIL_BLOCK",
    "FAMILY_INCOMPLETE",
    "WOULD_HAVE_WON_BUT_UNFILLABLE",
    "WOULD_HAVE_WON_AND_FILLABLE",
    "WOULD_HAVE_LOST",
    "LEAKAGE_BLOCKED",
    "UNKNOWN_REVIEW_REQUIRED",
]


@dataclass(frozen=True)
class NoTradeRegretEvent:
    event_id: str
    rejection_stage: RejectionStage
    rejection_reason: str
    regret_bucket: RegretBucket
    market_slug: str | None = None
    condition_id: str | None = None
    token_id: str | None = None
    outcome_label: str | None = None
    decision_time: str | None = None
    city: str | None = None
    target_date: str | None = None
    metric: str | None = None
    observation_time: str | None = None
    decision_seq: int | None = None
    family_id: str | None = None
    bin_label: str | None = None
    direction: str | None = None
    q_live: float | None = None
    q_lcb_5pct: float | None = None
    c_fee_adjusted: float | None = None
    c_cost_95pct: float | None = None
    p_fill_lcb: float | None = None
    trade_score: float | None = None
    native_quote_available: bool | None = None
    source_status: str | None = None
    family_complete: bool | None = None
    hypothetical_order_type: str | None = None
    hypothetical_fill_status: str | None = None
    hypothetical_fill_price: float | None = None
    causal_snapshot_id: str | None = None
    executable_snapshot_id: str | None = None
    later_outcome: str | None = None
    would_have_won: bool | None = None
    would_have_filled: bool | None = None


class NoTradeRegretLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_idempotent(self, event: NoTradeRegretEvent) -> str:
        regret_event_id = stable_event_id(event.event_id, event.rejection_stage, event.rejection_reason)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO no_trade_regret_events (
                regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
                market_slug, condition_id, token_id, outcome_label,
                decision_time, city, target_date, metric, family_id, bin_label, direction,
                q_live, q_lcb_5pct, c_fee_adjusted, c_cost_95pct, p_fill_lcb, trade_score,
                native_quote_available, source_status, family_complete,
                hypothetical_order_type, hypothetical_fill_status, hypothetical_fill_price,
                causal_snapshot_id, executable_snapshot_id,
                later_outcome, would_have_won, would_have_filled, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                regret_event_id,
                event.event_id,
                event.rejection_stage,
                event.rejection_reason,
                event.regret_bucket,
                event.market_slug,
                event.condition_id,
                event.token_id,
                event.outcome_label,
                event.decision_time,
                event.city,
                event.target_date,
                event.metric,
                event.family_id,
                event.bin_label,
                event.direction,
                event.q_live,
                event.q_lcb_5pct,
                event.c_fee_adjusted,
                event.c_cost_95pct,
                event.p_fill_lcb,
                event.trade_score,
                None if event.native_quote_available is None else int(event.native_quote_available),
                event.source_status,
                None if event.family_complete is None else int(event.family_complete),
                event.hypothetical_order_type,
                event.hypothetical_fill_status,
                event.hypothetical_fill_price,
                event.causal_snapshot_id,
                event.executable_snapshot_id,
                event.later_outcome,
                None if event.would_have_won is None else int(event.would_have_won),
                None if event.would_have_filled is None else int(event.would_have_filled),
                datetime.now(UTC).isoformat(),
            ),
        )
        if _has_compatibility_natural_key(event):
            self._write_no_trade_events_compatibility(event)
        return regret_event_id

    def live_reader_rows(self) -> list[dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT regret_event_id, event_id, rejection_stage, rejection_reason,
                   market_slug, condition_id, token_id, outcome_label, created_at
            FROM no_trade_regret_events
            ORDER BY created_at, regret_event_id
            """
        ).fetchall()
        keys = [
            "regret_event_id",
            "event_id",
            "rejection_stage",
            "rejection_reason",
            "market_slug",
            "condition_id",
            "token_id",
            "outcome_label",
            "created_at",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def _write_no_trade_events_compatibility(self, event: NoTradeRegretEvent) -> None:
        try:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO no_trade_events (
                    market_slug, temperature_metric, target_date, observation_time,
                    decision_seq, reason, reason_detail, strategy_key, event_source,
                    shadow_runtime, observed_at, schema_version, schema_compatibility
                ) VALUES (?, ?, ?, ?, ?, 'uncategorized', ?, 'edli_v1',
                          'edli_event', 0, ?, 38, 'degraded')
                """,
                (
                    event.market_slug,
                    event.metric,
                    event.target_date,
                    event.observation_time,
                    event.decision_seq,
                    event.rejection_reason,
                    event.decision_time or datetime.now(UTC).isoformat(),
                ),
            )
        except sqlite3.OperationalError:
            return


def classify_fillable_bucket(*, would_have_won: bool, would_have_filled: bool) -> RegretBucket:
    if would_have_won and would_have_filled:
        return "WOULD_HAVE_WON_AND_FILLABLE"
    if would_have_won and not would_have_filled:
        return "WOULD_HAVE_WON_BUT_UNFILLABLE"
    return "WOULD_HAVE_LOST"


def _has_compatibility_natural_key(event: NoTradeRegretEvent) -> bool:
    return (
        bool(event.market_slug)
        and bool(event.metric)
        and bool(event.target_date)
        and bool(event.observation_time)
        and event.decision_seq is not None
    )
