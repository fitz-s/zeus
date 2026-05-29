# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Row-level provenance contract + derived/live lane separation.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
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


def test_unknown_authority_tier_is_fail_closed() -> None:
    """F5: ALLOW-LIST fail-closed — an empty/unknown authority tier must NOT authorize live
    (the prior deny-list let unknown tiers through)."""
    from src.data.temporal_provenance import can_authorize_live_readiness

    assert can_authorize_live_readiness("", True) is False
    assert can_authorize_live_readiness("UNKNOWN", True) is False
    assert can_authorize_live_readiness("SOMETHING_NEW", True) is False


def test_provenance_required_fields_are_family_specific() -> None:
    """F6: provenance is keyed by data family — a venue/market row is NOT held to forecast
    source_run fields (source_run_id/source_issue_time), and unknown family fails closed."""
    from src.data.temporal_provenance import missing_live_provenance

    # forecast row needs source_run identity:
    assert missing_live_provenance({"source_id", "captured_at"}, "forecast")
    # an executable-market snapshot is complete WITHOUT source_run_id/source_issue_time:
    assert missing_live_provenance(
        {"condition_id", "captured_at", "freshness_deadline", "authority_tier"},
        "executable_snapshot",
    ) == []
    # market topology row uses REAL market_events column created_at (not captured_at):
    assert missing_live_provenance({"condition_id", "created_at"}, "market") == []
    assert "created_at" in missing_live_provenance({"condition_id", "captured_at"}, "market")
    # observation uses REAL observations columns (source/station_id/target_date/fetched_at):
    assert missing_live_provenance(
        {"source", "station_id", "target_date", "fetched_at"}, "observation") == []
    # unknown family fails closed (never "complete"):
    assert missing_live_provenance({"anything"}, "no_such_family")


def test_live_authority_is_family_specific() -> None:
    """F (R2): CLOB authorizes an executable-snapshot/venue row but NOT a forecast row;
    GAMMA authorizes market topology; unknown family fails closed."""
    from src.data.temporal_provenance import can_authorize_live_readiness

    assert can_authorize_live_readiness("CLOB", True, family="executable_snapshot") is True
    assert can_authorize_live_readiness("CLOB", True, family="forecast") is False
    assert can_authorize_live_readiness("GAMMA", True, family="market") is True
    assert can_authorize_live_readiness("DERIVED_FROM_DISSEMINATION", True, family="forecast") is True
    assert can_authorize_live_readiness("CLOB", True, family="no_such_family") is False


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


def test_obs_instant_v2_provenance_distinct_from_daily() -> None:
    """F11: observation_instant_v2 family requires utc_timestamp/imported_at/authority — daily
    observation fields (fetched_at) do NOT satisfy it, and vice-versa."""
    from src.data.temporal_provenance import missing_live_provenance

    daily_cols = {"source", "station_id", "target_date", "fetched_at"}
    v2_cols = {"source", "station_id", "target_date", "utc_timestamp", "imported_at",
               "authority", "data_version"}
    assert missing_live_provenance(v2_cols, "observation_instant_v2") == []
    assert missing_live_provenance(daily_cols, "observation_instant_v2")   # daily != v2
    assert missing_live_provenance(daily_cols, "daily_observation") == []
