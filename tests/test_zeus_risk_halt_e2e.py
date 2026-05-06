# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: evidence/phase4_h_decision.md L-1; IMPLEMENTATION_PLAN §6 Gate 5;
#                  src/architecture/gate_runtime.py; capabilities.yaml live_venue_submit

"""L-1 carry-forward: ZEUS_RISK_HALT E2E test.

Phase 4 critic L-1: _assert_risk_level_allows() is a documented no-op but still called;
no test covers the ZEUS_RISK_HALT path end-to-end through gate_runtime.

These tests verify:
  1. ZEUS_RISK_HALT=1 causes gate_runtime.check("live_venue_submit") to raise RuntimeError.
  2. execute_intent and execute_final_intent are refused when ZEUS_RISK_HALT=1.
  3. The ritual_signal entry emitted on ZEUS_RISK_HALT has severity="RUNTIME_BLOCK" and
     outcome="blocked" (regression guard — confirms gate emits telemetry on halt).

Env-var canonical name: ZEUS_RISK_HALT (confirmed via gate_runtime.py:_risk_level_halt)
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_last_ritual_signal(log_dir: pathlib.Path) -> dict | None:
    """Return the last JSON line from the most recent ritual_signal file in log_dir."""
    files = sorted(log_dir.glob("*.jsonl"))
    if not files:
        return None
    lines = files[-1].read_text().strip().splitlines()
    if not lines:
        return None
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# B-2-1: gate_runtime.check() raises on ZEUS_RISK_HALT=1
# ---------------------------------------------------------------------------

class TestGateRuntimeRiskHalt:
    """Direct unit test of gate_runtime.check() under ZEUS_RISK_HALT."""

    def test_risk_halt_blocks_live_venue_submit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ZEUS_RISK_HALT=1 must cause gate_runtime.check('live_venue_submit') to raise RuntimeError."""
        monkeypatch.setenv("ZEUS_RISK_HALT", "1")
        # Clear kill switch so only risk_halt fires
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)

        from src.architecture.gate_runtime import check

        with pytest.raises(RuntimeError, match="risk_level_halt|RISK_HALT|blocked|gate"):
            check("live_venue_submit")

    def test_risk_halt_inactive_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ZEUS_RISK_HALT unset must NOT block gate_runtime.check('live_venue_submit')
        (assuming ZEUS_KILL_SWITCH is also unset)."""
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)

        from src.architecture.gate_runtime import check

        # Must not raise
        check("live_venue_submit")

    def test_risk_halt_zero_does_not_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ZEUS_RISK_HALT=0 must NOT activate the halt condition."""
        monkeypatch.setenv("ZEUS_RISK_HALT", "0")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)

        from src.architecture.gate_runtime import check

        check("live_venue_submit")  # must not raise


# ---------------------------------------------------------------------------
# B-2-2: execute_intent / execute_final_intent refused under ZEUS_RISK_HALT
# ---------------------------------------------------------------------------

class TestExecuteIntentRiskHalt:
    """E2E: live execution functions refused when ZEUS_RISK_HALT=1."""

    def test_execute_intent_refused_on_risk_halt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """execute_intent with ZEUS_RISK_HALT=1 must raise RuntimeError before any state mutation."""
        monkeypatch.setenv("ZEUS_RISK_HALT", "1")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)

        from src.execution.executor import execute_intent
        from src.contracts import ExecutionIntent

        mock_intent = MagicMock(spec=ExecutionIntent)
        mock_intent.limit_price = 0.55
        mock_intent.token_id = "tok_abc"
        mock_intent.target_size_usd = 10.0

        with pytest.raises(RuntimeError, match="risk_level_halt|RISK_HALT|blocked|gate"):
            execute_intent(mock_intent, edge_vwmp=0.0, label="test_risk_halt")

    def test_execute_final_intent_refused_on_risk_halt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """execute_final_intent with ZEUS_RISK_HALT=1 must raise RuntimeError before venue I/O."""
        monkeypatch.setenv("ZEUS_RISK_HALT", "1")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)

        from src.execution.executor import execute_final_intent
        from src.contracts import FinalExecutionIntent

        mock_intent = MagicMock(spec=FinalExecutionIntent)

        with pytest.raises(RuntimeError, match="risk_level_halt|RISK_HALT|blocked|gate"):
            execute_final_intent(mock_intent)


# ---------------------------------------------------------------------------
# B-2-3: ritual_signal emitted with severity=RUNTIME_BLOCK on ZEUS_RISK_HALT
# ---------------------------------------------------------------------------

class TestRiskHaltRitualSignal:
    """Verify gate_runtime emits a ritual_signal entry with correct schema on risk halt."""

    def test_ritual_signal_emitted_on_risk_halt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """ZEUS_RISK_HALT=1 must cause gate_runtime to emit ritual_signal with
        severity='RUNTIME_BLOCK' and outcome='blocked'."""
        monkeypatch.setenv("ZEUS_RISK_HALT", "1")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)

        # Redirect ritual_signal writes to tmp_path
        import src.architecture.gate_runtime as gr_module
        monkeypatch.setattr(gr_module, "_RITUAL_SIGNAL_DIR", tmp_path)

        from src.architecture.gate_runtime import check

        with pytest.raises(RuntimeError):
            check("live_venue_submit")

        last = _read_last_ritual_signal(tmp_path)
        assert last is not None, "ritual_signal file must be written on risk halt"
        assert last.get("outcome") == "blocked", (
            f"outcome must be 'blocked' on risk halt, got {last.get('outcome')!r}"
        )
        assert last.get("severity") == "RUNTIME_BLOCK", (
            f"severity must be 'RUNTIME_BLOCK' on risk halt, got {last.get('severity')!r}"
        )
        assert last.get("condition") == "risk_level_halt", (
            f"condition must be 'risk_level_halt', got {last.get('condition')!r}"
        )
        assert last.get("cap_id") == "live_venue_submit", (
            f"cap_id must be 'live_venue_submit', got {last.get('cap_id')!r}"
        )
        assert last.get("decision") == "refuse", (
            f"decision must be 'refuse', got {last.get('decision')!r}"
        )
