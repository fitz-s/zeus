# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/SESSION_CLOSURE_VERDICT.md §Part B
"""Tests for PromotionReadinessValidator.

Key invariants tested:
  T1: READY only when all three signals agree (tribunal PROMOTE + CI > breakeven + settlement PASS).
  T2: NOT_READY when any one signal fails (CI fail, tribunal HOLD/DEMOTE, settlement fail).
  T3: Validator emits recommendation only — no tier written, no DB row inserted.
  T4: operator_ref required (ValueError) when tier_target >= LIVE_PILOT_TINY.
  T5: Settlement gate N/A when requires_settlement_gate=False (never blocks non-settlement strategies).
  T6: Settlement gate explicit fail when city/metric missing and requires_settlement_gate=True.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.analysis.evidence_report import EvidenceReport
from src.analysis.promotion_readiness import (
    PromotionReadinessReport,
    PromotionReadinessValidator,
    ReadinessVerdict,
    SignalResult,
)
from src.contracts.evidence_tier import EvidenceTier
from src.state.db import init_schema
from tests.conftest import make_world_forecasts_pair


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_report(
    strategy_id: str = "shoulder_sell",
    tier_observed: EvidenceTier = EvidenceTier.SHADOW_PASS,
    n_settled: int = 10,
    n_wins: int = 7,
    ci_lower: float | None = 0.60,
    ci_upper: float | None = 0.80,
    breakeven_win_rate: float = 0.55,
) -> EvidenceReport:
    return EvidenceReport(
        strategy_id=strategy_id,
        tier_observed=tier_observed,
        n_decisions=n_settled,
        n_wins=n_wins,
        n_no_trades=0,
        n_settled=n_settled,
        mean_regret_usd=0.5,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        breakeven_win_rate=breakeven_win_rate,
    )


@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture()
def dual_db_pair(tmp_path):
    """Return (world_conn, forecasts_conn) with both schemas initialised."""
    world_conn, forecasts_conn = make_world_forecasts_pair(tmp_path)
    yield world_conn, forecasts_conn
    world_conn.close()
    forecasts_conn.close()


def _seed_coherent_verifications(
    conn: sqlite3.Connection,
    city: str,
    metric: str,
    count: int,
) -> None:
    """Insert `count` COHERENT rows into settlement_capture_verifications (forecasts DB)."""
    for i in range(count):
        conn.execute(
            """
            INSERT INTO settlement_capture_verifications
                (city, target_date, temperature_metric,
                 fact_known_time, source_published_time,
                 venue_resolved_time, redeemed_time,
                 coherence_verdict, incoherence_reason, evidence_tier)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'COHERENT', NULL, 'SHADOW_PASS')
            """,
            (
                city,
                f"2026-04-{i + 1:02d}",
                metric,
                "2026-04-01T10:00:00Z",
                "2026-04-01T10:01:00Z",
                "2026-04-01T10:02:00Z",
                "2026-04-01T10:03:00Z",
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# T1: READY only when all three signals agree
# ---------------------------------------------------------------------------

class TestReadyRequiresAllThreeSignals:

    def test_ready_when_all_signals_pass(self, dual_db_pair) -> None:
        """T1: READY when CI > breakeven, tribunal would PROMOTE, settlement gate passes."""
        world_conn, forecasts_conn = dual_db_pair
        _seed_coherent_verifications(forecasts_conn, "NYC", "high", 5)
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=True,
            coherent_threshold=5,
        )
        result = validator.assess(
            report,
            city="NYC",
            temperature_metric="high",
            conn=forecasts_conn,
            operator_ref="op-test-001",
        )
        assert result.verdict == ReadinessVerdict.READY
        assert result.tier_target == EvidenceTier.PAPER_COHORT  # SHADOW_PASS(3) + 1
        assert all(s.passed for s in result.signals), (
            f"Expected all signals passed; got: {[(s.signal_name, s.passed) for s in result.signals]}"
        )

    def test_ready_without_settlement_gate(self) -> None:
        """T1: READY for non-settlement strategy (settlement gate N/A = PASS)."""
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.PAPER_COHORT,
            requires_settlement_gate=False,
        )
        result = validator.assess(report)
        assert result.verdict == ReadinessVerdict.READY
        settlement_sig = next(s for s in result.signals if s.signal_name == "settlement_gate")
        assert settlement_sig.passed
        assert "N/A" in settlement_sig.rationale


# ---------------------------------------------------------------------------
# T2: NOT_READY when any single signal fails
# ---------------------------------------------------------------------------

class TestNotReadyOnAnySingleFailure:

    def test_not_ready_ci_too_low(self) -> None:
        """T2a: NOT_READY when ci_lower <= breakeven (evidence insufficient)."""
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.50,  # <= breakeven
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=False,
        )
        result = validator.assess(report)
        assert result.verdict == ReadinessVerdict.NOT_READY
        ci_sig = next(s for s in result.signals if s.signal_name == "evidence_ci")
        assert not ci_sig.passed
        assert "FAIL" in ci_sig.rationale

    def test_not_ready_ci_is_none(self) -> None:
        """T2b: NOT_READY when n_settled=0 (ci_lower=None)."""
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            n_settled=0,
            ci_lower=None,
            ci_upper=None,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=False,
        )
        result = validator.assess(report)
        assert result.verdict == ReadinessVerdict.NOT_READY
        ci_sig = next(s for s in result.signals if s.signal_name == "evidence_ci")
        tribunal_sig = next(s for s in result.signals if s.signal_name == "tribunal")
        assert not ci_sig.passed
        assert not tribunal_sig.passed

    def test_not_ready_settlement_gate_fails(self, dual_db_pair) -> None:
        """T2c: NOT_READY when settlement gate has < threshold COHERENT rows."""
        world_conn, forecasts_conn = dual_db_pair
        # Only 2 coherent rows, threshold=5
        _seed_coherent_verifications(forecasts_conn, "CHI", "low", 2)
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=True,
            coherent_threshold=5,
        )
        result = validator.assess(
            report,
            city="CHI",
            temperature_metric="low",
            conn=forecasts_conn,
        )
        assert result.verdict == ReadinessVerdict.NOT_READY
        settlement_sig = next(s for s in result.signals if s.signal_name == "settlement_gate")
        assert not settlement_sig.passed
        assert "FAIL" in settlement_sig.rationale

    def test_not_ready_ci_demote_signal(self) -> None:
        """T2d: NOT_READY when ci_lower shows underperformance (tribunal would DEMOTE)."""
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.30,  # well below breakeven
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=False,
        )
        result = validator.assess(report)
        assert result.verdict == ReadinessVerdict.NOT_READY
        tribunal_sig = next(s for s in result.signals if s.signal_name == "tribunal")
        assert not tribunal_sig.passed
        assert "DEMOTE" in tribunal_sig.rationale


# ---------------------------------------------------------------------------
# T3: Validator emits recommendation only — no tier written
# ---------------------------------------------------------------------------

class TestNoTierWrite:

    def test_no_evidence_tier_assignments_row_inserted(self, world_conn) -> None:
        """T3: assess() must NOT insert any row into evidence_tier_assignments."""
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.PAPER_COHORT,
            requires_settlement_gate=False,
        )
        before = world_conn.execute(
            "SELECT COUNT(*) FROM evidence_tier_assignments"
        ).fetchone()[0]
        validator.assess(report)
        after = world_conn.execute(
            "SELECT COUNT(*) FROM evidence_tier_assignments"
        ).fetchone()[0]
        assert after == before, (
            f"assess() must not write to evidence_tier_assignments; "
            f"rows before={before} after={after}"
        )

    def test_returns_report_not_none(self) -> None:
        """T3: assess() always returns a PromotionReadinessReport."""
        report = _make_report(ci_lower=None, n_settled=0)
        validator = PromotionReadinessValidator(requires_settlement_gate=False)
        result = validator.assess(report)
        assert isinstance(result, PromotionReadinessReport)


# ---------------------------------------------------------------------------
# T4: operator_ref required for live-tier target
# ---------------------------------------------------------------------------

class TestOperatorRefRequired:

    def test_raises_without_operator_ref_for_live_tier(self, dual_db_pair) -> None:
        """T4: ValueError when recommending >= LIVE_PILOT_TINY without operator_ref."""
        world_conn, forecasts_conn = dual_db_pair
        # Seed enough coherent rows to pass settlement gate
        _seed_coherent_verifications(forecasts_conn, "NYC", "high", 5)
        # tier_required_for_live = LIVE_PILOT_TINY; tier_current = PAPER_COHORT → target = LIVE_PILOT_TINY
        report = _make_report(
            tier_observed=EvidenceTier.PAPER_COHORT,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=True,
            coherent_threshold=5,
        )
        with pytest.raises(ValueError, match="operator_ref"):
            validator.assess(
                report,
                city="NYC",
                temperature_metric="high",
                conn=forecasts_conn,
                operator_ref=None,
            )

    def test_raises_with_blank_operator_ref(self, dual_db_pair) -> None:
        """T4: ValueError also raised for whitespace-only operator_ref."""
        world_conn, forecasts_conn = dual_db_pair
        _seed_coherent_verifications(forecasts_conn, "NYC", "high", 5)
        report = _make_report(
            tier_observed=EvidenceTier.PAPER_COHORT,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=True,
            coherent_threshold=5,
        )
        with pytest.raises(ValueError, match="operator_ref"):
            validator.assess(
                report,
                city="NYC",
                temperature_metric="high",
                conn=forecasts_conn,
                operator_ref="   ",
            )

    def test_no_operator_ref_required_for_sublive_target(self) -> None:
        """T4: No ValueError for sub-live tier target (SHADOW_PASS → PAPER_COHORT)."""
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=False,
        )
        result = validator.assess(report, operator_ref=None)
        assert result.verdict == ReadinessVerdict.READY
        assert result.tier_target == EvidenceTier.PAPER_COHORT
        assert not result.operator_ref_required

    def test_operator_ref_required_flag_set_on_ready_live_target(self, dual_db_pair) -> None:
        """T4: operator_ref_required=True when verdict is READY and target is live."""
        world_conn, forecasts_conn = dual_db_pair
        _seed_coherent_verifications(forecasts_conn, "NYC", "high", 5)
        report = _make_report(
            tier_observed=EvidenceTier.PAPER_COHORT,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=True,
            coherent_threshold=5,
        )
        result = validator.assess(
            report,
            city="NYC",
            temperature_metric="high",
            conn=forecasts_conn,
            operator_ref="op-001",
        )
        assert result.verdict == ReadinessVerdict.READY
        assert result.operator_ref_required


# ---------------------------------------------------------------------------
# T5: Settlement gate N/A for non-settlement strategies
# ---------------------------------------------------------------------------

class TestSettlementGateExemption:

    def test_settlement_gate_na_passes_for_non_settlement_strategy(self) -> None:
        """T5: requires_settlement_gate=False → settlement signal always PASS."""
        report = _make_report(
            strategy_id="center_buy",
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.PAPER_COHORT,
            requires_settlement_gate=False,
        )
        result = validator.assess(report)
        settlement_sig = next(s for s in result.signals if s.signal_name == "settlement_gate")
        assert settlement_sig.passed
        assert "N/A" in settlement_sig.rationale


# ---------------------------------------------------------------------------
# T6: Settlement gate explicit fail when city/metric missing
# ---------------------------------------------------------------------------

class TestSettlementGateMissingArgs:

    def test_settlement_gate_fail_when_city_missing(self) -> None:
        """T6: FAIL when requires_settlement_gate=True but city not supplied."""
        report = _make_report(
            tier_observed=EvidenceTier.SHADOW_PASS,
            ci_lower=0.62,
            breakeven_win_rate=0.55,
        )
        validator = PromotionReadinessValidator(
            tier_required_for_live=EvidenceTier.LIVE_PILOT_TINY,
            requires_settlement_gate=True,
            coherent_threshold=5,
        )
        result = validator.assess(report, city=None, temperature_metric="high")
        assert result.verdict == ReadinessVerdict.NOT_READY
        settlement_sig = next(s for s in result.signals if s.signal_name == "settlement_gate")
        assert not settlement_sig.passed
        assert "FAIL" in settlement_sig.rationale
