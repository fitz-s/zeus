from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "state" / "zeus.db"
DEFAULT_LEGACY_EXPORTS = (
    ROOT / "state" / "positions.json",
)
CANONICAL_OPEN_PHASES = {
    "pending_entry",
    "active",
    "day0_window",
    "pending_exit",
}
LEGACY_INACTIVE_STATES = {
    "voided",
    "settled",
    "economically_closed",
    "quarantined",
    "admin_closed",
}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _canonical_open_projection(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT trade_id, strategy_key, phase
        FROM position_current
        """
    ).fetchall()
    open_rows = [
        row for row in rows
        if str(row["phase"] or "") in CANONICAL_OPEN_PHASES
    ]
    by_strategy: dict[str, int] = {}
    trade_ids: list[str] = []
    for row in open_rows:
        strategy = str(row["strategy_key"] or "unclassified")
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        trade_ids.append(str(row["trade_id"] or ""))
    return {
        "open_positions": len(open_rows),
        "trade_ids": sorted(tid for tid in trade_ids if tid),
        "by_strategy": by_strategy,
    }


def _infer_legacy_env(path: Path) -> str:
    stem = path.stem
    if stem.endswith("-live"):
        return "live"
    if "-" in stem:
        return "legacy_env"
    return "unknown"


def _legacy_open_projection(path: Path) -> dict:
    raw = json.loads(path.read_text())
    positions = raw.get("positions", []) if isinstance(raw, dict) else []
    open_positions = [
        pos for pos in positions
        if str(pos.get("state") or "") not in LEGACY_INACTIVE_STATES
    ]
    by_strategy: dict[str, int] = {}
    trade_ids: list[str] = []
    for pos in open_positions:
        strategy = str(pos.get("strategy") or pos.get("edge_source") or "unclassified")
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        trade_ids.append(str(pos.get("trade_id") or ""))
    return {
        "path": str(path),
        "env": _infer_legacy_env(path),
        "open_positions": len(open_positions),
        "trade_ids": sorted(tid for tid in trade_ids if tid),
        "by_strategy": by_strategy,
    }


def _compare_projection(canonical: dict, legacy: dict) -> dict:
    canonical_ids = set(canonical["trade_ids"])
    legacy_ids = set(legacy["trade_ids"])
    canonical_by_strategy = canonical["by_strategy"]
    legacy_by_strategy = legacy["by_strategy"]
    strategy_keys = sorted(set(canonical_by_strategy) | set(legacy_by_strategy))
    strategy_deltas = {
        key: {
            "canonical": int(canonical_by_strategy.get(key, 0)),
            "legacy": int(legacy_by_strategy.get(key, 0)),
        }
        for key in strategy_keys
        if int(canonical_by_strategy.get(key, 0)) != int(legacy_by_strategy.get(key, 0))
    }
    return {
        "status": "match" if canonical_ids == legacy_ids and not strategy_deltas else "mismatch",
        "missing_in_canonical": sorted(legacy_ids - canonical_ids),
        "missing_in_legacy": sorted(canonical_ids - legacy_ids),
        "strategy_deltas": strategy_deltas,
    }


def _default_legacy_exports() -> list[Path]:
    return [path for path in DEFAULT_LEGACY_EXPORTS if path.exists()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--legacy-export", action="append", type=Path, dest="legacy_exports")
    parser.add_argument("--ci", action="store_true")
    args = parser.parse_args()

    legacy_exports = args.legacy_exports or _default_legacy_exports()

    if not args.db.exists():
        print(json.dumps({
            "status": "db_missing",
            "db": str(args.db),
            "legacy_exports": [str(path) for path in legacy_exports],
        }, indent=2))
        return 0

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        if not table_exists(conn, "position_events") or not table_exists(conn, "position_current"):
            print(json.dumps({
                "status": "staged_missing_canonical_tables",
                "db": str(args.db),
                "missing_tables": [
                    table for table in ("position_events", "position_current")
                    if not table_exists(conn, table)
                ],
            }, indent=2))
            return 0

        canonical = _canonical_open_projection(conn)
        legacy_reports = []
        overall_status = "ok"
        for export_path in legacy_exports:
            if not export_path.exists():
                legacy_reports.append({
                    "path": str(export_path),
                    "status": "missing_export",
                })
                overall_status = "degraded"
                continue
            legacy = _legacy_open_projection(export_path)
            comparison = _compare_projection(canonical, legacy)
            legacy_reports.append({
                **legacy,
                "comparison": comparison,
            })
            if comparison["status"] != "match":
                overall_status = "mismatch"

        print(json.dumps({
            "status": overall_status,
            "db": str(args.db),
            "canonical": canonical,
            "legacy_exports": legacy_reports,
        }, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
