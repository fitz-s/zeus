#!/usr/bin/env python3
# Lifecycle: created=2026-04-07; last_reviewed=2026-05-06; last_reused=2026-05-06
# Purpose: Diagnose truth-surface health without promoting legacy projections.
# Reuse: Use for read-only operator diagnostics; fact-table health is non-authoritative unless settlement authority fields say otherwise.
"""
Diagnostic script: comprehensive truth surface health checks.

Runs 8 diagnostic checks and outputs structured JSON results.

Usage:
    python scripts/diagnose_truth_surfaces.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import STATE_DIR
from scripts.verify_truth_surfaces import (
    build_fact_table_authority_report,
    format_fact_table_authority_detail,
    format_fact_table_authority_evidence,
)

DEFAULT_DB = STATE_DIR / "zeus.db"
RISK_DB = STATE_DIR / "risk_state-live.db"
POSITIONS_JSON = STATE_DIR / "positions.json"
STATUS_JSON = STATE_DIR / "status_summary.json"

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


def _scalar(cur, sql, *params):
    cur.execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_ts(ts_str):
    """Best-effort parse of ISO timestamp string to aware datetime."""
    if not ts_str:
        return None
    ts_str = str(ts_str).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def check_canonical_freshness(cur) -> dict:
    """Check 1: position_current MAX(updated_at) vs now. FAIL if >24h stale while new entries exist."""
    max_updated = _scalar(cur, "SELECT MAX(updated_at) FROM position_current")
    newest_td = _scalar(cur, "SELECT MAX(timestamp) FROM trade_decisions WHERE status='entered'")

    if max_updated is None:
        return {"status": FAIL, "evidence": "position_current is empty", "detail": "no rows"}

    dt = _parse_ts(max_updated)
    now = _now_utc()
    age_hours = (now - dt).total_seconds() / 3600 if dt else None

    has_newer_entries = False
    if newest_td and dt:
        td_dt = _parse_ts(newest_td)
        has_newer_entries = td_dt is not None and td_dt > dt

    stale = age_hours is not None and age_hours > 24 and has_newer_entries
    return {
        "status": FAIL if stale else PASS,
        "evidence": f"max_updated_at={max_updated}, age_hours={age_hours:.1f}" if age_hours else f"max_updated_at={max_updated}",
        "detail": f"newest_trade_decision={newest_td}, has_newer_entries={has_newer_entries}",
    }


def check_position_count_match(cur) -> dict:
    """Check 2: position_current count vs positions.json active count."""
    pc_count = _scalar(cur, "SELECT COUNT(*) FROM position_current") or 0

    if not POSITIONS_JSON.exists():
        return {"status": FAIL, "evidence": f"{POSITIONS_JSON} not found", "detail": ""}

    data = json.loads(POSITIONS_JSON.read_text())
    active = data.get("positions", data.get("active_positions", []))
    json_count = len(active)

    match = pc_count == json_count
    return {
        "status": PASS if match else FAIL,
        "evidence": f"position_current={pc_count}, positions_json_active={json_count}",
        "detail": f"delta={abs(pc_count - json_count)}",
    }


def check_ghost_positions(cur) -> dict:
    """Check 3: entered trade_decisions with target_date in the past (parsed from bin_label)."""
    today = date.today()
    cur.execute("SELECT trade_id, bin_label FROM trade_decisions WHERE status='entered'")
    ghost_count = 0
    ghost_ids = []
    for row in cur.fetchall():
        trade_id, bin_label = row
        date_m = re.search(r"on (\w+ \d+)\?", bin_label or "")
        if date_m:
            try:
                target = datetime.strptime(date_m.group(1) + ", 2026", "%B %d, %Y").date()
                if target < today:
                    ghost_count += 1
                    ghost_ids.append(trade_id)
            except ValueError:
                pass

    return {
        "status": FAIL if ghost_count > 0 else PASS,
        "evidence": f"ghost_count={ghost_count}",
        "detail": f"trade_ids={ghost_ids[:10]}{'...' if len(ghost_ids) > 10 else ''}",
    }


def check_settlement_harvester(cur) -> dict:
    """Check 4: latest settlement activity vs now. FAIL if >48h since last settlement.

    Checks decision_log (settlement artifacts) and calibration_pairs (harvester output)
    rather than the legacy settlements table which is a static legacy-predecessor import.
    """
    # Primary: decision_log settlement artifacts have the actual settled_at timestamp
    max_settled = _scalar(
        cur,
        "SELECT MAX(timestamp) FROM decision_log WHERE mode = 'settlement'",
    )
    # Secondary: calibration_pairs target_date shows the latest market that was harvested
    max_cal_target = _scalar(
        cur,
        "SELECT MAX(target_date) FROM calibration_pairs",
    )

    if max_settled is None and max_cal_target is None:
        return {"status": FAIL, "evidence": "no settlement activity found", "detail": "decision_log and calibration_pairs both empty"}

    dt = _parse_ts(max_settled) if max_settled else None
    now = _now_utc()
    age_hours = (now - dt).total_seconds() / 3600 if dt else None

    stale = age_hours is not None and age_hours > 48
    return {
        "status": FAIL if stale else PASS,
        "evidence": f"max_settled_at={max_settled}, age_hours={age_hours:.1f}" if age_hours else f"max_settled_at={max_settled}",
        "detail": f"latest_calibration_target_date={max_cal_target}",
    }


def check_portfolio_truth_source() -> dict:
    """Check 5: risk_state-live.db latest row portfolio_truth_source."""
    if not RISK_DB.exists():
        return {"status": FAIL, "evidence": f"{RISK_DB} not found", "detail": ""}

    conn = sqlite3.connect(str(RISK_DB))
    cur = conn.cursor()
    try:
        cur.execute("SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row or not row[0]:
            return {"status": FAIL, "evidence": "risk_state empty or no details_json", "detail": ""}

        details = json.loads(row[0])
        source = details.get("portfolio_truth_source", "UNKNOWN")
        is_fallback = source in ("working_state_fallback", "CANONICAL_AUTHORITY_UNAVAILABLE")
        return {
            "status": FAIL if is_fallback else PASS,
            "evidence": f"portfolio_truth_source={source}",
            "detail": f"portfolio_loader_status={details.get('portfolio_loader_status', 'UNKNOWN')}",
        }
    finally:
        conn.close()


def check_status_summary_completeness() -> dict:
    """Check 6: status_summary.json risk.details is not empty."""
    if not STATUS_JSON.exists():
        return {"status": FAIL, "evidence": f"{STATUS_JSON} not found", "detail": ""}

    try:
        data = json.loads(STATUS_JSON.read_text())
    except Exception as exc:
        return {"status": FAIL, "evidence": f"JSON parse error: {exc}", "detail": ""}

    risk = data.get("risk")
    if risk is None:
        return {"status": FAIL, "evidence": "risk key missing", "detail": ""}

    details = risk.get("details")
    if not details:
        return {"status": FAIL, "evidence": "risk.details is empty or None", "detail": ""}

    key_count = len(details) if isinstance(details, dict) else 0
    sample_keys = list(details.keys())[:5] if isinstance(details, dict) else []
    return {
        "status": PASS,
        "evidence": f"risk.details has {key_count} keys",
        "detail": f"sample_keys={sample_keys}",
    }


def check_fact_tables(cur) -> dict:
    """Check 7: classify fact-table counts without promoting outcome_fact."""
    report = build_fact_table_authority_report(cur)
    authority_ready = report["authority_status"] == "authority_ready"
    return {
        "status": PASS if authority_ready else WARN,
        "evidence": format_fact_table_authority_evidence(report),
        "detail": format_fact_table_authority_detail(report),
    }


def check_unfilled_ghosts(cur) -> dict:
    """Check 8: entered trade_decisions with NULL runtime_trade_id."""
    count = _scalar(
        cur,
        "SELECT COUNT(*) FROM trade_decisions WHERE status='entered' AND runtime_trade_id IS NULL",
    ) or 0

    return {
        "status": WARN if count > 0 else PASS,
        "evidence": f"unfilled_ghost_count={count}",
        "detail": "decisions logged but never materialized into positions" if count > 0 else "",
    }


def diagnose() -> dict:
    """Run all diagnostic checks and return structured results."""
    conn = sqlite3.connect(str(DEFAULT_DB))
    cur = conn.cursor()

    results = {}
    try:
        results["canonical_freshness"] = check_canonical_freshness(cur)
        results["position_count_match"] = check_position_count_match(cur)
        results["ghost_positions"] = check_ghost_positions(cur)
        results["settlement_harvester"] = check_settlement_harvester(cur)
        results["portfolio_truth_source"] = check_portfolio_truth_source()
        results["status_summary_completeness"] = check_status_summary_completeness()
        results["fact_tables"] = check_fact_tables(cur)
        results["unfilled_ghosts"] = check_unfilled_ghosts(cur)
    finally:
        conn.close()

    return results


def main():
    results = diagnose()

    fail_count = sum(1 for r in results.values() if r["status"] == FAIL)
    warn_count = sum(1 for r in results.values() if r["status"] == WARN)
    pass_count = sum(1 for r in results.values() if r["status"] == PASS)

    for name, result in results.items():
        icon = "v" if result["status"] == PASS else ("!" if result["status"] == WARN else "X")
        print(f"  [{result['status']}] {icon} {name}")
        print(f"         {result['evidence']}")
        if result["detail"]:
            print(f"         {result['detail']}")

    print()
    overall = "HEALTHY" if fail_count == 0 and warn_count == 0 else ("DEGRADED" if fail_count == 0 else "UNHEALTHY")
    print(f"RESULT: {overall} — {pass_count} pass, {warn_count} warn, {fail_count} fail")

    # Also output JSON to stdout for programmatic consumption
    print()
    print("--- JSON ---")
    print(json.dumps(results, indent=2))

    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
