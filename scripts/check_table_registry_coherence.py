#!/usr/bin/env python3
# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §1.2 #2 (REV 4)
"""CI hook: bidirectional set-equality between registry and init_schema output.

Runs on every PR touching src/state/**, architecture/db_table_ownership.yaml,
or architecture/world_schema_manifest.yaml.

Exit 0 = PASS. Exit 1 = FAIL (prints diff). Exit 2 = SETUP ERROR.

Usage:
    python3 scripts/check_table_registry_coherence.py
    python3 scripts/check_table_registry_coherence.py --verbose
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _init_world(verbose: bool) -> frozenset[str]:
    import src.state.db as db_mod
    conn = sqlite3.connect(":memory:")
    db_mod.init_schema_world_only(conn)
    tables = frozenset(
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    conn.close()
    if verbose:
        print(f"  [world] init_schema_world_only -> {len(tables)} tables")
    return tables


def _init_forecasts(verbose: bool) -> frozenset[str]:
    import src.state.db as db_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        wc_path = Path(tmpdir) / "zeus-world.db"
        fc_path = Path(tmpdir) / "zeus-forecasts.db"
        conn_w = sqlite3.connect(str(wc_path))
        db_mod.init_schema(conn_w)
        conn_w.close()
        orig_w, orig_f = db_mod.ZEUS_WORLD_DB_PATH, db_mod.ZEUS_FORECASTS_DB_PATH
        try:
            db_mod.ZEUS_WORLD_DB_PATH = wc_path
            db_mod.ZEUS_FORECASTS_DB_PATH = fc_path
            conn_f = sqlite3.connect(str(fc_path))
            db_mod.init_schema_forecasts(conn_f)
            tables = frozenset(
                row[0]
                for row in conn_f.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            )
            conn_f.close()
        finally:
            db_mod.ZEUS_WORLD_DB_PATH = orig_w
            db_mod.ZEUS_FORECASTS_DB_PATH = orig_f
    if verbose:
        print(f"  [forecasts] init_schema_forecasts -> {len(tables)} tables")
    return tables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    verbose = args.verbose

    try:
        from src.state.table_registry import DBIdentity, SchemaClass, _REGISTRY, tables_for
    except Exception as exc:
        print(f"SETUP ERROR: failed to import table_registry: {exc}", file=sys.stderr)
        return 2

    failed = False

    # --- World side ---
    try:
        disk_world = _init_world(verbose)
    except Exception as exc:
        print(f"SETUP ERROR: init_schema_world_only failed: {exc}", file=sys.stderr)
        return 2

    legacy_archived_world = frozenset(
        name for (name, db_id), entry in _REGISTRY.items()
        if db_id == DBIdentity.WORLD and entry.schema_class == SchemaClass.LEGACY_ARCHIVED
    )
    disk_world_non_ghost = disk_world - legacy_archived_world
    registry_world = tables_for(DBIdentity.WORLD)

    missing_world = registry_world - disk_world_non_ghost
    extra_world = disk_world_non_ghost - registry_world

    if missing_world:
        print(f"FAIL [world direction 1]: registry declares these but init_schema_world_only doesn't create them:")
        for t in sorted(missing_world):
            print(f"  - {t}")
        failed = True
    if extra_world:
        print(f"FAIL [world direction 2]: init_schema_world_only creates these but registry doesn't declare them:")
        for t in sorted(extra_world):
            print(f"  - {t}")
        failed = True
    if not missing_world and not extra_world:
        if verbose:
            print(f"PASS [world]: {len(registry_world)} tables match registry")

    # --- Forecasts side ---
    try:
        disk_forecasts = _init_forecasts(verbose)
    except Exception as exc:
        print(f"SETUP ERROR: init_schema_forecasts failed: {exc}", file=sys.stderr)
        return 2

    registry_forecasts = tables_for(DBIdentity.FORECASTS)
    missing_forecasts = registry_forecasts - disk_forecasts
    extra_forecasts = disk_forecasts - registry_forecasts

    if missing_forecasts:
        print(f"FAIL [forecasts direction 1]: registry declares these but init_schema_forecasts doesn't create them:")
        for t in sorted(missing_forecasts):
            print(f"  - {t}")
        failed = True
    if extra_forecasts:
        print(f"FAIL [forecasts direction 2]: init_schema_forecasts creates these but registry doesn't declare them:")
        for t in sorted(extra_forecasts):
            print(f"  - {t}")
        failed = True
    if not missing_forecasts and not extra_forecasts:
        if verbose:
            print(f"PASS [forecasts]: {len(registry_forecasts)} tables match registry")

    if failed:
        print("\nFIX: update architecture/db_table_ownership.yaml to match deployed schema.")
        return 1

    if verbose:
        print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
