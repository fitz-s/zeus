#!/usr/bin/env python3
# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: F15 — drop two provably-redundant indexes on zeus_trades.db. Each is fully
#   covered by a UNIQUE-constraint autoindex, so queries fall back to the autoindex with
#   no plan regression; dropping them removes write-amplification on append-heavy tables.
#   NOTE: with auto_vacuum=OFF the freed pages go to the freelist (reused by future writes),
#   they are NOT returned to the OS without a VACUUM — so this reduces write-amp and future
#   bloat, it is not immediate disk-runway relief.
# Authority: FINDINGS.md F15 + a read-only redundancy proof (index_info) recorded in the
#   audit packet. Single-DB WAL, crash-atomic; all-writer fence pattern mirrors W0-a.
"""Drop redundant trades indexes (each a prefix/exact-dup of a UNIQUE autoindex).

Redundancy proof (index_info, 2026-07-21):
  - idx_book_hash_transitions_market_time (market_slug, observed_at)
      is the left-prefix of UNIQUE sqlite_autoindex_book_hash_transitions_1
      (market_slug, observed_at, transition_seq) -> prefix-redundant.
  - idx_market_price_history_token_recorded (token_id, recorded_at)
      is column-identical to UNIQUE sqlite_autoindex_market_price_history_1
      (token_id, recorded_at) -> exact duplicate.
Each DROP is verified to still leave a covering index for the same column prefix.

Usage: python scripts/migrations/202607_drop_redundant_trade_indexes.py --operator-confirms-fenced
       [--state-dir DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# (index to drop, table, its columns, a UNIQUE autoindex that still covers the prefix)
_DROPS = (
    ("idx_book_hash_transitions_market_time", "book_hash_transitions",
     ["market_slug", "observed_at"], "sqlite_autoindex_book_hash_transitions_1"),
    ("idx_market_price_history_token_recorded", "market_price_history",
     ["token_id", "recorded_at"], "sqlite_autoindex_market_price_history_1"),
)
_ZEUS_DAEMON_PATTERNS = (
    "src.main", "src/main.py", "src.engine.cycle_runner", "src/execution/harvester",
    "src.execution.harvester", "price_channel_ingest", "riskguard_live", "src.riskguard",
    "substrate_observer", "post_trade_capital", "forecast_live", "venue_heartbeat",
    "heartbeat_sensor", "data_ingest",
)
_SKIP_PROC_ENV = "ZEUS_DROPIDX_SKIP_PROCESS_CHECK"


def _live_zeus_processes() -> list[str]:
    if os.environ.get(_SKIP_PROC_ENV) == "1":
        return []
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,command"], text=True)
    except Exception:
        return []
    self_pid = os.getpid()
    hits = []
    for line in out.splitlines():
        pid_str, _, cmd = line.strip().partition(" ")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid == self_pid or "python" not in cmd:
            continue
        if any(p in cmd for p in _ZEUS_DAEMON_PATTERNS):
            hits.append(line.strip())
    return hits


def _index_cols(conn: sqlite3.Connection, name: str) -> list[str]:
    return [c[2] for c in conn.execute(f'PRAGMA index_info("{name}")').fetchall()]


def _trade_db(state_dir: Optional[str]) -> Path:
    if state_dir:
        return Path(state_dir) / "zeus_trades.db"
    from src.state.db import _zeus_trade_db_path
    return _zeus_trade_db_path()


def run(path: Path, *, fenced: bool, dry_run: bool) -> None:
    if not fenced:
        raise SystemExit("REFUSED: needs the all-writer plane fenced. Stop every zeus daemon, "
                         "then re-run with --operator-confirms-fenced.")
    live = _live_zeus_processes()
    if live:
        raise SystemExit("REFUSED: zeus daemon(s) still running:\n  " + "\n  ".join(live))
    if not path.exists():
        raise SystemExit(f"REFUSED: {path} not found.")
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 0")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA fullfsync = ON")
        # Pre-flight: prove each redundant index exists AND its covering UNIQUE autoindex
        # still covers the same column prefix — never drop the last index for a prefix.
        plan = []
        for idx, table, cols, cover in _DROPS:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (idx,)).fetchone():
                print(f"SKIP {idx}: already absent."); continue
            cover_cols = _index_cols(conn, cover)
            if cover_cols[:len(cols)] != cols:
                raise SystemExit(f"REFUSED: {cover} cols {cover_cols} do not cover {idx} prefix {cols}; "
                                 "not safe to drop.")
            plan.append(idx)
        if dry_run:
            print(f"DRY-RUN: would drop {plan} (each covered by its UNIQUE autoindex).")
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            for idx in plan:
                conn.execute(f'DROP INDEX "{idx}"')
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        # verify: dropped indexes gone, covering autoindexes remain
        for idx, table, cols, cover in _DROPS:
            if idx in plan:
                assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (idx,)).fetchone()
                assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (cover,)).fetchone(), \
                    f"covering autoindex {cover} missing after drop!"
        print(f"DROPPED {plan}; covering UNIQUE autoindexes intact.")
    finally:
        conn.close()


def up(conn):  # noqa: ANN001
    raise SystemExit("Not a shared-runner migration (needs an all-writer fence). Run main().")


def down(conn):  # noqa: ANN001
    raise SystemExit("Reverse by re-CREATE-ing the two indexes (definitions in the docstring).")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="F15: drop 2 redundant trades indexes.")
    ap.add_argument("--operator-confirms-fenced", action="store_true")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    run(_trade_db(a.state_dir), fenced=a.operator_confirms_fenced, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
