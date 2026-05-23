"""Tests for scripts/review_scope_collect.py — tier classification + advisory logic.

# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: REVIEW.md §4 (Tier 0/1/2/3 definitions)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make scripts/ importable without PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from review_scope_collect import advisory_fail, classify, main  # noqa: E402


# ---------------------------------------------------------------------------
# classify() — tier correctness
# ---------------------------------------------------------------------------

class TestClassify:
    # Tier 0 surfaces
    def test_execution_file_is_tier0(self):
        assert classify("src/execution/executor.py") == 0

    def test_venue_file_is_tier0(self):
        assert classify("src/venue/polymarket_adapter.py") == 0

    def test_settlement_semantics_is_tier0(self):
        assert classify("src/contracts/settlement_semantics.py") == 0

    def test_execution_price_is_tier0(self):
        assert classify("src/contracts/execution_price.py") == 0

    def test_venue_submission_envelope_is_tier0(self):
        assert classify("src/contracts/venue_submission_envelope.py") == 0

    def test_fx_classification_is_tier0(self):
        assert classify("src/contracts/fx_classification.py") == 0

    def test_state_db_is_tier0(self):
        assert classify("src/state/db.py") == 0

    def test_state_lifecycle_manager_is_tier0(self):
        assert classify("src/state/lifecycle_manager.py") == 0

    def test_riskguard_is_tier0(self):
        assert classify("src/riskguard/core.py") == 0

    def test_control_is_tier0(self):
        assert classify("src/control/kill_switch.py") == 0

    def test_supervisor_api_is_tier0(self):
        assert classify("src/supervisor_api/handler.py") == 0

    def test_main_is_tier0(self):
        assert classify("src/main.py") == 0

    def test_migrations_is_tier0(self):
        assert classify("migrations/0042_add_column.sql") == 0

    def test_hard_safety_kernel_is_tier0(self):
        assert classify("scripts/topology_v_next/hard_safety_kernel.py") == 0

    def test_admission_engine_is_tier0(self):
        assert classify("scripts/topology_v_next/admission_engine.py") == 0

    def test_safety_overrides_is_tier0(self):
        assert classify("bindings/zeus/safety_overrides.yaml") == 0

    # Tier 1 surfaces
    def test_calibration_is_tier1(self):
        assert classify("src/calibration/platt_fitter.py") == 1

    def test_signal_is_tier1(self):
        assert classify("src/signal/ensemble.py") == 1

    def test_strategy_is_tier1(self):
        assert classify("src/strategy/market_phase.py") == 1

    def test_contracts_calibration_bins_is_tier1(self):
        assert classify("src/contracts/calibration_bins.py") == 1

    def test_state_portfolio_is_tier1(self):
        assert classify("src/state/portfolio.py") == 1

    # Tier 2 — tests
    def test_test_file_is_tier2(self):
        assert classify("tests/test_execution.py") == 2

    def test_contracts_test_is_tier2(self):
        assert classify("tests/contracts/test_settlement.py") == 2

    def test_invariant_test_is_tier2(self):
        assert classify("tests/test_something_invariant_something.py") == 2

    def test_architecture_contracts_test_is_tier2(self):
        assert classify("tests/test_architecture_contracts.py") == 2

    # Tier 3 — docs / instructions / agent surfaces
    def test_agents_md_is_tier3(self):
        assert classify("AGENTS.md") == 3

    def test_review_md_is_tier3(self):
        assert classify("REVIEW.md") == 3

    def test_docs_operations_is_tier3(self):
        assert classify("docs/operations/current/index.md") == 3

    def test_architecture_yaml_is_tier3(self):
        assert classify("architecture/topology.yaml") == 3

    def test_github_workflow_is_tier3(self):
        assert classify(".github/workflows/review-scope.yml") == 3

    def test_docs_review_is_tier3(self):
        assert classify("docs/review/code_review.md") == 3

    # Skip / deprioritized
    def test_docs_archives_is_skip(self):
        assert classify("docs/archives/old_report.md") == 9

    def test_omc_is_skip(self):
        assert classify(".omc/plans/something.md") == 9

    def test_log_is_skip(self):
        assert classify("logs/daemon.log") == 9

    def test_pyc_is_skip(self):
        assert classify("src/execution/__pycache__/executor.cpython-313.pyc") == 9


# ---------------------------------------------------------------------------
# advisory_fail() logic
# ---------------------------------------------------------------------------

class TestAdvisoryFail:
    def test_no_tier0_no_fail(self):
        assert advisory_fail([], {"docs/review/code_review.md"}) is False

    def test_tier0_with_test_no_fail(self):
        tier0 = ["src/execution/executor.py"]
        all_files = {"src/execution/executor.py", "tests/test_executor.py"}
        assert advisory_fail(tier0, all_files) is False

    def test_tier0_without_test_fails(self):
        tier0 = ["src/execution/executor.py"]
        all_files = {"src/execution/executor.py", "docs/review/notes.md"}
        assert advisory_fail(tier0, all_files) is True

    def test_tier0_with_tests_subdir_no_fail(self):
        tier0 = ["src/venue/adapter.py"]
        all_files = {"src/venue/adapter.py", "tests/contracts/test_venue.py"}
        assert advisory_fail(tier0, all_files) is False


# ---------------------------------------------------------------------------
# main() integration — exit codes + JSON output
# ---------------------------------------------------------------------------

class TestMain:
    def test_docs_only_exit0(self):
        """Docs-only diff → exit 0 (no Tier-0 → no advisory fail)."""
        rc = main([
            "docs/review/code_review.md",
            "REVIEW.md",
            "architecture/topology.yaml",
        ])
        assert rc == 0

    def test_tier0_without_tests_exit1(self):
        """Tier-0 file + no tests → advisory exit 1."""
        rc = main(["src/execution/executor.py", "docs/review/notes.md"])
        assert rc == 1

    def test_tier0_with_tests_exit0(self):
        """Tier-0 file + test file → exit 0."""
        rc = main(["src/execution/executor.py", "tests/test_executor.py"])
        assert rc == 0

    def test_json_output_structure(self, capsys):
        main(["--json", "src/execution/executor.py", "tests/test_executor.py"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "summary" in data
        assert "files" in data
        assert data["summary"]["tier0"] == 1
        assert data["summary"]["tier2"] == 1
        assert data["summary"]["advisory_fail"] is False

    def test_json_advisory_flag_set(self, capsys):
        main(["--json", "src/execution/executor.py"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["summary"]["advisory_fail"] is True

    def test_skip_files_counted_correctly(self, capsys):
        main(["--json", "docs/archives/old.md", "REVIEW.md"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["summary"]["skip"] == 1
        assert data["summary"]["tier3"] == 1
