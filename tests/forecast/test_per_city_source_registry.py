# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator per-city source registry (docs/polyweather_city_source_overlay_verified.csv).
"""RED-on-revert tests for the per-city source registry loader."""
from __future__ import annotations

from src.forecast.per_city_source_registry import (
    NATIONAL_FORECAST_KINDS,
    load_per_city_source_registry,
)


def test_registry_loads_all_cities_with_openmeteo_multimodel():
    reg = load_per_city_source_registry()
    assert len(reg) >= 50, f"expected >=50 cities, got {len(reg)}"
    # every city must carry the open-meteo multi-model forecast source
    missing = [ck for ck, p in reg.items() if not p.has_openmeteo_multimodel]
    assert not missing, f"cities missing openmeteo multi-model: {missing}"


def test_national_forecasts_are_recognized_where_present():
    reg = load_per_city_source_registry()
    # the US cities carry an NWS forecast; HK/Shenzhen carry HKO; Taipei CWA; Turkey MGM; Jeddah NCM
    nws = [ck for ck, p in reg.items() if "nws_forecast" in p.national_forecast_kinds]
    assert len(nws) >= 8, f"expected the US cluster to carry nws_forecast, got {nws}"
    hko = [ck for ck, p in reg.items() if "hko_forecast" in p.national_forecast_kinds]
    assert {"hong-kong", "shenzhen"} <= set(hko), f"HK/Shenzhen must carry hko_forecast, got {hko}"


def test_forecast_sources_sorted_by_priority_rank():
    reg = load_per_city_source_registry()
    sample = next(iter(reg.values()))
    ranks = [r.priority_rank for r in sample.forecast_sources]
    assert ranks == sorted(ranks), "forecast_sources must be priority-ascending"


def test_national_kinds_constant_covers_the_five_services():
    assert NATIONAL_FORECAST_KINDS == {
        "nws_forecast", "mgm_forecast", "hko_forecast", "cwa_forecast", "ncm_forecast"
    }
