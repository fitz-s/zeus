# Lifecycle: created=2026-05-01; last_reviewed=2026-05-16; last_reused=2026-05-16
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §4.2; docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#   + architect audit A-2; live continuous run hotfix 2026-05-16 replacing stale data-ingest sentinel dependency with direct DB schema checks.
"""Antibody for A-2: _startup_world_schema_ready_check() in src/main.py.

Design §4.2: trading daemon must validate schema readiness at boot:
- World DB schema currency failure after 5-min retry → SystemExit (FATAL)
- Forecast DB schema currency failure after 5-min retry → SystemExit (FATAL)
- Stale legacy JSON sentinels no longer gate live startup after forecast-live split
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


class TestWorldSchemaReadyCheck:
    """Unit tests for _startup_world_schema_ready_check() in src.main."""

    def _get_fn(self):
        """Import the function under test."""
        from src.main import _startup_world_schema_ready_check
        return _startup_world_schema_ready_check

    def test_world_db_schema_failure_raises_system_exit(self, monkeypatch):
        """World DB schema currency failure → SystemExit after retry exhaustion."""
        import src.control.freshness_gate as fg_module
        import src.main as main_module

        monkeypatch.setattr(fg_module, "BOOT_RETRY_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(fg_module, "BOOT_RETRY_MAX_ATTEMPTS", 2)
        monkeypatch.setattr(main_module, "_startup_world_db_schema_prepare", lambda: "3")
        monkeypatch.setattr(
            main_module,
            "_startup_world_db_schema_ready_check",
            lambda: (_ for _ in ()).throw(RuntimeError("world user_version=0")),
        )
        monkeypatch.setattr(main_module, "_startup_forecasts_schema_ready_check", lambda: "3")

        fn = self._get_fn()
        with pytest.raises(SystemExit) as exc_info:
            fn()

        msg = str(exc_info.value)
        assert "zeus-world.db" in msg, f"Expected world DB mention in: {msg}"
        assert "FATAL" in msg, f"Expected FATAL in: {msg}"

    def test_direct_db_schema_checks_return_silently_without_sentinel(self, monkeypatch):
        """Direct DB schema checks are sufficient; JSON sentinel is not required."""
        import src.main as main_module

        monkeypatch.setattr(main_module, "_startup_world_db_schema_prepare", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_world_db_schema_ready_check", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_forecasts_schema_ready_check", lambda: "3")

        fn = self._get_fn()
        fn()  # Should return silently — no exception

    def test_stale_world_sentinel_ignored_when_db_schema_current(self, tmp_path, monkeypatch):
        """Stale legacy world_schema_ready.json no longer gates live startup."""
        import src.main as main_module

        monkeypatch.setattr(main_module, "_startup_world_db_schema_prepare", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_world_db_schema_ready_check", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_forecasts_schema_ready_check", lambda: "3")

        stale_time = datetime.now(timezone.utc) - timedelta(hours=25)
        sentinel = tmp_path / "world_schema_ready.json"
        payload = {
            "written_at": stale_time.isoformat(),
            "schema_version": "1",
            "ingest_pid": 12345,
            "init_schema_returned_ok": True,
        }
        sentinel.write_text(json.dumps(payload))

        fn = self._get_fn()
        fn()

    def test_world_schema_ready_check_reads_world_db_user_version(self, tmp_path, monkeypatch):
        """World schema proof comes from zeus-world.db, not legacy sentinel age."""
        import sqlite3

        import src.state.db as db_module
        from src.main import _startup_world_db_schema_ready_check

        world_db = tmp_path / "zeus-world.db"
        with sqlite3.connect(world_db) as conn:
            conn.execute(f"PRAGMA user_version = {db_module.SCHEMA_VERSION}")
        monkeypatch.setattr(db_module, "ZEUS_WORLD_DB_PATH", world_db)

        assert _startup_world_db_schema_ready_check() == str(db_module.SCHEMA_VERSION)

    def test_stale_forecasts_sentinel_ignored_when_forecasts_db_schema_current(self, tmp_path, monkeypatch):
        """Legacy forecasts_schema_ready.json no longer gates live startup."""
        import src.config as config_module
        import src.main as main_module

        monkeypatch.setattr(config_module, "STATE_DIR", tmp_path)
        monkeypatch.setattr(main_module, "_startup_world_db_schema_prepare", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_world_db_schema_ready_check", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_forecasts_schema_ready_check", lambda: "3")

        (tmp_path / "world_schema_ready.json").write_text(
            json.dumps(
                {
                    "written_at": datetime.now(timezone.utc).isoformat(),
                    "schema_version": "1",
                }
            )
        )
        (tmp_path / "forecasts_schema_ready.json").write_text(
            json.dumps(
                {
                    "written_at": (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(),
                    "schema_forecasts_version": 1,
                }
            )
        )

        fn = self._get_fn()
        fn()

    def test_forecasts_schema_failure_raises_system_exit(self, tmp_path, monkeypatch):
        """Forecast DB schema currency failure remains fail-closed."""
        import src.control.freshness_gate as fg_module
        import src.config as config_module
        import src.main as main_module

        monkeypatch.setattr(config_module, "STATE_DIR", tmp_path)
        monkeypatch.setattr(fg_module, "BOOT_RETRY_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(fg_module, "BOOT_RETRY_MAX_ATTEMPTS", 2)
        monkeypatch.setattr(main_module, "_startup_world_db_schema_prepare", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_world_db_schema_ready_check", lambda: "3")

        def fail_forecasts_schema():
            raise RuntimeError("forecasts user_version=0")

        monkeypatch.setattr(main_module, "_startup_forecasts_schema_ready_check", fail_forecasts_schema)
        (tmp_path / "world_schema_ready.json").write_text(
            json.dumps(
                {
                    "written_at": datetime.now(timezone.utc).isoformat(),
                    "schema_version": "1",
                }
            )
        )

        fn = self._get_fn()
        with pytest.raises(SystemExit) as exc_info:
            fn()

        msg = str(exc_info.value)
        assert "FATAL" in msg
        assert "zeus-forecasts.db" in msg
        assert "forecast-live" in msg

    def test_forecasts_schema_ready_check_reads_forecasts_db_user_version(self, tmp_path, monkeypatch):
        """Forecast schema proof comes from zeus-forecasts.db, not legacy sentinel age."""
        import sqlite3

        import src.state.db as db_module
        from src.main import _startup_forecasts_schema_ready_check

        forecasts_db = tmp_path / "zeus-forecasts.db"
        with sqlite3.connect(forecasts_db) as conn:
            conn.execute(f"PRAGMA user_version = {db_module.SCHEMA_FORECASTS_VERSION}")
        monkeypatch.setattr(db_module, "ZEUS_FORECASTS_DB_PATH", forecasts_db)

        assert _startup_forecasts_schema_ready_check() == str(db_module.SCHEMA_FORECASTS_VERSION)

    def test_function_exists_in_main(self):
        """Structural: _startup_world_schema_ready_check must exist in src/main.py."""
        content = (Path(__file__).parent.parent / "src" / "main.py").read_text()
        assert "_startup_world_schema_ready_check" in content, (
            "src/main.py must define _startup_world_schema_ready_check() (A-2)"
        )
        assert "_startup_world_schema_ready_check()" in content, (
            "src/main.py must CALL _startup_world_schema_ready_check() in main() boot sequence (A-2)"
        )

    def test_schema_ready_check_runs_before_first_world_db_open(self):
        """Boot schema authority must precede direct world DB smoke reads."""
        content = (Path(__file__).parent.parent / "src" / "main.py").read_text()
        main_body = content[content.index("def main():") :]
        assert main_body.index("_startup_world_schema_ready_check()") < main_body.index(
            "conn = get_world_connection()"
        )

    def test_world_schema_prepare_runs_before_read_only_proof(self, monkeypatch):
        """Live boot must repair stale world schema before read-only user_version proof."""
        import src.control.freshness_gate as fg_module
        import src.main as main_module

        calls: list[str] = []
        monkeypatch.setattr(fg_module, "BOOT_RETRY_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(fg_module, "BOOT_RETRY_MAX_ATTEMPTS", 1)
        monkeypatch.setattr(
            main_module,
            "_startup_world_db_schema_prepare",
            lambda: calls.append("prepare") or "18",
        )
        monkeypatch.setattr(
            main_module,
            "_startup_world_db_schema_ready_check",
            lambda: calls.append("read_only_proof") or "18",
        )
        monkeypatch.setattr(main_module, "_startup_forecasts_schema_ready_check", lambda: "5")

        main_module._startup_db_schema_ready_check()

        assert calls == ["prepare", "read_only_proof"]

    def test_world_schema_prepare_upgrades_stale_existing_world_db(self, tmp_path, monkeypatch):
        """A bumped SCHEMA_VERSION must not wedge live before init_schema() can run."""
        import sqlite3

        import src.state.db as db_module
        from src.main import _startup_world_db_schema_prepare

        world_db = tmp_path / "zeus-world.db"
        with sqlite3.connect(world_db) as conn:
            conn.execute(f"PRAGMA user_version = {db_module.SCHEMA_VERSION - 1}")
        monkeypatch.setattr(db_module, "ZEUS_WORLD_DB_PATH", world_db)

        assert _startup_world_db_schema_prepare() == str(db_module.SCHEMA_VERSION)
        with sqlite3.connect(world_db) as conn:
            row = conn.execute("PRAGMA user_version").fetchone()
            tail = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tail_stress_scenarios'"
            ).fetchone()

        assert row is not None
        assert row[0] == db_module.SCHEMA_VERSION
        assert tail == (1,)

    def test_init_schema_commits_pre_v2_schema_transaction_boundary(
        self, tmp_path, monkeypatch
    ):
        """Pre-v2 idempotent schema repairs must not poison the v2 transaction.

        Live boot runs ``init_schema()`` against existing world DBs. Several
        idempotent schema helpers before ``apply_v2_schema()`` may perform DML
        when upgrading a legacy DB, while ``apply_v2_schema()`` owns an explicit
        transaction. The boundary must be clean before control passes to v2.
        """
        import sqlite3

        import src.state.db as db_module
        import src.state.schema.tail_stress_scenarios_schema as tail_schema
        import src.state.schema.v2_schema as v2_schema

        db_path = tmp_path / "legacy-world.db"
        conn = sqlite3.connect(db_path)
        try:

            def dirty_schema_helper(helper_conn):
                helper_conn.execute(
                    "CREATE TABLE IF NOT EXISTS pre_v2_dirty_helper (id INTEGER PRIMARY KEY)"
                )
                helper_conn.execute("INSERT INTO pre_v2_dirty_helper DEFAULT VALUES")

            def v2_requires_clean_transaction(helper_conn, *, forecast_tables=True):
                assert helper_conn.in_transaction is False
                helper_conn.execute("BEGIN")
                helper_conn.execute(
                    "CREATE TABLE IF NOT EXISTS v2_transaction_probe (id INTEGER PRIMARY KEY)"
                )
                helper_conn.execute("COMMIT")

            monkeypatch.setattr(tail_schema, "ensure_table", dirty_schema_helper)
            monkeypatch.setattr(v2_schema, "apply_v2_schema", v2_requires_clean_transaction)

            db_module.init_schema(conn)

            assert conn.execute("SELECT COUNT(*) FROM pre_v2_dirty_helper").fetchone()[0] == 1
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_transaction_probe'"
            ).fetchone() == (1,)
        finally:
            conn.close()
