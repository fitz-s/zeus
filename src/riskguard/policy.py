from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any

from src.control.control_plane import (
    get_edge_threshold_multiplier,
    is_entries_paused,
)

logger = logging.getLogger(__name__)

# K1/#69: Explicit override precedence — higher number wins.
# When a higher-priority source locks a field, lower-priority sources
# are skipped and logged.  This table is the single source of truth;
# the if-chain in resolve_strategy_policy is the IMPLEMENTATION of this table.
OVERRIDE_PRECEDENCE = {
    "hard_safety": 3,   # system-level controls (pause_entries, tighten_risk)
    "manual_override": 2,   # human-issued control_overrides rows
    "risk_action": 1,   # automated risk_actions rows
}


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
    *,
    probability_semantics_revision: str | None = None,
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
        try:
            action_type = str(row["action_type"])
            if action_type == "gate":
                if "gated" in locked_fields:
                    logger.info("policy: manual_override gate skipped — field locked by higher-priority source")
                    continue
                gated = _parse_boolish(row["value"])
                # A permissive operator gate restores ordinary eligibility; it
                # is not an implicit waiver of a narrower, evidence-scoped
                # automated safety gate.  Only a restrictive manual gate owns
                # the deny field.  This keeps policy composition monotone:
                # permissions cannot erase a still-active loss cohort.
                if gated:
                    locked_fields.add("gated")
            elif action_type == "allocation_multiplier":
                if "allocation_multiplier" in locked_fields:
                    logger.info("policy: manual_override allocation_multiplier skipped — field locked by higher-priority source")
                    continue
                allocation_multiplier = _parse_multiplier(row["value"], action_type)
                locked_fields.add("allocation_multiplier")
            elif action_type == "threshold_multiplier":
                if "threshold_multiplier" in locked_fields:
                    logger.info("policy: manual_override threshold_multiplier skipped — field locked by higher-priority source")
                    continue
                threshold_multiplier = _parse_multiplier(row["value"], action_type)
                locked_fields.add("threshold_multiplier")
            elif action_type == "exit_only":
                if "exit_only" in locked_fields:
                    logger.info("policy: manual_override exit_only skipped — field locked by higher-priority source")
                    continue
                exit_only = _parse_boolish(row["value"])
                locked_fields.add("exit_only")
            else:
                continue
            sources.append(f"manual_override:{action_type}")
        except Exception as e:
            # B050: sqlite3.Row has no .get() — use keys() membership.
            row_id = row["override_id"] if "override_id" in row.keys() else "?"
            logger.error("policy: bad_row for manual_override %s: %s", row_id, e)
            continue

    for row in risk_actions:
        try:
            action_type = str(row["action_type"])
            if action_type == "gate":
                scoped_revisions = _risk_action_gate_probability_revisions(
                    row["value"]
                )
                if scoped_revisions:
                    current_revision = str(
                        probability_semantics_revision or ""
                    ).strip()
                    if current_revision and current_revision not in scoped_revisions:
                        continue
                if "gated" in locked_fields:
                    logger.info("policy: risk_action gate skipped — field locked by higher-priority source")
                    continue
                gated = _parse_boolish(row["value"])
                locked_fields.add("gated")
            elif action_type == "allocation_multiplier":
                if "allocation_multiplier" in locked_fields:
                    logger.info("policy: risk_action allocation_multiplier skipped — field locked by higher-priority source")
                    continue
                allocation_multiplier = _parse_multiplier(row["value"], action_type)
                locked_fields.add("allocation_multiplier")
            elif action_type == "threshold_multiplier":
                if "threshold_multiplier" in locked_fields:
                    logger.info("policy: risk_action threshold_multiplier skipped — field locked by higher-priority source")
                    continue
                threshold_multiplier = _parse_multiplier(row["value"], action_type)
                locked_fields.add("threshold_multiplier")
            elif action_type == "exit_only":
                if "exit_only" in locked_fields:
                    logger.info("policy: risk_action exit_only skipped — field locked by higher-priority source")
                    continue
                exit_only = _parse_boolish(row["value"])
                locked_fields.add("exit_only")
            else:
                continue
            sources.append(f"risk_action:{action_type}")
        except Exception as e:
            # B050: sqlite3.Row has no .get() — use keys() membership.
            row_id = row["action_id"] if "action_id" in row.keys() else "?"
            logger.error("policy: bad_row for risk_action %s: %s", row_id, e)
            continue

    return StrategyPolicy(
        strategy_key=strategy_key,
        gated=gated,
        allocation_multiplier=allocation_multiplier,
        threshold_multiplier=threshold_multiplier,
        exit_only=exit_only,
        sources=sources,
    )


def active_probability_revision_capital_gate_action_ids(
    conn: sqlite3.Connection,
    strategy_key: str,
    now: datetime,
    *,
    probability_semantics_revision: str | None,
) -> tuple[str, ...]:
    """Return exact automated proof gates applying to one probability revision.

    Entry controls such as ``pause_entries`` intentionally do not participate:
    this authority is consumed by every statistical capital action, including a
    reduce-only SELL, while deterministic/RED exits remain independent.  A
    missing revision matches a scoped gate fail-closed because the action cannot
    prove that it belongs to a different evidence cohort.
    """

    strategy = str(strategy_key or "").strip()
    if not strategy:
        raise ValueError("strategy_key is required")
    revision = str(probability_semantics_revision or "").strip()
    action_ids: list[str] = []
    for row in _select_rows(_load_risk_actions(conn, strategy, _normalize_datetime(now))):
        if str(row["action_type"] or "") != "gate":
            continue
        raw = str(row["value"] or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed probability revision capital gate") from exc
        if not isinstance(payload, dict) or payload.get("gate") is not True:
            continue
        revisions = payload.get("probability_semantics_revisions")
        if revisions is None:
            continue
        if not isinstance(revisions, list):
            raise ValueError("probability revision capital gate scope is invalid")
        scoped = frozenset(
            str(value).strip() for value in revisions if str(value).strip()
        )
        if not scoped:
            raise ValueError("probability revision capital gate scope is empty")
        if revision and revision not in scoped:
            continue
        action_id = str(row["action_id"] or "").strip()
        if not action_id:
            raise ValueError("probability revision capital gate action id is missing")
        action_ids.append(action_id)
    return tuple(sorted(action_ids))


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
    control_overrides_ref = _control_overrides_authority_ref(conn)
    if control_overrides_ref is None:
        logger.warning(
            "policy: manual control_overrides skipped — world authority not available "
            "on trade connection"
        )
        return []
    rows = _query_rows(
        conn,
        f"""
        SELECT override_id, target_type, target_key, action_type, value, issued_at,
               effective_until, precedence
        FROM {control_overrides_ref}
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


def _control_overrides_authority_ref(conn: sqlite3.Connection) -> str | None:
    """Return the schema-qualified control override authority for strategy policy.

    Live strategy policy may run on a trade-main connection with the world DB
    attached. In that shape, an unqualified ``control_overrides`` read resolves
    to the legacy archived trade ghost, not the canonical world authority. The
    risk layer must only consume world control authority; test/in-memory DBs keep
    the unqualified local surface.
    """

    databases = _database_list(conn)
    if _schema_has_control_overrides(conn, "world"):
        return "world.control_overrides"
    main_file = databases.get("main", "")
    if Path(main_file).name == "zeus-world.db" and _schema_has_control_overrides(conn, "main"):
        return "control_overrides"
    if Path(main_file).name == "zeus_trades.db":
        return None
    if _schema_has_control_overrides(conn, "main"):
        return "control_overrides"
    return None


def _database_list(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for row in rows:
        try:
            name = str(row["name"] or "")
            file_name = str(row["file"] or "")
        except (KeyError, IndexError, TypeError):
            name = str(row[1] or "")
            file_name = str(row[2] or "")
        if name:
            out[name] = file_name
    return out


def _schema_has_control_overrides(conn: sqlite3.Connection, schema: str) -> bool:
    if schema not in {"main", "world"}:
        return False
    try:
        row = conn.execute(
            f"""
            SELECT 1
            FROM {schema}.sqlite_master
            WHERE name = 'control_overrides'
              AND type IN ('table', 'view')
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


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


def _risk_action_gate_probability_revisions(raw: Any) -> frozenset[str]:
    """Return an automated gate's exact probability-evidence scope.

    A plain boolean remains a strategy-wide gate. A structured value may narrow
    only an automated risk action to named probability-semantics revisions. A
    caller that cannot name its revision remains gated (fail closed); a caller
    with a different exact revision is outside that evidence cohort.
    """

    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return frozenset()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return frozenset()
    if not isinstance(payload, dict) or payload.get("gate") is not True:
        return frozenset()
    revisions = payload.get("probability_semantics_revisions")
    if not isinstance(revisions, list):
        return frozenset()
    cleaned = frozenset(
        str(value).strip() for value in revisions if str(value).strip()
    )
    return cleaned


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
    """K1/#71: first-in wins per action_type; log discarded duplicates.

    B051: per-row isolation. A single malformed row (missing
    ``action_type`` column, non-string coercible value, etc.) must not
    discard every other row alongside it. Each row is handled under its
    own try/except.
    """
    chosen: dict[str, sqlite3.Row] = {}
    for row in rows:
        try:
            action_type = str(row["action_type"])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            # B050: sqlite3.Row has no .get() — use keys() membership.
            keys = row.keys() if hasattr(row, "keys") else []
            row_id = (
                row["override_id"] if "override_id" in keys
                else row["action_id"] if "action_id" in keys
                else "?"
            )
            logger.warning(
                "policy: malformed row %s skipped in _select_rows: %s",
                row_id, exc,
            )
            continue
        if action_type not in chosen:
            chosen[action_type] = row
        else:
            # B050: sqlite3.Row has no .get() — use keys() membership.
            keys = row.keys()
            if "override_id" in keys:
                row_id = row["override_id"]
            elif "action_id" in keys:
                row_id = row["action_id"]
            else:
                row_id = "?"
            logger.warning(
                "policy: duplicate %s (row %s) discarded — first-in wins",
                action_type, row_id,
            )
    return list(chosen.values())


def _parse_boolish(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("gate"), bool):
            return bool(payload["gate"])
    # K1/#71: removed "gate"/"ungate" — these are action keywords, not boolean
    # literals. Treating them as booleans loses semantic intent.
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"unsupported boolish policy value: {raw!r}")


def _parse_multiplier(raw: Any, action_type: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{action_type} must be a positive finite number")
    return value
