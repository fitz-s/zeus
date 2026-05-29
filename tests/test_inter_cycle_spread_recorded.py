# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL Finding 8 — inter-cycle spread diagnostics
#   (additive, election byte-identical per operator decision rule)
"""Tests for TRIBUNAL Finding 8: inter-cycle spread diagnostics on ExecutableForecastReadResult.

Tests exercise the helpers and the full read path via a synthetic in-memory SQLite
database that mimics the `ensemble_snapshots` table schema.  They are self-contained
and do NOT require conftest.py or schema initialisation (safe with --noconftest).

Regression guard: elected snapshot_id must be byte-identical to pre-change result.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Unit tests for the helper _compute_inter_cycle_spread
# ---------------------------------------------------------------------------
from src.data.executable_forecast_reader import (
    ExecutableForecastReadResult,
    _compute_inter_cycle_spread,
    read_executable_forecast_snapshot,
)
from src.data.forecast_target_contract import ForecastTargetScope

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS ensemble_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    city TEXT,
    target_date TEXT,
    temperature_metric TEXT,
    dataset_id TEXT,
    source_id TEXT,
    source_transport TEXT,
    source_run_id TEXT,
    release_calendar_key TEXT,
    source_cycle_time TEXT,
    source_release_time TEXT,
    source_available_at TEXT,
    issue_time TEXT,
    valid_time TEXT,
    available_at TEXT,
    fetch_time TEXT,
    manifest_hash TEXT,
    members_unit TEXT,
    members_json TEXT,
    local_day_start_utc TEXT,
    step_horizon_hours REAL,
    first_member_observed_time TEXT,
    run_complete_time TEXT,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    contributes_to_target_extrema INTEGER DEFAULT 1,
    forecast_window_attribution_status TEXT DEFAULT 'CONTRIBUTING',
    boundary_ambiguous INTEGER DEFAULT 0,
    authority TEXT DEFAULT 'VERIFIED',
    causality_status TEXT DEFAULT 'OK',
    data_version TEXT
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


_COMMON: dict[str, Any] = dict(
    city="TestCity",
    target_date="2026-07-01",
    temperature_metric="max",
    dataset_id="v4",
    source_id="gfs",
    source_transport="ensemble_snapshots_db_reader",
    source_run_id="run-A",
    release_calendar_key="2026-07-01T12:00:00+00:00",
    source_release_time="2026-07-01T12:00:00+00:00",
    source_available_at="2026-07-01T13:00:00+00:00",
    issue_time="2026-07-01T12:00:00+00:00",
    valid_time="2026-07-02T00:00:00+00:00",
    available_at="2026-05-29T00:00:00+00:00",
    fetch_time="2026-07-01T12:30:00+00:00",
    manifest_hash="abc123",
    members_unit="celsius",
    local_day_start_utc="2026-07-01T00:00:00+00:00",
    step_horizon_hours=24.0,
    first_member_observed_time="2026-07-01T12:00:00+00:00",
    run_complete_time="2026-07-01T13:00:00+00:00",
    raw_orderbook_hash_transition_delta_ms=None,
    contributes_to_target_extrema=1,
    forecast_window_attribution_status="CONTRIBUTES",
    boundary_ambiguous=0,
    authority="VERIFIED",
    causality_status="OK",
    data_version="v4",
)


def _insert_snapshot(conn: sqlite3.Connection, snapshot_id: int, **overrides: Any) -> None:
    row = {**_COMMON, "snapshot_id": snapshot_id, **overrides}
    members = row.pop("members_json", json.dumps([20.0, 21.0, 22.0]))
    cols = list(row.keys()) + ["members_json"]
    vals = list(row.values()) + [members]
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO ensemble_snapshots ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    conn.commit()


def _scope() -> ForecastTargetScope:
    _now = datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)
    return ForecastTargetScope(
        city_id="test_city",
        city_name="TestCity",
        city_timezone="UTC",
        target_local_date=date(2026, 7, 1),
        temperature_metric="max",
        source_cycle_time=_now,
        data_version="v4",
        target_window_start_utc=_now,
        target_window_end_utc=datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC),
        required_step_hours=(24,),
        market_refs=(),
    )


# ---------------------------------------------------------------------------
# Unit: _compute_inter_cycle_spread
# ---------------------------------------------------------------------------


class TestComputeInterCycleSpread:
    def test_single_element_returns_zero(self):
        assert _compute_inter_cycle_spread(["2026-05-29T00:00:00+00:00"]) == 0.0

    def test_empty_returns_zero(self):
        assert _compute_inter_cycle_spread([]) == 0.0

    def test_identical_times_returns_zero(self):
        t = "2026-05-29T00:00:00+00:00"
        assert _compute_inter_cycle_spread([t, t, t]) == 0.0

    def test_known_spread(self):
        # Two times 12 hours apart → stdev = 6h = 21600s
        t0 = "2026-05-29T00:00:00+00:00"
        t1 = "2026-05-29T12:00:00+00:00"
        spread = _compute_inter_cycle_spread([t0, t1])
        assert abs(spread - 21600.0) < 1.0

    def test_none_values_skipped(self):
        spread = _compute_inter_cycle_spread([None, None])
        assert spread == 0.0

    def test_mixed_none_and_valid(self):
        t0 = "2026-05-29T00:00:00+00:00"
        spread = _compute_inter_cycle_spread([None, t0])
        assert spread == 0.0  # only one parseable value → effectively single


# ---------------------------------------------------------------------------
# Integration: read_executable_forecast_snapshot diagnostic fields
# ---------------------------------------------------------------------------


class TestInterCycleSpreadIntegration:
    def test_single_candidate_spread_is_zero(self):
        """Single snapshot → spread=0, count=1, election_reason=SOLE_CANDIDATE."""
        conn = _make_conn()
        _insert_snapshot(conn, 1, source_cycle_time="2026-05-29T00:00:00+00:00")
        result = read_executable_forecast_snapshot(
            conn, scope=_scope(), source_id="gfs", now_utc=None
        )
        assert result.ok, f"expected LIVE_ELIGIBLE, got {result.reason_code}"
        assert result.candidate_snapshot_count == 1
        assert result.candidate_snapshot_ids == [1]
        assert result.inter_cycle_spread == 0.0
        assert result.election_reason == "SOLE_CANDIDATE"

    def test_multiple_candidates_spread_positive(self):
        """Two snapshots with different cycle times → spread > 0, count=2."""
        conn = _make_conn()
        # snapshot 2 ranks first (later source_cycle_time, higher snapshot_id as tiebreak)
        _insert_snapshot(
            conn, 1, source_cycle_time="2026-05-29T00:00:00+00:00", source_run_id="run-A"
        )
        _insert_snapshot(
            conn, 2, source_cycle_time="2026-05-29T12:00:00+00:00", source_run_id="run-A"
        )
        result = read_executable_forecast_snapshot(
            conn, scope=_scope(), source_id="gfs", now_utc=None
        )
        assert result.ok, f"expected LIVE_ELIGIBLE, got {result.reason_code}"
        assert result.candidate_snapshot_count == 2
        assert result.inter_cycle_spread is not None and result.inter_cycle_spread > 0.0
        assert len(result.candidate_snapshot_ids) == 2  # type: ignore[arg-type]
        assert result.election_reason == "EXTREMA_RANK_TOP1_OF_2"

    def test_election_result_unchanged(self):
        """Elected snapshot_id must be identical to what LIMIT 1 alone would return.

        Regression guard: top-ranked row is snapshot with later source_cycle_time.
        """
        conn = _make_conn()
        _insert_snapshot(
            conn, 10, source_cycle_time="2026-05-29T00:00:00+00:00", source_run_id="run-A"
        )
        _insert_snapshot(
            conn, 20, source_cycle_time="2026-05-29T12:00:00+00:00", source_run_id="run-A"
        )
        result = read_executable_forecast_snapshot(
            conn, scope=_scope(), source_id="gfs", now_utc=None
        )
        # _EXTREMA_RANK_ORDER_BY: source_cycle_time DESC → snapshot 20 wins
        assert result.snapshot is not None
        assert result.snapshot.snapshot_id == 20, (
            f"election changed! got {result.snapshot.snapshot_id}, expected 20"
        )

    def test_back_compat_existing_fields_intact(self):
        """Existing fields on ExecutableForecastReadResult and snapshot are unchanged."""
        conn = _make_conn()
        _insert_snapshot(conn, 5, source_cycle_time="2026-05-29T06:00:00+00:00")
        result = read_executable_forecast_snapshot(
            conn, scope=_scope(), source_id="gfs", now_utc=None
        )
        assert result.status == "LIVE_ELIGIBLE"
        assert result.reason_code == "EXECUTABLE_FORECAST_READY"
        assert result.snapshot is not None
        # Core snapshot fields
        assert result.snapshot.city == "TestCity"
        assert result.snapshot.temperature_metric == "max"
        assert result.snapshot.snapshot_id == 5

    def test_blocked_result_has_none_diagnostics(self):
        """BLOCKED result (no rows) has None diagnostic fields — back-compat."""
        conn = _make_conn()
        # no rows inserted
        result = read_executable_forecast_snapshot(
            conn, scope=_scope(), source_id="gfs", now_utc=None
        )
        assert result.status == "BLOCKED"
        assert result.candidate_snapshot_count is None
        assert result.candidate_snapshot_ids is None
        assert result.inter_cycle_spread is None
        assert result.election_reason is None

    def test_diagnostic_fields_optional_with_defaults(self):
        """ExecutableForecastReadResult can be constructed without diagnostic fields."""
        r = ExecutableForecastReadResult(status="BLOCKED", reason_code="TEST")
        assert r.candidate_snapshot_count is None
        assert r.candidate_snapshot_ids is None
        assert r.inter_cycle_spread is None
        assert r.election_reason is None
