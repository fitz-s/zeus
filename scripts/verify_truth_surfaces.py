#!/usr/bin/env python3
"""
Diagnostic script: verify canonical truth surface consistency.

Checks LIVE state of zeus.db, risk_state-live.db, and JSON state files.
NOT pytest — run directly to get PASS/FAIL for each surface invariant.

Usage:
    python scripts/verify_truth_surfaces.py
"""
# Lifecycle: created=2026-04-07; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Diagnose truth-surface integrity and P0 training-readiness blockers.
# Reuse: Inspect docs/operations/current_data_state.md and the active packet receipt before using as closeout evidence.

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.calibration.metric_specs import METRIC_SPECS
from src.config import STATE_DIR, calibration_maturity_thresholds

DEFAULT_TRADE_DB = STATE_DIR / "zeus_trades.db"
SHARED_DB = STATE_DIR / "zeus-world.db"
RISK_DB = STATE_DIR / "risk_state.db"
POSITIONS_JSON = STATE_DIR / "positions.json"
STATUS_JSON = STATE_DIR / "status_summary.json"
SCHEDULER_HEALTH_JSON = STATE_DIR / "scheduler_jobs_health.json"
AUTO_PAUSE_TOMBSTONE = STATE_DIR / "auto_pause_failclosed.tombstone"
FORECAST_ROW_COUNT_EVIDENCE_JSON = STATE_DIR / "k2_forecasts_daily_row_count.json"

TODAY = date.today().isoformat()

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

READY = "READY"
NOT_READY = "NOT_READY"
ELIGIBLE_OBSERVATION_SOURCE_ROLES = frozenset({
    "historical_hourly",
})
ELIGIBLE_OBSERVATION_READER_AUTHORITIES = frozenset({
    "ICAO_STATION_NATIVE",
    "VERIFIED",
})
LEGACY_SETTLEMENT_MARKET_IDENTITY_COLUMNS = (
    "market_slug",
)
LEGACY_SETTLEMENT_SOURCE_EVIDENCE_COLUMNS = (
    "settlement_source",
    "settlement_source_type",
    "provenance_json",
)
LEGACY_SETTLEMENT_VALUE_COLUMNS = (
    "settlement_value",
    "winning_bin",
    "temperature_metric",
    "unit",
    "provenance_json",
)
SETTLEMENTS_V2_MARKET_IDENTITY_COLUMNS = (
    "city",
    "target_date",
    "temperature_metric",
    "market_slug",
)
MARKET_EVENTS_V2_MARKET_IDENTITY_COLUMNS = (
    "market_slug",
    "condition_id",
    "token_id",
    "city",
    "target_date",
    "temperature_metric",
)
P4_METRIC_LAYER_DECISIONS = frozenset({
    "instant-level",
    "daily-aggregate",
})
DEFAULT_TIGGE_TRACK_PATHS = {
    "mx2t6_high": (
        STATE_DIR / "tigge_localday_mx2t6_max",
        STATE_DIR / "tigge_ecmwf_ens_mx2t6_localday_max",
    ),
    "mn2t6_low": (
        STATE_DIR / "tigge_localday_mn2t6_min",
        STATE_DIR / "tigge_ecmwf_ens_mn2t6_localday_min",
    ),
}
DEFAULT_MARKET_RULE_PATHS = (
    STATE_DIR / "market_rules",
    STATE_DIR / "market_events",
    ROOT / "raw" / "market_rules",
    ROOT / "data" / "market_rules",
)
P4_TIGGE_MANIFEST_NAMES = (
    "tigge_manifest.json",
    "integrity_manifest.json",
    "manifest.json",
    "cloud_local_parity.json",
)
P4_ALLOWED_LANES = frozenset({
    "4.5.B-full",
    "4.6.A",
    "4.6.B",
    "4.6.C",
    "4.7",
    "4.8",
})
ENSEMBLE_SNAPSHOT_PREFLIGHT_COLUMNS = (
    "city",
    "target_date",
    "temperature_metric",
    "physical_quantity",
    "observation_field",
    "issue_time",
    "available_at",
    "fetch_time",
    "lead_hours",
    "members_json",
    "data_version",
    "training_allowed",
    "causality_status",
    "authority",
)
OBSERVATION_PREFLIGHT_BASE_COLUMNS = (
    "city",
    "target_date",
    "authority",
)
CALIBRATION_PAIR_PREFLIGHT_COLUMNS = (
    "temperature_metric",
    "observation_field",
    "range_label",
    "p_raw",
    "outcome",
    "lead_days",
    "season",
    "cluster",
    "data_version",
    "training_allowed",
    "causality_status",
    "authority",
    "decision_group_id",
)
_, _, MIN_PLATT_DECISION_GROUPS = calibration_maturity_thresholds()


def _scalar(cur, sql, *params):
    cur.execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None


def _row(cur, sql, *params):
    cur.execute(sql, params)
    return cur.fetchone()


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return (
        _scalar(
            cur,
            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            table,
        )
        or 0
    ) > 0


def _columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    try:
        return {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def _count(cur: sqlite3.Cursor, table: str, where: str | None = None) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(_scalar(cur, sql) or 0)


def _count_params(
    cur: sqlite3.Cursor,
    table: str,
    where: str,
    params: tuple[object, ...],
) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
    row = cur.fetchone()
    return int(row[0] if row else 0)


def _blank_or_empty_json_sql(column: str) -> str:
    value = f"TRIM(COALESCE(CAST({column} AS TEXT), ''))"
    compact = (
        "REPLACE(REPLACE(REPLACE(REPLACE("
        f"{value}, ' ', ''), char(9), ''), char(10), ''), char(13), '')"
    )
    return f"({value} = '' OR {compact} IN ('{{}}', '[]'))"


def _any_blank_sql(columns: tuple[str, ...]) -> str:
    return " OR ".join(_blank_or_empty_json_sql(column) for column in columns)


def _all_blank_sql(columns: tuple[str, ...]) -> str:
    return " AND ".join(_blank_or_empty_json_sql(column) for column in columns)


def _check_entry(
    *,
    check_id: str,
    status: str,
    detail: str,
    count: int | None = None,
    threshold: int | None = None,
    met: bool | None = None,
) -> dict:
    entry: dict[str, object] = {
        "id": check_id,
        "status": status,
        "detail": detail,
    }
    if count is not None:
        entry["count"] = count
    if threshold is not None:
        entry["threshold"] = threshold
    if met is not None:
        entry["met"] = met
    return entry


def _add_count_min_check(
    report: dict,
    cur: sqlite3.Cursor,
    *,
    table: str,
    threshold: int = 1,
    empty_code: str = "empty_v2_table",
) -> None:
    check_id = table
    if not _table_exists(cur, table):
        report["checks"][check_id] = _check_entry(
            check_id=check_id,
            status=FAIL,
            detail=f"{table} table is missing",
            count=0,
            threshold=threshold,
            met=False,
        )
        report["blockers"].append(
            {"code": "missing_table", "table": table, "count": 0}
        )
        return

    count = _count(cur, table)
    met = count >= threshold
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=PASS if met else FAIL,
        detail=f"{table} rows={count}, required>={threshold}",
        count=count,
        threshold=threshold,
        met=met,
    )
    if not met:
        report["blockers"].append(
            {"code": empty_code, "table": table, "count": count}
        )


def _add_missing_table_check(
    report: dict,
    *,
    check_id: str,
    table: str,
    detail: str,
    code: str = "missing_table",
) -> None:
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=FAIL,
        detail=detail,
        count=0,
        threshold=0,
        met=False,
    )
    blocker = {"code": code, "table": table, "count": 0}
    if blocker not in report["blockers"]:
        report["blockers"].append(blocker)


def _hko_gate_is_released(root: Path = ROOT) -> bool:
    """Return True if the hko_canonical gate was formally released in the preflight override YAML.

    Gate re-arms automatically if the YAML's released.released_at field is absent
    or the file cannot be read. This mirrors the antibody design: the YAML release
    block is the immune-system trigger, not hardcoded logic.

    Added 2026-05-03: supports HK quarantine-release cycle from
    docs/archives/packets/task_2026-05-02_hk_paris_release/work_log.md Step 2.
    """
    yaml_path = root / "architecture" / "preflight_overrides_2026-04-28.yaml"
    if not yaml_path.exists() or not _YAML_AVAILABLE:
        return False
    try:
        data = _yaml.safe_load(yaml_path.read_text())
        released = (data or {}).get("hko_canonical", {}).get("released", {})
        return bool((released or {}).get("released_at"))
    except Exception:
        return False


def _new_report(mode: str, world_db: Path) -> dict:
    return {
        "mode": mode,
        "database": str(world_db),
        "status": NOT_READY,
        "ready": False,
        "checks": {},
        "blockers": [],
    }


def _add_database_missing(report: dict, world_db: Path) -> None:
    report["checks"]["database_exists"] = _check_entry(
        check_id="database_exists",
        status=FAIL,
        detail=f"{world_db} not found",
        met=False,
    )
    report["blockers"].append(
        {"code": "missing_database", "table": None, "count": 0}
    )


def _finalize_report(report: dict) -> dict:
    ready = not report["blockers"]
    report["ready"] = ready
    report["status"] = READY if ready else NOT_READY
    return report


def _path_has_any_entry(path: Path) -> bool:
    if path.is_file():
        return True
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except (OSError, StopIteration):
        return False
    return True


def _json_value_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _json_has_key(payload: object, keys: tuple[str, ...]) -> bool:
    if not isinstance(payload, dict):
        return False
    for key, value in payload.items():
        if key in keys and _json_value_present(value):
            return True
        if isinstance(value, dict) and _json_has_key(value, keys):
            return True
    return False


def _json_text_for_key(payload: object, keys: tuple[str, ...]) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if key in keys and _json_value_present(value):
            return str(value).strip()
        if isinstance(value, dict):
            nested = _json_text_for_key(value, keys)
            if nested:
                return nested
    return None


def _json_bool_or_status_ok(
    payload: dict,
    *,
    bool_keys: tuple[str, ...] = (),
    status_keys: tuple[str, ...] = (),
) -> bool:
    for key in bool_keys:
        value = _json_text_for_key(payload, (key,))
        if value and value.lower() in {"true", "1", "yes", "ok", "pass", "passed", "verified"}:
            return True
    for key in status_keys:
        value = _json_text_for_key(payload, (key,))
        if value and value.lower() in {"ok", "pass", "passed", "verified", "green"}:
            return True
    return False


def _iter_direct_json_candidates(paths: tuple[Path, ...]) -> list[Path]:
    candidates: list[Path] = []
    for path in paths:
        if path.is_file():
            candidates.append(path)
            continue
        if not path.is_dir():
            continue
        for name in P4_TIGGE_MANIFEST_NAMES:
            candidate = path / name
            if candidate.is_file():
                candidates.append(candidate)
        try:
            direct_json = sorted(path.glob("*.json"))
        except OSError:
            direct_json = []
        for candidate in direct_json:
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _load_json_object(path: Path) -> tuple[dict | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {exc}"
    if not isinstance(payload, dict):
        return None, "not a JSON object"
    return payload, None


def _market_rule_acceptance_errors(payload: dict) -> list[str]:
    required_checks = (
        ("source URL/file", _json_has_key(payload, ("source_url", "source_file"))),
        ("station/source", _json_has_key(payload, ("station_id", "station", "source", "source_name"))),
        ("finalization policy", _json_has_key(payload, ("finalization_policy", "source_finalization_policy"))),
        ("rule version", _json_has_key(payload, ("rule_version", "market_rule_version"))),
        ("metric", _json_has_key(payload, ("temperature_metric", "metric"))),
        ("unit", _json_has_key(payload, ("unit", "temp_unit", "temperature_unit"))),
        (
            "bin identity",
            _json_has_key(
                payload,
                (
                    "bin_identity",
                    "bins",
                    "range_label",
                    "bin_type",
                    "condition_id",
                    "token_id",
                    "market_slug",
                ),
            ),
        ),
    )
    return [name for name, met in required_checks if not met]


def _tigge_manifest_errors(payload: dict, *, track: str) -> list[str]:
    errors: list[str] = []
    manifest_track = _json_text_for_key(payload, ("track", "dataset_track"))
    if manifest_track != track:
        errors.append("track")
    parity_verified = _json_bool_or_status_ok(
        payload,
        bool_keys=("cloud_local_parity_verified", "parity_verified"),
        status_keys=("cloud_local_parity", "parity_status"),
    )
    if not parity_verified:
        errors.append("cloud/local parity")
    hash_present = _json_has_key(
        payload,
        ("manifest_hash", "source_hash", "sha256", "cloud_hash", "local_hash"),
    )
    files = payload.get("files")
    if isinstance(files, list):
        hash_present = hash_present or any(
            isinstance(item, dict)
            and _json_has_key(item, ("sha256", "source_hash", "cloud_hash", "local_hash"))
            for item in files
        )
    if not hash_present:
        errors.append("hash evidence")
    source_verified_timestamps = _json_bool_or_status_ok(
        payload,
        bool_keys=("source_verified_timestamps",),
        status_keys=("timestamp_status",),
    )
    issue_verified = source_verified_timestamps or _json_bool_or_status_ok(
        payload,
        bool_keys=("issue_time_verified",),
        status_keys=("issue_time_status",),
    )
    available_verified = source_verified_timestamps or _json_bool_or_status_ok(
        payload,
        bool_keys=("available_at_verified",),
        status_keys=("available_at_status",),
    )
    if not issue_verified:
        errors.append("source-verified issue_time")
    if not available_verified:
        errors.append("source-verified available_at")
    return errors


def _first_accepted_json_artifact(
    paths: tuple[Path, ...],
    validator,
) -> tuple[Path | None, list[str]]:
    diagnostics: list[str] = []
    candidates = _iter_direct_json_candidates(paths)
    if not candidates:
        return None, ["no JSON acceptance artifact found"]
    for candidate in candidates:
        payload, error = _load_json_object(candidate)
        if error:
            diagnostics.append(f"{candidate}: {error}")
            continue
        assert payload is not None
        errors = validator(payload)
        if not errors:
            return candidate, diagnostics
        diagnostics.append(f"{candidate}: missing " + ", ".join(errors))
    return None, diagnostics


def _add_p4_check(
    report: dict,
    *,
    check_id: str,
    met: bool,
    detail: str,
    code: str,
    lane: str,
    count: int | None = None,
    threshold: int | None = None,
    blocking: bool = True,
) -> None:
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=PASS if met else (FAIL if blocking else WARN),
        detail=detail,
        count=count,
        threshold=threshold,
        met=met,
    )
    if not met and blocking:
        blocker = {"code": code, "lane": lane, "count": count if count is not None else 0}
        if blocker not in report["blockers"]:
            report["blockers"].append(blocker)


def _add_required_columns_check(
    report: dict,
    cur: sqlite3.Cursor,
    *,
    table: str,
    check_id: str,
    columns: tuple[str, ...],
) -> bool:
    if not _table_exists(cur, table):
        _add_missing_table_check(
            report,
            check_id=check_id,
            table=table,
            detail=f"{table} table is missing",
        )
        return False

    existing_columns = _columns(cur, table)
    missing_columns = [column for column in columns if column not in existing_columns]
    met = not missing_columns
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=PASS if met else FAIL,
        detail=(
            f"{table} required columns present"
            if met
            else f"{table} lacks required columns: " + ", ".join(missing_columns)
        ),
        count=len(missing_columns),
        threshold=0,
        met=met,
    )
    if not met:
        report["blockers"].append(
            {
                "code": "missing_required_columns",
                "table": table,
                "count": len(missing_columns),
                "columns": missing_columns,
            }
        )
    return met


def _add_required_identity_check(
    report: dict,
    cur: sqlite3.Cursor,
    *,
    table: str,
    check_id: str,
    columns: tuple[str, ...],
    missing_code: str = "missing_market_identity_columns",
    empty_code: str = "missing_market_identity",
) -> None:
    """Fail closed unless a market table carries non-empty identity fields."""
    if not _table_exists(cur, table):
        _add_missing_table_check(
            report,
            check_id=check_id,
            table=table,
            detail=f"{table} table is missing",
        )
        return

    existing_columns = _columns(cur, table)
    missing_columns = [column for column in columns if column not in existing_columns]
    if missing_columns:
        report["checks"][check_id] = _check_entry(
            check_id=check_id,
            status=FAIL,
            detail=(
                f"{table} lacks required identity columns: "
                + ", ".join(missing_columns)
            ),
            count=0,
            threshold=0,
            met=False,
        )
        report["blockers"].append(
            {"code": missing_code, "table": table, "count": len(missing_columns)}
        )
        return

    where = " OR ".join(
        f"{column} IS NULL OR TRIM(CAST({column} AS TEXT)) = ''"
        for column in columns
    )
    count = _count(cur, table, where)
    met = count == 0
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=PASS if met else FAIL,
        detail=f"{table} rows with missing identity={count}",
        count=count,
        threshold=0,
        met=met,
    )
    if not met:
        report["blockers"].append(
            {"code": empty_code, "table": table, "count": count}
        )


def _add_payload_identity_check(report: dict, cur: sqlite3.Cursor) -> None:
    check_id = "observation_instants_v2.payload_identity_present"
    table = "observation_instants_v2"
    if not _table_exists(cur, table):
        _add_missing_table_check(
            report,
            check_id=check_id,
            table=table,
            detail="observation_instants_v2 table is missing",
        )
        return

    columns = _columns(cur, table)
    source_columns = tuple(
        column for column in ("source_url", "source_file") if column in columns
    )
    station_columns = tuple(
        column
        for column in ("station_registry_version", "station_registry_hash")
        if column in columns
    )
    required_columns = tuple(
        column for column in ("payload_hash", "parser_version") if column in columns
    )

    if len(required_columns) < 2 or not source_columns or not station_columns:
        if "provenance_json" in columns:
            identity_missing_where = _obs_v2_provenance_identity_missing_sql()
            count = _count(
                cur,
                table,
                f"""
                COALESCE(training_allowed, 0) = 1
                AND ({identity_missing_where})
                """,
            )
            met = count == 0
            report["checks"][check_id] = _check_entry(
                check_id=check_id,
                status=PASS if met else FAIL,
                detail=(
                    "training-allowed observation_instants_v2 rows missing "
                    f"provenance_json payload identity={count}"
                ),
                count=count,
                threshold=0,
                met=met,
            )
            if not met:
                report["blockers"].append(
                    {"code": "payload_identity_missing", "table": table, "count": count}
                )
            return

        report["checks"][check_id] = _check_entry(
            check_id=check_id,
            status=PASS,
            detail=(
                "payload identity columns not present as a complete contract; "
                "no rows checkable"
            ),
            count=0,
            threshold=0,
            met=True,
        )
        return

    identity_missing_where = " OR ".join(
        (
            _any_blank_sql(required_columns),
            f"({_all_blank_sql(source_columns)})",
            f"({_all_blank_sql(station_columns)})",
        )
    )
    count = _count(
        cur,
        table,
        f"""
        COALESCE(training_allowed, 0) = 1
        AND ({identity_missing_where})
        """,
    )
    met = count == 0
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=PASS if met else FAIL,
        detail=f"training-allowed observation_instants_v2 rows missing payload identity={count}",
        count=count,
        threshold=0,
        met=met,
    )
    if not met:
        report["blockers"].append(
            {"code": "payload_identity_missing", "table": table, "count": count}
        )


def _add_observation_reader_identity_check(
    report: dict,
    cur: sqlite3.Cursor,
) -> None:
    check_id = "observation_instants_v2.reader_identity_unsafe"
    table = "observation_instants_v2"
    if not _table_exists(cur, table):
        _add_missing_table_check(
            report,
            check_id=check_id,
            table=table,
            detail="observation_instants_v2 table is missing",
        )
        return

    columns = _columns(cur, table)
    required_columns = {"training_allowed", "authority", "data_version"}
    if required_columns.issubset(columns):
        quoted_authorities = ", ".join(
            f"'{authority}'"
            for authority in sorted(ELIGIBLE_OBSERVATION_READER_AUTHORITIES)
        )
        count = _count(
            cur,
            table,
            f"""
            COALESCE(training_allowed, 0) = 1
            AND (
                authority IS NULL
                OR authority NOT IN ({quoted_authorities})
                OR data_version IS NULL
                OR TRIM(CAST(data_version AS TEXT)) = ''
                OR TRIM(CAST(data_version AS TEXT)) = 'v0'
                OR TRIM(CAST(data_version AS TEXT)) NOT LIKE 'v1.%'
            )
            """,
        )
        detail = (
            "training-allowed observation_instants_v2 rows with unsafe "
            f"reader authority/data_version={count}"
        )
        code = check_id
    else:
        count = _count(cur, table)
        missing = ", ".join(sorted(required_columns - columns))
        detail = (
            "observation_instants_v2 lacks reader identity columns: "
            f"{missing or 'unknown'}"
        )
        code = "missing_reader_identity_columns"

    met = count == 0
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=PASS if met else FAIL,
        detail=detail,
        count=count,
        threshold=0,
        met=met,
    )
    if not met:
        report["blockers"].append(
            {"code": code, "table": table, "count": count}
        )


def _json_text_blank_sql(json_column: str, key: str) -> str:
    return (
        f"json_extract({json_column}, '$.{key}') IS NULL "
        f"OR TRIM(CAST(json_extract({json_column}, '$.{key}') AS TEXT)) = ''"
    )


def _obs_v2_provenance_identity_missing_sql() -> str:
    source_missing = " AND ".join(
        _json_text_blank_sql("provenance_json", key)
        for key in ("source_url", "source_file")
    )
    station_missing = " AND ".join(
        _json_text_blank_sql("provenance_json", key)
        for key in ("station_id", "station_registry_version", "station_registry_hash")
    )
    required_missing = " OR ".join(
        _json_text_blank_sql("provenance_json", key)
        for key in ("payload_hash", "parser_version")
    )
    return f"""
        CASE
          WHEN provenance_json IS NULL
            OR TRIM(CAST(provenance_json AS TEXT)) = ''
            OR json_valid(provenance_json) = 0
          THEN 1
          WHEN {required_missing}
            OR ({source_missing})
            OR ({station_missing})
          THEN 1
          ELSE 0
        END = 1
    """


def _add_legacy_settlement_check_result(
    report: dict,
    *,
    check_id: str,
    code: str,
    count: int,
    detail: str,
    table: str = "settlements",
) -> None:
    met = count == 0
    report["checks"][check_id] = _check_entry(
        check_id=check_id,
        status=PASS if met else FAIL,
        detail=detail,
        count=count,
        threshold=0,
        met=met,
    )
    if not met:
        report["blockers"].append({"code": code, "table": table, "count": count})


def _settlements_v2_identity_incomplete(cur: sqlite3.Cursor) -> tuple[bool, str]:
    table = "settlements_v2"
    if not _table_exists(cur, table):
        return True, "settlements_v2 table is missing"

    row_count = _count(cur, table)
    if row_count == 0:
        return True, "settlements_v2 rows=0"

    columns = _columns(cur, table)
    missing_columns = [
        column
        for column in SETTLEMENTS_V2_MARKET_IDENTITY_COLUMNS
        if column not in columns
    ]
    if missing_columns:
        return (
            True,
            "settlements_v2 lacks identity columns: " + ", ".join(missing_columns),
        )

    missing_identity = _count(
        cur,
        table,
        _any_blank_sql(SETTLEMENTS_V2_MARKET_IDENTITY_COLUMNS),
    )
    if missing_identity:
        return (
            True,
            f"settlements_v2 rows with incomplete market identity={missing_identity}",
        )

    return False, f"settlements_v2 market identity rows={row_count}"


def _add_legacy_settlement_evidence_checks(
    report: dict,
    cur: sqlite3.Cursor,
) -> None:
    table = "settlements"
    checks = report["checks"]

    if not _table_exists(cur, table):
        detail = "legacy settlements table is absent; no legacy rows to classify"
        for check_id in (
            "settlements.legacy_market_identity_present",
            "settlements.legacy_finalization_policy_present",
            "settlements.legacy_value_complete",
            "settlements.legacy_evidence_only",
        ):
            checks[check_id] = _check_entry(
                check_id=check_id,
                status=PASS,
                detail=detail,
                count=0,
                threshold=0,
                met=True,
            )
        return

    row_count = _count(cur, table)
    if row_count == 0:
        detail = "legacy settlements rows=0"
        for check_id in (
            "settlements.legacy_market_identity_present",
            "settlements.legacy_finalization_policy_present",
            "settlements.legacy_value_complete",
            "settlements.legacy_evidence_only",
        ):
            checks[check_id] = _check_entry(
                check_id=check_id,
                status=PASS,
                detail=detail,
                count=0,
                threshold=0,
                met=True,
            )
        return

    columns = _columns(cur, table)

    identity_columns = tuple(
        column
        for column in LEGACY_SETTLEMENT_MARKET_IDENTITY_COLUMNS
        if column in columns
    )
    if identity_columns:
        market_identity_missing = _count(cur, table, _all_blank_sql(identity_columns))
        market_detail = (
            "legacy settlements rows with no accepted market identity="
            f"{market_identity_missing}; accepted columns="
            + ", ".join(identity_columns)
        )
    else:
        market_identity_missing = row_count
        market_detail = (
            "legacy settlements has rows but lacks accepted market identity "
            "columns: " + ", ".join(LEGACY_SETTLEMENT_MARKET_IDENTITY_COLUMNS)
        )
    _add_legacy_settlement_check_result(
        report,
        check_id="settlements.legacy_market_identity_present",
        code="settlements.legacy_market_identity_missing",
        count=market_identity_missing,
        detail=market_detail,
    )

    missing_source_columns = [
        column
        for column in LEGACY_SETTLEMENT_SOURCE_EVIDENCE_COLUMNS
        if column not in columns
    ]
    if missing_source_columns:
        source_evidence_missing = row_count
    else:
        source_evidence_missing = _count(
            cur,
            table,
            " OR ".join(
                (
                    _any_blank_sql(LEGACY_SETTLEMENT_SOURCE_EVIDENCE_COLUMNS),
                    "json_extract(provenance_json, '$.rounding_rule') IS NULL",
                    "TRIM(CAST(json_extract(provenance_json, '$.rounding_rule') AS TEXT)) = ''",
                )
            ),
        )
    finalization_detail = (
        "legacy settlements lacks explicit source-finalization timestamp, "
        "revision/finalization policy, and market-rule version; settled_at is "
        "a local write timestamp and is not accepted as source finalization "
        f"proof; current source/rounding evidence missing rows={source_evidence_missing}"
    )
    if missing_source_columns:
        finalization_detail += (
            "; missing current evidence columns=" + ", ".join(missing_source_columns)
        )
    _add_legacy_settlement_check_result(
        report,
        check_id="settlements.legacy_finalization_policy_present",
        code="settlements.legacy_finalization_policy_missing",
        count=row_count,
        detail=finalization_detail,
    )

    missing_value_columns = [
        column for column in LEGACY_SETTLEMENT_VALUE_COLUMNS if column not in columns
    ]
    if missing_value_columns:
        value_incomplete = (
            _count(cur, table, "authority = 'VERIFIED'")
            if "authority" in columns
            else row_count
        )
        value_detail = (
            "legacy settlements table shape lacks value evidence columns: "
            + ", ".join(missing_value_columns)
            + "; value completeness only gates VERIFIED rows"
        )
    else:
        value_where = _any_blank_sql(LEGACY_SETTLEMENT_VALUE_COLUMNS)
        authority_where = (
            "authority = 'VERIFIED'"
            if "authority" in columns
            else "1 = 1"
        )
        value_incomplete = _count(cur, table, f"{authority_where} AND ({value_where})")
        value_detail = (
            "VERIFIED legacy settlements rows missing settlement value, bin, metric, unit, "
            f"or provenance={value_incomplete}"
        )
    _add_legacy_settlement_check_result(
        report,
        check_id="settlements.legacy_value_complete",
        code="settlements.legacy_value_incomplete",
        count=value_incomplete,
        detail=value_detail,
    )

    v2_incomplete, v2_detail = _settlements_v2_identity_incomplete(cur)
    evidence_only_count = row_count if v2_incomplete else 0
    _add_legacy_settlement_check_result(
        report,
        check_id="settlements.legacy_evidence_only",
        code="settlements.legacy_evidence_only",
        count=evidence_only_count,
        detail=(
            f"legacy settlements rows={row_count}; {v2_detail}; "
            "legacy rows are evidence-only until canonical v2 market identity is ready"
        ),
    )


def _metric_allowed_versions() -> dict[str, str]:
    return {
        spec.identity.temperature_metric: spec.allowed_data_version
        for spec in METRIC_SPECS
    }


def _add_observation_instants_safety_checks(report: dict, cur: sqlite3.Cursor) -> None:
    table = "observation_instants_v2"
    if not _table_exists(cur, table):
        for check_id in (
            "observation_instants_v2.training_role_unsafe",
            "observation_instants_v2.causality_unsafe",
            "observation_instants_v2.reader_identity_unsafe",
        ):
            _add_missing_table_check(
                report,
                check_id=check_id,
                table=table,
                detail="observation_instants_v2 table is missing",
            )
        _add_payload_identity_check(report, cur)
        return

    columns = _columns(cur, table)
    quoted_roles = ", ".join(
        f"'{role}'" for role in sorted(ELIGIBLE_OBSERVATION_SOURCE_ROLES)
    )

    if {"training_allowed", "source_role"}.issubset(columns):
        role_count = _count(
            cur,
            table,
            f"""
            COALESCE(training_allowed, 0) = 1
            AND (
                source_role IS NULL
                OR source_role = ''
                OR source_role NOT IN ({quoted_roles})
            )
            """,
        )
        role_detail = (
            "training-allowed observation_instants_v2 rows with unsafe "
            f"source_role={role_count}"
        )
        role_code = "observation_instants_v2.training_role_unsafe"
    else:
        role_count = _count(cur, table)
        role_detail = "observation_instants_v2 lacks training_allowed/source_role columns"
        role_code = "missing_source_role_columns"
    role_met = role_count == 0
    report["checks"]["observation_instants_v2.training_role_unsafe"] = _check_entry(
        check_id="observation_instants_v2.training_role_unsafe",
        status=PASS if role_met else FAIL,
        detail=role_detail,
        count=role_count,
        threshold=0,
        met=role_met,
    )
    if not role_met:
        report["blockers"].append(
            {"code": role_code, "table": table, "count": role_count}
        )

    if {"training_allowed", "causality_status"}.issubset(columns):
        causality_count = _count(
            cur,
            table,
            """
            COALESCE(training_allowed, 0) = 1
            AND (
                causality_status IS NULL
                OR TRIM(CAST(causality_status AS TEXT)) = ''
                OR UPPER(TRIM(CAST(causality_status AS TEXT))) != 'OK'
            )
            """,
        )
        causality_detail = (
            "training-allowed observation_instants_v2 rows with unsafe "
            f"causality_status={causality_count}"
        )
    elif "training_allowed" in columns:
        causality_count = _count(
            cur,
            table,
            "COALESCE(training_allowed, 0) = 1",
        )
        causality_detail = (
            "observation_instants_v2 lacks causality_status column for "
            f"training_allowed rows={causality_count}"
        )
    else:
        causality_count = _count(cur, table)
        causality_detail = "observation_instants_v2 lacks training_allowed/causality_status columns"
    causality_met = causality_count == 0
    report["checks"]["observation_instants_v2.causality_unsafe"] = _check_entry(
        check_id="observation_instants_v2.causality_unsafe",
        status=PASS if causality_met else FAIL,
        detail=causality_detail,
        count=causality_count,
        threshold=0,
        met=causality_met,
    )
    if not causality_met:
        report["blockers"].append(
            {
                "code": "observation_instants_v2.causality_unsafe",
                "table": table,
                "count": causality_count,
            }
        )

    _add_payload_identity_check(report, cur)
    _add_observation_reader_identity_check(report, cur)


def _add_rebuild_snapshot_preflight_checks(report: dict, cur: sqlite3.Cursor) -> None:
    table = "ensemble_snapshots_v2"
    if not _add_required_columns_check(
        report,
        cur,
        table=table,
        check_id="ensemble_snapshots_v2.preflight_columns_present",
        columns=ENSEMBLE_SNAPSHOT_PREFLIGHT_COLUMNS,
    ):
        return

    allowed_versions = _metric_allowed_versions()
    metric_names = tuple(allowed_versions)
    quoted_metrics = ", ".join(f"'{metric}'" for metric in metric_names)
    invalid_metric_count = _count(
        cur,
        table,
        f"""
        COALESCE(training_allowed, 0) = 1
        AND (
            temperature_metric IS NULL
            OR TRIM(CAST(temperature_metric AS TEXT)) = ''
            OR temperature_metric NOT IN ({quoted_metrics})
        )
        """,
    )
    invalid_metric_met = invalid_metric_count == 0
    report["checks"]["ensemble_snapshots_v2.metric_scope_safe"] = _check_entry(
        check_id="ensemble_snapshots_v2.metric_scope_safe",
        status=PASS if invalid_metric_met else FAIL,
        detail=(
            "training-allowed ensemble_snapshots_v2 rows with invalid "
            f"temperature_metric={invalid_metric_count}"
        ),
        count=invalid_metric_count,
        threshold=0,
        met=invalid_metric_met,
    )
    if not invalid_metric_met:
        report["blockers"].append(
            {
                "code": "ensemble_snapshots_v2.metric_scope_unsafe",
                "table": table,
                "count": invalid_metric_count,
            }
        )

    for spec in METRIC_SPECS:
        identity = spec.identity
        metric = identity.temperature_metric
        eligible_where = """
            temperature_metric = ?
            AND physical_quantity = ?
            AND observation_field = ?
            AND data_version = ?
            AND COALESCE(training_allowed, 0) = 1
            AND UPPER(TRIM(CAST(authority AS TEXT))) = 'VERIFIED'
            AND UPPER(TRIM(CAST(causality_status AS TEXT))) = 'OK'
            AND city IS NOT NULL AND TRIM(CAST(city AS TEXT)) != ''
            AND target_date IS NOT NULL AND TRIM(CAST(target_date AS TEXT)) != ''
            AND issue_time IS NOT NULL AND TRIM(CAST(issue_time AS TEXT)) != ''
            AND available_at IS NOT NULL AND TRIM(CAST(available_at AS TEXT)) != ''
            AND fetch_time IS NOT NULL AND TRIM(CAST(fetch_time AS TEXT)) != ''
            AND LOWER(CAST(issue_time AS TEXT)) NOT LIKE '%reconstruct%'
            AND LOWER(CAST(available_at AS TEXT)) NOT LIKE '%reconstruct%'
            AND LOWER(CAST(fetch_time AS TEXT)) NOT LIKE '%reconstruct%'
            AND members_json IS NOT NULL AND TRIM(CAST(members_json AS TEXT)) != ''
        """
        eligible_count = _count_params(
            cur,
            table,
            eligible_where,
            (
                metric,
                identity.physical_quantity,
                identity.observation_field,
                spec.allowed_data_version,
            ),
        )
        eligible_met = eligible_count >= 1
        eligible_check_id = f"ensemble_snapshots_v2.{metric}.rebuild_eligible_present"
        report["checks"][eligible_check_id] = _check_entry(
            check_id=eligible_check_id,
            status=PASS if eligible_met else FAIL,
            detail=f"{metric} rebuild-eligible ensemble_snapshots_v2 rows={eligible_count}",
            count=eligible_count,
            threshold=1,
            met=eligible_met,
        )
        if not eligible_met:
            report["blockers"].append(
                {
                    "code": "empty_rebuild_eligible_snapshots",
                    "table": table,
                    "count": eligible_count,
                    "temperature_metric": metric,
                }
            )

        unsafe_where = """
            temperature_metric = ?
            AND COALESCE(training_allowed, 0) = 1
            AND (
                physical_quantity IS NULL
                OR physical_quantity != ?
                OR observation_field IS NULL
                OR observation_field != ?
                OR data_version IS NULL
                OR data_version != ?
                OR UPPER(TRIM(CAST(authority AS TEXT))) != 'VERIFIED'
                OR UPPER(TRIM(CAST(causality_status AS TEXT))) != 'OK'
                OR city IS NULL OR TRIM(CAST(city AS TEXT)) = ''
                OR target_date IS NULL OR TRIM(CAST(target_date AS TEXT)) = ''
                OR issue_time IS NULL OR TRIM(CAST(issue_time AS TEXT)) = ''
                OR available_at IS NULL OR TRIM(CAST(available_at AS TEXT)) = ''
                OR fetch_time IS NULL OR TRIM(CAST(fetch_time AS TEXT)) = ''
                OR LOWER(CAST(issue_time AS TEXT)) LIKE '%reconstruct%'
                OR LOWER(CAST(available_at AS TEXT)) LIKE '%reconstruct%'
                OR LOWER(CAST(fetch_time AS TEXT)) LIKE '%reconstruct%'
                OR members_json IS NULL OR TRIM(CAST(members_json AS TEXT)) = ''
            )
        """
        unsafe_count = _count_params(
            cur,
            table,
            unsafe_where,
            (
                metric,
                identity.physical_quantity,
                identity.observation_field,
                spec.allowed_data_version,
            ),
        )
        unsafe_met = unsafe_count == 0
        unsafe_check_id = f"ensemble_snapshots_v2.{metric}.rebuild_input_safe"
        report["checks"][unsafe_check_id] = _check_entry(
            check_id=unsafe_check_id,
            status=PASS if unsafe_met else FAIL,
            detail=f"{metric} training-allowed snapshot rows with unsafe rebuild input={unsafe_count}",
            count=unsafe_count,
            threshold=0,
            met=unsafe_met,
        )
        if not unsafe_met:
            report["blockers"].append(
                {
                    "code": "ensemble_snapshots_v2.rebuild_input_unsafe",
                    "table": table,
                    "count": unsafe_count,
                    "temperature_metric": metric,
                }
            )


def _observation_provenance_column(columns: set[str], metric: str) -> str | None:
    if "provenance_metadata" in columns:
        return "provenance_metadata"
    split_column = (
        "high_provenance_metadata"
        if metric == "high"
        else "low_provenance_metadata"
    )
    if split_column in columns:
        return split_column
    return None


def _add_rebuild_observation_preflight_checks(report: dict, cur: sqlite3.Cursor) -> None:
    table = "observations"
    required_columns = OBSERVATION_PREFLIGHT_BASE_COLUMNS + tuple(
        spec.identity.observation_field for spec in METRIC_SPECS
    )
    if not _add_required_columns_check(
        report,
        cur,
        table=table,
        check_id="observations.preflight_columns_present",
        columns=required_columns,
    ):
        return

    columns = _columns(cur, table)
    has_source = "source" in columns
    has_city = "city" in columns
    for spec in METRIC_SPECS:
        metric = spec.identity.temperature_metric
        obs_column = spec.identity.observation_field
        label_where = f"""
            authority = 'VERIFIED'
            AND {obs_column} IS NOT NULL
        """
        label_count = _count(cur, table, label_where)
        label_met = label_count >= 1
        label_check_id = f"observations.{metric}.verified_labels_present"
        report["checks"][label_check_id] = _check_entry(
            check_id=label_check_id,
            status=PASS if label_met else FAIL,
            detail=f"{metric} VERIFIED observation label rows={label_count}",
            count=label_count,
            threshold=1,
            met=label_met,
        )
        if not label_met:
            report["blockers"].append(
                {
                    "code": "empty_verified_observation_labels",
                    "table": table,
                    "count": label_count,
                    "temperature_metric": metric,
                }
            )

        provenance_column = _observation_provenance_column(columns, metric)
        if provenance_column is None:
            report["checks"][f"observations.{metric}.provenance_present"] = _check_entry(
                check_id=f"observations.{metric}.provenance_present",
                status=FAIL,
                detail="observations lacks provenance metadata columns",
                count=0,
                threshold=0,
                met=False,
            )
            report["blockers"].append(
                {
                    "code": "missing_observation_provenance_columns",
                    "table": table,
                    "count": 0,
                    "temperature_metric": metric,
                }
            )
            continue

        provenance_where = f"{label_where} AND {_blank_or_empty_json_sql(provenance_column)}"
        provenance_count = _count(cur, table, provenance_where)
        provenance_met = provenance_count == 0
        provenance_check_id = f"observations.{metric}.provenance_present"
        report["checks"][provenance_check_id] = _check_entry(
            check_id=provenance_check_id,
            status=PASS if provenance_met else FAIL,
            detail=f"{metric} VERIFIED observation labels without provenance={provenance_count}",
            count=provenance_count,
            threshold=0,
            met=provenance_met,
        )
        if not provenance_met:
            report["blockers"].append(
                {
                    "code": "observations.verified_without_provenance",
                    "table": table,
                    "count": provenance_count,
                    "temperature_metric": metric,
                }
            )

        if has_source:
            wu_where = (
                f"{provenance_where} "
                "AND LOWER(COALESCE(source, '')) LIKE 'wu%'"
            )
            wu_count = _count(cur, table, wu_where)
            wu_met = wu_count == 0
            wu_check_id = f"observations.{metric}.wu_provenance_present"
            report["checks"][wu_check_id] = _check_entry(
                check_id=wu_check_id,
                status=PASS if wu_met else FAIL,
                detail=f"{metric} WU VERIFIED observation labels without provenance={wu_count}",
                count=wu_count,
                threshold=0,
                met=wu_met,
            )
            if not wu_met:
                report["blockers"].append(
                    {
                        "code": "observations.wu_empty_provenance",
                        "table": table,
                        "count": wu_count,
                        "temperature_metric": metric,
                    }
                )

        hko_predicates = []
        if has_source:
            hko_predicates.append("LOWER(COALESCE(source, '')) LIKE 'hko%'")
        if has_city:
            hko_predicates.append(
                "LOWER(COALESCE(city, '')) IN ('hong kong', 'hong_kong', 'hk', 'hkg')"
            )
        if hko_predicates:
            hko_where = f"{label_where} AND ({' OR '.join(hko_predicates)})"
            hko_count = _count(cur, table, hko_where)
            # Gate is advisory (non-blocking) if the hko_canonical release
            # block has been signed in architecture/preflight_overrides_2026-04-28.yaml.
            # The release block is the immune-system trigger — removing
            # released_at from the YAML re-arms the gate automatically.
            # Added 2026-05-03 per work_log task_2026-05-02_hk_paris_release Step 5a.
            hko_released = _hko_gate_is_released()
            hko_met = hko_count == 0
            hko_check_id = f"observations.{metric}.hko_training_blocked"
            report["checks"][hko_check_id] = _check_entry(
                check_id=hko_check_id,
                status=PASS if (hko_met or hko_released) else FAIL,
                detail=(
                    f"{metric} HKO/Hong Kong VERIFIED labels requiring fresh "
                    f"source audit={hko_count}"
                    + (" (advisory: hko_canonical released)" if hko_released and not hko_met else "")
                ),
                count=hko_count,
                threshold=0,
                met=hko_met or hko_released,
            )
            if not hko_met and not hko_released:
                report["blockers"].append(
                    {
                        "code": "observations.hko_requires_fresh_source_audit",
                        "table": table,
                        "count": hko_count,
                        "temperature_metric": metric,
                    }
                )


def _add_platt_pair_preflight_checks(report: dict, cur: sqlite3.Cursor) -> None:
    table = "calibration_pairs_v2"
    if not _add_required_columns_check(
        report,
        cur,
        table=table,
        check_id="calibration_pairs_v2.preflight_columns_present",
        columns=CALIBRATION_PAIR_PREFLIGHT_COLUMNS,
    ):
        return

    pair_quality_checks = (
        (
            "calibration_pairs_v2.authority_safe",
            "calibration_pairs_v2.authority_unsafe",
            """
            COALESCE(training_allowed, 0) = 1
            AND (
                authority IS NULL
                OR TRIM(CAST(authority AS TEXT)) = ''
                OR UPPER(TRIM(CAST(authority AS TEXT))) != 'VERIFIED'
            )
            """,
            "training-allowed calibration pair rows with non-VERIFIED authority",
        ),
        (
            "calibration_pairs_v2.causality_safe",
            "calibration_pairs_v2.causality_unsafe",
            """
            COALESCE(training_allowed, 0) = 1
            AND (
                causality_status IS NULL
                OR TRIM(CAST(causality_status AS TEXT)) = ''
                OR UPPER(TRIM(CAST(causality_status AS TEXT))) != 'OK'
            )
            """,
            "training-allowed calibration pair rows with unsafe causality_status",
        ),
        (
            "calibration_pairs_v2.decision_group_present",
            "calibration_pairs_v2.decision_group_missing",
            """
            COALESCE(training_allowed, 0) = 1
            AND authority = 'VERIFIED'
            AND (decision_group_id IS NULL OR TRIM(CAST(decision_group_id AS TEXT)) = '')
            """,
            "VERIFIED training-allowed calibration pair rows missing decision_group_id",
        ),
        (
            "calibration_pairs_v2.p_raw_domain_safe",
            "calibration_pairs_v2.p_raw_domain_unsafe",
            """
            COALESCE(training_allowed, 0) = 1
            AND authority = 'VERIFIED'
            AND (p_raw IS NULL OR p_raw < 0.0 OR p_raw > 1.0)
            """,
            "VERIFIED training-allowed calibration pair rows with p_raw outside [0, 1]",
        ),
        (
            "calibration_pairs_v2.required_values_present",
            "calibration_pairs_v2.required_values_missing",
            f"""
            COALESCE(training_allowed, 0) = 1
            AND authority = 'VERIFIED'
            AND (
                {_any_blank_sql(('range_label', 'season', 'cluster', 'data_version'))}
                OR lead_days IS NULL
                OR outcome IS NULL
                OR outcome NOT IN (0, 1)
            )
            """,
            "VERIFIED training-allowed calibration pair rows missing fit values",
        ),
    )
    for check_id, code, where, detail_prefix in pair_quality_checks:
        count = _count(cur, table, where)
        met = count == 0
        report["checks"][check_id] = _check_entry(
            check_id=check_id,
            status=PASS if met else FAIL,
            detail=f"{detail_prefix}={count}",
            count=count,
            threshold=0,
            met=met,
        )
        if not met:
            report["blockers"].append({"code": code, "table": table, "count": count})

    for spec in METRIC_SPECS:
        identity = spec.identity
        metric = identity.temperature_metric
        mismatch_count = _count_params(
            cur,
            table,
            """
            temperature_metric = ?
            AND COALESCE(training_allowed, 0) = 1
            AND (
                observation_field IS NULL
                OR observation_field != ?
                OR data_version IS NULL
                OR data_version != ?
            )
            """,
            (metric, identity.observation_field, spec.allowed_data_version),
        )
        mismatch_met = mismatch_count == 0
        mismatch_check_id = f"calibration_pairs_v2.{metric}.identity_safe"
        report["checks"][mismatch_check_id] = _check_entry(
            check_id=mismatch_check_id,
            status=PASS if mismatch_met else FAIL,
            detail=f"{metric} training-allowed calibration pair rows with identity mismatch={mismatch_count}",
            count=mismatch_count,
            threshold=0,
            met=mismatch_met,
        )
        if not mismatch_met:
            report["blockers"].append(
                {
                    "code": "calibration_pairs_v2.identity_mismatch",
                    "table": table,
                    "count": mismatch_count,
                    "temperature_metric": metric,
                }
            )

        cur.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT cluster, season, data_version,
                       COUNT(DISTINCT decision_group_id) AS n_eff
                FROM calibration_pairs_v2
                WHERE temperature_metric = ?
                  AND observation_field = ?
                  AND data_version = ?
                  AND COALESCE(training_allowed, 0) = 1
                  AND authority = 'VERIFIED'
                  AND UPPER(TRIM(CAST(causality_status AS TEXT))) = 'OK'
                  AND decision_group_id IS NOT NULL
                  AND TRIM(CAST(decision_group_id AS TEXT)) != ''
                  AND p_raw IS NOT NULL
                  AND p_raw >= 0.0
                  AND p_raw <= 1.0
                  AND cluster IS NOT NULL
                  AND TRIM(CAST(cluster AS TEXT)) != ''
                  AND season IS NOT NULL
                  AND TRIM(CAST(season AS TEXT)) != ''
                  AND range_label IS NOT NULL
                  AND TRIM(CAST(range_label AS TEXT)) != ''
                  AND lead_days IS NOT NULL
                  AND outcome IN (0, 1)
                GROUP BY cluster, season, data_version
                HAVING n_eff >= ?
            )
            """,
            (
                metric,
                identity.observation_field,
                spec.allowed_data_version,
                MIN_PLATT_DECISION_GROUPS,
            ),
        )
        bucket_row = cur.fetchone()
        bucket_count = int(bucket_row[0] if bucket_row else 0)
        bucket_met = bucket_count >= 1
        bucket_check_id = f"calibration_pairs_v2.{metric}.mature_bucket_present"
        report["checks"][bucket_check_id] = _check_entry(
            check_id=bucket_check_id,
            status=PASS if bucket_met else FAIL,
            detail=(
                f"{metric} Platt-refit buckets with n_eff>="
                f"{MIN_PLATT_DECISION_GROUPS}: {bucket_count}"
            ),
            count=bucket_count,
            threshold=1,
            met=bucket_met,
        )
        if not bucket_met:
            report["blockers"].append(
                {
                    "code": "empty_platt_refit_bucket",
                    "table": table,
                    "count": bucket_count,
                    "temperature_metric": metric,
                }
            )


def _add_p4_table_populated_check(
    report: dict,
    cur: sqlite3.Cursor,
    *,
    table: str,
    lane: str,
    code: str = "p4_table_empty",
) -> None:
    if not _table_exists(cur, table):
        _add_p4_check(
            report,
            check_id=f"{table}.p4_populated",
            met=False,
            detail=f"{table} table is missing",
            code="missing_table",
            lane=lane,
            count=0,
            threshold=1,
        )
        return
    count = _count(cur, table)
    _add_p4_check(
        report,
        check_id=f"{table}.p4_populated",
        met=count >= 1,
        detail=f"{table} rows={count}, required>=1 for P4 closure",
        code=code,
        lane=lane,
        count=count,
        threshold=1,
    )


def _add_p4_identity_check(
    report: dict,
    cur: sqlite3.Cursor,
    *,
    table: str,
    check_id: str,
    columns: tuple[str, ...],
    lane: str,
) -> None:
    if not _table_exists(cur, table):
        _add_p4_check(
            report,
            check_id=check_id,
            met=False,
            detail=f"{table} table is missing",
            code="missing_table",
            lane=lane,
            count=0,
            threshold=0,
        )
        return
    existing_columns = _columns(cur, table)
    missing_columns = [column for column in columns if column not in existing_columns]
    if missing_columns:
        _add_p4_check(
            report,
            check_id=check_id,
            met=False,
            detail=f"{table} lacks required identity columns: " + ", ".join(missing_columns),
            code="p4_market_identity_columns_missing",
            lane=lane,
            count=len(missing_columns),
            threshold=0,
        )
        return
    missing_identity = _count(cur, table, _any_blank_sql(columns))
    _add_p4_check(
        report,
        check_id=check_id,
        met=missing_identity == 0,
        detail=f"{table} rows with incomplete market identity={missing_identity}",
        code="p4_market_identity_missing",
        lane=lane,
        count=missing_identity,
        threshold=0,
    )


def _add_p4_metric_layer_decision_check(
    report: dict,
    metric_layer_decision: str | None,
) -> None:
    normalized = (metric_layer_decision or "").strip().lower()
    met = normalized in P4_METRIC_LAYER_DECISIONS
    detail = (
        f"obs_v2 metric-layer decision={normalized}"
        if met
        else "obs_v2 metric-layer decision missing; expected instant-level or daily-aggregate"
    )
    _add_p4_check(
        report,
        check_id="p4.4_5_b.metric_layer_decision_present",
        met=met,
        detail=detail,
        code="p4_metric_layer_decision_missing",
        lane="4.5.B-full",
    )


def _add_p4_market_rule_acceptance_check(
    report: dict,
    *,
    paths: tuple[Path, ...],
) -> None:
    accepted_path, diagnostics = _first_accepted_json_artifact(
        paths,
        _market_rule_acceptance_errors,
    )
    _add_p4_check(
        report,
        check_id="p4.4_6_a.market_rule_acceptance_contract_present",
        met=accepted_path is not None,
        detail=(
            f"accepted market-rule contract at {accepted_path}"
            if accepted_path is not None
            else "market-rule acceptance contract missing or incomplete; "
            + "; ".join(diagnostics)
        ),
        code="p4_market_rule_acceptance_contract_missing",
        lane="4.6.A",
        count=1 if accepted_path is not None else 0,
        threshold=1,
    )


def _add_p4_tigge_manifest_check(
    report: dict,
    *,
    track: str,
    paths: tuple[Path, ...],
) -> None:
    accepted_path, diagnostics = _first_accepted_json_artifact(
        paths,
        lambda payload: _tigge_manifest_errors(payload, track=track),
    )
    _add_p4_check(
        report,
        check_id=f"p4.4_7.{track}_parity_manifest_present",
        met=accepted_path is not None,
        detail=(
            f"{track} accepted TIGGE parity manifest at {accepted_path}"
            if accepted_path is not None
            else f"{track} TIGGE parity/hash/timestamp manifest missing or incomplete; "
            + "; ".join(diagnostics)
        ),
        code="p4_tigge_manifest_missing",
        lane="4.7",
        count=1 if accepted_path is not None else 0,
        threshold=1,
    )


def _add_p4_scheduler_job_check(
    report: dict,
    *,
    scheduler_health_path: Path,
    job_name: str,
    check_id: str,
) -> None:
    payload, error = _load_json_object(scheduler_health_path)
    if error:
        _add_p4_check(
            report,
            check_id=check_id,
            met=False,
            detail=f"scheduler health artifact {scheduler_health_path} {error}",
            code="p4_scheduler_health_missing",
            lane="4.8",
        )
        return
    job = payload.get(job_name)
    if not isinstance(job, dict):
        _add_p4_check(
            report,
            check_id=check_id,
            met=False,
            detail=f"scheduler job {job_name} missing from {scheduler_health_path}",
            code="p4_scheduler_job_missing",
            lane="4.8",
        )
        return
    status = str(job.get("status") or "").upper()
    met = status == "OK"
    detail = (
        f"{job_name} status={status or 'missing'}; "
        f"last_success_at={job.get('last_success_at')}; "
        f"last_failure_reason={job.get('last_failure_reason')}"
    )
    _add_p4_check(
        report,
        check_id=check_id,
        met=met,
        detail=detail,
        code=f"p4_{job_name}_not_ok",
        lane="4.8",
    )


def _json_int_for_key(payload: object, keys: tuple[str, ...]) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if key in keys:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
        if isinstance(value, dict):
            nested = _json_int_for_key(value, keys)
            if nested is not None:
                return nested
    return None


def _add_p4_forecast_row_count_check(report: dict, *, evidence_path: Path) -> None:
    payload, error = _load_json_object(evidence_path)
    if error:
        _add_p4_check(
            report,
            check_id="p4.4_8.k2_forecasts_daily_row_count_verified",
            met=False,
            detail=f"k2_forecasts_daily row-count evidence {evidence_path} {error}",
            code="p4_forecast_row_count_evidence_missing",
            lane="4.8",
        )
        return
    assert payload is not None
    status = _json_text_for_key(payload, ("status",))
    row_count = _json_int_for_key(
        payload,
        ("row_count", "rows", "rows_written", "forecast_rows", "rows_inserted"),
    )
    status_ok = not status or status.upper() == "OK"
    met = status_ok and row_count is not None and row_count >= 1
    detail = (
        f"k2_forecasts_daily row-count evidence status={status or 'unspecified'} "
        f"row_count={row_count}"
    )
    _add_p4_check(
        report,
        check_id="p4.4_8.k2_forecasts_daily_row_count_verified",
        met=met,
        detail=detail,
        code="p4_forecast_row_count_not_verified",
        lane="4.8",
        count=row_count if row_count is not None else 0,
        threshold=1,
    )


def _add_p4_runtime_checks(
    report: dict,
    *,
    env: dict[str, str],
    scheduler_health_path: Path,
    forecast_row_count_evidence_path: Path,
    auto_pause_tombstone_path: Path,
    status_summary_path: Path,
) -> None:
    wu_key_present = bool((env.get("WU_API_KEY") or "").strip())
    _add_p4_check(
        report,
        check_id="p4.4_8.wu_api_key_present",
        met=wu_key_present,
        detail=(
            "WU_API_KEY present in supplied environment"
            if wu_key_present
            else "WU_API_KEY missing from supplied environment"
        ),
        code="p4_wu_api_key_missing",
        lane="4.8",
    )
    _add_p4_scheduler_job_check(
        report,
        scheduler_health_path=scheduler_health_path,
        job_name="k2_daily_obs",
        check_id="p4.4_8.k2_daily_obs_ok",
    )
    _add_p4_scheduler_job_check(
        report,
        scheduler_health_path=scheduler_health_path,
        job_name="k2_forecasts_daily",
        check_id="p4.4_8.k2_forecasts_daily_ok",
    )
    _add_p4_forecast_row_count_check(
        report,
        evidence_path=forecast_row_count_evidence_path,
    )

    tombstone_present = auto_pause_tombstone_path.exists()
    tombstone_detail = (
        "auto-pause tombstone absent"
        if not tombstone_present
        else "auto-pause tombstone present: "
        + auto_pause_tombstone_path.read_text(encoding="utf-8", errors="replace").strip()
    )
    _add_p4_check(
        report,
        check_id="p4.4_8.auto_pause_tombstone_absent",
        met=not tombstone_present,
        detail=tombstone_detail,
        code="p4_auto_pause_tombstone_present",
        lane="4.8",
    )

    payload, error = _load_json_object(status_summary_path)
    if error:
        _add_p4_check(
            report,
            check_id="p4.4_8.status_summary_infrastructure_green",
            met=False,
            detail=f"status summary {status_summary_path} {error}",
            code="p4_status_summary_unavailable",
            lane="4.8",
        )
        return
    risk = payload.get("risk") if payload else None
    if not isinstance(risk, dict):
        _add_p4_check(
            report,
            check_id="p4.4_8.status_summary_infrastructure_green",
            met=False,
            detail=f"status summary {status_summary_path} lacks risk object",
            code="p4_status_summary_infrastructure_missing",
            lane="4.8",
        )
        return
    infrastructure_level = str(risk.get("infrastructure_level") or "").upper()
    _add_p4_check(
        report,
        check_id="p4.4_8.status_summary_infrastructure_green",
        met=infrastructure_level == "GREEN",
        detail=f"status_summary risk.infrastructure_level={infrastructure_level or 'missing'}",
        code="p4_status_summary_infrastructure_not_green",
        lane="4.8",
    )


def _p4_lane_for_generic_blocker(blocker: dict) -> tuple[str, str] | None:
    code = str(blocker.get("code") or "")
    if code.startswith("p4_") and blocker.get("lane") in P4_ALLOWED_LANES:
        return code, str(blocker["lane"])
    table = str(blocker.get("table") or "")
    if code.startswith("observation_instants_v2") or code in {
        "payload_identity_missing",
        "missing_source_role_columns",
        "missing_reader_identity_columns",
    } or table == "observation_instants_v2":
        return "p4_observation_instants_reader_inputs_not_ready", "4.5.B-full"
    if code in {
        "empty_rebuild_eligible_snapshots",
        "ensemble_snapshots_v2.rebuild_input_unsafe",
        "ensemble_snapshots_v2.metric_scope_unsafe",
    } or table == "ensemble_snapshots_v2":
        return "p4_ensemble_snapshot_inputs_not_ready", "4.6.B"
    if code.startswith("calibration_pairs_v2") or code == "empty_platt_refit_bucket" or table == "calibration_pairs_v2":
        return "p4_calibration_pairs_not_refit_ready", "4.6.C"
    if code == "missing_table":
        return "p4_required_table_missing", "4.6.A"
    return None


def _normalize_p4_blockers(report: dict) -> None:
    normalized: dict[tuple[str, str, str], dict] = {}
    for blocker in report["blockers"]:
        mapped = _p4_lane_for_generic_blocker(blocker)
        if mapped is None:
            continue
        code, lane = mapped
        metric = str(blocker.get("temperature_metric") or "")
        key = (code, lane, metric)
        count = int(blocker.get("count") or 0)
        existing = normalized.get(key)
        if existing is None:
            entry = {"code": code, "lane": lane, "count": count}
            if metric:
                entry["temperature_metric"] = metric
            normalized[key] = entry
        else:
            existing["count"] = max(int(existing.get("count") or 0), count)
    report["blockers"] = list(normalized.values())


def build_p4_readiness_report(
    world_db: Path = SHARED_DB,
    *,
    state_dir: Path = STATE_DIR,
    env: dict[str, str] | None = None,
    market_rule_paths: tuple[Path, ...] | None = None,
    tigge_track_paths: dict[str, tuple[Path, ...]] | None = None,
    metric_layer_decision: str | None = None,
    scheduler_health_path: Path | None = None,
    forecast_row_count_evidence_path: Path | None = None,
    status_summary_path: Path | None = None,
    auto_pause_tombstone_path: Path | None = None,
) -> dict:
    """Return a read-only P4 readiness report for post-P3 blockers."""
    report = _new_report("p4-readiness", world_db)
    report["lanes"] = {
        "4.5.B-full": {"boot_profile": "hourly_observation_ingest"},
        "4.6.A": {"boot_profile": "settlement_semantics"},
        "4.6.B": {"boot_profile": "calibration"},
        "4.6.C": {"boot_profile": "calibration"},
        "4.7": {"boot_profile": "operator_tigge_rsync"},
        "4.8": {"boot_profile": "operator_runtime_posture"},
    }

    _add_p4_metric_layer_decision_check(report, metric_layer_decision)

    market_rule_candidates = market_rule_paths or DEFAULT_MARKET_RULE_PATHS
    _add_p4_market_rule_acceptance_check(
        report,
        paths=tuple(market_rule_candidates),
    )

    tigge_candidates = tigge_track_paths or DEFAULT_TIGGE_TRACK_PATHS
    for track, paths in tigge_candidates.items():
        _add_p4_tigge_manifest_check(
            report,
            track=track,
            paths=tuple(paths),
        )

    if not world_db.exists():
        _add_database_missing(report, world_db)
    else:
        uri = f"file:{world_db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        try:
            _add_p4_table_populated_check(
                report,
                cur,
                table="market_events_v2",
                lane="4.6.A",
                code="p4_market_events_v2_empty",
            )
            _add_p4_identity_check(
                report,
                cur,
                table="market_events_v2",
                check_id="market_events_v2.p4_market_identity_present",
                columns=MARKET_EVENTS_V2_MARKET_IDENTITY_COLUMNS,
                lane="4.6.A",
            )
            _add_p4_table_populated_check(
                report,
                cur,
                table="settlements_v2",
                lane="4.6.A",
                code="p4_settlements_v2_empty",
            )
            _add_p4_identity_check(
                report,
                cur,
                table="settlements_v2",
                check_id="settlements_v2.p4_market_identity_present",
                columns=SETTLEMENTS_V2_MARKET_IDENTITY_COLUMNS,
                lane="4.6.A",
            )
            _add_p4_table_populated_check(
                report,
                cur,
                table="ensemble_snapshots_v2",
                lane="4.6.B",
                code="p4_ensemble_snapshots_v2_empty",
            )
            _add_rebuild_snapshot_preflight_checks(report, cur)
            _add_observation_instants_safety_checks(report, cur)
            _add_p4_table_populated_check(
                report,
                cur,
                table="calibration_pairs_v2",
                lane="4.6.C",
                code="p4_calibration_pairs_v2_empty",
            )
            _add_platt_pair_preflight_checks(report, cur)
        finally:
            conn.close()

    effective_state_dir = state_dir
    _add_p4_runtime_checks(
        report,
        env=dict(os.environ if env is None else env),
        scheduler_health_path=scheduler_health_path or effective_state_dir / "scheduler_jobs_health.json",
        forecast_row_count_evidence_path=(
            forecast_row_count_evidence_path
            or effective_state_dir / "k2_forecasts_daily_row_count.json"
        ),
        auto_pause_tombstone_path=(
            auto_pause_tombstone_path
            or effective_state_dir / "auto_pause_failclosed.tombstone"
        ),
        status_summary_path=status_summary_path or effective_state_dir / "status_summary.json",
    )
    _normalize_p4_blockers(report)
    return _finalize_report(report)


def build_calibration_pair_rebuild_preflight_report(world_db: Path = SHARED_DB) -> dict:
    """Return a read-only input preflight for calibration_pairs_v2 rebuilds.

    This is narrower than full training readiness: it checks only upstream
    rebuild inputs and intentionally does not require calibration_pairs_v2,
    platt_models_v2, market_events_v2, market_price_history, or settlements_v2.
    """
    report = _new_report("calibration-pair-rebuild-preflight", world_db)
    if not world_db.exists():
        _add_database_missing(report, world_db)
        return report

    uri = f"file:{world_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    cur = conn.cursor()
    try:
        _add_rebuild_snapshot_preflight_checks(report, cur)
        _add_rebuild_observation_preflight_checks(report, cur)
        _add_observation_instants_safety_checks(report, cur)
    finally:
        conn.close()

    return _finalize_report(report)


def build_platt_refit_preflight_report(world_db: Path = SHARED_DB) -> dict:
    """Return a read-only input preflight for platt_models_v2 refits.

    This validates calibration_pairs_v2 as the refit input and intentionally
    does not require platt_models_v2 to already be populated.
    """
    report = _new_report("platt-refit-preflight", world_db)
    if not world_db.exists():
        _add_database_missing(report, world_db)
        return report

    uri = f"file:{world_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    cur = conn.cursor()
    try:
        # Phase 2 schema gate (2026-05-05): both calibration tables MUST have
        # stratification columns post-migration. If absent, refit will crash on
        # OperationalError: no such column. Stage6 fails closed here with a
        # specific verdict so watcher's stage7 never fires against a regressed schema.
        required_strat_cols = ("cycle", "source_id", "horizon_profile")
        for table in ("calibration_pairs_v2", "platt_models_v2"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            missing = [c for c in required_strat_cols if c not in cols]
            if missing:
                return {
                    "ready": False,
                    "verdict": "aborted_phase2_migration_unapplied",
                    "reason": f"{table} missing Phase 2 columns: {missing}",
                    "table": table,
                    "missing_columns": missing,
                }

        # Verify platt_models_v2 UNIQUE includes stratification keys (post-migration shape).
        # Check the UNIQUE clause text specifically — the column names also appear as
        # column definitions so a full-sql substring search is insufficient.
        platt_create_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='platt_models_v2'"
        ).fetchone()
        if platt_create_sql:
            sql_lower = (platt_create_sql[0] or "").lower()
            unique_match = re.search(r"unique\s*\(([^)]+)\)", sql_lower)
            unique_cols_text = unique_match.group(1) if unique_match else ""
            # Extended UNIQUE must include cycle + source_id + horizon_profile
            if not all(k in unique_cols_text for k in ("cycle", "source_id", "horizon_profile")):
                return {
                    "ready": False,
                    "verdict": "aborted_platt_unique_not_extended",
                    "reason": "platt_models_v2 UNIQUE missing stratification keys; legacy 6-tuple still in place",
                }

        _add_platt_pair_preflight_checks(report, cur)
    finally:
        conn.close()

    return _finalize_report(report)


def build_training_readiness_report(world_db: Path = SHARED_DB) -> dict:
    """Return a read-only P0 training-readiness report for the world DB.

    P0 intentionally fails closed. This command proves the canonical data spine
    is populated and eligible before any later calibration/replay phase may
    claim readiness.
    """
    report: dict[str, object] = {
        "mode": "training-readiness",
        "database": str(world_db),
        "status": NOT_READY,
        "ready": False,
        "checks": {},
        "blockers": [],
    }

    if not world_db.exists():
        report["checks"]["database_exists"] = _check_entry(
            check_id="database_exists",
            status=FAIL,
            detail=f"{world_db} not found",
            met=False,
        )
        report["blockers"].append(
            {"code": "missing_database", "table": None, "count": 0}
        )
        return report

    uri = f"file:{world_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    cur = conn.cursor()
    try:
        for table in (
            "forecasts",
            "historical_forecasts_v2",
            "ensemble_snapshots_v2",
            "calibration_pairs_v2",
            "platt_models_v2",
            "market_events_v2",
            "market_price_history",
            "settlements_v2",
        ):
            _add_count_min_check(report, cur, table=table)
        for table in ("observation_instants_v2", "observations"):
            _add_count_min_check(
                report,
                cur,
                table=table,
                empty_code="empty_required_table",
            )

        # Full training-readiness must inherit the stricter per-metric preflight
        # predicates; raw table presence cannot certify unsafe inputs.
        _add_rebuild_snapshot_preflight_checks(report, cur)
        _add_platt_pair_preflight_checks(report, cur)

        checks = report["checks"]
        blockers = report["blockers"]

        _add_required_identity_check(
            report,
            cur,
            table="settlements_v2",
            check_id="settlements_v2.market_identity_present",
            columns=("city", "target_date", "temperature_metric", "market_slug"),
            empty_code="null_market_slug",
        )
        _add_required_identity_check(
            report,
            cur,
            table="market_events_v2",
            check_id="market_events_v2.market_identity_present",
            columns=(
                "market_slug",
                "condition_id",
                "token_id",
                "city",
                "target_date",
                "temperature_metric",
            ),
        )
        _add_required_identity_check(
            report,
            cur,
            table="market_price_history",
            check_id="market_price_history.market_identity_present",
            columns=("market_slug", "token_id"),
        )
        _add_legacy_settlement_evidence_checks(report, cur)

        if _table_exists(cur, "observation_instants_v2"):
            columns = _columns(cur, "observation_instants_v2")
            if {"training_allowed", "source_role"}.issubset(columns):
                quoted_roles = ", ".join(
                    f"'{role}'" for role in sorted(ELIGIBLE_OBSERVATION_SOURCE_ROLES)
                )
                eligible_count = _count(
                    cur,
                    "observation_instants_v2",
                    f"""
                    COALESCE(training_allowed, 0) = 1
                    AND source_role IN ({quoted_roles})
                    """,
                )
                eligible_met = eligible_count >= 1
                checks["observation_instants_v2.training_eligible_present"] = _check_entry(
                    check_id="observation_instants_v2.training_eligible_present",
                    status=PASS if eligible_met else FAIL,
                    detail=(
                        "training-eligible observation_instants_v2 rows="
                        f"{eligible_count}, required>=1"
                    ),
                    count=eligible_count,
                    threshold=1,
                    met=eligible_met,
                )
                if not eligible_met:
                    blockers.append(
                        {
                            "code": "empty_training_eligible_observations",
                            "table": "observation_instants_v2",
                            "count": eligible_count,
                        }
                    )
                count = _count(
                    cur,
                    "observation_instants_v2",
                    f"""
                    COALESCE(training_allowed, 0) = 1
                    AND (
                        source_role IS NULL
                        OR source_role = ''
                        OR source_role NOT IN ({quoted_roles})
                    )
                    """,
                )
                detail = (
                    "training-allowed observation_instants_v2 rows with unsafe "
                    f"source_role={count}"
                )
                code = "observation_instants_v2.training_role_unsafe"
            else:
                count = -1
                detail = "observation_instants_v2 lacks training_allowed/source_role columns"
                code = "missing_source_role_columns"
                checks["observation_instants_v2.training_eligible_present"] = _check_entry(
                    check_id="observation_instants_v2.training_eligible_present",
                    status=FAIL,
                    detail=detail,
                    count=0,
                    threshold=1,
                    met=False,
                )
                blockers.append(
                    {
                        "code": "empty_training_eligible_observations",
                        "table": "observation_instants_v2",
                        "count": 0,
                    }
                )
            met = count == 0
            checks["observation_instants_v2.training_role_unsafe"] = _check_entry(
                check_id="observation_instants_v2.training_role_unsafe",
                status=PASS if met else FAIL,
                detail=detail,
                count=max(count, 0),
                threshold=0,
                met=met,
            )
            if not met:
                blockers.append(
                    {
                        "code": code,
                        "table": "observation_instants_v2",
                        "count": max(count, 0),
                    }
                )
        else:
            _add_missing_table_check(
                report,
                check_id="observation_instants_v2.training_role_unsafe",
                table="observation_instants_v2",
                detail="observation_instants_v2 table is missing",
            )

        if _table_exists(cur, "observation_instants_v2"):
            columns = _columns(cur, "observation_instants_v2")
            if {"training_allowed", "causality_status"}.issubset(columns):
                count = _count(
                    cur,
                    "observation_instants_v2",
                    """
                    COALESCE(training_allowed, 0) = 1
                    AND (
                        causality_status IS NULL
                        OR TRIM(CAST(causality_status AS TEXT)) = ''
                        OR UPPER(TRIM(CAST(causality_status AS TEXT))) != 'OK'
                    )
                    """,
                )
                detail = (
                    "training-allowed observation_instants_v2 rows with unsafe "
                    f"causality_status={count}"
                )
            elif "training_allowed" in columns:
                count = _count(
                    cur,
                    "observation_instants_v2",
                    "COALESCE(training_allowed, 0) = 1",
                )
                detail = (
                    "observation_instants_v2 lacks causality_status column for "
                    f"training_allowed rows={count}"
                )
            else:
                count = 0
                detail = "observation_instants_v2 lacks training_allowed/causality_status columns"
            met = count == 0
            checks["observation_instants_v2.causality_unsafe"] = _check_entry(
                check_id="observation_instants_v2.causality_unsafe",
                status=PASS if met else FAIL,
                detail=detail,
                count=count,
                threshold=0,
                met=met,
            )
            if not met:
                blockers.append(
                    {
                        "code": "observation_instants_v2.causality_unsafe",
                        "table": "observation_instants_v2",
                        "count": count,
                    }
                )
        else:
            _add_missing_table_check(
                report,
                check_id="observation_instants_v2.causality_unsafe",
                table="observation_instants_v2",
                detail="observation_instants_v2 table is missing",
            )

        _add_payload_identity_check(report, cur)
        _add_observation_reader_identity_check(report, cur)

        if _table_exists(cur, "ensemble_snapshots_v2"):
            columns = _columns(cur, "ensemble_snapshots_v2")
            missing_time_predicates = []
            for column in ("issue_time", "available_at", "fetch_time"):
                if column in columns:
                    missing_time_predicates.append(f"{column} IS NULL OR {column} = ''")
                else:
                    missing_time_predicates.append("1=1")
            count = _count(cur, "ensemble_snapshots_v2", " OR ".join(missing_time_predicates))
            met = count == 0
            checks["ensemble_snapshots_v2.issue_time_present"] = _check_entry(
                check_id="ensemble_snapshots_v2.issue_time_present",
                status=PASS if met else FAIL,
                detail=f"ensemble_snapshots_v2 rows missing issue/available/fetch time={count}",
                count=count,
                threshold=0,
                met=met,
            )
            if not met:
                blockers.append(
                    {
                        "code": "missing_issue_time",
                        "table": "ensemble_snapshots_v2",
                        "count": count,
                    }
                )
        else:
            _add_missing_table_check(
                report,
                check_id="ensemble_snapshots_v2.issue_time_present",
                table="ensemble_snapshots_v2",
                detail="ensemble_snapshots_v2 table is missing",
            )

        if _table_exists(cur, "historical_forecasts_v2"):
            columns = _columns(cur, "historical_forecasts_v2")
            predicates = []
            if "data_version" in columns:
                predicates.append("LOWER(COALESCE(data_version, '')) LIKE '%reconstruct%'")
            if "provenance_json" in columns:
                predicates.append("LOWER(COALESCE(provenance_json, '')) LIKE '%reconstruct%'")
            if "available_at" in columns:
                predicates.append("available_at IS NULL OR available_at = ''")
            where = " OR ".join(predicates) if predicates else "1=1"
            count = _count(cur, "historical_forecasts_v2", where)
            met = count == 0
            checks["historical_forecasts_v2.available_at_not_reconstructed"] = _check_entry(
                check_id="historical_forecasts_v2.available_at_not_reconstructed",
                status=PASS if met else FAIL,
                detail=f"historical_forecasts_v2 rows with missing/reconstructed available_at={count}",
                count=count,
                threshold=0,
                met=met,
            )
            if not met:
                blockers.append(
                    {
                        "code": "reconstructed_available_at",
                        "table": "historical_forecasts_v2",
                        "count": count,
                    }
                )
        else:
            _add_missing_table_check(
                report,
                check_id="historical_forecasts_v2.available_at_not_reconstructed",
                table="historical_forecasts_v2",
                detail="historical_forecasts_v2 table is missing",
            )

        if _table_exists(cur, "observations"):
            columns = _columns(cur, "observations")
            if "authority" in columns:
                verified_count = _count(cur, "observations", "authority = 'VERIFIED'")
                verified_met = verified_count >= 1
                verified_detail = f"VERIFIED observations rows={verified_count}, required>=1"
                verified_code = "empty_verified_observations"
            else:
                verified_count = 0
                verified_met = False
                verified_detail = "observations lacks authority column"
                verified_code = "missing_observation_authority_column"
            checks["observations.verified_present"] = _check_entry(
                check_id="observations.verified_present",
                status=PASS if verified_met else FAIL,
                detail=verified_detail,
                count=verified_count,
                threshold=1,
                met=verified_met,
            )
            if not verified_met:
                blockers.append(
                    {
                        "code": verified_code,
                        "table": "observations",
                        "count": verified_count,
                    }
                )

            if "authority" in columns and "provenance_metadata" in columns:
                provenance_where = _blank_or_empty_json_sql("provenance_metadata")
                where = f"authority = 'VERIFIED' AND {provenance_where}"
                wu_where = (
                    "authority = 'VERIFIED' "
                    "AND LOWER(COALESCE(source, '')) LIKE 'wu%' "
                    f"AND {provenance_where}"
                )
                missing_provenance_columns = False
            elif "authority" in columns and {"high_provenance_metadata", "low_provenance_metadata"}.issubset(columns):
                provenance_where = _any_blank_sql(
                    ("high_provenance_metadata", "low_provenance_metadata")
                )
                where = """
                    authority = 'VERIFIED'
                    AND ({provenance_where})
                """
                where = where.format(provenance_where=provenance_where)
                wu_where = (
                    "authority = 'VERIFIED' "
                    "AND LOWER(COALESCE(source, '')) LIKE 'wu%' "
                    f"AND ({provenance_where})"
                )
                missing_provenance_columns = False
            else:
                where = "1 = 0"
                wu_where = "1 = 0"
                missing_provenance_columns = True
            count = _count(cur, "observations", where)
            met = count == 0 and not missing_provenance_columns
            detail = f"VERIFIED observations without provenance={count}"
            code = "observations.verified_without_provenance"
            if missing_provenance_columns:
                detail = "observations lacks provenance metadata columns"
                code = "missing_observation_provenance_columns"
            checks["observations.provenance_present"] = _check_entry(
                check_id="observations.provenance_present",
                status=PASS if met else FAIL,
                detail=detail,
                count=count,
                threshold=0,
                met=met,
            )
            if not met:
                blockers.append(
                    {
                        "code": code,
                        "table": "observations",
                        "count": count,
                    }
                )
            if not missing_provenance_columns and "source" in columns:
                wu_count = _count(cur, "observations", wu_where)
                wu_met = wu_count == 0
                checks["observations.wu_provenance_present"] = _check_entry(
                    check_id="observations.wu_provenance_present",
                    status=PASS if wu_met else FAIL,
                    detail=f"WU VERIFIED observations without provenance={wu_count}",
                    count=wu_count,
                    threshold=0,
                    met=wu_met,
                )
                if not wu_met:
                    blockers.append(
                        {
                            "code": "observations.wu_empty_provenance",
                            "table": "observations",
                            "count": wu_count,
                        }
                    )
        else:
            _add_missing_table_check(
                report,
                check_id="observations.provenance_present",
                table="observations",
                detail="observations table is missing",
            )
    finally:
        conn.close()

    ready = not report["blockers"]
    report["ready"] = ready
    report["status"] = READY if ready else NOT_READY
    return report


def run_training_readiness(*, world_db: Path = SHARED_DB, json_output: bool = False) -> int:
    report = build_training_readiness_report(world_db)
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Training readiness:")
        for check_id, check in report["checks"].items():
            print(f"  [{check['status']}] {check_id}: {check['detail']}")
        print()
        print(f"RESULT: {report['status']}")
    return 0 if report["ready"] else 1


def run_calibration_pair_rebuild_preflight(
    *,
    world_db: Path = SHARED_DB,
    json_output: bool = False,
) -> int:
    report = build_calibration_pair_rebuild_preflight_report(world_db)
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Calibration-pair rebuild preflight:")
        for check_id, check in report["checks"].items():
            print(f"  [{check['status']}] {check_id}: {check['detail']}")
        print()
        print(f"RESULT: {report['status']}")
    return 0 if report["ready"] else 1


def run_platt_refit_preflight(
    *,
    world_db: Path = SHARED_DB,
    json_output: bool = False,
) -> int:
    report = build_platt_refit_preflight_report(world_db)
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Platt-refit preflight:")
        for check_id, check in report["checks"].items():
            print(f"  [{check['status']}] {check_id}: {check['detail']}")
        print()
        print(f"RESULT: {report['status']}")
    return 0 if report["ready"] else 1


def run_p4_readiness(
    *,
    world_db: Path = SHARED_DB,
    json_output: bool = False,
    market_rule_paths: tuple[Path, ...] | None = None,
    tigge_track_paths: dict[str, tuple[Path, ...]] | None = None,
    metric_layer_decision: str | None = None,
    forecast_row_count_evidence_path: Path | None = None,
) -> int:
    report = build_p4_readiness_report(
        world_db,
        market_rule_paths=market_rule_paths,
        tigge_track_paths=tigge_track_paths,
        metric_layer_decision=metric_layer_decision,
        forecast_row_count_evidence_path=forecast_row_count_evidence_path,
    )
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("P4 readiness:")
        for check_id, check in report["checks"].items():
            print(f"  [{check['status']}] {check_id}: {check['detail']}")
        print()
        print(f"RESULT: {report['status']}")
    return 0 if report["ready"] else 1


# ---------------------------------------------------------------------------
# Individual checks — all accept a sqlite3.Cursor for the main zeus.db
# ---------------------------------------------------------------------------

def check_1_position_current_vs_trade_decisions(cur) -> tuple[str, str]:
    """position_current rows should equal trade_decisions WHERE status='entered'.

    Join is via trade_decisions.runtime_trade_id = position_current.trade_id (both UUIDs).
    trade_decisions.trade_id is an integer PK — not the join key.
    """
    try:
        pc_count = _scalar(cur, "SELECT COUNT(*) FROM position_current")
        td_entered = _scalar(cur, "SELECT COUNT(*) FROM trade_decisions WHERE status='entered'")
        # How many entered decisions have a canonical position_current row?
        matched = _scalar(
            cur,
            """
            SELECT COUNT(*) FROM trade_decisions td
            JOIN position_current pc ON pc.trade_id = td.runtime_trade_id
            WHERE td.status='entered'
            """,
        )
        td_null_rtid = _scalar(
            cur,
            "SELECT COUNT(*) FROM trade_decisions WHERE status='entered' AND runtime_trade_id IS NULL",
        )
    except sqlite3.OperationalError as exc:
        return FAIL, f"query error: {exc}"

    if pc_count is None or td_entered is None:
        return FAIL, f"table missing — position_current={pc_count}, trade_decisions entered={td_entered}"

    unmatched = td_entered - (matched or 0)
    status = PASS if unmatched == 0 else FAIL
    return (
        status,
        f"trade_decisions[entered]={td_entered}, matched_in_position_current={matched}, "
        f"unmatched={unmatched} (null runtime_trade_id={td_null_rtid}), position_current_total={pc_count}",
    )


def check_2_position_events_coverage(cur) -> tuple[str, str]:
    """Every position_current position must have at least one position_events row."""
    try:
        pc_count = _scalar(cur, "SELECT COUNT(*) FROM position_current")
    except sqlite3.OperationalError as exc:
        return FAIL, f"query error: {exc}"

    if not pc_count:
        return WARN, "position_current is empty — nothing to verify"

    try:
        orphaned = _scalar(
            cur,
            """
            SELECT COUNT(*) FROM position_current pc
            WHERE NOT EXISTS (
                SELECT 1 FROM position_events pe WHERE pe.position_id = pc.position_id
            )
            """,
        )
    except sqlite3.OperationalError as exc:
        return FAIL, f"query error: {exc}"

    if orphaned is None:
        return FAIL, "position_events table missing or query failed"

    status = PASS if orphaned == 0 else FAIL
    return status, f"position_current={pc_count}, missing event coverage={orphaned}"


def check_3_json_active_vs_position_current(cur) -> tuple[str, str]:
    """positions.json active count must match position_current count."""
    if not POSITIONS_JSON.exists():
        return FAIL, f"{POSITIONS_JSON} not found"

    try:
        data = json.loads(POSITIONS_JSON.read_text())
    except Exception as exc:
        return FAIL, f"JSON parse error: {exc}"

    positions = data.get("positions", {})
    if isinstance(positions, dict):
        active_json = sum(
            1 for p in positions.values()
            if isinstance(p, dict) and p.get("status") not in ("exited", "closed", "settled")
        )
    elif isinstance(positions, list):
        active_json = len([
            p for p in positions
            if isinstance(p, dict) and p.get("status") not in ("exited", "closed", "settled")
        ])
    else:
        active_json = 0

    try:
        pc_count = _scalar(cur, "SELECT COUNT(*) FROM position_current")
    except sqlite3.OperationalError as exc:
        return FAIL, f"query error: {exc}"

    status = PASS if active_json == pc_count else WARN
    return status, f"positions.json active={active_json}, position_current={pc_count}"


def check_4_status_summary_risk_details() -> tuple[str, str]:
    """status_summary.json risk.details must not be None/null."""
    if not STATUS_JSON.exists():
        return FAIL, f"{STATUS_JSON} not found"

    try:
        data = json.loads(STATUS_JSON.read_text())
    except Exception as exc:
        return FAIL, f"JSON parse error: {exc}"

    risk = data.get("risk")
    if risk is None:
        return FAIL, "risk key missing from status_summary"

    details = risk.get("details")
    status = PASS if details is not None else FAIL
    detail_summary = list(details.keys()) if isinstance(details, dict) else type(details).__name__
    return status, f"risk.details={detail_summary}"


def check_5_settlements_after_mar30(shared_cur) -> tuple[str, str]:
    """settlements table must have entries for target_date > '2026-03-30'."""
    try:
        count = _scalar(
            shared_cur,
            "SELECT COUNT(*) FROM settlements WHERE target_date > '2026-03-30'",
        )
    except sqlite3.OperationalError as exc:
        return FAIL, f"query error: {exc}"

    if count is None:
        return FAIL, "settlements table missing or query failed"

    status = PASS if count > 0 else FAIL
    return status, f"settlements with target_date > 2026-03-30: {count}"


def check_6_risk_state_truth_source() -> tuple[str, str]:
    """risk_state-paper.db latest entry must not have portfolio_truth_source='working_state_fallback'."""
    if not RISK_DB.exists():
        return FAIL, f"{RISK_DB} not found"

    try:
        rconn = sqlite3.connect(str(RISK_DB))
        rcur = rconn.cursor()
        row = _row(
            rcur,
            "SELECT details_json, checked_at FROM risk_state ORDER BY checked_at DESC LIMIT 1",
        )
        rconn.close()
    except Exception as exc:
        return FAIL, f"DB error: {exc}"

    if row is None:
        return FAIL, "risk_state table is empty"

    details_json, checked_at = row
    if not details_json:
        return FAIL, f"details_json is NULL (checked_at={checked_at})"

    try:
        details = json.loads(details_json)
    except Exception as exc:
        return FAIL, f"details_json parse error: {exc}"

    truth_source = details.get("portfolio_truth_source", "MISSING")
    status = PASS if truth_source not in ("working_state_fallback", "CANONICAL_AUTHORITY_UNAVAILABLE") else FAIL
    return status, f"portfolio_truth_source={truth_source!r} (checked_at={checked_at})"


def check_7_fact_tables_populated(cur) -> tuple[str, str]:
    """outcome_fact and execution_fact must each have > 0 rows."""
    try:
        outcome = _scalar(cur, "SELECT COUNT(*) FROM outcome_fact")
        execution = _scalar(cur, "SELECT COUNT(*) FROM execution_fact")
    except sqlite3.OperationalError as exc:
        return FAIL, f"query error: {exc}"

    if outcome is None or execution is None:
        return FAIL, f"table missing — outcome_fact={outcome}, execution_fact={execution}"

    status = PASS if outcome > 0 and execution > 0 else FAIL
    return status, f"outcome_fact={outcome}, execution_fact={execution}"


def check_8_no_stale_entered_decisions(cur) -> tuple[str, str]:
    """No position_current rows should have target_date before today (stale open positions)."""
    try:
        count = _scalar(
            cur,
            """
            SELECT COUNT(*)
            FROM position_current
            WHERE target_date < ?
              AND phase IN ('pending_entry', 'active', 'day0_window', 'pending_exit')
            """,
            TODAY,
        )
    except sqlite3.OperationalError as exc:
        return FAIL, f"query error: {exc}"

    if count is None:
        return FAIL, "position_current table missing or query failed"

    status = PASS if count == 0 else FAIL
    return status, f"open position_current rows with target_date < {TODAY}: {count}"


# ---------------------------------------------------------------------------

def run_checks() -> int:
    if not DEFAULT_TRADE_DB.exists():
        print(f"FATAL: {DEFAULT_TRADE_DB} not found")
        return 1
    if not SHARED_DB.exists():
        print(f"FATAL: {SHARED_DB} not found")
        return 1

    conn = sqlite3.connect(str(DEFAULT_TRADE_DB))
    cur = conn.cursor()
    shared_conn = sqlite3.connect(str(SHARED_DB))
    shared_cur = shared_conn.cursor()

    checks = [
        ("1. position_current == trade_decisions[entered]",
         check_1_position_current_vs_trade_decisions(cur)),
        ("2. position_events covers all position_current",
         check_2_position_events_coverage(cur)),
        ("3. positions.json active == position_current",
         check_3_json_active_vs_position_current(cur)),
        ("4. status_summary risk.details not None",
         check_4_status_summary_risk_details()),
        ("5. settlements has entries after 2026-03-30",
         check_5_settlements_after_mar30(shared_cur)),
        ("6. risk_state truth_source != working_state_fallback",
         check_6_risk_state_truth_source()),
        ("7. outcome_fact and execution_fact populated",
         check_7_fact_tables_populated(cur)),
        ("8. no stale entered decisions before today",
         check_8_no_stale_entered_decisions(cur)),
    ]

    conn.close()
    shared_conn.close()

    any_fail = False
    for label, (status, detail) in checks:
        icon = {"PASS": "v", "FAIL": "X", "WARN": "~"}.get(status, "?")
        print(f"  [{status}] {icon} {label}")
        print(f"         {detail}")
        if status == FAIL:
            any_fail = True

    print()
    if any_fail:
        print("RESULT: DEGRADED — one or more checks failed.")
        return 1
    else:
        print("RESULT: OK — all truth surface checks passed.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=(
            "truth-surfaces",
            "training-readiness",
            "calibration-pair-rebuild-preflight",
            "platt-refit-preflight",
            "p4-readiness",
        ),
        default="truth-surfaces",
        help="Diagnostic mode to run.",
    )
    parser.add_argument(
        "--world-db",
        type=Path,
        default=SHARED_DB,
        help="World DB path for training-readiness mode.",
    )
    parser.add_argument(
        "--obs-v2-metric-layer-decision",
        choices=sorted(P4_METRIC_LAYER_DECISIONS),
        default=None,
        help="Operator-approved obs_v2 metric-layer decision for p4-readiness.",
    )
    parser.add_argument(
        "--market-rule-path",
        type=Path,
        action="append",
        default=None,
        help="Accepted market-rule artifact path for p4-readiness; may repeat.",
    )
    parser.add_argument(
        "--tigge-mx2t6-path",
        type=Path,
        action="append",
        default=None,
        help="Local mx2t6 TIGGE artifact path for p4-readiness; may repeat.",
    )
    parser.add_argument(
        "--tigge-mn2t6-path",
        type=Path,
        action="append",
        default=None,
        help="Local mn2t6 TIGGE artifact path for p4-readiness; may repeat.",
    )
    parser.add_argument(
        "--forecast-row-count-evidence-path",
        type=Path,
        default=None,
        help="Explicit k2_forecasts_daily row-count evidence JSON for p4-readiness.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON where supported.",
    )
    args = parser.parse_args()

    if args.mode == "training-readiness":
        sys.exit(run_training_readiness(world_db=args.world_db, json_output=args.json))
    if args.mode == "calibration-pair-rebuild-preflight":
        sys.exit(
            run_calibration_pair_rebuild_preflight(
                world_db=args.world_db,
                json_output=args.json,
            )
        )
    if args.mode == "platt-refit-preflight":
        sys.exit(run_platt_refit_preflight(world_db=args.world_db, json_output=args.json))
    if args.mode == "p4-readiness":
        tigge_paths = None
        if args.tigge_mx2t6_path or args.tigge_mn2t6_path:
            tigge_paths = {
                "mx2t6_high": tuple(args.tigge_mx2t6_path or ()),
                "mn2t6_low": tuple(args.tigge_mn2t6_path or ()),
            }
        sys.exit(
            run_p4_readiness(
                world_db=args.world_db,
                json_output=args.json,
                market_rule_paths=(
                    tuple(args.market_rule_path)
                    if args.market_rule_path
                    else None
                ),
                tigge_track_paths=tigge_paths,
                metric_layer_decision=args.obs_v2_metric_layer_decision,
                forecast_row_count_evidence_path=args.forecast_row_count_evidence_path,
            )
        )
    sys.exit(run_checks())
