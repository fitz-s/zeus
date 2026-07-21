"""Antibody for the W1 online backup capability (scripts/ops/backup_canonical_dbs.py).

Fixture-only (no live DB, no config): builds tiny WAL DBs, backs them up via the SQLite
backup API, and proves the copy is consistent — including the case a raw file copy would
get WRONG (committed data still in the -wal). Also exercises the restore-drill verify path.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "backup_canonical_dbs.py"

_spec = importlib.util.spec_from_file_location("backup_canonical_dbs", SCRIPT)
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)


def _wal_db_with_uncheckpointed_commit(path: Path, rows: int) -> None:
    """A WAL DB whose committed rows are still in the -wal (no checkpoint) — the exact case
    a raw copy of the main file alone would lose."""
    c = sqlite3.connect(str(path))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")  # keep commits in the WAL, uncheckpointed
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    c.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    c.commit()
    # leave the connection open so nothing checkpoints/merges the WAL before backup
    c.execute("PRAGMA wal_checkpoint(PASSIVE)")  # even a passive cp: data is consistent either way
    c.close()


def test_backup_is_consistent_including_wal(tmp_path):
    src = tmp_path / "src" / "zeus_trades.db"
    src.parent.mkdir()
    _wal_db_with_uncheckpointed_commit(src, rows=50)
    # sanity: the -wal exists (committed data lives partly there)
    dest = tmp_path / "backup" / "zeus_trades.db"
    # monkeypatch the source-id gate (fixture sqlite build differs)
    bk.APPROVED_SOURCE_IDS = (sqlite3.connect(":memory:").execute("SELECT sqlite_source_id()").fetchone()[0],)
    entry = bk.backup_one(src, dest)
    assert entry["verify"]["ok"], entry
    # the backup has ALL 50 rows (a raw main-file copy could miss WAL-resident rows)
    c = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 50
        assert c.execute("SELECT max(id) FROM t").fetchone()[0] == 50
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        c.close()


def test_refuses_existing_destination(tmp_path):
    src = tmp_path / "src" / "zeus_trades.db"; src.parent.mkdir()
    _wal_db_with_uncheckpointed_commit(src, rows=3)
    dest = tmp_path / "backup" / "zeus_trades.db"; dest.parent.mkdir(); dest.write_text("x")
    bk.APPROVED_SOURCE_IDS = (sqlite3.connect(":memory:").execute("SELECT sqlite_source_id()").fetchone()[0],)
    try:
        bk.backup_one(src, dest)
        raised = False
    except SystemExit as e:
        raised = "already exists" in str(e)
    assert raised


def test_verify_only_restore_drill(tmp_path):
    src = tmp_path / "src" / "zeus-forecasts.db"; src.parent.mkdir()
    _wal_db_with_uncheckpointed_commit(src, rows=10)
    dest = tmp_path / "backup" / "zeus-forecasts.db"
    bk.APPROVED_SOURCE_IDS = (sqlite3.connect(":memory:").execute("SELECT sqlite_source_id()").fetchone()[0],)
    bk.backup_one(src, dest)
    v = bk._verify_one(dest)
    assert v["ok"] and v["integrity_check"] == "ok" and v["fk_violations"] == 0


def test_refuses_dest_equal_to_state_dir(tmp_path):
    src = tmp_path / "zeus_trades.db"
    _wal_db_with_uncheckpointed_commit(src, rows=2)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--dest", str(tmp_path), "--state-dir", str(tmp_path), "--only", "zeus_trades.db"],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "must not be the live state/ dir" in (r.stderr + r.stdout)
