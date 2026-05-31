"""World-DB event store for EDLI opportunity events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from src.events.opportunity_event import OpportunityEvent


class EventStoreSchemaError(RuntimeError):
    """Raised when a caller supplies a connection without EDLI world tables."""


class EventStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        consumer_name: str = "edli_reactor_v1",
        processing_lease_seconds: int = 300,
    ) -> None:
        self.conn = conn
        self.consumer_name = consumer_name
        self.processing_lease_seconds = processing_lease_seconds

    def insert_or_ignore(self, event: OpportunityEvent) -> bool:
        """Insert immutable event row and initialize mutable processing state."""

        self._require_world_event_tables()
        row = asdict(event)
        try:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_events (
                    event_id, event_type, entity_key, source,
                    observed_at, available_at, received_at,
                    causal_snapshot_id, payload_hash, idempotency_key,
                    priority, expires_at, payload_json, schema_version, created_at
                ) VALUES (
                    :event_id, :event_type, :entity_key, :source,
                    :observed_at, :available_at, :received_at,
                    :causal_snapshot_id, :payload_hash, :idempotency_key,
                    :priority, :expires_at, :payload_json, :schema_version, :created_at
                )
                """,
                row,
            )
        except sqlite3.OperationalError as exc:
            raise EventStoreSchemaError(
                "opportunity_events table missing from supplied connection; open the world DB"
            ) from exc

        inserted = cur.rowcount == 1
        if inserted:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_event_processing (
                    consumer_name, event_id, processing_status, attempt_count, updated_at
                ) VALUES (?, ?, 'pending', 0, ?)
                """,
                (self.consumer_name, event.event_id, _utc_now()),
            )
        return inserted

    def fetch_pending(self, *, decision_time: str, limit: int = 100) -> list[OpportunityEvent]:
        """Fetch pending events in deterministic replay/inference order."""

        self._require_world_event_tables()
        parsed_decision_time = _parse_utc(decision_time)
        stale_processing_before = (
            parsed_decision_time - timedelta(seconds=self.processing_lease_seconds)
        ).isoformat()
        rows = self.conn.execute(
            """
            SELECT e.*
            FROM opportunity_events e
            JOIN opportunity_event_processing p
              ON p.event_id = e.event_id
             AND p.consumer_name = ?
            WHERE (
                    p.processing_status = 'pending'
                 OR (
                    p.processing_status = 'processing'
                    AND p.claimed_at IS NOT NULL
                    AND p.claimed_at <= ?
                 )
              )
              AND e.available_at <= ?
              AND e.received_at <= ?
              AND (e.expires_at IS NULL OR e.expires_at > ?)
            ORDER BY
              -- Tier 0: COMPLETE FORECAST_SNAPSHOT_READY — direct receipt candidates, highest urgency.
              -- Tier 1: Other decision-trigger events (PARTIAL FSR, DAY0_EXTREME_UPDATED) — still
              --         actionable or cheaply dead-letterable; must not be starved by market-channel.
              -- Tier 2: Market-channel cache-hydration events (BEST_BID_ASK_CHANGED, BOOK_SNAPSHOT,
              --         NEW_MARKET_DISCOVERED) — they get rejected NO_DIRECT_STALE_TRADE immediately
              --         but can accumulate to 300k+; without explicit demotion they starve all FSR.
              CASE
                WHEN e.event_type = 'FORECAST_SNAPSHOT_READY'
                 AND json_extract(e.payload_json, '$.source_run_completeness_status') = 'COMPLETE'
                THEN 0
                WHEN e.event_type IN ('BEST_BID_ASK_CHANGED', 'BOOK_SNAPSHOT', 'NEW_MARKET_DISCOVERED')
                THEN 2
                ELSE 1
              END ASC,
              e.priority DESC, e.available_at ASC, e.received_at ASC, e.event_id ASC
            LIMIT ?
            """,
            (
                self.consumer_name,
                stale_processing_before,
                parsed_decision_time.isoformat(),
                parsed_decision_time.isoformat(),
                parsed_decision_time.isoformat(),
                limit,
            ),
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def replay_events(self) -> list[OpportunityEvent]:
        """Replay all event rows in deterministic event order."""

        self._require_world_event_tables()
        rows = self.conn.execute(
            """
            SELECT * FROM opportunity_events
            ORDER BY priority DESC, available_at ASC, received_at ASC, event_id ASC
            """
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def claim(self, event_id: str, *, claimed_at: str | None = None) -> bool:
        self._require_world_event_tables()
        cur = self.conn.execute(
            """
            UPDATE opportunity_event_processing
               SET processing_status = 'processing',
                   attempt_count = attempt_count + 1,
                   claimed_at = ?,
                   updated_at = ?
             WHERE consumer_name = ?
               AND event_id = ?
               AND (
                    processing_status = 'pending'
                 OR (
                    processing_status = 'processing'
                    AND claimed_at IS NOT NULL
                    AND claimed_at <= ?
                 )
               )
            """,
            (
                claimed_at or _utc_now(),
                _utc_now(),
                self.consumer_name,
                event_id,
                (_parse_utc(claimed_at) - timedelta(seconds=self.processing_lease_seconds)).isoformat()
                if claimed_at is not None
                else (datetime.now(timezone.utc) - timedelta(seconds=self.processing_lease_seconds)).isoformat(),
            ),
        )
        return cur.rowcount == 1

    def mark_processed(self, event_id: str, *, processed_at: str | None = None) -> None:
        self._mark_terminal(event_id, "processed", processed_at or _utc_now(), None)

    def mark_failed(self, event_id: str, error: str, *, failed_at: str | None = None) -> None:
        self._mark_terminal(event_id, "failed", failed_at or _utc_now(), error)

    def mark_dead_letter(
        self,
        event: OpportunityEvent,
        *,
        failure_stage: str,
        error_message: str,
        created_at: str | None = None,
    ) -> str:
        """Move an event into dead-letter evidence without mutating the event row."""

        self._require_world_event_tables()
        dead_letter_id = f"{self.consumer_name}:{event.event_id}:{failure_stage}"
        now = created_at or _utc_now()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO event_dead_letters (
                dead_letter_id, consumer_name, event_id, failure_stage,
                error_message, event_payload_json, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                dead_letter_id,
                self.consumer_name,
                event.event_id,
                failure_stage,
                error_message,
                json.dumps(
                    {
                        "event_type": event.event_type,
                        "entity_key": event.entity_key,
                        "payload_json": event.payload_json,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now,
            ),
        )
        self._mark_terminal(event.event_id, "dead_letter", now, error_message)
        return dead_letter_id

    def attempt_count(self, event_id: str) -> int:
        """Number of times this event has been claimed (incremented by `claim`)."""

        self._require_world_event_tables()
        row = self.conn.execute(
            "SELECT attempt_count FROM opportunity_event_processing "
            "WHERE consumer_name = ? AND event_id = ?",
            (self.consumer_name, event_id),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def requeue_pending(self, event_id: str) -> None:
        """Return an in-flight ('processing') event to 'pending' for retry next cycle.

        Used for TRANSIENT, non-terminal blocks (e.g. the executable market snapshot for the
        family has not been captured yet this cycle). Keeps ``attempt_count`` so the caller
        can bound retries and eventually dead-letter; does NOT consume the event the way
        ``mark_processed`` does.
        """

        self._require_world_event_tables()
        self.conn.execute(
            "UPDATE opportunity_event_processing "
            "SET processing_status = 'pending', claimed_at = NULL, updated_at = ? "
            "WHERE consumer_name = ? AND event_id = ?",
            (_utc_now(), self.consumer_name, event_id),
        )

    def _mark_terminal(
        self,
        event_id: str,
        status: str,
        status_time: str,
        error: str | None,
    ) -> None:
        self._require_world_event_tables()
        self.conn.execute(
            """
            UPDATE opportunity_event_processing
               SET processing_status = ?,
                   processed_at = ?,
                   last_error = ?,
                   updated_at = ?
             WHERE consumer_name = ?
               AND event_id = ?
            """,
            (status, status_time, error, _utc_now(), self.consumer_name, event_id),
        )

    def _require_world_event_tables(self) -> None:
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('opportunity_events', 'opportunity_event_processing', 'event_dead_letters')"
            ).fetchall()
        }
        if not {"opportunity_events", "opportunity_event_processing"} <= tables:
            raise EventStoreSchemaError(
                "EDLI event tables are missing from supplied connection; use init_schema/world DB"
            )


def _event_from_row(row: sqlite3.Row | tuple) -> OpportunityEvent:
    if isinstance(row, sqlite3.Row):
        data = dict(row)
    else:
        keys = [
            "event_id",
            "event_type",
            "entity_key",
            "source",
            "observed_at",
            "available_at",
            "received_at",
            "causal_snapshot_id",
            "payload_hash",
            "idempotency_key",
            "priority",
            "expires_at",
            "payload_json",
            "schema_version",
            "created_at",
        ]
        data = dict(zip(keys, row))
    return OpportunityEvent(**data)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event store timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
