# Created: 2026-04-30
# Last reused/audited: 2026-05-08
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §6 antibody #2; Wave38 dead hourly-observations writer deletion
# Lifecycle: created=2026-04-30; last_reviewed=2026-05-08; last_reused=2026-05-08
"""Antibody #2 — World-DB write boundary enforcement.

Only allowlisted modules may contain INSERT INTO / UPDATE / DELETE FROM
patterns targeting zeus-world.db tables.

Detection mechanism: grep-based scan of all Python files under src/ and
scripts/. Files that contain SQL write verbs (case-insensitive) AND
reference one of the known world-DB tables are checked against the
allowlist. Files NOT in the allowlist that contain both patterns → FAIL.

This is a Phase 1 detection pass. Phase 2 will replace with AST-based
cursor.execute inspection per design §6 antibody #4 specification.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Allowlisted write modules (relative to PROJECT_ROOT)
# These are the ONLY modules permitted to INSERT/UPDATE/DELETE world-DB tables.
# ---------------------------------------------------------------------------
ALLOWLISTED_WRITE_MODULES: frozenset[str] = frozenset(
    {
        # K2 appenders — primary world-DB writers
        "src/data/daily_obs_append.py",
        "src/data/hourly_instants_append.py",
        "src/data/solar_append.py",
        "src/data/forecasts_append.py",
        # Supporting writers
        "src/data/hole_scanner.py",
        "src/data/daily_observation_writer.py",
        "src/data/observation_instants_v2_writer.py",
        # Harvester — cross-DB writer; legacy allowlisted (Phase 1.5 split adds new writer below)
        "src/execution/harvester.py",
        # Phase 1.5: ingest-side settlement truth writer (owns world.settlements writes)
        "src/ingest/harvester_truth_writer.py",
        # Schema init — CREATE TABLE / ALTER TABLE (not DML, but allowed)
        "src/state/db.py",
        # Ingestion guard — wrapper layer; may emit INSERT via guard
        "src/data/ingestion_guard.py",
        # ---------------------------------------------------------------------------
        # Phase 1 legacy allowlist — pre-existing writers audited 2026-04-30.
        # These modules wrote world-DB tables before the two-system independence
        # boundary was established. They are allowlisted so this test can be
        # committed without breaking the test suite. Phase 2/3 will narrow each:
        # - ETL scripts → move to scripts/ingest/calibration/ (Q2 RESOLVED, Phase 3)
        # - calibration writers → remain read-only consumer in trading lane (Phase 2)
        # - engine/evaluator, engine/replay → move write paths to scripts/audit/ (§3.5)
        # ---------------------------------------------------------------------------
        # ETL scripts (operator-run offline; not daemon-scheduled)
        "scripts/etl_diurnal_curves.py",
        "scripts/etl_temp_persistence.py",
        "scripts/etl_historical_forecasts.py",
        "scripts/etl_forecast_skill_from_forecasts.py",
        "scripts/etl_asos_wu_offset.py",
        # Backfill and rebuild scripts (operator-run offline)
        "scripts/backfill_wu_daily_all.py",
        "scripts/rebuild_calibration_pairs_canonical.py",
        "scripts/rebuild_calibration_pairs_v2.py",
        "scripts/rebuild_settlements.py",
        "scripts/migrate_add_authority_column.py",
        # Calibration producers (trading-side read-only target per Q2; writers here
        # are the *producer* side — they produce Platt models, not consume them)
        "src/calibration/store.py",
        "src/calibration/retrain_trigger.py",
        # Engine paths that write ensemble snapshots / model_bias / forecast_skill
        # (replay write path to be moved to scripts/audit/ in Phase 2 per §3.5)
        "src/engine/evaluator.py",
        "src/engine/replay.py",
        # State layer — data_coverage and settlements path
        "src/state/data_coverage.py",
        "src/state/decision_chain.py",
        "src/state/venue_command_repo.py",
        # Riskguard — reads settlements/forecasts; may do limited writes
        "src/riskguard/riskguard.py",
        # Exchange reconcile — writes observations column for audit
        "src/execution/exchange_reconcile.py",
        # Strategy benchmark suite — writes observations for backtesting
        "src/strategy/benchmark_suite.py",
    }
)

# World-DB table names that must not be written outside the allowlist.
WORLD_DB_TABLES: tuple[str, ...] = (
    "observations",
    "observation_instants_v2",
    "forecasts",
    "solar_daily",
    "data_coverage",
    "settlements",
    "ensemble_snapshots",
    "ensemble_snapshots_v2",
    "calibration_pairs_v2",
    "platt_models_v2",
    "model_bias",
    "forecast_skill",
)

# SQL write verb pattern (case-insensitive).
_WRITE_VERB_RE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b",
    re.IGNORECASE,
)

# Table name pattern — matches any of the known world tables.
_TABLE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in WORLD_DB_TABLES) + r")\b",
    re.IGNORECASE,
)


def _scan_python_files() -> list[Path]:
    """All .py files under src/ and scripts/ (no __pycache__)."""
    files: list[Path] = []
    for base in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
        if base.exists():
            files.extend(
                p for p in base.rglob("*.py")
                if "__pycache__" not in p.parts
            )
    return sorted(files)


def test_deleted_hourly_observations_writer_not_allowlisted():
    """Wave38: deleted lossy compatibility writer must stay out of write allowlists."""
    deleted_writer = "scripts/etl_hourly_observations.py"

    assert deleted_writer not in ALLOWLISTED_WRITE_MODULES
    assert not (PROJECT_ROOT / deleted_writer).exists()


def _file_contains_write_and_table(py_path: Path) -> tuple[bool, list[str]]:
    """Return (has_both, matched_tables) if file has SQL write verbs + world table names."""
    try:
        text = py_path.read_text(encoding="utf-8")
    except Exception:
        return False, []
    has_write = bool(_WRITE_VERB_RE.search(text))
    if not has_write:
        return False, []
    tables = _TABLE_RE.findall(text)
    if not tables:
        return False, []
    return True, list(set(t.lower() for t in tables))


def test_world_writer_boundary():
    """Only allowlisted modules may contain world-DB write patterns.

    Detection: grep for (INSERT INTO|UPDATE ...|DELETE FROM) + any world
    table name in the same file. Files matching both patterns must be in
    ALLOWLISTED_WRITE_MODULES.

    Rationale: Any module outside the allowlist that writes world-DB
    tables bypasses the data-provenance contract and the two-system
    independence boundary. Trading-side code reading world data via
    SELECT is fine; only write paths are restricted.
    """
    violations: list[str] = []

    for py_path in _scan_python_files():
        rel = str(py_path.relative_to(PROJECT_ROOT))
        if rel in ALLOWLISTED_WRITE_MODULES:
            continue  # Explicitly allowed.

        has_both, tables = _file_contains_write_and_table(py_path)
        if has_both:
            violations.append(
                f"{rel}: contains SQL write verb + world table refs {tables} "
                f"but is NOT in ALLOWLISTED_WRITE_MODULES"
            )

    assert not violations, (
        "World-DB write boundary violated — modules outside the allowlist "
        "contain INSERT INTO / UPDATE / DELETE FROM targeting world-DB tables.\n"
        "To authorize a new writer: add it to ALLOWLISTED_WRITE_MODULES with "
        "an operator decision log entry.\n"
        "Violations:\n"
        + "\n".join("  - " + v for v in violations)
    )


def test_allowlisted_modules_exist():
    """All allowlisted write modules must exist on disk.

    Fails if an allowlisted module was deleted or renamed without updating
    the allowlist — prevents phantom allowlist entries.
    """
    missing = [
        rel for rel in ALLOWLISTED_WRITE_MODULES
        if not (PROJECT_ROOT / rel).exists()
    ]
    assert not missing, (
        "Allowlisted write modules do not exist on disk. "
        "Either they were renamed/deleted (update the allowlist) or "
        "the allowlist has stale entries:\n"
        + "\n".join("  - " + m for m in sorted(missing))
    )


def test_antibody_self_test_catches_synthetic_violation(tmp_path):
    """Build a fake file with a write verb + world table; confirm detection fires."""
    fake_file = tmp_path / "fake_writer.py"
    fake_file.write_text(
        'conn.execute("INSERT INTO observations VALUES (?, ?)", (city, value))\n',
        encoding="utf-8",
    )
    has_both, tables = _file_contains_write_and_table(fake_file)
    assert has_both, "Antibody failed to detect INSERT INTO + world table reference"
    assert "observations" in tables, f"Expected 'observations' in {tables}"
