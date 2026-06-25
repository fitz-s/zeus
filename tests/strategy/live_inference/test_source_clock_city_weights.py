# Created: 2026-06-25
# Last reused/audited: 2026-06-25

from pathlib import Path

from src.strategy.live_inference.source_clock_city_weights import (
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


def test_fixed_weight_center_renormalizes_missing_source(tmp_path: Path) -> None:
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
