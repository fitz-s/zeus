# Created: 2026-04-30
# Last reused/audited: 2026-04-30
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §6 antibody #8
"""Antibody #8: Phase 3 module-scope enforcement for src.main.

AST-scan of src/main.py imports. Asserts that src.main does NOT import
any K2 ingest module, source-health / calibration-producer module, or the
legacy get_trade_connection_with_world seam.

Allowed (trading-only):
- src.data.dual_run_lock (lock infrastructure — retained for future daemons)
- src.execution.harvester_pnl_resolver (trading-side P&L resolver)
- src.contracts.* (read API; typed ConnectionTriple accessors post-K1)
- src.data.proxy_health (startup gate)
- Everything else in src.engine, src.strategy, src.signal, src.control,
  src.config, src.observability, src.state, src.ingest.polymarket_user_channel.
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Modules that must NOT appear as imports in src/main.py (Phase 3 deportees)
FORBIDDEN_IMPORTS = [
    "src.data.daily_obs_append",
    "src.data.hourly_instants_append",
    "src.data.solar_append",
    "src.data.forecasts_append",
    "src.data.hole_scanner",
    "src.data.ecmwf_open_data",
    "src.data.source_health_probe",
    "src.data.ingest_status_writer",
    "src.calibration.drift_detector",
    "src.calibration.retrain_trigger_v2",
]

# String fragments that must NOT appear anywhere in src/main.py source text
FORBIDDEN_STRINGS = [
    "get_trade_connection_with_world",
    "ATTACH DATABASE",
]


def _collect_imports(tree: ast.AST) -> list[str]:
    """Return all module names imported (directly or via 'from ... import')."""
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
    return imported


def test_main_does_not_import_k2_ingest_modules():
    """src.main must not import any K2 ingest module (antibody #8)."""
    src_main = REPO_ROOT / "src" / "main.py"
    source = src_main.read_text()
    tree = ast.parse(source, filename=str(src_main))

    imported = _collect_imports(tree)
    violations = [
        module
        for module in FORBIDDEN_IMPORTS
        if any(imp == module or imp.startswith(module + ".") for imp in imported)
    ]

    assert not violations, (
        f"src/main.py imports forbidden ingest modules (Phase 3 violation):\n"
        + "\n".join(f"  - {m}" for m in violations)
        + "\nThese belong to com.zeus.data-ingest (src/ingest_main.py)."
    )


def test_main_does_not_contain_forbidden_strings():
    """src.main source must not contain ATTACH DATABASE or get_trade_connection_with_world."""
    src_main = REPO_ROOT / "src" / "main.py"
    source = src_main.read_text()

    violations = [s for s in FORBIDDEN_STRINGS if s in source]
    assert not violations, (
        f"src/main.py contains forbidden strings (Phase 3 violation):\n"
        + "\n".join(f"  - {s!r}" for s in violations)
    )


def test_dual_run_lock_allowed():
    """src.data.dual_run_lock is explicitly allowed — lock infrastructure stays."""
    src_main = REPO_ROOT / "src" / "main.py"
    source = src_main.read_text()
    tree = ast.parse(source, filename=str(src_main))
    imported = _collect_imports(tree)
    # dual_run_lock is allowed; this test documents the intent explicitly.
    # If it appears, that is expected. If not, also fine — just not forbidden.
    forbidden_present = any(
        imp.startswith("src.data.dual_run_lock") for imp in imported
    ) and False  # always passes — this is documentation only
    assert True, "src.data.dual_run_lock is an allowed import in src.main"


def test_harvester_pnl_resolver_allowed():
    """src.execution.harvester_pnl_resolver is trading-side; must remain importable from src.main."""
    src_main = REPO_ROOT / "src" / "main.py"
    source = src_main.read_text()
    # harvester_pnl_resolver is dynamically imported inside _harvester_cycle;
    # confirm the string appears (allowing either direct import or lazy import).
    assert "harvester_pnl_resolver" in source, (
        "src/main.py should reference harvester_pnl_resolver "
        "(trading-side P&L resolver per Phase 1.5 design)"
    )
