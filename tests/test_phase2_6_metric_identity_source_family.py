# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/
#                  DESIGN_PHASE2_6_DATA_VERSION_STRATIFICATION.md
#                  + critic-opus PR#55 BLOCKER 3 (TIGGE-vs-OpenData asymmetry)
"""Phase 2.6 contract tests: source-family-aware MetricIdentity factories.

Relationship invariants:
    * for_high_localday_max(source_family) returns a MetricIdentity whose
      data_version matches the registry → no hardcoded TIGGE constant
      slipping into OpenData paths
    * Unknown source_family raises ValueError at the factory boundary
      (fail-loud, not silent fallback to TIGGE)
    * source_family_from_data_version is the inverse of the factory's
      data_version selection
    * Manager.get_calibrator with source_id derives expected_data_version
      via the source_family registry, not via the legacy hardcoded constant
"""

from __future__ import annotations

import pytest

from src.types.metric_identity import (
    HIGH_LOCALDAY_MAX,
    LOW_LOCALDAY_MIN,
    MetricIdentity,
    source_family_from_data_version,
)


def test_for_high_localday_max_tigge_matches_legacy_constant():
    """tigge factory must produce the same data_version as the legacy
    module-level HIGH_LOCALDAY_MAX constant — otherwise existing TIGGE
    Platt buckets become unreachable.
    """
    mi = MetricIdentity.for_high_localday_max("tigge")
    assert mi.data_version == HIGH_LOCALDAY_MAX.data_version
    assert mi.temperature_metric == "high"
    assert mi.observation_field == "high_temp"


def test_for_high_localday_max_opendata_distinct_from_tigge():
    """opendata factory must produce a DIFFERENT data_version than tigge.

    If both produced the same string, OpenData forecasts would silently
    inherit TIGGE Platt buckets — exactly the bug critic-opus BLOCKER 3
    flagged.
    """
    tigge = MetricIdentity.for_high_localday_max("tigge")
    opendata = MetricIdentity.for_high_localday_max("ecmwf_opendata")
    assert tigge.data_version != opendata.data_version
    assert "ecmwf_opendata" in opendata.data_version
    assert opendata.observation_field == "high_temp"


def test_for_low_localday_min_tigge_matches_legacy_constant():
    mi = MetricIdentity.for_low_localday_min("tigge")
    assert mi.data_version == LOW_LOCALDAY_MIN.data_version
    assert mi.temperature_metric == "low"


def test_for_low_localday_min_opendata_distinct_from_tigge():
    tigge = MetricIdentity.for_low_localday_min("tigge")
    opendata = MetricIdentity.for_low_localday_min("ecmwf_opendata")
    assert tigge.data_version != opendata.data_version
    assert "ecmwf_opendata" in opendata.data_version


def test_unknown_source_family_raises_at_factory():
    """Unknown source_family must raise — not silently default to TIGGE.

    Silent fallback was the old bug: an OpenData snapshot would land in a
    TIGGE bucket because hardcoded HIGH_LOCALDAY_MAX wasn't source-aware.
    The factory makes the failure explicit.
    """
    with pytest.raises(ValueError, match="source_family"):
        MetricIdentity.for_high_localday_max("openmeteo")
    with pytest.raises(ValueError, match="source_family"):
        MetricIdentity.for_low_localday_min("gfs025")


def test_for_metric_with_source_family_dispatches_correctly():
    a = MetricIdentity.for_metric_with_source_family("high", "ecmwf_opendata")
    b = MetricIdentity.for_high_localday_max("ecmwf_opendata")
    assert a == b
    c = MetricIdentity.for_metric_with_source_family("low", "tigge")
    d = MetricIdentity.for_low_localday_min("tigge")
    assert c == d
    with pytest.raises(ValueError):
        MetricIdentity.for_metric_with_source_family("medium", "tigge")


def test_source_family_from_data_version_is_inverse_of_factory():
    """The reverse-lookup helper recovers source_family from any data_version
    the factory could produce. Closes the loop so evaluator can route on
    data_version strings stored in ensemble_snapshots_v2.
    """
    for family in ("tigge", "ecmwf_opendata"):
        for builder in (
            MetricIdentity.for_high_localday_max,
            MetricIdentity.for_low_localday_min,
        ):
            mi = builder(family)
            assert source_family_from_data_version(mi.data_version) == family


def test_source_family_from_data_version_rejects_unknown():
    assert source_family_from_data_version("openmeteo_v1") is None
    assert source_family_from_data_version("gfs_v1") is None
    assert source_family_from_data_version(None) is None
    assert source_family_from_data_version("") is None
    assert source_family_from_data_version(123) is None  # type: ignore[arg-type]
