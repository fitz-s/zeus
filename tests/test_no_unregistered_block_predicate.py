# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""CI anti-drift gate: test_no_unregistered_block_predicate.

Stage-1 antibody.  Reads cycle_runner.py source, finds the
# REGISTRY-GUARDED SHORT-CIRCUIT marker, walks forward to the first `if`
statement after that marker, and asserts that every Name/Attribute identifier
in the boolean expression appears in a known allow-list.

If a new boolean is added to the discovery short-circuit without a matching
adapter registration, this test fails with an actionable message.

Also asserts:
- `from src.control.entries_block_registry import` appears in cycle_runner.py
  (registry can never be silently removed).
- `summary["block_registry"] = ` appears in cycle_runner.py
  (observational integration can never be silently removed).
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ZEUS_ROOT = Path(__file__).resolve().parent.parent
_CYCLE_RUNNER = _ZEUS_ROOT / "src" / "engine" / "cycle_runner.py"

_MARKER = "REGISTRY-GUARDED SHORT-CIRCUIT"

# Allow-list of identifiers permitted in the boolean expression at the
# discovery short-circuit.  Add here when a new adapter is registered.
#
# The identifiers present NOW (2026-05-04 integration):
#   _risk_allows_new_entries  -- function call
#   risk_level                -- argument
#   entries_paused            -- bool flag
#   entries_blocked_reason    -- string-or-None
#
# Constant singletons (always allowed):
#   True / False / None
#
_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Current short-circuit identifiers
        "_risk_allows_new_entries",
        "risk_level",
        "entries_paused",
        "entries_blocked_reason",
        # Python builtins / constants that appear in AST as Name nodes
        "True",
        "False",
        "None",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_source() -> str:
    assert _CYCLE_RUNNER.exists(), f"cycle_runner.py not found at {_CYCLE_RUNNER}"
    return _CYCLE_RUNNER.read_text(encoding="utf-8")


def _find_short_circuit_if(source: str) -> ast.If:
    """Find the first `if` statement after the REGISTRY-GUARDED SHORT-CIRCUIT marker.

    Strategy:
    1. Find the line number of the marker in the raw source.
    2. Parse the full module with ast.
    3. Walk the tree for ast.If nodes whose lineno > marker_lineno.
    4. Return the one with the lowest lineno.
    """
    lines = source.splitlines()
    marker_lineno: int | None = None
    for i, line in enumerate(lines, start=1):
        if _MARKER in line:
            marker_lineno = i
            break

    assert marker_lineno is not None, (
        f"Marker '{_MARKER}' not found in {_CYCLE_RUNNER}.  "
        "Was it removed?  The CI gate depends on this marker."
    )

    tree = ast.parse(source, filename=str(_CYCLE_RUNNER))

    candidates: list[ast.If] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and node.lineno > marker_lineno:
            candidates.append(node)

    assert candidates, (
        f"No `if` statement found after line {marker_lineno} "
        f"(marker '{_MARKER}') in {_CYCLE_RUNNER}."
    )

    # The short-circuit `if` is the first one after the marker.
    candidates.sort(key=lambda n: n.lineno)
    return candidates[0]


def _collect_names(expr: ast.expr) -> set[str]:
    """Recursively collect all Name and Attribute identifiers from an AST expression."""
    names: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_registry_imported_in_cycle_runner() -> None:
    """Registry import must appear in cycle_runner.py.

    If this fails, the registry was removed from cycle_runner — that would
    break observational integration and every downstream consumer.
    """
    source = _load_source()
    assert "from src.control.entries_block_registry import" in source, (
        "Expected 'from src.control.entries_block_registry import' in "
        f"{_CYCLE_RUNNER} but it was not found.  The registry integration "
        "must not be removed from cycle_runner.py."
    )


def test_block_registry_summary_field_present() -> None:
    """cycle_runner.py must write `summary['block_registry']`.

    If this fails, the observational emission was silently removed — cycle
    JSON would no longer contain the 13-gate snapshot.
    """
    source = _load_source()
    assert 'summary["block_registry"] = ' in source, (
        "Expected 'summary[\"block_registry\"] = ' in "
        f"{_CYCLE_RUNNER} but it was not found.  The registry snapshot "
        "must be emitted into the cycle JSON summary dict."
    )


def test_no_unregistered_boolean_in_short_circuit() -> None:
    """Every identifier in the discovery short-circuit must be on the allow-list.

    If a new boolean is added to the `if` at the short-circuit without also
    registering an adapter in src/control/block_adapters/, this test fails.

    Error message guides the fixer:
        - Add an adapter under src/control/block_adapters/
        - Update REGISTRY_DESIGN.md
        - Add the new identifier to _ALLOWLIST in this test
    """
    source = _load_source()
    short_circuit_if = _find_short_circuit_if(source)

    # Reconstruct the source of just the boolean expression for the error message
    bool_expr_lines = source.splitlines()[short_circuit_if.lineno - 1 : short_circuit_if.end_lineno]
    bool_expr_text = " ".join(l.strip() for l in bool_expr_lines)[:200]

    names = _collect_names(short_circuit_if.test)
    unregistered = names - _ALLOWLIST

    assert not unregistered, (
        f"New boolean identifier(s) {sorted(unregistered)!r} added to the "
        f"discovery short-circuit at {_CYCLE_RUNNER}:{short_circuit_if.lineno} "
        f"but not registered in EntriesBlockRegistry.\n"
        f"\nExpression: {bool_expr_text}\n"
        f"\nTo fix:\n"
        f"  1. Add an adapter under src/control/block_adapters/ for each new identifier.\n"
        f"  2. Update docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md.\n"
        f"  3. Add the new identifier to _ALLOWLIST in "
        f"     tests/test_no_unregistered_block_predicate.py.\n"
        f"\nSee REGISTRY_DESIGN.md 'CI gate' section for rationale."
    )
