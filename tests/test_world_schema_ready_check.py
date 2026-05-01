# Lifecycle: created=2026-05-01; last_reviewed=2026-05-01; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §4.2
#   + architect audit A-2 (sentinel reader missing in main.py)
"""Antibody for A-2: _startup_world_schema_ready_check() in src/main.py.

Design §4.2: trading daemon must validate world_schema_ready.json at boot:
- Missing sentinel after 5-min retry → SystemExit (FATAL)
- Present + fresh sentinel → returns silently
- Present + stale sentinel (>24h) → SystemExit (FATAL)
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

    def test_missing_sentinel_raises_system_exit(self, tmp_path, monkeypatch):
        """Missing world_schema_ready.json → SystemExit after retry exhaustion.

        Monkeypatches STATE_DIR so no actual sleep occurs (override retry constants).
        """
        import src.control.freshness_gate as fg_module
        import src.config as config_module

        monkeypatch.setattr(config_module, "STATE_DIR", tmp_path)
        monkeypatch.setattr(fg_module, "BOOT_RETRY_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(fg_module, "BOOT_RETRY_MAX_ATTEMPTS", 2)

        fn = self._get_fn()
        with pytest.raises(SystemExit) as exc_info:
            fn()

        msg = str(exc_info.value)
        assert "world_schema_ready" in msg, f"Expected sentinel mention in: {msg}"
        assert "FATAL" in msg, f"Expected FATAL in: {msg}"

    def test_present_fresh_sentinel_returns_silently(self, tmp_path, monkeypatch):
        """Present sentinel written within 24h → returns without raising."""
        import src.config as config_module

        monkeypatch.setattr(config_module, "STATE_DIR", tmp_path)

        sentinel = tmp_path / "world_schema_ready.json"
        payload = {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "1",
            "ingest_pid": 12345,
            "init_schema_returned_ok": True,
        }
        sentinel.write_text(json.dumps(payload))

        fn = self._get_fn()
        fn()  # Should return silently — no exception

    def test_stale_sentinel_raises_system_exit(self, tmp_path, monkeypatch):
        """Sentinel written >24h ago → SystemExit (freshness enforcement)."""
        import src.config as config_module

        monkeypatch.setattr(config_module, "STATE_DIR", tmp_path)

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
        with pytest.raises(SystemExit) as exc_info:
            fn()

        msg = str(exc_info.value)
        assert "FATAL" in msg, f"Expected FATAL in: {msg}"
        assert "world_schema_ready" in msg, f"Expected sentinel mention in: {msg}"

    def test_sentinel_exactly_at_24h_raises_system_exit(self, tmp_path, monkeypatch):
        """Sentinel exactly 24h + 1s old → SystemExit (boundary condition)."""
        import src.config as config_module

        monkeypatch.setattr(config_module, "STATE_DIR", tmp_path)

        boundary_time = datetime.now(timezone.utc) - timedelta(hours=24, seconds=1)
        sentinel = tmp_path / "world_schema_ready.json"
        sentinel.write_text(json.dumps({
            "written_at": boundary_time.isoformat(),
            "schema_version": "1",
        }))

        fn = self._get_fn()
        with pytest.raises(SystemExit):
            fn()

    def test_function_exists_in_main(self):
        """Structural: _startup_world_schema_ready_check must exist in src/main.py."""
        content = (Path(__file__).parent.parent / "src" / "main.py").read_text()
        assert "_startup_world_schema_ready_check" in content, (
            "src/main.py must define _startup_world_schema_ready_check() (A-2)"
        )
        assert "_startup_world_schema_ready_check()" in content, (
            "src/main.py must CALL _startup_world_schema_ready_check() in main() boot sequence (A-2)"
        )
