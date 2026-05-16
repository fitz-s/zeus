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

        monkeypatch.setattr(main_module, "_startup_world_db_schema_ready_check", lambda: "3")
        monkeypatch.setattr(main_module, "_startup_forecasts_schema_ready_check", lambda: "3")

        fn = self._get_fn()
        fn()  # Should return silently — no exception

    def test_stale_world_sentinel_ignored_when_db_schema_current(self, tmp_path, monkeypatch):
        """Stale legacy world_schema_ready.json no longer gates live startup."""
        import src.main as main_module

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
