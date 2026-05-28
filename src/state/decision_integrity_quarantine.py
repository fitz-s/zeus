# Created: 2026-05-22
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E

"""PR-E — Quarantine tooling for decisions backed by non-contributing forecast snapshots.

Tags fact-table rows whose associated ensemble snapshot had
contributes_to_target_extrema=0 OR forecast_window_attribution_status='UNKNOWN'
with reason QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA.

Tables quarantined (all via forecast-snapshot linkage):
  - opportunity_fact           — direct: snapshot_id TEXT → ensemble_snapshots
  - calibration_pairs_v2       — direct: snapshot_id INTEGER → ensemble_snapshots
  - probability_trace_fact     — direct: decision_snapshot_id TEXT (CAST) → ensemble_snapshots
  - selection_family_fact      — direct: decision_snapshot_id TEXT (CAST) → ensemble_snapshots
  - selection_hypothesis_fact  — indirect: family_id → selection_family_fact → ensemble_snapshots
  - decision_events            — indirect: decision_event_id → opportunity_fact → ensemble_snapshots

Tables intentionally SKIPPED (no forecast snapshot linkage):
  - no_trade_events    — composite PK only, no single row_id column usable as quarantine key
  - shadow_experiments — no snapshot linkage of any kind
  - calibration_pairs  — legacy table, no snapshot_id column

Design choices:
  - NON-destructive: rows are tagged in decision_integrity_quarantine, never deleted.
  - Idempotent: uses INSERT OR IGNORE backed by UNIQUE(table_name, row_id, reason_code).
  - One reason code for both bad-contributes and UNKNOWN-attribution.
  - Cross-DB join: conn must have zeus-forecasts.db ATTACHed as 'forecasts'. Falls back
    to unqualified table name when 'forecasts' schema not detected (test pattern).
  - NULL passthrough: legacy rows with contributes_to_target_extrema IS NULL are NOT
    quarantined — they predate the bug and were not affected.

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Reason code written into decision_integrity_quarantine.
REASON_NON_CONTRIBUTING = "QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA"

# Table name tagged in quarantine rows for the original opportunity_fact function.
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
              same in-memory DB when ensemble_snapshots is co-located.
        dry_run: If True, return counts without writing anything.

    Returns:
        Dict with keys:
          - candidates_found: int — opportunity rows matching bad-snapshot criteria
          - already_quarantined: int — rows already tagged (skipped by INSERT OR IGNORE)
          - newly_quarantined: int — rows newly written this run
          - dry_run: bool

    INV-37: caller supplies conn; never auto-opens.

    Note on the cross-DB join:
        In production the query uses 'forecasts.ensemble_snapshots', which requires
        the forecasts DB to be ATTACHed as alias 'forecasts'.  When 'forecasts' is not
        attached (detected via PRAGMA database_list), the query falls back to the
        unqualified 'ensemble_snapshots' — this supports in-memory test DBs that
        carry the table without an ATTACH.
        See tests/test_decision_integrity_quarantine.py for the test fixture pattern.
    """
    recorded_at = datetime.now(timezone.utc).isoformat()

    # Determine which ensemble_snapshots prefix to use.
    # In production: 'forecasts.ensemble_snapshots' (ATTACHed forecasts DB).
    # In tests using a single in-memory DB: 'ensemble_snapshots' (no ATTACH needed).
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    snap_ref = "forecasts.ensemble_snapshots" if "forecasts" in attached else "ensemble_snapshots"

    # Find qualifying opportunity_fact rows.
    # A snapshot qualifies if contributes_to_target_extrema != 1 OR attribution is UNKNOWN.
    # snapshot_id in opportunity_fact is TEXT; snapshot_id in ensemble_snapshots is INTEGER.
    find_sql = f"""
        SELECT
            of.decision_id,
            of.snapshot_id
        FROM opportunity_fact of
        JOIN {snap_ref} esv
          ON CAST(of.snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE of.snapshot_id IS NOT NULL
          -- Align with the live reader gate (PR-A), which only acts when
          -- contributes_to_target_extrema is EXPLICITLY set; legacy NULL rows
          -- pass through live and must NOT be quarantined.
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
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

    q_ref = _quarantine_ref(conn)

    # Count pre-existing quarantine rows for accurate delta.
    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {q_ref} WHERE table_name=? AND reason_code=?",
        (TARGET_TABLE, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    # INSERT OR IGNORE — idempotent by UNIQUE(table_name, row_id, reason_code).
    insert_sql = f"""
        INSERT OR IGNORE INTO {q_ref}
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
        f"SELECT COUNT(*) FROM {q_ref} WHERE table_name=? AND reason_code=?",
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


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _quarantine_ref(conn: sqlite3.Connection) -> str:
    """Return qualified or unqualified decision_integrity_quarantine reference.

    In production, the quarantine table lives in the trade DB (zeus_trades.db),
    which may be ATTACHed as 'trade' on a world or forecasts connection.
    Falls back to unqualified name for in-memory test DBs.
    """
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    return "trade.decision_integrity_quarantine" if "trade" in attached else "decision_integrity_quarantine"


def _quarantine_table_via_snapshot(
    conn: sqlite3.Connection,
    *,
    target_table: str,
    find_sql: str,
    dry_run: bool,
) -> dict:
    """Generic quarantine writer: execute find_sql, tag qualifying rows.

    find_sql must SELECT (row_id TEXT, snapshot_id INTEGER|TEXT, source_run_id TEXT|NULL).
    Caller builds find_sql; this function handles INSERT OR IGNORE + counting.

    The quarantine table is referenced via _quarantine_ref(conn), which qualifies
    as 'trade.decision_integrity_quarantine' when the trade DB is ATTACHed, or falls
    back to unqualified for test in-memory DBs.

    INV-37: caller supplies conn; never auto-opens.
    """
    recorded_at = datetime.now(timezone.utc).isoformat()
    q_ref = _quarantine_ref(conn)

    try:
        candidates = conn.execute(find_sql).fetchall()
    except sqlite3.OperationalError as exc:
        msg = (
            f"quarantine query for {target_table} failed — "
            f"ensure forecasts DB is ATTACHed as 'forecasts': {exc}"
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
        "quarantine %s: found %d candidate rows with non-contributing snapshot",
        target_table,
        candidates_found,
    )

    if dry_run or candidates_found == 0:
        return {
            "candidates_found": candidates_found,
            "already_quarantined": 0,
            "newly_quarantined": 0,
            "dry_run": dry_run,
        }

    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {q_ref} WHERE table_name=? AND reason_code=?",
        (target_table, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    insert_sql = f"""
        INSERT OR IGNORE INTO {q_ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    rows_to_insert = []
    for row in candidates:
        row_id = str(row[0])
        snapshot_id = str(row[1]) if row[1] is not None else None
        source_run_id = row[2] if len(row) > 2 else None
        meta: dict = {"source": f"quarantine_{target_table}_for_noncontributing_forecast"}
        if source_run_id is not None:
            meta["source_run_id"] = source_run_id
        rows_to_insert.append(
            (target_table, row_id, REASON_NON_CONTRIBUTING, snapshot_id, recorded_at, json.dumps(meta))
        )
    conn.executemany(insert_sql, rows_to_insert)

    post_count = conn.execute(
        f"SELECT COUNT(*) FROM {q_ref} WHERE table_name=? AND reason_code=?",
        (target_table, REASON_NON_CONTRIBUTING),
    ).fetchone()[0]

    newly_quarantined = post_count - pre_count
    already_quarantined = candidates_found - newly_quarantined

    logger.info(
        "quarantine %s: newly=%d already=%d total_after=%d",
        target_table,
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


def _snap_ref(conn: sqlite3.Connection) -> str:
    """Return qualified or unqualified ensemble_snapshots reference."""
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    return "forecasts.ensemble_snapshots" if "forecasts" in attached else "ensemble_snapshots"


# ---------------------------------------------------------------------------
# Per-table quarantine entry points
# ---------------------------------------------------------------------------

def quarantine_calibration_pairs_v2_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Tag calibration_pairs_v2 rows whose forecast snapshot is non-contributing.

    calibration_pairs_v2 lives in zeus-forecasts.db; in production, conn must be a
    forecasts connection (or have forecasts as 'main'). ensemble_snapshots is
    in the same forecasts DB, so no ATTACH is needed for this table.

    row_id = str(pair_id)  (INTEGER PK, stored as TEXT in quarantine).
    forecast_snapshot_id = str(snapshot_id).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            CAST(cp2.pair_id AS TEXT) AS row_id,
            cp2.snapshot_id           AS snapshot_id,
            esv.source_run_id         AS source_run_id
        FROM calibration_pairs_v2 cp2
        JOIN {snap_ref} esv ON cp2.snapshot_id = esv.snapshot_id
        WHERE cp2.snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY cp2.pair_id
    """
    return _quarantine_table_via_snapshot(
        conn,
        target_table="calibration_pairs_v2",
        find_sql=find_sql,
        dry_run=dry_run,
    )


def quarantine_probability_trace_fact_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Tag probability_trace_fact rows whose forecast snapshot is non-contributing.

    probability_trace_fact.decision_snapshot_id is TEXT; cast to INTEGER for join.
    row_id = ptf.trace_id (TEXT PK).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            ptf.trace_id      AS row_id,
            ptf.decision_snapshot_id AS snapshot_id,
            esv.source_run_id AS source_run_id
        FROM probability_trace_fact ptf
        JOIN {snap_ref} esv
          ON CAST(ptf.decision_snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE ptf.decision_snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY ptf.trace_id
    """
    return _quarantine_table_via_snapshot(
        conn,
        target_table="probability_trace_fact",
        find_sql=find_sql,
        dry_run=dry_run,
    )


def quarantine_selection_family_fact_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Tag selection_family_fact rows whose forecast snapshot is non-contributing.

    selection_family_fact.decision_snapshot_id is TEXT; cast to INTEGER for join.
    row_id = sff.family_id (TEXT PK).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            sff.family_id     AS row_id,
            sff.decision_snapshot_id AS snapshot_id,
            esv.source_run_id AS source_run_id
        FROM selection_family_fact sff
        JOIN {snap_ref} esv
          ON CAST(sff.decision_snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE sff.decision_snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY sff.family_id
    """
    return _quarantine_table_via_snapshot(
        conn,
        target_table="selection_family_fact",
        find_sql=find_sql,
        dry_run=dry_run,
    )


def quarantine_selection_hypothesis_fact_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Tag selection_hypothesis_fact rows whose backing family has a non-contributing snapshot.

    Joins: selection_hypothesis_fact → selection_family_fact → ensemble_snapshots.
    row_id = shf.hypothesis_id (TEXT PK).

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            shf.hypothesis_id AS row_id,
            sff.decision_snapshot_id AS snapshot_id,
            esv.source_run_id AS source_run_id
        FROM selection_hypothesis_fact shf
        JOIN selection_family_fact sff ON shf.family_id = sff.family_id
        JOIN {snap_ref} esv
          ON CAST(sff.decision_snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE sff.decision_snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY shf.hypothesis_id
    """
    return _quarantine_table_via_snapshot(
        conn,
        target_table="selection_hypothesis_fact",
        find_sql=find_sql,
        dry_run=dry_run,
    )


def _de_natural_pk_hash(
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    observation_time: str,
    decision_seq: int,
) -> str:
    """Return a deterministic hex-digest row_id for a decision_events row.

    decision_event_id is only an INDEX (not UNIQUE) and may be the sentinel
    'deid_v1_BACKSTOP_NULL_WRITER_BYPASS' for multiple rows. The natural PK
    (market_slug, temperature_metric, target_date, observation_time, decision_seq)
    is the true uniqueness anchor for decision_events rows.
    """
    key = f"{market_slug}|{temperature_metric}|{target_date}|{observation_time}|{decision_seq}"
    # [:32] = 128-bit hex prefix of SHA-256. Collision-safe for this key space
    # (five structured fields with bounded cardinality). Not the full 256-bit digest.
    return "de_pk_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def quarantine_decision_events_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Tag decision_events rows whose backing opportunity_fact snapshot is non-contributing.

    Joins: decision_events → opportunity_fact (via decision_event_id = decision_id)
           → ensemble_snapshots.

    row_id = _de_natural_pk_hash(market_slug, temperature_metric, target_date,
                                  observation_time, decision_seq)
    Using the 5-col natural PK hash avoids the BACKSTOP sentinel collision
    (decision_event_id = 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS' repeats across rows).

    Only rows with a non-NULL, non-BACKSTOP decision_event_id are linked to
    opportunity_fact and tagged; pure-BACKSTOP rows (no decision_id in
    opportunity_fact) are skipped — they have no forecast linkage to verify.

    INV-37: caller supplies conn; never auto-opens.
    """
    snap_ref = _snap_ref(conn)
    find_sql = f"""
        SELECT
            de.market_slug       AS market_slug,
            de.temperature_metric AS temperature_metric,
            de.target_date       AS target_date,
            de.observation_time  AS observation_time,
            de.decision_seq      AS decision_seq,
            of.snapshot_id       AS snapshot_id,
            esv.source_run_id    AS source_run_id
        FROM decision_events de
        JOIN opportunity_fact of ON de.decision_event_id = of.decision_id
        JOIN {snap_ref} esv
          ON CAST(of.snapshot_id AS INTEGER) = esv.snapshot_id
        WHERE de.decision_event_id IS NOT NULL
          AND de.decision_event_id != 'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'
          AND of.snapshot_id IS NOT NULL
          AND esv.contributes_to_target_extrema IS NOT NULL
          AND (
              esv.contributes_to_target_extrema != 1
              OR COALESCE(esv.forecast_window_attribution_status, 'UNKNOWN') = 'UNKNOWN'
          )
        ORDER BY de.market_slug, de.temperature_metric, de.target_date,
                 de.observation_time, de.decision_seq
    """

    recorded_at = datetime.now(timezone.utc).isoformat()
    q_ref = _quarantine_ref(conn)

    try:
        raw_rows = conn.execute(find_sql).fetchall()
    except sqlite3.OperationalError as exc:
        msg = (
            f"quarantine query for decision_events failed — "
            f"ensure forecasts DB is ATTACHed as 'forecasts': {exc}"
        )
        logger.error(msg)
        return {
            "candidates_found": 0,
            "already_quarantined": 0,
            "newly_quarantined": 0,
            "dry_run": dry_run,
            "error": msg,
        }

    candidates_found = len(raw_rows)
    logger.info(
        "quarantine decision_events: found %d candidate rows with non-contributing snapshot",
        candidates_found,
    )

    if dry_run or candidates_found == 0:
        return {
            "candidates_found": candidates_found,
            "already_quarantined": 0,
            "newly_quarantined": 0,
            "dry_run": dry_run,
        }

    pre_count = conn.execute(
        f"SELECT COUNT(*) FROM {q_ref} WHERE table_name='decision_events' AND reason_code=?",
        (REASON_NON_CONTRIBUTING,),
    ).fetchone()[0]

    insert_sql = f"""
        INSERT OR IGNORE INTO {q_ref}
            (table_name, row_id, reason_code, forecast_snapshot_id, recorded_at, meta_json)
        VALUES ('decision_events', ?, ?, ?, ?, ?)
    """
    rows_to_insert = []
    for row in raw_rows:
        market_slug, temperature_metric, target_date, observation_time, decision_seq = (
            row[0], row[1], row[2], row[3], row[4]
        )
        snapshot_id = str(row[5]) if row[5] is not None else None
        source_run_id = row[6]
        row_id = _de_natural_pk_hash(
            market_slug, temperature_metric, target_date, observation_time, decision_seq
        )
        meta: dict = {
            "source": "quarantine_decision_events_for_noncontributing_forecast",
            "natural_pk": {
                "market_slug": market_slug,
                "temperature_metric": temperature_metric,
                "target_date": target_date,
                "observation_time": observation_time,
                "decision_seq": int(decision_seq),
            },
        }
        if source_run_id is not None:
            meta["source_run_id"] = source_run_id
        rows_to_insert.append(
            (row_id, REASON_NON_CONTRIBUTING, snapshot_id, recorded_at, json.dumps(meta))
        )
    conn.executemany(insert_sql, rows_to_insert)

    post_count = conn.execute(
        f"SELECT COUNT(*) FROM {q_ref} WHERE table_name='decision_events' AND reason_code=?",
        (REASON_NON_CONTRIBUTING,),
    ).fetchone()[0]

    newly_quarantined = post_count - pre_count
    already_quarantined = candidates_found - newly_quarantined

    logger.info(
        "quarantine decision_events: newly=%d already=%d total_after=%d",
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


def quarantine_all_tables_for_noncontributing_forecast(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict:
    """Run quarantine across all supported tables on a SINGLE connection.

    This convenience wrapper requires conn to have BOTH 'forecasts' and 'trade'
    ATTACHed (or be an in-memory test DB with all tables co-located).

    IMPORTANT — K1 DB-split production usage:
        calibration_pairs_v2 lives in zeus-forecasts.db (forecasts DB).
        decision_integrity_quarantine lives in zeus_trades.db (trade DB).
        World tables (opportunity_fact, decision_events, probability_trace_fact,
        selection_family_fact, selection_hypothesis_fact) live in zeus-world.db.

        For production: use the CLI scripts/quarantine_bad_forecast_decisions.py
        which opens per-DB connections with correct ATTACHes. This wrapper is
        intended for in-memory integration tests and operator one-shots where
        all tables are co-located.

    Raises ValueError if 'forecasts' is not attached/present (calibration_pairs_v2
    cannot be quarantined without it and would silently no-op).

    INV-37: caller supplies conn; never auto-opens.
    """
    # Verify forecasts tables are reachable before starting any writes.
    try:
        conn.execute("SELECT 1 FROM ensemble_snapshots LIMIT 0")
    except sqlite3.OperationalError:
        # Try forecasts-qualified name.
        try:
            conn.execute("SELECT 1 FROM forecasts.ensemble_snapshots LIMIT 0")
        except sqlite3.OperationalError:
            raise ValueError(
                "quarantine_all_tables_for_noncontributing_forecast: "
                "ensemble_snapshots not found — ensure the forecasts DB is "
                "ATTACHed as 'forecasts' OR all tables are co-located (in-memory test)."
            )
    # Mapping from function to the table name it quarantines.
    fn_table_pairs = [
        (quarantine_decisions_for_noncontributing_forecast, "opportunity_fact"),
        (quarantine_calibration_pairs_v2_for_noncontributing_forecast, "calibration_pairs_v2"),
        (quarantine_probability_trace_fact_for_noncontributing_forecast, "probability_trace_fact"),
        (quarantine_selection_family_fact_for_noncontributing_forecast, "selection_family_fact"),
        (quarantine_selection_hypothesis_fact_for_noncontributing_forecast, "selection_hypothesis_fact"),
        (quarantine_decision_events_for_noncontributing_forecast, "decision_events"),
    ]
    aggregate: dict = {
        "candidates_found": 0,
        "already_quarantined": 0,
        "newly_quarantined": 0,
        "dry_run": dry_run,
        "per_table": {},
    }
    for fn, tname in fn_table_pairs:
        result = fn(conn, dry_run=dry_run)
        aggregate["per_table"][tname] = result
        aggregate["candidates_found"] += result.get("candidates_found", 0)
        aggregate["already_quarantined"] += result.get("already_quarantined", 0)
        aggregate["newly_quarantined"] += result.get("newly_quarantined", 0)
    return aggregate
