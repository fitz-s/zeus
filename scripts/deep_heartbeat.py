"""Deep heartbeat diagnostics for Venus.

Goes beyond healthcheck.py's surface-level checks (daemon alive? status fresh?)
to audit *semantic correctness* of the trading system's internal state:

- Event table parity (canonical vs legacy exit events)
- Position lifecycle phase staleness
- Paper/live environment contamination
- Portfolio loader data-source integrity
- Trade activity liveness

Exit code 0 = all clear, 1 = anomalies detected, 2 = critical (trading integrity at risk).

Venus calls this during Layer 1 of the heartbeat loop.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import STATE_DIR, get_mode, mode_state_path


# Thresholds
PHASE_STALE_HOURS = 72  # position stuck at 'active' for > N hours → flag
TRADE_ACTIVITY_STALE_HOURS = 12  # no fills for > N hours with open positions → flag
STATUS_SUMMARY_MAX_AGE_SECONDS = 3600  # 1 hour


def _mode() -> str:
    return get_mode()


def _trade_db_path(mode: str | None = None) -> Path:
    mode = mode or _mode()
    return STATE_DIR / f"zeus-{mode}.db"


def _connect(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row and row[0] > 0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hours_ago(iso_ts: str | None) -> float | None:
    """Return hours since the given ISO timestamp, or None if unparseable."""
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return max(0.0, (_utc_now() - ts).total_seconds() / 3600)


# ---------------------------------------------------------------------------
# Check 1: Event Table Parity
# ---------------------------------------------------------------------------

def check_event_table_parity(conn: sqlite3.Connection) -> dict:
    """Compare canonical position_events exit events vs position_events_legacy exits.

    If legacy has EXIT events for positions that have NO canonical exit event,
    it means exits are being written to the wrong table.
    """
    result = {"check": "event_table_parity", "ok": True, "details": {}}

    has_canonical = _table_exists(conn, "position_events")
    has_legacy = _table_exists(conn, "position_events_legacy")

    result["details"]["canonical_table_exists"] = has_canonical
    result["details"]["legacy_table_exists"] = has_legacy

    if not has_legacy:
        result["details"]["note"] = "No legacy table — fully migrated"
        return result

    if not has_canonical:
        result["details"]["note"] = "No canonical table — pre-migration state"
        return result

    # Count canonical exit events
    canonical_exit_count = conn.execute(
        "SELECT COUNT(DISTINCT position_id) FROM position_events "
        "WHERE event_type IN ('ECONOMIC_CLOSE', 'SETTLEMENT_CONFIRMED')"
    ).fetchone()[0]

    # Count legacy exit events (position_state contains EXIT or CLOSED)
    legacy_exit_count = conn.execute(
        "SELECT COUNT(DISTINCT runtime_trade_id) FROM position_events_legacy "
        "WHERE position_state IN ('POSITION_EXIT_RECORDED', 'ECONOMICALLY_CLOSED', 'SETTLED')"
    ).fetchone()[0]

    result["details"]["canonical_exit_positions"] = canonical_exit_count
    result["details"]["legacy_exit_positions"] = legacy_exit_count

    # Find legacy exits with no canonical counterpart
    orphaned = conn.execute(
        """
        SELECT DISTINCT l.runtime_trade_id
        FROM position_events_legacy l
        WHERE l.position_state IN ('POSITION_EXIT_RECORDED', 'ECONOMICALLY_CLOSED', 'SETTLED')
          AND l.runtime_trade_id NOT IN (
            SELECT DISTINCT position_id FROM position_events
            WHERE event_type IN ('ECONOMIC_CLOSE', 'SETTLEMENT_CONFIRMED')
          )
        """
    ).fetchall()

    orphaned_ids = [row[0] for row in orphaned]
    result["details"]["orphaned_legacy_exits"] = len(orphaned_ids)

    if orphaned_ids:
        result["ok"] = False
        result["severity"] = "critical"
        result["details"]["orphaned_trade_ids"] = orphaned_ids[:20]  # cap output
        result["message"] = (
            f"{len(orphaned_ids)} exit event(s) written to legacy table only — "
            "canonical position_current won't be updated"
        )

    return result


# ---------------------------------------------------------------------------
# Check 2: Position Phase Staleness
# ---------------------------------------------------------------------------

def check_phase_staleness(conn: sqlite3.Connection) -> dict:
    """Flag positions stuck at 'active' phase beyond threshold."""
    result = {"check": "phase_staleness", "ok": True, "details": {}}

    if not _table_exists(conn, "position_current"):
        result["details"]["note"] = "position_current table missing"
        return result

    stale_rows = conn.execute(
        """
        SELECT position_id, phase, updated_at, city, target_date
        FROM position_current
        WHERE phase IN ('active', 'day0_window', 'pending_exit')
          AND updated_at < datetime('now', ? || ' hours')
        """,
        (f"-{PHASE_STALE_HOURS}",),
    ).fetchall()

    result["details"]["stale_active_count"] = len(stale_rows)

    if stale_rows:
        result["ok"] = False
        result["severity"] = "warning"
        stale_info = []
        for row in stale_rows[:20]:
            hours = _hours_ago(row["updated_at"])
            stale_info.append({
                "position_id": row["position_id"],
                "phase": row["phase"],
                "updated_at": row["updated_at"],
                "city": row["city"],
                "target_date": row["target_date"],
                "hours_stale": round(hours, 1) if hours else None,
            })
        result["details"]["stale_positions"] = stale_info
        result["message"] = (
            f"{len(stale_rows)} position(s) stuck at active phase for >{PHASE_STALE_HOURS}h"
        )

    return result


# ---------------------------------------------------------------------------
# Check 3: Environment Contamination
# ---------------------------------------------------------------------------

def check_env_contamination(conn: sqlite3.Connection) -> dict:
    """Verify all positions in the mode-specific DB have the correct env field."""
    result = {"check": "env_contamination", "ok": True, "details": {}}
    mode = _mode()
    result["details"]["expected_env"] = mode

    # Check position_current
    if _table_exists(conn, "position_current"):
        # position_current may not have an 'env' column — check schema
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
        }
        if "env" in columns:
            wrong_env = conn.execute(
                "SELECT position_id, phase, city FROM position_current WHERE env != ?",
                (mode,),
            ).fetchall()
            result["details"]["position_current_wrong_env"] = len(wrong_env)
            if wrong_env:
                result["ok"] = False
                result["severity"] = "critical"
                result["details"]["contaminated_positions"] = [
                    {"position_id": r["position_id"], "phase": r["phase"], "city": r["city"]}
                    for r in wrong_env[:20]
                ]
                result["message"] = (
                    f"{len(wrong_env)} position(s) in {mode} DB have wrong env field"
                )
        else:
            result["details"]["position_current_env_column"] = False

    # Check position_events_legacy
    if _table_exists(conn, "position_events_legacy"):
        wrong_legacy = conn.execute(
            "SELECT COUNT(*) FROM position_events_legacy WHERE env != ?",
            (mode,),
        ).fetchone()[0]
        result["details"]["legacy_events_wrong_env"] = wrong_legacy
        if wrong_legacy > 0 and result["ok"]:
            result["ok"] = False
            result["severity"] = "warning"
            result["message"] = (
                f"{wrong_legacy} legacy event(s) in {mode} DB have wrong env"
            )

    return result


# ---------------------------------------------------------------------------
# Check 4: Portfolio Loader Integrity
# ---------------------------------------------------------------------------

def check_portfolio_loader(conn: sqlite3.Connection) -> dict:
    """Verify the portfolio loader would return real data, not fall back to JSON.

    If position_current is empty but positions-{mode}.json has entries,
    the loader will use JSON fallback — which means canonical writes aren't working.
    """
    result = {"check": "portfolio_loader_integrity", "ok": True, "details": {}}
    mode = _mode()

    # Count canonical positions
    canonical_count = 0
    if _table_exists(conn, "position_current"):
        canonical_count = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE phase NOT IN ('economically_closed', 'settled', 'voided')"
        ).fetchone()[0]

    result["details"]["canonical_open_positions"] = canonical_count

    # Count JSON positions
    json_path = mode_state_path("positions.json")
    json_count = 0
    if json_path.exists():
        try:
            with open(json_path) as f:
                positions = json.load(f)
            if isinstance(positions, list):
                json_count = len(positions)
            elif isinstance(positions, dict):
                json_count = len(positions)
        except (json.JSONDecodeError, OSError):
            result["details"]["json_parse_error"] = True
    result["details"]["json_positions_count"] = json_count

    # Divergence = problem
    if canonical_count == 0 and json_count > 0:
        result["ok"] = False
        result["severity"] = "critical"
        result["message"] = (
            f"Canonical position_current is empty but JSON has {json_count} positions — "
            "exits may not be updating canonical table"
        )
    elif abs(canonical_count - json_count) > 3:
        result["ok"] = False
        result["severity"] = "warning"
        result["message"] = (
            f"Canonical ({canonical_count}) and JSON ({json_count}) position counts diverge by "
            f"{abs(canonical_count - json_count)}"
        )

    return result


# ---------------------------------------------------------------------------
# Check 5: Trade Activity Liveness
# ---------------------------------------------------------------------------

def check_trade_liveness() -> dict:
    """Alert if no new fills for extended period while positions are open."""
    result = {"check": "trade_activity_liveness", "ok": True, "details": {}}
    mode = _mode()

    status_path = mode_state_path("status_summary.json")
    if not status_path.exists():
        result["details"]["note"] = "status_summary missing"
        return result

    try:
        with open(status_path) as f:
            status = json.load(f)
    except (json.JSONDecodeError, OSError):
        result["details"]["note"] = "status_summary corrupt"
        return result

    # Check status summary age
    ts = status.get("timestamp")
    status_age_h = _hours_ago(ts)
    if status_age_h is not None:
        result["details"]["status_age_hours"] = round(status_age_h, 2)

    portfolio = status.get("portfolio", {})
    open_positions = portfolio.get("open_positions", 0)
    result["details"]["open_positions"] = open_positions

    # last_fill_at from execution section
    execution = status.get("execution", {})
    overall = execution.get("overall", execution) if isinstance(execution, dict) else {}
    last_fill_at = overall.get("last_fill_at") if isinstance(overall, dict) else None

    if last_fill_at:
        fill_age_h = _hours_ago(last_fill_at)
        if fill_age_h is not None:
            result["details"]["last_fill_hours_ago"] = round(fill_age_h, 2)
            if fill_age_h > TRADE_ACTIVITY_STALE_HOURS and open_positions > 0:
                result["ok"] = False
                result["severity"] = "warning"
                result["message"] = (
                    f"No fills for {fill_age_h:.1f}h with {open_positions} open position(s)"
                )
    elif open_positions > 0:
        result["details"]["last_fill_at"] = None
        result["ok"] = False
        result["severity"] = "warning"
        result["message"] = "No last_fill_at recorded but positions are open"

    # Check entries_paused
    control = status.get("control", {})
    if isinstance(control, dict) and control.get("entries_paused"):
        result["details"]["entries_paused"] = True

    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_diagnostics() -> dict:
    """Run all deep heartbeat checks and return structured results."""
    mode = _mode()
    now = _utc_now().isoformat()

    results = {
        "timestamp": now,
        "mode": mode,
        "checks": [],
        "ok": True,
        "critical_count": 0,
        "warning_count": 0,
    }

    db_path = _trade_db_path(mode)
    conn = _connect(db_path)

    if conn is None:
        results["ok"] = False
        results["error"] = f"Trade DB not found: {db_path}"
        return results

    try:
        checks = [
            check_event_table_parity(conn),
            check_phase_staleness(conn),
            check_env_contamination(conn),
            check_portfolio_loader(conn),
            check_trade_liveness(),
        ]
    finally:
        conn.close()

    for check in checks:
        results["checks"].append(check)
        if not check.get("ok", True):
            results["ok"] = False
            severity = check.get("severity", "warning")
            if severity == "critical":
                results["critical_count"] += 1
            else:
                results["warning_count"] += 1

    return results


def exit_code_for(results: dict) -> int:
    if results.get("ok"):
        return 0
    if results.get("critical_count", 0) > 0:
        return 2
    return 1


if __name__ == "__main__":
    results = run_diagnostics()
    print(json.dumps(results, indent=2))
    sys.exit(exit_code_for(results))
