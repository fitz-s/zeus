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
                later_outcome, would_have_won, would_have_filled, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
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
                event.later_outcome,
                None if event.would_have_won is None else int(event.would_have_won),
                None if event.would_have_filled is None else int(event.would_have_filled),
                datetime.now(UTC).isoformat(),
            ),
        )
        if event.market_slug:
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
                ) VALUES (?, 'high', 'unknown', 'unknown', 0, 'uncategorized', ?, 'edli_v1',
                          'edli_event', 0, ?, 38, 'degraded')
                """,
                (event.market_slug, event.rejection_reason, datetime.now(UTC).isoformat()),
            )
        except sqlite3.OperationalError:
            return


def classify_fillable_bucket(*, would_have_won: bool, would_have_filled: bool) -> RegretBucket:
    if would_have_won and would_have_filled:
        return "WOULD_HAVE_WON_AND_FILLABLE"
    if would_have_won and not would_have_filled:
        return "WOULD_HAVE_WON_BUT_UNFILLABLE"
    return "WOULD_HAVE_LOST"
