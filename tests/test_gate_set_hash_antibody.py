# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: operator adjudication 2026-05-27/28 — domain-canonicality antibody.
#   Relationship tests for the gate_set_hash + coverage_months read-time guards that
#   make the pre-gate-transport-delta contamination category structurally impossible.
"""Antibody tests: a stored bias row fit under a SUPERSEDED gate set, or applied to a
month it never covered, must NOT be servable by read_bias_model.

These are RELATIONSHIP tests (cross-module invariant), not function tests: they assert
the property that holds when write_bias_model's output flows into read_bias_model.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.calibration.ens_bias_repo import (
    init_ens_bias_schema,
    write_bias_model,
    read_bias_model,
)
from src.calibration.ens_error_model import current_gate_set_hash


CITY, SEASON, METRIC = "Shanghai", "MAM", "high"
LIVE_DV = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
PRIOR_DV = "tigge_mx2t6_local_calendar_day_max_v1"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_ens_bias_schema(c)
    yield c
    c.close()


def _write(conn, *, gate_set_hash, coverage_months, bias_c=-3.15):
    write_bias_model(
        conn,
        city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, prior_data_version=PRIOR_DV,
        posterior_bias_c=bias_c, posterior_sd_c=0.5,
        n_live=15, n_prior=54, weight_live=0.0,
        estimator="test",
        error_model_family="full_transport_v1",
        bias_c=bias_c, residual_sd_c=1.4, correction_strength=1.0,
        effective_bias_c=bias_c, authority="VERIFIED",
        gate_set_hash=gate_set_hash, coverage_months=coverage_months,
    )
    conn.commit()


def test_current_gate_set_hash_is_deterministic():
    assert current_gate_set_hash() == current_gate_set_hash()
    assert len(current_gate_set_hash()) == 16


def test_row_with_current_gate_hash_is_served(conn):
    cur = current_gate_set_hash()
    _write(conn, gate_set_hash=cur, coverage_months="3,4,5")
    row = read_bias_model(
        conn, city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, error_model_family="full_transport_v1",
        require_gate_set_hash=cur, target_month=4,
    )
    assert row is not None
    assert row["bias_c"] == pytest.approx(-3.15)


def test_stale_gate_hash_row_is_rejected(conn):
    # Row fit under a superseded gate set (the pre-MIN_PAIRED_N contamination class).
    _write(conn, gate_set_hash="STALEHASH00000000", coverage_months="3,4,5")
    row = read_bias_model(
        conn, city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, error_model_family="full_transport_v1",
        require_gate_set_hash=current_gate_set_hash(), target_month=4,
    )
    assert row is None, "stale-gate-set row must be rejected at read time"


def test_null_gate_hash_row_is_rejected_when_required(conn):
    # Pre-antibody row (NULL gate_set_hash) must fail closed when a hash is required.
    _write(conn, gate_set_hash=None, coverage_months="3,4,5")
    row = read_bias_model(
        conn, city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, error_model_family="full_transport_v1",
        require_gate_set_hash=current_gate_set_hash(),
    )
    assert row is None, "NULL gate_set_hash must fail closed when a hash is required"


def test_month_outside_coverage_is_rejected(conn):
    # Row covers only March (3) but is labeled DJF-ish; applying to month 4 must reject.
    cur = current_gate_set_hash()
    _write(conn, gate_set_hash=cur, coverage_months="3")
    row = read_bias_model(
        conn, city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, error_model_family="full_transport_v1",
        require_gate_set_hash=cur, target_month=4,
    )
    assert row is None, "month outside declared coverage_months must be rejected"


def test_month_inside_coverage_is_served(conn):
    cur = current_gate_set_hash()
    _write(conn, gate_set_hash=cur, coverage_months="3,4,5")
    row = read_bias_model(
        conn, city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, error_model_family="full_transport_v1",
        require_gate_set_hash=cur, target_month=5,
    )
    assert row is not None


def test_empty_coverage_is_noop_guard(conn):
    # Empty coverage_months = no declared scope → month guard is a no-op (row served).
    cur = current_gate_set_hash()
    _write(conn, gate_set_hash=cur, coverage_months="")
    row = read_bias_model(
        conn, city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, error_model_family="full_transport_v1",
        require_gate_set_hash=cur, target_month=7,
    )
    assert row is not None, "empty coverage must not block (no declared scope)"


def test_backward_compat_no_hash_required_serves_row(conn):
    # When require_gate_set_hash is NOT supplied, behaviour is unchanged (F4 filter only).
    _write(conn, gate_set_hash="anything", coverage_months="3,4,5")
    row = read_bias_model(
        conn, city=CITY, season=SEASON, metric=METRIC,
        live_data_version=LIVE_DV, error_model_family="full_transport_v1",
    )
    assert row is not None, "no gate-hash requirement → unchanged F4 behaviour"
