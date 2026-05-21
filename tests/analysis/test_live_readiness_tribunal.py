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
from src.state.evidence_tier_assignments import (
    current_evidence_tier_assignment,
    record_evidence_tier_assignment,
)
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


def test_t4_promote_write_is_durable_before_return(tmp_path) -> None:
    """PROMOTE returns only after the evidence_tier_assignments row is committed."""
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    report = _make_report(
        strategy_id="center_sell",
        tier_observed=EvidenceTier.REPLAY_PASS,
        n_settled=80,
        n_wins=55,
        ci_lower=0.60,
        ci_upper=0.75,
        breakeven_win_rate=0.55,
    )
    verdict = adjudicate(
        report,
        EvidenceTier.LIVE_LIMITED_HAIRCUT,
        conn=conn,
        operator_ref="op_durable_test",
    )
    assert verdict.verdict == VerdictKind.PROMOTE

    second = sqlite3.connect(db_path)
    try:
        row = second.execute(
            "SELECT tier, operator_ref FROM evidence_tier_assignments WHERE strategy_id=?",
            ("center_sell",),
        ).fetchone()
        assert row is not None
        assert row[0] == int(EvidenceTier.SHADOW_PASS)
        assert row[1] == "op_durable_test"
    finally:
        second.close()
        conn.close()


def test_t4_assignment_schema_rejects_invalid_tier(world_conn) -> None:
    """evidence_tier_assignments rejects malformed tier authority rows."""
    with pytest.raises(sqlite3.IntegrityError):
        world_conn.execute(
            """
            INSERT INTO evidence_tier_assignments (
                strategy_id, tier, assigned_at, schema_version,
                assignment_source, verdict_kind
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "shoulder_sell",
                999,
                datetime.now(timezone.utc).isoformat(),
                26,
                "tribunal",
                "PROMOTE",
            ),
        )


def test_t4_init_schema_rebuilds_legacy_assignment_table() -> None:
    """Legacy evidence_tier_assignments tables get reducer-required columns."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE evidence_tier_assignments (
            strategy_id TEXT NOT NULL,
            tier INTEGER NOT NULL,
            assigned_at TEXT NOT NULL,
            rationale TEXT,
            operator_ref TEXT,
            verdict_reason TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO evidence_tier_assignments (
            strategy_id, tier, assigned_at, rationale, operator_ref, verdict_reason
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "shoulder_sell",
            int(EvidenceTier.SHADOW_PASS),
            datetime.now(timezone.utc).isoformat(),
            "legacy",
            None,
            "legacy",
        ),
    )
    init_schema(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(evidence_tier_assignments)")}
    assert {"id", "schema_version", "assignment_source", "verdict_kind"} <= columns
    assignment = current_evidence_tier_assignment(conn, "shoulder_sell")
    assert assignment is not None
    assert assignment.tier == EvidenceTier.SHADOW_PASS
    assert assignment.assignment_source == "migration"


def test_t4_current_assignment_reducer_operator_override_outranks_tribunal(world_conn) -> None:
    """Reducer has deterministic authority order: operator_override > tribunal."""
    record_evidence_tier_assignment(
        world_conn,
        strategy_id="shoulder_sell",
        tier=EvidenceTier.LIVE_LIMITED_HAIRCUT,
        rationale="operator override",
        operator_ref="op_override",
        verdict_reason="manual approval",
        assignment_source="operator_override",
        verdict_kind="OPERATOR_OVERRIDE",
    )
    record_evidence_tier_assignment(
        world_conn,
        strategy_id="shoulder_sell",
        tier=EvidenceTier.REPLAY_PASS,
        rationale="tribunal demotion",
        operator_ref=None,
        verdict_reason="underperformed",
        assignment_source="tribunal",
        verdict_kind="DEMOTE",
    )
    assignment = current_evidence_tier_assignment(world_conn, "shoulder_sell")
    assert assignment is not None
    assert assignment.tier == EvidenceTier.LIVE_LIMITED_HAIRCUT
    assert assignment.assignment_source == "operator_override"


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
    """LIVE_NORMAL IS demoted when allow_live_normal_demote=True; operator_ref optional for DEMOTE."""
    report = _make_report(
        tier_observed=EvidenceTier.LIVE_NORMAL,
        n_settled=50,
        n_wins=10,
        ci_lower=0.15,
        ci_upper=0.35,
        breakeven_win_rate=0.55,
    )
    # DEMOTE within live tiers does NOT require operator_ref (fail-closed-to-loss guard
    # was reversed per Codex P1: blocking DEMOTE is fail-open-to-loss).
    verdict = adjudicate(
        report, EvidenceTier.LIVE_NORMAL,
        conn=world_conn, allow_live_normal_demote=True,
        operator_ref=None,
    )
    assert verdict.verdict == VerdictKind.DEMOTE
    assert verdict.tier_target == EvidenceTier.LIVE_LIMITED_HAIRCUT  # 7 → 6


def test_t4_demote_within_live_tier_does_not_require_operator_ref(world_conn) -> None:
    """DEMOTE targeting a live tier succeeds without operator_ref.

    Regression guard: guard is PROMOTE-only; blocking DEMOTEs would leave a
    losing live strategy running (fail-open-to-loss).
    """
    report = _make_report(
        tier_observed=EvidenceTier.LIVE_PILOT_TINY,
        n_settled=50,
        n_wins=5,
        ci_lower=0.10,
        ci_upper=0.30,
        breakeven_win_rate=0.55,
    )
    # LIVE_PILOT_TINY (5) → PAPER_COHORT (4) demotion, no operator_ref required
    verdict = adjudicate(
        report, EvidenceTier.LIVE_PILOT_TINY,
        conn=world_conn, operator_ref=None,
    )
    assert verdict.verdict == VerdictKind.DEMOTE
    assert verdict.tier_target == EvidenceTier.PAPER_COHORT  # 5 → 4


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

def _insert_decision_events(conn: sqlite3.Connection, strategy_key: str, count: int) -> None:
    """Insert minimal valid decision_events rows for a strategy (test helper)."""
    for i in range(count):
        conn.execute(
            """
            INSERT INTO decision_events (
                market_slug, temperature_metric, target_date, observation_time,
                decision_seq, decision_time, outcome, side, strategy_key,
                observation_available_at, polymarket_end_anchor_source, schema_version, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"test-market-{strategy_key}",
                "high",
                "2026-05-01",
                f"2026-05-01T{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}Z",
                i,
                "2026-05-01T12:00:00Z",
                "YES",
                "BUY",
                strategy_key,
                "2026-05-01T11:00:00Z",
                "gamma_explicit",
                25,
                "phase0_backfill",
            ),
        )


def test_t4_winning_cohort_is_promote_eligible(world_conn) -> None:
    """Winning cohort: Beta(2,2) CI lower > breakeven → adjudicate proposes PROMOTE.

    Regression guard for SEV2-1: verifies that positive total_regret_usd (realized > counterfactual)
    correctly maps to n_wins, and the Beta(2,2) CI lower exceeds breakeven for a strong winner.
    Also verifies n_decisions == n_settled == 100 (decision_events denominator path).
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
    # Insert matching decision_events rows (authoritative n_decisions denominator)
    _insert_decision_events(world_conn, "shoulder_sell", 100)

    report = build_evidence_report(
        "shoulder_sell", EvidenceTier.SHADOW_PASS,
        conn=world_conn, breakeven_win_rate=0.55,
    )
    assert report.n_settled == 100
    assert report.n_decisions == 100, (
        f"n_decisions should equal n_settled (100 decision_events inserted); "
        f"got n_decisions={report.n_decisions}"
    )
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


def test_t4_evidence_report_counts_structured_no_trade_rows(world_conn) -> None:
    """EvidenceReport counts no_trade_events by structured strategy_key."""
    world_conn.execute(
        """
        INSERT INTO no_trade_events (
            market_slug, temperature_metric, target_date, observation_time,
            decision_seq, reason, reason_detail, strategy_key, event_source,
            shadow_runtime, observed_at, schema_version, schema_compatibility
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "market-a",
            "high",
            "2026-05-01",
            "2026-05-01T12:00:00Z",
            0,
            "uncategorized",
            "structured",
            "shoulder_sell",
            "shadow_decision",
            1,
            "2026-05-01T12:00:00Z",
            26,
            "current",
        ),
    )
    world_conn.execute(
        """
        INSERT INTO no_trade_events (
            market_slug, temperature_metric, target_date, observation_time,
            decision_seq, reason, reason_detail, strategy_key, event_source,
            shadow_runtime, observed_at, schema_version, schema_compatibility
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "market-b",
            "high",
            "2026-05-01",
            "2026-05-01T13:00:00Z",
            0,
            "uncategorized",
            "degraded compatibility",
            "shoulder_sell",
            "shadow_decision",
            1,
            "2026-05-01T13:00:00Z",
            26,
            "degraded",
        ),
    )
    report = build_evidence_report(
        "shoulder_sell",
        EvidenceTier.SHADOW_PASS,
        conn=world_conn,
    )
    assert report.n_no_trades == 1


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
    # Insert matching decision_events rows (authoritative n_decisions denominator)
    _insert_decision_events(world_conn, "center_sell", 100)

    report = build_evidence_report(
        "center_sell", EvidenceTier.PAPER_COHORT,
        conn=world_conn, breakeven_win_rate=0.55,
    )
    assert report.n_settled == 100
    assert report.n_decisions == 100, (
        f"n_decisions should equal n_settled (100 decision_events inserted); "
        f"got n_decisions={report.n_decisions}"
    )
    assert report.n_wins == 0
    assert report.ci_lower is not None
    assert report.ci_lower < 0.55

    verdict = adjudicate(report, EvidenceTier.LIVE_LIMITED_HAIRCUT, conn=world_conn)
    assert verdict.verdict == VerdictKind.DEMOTE
