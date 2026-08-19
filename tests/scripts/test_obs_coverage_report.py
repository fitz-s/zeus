# Lifecycle: created=2026-08-05; last_reviewed=2026-08-05; last_reused=2026-08-05
# Purpose: Keep probe-only archive reachability out of city observation truth.
"""Observation coverage must not turn diagnostic probes into city truth."""

from scripts.obs_coverage_report import _blocking_probe_only_stale


def test_archive_probe_staleness_does_not_report_global_day0_blackout() -> None:
    assert _blocking_probe_only_stale(
        {"open_meteo_archive": False, "noaa": True}
    ) == []


def test_gated_probe_only_source_remains_visible_when_stale() -> None:
    assert _blocking_probe_only_stale(
        {"open_meteo_archive": True, "noaa": False}
    ) == ["noaa"]
