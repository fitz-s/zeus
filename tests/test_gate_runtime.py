# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §6 days 68-70 (Gate 5);
#                  ULTIMATE_DESIGN §5 Gate 5; ANTI_DRIFT_CHARTER §3 M1.

"""Tests for Gate 5: runtime kill-switch and settlement-window-freeze enforcement.

Three mandatory tests per deliverable spec (D-2):
  1. Refuse live_venue_submit when kill_switch_active.
  2. Refuse settlement_write when settlement_window_freeze_active.
  3. Allow both when all-clear.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest


REPO_ROOT = pathlib.Path(__file__).parent.parent


class TestGateRuntimeKillSwitch:
    """Test 1: gate_runtime.check("live_venue_submit") raises when kill switch armed."""

    def test_refuse_live_venue_submit_on_kill_switch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """kill_switch_active blocks live_venue_submit with RuntimeError."""
        monkeypatch.setenv("ZEUS_KILL_SWITCH", "1")
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        # Redirect ritual_signal writes to tmp dir so tests don't pollute real logs.
        monkeypatch.setattr(
            "src.architecture.gate_runtime._RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal"
        )

        from src.architecture import gate_runtime
        import importlib
        importlib.reload(gate_runtime)
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="kill_switch_active"):
            gate_runtime.check("live_venue_submit")

    def test_refuse_live_venue_submit_on_risk_halt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """risk_level_halt blocks live_venue_submit with RuntimeError."""
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.setenv("ZEUS_RISK_HALT", "true")
        monkeypatch.setattr(
            "src.architecture.gate_runtime._RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal"
        )

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="risk_level_halt"):
            gate_runtime.check("live_venue_submit")


class TestGateRuntimeSettlementFreeze:
    """Test 2: gate_runtime.check("settlement_write") raises when freeze active."""

    def test_refuse_settlement_write_on_freeze(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """settlement_window_freeze_active blocks settlement_write with RuntimeError."""
        monkeypatch.setenv("ZEUS_SETTLEMENT_FREEZE", "on")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="settlement_window_freeze_active"):
            gate_runtime.check("settlement_write")


class TestGateRuntimeAllClear:
    """Test 3: allow when all conditions are clear."""

    def test_allow_live_venue_submit_all_clear(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """All conditions clear => no exception raised; ritual_signal emitted with allow."""
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)

        from src.architecture import gate_runtime
        sig_dir = tmp_path / "ritual_signal"
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", sig_dir)

        # Should not raise
        gate_runtime.check("live_venue_submit")
        gate_runtime.check("settlement_write")

        # Verify ritual_signal written
        jsonl_files = list(sig_dir.glob("*.jsonl"))
        assert jsonl_files, "Expected ritual_signal log to be written"
        lines = jsonl_files[0].read_text().strip().splitlines()
        assert len(lines) >= 2
        records = [json.loads(l) for l in lines]
        decisions = {r["decision"] for r in records}
        assert "allow" in decisions, f"Expected at least one 'allow' decision; got {decisions}"

    def test_allow_emits_ritual_signal_with_required_schema(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Emitted ritual_signal must have cap_id, gate_id, decision, ts fields."""
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)

        from src.architecture import gate_runtime
        sig_dir = tmp_path / "ritual_signal"
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", sig_dir)

        gate_runtime.check("live_venue_submit")

        jsonl_files = list(sig_dir.glob("*.jsonl"))
        assert jsonl_files
        record = json.loads(jsonl_files[0].read_text().strip().splitlines()[-1])
        for field in ("cap_id", "gate_id", "decision", "ts", "invocation_ts", "charter_version"):
            assert field in record, f"Missing required ritual_signal field: {field!r}"
        assert record["gate_id"] == "gate5_runtime"
        assert record["cap_id"] == "live_venue_submit"
        assert record["decision"] == "allow"


class TestGateRuntimeSettlementFreezeBlocksLiveEntry:
    """Test: ZEUS_SETTLEMENT_FREEZE=1 blocks live_venue_submit (execute_intent / execute_final_intent paths).

    Per capabilities.yaml live_venue_submit.blocked_when: [settlement_window_freeze_active]
    and ULTIMATE_DESIGN §5 Gate 5 (line 181).  PR #71 Codex P1 fix.
    """

    def test_settlement_freeze_blocks_live_venue_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """ZEUS_SETTLEMENT_FREEZE=1 must raise RuntimeError on live_venue_submit."""
        monkeypatch.setenv("ZEUS_SETTLEMENT_FREEZE", "1")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="settlement_window_freeze_active"):
            gate_runtime.check("live_venue_submit")

    def test_settlement_freeze_true_blocks_live_venue_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """ZEUS_SETTLEMENT_FREEZE=true also blocks (all truthy variants)."""
        monkeypatch.setenv("ZEUS_SETTLEMENT_FREEZE", "true")
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        with pytest.raises(RuntimeError, match="settlement_window_freeze_active"):
            gate_runtime.check("live_venue_submit")

    def test_settlement_freeze_off_allows_live_venue_submit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """ZEUS_SETTLEMENT_FREEZE unset does not block live_venue_submit."""
        monkeypatch.delenv("ZEUS_SETTLEMENT_FREEZE", raising=False)
        monkeypatch.delenv("ZEUS_KILL_SWITCH", raising=False)
        monkeypatch.delenv("ZEUS_RISK_HALT", raising=False)

        from src.architecture import gate_runtime
        monkeypatch.setattr(gate_runtime, "_RITUAL_SIGNAL_DIR", tmp_path / "ritual_signal")

        # Should not raise
        gate_runtime.check("live_venue_submit")
