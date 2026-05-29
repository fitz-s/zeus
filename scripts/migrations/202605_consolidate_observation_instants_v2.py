# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/OBS_V2_CONSOLIDATION_PLAN.md
#   (Option B, operator-locked) — merge observation_instants_v2 (superset) + legacy
#   observation_instants (subset) into ONE canonical observation_instants.
#   Sequenced AFTER PR3 B3/B5 (pr3_b3_live_table_rename.py:167 explicitly STOPPED
#   at observation_instants_v2 pending this dedicated money-path slice).
# DB target: zeus-world.db (both observation_instants + observation_instants_v2 are
#   world-class tables, co-resident — no cross-DB ATTACH required; intra-DB SAVEPOINT
#   provides the atomicity envelope INV-37 mandates for multi-statement truth rewrites).
# Runner interface: def up(conn: sqlite3.Connection) -> None  (python -m scripts.migrations
#   apply --target 202605_consolidate_observation_instants_v2 [--dry-run])
# Standalone operator receipts: python scripts/migrations/202605_consolidate_observation_instants_v2.py [--execute]
"""Consolidate observation_instants_v2 → observation_instants (v2-wins merge).

Migration semantic policy:
  The canonical end-state is ONE table `observation_instants` carrying the
  observation_instants_v2 SUPERSET schema (authority / data_version /
  provenance_json / running_min / INV-14 identity spine + physical-bounds CHECK).
  The legacy `observation_instants` subset (22 cols) and the v2 superset (32 cols)
  share the SAME natural key UNIQUE(city, source, utc_timestamp).

  v2-WINS on key conflict (Fitz #4 — data provenance over code correctness):
    observation_instants_v2 carries the London-DST fix; legacy rows may hold
    DST-WRONG values. On any (city, source, utc_timestamp) collision the v2 row
    MUST survive and the legacy row MUST be discarded. A naive UNION or an
    INSERT OR REPLACE that let legacy win would silently reintroduce the DST
    settlement bug. This migration enforces v2-wins STRUCTURALLY: the v2 table is
    renamed to the canonical name FIRST (so every v2 row is already present), then
    legacy rows are inserted with INSERT OR IGNORE against the UNIQUE key — so an
    overlapping legacy key is dropped by the constraint, never overwriting v2.

  Legacy-only keys (present in legacy, absent in v2) migrate with provenance
  marked UNVERIFIED (NOT VERIFIED — we did not re-audit their source identity):
    authority='UNVERIFIED', data_version='v1', provenance_json='{}',
    running_min=NULL, and the identity-spine columns left NULL/default. This is
    the honest classification: these rows entered via the OpenMeteo filler /
    rebuild path and were never gated by the A1/A2/A6 native-source writer.

  data_version is PRESERVED as a PROVENANCE VALUE ('v1.wu-native', etc.) on the
  surviving v2 rows — it is the zeus_meta-join lineage tag, NOT the calibration
  dataset_id rename. Legacy-only rows get the schema default 'v1'.

  running_min NULL on legacy-only rows — contract:
    Legacy rows carry no running_min column. Migrated legacy-only rows get
    running_min=NULL. The canonical table's physical-bounds CHECK allows NULL
    (all three temperature columns are nullable). The downstream reader
    (day0_observation_reader) uses MIN(running_min) aggregation over a source;
    SQL MIN ignores NULLs so a mix of NULL and real rows produces the real minimum
    correctly. If ALL rows in a source window have running_min=NULL, MIN returns
    NULL; this surfaces as Day0ObservedExtrema.low_so_far=None, and all downstream
    consumers (evaluator, day0_low_nowcast_signal, observation_client) check for
    None and fail-closed (reject LOW-metric markets). No silent NULL propagation.

  Legacy-row pre-validation (SEV-2a antibody):
    The canonical table's physical-bounds CHECK is (temp_unit='C' AND ...) OR
    (temp_unit='F' AND ...). A legacy row with temp_unit=NULL fails BOTH arms →
    the CHECK fires → INSERT OR IGNORE would silently drop that row. To make such
    drops AUDITABLE, up() calls _validate_legacy_rows_before_merge() BEFORE the
    SAVEPOINT, which raises ValueError listing all violators. Operator must
    investigate and clean those rows before re-running --execute.

Idempotent: re-running after a successful apply is a no-op (the v2 table is gone
and the canonical table already exists with the superset shape). Dry-run prints
row-count receipts without mutating anything.
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "world"

# Subset columns shared by legacy observation_instants and the canonical superset.
# These are the only columns legacy carries; the 10 superset-only columns
# (running_min, authority, data_version, provenance_json, temperature_metric,
# physical_quantity, observation_field, training_allowed, causality_status,
# source_role) take their schema DEFAULTs for legacy-only rows.
_LEGACY_SUBSET_COLUMNS = (
    "city",
    "target_date",
    "source",
    "timezone_name",
    "local_hour",
    "local_timestamp",
    "utc_timestamp",
    "utc_offset_minutes",
    "dst_active",
    "is_ambiguous_local_hour",
    "is_missing_local_hour",
    "time_basis",
    "temp_current",
    "running_max",
    "delta_rate_per_h",
    "temp_unit",
    "station_id",
    "observation_count",
    "raw_response",
    "source_file",
    "imported_at",
)

_LEGACY_TMP = "observation_instants_legacy_premerge_tmp"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def _row_count(conn: sqlite3.Connection, name: str) -> int:
    if not _table_exists(conn, name):
        return -1
    return conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]


def _has_superset_shape(conn: sqlite3.Connection) -> bool:
    """True iff observation_instants already carries the superset provenance cols."""
    if not _table_exists(conn, "observation_instants"):
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(observation_instants)")}
    return {"authority", "data_version", "provenance_json", "running_min"}.issubset(cols)


def _validate_legacy_rows_before_merge(conn: sqlite3.Connection) -> None:
    """Pre-flight: assert every legacy-only row can pass the canonical CHECK.

    The canonical table enforces:
        (temp_unit = 'C' AND temp values within [-90, 60]) OR
        (temp_unit = 'F' AND temp values within [-130, 140])

    A legacy row with temp_unit=NULL (or an unknown unit) fails BOTH arms.
    If INSERT OR IGNORE ran without this pre-check, that row would be SILENTLY
    DROPPED — identical behaviour to a UNIQUE-conflict drop, making the two loss
    categories indistinguishable and breaking the row-count receipt invariant.

    We only need to validate legacy-only rows (those not also in v2): overlapping
    keys are dropped intentionally by the v2-wins UNIQUE conflict, which is the
    correct auditable discard.

    Raises ValueError listing every violating (city, source, utc_timestamp) triplet.
    """
    if not _table_exists(conn, "observation_instants"):
        return  # nothing to validate
    # Legacy-only rows: present in legacy but absent in v2 (by natural key).
    violators = conn.execute(
        """
        SELECT city, source, utc_timestamp, temp_unit, temp_current, running_max
        FROM observation_instants l
        WHERE NOT EXISTS (
            SELECT 1 FROM observation_instants_v2 v
            WHERE v.city = l.city AND v.source = l.source
              AND v.utc_timestamp = l.utc_timestamp
        )
        AND NOT (
            (l.temp_unit = 'C'
                AND (l.temp_current IS NULL OR l.temp_current BETWEEN -90 AND 60)
                AND (l.running_max   IS NULL OR l.running_max   BETWEEN -90 AND 60))
            OR
            (l.temp_unit = 'F'
                AND (l.temp_current IS NULL OR l.temp_current BETWEEN -130 AND 140)
                AND (l.running_max   IS NULL OR l.running_max   BETWEEN -130 AND 140))
        )
        """
    ).fetchall()
    if violators:
        details = "\n".join(
            f"  city={r[0]!r} source={r[1]!r} utc={r[2]!r} "
            f"temp_unit={r[3]!r} temp_current={r[4]} running_max={r[5]}"
            for r in violators[:20]
        )
        suffix = f"\n  ... and {len(violators) - 20} more" if len(violators) > 20 else ""
        raise ValueError(
            f"observation_instants consolidation ABORTED: {len(violators)} legacy-only "
            f"row(s) would be silently dropped by the CHECK constraint (temp_unit NULL "
            f"or temperature out-of-bounds). Investigate and clean these rows before "
            f"re-running --execute.\n{details}{suffix}"
        )


def compute_receipts(conn: sqlite3.Connection) -> dict[str, int]:
    """Pre-merge row-count receipts (read-only)."""
    legacy = _row_count(conn, "observation_instants")
    v2 = _row_count(conn, "observation_instants_v2")
    overlap = -1
    legacy_only = -1
    if legacy >= 0 and v2 >= 0 and _table_exists(conn, "observation_instants_v2"):
        overlap = conn.execute(
            """
            SELECT COUNT(*) FROM observation_instants l
            WHERE EXISTS (
                SELECT 1 FROM observation_instants_v2 v
                WHERE v.city = l.city AND v.source = l.source
                  AND v.utc_timestamp = l.utc_timestamp
            )
            """
        ).fetchone()[0]
        legacy_only = legacy - overlap
    return {
        "legacy_rows": legacy,
        "v2_rows": v2,
        "overlap_keys_v2_wins": overlap,
        "legacy_only_keys_migrate_unverified": legacy_only,
        "expected_post_merge_rows": (v2 + legacy_only) if (v2 >= 0 and legacy_only >= 0) else -1,
    }


def up(conn: sqlite3.Connection) -> None:
    """Apply the v2-wins consolidation on zeus-world.db.

    Steps (inside one SAVEPOINT for all-or-nothing atomicity):
      1. Idempotency: if observation_instants_v2 is already gone AND
         observation_instants has the superset shape, the merge already ran —
         return without mutating.
      2. Rename legacy observation_instants (subset) → tmp.
      3. Rename observation_instants_v2 (superset) → observation_instants.
         Every v2 row is now present under the canonical name (v2-wins basis).
      4. INSERT OR IGNORE legacy-only rows from tmp into the canonical table,
         supplying authority='UNVERIFIED' / data_version='v1' / provenance_json='{}'.
         Overlapping keys are dropped by UNIQUE(city, source, utc_timestamp) —
         v2 survives. NEVER let legacy overwrite v2.
      5. Drop tmp. Recreate canonical indexes/views (idx_observation_instants_city_ts,
         idx_observation_revisions_obs_lookup, observation_instants_current,
         observation_hourly_extrema) so they point at the canonical table.
      6. Repoint observation_revisions.table_name values 'observation_instants_v2'
         → 'observation_instants' (audit-row provenance follows the table rename).
    """
    # Step 1 — idempotency guard
    if not _table_exists(conn, "observation_instants_v2") and _has_superset_shape(conn):
        return  # already consolidated

    if not _table_exists(conn, "observation_instants_v2"):
        raise AssertionError(
            "observation_instants_v2 is absent but observation_instants lacks the "
            "superset shape — DB is in an unexpected state; inspect before retrying."
        )

    # SEV-2a pre-flight: fail-loud on legacy-only rows that would be silently
    # dropped by the canonical CHECK constraint (temp_unit NULL or out-of-bounds).
    # Must run BEFORE the SAVEPOINT so the error surfaces cleanly to the operator
    # without an open SAVEPOINT to roll back.
    _validate_legacy_rows_before_merge(conn)

    conn.execute("SAVEPOINT obs_v2_consolidation")
    try:
        # Step 2 — park the legacy subset table
        if _table_exists(conn, _LEGACY_TMP):
            conn.execute(f"DROP TABLE {_LEGACY_TMP}")
        if _table_exists(conn, "observation_instants"):
            conn.execute(
                f"ALTER TABLE observation_instants RENAME TO {_LEGACY_TMP}"
            )

        # Step 3 — promote the v2 superset to the canonical name.
        # Drop v2-named indexes/views first so the rename does not collide.
        conn.execute("DROP INDEX IF EXISTS idx_observation_instants_v2_city_ts")
        conn.execute("DROP VIEW IF EXISTS observation_instants_current")
        conn.execute("DROP VIEW IF EXISTS observation_hourly_extrema_v2")
        conn.execute("DROP VIEW IF EXISTS observation_hourly_extrema")
        conn.execute(
            "ALTER TABLE observation_instants_v2 RENAME TO observation_instants"
        )

        # Step 4 — merge legacy-only rows (v2-wins via OR IGNORE on UNIQUE key)
        cols_csv = ", ".join(_LEGACY_SUBSET_COLUMNS)
        conn.execute(
            f"""
            INSERT OR IGNORE INTO observation_instants
                ({cols_csv}, running_min, authority, data_version, provenance_json)
            SELECT {cols_csv}, NULL, 'UNVERIFIED', 'v1', '{{}}'
            FROM {_LEGACY_TMP}
            """
        )

        # Step 5 — drop the parked legacy table; recreate canonical indexes/views
        conn.execute(f"DROP TABLE {_LEGACY_TMP}")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observation_instants_city_ts
                ON observation_instants(city, target_date, utc_timestamp)
            """
        )
        conn.execute(
            """
            CREATE VIEW observation_instants_current AS
                SELECT o.*
                FROM observation_instants o
                JOIN zeus_meta m
                  ON m.key = 'observation_data_version'
                 AND o.data_version = m.value
            """
        )
        conn.execute(
            """
            CREATE VIEW observation_hourly_extrema AS
                SELECT
                    o.*,
                    o.running_max AS hour_bucket_max,
                    o.running_min AS hour_bucket_min
                FROM observation_instants o
            """
        )
        conn.execute(
            "DROP INDEX IF EXISTS idx_observation_revisions_obs_v2_lookup"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_observation_revisions_obs_lookup
                ON observation_revisions(table_name, city, source, utc_timestamp, recorded_at)
            """
        )

        # Step 6 — repoint audit-row provenance to the canonical table name.
        if _table_exists(conn, "observation_revisions"):
            conn.execute(
                "UPDATE observation_revisions SET table_name = 'observation_instants' "
                "WHERE table_name = 'observation_instants_v2'"
            )
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT obs_v2_consolidation")
        conn.execute("RELEASE SAVEPOINT obs_v2_consolidation")
        raise
    conn.execute("RELEASE SAVEPOINT obs_v2_consolidation")


def _standalone(argv: list[str] | None = None) -> int:
    """Operator entry point: dry-run receipts by default; --execute to apply."""
    import argparse
    import sys
    from pathlib import Path

    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    parser = argparse.ArgumentParser(
        description="Consolidate observation_instants_v2 → observation_instants (v2-wins)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the merge (default: dry-run row-count receipts only).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to zeus-world.db (default: canonical world connection).",
    )
    args = parser.parse_args(argv)

    if args.db_path:
        conn = sqlite3.connect(args.db_path)  # WRITER_LOCK_DEFER_REVIEW=2026-05-29 operator-invoked migration; daemon lock unavailable in standalone path
    else:
        from src.state.db import get_world_connection

        conn = get_world_connection(write_class="bulk")
    try:
        receipts = compute_receipts(conn)
        print("observation_instants consolidation — PRE-MERGE RECEIPTS")
        for k, v in receipts.items():
            print(f"  {k}: {v}")
        if not args.execute:
            print("\nDRY-RUN (no changes applied). Re-run with --execute to apply.")
            return 0
        up(conn)
        conn.commit()
        post = _row_count(conn, "observation_instants")
        v2_gone = not _table_exists(conn, "observation_instants_v2")
        print("\nAPPLIED. POST-MERGE RECEIPTS")
        print(f"  observation_instants rows: {post}")
        print(f"  observation_instants_v2 dropped: {v2_gone}")
        if receipts["expected_post_merge_rows"] >= 0 and post != receipts["expected_post_merge_rows"]:
            print(
                f"  WARNING: post-merge rows {post} != expected "
                f"{receipts['expected_post_merge_rows']} — investigate."
            )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_standalone())
