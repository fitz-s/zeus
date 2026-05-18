# Created: 2026-05-18
# Last reused/audited: 2026-05-18
# Authority basis: U7_SIGTERM_ORDERING.md MINOR_HARDEN verdict
"""Antibody test for U7: scheduler.shutdown(wait=True) in main.py SIGTERM path.

Sed-break verifiable:
  Revert src/main.py:1901 from scheduler.shutdown(wait=True) → scheduler.shutdown()
  and this test fails with AssertionError.
"""

import ast
import pathlib


def _parse_main_py() -> ast.Module:
    src = pathlib.Path(__file__).parent.parent / "src" / "main.py"
    return ast.parse(src.read_text())


def _find_scheduler_shutdown_calls(tree: ast.Module) -> list[ast.Call]:
    """Find all scheduler.shutdown() call nodes in main.py."""
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "shutdown"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "scheduler"
        ):
            calls.append(node)
    return calls


def test_main_scheduler_shutdown_uses_wait_true():
    """U7 MINOR_HARDEN: scheduler.shutdown in main() must use wait=True.

    Verifies the except (KeyboardInterrupt, SystemExit) shutdown path passes
    wait=True so inflight cycles commit before SIGTERM exits the process.

    Sed-break: change wait=True → wait=False (or remove kwarg) → this test fails.
    """
    tree = _parse_main_py()
    calls = _find_scheduler_shutdown_calls(tree)

    assert calls, "No scheduler.shutdown() calls found in src/main.py — check file path or symbol"

    # Find any call that has wait=True
    wait_true_calls = []
    for call in calls:
        for kw in call.keywords:
            if kw.arg == "wait" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                wait_true_calls.append(call)

    assert wait_true_calls, (
        "src/main.py scheduler.shutdown() must use wait=True "
        f"(found {len(calls)} call(s) but none with wait=True). "
        "U7 MINOR_HARDEN requires inflight cycles to commit before SIGTERM exit."
    )
