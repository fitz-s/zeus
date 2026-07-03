# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-position-model-refactor.md (Finding 2, PR A scaffold; flipped by PR C)
"""Antibody invariants: every literal assigned to `Position.state` is a legal LifecycleState value.

Finding 2 (P1 likely bug): src/state/chain_reconciliation.py:1003 writes
`pos.state = "quarantine_size_mismatch"`, a string outside the
`LifecycleState` enum. `phase_for_runtime_position()` then maps it to
`UNKNOWN`, and downstream open-exposure filters may not recognize it as
inactive — exposure/exit/harvester see inconsistent phases.

Existing test `test_fill_tracker_does_not_emit_legacy_nonvocabulary_quarantine_states`
in tests/test_live_safety_invariants.py covers only `src/execution/fill_tracker.py`
and only two specific banned strings. This invariant is broader: every
string literal assigned to `Position.state` anywhere under `src/state/` or
`src/execution/` must be a member value of `LifecycleState`.

This test is STRICT-XFAIL until PR C replaces ad-hoc state strings with
canonical review events.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.contracts.semantic_types import LifecycleState

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_DIRS = (
    REPO_ROOT / "src" / "state",
    REPO_ROOT / "src" / "execution",
)

LEGAL_VALUES = {member.value for member in LifecycleState}


def _walk_python_sources(root: Path):
    for path in root.rglob("*.py"):
        if path.is_file():
            yield path


def _collect_state_string_assignments(source: str, path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, value), ...] for every `<expr>.state = "<literal>"`
    assignment AND every `state="<literal>"` keyword argument to known
    Position-mutating callables (e.g. Position(...)).
    """
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # Pattern 1: `pos.state = "..."`
        if isinstance(node, ast.Assign):
            value = node.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "state":
                    hits.append((node.lineno, value.value))
        # Pattern 2: `Position(state="...")` keyword argument
        elif isinstance(node, ast.Call):
            func = node.func
            is_position_call = (
                (isinstance(func, ast.Name) and func.id == "Position")
                or (isinstance(func, ast.Attribute) and func.attr == "Position")
            )
            if not is_position_call:
                continue
            for kw in node.keywords:
                if kw.arg != "state":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    hits.append((node.lineno, kw.value.value))

    return hits


def test_every_position_state_literal_is_legal_lifecycle_state() -> None:
    violations: list[str] = []
    for scan_dir in SCAN_DIRS:
        for path in _walk_python_sources(scan_dir):
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for lineno, literal in _collect_state_string_assignments(source, path):
                if literal not in LEGAL_VALUES:
                    rel = path.relative_to(REPO_ROOT)
                    violations.append(f"{rel}:{lineno}: pos.state = {literal!r}")

    assert not violations, (
        "Illegal Position.state values written outside LifecycleState enum. "
        "Legal values: " + ", ".join(sorted(LEGAL_VALUES)) + "\n"
        "Violations:\n  " + "\n  ".join(violations)
    )
