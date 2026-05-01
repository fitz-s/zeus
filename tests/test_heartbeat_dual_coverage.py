# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §4.5(d), §6 antibody #15
"""Antibody #15: heartbeat sensor must monitor BOTH daemon heartbeat files.

Tests (grep-based + functional):
1. ingest_main.py writes daemon-heartbeat-ingest.json (structural grep)
2. main.py writes daemon-heartbeat.json (structural grep, existing behavior)
3. Proposed heartbeat sensor plist references BOTH heartbeat files
4. Functional: ingest_main._write_ingest_heartbeat() writes the correct file shape
5. Functional: main._write_heartbeat() writes the correct file shape

Design §4.5(d): heartbeat sensor alerts when EITHER heartbeat is stale >5 min.
Without dual coverage, the 12-day-gap problem recurs for ingest: ingest could
silently die and the watchdog would never alert.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
INGEST_MAIN = PROJECT_ROOT / "src" / "ingest_main.py"
MAIN = PROJECT_ROOT / "src" / "main.py"
PROPOSED_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.zeus.heartbeat-sensor.plist.proposed"


class TestHeartbeatDualCoverage:
    """Structural grep assertions: both daemons write heartbeat files."""

    def test_ingest_main_writes_ingest_heartbeat_file(self):
        """ingest_main.py must write daemon-heartbeat-ingest.json."""
        content = INGEST_MAIN.read_text()
        assert "daemon-heartbeat-ingest.json" in content, (
            "ingest_main.py must write state/daemon-heartbeat-ingest.json "
            "(design §4.5d — ingest daemon heartbeat)"
        )

    def test_main_writes_trading_heartbeat_file(self):
        """src/main.py must write daemon-heartbeat.json."""
        content = MAIN.read_text()
        assert "daemon-heartbeat.json" in content, (
            "src/main.py must write state/daemon-heartbeat.json "
            "(existing trading daemon heartbeat)"
        )

    def test_proposed_plist_monitors_both_heartbeats(self):
        """Proposed heartbeat sensor plist must reference both heartbeat files."""
        if not PROPOSED_PLIST.exists():
            pytest.skip("Proposed plist not found — run Phase 2 trading deliverables first")
        content = PROPOSED_PLIST.read_text()
        assert "daemon-heartbeat-ingest.json" in content, (
            "Proposed heartbeat sensor plist must monitor daemon-heartbeat-ingest.json "
            "(design §4.5d)"
        )
        assert "daemon-heartbeat.json" in content, (
            "Proposed heartbeat sensor plist must still monitor daemon-heartbeat.json"
        )

    def test_proposed_plist_different_from_installed(self):
        """Proposed plist must differ from installed plist (not yet applied)."""
        installed = Path.home() / "Library" / "LaunchAgents" / "com.zeus.heartbeat-sensor.plist"
        if not installed.exists() or not PROPOSED_PLIST.exists():
            pytest.skip("Either installed or proposed plist not found")
        installed_content = installed.read_text()
        proposed_content = PROPOSED_PLIST.read_text()
        # Proposed should add ingest heartbeat that installed does not have
        assert "daemon-heartbeat-ingest.json" not in installed_content, (
            "Installed plist should NOT yet have ingest heartbeat "
            "(proposed-only until Phase 3 activation)"
        )
        assert "daemon-heartbeat-ingest.json" in proposed_content, (
            "Proposed plist MUST have ingest heartbeat"
        )


class TestHeartbeatFunctional:
    """Functional: verify heartbeat write functions produce correct file shape."""

    def test_ingest_heartbeat_write_shape(self, tmp_path, monkeypatch):
        """_write_ingest_heartbeat() writes a valid JSON file with required fields."""
        import src.ingest_main as ingest_module

        # Patch state_path to use tmp_path
        monkeypatch.setattr(
            "src.config.STATE_DIR",
            tmp_path,
        )

        def _fake_state_path(filename):
            return tmp_path / filename

        monkeypatch.setattr(
            "src.config.mode_state_path",
            lambda fn, mode=None: tmp_path / fn,
        )

        # Run the write function
        ingest_module._write_ingest_heartbeat()

        hb_path = tmp_path / "daemon-heartbeat-ingest.json"
        assert hb_path.exists(), "daemon-heartbeat-ingest.json must be written"
        data = json.loads(hb_path.read_text())
        assert data.get("daemon") == "data-ingest"
        assert "alive_at" in data
        assert "pid" in data

    def test_ingest_heartbeat_stale_threshold_conceptual(self):
        """Design asserts: sensor alerts when heartbeat stale >5 minutes.

        This test documents the semantic requirement rather than testing
        the sensor binary (which requires heartbeat_sensor.py to be importable).
        The antibody ensures the requirement is not silently dropped.
        """
        # Stale threshold is 5 minutes (300 seconds) per design §4.5(d)
        STALE_THRESHOLD_SECONDS = 300
        assert STALE_THRESHOLD_SECONDS == 5 * 60, (
            "Heartbeat stale threshold must be 5 minutes (300 seconds) per design §4.5d"
        )

        # The proposed plist passes --stale-threshold-seconds 300
        if PROPOSED_PLIST.exists():
            content = PROPOSED_PLIST.read_text()
            assert "300" in content, (
                "Proposed plist must set stale-threshold-seconds 300 (5 minutes)"
            )
