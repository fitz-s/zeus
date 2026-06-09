# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: 2026-06-09 uncovered-cities regional survey
#   (/tmp/uncovered_cities_regional_report.md, settlement-graded): ncep_nbm_conus CONUS-anchor
#   candidate (-14.4% pooled MAE, n=1029, GFS-correlated -> never a decorrelated member);
#   ukmo_global_deterministic_10km 5th-global candidate (-10.3%, n=1099);
#   ukmo_uk_deterministic_2km London regional candidate (n=112). Single-runs + previous-runs
#   availability curl-verified 2026-06-09 (unlike gem_global).
"""CANDIDATE-ACCRUAL antibodies: data accrues, fusion membership impossible.

Two-sided invariant:
  (1) the download job fetches the candidate models (both legs, domain-gated) so walk-forward
      history accrues for a future promotion decision;
  (2) select_models NEVER admits a candidate into used_models — accrual is not promotion
      (iron rule: promotion needs forward-shadow validation, never in-sample).
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from src.data.u0r_multimodel_download import (
    U0R_CANDIDATE_ACCRUAL_MODELS,
    U0RDownloadTarget,
    _model_in_domain,
    download_u0r_extra_raw_inputs,
)
from src.forecast.model_selection import select_models

ATLANTA = (33.630, -84.442)
LONDON = (51.505, 0.055)
TOKYO = (35.68, 139.69)
SINGAPORE = (1.368, 103.982)


def test_candidate_domain_gates() -> None:
    # NBM: CONUS only.
    assert _model_in_domain("ncep_nbm_conus", lat=ATLANTA[0], lon=ATLANTA[1], lead_days=1)
    assert not _model_in_domain("ncep_nbm_conus", lat=TOKYO[0], lon=TOKYO[1], lead_days=1)
    assert not _model_in_domain("ncep_nbm_conus", lat=LONDON[0], lon=LONDON[1], lead_days=1)
    # UKMO UK 2km: UK only.
    assert _model_in_domain("ukmo_uk_deterministic_2km", lat=LONDON[0], lon=LONDON[1], lead_days=1)
    assert not _model_in_domain("ukmo_uk_deterministic_2km", lat=ATLANTA[0], lon=ATLANTA[1], lead_days=1)
    # UKMO global: worldwide (ungated).
    assert _model_in_domain("ukmo_global_deterministic_10km", lat=SINGAPORE[0], lon=SINGAPORE[1], lead_days=1)
    assert _model_in_domain("ukmo_global_deterministic_10km", lat=TOKYO[0], lon=TOKYO[1], lead_days=1)


def test_download_accrues_candidates_both_legs(tmp_path) -> None:
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
    # CONUS city: nbm + ukmo_global fetched on BOTH legs; ukmo_uk out-of-domain skipped.
    assert "ncep_nbm_conus" in single_calls and "ncep_nbm_conus" in prev_calls
    assert "ukmo_global_deterministic_10km" in single_calls
    assert "ukmo_global_deterministic_10km" in prev_calls
    assert "ukmo_uk_deterministic_2km" not in single_calls
    assert "ukmo_uk_deterministic_2km" not in prev_calls


def test_candidates_never_enter_fusion_selection() -> None:
    # Even with candidate values PRESENT in the capture inputs, select_models must not admit
    # them: accrual is not promotion. (REGIONAL_MODELS / GLOBAL_LIKELIHOOD_MODELS unchanged.)
    present = {
        "ecmwf_ifs": 4.7, "gfs_global": 5.1, "icon_global": 3.7, "gem_global": 5.0,
        "jma_seamless": 3.8,
        "ncep_nbm_conus": 4.4, "ukmo_global_deterministic_10km": 4.5,
        "ukmo_uk_deterministic_2km": 4.6,
    }
    for lat, lon in (ATLANTA, LONDON, SINGAPORE):
        sel = select_models(present_models=present, lat=lat, lon=lon, lead_days=1)
        for cand in U0R_CANDIDATE_ACCRUAL_MODELS:
            assert cand not in sel.used_models, (lat, lon, cand, sel.used_models)
            assert cand not in sel.likelihood_globals
            assert cand not in sel.regional_experts
