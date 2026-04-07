#!/usr/bin/env python3
"""Backfill persisted cluster labels to the canonical city metadata taxonomy.

Repairs three persistence surfaces:
1. zeus.db calibration_pairs.cluster
2. zeus.db platt_models bucket keys (recomputed from updated pairs)
3. positions-*.json positions/recent_exits cluster fields
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name
from src.state.db import get_shared_connection as get_connection, init_schema


STATE_DIR = PROJECT_ROOT / "state"


def _canonical_cluster(city: str | None) -> str | None:
    if not city:
        return None
    city_obj = cities_by_name.get(city)
    if city_obj is None:
        return None
    return city_obj.cluster


def backfill_calibration_pairs() -> dict[str, int]:
    conn = get_connection()
    init_schema(conn)
    rows = conn.execute("SELECT id, city, cluster FROM calibration_pairs").fetchall()
    updated = 0
    skipped = 0
    for row in rows:
        cluster = _canonical_cluster(row["city"])
        if cluster is None:
            skipped += 1
            continue
        if row["cluster"] == cluster:
            continue
        conn.execute(
            "UPDATE calibration_pairs SET cluster = ? WHERE id = ?",
            (cluster, row["id"]),
        )
        updated += 1

    cleared_models = conn.execute("SELECT COUNT(*) FROM platt_models").fetchone()[0]
    conn.execute("DELETE FROM platt_models")
    conn.commit()
    conn.close()
    return {
        "calibration_pairs_updated": updated,
        "calibration_pairs_skipped": skipped,
        "platt_models_cleared": int(cleared_models),
    }


def backfill_portfolio_state() -> dict[str, int]:
    updated = 0
    files_touched = 0
    for path in sorted(STATE_DIR.glob("positions-*.json")):
        data = json.loads(path.read_text())
        dirty = False
        for key in ("positions", "recent_exits"):
            for row in data.get(key, []):
                cluster = _canonical_cluster(row.get("city"))
                if cluster is None or row.get("cluster") == cluster:
                    continue
                row["cluster"] = cluster
                updated += 1
                dirty = True
        if dirty:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            files_touched += 1
    return {
        "portfolio_cluster_rows_updated": updated,
        "portfolio_files_touched": files_touched,
    }


def run() -> dict[str, int]:
    report = {}
    report.update(backfill_calibration_pairs())
    report.update(backfill_portfolio_state())

    from scripts.refit_platt import refit_all

    refit_all()
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
