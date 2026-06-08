# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 4 (sharpened) — same logical key with a different request_url_hash must be REJECTED (raises), not silently discarded via INSERT OR IGNORE; makes stale-forecast contamination impossible.
# Reuse: Run with pytest; update if the UNIQUE constraint or rejection semantics in raw_model_forecasts writer change.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R PR#400 BLOCKER 4 (operator-sharpened) + Fitz Constraint #4 (data
#   provenance) + Fitz Methodology #4 (make the category impossible). The pre-fix UNIQUE was
#   UNIQUE(model, city, target_date, metric, source_cycle_time, endpoint) with INSERT OR IGNORE:
#   a Run-2 with the SAME logical key but a DIFFERENT physical request (changed timezone /
#   cell_selection / elevation / product_id / request_url_hash) was SILENTLY discarded, leaving
#   a stale forecast_value_c to contaminate bias/MAE/sigma/covariance/q in the walk-forward
#   history JOIN (src/data/u0r_history_provider.py keys on model/city/metric/lead/endpoint/
#   target_date -- NOT on the request hash, so a stale row poisons the residual series).
"""BLOCKER 4 -- a same-logical-key row with a DIFFERENT request hash is REJECTED, not ignored.

Relationship test (cross-module invariant on the persist boundary): the property that must hold
when a corrected request (Run-2) flows into the raw_model_forecasts persist layer is --

    A logical key (model, city, target_date, metric, source_cycle_time, endpoint) is bound to
    EXACTLY ONE physical request identity (product_id, request_url_hash). A second persist under
    the same logical key but a DIFFERENT request identity is a CONFLICT and MUST raise (and write
    an audit row) -- it MUST NOT be silently INSERT-OR-IGNORE dropped, and it MUST NOT silently
    leave the stale value in place.

This is the operator-named B4 contamination test: the stale value must never silently survive a
corrected request.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.data.u0r_multimodel_download import (
    RawModelForecastRequestConflict,
    _persist_rows,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema


def _row(*, request_url_hash: str, product_id: str, value: float, cell_selection: str = "nearest") -> dict:
    """One fully-keyed raw_model_forecasts row dict (capture cols + B4 product identity)."""
    return {
        "model": "gfs_global",
        "city": "Paris",
        "target_date": "2026-06-09",
        "metric": "high",
        "source_cycle_time": "2026-06-08T00:00:00+00:00",
        "source_available_at": "2026-06-08T14:00:00+00:00",
        "captured_at": "2026-06-08T14:00:00+00:00",
        "lead_days": 1,
        "forecast_value_c": value,
        "endpoint": "previous_runs",
        "source_id": "gfs_previous_runs",
        "source_family": "openmeteo_previous_runs",
        "product_id": product_id,
        "provider": "open-meteo",
        "model_name": "gfs_global",
        "request_params_json": '{"cell_selection":"%s"}' % cell_selection,
        "request_url_hash": request_url_hash,
        "latitude_requested": 48.967,
        "longitude_requested": 2.428,
        "timezone_requested": "Europe/Paris",
        "cell_selection": cell_selection,
        "elevation_param": "requested",
        "downscaling_policy": "none",
        "endpoint_mode": "previous_runs",
        "model_domain_hash": "domainhash",
        "coverage_status": "COVERED",
    }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_shadow_schema(conn)
    return conn


def test_exact_duplicate_is_idempotent_no_dup_row() -> None:
    """A Run-2 with the IDENTICAL logical key AND identical request identity is the normal
    re-run case: INSERT OR IGNORE collapses it (one row), no conflict raised."""
    conn = _conn()
    row = _row(request_url_hash="hash_A", product_id="gfs_global::previous_runs", value=19.5)
    _persist_rows(conn, [row])
    _persist_rows(conn, [dict(row)])  # identical re-run
    n = conn.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
    assert n == 1, "identical re-run must be idempotent (one row, no conflict)"


def test_same_logical_key_different_request_hash_raises() -> None:
    """The contamination case: same logical key, DIFFERENT request_url_hash (a corrected
    request). It MUST raise -- NOT be silently INSERT-OR-IGNORE dropped."""
    conn = _conn()
    _persist_rows(conn, [_row(request_url_hash="hash_A", product_id="gfs_global::previous_runs", value=19.5)])
    with pytest.raises(RawModelForecastRequestConflict):
        _persist_rows(conn, [_row(request_url_hash="hash_B", product_id="gfs_global::previous_runs", value=22.0)])


def test_same_logical_key_different_product_id_raises() -> None:
    """A changed product_id under the same logical key is equally a conflict."""
    conn = _conn()
    _persist_rows(conn, [_row(request_url_hash="hash_A", product_id="gfs_global::previous_runs", value=19.5)])
    with pytest.raises(RawModelForecastRequestConflict):
        _persist_rows(conn, [_row(request_url_hash="hash_A", product_id="gfs_seamless::previous_runs", value=22.0)])


def test_stale_value_not_silently_overwritten_nor_left_to_contaminate() -> None:
    """After a rejected conflict the persist must NOT have silently mutated state: the original
    value is still the only row, and the conflicting (stale-or-corrected) value never entered the
    history JOIN surface. The conflict is loud (raise) so an operator re-pins identity, rather
    than a stale value silently poisoning bias/MAE/sigma."""
    conn = _conn()
    _persist_rows(conn, [_row(request_url_hash="hash_A", product_id="gfs_global::previous_runs", value=19.5)])
    with pytest.raises(RawModelForecastRequestConflict):
        _persist_rows(conn, [_row(request_url_hash="hash_B", product_id="gfs_global::previous_runs", value=22.0)])
    rows = conn.execute(
        "SELECT forecast_value_c, request_url_hash FROM raw_model_forecasts ORDER BY raw_model_forecast_id"
    ).fetchall()
    assert len(rows) == 1, "conflict must not have inserted the corrected row alongside the stale one"
    assert rows[0][0] == 19.5 and rows[0][1] == "hash_A", "original row must be untouched"


def test_conflict_writes_audit_row() -> None:
    """The conflict must leave an audit trail (operator directive: 'raise a hard error / write an
    audit row') so the silent-drop category is replaced by a visible, attributable event."""
    conn = _conn()
    _persist_rows(conn, [_row(request_url_hash="hash_A", product_id="gfs_global::previous_runs", value=19.5)])
    with pytest.raises(RawModelForecastRequestConflict):
        _persist_rows(conn, [_row(request_url_hash="hash_B", product_id="gfs_global::previous_runs", value=22.0)])
    audit = conn.execute(
        """SELECT model, city, target_date, metric, source_cycle_time, endpoint,
                  existing_request_url_hash, incoming_request_url_hash
           FROM raw_model_forecast_request_conflicts"""
    ).fetchall()
    assert len(audit) == 1, "exactly one audit row for the conflict"
    a = audit[0]
    assert a[0] == "gfs_global" and a[1] == "Paris" and a[2] == "2026-06-09"
    assert a[6] == "hash_A" and a[7] == "hash_B", "audit records BOTH hashes for forensics"


def test_different_logical_key_same_hash_is_a_new_row_not_conflict() -> None:
    """A genuinely different logical key (e.g. a different target_date) is NOT a conflict even if
    the request hash collides -- uniqueness is scoped to the logical key + request identity."""
    conn = _conn()
    base = _row(request_url_hash="hash_A", product_id="gfs_global::previous_runs", value=19.5)
    _persist_rows(conn, [base])
    other = dict(base, target_date="2026-06-10")
    _persist_rows(conn, [other])  # different logical key -> new row, no conflict
    n = conn.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0]
    assert n == 2
