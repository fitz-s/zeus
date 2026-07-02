# Created: 2026-07-02
# Last reused/audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 "input->q latency SLA (A2, 'THE metric')" — W0.2 packet (measure only, no gate).
"""Tests for src.data.latency_metrics: input->q_version wall-clock latency.

Latency = computed_at - source_cycle_time, using the columns forecast_posteriors
already persists per row (no new table). See src/data/latency_metrics.py module
docstring for the full design rationale and the dashboard query.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.data.latency_metrics import (
    LATENCY_QUERY,
    compute_latency_seconds,
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


def test_compute_latency_seconds_correct_arithmetic():
    source_cycle_time = "2026-07-02T00:00:00+00:00"
    computed_at = "2026-07-02T00:07:30+00:00"

    latency = compute_latency_seconds(
        source_cycle_time=source_cycle_time, computed_at=computed_at
    )

    assert latency == 450.0


def test_compute_latency_seconds_rejects_negative():
    # computed_at BEFORE source_cycle_time is unconstructable (the posterior
    # cannot be written before its own input existed) — surface it loudly
    # rather than silently returning a negative number a dashboard would plot.
    source_cycle_time = "2026-07-02T00:10:00+00:00"
    computed_at = "2026-07-02T00:00:00+00:00"

    import pytest

    with pytest.raises(ValueError):
        compute_latency_seconds(
            source_cycle_time=source_cycle_time, computed_at=computed_at
        )


def test_emit_materialization_latency_logs_and_returns_seconds(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="zeus.q_version_latency")

    latency = emit_materialization_latency(
        family_id="fam-shanghai-high",
        city="Shanghai",
        target_date="2026-07-02",
        temperature_metric="high",
        source_cycle_time="2026-07-02T00:00:00+00:00",
        computed_at="2026-07-02T00:05:00+00:00",
        posterior_id=42,
    )

    assert latency == 300.0
    assert any(
        "fam-shanghai-high" in record.message and "300" in record.message
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
    assert row["latency_seconds"] == pytest.approx(450.0)


def test_fetch_recent_latencies_tags_by_family():
    conn = _conn()
    _insert_posterior(
        conn,
        city="Shanghai",
        target_date="2026-07-02",
        metric="high",
        family_id="fam-a",
        source_cycle_time="2026-07-02T00:00:00+00:00",
        source_available_at="2026-07-02T00:01:00+00:00",
        computed_at="2026-07-02T00:02:00+00:00",
    )
    _insert_posterior(
        conn,
        city="Hong Kong",
        target_date="2026-07-02",
        metric="low",
        family_id="fam-b",
        source_cycle_time="2026-07-02T00:00:00+00:00",
        source_available_at="2026-07-02T00:01:00+00:00",
        computed_at="2026-07-02T00:20:00+00:00",
    )

    results = fetch_recent_latencies(conn)

    by_family = {row["family_id"]: row["latency_seconds"] for row in results}
    assert by_family["fam-a"] == pytest.approx(120.0)
    assert by_family["fam-b"] == pytest.approx(1200.0)
