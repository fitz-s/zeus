# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T4
"""T4 invariant tests: EvidenceReport + LiveReadinessTribunal."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.analysis.evidence_report import EvidenceReport, build_evidence_report
from src.analysis.live_readiness_tribunal import (
    TribunalVerdict,
    VerdictKind,
    adjudicate,
)
from src.contracts.evidence_tier import EvidenceTier
from src.state.db import init_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _make_report(
    strategy_id: str = "shoulder_sell",
    tier_observed: EvidenceTier = EvidenceTier.SHADOW_PASS,
    n_settled: int = 0,
    n_wins: int = 0,
    ci_lower: float | None = None,
    ci_upper: float | None = None,
    breakeven_win_rate: float = 0.55,
    promotion_blockers: tuple = (),
) -> EvidenceReport:
    return EvidenceReport(
        strategy_id=strategy_id,
        tier_observed=tier_observed,
        n_decisions=n_settled,
        n_wins=n_wins,
        n_no_trades=0,
        n_settled=n_settled,
        mean_regret_usd=0.0,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        breakeven_win_rate=breakeven_win_rate,
        promotion_blockers=promotion_blockers,
    )


# ---------------------------------------------------------------------------
# T4-1: HOLD for Tier-3 vs required LIVE_LIMITED_HAIRCUT (no evidence yet)
# ---------------------------------------------------------------------------

def test_t4_hold_tier3_vs_live_limited_haircut() -> None:
    """Tier-3 (SHADOW_PASS) vs required LIVE_LIMITED_HAIRCUT → HOLD."""
    report = _make_report(
        tier_observed=EvidenceTier.SHADOW_PASS,      # tier 3
        n_settled=0,
        ci_lower=None,
    )
    verdict = adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT)  # required = 6
    assert verdict.verdict == VerdictKind.HOLD
    assert verdict.tier_target == EvidenceTier.SHADOW_PASS
    assert "3" in verdict.verdict_reason or "SHADOW_PASS" in verdict.verdict_reason


def test_t4_hold_tier3_ci_below_threshold(world_conn) -> None:
    """ci_lower < breakeven → DEMOTE (not HOLD); SHADOW_PASS demotes to REPLAY_PASS."""
    report = _make_report(
        tier_observed=EvidenceTier.SHADOW_PASS,
        n_settled=50,
        n_wins=20,
        ci_lower=0.35,   # below breakeven 0.55 → DEMOTE
        ci_upper=0.55,
    )
    verdict = adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT, conn=world_conn)
    assert verdict.verdict == VerdictKind.DEMOTE
    assert verdict.tier_target == EvidenceTier.REPLAY_PASS  # 3 → 2


# ---------------------------------------------------------------------------
# T4-2: PROMOTE only when CI lower bound > breakeven
# ---------------------------------------------------------------------------

def test_t4_promote_when_ci_lower_above_breakeven(world_conn) -> None:
    """PROMOTE: ci_lower > breakeven + cost_of_capital."""
    report = _make_report(
        tier_observed=EvidenceTier.SHADOW_PASS,   # tier 3 < required 6
        n_settled=100,
        n_wins=65,
        ci_lower=0.58,    # > breakeven 0.55
        ci_upper=0.73,
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT, conn=world_conn)
    assert verdict.verdict == VerdictKind.PROMOTE
    assert verdict.tier_target == EvidenceTier.PAPER_COHORT  # 3 → 4 (one step)


def test_t4_no_promote_when_already_at_required_tier() -> None:
    """HOLD: tier_observed >= tier_required_for_live even if CI is high."""
    report = _make_report(
        tier_observed=EvidenceTier.LIVE_LIMITED_HAIRCUT,   # tier 6
        n_settled=100,
        n_wins=80,
        ci_lower=0.75,   # well above breakeven
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT)
    assert verdict.verdict == VerdictKind.HOLD
    assert verdict.tier_target == EvidenceTier.LIVE_LIMITED_HAIRCUT


# ---------------------------------------------------------------------------
# T4-3: DEMOTE writes row to evidence_tier_assignments with verdict_reason
# ---------------------------------------------------------------------------

def test_t4_demote_writes_row(world_conn) -> None:
    """DEMOTE writes a row to evidence_tier_assignments with verdict_reason."""
    report = _make_report(
        tier_observed=EvidenceTier.PAPER_COHORT,   # tier 4
        n_settled=50,
        n_wins=10,
        ci_lower=0.20,   # well below breakeven 0.55
        ci_upper=0.40,
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT, conn=world_conn)
    assert verdict.verdict == VerdictKind.DEMOTE

    row = world_conn.execute(
        "SELECT strategy_id, tier, verdict_reason FROM evidence_tier_assignments "
        "WHERE strategy_id = ? ORDER BY assigned_at DESC LIMIT 1",
        ("shoulder_sell",),
    ).fetchone()
    assert row is not None
    assert row[0] == "shoulder_sell"
    assert row[1] == int(EvidenceTier.SHADOW_PASS)  # demoted from 4 → 3
    assert row[2] is not None and len(row[2]) > 0


# ---------------------------------------------------------------------------
# T4-4: PROMOTE writes row to evidence_tier_assignments with verdict_reason
# ---------------------------------------------------------------------------

def test_t4_promote_writes_row(world_conn) -> None:
    """PROMOTE writes a row to evidence_tier_assignments with verdict_reason."""
    report = _make_report(
        strategy_id="center_sell",
        tier_observed=EvidenceTier.REPLAY_PASS,    # tier 2 < required 6
        n_settled=80,
        n_wins=55,
        ci_lower=0.60,   # above breakeven 0.55
        ci_upper=0.75,
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(
        report, EvidenceTier.LIVE_LIMITED_HAIRCUT,
        conn=world_conn, operator_ref="op_test_ref",
    )
    assert verdict.verdict == VerdictKind.PROMOTE

    row = world_conn.execute(
        "SELECT strategy_id, tier, verdict_reason, operator_ref FROM evidence_tier_assignments "
        "WHERE strategy_id = ? ORDER BY assigned_at DESC LIMIT 1",
        ("center_sell",),
    ).fetchone()
    assert row is not None
    assert row[0] == "center_sell"
    assert row[1] == int(EvidenceTier.SHADOW_PASS)  # 2 → 3
    assert row[2] is not None and len(row[2]) > 0
    assert row[3] == "op_test_ref"


# ---------------------------------------------------------------------------
# T4-5: LIVE_NORMAL not auto-demoted without operator override
# ---------------------------------------------------------------------------

def test_t4_live_normal_not_auto_demoted() -> None:
    """LIVE_NORMAL is NOT demoted by tribunal (allow_live_normal_demote=False)."""
    report = _make_report(
        tier_observed=EvidenceTier.LIVE_NORMAL,    # tier 7
        n_settled=50,
        n_wins=10,
        ci_lower=0.15,   # would normally trigger DEMOTE
        ci_upper=0.35,
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(report, EvidenceTier.LIVE_NORMAL)
    assert verdict.verdict == VerdictKind.HOLD
    assert verdict.tier_target == EvidenceTier.LIVE_NORMAL
    assert "suppressed" in verdict.verdict_reason


def test_t4_live_normal_demoted_with_operator_override(world_conn) -> None:
    """LIVE_NORMAL IS demoted when allow_live_normal_demote=True + operator_ref supplied."""
    report = _make_report(
        tier_observed=EvidenceTier.LIVE_NORMAL,
        n_settled=50,
        n_wins=10,
        ci_lower=0.15,
        ci_upper=0.35,
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(
        report, EvidenceTier.LIVE_NORMAL,
        conn=world_conn, allow_live_normal_demote=True,
        operator_ref="op_demote_live_normal",
    )
    assert verdict.verdict == VerdictKind.DEMOTE
    assert verdict.tier_target == EvidenceTier.LIVE_LIMITED_HAIRCUT  # 7 → 6


# ---------------------------------------------------------------------------
# T4-6: is_runtime_live False for tier < LIVE_PILOT_TINY (integration)
# ---------------------------------------------------------------------------

def test_t4_is_runtime_live_false_below_pilot_tiny() -> None:
    """StrategyProfile.is_runtime_live() False when tier < LIVE_PILOT_TINY."""
    from src.strategy.strategy_profile import get
    profile = get("shoulder_sell")
    # shoulder_sell: live_status=shadow, evidence_tier=SHADOW_PASS → False
    assert profile.is_runtime_live() is False

    profile_sc = get("settlement_capture")
    # settlement_capture: live_status=live, evidence_tier=LIVE_NORMAL → True
    assert profile_sc.is_runtime_live() is True


# ---------------------------------------------------------------------------
# T4-7: DEMOTE requires conn; raises RuntimeError if conn=None
# ---------------------------------------------------------------------------

def test_t4_demote_requires_conn() -> None:
    """PROMOTE/DEMOTE without conn raises RuntimeError."""
    report = _make_report(
        tier_observed=EvidenceTier.PAPER_COHORT,
        n_settled=50,
        n_wins=5,
        ci_lower=0.15,
        breakeven_win_rate=0.55,
    )
    with pytest.raises(RuntimeError, match="conn is required"):
        adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT, conn=None)


# ---------------------------------------------------------------------------
# T4-8: operator-gate guard — live-tier PROMOTE requires operator_ref (SEV2-2)
# ---------------------------------------------------------------------------

def test_t4_promote_to_live_tier_requires_operator_ref(world_conn) -> None:
    """PROMOTE targeting tier >= LIVE_PILOT_TINY without operator_ref raises ValueError.

    SEV2-2: prevents a future auto-apply reader from silently promoting a strategy
    into live execution without an explicit operator trace.
    """
    report = _make_report(
        strategy_id="shoulder_sell",
        tier_observed=EvidenceTier.PAPER_COHORT,   # tier 4 < required 5
        n_settled=100,
        n_wins=70,
        ci_lower=0.65,
        ci_upper=0.80,
        breakeven_win_rate=0.55,
    )
    # tier_target would be LIVE_PILOT_TINY (tier 5) — requires operator_ref
    with pytest.raises(ValueError, match="operator_ref"):
        adjudicate(
            report, EvidenceTier.LIVE_PILOT_TINY,
            conn=world_conn,
            operator_ref=None,  # explicitly absent
        )


def test_t4_promote_to_live_tier_with_operator_ref_succeeds(world_conn) -> None:
    """PROMOTE targeting tier >= LIVE_PILOT_TINY succeeds when operator_ref is supplied."""
    report = _make_report(
        strategy_id="shoulder_sell",
        tier_observed=EvidenceTier.PAPER_COHORT,
        n_settled=100,
        n_wins=70,
        ci_lower=0.65,
        ci_upper=0.80,
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(
        report, EvidenceTier.LIVE_PILOT_TINY,
        conn=world_conn,
        operator_ref="op_approve_phase6_test",
    )
    assert verdict.verdict == VerdictKind.PROMOTE
    assert verdict.tier_target == EvidenceTier.LIVE_PILOT_TINY


# ---------------------------------------------------------------------------
# T4-9: regression — winning cohort → ci_lower > breakeven (PROMOTE-eligible)
#                  — losing cohort → DEMOTE  (SEV2-1 regression guard)
# ---------------------------------------------------------------------------

def test_t4_winning_cohort_is_promote_eligible(world_conn) -> None:
    """Winning cohort: Beta(2,2) CI lower > breakeven → adjudicate proposes PROMOTE.

    Regression guard for SEV2-1: verifies that positive total_regret_usd (realized > counterfactual)
    correctly maps to n_wins, and the Beta(2,2) CI lower exceeds breakeven for a strong winner.
    """
    from src.analysis.evidence_report import build_evidence_report
    from src.state.shadow_experiment_registry import register_shadow_experiment
    from src.analysis.regret_decomposer import decompose_regret, write_regret_decomposition
    from datetime import datetime, timezone

    started_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    exp_id = register_shadow_experiment(
        "shoulder_sell", {"kelly": 0.5}, "winning_cohort",
        started_at=started_at, conn=world_conn,
    )
    # Insert 80 winning trades (positive total_regret_usd = win)
    for i in range(80):
        components = decompose_regret(
            forecast_error_usd=0.10,
            observation_error_usd=0.02,
            quote_error_usd=0.01,
            non_fill_error_usd=0.0,
            fee_error_usd=-0.01,
            timing_error_usd=0.0,
            settlement_ambiguity_error_usd=0.0,
            realized_pnl_usd=0.12,
            counterfactual_pnl_usd=0.0,  # realized(0.12) > counterfactual(0) → WIN
        )
        write_regret_decomposition(exp_id, f"evt_win_{i}", components, conn=world_conn)
    # Insert 20 losing trades
    for i in range(20):
        components = decompose_regret(
            forecast_error_usd=-0.05,
            observation_error_usd=0.0,
            quote_error_usd=0.0,
            non_fill_error_usd=0.0,
            fee_error_usd=-0.01,
            timing_error_usd=-0.04,
            settlement_ambiguity_error_usd=0.0,
            realized_pnl_usd=-0.10,
            counterfactual_pnl_usd=0.0,  # realized < counterfactual → LOSS
        )
        write_regret_decomposition(exp_id, f"evt_loss_{i}", components, conn=world_conn)

    report = build_evidence_report(
        "shoulder_sell", EvidenceTier.SHADOW_PASS,
        conn=world_conn, breakeven_win_rate=0.55,
    )
    assert report.n_settled == 100
    assert report.n_wins == 80
    assert report.ci_lower is not None
    assert report.ci_lower > 0.55, (
        f"Winning cohort (80/100) should have ci_lower > breakeven 0.55; "
        f"got ci_lower={report.ci_lower:.4f}"
    )
    # PROMOTE-eligible (tier 3 < required 6, ci_lower > breakeven)
    verdict = adjudicate(
        report, EvidenceTier.LIVE_LIMITED_HAIRCUT,
        conn=world_conn, operator_ref="op_regression_test",
    )
    assert verdict.verdict == VerdictKind.PROMOTE


def test_t4_losing_cohort_is_demote(world_conn) -> None:
    """Losing cohort: Beta(2,2) CI lower < breakeven → adjudicate proposes DEMOTE.

    Regression guard for SEV2-1: verifies negative total_regret_usd (realized < counterfactual)
    correctly maps to n_wins=0, and a losing cohort is demoted.
    """
    from src.analysis.evidence_report import build_evidence_report
    from src.state.shadow_experiment_registry import register_shadow_experiment
    from src.analysis.regret_decomposer import decompose_regret, write_regret_decomposition
    from datetime import datetime, timezone

    started_at = datetime(2026, 5, 2, tzinfo=timezone.utc)
    exp_id = register_shadow_experiment(
        "center_sell", {"kelly": 0.3}, "losing_cohort",
        started_at=started_at, conn=world_conn,
    )
    # Insert 100 losing trades (negative total_regret_usd)
    for i in range(100):
        components = decompose_regret(
            forecast_error_usd=-0.08,
            observation_error_usd=0.0,
            quote_error_usd=0.0,
            non_fill_error_usd=0.0,
            fee_error_usd=-0.02,
            timing_error_usd=0.0,
            settlement_ambiguity_error_usd=0.0,
            realized_pnl_usd=-0.10,
            counterfactual_pnl_usd=0.0,  # realized < counterfactual → LOSS
        )
        write_regret_decomposition(exp_id, f"evt_lose_{i}", components, conn=world_conn)

    report = build_evidence_report(
        "center_sell", EvidenceTier.PAPER_COHORT,
        conn=world_conn, breakeven_win_rate=0.55,
    )
    assert report.n_settled == 100
    assert report.n_wins == 0
    assert report.ci_lower is not None
    assert report.ci_lower < 0.55

    verdict = adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT, conn=world_conn)
    assert verdict.verdict == VerdictKind.DEMOTE
