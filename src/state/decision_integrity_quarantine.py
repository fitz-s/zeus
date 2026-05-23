# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E

"""PR-E — Quarantine tooling for decisions backed by non-contributing forecast snapshots.

Tags opportunity_fact rows whose associated ensemble snapshot had
contributes_to_target_extrema=0 OR forecast_window_attribution_status='UNKNOWN'
with reason QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA.

Design choices:
  - NON-destructive: rows are tagged in decision_integrity_quarantine, never deleted.
  - Idempotent: uses INSERT OR IGNORE backed by UNIQUE(table_name, row_id, reason_code).
  - One reason code for both bad-contributes and UNKNOWN-attribution, ordered by
    severity (non-contributing > UNKNOWN). A row qualifies if EITHER condition holds;
    the most-severe reason applies.
  - Cross-DB join: conn must be a trade connection (zeus_trades.db as main DB) with
    zeus-forecasts.db ATTACHed as 'forecasts'. For production use, pass the result of
    get_trade_connection_with_world_optional() (which ATTACHes forecasts). For test
    use, set up both tables in a single in-memory DB.
  - opportunity_fact.snapshot_id is TEXT; ensemble_snapshots_v2.snapshot_id is INTEGER.
    The join uses CAST(opportunity_fact.snapshot_id AS INTEGER).

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Reason code written into decision_integrity_quarantine.
REASON_NON_CONTRIBUTING = "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA"

# Table name tagged in quarantine rows.
TARGET_TABLE = "opportunity_fact"


def quarantine_decisions_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Tag opportunity_fact rows whose forecast snapshot has contributes=0 or attribution UNKNOWN.

    Args:
        conn: Trade DB connection with zeus-forecasts.db ATTACHed as 'forecasts'.
              In test contexts, 'forecasts.' schema can be a second ATTACH or the
              same in-memory DB when ensemble_snapshots_v2 is co-located.
        dry_run: If True, return counts without writing anything.

    Returns:
        Dict with keys:
          - candidates_found: int — opportunity rows matching bad-snapshot criteria
          - already_quarantined: int — rows already tagged (skipped by INSERT OR IGNORE)
          - newly_quarantined: int — rows newly written this run
          - dry_run: bool

    INV-37: caller supplies conn; never auto-opens.

    Note on the cross-DB join:
        The query references 'forecasts.ensemble_snapshots_v2' — this requires the
        forecasts DB to be ATTACHed as alias 'forecasts'. If the schema alias is absent,
        the query fails with OperationalError; callers must pre-ATTACH.
        In-memory tests may use a single DB with ensemble_snapshots_v2 as a bare table
        (no alias needed) — see tests/test_decision_integrity_quarantine.py for pattern.
    """
    recorded_at = datetime.now(timezone.utc).isoformat()

    # Determine which ensemble_snapshots_v2 prefix to use.
    # In production: 'forecasts.ensemble_snapshots_v2' (ATTACHed forecasts DB).
    # In tests using a single in-memory DB: 'ensemble_snapshots_v2' (no ATTACH needed).
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    snap_ref = "forecasts.ensemble_snapshots_v2" if "forecasts" in attached else "ensemble_snapshots_v2"

    # Find qualifying opportunity_fact rows.
    # A snapshot qualifies if contributes_to_target_extrema != 1 OR attribution is UNKNOWN.
    # snapshot_id in opportunity_fact is TEXT; snapshot_id in ensemble_snapshots_v2 is INTEGER.
    find_sql = f"""
        SELECT
            of.decision_id,
            of.snapshot_id
        FROM opportunity_fact of
        JOIN {snap_ref} esv
          ON CAST(of.snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE of.snapshot_id IS NOT NULL
          AND (
              COALESCE(esv.contributes_to_target_extrema, 0) != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY of.decision_id
    """

    try:
        candidates = conn.execute(find_sql).fetchall()
    except sqlite3.OperationalError as exc:
        msg = (
            f"quarantine query failed — ensure forecasts DB is ATTACHed as 'forecasts': {exc}"
        )
        logger.error(msg)
        return {
            "candidates_found": 0,
            "already_quarantined": 0,
            "newly_quarantined": 0,
            "dry_run": dry_run,
            "error": msg,
        }

    candidates_found = len(candidates)
    logger.info(
        "quarantine_decisions: found %d candidate opportunity_fact rows with non-contributing snapshot",
        candidates_found,
    )

    if dry_run or candidates_found == 0:
        return {
            "candidates_found": candidates_found,
            "already_quarantined": 0,
            "newly_quarantined": 0,
            "dry_run": dry_run,
        }

    # Count pre-existing quarantine rows for accurate delta.
    pre_count = conn.execute(
        "SELECT COUNT(*) FROM decision_integrity_quarantine WHERE table_name=? AND reason_code=?",
        (TARGET_TABLE, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    # INSERT OR IGNORE — idempotent by UNIQUE(table_name, row_id, reason_code).
    insert_sql = """
        INSERT OR IGNORE INTO decision_integrity_quarantine
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    meta = json.dumps({"source": "quarantine_decisions_for_noncontributing_forecast"})
    rows_to_insert = [
        (TARGET_TABLE, decision_id, REASON_NON_CONTRIBUTING, str(snapshot_id), recorded_at, meta)
        for decision_id, snapshot_id in candidates
    ]
    conn.executemany(insert_sql, rows_to_insert)

    post_count = conn.execute(
        "SELECT COUNT(*) FROM decision_integrity_quarantine WHERE table_name=? AND reason_code=?",
        (TARGET_TABLE, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    newly_quarantined = post_count - pre_count
    already_quarantined = candidates_found - newly_quarantined

    logger.info(
        "quarantine_decisions: newly=%d already=%d total_after=%d",
        newly_quarantined,
        already_quarantined,
        post_count,
    )

    return {
        "candidates_found": candidates_found,
        "already_quarantined": already_quarantined,
        "newly_quarantined": newly_quarantined,
        "dry_run": False,
    }
