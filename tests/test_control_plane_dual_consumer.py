# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §4.5(a), §6 antibody #14
"""Antibody #14: control_plane.json dual consumer — ingest_main.py must contain
a control_plane read pattern (grep-based; full implementation deferred to Phase 3).

Per design §4.5(a): the ingest daemon reads control_plane.json on each tick to
honor pause_source / resume_source / pause_ingest keys.

Phase 2 deliverable: verify the PHASE-3-STUB marker is present in ingest_main.py
(ensures Phase 3 has a concrete integration point to wire).

Phase 3 deliverable: replace this grep-based assertion with a functional test
that actually runs the ingest tick and asserts it honors the control plane key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
INGEST_MAIN = PROJECT_ROOT / "src" / "ingest_main.py"
CONTROL_PLANE = PROJECT_ROOT / "src" / "control" / "control_plane.py"


class TestControlPlaneDualConsumer:
    def test_ingest_main_exists(self):
        """src/ingest_main.py must exist (ingest daemon entry point)."""
        assert INGEST_MAIN.exists(), "src/ingest_main.py must exist"

    def test_ingest_main_has_control_plane_stub(self):
        """ingest_main.py must contain the PHASE-3-STUB §4.5(a) marker.

        This marker documents the control_plane read wiring that Phase 3
        must implement. Its presence ensures the integration point is not
        silently lost across sessions.
        """
        content = INGEST_MAIN.read_text()
        assert "PHASE-3-STUB" in content, (
            "ingest_main.py must contain PHASE-3-STUB §4.5(a) marker for control_plane "
            "dual consumer wiring. Add: "
            "'# PHASE-3-STUB §4.5(a): control_plane.json dual consumer wiring.'"
        )
        # Also assert the stub names the right keys
        assert "pause_source" in content, (
            "PHASE-3-STUB must mention 'pause_source' key (design §4.5a contract)"
        )

    def test_control_plane_module_exists(self):
        """src/control/control_plane.py must exist."""
        assert CONTROL_PLANE.exists(), "src/control/control_plane.py must exist"

    def test_control_plane_has_pause_commands(self):
        """control_plane.py must expose COMMANDS that include pause/resume variants.

        The ingest daemon will honor pause_source / resume_source / pause_ingest.
        These must be registered in the COMMANDS set (or added in Phase 3).
        """
        content = CONTROL_PLANE.read_text()
        assert "pause_entries" in content, (
            "control_plane.py must have pause_entries command (existing — sanity check)"
        )
        # Phase 3 will add pause_source, resume_source, pause_ingest to COMMANDS.
        # For now assert the module at least documents the pattern.
        assert "COMMANDS" in content, (
            "control_plane.py must have COMMANDS set for Phase 3 ingest extensions"
        )

    def test_ecmwf_tick_honors_pause_source_directive(self, tmp_path, monkeypatch):
        """Functional: _ecmwf_open_data_cycle returns paused_by_control_plane when
        control_plane.json has paused_sources: {ecmwf_open_data: true}.

        Writes state/control_plane.json, patches state_path to use tmp_path,
        then calls _ecmwf_open_data_cycle() directly and asserts the return value.
        """
        import json
        from src.control import control_plane as cp_module

        # Write control_plane.json with ecmwf_open_data paused
        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(json.dumps({"paused_sources": {"ecmwf_open_data": True}}))

        # Patch CONTROL_PATH so read_ingest_control_state reads from tmp_path
        original_path = cp_module.CONTROL_PATH
        cp_module.CONTROL_PATH = cp_path
        try:
            # Import _ecmwf_open_data_cycle after patching
            from src.ingest_main import _ecmwf_open_data_cycle
            result = _ecmwf_open_data_cycle()
            assert result is not None, "must return a dict, not None"
            assert result.get("status") == "paused_by_control_plane", (
                f"Expected paused_by_control_plane, got: {result}"
            )
            assert result.get("source") == "ecmwf_open_data"
        finally:
            cp_module.CONTROL_PATH = original_path

    def test_pause_source_via_apply_command_round_trip(self, tmp_path, monkeypatch):
        """Functional: _apply_command('pause_source', ...) calls set_pause_source and
        marks the source paused in control_plane.json (queue path, not just direct call).

        Verifies A-5 fix: pause_source branch exists in _apply_command and wires
        through to set_pause_source rather than falling through to the false-positive
        `return True, ""`.
        """
        import json
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(json.dumps({}))
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)

        # Call _apply_command directly (simulates queue dispatch)
        ok, msg = cp_module._apply_command("pause_source", {"source": "ecmwf_open_data"})
        assert ok is True, f"Expected ok=True, got {ok!r}"
        assert "paused" in msg, f"Expected 'paused' in msg, got {msg!r}"
        assert "ecmwf_open_data" in msg

        # Verify state was actually written to disk
        state = json.loads(cp_path.read_text())
        assert state.get("paused_sources", {}).get("ecmwf_open_data") is True, (
            "pause_source must write to control_plane.json paused_sources"
        )

    def test_resume_source_via_apply_command_round_trip(self, tmp_path, monkeypatch):
        """Functional: _apply_command('resume_source', ...) clears the pause in
        control_plane.json (queue path).

        Verifies A-5 fix: resume_source branch exists and correctly removes the
        paused_sources entry set by a prior pause_source command.
        """
        import json
        from src.control import control_plane as cp_module

        # Start with source already paused
        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(json.dumps({"paused_sources": {"ecmwf_open_data": True}}))
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)

        ok, msg = cp_module._apply_command("resume_source", {"source": "ecmwf_open_data"})
        assert ok is True, f"Expected ok=True, got {ok!r}"
        assert "resumed" in msg, f"Expected 'resumed' in msg, got {msg!r}"
        assert "ecmwf_open_data" in msg

        # Verify source no longer paused
        state = json.loads(cp_path.read_text())
        paused = state.get("paused_sources", {})
        assert "ecmwf_open_data" not in paused, (
            "resume_source must remove ecmwf_open_data from paused_sources"
        )

    def test_pause_source_missing_source_returns_error(self, tmp_path, monkeypatch):
        """Functional: _apply_command('pause_source', {}) returns (False, 'missing_source')
        when payload has no 'source' key (validation path).
        """
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text("{}")
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)

        ok, msg = cp_module._apply_command("pause_source", {})
        assert ok is False, "Expected ok=False for missing source"
        assert msg == "missing_source"

    def test_freshness_gate_respects_control_plane_override(self, tmp_path):
        """Freshness gate reads force_ignore_freshness from control_plane.json.

        This verifies the operator-override path that parallels control_plane's
        existing command dispatch: operators can suppress freshness staleness
        for a named source without restarting either daemon.
        """
        import json
        from datetime import datetime, timedelta, timezone
        from src.control.freshness_gate import evaluate_freshness

        # Create a stale source_health.json
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        health = {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "sources": {
                "ecmwf_open_data": {
                    "last_success_at": old_ts,
                    "consecutive_failures": 5,
                    "degraded_since": old_ts,
                    "latency_ms": None,
                    "error": "timeout",
                    "last_failure_at": old_ts,
                },
            },
        }
        (tmp_path / "source_health.json").write_text(json.dumps(health))

        # Without override → STALE
        verdict_stale = evaluate_freshness(state_dir=tmp_path)
        assert verdict_stale.branch == "STALE"
        assert "ecmwf_open_data" in verdict_stale.stale_sources

        # Write control_plane.json override
        cp = {"force_ignore_freshness": ["ecmwf_open_data"]}
        (tmp_path / "control_plane.json").write_text(json.dumps(cp))

        # With override → ecmwf_open_data removed from stale
        verdict_override = evaluate_freshness(state_dir=tmp_path)
        assert "ecmwf_open_data" not in verdict_override.stale_sources
        assert "ecmwf_open_data" in verdict_override.operator_overrides
