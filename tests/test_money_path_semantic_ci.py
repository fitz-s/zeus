# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Self-defense tests for money-path semantic CI helper scripts.
# Reuse: Run when changing scripts/ci money-path classifier/coverage/test-quality gates.
# Authority basis: architecture/money_path_objects.yaml; architecture/money_path_ci.yaml; architecture/test_quality.yaml
"""Self-defense tests for money-path semantic CI helper scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_classifier_cli_fails_on_unregistered_redeem_state(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/execution/settlement_commands.py b/src/execution/settlement_commands.py\n"
        "+++ b/src/execution/settlement_commands.py\n"
        "+REDEEM_AUTORETRYABLE_REVIEW = 'REDEEM_AUTORETRYABLE_REVIEW'\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert "state:REDEEM_AUTORETRYABLE_REVIEW" in payload["unregistered_objects"]


def test_classifier_cli_fails_on_unregistered_intent_state(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/state/venue_command_repo.py b/src/state/venue_command_repo.py\n"
        "+++ b/src/state/venue_command_repo.py\n"
        "+INTENT_CREATED_V2 = 'INTENT_CREATED_V2'\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert "state:INTENT_CREATED_V2" in payload["unregistered_objects"]


def test_semantic_ci_registry_changes_select_self_defense_tests(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/architecture/money_path_ci.yaml b/architecture/money_path_ci.yaml\n"
        "+++ b/architecture/money_path_ci.yaml\n"
        "+  MP-NEW-001:\n"
        "+    description: new invariant\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert "MP-CI-001" in payload["required_invariants"]
    assert "tests/test_money_path_semantic_ci.py" in payload["tests"]


def test_invariant_coverage_rejects_missing_selected_test() -> None:
    classification = {
        "required_invariants": ["MP-SCH-001"],
        "tests": ["tests/test_semantic_linter.py"],
    }
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/assert_invariant_coverage.py",
            "--classification-json",
            json.dumps(classification),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "MP-SCH-001" in proc.stdout
    assert "none of registered tests selected" in proc.stdout


def test_test_quality_gate_accepts_registered_money_path_tests() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/ci/assert_test_quality.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "money-path test quality OK" in proc.stdout
