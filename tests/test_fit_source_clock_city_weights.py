#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/evidence/upstream_physical_2026_07_17/consult_freshness_decoupling_verdict.txt
#   (basket-governance data-availability tiers >=60/30-59/<30); docs/evidence/
#   upstream_physical_2026_07_17/combo_experiments_report.md (walk-forward no-leak discipline).
"""Unit tests for scripts/fit_source_clock_city_weights.py — no live DB required.

Covers: (1) weight math parity vs src.forecast.center.raw_second_moment_weights (imported,
not reimplemented); (2) determinism (two runs of the same DB state + as_of are byte-
identical); (3) the walk-forward boundary (a settlement dated exactly on as_of must never
be used to train); (4) the >=60 / 30-59 / <30 paired-date data-availability tiers select
CITY_SPECIFIC / REGION_POOLED / GLOBAL_CORE respectively.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import fit_source_clock_city_weights as fscw  # noqa: E402

from src.forecast.center import raw_second_moment_weights  # noqa: E402


def _make_db(rows: list[dict]) -> sqlite3.Connection:
    """rows: each dict has model, city, metric, target_date, lead_days, forecast_value_c,
    settlement_value_c (settlement is always inserted as unit='C' — F-conversion is exercised
    by inserting a raw settlement_value + unit='F' explicitly where needed)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_model_forecasts (model TEXT, city TEXT, target_date TEXT, "
        "metric TEXT, lead_days INTEGER, forecast_value_c REAL, endpoint TEXT)"
    )
    conn.execute(
        "CREATE TABLE settlement_outcomes (city TEXT, target_date TEXT, temperature_metric TEXT, "
        "settlement_value REAL, settlement_unit TEXT, authority TEXT)"
    )
    seen_settlements: set[tuple[str, str, str]] = set()
    for r in rows:
        conn.execute(
            "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?)",
            (
                r["model"], r["city"], r["target_date"], r["metric"],
                r["lead_days"], r["forecast_value_c"], r.get("endpoint", "previous_runs"),
            ),
        )
        key = (r["city"], r["target_date"], r["metric"])
        if key not in seen_settlements:
            seen_settlements.add(key)
            unit = r.get("settlement_unit", "C")
            value = r.get("settlement_value", r.get("settlement_value_c"))
            conn.execute(
                "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
                (r["city"], r["target_date"], r["metric"], value, unit,
                 r.get("authority", "VERIFIED")),
            )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _rows_for_city(city: str, metric: str, n: int, *, models: dict[str, float]) -> list[dict]:
    """``models`` maps model id -> constant residual offset (forecast - settlement, degC).
    Produces ``n`` distinct settled target_dates, one 2026 calendar day apart, with lead_days=1
    for every row (a single archived lead; exact-lead dedup is tested separately)."""
    rows = []
    for i in range(n):
        month = 1 + (i // 28)
        day = 1 + (i % 28)
        date = f"2026-{month:02d}-{day:02d}"
        settle_c = 10.0 + (i % 7) * 0.4
        for model, offset in models.items():
            rows.append({
                "model": model, "city": city, "metric": metric, "target_date": date,
                "lead_days": 1, "forecast_value_c": settle_c + offset,
                "settlement_value_c": settle_c,
            })
    return rows


def _cities_json(tmp_path: Path, cities: list[dict]) -> Path:
    path = tmp_path / "cities.json"
    path.write_text(json.dumps({"cities": cities}), encoding="utf-8")
    return path


def test_weight_math_matches_center_raw_second_moment_weights() -> None:
    """The generator's per-model raw second moment feeds src.forecast.center's OWN weight
    formula unmodified — this is a parity check against reimplementation drift."""
    rows = _rows_for_city("TestCity", "high", 40, models={"A": 0.5, "B": 2.0})
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31")
    obs = loaded["obs"][("TestCity", "high")]
    settle = loaded["settle"][("TestCity", "high")]
    stats = fscw.residual_stats_by_model(obs, settle)

    # Known-closed-form raw second moments: constant residual +0.5 => m2=0.25; +2.0 => m2=4.0.
    assert stats["A"][0] == 0.25 and stats["A"][1] == 40
    assert stats["B"][0] == 4.0 and stats["B"][1] == 40

    got = raw_second_moment_weights(stats, unit="C")
    expected = raw_second_moment_weights({"A": (0.25, 40), "B": (4.0, 40)}, unit="C")
    assert got == expected
    assert got["A"] > got["B"]  # the tighter model (smaller m2) gets more weight


def test_exact_lead_prefers_smallest_lead_days() -> None:
    """A (model, city, metric, target_date) cell with archived lead 0 AND lead 2 keeps ONLY
    the lead-0 (smallest available) row — never both, never the larger lead."""
    rows = [
        {"model": "A", "city": "C1", "metric": "high", "target_date": "2026-03-01",
         "lead_days": 2, "forecast_value_c": 99.0, "settlement_value_c": 10.0},
        {"model": "A", "city": "C1", "metric": "high", "target_date": "2026-03-01",
         "lead_days": 0, "forecast_value_c": 10.5, "settlement_value_c": 10.0},
    ]
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31")
    obs = loaded["obs"][("C1", "high")]
    assert obs["2026-03-01"] == {"A": 10.5}  # the lead-0 value, not the lead-2 99.0 outlier


def test_settlement_unit_f_converted_to_celsius() -> None:
    rows = [{
        "model": "A", "city": "F-City", "metric": "high", "target_date": "2026-03-01",
        "lead_days": 1, "forecast_value_c": 20.0, "settlement_value": 68.0,
        "settlement_unit": "F",  # 68F == 20C
    }]
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of="2026-12-31")
    settle = loaded["settle"][("F-City", "high")]
    assert abs(settle["2026-03-01"] - 20.0) < 1e-9


def test_walk_forward_boundary_excludes_as_of_date() -> None:
    """A settlement dated exactly ``as_of`` must never enter training (strict target_date <
    as_of, per docs/authority Time law / no-look-ahead)."""
    rows = _rows_for_city("C1", "high", 5, models={"A": 0.1})
    # add a row settled exactly on the boundary date
    boundary_date = "2026-05-15"
    rows.append({
        "model": "A", "city": "C1", "metric": "high", "target_date": boundary_date,
        "lead_days": 1, "forecast_value_c": 15.0, "settlement_value_c": 15.0,
    })
    conn = _make_db(rows)
    loaded = fscw.load_walk_forward_rows(conn, as_of=boundary_date)
    settle = loaded["settle"][("C1", "high")]
    assert boundary_date not in settle
    assert len(settle) == 5  # only the strictly-prior dates


def _basic_setup(tmp_path: Path, *, n_city_specific=70, n_region=45, n_global=10):
    """Three cities in the same OTHER region: one clears the CITY_SPECIFIC tier (>=60), one
    lands in REGION_POOLED (30-59), one falls to GLOBAL_CORE (<30)."""
    rows = []
    rows += _rows_for_city("CitySpecific", "high", n_city_specific, models={"A": 0.3, "B": 1.0})
    rows += _rows_for_city("RegionCity", "high", n_region, models={"A": 0.4, "B": 0.9})
    rows += _rows_for_city("GlobalCity", "high", n_global, models={"X": 0.2})
    conn = _make_db(rows)
    cities = [
        {"name": "CitySpecific", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0},
        {"name": "RegionCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -17.0, "lon": 177.0},
        {"name": "GlobalCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -16.0, "lon": 176.0},
    ]
    cities_path = _cities_json(tmp_path, cities)
    frozen_csv_path = tmp_path / "nonexistent_frozen.csv"  # absent -> mae_vs_frozen_delta is None
    return conn, cities_path, frozen_csv_path


def test_data_availability_tiers_select_expected_basket_source(tmp_path: Path) -> None:
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)
    artifact = fscw.build_artifact(
        conn, as_of="2026-12-31", generated_at="FIXED", cities_path=cities_path,
        frozen_csv_path=frozen_csv_path, git_sha="FIXED", servable=None,
    )
    cities = artifact["cities"]
    assert cities["CitySpecific"]["high"]["basket_provenance"]["tier"] == "CITY_SPECIFIC"
    assert cities["CitySpecific"]["high"]["basket_provenance"]["region_fallback"] is False
    assert cities["RegionCity"]["high"]["basket_provenance"]["tier"] == "REGION_POOLED"
    assert cities["RegionCity"]["high"]["basket_provenance"]["region_fallback"] is True
    assert cities["GlobalCity"]["high"]["basket_provenance"]["tier"] == "GLOBAL_CORE"
    assert set(cities["GlobalCity"]["high"]["models"]) == set(fscw.GLOBAL_CORE_BASKET)


def test_determinism_same_db_state_and_as_of_byte_identical(tmp_path: Path) -> None:
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)

    def _run() -> str:
        artifact = fscw.build_artifact(
            conn, as_of="2026-12-31", generated_at="FIXED", cities_path=cities_path,
            frozen_csv_path=frozen_csv_path, git_sha="FIXED", servable=None,
        )
        return json.dumps(artifact, sort_keys=True, indent=2)

    first = _run()
    second = _run()
    assert first == second


def test_weights_keyed_by_exact_model_id_never_positional(tmp_path: Path) -> None:
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)
    artifact = fscw.build_artifact(
        conn, as_of="2026-12-31", generated_at="FIXED", cities_path=cities_path,
        frozen_csv_path=frozen_csv_path, git_sha="FIXED", servable=None,
    )
    models = artifact["cities"]["CitySpecific"]["high"]["models"]
    assert set(models) <= {"A", "B"}
    assert all(isinstance(k, str) for k in models)
    assert abs(sum(models.values()) - 1.0) < 1e-6


def test_city_specific_greedy_keeps_second_provider_without_mae_gain(tmp_path: Path) -> None:
    """A dominant first model cannot collapse an entry basket to one provider."""
    rows = _rows_for_city(
        "TwoProviderCity",
        "high",
        70,
        models={"A": 0.01, "B": 4.0},
    )
    rows += _rows_for_city(
        "TwoProviderCity",
        "low",
        70,
        models={"A": 0.01, "B": 4.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "TwoProviderCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0}],
    )

    artifact = fscw.build_artifact(
        conn,
        as_of="2026-12-31",
        generated_at="FIXED",
        cities_path=cities_path,
        frozen_csv_path=tmp_path / "missing.csv",
        git_sha="FIXED",
        servable=frozenset({"A", "B"}),
    )

    models = artifact["cities"]["TwoProviderCity"]["high"]["models"]
    assert set(models) == {"A", "B"}
    assert all(weight > 0.0 for weight in models.values())


def test_artifact_refuses_second_provider_rounded_out_of_publication(tmp_path: Path) -> None:
    """The published positive-weight map, not the pre-rounding basket, is authority."""
    rows = _rows_for_city(
        "RoundedProviderCity",
        "high",
        70,
        models={"A": 0.01, "B": 10_000.0},
    )
    rows += _rows_for_city(
        "RoundedProviderCity",
        "low",
        70,
        models={"A": 0.01, "B": 10_000.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "RoundedProviderCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0}],
    )

    with pytest.raises(ValueError, match=r"sources=\('A',\).*families=\('a',\)"):
        fscw.build_artifact(
            conn,
            as_of="2026-12-31",
            generated_at="FIXED",
            cities_path=cities_path,
            frozen_csv_path=tmp_path / "missing.csv",
            git_sha="FIXED",
            servable=frozenset({"A", "B"}),
        )


def test_artifact_refuses_single_servable_domain_provider(tmp_path: Path) -> None:
    """A one-provider candidate set must fail before any artifact can be published."""
    rows = _rows_for_city("OneProviderCity", "high", 70, models={"A": 0.1})
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "OneProviderCity", "timezone": "Pacific/Fiji", "country_code": "FJ", "lat": -18.0, "lon": 178.0}],
    )

    with pytest.raises(ValueError, match="at least 2 distinct servable/domain provider families"):
        fscw.build_artifact(
            conn,
            as_of="2026-12-31",
            generated_at="FIXED",
            cities_path=cities_path,
            frozen_csv_path=tmp_path / "missing.csv",
            git_sha="FIXED",
            servable=frozenset({"A"}),
        )


def test_greedy_uses_ecmwf_not_second_icon_alias_for_second_family(tmp_path: Path) -> None:
    """Two DWD ICON aliases cannot satisfy the independent-provider requirement."""
    rows = _rows_for_city(
        "Munich",
        "high",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02, "ecmwf_ifs": 4.0},
    )
    rows += _rows_for_city(
        "Munich",
        "low",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02, "ecmwf_ifs": 4.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "Munich", "timezone": "Europe/Berlin", "country_code": "DE", "lat": 48.1351, "lon": 11.5820}],
    )

    artifact = fscw.build_artifact(
        conn,
        as_of="2026-12-31",
        generated_at="FIXED",
        cities_path=cities_path,
        frozen_csv_path=tmp_path / "missing.csv",
        git_sha="FIXED",
        servable=frozenset({"icon_d2", "icon_eu", "ecmwf_ifs"}),
    )

    sources = artifact["cities"]["Munich"]["high"]["models"]
    assert set(sources) == {"icon_d2", "ecmwf_ifs"}
    assert {fscw.provider_family_for_source(source) for source in sources} == {"dwd_icon", "ecmwf"}


def test_artifact_refuses_two_models_from_one_provider_family(tmp_path: Path) -> None:
    """Two source ids from the same DWD ICON family are still one provider."""
    rows = _rows_for_city(
        "Munich",
        "high",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02},
    )
    rows += _rows_for_city(
        "Munich",
        "low",
        70,
        models={"icon_d2": 0.01, "icon_eu": 0.02},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [{"name": "Munich", "timezone": "Europe/Berlin", "country_code": "DE", "lat": 48.1351, "lon": 11.5820}],
    )

    with pytest.raises(ValueError, match=r"sources=\('icon_d2',\).*families=\('dwd_icon',\)"):
        fscw.build_artifact(
            conn,
            as_of="2026-12-31",
            generated_at="FIXED",
            cities_path=cities_path,
            frozen_csv_path=tmp_path / "missing.csv",
            git_sha="FIXED",
            servable=frozenset({"icon_d2", "icon_eu"}),
        )


def test_default_servable_filter_excludes_retired_archive_models(tmp_path: Path) -> None:
    """A retired archive-only model (e.g. gfs_global, dropped from the live fetch
    2026-06-17) must never enter a basket: a basket naming it would be permanently
    unservable at decision time and, if single-model, would blank the city at the
    serving renormalizer's PRESENT_WEIGHT_FLOOR — the incident class this artifact
    exists to prevent."""
    conn, cities_path, frozen_csv_path = _basic_setup(tmp_path)
    loaded = fscw.load_walk_forward_rows(
        conn, as_of="2026-12-31", servable=frozenset({"A"})
    )
    for by_date in loaded["obs"].values():
        for models in by_date.values():
            assert set(models) <= {"A"}
    assert "gfs_global" in ("gfs_global",)  # retired set documented in LIVE_SERVABLE_MODELS comment
    assert "gfs_global" not in fscw.LIVE_SERVABLE_MODELS
    assert "gem_global" not in fscw.LIVE_SERVABLE_MODELS
    assert "jma_seamless" not in fscw.LIVE_SERVABLE_MODELS
    assert "icon_seamless" not in fscw.LIVE_SERVABLE_MODELS
    assert "ecmwf_ifs" in fscw.LIVE_SERVABLE_MODELS


def test_artifact_excludes_models_outside_each_city_domain(tmp_path: Path) -> None:
    """Archived rows cannot license a model the current downloader will never request.

    Cover both city-specific and region-pooled selection: the latter also proves the
    pooled-basket cache is scoped by the target city's physically eligible model set.
    """
    rows = _rows_for_city(
        "Amsterdam",
        "high",
        70,
        models={
            "meteofrance_arome_france_hd": 0.05,
            "ukmo_global_deterministic_10km": 0.8,
            "ecmwf_ifs": 1.0,
        },
    )
    rows += _rows_for_city(
        "Lagos",
        "high",
        45,
        models={"ncep_nbm_conus": 0.05, "ecmwf_ifs": 0.8, "icon_global": 1.0},
    )
    conn = _make_db(rows)
    cities_path = _cities_json(
        tmp_path,
        [
            {
                "name": "Amsterdam",
                "timezone": "Europe/Amsterdam",
                "country_code": "NL",
                "lat": 52.3105,
                "lon": 4.7683,
            },
            {
                "name": "Lagos",
                "timezone": "Africa/Lagos",
                "country_code": "NG",
                "lat": 6.5774,
                "lon": 3.3212,
            },
        ],
    )
    artifact = fscw.build_artifact(
        conn,
        as_of="2026-12-31",
        generated_at="FIXED",
        cities_path=cities_path,
        frozen_csv_path=tmp_path / "missing.csv",
        git_sha="FIXED",
    )

    amsterdam = artifact["cities"]["Amsterdam"]["high"]
    assert amsterdam["basket_provenance"]["tier"] == "CITY_SPECIFIC"
    assert set(amsterdam["models"]) == {"ukmo_global_deterministic_10km", "ecmwf_ifs"}

    lagos = artifact["cities"]["Lagos"]["high"]
    assert lagos["basket_provenance"]["tier"] == "REGION_POOLED"
    assert set(lagos["models"]) == {"ecmwf_ifs", "icon_global"}
