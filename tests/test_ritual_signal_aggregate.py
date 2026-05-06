# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ANTI_DRIFT_CHARTER §3 M1; IMPLEMENTATION_PLAN Phase 5.A deliverable A-1
"""tests/test_ritual_signal_aggregate.py — unit tests for ritual_signal_aggregate.py.

Feeds a synthetic 5-line log fixture and asserts aggregation matches expected distribution.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


class TestRitualSignalAggregate:
    """A-1: Synthetic fixture tests for ritual_signal_aggregate."""

    # Five synthetic log lines covering distinct helpers, cap_ids, and decisions.
    _FIXTURE_LINES = [
        {
            "helper": "gate_edit_time",
            "task_id": "aaaa0001",
            "fit_score": 1.0,
            "advisory_or_blocking": "blocking",
            "outcome": "applied",
            "diff_paths_touched": ["src/state/ledger.py"],
            "invocation_ts": "2026-05-06T10:00:00+00:00",
            "charter_version": "1.0.0",
            "cap_id": "canonical_position_write",
            "severity": "TRUTH_REWRITE",
            "decision": "refuse",
            "evidence_path": None,
        },
        {
            "helper": "gate_edit_time",
            "task_id": "aaaa0002",
            "fit_score": 1.0,
            "advisory_or_blocking": "advisory",
            "outcome": "applied",
            "diff_paths_touched": ["scripts/some_utility.py"],
            "invocation_ts": "2026-05-06T11:00:00+00:00",
            "charter_version": "1.0.0",
            "cap_id": "(none)",
            "severity": "WORKING",
            "decision": "allow",
            "evidence_path": None,
        },
        {
            "helper": "gate_commit_time",
            "task_id": "bbbb0001",
            "fit_score": 1.0,
            "advisory_or_blocking": "advisory",
            "outcome": "applied",
            "diff_paths_touched": ["src/execution/live_executor.py"],
            "invocation_ts": "2026-05-06T12:00:00+00:00",
            "charter_version": "1.0.0",
            "cap_id": "live_venue_submit",
            "severity": "ON_CHAIN",
            "decision": "warn",
            "evidence_path": None,
        },
        {
            "helper": "gate_runtime",
            "task_id": "cccc0001",
            "fit_score": 1.0,
            "advisory_or_blocking": "blocking",
            "outcome": "applied",
            "diff_paths_touched": [],
            "invocation_ts": "2026-05-06T13:00:00+00:00",
            "charter_version": "1.0.0",
            "cap_id": "live_venue_submit",
            "decision": "allow",
        },
        {
            "helper": "gate_runtime",
            "task_id": "cccc0002",
            "fit_score": 1.0,
            "advisory_or_blocking": "blocking",
            "outcome": "applied",
            "diff_paths_touched": [],
            "invocation_ts": "2026-05-06T14:00:00+00:00",
            "charter_version": "1.0.0",
            "cap_id": "live_venue_submit",
            "decision": "refuse",
        },
    ]

    @pytest.fixture()
    def fixture_log_dir(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Write synthetic fixture to a temp directory and return it."""
        log_dir = tmp_path / "ritual_signal"
        log_dir.mkdir()
        log_file = log_dir / "2026-05.jsonl"
        log_file.write_text(
            "\n".join(json.dumps(e) for e in self._FIXTURE_LINES) + "\n"
        )
        return log_dir

    def test_aggregate_total_count(self, fixture_log_dir: pathlib.Path) -> None:
        """all_time.total must equal the 5 fixture entries."""
        from ritual_signal_aggregate import aggregate

        result = aggregate(fixture_log_dir)
        assert result["all_time"]["total"] == 5

    def test_aggregate_per_gate_counts(self, fixture_log_dir: pathlib.Path) -> None:
        """per_gate must reflect 2 gate_edit_time, 1 gate_commit_time, 2 gate_runtime."""
        from ritual_signal_aggregate import aggregate

        result = aggregate(fixture_log_dir)
        per_gate = result["all_time"]["per_gate"]
        assert per_gate.get("gate_edit_time") == 2
        assert per_gate.get("gate_commit_time") == 1
        assert per_gate.get("gate_runtime") == 2

    def test_aggregate_per_cap_id_distribution(self, fixture_log_dir: pathlib.Path) -> None:
        """per_cap_id must reflect the fixture's cap_id distribution."""
        from ritual_signal_aggregate import aggregate

        result = aggregate(fixture_log_dir)
        per_cap = result["all_time"]["per_cap_id"]
        # canonical_position_write: 1, (none): 1, live_venue_submit: 3
        assert per_cap.get("canonical_position_write") == 1
        assert per_cap.get("(none)") == 1
        assert per_cap.get("live_venue_submit") == 3

    def test_aggregate_per_decision_counts(self, fixture_log_dir: pathlib.Path) -> None:
        """per_decision must reflect refuse:2, allow:2, warn:1."""
        from ritual_signal_aggregate import aggregate

        result = aggregate(fixture_log_dir)
        per_decision = result["all_time"]["per_decision"]
        assert per_decision.get("refuse") == 2
        assert per_decision.get("allow") == 2
        assert per_decision.get("warn") == 1

    def test_aggregate_windows_present(self, fixture_log_dir: pathlib.Path) -> None:
        """Result must contain '24h', '7d', '30d' window keys."""
        from ritual_signal_aggregate import aggregate

        result = aggregate(fixture_log_dir)
        assert "windows" in result
        for window in ("24h", "7d", "30d"):
            assert window in result["windows"], f"Missing window: {window!r}"

    def test_aggregate_empty_log_dir(self, tmp_path: pathlib.Path) -> None:
        """Empty log dir returns all_time.total == 0 without error."""
        from ritual_signal_aggregate import aggregate

        empty_dir = tmp_path / "empty_ritual_signal"
        empty_dir.mkdir()
        result = aggregate(empty_dir)
        assert result["all_time"]["total"] == 0

    def test_aggregate_nonexistent_log_dir(self, tmp_path: pathlib.Path) -> None:
        """Nonexistent log dir returns all_time.total == 0 without error."""
        from ritual_signal_aggregate import aggregate

        nonexistent = tmp_path / "no_such_dir"
        result = aggregate(nonexistent)
        assert result["all_time"]["total"] == 0

    def test_aggregate_output_has_generated_at(self, fixture_log_dir: pathlib.Path) -> None:
        """Result must carry 'generated_at' ISO timestamp."""
        from ritual_signal_aggregate import aggregate

        result = aggregate(fixture_log_dir)
        assert "generated_at" in result
        assert result["generated_at"]  # non-empty
