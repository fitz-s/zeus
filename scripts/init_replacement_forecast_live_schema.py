#!/usr/bin/env python3
"""Initialize replacement forecast live-support tables on a forecast DB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_live_switch_surface import REQUIRED_FORECAST_TABLES  # noqa: E402
from src.state.db import _connect  # noqa: E402
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema  # noqa: E402


REPLACEMENT_LIVE_TABLES = (
    "raw_forecast_artifacts",
    "deterministic_forecast_anchors",
    "forecast_posteriors",
    "replacement_shadow_decisions",
)


def _tables(conn: Any) -> tuple[str, ...]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return tuple(sorted(str(row[0]) for row in rows))


def _report(
    *,
    db_path: Path,
    before: tuple[str, ...],
    after: tuple[str, ...],
    committed: bool,
) -> dict[str, object]:
    before_set = set(before)
    after_set = set(after)
    created = tuple(table for table in REPLACEMENT_LIVE_TABLES if table not in before_set and table in after_set)
    missing_after = tuple(table for table in REPLACEMENT_LIVE_TABLES if table not in after_set)
    live_switch_missing_after = tuple(table for table in REQUIRED_FORECAST_TABLES if table not in after_set)
    status = "READY" if not missing_after else "BLOCKED"
    return {
        "status": status,
        "db_path": str(db_path),
        "committed": committed,
        "replacement_live_tables": list(REPLACEMENT_LIVE_TABLES),
        "created_tables": list(created),
        "missing_replacement_live_tables": list(missing_after),
        "missing_live_switch_forecast_tables_after": list(live_switch_missing_after),
        "table_count_before": len(before),
        "table_count_after": len(after),
    }


def initialize_replacement_forecast_live_schema(
    db_path: Path,
    *,
    commit: bool,
) -> dict[str, object]:
    resolved = Path(db_path)
    if not resolved.exists():
        raise FileNotFoundError(str(resolved))
    conn = _connect(resolved, write_class="bulk")
    try:
        before = _tables(conn)
        conn.execute("BEGIN")
        ensure_replacement_forecast_live_schema(conn)
        after = _tables(conn)
        if commit:
            conn.commit()
        else:
            conn.rollback()
        return _report(db_path=resolved, before=before, after=after, committed=commit)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize replacement forecast live-support tables")
    parser.add_argument("--forecast-db", type=Path, default=ROOT / "state" / "zeus-forecasts.db")
    parser.add_argument("--commit", action="store_true", help="Commit DDL; default is dry-run rollback")
    parser.add_argument("--stdout", action="store_true", help="Print JSON report")
    args = parser.parse_args(argv)
    try:
        report = initialize_replacement_forecast_live_schema(args.forecast_db, commit=bool(args.commit))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"{report['status']}: committed={report['committed']} created={','.join(report['created_tables'])}")
    return 0 if report["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
