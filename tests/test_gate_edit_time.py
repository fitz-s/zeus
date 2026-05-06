# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §5 Gate 1; IMPLEMENTATION_PLAN §6 days 51-55;
#                  ANTI_DRIFT_CHARTER §3 (ritual_signal M1)

"""Tests for Gate 1: edit-time Write-tool capability hook.

Three mandatory tests per deliverable spec:
  1. Allow on non-blocking reversibility class (e.g. WORKING -> advisory)
  2. Refuse on blocking class without ARCH_PLAN_EVIDENCE
  3. Allow on blocking class with valid ARCH_PLAN_EVIDENCE
"""

from __future__ import annotations

import os
import pathlib
import unittest.mock as mock

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent


class TestGateEditTimeAllowNonBlocking:
    """Test 1: paths whose capability reversibility_class is non-blocking -> allow."""

    def test_allow_path_with_no_capability_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A path that matches no capability is always allowed."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_EDIT", raising=False)
        monkeypatch.delenv("ARCH_PLAN_EVIDENCE", raising=False)

        from src.architecture.gate_edit_time import evaluate

        allowed, msg = evaluate(["scripts/some_utility_script.py"])
        assert allowed, f"Expected allow for non-capability path, got: {msg}"
        assert "ALLOWED" in msg or "SKIPPED" in msg

    def test_allow_working_class_capability_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A path with WORKING (advisory) reversibility_class is allowed without evidence."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_EDIT", raising=False)
        monkeypatch.delenv("ARCH_PLAN_EVIDENCE", raising=False)

        from src.architecture.gate_edit_time import evaluate

        # control_write capability owns src/control/control_plane.py -> WORKING class
        allowed, msg = evaluate(["src/control/control_plane.py"])
        assert allowed, f"Expected allow for WORKING class path, got: {msg}"


class TestGateEditTimeRefuseBlockingNoEvidence:
    """Test 2: blocking class path without evidence -> refuse."""

    def test_refuse_truth_rewrite_without_evidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRUTH_REWRITE class (canonical_position_write) -> blocking -> refuse without evidence."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_EDIT", raising=False)
        monkeypatch.delenv("ARCH_PLAN_EVIDENCE", raising=False)

        from src.architecture.gate_edit_time import evaluate

        # src/state/ledger.py is a hard_kernel_path for canonical_position_write -> TRUTH_REWRITE
        allowed, msg = evaluate(["src/state/ledger.py"])
        assert not allowed, (
            f"Expected refuse for TRUTH_REWRITE blocking path without evidence, got allow. "
            f"Message: {msg}"
        )
        assert "BLOCKED" in msg
        assert "ARCH_PLAN_EVIDENCE" in msg

    def test_refuse_on_chain_without_evidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ON_CHAIN class -> blocking -> refuse without evidence."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_EDIT", raising=False)
        monkeypatch.delenv("ARCH_PLAN_EVIDENCE", raising=False)

        from src.architecture import gate_edit_time
        from src.architecture.route_function import RouteCard

        fake_card = RouteCard(
            capabilities=["live_venue_submit"],
            invariants=[],
            relationship_tests=[],
            hard_kernel_hits=["src/execution/live_executor.py"],
            reversibility="ON_CHAIN",
            leases=["live_venue_submit"],
        )

        # route is imported at module level in gate_edit_time; patch it there.
        with mock.patch("src.architecture.gate_edit_time.route", return_value=fake_card):
            allowed, msg = gate_edit_time.evaluate(["src/execution/live_executor.py"])

        assert not allowed, f"Expected refuse for ON_CHAIN path without evidence, got: {msg}"
        assert "BLOCKED" in msg


class TestGateEditTimeAllowBlockingWithEvidence:
    """Test 3: blocking class path with valid ARCH_PLAN_EVIDENCE -> allow."""

    def test_allow_truth_rewrite_with_valid_evidence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """TRUTH_REWRITE blocking path is allowed when ARCH_PLAN_EVIDENCE points to existing file."""
        monkeypatch.delenv("ZEUS_ROUTE_GATE_EDIT", raising=False)

        plan_file = tmp_path / "test_plan.md"
        plan_file.write_text("# Test plan evidence\n")
        monkeypatch.setenv("ARCH_PLAN_EVIDENCE", str(plan_file))

        from src.architecture.gate_edit_time import evaluate

        allowed, msg = evaluate(["src/state/ledger.py"])
        assert allowed, (
            f"Expected allow for TRUTH_REWRITE path with valid evidence, got refuse. "
            f"Message: {msg}"
        )
        assert "BLOCKED" not in msg


class TestGateEditTimeFeatureFlag:
    """Feature flag ZEUS_ROUTE_GATE_EDIT=off skips all checks."""

    def test_flag_off_skips_blocking_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZEUS_ROUTE_GATE_EDIT", "off")
        monkeypatch.delenv("ARCH_PLAN_EVIDENCE", raising=False)

        from src.architecture.gate_edit_time import evaluate

        allowed, msg = evaluate(["src/state/ledger.py"])
        assert allowed, f"Expected allow with feature flag off, got: {msg}"
        assert "SKIPPED" in msg


class TestGateEditTimeRitualSignal:
    """Verify ritual_signal is emitted and has correct schema."""

    def test_signal_emitted_on_evaluation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """evaluate() emits a ritual_signal JSON line."""
        import json

        monkeypatch.delenv("ZEUS_ROUTE_GATE_EDIT", raising=False)
        monkeypatch.delenv("ARCH_PLAN_EVIDENCE", raising=False)

        import src.architecture.gate_edit_time as g
        orig_dir = g._RITUAL_SIGNAL_DIR
        g._RITUAL_SIGNAL_DIR = tmp_path / "ritual_signal"
        try:
            g.evaluate(["src/state/ledger.py"])
        finally:
            g._RITUAL_SIGNAL_DIR = orig_dir

        signal_files = list((tmp_path / "ritual_signal").glob("*.jsonl"))
        assert signal_files, "No ritual_signal .jsonl file emitted"

        lines = signal_files[0].read_text().strip().splitlines()
        assert lines, "ritual_signal file is empty"

        record = json.loads(lines[0])
        assert record["helper"] == "gate_edit_time"
        assert "cap_id" in record
        assert "decision" in record
        assert "charter_version" in record
        assert record["charter_version"] == "1.0.0"
