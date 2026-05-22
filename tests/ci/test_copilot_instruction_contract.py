# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: §3/4 Copilot-review-system design; MP-CI-001
"""Antibody tests for Copilot instruction CI contract (budget, applyTo, coverage).

Ensures check_copilot_instruction_budget.py and check_review_instruction_coverage.py
enforce the instruction file rules: ≤3600 chars, applyTo required, no vague phrases.
These tests catch regressions where someone adds an oversized or applyTo-less instruction
file that would silently be accepted by CI.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS_CI = Path(__file__).parent.parent.parent / "scripts" / "ci"
sys.path.insert(0, str(SCRIPTS_CI))

from check_copilot_instruction_budget import check_file as budget_check_file, CHAR_BUDGET
from check_review_instruction_coverage import parse_apply_to


class TestBudgetChecker:
    """check_copilot_instruction_budget.py contract."""

    def test_file_within_budget_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "test.instructions.md"
        f.write_text("---\napplyTo: \"src/**\"\n---\n" + "x" * 100)
        assert budget_check_file(f) == []

    def test_file_exceeding_budget_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "oversized.instructions.md"
        f.write_text("---\napplyTo: \"src/**\"\n---\n" + "x" * (CHAR_BUDGET + 1))
        violations = budget_check_file(f)
        assert any("exceeds budget" in v for v in violations), violations

    def test_missing_apply_to_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "noapply.instructions.md"
        f.write_text("---\n# no applyTo here\n---\nsome content\n")
        violations = budget_check_file(f)
        assert any("applyTo" in v for v in violations), violations

    def test_missing_frontmatter_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "nofm.instructions.md"
        f.write_text("# No frontmatter at all\nsome content\n")
        violations = budget_check_file(f)
        assert any("frontmatter" in v for v in violations), violations

    def test_vague_phrase_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "vague.instructions.md"
        f.write_text("---\napplyTo: \"src/**\"\n---\nEnsure proper handling of X.\n")
        violations = budget_check_file(f)
        assert any("ensure proper" in v for v in violations), violations

    def test_root_copilot_instructions_does_not_require_apply_to(self, tmp_path: Path) -> None:
        """copilot-instructions.md is the global root file; applyTo is optional."""
        f = tmp_path / "copilot-instructions.md"
        f.write_text("# Global instructions\nsome review guidance\n")
        violations = budget_check_file(f)
        assert not any("applyTo" in v for v in violations), violations

    def test_actual_instruction_files_pass_budget(self) -> None:
        """All instruction files in the repo must pass the budget check."""
        root = SCRIPTS_CI.parent.parent
        files = list(root.glob(".github/copilot-instructions.md")) + list(
            root.glob(".github/instructions/*.instructions.md")
        )
        assert files, "No instruction files found — check path"
        all_violations = []
        for f in files:
            all_violations.extend(budget_check_file(f))
        assert not all_violations, f"Budget violations in repo files:\n" + "\n".join(all_violations)


class TestApplyToParser:
    """parse_apply_to extracts patterns correctly."""

    def test_parses_comma_separated_patterns(self, tmp_path: Path) -> None:
        f = tmp_path / "multi.instructions.md"
        f.write_text('---\napplyTo: "src/a/**,src/b/**,tests/**"\n---\ncontent\n')
        patterns = parse_apply_to(f)
        assert patterns == ["src/a/**", "src/b/**", "tests/**"]

    def test_returns_empty_for_missing_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "nofm.instructions.md"
        f.write_text("# No frontmatter\ncontent\n")
        assert parse_apply_to(f) == []

    def test_returns_empty_for_missing_apply_to(self, tmp_path: Path) -> None:
        f = tmp_path / "noapply.instructions.md"
        f.write_text("---\nname: test\n---\ncontent\n")
        assert parse_apply_to(f) == []
