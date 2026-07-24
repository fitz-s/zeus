# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
"""Antibodies for bounded book-hash transition tail repair."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_book_hash_transitions_corruption.py"


def _build_fixture(path: Path, *, surviving_snapshot: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA page_size=512")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE book_hash_transitions (
                market_slug TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                transition_seq INTEGER NOT NULL,
                prev_hash TEXT NOT NULL,
                new_hash TEXT NOT NULL CHECK (new_hash != prev_hash),
                delta_ms INTEGER NOT NULL CHECK (delta_ms >= 0),
                cycle_id TEXT,
                schema_version INTEGER NOT NULL CHECK (schema_version IN (13, 14)),
                PRIMARY KEY (market_slug, observed_at, transition_seq)
            );
            CREATE INDEX idx_book_hash_transitions_market_time
              ON book_hash_transitions(market_slug, observed_at);
            CREATE INDEX idx_book_hash_transitions_new_hash
              ON book_hash_transitions(new_hash);
            CREATE TABLE executable_market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                event_slug TEXT,
                captured_at TEXT NOT NULL
            );
            """
        )
        for rowid in range(1, 301):
            observed = f"2026-07-24T11:{rowid // 60:02d}:{rowid % 60:02d}+00:00"
            conn.execute(
                """
                INSERT INTO book_hash_transitions (
                    rowid, market_slug, observed_at, transition_seq,
                    prev_hash, new_hash, delta_ms, cycle_id, schema_version
                ) VALUES (?, 'market-a', ?, 1, ?, ?, 1000, NULL, 14)
                """,
                (rowid, observed, f"prev-{rowid}-" + "x" * 200, f"new-{rowid}-" + "y" * 200),
            )
        if surviving_snapshot:
            conn.execute(
                """
                INSERT INTO executable_market_snapshots
                    (snapshot_id, event_slug, captured_at)
                VALUES ('survivor', 'market-a', '2026-07-24T11:05:00+00:00')
                """
            )
        conn.commit()
        root = int(
            conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name='book_hash_transitions'"
            ).fetchone()[0]
        )
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
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
        for page in range(2, page_count + 1):
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
        ZEUS_BOOK_HASH_REPAIR_ALLOW_FIXTURE_SCHEMA="1",
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
    assert report["lost_count"] > 0
    assert db.read_bytes() == before


def test_apply_preserves_readable_history_and_rebuilds_indexes(
    tmp_path: Path,
) -> None:
    db = tmp_path / "zeus_trades.db"
    _build_fixture(db)
    dry = json.loads(_run(db).stdout)

    result = _run(db, apply=True)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["book_hash_integrity"] == ["ok"]
    assert report["preserved_rows"] == dry["last_readable_rowid"]
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "PRAGMA integrity_check('book_hash_transitions')"
        ).fetchall() == [("ok",)]
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list('book_hash_transitions')")
        }
        assert indexes == {
            "idx_book_hash_transitions_market_time",
            "idx_book_hash_transitions_new_hash",
            "sqlite_autoindex_book_hash_transitions_1",
        }
    finally:
        conn.close()


def test_refuses_tail_with_surviving_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "zeus_trades.db"
    _build_fixture(db, surviving_snapshot=True)

    result = _run(db)

    assert result.returncode != 0
    assert "surviving snapshot evidence" in result.stderr
