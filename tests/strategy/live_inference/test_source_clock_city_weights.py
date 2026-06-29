# Created: 2026-06-25
# Last reused/audited: 2026-06-25

from pathlib import Path

from src.strategy.live_inference.source_clock_city_weights import (
    DEFAULT_CITY_ONE_SCHEME_PATH,
    affected_cities_for_source_updates,
    fixed_weight_center_from_values,
    load_city_one_schemes,
)


def test_loads_one_scheme_and_normalizes_weights(tmp_path: Path) -> None:
    path = tmp_path / "city_one_scheme_final.csv"
    path.write_text(
        "city,scheme_status,final_sources,final_weighted_sources,sample_n,walkforward_pass,one_scheme_status\n"
        "Seoul,SOURCE_SELECTOR_FIT,ecmwf_ifs+kma_ldps,ecmwf_ifs:0.75+kma_ldps:0.25,200,True,FINAL_ONE_SCHEME_PASS\n",
        encoding="utf-8",
    )

    schemes = load_city_one_schemes(str(path))

    scheme = schemes["Seoul"]
    assert scheme.final_sources == ("ecmwf_ifs", "kma_ldps")
    assert round(sum(scheme.weights.values()), 12) == 1.0
    assert scheme.walkforward_pass is True


def test_default_source_clock_weights_use_grid_aware_artifact() -> None:
    assert DEFAULT_CITY_ONE_SCHEME_PATH.as_posix().endswith(
        "state/fusion_source_compare/grid_aware_retest_20260625/city_one_scheme_grid_aware.csv"
    )


def test_loads_grid_aware_scheme_schema(tmp_path: Path) -> None:
    path = tmp_path / "city_one_scheme_grid_aware.csv"
    path.write_text(
        "city,selection_status,grid_aware_sources,grid_aware_weighted_sources,"
        "grid_aware_max_distance_km,old_weighted_sources,old_positive_sources,"
        "changed_vs_old,candidate_count,eligible_live_grid_cap10_count,"
        "eligible_grid_cap10_count,reason\n"
        "Seoul,GRID_CAP10_LIVE_READY,ecmwf_ifs+ukmo_global_deterministic_10km,"
        "ecmwf_ifs:0.922+ukmo_global_deterministic_10km:0.078,4.290356,"
        "ecmwf_ifs:0.785+kma_ldps:0.192,ecmwf_ifs+kma_ldps,True,4,2,4,\n"
        "Auckland,NO_SOURCE_POOL,,,,,,0,0,0,no source pool\n",
        encoding="utf-8",
    )

    schemes = load_city_one_schemes(str(path))

    assert tuple(schemes) == ("Seoul",)
    scheme = schemes["Seoul"]
    assert scheme.scheme_status == "GRID_CAP10_LIVE_READY"
    assert scheme.final_sources == ("ecmwf_ifs", "ukmo_global_deterministic_10km")
    assert scheme.weights == {
        "ecmwf_ifs": 0.922,
        "ukmo_global_deterministic_10km": 0.078,
    }
    assert scheme.sample_n == 4
    assert scheme.walkforward_pass is True
    assert scheme.one_scheme_status == "GRID_CAP10_LIVE_READY"


def test_fixed_weight_center_rejects_missing_source_by_default(tmp_path: Path) -> None:
    path = tmp_path / "city_one_scheme_final.csv"
    path.write_text(
        "city,scheme_status,final_sources,final_weighted_sources,sample_n,walkforward_pass,one_scheme_status\n"
        "Seoul,SOURCE_SELECTOR_FIT,ecmwf_ifs+kma_ldps,ecmwf_ifs:0.75+kma_ldps:0.25,200,True,FINAL_ONE_SCHEME_PASS\n",
        encoding="utf-8",
    )

    center = fixed_weight_center_from_values(
        city="Seoul",
        values_c_by_source={"ecmwf_ifs": 20.0},
        path=path,
    )

    assert center is None


def test_fixed_weight_center_allows_incomplete_only_when_requested(tmp_path: Path) -> None:
    path = tmp_path / "city_one_scheme_final.csv"
    path.write_text(
        "city,scheme_status,final_sources,final_weighted_sources,sample_n,walkforward_pass,one_scheme_status\n"
        "Seoul,SOURCE_SELECTOR_FIT,ecmwf_ifs+kma_ldps,ecmwf_ifs:0.75+kma_ldps:0.25,200,True,FINAL_ONE_SCHEME_PASS\n",
        encoding="utf-8",
    )

    center = fixed_weight_center_from_values(
        city="Seoul",
        values_c_by_source={"ecmwf_ifs": 20.0},
        path=path,
        allow_incomplete=True,
    )

    assert center is not None
    assert center.mu_c == 20.0
    assert center.missing_sources == ("kma_ldps",)
    assert center.renormalized is True
    assert center.used_weights == {"ecmwf_ifs": 1.0}


def test_affected_cities_follow_updated_sources(tmp_path: Path) -> None:
    path = tmp_path / "city_one_scheme_final.csv"
    path.write_text(
        "city,scheme_status,final_sources,final_weighted_sources,sample_n,walkforward_pass,one_scheme_status\n"
        "Seoul,SOURCE_SELECTOR_FIT,ecmwf_ifs+kma_ldps,ecmwf_ifs:0.75+kma_ldps:0.25,200,True,FINAL_ONE_SCHEME_PASS\n"
        "Paris,SOURCE_SELECTOR_FIT,ecmwf_ifs+meteofrance_arome_france_hd,ecmwf_ifs:0.4+meteofrance_arome_france_hd:0.6,210,True,FINAL_ONE_SCHEME_PASS\n",
        encoding="utf-8",
    )

    assert affected_cities_for_source_updates(["kma_ldps"], path=path) == ("Seoul",)
