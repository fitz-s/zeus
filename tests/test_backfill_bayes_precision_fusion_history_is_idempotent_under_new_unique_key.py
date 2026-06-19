# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: operator BLOCKER (the_path PR review 2026-06-08) — the backfill must be
#   idempotent under the new UNIQUE(model,product_id,request_url_hash,city,target_date,metric,
#   source_cycle_time,endpoint). The pre-fix script wrote NULL product_id/request_url_hash, and
#   because SQLite treats NULL!=NULL the row was NOT idempotent: a 2nd run DUPLICATED every seed
#   row, doubling n_train and corrupting EB-lambda / covariance / tau0. BAYES_PRECISION_FUSION_SPEC §6 F1.
"""Relationship test (backfill -> UNIQUE-key idempotency boundary).

Running the seed twice on a FRESH PR400-schema DB must add ZERO rows the second time.
This is the direct antibody to the NULL!=NULL non-idempotency hole the operator flagged.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()
    return db


def _b0() -> dict:
    return {
        "Paris": {
            "leads": {
                "1": {
                    "ecmwf_ifs": {"2026-06-01": [7.4, 2.2], "2026-06-02": [8.1, 3.0]},
                    "gfs_global": {"2026-06-01": [7.1, 2.0]},
                },
                "3": {
                    "ecmwf_ifs": {"2026-06-01": [7.0, 2.5]},
                },
            },
        },
        "London": {
            "leads": {
                "1": {
                    "icon_eu": {"2026-06-01": [15.0, 9.0]},
                },
            },
        },
    }


def _count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
    conn.close()
    return int(n)


def test_backfill_run_twice_adds_zero_rows_second_time(tmp_path) -> None:
    from scripts.backfill_bayes_precision_fusion_history_from_b0 import backfill_bayes_precision_fusion_history

    db = _db(tmp_path)
    b0 = _b0()

    r1 = backfill_bayes_precision_fusion_history(b0=b0, db=db, dry_run=False)
    n1 = _count(db)
    assert n1 > 0, "first run must seed rows"
    assert r1["written_row_count"] == n1

    r2 = backfill_bayes_precision_fusion_history(b0=b0, db=db, dry_run=False)
    n2 = _count(db)
    assert n2 == n1, (
        f"second run must add ZERO rows under the new UNIQUE (got {n1} -> {n2}); "
        "NULL product_id/request_url_hash would have duplicated"
    )
    assert r2["written_row_count"] == 0, "idempotent re-run writes 0 rows"
