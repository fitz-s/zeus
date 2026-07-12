"""Read-only held-position monitor cadence evidence.

This module is intentionally pure SELECT/in-memory classification.  It proves
whether live-money positions have fresh per-position ``MONITOR_REFRESHED``
events; it does not use projection timestamps and never writes runtime state.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.contracts.position_truth import (
    CURRENT_MONEY_RISK_CHAIN_STATES,
)


MONITOR_CADENCE_EXPOSURE_EPS = 0.01
MONITOR_CADENCE_FUTURE_TOLERANCE_SECONDS = 30.0
MONITOR_CADENCE_POSITION_PHASES = frozenset({"active", "day0_window", "pending_exit"})
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' retired
# from this set — the T5 schema migration has run and the DB CHECK no longer
# admits the literal, so a live row can never carry it. A disputed-entry
# position now keeps its TRUE phase (in MONITOR_CADENCE_POSITION_PHASES
# above) per REPLACEMENT PHASE LAW, so it is normally monitored rather than
# routing through this non-monitor bucket; 'voided' remains a genuine
# not-actively-monitored-but-still-has-residual-chain-risk case.
NON_MONITOR_CHAIN_RISK_PHASES = frozenset({"voided"})
EXIT_REDECISION_EVENT_TYPES = frozenset({"EXIT_ORDER_REJECTED", "EXIT_RETRY_RELEASED"})
EXIT_REDECISION_PHASES = frozenset({"day0_window", "pending_exit"})
CLOSED_MARKET_PENDING_SETTLEMENT_VALIDATIONS = frozenset(
    {
        "day0_hard_fact_bin_dead_closed_market",
        "market_closed_non_accepting_orders",
    }
)


def collect_monitor_cadence_evidence(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    max_age_seconds: float | None = None,
    min_occurred_at: datetime | None = None,
    sample_limit: int = 25,
) -> dict[str, Any]:
    """Return per-position monitor cadence evidence for current money risk.

    ``max_age_seconds`` is the normal health/preflight freshness window.
    ``min_occurred_at`` is the post-start restart proof floor.  When both are
    supplied, a position must satisfy both.  Future-dated monitor events are
    reported separately because they are clock/data faults, not stale cadence.
    """

    position_columns = _table_columns(conn, "position_current")
    event_columns = _table_columns(conn, "position_events")
    now_utc = _ensure_utc(now)
    monitored_rows = _monitor_cadence_position_rows(conn, position_columns, now_utc=now_utc)
    non_monitor_chain_risk_rows = _non_monitor_chain_risk_position_rows(
        conn,
        position_columns,
        now_utc=now_utc,
    )
    min_occurred_utc = _ensure_utc(min_occurred_at) if min_occurred_at else None
    stale_or_missing: list[dict[str, Any]] = []
    future_events: list[dict[str, Any]] = []
    settlement_recoverable: list[dict[str, Any]] = []
    fresh_count = 0
    for position in monitored_rows:
        monitor_event = _latest_monitor_refreshed_event(
            conn,
            str(position["position_id"]),
            event_columns,
        )
        occurred_at = None if monitor_event is None else str(monitor_event.get("occurred_at") or "")
        position_evidence = {
            "position_id": position["position_id"],
            "phase": position["phase"],
            "chain_state": position["chain_state"],
        }
        exit_event = _latest_exit_redecision_event(
            conn,
            str(position["position_id"]),
            event_columns,
        )
        if not occurred_at:
            if _exit_redecision_event_is_fresh(
                position,
                exit_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
                continue
            stale_or_missing.append(
                {**position_evidence, "last_monitor_refreshed_at": None}
            )
            continue
        position_evidence["last_monitor_refreshed_at"] = occurred_at
        occurred_dt = _parse_iso_utc(occurred_at)
        if occurred_dt is None:
            stale_or_missing.append(
                {**position_evidence, "issue": "timestamp_unparseable"}
            )
            continue
        age_seconds = (now_utc - occurred_dt).total_seconds()
        position_evidence["age_seconds"] = round(age_seconds, 1)
        if age_seconds < -MONITOR_CADENCE_FUTURE_TOLERANCE_SECONDS:
            future_events.append(position_evidence)
        elif age_seconds < 0.0:
            fresh_count += 1
        elif min_occurred_utc is not None and occurred_dt < min_occurred_utc:
            if _exit_redecision_event_is_fresh(
                position,
                exit_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
            else:
                if _monitor_event_closed_market_pending_settlement(
                    position_evidence,
                    monitor_event,
                ):
                    settlement_recoverable.append(position_evidence.copy())
                else:
                    stale_or_missing.append(position_evidence)
        elif max_age_seconds is not None and age_seconds > float(max_age_seconds):
            if _exit_redecision_event_is_fresh(
                position,
                exit_event,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                min_occurred_utc=min_occurred_utc,
                position_evidence=position_evidence,
                future_events=future_events,
            ):
                fresh_count += 1
            else:
                if _monitor_event_closed_market_pending_settlement(
                    position_evidence,
                    monitor_event,
                ):
                    settlement_recoverable.append(position_evidence.copy())
                else:
                    stale_or_missing.append(position_evidence)
        else:
            fresh_count += 1
    open_count = len(monitored_rows)
    return {
        "open_position_count": open_count,
        "monitored_position_count": open_count,
        "fresh_position_count": fresh_count,
        "stale_or_missing_position_count": len(stale_or_missing),
        "stale_or_missing_positions": stale_or_missing[:sample_limit],
        "settlement_recoverable_position_count": len(settlement_recoverable),
        "settlement_recoverable_positions": settlement_recoverable[:sample_limit],
        "future_monitor_event_count": len(future_events),
        "future_monitor_events": future_events[:sample_limit],
        "non_monitor_chain_risk_position_count": len(non_monitor_chain_risk_rows),
        "non_monitor_chain_risk_positions": non_monitor_chain_risk_rows[:sample_limit],
        "non_monitor_chain_risk_role": "chain_reconciliation_not_monitor_cadence",
    }


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _monitor_cadence_position_rows(
    conn: sqlite3.Connection,
    position_columns: set[str],
    *,
    now_utc: datetime,
) -> list[dict[str, object]]:
    if "position_id" not in position_columns:
        return []
    optional_selects = []
    for column in (
        "phase",
        "shares",
        "chain_shares",
        "chain_state",
        "order_status",
        "exit_reason",
        "target_date",
    ):
        optional_selects.append(column if column in position_columns else f"NULL AS {column}")
    rows = conn.execute(
        f"""
        SELECT position_id, {", ".join(optional_selects)}
          FROM position_current
        """
    ).fetchall()
    monitored: list[dict[str, object]] = []
    for row in rows:
        position_id = str(row["position_id"] or "")
        phase = str(row["phase"] or "").strip().lower()
        chain_state = str(row["chain_state"] or "").strip()
        shares = _float_or_zero(row["shares"])
        chain_shares = _float_or_zero(row["chain_shares"])
        exposure_positive = (
            shares > MONITOR_CADENCE_EXPOSURE_EPS
            or chain_shares > MONITOR_CADENCE_EXPOSURE_EPS
        )
        if _position_requires_monitor_cadence(
            phase=phase,
            chain_state=chain_state,
            exposure_positive=exposure_positive,
            target_date=row["target_date"],
            now_utc=now_utc,
        ):
            monitored.append(
                {
                    "position_id": position_id,
                    "phase": phase,
                    "chain_state": chain_state,
                    "order_status": str(row["order_status"] or "").strip().lower(),
                    "exit_reason": str(row["exit_reason"] or "").strip(),
                }
            )
    return monitored


def _position_requires_monitor_cadence(
    *,
    phase: str,
    chain_state: str,
    exposure_positive: bool,
    target_date: object = None,
    now_utc: datetime | None = None,
) -> bool:
    if not exposure_positive:
        return False
    if not phase:
        return True
    if phase in MONITOR_CADENCE_POSITION_PHASES:
        return True
    return False


def _non_monitor_chain_risk_position_rows(
    conn: sqlite3.Connection,
    position_columns: set[str],
    *,
    now_utc: datetime,
) -> list[dict[str, object]]:
    if "position_id" not in position_columns:
        return []
    optional_selects = []
    for column in ("phase", "shares", "chain_shares", "chain_state", "target_date"):
        optional_selects.append(column if column in position_columns else f"NULL AS {column}")
    rows = conn.execute(
        f"""
        SELECT position_id, {", ".join(optional_selects)}
          FROM position_current
        """
    ).fetchall()
    chain_risk_rows: list[dict[str, object]] = []
    for row in rows:
        phase = str(row["phase"] or "").strip().lower()
        chain_state = str(row["chain_state"] or "").strip()
        chain_shares = _float_or_zero(row["chain_shares"])
        if phase not in NON_MONITOR_CHAIN_RISK_PHASES:
            continue
        if chain_shares <= MONITOR_CADENCE_EXPOSURE_EPS:
            continue
        if chain_state not in CURRENT_MONEY_RISK_CHAIN_STATES:
            continue
        if _position_requires_monitor_cadence(
            phase=phase,
            chain_state=chain_state,
            exposure_positive=True,
            target_date=row["target_date"],
            now_utc=now_utc,
        ):
            continue
        chain_risk_rows.append(
            {
                "position_id": str(row["position_id"] or ""),
                "phase": phase,
                "chain_state": chain_state,
                "shares": _float_or_zero(row["shares"]),
                "chain_shares": chain_shares,
                "target_date": str(row["target_date"] or ""),
            }
        )
    return chain_risk_rows


def _latest_monitor_refreshed_event(
    conn: sqlite3.Connection,
    position_id: str,
    event_columns: set[str],
) -> dict[str, str] | None:
    order_by = "datetime(occurred_at) DESC"
    if "sequence_no" in event_columns:
        order_by += ", sequence_no DESC"
    payload_select = "payload_json" if "payload_json" in event_columns else "NULL AS payload_json"
    row = conn.execute(
        f"""
        SELECT occurred_at, {payload_select}
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'MONITOR_REFRESHED'
         ORDER BY {order_by}
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "occurred_at": str(row["occurred_at"] or ""),
        "payload_json": str(row["payload_json"] or ""),
    }


def _monitor_event_closed_market_pending_settlement(
    position_evidence: dict[str, Any],
    monitor_event: dict[str, str] | None,
) -> bool:
    if monitor_event is None:
        return False
    try:
        payload = json.loads(monitor_event.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    validations_raw = payload.get("applied_validations")
    validations = {str(item) for item in validations_raw} if isinstance(validations_raw, list) else set()
    matched = sorted(validations & CLOSED_MARKET_PENDING_SETTLEMENT_VALIDATIONS)
    if not matched:
        return False
    position_evidence.update(
        {
            "cadence_source": "MONITOR_REFRESHED_CLOSED_MARKET_PENDING_SETTLEMENT",
            "closed_market_validation": matched[0],
            "restart_resolution": "settlement_harvester_or_market_reopen_recovery",
        }
    )
    return True


def _latest_exit_redecision_event(
    conn: sqlite3.Connection,
    position_id: str,
    event_columns: set[str],
) -> tuple[str, str] | None:
    if "event_type" not in event_columns or "occurred_at" not in event_columns:
        return None
    order_by = "datetime(occurred_at) DESC"
    if "sequence_no" in event_columns:
        order_by += ", sequence_no DESC"
    placeholders = ", ".join("?" for _ in EXIT_REDECISION_EVENT_TYPES)
    row = conn.execute(
        f"""
        SELECT event_type, occurred_at
          FROM position_events
         WHERE position_id = ?
           AND event_type IN ({placeholders})
         ORDER BY {order_by}
         LIMIT 1
        """,
        (position_id, *tuple(sorted(EXIT_REDECISION_EVENT_TYPES))),
    ).fetchone()
    if row is None:
        return None
    return str(row["event_type"] or ""), str(row["occurred_at"] or "")


def _exit_redecision_event_is_fresh(
    position: dict[str, object],
    exit_event: tuple[str, str] | None,
    *,
    now_utc: datetime,
    max_age_seconds: float | None,
    min_occurred_utc: datetime | None,
    position_evidence: dict[str, Any],
    future_events: list[dict[str, Any]],
) -> bool:
    phase = str(position.get("phase") or "").strip().lower()
    order_status = str(position.get("order_status") or "").strip().lower()
    exit_reason = str(position.get("exit_reason") or "").strip()
    if phase not in EXIT_REDECISION_PHASES:
        return False
    if not exit_reason and order_status not in {"retry_pending", "exit_intent"}:
        return False
    if exit_event is None:
        return False
    event_type, occurred_at = exit_event
    occurred_dt = _parse_iso_utc(occurred_at)
    enriched = {
        **position_evidence,
        "cadence_source": event_type,
        "latest_exit_redecision_at": occurred_at,
    }
    if occurred_dt is None:
        return False
    age_seconds = (now_utc - occurred_dt).total_seconds()
    enriched["exit_redecision_age_seconds"] = round(age_seconds, 1)
    if age_seconds < 0.0:
        future_events.append(enriched)
        return False
    if min_occurred_utc is not None and occurred_dt < min_occurred_utc:
        return False
    if max_age_seconds is not None and age_seconds > float(max_age_seconds):
        return False
    position_evidence.update(enriched)
    return True


def _parse_iso_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _float_or_zero(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
