# Created: 2026-06-08
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-18; last_reused=2026-06-18
# Purpose: Regression tests for BPF raw forecast download and persistence semantics.
# Reuse: Run when changing Bayes precision fusion raw-input capture or scheduler health.
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 (raw capture: previous_runs + single_runs ->
#   raw_model_forecasts), §5 (~6mo retention); CONTINUITY_AND_WIRING.md §4 steps 2-3 + 9
#   (forward+history daily download/persist + 180d prune). IRON RULE #4 (one-builder: reuse
#   the OM fetchers, single persist conn), INV-37 (single zeus-forecasts.db connection).
"""TDD for the BAYES_PRECISION_FUSION multi-model forward+history download/persist job.

Relationship under test (download job -> raw_model_forecasts persistence boundary):
  (a) when capture-flag ON it WRITES raw_model_forecasts rows (single_runs FORWARD +
      previous_runs fixed-lead) for the surviving models; (b) it persists NOTHING when there
      are no targets / no surviving fetches; (c) FAIL-SOFT per model (a raising fetch drops
      only that model); (d) forecast_value_c is degC and training_allowed=0;
      (e) retention prunes rows older than the cutoff; (f) idempotent re-run (UNIQUE upsert).
All fetchers are injected (NO network).
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


def _forecast_db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()
    return db


def _targets():
    # (city, metric, target_date, lead_days, lat, lon, timezone)
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                          lead_days=1, latitude=48.967, longitude=2.428, timezone_name="Europe/Paris"),
    ]


def _count(db: Path, **where) -> int:
    conn = sqlite3.connect(str(db))
    clause = " AND ".join(f"{k}=?" for k in where)
    sql = "SELECT COUNT(*) FROM raw_model_forecasts" + (f" WHERE {clause}" if where else "")
    n = conn.execute(sql, tuple(where.values())).fetchone()[0]
    conn.close()
    return int(n)


# =====================================================================================
# (a) capture-flag ON: writes single_runs (forward) + previous_runs (fixed-lead) rows
# =====================================================================================
def test_download_writes_single_and_previous_runs(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)

    # Inject deterministic fetchers (no network). single_runs -> one degC value;
    # previous_runs -> a degC value for the fixed-lead.
    def _single(*, model, latitude, longitude, timezone_name, run, target_local_date, metric, forecast_hours):
        return 20.0 + len(model) * 0.01  # model-specific degC

    def _previous(*, model, latitude, longitude, timezone_name, target_date, lead_days, metric):
        return 19.5 + len(model) * 0.01

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    # Current selected model set x {single_runs, previous_runs} for the one target.
    n_single = _count(db, endpoint="single_runs")
    n_prev = _count(db, endpoint="previous_runs")
    assert n_single > 0, f"expected in-domain forward single_runs rows, got {n_single}"
    assert n_prev > 0, f"expected previous_runs fixed-lead rows, got {n_prev}"
    # forecast_value_c is degC; training_allowed=0 is pinned.
    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM raw_model_forecasts LIMIT 1").fetchone()
    assert row["training_allowed"] == 0
    assert 15.0 <= row["forecast_value_c"] <= 25.0
    conn.close()


# =====================================================================================
# (b) no targets -> no writes
# =====================================================================================
def test_download_no_targets_writes_nothing(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=[],
        single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 20.0,
    )
    assert _count(db) == 0
    assert report["written_row_count"] == 0


# =====================================================================================
# (c) FAIL-SOFT per model: a raising fetch drops only that model
# =====================================================================================
def test_download_failsoft_per_model(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)

    def _single(*, model, **k):
        if model == "gfs_global":
            raise RuntimeError("simulated network blowup for gfs_global")
        return 20.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=lambda **k: 19.0,
    )
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    # gfs_global single_runs dropped; others present.
    assert _count(db, endpoint="single_runs", model="gfs_global") == 0
    assert _count(db, endpoint="single_runs", model="icon_global") == 1


# =====================================================================================
# (d) None fetch -> model simply absent (fail-soft drop, no crash)
# =====================================================================================
def test_download_none_fetch_drops_model(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)

    def _single(*, model, **k):
        return None if model == "jma_seamless" else 20.0

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=lambda **k: None,
    )
    assert _count(db, endpoint="single_runs", model="jma_seamless") == 0
    assert _count(db, endpoint="previous_runs") == 0  # all previous_runs returned None


# =====================================================================================
# (e) retention prune: rows older than the cutoff are deleted
# =====================================================================================
def test_download_prunes_old_rows(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs, RETENTION_DAYS

    db = _forecast_db(tmp_path)
    # Seed one ancient row (captured ~200d ago) and one recent row.
    conn = sqlite3.connect(str(db))
    old_captured = (datetime(2026, 6, 8, tzinfo=UTC).timestamp() - (RETENTION_DAYS + 20) * 86400)
    old_iso = datetime.fromtimestamp(old_captured, tz=UTC).isoformat()
    conn.execute(
        """INSERT INTO raw_model_forecasts
           (model, city, target_date, metric, source_cycle_time, source_available_at,
            captured_at, lead_days, forecast_value_c, endpoint)
           VALUES ('gfs_global','Paris','2025-11-01','high','x','y',?,1,20.0,'previous_runs')""",
        (old_iso,),
    )
    conn.commit(); conn.close()
    assert _count(db) == 1

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=[],
        single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 20.0,
    )
    assert report["pruned_row_count"] == 1
    assert _count(db) == 0, "rows older than the retention cutoff must be pruned"


# =====================================================================================
# (f) idempotent re-run: UNIQUE(model,city,target_date,metric,source_cycle_time,endpoint)
# =====================================================================================
def test_download_is_idempotent(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, tzinfo=UTC)
    kwargs = dict(forecast_db=db, cycle=cycle, targets=_targets(),
                  single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 19.0)
    download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    n1 = _count(db)
    download_bayes_precision_fusion_extra_raw_inputs(**kwargs)  # same cycle -> no duplicate rows
    n2 = _count(db)
    assert n1 == n2, "re-running the same cycle must not duplicate rows (UNIQUE upsert)"


# =====================================================================================
# (g) FIX 1 — the ANCHOR (ecmwf_ifs) MUST be captured, else its walk-forward history is
#     forever empty (have_anchor=False -> fusion stuck EQUAL_WEIGHT). The download set
#     MUST include ANCHOR_MODEL, and the PERSISTED `model` column MUST be the anchor
#     identity "ecmwf_ifs" (the key BayesPrecisionFusionHistoryProvider / capture join on), NOT the
#     Open-Meteo model id. The OM previous-runs fetch uses the OM ECMWF id (ecmwf_ifs025),
#     but the STORED model col = "ecmwf_ifs".
# =====================================================================================
def test_download_includes_anchor_and_stores_ecmwf_ifs_identity(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import (
        BAYES_PRECISION_FUSION_EXTRA_MODELS,
        download_bayes_precision_fusion_extra_raw_inputs,
    )
    from src.forecast.model_selection import ANCHOR_MODEL

    # The download set MUST include the anchor (ecmwf_ifs); otherwise no anchor history
    # ever accrues and the fusion can never leave EQUAL_WEIGHT.
    assert ANCHOR_MODEL in BAYES_PRECISION_FUSION_EXTRA_MODELS, (
        "the anchor (ecmwf_ifs) must be in the BAYES_PRECISION_FUSION capture set so its previous_runs "
        "history accrues -> have_anchor=True -> T2_BAYES"
    )

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)

    seen_prev_models: list[str] = []

    def _single(*, model, **k):
        return 20.0

    def _previous(*, model, **k):
        seen_prev_models.append(model)
        return 19.0

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )

    # The previous_runs fetch was asked for the anchor under its STORED identity
    # (ecmwf_ifs) — the OM-id translation is the fetch's internal concern.
    assert ANCHOR_MODEL in seen_prev_models, "anchor previous_runs fetch must be invoked"

    # A previous_runs row keyed model='ecmwf_ifs' (the anchor identity) was persisted.
    assert _count(db, endpoint="previous_runs", model="ecmwf_ifs") == 1, (
        "anchor previous_runs row must be stored keyed model='ecmwf_ifs' (the join key)"
    )
    # And a forward single_runs anchor row too (the anchor is captured both ways).
    assert _count(db, endpoint="single_runs", model="ecmwf_ifs") == 1
    # The OM model id (ecmwf_ifs025) MUST NOT leak into the stored model column.
    assert _count(db, model="ecmwf_ifs025") == 0, (
        "the OM model id must never be the stored model col; store the anchor identity"
    )


# =====================================================================================
# DOMAIN GATE RELATIONSHIP TESTS (FAULT-B fix: no constructable 400 for regional models)
#
# Relationship under test: for each configured model the download request is only built
# and sent when the city coordinate is inside the model's geographic domain, so "No data
# is available for this location" HTTP 400s become structurally impossible. A
# domain-excluded model is recorded in domain_excluded (loud), not silently absent.
# =====================================================================================

def _tokyo_target():
    """A city outside icon_eu/icon_d2/arome domains (Tokyo)."""
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(city="Tokyo", metric="high", target_date="2026-06-09",
                          lead_days=1, latitude=35.553, longitude=139.781,
                          timezone_name="Asia/Tokyo"),
    ]


def _paris_target():
    """A city inside all EU domains (Paris)."""
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                          lead_days=1, latitude=48.967, longitude=2.428,
                          timezone_name="Europe/Paris"),
    ]


def test_domain_gate_no_request_for_regional_at_out_of_domain_city(tmp_path) -> None:
    """icon_eu, icon_d2, meteofrance_arome_france_hd MUST NOT be fetched for Tokyo.

    The single-runs and previous-runs APIs return HTTP 400 for out-of-domain coords;
    this gate prevents those requests from ever being built, making the 400 category
    structurally impossible.
    """
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    fetched_models: list[str] = []

    def _single(*, model, **k):
        fetched_models.append(model)
        return 20.0

    def _previous(*, model, **k):
        fetched_models.append(model)
        return 19.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=_tokyo_target(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )

    # No request must be built for domain-limited models at Tokyo.
    assert "icon_eu" not in fetched_models, (
        "icon_eu must not be requested for Tokyo (out-of-EU domain)"
    )
    assert "icon_d2" not in fetched_models, (
        "icon_d2 must not be requested for Tokyo (out-of-Central-EU domain)"
    )
    assert "meteofrance_arome_france_hd" not in fetched_models, (
        "meteofrance_arome_france_hd must not be requested for Tokyo (out-of-France domain)"
    )

    # The skips must be loudly recorded in domain_excluded.
    excluded_set = set(report["domain_excluded"])
    assert any("icon_eu" in e and "Tokyo" in e for e in excluded_set), (
        "icon_eu:Tokyo must appear in domain_excluded"
    )
    assert any("icon_d2" in e and "Tokyo" in e for e in excluded_set), (
        "icon_d2:Tokyo must appear in domain_excluded"
    )
    assert any("meteofrance_arome_france_hd" in e and "Tokyo" in e for e in excluded_set), (
        "meteofrance_arome_france_hd:Tokyo must appear in domain_excluded"
    )

    # Global models (gfs_global, icon_global, gem_global, jma_seamless, ecmwf_ifs) MUST
    # still be fetched — the domain gate must not touch globals.
    from src.forecast.model_selection import ANCHOR_MODEL, DECORR_GLOBALS
    for global_model in list(DECORR_GLOBALS) + [ANCHOR_MODEL]:
        assert global_model in fetched_models, (
            f"global model {global_model} must still be fetched for out-of-domain city"
        )


def test_domain_gate_all_models_fetched_for_in_domain_city(tmp_path) -> None:
    """All configured models including icon_eu/icon_d2/arome MUST be fetched for Paris.

    The gate must not over-exclude: in-domain cities should request every model.
    """
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    fetched_models: list[str] = []

    def _single(*, model, **k):
        fetched_models.append(model)
        return 20.0

    def _previous(*, model, **k):
        return 19.0

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=_paris_target(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )

    for regional in ("icon_eu", "icon_d2", "meteofrance_arome_france_hd"):
        assert regional in fetched_models, (
            f"{regional} must be fetched for Paris (in-domain)"
        )


def test_domain_gate_unavailable_global_is_loud_not_silent(tmp_path) -> None:
    """A global model (always in-domain) that fails to fetch must appear in `dropped`,
    NOT in `domain_excluded`. The ensemble-completeness report must flag it.

    This guards STEP 4: real upstream failures on global models are distinguishable
    from expected domain-exclusion absences.
    """
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs
    from src.forecast.model_selection import ANCHOR_MODEL

    db = _forecast_db(tmp_path)

    def _single(*, model, **k):
        if model == "icon_global":
            return None  # simulate upstream unavailability
        return 20.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=_paris_target(),
        single_runs_fetch=_single, previous_runs_fetch=lambda **k: 19.0,
    )

    # icon_global (worldwide global, always in-domain) failure must be in `dropped`. (2026-06-17:
    # gfs_global was dropped from the fusion; icon_global is the canonical worldwide global here.)
    assert "icon_global:single_runs" in report["dropped"], (
        "unavailable global model must be in dropped, not silently absent"
    )
    # It must NOT be in domain_excluded (that is reserved for domain-geographic skips).
    excluded_set = set(report["domain_excluded"])
    assert not any("icon_global" in e for e in excluded_set), (
        "icon_global must never appear in domain_excluded (it is a global model)"
    )
    # global_models_unavailable must flag it.
    assert "icon_global" in report["global_models_unavailable"], (
        "icon_global single_runs failure must appear in global_models_unavailable"
    )


def test_scoped_global_drop_does_not_fail_cycle_when_model_succeeds_elsewhere(tmp_path) -> None:
    """A global model with at least one successful row for the cycle is degraded by scope,
    not unavailable for the whole BPF capture job.

    This guards the live preflight deadlock where one residual target gap marked
    bayes_precision_fusion_capture FAILED even though the same global model had already
    landed rows for other targets in that cycle.
    """
    from src.data.bayes_precision_fusion_download import (
        BayesPrecisionFusionDownloadTarget,
        download_bayes_precision_fusion_extra_raw_inputs,
    )

    db = _forecast_db(tmp_path)
    targets = [
        BayesPrecisionFusionDownloadTarget(
            city="Paris",
            metric="high",
            target_date="2026-06-09",
            lead_days=1,
            latitude=48.967,
            longitude=2.428,
            timezone_name="Europe/Paris",
        ),
        BayesPrecisionFusionDownloadTarget(
            city="Berlin",
            metric="high",
            target_date="2026-06-09",
            lead_days=1,
            latitude=52.520,
            longitude=13.405,
            timezone_name="Europe/Berlin",
        ),
    ]

    def _single(*, model, latitude, **_k):
        if model == "icon_global" and float(latitude) < 50.0:
            return None
        return 20.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=targets,
        single_runs_fetch=_single,
        previous_runs_fetch=lambda **_k: 19.0,
    )

    assert "icon_global:single_runs" in report["dropped"]
    assert "icon_global" in report["global_models_dropped_scoped"]
    assert "icon_global" not in report["global_models_unavailable"]


def test_batched_single_runs_transport_failure_is_retryable_not_empty_success(tmp_path, monkeypatch) -> None:
    """A process-local Open-Meteo quota/cooldown failure must not flatten to a successful empty pass."""
    import src.data.bayes_precision_fusion_download as dl
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)

    def _single_batch_fail(**_kwargs):
        return {
            dl._BATCH_TRANSPORT_ERROR_KEY: (
                "Open-Meteo quota exhausted (2 calls today)",
                None,
            )
        }

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _single_batch_fail)
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", lambda **_kwargs: {})

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 12, tzinfo=UTC),
        targets=_targets(),
    )

    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert report["written_row_count"] == 0
    assert report["transport_errors"]
    assert "Open-Meteo quota exhausted" in report["transport_errors"][0]


def test_bpf_batched_fetch_uses_separate_quota_state_from_shared_openmeteo_lane(monkeypatch) -> None:
    """A cooldown in another Open-Meteo lane must not suppress the BPF raw-input lane."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as om
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    class _Resp:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "hourly": {
                    "temperature_2m_previous_day1_icon_global": [18.0, 19.5, 17.25],
                }
            }

    monkeypatch.setattr(om.quota_tracker, "can_call", lambda: False)
    monkeypatch.setattr(dl, "_BPF_OPENMETEO_QUOTA_TRACKER", OpenMeteoQuotaTracker())
    monkeypatch.setattr(om.httpx, "get", lambda *_args, **_kwargs: _Resp())

    got = dl._default_previous_runs_fetch_batched(
        models=["icon_global"],
        latitude=48.967,
        longitude=2.428,
        timezone_name="Europe/Paris",
        target_date="2026-06-09",
        lead_days=1,
    )

    assert got == {"icon_global": (19.5, 17.25)}


def test_default_previous_runs_fetch_uses_om_ecmwf_id_for_anchor(monkeypatch) -> None:
    """FIX 1 mapping: the DEFAULT previous-runs fetch for the anchor must send the OM
    ECMWF previous-runs model id (ecmwf_ifs025) to the OM API, even though the stored
    model col is the anchor identity 'ecmwf_ifs'. Guards the fetch-vs-store split so a
    future edit cannot send 'ecmwf_ifs' (no OM previous-runs entry) and silently drop
    the anchor history fail-soft.
    """
    import src.data.openmeteo_client as om
    from src.data.bayes_precision_fusion_download import _default_previous_runs_fetch
    from src.forecast.model_selection import ANCHOR_MODEL

    captured: dict[str, object] = {}

    def _fake_fetch(url, params, *, endpoint_label):
        captured["models"] = params["models"]
        return {"hourly": {params["hourly"]: [18.0, 19.0, 17.5]}}

    monkeypatch.setattr(om, "fetch", _fake_fetch)

    value = _default_previous_runs_fetch(
        model=ANCHOR_MODEL, latitude=48.967, longitude=2.428,
        timezone_name="Europe/Paris", target_date="2026-06-09",
        lead_days=1, metric="high",
    )
    assert value == 19.0  # max over the local-day window for 'high'
    assert captured["models"] == "ecmwf_ifs025", (
        "anchor previous-runs OM fetch must use the OM ECMWF id ecmwf_ifs025"
    )
