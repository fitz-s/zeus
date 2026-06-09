# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 4 (sharpened) — a changed cell_selection on the same logical key must NOT be silently discarded; INSERT OR IGNORE must treat it as a new product row.
# Reuse: Run with pytest; update if raw_model_forecasts UNIQUE constraint or INSERT OR IGNORE semantics change.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R PR#400 BLOCKER 4 (operator-sharpened) + Fitz Constraint #4 (data
#   provenance) + the cell_selection-is-product-identity antibody
#   (tests/test_openmeteo_cell_selection_and_elevation_are_product_identity.py). Open-Meteo's
#   cell_selection (nearest vs land vs sea) materially changes the returned 2m temperature: the
#   SAME lat/lon at a DIFFERENT cell_selection is a DIFFERENT physical product. cell_selection
#   flows into request_params_json -> request_url_hash AND into product identity, so a changed
#   cell_selection under the same logical key is exactly the B4 same-key/different-request case.
"""BLOCKER 4 -- a changed cell_selection is NEVER silently INSERT-OR-IGNORE dropped.

Relationship test across the download->persist boundary: when the download captures a value
under cell_selection=nearest and a later run captures the SAME logical key under a DIFFERENT
cell_selection, the persist must NOT silently keep the stale 'nearest' value (the pre-fix
INSERT-OR-IGNORE bug). It must surface the changed request -- either as a NEW distinguishable row
(because request_url_hash differs) when the logical key already differs, or as a hard conflict
when the logical key is identical but the request identity changed.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.data.u0r_multimodel_download import (
    RawModelForecastRequestConflict,
    _persist_rows,
    _u0r_product_identity,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_shadow_schema(conn)
    return conn


def _target():
    from src.data.u0r_multimodel_download import U0RDownloadTarget
    return U0RDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                             lead_days=1, latitude=48.967, longitude=2.428,
                             timezone_name="Europe/Paris")


def _row_with_identity(identity: dict, *, value: float) -> dict:
    return {
        "model": "gfs_global", "city": "Paris", "target_date": "2026-06-09",
        "metric": "high", "source_cycle_time": "2026-06-08T00:00:00+00:00",
        "source_available_at": "2026-06-08T14:00:00+00:00",
        "captured_at": "2026-06-08T14:00:00+00:00", "lead_days": 1,
        "forecast_value_c": value, "endpoint": "previous_runs", **identity,
    }


def test_changed_cell_selection_changes_request_identity() -> None:
    """Sanity on the upstream relationship: cell_selection participates in request_url_hash and
    product/domain identity, so two cell_selections are NOT identity-equal. (If this ever broke,
    the conflict below could never fire -- the bug would be silent again.)"""
    import src.data.u0r_multimodel_download as dl

    id_nearest = _u0r_product_identity("gfs_global", "previous_runs", _target())
    orig = dl.U0R_CELL_SELECTION
    try:
        dl.U0R_CELL_SELECTION = "land"
        id_land = _u0r_product_identity("gfs_global", "previous_runs", _target())
    finally:
        dl.U0R_CELL_SELECTION = orig
    assert id_nearest["cell_selection"] == "nearest"
    assert id_land["cell_selection"] == "land"
    assert id_nearest["request_url_hash"] != id_land["request_url_hash"], \
        "cell_selection must change request_url_hash"
    assert id_nearest["model_domain_hash"] != id_land["model_domain_hash"], \
        "cell_selection must change model_domain_hash"


def test_changed_cell_selection_same_logical_key_is_not_ignored() -> None:
    """The core operator assertion: a corrected cell_selection under the SAME logical key must
    NOT be silently dropped. It raises (loud) instead of leaving the stale 'nearest' value."""
    import src.data.u0r_multimodel_download as dl

    conn = _conn()
    id_nearest = _u0r_product_identity("gfs_global", "previous_runs", _target())
    _persist_rows(conn, [_row_with_identity(id_nearest, value=19.5)])

    orig = dl.U0R_CELL_SELECTION
    try:
        dl.U0R_CELL_SELECTION = "land"
        id_land = _u0r_product_identity("gfs_global", "previous_runs", _target())
    finally:
        dl.U0R_CELL_SELECTION = orig

    with pytest.raises(RawModelForecastRequestConflict):
        _persist_rows(conn, [_row_with_identity(id_land, value=22.0)])

    # The stale nearest value is the only row and is untouched (not silently overwritten/ignored).
    rows = conn.execute(
        "SELECT forecast_value_c, cell_selection FROM raw_model_forecasts"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 19.5 and rows[0][1] == "nearest"


def test_changed_cell_selection_audit_row_records_both_requests() -> None:
    """The conflict audit row records the existing and incoming request hashes so the changed
    cell_selection is forensically attributable, not a silent no-op."""
    import src.data.u0r_multimodel_download as dl

    conn = _conn()
    id_nearest = _u0r_product_identity("gfs_global", "previous_runs", _target())
    _persist_rows(conn, [_row_with_identity(id_nearest, value=19.5)])

    orig = dl.U0R_CELL_SELECTION
    try:
        dl.U0R_CELL_SELECTION = "land"
        id_land = _u0r_product_identity("gfs_global", "previous_runs", _target())
    finally:
        dl.U0R_CELL_SELECTION = orig

    with pytest.raises(RawModelForecastRequestConflict):
        _persist_rows(conn, [_row_with_identity(id_land, value=22.0)])

    audit = conn.execute(
        """SELECT existing_request_url_hash, incoming_request_url_hash,
                  existing_product_id, incoming_product_id
           FROM raw_model_forecast_request_conflicts"""
    ).fetchone()
    assert audit is not None
    assert audit[0] == id_nearest["request_url_hash"]
    assert audit[1] == id_land["request_url_hash"]
