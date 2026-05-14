# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §2 P0
"""K1 P0 triage: copy post-K1-split observation rows from zeus-world.db into
zeus-forecasts.db, and mirror market_events_v2 rows that are world-only.

Background
----------
The K1 forecast DB split (commit eba80d2b9d, 2026-05-11) moved ownership of
the ``observations`` and ``market_events_v2`` tables to ``zeus-forecasts.db``.
However, ``_k2_daily_obs_tick`` and ``_k2_startup_catch_up`` in
``src/ingest_main.py`` continued writing new daily observations to
``zeus-world.db`` until this P0 hotpatch.

This script copies the ~109 stranded post-K1 rows into forecasts.db using
``INSERT OR IGNORE`` (safe because both tables have UNIQUE constraints; see
PLAN §2 P0 idempotency contract). It is idempotent and safe to re-run.

OPERATOR INSTRUCTIONS — DO NOT RUN without reading:
  1. Stop the ingest daemon first:
       launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist
  2. Verify no open file descriptors on either DB:
       lsof state/zeus-world.db state/zeus-forecasts.db
     Both must return empty.
  3. From the zeus repo root, run:
       python scripts/migrate_world_observations_to_forecasts.py [--dry-run]
  4. Restart the ingest daemon:
       launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist
     The P0-patched daemon will now write observations to forecasts.db going
     forward. The boot-time catch_up_obs will fill any gap from the quiesce
     window.

ROLLBACK: not needed. The world.db rows are preserved (not deleted). Reverting
src/ingest_main.py to ``get_world_connection`` restores previous routing.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

_ZEUS_ROOT = Path(__file__).resolve().parent.parent
if str(_ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZEUS_ROOT))

from src.state.db import ZEUS_WORLD_DB_PATH, ZEUS_FORECASTS_DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("migrate_world_observations_to_forecasts")

# ── Column lists ──────────────────────────────────────────────────────────────
# Explicit column lists (no SELECT *) so the script is immune to future column
# additions on either side.

_OBS_COLS = (
    "city", "target_date", "source",
    "high_temp", "low_temp", "unit", "station_id", "fetched_at",
    "high_raw_value", "high_raw_unit", "high_target_unit",
    "low_raw_value", "low_raw_unit", "low_target_unit",
    "high_fetch_utc", "high_local_time",
    "high_collection_window_start_utc", "high_collection_window_end_utc",
    "low_fetch_utc", "low_local_time",
    "low_collection_window_start_utc", "low_collection_window_end_utc",
    "timezone", "utc_offset_minutes", "dst_active",
    "is_ambiguous_local_hour", "is_missing_local_hour",
    "hemisphere", "season", "month",
    "rebuild_run_id", "data_source_version",
    "authority", "high_provenance_metadata", "low_provenance_metadata",
)

_MEV2_COLS = (
    "market_slug", "city", "target_date", "temperature_metric",
    "condition_id", "token_id", "range_label", "range_low", "range_high",
    "outcome", "created_at", "recorded_at",
)


# ── Value-diff probe ──────────────────────────────────────────────────────────

def _obs_value_diff_probe(world: sqlite3.Connection) -> int:
    """Return count of (city, target_date, source) tuples that exist on BOTH
    world.observations and forecasts.observations with DIFFERENT payload values.

    Per PLAN §2 P0 idempotency contract: any count > 0 is a STOP condition.
    """
    payload_cols = [
        c for c in _OBS_COLS
        if c not in ("city", "target_date", "source")
    ]
    mismatch_clauses = " OR ".join(
        f"(w.{c} IS NOT f.{c})" for c in payload_cols
    )
    sql = f"""
        SELECT w.city, w.target_date, w.source,
               w.high_temp, w.low_temp, w.unit,
               f.high_temp AS f_high_temp, f.low_temp AS f_low_temp, f.unit AS f_unit
        FROM main.observations w
        INNER JOIN forecasts.observations f
            ON w.city=f.city AND w.target_date=f.target_date AND w.source=f.source
        WHERE w.target_date >= '2026-05-11'
          AND ({mismatch_clauses})
    """
    rows = world.execute(sql).fetchall()
    if rows:
        logger.error("VALUE-DIFF CONFLICTS found (%d rows):", len(rows))
        for row in rows[:20]:  # cap display at 20
            logger.error("  %s", row)
        if len(rows) > 20:
            logger.error("  ... and %d more", len(rows) - 20)
    return len(rows)


def _mev2_value_diff_probe(world: sqlite3.Connection) -> int:
    """Return count of (market_slug, condition_id) tuples with VALUE conflicts
    between world.market_events_v2 and forecasts.market_events_v2.

    Per PLAN §2 P0.2: any count > 0 is a STOP condition.
    """
    payload_cols = [c for c in _MEV2_COLS if c not in ("market_slug", "condition_id")]
    mismatch_clauses = " OR ".join(
        f"(w.{c} IS NOT f.{c})" for c in payload_cols
    )
    sql = f"""
        SELECT w.market_slug, w.condition_id,
               w.city, f.city AS f_city,
               w.outcome, f.outcome AS f_outcome
        FROM main.market_events_v2 w
        INNER JOIN forecasts.market_events_v2 f
            ON w.market_slug = f.market_slug AND w.condition_id = f.condition_id
        WHERE {mismatch_clauses}
    """
    rows = world.execute(sql).fetchall()
    if rows:
        logger.error("market_events_v2 VALUE-DIFF CONFLICTS found (%d rows):", len(rows))
        for row in rows[:20]:
            logger.error("  %s", row)
        if len(rows) > 20:
            logger.error("  ... and %d more", len(rows) - 20)
    return len(rows)


# ── Migration steps ───────────────────────────────────────────────────────────

def _migrate_observations(world: sqlite3.Connection, dry_run: bool) -> dict:
    """Copy world-only observations (target_date >= 2026-05-11) to forecasts.

    Returns a summary dict with pre/post row counts.
    """
    pre_world = world.execute(
        "SELECT COUNT(*), MAX(target_date) FROM main.observations"
        " WHERE target_date >= '2026-05-11'"
    ).fetchone()
    pre_forecasts = world.execute(
        "SELECT COUNT(*), MAX(target_date) FROM forecasts.observations"
    ).fetchone()

    cols_str = ", ".join(_OBS_COLS)
    insert_sql = f"""
        INSERT OR IGNORE INTO forecasts.observations ({cols_str})
        SELECT {cols_str}
        FROM main.observations
        WHERE target_date >= '2026-05-11'
    """
    if dry_run:
        logger.info("[DRY-RUN] Would execute: %s", insert_sql.strip())
        copied = 0
    else:
        world.execute(insert_sql)
        world.commit()
        copied = world.execute("SELECT changes()").fetchone()[0]

    post_forecasts = world.execute(
        "SELECT COUNT(*), MAX(target_date) FROM forecasts.observations"
    ).fetchone()

    summary = {
        "world_post_k1_rows": pre_world[0],
        "world_max_target_date": pre_world[1],
        "forecasts_pre_count": pre_forecasts[0],
        "forecasts_pre_max_date": pre_forecasts[1],
        "forecasts_post_count": post_forecasts[0],
        "forecasts_post_max_date": post_forecasts[1],
        "inserted": copied,
        "dry_run": dry_run,
    }
    logger.info("observations migration: %s", summary)
    return summary


def _migrate_market_events_v2(world: sqlite3.Connection, dry_run: bool) -> dict:
    """Mirror world.market_events_v2 rows into forecasts.market_events_v2 via
    INSERT OR IGNORE (UNIQUE(market_slug, condition_id) deduplicates).
    """
    pre_world = world.execute("SELECT COUNT(*) FROM main.market_events_v2").fetchone()
    pre_forecasts = world.execute("SELECT COUNT(*) FROM forecasts.market_events_v2").fetchone()

    cols_str = ", ".join(_MEV2_COLS)
    insert_sql = f"""
        INSERT OR IGNORE INTO forecasts.market_events_v2 ({cols_str})
        SELECT {cols_str}
        FROM main.market_events_v2
    """
    if dry_run:
        logger.info("[DRY-RUN] Would execute: %s", insert_sql.strip())
        inserted = 0
    else:
        world.execute(insert_sql)
        world.commit()
        inserted = world.execute("SELECT changes()").fetchone()[0]

    post_forecasts = world.execute("SELECT COUNT(*) FROM forecasts.market_events_v2").fetchone()

    summary = {
        "world_rows": pre_world[0],
        "forecasts_pre_count": pre_forecasts[0],
        "forecasts_post_count": post_forecasts[0],
        "inserted": inserted,
        "dry_run": dry_run,
    }
    logger.info("market_events_v2 migration: %s", summary)
    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="K1 P0: copy stranded world.observations + market_events_v2 "
                    "to forecasts.db (idempotent via INSERT OR IGNORE)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without writing any rows.",
    )
    parser.add_argument(
        "--conflict-policy",
        choices=["stop", "keep_forecasts"],
        default="stop",
        help=(
            "What to do when VALUE-DIFF conflicts are found. "
            "'stop' (default) aborts. "
            "'keep_forecasts' proceeds with INSERT OR IGNORE (forecasts-side row wins)."
        ),
    )
    args = parser.parse_args(argv)

    world_path = str(ZEUS_WORLD_DB_PATH)
    forecasts_path = str(ZEUS_FORECASTS_DB_PATH)

    logger.info("Opening world.db: %s", world_path)
    logger.info("Attaching forecasts.db: %s", forecasts_path)
    world = sqlite3.connect(world_path)
    world.execute(f"ATTACH DATABASE '{forecasts_path}' AS forecasts")

    errors = 0

    # ── Step 1: observations VALUE-diff probe ──────────────────────────────
    logger.info("Step 1: observations VALUE-diff probe (PLAN §2 P0 STOP condition)")
    obs_conflicts = _obs_value_diff_probe(world)
    if obs_conflicts > 0:
        if args.conflict_policy == "stop":
            logger.error(
                "STOP: %d observations VALUE-DIFF conflicts found. "
                "Re-run with --conflict-policy keep_forecasts to proceed "
                "(forecasts-side row wins, world-side insert is IGNORED).",
                obs_conflicts,
            )
            world.close()
            return 1
        else:
            logger.warning(
                "conflict-policy=keep_forecasts: proceeding despite %d conflicts "
                "(INSERT OR IGNORE; forecasts row kept).",
                obs_conflicts,
            )

    # ── Step 2: market_events_v2 VALUE-diff probe ──────────────────────────
    logger.info("Step 2: market_events_v2 VALUE-diff probe (PLAN §2 P0.2 STOP condition)")
    mev2_conflicts = _mev2_value_diff_probe(world)
    if mev2_conflicts > 0:
        if args.conflict_policy == "stop":
            logger.error(
                "STOP: %d market_events_v2 VALUE-DIFF conflicts found. "
                "Re-run with --conflict-policy keep_forecasts to proceed.",
                mev2_conflicts,
            )
            world.close()
            return 1
        else:
            logger.warning(
                "conflict-policy=keep_forecasts: proceeding despite %d mev2 conflicts.",
                mev2_conflicts,
            )

    # ── Step 3: copy observations ──────────────────────────────────────────
    logger.info("Step 3: copy observations (INSERT OR IGNORE, target_date >= 2026-05-11)")
    obs_result = _migrate_observations(world, dry_run=args.dry_run)

    # ── Step 4: copy market_events_v2 ─────────────────────────────────────
    logger.info("Step 4: copy market_events_v2 (INSERT OR IGNORE, all rows)")
    mev2_result = _migrate_market_events_v2(world, dry_run=args.dry_run)

    world.close()

    # ── Acceptance checks (PLAN §2 P0 acceptance gates 1-4) ───────────────
    if not args.dry_run:
        logger.info("── Acceptance gate checks ──")
        # Gate 3: forecasts.observations MAX(target_date) >= pre-P0 world MAX
        if obs_result["forecasts_post_max_date"] < obs_result["world_max_target_date"]:
            logger.error(
                "GATE FAIL: forecasts MAX(target_date) %s < world MAX %s",
                obs_result["forecasts_post_max_date"],
                obs_result["world_max_target_date"],
            )
            errors += 1
        else:
            logger.info(
                "GATE OK: forecasts MAX(target_date) %s >= world MAX %s",
                obs_result["forecasts_post_max_date"],
                obs_result["world_max_target_date"],
            )

        # Gate 4: forecasts row count increased by at least (world_post_k1_rows - overlap)
        # Since probe confirmed 0 overlap for observations, delta == world_post_k1_rows
        expected_delta = obs_result["world_post_k1_rows"]
        actual_delta = obs_result["forecasts_post_count"] - obs_result["forecasts_pre_count"]
        if actual_delta < expected_delta and obs_conflicts == 0:
            logger.error(
                "GATE FAIL: forecasts row count delta %d < expected %d",
                actual_delta, expected_delta,
            )
            errors += 1
        else:
            logger.info(
                "GATE OK: forecasts row count delta %d (expected %d world-only rows)",
                actual_delta, expected_delta,
            )

    if errors:
        logger.error("Migration completed WITH %d gate failures. Inspect logs above.", errors)
        return 1

    logger.info("Migration complete. Summary:")
    logger.info("  observations: %s", obs_result)
    logger.info("  market_events_v2: %s", mev2_result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
