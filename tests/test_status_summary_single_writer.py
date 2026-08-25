# Created: 2026-06-10
# Last reused or audited: 2026-07-31
# Authority basis: status dual-writer oscillation fix 2026-06-10; daemon
#   pulse/result command-projection contract repair 2026-07-31
"""Antibody: status_summary.json single-writer principle.

Root cause: control_plane._apply_command("request_status") called write_status()
from the riskguard-live process, which lacks heartbeat_supervisor + collateral_ledger
config.  That write produced global_allow_submit=False, oscillating every few
minutes against the daemon's correct True writes.

Fix: the request_status branch no longer calls write_status(); it returns success
and relies on the daemon's cadence to keep status_summary.json fresh.

Invariants tested:
  1. request_status does NOT import or call write_status (structural grep).
  2. _apply_command("request_status", ...) returns (True, ...) without writing
     status_summary.json.
  3. The daemon's write_cycle_pulse keeps status_summary.json within
     live_health.py's STATUS_FRESH_BUDGET_SECONDS=300 s freshness window.
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CONTROL_PLANE = PROJECT_ROOT / "src" / "control" / "control_plane.py"
POST_TRADE_CAPITAL = PROJECT_ROOT / "src" / "execution" / "post_trade_capital.py"


def _configured_execution_capability() -> dict:
    return {
        action: {
            "status": "available",
            "global_allow_submit": True,
            "unavailable_components": [],
            "components": [],
        }
        for action in ("entry", "exit")
    }


def _status_summary_digest(path: Path) -> tuple[int, int, str, bytes]:
    stat = path.stat()
    content = path.read_bytes()
    return stat.st_ino, stat.st_mtime_ns, hashlib.sha256(content).hexdigest(), content


class TestRequestStatusNoLongerWritesStatusSummary:
    """Structural: the request_status branch must not call write_status."""

    def test_request_status_branch_does_not_call_write_status(self):
        """The request_status branch in _apply_command must not invoke write_status().

        Any call to write_status() from control_plane in a request_status context
        would emit a partial/incorrect snapshot (missing heartbeat_supervisor etc.)
        and oscillate with the daemon's correct writes.
        """
        content = CONTROL_PLANE.read_text()

        # Find the request_status block.
        # Pattern: from the `if name == "request_status":` line through the
        # next `if name ==` or `return` that closes the block.
        match = re.search(
            r'if name == "request_status":(.*?)(?=\n        if name ==|\Z)',
            content,
            re.DOTALL,
        )
        assert match is not None, (
            "Could not locate 'if name == \"request_status\":' block in control_plane.py"
        )
        block = match.group(1)
        # Strip comment lines (lines beginning with optional whitespace + #) before
        # checking, so references to write_status in explanatory comments do not
        # trigger the assertion.
        non_comment_lines = [
            line for line in block.splitlines()
            if not re.match(r"^\s*#", line)
        ]
        non_comment_block = "\n".join(non_comment_lines)
        assert "write_status" not in non_comment_block, (
            "request_status branch must NOT call write_status(). "
            "The control-plane process lacks heartbeat_supervisor + collateral_ledger "
            "config and would write a misleading global_allow_submit=False snapshot. "
            "status_summary.json is a single-writer file owned by the daemon."
        )

    def test_request_status_branch_does_not_import_write_status_locally(self):
        """No local import of write_status inside the request_status block."""
        content = CONTROL_PLANE.read_text()
        match = re.search(
            r'if name == "request_status":(.*?)(?=\n        if name ==|\Z)',
            content,
            re.DOTALL,
        )
        assert match is not None, "request_status block not found"
        block = match.group(1)
        non_comment_lines = [
            line for line in block.splitlines()
            if not re.match(r"^\s*#", line)
        ]
        non_comment_block = "\n".join(non_comment_lines)
        assert "from src.observability.status_summary import write_status" not in non_comment_block, (
            "request_status branch must not locally import write_status"
        )

    def test_post_trade_capital_sidecar_does_not_write_status_summary(self):
        """The post-trade sidecar must not refresh daemon-owned status_summary.json.

        The sidecar lacks the live trading daemon's process-local heartbeat,
        risk-allocator, and collateral-ledger singletons. Calling write_cycle_pulse
        from this process overwrites the daemon's correct execution_capability with
        false UNCONFIGURED blockers.
        """
        content = POST_TRADE_CAPITAL.read_text()
        non_comment_lines = [
            line for line in content.splitlines()
            if not re.match(r"^\s*#", line)
        ]
        non_comment_content = "\n".join(non_comment_lines)
        assert "write_cycle_pulse" not in non_comment_content


class TestCompositeChildReadsDaemonStatusSummary:
    """The bounded composite child must never become a second status writer."""

    def _run_composite(self, state_dir: Path):
        from src.control import live_health

        return live_health.refresh_composite_live_health_bounded(
            state_dir=state_dir,
            timeout_seconds=20.0,
        )

    def test_child_code_does_not_import_or_call_write_cycle_pulse(self):
        from src.control import live_health

        assert "write_cycle_pulse" not in live_health._COMPOSITE_CHILD_CODE
        assert "src.observability.status_summary" not in live_health._COMPOSITE_CHILD_CODE

    def test_composite_child_preserves_fresh_configured_daemon_status(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        status_path = state_dir / "status_summary.json"
        status_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "execution_capability": _configured_execution_capability(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        before = _status_summary_digest(status_path)

        result = self._run_composite(state_dir)

        assert result["surfaces"]["execution_capability"]["ok"] is True
        assert _status_summary_digest(status_path) == before

    def test_composite_child_keeps_stale_status_fail_closed(self, tmp_path):
        from src.control.live_health import STATUS_FRESH_BUDGET_SECONDS

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        status_path = state_dir / "status_summary.json"
        status_path.write_text(
            json.dumps(
                {
                    "timestamp": (
                        datetime.now(timezone.utc)
                        - timedelta(seconds=STATUS_FRESH_BUDGET_SECONDS + 1)
                    ).isoformat(),
                    "execution_capability": _configured_execution_capability(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        result = self._run_composite(state_dir)

        assert result["status"] == "DEGRADED"
        assert result["surfaces"]["status_summary"]["ok"] is False
        assert result["surfaces"]["status_summary"]["issue"].startswith(
            "STATUS_SUMMARY_STALE"
        )


class TestRequestStatusApplyCommandBehavior:
    """Functional: _apply_command('request_status') returns success without side effects."""

    def test_request_status_returns_true(self, tmp_path, monkeypatch):
        """_apply_command('request_status') returns (True, ...) — ACKs the command."""
        import json
        from src.control import control_plane as cp_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(json.dumps({}))
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)

        ok, msg = cp_module._apply_command("request_status", {})
        assert ok is True, f"request_status must return ok=True, got {ok!r}"

    def test_request_status_does_not_write_status_summary(self, tmp_path, monkeypatch):
        """_apply_command('request_status') must NOT create or overwrite status_summary.json.

        The file is owned by the daemon process alone.
        """
        import json
        from src.control import control_plane as cp_module
        from src.observability import status_summary as ss_module

        cp_path = tmp_path / "control_plane.json"
        cp_path.write_text(json.dumps({}))
        monkeypatch.setattr(cp_module, "CONTROL_PATH", cp_path)

        # Redirect the status_summary path to a sentinel file that must not be created.
        sentinel = tmp_path / "status_summary.json"
        monkeypatch.setattr(ss_module, "STATUS_PATH", sentinel, raising=True)
        assert not sentinel.exists(), "sentinel must not exist before the call"

        cp_module._apply_command("request_status", {})

        assert not sentinel.exists(), (
            "request_status must not write status_summary.json. "
            "The daemon is the sole writer; control-plane writes produce a "
            "misleading global_allow_submit=False snapshot."
        )


class TestLiveHealthCompositeCyclePulsesOnEmptyBook:
    """Antibody: 2026-08-25 incident -- status_summary.json froze indefinitely
    once the book emptied.

    Root cause: the only two live write_cycle_pulse callers are (1)
    src/engine/cycle_runner.py's run_cycle(), never invoked in EDLI
    event-driven modes, and (2) src/execution/exit_lifecycle.py's
    _schedule_exit_monitor_status_pulse(), reachable only via
    run_exit_monitor_cycle() -- but src/main.py's periodic exit_monitor job
    returns before calling run_exit_monitor_cycle() whenever canonical
    monitored exposure is zero ("periodic exit_monitor completed without
    reactor handoff: canonical monitored exposure is empty"). Commit
    a0811394e ("keep composite child read-only") then removed the one other
    unconditional refresher, which used to run every 60s inside the
    live_health_composite subprocess child regardless of held-position count.
    With zero held positions, generated_at never advanced again, and the
    EDLI_STAGE_STATUS_SUMMARY_STALE entry-readiness check read that as
    unbounded staleness (mitigated separately, but the freshness pipeline
    itself remained broken).

    Fix: _live_health_composite_cycle -- the surviving 60s job that runs
    regardless of held-position count -- now calls write_cycle_pulse directly
    from the daemon process itself (no impersonation needed, unlike the
    removed child call) after refreshing composite live health.
    """

    def test_pulses_status_summary_with_zero_held_positions(self, monkeypatch):
        import src.main as main_mod

        monkeypatch.setattr(main_mod, "_status_summary_refresh_can_defer", lambda: False)
        monkeypatch.setattr(
            main_mod,
            "_defer_for_held_position_monitor",
            lambda job_name: False,
        )
        monkeypatch.setattr(
            main_mod,
            "_defer_for_active_entry_reactor",
            lambda job_name: False,
        )

        composite_calls = []
        monkeypatch.setattr(
            "src.control.live_health.refresh_composite_live_health_bounded",
            lambda **kwargs: composite_calls.append(kwargs) or {"status": "OK"},
        )

        pulse_calls = []
        monkeypatch.setattr(
            "src.observability.status_summary.write_cycle_pulse",
            lambda cycle_summary=None, **kwargs: pulse_calls.append(
                (cycle_summary, kwargs)
            ),
        )

        # Simulate the empty-book condition: run_exit_monitor_cycle's own
        # pulse path is unreachable this cycle, yet freshness must still
        # advance via this independent 60s job.
        main_mod._live_health_composite_cycle()

        assert composite_calls, "composite live-health refresh must still run"
        assert pulse_calls, (
            "an empty-portfolio process must still refresh status_summary.json's "
            "generated_at via write_cycle_pulse -- this is the only remaining "
            "unconditional freshness path when held positions are zero"
        )
        cycle_summary, kwargs = pulse_calls[0]
        assert cycle_summary == {"mode": "heartbeat_pulse", "heartbeat": True}
        assert "process_identity" not in kwargs, (
            "the pulse now runs in the real daemon process; no impersonated "
            "process_identity should be threaded through"
        )

    def test_pulse_failure_does_not_raise(self, monkeypatch):
        """A pulse failure must stay non-fatal to the composite health job."""
        import src.main as main_mod

        monkeypatch.setattr(main_mod, "_status_summary_refresh_can_defer", lambda: False)
        monkeypatch.setattr(
            main_mod,
            "_defer_for_held_position_monitor",
            lambda job_name: False,
        )
        monkeypatch.setattr(
            main_mod,
            "_defer_for_active_entry_reactor",
            lambda job_name: False,
        )
        monkeypatch.setattr(
            "src.control.live_health.refresh_composite_live_health_bounded",
            lambda **kwargs: {"status": "OK"},
        )

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated pulse failure")

        monkeypatch.setattr(
            "src.observability.status_summary.write_cycle_pulse", _boom
        )

        main_mod._live_health_composite_cycle()  # must not raise


class TestDaemonWriteCadenceCoversFreshnessBudget:
    """Relationship: write_cycle_pulse output is within STATUS_FRESH_BUDGET_SECONDS.

    Pins the invariant that dropping request_status's write does NOT cause
    live_health.py to flag STATUS_SUMMARY_STALE.  The daemon's write_cycle_pulse
    emits a timestamp-bearing file; live_health reads that timestamp and allows
    up to 300 s.  A just-written pulse must pass the freshness check.
    """

    def test_fresh_write_cycle_pulse_satisfies_live_health_freshness_window(
        self, tmp_path, monkeypatch
    ):
        """A freshly-emitted write_cycle_pulse satisfies live_health's freshness gate.

        If this fails after the single-writer fix, the daemon's cadence has drifted
        beyond STATUS_FRESH_BUDGET_SECONDS and a different mitigation is needed.
        """
        from src.observability import status_summary as ss_module
        from src.observability.status_summary import write_cycle_pulse
        from src.control.live_health import STATUS_FRESH_BUDGET_SECONDS, _age_seconds

        target = tmp_path / "status_summary.json"
        monkeypatch.setattr(ss_module, "STATUS_PATH", target, raising=True)
        monkeypatch.setattr(
            ss_module,
            "_refresh_minimal_runtime_read_model_for_status",
            lambda status: True,
        )
        monkeypatch.setattr(ss_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(
            ss_module,
            "_refresh_current_open_entry_orders_for_status",
            lambda status: None,
        )
        monkeypatch.setattr(
            ss_module,
            "_refresh_control_status_for_pulse",
            lambda status: status.setdefault("control", {}),
        )
        monkeypatch.setattr(
            ss_module,
            "_refresh_pulse_infrastructure_status",
            lambda status, cycle, risk_level_refreshed_by_pulse: None,
        )

        write_cycle_pulse({"monitors": 0, "exits": 0})

        assert target.exists(), "write_cycle_pulse must create the file"
        payload = json.loads(target.read_text())

        # live_health reads "timestamp" key (line 329-343 in live_health.py).
        ts_str = payload.get("timestamp")
        assert ts_str, (
            f"write_cycle_pulse payload lacks 'timestamp' key; live_health would "
            f"flag STATUS_SUMMARY_NO_TIMESTAMP. Keys present: {sorted(payload.keys())}"
        )

        now = datetime.now(timezone.utc)
        age = _age_seconds(ts_str, now)
        assert age is not None, f"timestamp {ts_str!r} not parseable by _age_seconds"
        assert age <= STATUS_FRESH_BUDGET_SECONDS, (
            f"write_cycle_pulse timestamp is {age:.1f}s old; live_health budget is "
            f"{STATUS_FRESH_BUDGET_SECONDS}s.  Dropping request_status write would "
            f"trigger STATUS_SUMMARY_STALE if daemon cadence exceeds budget."
        )


class TestDaemonWritersKeepCommandProjectionContract:
    @staticmethod
    def _install_command_projection_spies(monkeypatch, ss_module):
        monkeypatch.setattr(
            ss_module,
            "recommended_autosafe_commands_from_status",
            lambda status: [{"command": "autosafe", "risk": status.get("risk", {})}],
        )
        monkeypatch.setattr(
            ss_module,
            "review_required_commands_from_status",
            lambda status: [{"command": "review", "cycle": status.get("cycle", {})}],
        )

        def _recommended(status, *, include_review_required):
            assert include_review_required is True
            return [{"command": "combined", "control": bool(status.get("control"))}]

        monkeypatch.setattr(ss_module, "recommended_commands_from_status", _recommended)

    def test_cycle_result_self_heals_missing_command_projection(self, tmp_path, monkeypatch):
        from src.observability import status_summary as ss_module

        target = tmp_path / "status_summary.json"
        target.write_text(json.dumps({"control": {"entries_paused": False}}))
        monkeypatch.setattr(ss_module, "STATUS_PATH", target, raising=True)
        self._install_command_projection_spies(monkeypatch, ss_module)

        ss_module.write_cycle_result({"mode": "test_result"})

        control = json.loads(target.read_text())["control"]
        assert control["recommended_auto_commands"][0]["command"] == "autosafe"
        assert control["review_required_commands"][0]["command"] == "review"
        assert control["recommended_commands"][0]["command"] == "combined"

    def test_cycle_pulse_self_heals_empty_prior_from_canonical_projection(
        self, tmp_path, monkeypatch
    ):
        from src.observability import status_summary as ss_module

        target = tmp_path / "status_summary.json"
        monkeypatch.setattr(ss_module, "STATUS_PATH", target, raising=True)
        self._install_command_projection_spies(monkeypatch, ss_module)
        monkeypatch.setattr(
            ss_module,
            "_refresh_minimal_runtime_read_model_for_status",
            lambda status: True,
        )
        monkeypatch.setattr(ss_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(
            ss_module,
            "_refresh_current_open_entry_orders_for_status",
            lambda status: None,
        )
        monkeypatch.setattr(
            ss_module,
            "_refresh_control_status_for_pulse",
            lambda status: status.setdefault("control", {"entries_paused": False}),
        )
        monkeypatch.setattr(
            ss_module,
            "_refresh_pulse_infrastructure_status",
            lambda status, cycle, risk_level_refreshed_by_pulse: None,
        )

        ss_module.write_cycle_pulse({"mode": "test_pulse"})

        control = json.loads(target.read_text())["control"]
        assert control["recommended_auto_commands"][0]["command"] == "autosafe"
        assert control["review_required_commands"][0]["command"] == "review"
        assert control["recommended_commands"][0]["command"] == "combined"

    def test_full_status_writer_uses_the_same_command_projection(
        self, tmp_path, monkeypatch
    ):
        from src.observability import status_summary as ss_module
        from src.runtime import bankroll_provider

        target = tmp_path / "status_summary.json"
        monkeypatch.setattr(ss_module, "STATUS_PATH", target, raising=True)
        self._install_command_projection_spies(monkeypatch, ss_module)
        monkeypatch.setattr(ss_module, "refresh_control_state", lambda: None)
        monkeypatch.setattr(ss_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(ss_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(ss_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(ss_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(ss_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(ss_module, "get_entries_pause_evidence", lambda: {})
        monkeypatch.setattr(ss_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(ss_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(
            ss_module,
            "get_trade_connection_with_world",
            lambda: (_ for _ in ()).throw(RuntimeError("test has no canonical DB")),
        )
        monkeypatch.setattr(ss_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(bankroll_provider, "current", lambda: None)

        ss_module.write_status({"mode": "test_full"})

        control = json.loads(target.read_text())["control"]
        assert control["recommended_auto_commands"][0]["command"] == "autosafe"
        assert control["review_required_commands"][0]["command"] == "review"
        assert control["recommended_commands"][0]["command"] == "combined"
