# Lifecycle: created=2026-04-30; last_reviewed=2026-05-08; last_reused=2026-05-08
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 1.5
"""Ingest-side settlement truth writer (Phase 1.5 harvester split).

Owns world.settlements writes for open/settling markets.
Runs from src/ingest_main.py at hourly cadence via acquire_lock("harvester_truth").

Design invariants:
- Single connection: world_conn = get_world_connection(). NO trade_conn.
- Feature-flagged: ZEUS_HARVESTER_LIVE_ENABLED must equal "1" or function is a no-op.
- NO imports from src.engine, src.execution, src.strategy, src.signal,
  src.control, src.main, src.ingest_main — ingest-side only.
- Logic copied verbatim from src/execution/harvester.py:_write_settlement_truth +
  supporting helpers to avoid circular imports.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

# Default SOURCE_DISAGREEMENT tolerance (°C). Overridden by
# config/settings.json::settlement.disagreement_tolerance_celsius (fix #263).
_DEFAULT_DISAGREEMENT_TOLERANCE_C = 1.0

# Harvester paginator antibody (PLAN §D.1/D.3, critic v4 ACCEPT 2026-05-11).
# Hard-coded module-private constants; no kwargs path exists to relax them.
# Backfill (scripts/backfill_harvester_settlements.py) uses its own loop.
_CLOSED_EVENTS_CUTOFF_DAYS = 30          # live scope: only events closed ≤30d ago
_CLOSED_EVENTS_MAX_WALL_SECONDS = 120    # mandatory wall-cap antibody (Fitz §3)
_CLOSED_EVENTS_PAGE_LIMIT = 100          # ingest twin page size


def _disagreement_tolerance() -> float:
    """Read disagreement tolerance from settings.json; fall back to 1.0°C."""
    try:
        import json as _json
        from src.config import PROJECT_ROOT
        cfg_path = PROJECT_ROOT / "config" / "settings.json"
        with open(cfg_path) as _f:
            _cfg = _json.load(_f)
        return float(
            _cfg.get("settlement", {}).get(
                "disagreement_tolerance_celsius", _DEFAULT_DISAGREEMENT_TOLERANCE_C
            )
        )
    except Exception:
        return _DEFAULT_DISAGREEMENT_TOLERANCE_C


def _nearest_bin_edge_distance(rounded: float, lo: Optional[float], hi: Optional[float]) -> float:
    """Absolute distance (in bin units) from rounded to the nearest bin boundary.

    For a closed bin [lo, hi]: min distance to lo or hi.
    For open-shoulder lo-only (hi=None): distance to lo.
    For open-shoulder hi-only (lo=None): distance to hi.
    Returns inf if both are None (caller should not reach here in that case).
    """
    distances = []
    if lo is not None:
        distances.append(abs(rounded - lo))
    if hi is not None:
        distances.append(abs(rounded - hi))
    return min(distances) if distances else math.inf

from src.config import City, cities_by_name
from src.contracts.settlement_semantics import SettlementSemantics
from src.contracts.exceptions import SettlementPrecisionError
from src.state.db import (
    get_world_connection,
    log_market_event_outcomes_v2,
    log_settlement_v2,
)
from src.types.metric_identity import MetricIdentity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (copied from harvester.py — no runtime circular dependency)
# ---------------------------------------------------------------------------

_HARVESTER_LIVE_DATA_VERSION = {
    "wu_icao": "wu_icao_history_v1",
    "hko": "hko_daily_api_v1",
    "noaa": "ogimet_metar_v1",
    "cwa_station": "cwa_no_collector_v0",
}

_SOURCE_TYPE_MAP = {
    "wu_icao": "WU",
    "hko": "HKO",
    "noaa": "NOAA",
    "cwa_station": "CWA",
}

_TRAINING_FORECAST_SOURCES = frozenset({"tigge", "ecmwf_ens"})


# ---------------------------------------------------------------------------
# Private helpers (ingest-side copies; no harvester.py import)
# ---------------------------------------------------------------------------

def _metric_identity_for(temperature_metric: str | MetricIdentity) -> MetricIdentity:
    return MetricIdentity.from_raw(temperature_metric)


def _detect_bin_unit(question: str) -> Optional[str]:
    """Detect temperature unit ('F' or 'C') from a Polymarket market question string.

    Returns 'F', 'C', or None if no unit symbol found. Used to detect pre-2026
    London Gamma markets that were posed in degrees F even though London is now
    configured as a degrees C city (fix #262).

    Checks for degrees F before degrees C -- if both appear (should not happen in
    practice for a single-unit question), F takes precedence defensively.
    """
    if re.search(r"\xb0[Ff]", question):
        return "F"
    if re.search(r"\xb0[Cc]", question):
        return "C"
    return None


def _f_to_c(val: float) -> float:
    """Convert Fahrenheit to Celsius: (F - 32) x 5/9."""
    return (val - 32.0) * 5.0 / 9.0


def _canonical_bin_label(lo: Optional[float], hi: Optional[float], unit: str) -> Optional[str]:
    """Canonical winning_bin label matching P-E reconstruction convention."""
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        if lo == hi:
            return f"{int(lo)}°{unit}"
        return f"{int(lo)}-{int(hi)}°{unit}"
    if lo is None and hi is not None:
        return f"{int(hi)}°{unit} or below"
    return f"{int(lo)}°{unit} or higher"


def _table_column_names(conn, table_name: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [
            str(r["name"] if hasattr(r, "keys") else r[1])
            for r in rows
        ]
    except Exception:
        return []


def _row_value(row, key: str):
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _source_matches_settlement_family(source: str, settlement_source_type: str) -> bool:
    """Route obs to the correct source-family per DR-33 plan §3.3."""
    if settlement_source_type == "wu_icao":
        return source == "wu_icao_history"
    if settlement_source_type == "noaa":
        return source.startswith("ogimet_metar_")
    if settlement_source_type == "hko":
        return source == "hko_daily_api"
    return False


def _station_matches_city(station_id, city: City) -> bool:
    if not station_id:
        return True
    city_station = getattr(city, "station_id", None) or getattr(city, "icao", None)
    if not city_station:
        return True
    return str(station_id).upper() == str(city_station).upper()


def _lookup_settlement_obs(
    conn,
    city: City,
    target_date: str,
    *,
    temperature_metric: str = "high",
) -> Optional[dict]:
    """Look up source-family-correct observation for the harvester write path."""
    metric_identity = _metric_identity_for(temperature_metric)
    st = city.settlement_source_type
    if st == "cwa_station":
        return None
    column_names = _table_column_names(conn, "observations")
    columns = set(column_names)
    if not columns:
        return None
    metric_field = metric_identity.observation_field
    if metric_field not in columns:
        return None
    rows = conn.execute(
        "SELECT * FROM observations WHERE city = ? AND target_date = ?",
        (city.name, target_date),
    ).fetchall()
    for r in rows:
        if not isinstance(r, (sqlite3.Row, dict)):
            r = dict(zip(column_names, r))
        src = str(_row_value(r, "source") or "")
        if not _source_matches_settlement_family(src, st):
            continue
        if "authority" in columns and str(_row_value(r, "authority") or "").upper() != "VERIFIED":
            continue
        if "station_id" in columns and not _station_matches_city(_row_value(r, "station_id"), city):
            continue
        observed_temp = _row_value(r, metric_field)
        if observed_temp is None:
            continue
        return {
            "id": _row_value(r, "id"),
            "source": src,
            "high_temp": _row_value(r, "high_temp"),
            "low_temp": _row_value(r, "low_temp"),
            "unit": _row_value(r, "unit"),
            "fetched_at": _row_value(r, "fetched_at"),
            "station_id": _row_value(r, "station_id"),
            "authority": _row_value(r, "authority"),
            "observation_field": metric_field,
            "observed_temp": observed_temp,
        }
    return None


def _fetch_open_settling_markets() -> list[dict]:
    """Poll Gamma API for recently settled weather markets (world-side only).

    Bounded paginator: fetches closed events in descending endDate order and
    stops once the oldest event in a page crosses the 30-day cutoff window.
    A mandatory wall-cap fires unconditionally at _CLOSED_EVENTS_MAX_WALL_SECONDS
    regardless of Gamma API ordering behaviour (Fitz §3 antibody).

    Returns list of settled event dicts.  Returns [] on any HTTP failure.
    """
    try:
        from src.data.market_scanner import GAMMA_BASE
        import httpx
    except ImportError:
        logger.warning("harvester_truth_writer: market_scanner or httpx not available")
        return []

    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=_CLOSED_EVENTS_CUTOFF_DAYS)
    ).isoformat()
    start_wall = time.monotonic()
    results: list[dict] = []
    offset = 0

    while True:
        if time.monotonic() - start_wall > _CLOSED_EVENTS_MAX_WALL_SECONDS:
            logger.warning(
                "harvester_truth_writer paginator: wall-cap %.0fs hit at offset=%d; truncating",
                _CLOSED_EVENTS_MAX_WALL_SECONDS,
                offset,
            )
            break
        try:
            resp = httpx.get(
                f"{GAMMA_BASE}/events",
                params={
                    "closed": "true",
                    "limit": _CLOSED_EVENTS_PAGE_LIMIT,
                    "offset": offset,
                    "order": "endDate",
                    "ascending": "false",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            batch = resp.json()
        except Exception as exc:
            if offset == 0:
                logger.warning("harvester_truth_writer: Gamma fetch failed: %s", exc)
            break

        if not batch:
            break

        results.extend(batch)
        oldest_end = min(
            (m.get("endDate", "") for m in batch if m.get("endDate")),
            default="",
        )
        if oldest_end and oldest_end < cutoff_iso:
            break  # absorb this page; dedup downstream
        if len(batch) < _CLOSED_EVENTS_PAGE_LIMIT:
            break
        offset += _CLOSED_EVENTS_PAGE_LIMIT

    # Dedup at event grain by (conditionId or id).
    # Downstream INSERT OR IGNORE is the authoritative uniqueness guard;
    # this set is an HTTP-cost optimisation only (PLAN §D.1).
    seen: set[str] = set()
    deduped: list[dict] = []
    for ev in results:
        key = str(ev.get("conditionId") or ev.get("id") or "")
        if not key:
            deduped.append(ev)
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(ev)
    return deduped


def _extract_resolved_market_outcomes(event: dict) -> list[dict]:
    """Extract resolved market outcomes as dicts (range_low, range_high, yes_won)."""
    from src.data.market_scanner import _parse_temp_range, infer_temperature_metric
    outcomes: list[dict] = []
    for market in event.get("markets", []) or []:
        outcome_str = str(market.get("outcomePrices") or "")
        tokens = market.get("clobTokenIds") or []
        yes_price = None
        try:
            import ast
            prices = ast.literal_eval(outcome_str) if outcome_str else []
            yes_price = float(prices[0]) if prices else None
        except Exception:
            pass
        question = str(market.get("question") or market.get("groupItemTitle") or "")
        lo, hi = _parse_temp_range(question)
        yes_won = bool(yes_price is not None and yes_price >= 0.99)
        condition_id = str(market.get("conditionId") or "")
        yes_token_id = str(tokens[0]) if tokens else ""
        outcomes.append({
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "range_label": question,
            "range_low": lo,
            "range_high": hi,
            "yes_won": yes_won,
        })
    return outcomes


def _disagreement_tolerance() -> float:
    """Tolerance (in settlement units) for SOURCE_DISAGREEMENT classification (fix #263).

    If the rounded obs is within this distance of the nearest bin edge, the
    quarantine reason is 'harvester_source_disagreement_within_tolerance' rather
    than 'harvester_live_obs_outside_bin'.  1.0 unit = one settlement integer step.
    """
    return 1.0


def _nearest_bin_edge_distance(
    rounded: float,
    effective_bin_lo: Optional[float],
    effective_bin_hi: Optional[float],
) -> float:
    """Return the distance from rounded obs to the nearest bin boundary (fix #263).

    For a closed bin [lo, hi]: min(|obs - lo|, |obs - hi|).
    For an open-shoulder bin (lo only or hi only): distance to the single edge.
    Returns float('inf') when no bin bounds are available.
    """
    distances: list[float] = []
    if effective_bin_lo is not None:
        distances.append(abs(rounded - effective_bin_lo))
    if effective_bin_hi is not None:
        distances.append(abs(rounded - effective_bin_hi))
    return min(distances) if distances else float("inf")


def _write_settlement_truth(
    conn,
    city: City,
    target_date: str,
    pm_bin_lo: Optional[float],
    pm_bin_hi: Optional[float],
    *,
    event_slug: str = "",
    obs_row: Optional[dict] = None,
    resolved_market_outcomes: Optional[list[dict]] = None,
    temperature_metric: str | MetricIdentity = "high",
    pm_bin_unit: Optional[str] = None,
) -> dict:
    """Write canonical-authority settlement truth to settlements table.

    This is an ingest-side copy of harvester.py:_write_settlement_truth.
    Writes ONLY to world_conn (settlements, settlements_v2, market_events_v2).
    Does NOT commit -- caller owns transaction boundary.

    pm_bin_unit: the unit of pm_bin_lo/pm_bin_hi as parsed from the market question
        ('F' or 'C'). When pm_bin_unit='F' and city.settlement_unit='C', the bin
        bounds are converted F->C before containment check (fix #262: pre-2026
        London markets were posed in F; London is now a C city).
    """
    db_source_type = _SOURCE_TYPE_MAP.get(city.settlement_source_type, city.settlement_source_type.upper())
    data_version = _HARVESTER_LIVE_DATA_VERSION.get(
        city.settlement_source_type, "unknown_v0"
    )
    metric_identity = _metric_identity_for(temperature_metric)
    settled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    authority = "QUARANTINED"
    settlement_value: Optional[float] = None
    winning_bin: Optional[str] = None
    reason: Optional[str] = None
    rounding_rule: str = "wmo_half_up"
    bin_unit_converted: bool = False

    observation_value = (
        obs_row.get(metric_identity.observation_field)
        if obs_row is not None
        else None
    )
    if obs_row is None or observation_value is None:
        reason = "harvester_live_no_obs"
    else:
        try:
            sem = SettlementSemantics.for_city(city)
            rounding_rule = sem.rounding_rule
            rounded = sem.assert_settlement_value(
                float(observation_value),
                context=f"harvester_truth_writer/{city.name}/{target_date}",
            )
        except SettlementPrecisionError:
            reason = "harvester_live_settlement_precision_error"
            rounded = None

        if rounded is not None and math.isfinite(rounded):
            contained = False
            if pm_bin_lo is None and pm_bin_hi is None:
                # No bin information available -- cannot evaluate containment.
                # Record the observation value but quarantine with a distinct reason
                # so data consumers can distinguish "obs outside known bin" from
                # "no bin was provided at all" (e.g. uma_backfill synthetic slugs).
                settlement_value = rounded
                reason = "harvester_live_no_bin_info"
            else:
                # Fix #262: pre-2026 London Gamma markets were posed in degrees F
                # (bin values like 40-41 are F). London is now configured as a C city.
                # Convert bin bounds to C before containment so the check operates in
                # matching units. Applies whenever pm_bin_unit differs from
                # city.settlement_unit (F bin vs C city is the only live case).
                effective_bin_lo = pm_bin_lo
                effective_bin_hi = pm_bin_hi
                if pm_bin_unit == "F" and city.settlement_unit == "C":
                    if effective_bin_lo is not None:
                        effective_bin_lo = _f_to_c(effective_bin_lo)
                    if effective_bin_hi is not None:
                        effective_bin_hi = _f_to_c(effective_bin_hi)
                    bin_unit_converted = True
                    logger.debug(
                        "harvester_truth_writer: bin unit mismatch for %s %s -- "
                        "converted F bin [%s, %s] -> C [%.4f, %.4f] (fix #262)",
                        city.name, target_date,
                        pm_bin_lo, pm_bin_hi,
                        effective_bin_lo if effective_bin_lo is not None else 0.0,
                        effective_bin_hi if effective_bin_hi is not None else 0.0,
                    )
                # Fix #264: Polymarket °C bins are INTEGER. After F→C conversion
                # bin bounds are floats (e.g. 48°F → 8.888°C). Snap both edges via
                # WMO half-up before containment so float precision does not cause
                # false negatives. Closed bin: obs in {lo_int, hi_int}.
                # Open-shoulder: obs <= snapped_hi  or  obs >= snapped_lo.
                if bin_unit_converted:
                    if effective_bin_lo is not None:
                        effective_bin_lo = math.floor(effective_bin_lo + 0.5)
                    if effective_bin_hi is not None:
                        effective_bin_hi = math.floor(effective_bin_hi + 0.5)
                    if effective_bin_lo is not None and effective_bin_hi is not None:
                        contained = rounded in {effective_bin_lo, effective_bin_hi}
                    elif effective_bin_lo is None and effective_bin_hi is not None:
                        contained = rounded <= effective_bin_hi
                    elif effective_bin_hi is None and effective_bin_lo is not None:
                        contained = rounded >= effective_bin_lo
                elif effective_bin_lo is not None and effective_bin_hi is not None:
                    contained = effective_bin_lo <= rounded <= effective_bin_hi
                elif effective_bin_lo is None and effective_bin_hi is not None:
                    contained = rounded <= effective_bin_hi
                elif effective_bin_hi is None and effective_bin_lo is not None:
                    contained = rounded >= effective_bin_lo
                if contained:
                    authority = "VERIFIED"
                    settlement_value = rounded
                    # Use effective (possibly converted) bin bounds for the label so
                    # "40-41°F bin, London C city" → "4-5°C" not "40-41°C" (fix #262 P1).
                    winning_bin = _canonical_bin_label(effective_bin_lo, effective_bin_hi, city.settlement_unit)
                    reason = None
                else:
                    settlement_value = rounded
                    # Fix #263: distinguish SOURCE_DISAGREEMENT from genuine
                    # obs_outside_bin. If the obs rounds to within ±tolerance of
                    # the nearest bin edge (i.e. one source passes, the other
                    # just misses due to measurement/rounding variance), emit a
                    # distinct quarantine reason so operators can triage separately.
                    # "Both outside bin" scenario: obs is far from the bin — keep
                    # obs_outside_bin. When only one source fails containment and
                    # they are within tolerance, emit source_disagreement.
                    _tol = _disagreement_tolerance()
                    _dist = _nearest_bin_edge_distance(
                        rounded, effective_bin_lo, effective_bin_hi
                    )
                    if _dist <= _tol:
                        reason = "harvester_source_disagreement_within_tolerance"
                        logger.debug(
                            "harvester_truth_writer: source disagreement %s %s — "
                            "obs=%.1f nearest_bin_edge_dist=%.2f tol=%.1f (fix #263)",
                            city.name, target_date, rounded, _dist, _tol,
                        )
                    else:
                        reason = "harvester_live_obs_outside_bin"

    provenance = {
        "writer": "harvester_truth_writer_dr33",
        "writer_script": "src/ingest/harvester_truth_writer.py",
        "source_family": db_source_type,
        "obs_source": obs_row.get("source") if obs_row else None,
        "obs_id": obs_row.get("id") if obs_row else None,
        "decision_time_snapshot_id": obs_row.get("fetched_at") if obs_row else None,
        "rounding_rule": rounding_rule,
        "reconstruction_method": "harvester_live_uma_vote",
        "event_slug": event_slug or None,
        "pm_bin_lo": pm_bin_lo,
        "pm_bin_hi": pm_bin_hi,
        "pm_bin_unit": pm_bin_unit,
        "bin_unit_converted": bin_unit_converted,
        "unit": city.settlement_unit,
        "settlement_source_type": db_source_type,
        "temperature_metric": metric_identity.temperature_metric,
        "physical_quantity": metric_identity.physical_quantity,
        "observation_field": metric_identity.observation_field,
        "data_version": data_version,
        "reconstructed_at": settled_at,
        "audit_ref": "docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 1.5",
    }
    if reason is not None:
        provenance["quarantine_reason"] = reason

    settlement_v2_result: dict = {}
    market_events_v2_result: dict = {}
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO settlements (
                city, target_date, market_slug, winning_bin, settlement_value,
                settlement_source, settled_at, authority,
                pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
                temperature_metric, physical_quantity, observation_field,
                data_version, provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city.name, target_date, event_slug or None, winning_bin, settlement_value,
                city.settlement_source, settled_at, authority,
                pm_bin_lo, pm_bin_hi, city.settlement_unit, db_source_type,
                metric_identity.temperature_metric,
                metric_identity.physical_quantity,
                metric_identity.observation_field,
                data_version, json.dumps(provenance, sort_keys=True, default=str),
            ),
        )
        settlement_v2_result = log_settlement_v2(
            conn,
            city=city.name,
            target_date=target_date,
            temperature_metric=metric_identity.temperature_metric,
            market_slug=event_slug or None,
            winning_bin=winning_bin,
            settlement_value=settlement_value,
            settlement_source=city.settlement_source,
            settled_at=settled_at,
            authority=authority,
            provenance=provenance,
            recorded_at=settled_at,
        )
        if authority == "VERIFIED" and resolved_market_outcomes:
            outcomes_v2 = [
                {
                    "condition_id": o["condition_id"],
                    "token_id": o["yes_token_id"],
                    "outcome": "YES" if o["yes_won"] else "NO",
                }
                for o in resolved_market_outcomes
            ]
            market_events_v2_result = log_market_event_outcomes_v2(
                conn,
                market_slug=event_slug or None,
                city=city.name,
                target_date=target_date,
                temperature_metric=metric_identity.temperature_metric,
                outcomes=outcomes_v2,
            )
        elif resolved_market_outcomes:
            market_events_v2_result = {
                "status": "skipped_unverified_settlement",
                "table": "market_events_v2",
                "authority": authority,
            }
        else:
            market_events_v2_result = {
                "status": "skipped_no_resolved_market_identity",
                "table": "market_events_v2",
            }
        logger.info(
            "harvester_truth_writer write: %s %s → authority=%s settlement_value=%s "
            "winning_bin=%s reason=%s settlements_v2=%s market_events_v2=%s",
            city.name, target_date, authority, settlement_value, winning_bin, reason,
            settlement_v2_result.get("status"), market_events_v2_result.get("status"),
        )
    except Exception as exc:
        logger.warning(
            "harvester_truth_writer write failed for %s %s: %s", city.name, target_date, exc,
        )
        raise

    return {
        "authority": authority,
        "settlement_value": settlement_value,
        "winning_bin": winning_bin,
        "reason": reason,
        "settlement_v2": settlement_v2_result,
        "market_events_v2": market_events_v2_result,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_settlement_truth_for_open_markets(
    world_conn,
    *,
    dry_run: bool = False,
) -> dict:
    """Write world.settlements for all currently settling markets.

    Entry point for the ingest-side harvester tick.
    Feature flag: ZEUS_HARVESTER_LIVE_ENABLED must equal "1" or returns disabled status.

    Parameters
    ----------
    world_conn:
        A connection returned by get_world_connection().  NO trade_conn used.
    dry_run:
        If True, fetches and processes but does not commit.

    Returns
    -------
    dict with keys: markets_resolved, settlements_written, errors.
    """
    if os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED", "0") != "1":
        logger.info(
            "harvester_truth_writer disabled by ZEUS_HARVESTER_LIVE_ENABLED flag "
            "(DR-33-A default-OFF); cycle skipped"
        )
        return {
            "status": "disabled_by_feature_flag",
            "disabled_by_flag": True,
            "markets_resolved": 0,
            "settlements_written": 0,
            "errors": 0,
        }

    from src.data.market_scanner import _match_city, infer_temperature_metric

    settled_events = _fetch_open_settling_markets()
    logger.info("harvester_truth_writer: found %d settled events", len(settled_events))

    markets_resolved = 0
    settlements_written = 0
    errors = 0

    for event in settled_events:
        try:
            city = _match_city(
                (event.get("title") or "").lower(),
                event.get("slug", ""),
            )
            if city is None:
                continue

            # Extract target date
            target_date: Optional[str] = None
            try:
                from src.data.market_scanner import _parse_target_date
                target_date = _parse_target_date(event)
            except Exception:
                pass
            if target_date is None:
                continue

            temperature_metric = infer_temperature_metric(
                event.get("title", ""),
                event.get("slug", ""),
                *[
                    str(market.get("question") or market.get("groupItemTitle") or "")
                    for market in event.get("markets", []) or []
                ],
            )

            resolved_market_outcomes = _extract_resolved_market_outcomes(event)
            winning_outcomes = [o for o in resolved_market_outcomes if o["yes_won"]]
            if len(winning_outcomes) != 1:
                if winning_outcomes:
                    logger.warning(
                        "harvester_truth_writer: skipping %s %s ambiguous winners=%d slug=%s",
                        city.name, target_date, len(winning_outcomes), event.get("slug", ""),
                    )
                continue
            winning = winning_outcomes[0]
            pm_bin_lo, pm_bin_hi = winning["range_low"], winning["range_high"]

            winning_label = _canonical_bin_label(pm_bin_lo, pm_bin_hi, city.settlement_unit)
            if winning_label is None:
                logger.warning(
                    "harvester_truth_writer: both pm_bin_lo and pm_bin_hi are None; "
                    "skipping %s %s (degenerate bin)",
                    city.name, target_date,
                )
                continue

            obs_row = _lookup_settlement_obs(
                world_conn, city, target_date, temperature_metric=temperature_metric,
            )
            if obs_row is None:
                logger.debug(
                    "harvester_truth_writer: skipping %s %s — no source-correct obs yet",
                    city.name, target_date,
                )
                continue

            markets_resolved += 1

            if dry_run:
                logger.info(
                    "harvester_truth_writer DRY-RUN: would write %s %s authority=pending",
                    city.name, target_date,
                )
                settlements_written += 1
                continue

            # Detect the unit of the winning bin from its market question text.
            # Pre-2026 London markets used F bins; London is now a C city.
            # _write_settlement_truth converts F->C when units mismatch (fix #262).
            winning_bin_unit = _detect_bin_unit(winning.get("range_label", ""))

            _write_settlement_truth(
                world_conn, city, target_date, pm_bin_lo, pm_bin_hi,
                event_slug=event.get("slug", ""),
                obs_row=obs_row,
                resolved_market_outcomes=resolved_market_outcomes,
                temperature_metric=temperature_metric,
                pm_bin_unit=winning_bin_unit,
            )
            settlements_written += 1

        except Exception as exc:
            logger.error(
                "harvester_truth_writer error for event %s: %s",
                event.get("slug", "?"), exc,
            )
            errors += 1

    if not dry_run:
        try:
            world_conn.commit()
        except Exception as exc:
            logger.error("harvester_truth_writer: commit failed: %s", exc)
            errors += 1

    return {
        "status": "ok",
        "markets_resolved": markets_resolved,
        "settlements_written": settlements_written,
        "errors": errors,
        "dry_run": dry_run,
    }
