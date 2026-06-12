#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做") —
#   kills the PRAGMA-trial-and-error tax (6+ failed probes on wrong column names in one
#   night). READ-ONLY over the three live DBs (mode=ro): reads sqlite_master + PRAGMA
#   table_info only; emits docs/reference/schema_cheatsheet.md. Registered in
#   SQLITE_CONNECT_ALLOWLIST (src/state/db_writer_lock.py).
"""Generate docs/reference/schema_cheatsheet.md from the three live DBs.

For each DB (zeus-world / zeus_trades / zeus-forecasts), for each base table:
  - one line per table: column names + types, wrapped at 100 chars;
  - a row count, SKIPPED ('-') for tables estimated over 1M rows (the estimate
    is max(rowid) when the table has a rowid, else exact COUNT for small ones).

This file pins NAMES for humans/agents; the schema-fingerprint test pins the
SCHEMA itself. Regenerate after schema changes.

USAGE
    .venv/bin/python scripts/gen_schema_cheatsheet.py
    .venv/bin/python scripts/gen_schema_cheatsheet.py --stdout   # don't write file
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone

STATE = "/Users/leofitz/zeus/state"
DBS = [
    ("zeus-world.db", f"{STATE}/zeus-world.db"),
    ("zeus_trades.db", f"{STATE}/zeus_trades.db"),
    ("zeus-forecasts.db", f"{STATE}/zeus-forecasts.db"),
]
OUT_PATH = "docs/reference/schema_cheatsheet.md"
ROWCOUNT_SKIP_THRESHOLD = 1_000_000


def ro(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def base_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [(r["name"], (r["type"] or "").strip() or "?") for r in rows]


def row_estimate(conn: sqlite3.Connection, table: str) -> str:
    """Cheap row count: max(rowid) estimate; '-' if estimated > 1M.

    A WITHOUT ROWID table has no rowid; for those we fall back to an exact
    COUNT only when it is cheap enough (estimated small via a LIMIT probe).
    Never runs a full COUNT(*) on a >1M-row table.
    """
    try:
        row = conn.execute(f"SELECT max(rowid) FROM '{table}'").fetchone()
        est = row[0] if row is not None else None
        if est is None:
            # No rowid (WITHOUT ROWID) or empty. Probe size with a bounded count.
            probe = conn.execute(
                f"SELECT count(*) FROM (SELECT 1 FROM '{table}' "
                f"LIMIT {ROWCOUNT_SKIP_THRESHOLD + 1})"
            ).fetchone()[0]
            if probe > ROWCOUNT_SKIP_THRESHOLD:
                return "-"
            return str(probe)
        if est > ROWCOUNT_SKIP_THRESHOLD:
            return "-"
        # Small enough: exact count (cheap on a <=1M table).
        exact = conn.execute(f"SELECT count(*) FROM '{table}'").fetchone()[0]
        return str(exact)
    except sqlite3.Error:
        return "?"


def render_table(conn: sqlite3.Connection, table: str) -> str:
    cols = columns(conn, table)
    est = row_estimate(conn, table)
    col_str = ", ".join(f"{n}:{t}" for n, t in cols)
    wrapped = textwrap.wrap(
        col_str, width=100, break_long_words=False, break_on_hyphens=False
    ) or ["(no columns)"]
    head = f"- **{table}**  (rows≈{est}, cols={len(cols)})"
    body = "\n".join(f"    {line}" for line in wrapped)
    return f"{head}\n{body}"


def build() -> str:
    ts = datetime.now(timezone.utc).isoformat()
    out: list[str] = []
    out.append("# Zeus live-DB schema cheatsheet")
    out.append("")
    out.append(f"Generated: `{ts}`")
    out.append("Generator: `.venv/bin/python scripts/gen_schema_cheatsheet.py`")
    out.append("")
    out.append("> Regenerate after schema changes. The schema-fingerprint test pins the "
               "SCHEMA; this file pins the NAMES (column names + types) for humans/agents, "
               "to kill the PRAGMA-trial-and-error tax. Row counts are cheap estimates "
               "(max(rowid)); tables over 1M rows show `rows≈-`. READ-ONLY (mode=ro).")
    out.append("")
    for db_label, db_path in DBS:
        out.append(f"## {db_label}")
        out.append("")
        try:
            conn = ro(db_path)
        except sqlite3.Error as exc:
            out.append(f"_ERR opening {db_label}: {type(exc).__name__}: {exc}_")
            out.append("")
            continue
        try:
            tables = base_tables(conn)
            out.append(f"_{len(tables)} base tables_")
            out.append("")
            for t in tables:
                out.append(render_table(conn, t))
            out.append("")
        finally:
            conn.close()
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the live-DB schema cheatsheet.")
    ap.add_argument("--stdout", action="store_true", help="print to stdout, do not write file")
    args = ap.parse_args(argv)
    content = build()
    if args.stdout:
        sys.stdout.write(content)
        return 0
    with open(OUT_PATH, "w") as fh:
        fh.write(content)
    sys.stdout.write(f"wrote {OUT_PATH} ({len(content)} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
