# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: live-unblock PR37; evaluator rejection stages must be closed-enum serializable
"""Contract tests for evaluator rejection-stage literals."""
from __future__ import annotations

import ast
from pathlib import Path

from src.contracts.semantic_types import RejectionStage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_evaluator_rejection_stage_literals_are_closed_enum_members():
    tree = ast.parse((PROJECT_ROOT / "src/engine/evaluator.py").read_text())
    literals: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_keyword(self, node: ast.keyword) -> None:
            if (
                node.arg == "rejection_stage"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                literals.add(node.value.value)
            self.generic_visit(node)

    Visitor().visit(tree)

    enum_values = {stage.value for stage in RejectionStage}
    assert literals <= enum_values
