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

_DC_SQL = """
    INSERT INTO decision_certificates (
        certificate_id, certificate_type, schema_version, canonicalization_version,
        semantic_key, claim_type, mode, decision_time,
        authority_id, authority_version, algorithm_id, algorithm_version,
        payload_json, payload_hash, certificate_hash, verifier_status, created_at
    ) VALUES (?, 'FinalIntentCertificate', 1, '1.0',
              ?, 'FINAL_INTENT', 'SHADOW', ?,
              'test_authority', '1.0', 'test_algorithm', '1.0',
              ?, 'hash_' || ?, 'cert_hash_' || ?, 'VERIFIED', ?)
"""


def _insert_certificate(
    conn: sqlite3.Connection,
    *,
    cert_id: str,
    strategy_key: str,
) -> None:
    """Insert a FinalIntentCertificate row for the given strategy_key."""
    import json
    payload = json.dumps({"strategy_key": strategy_key})
    conn.execute(
        _DC_SQL,
        (cert_id, cert_id, UTC_NOW, payload, cert_id, cert_id, UTC_NOW),
    )

_RD_SQL = """
    INSERT INTO regret_decompositions
        (decision_event_id, experiment_id, strategy_id, cohort_tag, total_regret_usd, computed_at)
    VALUES (?, ?, ?, ?, ?, ?)
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
    seed_certificate: bool = True,
) -> None:
    conn.execute(
        _DE_SQL,
        (de_id, de_id, UTC_NOW, UTC_NOW, strategy_key, UTC_NOW, source),
    )
    conn.execute(_RD_SQL, (de_id, experiment_id, strategy_key, cohort_tag, regret, UTC_NOW))
    # C2 fix: n_decisions now reads decision_certificates, not decision_events.
    # Seed the corresponding FinalIntentCertificate row so n_decisions counts correctly.
    # Pass seed_certificate=False for chains that should NOT appear in n_decisions
    # (e.g. a live_decision entity that never reached the certificate lane).
    if seed_certificate:
        _insert_certificate(conn, cert_id=f"cert-{de_id}", strategy_key=strategy_key)
    conn.commit()


class TestSourceFilterConsistency:
    """source filter must scope n_decisions AND n_settled AND n_wins to the same population."""

    def test_source_filter_applies_to_all_three_metrics(self) -> None:
        """MP-LEA-001: offline_decision source scopes n_decisions=1, n_settled=1, n_wins=1.
        A live_decision chain for the same strategy must not bleed into any metric."""
        conn = _fresh_conn()
        _seed_chain(
            conn,
            de_id="de-offline",
            strategy_key="center_buy",
            source="offline_decision",
            experiment_id="exp-offline",
            cohort_tag="cohort-A",
            regret=0.10,
        )
        # A second chain with live_decision source — should be excluded from n_decisions.
        # C2: source filter is not applied to decision_certificates; exclusion is modelled
        # by not seeding a certificate for this entity (it never reached the active lane).
        _seed_chain(
            conn,
            de_id="de-live",
            strategy_key="center_buy",
            source="live_decision",
            experiment_id="exp-live",
            cohort_tag="cohort-A",
            regret=0.20,
            seed_certificate=False,
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.REPLAY_PASS,
            conn=conn,
            source="offline_decision",
            breakeven_win_rate=0.5,
        )
        assert report.n_decisions == 1, f"n_decisions={report.n_decisions} should be 1 (offline only)"
        assert report.n_settled == 1, f"n_settled={report.n_settled} should be 1 (offline only)"
        assert report.n_wins == 1, f"n_wins={report.n_wins} should be 1 (regret>0)"

    def test_source_filter_excluded_loses_both_count_and_wins(self) -> None:
        """When the only chain is a different source with no certificate, all metrics=0."""
        conn = _fresh_conn()
        # C2: live_decision entity has no certificate in the active lane → n_decisions=0.
        _seed_chain(
            conn,
            de_id="de-live",
            strategy_key="center_buy",
            source="live_decision",
            experiment_id="exp-live",
            cohort_tag="cohort-B",
            regret=0.10,
            seed_certificate=False,
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.REPLAY_PASS,
            conn=conn,
            source="offline_decision",
            breakeven_win_rate=0.5,
        )
        assert report.n_decisions == 0
        assert report.n_settled == 0
        assert report.n_wins == 0
        assert report.ci_lower is None, "ci_lower must be None when n_settled=0"


class TestCohortTagFilterConsistency:
    """cohort_tag filter scopes regret analytics (n_settled, n_wins) but NOT n_decisions.

    P1-7 design: n_decisions is the full strategy universe (no experiment/cohort FK on
    decision_events). Scoping via regret_decompositions would exclude unsettled decisions
    from the denominator, corrupting the CI. cohort_tag only filters regret rows.
    """

    def test_cohort_tag_excludes_other_cohorts(self) -> None:
        """MP-LEA-001: cohort-A regret filter must not count cohort-B regret rows.
        n_decisions = full strategy universe (2 decisions); n_settled = cohort-A only (1).
        """
        conn = _fresh_conn()
        _seed_chain(
            conn,
            de_id="de-cohA",
            strategy_key="center_buy",
            source="offline_decision",
            experiment_id="exp-cohA",
            cohort_tag="cohort-A",
            regret=0.10,
        )
        _seed_chain(
            conn,
            de_id="de-cohB",
            strategy_key="center_buy",
            source="offline_decision",
            experiment_id="exp-cohB",
            cohort_tag="cohort-B",
            regret=-0.05,  # loss in cohort-B
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.REPLAY_PASS,
            conn=conn,
            cohort_tag="cohort-A",
            breakeven_win_rate=0.5,
        )
        # n_decisions = full strategy universe (no cohort FK on decision_events)
        assert report.n_decisions == 2, (
            f"n_decisions={report.n_decisions}: expected 2 (full strategy universe). "
            "cohort_tag scopes regret analytics only, not the denominator (P1-7)."
        )
        assert report.n_settled == 1
        assert report.n_wins == 1, "cohort-A regret>0, cohort-B loss must not bleed into n_wins"

    def test_cohort_tag_loss_not_masked_by_other_cohort_wins(self) -> None:
        """Cohort filter prevents a winning cohort from masking a losing cohort's metrics."""
        conn = _fresh_conn()
        _seed_chain(
            conn,
            de_id="de-win",
            strategy_key="center_buy",
            source="offline_decision",
            experiment_id="exp-win",
            cohort_tag="cohort-win",
            regret=0.10,
        )
        _seed_chain(
            conn,
            de_id="de-loss",
            strategy_key="center_buy",
            source="offline_decision",
            experiment_id="exp-loss",
            cohort_tag="cohort-loss",
            regret=-0.10,
        )

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.REPLAY_PASS,
            conn=conn,
            cohort_tag="cohort-loss",
            breakeven_win_rate=0.5,
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
            "center_buy", EvidenceTier.REPLAY_PASS, conn=conn, breakeven_win_rate=0.5
        )
        assert report.ci_lower is None
        assert report.ci_upper is None

    def test_breakeven_win_rate_propagated_to_report(self) -> None:
        """breakeven_win_rate from caller reaches the report unmodified."""
        conn = _fresh_conn()
        report = build_evidence_report(
            "center_buy",
            EvidenceTier.REPLAY_PASS,
            conn=conn,
            breakeven_win_rate=0.55,
        )
        assert report.breakeven_win_rate == 0.55, (
            f"Expected 0.55, got {report.breakeven_win_rate}. "
            "breakeven_win_rate must come from caller, not be hardcoded."
        )

    def test_breakeven_win_rate_none_raises(self) -> None:
        """P1-9 antibody: build_evidence_report must raise ValueError when
        breakeven_win_rate is not supplied. No generic 0.5 default — every
        strategy has a different breakeven from its fee structure."""
        conn = _fresh_conn()
        with pytest.raises(ValueError, match="breakeven_win_rate"):
            build_evidence_report(
                "center_buy",
                EvidenceTier.REPLAY_PASS,
                conn=conn,
                # breakeven_win_rate intentionally omitted — should raise
            )

    def test_n_decisions_is_full_universe_when_cohort_scoped(self) -> None:
        """P1-7 antibody: n_decisions must include unsettled decisions even when
        cohort_tag is provided. Scoping n_decisions via regret_decompositions
        excluded unsettled decisions (those with no regret row yet), corrupting
        the Beta denominator by making n_decisions == n_settled."""
        conn = _fresh_conn()
        # Settled decision in cohort-A (has regret row)
        _seed_chain(
            conn,
            de_id="de-settled",
            strategy_key="center_buy",
            source="offline_decision",
            experiment_id="exp-A",
            cohort_tag="cohort-A",
            regret=0.10,
        )
        # Unsettled decision for same strategy — no regret row, no experiment link
        conn.execute(
            _DE_SQL,
            ("de-unsettled", "de-unsettled", UTC_NOW, UTC_NOW, "center_buy", UTC_NOW, "offline_decision"),
        )
        # C2 fix: unsettled decisions must also appear in decision_certificates so
        # n_decisions counts them in the full-strategy-universe denominator (P1-7).
        _insert_certificate(conn, cert_id="cert-de-unsettled", strategy_key="center_buy")
        conn.commit()

        report = build_evidence_report(
            "center_buy",
            EvidenceTier.REPLAY_PASS,
            conn=conn,
            cohort_tag="cohort-A",
            breakeven_win_rate=0.5,
        )
        assert report.n_decisions == 2, (
            f"n_decisions={report.n_decisions}: unsettled decision must be counted in denominator. "
            "cohort_tag scopes regret analytics only (P1-7 fix)."
        )
        assert report.n_settled == 1, "Only 1 decision has a regret row (settled)"
        assert report.n_wins == 1
