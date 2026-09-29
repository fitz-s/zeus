# Created: pre-Phase-0
# Last reused/audited: 2026-09-29
# Authority basis: Phase 10 DT-close B001 config contract + first-principles ZEUS_MODE cleanup 2026-04-30
"""Tests for config loader and city metadata."""

import json
import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.config import (
    ALL_CLUSTERS,
    Settings,
    calibration_batch_rebuild_n_mc,
    calibration_clusters,
    calibration_maturity_thresholds,
    day0_n_mc,
    edge_n_bootstrap,
    ensemble_bimodal_gap_ratio,
    ensemble_bimodal_kde_order,
    ensemble_boundary_window,
    ensemble_instrument_noise,
    ensemble_member_count,
    ensemble_n_mc,
    ensemble_unimodal_range_epsilon,
    get_mode,
    load_cities,
    sizing_defaults,
)
from src.contracts.settlement_semantics import SettlementSemantics


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_get_mode_is_live_constant_not_env_authority(monkeypatch):
    monkeypatch.setenv("ZEUS_MODE", "legacy_env")

    assert get_mode() == "live"


def test_runtime_state_path_is_code_authoritative(monkeypatch):
    import importlib
    import src.config as config_mod

    monkeypatch.delenv("ZEUS_PRIMARY_ROOT", raising=False)
    reloaded = importlib.reload(config_mod)

    isolated_state = Path(os.environ[reloaded.TEST_STATE_ROOT_ENV]).resolve()
    assert reloaded.runtime_state_path("status_summary.json") == isolated_state / "status_summary.json"
    assert reloaded.PROJECT_ROOT == PROJECT_ROOT
    assert reloaded.RUNTIME_ROOT == PROJECT_ROOT
    assert reloaded.STATE_DIR == isolated_state


def test_runtime_state_path_honors_primary_root_at_import(tmp_path):
    import importlib
    import src.config as config_mod

    old_primary = os.environ.get("ZEUS_PRIMARY_ROOT")
    os.environ["ZEUS_PRIMARY_ROOT"] = str(tmp_path)
    try:
        reloaded = importlib.reload(config_mod)
        assert reloaded.PROJECT_ROOT == PROJECT_ROOT
        assert reloaded.RUNTIME_ROOT == tmp_path.resolve()
        isolated_state = Path(os.environ[reloaded.TEST_STATE_ROOT_ENV]).resolve()
        assert reloaded.STATE_DIR == isolated_state
        assert reloaded.runtime_state_path("status_summary.json") == isolated_state / "status_summary.json"
    finally:
        if old_primary is None:
            os.environ.pop("ZEUS_PRIMARY_ROOT", None)
        else:
            os.environ["ZEUS_PRIMARY_ROOT"] = old_primary
        importlib.reload(config_mod)


def test_runtime_state_path_without_test_marker_uses_safe_primary_root(tmp_path):
    from src.config import TEST_STATE_ROOT_ENV

    # The pure config import runs outside pytest but can only resolve state
    # under this temporary primary root. Never disable the parent's isolation.
    env = dict(os.environ)
    env.pop(TEST_STATE_ROOT_ENV, None)
    env["ZEUS_PRIMARY_ROOT"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", (
            "import json; from src.config import RUNTIME_ROOT, STATE_DIR, runtime_state_path; "
            "print(json.dumps([str(RUNTIME_ROOT), str(STATE_DIR), "
            "str(runtime_state_path('status_summary.json'))]))"
        )],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == [
        str(tmp_path.resolve()), str(tmp_path.resolve() / "state"),
        str(tmp_path.resolve() / "state" / "status_summary.json"),
    ]


def test_settings_mode_key_is_legacy_optional(tmp_path):
    data = json.loads((PROJECT_ROOT / "config/settings.json").read_text())
    data.pop("mode", None)
    path = tmp_path / "settings-no-mode.json"
    path.write_text(json.dumps(data))

    assert Settings(path=path).mode == "live"


def test_settings_missing_key_raises():
    # 2026-05-04: fixed config-bankroll authority was removed from required
    # keys; use a still-required key to prove missing required sections fail.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"discovery": {}}, f)
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
    assert len(cities) == 54
    names = {c.name for c in cities}
    assert "NYC" in names
    assert "London" in names
    assert "Paris" in names
    assert "Seoul" in names
    assert "Austin" in names
    assert "Qingdao" in names


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


def test_city_weighted_low_calibration_eligibility_is_explicit():
    cities = load_cities()
    by_name = {c.name: c for c in cities}
    opt_out = {
        "Jakarta",
        "Busan",
        "Hong Kong",
        "NYC",
        "Houston",
        "Chicago",
        "Guangzhou",
        "Beijing",
        "Jinan",
        "Zhengzhou",
    }

    false_cities = {
        name
        for name, city in by_name.items()
        if not city.weighted_low_calibration_eligible
    }
    assert false_cities == opt_out
    assert all(isinstance(city.weighted_low_calibration_eligible, bool) for city in cities)


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


def test_city_without_explicit_settlement_source_type_is_rejected(tmp_path):
    path = tmp_path / "cities.json"
    path.write_text(json.dumps({
        "cities": [
            {
                "name": "Unknown City",
                "lat": 1,
                "lon": 2,
                "timezone": "UTC",
                "unit": "F",
                "cluster": "test",
                "wu_station": "KUNK",
                "country_code": "US",
                "weighted_low_calibration_eligible": True,
            }
        ]
    }))
    with pytest.raises(KeyError, match="Unknown City.*settlement_source_type"):
        load_cities(path=path)


def test_calibration_manager_cluster_taxonomy_matches_config():
    manager_source = (PROJECT_ROOT / "src/calibration/manager.py").read_text()
    refit_source = (PROJECT_ROOT / "scripts/refit_platt.py").read_text()

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


def test_batch_calibration_rebuild_n_mc_is_separate_from_runtime_n_mc():
    assert calibration_batch_rebuild_n_mc() == 1000
    assert 100 <= calibration_batch_rebuild_n_mc() <= 2000
    assert calibration_batch_rebuild_n_mc() < ensemble_n_mc()


def test_day0_constants_are_single_sourced_from_settings():
    from src.signal.day0_signal import Day0Signal
    from src.types.metric_identity import HIGH_LOCALDAY_MAX
    import numpy as np

    # P4-fix1 (post-review BLOCKER from code-reviewer, 2026-04-26):
    # Day0Signal hardened to require explicit MetricIdentity (no default).
    # Pre-fix1 the test silently relied on the now-removed permissive
    # default and crashed at construction with TypeError; P4-1b preserved
    # the broken call. Pass HIGH_LOCALDAY_MAX explicitly.
    sig = Day0Signal(
        observed_high_so_far=40.0,
        current_temp=39.0,
        hours_remaining=6.0,
        member_maxes_remaining=np.array([39.0, 40.0, 41.0]),
        temperature_metric=HIGH_LOCALDAY_MAX,
    )
    # 2026-04-29: bumped from 5000 to 10000 per LAW 4 forbidden move 7 (runtime
    # per-trade precision floor). Test pinned to current production value.
    assert day0_n_mc() == 10000
    # Slice P4-1 (PR #19 phase 4 cleanup, 2026-04-26): obs_dominates() and
    # day0_obs_dominates_threshold() removed as dead code (zero callers
    # outside legacy interface). Replaced with continuous observation_weight()
    # check — observation_weight returns a finite float in [0, 1] for any
    # valid Day0Signal.
    weight = sig.observation_weight()
    assert 0.0 <= weight <= 1.0

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

    # Chicago: operational NOAA grid point for O'Hare, NOT downtown (~41.88).
    chi = by_name["Chicago"]
    assert abs(chi.lat - 41.96017) < 0.01

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


def _support_complement_question(question: str) -> str:
    if "68°F or higher" in question:
        return question.replace("68°F or higher", "67°F or below")
    if "20°C or higher" in question:
        return question.replace("20°C or higher", "19°C or below")
    raise AssertionError(f"no support complement for question {question!r}")


def _gamma_temperature_event(
    *,
    title: str,
    slug: str,
    question: str,
    complete_support: bool = False,
    **extra,
):
    markets = [
        {
            "id": "market-city-sanity",
            "conditionId": "condition-city-sanity",
            "question": question,
            "clobTokenIds": json.dumps(["yes-token", "no-token"]),
            "outcomePrices": json.dumps([0.4, 0.6]),
            "closed": False,
            "active": True,
            "acceptingOrders": True,
            "enableOrderBook": True,
        }
    ]
    if complete_support:
        markets.insert(
            0,
            {
                "id": "market-city-sanity-left",
                "conditionId": "condition-city-sanity-left",
                "question": _support_complement_question(question),
                "clobTokenIds": json.dumps([]),
                "outcomePrices": json.dumps([]),
            },
        )
    event = {
        "id": "event-city-sanity",
        "title": title,
        "slug": slug,
        "endDate": "2026-04-13T23:59:00Z",
        "markets": markets,
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
        complete_support=True,
    )

    parsed = _parse_event(event, datetime(2026, 4, 13, tzinfo=timezone.utc), 0.0)

    assert parsed is not None
    assert parsed["city"].name == "Los Angeles"
    executable = [outcome for outcome in parsed["outcomes"] if outcome["executable"]]
    assert executable[0]["range_low"] == pytest.approx(68.0)


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
            complete_support=True,
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
        elif city.settlement_source_type == "hko":
            assert sem.resolution_source == "HKO_HQ"
            assert sem.rounding_rule == "oracle_truncate"
        else:
            # Non-WU sources use source_type prefix
            assert sem.resolution_source == f"{city.settlement_source_type}_{city.wu_station}"


def test_validate_cities_config_no_warnings():
    from src.config import validate_cities_config
    warnings = validate_cities_config()
    assert warnings == [], f"City config validation warnings: {warnings}"


def test_hong_kong_station_reference_does_not_self_attest_ground(tmp_path) -> None:
    from src.config import CONFIG_DIR, runtime_cities_by_name, runtime_station_geometry_for_city

    rows = json.loads((CONFIG_DIR / "station_precise_coords.json").read_text())
    rows["Hong Kong"].pop("station_ground_proof", None)
    registry = tmp_path / "stations.json"
    registry.write_text(json.dumps(rows))
    row = runtime_station_geometry_for_city(runtime_cities_by_name()["Hong Kong"], registry_path=registry)
    assert row["validity_reason"] is None
    assert row["station_id"] == "HKO_HQ"
    assert row["elevation_m"] == 32.0
    assert row["station_surface"] == "UNKNOWN"
    assert row["ground_status"] == "UNPROVEN"
    assert row["ground_elevation_m"] is None
    assert len(row["registry_sha256"]) == 64


def test_coordinate_manifest_identity_excludes_station_audit_only_edits(monkeypatch) -> None:
    import src.config as config
    from src.data.replacement_forecast_source_run_identity import (
        expected_replacement_dependency_identity_by_role,
    )

    original = config.runtime_station_geometry_for_city
    baseline = config.runtime_coordinate_manifest_json()
    hong_kong = next(row for row in json.loads(baseline)["cities"] if row["city"] == "Hong Kong")
    assert set(hong_kong["station_geometry"]) == {
        "station_id", "lat", "lon", "validity_reason",
    }
    expected = {
        metric: expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version
        for metric in ("high", "low")
    }

    with monkeypatch.context() as patcher:
        def changed_audit(city):
            # Editing one unrelated row changes the audit hash of the whole
            # registry, including every other station's helper result.
            station = {**original(city), "registry_sha256": "f" * 64}
            if city.name == "Manila":
                return {**station, "source": "reworded audit citation"}
            return station
        patcher.setattr(config, "runtime_station_geometry_for_city", changed_audit)
        assert config.runtime_coordinate_manifest_json() == baseline
        for metric in ("high", "low"):
            assert expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version == expected[metric]

    for physical in ("lat",):
        with monkeypatch.context() as patcher:
            def changed_physics(city, *, field=physical):
                station = original(city)
                if city.name == "Hong Kong":
                    return {**station, field: float(station[field]) + .001}
                return station
            patcher.setattr(config, "runtime_station_geometry_for_city", changed_physics)
            assert config.runtime_coordinate_manifest_json() != baseline
            for metric in ("high", "low"):
                assert expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version != expected[metric]

    with monkeypatch.context() as patcher:
        patcher.setattr(config, "runtime_station_geometry_for_city", lambda city: {
            **original(city), "elevation_m": 999.0,
            "ground_audit": {"body_sha256": "f" * 64},
        })
        assert config.runtime_coordinate_manifest_json() == baseline


def test_station_geometry_wrong_station_degrades_only_that_city(tmp_path) -> None:
    import json
    from src.config import runtime_cities_by_name, runtime_station_geometry_for_city

    registry = tmp_path / "stations.json"
    registry.write_text(json.dumps({
        "Hong Kong": {
            "station": "LEMD", "lat": "22.3022", "lon": "114.1742",
            "elevation_m": 32, "source": "untrusted",
        }
    }))
    row = runtime_station_geometry_for_city(
        runtime_cities_by_name()["Hong Kong"], registry_path=registry,
    )
    assert row["validity_reason"] == "STATION_REGISTRY_ID_MISMATCH"
    assert row["station_surface"] is None


def _official_hko_registry(tmp_path, monkeypatch):
    """Replay captured official entity bytes; no HTTP or claimed label fixture."""
    import hashlib
    import src.config as config
    body = (config.PROJECT_ROOT / "config/hko_station_metadata.html").read_bytes()
    assert hashlib.sha256(body).hexdigest() == "88e4e04edb57201646035558898ea1a4f235189d634a4cc7c059131483a4af1b"
    artifact = tmp_path / "hko_station_metadata.html"
    artifact.write_bytes(body)
    facts = config._hko_ground_facts(body, "HKO_HQ")
    claim = {**facts, "artifact_ref": "config/hko_station_metadata.html", "body_sha256": hashlib.sha256(body).hexdigest(),
             "checked_at": "2026-09-29T21:23:56Z"}
    original = json.loads((config.CONFIG_DIR / "station_precise_coords.json").read_text())
    original["Hong Kong"]["station_ground_proof"] = claim
    registry = tmp_path / "station_precise_coords.json"
    registry.write_text(json.dumps(original))
    (tmp_path / "cities.json").write_bytes((config.CONFIG_DIR / "cities.json").read_bytes())
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return registry, artifact, original


def test_official_hko_ground_role_is_bytes_bound_and_not_sensor_agl(tmp_path, monkeypatch):
    import hashlib
    import src.config as config
    registry, artifact, rows = _official_hko_registry(tmp_path, monkeypatch)
    city = config.cities_by_name["Hong Kong"]
    real = config.runtime_station_geometry_for_city(city)
    assert real["ground_status"] == "VERIFIED"
    assert real["ground_elevation_m"] == 32
    assert real["ground_facts"]["height_role"] == "ground_msl"
    assert real["ground_facts"]["site_lat"] == pytest.approx(22 + 18 / 60 + 7 / 3600)
    assert real["ground_facts"]["site_lon"] == pytest.approx(114 + 10 / 60 + 27 / 3600)
    assert real["station_surface"] == "UNKNOWN"  # actual model LSM owns surface eligibility
    assert real["lat"] == float(rows["Hong Kong"]["lat"])  # reference coordinates unchanged
    for field, value in (("source_station_id", "HKA"), ("elevation_m", 6), ("height_role", "sensor_agl"), ("body_sha256", "a" * 64), ("artifact_ref", "/etc/passwd"), ("source_kind", "unknown_source")):
        altered = json.loads(json.dumps(rows))
        altered["Hong Kong"]["station_ground_proof"][field] = value
        registry.write_text(json.dumps(altered))
        assert config.runtime_station_geometry_for_city(city)["ground_status"] == "UNPROVEN"
    registry.write_text(json.dumps(rows))
    body = artifact.read_bytes()
    artifact.write_bytes(body.replace(b"Elevation of ground above mean sea-level", b"Elevation of airport reference above mean sea-level"))
    rows["Hong Kong"]["station_ground_proof"]["body_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    registry.write_text(json.dumps(rows))
    assert config.runtime_station_geometry_for_city(city)["ground_status"] == "UNPROVEN"


@pytest.mark.parametrize("mutation", ["wrong_site", "missing_temperature", "duplicate_site", "symlink"])
def test_official_hko_ground_rejects_foreign_or_non_temperature_site(tmp_path, monkeypatch, mutation):
    import hashlib
    import src.config as config
    registry, artifact, rows = _official_hko_registry(tmp_path, monkeypatch)
    body = artifact.read_bytes()
    start = body.index(b'<td class="td1_year_class">Hong Kong Observatory')
    end = body.index(b"</tr>", start)
    site = body[start:end]
    if mutation == "wrong_site":
        body = body.replace(b"Hong Kong Observatory<br>(HKO)", b"Hong Kong International Airport<br>(HKA)", 1)
    elif mutation == "missing_temperature":
        # The second meteorological cell is Temp, not Wind or another quantity.
        altered = site.replace(b'<td class="td1_normal_class">&#10004;</td>', b'<td class="td1_normal_class"></td>', 2)
        body = body[:start] + altered + body[end:]
    elif mutation == "duplicate_site":
        body = body[:end] + b"</tr><tr>" + site + body[end:]
    else:
        other = tmp_path / "other.html"
        other.write_bytes(body)
        artifact.unlink()
        artifact.symlink_to(other)
    if mutation != "symlink":
        artifact.write_bytes(body)
    rows["Hong Kong"]["station_ground_proof"]["body_sha256"] = hashlib.sha256(body).hexdigest()
    registry.write_text(json.dumps(rows))
    geometry = config.runtime_station_geometry_for_city(config.cities_by_name["Hong Kong"])
    assert geometry["validity_reason"] is None  # reference identity survives independent ground loss
    assert geometry["ground_status"] == "UNPROVEN"


def test_official_page_audit_changes_do_not_rotate_same_station_geometry(tmp_path, monkeypatch):
    import hashlib
    import src.config as config
    registry, artifact, rows = _official_hko_registry(tmp_path, monkeypatch)
    baseline = config.runtime_coordinate_manifest_json()
    ground_facts = config.runtime_station_geometry_for_city(config.cities_by_name["Hong Kong"])["ground_facts"]
    artifact.write_bytes(artifact.read_bytes() + b"<!-- unrelated page edit -->")
    rows["Hong Kong"]["station_ground_proof"]["body_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    rows["Hong Kong"]["station_ground_proof"]["checked_at"] = "2026-09-30T00:00:00Z"
    registry.write_text(json.dumps(rows))
    assert config.runtime_coordinate_manifest_json() == baseline
    assert config.runtime_station_geometry_for_city(config.cities_by_name["Hong Kong"])["ground_facts"] == ground_facts
    artifact.write_bytes(artifact.read_bytes().replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1))
    rows["Hong Kong"]["station_ground_proof"]["body_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    rows["Hong Kong"]["station_ground_proof"]["elevation_m"] = 33.0
    registry.write_text(json.dumps(rows))
    assert config.runtime_coordinate_manifest_json() == baseline  # ENS does not use ground height
    assert config.runtime_station_geometry_for_city(config.cities_by_name["Hong Kong"])["ground_facts"] != ground_facts


def test_airport_height_absence_does_not_erase_reference_identity(tmp_path):
    import src.config as config
    rows = json.loads((config.CONFIG_DIR / "station_precise_coords.json").read_text())
    rows["Manila"].pop("elevation_m")
    registry = tmp_path / "station_precise_coords.json"
    registry.write_text(json.dumps(rows))
    geometry = config.runtime_station_geometry_for_city(config.cities_by_name["Manila"], registry_path=registry)
    assert geometry["validity_reason"] is None
    assert geometry["elevation_m"] is None
    assert geometry["ground_status"] == "UNPROVEN"


def _official_kord_registry(tmp_path, monkeypatch):
    """Replay captured current HOMR bytes; airport and barometer stay distinct."""
    import hashlib
    import src.config as config
    body = (config.PROJECT_ROOT / "config/noaa_homr_kord_station.json").read_bytes()
    assert hashlib.sha256(body).hexdigest() == "3c95677db4c091cb4c01a027b7276b7053dbe945c764803f1cd166c7e5a25aac"
    artifact = tmp_path / "noaa_homr_kord_station.json"
    artifact.write_bytes(body)
    facts = config._homr_ground_facts(body, "KORD")
    claim = {**facts, "artifact_ref": "config/noaa_homr_kord_station.json",
             "body_sha256": hashlib.sha256(body).hexdigest(), "checked_at": "2026-09-29T21:50:23Z",
             "query_date": "2026-09-29", "query_url": f"{config.HOMR_GROUND_SOURCE_URL}?qid=ICAO%3AKORD&date=2026-09-29&phrData=false"}
    original = json.loads((config.CONFIG_DIR / "station_precise_coords.json").read_text())
    original["Chicago"]["station_ground_proof"] = claim
    registry = tmp_path / "station_precise_coords.json"
    registry.write_text(json.dumps(original))
    (tmp_path / "cities.json").write_bytes((config.CONFIG_DIR / "cities.json").read_bytes())
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return registry, artifact, original


def test_official_kord_ground_is_primary_temperature_dcp_not_airport_reference(tmp_path, monkeypatch):
    import src.config as config
    registry, artifact, rows = _official_kord_registry(tmp_path, monkeypatch)
    city = config.cities_by_name["Chicago"]
    ground = config.runtime_station_geometry_for_city(city)
    assert ground["ground_status"] == "VERIFIED"
    assert ground["ground_elevation_m"] == 204.8
    assert ground["ground_facts"]["site_lat"] == 41.96017
    assert ground["ground_facts"]["site_lon"] == -87.93164
    assert ground["ground_facts"]["location_role"] == "primary_temperature_dcp"
    assert ground["lat"] == float(rows["Chicago"]["lat"]) == 41.9786
    assert ground["lon"] == float(rows["Chicago"]["lon"]) == -87.9048
    assert ground["elevation_m"] == float(rows["Chicago"]["elevation_m"]) == 207.3
    # The old reference height is retained, not certified as HOMR airport 203.3
    # or temperature-site ground 204.8.
    assert ground["station_surface"] == "UNKNOWN"
    assert abs(city.lon - ground["ground_facts"]["site_lon"]) == pytest.approx(.00003)
    assert config.runtime_station_geometry_for_city(config.cities_by_name["Manila"])["ground_status"] == "UNPROVEN"
    for field, value in (("station_id", "ZSSS"), ("height_role", "airport_msl"),
                         ("elevation_m", 203.3), ("source_kind", "unapproved_homr"),
                         ("artifact_ref", "/etc/passwd"), ("query_date", "2026-09-28")):
        changed = json.loads(json.dumps(rows))
        changed["Chicago"]["station_ground_proof"][field] = value
        registry.write_text(json.dumps(changed))
        assert config.runtime_station_geometry_for_city(city)["ground_status"] == "UNPROVEN"


@pytest.mark.parametrize("mutation", [
    "foreign", "multiple", "duplicate_id", "airport", "barometric", "unknown", "duplicate_ground",
    "missing_ground", "invalid_units", "nonfinite", "multiple_coordinates", "header_mismatch",
    "not_primary_dcp", "not_temperature", "not_current", "malformed_shape", "symlink",
])
def test_official_kord_rejects_ambiguous_or_non_ground_snapshot(tmp_path, monkeypatch, mutation):
    import hashlib
    import src.config as config
    registry, artifact, rows = _official_kord_registry(tmp_path, monkeypatch)
    payload = json.loads(artifact.read_bytes())
    stations = payload["stationCollection"]["stations"]
    station = stations[0]
    location = station["location"]
    if mutation == "foreign":
        next(row for row in station["identifiers"] if row["idType"] == "ICAO")["id"] = "ZSSS"
    elif mutation == "multiple":
        stations.append(json.loads(json.dumps(station)))
    elif mutation == "duplicate_id":
        station["identifiers"].append({"idType": "ICAO", "id": "KORD"})
    elif mutation in {"airport", "barometric", "unknown"}:
        location["elevations"][0]["elevationType"] = mutation.upper()
    elif mutation == "duplicate_ground":
        location["elevations"].append(dict(location["elevations"][0]))
    elif mutation == "missing_ground":
        location["elevations"].pop(0)
    elif mutation == "invalid_units":
        location["elevations"][0]["elevationMeters"] = "672"
    elif mutation == "nonfinite":
        location["elevations"][0]["elevationMeters"] = "nan"
    elif mutation == "multiple_coordinates":
        location["latLonPairs"].append(dict(location["latLonPairs"][0]))
    elif mutation == "header_mismatch":
        station["header"]["latitude_dec"] = "41.9786"
    elif mutation == "not_primary_dcp":
        station["remarks"] = []
    elif mutation == "not_temperature":
        station["platforms"] = [{"platform": "COOP"}]
    elif mutation == "not_current":
        station["header"]["por"]["endDate"] = "2023-09-15"
    elif mutation == "malformed_shape":
        payload["stationCollection"]["definitions"] = ["GROUND"]
    else:
        other = tmp_path / "elsewhere.json"
        other.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(other)
    if mutation != "symlink":
        artifact.write_bytes(json.dumps(payload).encode())
    rows["Chicago"]["station_ground_proof"]["body_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    registry.write_text(json.dumps(rows))
    geometry = config.runtime_station_geometry_for_city(config.cities_by_name["Chicago"])
    assert geometry["validity_reason"] is None
    assert geometry["ground_status"] == "UNPROVEN"


def test_official_kord_audit_is_not_stable_ground_or_ens_identity(tmp_path, monkeypatch):
    import hashlib
    import src.config as config
    registry, artifact, rows = _official_kord_registry(tmp_path, monkeypatch)
    city = config.cities_by_name["Chicago"]
    before = config.runtime_station_geometry_for_city(city)
    manifest = config.runtime_coordinate_manifest_json()
    artifact.write_bytes(artifact.read_bytes() + b"\n")
    claim = rows["Chicago"]["station_ground_proof"]
    claim.update(body_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), checked_at="2026-09-30T00:00:00Z",
                 query_date="2026-09-30", query_url=f"{config.HOMR_GROUND_SOURCE_URL}?qid=ICAO%3AKORD&date=2026-09-30&phrData=false")
    registry.write_text(json.dumps(rows))
    after = config.runtime_station_geometry_for_city(city)
    assert after["ground_status"] == "VERIFIED"
    assert after["ground_facts"] == before["ground_facts"]
    assert after["ground_audit"] != before["ground_audit"]
    assert config.runtime_coordinate_manifest_json() == manifest


@pytest.mark.parametrize("feet,metres,verified", [
    (True, .3048, False), (1 / .3048, True, False),
    (0, 0, True), (-1, -.3048, True), (672, 204.8, True), ("672", "204.8", True),
    ("481", "146.5", True), ("484", "147.6", True), ("97", "29.7", True),
    ("5", "1.4", True), ("10", "3.2", True),
    ("0", "0.2", True), ("0", "-0.2", True),
    ("0", "0.3", False), ("0", "-0.3", False),
    ("1.0", "0.33", False), ("nan", "0", False), ("0", "inf", False),
])
def test_official_kord_ground_measurements_reject_booleans_not_zero_or_negative(
    tmp_path, monkeypatch, feet, metres, verified,
):
    import hashlib
    import src.config as config
    registry, artifact, rows = _official_kord_registry(tmp_path, monkeypatch)
    payload = json.loads(artifact.read_bytes())
    elevation = payload["stationCollection"]["stations"][0]["location"]["elevations"][0]
    elevation.update(elevationFeet=feet, elevationMeters=metres)
    artifact.write_bytes(json.dumps(payload).encode())
    # Rebind both quantity/unit values and claimed facts: rejection cannot be
    # credited to the old 204.8 claim differing from the malformed source.
    rows["Chicago"]["station_ground_proof"].update(
        body_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), elevation_m=float(metres),
    )
    registry.write_text(json.dumps(rows))
    ground = config.runtime_station_geometry_for_city(config.cities_by_name["Chicago"])
    assert ground["ground_status"] == ("VERIFIED" if verified else "UNPROVEN")
    if verified:
        assert ground["ground_elevation_m"] == float(metres)


def test_pure_ground_parser_uses_frozen_bytes_not_current_file_or_publication_label(tmp_path, monkeypatch):
    import src.config as config
    _, artifact, _ = _official_kord_registry(tmp_path, monkeypatch)
    frozen = artifact.read_bytes()
    kwargs = {"source_kind": "noaa_homr_primary_dcp_snapshot_v1", "station_id": "KORD"}
    facts = config.station_ground_facts_from_bytes(**kwargs, raw_body=frozen)
    assert facts["elevation_m"] == 204.8
    artifact.write_bytes(b"unavailable latest file")
    assert config.station_ground_facts_from_bytes(**kwargs, raw_body=frozen) == facts
    payload = json.loads(frozen)
    station = payload["stationCollection"]["stations"][0]
    station["platforms"] = [row for row in station["platforms"] if row["platform"] != "PLCD"]
    assert config.station_ground_facts_from_bytes(**kwargs, raw_body=json.dumps(payload).encode()) == facts
    station["remarks"] = []
    assert config.station_ground_facts_from_bytes(**kwargs, raw_body=json.dumps(payload).encode()) is None
    for kind, station_id, raw in (("unknown", "KORD", frozen),
                                  (kwargs["source_kind"], "KATL", frozen),
                                  (kwargs["source_kind"], "ZSSS", frozen),
                                  (kwargs["source_kind"], "KORD", b"{}")):
        assert config.station_ground_facts_from_bytes(source_kind=kind, station_id=station_id, raw_body=raw) is None
    assert config.station_ground_source_artifact_ref(**kwargs) == "config/noaa_homr_kord_station.json"
    assert config.station_ground_source_artifact_ref(source_kind=kwargs["source_kind"], station_id="KATL") == "config/noaa_homr_katl_station.json"
    assert config.station_ground_source_artifact_ref(source_kind="hko_station_table_v1", station_id="KORD") is None


_US_GROUND_ENTITIES = (
    ("Atlanta", "KATL", 308.2, "19e0b72444a86e5862100dc15dad4e36a02de3e6c82c8d53274f17bdadda3819"),
    ("Austin", "KAUS", 146.5, "d33c148d61ef0834d0a0e0d0eafbc4789d9786c966cfbdeb09d3fe14485197d3"),
    ("Dallas", "KDAL", 147.6, "48145615d6799f04b1aa2aa899c8799085f6a3c9a5d018260ff10c50a22f3db3"),
    ("Houston", "KHOU", 13.2, "fafc9580cddd11e3d2432308ae863653dada9734ec834748cc38832b83bfbd57"),
    ("Los Angeles", "KLAX", 29.7, "359eda39ea6bd315e0201a678d51b6a3a7403ab41fe91d8d7f205be1ee64c8c8"),
    ("Miami", "KMIA", 1.4, "44367b7c019b8a2bb2d81fe5c2d6f6cee5aa4ddf188be7b7724c617d211716f6"),
    ("NYC", "KLGA", 3.0, "739196cc9251325880f41eab280047a6cd3d0c3fa427d7ebc3841f79db495f6a"),
    ("San Francisco", "KSFO", 3.2, "f88da267cd77d902455a5ded817ee6237dcb5a06d8dc93a4843add1857009fb2"),
    ("Seattle", "KSEA", 112.5, "3691641e6b31385561f43826872c1b49101adc57020e8911455ea1736ac42c2e"),
)


def _official_us_ground_registry(tmp_path, monkeypatch, city_name):
    import hashlib
    import src.config as config
    _, station_id, height, expected_hash = next(row for row in _US_GROUND_ENTITIES if row[0] == city_name)
    artifact_ref = config.station_ground_source_artifact_ref(
        source_kind="noaa_homr_primary_dcp_snapshot_v1", station_id=station_id,
    )
    raw = (config.PROJECT_ROOT / artifact_ref).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected_hash
    facts = config.station_ground_facts_from_bytes(
        source_kind="noaa_homr_primary_dcp_snapshot_v1", station_id=station_id, raw_body=raw,
    )
    assert facts["elevation_m"] == height
    artifact = tmp_path / Path(artifact_ref).name
    artifact.write_bytes(raw)
    rows = json.loads((config.CONFIG_DIR / "station_precise_coords.json").read_text())
    rows[city_name]["station_ground_proof"] = {
        **facts, "artifact_ref": artifact_ref, "body_sha256": expected_hash,
        "checked_at": "2026-09-29T23:30:00Z", "query_date": "2026-09-29",
        "query_url": f"{config.HOMR_GROUND_SOURCE_URL}?current=true&qid=ICAO%3A{station_id}&date=2026-09-29&phrData=false",
    }
    registry = tmp_path / "station_precise_coords.json"
    registry.write_text(json.dumps(rows))
    (tmp_path / "cities.json").write_bytes((config.CONFIG_DIR / "cities.json").read_bytes())
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return registry, artifact, rows


@pytest.mark.parametrize("city_name,station_id,height,expected_hash", _US_GROUND_ENTITIES)
def test_current_us_ground_entities_are_independent_not_kord_fallback(
    tmp_path, monkeypatch, city_name, station_id, height, expected_hash,
):
    import src.config as config
    registry, artifact, rows = _official_us_ground_registry(tmp_path, monkeypatch, city_name)
    city = config.cities_by_name[city_name]
    geometry = config.runtime_station_geometry_for_city(city)
    assert geometry["ground_status"] == "VERIFIED"
    assert geometry["ground_facts"]["station_id"] == station_id
    assert geometry["ground_elevation_m"] == height
    assert geometry["station_surface"] == "UNKNOWN"
    assert geometry["lat"] == float(rows[city_name]["lat"])
    assert geometry["ground_audit"]["body_sha256"] == expected_hash
    assert config.station_ground_facts_from_bytes(
        source_kind="noaa_homr_primary_dcp_snapshot_v1", station_id="KORD", raw_body=artifact.read_bytes(),
    ) is None
    assert config.runtime_station_geometry_for_city(config.cities_by_name["Denver"])["ground_status"] == "UNPROVEN"
    rows[city_name]["station_ground_proof"]["artifact_ref"] = "config/noaa_homr_kord_station.json"
    registry.write_text(json.dumps(rows))
    assert config.runtime_station_geometry_for_city(city)["ground_status"] == "UNPROVEN"
