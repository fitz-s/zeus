# Created: 2026-06-08
# Last reused or audited: 2026-06-08
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

import logging
import os
import time
from datetime import datetime, timezone
from typing import Iterable

from src.config import settings

logger = logging.getLogger("zeus.substrate_observer")

# In-process locks shared by the two lifted jobs (system_decomposition_plan §4.1):
# both writers serialize through ``_market_substrate_refresh_lock`` so they cannot
# race-write ``executable_market_snapshots``. ``_market_discovery_lock`` prevents a
# universe sweep from overlapping itself.
import threading

_market_discovery_lock = threading.Lock()
_market_substrate_refresh_lock = threading.Lock()
# Producer-local staleness clock — the SOLE trigger for the universe sweep after the
# outer pending gates were deleted (§9 point 2). Never references consumer state.
_market_discovery_last_completed_monotonic: float | None = None
_SUBSTRATE_REFRESH_CURSOR = 0
_SUBSTRATE_PRIORITY_REFRESH_CURSOR = 0
_GAMMA_EMPTY_BACKOFF_UNTIL: dict[tuple[str, str, str], float] = {}
_NEW_FAMILY_CONDITION_IDS: set[str] = set()


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


def _pending_family_rows_for_refresh(world_conn, *, consumer_name: str):
    event_window_limit = max(
        100,
        min(
            10000,
            int(os.environ.get("ZEUS_PENDING_FAMILY_REFRESH_EVENT_WINDOW_LIMIT", "2000")),
        ),
    )
    return world_conn.execute(
        """
        WITH pending AS (
            SELECT p.event_id
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            WHERE p.consumer_name = ? AND p.processing_status = 'pending'
            ORDER BY p.updated_at DESC
            LIMIT ?
        )
        SELECT
            json_extract(e.payload_json, '$.city')        AS city,
            json_extract(e.payload_json, '$.target_date') AS target_date,
            json_extract(e.payload_json, '$.metric')      AS metric
        FROM pending p
        JOIN opportunity_events e ON e.event_id = p.event_id
        GROUP BY city, target_date, metric
        -- Refresh the newest target date first. Old target-date rows can remain
        -- pending after a market has disappeared from Gamma; if they consume the
        -- per-cycle cap, fresh executable snapshots starve and no receipt is
        -- emitted even though the reactor itself is healthy.
        ORDER BY
            MAX(json_extract(e.payload_json, '$.target_date')) DESC,
            MAX(e.priority) DESC,
            MAX(e.available_at) DESC,
            MIN(e.event_id) ASC
        """,
        (consumer_name, event_window_limit),
    ).fetchall()


def _open_rest_family_rows_for_refresh(trade_conn) -> list[tuple[str, str, str]]:
    """Families with live unfilled entry rests that need fresh executable books."""

    try:
        commands = trade_conn.execute(
            """
            SELECT command_id, position_id, venue_order_id
              FROM venue_commands
             WHERE intent_kind = 'ENTRY'
               AND venue_order_id IS NOT NULL
               AND venue_order_id != ''
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    open_states = {"LIVE", "RESTING", "PARTIALLY_MATCHED"}
    for row in commands:
        venue_order_id = str(row[2] or "")
        if not venue_order_id:
            continue
        try:
            fact = trade_conn.execute(
                """
                SELECT state
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
        position_id = str(row[1] or "")
        if not position_id:
            continue
        try:
            pos = trade_conn.execute(
                """
                SELECT city, target_date, temperature_metric
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
            continue
        family = (
            str(pos[0] or "").strip(),
            str(pos[1] or "").strip(),
            str(pos[2] or "").strip(),
        )
        if all(family) and family not in seen:
            seen.add(family)
            out.append(family)
    return out


def _edli_current_held_position_family_keys() -> set[tuple[str, str, str]]:
    """Current held-position families for warmer priority.

    Fail-soft: a producer read failure must not crash the substrate daemon.
    """

    from src.state.db import get_trade_connection_read_only

    try:
        conn = get_trade_connection_read_only()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT city, target_date, temperature_metric
                  FROM position_current
                 WHERE phase IN ('pending_entry', 'active', 'day0_window', 'pending_exit')
                """
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "substrate_observer: held-position family read failed; held families not prioritized this tick: %r",
            exc,
        )
        return set()
    out: set[tuple[str, str, str]] = set()
    for row in rows:
        family = (
            str(row[0] or "").strip(),
            str(row[1] or "").strip(),
            str(row[2] or "").strip(),
        )
        if all(family):
            out.add(family)
    return out


def _condition_buy_sides_fresh(write_conn, condition_id: str, fresh_at_iso: str) -> bool:
    rows = write_conn.execute(
        """
        SELECT yes_token_id, no_token_id, selected_outcome_token_id
        FROM executable_market_snapshots
        WHERE condition_id = ? AND freshness_deadline >= ?
        ORDER BY captured_at DESC, snapshot_id DESC
        """,
        (condition_id, fresh_at_iso),
    ).fetchall()
    if not rows:
        return False

    yes_token_id = ""
    no_token_id = ""
    fresh_selected_tokens: set[str] = set()

    def _cell(row, key: str, index: int) -> str:
        try:
            value = row[key] if hasattr(row, "keys") else row[index]
        except (KeyError, IndexError, TypeError):
            value = None
        return str(value or "").strip()

    for row in rows:
        yes = _cell(row, "yes_token_id", 0)
        no = _cell(row, "no_token_id", 1)
        selected = _cell(row, "selected_outcome_token_id", 2)
        if yes and not yes_token_id:
            yes_token_id = yes
        if no and not no_token_id:
            no_token_id = no
        if selected:
            fresh_selected_tokens.add(selected)
    if not yes_token_id or not no_token_id:
        return False
    return yes_token_id in fresh_selected_tokens and no_token_id in fresh_selected_tokens
def _prune_fresh_market_outcomes_for_snapshot_refresh(
    write_conn,
    markets: list[dict],
    *,
    fresh_at_iso: str,
) -> tuple[list[dict], int, int]:
    pruned: list[dict] = []
    fresh_conditions_skipped = 0
    stale_conditions_submitted = 0
    for market in markets:
        stale_outcomes: list[dict] = []
        for outcome in market.get("outcomes", []) or []:
            if not isinstance(outcome, dict):
                continue
            cid = str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
            if cid and _condition_buy_sides_fresh(write_conn, cid, fresh_at_iso):
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
) -> float:
    del refresh_budget_s
    if cached_topology_count > 0:
        cached_gamma_s = max(
            0.0,
            float(os.environ.get("ZEUS_REACTOR_CACHED_TOPOLOGY_GAMMA_SECONDS", "14.0")),
        )
        return refresh_deadline - cached_gamma_s
    return refresh_deadline - snapshot_reserve_s
def _topology_lookup_deadline_for_snapshot_refresh(
    *,
    refresh_deadline: float,
    refresh_budget_s: float,
    snapshot_reserve_s: float,
) -> float:
    """Stop topology reconstruction early enough to attempt direct Gamma lookup."""

    pre_capture_deadline = refresh_deadline - snapshot_reserve_s
    gamma_min_slice_s = max(
        0.0,
        float(os.environ.get("ZEUS_REACTOR_GAMMA_LOOKUP_MIN_SECONDS", "15.0")),
    )
    available_pre_capture_s = max(0.0, refresh_budget_s - snapshot_reserve_s)
    gamma_min_slice_s = min(gamma_min_slice_s, available_pre_capture_s)
    return max(refresh_deadline - refresh_budget_s, pre_capture_deadline - gamma_min_slice_s)
def _snapshot_capture_budget_for_refresh(
    *,
    refresh_deadline: float,
    snapshot_reserve_s: float,
) -> float:
    """Return the CLOB capture slice for pending-family snapshot refresh.

    The warm job has two qualitatively different phases: cheap topology/cache
    selection and price capture.  Live evidence showed the selection phase can
    consume the full nominal refresh budget; passing the leftover 0.1s to CLOB
    creates one-row "progress" while every pending family remains effectively
    blocked.  The reserve is therefore a phase budget, not a leftover hint.
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
    min_budget_s = snapshot_reserve_s + target_prefetch_window_s
    remaining_s = refresh_deadline - time.monotonic()
    return max(min_budget_s, remaining_s)


def _refresh_pending_family_snapshots(
    world_conn,
    forecasts_conn,
    *,
    consumer_name: str = "edli_reactor_v1",
    now_utc: datetime | None = None,
    extra_priority_families: Iterable[tuple[str, str, str]] | None = None,
    include_pending_families: bool = True,
) -> dict:
    """Targeted, cache-aware snapshot refresh for pending opportunity event families.

    Decision-driven design ("先有下单结果再去找市场"):
      - Scope: ONLY the families (city/target_date/metric) of PENDING events.
      - Cache: skip entire families whose ALL bins are still fresh.
      - Discovery: Gamma slug lookup scoped to pending target_dates — discovers
        EVERY bin (incl. never-seen illiquid MECE tail bins) via full token payload.
      - CLOB: max_outcomes=None so all family bins are captured (no city cap).
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
    from src.strategy.market_phase import family_venue_closed as _family_venue_closed
    from src.state.db import get_trade_connection, get_trade_connection_read_only

    now_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # Step 1: Collect distinct (city, target_date, metric) for pending events.
    if include_pending_families:
        try:
            pending_rows = _pending_family_rows_for_refresh(
                world_conn, consumer_name=consumer_name
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
        raw = str(city or "").strip()
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
    for row in pending_rows:
        city = _canonical_refresh_city_name(row[0])
        target_date = str(row[1] or "").strip()
        metric = _canonical_refresh_metric(row[2])
        if city and target_date and metric:
            pending_families.append((city, target_date, metric))

    open_rest_priority_families: list[tuple[str, str, str]] = []
    try:
        trade_ro = get_trade_connection_read_only()
        try:
            open_rest_priority_families = _open_rest_family_rows_for_refresh(trade_ro)
        finally:
            trade_ro.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "refresh_pending_family_snapshots: open-rest priority read failed (non-fatal): %s",
            exc,
        )
    held_position_priority_families = sorted(_edli_current_held_position_family_keys())

    priority_families: list[tuple[str, str, str]] = []
    priority_keys: set[tuple[str, str, str]] = set()
    explicit_priority_families = list(extra_priority_families or ())
    for family in (
        explicit_priority_families
        + list(open_rest_priority_families)
        + list(held_position_priority_families)
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
        }

    global _SUBSTRATE_REFRESH_CURSOR, _SUBSTRATE_PRIORITY_REFRESH_CURSOR, _NEW_FAMILY_CONDITION_IDS, _GAMMA_EMPTY_BACKOFF_UNTIL
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
        float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0")),
    )
    refresh_deadline = time.monotonic() + refresh_budget_s
    snapshot_reserve_s = min(
        max(1.0, float(os.environ.get("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", "12.0"))),
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

    write_conn = get_trade_connection(write_class="live")
    try:
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
            if _family_venue_closed(city=city, target_date=target_date, now_utc=now_utc):
                venue_closed_skipped += 1
                continue
            payload = {"city": city, "target_date": target_date, "metric": metric}
            topology_rows = _event_family_market_topology_rows(forecasts_conn, payload)
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

            any_stale = False
            for trow in topology_rows:
                cid = str(trow.get("condition_id") or "").strip()
                if not cid:
                    continue
                if not _condition_buy_sides_fresh(write_conn, cid, now_iso):
                    any_stale = True
                    break

            if any_stale:
                reconstructed = reconstruct_weather_market_from_static_topology(
                    write_conn,
                    topology_rows=topology_rows,
                    now_utc=now_utc,
                )
                if reconstructed is not None:
                    cached_topology_markets.append(reconstructed)
                    cached_topology_families += 1
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

        if not gamma_refresh_families and not cached_topology_markets:
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
            gamma_attempted_family_keys: set[tuple[str, str, str]] = set()
            gamma_empty_family_keys: set[tuple[str, str, str]] = set()

            gamma_jobs: list[dict] = []
            for fam_city, fam_date, fam_metric in gamma_refresh_families:
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
                    gamma_attempted_family_keys.add(job["family_key"])
                    pending_futures[executor.submit(_fetch_gamma_slug, job)] = job

            if gamma_jobs:
                with ThreadPoolExecutor(
                    max_workers=gamma_concurrency,
                    thread_name_prefix="zeus-gamma-refresh",
                ) as executor:
                    _submit_gamma_jobs(executor)
                    while pending_futures:
                        remaining = gamma_deadline - time.monotonic()
                        if remaining <= 0.0:
                            gamma_slug_timebox_unattempted += len(gamma_jobs) - next_job_index
                            for future in pending_futures:
                                future.cancel()
                            logger.info(
                                "refresh_pending_family_snapshots: Gamma time-box %.0fs hit after %d/%d "
                                "submitted families; reserving %.1fs for CLOB capture",
                                max(0.1, gamma_deadline - (refresh_deadline - refresh_budget_s)),
                                gamma_slug_attempted,
                                len(gamma_jobs),
                                snapshot_reserve_s,
                            )
                            next_job_index = len(gamma_jobs)
                            pending_futures.clear()
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
                        _submit_gamma_jobs(executor)

            gamma_slug_timebox_unattempted += len(gamma_jobs) - next_job_index
            if _gamma_empty_backoff_s > 0.0 and gamma_empty_family_keys:
                _eb_deadline = time.monotonic() + _gamma_empty_backoff_s
                for _eb_key in gamma_empty_family_keys:
                    _GAMMA_EMPTY_BACKOFF_UNTIL[_eb_key] = _eb_deadline

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

        # Filter to ONLY the pending families (bounded CLOB calls, no universe sweep).
        markets: list[dict] = []
        markets.extend(cached_topology_markets)
        for city, target_date, metric in gamma_refresh_families:
            key = _refresh_family_key(city, target_date, metric)
            ev = gamma_by_family.get(key)
            if ev is None:
                if key in gamma_attempted_family_keys:
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
                    logger.info(
                        "refresh_pending_family_snapshots: Gamma not attempted before time-box for "
                        "%s/%s/%s — family remains retryable",
                        city, target_date, metric,
                    )
                continue
            markets.append(ev)

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
        _clob_timeout = max(
            1.0,
            float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "5.0")),
        )
        markets_for_refresh, fresh_condition_skipped, stale_condition_submitted = (
            _prune_fresh_market_outcomes_for_snapshot_refresh(
                write_conn,
                markets,
                fresh_at_iso=now_iso,
            )
        )
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
        with PolymarketClient(public_http_timeout=_clob_timeout) as clob:
            summary = refresh_executable_market_substrate_snapshots(
                write_conn,
                markets=markets_for_refresh,
                clob=clob,
                captured_at=datetime.now(timezone.utc),
                scan_authority="VERIFIED",
                max_outcomes=0,  # UNLIMITED: capture every bin of each pending family
                budget_seconds=snapshot_budget_s,
            )
        write_conn.commit()

    except Exception as exc:
        logger.warning("refresh_pending_family_snapshots: failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        write_conn.close()

    result = {
        "status": "refreshed",
        "families_checked": len(families),
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
    logger.info("refresh_pending_family_snapshots: %s", result)
    return result


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
    try:
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
            _discovery_clob_timeout = max(
                1.0,
                float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "5.0")),
            )
            with PolymarketClient(public_http_timeout=_discovery_clob_timeout) as snapshot_clob:
                snapshot_summary = refresh_executable_market_substrate_snapshots(
                    conn,
                    markets=events,
                    clob=snapshot_clob,
                    captured_at=datetime.now(timezone.utc),
                    scan_authority="VERIFIED",
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
    from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection_read_only, get_world_connection

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
            "EDLI market-substrate warm: ATTACH forecasts failed (non-fatal): %r", _attach_exc
        )
    forecasts_conn = get_forecasts_connection_read_only()
    substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
    if not substrate_acquired:
        logger.info("EDLI market-substrate warm skipped: executable substrate refresh already running")
        try:
            forecasts_conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        # _refresh_pending_family_snapshots never raises by contract (it logs+returns an
        # error dict), but wrap defensively so a venue-I/O failure can NEVER propagate out
        # of the scheduler job (the reactor stays decoupled and fail-closed regardless).
        summary = _refresh_pending_family_snapshots(conn, forecasts_conn)
        logger.info("EDLI market-substrate warm: refresh summary=%r", summary)
    except Exception as exc:  # noqa: BLE001 — fail-soft; next tick retries
        logger.error(
            "EDLI market-substrate warm: refresh raised (non-fatal, snapshots did not "
            "advance this tick): %r",
            exc,
        )
    finally:
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
