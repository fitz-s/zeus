# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Purpose: crash-atomic antibody for bounded monitor-only position_events repair.
# Reuse: pytest tests/test_repair_position_events_corruption.py
# Authority basis: AGENTS.md append-first truth law and 2026-07-24 live corruption.
"""Antibodies for the incident-scoped position_events corruption repair."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_position_events_corruption.py"

DDL = """
CREATE TABLE "position_events" (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    phase_before TEXT,
    phase_after TEXT NOT NULL,
    strategy_key TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(position_id, sequence_no)
)
"""


def _build_fixture(path: Path, *, unsafe_tail: bool = False) -> tuple[int, int]:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA page_size=512")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(DDL)
        conn.executescript(
            """
            CREATE VIEW unrelated_invalid_legacy_view AS
                SELECT * FROM main.observation_instants;
            CREATE INDEX idx_position_events_position_type_sequence
                ON position_events(position_id, event_type, sequence_no);
            CREATE INDEX idx_position_events_position_phase_after_sequence
                ON position_events(position_id, phase_after, sequence_no);
            CREATE INDEX idx_position_events_settled_env_position_sequence
                ON position_events(env, position_id, sequence_no)
                WHERE phase_after='settled';
            CREATE TRIGGER trg_position_events_no_update
                BEFORE UPDATE ON position_events
                BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER trg_position_events_no_delete
                BEFORE DELETE ON position_events
                BEGIN SELECT RAISE(ABORT, 'append-only'); END;
            CREATE TRIGGER trg_position_events_require_env
                BEFORE INSERT ON position_events
                WHEN NEW.env IS NULL OR trim(NEW.env)=''
                BEGIN SELECT RAISE(ABORT, 'env required'); END;
            """
        )
        for rowid in range(1, 301):
            event_type = (
                "ENTRY_ORDER_FILLED" if unsafe_tail and rowid > 270
                else "MONITOR_REFRESHED"
            )
            conn.execute(
                """
                INSERT INTO position_events (
                    rowid, event_id, position_id, event_version, sequence_no,
                    event_type, occurred_at, phase_before, phase_after,
                    strategy_key, decision_id, snapshot_id, order_id, command_id,
                    caused_by, idempotency_key, venue_status, source_module, env,
                    payload_json
                ) VALUES (?, ?, ?, 1, ?, ?, ?, 'active', 'active', 'center_buy',
                          NULL, NULL, NULL, NULL, 'fixture', ?, NULL, 'fixture',
                          'live', ?)
                """,
                (
                    rowid,
                    f"pos-{rowid}:monitor_refreshed:{rowid}",
                    f"pos-{rowid}",
                    rowid,
                    event_type,
                    f"2026-07-24T11:{rowid % 60:02d}:00+00:00",
                    f"idem-{rowid}",
                    json.dumps({"rowid": rowid, "evidence": "x" * 1_200}),
                ),
            )
        conn.commit()
        root = int(
            conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name='position_events'"
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
        assert root_page[offset] == 0x05, "fixture root must be an interior table page"

        invalid_page = 0
        for page in range(2, page_count + 1):
            handle.seek((page - 1) * page_size)
            first = handle.read(1)
            if first and first[0] not in {0x02, 0x05, 0x0A, 0x0D}:
                invalid_page = page
                break
        assert invalid_page, "fixture must contain an overflow page"
        root_page[offset + 8 : offset + 12] = struct.pack(">I", invalid_page)
        handle.seek((root - 1) * page_size)
        handle.write(root_page)
        handle.flush()
        os.fsync(handle.fileno())
    return root, invalid_page


def _run(
    db: Path,
    *,
    apply: bool = False,
    kill_at: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        ZEUS_POSITION_EVENTS_REPAIR_ALLOW_FIXTURE_SCHEMA="1",
        ZEUS_POSITION_EVENTS_REPAIR_SKIP_FENCE="1",
    )
    if kill_at:
        env["ZEUS_POSITION_EVENTS_REPAIR_KILL_AT"] = kill_at
    command = [
        sys.executable,
        str(SCRIPT),
        "--db",
        str(db),
        "--max-orphan-tail",
        "512",
        "--copy-chunk",
        "17",
    ]
    if apply:
        command.extend(
            ["--apply", "--operator-confirms-fenced", "--candidate-clone"]
        )
    return subprocess.run(command, env=env, capture_output=True, text=True)


def _integrity(db: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [str(row[0]) for row in conn.execute(
            "PRAGMA integrity_check('position_events')"
        )]
    except sqlite3.DatabaseError as exc:
        return [str(exc)]
    finally:
        conn.close()


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
    assert report["last_readable_rowid"] + report["lost_count"] == 300
    assert db.read_bytes() == before
    assert _integrity(db) != ["ok"]


def test_apply_preserves_prefix_and_restores_append_only_schema(
    tmp_path: Path,
) -> None:
    db = tmp_path / "zeus_trades.db"
    _build_fixture(db)
    dry = json.loads(_run(db).stdout)

    result = _run(db, apply=True)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "repaired"
    assert report["preserved_rows"] == dry["last_readable_rowid"]
    assert report["dropped_unreadable_monitor_index_rows"]
    assert _integrity(db) == ["ok"]

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == dry[
            "last_readable_rowid"
        ]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(position_events)")}
        assert "idx_position_events_position_type_sequence" in indexes
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='position_events'"
            )
        }
        assert triggers == {
            "trg_position_events_no_update",
            "trg_position_events_no_delete",
            "trg_position_events_require_env",
        }
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE position_events SET payload_json='{}' WHERE rowid=1"
            )
    finally:
        conn.close()

    second = _run(db)
    assert second.returncode != 0
    assert "no bounded unreadable indexed tail" in second.stderr


def test_refuses_lost_money_side_effect_event(tmp_path: Path) -> None:
    db = tmp_path / "zeus_trades.db"
    _build_fixture(db, unsafe_tail=True)

    result = _run(db)

    assert result.returncode != 0
    assert "non-monitor or unrecognized events" in result.stderr
    assert _integrity(db) != ["ok"]


@pytest.mark.parametrize(
    "kill_at",
    (
        "after_begin",
        "after_create",
        "after_copy",
        "after_drop",
        "after_rename",
        "after_schema",
        "before_commit",
    ),
)
def test_precommit_crash_restores_original_corrupt_state(
    tmp_path: Path, kill_at: str
) -> None:
    db = tmp_path / f"{kill_at}.db"
    _build_fixture(db)

    result = _run(db, apply=True, kill_at=kill_at)

    assert result.returncode == 91
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE name='position_events_recovered_20260724'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='position_events'"
        ).fetchone()[0] == 1
    finally:
        conn.close()
    assert _integrity(db) != ["ok"]
