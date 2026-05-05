# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1C/phase.json
"""Relationship tests: harvester learning-pair write authority gates.

T1C-LEARNING-AUTHORITY-GATE: maybe_write_learning_pair() refuses to call
harvest_settlement() unless source_model_version is non-empty AND
snapshot_training_allowed is True.

T1C-LIVE-PRAW-NOT-TRAINING-DATA: even when authority is nominally present,
if _is_training_forecast_source() returns False (live/non-TIGGE source),
maybe_write_learning_pair() returns 0 pairs with reason=live_praw_no_training_lineage.

T1C-LEARNING-AUTHORITY-GATE (existing guard): harvest_settlement() returns 0 when
forecast_issue_time is missing with p_raw_vector present, and now emits counter.

Tests:
  T1: (smv=set, training=True) -> pairs written, no counter
  T2: (smv=None, training=True) -> 0 pairs, reason=missing_source_model_version_or_lineage
  T3: (smv=set, training=False) -> 0 pairs, reason=missing_source_model_version_or_lineage
  T4: (smv=None, training=False) -> 0 pairs, reason=missing_source_model_version_or_lineage
  T5: live-praw (openmeteo) source -> 0 pairs, reason=live_praw_no_training_lineage
  T6: missing forecast_issue_time in harvest_settlement -> 0 pairs, reason=missing_forecast_issue_time
  T7: parametrize all authority cases together
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from src.config import City
from src.execution.harvester import (
    _is_training_forecast_source,
    harvest_settlement,
    maybe_write_learning_pair,
)
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema

COUNTER_EVENT = "harvester_learning_write_blocked_total"
HARVESTER_LOGGER = "src.execution.harvester"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_city(name: str = "testcity") -> City:
    return City(
        name=name,
        lat=41.878,
        lon=-87.630,
        timezone="America/Chicago",
        settlement_unit="F",
        cluster="north",
        wu_station="KORD",
        settlement_source="KORD",
        country_code="US",
        settlement_source_type="wu_icao",
    )


@pytest.fixture()
def shared_conn():
    """In-memory shared DB with full schema (calibration_pairs + v2 tables)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    conn.commit()
    yield conn
    conn.close()


def _make_context(
    *,
    source_model_version: Optional[str] = "tigge_ens_v3",
    snapshot_training_allowed: bool = True,
    snapshot_learning_ready: bool = True,
    temperature_metric: str = "high",
    p_raw_vector: Optional[list] = None,
    issue_time: str = "2026-05-01T00:00:00Z",
    available_at: str = "2026-05-01T06:00:00Z",
    lead_days: float = 3.0,
    forecast_source: str = "tigge",
) -> dict:
    return {
        "source_model_version": source_model_version,
        "snapshot_training_allowed": snapshot_training_allowed,
        "snapshot_learning_ready": snapshot_learning_ready,
        "temperature_metric": temperature_metric,
        "p_raw_vector": p_raw_vector or [0.2, 0.5, 0.3],
        "issue_time": issue_time,
        "available_at": available_at,
        "lead_days": lead_days,
        "forecast_source": forecast_source,
        "decision_snapshot_id": None,
        "snapshot_causality_status": "OK",
    }


_ALL_LABELS = ["<30°F", "30-35°F", "35-40°F"]
_WINNING_LABEL = "30-35°F"
_TARGET_DATE = "2026-05-01"


# ---------------------------------------------------------------------------
# T1: valid authority writes pairs
# ---------------------------------------------------------------------------

def test_T1_valid_authority_writes_pairs(shared_conn, caplog):
    """With valid tigge source_model_version and training=True, pairs are written."""
    city = _make_city("auth_city")
    ctx = _make_context(source_model_version="tigge_ens_v3", snapshot_training_allowed=True)

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = maybe_write_learning_pair(
            shared_conn, city, _TARGET_DATE, _WINNING_LABEL, _ALL_LABELS,
            ctx, temperature_metric="high",
        )

    # tigge source with training=True should produce 3 pairs (one per bin with p_raw)
    assert n == 3
    # No blocked counter should be emitted
    blocked = [r for r in caplog.records if COUNTER_EVENT in r.message]
    assert blocked == [], f"Unexpected counter emit: {[r.message for r in blocked]}"


# ---------------------------------------------------------------------------
# T2: missing source_model_version blocks
# ---------------------------------------------------------------------------

def test_T2_missing_source_model_version_blocks(shared_conn, caplog):
    """None source_model_version -> 0 pairs, counter=missing_source_model_version_or_lineage."""
    city = _make_city("no_smv_city")
    ctx = _make_context(source_model_version=None, snapshot_training_allowed=True)

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = maybe_write_learning_pair(
            shared_conn, city, _TARGET_DATE, _WINNING_LABEL, _ALL_LABELS,
            ctx, temperature_metric="high",
        )

    assert n == 0
    reasons = [r.message for r in caplog.records if COUNTER_EVENT in r.message]
    assert any("missing_source_model_version_or_lineage" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# T3: training=False blocks even when source_model_version is set
# ---------------------------------------------------------------------------

def test_T3_training_false_blocks(shared_conn, caplog):
    """snapshot_training_allowed=False -> 0 pairs, counter=missing_source_model_version_or_lineage."""
    city = _make_city("no_train_city")
    ctx = _make_context(
        source_model_version="tigge_ens_v3",
        snapshot_training_allowed=False,
        snapshot_learning_ready=False,
    )

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = maybe_write_learning_pair(
            shared_conn, city, _TARGET_DATE, _WINNING_LABEL, _ALL_LABELS,
            ctx, temperature_metric="high",
        )

    assert n == 0
    reasons = [r.message for r in caplog.records if COUNTER_EVENT in r.message]
    assert any("missing_source_model_version_or_lineage" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# T4: both missing blocks
# ---------------------------------------------------------------------------

def test_T4_both_missing_blocks(shared_conn, caplog):
    """None smv + training=False -> 0 pairs, counter emitted once."""
    city = _make_city("both_missing_city")
    ctx = _make_context(
        source_model_version=None,
        snapshot_training_allowed=False,
        snapshot_learning_ready=False,
    )

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = maybe_write_learning_pair(
            shared_conn, city, _TARGET_DATE, _WINNING_LABEL, _ALL_LABELS,
            ctx, temperature_metric="high",
        )

    assert n == 0
    reasons = [r.message for r in caplog.records if COUNTER_EVENT in r.message]
    assert len(reasons) >= 1
    assert any("missing_source_model_version_or_lineage" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# T5: live-praw (openmeteo) blocked with live_praw_no_training_lineage
# ---------------------------------------------------------------------------

def test_T5_live_praw_blocked(shared_conn, caplog):
    """openmeteo source with training=True -> 0 pairs, reason=live_praw_no_training_lineage.

    T1C-LIVE-PRAW-NOT-TRAINING-DATA: Open-Meteo is not in _TRAINING_FORECAST_SOURCES
    (tigge, ecmwf_ens), so maybe_write_learning_pair blocks before calling harvest_settlement.
    """
    city = _make_city("openmeteo_city")
    ctx = _make_context(
        source_model_version="openmeteo_ecmwf_ifs025_live_v1",
        snapshot_training_allowed=True,
        snapshot_learning_ready=True,
        forecast_source="openmeteo",
    )

    # Sanity: confirm _is_training_forecast_source returns False for this version
    assert not _is_training_forecast_source("openmeteo_ecmwf_ifs025_live_v1")

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = maybe_write_learning_pair(
            shared_conn, city, _TARGET_DATE, _WINNING_LABEL, _ALL_LABELS,
            ctx, temperature_metric="high",
        )

    assert n == 0
    reasons = [r.message for r in caplog.records if COUNTER_EVENT in r.message]
    assert any("live_praw_no_training_lineage" in r for r in reasons), reasons

    # No pairs written to DB
    pair_count = shared_conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE city = ?",
        (city.name,),
    ).fetchone()[0]
    assert pair_count == 0


# ---------------------------------------------------------------------------
# T6: existing missing-issue-time guard in harvest_settlement now emits counter
# ---------------------------------------------------------------------------

def test_T6_missing_issue_time_emits_counter(shared_conn, caplog):
    """harvest_settlement() with p_raw_vector but no forecast_issue_time returns 0
    and emits harvester_learning_write_blocked_total{reason=missing_forecast_issue_time}.
    This preserves the pre-T1C guard at lines 1597-1603 but adds the counter.
    """
    city = _make_city("issue_time_city")

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = harvest_settlement(
            shared_conn,
            city,
            target_date=_TARGET_DATE,
            winning_bin_label=_WINNING_LABEL,
            bin_labels=_ALL_LABELS,
            p_raw_vector=[0.2, 0.5, 0.3],
            lead_days=3.0,
            forecast_issue_time=None,   # missing — should trigger the guard
            forecast_available_at="2026-05-01T06:00:00Z",
            source_model_version="tigge_ens_v3",
            snapshot_training_allowed=True,
            temperature_metric="high",
        )

    assert n == 0
    reasons = [r.message for r in caplog.records if COUNTER_EVENT in r.message]
    assert any("missing_forecast_issue_time" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# T7: parametrized authority matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source_model_version,snapshot_training_allowed,exp_pairs,exp_reason", [
    # Only path that writes: tigge source + training=True
    ("tigge_ens_v3",                True,  3,    None),
    # Missing smv
    (None,                          True,  0,    "missing_source_model_version_or_lineage"),
    # Empty smv
    ("",                            True,  0,    "missing_source_model_version_or_lineage"),
    # training=False
    ("tigge_ens_v3",                False, 0,    "missing_source_model_version_or_lineage"),
    # Both missing
    (None,                          False, 0,    "missing_source_model_version_or_lineage"),
])
def test_T7_parametrized_authority_matrix(
    shared_conn, caplog, source_model_version, snapshot_training_allowed, exp_pairs, exp_reason
):
    """Parametrized authority matrix for maybe_write_learning_pair()."""
    city = _make_city(f"matrix_{source_model_version or 'none'}_{snapshot_training_allowed}")
    ctx = _make_context(
        source_model_version=source_model_version,
        snapshot_training_allowed=snapshot_training_allowed,
        snapshot_learning_ready=snapshot_training_allowed,
    )

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = maybe_write_learning_pair(
            shared_conn, city, _TARGET_DATE, _WINNING_LABEL, _ALL_LABELS,
            ctx, temperature_metric="high",
        )

    if exp_pairs == 0:
        assert n == 0
        if exp_reason:
            reasons = [r.message for r in caplog.records if COUNTER_EVENT in r.message]
            assert any(exp_reason in r for r in reasons), \
                f"Expected reason {exp_reason!r} not found in: {reasons}"
    else:
        assert n == exp_pairs


# ---------------------------------------------------------------------------
# T8: ecmwf_ens source (also in training allowlist) writes pairs
# ---------------------------------------------------------------------------

def test_T8_ecmwf_ens_source_writes_pairs(shared_conn, caplog):
    """ecmwf_ens is in _TRAINING_FORECAST_SOURCES; pairs should be written."""
    city = _make_city("ecmwf_city")
    ctx = _make_context(
        source_model_version="ecmwf_ens_tigge_v2",
        snapshot_training_allowed=True,
        forecast_source="ecmwf_ens",
    )

    assert _is_training_forecast_source("ecmwf_ens_tigge_v2")

    with caplog.at_level(logging.WARNING, logger=HARVESTER_LOGGER):
        n = maybe_write_learning_pair(
            shared_conn, city, _TARGET_DATE, _WINNING_LABEL, _ALL_LABELS,
            ctx, temperature_metric="high",
        )

    assert n == 3
    blocked = [r for r in caplog.records if COUNTER_EVENT in r.message]
    assert blocked == []
