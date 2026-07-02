# Created: 2026-07-02
# Last reused/audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "input->q latency SLA (A2, 'THE metric')" — W0.2 packet (measure only, no gate).
"""Tests for src.data.latency_metrics: input->q_version wall-clock latency, two variants.

latency_from_issue_seconds   = computed_at - source_cycle_time (provider-issued clock)
latency_from_arrival_seconds = computed_at - source_available_at (Zeus possession clock,
                                the A2 SLA target — excludes provider publication delay)

Both read straight off columns forecast_posteriors already persists per row (no new
table). See src/data/latency_metrics.py module docstring for the full rationale.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.data.latency_metrics import (
    LATENCY_QUERY,
    compute_arrival_latency_seconds,
    compute_issue_latency_seconds,
    emit_materialization_latency,
    fetch_recent_latencies,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    return conn


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    family_id: str,
    source_cycle_time: str,
    source_available_at: str,
    computed_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, posterior_method, family_id,
            runtime_layer, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 0)
        """,
        (
            "openmeteo_ecmwf_ifs9",
            "temperature",
            "v1",
            city,
            target_date,
            metric,
            source_cycle_time,
            source_available_at,
            computed_at,
            "{}",
            "openmeteo_ecmwf_ifs9_bayes_fusion",
            family_id,
        ),
    )
    conn.commit()


def test_compute_issue_latency_seconds_correct_arithmetic():
    source_cycle_time = "2026-07-02T00:00:00+00:00"
    computed_at = "2026-07-02T00:07:30+00:00"

    latency = compute_issue_latency_seconds(
        source_cycle_time=source_cycle_time, computed_at=computed_at
    )

    assert latency == 450.0


def test_compute_issue_latency_seconds_rejects_negative():
    # computed_at BEFORE source_cycle_time is unconstructable (the posterior
    # cannot be written before its own input existed) — surface it loudly
    # rather than silently returning a negative number a dashboard would plot.
    source_cycle_time = "2026-07-02T00:10:00+00:00"
    computed_at = "2026-07-02T00:00:00+00:00"

    with pytest.raises(ValueError):
        compute_issue_latency_seconds(
            source_cycle_time=source_cycle_time, computed_at=computed_at
        )


def test_compute_arrival_latency_seconds_correct_arithmetic():
    source_available_at = "2026-07-02T00:03:00+00:00"
    computed_at = "2026-07-02T00:07:30+00:00"

    latency = compute_arrival_latency_seconds(
        source_available_at=source_available_at, computed_at=computed_at
    )

    assert latency == 270.0


def test_compute_arrival_latency_seconds_rejects_negative():
    source_available_at = "2026-07-02T00:10:00+00:00"
    computed_at = "2026-07-02T00:00:00+00:00"

    with pytest.raises(ValueError):
        compute_arrival_latency_seconds(
            source_available_at=source_available_at, computed_at=computed_at
        )


def test_arrival_latency_is_shorter_than_issue_latency_when_source_available_at_is_later():
    # source_available_at (Zeus possession) is always >= source_cycle_time
    # (provider issue) in honest data — issue happens first, then Zeus fetches.
    # So latency_from_arrival must be <= latency_from_issue for the same
    # computed_at, never the reverse.
    source_cycle_time = "2026-07-02T00:00:00+00:00"
    source_available_at = "2026-07-02T00:05:00+00:00"
    computed_at = "2026-07-02T00:10:00+00:00"

    issue_latency = compute_issue_latency_seconds(
        source_cycle_time=source_cycle_time, computed_at=computed_at
    )
    arrival_latency = compute_arrival_latency_seconds(
        source_available_at=source_available_at, computed_at=computed_at
    )

    assert arrival_latency < issue_latency
    assert issue_latency == 600.0
    assert arrival_latency == 300.0


def test_emit_materialization_latency_logs_and_returns_both_variants(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="zeus.q_version_latency")

    latencies = emit_materialization_latency(
        family_id="fam-shanghai-high",
        city="Shanghai",
        target_date="2026-07-02",
        temperature_metric="high",
        source_cycle_time="2026-07-02T00:00:00+00:00",
        source_available_at="2026-07-02T00:02:00+00:00",
        computed_at="2026-07-02T00:05:00+00:00",
        posterior_id=42,
    )

    assert latencies == {
        "latency_from_issue_seconds": 300.0,
        "latency_from_arrival_seconds": 180.0,
    }
    assert any(
        "fam-shanghai-high" in record.message
        and "latency_from_issue_seconds=300" in record.message
        and "latency_from_arrival_seconds=180" in record.message
        for record in caplog.records
    )


def test_latency_query_matches_manual_arithmetic_from_persisted_row():
    conn = _conn()
    _insert_posterior(
        conn,
        city="Shanghai",
        target_date="2026-07-02",
        metric="high",
        family_id="fam-shanghai-high",
        source_cycle_time="2026-07-02T00:00:00+00:00",
        source_available_at="2026-07-02T00:01:00+00:00",
        computed_at="2026-07-02T00:07:30+00:00",
    )

    rows = conn.execute(LATENCY_QUERY).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["family_id"] == "fam-shanghai-high"
    assert row["latency_from_issue_seconds"] == pytest.approx(450.0)
    assert row["latency_from_arrival_seconds"] == pytest.approx(390.0)


def test_fetch_recent_latencies_tags_by_family_both_variants():
    conn = _conn()
    _insert_posterior(
        conn,
        city="Shanghai",
        target_date="2026-07-02",
        metric="high",
        family_id="fam-a",
        source_cycle_time="2026-07-02T00:00:00+00:00",
        source_available_at="2026-07-02T00:00:30+00:00",
        computed_at="2026-07-02T00:02:00+00:00",
    )
    _insert_posterior(
        conn,
        city="Hong Kong",
        target_date="2026-07-02",
        metric="low",
        family_id="fam-b",
        source_cycle_time="2026-07-02T00:00:00+00:00",
        source_available_at="2026-07-02T00:15:00+00:00",
        computed_at="2026-07-02T00:20:00+00:00",
    )

    results = fetch_recent_latencies(conn)

    by_family = {row["family_id"]: row for row in results}
    assert by_family["fam-a"]["latency_from_issue_seconds"] == pytest.approx(120.0)
    assert by_family["fam-a"]["latency_from_arrival_seconds"] == pytest.approx(90.0)
    assert by_family["fam-b"]["latency_from_issue_seconds"] == pytest.approx(1200.0)
    assert by_family["fam-b"]["latency_from_arrival_seconds"] == pytest.approx(300.0)
