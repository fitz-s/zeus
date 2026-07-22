#!/usr/bin/env python3
# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: W2 manifest-rot gate (critic BLOCKING finding). db_table_ownership.yaml labels
#   108 tables `legacy_archived` and notes ~138 as "ghost"/"drop after <date>". The critic
#   proved some are LIVE (world decision_certificates = the money-certificate authority;
#   decision_log = a live money-recovery table). A drop/retention script keyed on those
#   labels would DELETE LIVE MONEY DATA. This gate refuses to trust a droppable label
#   without a writers/readers + row-count recheck: it reports every table labeled
#   droppable that actually has rows or a live writer in src/.
# Authority: implementation/critic_matrix_review.md (M1/M3, B1) + the writers/readers
#   re-derivation rule in EXECUTION_MASTER.md.
"""Audit db_table_ownership.yaml for rot: tables labeled droppable that are actually live.

Read-only. Run before ANY registry-driven drop/retention. Exit 1 if any rot is found."""
from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
_DB_FILE = {"trade": "zeus_trades.db", "world": "zeus-world.db", "forecasts": "zeus-forecasts.db"}
_GHOST_NOTE = re.compile(r"ghost|drop after|residual drift|deprecated", re.IGNORECASE)


def _load_entries(manifest: Path) -> list[dict]:
    try:
        import yaml
        doc = yaml.safe_load(manifest.read_text())
        # tables live under some top-level key(s); collect every mapping with a 'name'+'db'.
        out = []
        def walk(x):
            if isinstance(x, dict):
                if "name" in x and "db" in x:
                    out.append(x)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(doc)
        return out
    except Exception as e:
        print(f"WARN: yaml parse failed ({e}); falling back to line scan", file=sys.stderr)
        return _load_entries_lines(manifest)


def _load_entries_lines(manifest: Path) -> list[dict]:
    entries, cur = [], None
    for line in manifest.read_text().splitlines():
        m = re.match(r"\s*-\s+name:\s*(\S+)", line)
        if m:
            if cur:
                entries.append(cur)
            cur = {"name": m.group(1), "notes": ""}
            continue
        if cur is None:
            continue
        for f in ("db", "schema_class"):
            mm = re.match(rf"\s+{f}:\s*(\S+)", line)
            if mm:
                cur[f] = mm.group(1)
        if re.match(r"\s+notes:", line) or (cur.get("_in_notes")):
            cur["notes"] = cur.get("notes", "") + " " + line.strip()
    if cur:
        entries.append(cur)
    return entries


def _has_rows(state_dir: Path, db_file: str, table: str) -> Optional[bool]:
    path = state_dir / db_file
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro&cache=private", uri=True, timeout=0.25, isolation_level=None)
    try:
        conn.execute("PRAGMA query_only=ON"); conn.execute("PRAGMA busy_timeout=250"); conn.execute("PRAGMA mmap_size=0")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return None  # absent
        return conn.execute(f'SELECT EXISTS(SELECT 1 FROM "{table}" LIMIT 1)').fetchone()[0] == 1
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _writer_pattern(table: str) -> str:
    """Regex text for an INSERT/REPLACE INTO <table> writer statement. Matches bare INSERT
    INTO, INSERT OR REPLACE/IGNORE INTO, and REPLACE INTO; an optional schema qualifier
    (main.<table>); double-quoted, backtick-, or bracket-quoted identifiers; and arbitrary
    whitespace/newlines between tokens. Case-insensitivity is applied by the caller."""
    t = re.escape(table)
    name = rf'(?:"{t}"|`{t}`|\[{t}\]|\b{t}\b)'
    qualifier_ident = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+)'
    qualifier = rf'(?:{qualifier_ident}\s*\.\s*)?'
    verb = r'(?:INSERT\s+OR\s+(?:REPLACE|IGNORE)\s+INTO|INSERT\s+INTO|REPLACE\s+INTO)'
    return rf'{verb}\s+{qualifier}{name}'


def _has_writer(table: str) -> bool:
    """A live writer statement targeting `table` in src/ (the strongest 'this is live' signal).

    HEURISTIC TEXT SCAN, NOT A SQL PARSER: it cannot see writes built from f-strings/string
    concatenation, helper-generated SQL, or a repository/ORM abstraction that never spells the
    table name next to INTO in source. A False result here is NOT proof no writer exists — the
    operator must still reason about dynamic/indirect writers before trusting this gate."""
    pattern = _writer_pattern(table)
    # Fail-CLOSED: a scan we cannot run must NOT read as "no writer" — that would
    # green-light a drop. rg/grep return 0=match, 1=no-match, >=2=error. If a
    # scanner errors, or neither rg nor grep is installed, raise so the operator
    # resolves it rather than silently proceeding on a false negative.
    for argv in (
        ["rg", "-l", "-i", "--multiline", pattern, str(ROOT / "src")],
        ["grep", "-rliE", pattern, str(ROOT / "src")],
    ):
        try:
            r = subprocess.run(argv, capture_output=True, text=True)
        except FileNotFoundError:
            continue  # scanner not installed — try the fallback
        if r.returncode >= 2:
            raise RuntimeError(
                f"_has_writer: {argv[0]} failed (rc={r.returncode}) scanning for "
                f"{table!r}: {r.stderr.strip()[:200]}"
            )
        return bool(r.stdout.strip())
    raise RuntimeError(
        "_has_writer: neither 'rg' nor 'grep' is available — cannot scan for live "
        f"writers of {table!r}. Install one, or resolve writer-presence manually "
        "(this gate must not fail open)."
    )


_OWNING_CLASSES = {"trade_class", "world_class", "forecast_class", "backtest_class"}


def audit(manifest: Path, state_dir: Path) -> list[dict]:
    """Genuine manifest rot: a table labeled droppable (legacy_archived / ghost-noted) that
    is actually LIVE **and has no canonical (owning-class) sibling registration for the same
    name in any DB**. A legacy_archived copy WITH a canonical sibling is a legitimate ghost
    (correct dual-registration, pending drop) — NOT rot; flagging those over-reports. The
    money-data-loss case is a droppable label on a table whose only/authoritative home it is."""
    print("WARN: live_writer uses a HEURISTIC text scan (INSERT/REPLACE INTO <table>), not a "
          "SQL parser — it cannot see writes built from f-strings/string concatenation, "
          "helper-generated SQL, or a repository/ORM abstraction. live_writer=False is NOT "
          "proof no writer exists; the operator must still reason about dynamic writers.",
          file=sys.stderr)
    entries = [e for e in _load_entries(manifest) if e.get("name") and e.get("db") in _DB_FILE]
    # names that have a canonical (owning-class) registration somewhere
    canonical_names = {
        e["name"] for e in entries if (e.get("schema_class") or "").strip() in _OWNING_CLASSES
    }
    rot = []
    for e in entries:
        name, db = e["name"], e["db"]
        sc = (e.get("schema_class") or "").strip()
        notes = e.get("notes") or ""
        droppable_label = (sc == "legacy_archived") or bool(_GHOST_NOTE.search(notes))
        if not droppable_label:
            continue
        if name in canonical_names:
            continue  # legitimate ghost — a canonical sibling owns it; this copy is droppable
        has_rows = _has_rows(state_dir, _DB_FILE[db], name)
        has_writer = _has_writer(name)
        if has_rows or has_writer:
            rot.append({"name": name, "db": db, "schema_class": sc,
                        "has_rows": has_rows, "live_writer": has_writer,
                        "label": "legacy_archived" if sc == "legacy_archived" else "ghost/drop-note"})
    return rot


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="W2 manifest-rot gate: droppable-labeled but live tables.")
    ap.add_argument("--manifest", default=str(ROOT / "architecture" / "db_table_ownership.yaml"))
    ap.add_argument("--state-dir", default=None,
                    help="Live state dir (default: the PRIMARY checkout's state via db_paths / ZEUS_PRIMARY_ROOT).")
    a = ap.parse_args(argv)
    if a.state_dir is None:
        try:
            from src.state.db_paths import primary_trade_db_path  # config-free resolver
            a.state_dir = str(primary_trade_db_path().parent)
        except Exception:
            a.state_dir = str(ROOT / "state")
    rot = audit(Path(a.manifest), Path(a.state_dir))
    if not rot:
        print("PRE-DROP OK: no droppable-labeled table (with no canonical sibling) still holds "
              "data or a writer.")
        return 0
    # These are droppable-labeled tables that still hold rows or have a same-named writer and
    # have NO canonical (owning-class) sibling. In a correct manifest these are legitimately-
    # labeled legacy tables (superseded / retired / migration artifacts) that are simply not yet
    # physically dropped — NOT live-authority mislabels. The gate blocks a BLIND label-keyed drop
    # so the operator confirms each is fully drained/retired first (and rules out a genuine
    # mislabel by checking the writer's target DB — the writer signal here is name-only).
    print(f"PRE-DROP REVIEW: {len(rot)} droppable-labeled table(s) still hold data or a writer "
          "and have no canonical sibling — confirm each is fully drained/retired before any "
          "label-keyed drop (rule out a genuine live-authority mislabel):")
    for r in sorted(rot, key=lambda x: (x["db"], x["name"])):
        print(f"  [{r['db']}] {r['name']}  label={r['label']}  has_rows={r['has_rows']}  live_writer={r['live_writer']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
