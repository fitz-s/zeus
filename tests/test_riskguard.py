# Created: 2026-03-30
# Last reused/audited: 2026-05-05
# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md Batch D RiskGuard test-law remediation.
# Lifecycle: created=2026-03-30; last_reviewed=2026-05-05; last_reused=2026-05-05
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


def _policy_conn() -> sqlite3.Connection:
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
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
        env="live",
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
        env="live",
        settlement_rows=settlement_rows,
    )

    assert source == "authoritative_settlement_rows"
    assert degraded is True
    assert exits == []


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
    def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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
        assert details["portfolio_position_count"] == 1
        assert details["portfolio_capital_source"] == "dual_source_blended"
        # Bankroll truth axis (P0-A): provider-sourced wallet; DEF A
        # makes effective_bankroll == initial_bankroll (no PnL fold-in).
        assert details["initial_bankroll"] == pytest.approx(211.37)
        assert details["effective_bankroll"] == pytest.approx(211.37)
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

    def test_tick_does_not_use_metadata_recent_exits_without_authoritative_settlements(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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

    def test_tick_records_explicit_portfolio_fallback_when_projection_unavailable(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
            return get_connection(risk_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)

        level = riskguard_module.get_current_level()

        assert level == RiskLevel.RED

    def test_tick_records_canonical_settlement_source(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events"}],
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

        def _fake_get_connection(path=None):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        _init_empty_canonical_portfolio_schema(zeus_db)
        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50, **kwargs: [{"p_posterior": 0.4, "outcome": 0, "source": "decision_log"}],
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

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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
             occurred_at, strategy_key, source_module, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, ("exec-1:intent:1", "exec-1", 1, 1, "POSITION_OPEN_INTENT",
               "2026-04-01T10:00:00Z", "center_buy", "test", '{}'))
        conn.execute("""
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, ("exec-1:filled:2", "exec-1", 1, 2, "ENTRY_ORDER_FILLED",
               "2026-04-01T10:01:00Z", "center_buy", "test", '{}'))
        conn.execute("""
            INSERT INTO position_events
            (event_id, position_id, event_version, sequence_no, event_type,
             occurred_at, strategy_key, source_module, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, ("exec-2:rejected:1", "exec-2", 1, 1, "ENTRY_ORDER_REJECTED",
               "2026-04-01T10:02:00Z", "opening_inertia", "test", '{}'))
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
        assert overall["fill_rate"] == pytest.approx(0.5)
        assert details["entry_execution_summary"]["by_strategy"]["center_buy"]["filled"] == 1
        assert details["entry_execution_summary"]["by_strategy"]["opening_inertia"]["rejected"] == 1

    def test_tick_records_strategy_tracker_diagnostics(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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
        # Insert 10 ENTRY_ORDER_REJECTED canonical events (P9: log_position_event deleted)
        for i in range(10):
            conn.execute("""
                INSERT INTO position_events
                (event_id, position_id, event_version, sequence_no, event_type,
                 occurred_at, strategy_key, source_module, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (f"reject-{i}:rejected:1", f"reject-{i}", 1, 1,
                   "ENTRY_ORDER_REJECTED", "2026-04-01T10:00:00Z",
                   "center_buy", "test", '{}'))
        conn.commit()
        conn.close()

        level = riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert level == RiskLevel.YELLOW
        assert row["level"] == RiskLevel.YELLOW.value
        assert details["execution_quality_level"] == "YELLOW"
        assert details["recommended_strategy_gates"] == ["center_buy"]
        assert "tighten_risk" in details["recommended_controls"]
        assert details["recommended_strategy_gate_reasons"]["center_buy"] == [
            "execution_decay(fill_rate=0.0, observed=10)"
        ]
        assert details["recommended_control_reasons"]["tighten_risk"] == [
            "execution_decay(fill_rate=0.0, observed=10)"
        ]

    def test_tick_turns_yellow_on_strategy_edge_compression_alert(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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

    def test_tick_turns_yellow_when_strategy_tracker_unavailable(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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

        def _fake_get_connection(path=None):
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
        settled_at="2026-04-03 12:00:00",
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

    def _fake_get_connection(path=None):
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
