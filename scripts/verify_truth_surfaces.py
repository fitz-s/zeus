#!/usr/bin/env python3
"""
Diagnostic script: verify canonical truth surface consistency.

Checks LIVE state of zeus.db, risk_state-live.db, and JSON state files.
NOT pytest — run directly to get PASS/FAIL for each surface invariant.

Usage:
    python scripts/verify_truth_surfaces.py
"""
# Lifecycle: created=2026-04-07; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Diagnose truth-surface integrity and P0 training-readiness blockers.
# Reuse: Inspect docs/operations/current_data_state.md and the active packet receipt before using as closeout evidence.

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import STATE_DIR

DEFAULT_TRADE_DB = STATE_DIR / "zeus_trades.db"
SHARED_DB = STATE_DIR / "zeus-world.db"
RISK_DB = STATE_DIR / "risk_state.db"
POSITIONS_JSON = STATE_DIR / "positions.json"
STATUS_JSON = STATE_DIR / "status_summary.json"

TODAY = date.today().isoformat()

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

READY = "READY"
NOT_READY = "NOT_READY"
ELIGIBLE_OBSERVATION_SOURCE_ROLES = frozenset({
    "settlement_truth",
    "historical_hourly",
})


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
                    "training-allowed observation_instants_v2 rows without an "
                    f"eligible source_role={count}"
                )
                code = "fallback_source_role"
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
            checks["observation_instants_v2.source_role_canonical"] = _check_entry(
                check_id="observation_instants_v2.source_role_canonical",
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
                check_id="observation_instants_v2.source_role_canonical",
                table="observation_instants_v2",
                detail="observation_instants_v2 table is missing",
            )

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
                where = "authority = 'VERIFIED' AND COALESCE(provenance_metadata, '') = ''"
                missing_provenance_columns = False
            elif "authority" in columns and {"high_provenance_metadata", "low_provenance_metadata"}.issubset(columns):
                where = """
                    authority = 'VERIFIED'
                    AND (
                        COALESCE(high_provenance_metadata, '') = ''
                        OR COALESCE(low_provenance_metadata, '') = ''
                    )
                """
                missing_provenance_columns = False
            else:
                where = "1 = 0"
                missing_provenance_columns = True
            count = _count(cur, "observations", where)
            met = count == 0 and not missing_provenance_columns
            detail = f"VERIFIED observations without provenance={count}"
            code = "empty_observation_provenance"
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
    return status, f"positions-paper.json active={active_json}, position_current={pc_count}"


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
        ("3. positions-paper.json active == position_current",
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
        choices=("truth-surfaces", "training-readiness"),
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
        "--json",
        action="store_true",
        help="Emit machine-readable JSON where supported.",
    )
    args = parser.parse_args()

    if args.mode == "training-readiness":
        sys.exit(run_training_readiness(world_db=args.world_db, json_output=args.json))
    sys.exit(run_checks())
