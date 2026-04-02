"""Cross-module P&L flow, CI-threshold, and hardcoded-audit tests."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest

import src.control.control_plane as control_plane_module
import src.engine.cycle_runner as cycle_runner
import src.engine.evaluator as evaluator_module
import src.engine.monitor_refresh as monitor_refresh
import src.main as main_module
import src.execution.harvester as harvester_module
import src.observability.status_summary as status_summary_module
import src.riskguard.riskguard as riskguard_module
import scripts.apply_recommended_controls as apply_recommended_controls_script
from src.supervisor_api.contracts import SupervisorCommand
from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair
from src.config import City
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import EdgeDecision, MarketCandidate
from src.execution.executor import OrderResult
from src.riskguard.risk_level import RiskLevel
from src.state.db import get_connection, init_schema, log_settlement_event
from src.state.decision_chain import SettlementRecord, store_settlement_records
from src.state.portfolio import (
    PortfolioState,
    Position,
    buy_no_edge_threshold,
    buy_yes_edge_threshold,
    close_position,
)
from src.state.strategy_tracker import StrategyTracker
from src.types import Bin, BinEdge


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="US-Northeast",
    settlement_unit="F",
    wu_station="KLGA",
)

MISSING = object()


def _position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=5.0,
        entry_price=0.10,
        p_posterior=0.20,
        edge=0.10,
        shares=50.0,
        cost_basis_usd=5.0,
        entered_at="2026-03-30T00:00:00Z",
        token_id="yes123",
        no_token_id="no456",
        entry_method="ens_member_counting",
        entry_ci_width=0.10,
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _recent_exit(pnl: float) -> dict:
    return {
        "city": "NYC",
        "bin_label": "39-40°F",
        "target_date": "2026-04-01",
        "direction": "buy_yes",
        "token_id": "yes123",
        "no_token_id": "no456",
        "exit_reason": "SETTLEMENT",
        "exited_at": "2026-03-30T00:00:00Z",
        "pnl": pnl,
    }


def _insert_snapshot(
    conn,
    city: str,
    target_date: str,
    p_raw: list[float],
    *,
    issue_time: str = "2026-03-30T00:00:00Z",
    data_version: str = "live_v1",
) -> str:
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city,
            target_date,
            issue_time,
            f"{target_date}T12:00:00Z",
            "2026-03-30T01:00:00Z",
            "2026-03-30T01:00:00Z",
            48.0,
            json.dumps([40.0] * 51),
            json.dumps(p_raw),
            2.0,
            0,
            "ecmwf_ifs025",
            data_version,
        ),
    )
    row = conn.execute("SELECT last_insert_rowid() AS snapshot_id").fetchone()
    conn.commit()
    return str(row["snapshot_id"])


def _lookup_nested(data: dict, dotted_path: str):
    current = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def test_unrealized_pnl_updates_with_market():
    pos = _position()
    assert pos.unrealized_pnl == 0.0

    pos.last_monitor_market_price = 0.15
    assert pos.unrealized_pnl == pytest.approx(2.50)

    pos.last_monitor_market_price = 0.05
    assert pos.unrealized_pnl == pytest.approx(-2.50)


def test_total_pnl_combines_realized_and_unrealized():
    portfolio = PortfolioState(
        bankroll=150.0,
        recent_exits=[_recent_exit(3.0)],
    )

    open_pos = _position()
    open_pos.last_monitor_market_price = 0.08
    portfolio.positions.append(open_pos)

    assert portfolio.realized_pnl == pytest.approx(3.0)
    assert portfolio.total_unrealized_pnl == pytest.approx(-1.0)
    assert portfolio.total_pnl == pytest.approx(2.0)
    assert portfolio.effective_bankroll == pytest.approx(152.0)


def test_buy_no_edge_threshold_uses_entry_ci_width():
    # ExpiringAssumption for buy_no_scaling_factor expires after 180 days from 2025-01-01.
    # With current date (2026-03-31), the fallback scaling=1.5 and fallback floor=-0.03 apply:
    # raw = -0.04 * 1.5 = -0.06, clamped to max(-0.15, min(-0.03, -0.06)) = -0.06
    assert buy_no_edge_threshold(0.04) == pytest.approx(-0.06)


def test_buy_no_edge_threshold_clamps_to_ceiling():
    assert buy_no_edge_threshold(0.80) == pytest.approx(-0.15)


def test_buy_yes_edge_threshold_uses_entry_ci_width():
    # ExpiringAssumption for buy_yes_scaling_factor expires after 180 days from 2025-01-01.
    # With current date (2026-03-31), the fallback scaling=1.0 and fallback floor=-0.02 apply:
    # raw = -0.02 * 1.0 = -0.02, clamped to max(-0.10, min(-0.02, -0.02)) = -0.02
    assert buy_yes_edge_threshold(0.02) == pytest.approx(-0.02)


def test_buy_yes_edge_threshold_clamps_to_ceiling():
    assert buy_yes_edge_threshold(0.80) == pytest.approx(-0.10)


def test_hardcoded_constants_documented():
    settings = json.loads(
        Path("/Users/leofitz/.openclaw/workspace-venus/zeus/config/settings.json").read_text()
    )
    src_root = Path("/Users/leofitz/.openclaw/workspace-venus/zeus/src")
    marker_pattern = re.compile(
        r'HARDCODED\(\s*setting_key="([^"]+)",\s*note_key="([^"]+)"',
        re.S,
    )

    markers: list[tuple[str, str]] = []
    for path in src_root.rglob("*.py"):
        markers.extend(marker_pattern.findall(path.read_text()))

    assert markers, "No HARDCODED markers found in source"

    for setting_key, note_key in markers:
        assert _lookup_nested(settings, setting_key) is not MISSING
        note_value = _lookup_nested(settings, note_key)
        assert note_value is not MISSING
        assert isinstance(note_value, str) and note_value

    # Settings-only tracked constants still need explicit notes even if not hardcoded in src/.
    for note_key in [
        "sizing._kelly_multiplier_note",
        "edge._base_alpha_note",
        "edge._spread_tight_f_note",
        "edge._spread_wide_f_note",
        "edge._fdr_alpha_note",
        "exit._buy_no_scaling_factor_note",
        "exit._buy_yes_scaling_factor_note",
        "exit._buy_no_floor_note",
        "exit._buy_no_ceiling_note",
        "exit._buy_yes_floor_note",
        "exit._buy_yes_ceiling_note",
        "exit._consecutive_confirmations_note",
        "exit._near_settlement_hours_note",
        "exit._divergence_soft_threshold_note",
        "exit._divergence_hard_threshold_note",
        "exit._divergence_velocity_confirm_note",
    ]:
        value = _lookup_nested(settings, note_key)
        assert value is not MISSING
        assert isinstance(value, str) and value


def test_inv_unrealized_pnl_computed():
    pos = _position()
    pos.last_monitor_market_price = 0.12
    assert pos.unrealized_pnl == pytest.approx(1.0)


def test_exit_telemetry_persists_to_recent_exits():
    portfolio = PortfolioState()
    pos = _position(
        trade_id="tx1",
        market_id="m1",
        direction="buy_no",
        exit_trigger="MODEL_DIVERGENCE_PANIC",
        exit_divergence_score=0.34,
        exit_market_velocity_1h=-0.12,
        exit_forward_edge=-0.08,
    )
    portfolio.positions.append(pos)

    close_position(portfolio, "tx1", 0.12, "Model-Market divergence score 0.34 exceeds hard threshold")
    ex = portfolio.recent_exits[-1]
    assert ex["exit_trigger"] == "MODEL_DIVERGENCE_PANIC"
    assert ex["exit_divergence_score"] == pytest.approx(0.34)
    assert ex["exit_market_velocity_1h"] == pytest.approx(-0.12)
    assert ex["exit_forward_edge"] == pytest.approx(-0.08)


def test_inv_monitor_updates_market_price(monkeypatch):
    class DummyClob:
        paper_mode = True

    monkeypatch.setattr(monitor_refresh, "get_current_yes_price", lambda market_id: 0.62)
    monkeypatch.setattr(
        monitor_refresh,
        "recompute_native_probability",
        lambda position, current_p_market, registry, **context: position.p_posterior,
    )

    pos = _position(last_monitor_market_price=None, last_monitor_at="")
    initial = pos.last_monitor_at

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)
    market_price = edge_ctx.p_market[0]

    assert market_price == pytest.approx(0.62)
    assert pos.last_monitor_market_price == pytest.approx(0.62)
    assert pos.last_monitor_at != initial


def test_inv_status_reports_real_pnl(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            now,
            now,
            json.dumps(
                {
                    "no_trade_cases": [
                        {
                            "decision_id": "nt1",
                            "city": "NYC",
                            "target_date": "2026-04-01",
                            "range_label": "39-40°F",
                            "direction": "buy_yes",
                            "rejection_stage": "EDGE_INSUFFICIENT",
                            "rejection_reasons": ["small"],
                        }
                    ]
                }
            ),
            now,
            "paper",
        ),
    )
    conn.commit()
    conn.close()
    portfolio = PortfolioState(
        bankroll=150.0,
        recent_exits=[_recent_exit(-2.3)],
    )
    open_pos = _position()
    open_pos.strategy = "center_buy"
    open_pos.last_monitor_market_price = 0.13
    open_pos.chain_state = "exit_pending_missing"
    open_pos.exit_state = "retry_pending"
    open_pos.entry_fill_verified = False
    open_pos.state = "day0_window"
    portfolio.positions.append(open_pos)
    portfolio.recent_exits[0]["strategy"] = "opening_inertia"

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(
        status_summary_module,
        "_get_risk_details",
        lambda: {
            "execution_quality_level": "YELLOW",
            "recommended_strategy_gates": ["center_buy"],
            "recommended_strategy_gate_reasons": {"center_buy": ["execution_decay(fill_rate=0.2, observed=12)"]},
            "recommended_controls": ["tighten_risk"],
            "recommended_control_reasons": {"tighten_risk": ["execution_decay(fill_rate=0.2, observed=12)"]},
        },
    )
    monkeypatch.setattr(status_summary_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: True)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 2.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {"opening_inertia": False})

    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())

    assert status["portfolio"]["realized_pnl"] == pytest.approx(-2.3)
    assert status["portfolio"]["unrealized_pnl"] == pytest.approx(1.5)
    assert status["portfolio"]["total_pnl"] == pytest.approx(-0.8)
    assert status["portfolio"]["effective_bankroll"] == pytest.approx(149.2)
    assert status["portfolio"]["positions"][0]["chain_state"] == open_pos.chain_state
    assert status["portfolio"]["positions"][0]["exit_state"] == open_pos.exit_state
    assert status["portfolio"]["positions"][0]["entry_fill_verified"] == open_pos.entry_fill_verified
    assert status["portfolio"]["positions"][0]["admin_exit_reason"] == open_pos.admin_exit_reason
    assert status["control"]["entries_paused"] is True
    assert status["control"]["edge_threshold_multiplier"] == 2.0
    assert status["control"]["strategy_gates"]["opening_inertia"] is False
    assert status["control"]["recommended_controls"] == ["tighten_risk"]
    assert status["control"]["recommended_control_reasons"]["tighten_risk"] == [
        "execution_decay(fill_rate=0.2, observed=12)"
    ]
    assert status["control"]["recommended_strategy_gates"] == ["center_buy"]
    assert status["control"]["recommended_strategy_gate_reasons"]["center_buy"] == [
        "execution_decay(fill_rate=0.2, observed=12)"
    ]
    assert status["control"]["recommended_but_not_gated"] == ["center_buy"]
    assert status["control"]["gated_but_not_recommended"] == ["opening_inertia"]
    assert status["control"]["recommended_controls_not_applied"] == []
    assert status["control"]["recommended_auto_commands"] == []
    assert status["control"]["review_required_commands"] == [
        {
            "command": "set_strategy_gate",
            "strategy": "center_buy",
            "enabled": False,
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
        {
            "command": "set_strategy_gate",
            "strategy": "opening_inertia",
            "enabled": True,
            "note": "recommended_by=gate_drift_resolved",
        },
    ]
    assert status["control"]["recommended_commands"] == [
        {
            "command": "set_strategy_gate",
            "strategy": "center_buy",
            "enabled": False,
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
        {
            "command": "set_strategy_gate",
            "strategy": "opening_inertia",
            "enabled": True,
            "note": "recommended_by=gate_drift_resolved",
        },
    ]
    assert status["runtime"]["chain_state_counts"]["exit_pending_missing"] == 1
    assert status["runtime"]["exit_state_counts"]["retry_pending"] == 1
    assert status["runtime"]["unverified_entries"] == 1
    assert status["runtime"]["day0_positions"] == 1
    assert "overall" in status["execution"]
    assert status["no_trade"]["recent_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert status["learning"]["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert status["learning"]["by_strategy"] == {}
    assert status["strategy"]["center_buy"]["open_positions"] == 1
    assert status["strategy"]["center_buy"]["unrealized_pnl"] == pytest.approx(1.5)
    assert status["strategy"]["center_buy"]["gated"] is False
    assert status["strategy"]["center_buy"]["recommended_gate"] is True
    assert status["strategy"]["center_buy"]["recommended_gate_reasons"] == [
        "execution_decay(fill_rate=0.2, observed=12)"
    ]
    assert status["strategy"]["opening_inertia"]["realized_pnl"] == pytest.approx(-2.3)
    assert status["strategy"]["opening_inertia"]["gated"] is True
    assert status["risk"]["level"] == "GREEN"
    assert status["risk"]["riskguard_level"] == "GREEN"
    assert status["risk"]["consistency_check"]["ok"] is True
    assert status["risk"]["details"]["execution_quality_level"] == "YELLOW"
    assert status["truth"]["source_path"] == str(status_path)
    assert status["truth"]["deprecated"] is False


def test_inv_run_mode_writes_failure_status(monkeypatch):
    captured = {}
    monkeypatch.setattr(main_module, "run_cycle", lambda mode: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(
        "src.observability.status_summary.write_status",
        lambda cycle_summary=None: captured.setdefault("summary", cycle_summary),
    )

    main_module._run_mode(DiscoveryMode.OPENING_HUNT)

    assert captured["summary"]["mode"] == DiscoveryMode.OPENING_HUNT.value
    assert captured["summary"]["failed"] is True
    assert captured["summary"]["failure_reason"] == "boom"


def test_inv_status_escalates_risk_when_cycle_failed_or_query_errors(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    portfolio = PortfolioState(bankroll=150.0)

    class BrokenConn:
        def close(self):
            return None

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_connection", lambda: BrokenConn())
    monkeypatch.setattr(status_summary_module, "load_tracker", lambda: type("T", (), {"accounting": {}})())

    def _boom(_conn):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: _boom(conn))
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})

    status_summary_module.write_status({"mode": "test", "risk_level": "ORANGE", "failed": True, "failure_reason": "boom"})
    status = json.loads(status_path.read_text())

    assert status["risk"]["level"] == "RED"
    assert status["risk"]["riskguard_level"] == "GREEN"
    assert status["risk"]["consistency_check"]["ok"] is False
    assert "cycle_risk_level_mismatch:ORANGE->GREEN" in status["risk"]["consistency_check"]["issues"]
    assert "cycle_failed" in status["risk"]["consistency_check"]["issues"]
    assert "execution_summary_unavailable" in status["risk"]["consistency_check"]["issues"]


def test_inv_status_strategy_merges_learning_surface(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    portfolio = PortfolioState(bankroll=150.0, positions=[_position(strategy="center_buy")])

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(
        status_summary_module,
        "_get_risk_details",
        lambda: {
            "recommended_strategy_gates": ["center_buy"],
            "recommended_strategy_gate_reasons": {"center_buy": ["edge_compression"]},
        },
    )
    monkeypatch.setattr(status_summary_module, "get_connection", lambda: DummyConn())
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {})
    monkeypatch.setattr(
        status_summary_module,
        "query_learning_surface_summary",
        lambda conn, not_before=None: {
            "by_strategy": {
                "center_buy": {
                    "settlement_count": 2,
                    "settlement_pnl": 1.25,
                    "settlement_accuracy": 0.5,
                    "no_trade_count": 3,
                    "no_trade_stage_counts": {"EDGE_INSUFFICIENT": 2, "RISK_REJECTED": 1},
                    "entry_attempted": 4,
                    "entry_filled": 1,
                    "entry_rejected": 3,
                },
                "opening_inertia": {
                    "settlement_count": 0,
                    "settlement_pnl": 0.0,
                    "settlement_accuracy": None,
                    "no_trade_count": 2,
                    "no_trade_stage_counts": {"MARKET_FILTER": 2},
                    "entry_attempted": 0,
                    "entry_filled": 0,
                    "entry_rejected": 0,
                },
            }
        },
    )
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {"opening_inertia": False})

    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())

    assert status["strategy"]["center_buy"]["settlement_count"] == 2
    assert status["strategy"]["center_buy"]["settlement_pnl"] == 1.25
    assert status["strategy"]["center_buy"]["no_trade_count"] == 3
    assert status["strategy"]["center_buy"]["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 2
    assert status["strategy"]["center_buy"]["entry_rejected"] == 3
    assert status["strategy"]["center_buy"]["recommended_gate"] is True
    assert status["strategy"]["center_buy"]["recommended_gate_reasons"] == ["edge_compression"]
    assert status["strategy"]["opening_inertia"]["open_positions"] == 0
    assert status["strategy"]["opening_inertia"]["gated"] is True
    assert status["strategy"]["opening_inertia"]["no_trade_stage_counts"]["MARKET_FILTER"] == 2


def test_inv_status_normalizes_enum_backed_runtime_keys(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    portfolio = PortfolioState(
        bankroll=150.0,
        positions=[_position(chain_state="unknown", exit_state="")],
    )

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_connection", lambda: DummyConn())
    monkeypatch.setattr(status_summary_module, "load_tracker", lambda: type("T", (), {"accounting": {}})())
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})

    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())

    assert status["portfolio"]["positions"][0]["chain_state"] == "unknown"
    assert status["runtime"]["chain_state_counts"] == {"unknown": 1}
    assert status["runtime"]["exit_state_counts"] == {"none": 1}


def test_inv_status_passes_current_regime_start_to_learning_surface(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    portfolio = PortfolioState(bankroll=150.0)
    captured: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    class DummyTracker:
        accounting = {"current_regime_started_at": "2026-04-03T00:00:00+00:00"}

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_connection", lambda: DummyConn())
    monkeypatch.setattr(status_summary_module, "load_tracker", lambda: DummyTracker())
    def _fake_execution_summary(conn, not_before=None):
        captured["execution_not_before"] = not_before
        return {}

    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", _fake_execution_summary)
    def _fake_learning_surface(conn, not_before=None):
        captured["not_before"] = not_before
        return {"by_strategy": {}}

    monkeypatch.setattr(
        status_summary_module,
        "query_learning_surface_summary",
        _fake_learning_surface,
    )
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})

    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())

    assert captured["not_before"] == "2026-04-03T00:00:00+00:00"
    assert captured["execution_not_before"] == "2026-04-03T00:00:00+00:00"
    assert status["execution"]["current_regime_started_at"] == "2026-04-03T00:00:00+00:00"
    assert status["learning"]["current_regime_started_at"] == "2026-04-03T00:00:00+00:00"


def test_inv_write_status_preserves_cycle_when_refreshing_without_summary(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    status_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-02T00:00:00Z",
                "process": {"pid": 1, "mode": "paper", "version": "zeus_v2"},
                "risk": {"level": "GREEN"},
                "portfolio": {"open_positions": 0, "total_exposure_usd": 0.0},
                "cycle": {"entries_blocked_reason": "risk_level=ORANGE", "failed": False},
            }
        )
    )

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "get_connection", lambda: get_connection(db_path))

    status_summary_module.write_status()
    refreshed = json.loads(status_path.read_text())

    assert refreshed["cycle"]["entries_blocked_reason"] == "risk_level=ORANGE"


def test_inv_control_pause_stops_entries(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = paper_mode

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{"city": NYC}])
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: True)
    monkeypatch.setattr(control_plane_module, "process_commands", lambda: [])
    monkeypatch.setattr(status_summary_module, "write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entries should be paused")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["entries_paused"] is True


def test_inv_control_strategy_gate_persists_and_is_readable(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    control_path.write_text(
        json.dumps(
            {
                "commands": [{"command": "set_strategy_gate", "strategy": "opening_inertia", "enabled": False}],
                "acks": [],
            }
        )
    )

    processed = control_plane_module.process_commands()

    assert processed == ["set_strategy_gate"]
    assert control_plane_module.is_strategy_enabled("opening_inertia") is False
    assert control_plane_module.is_strategy_enabled("center_buy") is True


def test_inv_pause_entries_survives_control_state_refresh(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    control_plane_module.clear_control_state()
    control_path.write_text(json.dumps({"commands": [{"command": "pause_entries"}], "acks": []}))

    processed = control_plane_module.process_commands()

    assert processed == ["pause_entries"]
    assert control_plane_module.is_entries_paused() is True

    control_plane_module.clear_control_state()

    assert control_plane_module.is_entries_paused() is True


def test_inv_tighten_risk_survives_control_state_refresh_until_resume(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    control_plane_module.clear_control_state()
    control_path.write_text(json.dumps({"commands": [{"command": "tighten_risk"}], "acks": []}))

    processed = control_plane_module.process_commands()

    assert processed == ["tighten_risk"]
    assert control_plane_module.get_edge_threshold_multiplier() == 2.0

    control_plane_module.clear_control_state()

    assert control_plane_module.get_edge_threshold_multiplier() == 2.0

    control_path.write_text(
        json.dumps(
            {
                "commands": [{"command": "resume"}],
                "acks": json.loads(control_path.read_text())["acks"],
            }
        )
    )

    processed = control_plane_module.process_commands()

    assert processed == ["resume"]
    assert control_plane_module.is_entries_paused() is False
    assert control_plane_module.get_edge_threshold_multiplier() == 1.0

    control_plane_module.clear_control_state()

    assert control_plane_module.is_entries_paused() is False
    assert control_plane_module.get_edge_threshold_multiplier() == 1.0


def test_inv_supervisor_command_matches_real_control_plane_contract():
    cmd = SupervisorCommand(
        command="set_strategy_gate",
        reason="edge compression",
        strategy="opening_inertia",
        enabled=False,
    )
    assert cmd.command == "set_strategy_gate"
    assert cmd.strategy == "opening_inertia"
    assert cmd.enabled is False


def test_inv_recommended_commands_from_status_builds_explicit_control_actions():
    status = {
        "control": {
            "recommended_controls_not_applied": ["tighten_risk"],
            "recommended_control_reasons": {"tighten_risk": ["execution_decay(fill_rate=0.2, observed=12)"]},
            "recommended_but_not_gated": ["center_buy", "opening_inertia"],
            "gated_but_not_recommended": ["shoulder_sell"],
            "recommended_strategy_gate_reasons": {
                "center_buy": ["execution_decay(fill_rate=0.2, observed=12)"],
                "opening_inertia": ["edge_compression"],
            },
        }
    }

    commands = control_plane_module.recommended_commands_from_status(
        status,
        include_review_required=True,
    )

    assert commands == [
        {
            "command": "tighten_risk",
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
        {
            "command": "set_strategy_gate",
            "strategy": "center_buy",
            "enabled": False,
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
        {
            "command": "set_strategy_gate",
            "strategy": "opening_inertia",
            "enabled": False,
            "note": "recommended_by=edge_compression",
        },
        {
            "command": "set_strategy_gate",
            "strategy": "shoulder_sell",
            "enabled": True,
            "note": "recommended_by=gate_drift_resolved",
        },
    ]


def test_inv_recommended_autosafe_commands_excludes_review_required_strategy_gates():
    status = {
        "control": {
            "recommended_controls_not_applied": ["tighten_risk"],
            "recommended_control_reasons": {"tighten_risk": ["execution_decay(fill_rate=0.2, observed=12)"]},
            "recommended_but_not_gated": ["center_buy", "opening_inertia"],
        }
    }

    commands = control_plane_module.recommended_autosafe_commands_from_status(status)

    assert commands == [
        {
            "command": "tighten_risk",
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
    ]


def test_inv_enqueue_commands_avoids_duplicates(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    control_path.write_text(json.dumps({"commands": [{"command": "tighten_risk"}], "acks": []}))

    added = control_plane_module.enqueue_commands(
        [{"command": "tighten_risk"}, {"command": "set_strategy_gate", "strategy": "center_buy", "enabled": False}]
    )

    payload = json.loads(control_path.read_text())
    assert added == 1
    assert payload["commands"] == [
        {"command": "tighten_risk"},
        {"command": "set_strategy_gate", "strategy": "center_buy", "enabled": False},
    ]


def test_inv_apply_recommended_controls_defaults_to_autosafe_commands(monkeypatch, tmp_path, capsys):
    status_path = tmp_path / "status_summary.json"
    control_path = tmp_path / "control_plane.json"
    status_path.write_text(
        json.dumps(
            {
                "control": {
                    "recommended_auto_commands": [
                        {
                            "command": "tighten_risk",
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        }
                    ],
                    "review_required_commands": [
                        {
                            "command": "set_strategy_gate",
                            "strategy": "center_buy",
                            "enabled": False,
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        },
                        {
                            "command": "set_strategy_gate",
                            "strategy": "shoulder_sell",
                            "enabled": True,
                            "note": "recommended_by=gate_drift_resolved",
                        },
                    ],
                    "recommended_commands": [
                        {
                            "command": "tighten_risk",
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        },
                        {
                            "command": "set_strategy_gate",
                            "strategy": "center_buy",
                            "enabled": False,
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        },
                        {
                            "command": "set_strategy_gate",
                            "strategy": "shoulder_sell",
                            "enabled": True,
                            "note": "recommended_by=gate_drift_resolved",
                        },
                    ],
                }
            }
        )
    )
    control_path.write_text(json.dumps({"commands": [], "acks": []}))

    monkeypatch.setattr(apply_recommended_controls_script, "state_path", lambda name: status_path if name == "status_summary.json" else control_path)
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(apply_recommended_controls_script, "enqueue_commands", control_plane_module.enqueue_commands)

    rc = apply_recommended_controls_script.main([])
    output = json.loads(capsys.readouterr().out)
    payload = json.loads(control_path.read_text())

    assert rc == 0
    assert output["include_review_required"] is False
    assert output["added"] == 1
    assert payload["commands"] == [
        {
            "command": "tighten_risk",
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
    ]


def test_inv_apply_recommended_controls_can_include_review_required_commands(monkeypatch, tmp_path, capsys):
    status_path = tmp_path / "status_summary.json"
    control_path = tmp_path / "control_plane.json"
    status_path.write_text(
        json.dumps(
            {
                "control": {
                    "recommended_auto_commands": [
                        {
                            "command": "tighten_risk",
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        }
                    ],
                    "review_required_commands": [
                        {
                            "command": "set_strategy_gate",
                            "strategy": "center_buy",
                            "enabled": False,
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        },
                        {
                            "command": "set_strategy_gate",
                            "strategy": "shoulder_sell",
                            "enabled": True,
                            "note": "recommended_by=gate_drift_resolved",
                        },
                    ],
                    "recommended_commands": [
                        {
                            "command": "tighten_risk",
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        },
                        {
                            "command": "set_strategy_gate",
                            "strategy": "center_buy",
                            "enabled": False,
                            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
                        },
                        {
                            "command": "set_strategy_gate",
                            "strategy": "shoulder_sell",
                            "enabled": True,
                            "note": "recommended_by=gate_drift_resolved",
                        },
                    ],
                }
            }
        )
    )
    control_path.write_text(json.dumps({"commands": [], "acks": []}))

    monkeypatch.setattr(apply_recommended_controls_script, "state_path", lambda name: status_path if name == "status_summary.json" else control_path)
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(apply_recommended_controls_script, "enqueue_commands", control_plane_module.enqueue_commands)

    rc = apply_recommended_controls_script.main(["--include-review-required"])
    output = json.loads(capsys.readouterr().out)
    payload = json.loads(control_path.read_text())

    assert rc == 0
    assert output["include_review_required"] is True
    assert output["added"] == 3
    assert payload["commands"] == [
        {
            "command": "tighten_risk",
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
        {
            "command": "set_strategy_gate",
            "strategy": "center_buy",
            "enabled": False,
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
        {
            "command": "set_strategy_gate",
            "strategy": "shoulder_sell",
            "enabled": True,
            "note": "recommended_by=gate_drift_resolved",
        },
    ]


def test_inv_apply_recommended_controls_rejects_stale_status_contract(monkeypatch, tmp_path, capsys):
    status_path = tmp_path / "status_summary.json"
    control_path = tmp_path / "control_plane.json"
    status_path.write_text(json.dumps({"control": {"recommended_auto_commands": []}}))
    control_path.write_text(json.dumps({"commands": [], "acks": []}))

    monkeypatch.setattr(apply_recommended_controls_script, "state_path", lambda name: status_path if name == "status_summary.json" else control_path)
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(apply_recommended_controls_script, "enqueue_commands", control_plane_module.enqueue_commands)

    rc = apply_recommended_controls_script.main([])
    output = json.loads(capsys.readouterr().out)
    payload = json.loads(control_path.read_text())

    assert rc == 1
    assert output["reason"] == "stale_status_contract"
    assert "control.review_required_commands" in output["missing_keys"]
    assert payload["commands"] == []


def test_inv_kelly_uses_effective_bankroll(monkeypatch):
    captured: dict[str, float] = {}

    future_target = "2026-04-03"
    candidate = MarketCandidate(
        city=NYC,
        target_date=future_target,
        outcomes=[
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
                "price": 0.35,
            },
            {
                "title": "41-42°F",
                "range_low": 41,
                "range_high": 42,
                "token_id": "yes2",
                "no_token_id": "no2",
                "market_id": "m2",
                "price": 0.33,
            },
            {
                "title": "43°F or higher",
                "range_low": 43,
                "range_high": None,
                "token_id": "yes3",
                "no_token_id": "no3",
                "market_id": "m3",
                "price": 0.32,
            },
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    portfolio = PortfolioState(
        bankroll=150.0,
        recent_exits=[_recent_exit(3.0)],
        positions=[_position(bin_label="41-42°F", last_monitor_market_price=0.08)],
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None):
            self.member_maxes = np.full(51, 40.0)

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.60, 0.25, 0.15])

        def spread(self):
            from src.types.temperature import TemperatureDelta

            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

        def is_bimodal(self):
            return False

    class DummyAnalysis:
        def __init__(self, **kwargs):
            self.bins = kwargs["bins"]

        def find_edges(self, n_bootstrap=500):
            edge = BinEdge(
                bin=self.bins[0],
                direction="buy_yes",
                edge=0.12,
                ci_lower=0.05,
                ci_upper=0.15,
                p_model=0.60,
                p_market=0.35,
                p_posterior=0.47,
                entry_price=0.35,
                p_value=0.02,
                vwmp=0.35,
            )
            edge.forward_edge = edge.p_posterior - edge.p_market
            return [edge]

        def sigma_context(self):
            return {"city_name": "NYC", "season": "MAM", "forecast_source": "ecmwf_ifs025", "base_sigma": 0.5, "lead_multiplier": 1.1, "spread_multiplier": 1.05, "final_sigma": 0.5775}

        def mean_context(self):
            return {"city_name": "NYC", "season": "MAM", "forecast_source": "ecmwf_ifs025", "bias_corrected": False, "offset": 0.0, "lead_days": 1.5}

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=8, model=None: {
            "members_hourly": np.ones(((31 if model == "gfs025" else 51), 48)) * 40.0,
            "times": [
                datetime(2026, 4, 3, hour % 24, 0, tzinfo=timezone.utc).isoformat()
                for hour in range(48)
            ],
            "issue_time": datetime.now(timezone.utc),
            "fetch_time": datetime.now(timezone.utc),
            "model": model or "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap1")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)

    def _capture_kelly(p_posterior, entry_price, bankroll, kelly_mult):
        captured["bankroll"] = bankroll
        return 5.0

    monkeypatch.setattr(evaluator_module, "kelly_size", _capture_kelly)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=portfolio,
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            max_region_pct=0.35,
            min_order_usd=1.0,
        ),
    )

    assert decisions[0].should_trade is True
    assert captured["bankroll"] == pytest.approx(portfolio.effective_bankroll)
    epistemic = json.loads(decisions[0].epistemic_context_json)
    assert epistemic["forecast_context"]["uncertainty"]["forecast_source"] == "ecmwf_ifs025"
    assert epistemic["forecast_context"]["uncertainty"]["final_sigma"] == pytest.approx(0.5775)
    assert epistemic["forecast_context"]["location"]["season"] == "MAM"
    assert epistemic["forecast_context"]["location"]["bias_corrected"] is False
    assert epistemic["forecast_context"]["location"]["offset"] == 0.0


def test_inv_tighten_risk_reduces_kelly_multiplier(monkeypatch):
    captured: dict[str, float] = {}

    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None):
            self.member_maxes = np.full(51, 40.0)

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.60, 0.25, 0.15])

        def spread(self):
            from src.types.temperature import TemperatureDelta
            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

        def is_bimodal(self):
            return False

    class DummyAnalysis:
        def __init__(self, **kwargs):
            self.bins = kwargs["bins"]

        def find_edges(self, n_bootstrap=500):
            edge = BinEdge(
                bin=self.bins[0],
                direction="buy_yes",
                edge=0.12,
                ci_lower=0.05,
                ci_upper=0.15,
                p_model=0.60,
                p_market=0.35,
                p_posterior=0.47,
                entry_price=0.35,
                p_value=0.02,
                vwmp=0.35,
            )
            edge.forward_edge = edge.p_posterior - edge.p_market
            return [edge]

        def sigma_context(self):
            return {"base_sigma": 0.5, "lead_multiplier": 1.1, "spread_multiplier": 1.05, "final_sigma": 0.5775}

        def mean_context(self):
            return {"offset": 0.0, "lead_days": 1.5}

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=8, model=None: {
            "members_hourly": np.ones(((31 if model == "gfs025" else 51), 48)) * 40.0,
            "times": [datetime(2026, 4, 3, hour % 24, 0, tzinfo=timezone.utc).isoformat() for hour in range(48)],
            "issue_time": datetime.now(timezone.utc),
            "fetch_time": datetime.now(timezone.utc),
            "model": model or "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-tighten")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "get_edge_threshold_multiplier", lambda: 2.0)

    def _capture_kelly(p_posterior, entry_price, bankroll, kelly_mult):
        captured["kelly_mult"] = kelly_mult
        return 5.0

    monkeypatch.setattr(evaluator_module, "kelly_size", _capture_kelly)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            max_region_pct=0.35,
            min_order_usd=1.0,
        ),
    )

    assert decisions[0].should_trade is True
    assert captured["kelly_mult"] == pytest.approx(0.125)


def test_inv_evaluator_epistemic_context_includes_model_bias_reference(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT INTO model_bias (city, season, source, bias, mae, n_samples, discount_factor) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("NYC", "MAM", "ecmwf", 1.5, 2.0, 20, 0.7),
    )
    conn.commit()

    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None):
            self.member_maxes = np.full(51, 40.0)
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.60, 0.25, 0.15])

        def spread(self):
            from src.types.temperature import TemperatureDelta
            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

        def is_bimodal(self):
            return False

    class DummyAnalysis:
        def __init__(self, **kwargs):
            self.bins = kwargs["bins"]

        def find_edges(self, n_bootstrap=500):
            edge = BinEdge(
                bin=self.bins[0],
                direction="buy_yes",
                edge=0.12,
                ci_lower=0.05,
                ci_upper=0.15,
                p_model=0.60,
                p_market=0.35,
                p_posterior=0.47,
                entry_price=0.35,
                p_value=0.02,
                vwmp=0.35,
            )
            edge.forward_edge = edge.p_posterior - edge.p_market
            return [edge]

        def forecast_context(self):
            return {
                "uncertainty": {"forecast_source": "ecmwf", "final_sigma": 0.5},
                "location": {"forecast_source": "ecmwf", "bias_reference": {"source": "ecmwf", "bias": 1.5, "mae": 2.0, "n_samples": 20, "discount_factor": 0.7}},
            }

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=8, model=None: {
            "members_hourly": np.ones(((31 if model == "gfs025" else 51), 48)) * 40.0,
            "times": [datetime(2026, 4, 3, hour % 24, 0, tzinfo=timezone.utc).isoformat() for hour in range(48)],
            "issue_time": datetime.now(timezone.utc),
            "fetch_time": datetime.now(timezone.utc),
            "model": model or "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-bias")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=conn,
        portfolio=PortfolioState(bankroll=150.0),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(),
    )
    conn.close()

    epistemic = json.loads(decisions[0].epistemic_context_json)
    assert epistemic["forecast_context"]["location"]["bias_reference"]["bias"] == 1.5


def test_inv_daily_loss_enforced(monkeypatch, tmp_path):
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(zeus_db)
    init_schema(conn)
    conn.close()

    portfolio = PortfolioState(
        bankroll=150.0,
        daily_baseline_total=150.0,
        recent_exits=[_recent_exit(-13.0)],
    )

    def _fake_get_connection(path=None):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: portfolio)

    level = riskguard_module.tick()
    row = get_connection(risk_db).execute(
        "SELECT level, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert level == RiskLevel.RED
    assert row["level"] == RiskLevel.RED.value
    assert details["daily_loss"] == pytest.approx(13.0)
    assert details["total_pnl"] == pytest.approx(-13.0)


def test_inv_riskguard_reads_real_pnl(monkeypatch, tmp_path):
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(zeus_db)
    init_schema(conn)
    store_settlement_records(conn, [
        SettlementRecord(
            trade_id="trade-1",
            city="NYC",
            target_date="2026-04-01",
            range_label="39-40°F",
            direction="buy_yes",
            p_posterior=0.70,
            outcome=1,
            pnl=3.0,
        ),
        SettlementRecord(
            trade_id="trade-2",
            city="NYC",
            target_date="2026-04-02",
            range_label="41-42°F",
            direction="buy_yes",
            p_posterior=0.35,
            outcome=1,
            pnl=1.5,
        ),
    ])
    conn.close()

    portfolio = PortfolioState(bankroll=150.0)

    def _fake_get_connection(path=None):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: portfolio)

    riskguard_module.tick()
    row = get_connection(risk_db).execute(
        "SELECT win_rate, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert row["win_rate"] is None
    assert details["accuracy"] == pytest.approx(0.5)


def test_inv_settlement_flows_to_brier(monkeypatch, tmp_path):
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(zeus_db)
    init_schema(conn)
    store_settlement_records(conn, [
        SettlementRecord(
            trade_id="trade-1",
            city="NYC",
            target_date="2026-04-01",
            range_label="39-40°F",
            direction="buy_yes",
            p_posterior=0.80,
            outcome=0,
            pnl=-5.0,
        )
    ])
    conn.close()

    def _fake_get_connection(path=None):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))

    riskguard_module.tick()
    row = get_connection(risk_db).execute(
        "SELECT brier, details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert row["brier"] == pytest.approx(0.64)
    assert details["settlement_storage_source"] == "decision_log"
    assert details["settlement_row_storage_sources"] == ["decision_log"]



def test_inv_riskguard_prefers_canonical_position_events_settlement_source(monkeypatch, tmp_path):
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(zeus_db)
    init_schema(conn)

    pos = Position(
        trade_id="rt-settle-auth",
        market_id="m6",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        edge=0.21,
        decision_snapshot_id="snap-auth",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    conn.commit()
    conn.close()

    def _fake_get_connection(path=None):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))

    riskguard_module.tick()
    row = get_connection(risk_db).execute(
        "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert details["settlement_storage_source"] == "position_events"
    assert details["settlement_row_storage_sources"] == ["position_events"]
    assert details["settlement_sample_size"] == 1
    assert details["accuracy"] == pytest.approx(1.0)



def test_inv_riskguard_falls_back_to_legacy_settlement_source(monkeypatch, tmp_path):
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(zeus_db)
    init_schema(conn)
    store_settlement_records(conn, [
        SettlementRecord(
            trade_id="legacy-settle",
            city="NYC",
            target_date="2026-04-01",
            range_label="39-40°F",
            direction="buy_yes",
            p_posterior=0.58,
            outcome=1,
            pnl=12.5,
            decision_snapshot_id="legacy-snap",
            edge_source="center_buy",
            strategy="center_buy",
            settled_at="2026-04-01T23:00:00Z",
        )
    ])
    conn.close()

    def _fake_get_connection(path=None):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))

    riskguard_module.tick()
    row = get_connection(risk_db).execute(
        "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert details["settlement_storage_source"] == "decision_log"
    assert details["settlement_row_storage_sources"] == ["decision_log"]
    assert details["settlement_sample_size"] == 1


def test_inv_strategy_tracker_receives_trades(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = paper_mode

    calls: list[dict] = []

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    _market_list = [{
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 2.0,
        "hours_to_resolution": 30.0,
    }]
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: _market_list)
    monkeypatch.setattr("src.data.market_scanner.find_weather_markets", lambda **kwargs: _market_list)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: False)
    monkeypatch.setattr(control_plane_module, "process_commands", lambda: [])
    monkeypatch.setattr(status_summary_module, "write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: [EdgeDecision(
            should_trade=True,
            edge=BinEdge(
                bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
                direction="buy_yes",
                edge=0.12,
                ci_lower=0.05,
                ci_upper=0.15,
                p_model=0.60,
                p_market=0.35,
                p_posterior=0.47,
                entry_price=0.35,
                p_value=0.02,
                vwmp=0.35,
            ),
            tokens={"market_id": "m1", "token_id": "yes123", "no_token_id": "no456"},
            size_usd=5.0,
            decision_id="dec1",
            decision_snapshot_id="snap1",
            edge_source="settlement_capture",
            applied_validations=["ens_fetch"],
        )],
    )
    monkeypatch.setattr(
        cycle_runner,
        "create_execution_intent",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runner,
        "execute_intent",
        lambda *args, **kwargs: OrderResult(trade_id="trade-1", status="filled", fill_price=0.35, shares=14.29),
    )
    monkeypatch.setattr(
        "src.state.strategy_tracker.StrategyTracker.record_entry",
        lambda self, pos: calls.append(pos),
    )

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert calls, "StrategyTracker never received the filled trade"


def test_inv_harvester_triggers_refit(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    season = season_from_date("2026-04-01")
    for i in range(13):
        add_calibration_pair(
            conn,
            city="NYC",
            target_date=f"2026-04-{i+1:02d}",
            range_label=f"{30+i}-{31+i}",
            p_raw=0.10 + 0.02 * (i % 5),
            outcome=i % 2,
            lead_days=3.0,
            season=season,
            cluster=NYC.cluster,
            forecast_available_at="2026-03-30T01:00:00Z",
            settlement_value=None,
        )
    snapshot_id = _insert_snapshot(conn, "NYC", "2026-04-01", [0.65, 0.35])
    settled_pos = _position(
        trade_id="trade-1",
        target_date="2026-04-01",
        bin_label="39-40°F",
        decision_snapshot_id=snapshot_id,
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    log_settlement_event(conn, settled_pos, winning_bin="39-40°F", won=True, outcome=1)
    conn.commit()
    conn.close()

    event = {
        "title": "Highest temperature in New York City on April 1 2026",
        "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
        "markets": [
            {
                "question": "39-40°F",
                "winningOutcome": "Yes",
                "clobTokenIds": json.dumps(["yes1", "no1"]),
                "outcomePrices": json.dumps([1.0, 0.0]),
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps([0.0, 1.0]),
                "conditionId": "m2",
            },
        ],
    }

    monkeypatch.setattr(harvester_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(bankroll=150.0, positions=[]),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])

    result = harvester_module.run_harvester()
    conn = get_connection(db_path)
    row = conn.execute("SELECT n_samples FROM platt_models ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    assert result["pairs_created"] == 2
    assert row is not None
    assert row["n_samples"] >= 15


def test_inv_harvester_falls_back_to_open_portfolio_snapshot_when_no_durable_settlement_exists(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    snapshot_id = _insert_snapshot(conn, "NYC", "2026-04-01", [0.65, 0.35])
    conn.commit()
    conn.close()

    event = {
        "title": "Highest temperature in New York City on April 1 2026",
        "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
        "markets": [
            {
                "question": "39-40°F",
                "winningOutcome": "Yes",
                "clobTokenIds": json.dumps(["yes1", "no1"]),
                "outcomePrices": json.dumps([1.0, 0.0]),
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps([0.0, 1.0]),
                "conditionId": "m2",
            },
        ],
    }

    monkeypatch.setattr(harvester_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(
            bankroll=150.0,
            positions=[_position(
                trade_id="trade-open-fallback",
                target_date="2026-04-01",
                bin_label="39-40°F",
                decision_snapshot_id=snapshot_id,
            )],
        ),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])

    result = harvester_module.run_harvester()

    assert result["pairs_created"] == 0

    conn = get_connection(db_path)
    pair_count = conn.execute(
        "SELECT COUNT(*) AS n FROM calibration_pairs WHERE city = ? AND target_date = ?",
        ("NYC", "2026-04-01"),
    ).fetchone()["n"]
    snapshot_event = conn.execute(
        """
        SELECT details_json FROM chronicle
        WHERE event_type = 'SETTLEMENT_SNAPSHOT_SOURCE'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert pair_count == 0
    snapshot_details = json.loads(snapshot_event["details_json"])
    assert snapshot_details["context_count"] == 1
    assert snapshot_details["contexts"][0]["source"] == "portfolio_open_fallback"
    assert snapshot_details["contexts"][0]["authority_level"] == "working_state_fallback"
    assert snapshot_details["contexts"][0]["is_degraded"] is True
    assert snapshot_details["contexts"][0]["degraded_reason"] == "no_durable_settlement_snapshot"
    assert snapshot_details["contexts"][0]["learning_snapshot_ready"] is False


def test_inv_harvester_uses_legacy_decision_log_snapshot_before_open_portfolio(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    legacy_snapshot_id = _insert_snapshot(conn, "NYC", "2026-04-01", [0.65, 0.35])
    portfolio_snapshot_id = _insert_snapshot(
        conn,
        "NYC",
        "2026-04-01",
        [0.10, 0.90],
        issue_time="2026-03-30T06:00:00Z",
    )
    store_settlement_records(conn, [
        SettlementRecord(
            trade_id="legacy-settle",
            city="NYC",
            target_date="2026-04-01",
            range_label="39-40°F",
            direction="buy_yes",
            p_posterior=0.58,
            outcome=1,
            pnl=12.5,
            decision_snapshot_id=legacy_snapshot_id,
            edge_source="center_buy",
            strategy="center_buy",
            settled_at="2026-04-01T23:00:00Z",
        )
    ])
    conn.close()

    event = {
        "title": "Highest temperature in New York City on April 1 2026",
        "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
        "markets": [
            {
                "question": "39-40°F",
                "winningOutcome": "Yes",
                "clobTokenIds": json.dumps(["yes1", "no1"]),
                "outcomePrices": json.dumps([1.0, 0.0]),
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps([0.0, 1.0]),
                "conditionId": "m2",
            },
        ],
    }

    monkeypatch.setattr(harvester_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(
            bankroll=150.0,
            positions=[_position(
                trade_id="trade-open-ignored",
                target_date="2026-04-01",
                bin_label="39-40°F",
                decision_snapshot_id=portfolio_snapshot_id,
            )],
        ),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])

    result = harvester_module.run_harvester()

    assert result["pairs_created"] == 2

    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT range_label, p_raw
        FROM calibration_pairs
        WHERE city = ? AND target_date = ?
        ORDER BY range_label ASC
        """,
        ("NYC", "2026-04-01"),
    ).fetchall()
    snapshot_event = conn.execute(
        """
        SELECT details_json FROM chronicle
        WHERE event_type = 'SETTLEMENT_SNAPSHOT_SOURCE'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert [row["range_label"] for row in rows] == ["39-40°F", "41-42°F"]
    assert [row["p_raw"] for row in rows] == pytest.approx([0.65, 0.35])
    snapshot_details = json.loads(snapshot_event["details_json"])
    assert snapshot_details["contexts"][0]["source"] == "decision_log"
    assert snapshot_details["contexts"][0]["authority_level"] == "legacy_decision_log_fallback"
    assert snapshot_details["contexts"][0]["is_degraded"] is True


def test_inv_harvester_prefers_durable_snapshot_over_open_portfolio(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    durable_snapshot_id = _insert_snapshot(conn, "NYC", "2026-04-01", [0.65, 0.35])
    portfolio_snapshot_id = _insert_snapshot(
        conn,
        "NYC",
        "2026-04-01",
        [0.10, 0.90],
        issue_time="2026-03-30T06:00:00Z",
    )
    settled_pos = _position(
        trade_id="trade-durable-preferred",
        target_date="2026-04-01",
        bin_label="39-40°F",
        decision_snapshot_id=durable_snapshot_id,
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    log_settlement_event(conn, settled_pos, winning_bin="39-40°F", won=True, outcome=1)
    conn.commit()
    conn.close()

    event = {
        "title": "Highest temperature in New York City on April 1 2026",
        "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
        "markets": [
            {
                "question": "39-40°F",
                "winningOutcome": "Yes",
                "clobTokenIds": json.dumps(["yes1", "no1"]),
                "outcomePrices": json.dumps([1.0, 0.0]),
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps([0.0, 1.0]),
                "conditionId": "m2",
            },
        ],
    }

    monkeypatch.setattr(harvester_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(
            bankroll=150.0,
            positions=[_position(
                trade_id="trade-open-ignored",
                target_date="2026-04-01",
                bin_label="39-40°F",
                decision_snapshot_id=portfolio_snapshot_id,
            )],
        ),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])

    result = harvester_module.run_harvester()

    assert result["pairs_created"] == 2

    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT range_label, p_raw
        FROM calibration_pairs
        WHERE city = ? AND target_date = ?
        ORDER BY range_label ASC
        """,
        ("NYC", "2026-04-01"),
    ).fetchall()
    snapshot_event = conn.execute(
        """
        SELECT details_json FROM chronicle
        WHERE event_type = 'SETTLEMENT_SNAPSHOT_SOURCE'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert [row["range_label"] for row in rows] == ["39-40°F", "41-42°F"]
    assert [row["p_raw"] for row in rows] == pytest.approx([0.65, 0.35])

    assert durable_snapshot_id != portfolio_snapshot_id
    snapshot_details = json.loads(snapshot_event["details_json"])
    assert snapshot_details["contexts"][0]["source"] == "position_events"
    assert snapshot_details["contexts"][0]["authority_level"] == "durable_event"
    assert snapshot_details["contexts"][0]["is_degraded"] is False


def test_inv_harvester_marks_partial_context_resolution(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    good_snapshot_id = _insert_snapshot(conn, "NYC", "2026-04-01", [0.65, 0.35])
    good_pos = _position(
        trade_id="trade-good-context",
        target_date="2026-04-01",
        bin_label="39-40°F",
        decision_snapshot_id=good_snapshot_id,
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    bad_pos = _position(
        trade_id="trade-missing-context",
        target_date="2026-04-01",
        bin_label="41-42°F",
        decision_snapshot_id="",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=0.0,
        pnl=-3.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    log_settlement_event(conn, good_pos, winning_bin="39-40°F", won=True, outcome=1)
    log_settlement_event(conn, bad_pos, winning_bin="39-40°F", won=False, outcome=0)
    conn.commit()
    conn.close()

    event = {
        "title": "Highest temperature in New York City on April 1 2026",
        "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
        "markets": [
            {"question": "39-40°F", "winningOutcome": "Yes", "clobTokenIds": json.dumps(["yes1", "no1"]), "outcomePrices": json.dumps([1.0, 0.0]), "conditionId": "m1"},
            {"question": "41-42°F", "winningOutcome": "No", "clobTokenIds": json.dumps(["yes2", "no2"]), "outcomePrices": json.dumps([0.0, 1.0]), "conditionId": "m2"},
        ],
    }

    monkeypatch.setattr(harvester_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(harvester_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0, positions=[]))
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])

    result = harvester_module.run_harvester()
    assert result["pairs_created"] == 2

    conn = get_connection(db_path)
    snapshot_event = conn.execute(
        """
        SELECT details_json FROM chronicle
        WHERE event_type = 'SETTLEMENT_SNAPSHOT_SOURCE'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
    details = json.loads(snapshot_event["details_json"])
    assert details["partial_context_resolution"] is True
    assert details["dropped_context_count"] == 1
    assert details["dropped_rows"][0]["reason"] == "missing_decision_snapshot_id"
