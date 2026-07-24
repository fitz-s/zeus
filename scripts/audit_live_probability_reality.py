#!/usr/bin/env python3
# Lifecycle: created=2026-07-09; last_reviewed=2026-07-09; last_reused=2026-07-09
# Purpose: read-only observation for live position probability vs real settlement/PnL evidence.
# Authority basis: AGENTS.md live-money proof gates; operator focus on probability/reality alignment.
"""Audit live position probabilities against real settled outcomes and monitor evidence.

This observation reads canonical trade/world DBs in read-only SQLite mode. It
does not mutate DB truth, contact venues, authorize live trading, or restart
daemons.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_TRADE_DB = ROOT / "state" / "zeus_trades.db"
DEFAULT_WORLD_DB = ROOT / "state" / "zeus-world.db"
CURRENT_SOURCE_PPC_MISMATCH_EPS = 0.025


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        if not math.isfinite(out):
            return None
        return out
    except (TypeError, ValueError):
        return None


def _prob_bin(value: float | None) -> str:
    if value is None:
        return "missing"
    edges = (0.5, 0.7, 0.8, 0.85, 0.9, 0.95, 1.01)
    lo = 0.0
    for hi in edges:
        if lo <= value < hi:
            return f"[{lo:.2f},{hi:.2f})"
        lo = hi
    return "[1.01,+)"


def _summarize_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    n = len(rows)
    with_outcome = [r for r in rows if r["outcome"] is not None]
    wins = sum(1 for r in with_outcome if int(r["outcome"]) == 1)
    pnl = sum(float(r["pnl"] or 0.0) for r in with_outcome)
    probs = [_float(r["p_posterior"]) for r in with_outcome]
    probs = [p for p in probs if p is not None]
    outcome_monitors = [int(r["monitor_count"] or 0) for r in with_outcome]
    actual_monitors = [
        int(r["actual_monitor_events"])
        for r in with_outcome
        if r["actual_monitor_events"] is not None
    ]
    projection_gap = [
        r
        for r in with_outcome
        if int(r["monitor_count"] or 0) == 0
        and r["actual_monitor_events"] is not None
        and int(r["actual_monitor_events"] or 0) > 0
    ]
    return {
        "positions": n,
        "with_outcome_fact": len(with_outcome),
        "wins": wins,
        "win_rate": (wins / len(with_outcome)) if with_outcome else None,
        "avg_declared_probability": (sum(probs) / len(probs)) if probs else None,
        "pnl_usd": pnl,
        "outcome_monitor_zero": sum(1 for m in outcome_monitors if m == 0),
        "avg_outcome_monitor_count": (
            sum(outcome_monitors) / len(outcome_monitors)
        ) if outcome_monitors else None,
        "actual_monitor_zero": sum(1 for m in actual_monitors if m == 0),
        "actual_monitor_unknown": len(with_outcome) - len(actual_monitors),
        "avg_actual_monitor_events": (
            sum(actual_monitors) / len(actual_monitors)
        ) if actual_monitors else None,
        "monitor_projection_gap": len(projection_gap),
    }


def _row_sample(row: sqlite3.Row) -> dict[str, Any]:
    keys = (
        "position_id",
        "strategy_key",
        "direction",
        "city",
        "target_date",
        "temperature_metric",
        "bin_label",
        "p_posterior",
        "entry_price",
        "cost_basis_usd",
        "shares",
        "phase",
        "chain_state",
        "order_status",
        "settled_at",
        "outcome",
        "pnl",
        "monitor_count",
        "actual_monitor_events",
        "last_monitor_event_at",
        "all_position_events",
        "hold_duration_hours",
    )
    return {key: row[key] for key in keys if key in row.keys()}


def _settled_position_rows(conn: sqlite3.Connection, *, days: float | None) -> list[sqlite3.Row]:
    has_position_events = conn.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name = 'position_events'
        """
    ).fetchone() is not None
    monitor_select = (
        """
            (
                SELECT COUNT(*)
                  FROM position_events e
                 WHERE e.position_id = p.position_id
                   AND e.event_type = 'MONITOR_REFRESHED'
            ) AS actual_monitor_events,
            (
                SELECT MAX(e.occurred_at)
                  FROM position_events e
                 WHERE e.position_id = p.position_id
                   AND e.event_type = 'MONITOR_REFRESHED'
            ) AS last_monitor_event_at,
            (
                SELECT COUNT(*)
                  FROM position_events e
                 WHERE e.position_id = p.position_id
            ) AS all_position_events,
        """
        if has_position_events
        else """
            NULL AS actual_monitor_events,
            NULL AS last_monitor_event_at,
            NULL AS all_position_events,
        """
    )
    where = "p.phase = 'settled'"
    params: list[Any] = []
    if days is not None:
        where += " AND datetime(p.settled_at) >= datetime('now', ?)"
        params.append(f"-{float(days)} days")
    return conn.execute(
        f"""
        SELECT
            p.position_id, p.strategy_key, p.direction, p.city, p.target_date,
            p.temperature_metric, p.bin_label, p.p_posterior, p.entry_price,
            p.cost_basis_usd, p.shares, p.phase, p.chain_state, p.order_status,
            p.realized_pnl_usd, p.settled_at,
            o.outcome, o.pnl, o.monitor_count,
            {monitor_select}
            o.hold_duration_hours
          FROM position_current p
          LEFT JOIN outcome_fact o ON o.position_id = p.position_id
         WHERE {where}
         ORDER BY datetime(p.settled_at) DESC
        """,
        params,
    ).fetchall()


def _open_position_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, shares, chain_shares, cost_basis_usd,
            entry_price, p_posterior, last_monitor_prob, last_monitor_market_price,
            last_monitor_prob_is_fresh, last_monitor_market_price_is_fresh,
            updated_at, exit_reason, chain_state, order_status
          FROM position_current
         WHERE phase NOT IN ('settled', 'voided', 'economically_closed', 'admin_closed')
         ORDER BY datetime(updated_at) DESC
        """
    ).fetchall()


def _monitor_probability_jump_rows(
    conn: sqlite3.Connection,
    *,
    min_abs_delta: float = 0.10,
    sample_limit: int = 20,
) -> list[sqlite3.Row]:
    """Find single-cycle open-position probability jumps in monitor evidence."""

    has_position_events = conn.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name = 'position_events'
        """
    ).fetchone() is not None
    if not has_position_events:
        return []
    return conn.execute(
        """
        WITH open_positions AS (
            SELECT position_id, city, target_date, temperature_metric, bin_label,
                   direction, strategy_key
              FROM position_current
             WHERE phase NOT IN ('settled', 'voided', 'economically_closed', 'admin_closed')
        ),
        monitor AS (
            SELECT
                p.position_id, p.city, p.target_date, p.temperature_metric,
                p.bin_label, p.direction, p.strategy_key,
                e.occurred_at,
                CAST(json_extract(e.payload_json, '$.last_monitor_prob') AS REAL) AS prob,
                json_extract(e.payload_json, '$.last_monitor_prob_is_fresh') AS prob_is_fresh,
                CAST(json_extract(e.payload_json, '$.last_monitor_market_price') AS REAL) AS market_price,
                json_extract(e.payload_json, '$.last_monitor_market_price_is_fresh') AS market_price_is_fresh,
                json_extract(e.payload_json, '$.selected_method') AS selected_method,
                json_extract(e.payload_json, '$.day0_monitor_probability_receipt') AS day0_receipt,
                json_extract(e.payload_json, '$.exit_decision_reason') AS exit_decision_reason,
                json_extract(e.payload_json, '$.applied_validations') AS applied_validations
              FROM position_events e
              JOIN open_positions p ON p.position_id = e.position_id
             WHERE e.event_type = 'MONITOR_REFRESHED'
               AND json_extract(e.payload_json, '$.last_monitor_prob') IS NOT NULL
        ),
        diffs AS (
            SELECT
                monitor.*,
                LAG(prob) OVER (
                    PARTITION BY position_id
                    ORDER BY datetime(occurred_at), occurred_at
                ) AS previous_prob,
                LAG(market_price) OVER (
                    PARTITION BY position_id
                    ORDER BY datetime(occurred_at), occurred_at
                ) AS previous_market_price,
                LAG(prob_is_fresh) OVER (
                    PARTITION BY position_id
                    ORDER BY datetime(occurred_at), occurred_at
                ) AS previous_prob_is_fresh,
                LAG(market_price_is_fresh) OVER (
                    PARTITION BY position_id
                    ORDER BY datetime(occurred_at), occurred_at
                ) AS previous_market_price_is_fresh,
                LAG(occurred_at) OVER (
                    PARTITION BY position_id
                    ORDER BY datetime(occurred_at), occurred_at
                ) AS previous_occurred_at,
                LAG(selected_method) OVER (
                    PARTITION BY position_id
                    ORDER BY datetime(occurred_at), occurred_at
                ) AS previous_selected_method,
                LAG(day0_receipt) OVER (
                    PARTITION BY position_id
                    ORDER BY datetime(occurred_at), occurred_at
                ) AS previous_day0_receipt
              FROM monitor
        )
        SELECT
            position_id, city, target_date, temperature_metric, bin_label,
            direction, strategy_key, previous_occurred_at, occurred_at,
            previous_prob, prob, (prob - previous_prob) AS delta_prob,
            previous_prob_is_fresh, prob_is_fresh,
            previous_market_price, market_price,
            (market_price - previous_market_price) AS delta_market_price,
            previous_market_price_is_fresh, market_price_is_fresh,
            previous_selected_method, selected_method,
            previous_day0_receipt, day0_receipt,
            exit_decision_reason, applied_validations
          FROM diffs
         WHERE previous_prob IS NOT NULL
           AND ABS(prob - previous_prob) >= ?
         ORDER BY ABS(prob - previous_prob) DESC,
                  datetime(occurred_at) DESC
         LIMIT ?
        """,
        (float(min_abs_delta), int(max(0, sample_limit))),
    ).fetchall()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested(mapping: dict[str, Any], *keys: str) -> Any:
    cur: Any = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _json_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _forecast_cycle(receipt: dict[str, Any]) -> str | None:
    validations = _nested(receipt, "remaining_window", "forecast_source_validations")
    if not isinstance(validations, list):
        return None
    for item in validations:
        text = str(item)
        if text.startswith("forecast_source_cycle_time:"):
            return text.split(":", 1)[1]
    return None


def _forecast_validation_value(receipt: dict[str, Any], prefix: str) -> str | None:
    validations = _nested(receipt, "remaining_window", "forecast_source_validations")
    if not isinstance(validations, list):
        return None
    marker = f"{prefix}:"
    for item in validations:
        text = str(item)
        if text.startswith(marker):
            return text.split(":", 1)[1]
    return None


def _remaining_window_source(receipt: dict[str, Any]) -> str | None:
    value = _nested(receipt, "remaining_window", "source")
    return str(value) if value not in (None, "") else None


def _unconditioned_daily_extrema_receipt(receipt: dict[str, Any], selected_method: Any) -> bool:
    return (
        str(selected_method or "") == "day0_observation_remaining_window"
        and _remaining_window_source(receipt) == "day0_raw_model_extrema"
        and _forecast_validation_value(receipt, "forecast_source_role")
        == "day0_daily_extrema_live"
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _city_timezone(city_name: str) -> str | None:
    try:
        from src.config import cities_by_name

        city = cities_by_name.get(city_name)
        return str(getattr(city, "timezone", "") or "") or None
    except Exception:
        return None


def _receipt_local_hour(
    *,
    city_name: str,
    receipt: dict[str, Any],
    fallback_occurred_at: Any,
) -> float | None:
    timestamp = _parse_timestamp(
        _nested(receipt, "temporal_context", "current_utc_timestamp")
    )
    if timestamp is None:
        timestamp = _parse_timestamp(fallback_occurred_at)
    timezone_name = _city_timezone(city_name)
    if timestamp is None or timezone_name is None:
        return None
    try:
        local = timestamp.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        return None
    return (
        local.hour
        + local.minute / 60.0
        + local.second / 3600.0
        + local.microsecond / 3_600_000_000.0
    )


def _solar_day_from_readonly(
    conn: sqlite3.Connection,
    *,
    city_name: str,
    target_date: date,
):
    try:
        from src.types import SolarDay

        row = conn.execute(
            """
            SELECT timezone, sunrise_local, sunset_local, sunrise_utc, sunset_utc,
                   utc_offset_minutes, dst_active
              FROM solar_daily
             WHERE city = ? AND target_date = ?
            """,
            (city_name, target_date.isoformat()),
        ).fetchone()
        if row is None:
            return None
        return SolarDay(
            city=city_name,
            target_date=target_date,
            timezone=row["timezone"],
            sunrise_local=datetime.fromisoformat(row["sunrise_local"]),
            sunset_local=datetime.fromisoformat(row["sunset_local"]),
            sunrise_utc=datetime.fromisoformat(row["sunrise_utc"]),
            sunset_utc=datetime.fromisoformat(row["sunset_utc"]),
            utc_offset_minutes=int(row["utc_offset_minutes"]),
            dst_active=bool(row["dst_active"]),
        )
    except Exception:
        return None


def _current_source_post_peak_evidence(
    row: sqlite3.Row,
    receipt: dict[str, Any],
    *,
    world_conn: sqlite3.Connection | None,
    occurred_at_key: str,
) -> dict[str, Any]:
    if not receipt or world_conn is None:
        return {
            "local_hour": None,
            "post_peak_confidence": None,
            "error": None if not receipt else "world_conn_unavailable",
        }
    city_name = str(row["city"] or "")
    target = _parse_date(row["target_date"])
    local_hour = _receipt_local_hour(
        city_name=city_name,
        receipt=receipt,
        fallback_occurred_at=row[occurred_at_key],
    )
    if not city_name or target is None or local_hour is None:
        return {
            "local_hour": local_hour,
            "post_peak_confidence": None,
            "error": "missing_city_target_or_local_hour",
        }
    try:
        from src.calibration.manager import season_from_month
        from src.config import cities_by_name
        from src.signal.diurnal import (
            _apply_solar_bounds,
            _interpolated_seasonal_confidence,
            _lookup_interpolated_monthly_confidence,
            _solar_only_post_peak_confidence,
        )

        city = cities_by_name.get(city_name)
        if city is None:
            return {
                "local_hour": local_hour,
                "post_peak_confidence": None,
                "error": "unknown_city",
            }
        season = season_from_month(target.month, lat=float(city.lat))
        solar_day = _solar_day_from_readonly(
            world_conn,
            city_name=city_name,
            target_date=target,
        )
        monthly_confidence = _lookup_interpolated_monthly_confidence(
            world_conn,
            city_name=city_name,
            month=target.month,
            current_local_hour=local_hour,
        )
        if monthly_confidence is not None:
            return {
                "local_hour": local_hour,
                "post_peak_confidence": _apply_solar_bounds(
                    monthly_confidence,
                    local_hour,
                    solar_day,
                ),
                "error": None,
            }
        season_rows = world_conn.execute(
            "SELECT hour, avg_temp, std_temp, p_high_set FROM diurnal_curves "
            "WHERE city = ? AND season = ? ORDER BY hour",
            (city_name, season),
        ).fetchall()
        if not season_rows or len(season_rows) < 12:
            solar_conf = _solar_only_post_peak_confidence(local_hour, solar_day)
            return {
                "local_hour": local_hour,
                "post_peak_confidence": solar_conf if solar_conf is not None else 0.0,
                "error": None,
            }
        seasonal_confidence = _interpolated_seasonal_confidence(
            season_rows,
            local_hour,
        )
        if seasonal_confidence is not None:
            return {
                "local_hour": local_hour,
                "post_peak_confidence": _apply_solar_bounds(
                    seasonal_confidence,
                    local_hour,
                    solar_day,
                ),
                "error": None,
            }
    except Exception as exc:
        return {
            "local_hour": local_hour,
            "post_peak_confidence": None,
            "error": str(exc),
        }
    return {
        "local_hour": local_hour,
        "post_peak_confidence": None,
        "error": "no_current_source_confidence",
    }


def _monitor_jump_sample(
    row: sqlite3.Row,
    *,
    world_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    keys = (
        "position_id",
        "strategy_key",
        "direction",
        "city",
        "target_date",
        "temperature_metric",
        "bin_label",
        "previous_occurred_at",
        "occurred_at",
        "previous_prob",
        "prob",
        "delta_prob",
        "previous_market_price",
        "market_price",
        "delta_market_price",
        "exit_decision_reason",
    )
    sample = {key: row[key] for key in keys if key in row.keys()}
    previous_receipt = _json_object(row["previous_day0_receipt"] if "previous_day0_receipt" in row.keys() else None)
    receipt = _json_object(row["day0_receipt"] if "day0_receipt" in row.keys() else None)
    previous_observation_time = _nested(previous_receipt, "observation", "observation_time")
    observation_time = _nested(receipt, "observation", "observation_time")
    previous_forecast_cycle = _forecast_cycle(previous_receipt)
    forecast_cycle = _forecast_cycle(receipt)
    previous_remaining_source = _remaining_window_source(previous_receipt)
    remaining_source = _remaining_window_source(receipt)
    previous_forecast_role = _forecast_validation_value(previous_receipt, "forecast_source_role")
    forecast_role = _forecast_validation_value(receipt, "forecast_source_role")
    previous_post_peak = _float(_nested(previous_receipt, "temporal_context", "post_peak_confidence"))
    post_peak = _float(_nested(receipt, "temporal_context", "post_peak_confidence"))
    previous_current_source = _current_source_post_peak_evidence(
        row,
        previous_receipt,
        world_conn=world_conn,
        occurred_at_key="previous_occurred_at",
    )
    current_source = _current_source_post_peak_evidence(
        row,
        receipt,
        world_conn=world_conn,
        occurred_at_key="occurred_at",
    )
    previous_current_source_ppc = _float(previous_current_source.get("post_peak_confidence"))
    current_source_ppc = _float(current_source.get("post_peak_confidence"))
    previous_current_source_delta = (
        None
        if previous_post_peak is None or previous_current_source_ppc is None
        else previous_post_peak - previous_current_source_ppc
    )
    current_source_delta = (
        None
        if post_peak is None or current_source_ppc is None
        else post_peak - current_source_ppc
    )
    sample.update(
        {
            "previous_selected_method": row["previous_selected_method"] if "previous_selected_method" in row.keys() else None,
            "selected_method": row["selected_method"] if "selected_method" in row.keys() else None,
            "previous_prob_is_fresh": _json_bool(row["previous_prob_is_fresh"] if "previous_prob_is_fresh" in row.keys() else None),
            "prob_is_fresh": _json_bool(row["prob_is_fresh"] if "prob_is_fresh" in row.keys() else None),
            "previous_market_price_is_fresh": _json_bool(row["previous_market_price_is_fresh"] if "previous_market_price_is_fresh" in row.keys() else None),
            "market_price_is_fresh": _json_bool(row["market_price_is_fresh"] if "market_price_is_fresh" in row.keys() else None),
            "previous_observation_time": previous_observation_time,
            "observation_time": observation_time,
            "same_observation_time": (
                previous_observation_time is not None
                and observation_time is not None
                and str(previous_observation_time) == str(observation_time)
            ),
            "previous_forecast_cycle": previous_forecast_cycle,
            "forecast_cycle": forecast_cycle,
            "previous_remaining_window_source": previous_remaining_source,
            "remaining_window_source": remaining_source,
            "previous_forecast_source_role": previous_forecast_role,
            "forecast_source_role": forecast_role,
            "same_forecast_cycle": (
                previous_forecast_cycle is not None
                and forecast_cycle is not None
                and str(previous_forecast_cycle) == str(forecast_cycle)
            ),
            "previous_post_peak_confidence": previous_post_peak,
            "post_peak_confidence": post_peak,
            "delta_post_peak_confidence": (
                None
                if previous_post_peak is None or post_peak is None
                else post_peak - previous_post_peak
            ),
            "previous_current_source_local_hour": previous_current_source.get("local_hour"),
            "current_source_local_hour": current_source.get("local_hour"),
            "previous_current_source_post_peak_confidence": previous_current_source_ppc,
            "current_source_post_peak_confidence": current_source_ppc,
            "previous_receipt_current_source_post_peak_delta": previous_current_source_delta,
            "receipt_current_source_post_peak_delta": current_source_delta,
            "previous_current_source_post_peak_error": previous_current_source.get("error"),
            "current_source_post_peak_error": current_source.get("error"),
        }
    )
    same_physical_inputs = bool(
        sample["same_observation_time"]
        and sample["same_forecast_cycle"]
        and sample["previous_selected_method"] == sample["selected_method"]
    )
    freshness_values = (
        sample.get("previous_prob_is_fresh"),
        sample.get("prob_is_fresh"),
        sample.get("previous_market_price_is_fresh"),
        sample.get("market_price_is_fresh"),
    )
    if any(value is False for value in freshness_values):
        sample["jump_driver"] = "stale_monitor_evidence"
    elif (
        _unconditioned_daily_extrema_receipt(
            previous_receipt,
            sample["previous_selected_method"],
        )
        or _unconditioned_daily_extrema_receipt(receipt, sample["selected_method"])
    ):
        sample["jump_driver"] = "unconditioned_daily_extrema_used_as_remaining_window"
    elif any(
        abs(delta) >= CURRENT_SOURCE_PPC_MISMATCH_EPS
        for delta in (
            sample["previous_receipt_current_source_post_peak_delta"],
            sample["receipt_current_source_post_peak_delta"],
        )
        if delta is not None
    ):
        sample["jump_driver"] = "current_source_semantic_mismatch"
    elif same_physical_inputs and sample["delta_post_peak_confidence"] not in (None, 0.0):
        sample["jump_driver"] = "same_source_temporal_context_change"
    elif same_physical_inputs:
        sample["jump_driver"] = "same_source_unexplained_after_deterministic_mc"
    elif sample["previous_selected_method"] != sample["selected_method"]:
        sample["jump_driver"] = "selected_method_change"
    else:
        sample["jump_driver"] = "source_or_observation_change"
    return sample


def _jump_driver_counts(
    rows: list[sqlite3.Row],
    *,
    world_conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[str(_monitor_jump_sample(row, world_conn=world_conn).get("jump_driver") or "unknown")] += 1
    return dict(counts)


def _latest_unconditioned_daily_extrema_hold_rows(
    conn: sqlite3.Connection,
    *,
    sample_limit: int = 20,
) -> list[sqlite3.Row]:
    """Latest open monitor receipts that still hold on unconditioned daily extrema."""

    has_position_events = conn.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name = 'position_events'
        """
    ).fetchone() is not None
    if not has_position_events:
        return []
    return conn.execute(
        """
        WITH open_positions AS (
            SELECT position_id, phase, strategy_key, direction, city, target_date,
                   temperature_metric, bin_label, shares, chain_shares,
                   cost_basis_usd, entry_price, p_posterior, chain_state,
                   order_status, exit_reason
              FROM position_current
             WHERE phase NOT IN ('settled', 'voided', 'economically_closed', 'admin_closed')
        ),
        latest_monitor AS (
            SELECT p.*, e.occurred_at, e.sequence_no, e.payload_json
              FROM open_positions p
              JOIN position_events e
                ON e.rowid = (
                    SELECT inner_e.rowid
                      FROM position_events inner_e
                     WHERE inner_e.position_id = p.position_id
                       AND inner_e.event_type = 'MONITOR_REFRESHED'
                     ORDER BY inner_e.sequence_no DESC, datetime(inner_e.occurred_at) DESC
                     LIMIT 1
                )
        )
        SELECT
            position_id, phase, strategy_key, direction, city, target_date,
            temperature_metric, bin_label, shares, chain_shares,
            cost_basis_usd, entry_price, p_posterior, chain_state,
            order_status, exit_reason,
            occurred_at AS latest_monitor_at,
            CAST(json_extract(payload_json, '$.last_monitor_prob') AS REAL) AS last_monitor_prob,
            CAST(json_extract(payload_json, '$.last_monitor_market_price') AS REAL) AS last_monitor_market_price,
            json_extract(payload_json, '$.last_monitor_prob_is_fresh') AS last_monitor_prob_is_fresh,
            json_extract(payload_json, '$.last_monitor_market_price_is_fresh') AS last_monitor_market_price_is_fresh,
            json_extract(payload_json, '$.exit_decision_should_exit') AS exit_decision_should_exit,
            json_extract(payload_json, '$.exit_decision_reason') AS exit_decision_reason,
            json_extract(payload_json, '$.exit_decision_trigger') AS exit_decision_trigger,
            json_extract(payload_json, '$.day0_monitor_probability_receipt.selected_method') AS selected_method,
            json_extract(payload_json, '$.day0_monitor_probability_receipt.remaining_window.source') AS remaining_window_source,
            json_extract(payload_json, '$.day0_monitor_probability_receipt.remaining_window.forecast_source_validations') AS forecast_source_validations
          FROM latest_monitor
         WHERE json_extract(payload_json, '$.day0_monitor_probability_receipt.selected_method')
               = 'day0_observation_remaining_window'
           AND json_extract(payload_json, '$.day0_monitor_probability_receipt.remaining_window.source')
               = 'day0_raw_model_extrema'
           AND COALESCE(json_extract(payload_json, '$.exit_decision_should_exit'), 0) = 0
           AND EXISTS (
               SELECT 1
                 FROM json_each(
                     json_extract(
                         payload_json,
                         '$.day0_monitor_probability_receipt.remaining_window.forecast_source_validations'
                     )
                 )
                WHERE json_each.value = 'forecast_source_role:day0_daily_extrema_live'
           )
         ORDER BY datetime(occurred_at) DESC, position_id
         LIMIT ?
        """,
        (int(max(0, sample_limit)),),
    ).fetchall()


def _unconditioned_daily_extrema_hold_sample(row: sqlite3.Row) -> dict[str, Any]:
    keys = (
        "position_id",
        "strategy_key",
        "direction",
        "city",
        "target_date",
        "temperature_metric",
        "bin_label",
        "phase",
        "order_status",
        "exit_reason",
        "shares",
        "chain_shares",
        "p_posterior",
        "last_monitor_prob",
        "last_monitor_market_price",
        "last_monitor_prob_is_fresh",
        "last_monitor_market_price_is_fresh",
        "latest_monitor_at",
        "exit_decision_should_exit",
        "exit_decision_reason",
        "exit_decision_trigger",
        "selected_method",
        "remaining_window_source",
        "forecast_source_validations",
    )
    return {key: row[key] for key in keys if key in row.keys()}


def _open_runtime_gate_exit_block_rows(
    conn: sqlite3.Connection,
    *,
    sample_limit: int = 20,
) -> list[sqlite3.Row]:
    """Latest open-position exit rejects caused by runtime submit gating."""

    has_position_events = conn.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name = 'position_events'
        """
    ).fetchone() is not None
    if not has_position_events:
        return []
    return conn.execute(
        """
        WITH latest_gate_reject AS (
            SELECT
                p.position_id,
                (
                    SELECT inner_e.rowid
                     FROM position_events inner_e
                     WHERE inner_e.position_id = p.position_id
                       AND inner_e.event_type = 'EXIT_ORDER_REJECTED'
                       AND (
                           json_extract(inner_e.payload_json, '$.runtime_submit_gate_block') IN (1, 'true')
                           OR json_extract(inner_e.payload_json, '$.status') = 'runtime_submit_gate_blocked'
                           OR (
                               json_extract(inner_e.payload_json, '$.error') LIKE '%[gate_runtime] BLOCKED%'
                               AND (
                                   json_extract(inner_e.payload_json, '$.error') LIKE '%live_venue_submit%'
                                   OR json_extract(inner_e.payload_json, '$.error') LIKE '%reduce_only_exit_submit%'
                               )
                           )
                       )
                     ORDER BY inner_e.sequence_no DESC, datetime(inner_e.occurred_at) DESC
                     LIMIT 1
                ) AS latest_reject_rowid
              FROM position_current p
             WHERE p.phase NOT IN ('settled', 'voided', 'economically_closed', 'admin_closed')
        ),
        gate_counts AS (
            SELECT
                p.position_id,
                COUNT(*) AS runtime_gate_reject_count,
                MIN(e.occurred_at) AS first_runtime_gate_reject_at,
                MAX(e.occurred_at) AS latest_runtime_gate_reject_at
              FROM position_current p
             JOIN position_events e ON e.position_id = p.position_id
             WHERE p.phase NOT IN ('settled', 'voided', 'economically_closed', 'admin_closed')
               AND e.event_type = 'EXIT_ORDER_REJECTED'
               AND (
                   json_extract(e.payload_json, '$.runtime_submit_gate_block') IN (1, 'true')
                   OR json_extract(e.payload_json, '$.status') = 'runtime_submit_gate_blocked'
                   OR (
                       json_extract(e.payload_json, '$.error') LIKE '%[gate_runtime] BLOCKED%'
                       AND (
                           json_extract(e.payload_json, '$.error') LIKE '%live_venue_submit%'
                           OR json_extract(e.payload_json, '$.error') LIKE '%reduce_only_exit_submit%'
                       )
                   )
               )
             GROUP BY p.position_id
        )
        SELECT
            p.position_id, p.phase, p.strategy_key, p.direction, p.city,
            p.target_date, p.temperature_metric, p.bin_label, p.shares,
            p.chain_shares, p.cost_basis_usd, p.entry_price, p.p_posterior,
            p.last_monitor_prob, p.last_monitor_market_price,
            p.last_monitor_prob_is_fresh, p.last_monitor_market_price_is_fresh,
            p.updated_at, p.exit_reason, p.chain_state, p.order_status,
            c.runtime_gate_reject_count,
            c.first_runtime_gate_reject_at,
            c.latest_runtime_gate_reject_at,
            e.sequence_no AS latest_runtime_gate_reject_sequence_no,
            json_extract(e.payload_json, '$.status') AS latest_runtime_gate_reject_status,
            json_extract(e.payload_json, '$.exit_reason') AS latest_runtime_gate_exit_reason,
            json_extract(e.payload_json, '$.error') AS latest_runtime_gate_error
          FROM latest_gate_reject latest
          JOIN gate_counts c ON c.position_id = latest.position_id
          JOIN position_current p ON p.position_id = latest.position_id
          JOIN position_events e ON e.rowid = latest.latest_reject_rowid
         WHERE latest.latest_reject_rowid IS NOT NULL
         ORDER BY datetime(e.occurred_at) DESC, e.sequence_no DESC
         LIMIT ?
        """,
        (int(max(0, sample_limit)),),
    ).fetchall()


def _runtime_gate_exit_block_sample(row: sqlite3.Row) -> dict[str, Any]:
    keys = (
        "position_id",
        "strategy_key",
        "direction",
        "city",
        "target_date",
        "temperature_metric",
        "bin_label",
        "phase",
        "order_status",
        "exit_reason",
        "shares",
        "chain_shares",
        "last_monitor_prob",
        "last_monitor_market_price",
        "runtime_gate_reject_count",
        "first_runtime_gate_reject_at",
        "latest_runtime_gate_reject_at",
        "latest_runtime_gate_reject_status",
        "latest_runtime_gate_exit_reason",
        "latest_runtime_gate_error",
    )
    return {key: row[key] for key in keys if key in row.keys()}



def _open_dust_exit_rows(conn: sqlite3.Connection, *, sample_limit: int = 20) -> list[sqlite3.Row]:
    has_position_events = conn.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name = 'position_events'
        """
    ).fetchone() is not None
    if not has_position_events:
        return []
    return conn.execute(
        """
        WITH latest_dust AS (
            SELECT
                p.position_id,
                MAX(e.occurred_at) AS last_dust_reject_at
              FROM position_current p
              JOIN position_events e ON e.position_id = p.position_id
             WHERE p.phase NOT IN ('settled', 'voided', 'economically_closed', 'admin_closed')
               AND e.event_type = 'EXIT_ORDER_REJECTED'
               AND json_extract(e.payload_json, '$.error') LIKE '%min_order_size%'
             GROUP BY p.position_id
        )
        SELECT
            p.position_id, p.phase, p.strategy_key, p.direction, p.city,
            p.target_date, p.temperature_metric, p.bin_label, p.shares,
            p.chain_shares, p.cost_basis_usd, p.entry_price, p.p_posterior,
            p.last_monitor_prob, p.last_monitor_market_price,
            p.last_monitor_prob_is_fresh, p.last_monitor_market_price_is_fresh,
            p.updated_at, p.exit_reason, p.chain_state, p.order_status,
            e.occurred_at AS dust_reject_at,
            json_extract(e.payload_json, '$.status') AS dust_reject_status,
            json_extract(e.payload_json, '$.exit_reason') AS dust_reject_reason,
            json_extract(e.payload_json, '$.error') AS dust_reject_error
          FROM latest_dust d
          JOIN position_current p ON p.position_id = d.position_id
          JOIN position_events e
            ON e.position_id = d.position_id
           AND e.occurred_at = d.last_dust_reject_at
           AND e.event_type = 'EXIT_ORDER_REJECTED'
         ORDER BY datetime(e.occurred_at) DESC, e.sequence_no DESC
         LIMIT ?
        """,
        (int(max(0, sample_limit)),),
    ).fetchall()


def _dust_exit_sample(row: sqlite3.Row) -> dict[str, Any]:
    keys = (
        "position_id",
        "strategy_key",
        "direction",
        "city",
        "target_date",
        "temperature_metric",
        "bin_label",
        "phase",
        "order_status",
        "exit_reason",
        "shares",
        "chain_shares",
        "last_monitor_prob",
        "last_monitor_market_price",
        "dust_reject_at",
        "dust_reject_status",
        "dust_reject_reason",
        "dust_reject_error",
    )
    return {key: row[key] for key in keys if key in row.keys()}


def _settlement_attribution_summary(world_conn: sqlite3.Connection) -> dict[str, Any]:
    rows = world_conn.execute(
        """
        SELECT direction, category, won, q_live, q_lcb_5pct, fresh_q_supports_position
          FROM settlement_attribution
        """
    ).fetchall()
    by_category: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "with_q_live": 0, "fresh_against": 0}
    )
    for row in rows:
        key = f"{row['direction'] or 'missing'}:{row['category'] or 'missing'}"
        bucket = by_category[key]
        bucket["n"] += 1
        bucket["wins"] += 1 if row["won"] else 0
        bucket["with_q_live"] += 1 if row["q_live"] is not None else 0
        bucket["fresh_against"] += 1 if row["fresh_q_supports_position"] == 0 else 0
    return {
        "rows": len(rows),
        "by_direction_category": dict(sorted(by_category.items())),
    }


def audit_live_probability_reality(
    *,
    trade_db: Path = DEFAULT_TRADE_DB,
    world_db: Path = DEFAULT_WORLD_DB,
    days: float | None = 14.0,
    sample_limit: int = 20,
) -> dict[str, Any]:
    trade_conn = _open_readonly(trade_db)
    world_conn = _open_readonly(world_db)
    try:
        settled_rows = _settled_position_rows(trade_conn, days=days)
        open_rows = _open_position_rows(trade_conn)
        jump_rows = _monitor_probability_jump_rows(
            trade_conn,
            min_abs_delta=0.10,
            sample_limit=sample_limit,
        )
        dust_exit_rows = _open_dust_exit_rows(
            trade_conn,
            sample_limit=sample_limit,
        )
        unconditioned_daily_extrema_hold_rows = (
            _latest_unconditioned_daily_extrema_hold_rows(
                trade_conn,
                sample_limit=sample_limit,
            )
        )
        runtime_gate_exit_block_rows = _open_runtime_gate_exit_block_rows(
            trade_conn,
            sample_limit=sample_limit,
        )
        attribution = _settlement_attribution_summary(world_conn)
        jump_driver_counts = _jump_driver_counts(jump_rows, world_conn=world_conn)
        monitor_probability_jump_samples = [
            _monitor_jump_sample(row, world_conn=world_conn) for row in jump_rows
        ]
    finally:
        trade_conn.close()
        world_conn.close()

    by_strategy_direction: dict[str, list[sqlite3.Row]] = defaultdict(list)
    by_prob_bin: dict[str, list[sqlite3.Row]] = defaultdict(list)
    outcome_missing = 0
    for row in settled_rows:
        by_strategy_direction[f"{row['strategy_key'] or 'missing'}:{row['direction'] or 'missing'}"].append(row)
        by_prob_bin[_prob_bin(_float(row["p_posterior"]))].append(row)
        if row["outcome"] is None:
            outcome_missing += 1

    open_phase_counts = Counter(str(row["phase"] or "missing") for row in open_rows)
    open_exit_intents = [
        row
        for row in open_rows
        if row["phase"] == "pending_exit" or row["exit_reason"]
    ]
    open_stale_monitor = [
        row
        for row in open_rows
        if row["last_monitor_prob_is_fresh"] != 1
        or row["last_monitor_market_price_is_fresh"] != 1
    ]
    dust_projection_lost = [
        row
        for row in dust_exit_rows
        if not (
            str(row["phase"] or "") == "pending_exit"
            and str(row["order_status"] or "") == "backoff_exhausted"
        )
    ]

    high_confidence_misses = [
        row
        for row in settled_rows
        if row["outcome"] == 0 and (_float(row["p_posterior"]) or 0.0) >= 0.8
    ]
    zero_monitor_settled = [
        row
        for row in settled_rows
        if row["outcome"] is not None
        and row["actual_monitor_events"] is not None
        and int(row["actual_monitor_events"] or 0) == 0
    ]
    monitor_projection_gaps = [
        row
        for row in settled_rows
        if row["outcome"] is not None
        and int(row["monitor_count"] or 0) == 0
        and row["actual_monitor_events"] is not None
        and int(row["actual_monitor_events"] or 0) > 0
    ]

    return {
        "trade_db": str(trade_db),
        "world_db": str(world_db),
        "days": days,
        "settled_summary": _summarize_rows(settled_rows),
        "settled_outcome_missing": outcome_missing,
        "by_strategy_direction": {
            key: _summarize_rows(rows)
            for key, rows in sorted(by_strategy_direction.items())
        },
        "by_declared_probability_bin": {
            key: _summarize_rows(rows)
            for key, rows in sorted(by_prob_bin.items())
        },
        "open_summary": {
            "positions": len(open_rows),
            "phase_counts": dict(sorted(open_phase_counts.items())),
            "exit_intent_or_reason_count": len(open_exit_intents),
            "stale_monitor_count": len(open_stale_monitor),
            "monitor_probability_jump_count": len(jump_rows),
            "monitor_probability_jump_driver_counts": jump_driver_counts,
            "dust_exit_blocked_count": len(dust_exit_rows),
            "dust_exit_projection_lost_count": len(dust_projection_lost),
            "unconditioned_daily_extrema_hold_count": len(
                unconditioned_daily_extrema_hold_rows
            ),
            "runtime_gate_exit_block_count": len(runtime_gate_exit_block_rows),
            "exit_intent_or_reason_samples": [
                _row_sample(row) for row in open_exit_intents[:sample_limit]
            ],
            "stale_monitor_samples": [
                _row_sample(row) for row in open_stale_monitor[:sample_limit]
            ],
            "monitor_probability_jump_samples": monitor_probability_jump_samples,
            "dust_exit_blocked_samples": [
                _dust_exit_sample(row) for row in dust_exit_rows
            ],
            "dust_exit_projection_lost_samples": [
                _dust_exit_sample(row) for row in dust_projection_lost
            ],
            "unconditioned_daily_extrema_hold_samples": [
                _unconditioned_daily_extrema_hold_sample(row)
                for row in unconditioned_daily_extrema_hold_rows
            ],
            "runtime_gate_exit_block_samples": [
                _runtime_gate_exit_block_sample(row)
                for row in runtime_gate_exit_block_rows
            ],
        },
        "settlement_attribution": attribution,
        "high_confidence_miss_samples": [
            _row_sample(row) for row in high_confidence_misses[:sample_limit]
        ],
        "zero_monitor_settled_samples": [
            _row_sample(row) for row in zero_monitor_settled[:sample_limit]
        ],
        "monitor_projection_gap_samples": [
            _row_sample(row) for row in monitor_projection_gaps[:sample_limit]
        ],
        "verdict": _verdict(
            settled_rows=settled_rows,
            high_confidence_misses=high_confidence_misses,
            zero_monitor_settled=zero_monitor_settled,
            monitor_projection_gaps=monitor_projection_gaps,
            open_exit_intents=open_exit_intents,
            unconditioned_daily_extrema_hold_rows=unconditioned_daily_extrema_hold_rows,
            runtime_gate_exit_block_rows=runtime_gate_exit_block_rows,
        ),
    }


def _verdict(
    *,
    settled_rows: list[sqlite3.Row],
    high_confidence_misses: list[sqlite3.Row],
    zero_monitor_settled: list[sqlite3.Row],
    monitor_projection_gaps: list[sqlite3.Row],
    open_exit_intents: list[sqlite3.Row],
    unconditioned_daily_extrema_hold_rows: list[sqlite3.Row],
    runtime_gate_exit_block_rows: list[sqlite3.Row],
) -> str:
    if unconditioned_daily_extrema_hold_rows and runtime_gate_exit_block_rows:
        return "OPEN_DAY0_UNCONDITIONED_HOLD_AND_RUNTIME_GATE_EXIT_BLOCK_EVIDENCE"
    if unconditioned_daily_extrema_hold_rows:
        return "OPEN_DAY0_UNCONDITIONED_DAILY_EXTREMA_HOLD_EVIDENCE"
    if runtime_gate_exit_block_rows:
        return "OPEN_RUNTIME_GATE_EXIT_BLOCK_EVIDENCE"
    if not settled_rows:
        return "NO_SETTLED_POSITION_EVIDENCE"
    if high_confidence_misses and zero_monitor_settled:
        return "PROBABILITY_REALITY_AND_ACTUAL_MONITOR_ABSENCE_EVIDENCE"
    if high_confidence_misses and monitor_projection_gaps:
        return "PROBABILITY_REALITY_AND_MONITOR_PROJECTION_GAP_EVIDENCE"
    if high_confidence_misses:
        return "PROBABILITY_REALITY_MISS_EVIDENCE"
    if zero_monitor_settled or open_exit_intents:
        return "MONITOR_LIFECYCLE_FAILURE_EVIDENCE"
    if monitor_projection_gaps:
        return "MONITOR_PROJECTION_GAP_EVIDENCE"
    return "NO_OBVIOUS_SETTLED_PROBABILITY_MONITOR_FAILURE"


def _print_markdown(report: dict[str, Any]) -> None:
    print("# Live Probability Reality Audit")
    print()
    print(f"- verdict: `{report['verdict']}`")
    print(f"- trade_db: `{report['trade_db']}`")
    print(f"- world_db: `{report['world_db']}`")
    print(f"- days: `{report['days']}`")
    print(f"- settled_summary: `{json.dumps(report['settled_summary'], sort_keys=True)}`")
    print(f"- open_summary: `{json.dumps(report['open_summary'], sort_keys=True)}`")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-db", type=Path, default=DEFAULT_TRADE_DB)
    parser.add_argument("--world-db", type=Path, default=DEFAULT_WORLD_DB)
    parser.add_argument("--days", type=float, default=14.0)
    parser.add_argument("--all-history", action="store_true", help="Ignore --days")
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = audit_live_probability_reality(
        trade_db=args.trade_db,
        world_db=args.world_db,
        days=None if args.all_history else args.days,
        sample_limit=max(0, args.sample_limit),
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, default=str))
    else:
        _print_markdown(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
