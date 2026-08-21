# Created: 2026-03-30
# Last reused/audited: 2026-08-21
# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md Batch D RiskGuard test-law remediation; Wave26 verification-noise helper alignment; PR90 current-env fallback review fix; 2026-08-15 economic-settlement trailing-loss hotfix.
#                  2026-05-17 live lock remediation: RiskGuard trade/world DB lock degrades to fresh DATA_DEGRADED rather than stale RED.
# Lifecycle: created=2026-03-30; last_reviewed=2026-08-21; last_reused=2026-08-21
# Purpose: Guard RiskGuard protective metrics, policy resolution, source authority, and portfolio loader invariants.
# Reuse: Run after RiskGuard risk details, portfolio loader, settlement source, bankroll, or risk-action changes.
# 2026-08-17: Brier strategy-gate evidence is independent by target date.
# 2026-08-19: New Day0 probability semantics must earn revision-scoped causal
# capital admission before BUY; shadow evidence and held exits remain live.
"""Tests for RiskGuard metrics, policy resolution, and risk levels."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.riskguard.policy as policy_module
import src.riskguard.riskguard as riskguard_module
import src.state.db as state_db_module
import src.state.strategy_tracker as strategy_tracker_module
from src.riskguard.risk_level import RiskLevel, overall_level
from src.riskguard.metrics import (
    brier_score,
    directional_accuracy,
    evaluate_brier,
)
from src.state.db import (
    get_connection,
    init_schema,
    query_strategy_health_snapshot,
    refresh_strategy_health,
)
from src.state.portfolio import (
    CANONICAL_STRATEGY_KEYS,
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    PortfolioState,
    Position,
    total_exposure_usd,
)


@pytest.fixture(autouse=True)
def _stable_host_power_truth(monkeypatch):
    monkeypatch.setattr(
        riskguard_module,
        "_pmset_battery_status",
        lambda: (
            "Now drawing from 'AC Power'\n"
            " -InternalBattery-0 (id=1)\t80%; charging; present: true\n"
        ),
    )


class TestForwardCapitalAudit:
    def test_activity_excludes_preboundary_entry_decisions(self):
        from scripts.audit_realtime_pnl import _cohort_activity

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE venue_commands (command_id TEXT, intent_kind TEXT, "
            "state TEXT, created_at TEXT);"
            "CREATE TABLE execution_fact (command_id TEXT, position_id TEXT, "
            "order_role TEXT, filled_at TEXT, terminal_exec_status TEXT);"
            "CREATE TABLE venue_order_facts (command_id TEXT, state TEXT);"
            "CREATE TABLE position_current (position_id TEXT, strategy_key TEXT, "
            "decision_law_id TEXT);"
        )
        conn.executemany(
            "INSERT INTO venue_commands VALUES (?,?,?,?)",
            (
                ("new", "ENTRY", "FILLED", "2026-08-11T23:10:00+00:00"),
                ("old", "ENTRY", "FILLED", "2026-08-11T22:59:00+00:00"),
            ),
        )
        conn.executemany(
            "INSERT INTO execution_fact VALUES (?,?,?,?,?)",
            (
                (
                    "new",
                    "new-position",
                    "entry",
                    "2026-08-11T23:11:00+00:00",
                    "filled",
                ),
                (
                    "old",
                    "old-position",
                    "entry",
                    "2026-08-11T23:12:00+00:00",
                    "filled",
                ),
            ),
        )
        conn.executemany(
            "INSERT INTO venue_order_facts VALUES (?,?)",
            (("new", "MATCHED"), ("old", "MATCHED")),
        )
        conn.executemany(
            "INSERT INTO position_current VALUES (?,?,?)",
            (
                ("new-position", "day0_nowcast_entry", "predicted_bin_ev_v1"),
                ("old-position", "day0_nowcast_entry", "predicted_bin_ev_v1"),
            ),
        )

        activity = _cohort_activity(
            conn,
            since=datetime(2026, 8, 11, 23, tzinfo=timezone.utc),
            as_of=datetime(2026, 8, 12, 0, tzinfo=timezone.utc),
        )

        assert activity["entry_filled_position_count"] == 1
        assert activity["filled_command_count"] == 1
        assert activity["chain_matched_fact_count"] == 1
        assert activity["chain_fact_coverage_complete"] is True
        assert activity["preboundary_entry_fill_count"] == 1
        conn.close()

    def test_activity_counts_confirmed_and_partial_execution_facts(self):
        from scripts.audit_realtime_pnl import _cohort_activity

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE venue_commands (command_id TEXT, intent_kind TEXT, "
            "state TEXT, created_at TEXT);"
            "CREATE TABLE execution_fact (command_id TEXT, position_id TEXT, "
            "order_role TEXT, filled_at TEXT, terminal_exec_status TEXT);"
            "CREATE TABLE venue_order_facts (command_id TEXT, state TEXT);"
            "CREATE TABLE position_current (position_id TEXT, strategy_key TEXT, "
            "decision_law_id TEXT);"
        )
        conn.executemany(
            "INSERT INTO venue_commands VALUES (?,?,?,?)",
            (
                ("confirmed", "ENTRY", "FILLED", "2026-08-11T23:10:00+00:00"),
                ("partial", "ENTRY", "EXPIRED", "2026-08-11T23:12:00+00:00"),
            ),
        )
        conn.executemany(
            "INSERT INTO execution_fact VALUES (?,?,?,?,?)",
            (
                (
                    "confirmed",
                    "confirmed-position",
                    "entry",
                    "2026-08-11T23:11:00+00:00",
                    "CONFIRMED",
                ),
                (
                    "partial",
                    "partial-position",
                    "entry",
                    "2026-08-11T23:13:00+00:00",
                    "partial",
                ),
            ),
        )
        conn.executemany(
            "INSERT INTO venue_order_facts VALUES (?,?)",
            (("confirmed", "MATCHED"), ("partial", "PARTIALLY_MATCHED")),
        )
        conn.executemany(
            "INSERT INTO position_current VALUES (?,?,?)",
            (
                (
                    "confirmed-position",
                    "forecast_qkernel_entry",
                    "predicted_bin_ev_v1",
                ),
                (
                    "partial-position",
                    "day0_nowcast_entry",
                    "predicted_bin_ev_v1",
                ),
            ),
        )

        activity = _cohort_activity(
            conn,
            since=datetime(2026, 8, 11, 23, tzinfo=timezone.utc),
            as_of=datetime(2026, 8, 12, 0, tzinfo=timezone.utc),
        )

        assert activity["entry_filled_position_count"] == 2
        assert activity["filled_command_count"] == 2
        assert activity["chain_matched_fact_count"] == 2
        assert activity["chain_fact_coverage_complete"] is True
        conn.close()

    def test_zero_realized_positions_never_prove_capital_gain(self):
        from scripts.audit_realtime_pnl import _forward_capital_summary

        activity = {
            "chain_fact_coverage_complete": True,
            "entry_filled_position_count": 0,
            "unclassified_filled_position_count": 0,
        }
        curves = (
            {
                "status": "awaiting_current_law_fills",
                "filled_position_count": 0,
                "open_position_count": 0,
                "capital_committed_usd": 0.0,
                "curve": [],
            },
            {
                "status": "awaiting_current_law_fills",
                "filled_position_count": 0,
                "open_position_count": 0,
                "capital_committed_usd": 0.0,
                "curve": [],
            },
        )

        result = _forward_capital_summary(
            activity=activity,
            curves=curves,
            robust_evalue_threshold=10.0,
        )

        assert result["status"] == "awaiting_current_law_fills"
        assert result["capital_gain_proven"] is False
        assert result["robust_capital_gain_proven"] is False
        assert result["capital_committed_usd"] == 0.0
        assert result["net_realized_pnl_usd"] == 0.0

    def test_positive_realized_gain_requires_complete_chain_and_attribution_truth(self):
        from scripts.audit_realtime_pnl import _forward_capital_summary

        row = {
            "position_id": "p1",
            "target_date": "2026-08-11",
            "close_type": "SETTLED",
            "realized_at": "2026-08-11T23:00:00+00:00",
            "capital_committed_usd": 2.0,
            "gross_realized_pnl_usd": 1.0,
            "fee_bound_usd": 0.1,
            "net_realized_pnl_usd": 0.9,
        }
        curves = (
            {
                "status": "positive",
                "filled_position_count": 1,
                "open_position_count": 0,
                "capital_committed_usd": 2.0,
                "curve": [row],
            },
            {
                "status": "awaiting_current_law_fills",
                "filled_position_count": 0,
                "open_position_count": 0,
                "capital_committed_usd": 0.0,
                "curve": [],
            },
        )
        complete = {
            "chain_fact_coverage_complete": True,
            "entry_filled_position_count": 1,
            "unclassified_filled_position_count": 0,
        }

        proven = _forward_capital_summary(
            activity=complete,
            curves=curves,
            robust_evalue_threshold=10.0,
        )
        degraded = _forward_capital_summary(
            activity={**complete, "chain_fact_coverage_complete": False},
            curves=curves,
            robust_evalue_threshold=10.0,
        )

        assert proven["status"] == "positive_observed"
        assert proven["capital_gain_proven"] is True
        assert proven["robust_capital_gain_proven"] is False
        assert proven["settled_position_count"] == 1
        assert proven["win_count"] == 1
        assert proven["capital_committed_usd"] == pytest.approx(2.0)
        assert proven["open_capital_committed_usd"] == 0.0
        assert proven["net_realized_pnl_usd"] == pytest.approx(0.9)
        assert degraded["status"] == "capital_truth_degraded"
        assert degraded["capital_gain_proven"] is False

    def test_one_profitable_cluster_does_not_prove_robust_capital_gain(self):
        from scripts.audit_realtime_pnl import _robust_capital_evidence

        evidence = _robust_capital_evidence(
            [
                {
                    "target_date": "2026-08-11",
                    "capital_committed_usd": 1.0,
                    "net_realized_pnl_usd": 4.0,
                }
            ],
            threshold=10.0,
        )

        assert evidence["independent_cluster_count"] == 1
        assert evidence["evalue"] == pytest.approx(1.3875)
        assert evidence["threshold_reached"] is False

    def test_hybrid_exit_and_residual_settlement_counts_as_early_exit(self):
        from scripts.audit_realtime_pnl import _forward_capital_summary

        result = _forward_capital_summary(
            activity={
                "chain_fact_coverage_complete": True,
                "entry_filled_position_count": 1,
                "unclassified_filled_position_count": 0,
                "preboundary_entry_fill_count": 0,
            },
            curves=(
                {
                    "status": "positive",
                    "filled_position_count": 1,
                    "open_position_count": 0,
                    "capital_committed_usd": 2.0,
                    "curve": [
                        {
                            "position_id": "hybrid",
                            "target_date": "2026-08-11",
                            "close_type": (
                                "EXIT_ORDER_FILLED_WITH_RESIDUAL_SETTLEMENT"
                            ),
                            "realized_at": "2026-08-11T23:00:00+00:00",
                            "capital_committed_usd": 2.0,
                            "gross_realized_pnl_usd": 1.0,
                            "fee_bound_usd": 0.1,
                            "net_realized_pnl_usd": 0.9,
                        }
                    ],
                },
            ),
            robust_evalue_threshold=10.0,
        )

        assert result["settled_position_count"] == 0
        assert result["early_exit_position_count"] == 1
        assert result["hybrid_exit_settlement_position_count"] == 1

    def test_same_target_date_positions_are_one_capital_evidence_cluster(self):
        from scripts.audit_realtime_pnl import _robust_capital_evidence

        evidence = _robust_capital_evidence(
            [
                {
                    "target_date": "2026-08-11",
                    "capital_committed_usd": 1.0,
                    "net_realized_pnl_usd": 1.0,
                },
                {
                    "target_date": "2026-08-11",
                    "capital_committed_usd": 1.0,
                    "net_realized_pnl_usd": 1.0,
                },
            ],
            threshold=10.0,
        )

        assert evidence["independent_cluster_count"] == 1
        assert evidence["clusters"][0]["position_count"] == 2
        assert evidence["evalue"] == pytest.approx(1.3875)

    def test_repeated_cross_date_capital_gains_can_prove_robustness(self):
        from scripts.audit_realtime_pnl import _forward_capital_summary

        rows = [
            {
                "position_id": f"p{day}",
                "target_date": f"2026-08-{day:02d}",
                "close_type": "SETTLED",
                "realized_at": f"2026-08-{day:02d}T23:00:00+00:00",
                "capital_committed_usd": 1.0,
                "gross_realized_pnl_usd": 1.0,
                "fee_bound_usd": 0.0,
                "net_realized_pnl_usd": 1.0,
            }
            for day in range(1, 7)
        ]
        activity = {
            "chain_fact_coverage_complete": True,
            "entry_filled_position_count": 6,
            "unclassified_filled_position_count": 0,
            "preboundary_entry_fill_count": 0,
        }
        result = _forward_capital_summary(
            activity=activity,
            curves=(
                {
                    "status": "positive",
                    "filled_position_count": 6,
                    "open_position_count": 0,
                    "capital_committed_usd": 6.0,
                    "curve": rows,
                },
            ),
            robust_evalue_threshold=10.0,
        )

        assert result["capital_gain_proven"] is True
        assert result["robust_capital_gain_proven"] is True
        assert result["robust_capital_evidence"]["evalue"] == pytest.approx(
            16.534264
        )

    def test_realized_loss_prevents_robust_capital_claim(self):
        from scripts.audit_realtime_pnl import _robust_capital_evidence

        evidence = _robust_capital_evidence(
            [
                {
                    "target_date": f"2026-08-{day:02d}",
                    "capital_committed_usd": 1.0,
                    "net_realized_pnl_usd": 1.0 if day < 6 else -1.0,
                }
                for day in range(1, 7)
            ],
            threshold=10.0,
        )

        assert evidence["threshold_reached"] is False
        assert evidence["evalue"] < 10.0

    def test_audit_requires_explicit_utc_boundary_and_read_only_connection(self):
        import inspect

        import scripts.audit_realtime_pnl as audit

        assert audit._parse_utc("2026-08-11T23:00:00Z") == datetime(
            2026, 8, 11, 23, tzinfo=timezone.utc
        )
        with pytest.raises(Exception, match="explicit UTC offset"):
            audit._parse_utc("2026-08-11T23:00:00")
        source = inspect.getsource(audit)
        assert "get_trade_connection_read_only" in source
        assert "load_portfolio" not in source
        assert "get_trade_connection(" not in source


def _recent_iso(*, minutes: int) -> str:
    """occurred_at inside _ENTRY_EXECUTION_LOOKBACK (execution summary is time-bounded)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _policy_conn() -> sqlite3.Connection:
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


def _policy_file_conn(db_path) -> sqlite3.Connection:
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


def _bootstrap_policy_tables(conn: sqlite3.Connection) -> None:
    from src.state.db import apply_architecture_kernel_schema

    apply_architecture_kernel_schema(conn)


def _init_empty_canonical_portfolio_schema(
    db_path,
    *,
    drop_risk_actions: bool = False,
) -> None:
    """Create canonical DB tables with an empty, healthy position_current view."""

    conn = get_connection(db_path)
    init_schema(conn)
    if drop_risk_actions:
        conn.execute("DROP TABLE IF EXISTS risk_actions")
    conn.commit()
    conn.close()


def _insert_risk_action(
    conn: sqlite3.Connection,
    *,
    action_id: str,
    strategy_key: str,
    action_type: str,
    value: str,
    issued_at: str,
    effective_until: str | None,
    precedence: int = 10,
    status: str = "active",
) -> None:
    conn.execute(
        """
        INSERT INTO risk_actions (
            action_id, strategy_key, action_type, value, issued_at,
            effective_until, reason, source, precedence, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_id,
            strategy_key,
            action_type,
            value,
            issued_at,
            effective_until,
            "test",
            "riskguard",
            precedence,
            status,
        ),
    )


def _insert_position_current(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy_key: str,
    phase: str = "active",
    size_usd: float = 0.0,
    shares: float = 0.0,
    cost_basis_usd: float = 0.0,
    last_monitor_market_price: float | None = None,
    temperature_metric: str = "high",
    token_id: str = "yes-test-token",
    no_token_id: str = "no-test-token",
    condition_id: str = "condition-test",
    decision_law_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id, order_status, updated_at,
            temperature_metric, fill_authority, decision_law_id
        ) VALUES (?, ?, ?, 'm-test', 'NYC', 'NYC', '2026-04-01', '39-40°F', 'buy_yes', 'F', ?, ?, ?, ?, NULL, NULL, NULL, ?, '', '', ?, '', '', 'unknown', ?, ?, ?, '', '', ?, ?, 'none', ?)
        """,
        (
            position_id,
            phase,
            position_id,
            size_usd,
            shares,
            cost_basis_usd,
            cost_basis_usd / shares if shares else 0.0,
            last_monitor_market_price,
            strategy_key,
            token_id,
            no_token_id,
            condition_id,
            "2026-04-04T12:00:00+00:00",
            temperature_metric,
            decision_law_id,
        ),
    )


def _persist_decision_law_identities(db_path, rows: list[dict]) -> None:
    conn = get_connection(db_path)
    for row in rows:
        trade_id = str(row.get("trade_id") or "").strip()
        decision_law_id = str(row.get("decision_law_id") or "").strip()
        if not trade_id or not decision_law_id:
            continue
        _insert_position_current(
            conn,
            position_id=trade_id,
            strategy_key="forecast_qkernel_entry",
            decision_law_id=decision_law_id,
        )
    conn.commit()
    conn.close()


def _insert_outcome_fact(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy_key: str,
    settled_at: str,
    pnl: float,
    outcome: int,
) -> None:
    conn.execute(
        """
        INSERT INTO outcome_fact (
            position_id, strategy_key, entered_at, exited_at, settled_at,
            exit_reason, admin_exit_reason, decision_snapshot_id, pnl, outcome,
            hold_duration_hours, monitor_count, chain_corrections_count
        ) VALUES (?, ?, NULL, NULL, ?, '', '', '', ?, ?, NULL, 0, 0)
        """,
        (
            position_id,
            strategy_key,
            settled_at,
            pnl,
            outcome,
        ),
    )


def _append_verified_settlement_event(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy_key: str,
    settled_at: str,
    pnl: float,
    outcome: int,
    sequence_no: int,
    settlement_authority: str = "VERIFIED",
    settlement_truth_source: str = "world.settlements",
    settlement_source: str = "WU",
    include_settlement_value: bool = True,
) -> None:
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project

    pos = Position(
        trade_id=position_id,
        market_id=f"m-{position_id}",
        city="NYC",
        cluster="NYC",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.7,
        decision_snapshot_id=f"snap-{position_id}",
        strategy_key=strategy_key,
        strategy=strategy_key,
        edge_source=strategy_key,
        exit_price=1.0 if outcome == 1 else 0.0,
        pnl=pnl,
        exit_reason="SETTLEMENT",
        last_exit_at=settled_at,
        state="settled",
    )
    events, projection = build_settlement_canonical_write(
        pos,
        winning_bin="39-40°F" if outcome == 1 else "41-42°F",
        won=bool(outcome),
        outcome=outcome,
        sequence_no=sequence_no,
        phase_before="pending_exit",
        settlement_authority=settlement_authority,
        settlement_truth_source=settlement_truth_source,
        settlement_market_slug=f"nyc-high-{position_id}",
        settlement_temperature_metric="high",
        settlement_source=settlement_source,
        settlement_value=(
            (40.0 if outcome == 1 else 42.0)
            if include_settlement_value
            else None
        ),
    )
    append_many_and_project(conn, events, projection)


def _insert_execution_fact(
    conn: sqlite3.Connection,
    *,
    intent_id: str,
    strategy_key: str,
    terminal_exec_status: str,
    posted_at: str,
    filled_at: str | None = None,
    fill_price: float | None = None,
    shares: float | None = None,
    venue_status: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO execution_fact (
            intent_id, position_id, decision_id, order_role, strategy_key, posted_at,
            filled_at, voided_at, submitted_price, fill_price, shares, fill_quality,
            latency_seconds, venue_status, terminal_exec_status
        ) VALUES (?, ?, NULL, 'entry', ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            intent_id,
            intent_id,
            strategy_key,
            posted_at,
            filled_at,
            fill_price,
            shares,
            venue_status,
            terminal_exec_status,
        ),
    )


def test_riskguard_recent_exits_use_economic_not_metric_readiness():
    rows = [
        {
            "city": "NYC",
            "range_label": "economic-only-bin",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "exit_reason": "SETTLEMENT",
            "settled_at": "2026-04-01T23:00:00Z",
            "pnl": -3.5,
            "metric_ready": False,
            "settlement_authority": "VENUE_RESOLVED",
            "authority_level": "durable_event",
            "required_missing_fields": [],
        },
        {
            "city": "NYC",
            "range_label": "malformed-bin",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "exit_reason": "SETTLEMENT",
            "settled_at": "2026-04-01T23:30:00Z",
            "pnl": 99.0,
            "metric_ready": True,
            "settlement_authority": "VERIFIED",
            "authority_level": "durable_event_malformed",
            "required_missing_fields": ["trade_id"],
        },
    ]

    assert riskguard_module._canonical_recent_exits_from_settlement_rows(rows) == [
        {
            "city": "NYC",
            "bin_label": "economic-only-bin",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "token_id": "",
            "no_token_id": "",
            "exit_reason": "SETTLEMENT",
            "exited_at": "2026-04-01T23:00:00Z",
            "pnl": -3.5,
            "strategy_key": "",
            "loss_eligible": True,
            "loss_exclusion_reason": "",
        }
    ]


def test_loss_breaker_excludes_balance_only_chain_recovery_but_keeps_system_loss():
    now = "2026-07-10T15:00:00+00:00"
    snapshot = riskguard_module._realized_window_loss_telemetry(
        [
            {
                "exited_at": "2026-07-10T14:00:00+00:00",
                "pnl": -186.72,
                "loss_eligible": False,
            },
            {
                "exited_at": "2026-07-10T14:30:00+00:00",
                "pnl": -8.56,
                "loss_eligible": True,
            },
        ],
        now=now,
        lookback=timedelta(hours=24),
        degraded=False,
        source="test",
    )

    assert "level" not in snapshot
    assert snapshot["loss"] == pytest.approx(8.56)
    assert snapshot["reference"]["settlement_count"] == 1
    assert snapshot["reference"]["excluded_unowned_settlement_count"] == 1
    assert snapshot["reference"]["excluded_unowned_realized_pnl"] == pytest.approx(-186.72)


def test_system_authorized_loss_remains_diagnostic_only():
    snapshot = riskguard_module._realized_window_loss_telemetry(
        [
            {
                "exited_at": "2026-07-10T14:30:00+00:00",
                "pnl": -100.0,
                "loss_eligible": True,
            }
        ],
        now="2026-07-10T15:00:00+00:00",
        lookback=timedelta(hours=24),
        degraded=False,
        source="test",
    )

    assert "level" not in snapshot
    assert snapshot["loss"] == pytest.approx(100.0)


def test_current_mode_realized_exits_prefers_verified_settlements_over_outcome_fact():
    conn = _policy_conn()
    _insert_outcome_fact(
        conn,
        position_id="authorityless-outcome",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",
        pnl=99.0,
        outcome=1,
    )
    settlement_rows = [
        {
            "city": "NYC",
            "range_label": "39-40°F",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "exit_reason": "SETTLEMENT",
            "settled_at": "2026-04-03T12:00:00+00:00",
            "pnl": 4.25,
            "metric_ready": True,
            "settlement_authority": "VERIFIED",
        }
    ]

    exits, source, degraded = riskguard_module._current_mode_realized_exits(
        conn,
        settlement_rows=settlement_rows,
    )

    assert source == "authoritative_settlement_rows"
    assert degraded is False
    assert [exit_row["pnl"] for exit_row in exits] == [4.25]


def test_current_mode_realized_exits_blocks_malformed_economic_rows_without_outcome_fact_fallback():
    conn = _policy_conn()
    _insert_outcome_fact(
        conn,
        position_id="authorityless-outcome",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",
        pnl=99.0,
        outcome=1,
    )
    settlement_rows = [
        {
            "city": "NYC",
            "range_label": "legacy-bin",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "exit_reason": "SETTLEMENT",
            "settled_at": "2026-04-03T12:00:00+00:00",
            "pnl": 99.0,
            "metric_ready": False,
            "is_degraded": True,
            "settlement_authority": "LEGACY_UNKNOWN",
            "authority_level": "durable_event_malformed",
            "required_missing_fields": ["trade_id"],
        }
    ]

    exits, source, degraded = riskguard_module._current_mode_realized_exits(
        conn,
        settlement_rows=settlement_rows,
    )

    assert source == "authoritative_settlement_rows"
    assert degraded is True
    assert exits == []


def test_current_mode_realized_exits_chronicle_fallback_filters_current_env(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE chronicle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            trade_id INTEGER,
            timestamp TEXT NOT NULL,
            details_json TEXT NOT NULL,
            env TEXT NOT NULL
        )
        """
    )
    for env, pnl in (("live", 99.0), ("test", 4.25)):
        conn.execute(
            """
            INSERT INTO chronicle (event_type, trade_id, timestamp, details_json, env)
            VALUES ('SETTLEMENT', 101, '2026-04-03T12:00:00+00:00', ?, ?)
            """,
            (
                json.dumps(
                    {
                        "city": "NYC",
                        "range_label": "39-40°F",
                        "target_date": "2026-04-01",
                        "direction": "buy_yes",
                        "exit_reason": "SETTLEMENT",
                        "pnl": pnl,
                    }
                ),
                env,
            ),
        )
    monkeypatch.setattr(riskguard_module, "get_mode", lambda: "test")

    exits, source, degraded = riskguard_module._current_mode_realized_exits(conn)
    conn.close()

    assert source == "chronicle_dedup"
    assert degraded is True
    assert [exit_row["pnl"] for exit_row in exits] == [4.25]


def _insert_risk_state_row(
    conn: sqlite3.Connection,
    *,
    checked_at: str,
    level: str = "GREEN",
    initial_bankroll: float = 211.37,
    total_pnl: float = 0.0,
    effective_bankroll: float | None = None,
    execution_quality_level: str = "GREEN",
    strategy_signal_level: str = "GREEN",
    recommended_controls: list[str] | None = None,
    recommended_strategy_gates: list[str] | None = None,
) -> int:
    """Insert a risk_state row that `_risk_state_reference_from_row` accepts.

    P0-A (2026-05-01): DEF A semantics — effective_bankroll defaults to
    initial_bankroll (= wallet snapshot, no PnL math). Tests that pass an
    explicit `effective_bankroll` are honoured but those values must satisfy
    `abs(initial_bankroll - effective_bankroll) <= TRAILING_LOSS_ROW_TOLERANCE_USD`
    or the reference loader will reject them. Provenance tag
    `bankroll_truth_source = "polymarket_wallet"` is added so the cutover-day
    filter accepts these rows as eligible references.
    """
    if effective_bankroll is None:
        effective_bankroll = round(initial_bankroll, 2)  # DEF A: equity == wallet
    cur = conn.execute(
        """
        INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
        VALUES (?, NULL, NULL, NULL, ?, ?)
        """,
        (
            level,
            json.dumps(
                {
                    "initial_bankroll": round(initial_bankroll, 2),
                    "total_pnl": round(total_pnl, 2),
                    "effective_bankroll": round(effective_bankroll, 2),
                    "bankroll_truth_source": "polymarket_wallet",
                    "execution_quality_level": execution_quality_level,
                    "strategy_signal_level": strategy_signal_level,
                    "recommended_controls": list(recommended_controls or []),
                    "recommended_strategy_gates": list(recommended_strategy_gates or []),
                }
            ),
            checked_at,
        ),
    )
    return int(cur.lastrowid)


def _insert_control_override(
    conn: sqlite3.Connection,
    *,
    override_id: str,
    target_type: str,
    target_key: str,
    action_type: str,
    value: str,
    issued_at: str,
    effective_until: str | None,
    precedence: int = 100,
) -> None:
    # B070: control_overrides is now a VIEW. Seed the append-only history
    # directly with operation='upsert' and recorded_at=issued_at so the VIEW
    # projects this row as the latest.
    conn.execute(
        """
        INSERT INTO control_overrides_history (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, effective_until, reason, precedence,
            operation, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upsert', ?)
        """,
        (
            override_id,
            target_type,
            target_key,
            action_type,
            value,
            "test",
            issued_at,
            effective_until,
            "test",
            precedence,
            issued_at,
        ),
    )


def _neutralize_hard_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(policy_module, "get_edge_threshold_multiplier", lambda: 1.0)


def _mock_trailing_loss_tick(
    monkeypatch: pytest.MonkeyPatch,
    *,
    zeus_db,
    risk_db,
    realized_pnl: float,
    unrealized_pnl: float = 0.0,
    portfolio: PortfolioState | None = None,
) -> None:
    def _fake_get_connection(path=None, **_kwargs):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(
        riskguard_module,
        "load_portfolio",
        lambda: portfolio or PortfolioState(bankroll=211.37, daily_baseline_total=211.37, weekly_baseline_total=211.37),
    )
    monkeypatch.setattr(
        riskguard_module,
        "query_authoritative_settlement_rows",
        lambda conn, limit=50, **kwargs: [],
    )
    monkeypatch.setattr(
        riskguard_module,
        "refresh_strategy_health",
        lambda conn, as_of=None, **kwargs: {"status": "refreshed", "rows_written": 1},
    )
    monkeypatch.setattr(
        riskguard_module,
        "query_strategy_health_snapshot",
        lambda conn, now=None: {
            "status": "fresh",
            "by_strategy": {
                "center_buy": {
                    "realized_pnl_30d": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                }
            },
        },
    )
    monkeypatch.setattr(
        riskguard_module,
        "load_tracker",
        lambda: strategy_tracker_module.StrategyTracker(),
    )


class TestRiskLevel:
    def test_overall_all_green(self):
        assert overall_level(RiskLevel.GREEN, RiskLevel.GREEN) == RiskLevel.GREEN

    def test_overall_worst_wins(self):
        assert overall_level(RiskLevel.GREEN, RiskLevel.ORANGE) == RiskLevel.ORANGE
        assert overall_level(RiskLevel.YELLOW, RiskLevel.RED) == RiskLevel.RED

    def test_overall_empty(self):
        assert overall_level() == RiskLevel.GREEN


class TestMetrics:
    def test_brier_perfect(self):
        """Perfect forecasts → Brier = 0."""
        assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)

    def test_brier_worst(self):
        """Completely wrong → Brier = 1."""
        assert brier_score([0.0, 1.0], [1, 0]) == pytest.approx(1.0)

    def test_brier_moderate(self):
        score = brier_score([0.7, 0.3, 0.6], [1, 0, 1])
        assert 0 < score < 0.5

    def test_directional_accuracy_perfect(self):
        assert directional_accuracy([0.8, 0.2, 0.9], [1, 0, 1]) == pytest.approx(1.0)

    def test_riskguard_brier_sample_skips_non_learning_backfill_rows(self):
        rows = [
            {
                "id": "newest-repair-no-snapshot",
                "learning_snapshot_ready": False,
                "metric_ready": True,
                "p_posterior": 0.99,
                "outcome": 0,
            },
            {
                "id": "learning-ready-1",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "probability_identity_ready": True,
                "p_posterior": 0.78,
                "outcome": 1,
            },
            {
                "id": "missing-prob",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "probability_identity_ready": True,
                "p_posterior": None,
                "outcome": 1,
            },
            {
                "id": "metric-not-ready",
                "learning_snapshot_ready": True,
                "metric_ready": False,
                "probability_identity_ready": True,
                "p_posterior": 0.65,
                "outcome": 1,
            },
            {
                "id": "learning-ready-2",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "probability_identity_ready": True,
                "p_posterior": 0.31,
                "outcome": 0,
            },
            {
                "id": "learning-ready-3",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "probability_identity_ready": True,
                "p_posterior": 0.52,
                "outcome": 1,
            },
        ]

        selected = riskguard_module._riskguard_brier_metric_rows(rows, limit=2)

        assert [row["id"] for row in selected] == ["learning-ready-1", "learning-ready-2"]

    def test_brier_sample_rejects_probability_without_q_identity(self):
        rows = [
            {
                "id": "unbound",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "probability_identity_ready": False,
                "p_posterior": 0.90,
                "outcome": 0,
            }
        ]

        assert riskguard_module._riskguard_brier_metric_rows(rows) == []

    @pytest.mark.parametrize(
        "truth_source",
        ["gamma_exact_held_event", "trades.payout_observations"],
    )
    def test_venue_resolved_outcome_grades_q_before_physical_value(
        self,
        truth_source,
    ):
        from src.state.db import _normalize_position_settlement_event

        normalized = _normalize_position_settlement_event(
            {
                "runtime_trade_id": "venue-resolved-loss",
                "city": "Guangzhou",
                "target_date": "2026-07-24",
                "bin_label": "36°C",
                "direction": "buy_no",
                "decision_snapshot_id": "metar-fast-zggg",
                "edge_source": "settlement_capture",
                "strategy": "settlement_capture",
                "timestamp": "2026-07-24T22:19:15Z",
                "env": "live",
                "details": {
                    "contract_version": "position_settled.v1",
                    "winning_bin": "",
                    "position_bin": "36°C",
                    "won": False,
                    "outcome": 0,
                    "p_posterior": 0.999999999,
                    "exit_price": 0.0,
                    "pnl": -1.768,
                    "exit_reason": "SETTLEMENT",
                    "settlement_authority": "VENUE_RESOLVED",
                    "settlement_truth_source": truth_source,
                    "settlement_market_slug": "guangzhou-high-2026-07-24",
                    "settlement_temperature_metric": "high",
                    "settlement_source": (
                        "polymarket_chain_rpc_finalized_v1"
                        if truth_source == "trades.payout_observations"
                        else "gamma"
                    ),
                    "settlement_value": None,
                },
            }
        )

        assert normalized is not None
        assert normalized["probability_outcome_ready"] is True
        assert normalized["learning_snapshot_ready"] is True
        assert normalized["metric_ready"] is False
        normalized["probability_identity_ready"] = True
        assert riskguard_module._riskguard_brier_metric_rows([normalized]) == [
            normalized
        ]

    def test_unfinalized_chain_payout_cannot_grade_probability(self):
        from src.state.db import _normalize_position_settlement_event

        normalized = _normalize_position_settlement_event(
            {
                "runtime_trade_id": "unfinalized-chain-payout",
                "city": "Busan",
                "target_date": "2026-08-18",
                "bin_label": "29°C",
                "direction": "buy_yes",
                "decision_snapshot_id": "entry-q",
                "strategy": "forecast_qkernel_entry",
                "timestamp": "2026-08-18T16:23:37Z",
                "env": "live",
                "details": {
                    "contract_version": "position_settled.v1",
                    "winning_bin": "",
                    "position_bin": "29°C",
                    "won": False,
                    "outcome": 0,
                    "p_posterior": 0.41,
                    "exit_price": 0.0,
                    "pnl": -1.2,
                    "exit_reason": "SETTLEMENT",
                    "settlement_authority": "VENUE_RESOLVED",
                    "settlement_truth_source": "trades.payout_observations",
                    "settlement_market_slug": "busan-high-2026-08-18",
                    "settlement_temperature_metric": "high",
                    "settlement_source": "chain_rpc_latest_unfinalized",
                    "settlement_value": None,
                },
            }
        )

        assert normalized is not None
        assert normalized["probability_outcome_ready"] is False
        assert normalized["learning_snapshot_ready"] is False

    def test_probability_identity_binding_requires_one_complete_entry_q_version(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE venue_commands ("
            "position_id TEXT,intent_kind TEXT,q_version TEXT)"
        )
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY,decision_law_id TEXT)"
        )
        conn.executemany(
            "INSERT INTO venue_commands VALUES (?,?,?)",
            [
                ("bound", "ENTRY", "q-v1"),
                ("missing", "ENTRY", None),
                ("conflict", "ENTRY", "q-v1"),
                ("conflict", "ENTRY", "q-v2"),
                ("legacy", "ENTRY", "q-old"),
                ("claimed", "ENTRY", "q-claimed"),
            ],
        )
        conn.executemany(
            "INSERT INTO position_current VALUES (?,?)",
            [
                ("bound", "predicted_bin_ev_v1"),
                ("missing", "predicted_bin_ev_v1"),
                ("conflict", "predicted_bin_ev_v1"),
                ("legacy", None),
            ],
        )

        bound = riskguard_module._bind_brier_probability_identities(
            conn,
            [
                {"trade_id": "bound"},
                {"trade_id": "missing"},
                {"trade_id": "conflict"},
                {"trade_id": "absent"},
                {"trade_id": "legacy"},
                {
                    "trade_id": "claimed",
                    "decision_law_id": "predicted_bin_ev_v1",
                },
            ],
        )

        assert bound[0]["probability_identity_ready"] is True
        assert bound[0]["entry_q_version"] == "q-v1"
        assert bound[0]["decision_law_identity_ready"] is True
        assert bound[0]["decision_law_id"] == "predicted_bin_ev_v1"
        assert bound[1]["probability_identity_blocked_reason"] == "entry_q_version_missing"
        assert bound[2]["probability_identity_blocked_reason"] == "entry_q_version_conflicting"
        assert bound[3]["probability_identity_blocked_reason"] == "entry_command_missing"
        assert bound[4]["probability_identity_ready"] is True
        assert bound[4]["decision_law_identity_ready"] is False
        assert bound[4]["decision_law_identity_blocked_reason"] == "decision_law_id_missing"
        assert bound[5]["probability_identity_ready"] is True
        assert bound[5]["decision_law_identity_ready"] is False
        assert bound[5]["decision_law_id"] == ""
        assert bound[5]["decision_law_identity_blocked_reason"] == "decision_law_id_missing"
        assert riskguard_module._riskguard_brier_actuating_rows(bound) == [bound[0]]
        conn.close()

    def test_probability_identity_binding_composites_economically_filled_entries(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE venue_commands ("
            "position_id TEXT,command_id TEXT,intent_kind TEXT,q_version TEXT,"
            "state TEXT)"
        )
        conn.execute(
            "CREATE TABLE execution_fact ("
            "command_id TEXT,order_role TEXT,filled_at TEXT,"
            "terminal_exec_status TEXT,shares REAL)"
        )
        conn.execute(
            "CREATE TABLE provenance_envelope_events ("
            "subject_type TEXT,subject_id TEXT,event_type TEXT,"
            "local_sequence INTEGER,payload_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY,decision_law_id TEXT)"
        )
        conn.executemany(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?)",
            [
                ("composite", "filled-a", "ENTRY", "q-v1", "FILLED"),
                ("composite", "filled-b", "ENTRY", "q-v2", "FILLED"),
                ("composite", "partial-c", "ENTRY", "q-v3", "CANCELLED"),
                ("composite", "confirmed-d", "ENTRY", "q-v4", "FILLED"),
                ("composite", "rejected", "ENTRY", "q-v3", "REJECTED"),
            ],
        )
        conn.executemany(
            "INSERT INTO execution_fact VALUES (?,?,?,?,?)",
            [
                ("filled-a", "entry", "2026-07-27T00:00:00Z", "filled", 4.0),
                ("filled-a", "entry", "2026-07-27T00:00:01Z", "filled", 4.0),
                ("filled-b", "entry", "2026-07-27T00:01:00Z", "filled", 6.0),
                ("partial-c", "entry", "2026-07-27T00:02:00Z", "partial", 2.0),
                ("confirmed-d", "entry", "2026-07-27T00:03:00Z", "confirmed", 3.0),
            ],
        )

        def submit_payload(q_live: float) -> str:
            return json.dumps(
                {
                    "payload": {
                        "execution_capability": {
                            "components": [
                                {
                                    "component": "entry_economics",
                                    "details": {"q_live": q_live},
                                }
                            ]
                        }
                    }
                }
            )

        conn.executemany(
            "INSERT INTO provenance_envelope_events VALUES (?,?,?,?,?)",
            [
                ("command", "filled-a", "SUBMIT_REQUESTED", 1, submit_payload(0.8)),
                ("command", "filled-b", "SUBMIT_REQUESTED", 1, submit_payload(0.6)),
                ("command", "partial-c", "SUBMIT_REQUESTED", 1, submit_payload(0.4)),
                ("command", "confirmed-d", "SUBMIT_REQUESTED", 1, submit_payload(0.9)),
            ],
        )
        conn.execute(
            "INSERT INTO position_current VALUES (?,?)",
            ("composite", "predicted_bin_ev_v1"),
        )

        bound = riskguard_module._bind_brier_probability_identities(
            conn,
            [{"trade_id": "composite", "p_posterior": 0.99}],
        )

        assert bound[0]["probability_identity_ready"] is True
        assert bound[0]["p_posterior"] == pytest.approx(10.3 / 15.0)
        assert bound[0]["entry_q_version"].startswith("filled-entry-composite:")
        assert bound[0]["entry_q_versions"] == ("q-v1", "q-v2", "q-v3", "q-v4")
        assert (
            bound[0]["probability_identity_source"]
            == "filled_entry_commands.q_version+submit_q_live+fill_shares"
        )
        assert bound[0]["decision_law_identity_ready"] is True
        conn.close()

    def test_qkernel_brier_actuation_uses_only_current_probability_semantics(
        self,
        tmp_path,
    ):
        forecasts_db = tmp_path / "zeus-forecasts.db"
        conn = sqlite3.connect(forecasts_db)
        conn.execute(
            "CREATE TABLE forecast_posteriors ("
            "posterior_identity_hash TEXT PRIMARY KEY,provenance_json TEXT)"
        )

        def provenance(
            semantics_revision: str,
            *,
            stale: bool = False,
            translated: bool = False,
        ) -> str:
            return json.dumps(
                {
                    "bayes_precision_fusion": {
                        "current_evidence_shape": {
                            "semantics_revision": semantics_revision,
                            "translation_applied": translated,
                            "shape_lag_hours": 1.0 if stale else 0.0,
                            "stale_shape_reused": stale,
                        }
                    }
                }
            )

        conn.executemany(
            "INSERT INTO forecast_posteriors VALUES (?,?)",
            [
                (
                    "q-current",
                    provenance(
                        riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
                    ),
                ),
                (
                    "q-stale",
                    provenance(
                        riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
                        stale=True,
                    ),
                ),
                (
                    "q-superseded",
                    provenance("ensemble_anomaly_transport_v3"),
                ),
                (
                    "q-translated",
                    provenance(
                        riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
                        translated=True,
                    ),
                ),
            ],
        )
        conn.commit()
        conn.close()
        common = {
            "strategy": "forecast_qkernel_entry",
            "probability_identity_ready": True,
            "decision_law_identity_ready": True,
            "decision_law_id": "predicted_bin_ev_v1",
        }
        rows = [
            {**common, "trade_id": "current", "entry_q_versions": ("q-current",)},
            {**common, "trade_id": "stale", "entry_q_versions": ("q-stale",)},
            {
                **common,
                "trade_id": "superseded",
                "entry_q_versions": ("q-superseded",),
            },
            {
                **common,
                "trade_id": "translated",
                "entry_q_versions": ("q-translated",),
            },
            {
                **common,
                "trade_id": "mixed",
                "entry_q_versions": ("q-current", "q-superseded"),
            },
            {**common, "trade_id": "missing", "entry_q_versions": ("q-missing",)},
            {
                **common,
                "trade_id": "no-lineage",
                "entry_q_versions": (),
            },
            {
                **common,
                "trade_id": "day0",
                "strategy": "day0_nowcast_entry",
            },
        ]

        bound, status = riskguard_module._bind_qkernel_probability_semantics(
            rows,
            forecasts_connection_factory=lambda: sqlite3.connect(forecasts_db),
        )

        by_id = {row["trade_id"]: row for row in bound}
        assert by_id["current"]["probability_semantics_ready"] is True
        assert by_id["stale"]["probability_semantics_ready"] is False
        assert by_id["stale"]["probability_semantics_blocked_reason"] == (
            "superseded_probability_semantics"
        )
        assert by_id["superseded"]["probability_semantics_blocked_reason"] == (
            "superseded_probability_semantics"
        )
        assert by_id["translated"]["probability_semantics_ready"] is False
        assert by_id["mixed"]["probability_semantics_blocked_reason"] == (
            "mixed_probability_semantics"
        )
        assert by_id["missing"]["probability_semantics_blocked_reason"] == (
            "probability_semantics_provenance_missing"
        )
        assert by_id["no-lineage"]["probability_semantics_blocked_reason"] == (
            "entry_q_version_lineage_missing"
        )
        assert status == {
            "status": "ok",
            "licensed_revisions": [
                riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
            ],
            "strategy_candidate_count": 7,
            "current_count": 1,
            "superseded_count": 3,
            "missing_count": 2,
            "mixed_count": 1,
        }
        assert [
            row["trade_id"]
            for row in riskguard_module._riskguard_brier_actuating_rows(bound)
        ] == ["current"]

    def test_qkernel_semantics_lookup_unavailable_fails_closed(self, tmp_path):
        row = {
            "trade_id": "qkernel",
            "strategy": "forecast_qkernel_entry",
            "entry_q_versions": ("q-current",),
            "probability_identity_ready": True,
            "decision_law_identity_ready": True,
            "decision_law_id": "predicted_bin_ev_v1",
        }

        bound, status = riskguard_module._bind_qkernel_probability_semantics(
            [row],
            forecasts_connection_factory=lambda: sqlite3.connect(
                f"file:{tmp_path / 'missing.db'}?mode=ro",
                uri=True,
            ),
        )

        assert status["status"] == "unavailable"
        assert bound[0]["probability_semantics_ready"] is False
        assert bound[0]["probability_semantics_blocked_reason"] == (
            "probability_semantics_authority_unavailable"
        )
        assert riskguard_module._riskguard_brier_actuating_rows(bound) == []

    def test_day0_brier_actuation_excludes_superseded_and_mixed_semantics(self):
        from src.events.day0_authority import bind_day0_probability_semantics

        common = {
            "strategy": "day0_nowcast_entry",
            "probability_identity_ready": True,
            "decision_law_identity_ready": True,
            "decision_law_id": "predicted_bin_ev_v1",
        }
        current = bind_day0_probability_semantics("q-current")
        rows = [
            {**common, "trade_id": "current", "entry_q_versions": (current,)},
            {**common, "trade_id": "legacy", "entry_q_versions": ("q-old",)},
            {
                **common,
                "trade_id": "mixed",
                "entry_q_versions": (current, "q-old"),
            },
            {**common, "trade_id": "missing", "entry_q_versions": ()},
        ]

        bound, status = riskguard_module._bind_day0_probability_semantics(rows)
        by_id = {row["trade_id"]: row for row in bound}

        assert by_id["current"]["probability_semantics_ready"] is True
        assert by_id["legacy"]["probability_semantics_blocked_reason"] == (
            "superseded_probability_semantics"
        )
        assert by_id["mixed"]["probability_semantics_blocked_reason"] == (
            "mixed_probability_semantics"
        )
        assert by_id["missing"]["probability_semantics_blocked_reason"] == (
            "entry_q_version_lineage_missing"
        )
        assert status["current_count"] == 1
        assert status["superseded_count"] == 1
        assert status["mixed_count"] == 1
        assert status["missing_count"] == 1
        assert [
            row["trade_id"]
            for row in riskguard_module._riskguard_brier_actuating_rows(bound)
        ] == ["current"]

    def test_probability_identity_binding_rejects_filled_command_without_fact(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE venue_commands ("
            "position_id TEXT,command_id TEXT,intent_kind TEXT,q_version TEXT,"
            "state TEXT)"
        )
        conn.execute(
            "CREATE TABLE execution_fact ("
            "command_id TEXT,order_role TEXT,filled_at TEXT,"
            "terminal_exec_status TEXT,shares REAL)"
        )
        conn.execute(
            "CREATE TABLE provenance_envelope_events ("
            "subject_type TEXT,subject_id TEXT,event_type TEXT,"
            "local_sequence INTEGER,payload_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY,decision_law_id TEXT)"
        )
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?)",
            ("incomplete", "missing-fact", "ENTRY", "q-v1", "FILLED"),
        )
        conn.execute(
            "INSERT INTO position_current VALUES (?,?)",
            ("incomplete", "predicted_bin_ev_v1"),
        )

        bound = riskguard_module._bind_brier_probability_identities(
            conn,
            [{"trade_id": "incomplete", "p_posterior": 0.8}],
        )

        assert bound[0]["probability_identity_ready"] is False
        assert (
            bound[0]["probability_identity_blocked_reason"]
            == "filled_entry_evidence_incomplete"
        )
        conn.close()


def _settlement_row(
    *,
    trade_id: str,
    strategy: str,
    p_posterior: float,
    outcome: int,
    pnl: float = 0.0,
    decision_law_id: str | None = "predicted_bin_ev_v1",
    target_date: str = "2026-04-01",
) -> dict:
    return {
        "trade_id": trade_id,
        "strategy": strategy,
        "p_posterior": p_posterior,
        "outcome": outcome,
        "source": "position_events",
        "authority_level": "VERIFIED",
        "metric_ready": True,
        "learning_snapshot_ready": True,
        "probability_identity_ready": True,
        "probability_semantics_ready": True,
        "entry_q_version": "test-q-version",
        "decision_law_id": decision_law_id,
        "canonical_payload_complete": True,
        "is_degraded": False,
        "pnl": pnl,
        "city": "NYC",
        "range_label": "29C",
        "target_date": target_date,
        "direction": "buy_yes",
        "settled_at": "2026-04-02T00:00:00+00:00",
    }


def _independent_target_date(index: int) -> str:
    return (datetime(2026, 1, 1) + timedelta(days=index)).date().isoformat()



class TestRiskEvaluation:
    def test_brier_green(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35}
        assert evaluate_brier(0.20, thresholds) == RiskLevel.GREEN

    def test_brier_yellow(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35}
        assert evaluate_brier(0.27, thresholds) == RiskLevel.YELLOW

    def test_brier_red(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35}
        assert evaluate_brier(0.40, thresholds) == RiskLevel.RED


class TestRiskGuardSettlementSource:
    def test_tick_separates_bounded_quality_scan_from_complete_realized_window(
        self,
        monkeypatch,
        tmp_path,
    ):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        _init_empty_canonical_portfolio_schema(zeus_db)
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        risk_conn.close()
        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=0.0,
        )
        _patch_riskguard_bankroll(monkeypatch)
        calls: list[tuple[int | None, str | None]] = []

        def _query_authoritative_settlement_rows(conn, limit=50, **kwargs):  # noqa: ANN001, ARG001
            calls.append((limit, kwargs.get("not_before")))
            if kwargs.get("not_before") is None:
                return []
            settled_at = datetime.now(timezone.utc).isoformat()
            return [
                {
                    "pnl": -1.0,
                    "settled_at": settled_at,
                    "authority_level": "durable_event",
                    "required_missing_fields": [],
                    "strategy": "center_buy",
                }
                for _ in range(60)
            ]

        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            _query_authoritative_settlement_rows,
        )

        level = riskguard_module.tick()

        assert level == RiskLevel.GREEN
        assert calls[0] == (riskguard_module.RISKGUARD_BRIER_SCAN_LIMIT, None)
        assert calls[1][0] is None
        cutoff = datetime.fromisoformat(str(calls[1][1]))
        expected = (
            datetime.now(timezone.utc)
            - riskguard_module.RISKGUARD_REALIZED_TELEMETRY_WINDOW
        )
        assert abs((cutoff - expected).total_seconds()) < 5
        conn = get_connection(risk_db)
        row = conn.execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        details = json.loads(row["details_json"])
        assert details["weekly_loss_reference"]["settlement_count"] == 60
        assert details["weekly_loss_reference"]["realized_pnl_window"] == pytest.approx(
            -60.0
        )

    def test_tick_floors_fresh_green_to_data_degraded_when_dependency_db_metrics_lock(self, monkeypatch, tmp_path):
        """Relationship (AGENTS.md iron #6 — FAIL CONSERVATIVE): a metric DB lock
        over a fresh GREEN full row must NOT re-stamp GREEN.

        LAW CHANGE (2026-06-08 live fail-open remediation): the previous behavior
        preserved the prior fresh level verbatim, which re-stamped GREEN through a
        window where RiskGuard could not compute risk — a fail-open. The
        conservative floor is now max(previous_level, DATA_DEGRADED): a fresh GREEN
        floors to DATA_DEGRADED (blocks new entries, preserves positions) while the
        previous level is still recorded in details for audit. The previous_level
        carry-forward of a STRONGER halt (RED/ORANGE/YELLOW) is covered by the
        dedicated tests in test_wal_busy_factory_fail_conservative.py.
        """
        risk_db = tmp_path / "risk_state.db"
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat(),
            level=RiskLevel.GREEN.value,
        )
        risk_conn.commit()
        risk_conn.close()

        class _LockedTradeConn:
            def __init__(self):
                self.rollback_called = False
                self.close_called = False

            def rollback(self):
                self.rollback_called = True

            def close(self):
                self.close_called = True

        trade_conn = _LockedTradeConn()

        def _fake_get_connection(path=None, **_kwargs):
            assert path == riskguard_module.RISK_DB_PATH
            return get_connection(risk_db)

        def _raise_trade_db_locked(_conn):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "_get_runtime_trade_connection", lambda: trade_conn)
        monkeypatch.setattr(riskguard_module, "_load_riskguard_portfolio_truth", _raise_trade_db_locked)

        level = riskguard_module.tick()

        row = get_connection(risk_db).execute(
            "SELECT level, details_json, checked_at FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        # REGRESSION REVERTED (2026-06-08): a TRANSIENT dependency lock over a FRESH
        # (<5 min) GREEN full row PRESERVES GREEN — it does NOT floor to
        # DATA_DEGRADED. Risk (daily-loss/settlement-quality/Brier) is slow-moving
        # and unchanged within the 5-min freshness window, so a momentary lock must
        # not block the GREEN-only entry gate (the weeks-stable behavior). The earlier
        # max(prev, DATA_DEGRADED) floor downgraded every transient lock and blocked
        # all trading. Persistent locks (no fresh full row) still degrade — covered by
        # the no-fresh-row test; stronger halts (RED/ORANGE/YELLOW) carry forward via
        # test_wal_busy_factory_fail_conservative.py.
        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["status"] == "dependency_db_locked_previous_risk_level_preserved"
        assert details["riskguard_degraded_reason"] == "dependency_db_locked"
        assert details["full_metrics_status"] == "locked_previous_fresh_level_preserved"
        assert details["conservative_floor_applied"] is False
        assert details["previous_full_risk_level"] == RiskLevel.GREEN.value
        assert details["bankroll_truth_source"] == "polymarket_wallet"
        assert details["execution_quality_level"] == "GREEN"
        assert details["strategy_signal_level"] == "GREEN"
        assert details["recommended_controls"] == []
        assert details["recommended_strategy_gates"] == []
        # Single-authority read surfaces the preserved fresh GREEN to the entry gate.
        assert riskguard_module.get_current_level() == RiskLevel.GREEN
        assert trade_conn.rollback_called is True
        assert trade_conn.close_called is True

    def test_tick_degrades_when_dependency_db_metrics_lock_has_no_fresh_full_level(self, monkeypatch, tmp_path):
        """Relationship: old full risk truth cannot be extended past its TTL."""
        risk_db = tmp_path / "risk_state.db"
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
            level=RiskLevel.GREEN.value,
        )
        risk_conn.commit()
        risk_conn.close()

        class _LockedTradeConn:
            def rollback(self):
                pass

            def close(self):
                pass

        def _fake_get_connection(path=None, **_kwargs):
            assert path == riskguard_module.RISK_DB_PATH
            return get_connection(risk_db)

        def _raise_dependency_db_locked(_conn):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "_get_runtime_trade_connection", lambda: _LockedTradeConn())
        monkeypatch.setattr(riskguard_module, "_load_riskguard_portfolio_truth", _raise_dependency_db_locked)

        level = riskguard_module.tick()

        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.DATA_DEGRADED
        assert row["level"] == RiskLevel.DATA_DEGRADED.value
        assert details["status"] == "dependency_db_locked"
        assert details["full_metrics_status"] == "unavailable_no_fresh_full_risk_row"
        assert details["execution_quality_level"] == "DATA_DEGRADED"
        assert details["strategy_signal_level"] == "DATA_DEGRADED"
        assert details["recommended_controls"] == []
        assert details["recommended_strategy_gates"] == []
        assert riskguard_module.get_current_level() == RiskLevel.DATA_DEGRADED

    def test_tick_prefers_position_current_for_portfolio_truth(self, monkeypatch, tmp_path):
        # P0-A masking-test repoint (architect_memo §6, followup_design §2.1):
        # this test's axis is portfolio TRUTH-SOURCE preference (canonical_db
        # vs metadata fallback). Bankroll value is now provider-sourced, so
        # we monkeypatch `bankroll_provider.current()` instead of stuffing
        # PortfolioState(bankroll=211.37). Under DEF A, effective_bankroll
        # equals the wallet value with NO PnL math added (formerly a fixed-capital
        # literal plus PnL).
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        conn = get_connection(zeus_db)
        from src.state.db import init_schema

        init_schema(conn)
        _insert_position_current(
            conn,
            position_id="db-pos-1",
            strategy_key="center_buy",
            size_usd=25.0,
            shares=10.0,
            cost_basis_usd=20.0,
            last_monitor_market_price=2.5,
        )
        _insert_position_current(
            conn,
            position_id="db-pos-settled",
            strategy_key="center_buy",
            phase="settled",
            size_usd=1000.0,
            shares=1000.0,
            cost_basis_usd=1000.0,
            last_monitor_market_price=1.0,
        )
        conn.commit()
        conn.close()

        from src.runtime import bankroll_provider as _bp
        monkeypatch.setattr(
            _bp,
            "current",
            lambda **_kw: _bp.BankrollOfRecord(
                value_usd=211.37,
                fetched_at="2026-04-01T00:00:00+00:00",
                source="polymarket_wallet",
                authority="canonical",
                staleness_seconds=0.0,
                cached=False,
            ),
        )
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            # Legacy metadata is no longer consumed by RiskGuard; this sentinel
            # would change details_json if the redundant load_portfolio path came back.
            lambda: PortfolioState(
                bankroll=211.37,
                daily_baseline_total=151.0,
                weekly_baseline_total=152.0,
                recent_exits=[
                    {
                        "city": "NYC",
                        "bin_label": "39-40°F",
                        "target_date": "2026-04-01",
                        "direction": "buy_yes",
                        "token_id": "yes123",
                        "no_token_id": "no456",
                        "exit_reason": "SETTLEMENT",
                        "exited_at": "2026-03-30T00:00:00Z",
                        "pnl": -3.0,
                    }
                ],
            ),
        )
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{
                "p_posterior": 0.7,
                "outcome": 1,
                "source": "position_events",
                "metric_ready": True,
                "strategy": "center_buy",
                "pnl": -3.0,
            }],
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        # Truth-source axis (the original purpose of this test) — preserved.
        assert details["portfolio_truth_source"] == "position_current"
        assert details["portfolio_loader_status"] == "ok"
        assert details["portfolio_fallback_active"] is False
        # RiskGuard consumes current-money-risk rows only; terminal history is
        # graded through the separate settlement path and must not inflate the
        # live exposure count or force a full-table sort every minute.
        assert details["portfolio_position_count"] == 1
        assert details["portfolio_capital_source"] == "canonical_loader_view"
        # Bankroll truth axis: provider-sourced wallet cash plus canonical
        # open-position value, with no realized-PnL fold-in.
        assert details["initial_bankroll"] == pytest.approx(211.37)
        assert details["account_equity_components"]["wallet_cash_usd"] == pytest.approx(211.37)
        assert details["account_equity_components"]["open_position_equity_usd"] == pytest.approx(25.0)
        assert details["effective_bankroll"] == pytest.approx(236.37)
        assert details["bankroll_truth_source"] == "polymarket_wallet"
        # Baselines are intentionally uninitialized here. Live bankroll truth
        # comes from bankroll_provider, not the retired load_portfolio metadata path.
        assert details["daily_baseline_total"] == pytest.approx(0.0)
        assert details["weekly_baseline_total"] == pytest.approx(0.0)
        # PnL signals are still emitted for analytics, but realized PnL now
        # comes only from the strategy_health 30d read-model window.
        assert details["realized_pnl"] == pytest.approx(0.0)
        assert details["realized_pnl_source"] == "strategy_health.realized_pnl_30d"
        assert details["realized_pnl_window_days"] == 30
        assert details["unrealized_pnl"] == pytest.approx(5.0)

    def test_portfolio_loader_reads_only_current_money_risk_projection(self, monkeypatch):
        observed = {}

        def _loader(conn, **kwargs):
            observed["conn"] = conn
            observed.update(kwargs)
            return {"status": "ok", "table": "position_current", "positions": []}

        sentinel = object()
        monkeypatch.setattr(riskguard_module, "query_portfolio_loader_view", _loader)

        portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(sentinel)

        assert observed == {"conn": sentinel, "runtime_exposure_only": True}
        assert portfolio.positions == []
        assert truth["source"] == "position_current"

    def test_portfolio_loader_fill_authority_preserved_into_riskguard_position(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        conn = get_connection(zeus_db)
        from src.state.db import init_schema

        init_schema(conn)
        _insert_position_current(
            conn,
            position_id="db-pos-fill",
            strategy_key="center_buy",
            size_usd=25.0,
            shares=12.0,
            cost_basis_usd=25.0,
            last_monitor_market_price=2.5,
            temperature_metric="low",
            token_id="yes-low-token",
            no_token_id="no-low-token",
            condition_id="condition-low",
        )
        _insert_execution_fact(
            conn,
            intent_id="db-pos-fill",
            strategy_key="center_buy",
            terminal_exec_status="filled",
            posted_at="2026-04-04T12:00:00+00:00",
            filled_at="2026-04-04T12:00:03+00:00",
            fill_price=2.0,
            shares=10.0,
            venue_status="filled",
        )
        conn.commit()

        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(
                bankroll=211.37,
                positions=[
                    Position(
                        trade_id="metadata-pos",
                        market_id="m-test",
                        city="NYC",
                        cluster="NYC",
                        target_date="2026-04-01",
                        bin_label="39-40°F",
                        direction="buy_yes",
                    )
                ],
            ),
        )

        portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(conn)
        pos = portfolio.positions[0]

        assert truth["source"] == "position_current"
        assert truth["loader_status"] == "ok"
        assert truth["consistency_lock"] == "pass"
        assert pos.temperature_metric == "low"
        assert pos.token_id == "yes-low-token"
        assert pos.no_token_id == "no-low-token"
        assert pos.condition_id == "condition-low"
        assert pos.entry_economics_authority == ENTRY_ECONOMICS_AVG_FILL_PRICE
        assert pos.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL
        assert pos.entry_fill_verified is True
        assert pos.has_fill_economics_authority is True
        assert pos.entry_price_avg_fill == pytest.approx(2.0)
        assert pos.shares_filled == pytest.approx(10.0)
        assert pos.filled_cost_basis_usd == pytest.approx(20.0)
        assert pos.effective_shares == pytest.approx(10.0)
        assert pos.effective_cost_basis_usd == pytest.approx(20.0)
        assert pos.unrealized_pnl == pytest.approx(5.0)
        assert total_exposure_usd(portfolio) == pytest.approx(20.0)

    def test_portfolio_loader_missing_monitor_evidence_stays_non_authoritative(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        conn = get_connection(zeus_db)
        from src.state.db import init_schema

        init_schema(conn)
        _insert_position_current(
            conn,
            position_id="db-pos-missing-monitor",
            strategy_key="center_buy",
            size_usd=25.0,
            shares=12.0,
            cost_basis_usd=25.0,
            last_monitor_market_price=2.5,
        )
        conn.commit()

        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(
                bankroll=211.37,
                positions=[
                    Position(
                        trade_id="metadata-pos",
                        market_id="m-test",
                        city="NYC",
                        cluster="NYC",
                        target_date="2026-04-01",
                        bin_label="39-40°F",
                        direction="buy_yes",
                    )
                ],
            ),
        )

        portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(conn)
        pos = portfolio.positions[0]

        assert truth["source"] == "position_current"
        assert pos.last_monitor_prob is None
        assert pos.last_monitor_edge is None
        assert pos.last_monitor_prob != 0.0
        assert pos.last_monitor_edge != 0.0
        assert pos.last_monitor_market_price == pytest.approx(2.5)

    def test_tick_does_not_use_metadata_recent_exits_without_authoritative_settlements(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        from src.runtime import bankroll_provider as _bp
        monkeypatch.setattr(
            _bp,
            "current",
            lambda **_kw: _bp.BankrollOfRecord(
                value_usd=211.37,
                fetched_at="2026-04-01T00:00:00+00:00",
                source="polymarket_wallet",
                authority="canonical",
                staleness_seconds=0.0,
                cached=False,
            ),
        )
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(
                bankroll=211.37,
                recent_exits=[
                    {
                        "city": "NYC",
                        "bin_label": "legacy",
                        "target_date": "2026-04-01",
                        "direction": "buy_yes",
                        "exit_reason": "SETTLEMENT",
                        "exited_at": "2026-04-03T12:00:00+00:00",
                        "pnl": 99.0,
                    }
                ],
            ),
        )
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: [])
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["realized_truth_source"] == "authoritative_settlement_rows"
        assert details["realized_degraded"] is False
        assert details["realized_pnl"] == pytest.approx(0.0)

    def test_tick_marks_missing_settlement_authority_surface_degraded(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        conn = get_connection(zeus_db)
        conn.execute("DROP TABLE position_events")
        conn.commit()
        conn.close()

        from src.runtime import bankroll_provider as _bp
        monkeypatch.setattr(
            _bp,
            "current",
            lambda **_kw: _bp.BankrollOfRecord(
                value_usd=211.37,
                fetched_at="2026-04-01T00:00:00+00:00",
                source="polymarket_wallet",
                authority="canonical",
                staleness_seconds=0.0,
                cached=False,
            ),
        )
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["strategy_health_refresh_status"] == "refreshed_empty_degraded"
        assert details["strategy_health_settlement_authority_missing_tables"] == ["position_events"]
        assert details["realized_truth_source"] == "authoritative_settlement_rows"
        assert details["realized_degraded"] is True

    def test_portfolio_loader_fill_authority_requires_source_time_provenance(self, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        conn = get_connection(zeus_db)
        from src.state.db import init_schema, query_portfolio_loader_view

        init_schema(conn)
        _insert_position_current(
            conn,
            position_id="db-pos-fill",
            strategy_key="center_buy",
            size_usd=25.0,
            shares=12.0,
            cost_basis_usd=25.0,
            last_monitor_market_price=2.5,
        )
        _insert_execution_fact(
            conn,
            intent_id="db-pos-fill",
            strategy_key="center_buy",
            terminal_exec_status="filled",
            posted_at="2026-04-04T12:00:00+00:00",
            filled_at="2026-04-04T12:00:03+00:00",
            fill_price=2.0,
            shares=10.0,
            venue_status="filled",
        )
        conn.commit()
        loader_row = dict(query_portfolio_loader_view(conn)["positions"][0])
        loader_row["execution_fact_filled_at"] = ""

        with pytest.raises(ValueError, match="execution_fact_filled_at"):
            riskguard_module._portfolio_position_from_loader_row(loader_row)

    def test_portfolio_loader_accepts_chain_corrected_fill_truth(self, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        conn = get_connection(zeus_db)
        from src.state.db import init_schema, query_portfolio_loader_view

        init_schema(conn)
        _insert_position_current(
            conn,
            position_id="db-pos-chain-corrected",
            strategy_key="center_buy",
            size_usd=18.83,
            shares=29.15,
            cost_basis_usd=18.83,
            last_monitor_market_price=0.64,
        )
        conn.execute(
            """
            UPDATE position_current
               SET fill_authority = 'venue_confirmed_full',
                   chain_state = 'synced',
                   chain_shares = 29.15,
                   chain_avg_price = 0.645969,
                   chain_cost_basis_usd = 18.83,
                   chain_seen_at = '2026-07-17T06:00:00+00:00'
             WHERE position_id = 'db-pos-chain-corrected'
            """
        )
        _insert_execution_fact(
            conn,
            intent_id="db-pos-chain-corrected",
            strategy_key="center_buy",
            terminal_exec_status="filled",
            posted_at="2026-07-17T05:59:00+00:00",
            filled_at="2026-07-17T05:59:03+00:00",
            fill_price=0.64,
            shares=23.55,
            venue_status="filled",
        )
        conn.commit()

        loader_row = query_portfolio_loader_view(
            conn,
            runtime_exposure_only=True,
        )["positions"][0]
        portfolio, metadata = riskguard_module._load_riskguard_portfolio_truth(conn)

        assert loader_row["entry_economics_source"] == "position_current_chain_corrected"
        assert loader_row["execution_fact_intent_id"] == "db-pos-chain-corrected"
        assert metadata["consistency_lock"] == "pass"
        assert metadata["unloadable_count"] == 0
        assert len(portfolio.positions) == 1
        assert portfolio.positions[0].effective_shares == pytest.approx(29.15)
        assert portfolio.positions[0].effective_cost_basis_usd == pytest.approx(18.83)

    def test_portfolio_loader_accepts_balance_only_chain_observed_economics(self):
        """Current chain exposure is not a claim of verified fill history.

        BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md,
        post-T5-migration): this used to use
        state='quarantined'/chain_state='entry_authority_quarantined' — both
        retired, DB CHECK no longer admits them, and Position construction
        now raises instead of remapping. Per REPLACEMENT PHASE LAW a
        disputed-entry position keeps its TRUE phase directly, so this uses
        state='holding'/chain_state='synced' — same real assertions about
        fill/chain-observed authority, unrelated to the phase label.
        """
        loader_row = {
            "trade_id": "balance-only-chain-row",
            "market_id": "condition-1",
            "city": "Tokyo",
            "cluster": "Asia",
            "target_date": "2026-07-13",
            "bin_label": "31C",
            "direction": "buy_no",
            "unit": "C",
            "temperature_metric": "high",
            "env": "live",
            "size_usd": 2.432,
            "shares": 3.8,
            "cost_basis_usd": 2.432,
            "entry_price": 0.64,
            "entry_economics_authority": "corrected_executable_cost_basis",
            "fill_authority": "venue_position_observed",
            "entry_economics_source": "position_current_chain_observed",
            "execution_fact_intent_id": "",
            "execution_fact_filled_at": "",
            "chain_state": "synced",
            "chain_shares": 3.8,
            "chain_avg_price": 0.64,
            "chain_cost_basis_usd": 2.432,
            "state": "holding",
        }

        position = riskguard_module._portfolio_position_from_loader_row(loader_row)

        assert position.fill_authority == "venue_position_observed"
        assert position.has_chain_observed_authority is True
        assert position.has_fill_economics_authority is False
        assert position.effective_shares == pytest.approx(3.8)
        assert position.effective_cost_basis_usd == pytest.approx(2.432)

    def test_loader_excludes_unloadable_row_instead_of_failing_whole_tick(
        self, monkeypatch, tmp_path
    ):
        """One un-loadable canonical row must NOT take down the whole RiskGuard loader.

        Regression guard (2026-06-16 incident): a single fill-grade row missing
        execution_fact provenance (a dual-id recovered-fill duplicate) caused the loader
        to RAISE -> RiskGuard tick failed -> RiskGuard went STALE -> trader fail-closed
        RED -> ALL trading blocked. The loader must EXCLUDE the bad row (exclude +
        log + count) and CONTINUE loading the valid rows. RED-on-revert: restoring the
        `raise RuntimeError(...)` makes `_load_riskguard_portfolio_truth` raise here.

        T3 fix (2026-07-11): a row exclusion is real missing exposure, so the verdict
        must NOT read "pass" — it degrades to consistency_lock="degraded", which the
        tick() caller routes to RiskLevel.DATA_DEGRADED (see
        `_portfolio_consistency_level`, src/riskguard/riskguard.py).
        """
        zeus_db = tmp_path / "zeus.db"
        conn = get_connection(zeus_db)

        valid_row = {
            "trade_id": "valid-good-1", "market_id": "m-good", "city": "NYC",
            "target_date": "2026-06-17", "direction": "buy_yes", "unit": "F",
            "env": "live", "size_usd": 10.0, "shares": 4.0, "cost_basis_usd": 10.0,
            "entry_price": 2.5, "entry_economics_authority": "legacy_unknown",
            "fill_authority": "none",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
        }
        # Fill-grade (venue_confirmed_full) but NO execution_fact provenance -> raises in
        # _portfolio_position_from_loader_row exactly like the live incident row.
        bad_row = {
            "trade_id": "bad-dup-1", "market_id": "m-bad", "city": "Houston",
            "target_date": "2026-06-17", "direction": "buy_no", "unit": "F",
            "env": "live", "size_usd": 3.24, "shares": 5.07, "cost_basis_usd": 3.24,
            "entry_price": 0.64,
            "entry_economics_authority": "legacy_unknown",
            "fill_authority": "venue_confirmed_full",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
        }

        monkeypatch.setattr(
            riskguard_module,
            "query_portfolio_loader_view",
            lambda _conn, **_kw: {"status": "ok", "table": "position_current",
                                  "positions": [valid_row, bad_row]},
        )
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(
                bankroll=100.0,
                positions=[
                    Position(trade_id="valid-good-1", market_id="m-good", city="NYC",
                             cluster="NYC", target_date="2026-06-17", bin_label="b",
                             direction="buy_yes"),
                    Position(trade_id="bad-dup-1", market_id="m-bad", city="Houston",
                             cluster="HOU", target_date="2026-06-17", bin_label="b",
                             direction="buy_no"),
                ],
            ),
        )

        # MUST NOT raise (pre-fix this raised RuntimeError("RiskGuard DB loader fault")).
        portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(conn)

        assert truth["unloadable_count"] == 1
        assert truth["unloadable_rows"][0]["trade_id"] == "bad-dup-1"
        # No token_id on the excluded row -> duplication cannot be proven ->
        # "excluded_unaccounted", the conservative default.
        assert truth["unloadable_rows"][0]["classification"] == "excluded_unaccounted"
        assert [p.trade_id for p in portfolio.positions] == ["valid-good-1"]
        # A row exclusion is a KNOWN, reconciled exclusion (1 loaded + 1 unloadable ==
        # 2 metadata rows), but it is STILL missing real exposure from the risk view ->
        # consistency_lock must degrade, never report 'pass' (the verdict lie this
        # packet fixes).
        assert truth["consistency_lock"] == "degraded"
        # And the caller-side risk lane wiring: degraded routes to DATA_DEGRADED
        # (YELLOW-equivalent: no new entries, monitor/exit continue), never RED.
        assert (
            riskguard_module._portfolio_consistency_level(truth["consistency_lock"])
            == RiskLevel.DATA_DEGRADED
        )

    def test_loader_excluded_duplicate_row_does_not_degrade_consistency(
        self, monkeypatch, tmp_path
    ):
        """Critic amendment M-2 (2026-07-11): a blanket "any exclusion degrades"
        over-blocks the documented benign B052 trigger — a dual-id recovered-fill
        DUPLICATE whose on-chain exposure is already accounted for via the loaded
        canonical position (see the B052 comment at riskguard.py). When the excluded
        row's token_id matches a LOADED position's token_id with >= shares, it is
        PROVEN accounted for ("excluded_duplicate") and must NOT force a false
        YELLOW halt — consistency_lock stays "pass" (still counted + logged).
        """
        conn = get_connection(tmp_path / "zeus.db")

        # Canonical, successfully-loaded position already covering the on-chain
        # exposure for token "tok-shared-1" (10 shares).
        valid_row = {
            "trade_id": "valid-good-1", "market_id": "m-good", "city": "NYC",
            "target_date": "2026-06-17", "direction": "buy_yes", "unit": "F",
            "env": "live", "size_usd": 10.0, "shares": 10.0, "cost_basis_usd": 10.0,
            "entry_price": 2.5, "entry_economics_authority": "legacy_unknown",
            "fill_authority": "none",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
            "token_id": "tok-shared-1",
        }
        # Dual-id recovered-fill DUPLICATE of the SAME on-chain token, fewer shares,
        # fill-grade but missing execution_fact provenance -> raises ValueError exactly
        # like the live B052 incident row.
        duplicate_row = {
            "trade_id": "bad-dup-1", "market_id": "m-bad", "city": "Houston",
            "target_date": "2026-06-17", "direction": "buy_no", "unit": "F",
            "env": "live", "size_usd": 3.24, "shares": 5.07, "cost_basis_usd": 3.24,
            "entry_price": 0.64,
            "entry_economics_authority": "legacy_unknown",
            "fill_authority": "venue_confirmed_full",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
            "token_id": "tok-shared-1",
        }

        monkeypatch.setattr(
            riskguard_module,
            "query_portfolio_loader_view",
            lambda _conn, **_kw: {"status": "ok", "table": "position_current",
                                  "positions": [valid_row, duplicate_row]},
        )

        portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(conn)

        assert truth["unloadable_count"] == 1
        assert truth["unloadable_rows"][0]["trade_id"] == "bad-dup-1"
        assert truth["unloadable_rows"][0]["classification"] == "excluded_duplicate"
        assert truth["excluded_duplicate_count"] == 1
        assert [p.trade_id for p in portfolio.positions] == ["valid-good-1"]
        # Proven-accounted exclusion is pass-eligible: no false YELLOW halt.
        assert truth["consistency_lock"] == "pass"
        assert (
            riskguard_module._portfolio_consistency_level(truth["consistency_lock"])
            == RiskLevel.GREEN
        )

    def test_loader_excluded_duplicate_with_insufficient_loaded_shares_still_degrades(
        self, monkeypatch, tmp_path
    ):
        """The duplicate-proof requires the loaded position to cover AT LEAST as many
        shares as the excluded row claims. A loaded match that covers FEWER shares
        cannot prove the excluded row adds no unaccounted exposure, so it must stay
        "excluded_unaccounted" / degraded — proof of safety is required here, not
        absence of proof of danger.
        """
        conn = get_connection(tmp_path / "zeus.db")

        valid_row = {
            "trade_id": "valid-good-1", "market_id": "m-good", "city": "NYC",
            "target_date": "2026-06-17", "direction": "buy_yes", "unit": "F",
            "env": "live", "size_usd": 10.0, "shares": 1.0, "cost_basis_usd": 10.0,
            "entry_price": 2.5, "entry_economics_authority": "legacy_unknown",
            "fill_authority": "none",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
            "token_id": "tok-shared-1",
        }
        duplicate_row = {
            "trade_id": "bad-dup-1", "market_id": "m-bad", "city": "Houston",
            "target_date": "2026-06-17", "direction": "buy_no", "unit": "F",
            "env": "live", "size_usd": 3.24, "shares": 5.07, "cost_basis_usd": 3.24,
            "entry_price": 0.64,
            "entry_economics_authority": "legacy_unknown",
            "fill_authority": "venue_confirmed_full",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
            "token_id": "tok-shared-1",
        }

        monkeypatch.setattr(
            riskguard_module,
            "query_portfolio_loader_view",
            lambda _conn, **_kw: {"status": "ok", "table": "position_current",
                                  "positions": [valid_row, duplicate_row]},
        )

        _portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(conn)

        assert truth["unloadable_rows"][0]["classification"] == "excluded_unaccounted"
        assert truth["excluded_duplicate_count"] == 0
        assert truth["consistency_lock"] == "degraded"

    def test_loader_zero_exclusions_reports_pass(self, monkeypatch, tmp_path):
        """No unloadable rows -> consistency_lock stays 'pass' (unchanged behavior)."""
        conn = get_connection(tmp_path / "zeus.db")

        valid_row = {
            "trade_id": "valid-good-1", "market_id": "m-good", "city": "NYC",
            "target_date": "2026-06-17", "direction": "buy_yes", "unit": "F",
            "env": "live", "size_usd": 10.0, "shares": 4.0, "cost_basis_usd": 10.0,
            "entry_price": 2.5, "entry_economics_authority": "legacy_unknown",
            "fill_authority": "none",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
        }

        monkeypatch.setattr(
            riskguard_module,
            "query_portfolio_loader_view",
            lambda _conn, **_kw: {"status": "ok", "table": "position_current",
                                  "positions": [valid_row]},
        )
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(
                bankroll=100.0,
                positions=[
                    Position(trade_id="valid-good-1", market_id="m-good", city="NYC",
                             cluster="NYC", target_date="2026-06-17", bin_label="b",
                             direction="buy_yes"),
                ],
            ),
        )

        _portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(conn)

        assert truth["unloadable_count"] == 0
        assert truth["consistency_lock"] == "pass"
        assert (
            riskguard_module._portfolio_consistency_level(truth["consistency_lock"])
            == RiskLevel.GREEN
        )

    def test_loader_exclusion_logs_one_summary_for_multiple_bad_rows(
        self, monkeypatch, tmp_path, caplog
    ):
        conn = get_connection(tmp_path / "zeus.db")

        valid_row = {
            "trade_id": "valid-good-1", "market_id": "m-good", "city": "NYC",
            "target_date": "2026-06-17", "direction": "buy_yes", "unit": "F",
            "env": "live", "size_usd": 10.0, "shares": 4.0, "cost_basis_usd": 10.0,
            "entry_price": 2.5, "entry_economics_authority": "legacy_unknown",
            "fill_authority": "none",
            "entry_economics_source": "position_current_projection",
            "execution_fact_intent_id": "", "execution_fact_filled_at": "",
            "state": "entered", "chain_state": "unknown",
        }
        bad_row_1 = {
            **valid_row,
            "trade_id": "bad-dup-1",
            "market_id": "m-bad-1",
            "fill_authority": "venue_confirmed_full",
        }
        bad_row_2 = {
            **valid_row,
            "trade_id": "bad-dup-2",
            "market_id": "m-bad-2",
            "fill_authority": "venue_confirmed_full",
        }

        monkeypatch.setattr(
            riskguard_module,
            "query_portfolio_loader_view",
            lambda _conn, **_kw: {
                "status": "ok",
                "table": "position_current",
                "positions": [valid_row, bad_row_1, bad_row_2],
            },
        )
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(
                bankroll=100.0,
                positions=[
                    Position(trade_id="valid-good-1", market_id="m-good", city="NYC",
                             cluster="NYC", target_date="2026-06-17", bin_label="b",
                             direction="buy_yes"),
                    Position(trade_id="bad-dup-1", market_id="m-bad-1", city="Houston",
                             cluster="HOU", target_date="2026-06-17", bin_label="b",
                             direction="buy_no"),
                    Position(trade_id="bad-dup-2", market_id="m-bad-2", city="Austin",
                             cluster="AUS", target_date="2026-06-17", bin_label="b",
                             direction="buy_no"),
                ],
            ),
        )
        caplog.set_level(logging.ERROR, logger=riskguard_module.__name__)

        _portfolio, truth = riskguard_module._load_riskguard_portfolio_truth(conn)

        exclusion_logs = [
            record
            for record in caplog.records
            if "RiskGuard excluded" in record.getMessage()
        ]
        assert truth["unloadable_count"] == 2
        assert len(exclusion_logs) == 1
        assert "excluded 2 un-loadable" in exclusion_logs[0].getMessage()
        assert truth["consistency_lock"] == "degraded"

    def test_tick_records_explicit_portfolio_fallback_when_projection_unavailable(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(
                bankroll=211.37,
                daily_baseline_total=149.0,
                weekly_baseline_total=148.0,
            ),
        )
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
        )

        with pytest.raises(RuntimeError, match="riskguard requires canonical truth source.*json_fallback"):
            riskguard_module.tick()

    def test_get_current_level_fails_closed_when_risk_state_has_no_rows(self, monkeypatch, tmp_path):
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            return get_connection(risk_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)

        level = riskguard_module.get_current_level()

        assert level == RiskLevel.RED

    def test_get_current_level_reads_latest_append_without_schema_work(
        self,
        monkeypatch,
        tmp_path,
    ):
        risk_db = tmp_path / "risk_state.db"
        conn = get_connection(risk_db)
        riskguard_module.init_risk_db(conn)
        future = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        _insert_risk_state_row(conn, checked_at=future, level=RiskLevel.RED.value)
        _insert_risk_state_row(conn, checked_at=now, level=RiskLevel.GREEN.value)
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            riskguard_module,
            "get_connection",
            lambda path=None, **_kwargs: get_connection(risk_db),
        )
        monkeypatch.setattr(
            riskguard_module,
            "init_risk_db",
            lambda _conn: (_ for _ in ()).throw(
                AssertionError("current-level reads must not run schema initialization")
            ),
        )

        assert riskguard_module.get_current_level() == RiskLevel.GREEN

    def test_tick_start_attestation_preserves_fresh_full_level_during_long_metrics_pass(
        self,
        monkeypatch,
        tmp_path,
    ):
        risk_db = tmp_path / "risk_state.db"
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat(),
            level=RiskLevel.YELLOW.value,
            strategy_signal_level="YELLOW",
            recommended_controls=["review_strategy_gates"],
            recommended_strategy_gates=["forecast_qkernel_entry"],
        )
        risk_conn.commit()
        risk_conn.close()

        def _fake_get_connection(path=None, **_kwargs):
            assert path == riskguard_module.RISK_DB_PATH
            return get_connection(risk_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)

        riskguard_module._persist_tick_in_progress_attestation()

        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert row["level"] == RiskLevel.YELLOW.value
        assert details["status"] == "metrics_in_progress_previous_risk_level_preserved"
        assert details["riskguard_degraded_reason"] == "metrics_refresh_in_progress"
        assert details["previous_full_risk_level"] == RiskLevel.YELLOW.value
        assert details["execution_quality_level"] == "GREEN"
        assert details["strategy_signal_level"] == "YELLOW"
        assert details["recommended_controls"] == ["review_strategy_gates"]
        assert details["recommended_strategy_gates"] == ["forecast_qkernel_entry"]
        assert riskguard_module.get_current_level() == RiskLevel.YELLOW

        # The in-progress row is not itself a full metrics row and cannot extend
        # the full-risk freshness chain indefinitely.
        latest_full = riskguard_module._latest_fresh_full_risk_row(
            get_connection(risk_db),
            now=datetime.now(timezone.utc),
        )
        assert latest_full is not None
        assert json.loads(latest_full["details_json"]).get("riskguard_degraded_reason") is None

    def test_tick_start_attestation_does_not_extend_stale_full_level(
        self,
        monkeypatch,
        tmp_path,
    ):
        risk_db = tmp_path / "risk_state.db"
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
            level=RiskLevel.GREEN.value,
        )
        risk_conn.commit()
        risk_conn.close()

        def _fake_get_connection(path=None, **_kwargs):
            assert path == riskguard_module.RISK_DB_PATH
            return get_connection(risk_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)

        riskguard_module._persist_tick_in_progress_attestation()

        rows = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC"
        ).fetchall()

        assert len(rows) == 1
        assert riskguard_module.get_current_level() == RiskLevel.RED

    def test_bankroll_unavailable_row_keeps_degraded_details_contract(
        self,
        monkeypatch,
        tmp_path,
    ):
        risk_db = tmp_path / "risk_state.db"
        trade_conn = sqlite3.connect(":memory:")
        trade_conn.row_factory = sqlite3.Row

        def _fake_get_connection(path=None, **_kwargs):
            assert path == riskguard_module.RISK_DB_PATH
            return get_connection(risk_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "_bankroll_of_record_for_riskguard",
            lambda: None,
        )
        monkeypatch.setattr(
            riskguard_module,
            "_get_runtime_trade_connection",
            lambda: trade_conn,
        )
        monkeypatch.setattr(
            riskguard_module,
            "_load_riskguard_portfolio_truth",
            lambda conn: (PortfolioState(bankroll=0.0), {}),
        )

        level = riskguard_module._tick_once()

        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])
        assert level == RiskLevel.DATA_DEGRADED
        assert row["level"] == RiskLevel.DATA_DEGRADED.value
        assert details["status"] == "bankroll_provider_unavailable"
        assert details["riskguard_degraded_reason"] == "bankroll_provider_unavailable"
        assert details["execution_quality_level"] == "DATA_DEGRADED"
        assert details["strategy_signal_level"] == "DATA_DEGRADED"
        assert details["recommended_controls"] == []
        assert details["recommended_strategy_gates"] == []

    def test_writer_contract_keys_match_health_reader_contract(self):
        from scripts import healthcheck

        assert riskguard_module._RISK_DETAILS_CONTRACT_KEYS == (
            healthcheck.RISK_DETAILS_REQUIRED_KEYS
        )

    def test_bankroll_unavailable_with_fresh_full_degrades_levels_and_keeps_recommendations(
        self,
        monkeypatch,
        tmp_path,
    ):
        risk_db = tmp_path / "risk_state.db"
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            level=RiskLevel.GREEN.value,
            recommended_controls=["review_strategy_gates"],
            recommended_strategy_gates=["forecast_qkernel_entry"],
        )
        risk_conn.commit()
        risk_conn.close()
        trade_conn = sqlite3.connect(":memory:")
        trade_conn.row_factory = sqlite3.Row

        def _fake_get_connection(path=None, **_kwargs):
            assert path == riskguard_module.RISK_DB_PATH
            return get_connection(risk_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "_bankroll_of_record_for_riskguard",
            lambda: None,
        )
        monkeypatch.setattr(
            riskguard_module,
            "_get_runtime_trade_connection",
            lambda: trade_conn,
        )
        monkeypatch.setattr(
            riskguard_module,
            "_load_riskguard_portfolio_truth",
            lambda conn: (PortfolioState(bankroll=0.0), {}),
        )

        level = riskguard_module._tick_once()

        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])
        assert level == RiskLevel.DATA_DEGRADED
        assert details["execution_quality_level"] == "DATA_DEGRADED"
        assert details["strategy_signal_level"] == "DATA_DEGRADED"
        assert details["recommended_controls"] == ["review_strategy_gates"]
        assert details["recommended_strategy_gates"] == ["forecast_qkernel_entry"]
        assert details["previous_full_risk_level"] == "GREEN"
        assert details["previous_full_risk_checked_at"]

    def test_tick_records_canonical_settlement_source(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [
                {
                    "p_posterior": 0.7,
                    "outcome": 1,
                    "source": "position_events",
                    "metric_ready": True,
                    "learning_snapshot_ready": True,
                    "probability_identity_ready": True,
                    "entry_q_version": "test-q-version",
                }
            ],
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["settlement_storage_source"] == "position_events"
        assert details["settlement_row_storage_sources"] == ["position_events"]
        assert details["settlement_sample_size"] == 1
        assert details["strategy_settlement_summary"]["unclassified"]["count"] == 1

    def test_tick_records_legacy_settlement_fallback_source(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [
                {
                    "p_posterior": 0.4,
                    "outcome": 0,
                    "source": "decision_log",
                    "metric_ready": True,
                    "learning_snapshot_ready": True,
                    "probability_identity_ready": True,
                    "entry_q_version": "test-q-version",
                }
            ],
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["settlement_storage_source"] == "decision_log"
        assert details["settlement_row_storage_sources"] == ["decision_log"]
        assert details["settlement_sample_size"] == 1

    def test_tick_records_authoritative_strategy_breakdown(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [
                {"p_posterior": 0.7, "outcome": 1, "pnl": 5.0, "strategy": "center_buy", "source": "position_events", "metric_ready": True},
                {"p_posterior": 0.4, "outcome": 0, "pnl": -2.0, "strategy": "center_buy", "source": "position_events", "metric_ready": True},
                {"p_posterior": 0.8, "outcome": 1, "pnl": 4.0, "strategy": "opening_inertia", "source": "position_events", "metric_ready": True},
            ],
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["strategy_settlement_summary"]["center_buy"]["count"] == 2
        assert details["strategy_settlement_summary"]["center_buy"]["pnl"] == pytest.approx(3.0)
        assert details["strategy_settlement_summary"]["center_buy"]["trade_profitability_rate"] == pytest.approx(0.5)
        assert details["strategy_settlement_summary"]["opening_inertia"]["count"] == 1

    def test_tick_records_entry_execution_summary(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        conn = get_connection(zeus_db)
        from src.state.db import init_schema
        init_schema(conn)
        # Insert canonical position_events directly (P9: log_position_event deleted)
        import json as _json
        conn.execute("""
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, env, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, ("exec-1:intent:1", "exec-1", 1, 1, "POSITION_OPEN_INTENT",
               _recent_iso(minutes=4), "center_buy", "test", "live", '{}'))
        conn.execute("""
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, env, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, ("exec-1:filled:2", "exec-1", 1, 2, "ENTRY_ORDER_FILLED",
               _recent_iso(minutes=3), "center_buy", "test", "live", '{}'))
        conn.execute("""
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, env, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, ("exec-2:rejected:1", "exec-2", 1, 1, "ENTRY_ORDER_REJECTED",
               _recent_iso(minutes=2), "opening_inertia", "test", "live", '{}'))
        conn.execute("""
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, env, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, ("exec-3:voided:1", "exec-3", 1, 1, "ENTRY_ORDER_VOIDED",
               _recent_iso(minutes=1), "opening_inertia", "test", "live", '{}'))
        conn.commit()
        conn.close()

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        overall = details["entry_execution_summary"]["overall"]
        assert overall["attempted"] == 1
        assert overall["filled"] == 1
        assert overall["rejected"] == 1
        assert overall["voided"] == 1
        assert overall["terminal_observed"] == 3
        assert overall["fill_rate"] == pytest.approx(1 / 3, rel=1e-3)
        assert details["entry_execution_summary"]["by_strategy"]["center_buy"]["filled"] == 1
        assert details["entry_execution_summary"]["by_strategy"]["opening_inertia"]["rejected"] == 1
        assert details["entry_execution_summary"]["by_strategy"]["opening_inertia"]["voided"] == 1
        assert details["entry_execution_summary"]["by_strategy"]["opening_inertia"]["fill_rate"] == 0.0

    def test_tick_records_strategy_tracker_diagnostics(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        # Post-K1: record_trade / set_accounting_metadata are no-ops; tracker.summary()
        # reads from position_events via query_authoritative_settlement_rows. Stub
        # summary() to return fixed data so this test stays focused on riskguard's
        # serialization of the tracker diagnostics, not on the tracker's own projection.
        tracker = strategy_tracker_module.StrategyTracker()
        tracker.summary = lambda conn=None: {
            "center_buy": {"trades": 2, "pnl": 2.0},
            "shoulder_sell": {"trades": 0, "pnl": 0.0},
            "opening_inertia": {"trades": 0, "pnl": 0.0},
            "settlement_capture": {"trades": 0, "pnl": 0.0},
        }

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["strategy_tracker_summary"]["center_buy"]["trades"] == 2
        assert details["strategy_tracker_summary"]["center_buy"]["pnl"] == pytest.approx(2.0)
        # Post-K1: set_accounting_metadata is a no-op; current_regime_started_at is always ""
        assert details["strategy_tracker_accounting"]["current_regime_started_at"] == ""
        assert details["recommended_strategy_gates"] == []


class TestRiskGuardTrailingLossSemantics:
    def test_tick_uses_trailing_24h_loss_not_all_time_loss(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        zeus_conn = get_connection(zeus_db)
        init_schema(zeus_conn)
        zeus_conn.close()
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        reference_checked_at = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        reference_id = _insert_risk_state_row(
            risk_conn,
            checked_at=reference_checked_at,
            total_pnl=-13.26,
        )
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(days=7, minutes=30)).isoformat(),
            total_pnl=-13.26,
        )
        risk_conn.commit()
        risk_conn.close()

        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=-13.26,
            unrealized_pnl=0.0,
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "no_settlements_in_window"
        assert details["daily_loss_source"] == "realized_settlement_window:authoritative_settlement_rows"
        assert details["daily_loss_reference"]["settlement_count"] == 0
        assert details["daily_loss_reference"]["realized_pnl_window"] == pytest.approx(0.0)

    def test_tick_uses_trailing_7d_loss_when_reference_exists(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        zeus_conn = get_connection(zeus_db)
        init_schema(zeus_conn)
        zeus_conn.close()
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
            total_pnl=-10.0,
        )
        weekly_reference_checked_at = (datetime.now(timezone.utc) - timedelta(days=7, minutes=30)).isoformat()
        weekly_reference_id = _insert_risk_state_row(
            risk_conn,
            checked_at=weekly_reference_checked_at,
            total_pnl=-5.0,
        )
        risk_conn.commit()
        risk_conn.close()

        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=-10.0,
            unrealized_pnl=0.0,
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["weekly_loss"] == pytest.approx(0.0)
        assert details["weekly_loss_status"] == "no_settlements_in_window"
        assert details["weekly_loss_source"] == "realized_settlement_window:authoritative_settlement_rows"
        assert details["weekly_loss_reference"]["settlement_count"] == 0
        assert details["weekly_loss_reference"]["realized_pnl_window"] == pytest.approx(0.0)

    def test_tick_marks_insufficient_history_without_false_trigger(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        zeus_conn = get_connection(zeus_db)
        init_schema(zeus_conn)
        zeus_conn.close()
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            total_pnl=-5.0,
        )
        risk_conn.commit()
        risk_conn.close()

        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=-5.0,
            unrealized_pnl=0.0,
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        # Realized settlement loss is settlement-window based, not risk_state
        # history based. A row without a settled exit cannot manufacture loss.
        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "no_settlements_in_window"
        assert details["daily_loss_level"] == RiskLevel.GREEN.value
        assert details["daily_loss_source"] == "realized_settlement_window:authoritative_settlement_rows"
        assert details["daily_loss_reference"]["settlement_count"] == 0

    def test_tick_marks_inconsistent_history_without_false_trigger(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        zeus_conn = get_connection(zeus_db)
        init_schema(zeus_conn)
        zeus_conn.close()
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
            total_pnl=-5.0,
            effective_bankroll=149.0,
        )
        risk_conn.commit()
        risk_conn.close()

        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=-5.0,
            unrealized_pnl=0.0,
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "no_settlements_in_window"
        assert details["daily_loss_level"] == RiskLevel.GREEN.value
        assert details["daily_loss_reference"]["settlement_count"] == 0

    def test_tick_marks_no_reference_row_when_risk_history_is_empty(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        zeus_conn = get_connection(zeus_db)
        init_schema(zeus_conn)
        zeus_conn.close()
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        risk_conn.close()

        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=-5.0,
            unrealized_pnl=0.0,
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        # Empty risk_state history is irrelevant to realized settlement loss.
        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "no_settlements_in_window"
        assert details["daily_loss_level"] == RiskLevel.GREEN.value
        assert details["daily_loss_source"] == "realized_settlement_window:authoritative_settlement_rows"
        assert details["daily_loss_reference"]["settlement_count"] == 0

    def test_tick_marks_inconsistent_when_only_older_out_of_window_row_is_trustworthy(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        zeus_conn = get_connection(zeus_db)
        init_schema(zeus_conn)
        zeus_conn.close()
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            total_pnl=-5.0,
        )
        _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
            total_pnl=-6.0,
            effective_bankroll=149.0,
        )
        stale_reference_id = _insert_risk_state_row(
            risk_conn,
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=27)).isoformat(),
            total_pnl=-8.0,
        )
        risk_conn.commit()
        risk_conn.close()

        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=-10.0,
            unrealized_pnl=0.0,
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "no_settlements_in_window"
        assert details["daily_loss_source"] == "realized_settlement_window:authoritative_settlement_rows"
        assert details["daily_loss_reference"]["settlement_count"] == 0

    def test_tick_uses_trustworthy_reference_within_freshness_window(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        zeus_conn = get_connection(zeus_db)
        init_schema(zeus_conn)
        zeus_conn.close()
        risk_conn = get_connection(risk_db)
        riskguard_module.init_risk_db(risk_conn)
        trusted_checked_at = (datetime.now(timezone.utc) - timedelta(hours=24, minutes=30)).isoformat()
        trusted_id = _insert_risk_state_row(
            risk_conn,
            checked_at=trusted_checked_at,
            total_pnl=-8.0,
        )
        risk_conn.commit()
        risk_conn.close()

        _mock_trailing_loss_tick(
            monkeypatch,
            zeus_db=zeus_db,
            risk_db=risk_db,
            realized_pnl=-10.0,
            unrealized_pnl=0.0,
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "no_settlements_in_window"
        assert details["daily_loss_reference"]["settlement_count"] == 0


def _patch_riskguard_bankroll(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.runtime import bankroll_provider as _bp

    monkeypatch.setattr(
        _bp,
        "current",
        lambda **_kw: _bp.BankrollOfRecord(
            value_usd=211.37,
            fetched_at="2026-04-01T00:00:00+00:00",
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        ),
    )


class TestRiskGuardOrangeLocalization:
    """Current-law Brier breaches must alter strategy admission.

    Test data: 45 opening_inertia rows at p=0.58/outcome=0 (per-row squared
    error 0.3364, individually ORANGE) + 5 center_buy rows at p=0.80/outcome=1
    (per-row squared error 0.04, individually GREEN) pool to a portfolio Brier
    of ~0.3068 (ORANGE). Exact strategy attribution may localize that breach
    only after the durable gate is confirmed and the residual portfolio is
    independently GREEN.
    """

    def _orange_rows(self, *, unclassified_count: int = 0) -> list[dict]:
        # RISKGUARD_SETTLEMENT_LIMIT caps the learning-ready sample at 50, so
        # keep the total at 45 (degraded pool, minus any unclassified_count
        # carved out of it) + 5 (clean) == 50 — otherwise trailing rows appended
        # past the limit are silently dropped from the Brier sample.
        classified_degraded = 45 - unclassified_count
        rows = [
            _settlement_row(
                trade_id=f"opening-{i}",
                strategy="opening_inertia",
                p_posterior=0.58,
                outcome=0,
                target_date=_independent_target_date(i),
            )
            for i in range(classified_degraded)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
                target_date=_independent_target_date(45 + i),
            )
            for i in range(5)
        ] + [
            _settlement_row(
                trade_id=f"unclassified-{i}",
                strategy="legacy_unattributed",
                p_posterior=0.58,
                outcome=0,
                target_date=_independent_target_date(50 + i),
            )
            for i in range(unclassified_count)
        ]
        return rows

    def test_orange_localizes_to_green_when_clean_attribution_and_gate_confirmed_and_residual_green(
        self, monkeypatch, tmp_path,
    ):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = self._orange_rows()

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])
        gate_row = get_connection(zeus_db).execute(
            """
            SELECT strategy_key, status
            FROM risk_actions
            WHERE action_id = 'riskguard:gate:opening_inertia'
            """
        ).fetchone()

        assert level == RiskLevel.GREEN
        assert risk_row["level"] == RiskLevel.GREEN.value
        assert details["portfolio_brier_level"] == "ORANGE"
        assert details["brier_level"] == "GREEN"
        assert details["brier_all_strategies_level"] == "ORANGE"
        assert details["brier_active_portfolio_level"] == "GREEN"
        assert details["localized_orange_scope"] is True
        assert details["brier_strategy_localization"]["status"] == "localized_orange_scope"
        assert details["brier_strategy_localization"]["gated_strategies"] == ["opening_inertia"]
        assert details["brier_strategy_localization"]["gate_confirmation"] == {
            "opening_inertia": True
        }
        assert dict(gate_row) == {
            "strategy_key": "opening_inertia",
            "status": "active",
        }

    def test_unlabeled_legacy_rows_do_not_veto_current_law_localization(
        self, monkeypatch, tmp_path
    ):
        """Rows without law identity remain telemetry, never current-law votes."""
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = self._orange_rows(unclassified_count=3)
        for row in rows:
            if row["strategy"] == "legacy_unattributed":
                row["decision_law_id"] = None

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        assert level == RiskLevel.GREEN
        assert risk_row["level"] == RiskLevel.GREEN.value
        assert details["portfolio_brier_level"] == "ORANGE"
        assert details["portfolio_brier_raw_level"] == "ORANGE"
        assert details["brier_all_strategies_level"] == "ORANGE"
        assert details["brier_level"] == "GREEN"
        assert details["brier_active_portfolio_level"] == "GREEN"
        assert details["localized_orange_scope"] is True
        assert details["brier_strategy_localization"]["status"] == "localized_orange_scope"
        assert details["brier_strategy_breakdown"]["unclassified_count"] == 0
        assert details["brier_observed_all_lineage_sample_size"] == 50
        assert details["brier_actuating_sample_size"] == 47

    def test_orange_stays_global_when_durable_gate_write_is_skipped(self, monkeypatch, tmp_path):
        """Condition #2 failure mode A: the write itself reports non-emitted
        (e.g. lock/contention) — ORANGE localization is the SAFETY
        PRECONDITION, unlike YELLOW's lock-tolerant auxiliary bookkeeping."""
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = self._orange_rows()

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)
        monkeypatch.setattr(
            riskguard_module,
            "_sync_riskguard_strategy_gate_actions",
            lambda *a, **k: {"status": "skipped_dependency_lock", "emitted_count": 0, "expired_count": 0},
        )

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        assert level == RiskLevel.YELLOW
        assert risk_row["level"] == RiskLevel.YELLOW.value
        assert details["portfolio_brier_raw_level"] == "ORANGE"
        assert details["brier_level"] == "YELLOW"
        assert details["localized_orange_scope"] is False
        assert (
            details["brier_strategy_localization"]["status"]
            == "durable_strategy_gate_unconfirmed_global_orange"
        )
        assert details["brier_strategy_localization"]["durable_risk_action_status"] == "skipped_dependency_lock"
        assert details["durable_risk_action_emission_status"] == "skipped_dependency_lock"

    def test_orange_stays_global_when_residual_portfolio_is_not_green(self, monkeypatch, tmp_path):
        """Condition #3 failure mode. Note: with clean per-strategy attribution
        (condition #1) and ALL degraded strategies durably gated (condition
        #2), the residual portfolio is mathematically bounded GREEN — a
        weighted mean of individually-GREEN strategy scores cannot itself
        exceed the yellow threshold. So this precondition is exercised via a
        targeted monkeypatch of the isolated `_residual_active_portfolio_brier_level`
        helper (unit-tested in isolation from the data-shape constraint) to
        verify the orchestration keeps global ORANGE when the residual verdict
        is NOT GREEN, regardless of how that residual was computed."""
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = self._orange_rows()

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)
        monkeypatch.setattr(
            riskguard_module,
            "_residual_active_portfolio_brier_level",
            lambda *a, **k: (RiskLevel.ORANGE, 0.31, 10, []),
        )

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        assert level == RiskLevel.YELLOW
        assert risk_row["level"] == RiskLevel.YELLOW.value
        assert details["portfolio_brier_raw_level"] == "ORANGE"
        assert details["brier_level"] == "YELLOW"
        assert details["localized_orange_scope"] is False
        assert details["brier_strategy_localization"]["status"] == "orange_residual_portfolio_not_green"
        assert details["brier_strategy_localization"]["residual_brier_level"] == "ORANGE"
        assert details["brier_strategy_localization"]["gate_confirmation"] == {
            "opening_inertia": True
        }

    def test_red_localizes_when_clean_attribution_gate_confirmed_and_residual_green(
        self, monkeypatch, tmp_path
    ):
        """Severe probability failure stops its entry law, not every holding.

        RED remains global unless exact strategy attribution, confirmed durable
        gates, and a GREEN residual portfolio are all proven in the same tick.
        """
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = [
            _settlement_row(
                trade_id=f"opening-{i}",
                strategy="opening_inertia",
                p_posterior=0.95,
                outcome=0,
                target_date=_independent_target_date(i),
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
                target_date=_independent_target_date(45 + i),
            )
            for i in range(5)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        gate_row = get_connection(zeus_db).execute(
            """
            SELECT strategy_key, status
            FROM risk_actions
            WHERE action_id = 'riskguard:gate:opening_inertia'
            """
        ).fetchone()

        assert level == RiskLevel.GREEN
        assert risk_row["level"] == RiskLevel.GREEN.value
        assert details["portfolio_brier_level"] == "RED"
        assert details["portfolio_brier_raw_level"] == "RED"
        assert details["brier_level"] == "GREEN"
        assert details["brier_all_strategies_level"] == "RED"
        assert details["brier_active_portfolio_level"] == "GREEN"
        assert details["localized_orange_scope"] is False
        assert details["localized_red_scope"] is True
        assert details["brier_strategy_localization"]["status"] == "localized_red_scope"
        assert details["brier_strategy_localization"]["gate_confirmation"] == {
            "opening_inertia": True
        }
        assert dict(gate_row) == {
            "strategy_key": "opening_inertia",
            "status": "active",
        }

    def test_red_stays_global_when_durable_gate_write_is_skipped(
        self, monkeypatch, tmp_path
    ):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = [
            _settlement_row(
                trade_id=f"opening-{i}",
                strategy="opening_inertia",
                p_posterior=0.95,
                outcome=0,
                target_date=_independent_target_date(i),
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
                target_date=_independent_target_date(45 + i),
            )
            for i in range(5)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(bankroll=211.37),
        )
        monkeypatch.setattr(
            riskguard_module,
            "load_tracker",
            lambda: strategy_tracker_module.StrategyTracker(),
        )
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda *_, **__: rows,
        )
        monkeypatch.setattr(
            riskguard_module,
            "_sync_riskguard_strategy_gate_actions",
            lambda *a, **k: {
                "status": "skipped_dependency_lock",
                "emitted_count": 0,
                "expired_count": 0,
            },
        )

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        assert level == RiskLevel.YELLOW
        assert risk_row["level"] == RiskLevel.YELLOW.value
        assert details["portfolio_brier_level"] == "RED"
        assert details["portfolio_brier_raw_level"] == "RED"
        assert details["brier_level"] == "YELLOW"
        assert details["localized_red_scope"] is False
        assert (
            details["brier_strategy_localization"]["status"]
            == "durable_strategy_gate_unconfirmed_global_entry_block"
        )

    def test_orange_stays_global_when_read_after_write_confirmation_finds_no_gate_row(
        self, monkeypatch, tmp_path,
    ):
        """Condition #2 failure mode B: the write CLAIMS emission ("emitted")
        but the read-after-write confirmation finds no active gate row for the
        degraded strategy — must NOT be trusted, unlike YELLOW's write-status-only
        check."""
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = self._orange_rows()

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)
        # The write CLAIMS success but performs no actual INSERT — simulating a
        # write that lies about emission (or writes the wrong row/strategy_key).
        monkeypatch.setattr(
            riskguard_module,
            "_sync_riskguard_strategy_gate_actions",
            lambda *a, **k: {"status": "emitted", "emitted_count": 1, "expired_count": 0},
        )

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])
        gate_row = get_connection(zeus_db).execute(
            "SELECT 1 FROM risk_actions WHERE action_id = 'riskguard:gate:opening_inertia'"
        ).fetchone()

        assert gate_row is None
        assert level == RiskLevel.YELLOW
        assert risk_row["level"] == RiskLevel.YELLOW.value
        assert details["portfolio_brier_raw_level"] == "ORANGE"
        assert details["brier_level"] == "YELLOW"
        assert details["localized_orange_scope"] is False
        assert (
            details["brier_strategy_localization"]["status"]
            == "durable_strategy_gate_unconfirmed_global_orange"
        )
        assert details["brier_strategy_localization"]["gate_confirmation"] == {
            "opening_inertia": False
        }
        assert details["brier_strategy_localization"]["durable_risk_action_status"] == "emitted"


class TestResidualBrierMinSample:
    """Pool edition of the minimum-evidence floor (2026-07-05 live incident):
    ORANGE localization's residual check let n=1 strategies vote — two
    single-loss corpses (day0_nowcast 0.92, qkernel 0.79) dragged an
    otherwise-GREEN residual to YELLOW and kept the whole book frozen."""

    def _row(self, strategy, p, o):
        return {"strategy": strategy, "p_posterior": p, "outcome": o,
                "source": "position_events", "metric_ready": True}

    def test_thin_strategies_do_not_vote_in_residual(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.3, "brier_red": 0.35}
        rows = (
            [self._row("center_buy", 0.12, 0) for _ in range(10)]
            + [self._row("day0_nowcast_entry", 0.96, 0)]      # n=1 corpse
            + [self._row("forecast_qkernel_entry", 0.89, 0)]  # n=1 corpse
        )
        level, score, n, thin = riskguard_module._residual_active_portfolio_brier_level(
            rows, thresholds, set()
        )
        assert level == RiskLevel.GREEN
        assert n == 10
        assert thin == ["day0_nowcast_entry", "forecast_qkernel_entry"]
        assert score < 0.25

    def test_thick_degraded_strategy_still_fails_residual(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.3, "brier_red": 0.35}
        rows = [self._row("center_buy", 0.9, 0) for _ in range(10)]
        level, score, n, thin = riskguard_module._residual_active_portfolio_brier_level(
            rows, thresholds, set()
        )
        assert level == RiskLevel.RED
        assert n == 10
        assert thin == []

    def test_empty_after_thin_exclusion_is_green(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.3, "brier_red": 0.35}
        rows = [self._row("day0_nowcast_entry", 0.96, 0)]
        level, score, n, thin = riskguard_module._residual_active_portfolio_brier_level(
            rows, thresholds, set()
        )
        assert level == RiskLevel.GREEN
        assert n == 0
        assert thin == ["day0_nowcast_entry"]


class TestEntryExecutionSummaryWindow:
    """Execution quality measures the CURRENT machinery (2026-07-05): events
    older than _ENTRY_EXECUTION_LOOKBACK are excluded. Live incident: a 0.14
    fill rate computed over 07-01..07-03 legacy maker rests kept gating
    forecast_qkernel_entry after the execution pipeline it measured was
    rebuilt and redeployed."""

    def test_stale_terminal_events_are_excluded(self, tmp_path):
        from src.state.db import get_connection, init_schema

        db = tmp_path / "zeus.db"
        conn = get_connection(db)
        init_schema(conn)
        for i in range(10):
            conn.execute(
                """
                INSERT INTO position_events
                (event_id, position_id, event_version, sequence_no, event_type,
                 occurred_at, strategy_key, source_module, env, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (f"stale-{i}:ENTRY_ORDER_VOIDED:1", f"stale-{i}", 1, 1,
                 "ENTRY_ORDER_VOIDED", "2026-04-01T10:00:00Z",
                 "forecast_qkernel_entry", "test", "live", "{}"),
            )
        conn.commit()

        summary = riskguard_module._entry_execution_summary(conn)
        assert summary["overall"]["terminal_observed"] == 0
        assert "forecast_qkernel_entry" not in summary["by_strategy"]
        conn.close()

    def test_recent_terminal_events_are_counted(self, tmp_path):
        from src.state.db import get_connection, init_schema

        db = tmp_path / "zeus.db"
        conn = get_connection(db)
        init_schema(conn)
        for i in range(10):
            conn.execute(
                """
                INSERT INTO position_events
                (event_id, position_id, event_version, sequence_no, event_type,
                 occurred_at, strategy_key, source_module, env, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (f"fresh-{i}:ENTRY_ORDER_VOIDED:1", f"fresh-{i}", 1, 1,
                 "ENTRY_ORDER_VOIDED", _recent_iso(minutes=10 - i),
                 "forecast_qkernel_entry", "test", "live", "{}"),
            )
        conn.commit()

        summary = riskguard_module._entry_execution_summary(conn)
        bucket = summary["by_strategy"]["forecast_qkernel_entry"]
        assert bucket["terminal_observed"] == 10
        assert bucket["fill_rate"] == 0.0
        conn.close()

    def test_offset_timestamps_use_exact_utc_window_and_order(self, tmp_path):
        from src.state.db import get_connection, init_schema

        db = tmp_path / "zeus.db"
        conn = get_connection(db)
        init_schema(conn)
        rows = (
            (
                "fresh-offset",
                "ENTRY_ORDER_FILLED",
                "2026-07-24T00:30:00-12:00",
            ),
            (
                "stale-offset",
                "ENTRY_ORDER_VOIDED",
                "2026-07-24T23:00:00+14:00",
            ),
            (
                "newest",
                "ENTRY_ORDER_REJECTED",
                "2026-07-26T10:00:00+00:00",
            ),
        )
        for position_id, event_type, occurred_at in rows:
            conn.execute(
                """
                INSERT INTO position_events
                (event_id, position_id, event_version, sequence_no, event_type,
                 occurred_at, strategy_key, source_module, env, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"{position_id}:{event_type}:1",
                    position_id,
                    1,
                    1,
                    event_type,
                    occurred_at,
                    "forecast_qkernel_entry",
                    "test",
                    "live",
                    "{}",
                ),
            )
        conn.commit()

        summary = riskguard_module._entry_execution_summary(
            conn,
            now="2026-07-26T12:00:00+00:00",
            limit=1,
        )
        assert summary["overall"]["rejected"] == 1
        assert summary["overall"]["filled"] == 0

        summary = riskguard_module._entry_execution_summary(
            conn,
            now="2026-07-26T12:00:00+00:00",
            limit=10,
        )
        assert summary["overall"]["rejected"] == 1
        assert summary["overall"]["filled"] == 1
        assert summary["overall"]["voided"] == 0
        conn.close()


class TestExecutionDecayNotASelectionGate:
    """execution_decay must NEVER emit a per-strategy selection gate (2026-07-05,
    INV-05 advisory-risk-forbidden). A fill-rate heuristic is not capital
    protection: non-fills and voided maker rests cost $0, and the fill_rate
    denominator (filled / filled+rejected+voided) counts our own DELIBERATE
    maker-patience pulls as "decay", penalizing correct behavior. The gate
    self-perpetuated (gate -> quiet -> frozen window -> re-gate), blocking the
    only fat-edge strategy every cycle and starving the settle->grade loop.
    Calibration failure — the real risk — is caught by brier_degraded and
    edge_compression, not by fill rate. fill_rate stays computed for
    observability and the GLOBAL execution_quality signal; it never gates."""

    def _run_decay_tick(self, monkeypatch, tmp_path, *, minutes_old: int):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
        )

        conn = get_connection(zeus_db)
        init_schema(conn)
        # 12 terminal-but-unfilled events for one strategy: fill_rate 0.0,
        # observed 12 (>= 10 floor). All inside the 48h execution lookback so
        # they COUNT; minutes_old decides whether they are a CURRENT verdict.
        for i in range(12):
            conn.execute(
                """
                INSERT INTO position_events
                (event_id, position_id, event_version, sequence_no, event_type,
                 occurred_at, strategy_key, source_module, env, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (f"decay-{i}:ENTRY_ORDER_VOIDED:1", f"decay-{i}", 1, 1,
                 "ENTRY_ORDER_VOIDED", _recent_iso(minutes=minutes_old + i),
                 "center_buy", "test", "live", "{}"),
            )
        conn.commit()
        conn.close()

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return level, json.loads(row["details_json"])

    def test_fresh_low_fill_window_does_not_gate(self, monkeypatch, tmp_path):
        # execution_decay is NOT a selection gate (INV-05). Even a FRESH window
        # (newest terminal ~1 min old) with 12 voided rests (fill_rate 0.0,
        # observed 12 >= floor) must NOT emit a per-strategy gate: non-fills cost
        # $0 and the 12 voids are deliberate maker-patience pulls, not decay.
        # (Before the removal this asserted the gate fired.)
        _level, details = self._run_decay_tick(monkeypatch, tmp_path, minutes_old=1)
        assert "center_buy" not in details["recommended_strategy_gate_reasons"]
        assert "center_buy" not in details["recommended_strategy_gates"]
        # fill_rate is still computed for observability — just never gated on.
        bucket = details["entry_execution_summary"]["by_strategy"]["center_buy"]
        assert bucket["terminal_observed"] == 12
        assert bucket["fill_rate"] == 0.0

    def test_stale_frozen_window_does_not_gate(self, monkeypatch, tmp_path):
        # Same 12 events, newest ~3h old: inside the 48h lookback (still
        # counted) but outside the 2h fresh horizon. This is the live
        # forecast_qkernel_entry case — the strategy is quiet BECAUSE it was
        # gated, so the frozen window must not re-gate it.
        _level, details = self._run_decay_tick(monkeypatch, tmp_path, minutes_old=180)
        # The window is still counted (proves it did not simply age out of the
        # 48h lookback — the summary sees a decayed fill rate)...
        bucket = details["entry_execution_summary"]["by_strategy"]["center_buy"]
        assert bucket["terminal_observed"] == 12
        assert bucket["fill_rate"] == 0.0
        # ...yet no per-strategy execution_decay gate is emitted (self-heal).
        assert "center_buy" not in details["recommended_strategy_gate_reasons"]
        assert "center_buy" not in details["recommended_strategy_gates"]

    def test_overall_summary_records_newest_terminal_at(self, tmp_path):
        db = tmp_path / "zeus.db"
        conn = get_connection(db)
        init_schema(conn)
        newest_terminal = _recent_iso(minutes=5)
        # A POSITION_OPEN_INTENT NEWER (1 min) than the terminal void (5 min):
        # newest_terminal_at must track the terminal event, not the intent.
        conn.execute(
            """
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, env, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            ("nt-open:POSITION_OPEN_INTENT:1", "nt-open", 1, 1, "POSITION_OPEN_INTENT",
             _recent_iso(minutes=1), "center_buy", "test", "live", "{}"),
        )
        conn.execute(
            """
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, env, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            ("nt-void:ENTRY_ORDER_VOIDED:1", "nt-void", 1, 1, "ENTRY_ORDER_VOIDED",
             newest_terminal, "center_buy", "test", "live", "{}"),
        )
        conn.commit()
        summary = riskguard_module._entry_execution_summary(conn)
        assert summary["overall"]["newest_terminal_at"] == newest_terminal
        assert summary["by_strategy"]["center_buy"]["newest_terminal_at"] == newest_terminal
        conn.close()

    def test_verdict_current_predicate(self):
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(minutes=30)).isoformat()
        stale = (now - timedelta(hours=3)).isoformat()
        assert riskguard_module._execution_decay_verdict_is_current(fresh, now=now) is True
        assert riskguard_module._execution_decay_verdict_is_current(stale, now=now) is False
        # A missing window is never a current verdict (fail-safe: do not gate).
        assert riskguard_module._execution_decay_verdict_is_current(None, now=now) is False
        # Boundary: exactly at the horizon is still current (<=).
        boundary = (now - riskguard_module._EXECUTION_DECAY_FRESH_HORIZON).isoformat()
        assert riskguard_module._execution_decay_verdict_is_current(boundary, now=now) is True


class TestStrategyBrierMinSample:
    """Per-strategy Brier verdicts need evidence (2026-07-05 live incident:
    forecast_qkernel_entry was gated on a single confident settled loss —
    n=1, Brier (0.79-0)^2 = 0.6241 here — while its live candidates carried
    the book's best positive edges).
    Below _STRATEGY_BRIER_MIN_SAMPLE the strategy stays visible in
    by_strategy (thin_sample_no_verdict) but never enters
    degraded_strategies; the portfolio pool and loss gates still bind."""

    def test_single_loss_does_not_convict_a_strategy(self):
        rows = [
            {
                "strategy": "forecast_qkernel_entry",
                "target_date": "2026-08-01",
                "p_posterior": 0.79,
                "outcome": 0,
            },
        ] + [
            {
                "strategy": "center_buy",
                "target_date": f"2026-08-{day:02d}",
                "p_posterior": 0.80,
                "outcome": 1,
            }
            for day in range(1, 13)
        ]
        out = riskguard_module._strategy_brier_breakdown(
            rows, {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        qk = out["by_strategy"]["forecast_qkernel_entry"]
        assert qk["sample_size"] == 1
        assert qk["level"] == "GREEN"
        assert qk["thin_sample_no_verdict"] is True
        assert "forecast_qkernel_entry" not in out["degraded_strategies"]

    def test_floor_boundary_convicts_at_min_sample(self):
        n = riskguard_module._STRATEGY_BRIER_MIN_SAMPLE
        bad = [
            {
                "strategy": "opening_inertia",
                "target_date": f"2026-08-{day:02d}",
                "p_posterior": 0.58,
                "outcome": 0,
            }
            for day in range(1, n + 1)
        ]
        out = riskguard_module._strategy_brier_breakdown(
            bad, {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        oi = out["by_strategy"]["opening_inertia"]
        assert oi["sample_size"] == n
        assert oi["independent_target_date_count"] == n
        assert oi["level"] != "GREEN"
        assert "opening_inertia" in out["degraded_strategies"]

    def test_same_target_date_cells_do_not_fabricate_minimum_evidence(self):
        n = riskguard_module._STRATEGY_BRIER_MIN_SAMPLE
        bad = [
            {
                "strategy": "forecast_qkernel_entry",
                "target_date": "2026-08-15",
                "p_posterior": 0.80,
                "outcome": 0,
            }
            for _ in range(n * 3)
        ]

        assert riskguard_module._brier_evidence_ready_rows(bad) == []
        out = riskguard_module._strategy_brier_breakdown(
            bad,
            {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        qkernel = out["by_strategy"]["forecast_qkernel_entry"]
        assert qkernel["sample_size"] == n * 3
        assert qkernel["independent_target_date_count"] == 1
        assert qkernel["thin_sample_no_verdict"] is True
        assert "forecast_qkernel_entry" not in out["degraded_strategies"]

    def test_one_below_floor_does_not_convict(self):
        n = riskguard_module._STRATEGY_BRIER_MIN_SAMPLE - 1
        bad = [
            {
                "strategy": "opening_inertia",
                "target_date": f"2026-08-{day:02d}",
                "p_posterior": 0.58,
                "outcome": 0,
            }
            for day in range(1, n + 1)
        ]
        out = riskguard_module._strategy_brier_breakdown(
            bad, {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        assert "opening_inertia" not in out["degraded_strategies"]
        assert out["by_strategy"]["opening_inertia"]["thin_sample_no_verdict"] is True


class TestStrategyBrierMinSampleContinued:
    def test_one_strategy_cannot_pool_across_probability_semantics(self):
        rows = [
            {
                "strategy": "forecast_qkernel_entry",
                "decision_law_id": "predicted_bin_ev_v1",
                "probability_semantics_revisions": (revision,),
                "p_posterior": 0.8,
                "outcome": 0,
            }
            for revision in (
                riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
                riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
            )
            for _ in range(5)
        ]

        ready = riskguard_module._brier_evidence_ready_rows(rows)
        assert ready == []
        breakdown = riskguard_module._strategy_brier_breakdown(
            ready,
            {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        assert breakdown["degraded_strategies"] == {}

    def test_degraded_probability_cohort_does_not_convict_green_revision(self):
        old_revision = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )
        current_revision = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        rows = [
            {
                "strategy": "forecast_qkernel_entry",
                "decision_law_id": "predicted_bin_ev_v1",
                "probability_semantics_revisions": (old_revision,),
                "target_date": f"2026-07-{day:02d}",
                "p_posterior": 0.8,
                "outcome": 0,
            }
            for day in range(1, 11)
        ] + [
            {
                "strategy": "forecast_qkernel_entry",
                "decision_law_id": "predicted_bin_ev_v1",
                "probability_semantics_revisions": (current_revision,),
                "target_date": f"2026-08-{day:02d}",
                "p_posterior": 0.8,
                "outcome": 1,
            }
            for day in range(1, 11)
        ]

        ready = riskguard_module._brier_evidence_ready_rows(rows)
        breakdown = riskguard_module._strategy_brier_breakdown(
            ready,
            {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        degraded = breakdown["degraded_strategies"]["forecast_qkernel_entry"]

        assert degraded["sample_size"] == 10
        assert degraded["probability_semantics_revisions"] == [old_revision]
        assert current_revision not in degraded["probability_semantics_revisions"]

    def test_heterogeneous_thin_probability_laws_do_not_form_portfolio_verdict(
        self,
        monkeypatch,
        tmp_path,
    ):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        qkernel_rows = [
            {
                **_settlement_row(
                    trade_id=f"qkernel-loss-{i}",
                    strategy="forecast_qkernel_entry",
                    p_posterior=0.8,
                    outcome=0,
                ),
                "probability_semantics_revisions": (
                    riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
                ),
            }
            for i in range(6)
        ]
        day0_rows = []
        for i in range(5):
            row = _settlement_row(
                trade_id=f"day0-loss-{i}",
                strategy="day0_nowcast_entry",
                p_posterior=0.8,
                outcome=0,
            )
            row["entry_q_version"] = (
                f"day0-semrev:{DAY0_PROBABILITY_SEMANTICS_REVISION}:test-{i}"
            )
            day0_rows.append(row)
        rows = qkernel_rows + day0_rows

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(bankroll=211.37),
        )
        monkeypatch.setattr(
            riskguard_module,
            "load_tracker",
            lambda: strategy_tracker_module.StrategyTracker(),
        )
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda *_, **__: rows,
        )

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        assert details["portfolio_brier_raw_level"] == "RED"
        assert details["portfolio_brier_level"] == "GREEN"
        assert details["brier_level"] == "GREEN"
        assert details["brier_actuating_sample_size"] == 11
        assert details["brier_evidence_ready_sample_size"] == 0
        assert details["portfolio_brier_thin_sample_no_verdict"] is True
        assert details["recommended_strategy_gates"] == [
            "day0_nowcast_entry",
            "forecast_qkernel_entry",
        ]
        assert details["market_relative_alpha_gate_reason"].startswith(
            "market_relative_alpha_unproven("
        )
        assert details["day0_market_relative_alpha_gate_required"] is True
        assert details["day0_market_relative_alpha_gate_reason"].startswith(
            "market_relative_alpha_unproven("
        )
        assert details["market_relative_alpha_observation"].startswith(
            "market_relative_alpha_unproven("
        )
        assert details["day0_market_relative_alpha_observation"].startswith(
            "market_relative_alpha_unproven("
        )
        assert level == RiskLevel.GREEN
        assert risk_row["level"] == RiskLevel.GREEN.value

        monkeypatch.setattr(
            riskguard_module,
            "_collateral_identity_level",
            lambda _conn: RiskLevel.RED,
        )
        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        assert details["portfolio_brier_raw_level"] == "RED"
        assert details["brier_level"] == "GREEN"
        assert details["portfolio_brier_level"] == "GREEN"
        assert level == RiskLevel.RED
        assert risk_row["level"] == RiskLevel.RED.value

    def test_shared_recorded_mechanism_requires_one_recorded_decision_law(self):
        rows = [
            {
                "strategy": "day0_nowcast_entry",
                "decision_law_id": "predicted_bin_ev_v1",
                "decision_law_identity_ready": True,
                "decision_snapshot_id": f"metar_fast:ZGGG:day0:{i}",
                "target_date": f"2026-07-{i + 1:02d}",
                "p_posterior": 0.99,
                "outcome": 0,
            }
            for i in range(7)
        ] + [
            {
                "strategy": "settlement_capture",
                "decision_law_id": "predicted_bin_ev_v1",
                "decision_law_identity_ready": True,
                "decision_snapshot_id": f"metar_fast:LIMC:capture:{i}",
                "target_date": f"2026-07-{i + 8:02d}",
                "p_posterior": 0.90,
                "outcome": 0,
            }
            for i in range(7)
        ] + [
            {
                "strategy": "forecast_qkernel_entry",
                "decision_snapshot_id": f"forecast-certificate-{i}",
                "p_posterior": 0.80,
                "outcome": 1,
            }
            for i in range(34)
        ]
        out = riskguard_module._strategy_brier_breakdown(
            rows, {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )

        mechanism = (
            "law:predicted_bin_ev_v1:decision_snapshot:metar_fast"
        )
        metar = out["by_mechanism"][mechanism]
        assert metar["sample_size"] == 14
        assert metar["level"] == "RED"
        assert set(out["degraded_strategies"]) == {
            "day0_nowcast_entry",
            "settlement_capture",
        }
        assert out["degraded_strategies"]["day0_nowcast_entry"]["cohort"] == (
            mechanism
        )
        assert out["degraded_strategies"]["settlement_capture"]["member_sample_size"] == 7
        assert out["by_strategy"]["forecast_qkernel_entry"]["level"] == "GREEN"
        assert riskguard_module._brier_evidence_ready_rows(rows[:14]) == rows[:14]

    def test_unlabeled_snapshot_namespace_cannot_pool_thin_legacy_strategies(self):
        rows = [
            {
                "strategy": strategy,
                "decision_snapshot_id": f"metar_fast:{strategy}:{i}",
                "p_posterior": 0.99,
                "outcome": 0,
            }
            for strategy in ("day0_nowcast_entry", "settlement_capture")
            for i in range(7)
        ]

        out = riskguard_module._strategy_brier_breakdown(
            rows,
            {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )

        assert out["by_mechanism"] == {}
        assert out["degraded_strategies"] == {}

    @pytest.mark.parametrize(
        ("p_posterior", "expected_portfolio_level", "expected_active_level"),
        [
            (0.51, RiskLevel.YELLOW, RiskLevel.YELLOW),
            (0.56, RiskLevel.ORANGE, RiskLevel.YELLOW),
        ],
    )
    def test_current_law_brier_breach_stays_global_without_law_gate_consumer(
        self,
        monkeypatch,
        tmp_path,
        p_posterior,
        expected_portfolio_level,
        expected_active_level,
    ):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = [
            _settlement_row(
                trade_id=f"law-loss-{i}",
                strategy="",
                p_posterior=p_posterior,
                outcome=0,
                target_date=f"2026-08-{i + 1:02d}",
            )
            for i in range(riskguard_module._STRATEGY_BRIER_MIN_SAMPLE)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(bankroll=211.37),
        )
        monkeypatch.setattr(
            riskguard_module,
            "load_tracker",
            lambda: strategy_tracker_module.StrategyTracker(),
        )
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda *_, **__: rows,
        )

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])
        law_gate = get_connection(zeus_db).execute(
            """
            SELECT 1 FROM risk_actions
            WHERE status = 'active'
              AND strategy_key = 'law:predicted_bin_ev_v1'
            """
        ).fetchone()

        assert level == expected_active_level
        assert details["portfolio_brier_level"] == expected_portfolio_level.value
        assert details["brier_level"] == expected_active_level.value
        assert details["brier_strategy_localization"]["status"] == "not_localized"
        assert set(details["brier_strategy_breakdown"]["degraded_strategies"]) == {
            "law:predicted_bin_ev_v1"
        }
        assert law_gate is None

    @pytest.mark.parametrize(
        (
            "sample_size",
            "expected_portfolio_level",
            "expected_active_level",
            "expected_thin",
            "expected_status",
            "expected_reason",
            "expected_gates",
        ),
        [
            (
                1,
                RiskLevel.GREEN,
                RiskLevel.GREEN,
                True,
                "not_applicable",
                "portfolio_brier_thin_sample_no_verdict",
                ["forecast_qkernel_entry"],
            ),
            (
                10,
                RiskLevel.RED,
                RiskLevel.GREEN,
                False,
                "localized_red_scope",
                None,
                ["forecast_qkernel_entry"],
            ),
        ],
    )
    def test_portfolio_brier_requires_minimum_evidence(
        self,
        monkeypatch,
        tmp_path,
        sample_size,
        expected_portfolio_level,
        expected_active_level,
        expected_thin,
        expected_status,
        expected_reason,
        expected_gates,
    ):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = [
            _settlement_row(
                trade_id=f"loss-{i}",
                strategy="forecast_qkernel_entry",
                p_posterior=0.9033,
                outcome=0,
                target_date=f"2026-08-{i + 1:02d}",
            )
            for i in range(sample_size)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(bankroll=211.37),
        )
        monkeypatch.setattr(
            riskguard_module,
            "load_tracker",
            lambda: strategy_tracker_module.StrategyTracker(),
        )
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda *_, **__: rows,
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, brier, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert row["brier"] > 0.8
        assert details["portfolio_brier_raw_level"] == "RED"
        assert details["portfolio_brier_level"] == expected_portfolio_level.value
        assert details["brier_level"] == expected_active_level.value
        assert details["portfolio_brier_thin_sample_no_verdict"] is expected_thin
        assert details["brier_strategy_localization"]["status"] == expected_status
        if expected_reason is not None:
            assert details["brier_strategy_localization"]["reason"] == expected_reason
        assert details["recommended_strategy_gates"] == expected_gates
        assert level == expected_active_level
        assert row["level"] == expected_active_level.value


class TestQkernelMarketRelativeAlphaEvidence:
    """Thin samples may actuate only through sequential market-relative proof."""

    @staticmethod
    def _row(
        trade_id: str,
        *,
        city: str,
        q: float,
        outcome: int,
    ) -> dict:
        return {
            "trade_id": trade_id,
            "strategy": "forecast_qkernel_entry",
            "decision_law_id": "predicted_bin_ev_v1",
            "global_selection_revision": (
                riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "probability_semantics_ready": True,
            "probability_semantics_revisions": (
                "stale_ensemble_absolute_disagreement_v2",
            ),
            "p_posterior": q,
            "outcome": outcome,
            "city": city,
            "settled_at": "2026-08-10T22:00:00+00:00",
        }

    @staticmethod
    def _conn() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                entry_price REAL,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE execution_fact (
                position_id TEXT,
                order_role TEXT,
                filled_at TEXT,
                terminal_exec_status TEXT,
                fill_price REAL,
                shares REAL
            )
            """
        )
        return conn

    @staticmethod
    def _actual_global_conn(
        *,
        q: float = 0.95,
        receipt_candidate_id: str = "candidate",
        terminal_status: str = "partial",
        strategy_key: str = "forecast_qkernel_entry",
        global_selection_revision: str | None = None,
    ) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ':memory:' AS world")
        conn.execute(
            "CREATE TABLE execution_fact ("
            "position_id TEXT,command_id TEXT,order_role TEXT,filled_at TEXT,"
            "terminal_exec_status TEXT,fill_price REAL,shares REAL)"
        )
        conn.execute(
            "CREATE TABLE position_decision_attribution ("
            "position_id TEXT,command_id TEXT,decision_certificate_hash TEXT,"
            "resolution TEXT,intent_kind TEXT)"
        )
        conn.execute(
            "CREATE TABLE world.decision_certificates ("
            "certificate_hash TEXT,certificate_type TEXT,mode TEXT,"
            "verifier_status TEXT,payload_json TEXT)"
        )
        conn.execute(
            "INSERT INTO execution_fact VALUES "
            "('actual','command','entry','2026-08-10T12:00:00Z',?,0.05,5.0)",
            (terminal_status,),
        )
        conn.execute(
            "INSERT INTO position_decision_attribution VALUES "
            "('actual','command','certificate','ATTRIBUTED','ENTRY')"
        )
        payload = {
            "strategy_key": strategy_key,
            "qkernel_execution_economics": {
                "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
                "global_probability_functional": "POSTERIOR_PREDICTIVE_MEAN",
                "global_execution_mode": "TAKER_LIMIT",
                "global_candidate_id": "candidate",
                "global_actuation_identity": "actuation",
                "global_selection_epoch_identity": "epoch",
                "global_winner_event_id": "winner-event",
                "global_selection_revision": (
                    global_selection_revision
                    or riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                ),
                "global_cut_time_win_probability_mean": q,
                "global_target_shares": "5",
                "global_max_spend_usd": "0.25",
                "global_expected_delta_log_wealth": 0.01,
                "global_expected_ev_usd": 1.0,
                "global_auction_receipt": {
                    "winner_candidate_id": receipt_candidate_id,
                    "winner_actuation_identity": "actuation",
                    "selection_epoch_identity": "epoch",
                    "winner_event_id": "winner-event",
                },
            },
        }
        conn.execute(
            "INSERT INTO world.decision_certificates VALUES "
            "('certificate','ActionableTradeCertificate','LIVE','VERIFIED',?)",
            (json.dumps(payload),),
        )
        return conn

    @staticmethod
    def _actual_global_row(
        *,
        q: float = 0.95,
        strategy_key: str = "forecast_qkernel_entry",
        revision: str | None = None,
    ) -> dict:
        return {
            "trade_id": "actual",
            "strategy": strategy_key,
            "decision_law_id": "predicted_bin_ev_v1",
            "probability_identity_ready": True,
            "probability_semantics_ready": True,
            "probability_semantics_revisions": (
                revision
                or riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
            ),
            "p_posterior": q,
            "outcome": 0,
            "settled_at": "2026-08-10T22:00:00+00:00",
            "entry_market_benchmark_ready": True,
            "entry_market_benchmark": 0.05,
            "entry_market_benchmark_family": ("NYC", "2026-08-10", "high"),
        }

    @staticmethod
    def _actual_capital_curve() -> dict:
        return {
            "curve": [
                {
                    "position_id": "actual",
                    "capital_committed_usd": 0.25,
                    "net_realized_pnl_usd": -0.25,
                }
            ]
        }

    def test_live_failure_path_rejects_without_counting_same_city_date_twice(self):
        rows = [
            self._row("helsinki-yes", city="Helsinki", q=0.3720459264, outcome=0),
            self._row("helsinki-no", city="Helsinki", q=0.9998639330, outcome=1),
            self._row("guangzhou", city="Guangzhou", q=0.4484491333, outcome=0),
            self._row("tel-aviv", city="Tel Aviv", q=0.9059764849, outcome=0),
        ]
        prices = {
            "helsinki-yes": 0.06,
            "helsinki-no": 0.82,
            "guangzhou": 0.06,
            "tel-aviv": 0.30,
        }
        conn = self._conn()
        conn.executemany(
            "INSERT INTO position_current VALUES (?,?,?,?,?)",
            [
                (
                    row["trade_id"],
                    prices[row["trade_id"]],
                    row["city"],
                    "2026-08-10" if row["city"] != "Tel Aviv" else "2026-08-09",
                    "high",
                )
                for row in rows
            ],
        )
        conn.executemany(
            "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10','filled',?,1)",
            [(trade_id, price) for trade_id, price in prices.items()],
        )

        bound = riskguard_module._bind_entry_market_benchmarks(conn, rows)
        evidence = riskguard_module._qkernel_market_relative_alpha_evidence(
            bound,
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        assert evidence["status"] == "rejected"
        assert evidence["rejected"] is True
        assert len(evidence["cohorts"]) == 1
        cohort = evidence["cohorts"][0]
        assert cohort["candidate_count"] == 4
        assert cohort["independent_cluster_count"] == 3
        assert cohort["market_over_model_evalue"] > 12.0
        conn.close()

    def test_one_loss_below_sequential_evidence_boundary_does_not_gate(self):
        conn = self._conn()
        conn.execute(
            "INSERT INTO position_current VALUES (?,?,?,?,?)",
            ("one-loss", 0.20, "NYC", "2026-08-10", "high"),
        )
        conn.execute(
            "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10','filled',?,1)",
            ("one-loss", 0.20),
        )
        rows = [self._row("one-loss", city="NYC", q=0.79, outcome=0)]

        evidence = riskguard_module._qkernel_market_relative_alpha_evidence(
            riskguard_module._bind_entry_market_benchmarks(conn, rows),
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        assert evidence["status"] == "ok"
        assert evidence["rejected"] is False
        assert evidence["cohorts"][0]["market_over_model_evalue"] < 10.0
        conn.close()

    @pytest.mark.parametrize("terminal_status", ["filled", "confirmed", "partial"])
    def test_entry_market_benchmark_accepts_economically_filled_statuses(
        self,
        terminal_status,
    ):
        conn = self._conn()
        conn.execute(
            "INSERT INTO position_current VALUES (?,?,?,?,?)",
            ("economic-fill", 0.20, "NYC", "2026-08-10", "high"),
        )
        conn.execute(
            "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10',?,?,1)",
            ("economic-fill", terminal_status, 0.20),
        )

        bound = riskguard_module._bind_entry_market_benchmarks(
            conn,
            [self._row("economic-fill", city="NYC", q=0.60, outcome=1)],
        )

        assert bound[0]["entry_market_benchmark_ready"] is True
        assert bound[0]["entry_market_benchmark"] == pytest.approx(0.20)
        conn.close()

    def test_actual_global_winner_fill_supplies_capital_law_evidence(self):
        current = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )
        conn = self._actual_global_conn()

        bound, status = riskguard_module._bind_actual_global_capital_evidence(
            conn,
            [self._actual_global_row()],
            strategy_key="forecast_qkernel_entry",
            capital_curve=self._actual_capital_curve(),
        )
        evidence = riskguard_module._market_relative_alpha_evidence(
            bound,
            strategy_key="forecast_qkernel_entry",
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )
        reason, revisions = (
            riskguard_module._market_relative_alpha_rejection_gate_reason(
                {"status": "ok", "licensed_revisions": [current]},
                evidence,
                required_evalue=10.0,
            )
        )

        assert status["status"] == "ok"
        assert status["capital_law_ready_count"] == 1
        assert status["capital_gain_proof_ready_count"] == 1
        assert bound[0]["persisted_decision_law_id"] == "predicted_bin_ev_v1"
        assert bound[0]["decision_law_id"] == "executable_min_order_capital_gain_v2"
        assert bound[0]["global_selection_revision"] == (
            riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        )
        assert bound[0]["capital_gain_proof_ready"] is True
        assert bound[0]["hypothetical_capital_committed_usd"] == pytest.approx(0.25)
        assert bound[0]["hypothetical_realized_pnl_usd"] == pytest.approx(-0.25)
        assert evidence["rejected"] is True
        assert evidence["cohorts"][0]["market_over_model_evalue"] == pytest.approx(19.0)
        assert revisions == (current,)
        assert reason is not None and "status=rejected" in reason
        conn.close()

    def test_superseded_global_selection_cannot_name_current_capital_law(self):
        conn = self._actual_global_conn(
            global_selection_revision=(
                "global_single_order_posterior_mean_expected_growth_v1"
            )
        )

        bound, status = riskguard_module._bind_actual_global_capital_evidence(
            conn,
            [self._actual_global_row()],
            strategy_key="forecast_qkernel_entry",
            capital_curve=self._actual_capital_curve(),
        )

        assert status["status"] == "no_verified_winners"
        assert status["capital_law_ready_count"] == 0
        assert status["blocked_reasons"] == {
            "global_selection_revision_mismatch": 1,
        }
        assert bound[0]["decision_law_id"] == "predicted_bin_ev_v1"
        conn.close()

    def test_mismatched_global_winner_receipt_cannot_name_capital_law(self):
        conn = self._actual_global_conn(receipt_candidate_id="other")

        bound, status = riskguard_module._bind_actual_global_capital_evidence(
            conn,
            [self._actual_global_row()],
            strategy_key="forecast_qkernel_entry",
            capital_curve=self._actual_capital_curve(),
        )

        assert status["status"] == "no_verified_winners"
        assert status["capital_law_ready_count"] == 0
        assert status["blocked_reasons"] == {
            "global_certificate_identity_incomplete": 1,
        }
        assert bound[0]["decision_law_id"] == "predicted_bin_ev_v1"
        conn.close()

    def test_day0_actual_global_winner_uses_same_capital_law_binding(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        conn = self._actual_global_conn(strategy_key="day0_nowcast_entry")
        bound, status = riskguard_module._bind_actual_global_capital_evidence(
            conn,
            [
                self._actual_global_row(
                    strategy_key="day0_nowcast_entry",
                    revision=DAY0_PROBABILITY_SEMANTICS_REVISION,
                )
            ],
            strategy_key="day0_nowcast_entry",
            capital_curve=self._actual_capital_curve(),
        )

        assert status["capital_law_ready_count"] == 1
        assert bound[0]["decision_law_id"] == "executable_min_order_capital_gain_v2"
        assert bound[0]["capital_evidence_source"] == "actual_global_winner_fill"
        conn.close()

    def test_missing_executable_benchmark_is_visible_but_non_actuating(self):
        conn = self._conn()
        rows = [self._row("missing", city="NYC", q=0.99, outcome=0)]

        evidence = riskguard_module._qkernel_market_relative_alpha_evidence(
            riskguard_module._bind_entry_market_benchmarks(conn, rows),
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        assert evidence == {
            "status": "no_evidence",
            "rejection_evalue": 10.0,
            "global_selection_revision": (
                riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "window_days": 7.0,
            "evaluated_at": "2026-08-11T00:00:00+00:00",
            "rejected": False,
            "missing_benchmark_count": 1,
            "cohorts": [],
        }
        conn.close()

    def test_current_day0_law_requires_sequential_market_advantage(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        rows = [
            {
                **self._row(
                    f"day0-{index}",
                    city=f"City {index}",
                    q=0.90,
                    outcome=1,
                ),
                "strategy": "day0_nowcast_entry",
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "probability_semantics_revisions": (
                    DAY0_PROBABILITY_SEMANTICS_REVISION,
                ),
                "capital_gain_proof_ready": True,
                "hypothetical_capital_committed_usd": 1.0,
                "hypothetical_realized_pnl_usd": 4.0,
            }
            for index in range(2)
        ]
        conn = self._conn()
        for index, row in enumerate(rows):
            conn.execute(
                "INSERT INTO position_current VALUES (?,?,?,?,?)",
                (
                    row["trade_id"],
                    0.20,
                    row["city"],
                    f"2026-08-{9 + index:02d}",
                    "high",
                ),
            )
            conn.execute(
                "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10','filled',?,1)",
                (row["trade_id"], 0.20),
            )

        evidence = riskguard_module._market_relative_alpha_evidence(
            riskguard_module._bind_entry_market_benchmarks(conn, rows),
            strategy_key="day0_nowcast_entry",
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        assert evidence["status"] == "validated"
        assert evidence["validated"] is True
        assert evidence["rejected"] is False
        assert evidence["cohorts"][0]["independent_cluster_count"] == 2
        assert evidence["cohorts"][0]["model_over_market_evalue"] > 20.0
        assert evidence["cohorts"][0]["hypothetical_realized_pnl_usd"] == 8.0
        assert evidence["cohorts"][0]["capital_gain_validated"] is True
        binding = {
            "status": "ok",
            "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
        }
        assert riskguard_module._market_relative_alpha_gate_reason(
            binding,
            evidence,
            required_evalue=10.0,
        ) is None
        conn.close()

    @staticmethod
    def _live_capital_conn(
        *,
        phase: str,
        gross_pnl: float | None,
        exit_price: float | None,
    ) -> sqlite3.Connection:
        from src.events.day0_authority import bind_day0_probability_semantics

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                strategy_key TEXT,
                decision_law_id TEXT,
                shares REAL,
                cost_basis_usd REAL,
                realized_pnl_usd REAL
            );
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY,
                position_id TEXT,
                intent_kind TEXT,
                q_version TEXT,
                envelope_id TEXT
            );
            CREATE TABLE venue_submission_envelopes (
                envelope_id TEXT PRIMARY KEY,
                post_only INTEGER,
                fee_details_json TEXT
            );
            CREATE TABLE execution_fact (
                command_id TEXT,
                position_id TEXT,
                order_role TEXT,
                filled_at TEXT,
                terminal_exec_status TEXT,
                fill_price REAL,
                shares REAL
            );
            CREATE TABLE position_events (
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                payload_json TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "current-trial",
                phase,
                "Buenos Aires",
                "2026-08-11",
                "high",
                "day0_nowcast_entry",
                "predicted_bin_ev_v1",
                6.24,
                1.56,
                gross_pnl,
            ),
        )
        fee_json = json.dumps({"fee_rate_fraction": 0.05})
        conn.execute(
            "INSERT INTO venue_submission_envelopes VALUES (?,?,?)",
            ("entry-envelope", 0, fee_json),
        )
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?)",
            (
                "entry-command",
                "current-trial",
                "ENTRY",
                bind_day0_probability_semantics("current-trial-q"),
                "entry-envelope",
            ),
        )
        conn.execute(
            "INSERT INTO execution_fact VALUES (?,?,?,?,?,?,?)",
            (
                "entry-command",
                "current-trial",
                "entry",
                "2026-08-11T15:07:43+00:00",
                "filled",
                0.25,
                6.24,
            ),
        )
        if exit_price is not None:
            conn.execute(
                "INSERT INTO venue_submission_envelopes VALUES (?,?,?)",
                ("exit-envelope", 0, fee_json),
            )
            conn.execute(
                "INSERT INTO venue_commands VALUES (?,?,?,?,?)",
                (
                    "exit-command",
                    "current-trial",
                    "EXIT",
                    "",
                    "exit-envelope",
                ),
            )
            conn.execute(
                "INSERT INTO execution_fact VALUES (?,?,?,?,?,?,?)",
                (
                    "exit-command",
                    "current-trial",
                    "exit",
                    "2026-08-11T16:48:04+00:00",
                    "filled",
                    exit_price,
                    6.24,
                ),
            )
            conn.execute(
                "INSERT INTO position_events VALUES (?,?,?,?,?)",
                (
                    "current-trial",
                    2,
                    "EXIT_ORDER_FILLED",
                    "2026-08-11T16:52:07+00:00",
                    json.dumps({"pnl": gross_pnl}),
                ),
            )
        conn.commit()
        return conn

    @staticmethod
    def _validated_day0_shadow_evidence() -> dict:
        from src.events.day0_authority import (
            DAY0_PROBABILITY_SEMANTICS_REVISION,
        )

        return {
            "status": "validated",
            "validated": True,
            "rejected": False,
            "cohorts": [
                {
                    "decision_law_id": "executable_min_order_capital_gain_v2",
                    "global_selection_revision": (
                        riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                    ),
                    "probability_semantics_revisions": [
                        DAY0_PROBABILITY_SEMANTICS_REVISION
                    ],
                    "model_over_market_evalue": 12.0,
                    "independent_cluster_count": 2,
                    "validated": True,
                    "rejected": False,
                }
            ],
        }

    def test_validated_shadow_ignores_unrelated_current_revision_realized_loss(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        conn = self._live_capital_conn(
            phase="economically_closed",
            gross_pnl=-0.06,
            exit_price=0.24,
        )
        curve = riskguard_module._day0_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 11, 17, tzinfo=timezone.utc),
        )

        assert curve["status"] == "nonpositive"
        assert curve["filled_position_count"] == 1
        assert curve["realized_position_count"] == 1
        assert curve["gross_realized_pnl_usd"] == pytest.approx(-0.06)
        assert curve["fee_bound_usd"] == pytest.approx(0.115409)
        assert curve["net_realized_pnl_usd"] == pytest.approx(-0.175409)
        reason = riskguard_module._market_relative_alpha_gate_reason(
            {
                "status": "ok",
                "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
            },
            self._validated_day0_shadow_evidence(),
            required_evalue=10.0,
        )
        assert reason is None
        conn.close()

    def test_confirmed_exit_remains_fee_bearing_realized_capital(self):
        conn = self._live_capital_conn(
            phase="economically_closed",
            gross_pnl=4.68,
            exit_price=0.999,
        )
        conn.execute(
            "UPDATE execution_fact SET terminal_exec_status='CONFIRMED' "
            "WHERE order_role='exit'"
        )
        conn.commit()

        curve = riskguard_module._day0_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 11, 17, tzinfo=timezone.utc),
        )

        assert curve["status"] == "positive"
        assert curve["realized_position_count"] == 1
        assert curve["fee_bound_usd"] == pytest.approx(0.058812)
        assert curve["net_realized_pnl_usd"] == pytest.approx(4.621188)
        conn.close()

    def test_partial_entry_fill_contributes_exact_realized_capital(self):
        from src.events.day0_authority import bind_day0_probability_semantics

        conn = self._live_capital_conn(
            phase="settled",
            gross_pnl=3.62,
            exit_price=None,
        )
        fee_json = json.dumps({"fee_rate_fraction": 0.05})
        conn.execute(
            "INSERT INTO venue_submission_envelopes VALUES (?,?,?)",
            ("partial-envelope", 1, fee_json),
        )
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?)",
            (
                "partial-command",
                "current-trial",
                "ENTRY",
                bind_day0_probability_semantics("partial-current-q"),
                "partial-envelope",
            ),
        )
        conn.execute(
            "INSERT INTO execution_fact VALUES (?,?,?,?,?,?,?)",
            (
                "partial-command",
                "current-trial",
                "entry",
                "2026-08-11T15:30:00+00:00",
                "partial",
                0.53,
                2.0,
            ),
        )
        conn.execute(
            "UPDATE position_current SET cost_basis_usd=2.62,realized_pnl_usd=3.62"
        )
        conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?)",
            (
                "current-trial",
                2,
                "SETTLED",
                "2026-08-11T16:52:07+00:00",
                json.dumps({"pnl": 3.62}),
            ),
        )
        conn.commit()

        curve = riskguard_module._day0_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 11, 17, tzinfo=timezone.utc),
        )

        assert curve["status"] == "positive"
        assert curve["blocked_position_count"] == 0
        assert curve["realized_position_count"] == 1
        assert curve["realized_capital_committed_usd"] == pytest.approx(2.6785)
        assert curve["net_realized_pnl_usd"] == pytest.approx(3.5615)
        conn.close()

    def test_partial_exit_reconciles_original_capital_to_residual_projection(self):
        conn = self._live_capital_conn(
            phase="pending_exit",
            gross_pnl=0.24,
            exit_price=0.30,
        )
        conn.execute(
            "UPDATE execution_fact SET shares=6.0 WHERE order_role='exit'"
        )
        conn.execute(
            "UPDATE position_current SET shares=0.24,cost_basis_usd=0.06"
        )
        conn.commit()

        curve = riskguard_module._day0_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 11, 17, tzinfo=timezone.utc),
        )

        assert curve["blocked_position_count"] == 0
        assert curve["filled_position_count"] == 1
        assert curve["capital_committed_usd"] == pytest.approx(1.6185)
        conn.close()

    def test_settled_dust_after_material_exit_is_not_reported_as_hold_to_settlement(self):
        conn = self._live_capital_conn(
            phase="settled",
            gross_pnl=4.68,
            exit_price=0.93,
        )
        conn.execute(
            "UPDATE execution_fact SET shares=6.23 WHERE order_role='exit'"
        )
        conn.execute(
            "UPDATE position_current SET shares=0.01,cost_basis_usd=0.0025"
        )
        conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?)",
            (
                "current-trial",
                3,
                "SETTLED",
                "2026-08-11T23:00:00+00:00",
                json.dumps({"pnl": 4.68}),
            ),
        )
        conn.commit()

        curve = riskguard_module._day0_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 12, 0, tzinfo=timezone.utc),
        )

        row = curve["curve"][0]
        assert row["close_type"] == "EXIT_ORDER_FILLED_WITH_RESIDUAL_SETTLEMENT"
        assert row["terminal_event_type"] == "SETTLED"
        assert row["entry_filled_shares"] == pytest.approx(6.24)
        assert row["exit_filled_shares"] == pytest.approx(6.23)
        assert row["exit_fill_fraction"] == pytest.approx(0.998397)
        assert row["remaining_after_exit_shares"] == pytest.approx(0.01)
        assert row["first_exit_filled_at"] == "2026-08-11T16:48:04+00:00"
        conn.close()

    def test_validated_shadow_ignores_unresolved_live_fill(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        conn = self._live_capital_conn(
            phase="day0_window",
            gross_pnl=None,
            exit_price=None,
        )
        curve = riskguard_module._day0_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 11, 17, tzinfo=timezone.utc),
        )

        assert curve["status"] == "probation_in_flight"
        assert curve["filled_position_count"] == 1
        assert curve["open_position_count"] == 1
        assert curve["realized_position_count"] == 0
        reason = riskguard_module._market_relative_alpha_gate_reason(
            {
                "status": "ok",
                "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
            },
            self._validated_day0_shadow_evidence(),
            required_evalue=10.0,
        )
        assert reason is None
        conn.close()

    @pytest.mark.parametrize(
        "capital_status",
        ("capital_truth_unavailable", "capital_truth_degraded"),
    )
    def test_validated_shadow_ignores_degraded_capital_curve(
        self,
        capital_status,
    ):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        if capital_status == "capital_truth_unavailable":
            conn = sqlite3.connect(":memory:")
        else:
            conn = self._live_capital_conn(
                phase="day0_window",
                gross_pnl=None,
                exit_price=None,
            )
            conn.execute(
                "UPDATE venue_submission_envelopes SET fee_details_json='{}'"
            )
            conn.commit()
        curve = riskguard_module._day0_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 11, 17, tzinfo=timezone.utc),
        )

        assert curve["status"] == capital_status
        assert riskguard_module._market_relative_alpha_gate_reason(
            {
                "status": "ok",
                "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
            },
            self._validated_day0_shadow_evidence(),
            required_evalue=10.0,
        ) is None
        conn.close()

    def test_qkernel_realized_loss_remains_observability_only(
        self,
        monkeypatch,
    ):
        conn = self._live_capital_conn(
            phase="economically_closed",
            gross_pnl=-0.50,
            exit_price=0.17,
        )
        conn.execute(
            "UPDATE position_current SET strategy_key='forecast_qkernel_entry'"
        )
        conn.execute(
            "UPDATE venue_commands SET q_version='qkernel-current' "
            "WHERE intent_kind='ENTRY'"
        )
        conn.execute(
            "INSERT INTO execution_fact VALUES (?,?,?,?,?,?,?)",
            (
                "entry-command",
                "current-trial",
                "entry",
                "2026-08-11T15:08:03+00:00",
                "filled",
                0.25,
                6.24,
            ),
        )
        conn.commit()

        def classify(rows):
            return (
                [
                    {
                        **row,
                        "probability_semantics_ready": True,
                        "probability_semantics_revisions": (
                            riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
                        ),
                    }
                    for row in rows
                ],
                {"status": "ok", "current_count": len(rows)},
            )

        monkeypatch.setattr(
            riskguard_module,
            "_bind_qkernel_probability_semantics",
            classify,
        )
        curve = riskguard_module._qkernel_live_realized_capital_curve(
            conn,
            window_days=7.0,
            as_of=datetime(2026, 8, 11, 17, tzinfo=timezone.utc),
        )

        assert curve["status"] == "nonpositive"
        assert curve["strategy_key"] == "forecast_qkernel_entry"
        assert curve["filled_position_count"] == 1
        assert curve["blocked_position_count"] == 0
        assert curve["realized_position_count"] == 1
        assert curve["net_realized_pnl_usd"] == pytest.approx(-0.602523)
        conn.close()

    def test_current_revision_capital_proof_is_wired_to_entry_gate(self):
        import inspect

        tick_source = inspect.getsource(riskguard_module._tick_once)

        assert "_qkernel_live_realized_capital_curve(" in tick_source
        assert "_day0_live_realized_capital_curve(" in tick_source
        assert '"qkernel_live_realized_capital_curve":' in tick_source
        assert '"day0_live_realized_capital_curve":' in tick_source
        assert "qkernel_market_relative_alpha_gate_reason" in tick_source
        assert "day0_market_relative_alpha_gate_required" in tick_source
        assert "recommended_strategy_gate_scopes" in tick_source
        assert "live_capital_curve" not in inspect.signature(
            riskguard_module._qkernel_market_relative_alpha_evidence
        ).parameters
        assert "live_capital_curve" not in inspect.signature(
            riskguard_module._market_relative_alpha_gate_reason
        ).parameters
        assert not hasattr(
            riskguard_module,
            "_live_realized_capital_gate_reason",
        )

    def test_same_target_date_high_and_low_count_as_one_evidence_cluster(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        rows = []
        conn = self._conn()
        for trade_id, metric, q in (
            ("same-date-high", "high", 0.90),
            ("same-date-low", "low", 0.95),
        ):
            row = {
                **self._row(trade_id, city="NYC", q=q, outcome=1),
                "strategy": "day0_nowcast_entry",
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "probability_semantics_revisions": (
                    DAY0_PROBABILITY_SEMANTICS_REVISION,
                ),
                "capital_gain_proof_ready": True,
                "hypothetical_capital_committed_usd": 1.0,
                "hypothetical_realized_pnl_usd": 4.0,
            }
            rows.append(row)
            conn.execute(
                "INSERT INTO position_current VALUES (?,?,?,?,?)",
                (trade_id, 0.20, "NYC", "2026-08-10", metric),
            )
            conn.execute(
                "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10','filled',?,1)",
                (trade_id, 0.20),
            )

        evidence = riskguard_module._market_relative_alpha_evidence(
            riskguard_module._bind_entry_market_benchmarks(conn, rows),
            strategy_key="day0_nowcast_entry",
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        cohort = evidence["cohorts"][0]
        assert cohort["candidate_count"] == 2
        assert cohort["independent_cluster_count"] == 1
        assert cohort["model_over_market_evalue"] == pytest.approx(4.75)
        assert evidence["validated"] is False
        conn.close()

    def test_same_target_date_different_cities_are_independent_evidence_clusters(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        rows = []
        conn = self._conn()
        for trade_id, city in (
            ("same-date-nyc", "NYC"),
            ("same-date-tel-aviv", "Tel Aviv"),
        ):
            rows.append(
                {
                    **self._row(trade_id, city=city, q=0.90, outcome=0),
                    "strategy": "day0_nowcast_entry",
                    "decision_law_id": "executable_min_order_capital_gain_v2",
                    "probability_semantics_revisions": (
                        DAY0_PROBABILITY_SEMANTICS_REVISION,
                    ),
                    "capital_gain_proof_ready": True,
                    "hypothetical_capital_committed_usd": 1.0,
                    "hypothetical_realized_pnl_usd": -1.0,
                }
            )
            conn.execute(
                "INSERT INTO position_current VALUES (?,?,?,?,?)",
                (trade_id, 0.20, city, "2026-08-10", "high"),
            )
            conn.execute(
                "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10','filled',?,1)",
                (trade_id, 0.20),
            )

        evidence = riskguard_module._market_relative_alpha_evidence(
            riskguard_module._bind_entry_market_benchmarks(conn, rows),
            strategy_key="day0_nowcast_entry",
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        cohort = evidence["cohorts"][0]
        assert cohort["candidate_count"] == 2
        assert cohort["independent_cluster_count"] == 2
        assert cohort["market_over_model_evalue"] == pytest.approx(64.0)
        assert evidence["rejected"] is True
        conn.close()

    def test_unvalidated_shadow_remains_visible_under_evalue_contract(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        rows = [
            {
                **self._row("small-win", city="Alpha", q=0.90, outcome=1),
                "strategy": "day0_nowcast_entry",
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "probability_semantics_revisions": (
                    DAY0_PROBABILITY_SEMANTICS_REVISION,
                ),
                "capital_gain_proof_ready": True,
                "hypothetical_capital_committed_usd": 0.30,
                "hypothetical_realized_pnl_usd": 4.70,
            },
            {
                **self._row("large-loss", city="Beta", q=0.20, outcome=0),
                "strategy": "day0_nowcast_entry",
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "probability_semantics_revisions": (
                    DAY0_PROBABILITY_SEMANTICS_REVISION,
                ),
                "capital_gain_proof_ready": True,
                "hypothetical_capital_committed_usd": 6.00,
                "hypothetical_realized_pnl_usd": -6.00,
            },
        ]
        conn = self._conn()
        for index, row in enumerate(rows):
            conn.execute(
                "INSERT INTO position_current VALUES (?,?,?,?,?)",
                (
                    row["trade_id"],
                    0.05,
                    row["city"],
                    f"2026-08-{9 + index:02d}",
                    "high",
                ),
            )
            conn.execute(
                "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10','filled',?,1)",
                (row["trade_id"], 0.05),
            )

        evidence = riskguard_module._market_relative_alpha_evidence(
            riskguard_module._bind_entry_market_benchmarks(conn, rows),
            strategy_key="day0_nowcast_entry",
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        cohort = evidence["cohorts"][0]
        assert cohort["model_over_market_evalue"] > 10.0
        assert cohort["hypothetical_realized_pnl_usd"] == pytest.approx(-1.30)
        assert cohort["capital_gain_validated"] is False
        assert evidence["validated"] is False
        assert riskguard_module._market_relative_alpha_gate_reason(
            {
                "status": "ok",
                "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
            },
            evidence,
            required_evalue=10.0,
        ) is not None
        conn.close()

    def test_qkernel_likelihood_win_without_capital_gain_is_not_validated(self):
        rows = [
            {
                **self._row("qkernel-win", city="Alpha", q=0.90, outcome=1),
                "strategy": "forecast_qkernel_entry",
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "probability_semantics_revisions": (
                    riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
                ),
                "capital_gain_proof_ready": True,
                "hypothetical_capital_committed_usd": 0.30,
                "hypothetical_realized_pnl_usd": 4.70,
            },
            {
                **self._row("qkernel-loss", city="Beta", q=0.20, outcome=0),
                "strategy": "forecast_qkernel_entry",
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "probability_semantics_revisions": (
                    riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
                ),
                "capital_gain_proof_ready": True,
                "hypothetical_capital_committed_usd": 6.00,
                "hypothetical_realized_pnl_usd": -6.00,
            },
        ]
        conn = self._conn()
        for index, row in enumerate(rows):
            conn.execute(
                "INSERT INTO position_current VALUES (?,?,?,?,?)",
                (
                    row["trade_id"],
                    0.05,
                    row["city"],
                    f"2026-08-{9 + index:02d}",
                    "high",
                ),
            )
            conn.execute(
                "INSERT INTO execution_fact VALUES (?,'entry','2026-08-10','filled',?,1)",
                (row["trade_id"], 0.05),
            )

        evidence = riskguard_module._market_relative_alpha_evidence(
            riskguard_module._bind_entry_market_benchmarks(conn, rows),
            strategy_key="forecast_qkernel_entry",
            rejection_evalue=10.0,
            as_of=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        cohort = evidence["cohorts"][0]
        assert cohort["model_over_market_evalue"] > 10.0
        assert cohort["hypothetical_realized_pnl_usd"] == pytest.approx(-1.30)
        assert cohort["capital_gain_validated"] is False
        assert evidence["validated"] is False
        conn.close()

    def test_current_day0_without_validated_causal_evidence_is_gated(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        binding = {
            "status": "ok",
            "current_count": 0,
            "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
        }
        evidence = {
            "status": "no_evidence",
            "validated": False,
            "rejected": False,
            "cohorts": [],
        }

        reason = riskguard_module._market_relative_alpha_gate_reason(
            binding,
            evidence,
            required_evalue=10.0,
        )

        assert reason == (
            "market_relative_alpha_unproven("
            "status=no_evidence,model_evalue=0.0,required=10.0,clusters=0,"
            "law=executable_min_order_capital_gain_v2,"
            "selection_revision="
            f"{riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION},"
            f"revision={DAY0_PROBABILITY_SEMANTICS_REVISION})"
        )
        assert riskguard_module._market_relative_alpha_unproven_revisions(
            binding,
            evidence,
        ) == (DAY0_PROBABILITY_SEMANTICS_REVISION,)

    def test_qkernel_alpha_observation_does_not_gate_unproven_revision(self):
        current = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        stale = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )
        binding = {
            "status": "ok",
            "licensed_revisions": [current, stale],
        }
        evidence = {
            "cohorts": [
                {
                    "decision_law_id": "executable_min_order_capital_gain_v2",
                    "global_selection_revision": (
                        riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                    ),
                    "probability_semantics_revisions": [current],
                    "model_over_market_evalue": 12.0,
                    "independent_cluster_count": 3,
                    "validated": True,
                    "rejected": False,
                },
                {
                    "decision_law_id": "executable_min_order_capital_gain_v2",
                    "global_selection_revision": (
                        riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                    ),
                    "probability_semantics_revisions": [stale],
                    "model_over_market_evalue": 2.0,
                    "independent_cluster_count": 1,
                    "validated": False,
                    "rejected": False,
                },
            ]
        }

        reason = riskguard_module._market_relative_alpha_gate_reason(
            binding,
            evidence,
            required_evalue=10.0,
        )

        assert f"revision={stale})" in reason
        assert riskguard_module._market_relative_alpha_unproven_revisions(
            binding,
            evidence,
        ) == (stale,)
        assert riskguard_module._market_relative_alpha_rejection_gate_reason(
            binding,
            evidence,
            required_evalue=10.0,
        ) == (None, ())

    def test_qkernel_alpha_gate_scopes_only_directly_rejected_capital_law(self):
        current = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        stale = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )
        binding = {
            "status": "ok",
            "licensed_revisions": [current, stale],
        }
        evidence = {
            "cohorts": [
                {
                    "decision_law_id": "executable_min_order_capital_gain_v2",
                    "global_selection_revision": (
                        riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                    ),
                    "probability_semantics_revisions": [current],
                    "model_over_market_evalue": 1.0,
                    "independent_cluster_count": 12,
                    "validated": False,
                    "rejected": False,
                },
                {
                    "decision_law_id": "executable_min_order_capital_gain_v2",
                    "global_selection_revision": (
                        riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                    ),
                    "probability_semantics_revisions": [stale],
                    "model_over_market_evalue": 0.05,
                    "independent_cluster_count": 12,
                    "validated": False,
                    "rejected": True,
                },
            ]
        }

        reason, revisions = (
            riskguard_module._market_relative_alpha_rejection_gate_reason(
                binding,
                evidence,
                required_evalue=10.0,
            )
        )

        assert revisions == (stale,)
        assert reason is not None
        assert "status=rejected" in reason
        assert f"revision={stale})" in reason

    def test_superseded_accuracy_cohort_cannot_unlock_capital_gain_law(self):
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

        reason = riskguard_module._market_relative_alpha_gate_reason(
            {
                "status": "ok",
                "current_revision": DAY0_PROBABILITY_SEMANTICS_REVISION,
            },
            {
                "status": "validated",
                "validated": True,
                "rejected": False,
                "cohorts": [
                    {
                        "decision_law_id": "predicted_bin_ev_v1",
                        "model_over_market_evalue": 100.0,
                        "independent_cluster_count": 20,
                        "validated": True,
                        "rejected": False,
                    }
                ],
            },
            required_evalue=10.0,
        )

        assert reason == (
            "market_relative_alpha_unproven("
            "status=no_evidence,model_evalue=0.0,required=10.0,clusters=0,"
            "law=executable_min_order_capital_gain_v2,"
            "selection_revision="
            f"{riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION},"
            f"revision={DAY0_PROBABILITY_SEMANTICS_REVISION})"
        )

    def test_superseded_probability_revision_cannot_unlock_current_law(self):
        current_revision = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        reason = riskguard_module._market_relative_alpha_gate_reason(
            {
                "status": "ok",
                "current_revision": current_revision,
            },
            {
                "status": "validated",
                "validated": True,
                "rejected": False,
                "cohorts": [
                    {
                        "decision_law_id": (
                            "executable_min_order_capital_gain_v2"
                        ),
                        "global_selection_revision": (
                            riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                        ),
                        "probability_semantics_revisions": ["superseded-v1"],
                        "model_over_market_evalue": 100.0,
                        "independent_cluster_count": 30,
                        "validated": True,
                        "rejected": False,
                    }
                ],
            },
            required_evalue=10.0,
        )

        assert reason == (
            "market_relative_alpha_unproven("
            "status=no_evidence,model_evalue=0.0,required=10.0,clusters=0,"
            "law=executable_min_order_capital_gain_v2,"
            "selection_revision="
            f"{riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION},"
            f"revision={current_revision})"
        )

    def test_superseded_global_selector_cannot_unlock_current_law(self):
        current_revision = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        evidence = {
            "status": "validated",
            "validated": True,
            "rejected": False,
            "cohorts": [
                {
                    "decision_law_id": "executable_min_order_capital_gain_v2",
                    "global_selection_revision": (
                        "global_single_order_posterior_mean_expected_growth_v1"
                    ),
                    "probability_semantics_revisions": [current_revision],
                    "model_over_market_evalue": 100.0,
                    "independent_cluster_count": 30,
                    "validated": True,
                    "rejected": False,
                }
            ],
        }

        reason = riskguard_module._market_relative_alpha_gate_reason(
            {"status": "ok", "current_revision": current_revision},
            evidence,
            required_evalue=10.0,
        )

        assert reason is not None
        assert "status=no_evidence" in reason
        assert (
            f"selection_revision="
            f"{riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION}"
        ) in reason

    def test_day0_shadow_joins_only_later_verified_exact_condition(self, tmp_path):
        from src.events.day0_authority import (
            DAY0_PROBABILITY_SEMANTICS_REVISION,
            bind_day0_probability_semantics,
        )
        from src.state.schema.no_trade_regret_events_schema import ensure_table
        from src.strategy.live_inference.no_trade_regret import (
            NoTradeRegretEvent,
            NoTradeRegretLedger,
        )

        decision_at = datetime(2026, 8, 10, 16, tzinfo=timezone.utc)
        q_version = bind_day0_probability_semantics("q-shadow")
        envelope = {
            "schema_version": 3,
            "strategy_key": "day0_nowcast_entry",
            "decision_law_id": "executable_min_order_capital_gain_v2",
            "global_selection_revision": (
                riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "probability_semantics_revision": (
                DAY0_PROBABILITY_SEMANTICS_REVISION
            ),
            "selection_rule": (
                "earliest_complete_global_cut_exact_global_posterior_mean_"
                "expected_growth_winner_v3"
            ),
            "selection_epoch_identity": "selection",
            "selection_cut_at_utc": decision_at.isoformat(),
            "decision_at_utc": decision_at.isoformat(),
            "family_key": "family",
            "city": "Helsinki",
            "target_date": "2026-08-10",
            "metric": "high",
            "bin_id": "23C",
            "condition_id": "condition-23c",
            "side": "YES",
            "token_id": "token-yes",
            "q": 0.90,
            "q_version": q_version,
            "probability_witness_identity": "witness",
            "probability_content_identity": "content",
            "posterior_identity_hash": "posterior",
            "source_truth_identity": "source",
            "resolution_identity": "resolution",
            "topology_identity": "topology",
            "band_alpha": 0.05,
            "band_basis": "current-day0",
            "probability_captured_at_utc": decision_at.isoformat(),
            "book_epoch_identity": "book-epoch",
            "book_snapshot_id": "book-snapshot",
            "book_hash": "book-hash",
            "book_captured_at_utc": decision_at.isoformat(),
            "min_order_size": "5",
            "raw_min_order_vwap": 0.20,
            "fee_adjusted_min_order_cost": 0.21,
            "expected_net_edge_per_share": 0.69,
            "global_proof_winner": True,
            "global_proof_candidate_id": "candidate-global-winner",
            "global_proof_execution_mode": "TAKER_LIMIT",
            "global_proof_shares": "5",
            "global_proof_cost_usd": "1.05",
            "global_proof_expected_delta_log_wealth": 0.01,
            "global_proof_expected_ev_usd": 3.95,
        }
        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        NoTradeRegretLedger(conn).insert_idempotent(
            NoTradeRegretEvent(
                event_id=(
                    "market-relative-alpha-shadow-v5-global-selection:"
                    "day0_nowcast_entry:"
                    f"{riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION}:"
                    f"{DAY0_PROBABILITY_SEMANTICS_REVISION}:"
                    "2026-08-10"
                ),
                rejection_stage="RISK_GUARD",
                rejection_reason=(
                    "MARKET_RELATIVE_ALPHA_SHADOW:day0_nowcast_entry"
                ),
                regret_bucket="RISK_CAP",
                condition_id="condition-23c",
                token_id="token-yes",
                outcome_label="23C",
                decision_time=decision_at.isoformat(),
                city="Helsinki",
                target_date="2026-08-10",
                metric="high",
                family_id="family",
                bin_label="23C",
                direction="buy_yes",
                q_live=0.90,
                c_fee_adjusted=0.21,
                p_fill_lcb=1.0,
                native_quote_available=True,
                source_status="current_day0_probability_authority",
                family_complete=True,
                hypothetical_order_type="MARKETABLE_LIMIT",
                hypothetical_fill_status="EXECUTABLE_AT_DECISION",
                hypothetical_fill_price=0.20,
                causal_snapshot_id="witness",
                executable_snapshot_id="book-snapshot",
                envelope_json=json.dumps(envelope, sort_keys=True),
            )
        )
        conn.execute(
            "UPDATE no_trade_regret_events SET created_at=?",
            ((decision_at + timedelta(minutes=1)).isoformat(),),
        )
        conn.commit()

        forecasts_path = tmp_path / "forecasts.db"
        forecasts = sqlite3.connect(forecasts_path)
        forecasts.executescript(
            """
            CREATE TABLE market_events (
                condition_id TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                outcome TEXT
            );
            CREATE TABLE settlement_outcomes (
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                settled_at TEXT,
                authority TEXT
            );
            """
        )
        forecasts.execute(
            "INSERT INTO market_events VALUES (?,?,?,?,?)",
            (
                "condition-23c",
                "Helsinki",
                "2026-08-10",
                "high",
                "YES",
            ),
        )
        forecasts.execute(
            "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?)",
            (
                "Helsinki",
                "2026-08-10",
                "high",
                "2026-08-11T10:00:00+00:00",
                "VERIFIED",
            ),
        )
        forecasts.commit()
        forecasts.close()

        rows, status = (
            riskguard_module._settled_day0_market_relative_alpha_shadow_rows(
                conn,
                window_days=7.0,
                as_of=datetime(2026, 8, 12, tzinfo=timezone.utc),
                forecasts_connection_factory=lambda: sqlite3.connect(
                    forecasts_path
                ),
            )
        )

        assert status["status"] == "ok"
        assert status["settlement_ready_count"] == 1
        assert rows == [
            {
                "trade_id": conn.execute(
                    "SELECT regret_event_id FROM no_trade_regret_events"
                ).fetchone()[0],
                "strategy": "day0_nowcast_entry",
                "probability_semantics_ready": True,
                "probability_semantics_revisions": (
                    DAY0_PROBABILITY_SEMANTICS_REVISION,
                ),
                "decision_law_id": "executable_min_order_capital_gain_v2",
                "global_selection_revision": (
                    riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                ),
                "settled_at": "2026-08-11T10:00:00+00:00",
                "entry_market_benchmark_ready": True,
                "entry_market_benchmark": 0.20,
                "entry_market_benchmark_family": (
                    "Helsinki",
                    "2026-08-10",
                    "high",
                ),
                "p_posterior": 0.90,
                "outcome": 1,
                "capital_gain_proof_ready": True,
                "hypothetical_min_order_size": 5.0,
                "hypothetical_capital_committed_usd": 1.05,
                "hypothetical_settlement_payout_usd": 5.0,
                "hypothetical_realized_pnl_usd": 3.95,
                "evidence_source": (
                    "no_trade_regret_events_day0_shadow_v3"
                ),
            }
        ]
        envelope["global_selection_revision"] = (
            "global_single_order_posterior_mean_expected_growth_v1"
        )
        conn.execute(
            "UPDATE no_trade_regret_events SET envelope_json=?",
            (json.dumps(envelope, sort_keys=True),),
        )
        conn.commit()
        superseded_rows, superseded_status = (
            riskguard_module._settled_day0_market_relative_alpha_shadow_rows(
                conn,
                window_days=7.0,
                as_of=datetime(2026, 8, 12, tzinfo=timezone.utc),
                forecasts_connection_factory=lambda: sqlite3.connect(
                    forecasts_path
                ),
            )
        )
        assert superseded_rows == []
        assert superseded_status["blocked_reasons"] == {
            "global_selection_revision_mismatch": 1,
        }
        conn.close()

    @pytest.mark.parametrize(
        ("revision", "shape_lag_hours", "stale_shape_reused", "expected_ready"),
        (
            (
                riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
                0.0,
                False,
                True,
            ),
            (
                riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
                6.0,
                True,
                False,
            ),
        ),
    )
    def test_qkernel_shadow_requires_current_semantics_and_verified_settlement(
        self,
        tmp_path,
        revision,
        shape_lag_hours,
        stale_shape_reused,
        expected_ready,
    ):
        from src.state.schema.no_trade_regret_events_schema import ensure_table
        from src.strategy.live_inference.no_trade_regret import (
            NoTradeRegretEvent,
            NoTradeRegretLedger,
        )

        decision_at = datetime(2026, 8, 10, 16, tzinfo=timezone.utc)
        envelope = {
            "schema_version": 3,
            "strategy_key": "forecast_qkernel_entry",
            "decision_law_id": "executable_min_order_capital_gain_v2",
            "global_selection_revision": (
                riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
            ),
            "probability_semantics_revision": revision,
            "selection_rule": (
                "earliest_complete_global_cut_exact_global_posterior_mean_"
                "expected_growth_winner_v3"
            ),
            "selection_epoch_identity": "selection",
            "selection_cut_at_utc": decision_at.isoformat(),
            "decision_at_utc": decision_at.isoformat(),
            "family_key": "family",
            "city": "Helsinki",
            "target_date": "2026-08-10",
            "metric": "high",
            "bin_id": "23C",
            "condition_id": "condition-23c",
            "side": "YES",
            "token_id": "token-yes",
            "q": 0.90,
            "q_version": "joint-q-current",
            "probability_witness_identity": "witness",
            "probability_content_identity": "content",
            "posterior_identity_hash": "posterior-current",
            "source_truth_identity": "source",
            "resolution_identity": "resolution",
            "topology_identity": "topology",
            "band_alpha": 0.05,
            "band_basis": "current-qkernel",
            "probability_captured_at_utc": decision_at.isoformat(),
            "book_epoch_identity": "book-epoch",
            "book_snapshot_id": "book-snapshot",
            "book_hash": "book-hash",
            "book_captured_at_utc": decision_at.isoformat(),
            "min_order_size": "5",
            "raw_min_order_vwap": 0.20,
            "fee_adjusted_min_order_cost": 0.21,
            "expected_net_edge_per_share": 0.69,
            "global_proof_winner": True,
            "global_proof_candidate_id": "candidate-global-winner",
            "global_proof_execution_mode": "TAKER_LIMIT",
            "global_proof_shares": "5",
            "global_proof_cost_usd": "1.05",
            "global_proof_expected_delta_log_wealth": 0.01,
            "global_proof_expected_ev_usd": 3.95,
        }
        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        NoTradeRegretLedger(conn).insert_idempotent(
            NoTradeRegretEvent(
                event_id=(
                    "market-relative-alpha-shadow-v5-global-selection:"
                    "forecast_qkernel_entry:"
                    f"{riskguard_module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION}:"
                    f"{revision}:2026-08-10"
                ),
                rejection_stage="RISK_GUARD",
                rejection_reason=(
                    "MARKET_RELATIVE_ALPHA_SHADOW:forecast_qkernel_entry"
                ),
                regret_bucket="RISK_CAP",
                condition_id="condition-23c",
                token_id="token-yes",
                outcome_label="23C",
                decision_time=decision_at.isoformat(),
                city="Helsinki",
                target_date="2026-08-10",
                metric="high",
                family_id="family",
                bin_label="23C",
                direction="buy_yes",
                q_live=0.90,
                c_fee_adjusted=0.21,
                p_fill_lcb=1.0,
                native_quote_available=True,
                source_status="current_qkernel_probability_authority",
                family_complete=True,
                hypothetical_order_type="MARKETABLE_LIMIT",
                hypothetical_fill_status="EXECUTABLE_AT_DECISION",
                hypothetical_fill_price=0.20,
                causal_snapshot_id="witness",
                executable_snapshot_id="book-snapshot",
                envelope_json=json.dumps(envelope, sort_keys=True),
            )
        )
        conn.execute(
            "UPDATE no_trade_regret_events SET created_at=?",
            ((decision_at + timedelta(minutes=1)).isoformat(),),
        )
        conn.commit()

        forecasts_path = tmp_path / "forecasts-qkernel.db"
        forecasts = sqlite3.connect(forecasts_path)
        forecasts.executescript(
            """
            CREATE TABLE market_events (
                condition_id TEXT, city TEXT, target_date TEXT,
                temperature_metric TEXT, outcome TEXT
            );
            CREATE TABLE settlement_outcomes (
                city TEXT, target_date TEXT, temperature_metric TEXT,
                settled_at TEXT, authority TEXT
            );
            CREATE TABLE forecast_posteriors (
                posterior_identity_hash TEXT PRIMARY KEY,
                provenance_json TEXT
            );
            """
        )
        forecasts.execute(
            "INSERT INTO market_events VALUES (?,?,?,?,?)",
            ("condition-23c", "Helsinki", "2026-08-10", "high", "YES"),
        )
        forecasts.execute(
            "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?)",
            (
                "Helsinki",
                "2026-08-10",
                "high",
                "2026-08-11T10:00:00+00:00",
                "VERIFIED",
            ),
        )
        forecasts.execute(
            "INSERT INTO forecast_posteriors VALUES (?,?)",
            (
                "posterior-current",
                json.dumps(
                    {
                        "bayes_precision_fusion": {
                            "current_evidence_shape": {
                                "semantics_revision": revision,
                                "translation_applied": False,
                                "shape_lag_hours": shape_lag_hours,
                                "stale_shape_reused": stale_shape_reused,
                            }
                        }
                    }
                ),
            ),
        )
        forecasts.commit()
        forecasts.close()

        rows, status = (
            riskguard_module._settled_qkernel_market_relative_alpha_shadow_rows(
                conn,
                window_days=7.0,
                as_of=datetime(2026, 8, 12, tzinfo=timezone.utc),
                forecasts_connection_factory=lambda: sqlite3.connect(
                    forecasts_path
                ),
            )
        )

        if not expected_ready:
            assert rows == []
            assert status["certificate_ready_count"] == 0
            assert status["blocked_reasons"] == {
                "certificate_identity_mismatch": 1
            }
            conn.close()
            return

        assert status["status"] == "ok"
        assert status["settlement_ready_count"] == 1
        assert rows[0]["strategy"] == "forecast_qkernel_entry"
        assert rows[0]["probability_semantics_revisions"] == (revision,)
        assert rows[0]["hypothetical_realized_pnl_usd"] == pytest.approx(3.95)
        assert (
            rows[0]["evidence_source"]
            == "no_trade_regret_events_qkernel_shadow_v3"
        )

        forecasts = sqlite3.connect(forecasts_path)
        forecasts.execute(
            "UPDATE forecast_posteriors SET provenance_json=? "
            "WHERE posterior_identity_hash=?",
            (
                json.dumps(
                    {
                        "bayes_precision_fusion": {
                            "current_evidence_shape": {
                                "semantics_revision": "superseded",
                                "translation_applied": False,
                                "shape_lag_hours": 0.0,
                                "stale_shape_reused": False,
                            }
                        }
                    }
                ),
                "posterior-current",
            ),
        )
        forecasts.commit()
        forecasts.close()

        stale_rows, stale_status = (
            riskguard_module._settled_qkernel_market_relative_alpha_shadow_rows(
                conn,
                window_days=7.0,
                as_of=datetime(2026, 8, 12, tzinfo=timezone.utc),
                forecasts_connection_factory=lambda: sqlite3.connect(
                    forecasts_path
                ),
            )
        )
        assert stale_rows == []
        assert stale_status["blocked_reasons"] == {
            "probability_semantics_not_current": 1
        }
        conn.close()

    def test_tick_gates_current_revision_without_mixing_legacy_selector(
        self,
        monkeypatch,
        tmp_path,
    ):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = [
            _settlement_row(
                trade_id="helsinki-yes",
                strategy="forecast_qkernel_entry",
                p_posterior=0.3720459264,
                outcome=0,
            ),
            _settlement_row(
                trade_id="helsinki-no",
                strategy="forecast_qkernel_entry",
                p_posterior=0.9998639330,
                outcome=1,
            ),
            _settlement_row(
                trade_id="guangzhou",
                strategy="forecast_qkernel_entry",
                p_posterior=0.4484491333,
                outcome=0,
            ),
            _settlement_row(
                trade_id="tel-aviv",
                strategy="forecast_qkernel_entry",
                p_posterior=0.9059764849,
                outcome=0,
            ),
        ]
        for row in rows:
            row["probability_semantics_revisions"] = (
                "stale_ensemble_absolute_disagreement_v2",
            )
            row["settled_at"] = datetime.now(timezone.utc).isoformat()

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        conn = get_connection(zeus_db)
        prices = {
            "helsinki-yes": (0.06, "Helsinki", "2026-08-10"),
            "helsinki-no": (0.82, "Helsinki", "2026-08-10"),
            "guangzhou": (0.06, "Guangzhou", "2026-08-10"),
            "tel-aviv": (0.30, "Tel Aviv", "2026-08-09"),
        }
        for trade_id, (price, city, target_date) in prices.items():
            conn.execute(
                "UPDATE position_current "
                "SET entry_price=?,city=?,target_date=?,temperature_metric='high' "
                "WHERE position_id=?",
                (price, city, target_date, trade_id),
            )
            conn.execute(
                "INSERT INTO execution_fact ("
                "intent_id,position_id,order_role,filled_at,fill_price,shares,"
                "terminal_exec_status) VALUES (?,?,?,?,?,?,?)",
                (
                    f"entry-{trade_id}",
                    trade_id,
                    "entry",
                    datetime.now(timezone.utc).isoformat(),
                    price,
                    1.0,
                    "filled",
                ),
            )
        for strategy_key, reason in (
            ("forecast_qkernel_entry", "legacy_alpha_gate"),
            ("day0_nowcast_entry", "legacy_day0_alpha_gate"),
        ):
            conn.execute(
                "INSERT INTO risk_actions ("
                "action_id,strategy_key,action_type,value,issued_at,effective_until,"
                "reason,source,precedence,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"riskguard:gate:{strategy_key}",
                    strategy_key,
                    "gate",
                    "true",
                    "2026-08-11T00:00:00+00:00",
                    None,
                    reason,
                    "riskguard",
                    50,
                    "active",
                ),
            )
        conn.commit()
        conn.close()

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        tracker = SimpleNamespace(
            summary=lambda: {},
            edge_compression_check=lambda: [],
            accounting={},
        )
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(bankroll=211.37),
        )
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda *_, **__: rows,
        )

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level,details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])
        gate_rows = get_connection(zeus_db).execute(
            "SELECT strategy_key,status,reason FROM risk_actions "
            "WHERE action_id IN (?,?) ORDER BY strategy_key",
            (
                "riskguard:gate:forecast_qkernel_entry",
                "riskguard:gate:day0_nowcast_entry",
            ),
        ).fetchall()

        assert level == RiskLevel.GREEN
        assert risk_row["level"] == RiskLevel.GREEN.value
        assert details["market_relative_alpha_evidence"]["status"] == "no_evidence"
        assert details["market_relative_alpha_evidence"]["rejected"] is False
        assert details["market_relative_alpha_admission_role"] == (
            "revision_scoped_pretrade_proof_gate"
        )
        assert details["market_relative_alpha_gate_reason"].startswith(
            "market_relative_alpha_unproven("
        )
        assert details["market_relative_alpha_observation"].startswith(
            "market_relative_alpha_unproven("
        )
        assert details["market_relative_alpha_gate_confirmation"] == {
            "forecast_qkernel_entry": True,
        }
        assert details["day0_market_relative_alpha_admission_role"] == (
            "revision_scoped_pretrade_proof_gate"
        )
        assert details["day0_market_relative_alpha_gate_required"] is False
        assert details["day0_market_relative_alpha_gate_confirmation"] == {}
        gate_state = {
            row["strategy_key"]: (row["status"], row["reason"])
            for row in gate_rows
        }
        assert gate_state["day0_nowcast_entry"] == (
            "expired",
            "legacy_day0_alpha_gate",
        )
        assert gate_state["forecast_qkernel_entry"] == (
            "active",
            details["market_relative_alpha_gate_reason"],
        )


class TestRiskGuardExecutionQualityLocalization:
    """Regression guard (2026-07-05, INV-05): fill-rate is no longer a risk
    input. execution_quality_level is always GREEN, so there is nothing to
    localize — no matter whether the residual fill-rate is healthy or decayed.
    Non-fills / voided maker rests cost $0; a low maker fill-rate is expected
    for a maker-patient strategy, not decay. (These tests previously pinned an
    execution-quality YELLOW that localized to GREEN by excluding durably-gated
    low-fill strategies; that whole apparatus is now inert.)"""

    def _orange_rows(self) -> list[dict]:
        rows = [
            _settlement_row(
                trade_id=f"opening-{i}", strategy="opening_inertia",
                p_posterior=0.58, outcome=0,
                target_date=_independent_target_date(i),
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}", strategy="center_buy",
                p_posterior=0.80, outcome=1,
                target_date=_independent_target_date(45 + i),
            )
            for i in range(5)
        ]
        return rows

    def _exec_summary(self, *, residual_fill_rate_healthy: bool):
        # Buckets honor the production contract terminal_observed ==
        # filled + rejected + voided (_entry_execution_summary).
        # opening_inertia (gated): 8/49 fill dominates the overall.
        # center_buy (non-gated): 3 filled + 5 voided = 8 terminal (0.375,
        # healthy) when residual_fill_rate_healthy, else 1 filled + 12
        # voided = 13 terminal (0.077, also decayed) so localization must
        # NOT fire on the residual.
        center_filled = 3 if residual_fill_rate_healthy else 1
        center_voided = 5 if residual_fill_rate_healthy else 12
        center_terminal = center_filled + center_voided
        overall_filled = 8 + center_filled
        overall_voided = 41 + center_voided
        overall_terminal = overall_filled + overall_voided
        return {
            "overall": {
                "attempted": 55, "filled": overall_filled, "rejected": 0,
                "voided": overall_voided,
                "terminal_observed": overall_terminal,
                "fill_rate": overall_filled / overall_terminal,
            },
            "by_strategy": {
                "opening_inertia": {
                    "attempted": 47, "filled": 8, "rejected": 0, "voided": 41,
                    "terminal_observed": 49, "fill_rate": 8 / 49,
                },
                "center_buy": {
                    "attempted": 8, "filled": center_filled, "rejected": 0,
                    "voided": center_voided,
                    "terminal_observed": center_terminal,
                    "fill_rate": center_filled / center_terminal,
                },
            },
        }

    def _run_tick(self, monkeypatch, tmp_path, *, residual_fill_rate_healthy: bool):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = self._orange_rows()

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)
        monkeypatch.setattr(
            riskguard_module,
            "_entry_execution_summary",
            lambda *_, **__: self._exec_summary(
                residual_fill_rate_healthy=residual_fill_rate_healthy
            ),
        )
        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return level, json.loads(risk_row["details_json"])

    def test_low_fill_rate_does_not_raise_execution_quality(
        self, monkeypatch, tmp_path,
    ):
        # fill-rate never raises execution_quality_level (INV-05): it is GREEN
        # regardless, so there is nothing to localize and no tighten_risk to
        # recommend. Admission stays GREEN via the Brier ORANGE localization.
        level, details = self._run_tick(
            monkeypatch, tmp_path, residual_fill_rate_healthy=True,
        )
        assert details["execution_quality_level"] == "GREEN"
        assert details["brier_strategy_localization"].get("execution_quality_localized") is None
        assert "tighten_risk" not in details.get("recommended_controls", [])
        assert level == RiskLevel.GREEN

    def test_decayed_residual_fill_rate_no_longer_forces_yellow(
        self, monkeypatch, tmp_path,
    ):
        # Before 2026-07-05 a decayed RESIDUAL fill-rate whose durable gate did
        # not confirm kept the portfolio YELLOW. Now fill-rate is not a risk
        # input at all: execution_quality stays GREEN and admission is not
        # frozen, so the confirmed-gate localization dance is moot.
        real_confirm = riskguard_module._confirm_active_durable_strategy_gates

        def _confirm_without_center_buy(conn, strategies):
            out = real_confirm(conn, strategies)
            if "center_buy" in out:
                out["center_buy"] = False
            return out

        monkeypatch.setattr(
            riskguard_module,
            "_confirm_active_durable_strategy_gates",
            _confirm_without_center_buy,
        )
        level, details = self._run_tick(
            monkeypatch, tmp_path, residual_fill_rate_healthy=False,
        )
        assert details["execution_quality_level"] == "GREEN"
        assert details["brier_strategy_localization"].get("execution_quality_localized") is None
        assert "tighten_risk" not in details.get("recommended_controls", [])


class TestStrategyPolicyResolver:
    def test_riskguard_emits_probability_scoped_brier_gate(self):
        conn = _policy_conn()
        revision = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )

        status = riskguard_module._sync_riskguard_strategy_gate_actions(
            conn,
            {
                "forecast_qkernel_entry": [
                    "brier_degraded(level=YELLOW,n=16,brier=0.270822)"
                ]
            },
            probability_semantics_scopes={
                "forecast_qkernel_entry": {revision}
            },
            issued_at="2026-08-13T01:29:58+00:00",
        )
        row = conn.execute(
            "SELECT value FROM risk_actions WHERE action_id = ?",
            ("riskguard:gate:forecast_qkernel_entry",),
        ).fetchone()

        assert status["emitted_count"] == 1
        assert json.loads(row["value"]) == {
            "gate": True,
            "probability_semantics_revisions": [revision],
        }

        riskguard_module._sync_riskguard_strategy_gate_actions(
            conn,
            {
                "forecast_qkernel_entry": [
                    "brier_degraded(level=YELLOW,n=16,brier=0.270822)",
                    "loss_streak=3",
                ]
            },
            probability_semantics_scopes={
                "forecast_qkernel_entry": {revision}
            },
            issued_at="2026-08-13T01:30:58+00:00",
        )
        row = conn.execute(
            "SELECT value FROM risk_actions WHERE action_id = ?",
            ("riskguard:gate:forecast_qkernel_entry",),
        ).fetchone()
        assert row["value"] == "true"
        conn.close()

    def test_riskguard_emits_probability_scoped_unproven_alpha_gate(self):
        conn = _policy_conn()
        revision = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION

        status = riskguard_module._sync_riskguard_strategy_gate_actions(
            conn,
            {
                "forecast_qkernel_entry": [
                    "market_relative_alpha_unproven("
                    "status=no_evidence,model_evalue=0.0,required=10.0,"
                    "clusters=0,law=executable_min_order_capital_gain_v2,"
                    f"revision={revision})"
                ]
            },
            probability_semantics_scopes={
                "forecast_qkernel_entry": {revision}
            },
            issued_at="2026-08-16T19:00:00+00:00",
        )
        row = conn.execute(
            "SELECT value FROM risk_actions WHERE action_id = ?",
            ("riskguard:gate:forecast_qkernel_entry",),
        ).fetchone()

        assert status["emitted_count"] == 1
        assert json.loads(row["value"]) == {
            "gate": True,
            "probability_semantics_revisions": [revision],
        }
        conn.close()

    def test_riskguard_emits_multi_revision_scoped_unproven_alpha_gate(self):
        conn = _policy_conn()
        revisions = {
            riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION,
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
        }

        status = riskguard_module._sync_riskguard_strategy_gate_actions(
            conn,
            {
                "forecast_qkernel_entry": [
                    "brier_degraded(level=RED,brier=0.35,sample=31)",
                    "market_relative_alpha_unproven("
                    "status=no_evidence,model_evalue=0.0,required=10.0,"
                    "clusters=0,law=executable_min_order_capital_gain_v2,"
                    f"revision={','.join(sorted(revisions))})",
                ]
            },
            probability_semantics_scopes={
                "forecast_qkernel_entry": revisions,
            },
            issued_at="2026-08-17T00:17:00+00:00",
        )
        row = conn.execute(
            "SELECT value FROM risk_actions WHERE action_id = ?",
            ("riskguard:gate:forecast_qkernel_entry",),
        ).fetchone()

        assert status["emitted_count"] == 1
        assert json.loads(row["value"]) == {
            "gate": True,
            "probability_semantics_revisions": sorted(revisions),
        }
        conn.close()

    def test_unbound_alpha_reason_cannot_inherit_unrelated_brier_scope(self):
        conn = _policy_conn()
        stale_revision = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )

        riskguard_module._sync_riskguard_strategy_gate_actions(
            conn,
            {
                "forecast_qkernel_entry": [
                    "brier_degraded(level=RED,brier=0.35,sample=31)",
                    "market_relative_alpha_unproven("
                    "status=no_evidence,model_evalue=0.0,required=10.0,"
                    "clusters=0,law=executable_min_order_capital_gain_v2,"
                    "revision=None)",
                ]
            },
            probability_semantics_scopes={
                "forecast_qkernel_entry": {stale_revision}
            },
            issued_at="2026-08-16T19:36:56+00:00",
        )
        row = conn.execute(
            "SELECT value FROM risk_actions WHERE action_id = ?",
            ("riskguard:gate:forecast_qkernel_entry",),
        ).fetchone()

        assert row["value"] == "true"
        conn.close()

    def test_resolve_strategy_policy_defaults_without_rows(self, monkeypatch):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)

        policy = policy_module.resolve_strategy_policy(conn, "center_buy", now)

        assert policy.strategy_key == "center_buy"
        assert policy.gated is False
        assert policy.allocation_multiplier == pytest.approx(1.0)
        assert policy.threshold_multiplier == pytest.approx(1.0)
        assert policy.exit_only is False
        assert policy.sources == []
        conn.close()

    def test_resolve_strategy_policy_defaults_identity_for_every_strategy_key(
        self, monkeypatch,
    ):
        """One-kappa pin: with no operator rows, strategy_key alone must not
        change admission or size for ANY strategy. threshold_multiplier and
        allocation_multiplier must be identity (1.0) and gated/exit_only must
        be False across the whole canonical registry, not just one key."""
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)

        assert CANONICAL_STRATEGY_KEYS, "canonical strategy registry must be non-empty"
        for strategy_key in sorted(CANONICAL_STRATEGY_KEYS):
            policy = policy_module.resolve_strategy_policy(conn, strategy_key, now)
            assert policy.gated is False, strategy_key
            assert policy.exit_only is False, strategy_key
            assert policy.allocation_multiplier == pytest.approx(1.0), strategy_key
            assert policy.threshold_multiplier == pytest.approx(1.0), strategy_key
            assert policy.sources == [], strategy_key
        conn.close()

    def test_resolve_strategy_policy_gates_only_one_strategy(self, monkeypatch):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        _insert_risk_action(
            conn,
            action_id="ra-gate-center",
            strategy_key="center_buy",
            action_type="gate",
            value="true",
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )

        center_buy = policy_module.resolve_strategy_policy(conn, "center_buy", now)
        opening_inertia = policy_module.resolve_strategy_policy(conn, "opening_inertia", now)

        assert center_buy.gated is True
        assert "risk_action:gate" in center_buy.sources
        assert opening_inertia.gated is False
        conn.close()

    def test_probability_scoped_risk_gate_blocks_only_matching_revision(
        self, monkeypatch,
    ):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        old_revision = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )
        current_revision = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        _insert_risk_action(
            conn,
            action_id="ra-gate-old-q",
            strategy_key="forecast_qkernel_entry",
            action_type="gate",
            value=json.dumps(
                {
                    "gate": True,
                    "probability_semantics_revisions": [old_revision],
                }
            ),
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )

        old = policy_module.resolve_strategy_policy(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=old_revision,
        )
        current = policy_module.resolve_strategy_policy(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=current_revision,
        )
        unknown = policy_module.resolve_strategy_policy(
            conn,
            "forecast_qkernel_entry",
            now,
        )

        assert old.gated is True
        assert current.gated is False
        assert unknown.gated is True
        assert policy_module.active_probability_revision_capital_gate_action_ids(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=old_revision,
        ) == ("ra-gate-old-q",)
        assert policy_module.active_probability_revision_capital_gate_action_ids(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=current_revision,
        ) == ()
        assert policy_module.active_probability_revision_capital_gate_action_ids(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=None,
        ) == ("ra-gate-old-q",)
        monkeypatch.setattr(policy_module, "is_entries_paused", lambda: True)
        assert policy_module.active_probability_revision_capital_gate_action_ids(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=old_revision,
        ) == ("ra-gate-old-q",)
        conn.close()

    def test_permissive_manual_gate_cannot_waive_matching_revision_risk_gate(
        self, monkeypatch,
    ):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 8, 14, 12, 30, tzinfo=timezone.utc)
        old_revision = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )
        current_revision = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        _insert_control_override(
            conn,
            override_id="restore-ordinary-entry-eligibility",
            target_type="strategy",
            target_key="forecast_qkernel_entry",
            action_type="gate",
            value="false",
            issued_at=(now - timedelta(minutes=10)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
            precedence=1000,
        )
        _insert_risk_action(
            conn,
            action_id="gate-stale-q-revision",
            strategy_key="forecast_qkernel_entry",
            action_type="gate",
            value=json.dumps(
                {
                    "gate": True,
                    "probability_semantics_revisions": [old_revision],
                }
            ),
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
            precedence=10,
        )

        old = policy_module.resolve_strategy_policy(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=old_revision,
        )
        current = policy_module.resolve_strategy_policy(
            conn,
            "forecast_qkernel_entry",
            now,
            probability_semantics_revision=current_revision,
        )

        assert old.gated is True
        assert old.sources == ["manual_override:gate", "risk_action:gate"]
        assert current.gated is False
        assert current.sources == ["manual_override:gate"]
        conn.close()

    def test_open_rest_revalidates_its_exact_certificate_revision(
        self, monkeypatch, tmp_path,
    ):
        from src.events.reactor import _edli_policy_blocked_open_rest_commands

        _neutralize_hard_safety(monkeypatch)
        now = datetime(2026, 8, 14, 12, 30, tzinfo=timezone.utc)
        old_revision = (
            riskguard_module.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        )
        current_revision = riskguard_module.CURRENT_EVIDENCE_SEMANTICS_REVISION
        trade_path = tmp_path / "zeus_trades.db"
        world_path = tmp_path / "zeus-world.db"
        trade_conn = sqlite3.connect(trade_path)
        trade_conn.row_factory = sqlite3.Row
        trade_conn.executescript(
            """
            CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, position_id TEXT);
            CREATE TABLE position_current (position_id TEXT PRIMARY KEY, strategy_key TEXT);
            CREATE TABLE position_decision_attribution (
                command_id TEXT PRIMARY KEY, decision_certificate_hash TEXT
            );
            CREATE TABLE risk_actions (
                action_id TEXT PRIMARY KEY, strategy_key TEXT, action_type TEXT,
                value TEXT, issued_at TEXT, effective_until TEXT, precedence INTEGER,
                status TEXT
            );
            """
        )
        world_conn = sqlite3.connect(world_path)
        world_conn.executescript(
            """
            CREATE TABLE decision_certificates (
                certificate_hash TEXT PRIMARY KEY, payload_json TEXT
            );
            CREATE TABLE control_overrides (
                override_id TEXT PRIMARY KEY, target_type TEXT, target_key TEXT,
                action_type TEXT, value TEXT, issued_at TEXT, effective_until TEXT,
                precedence INTEGER
            );
            """
        )
        trade_conn.execute("INSERT INTO venue_commands VALUES ('rest-1', 'position-1')")
        trade_conn.execute(
            "INSERT INTO position_current VALUES ('position-1', 'forecast_qkernel_entry')"
        )
        trade_conn.execute(
            "INSERT INTO position_decision_attribution VALUES ('rest-1', 'cert-1')"
        )
        trade_conn.execute(
            "INSERT INTO risk_actions VALUES (?,?,?,?,?,?,?,?)",
            (
                "gate-stale-q-revision",
                "forecast_qkernel_entry",
                "gate",
                json.dumps(
                    {
                        "gate": True,
                        "probability_semantics_revisions": [old_revision],
                    }
                ),
                (now - timedelta(minutes=5)).isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                10,
                "active",
            ),
        )
        world_conn.execute(
            "INSERT INTO decision_certificates VALUES (?, ?)",
            (
                "cert-1",
                json.dumps(
                    {
                        "strategy_key": "forecast_qkernel_entry",
                        "probability_semantics_revision": old_revision,
                    }
                ),
            ),
        )
        world_conn.execute(
            "INSERT INTO control_overrides VALUES (?,?,?,?,?,?,?,?)",
            (
                "restore-ordinary-entry-eligibility",
                "strategy",
                "forecast_qkernel_entry",
                "gate",
                "false",
                (now - timedelta(minutes=10)).isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                1000,
            ),
        )
        trade_conn.commit()
        world_conn.commit()
        world_conn.close()
        trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
        rest = SimpleNamespace(command_id="rest-1")

        blocked = _edli_policy_blocked_open_rest_commands(
            trade_conn,
            [rest],
            decision_time=now,
        )
        assert blocked == {"rest-1": "STRATEGY_POLICY_GATED"}

        trade_conn.execute(
            "UPDATE world.decision_certificates SET payload_json = ? WHERE certificate_hash = ?",
            (
                json.dumps(
                    {
                        "strategy_key": "forecast_qkernel_entry",
                        "probability_semantics_revision": current_revision,
                    }
                ),
                "cert-1",
            ),
        )
        allowed = _edli_policy_blocked_open_rest_commands(
            trade_conn,
            [rest],
            decision_time=now,
        )
        assert allowed == {}
        trade_conn.close()

    def test_resolve_strategy_policy_shrinks_only_one_strategy_allocation(self, monkeypatch):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        _insert_risk_action(
            conn,
            action_id="ra-alloc-center",
            strategy_key="center_buy",
            action_type="allocation_multiplier",
            value="0.4",
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )

        center_buy = policy_module.resolve_strategy_policy(conn, "center_buy", now)
        opening_inertia = policy_module.resolve_strategy_policy(conn, "opening_inertia", now)

        assert center_buy.allocation_multiplier == pytest.approx(0.4)
        assert "risk_action:allocation_multiplier" in center_buy.sources
        assert opening_inertia.allocation_multiplier == pytest.approx(1.0)
        conn.close()

    def test_resolve_strategy_policy_manual_override_wins_over_risk_action(self, monkeypatch):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        _insert_risk_action(
            conn,
            action_id="ra-threshold-center",
            strategy_key="center_buy",
            action_type="threshold_multiplier",
            value="1.8",
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )
        _insert_control_override(
            conn,
            override_id="ov-threshold-center",
            target_type="strategy",
            target_key="center_buy",
            action_type="threshold_multiplier",
            value="1.1",
            issued_at=(now - timedelta(minutes=1)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )

        policy = policy_module.resolve_strategy_policy(conn, "center_buy", now)

        assert policy.threshold_multiplier == pytest.approx(1.1)
        assert "manual_override:threshold_multiplier" in policy.sources
        conn.close()

    def test_trade_control_override_ghost_is_not_strategy_policy_authority(
        self,
        monkeypatch,
        tmp_path,
    ):
        _neutralize_hard_safety(monkeypatch)
        now = datetime(2026, 6, 29, 2, 25, tzinfo=timezone.utc)
        trade_path = tmp_path / "zeus_trades.db"
        world_path = tmp_path / "zeus-world.db"
        trade_conn = _policy_file_conn(trade_path)
        world_conn = _policy_file_conn(world_path)
        _insert_control_override(
            trade_conn,
            override_id="ghost-trade-gate",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )
        trade_conn.commit()
        world_conn.commit()
        world_conn.close()
        trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))

        policy = policy_module.resolve_strategy_policy(trade_conn, "center_buy", now)

        assert policy.gated is False
        assert "manual_override:gate" not in policy.sources
        trade_conn.close()

    def test_strategy_policy_reads_attached_world_control_authority(
        self,
        monkeypatch,
        tmp_path,
    ):
        _neutralize_hard_safety(monkeypatch)
        now = datetime(2026, 6, 29, 2, 30, tzinfo=timezone.utc)
        trade_path = tmp_path / "zeus_trades.db"
        world_path = tmp_path / "zeus-world.db"
        trade_conn = _policy_file_conn(trade_path)
        world_conn = _policy_file_conn(world_path)
        _insert_control_override(
            world_conn,
            override_id="world-center-buy-gate",
            target_type="strategy",
            target_key="center_buy",
            action_type="gate",
            value="true",
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )
        trade_conn.commit()
        world_conn.commit()
        world_conn.close()
        trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))

        policy = policy_module.resolve_strategy_policy(trade_conn, "center_buy", now)

        assert policy.gated is True
        assert "manual_override:gate" in policy.sources
        trade_conn.close()

    def test_resolve_strategy_policy_expired_override_restores_automatic_policy(self, monkeypatch):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        _insert_risk_action(
            conn,
            action_id="ra-threshold-center",
            strategy_key="center_buy",
            action_type="threshold_multiplier",
            value="1.6",
            issued_at=(now - timedelta(minutes=5)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )
        _insert_control_override(
            conn,
            override_id="ov-threshold-expired",
            target_type="strategy",
            target_key="center_buy",
            action_type="threshold_multiplier",
            value="1.1",
            issued_at=(now - timedelta(hours=2)).isoformat(),
            effective_until=(now - timedelta(minutes=1)).isoformat(),
        )

        policy = policy_module.resolve_strategy_policy(conn, "center_buy", now)

        assert policy.threshold_multiplier == pytest.approx(1.6)
        assert "risk_action:threshold_multiplier" in policy.sources
        conn.close()

    def test_resolve_strategy_policy_hard_safety_wins_first(self, monkeypatch):
        monkeypatch.setattr(policy_module, "is_entries_paused", lambda: True)
        monkeypatch.setattr(policy_module, "get_edge_threshold_multiplier", lambda: 2.0)

        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        _insert_control_override(
            conn,
            override_id="ov-threshold-center",
            target_type="strategy",
            target_key="center_buy",
            action_type="threshold_multiplier",
            value="1.1",
            issued_at=(now - timedelta(minutes=1)).isoformat(),
            effective_until=(now + timedelta(hours=1)).isoformat(),
        )

        policy = policy_module.resolve_strategy_policy(conn, "center_buy", now)

        assert policy.gated is True
        assert policy.threshold_multiplier == pytest.approx(2.0)
        assert "hard_safety:pause_entries" in policy.sources
        assert "hard_safety:tighten_risk:2" in policy.sources
        conn.close()

    def test_low_fill_rate_does_not_gate_or_raise_risk(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
        )

        conn = get_connection(zeus_db)
        from src.state.db import init_schema
        init_schema(conn)
        # Insert 10 terminal-but-unfilled canonical events (P9: log_position_event deleted)
        for i in range(10):
            event_type = "ENTRY_ORDER_VOIDED" if i < 8 else "ENTRY_ORDER_REJECTED"
            conn.execute("""
                INSERT INTO position_events
                (event_id, position_id, event_version, sequence_no, event_type,
                 occurred_at, strategy_key, source_module, env, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (f"terminal-{i}:{event_type}:1", f"terminal-{i}", 1, 1,
                   event_type, _recent_iso(minutes=10 - i),
                   "center_buy", "test", "live", '{}'))
        conn.commit()
        conn.close()

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        # Regression guard (2026-07-05, INV-05): a strategy with a low maker
        # fill-rate (0.0 over 10 terminal events, all voided/rejected) must NOT
        # be gated and must NOT raise the risk level. Non-fills cost $0;
        # fill-rate is observability, never a gate or a YELLOW. Before the
        # execution_decay removal this asserted the tick gated center_buy and
        # localized the global YELLOW back to GREEN.
        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["execution_quality_level"] == "GREEN"
        assert "center_buy" not in details["recommended_strategy_gates"]
        assert "center_buy" not in details.get("recommended_strategy_gate_reasons", {})
        assert details["brier_strategy_localization"].get("execution_quality_localized") is None
        assert "tighten_risk" not in details.get("recommended_controls", [])
        assert "tighten_risk" not in details.get("recommended_control_reasons", {})
        # fill_rate is still computed for observability — just never gated on.
        bucket = details["entry_execution_summary"]["by_strategy"]["center_buy"]
        assert bucket["fill_rate"] == 0.0

    def test_tick_records_strategy_edge_compression_without_actuating(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        tracker = strategy_tracker_module.StrategyTracker()
        tracker.edge_compression_check = lambda window_days=30: ["EDGE_COMPRESSION: center_buy edge shrinking"]

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["strategy_signal_level"] == "GREEN"
        assert details["strategy_edge_compression_alerts"] == [
            "EDGE_COMPRESSION: center_buy edge shrinking"
        ]
        assert details["recommended_strategy_gates"] == []
        assert "review_strategy_gates" not in details["recommended_controls"]

    def test_tick_does_not_emit_durable_gate_for_edge_compression(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        tracker = strategy_tracker_module.StrategyTracker()
        tracker.edge_compression_check = lambda window_days=30: ["EDGE_COMPRESSION: center_buy edge shrinking"]

        conn = get_connection(zeus_db)
        _bootstrap_policy_tables(conn)
        conn.commit()
        conn.close()

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        riskguard_module.tick()

        row = get_connection(zeus_db).execute(
            """
            SELECT strategy_key, action_type, value, source, precedence, status, reason
            FROM risk_actions
            WHERE action_id = 'riskguard:gate:center_buy'
            """
        ).fetchone()

        assert row is None
        risk_state_row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_state_row["details_json"])
        assert details["durable_risk_action_emission_status"] == "emitted"
        assert details["durable_risk_action_emitted_count"] == 0
        assert details["durable_risk_action_expired_count"] == 0

    def test_tick_expires_existing_edge_compression_gate_without_duplication(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        tracker = strategy_tracker_module.StrategyTracker()
        tracker.edge_compression_check = lambda window_days=30: ["EDGE_COMPRESSION: center_buy edge shrinking"]

        conn = get_connection(zeus_db)
        _bootstrap_policy_tables(conn)
        _insert_risk_action(
            conn,
            action_id="riskguard:gate:center_buy",
            strategy_key="center_buy",
            action_type="gate",
            value="true",
            issued_at="2026-04-03T16:00:00+00:00",
            effective_until=None,
            precedence=50,
            status="active",
        )
        conn.execute(
            "UPDATE risk_actions SET reason = ? WHERE action_id = ?",
            ("stale_reason", "riskguard:gate:center_buy"),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        riskguard_module.tick()

        conn = get_connection(zeus_db)
        count = conn.execute(
            "SELECT COUNT(*) FROM risk_actions WHERE action_id = 'riskguard:gate:center_buy'"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT status, reason FROM risk_actions WHERE action_id = 'riskguard:gate:center_buy'"
        ).fetchone()
        conn.close()

        assert count == 1
        assert dict(row) == {"status": "expired", "reason": "stale_reason"}

    def test_tick_expires_emitted_risk_action_when_strategy_gate_clears(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        conn = get_connection(zeus_db)
        _bootstrap_policy_tables(conn)
        _insert_risk_action(
            conn,
            action_id="riskguard:gate:center_buy",
            strategy_key="center_buy",
            action_type="gate",
            value="true",
            issued_at="2026-04-03T16:00:00+00:00",
            effective_until=None,
            precedence=50,
            status="active",
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        riskguard_module.tick()

        row = get_connection(zeus_db).execute(
            "SELECT status, effective_until FROM risk_actions WHERE action_id = 'riskguard:gate:center_buy'"
        ).fetchone()

        assert row["status"] == "expired"
        assert row["effective_until"] is not None
        risk_state_row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_state_row["details_json"])
        assert details["durable_risk_action_emission_status"] == "emitted"
        assert details["durable_risk_action_emitted_count"] == 0
        assert details["durable_risk_action_expired_count"] == 1

    def test_tick_records_explicit_skip_when_durable_risk_actions_table_is_missing(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        tracker = strategy_tracker_module.StrategyTracker()
        tracker.edge_compression_check = lambda window_days=30: ["EDGE_COMPRESSION: center_buy edge shrinking"]

        _init_empty_canonical_portfolio_schema(zeus_db, drop_risk_actions=True)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        riskguard_module.tick()

        risk_state_row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_state_row["details_json"])

        assert details["recommended_strategy_gates"] == []
        assert details["durable_risk_action_emission_status"] == "skipped_missing_table"
        assert details["durable_risk_action_emitted_count"] == 0
        assert details["durable_risk_action_expired_count"] == 0

    def test_tick_localizes_yellow_brier_to_durable_strategy_gate(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        rows = [
            _settlement_row(
                trade_id=f"opening-{i}",
                strategy="opening_inertia",
                p_posterior=0.53,
                outcome=0,
                target_date=_independent_target_date(i),
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
                target_date=_independent_target_date(45 + i),
            )
            for i in range(5)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _persist_decision_law_identities(zeus_db, rows)
        from src.runtime import bankroll_provider as _bp

        monkeypatch.setattr(
            _bp,
            "current",
            lambda **_kw: _bp.BankrollOfRecord(
                value_usd=211.37,
                fetched_at="2026-04-01T00:00:00+00:00",
                source="polymarket_wallet",
                authority="canonical",
                staleness_seconds=0.0,
                cached=False,
            ),
        )
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, brier, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])
        gate_row = get_connection(zeus_db).execute(
            """
            SELECT strategy_key, action_type, value, source, status, reason
            FROM risk_actions
            WHERE action_id = 'riskguard:gate:opening_inertia'
            """
        ).fetchone()

        assert level == RiskLevel.GREEN
        assert risk_row["level"] == RiskLevel.GREEN.value
        assert risk_row["brier"] > 0.25
        assert details["portfolio_brier_level"] == "YELLOW"
        assert details["portfolio_brier_raw_level"] == "YELLOW"
        assert details["brier_level"] == "GREEN"
        assert details["brier_strategy_localization"]["status"] == "localized_to_durable_strategy_gates"
        assert details["recommended_strategy_gates"] == ["opening_inertia"]
        assert details["recommended_strategy_gate_reasons"]["opening_inertia"] == [
            "brier_degraded(level=YELLOW,brier=0.2809,sample=45)"
        ]
        assert details["brier_strategy_breakdown"]["by_strategy"]["center_buy"]["level"] == "GREEN"
        assert dict(gate_row) == {
            "strategy_key": "opening_inertia",
            "action_type": "gate",
            "value": "true",
            "source": "riskguard",
            "status": "active",
            "reason": "brier_degraded(level=YELLOW,brier=0.2809,sample=45)",
        }

    def test_tick_keeps_global_yellow_when_brier_strategy_gate_cannot_persist(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        rows = [
            _settlement_row(
                trade_id=f"opening-{i}",
                strategy="opening_inertia",
                p_posterior=0.53,
                outcome=0,
                target_date=_independent_target_date(i),
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
                target_date=_independent_target_date(45 + i),
            )
            for i in range(5)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db, drop_risk_actions=True)
        _persist_decision_law_identities(zeus_db, rows)
        from src.runtime import bankroll_provider as _bp

        monkeypatch.setattr(
            _bp,
            "current",
            lambda **_kw: _bp.BankrollOfRecord(
                value_usd=211.37,
                fetched_at="2026-04-01T00:00:00+00:00",
                source="polymarket_wallet",
                authority="canonical",
                staleness_seconds=0.0,
                cached=False,
            ),
        )
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(riskguard_module, "query_authoritative_settlement_rows", lambda *_, **__: rows)

        level = riskguard_module.tick()
        risk_row = get_connection(risk_db).execute(
            "SELECT level, brier, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_row["details_json"])

        assert level == RiskLevel.YELLOW
        assert risk_row["level"] == RiskLevel.YELLOW.value
        assert risk_row["brier"] > 0.25
        assert details["portfolio_brier_level"] == "YELLOW"
        assert details["portfolio_brier_raw_level"] == "YELLOW"
        assert details["brier_level"] == "YELLOW"
        assert (
            details["brier_strategy_localization"]["status"]
            == "durable_strategy_gate_unavailable_global_yellow"
        )
        assert details["durable_risk_action_emission_status"] == "skipped_missing_table"
        assert details["recommended_strategy_gates"] == ["opening_inertia"]

    def test_tick_records_strategy_tracker_failure_without_actuating(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: (_ for _ in ()).throw(RuntimeError("tracker unavailable")))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["strategy_signal_level"] == "GREEN"
        assert details["strategy_tracker_error"] == "tracker unavailable"
        assert details["recommended_strategy_gates"] == []

    def test_tick_records_degraded_settlement_counts(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [
                {
                    "p_posterior": 0.7,
                    "outcome": 1,
                    "source": "position_events",
                    "authority_level": "durable_event",
                    "is_degraded": False,
                    "learning_snapshot_ready": True,
                    "canonical_payload_complete": True,
                    "metric_ready": True,
                    "probability_identity_ready": True,
                    "entry_q_version": "test-q-version",
                },
                {
                    "p_posterior": None,
                    "outcome": None,
                    "source": "position_events",
                    "authority_level": "durable_event_malformed",
                    "is_degraded": True,
                    "learning_snapshot_ready": False,
                    "canonical_payload_complete": False,
                    "metric_ready": False,
                },
            ],
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["settlement_sample_size"] == 1
        assert details["settlement_degraded_row_count"] == 1
        assert details["settlement_learning_snapshot_ready_count"] == 1
        assert details["settlement_canonical_payload_complete_count"] == 1
        assert details["settlement_metric_ready_count"] == 1
        assert details["settlement_quality_level"] == "YELLOW"
        assert details["settlement_economic_ready_count"] == 1
        assert details["settlement_authority_levels"]["durable_event"] == 1
        assert details["settlement_authority_levels"]["durable_event_malformed"] == 1

    def test_venue_payout_updates_loss_telemetry_without_physical_metric_or_actuation(
        self, monkeypatch, tmp_path
    ):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        settled_at = datetime.now(timezone.utc).isoformat()

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        _patch_riskguard_bankroll(monkeypatch)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(
            riskguard_module,
            "load_portfolio",
            lambda: PortfolioState(bankroll=211.37),
        )
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [
                {
                    "city": "NYC",
                    "range_label": "39-40°F",
                    "target_date": "2026-08-15",
                    "direction": "buy_yes",
                    "exit_reason": "SETTLEMENT",
                    "settled_at": settled_at,
                    "p_posterior": 0.99,
                    "outcome": 1,
                    "pnl": -2.5,
                    "source": "position_events",
                    "strategy": "center_buy",
                    "position_origin": "zeus_decision",
                    "authority_level": "durable_event",
                    "is_degraded": True,
                    "degraded_reason": "missing_payload_fields:settlement_value",
                    "required_missing_fields": [],
                    "learning_snapshot_ready": False,
                    "canonical_payload_complete": False,
                    "metric_ready": False,
                    "probability_identity_ready": False,
                }
            ],
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["settlement_quality_level"] == "GREEN"
        assert details["settlement_economic_ready_count"] == 1
        assert details["settlement_contract_incomplete_count"] == 1
        assert details["settlement_degraded_row_count"] == 0
        assert details["settlement_metric_ready_count"] == 0
        assert details["settlement_sample_size"] == 0
        assert details["brier_actuating_sample_size"] == 0
        assert details["trailing_loss_decision_role"] == "record_only"
        assert details["daily_loss_level"] == "GREEN"
        assert details["weekly_loss_level"] == "GREEN"
        assert details["daily_loss"] == pytest.approx(2.5)
        assert details["weekly_loss"] == pytest.approx(2.5)
        assert details["daily_loss_reference"]["settlement_count"] == 1
        assert details["weekly_loss_reference"]["settlement_count"] == 1
        assert details["daily_loss_reference"]["realized_pnl_window"] == pytest.approx(-2.5)
        assert details["weekly_loss_reference"]["realized_pnl_window"] == pytest.approx(-2.5)

    def test_tick_fails_closed_when_only_malformed_settlement_rows_exist(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [
                {
                    "p_posterior": None,
                    "outcome": None,
                    "source": "position_events",
                    "authority_level": "durable_event_malformed",
                    "is_degraded": True,
                    "learning_snapshot_ready": False,
                    "canonical_payload_complete": False,
                    "metric_ready": False,
                }
            ],
        )

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.RED
        assert row["level"] == RiskLevel.RED.value
        assert details["settlement_quality_level"] == "RED"
        assert details["settlement_economic_ready_count"] == 0
        assert details["settlement_metric_ready_count"] == 0

    # B050 relationship tests — policy resolver must survive duplicate rows.
    # sqlite3.Row has no .get(); duplicate-detection + bad-row logging both
    # previously fabricated AttributeError.  The resolver must keep working
    # (first-in wins) and log the discarded row, never crash the caller.
    def test_resolve_strategy_policy_survives_duplicate_manual_overrides(self, monkeypatch):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        base = (now - timedelta(minutes=5)).isoformat()
        expires = (now + timedelta(hours=1)).isoformat()
        # Two rows with the same action_type → _select_rows must drop one
        # and log the discarded override_id without raising.
        _insert_control_override(
            conn,
            override_id="ov-dup-a",
            target_type="strategy",
            target_key="center_buy",
            action_type="allocation_multiplier",
            value="0.5",
            issued_at=base,
            effective_until=expires,
        )
        _insert_control_override(
            conn,
            override_id="ov-dup-b",
            target_type="strategy",
            target_key="center_buy",
            action_type="allocation_multiplier",
            value="0.3",
            issued_at=base,
            effective_until=expires,
        )

        policy = policy_module.resolve_strategy_policy(conn, "center_buy", now)

        # First-in wins (higher precedence then issued_at then override_id DESC).
        assert policy.allocation_multiplier in (pytest.approx(0.5), pytest.approx(0.3))
        assert "manual_override:allocation_multiplier" in policy.sources
        conn.close()

    def test_resolve_strategy_policy_survives_duplicate_risk_actions(self, monkeypatch):
        _neutralize_hard_safety(monkeypatch)
        conn = _policy_conn()
        now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
        base = (now - timedelta(minutes=5)).isoformat()
        expires = (now + timedelta(hours=1)).isoformat()
        _insert_risk_action(
            conn,
            action_id="ra-dup-a",
            strategy_key="center_buy",
            action_type="threshold_multiplier",
            value="1.5",
            issued_at=base,
            effective_until=expires,
        )
        _insert_risk_action(
            conn,
            action_id="ra-dup-b",
            strategy_key="center_buy",
            action_type="threshold_multiplier",
            value="1.8",
            issued_at=base,
            effective_until=expires,
        )

        policy = policy_module.resolve_strategy_policy(conn, "center_buy", now)

        assert policy.threshold_multiplier in (pytest.approx(1.5), pytest.approx(1.8))
        assert "risk_action:threshold_multiplier" in policy.sources
        conn.close()


def test_refresh_strategy_health_records_rows_from_lawful_surfaces():
    conn = _policy_conn()
    as_of = "2026-04-04T12:00:00+00:00"

    _insert_position_current(
        conn,
        position_id="pos-center",
        strategy_key="center_buy",
        size_usd=25.0,
        shares=10.0,
        cost_basis_usd=20.0,
        last_monitor_market_price=2.5,
    )
    _insert_outcome_fact(
        conn,
        position_id="unverified-outcome-fact",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",
        pnl=99.0,
        outcome=1,
    )
    _append_verified_settlement_event(
        conn,
        position_id="settle-center-1",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",
        pnl=7.5,
        outcome=1,
        sequence_no=1,
    )
    _append_verified_settlement_event(
        conn,
        position_id="settle-center-2",
        strategy_key="center_buy",
        settled_at="2026-03-20T12:00:00+00:00",
        pnl=-2.0,
        outcome=0,
        sequence_no=2,
    )
    for idx in range(2):
        _insert_execution_fact(
            conn,
            intent_id=f"filled-{idx}",
            strategy_key="center_buy",
            terminal_exec_status="filled",
            posted_at="2026-04-02T12:00:00+00:00",
        )
    for idx in range(8):
        _insert_execution_fact(
            conn,
            intent_id=f"rejected-{idx}",
            strategy_key="center_buy",
            terminal_exec_status="rejected",
            posted_at="2026-04-02T12:00:00+00:00",
        )
    _insert_risk_action(
        conn,
        action_id="riskguard:gate:center_buy",
        strategy_key="center_buy",
        action_type="gate",
        value="true",
        issued_at="2026-04-04T11:55:00+00:00",
        effective_until=None,
        precedence=50,
        status="active",
    )
    conn.execute(
        "UPDATE risk_actions SET reason = ? WHERE action_id = ?",
        ("edge_compression|execution_decay(fill_rate=0.2, observed=10)", "riskguard:gate:center_buy"),
    )

    result = refresh_strategy_health(conn, as_of=as_of)
    snapshot = query_strategy_health_snapshot(
        conn,
        now="2026-04-04T12:04:00+00:00",
        max_age_seconds=300,
    )
    row = conn.execute(
        """
        SELECT open_exposure_usd, settled_trades_30d, realized_pnl_30d, unrealized_pnl,
               win_rate_30d, fill_rate_14d, execution_decay_flag, edge_compression_flag
        FROM strategy_health
        WHERE strategy_key = 'center_buy' AND as_of = ?
        """,
        (as_of,),
    ).fetchone()

    assert result["status"] == "refreshed"
    assert result["rows_written"] == 1
    assert row["open_exposure_usd"] == pytest.approx(25.0)
    assert row["settled_trades_30d"] == 2
    assert row["realized_pnl_30d"] == pytest.approx(5.5)
    assert row["unrealized_pnl"] == pytest.approx(5.0)
    assert row["win_rate_30d"] == pytest.approx(0.5)
    assert row["fill_rate_14d"] == pytest.approx(0.2)
    assert row["execution_decay_flag"] == 1
    assert row["edge_compression_flag"] == 1
    assert snapshot["status"] == "fresh"
    assert snapshot["stale_strategy_keys"] == []


def test_refresh_strategy_health_counts_venue_payout_without_physical_value():
    conn = _policy_conn()
    as_of = "2026-04-04T12:00:00+00:00"
    _append_verified_settlement_event(
        conn,
        position_id="venue-resolved-loss",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",
        pnl=-8.0,
        outcome=0,
        sequence_no=1,
        settlement_authority="VENUE_RESOLVED",
        settlement_truth_source="gamma_exact_held_event",
        settlement_source="polymarket_gamma",
        include_settlement_value=False,
    )

    result = refresh_strategy_health(conn, as_of=as_of)
    row = conn.execute(
        """
        SELECT settled_trades_30d, realized_pnl_30d, win_rate_30d
        FROM strategy_health
        WHERE strategy_key = 'center_buy' AND as_of = ?
        """,
        (as_of,),
    ).fetchone()

    assert result["status"] == "refreshed_degraded"
    assert row["settled_trades_30d"] == 1
    assert row["realized_pnl_30d"] == pytest.approx(-8.0)
    assert row["win_rate_30d"] == pytest.approx(0.0)


def test_refresh_strategy_health_reuses_supplied_position_view(monkeypatch):
    conn = _policy_conn()
    as_of = "2026-04-04T12:00:00+00:00"

    def _unexpected_status_query(_conn):
        raise AssertionError("position_current status query should be reused by caller")

    monkeypatch.setattr(
        state_db_module,
        "query_position_current_status_view",
        _unexpected_status_query,
    )

    result = refresh_strategy_health(
        conn,
        as_of=as_of,
        position_view={
            "status": "ok",
            "table": "position_current",
            "positions": [
                {
                    "strategy": "center_buy",
                    "effective_cost_basis_usd": 25.0,
                    "size_usd": 25.0,
                    "unrealized_pnl": 5.0,
                }
            ],
        },
    )
    row = conn.execute(
        """
        SELECT open_exposure_usd, unrealized_pnl
        FROM strategy_health
        WHERE strategy_key = 'center_buy' AND as_of = ?
        """,
        (as_of,),
    ).fetchone()

    assert result["status"] == "refreshed"
    assert result["rows_written"] == 1
    assert row["open_exposure_usd"] == pytest.approx(25.0)
    assert row["unrealized_pnl"] == pytest.approx(5.0)


def test_refresh_strategy_health_omits_noncanonical_execution_strategy_rows():
    conn = _policy_conn()
    as_of = "2026-04-04T12:00:00+00:00"

    _insert_position_current(
        conn,
        position_id="pos-center",
        strategy_key="center_buy",
        size_usd=25.0,
        shares=10.0,
        cost_basis_usd=20.0,
        last_monitor_market_price=2.5,
    )
    _insert_execution_fact(
        conn,
        intent_id="legacy-null-strategy-fill",
        strategy_key=None,  # type: ignore[arg-type]
        terminal_exec_status="filled",
        posted_at="2026-04-02T12:00:00+00:00",
    )

    result = refresh_strategy_health(conn, as_of=as_of)
    rows = conn.execute(
        "SELECT strategy_key, fill_rate_14d FROM strategy_health ORDER BY strategy_key"
    ).fetchall()

    assert result["status"] == "refreshed"
    assert result["omitted_noncanonical_strategy_counts"]["execution_fact"] == 1
    assert [(row["strategy_key"], row["fill_rate_14d"]) for row in rows] == [
        ("center_buy", None)
    ]


def test_refresh_strategy_health_ignores_authorityless_outcome_fact_rows():
    conn = _policy_conn()
    as_of = "2026-04-04T12:00:00+00:00"

    _insert_outcome_fact(
        conn,
        position_id="authorityless-outcome",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",
        pnl=99.0,
        outcome=1,
    )
    _append_verified_settlement_event(
        conn,
        position_id="verified-settlement",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",
        pnl=4.25,
        outcome=1,
        sequence_no=1,
    )

    result = refresh_strategy_health(conn, as_of=as_of)
    row = conn.execute(
        """
        SELECT settled_trades_30d, realized_pnl_30d, win_rate_30d
        FROM strategy_health
        WHERE strategy_key = 'center_buy' AND as_of = ?
        """,
        (as_of,),
    ).fetchone()

    assert result["status"] == "refreshed"
    assert row["settled_trades_30d"] == 1
    assert row["realized_pnl_30d"] == pytest.approx(4.25)
    assert row["win_rate_30d"] == pytest.approx(1.0)


def test_refresh_strategy_health_uses_parsed_settlement_time_basis():
    conn = _policy_conn()
    as_of = "2026-05-03T12:00:00+00:00"
    _append_verified_settlement_event(
        conn,
        position_id="verified-cutoff-settlement",
        strategy_key="center_buy",
        settled_at="2026-04-03T12:00:00+00:00",  # Cluster M.1: ISO 8601 T-separator required by occurred_at CHECK constraint
        pnl=3.5,
        outcome=1,
        sequence_no=1,
    )

    result = refresh_strategy_health(conn, as_of=as_of)
    row = conn.execute(
        """
        SELECT settled_trades_30d, realized_pnl_30d, win_rate_30d
        FROM strategy_health
        WHERE strategy_key = 'center_buy' AND as_of = ?
        """,
        (as_of,),
    ).fetchone()

    assert result["status"] == "refreshed"
    assert row["settled_trades_30d"] == 1
    assert row["realized_pnl_30d"] == pytest.approx(3.5)
    assert row["win_rate_30d"] == pytest.approx(1.0)


def test_refresh_strategy_health_marks_missing_settlement_authority_surface():
    conn = _policy_conn()
    conn.execute("DROP TABLE position_events")

    result = refresh_strategy_health(conn, as_of="2026-04-04T12:00:00+00:00")

    assert result["status"] == "refreshed_empty_degraded"
    assert result["settlement_authority_missing_tables"] == ["position_events", "decision_log"]
    assert result["settlement_degraded_rows"] == 0


def test_refresh_strategy_health_reports_missing_inputs_explicitly():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    result = refresh_strategy_health(conn, as_of="2026-04-04T12:00:00+00:00")
    snapshot = query_strategy_health_snapshot(conn)

    assert result["status"] == "skipped_missing_table"
    assert result["rows_written"] == 0
    assert snapshot["status"] == "missing_table"


def test_refresh_strategy_health_reports_required_input_gap_when_projection_missing():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE strategy_health (strategy_key TEXT, as_of TEXT)")

    result = refresh_strategy_health(conn, as_of="2026-04-04T12:00:00+00:00")

    assert result["status"] == "skipped_missing_inputs"
    assert result["missing_required_tables"] == ["position_current"]
    assert result["omitted_fields"] == [
        "risk_level",
        "brier_30d",
        "edge_trend_30d",
    ]


def test_query_strategy_health_snapshot_reports_stale_rows():
    conn = _policy_conn()
    conn.execute(
        """
        INSERT INTO strategy_health (
            strategy_key, as_of, open_exposure_usd, settled_trades_30d, realized_pnl_30d,
            unrealized_pnl, win_rate_30d, brier_30d, fill_rate_14d, edge_trend_30d,
            risk_level, execution_decay_flag, edge_compression_flag
        ) VALUES ('center_buy', '2026-04-04T11:40:00+00:00', 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, 0, 0)
        """
    )

    snapshot = query_strategy_health_snapshot(
        conn,
        now="2026-04-04T12:00:00+00:00",
        max_age_seconds=300,
    )

    assert snapshot["status"] == "stale"
    assert snapshot["stale_strategy_keys"] == ["center_buy"]


def test_tick_records_strategy_health_refresh_metadata(monkeypatch, tmp_path):
    # P0-A masking-test repoint (architect_memo §6, followup_design §2.1):
    # this test's axis is strategy_health_refresh metadata. Bankroll is now
    # provider-sourced; we monkeypatch the provider explicitly so the test
    # stops enshrining legacy `PortfolioState.bankroll` as a
    # truth source. The PortfolioState patch is kept (without bankroll= kwarg)
    # because the canonical-loader-truth path uses it for non-bankroll fields.
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"

    def _fake_get_connection(path=None, **_kwargs):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    conn = get_connection(zeus_db)
    _bootstrap_policy_tables(conn)
    _insert_position_current(
        conn,
        position_id="pos-center",
        strategy_key="center_buy",
        size_usd=30.0,
        shares=12.0,
        cost_basis_usd=24.0,
        last_monitor_market_price=2.5,
    )
    conn.commit()
    conn.close()

    from src.runtime import bankroll_provider as _bp
    monkeypatch.setattr(
        _bp,
        "current",
        lambda **_kw: _bp.BankrollOfRecord(
            value_usd=211.37,
            fetched_at="2026-04-01T00:00:00+00:00",
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        ),
    )
    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())

    riskguard_module.tick()
    row = get_connection(risk_db).execute(
        "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert details["strategy_health_refresh_status"] == "refreshed"
    assert details["strategy_health_rows_written"] == 1
    assert details["strategy_health_snapshot_status"] == "fresh"
    assert details["strategy_health_stale_strategy_keys"] == []


# ---------------------------------------------------------------------------
# Bug-C regression (2026-07-06): _unprojected_entry_fill_equity_usd deduped
# venue_trade_facts keyed by command_id ALONE (MAX(local_sequence) GROUP BY
# command_id), silently dropping a command's other trade_ids instead of
# collapsing lifecycle revisions of the SAME trade_id.
# ---------------------------------------------------------------------------


def _unprojected_equity_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            intent_kind TEXT NOT NULL,
            side TEXT NOT NULL,
            state TEXT NOT NULL,
            venue_order_id TEXT
        );
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            filled_size TEXT NOT NULL,
            fill_price TEXT NOT NULL,
            observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            venue_timestamp TEXT,
            local_sequence INTEGER NOT NULL
        );
        CREATE TABLE position_lots (
            source_command_id TEXT,
            state TEXT
        );
        CREATE TABLE position_current (
            position_id TEXT,
            order_id TEXT,
            phase TEXT
        );
        """
    )


def test_unprojected_entry_fill_equity_usd_sums_all_distinct_trade_ids():
    """One ENTRY/BUY command (cmd.state='FILLED') with TWO distinct trade_ids:
    trade-8p1 fills 8.1 shares over 3 lifecycle revisions (MATCHED -> MINED ->
    CONFIRMED, local_sequence 1..3 — its own per-trade_id counter); trade-5p0
    fills 5.0 shares over 2 revisions (MATCHED -> CONFIRMED, local_sequence
    1..2 — its own counter). local_sequence is scoped PER trade_id
    (src/state/venue_command_repo.py _coerce_local_sequence,
    where_sql="trade_id = ?"), so the command-wide MAX(local_sequence) is 3,
    contributed only by trade-8p1 — a command_id-only dedup keeps only that
    row and silently drops trade-5p0's fill entirely.

    No position_lots / position_current row is projected for this command,
    so both NOT EXISTS projection-guards pass and the fill counts as
    unprojected entry-fill equity: must be (8.1+5.0)*price, NOT 8.1*price.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _unprojected_equity_schema(conn)
    conn.execute(
        "INSERT INTO venue_commands (command_id, intent_kind, side, state, venue_order_id) "
        "VALUES ('cmd-multi-trade', 'ENTRY', 'BUY', 'FILLED', 'ord-multi-trade')"
    )
    price = "0.37"
    # trade-8p1: 8.1 shares, 3 revisions, local_sequence 1..3 (own per-trade_id counter)
    for seq, state in enumerate(("MATCHED", "MINED", "CONFIRMED"), start=1):
        conn.execute(
            "INSERT INTO venue_trade_facts "
            "(trade_id, command_id, state, filled_size, fill_price, local_sequence) "
            "VALUES (?, 'cmd-multi-trade', ?, '8.1', ?, ?)",
            ("trade-8p1", state, price, seq),
        )
    # trade-5p0: 5.0 shares, 2 revisions, local_sequence 1..2 (own per-trade_id counter)
    for seq, state in enumerate(("MATCHED", "CONFIRMED"), start=1):
        conn.execute(
            "INSERT INTO venue_trade_facts "
            "(trade_id, command_id, state, filled_size, fill_price, local_sequence) "
            "VALUES (?, 'cmd-multi-trade', ?, '5.0', ?, ?)",
            ("trade-5p0", state, price, seq),
        )
    conn.commit()

    result = riskguard_module._unprojected_entry_fill_equity_usd(conn)

    expected = round(8.1 * float(price) + 5.0 * float(price), 2)
    assert result == expected, (
        f"expected (8.1+5.0)*{price}={expected} (sum across both trade_ids), "
        f"got {result!r}. A command_id-only dedup guard silently drops "
        "trade-5p0's fill, under-counting to 8.1*price."
    )


def test_unprojected_entry_fill_equity_usd_single_trade_id_unchanged():
    """A single-trade_id command must still return the same value after the
    fix: one canonical row per (command_id, trade_id) is still exactly one
    row when a command has only one trade_id."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _unprojected_equity_schema(conn)
    conn.execute(
        "INSERT INTO venue_commands (command_id, intent_kind, side, state, venue_order_id) "
        "VALUES ('cmd-single-trade', 'ENTRY', 'BUY', 'FILLED', 'ord-single-trade')"
    )
    for seq, state in enumerate(("MATCHED", "MINED", "CONFIRMED"), start=1):
        conn.execute(
            "INSERT INTO venue_trade_facts "
            "(trade_id, command_id, state, filled_size, fill_price, local_sequence) "
            "VALUES ('trade-only-1', 'cmd-single-trade', ?, '4.2', '0.55', ?)",
            (state, seq),
        )
    conn.commit()

    result = riskguard_module._unprojected_entry_fill_equity_usd(conn)

    assert result == round(4.2 * 0.55, 2)


def test_unprojected_entry_fill_equity_excludes_any_canonical_position_projection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _unprojected_equity_schema(conn)
    conn.execute(
        "INSERT INTO venue_commands "
        "(command_id, position_id, intent_kind, side, state, venue_order_id) "
        "VALUES ('cmd-projected', 'pos-projected', 'ENTRY', 'BUY', 'FILLED', 'ord-entry')"
    )
    conn.execute(
        "INSERT INTO venue_trade_facts "
        "(trade_id, command_id, state, filled_size, fill_price, local_sequence) "
        "VALUES ('trade-projected', 'cmd-projected', 'CONFIRMED', '41', '0.71', 1)"
    )
    conn.execute(
        "INSERT INTO position_current (position_id, order_id, phase) "
        "VALUES ('pos-projected', 'ord-replaced', 'settled')"
    )
    conn.commit()

    assert riskguard_module._unprojected_entry_fill_equity_usd(conn) == 0.0


def test_unprojected_entry_fill_equity_excludes_terminal_lot_projection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _unprojected_equity_schema(conn)
    conn.execute(
        "INSERT INTO venue_commands "
        "(command_id, position_id, intent_kind, side, state, venue_order_id) "
        "VALUES ('cmd-lot', 'pos-lot', 'ENTRY', 'BUY', 'FILLED', 'ord-lot')"
    )
    conn.execute(
        "INSERT INTO venue_trade_facts "
        "(trade_id, command_id, state, filled_size, fill_price, local_sequence) "
        "VALUES ('trade-lot', 'cmd-lot', 'CONFIRMED', '10', '0.50', 1)"
    )
    conn.execute(
        "INSERT INTO position_lots (source_command_id, state) "
        "VALUES ('cmd-lot', 'RELEASED')"
    )
    conn.commit()

    assert riskguard_module._unprojected_entry_fill_equity_usd(conn) == 0.0


def test_storage_capacity_blocks_entry_before_enospc(monkeypatch, tmp_path):
    from src.engine.cycle_runner import _risk_allows_new_entries

    total = 1024**4
    free = 60 * 1024**3
    monkeypatch.setattr(
        riskguard_module,
        "_disk_usage",
        lambda _path: SimpleNamespace(total=total, used=total - free, free=free),
    )

    snapshot = riskguard_module.storage_capacity_snapshot(tmp_path)

    assert snapshot["level"] == RiskLevel.DATA_DEGRADED.value
    assert snapshot["status"] == "LOW_DISK"
    assert snapshot["reason"] == "ENTRY_RESERVE_BREACHED"
    assert snapshot["required_free_bytes"] == int(total * 0.10)
    level = RiskLevel(str(snapshot["level"]))
    assert level == RiskLevel.DATA_DEGRADED
    assert not _risk_allows_new_entries(level)


def test_storage_capacity_read_failure_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        riskguard_module,
        "_disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError("capacity unavailable")),
    )

    snapshot = riskguard_module.storage_capacity_snapshot(tmp_path)

    assert snapshot["level"] == RiskLevel.DATA_DEGRADED.value
    assert snapshot["status"] == "CAPACITY_UNAVAILABLE"


def test_host_power_runway_uses_time_to_preserve_execution_authority():
    snapshot = riskguard_module.host_power_runway_snapshot(
        "Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=1)\t25%; discharging; 0:29 remaining present: true\n"
    )

    assert snapshot["level"] == RiskLevel.ORANGE.value
    assert snapshot["reason"] == "HOST_EXECUTION_RUNWAY_SEVERE"
    assert snapshot["battery_percent"] == 25
    assert snapshot["remaining_minutes"] == 29.0


def test_host_power_runway_red_precedes_forced_low_power_hibernate():
    snapshot = riskguard_module.host_power_runway_snapshot(
        "Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=1)\t4%; discharging; 0:18 remaining present: true\n"
    )

    assert snapshot["level"] == RiskLevel.RED.value
    assert snapshot["reason"] == "HOST_EXECUTION_RUNWAY_CRITICAL"


def test_host_power_runway_resets_on_ac_power():
    snapshot = riskguard_module.host_power_runway_snapshot(
        "Now drawing from 'AC Power'\n"
        " -InternalBattery-0 (id=1)\t4%; charging; 0:12 remaining present: true\n"
    )

    assert snapshot["level"] == RiskLevel.GREEN.value
    assert snapshot["status"] == "AC_POWER"


def test_host_power_runway_unreadable_truth_fails_closed(monkeypatch):
    monkeypatch.setattr(
        riskguard_module,
        "_pmset_battery_status",
        lambda: (_ for _ in ()).throw(OSError("pmset unavailable")),
    )
    monkeypatch.setattr(riskguard_module.sys, "platform", "darwin")

    snapshot = riskguard_module.host_power_runway_snapshot()

    assert snapshot["level"] == RiskLevel.DATA_DEGRADED.value
    assert snapshot["status"] == "POWER_TRUTH_UNAVAILABLE"


def test_host_power_red_flows_through_existing_risk_authority(monkeypatch):
    risk_conn = sqlite3.connect(":memory:")
    risk_conn.row_factory = sqlite3.Row
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.row_factory = sqlite3.Row

    monkeypatch.setattr(
        riskguard_module,
        "host_power_runway_snapshot",
        lambda: {"level": RiskLevel.RED.value},
    )
    monkeypatch.setattr(
        riskguard_module,
        "_bankroll_of_record_for_riskguard",
        lambda: object(),
    )
    monkeypatch.setattr(
        riskguard_module,
        "_collateral_identity_level",
        lambda _conn: RiskLevel.GREEN,
    )
    monkeypatch.setattr(
        riskguard_module,
        "storage_capacity_snapshot",
        lambda: {"level": RiskLevel.GREEN.value},
    )
    monkeypatch.setattr(riskguard_module, "get_connection", lambda *_a, **_k: risk_conn)
    monkeypatch.setattr(
        riskguard_module,
        "_get_runtime_trade_connection",
        lambda: trade_conn,
    )

    level = riskguard_module.tick_with_portfolio(
        PortfolioState(bankroll=100.0, authority="canonical_db")
    )

    assert level is RiskLevel.RED


def test_host_power_red_is_not_weakened_by_dependency_lock_attestation(
    monkeypatch,
    tmp_path,
):
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(risk_db)
    riskguard_module.init_risk_db(conn)
    _insert_risk_state_row(
        conn,
        checked_at=datetime.now(timezone.utc).isoformat(),
        level=RiskLevel.GREEN.value,
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        riskguard_module,
        "get_connection",
        lambda *_a, **_k: get_connection(risk_db),
    )
    monkeypatch.setattr(
        riskguard_module,
        "host_power_runway_snapshot",
        lambda: {"level": RiskLevel.RED.value},
    )

    level = riskguard_module._persist_dependency_db_locked_attestation(
        sqlite3.OperationalError("database is locked")
    )

    row = get_connection(risk_db).execute(
        "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])
    assert level is RiskLevel.RED
    assert row["level"] == RiskLevel.RED.value
    assert details["host_power_floor_applied"] is True
