#!/usr/bin/env python3
"""
Diagnostic script: verify canonical truth surface consistency.

Checks LIVE state of zeus.db, risk_state-paper.db, and JSON state files.
NOT pytest — run directly to get PASS/FAIL for each surface invariant.

Usage:
    python scripts/verify_truth_surfaces.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import STATE_DIR

DEFAULT_TRADE_DB = STATE_DIR / "zeus-paper.db"
SHARED_DB = STATE_DIR / "zeus-shared.db"
RISK_DB = STATE_DIR / "risk_state-paper.db"
POSITIONS_JSON = STATE_DIR / "positions-paper.json"
STATUS_JSON = STATE_DIR / "status_summary-paper.json"

TODAY = date.today().isoformat()

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


def _scalar(cur, sql, *params):
    cur.execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None


def _row(cur, sql, *params):
    cur.execute(sql, params)
    return cur.fetchone()


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
    """positions-paper.json active count must match position_current count."""
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
    """status_summary-paper.json risk.details must not be None/null."""
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
    sys.exit(run_checks())
