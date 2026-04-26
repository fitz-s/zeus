# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: G10-scaffold antibody — enforce that scripts/ingest/* modules do
#          not import from src.engine, src.execution, src.strategy, src.signal,
#          src.supervisor_api, src.control, src.observability, or src.main.
#          AST-walk (not regex) to avoid false negatives on `from src.X import y`
#          masquerading as `import src.X.y`.
# Reuse: Add a new tick under scripts/ingest/? This test will validate it on
#        the next pytest run. To extend the forbidden list, edit
#        FORBIDDEN_IMPORT_PREFIXES below + log the operator decision in the
#        slice receipt.
# Authority basis: docs/operations/task_2026-04-26_g10_ingest_scaffold/plan.md
#   §2 forbidden import set + parent
#   docs/operations/task_2026-04-26_live_readiness_completion/plan.md K3.G10.
"""G10-scaffold isolation antibody.

Enforces that the ingest lane (`scripts/ingest/`) does NOT depend on the
trading engine, execution, strategy, signal, supervisor, control,
observability, or main-module surfaces. The point of the decoupling is
that an ingest tick can be deployed / restarted / debugged without
touching the live-trading daemon.

If a future tick script needs (say) `src.signal.diurnal._is_missing_local_hour`,
the right move is either:
1. Inline the small helper into `src.data.*` (where ingest may legitimately
   depend), OR
2. Promote it to `src.contracts.*` (allowed for both lanes), OR
3. File a separate slice + operator decision to widen the contract.

NOT acceptable: silently `from src.signal.diurnal import _is_missing_local_hour`
in a tick script. This test fires on that.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

INGEST_DIR = PROJECT_ROOT / "scripts" / "ingest"

# Forbidden module-name prefixes per workbook G10 acceptance criterion.
# Imports of these (or any submodule) from scripts/ingest/* are violations.
FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "src.engine",
    "src.execution",
    "src.strategy",
    "src.signal",
    "src.supervisor_api",
    "src.control",
    "src.observability",
    "src.main",
)


# ---------------------------------------------------------------------------
# Directory + package shape (1-2)
# ---------------------------------------------------------------------------


def test_scripts_ingest_directory_exists():
    """scripts/ingest/ directory exists — sanity check for the decoupling lane."""
    assert INGEST_DIR.is_dir(), (
        f"scripts/ingest/ must exist as a package directory. "
        f"Looked at: {INGEST_DIR}"
    )


def test_scripts_ingest_has_init_module():
    """__init__.py present so scripts.ingest is importable as a package."""
    init = INGEST_DIR / "__init__.py"
    assert init.is_file(), f"scripts/ingest/__init__.py missing — required for package import"


# ---------------------------------------------------------------------------
# Forbidden-import walk (3) — the load-bearing antibody
# ---------------------------------------------------------------------------


def _collect_imports(py_path: Path) -> list[str]:
    """Return the dotted module names imported by a Python file (AST-walked)."""
    src = py_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(py_path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.append(node.module)
    return names


def _ingest_python_files() -> list[Path]:
    """All .py files under scripts/ingest/ excluding __pycache__."""
    return sorted(
        p for p in INGEST_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def test_no_forbidden_imports_in_ingest():
    """AST-walk every scripts/ingest/*.py — no import may match a forbidden prefix.

    This is the slice's load-bearing antibody. If a future tick script
    accidentally `from src.signal.diurnal import _x` or
    `import src.engine.cycle_runner`, this test fires with the file +
    offending module name.
    """
    violations: list[str] = []
    py_files = _ingest_python_files()
    assert py_files, "scripts/ingest/ contains no .py files — antibody has nothing to verify"

    for py_path in py_files:
        imports = _collect_imports(py_path)
        for module in imports:
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                # `module` matches `prefix` if it equals it or starts with `prefix.`
                if module == prefix or module.startswith(prefix + "."):
                    rel = py_path.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel}: imports {module!r} (forbidden prefix {prefix!r})")
                    break

    assert not violations, (
        "G10 isolation contract violated — scripts/ingest/* may NOT import "
        f"from {sorted(FORBIDDEN_IMPORT_PREFIXES)}. Offenders:\n"
        + "\n".join("  - " + v for v in violations)
    )


# ---------------------------------------------------------------------------
# Tick-script convention (4-5)
# ---------------------------------------------------------------------------


def test_each_tick_script_has_main_callable():
    """Every *_tick.py defines a top-level main() function + __main__ block."""
    tick_files = [p for p in _ingest_python_files() if p.name.endswith("_tick.py")]
    assert tick_files, "No *_tick.py scripts found under scripts/ingest/"

    missing_main: list[str] = []
    missing_dunder: list[str] = []

    for py_path in tick_files:
        src = py_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(py_path))

        has_main = any(
            isinstance(node, ast.FunctionDef) and node.name == "main"
            for node in tree.body
        )
        if not has_main:
            missing_main.append(str(py_path.relative_to(PROJECT_ROOT)))

        if '__name__ == "__main__"' not in src and "__name__ == '__main__'" not in src:
            missing_dunder.append(str(py_path.relative_to(PROJECT_ROOT)))

    assert not missing_main, (
        f"Tick scripts missing top-level main(): {missing_main}"
    )
    assert not missing_dunder, (
        f"Tick scripts missing `if __name__ == '__main__':` block: {missing_dunder}"
    )


def test_each_tick_script_carries_lifecycle_header():
    """Every script in scripts/ingest/ carries the standard Lifecycle/Purpose/Reuse header.

    Per Zeus convention (Code Provenance §file-header rule). Header drift
    means an inherited script slips back into "legacy until audited" status.
    """
    py_files = _ingest_python_files()
    missing: list[tuple[str, str]] = []

    required_markers = ("# Lifecycle:", "# Purpose:", "# Reuse:", "# Authority basis:")

    for py_path in py_files:
        src = py_path.read_text(encoding="utf-8")
        # Read just the first 30 lines — header should be at top.
        head = "\n".join(src.splitlines()[:30])
        for marker in required_markers:
            if marker not in head:
                missing.append((str(py_path.relative_to(PROJECT_ROOT)), marker))

    assert not missing, (
        "Tick scripts missing required lifecycle headers:\n"
        + "\n".join(f"  - {p}: missing {m!r}" for p, m in missing)
    )
