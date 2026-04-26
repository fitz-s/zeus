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
    # G10 calibration-fence (2026-04-26, con-nyx NICE-TO-HAVE #4):
    # src.calibration writes to ensemble_snapshots_v2 + platt_models — surfaces
    # the ingest lane should not reach into. season helpers extracted to
    # src.contracts.season for callers that need calendar mapping only.
    "src.calibration",
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
    """Every executable module under scripts/ingest/ defines main() + __main__ block.

    Per con-nyx NICE-TO-HAVE #3 (G10 suffix broadening): broadened from
    `*_tick.py` to "any non-_shared, non-__init__ module". A future
    `daily_runner.py` or `obs_dispatcher.py` would have skipped the
    `*_tick.py` filter; now it surfaces here.
    """
    tick_files = [
        p for p in _ingest_python_files()
        if p.name not in {"_shared.py", "__init__.py"}
    ]
    assert tick_files, "No executable scripts found under scripts/ingest/"

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


# ---------------------------------------------------------------------------
# Transitive-import audit (6) — con-nyx G10 MAJOR #1 fix
# ---------------------------------------------------------------------------


def _tick_modules() -> list[str]:
    """List of importable scripts.ingest.X_tick module names."""
    return sorted(
        f"scripts.ingest.{p.stem}"
        for p in INGEST_DIR.glob("*_tick.py")
    )


def test_no_forbidden_transitive_imports_in_ingest():
    """Subprocess-isolated audit: each tick's full import closure is forbidden-clean.

    AST-walk antibody (test_no_forbidden_imports_in_ingest) catches DIRECT
    imports only. A tick that does `from src.data.daily_obs_append import X`
    transitively pulls whatever `src.data.daily_obs_append` imports — and if
    THAT chain reaches src.signal / src.engine / etc, the decoupling premise
    breaks silently.

    This test runs each tick's import in a fresh Python subprocess (so no
    sys.modules pollution from pytest's own import graph) and verifies the
    set of newly-loaded src.* modules contains NONE of FORBIDDEN_IMPORT_PREFIXES.

    Pre-G10-helper-extraction (commit pending), this test fired on:
    - daily_obs_tick: pulled src.signal.diurnal via daily_obs_append
    - hourly_instants_tick: same via hourly_instants_append

    G10-helper-extraction moved _is_missing_local_hour to
    src.contracts.dst_semantics (allowed) and updated the *_append imports.
    Now all 5 ticks should pass.
    """
    import subprocess

    forbidden_pickled = repr(list(FORBIDDEN_IMPORT_PREFIXES))
    violators_per_tick: dict[str, list[str]] = {}

    for tick_module in _tick_modules():
        probe = f"""
import sys, json
sys.path.insert(0, {repr(str(PROJECT_ROOT))})
forbidden = {forbidden_pickled}
before = set(sys.modules.keys())
__import__({tick_module!r})
new = sorted(set(sys.modules.keys()) - before)
violators = [
    m for m in new
    if any(m == p or m.startswith(p + '.') for p in forbidden)
]
print(json.dumps(violators))
"""
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Subprocess crashed during import — record as a violation surface,
            # not a green pass.
            violators_per_tick[tick_module] = [
                f"subprocess-import-error: {result.stderr.strip().splitlines()[-1] if result.stderr else 'unknown'}"
            ]
            continue
        try:
            import json as _json
            violators = _json.loads(result.stdout.strip().splitlines()[-1])
        except Exception:
            violators = [f"unparseable-output: {result.stdout!r}"]
        if violators:
            violators_per_tick[tick_module] = violators

    assert not violators_per_tick, (
        "Transitive forbidden-import violation in scripts/ingest/* ticks. "
        "Direct imports are clean (per test_no_forbidden_imports_in_ingest), "
        "but the import closure transitively pulls a forbidden module. "
        "Fix: extract the offending helper to src.contracts.* or src.types.* "
        "(allowed for both lanes). Per-tick violators:\n"
        + "\n".join(
            f"  - {tick}: {viol}"
            for tick, viol in sorted(violators_per_tick.items())
        )
    )


# ---------------------------------------------------------------------------
# sys.path bootstrap audit (7) — con-nyx G10 MAJOR #2 fix
# ---------------------------------------------------------------------------


def test_each_tick_script_self_bootstraps_syspath():
    """Each *_tick.py inserts the repo root into sys.path before project imports.

    Required so `python scripts/ingest/X_tick.py` (direct invocation, the
    canonical_command in script_manifest.yaml) resolves `src.*` and
    `scripts.*`. Without this shim, default sys.path[0] is the script's
    directory and `from src.data.X import Y` fails with ModuleNotFoundError
    even though the project structure is correct.

    Convention matches scripts/live_smoke_test.py:23.
    """
    tick_files = [p for p in _ingest_python_files() if p.name.endswith("_tick.py")]
    assert tick_files, "No *_tick.py scripts found"

    missing: list[str] = []
    for py_path in tick_files:
        src = py_path.read_text(encoding="utf-8")
        # Substring match — must contain a sys.path.insert with parents[2]
        # OR an equivalent bootstrap. Conservative: require the literal phrase.
        if "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))" not in src:
            missing.append(str(py_path.relative_to(PROJECT_ROOT)))

    assert not missing, (
        "Tick scripts missing sys.path bootstrap. Required for direct\n"
        "invocation `python scripts/ingest/X_tick.py`. Add to top of file\n"
        "before any `from src.X` import:\n"
        "  sys.path.insert(0, str(Path(__file__).resolve().parents[2]))\n"
        "Offenders:\n"
        + "\n".join(f"  - {p}" for p in missing)
    )


# ---------------------------------------------------------------------------
# Negative-detection (8) — con-nyx pattern feedback #12
# ---------------------------------------------------------------------------


def _collect_dynamic_imports(py_path: Path) -> list[str]:
    """Return the string-literal first args of __import__() and importlib.import_module() calls.

    Per con-nyx NICE-TO-HAVE #1 (G10 dynamic-imports): the AST-walk antibody
    in `_collect_imports` only handles ast.Import + ast.ImportFrom statement-
    level imports. Dynamic call-expression imports (`__import__("x")` and
    `importlib.import_module("x")`) evade because the module name is a string
    literal in a Call node, not an Import node.

    This helper inspects all Call nodes for those two patterns and returns
    the string-literal first arguments. Non-string first args (e.g.,
    `__import__(some_var)`) are ignored — they're un-auditable statically
    and treated as out-of-scope by the antibody.
    """
    src = py_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(py_path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Pattern 1: __import__("module.name")
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.append(node.args[0].value)
        # Pattern 2: importlib.import_module("module.name")
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
        ):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.append(node.args[0].value)
    return found


def test_no_forbidden_dynamic_imports_in_ingest():
    """Detect __import__() + importlib.import_module() string-arg violations.

    Closes con-nyx NICE-TO-HAVE #1 — AST-walk's `_collect_imports` saw only
    statement-level imports. A future contributor doing
    `__import__("src.engine.cycle_runner")` or
    `importlib.import_module("src.engine.cycle_runner")` would have evaded
    test_no_forbidden_imports_in_ingest. This test scans Call-node patterns
    and applies the same forbidden-prefix check.
    """
    violations: list[str] = []
    for py_path in _ingest_python_files():
        dynamic_imports = _collect_dynamic_imports(py_path)
        for module in dynamic_imports:
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    rel = py_path.relative_to(PROJECT_ROOT)
                    violations.append(
                        f"{rel}: dynamic import of {module!r} (forbidden prefix {prefix!r})"
                    )
                    break

    assert not violations, (
        "Dynamic-import violation in scripts/ingest/* — __import__() or "
        "importlib.import_module() resolves to a forbidden module. Static "
        "AST walk catches statement-level imports; this test catches the "
        "dynamic equivalents. Offenders:\n"
        + "\n".join("  - " + v for v in violations)
    )


def test_antibody_self_test_catches_synthetic_violation(tmp_path):
    """Programmatically craft a violating tick; assert the AST-walk antibody detects it.

    Without this test, N/N green doesn't prove the antibody actually FIRES on
    a real violation — only that current files happen to be clean. This test
    builds a fake tick with `from src.engine.cycle_runner import KNOWN_STRATEGIES`
    in a tmp dir, runs `_collect_imports` on it, and confirms a violation
    is detected.
    """
    fake_tick = tmp_path / "fake_violator_tick.py"
    fake_tick.write_text(
        "# Lifecycle: created=test; last_reviewed=test; last_reused=never\n"
        "# Purpose: synthetic violator for antibody self-test\n"
        "# Reuse: test fixture only\n"
        "# Authority basis: tests/test_ingest_isolation.py\n"
        "from src.engine.cycle_runner import KNOWN_STRATEGIES\n"
        "from src.signal.diurnal import _is_missing_local_hour\n"
        "def main(): return 0\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )

    imports = _collect_imports(fake_tick)
    violations: list[str] = []
    for module in imports:
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            if module == prefix or module.startswith(prefix + "."):
                violations.append((module, prefix))
                break

    assert ("src.engine.cycle_runner", "src.engine") in violations, (
        f"Antibody failed to detect synthetic violation of src.engine.* — "
        f"either _collect_imports is broken or FORBIDDEN_IMPORT_PREFIXES "
        f"dropped src.engine. Detected: {violations}"
    )
    assert ("src.signal.diurnal", "src.signal") in violations, (
        f"Antibody failed to detect synthetic violation of src.signal.* — "
        f"either _collect_imports is broken or FORBIDDEN_IMPORT_PREFIXES "
        f"dropped src.signal. Detected: {violations}"
    )
