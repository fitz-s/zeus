# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: K1_READER_SWEEP.md TOP-10 batch-2 — scripts 3-5
#                  K1 DB split commit eba80d2b9d (2026-05-11)
#
# Scope: Covers batch-2 scripts only (data_chain_monitor.sh, ingest_grib_to_snapshots.py,
#        rebuild_calibration_pairs_v2.py).
#
# NOTE: Bundle with tests/test_k1_reader_isolation.py once both PRs merge
#       (fix/k1-reader-sweep-2026-05-17 + fix/k1-readers-batch-2-2026-05-17).
#       At that point merge into one parametrized test covering all fixed scripts.
#
# ALLOWLIST: bridge_oracle_to_calibration.py + evaluate_calibration_transfer_oos.py
# are intentionally excluded — their fixes are in-flight on fix/k1-reader-sweep-2026-05-17
# and are NOT yet merged. Remove from allowlist when that PR lands.

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

FORECAST_CLASS_TABLES = {
    "observations",
    "settlements",
    "settlements_v2",
    "source_run",
    "job_run",
    "source_run_coverage",
    "readiness_state",
    "market_events_v2",
    "ensemble_snapshots_v2",
    "calibration_pairs_v2",
}

# Patterns that indicate routing to world.db — both Python and shell variants
BAD_PATTERNS = [
    r"state/zeus-world\.db",
    r"get_world_connection",
    r"ZEUS_WORLD_DB_PATH",
]

# Batch-2 fixed scripts — explicit list, not a glob (avoids false positives from
# scripts awaiting fix in other PRs).
# NOTE: rebuild_calibration_pairs_v2.py is intentionally excluded from this parametrized
# test because it legitimately references ZEUS_WORLD_DB_PATH in a write-guard function
# (_resolve_isolated_calibration_write_db_path) that prevents accidental writes to world.db.
# Its K1 fix is covered by test_rebuild_calibration_pairs_v2_dry_run_default_is_forecasts.
BATCH2_SCRIPTS = [
    REPO / "scripts" / "data_chain_monitor.sh",
    REPO / "scripts" / "ingest_grib_to_snapshots.py",
]


@pytest.mark.parametrize("script", BATCH2_SCRIPTS, ids=lambda p: p.name)
def test_batch2_no_forecast_class_via_world_connection(script: Path) -> None:
    """Batch-2 fixed scripts must not access forecast_class tables via world-DB paths."""
    assert script.exists(), f"Script not found: {script}"
    src = script.read_text()

    uses_bad = any(re.search(p, src) for p in BAD_PATTERNS)
    uses_forecast_table = any(t in src for t in FORECAST_CLASS_TABLES)

    assert not (uses_bad and uses_forecast_table), (
        f"{script.name}: still uses world-DB access AND forecast_class table — "
        f"K1-batch2 fix may not have been applied correctly. "
        f"Expected get_forecasts_connection() or get_forecasts_connection_with_world()."
    )


def test_rebuild_calibration_pairs_v2_dry_run_default_is_forecasts() -> None:
    """rebuild_calibration_pairs_v2.py dry-run default must point at ZEUS_FORECASTS_DB_PATH."""
    src = (REPO / "scripts" / "rebuild_calibration_pairs_v2.py").read_text()
    # The dry-run block must import ZEUS_FORECASTS_DB_PATH, not ZEUS_WORLD_DB_PATH
    dry_run_block = src[src.find("if args.dry_run:"):]
    assert "ZEUS_FORECASTS_DB_PATH" in dry_run_block, (
        "rebuild_calibration_pairs_v2.py dry-run default must use ZEUS_FORECASTS_DB_PATH"
    )
    # Ensure the old world path is not present in the dry-run block
    assert "ZEUS_WORLD_DB_PATH" not in dry_run_block, (
        "rebuild_calibration_pairs_v2.py dry-run block must not reference ZEUS_WORLD_DB_PATH"
    )


def test_ingest_grib_default_lock_is_forecasts() -> None:
    """ingest_grib_to_snapshots.py default lock path must reference ZEUS_FORECASTS_DB_PATH."""
    src = (REPO / "scripts" / "ingest_grib_to_snapshots.py").read_text()
    # Top-level import must bring in ZEUS_FORECASTS_DB_PATH, not ZEUS_WORLD_DB_PATH
    assert "ZEUS_FORECASTS_DB_PATH" in src, (
        "ingest_grib_to_snapshots.py must import ZEUS_FORECASTS_DB_PATH"
    )
    assert "get_forecasts_connection" in src, (
        "ingest_grib_to_snapshots.py must use get_forecasts_connection"
    )
    # Old import must be gone
    assert "get_world_connection" not in src, (
        "ingest_grib_to_snapshots.py must not import get_world_connection"
    )


def test_data_chain_monitor_uses_forecasts_db() -> None:
    """data_chain_monitor.sh must connect to zeus-forecasts.db, not zeus-world.db."""
    src = (REPO / "scripts" / "data_chain_monitor.sh").read_text()
    assert "state/zeus-forecasts.db" in src, (
        "data_chain_monitor.sh must connect to state/zeus-forecasts.db"
    )
    assert "state/zeus-world.db" not in src, (
        "data_chain_monitor.sh must not connect to state/zeus-world.db"
    )
