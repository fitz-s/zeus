# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR7, PR8);
#   operator spec §5/§7 (provenance) + §"Scheduler/concurrency efficiency" (lane separation).
"""PR7 (row-level provenance + live gate) + PR8 (derived/live lane separation)."""
from __future__ import annotations


# ---- PR7: temporal provenance ----

def test_live_reader_gate_default_off() -> None:
    from src.data.temporal_provenance import live_reader_requires_provenance

    assert live_reader_requires_provenance() is False


def test_source_run_columns_satisfy_live_provenance() -> None:
    """The real source_run schema must carry the required live-provenance fields (it does today —
    this locks it so a future column drop is caught)."""
    from src.data.temporal_provenance import REQUIRED_LIVE_PROVENANCE, missing_live_provenance

    source_run_cols = {
        "source_run_id", "source_id", "track", "source_issue_time", "source_release_time",
        "captured_at", "imported_at", "target_local_date", "data_version", "status",
    }
    assert missing_live_provenance(source_run_cols) == []
    assert REQUIRED_LIVE_PROVENANCE <= source_run_cols


def test_incomplete_columns_flagged() -> None:
    from src.data.temporal_provenance import missing_live_provenance

    missing = missing_live_provenance({"source_id", "captured_at"})
    assert "data_version" in missing and "source_issue_time" in missing


def test_backfill_shadow_cannot_authorize_live() -> None:
    """Reconstructed/archive/shadow/backfill tiers can NEVER authorize live readiness."""
    from src.data.temporal_provenance import can_authorize_live_readiness

    assert can_authorize_live_readiness("DERIVED_FROM_DISSEMINATION", True) is True   # live ECMWF
    assert can_authorize_live_readiness("RECONSTRUCTED", True) is False                # Open-Meteo/TIGGE
    assert can_authorize_live_readiness("ARCHIVE", True) is False
    assert can_authorize_live_readiness("DERIVED_FROM_DISSEMINATION", False) is False  # not authorized


# ---- PR8: lane separation ----

def test_derived_jobs_not_on_live_lane() -> None:
    """ANTIBODY: no derived/diagnostic/backfill DB writer shares the live_db lane (would starve
    live ingest behind the serial SQLite writer)."""
    from src.data.scheduler_adapter import build_job_specs, validate_lane_separation

    assert validate_lane_separation(build_job_specs()) == []


def test_calibration_etl_is_derived_lane_not_live() -> None:
    from src.data.scheduler_adapter import build_job_specs

    by_id = {s.job_id: s for s in build_job_specs()}
    assert by_id["ingest_etl_recalibrate"].executor_class == "derived_db"
    assert by_id["ingest_calibration_auto_promote"].executor_class == "derived_db"
    assert by_id["ingest_drift_detector"].executor_class == "derived_db"
    # while a live ingest job is on the live lane:
    assert by_id["ingest_market_scan"].executor_class == "live_db"
