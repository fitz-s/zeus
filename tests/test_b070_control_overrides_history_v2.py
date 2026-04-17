"""B070 tests: control_overrides event-sourced refactor.

Covers:
- control_overrides_history DDL + triggers + VIEW creation from kernel SQL
- upsert_control_override appends 'upsert' rows
- expire_control_override appends 'expire' rows (or no-ops cleanly)
- VIEW projects the latest recorded_at per override_id
- Append-only triggers reject UPDATE and DELETE
- skip-missing-table guard still works when history table is absent
- Migration script: dry-run, apply, idempotency, destructive gate
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.state.db import (
    expire_control_override,
    query_control_override_state,
    upsert_control_override,
)
from src.state.ledger import apply_architecture_kernel_schema


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


class TestSchemaShape:
    def test_history_table_exists(self):
        conn = _memory_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='control_overrides_history'"
        ).fetchone()
        assert row is not None

    def test_control_overrides_is_a_view(self):
        conn = _memory_conn()
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='control_overrides'"
        ).fetchone()
        assert row is not None
        assert row["type"] == "view"

    def test_history_index_exists(self):
        conn = _memory_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_control_overrides_history_id_time'"
        ).fetchone()
        assert row is not None

    def test_append_only_triggers_exist(self):
        conn = _memory_conn()
        triggers = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert "control_overrides_history_no_update" in triggers
        assert "control_overrides_history_no_delete" in triggers

    def test_view_exposes_expected_columns(self):
        conn = _memory_conn()
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(control_overrides)").fetchall()
        }
        assert {
            "override_id",
            "target_type",
            "target_key",
            "action_type",
            "value",
            "issued_by",
            "issued_at",
            "effective_until",
            "reason",
            "precedence",
        }.issubset(cols)


class TestUpsert:
    def test_upsert_appends_history_row(self):
        conn = _memory_conn()
        result = upsert_control_override(
            conn,
            override_id="test:global:gate",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="test",
            issued_at="2026-04-17T12:00:00+00:00",
            reason="unit test",
        )
        assert result["status"] == "written"
        rows = conn.execute(
            "SELECT operation, value, effective_until FROM control_overrides_history "
            "WHERE override_id = 'test:global:gate'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["operation"] == "upsert"
        assert rows[0]["value"] == "true"

    def test_second_upsert_appends_new_row_does_not_overwrite(self):
        conn = _memory_conn()
        for value, issued_at in [
            ("true", "2026-04-17T12:00:00+00:00"),
            ("false", "2026-04-17T13:00:00+00:00"),
        ]:
            upsert_control_override(
                conn,
                override_id="test:global:gate",
                target_type="global",
                target_key="entries",
                action_type="gate",
                value=value,
                issued_by="test",
                issued_at=issued_at,
                reason="unit test",
            )
        rows = conn.execute(
            "SELECT operation, value, issued_at FROM control_overrides_history "
            "WHERE override_id = 'test:global:gate' ORDER BY history_id"
        ).fetchall()
        assert len(rows) == 2
        assert [r["value"] for r in rows] == ["true", "false"]
        assert all(r["operation"] == "upsert" for r in rows)

    def test_view_projects_latest_row(self):
        conn = _memory_conn()
        for value, issued_at in [
            ("v1", "2026-04-17T12:00:00+00:00"),
            ("v2", "2026-04-17T13:00:00+00:00"),
            ("v3", "2026-04-17T14:00:00+00:00"),
        ]:
            upsert_control_override(
                conn,
                override_id="test:global:gate",
                target_type="global",
                target_key="entries",
                action_type="gate",
                value=value,
                issued_by="test",
                issued_at=issued_at,
                reason="t",
            )
        row = conn.execute(
            "SELECT value FROM control_overrides WHERE override_id = 'test:global:gate'"
        ).fetchone()
        assert row["value"] == "v3"


class TestExpire:
    def _seed(self, conn: sqlite3.Connection, *, effective_until: str | None = None) -> None:
        upsert_control_override(
            conn,
            override_id="test:global:gate",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="test",
            issued_at="2026-04-17T12:00:00+00:00",
            effective_until=effective_until,
            reason="unit test",
        )

    def test_expire_active_row_appends_expire_event(self):
        conn = _memory_conn()
        self._seed(conn)
        result = expire_control_override(
            conn,
            override_id="test:global:gate",
            expired_at="2026-04-17T13:00:00+00:00",
        )
        assert result["status"] == "expired"
        assert result["expired_count"] == 1
        rows = conn.execute(
            "SELECT operation, effective_until FROM control_overrides_history "
            "WHERE override_id = 'test:global:gate' ORDER BY history_id"
        ).fetchall()
        assert [r["operation"] for r in rows] == ["upsert", "expire"]
        assert rows[-1]["effective_until"] == "2026-04-17T13:00:00+00:00"

    def test_expire_noop_when_already_expired(self):
        conn = _memory_conn()
        self._seed(conn, effective_until="2026-04-17T11:00:00+00:00")  # past
        result = expire_control_override(
            conn,
            override_id="test:global:gate",
            expired_at="2026-04-17T13:00:00+00:00",
        )
        assert result["status"] == "noop"
        assert result["expired_count"] == 0
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM control_overrides_history "
            "WHERE override_id = 'test:global:gate'"
        ).fetchone()
        assert rows["n"] == 1  # only the seed, no expire row added

    def test_expire_noop_when_no_row_exists(self):
        conn = _memory_conn()
        result = expire_control_override(
            conn,
            override_id="test:nonexistent",
            expired_at="2026-04-17T13:00:00+00:00",
        )
        assert result["status"] == "noop"
        assert result["expired_count"] == 0

    def test_view_hides_expired_row_via_existing_filter(self):
        """query_control_override_state filters WHERE effective_until IS NULL OR > now.
        After expire, the latest VIEW row has effective_until in the past."""
        conn = _memory_conn()
        self._seed(conn)  # seed with no expiry
        expire_control_override(
            conn,
            override_id="test:global:gate",
            expired_at="2026-04-17T13:00:00+00:00",
        )
        state = query_control_override_state(conn, now="2026-04-17T14:00:00+00:00")
        # global:entries:gate with value 'true' should NOT cause entries_paused
        # because the latest row's effective_until (13:00) is before now (14:00)
        assert state["entries_paused"] is False


class TestAppendOnlyEnforcement:
    def test_update_raises(self):
        conn = _memory_conn()
        upsert_control_override(
            conn,
            override_id="test:global:gate",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="test",
            issued_at="2026-04-17T12:00:00+00:00",
            reason="t",
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE control_overrides_history SET value='false' WHERE override_id='test:global:gate'"
            )

    def test_delete_raises(self):
        conn = _memory_conn()
        upsert_control_override(
            conn,
            override_id="test:global:gate",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="test",
            issued_at="2026-04-17T12:00:00+00:00",
            reason="t",
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM control_overrides_history WHERE override_id='test:global:gate'"
            )

    def test_connection_usable_after_trigger_abort(self):
        """P1 antibody: after a trigger-raised IntegrityError + rollback, the
        connection must remain usable for subsequent writes."""
        conn = _memory_conn()
        upsert_control_override(
            conn,
            override_id="test:global:gate",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="test",
            issued_at="2026-04-17T12:00:00+00:00",
            reason="t",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE control_overrides_history SET value='x' WHERE override_id='test:global:gate'"
            )
        conn.rollback()
        result = upsert_control_override(
            conn,
            override_id="test:global:gate2",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="false",
            issued_by="test",
            issued_at="2026-04-17T13:00:00+00:00",
            reason="after-abort",
        )
        assert result["status"] == "written"


class TestViewDeterminism:
    """P0 antibody: the VIEW must project exactly one row per override_id
    even when two history rows share the same recorded_at (microsecond tie
    from concurrent writers, or clock skew)."""

    def test_view_single_row_under_identical_recorded_at(self):
        conn = _memory_conn()
        ts = "2026-04-17T12:00:00.000001+00:00"
        for value in ("v1", "v2"):
            conn.execute(
                """
                INSERT INTO control_overrides_history (
                    override_id, target_type, target_key, action_type, value,
                    issued_by, issued_at, effective_until, reason, precedence,
                    operation, recorded_at
                ) VALUES ('tie:id', 'global', 'entries', 'gate', ?, 't',
                          '2026-04-17T12:00:00+00:00', NULL, 'r', 100,
                          'upsert', ?)
                """,
                (value, ts),
            )
        rows = conn.execute(
            "SELECT value FROM control_overrides WHERE override_id = 'tie:id'"
        ).fetchall()
        assert len(rows) == 1, (
            f"VIEW projected {len(rows)} rows for tied recorded_at; "
            "ordering must use history_id, not recorded_at"
        )
        # The later-inserted row (higher AUTOINCREMENT history_id) wins.
        assert rows[0]["value"] == "v2"

    def test_expire_under_identical_recorded_at_inserts_one_row(self):
        conn = _memory_conn()
        ts = "2026-04-17T12:00:00.000001+00:00"
        for value in ("v1", "v2"):
            conn.execute(
                """
                INSERT INTO control_overrides_history (
                    override_id, target_type, target_key, action_type, value,
                    issued_by, issued_at, effective_until, reason, precedence,
                    operation, recorded_at
                ) VALUES ('tie:id', 'global', 'entries', 'gate', ?, 't',
                          '2026-04-17T12:00:00+00:00', NULL, 'r', 100,
                          'upsert', ?)
                """,
                (value, ts),
            )
        result = expire_control_override(
            conn,
            override_id="tie:id",
            expired_at="2026-04-17T13:00:00+00:00",
        )
        # Exactly one expire row (keyed off MAX(history_id), not MAX(recorded_at))
        assert result["expired_count"] == 1
        expire_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM control_overrides_history "
            "WHERE override_id = 'tie:id' AND operation = 'expire'"
        ).fetchone()
        assert expire_rows["n"] == 1


class TestMissingTableGuard:
    def test_upsert_skips_when_history_absent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # do NOT apply schema
        result = upsert_control_override(
            conn,
            override_id="x",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="t",
            issued_at="2026-04-17T12:00:00+00:00",
            reason="t",
        )
        assert result["status"] == "skipped_missing_table"
        assert result["table"] == "control_overrides"

    def test_expire_skips_when_history_absent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        result = expire_control_override(
            conn,
            override_id="x",
            expired_at="2026-04-17T13:00:00+00:00",
        )
        assert result["status"] == "skipped_missing_table"
        assert result["expired_count"] == 0


class TestMigrationScript:
    """Tests for scripts/migrate_b070_control_overrides_to_history.py"""

    def _legacy_db(self, tmp_path: Path) -> Path:
        """Create a DB with the legacy (pre-B070) control_overrides shape."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE control_overrides (
                override_id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_key TEXT NOT NULL,
                action_type TEXT NOT NULL,
                value TEXT NOT NULL,
                issued_by TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                effective_until TEXT,
                reason TEXT NOT NULL,
                precedence INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO control_overrides VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "control_plane:global:entries_paused",
                "global",
                "entries",
                "gate",
                "true",
                "control_plane",
                "2026-04-17T12:00:00+00:00",
                None,
                "test",
                100,
            ),
        )
        conn.execute(
            "INSERT INTO control_overrides VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "control_plane:global:edge_threshold_multiplier",
                "global",
                "entries",
                "threshold_multiplier",
                "2.0",
                "control_plane",
                "2026-04-17T10:00:00+00:00",
                "2026-04-17T11:00:00+00:00",  # already expired
                "test",
                100,
            ),
        )
        conn.commit()
        conn.close()
        return db_path

    def _run_script(self, db_path: Path, *, apply: bool, destructive: bool) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "migrate_b070_control_overrides_to_history.py"),
            "--db",
            str(db_path),
        ]
        if apply:
            cmd.append("--apply")
        env = os.environ.copy()
        if destructive:
            env["ZEUS_DESTRUCTIVE_CONFIRMED"] = "1"
        else:
            env.pop("ZEUS_DESTRUCTIVE_CONFIRMED", None)
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT))

    def test_dry_run_does_not_modify_db(self, tmp_path):
        db_path = self._legacy_db(tmp_path)
        res = self._run_script(db_path, apply=False, destructive=False)
        assert res.returncode == 0, res.stderr
        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "control_overrides" in tables  # still a table
        assert "control_overrides_history" not in tables
        conn.close()

    def test_apply_without_destructive_does_not_drop(self, tmp_path):
        db_path = self._legacy_db(tmp_path)
        res = self._run_script(db_path, apply=True, destructive=False)
        # exit code 1 because destructive gate blocked completion
        assert res.returncode == 1
        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # legacy table still present; history table rolled back
        assert "control_overrides" in tables
        # history may exist as an empty shell created before rollback, but its
        # contents should be empty
        if "control_overrides_history" in tables:
            count = conn.execute(
                "SELECT COUNT(*) FROM control_overrides_history"
            ).fetchone()[0]
            assert count == 0
        conn.close()

    def test_apply_with_destructive_completes_migration(self, tmp_path):
        db_path = self._legacy_db(tmp_path)
        res = self._run_script(db_path, apply=True, destructive=True)
        assert res.returncode == 0, res.stderr
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        master = {
            row["name"]: row["type"]
            for row in conn.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE name IN ('control_overrides', 'control_overrides_history')"
            ).fetchall()
        }
        assert master.get("control_overrides_history") == "table"
        assert master.get("control_overrides") == "view"

        # 2 'migrated' rows + 1 'expire' row for the already-expired one
        rows = conn.execute(
            "SELECT operation FROM control_overrides_history ORDER BY history_id"
        ).fetchall()
        ops = [r["operation"] for r in rows]
        assert ops.count("migrated") == 2
        assert ops.count("expire") == 1

        # VIEW projects both, but the expired one has effective_until in the past
        view_rows = {
            r["override_id"]: dict(r)
            for r in conn.execute(
                "SELECT override_id, value, effective_until FROM control_overrides"
            ).fetchall()
        }
        assert view_rows["control_plane:global:entries_paused"]["value"] == "true"
        assert view_rows["control_plane:global:entries_paused"]["effective_until"] is None
        assert (
            view_rows["control_plane:global:edge_threshold_multiplier"]["effective_until"]
            == "2026-04-17T11:00:00+00:00"
        )
        conn.close()

    def test_idempotent_on_fresh_b070_db(self, tmp_path):
        """Running migration on a DB that already has the B070 shape is a no-op."""
        db_path = tmp_path / "fresh.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        apply_architecture_kernel_schema(conn)
        conn.close()

        res = self._run_script(db_path, apply=True, destructive=True)
        assert res.returncode == 0, res.stderr
        assert "noop" in res.stdout.lower()


class TestLegacyTableGuard:
    """P1 antibody (critic 2026-04-17): SQLite's `CREATE VIEW IF NOT EXISTS`
    silently no-ops when a TABLE of the same name already exists. On a legacy
    DB where `control_overrides` is still a TABLE, kernel init would create
    the new history table, skip the VIEW, and reads would silently hit the
    stale legacy table while writes go to the new history — split-brain.
    apply_architecture_kernel_schema must fail-fast on this condition."""

    def test_kernel_init_raises_on_legacy_control_overrides_table(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create the legacy TABLE shape (subset of original kernel definition)
        conn.execute(
            """
            CREATE TABLE control_overrides (
                override_id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_key TEXT NOT NULL,
                action_type TEXT NOT NULL,
                value TEXT NOT NULL,
                issued_by TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                effective_until TEXT,
                reason TEXT NOT NULL,
                precedence INTEGER NOT NULL
            )
            """
        )
        with pytest.raises(RuntimeError, match="legacy control_overrides TABLE"):
            apply_architecture_kernel_schema(conn)

    def test_kernel_init_succeeds_on_fresh_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_architecture_kernel_schema(conn)  # should not raise
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='control_overrides'"
        ).fetchone()
        assert row is not None
        assert row["type"] == "view"

    def test_kernel_init_succeeds_on_already_migrated_db(self):
        """Re-applying kernel SQL on a DB that already has the B070 shape
        must be a no-op (idempotent), not raise."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_architecture_kernel_schema(conn)
        # Re-apply
        apply_architecture_kernel_schema(conn)
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='control_overrides'"
        ).fetchone()
        assert row["type"] == "view"
