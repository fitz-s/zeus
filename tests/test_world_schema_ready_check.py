# Lifecycle: created=2026-05-01; last_reviewed=2026-05-16; last_reused=2026-05-16
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §4.2; docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#   + architect audit A-2; live continuous run hotfix 2026-05-16 replacing stale data-ingest sentinel dependency with direct DB schema checks.
"""Antibody for A-2: _startup_world_schema_ready_check() in src/main.py.

Design §4.2: trading daemon must validate schema readiness at boot:
- World DB schema currency failure after 5-min retry → SystemExit (FATAL)
- Forecast DB schema currency failure after 5-min retry → SystemExit (FATAL)
- Live boot is read-only against canonical DB schemas; explicit deployment
  tooling owns schema repair/migration.
- Stale legacy JSON sentinels no longer gate live startup after forecast-live split
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# B2 (2026-05-28): SCHEMA_VERSION / SCHEMA_FORECASTS_VERSION constants and PRAGMA user_version
# mechanism removed entirely. Schema currency is now proven via structural table presence.


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

    def test_world_schema_ready_check_uses_canonical_table_presence(self, tmp_path, monkeypatch):
        """World schema proof comes from canonical table presence in zeus-world.db."""
        import sqlite3

        import src.state.db as db_module
        from src.main import _startup_world_db_schema_ready_check

        world_db = tmp_path / "zeus-world.db"
        with sqlite3.connect(world_db) as conn:
            conn.execute("CREATE TABLE decision_events (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE position_current (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE trade_decisions (id INTEGER PRIMARY KEY)")
        monkeypatch.setattr(db_module, "ZEUS_WORLD_DB_PATH", world_db)

        assert _startup_world_db_schema_ready_check() == "ready"

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

    def test_forecasts_schema_ready_check_uses_canonical_table_presence(self, tmp_path, monkeypatch):
        """Forecast schema proof comes from canonical table presence in zeus-forecasts.db."""
        import sqlite3

        import src.state.db as db_module
        from src.main import _startup_forecasts_schema_ready_check

        forecasts_db = tmp_path / "zeus-forecasts.db"
        with sqlite3.connect(forecasts_db) as conn:
            conn.execute("CREATE TABLE ensemble_snapshots (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE settlement_outcomes (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE source_run (id INTEGER PRIMARY KEY)")
        monkeypatch.setattr(db_module, "ZEUS_FORECASTS_DB_PATH", forecasts_db)

        assert _startup_forecasts_schema_ready_check() == "ready"

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

    def test_live_boot_uses_read_only_schema_proof_without_prepare(self, monkeypatch):
        """Live boot must not run schema DDL repair before read-only structural proof."""
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

        assert calls == ["read_only_proof"]

    def test_world_schema_prepare_runs_init_schema_unconditionally(self, tmp_path, monkeypatch):
        """init_schema runs unconditionally — no version gating; returns 'prepared'."""
        import sqlite3

        import src.state.db as db_module
        from src.main import _startup_world_db_schema_prepare

        world_db = tmp_path / "zeus-world.db"
        # Create an empty DB — no tables, no user_version seeding
        sqlite3.connect(world_db).close()
        monkeypatch.setattr(db_module, "ZEUS_WORLD_DB_PATH", world_db)

        calls = []
        monkeypatch.setattr(
            db_module, "get_world_connection",
            lambda write_class=None: sqlite3.connect(world_db),
        )
        monkeypatch.setattr(db_module, "init_schema", lambda conn: calls.append("init"))

        result = _startup_world_db_schema_prepare()
        assert result == "prepared"
        assert calls == ["init"]

    def test_v2_schema_uses_savepoint_inside_caller_owned_transaction(self, tmp_path):
        """The v2 schema owner must not require a caller-owned transaction commit.

        Pre-v2 idempotent repairs can leave a supplied connection inside a
        transaction. ``apply_canonical_schema()`` must use a nested savepoint in that
        case, not force the caller to commit unrelated writes first.
        """
        import sqlite3

        import src.state.schema.v2_schema as v2_schema

        db_path = tmp_path / "legacy-world.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE caller_owned_rows (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.execute("BEGIN")
            conn.execute("INSERT INTO caller_owned_rows DEFAULT VALUES")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pre_v2_dirty_helper (id INTEGER PRIMARY KEY)"
            )
            conn.execute("INSERT INTO pre_v2_dirty_helper DEFAULT VALUES")

            v2_schema.apply_canonical_schema(conn, forecast_tables=False)

            assert conn.in_transaction is True
            assert conn.execute("SELECT COUNT(*) FROM pre_v2_dirty_helper").fetchone()[0] == 1
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_price_history'"
            ).fetchone() == (1,)
            conn.rollback()
            assert conn.execute("SELECT COUNT(*) FROM caller_owned_rows").fetchone()[0] == 0
        finally:
            conn.close()

    def test_v2_schema_nested_transaction_preserves_fk_sensitive_dead_tables(self, tmp_path):
        """Caller-owned transactions cannot switch SQLite FK enforcement off.

        The nested v2 schema path must not attempt FK-sensitive destructive
        cleanup while foreign_keys is already enabled by the caller; otherwise a
        legacy dead-table parent with dependent rows can abort schema readiness.
        """
        import sqlite3

        import src.state.schema.v2_schema as v2_schema

        db_path = tmp_path / "legacy-fk-world.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("CREATE TABLE promotion_registry (id INTEGER PRIMARY KEY)")
            conn.execute(
                """
                CREATE TABLE promotion_registry_child (
                    id INTEGER PRIMARY KEY,
                    promotion_id INTEGER NOT NULL REFERENCES promotion_registry(id)
                )
                """
            )
            conn.execute("INSERT INTO promotion_registry (id) VALUES (1)")
            conn.execute(
                "INSERT INTO promotion_registry_child (id, promotion_id) VALUES (1, 1)"
            )
            conn.commit()

            conn.execute("BEGIN")
            assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)

            v2_schema.apply_canonical_schema(conn, forecast_tables=False)

            assert conn.in_transaction is True
            assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='promotion_registry'"
            ).fetchone() == (1,)
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_price_history'"
            ).fetchone() == (1,)
        finally:
            conn.close()


def test_world_db_schema_prepare_remains_explicit_operator_repair(monkeypatch, tmp_path):
    """The repair helper may run init_schema, but live boot must not call it."""
    import sqlite3

    import src.main as main_module
    import src.state.db as db_module

    db_path = tmp_path / "world.db"
    # Create an empty DB — no tables, no user_version seeding needed
    sqlite3.connect(db_path).close()

    calls = []
    monkeypatch.setattr(db_module, "ZEUS_WORLD_DB_PATH", db_path)
    monkeypatch.setattr(
        db_module, "get_world_connection",
        lambda write_class=None: sqlite3.connect(db_path),
    )
    monkeypatch.setattr(db_module, "init_schema", lambda conn: calls.append("init"))

    result = main_module._startup_world_db_schema_prepare()
    assert result == "prepared", (
        "_startup_world_db_schema_prepare must return 'prepared' for explicit repair"
    )
    assert calls == ["init"], (
        "explicit schema repair still runs init_schema; live boot guards separately"
    )
