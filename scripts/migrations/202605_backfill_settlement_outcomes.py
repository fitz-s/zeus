# Lifecycle: created=2026-05-28; last_reviewed=2026-05-28; last_reused=never
# Purpose: Backfill settlement_outcomes from settlements (B3cont rename from settlements_v2).
#   Idempotent: settlement_outcomes has UNIQUE(city, target_date, temperature_metric);
#   INSERT OR IGNORE skips rows already present.
#
# Pre-checks:
#   1. No duplicate (city, target_date, temperature_metric) keys in settlements
#      (would silently lose rows on INSERT OR IGNORE from a different source).
#   2. No rows with temperature_metric IS NULL — settlement_outcomes NOT NULL constraint would
#      reject them; they are skipped and logged (not silently lost).
#
# Column mapping: settlement_outcomes has fewer columns than settlements. The 7 v1-only columns
#   (pm_bin_lo, pm_bin_hi, unit, settlement_source_type, physical_quantity,
#   observation_field, data_version) are merged into provenance_json alongside
#   the existing v1 provenance_json content under key "v1_extra".
#
# Target DB: zeus-forecasts.db (NOT zeus-world.db, post-K1 redesign).
#
# Authority: FIX_SEV1_BUNDLE.md §F15 + PLAN.md WAVE-4 §F15
# B3cont (2026-05-28): renamed from 202605_backfill_settlements_v2.py
# Depends on: fix/migration-runner-2026-05-17 (def up(conn) runner interface)
from __future__ import annotations

import json
import sqlite3

TARGET_DB = "forecasts"


def up(conn: sqlite3.Connection) -> None:
    """Backfill settlement_outcomes from settlements. Idempotent."""
    # Pre-check 1: no duplicates in v1 eligible rows that would cause silent data loss.
    # Scope to temperature_metric IS NOT NULL: NULL-metric rows are always skipped
    # (settlement_outcomes has a NOT NULL constraint) so duplicate (city, target_date, NULL) groups
    # cannot collide in settlement_outcomes and must not abort the migration.
    dupes = conn.execute(
        """SELECT city, target_date, temperature_metric, COUNT(*) as cnt
           FROM settlements
           WHERE temperature_metric IS NOT NULL
           GROUP BY city, target_date, temperature_metric
           HAVING cnt > 1"""
    ).fetchall()
    if dupes:
        raise RuntimeError(
            f"settlements has {len(dupes)} duplicate (city, target_date, temperature_metric) "
            f"keys — aborting backfill to avoid silent data loss. "
            f"First few: {dupes[:3]!r}"
        )

    # Pre-check 2: count NULL temperature_metric rows (cannot land in settlement_outcomes)
    null_metric_count = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE temperature_metric IS NULL"
    ).fetchone()[0]
    if null_metric_count:
        print(
            f"  WARNING: {null_metric_count} settlements rows have NULL temperature_metric "
            f"— skipped (settlement_outcomes requires NOT NULL)"
        )

    # Count v1 rows eligible for backfill
    eligible = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE temperature_metric IS NOT NULL"
    ).fetchone()[0]

    # Count already in settlement_outcomes
    already_in_v2 = conn.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]

    # Backfill: merge v1-only columns into provenance_json under "v1_extra" key.
    # We do row-by-row to safely merge JSON (SQLite has no native json_patch).
    rows = conn.execute(
        """SELECT city, target_date, temperature_metric,
                  market_slug, winning_bin, settlement_value, settlement_source,
                  settled_at, authority, provenance_json,
                  pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
                  physical_quantity, observation_field, data_version
           FROM settlements
           WHERE temperature_metric IS NOT NULL"""
    ).fetchall()

    inserted = 0
    for row in rows:
        (city, target_date, temperature_metric,
         market_slug, winning_bin, settlement_value, settlement_source,
         settled_at, authority, provenance_json_str,
         pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
         physical_quantity, observation_field, data_version) = row

        # Merge v1-only fields into provenance_json.
        # Also stamp explicit backfill provenance so rows inserted via this
        # migration are auditable (reconstruction_method + writer_module) even
        # when the legacy settlements.provenance_json was empty or malformed.
        try:
            provenance = json.loads(provenance_json_str) if provenance_json_str else {}
        except (json.JSONDecodeError, TypeError):
            provenance = {"raw": provenance_json_str}

        provenance["v1_extra"] = {
            "pm_bin_lo": pm_bin_lo,
            "pm_bin_hi": pm_bin_hi,
            "unit": unit,
            "settlement_source_type": settlement_source_type,
            "physical_quantity": physical_quantity,
            "observation_field": observation_field,
            "data_version": data_version,
        }

        # Stamp backfill audit keys only when absent so live-written rows
        # that already carry these fields are not overwritten.
        if "reconstruction_method" not in provenance:
            provenance["reconstruction_method"] = "v1_backfill"
        if "writer_module" not in provenance:
            provenance["writer_module"] = "scripts.migrations.202605_backfill_settlement_outcomes"

        # Normalize authority: settlement_outcomes CHECK only allows VERIFIED/UNVERIFIED/QUARANTINED
        if authority not in ("VERIFIED", "UNVERIFIED", "QUARANTINED"):
            authority = "UNVERIFIED"

        cursor = conn.execute(
            """INSERT OR IGNORE INTO settlement_outcomes
               (city, target_date, temperature_metric,
                market_slug, winning_bin, settlement_value, settlement_source,
                settled_at, authority, provenance_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (city, target_date, temperature_metric,
             market_slug, winning_bin, settlement_value, settlement_source,
             settled_at, authority, json.dumps(provenance)),
        )
        if cursor.rowcount:
            inserted += 1

    after_v2 = conn.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]

    print(
        f"202605_backfill_settlement_outcomes: applied — "
        f"v1 eligible={eligible}, already_in_settlement_outcomes={already_in_v2}, "
        f"inserted={inserted}, settlement_outcomes_total={after_v2}"
    )
