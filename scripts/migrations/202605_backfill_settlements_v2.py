# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: Backfill settlements_v2 from settlements (historical 1583-row gap).
#   Idempotent: settlements_v2 has UNIQUE(city, target_date, temperature_metric);
#   INSERT OR IGNORE skips rows already present.
#
# Pre-checks:
#   1. No duplicate (city, target_date, temperature_metric) keys in settlements
#      (would silently lose rows on INSERT OR IGNORE from a different source).
#   2. No rows with temperature_metric IS NULL — v2 NOT NULL constraint would
#      reject them; they are skipped and logged (not silently lost).
#
# Column mapping: v2 has fewer columns than v1. The 7 v1-only columns
#   (pm_bin_lo, pm_bin_hi, unit, settlement_source_type, physical_quantity,
#   observation_field, data_version) are merged into provenance_json alongside
#   the existing v1 provenance_json content under key "v1_extra".
#
# Target DB: zeus-forecasts.db (NOT zeus-world.db, post-K1 redesign).
#
# Authority: FIX_SEV1_BUNDLE.md §F15 + PLAN.md WAVE-4 §F15
# Depends on: fix/migration-runner-2026-05-17 (def up(conn) runner interface)
from __future__ import annotations

import json
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Backfill settlements_v2 from settlements. Idempotent."""
    # Pre-check 1: no duplicates in v1 eligible rows that would cause silent data loss.
    # Scope to temperature_metric IS NOT NULL: NULL-metric rows are always skipped
    # (v2 has a NOT NULL constraint) so duplicate (city, target_date, NULL) groups
    # cannot collide in settlements_v2 and must not abort the migration.
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

    # Pre-check 2: count NULL temperature_metric rows (cannot land in v2)
    null_metric_count = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE temperature_metric IS NULL"
    ).fetchone()[0]
    if null_metric_count:
        print(
            f"  WARNING: {null_metric_count} settlements rows have NULL temperature_metric "
            f"— skipped (v2 requires NOT NULL)"
        )

    # Count v1 rows eligible for backfill
    eligible = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE temperature_metric IS NOT NULL"
    ).fetchone()[0]

    # Count already in v2
    already_in_v2 = conn.execute("SELECT COUNT(*) FROM settlements_v2").fetchone()[0]

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
            provenance["writer_module"] = "scripts.migrations.202605_backfill_settlements_v2"

        # Normalize authority: v2 CHECK only allows VERIFIED/UNVERIFIED/QUARANTINED
        if authority not in ("VERIFIED", "UNVERIFIED", "QUARANTINED"):
            authority = "UNVERIFIED"

        cursor = conn.execute(
            """INSERT OR IGNORE INTO settlements_v2
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

    after_v2 = conn.execute("SELECT COUNT(*) FROM settlements_v2").fetchone()[0]

    print(
        f"202605_backfill_settlements_v2: applied — "
        f"v1 eligible={eligible}, already_in_v2={already_in_v2}, "
        f"inserted={inserted}, v2_total={after_v2}"
    )
