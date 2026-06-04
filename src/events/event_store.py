"""World-DB event store for EDLI opportunity events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
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
            # Distinguish a GENUINE schema fault ("no such table") from TRANSIENT
            # write contention ("database is locked" / "database is busy"). The world
            # DB is a WAL multi-writer (market-channel ingestor + CollateralLedger +
            # reactor emit) and a >busy_timeout lock here is a transient blip, NOT a
            # missing table. Mislabeling a locked DB as "table missing" raised a fatal-
            # looking EventStoreSchemaError that crashed the whole reactor cycle and
            # mis-led every diagnosis (the table demonstrably exists). Re-raise the
            # transient lock as-is so the caller can treat it as retryable; only the
            # real schema fault becomes EventStoreSchemaError.
            message = str(exc).lower()
            if "no such table" in message:
                raise EventStoreSchemaError(
                    "opportunity_events table missing from supplied connection; open the world DB"
                ) from exc
            raise

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
        """Fetch pending events in deterministic replay/inference order.

        STEP 3 timeliness fix (consolidated timeliness/tradeability design):

        (a) **Claim floor** — a past-target event (its whole target LOCAL day
            already settled at ``decision_time``) is NEVER returned. Enforced by
            the canonical ``is_forecast_only_admissible`` cheap predicate as a
            Python post-filter (timezone resolution is per-city Python, not SQL).
            This is the conservative lower bound of the reactor's full phase
            gate, so it never starves a candidate the reactor would admit.

        (b) **Freshest-target-first ordering** — within each urgency tier the
            rows are sorted by ``target_date DESC`` then ``available_at DESC`` so
            the reactor reaches fresh candidates before its per-cycle budget is
            exhausted, instead of draining the budget on the oldest (often
            already-settled) events first.

        Non-FORECAST_SNAPSHOT_READY events carry no per-city forecast target and
        are NOT phase-filtered here (market-channel/day0 events have their own
        scope); only events that expose a city+target_date are subject to the
        timeliness floor.
        """

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
              e.priority DESC,
              -- FRESHEST-TARGET-FIRST: reach fresh candidates before the per-cycle
              -- budget is spent on stale ones. NULL target_date (non-forecast
              -- events) sorts last within its tier (json_extract → NULL → last
              -- under DESC in SQLite, which orders NULLs first for ASC / last for
              -- DESC). available_at DESC breaks ties freshest-first.
              json_extract(e.payload_json, '$.target_date') DESC,
              e.available_at DESC, e.received_at DESC, e.event_id ASC
            LIMIT ?
            """,
            (
                self.consumer_name,
                stale_processing_before,
                parsed_decision_time.isoformat(),
                parsed_decision_time.isoformat(),
                parsed_decision_time.isoformat(),
                # Fetch extra rows so the post-filter can drop stale events
                # without under-filling the caller's requested limit.
                max(limit * 4, limit + 50),
            ),
        ).fetchall()

        events = [_event_from_row(row) for row in rows]
        admissible = [e for e in events if self._is_timely(e, parsed_decision_time)]
        return admissible[:limit]

    def _is_timely(self, event: OpportunityEvent, decision_time_utc: datetime) -> bool:
        """Claim-floor timeliness gate (STEP 3a).

        Rejects a FORECAST_SNAPSHOT_READY event whose target LOCAL day is
        ALREADY STRICTLY PAST at ``decision_time`` — i.e. the event refers to a
        market that has already entered POST_TRADING/settled. This is the bug
        the operator reported: the reactor burned its per-cycle budget on
        already-closed June-4 markets while the June-5 decision ran, never
        reaching fresh candidates.

        Scope deliberately STRICTLY-PAST, not same-day: a same-day
        (SETTLEMENT_DAY) FORECAST_SNAPSHOT_READY event is still allowed through
        so the reactor's own bind-time gate
        (``EVENT_BOUND_MARKET_PHASE_CLOSED``) rejects-and-CONSUMES it cleanly.
        Dropping same-day events here would strand them as permanently-pending
        (never returned ⇒ never claimed ⇒ never marked processed ⇒ leak). The
        strictly-past floor only removes events that can neither produce a
        receipt nor need the reactor's settlement-day handling — pure budget
        waste.

        Strictly-past is computed via the canonical settlement geometry: the
        target local day is entirely in the past iff ``decision_time`` is at or
        after local-midnight of the day AFTER ``target_local_date`` (the
        SETTLEMENT_DAY-entry instant of ``target_date + 1``). tz arithmetic,
        never lexicographic string compare.

        Events with no city+target_date (market-channel/day0) are not
        phase-filtered here and pass through. Fail-closed on an unresolvable
        city/target → reject (cannot be timely-verified).
        """
        if event.event_type != "FORECAST_SNAPSHOT_READY":
            return True
        try:
            payload = json.loads(event.payload_json)
        except (ValueError, TypeError):
            return False
        city = payload.get("city")
        target_date = payload.get("target_date")
        if not city or not target_date:
            return False

        from datetime import timedelta as _timedelta

        from src.config import runtime_cities_by_name
        from src.strategy.market_phase import settlement_day_entry_utc

        city_config = runtime_cities_by_name().get(city)
        tz = getattr(city_config, "timezone", None) if city_config is not None else None
        if not tz:
            return False
        try:
            target_local_date = date.fromisoformat(str(target_date))
        except ValueError:
            return False

        try:
            day_after_entry = settlement_day_entry_utc(
                target_local_date=target_local_date + _timedelta(days=1),
                city_timezone=tz,
            )
        except Exception:
            return False

        # Strictly past ⇒ decision is at/after local-midnight of the next day.
        return decision_time_utc < day_after_entry

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
