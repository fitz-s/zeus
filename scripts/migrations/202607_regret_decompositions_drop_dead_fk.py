#!/usr/bin/env python3
# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: clear the 2nd dangling-FK instance found by the FK-resolution antibody:
#   zeus-world.db regret_decompositions.experiment_id -> shadow_experiments (a removed
#   alternate-named table). 0 rows today, but with foreign_keys=ON any future INSERT would
#   compile-fail `no such table: main.shadow_experiments` — same freeze class as
#   trade_decisions. Rebuild the table without the dead FK. SINGLE-DB WAL, crash-atomic.
# Authority: docs/.../implementation/tests/test_no_dangling_foreign_keys.py known-instances.
"""Drop regret_decompositions' dead FK to the removed shadow_experiments table (rebuild)."""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TABLE = "regret_decompositions"
EXPECTED_SHA256 = "98d8856b5c207891cad9eb05609390328713e15d9cc63fec3fe84f7dadd2330c"
FK_CLAUSE = "REFERENCES shadow_experiments(experiment_id)"
_ZEUS_DAEMON_PATTERNS = (
    "src.main", "src/main.py", "src.engine.cycle_runner", "src/execution/harvester",
    "src.execution.harvester", "price_channel_ingest", "riskguard_live", "src.riskguard",
    "substrate_observer", "post_trade_capital", "forecast_live", "venue_heartbeat",
    "heartbeat_sensor", "data_ingest",
)
_SKIP = "ZEUS_REGRET_FK_SKIP_PROCESS_CHECK"


def _live() -> list[str]:
    if os.environ.get(_SKIP) == "1":
        return []
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        return []
    self_pid, hits = os.getpid(), []
    for line in out.splitlines():
        p, _, cmd = line.strip().partition(" ")
        try:
            pid = int(p)
        except ValueError:
            continue
        if pid != self_pid and "python" in cmd and any(x in cmd for x in _ZEUS_DAEMON_PATTERNS):
            hits.append(line.strip())
    return hits


def _world_db(state_dir: Optional[str]) -> Path:
    if state_dir:
        return Path(state_dir) / "zeus-world.db"
    from src.state.db import ZEUS_WORLD_DB_PATH
    return ZEUS_WORLD_DB_PATH


def run(path: Path, *, fenced: bool, dry_run: bool) -> None:
    if not fenced:
        raise SystemExit("REFUSED: needs the all-writer plane fenced. Stop every zeus daemon, "
                         "then re-run with --operator-confirms-fenced.")
    live = _live()
    if live:
        raise SystemExit("REFUSED: zeus daemon(s) still running:\n  " + "\n  ".join(live))
    if not path.exists():
        raise SystemExit(f"REFUSED: {path} not found.")
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=0")
        conn.execute("PRAGMA synchronous=FULL"); conn.execute("PRAGMA fullfsync=ON")
        conn.execute("PRAGMA wal_autocheckpoint=0"); conn.execute("PRAGMA legacy_alter_table=OFF")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA foreign_keys=OFF")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0

        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
        if row is None:
            raise SystemExit(f"REFUSED: {TABLE} not present.")
        sql = row[0]
        if hashlib.sha256(sql.encode()).hexdigest() != EXPECTED_SHA256:
            raise SystemExit("REFUSED: schema drifted from the pinned sha; re-review.")
        if sql.count(FK_CLAUSE) != 1:
            raise SystemExit(f"REFUSED: expected exactly one {FK_CLAUSE!r}; found {sql.count(FK_CLAUSE)}.")
        if conn.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (TABLE + "_new",)).fetchone()[0]:
            raise SystemExit(f"REFUSED: {TABLE}_new already exists.")
        deps = conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('view','trigger') "
                            "AND name != ? AND sql LIKE ?", (TABLE, f"%{TABLE}%")).fetchall()
        if deps:
            raise SystemExit(f"REFUSED: dependent view/trigger(s) reference {TABLE}: {deps}.")
        cols = tuple(c[1] for c in conn.execute(f"PRAGMA table_xinfo({TABLE})").fetchall() if (len(c) < 7 or c[6] == 0))
        new_ddl = sql.replace("\n        " + FK_CLAUSE, "").replace(" " + FK_CLAUSE, "").replace(FK_CLAUSE, "")
        if FK_CLAUSE in new_ddl:
            raise SystemExit("REFUSED: FK clause survived strip.")
        for needle in (f'CREATE TABLE {TABLE} (', f'CREATE TABLE "{TABLE}" ('):
            if needle in new_ddl:
                new_ddl = new_ddl.replace(needle, f'CREATE TABLE {TABLE}_new (', 1)
                break
        else:
            raise SystemExit("REFUSED: could not rename CREATE header.")
        old_count = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        seq_rows = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (TABLE,)).fetchall()
        old_seq = seq_rows[0][0] if seq_rows else None

        if dry_run:
            print(f"DRY-RUN: would rebuild {TABLE} ({old_count} rows, seq={old_seq}) without the dead FK.")
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(new_ddl)
            col_list = ",".join(cols)
            conn.execute(f"INSERT INTO {TABLE}_new ({col_list}) SELECT {col_list} FROM {TABLE} ORDER BY id")
            if conn.execute(f"SELECT count(*) FROM {TABLE}_new").fetchone()[0] != old_count:
                raise SystemExit("ABORT: row count mismatch after copy.")
            conn.execute(f"DROP TABLE {TABLE}")
            conn.execute(f"ALTER TABLE {TABLE}_new RENAME TO {TABLE}")
            if old_seq is not None:
                post = conn.execute("SELECT count(*) FROM sqlite_sequence WHERE name=?", (TABLE,)).fetchone()[0]
                if post == 1:
                    conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (old_seq, TABLE))
            if any(r[2] == "shadow_experiments" for r in conn.execute(f"PRAGMA foreign_key_list({TABLE})").fetchall()):
                raise SystemExit("ABORT: dead FK still present.")
            if conn.execute(f"PRAGMA main.integrity_check({TABLE!r})").fetchone()[0] != "ok":
                raise SystemExit("ABORT: table-scoped integrity_check failed.")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        conn.execute("PRAGMA foreign_keys=ON")
        print(f"DONE: {TABLE} rebuilt without the dead shadow_experiments FK ({old_count} rows).")
    finally:
        conn.close()


def up(conn):  # noqa: ANN001
    raise SystemExit("Not a shared-runner migration (needs an all-writer fence). Run main().")


def down(conn):  # noqa: ANN001
    raise SystemExit("Reverse by re-adding the FK (but shadow_experiments is removed — do not).")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--operator-confirms-fenced", action="store_true")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    run(_world_db(a.state_dir), fenced=a.operator_confirms_fenced, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
