# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator directive 2026-06-09 (override 90-day K1 retention, "都是死数据就清掉");
#   evidence .omc/research/dead_table_live_read_proof.md (21/21 DEAD-SAFE, 0 live readers, all
#   legacy_archived or unregistered-orphan); db_table_ownership.yaml drop-after-2026-08-09.
# delete_by: 2026-07-01 (one-off cleanup; remove after the drop is confirmed durable)
"""One-off: drop 21 audited-dead tables from the 3 canonical DBs, then VACUUM world.db.

SAFETY MODEL
------------
- Dry-run by default. --execute required to apply. --vacuum-world required to reclaim disk.
- DB IDENTITY GATE: each DB is verified by sentinel tables before any drop (refuse if the
  path is not the expected canonical DB).
- NEVER-DROP GUARD: a hardcoded frozenset of canonical live table names; if any target name
  collides with it, the script aborts (defense against a typo dropping a live table).
- PER-TABLE: prints current row count before dropping; uses DROP TABLE IF EXISTS.
- Each DB's drops run inside a single transaction (all-or-nothing per DB).
- Targets are EXACT names from the live-read-proof audit. The non-zero ghosts
  (rescue_events_v2=3, market_events_v2=7964 on trade.db) are pre-PR-S4b residue with zero
  live readers; canonical copies live elsewhere (audit notes).

IRREVERSIBLE. Run only with the DB-writing daemons paused (VACUUM needs an exclusive lock).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_STATE = _REPO / "state"

# --- exact dead-table targets per DB (from dead_table_live_read_proof.md) -----------------
_TARGETS: dict[str, list[str]] = {
    "zeus-world.db": [
        "calibration_pairs_v2_archived_2026_05_11",
        "ensemble_snapshots_v2_archived_2026_05_11",
        "observations_archived_2026_05_11",
        "settlements_archived_2026_05_11",
        "settlements_v2_archived_2026_05_11",
        "market_events_v2_archived_2026_05_11",
        "source_run_archived_2026_05_11",
        "forecast_error_profile",
        "day0_residual_fact",
        "settlements_v2",
    ],
    "zeus_trades.db": [
        "historical_forecasts_v2",
        "rescue_events_v2",
        "market_events_v2",
        "platt_models_v2",
        "observation_instants_v2",
        "settlements_v2",
        "ensemble_snapshots_v2",
    ],
    "zeus-forecasts.db": [
        "migration_progress",
        "observation_instants_v2",
        "rescue_events_v2",
        # NOTE: platt_models on forecasts.db is 0-row and RECREATED empty by
        # tigge_pipeline apply_canonical_schema on next run. Dropping it is cosmetic.
        # Included for validate-boot cleanliness; it will reappear empty (harmless).
        "platt_models",
    ],
}

# --- DB identity sentinels: tables that MUST exist on each DB (prove we have the right file) --
_SENTINELS: dict[str, frozenset[str]] = {
    "zeus-world.db": frozenset({"data_coverage", "job_run", "zeus_meta"}),
    "zeus_trades.db": frozenset({"position_current", "venue_order_facts", "zeus_meta"}),
    "zeus-forecasts.db": frozenset({"calibration_pairs", "settlement_outcomes", "zeus_meta"}),
}

# --- NEVER-DROP guard: canonical live tables. If a target collides, abort. -------------------
_NEVER_DROP: frozenset[str] = frozenset({
    "calibration_pairs", "ensemble_snapshots", "observations", "settlements",
    "settlement_outcomes", "market_events", "source_run", "platt_models_world",
    "observation_instants", "rescue_events", "forecasts", "position_current",
    "venue_order_facts", "venue_trade_facts", "decision_certificates", "data_coverage",
})


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _tables_on_disk(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _verify_identity(db_name: str, conn: sqlite3.Connection) -> None:
    on_disk = _tables_on_disk(conn)
    missing = _SENTINELS[db_name] - on_disk
    if missing:
        sys.exit(f"ABORT {db_name}: missing sentinel tables {sorted(missing)} — not the expected DB.")


def _guard_targets(db_name: str) -> None:
    bad = [t for t in _TARGETS[db_name] if t in _NEVER_DROP]
    if bad:
        sys.exit(f"ABORT {db_name}: target list intersects NEVER_DROP canonical tables {bad}.")


def _size(path: Path) -> str:
    b = path.stat().st_size
    return f"{b/1e9:.2f} GB" if b >= 1e9 else f"{b/1e6:.1f} MB"


def main() -> None:
    ap = argparse.ArgumentParser(description="Drop audited-dead tables + optional world.db VACUUM.")
    ap.add_argument("--execute", action="store_true", help="Apply DROPs. Default: dry-run.")
    ap.add_argument("--vacuum-world", action="store_true", help="VACUUM zeus-world.db after drops (needs exclusive lock).")
    args = ap.parse_args()
    mode = "EXECUTE (irreversible)" if args.execute else "DRY-RUN (no writes)"
    print("=" * 72)
    print(f"Zeus dead-table cleanup  |  mode: {mode}")
    print("=" * 72)

    total_rows = 0
    for db_name, targets in _TARGETS.items():
        path = _STATE / db_name
        if not path.exists():
            sys.exit(f"ABORT: {path} not found.")
        _guard_targets(db_name)
        conn = _connect(path)
        try:
            _verify_identity(db_name, conn)
            on_disk = _tables_on_disk(conn)
            print(f"\n[{db_name}]  size={_size(path)}  identity OK")
            present = [t for t in targets if t in on_disk]
            absent = [t for t in targets if t not in on_disk]
            for t in present:
                n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                total_rows += n
                print(f"    DROP {t:<48} rows={n}")
            for t in absent:
                print(f"    skip {t:<48} (already absent)")
            if args.execute and present:
                with conn:  # single transaction per DB
                    for t in present:
                        conn.execute(f'DROP TABLE IF EXISTS "{t}"')
                print(f"    -> dropped {len(present)} table(s) on {db_name}")
        finally:
            conn.close()

    print(f"\nTotal rows in dropped tables: {total_rows:,}")

    if args.vacuum_world:
        wpath = _STATE / "zeus-world.db"
        print(f"\nVACUUM {wpath} (before={_size(wpath)}) — exclusive lock, may take minutes...")
        if args.execute:
            conn = _connect(wpath)
            try:
                conn.execute("VACUUM")
                conn.execute("PRAGMA integrity_check")
                ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
                print(f"    VACUUM done. after={_size(wpath)}  integrity_check={ok}")
            finally:
                conn.close()
        else:
            print("    (dry-run: VACUUM not executed)")

    if not args.execute:
        print("\nDry-run complete. Re-run with --execute [--vacuum-world] to apply.")


if __name__ == "__main__":
    main()
