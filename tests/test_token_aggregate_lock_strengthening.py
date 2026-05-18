# Created: 2026-05-18
# Last reused/audited: 2026-05-18
# Authority basis: STRUCTURAL_PLAN.md v3 §2 PR-S1 MAJOR-1 critic finding
"""Strengthened threading.Lock antibody for token aggregate block-list (FOLLOW-1).

MAJOR-1 critic finding: the original test_block_list_concurrent_mutation_does_not_raise
(test_allocate_chain_truth.py:325) acquires _tokens_blocked_lock in the TEST itself —
so removing `with _tokens_blocked_lock:` from cycle_runtime.py leaves it passing.
That makes it a fake antibody.

This test uses AST inspection to verify that every mutation and snapshot-read of
tokens_blocked_until_resolution inside _assert_token_aggregate_invariant is
wrapped in `with _tokens_blocked_lock:`. This is directly sed-break verifiable:

  Sed-break: remove any `with _tokens_blocked_lock:` guard in
  cycle_runtime._assert_token_aggregate_invariant (leave body, remove context
  manager line) → assertion count drops → this test fails immediately.

  Restore → PASS.
"""

import ast
import pathlib


_CYCLE_RUNTIME = pathlib.Path(__file__).parent.parent / "src" / "engine" / "cycle_runtime.py"
_FUNCTION_NAME = "_assert_token_aggregate_invariant"
_LOCK_NAME = "_tokens_blocked_lock"
_SET_NAME = "tokens_blocked_until_resolution"


def _parse_cycle_runtime() -> ast.Module:
    return ast.parse(_CYCLE_RUNTIME.read_text())


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function {name!r} not found in {_CYCLE_RUNTIME}")


def _with_lock_bodies(func_node: ast.FunctionDef) -> list[ast.With]:
    """Return all `with _tokens_blocked_lock:` statements in the function."""
    result = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            ctx = item.context_expr
            if isinstance(ctx, ast.Name) and ctx.id == _LOCK_NAME:
                result.append(node)
                break
    return result


def _set_accesses_in_node(node: ast.AST) -> list[str]:
    """Return list of access kinds ('read'/'write') for tokens_blocked_until_resolution."""
    accesses = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == _SET_NAME:
            accesses.append("access")
    return accesses


def test_assert_token_aggregate_invariant_lock_guards_all_set_accesses():
    """FOLLOW-1 MAJOR-1: every access to tokens_blocked_until_resolution inside
    _assert_token_aggregate_invariant must be inside `with _tokens_blocked_lock:`.

    Verifies:
    1. At least 4 `with _tokens_blocked_lock:` blocks exist in the function
       (snapshot read, add, discard, gauge read).
    2. Every reference to tokens_blocked_until_resolution in the function body
       is inside one of those lock blocks (none are bare/unguarded).

    Sed-break: remove any `with _tokens_blocked_lock:` guard in cycle_runtime.py
    _assert_token_aggregate_invariant → guard count < 4 → this test fails.
    Restore → PASS.
    """
    tree = _parse_cycle_runtime()
    func = _find_function(tree, _FUNCTION_NAME)
    lock_withs = _with_lock_bodies(func)

    assert len(lock_withs) >= 4, (
        f"Expected ≥4 `with {_LOCK_NAME}:` blocks in {_FUNCTION_NAME}, "
        f"found {len(lock_withs)}. "
        "MAJOR-1: snapshot read, add, discard, and gauge read must each be lock-guarded."
    )

    # Collect all set accesses WITHIN lock blocks
    guarded_set_linenos: set[int] = set()
    for with_node in lock_withs:
        for child in ast.walk(with_node):
            if isinstance(child, ast.Name) and child.id == _SET_NAME:
                guarded_set_linenos.add(child.col_offset)  # identity by position

    # Collect ALL set accesses in the function (including unguarded)
    all_set_accesses: list[tuple[int, int]] = []
    for child in ast.walk(func):
        if isinstance(child, ast.Name) and child.id == _SET_NAME:
            all_set_accesses.append((child.lineno, child.col_offset))

    assert all_set_accesses, (
        f"No references to {_SET_NAME!r} found in {_FUNCTION_NAME} — "
        "check symbol name or function scope."
    )

    # Collect set access positions INSIDE lock blocks for comparison
    guarded_positions: set[tuple[int, int]] = set()
    for with_node in lock_withs:
        for child in ast.walk(with_node):
            if isinstance(child, ast.Name) and child.id == _SET_NAME:
                guarded_positions.add((child.lineno, child.col_offset))

    unguarded = [pos for pos in all_set_accesses if pos not in guarded_positions]

    assert not unguarded, (
        f"Unguarded accesses to {_SET_NAME!r} found in {_FUNCTION_NAME} "
        f"at line:col positions {unguarded}. "
        "All mutations and reads of the block-list must be inside "
        f"`with {_LOCK_NAME}:` to prevent concurrent RuntimeError."
    )
