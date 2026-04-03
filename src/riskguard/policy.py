from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import sqlite3
from typing import Any

from src.control.control_plane import (
    get_edge_threshold_multiplier,
    is_entries_paused,
)


@dataclass(frozen=True)
class StrategyPolicy:
    strategy_key: str
    gated: bool
    allocation_multiplier: float
    threshold_multiplier: float
    exit_only: bool
    sources: list[str]


def resolve_strategy_policy(
    conn: sqlite3.Connection,
    strategy_key: str,
    now: datetime,
) -> StrategyPolicy:
    if not strategy_key:
        raise ValueError("strategy_key is required")

    current_time = _normalize_datetime(now)
    gated = False
    allocation_multiplier = 1.0
    threshold_multiplier = 1.0
    exit_only = False
    sources: list[str] = []
    locked_fields: set[str] = set()

    if is_entries_paused():
        gated = True
        locked_fields.add("gated")
        sources.append("hard_safety:pause_entries")

    control_threshold_multiplier = max(1.0, float(get_edge_threshold_multiplier()))
    if control_threshold_multiplier > 1.0:
        threshold_multiplier = control_threshold_multiplier
        locked_fields.add("threshold_multiplier")
        sources.append(f"hard_safety:tighten_risk:{control_threshold_multiplier:g}")

    manual_overrides = _select_rows(_load_manual_overrides(conn, strategy_key, current_time))
    risk_actions = _select_rows(_load_risk_actions(conn, strategy_key, current_time))

    for row in manual_overrides:
        action_type = str(row["action_type"])
        if action_type == "gate":
            if "gated" in locked_fields:
                continue
            gated = _parse_boolish(row["value"])
            locked_fields.add("gated")
        elif action_type == "allocation_multiplier":
            if "allocation_multiplier" in locked_fields:
                continue
            allocation_multiplier = _parse_multiplier(row["value"], action_type)
            locked_fields.add("allocation_multiplier")
        elif action_type == "threshold_multiplier":
            if "threshold_multiplier" in locked_fields:
                continue
            threshold_multiplier = _parse_multiplier(row["value"], action_type)
            locked_fields.add("threshold_multiplier")
        elif action_type == "exit_only":
            if "exit_only" in locked_fields:
                continue
            exit_only = _parse_boolish(row["value"])
            locked_fields.add("exit_only")
        else:
            continue
        sources.append(f"manual_override:{action_type}")

    for row in risk_actions:
        action_type = str(row["action_type"])
        if action_type == "gate":
            if "gated" in locked_fields:
                continue
            gated = _parse_boolish(row["value"])
            locked_fields.add("gated")
        elif action_type == "allocation_multiplier":
            if "allocation_multiplier" in locked_fields:
                continue
            allocation_multiplier = _parse_multiplier(row["value"], action_type)
            locked_fields.add("allocation_multiplier")
        elif action_type == "threshold_multiplier":
            if "threshold_multiplier" in locked_fields:
                continue
            threshold_multiplier = _parse_multiplier(row["value"], action_type)
            locked_fields.add("threshold_multiplier")
        elif action_type == "exit_only":
            if "exit_only" in locked_fields:
                continue
            exit_only = _parse_boolish(row["value"])
            locked_fields.add("exit_only")
        else:
            continue
        sources.append(f"risk_action:{action_type}")

    return StrategyPolicy(
        strategy_key=strategy_key,
        gated=gated,
        allocation_multiplier=allocation_multiplier,
        threshold_multiplier=threshold_multiplier,
        exit_only=exit_only,
        sources=sources,
    )


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(raw: str | None) -> datetime | None:
    if raw is None or raw == "":
        return None
    normalized = raw.replace("Z", "+00:00")
    return _normalize_datetime(datetime.fromisoformat(normalized))


def _is_active(now: datetime, issued_at: str, effective_until: str | None) -> bool:
    issued = _parse_timestamp(issued_at)
    if issued is not None and issued > now:
        return False
    expires = _parse_timestamp(effective_until)
    if expires is not None and expires <= now:
        return False
    return True


def _load_manual_overrides(
    conn: sqlite3.Connection,
    strategy_key: str,
    now: datetime,
) -> list[sqlite3.Row]:
    rows = _query_rows(
        conn,
        """
        SELECT override_id, target_type, target_key, action_type, value, issued_at,
               effective_until, precedence
        FROM control_overrides
        WHERE target_type IN ('global', 'strategy')
        ORDER BY precedence DESC, issued_at DESC, override_id DESC
        """,
    )
    applicable: list[sqlite3.Row] = []
    for row in rows:
        target_type = str(row["target_type"])
        target_key = str(row["target_key"])
        if target_type == "strategy" and target_key != strategy_key:
            continue
        if not _is_active(now, str(row["issued_at"]), row["effective_until"]):
            continue
        applicable.append(row)
    return applicable


def _load_risk_actions(
    conn: sqlite3.Connection,
    strategy_key: str,
    now: datetime,
) -> list[sqlite3.Row]:
    rows = _query_rows(
        conn,
        """
        SELECT action_id, action_type, value, issued_at, effective_until, precedence, status
        FROM risk_actions
        WHERE strategy_key = ?
        ORDER BY precedence DESC, issued_at DESC, action_id DESC
        """,
        (strategy_key,),
    )
    applicable: list[sqlite3.Row] = []
    for row in rows:
        if str(row["status"]) != "active":
            continue
        if not _is_active(now, str(row["issued_at"]), row["effective_until"]):
            continue
        applicable.append(row)
    return applicable


def _query_rows(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise


def _select_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    chosen: dict[str, sqlite3.Row] = {}
    for row in rows:
        action_type = str(row["action_type"])
        if action_type not in chosen:
            chosen[action_type] = row
    return list(chosen.values())


def _parse_boolish(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "gate"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "ungate"}:
        return False
    raise ValueError(f"unsupported boolish policy value: {raw!r}")


def _parse_multiplier(raw: Any, action_type: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{action_type} must be a positive finite number")
    return value
