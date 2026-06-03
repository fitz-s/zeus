# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: project_settlement_store_split_b3cont_2026_06_02 (memory);
#   B3cont rename (settlements -> settlements_v2 -> settlement_outcomes, 2026-05-28);
#   D-S1 first-class settlement_unit/settlement_station (task #17 /
#   202605_add_settlement_outcomes_station_unit.py). Supersedes the
#   never-ran scripts/migrations/202605_backfill_settlement_outcomes.py
#   (which buried `unit` in provenance instead of the first-class settlement_unit column).
#
# Purpose: Populate the DECLARED-canonical (but EMPTY) settlement_outcomes table in
#   state/zeus-forecasts.db from the live-written legacy `settlements` table, so the
#   readers that point at settlement_outcomes (live exit-discount monitor_refresh.py:1372,
#   bias ens_bias_repo.py:409, replay, attribution) stop silently degrading.
#
# MOST-CORRECT method (vs the committed migration):
#   1. SOURCE = settlements (the live, reconstructed-from-obs+settlement_semantics truth,
#      written by harvester_truth_writer.py:579). Preserve authority labels
#      (VERIFIED + QUARANTINED) verbatim — readers filter authority='VERIFIED' themselves.
#   2. settlement_unit <- settlements.unit  (FIRST-CLASS column; CHECK IN ('F','C')).
#      The committed migration dropped unit into provenance_json — this fixes that so
#      pairing/derivation reads the typed column, not a parse fallback.
#   3. settlement_station <- NULL (settlements has no station column; derived at read).
#   4. outcome_type <- NULL (a separate dedicated backfill handles outcome_type).
#   5. v1-only columns (pm_bin_lo, pm_bin_hi, settlement_source_type, physical_quantity,
#      observation_field, data_version) -> provenance_json["v1_extra"] for full audit.
#   6. Idempotent: INSERT OR IGNORE on UNIQUE(city, target_date, temperature_metric).
#   7. Atomic SAVEPOINT; dry-run by DEFAULT (prints receipts + rolls back); --execute commits.
#
# Run:
#   dry-run (default):  python scripts/backfill_settlement_outcomes_canonical_2026_06_02.py
#   execute:            python scripts/backfill_settlement_outcomes_canonical_2026_06_02.py --execute
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / "state" / "zeus-forecasts.db"

_BASE_COLS = (
    "city", "target_date", "temperature_metric", "market_slug", "winning_bin",
    "settlement_value", "settlement_source", "settled_at", "authority",
)
_V1_EXTRA_COLS = (
    "pm_bin_lo", "pm_bin_hi", "settlement_source_type",
    "physical_quantity", "observation_field", "data_version",
)


def _preflight(conn: sqlite3.Connection) -> dict:
    so_rows = conn.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]
    s_total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    eligible = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE temperature_metric IS NOT NULL"
    ).fetchone()[0]
    null_metric = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE temperature_metric IS NULL"
    ).fetchone()[0]
    dupes = conn.execute(
        """SELECT city, target_date, temperature_metric, COUNT(*) c
           FROM settlements WHERE temperature_metric IS NOT NULL
           GROUP BY city, target_date, temperature_metric HAVING c > 1"""
    ).fetchall()
    bad_metric = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE temperature_metric NOT IN ('high','low')"
    ).fetchone()[0]
    bad_unit = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE unit IS NOT NULL AND unit NOT IN ('F','C')"
    ).fetchone()[0]
    return {
        "settlement_outcomes_rows_before": so_rows,
        "settlements_total": s_total,
        "eligible": eligible,
        "null_metric_skipped": null_metric,
        "dupes": dupes,
        "bad_metric_check_violation": bad_metric,
        "bad_unit_check_violation": bad_unit,
    }


def run(execute: bool) -> int:
    if not _DB.exists():
        print(f"FATAL: {_DB} not found", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(_DB))
    try:
        pf = _preflight(conn)
        print("=== PREFLIGHT ===")
        for k, v in pf.items():
            print(f"  {k}: {v if k != 'dupes' else len(v)}")
        if pf["dupes"]:
            print(f"ABORT: {len(pf['dupes'])} duplicate (city,target_date,metric) keys in settlements "
                  f"— would silently lose rows. First: {pf['dupes'][:3]!r}", file=sys.stderr)
            return 3
        if pf["bad_metric_check_violation"]:
            print(f"ABORT: {pf['bad_metric_check_violation']} settlements rows fail temperature_metric "
                  f"CHECK IN ('high','low')", file=sys.stderr)
            return 3
        if pf["bad_unit_check_violation"]:
            print(f"ABORT: {pf['bad_unit_check_violation']} settlements rows fail unit CHECK IN ('F','C')",
                  file=sys.stderr)
            return 3

        rows = conn.execute(
            f"""SELECT {', '.join(_BASE_COLS)}, unit, provenance_json,
                       {', '.join(_V1_EXTRA_COLS)}
                FROM settlements
                WHERE temperature_metric IS NOT NULL"""
        ).fetchall()

        conn.execute("SAVEPOINT backfill_so")
        inserted = 0
        unit_populated = 0
        for r in rows:
            base = dict(zip(_BASE_COLS, r[:len(_BASE_COLS)]))
            unit = r[len(_BASE_COLS)]
            prov_str = r[len(_BASE_COLS) + 1]
            v1 = dict(zip(_V1_EXTRA_COLS, r[len(_BASE_COLS) + 2:]))

            try:
                provenance = json.loads(prov_str) if prov_str else {}
                if not isinstance(provenance, dict):
                    provenance = {"raw": prov_str}
            except (json.JSONDecodeError, TypeError):
                provenance = {"raw": prov_str}
            provenance["v1_extra"] = {**v1, "unit": unit}
            provenance.setdefault("reconstruction_method", "settlements_canonical_backfill")
            provenance.setdefault(
                "writer_module", "scripts.backfill_settlement_outcomes_canonical_2026_06_02"
            )

            authority = base["authority"]
            if authority not in ("VERIFIED", "UNVERIFIED", "QUARANTINED"):
                authority = "UNVERIFIED"
            settlement_unit = unit if unit in ("F", "C") else None
            if settlement_unit is not None:
                unit_populated += 1

            cur = conn.execute(
                """INSERT OR IGNORE INTO settlement_outcomes
                   (city, target_date, temperature_metric, market_slug, winning_bin,
                    settlement_value, settlement_source, settled_at, authority,
                    provenance_json, settlement_unit, settlement_station, outcome_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                (base["city"], base["target_date"], base["temperature_metric"],
                 base["market_slug"], base["winning_bin"], base["settlement_value"],
                 base["settlement_source"], base["settled_at"], authority,
                 json.dumps(provenance), settlement_unit),
            )
            if cur.rowcount:
                inserted += 1

        after = conn.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]
        so_unit = conn.execute(
            "SELECT COUNT(*) FROM settlement_outcomes WHERE settlement_unit IS NOT NULL"
        ).fetchone()[0]
        so_auth = conn.execute(
            "SELECT authority, COUNT(*) FROM settlement_outcomes GROUP BY authority"
        ).fetchall()
        sample = conn.execute(
            """SELECT city, target_date, temperature_metric, winning_bin, settlement_value,
                      settlement_unit, authority
               FROM settlement_outcomes ORDER BY settled_at DESC LIMIT 3"""
        ).fetchall()

        print("\n=== RESULT (in-transaction) ===")
        print(f"  rows_examined: {len(rows)}")
        print(f"  inserted (new): {inserted}")
        print(f"  settlement_outcomes total after: {after}")
        print(f"  settlement_unit populated: {so_unit} (of {after})")
        print(f"  authority dist: {so_auth}")
        print(f"  sample newest: {sample}")

        if execute:
            conn.execute("RELEASE SAVEPOINT backfill_so")
            conn.commit()
            print("\n=== COMMITTED ===")
        else:
            conn.execute("ROLLBACK TO SAVEPOINT backfill_so")
            conn.execute("RELEASE SAVEPOINT backfill_so")
            conn.rollback()
            print("\n=== DRY-RUN: rolled back (no write). Re-run with --execute to commit. ===")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="Commit the backfill (default: dry-run + rollback).")
    args = ap.parse_args()
    sys.exit(run(execute=args.execute))
