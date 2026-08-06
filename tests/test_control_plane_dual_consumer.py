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

import json
import multiprocessing
import re
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
INGEST_MAIN = PROJECT_ROOT / "src" / "ingest_main.py"
CONTROL_PLANE = PROJECT_ROOT / "src" / "control" / "control_plane.py"


def _consume_control_queue_in_child(path: str) -> None:
    from src.control import control_plane as cp_module

    cp_module.CONTROL_PATH = Path(path)
    cp_module.refresh_control_state = lambda: {}
    cp_module._apply_command = lambda _name, _command: (True, "")
    cp_module.process_commands(refresh_when_empty=False)


class TestControlPlaneDualConsumer:
    def test_empty_fast_poll_does_not_refresh_db_state(self, tmp_path, monkeypatch):
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text('{"commands":[],"acks":[]}')
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        refreshes: list[bool] = []
        monkeypatch.setattr(
            cp_module,
            "refresh_control_state",
            lambda: refreshes.append(True),
        )

        assert cp_module.process_commands(refresh_when_empty=False) == []
        assert refreshes == []

    def test_concurrent_command_consumers_execute_queue_once(self, tmp_path, monkeypatch):
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(
            '{"commands":[{"command":"request_status"}],"acks":[]}'
        )
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        monkeypatch.setattr(cp_module, "refresh_control_state", lambda: {})
        calls: list[str] = []

        def apply_once(name, _command):
            calls.append(name)
            time.sleep(0.05)
            return True, ""

        monkeypatch.setattr(cp_module, "_apply_command", apply_once)
        results: list[list[str]] = []

        threads = [
            threading.Thread(target=lambda: results.append(cp_module.process_commands()))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert calls == ["request_status"]
        assert sorted(results, key=len) == [[], ["request_status"]]
        payload = cp_module.read_control_payload()
        assert payload["commands"] == []
        assert [ack["command"] for ack in payload["acks"]] == ["request_status"]

    def test_cross_process_consumers_execute_queue_once(self, tmp_path):
        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(
            '{"commands":[{"command":"request_status"}],"acks":[]}'
        )
        ctx = multiprocessing.get_context("spawn")
        processes = [
            ctx.Process(target=_consume_control_queue_in_child, args=(str(cp_path),))
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=5)

        assert [process.exitcode for process in processes] == [0, 0]
        payload = json.loads(cp_path.read_text())
        assert payload["commands"] == []
        assert [ack["command"] for ack in payload["acks"]] == ["request_status"]

    def test_processing_pause_source_preserves_directive_and_ack(self, tmp_path, monkeypatch):
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(
            '{"commands":[{"command":"pause_source","source":"ecmwf_open_data"}],"acks":[]}'
        )
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        monkeypatch.setattr(cp_module, "refresh_control_state", lambda: {})

        assert cp_module.process_commands() == ["pause_source"]
        payload = cp_module.read_control_payload()
        assert payload["commands"] == []
        assert payload["paused_sources"] == {"ecmwf_open_data": True}
        assert payload["acks"][-1]["command"] == "pause_source"
        assert payload["acks"][-1]["status"] == "executed"

    def test_enqueue_during_command_processing_is_not_lost(self, tmp_path, monkeypatch):
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(
            '{"commands":[{"command":"request_status"}],"acks":[]}'
        )
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        monkeypatch.setattr(cp_module, "refresh_control_state", lambda: {})
        applying = threading.Event()
        release = threading.Event()

        def apply_then_release(_name, _command):
            applying.set()
            assert release.wait(timeout=2)
            return True, ""

        monkeypatch.setattr(cp_module, "_apply_command", apply_then_release)
        consumer = threading.Thread(target=cp_module.process_commands)
        consumer.start()
        assert applying.wait(timeout=2)

        producer = threading.Thread(
            target=lambda: cp_module.enqueue_command(
                {"command": "tighten_risk", "note": "arrived_during_drain"}
            )
        )
        producer.start()
        release.set()
        consumer.join(timeout=2)
        producer.join(timeout=2)

        assert not consumer.is_alive()
        assert not producer.is_alive()
        payload = cp_module.read_control_payload()
        assert payload["commands"] == [
            {"command": "tighten_risk", "note": "arrived_during_drain"}
        ]
        assert [ack["command"] for ack in payload["acks"]] == ["request_status"]

    def test_repaired_control_payload_clears_prior_parse_fault(self, tmp_path, monkeypatch):
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text("{")
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        monkeypatch.setattr(cp_module, "refresh_control_state", lambda: {})
        monkeypatch.setattr(cp_module, "_apply_command", lambda _name, _cmd: (True, ""))

        with pytest.raises(ValueError, match="Corrupted control_plane.json"):
            cp_module.process_commands()

        cp_path.write_text(
            '{"commands":[{"command":"request_status"}],"acks":[]}'
        )
        assert cp_module.process_commands() == ["request_status"]

    def test_known_command_rejection_is_retained_and_fails_closed(
        self, tmp_path, monkeypatch
    ):
        from src.control import control_plane as cp_module

        command = {"command": "tighten_risk", "note": "must_retry"}
        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(
            '{"commands":[{"command":"tighten_risk","note":"must_retry"}],"acks":[]}'
        )
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        monkeypatch.setattr(cp_module, "refresh_control_state", lambda: {})
        monkeypatch.setattr(
            cp_module,
            "_apply_command",
            lambda _name, _cmd: (False, "durable_write_failed"),
        )

        with pytest.raises(RuntimeError, match="retry retained"):
            cp_module.process_commands()
        payload = cp_module.read_control_payload()
        assert payload["commands"] == [command]
        assert payload["acks"][-1]["status"] == "rejected"
        assert payload["acks"][-1]["reason"] == "durable_write_failed"

    def test_pause_resume_tighten_batch_finishes_resumed_and_tightened(
        self, tmp_path, monkeypatch
    ):
        import json

        from src.control import control_plane as cp_module
        from src.state.db import apply_architecture_kernel_schema, get_connection

        cp_path = tmp_path / "control_plane.json"
        db_path = tmp_path / "world.db"
        conn = get_connection(db_path)
        apply_architecture_kernel_schema(conn)
        conn.close()
        cp_path.write_text(
            json.dumps(
                {
                    "commands": [
                        {"command": "pause_entries", "note": "existing_freeze"},
                        {"command": "resume", "note": "canary_resume"},
                        {"command": "tighten_risk", "note": "canary_tighten"},
                    ],
                    "acks": [],
                }
            )
        )
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        monkeypatch.setattr(
            cp_module,
            "get_world_connection",
            lambda: get_connection(db_path),
        )
        monkeypatch.setattr(
            cp_module,
            "get_world_connection_with_trades_required",
            lambda: get_connection(db_path),
        )
        monkeypatch.setattr(
            cp_module,
            "_refresh_live_allowed_strategy_cache",
            lambda _conn: None,
        )
        cp_module.clear_control_state()

        assert cp_module.process_commands() == [
            "pause_entries",
            "resume",
            "tighten_risk",
        ]
        assert cp_module.is_entries_paused() is False
        assert cp_module.get_edge_threshold_multiplier() == 2.0
        payload = cp_module.read_control_payload()
        assert payload["commands"] == []
        assert [ack["status"] for ack in payload["acks"][-3:]] == [
            "executed",
            "executed",
            "executed",
        ]

    def test_atomic_enqueue_and_source_pause_never_expose_partial_json(
        self, tmp_path, monkeypatch
    ):
        import json

        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text('{"commands":[],"acks":[],"operator_key":"keep"}')
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)
        monkeypatch.setattr(cp_module, "refresh_control_state", lambda: {})
        errors: list[Exception] = []

        def enqueue_many():
            for index in range(30):
                cp_module.enqueue_commands(
                    [{"command": "request_status", "note": f"command-{index}"}]
                )

        def pause_sources():
            for index in range(30):
                cp_module.set_pause_source(f"source-{index}", paused=True)

        def read_many():
            for _ in range(300):
                try:
                    json.loads(cp_path.read_text())
                except Exception as exc:
                    errors.append(exc)

        threads = [
            threading.Thread(target=enqueue_many),
            threading.Thread(target=pause_sources),
            threading.Thread(target=read_many),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        payload = cp_module.read_control_payload()
        assert len(payload["commands"]) == 30
        assert len(payload["paused_sources"]) == 30
        assert payload["operator_key"] == "keep"

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
