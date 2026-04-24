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
    sizing_defaults,
)
from src.contracts.settlement_semantics import SettlementSemantics


## Paper mode test removed — Zeus is live-only (Phase 1 decommission).
## Original test asserted Settings().mode == "paper", which is no longer valid.


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
    assert len(cities) == 51  # 51 cities after global expansion
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
    # K3: cluster == city name for all cities
    assert by_name["NYC"].cluster == "NYC"
    assert by_name["Chicago"].cluster == "Chicago"
    assert by_name["Atlanta"].cluster == "Atlanta"
    assert by_name["London"].cluster == "London"
    assert by_name["Paris"].cluster == "Paris"
    assert by_name["Seoul"].cluster == "Seoul"
    assert by_name["Shanghai"].cluster == "Shanghai"
    assert by_name["Denver"].cluster == "Denver"
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
    refit_source = Path("/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/refit_platt.py").read_text()

    assert tuple(calibration_clusters()) == tuple(ALL_CLUSTERS)
    assert "calibration_clusters()" in manager_source
    assert "cluster, season" in refit_source


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
    assert limits.min_order_usd == defaults["min_order_usd"]


def test_correlation_matrix_covers_all_configured_clusters():
    # K3: correlation_matrix() removed from src.config — matrix is now in
    # config/city_correlation_matrix.json, accessed via src.strategy.correlation.
    # Coverage: test_cluster_collapse.py::test_correlation_self_is_one and
    # test_correlation_function_returns_float_in_01.
    # Verify get_correlation is importable and returns sane values for all clusters.
    from src.strategy.correlation import get_correlation
    for cluster in ALL_CLUSTERS:
        r = get_correlation(cluster, cluster)
        assert r == 1.0, f"{cluster} self-correlation should be 1.0"


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

    # London: City Airport (~51.505), NOT city center (~51.51) or Heathrow.
    lon = by_name["London"]
    assert abs(lon.lat - 51.5053) < 0.01


def test_city_wu_station_icao():
    """WU stations must be ICAO codes, not PWS IDs."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert by_name["NYC"].wu_station == "KLGA"
    assert by_name["Chicago"].wu_station == "KORD"
    assert by_name["Seattle"].wu_station == "KSEA"
    assert by_name["London"].wu_station == "EGLC"


def test_city_aliases():
    """Each city should have aliases for market title matching."""
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    assert "New York City" in by_name["NYC"].aliases
    assert "LA" in by_name["Los Angeles"].aliases
    assert "SF" in by_name["San Francisco"].aliases


def test_market_scanner_short_aliases_do_not_match_inside_other_city_names():
    from src.data.market_scanner import _match_city

    assert _match_city(
        "Highest temperature in Kuala Lumpur on April 12?",
        "highest-temperature-in-kuala-lumpur-on-april-12-2026",
    ).name == "Kuala Lumpur"
    assert _match_city(
        "Highest temperature in Lagos on April 12?",
        "highest-temperature-in-lagos-on-april-12-2026",
    ).name == "Lagos"
    assert _match_city(
        "Highest temperature in LA on April 12?",
        "highest-temperature-in-la-on-april-12-2026",
    ).name == "Los Angeles"


def _gamma_temperature_event(*, title: str, slug: str, question: str, **extra):
    event = {
        "id": "event-city-sanity",
        "title": title,
        "slug": slug,
        "endDate": "2026-04-13T23:59:00Z",
        "markets": [
            {
                "id": "market-city-sanity",
                "conditionId": "condition-city-sanity",
                "question": question,
                "clobTokenIds": json.dumps(["yes-token", "no-token"]),
                "outcomePrices": json.dumps([0.4, 0.6]),
            }
        ],
    }
    event.update(extra)
    return event


def test_market_scanner_rejects_la_event_with_milan_market_question():
    from datetime import datetime, timezone
    from src.data.market_scanner import _parse_event

    event = _gamma_temperature_event(
        title="Highest temperature in Los Angeles on April 13?",
        slug="highest-temperature-in-los-angeles-on-april-13-2026",
        question="Will the high temperature in Milan be 20°C or higher?",
    )

    assert _parse_event(event, datetime(2026, 4, 13, tzinfo=timezone.utc), 0.0) is None


def test_market_scanner_rejects_conflicting_title_and_slug_city():
    from datetime import datetime, timezone
    from src.data.market_scanner import _parse_event

    event = _gamma_temperature_event(
        title="Highest temperature in Milan on April 13?",
        slug="highest-temperature-in-los-angeles-on-april-13-2026",
        question="Will the high temperature in Los Angeles be 68°F or higher?",
    )

    assert _parse_event(event, datetime(2026, 4, 13, tzinfo=timezone.utc), 0.0) is None


def test_market_scanner_rejects_la_event_with_milan_station_metadata():
    from datetime import datetime, timezone
    from src.data.market_scanner import _parse_event

    event = _gamma_temperature_event(
        title="Highest temperature in Los Angeles on April 13?",
        slug="highest-temperature-in-los-angeles-on-april-13-2026",
        question="Will the high temperature in Los Angeles be 68°F or higher?",
        resolutionSource="Milan Malpensa Airport LIMC",
    )

    assert _parse_event(event, datetime(2026, 4, 13, tzinfo=timezone.utc), 0.0) is None


def test_market_scanner_accepts_la_event_with_la_station_metadata():
    from datetime import datetime, timezone
    from src.data.market_scanner import _parse_event

    event = _gamma_temperature_event(
        title="Highest temperature in Los Angeles on April 13?",
        slug="highest-temperature-in-los-angeles-on-april-13-2026",
        question="Will the high temperature in Los Angeles be 68°F or higher?",
        resolutionSource="Los Angeles International Airport KLAX",
    )

    parsed = _parse_event(event, datetime(2026, 4, 13, tzinfo=timezone.utc), 0.0)

    assert parsed is not None
    assert parsed["city"].name == "Los Angeles"
    assert parsed["outcomes"][0]["range_low"] == pytest.approx(68.0)


def test_market_scanner_accepts_self_consistent_configured_city_metadata():
    from datetime import datetime, timezone
    from src.data.market_scanner import _parse_event

    for city in load_cities():
        slug_city = (city.slug_names[0] if city.slug_names else city.name.lower().replace(" ", "-"))
        temp_label = "68°F" if city.settlement_unit == "F" else "20°C"
        event = _gamma_temperature_event(
            title=f"Highest temperature in {city.name} on April 13?",
            slug=f"highest-temperature-in-{slug_city}-on-april-13-2026",
            question=f"Will the high temperature in {city.name} be {temp_label} or higher?",
            resolutionSource=f"{city.airport_name} {city.wu_station}",
        )

        parsed = _parse_event(event, datetime(2026, 4, 13, tzinfo=timezone.utc), 0.0)

        assert parsed is not None, city.name
        assert parsed["city"].name == city.name


def test_settlement_semantics_matches_city_metadata():
    for city in load_cities():
        sem = SettlementSemantics.for_city(city)
        assert sem.measurement_unit == city.settlement_unit
        assert sem.finalization_time == "12:00:00Z"

        if city.settlement_source_type == "wu_icao":
            assert sem.resolution_source == f"WU_{city.wu_station}"
        else:
            # Non-WU sources use source_type prefix
            assert sem.resolution_source == f"{city.settlement_source_type}_{city.wu_station}"


def test_validate_cities_config_no_warnings():
    from src.config import validate_cities_config
    warnings = validate_cities_config()
    assert warnings == [], f"City config validation warnings: {warnings}"
