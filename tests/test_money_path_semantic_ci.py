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


def test_classifier_accepts_registered_reactor_runtime_config(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "+++ b/src/main.py\n"
        "+float(os.environ.get('ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS', '300.0'))\n",
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

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert "ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS" in payload["new_states"]
    assert "state:ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS" not in payload[
        "unregistered_objects"
    ]


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


def test_submit_order_patterns_include_place_limit_order() -> None:
    """P1-5 antibody: place_limit_order must be a registered submit_order side-effect pattern.
    Missing patterns allow undetected order submission paths to bypass MP-SIDE invariants.
    """
    import yaml

    objects_path = ROOT / "architecture" / "money_path_objects.yaml"
    data = yaml.safe_load(objects_path.read_text(encoding="utf-8"))
    patterns = data["side_effect_calls"]["submit_order"]["patterns"]
    for expected in ("place_limit_order", "place_market_order", "post_order", "build_order"):
        assert expected in patterns, (
            f"{expected} missing from submit_order.patterns — "
            "semantic classifier will not flag this as a side-effect path"
        )


def test_copilot_instruction_change_routes_to_self_defense_segment(tmp_path: Path) -> None:
    """P1-2 antibody: .github/copilot-instructions.md changes must select MP-CI-001
    and the self-defense tests. Without this, Copilot instruction drift is invisible
    to the semantic CI gate.
    """
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/.github/copilot-instructions.md b/.github/copilot-instructions.md\n"
        "+++ b/.github/copilot-instructions.md\n"
        "+# changed review guidance\n",
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

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "MP-CI-001" in payload["required_invariants"], (
        f"MP-CI-001 not in {payload['required_invariants']} — "
        ".github/copilot-instructions.md not routed to semantic_ci_self_defense segment"
    )


def test_strategy_profile_registry_change_routes_to_strategy_authority(tmp_path: Path) -> None:
    """P1-6 antibody: architecture/strategy_profile_registry.yaml changes must select
    MP-STR-001/STR-002 and the strategy_authority tests. Without this, registry changes
    that add/remove strategies bypass the governance gate.
    """
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/architecture/strategy_profile_registry.yaml"
        " b/architecture/strategy_profile_registry.yaml\n"
        "+++ b/architecture/strategy_profile_registry.yaml\n"
        "+  new_strategy:\n"
        "+    breakeven_win_rate: 0.52\n",
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

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "MP-STR-001" in payload["required_invariants"] or "MP-STR-002" in payload["required_invariants"], (
        f"Neither MP-STR-001 nor MP-STR-002 in {payload['required_invariants']} — "
        "architecture/strategy_profile_registry.yaml not routed to strategy_authority segment"
    )
