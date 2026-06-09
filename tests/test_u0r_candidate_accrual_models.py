# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: 2026-06-09 uncovered-cities regional survey
#   (/tmp/uncovered_cities_regional_report.md, settlement-graded) + SAME-DAY operator-directed
#   promotion: ukmo_global_deterministic_10km -> 5th decorrelated global (-10.3%, n=1099);
#   ncep_nbm_conus -> NCEP-family CONUS rep (-14.4%, n=1029; NBM blends NCEP models incl. GFS
#   -> family single-rep contest, NEVER alongside gfs_global); ukmo_uk_deterministic_2km ->
#   London regional expert + UKMO-family rep (0.919 vs 1.039, n=112).
"""PROMOTION antibodies: family single-rep correctness for the 2026-06-09 promoted models.

The cross-model invariant (one mechanism, three families): each physical provider family
(ICON, NCEP, UKMO) contributes EXACTLY ONE instrument per fusion, most-specific-eligible-first.
NBM and gfs_global never coexist; ukmo_uk_2km and ukmo_global never coexist; out-of-domain
specifics fall back to the family global."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from src.data.u0r_multimodel_download import (
    U0R_CANDIDATE_ACCRUAL_MODELS,
    U0R_EXTRA_MODELS,
    U0RDownloadTarget,
    _model_in_domain,
    download_u0r_extra_raw_inputs,
)
from src.forecast.model_selection import (
    NBM_MODEL,
    UKMO_GLOBAL_MODEL,
    UKMO_UK_MODEL,
    select_models,
)

ATLANTA = (33.630, -84.442)   # CONUS: NBM domain
LONDON = (51.505, 0.055)      # UK: ukmo_uk domain; also icon_d2 Central-EU box
TOKYO = (35.68, 139.69)       # outside every regional/limited domain
SINGAPORE = (1.368, 103.982)

PRESENT_ALL = {
    "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
    "jma_seamless": 3.8, "icon_eu": 4.1, "icon_d2": 4.2,
    NBM_MODEL: 4.4, UKMO_GLOBAL_MODEL: 4.5, UKMO_UK_MODEL: 4.6,
}


def test_promoted_models_ride_the_extra_download_set() -> None:
    # Promotion moved them from the candidate lane into the selection sets -> they ride
    # U0R_EXTRA_MODELS; the candidate lane MUST be empty (no double-fetch).
    for m in (NBM_MODEL, UKMO_GLOBAL_MODEL, UKMO_UK_MODEL):
        assert m in U0R_EXTRA_MODELS
        assert m not in U0R_CANDIDATE_ACCRUAL_MODELS


def test_conus_nbm_is_the_ncep_rep_and_gfs_is_suppressed() -> None:
    sel = select_models(present_models=PRESENT_ALL, lat=ATLANTA[0], lon=ATLANTA[1], lead_days=1)
    assert NBM_MODEL in sel.used_models
    assert "gfs_global" not in sel.used_models, (
        "NBM blends NCEP models including GFS — they must NEVER coexist in one fusion"
    )
    assert "gfs_global" in sel.dropped_provider_dups
    # UKMO family: uk_2km out-of-domain -> the 10km global is the rep.
    assert UKMO_GLOBAL_MODEL in sel.used_models
    assert UKMO_UK_MODEL not in sel.used_models


def test_outside_conus_gfs_carries_the_ncep_family() -> None:
    for lat, lon in (TOKYO, SINGAPORE, LONDON):
        sel = select_models(present_models=PRESENT_ALL, lat=lat, lon=lon, lead_days=1)
        ncep = [m for m in sel.used_models if m in (NBM_MODEL, "gfs_global")]
        assert ncep == ["gfs_global"], (lat, lon, sel.used_models)
        assert NBM_MODEL in sel.dropped_provider_dups


def test_conus_beyond_nbm_lead_horizon_falls_back_to_gfs() -> None:
    sel = select_models(present_models=PRESENT_ALL, lat=ATLANTA[0], lon=ATLANTA[1], lead_days=5)
    ncep = [m for m in sel.used_models if m in (NBM_MODEL, "gfs_global")]
    assert ncep == ["gfs_global"]


def test_london_uk2km_is_regional_expert_and_ukmo_global_suppressed() -> None:
    sel = select_models(present_models=PRESENT_ALL, lat=LONDON[0], lon=LONDON[1], lead_days=1)
    assert UKMO_UK_MODEL in sel.regional_experts
    assert UKMO_UK_MODEL in sel.used_models
    assert UKMO_GLOBAL_MODEL not in sel.used_models, (
        "UKV 2km and the 10km global are the same Met Office physics — single rep only"
    )
    assert UKMO_GLOBAL_MODEL in sel.dropped_provider_dups
    # ICON family still contests independently (icon_d2 in-box rep for London).
    icon = [m for m in sel.used_models if m in ("icon_d2", "icon_eu", "icon_global")]
    assert icon == ["icon_d2"]


def test_ukmo_global_is_fifth_decorrelated_member_worldwide() -> None:
    for lat, lon in (TOKYO, SINGAPORE):
        sel = select_models(present_models=PRESENT_ALL, lat=lat, lon=lon, lead_days=1)
        assert UKMO_GLOBAL_MODEL in sel.likelihood_globals, (lat, lon, sel.likelihood_globals)
        # the two UKMO instruments never coexist
        assert not ({UKMO_GLOBAL_MODEL, UKMO_UK_MODEL} <= set(sel.used_models))


def test_candidate_domain_gates() -> None:
    assert _model_in_domain(NBM_MODEL, lat=ATLANTA[0], lon=ATLANTA[1], lead_days=1)
    assert not _model_in_domain(NBM_MODEL, lat=TOKYO[0], lon=TOKYO[1], lead_days=1)
    assert not _model_in_domain(NBM_MODEL, lat=LONDON[0], lon=LONDON[1], lead_days=1)
    assert _model_in_domain(UKMO_UK_MODEL, lat=LONDON[0], lon=LONDON[1], lead_days=1)
    assert not _model_in_domain(UKMO_UK_MODEL, lat=ATLANTA[0], lon=ATLANTA[1], lead_days=1)
    assert _model_in_domain(UKMO_GLOBAL_MODEL, lat=SINGAPORE[0], lon=SINGAPORE[1], lead_days=1)


def test_download_fetches_promoted_models_domain_gated(tmp_path) -> None:
    from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema

    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_shadow_schema(conn)
    conn.close()

    single_calls: list[str] = []
    prev_calls: list[str] = []

    def _single(*, model, **_kw):
        single_calls.append(model)
        return 20.0

    def _previous(*, model, **_kw):
        prev_calls.append(model)
        return 19.5

    target = U0RDownloadTarget(
        city="Atlanta", latitude=ATLANTA[0], longitude=ATLANTA[1],
        timezone_name="America/New_York", target_date="2026-06-10", lead_days=1, metric="high",
    )
    download_u0r_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC), targets=[target],
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )
    assert NBM_MODEL in single_calls and NBM_MODEL in prev_calls
    assert UKMO_GLOBAL_MODEL in single_calls and UKMO_GLOBAL_MODEL in prev_calls
    assert UKMO_UK_MODEL not in single_calls and UKMO_UK_MODEL not in prev_calls
    # each model fetched exactly once per target (no candidate-lane double-iteration)
    assert single_calls.count(NBM_MODEL) == 1
    assert single_calls.count(UKMO_GLOBAL_MODEL) == 1
