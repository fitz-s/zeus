#!/usr/bin/env python3
# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: single pre-flight the operator runs before ANY registry-driven drop/retention,
#   or as a boot preflight, that fails closed on the two money-data-loss classes this audit
#   surfaced: (1) dangling foreign keys that silently freeze a table (froze trade_decisions),
#   (2) manifest rot — db_table_ownership.yaml labels a live table droppable (a drop keyed on
#   the label would delete live money data; the critic's BLOCKING finding).
# Authority: implementation/W0b + tests/test_no_dangling_foreign_keys.py + critic_matrix_review.md.
"""Run the DB safety gates. Exit 0 only if both are clean; non-zero (and print) otherwise."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DB_FILES = ("zeus_trades.db", "zeus-forecasts.db", "zeus-world.db")


def _dangling_fks(state_dir: Path) -> list[tuple[str, str, str, str]]:
    """(db_file, child, column, missing_parent) across the three canonical DBs."""
    from tests.test_no_dangling_foreign_keys import find_dangling_foreign_keys  # reuse the checker
    out = []
    for f in _DB_FILES:
        p = state_dir / f
        if not p.exists():
            continue
        conn = sqlite3.connect(f"file:{p}?mode=ro&cache=private", uri=True, timeout=0.25, isolation_level=None)
        try:
            conn.execute("PRAGMA query_only=ON"); conn.execute("PRAGMA mmap_size=0")
            for child, col, parent in find_dangling_foreign_keys(conn):
                out.append((f, child, col, parent))
        finally:
            conn.close()
    return out


def _stray_db_files(state_dir: Path) -> list[tuple[str, int]]:
    """Decoy DB files that shadow a canonical one under the opposite dash/underscore spelling.
    zeus_trades.db (canonical) vs zeus-trades.db (decoy), etc. A wrong-separator open under
    fail-open connect creates/uses one of these EMPTY files instead of erroring."""
    canonical = set(_DB_FILES)
    decoys = []
    for f in canonical:
        # the opposite-separator spelling of each canonical name
        alt = f.replace("_", "-") if "_" in f else f.replace("-", "_")
        if alt == f:
            continue
        p = state_dir / alt
        if p.exists():
            decoys.append((alt, p.stat().st_size))
    return decoys


def _state_dir(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg)
    try:
        from src.state.db_paths import primary_trade_db_path
        return primary_trade_db_path().parent
    except Exception:
        return ROOT / "state"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="DB safety gates preflight (dangling FK + manifest rot).")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--manifest", default=str(ROOT / "architecture" / "db_table_ownership.yaml"))
    a = ap.parse_args(argv)
    state = _state_dir(a.state_dir)

    failed = False

    dangling = _dangling_fks(state)
    if dangling:
        failed = True
        print(f"GATE FAIL — {len(dangling)} dangling foreign key(s) (silently freeze the child table):")
        for f, child, col, parent in dangling:
            print(f"  [{f}] {child}.{col} -> MISSING {parent}")
    else:
        print("GATE OK — no dangling foreign keys.")

    strays = _stray_db_files(state)
    if strays:
        failed = True
        print(f"GATE FAIL — {len(strays)} stray/decoy DB file(s) (wrong dash/underscore separator; "
              "opening one silently yields an EMPTY DB, the fail-open-connect hazard):")
        for name, size in strays:
            print(f"  {name}  ({size} bytes)")
    else:
        print("GATE OK — no stray/decoy DB files.")

    from scripts.ops.audit_manifest_rot import audit as _rot_audit
    rot = _rot_audit(Path(a.manifest), state)
    if rot:
        failed = True
        print(f"GATE FAIL — {len(rot)} droppable-labeled table(s) are actually LIVE "
              "(a registry drop would delete live data). Run audit_manifest_rot.py for the list.")
    else:
        print("GATE OK — no manifest rot.")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
