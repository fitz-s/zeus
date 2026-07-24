# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Purpose: behavioral antibody for bounded executable snapshot tail repair.
# Reuse: pytest tests/test_repair_executable_snapshot_corruption.py
"""Tests for candidate-only executable snapshot tail repair."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_executable_snapshot_corruption.py"


def _build_fixture(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA page_size=512")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE executable_market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                condition_id TEXT NOT NULL,
                yes_token_id TEXT NOT NULL,
                no_token_id TEXT NOT NULL,
                selected_outcome_token_id TEXT,
                captured_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(snapshot_id)
            );
            CREATE INDEX idx_snapshots_condition_captured
              ON executable_market_snapshots(condition_id, captured_at DESC);
            CREATE INDEX idx_snapshots_no_token_captured
              ON executable_market_snapshots(no_token_id, captured_at DESC);
            CREATE INDEX idx_snapshots_selected_token_captured
              ON executable_market_snapshots(selected_outcome_token_id, captured_at DESC);
            CREATE INDEX idx_snapshots_yes_token_captured
              ON executable_market_snapshots(yes_token_id, captured_at DESC);
            CREATE TRIGGER no_delete_executable_market_snapshots
              BEFORE DELETE ON executable_market_snapshots
              BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER no_update_executable_market_snapshots
              BEFORE UPDATE ON executable_market_snapshots
              BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            """
        )
        for rowid in range(1, 301):
            conn.execute(
                """
                INSERT INTO executable_market_snapshots
                  (rowid, snapshot_id, condition_id, yes_token_id, no_token_id,
                   selected_outcome_token_id, captured_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rowid,
                    f"snapshot-{rowid:04d}",
                    f"condition-{rowid % 11}",
                    f"yes-{rowid % 17}",
                    f"no-{rowid % 17}",
                    f"yes-{rowid % 17}",
                    f"2026-07-24T11:{rowid % 60:02d}:00+00:00",
                    "x" * 1_200,
                ),
            )
        conn.commit()
        root = int(
            conn.execute(
                "SELECT rootpage FROM sqlite_master "
                "WHERE type='table' AND name='executable_market_snapshots'"
            ).fetchone()[0]
        )
        pages = int(conn.execute("PRAGMA page_count").fetchone()[0])
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    with path.open("r+b", buffering=0) as handle:
        header = handle.read(100)
        page_size = int.from_bytes(header[16:18], "big") or 65_536
        handle.seek((root - 1) * page_size)
        root_page = bytearray(handle.read(page_size))
        offset = 100 if root == 1 else 0
        assert root_page[offset] == 0x05
        invalid_page = 0
        for page in range(2, pages + 1):
            handle.seek((page - 1) * page_size)
            first = handle.read(1)
            if first and first[0] not in {0x02, 0x05, 0x0A, 0x0D}:
                invalid_page = page
                break
        assert invalid_page
        root_page[offset + 8 : offset + 12] = struct.pack(">I", invalid_page)
        handle.seek((root - 1) * page_size)
        handle.write(root_page)
        handle.flush()
        os.fsync(handle.fileno())


def _run(db: Path, *, apply: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        ZEUS_EXECUTABLE_SNAPSHOT_REPAIR_ALLOW_FIXTURE_SCHEMA="1",
        ZEUS_POSITION_EVENTS_REPAIR_SKIP_FENCE="1",
    )
    command = [
        sys.executable,
        str(SCRIPT),
        "--db",
        str(db),
        "--max-orphan-tail",
        "512",
    ]
    if apply:
        command.extend(
            ["--apply", "--operator-confirms-fenced", "--candidate-clone"]
        )
    return subprocess.run(command, env=env, capture_output=True, text=True)


def test_dry_run_fingerprints_without_mutation(tmp_path: Path) -> None:
    db = tmp_path / "zeus_trades.db"
    _build_fixture(db)
    before = db.read_bytes()

    result = _run(db)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "repairable"
    assert 0 < report["lost_count"] <= 512
    assert report["indexed_max_rowid"] == 300
    assert db.read_bytes() == before


def test_apply_preserves_readable_history_and_rebuilds_every_index(
    tmp_path: Path,
) -> None:
    db = tmp_path / "zeus_trades.db"
    _build_fixture(db)
    dry = json.loads(_run(db).stdout)

    result = _run(db, apply=True)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "repaired"
    assert report["preserved_max_rowid"] == dry["last_readable_rowid"]
    assert report["dropped_unreadable_snapshots"]

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "PRAGMA integrity_check('executable_market_snapshots')"
        ).fetchall() == [("ok",)]
        assert conn.execute(
            "SELECT COUNT(*) FROM executable_market_snapshots"
        ).fetchone()[0] == report["preserved_rows"]
        indexes = {
            row[1]
            for row in conn.execute(
                "PRAGMA index_list('executable_market_snapshots')"
            )
        }
        assert indexes == {
            "idx_snapshots_condition_captured",
            "idx_snapshots_no_token_captured",
            "idx_snapshots_selected_token_captured",
            "idx_snapshots_yes_token_captured",
            "sqlite_autoindex_executable_market_snapshots_1",
        }
        conn.execute(
            """
            INSERT INTO executable_market_snapshots
              (snapshot_id, condition_id, yes_token_id, no_token_id,
               selected_outcome_token_id, captured_at, payload)
            VALUES ('after-repair', 'condition-new', 'yes-new', 'no-new',
                    'yes-new', '2026-07-24T12:00:00Z', '{}')
            """
        )
        conn.commit()
        assert conn.execute(
            "SELECT snapshot_id FROM executable_market_snapshots "
            "WHERE condition_id='condition-new'"
        ).fetchone()[0] == "after-repair"
    finally:
        conn.close()
