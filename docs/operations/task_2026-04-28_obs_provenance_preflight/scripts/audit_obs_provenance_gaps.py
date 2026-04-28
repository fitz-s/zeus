#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
"""P0 diagnostic — count rows that fire each of the 5 OBS-side preflight gates.

This is READ-ONLY. It mirrors the predicates in
``scripts/verify_truth_surfaces.py::build_calibration_pair_rebuild_preflight_report``
without touching the live preflight runner (which raises on NOT_READY).

Output: per-gate count + per-(city, source) breakdown JSON.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Match the schema's metric → provenance column resolution
# (mirrors verify_truth_surfaces.py:1339-1349 _observation_provenance_column).
# Live canonical DB has singular 'provenance_metadata'; the verify_truth_surfaces
# resolver prefers singular when present, falls back to split per-metric columns.
def _resolve_provenance_col(conn) -> dict[str, str]:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(observations)").fetchall()}
    if "provenance_metadata" in cols:
        return {"high": "provenance_metadata", "low": "provenance_metadata"}
    return {"high": "high_provenance_metadata", "low": "low_provenance_metadata"}


OBS_COLS = {
    "high": "high_temp",
    "low": "low_temp",
}


def gate_3_verified_without_provenance(conn: sqlite3.Connection, metric: str) -> dict:
    """`observations.verified_without_provenance` — VERIFIED rows with NULL/empty provenance."""
    obs_col = OBS_COLS[metric]
    prov_col = _resolve_provenance_col(conn)[metric]
    rows = conn.execute(
        f"""
        SELECT city, source, COUNT(*) AS n
        FROM observations
        WHERE authority='VERIFIED'
          AND {obs_col} IS NOT NULL
          AND ({prov_col} IS NULL OR TRIM({prov_col})='' OR {prov_col}='{{}}')
        GROUP BY city, source
        ORDER BY 3 DESC
        """
    ).fetchall()
    total = sum(r[2] for r in rows)
    return {
        "gate": "observations.verified_without_provenance",
        "metric": metric,
        "total": total,
        "by_city_source": [{"city": r[0], "source": r[1], "n": r[2]} for r in rows[:30]],
    }


def gate_4_wu_empty_provenance(conn: sqlite3.Connection, metric: str) -> dict:
    """`observations.wu_empty_provenance` — sub-slice of #3 for WU sources."""
    obs_col = OBS_COLS[metric]
    prov_col = _resolve_provenance_col(conn)[metric]
    n = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM observations
        WHERE authority='VERIFIED'
          AND {obs_col} IS NOT NULL
          AND ({prov_col} IS NULL OR TRIM({prov_col})='' OR {prov_col}='{{}}')
          AND LOWER(source) LIKE 'wu%'
        """
    ).fetchone()[0]
    return {
        "gate": "observations.wu_empty_provenance",
        "metric": metric,
        "total": n,
    }


def gate_2_hko_requires_fresh_audit(conn: sqlite3.Connection, metric: str) -> dict:
    """`observations.hko_requires_fresh_source_audit` — every HKO/HK VERIFIED row blocks."""
    obs_col = OBS_COLS[metric]
    n = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM observations
        WHERE authority='VERIFIED'
          AND {obs_col} IS NOT NULL
          AND (LOWER(source) LIKE 'hko%' OR LOWER(city) IN ('hong kong','hong_kong','hk','hkg'))
        """
    ).fetchone()[0]
    return {
        "gate": "observations.hko_requires_fresh_source_audit",
        "metric": metric,
        "total": n,
        "note": "operator-blocked: no promotion mechanism in code today (Gate 2)",
    }


def gate_1_training_role_unsafe(conn: sqlite3.Connection) -> dict:
    """`observation_instants_v2.training_role_unsafe` — training_allowed=1 AND source_role!=historical_hourly."""
    try:
        rows = conn.execute(
            """
            SELECT source_role, COUNT(*) AS n
            FROM observation_instants_v2
            WHERE COALESCE(training_allowed, 0) = 1
              AND source_role NOT IN ('historical_hourly')
            GROUP BY source_role
            ORDER BY 2 DESC
            """
        ).fetchall()
    except sqlite3.OperationalError as e:
        return {
            "gate": "observation_instants_v2.training_role_unsafe",
            "total": None,
            "error": str(e),
        }
    total = sum(r[1] for r in rows)
    return {
        "gate": "observation_instants_v2.training_role_unsafe",
        "total": total,
        "by_source_role": [{"source_role": r[0], "n": r[1]} for r in rows],
    }


def gate_5_payload_identity_missing(conn: sqlite3.Connection) -> dict:
    """`payload_identity_missing` — observation_instants_v2 rows lacking payload identity in provenance_json."""
    # Mirror the EXACT predicate from
    # scripts/verify_truth_surfaces.py::_obs_v2_provenance_identity_missing_sql:
    #   required_missing = OR(payload_hash blank, parser_version blank)
    #   source_missing   = AND(source_url blank, source_file blank)   ← ALL blank
    #   station_missing  = AND(station_id blank, station_registry_version blank,
    #                          station_registry_hash blank)            ← ALL blank
    # Earlier draft used OR for station — that was over-strict and
    # produced false positives. Fixed 2026-04-28 to match production logic.
    try:
        n = conn.execute(
            """
            SELECT COUNT(*)
            FROM observation_instants_v2
            WHERE COALESCE(training_allowed, 0) = 1
              AND (
                json_extract(provenance_json, '$.payload_hash') IS NULL
                OR json_extract(provenance_json, '$.parser_version') IS NULL
                OR (
                    json_extract(provenance_json, '$.source_url') IS NULL
                    AND json_extract(provenance_json, '$.source_file') IS NULL
                )
                OR (
                    json_extract(provenance_json, '$.station_id') IS NULL
                    AND json_extract(provenance_json, '$.station_registry_version') IS NULL
                    AND json_extract(provenance_json, '$.station_registry_hash') IS NULL
                )
              )
            """
        ).fetchone()[0]
    except sqlite3.OperationalError as e:
        return {
            "gate": "payload_identity_missing",
            "total": None,
            "error": str(e),
        }
    return {
        "gate": "payload_identity_missing",
        "total": n,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="OBS provenance gap diagnostic (READ-ONLY)")
    p.add_argument("--db-path", required=True, help="path to zeus-world.db (read-only mode)")
    p.add_argument("--out", default=None, help="optional JSON output path")
    args = p.parse_args()

    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": args.db_path,
        "gates": {},
    }
    report["gates"]["high"] = {
        "gate_3": gate_3_verified_without_provenance(conn, "high"),
        "gate_4": gate_4_wu_empty_provenance(conn, "high"),
        "gate_2": gate_2_hko_requires_fresh_audit(conn, "high"),
    }
    report["gates"]["low"] = {
        "gate_3": gate_3_verified_without_provenance(conn, "low"),
        "gate_4": gate_4_wu_empty_provenance(conn, "low"),
        "gate_2": gate_2_hko_requires_fresh_audit(conn, "low"),
    }
    report["gates"]["instants_v2"] = {
        "gate_1": gate_1_training_role_unsafe(conn),
        "gate_5": gate_5_payload_identity_missing(conn),
    }
    conn.close()

    # Print compact summary
    print(f"=== OBS provenance gap diagnostic — {args.db_path} ===")
    for metric, sub in report["gates"].items():
        for k, v in sub.items():
            print(f"  [{metric}] {v.get('gate', k)}: {v.get('total')}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2, default=str))
        print(f"\n[ok] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
