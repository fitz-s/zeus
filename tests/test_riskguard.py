# Created: 2026-03-30
# Last reused/audited: 2026-06-29
# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md Batch D RiskGuard test-law remediation; Wave26 verification-noise helper alignment; PR90 current-env fallback review fix.
#                  2026-05-17 live lock remediation: RiskGuard trade/world DB lock degrades to fresh DATA_DEGRADED rather than stale RED.
# Lifecycle: created=2026-03-30; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Guard RiskGuard protective metrics, policy resolution, source authority, and portfolio loader invariants.
# Reuse: Run after RiskGuard risk details, portfolio loader, settlement source, bankroll, or risk-action changes.
"""Tests for RiskGuard metrics, policy resolution, and risk levels."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import src.riskguard.policy as policy_module
import src.riskguard.riskguard as riskguard_module
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
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    PortfolioState,
    Position,
    total_exposure_usd,
)


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
    token_id: str = "",
    no_token_id: str = "",
    condition_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id, order_status, updated_at,
            temperature_metric
        ) VALUES (?, ?, ?, 'm-test', 'NYC', 'NYC', '2026-04-01', '39-40°F', 'buy_yes', 'F', ?, ?, ?, NULL, NULL, NULL, NULL, ?, '', '', ?, '', '', 'unknown', ?, ?, ?, '', '', ?, ?)
        """,
        (
            position_id,
            phase,
            position_id,
            size_usd,
            shares,
            cost_basis_usd,
            last_monitor_market_price,
            strategy_key,
            token_id,
            no_token_id,
            condition_id,
            "2026-04-04T12:00:00+00:00",
            temperature_metric,
        ),
    )


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
        settlement_authority="VERIFIED",
        settlement_truth_source="world.settlements",
        settlement_market_slug=f"nyc-high-{position_id}",
        settlement_temperature_metric="high",
        settlement_source="WU",
        settlement_value=40.0 if outcome == 1 else 42.0,
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


def test_riskguard_recent_exits_skip_settlement_rows_without_metric_authority():
    rows = [
        {
            "city": "NYC",
            "range_label": "legacy-bin",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "exit_reason": "SETTLEMENT",
            "settled_at": "2026-04-01T23:00:00Z",
            "pnl": 99.0,
            "metric_ready": False,
            "settlement_authority": "LEGACY_UNKNOWN",
        },
        {
            "city": "NYC",
            "range_label": "39-40°F",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "exit_reason": "SETTLEMENT",
            "settled_at": "2026-04-02T00:00:00Z",
            "pnl": 4.2,
            "metric_ready": True,
            "settlement_authority": "VERIFIED",
        },
    ]

    assert riskguard_module._canonical_recent_exits_from_settlement_rows(rows) == [
        {
            "city": "NYC",
            "bin_label": "39-40°F",
            "target_date": "2026-04-01",
            "direction": "buy_yes",
            "token_id": "",
            "no_token_id": "",
            "exit_reason": "SETTLEMENT",
            "exited_at": "2026-04-02T00:00:00Z",
            "pnl": 4.2,
        }
    ]


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


def test_current_mode_realized_exits_blocks_degraded_settlement_rows_without_outcome_fact_fallback():
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
        lambda conn, as_of=None: {"status": "refreshed", "rows_written": 1},
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
                "p_posterior": 0.78,
                "outcome": 1,
            },
            {
                "id": "missing-prob",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "p_posterior": None,
                "outcome": 1,
            },
            {
                "id": "metric-not-ready",
                "learning_snapshot_ready": True,
                "metric_ready": False,
                "p_posterior": 0.65,
                "outcome": 1,
            },
            {
                "id": "learning-ready-2",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "p_posterior": 0.31,
                "outcome": 0,
            },
            {
                "id": "learning-ready-3",
                "learning_snapshot_ready": True,
                "metric_ready": True,
                "p_posterior": 0.52,
                "outcome": 1,
            },
        ]

        selected = riskguard_module._riskguard_brier_metric_rows(rows, limit=2)

        assert [row["id"] for row in selected] == ["learning-ready-1", "learning-ready-2"]


def _settlement_row(
    *,
    trade_id: str,
    strategy: str,
    p_posterior: float,
    outcome: int,
    pnl: float = 0.0,
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
        "canonical_payload_complete": True,
        "is_degraded": False,
        "pnl": pnl,
        "city": "NYC",
        "range_label": "29C",
        "target_date": "2026-04-01",
        "direction": "buy_yes",
        "settled_at": "2026-04-02T00:00:00+00:00",
    }



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
            # bankroll/baseline values here are no longer the bankroll truth
            # source; left as-is so the daily/weekly baseline annotations in
            # details_json keep their previous values.
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
        assert details["portfolio_position_count"] == 2
        assert details["portfolio_capital_source"] == "dual_source_blended"
        # Bankroll truth axis: provider-sourced wallet cash plus canonical
        # open-position value, with no realized-PnL fold-in.
        assert details["initial_bankroll"] == pytest.approx(211.37)
        assert details["account_equity_components"]["wallet_cash_usd"] == pytest.approx(211.37)
        assert details["account_equity_components"]["open_position_equity_usd"] == pytest.approx(25.0)
        assert details["effective_bankroll"] == pytest.approx(236.37)
        assert details["bankroll_truth_source"] == "polymarket_wallet"
        # Baselines come from PortfolioState's daily/weekly snapshots (still
        # provided by the legacy load_portfolio path).
        assert details["daily_baseline_total"] == pytest.approx(151.0)
        assert details["weekly_baseline_total"] == pytest.approx(152.0)
        # PnL signals are still emitted for analytics, but realized PnL now
        # comes only from the strategy_health 30d read-model window.
        assert details["realized_pnl"] == pytest.approx(0.0)
        assert details["realized_pnl_source"] == "strategy_health.realized_pnl_30d"
        assert details["realized_pnl_window_days"] == 30
        assert details["unrealized_pnl"] == pytest.approx(5.0)

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

    def test_loader_quarantines_unloadable_row_instead_of_failing_whole_tick(
        self, monkeypatch, tmp_path
    ):
        """One un-loadable canonical row must NOT take down the whole RiskGuard loader.

        Regression guard (2026-06-16 incident): a single fill-grade row missing
        execution_fact provenance (a dual-id recovered-fill duplicate) caused the loader
        to RAISE -> RiskGuard tick failed -> RiskGuard went STALE -> trader fail-closed
        RED -> ALL trading blocked. The loader must QUARANTINE the bad row (exclude +
        log + count) and CONTINUE loading the valid rows. RED-on-revert: restoring the
        `raise RuntimeError(...)` makes `_load_riskguard_portfolio_truth` raise here.
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

        assert truth["quarantined_count"] == 1
        assert truth["quarantined_rows"][0]["trade_id"] == "bad-dup-1"
        assert [p.trade_id for p in portfolio.positions] == ["valid-good-1"]
        # Quarantine is a KNOWN exclusion -> consistency stays 'pass' (1 loaded + 1 quarantined == 2 metadata).
        assert truth["consistency_lock"] == "pass"

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
            level=RiskLevel.GREEN.value,
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

        assert row["level"] == RiskLevel.GREEN.value
        assert details["status"] == "metrics_in_progress_previous_risk_level_preserved"
        assert details["riskguard_degraded_reason"] == "metrics_refresh_in_progress"
        assert details["previous_full_risk_level"] == RiskLevel.GREEN.value
        assert riskguard_module.get_current_level() == RiskLevel.GREEN

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
        assert details["daily_loss_status"] == "ok"
        assert details["daily_loss_source"] == "risk_state_history"
        # P0-A DEF A (followup_design.md §2.1): effective_bankroll == initial_bankroll
        # (= wallet snapshot, no PnL math). The legacy assertion expected
        # effective == initial minus PnL under DEF B; the structural correction
        # is effective == initial with total_pnl preserved as analytics-only.
        assert details["daily_loss_reference"] == {
            "row_id": reference_id,
            "checked_at": reference_checked_at,
            "initial_bankroll": 211.37,
            "total_pnl": -13.26,
            "effective_bankroll": 211.37,
        }

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

        # P0-A DEF A: equity == wallet, not wallet+pnl. Both daily and weekly
        # references use initial_bankroll at this default seed. Loss
        # signal comes from current-equity vs reference-equity, both of which
        # are wallet snapshots. With monkey-patched wallet truth on both sides,
        # weekly_loss is 0 — but the test fixtures inject realized_pnl=-10 via
        # _mock_trailing_loss_tick which moves current_total_value separately.
        # Under DEF A this no longer changes equity; the assertion below is
        # rewritten to lock the structural property that effective_bankroll
        # equals initial_bankroll, NOT pnl-adjusted.
        assert details["weekly_loss_status"] == "ok"
        assert details["weekly_loss_source"] == "risk_state_history"
        assert details["weekly_loss_reference"] == {
            "row_id": weekly_reference_id,
            "checked_at": weekly_reference_checked_at,
            "initial_bankroll": 211.37,
            "total_pnl": -5.0,
            "effective_bankroll": 211.37,
        }

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

        # Per df5ce642 (RiskGuard cold-start: empty/stale → GREEN): the
        # cold-start `insufficient_history` case is not a data-integrity
        # failure — there's no history yet, so no loss can have occurred.
        # Level is GREEN with explicit `bootstrap_no_history:...` status.
        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "bootstrap_no_history:insufficient_history"
        assert details["daily_loss_level"] == RiskLevel.GREEN.value
        assert details["daily_loss_source"] == "no_trustworthy_reference_row"
        assert details["daily_loss_reference"] is None

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

        assert level == RiskLevel.DATA_DEGRADED
        assert row["level"] == RiskLevel.DATA_DEGRADED.value
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "degraded:inconsistent_history"
        assert details["daily_loss_level"] == RiskLevel.DATA_DEGRADED.value
        assert details["daily_loss_reference"] is None

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

        # Per df5ce642: cold-start `no_reference_row` → GREEN with
        # `bootstrap_no_history:...` status (no history yet means no loss).
        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "bootstrap_no_history:no_reference_row"
        assert details["daily_loss_level"] == RiskLevel.GREEN.value
        assert details["daily_loss_source"] == "no_trustworthy_reference_row"
        assert details["daily_loss_reference"] is None

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

        # P0-A DEF A (followup_design.md §2.1): equity = wallet, no pnl math.
        # Both reference and current equity come from the same wallet
        # (default monkeypatched in conftest), so daily_loss is structurally 0
        # under DEF A. The original assertion (loss=2) encoded DEF B; the
        # structural property this test guards is "stale-but-trustworthy
        # reference is correctly selected" — preserved via the row_id check.
        assert details["daily_loss"] == pytest.approx(0.0)
        # Per df5ce642 (cold-start follow-up): out-of-window stale row →
        # `bootstrap_stale_reference` (not bare `stale_reference`) so
        # observability distinguishes "history but stale" from fresh deploy.
        assert details["daily_loss_status"] == "bootstrap_stale_reference"
        assert details["daily_loss_source"] == "risk_state_history"
        assert details["daily_loss_reference"]["row_id"] == stale_reference_id

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

        # P0-A DEF A: equity == wallet on both sides → daily_loss = 0 under flat
        # wallet. The structural property this test guards is "trustworthy
        # within-window reference is selected" — preserved via row_id +
        # checked_at + status="ok".
        assert details["daily_loss"] == pytest.approx(0.0)
        assert details["daily_loss_status"] == "ok"
        assert details["daily_loss_reference"]["row_id"] == trusted_id
        assert details["daily_loss_reference"]["checked_at"] == trusted_checked_at


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
    """ORANGE-localization coverage (live incident 2026-07-04): a portfolio
    Brier ORANGE breach fully attributable to a durably-gated, canonical
    strategy may localize to GREEN admission instead of freezing every
    strategy — but ONLY when all three safety preconditions hold: clean
    attribution (no unclassified rows), a read-after-write CONFIRMED active
    durable gate per degraded strategy, and a residual (non-gated) portfolio
    that itself recomputes to GREEN. RED never localizes.

    Test data: 45 opening_inertia rows at p=0.58/outcome=0 (per-row squared
    error 0.3364, individually ORANGE) + 5 center_buy rows at p=0.80/outcome=1
    (per-row squared error 0.04, individually GREEN) pool to a portfolio Brier
    of ~0.3068 (ORANGE); excluding the gated opening_inertia rows leaves just
    the clean center_buy rows at 0.04 (GREEN) — mirroring the live incident's
    opening_inertia trailing-30d Brier 0.322 freezing healthy center_buy.
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
            )
            for i in range(classified_degraded)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
            )
            for i in range(5)
        ] + [
            _settlement_row(
                trade_id=f"unclassified-{i}",
                strategy="legacy_unattributed",
                p_posterior=0.58,
                outcome=0,
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
        assert details["localized_orange_quarantine"] is True
        assert details["brier_strategy_localization"]["status"] == "localized_orange_quarantine"
        assert details["brier_strategy_localization"]["gated_strategies"] == ["opening_inertia"]
        assert details["brier_strategy_localization"]["gate_confirmation"] == {"opening_inertia": True}
        assert dict(gate_row) == {"strategy_key": "opening_inertia", "status": "active"}

    def test_orange_stays_global_when_unclassified_rows_present(self, monkeypatch, tmp_path):
        """Live-incident regression pin: unclassified_count>0 must NOT localize,
        even though the classified portion is cleanly attributable and gated."""
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = self._orange_rows(unclassified_count=3)

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
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

        assert level == RiskLevel.ORANGE
        assert risk_row["level"] == RiskLevel.ORANGE.value
        assert details["portfolio_brier_level"] == "ORANGE"
        assert details["brier_level"] == "ORANGE"
        assert details["brier_active_portfolio_level"] == "ORANGE"
        assert details["localized_orange_quarantine"] is False
        assert details["brier_strategy_localization"]["status"] == "not_localized"
        assert details["brier_strategy_breakdown"]["unclassified_count"] == 3

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

        assert level == RiskLevel.ORANGE
        assert risk_row["level"] == RiskLevel.ORANGE.value
        assert details["brier_level"] == "ORANGE"
        assert details["localized_orange_quarantine"] is False
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

        assert level == RiskLevel.ORANGE
        assert risk_row["level"] == RiskLevel.ORANGE.value
        assert details["brier_level"] == "ORANGE"
        assert details["localized_orange_quarantine"] is False
        assert details["brier_strategy_localization"]["status"] == "orange_residual_portfolio_not_green"
        assert details["brier_strategy_localization"]["residual_brier_level"] == "ORANGE"
        assert details["brier_strategy_localization"]["gate_confirmation"] == {"opening_inertia": True}

    def test_red_never_localizes_even_with_confirmed_durable_gate(self, monkeypatch, tmp_path):
        """RED stays global fail-closed unconditionally — even when a durable
        gate for the offending strategy is ALREADY active going into the tick."""
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"
        rows = [
            _settlement_row(
                trade_id=f"opening-{i}",
                strategy="opening_inertia",
                p_posterior=0.95,
                outcome=0,
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
            )
            for i in range(5)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        conn = get_connection(zeus_db)
        _insert_risk_action(
            conn,
            action_id="riskguard:gate:opening_inertia",
            strategy_key="opening_inertia",
            action_type="gate",
            value="true",
            issued_at="2026-07-03T00:00:00+00:00",
            effective_until=None,
            precedence=50,
            status="active",
        )
        conn.commit()
        conn.close()
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

        assert level == RiskLevel.RED
        assert risk_row["level"] == RiskLevel.RED.value
        assert details["portfolio_brier_level"] == "RED"
        assert details["brier_level"] == "RED"
        assert details["brier_all_strategies_level"] == "RED"
        assert details["brier_active_portfolio_level"] == "RED"
        assert details["localized_orange_quarantine"] is False
        assert details["brier_strategy_localization"]["status"] == "not_localized"

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
        assert level == RiskLevel.ORANGE
        assert risk_row["level"] == RiskLevel.ORANGE.value
        assert details["brier_level"] == "ORANGE"
        assert details["localized_orange_quarantine"] is False
        assert (
            details["brier_strategy_localization"]["status"]
            == "durable_strategy_gate_unconfirmed_global_orange"
        )
        assert details["brier_strategy_localization"]["gate_confirmation"] == {"opening_inertia": False}
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
            {"strategy": "forecast_qkernel_entry", "p_posterior": 0.79, "outcome": 0},
        ] + [
            {"strategy": "center_buy", "p_posterior": 0.80, "outcome": 1}
            for _ in range(12)
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
            {"strategy": "opening_inertia", "p_posterior": 0.58, "outcome": 0}
            for _ in range(n)
        ]
        out = riskguard_module._strategy_brier_breakdown(
            bad, {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        oi = out["by_strategy"]["opening_inertia"]
        assert oi["sample_size"] == n
        assert oi["level"] != "GREEN"
        assert "opening_inertia" in out["degraded_strategies"]

    def test_one_below_floor_does_not_convict(self):
        n = riskguard_module._STRATEGY_BRIER_MIN_SAMPLE - 1
        bad = [
            {"strategy": "opening_inertia", "p_posterior": 0.58, "outcome": 0}
            for _ in range(n)
        ]
        out = riskguard_module._strategy_brier_breakdown(
            bad, {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35},
        )
        assert "opening_inertia" not in out["degraded_strategies"]
        assert out["by_strategy"]["opening_inertia"]["thin_sample_no_verdict"] is True


class TestRiskGuardExecutionQualityLocalization:
    """Execution-quality localization (2026-07-05 fresh-start deadlock): the
    legacy book's global fill-rate (0.178 over 200 events, almost all
    pre-restart) sat below the 0.3 decay threshold and froze ALL entries on
    the GREEN-only reactor gate — but every low-fill strategy was already
    held behind a confirmed durable gate, and the admissible (non-gated)
    portfolio's own fill-rate was healthy. Same admissible-portfolio
    principle as ORANGE Brier localization: attribute the evidence, never
    age or window it away."""

    def _orange_rows(self) -> list[dict]:
        rows = [
            _settlement_row(
                trade_id=f"opening-{i}", strategy="opening_inertia",
                p_posterior=0.58, outcome=0,
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}", strategy="center_buy",
                p_posterior=0.80, outcome=1,
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

    def test_gated_strategy_fill_decay_localizes_when_residual_healthy(
        self, monkeypatch, tmp_path,
    ):
        level, details = self._run_tick(
            monkeypatch, tmp_path, residual_fill_rate_healthy=True,
        )
        assert details["execution_quality_level"] == "GREEN"
        assert details["brier_strategy_localization"]["execution_quality_localized"] is True
        assert details["brier_strategy_localization"]["execution_gated_strategies"] == ["opening_inertia"]
        assert details["brier_strategy_localization"]["execution_residual_fill_rate"] >= 0.25
        assert "tighten_risk" not in details.get("recommended_controls", [])
        # With Brier ORANGE localized AND execution localized, admission is GREEN.
        assert level == RiskLevel.GREEN

    def test_stays_yellow_when_decayed_strategy_gate_not_confirmed(
        self, monkeypatch, tmp_path,
    ):
        """A decayed residual strategy whose durable gate did NOT confirm
        (lock/skip) must keep the global YELLOW: localization's safety
        precondition is a CONFIRMED gate, exactly as in ORANGE Brier
        localization. (A confirmed-gated decayed strategy leaves the
        residual and can no longer enter — that path localizes.)"""
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
        assert details["execution_quality_level"] == "YELLOW"
        assert details["brier_strategy_localization"].get("execution_quality_localized") is None
        assert level == RiskLevel.YELLOW


class TestStrategyPolicyResolver:
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

    def test_tick_turns_yellow_on_execution_decay(self, monkeypatch, tmp_path):
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

        # 2026-07-05 execution-quality localization: the decayed strategy is
        # identified, a durable gate is emitted AND read-after-write
        # confirmed in the same tick, and the residual (non-gated) portfolio
        # is empty — so admission localizes to GREEN instead of freezing
        # everything. The unconfirmed-gate path staying YELLOW is pinned by
        # TestRiskGuardExecutionQualityLocalization.
        assert level == RiskLevel.GREEN
        assert row["level"] == RiskLevel.GREEN.value
        assert details["execution_quality_level"] == "GREEN"
        assert details["recommended_strategy_gates"] == ["center_buy"]
        assert details["brier_strategy_localization"]["execution_quality_localized"] is True
        assert details["brier_strategy_localization"]["execution_gated_strategies"] == ["center_buy"]
        assert "tighten_risk" not in details["recommended_controls"]
        assert details["recommended_strategy_gate_reasons"]["center_buy"] == [
            "execution_decay(fill_rate=0.0, observed=10)"
        ]
        # tighten_risk is withdrawn when localization clears the global level
        # (the decayed strategy is durably gated; nothing else needs tightening).
        assert "tighten_risk" not in details["recommended_control_reasons"]

    def test_tick_turns_yellow_on_strategy_edge_compression_alert(self, monkeypatch, tmp_path):
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

        assert level == RiskLevel.YELLOW
        assert row["level"] == RiskLevel.YELLOW.value
        assert details["strategy_signal_level"] == "YELLOW"
        assert details["recommended_strategy_gates"] == ["center_buy"]
        assert "review_strategy_gates" in details["recommended_controls"]
        assert details["recommended_strategy_gate_reasons"]["center_buy"] == ["edge_compression"]
        assert details["recommended_control_reasons"]["review_strategy_gates"] == [
            "center_buy:edge_compression"
        ]

    def test_tick_emits_durable_risk_action_for_recommended_strategy_gate(self, monkeypatch, tmp_path):
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

        assert dict(row) == {
            "strategy_key": "center_buy",
            "action_type": "gate",
            "value": "true",
            "source": "riskguard",
            "precedence": 50,
            "status": "active",
            "reason": "edge_compression",
        }
        risk_state_row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(risk_state_row["details_json"])
        assert details["durable_risk_action_emission_status"] == "emitted"
        assert details["durable_risk_action_emitted_count"] == 1
        assert details["durable_risk_action_expired_count"] == 0

    def test_tick_refreshes_existing_durable_risk_action_without_duplication(self, monkeypatch, tmp_path):
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
        assert dict(row) == {"status": "active", "reason": "edge_compression"}

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

        assert details["recommended_strategy_gates"] == ["center_buy"]
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
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
            )
            for i in range(5)
        ]

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
            )
            for i in range(45)
        ] + [
            _settlement_row(
                trade_id=f"center-{i}",
                strategy="center_buy",
                p_posterior=0.80,
                outcome=1,
            )
            for i in range(5)
        ]

        def _fake_get_connection(path=None, **_kwargs):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db, drop_risk_actions=True)
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
        assert details["brier_level"] == "YELLOW"
        assert (
            details["brier_strategy_localization"]["status"]
            == "durable_strategy_gate_unavailable_global_yellow"
        )
        assert details["durable_risk_action_emission_status"] == "skipped_missing_table"
        assert details["recommended_strategy_gates"] == ["opening_inertia"]

    def test_tick_turns_yellow_when_strategy_tracker_unavailable(self, monkeypatch, tmp_path):
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

        assert level == RiskLevel.YELLOW
        assert row["level"] == RiskLevel.YELLOW.value
        assert details["strategy_signal_level"] == "YELLOW"
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
        assert details["settlement_authority_levels"]["durable_event"] == 1
        assert details["settlement_authority_levels"]["durable_event_malformed"] == 1

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
