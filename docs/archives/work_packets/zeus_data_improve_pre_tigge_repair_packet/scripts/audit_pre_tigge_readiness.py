#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3


def exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        report = {
            "probability_trace_fact": conn.execute("SELECT COUNT(*) FROM probability_trace_fact").fetchone()[0] if exists(conn, "probability_trace_fact") else None,
            "selection_family_fact": conn.execute("SELECT COUNT(*) FROM selection_family_fact").fetchone()[0] if exists(conn, "selection_family_fact") else None,
            "selection_hypothesis_fact": conn.execute("SELECT COUNT(*) FROM selection_hypothesis_fact").fetchone()[0] if exists(conn, "selection_hypothesis_fact") else None,
            "calibration_decision_group": conn.execute("SELECT COUNT(*) FROM calibration_decision_group").fetchone()[0] if exists(conn, "calibration_decision_group") else None,
            "forecast_error_profile": conn.execute("SELECT COUNT(*) FROM forecast_error_profile").fetchone()[0] if exists(conn, "forecast_error_profile") else None,
            "day0_residual_fact": conn.execute("SELECT COUNT(*) FROM day0_residual_fact").fetchone()[0] if exists(conn, "day0_residual_fact") else None,
            "model_eval_run": conn.execute("SELECT COUNT(*) FROM model_eval_run").fetchone()[0] if exists(conn, "model_eval_run") else None,
            "model_eval_point": conn.execute("SELECT COUNT(*) FROM model_eval_point").fetchone()[0] if exists(conn, "model_eval_point") else None,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
