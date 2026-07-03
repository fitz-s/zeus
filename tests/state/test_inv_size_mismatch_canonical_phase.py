# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-position-model-refactor.md (Finding 2, PR A scaffold; flipped by PR C)
"""Antibody invariants: chain↔local size mismatch routes through canonical review.

Finding 2 (P1 likely bug): when canonical size correction is unavailable,
chain_reconciliation.py:1003 writes `pos.state = "quarantine_size_mismatch"`
— a string outside `LifecycleState`. The intended target is a canonical
review event mapping to `LifecyclePhase.QUARANTINED` (or
`REVIEW_REQUIRED`) with the mismatch reason in the event payload.

This invariant scans chain_reconciliation.py's source: any literal string
written to `Position.state` in a size-mismatch context must be a member of
`LifecycleState`. Strict-xfail until PR C lands.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.contracts.semantic_types import LifecycleState

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAIN_RECON_PATH = REPO_ROOT / "src" / "state" / "chain_reconciliation.py"

LEGAL_LIFECYCLE_VALUES = {member.value for member in LifecycleState}


def test_size_mismatch_state_writes_use_legal_lifecycle_values() -> None:
    source = CHAIN_RECON_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == "state":
                if value.value not in LEGAL_LIFECYCLE_VALUES:
                    violations.append(
                        f"chain_reconciliation.py:{node.lineno}: pos.state = {value.value!r}"
                    )

    assert not violations, (
        "Illegal Position.state writes in chain_reconciliation.py. "
        "Legal values: " + ", ".join(sorted(LEGAL_LIFECYCLE_VALUES)) + "\n"
        "Violations:\n  " + "\n  ".join(violations)
    )
