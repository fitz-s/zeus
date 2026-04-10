"""Tests for config loader and city metadata."""

import json
import inspect
import tempfile
from pathlib import Path

import pytest

from src.config import (
    ALL_CLUSTERS,
    Settings,
    calibration_clusters,
    calibration_maturity_thresholds,
    day0_n_mc,
    day0_obs_dominates_threshold,
    edge_n_bootstrap,
    ensemble_bimodal_gap_ratio,
    ensemble_bimodal_kde_order,
    ensemble_boundary_window,
    ensemble_instrument_noise,
    ensemble_member_count,
    ensemble_n_mc,
    ensemble_unimodal_range_epsilon,
    load_cities,
    correlation_matrix,
    sizing_defaults,
)
from src.contracts.settlement_semantics import SettlementSemantics


def test_settings_loads_all_required_keys():
    s = Settings()
    assert s.mode == "paper"
    assert s.capital_base_usd == 150.0
    assert s["discovery"]["opening_hunt_interval_min"] == 30
    assert s["discovery"]["ecmwf_open_data_times_utc"] == ["01:30", "13:30"]
    assert s["sizing"]["kelly_multiplier"] == 0.25


def test_settings_missing_key_raises():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"capital_base_usd": 100}, f)
        f.flush()
        with pytest.raises(KeyError, match="Missing required config key"):
            Settings(path=Path(f.name))


def test_settings_no_fallback_pattern():
    """Settings must raise KeyError on missing nested keys, not return a default."""
    s = Settings()
    with pytest.raises(KeyError):
        _ = s["nonexistent_section"]


def test_cities_load():
    cities = load_cities()
    assert len(cities) == 46  # 46 cities after global expansion
    names = {c.name for c in cities}
    assert "NYC" in names
    assert "London" in names
    assert "Paris" in names
    assert "Seoul" in names
    assert "Austin" in names


def test_city_settlement_units():
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].settlement_unit == "F"
    assert by_name["London"].settlement_unit == "C"
    assert by_name["Paris"].settlement_unit == "C"
    assert by_name["Seoul"].settlement_unit == "C"
    assert by_name["Tokyo"].settlement_unit == "C"


def test_city_clusters():
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].cluster == "US-Northeast"
    assert by_name["Chicago"].cluster == "US-GreatLakes"
    assert by_name["Atlanta"].cluster == "US-Southeast-Inland"
    assert by_name["London"].cluster == "Europe-Maritime"
    assert by_name["Paris"].cluster == "Europe-Continental"
    assert by_name["Seoul"].cluster == "Asia-Northeast"
    assert by_name["Shanghai"].cluster == "Asia-East-China"
    assert by_name["Denver"].cluster == "US-Rockies"
    assert set(calibration_clusters()) == set(ALL_CLUSTERS)


def test_city_without_explicit_cluster_is_rejected(tmp_path):
    path = tmp_path / "cities.json"
    path.write_text(json.dumps({
        "cities": [
            {
                "name": "Unknown City",
                "lat": 1,
                "lon": 2,
                "timezone": "UTC",
                "unit": "F",
                "wu_station": "KUNK",
            }
        ]
    }))
    with pytest.raises(KeyError, match="missing from city metadata cluster field"):
        load_cities(path=path)


def test_calibration_manager_cluster_taxonomy_matches_config():
    manager_source = Path("/Users/leofitz/.openclaw/workspace-venus/zeus/src/calibration/manager.py").read_text()
    etl_source = Path("/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/etl_tigge_calibration.py").read_text()

    assert tuple(calibration_clusters()) == tuple(ALL_CLUSTERS)
    assert "calibration_clusters()" in manager_source
    assert "calibration_clusters()" in etl_source


def test_calibration_thresholds_are_single_sourced_from_settings():
    s = Settings()
    expected = (
        int(s["calibration"]["maturity"]["level1"]),
        int(s["calibration"]["maturity"]["level2"]),
        int(s["calibration"]["maturity"]["level3"]),
    )
    assert calibration_maturity_thresholds() == expected
    from src.calibration.manager import maturity_level
    assert maturity_level(expected[0]) == 1
    assert maturity_level(expected[1]) == 2
    assert maturity_level(expected[2]) == 3
    assert maturity_level(expected[2] - 1) == 4


def test_platt_bootstrap_iterations_are_single_sourced_from_settings():
    from src.calibration.platt import DEFAULT_N_BOOTSTRAP
    from src.strategy.market_analysis import DEFAULT_EDGE_BOOTSTRAP
    s = Settings()
    assert DEFAULT_N_BOOTSTRAP == int(s["calibration"]["n_bootstrap"])
    assert DEFAULT_EDGE_BOOTSTRAP == edge_n_bootstrap()


def test_risk_limit_defaults_are_single_sourced_from_settings():
    from src.strategy.risk_limits import RiskLimits

    defaults = sizing_defaults()
    limits = RiskLimits()
    assert limits.max_single_position_pct == defaults["max_single_position_pct"]
    assert limits.max_portfolio_heat_pct == defaults["max_portfolio_heat_pct"]
    assert limits.max_correlated_pct == defaults["max_correlated_pct"]
    assert limits.max_city_pct == defaults["max_city_pct"]
    assert limits.max_region_pct == defaults["max_region_pct"]
    assert limits.min_order_usd == defaults["min_order_usd"]


def test_correlation_matrix_covers_all_configured_clusters():
    matrix = correlation_matrix()
    assert set(matrix) == set(ALL_CLUSTERS)
    for cluster, mapping in matrix.items():
        assert cluster in ALL_CLUSTERS
        assert set(mapping).issubset(set(ALL_CLUSTERS))
        assert cluster not in mapping


def test_signal_constants_are_single_sourced_from_settings():
    from src.signal.ensemble_signal import (
        BIMODAL_GAP_RATIO,
        BIMODAL_KDE_ORDER,
        BOUNDARY_WINDOW,
        DEFAULT_N_MC,
        SIGMA_INSTRUMENT,
        UNIMODAL_RANGE_EPSILON,
    )

    assert ensemble_member_count() == 51
    assert DEFAULT_N_MC == ensemble_n_mc()
    assert SIGMA_INSTRUMENT == ensemble_instrument_noise("F")
    assert BIMODAL_KDE_ORDER == ensemble_bimodal_kde_order()
    assert BIMODAL_GAP_RATIO == ensemble_bimodal_gap_ratio()
    assert BOUNDARY_WINDOW == ensemble_boundary_window()
    assert UNIMODAL_RANGE_EPSILON == ensemble_unimodal_range_epsilon()


def test_day0_constants_are_single_sourced_from_settings():
    from src.signal.day0_signal import Day0Signal
    import numpy as np

    sig = Day0Signal(
        observed_high_so_far=40.0,
        current_temp=39.0,
        hours_remaining=6.0,
        member_maxes_remaining=np.array([39.0, 40.0, 41.0]),
    )
    assert day0_n_mc() == 5000
    assert sig.obs_dominates() is False
    assert day0_obs_dominates_threshold() == 0.8

    p_vector_signature = inspect.signature(Day0Signal.p_vector)
    assert p_vector_signature.parameters["n_mc"].default is None


def test_city_has_timezone():
    cities = load_cities()
    for c in cities:
        assert c.timezone is not None
        assert "/" in c.timezone  # IANA format


def test_city_airport_coordinates():
    """Coordinates must be airport (settlement station), not city center."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}

    # NYC: LaGuardia (40.7772), NOT Manhattan (40.7128)
    nyc = by_name["NYC"]
    assert abs(nyc.lat - 40.7772) < 0.01
    assert abs(nyc.lon - (-73.8726)) < 0.01

    # Chicago: O'Hare (~41.97), NOT downtown (~41.88)
    chi = by_name["Chicago"]
    assert abs(chi.lat - 41.9742) < 0.01

    # London: Heathrow (~51.48), NOT city center (~51.51)
    lon = by_name["London"]
    assert abs(lon.lat - 51.4775) < 0.01


def test_city_wu_station_icao():
    """WU stations must be ICAO codes, not PWS IDs."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].wu_station == "KLGA"
    assert by_name["Chicago"].wu_station == "KORD"
    assert by_name["Seattle"].wu_station == "KSEA"
    assert by_name["London"].wu_station == "EGLL"


def test_city_aliases():
    """Each city should have aliases for market title matching."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert "New York City" in by_name["NYC"].aliases
    assert "LA" in by_name["Los Angeles"].aliases
    assert "SF" in by_name["San Francisco"].aliases


def test_settlement_semantics_matches_city_metadata():
    for city in load_cities():
        sem = SettlementSemantics.for_city(city)
        assert sem.measurement_unit == city.settlement_unit
        assert sem.finalization_time == "12:00:00Z"

        if "wunderground.com" in city.settlement_source:
            assert sem.resolution_source == f"WU_{city.wu_station}"
        else:
            # Non-WU sources use station code directly
            assert sem.resolution_source == city.wu_station
