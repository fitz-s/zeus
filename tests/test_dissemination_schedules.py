# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_f11_forecast_issue_time/plan.md (Slice F11.1)
"""Antibodies for per-source forecast dissemination schedule registry.

Locks the verified primary-source dissemination lags into pytest
assertions that reference the citation URL in their docstring. A future
maintainer who changes a constant must justify the change against the
upstream wiki/page.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.backtest.decision_time_truth import (
    AvailabilityProvenance,
    DecisionTimeTruth,
    HindsightLeakageRefused,
    gate_for_purpose,
)
from src.backtest.purpose import BacktestPurpose
from src.data.dissemination_schedules import (
    UnknownSourceError,
    derive_availability,
    known_sources,
    schedule_url_for,
    verified_sources,
)


# ---------------------------------------------------------------------------
# ECMWF ENS — verified at https://confluence.ecmwf.int/display/DAC/Dissemination+schedule
# ---------------------------------------------------------------------------


def test_ecmwf_day0_dissemination_matches_confluence_wiki():
    base = datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc)
    avail, prov = derive_availability("ecmwf_previous_runs", base, 0)
    assert avail == base + timedelta(hours=6, minutes=40)
    assert prov is AvailabilityProvenance.DERIVED_FROM_DISSEMINATION


def test_ecmwf_day15_dissemination_matches_confluence_wiki():
    base = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    avail, _ = derive_availability("ecmwf_previous_runs", base, 15)
    assert avail == base + timedelta(hours=7, minutes=40)


def test_ecmwf_18z_base_disseminated_after_midnight_next_day():
    base = datetime(2026, 4, 28, 18, 0, tzinfo=timezone.utc)
    avail, _ = derive_availability("ecmwf_previous_runs", base, 0)
    assert avail == datetime(2026, 4, 29, 0, 40, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# NOAA GFS — verified at https://www.nco.ncep.noaa.gov/pmb/nwprod/prodstat/
# ---------------------------------------------------------------------------


def test_gfs_00z_completes_at_0414():
    """GFS MOS Forecast completion 04:14 UTC for 00z cycle."""
    base = datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc)
    avail, prov = derive_availability("gfs_previous_runs", base, 0)
    assert avail == datetime(2026, 4, 28, 4, 14, tzinfo=timezone.utc)
    assert prov is AvailabilityProvenance.DERIVED_FROM_DISSEMINATION


def test_gfs_06z_completes_at_1014():
    base = datetime(2026, 4, 28, 6, 0, tzinfo=timezone.utc)
    avail, _ = derive_availability("gfs_previous_runs", base, 0)
    assert avail == datetime(2026, 4, 28, 10, 14, tzinfo=timezone.utc)


def test_gfs_12z_completes_at_1614():
    base = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    avail, _ = derive_availability("gfs_previous_runs", base, 0)
    assert avail == datetime(2026, 4, 28, 16, 14, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Sources with unverified schedules — RECONSTRUCTED tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["icon_previous_runs", "ukmo_previous_runs", "openmeteo_previous_runs"])
def test_unverified_sources_return_reconstructed(source):
    base = datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc)
    _, prov = derive_availability(source, base, 0)
    assert prov is AvailabilityProvenance.RECONSTRUCTED


# ---------------------------------------------------------------------------
# Registry coverage + error handling
# ---------------------------------------------------------------------------


def test_known_sources_covers_all_forecasts_db_distribution():
    """Every source actually present in state/zeus-world.db::forecasts must be
    registered. Disk truth verified 2026-04-27/28: 5 sources distributed
    openmeteo / gfs / ecmwf / icon / ukmo previous_runs."""
    actual_sources = {
        "openmeteo_previous_runs",
        "gfs_previous_runs",
        "ecmwf_previous_runs",
        "icon_previous_runs",
        "ukmo_previous_runs",
    }
    assert actual_sources.issubset(known_sources())


def test_verified_sources_includes_ecmwf_and_gfs():
    verified = verified_sources()
    assert "ecmwf_previous_runs" in verified
    assert "gfs_previous_runs" in verified


def test_unverified_sources_excluded_from_verified_set():
    verified = verified_sources()
    assert "icon_previous_runs" not in verified
    assert "ukmo_previous_runs" not in verified
    assert "openmeteo_previous_runs" not in verified


def test_unknown_source_raises():
    with pytest.raises(UnknownSourceError):
        derive_availability(
            "not_a_real_source",
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            0,
        )


def test_negative_lead_day_rejected():
    with pytest.raises(ValueError):
        derive_availability(
            "ecmwf_previous_runs",
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            -1,
        )


def test_schedule_url_for_ecmwf_cites_confluence_wiki():
    assert "confluence.ecmwf.int" in schedule_url_for("ecmwf_previous_runs")


def test_schedule_url_for_gfs_cites_ncep_production_status():
    assert "nco.ncep.noaa.gov" in schedule_url_for("gfs_previous_runs")


def test_schedule_url_for_unknown_raises():
    with pytest.raises(UnknownSourceError):
        schedule_url_for("not_a_real_source")


# ---------------------------------------------------------------------------
# Integration with src.backtest.decision_time_truth gate_for_purpose
# ---------------------------------------------------------------------------


def test_skill_purpose_rejects_reconstructed_source():
    """SKILL refuses RECONSTRUCTED rows — heuristic timestamps corrupt
    forecast skill scoring (D4 antibody)."""
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    avail, prov = derive_availability("icon_previous_runs", base, 0)
    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
    with pytest.raises(HindsightLeakageRefused):
        gate_for_purpose(truth, BacktestPurpose.SKILL)


def test_skill_purpose_accepts_verified_ecmwf_source():
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    avail, prov = derive_availability("ecmwf_previous_runs", base, 0)
    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
    assert gate_for_purpose(truth, BacktestPurpose.SKILL) is truth


def test_skill_purpose_accepts_verified_gfs_source():
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    avail, prov = derive_availability("gfs_previous_runs", base, 0)
    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
    assert gate_for_purpose(truth, BacktestPurpose.SKILL) is truth


def test_economics_purpose_rejects_even_derived_dissemination():
    """ECONOMICS requires FETCH_TIME or RECORDED — DERIVED_FROM_DISSEMINATION
    is not promotion-grade."""
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    avail, prov = derive_availability("ecmwf_previous_runs", base, 0)
    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
    with pytest.raises(HindsightLeakageRefused):
        gate_for_purpose(truth, BacktestPurpose.ECONOMICS)


def test_diagnostic_purpose_accepts_all_sources():
    """DIAGNOSTIC accepts every tier; surfaces code/history divergence
    without making PnL or skill claims."""
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    for source in known_sources():
        avail, prov = derive_availability(source, base, 0)
        truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
        assert gate_for_purpose(truth, BacktestPurpose.DIAGNOSTIC) is truth
