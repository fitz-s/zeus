# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 5 — the U0R fusion override must read persisted current single_runs rows from the DB, never network-fetch inside the q path.
# Reuse: Run with pytest; update if the current-rows consumption path or network-fetch prohibition in the U0R override changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §6 F1 (the download job persists raw_model_forecasts;
#   the q path CONSUMES persisted rows) + Fitz Constraint #4 (a traded q must be reconstructable
#   to the exact persisted inputs: which models, URL, params, payload, source_available_at).
#   BLOCKER 5 (PR#400 review): the materializer must NOT network-fetch inside the q path.
"""BLOCKER 5 — the U0R fusion override reads PERSISTED current single_runs rows, not network.

Before the fix, _replacement_u0r_fusion_override called capture_u0r_instruments which
network-fetched the current (single_runs) values in-memory (no DB write) and fused them into the
traded posterior — so the q was built from un-persisted network values (not reconstructable).

The fix: the override reads the current single_runs rows already PERSISTED in raw_model_forecasts
(written by the download job) for this (city, metric, target_date, lead, source_cycle_time). This
test proves: (a) when the live values come ONLY from the persisted rows (and the network live_fetch
would raise), the fusion still reaches the extras; (b) the network live_fetch is NOT invoked.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod
from tests.test_u0r_history_provider_materializer_wiring import (  # reuse fixtures
    _anchor,
    _aifs_extraction,
    _bins,
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _precision_guard,
    _request,
    _reset_override_seams,  # noqa: F401  (autouse fixture import)
    _row,
    _seed_history,
)
from src.forecast.u0r_bayes import MIN_TRAIN

UTC = timezone.utc

CURRENT_MODELS = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]


def _seed_current_single_runs(conn, *, request) -> None:
    """Persist the CURRENT single_runs rows the download job would have written for THIS cycle
    (city, metric, target_date, lead, source_cycle_time). These are what the q path must read."""
    from src.data.replacement_forecast_materializer import _date_text, _to_utc, _u0r_city_local_lead_days

    target_date = _date_text(request.target_date)
    cyc = _to_utc(request.source_cycle_time, field_name="source_cycle_time").isoformat()
    lead = _u0r_city_local_lead_days(
        computed_at=_to_utc(request.computed_at, field_name="computed_at"),
        target_local_date=date.fromisoformat(target_date),
        tz_name="Europe/Paris",
    )
    values = {"ecmwf_ifs": 27.0, "gfs_global": 23.0, "icon_global": 23.5,
              "gem_global": 22.5, "jma_seamless": 24.0, "icon_eu": 23.2}
    for m, v in values.items():
        conn.execute(
            """INSERT INTO raw_model_forecasts
               (model, city, target_date, metric, source_cycle_time, source_available_at,
                captured_at, lead_days, forecast_value_c, endpoint, model_name, source_family)
               VALUES (?, 'Paris', ?, 'high', ?, 'avail', 'cap', ?, ?, 'single_runs', ?, 'openmeteo_single_runs')""",
            (m, target_date, cyc, lead, v, m if m != "ecmwf_ifs" else "ecmwf_ifs"),
        )


def test_q_path_uses_persisted_current_rows_and_never_calls_network(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=CURRENT_MODELS)
    _seed_current_single_runs(conn, request=_request())

    # A network live_fetch that RAISES if invoked — proves the q path does not network-fetch.
    def _exploding_fetch(*a, **k):
        raise AssertionError("the q path must NOT network-fetch; it must read persisted rows")

    mod._replacement_u0r_fusion_override._live_fetch = _exploding_fetch

    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    prov = json.loads(_row(conn, pid)["provenance_json"]).get("u0r_fusion")
    assert prov is not None, "fusion must have run from the persisted current rows"
    assert prov["method"] in {"T2_BAYES", "EQUAL_WEIGHT", "ANCHOR_FALLBACK"}
    # The fused center must be sane (built from the persisted ~23 current values vs 27 anchor).
    assert prov["anchor_value_c"] < 27.0
