# Created: 2026-06-04
# Last reused/audited: 2026-06-19
# Authority basis: Operator P1 2026-06-04 — channel-sweep keeper query index-back
#                  (category-kill of 85s json_extract full-scan); Step-3 batch UPDATE.
#                  2026-06-11 operator throughput/fairness directive — fetch_pending
#                  per-city round-robin claim order (anti-starvation under a bounded
#                  per-cycle decision budget); see fetch_pending docstring + the
#                  tests/events/test_fetch_pending_city_fairness.py antibodies.
"""World-DB event store for EDLI opportunity events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from src.events.event_priority import ESCALATION_CROSS_SOURCE_PREFIX
from src.events.opportunity_event import OpportunityEvent

# Continuous re-decision resurrection (2026-06-12): the forecast decision lane. EDLI_REDECISION_PENDING
# carries the same FSR-shaped city/target payload and gets the same timeliness floor. Literal here
# (mirrors src.events.continuous_redecision.REDECISION_EVENT_TYPE) to avoid an import cycle.
_FORECAST_DECISION_EVENT_TYPES = frozenset({"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"})
_NO_VALUE_REFUTATION_EVENT_TYPES = _FORECAST_DECISION_EVENT_TYPES | frozenset(
    {"DAY0_EXTREME_UPDATED"}
)
_TERMINAL_NO_VALUE_REFUTATION_SQL = """
    (
        rejection_stage = 'TRADE_SCORE'
        AND (
            rejection_reason IN ('TRADE_SCORE_NON_POSITIVE', 'TRADE_SCORE_BLOCKED')
         OR rejection_reason LIKE 'TRADE_SCORE_NON_POSITIVE:%'
         OR rejection_reason LIKE 'TRADE_SCORE_BLOCKED:%'
         OR rejection_reason = 'FDR_REJECTED'
         OR rejection_reason LIKE 'FDR_REJECTED:%'
         OR rejection_reason LIKE 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:%'
        )
    )
 OR (
        rejection_stage = 'EXECUTION_RECEIPT'
        AND (
            rejection_reason LIKE 'TAKER_QUALITY_PROOF_NOT_PASSED:%'
         OR rejection_reason LIKE 'entry_taker_quality:%'
        )
    )
 OR (
        rejection_stage = 'EXECUTOR_EXPRESSIBILITY'
        AND (
            rejection_reason LIKE 'EDLI_LIVE_CERTIFICATE_BUILD_FAILED:NO_SUBMIT_CERTIFICATE_REJECTED:%'
        )
    )
"""
_FORECAST_ONLY_NO_VALUE_REFUTATION_GUARD_SQL = "COALESCE(executable_snapshot_id, '') = ''"


def _no_value_refutation_event_types_compatible(
    active_event_type: str, regret_event_type: str
) -> bool:
    active = str(active_event_type or "").strip()
    regret = str(regret_event_type or "").strip()
    # A redecision row is created only after the continuous screen sees current
    # value/rest evidence. Older same-payload no-value receipts may suppress
    # ordinary discovery rows, but must not expire the live redecision itself.
    if active == "EDLI_REDECISION_PENDING":
        return False
    if active in _FORECAST_DECISION_EVENT_TYPES:
        return not regret or regret in _FORECAST_DECISION_EVENT_TYPES
    if active == "DAY0_EXTREME_UPDATED":
        return regret == "DAY0_EXTREME_UPDATED"
    return bool(active and regret and active == regret)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


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
            now = _utc_now()
            if event.event_type in self._CHANNEL_EVENT_TYPES:
                processing_status = "ignored"
                processed_at = now
                last_error = "MARKET_CHANNEL_CACHE_EVENT_NOT_DECISION_TRIGGER"
            else:
                processing_status = "pending"
                processed_at = None
                last_error = None
            self.conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_event_processing (
                    consumer_name, event_id, processing_status, attempt_count,
                    processed_at, last_error, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    self.consumer_name,
                    event.event_id,
                    processing_status,
                    processed_at,
                    last_error,
                    now,
                ),
            )
        return inserted

    def fetch_pending(
        self, *, decision_time: str, limit: int = 100, day0_is_tradeable: bool = True
    ) -> list[OpportunityEvent]:
        """Fetch pending events in deterministic replay/inference order.

        ``day0_is_tradeable`` (default True = production behaviour) controls the
        scope-aware claim tier for test/replay callers. Production live scope
        keeps Day0 tradeable. The tier authority lives in
        ``src.events.event_priority.claim_tier_case_sql`` — one ordering law,
        shared with the emit-priority constants, never a magic number here.

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
        # Scope-aware claim tier (ONE ordering authority, shared with the emit
        # constants). day0_is_tradeable=False omits the DAY0_EXTREME_UPDATED Tier-0
        # clause so shadow-only day0 events fall to Tier 2 — strictly below the
        # tradeable FORECAST_SNAPSHOT_READY Tier 1 (2026-06-11 live anti-starvation).
        # PER-CITY ROUND-ROBIN FAIRNESS (2026-06-11 live throughput incident).
        #
        # THE CATEGORY THIS MAKES UNCONSTRUCTABLE
        # ---------------------------------------
        # A per-cycle decision budget (K events) combined with a STRICTLY
        # freshness-ordered queue (target_date DESC, available_at DESC) is a
        # starvation engine: the few cities whose forecast snapshots refresh with
        # the newest available_at win the first K slots EVERY cycle, so the tail
        # cities (whose available_at is older within the same target window) are
        # never reached before the budget is spent. Measured live 2026-06-11: a
        # 28x city imbalance (Shanghai 309 decisions/h vs Toronto 11/h), and during
        # slow-cadence windows the budget only cleared ~21 of ~50 cities so 18+
        # cities went undecided for hours despite fresh pending FSR.
        #
        # THE STRUCTURAL DECISION
        # -----------------------
        # Freshness is the RIGHT order WITHIN a city, but the WRONG primary order
        # ACROSS cities under a bounded budget. We compute a per-(tier, city)
        # occurrence rank — each city's freshest event is rank 1, its second
        # freshest rank 2, ... — and make that rank the PRIMARY sort key within a
        # tier. The queue then returns "every city's freshest, then every city's
        # second freshest, ..." so a budget of K reaches K DISTINCT cities per
        # cycle and every one of N cities is reached within ceil(N/K) cycles.
        # Decision semantics are UNCHANGED: same events, same tiers, same
        # admissibility, same WITHIN-city freshness order — only the CROSS-city
        # interleaving changes from "drain one city fully" to "one per city, fair".
        #
        # The original SQL implemented the round-robin with ROW_NUMBER() and sorted
        # by json_extract(payload_json, '$.target_date'). On the live world DB that
        # forced SQLite to build temp B-trees and spend reactor time in jsonExtractFunc.
        # Keep SQL to indexed eligibility + bounded overfetch, then do the cheap
        # target/city/tier ranking in Python over the bounded candidate pool.
        base_sql = """
            WITH eligible_processing AS (
              SELECT p.event_id, p.attempt_count
              FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
              WHERE p.consumer_name = ?
                AND p.processing_status = 'pending'
                AND (p.claimed_at IS NULL OR p.claimed_at <= ?)
              UNION ALL
              SELECT p.event_id, p.attempt_count
              FROM opportunity_event_processing p
              WHERE p.consumer_name = ?
                AND p.processing_status = 'processing'
                AND p.claimed_at IS NOT NULL
                AND p.claimed_at <= ?
            ),
            candidates AS (
              SELECT
                e.*,
                p.attempt_count AS _p_attempt_count
              FROM eligible_processing p
              JOIN opportunity_events e
                ON e.event_id = p.event_id
              WHERE e.available_at <= ?
                AND e.received_at <= ?
                AND (e.expires_at IS NULL OR e.expires_at > ?)
                AND e.event_type NOT IN (
                      'BEST_BID_ASK_CHANGED',
                      'BOOK_SNAPSHOT',
                      'NEW_MARKET_DISCOVERED'
                )
            )
            SELECT
              -- Project EXACTLY the opportunity_events columns (in table order) so
              -- _event_from_row receives only its expected keys — the helper
              -- column (_p_attempt_count) drives Python ordering but must NOT reach
              -- OpportunityEvent(**row).
              c.event_id, c.event_type, c.entity_key, c.source,
              c.observed_at, c.available_at, c.received_at,
              c.causal_snapshot_id, c.payload_hash, c.idempotency_key,
              c.priority, c.expires_at, c.payload_json, c.schema_version,
              c.created_at,
              c._p_attempt_count
            FROM candidates c
            WHERE {tier_predicate}
            LIMIT ?
            """
        tier_queries: tuple[tuple[str, tuple[object, ...]], ...] = (
            (
                "("
                "c.event_type = 'EDLI_REDECISION_PENDING'"
                " OR (c.event_type = 'FORECAST_SNAPSHOT_READY' AND c.source LIKE ?)"
                + (" OR c.event_type = 'DAY0_EXTREME_UPDATED'" if day0_is_tradeable else "")
                + ")",
                (f"{ESCALATION_CROSS_SOURCE_PREFIX}%",),
            ),
            (
                "("
                "c.event_type = 'FORECAST_SNAPSHOT_READY'"
                " AND c.source NOT LIKE ?"
                " AND json_extract(c.payload_json, '$.coverage_completeness_status') = 'COMPLETE'"
                " AND json_extract(c.payload_json, '$.coverage_readiness_status') = 'LIVE_ELIGIBLE'"
                ")",
                (f"{ESCALATION_CROSS_SOURCE_PREFIX}%",),
            ),
            (
                "("
                "c.event_type NOT IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING'"
                + (", 'DAY0_EXTREME_UPDATED'" if day0_is_tradeable else "")
                + ")"
                " OR ("
                "c.event_type = 'FORECAST_SNAPSHOT_READY'"
                " AND c.source NOT LIKE ?"
                " AND ("
                "json_extract(c.payload_json, '$.coverage_completeness_status') != 'COMPLETE'"
                " OR json_extract(c.payload_json, '$.coverage_readiness_status') != 'LIVE_ELIGIBLE'"
                " OR json_extract(c.payload_json, '$.coverage_completeness_status') IS NULL"
                " OR json_extract(c.payload_json, '$.coverage_readiness_status') IS NULL"
                ")"
                ")"
                ")",
                (f"{ESCALATION_CROSS_SOURCE_PREFIX}%",),
            ),
        )
        out: list[OpportunityEvent] = []
        pool_limits = (
            max(limit * 64, limit + 2_000),
            max(limit * 256, limit + 10_000),
        )
        common_params = (
            self.consumer_name,
            parsed_decision_time.isoformat(),
            self.consumer_name,
            stale_processing_before,
            parsed_decision_time.isoformat(),
            parsed_decision_time.isoformat(),
            parsed_decision_time.isoformat(),
        )
        for tier_predicate, tier_params in tier_queries:
            tier_admissible: list[OpportunityEvent] = []
            for pool_limit in pool_limits:
                rows = self.conn.execute(
                    base_sql.format(tier_predicate=tier_predicate),
                    common_params + tier_params + (pool_limit,),
                ).fetchall()
                ranked = _rank_pending_rows_python(
                    rows,
                    day0_is_tradeable=day0_is_tradeable,
                )
                events = [event for event, _attempt_count in ranked]
                tier_admissible = [e for e in events if self._is_timely(e, parsed_decision_time)]
                if len(tier_admissible) >= limit - len(out) or len(rows) < pool_limit:
                    break
            out.extend(tier_admissible[: max(0, limit - len(out))])
            if len(out) >= limit:
                return out[:limit]
            # If the tier remained at the hard overfetch ceiling, do not claim lower-tier rows
            # ahead of a not-yet-exhausted higher tier. This preserves fail-closed money priority
            # under pathological backlogs instead of letting lower tiers jump the queue.
            if rows and len(rows) >= pool_limits[-1]:
                return out[:limit]
        return out[:limit]

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

        VENUE-CLOSE (POST_TRADING) SWEEP (#126, 2026-06-15): also archives any family
        whose Polymarket venue has closed (POST_TRADING) at ``decision_time`` but whose
        local day has NOT yet ended — the ``[venue_close, local_day_end)`` window. The
        venue closes at the F1 12:00-UTC anchor of target_date; at that moment the book
        is gone (no fresh executable snapshot, no receipt possible), yet the local-day
        predicate alone reports the family TIMELY and keeps it ``'pending'`` forever.
        Live root-cause 2026-06-16: 132 families stuck ``'pending'`` (target_date
        2026-06-15, venue closed 2026-06-15T12:00Z at ~02:00Z next day) clogged
        ``_edli_pending_entity_keys``, EDLI re-decision emitted 0 new families every
        cycle (``edli_redecision: enqueued=0 batch=60 skipped_pending=132``), harvest
        lane dark, zero orders.

        The fix reuses the EXACT authority the reactor's ``_venue_market_closed_horizon``
        (horizon b) uses: ``market_phase_for_decision`` with the F1 12:00-UTC geometric
        close anchor (``_f1_fallback_end_utc``). No venue HTTP probe, no new clock.
        Fail-closed: city/tz/date unresolvable → KEEP active (same contract as the
        reactor). Only POST_TRADING/RESOLVED archives; PRE_SETTLEMENT/SETTLEMENT/open →
        kept.

        The candidate band is widened from the old local-day-only floor to also capture
        rows whose ``target_date <= venue_close_ceiling`` (the latest target_date whose
        F1-12:00-UTC close COULD have fired at ``decision_time``). This is the date
        portion of ``(decision_time - 12h)`` in UTC. Rows in the new venue-close band
        that are NOT actually POST_TRADING are filtered out in the Python loop (fail-
        closed); the SQL band is the NECESSARY condition, Python is the SUFFICIENT gate.

        OCEANIA-FRONTIER cheap pre-filter: only rows whose ``target_date`` is at or
        after ``frontier_local_date - 1`` (the current local date in the
        globally-earliest-rolling timezone, Oceania UTC+13/+12, minus one day for the
        local-day-still-open margin) can POSSIBLY still be active in any city.
        Everything strictly older is unconditionally past in every timezone on Earth,
        so we archive those by the cheap string bound WITHOUT the per-city Python tz
        round-trip, and only run the expensive per-city check on the frontier band.
        This bounds the expensive work to the handful of recent target_dates.

        SCOPE (2026-06-15): sweeps BOTH FORECAST_SNAPSHOT_READY and
        DAY0_EXTREME_UPDATED — day0 events DO carry per-city ``$.city`` + ``$.target_date``
        (verified live), so the identical per-city-tz strictly-past predicate applies.
        Day0 was previously excluded by a wrong assumption ("day0 carries no per-city
        target"); that left ~900 past-local-day day0 rows (06-13/06-14) sitting at the
        Tier-0 claim priority on SETTLED markets, claimed ahead of every tradeable
        FORECAST_SNAPSHOT_READY (spine) family and starving the spine lane to zero
        decisions. Market-channel events (BEST_BID_ASK_CHANGED/BOOK_SNAPSHOT/
        NEW_MARKET_DISCOVERED) are token-keyed, carry no per-city forecast target, and
        remain out of scope here (their supersession/ignore sweeps own them).

        FAIL-CLOSED: a row whose city/target_date is missing or whose timezone is
        unresolvable is KEPT ACTIVE (never archived) — archiving an active row would
        silently drop a real candidate.

        IDEMPOTENT + budget-safe: only ``pending``/``processing`` rows are touched and
        only those proven strictly-past or venue-closed; a re-run at the same decision
        time is a no-op.  ``batch_limit`` bounds the rows examined per call so a one-
        time 1.7M backlog drains across cycles instead of in one giant transaction.

        Returns the number of processing rows transitioned to ``expired``.
        """

        self._require_world_event_tables()
        decision_time_utc = _parse_utc(decision_time)

        # Oceania-frontier cheap bound: the most-advanced local calendar date on Earth
        # at decision_time, minus one day of margin. Any target_date strictly before
        # this is past in EVERY timezone and needs no per-city check.
        frontier_floor = _oceania_frontier_target_floor(decision_time_utc)
        # DAY0 uses a TODAY-INCLUSIVE frontier (2026-06-15). The -1 day margin exists for
        # FSR, whose target can be a future TRADING day still ambiguous across timezones.
        # A DAY0_EXTREME_UPDATED is a SAME-DAY realized-observation signal — it never refers
        # to a future trading day, so the margin only strands settled past-local-day day0
        # (e.g. yesterday's) in the FRONTIER BAND, where they pile up at the Tier-0 claim
        # priority and starve tradeable FORECAST_SNAPSHOT_READY off the bounded per-cycle
        # claim. Widen the day0 candidate band to ``< frontier_floor + 2`` (= the Oceania
        # local date + 1, i.e. today and everything before); the exact per-city
        # _strictly_past_in_tz check below still KEEPS any day0 whose local day is still
        # open, so today's live day0 is never archived — only genuinely-settled ones go.
        try:
            day0_floor = (date.fromisoformat(frontier_floor) + timedelta(days=2)).isoformat()
        except ValueError:
            day0_floor = frontier_floor

        # VENUE-CLOSE BAND (#126, 2026-06-15): The F1 12:00-UTC close for target_date T
        # fires at T+12h in UTC. Any target_date T whose close has already fired satisfies
        # decision_time >= T+12h  ⟺  T <= decision_time - 12h (UTC date part).
        # This ceiling captures the widest target_date that COULD be POST_TRADING now;
        # rows in this band that are not actually POST_TRADING are filtered in Python.
        venue_close_ceiling = _venue_close_target_ceiling(decision_time_utc)

        candidate_rows = self.conn.execute(
            """
            SELECT e.event_id,
                   json_extract(e.payload_json, '$.city')        AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date
            FROM opportunity_events e INDEXED BY idx_opportunity_events_fsr_target_date
            JOIN opportunity_event_processing p
              ON p.event_id = e.event_id
             AND p.consumer_name = ?
            WHERE e.event_type IN ('FORECAST_SNAPSHOT_READY', 'DAY0_EXTREME_UPDATED')
              AND p.processing_status IN ('pending', 'processing')
              AND json_extract(e.payload_json, '$.target_date') IS NOT NULL
              AND (
                    (e.event_type = 'FORECAST_SNAPSHOT_READY'
                       AND (   json_extract(e.payload_json, '$.target_date') < ?
                            OR json_extract(e.payload_json, '$.target_date') <= ?))
                 OR (e.event_type = 'DAY0_EXTREME_UPDATED'
                       AND (   json_extract(e.payload_json, '$.target_date') < ?
                            OR json_extract(e.payload_json, '$.target_date') <= ?))
              )
            ORDER BY json_extract(e.payload_json, '$.target_date') ASC
            LIMIT ?
            """,
            (self.consumer_name, frontier_floor, venue_close_ceiling,
             day0_floor, venue_close_ceiling, batch_limit),
        ).fetchall()

        expired_ids: list[str] = []
        for row in candidate_rows:
            event_id = row[0]
            city = row[1]
            target_date = row[2]
            if self._strictly_past_in_tz(city, target_date, decision_time_utc) or \
               self._venue_closed_in_phase(city, target_date, decision_time_utc):
                expired_ids.append(event_id)

        now = _utc_now()
        _CHUNK = 500
        for chunk_start in range(0, len(expired_ids), _CHUNK):
            chunk = expired_ids[chunk_start : chunk_start + _CHUNK]
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
                (decision_time_utc.isoformat(), now, self.consumer_name, *chunk),
            )
        return len(expired_ids)

    def archive_orphan_processing_rows(self, *, batch_limit: int = 5_000) -> int:
        """Expire active processing rows whose immutable event row is missing.

        ``opportunity_events`` is the append-only provenance row. A mutable
        ``opportunity_event_processing`` row without that parent can never be
        claimed, re-decided, or converted into an order because every money-path
        reader joins back to the event payload. Leaving such rows in
        ``pending``/``processing`` pollutes active working-set counts and keeps
        maintenance queries scanning dead IDs. Mark only the mutable row
        ``expired``; do not delete anything, so the anomaly remains auditable.
        """

        self._require_world_event_tables()
        rows = self.conn.execute(
            """
            SELECT p.event_id
              FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
              LEFT JOIN opportunity_events e
                ON e.event_id = p.event_id
             WHERE p.consumer_name = ?
               AND p.processing_status IN ('pending', 'processing')
               AND e.event_id IS NULL
             ORDER BY p.updated_at ASC, p.event_id ASC
             LIMIT ?
            """,
            (self.consumer_name, batch_limit),
        ).fetchall()
        event_ids = [str(row[0]) for row in rows]
        if not event_ids:
            return 0

        now = _utc_now()
        _CHUNK = 500
        for chunk_start in range(0, len(event_ids), _CHUNK):
            chunk = event_ids[chunk_start : chunk_start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            self.conn.execute(
                f"""
                UPDATE opportunity_event_processing
                   SET processing_status = 'expired',
                       processed_at = ?,
                       last_error = 'ORPHAN_EVENT_ROW_MISSING',
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id IN ({placeholders})
                   AND processing_status IN ('pending', 'processing')
                """,
                (now, now, self.consumer_name, *chunk),
            )
        return len(event_ids)

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
        # timestamps for distinct payloads. Keep this as per-key indexed probes, not
        # one CTE/GROUP BY over the active backlog: the live table can hold millions
        # of channel rows, and a single expression-join can pin the reactor worker.
        candidate_keys = {
            (str(row[1]), str(row[2]))
            for row in candidate_rows
            if row[1] is not None and row[2] is not None
        }
        keeper_ids: set[str] = set()
        for event_type, token_id in candidate_keys:
            max_row = self.conn.execute(
                """
                SELECT e.available_at
                  FROM opportunity_events e INDEXED BY idx_opportunity_events_channel_token
                  JOIN opportunity_event_processing p
                    ON p.event_id = e.event_id
                   AND p.consumer_name = ?
                 WHERE e.event_type = ?
                   AND json_extract(e.payload_json, '$.token_id') = ?
                   AND p.processing_status IN ('pending', 'processing')
                 ORDER BY e.available_at DESC
                 LIMIT 1
                """,
                (self.consumer_name, event_type, token_id),
            ).fetchone()
            if max_row is None:
                continue
            max_available_at = str(max_row[0])
            keeper_rows = self.conn.execute(
                """
                SELECT e.event_id
                  FROM opportunity_events e INDEXED BY idx_opportunity_events_channel_token
                  JOIN opportunity_event_processing p
                    ON p.event_id = e.event_id
                   AND p.consumer_name = ?
                 WHERE e.event_type = ?
                   AND json_extract(e.payload_json, '$.token_id') = ?
                   AND e.available_at = ?
                   AND p.processing_status IN ('pending', 'processing')
                """,
                (self.consumer_name, event_type, token_id, max_available_at),
            ).fetchall()
            keeper_ids.update(str(row[0]) for row in keeper_rows)

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

    def archive_superseded_day0_events(self, *, batch_limit: int = 5_000) -> int:
        """Sweep superseded per-family DAY0_EXTREME_UPDATED events to terminal ``'expired'``.

        Day0 companion to ``archive_superseded_channel_events`` (2026-06-15). A
        ``DAY0_EXTREME_UPDATED`` event is a realized-extreme observation update for a
        forecast family ``(city, target_date, metric)``. The actionable value is the
        absorbing running extreme, not the latest publication clock: high keeps the
        maximum rounded value seen for the local day; low keeps the minimum. A later
        API window or daemon restart must never make a local-day high/low regress
        (Chengdu 2026-06-17: 25C crossing followed by a shorter-window 24C event).
        Day0 events were in NEITHER the expiry nor the channel-supersession sweep, so
        stale duplicates piled up (measured
        2026-06-15: 1972 pending day0 rows across only 152 families, ~13/family). Under
        scope ``forecast_plus_day0`` day0 claims at Tier 0, so this stale pileup is
        claimed ahead of — and starves — the tradeable FORECAST_SNAPSHOT_READY (spine)
        lane every cycle.

        INVARIANT (superseded-keep-latest): for each ``(city, target_date, metric)``
        family in the active working set (``pending``/``processing`` only), keep the
        row(s) carrying the absorbing extreme across the full persisted family:
        ``MAX(rounded_value)`` for high and ``MIN(rounded_value)`` for low. Day0 carries
        NO ``token_id``, so the family tuple is the supersession key (the token-keyed
        channel sweep does not apply). Ties at the absorbing extreme are all kept
        (never archive on a duplicate-value ambiguity).

        Past-LOCAL-DAY day0 events are removed separately by
        ``archive_expired_candidates`` (which now covers day0); this sweep dedups the
        still-active families so only ONE latest day0 per family remains claimable.

        FAIL-CLOSED: a row whose city/target_date/metric is missing is KEPT ACTIVE.
        APPEND-ONLY PROVENANCE: only ``opportunity_event_processing.processing_status``
        is mutated; the immutable ``opportunity_events`` row is never deleted.
        IDEMPOTENT + batch-bounded (mirrors the channel sweep exactly).

        Returns the number of processing rows transitioned to ``'expired'``.
        """

        self._require_world_event_tables()

        # Step 1: oldest active day0 rows with a parseable (city, target_date, metric)
        # family key — the only unscoped scan, batch-limited.
        candidate_rows = self.conn.execute(
            """
            SELECT e.event_id,
                   json_extract(e.payload_json, '$.city')        AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date,
                   json_extract(e.payload_json, '$.metric')      AS metric
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            JOIN opportunity_events e
              ON e.event_id = p.event_id
            WHERE p.consumer_name = ?
              AND p.processing_status IN ('pending', 'processing')
              AND e.event_type = 'DAY0_EXTREME_UPDATED'
              AND json_extract(e.payload_json, '$.city') IS NOT NULL
              AND json_extract(e.payload_json, '$.target_date') IS NOT NULL
              AND json_extract(e.payload_json, '$.metric') IS NOT NULL
            ORDER BY e.available_at ASC
            LIMIT ?
            """,
            (self.consumer_name, batch_limit),
        ).fetchall()

        if not candidate_rows:
            return 0

        # Step 2: per family key in the candidate batch, find the absorbing keeper(s).
        # The keeper may be outside the batch or already terminal from the older
        # latest-by-clock rule; in that case active regressed rows are still expired so
        # the queue cannot keep reprocessing a lower high / higher low as "latest".
        candidate_keys = {
            (str(row[1]), str(row[2]), str(row[3]))
            for row in candidate_rows
            if row[1] is not None and row[2] is not None and row[3] is not None
        }
        keeper_ids: set[str] = set()
        for city, target_date, metric in candidate_keys:
            agg = "MAX" if metric == "high" else "MIN"
            value_expr = (
                "CAST(COALESCE("
                "json_extract(e.payload_json, '$.rounded_value'), "
                "json_extract(e.payload_json, '$.high_so_far'), "
                "json_extract(e.payload_json, '$.low_so_far')"
                ") AS REAL)"
            )
            extreme_row = self.conn.execute(
                """
                SELECT {agg}({value_expr})
                  FROM opportunity_events e INDEXED BY idx_opportunity_events_fsr_target_date
                  JOIN opportunity_event_processing p
                    ON p.event_id = e.event_id
                   AND p.consumer_name = ?
                 WHERE e.event_type = 'DAY0_EXTREME_UPDATED'
                   AND json_extract(e.payload_json, '$.target_date') = ?
                   AND json_extract(e.payload_json, '$.city') = ?
                   AND json_extract(e.payload_json, '$.metric') = ?
                   AND {value_expr} IS NOT NULL
                """.format(agg=agg, value_expr=value_expr),
                (self.consumer_name, target_date, city, metric),
            ).fetchone()
            if extreme_row is None or extreme_row[0] is None:
                continue
            extreme_value = float(extreme_row[0])
            keeper_rows = self.conn.execute(
                """
                SELECT e.event_id
                  FROM opportunity_events e INDEXED BY idx_opportunity_events_fsr_target_date
                  JOIN opportunity_event_processing p
                    ON p.event_id = e.event_id
                   AND p.consumer_name = ?
                 WHERE e.event_type = 'DAY0_EXTREME_UPDATED'
                   AND json_extract(e.payload_json, '$.target_date') = ?
                   AND json_extract(e.payload_json, '$.city') = ?
                   AND json_extract(e.payload_json, '$.metric') = ?
                   AND CAST(COALESCE(
                         json_extract(e.payload_json, '$.rounded_value'),
                         json_extract(e.payload_json, '$.high_so_far'),
                         json_extract(e.payload_json, '$.low_so_far')
                       ) AS REAL) = ?
                   AND p.processing_status IN ('pending', 'processing')
                """,
                (self.consumer_name, target_date, city, metric, extreme_value),
            ).fetchall()
            keeper_ids.update(str(row[0]) for row in keeper_rows)

        superseded_ids = [str(row[0]) for row in candidate_rows if str(row[0]) not in keeper_ids]

        if not superseded_ids:
            return 0

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

    def ignore_channel_cache_events(self, *, batch_limit: int = 5_000) -> int:
        """Move channel cache-hydration events out of the submit reactor working set.

        Market-channel rows are authoritative inputs for quote cache / feasibility
        evidence, but they are not direct decision events. Letting their latest
        per-token rows remain ``pending`` makes the reactor spend live proof budget
        on deterministic ``NO_DIRECT_STALE_TRADE`` rejects. Preserve the immutable
        event rows and mark only the mutable processing rows ``ignored``.
        """

        self._require_world_event_tables()
        type_placeholders = ",".join("?" * len(self._CHANNEL_EVENT_TYPES))
        rows = self.conn.execute(
            f"""
            SELECT e.event_id
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
        event_ids = [str(row[0]) for row in rows]
        if not event_ids:
            return 0

        now = _utc_now()
        _CHUNK = 500
        for chunk_start in range(0, len(event_ids), _CHUNK):
            chunk = event_ids[chunk_start : chunk_start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            self.conn.execute(
                f"""
                UPDATE opportunity_event_processing
                   SET processing_status = 'ignored',
                       processed_at = ?,
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id IN ({placeholders})
                   AND processing_status IN ('pending', 'processing')
                """,
                (now, now, self.consumer_name, *chunk),
            )
        return len(event_ids)

    def archive_superseded_forecast_snapshot_events(
        self, *, batch_limit: int = 5_000
    ) -> int:
        """Sweep superseded FSR redecision rows to terminal ``'expired'`` status.

        Continuous redecision intentionally emits fresh FSR-equivalent events for the
        same weather family across cycles. Only the newest active row for a
        ``(city, target_date, metric)`` family can be useful; older pending/processing
        rows force the reactor to re-evaluate stale source runs and can keep the queue
        focused on old forecast snapshots. ``entity_key`` includes the source run, so
        using it as the supersession key leaves previous runs active forever.

        The immutable ``opportunity_events`` row remains append-only. This method only
        expires mutable ``opportunity_event_processing`` rows, preserving provenance
        while reducing the active working set.
        """

        self._require_world_event_tables()

        candidate_rows = self.conn.execute(
            """
            SELECT
                e.event_id,
                e.event_type,
                e.entity_key,
                json_extract(e.payload_json, '$.city') AS city,
                json_extract(e.payload_json, '$.target_date') AS target_date,
                json_extract(e.payload_json, '$.metric') AS metric
              FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
              JOIN opportunity_events e
                ON e.event_id = p.event_id
             WHERE p.consumer_name = ?
               AND p.processing_status IN ('pending', 'processing')
               AND e.event_type IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING')
               AND e.entity_key IS NOT NULL
             ORDER BY e.available_at ASC, e.received_at ASC, e.event_id ASC
             LIMIT ?
            """,
            (self.consumer_name, batch_limit),
        ).fetchall()
        if not candidate_rows:
            return 0

        _FsrPruneKey = tuple[str, str, str, str, str] | tuple[str, str, str]

        def _prune_key(row: sqlite3.Row | tuple) -> _FsrPruneKey:
            event_type = str(row[1] or "").strip()
            city = str(row[3] or "").strip()
            target_date = str(row[4] or "").strip()
            metric = str(row[5] or "").strip()
            if city and target_date and metric:
                return ("family", event_type, city, target_date, metric)
            return ("entity", event_type, str(row[2] or ""))

        candidate_keys = {
            key for row in candidate_rows if (key := _prune_key(row))[-1]
        }
        keeper_ids: set[str] = set()
        for prune_key in candidate_keys:
            if prune_key[0] == "family":
                _, event_type, city, target_date, metric = prune_key
                key_predicate = (
                    "json_extract(e.payload_json, '$.city') = ? "
                    "AND json_extract(e.payload_json, '$.target_date') = ? "
                    "AND json_extract(e.payload_json, '$.metric') = ?"
                )
                key_params = (city, target_date, metric)
            else:
                _, event_type, entity_key = prune_key
                key_predicate = "e.entity_key = ?"
                key_params = (entity_key,)
            keeper_row = self.conn.execute(
                f"""
                SELECT e.event_id
                  FROM opportunity_events e
                  JOIN opportunity_event_processing p
                    ON p.event_id = e.event_id
                   AND p.consumer_name = ?
                 WHERE e.event_type = ?
                   AND {key_predicate}
                   AND p.processing_status IN ('pending', 'processing')
                 ORDER BY
                   CASE
                     WHEN json_extract(e.payload_json, '$.coverage_completeness_status') = 'COMPLETE'
                      AND json_extract(e.payload_json, '$.coverage_readiness_status') = 'LIVE_ELIGIBLE'
                      AND COALESCE(
                            json_extract(e.payload_json, '$.member_count'),
                            json_extract(e.payload_json, '$.observed_members'),
                            json_extract(e.payload_json, '$.sr_observed_members')
                          ) IS NOT NULL
                      AND COALESCE(
                            json_extract(e.payload_json, '$.expected_members'),
                            json_extract(e.payload_json, '$.sr_expected_members')
                          ) IS NOT NULL
                      AND CAST(COALESCE(
                            json_extract(e.payload_json, '$.expected_members'),
                            json_extract(e.payload_json, '$.sr_expected_members')
                          ) AS INTEGER) > 0
                      AND CAST(COALESCE(
                            json_extract(e.payload_json, '$.member_count'),
                            json_extract(e.payload_json, '$.observed_members'),
                            json_extract(e.payload_json, '$.sr_observed_members')
                          ) AS INTEGER) >= CAST(COALESCE(
                            json_extract(e.payload_json, '$.expected_members'),
                            json_extract(e.payload_json, '$.sr_expected_members')
                          ) AS INTEGER)
                     THEN 0
                     WHEN json_extract(e.payload_json, '$.coverage_completeness_status') = 'COMPLETE'
                      AND json_extract(e.payload_json, '$.coverage_readiness_status') = 'LIVE_ELIGIBLE'
                     THEN 1
                     ELSE 2
                   END ASC,
                   e.available_at DESC,
                   e.received_at DESC,
                   e.event_id DESC
                LIMIT 1
                """,
                (self.consumer_name, event_type, *key_params),
            ).fetchone()
            if keeper_row is not None:
                keeper_ids.add(str(keeper_row[0]))

        superseded_ids = [str(row[0]) for row in candidate_rows if str(row[0]) not in keeper_ids]
        if not superseded_ids:
            return 0

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

    def archive_invalid_forecast_snapshot_events(
        self, *, batch_limit: int = 5_000
    ) -> int:
        """Terminalize live-eligible FSR/redecision rows with impossible carrier counts.

        ``FORECAST_SNAPSHOT_READY`` and ``EDLI_REDECISION_PENDING`` are money-path
        carriers. If a row advertises ``COMPLETE``/``LIVE_ELIGIBLE`` coverage while
        its observed carrier count is missing or smaller than its expected carrier
        count, the row is not a conservative no-trade signal; it is a structurally
        invalid live carrier. Leaving it ``pending`` or ``processing`` lets legacy
        producer bugs re-enter the decision path after restart.

        The immutable event remains append-only. Only the mutable processing row is
        expired, with ``last_error`` explaining why it was removed from the active
        working set.
        """

        self._require_world_event_tables()
        rows = self.conn.execute(
            """
            SELECT e.event_id
              FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
              JOIN opportunity_events e
                ON e.event_id = p.event_id
             WHERE p.consumer_name = ?
               AND p.processing_status IN ('pending', 'processing')
               AND e.event_type IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING')
               AND json_extract(e.payload_json, '$.coverage_completeness_status') = 'COMPLETE'
               AND json_extract(e.payload_json, '$.coverage_readiness_status') = 'LIVE_ELIGIBLE'
               AND COALESCE(
                     json_extract(e.payload_json, '$.expected_members'),
                     json_extract(e.payload_json, '$.sr_expected_members')
                   ) IS NOT NULL
               AND CAST(COALESCE(
                     json_extract(e.payload_json, '$.expected_members'),
                     json_extract(e.payload_json, '$.sr_expected_members')
                   ) AS INTEGER) > 0
               AND (
                    COALESCE(
                      json_extract(e.payload_json, '$.member_count'),
                      json_extract(e.payload_json, '$.observed_members'),
                      json_extract(e.payload_json, '$.sr_observed_members')
                    ) IS NULL
                 OR CAST(COALESCE(
                      json_extract(e.payload_json, '$.member_count'),
                      json_extract(e.payload_json, '$.observed_members'),
                      json_extract(e.payload_json, '$.sr_observed_members')
                    ) AS INTEGER) < CAST(COALESCE(
                      json_extract(e.payload_json, '$.expected_members'),
                      json_extract(e.payload_json, '$.sr_expected_members')
                    ) AS INTEGER)
               )
             ORDER BY e.available_at ASC, e.received_at ASC, e.event_id ASC
             LIMIT ?
            """,
            (self.consumer_name, batch_limit),
        ).fetchall()
        event_ids = [str(row[0]) for row in rows]
        if not event_ids:
            return 0

        now = _utc_now()
        _CHUNK = 500
        for chunk_start in range(0, len(event_ids), _CHUNK):
            chunk = event_ids[chunk_start : chunk_start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            self.conn.execute(
                f"""
                UPDATE opportunity_event_processing
                   SET processing_status = 'expired',
                       processed_at = ?,
                       last_error = 'INVALID_FORECAST_SNAPSHOT_CARRIER_COUNTS',
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id IN ({placeholders})
                   AND processing_status IN ('pending', 'processing')
                """,
                (now, now, self.consumer_name, *chunk),
            )
        return len(event_ids)

    def archive_recent_no_value_refuted_events(
        self,
        *,
        decision_time: str,
        batch_limit: int = 5_000,
    ) -> int:
        """Expire already-queued events refuted by same-evidence terminal no-trade.

        Emit-time suppression stops newly minted FSR/Day0 rows after a full
        economics no-value decision. It cannot clean rows that were already
        queued before the no-trade receipt was written. Those stale rows keep
        burning bounded reactor proof budget on the same evidence. This sweep
        closes that gap by mutating only ``opportunity_event_processing`` while
        preserving the append-only ``opportunity_events`` provenance.

        Same-evidence is deliberately narrow: same city/target/metric plus
        matching payload hash or causal snapshot id. Ordinary FSR rows can be
        refuted by prior forecast/redecision no-value receipts. Active
        ``EDLI_REDECISION_PENDING`` rows are not archive-refuted here because
        the continuous screen already observed current value/rest evidence; the
        reactor must decide them on the fresh path. Day0 remains a separate
        observation lane and only Day0 no-value can refute Day0. Unlike
        emit-time suppression, this is not limited to the short cooldown window:
        if an old row is still queued and the same evidence was terminally
        refuted after that evidence became available, the row is stale.
        """

        self._require_world_event_tables()
        if not _table_exists(self.conn, "no_trade_regret_events"):
            return 0

        parsed_decision_time = _parse_utc(decision_time)
        event_types = sorted(_NO_VALUE_REFUTATION_EVENT_TYPES)
        type_placeholders = ",".join("?" * len(event_types))
        candidate_rows = self.conn.execute(
            f"""
            SELECT
                e.event_id,
                e.event_type,
                json_extract(e.payload_json, '$.city') AS city,
                json_extract(e.payload_json, '$.target_date') AS target_date,
                json_extract(e.payload_json, '$.metric') AS metric,
                e.causal_snapshot_id,
                e.payload_hash,
                e.available_at,
                e.created_at
              FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
              JOIN opportunity_events e
                ON e.event_id = p.event_id
             WHERE p.consumer_name = ?
               AND p.processing_status IN ('pending', 'processing')
               AND e.event_type IN ({type_placeholders})
               AND json_extract(e.payload_json, '$.city') IS NOT NULL
               AND json_extract(e.payload_json, '$.target_date') IS NOT NULL
               AND json_extract(e.payload_json, '$.metric') IS NOT NULL
             ORDER BY e.available_at ASC, e.received_at ASC, e.event_id ASC
             LIMIT ?
            """,
            (
                self.consumer_name,
                *event_types,
                batch_limit,
            ),
        ).fetchall()
        if not candidate_rows:
            return 0

        evidence_floor = min(
            str(row[7] or row[8] or "") for row in candidate_rows if row[7] or row[8]
        )
        if not evidence_floor:
            return 0

        regret_rows = self.conn.execute(
            f"""
            SELECT n.city,
                   n.target_date,
                   n.metric,
                   n.event_id,
                   n.rejection_reason,
                   n.created_at,
                   n.causal_snapshot_id AS regret_causal_snapshot_id,
                   e.causal_snapshot_id AS regret_event_causal_snapshot_id,
                   e.payload_hash AS regret_payload_hash,
                   e.event_type AS regret_event_type
             FROM no_trade_regret_events n
              LEFT JOIN opportunity_events e
                ON e.event_id = n.event_id
             WHERE n.created_at >= ?
               AND n.created_at <= ?
               AND ({_TERMINAL_NO_VALUE_REFUTATION_SQL})
               AND ({_FORECAST_ONLY_NO_VALUE_REFUTATION_GUARD_SQL})
             ORDER BY n.created_at DESC
            """,
            (evidence_floor, parsed_decision_time.isoformat()),
        ).fetchall()
        if not regret_rows:
            return 0

        regrets_by_family: dict[tuple[str, str, str], list[sqlite3.Row | tuple]] = {}
        for regret in regret_rows:
            key = (
                str(regret[0] or "").strip(),
                str(regret[1] or "").strip(),
                str(regret[2] or "").strip(),
            )
            if all(key):
                regrets_by_family.setdefault(key, []).append(regret)

        refuted: list[tuple[str, str]] = []
        for row in candidate_rows:
            active_event_id = str(row[0] or "").strip()
            active_event_type = str(row[1] or "").strip()
            city = str(row[2] or "").strip()
            target_date = str(row[3] or "").strip()
            metric = str(row[4] or "").strip()
            causal_snapshot_id = str(row[5] or "").strip()
            payload_hash = str(row[6] or "").strip()
            if not (active_event_id and city and target_date and metric):
                continue
            for regret in regrets_by_family.get((city, target_date, metric), []):
                source_event_id = str(regret[3] or "").strip()
                reason = str(regret[4] or "")
                prior_causal = str(regret[7] or regret[6] or "").strip()
                prior_payload_hash = str(regret[8] or "").strip()
                regret_event_type = str(regret[9] or "").strip()
                if not _no_value_refutation_event_types_compatible(
                    active_event_type, regret_event_type
                ):
                    continue
                evidence_match = ""
                if payload_hash and prior_payload_hash and payload_hash == prior_payload_hash:
                    evidence_match = "payload_hash"
                elif causal_snapshot_id and prior_causal and causal_snapshot_id == prior_causal:
                    evidence_match = "causal_snapshot_id"
                if not evidence_match:
                    continue
                refuted.append(
                    (
                        active_event_id,
                        (
                            "RECENT_NO_VALUE_REFUTATION:"
                            f"{evidence_match}:{source_event_id}:{reason}"
                        )[:500],
                    )
                )
                break

        if not refuted:
            return 0

        now = _utc_now()
        for event_id, last_error in refuted:
            self.conn.execute(
                """
                UPDATE opportunity_event_processing
                   SET processing_status = 'expired',
                       processed_at = ?,
                       updated_at = ?,
                       last_error = ?
                 WHERE consumer_name = ?
                   AND event_id = ?
                   AND processing_status IN ('pending', 'processing')
                """,
                (now, now, last_error, self.consumer_name, event_id),
            )
        return len(refuted)

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

    @staticmethod
    def _venue_closed_in_phase(
        city: str | None, target_date: str | None, decision_time_utc: datetime
    ) -> bool:
        """True iff the Polymarket venue market for ``city``/``target_date`` has entered
        POST_TRADING (or RESOLVED) at ``decision_time`` — using the EXACT authority the
        reactor's ``_venue_market_closed_horizon`` (horizon b) uses.

        Authority: ``market_phase_for_decision`` with the F1 12:00-UTC geometric close
        anchor (``_f1_fallback_end_utc(target_local_date)``).  No venue HTTP probe, no
        new clock, no external state — purely city-tz + target_date + decision_time.

        FAIL-CLOSED: missing city/target_date, unresolvable city config/tz, or ANY
        exception → returns False (NOT closed) so the row is KEPT active.  Mislabeling
        an open family as closed would silently drop a live candidate, which is the
        unrecoverable failure mode.

        Only POST_TRADING and RESOLVED return True; every other phase (PRE_TRADING,
        PRE_SETTLEMENT_DAY, SETTLEMENT_DAY) returns False.

        #126, 2026-06-15: closes the ``[venue_close, local_day_end)`` gap where the
        local-day predicate alone (``_strictly_past_in_tz``) reported a POST_TRADING
        family as still TIMELY and left it ``'pending'`` forever (132 families confirmed
        live; root-cause docs/evidence/qkernel_rebuild/fix_venue_close_sweep_2026-06-15.md).
        """
        if not city or not target_date:
            return False
        try:
            from datetime import date as _date_cls

            from src.config import runtime_cities_by_name
            from src.strategy.market_phase import (
                MarketPhase,
                _f1_fallback_end_utc,
                market_phase_for_decision,
            )

            city_config = runtime_cities_by_name().get(city)
            tz = getattr(city_config, "timezone", None) if city_config is not None else None
            if not tz:
                return False
            target_local_date = _date_cls.fromisoformat(str(target_date))
            phase = market_phase_for_decision(
                target_local_date=target_local_date,
                city_timezone=tz,
                decision_time_utc=decision_time_utc,
                polymarket_start_utc=None,
                polymarket_end_utc=_f1_fallback_end_utc(target_local_date),
            )
        except Exception:
            # Fail-closed: any unresolvable input keeps the row active.
            return False
        return phase in (MarketPhase.POST_TRADING, MarketPhase.RESOLVED)

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

        EDLI_REDECISION_PENDING (continuous re-decision resurrection 2026-06-12) carries the same
        FSR-shaped city/target payload and re-decides a forecast family — it MUST get the same
        strictly-past timeliness floor, or a price-driven redecision would re-fire on an
        already-settled market (wrong-side risk). Hence the forecast-decision set, not == FSR.
        """
        if event.event_type not in _FORECAST_DECISION_EVENT_TYPES:
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

    def requeue_pending(
        self,
        event_id: str,
        *,
        not_before: str | None = None,
        last_error: str | None = None,
    ) -> None:
        """Return an in-flight ('processing') event to 'pending' for retry next cycle.

        Used for TRANSIENT, non-terminal blocks (e.g. the executable market snapshot for the
        family has not been captured yet this cycle). Keeps ``attempt_count`` so the caller
        can observe retry debt; does NOT consume the event the way ``mark_processed`` does.
        ``not_before`` stores a retry floor in ``claimed_at`` for pending rows so refresh-waiting
        substrate blocks do not immediately reclaim the next decision slot before their sidecar
        refresh can complete.
        """

        self._require_world_event_tables()
        self.conn.execute(
            "UPDATE opportunity_event_processing "
            "SET processing_status = 'pending', claimed_at = ?, last_error = ?, updated_at = ? "
            "WHERE consumer_name = ? AND event_id = ?",
            (not_before, last_error, _utc_now(), self.consumer_name, event_id),
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


# The canonical opportunity_events column order. A SELECTed row may carry EXTRA
# trailing columns — fetch_pending's per-city round-robin appends the ordering
# helpers _claim_tier and _city_round so the budget-bounded queue can interleave
# cities fairly — which are NOT OpportunityEvent fields. Projecting to exactly
# these keys keeps OpportunityEvent(**data) from receiving an unexpected kwarg.
_EVENT_ROW_KEYS: tuple[str, ...] = (
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
)


def _event_payload_dict(event: OpportunityEvent) -> dict:
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _event_city_key(event: OpportunityEvent) -> str:
    entity_key = str(event.entity_key or "")
    if "|" in entity_key:
        city = entity_key.split("|", 1)[0].strip()
        if city:
            return city
    return entity_key.strip() or str(event.event_type or "")


def _claim_tier_for_event(
    event: OpportunityEvent,
    payload: dict,
    *,
    day0_is_tradeable: bool,
) -> int:
    if (
        event.event_type == "FORECAST_SNAPSHOT_READY"
        and str(event.source or "").startswith(ESCALATION_CROSS_SOURCE_PREFIX)
    ):
        return 0
    if event.event_type == "EDLI_REDECISION_PENDING":
        return 0
    if event.event_type == "DAY0_EXTREME_UPDATED" and day0_is_tradeable:
        return 0
    if (
        event.event_type == "FORECAST_SNAPSHOT_READY"
        and payload.get("coverage_completeness_status") == "COMPLETE"
        and payload.get("coverage_readiness_status") == "LIVE_ELIGIBLE"
    ):
        return 1
    if event.event_type in {
        "BEST_BID_ASK_CHANGED",
        "BOOK_SNAPSHOT",
        "NEW_MARKET_DISCOVERED",
    }:
        return 3
    return 2


def _date_desc_key(value: object) -> tuple[int, int]:
    text = str(value or "").strip()
    if not text:
        return (1, 0)
    try:
        return (0, -date.fromisoformat(text).toordinal())
    except ValueError:
        return (1, 0)


def _datetime_desc_key(value: object) -> tuple[int, float]:
    text = str(value or "").strip()
    if not text:
        return (1, 0.0)
    try:
        parsed = _parse_utc(text)
    except Exception:
        return (1, 0.0)
    return (0, -parsed.timestamp())


def _pending_row_attempt_count(row: sqlite3.Row | tuple) -> int:
    if isinstance(row, sqlite3.Row):
        try:
            raw = row["_p_attempt_count"]
        except (IndexError, KeyError):
            raw = None
    else:
        raw = row[len(_EVENT_ROW_KEYS)] if len(row) > len(_EVENT_ROW_KEYS) else None
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _rank_pending_rows_python(
    rows: list[sqlite3.Row | tuple] | tuple[sqlite3.Row | tuple, ...],
    *,
    day0_is_tradeable: bool,
) -> list[tuple[OpportunityEvent, int]]:
    records: list[dict] = []
    for row in rows:
        event = _event_from_row(row)
        attempt_count = _pending_row_attempt_count(row)
        payload = _event_payload_dict(event)
        records.append(
            {
                "event": event,
                "attempt_count": attempt_count,
                "payload": payload,
                "tier": _claim_tier_for_event(
                    event,
                    payload,
                    day0_is_tradeable=day0_is_tradeable,
                ),
                "city_key": _event_city_key(event),
                "target_key": _date_desc_key(payload.get("target_date")),
                "available_key": _datetime_desc_key(event.available_at),
                "retry_key": 0 if attempt_count > 0 else 1,
                "received_key": _datetime_desc_key(event.received_at),
            }
        )

    intra_city = sorted(
        records,
        key=lambda item: (
            item["tier"],
            item["city_key"],
            item["target_key"],
            item["available_key"],
            item["retry_key"],
            item["received_key"],
            item["event"].event_id,
        ),
    )
    rounds_by_key: dict[tuple[int, str], int] = {}
    for item in intra_city:
        key = (int(item["tier"]), str(item["city_key"]))
        rounds_by_key[key] = rounds_by_key.get(key, 0) + 1
        item["city_round"] = rounds_by_key[key]

    ranked = sorted(
        records,
        key=lambda item: (
            item["tier"],
            item.get("city_round", 1),
            -int(getattr(item["event"], "priority", 0) or 0),
            item["target_key"],
            item["available_key"],
            item["retry_key"],
            item["received_key"],
            item["event"].event_id,
        ),
    )
    return [(item["event"], int(item["attempt_count"])) for item in ranked]


def _event_from_row(row: sqlite3.Row | tuple) -> OpportunityEvent:
    if isinstance(row, sqlite3.Row):
        full = dict(row)
        data = {key: full[key] for key in _EVENT_ROW_KEYS}
    else:
        # Positional rows must lead with the event columns in table order; any
        # trailing ordering-helper columns are dropped by the zip truncation.
        data = dict(zip(_EVENT_ROW_KEYS, row))
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


def _venue_close_target_ceiling(decision_time_utc: datetime) -> str:
    """ISO date string: the latest ``target_date`` whose F1 12:00-UTC venue close
    COULD have fired at ``decision_time``.

    The Polymarket weather venue closes at 12:00 UTC of ``target_date`` (the F1
    anchor, ``_f1_fallback_end_utc``).  A family with target_date T is POST_TRADING
    iff ``decision_time >= T 12:00 UTC``, i.e. ``T <= decision_time - 12h`` (UTC date
    part).  Any target_date UP TO AND INCLUDING this date is a candidate for the
    venue-close check in Python; target_dates strictly after it cannot yet be
    POST_TRADING (their 12:00-UTC anchor has not fired).

    This is a NECESSARY-CONDITION band, not a SUFFICIENT one — the Python loop's
    ``_venue_closed_in_phase`` call is the sufficient gate (fail-closed).

    Fail-safe: on arithmetic error returns a date far in the past (no rows matched)
    — never an over-archive.
    """
    try:
        shifted = decision_time_utc - timedelta(hours=12)
        return shifted.date().isoformat()
    except Exception:
        return "0001-01-01"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event store timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
