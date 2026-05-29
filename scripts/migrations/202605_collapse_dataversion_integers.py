# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Authority basis: operator directive 2026-05-29 — "version must leave the system";
#   investigation verdict (metric_specs.py:44-63): v1 is READ (HIGH's only TIGGE source
#   + a LOW read path), so v1 is NOT droppable → the trailing _vN INTEGER is collapsed
#   while the SEMANTIC distinction (e.g. _contract_window) is preserved.
# DB target:
#   - zeus-forecasts.db : rename data_version VALUES (strip trailing _v<int>) in every
#     table carrying a data_version column. db_writer_lock + intra-DB SAVEPOINT (INV-37).
#   (model_bias_ens was renamed v2→canonical separately on the live world DB — NOT this script.)
# Runner:
#   python scripts/migrations/202605_collapse_dataversion_integers.py            # dry-run (default)
#   python scripts/migrations/202605_collapse_dataversion_integers.py --execute  # apply
"""Collapse version-integer suffixes out of data_version VALUES (zeus-forecasts.db).

WHY (Fitz #4 — data provenance over code correctness):
  `data_version` strings are stored provenance on millions of live rows. They MUST NOT
  be dropped (would orphan distinct lineages: HIGH reads only tigge `_v1`, LOW reads BOTH
  `_v1` and `_contract_window_v2`). The DISEASE is the trailing `_v<int>` counter, not the
  distinction. This migration strips ONLY a trailing `_v<int>` token; the semantic body
  (source family, variable, extraction method incl. `contract_window`) is preserved, so
  every lineage stays distinct. A collision guard ABORTS if two distinct old values would
  map to the same new value (that would mean a real lineage merge — operator must decide).


IDEMPOTENT: a value with no trailing `_v<int>` is left untouched; re-running is a no-op.
ATOMIC: each DB's writes run inside ONE SAVEPOINT; any error rolls the whole DB back.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.db import (  # noqa: E402
    ZEUS_FORECASTS_DB_PATH,
)
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

# SCOPE NOTE: `full_transport_v1` is NOT handled here. It is a CORRECTION-FAMILY tag
# (_FT_FAMILY in evaluator.py), stored in model_bias_ens (renamed v2→canonical separately on live; not touched here) and in
# the corrected-pairs family column — a DIFFERENT column from data_version. Collapsing
# that integer is a separate code-constant + family-column migration; out of scope for
# this data_version-value pass.

# Strip a SINGLE trailing _v<int> (e.g. _v1, _v2, _v0). Anchored at end-of-string only,
# so an internal token like ..._contract_window is never touched; only the final counter.
_TRAILING_VINT = re.compile(r"_v\d+$")

_SAVEPOINT = "collapse_dataversion_integers"


def collapse(value: str) -> str:
    """Strip one trailing _v<int> token. No-op if absent."""
    return _TRAILING_VINT.sub("", value)


def _tables_with_data_version(conn: sqlite3.Connection) -> list[str]:
    out = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
        if "data_version" in cols:
            out.append(name)
    return out


def _plan_forecasts(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Return ({(table, old) -> (new, count)}, {new -> set(old)}) for collision analysis."""
    plan: dict = {}
    new_to_olds: dict = {}
    for table in _tables_with_data_version(conn):
        for old, n in conn.execute(
            f"SELECT data_version, COUNT(*) FROM {table} "
            f"WHERE data_version IS NOT NULL GROUP BY data_version"
        ).fetchall():
            new = collapse(old)
            if new == old:
                continue  # already integer-free
            plan[(table, old)] = (new, n)
            new_to_olds.setdefault((table, new), set()).add(old)
    return plan, new_to_olds


def _assert_no_collision(new_to_olds: dict) -> None:
    bad = {k: v for k, v in new_to_olds.items() if len(v) > 1}
    if bad:
        lines = [
            f"  table={t} new={new!r} <= {sorted(olds)}" for (t, new), olds in bad.items()
        ]
        raise ValueError(
            "ABORT: distinct data_version VALUES would collapse to the SAME new value "
            "(real lineage merge — needs an operator decision, not a blind strip):\n"
            + "\n".join(lines)
        )


def migrate_forecasts(db_path: Path, execute: bool) -> None:
    with db_writer_lock(db_path, WriteClass.BULK):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            plan, new_to_olds = _plan_forecasts(conn)
            _assert_no_collision(new_to_olds)
            total = sum(n for _, n in plan.values())
            print(f"[forecasts] {len(plan)} (table,value) rewrites; {total} rows affected")
            for (table, old), (new, n) in sorted(plan.items()):
                print(f"  {table}: {old!r} -> {new!r}  ({n} rows)")
            if not execute:
                print("[forecasts] DRY-RUN — no writes. Re-run with --execute to apply.")
                return
            conn.execute(f"SAVEPOINT {_SAVEPOINT}")
            try:
                for (table, old), (new, _n) in plan.items():
                    conn.execute(
                        f"UPDATE {table} SET data_version = ? WHERE data_version = ?",
                        (new, old),
                    )
                conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
                conn.commit()
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
                conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
                raise
            # Verify: zero residual trailing _v<int> across all data_version columns.
            residual = []
            for table in _tables_with_data_version(conn):
                for (v,) in conn.execute(
                    f"SELECT DISTINCT data_version FROM {table} "
                    f"WHERE data_version IS NOT NULL"
                ).fetchall():
                    if _TRAILING_VINT.search(v):
                        residual.append((table, v))
            if residual:
                raise RuntimeError(f"POST-CHECK FAILED: residual _v<int> values: {residual}")
            print(f"[forecasts] applied {len(plan)} rewrites; post-check clean.")
        finally:
            conn.close()



def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="apply changes (default is dry-run)")
    args = ap.parse_args()

    forecasts = Path(ZEUS_FORECASTS_DB_PATH)
    print(f"forecasts.db = {forecasts}")
    print(f"mode         = {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print("-" * 70)

    migrate_forecasts(forecasts, args.execute)
    print("-" * 70)
    print("DONE." if args.execute else "DRY-RUN complete. Re-run with --execute to apply.")


if __name__ == "__main__":
    main()
