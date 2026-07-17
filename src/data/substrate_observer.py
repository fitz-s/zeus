# Created: 2026-06-08
# Last reused or audited: 2026-07-11
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.1 (Executable-Substrate Observer), §6 (P2 row), §7 I1 (no-back-coupling),
#   §8 Step 1 (lift + DELETE outer pending gates), §9 (regression-unconstructable proof).
"""P2 substrate-observer producer logic — lifted out of the order daemon (src.main).

This module owns the EXECUTABLE-SUBSTRATE producer that writes the snapshot tables
(``executable_market_snapshots`` / ``book_hash_transitions`` on trades.db) the order
runtime only READS. It is the WRITER side of interface I1; the order runtime is a pure
READER (``_latest_snapshot_rows_for_event_family``).

WHY IT LIVES HERE (and NOT in src.main):
  - It is ALWAYS_ON (criterion 1): the substrate must keep being captured even when no
    trading happens. Hosting it in the order daemon let the EDLI reactor's pending
    backlog gate substrate capture — the zero-trade coverage-collapse regression
    (system_decomposition_plan §0). In its own process there is no reactor handle and
    no ``pending_count`` to reference, so ``if pending: skip-capture`` is UN-WRITABLE
    across the process boundary (§9 point 1).
  - It is failure-domain-isolated (criterion 3): this module imports NO trading lane
    (src.main / src.engine / src.execution / src.strategy / src.signal / src.control),
    so a Gamma/CLOB fetch error here cannot raise into the reactor, and a trading bug
    cannot blind substrate capture.

THE TWO LIFTED JOBS SHARE ONE LOCK (system_decomposition_plan §4.1):
  ``_market_discovery_cycle`` (universe sweep) and ``_edli_market_substrate_warm_cycle``
  (pending-family warm) BOTH acquire the module-global ``_market_substrate_refresh_lock``
  so they cannot race-write the snapshot table. They MUST run in ONE process; that is
  why they are lifted together into this one module / daemon.

OUTER PENDING GATES DELETED (system_decomposition_plan §0/§8 Step 1/§9): the universe
  sweep's old ``if _edli_reactor_active(): return`` and
  ``if pending_count > 0 and recent_discovery: return`` gates are GONE. The producer's
  sole trigger is substrate STALENESS, keyed on the producer-local
  ``_market_discovery_last_completed_monotonic`` clock.

PENDING-FAMILY SCOPE IS DB-MEDIATED, NOT QUEUE-GATED (§4.1/§7 I1): the warmer scopes its
  WORKLOAD to pending families by SELECTing world-DB rows
  (``opportunity_event_processing WHERE processing_status='pending'`` via
  ``_pending_family_rows_for_refresh``) — a queryable TABLE, never an in-process queue
  handle. A reactor backlog changes WHICH families it prioritizes, never WHETHER it
  fires. The same SELECT helper is reused (cross-process, by re-reading the same table)
  by src.main's mainstream warmer, which STAYS in P1.

INV-37: the producer WRITE is single-DB (trades.db only) via ``get_trade_connection`` —
  the cross-DB ATTACH+SAVEPOINT rule is not triggered (no cross-DB write). The only
  cross-DB touch is a READ-ONLY ATTACH of forecasts for topology lookup. No independent
  cross-DB connection is opened.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

from src.config import settings
from src.data.substrate_priority import (
    money_path_substrate_priority_active,
    money_path_substrate_priority_condition_ids,
    money_path_substrate_priority_families,
    money_path_substrate_priority_request,
    record_money_path_substrate_priority_receipt,
)
from src.contracts.canonical_lifecycle import VenueOrderStatus
from src.contracts.position_truth import CURRENT_MONEY_RISK_CHAIN_STATES

logger = logging.getLogger("zeus.substrate_observer")

# In-process locks shared by the two lifted jobs (system_decomposition_plan §4.1):
# both writers serialize through ``_market_substrate_refresh_lock`` so they cannot
# race-write ``executable_market_snapshots``. ``_market_discovery_lock`` prevents a
# universe sweep from overlapping itself.
_market_discovery_lock = threading.Lock()
_market_substrate_refresh_lock = threading.Lock()
# Producer-local staleness clock — the SOLE trigger for the universe sweep after the
# outer pending gates were deleted (§9 point 2). Never references consumer state.
_market_discovery_last_completed_monotonic: float | None = None
_SUBSTRATE_REFRESH_CURSOR = 0
_SUBSTRATE_PRIORITY_REFRESH_CURSOR = 0
_SUBSTRATE_GAMMA_REFRESH_CURSOR = 0
_GAMMA_EMPTY_BACKOFF_UNTIL: dict[tuple[str, str, str], float] = {}
_NEW_FAMILY_CONDITION_IDS: set[str] = set()
# The sidecar has a short freshness budget, but the outer write-coordinator
# lease cannot be shorter than the row-level SQLite busy wait used by
# market_scanner. Otherwise the coordinator fails first and the substrate lane
# never reaches the bounded SQLite wait that was added to survive transient
# zeus_trades.db writer contention. Live evidence after the sidecar split
# showed 5s is still too brittle when executor, exit, and capital writers are
# active; 8s keeps the wait bounded inside the 20s warm cadence while giving
# scoped hot refresh a realistic chance to acquire the trade write lane.
SUBSTRATE_SNAPSHOT_DB_WRITE_LEASE_DEADLINE_MS = 8000
SUBSTRATE_SNAPSHOT_DB_WRITE_MAX_HOLD_MS = 8000


def _substrate_snapshot_sqlite_busy_floor_ms() -> int:
    raw = os.environ.get("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS")
    try:
        value = int(raw) if raw is not None else 4000
    except (TypeError, ValueError):
        value = 4000
    return max(1000, min(value, 30000))


def _substrate_snapshot_write_lease_deadline_default_ms() -> int:
    return max(
        SUBSTRATE_SNAPSHOT_DB_WRITE_LEASE_DEADLINE_MS,
        _substrate_snapshot_sqlite_busy_floor_ms() + 1000,
    )


def _substrate_snapshot_write_lease_ms(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.environ.get(f"ZEUS_{name.upper()}")
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _substrate_snapshot_trade_write_context_factory(owner: str):
    def _factory():
        from src.state.write_coordinator import DBIdentity, default_runtime_write_coordinator

        return default_runtime_write_coordinator().lease(
            (DBIdentity.TRADE,),
            owner=owner,
            write_class="live",
            deadline_ms=_substrate_snapshot_write_lease_ms(
                "substrate_snapshot_db_write_lease_deadline_ms",
                _substrate_snapshot_write_lease_deadline_default_ms(),
                minimum=_substrate_snapshot_sqlite_busy_floor_ms(),
                maximum=30000,
            ),
            max_hold_ms=_substrate_snapshot_write_lease_ms(
                "substrate_snapshot_db_write_max_hold_ms",
                SUBSTRATE_SNAPSHOT_DB_WRITE_MAX_HOLD_MS,
                minimum=_substrate_snapshot_sqlite_busy_floor_ms(),
                maximum=10000,
            ),
        )

    return _factory


def _substrate_clob_timeout_seconds() -> float:
    """Short public-CLOB timeout for background substrate refresh.

    This lane is a continuously retried producer. It must finish inside the
    warm cadence; a missing orderbook is cheaper to retry next tick than to let
    one slow public CLOB read starve every pending family. The default still
    has to clear the measured cold TLS handshake envelope for clob.polymarket.com;
    the public client documents ~2.2-2.7s cold handshakes, so 1.5s made the live
    priority refresh fail before it could establish a connection.
    """

    return max(
        1.0,
        float(os.environ.get("ZEUS_SUBSTRATE_CLOB_TIMEOUT_SECONDS", "4.0")),
    )


def _market_unavailable_evidence_ttl_seconds() -> float:
    """TTL for sidecar-written market-unavailable evidence consumed by reactor."""

    return max(
        300.0,
        float(os.environ.get("ZEUS_REACTOR_MARKET_UNAVAILABLE_EVIDENCE_SECONDS", "1800.0")),
    )


def _background_warm_refresh_budget_seconds() -> float:
    """Bound background warm lock occupancy so money-path refresh gets windows."""
    configured = max(
        5.0,
        float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0")),
    )
    # Keep the sidecar under the 20s cadence while still giving the topology,
    # CLOB, and trade-write phases enough wall-clock to commit real coverage.
    # A 6s cap looked conservative but live evidence showed it repeatedly
    # exhausted topology before any snapshot could be written.
    background_cap = max(
        10.0,
        float(os.environ.get("ZEUS_SUBSTRATE_BACKGROUND_REFRESH_BUDGET_SECONDS", "14.0")),
    )
    return min(configured, background_cap)


def _background_warm_snapshot_reserve_seconds(refresh_budget_s: float) -> float:
    configured = max(
        1.0,
        float(os.environ.get("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", "12.0")),
    )
    background_cap = max(
        4.0,
        float(os.environ.get("ZEUS_SUBSTRATE_BACKGROUND_SNAPSHOT_RESERVE_SECONDS", "5.0")),
    )
    return min(configured, background_cap, max(0.1, refresh_budget_s - 0.1))


def _priority_refresh_interval_seconds() -> float:
    return max(
        5.0,
        float(os.environ.get("ZEUS_SUBSTRATE_PRIORITY_REFRESH_INTERVAL_SECONDS", "20.0")),
    )


def _priority_refresh_budget_seconds() -> float:
    interval_s = _priority_refresh_interval_seconds()
    configured = max(
        2.0,
        float(os.environ.get("ZEUS_SUBSTRATE_PRIORITY_REFRESH_BUDGET_SECONDS", "18.0")),
    )
    return min(configured, max(1.0, interval_s - 0.5))


def _priority_refresh_lock_wait_seconds() -> float:
    """Bounded wait so a hot priority tick is not lost behind broad substrate work."""

    raw = os.environ.get("ZEUS_SUBSTRATE_PRIORITY_LOCK_WAIT_SECONDS", "6.0")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 6.0
    return max(0.0, min(value, 15.0))


def _inline_refresh_lock_wait_seconds() -> float:
    """Bounded wait long enough for existing background work to release the lock."""

    default_wait_s = min(20.0, _background_warm_refresh_budget_seconds() + 1.0)
    raw = os.environ.get(
        "ZEUS_MONEY_PATH_INLINE_SUBSTRATE_LOCK_WAIT_SECONDS",
        str(default_wait_s),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default_wait_s
    return max(0.0, min(value, 20.0))


def _priority_snapshot_reserve_seconds(refresh_budget_s: float) -> float:
    configured = max(
        0.5,
        float(os.environ.get("ZEUS_SUBSTRATE_PRIORITY_SNAPSHOT_RESERVE_SECONDS", "2.0")),
    )
    return min(configured, max(0.1, refresh_budget_s - 0.1))


def _settings_section(name: str, default=None):
    """Mirror of src.main._settings_section (precedent: replacement_forecast_production)."""
    source = settings._data if hasattr(settings, "_data") else settings
    if isinstance(source, dict):
        value = source.get(name)
        if value is None and name == "edli_v1":
            value = source.get("edli")
        return value if value is not None else default
    try:
        return source[name]
    except KeyError:
        if name == "edli_v1":
            try:
                return source["edli"]
            except KeyError:
                pass
        return default


def _pending_family_rows_for_refresh(
    world_conn,
    *,
    consumer_name: str,
    now_utc: datetime | None = None,
):
    from src.events.event_store import EventStore, _oceania_frontier_target_floor

    event_window_limit = max(
        100,
        min(
            10000,
            int(os.environ.get("ZEUS_PENDING_FAMILY_REFRESH_EVENT_WINDOW_LIMIT", "2000")),
        ),
    )
    decision_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
    stale_target_floor = _oceania_frontier_target_floor(decision_utc)
    rows = world_conn.execute(
        """
        WITH pending AS (
            SELECT p.event_id,
                   p.last_error
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            JOIN opportunity_events e ON e.event_id = p.event_id
            WHERE p.consumer_name = ?
              AND (
                    p.processing_status = 'pending'
                    OR (
                        p.processing_status = 'processing'
                        AND COALESCE(p.last_error, '') <> ''
                    )
                  )
              AND (p.claimed_at IS NULL OR p.claimed_at <= ?)
              AND (
                    e.event_type NOT IN (
                        'FORECAST_SNAPSHOT_READY',
                        'EDLI_REDECISION_PENDING',
                        'DAY0_EXTREME_UPDATED'
                    )
                    OR json_extract(e.payload_json, '$.target_date') IS NULL
                    OR json_extract(e.payload_json, '$.target_date') >= ?
              )
            ORDER BY p.updated_at DESC
            LIMIT ?
        )
        SELECT
            json_extract(e.payload_json, '$.city')        AS city,
            json_extract(e.payload_json, '$.target_date') AS target_date,
            json_extract(e.payload_json, '$.metric')      AS metric,
            MAX(CASE e.event_type
                  WHEN 'DAY0_EXTREME_UPDATED' THEN 4
                  WHEN 'EDLI_REDECISION_PENDING' THEN 3
                  WHEN 'FORECAST_SNAPSHOT_READY' THEN 2
                  ELSE 1
                END) AS refresh_urgency,
            MAX(CASE
                  WHEN e.event_type = 'DAY0_EXTREME_UPDATED'
                   AND COALESCE(p.last_error, '') LIKE '%DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE%'
                  THEN 1 ELSE 0
                END) AS day0_hourly_blocked
        FROM pending p
        JOIN opportunity_events e ON e.event_id = p.event_id
        GROUP BY city, target_date, metric
        -- Refresh live-money urgency first. Day0 hard facts and price-driven
        -- redecisions are the rows whose stale executable substrate directly
        -- blocks hold/exit/shift/new-entry decisions. Target date remains a
        -- freshness tiebreak, not the primary ordering law; otherwise future
        -- families can bury same-day Day0 rows.
        ORDER BY
            MAX(CASE e.event_type
                  WHEN 'DAY0_EXTREME_UPDATED' THEN 4
                  WHEN 'EDLI_REDECISION_PENDING' THEN 3
                  WHEN 'FORECAST_SNAPSHOT_READY' THEN 2
                  ELSE 1
                END) DESC,
            MAX(CASE
                  WHEN e.event_type = 'DAY0_EXTREME_UPDATED'
                   AND COALESCE(p.last_error, '') LIKE '%DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE%'
                  THEN 1 ELSE 0
                END) DESC,
            MAX(e.priority) DESC,
            MAX(e.available_at) DESC,
            MAX(json_extract(e.payload_json, '$.target_date')) DESC,
            MIN(e.event_id) ASC
        """,
        (consumer_name, decision_utc.isoformat(), stale_target_floor, event_window_limit),
    ).fetchall()
    return [
        row
        for row in rows
        if not EventStore._strictly_past_in_tz(
            str(row[0] or "").strip(),
            str(row[1] or "").strip(),
            decision_utc,
        )
    ]


_CLAIM_ORDER_PRIORITY_DEFAULT_FAMILY_LIMIT = 4
_CLAIM_ORDER_PRIORITY_MAX_FAMILY_LIMIT = 16


def _claim_order_priority_family_limit() -> int:
    raw = os.environ.get(
        "ZEUS_SUBSTRATE_CLAIM_PRIORITY_FAMILY_LIMIT",
        str(_CLAIM_ORDER_PRIORITY_DEFAULT_FAMILY_LIMIT),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _CLAIM_ORDER_PRIORITY_DEFAULT_FAMILY_LIMIT
    return max(1, min(_CLAIM_ORDER_PRIORITY_MAX_FAMILY_LIMIT, value))


def _claim_order_priority_families_for_refresh(
    world_conn,
    *,
    consumer_name: str,
    now_utc: datetime,
    limit: int | None = None,
) -> list[tuple[str, str, str]] | None:
    """Families the reactor is actually eligible to claim next, in claim order.

    The broad pending-family query is a backlog surface. It must not decide which
    families get the first substrate budget when some rows are still under a live
    processing lease. Use EventStore.fetch_pending as the single claim-order
    authority and feed that lookahead into the warmer's existing explicit-priority
    lane.
    """

    from src.events.event_store import EventStore

    event_limit = _claim_order_priority_family_limit() if limit is None else max(1, int(limit))
    try:
        events = EventStore(world_conn, consumer_name=consumer_name).fetch_pending(
            decision_time=now_utc.isoformat(),
            limit=event_limit,
            day0_is_tradeable=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EDLI market-substrate warm: claim-order priority read failed (non-fatal): %s",
            exc,
        )
        return None

    families: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        try:
            payload = json.loads(event.payload_json or "{}")
        except Exception:  # noqa: BLE001
            continue
        city = str(payload.get("city") or "").strip()
        target_date = str(payload.get("target_date") or "").strip()
        metric = str(payload.get("metric") or "").strip()
        if not city or not target_date or not metric:
            continue
        key = (
            " ".join(city.replace("-", " ").replace("_", " ").lower().split()),
            target_date,
            " ".join(metric.replace("-", " ").replace("_", " ").lower().split()),
        )
        if key in seen:
            continue
        families.append((city, target_date, metric))
        seen.add(key)
    return families


def _condition_priority_families_for_refresh(
    forecasts_conn,
    condition_ids: Iterable[str],
) -> list[tuple[str, str, str]]:
    """Resolve exact money-path condition ids to refreshable market families.

    Priority markers can carry only concrete condition ids.  The warmer still
    needs a family to reconstruct cached topology or fetch the exact Gamma slug,
    but falling back to broad pending rows turns a scoped live request into a
    backlog sweep.  Use the canonical forecasts.market_events condition map and
    preserve the request order when possible.
    """

    ordered_condition_ids: list[str] = []
    seen_conditions: set[str] = set()
    for raw in condition_ids:
        condition_id = str(raw or "").strip()
        if condition_id and condition_id not in seen_conditions:
            ordered_condition_ids.append(condition_id)
            seen_conditions.add(condition_id)
    if not ordered_condition_ids:
        return []

    rows_by_condition: dict[str, tuple[str, str, str]] = {}
    try:
        for offset in range(0, len(ordered_condition_ids), 500):
            chunk = ordered_condition_ids[offset: offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = forecasts_conn.execute(
                f"""
                SELECT condition_id, city, target_date, temperature_metric
                  FROM market_events
                 WHERE condition_id IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            for row in rows:
                condition_id = str(row[0] or "").strip()
                family = (
                    str(row[1] or "").strip(),
                    str(row[2] or "").strip(),
                    str(row[3] or "").strip(),
                )
                if condition_id and all(family) and condition_id not in rows_by_condition:
                    rows_by_condition[condition_id] = family
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EDLI market-substrate warm: condition priority family read failed (non-fatal): %s",
            exc,
        )
        return []

    families: list[tuple[str, str, str]] = []
    seen_families: set[tuple[str, str, str]] = set()
    for condition_id in ordered_condition_ids:
        family = rows_by_condition.get(condition_id)
        if family and family not in seen_families:
            families.append(family)
            seen_families.add(family)
    return families


def _open_rest_condition_id_from_snapshot(
    trade_conn,
    *,
    token_id: str,
    snapshot_id: str,
) -> str | None:
    try:
        snap_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()
        }
    except Exception:  # noqa: BLE001
        return None
    if "condition_id" not in snap_cols:
        return None
    predicates: list[str] = []
    params: list[str] = []
    if snapshot_id and "snapshot_id" in snap_cols:
        predicates.append("snapshot_id = ?")
        params.append(snapshot_id)
    if token_id:
        for col in ("selected_outcome_token_id", "yes_token_id", "no_token_id"):
            if col in snap_cols:
                predicates.append(f"{col} = ?")
                params.append(token_id)
    if not predicates:
        return None
    order_terms = []
    query_params = [*params]
    if snapshot_id and "snapshot_id" in snap_cols:
        order_terms.append("CASE WHEN snapshot_id = ? THEN 0 ELSE 1 END")
        query_params.append(snapshot_id)
    if "captured_at" in snap_cols:
        order_terms.append("captured_at DESC")
    if "snapshot_id" in snap_cols:
        order_terms.append("snapshot_id DESC")
    order_clause = ", ".join(order_terms) if order_terms else "condition_id DESC"
    try:
        row = trade_conn.execute(
            f"""
            SELECT condition_id
              FROM executable_market_snapshots
             WHERE {" OR ".join(predicates)}
             ORDER BY {order_clause}
             LIMIT 1
            """,
            tuple(query_params),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    condition_id = str(row[0] or "").strip()
    return condition_id or None


def _open_rest_scope_rows_for_refresh(
    trade_conn,
    *,
    forecasts_conn=None,
) -> list[tuple[tuple[str, str, str], str]]:
    """Live unfilled entry rests as (family, condition_id) refresh scope."""

    try:
        command_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(venue_commands)").fetchall()
        }
        fact_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(venue_order_facts)").fetchall()
        }
        position_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(position_current)").fetchall()
        }
        state_select = "state" if "state" in command_cols else "'' AS state"
        state_filter = (
            "AND state IN ('ACKED', 'POST_ACKED', 'PARTIAL')" if "state" in command_cols else ""
        )
        token_select = "token_id" if "token_id" in command_cols else "'' AS token_id"
        snapshot_select = "snapshot_id" if "snapshot_id" in command_cols else "'' AS snapshot_id"
        remaining_select = "remaining_size" if "remaining_size" in fact_cols else "NULL AS remaining_size"
        position_condition_select = (
            "condition_id" if "condition_id" in position_cols else "'' AS condition_id"
        )
        commands = trade_conn.execute(
            f"""
            SELECT command_id, position_id, venue_order_id, {state_select}, {token_select}, {snapshot_select}
              FROM venue_commands
             WHERE intent_kind = 'ENTRY'
               AND venue_order_id IS NOT NULL
               AND venue_order_id != ''
               {state_filter}
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[tuple[str, str, str], str]] = []
    seen: set[tuple[tuple[str, str, str], str]] = set()
    open_states = {"LIVE", "RESTING", "PARTIALLY_MATCHED"}
    for row in commands:
        venue_order_id = str(row[2] or "")
        if not venue_order_id:
            continue
        try:
            fact = trade_conn.execute(
                f"""
                SELECT state, {remaining_select}
                  FROM venue_order_facts
                 WHERE venue_order_id = ?
                 ORDER BY local_sequence DESC
                 LIMIT 1
                """,
                (venue_order_id,),
            ).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if fact is None or str(fact[0] or "") not in open_states:
            continue
        remaining_value = fact[1] if len(fact) > 1 else None
        raw_remaining = "" if remaining_value is None else str(remaining_value).strip()
        if raw_remaining:
            try:
                if float(raw_remaining) <= 0.000001:
                    continue
            except ValueError:
                continue
        if str(fact[0] or "") == VenueOrderStatus.PARTIALLY_MATCHED and not raw_remaining:
            continue
        position_id = str(row[1] or "")
        if not position_id:
            continue
        snapshot_condition_id = _open_rest_condition_id_from_snapshot(
            trade_conn,
            token_id=str(row[4] or ""),
            snapshot_id=str(row[5] or ""),
        )
        try:
            pos = trade_conn.execute(
                f"""
                SELECT city, target_date, temperature_metric, {position_condition_select}
                  FROM position_current
                 WHERE position_id = ?
                   AND phase IN ('pending_entry', 'active', 'day0_window')
                 LIMIT 1
                """,
                (position_id,),
            ).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if pos is None:
            if forecasts_conn is None:
                continue
            resolved = _condition_priority_families_for_refresh(
                forecasts_conn,
                [snapshot_condition_id] if snapshot_condition_id else [],
            )
            family = resolved[0] if resolved else ("", "", "")
            condition_id = snapshot_condition_id or ""
        else:
            family = (
                str(pos[0] or "").strip(),
                str(pos[1] or "").strip(),
                str(pos[2] or "").strip(),
            )
            condition_id = str(pos[3] or "").strip() or (snapshot_condition_id or "")
        key = (family, condition_id)
        if all(family) and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _open_rest_family_rows_for_refresh(
    trade_conn,
    *,
    forecasts_conn=None,
) -> list[tuple[str, str, str]]:
    """Families with live unfilled entry rests that need fresh executable books."""

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for family, _condition_id in _open_rest_scope_rows_for_refresh(
        trade_conn,
        forecasts_conn=forecasts_conn,
    ):
        if family not in seen:
            seen.add(family)
            out.append(family)
    return out


def _open_rest_condition_ids_for_refresh(
    trade_conn,
    *,
    forecasts_conn=None,
) -> list[str]:
    """Exact condition ids for live unfilled entry rests.

    An unfilled venue order needs price redecision for the specific book it is
    resting on.  Expanding it to every sibling bin in the weather family makes
    stale unrelated bins consume the live freshness window.
    """

    out: list[str] = []
    seen: set[str] = set()
    for _family, condition_id in _open_rest_scope_rows_for_refresh(
        trade_conn,
        forecasts_conn=forecasts_conn,
    ):
        condition_id = str(condition_id or "").strip()
        if condition_id and condition_id not in seen:
            seen.add(condition_id)
            out.append(condition_id)
    return out


def _edli_current_held_position_scope_rows() -> list[tuple[tuple[str, str, str], str]]:
    """Current on-chain held positions as (family, condition_id) refresh scope.

    Fail-soft: a producer read failure must not crash the substrate daemon.
    """

    from src.state.db import get_trade_connection_read_only

    try:
        conn = get_trade_connection_read_only()
        try:
            cols = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
            }
            required = {"city", "target_date", "temperature_metric", "phase", "condition_id"}
            if not required.issubset(cols):
                return []
            if "chain_shares" not in cols:
                return []
            open_share_expr = (
                "COALESCE(chain_shares, shares, 0)"
                if "shares" in cols
                else "COALESCE(chain_shares, 0)"
            )
            # Executable substrate refresh is for live/redecision money paths, not
            # historical settlement cleanup. Keep yesterday to avoid UTC/local-date
            # false drops for west-of-UTC markets after 00:00Z; older held rows remain
            # settlement/reconciliation work and must not consume live refresh budget.
            target_floor = (
                datetime.now(timezone.utc).date() - timedelta(days=1)
            ).isoformat()
            # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): the second
            # OR branch below used to also match phase='quarantined' —
            # retired, DB CHECK no longer admits the literal post-migration;
            # 'voided' remains a genuine residual-chain-shares case.
            chain_state_values = tuple(sorted(CURRENT_MONEY_RISK_CHAIN_STATES))
            if "chain_state" in cols:
                chain_state_filter = "AND COALESCE(chain_state, '') IN ({})".format(
                    ",".join("?" for _ in chain_state_values)
                )
                query_params: tuple[object, ...] = (
                    *chain_state_values,
                    target_floor,
                )
            else:
                chain_state_filter = ""
                query_params = (target_floor,)
            rows = conn.execute(
                f"""
                SELECT DISTINCT city, target_date, temperature_metric, condition_id
                  FROM position_current
                 WHERE (
                        (
                            phase IN ('active', 'day0_window', 'pending_exit')
                            AND {open_share_expr} > 0.000001
                            {chain_state_filter}
                        )
                        OR (
                            phase = 'voided'
                            AND COALESCE(chain_shares, 0) > 0.000001
                        )
                       )
                   AND condition_id IS NOT NULL
                   AND TRIM(condition_id) != ''
                   AND target_date >= ?
                """,
                query_params,
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "substrate_observer: held-position family read failed; held families not prioritized this tick: %r",
            exc,
        )
        return []
    out: list[tuple[tuple[str, str, str], str]] = []
    seen: set[tuple[tuple[str, str, str], str]] = set()
    for row in rows:
        family = (
            str(row[0] or "").strip(),
            str(row[1] or "").strip(),
            str(row[2] or "").strip(),
        )
        condition_id = str(row[3] or "").strip()
        key = (family, condition_id)
        if all(family) and condition_id and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _edli_current_held_position_family_keys() -> set[tuple[str, str, str]]:
    """Current held-position families for warmer priority."""

    return {family for family, _condition_id in _edli_current_held_position_scope_rows()}


def _edli_current_held_position_condition_ids() -> list[str]:
    """Exact condition ids for current held positions."""

    out: list[str] = []
    seen: set[str] = set()
    for _family, condition_id in _edli_current_held_position_scope_rows():
        condition_id = str(condition_id or "").strip()
        if condition_id and condition_id not in seen:
            seen.add(condition_id)
            out.append(condition_id)
    return out


def _condition_buy_sides_fresh(write_conn, condition_id: str, fresh_at_iso: str) -> bool:
    from src.state.snapshot_repo import condition_buy_sides_fresh

    return condition_buy_sides_fresh(write_conn, condition_id, fresh_at_iso)


def _conditions_buy_sides_fresh(
    write_conn,
    condition_ids: Iterable[str],
    fresh_at_iso: str,
) -> set[str]:
    """Return fresh conditions with one SQL query per bounded ID chunk."""

    ordered = tuple(
        dict.fromkeys(
            condition_id
            for raw in condition_ids
            if (condition_id := str(raw or "").strip())
        )
    )
    if not ordered:
        return set()

    def _table_exists(table: str) -> bool:
        row = write_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table,),
        ).fetchone()
        return row is not None

    try:
        latest_exists = _table_exists("executable_market_snapshot_latest")
        append_exists = _table_exists("executable_market_snapshots")
        invalidations_exist = _table_exists("executable_market_snapshot_invalidations")
    except sqlite3.Error:
        return set()

    # Minimal test doubles and legacy callers may not expose snapshot tables.
    # Preserve their scalar contract while production uses the bounded path.
    if not latest_exists and not append_exists:
        return {
            condition_id
            for condition_id in ordered
            if _condition_buy_sides_fresh(write_conn, condition_id, fresh_at_iso)
        }

    try:
        variable_limit = write_conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    except (AttributeError, sqlite3.Error):
        variable_limit = 500
    chunk_size = max(1, min(500, int(variable_limit) - 1))

    def _from_table(table: str, scoped: tuple[str, ...]) -> tuple[set[str], set[str]]:
        if not scoped:
            return set(), set()
        state: dict[str, tuple[str, str, set[str]]] = {}
        covered: set[str] = set()
        valid_snapshot = "1"
        if invalidations_exist:
            valid_snapshot = """
              NOT EXISTS (
                    SELECT 1
                      FROM executable_market_snapshot_invalidations inv
                     WHERE inv.invalidated_at >= snapshot.captured_at
                       AND (
                            inv.condition_id = snapshot.condition_id
                            OR inv.token_id = snapshot.selected_outcome_token_id
                            OR inv.token_id = snapshot.yes_token_id
                            OR inv.token_id = snapshot.no_token_id
                       )
              )
            """
        for offset in range(0, len(scoped), chunk_size):
            chunk = scoped[offset: offset + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = write_conn.execute(
                f"""
                SELECT snapshot.condition_id,
                       snapshot.yes_token_id,
                       snapshot.no_token_id,
                       snapshot.selected_outcome_token_id,
                       CASE
                         WHEN snapshot.freshness_deadline >= ?
                          AND ({valid_snapshot})
                         THEN 1 ELSE 0
                       END AS snapshot_is_fresh
                  FROM {table} snapshot
                 WHERE snapshot.condition_id IN ({placeholders})
                 ORDER BY snapshot.condition_id,
                          snapshot.captured_at DESC,
                          snapshot.snapshot_id DESC
                """,
                (fresh_at_iso, *chunk),
            ).fetchall()
            for row in rows:
                condition_id = str(row[0] or "").strip()
                yes = str(row[1] or "").strip()
                no = str(row[2] or "").strip()
                selected = str(row[3] or "").strip()
                if not condition_id:
                    continue
                covered.add(condition_id)
                current = state.get(condition_id)
                if current is None:
                    current = (yes, no, set())
                    state[condition_id] = current
                if selected and bool(row[4]):
                    current[2].add(selected)
        fresh = {
            condition_id
            for condition_id, (yes, no, selected) in state.items()
            if yes and no and yes in selected and no in selected
        }
        return fresh, covered

    try:
        if latest_exists:
            fresh, covered = _from_table(
                "executable_market_snapshot_latest",
                ordered,
            )
        else:
            fresh, covered = set(), set()
        projection_missing = tuple(
            condition_id for condition_id in ordered if condition_id not in covered
        )
        if projection_missing and append_exists:
            append_fresh, _append_covered = _from_table(
                "executable_market_snapshots",
                projection_missing,
            )
            fresh.update(append_fresh)
        return fresh
    except sqlite3.Error:
        # Read failure is not evidence of freshness. The caller will submit the
        # affected conditions for recapture or stop at its absolute deadline.
        return set()


def _prune_fresh_market_outcomes_for_snapshot_refresh(
    write_conn,
    markets: list[dict],
    *,
    fresh_at_iso: str,
    restrict_to_condition_ids: Iterable[str] | None = None,
    force_refresh_condition_ids: Iterable[str] | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[list[dict], int, int]:
    scoped_conditions = {
        str(condition_id or "").strip()
        for condition_id in (restrict_to_condition_ids or ())
        if str(condition_id or "").strip()
    }
    forced_conditions = {
        str(condition_id or "").strip()
        for condition_id in (force_refresh_condition_ids or ())
        if str(condition_id or "").strip()
    }
    if not forced_conditions.issubset(scoped_conditions):
        raise ValueError("forced refresh conditions must be inside the exact condition scope")
    freshness_candidates: list[str] = []
    for market in markets:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError("snapshot freshness prune deadline exceeded")
        market_condition_ids = {
            str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
            for outcome in market.get("outcomes", []) or []
            if isinstance(outcome, dict)
            and str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
        }
        restrict_this_market = bool(scoped_conditions and market_condition_ids & scoped_conditions)
        for outcome in market.get("outcomes", []) or []:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise TimeoutError("snapshot freshness prune deadline exceeded")
            if not isinstance(outcome, dict):
                continue
            condition_id = str(
                outcome.get("condition_id") or outcome.get("market_id") or ""
            ).strip()
            if restrict_this_market and condition_id not in scoped_conditions:
                continue
            if condition_id and condition_id not in forced_conditions:
                freshness_candidates.append(condition_id)
    fresh_conditions = _conditions_buy_sides_fresh(
        write_conn,
        freshness_candidates,
        fresh_at_iso,
    )
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise TimeoutError("snapshot freshness prune deadline exceeded")

    pruned: list[dict] = []
    fresh_conditions_skipped = 0
    stale_conditions_submitted = 0
    for market in markets:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise TimeoutError("snapshot freshness prune deadline exceeded")
        market_condition_ids = {
            str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
            for outcome in market.get("outcomes", []) or []
            if isinstance(outcome, dict)
            and str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
        }
        restrict_this_market = bool(scoped_conditions and market_condition_ids & scoped_conditions)
        stale_outcomes: list[dict] = []
        for outcome in market.get("outcomes", []) or []:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise TimeoutError("snapshot freshness prune deadline exceeded")
            if not isinstance(outcome, dict):
                continue
            cid = str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
            if restrict_this_market and cid not in scoped_conditions:
                continue
            if cid in fresh_conditions:
                fresh_conditions_skipped += 1
                continue
            stale_outcomes.append(outcome)
            stale_conditions_submitted += 1
        if not stale_outcomes:
            continue
        cloned = dict(market)
        cloned["outcomes"] = stale_outcomes
        if "condition_ids" in cloned:
            cloned["condition_ids"] = [
                str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
                for outcome in stale_outcomes
                if str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
            ]
        pruned.append(cloned)
    return pruned, fresh_conditions_skipped, stale_conditions_submitted
def _gamma_lookup_deadline_for_snapshot_refresh(
    *,
    refresh_deadline: float,
    refresh_budget_s: float,
    snapshot_reserve_s: float,
    cached_topology_count: int,
    gamma_family_count: int = 0,
) -> float:
    pre_capture_deadline = refresh_deadline - snapshot_reserve_s
    if cached_topology_count > 0 and gamma_family_count <= 0:
        cached_gamma_s = max(
            0.1,
            float(os.environ.get("ZEUS_REACTOR_CACHED_TOPOLOGY_GAMMA_SECONDS", "1.0")),
        )
        return min(pre_capture_deadline, refresh_deadline - refresh_budget_s + cached_gamma_s)
    return pre_capture_deadline
def _topology_lookup_deadline_for_snapshot_refresh(
    *,
    refresh_deadline: float,
    refresh_budget_s: float,
    snapshot_reserve_s: float,
) -> float:
    """Stop topology reconstruction early enough to attempt direct Gamma lookup."""

    pre_capture_deadline = refresh_deadline - snapshot_reserve_s
    available_pre_capture_s = max(0.0, refresh_budget_s - snapshot_reserve_s)
    gamma_min_slice_s = max(
        0.0,
        float(os.environ.get("ZEUS_REACTOR_GAMMA_LOOKUP_MIN_SECONDS", "2.0")),
    )
    # Gamma is only needed for families whose topology is missing. In steady-state
    # live operation the money-path families already have cached topology, so the
    # Gamma reserve must not consume the whole pre-capture window and collapse the
    # topology scan to one family per tick.
    gamma_min_slice_s = min(gamma_min_slice_s, available_pre_capture_s * 0.5)
    return max(refresh_deadline - refresh_budget_s, pre_capture_deadline - gamma_min_slice_s)
def _snapshot_capture_budget_for_refresh(
    *,
    refresh_deadline: float,
    snapshot_reserve_s: float,
) -> float:
    """Return the CLOB capture slice for pending-family snapshot refresh.

    The warm job has two qualitatively different phases: cheap topology/cache
    selection and price capture.  The topology phase is expected to stop early
    enough to leave a capture window; if it fails to do that, the safe behavior is
    to skip capture until the next tick.  Fabricating a fresh CLOB budget after
    the wall-clock deadline makes the APScheduler max_instances=1 guard skip
    every later tick and starves the live substrate.
    """

    min_prefetch_window_s = max(
        0.0,
        float(os.environ.get("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", "0.75")),
    )
    target_prefetch_window_s = max(
        min_prefetch_window_s,
        float(os.environ.get("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_TARGET_WINDOW_SECONDS", "2.0")),
    )
    # refresh_executable_market_substrate_snapshots internally reserves
    # snapshot_reserve_s for the capture loop and admits /books only before that
    # reserve starts. Passing exactly snapshot_reserve_s double-reserves the
    # same phase and makes the batch prefetch deadline effectively immediate.
    # The admission threshold is not enough as a budget: real scheduler/function
    # overhead can burn milliseconds between budget construction and prefetch,
    # making a nominal 0.750s window measure as "below 0.750s" and collapse back
    # to serial /book reads. Keep prefetch as its own small phase budget.
    remaining_s = refresh_deadline - time.monotonic()
    if remaining_s <= 0.0:
        return 0.0
    min_budget_s = snapshot_reserve_s + target_prefetch_window_s
    if remaining_s < min_budget_s:
        logger.info(
            "refresh_pending_family_snapshots: CLOB capture window %.3fs below target %.3fs; "
            "using remaining wall-clock budget rather than extending the scheduler tick",
            remaining_s,
            min_budget_s,
        )
    return remaining_s


def _install_sqlite_deadline(conn, *, deadline_monotonic: float | None) -> bool:
    """Interrupt long SQLite work once this warm tick has spent its budget."""

    if conn is None or deadline_monotonic is None:
        return False

    def _deadline_progress() -> int:
        return 1 if time.monotonic() >= deadline_monotonic else 0

    try:
        conn.set_progress_handler(_deadline_progress, 1_000)
        return True
    except Exception:  # noqa: BLE001
        return False


def _start_sqlite_deadline_interrupt(conn, *, deadline_monotonic: float | None) -> threading.Timer | None:
    """Hard-interrupt SQLite if a single pager read overruns the progress handler."""

    if conn is None or deadline_monotonic is None:
        return None
    remaining_s = deadline_monotonic - time.monotonic()
    if remaining_s <= 0.0:
        try:
            conn.interrupt()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _interrupt() -> None:
        try:
            conn.interrupt()
        except Exception:  # noqa: BLE001
            pass

    timer = threading.Timer(remaining_s, _interrupt)
    timer.daemon = True
    timer.start()
    return timer


def _cancel_sqlite_deadline_interrupt(timer: threading.Timer | None) -> None:
    if timer is not None:
        timer.cancel()


def _clear_sqlite_deadline(conn) -> None:
    if conn is None:
        return
    try:
        conn.set_progress_handler(None, 0)
    except Exception:  # noqa: BLE001
        pass


def _sqlite_budget_expired(deadline_monotonic: float) -> bool:
    return time.monotonic() >= deadline_monotonic


def _claim_order_priority_read_budget_seconds() -> float:
    raw = os.environ.get("ZEUS_SUBSTRATE_CLAIM_PRIORITY_READ_BUDGET_SECONDS", "2.0")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 2.0
    return max(0.1, min(value, 5.0))


def _substrate_priority_receipt(
    *,
    request: dict | None,
    summary: dict | None,
) -> None:
    now = datetime.now(timezone.utc)
    record_money_path_substrate_priority_receipt(
        request=request,
        summary=summary,
        now=now,
    )
    if (
        not isinstance(request, dict)
        or not isinstance(summary, dict)
        or str(summary.get("status") or "") != "refreshed"
        or summary.get("scheduler_failed") is True
    ):
        return
    families = []
    for raw in request.get("families", ()):
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            continue
        family = (
            str(raw[0] or "").strip(),
            str(raw[1] or "").strip(),
            str(raw[2] or "").strip().lower(),
        )
        if all(family) and family[2] in {"high", "low"}:
            families.append(family)
    try:
        from src.runtime.reactor_wake import publish_reactor_wake

        publish_reactor_wake(
            source="substrate_observer",
            reason="money_path_substrate_refreshed",
            published_at=now,
            forecast_families=tuple(families),
        )
    except Exception:
        logger.warning(
            "substrate priority refresh committed but redecision wake publish failed",
            exc_info=True,
        )


def _substrate_warm_failed_summary(
    *,
    status: str,
    reason: str | None = None,
    priority_request: dict | None = None,
    priority_marker_active: bool = False,
) -> dict:
    summary = {
        "status": status,
        "priority_marker_active": bool(priority_marker_active),
        "scheduler_failed": True,
        "scheduler_failure_reason": reason or status,
    }
    if isinstance(priority_request, dict):
        summary["priority_request_id"] = str(priority_request.get("request_id") or "")
        summary["priority_marker_families"] = len(priority_request.get("families") or [])
        summary["priority_marker_condition_ids"] = len(priority_request.get("condition_ids") or [])
    return summary


def _substrate_warm_business_summary(
    summary: dict | None,
    *,
    priority_request: dict | None,
    priority_marker_active: bool,
) -> dict:
    out = dict(summary or {})
    status = str(out.get("status") or "unknown")
    out["priority_marker_active"] = bool(priority_marker_active)
    if status in {"error", "budget_exhausted_before_snapshot_capture", "topology_budget_exhausted"}:
        out["scheduler_failed"] = True
        out["scheduler_failure_reason"] = str(out.get("reason") or status)
    attempted = int(out.get("attempted") or 0)
    inserted = int(out.get("inserted") or 0)
    failed = int(out.get("failed") or 0)
    budget_exhausted = bool(out.get("budget_exhausted"))
    coverage_status = str(out.get("executable_substrate_coverage_status") or "").strip().upper()
    if attempted > 0 and inserted <= 0 and failed > 0:
        out["scheduler_failed"] = True
        out["scheduler_failure_reason"] = "snapshot_write_failed_no_coverage"
    if status == "refreshed" and attempted > 0 and inserted <= 0 and (
        budget_exhausted or coverage_status == "NONE"
    ):
        out["scheduler_failed"] = True
        out["scheduler_failure_reason"] = (
            "snapshot_refresh_exhausted_no_coverage"
            if budget_exhausted
            else "snapshot_refresh_no_executable_coverage"
        )
    if isinstance(priority_request, dict):
        condition_ids = list(priority_request.get("condition_ids") or [])
        families = list(priority_request.get("families") or [])
        out["priority_request_id"] = str(priority_request.get("request_id") or "")
        out["priority_marker_families"] = len(families)
        out["priority_marker_condition_ids"] = len(condition_ids)
        if condition_ids:
            selected = int(out.get("direct_clob_prefetch_selected_priority_condition_count") or 0)
            if status == "refreshed" and selected <= 0:
                stale_conditions = int(out.get("stale_condition_submitted") or 0)
                if inserted <= 0 or stale_conditions > 0:
                    out["scheduler_failed"] = True
                    out["scheduler_failure_reason"] = "priority_conditions_not_serviced"
                else:
                    out["priority_conditions_deferred"] = True
                    out["scheduler_degraded_reason"] = "priority_conditions_deferred"
            elif status in {"error", "budget_exhausted_before_snapshot_capture", "topology_budget_exhausted"}:
                out["scheduler_failed"] = True
                out["scheduler_failure_reason"] = str(out.get("reason") or status)
        elif families and status in {"error", "budget_exhausted_before_snapshot_capture", "topology_budget_exhausted"}:
            out["scheduler_failed"] = True
            out["scheduler_failure_reason"] = str(out.get("reason") or status)
    elif status == "error":
        out["scheduler_failed"] = True
        out["scheduler_failure_reason"] = str(out.get("reason") or status)
    out.setdefault("scheduler_failed", False)
    return out


def _refresh_pending_family_snapshots(
    world_conn,
    forecasts_conn,
    *,
    consumer_name: str = "edli_reactor_v1",
    now_utc: datetime | None = None,
    extra_priority_families: Iterable[tuple[str, str, str]] | None = None,
    include_pending_families: bool = True,
    priority_condition_ids: Iterable[str] | None = None,
    force_refresh_condition_ids: Iterable[str] | None = None,
    refresh_budget_seconds: float | None = None,
    snapshot_reserve_seconds: float | None = None,
    include_money_risk_families: bool = True,
) -> dict:
    """Targeted, cache-aware snapshot refresh for pending opportunity event families.

    Decision-driven design ("先有下单结果再去找市场"):
      - Scope: ONLY the families (city/target_date/metric) of PENDING events.
      - Cache: skip entire families whose ALL bins are still fresh.
      - Discovery: Gamma slug lookup scoped to pending target_dates — discovers
        EVERY bin (incl. never-seen illiquid MECE tail bins) via full token payload.
      - CLOB: max_outcomes=0 requests pending-family capture semantics: urgent
        live-money families get breadth-first YES/NO price coverage before the
        remaining budget completes full family proofs.
        tolerate_missing_book=True (inside refresh) lets illiquid bins snapshot
        as top_ask=None / executable_allowed=False.
      - No universe sweep, no market_discovery, no find_weather_markets.

    Reuses refresh_executable_market_substrate_snapshots write path unchanged.
    Returns a summary dict; never raises (failures are logged and skipped).
    """

    from src.data.market_scanner import (
        reconstruct_weather_market_from_static_topology,
        refresh_executable_market_substrate_snapshots,
    )
    from src.data.market_topology_rows import _event_family_market_topology_rows
    from src.data.polymarket_client import PolymarketClient
    from src.state.db import (
        get_trade_connection,
        get_trade_connection_read_only,
    )

    now_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    priority_conditions = {
        str(condition_id or "").strip()
        for condition_id in (priority_condition_ids or ())
        if str(condition_id or "").strip()
    }
    explicit_priority_conditions = set(priority_conditions)
    forced_conditions = {
        str(condition_id or "").strip()
        for condition_id in (force_refresh_condition_ids or ())
        if str(condition_id or "").strip()
    }
    if not forced_conditions.issubset(explicit_priority_conditions):
        raise ValueError("forced refresh conditions require exact priority scope")

    # Step 1: Collect distinct (city, target_date, metric) for pending events.
    if include_pending_families:
        try:
            pending_rows = _pending_family_rows_for_refresh(
                world_conn, consumer_name=consumer_name, now_utc=now_utc
            )
        except Exception as exc:
            logger.warning("refresh_pending_family_snapshots: pending-event query failed: %s", exc)
            return {"status": "error", "reason": str(exc)}
    else:
        pending_rows = []

    from src.config import cities_by_name as _refresh_cities_by_name

    def _refresh_family_text_key(value: object) -> str:
        text = str(value or "").strip().lower()
        return " ".join(text.replace("-", " ").replace("_", " ").split())

    _refresh_city_alias_to_name: dict[str, str] = {}
    for _city in _refresh_cities_by_name.values():
        for _surface in (
            _city.name,
            *_city.aliases,
            *_city.slug_names,
        ):
            _key = _refresh_family_text_key(_surface)
            if _key:
                _refresh_city_alias_to_name[_key] = _city.name

    def _canonical_refresh_city_name(city: object) -> str:
        raw = str(getattr(city, "name", None) or city or "").strip()
        return _refresh_city_alias_to_name.get(_refresh_family_text_key(raw), raw)

    def _canonical_refresh_metric(metric: object) -> str:
        text = _refresh_family_text_key(metric)
        if text in {"low", "lowest", "min", "minimum"} or text.startswith("lowest "):
            return "low"
        if text in {"high", "highest", "max", "maximum"} or text.startswith("highest "):
            return "high"
        return text

    def _refresh_family_key(city: object, target_date: object, metric: object) -> tuple[str, str, str]:
        return (
            _refresh_family_text_key(_canonical_refresh_city_name(city)),
            str(target_date or "").strip(),
            _canonical_refresh_metric(metric),
        )

    pending_families: list[tuple[str, str, str]] = []
    pending_urgent_families: list[tuple[str, str, str]] = []
    for row in pending_rows:
        city = _canonical_refresh_city_name(row[0])
        target_date = str(row[1] or "").strip()
        metric = _canonical_refresh_metric(row[2])
        try:
            refresh_urgency = int(row[3] or 0)
        except (TypeError, ValueError, IndexError):
            refresh_urgency = 0
        if city and target_date and metric:
            family = (city, target_date, metric)
            if refresh_urgency >= 3:
                pending_urgent_families.append(family)
            else:
                pending_families.append(family)

    open_rest_priority_families: list[tuple[str, str, str]] = []
    if include_money_risk_families:
        try:
            trade_ro = get_trade_connection_read_only()
            try:
                open_rest_priority_families = _open_rest_family_rows_for_refresh(
                    trade_ro,
                    forecasts_conn=forecasts_conn,
                )
            finally:
                trade_ro.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "refresh_pending_family_snapshots: open-rest priority read failed (non-fatal): %s",
                exc,
            )
        held_position_priority_families = sorted(_edli_current_held_position_family_keys())
    else:
        open_rest_priority_families = []
        held_position_priority_families = []

    priority_families: list[tuple[str, str, str]] = []
    priority_keys: set[tuple[str, str, str]] = set()
    explicit_priority_families = list(extra_priority_families or ())
    for family in (
        explicit_priority_families
        + list(open_rest_priority_families)
        + list(held_position_priority_families)
        + pending_urgent_families
    ):
        key = _refresh_family_key(*family)
        if key and key not in priority_keys:
            priority_families.append(family)
            priority_keys.add(key)

    families = priority_families + [
        family for family in pending_families if _refresh_family_key(*family) not in priority_keys
    ]

    if not families:
        return {
            "status": "no_pending_open_rest_or_held_families",
            "open_rest_priority_families": 0,
            "held_position_priority_families": 0,
            "explicit_priority_families": 0,
            "include_pending_families": bool(include_pending_families),
            "include_money_risk_families": bool(include_money_risk_families),
        }

    global _SUBSTRATE_REFRESH_CURSOR, _SUBSTRATE_PRIORITY_REFRESH_CURSOR, _SUBSTRATE_GAMMA_REFRESH_CURSOR, _NEW_FAMILY_CONDITION_IDS, _GAMMA_EMPTY_BACKOFF_UNTIL
    new_priority_families: list[tuple[str, str, str]] = []
    if _NEW_FAMILY_CONDITION_IDS:
        try:
            new_cids_snapshot = set(_NEW_FAMILY_CONDITION_IDS)
            _NEW_FAMILY_CONDITION_IDS.clear()
            for cid in sorted(new_cids_snapshot):
                try:
                    row_q = world_conn.execute(
                        "SELECT city, target_date, temperature_metric FROM market_events WHERE condition_id = ? LIMIT 1",
                        (cid,),
                    ).fetchone()
                    if row_q is not None:
                        city_v, td_v, metric_v = (
                            _canonical_refresh_city_name(row_q[0]),
                            str(row_q[1] or "").strip(),
                            _canonical_refresh_metric(row_q[2]),
                        )
                        fk = _refresh_family_key(city_v, td_v, metric_v)
                        if fk not in {_refresh_family_key(*f) for f in families}:
                            new_priority_families.append((city_v, td_v, metric_v))
                except Exception:
                    pass
        except Exception:
            pass
    ordinary_families = families[len(priority_families):]
    n_priority_families = len(priority_families)
    priority_start_offset = _SUBSTRATE_PRIORITY_REFRESH_CURSOR % max(1, n_priority_families)
    rotated_priority_families = (
        priority_families[priority_start_offset:] + priority_families[:priority_start_offset]
        if priority_families
        else []
    )
    n_ordinary_families = len(ordinary_families)
    start_offset = _SUBSTRATE_REFRESH_CURSOR % max(1, n_ordinary_families)
    rotated_ordinary_families = (
        ordinary_families[start_offset:] + ordinary_families[:start_offset]
        if ordinary_families
        else []
    )
    families = rotated_priority_families + new_priority_families + rotated_ordinary_families

    # Fitz #5 scheduler-liveness fix (2026-06-08): this wall-clock budget MUST be
    # STRICTLY LESS than the warm-cycle APScheduler interval (_EDLI_SUBSTRATE_WARM_
    # INTERVAL_SECONDS, 20s) and MUST stay within the 30s executable-price freshness
    # window. The prior 29.0 default predated the reactor→warm-cycle split (blame
    # 014408394f, sized for the old 1-min reactor interval) and was never re-aligned:
    # a 29s budget on a 20s interval guarantees the cycle overruns its own trigger,
    # so every subsequent run is "skipped: maximum number of running instances
    # reached (1)" (zeus-live.err 2026-06-08) and the universe-wide executable
    # substrate is never refreshed — coverage NONE, daemon starved of candidates.
    # The default now fits inside the interval with headroom for scheduler dispatch
    # and connection teardown; the internal capture reserve (snapshot_reserve_s) and
    # Gamma slice scale down off this budget below. Env-overridable, but the
    # interval-fit invariant is asserted at job registration (see add_job below).
    refresh_budget_s = max(
        5.0,
        float(
            refresh_budget_seconds
            if refresh_budget_seconds is not None
            else os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0")
        ),
    )
    refresh_deadline = time.monotonic() + refresh_budget_s
    snapshot_reserve_s = min(
        max(
            1.0,
            float(
                snapshot_reserve_seconds
                if snapshot_reserve_seconds is not None
                else os.environ.get("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", "12.0")
            ),
        ),
        max(0.1, refresh_budget_s - 0.1),
    )
    topology_deadline = _topology_lookup_deadline_for_snapshot_refresh(
        refresh_deadline=refresh_deadline,
        refresh_budget_s=refresh_budget_s,
        snapshot_reserve_s=snapshot_reserve_s,
    )
    _gamma_empty_backoff_s = max(
        0.0,
        float(os.environ.get("ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS", "300.0")),
    )

    # Throughput contract: never slice pending families by a fixed count. Live has
    # hundreds of active weather families across time zones; a hard family cap lets
    # a small prefix monopolise the freshness window. The wall-clock budget is the
    # only per-tick bound, with a reserved CLOB capture slice so Gamma lookup cannot
    # consume the whole tick and leave snapshot insertion at attempted=0.

    # Step 2: Cache-skip: for each family check whether ALL known condition_ids
    #         (from market_events topology) already have fresh snapshots.
    #         Families with ANY stale/missing bin still proceed to Gamma fetch.
    fresh_skipped = 0
    no_topology = 0
    gamma_refresh_families: list[tuple[str, str, str]] = []
    cached_topology_markets: list[dict] = []
    cached_topology_families = 0
    cached_topology_incomplete = 0
    topology_budget_exhausted = False
    topology_deferred_families = 0
    venue_closed_skipped = 0
    no_topology_backed_off = 0

    snapshot_read_conn = None
    forecasts_deadline_installed = False
    snapshot_deadline_installed = False
    forecasts_deadline_timer: threading.Timer | None = None
    snapshot_deadline_timer: threading.Timer | None = None
    try:
        forecasts_deadline_installed = _install_sqlite_deadline(
            forecasts_conn,
            deadline_monotonic=topology_deadline,
        )
        forecasts_deadline_timer = _start_sqlite_deadline_interrupt(
            forecasts_conn,
            deadline_monotonic=topology_deadline,
        )
        try:
            snapshot_read_conn = get_trade_connection_read_only()
            snapshot_deadline_installed = _install_sqlite_deadline(
                snapshot_read_conn,
                deadline_monotonic=topology_deadline,
            )
            snapshot_deadline_timer = _start_sqlite_deadline_interrupt(
                snapshot_read_conn,
                deadline_monotonic=topology_deadline,
            )
        except Exception as exc:
            logger.warning(
                "refresh_pending_family_snapshots: trade snapshot read unavailable; "
                "treating cached executable substrate as stale: %s",
                exc,
            )
        families_processed_this_cycle = 0
        for index, (city, target_date, metric) in enumerate(families):
            if time.monotonic() >= topology_deadline and (
                cached_topology_markets or gamma_refresh_families
            ):
                topology_budget_exhausted = True
                topology_deferred_families = len(families) - index
                logger.info(
                    "refresh_pending_family_snapshots: topology time-box hit after %d/%d "
                    "families; reserving %.1fs for CLOB capture",
                    index,
                    len(families),
                    snapshot_reserve_s,
                )
                break
            families_processed_this_cycle += 1
            payload = {"city": city, "target_date": target_date, "metric": metric}
            try:
                topology_rows = _event_family_market_topology_rows(forecasts_conn, payload)
            except sqlite3.Error as exc:
                if _sqlite_budget_expired(topology_deadline):
                    topology_budget_exhausted = True
                    topology_deferred_families = len(families) - index
                    logger.info(
                        "refresh_pending_family_snapshots: topology SQLite deadline hit "
                        "after %d/%d families; reserving %.1fs for CLOB capture (%s)",
                        index,
                        len(families),
                        snapshot_reserve_s,
                        exc,
                    )
                    break
                raise
            if not topology_rows:
                no_topology += 1
                nb_key = _refresh_family_key(city, target_date, metric)
                if (
                    _gamma_empty_backoff_s > 0.0
                    and _GAMMA_EMPTY_BACKOFF_UNTIL.get(nb_key, 0.0) > time.monotonic()
                ):
                    no_topology_backed_off += 1
                    continue
                logger.debug(
                    "refresh_pending_family_snapshots: no market topology for %s/%s/%s "
                    "(no Polymarket market for this family — event will be rejected at gate)",
                    city, target_date, metric,
                )
                # Still include: Gamma may discover bins not yet in topology.
                gamma_refresh_families.append((city, target_date, metric))
                continue
            topology_rows = [
                {
                    **dict(trow),
                    "city": city,
                    "target_date": target_date,
                    "temperature_metric": metric,
                }
                for trow in topology_rows
            ]
            scoped_topology_condition_ids = {
                str(trow.get("condition_id") or "").strip()
                for trow in topology_rows
            } & explicit_priority_conditions
            if scoped_topology_condition_ids:
                topology_rows = [
                    trow
                    for trow in topology_rows
                    if str(trow.get("condition_id") or "").strip()
                    in scoped_topology_condition_ids
                ]
            family_key = _refresh_family_key(city, target_date, metric)
            if family_key in priority_keys and not explicit_priority_conditions:
                for trow in topology_rows:
                    cid = str(trow.get("condition_id") or "").strip()
                    if cid:
                        priority_conditions.add(cid)

            any_stale = bool(scoped_topology_condition_ids & forced_conditions)
            if snapshot_read_conn is None:
                any_stale = True
            else:
                try:
                    for trow in topology_rows:
                        cid = str(trow.get("condition_id") or "").strip()
                        if not cid:
                            continue
                        if not _condition_buy_sides_fresh(snapshot_read_conn, cid, now_iso):
                            any_stale = True
                            break
                except sqlite3.Error as exc:
                    if _sqlite_budget_expired(topology_deadline):
                        topology_budget_exhausted = True
                        topology_deferred_families = len(families) - index
                        logger.info(
                            "refresh_pending_family_snapshots: freshness SQLite deadline hit "
                            "after %d/%d families; reserving %.1fs for CLOB capture (%s)",
                            index,
                            len(families),
                            snapshot_reserve_s,
                            exc,
                        )
                        break
                    raise

            if any_stale:
                reconstructed = (
                    reconstruct_weather_market_from_static_topology(
                        snapshot_read_conn,
                        topology_rows=topology_rows,
                        now_utc=now_utc,
                    )
                    if snapshot_read_conn is not None
                    else None
                )
                if reconstructed is not None:
                    cached_topology_markets.append(reconstructed)
                    cached_topology_families += 1
                elif snapshot_read_conn is not None and _sqlite_budget_expired(topology_deadline):
                    topology_budget_exhausted = True
                    topology_deferred_families = len(families) - index
                    logger.info(
                        "refresh_pending_family_snapshots: reconstruction SQLite deadline hit "
                        "after %d/%d families; reserving %.1fs for CLOB capture",
                        index,
                        len(families),
                        snapshot_reserve_s,
                    )
                    break
                else:
                    cached_topology_incomplete += 1
                    gamma_refresh_families.append((city, target_date, metric))
            else:
                fresh_skipped += 1

        _SUBSTRATE_REFRESH_CURSOR = (
            start_offset
            + max(1, max(0, families_processed_this_cycle - len(priority_families)))
        ) % max(1, n_ordinary_families)
        if n_priority_families:
            _SUBSTRATE_PRIORITY_REFRESH_CURSOR = (
                priority_start_offset
                + max(1, min(families_processed_this_cycle, n_priority_families))
            ) % n_priority_families

        if forecasts_deadline_installed:
            _clear_sqlite_deadline(forecasts_conn)
            forecasts_deadline_installed = False
        _cancel_sqlite_deadline_interrupt(forecasts_deadline_timer)
        forecasts_deadline_timer = None
        if snapshot_deadline_installed and snapshot_read_conn is not None:
            _clear_sqlite_deadline(snapshot_read_conn)
            snapshot_deadline_installed = False
        _cancel_sqlite_deadline_interrupt(snapshot_deadline_timer)
        snapshot_deadline_timer = None

        if not gamma_refresh_families and not cached_topology_markets:
            if topology_budget_exhausted:
                logger.info(
                    "refresh_pending_family_snapshots: topology budget exhausted before "
                    "building refreshable markets. families=%d deferred=%d fresh_skipped=%d "
                    "no_topology=%d cached_topology_incomplete=%d",
                    len(families),
                    topology_deferred_families,
                    fresh_skipped,
                    no_topology,
                    cached_topology_incomplete,
                )
                return {
                    "status": "topology_budget_exhausted",
                    "families_checked": len(families),
                    "explicit_priority_families": len(explicit_priority_families),
                    "include_pending_families": bool(include_pending_families),
                    "include_money_risk_families": bool(include_money_risk_families),
                    "open_rest_priority_families": len(open_rest_priority_families),
                    "held_position_priority_families": len(held_position_priority_families),
                    "fresh_skipped": fresh_skipped,
                    "no_topology": no_topology,
                    "venue_closed_skipped": venue_closed_skipped,
                    "no_topology_backed_off": no_topology_backed_off,
                    "cached_topology_incomplete": cached_topology_incomplete,
                    "topology_budget_exhausted": 1,
                    "topology_deferred_families": topology_deferred_families,
                    "refresh_budget_seconds": refresh_budget_s,
                    "snapshot_reserve_seconds": snapshot_reserve_s,
                }
            if venue_closed_skipped:
                no_work_status = (
                    "venue_closed"
                    if venue_closed_skipped == len(families)
                    else "no_refreshable_families"
                )
                logger.info(
                    "refresh_pending_family_snapshots: no refreshable families, skipped. "
                    "status=%s families=%d fresh_skipped=%d venue_closed_skipped=%d "
                    "no_topology=%d no_topology_backed_off=%d cached_topology_incomplete=%d",
                    no_work_status,
                    len(families),
                    fresh_skipped,
                    venue_closed_skipped,
                    no_topology,
                    no_topology_backed_off,
                    cached_topology_incomplete,
                )
                return {
                    "status": no_work_status,
                    "families_checked": len(families),
                    "explicit_priority_families": len(explicit_priority_families),
                    "include_pending_families": bool(include_pending_families),
                    "include_money_risk_families": bool(include_money_risk_families),
                    "open_rest_priority_families": len(open_rest_priority_families),
                    "held_position_priority_families": len(held_position_priority_families),
                    "fresh_skipped": fresh_skipped,
                    "no_topology": no_topology,
                    "venue_closed_skipped": venue_closed_skipped,
                    "no_topology_backed_off": no_topology_backed_off,
                    "cached_topology_incomplete": cached_topology_incomplete,
                }
            logger.info(
                "refresh_pending_family_snapshots: all families fresh, skipped. "
                "families=%d fresh_skipped=%d no_topology=%d venue_closed_skipped=%d "
                "no_topology_backed_off=%d cached_topology_incomplete=%d",
                len(families), fresh_skipped, no_topology, venue_closed_skipped,
                no_topology_backed_off, cached_topology_incomplete,
            )
            return {
                "status": "all_fresh",
                "families_checked": len(families),
                "explicit_priority_families": len(explicit_priority_families),
                "include_pending_families": bool(include_pending_families),
                "include_money_risk_families": bool(include_money_risk_families),
                "open_rest_priority_families": len(open_rest_priority_families),
                "held_position_priority_families": len(held_position_priority_families),
                "fresh_skipped": fresh_skipped,
                "no_topology": no_topology,
                "venue_closed_skipped": venue_closed_skipped,
                "no_topology_backed_off": no_topology_backed_off,
                "cached_topology_incomplete": cached_topology_incomplete,
            }

        # Step 3: Targeted Gamma slug fetch — one request per pending family.
        #         Build the exact slug for each (city, date, metric) and fetch
        #         directly.  This is maximally bounded: N pending families = N
        #         Gamma calls (vs the background slug-pattern scanner which
        #         enumerates all 14 cities × all dates and is budget-capped).
        #         Uses the City's slug_names[0] for the slug fragment.
        gamma_deadline = _gamma_lookup_deadline_for_snapshot_refresh(
            refresh_deadline=refresh_deadline,
            refresh_budget_s=refresh_budget_s,
            snapshot_reserve_s=snapshot_reserve_s,
            cached_topology_count=len(cached_topology_markets),
            gamma_family_count=len(gamma_refresh_families),
        )
        skipped_not_found = 0
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
            from datetime import date as _date_cls
            from src.data.market_scanner import (
                _gamma_get,
                _parse_and_persist_weather_events,
            )

            def _date_to_slug_fragment(date_str: str) -> str:
                d = _date_cls.fromisoformat(date_str)
                return d.strftime("%B-%-d-%Y").lower()

            raw_events_seen: set = set()
            raw_events_collected: list[dict] = []
            gamma_slug_attempted = 0
            gamma_slug_empty = 0
            gamma_slug_http_non_200 = 0
            gamma_slug_failed = 0
            gamma_slug_invalid = 0
            gamma_slug_timebox_unattempted = 0
            gamma_empty_family_keys: set[tuple[str, str, str]] = set()
            gamma_harvested_family_keys: set[tuple[str, str, str]] = set()

            gamma_family_count = len(gamma_refresh_families)
            gamma_start_offset = _SUBSTRATE_GAMMA_REFRESH_CURSOR % max(1, gamma_family_count)
            rotated_gamma_refresh_families = (
                gamma_refresh_families[gamma_start_offset:]
                + gamma_refresh_families[:gamma_start_offset]
                if gamma_refresh_families
                else []
            )
            gamma_jobs: list[dict] = []
            for fam_city, fam_date, fam_metric in rotated_gamma_refresh_families:
                family_key = _refresh_family_key(fam_city, fam_date, fam_metric)
                if time.monotonic() > gamma_deadline:
                    gamma_slug_timebox_unattempted += 1
                    continue
                city_obj = _refresh_cities_by_name.get(_canonical_refresh_city_name(fam_city))
                if city_obj is None:
                    gamma_slug_invalid += 1
                    logger.info(
                        "refresh_pending_family_snapshots: city %r not in config, skipping",
                        fam_city,
                    )
                    continue
                slug_fragment = city_obj.slug_names[0] if city_obj.slug_names else fam_city.lower().replace(" ", "-")
                try:
                    slug_date = _date_to_slug_fragment(fam_date)
                except (ValueError, TypeError):
                    gamma_slug_invalid += 1
                    logger.info(
                        "refresh_pending_family_snapshots: invalid date %r for %s, skipping",
                        fam_date, fam_city,
                    )
                    continue
                prefix = "lowest" if fam_metric == "low" else "highest"
                slug = f"{prefix}-temperature-in-{slug_fragment}-on-{slug_date}"
                gamma_jobs.append(
                    {
                        "city": fam_city,
                        "target_date": fam_date,
                        "metric": fam_metric,
                        "family_key": family_key,
                        "slug": slug,
                    }
                )

            def _fetch_gamma_slug(job: dict) -> dict:
                remaining = max(0.1, gamma_deadline - time.monotonic())
                _gamma_timeout = min(
                    max(1.0, float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "10.0"))),
                    remaining,
                )
                slug = str(job["slug"])
                resp = _gamma_get("/events", params={"slug": slug}, timeout=_gamma_timeout)
                if resp.status_code != 200:
                    return {**job, "status": "http_non_200", "status_code": resp.status_code, "events": []}
                batch = resp.json()
                if not isinstance(batch, list):
                    batch = [batch] if isinstance(batch, dict) and batch else []
                events = [event for event in batch if isinstance(event, dict)]
                return {**job, "status": "ok" if events else "empty", "events": events}

            gamma_concurrency = max(
                1,
                min(32, int(os.environ.get("ZEUS_REACTOR_GAMMA_LOOKUP_CONCURRENCY", "8"))),
            )
            pending_futures: dict = {}
            next_job_index = 0

            def _submit_gamma_jobs(executor: ThreadPoolExecutor) -> None:
                nonlocal gamma_slug_attempted, next_job_index
                while (
                    len(pending_futures) < gamma_concurrency
                    and next_job_index < len(gamma_jobs)
                    and time.monotonic() <= gamma_deadline
                ):
                    job = gamma_jobs[next_job_index]
                    next_job_index += 1
                    gamma_slug_attempted += 1
                    pending_futures[executor.submit(_fetch_gamma_slug, job)] = job

            def _harvest_gamma_result(result: dict) -> None:
                nonlocal gamma_slug_http_non_200, gamma_slug_empty
                gamma_harvested_family_keys.add(result["family_key"])
                if result["status"] == "http_non_200":
                    gamma_slug_http_non_200 += 1
                    logger.debug(
                        "refresh_pending_family_snapshots: Gamma %s -> HTTP %s",
                        result["slug"], result.get("status_code"),
                    )
                elif result["status"] == "empty":
                    gamma_slug_empty += 1
                    gamma_empty_family_keys.add(result["family_key"])
                else:
                    for event in result["events"]:
                        event_id = event.get("id") or event.get("slug")
                        if event_id and event_id not in raw_events_seen:
                            raw_events_seen.add(event_id)
                            raw_events_collected.append(event)

            if gamma_jobs:
                executor = ThreadPoolExecutor(
                    max_workers=gamma_concurrency,
                    thread_name_prefix="zeus-gamma-refresh",
                )
                try:
                    _submit_gamma_jobs(executor)
                    while pending_futures:
                        remaining = gamma_deadline - time.monotonic()
                        if remaining <= 0.0:
                            gamma_slug_timebox_unattempted += len(gamma_jobs) - next_job_index
                            logger.info(
                                "refresh_pending_family_snapshots: Gamma time-box %.0fs hit after %d/%d "
                                "submitted families; draining %d in-flight, reserving %.1fs for CLOB capture",
                                max(0.1, gamma_deadline - (refresh_deadline - refresh_budget_s)),
                                gamma_slug_attempted,
                                len(gamma_jobs),
                                len(pending_futures),
                                snapshot_reserve_s,
                            )
                            next_job_index = len(gamma_jobs)
                            break
                        try:
                            future = next(
                                as_completed(
                                    tuple(pending_futures),
                                    timeout=max(0.05, min(remaining, 0.5)),
                                )
                            )
                        except FuturesTimeoutError:
                            continue
                        job = pending_futures.pop(future)
                        try:
                            result = future.result()
                        except Exception as _exc:
                            gamma_slug_failed += 1
                            logger.warning(
                                "refresh_pending_family_snapshots: Gamma fetch failed for %s: %s",
                                job["slug"], _exc,
                            )
                            _submit_gamma_jobs(executor)
                            continue
                        _harvest_gamma_result(result)
                        _submit_gamma_jobs(executor)

                    if pending_futures:
                        grace_env = (
                            "ZEUS_REACTOR_CACHED_TOPOLOGY_GAMMA_DRAIN_GRACE_SECONDS"
                            if cached_topology_markets
                            else "ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS"
                        )
                        grace_default = "0.0" if cached_topology_markets else "2.0"
                        grace_s = max(
                            0.0,
                            float(os.environ.get(grace_env, grace_default)),
                        )
                        grace_deadline = min(time.monotonic() + grace_s, refresh_deadline)
                        while pending_futures:
                            remaining_grace = grace_deadline - time.monotonic()
                            if remaining_grace <= 0.0:
                                break
                            try:
                                future = next(
                                    as_completed(
                                        tuple(pending_futures),
                                        timeout=remaining_grace,
                                    )
                                )
                            except FuturesTimeoutError:
                                break
                            job = pending_futures.pop(future)
                            try:
                                result = future.result()
                            except Exception as _exc:
                                gamma_slug_failed += 1
                                logger.debug(
                                    "refresh_pending_family_snapshots: Gamma drain fetch failed for %s: %s",
                                    job["slug"], _exc,
                                )
                                continue
                            _harvest_gamma_result(result)
                        for future in pending_futures:
                            future.cancel()
                        pending_futures.clear()
                finally:
                    for future in tuple(pending_futures):
                        future.cancel()
                    pending_futures.clear()
                    executor.shutdown(wait=False, cancel_futures=True)

            if gamma_family_count:
                gamma_progress = max(1, gamma_slug_attempted + gamma_slug_invalid)
                _SUBSTRATE_GAMMA_REFRESH_CURSOR = (
                    gamma_start_offset + gamma_progress
                ) % gamma_family_count

            gamma_slug_timebox_unattempted += len(gamma_jobs) - next_job_index
            if _gamma_empty_backoff_s > 0.0 and gamma_empty_family_keys:
                _eb_deadline = time.monotonic() + _gamma_empty_backoff_s
                for _eb_key in gamma_empty_family_keys:
                    _GAMMA_EMPTY_BACKOFF_UNTIL[_eb_key] = _eb_deadline
                try:
                    from src.data.market_absence_evidence import record_gamma_empty_families

                    record_gamma_empty_families(
                        gamma_empty_family_keys,
                        ttl_seconds=_gamma_empty_backoff_s,
                        observed_at=now_utc,
                    )
                except Exception:
                    logger.debug(
                        "refresh_pending_family_snapshots: failed to persist Gamma-empty "
                        "absence evidence",
                        exc_info=True,
                    )

            # 2026-06-06 throughput repair: keep this refresh truly scoped to pending
            # families. The old fallback called the global weather discovery scanner,
            # which performs a tag/slug sweep and routinely exhausts its
            # request budget before the warm job completes. Current Gamma slug payloads
            # include the required child fields (conditionId, acceptingOrders,
            # enableOrderBook, clobTokenIds), so the exact per-family slug responses are
            # sufficient for parsing, topology persistence, and CLOB snapshot capture.
            discovered_events = _parse_and_persist_weather_events(
                raw_events_collected,
                min_hours_to_resolution=0.0,
                now=now_utc,
            )
            logger.info(
                "refresh_pending_family_snapshots: slug fetch complete "
                "gamma_refresh_families=%d cached_topology_families=%d "
                "raw_events=%d discovered_events=%d attempted=%d empty=%d "
                "http_non_200=%d failed=%d invalid=%d timebox_unattempted=%d "
                "concurrency=%d",
                len(gamma_refresh_families), cached_topology_families,
                len(raw_events_collected), len(discovered_events),
                gamma_slug_attempted, gamma_slug_empty, gamma_slug_http_non_200,
                gamma_slug_failed, gamma_slug_invalid, gamma_slug_timebox_unattempted,
                gamma_concurrency,
            )
        except Exception as exc:
            logger.warning(
                "refresh_pending_family_snapshots: Gamma slug lookup failed: %s", exc
            )
            return {"status": "error_gamma_lookup", "reason": str(exc)}

        # Build a lookup: (city_name_lower, target_date, metric) -> parsed event dict.
        gamma_by_family: dict[tuple[str, str, str], dict] = {}
        for ev in discovered_events:
            city_obj = ev.get("city")
            city_name = getattr(city_obj, "name", None) or (city_obj if isinstance(city_obj, str) else "")
            td = str(ev.get("target_date") or "")
            metric_ev = str(ev.get("temperature_metric") or "")
            key = _refresh_family_key(city_name, td, metric_ev)
            gamma_by_family[key] = ev
        if gamma_by_family:
            for key in gamma_by_family:
                _GAMMA_EMPTY_BACKOFF_UNTIL.pop(key, None)
            try:
                from src.data.market_absence_evidence import clear_gamma_empty_families

                clear_gamma_empty_families(gamma_by_family.keys(), cleared_at=now_utc)
            except Exception:
                logger.debug(
                    "refresh_pending_family_snapshots: failed to clear stale "
                    "Gamma-empty absence evidence",
                    exc_info=True,
                )

        # Filter to ONLY the pending families (bounded CLOB calls, no universe sweep).
        markets: list[dict] = []
        markets.extend(cached_topology_markets)
        gamma_unharvested_retryable = 0
        for city, target_date, metric in gamma_refresh_families:
            key = _refresh_family_key(city, target_date, metric)
            ev = gamma_by_family.get(key)
            if ev is None:
                if key in gamma_harvested_family_keys:
                    skipped_not_found += 1
                    if key in gamma_empty_family_keys:
                        logger.warning(
                            "refresh_pending_family_snapshots: Gamma returned empty event list for "
                            "%s/%s/%s — bin identity unknown, family will stay at FDR gate",
                            city, target_date, metric,
                        )
                    else:
                        logger.warning(
                            "refresh_pending_family_snapshots: Gamma response did not parse to pending family "
                            "%s/%s/%s — bin identity unknown, family will stay at FDR gate",
                            city, target_date, metric,
                        )
                else:
                    gamma_unharvested_retryable += 1
                continue
            markets.append(ev)
        if gamma_unharvested_retryable:
            logger.info(
                "refresh_pending_family_snapshots: %d Gamma families were not harvested before "
                "the time-box and remain retryable",
                gamma_unharvested_retryable,
            )

        for market in markets:
            if not isinstance(market, dict):
                continue
            family_key = _refresh_family_key(
                market.get("city"),
                market.get("target_date"),
                market.get("temperature_metric") or market.get("metric"),
            )
            if family_key in priority_keys:
                market["_zeus_refresh_urgency"] = 4
                if not explicit_priority_conditions:
                    for outcome in market.get("outcomes", []) or []:
                        if not isinstance(outcome, dict):
                            continue
                        cid = str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
                        if cid:
                            priority_conditions.add(cid)

        if not markets:
            logger.warning(
                "refresh_pending_family_snapshots: no Gamma events matched pending families; "
                "gamma_refresh_families=%d cached_topology_families=%d skipped_not_found=%d",
                len(gamma_refresh_families), cached_topology_families, skipped_not_found,
            )
            return {
                "status": "no_refreshable_markets",
                "families_needing_refresh": len(gamma_refresh_families) + cached_topology_families,
                "gamma_refresh_families": len(gamma_refresh_families),
                "cached_topology_families": cached_topology_families,
                "skipped_not_found": skipped_not_found,
                "no_topology": no_topology,
                "venue_closed_skipped": venue_closed_skipped,
                "no_topology_backed_off": no_topology_backed_off,
                "gamma_slug_attempted": gamma_slug_attempted,
                "gamma_slug_empty": gamma_slug_empty,
                "gamma_slug_http_non_200": gamma_slug_http_non_200,
                "gamma_slug_failed": gamma_slug_failed,
                "gamma_slug_invalid": gamma_slug_invalid,
                "gamma_slug_timebox_unattempted": gamma_slug_timebox_unattempted,
            }

        # Step 4: CLOB fetch + cache write.
        #         max_outcomes=0 is the UNLIMITED sentinel: bypass the per-city cap so
        #         ALL bins of each pending family are captured in ONE cycle (e.g. an
        #         11-bin negRisk family needs all 11 — incl. non-tradeable tail bins —
        #         for the FDR full-family proof / entry gate). max_outcomes=None did NOT
        #         bypass the cap: it fell through to ZEUS_..._MAX_OUTCOMES (default 4),
        #         so families stalled at 4-of-22 candidates → EXECUTABLE_SNAPSHOT_BLOCKED
        #         (2026-06-04 root cause). This caller is scoped to pending families only
        #         (bounded set), so uncapped capture stays within the wall-clock budget.
        #         tolerate_missing_book=True is already hardwired inside
        #         refresh_executable_market_substrate_snapshots, so illiquid bins
        #         snapshot as top_ask=None / executable_allowed=False — never tradeable.
        _clob_timeout = _substrate_clob_timeout_seconds()
        if snapshot_read_conn is None:
            markets_for_refresh = markets
            fresh_condition_skipped = 0
            stale_condition_submitted = sum(len(market.get("outcomes") or []) for market in markets)
        else:
            prune_deadline_installed = _install_sqlite_deadline(
                snapshot_read_conn,
                deadline_monotonic=refresh_deadline,
            )
            prune_deadline_timer = _start_sqlite_deadline_interrupt(
                snapshot_read_conn,
                deadline_monotonic=refresh_deadline,
            )
            try:
                markets_for_refresh, fresh_condition_skipped, stale_condition_submitted = (
                    _prune_fresh_market_outcomes_for_snapshot_refresh(
                        snapshot_read_conn,
                        markets,
                        fresh_at_iso=now_iso,
                        restrict_to_condition_ids=(
                            explicit_priority_conditions if explicit_priority_conditions else None
                        ),
                        force_refresh_condition_ids=forced_conditions,
                        deadline_monotonic=refresh_deadline,
                    )
                )
            finally:
                _cancel_sqlite_deadline_interrupt(prune_deadline_timer)
                if prune_deadline_installed:
                    _clear_sqlite_deadline(snapshot_read_conn)
        if not markets_for_refresh:
            return {
                "status": "all_fresh",
                "families_checked": len(families),
                "families_needing_refresh": len(gamma_refresh_families) + cached_topology_families,
                "gamma_refresh_families": len(gamma_refresh_families),
                "cached_topology_families": cached_topology_families,
                "cached_topology_incomplete": cached_topology_incomplete,
                "no_topology": no_topology,
                "fresh_skipped": fresh_skipped,
                "venue_closed_skipped": venue_closed_skipped,
                "no_topology_backed_off": no_topology_backed_off,
                "gamma_slug_attempted": gamma_slug_attempted,
                "gamma_slug_empty": gamma_slug_empty,
                "gamma_slug_http_non_200": gamma_slug_http_non_200,
                "gamma_slug_failed": gamma_slug_failed,
                "gamma_slug_invalid": gamma_slug_invalid,
                "gamma_slug_timebox_unattempted": gamma_slug_timebox_unattempted,
                "fresh_condition_skipped": fresh_condition_skipped,
                "stale_condition_submitted": stale_condition_submitted,
            }

        snapshot_budget_s = _snapshot_capture_budget_for_refresh(
            refresh_deadline=refresh_deadline,
            snapshot_reserve_s=snapshot_reserve_s,
        )
        if snapshot_budget_s <= 0.0:
            return {
                "status": "budget_exhausted_before_snapshot_capture",
                "families_checked": len(families),
                "families_needing_refresh": len(gamma_refresh_families) + cached_topology_families,
                "gamma_refresh_families": len(gamma_refresh_families),
                "cached_topology_families": cached_topology_families,
                "cached_topology_incomplete": cached_topology_incomplete,
                "no_topology": no_topology,
                "fresh_skipped": fresh_skipped,
                "venue_closed_skipped": venue_closed_skipped,
                "no_topology_backed_off": no_topology_backed_off,
                "gamma_slug_attempted": gamma_slug_attempted,
                "gamma_slug_empty": gamma_slug_empty,
                "gamma_slug_http_non_200": gamma_slug_http_non_200,
                "gamma_slug_failed": gamma_slug_failed,
                "gamma_slug_invalid": gamma_slug_invalid,
                "gamma_slug_timebox_unattempted": gamma_slug_timebox_unattempted,
                "fresh_condition_skipped": fresh_condition_skipped,
                "stale_condition_submitted": stale_condition_submitted,
                "refresh_budget_seconds": refresh_budget_s,
                "snapshot_reserve_seconds": snapshot_reserve_s,
                "snapshot_budget_seconds": snapshot_budget_s,
            }
        if snapshot_read_conn is not None:
            snapshot_read_conn.close()
            snapshot_read_conn = None
        write_conn = get_trade_connection(write_class="live")
        try:
            with PolymarketClient(public_http_timeout=_clob_timeout) as clob:
                summary = refresh_executable_market_substrate_snapshots(
                    write_conn,
                    markets=markets_for_refresh,
                    clob=clob,
                    captured_at=datetime.now(timezone.utc),
                    scan_authority="VERIFIED",
                    max_outcomes=0,  # UNLIMITED: capture every bin of each pending family
                    budget_seconds=snapshot_budget_s,
                    capture_reserve_seconds=snapshot_reserve_s,
                    priority_condition_ids=priority_conditions,
                    force_refresh_condition_ids=forced_conditions,
                    snapshot_write_context_factory=_substrate_snapshot_trade_write_context_factory(
                        "substrate_pending_family_snapshot_refresh"
                    ),
                )
        finally:
            write_conn.close()

    except Exception as exc:
        logger.warning("refresh_pending_family_snapshots: failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        _cancel_sqlite_deadline_interrupt(forecasts_deadline_timer)
        _cancel_sqlite_deadline_interrupt(snapshot_deadline_timer)
        if forecasts_deadline_installed:
            _clear_sqlite_deadline(forecasts_conn)
        if snapshot_read_conn is not None:
            if snapshot_deadline_installed:
                _clear_sqlite_deadline(snapshot_read_conn)
            snapshot_read_conn.close()

    result = {
        "status": "refreshed",
        "families_checked": len(families),
        "explicit_priority_families": len(explicit_priority_families),
        "include_pending_families": bool(include_pending_families),
        "include_money_risk_families": bool(include_money_risk_families),
        "open_rest_priority_families": len(open_rest_priority_families),
        "held_position_priority_families": len(held_position_priority_families),
        "priority_family_count": len(priority_families),
        "priority_condition_ids_requested": len(priority_conditions),
        "families_needing_refresh": len(gamma_refresh_families) + cached_topology_families,
        "gamma_refresh_families": len(gamma_refresh_families),
        "cached_topology_families": cached_topology_families,
        "cached_topology_incomplete": cached_topology_incomplete,
        "no_topology": no_topology,
        "no_topology_backed_off": no_topology_backed_off,
        "fresh_skipped": fresh_skipped,
        "venue_closed_skipped": venue_closed_skipped,
        "topology_budget_exhausted": int(topology_budget_exhausted),
        "topology_deferred_families": topology_deferred_families,
        "skipped_not_found": skipped_not_found,
        "gamma_slug_attempted": gamma_slug_attempted,
        "gamma_slug_empty": gamma_slug_empty,
        "gamma_slug_http_non_200": gamma_slug_http_non_200,
        "gamma_slug_failed": gamma_slug_failed,
        "gamma_slug_invalid": gamma_slug_invalid,
        "gamma_slug_timebox_unattempted": gamma_slug_timebox_unattempted,
        "markets_submitted": len(markets_for_refresh),
        "fresh_condition_skipped": fresh_condition_skipped,
        "stale_condition_submitted": stale_condition_submitted,
        "refresh_budget_seconds": refresh_budget_s,
        "snapshot_reserve_seconds": snapshot_reserve_s,
        "snapshot_budget_seconds": snapshot_budget_s,
        **summary,
    }
    unavailable_source = _market_unavailable_source_from_snapshot_summary(summary)
    if unavailable_source is not None:
        unavailable_families = sorted(
            {
                _refresh_family_key(
                    market.get("city"),
                    market.get("target_date"),
                    market.get("temperature_metric") or market.get("metric"),
                )
                for market in markets_for_refresh
                if isinstance(market, dict)
            }
        )
        unavailable_families = [
            family for family in unavailable_families if family[0] and family[1] and family[2]
        ]
        if unavailable_families:
            try:
                from src.data.market_absence_evidence import record_market_unavailable_families

                record_market_unavailable_families(
                    unavailable_families,
                    ttl_seconds=_market_unavailable_evidence_ttl_seconds(),
                    observed_at=now_utc,
                    source=unavailable_source,
                )
                result["market_unavailable_evidence_source"] = unavailable_source
                result["market_unavailable_families_recorded"] = len(unavailable_families)
            except Exception:
                logger.debug(
                    "refresh_pending_family_snapshots: failed to persist "
                    "market-unavailable evidence",
                    exc_info=True,
                )
    logger.info("refresh_pending_family_snapshots: %s", result)
    return result


def _market_unavailable_source_from_snapshot_summary(summary: dict) -> str | None:
    """Return a terminal market-unavailable source proven by snapshot refresh."""

    if str(summary.get("executable_substrate_coverage_status") or "") != "NO_EXECUTABLE_CANDIDATES":
        return None
    try:
        candidate_count = int(summary.get("executable_snapshot_candidate_count") or 0)
    except (TypeError, ValueError):
        candidate_count = 0
    if candidate_count != 0:
        return None
    counts = summary.get("executable_snapshot_candidate_rejection_counts")
    if not isinstance(counts, dict):
        return None
    if set(str(key) for key in counts) != {"market_end_at_elapsed"}:
        return None
    try:
        elapsed = int(counts.get("market_end_at_elapsed") or 0)
    except (TypeError, ValueError):
        elapsed = 0
    return "market_end_at_elapsed" if elapsed > 0 else None


def _market_discovery_staleness_window_seconds() -> float:
    """Substrate staleness window (seconds): skip a re-capture if the last full capture is
    newer than this. Producer-local — NO consumer/reactor state. Env-overridable
    (ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS kept as the var name for operator
    config continuity; its SEMANTICS are now pure staleness, never pending-gated)."""
    return max(
        0.0,
        float(os.environ.get("ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS", "300.0")),
    )


def _market_discovery_cycle() -> None:
    """Refresh executable market substrate outside decision-cycle critical path.

    P2 PRODUCER (system_decomposition_plan §8 Step 1 / §9): the outer pending gates
    (`if _edli_reactor_active(): return`, `if pending_count>0 and recent_discovery: return`)
    are DELETED. The sole trigger is now substrate STALENESS via the producer-local clock
    `_market_discovery_last_completed_monotonic` — a backlog in the (out-of-process) reactor
    has ZERO effect on whether this fires. The hybrid staleness+pending gate is replaced by a
    PURE staleness gate.
    """

    global _market_discovery_last_completed_monotonic

    if (
        money_path_substrate_priority_active()
        and (
            money_path_substrate_priority_families()
            or money_path_substrate_priority_condition_ids()
        )
    ):
        logger.info("market_discovery deferred: money-path priority marker active")
        return

    acquired = _market_discovery_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("market_discovery skipped: previous market_discovery still running")
        return
    # PURE STALENESS GATE (system_decomposition_plan §9 point 2): skip a redundant re-capture
    # if the substrate is still FRESH (last full capture within the staleness window). This is
    # keyed ONLY on the producer-local _market_discovery_last_completed_monotonic clock — it
    # NEVER reads a consumer pending_count (the old hybrid pending+staleness gate is deleted by
    # the lift, §0). A reactor backlog cannot influence this skip; only the substrate's own age
    # can. When stale (or never captured), fall through and capture the universe regardless of
    # how many events are pending.
    staleness_window_s = _market_discovery_staleness_window_seconds()
    last_completed = _market_discovery_last_completed_monotonic
    substrate_fresh = (
        staleness_window_s > 0
        and last_completed is not None
        and (time.monotonic() - last_completed) < staleness_window_s
    )
    if substrate_fresh:
        _market_discovery_lock.release()
        logger.info(
            "market_discovery skipped: executable substrate still fresh "
            "(last full capture %.1fs ago, staleness window %.1fs)",
            time.monotonic() - last_completed,
            staleness_window_s,
        )
        return
    substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
    if not substrate_acquired:
        _market_discovery_lock.release()
        logger.info("market_discovery deferred: executable substrate refresh already running")
        return
    from src.data.dual_run_lock import acquire_lock

    process_lock_ctx = acquire_lock("market_substrate_refresh")
    try:
        substrate_process_acquired = process_lock_ctx.__enter__()
        if not substrate_process_acquired:
            logger.info("market_discovery deferred: cross-process executable substrate refresh already running")
            return
        from src.data.market_scanner import (
            find_weather_markets_or_raise,
            refresh_executable_market_substrate_snapshots,
        )
        from src.data.polymarket_client import PolymarketClient
        from src.state.db import get_trade_connection

        events = find_weather_markets_or_raise(
            min_hours_to_resolution=0.0,
            include_slug_pattern=True,
        )
        conn = get_trade_connection(write_class="live")
        try:
            _discovery_clob_timeout = _substrate_clob_timeout_seconds()
            with PolymarketClient(public_http_timeout=_discovery_clob_timeout) as snapshot_clob:
                snapshot_summary = refresh_executable_market_substrate_snapshots(
                    conn,
                    markets=events,
                    clob=snapshot_clob,
                    captured_at=datetime.now(timezone.utc),
                    scan_authority="VERIFIED",
                    snapshot_write_context_factory=_substrate_snapshot_trade_write_context_factory(
                        "substrate_market_discovery_snapshot_refresh"
                    ),
                )
                conn.commit()
        finally:
            conn.close()
        if snapshot_summary.get("attempted", 0) > 0 and snapshot_summary.get("inserted", 0) == 0:
            raise RuntimeError(
                "market_discovery refreshed events but captured no executable snapshots: "
                f"{snapshot_summary}"
            )
        logger.info(
            "market_discovery: refreshed %s weather events; executable_snapshots=%s",
            len(events),
            snapshot_summary,
        )
        _market_discovery_last_completed_monotonic = time.monotonic()
    finally:
        try:
            process_lock_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        _market_substrate_refresh_lock.release()
        _market_discovery_lock.release()


def _edli_market_substrate_warm_cycle() -> None:
    """Dedicated EDLI executable-snapshot substrate warmer, DECOUPLED from the reactor.

    THROUGHPUT STRUCTURAL FIX (2026-06-01): _refresh_pending_family_snapshots makes a
    full-universe Gamma scan (find_weather_markets → _get_active_events, benchmarked
    ~76s COLD; TTL 300s so it re-ran nearly every cycle) + per-token CLOB /book capture
    across all pending-family bins. Running it INLINE at the top of
    _edli_event_reactor_cycle made the reactor's wall-clock blow past its 1-min
    APScheduler interval — with max_instances=1/coalesce=True, every overlapping trigger
    was skipped, so process_pending essentially never ran (23 min with ZERO completed
    cycles / ZERO trades observed on the live daemon, even though the submit path is
    CODE-CLEAR to the venue POST boundary).

    Moving the refresh here (mirroring _edli_bankroll_warm_cycle, #45) puts the expensive
    venue-I/O on its OWN cadence so the reactor reads ALREADY-captured snapshots
    (DB-only, microseconds) and reaches submit in seconds. This changes NO decision: the
    reactor's no-submit proof, full gate chain, and just-in-time submit /book are
    byte-for-byte unchanged — they just consume snapshots a background job produced.
    Fail-closed is preserved: a family not yet captured this tick requeues via the
    reactor's existing EXECUTABLE_SNAPSHOT_RETRY path.

    Not a DB writer of its own ledger — it delegates to _refresh_pending_family_snapshots,
    which owns its write trade connection + commit. The @_scheduler_job decorator is the
    only wiring needed (B047). Fail-soft: a transient Gamma/CLOB failure logs but never
    crashes this job (the next tick retries; consumers stay fail-closed in the interim).
    """

    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled"):
        return
    priority_marker_active = money_path_substrate_priority_active()
    priority_marker_request = (
        money_path_substrate_priority_request() if priority_marker_active else None
    )
    priority_marker_families = (
        money_path_substrate_priority_families() if priority_marker_active else []
    )
    priority_marker_condition_ids = (
        money_path_substrate_priority_condition_ids() if priority_marker_active else []
    )
    priority_marker_has_scope = bool(priority_marker_families or priority_marker_condition_ids)
    if priority_marker_has_scope:
        summary = {
            "status": "priority_deferred_to_priority_lane",
            "priority_marker_active": True,
            "scheduler_failed": False,
            "priority_marker_families": len(priority_marker_families),
            "priority_marker_condition_ids": len(priority_marker_condition_ids),
            "serviced_by": "money_path_substrate_priority",
        }
        if isinstance(priority_marker_request, dict):
            summary["priority_request_id"] = str(priority_marker_request.get("request_id") or "")
        logger.info("EDLI market-substrate warm deferred to priority lane: %s", summary)
        return summary
    from src.state.db import (
        ZEUS_FORECASTS_DB_PATH,
        get_forecasts_connection_read_only,
        get_world_connection,
    )

    conn = get_world_connection()
    try:
        _attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in _attached:
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except Exception as _attach_exc:  # noqa: BLE001
        logger.warning(
            "EDLI market-substrate warm: ATTACH forecasts failed (non-fatal): %r", _attach_exc
        )
    forecasts_conn = get_forecasts_connection_read_only()
    substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
    if not substrate_acquired:
        summary = {"status": "skipped_in_process_lock_busy", "priority_marker_active": False}
        logger.info("EDLI market-substrate warm skipped: %s", summary.get("status"))
        try:
            forecasts_conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return summary
    from src.data.dual_run_lock import acquire_lock

    process_lock_ctx = acquire_lock("market_substrate_refresh")
    try:
        substrate_process_acquired = process_lock_ctx.__enter__()
        if not substrate_process_acquired:
            summary = {"status": "skipped_cross_process_lock_busy", "priority_marker_active": False}
            logger.info("EDLI market-substrate warm skipped: %s", summary.get("status"))
            return summary
        background_budget_s = _background_warm_refresh_budget_seconds()
        background_snapshot_reserve_s = _background_warm_snapshot_reserve_seconds(background_budget_s)
        summary = _refresh_pending_family_snapshots(
            conn,
            forecasts_conn,
            extra_priority_families=(),
            include_pending_families=True,
            priority_condition_ids=(),
            refresh_budget_seconds=background_budget_s,
            snapshot_reserve_seconds=background_snapshot_reserve_s,
            include_money_risk_families=False,
        )
        summary = {
            **dict(summary or {}),
            "condition_priority_families": 0,
            "open_rest_priority_condition_ids": 0,
            "held_position_priority_condition_ids": 0,
            "claim_order_priority_families": 0,
            "claim_order_priority_read_failed": False,
        }
        summary = _substrate_warm_business_summary(
            summary,
            priority_request=priority_marker_request,
            priority_marker_active=False,
        )
        logger.info("EDLI market-substrate warm: refresh summary=%r", summary)
        return summary
    except Exception as exc:  # noqa: BLE001 — fail-soft; next tick retries
        summary = _substrate_warm_failed_summary(
            status="error",
            reason=str(exc),
            priority_request=priority_marker_request,
            priority_marker_active=False,
        )
        logger.error(
            "EDLI market-substrate warm failed: %s",
            exc,
            exc_info=True,
        )
        return summary
    finally:
        try:
            forecasts_conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            process_lock_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        _market_substrate_refresh_lock.release()


def _edli_money_path_substrate_priority_cycle() -> dict | None:
    """Refresh only the executable books that can unblock live money-path decisions."""

    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled"):
        return None

    priority_marker_active = money_path_substrate_priority_active()
    priority_marker_request = (
        money_path_substrate_priority_request() if priority_marker_active else None
    )
    priority_marker_families = (
        money_path_substrate_priority_families() if priority_marker_active else []
    )
    priority_marker_condition_ids = (
        money_path_substrate_priority_condition_ids() if priority_marker_active else []
    )
    if priority_marker_active and not priority_marker_families and not priority_marker_condition_ids:
        summary = {
            "status": "priority_request_empty_scope",
            "priority_marker_active": True,
            "scheduler_failed": False,
        }
        if isinstance(priority_marker_request, dict):
            summary["priority_request_id"] = str(priority_marker_request.get("request_id") or "")
            summary["priority_marker_families"] = len(priority_marker_request.get("families") or [])
            summary["priority_marker_condition_ids"] = len(
                priority_marker_request.get("condition_ids") or []
            )
        _substrate_priority_receipt(request=priority_marker_request, summary=summary)
        logger.info("EDLI money-path substrate priority skipped: %s", summary["status"])
        return summary
    from src.state.db import (
        ZEUS_FORECASTS_DB_PATH,
        get_forecasts_connection_read_only,
        get_trade_connection_read_only,
        get_world_connection,
    )

    conn = get_world_connection()
    # K1: the snapshot refresh reads market topology off the forecasts DB (market_events).
    # Attach read-only (idempotent) so the family-topology lookup resolves, mirroring the
    # reactor's own ATTACH. _refresh_pending_family_snapshots opens its own WRITE trade
    # connection internally and commits — this conn is only the world-side pending-event
    # reader.
    try:
        _attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in _attached:
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except Exception as _attach_exc:  # noqa: BLE001 — non-fatal; refresh logs+skips on topology miss
        logger.warning(
            "EDLI money-path substrate priority: ATTACH forecasts failed (non-fatal): %r",
            _attach_exc,
        )
    forecasts_conn = get_forecasts_connection_read_only()
    lock_wait_s = _priority_refresh_lock_wait_seconds()
    substrate_acquired = (
        _market_substrate_refresh_lock.acquire(blocking=False)
        if lock_wait_s <= 0.0
        else _market_substrate_refresh_lock.acquire(timeout=lock_wait_s)
    )
    if not substrate_acquired:
        summary = (
            _substrate_warm_failed_summary(
                status="priority_unserviced_in_process_lock_busy",
                reason="executable substrate refresh already running",
                priority_request=priority_marker_request,
                priority_marker_active=priority_marker_active,
            )
            if priority_marker_active
            else {"status": "skipped_in_process_lock_busy", "priority_marker_active": False}
        )
        summary["lock_wait_seconds"] = lock_wait_s
        if priority_marker_active:
            _substrate_priority_receipt(request=priority_marker_request, summary=summary)
        logger.info("EDLI money-path substrate priority skipped: %s", summary.get("status"))
        try:
            forecasts_conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return summary
    from src.data.dual_run_lock import acquire_lock

    process_lock_ctx = acquire_lock("market_substrate_refresh")
    try:
        substrate_process_acquired = process_lock_ctx.__enter__()
        if not substrate_process_acquired:
            summary = (
                _substrate_warm_failed_summary(
                    status="priority_unserviced_cross_process_lock_busy",
                    reason="cross-process executable substrate refresh already running",
                    priority_request=priority_marker_request,
                    priority_marker_active=priority_marker_active,
                )
                if priority_marker_active
                else {"status": "skipped_cross_process_lock_busy", "priority_marker_active": False}
            )
            if priority_marker_active:
                _substrate_priority_receipt(request=priority_marker_request, summary=summary)
            logger.info("EDLI money-path substrate priority skipped: %s", summary.get("status"))
            return summary
        priority_budget_s = _priority_refresh_budget_seconds()
        priority_snapshot_reserve_s = _priority_snapshot_reserve_seconds(priority_budget_s)
        claim_deadline = time.monotonic() + _claim_order_priority_read_budget_seconds()
        claim_deadline_installed = _install_sqlite_deadline(
            conn,
            deadline_monotonic=claim_deadline,
        )
        claim_deadline_timer = _start_sqlite_deadline_interrupt(
            conn,
            deadline_monotonic=claim_deadline,
        )
        try:
            marker_exact_condition_ids = list(priority_marker_condition_ids)
            marker_force_refresh_condition_ids = list(
                (priority_marker_request or {}).get("force_refresh_condition_ids") or []
            )
            open_rest_priority_condition_ids: list[str] = []
            held_position_priority_condition_ids: list[str] = []
            claim_priority_read_failed = False
            claim_priority_families: list[tuple[str, str, str]] = []
            exact_priority_condition_ids = list(marker_exact_condition_ids)
            # A forced FC-03 winner recapture owns this one short sidecar tick.
            # Broad held/rest/claim discovery resumes on the next tick; reading it
            # first can spend the whole deadline and make the elected order stale.
            if not marker_force_refresh_condition_ids:
                trade_ro = None
                try:
                    trade_ro = get_trade_connection_read_only()
                    open_rest_priority_condition_ids = _open_rest_condition_ids_for_refresh(
                        trade_ro,
                        forecasts_conn=forecasts_conn,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "EDLI money-path substrate priority: open-rest condition priority read failed "
                        "(non-fatal): %s",
                        exc,
                    )
                finally:
                    if trade_ro is not None:
                        try:
                            trade_ro.close()
                        except Exception:  # noqa: BLE001
                            pass
                held_position_priority_condition_ids = _edli_current_held_position_condition_ids()
                exact_priority_condition_ids.extend(open_rest_priority_condition_ids)
                exact_priority_condition_ids.extend(held_position_priority_condition_ids)
            condition_priority_families = _condition_priority_families_for_refresh(
                forecasts_conn,
                exact_priority_condition_ids,
            )
            if not marker_force_refresh_condition_ids:
                claim_priority_families = _claim_order_priority_families_for_refresh(
                    conn,
                    consumer_name="edli_reactor_v1",
                    now_utc=datetime.now(timezone.utc),
                )
                if claim_priority_families is None:
                    claim_priority_read_failed = True
                    claim_priority_families = []
        finally:
            _cancel_sqlite_deadline_interrupt(claim_deadline_timer)
            if claim_deadline_installed:
                _clear_sqlite_deadline(conn)
        # _refresh_pending_family_snapshots never raises by contract (it logs+returns an
        # error dict), but wrap defensively so a venue-I/O failure can NEVER propagate out
        # of the scheduler job (the reactor stays decoupled and fail-closed regardless).
        priority_families: list[tuple[str, str, str]] = []
        priority_family_seen: set[tuple[str, str, str]] = set()
        # Claim-order families are already live-money blocked reactor work, not broad backlog.
        # Exact condition markers must remain exact.  Adding every marker family after resolving
        # condition ids silently turns a scoped request back into a full-family topology sweep,
        # which can burn the whole sidecar budget before any requested condition is captured.
        marker_family_candidates = (
            []
            if marker_exact_condition_ids
            else list(priority_marker_families)
        )
        priority_family_candidates = (
            list(condition_priority_families)
            + list(claim_priority_families)
            + marker_family_candidates
        )
        for family in priority_family_candidates:
            key = tuple(str(part or "").strip() for part in family)
            if len(key) != 3 or not all(key) or key in priority_family_seen:
                continue
            priority_family_seen.add(key)
            priority_families.append(key)  # type: ignore[arg-type]
        if not (
            priority_families
            or exact_priority_condition_ids
            or claim_priority_read_failed
        ):
            summary = {
                "status": "no_money_path_priority_scope",
                "priority_marker_active": bool(priority_marker_active),
                "scheduler_failed": False,
                "condition_priority_families": 0,
                "open_rest_priority_condition_ids": len(open_rest_priority_condition_ids),
                "held_position_priority_condition_ids": len(held_position_priority_condition_ids),
                "claim_order_priority_families": 0,
                "claim_order_priority_read_failed": False,
            }
            _substrate_priority_receipt(request=priority_marker_request, summary=summary)
            logger.info("EDLI money-path substrate priority: %r", summary)
            return summary
        summary = _refresh_pending_family_snapshots(
            conn,
            forecasts_conn,
            extra_priority_families=priority_families,
            include_pending_families=False,
            priority_condition_ids=exact_priority_condition_ids,
            force_refresh_condition_ids=marker_force_refresh_condition_ids,
            refresh_budget_seconds=priority_budget_s,
            snapshot_reserve_seconds=priority_snapshot_reserve_s,
            include_money_risk_families=not bool(marker_exact_condition_ids),
        )
        summary = {
            **dict(summary or {}),
            "condition_priority_families": len(condition_priority_families),
            "open_rest_priority_condition_ids": len(open_rest_priority_condition_ids),
            "held_position_priority_condition_ids": len(held_position_priority_condition_ids),
            "claim_order_priority_families": len(claim_priority_families),
            "claim_order_priority_read_failed": bool(claim_priority_read_failed),
            "marker_family_scope_suppressed_by_exact_conditions": int(
                bool(marker_exact_condition_ids)
            ),
        }
        summary = _substrate_warm_business_summary(
            summary,
            priority_request=priority_marker_request,
            priority_marker_active=priority_marker_active,
        )
        _substrate_priority_receipt(request=priority_marker_request, summary=summary)
        logger.info("EDLI money-path substrate priority: refresh summary=%r", summary)
        return summary
    except Exception as exc:  # noqa: BLE001 — fail-soft; next tick retries
        summary = _substrate_warm_failed_summary(
            status="error",
            reason=str(exc),
            priority_request=priority_marker_request,
            priority_marker_active=priority_marker_active,
        )
        _substrate_priority_receipt(request=priority_marker_request, summary=summary)
        logger.error(
            "EDLI money-path substrate priority: refresh raised (non-fatal, snapshots did not "
            "advance this tick): %r",
            exc,
        )
        return summary
    finally:
        try:
            process_lock_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        try:
            _market_substrate_refresh_lock.release()
        except RuntimeError:
            pass
        try:
            forecasts_conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def refresh_money_path_substrate_now(
    *,
    families: Iterable[tuple[str, str, str]],
    condition_ids: Iterable[str] | None = None,
    reason: str = "money_path_targeted_refresh",
    refresh_budget_seconds: float | None = None,
    snapshot_reserve_seconds: float | None = None,
    include_money_risk_families: bool = False,
    force_refresh: bool = False,
) -> dict:
    """Synchronously refresh the exact executable substrate needed by a money-path decision.

    Broad warming remains sidecar-owned. This entry point is the producer-side
    escape hatch for an already-selected live-money family whose current decision
    would otherwise fail only because its executable book row is stale. It uses the
    same cross-process substrate lock and snapshot writer as the sidecar priority
    lane, then returns the actual refresh summary to the consumer.
    """

    clean_families = {
        (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        for city, target_date, metric in (families or ())
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    clean_condition_ids = {
        str(condition_id or "").strip()
        for condition_id in (condition_ids or ())
        if str(condition_id or "").strip()
    }
    if force_refresh and not clean_condition_ids:
        return {
            "status": "force_refresh_scope_missing",
            "reason": str(reason or ""),
            "families_requested": len(clean_families),
            "condition_ids_requested": 0,
        }
    if not clean_families and not clean_condition_ids:
        return {
            "status": "no_money_path_refresh_scope",
            "reason": str(reason or ""),
            "families_requested": 0,
            "condition_ids_requested": 0,
        }

    lock_wait_s = _inline_refresh_lock_wait_seconds()
    lock_wait_started = time.monotonic()
    lock_deadline = lock_wait_started + lock_wait_s
    substrate_acquired = (
        _market_substrate_refresh_lock.acquire(blocking=False)
        if lock_wait_s <= 0.0
        else _market_substrate_refresh_lock.acquire(timeout=lock_wait_s)
    )
    if not substrate_acquired:
        return {
            "status": "inline_skipped_in_process_lock_busy",
            "reason": str(reason or ""),
            "families_requested": len(clean_families),
            "condition_ids_requested": len(clean_condition_ids),
            "lock_wait_seconds": lock_wait_s,
            "lock_wait_elapsed_seconds": time.monotonic() - lock_wait_started,
        }

    process_lock_ctx = None
    world_conn = None
    forecasts_conn = None
    try:
        from src.data.dual_run_lock import acquire_lock

        while True:
            process_lock_ctx = acquire_lock("market_substrate_refresh")
            substrate_process_acquired = process_lock_ctx.__enter__()
            if substrate_process_acquired:
                break
            process_lock_ctx.__exit__(None, None, None)
            process_lock_ctx = None
            lock_remaining_s = lock_deadline - time.monotonic()
            if lock_remaining_s <= 0.0:
                return {
                    "status": "inline_skipped_cross_process_lock_busy",
                    "reason": str(reason or ""),
                    "families_requested": len(clean_families),
                    "condition_ids_requested": len(clean_condition_ids),
                    "lock_wait_seconds": lock_wait_s,
                    "lock_wait_elapsed_seconds": time.monotonic() - lock_wait_started,
                }
            time.sleep(min(0.05, lock_remaining_s))
        lock_wait_elapsed_s = time.monotonic() - lock_wait_started

        from src.state.db import (
            ZEUS_FORECASTS_DB_PATH,
            get_forecasts_connection_read_only,
            get_world_connection,
        )

        world_conn = get_world_connection()
        try:
            attached = {row[1] for row in world_conn.execute("PRAGMA database_list").fetchall()}
            if "forecasts" not in attached:
                world_conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
        except Exception as attach_exc:  # noqa: BLE001
            logger.warning(
                "money-path substrate inline refresh: ATTACH forecasts failed "
                "(continuing with direct forecasts connection): %r",
                attach_exc,
            )
        forecasts_conn = get_forecasts_connection_read_only()
        summary = _refresh_pending_family_snapshots(
            world_conn,
            forecasts_conn,
            extra_priority_families=clean_families,
            include_pending_families=False,
            priority_condition_ids=clean_condition_ids,
            force_refresh_condition_ids=(clean_condition_ids if force_refresh else ()),
            refresh_budget_seconds=refresh_budget_seconds,
            snapshot_reserve_seconds=snapshot_reserve_seconds,
            include_money_risk_families=include_money_risk_families,
        )
        out = dict(summary or {})
        out.update(
            {
                "serviced_by": "inline_money_path_substrate_refresh",
                "reason": str(reason or ""),
                "families_requested": len(clean_families),
                "condition_ids_requested": len(clean_condition_ids),
                "lock_wait_seconds": lock_wait_s,
                "lock_wait_elapsed_seconds": lock_wait_elapsed_s,
            }
        )
        logger.info("money-path substrate inline refresh: %r", out)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("money-path substrate inline refresh failed: %s", exc, exc_info=True)
        return {
            "status": "inline_error",
            "reason": str(reason or ""),
            "error": str(exc),
            "families_requested": len(clean_families),
            "condition_ids_requested": len(clean_condition_ids),
        }
    finally:
        if forecasts_conn is not None:
            try:
                forecasts_conn.close()
            except Exception:  # noqa: BLE001
                pass
        if world_conn is not None:
            try:
                world_conn.close()
            except Exception:  # noqa: BLE001
                pass
        if process_lock_ctx is not None:
            try:
                process_lock_ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        try:
            _market_substrate_refresh_lock.release()
        except RuntimeError:
            pass
