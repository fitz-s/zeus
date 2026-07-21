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
_DB_FILE = {"trade": "zeus_trades.db", "world": "zeus-world.db", "forecast": "zeus-forecasts.db"}
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


def _has_writer(table: str) -> bool:
    """A live INSERT writer in src/ (the strongest 'this is live' signal)."""
    try:
        r = subprocess.run(["rg", "-l", rf"INTO\s+{re.escape(table)}\b", str(ROOT / "src")],
                           capture_output=True, text=True)
        return r.returncode == 0 and bool(r.stdout.strip())
    except FileNotFoundError:
        r = subprocess.run(["grep", "-rl", f"INTO {table}", str(ROOT / "src")],
                           capture_output=True, text=True)
        return bool(r.stdout.strip())


_OWNING_CLASSES = {"trade_class", "world_class", "forecast_class", "backtest_class"}


def audit(manifest: Path, state_dir: Path) -> list[dict]:
    """Genuine manifest rot: a table labeled droppable (legacy_archived / ghost-noted) that
    is actually LIVE **and has no canonical (owning-class) sibling registration for the same
    name in any DB**. A legacy_archived copy WITH a canonical sibling is a legitimate ghost
    (correct dual-registration, pending drop) — NOT rot; flagging those over-reports. The
    money-data-loss case is a droppable label on a table whose only/authoritative home it is."""
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
        print("MANIFEST-ROT: none — no droppable-labeled table has rows or a live writer.")
        return 0
    print(f"MANIFEST-ROT: {len(rot)} droppable-labeled table(s) are actually LIVE "
          "(a drop keyed on the label would delete live data):")
    for r in sorted(rot, key=lambda x: (x["db"], x["name"])):
        print(f"  [{r['db']}] {r['name']}  label={r['label']}  has_rows={r['has_rows']}  live_writer={r['live_writer']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
