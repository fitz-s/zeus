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
    """LIVE_NORMAL IS demoted when allow_live_normal_demote=True."""
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
