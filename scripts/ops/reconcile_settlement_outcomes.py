#!/usr/bin/env python3
# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: standing read-only reconciliation net between money-truth (zeus_trades.db
#   position_current) and forecast-truth (zeus-forecasts.db settlement_outcomes) — the
#   check that would have caught the 16 chain-settled positions silently missing their
#   settlement_outcomes row. Root cause: the harvester_pnl_resolver VENUE_RESOLVED
#   settlement route settles positions in zeus_trades.db only and, by explicit module
#   invariant, never writes forecast settlement truth (harvester_pnl_resolver.py:12).
#   This is ONLY the reconciliation net promoted from the W13 F2 probe to a standing
#   check. The durable outbox producer/consumer that would close the gap at the source
#   is DEFERRED (lives in money-hot.db, which does not exist yet — operator-gated W4);
#   this script does not build any outbox table.
# Authority: docs/operations/current/plans/db_first_principles_audit_2026-07-20/
#   implementation/atomicity_outbox_contract.md §3.5.
"""Anti-join settled positions against settlement_outcomes. Exit 0 clean, 1 on a real gap.

Strictly read-only: two independent read-only connections (trades, forecasts), app-side
anti-join in Python. Never ATTACHes one DB to the other, never opens either read-write.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&cache=private", uri=True, timeout=0.25, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=250")
    conn.execute("PRAGMA mmap_size=0")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _settled_positions(conn: sqlite3.Connection) -> list[dict]:
    """Every terminally-settled position with a realized settlement price.

    settlement_authority is observation only — it is NOT a position_current column
    (verified 2026-07-21 against both the CREATE TABLE in src/state/db.py:5482 and the
    live DB schema; the atomicity_outbox_contract.md §3.5 query's column list assumed it
    was). The real value lives inside the latest SETTLED position_events row's
    payload_json (src/engine/lifecycle_events.py:913), so it is a best-effort correlated
    lookup keyed off the covering index idx_position_events_position_type_sequence
    (position_id, event_type, sequence_no DESC). Degrades to NULL if position_events
    is absent — it never blocks the anti-join itself.
    """
    has_events = _table_exists(conn, "position_events")
    authority_expr = (
        "(SELECT json_extract(pe.payload_json, '$.settlement_authority') FROM position_events pe "
        " WHERE pe.position_id = pc.position_id AND pe.event_type = 'SETTLED' "
        " ORDER BY pe.sequence_no DESC LIMIT 1)"
        if has_events else "NULL"
    )
    rows = conn.execute(f"""
        SELECT pc.city, pc.target_date, COALESCE(pc.temperature_metric, 'high') AS metric,
               COALESCE(pc.trade_id, pc.position_id) AS trade_id, pc.settled_at,
               {authority_expr} AS settlement_authority
        FROM position_current pc
        WHERE pc.phase = 'settled' AND pc.settlement_price IS NOT NULL
    """).fetchall()
    cols = ("city", "target_date", "metric", "trade_id", "settled_at", "settlement_authority")
    return [dict(zip(cols, r)) for r in rows]


def _settled_outcome_keys(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    rows = conn.execute("SELECT city, target_date, temperature_metric FROM settlement_outcomes").fetchall()
    return {(city, date, metric) for city, date, metric in rows}


def find_gaps(trades_conn: sqlite3.Connection, forecasts_conn: sqlite3.Connection) -> list[dict]:
    """The §3.5 anti-join, app-side. Rows in position_current(settled) with no matching
    settlement_outcomes row on (city, target_date, metric)."""
    have = _settled_outcome_keys(forecasts_conn)
    return [row for row in _settled_positions(trades_conn)
            if (row["city"], row["target_date"], row["metric"]) not in have]


def _state_dir(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg)
    try:
        from src.state.db_paths import primary_trade_db_path
        return primary_trade_db_path().parent
    except Exception:
        return ROOT / "state"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile settled positions against settlement_outcomes (read-only).")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--max-gap", type=int, default=0,
                     help="Gap count above which the gate fails. A brief same-cycle settlement "
                          "lag (trades-side settle lands before the forecasts-side truth row) "
                          "can make a small transient gap legitimate; default 0.")
    a = ap.parse_args(argv)
    state = _state_dir(a.state_dir)

    trades_path = state / "zeus_trades.db"
    forecasts_path = state / "zeus-forecasts.db"
    if not trades_path.exists() or not forecasts_path.exists():
        print(f"SKIP — DB file absent (trades={trades_path.exists()}, forecasts={forecasts_path.exists()}).")
        return 0

    trades_conn = _ro_connect(trades_path)
    forecasts_conn = _ro_connect(forecasts_path)
    try:
        if not _table_exists(trades_conn, "position_current") or not _table_exists(forecasts_conn, "settlement_outcomes"):
            print("SKIP — position_current or settlement_outcomes table absent.")
            return 0
        gaps = find_gaps(trades_conn, forecasts_conn)
    finally:
        trades_conn.close()
        forecasts_conn.close()

    total = len(gaps)
    print(f"settled_without_outcome_total={total}")
    for g in gaps:
        print(f"  [{g['city']}] {g['target_date']}  metric={g['metric']}  trade_id={g['trade_id']}  "
              f"settled_at={g['settled_at']}  settlement_authority={g['settlement_authority']}")

    if total > a.max_gap:
        print(f"GATE FAIL — {total} settled position(s) with no settlement_outcomes row "
              f"(threshold --max-gap={a.max_gap}).")
        return 1
    print("GATE OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
