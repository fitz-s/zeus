# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: Operator P1 2026-06-04 — channel-sweep keeper query index-back
#                  (category-kill of 85s json_extract full-scan); Step-3 batch UPDATE
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
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            JOIN opportunity_events e
              ON e.event_id = p.event_id
            WHERE p.consumer_name = ?
              AND (
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

    def archive_expired_candidates(
        self, *, decision_time: str, batch_limit: int = 50_000
    ) -> int:
        """Sweep strictly-past-in-tz pending/processing candidates to terminal
        ``expired`` status so the active scan stops re-reading them.

        OPERATOR DIRECTIVE 2026-06-04 — the working set
        (``opportunity_event_processing``) accumulated ~1.76M ``pending`` rows that
        ``fetch_pending`` and the warm-cache family queries re-JOIN and re-ORDER every
        cycle. ``fetch_pending`` filters strictly-past FSR rows on READ (#183) but
        never PRUNES them; this sweep is the missing prune.

        STRUCTURAL CHOICE (not a patch): the immutable ``opportunity_events`` log is
        append-only (provenance — protected by a no-DELETE trigger). We mark the
        MUTABLE processing row ``'expired'`` (a terminal status already reserved in the
        ``opportunity_event_processing`` CHECK constraint, until now un-wired).
        ``'expired'`` is excluded from every reader's ``processing_status`` filter
        (``fetch_pending``, the two warm-cache family queries, ``_edli_pending_entity_keys``),
        so one sweep removes the row from ALL scan paths without touching provenance.

        EXPIRY is PER-CITY LOCAL TIMEZONE, never raw UTC: a candidate is expired iff
        its whole target LOCAL day has ENDED in its OWN city tz — exactly the
        strictly-past boundary ``_is_timely`` rejects (``decision_time >=
        settlement_day_entry_utc(target_date + 1 day)``). Same predicate, shared with
        the read floor (``_event_strictly_past_in_tz``) so the two can never diverge.

        OCEANIA-FRONTIER cheap pre-filter: only rows whose ``target_date`` is at or
        after ``frontier_local_date - 1`` (the current local date in the
        globally-earliest-rolling timezone, Oceania UTC+13/+12, minus one day for the
        local-day-still-open margin) can POSSIBLY still be active in any city.
        Everything strictly older is unconditionally past in every timezone on Earth,
        so we archive those by the cheap string bound WITHOUT the per-city Python tz
        round-trip, and only run the expensive per-city check on the frontier band.
        This bounds the expensive work to the handful of recent target_dates.

        FAIL-CLOSED: an FSR whose city/target_date is missing or whose timezone is
        unresolvable is KEPT ACTIVE (never archived) — archiving an active row would
        silently drop a real candidate. Non-FSR (market-channel/day0) events carry no
        per-city forecast target and are out of scope for this per-city sweep.

        IDEMPOTENT + budget-safe: only ``pending``/``processing`` rows are touched and
        only those proven strictly-past; a re-run at the same decision time is a no-op.
        ``batch_limit`` bounds the rows examined per call so a one-time 1.7M backlog
        drains across cycles instead of in one giant transaction.

        Returns the number of processing rows transitioned to ``expired``.
        """

        self._require_world_event_tables()
        decision_time_utc = _parse_utc(decision_time)

        # Oceania-frontier cheap bound: the most-advanced local calendar date on Earth
        # at decision_time, minus one day of margin. Any target_date strictly before
        # this is past in EVERY timezone and needs no per-city check.
        frontier_floor = _oceania_frontier_target_floor(decision_time_utc)

        candidate_rows = self.conn.execute(
            """
            SELECT e.event_id,
                   json_extract(e.payload_json, '$.city')        AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date
            FROM opportunity_events e
            JOIN opportunity_event_processing p
              ON p.event_id = e.event_id
             AND p.consumer_name = ?
            WHERE e.event_type = 'FORECAST_SNAPSHOT_READY'
              AND p.processing_status IN ('pending', 'processing')
              AND json_extract(e.payload_json, '$.target_date') IS NOT NULL
              AND json_extract(e.payload_json, '$.target_date') < ?
            ORDER BY json_extract(e.payload_json, '$.target_date') ASC
            LIMIT ?
            """,
            (self.consumer_name, frontier_floor, batch_limit),
        ).fetchall()

        expired_ids: list[str] = []
        for row in candidate_rows:
            event_id = row[0]
            city = row[1]
            target_date = row[2]
            if self._strictly_past_in_tz(city, target_date, decision_time_utc):
                expired_ids.append(event_id)

        for event_id in expired_ids:
            self.conn.execute(
                """
                UPDATE opportunity_event_processing
                   SET processing_status = 'expired',
                       processed_at = ?,
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id = ?
                   AND processing_status IN ('pending', 'processing')
                """,
                (
                    decision_time_utc.isoformat(),
                    _utc_now(),
                    self.consumer_name,
                    event_id,
                ),
            )
        return len(expired_ids)

    # Channel event types that carry a per-token price-update stream and are
    # subject to the superseded-keep-latest sweep. NEW_MARKET_DISCOVERED is
    # included because a re-discovered market replaces prior discovery events
    # for the same token just as price ticks do.
    _CHANNEL_EVENT_TYPES: tuple[str, ...] = (
        "BEST_BID_ASK_CHANGED",
        "BOOK_SNAPSHOT",
        "NEW_MARKET_DISCOVERED",
    )

    def archive_superseded_channel_events(
        self, *, batch_limit: int = 5_000
    ) -> int:
        """Sweep superseded per-token channel events to terminal ``'expired'`` status.

        OPERATOR DIRECTIVE 2026-06-04 (companion to ``archive_expired_candidates``):
        ``BEST_BID_ASK_CHANGED`` / ``BOOK_SNAPSHOT`` / ``NEW_MARKET_DISCOVERED`` events
        are a price-update stream for each market token. For any given ``(event_type,
        token_id)`` group, only the event with the LATEST ``available_at`` carries live
        state; every older event in the group is superseded and useless to re-scan —
        but ~1.7M such pending rows were piling up because nothing ever pruned them.

        INVARIANT (superseded-keep-latest): for each ``(event_type, token_id)`` group
        in the active working set (``pending`` / ``processing`` only), mark all rows
        EXCEPT the one with ``MAX(available_at)`` as ``'expired'``. This is strictly
        correct: the latest tick for a token is the only one the reactor could act on;
        all older ticks are definitionally superseded regardless of elapsed time.

        WHY NOT A TIME THRESHOLD: a pure age cutoff (e.g. "older than 2h") could
        wrongly archive a fresh event for a slow-updating token.  The superseded test
        is definitional and requires no arbitrary threshold: if there is a newer event
        for the SAME key, the older one cannot contribute new information.

        TOKEN KEY: ``token_id`` from ``payload_json`` (confirmed present in every live
        ``BEST_BID_ASK_CHANGED`` and ``BOOK_SNAPSHOT`` row sampled 2026-06-04; see
        ``MarketBookEventPayload.token_id``). Events whose ``token_id`` is NULL or
        missing are KEPT ACTIVE (fail-closed — never archive an unverifiable row).

        The group key is ``(event_type, token_id)`` — not just ``token_id`` — so a
        ``BEST_BID_ASK_CHANGED`` and a ``BOOK_SNAPSHOT`` for the same token are
        treated as independent streams (they carry different information; the latest
        BA-changed event and the latest book snapshot are both kept).

        APPEND-ONLY PROVENANCE: only ``opportunity_event_processing.processing_status``
        is mutated; the immutable ``opportunity_events`` row is never deleted.

        BATCH-BOUNDED: ``batch_limit`` caps the candidate rows examined per call so
        a large backlog drains across cycles without occupying the reactor worker.
        The keeper lookup is scoped to only the ``(event_type, token_id)`` keys seen
        in that candidate batch; it must not group-scan the entire channel backlog.
        The groups are evaluated in ascending ``available_at`` order so older
        superseded events are swept first.

        IDEMPOTENT: re-running at the same state archives nothing new (already-expired
        rows are excluded from the ``pending``/``processing`` filter).

        Returns the number of processing rows transitioned to ``'expired'``.
        """

        self._require_world_event_tables()
        type_placeholders = ",".join("?" * len(self._CHANNEL_EVENT_TYPES))

        # Step 1: fetch the oldest active channel-event rows with parseable token_id.
        # This is the only unscoped scan in the sweep and is batch-limited. The prior
        # implementation first computed keepers across the whole backlog, which could
        # pin the EDLI reactor for minutes before it reached fetch_pending/receipts.
        candidate_rows = self.conn.execute(
            f"""
            SELECT e.event_id,
                   e.event_type,
                   json_extract(e.payload_json, '$.token_id') AS token_id
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            JOIN opportunity_events e
              ON e.event_id = p.event_id
            WHERE p.consumer_name = ?
              AND p.processing_status IN ('pending', 'processing')
              AND e.event_type IN ({type_placeholders})
              AND json_extract(e.payload_json, '$.token_id') IS NOT NULL
            ORDER BY e.available_at ASC
            LIMIT ?
            """,
            (self.consumer_name, *self._CHANNEL_EVENT_TYPES, batch_limit),
        ).fetchall()

        if not candidate_rows:
            return 0

        # Step 2: for only the token streams represented in the candidate batch,
        # find the current keeper(s). The keeper may be outside the candidate batch;
        # preserving it is what makes a small batch safe. Ties at MAX(available_at)
        # are all kept to avoid arbitrary archiving when the venue emits duplicate
        # timestamps for distinct payloads.
        keeper_rows = self.conn.execute(
            f"""
            WITH candidate_rows AS (
                SELECT e.event_id,
                       e.event_type,
                       json_extract(e.payload_json, '$.token_id') AS token_id,
                       e.available_at
                FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
                JOIN opportunity_events e
                  ON e.event_id = p.event_id
                WHERE p.consumer_name = ?
                  AND p.processing_status IN ('pending', 'processing')
                  AND e.event_type IN ({type_placeholders})
                  AND json_extract(e.payload_json, '$.token_id') IS NOT NULL
                ORDER BY e.available_at ASC
                LIMIT ?
            ),
            candidate_keys AS (
                SELECT DISTINCT event_type, token_id
                FROM candidate_rows
            ),
            keeper_max AS (
                SELECT e2.event_type AS event_type,
                       json_extract(e2.payload_json, '$.token_id') AS token_id,
                       MAX(e2.available_at) AS max_available_at
                FROM opportunity_events e2
                JOIN opportunity_event_processing p2
                  ON p2.event_id = e2.event_id
                 AND p2.consumer_name = ?
                JOIN candidate_keys k
                  ON k.event_type = e2.event_type
                 AND k.token_id = json_extract(e2.payload_json, '$.token_id')
                WHERE p2.processing_status IN ('pending', 'processing')
                GROUP BY e2.event_type, json_extract(e2.payload_json, '$.token_id')
            )
                SELECT e.event_id
                FROM opportunity_events e
                JOIN opportunity_event_processing p
                  ON p.event_id = e.event_id
                 AND p.consumer_name = ?
                JOIN keeper_max k
                  ON k.event_type = e.event_type
                 AND k.token_id = json_extract(e.payload_json, '$.token_id')
                 AND k.max_available_at = e.available_at
                WHERE p.processing_status IN ('pending', 'processing')
            """,
            (
                self.consumer_name,
                *self._CHANNEL_EVENT_TYPES,
                batch_limit,
                self.consumer_name,
                self.consumer_name,
            ),
        ).fetchall()
        keeper_ids: set[str] = {str(row[0]) for row in keeper_rows}

        superseded_ids = [str(row[0]) for row in candidate_rows if str(row[0]) not in keeper_ids]

        if not superseded_ids:
            return 0

        # Step 3: batch UPDATE in chunks of 500 instead of one statement per row.
        # Replaces the prior per-row loop that issued up to batch_limit (100k)
        # individual statements per cycle.  Chunk size 500 keeps the IN-list well
        # below SQLite's SQLITE_MAX_VARIABLE_NUMBER (999 default) while reducing
        # round-trips by ~200×.  Semantics are identical: only pending/processing
        # rows transition to expired.
        now = _utc_now()
        _CHUNK = 500
        for chunk_start in range(0, len(superseded_ids), _CHUNK):
            chunk = superseded_ids[chunk_start : chunk_start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            self.conn.execute(
                f"""
                UPDATE opportunity_event_processing
                   SET processing_status = 'expired',
                       processed_at = ?,
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id IN ({placeholders})
                   AND processing_status IN ('pending', 'processing')
                """,
                (now, now, self.consumer_name, *chunk),
            )
        return len(superseded_ids)

    @staticmethod
    def _strictly_past_in_tz(
        city: str | None, target_date: str | None, decision_time_utc: datetime
    ) -> bool:
        """True iff city X's target LOCAL day has ENDED at ``decision_time``.

        Single authority shared by the read floor (``_is_timely``) and the archive
        sweep: the target local day is strictly past iff ``decision_time`` is at or
        after city-local midnight of ``target_date + 1`` (the SETTLEMENT_DAY-entry
        instant of the day AFTER the target). tz arithmetic via the canonical
        ``settlement_day_entry_utc`` — never a lexicographic string compare.

        Fail-closed: missing city/target_date or an unresolvable timezone → returns
        False (NOT strictly past) so the caller keeps the row active. A True here
        archives a row; mislabeling an active row True would silently drop a real
        candidate, so every uncertain case must return False.
        """
        if not city or not target_date:
            return False

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
                target_local_date=target_local_date + timedelta(days=1),
                city_timezone=tz,
            )
        except Exception:
            return False
        return decision_time_utc >= day_after_entry

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

        # Timely ⇔ NOT strictly-past-in-its-tz. Shares the SINGLE authority
        # (_strictly_past_in_tz) with the archive sweep so the read floor and the
        # prune can never disagree on the boundary. Fail-closed: an unresolvable
        # city/tz makes _strictly_past_in_tz return False (not provably past), so
        # the read floor must independently reject the unverifiable event here.
        from src.config import runtime_cities_by_name

        city_config = runtime_cities_by_name().get(city)
        tz = getattr(city_config, "timezone", None) if city_config is not None else None
        if not tz:
            # Unresolvable tz: read floor fails closed (cannot timely-verify) — but
            # the archive sweep keeps the same row active. The asymmetry is
            # deliberate: dropping from a single read cycle is recoverable; archiving
            # an unverifiable row is not.
            return False
        try:
            date.fromisoformat(str(target_date))
        except ValueError:
            return False

        return not self._strictly_past_in_tz(city, target_date, decision_time_utc)

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


# The globally-earliest-rolling timezone — Oceania (Auckland / Wellington,
# UTC+13 DST / UTC+12 standard). This is the most-advanced wall clock on Earth,
# so its local calendar date is the frontier that drives the rollover reference
# for the archive sweep. Using the earliest-tz "now" (never raw UTC) guarantees a
# candidate is not declared globally-past while its own city's local day — or the
# frontier's — is still open.
_OCEANIA_FRONTIER_TZ = "Pacific/Auckland"


def _oceania_frontier_target_floor(decision_time_utc: datetime) -> str:
    """ISO date string: any FSR ``target_date`` strictly BELOW this is past in
    EVERY timezone on Earth at ``decision_time``, so it can be archived by a cheap
    string compare without a per-city tz round-trip.

    Anchored to the earliest-rolling clock (Oceania): the floor is the current
    local calendar date in ``Pacific/Auckland`` MINUS one day. The one-day margin
    is conservative — the widest possible spread between the earliest tz (UTC+13)
    and the latest inhabited tz (UTC-12) is 25h < 2 calendar days, so a target on
    ``frontier_date - 1`` could still be the active local day for the most-lagging
    city; only ``< frontier_date - 1`` is unconditionally past everywhere. Rows in
    the frontier band still get the exact per-city ``_strictly_past_in_tz`` check.

    Fail-open on a tz resolution error → returns a date far in the past so NOTHING
    is cheap-archived and every row falls through to the exact per-city check
    (never an over-archive).
    """
    from zoneinfo import ZoneInfo

    try:
        frontier_local_date = decision_time_utc.astimezone(
            ZoneInfo(_OCEANIA_FRONTIER_TZ)
        ).date()
    except Exception:
        return "0001-01-01"
    return (frontier_local_date - timedelta(days=1)).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event store timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
