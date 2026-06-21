# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: status dual-writer oscillation fix 2026-06-10
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
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CONTROL_PLANE = PROJECT_ROOT / "src" / "control" / "control_plane.py"
POST_TRADE_CAPITAL = PROJECT_ROOT / "src" / "execution" / "post_trade_capital.py"


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
