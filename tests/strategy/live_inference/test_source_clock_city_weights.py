# Created: 2026-06-25
# Last reused/audited: 2026-07-17

import hashlib
import json
from pathlib import Path

from src.strategy.live_inference.source_clock_city_weights import (
    DEFAULT_CITY_ONE_SCHEME_PATH,
    affected_cities_for_source_updates,
    fixed_weight_center_from_values,
    load_city_one_schemes,
    scheme_for_city,
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


def test_fixed_weight_center_renormalizes_over_present_sources(tmp_path: Path) -> None:
    # Incident 2026-07-13/14: a missing configured source (kma_ldps, weight
    # 0.25) must be omitted and the remaining weight renormalized, never
    # collapse the whole center to None. Consult verdict P2-C: basket
    # membership is never a readiness requirement.
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


def test_fixed_weight_center_refuses_below_present_weight_floor(tmp_path: Path) -> None:
    # Present configured weight sum for ecmwf_ifs alone is 0.1 < the 0.25
    # floor (i.e. >75% of the fitted basket is absent) -> fail closed. A
    # center from a sliver of the basket is not the fitted estimator.
    path = tmp_path / "city_one_scheme_final.csv"
    path.write_text(
        "city,scheme_status,final_sources,final_weighted_sources,sample_n,walkforward_pass,one_scheme_status\n"
        "Seoul,SOURCE_SELECTOR_FIT,ecmwf_ifs+kma_ldps,ecmwf_ifs:0.1+kma_ldps:0.9,200,True,FINAL_ONE_SCHEME_PASS\n",
        encoding="utf-8",
    )

    center = fixed_weight_center_from_values(
        city="Seoul",
        values_c_by_source={"ecmwf_ifs": 20.0},
        path=path,
    )

    assert center is None


def test_fixed_weight_center_full_basket_matches_prior_weights(tmp_path: Path) -> None:
    # Regression: a full basket must serve byte-identical weights to before
    # this change (renormalization must not touch the complete-basket path).
    path = tmp_path / "city_one_scheme_final.csv"
    path.write_text(
        "city,scheme_status,final_sources,final_weighted_sources,sample_n,walkforward_pass,one_scheme_status\n"
        "Seoul,SOURCE_SELECTOR_FIT,ecmwf_ifs+kma_ldps,ecmwf_ifs:0.75+kma_ldps:0.25,200,True,FINAL_ONE_SCHEME_PASS\n",
        encoding="utf-8",
    )

    center = fixed_weight_center_from_values(
        city="Seoul",
        values_c_by_source={"ecmwf_ifs": 20.0, "kma_ldps": 10.0},
        path=path,
    )

    assert center is not None
    assert center.missing_sources == ()
    assert center.renormalized is False
    assert center.used_weights == {"ecmwf_ifs": 0.75, "kma_ldps": 0.25}
    assert center.mu_c == 20.0 * 0.75 + 10.0 * 0.25


def test_affected_cities_follow_updated_sources(tmp_path: Path) -> None:
    path = tmp_path / "city_one_scheme_final.csv"
    path.write_text(
        "city,scheme_status,final_sources,final_weighted_sources,sample_n,walkforward_pass,one_scheme_status\n"
        "Seoul,SOURCE_SELECTOR_FIT,ecmwf_ifs+kma_ldps,ecmwf_ifs:0.75+kma_ldps:0.25,200,True,FINAL_ONE_SCHEME_PASS\n"
        "Paris,SOURCE_SELECTOR_FIT,ecmwf_ifs+meteofrance_arome_france_hd,ecmwf_ifs:0.4+meteofrance_arome_france_hd:0.6,210,True,FINAL_ONE_SCHEME_PASS\n",
        encoding="utf-8",
    )

    assert affected_cities_for_source_updates(["kma_ldps"], path=path) == ("Seoul",)


def _write_frozen_csv(tmp_path: Path, *, city: str = "Seoul") -> Path:
    path = tmp_path / "city_one_scheme_grid_aware.csv"
    path.write_text(
        "city,selection_status,grid_aware_sources,grid_aware_weighted_sources,"
        "grid_aware_max_distance_km,old_weighted_sources,old_positive_sources,"
        "changed_vs_old,candidate_count,eligible_live_grid_cap10_count,"
        "eligible_grid_cap10_count,reason\n"
        f"{city},GRID_CAP10_LIVE_READY,ecmwf_ifs+icon_eu,"
        "ecmwf_ifs:0.5+icon_eu:0.5,4.0,,,\n",
        encoding="utf-8",
    )
    return path


def _write_artifact(
    tmp_path: Path,
    *,
    city: str,
    weights: dict[str, float],
    low_weights: dict[str, float] | None = None,
) -> Path:
    artifact_dir = tmp_path / "source_clock_weights"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    provenance = {
        "n_paired_dates": 100,
        "mae_basket": 0.5,
        "mae_vs_frozen_delta": -0.1,
        "region_fallback": False,
        "tier": "CITY_SPECIFIC",
    }
    buckets: dict[str, dict] = {
        "high": {"models": weights, "basket_provenance": provenance}
    }
    if low_weights is not None:
        buckets["low"] = {"models": low_weights, "basket_provenance": provenance}
    artifact = {
        "schema_version": 1,
        "as_of": "2026-07-16",
        "generated_at": "TEST",
        "git_sha": "TEST",
        "settlement_rows_used": 1,
        "cities": {city: buckets},
    }
    payload = json.dumps(artifact, sort_keys=True) + "\n"
    (artifact_dir / "city_weights_20260716.json").write_text(payload, encoding="utf-8")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    pointer = {"artifact": "city_weights_20260716.json", "sha256": sha}
    (artifact_dir / "ACTIVE.json").write_text(json.dumps(pointer), encoding="utf-8")
    return artifact_dir


def test_scheme_for_city_prefers_artifact_over_frozen_csv(tmp_path, monkeypatch) -> None:
    import src.strategy.live_inference.source_clock_city_weights as m

    csv_path = _write_frozen_csv(tmp_path, city="Seoul")
    artifact_dir = _write_artifact(tmp_path, city="Seoul", weights={"jma_seamless": 1.0})
    monkeypatch.setattr(m, "DEFAULT_CITY_ONE_SCHEME_PATH", csv_path)
    monkeypatch.setattr(m, "DEFAULT_SOURCE_CLOCK_ARTIFACT_DIR", artifact_dir)
    m._load_active_artifact.cache_clear()
    m.load_city_one_schemes.cache_clear()
    monkeypatch.delenv(m.ENV_CITY_ONE_SCHEME_PATH, raising=False)
    monkeypatch.delenv(m.ENV_SOURCE_CLOCK_ARTIFACT_DIR, raising=False)

    scheme = scheme_for_city("Seoul")

    assert scheme is not None
    assert scheme.scheme_status == "SOURCE_CLOCK_ARTIFACT"
    assert scheme.weights == {"jma_seamless": 1.0}


def test_scheme_for_city_metric_selects_low_bucket(tmp_path, monkeypatch) -> None:
    """metric="low" reads the artifact's low bucket; metric-less callers keep "high"."""
    import src.strategy.live_inference.source_clock_city_weights as m

    csv_path = _write_frozen_csv(tmp_path, city="Seoul")
    artifact_dir = _write_artifact(
        tmp_path,
        city="Seoul",
        weights={"jma_seamless": 1.0},
        low_weights={"ukmo_global_deterministic_10km": 1.0},
    )
    monkeypatch.setattr(m, "DEFAULT_CITY_ONE_SCHEME_PATH", csv_path)
    monkeypatch.setattr(m, "DEFAULT_SOURCE_CLOCK_ARTIFACT_DIR", artifact_dir)
    m._load_active_artifact.cache_clear()
    m.load_city_one_schemes.cache_clear()
    monkeypatch.delenv(m.ENV_CITY_ONE_SCHEME_PATH, raising=False)
    monkeypatch.delenv(m.ENV_SOURCE_CLOCK_ARTIFACT_DIR, raising=False)

    low = scheme_for_city("Seoul", metric="low")
    assert low is not None
    assert low.weights == {"ukmo_global_deterministic_10km": 1.0}

    default = scheme_for_city("Seoul")
    assert default is not None
    assert default.weights == {"jma_seamless": 1.0}

    center = m.fixed_weight_center_from_values(
        city="Seoul",
        values_c_by_source={"ukmo_global_deterministic_10km": 21.5},
        metric="low",
    )
    assert center is not None
    assert center.mu_c == 21.5
    assert center.used_weights == {"ukmo_global_deterministic_10km": 1.0}


def test_scheme_for_city_metric_miss_falls_back_to_csv(tmp_path, monkeypatch) -> None:
    """A city whose artifact entry lacks the requested metric bucket falls to the CSV."""
    import src.strategy.live_inference.source_clock_city_weights as m

    csv_path = _write_frozen_csv(tmp_path, city="Seoul")
    artifact_dir = _write_artifact(tmp_path, city="Seoul", weights={"jma_seamless": 1.0})
    monkeypatch.setattr(m, "DEFAULT_CITY_ONE_SCHEME_PATH", csv_path)
    monkeypatch.setattr(m, "DEFAULT_SOURCE_CLOCK_ARTIFACT_DIR", artifact_dir)
    m._load_active_artifact.cache_clear()
    m.load_city_one_schemes.cache_clear()
    monkeypatch.delenv(m.ENV_CITY_ONE_SCHEME_PATH, raising=False)
    monkeypatch.delenv(m.ENV_SOURCE_CLOCK_ARTIFACT_DIR, raising=False)

    scheme = scheme_for_city("Seoul", metric="low")
    assert scheme is not None
    assert scheme.scheme_status == "GRID_CAP10_LIVE_READY"


def test_scheme_for_city_falls_back_to_csv_when_city_absent_from_artifact(
    tmp_path, monkeypatch
) -> None:
    import src.strategy.live_inference.source_clock_city_weights as m

    csv_path = _write_frozen_csv(tmp_path, city="Tokyo")
    artifact_dir = _write_artifact(tmp_path, city="Seoul", weights={"jma_seamless": 1.0})
    monkeypatch.setattr(m, "DEFAULT_CITY_ONE_SCHEME_PATH", csv_path)
    monkeypatch.setattr(m, "DEFAULT_SOURCE_CLOCK_ARTIFACT_DIR", artifact_dir)
    m._load_active_artifact.cache_clear()
    m.load_city_one_schemes.cache_clear()
    monkeypatch.delenv(m.ENV_CITY_ONE_SCHEME_PATH, raising=False)
    monkeypatch.delenv(m.ENV_SOURCE_CLOCK_ARTIFACT_DIR, raising=False)

    scheme = scheme_for_city("Tokyo")

    assert scheme is not None
    assert scheme.scheme_status == "GRID_CAP10_LIVE_READY"
    assert scheme.weights == {"ecmwf_ifs": 0.5, "icon_eu": 0.5}


def test_scheme_for_city_explicit_path_bypasses_artifact(tmp_path, monkeypatch) -> None:
    import src.strategy.live_inference.source_clock_city_weights as m

    csv_path = _write_frozen_csv(tmp_path, city="Seoul")
    artifact_dir = _write_artifact(tmp_path, city="Seoul", weights={"jma_seamless": 1.0})
    monkeypatch.setattr(m, "DEFAULT_SOURCE_CLOCK_ARTIFACT_DIR", artifact_dir)
    m._load_active_artifact.cache_clear()

    scheme = scheme_for_city("Seoul", path=csv_path)

    assert scheme is not None
    assert scheme.scheme_status == "GRID_CAP10_LIVE_READY"
    assert scheme.weights == {"ecmwf_ifs": 0.5, "icon_eu": 0.5}
