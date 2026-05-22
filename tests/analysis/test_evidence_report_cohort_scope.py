# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: MP-LEA-001/002/003; architecture code review 2026-05-22 §evidence cohort scope
"""Antibody tests: evidence report cohort scope consistency (MP-LEA-001, MP-LEA-003).

Invariant: when source / cohort_tag / experiment_id filters are applied, all
three query paths (n_decisions, n_settled, n_wins) reflect the SAME cohort.
A mismatch means the numerator and denominator come from different populations,
silently biasing ci_lower.

These tests are complementary to TestF2/TestF3 in test_p1_findings_evidence_risk.py:
  F2 tests source filter on n_decisions only.
  F3 tests cross-strategy join correctness.
  This file tests CONSISTENCY across all three metrics under each filter type.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.analysis.evidence_report import build_evidence_report, _bayesian_ci
from src.contracts.evidence_tier import EvidenceTier
from src.state.db import init_schema

UTC_NOW = "2026-05-22T00:00:00Z"

_DE_SQL = """
    INSERT INTO decision_events (
        decision_event_id, market_slug, temperature_metric, target_date,
        observation_time, decision_seq, decision_time, outcome, side,
        strategy_key, observation_available_at, polymarket_end_anchor_source,
        schema_version, source
    ) VALUES (?, ?, 'high', '2026-05-22', ?, 0, ?, 'buy_yes', 'YES',
              ?, ?, 'unknown_legacy', 27, ?)
"""

_SE_SQL = """
    INSERT INTO shadow_experiments
        (experiment_id, strategy_id, config_hash, cohort_tag, started_at, immutable)
    VALUES (?, ?, 'h', ?, ?, 0)
"""

_RD_SQL = """
    INSERT INTO regret_decompositions
        (decision_event_id, experiment_id, total_regret_usd, computed_at)
    VALUES (?, ?, ?, ?)
"""


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _seed_chain(
    conn: sqlite3.Connection,
    *,
    de_id: str,
    strategy_key: str,
    source: str,
    experiment_id: str,
    cohort_tag: str,
    regret: float = 0.05,
) -> None:
    conn.execute(
        _DE_SQL,
        (de_id, de_id, UTC_NOW, UTC_NOW, strategy_key, UTC_NOW, source),
    )
    conn.execute(_SE_SQL, (experiment_id, strategy_key, cohort_tag, UTC_NOW))
    conn.execute(_RD_SQL, (de_id, experiment_id, regret, UTC_NOW))
    conn.commit()


class TestSourceFilterConsistency:
    """source filter must scope n_decisions AND n_settled AND n_wins to the same population."""

    def test_source_filter_applies_to_all_three_metrics(self) -> None:
        """MP-LEA-001: shadow_decision source scopes n_decisions=1, n_settled=1, n_wins=1.
        A live_decision chain for the same strategy must not bleed into any metric."""
        conn = _fresh_conn()
        _seed_chain(
            conn,
            de_id="de-shadow",
            strategy_key="center_buy",
            source="shadow_decision",
            experiment_id="exp-shadow",
            cohort_tag="cohort-A",
            regret=0.10,
        )
        # A second chain with live_decision source — should be excluded by source filter
        _seed_chain(
            conn,
            de_id="de-live",
            strategy_key="center_buy",
            source="live_decision",
            experiment_id="exp-live",
            cohort_tag="cohort-A",
            regret=0.20,
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.SHADOW_PASS,
            conn=conn,
            source="shadow_decision",
        )
        assert report.n_decisions == 1, f"n_decisions={report.n_decisions} should be 1 (shadow only)"
        assert report.n_settled == 1, f"n_settled={report.n_settled} should be 1 (shadow only)"
        assert report.n_wins == 1, f"n_wins={report.n_wins} should be 1 (regret>0)"

    def test_source_filter_excluded_loses_both_count_and_wins(self) -> None:
        """When the only regret chain has a different source, n_settled=0 and n_wins=0."""
        conn = _fresh_conn()
        _seed_chain(
            conn,
            de_id="de-live",
            strategy_key="center_buy",
            source="live_decision",
            experiment_id="exp-live",
            cohort_tag="cohort-B",
            regret=0.10,
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.SHADOW_PASS,
            conn=conn,
            source="shadow_decision",
        )
        assert report.n_decisions == 0
        assert report.n_settled == 0
        assert report.n_wins == 0
        assert report.ci_lower is None, "ci_lower must be None when n_settled=0"


class TestCohortTagFilterConsistency:
    """cohort_tag filter must scope all three metrics consistently."""

    def test_cohort_tag_excludes_other_cohorts(self) -> None:
        """MP-LEA-001: filtering by cohort-A must not count cohort-B regret rows."""
        conn = _fresh_conn()
        _seed_chain(
            conn,
            de_id="de-cohA",
            strategy_key="center_buy",
            source="shadow_decision",
            experiment_id="exp-cohA",
            cohort_tag="cohort-A",
            regret=0.10,
        )
        _seed_chain(
            conn,
            de_id="de-cohB",
            strategy_key="center_buy",
            source="shadow_decision",
            experiment_id="exp-cohB",
            cohort_tag="cohort-B",
            regret=-0.05,  # loss in cohort-B
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.SHADOW_PASS,
            conn=conn,
            cohort_tag="cohort-A",
        )
        assert report.n_decisions == 1
        assert report.n_settled == 1
        assert report.n_wins == 1, "cohort-A regret>0, cohort-B loss must not bleed in"

    def test_cohort_tag_loss_not_masked_by_other_cohort_wins(self) -> None:
        """Cohort filter prevents a winning cohort from masking a losing cohort's metrics."""
        conn = _fresh_conn()
        _seed_chain(
            conn,
            de_id="de-win",
            strategy_key="center_buy",
            source="shadow_decision",
            experiment_id="exp-win",
            cohort_tag="cohort-win",
            regret=0.10,
        )
        _seed_chain(
            conn,
            de_id="de-loss",
            strategy_key="center_buy",
            source="shadow_decision",
            experiment_id="exp-loss",
            cohort_tag="cohort-loss",
            regret=-0.10,
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.SHADOW_PASS,
            conn=conn,
            cohort_tag="cohort-loss",
        )
        assert report.n_wins == 0, "loss cohort should show 0 wins"
        assert report.n_settled == 1


class TestBayesianPriorContract:
    """MP-LEA-003: prior is Beta(2,2); ci_lower uses strategy breakeven, not hardcoded 0.5."""

    def test_bayesian_ci_uses_beta_2_2_prior(self) -> None:
        """With 5 wins out of 5 trials, Beta(2,2) posterior is Beta(7,2), not Beta(6,1)."""
        from scipy.stats import beta as scipy_beta

        lower, upper = _bayesian_ci(5, 5)
        # Beta(2+5, 2+0) = Beta(7,2)
        expected_lower = scipy_beta.ppf(0.025, 7, 2)
        expected_upper = scipy_beta.ppf(0.975, 7, 2)
        assert abs(lower - expected_lower) < 1e-9, f"Prior mismatch: got lower={lower}, expected={expected_lower}"
        assert abs(upper - expected_upper) < 1e-9

    def test_ci_lower_none_when_no_settled(self) -> None:
        """ci_lower must be None when n_settled=0 — prevents hardcoded-0.5 fallback."""
        conn = _fresh_conn()
        report = build_evidence_report(
            "center_buy", EvidenceTier.SHADOW_PASS, conn=conn
        )
        assert report.ci_lower is None
        assert report.ci_upper is None

    def test_breakeven_win_rate_propagated_to_report(self) -> None:
        """breakeven_win_rate from caller reaches the report unmodified."""
        conn = _fresh_conn()
        report = build_evidence_report(
            "center_buy",
            EvidenceTier.SHADOW_PASS,
            conn=conn,
            breakeven_win_rate=0.55,
        )
        assert report.breakeven_win_rate == 0.55, (
            f"Expected 0.55, got {report.breakeven_win_rate}. "
            "breakeven_win_rate must come from caller, not be hardcoded."
        )
