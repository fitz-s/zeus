#!/usr/bin/env python3
"""Audit per-city data readiness for paper runtime and archive work."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name
from src.state.db import get_shared_connection, init_schema

SOURCE_DATA_ROOT = PROJECT_ROOT.parent / "51 source data"
TIGGE_MANIFEST = SOURCE_DATA_ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"
TIGGE_COVERAGE = SOURCE_DATA_ROOT / "tmp" / "tigge_coverage_gaps_latest.json"


def _fetch_one(conn: sqlite3.Connection, query: str, params: tuple = ()) -> sqlite3.Row:
    return conn.execute(query, params).fetchone()


def _count(conn: sqlite3.Connection, table: str, city: str) -> int:
    return int(_fetch_one(conn, f"SELECT COUNT(*) AS n FROM {table} WHERE city = ?", (city,))["n"] or 0)


def _date_bounds(conn: sqlite3.Connection, table: str, city: str, *, date_col: str = "target_date") -> dict:
    row = _fetch_one(
        conn,
        f"SELECT MIN({date_col}) AS min_date, MAX({date_col}) AS max_date FROM {table} WHERE city = ?",
        (city,),
    )
    return {"min_date": row["min_date"], "max_date": row["max_date"]}


def _alias_values(city) -> tuple[str, ...]:
    return tuple(dict.fromkeys((city.name, *city.aliases)))


def _market_event_counts(conn: sqlite3.Connection, city) -> dict:
    aliases = _alias_values(city)
    placeholders = ",".join("?" for _ in aliases)
    row = _fetch_one(
        conn,
        f"""
        SELECT COUNT(*) AS n, MIN(target_date) AS min_date, MAX(target_date) AS max_date
        FROM market_events
        WHERE city IN ({placeholders})
        """,
        aliases,
    )
    canonical = _count(conn, "market_events", city.name)
    return {
        "rows": int(row["n"] or 0),
        "canonical_rows": canonical,
        "alias_rows": int(row["n"] or 0) - canonical,
        "min_date": row["min_date"],
        "max_date": row["max_date"],
        "aliases_checked": list(aliases),
    }


def _load_tigge_manifest_cities(path: Path = TIGGE_MANIFEST) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["city"]) for row in data.get("cities", [])}


def _load_tigge_coverage(path: Path = TIGGE_COVERAGE) -> dict:
    if not path.exists():
        return {"status": "missing", "path": str(path), "city_missing_counts": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    missing_counts: dict[str, int] = {}
    for gap in data.get("gaps", []):
        for city in gap.get("missing_cities", []) or []:
            missing_counts[str(city)] = missing_counts.get(str(city), 0) + 1
    return {
        "status": "ok",
        "generated_at": data.get("generated_at"),
        "coverage_slots": len(data.get("coverage", [])),
        "gap_count": len(data.get("gaps", [])),
        "city_missing_counts": missing_counts,
    }


def _runtime_status(*, runtime_blockers: list[str]) -> str:
    hard = {"no_settlement_history", "no_observations"}
    if any(blocker in hard for blocker in runtime_blockers):
        return "data_unavailable"
    if "no_market_events" in runtime_blockers:
        return "no_active_market"
    return "paper_ready"


def _paper_status(*, runtime_blockers: list[str], archive_gaps: list[str]) -> str:
    runtime_status = _runtime_status(runtime_blockers=runtime_blockers)
    if runtime_status != "paper_ready":
        return runtime_status
    if archive_gaps:
        return "shadow_only"
    return "tradable"


def audit_city_data_readiness() -> dict:
    conn = get_shared_connection()
    init_schema(conn)
    tigge_cities = _load_tigge_manifest_cities()
    tigge_coverage = _load_tigge_coverage()

    rows = []
    for city_name in sorted(cities_by_name):
        city = cities_by_name[city_name]
        settlement_rows = _count(conn, "settlements", city_name)
        observation_rows = _count(conn, "observations", city_name)
        forecast_skill_rows = _count(conn, "forecast_skill", city_name)
        model_bias_rows = _count(conn, "model_bias", city_name)
        calibration_pair_rows = _count(conn, "calibration_pairs", city_name)
        token_price_rows = _count(conn, "token_price_log", city_name)
        market_events = _market_event_counts(conn, city)
        missing_observations = int(_fetch_one(
            conn,
            """
            SELECT COUNT(*) AS n
            FROM settlements s
            WHERE s.city = ?
              AND s.settlement_value IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM observations o
                  WHERE o.city = s.city
                    AND o.target_date = s.target_date
                    AND o.high_temp IS NOT NULL
              )
            """,
            (city_name,),
        )["n"] or 0)

        runtime_blockers = []
        archive_gaps = []
        if settlement_rows == 0:
            runtime_blockers.append("no_settlement_history")
        if observation_rows == 0 or missing_observations > 0:
            runtime_blockers.append("no_observations")
        if market_events["rows"] == 0:
            runtime_blockers.append("no_market_events")
        if forecast_skill_rows == 0:
            archive_gaps.append("forecast_history_unavailable")
        if model_bias_rows == 0:
            archive_gaps.append("model_bias_unavailable")
        if token_price_rows == 0:
            archive_gaps.append("price_history_unavailable")
        if calibration_pair_rows == 0:
            archive_gaps.append("calibration_pairs_unavailable")
        if city_name not in tigge_cities:
            archive_gaps.append("tigge_manifest_unavailable")
        elif tigge_coverage["status"] == "ok" and tigge_coverage["city_missing_counts"].get(city_name, 0) > 0:
            archive_gaps.append("tigge_coverage_incomplete")

        blockers = [*runtime_blockers, *archive_gaps]

        row = {
            "city": city_name,
            "cluster": city.cluster,
            "settlement_rows": settlement_rows,
            "settlement_dates": _date_bounds(conn, "settlements", city_name),
            "observation_rows": observation_rows,
            "missing_settlement_observations": missing_observations,
            "market_event_rows": market_events["rows"],
            "market_event_canonical_rows": market_events["canonical_rows"],
            "market_event_alias_rows": market_events["alias_rows"],
            "market_event_dates": {"min_date": market_events["min_date"], "max_date": market_events["max_date"]},
            "forecast_skill_rows": forecast_skill_rows,
            "model_bias_rows": model_bias_rows,
            "calibration_pair_rows": calibration_pair_rows,
            "token_price_log_rows": token_price_rows,
            "tigge_manifest": city_name in tigge_cities,
            "tigge_missing_slots": tigge_coverage.get("city_missing_counts", {}).get(city_name),
            "runtime_blockers": runtime_blockers,
            "archive_gaps": archive_gaps,
            "blockers": blockers,
            "runtime_status": _runtime_status(runtime_blockers=runtime_blockers),
            "paper_status": _paper_status(runtime_blockers=runtime_blockers, archive_gaps=archive_gaps),
        }
        rows.append(row)

    summary_counts: dict[str, int] = {}
    runtime_status_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    runtime_blocker_counts: dict[str, int] = {}
    archive_gap_counts: dict[str, int] = {}
    for row in rows:
        summary_counts[row["paper_status"]] = summary_counts.get(row["paper_status"], 0) + 1
        runtime_status_counts[row["runtime_status"]] = runtime_status_counts.get(row["runtime_status"], 0) + 1
        for blocker in row["blockers"]:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        for blocker in row["runtime_blockers"]:
            runtime_blocker_counts[blocker] = runtime_blocker_counts.get(blocker, 0) + 1
        for gap in row["archive_gaps"]:
            archive_gap_counts[gap] = archive_gap_counts.get(gap, 0) + 1

    conn.close()
    return {
        "configured_cities": len(cities_by_name),
        "rows": rows,
        "summary": {
            "paper_status_counts": dict(sorted(summary_counts.items())),
            "runtime_status_counts": dict(sorted(runtime_status_counts.items())),
            "blocker_counts": dict(sorted(blocker_counts.items())),
            "runtime_blocker_counts": dict(sorted(runtime_blocker_counts.items())),
            "archive_gap_counts": dict(sorted(archive_gap_counts.items())),
            "uncategorized_cities": [
                row["city"] for row in rows if row["paper_status"] not in {"tradable", "shadow_only", "data_unavailable", "no_active_market"}
            ],
            "tigge_coverage": {
                key: value for key, value in tigge_coverage.items() if key != "city_missing_counts"
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_city_data_readiness()
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
