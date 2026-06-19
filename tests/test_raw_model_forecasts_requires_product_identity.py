# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 4 — raw_model_forecasts must carry product-identity columns; a forecast value that cannot prove its physical product must not silently train a live-money posterior.
# Reuse: Run with pytest; update if raw_model_forecasts schema or the product-identity column set changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 raw capture + Fitz Constraint #4 (data provenance:
#   every data source needs source/authority; a forecast value that cannot prove its PHYSICAL
#   PRODUCT — daily vs hourly-agg endpoint, cell_selection, elevation/downscaling, requested
#   lat/lon, timezone, which ECMWF id — is UNVERIFIED provenance and must not silently train a
#   live-money posterior). architecture/db_table_ownership.yaml (raw_model_forecasts registry).
"""BLOCKER 4 — raw_model_forecasts must carry product-identity columns.

The table previously held only model/city/target_date/metric/source_cycle_time/
source_available_at/captured_at/lead_days/forecast_value_c/endpoint/trade_authority_status/
training_allowed. For live-money provenance that CANNOT prove the physical product (which OM
endpoint/product, cell_selection, elevation, requested coordinates, timezone, which ECMWF id).
This test pins the product-identity column set on the created schema.
"""
from __future__ import annotations

import sqlite3

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

PRODUCT_IDENTITY_COLUMNS = {
    "source_id",
    "source_family",
    "product_id",
    "provider",
    "model_name",
    "request_params_json",
    "request_url_hash",
    "raw_sha256",
    "latitude_requested",
    "longitude_requested",
    "timezone_requested",
    "cell_selection",
    "elevation_param",
    "downscaling_policy",
    "endpoint_mode",
    "model_domain_hash",
    "coverage_status",
    "artifact_id",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_raw_model_forecasts_has_all_product_identity_columns() -> None:
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_live_schema(conn)
    cols = _columns(conn, "raw_model_forecasts")
    missing = PRODUCT_IDENTITY_COLUMNS - cols
    assert not missing, f"raw_model_forecasts missing product-identity columns: {sorted(missing)}"


def test_legacy_value_columns_preserved() -> None:
    """The extension is additive — the original capture columns survive (no DROP)."""
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_live_schema(conn)
    cols = _columns(conn, "raw_model_forecasts")
    for legacy in (
        "model", "city", "target_date", "metric", "source_cycle_time",
        "source_available_at", "captured_at", "lead_days", "forecast_value_c",
        "endpoint", "trade_authority_status", "training_allowed",
    ):
        assert legacy in cols, f"legacy column {legacy} must be preserved"


def test_raw_sha256_and_artifact_id_are_nullable() -> None:
    """raw_sha256 and artifact_id are nullable per the brief (capture may precede artifact
    persistence). Inserting a row WITHOUT them must succeed."""
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_live_schema(conn)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint,
             source_id, source_family, product_id, provider, model_name,
             request_params_json, request_url_hash, latitude_requested, longitude_requested,
             timezone_requested, cell_selection, elevation_param, downscaling_policy,
             endpoint_mode, model_domain_hash, coverage_status)
        VALUES ('gfs_global', 'Paris', '2026-06-09', 'high', 'cyc', 'avail', 'cap', 1, 20.0,
                'previous_runs', 'gfs_previous_runs', 'openmeteo_previous_runs',
                'gfs_global_previous_runs', 'open-meteo', 'gfs_global', '{}', 'urlhash',
                48.967, 2.428, 'Europe/Paris', 'nearest', 'requested', 'none',
                'hourly_zeus_aggregated', 'domainhash', 'COVERED')
        """
    )
    row = conn.execute(
        "SELECT raw_sha256, artifact_id FROM raw_model_forecasts"
    ).fetchone()
    assert row[0] is None and row[1] is None
