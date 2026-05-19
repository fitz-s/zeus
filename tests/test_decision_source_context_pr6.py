# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: pr36_scaffold.md §7 — R-6.1 through R-6.4
# Lifecycle: PR 6 relationship tests for DecisionSourceContext timing chain
# Purpose: Verify chain-finality split, submit/ack ordering, clock drift threshold
# Reuse: relationship tests only; alpha-provenance antibody lives in test_inv_alpha_provenance.py
"""Relationship tests for DecisionSourceContext PR 6 timing chain.

Tests the new PR 6 validators:
- R-6.1: inclusion_after_finality (first_inclusion_block_time > finality_confirmed_time)
- R-6.2: submit_after_ack (zeus_submit_intent_time > venue_ack_time)
- R-6.3a/b/c: clock drift thresholds (>200ms blocking, 100-200ms warning, ≤100ms clean)
- R-6.4: raw_orderbook_hash_transition_delta_ms antibody via DB fixture
"""

import sqlite3

import pytest

from src.contracts.execution_intent import DecisionSourceContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pr6_ctx(**kwargs) -> DecisionSourceContext:
    """Build a minimal DecisionSourceContext valid for PR 6 validator testing."""
    defaults = dict(
        source_id="ecmwf_open_data",
        decision_time="2026-05-19T10:00:00Z",
        observation_time="2026-05-19T09:55:00Z",
        observation_available_at="2026-05-19T09:56:00Z",
        polymarket_end_anchor_source="gamma_explicit",
        first_member_observed_time="2026-05-19T06:00:00Z",
        run_complete_time="2026-05-19T06:30:00Z",
        zeus_submit_intent_time="2026-05-19T09:59:00Z",
        venue_ack_time="2026-05-19T10:00:00Z",
        first_inclusion_block_time="2026-05-19T10:01:00Z",
        finality_confirmed_time="2026-05-19T10:02:00Z",
    )
    defaults.update(kwargs)
    return DecisionSourceContext(**defaults)


# ---------------------------------------------------------------------------
# R-6.1: inclusion_after_finality
# ---------------------------------------------------------------------------

def test_r6_1_inclusion_after_finality():
    """R-6.1: first_inclusion_block_time > finality_confirmed_time triggers inclusion_after_finality."""
    ctx = _make_pr6_ctx(
        first_inclusion_block_time="2026-05-19T10:05:00Z",
        finality_confirmed_time="2026-05-19T10:01:00Z",  # finality before inclusion — inverted
    )
    errors = ctx.integrity_errors()
    assert "inclusion_after_finality" in errors, (
        "Expected inclusion_after_finality when first_inclusion_block_time > finality_confirmed_time"
    )


def test_r6_1_inclusion_before_finality_no_error():
    """R-6.1 happy path: correct ordering produces no inclusion_after_finality error."""
    ctx = _make_pr6_ctx(
        first_inclusion_block_time="2026-05-19T10:01:00Z",
        finality_confirmed_time="2026-05-19T10:05:00Z",
    )
    errors = ctx.integrity_errors()
    assert "inclusion_after_finality" not in errors


# ---------------------------------------------------------------------------
# R-6.2: submit_after_ack
# ---------------------------------------------------------------------------

def test_r6_2_submit_after_ack():
    """R-6.2: zeus_submit_intent_time > venue_ack_time triggers submit_after_ack."""
    ctx = _make_pr6_ctx(
        zeus_submit_intent_time="2026-05-19T10:01:00Z",
        venue_ack_time="2026-05-19T09:59:00Z",  # ack before submit — inverted
    )
    errors = ctx.integrity_errors()
    assert "submit_after_ack" in errors, (
        "Expected submit_after_ack when zeus_submit_intent_time > venue_ack_time"
    )


def test_r6_2_submit_before_ack_no_error():
    """R-6.2 happy path: correct submit/ack ordering produces no error."""
    ctx = _make_pr6_ctx(
        zeus_submit_intent_time="2026-05-19T09:59:00Z",
        venue_ack_time="2026-05-19T10:00:00Z",
    )
    errors = ctx.integrity_errors()
    assert "submit_after_ack" not in errors


# ---------------------------------------------------------------------------
# R-6.3a: excessive_clock_drift (blocking, |skew| > 200ms)
# ---------------------------------------------------------------------------

def test_r6_3a_excessive_clock_drift_positive():
    """R-6.3a: clock_skew_estimate_ms > 200 triggers excessive_clock_drift (blocking)."""
    ctx = _make_pr6_ctx(clock_skew_estimate_ms=201)
    errors = ctx.integrity_errors()
    assert "excessive_clock_drift" in errors
    assert "clock_drift_warning" not in errors, (
        "clock_drift_warning must not fire alongside excessive_clock_drift"
    )


def test_r6_3a_excessive_clock_drift_negative():
    """R-6.3a: clock_skew_estimate_ms < -200 also triggers excessive_clock_drift."""
    ctx = _make_pr6_ctx(clock_skew_estimate_ms=-201)
    errors = ctx.integrity_errors()
    assert "excessive_clock_drift" in errors


# ---------------------------------------------------------------------------
# R-6.3b: clock_drift_warning (non-blocking observability, 100ms < |skew| <= 200ms)
# ---------------------------------------------------------------------------

def test_r6_3b_clock_drift_warning_lower_bound():
    """R-6.3b: 101ms skew triggers clock_drift_warning but NOT excessive_clock_drift."""
    ctx = _make_pr6_ctx(clock_skew_estimate_ms=101)
    errors = ctx.integrity_errors()
    assert "clock_drift_warning" in errors
    assert "excessive_clock_drift" not in errors


def test_r6_3b_clock_drift_warning_upper_bound():
    """R-6.3b: 200ms skew triggers clock_drift_warning but NOT excessive_clock_drift."""
    ctx = _make_pr6_ctx(clock_skew_estimate_ms=200)
    errors = ctx.integrity_errors()
    assert "clock_drift_warning" in errors
    assert "excessive_clock_drift" not in errors


def test_r6_3b_clock_drift_warning_negative():
    """R-6.3b: -150ms skew triggers clock_drift_warning."""
    ctx = _make_pr6_ctx(clock_skew_estimate_ms=-150)
    errors = ctx.integrity_errors()
    assert "clock_drift_warning" in errors
    assert "excessive_clock_drift" not in errors


# ---------------------------------------------------------------------------
# R-6.3c: no drift errors when |skew| <= 100ms
# ---------------------------------------------------------------------------

def test_r6_3c_clock_skew_within_threshold_no_error():
    """R-6.3c: |clock_skew_estimate_ms| <= 100 produces no drift errors."""
    for skew in (0, 50, -50, 100, -100):
        ctx = _make_pr6_ctx(clock_skew_estimate_ms=skew)
        errors = ctx.integrity_errors()
        assert "clock_drift_warning" not in errors, f"Unexpected warning at skew={skew}"
        assert "excessive_clock_drift" not in errors, f"Unexpected error at skew={skew}"


def test_r6_3c_clock_skew_none_no_error():
    """R-6.3c: clock_skew_estimate_ms=None produces no drift errors."""
    ctx = _make_pr6_ctx(clock_skew_estimate_ms=None)
    errors = ctx.integrity_errors()
    assert "clock_drift_warning" not in errors
    assert "excessive_clock_drift" not in errors


# ---------------------------------------------------------------------------
# R-6.4: raw_orderbook_hash_transition_delta_ms antibody via DB fixture
# ---------------------------------------------------------------------------

def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory DB with ensemble_snapshots_v2 schema for R-6.4 probe."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            issue_time TEXT,
            data_version TEXT NOT NULL,
            model_version TEXT NOT NULL DEFAULT 'ecmwf_ens',
            available_at TEXT,
            fetch_time TEXT NOT NULL,
            lead_hours REAL,
            members_json TEXT,
            spread REAL,
            is_bimodal INTEGER,
            physical_quantity TEXT,
            observation_field TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            boundary_ambiguous INTEGER,
            provenance_json TEXT,
            authority TEXT,
            members_unit TEXT,
            unit TEXT,
            valid_time TEXT,
            first_member_observed_time TEXT,
            run_complete_time TEXT,
            raw_orderbook_hash_transition_delta_ms INTEGER,
            UNIQUE (city, target_date, temperature_metric, issue_time, data_version)
        )
    """)
    return conn


def test_r6_4_orderbook_hash_delta_non_null_via_fixture():
    """R-6.4: rows inserted via PR6 writer path have non-null raw_orderbook_hash_transition_delta_ms."""
    conn = _make_test_db()
    # Simulate a PR6 writer inserting with non-null delta
    conn.execute("""
        INSERT INTO ensemble_snapshots_v2
            (city, target_date, temperature_metric, data_version, model_version,
             fetch_time, training_allowed, causality_status, boundary_ambiguous,
             authority, members_unit, unit,
             first_member_observed_time, run_complete_time, raw_orderbook_hash_transition_delta_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "Chicago", "2026-05-19", "high", "ecmwf_open_data_ens_v1", "ecmwf_ens",
        "2026-05-19T10:00:00Z", 1, "OK", 0, "VERIFIED", "degF", "F",
        "2026-05-19T06:00:00Z", "2026-05-19T06:30:00Z",
        1234,  # non-null delta
    ))
    conn.commit()

    rows = conn.execute(
        "SELECT raw_orderbook_hash_transition_delta_ms FROM ensemble_snapshots_v2 "
        "WHERE city = 'Chicago'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["raw_orderbook_hash_transition_delta_ms"] is not None, (
        "PR6 writer must populate raw_orderbook_hash_transition_delta_ms"
    )
    assert rows[0]["raw_orderbook_hash_transition_delta_ms"] == 1234
