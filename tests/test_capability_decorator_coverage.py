# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §3 lines 266-269; IMPLEMENTATION_PLAN Phase 2 D-2

"""CI lint: every .py hard_kernel_path in capabilities.yaml must carry at
least one function decorated with @capability(cap_id, ...) matching the
capability's id.

Phase 2 rollout: all capabilities that have .py hard_kernel_paths are now
decorated. Capabilities whose only paths are non-py (docs/AGENTS.md,
architecture/*.yaml, *.db) are skipped — AST coverage is not applicable.

Phase 4 pre-registered paths (src/execution/venue_adapter.py,
src/execution/live_executor.py) that do not yet exist are explicitly
skipped via pytest.skip — not vacuous-pass, not error.

Per ULTIMATE_DESIGN §3 lines 266-269 + IMPLEMENTATION_PLAN Phase 2 D-2.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
CAPABILITIES_YAML = REPO_ROOT / "architecture" / "capabilities.yaml"

# ---------------------------------------------------------------------------
# Phase 4 pre-registered paths: exist in capabilities.yaml but files not yet
# created (LiveAuthToken phantom + ABC split lands in Phase 4). C-6 handling.
# ---------------------------------------------------------------------------
PHASE_4_DEFERRED_PATHS: set[str] = {
    "src/execution/venue_adapter.py",
    "src/execution/live_executor.py",
}

# Capabilities whose ALL hard_kernel_paths are non-py (docs, YAML, DB).
# These cannot be AST-walked; they are explicitly exempt from decorator check.
NON_PY_ONLY_CAPS: set[str] = {
    "authority_doc_rewrite",
    "archive_promotion",
}


def _load_capabilities() -> list[dict]:
    with CAPABILITIES_YAML.open() as f:
        return yaml.safe_load(f)["capabilities"]


def _py_kernel_paths(cap: dict) -> list[str]:
    """Return .py hard_kernel_paths only (exclude .db, .yaml, .md, etc.)."""
    return [
        p for p in cap.get("hard_kernel_paths", [])
        if p.endswith(".py")
    ]


def _ast_has_capability_decorator(py_path: pathlib.Path, cap_id: str) -> bool:
    """Return True if any function/method in py_path carries @capability(cap_id, ...)."""
    try:
        source = py_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    tree = ast.parse(source, filename=str(py_path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Name) and func.id == "capability":
                    args = dec.args
                    if args and isinstance(args[0], ast.Constant) and args[0].value == cap_id:
                        return True
    return False


# Build test parametrize: one case per capability (not per path; one writer per cap)
_ALL_CAPS = _load_capabilities()
_PARAMS: list[tuple[str, list[str]]] = [
    (cap["id"], _py_kernel_paths(cap)) for cap in _ALL_CAPS
]


@pytest.mark.parametrize("cap_id,py_paths", _PARAMS, ids=[p[0] for p in _PARAMS])
def test_capability_decorator_present(cap_id: str, py_paths: list[str]) -> None:
    """Assert that at least one .py hard_kernel_path for cap_id carries @capability(cap_id)."""
    # Non-py-only capabilities: authority_doc_rewrite, archive_promotion — exempt.
    if cap_id in NON_PY_ONLY_CAPS:
        pytest.skip(
            reason=f"{cap_id}: all hard_kernel_paths are non-.py (docs/YAML/DB); "
                   "AST decorator coverage not applicable"
        )

    if not py_paths:
        pytest.skip(reason=f"{cap_id}: no .py hard_kernel_paths found in capabilities.yaml")

    # Check each py path; pass if ANY carries the decorator
    found_in: list[str] = []
    deferred: list[str] = []
    missing: list[str] = []

    for rel_path in py_paths:
        abs_path = REPO_ROOT / rel_path
        if rel_path in PHASE_4_DEFERRED_PATHS and not abs_path.exists():
            deferred.append(rel_path)
            continue
        if not abs_path.exists():
            missing.append(rel_path)
            continue
        if _ast_has_capability_decorator(abs_path, cap_id):
            found_in.append(rel_path)

    # All paths are phase-4-deferred and don't exist yet
    if deferred and not found_in and not missing:
        pytest.skip(
            reason=f"{cap_id}: all .py hard_kernel_paths are Phase 4 deliverables "
                   f"({deferred}) — skipping until Phase 4"
        )

    if missing:
        pytest.fail(
            f"hard_kernel_path(s) {missing} for capability {cap_id!r} do not exist. "
            f"If Phase 4 deliverables, add to PHASE_4_DEFERRED_PATHS."
        )

    assert found_in, (
        f"No @capability({cap_id!r}, ...) found in any of {py_paths}. "
        f"Apply the decorator to the canonical writer function for {cap_id!r}."
    )
