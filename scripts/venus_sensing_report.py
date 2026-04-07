#!/usr/bin/env python3
"""Venus sensing report generator.

Collects ALL truth surface data Venus needs into one JSON file.
Four layers: diagnostics, truth_surfaces, consistency, deltas.

Usage:
    python scripts/venus_sensing_report.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import STATE_DIR, state_path
from src.contracts.reality_contracts_loader import load_contracts

# Paths
ZEUS_DB = STATE_DIR / "zeus.db"
RISK_DB = state_path("risk_state.db")
POSITIONS_JSON = state_path("positions.json")
REPORT_PATH = STATE_DIR / "venus_sensing_report.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _scalar(cur: sqlite3.Cursor, sql: str, *params) -> object:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def _safe_query(conn: sqlite3.Connection, sql: str, params=()) -> list[sqlite3.Row]:
    """Execute query, return empty list on missing table or error."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


# ── Layer 1: Diagnostics ──────────────────────────────────────────────────

def _collect_diagnostics() -> dict:
    """Import and run diagnose_truth_surfaces.py checks."""
    try:
        from scripts.diagnose_truth_surfaces import diagnose
        return diagnose()
    except Exception as exc:
        return {"_error": str(exc)}


# ── Layer 2: Truth Surfaces ───────────────────────────────────────────────

def _collect_trade_decisions(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "trade_decisions"):
        return {"_error": "table missing"}

    total = _scalar(conn.cursor(), "SELECT COUNT(*) FROM trade_decisions") or 0
    by_status = {}
    for row in _safe_query(conn, "SELECT status, COUNT(*) as cnt FROM trade_decisions GROUP BY status"):
        by_status[row[0]] = row[1]

    oldest_entered = _scalar(
        conn.cursor(),
        "SELECT MIN(timestamp) FROM trade_decisions WHERE status='entered'",
    )
    newest = _scalar(conn.cursor(), "SELECT MAX(timestamp) FROM trade_decisions")

    return {
        "total": total,
        "by_status": by_status,
        "oldest_entered": oldest_entered,
        "newest": newest,
    }


def _collect_position_current(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "position_current"):
        return {"_error": "table missing"}

    count = _scalar(conn.cursor(), "SELECT COUNT(*) FROM position_current") or 0
    latest = _scalar(conn.cursor(), "SELECT MAX(updated_at) FROM position_current")
    phases = {}
    for row in _safe_query(conn, "SELECT phase, COUNT(*) as cnt FROM position_current GROUP BY phase"):
        phases[row[0]] = row[1]

    return {
        "count": count,
        "latest_updated_at": latest,
        "phases": phases,
    }


def _collect_positions_json() -> dict:
    if not POSITIONS_JSON.exists():
        return {"_error": f"{POSITIONS_JSON.name} not found"}

    try:
        data = json.loads(POSITIONS_JSON.read_text())
    except Exception as exc:
        return {"_error": f"parse error: {exc}"}

    positions = data.get("positions", data.get("active_positions", []))
    states: dict[str, int] = {}
    for p in positions:
        s = p.get("state", "unknown")
        states[s] = states.get(s, 0) + 1

    active = sum(1 for p in positions if p.get("state", "") in ("entered", "day0_window", "pending_entry", "active"))
    exited = sum(1 for p in positions if p.get("state", "") in ("economically_closed", "exited", "settled"))

    return {
        "active_count": active,
        "exit_count": exited,
        "total_count": len(positions),
        "states": states,
    }


def _collect_settlements(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "settlements"):
        return {"_error": "table missing"}

    total = _scalar(conn.cursor(), "SELECT COUNT(*) FROM settlements") or 0
    latest_target = _scalar(conn.cursor(), "SELECT MAX(target_date) FROM settlements")
    latest_settled = _scalar(conn.cursor(), "SELECT MAX(settled_at) FROM settlements")

    return {
        "total": total,
        "latest_target_date": latest_target,
        "latest_settled_at": latest_settled,
    }


def _collect_risk_state() -> dict:
    if not RISK_DB.exists():
        return {"_error": f"{RISK_DB.name} not found"}

    try:
        rconn = sqlite3.connect(str(RISK_DB))
        rconn.row_factory = sqlite3.Row
        row = rconn.execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        rconn.close()
    except Exception as exc:
        return {"_error": str(exc)}

    if not row:
        return {"_error": "risk_state table empty"}

    details = json.loads(row["details_json"]) if row["details_json"] else {}
    return {
        "level": row["level"],
        "portfolio_truth_source": details.get("portfolio_truth_source", "UNKNOWN"),
        "realized_pnl": details.get("realized_pnl"),
        "unrealized_pnl": details.get("unrealized_pnl"),
        "total_pnl": details.get("total_pnl"),
        "settlement_sample_size": details.get("settlement_sample_size"),
        "brier_level": details.get("brier_level"),
        "portfolio_fallback_active": details.get("portfolio_fallback_active"),
    }


def _collect_reality_contracts() -> dict:
    try:
        contracts = load_contracts()
    except Exception as exc:
        return {"_error": str(exc)}

    total = len(contracts)
    blocking_total = sum(1 for c in contracts if c.criticality == "blocking")
    blocking_stale = sum(1 for c in contracts if c.must_reverify)
    stale = [c.contract_id for c in contracts if c.is_stale]
    return {
        "total": total,
        "blocking_total": blocking_total,
        "blocking_stale": blocking_stale,
        "stale": stale,
        "stale_count": len(stale),
        "blocking_ok": blocking_stale == 0,  # no blocking contracts need reverification
        "freshness_ok": len(stale) == 0,     # no contracts of any criticality are stale
    }


def _collect_fact_tables(conn: sqlite3.Connection) -> dict:
    outcome = 0
    execution = 0
    if _table_exists(conn, "outcome_fact"):
        outcome = _scalar(conn.cursor(), "SELECT COUNT(*) FROM outcome_fact") or 0
    if _table_exists(conn, "execution_fact"):
        execution = _scalar(conn.cursor(), "SELECT COUNT(*) FROM execution_fact") or 0
    return {"outcome_fact": outcome, "execution_fact": execution}


def _collect_truth_surfaces(conn: sqlite3.Connection) -> dict:
    return {
        "trade_decisions": _collect_trade_decisions(conn),
        "position_current": _collect_position_current(conn),
        "positions_json": _collect_positions_json(),
        "settlements": _collect_settlements(conn),
        "risk_state": _collect_risk_state(),
        "reality_contracts": _collect_reality_contracts(),
        "fact_tables": _collect_fact_tables(conn),
    }


# ── Layer 3: Consistency ──────────────────────────────────────────────────

def _collect_consistency(conn: sqlite3.Connection, surfaces: dict) -> dict:
    td = surfaces.get("trade_decisions", {})
    pc = surfaces.get("position_current", {})
    pj = surfaces.get("positions_json", {})

    # trade_decisions entered vs position_current count
    td_entered = td.get("by_status", {}).get("entered", 0)
    pc_count = pc.get("count", 0)
    td_vs_pc = {"td_entered": td_entered, "pc_count": pc_count, "gap": td_entered - pc_count}

    # position_current vs positions-paper.json active count
    pj_active = pj.get("active_count", 0)
    pc_vs_json = {"pc": pc_count, "json_active": pj_active, "match": pc_count == pj_active}

    # Ghost positions (entered with past target dates)
    ghost_count = 0
    oldest_ghost_target = None
    try:
        from scripts.diagnose_truth_surfaces import check_ghost_positions
        ghost_cur = conn.cursor()
        ghost_result = check_ghost_positions(ghost_cur)
        ghost_count = int(ghost_result.get("evidence", "0").replace("ghost_count=", ""))
    except Exception:
        pass

    # Settlement gap hours
    settlement_gap_hours = None
    settlements = surfaces.get("settlements", {})
    latest_settled = settlements.get("latest_settled_at")
    if latest_settled:
        try:
            from scripts.diagnose_truth_surfaces import _parse_ts
            dt = _parse_ts(latest_settled)
            if dt:
                settlement_gap_hours = round((_utcnow() - dt).total_seconds() / 3600, 1)
        except Exception:
            pass

    # Canonical path alive: position_current is fresher than the latest trade_decisions entry
    canonical_path_alive = True
    pc_latest = pc.get("latest_updated_at")
    td_newest = td.get("newest")
    if pc_latest and td_newest:
        try:
            from scripts.diagnose_truth_surfaces import _parse_ts
            pc_dt = _parse_ts(pc_latest)
            td_dt = _parse_ts(td_newest)
            if pc_dt and td_dt:
                canonical_path_alive = pc_dt >= td_dt
        except Exception:
            pass

    return {
        "td_entered_vs_pc": td_vs_pc,
        "pc_vs_json_active": pc_vs_json,
        "ghost_positions": {"count": ghost_count},
        "settlement_gap_hours": settlement_gap_hours,
        "canonical_path_alive": canonical_path_alive,
    }


# ── Layer 4: Deltas ───────────────────────────────────────────────────────

def _collect_deltas(surfaces: dict) -> dict:
    """Compute changes since last report."""
    if not REPORT_PATH.exists():
        return {"_note": "no previous report — first run"}

    try:
        prev = json.loads(REPORT_PATH.read_text())
    except Exception:
        return {"_note": "previous report unreadable"}

    prev_surfaces = prev.get("truth_surfaces", {})

    def _get(d: dict, *keys, default=0):
        for k in keys:
            d = d.get(k, {}) if isinstance(d, dict) else {}
        return d if d != {} else default

    prev_td = prev_surfaces.get("trade_decisions", {})
    prev_settlements = prev_surfaces.get("settlements", {})
    prev_risk = prev_surfaces.get("risk_state", {})

    curr_td = surfaces.get("trade_decisions", {})
    curr_settlements = surfaces.get("settlements", {})
    curr_risk = surfaces.get("risk_state", {})

    # New entries/exits
    prev_entered = prev_td.get("by_status", {}).get("entered", 0)
    curr_entered = curr_td.get("by_status", {}).get("entered", 0)
    prev_exited = prev_td.get("by_status", {}).get("exited", 0)
    curr_exited = curr_td.get("by_status", {}).get("exited", 0)

    # Settlements
    prev_settle_count = prev_settlements.get("total", 0)
    curr_settle_count = curr_settlements.get("total", 0)

    # PnL
    prev_pnl = prev_risk.get("total_pnl") or 0
    curr_pnl = curr_risk.get("total_pnl") or 0

    # Risk level
    prev_level = prev_risk.get("level", "")
    curr_level = curr_risk.get("level", "")

    return {
        "new_entries_since_last": max(0, curr_entered - prev_entered),
        "new_exits_since_last": max(0, curr_exited - prev_exited),
        "new_settlements_since_last": max(0, curr_settle_count - prev_settle_count),
        "pnl_change": round((curr_pnl or 0) - (prev_pnl or 0), 4),
        "risk_level_changed": prev_level != curr_level,
        "previous_report_at": prev.get("generated_at"),
    }


# ── Main ──────────────────────────────────────────────────────────────────

def _collect_relationship_checks() -> dict:
    """Run cross-module relationship checks.

    Imports run_relationship_checks() from tests/test_cross_module_relationships.py.
    Each check verifies that data flowing from Module A to Module B preserves
    the critical property (P&L agreement, count consistency, flag propagation, etc.).
    """
    try:
        from tests.test_cross_module_relationships import run_relationship_checks
        return run_relationship_checks()
    except Exception as exc:
        return {"_error": str(exc)}


def generate_sensing_report() -> dict:
    """Generate the full sensing report (diagnostics, surfaces, consistency, relationship_checks, deltas)."""
    generated_at = _utcnow().isoformat()

    # Open zeus.db
    try:
        conn = sqlite3.connect(str(ZEUS_DB))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        return {"generated_at": generated_at, "_error": f"cannot open zeus.db: {exc}"}

    try:
        diagnostics = _collect_diagnostics()
        surfaces = _collect_truth_surfaces(conn)
        consistency = _collect_consistency(conn, surfaces)
        relationship_checks = _collect_relationship_checks()
        deltas = _collect_deltas(surfaces)
    finally:
        conn.close()

    return {
        "generated_at": generated_at,
        "diagnostics": diagnostics,
        "truth_surfaces": surfaces,
        "consistency": consistency,
        "relationship_checks": relationship_checks,
        "deltas": deltas,
    }


def write_report() -> Path:
    """Generate report and atomically write to state/venus_sensing_report.json."""
    report = generate_sensing_report()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(REPORT_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(report, f, indent=2, default=str)
        os.replace(tmp, str(REPORT_PATH))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return REPORT_PATH


def main():
    path = write_report()
    report = json.loads(path.read_text())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
