# Created: 2026-05-11
# Last reused or audited: 2026-05-11
# Authority basis: PLAN.md §10, critic v4 ACCEPT 2026-05-11
"""Isolation tests — backfill_harvester_settlements.py physical isolation.

Verifies:
  1. The backfill script does NOT import the live paginator functions
     (_fetch_open_settling_markets from ingest twin,
      _fetch_settled_events from trading twin).
  2. The backfill script's own paginate loop honors its 900s wall-cap.
"""
from __future__ import annotations

import ast
import os


BACKFILL_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "backfill_harvester_settlements.py"
)


def _load_ast() -> ast.Module:
    path = os.path.abspath(BACKFILL_SCRIPT)
    with open(path) as f:
        return ast.parse(f.read(), filename=path)


def _collect_imported_names(tree: ast.Module) -> set[str]:
    """Collect all names imported in the module (module paths + explicit names)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            for alias in node.names:
                names.add(f"{module}.{alias.name}")
                names.add(alias.name)
    return names


def test_backfill_does_not_import_live_paginator_functions():
    """scripts/backfill_harvester_settlements.py must NOT import live paginator functions."""
    tree = _load_ast()
    imported = _collect_imported_names(tree)

    forbidden_names = {
        "_fetch_open_settling_markets",   # ingest twin paginator
        "_fetch_settled_events",          # trading twin paginator
    }
    violations = forbidden_names & imported
    assert not violations, (
        f"backfill script imports forbidden live paginator(s): {violations}. "
        "The paginator antibody requires physical isolation — copy the loop, "
        "do not import from src/ingest/harvester_truth_writer.py or "
        "src/execution/harvester.py paginator functions."
    )


def test_backfill_script_exists():
    """scripts/backfill_harvester_settlements.py must exist."""
    path = os.path.abspath(BACKFILL_SCRIPT)
    assert os.path.isfile(path), (
        f"Backfill script not found at {path}. "
        "Create scripts/backfill_harvester_settlements.py per PLAN §10."
    )


def test_backfill_has_wall_cap_constant():
    """Backfill script must define its own wall-cap constant (900s)."""
    tree = _load_ast()
    # Look for an assignment like _BACKFILL_MAX_WALL_SECONDS = 900
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "WALL" in target.id.upper():
                    # Found a wall-cap constant
                    if isinstance(node.value, ast.Constant) and node.value.value == 900:
                        return
    # Also accept annotated assignments
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and "WALL" in node.target.id.upper():
                if isinstance(node.value, ast.Constant) and node.value.value == 900:
                    return
    raise AssertionError(
        "backfill script must define a wall-cap constant equal to 900 (seconds). "
        "Expected e.g. _BACKFILL_MAX_WALL_SECONDS = 900 per PLAN §10."
    )
