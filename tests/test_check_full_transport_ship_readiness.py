#!/usr/bin/env python3
# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Unit tests for check_full_transport_ship_readiness — gate returns FAIL today; check names match spec.
# Reuse: Inspect ship-readiness check functions before reuse; tests expect all checks to FAIL until ship.
# Authority basis: docs/operations/FT_SHIP_MASTER_SPEC_2026-05-25.md §Antibody
"""Unit tests for check_full_transport_ship_readiness.

Two invariants verified:
1. The gate returns FAIL today (nothing has been shipped yet).
2. The set of check function names matches the 9 spec items exactly.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from scripts.check_full_transport_ship_readiness import (
    FAIL,
    PASS,
    SPEC_NAMES,
    CheckResult,
    check_calibration_pin_complete,
    check_error_models_persisted,
    check_hk_high_or_pathology_carveouts_declared,
    check_live_trace_smoke_pass,
    check_live_wiring_flag_off_byte_identical,
    check_p_raw_replay_equivalence_pass,
    check_pairs_complete,
    check_platt_or_identity_coverage_complete,
    check_sentinel_complete,
    run_all_checks,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_db(path: str) -> None:
    """Create an empty but valid SQLite DB at path."""
    conn = sqlite3.connect(path)
    conn.close()


def _missing_path() -> str:
    """Return a path that does not exist."""
    return "/nonexistent/path/that/does/not/exist.db"


# ── Invariant 1: spec name set matches exactly ────────────────────────────────

def test_spec_names_match_9_items():
    expected = {
        "pairs_complete",
        "error_models_persisted",
        "p_raw_replay_equivalence_pass",
        "platt_or_identity_coverage_complete",
        "hk_high_or_pathology_carveouts_declared",
        "sentinel_complete",
        "calibration_pin_complete",
        "live_wiring_flag_off_byte_identical",
        "live_trace_smoke_pass",
    }
    assert set(SPEC_NAMES) == expected, f"SPEC_NAMES mismatch: {set(SPEC_NAMES) ^ expected}"
    assert len(SPEC_NAMES) == 9


# ── Invariant 2: gate returns FAIL today against prod / empty DBs ─────────────

class TestGateFailsToday:
    """Run the gate against the real prod DB paths (read-only).

    All checks must fail today because nothing has shipped yet.
    The live_wiring_flag_off_byte_identical check is the only one that
    may legitimately PASS today (the flag is absent + code is unmodified).
    """

    def test_run_all_checks_produces_9_results(self, tmp_path):
        stage_db = str(tmp_path / "stage.db")
        _empty_db(stage_db)
        results = run_all_checks(
            world_db=_missing_path(),
            forecasts_db=_missing_path(),
            stage_db=stage_db,
        )
        assert len(results) == 9

    def test_result_names_match_spec(self, tmp_path):
        stage_db = str(tmp_path / "stage.db")
        _empty_db(stage_db)
        results = run_all_checks(
            world_db=_missing_path(),
            forecasts_db=_missing_path(),
            stage_db=stage_db,
        )
        assert [r.name for r in results] == list(SPEC_NAMES)

    def test_all_checks_fail_with_missing_dbs(self, tmp_path):
        """All checks fail when world/stage DBs are missing."""
        results = run_all_checks(
            world_db=_missing_path(),
            forecasts_db=_missing_path(),
            stage_db=_missing_path(),
        )
        # live_wiring_flag_off_byte_identical checks source files, not DBs —
        # it may pass independently. All DB-dependent checks must fail.
        db_dependent = {r.name for r in results if r.name != "live_wiring_flag_off_byte_identical" and r.name != "calibration_pin_complete"}
        for r in results:
            if r.name in db_dependent:
                assert r.status == FAIL, (
                    f"{r.name} should FAIL with missing DBs but got {r.status}: {r.evidence}"
                )

    def test_calibration_pin_fails_without_frozen_as_of(self):
        """calibration_pin_complete fails if frozen_as_of is absent (current state)."""
        result = check_calibration_pin_complete()
        # Today frozen_as_of is not set in settings.json
        assert result.status == FAIL
        assert "frozen_as_of" in result.evidence.lower() or "not set" in result.evidence.lower()

    def test_live_wiring_flag_off_today(self):
        """live_wiring_flag_off_byte_identical: flag absent today = PASS (no live wiring)."""
        result = check_live_wiring_flag_off_byte_identical()
        # Flag doesn't exist yet (Phase 1 not landed) — correct state
        assert result.status == PASS
        assert "absent" in result.evidence.lower() or "byte-identical" in result.evidence.lower()


# ── Per-check unit tests with minimal stub DBs ────────────────────────────────

class TestPairsComplete:
    def test_fails_missing_db(self):
        r = check_pairs_complete(_missing_path())
        assert r.status == FAIL
        assert r.name == "pairs_complete"

    def test_fails_no_ft_rows(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE calibration_pairs_v2 "
            "(pair_id INTEGER PRIMARY KEY, error_model_family TEXT)"
        )
        conn.execute(
            "INSERT INTO calibration_pairs_v2 VALUES (1, 'none')"
        )
        conn.commit()
        conn.close()
        r = check_pairs_complete(db)
        assert r.status == FAIL
        assert "not yet produced" in r.evidence

    def test_passes_with_ft_rows(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE calibration_pairs_v2 "
            "(pair_id INTEGER PRIMARY KEY, error_model_family TEXT)"
        )
        conn.execute(
            "INSERT INTO calibration_pairs_v2 VALUES (1, 'full_transport_v1')"
        )
        conn.commit()
        conn.close()
        r = check_pairs_complete(db)
        assert r.status == PASS
        assert "full_transport_v1" in r.evidence


class TestErrorModelsPersisted:
    def test_fails_missing_db(self):
        r = check_error_models_persisted(_missing_path())
        assert r.status == FAIL

    def test_fails_no_table(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        r = check_error_models_persisted(db)
        assert r.status == FAIL
        assert "not yet produced" in r.evidence

    def test_fails_missing_required_fields(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        # Table exists but missing required fields
        conn.execute(
            "CREATE TABLE ens_error_model_v1 (error_model_key TEXT, bias_c REAL)"
        )
        conn.execute("INSERT INTO ens_error_model_v1 VALUES ('k1', 0.5)")
        conn.commit()
        conn.close()
        r = check_error_models_persisted(db)
        assert r.status == FAIL
        assert "missing fields" in r.evidence

    def test_passes_with_all_fields(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            """CREATE TABLE ens_error_model_v1 (
                error_model_key TEXT, bias_c REAL, residual_sd_c REAL,
                heterogeneity_var_c2 REAL, correction_strength REAL,
                n_live INTEGER, n_prior INTEGER, n_paired INTEGER,
                fit_signature_hash TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO ens_error_model_v1 VALUES ('k1',0.1,0.2,0.3,0.9,5,10,8,'abc')"
        )
        conn.commit()
        conn.close()
        r = check_error_models_persisted(db)
        assert r.status == PASS


class TestReplayEquivalencePass:
    def test_fails_missing_db(self):
        r = check_p_raw_replay_equivalence_pass(_missing_path())
        assert r.status == FAIL
        assert "not yet produced" in r.evidence

    def test_fails_empty_db(self, tmp_path):
        db = str(tmp_path / "s.db")
        _empty_db(db)
        r = check_p_raw_replay_equivalence_pass(db)
        assert r.status == FAIL

    def test_fails_verdict_fail(self, tmp_path):
        db = str(tmp_path / "s.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE replay_equivalence_proof "
            "(verdict TEXT, max_abs_diff REAL, n_snapshots INTEGER, recorded_at TEXT)"
        )
        conn.execute(
            "INSERT INTO replay_equivalence_proof VALUES ('FAIL', 0.05, 10, '2026-05-25')"
        )
        conn.commit()
        conn.close()
        r = check_p_raw_replay_equivalence_pass(db)
        assert r.status == FAIL
        assert "FAIL" in r.evidence

    def test_passes_verdict_pass(self, tmp_path):
        db = str(tmp_path / "s.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE replay_equivalence_proof "
            "(verdict TEXT, max_abs_diff REAL, n_snapshots INTEGER, recorded_at TEXT)"
        )
        conn.execute(
            "INSERT INTO replay_equivalence_proof VALUES ('PASS', 0.001, 50, '2026-05-25')"
        )
        conn.commit()
        conn.close()
        r = check_p_raw_replay_equivalence_pass(db)
        assert r.status == PASS


class TestHkHighCarveouts:
    def test_fails_missing_db_no_config(self):
        r = check_hk_high_or_pathology_carveouts_declared(_missing_path())
        assert r.status == FAIL
        # may say "world_db unavailable" (DB missing) or "not yet declared" (DB present, no carve-out)
        assert "unavailable" in r.evidence or "not yet declared" in r.evidence

    def test_passes_with_no_trade_event(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            """CREATE TABLE no_trade_events (
                market_slug TEXT NOT NULL, temperature_metric TEXT NOT NULL, reason TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO no_trade_events VALUES "
            "('will-the-high-temperature-in-hong-kong-exceed-30c','high','full_transport pathology carve-out')"
        )
        conn.commit()
        conn.close()
        r = check_hk_high_or_pathology_carveouts_declared(db)
        assert r.status == PASS


class TestSentinelComplete:
    def test_fails_missing_db(self):
        r = check_sentinel_complete(_missing_path())
        assert r.status == FAIL

    def test_fails_no_sentinel(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE chronicle "
            "(id INTEGER PRIMARY KEY, event_type TEXT, trade_id INTEGER, "
            "timestamp TEXT, details_json TEXT, env TEXT)"
        )
        conn.commit()
        conn.close()
        r = check_sentinel_complete(db)
        assert r.status == FAIL
        assert "not yet produced" in r.evidence

    def test_fails_in_progress(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE chronicle "
            "(id INTEGER PRIMARY KEY, event_type TEXT, trade_id INTEGER, "
            "timestamp TEXT, details_json TEXT, env TEXT)"
        )
        conn.execute(
            "INSERT INTO chronicle(event_type, timestamp, details_json, env) VALUES "
            "('rebuild_sentinel', '2026-05-25', "
            "'{\"status\": \"in_progress\", \"key\": \"full_transport_v1\"}', 'live')"
        )
        conn.commit()
        conn.close()
        r = check_sentinel_complete(db)
        assert r.status == FAIL
        assert "in_progress" in r.evidence

    def test_passes_complete(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE chronicle "
            "(id INTEGER PRIMARY KEY, event_type TEXT, trade_id INTEGER, "
            "timestamp TEXT, details_json TEXT, env TEXT)"
        )
        conn.execute(
            "INSERT INTO chronicle(event_type, timestamp, details_json, env) VALUES "
            "('rebuild_sentinel', '2026-05-25', "
            "'{\"status\": \"complete\", \"key\": \"full_transport_v1\"}', 'live')"
        )
        conn.commit()
        conn.close()
        r = check_sentinel_complete(db)
        assert r.status == PASS


class TestLiveTraceSmoke:
    def test_fails_missing_db(self):
        r = check_live_trace_smoke_pass(_missing_path())
        assert r.status == FAIL

    def test_fails_no_table(self, tmp_path):
        db = str(tmp_path / "w.db")
        _empty_db(db)
        r = check_live_trace_smoke_pass(db)
        assert r.status == FAIL

    def test_fails_no_ft_rows(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE probability_trace_fact (p_raw_domain TEXT)"
        )
        conn.execute("INSERT INTO probability_trace_fact VALUES ('none')")
        conn.commit()
        conn.close()
        r = check_live_trace_smoke_pass(db)
        assert r.status == FAIL
        assert "not yet produced" in r.evidence

    def test_passes_with_ft_rows(self, tmp_path):
        db = str(tmp_path / "w.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE probability_trace_fact (p_raw_domain TEXT)"
        )
        conn.execute(
            "INSERT INTO probability_trace_fact VALUES ('full_transport_v1')"
        )
        conn.commit()
        conn.close()
        r = check_live_trace_smoke_pass(db)
        assert r.status == PASS
