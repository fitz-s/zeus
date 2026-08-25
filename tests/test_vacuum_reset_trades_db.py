# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 13 Slice C -- coverage for scripts/ops/vacuum_reset_trades_db.py's
#   PRECONDITION AND ASSERTION LOGIC ONLY, exercised exclusively against tiny
#   disposable fixture files under tmp_path. This script has never been run
#   against zeus_trades.db or any DB resembling it, live or otherwise -- these
#   tests do not change that; they prove the logic is correct in isolation.
"""Antibodies for vacuum_reset_trades_db.py: the position/entries-paused
precondition, backup-manifest verification, source integrity check,
free-space check, the full --vacuum-into flow (row-count match, size
assertions, auto_vacuum conversion, receipt), the writer-plane fence, and the
full --swap flow (receipt verification, atomic swap, restore-on-failure)."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.ops.vacuum_reset_trades_db as vrt


TRADE_DDL = """
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL
);
CREATE TABLE decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def _make_trade_db(
    path: Path,
    *,
    open_positions: int = 0,
    decision_log_rows: int = 0,
    bloat_and_delete_rows: int = 0,
) -> None:
    """``bloat_and_delete_rows`` inserts N large rows then deletes most of
    them (keeping ``decision_log_rows``), simulating a DB where retention
    has already run: with auto_vacuum=0 the freed pages stay in the file
    (internal freelist) rather than shrinking it, so VACUUM INTO has real
    bloat to reclaim -- matching the actual live scenario this script exists
    for, rather than a trivially-empty fixture VACUUM INTO cannot shrink."""
    conn = sqlite3.connect(str(path))
    conn.executescript(TRADE_DDL)
    for i in range(open_positions):
        conn.execute(
            "INSERT INTO position_current VALUES (?, 'active')", (f"pos-{i}",)
        )
    total_rows = max(decision_log_rows, bloat_and_delete_rows)
    for i in range(total_rows):
        conn.execute(
            "INSERT INTO decision_log (mode, payload) VALUES ('settlement', ?)",
            (f"payload-{i}" * 200,),  # pad so the file is non-trivially sized
        )
    if bloat_and_delete_rows:
        conn.execute(
            "DELETE FROM decision_log WHERE id <= ?",
            (bloat_and_delete_rows - decision_log_rows,),
        )
    conn.commit()
    conn.close()


def _make_backup_manifest(
    path: Path, *, db_name: str, created_at: datetime, ok: bool = True
) -> None:
    manifest = {
        "created_at": created_at.isoformat(),
        "entries": [
            {
                "db": db_name,
                "dest": "/tmp/fake_backup_dest.db",
                "dest_sha256": "deadbeef",
                "verify": {"ok": ok, "integrity_check": "ok"},
                "created_at": created_at.isoformat(),
            }
        ],
    }
    path.write_text(json.dumps(manifest))


# ---------------------------------------------------------------------------
# Position / entries-paused precondition
# ---------------------------------------------------------------------------


def test_zero_open_positions_passes(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _make_trade_db(db, open_positions=0)
    conn = sqlite3.connect(str(db))
    assert vrt.check_zero_open_positions_or_entries_paused(conn) == "zero_open_positions"


def test_open_positions_without_entries_paused_refuses(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "trades.db"
    _make_trade_db(db, open_positions=2)
    conn = sqlite3.connect(str(db))
    monkeypatch.setattr(
        "src.control.control_plane.is_entries_paused", lambda: False
    )
    with pytest.raises(vrt.PreconditionError, match="open position"):
        vrt.check_zero_open_positions_or_entries_paused(conn)


def test_open_positions_with_entries_paused_passes_with_warning(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "trades.db"
    _make_trade_db(db, open_positions=2)
    conn = sqlite3.connect(str(db))
    monkeypatch.setattr(
        "src.control.control_plane.is_entries_paused", lambda: True
    )
    result = vrt.check_zero_open_positions_or_entries_paused(conn)
    assert "entries_paused" in result
    assert "writer-plane fence" in result


# ---------------------------------------------------------------------------
# Backup manifest
# ---------------------------------------------------------------------------


def test_backup_manifest_missing_file_refuses(tmp_path: Path) -> None:
    with pytest.raises(vrt.PreconditionError, match="not found"):
        vrt.check_backup_manifest(
            tmp_path / "nonexistent.json", db_path=tmp_path / "zeus_trades.db", max_age_hours=24
        )


def test_backup_manifest_too_old_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _make_backup_manifest(
        manifest_path, db_name="zeus_trades.db", created_at=datetime.now(timezone.utc) - timedelta(hours=48)
    )
    with pytest.raises(vrt.PreconditionError, match="older than"):
        vrt.check_backup_manifest(
            manifest_path, db_path=tmp_path / "zeus_trades.db", max_age_hours=24
        )


def test_backup_manifest_wrong_db_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _make_backup_manifest(
        manifest_path, db_name="zeus-world.db", created_at=datetime.now(timezone.utc)
    )
    with pytest.raises(vrt.PreconditionError, match="no entry for"):
        vrt.check_backup_manifest(
            manifest_path, db_path=tmp_path / "zeus_trades.db", max_age_hours=24
        )


def test_backup_manifest_unverified_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _make_backup_manifest(
        manifest_path, db_name="zeus_trades.db", created_at=datetime.now(timezone.utc), ok=False
    )
    with pytest.raises(vrt.PreconditionError, match="did not verify ok"):
        vrt.check_backup_manifest(
            manifest_path, db_path=tmp_path / "zeus_trades.db", max_age_hours=24
        )


def test_backup_manifest_fresh_and_verified_passes(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _make_backup_manifest(
        manifest_path, db_name="zeus_trades.db", created_at=datetime.now(timezone.utc)
    )
    match = vrt.check_backup_manifest(
        manifest_path, db_path=tmp_path / "zeus_trades.db", max_age_hours=24
    )
    assert match["db"] == "zeus_trades.db"


# ---------------------------------------------------------------------------
# Source integrity
# ---------------------------------------------------------------------------


def test_source_integrity_ok_passes(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _make_trade_db(db)
    conn = sqlite3.connect(str(db))
    vrt.check_source_integrity(conn)  # must not raise


def test_source_integrity_failure_refuses(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _make_trade_db(db, decision_log_rows=200)
    # Truncate off the back half of the file, chopping through real b-tree
    # pages -- more robust than monkeypatching a C-extension type, and a
    # single corrupted byte doesn't reliably trip integrity_check depending
    # on which region it lands in.
    size = db.stat().st_size
    with open(db, "r+b") as f:
        f.truncate(size // 2)
    conn = sqlite3.connect(str(db))
    with pytest.raises(vrt.PreconditionError, match="integrity_check"):
        vrt.check_source_integrity(conn)


# ---------------------------------------------------------------------------
# Writer-plane fence
# ---------------------------------------------------------------------------


def test_writer_plane_fence_requires_flag() -> None:
    with pytest.raises(vrt.PreconditionError, match="requires the writer plane fenced"):
        vrt.assert_writer_plane_fenced(False)


def test_writer_plane_fence_passes_with_flag_and_no_live_processes(monkeypatch) -> None:
    monkeypatch.setenv(vrt._SKIP_PROCESS_CHECK_ENV_VAR, "1")
    vrt.assert_writer_plane_fenced(True)  # must not raise


# ---------------------------------------------------------------------------
# Full --vacuum-into flow (tiny disposable fixture files only)
# ---------------------------------------------------------------------------


def test_vacuum_into_full_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.control.control_plane.is_entries_paused", lambda: False
    )
    source = tmp_path / "source" / "zeus_trades.db"
    source.parent.mkdir()
    _make_trade_db(source, open_positions=0, decision_log_rows=800, bloat_and_delete_rows=1600)
    dest = tmp_path / "dest" / "zeus_trades_compact.db"

    receipt = vrt.run_vacuum_into(
        db_path=source, dest=dest, backup_manifest=None, backup_max_age_hours=24
    )

    assert dest.exists()
    assert receipt["dest_integrity_check"] == "ok"
    assert receipt["dest_auto_vacuum_mode"] == "incremental"
    assert receipt["source_row_counts"]["decision_log"] == 800
    assert Path(receipt["receipt_path"]).exists()

    dest_conn = sqlite3.connect(str(dest))
    assert dest_conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert dest_conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 800


def test_vacuum_into_refuses_if_dest_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.control.control_plane.is_entries_paused", lambda: False
    )
    source = tmp_path / "zeus_trades.db"
    _make_trade_db(source)
    dest = tmp_path / "dest.db"
    dest.write_text("already here")

    with pytest.raises(vrt.PreconditionError, match="already exists"):
        vrt.run_vacuum_into(db_path=source, dest=dest, backup_manifest=None, backup_max_age_hours=24)


def test_vacuum_into_refuses_on_open_positions_without_pause(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.control.control_plane.is_entries_paused", lambda: False
    )
    source = tmp_path / "zeus_trades.db"
    _make_trade_db(source, open_positions=3)
    dest = tmp_path / "dest.db"

    with pytest.raises(vrt.PreconditionError, match="open position"):
        vrt.run_vacuum_into(db_path=source, dest=dest, backup_manifest=None, backup_max_age_hours=24)


# ---------------------------------------------------------------------------
# Full --swap flow
# ---------------------------------------------------------------------------


def test_swap_requires_a_prior_vacuum_into_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(vrt._SKIP_PROCESS_CHECK_ENV_VAR, "1")
    source = tmp_path / "zeus_trades.db"
    _make_trade_db(source)
    dest = tmp_path / "dest.db"
    dest.write_text("no receipt for this file")

    with pytest.raises(vrt.PreconditionError, match="no vacuum_reset receipt"):
        vrt.run_swap(
            db_path=source, dest=dest, operator_confirms_fenced=True,
            backup_manifest=None, backup_max_age_hours=24,
        )


def test_swap_full_flow_replaces_live_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(vrt._SKIP_PROCESS_CHECK_ENV_VAR, "1")
    monkeypatch.setattr(
        "src.control.control_plane.is_entries_paused", lambda: False
    )
    source = tmp_path / "zeus_trades.db"
    _make_trade_db(source, open_positions=0, decision_log_rows=300, bloat_and_delete_rows=600)
    original_sha = vrt._sha256_file(source)
    dest = tmp_path / "compact.db"

    vrt.run_vacuum_into(db_path=source, dest=dest, backup_manifest=None, backup_max_age_hours=24)
    dest_sha_before_swap = vrt._sha256_file(dest)

    result = vrt.run_swap(
        db_path=source, dest=dest, operator_confirms_fenced=True,
        backup_manifest=None, backup_max_age_hours=24,
    )

    assert result["post_swap_integrity_check"] == "ok"
    # The live path now holds the compacted content, not the original.
    assert vrt._sha256_file(source) == dest_sha_before_swap
    assert vrt._sha256_file(source) != original_sha
    # A pre-swap backup of the original file was preserved.
    backup_path = Path(result["pre_swap_backup_path"])
    assert backup_path.exists()
    assert vrt._sha256_file(backup_path) == original_sha
    # The compacted content is readable and has the right row count.
    live_conn = sqlite3.connect(str(source))
    assert live_conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 300


def test_swap_refuses_without_operator_confirms_fenced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.control.control_plane.is_entries_paused", lambda: False
    )
    source = tmp_path / "zeus_trades.db"
    _make_trade_db(source, decision_log_rows=200, bloat_and_delete_rows=400)
    dest = tmp_path / "compact.db"
    vrt.run_vacuum_into(db_path=source, dest=dest, backup_manifest=None, backup_max_age_hours=24)

    with pytest.raises(vrt.PreconditionError, match="writer plane fenced"):
        vrt.run_swap(
            db_path=source, dest=dest, operator_confirms_fenced=False,
            backup_manifest=None, backup_max_age_hours=24,
        )
    # Nothing was touched.
    assert source.exists()
