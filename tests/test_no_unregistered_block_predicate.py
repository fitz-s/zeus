# Created: 2026-05-04
# Last reused/audited: 2026-06-28
"""Anti-drift tests for entry blocker observability.

The block registry is an operator snapshot. It must remain visible in the
cycle summary, but it must not become an implicit runtime gate again. Runtime
entry authority belongs to the explicit ``_discovery_gates_allow_entries``
argument list.
"""

from __future__ import annotations

import ast
from pathlib import Path


_ZEUS_ROOT = Path(__file__).resolve().parent.parent
_CYCLE_RUNNER = _ZEUS_ROOT / "src" / "engine" / "cycle_runner.py"


def _load_source() -> str:
    assert _CYCLE_RUNNER.exists(), f"cycle_runner.py not found at {_CYCLE_RUNNER}"
    return _CYCLE_RUNNER.read_text(encoding="utf-8")


def _find_discovery_gate_def(source: str) -> ast.FunctionDef:
    tree = ast.parse(source, filename=str(_CYCLE_RUNNER))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_discovery_gates_allow_entries":
            return node
    raise AssertionError("_discovery_gates_allow_entries() not found")


def _find_discovery_gate_call(source: str) -> ast.Call:
    tree = ast.parse(source, filename=str(_CYCLE_RUNNER))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_discovery_gates_allow_entries":
                calls.append(node)
    assert calls, "_discovery_gates_allow_entries() call not found"
    calls.sort(key=lambda node: node.lineno)
    return calls[0]


def test_registry_imported_in_cycle_runner() -> None:
    """Registry import must appear in cycle_runner.py."""
    source = _load_source()
    assert "from src.control.entries_block_registry import" in source, (
        "Expected registry import in cycle_runner.py so blocker observability "
        "cannot disappear silently."
    )


def test_block_registry_summary_field_present() -> None:
    """cycle_runner.py must write ``summary['block_registry']``."""
    source = _load_source()
    assert 'summary["block_registry"] = ' in source, (
        "Expected block_registry emission in the cycle JSON summary."
    )


def test_block_registry_not_a_discovery_gate_argument() -> None:
    """Registry snapshots must not feed the runtime entry gate."""
    source = _load_source()
    gate_def = _find_discovery_gate_def(source)
    gate_call = _find_discovery_gate_call(source)

    def_arg_names = [arg.arg for arg in gate_def.args.args]
    call_kwarg_names = [kw.arg for kw in gate_call.keywords if kw.arg]

    assert "block_registry" not in def_arg_names
    assert "block_registry" not in call_kwarg_names
