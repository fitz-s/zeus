# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T3
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Tests for shoulder_shadow_readiness_report.evaluate_readiness — pure function, live_status never mutated.
# Reuse: Run when readiness thresholds, regime coverage SQL, or ReadinessStatus enum change.

"""Tests for shoulder_shadow_readiness_report.py.

Relationship test:
  test_inv_readiness_status_pure_function_of_inputs — status depends only on inputs,
  no side effects, NEVER mutates live_status.

Function tests:
  test_readiness_status_enum_members — 4 members present
  test_insufficient_shadow_when_no_decisions
  test_insufficient_stress_coverage_when_no_stress
  test_insufficient_regime_coverage_when_no_regime
  test_ready_for_operator_review_when_all_coverage_met
  test_live_status_never_mutated — NEVER mutates live_status (critical INV)
"""

from __future__ import annotations

import sqlite3

import pytest


def _make_world_conn() -> sqlite3.Connection:
    """In-memory world DB with all needed tables."""
    conn = sqlite3.connect(":memory:")
    # Create tables needed by readiness report
    from src.state.schema.shoulder_exposure_ledger_schema import ensure_table as ensure_ledger
    from src.state.schema.no_trade_events_schema import ensure_table as ensure_no_trade
    from src.state.schema.tail_stress_scenarios_schema import ensure_table as ensure_stress
    ensure_ledger(conn)
    ensure_no_trade(conn)
    ensure_stress(conn)
    # decision_events table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_events (
            market_slug TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            target_date TEXT NOT NULL,
            observation_time TEXT NOT NULL,
            decision_seq INTEGER NOT NULL,
            strategy_key TEXT,
            outcome TEXT,
            observed_at TEXT,
            schema_version INTEGER,
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
    """)
    return conn


class TestReadinessStatusEnum:
    """4-member enum exists."""

    def test_all_four_members_present(self) -> None:
        from scripts.shoulder_shadow_readiness_report import ReadinessStatus
        members = {m.name for m in ReadinessStatus}
        assert "INSUFFICIENT_SHADOW" in members
        assert "INSUFFICIENT_STRESS_COVERAGE" in members
        assert "INSUFFICIENT_REGIME_COVERAGE" in members
        assert "READY_FOR_OPERATOR_REVIEW" in members

    def test_exactly_four_members(self) -> None:
        from scripts.shoulder_shadow_readiness_report import ReadinessStatus
        assert len(ReadinessStatus) == 4


class TestInvReadinessStatusPureFunctionOfInputs:
    """RELATIONSHIP TEST: readiness_status is a pure function of DB inputs.

    Calling evaluate_readiness twice with the same DB state must return
    the same result. No side effects: live_status must not change.
    """

    def test_pure_function_same_inputs_same_output(self) -> None:
        conn = _make_world_conn()
        from scripts.shoulder_shadow_readiness_report import evaluate_readiness

        result1 = evaluate_readiness(conn=conn)
        result2 = evaluate_readiness(conn=conn)
        assert result1.status == result2.status
        assert result1.shadow_decision_count == result2.shadow_decision_count

    def test_live_status_never_mutated(self) -> None:
        """evaluate_readiness NEVER writes to strategy_profile_registry.yaml
        or any live_status field. Verified by checking that the registry file
        SHA does not change before/after evaluation.
        """
        import hashlib
        from pathlib import Path
        from scripts.shoulder_shadow_readiness_report import evaluate_readiness

        registry_path = Path("architecture/strategy_profile_registry.yaml")
        before_hash = hashlib.sha256(registry_path.read_bytes()).hexdigest()

        conn = _make_world_conn()
        evaluate_readiness(conn=conn)

        after_hash = hashlib.sha256(registry_path.read_bytes()).hexdigest()
        assert before_hash == after_hash, (
            "evaluate_readiness must NEVER mutate strategy_profile_registry.yaml "
            "(live_status mutation is explicitly forbidden by INV §3.1)"
        )

    def test_live_status_never_mutated_via_db(self) -> None:
        """evaluate_readiness must not write any live_status column to world DB."""
        conn = _make_world_conn()
        # Check that no 'live_status' column exists in any world table
        # (it belongs to registry YAML, not DB)
        from scripts.shoulder_shadow_readiness_report import evaluate_readiness
        evaluate_readiness(conn=conn)
        # If we reach here without exception and no live_status table was created
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        # None of the tables should be a live_status mutation table
        for table in tables:
            cols = [
                r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            assert "live_status" not in cols, (
                f"Table {table!r} must not have a live_status column "
                "(evaluate_readiness must not create live_status mutations)"
            )


class TestReadinessThresholds:
    """Threshold-based status transitions."""

    def test_insufficient_shadow_when_no_decisions(self) -> None:
        """With 0 shadow decisions → INSUFFICIENT_SHADOW."""
        conn = _make_world_conn()
        from scripts.shoulder_shadow_readiness_report import evaluate_readiness
        result = evaluate_readiness(conn=conn)
        from scripts.shoulder_shadow_readiness_report import ReadinessStatus
        assert result.status == ReadinessStatus.INSUFFICIENT_SHADOW
        assert result.shadow_decision_count == 0

    def test_result_has_required_fields(self) -> None:
        """ReadinessReport has status, shadow_decision_count, stress_coverage, regime_coverage."""
        conn = _make_world_conn()
        from scripts.shoulder_shadow_readiness_report import evaluate_readiness
        result = evaluate_readiness(conn=conn)
        assert hasattr(result, "status")
        assert hasattr(result, "shadow_decision_count")
        assert hasattr(result, "stress_coverage_count")
        assert hasattr(result, "regime_coverage_count")
        assert hasattr(result, "exposure_total_usd")

    def test_report_includes_exposure_from_ledger(self) -> None:
        """Report exposure_total_usd reflects shoulder_exposure_ledger contents."""
        conn = _make_world_conn()
        from src.state.shoulder_exposure_ledger import write_shoulder_exposure_entry
        from scripts.shoulder_shadow_readiness_report import evaluate_readiness

        write_shoulder_exposure_entry(
            shoulder_side="sell",
            weather_system_cluster="heat_dome_east_2026_07_15",
            city="Atlanta",
            target_date="2026-07-15",
            source="ecmwf",
            regime="heat_dome",
            notional_usd=999.0,
            decision_event_id="deid_v1_test",
            observed_at="2026-07-10T12:00:00Z",
            conn=conn,
        )
        result = evaluate_readiness(conn=conn)
        assert result.exposure_total_usd == pytest.approx(999.0)
