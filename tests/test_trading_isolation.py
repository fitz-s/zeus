# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §6 antibody #3
"""Antibody #3 — Trading-lane isolation (reverse direction).

Enforces that src.engine, src.execution, src.strategy, src.signal do NOT
import from scripts.ingest.*, scripts.refit_*, or scripts.rebuild_*.

Mirrors test_ingest_isolation.py but in the reverse direction:
scripts/ingest/* must NOT bleed into trading; trading must NOT bleed
back into scripts/ingest/*, refit_*, rebuild_*.

AST-walk (not regex) to avoid false negatives on `from scripts.ingest.x import y`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# Trading modules whose import closure must not reference ingest scripts.
TRADING_SCAN_PREFIXES: tuple[str, ...] = (
    "src/engine",
    "src/strategy",
    "src/signal",
    "src/execution",
)

# Forbidden import prefixes for the trading lane.
FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "scripts.ingest",
    "scripts.refit_",
    "scripts.rebuild_",
)


def _collect_imports(py_path: Path) -> list[str]:
    """Return dotted module names imported by a Python file (AST-walked)."""
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


def _trading_python_files() -> list[Path]:
    """All .py files under src/engine, src/strategy, src/signal, src/execution."""
    files: list[Path] = []
    for prefix in TRADING_SCAN_PREFIXES:
        scan_dir = PROJECT_ROOT / prefix
        if scan_dir.exists():
            files.extend(
                p for p in scan_dir.rglob("*.py")
                if "__pycache__" not in p.parts
            )
    return sorted(files)


def _is_forbidden(module: str) -> bool:
    for prefix in FORBIDDEN_IMPORT_PREFIXES:
        if prefix.endswith("_"):
            # Prefix match for patterns like "scripts.refit_"
            if module.startswith(prefix):
                return True
        else:
            if module == prefix or module.startswith(prefix + "."):
                return True
    return False


def test_trading_dirs_exist():
    """At least one trading directory exists — sanity check."""
    existing = [
        d for prefix in TRADING_SCAN_PREFIXES
        if (d := PROJECT_ROOT / prefix).exists()
    ]
    assert existing, (
        f"None of the expected trading directories exist under {SRC_DIR}. "
        f"Checked: {TRADING_SCAN_PREFIXES}"
    )


def test_no_forbidden_ingest_imports_in_trading():
    """AST-walk every trading module — no import may reference scripts.ingest/refit/rebuild.

    This is antibody #3 (reverse direction of test_ingest_isolation.py antibody #1).
    Trading modules (src.engine, src.strategy, src.signal, src.execution) must not
    reach into scripts.ingest.*, scripts.refit_*, or scripts.rebuild_* because:
    - Those are ingest-lane artifacts owned by the ingest daemon.
    - Importing them would couple trading's restart lifecycle to ingest.
    - Cross-lane coupling defeats the purpose of the two-system split.

    If a future trading module needs a helper from ingest scripts:
    1. Move the helper to src.data.* (shared data layer), OR
    2. Move it to src.contracts.* (cross-lane contract), OR
    3. File a separate slice + operator decision to widen the contract.
    """
    violations: list[str] = []
    py_files = _trading_python_files()

    for py_path in py_files:
        imports = _collect_imports(py_path)
        for module in imports:
            if _is_forbidden(module):
                rel = py_path.relative_to(PROJECT_ROOT)
                violations.append(f"{rel}: imports {module!r} (forbidden ingest-side module)")

    assert not violations, (
        "Trading-isolation contract violated — src.engine|execution|strategy|signal "
        "may NOT import from scripts.ingest.* or scripts.refit_* or scripts.rebuild_*. "
        "These are ingest-lane artifacts. Offenders:\n"
        + "\n".join("  - " + v for v in violations)
    )


def test_antibody_self_test_catches_synthetic_violation(tmp_path):
    """Build a synthetic violating trading file and verify the antibody fires."""
    fake_module = tmp_path / "fake_trading.py"
    fake_module.write_text(
        "from scripts.ingest.hourly_instants_tick import main\n"
        "import scripts.refit_platt_v2\n",
        encoding="utf-8",
    )
    imports = _collect_imports(fake_module)
    found_violations = [m for m in imports if _is_forbidden(m)]
    assert "scripts.ingest.hourly_instants_tick" in found_violations, (
        "Antibody failed to detect scripts.ingest.* import"
    )
    assert "scripts.refit_platt_v2" in found_violations, (
        "Antibody failed to detect scripts.refit_* import"
    )
