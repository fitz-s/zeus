# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 4 — the BAYES_PRECISION_FUSION download writer must persist Open-Meteo request params (lat/lon, timezone, model, endpoint, request_params_json, url_hash) so every stored forecast is reconstructable.
# Reuse: Run with pytest; update if raw_model_forecasts schema or download writer changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 raw capture; Fitz Constraint #4 (data provenance:
#   the request that produced a forecast value MUST be reconstructable — requested lat/lon,
#   timezone, the OM model id, the endpoint). CONTINUITY_AND_WIRING.md §4 steps 2-3.
"""BLOCKER 4 — the BAYES_PRECISION_FUSION download job must PERSIST the Open-Meteo request params + identity.

When the download writes a raw_model_forecasts row it must record, for that exact fetch:
requested latitude/longitude, requested timezone, the OM model_name actually addressed, the
request_params_json + request_url_hash, the source_id/source_family/product_id/provider, and
the endpoint_mode. Without this the stored forecast_value_c cannot prove WHICH physical product
produced it. This test injects deterministic fetchers and asserts the persisted identity.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


def _forecast_db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()
    return db


def _targets():
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                          lead_days=1, latitude=48.967, longitude=2.428,
                          timezone_name="Europe/Paris"),
    ]


def test_request_params_and_identity_persisted(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)

    def _single(*, model, latitude, longitude, timezone_name, run, target_local_date, metric, forecast_hours):
        return 20.0

    def _previous(*, model, latitude, longitude, timezone_name, target_date, lead_days, metric):
        return 19.5

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT model, endpoint, source_id, source_family, product_id, provider, model_name,
                  request_params_json, request_url_hash, latitude_requested, longitude_requested,
                  timezone_requested, endpoint_mode
           FROM raw_model_forecasts ORDER BY model, endpoint"""
    ).fetchall()
    conn.close()
    assert rows, "download must persist rows"

    for r in rows:
        # Coordinates + timezone of the actual request are recorded.
        assert abs(r["latitude_requested"] - 48.967) < 1e-9
        assert abs(r["longitude_requested"] - 2.428) < 1e-9
        assert r["timezone_requested"] == "Europe/Paris"
        # provider + product/source identity present and non-empty.
        assert r["provider"] == "open-meteo"
        assert r["source_id"], "source_id must be recorded"
        assert r["source_family"], "source_family must be recorded"
        assert r["product_id"], "product_id must be recorded"
        assert r["model_name"], "model_name (OM model id actually addressed) must be recorded"
        # request params reconstructable: valid JSON carrying lat/lon/timezone + the OM model.
        params = json.loads(r["request_params_json"])
        assert params, "request_params_json must not be empty"
        assert str(params.get("timezone")) == "Europe/Paris"
        assert "models" in params
        # endpoint_mode distinguishes single_runs vs previous_runs physical product.
        assert r["endpoint_mode"] in {"single_runs", "previous_runs"}
        assert r["endpoint_mode"] == r["endpoint"]
        # url hash present (the request URL is fingerprinted, not stored raw).
        assert r["request_url_hash"], "request_url_hash must be recorded"


def test_previous_runs_records_om_previous_runs_product(tmp_path) -> None:
    """The previous_runs rows must record source_family='openmeteo_previous_runs' and the
    correct per-model previous-runs source_id (e.g. icon_global -> icon_previous_runs).
    (2026-06-17: the example was gfs_global; gfs_global was dropped from the fusion and is no
    longer fetched, so the pin moved to the still-fetched icon_global.)"""
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=_targets(),
        single_runs_fetch=lambda **k: 20.0,
        previous_runs_fetch=lambda **k: 19.5,
    )

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT source_id, source_family, product_id FROM raw_model_forecasts
           WHERE model='icon_global' AND endpoint='previous_runs'"""
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["source_family"] == "openmeteo_previous_runs"
    assert row["source_id"] == "icon_previous_runs"
