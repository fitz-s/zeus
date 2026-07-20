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
import os
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from src.events.opportunity_event import OpportunityEvent

GLOBAL_WINNER_TARGETED_CLAIM = "GLOBAL_WINNER_TARGETED_CLAIM"

# Continuous re-decision resurrection (2026-06-12): the forecast decision lane. EDLI_REDECISION_PENDING
# carries the same FSR-shaped city/target payload and gets the same timeliness floor. Literal here
# (mirrors src.events.continuous_redecision.REDECISION_EVENT_TYPE) to avoid an import cycle.
_FORECAST_DECISION_EVENT_TYPES = frozenset({"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"})
_DECISION_TRIGGER_EVENT_TYPES = _FORECAST_DECISION_EVENT_TYPES | frozenset(
    {"DAY0_EXTREME_UPDATED"}
)
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
_TERMINAL_PENDING_LAST_ERROR_PREFIXES = (
    "QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:",
)
_RECENT_RECAPTURE_EDGE_REVERSED_REASON = "SUBMIT_ABORTED_EDGE_REVERSED"
_MISSING_PROCESSING_REPAIR_LOOKBACK_HOURS = 48
_DAY0_PAUSE_REQUEUE_LOOKBACK_HOURS = 48
_DAY0_EXTREME_VALUE_SQL = (
    "CAST(COALESCE("
    "json_extract(e.payload_json, '$.rounded_value'), "
    "json_extract(e.payload_json, '$.high_so_far'), "
    "json_extract(e.payload_json, '$.low_so_far')"
    ") AS REAL)"
)
_FamilyNormalizer = Callable[[object, object, object], tuple[str, str, str]]


def _recapture_edge_backoff_seconds() -> int:
    try:
        value = int(os.environ.get("ZEUS_RECAPTURE_EDGE_BACKOFF_SECONDS", "600"))
    except (TypeError, ValueError):
        value = 600
    return max(0, min(value, 3600))


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


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _safe_bool_int(value: object) -> int:
    return 1 if _safe_int(value, 0) > 0 else 0


def _default_family_normalizer(city: object, target_date: object, metric: object) -> tuple[str, str, str]:
    city_text = " ".join(str(city or "").strip().lower().replace("-", " ").replace("_", " ").split())
    metric_text = " ".join(str(metric or "").strip().lower().replace("-", " ").replace("_", " ").split())
    if metric_text in {"lowest", "min", "minimum", "tmin"} or metric_text.startswith("lowest "):
        metric_text = "low"
    elif metric_text in {"highest", "max", "maximum", "tmax"} or metric_text.startswith("highest "):
        metric_text = "high"
    return (city_text, str(target_date or "").strip(), metric_text)


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
        self._world_event_tables_ready = False

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
        if event.event_type in self._CHANNEL_EVENT_TYPES:
            # Channel rows are immutable cache provenance, not reactor work. A
            # terminal processing row says nothing beyond the event type itself
            # and permanently amplifies every quote tick across all processing
            # indexes. Legacy rows remain compatible with the archive helpers.
            return inserted

        now = _utc_now()
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
                "pending",
                None,
                None,
                now,
            ),
        )
        return inserted

    def archive_superseded_day0_family(self, event: OpportunityEvent) -> int:
        """Expire older pending work for one Day0 family in the append transaction."""

        try:
            payload = json.loads(event.payload_json)
        except (TypeError, json.JSONDecodeError):
            return 0
        city = str(payload.get("city") or "").strip()
        target_date = str(payload.get("target_date") or "").strip()
        metric = str(payload.get("metric") or "").strip().lower()
        if not city or not target_date or metric not in {"high", "low"}:
            return 0

        keeper_id = self._day0_family_keeper_id(city, target_date, metric)
        if keeper_id is None:
            return 0

        now = _utc_now()
        cur = self.conn.execute(
            """
            UPDATE opportunity_event_processing
               SET processing_status = 'expired',
                   processed_at = ?,
                   last_error = 'DAY0_FAMILY_SUPERSEDED',
                   updated_at = ?
             WHERE consumer_name = ?
               AND processing_status = 'pending'
               AND event_id != ?
               AND event_id IN (
                    SELECT e.event_id
                      FROM opportunity_events e INDEXED BY idx_opportunity_events_day0_family_extreme
                     WHERE e.event_type = 'DAY0_EXTREME_UPDATED'
                       AND json_extract(e.payload_json, '$.city') = ?
                       AND json_extract(e.payload_json, '$.target_date') = ?
                       AND json_extract(e.payload_json, '$.metric') = ?
               )
            """,
            (
                now,
                now,
                self.consumer_name,
                keeper_id,
                city,
                target_date,
                metric,
            ),
        )
        return int(cur.rowcount)

    def _day0_family_keeper_id(
        self,
        city: str,
        target_date: str,
        metric: str,
    ) -> str | None:
        """Return one deterministic trigger for the family's absorbing extreme."""

        order = "DESC" if metric == "high" else "ASC"
        extreme_row = self.conn.execute(
            f"""
            SELECT {_DAY0_EXTREME_VALUE_SQL} AS extreme_value,
                   e.available_at
              FROM opportunity_events e INDEXED BY idx_opportunity_events_day0_family_extreme
             WHERE e.event_type = 'DAY0_EXTREME_UPDATED'
               AND json_extract(e.payload_json, '$.city') = ?
               AND json_extract(e.payload_json, '$.target_date') = ?
               AND json_extract(e.payload_json, '$.metric') = ?
               AND {_DAY0_EXTREME_VALUE_SQL} IS NOT NULL
             ORDER BY {_DAY0_EXTREME_VALUE_SQL} {order}, e.available_at DESC
             LIMIT 1
            """,
            (city, target_date, metric),
        ).fetchone()
        if extreme_row is None:
            return None
        try:
            extreme_value = float(extreme_row[0])
        except (TypeError, ValueError):
            return None
        available_at = str(extreme_row[1] or "")
        keeper_rows = self.conn.execute(
            f"""
            SELECT e.event_id, e.received_at
              FROM opportunity_events e INDEXED BY idx_opportunity_events_day0_family_extreme
             WHERE e.event_type = 'DAY0_EXTREME_UPDATED'
               AND json_extract(e.payload_json, '$.city') = ?
               AND json_extract(e.payload_json, '$.target_date') = ?
               AND json_extract(e.payload_json, '$.metric') = ?
               AND {_DAY0_EXTREME_VALUE_SQL} = ?
               AND e.available_at = ?
            """,
            (
                city,
                target_date,
                metric,
                extreme_value,
                available_at,
            ),
        ).fetchall()
        if not keeper_rows:
            return None
        return str(
            max(
                keeper_rows,
                key=lambda row: (str(row[1] or ""), str(row[0] or "")),
            )[0]
        )

    def repair_missing_processing_rows(
        self,
        *,
        decision_time: str,
        batch_limit: int = 1_000,
    ) -> int:
        """Backfill missing mutable processing rows for immutable decision events.

        Older writer bugs could leave an ``opportunity_events`` row without the
        matching ``opportunity_event_processing`` row. Such events are invisible
        to ``fetch_pending`` forever because processing rows are the mutable
        claim/retry surface. Runtime repair is scoped to recent decision events:
        a full historical scan belongs to explicit maintenance, not every
        reactor prune cycle. Repair only decision-trigger event types; market
        channel cache events are intentionally ignored work and must not be
        resurrected as reactor candidates.
        """

        self._require_world_event_tables()
        parsed_decision_time = _parse_utc(decision_time)
        parsed_decision_time_iso = parsed_decision_time.isoformat()
        repair_floor = (
            parsed_decision_time - timedelta(hours=_MISSING_PROCESSING_REPAIR_LOOKBACK_HOURS)
        ).isoformat()
        limit = max(1, int(batch_limit))
        placeholders = ",".join("?" for _ in _DECISION_TRIGGER_EVENT_TYPES)
        rows = self.conn.execute(
            f"""
            SELECT e.event_id
              FROM opportunity_events e INDEXED BY idx_opportunity_events_type_available
              LEFT JOIN opportunity_event_processing p
                ON p.consumer_name = ?
               AND p.event_id = e.event_id
             WHERE p.event_id IS NULL
               AND e.event_type IN ({placeholders})
               AND e.available_at <= ?
               AND e.received_at <= ?
               AND (e.expires_at IS NULL OR e.expires_at > ?)
               AND e.available_at >= ?
             ORDER BY e.available_at DESC
             LIMIT ?
            """,
            (
                self.consumer_name,
                *sorted(_DECISION_TRIGGER_EVENT_TYPES),
                parsed_decision_time_iso,
                parsed_decision_time_iso,
                parsed_decision_time_iso,
                repair_floor,
                limit,
            ),
        ).fetchall()
        event_ids = [str(row[0] or "") for row in rows if str(row[0] or "")]
        if not event_ids:
            return 0
        now = _utc_now()
        before = self.conn.total_changes
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO opportunity_event_processing (
                consumer_name, event_id, processing_status, attempt_count,
                processed_at, last_error, updated_at
            ) VALUES (?, ?, 'pending', 0, NULL, NULL, ?)
            """,
            ((self.consumer_name, event_id, now) for event_id in event_ids),
        )
        return int(self.conn.total_changes - before)

    def _active_processing_candidate_rows(
        self,
        select_sql: str,
        *,
        join_sql: str = "",
        where_sql: str = "",
        params: tuple[object, ...] = (),
        order_by: str = "p.updated_at ASC",
        batch_limit: int = 5_000,
    ) -> list[sqlite3.Row | tuple[Any, ...]]:
        """Read active processing candidates without multi-status temp sorts."""

        limit = max(1, int(batch_limit))
        rows: list[sqlite3.Row | tuple[Any, ...]] = []
        for status in ("pending", "processing"):
            rows.extend(
                self.conn.execute(
                    f"""
                    SELECT {select_sql},
                           p.updated_at AS _p_updated_at,
                           p.event_id AS _p_event_id
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_status
                      {join_sql}
                     WHERE p.consumer_name = ?
                       AND p.processing_status = ?
                       {where_sql}
                     ORDER BY {order_by}
                     LIMIT ?
                    """,
                    (self.consumer_name, status, *params, limit),
                ).fetchall()
            )

        def _hidden(row: sqlite3.Row | tuple[Any, ...], key: str, index: int) -> str:
            try:
                value = row[key] if isinstance(row, sqlite3.Row) else row[index]
            except (IndexError, KeyError):
                value = ""
            return str(value or "")

        rows.sort(
            key=lambda row: (
                _hidden(row, "_p_updated_at", -2),
                _hidden(row, "_p_event_id", -1),
            )
        )
        return rows[:limit]

    def fetch_pending_by_event_type(
        self, *, event_type: str, decision_time: str, limit: int = 100
    ) -> list[OpportunityEvent]:
        """Fetch pending events of exactly ``event_type`` for THIS consumer, oldest
        ``available_at`` first.

        A standalone, event-type-scoped sibling of :meth:`fetch_pending` for
        consumers outside the main forecast/day0 decision lane (W4.2:
        ``SOURCE_RUN_ARRIVED`` staleness consumption). It does NOT touch
        ``_DECISION_TRIGGER_EVENT_TYPES``/``_FORECAST_DECISION_EVENT_TYPES`` or the
        city-fairness/tier ranking those event types share — a caller here should
        use its own ``consumer_name`` (never ``edli_reactor_v1``) so this claim
        lane cannot starve or reorder the main reactor's queue.

        Self-backfilling: a processing row for THIS consumer is created lazily
        (mirrors :meth:`repair_missing_processing_rows`) rather than requiring the
        writer to have known about this consumer at insert time.
        """

        self._require_world_event_tables()
        parsed_decision_time = _parse_utc(decision_time)
        stale_processing_before = (
            parsed_decision_time - timedelta(seconds=self.processing_lease_seconds)
        ).isoformat()

        backfill_rows = self.conn.execute(
            """
            SELECT e.event_id
              FROM opportunity_events e
              LEFT JOIN opportunity_event_processing p
                ON p.consumer_name = ?
               AND p.event_id = e.event_id
             WHERE p.event_id IS NULL
               AND e.event_type = ?
               AND e.available_at <= ?
             LIMIT ?
            """,
            (self.consumer_name, event_type, parsed_decision_time.isoformat(), max(1, limit) * 4),
        ).fetchall()
        backfill_ids = [str(row[0] or "") for row in backfill_rows if str(row[0] or "")]
        if backfill_ids:
            now = _utc_now()
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO opportunity_event_processing (
                    consumer_name, event_id, processing_status, attempt_count,
                    processed_at, last_error, updated_at
                ) VALUES (?, ?, 'pending', 0, NULL, NULL, ?)
                """,
                ((self.consumer_name, event_id, now) for event_id in backfill_ids),
            )

        event_cols = ", ".join(f"e.{key}" for key in _EVENT_ROW_KEYS)
        rows = self.conn.execute(
            f"""
            SELECT {event_cols}
              FROM opportunity_event_processing p
              JOIN opportunity_events e ON e.event_id = p.event_id
             WHERE p.consumer_name = ?
               AND e.event_type = ?
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
             ORDER BY e.available_at ASC, e.event_id ASC
             LIMIT ?
            """,
            (
                self.consumer_name,
                event_type,
                stale_processing_before,
                parsed_decision_time.isoformat(),
                parsed_decision_time.isoformat(),
                parsed_decision_time.isoformat(),
                limit,
            ),
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def fetch_pending(
        self,
        *,
        decision_time: str,
        limit: int = 100,
        day0_is_tradeable: bool = True,
        targeted_event_ids: frozenset[str] = frozenset(),
        targeted_only: bool = False,
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

        ``targeted_only`` is the producer-wake fast path. It admits only the
        explicitly committed event IDs instead of filling the remaining page
        with unrelated queue debt. The global auction may still select an
        unpaged winner from its complete current universe.
        """

        self._require_world_event_tables()

        parsed_decision_time = _parse_utc(decision_time)
        stale_processing_before = (
            parsed_decision_time - timedelta(seconds=self.processing_lease_seconds)
        ).isoformat()
        # Scope-aware claim tier (ONE ordering authority, shared with the emit
        # constants). day0_is_tradeable=False omits the DAY0_EXTREME_UPDATED Tier-0
        # clause so non-tradeable day0 events fall to Tier 2 — strictly below the
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
        # A later bounded-overfetch CTE still let SQLite choose an event-table-first
        # plan under redecision/day0 predicates. Keep SQL to active processing rows
        # only, then point-read events by event_id and do tier/city ranking in Python.
        active_limit = 0 if targeted_only else max(limit * 512, limit + 20_000)
        active_rows: list[tuple[str, int, str, int]] = []
        clean_targeted_event_ids = tuple(
            dict.fromkeys(
                event_id
                for raw_event_id in targeted_event_ids
                if (event_id := str(raw_event_id or "").strip())
            )
        )[:100]
        if targeted_only and not clean_targeted_event_ids:
            return []
        rows: list[tuple[object, ...]] = []
        attempt_by_event: dict[str, int] = {}
        last_error_by_event: dict[str, str] = {}
        stale_reclaim_by_event: dict[str, int] = {}
        event_cols = ", ".join(f"e.{key}" for key in _EVENT_ROW_KEYS)
        if targeted_only:
            placeholders = ",".join("?" for _ in clean_targeted_event_ids)
            event_col_count = len(_EVENT_ROW_KEYS)
            targeted_rows = self.conn.execute(
                f"""
                SELECT {event_cols},
                       p.attempt_count,
                       p.last_error,
                       CASE WHEN p.processing_status = 'processing' THEN 1 ELSE 0 END
                  FROM opportunity_event_processing p
                  JOIN opportunity_events e ON e.event_id = p.event_id
                 WHERE p.consumer_name = ?
                   AND p.event_id IN ({placeholders})
                   AND (
                        (
                            p.processing_status = 'pending'
                            AND (
                                p.claimed_at IS NULL
                                OR p.claimed_at <= ?
                            )
                        )
                        OR (
                            p.processing_status = 'processing'
                            AND p.claimed_at IS NOT NULL
                            AND p.claimed_at <= ?
                        )
                   )
                   AND e.available_at <= ?
                   AND e.received_at <= ?
                   AND (e.expires_at IS NULL OR e.expires_at > ?)
                   AND e.event_type NOT IN (
                         'BEST_BID_ASK_CHANGED',
                         'BOOK_SNAPSHOT',
                         'NEW_MARKET_DISCOVERED'
                   )
                """,
                (
                    self.consumer_name,
                    *clean_targeted_event_ids,
                    parsed_decision_time.isoformat(),
                    stale_processing_before,
                    parsed_decision_time.isoformat(),
                    parsed_decision_time.isoformat(),
                    parsed_decision_time.isoformat(),
                ),
            ).fetchall()
            for row in targeted_rows:
                event_tuple = tuple(row[:event_col_count])
                event_id = str(event_tuple[0] or "")
                attempt_by_event[event_id] = _safe_int(row[event_col_count])
                last_error_by_event[event_id] = str(row[event_col_count + 1] or "")
                stale_reclaim_by_event[event_id] = _safe_int(row[event_col_count + 2])
                if _selection_deadline_past(
                    last_error_by_event[event_id],
                    parsed_decision_time,
                ):
                    continue
                rows.append(event_tuple + (attempt_by_event[event_id],))
        if clean_targeted_event_ids and not targeted_only:
            placeholders = ",".join("?" for _ in clean_targeted_event_ids)
            active_rows.extend(
                (
                    str(row[0] or ""),
                    _safe_int(row[1]),
                    str(row[2] or ""),
                    _safe_int(row[3]),
                )
                for row in self.conn.execute(
                    f"""
                    SELECT p.event_id,
                           p.attempt_count,
                           p.last_error,
                           CASE WHEN p.processing_status = 'processing' THEN 1 ELSE 0 END
                      FROM opportunity_event_processing p
                     WHERE p.consumer_name = ?
                       AND p.event_id IN ({placeholders})
                       AND (
                            (
                                p.processing_status = 'pending'
                                AND (
                                    p.claimed_at IS NULL
                                    OR p.claimed_at <= ?
                                )
                            )
                            OR (
                                p.processing_status = 'processing'
                                AND p.claimed_at IS NOT NULL
                                AND p.claimed_at <= ?
                            )
                       )
                    """,
                    (
                        self.consumer_name,
                        *clean_targeted_event_ids,
                        parsed_decision_time.isoformat(),
                        stale_processing_before,
                    ),
                ).fetchall()
            )
        # A target is inserted before the current batch's claimed rows are
        # finalized.  Finalization then updates as many as ``limit`` older target
        # rows after the new target, so the generic newest-row probe below can
        # page the actual winner out by exactly one slot.  Read one extra targeted
        # row through the status/update index; after event rows are loaded we keep
        # only the carrier with the newest ``received_at`` as the global target.
        if not targeted_only:
            active_rows.extend(
                (str(row[0] or ""), _safe_int(row[1]), str(row[2] or ""), 0)
                for row in self.conn.execute(
                    """
                    SELECT p.event_id, p.attempt_count, p.last_error
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_status
                     WHERE p.consumer_name = ?
                       AND p.processing_status = 'pending'
                       AND p.claimed_at IS NULL
                       AND p.last_error = ?
                     ORDER BY p.updated_at DESC
                     LIMIT ?
                    """,
                    (
                        self.consumer_name,
                        GLOBAL_WINNER_TARGETED_CLAIM,
                        max(1, limit + 1),
                    ),
                ).fetchall()
            )
            # A global-auction winner that was outside the current claim page is
            # materialized as a fresh pending row with
            # last_error=GLOBAL_WINNER_TARGETED_CLAIM. The debt/fairness probe below
            # intentionally reads the OLDEST updated rows first, but on a backlog
            # larger than active_limit that makes the freshly targeted winner
            # invisible forever: every epoch selects it globally, cannot find it in
            # the claimed page, and targets it again at the tail. Probe one page from
            # the indexed NEWEST end before the old-debt scan. Python ranking still
            # gives only explicitly targeted rows the priority tier, so ordinary new
            # rows do not bypass fairness; this merely makes a targeted row visible.
            active_rows.extend(
                (str(row[0] or ""), _safe_int(row[1]), str(row[2] or ""), 0)
                for row in self.conn.execute(
                    """
                    SELECT p.event_id, p.attempt_count, p.last_error
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_pending_retry_floor
                     WHERE p.consumer_name = ?
                       AND p.processing_status = 'pending'
                       AND p.claimed_at IS NULL
                     ORDER BY p.updated_at DESC
                     LIMIT ?
                    """,
                    (self.consumer_name, max(1, limit)),
                ).fetchall()
            )
            # Keep the immediate-ready and retry-floor-ready pending lanes as two
            # indexed probes. A single OR predicate over claimed_at makes SQLite
            # materialize a temp ORDER BY tree on large live processing tables.
            active_rows.extend(
                (str(row[0] or ""), _safe_int(row[1]), str(row[2] or ""), 0)
                for row in self.conn.execute(
                    """
                    SELECT p.event_id, p.attempt_count, p.last_error
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_pending_retry_floor
                     WHERE p.consumer_name = ?
                       AND p.processing_status = 'pending'
                       AND p.claimed_at IS NULL
                     ORDER BY p.updated_at ASC
                     LIMIT ?
                    """,
                    (self.consumer_name, active_limit),
                ).fetchall()
            )
            active_rows.extend(
                (str(row[0] or ""), _safe_int(row[1]), str(row[2] or ""), 0)
                for row in self.conn.execute(
                    """
                    SELECT p.event_id, p.attempt_count, p.last_error
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_pending_retry_floor
                     WHERE p.consumer_name = ?
                       AND p.processing_status = 'pending'
                       AND p.claimed_at IS NOT NULL
                       AND p.claimed_at <= ?
                     ORDER BY p.claimed_at ASC
                     LIMIT ?
                    """,
                    (self.consumer_name, parsed_decision_time.isoformat(), active_limit),
                ).fetchall()
            )
            active_rows.extend(
                (str(row[0] or ""), _safe_int(row[1]), str(row[2] or ""), 1)
                for row in self.conn.execute(
                    """
                    SELECT p.event_id, p.attempt_count, p.last_error
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_stale_claim
                     WHERE p.consumer_name = ?
                       AND p.processing_status = 'processing'
                       AND p.claimed_at IS NOT NULL
                       AND p.claimed_at <= ?
                     ORDER BY p.claimed_at ASC
                     LIMIT ?
                    """,
                    (self.consumer_name, stale_processing_before, active_limit),
                ).fetchall()
            )
        if not rows and not active_rows:
            return []
        event_ids: list[str] = []
        for event_id, attempt_count, last_error, stale_reclaim in active_rows:
            if not event_id or event_id in attempt_by_event:
                continue
            attempt_by_event[event_id] = attempt_count
            last_error_by_event[event_id] = last_error
            stale_reclaim_by_event[event_id] = stale_reclaim
            event_ids.append(event_id)

        for start in range(0, len(event_ids), 250):
            chunk = event_ids[start : start + 250]
            placeholders = ",".join("?" for _ in chunk)
            event_rows = self.conn.execute(
                f"""
                SELECT {event_cols}
                  FROM opportunity_events e
                 WHERE e.available_at <= ?
                   AND e.received_at <= ?
                   AND (e.expires_at IS NULL OR e.expires_at > ?)
                   AND e.event_type NOT IN (
                         'BEST_BID_ASK_CHANGED',
                         'BOOK_SNAPSHOT',
                         'NEW_MARKET_DISCOVERED'
                   )
                   AND e.event_id IN ({placeholders})
                """,
                (
                    parsed_decision_time.isoformat(),
                    parsed_decision_time.isoformat(),
                    parsed_decision_time.isoformat(),
                    *chunk,
                ),
            ).fetchall()
            for row in event_rows:
                if isinstance(row, sqlite3.Row):
                    event_id = str(row["event_id"] or "")
                    event_tuple = tuple(row[key] for key in _EVENT_ROW_KEYS)
                else:
                    event_id = str(row[0] or "")
                    event_tuple = tuple(row[: len(_EVENT_ROW_KEYS)])
                if _selection_deadline_past(
                    last_error_by_event.get(event_id, ""),
                    parsed_decision_time,
                ):
                    continue
                rows.append(event_tuple + (attempt_by_event.get(event_id, 0),))

        cooled_families = _recent_recapture_edge_reversed_families(
            self.conn,
            rows,
            decision_time_utc=parsed_decision_time,
        )
        rank_rows = []
        targeted_generations: dict[str, str] = {}
        for row in rows:
            event = _event_from_row(row)
            if (
                last_error_by_event.get(event.event_id)
                == GLOBAL_WINNER_TARGETED_CLAIM
            ):
                targeted_generations[event.event_id] = event.received_at
            payload = _event_payload_dict(event)
            family_key = _forecast_family_key_from_payload(payload)
            redecision_origin = str(payload.get("redecision_origin") or "").strip().lower()
            recapture_edge_backoff = (
                (
                    event.event_type == "FORECAST_SNAPSHOT_READY"
                    or (
                        event.event_type == "EDLI_REDECISION_PENDING"
                        and redecision_origin in {"entry_screen", "market_price", ""}
                    )
                )
                and family_key is not None
                and family_key in cooled_families
            )
            if isinstance(row, sqlite3.Row):
                rank_rows.append(tuple(row[key] for key in _EVENT_ROW_KEYS) + (
                    _pending_row_attempt_count(row),
                    1 if recapture_edge_backoff else 0,
                    0,
                ))
            else:
                event_id = str(row[0] or "") if row else ""
                rank_rows.append(
                    tuple(row)
                    + (
                        1 if recapture_edge_backoff else 0,
                        stale_reclaim_by_event.get(event_id, 0),
                    )
                )

        winner_targeted_event_ids = frozenset(
            {
                max(
                    targeted_generations,
                    key=lambda event_id: (
                        targeted_generations[event_id],
                        event_id,
                    ),
                )
            }
            if targeted_generations
            else ()
        )
        targeted_event_ids = (
            frozenset(clean_targeted_event_ids) | winner_targeted_event_ids
        )
        ranked = _rank_pending_rows_python(
            rank_rows,
            day0_is_tradeable=day0_is_tradeable,
            targeted_event_ids=targeted_event_ids,
        )
        events = [event for event, _attempt_count in ranked]
        timely = [event for event in events if self._is_timely(event, parsed_decision_time)]
        return timely[:limit]

    def archive_expired_candidates(
        self, *, decision_time: str, batch_limit: int = 50_000
    ) -> int:
        """Sweep strictly-past-in-tz pending/processing candidates to terminal
        ``expired`` status so the active scan stops re-reading them. A row is
        also expired when its own event/executable selection deadline is already
        past; that window is a market-chain fact for this specific pending
        decision, not a transient infrastructure retry.

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

        LOCAL-DAY EXPIRY is PER-CITY LOCAL TIMEZONE, never raw UTC: a candidate is
        expired iff its whole target LOCAL day has ENDED in its OWN city tz —
        exactly the strictly-past boundary ``_is_timely`` rejects
        (``decision_time >= settlement_day_entry_utc(target_date + 1 day)``).
        Same predicate, shared with the read floor (``_event_strictly_past_in_tz``)
        so the two can never diverge.

        Venue closure is deliberately NOT inferred from Gamma ``endDate`` or the
        historical F1 12:00Z anchor. Those timestamps mark resolution timing, not
        order-entry availability; live markets can still report ``closed=false``
        and ``acceptingOrders=true`` after them. This sweep therefore expires rows
        only after the target local day is strictly past. Explicit venue closure is
        enforced by executable snapshot / submit gates where ``closed`` and
        ``accepting_orders`` are visible.

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
        only those proven strictly-past in their local calendar, event expiry, or
        selected execution window are expired; a re-run at the same decision time
        is a no-op.  ``batch_limit`` bounds the rows examined per call so a one-time
        backlog drains across cycles instead of in one giant transaction.

        Returns the number of processing rows transitioned to ``expired``.
        """

        self._require_world_event_tables()
        decision_time_utc = _parse_utc(decision_time)

        # Oceania-frontier bound: the most-advanced local calendar date on Earth
        # at decision_time, minus one day of margin. Any target_date strictly before
        # this is past in EVERY timezone; target_date exactly equal to the frontier
        # band still needs the per-city check below. Keep the SQL candidate band
        # inclusive so yesterday's UTC-negative rows do not remain pending forever.
        frontier_floor = _oceania_frontier_target_floor(decision_time_utc)
        # DAY0 uses a TODAY-INCLUSIVE frontier (2026-06-15). The -1 day margin exists for
        # forecast-decision rows whose target can be a future TRADING day still ambiguous
        # across timezones.
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

        # Legacy no-op query shape. Static F1/Gamma endDate timing no longer widens
        # expiry; only the local-day predicates below can expire rows.
        venue_close_ceiling = _venue_close_target_ceiling(decision_time_utc)

        candidate_rows = self._active_processing_candidate_rows(
            """
                   e.event_id,
                   json_extract(e.payload_json, '$.city')        AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date,
                   e.event_type,
                   e.expires_at,
                   p.last_error
            """,
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql="""
               AND e.event_type IN (
                    'FORECAST_SNAPSHOT_READY',
                    'EDLI_REDECISION_PENDING',
                    'DAY0_EXTREME_UPDATED'
               )
               AND (
                    (
                        json_extract(e.payload_json, '$.target_date') IS NOT NULL
                        AND
                        (
                            (
                                e.event_type IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING')
                                AND (
                                    json_extract(e.payload_json, '$.target_date') <= ?
                                    OR json_extract(e.payload_json, '$.target_date') <= ?
                                )
                            )
                            OR (
                                e.event_type = 'DAY0_EXTREME_UPDATED'
                                AND (
                                    json_extract(e.payload_json, '$.target_date') < ?
                                    OR json_extract(e.payload_json, '$.target_date') <= ?
                                )
                            )
                        )
                    )
                    OR e.expires_at IS NOT NULL
                    OR p.last_error LIKE '%selection_deadline=%'
               )
            """,
            params=(
                frontier_floor,
                venue_close_ceiling,
                day0_floor,
                venue_close_ceiling,
            ),
            batch_limit=batch_limit,
        )

        expired_ids: list[str] = []
        for row in candidate_rows:
            event_id = row[0]
            city = str(row[1] or "")
            target_date = str(row[2] or "")
            event_type = str(row[3] or "")
            expires_at = row[4]
            last_error = row[5]
            if _instant_past(expires_at, decision_time_utc):
                expired_ids.append(event_id)
                continue
            if _selection_deadline_past(last_error, decision_time_utc):
                expired_ids.append(event_id)
                continue
            if not target_date:
                continue
            if event_type in _FORECAST_DECISION_EVENT_TYPES:
                if not (target_date <= frontier_floor or target_date <= venue_close_ceiling):
                    continue
            elif event_type == "DAY0_EXTREME_UPDATED":
                if not (target_date < day0_floor or target_date <= venue_close_ceiling):
                    continue
            else:
                continue
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
        candidate_rows = self._active_processing_candidate_rows(
            "p.event_id",
            batch_limit=batch_limit,
        )
        candidate_ids = [str(row[0]) for row in candidate_rows]
        if not candidate_ids:
            return 0

        missing_ids: list[str] = []
        _CHUNK = 500
        for chunk_start in range(0, len(candidate_ids), _CHUNK):
            chunk = candidate_ids[chunk_start : chunk_start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            existing = {
                str(row[0])
                for row in self.conn.execute(
                    f"""
                    SELECT event_id
                      FROM opportunity_events
                     WHERE event_id IN ({placeholders})
                    """,
                    tuple(chunk),
                ).fetchall()
            }
            missing_ids.extend(event_id for event_id in chunk if event_id not in existing)
        if not missing_ids:
            return 0

        now = _utc_now()
        for chunk_start in range(0, len(missing_ids), _CHUNK):
            chunk = missing_ids[chunk_start : chunk_start + _CHUNK]
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
        return len(missing_ids)

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
        Candidate batches are evaluated in active-processing ``updated_at`` order
        so the query stays backed by ``idx_opportunity_event_processing_status``;
        keeper probes still use ``MAX(available_at)`` for the supersession truth.

        IDEMPOTENT: re-running at the same state archives nothing new (already-expired
        rows are excluded from the ``pending``/``processing`` filter).

        Returns the number of processing rows transitioned to ``'expired'``.
        """

        self._require_world_event_tables()
        type_placeholders = ",".join("?" * len(self._CHANNEL_EVENT_TYPES))

        # Step 1: fetch active channel-event rows with parseable token_id in
        # processing-updated order.
        # This is the only unscoped scan in the sweep and is batch-limited. The prior
        # implementation first computed keepers across the whole backlog, which could
        # pin the EDLI reactor for minutes before it reached fetch_pending/receipts.
        candidate_rows = self._active_processing_candidate_rows(
            """
                   e.event_id,
                   e.event_type,
                   json_extract(e.payload_json, '$.token_id') AS token_id
            """,
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql=f"""
              AND e.event_type IN ({type_placeholders})
              AND json_extract(e.payload_json, '$.token_id') IS NOT NULL
            """,
            params=tuple(self._CHANNEL_EVENT_TYPES),
            batch_limit=batch_limit,
        )

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
        family represented in the active working set, derive the absorbing extreme
        across the full immutable family and keep at most one active row carrying it:
        ``MAX(rounded_value)`` for high and ``MIN(rounded_value)`` for low. Day0 carries
        NO ``token_id``, so the family tuple is the supersession key (the token-keyed
        channel sweep does not apply). Equal-value duplicates retain immutable event
        provenance, but only the latest ``(available_at, received_at, event_id)``
        trigger may remain active for this consumer. If that keeper was already
        processed, a later regressed or duplicate observation does not replay it.

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

        # Step 1: active day0 rows with a parseable (city, target_date, metric)
        # family key in processing-updated order — the only unscoped scan, batch-limited.
        candidate_rows = self._active_processing_candidate_rows(
            """
                   e.event_id,
                   json_extract(e.payload_json, '$.city')        AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date,
                   json_extract(e.payload_json, '$.metric')      AS metric
            """,
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql="""
              AND e.event_type = 'DAY0_EXTREME_UPDATED'
              AND json_extract(e.payload_json, '$.city') IS NOT NULL
              AND json_extract(e.payload_json, '$.target_date') IS NOT NULL
              AND json_extract(e.payload_json, '$.metric') IS NOT NULL
            """,
            batch_limit=batch_limit,
        )

        if not candidate_rows:
            return 0

        # Step 2: for only the family streams represented in the candidate batch,
        # find the current absorbing keeper(s). The keeper may be outside the
        # candidate batch; preserving it is what makes a small batch safe. Keep this
        # as per-family indexed probes, not one GROUP BY over the active Day0
        # backlog: the live table can hold many stale DAY0 rows, and scanning all of
        # them can pin the reactor worker before it reaches money-path decisions.
        candidate_keys = {
            (str(row[1]), str(row[2]), str(row[3]))
            for row in candidate_rows
            if row[1] is not None and row[2] is not None and row[3] is not None
        }
        keeper_ids: set[str] = set()
        for city, target_date, metric in candidate_keys:
            keeper_id = self._day0_family_keeper_id(city, target_date, metric)
            if keeper_id is not None:
                keeper_ids.add(keeper_id)

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

    def archive_unmarketed_day0_events(
        self,
        *,
        admitted_families: set[tuple[str, str, str]] | frozenset[tuple[str, str, str]],
        normalizer: _FamilyNormalizer | None = None,
        batch_limit: int = 5_000,
    ) -> int:
        """Expire active Day0 execution events for families with no live market/exposure.

        Day0 observations are truth inputs, but a ``DAY0_EXTREME_UPDATED`` row in
        ``opportunity_event_processing`` is an execution decision event. If the family has
        no Polymarket market topology and no current held/open-rest exposure, the reactor
        can only requeue it through executable-snapshot/Gamma-empty churn. Marking the
        mutable processing row ``expired`` keeps the append-only observation provenance
        while removing non-executable observation facts from the live money-path working set.
        """

        self._require_world_event_tables()
        if normalizer is None:
            normalizer = _default_family_normalizer
        admitted = {tuple(family) for family in admitted_families if all(family)}

        candidate_rows = self._active_processing_candidate_rows(
            """
                   e.event_id,
                   json_extract(e.payload_json, '$.city')        AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date,
                   json_extract(e.payload_json, '$.metric')      AS metric
            """,
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql="""
               AND e.event_type = 'DAY0_EXTREME_UPDATED'
               AND json_extract(e.payload_json, '$.city') IS NOT NULL
               AND json_extract(e.payload_json, '$.target_date') IS NOT NULL
               AND json_extract(e.payload_json, '$.metric') IS NOT NULL
            """,
            batch_limit=max(1, int(batch_limit)),
        )
        if not candidate_rows:
            return 0

        expired_ids: list[str] = []
        for row in candidate_rows:
            try:
                family = normalizer(row[1], row[2], row[3])
            except Exception:  # noqa: BLE001
                continue
            if not all(family):
                continue
            if family not in admitted:
                expired_ids.append(str(row[0]))
        if not expired_ids:
            return 0

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
                       last_error = 'DAY0_UNMARKETED_EXECUTION_EVENT:no_market_topology_or_exposure',
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id IN ({placeholders})
                   AND processing_status IN ('pending', 'processing')
                """,
                (now, now, self.consumer_name, *chunk),
            )
        return len(expired_ids)

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
        rows = self._active_processing_candidate_rows(
            "e.event_id",
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql=f"""
               AND e.event_type IN ({type_placeholders})
               AND json_extract(e.payload_json, '$.token_id') IS NOT NULL
            """,
            params=tuple(self._CHANNEL_EVENT_TYPES),
            batch_limit=batch_limit,
        )
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

        candidate_rows = self._active_processing_candidate_rows(
            """
                e.event_id,
                e.event_type,
                e.entity_key,
                json_extract(e.payload_json, '$.city') AS city,
                json_extract(e.payload_json, '$.target_date') AS target_date,
                json_extract(e.payload_json, '$.metric') AS metric,
                json_extract(e.payload_json, '$.coverage_completeness_status')
                    AS coverage_completeness_status,
                json_extract(e.payload_json, '$.coverage_readiness_status')
                    AS coverage_readiness_status,
                COALESCE(
                    json_extract(e.payload_json, '$.member_count'),
                    json_extract(e.payload_json, '$.observed_members'),
                    json_extract(e.payload_json, '$.sr_observed_members')
                ) AS observed_members,
                COALESCE(
                    json_extract(e.payload_json, '$.expected_members'),
                    json_extract(e.payload_json, '$.sr_expected_members')
                ) AS expected_members,
                e.available_at,
                e.received_at
            """,
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql="""
               AND e.event_type IN ('FORECAST_SNAPSHOT_READY', 'EDLI_REDECISION_PENDING')
               AND e.entity_key IS NOT NULL
            """,
            batch_limit=batch_limit,
        )
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
        candidate_limit = max(1, int(batch_limit))

        def _rank(row: sqlite3.Row | tuple) -> tuple[int, str, str, str]:
            coverage_complete = str(row[6] or "") == "COMPLETE"
            coverage_ready = str(row[7] or "") == "LIVE_ELIGIBLE"
            try:
                observed_members = int(row[8]) if row[8] is not None else None
                expected_members = int(row[9]) if row[9] is not None else None
            except (TypeError, ValueError):
                observed_members = None
                expected_members = None
            if (
                coverage_complete
                and coverage_ready
                and observed_members is not None
                and expected_members is not None
                and expected_members > 0
                and observed_members >= expected_members
            ):
                quality = 2
            elif coverage_complete and coverage_ready:
                quality = 1
            else:
                quality = 0
            return quality, str(row[10] or ""), str(row[11] or ""), str(row[0] or "")

        if len(candidate_rows) < candidate_limit:
            keepers: dict[_FsrPruneKey, sqlite3.Row | tuple] = {}
            for row in candidate_rows:
                key = _prune_key(row)
                if not key[-1]:
                    continue
                current = keepers.get(key)
                if current is None or _rank(row) > _rank(current):
                    keepers[key] = row
            keeper_ids.update(str(row[0]) for row in keepers.values())
        else:
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
        rows = self._active_processing_candidate_rows(
            "e.event_id",
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql="""
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
            """,
            batch_limit=batch_limit,
        )
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
        candidate_rows = self._active_processing_candidate_rows(
            """
                e.event_id,
                e.event_type,
                json_extract(e.payload_json, '$.city') AS city,
                json_extract(e.payload_json, '$.target_date') AS target_date,
                json_extract(e.payload_json, '$.metric') AS metric,
                e.causal_snapshot_id,
                e.payload_hash,
                e.available_at,
                e.created_at
            """,
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql=f"""
               AND e.event_type IN ({type_placeholders})
               AND json_extract(e.payload_json, '$.city') IS NOT NULL
               AND json_extract(e.payload_json, '$.target_date') IS NOT NULL
               AND json_extract(e.payload_json, '$.metric') IS NOT NULL
            """,
            params=tuple(event_types),
            batch_limit=batch_limit,
        )
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

    def archive_terminal_last_error_events(self, *, batch_limit: int = 5_000) -> int:
        """Expire active events whose durable retry reason is now terminal law.

        Older live daemons misclassified some same-event terminal economics verdicts
        as money-path transients, leaving rows pending forever and suppressing fresher
        forecast emissions for the same family. This recovery mutates only the
        processing row; the immutable opportunity event stays available as provenance.
        """

        self._require_world_event_tables()
        limit = max(1, min(int(batch_limit or 5_000), 50_000))
        event_types = sorted(_FORECAST_DECISION_EVENT_TYPES)
        type_placeholders = ",".join("?" * len(event_types))
        prefix_sql = " OR ".join(
            "p.last_error LIKE ?" for _ in _TERMINAL_PENDING_LAST_ERROR_PREFIXES
        )
        candidate_rows = self._active_processing_candidate_rows(
            """
                p.event_id,
                p.last_error
            """,
            join_sql="JOIN opportunity_events e ON e.event_id = p.event_id",
            where_sql=f"""
               AND e.event_type IN ({type_placeholders})
               AND p.last_error IS NOT NULL
               AND ({prefix_sql})
            """,
            params=(
                *event_types,
                *(f"{prefix}%" for prefix in _TERMINAL_PENDING_LAST_ERROR_PREFIXES),
            ),
            batch_limit=limit,
        )
        updates: list[tuple[str, str, str, str, str]] = []
        now = _utc_now()
        for row in candidate_rows:
            event_id = str(row[0] or "").strip()
            last_error = str(row[1] or "").strip()
            if not event_id:
                continue
            if not any(
                last_error.startswith(prefix)
                for prefix in _TERMINAL_PENDING_LAST_ERROR_PREFIXES
            ):
                continue
            updates.append(
                (
                    now,
                    ("TERMINAL_LAST_ERROR_ARCHIVED:" + last_error)[:500],
                    now,
                    self.consumer_name,
                    event_id,
                )
            )
        if not updates:
            return 0
        before = self.conn.total_changes
        self.conn.executemany(
            """
            UPDATE opportunity_event_processing
               SET processing_status = 'expired',
                   processed_at = ?,
                   last_error = ?,
                   updated_at = ?
             WHERE consumer_name = ?
               AND event_id = ?
               AND processing_status IN ('pending', 'processing')
            """,
            updates,
        )
        return int(self.conn.total_changes - before)

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
        """Static city/date inputs cannot prove venue closure.

        Gamma ``endDate`` is not an order-entry close proof; live weather rows
        can remain open and accepting orders after that timestamp. The mutable
        processing row is therefore expired only by the local-day floor here.
        Explicit venue closure is enforced at executable snapshot/submit gates.
        """
        return False

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

    def processing_last_error(self, event_id: str) -> str | None:
        """Durable retry/dead-letter context for one event processing row."""

        self._require_world_event_tables()
        row = self.conn.execute(
            "SELECT last_error FROM opportunity_event_processing "
            "WHERE consumer_name = ? AND event_id = ?",
            (self.consumer_name, event_id),
        ).fetchone()
        if row is None or row[0] in {None, ""}:
            return None
        return str(row[0])

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

    def requeue_processing_before_boot(self, *, boot_at: str) -> int:
        """Recover claims whose process owner predates this runtime generation."""

        boundary = _parse_utc(boot_at).isoformat()
        now = _utc_now()
        cur = self.conn.execute(
            """
            UPDATE opportunity_event_processing
               SET processing_status = 'pending',
                   claimed_at = NULL,
                   processed_at = NULL,
                   last_error = CASE
                       WHEN last_error = ? THEN last_error
                       ELSE 'PROCESS_OWNER_RESTARTED'
                   END,
                   updated_at = ?
             WHERE consumer_name = ?
               AND processing_status = 'processing'
               AND claimed_at IS NOT NULL
               AND claimed_at < ?
            """,
            (
                GLOBAL_WINNER_TARGETED_CLAIM,
                now,
                self.consumer_name,
                boundary,
            ),
        )
        return int(cur.rowcount)

    def prioritize_global_winner(
        self,
        event: OpportunityEvent,
        *,
        current_batch_claim_generations: dict[str, str] | None = None,
    ) -> bool:
        """Materialize one current auction winner as the next legal claim.

        The global feasible set is independent of queue pagination, but venue
        actuation remains event-claim-bound.  Insert the scope event when needed
        and mark only an untouched/previously-targeted pending row; never revive a
        terminal event or erase an unrelated transient retry floor.
        """

        try:
            payload = json.loads(event.payload_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        family = (
            str(payload.get("city") or "").strip(),
            str(payload.get("target_date") or "").strip(),
            str(payload.get("metric") or "").strip().lower(),
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            return False

        allowed_claims = current_batch_claim_generations or {}
        if allowed_claims:
            if not self.conn.in_transaction:
                return False
            current_claims: dict[str, str] = {}
            ordered_ids = sorted(allowed_claims)
            for start in range(0, len(ordered_ids), 250):
                chunk = ordered_ids[start : start + 250]
                placeholders = ",".join("?" for _ in chunk)
                rows = self.conn.execute(
                    f"""
                    SELECT event_id, claimed_at
                      FROM opportunity_event_processing
                     WHERE consumer_name = ?
                       AND processing_status = 'processing'
                       AND claimed_at IS NOT NULL
                       AND event_id IN ({placeholders})
                    """,
                    (self.consumer_name, *chunk),
                ).fetchall()
                current_claims.update(
                    (str(row[0]), str(row[1]))
                    for row in rows
                    if row[0] and row[1]
                )
            if current_claims != allowed_claims:
                return False

        old_ids: set[str] = set()
        processing_claims: dict[str, str] = {}
        for event_type, index_name in (
            ("FORECAST_SNAPSHOT_READY", "idx_opportunity_events_fsr_target_date"),
            ("EDLI_REDECISION_PENDING", "idx_opportunity_events_fsr_target_date"),
            ("DAY0_EXTREME_UPDATED", "idx_opportunity_events_day0_family_extreme"),
        ):
            event_type_sql = {
                "FORECAST_SNAPSHOT_READY": "'FORECAST_SNAPSHOT_READY'",
                "EDLI_REDECISION_PENDING": "'EDLI_REDECISION_PENDING'",
                "DAY0_EXTREME_UPDATED": "'DAY0_EXTREME_UPDATED'",
            }[event_type]
            rows = self.conn.execute(
                f"""
                SELECT e.event_id, p.processing_status, p.claimed_at
                  FROM opportunity_events e INDEXED BY {index_name}
                  JOIN opportunity_event_processing p
                    ON p.consumer_name = ? AND p.event_id = e.event_id
                 WHERE e.event_type = {event_type_sql}
                   AND json_extract(e.payload_json, '$.city') = ?
                   AND json_extract(e.payload_json, '$.target_date') = ?
                   AND json_extract(e.payload_json, '$.metric') = ?
                   AND (
                        p.processing_status = 'processing'
                     OR (
                            p.processing_status = 'pending'
                        AND p.last_error = ?
                     )
                   )
                """,
                (
                    self.consumer_name,
                    *family,
                    GLOBAL_WINNER_TARGETED_CLAIM,
                ),
            ).fetchall()
            for row in rows:
                event_id = str(row[0] or "")
                if not event_id:
                    continue
                if str(row[1] or "") == "processing":
                    processing_claims[event_id] = str(row[2] or "")
                elif event_id != event.event_id:
                    old_ids.add(event_id)

        if any(
            allowed_claims.get(event_id) != claimed_at
            for event_id, claimed_at in processing_claims.items()
        ):
            return False

        self.insert_or_ignore(event)

        now = _utc_now()
        if old_ids:
            ordered_ids = sorted(old_ids)
            for start in range(0, len(ordered_ids), 250):
                chunk = ordered_ids[start : start + 250]
                placeholders = ",".join("?" for _ in chunk)
                self.conn.execute(
                    f"""
                    UPDATE opportunity_event_processing
                       SET processing_status = 'expired',
                           processed_at = ?,
                           last_error = 'GLOBAL_WINNER_TARGET_SUPERSEDED',
                           updated_at = ?
                     WHERE consumer_name = ?
                       AND processing_status = 'pending'
                       AND last_error = ?
                       AND event_id IN ({placeholders})
                    """,
                    (
                        now,
                        now,
                        self.consumer_name,
                        GLOBAL_WINNER_TARGETED_CLAIM,
                        *chunk,
                    ),
                )
        cur = self.conn.execute(
            "UPDATE opportunity_event_processing "
            "SET claimed_at = NULL, last_error = ?, updated_at = ? "
            "WHERE consumer_name = ? AND event_id = ? "
            "AND processing_status = 'pending' "
            "AND (last_error IS NULL OR last_error = ?)",
            (
                GLOBAL_WINNER_TARGETED_CLAIM,
                now,
                self.consumer_name,
                event.event_id,
                GLOBAL_WINNER_TARGETED_CLAIM,
            ),
        )
        return cur.rowcount == 1

    def requeue_misclassified_local_pre_submit_rejections(self, *, batch_limit: int = 100) -> int:
        """Recover processed events poisoned by old local pre-submit reject receipts.

        A historical executor-boundary bug mapped local ``entries_paused:*``
        rejects to venue ``REJECTED`` receipts. That wrote a fake
        ``VenueSubmitAttempted`` plus ``SubmitRejected(pre_submit_rejection=0)``
        into the live-order aggregate, then consumed the opportunity event as
        processed. The fixed boundary emits ``PRE_SUBMIT_ERROR`` with no venue
        attempt; this recovery only revives the old malformed shape so the
        normal reactor path can re-decide it.
        """

        self._require_world_event_tables()
        limit = max(1, min(int(batch_limit or 100), 1000))
        now = _utc_now()
        recent_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        self.conn.execute(
            """
            WITH malformed AS (
                SELECT DISTINCT json_extract(loe.payload_json, '$.event_id') AS event_id
                  FROM edli_live_order_events loe
                  JOIN opportunity_events e
                    ON e.event_id = json_extract(loe.payload_json, '$.event_id')
                 WHERE loe.event_type = 'SubmitRejected'
                   AND COALESCE(json_extract(loe.payload_json, '$.reason_code'), '') LIKE 'entries_paused:%'
                   AND COALESCE(json_extract(loe.payload_json, '$.pre_submit_rejection'), 0) = 0
                   AND COALESCE(json_extract(loe.payload_json, '$.venue_order_id'), '') = ''
                   AND e.created_at >= ?
                 LIMIT ?
            )
            UPDATE opportunity_event_processing
               SET processing_status = 'pending',
                   claimed_at = NULL,
                   processed_at = NULL,
                   last_error = 'RECOVERED_MISCLASSIFIED_LOCAL_PRESUBMIT_REJECTION',
                   updated_at = ?
             WHERE consumer_name = ?
               AND processing_status = 'processed'
               AND event_id IN (SELECT event_id FROM malformed WHERE event_id IS NOT NULL)
            """,
            (recent_cutoff, limit, now, self.consumer_name),
        )
        row = self.conn.execute("SELECT changes()").fetchone()
        return int(row[0] or 0) if row is not None else 0

    def requeue_processed_day0_entries_paused(
        self,
        *,
        decision_time: str,
        batch_limit: int = 500,
    ) -> int:
        """Reopen Day0 facts that were consumed only because entries were paused.

        ``DAY0_EXTREME_UPDATED`` is an immutable observation fact. If the latest
        decision for that fact was a runtime ``pause_entries``/``entries_paused``
        block, the event must re-enter the money path once the pause clears;
        otherwise unchanged observation watermarks and event idempotency make the
        post-pause system silently skip same evidence. Requeue only when that
        pause block is the latest no-trade verdict for the event and the city's
        local target day is still open.
        """

        self._require_world_event_tables()
        if not _table_exists(self.conn, "no_trade_regret_events"):
            return 0
        decision_time_utc = _parse_utc(decision_time)
        recent_floor = (
            decision_time_utc - timedelta(hours=_DAY0_PAUSE_REQUEUE_LOOKBACK_HOURS)
        ).isoformat()
        limit = max(1, min(int(batch_limit or 500), 5000))
        rows = self.conn.execute(
            """
            SELECT e.event_id,
                   json_extract(e.payload_json, '$.city') AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date,
                   n.rejection_reason
              FROM opportunity_events e
              JOIN opportunity_event_processing p
                ON p.consumer_name = ?
               AND p.event_id = e.event_id
              JOIN no_trade_regret_events n
                ON n.event_id = e.event_id
             WHERE p.processing_status = 'processed'
               AND e.event_type = 'DAY0_EXTREME_UPDATED'
               AND e.available_at >= ?
               AND n.created_at = (
                   SELECT MAX(n2.created_at)
                     FROM no_trade_regret_events n2
                    WHERE n2.event_id = e.event_id
               )
               AND (
                    n.rejection_reason LIKE '%entries_paused%'
                 OR n.rejection_reason LIKE '%pause_entries%'
               )
             ORDER BY n.created_at DESC
             LIMIT ?
            """,
            (self.consumer_name, recent_floor, limit),
        ).fetchall()

        recover: list[str] = []
        for event_id, city, target_date, _reason in rows:
            if not event_id:
                continue
            if self._strictly_past_in_tz(
                str(city or "").strip(),
                str(target_date or "").strip(),
                decision_time_utc,
            ):
                continue
            recover.append(str(event_id))

        if not recover:
            return 0

        now = _utc_now()
        for event_id in recover:
            self.conn.execute(
                """
                UPDATE opportunity_event_processing
                   SET processing_status = 'pending',
                       claimed_at = NULL,
                       processed_at = NULL,
                       last_error = 'RECOVERED_DAY0_ENTRIES_PAUSED',
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id = ?
                   AND processing_status = 'processed'
                """,
                (now, self.consumer_name, event_id),
            )
        return len(recover)

    def requeue_false_static_venue_close_day0_dead_letters(
        self,
        *,
        decision_time: str,
        batch_limit: int = 500,
    ) -> int:
        """Recover Day0 events killed by the old static F1 venue-close horizon.

        The removed bug dead-lettered same-day ``DAY0_EXTREME_UPDATED`` rows with
        ``MARKET_VENUE_CLOSED: ... F1 12:00-UTC close`` even when the target local
        day was still active and Gamma/CLOB still reported accepting orders. This
        is a bounded automatic recovery, not an operator migration: only the exact
        old static-close signature is revived, and rows whose local target day is
        already strictly past remain terminal.
        """

        self._require_world_event_tables()
        decision_time_utc = _parse_utc(decision_time)
        # A Day0 target older than UTC yesterday cannot still be inside its
        # local target day in any Zeus market timezone. Keep the final
        # _strictly_past_in_tz authority below, but avoid re-scanning historical
        # dead-letter rows every prune cycle.
        active_target_floor = (decision_time_utc.date() - timedelta(days=1)).isoformat()
        limit = max(1, min(int(batch_limit or 500), 5000))
        dead_letter_rows = self.conn.execute(
            """
            SELECT p.event_id
              FROM opportunity_event_processing p
                   INDEXED BY idx_opportunity_event_processing_status
              CROSS JOIN event_dead_letters d
             WHERE p.consumer_name = ?
               AND p.processing_status = 'dead_letter'
               AND d.consumer_name = p.consumer_name
               AND d.event_id = p.event_id
               AND d.failure_stage = 'MONEY_PATH_HORIZON_EXPIRED'
               AND d.error_message LIKE '%MARKET_VENUE_CLOSED%'
               AND d.error_message LIKE '%F1 12:00-UTC close%'
             ORDER BY d.created_at DESC
             LIMIT ?
            """,
            (self.consumer_name, limit),
        ).fetchall()
        dead_letter_ids = [str(row[0] or "") for row in dead_letter_rows if row[0]]
        if not dead_letter_ids:
            return 0

        placeholders = ",".join("?" for _ in dead_letter_ids)
        rows = self.conn.execute(
            f"""
            SELECT event_id,
                   json_extract(payload_json, '$.city') AS city,
                   json_extract(payload_json, '$.target_date') AS target_date
              FROM opportunity_events
             WHERE event_id IN ({placeholders})
               AND event_type = 'DAY0_EXTREME_UPDATED'
               AND json_extract(payload_json, '$.target_date') >= ?
            """,
            (*dead_letter_ids, active_target_floor),
        ).fetchall()

        recover: list[str] = []
        for event_id, city, target_date in rows:
            if not event_id:
                continue
            if self._strictly_past_in_tz(
                str(city or "").strip(),
                str(target_date or "").strip(),
                decision_time_utc,
            ):
                continue
            recover.append(str(event_id))

        if not recover:
            return 0

        now = _utc_now()
        for event_id in recover:
            self.conn.execute(
                """
                UPDATE opportunity_event_processing
                   SET processing_status = 'pending',
                       claimed_at = NULL,
                       processed_at = NULL,
                       last_error = 'RECOVERED_FALSE_STATIC_VENUE_CLOSE_DAY0',
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id = ?
                   AND processing_status = 'dead_letter'
                """,
                (now, self.consumer_name, event_id),
            )
        return len(recover)

    def requeue_false_executable_snapshot_deadline_day0_dead_letters(
        self,
        *,
        decision_time: str,
        batch_limit: int = 500,
    ) -> int:
        """Recover Day0 events killed by treating stale price deadlines as event life.

        ``EXECUTABLE_SNAPSHOT_STALE:selection_deadline=...`` is selected-book
        freshness evidence. The cure is targeted executable-substrate refresh plus
        retry; it is not a Day0 event horizon. Older runtime rows terminalized these
        as ``MONEY_PATH_HORIZON_EXPIRED:SELECTION_DEADLINE_PAST``. Revive only that
        exact shape while the city-local target day is still active.
        """

        self._require_world_event_tables()
        decision_time_utc = _parse_utc(decision_time)
        # Same conservative Day0 floor as static-close recovery: old historical
        # dead letters are unrecoverable by local-day law and should not be read
        # every live prune cycle.
        active_target_floor = (decision_time_utc.date() - timedelta(days=1)).isoformat()
        limit = max(1, min(int(batch_limit or 500), 5000))
        rows = self.conn.execute(
            """
            SELECT e.event_id,
                   json_extract(e.payload_json, '$.city') AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date
              FROM opportunity_event_processing p
              JOIN opportunity_events e
                ON e.event_id = p.event_id
              JOIN event_dead_letters d
                ON d.consumer_name = p.consumer_name
               AND d.event_id = p.event_id
             WHERE p.consumer_name = ?
               AND p.processing_status = 'dead_letter'
               AND e.event_type = 'DAY0_EXTREME_UPDATED'
               AND json_extract(e.payload_json, '$.target_date') >= ?
               AND d.failure_stage = 'MONEY_PATH_HORIZON_EXPIRED'
               AND d.error_message LIKE '%SELECTION_DEADLINE_PAST%'
               AND d.error_message LIKE '%EXECUTABLE_SNAPSHOT_STALE%'
             LIMIT ?
            """,
            (self.consumer_name, active_target_floor, limit),
        ).fetchall()

        recover: list[str] = []
        for event_id, city, target_date in rows:
            if not event_id:
                continue
            if self._strictly_past_in_tz(
                str(city or "").strip(),
                str(target_date or "").strip(),
                decision_time_utc,
            ):
                continue
            recover.append(str(event_id))

        if not recover:
            return 0

        now = _utc_now()
        for event_id in recover:
            self.conn.execute(
                """
                UPDATE opportunity_event_processing
                   SET processing_status = 'pending',
                       claimed_at = NULL,
                       processed_at = NULL,
                       last_error = 'RECOVERED_FALSE_EXECUTABLE_SNAPSHOT_SELECTION_DEADLINE_DAY0',
                       updated_at = ?
                 WHERE consumer_name = ?
                   AND event_id = ?
                   AND processing_status = 'dead_letter'
                """,
                (now, self.consumer_name, event_id),
            )
        return len(recover)

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
        if self._world_event_tables_ready:
            return
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
        self._world_event_tables_ready = True


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


def _forecast_family_key_from_payload(payload: dict) -> tuple[str, str, str] | None:
    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    metric = str(payload.get("metric") or "").strip().lower()
    if not (city and target_date and metric):
        return None
    return city, target_date, metric


def _parse_optional_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _instant_past(value: object, decision_time_utc: datetime) -> bool:
    parsed = _parse_optional_utc(value)
    if parsed is None:
        return False
    return parsed <= decision_time_utc.astimezone(timezone.utc)


def _deadline_from_reason(reason: object, field_name: str) -> datetime | None:
    text = str(reason or "")
    marker = f"{field_name}="
    start = text.find(marker)
    if start < 0:
        return None
    tail = text[start + len(marker) :]
    for delimiter in (":decision_time=", " ", ",", ";", "|"):
        split_at = tail.find(delimiter)
        if split_at > 0:
            tail = tail[:split_at]
            break
    return _parse_optional_utc(tail)


def _selection_deadline_past(reason: object, decision_time_utc: datetime) -> bool:
    text = str(reason or "")
    if text.split(":", 1)[0].strip() == "EXECUTABLE_SNAPSHOT_STALE":
        return False
    deadline = _deadline_from_reason(reason, "selection_deadline")
    if deadline is None:
        return False
    return deadline <= decision_time_utc.astimezone(timezone.utc)


def _recent_recapture_edge_reversed_families(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row | tuple] | tuple[sqlite3.Row | tuple, ...],
    *,
    decision_time_utc: datetime,
) -> set[tuple[str, str, str]]:
    """Families that just failed submit recapture on a fresh executable book.

    This is a queue-efficiency feedback signal, not a no-trade proof. The reactor
    already consumed the failed event terminally; this short backoff only prevents
    the next ordinary FSR row for the same family from immediately taking another
    bounded-budget slot before other families are reached.
    """

    backoff_seconds = _recapture_edge_backoff_seconds()
    if backoff_seconds <= 0 or not rows or not _table_exists(conn, "no_trade_regret_events"):
        return set()

    families: set[tuple[str, str, str]] = set()
    for row in rows:
        event = _event_from_row(row)
        payload = _event_payload_dict(event)
        redecision_origin = str(payload.get("redecision_origin") or "").strip().lower()
        if not (
            event.event_type == "FORECAST_SNAPSHOT_READY"
            or (
                event.event_type == "EDLI_REDECISION_PENDING"
                and redecision_origin in {"entry_screen", "market_price", ""}
            )
        ):
            continue
        family_key = _forecast_family_key_from_payload(payload)
        if family_key is not None:
            families.add(family_key)
    if not families:
        return set()

    floor = (decision_time_utc - timedelta(seconds=backoff_seconds)).isoformat()
    try:
        regret_rows = conn.execute(
            """
            SELECT city, target_date, metric
              FROM no_trade_regret_events
             WHERE created_at >= ?
               AND rejection_reason LIKE ?
               AND COALESCE(executable_snapshot_id, '') <> ''
            """,
            (floor, f"{_RECENT_RECAPTURE_EDGE_REVERSED_REASON}%"),
        ).fetchall()
    except sqlite3.Error:
        return set()

    cooled: set[tuple[str, str, str]] = set()
    for row in regret_rows:
        key = (
            str(row[0] or "").strip(),
            str(row[1] or "").strip(),
            str(row[2] or "").strip().lower(),
        )
        if key in families:
            cooled.add(key)
    return cooled


def _claim_tier_for_event(
    event: OpportunityEvent,
    payload: dict,
    *,
    day0_is_tradeable: bool,
) -> int:
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


def _pending_row_recapture_edge_backoff(row: sqlite3.Row | tuple) -> int:
    if isinstance(row, sqlite3.Row):
        try:
            raw = row["_recapture_edge_backoff"]
        except (IndexError, KeyError):
            raw = None
    else:
        index = len(_EVENT_ROW_KEYS) + 1
        raw = row[index] if len(row) > index else None
    return _safe_bool_int(raw)


def _pending_row_stale_processing_reclaim(row: sqlite3.Row | tuple) -> int:
    if isinstance(row, sqlite3.Row):
        try:
            raw = row["_stale_processing_reclaim"]
        except (IndexError, KeyError):
            raw = None
    else:
        index = len(_EVENT_ROW_KEYS) + 2
        raw = row[index] if len(row) > index else None
    return _safe_bool_int(raw)


def _live_redecision_retry_lane(event: OpportunityEvent, attempt_count: int) -> int:
    """Claim lane for live redecision rows already carrying retry debt.

    A requeued ``EDLI_REDECISION_PENDING`` row is not ordinary discovery work:
    it represents a family with live money-path work already in progress
    (rest-management, held-position re-evaluation, or a shift/fill-up lease).
    Keep it ahead of fresh tier-0 forecast/day0 work so a cancel/price-move
    retry cannot disappear behind the normal city round-robin budget.
    """

    if event.event_type == "EDLI_REDECISION_PENDING" and attempt_count > 0:
        return 0
    return 1


def _rank_pending_rows_python(
    rows: list[sqlite3.Row | tuple] | tuple[sqlite3.Row | tuple, ...],
    *,
    day0_is_tradeable: bool,
    targeted_event_ids: frozenset[str] = frozenset(),
) -> list[tuple[OpportunityEvent, int]]:
    records: list[dict] = []
    for row in rows:
        event = _event_from_row(row)
        attempt_count = _pending_row_attempt_count(row)
        recapture_edge_backoff = _pending_row_recapture_edge_backoff(row)
        stale_processing_reclaim = _pending_row_stale_processing_reclaim(row)
        payload = _event_payload_dict(event)
        records.append(
            {
                "event": event,
                "attempt_count": attempt_count,
                "recapture_edge_backoff": recapture_edge_backoff,
                "stale_processing_reclaim": stale_processing_reclaim,
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
                "stale_processing_reclaim_lane": (
                    0 if stale_processing_reclaim else 1
                ),
                "global_winner_target_lane": (
                    0 if event.event_id in targeted_event_ids else 1
                ),
                "live_redecision_retry_lane": _live_redecision_retry_lane(
                    event, attempt_count
                ),
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
            item["stale_processing_reclaim_lane"],
            item["global_winner_target_lane"],
            item["tier"],
            item["live_redecision_retry_lane"],
            item["recapture_edge_backoff"],
            item.get("city_round", 1),
            -int(getattr(item["event"], "priority", 0) or 0),
            item["target_key"],
            item["available_key"],
            item["retry_key"],
            item["received_key"],
            item["event"].event_id,
        ),
    )
    ranked = _fair_decision_lane_interleave(ranked)
    return [(item["event"], int(item["attempt_count"])) for item in ranked]


def _is_forecast_decision_lane_item(item: dict) -> bool:
    event = item["event"]
    event_type = getattr(event, "event_type", "")
    if event_type == "EDLI_REDECISION_PENDING":
        return True
    return event_type == "FORECAST_SNAPSHOT_READY" and int(item.get("tier", 99)) <= 1


def _fair_decision_lane_interleave(records: list[dict]) -> list[dict]:
    """Keep the forecast/redecision lane visible under a Day0 Tier-0 flood.

    Reactor-level interleave only works if the fetched page already contains both
    lanes. Live can run with a work limit near one event while hundreds of current
    Day0 observations sit ahead of FSR rows, so the fairness boundary must be the
    store's final claim order, before ``limit`` is applied. If a forecast or
    redecision row exists, it takes the first slot; otherwise a one-event budget
    still gives the whole cycle to Day0 and the entry/redecision lane remains
    invisible.
    """

    # Stale processing recovery remains the absolute first lane. A globally
    # selected family targeted for its first legal claim comes immediately after
    # it; the ordinary forecast/Day0 fairness weave must not page that winner out
    # again merely because its carrier is Day0.
    fixed = [
        item
        for item in records
        if item["stale_processing_reclaim_lane"] == 0
        or item["global_winner_target_lane"] == 0
    ]
    fixed_ids = {item["event"].event_id for item in fixed}
    records = [item for item in records if item["event"].event_id not in fixed_ids]
    forecast = [item for item in records if _is_forecast_decision_lane_item(item)]
    if not forecast:
        return fixed + records
    rest = [item for item in records if not _is_forecast_decision_lane_item(item)]
    if not rest:
        return fixed + records
    out: list[dict] = []
    i = j = 0
    while i < len(forecast) or j < len(rest):
        if i < len(forecast):
            out.append(forecast[i])
            i += 1
        if j < len(rest):
            out.append(rest[j])
            j += 1
    return fixed + out


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
    """Static F1 close is no longer a processing-expiry band.

    Keep the helper as a harmless far-past bound for older query structure:
    rows are now expired by the local-day floor, not by Gamma ``endDate``.
    """
    return "0001-01-01"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event store timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
