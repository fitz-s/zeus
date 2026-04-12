#!/usr/bin/env python3
"""Backfill probability_trace_fact from existing opportunity_fact rows.

Runtime writes should come from `src.state.db.log_probability_trace_fact`.
This script is a conservative historical repair for decisions that already
exist in `opportunity_fact` before the trace writer was active.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection, init_schema


def _missing_reasons(row: sqlite3.Row) -> list[str]:
    missing = []
    if not row["snapshot_id"]:
        missing.append("decision_snapshot_id")
    for name in ("p_raw", "p_cal", "p_market"):
        if row[name] is None:
            missing.append(name)
    missing.append("vector_history_unavailable")
    return missing


def _trace_status(row: sqlite3.Row) -> str:
    missing = _missing_reasons(row)
    if "decision_snapshot_id" in missing:
        return "degraded_decision_context"
    return "degraded_missing_vectors"


def _posterior(row: sqlite3.Row) -> float | None:
    if row["p_cal"] is None or row["p_market"] is None:
        return None
    alpha = float(row["alpha"] or 0.0)
    return alpha * float(row["p_cal"]) + (1.0 - alpha) * float(row["p_market"])


def run_backfill(*, dry_run: bool = False) -> dict:
    conn = get_world_connection()
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM opportunity_fact
        WHERE decision_id IS NOT NULL
          AND decision_id != ''
        ORDER BY recorded_at
        """
    ).fetchall()
    pending = [
        row for row in rows
        if conn.execute(
            "SELECT 1 FROM probability_trace_fact WHERE decision_id = ?",
            (row["decision_id"],),
        ).fetchone() is None
    ]
    if not dry_run and pending:
        conn.executemany(
            """
            INSERT OR REPLACE INTO probability_trace_fact (
                trace_id, decision_id, decision_snapshot_id, candidate_id,
                city, target_date, range_label, direction, mode, strategy_key,
                discovery_mode, entry_method, selected_method, trace_status,
                missing_reason_json, bin_labels_json, p_raw_json, p_cal_json,
                p_market_json, p_posterior_json, p_posterior, alpha, agreement,
                n_edges_found, n_edges_after_fdr, rejection_stage,
                availability_status, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"backfill:{row['decision_id']}",
                    row["decision_id"],
                    row["snapshot_id"],
                    row["candidate_id"],
                    row["city"],
                    row["target_date"],
                    row["range_label"],
                    row["direction"] or "unknown",
                    None,
                    row["strategy_key"],
                    row["discovery_mode"],
                    row["entry_method"],
                    None,
                    _trace_status(row),
                    json.dumps(_missing_reasons(row), ensure_ascii=False),
                    None,
                    None if row["p_raw"] is None else json.dumps([float(row["p_raw"])]),
                    None if row["p_cal"] is None else json.dumps([float(row["p_cal"])]),
                    None if row["p_market"] is None else json.dumps([float(row["p_market"])]),
                    None,
                    _posterior(row),
                    row["alpha"],
                    None,
                    None,
                    None,
                    row["rejection_stage"],
                    row["availability_status"],
                    row["recorded_at"],
                )
                for row in pending
            ],
        )
        conn.commit()
    summary = {
        "dry_run": dry_run,
        "opportunity_rows": len(rows),
        "pending_trace_rows": len(pending),
        "trace_rows": conn.execute("SELECT COUNT(*) FROM probability_trace_fact").fetchone()[0],
    }
    conn.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_backfill(dry_run=args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
