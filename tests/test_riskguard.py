"""Tests for RiskGuard metrics and risk levels."""

import json

import pytest

import src.riskguard.riskguard as riskguard_module
import src.state.strategy_tracker as strategy_tracker_module
from src.riskguard.risk_level import RiskLevel, overall_level
from src.riskguard.metrics import (
    brier_score,
    directional_accuracy,
    evaluate_brier,
)
from src.state.db import get_connection
from src.state.portfolio import Position
from src.state.portfolio import PortfolioState


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
    def test_tick_records_canonical_settlement_source(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events"}],
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

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [{"p_posterior": 0.4, "outcome": 0, "source": "decision_log"}],
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

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [
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
        assert details["strategy_settlement_summary"]["center_buy"]["accuracy"] == pytest.approx(0.5)
        assert details["strategy_settlement_summary"]["opening_inertia"]["count"] == 1

    def test_tick_records_entry_execution_summary(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        conn = get_connection(zeus_db)
        from src.state.db import init_schema, log_position_event
        init_schema(conn)
        pos = Position(
            trade_id="exec-1",
            market_id="m1",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-04-01",
            bin_label="39-40°F",
            direction="buy_yes",
            strategy="center_buy",
            edge_source="center_buy",
            env="paper",
        )
        log_position_event(conn, "ORDER_ATTEMPTED", pos, details={"status": "pending"}, source="execution")
        log_position_event(conn, "ORDER_FILLED", pos, details={"status": "filled"}, source="execution")
        reject_pos = Position(
            trade_id="exec-2",
            market_id="m2",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-04-01",
            bin_label="41-42°F",
            direction="buy_yes",
            strategy="opening_inertia",
            edge_source="opening_inertia",
            env="paper",
        )
        log_position_event(conn, "ORDER_REJECTED", reject_pos, details={"status": "rejected"}, source="execution")
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

        tracker = strategy_tracker_module.StrategyTracker()
        tracker.record_trade({"trade_id": "t1", "strategy": "center_buy", "pnl": 3.0, "entered_at": "2026-04-01T00:00:00Z", "edge": 0.12})
        tracker.record_trade({"trade_id": "t2", "strategy": "center_buy", "pnl": -1.0, "entered_at": "2026-04-02T00:00:00Z", "edge": 0.08})
        tracker.set_accounting_metadata(current_regime_started_at="2026-04-01T00:00:00Z")

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True, "strategy": "center_buy"}],
        )

        riskguard_module.tick()
        row = get_connection(risk_db).execute(
            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        details = json.loads(row["details_json"])

        assert details["strategy_tracker_summary"]["center_buy"]["trades"] == 2
        assert details["strategy_tracker_summary"]["center_buy"]["pnl"] == pytest.approx(2.0)
        assert details["strategy_tracker_accounting"]["current_regime_started_at"] == "2026-04-01T00:00:00Z"
        assert details["recommended_strategy_gates"] == []

    def test_tick_turns_yellow_on_execution_decay(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: strategy_tracker_module.StrategyTracker())
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
        )

        conn = get_connection(zeus_db)
        from src.state.db import init_schema, log_position_event
        init_schema(conn)
        for i in range(10):
            pos = Position(
                trade_id=f"reject-{i}",
                market_id="m1",
                city="NYC",
                cluster="US-Northeast",
                target_date="2026-04-01",
                bin_label="39-40°F",
                direction="buy_yes",
                strategy="center_buy",
                edge_source="center_buy",
                env="paper",
            )
            log_position_event(conn, "ORDER_REJECTED", pos, details={"status": "rejected"}, source="execution")
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

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
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

    def test_tick_records_degraded_settlement_counts(self, monkeypatch, tmp_path):
        zeus_db = tmp_path / "zeus.db"
        risk_db = tmp_path / "risk_state.db"

        def _fake_get_connection(path=None):
            if path == riskguard_module.RISK_DB_PATH:
                return get_connection(risk_db)
            return get_connection(zeus_db)

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [
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

        monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
        monkeypatch.setattr(
            riskguard_module,
            "query_authoritative_settlement_rows",
            lambda conn, limit=50: [
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
