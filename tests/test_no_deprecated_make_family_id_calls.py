# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: INV-22 make_family_id canonicality; T7.b of midstream
# remediation packet (docs/operations/task_2026-04-23_midstream_remediation/plan.md)

"""AST-walk guard — no non-deprecated `make_family_id` calls in `src/` or `scripts/`.

INV-22 (`architecture/invariants.yaml`) requires `make_family_id()` to
resolve to one canonical family grammar across every call site. The
original `make_family_id()` at `src/strategy/selection_family.py:92` is
deprecated — new code must use `make_hypothesis_family_id()` or
`make_edge_family_id()` from the same module.

This test AST-walks every `.py` file under `src/` and `scripts/` and
fails if any `Call` node targets `make_family_id`, whether via direct
name (`make_family_id(...)`) or attribute (`obj.make_family_id(...)`).

The deprecated definition file itself (`src/strategy/selection_family.py`)
is excluded from the scan even though it contains no `make_family_id`
Call nodes (the `def make_family_id(...)` is a `FunctionDef` node, and
the body calls only `make_hypothesis_family_id` / `make_edge_family_id`).
The exclusion is defensive against future edits that might add a
self-referential call inside the deprecated wrapper.
"""

import ast
from pathlib import Path

ZEUS_ROOT = Path(__file__).parent.parent
ALLOWED_DEFINITION_FILE = ZEUS_ROOT / "src" / "strategy" / "selection_family.py"


def _walk_python_files(*roots: Path):
    for root in roots:
        if not root.exists():
            continue
        yield from root.rglob("*.py")


class _MakeFamilyIdCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "make_family_id":
            self.violations.append((node.lineno, "direct name call"))
        elif isinstance(func, ast.Attribute) and func.attr == "make_family_id":
            self.violations.append((node.lineno, "attribute call"))
        self.generic_visit(node)


def _scan_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    visitor = _MakeFamilyIdCallVisitor()
    visitor.visit(tree)
    return [f"{path.relative_to(ZEUS_ROOT)}:{lineno}: {kind}" for lineno, kind in visitor.violations]


def test_no_deprecated_make_family_id_calls_in_src_or_scripts() -> None:
    """INV-22: no Call to the deprecated `make_family_id()` remains in src/ or scripts/.

    New code must use `make_hypothesis_family_id()` or
    `make_edge_family_id()`. The deprecated shim at
    `src/strategy/selection_family.py:92` exists only for backward-compat
    and emits `DeprecationWarning` on every call.
    """
    src = ZEUS_ROOT / "src"
    scripts = ZEUS_ROOT / "scripts"
    all_violations: list[str] = []
    for py_file in _walk_python_files(src, scripts):
        if py_file.resolve() == ALLOWED_DEFINITION_FILE.resolve():
            continue
        all_violations.extend(_scan_file(py_file))

    assert not all_violations, (
        "Deprecated `make_family_id()` calls found (INV-22 canonicality "
        "violation). Use `make_hypothesis_family_id()` for per-candidate "
        "scope or `make_edge_family_id()` for per-strategy scope. "
        "Violations:\n  " + "\n  ".join(all_violations)
    )
