# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/
#                  DESIGN_PHASE3_LIVE_ROUTING_FIX.md
#                  + Operator directive 2026-05-04 ("下单数据要和训练数据对齐")
"""Phase 3 contract tests: ecmwf_ifs025 routes to ecmwf_open_data, not Open-Meteo.

Relationship invariants:
    * ENSEMBLE_MODEL_SOURCE_MAP['ecmwf_ifs025'] points to 'ecmwf_open_data'
    * ecmwf_open_data ForecastSourceSpec has 'entry_primary' role
    * ecmwf_open_data degradation_level is 'OK' (not DIAGNOSTIC_*)
    * Open-Meteo ensemble source remains monitor_fallback only (we did not
      regress the legacy openmeteo profile — it is deliberately excluded
      from entry_primary)
"""

from __future__ import annotations

from src.data.forecast_source_registry import (
    ENSEMBLE_MODEL_SOURCE_MAP,
    SOURCES,
    get_source,
)


def test_ecmwf_ifs025_model_routes_to_ecmwf_open_data():
    """The fix: model 'ecmwf_ifs025' resolves to source 'ecmwf_open_data'.

    Before this change it routed to 'openmeteo_ensemble_ecmwf_ifs025',
    which has degradation_level='DEGRADED_FORECAST_FALLBACK' and was
    rejected for entry_primary — causing 74/200 SIGNAL_QUALITY rejections.
    """
    assert ENSEMBLE_MODEL_SOURCE_MAP["ecmwf_ifs025"] == "ecmwf_open_data"


def test_ecmwf_open_data_authorized_for_entry_primary():
    spec = get_source("ecmwf_open_data")
    assert "entry_primary" in spec.allowed_roles
    assert spec.degradation_level == "OK"


def test_openmeteo_ensemble_remains_monitor_fallback_only():
    """Legacy Open-Meteo ensemble source stays restricted to monitor_fallback.

    We deliberately did NOT promote openmeteo_ensemble_ecmwf_ifs025; the
    fix changes routing, not the broker source's role. Open-Meteo applies
    1-hour temporal interpolation that breaks training/serving alignment.
    """
    spec = get_source("openmeteo_ensemble_ecmwf_ifs025")
    assert "entry_primary" not in spec.allowed_roles
    assert "monitor_fallback" in spec.allowed_roles
    assert spec.degradation_level == "DEGRADED_FORECAST_FALLBACK"


def test_gfs_routing_unchanged_by_phase3():
    """Phase 3 only fixed ecmwf_ifs025. GFS routing is untouched."""
    assert ENSEMBLE_MODEL_SOURCE_MAP["gfs025"] == "openmeteo_ensemble_gfs025"
    assert ENSEMBLE_MODEL_SOURCE_MAP["gfs"] == "openmeteo_ensemble_gfs025"


def test_tigge_routing_unchanged_by_phase3():
    """TIGGE routes to itself; Phase 3 does not touch the archive path."""
    assert ENSEMBLE_MODEL_SOURCE_MAP["tigge"] == "tigge"
    spec = SOURCES["tigge"]
    assert "entry_primary" in spec.allowed_roles
