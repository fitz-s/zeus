# Created: 2026-04-28
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md; task_2026-04-29 Phase 1A/1B evaluator gates; docs/operations/task_2026-05-07_object_invariance_wave24/PLAN.md.
# Lifecycle: created=2026-04-28; last_reviewed=2026-05-07; last_reused=2026-05-07
# Purpose: Cross-module P&L flow, CI-threshold, status-summary bankroll, and hardcoded-audit tests.
# Reuse: Run after P&L/status-summary/riskguard/reporting boundary changes.
"""Cross-module P&L flow, CI-threshold, and hardcoded-audit tests."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

import src.control.control_plane as control_plane_module
import src.engine.cycle_runner as cycle_runner
import src.engine.cycle_runtime as cycle_runtime
import src.engine.evaluator as evaluator_module
import src.engine.monitor_refresh as monitor_refresh
import src.main as main_module
import src.execution.harvester as harvester_module
import src.observability.status_summary as status_summary_module
import src.riskguard.riskguard as riskguard_module
import scripts.apply_recommended_controls as apply_recommended_controls_script
from src.control.gate_decision import GateDecision, ReasonCode
from src.supervisor_api.contracts import SupervisorCommand
from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair
from src.config import City
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import EdgeDecision, MarketCandidate
from src.execution.executor import OrderResult
from src.riskguard.risk_level import RiskLevel
from src.state.db import (
    apply_architecture_kernel_schema,
    get_connection,
    init_schema,
    log_settlement_event,
    query_portfolio_loader_view,
    query_position_current_status_view,
)
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
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis


def _ensure_auth_verified(conn) -> None:
    """Mark all calibration_pairs rows VERIFIED (init_schema now creates the column)."""
    conn.execute("UPDATE calibration_pairs SET authority = 'VERIFIED'")
    conn.commit()


def _allow_entry_gates_for_cycle_test(monkeypatch) -> None:
    """Open only test-local runtime gates needed to exercise entry materialization."""
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
    monkeypatch.setattr(cycle_runner.cutover_guard, "summary", lambda: {"state": "READY", "entry": {"allow_submit": True}})
    monkeypatch.setattr(
        "src.control.heartbeat_supervisor.summary",
        lambda: {"health": "OK", "entry": {"allow_submit": True}},
    )
    monkeypatch.setattr(
        "src.control.ws_gap_guard.summary",
        lambda: {
            "subscription_state": "CONNECTED",
            "gap_reason": "",
            "m5_reconcile_required": False,
            "entry": {"allow_submit": True},
        },
    )
    monkeypatch.setattr(
        "src.risk_allocator.refresh_global_allocator",
        lambda *args, **kwargs: {"entry": {"allow_submit": True}},
    )
    monkeypatch.setattr("src.runtime.posture.read_runtime_posture", lambda: "NORMAL")


def _patch_mature_calibration(monkeypatch, *, level: int = 1) -> None:
    from src.contracts.alpha_decision import AlphaDecision

    class _Calibrator:
        pass

    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (_Calibrator(), level))
    monkeypatch.setattr(
        evaluator_module,
        "calibrate_and_normalize",
        lambda p_raw, *args, **kwargs: np.array(p_raw, dtype=float).copy(),
    )
    monkeypatch.setattr(
        evaluator_module,
        "compute_alpha",
        lambda *args, **kwargs: AlphaDecision(
            value=0.5,
            optimization_target="risk_cap",
            evidence_basis="pnl flow mature calibration fixture",
            ci_bound=0.05,
        ),
    )
    # Oracle fail-closed gate removed 2026-05-02 (PR-B). Previous monkeypatch
    # bypassed _oracle_evidence_rejection_reason; gate no longer exists.


def _insert_source_correct_harvester_obs(
    conn,
    *,
    city: City | None = None,
    target_date: str = "2026-04-01",
    high_temp: float = 40.0,
) -> None:
    """Seed a source-family-correct observation row for harvester live tests."""
    city = city or NYC
    conn.execute(
        """
        INSERT INTO observations (
            city, target_date, source, high_temp, unit, station_id, fetched_at, authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city.name,
            target_date,
            "wu_icao_history",
            high_temp,
            city.settlement_unit,
            city.wu_station,
            "2026-04-01T23:00:00Z",
            "VERIFIED",
        ),
    )


def _enable_live_harvester_test_path(monkeypatch, *, mock_lookup: bool = False) -> None:
    """Enable flag-gated harvester tests without depending on Batch-A schema parity."""
    monkeypatch.setenv("ZEUS_HARVESTER_LIVE_ENABLED", "1")

    def _assert_source_family_and_skip_schema_write(
        conn,
        city,
        target_date,
        pm_bin_lo,
        pm_bin_hi,
        *,
        event_slug="",
        obs_row=None,
        **_kwargs,
    ):
        assert obs_row is not None
        assert obs_row["source"] == "wu_icao_history"
        return {
            "authority": "VERIFIED",
            "settlement_value": obs_row["high_temp"],
            "winning_bin": f"{int(pm_bin_lo)}-{int(pm_bin_hi)}°{city.settlement_unit}",
            "reason": None,
        }

    monkeypatch.setattr(
        harvester_module,
        "_write_settlement_truth",
        _assert_source_family_and_skip_schema_write,
    )
    if mock_lookup:
        monkeypatch.setattr(
            harvester_module,
            "_lookup_settlement_obs",
            lambda conn, city, target_date, **_kwargs: {
                "id": 1,
                "source": "wu_icao_history",
                "high_temp": 40.0,
                "unit": city.settlement_unit,
                "fetched_at": "2026-04-01T23:00:00Z",
            },
        )


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="NYC",
    settlement_unit="F",
    wu_station="KLGA",
)

MISSING = object()


def _stub_full_family_scan(monkeypatch) -> None:
    def _scan(analysis, *args, **kwargs):
        selected_method = getattr(analysis, "selected_method", "test_fixture")
        hypotheses = []
        for i, edge in enumerate(analysis.find_edges(n_bootstrap=kwargs.get("n_bootstrap", 0))):
            edge.selected_method = getattr(edge, "selected_method", selected_method)
            assert edge.selected_method
            # B4 Phase 5 (2026-05-01): match production behavior — hypothesis index
            # mirrors the bin's support_index in analysis.bins, not the enumeration
            # index of edges that crossed the > 0 threshold. Mismatch surfaces as
            # FDR_SELECTION_UNEXECUTABLE because _filter_executable_selected_edges
            # checks (edge.support_index, direction) keys.
            edge_support_index = getattr(edge, "support_index", None)
            hypothesis_index = int(edge_support_index) if edge_support_index is not None else i
            hypotheses.append(
                FullFamilyHypothesis(
                    index=hypothesis_index,
                    range_label=edge.bin.label,
                    direction=edge.direction,
                    edge=edge.edge,
                    ci_lower=edge.ci_lower,
                    ci_upper=edge.ci_upper,
                    p_value=edge.p_value,
                    p_model=edge.p_model,
                    p_market=edge.p_market,
                    p_posterior=edge.p_posterior,
                    entry_price=edge.entry_price,
                    is_shoulder=bool(getattr(edge.bin, "is_shoulder", False)),
                    passed_prefilter=True,
                )
            )
        return hypotheses

    monkeypatch.setattr(evaluator_module, "scan_full_hypothesis_family", _scan)


def _position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="NYC",
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


def _policy_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


def _insert_risk_action_row(
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


def _insert_position_current_row(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy_key: str,
    phase: str = "active",
    size_usd: float = 0.0,
    shares: float = 0.0,
    cost_basis_usd: float = 0.0,
    entry_price: float | None = None,
    mark_price: float | None = None,
    city: str = "NYC",
    direction: str = "buy_yes",
    bin_label: str = "39-40°F",
    chain_state: str = "unknown",
    decision_snapshot_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at, temperature_metric
        ) VALUES (?, ?, ?, 'm-test', ?, 'NYC', '2026-04-01', ?, ?, 'F', ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, '', ?, '', '', ?, '', '', ?, 'high')
        """,
        (
            position_id,
            phase,
            position_id,
            city,
            bin_label,
            direction,
            size_usd,
            shares,
            cost_basis_usd,
            entry_price,
            mark_price,
            decision_snapshot_id,
            strategy_key,
            chain_state,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _insert_strategy_health_row(
    conn: sqlite3.Connection,
    *,
    strategy_key: str,
    as_of: str,
    open_exposure_usd: float = 0.0,
    settled_trades_30d: int = 0,
    realized_pnl_30d: float = 0.0,
    unrealized_pnl: float = 0.0,
    win_rate_30d: float | None = None,
    fill_rate_14d: float | None = None,
    execution_decay_flag: int = 0,
    edge_compression_flag: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO strategy_health (
            strategy_key, as_of, open_exposure_usd, settled_trades_30d, realized_pnl_30d,
            unrealized_pnl, win_rate_30d, brier_30d, fill_rate_14d, edge_trend_30d,
            risk_level, execution_decay_flag, edge_compression_flag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
        """,
        (
            strategy_key,
            as_of,
            open_exposure_usd,
            settled_trades_30d,
            realized_pnl_30d,
            unrealized_pnl,
            win_rate_30d,
            fill_rate_14d,
            execution_decay_flag,
            edge_compression_flag,
        ),
    )


def _insert_control_override_row(
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
    data_version: str = "tigge_mx2t6_local_calendar_day_max_v1",
    temperature_metric: str = "high",
) -> str:
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version,
         data_version, temperature_metric)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            temperature_metric,
        ),
    )
    row = conn.execute("SELECT last_insert_rowid() AS snapshot_id").fetchone()
    snapshot_id = int(row["snapshot_id"])
    physical_quantity = (
        "mn2t6_local_calendar_day_min"
        if temperature_metric == "low"
        else "mx2t6_local_calendar_day_max"
    )
    observation_field = "low_temp" if temperature_metric == "low" else "high_temp"
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2
        (snapshot_id, city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version,
         data_version, training_allowed, causality_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            city,
            target_date,
            temperature_metric,
            physical_quantity,
            observation_field,
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
            1,
            "OK",
        ),
    )
    conn.commit()
    return str(snapshot_id)


def _make_ens_result_with_evidence(
    *, model: str | None = None, target_date: str = "2026-04-03",
) -> dict:
    """Build an ens_result dict satisfying the executable-entry forecast-evidence
    contract (`src/engine/evaluator.py:_entry_forecast_evidence_errors`).

    B4 Phase 5 (2026-05-01): the entry path now requires source_id,
    degradation_level, forecast_source_role, authority_tier, raw_payload_hash,
    plus ordered timestamps (issue ≤ available ≤ fetch). Lambdas that returned
    only members_hourly/times/issue_time/fetch_time/model now fail SIGNAL_QUALITY.

    Time invariants enforced by `_entry_forecast_evidence_errors`:
      issue_time ≤ fetch_time
      issue_time ≤ available_at
      available_at ≤ fetch_time
      issue_time / fetch_time / available_at ≤ decision_time (now)
    """
    return {
        "members_hourly": np.ones(((31 if model == "gfs025" else 51), 48)) * 40.0,
        "times": [
            datetime(2026, 4, 3, hour % 24, 0, tzinfo=timezone.utc).isoformat()
            for hour in range(48)
        ],
        "issue_time": "2026-04-02T00:00:00+00:00",
        "available_at": "2026-04-02T07:55:00+00:00",
        "fetch_time": "2026-04-02T08:00:00+00:00",
        "valid_time": f"{target_date}T12:00:00+00:00",
        "model": model or "ecmwf_ifs025",
        "source_id": "ecmwf_ifs025",
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "raw_payload_hash": "a" * 64,
    }


def _lookup_nested(data: dict, dotted_path: str):
    current = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


@pytest.mark.skip(reason="BI-05")
def test_unrealized_pnl_updates_with_market():
    pos = _position()
    assert pos.unrealized_pnl == 0.0

    pos.last_monitor_market_price = 0.15
    assert pos.unrealized_pnl == pytest.approx(2.50)

    pos.last_monitor_market_price = 0.05
    assert pos.unrealized_pnl == pytest.approx(-2.50)


@pytest.mark.skip(reason="BI-05")
def test_total_pnl_combines_realized_and_unrealized():
    portfolio = PortfolioState(
        bankroll=211.37,
        recent_exits=[_recent_exit(3.0)],
    )

    open_pos = _position()
    open_pos.last_monitor_market_price = 0.08
    portfolio.positions.append(open_pos)

    assert portfolio.realized_pnl == pytest.approx(3.0)
    assert portfolio.total_unrealized_pnl == pytest.approx(-1.0)
    assert portfolio.total_pnl == pytest.approx(2.0)
    assert portfolio.initial_bankroll == pytest.approx(152.0)


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


@pytest.mark.skip(reason="BI-05")
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
        def get_best_bid_ask(self, token_id):
            assert token_id == "yes123"
            return 0.62, 0.62, 100.0, 100.0

    monkeypatch.setattr(
        monitor_refresh,
        "recompute_native_probability",
        lambda position, current_p_market, registry, **context: position.p_posterior,
    )

    pos = _position(last_monitor_market_price=None, last_monitor_at="")
    pos.entry_method = getattr(pos, "entry_method", "ens_member_counting") or "ens_member_counting"
    assert pos.entry_method
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
    apply_architecture_kernel_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    open_pos = _position()
    open_pos.strategy = "center_buy"
    open_pos.last_monitor_market_price = 0.13
    open_pos.chain_state = "exit_pending_missing"
    open_pos.exit_state = "retry_pending"
    open_pos.entry_fill_verified = False
    open_pos.state = "day0_window"
    _insert_position_current_row(
        conn,
        position_id=open_pos.trade_id,
        strategy_key="center_buy",
        phase="day0_window",
        size_usd=open_pos.size_usd,
        shares=open_pos.shares,
        cost_basis_usd=open_pos.cost_basis_usd,
        entry_price=open_pos.entry_price,
        mark_price=open_pos.last_monitor_market_price,
        city=open_pos.city,
        direction=open_pos.direction,
        bin_label=open_pos.bin_label,
        chain_state=open_pos.chain_state,
        decision_snapshot_id=open_pos.decision_snapshot_id,
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, decision_id, snapshot_id, order_id,
            command_id, caused_by, idempotency_key, venue_status, source_module, payload_json
        ) VALUES (?, ?, 1, 1, ?, ?, 'day0_window', 'pending_exit', ?, NULL, ?, NULL, NULL, ?, ?, NULL, 'tests', ?)
        """,
        (
            f"{open_pos.trade_id}:retry",
            open_pos.trade_id,
            "EXIT_ORDER_REJECTED",
            now,
            "center_buy",
            open_pos.decision_snapshot_id,
            "retry_pending",
            f"{open_pos.trade_id}:retry",
            json.dumps({"status": "retry_pending", "entry_fill_verified": False}),
        ),
    )
    _insert_strategy_health_row(
        conn,
        strategy_key="center_buy",
        as_of=now,
        open_exposure_usd=open_pos.size_usd,
        unrealized_pnl=1.5,
    )
    _insert_strategy_health_row(
        conn,
        strategy_key="opening_inertia",
        as_of=now,
        realized_pnl_30d=-2.3,
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(
        status_summary_module,
        "_get_risk_details",
        lambda: {
            "effective_bankroll": 149.2,
            "realized_pnl": -2.3,
            "unrealized_pnl": 1.5,
            "total_pnl": -0.8,
            "execution_quality_level": "YELLOW",
            "recommended_strategy_gates": ["center_buy"],
            "recommended_strategy_gate_reasons": {"center_buy": ["execution_decay(fill_rate=0.2, observed=12)"]},
            "recommended_controls": ["tighten_risk"],
            "recommended_control_reasons": {"tighten_risk": ["execution_decay(fill_rate=0.2, observed=12)"]},
        },
    )
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: True)
    monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: "auto_exception")
    monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: "auto_pause:ValueError")
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 2.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {"opening_inertia": GateDecision(enabled=False, reason_code=ReasonCode.UNSPECIFIED, reason_snapshot={}, gated_at="", gated_by="unknown")})
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [{"rejection_stage": "EDGE_INSUFFICIENT"}])

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
    assert status["control"]["entries_pause_source"] == "auto_exception"
    assert status["control"]["entries_pause_reason"] == "auto_pause:ValueError"
    assert status["control"]["edge_threshold_multiplier"] == 2.0
    assert status["control"]["strategy_gates"]["opening_inertia"]["enabled"] is False
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
    # K3: gated_but_not_recommended → auto un-gate loop was removed; only gate-OFF
    # commands (for recommended_but_not_gated) appear in review_required_commands.
    assert status["control"]["review_required_commands"] == [
        {
            "command": "set_strategy_gate",
            "strategy": "center_buy",
            "enabled": False,
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
    ]
    assert status["control"]["recommended_commands"] == [
        {
            "command": "set_strategy_gate",
            "strategy": "center_buy",
            "enabled": False,
            "note": "recommended_by=execution_decay(fill_rate=0.2, observed=12)",
        },
    ]
    assert status["runtime"]["chain_state_counts"]["exit_pending_missing"] == 1
    assert status["runtime"]["exit_state_counts"]["retry_pending"] == 1
    assert status["runtime"]["unverified_entries"] == 1
    assert status["runtime"]["day0_positions"] == 1
    assert "overall" in status["execution"]
    assert status["no_trade"]["recent_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert status["learning"] == {"by_strategy": {}}
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

    class BrokenConn:
        def close(self):
            return None

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: BrokenConn())
    monkeypatch.setattr(
        status_summary_module,
        "query_position_current_status_view",
        lambda conn: {
            "status": "missing_table",
            "positions": [],
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "strategy_open_counts": {},
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        },
    )
    monkeypatch.setattr(
        status_summary_module,
        "query_strategy_health_snapshot",
        lambda conn, now=None: {"status": "stale", "by_strategy": {}, "stale_strategy_keys": ["center_buy"]},
    )

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

    assert status["risk"]["infrastructure_level"] == "RED"
    assert status["risk"]["riskguard_level"] == "GREEN"
    assert status["risk"]["consistency_check"]["ok"] is False
    assert "cycle_risk_level_mismatch:ORANGE->GREEN" in status["risk"]["consistency_check"]["issues"]
    assert "cycle_failed" in status["risk"]["consistency_check"]["issues"]
    assert "execution_summary_unavailable" in status["risk"]["consistency_check"]["issues"]
    assert "position_current_missing_table" in status["risk"]["consistency_check"]["issues"]
    assert "strategy_health_stale" in status["risk"]["consistency_check"]["issues"]
    assert status["truth"]["db_primary_inputs"] == {
        "position_current": "missing_table",
        "strategy_health": "stale",
    }


def test_status_summary_projects_monitor_chain_missing_as_infrastructure_red(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
    monkeypatch.setattr(
        status_summary_module,
        "query_position_current_status_view",
        lambda conn: {
            "status": "ok",
            "positions": [],
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "strategy_open_counts": {},
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        },
    )
    monkeypatch.setattr(
        status_summary_module,
        "query_strategy_health_snapshot",
        lambda conn, now=None: {"status": "fresh", "by_strategy": {}, "stale_strategy_keys": []},
    )
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})

    status_summary_module.write_status({"mode": "test", "monitor_chain_missing": 2})
    status = json.loads(status_path.read_text())

    assert status["risk"]["infrastructure_level"] == "RED"
    assert "cycle_monitor_chain_missing:2" in status["risk"]["infrastructure_issues"]


def test_inv_status_strategy_merges_learning_surface(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    as_of = datetime.now(timezone.utc).isoformat()
    _insert_position_current_row(
        conn,
        position_id="t1",
        strategy_key="center_buy",
        size_usd=5.0,
        shares=50.0,
        cost_basis_usd=5.0,
        entry_price=0.1,
        mark_price=0.13,
        decision_snapshot_id="snap-1",
    )
    _insert_strategy_health_row(
        conn,
        strategy_key="center_buy",
        as_of=as_of,
        open_exposure_usd=5.0,
        unrealized_pnl=1.5,
        settled_trades_30d=2,
        realized_pnl_30d=1.25,
        win_rate_30d=0.5,
    )
    _insert_strategy_health_row(
        conn,
        strategy_key="opening_inertia",
        as_of=as_of,
        settled_trades_30d=0,
        realized_pnl_30d=0.0,
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(
        status_summary_module,
        "_get_risk_details",
        lambda: {
            "recommended_strategy_gates": ["center_buy"],
            "recommended_strategy_gate_reasons": {"center_buy": ["edge_compression"]},
        },
    )
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {})
    monkeypatch.setattr(
        status_summary_module,
        "query_learning_surface_summary",
        lambda conn, not_before=None: {
            "by_strategy": {
                "center_buy": {
                    "settlement_count": 99,
                    "settlement_pnl": 99.0,
                    "settlement_accuracy": 0.99,
                    "no_trade_count": 3,
                    "no_trade_stage_counts": {"EDGE_INSUFFICIENT": 2, "RISK_REJECTED": 1},
                    "entry_attempted": 4,
                    "entry_filled": 1,
                    "entry_rejected": 3,
                },
                "opening_inertia": {
                    "settlement_count": 8,
                    "settlement_pnl": 8.0,
                    "settlement_accuracy": 0.8,
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
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {"opening_inertia": GateDecision(enabled=False, reason_code=ReasonCode.UNSPECIFIED, reason_snapshot={}, gated_at="", gated_by="unknown")})

    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())

    assert status["strategy"]["center_buy"]["settlement_count"] == 2
    assert status["strategy"]["center_buy"]["settlement_pnl"] == 1.25
    assert status["strategy"]["center_buy"]["settlement_accuracy"] == 0.5
    assert status["strategy"]["center_buy"]["settlement_source"] == "strategy_health"
    assert status["strategy"]["center_buy"]["settlement_window"] == "30d"
    assert status["strategy"]["center_buy"]["learning_settlement_count"] == 99
    assert status["strategy"]["center_buy"]["learning_settlement_pnl"] == 99.0
    assert status["strategy"]["center_buy"]["learning_settlement_accuracy"] == 0.99
    assert status["strategy"]["center_buy"]["learning_settlement_source"] == "learning_surface"
    assert status["strategy"]["center_buy"]["no_trade_count"] == 3
    assert status["strategy"]["center_buy"]["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 2
    assert status["strategy"]["center_buy"]["entry_rejected"] == 3
    assert status["strategy"]["center_buy"]["recommended_gate"] is True
    assert status["strategy"]["center_buy"]["recommended_gate_reasons"] == ["edge_compression"]
    assert status["strategy"]["opening_inertia"]["open_positions"] == 0
    assert status["strategy"]["opening_inertia"]["settlement_count"] == 0
    assert status["strategy"]["opening_inertia"]["settlement_pnl"] == 0.0
    assert status["strategy"]["opening_inertia"]["learning_settlement_count"] == 8
    assert status["strategy"]["opening_inertia"]["learning_settlement_pnl"] == 8.0
    assert status["strategy"]["opening_inertia"]["gated"] is True
    assert status["strategy"]["opening_inertia"]["no_trade_stage_counts"]["MARKET_FILTER"] == 2


def test_inv_status_normalizes_enum_backed_runtime_keys(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    as_of = datetime.now(timezone.utc).isoformat()
    _insert_position_current_row(
        conn,
        position_id="t1",
        strategy_key="center_buy",
        size_usd=5.0,
        shares=50.0,
        cost_basis_usd=5.0,
        entry_price=0.1,
        mark_price=0.13,
        chain_state="unknown",
    )
    _insert_strategy_health_row(
        conn,
        strategy_key="center_buy",
        as_of=as_of,
        open_exposure_usd=5.0,
        unrealized_pnl=1.5,
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
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
    captured: dict[str, object] = {}
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    _insert_position_current_row(conn, position_id="t1", strategy_key="center_buy", size_usd=5.0)
    _insert_strategy_health_row(conn, strategy_key="center_buy", as_of="2026-04-04T00:00:00+00:00", open_exposure_usd=5.0)
    conn.commit()
    conn.close()

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(
        status_summary_module,
        "_get_risk_details",
        lambda: {"strategy_tracker_accounting": {"current_regime_started_at": "2026-04-03T00:00:00+00:00"}},
    )
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
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
    assert status["truth"]["compatibility_inputs"]["strategy_tracker_current_regime_started_at"] == "2026-04-03T00:00:00+00:00"


def test_inv_status_fallback_bankroll_uses_initial_bankroll(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    as_of = datetime.now(timezone.utc).isoformat()
    _insert_position_current_row(
        conn,
        position_id="t1",
        strategy_key="center_buy",
        size_usd=5.0,
        shares=50.0,
        cost_basis_usd=5.0,
        entry_price=0.1,
        mark_price=0.13,
    )
    _insert_strategy_health_row(
        conn,
        strategy_key="center_buy",
        as_of=as_of,
        open_exposure_usd=5.0,
        realized_pnl_30d=1.25,
        unrealized_pnl=1.5,
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})

    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())
    # 2026-05-04: status_summary now derives initial_bankroll from
    # bankroll_provider.current() (the conftest.py fixture returns wallet truth),
    # not from the removed config-cap literal.
    expected_initial = 211.37
    expected_total = 1.25 + 1.5

    assert status["portfolio"]["initial_bankroll"] == pytest.approx(expected_initial)
    assert status["portfolio"]["total_pnl"] == pytest.approx(expected_total)
    assert status["portfolio"]["effective_bankroll"] == pytest.approx(expected_initial)
    assert status["portfolio"]["effective_bankroll_derivation"] == "wallet_equity_no_pnl"
    assert status["truth"]["compatibility_inputs"]["bankroll_fallback_source"] == "bankroll_provider"


def test_inv_write_status_preserves_cycle_when_refreshing_without_summary(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    _insert_position_current_row(conn, position_id="t1", strategy_key="center_buy", size_usd=5.0)
    _insert_strategy_health_row(conn, strategy_key="center_buy", as_of="2026-04-04T00:00:00+00:00", open_exposure_usd=5.0)
    conn.close()
    status_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-02T00:00:00Z",
                "process": {"pid": 1, "mode": "live", "version": "zeus_v2"},
                "risk": {"level": "GREEN"},
                "portfolio": {"open_positions": 0, "total_exposure_usd": 0.0},
                "cycle": {"entries_blocked_reason": "risk_level=ORANGE", "failed": False},
            }
        )
    )

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
    monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])

    status_summary_module.write_status()
    refreshed = json.loads(status_path.read_text())

    assert refreshed["cycle"]["entries_blocked_reason"] == "risk_level=ORANGE"


def test_inv_write_status_drops_stale_pause_cycle_when_refreshing_after_resume(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    _insert_position_current_row(conn, position_id="t1", strategy_key="center_buy", size_usd=5.0)
    _insert_strategy_health_row(conn, strategy_key="center_buy", as_of="2026-04-04T00:00:00+00:00", open_exposure_usd=5.0)
    conn.close()
    status_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-02T00:00:00Z",
                "process": {"pid": 1, "mode": "live", "version": "zeus_v2"},
                "risk": {"level": "GREEN"},
                "portfolio": {"open_positions": 0, "total_exposure_usd": 0.0},
                "cycle": {
                    "entries_paused": True,
                    "entries_pause_reason": "auto_pause:ValueError",
                    "entries_blocked_reason": "entries_paused",
                    "failed": False,
                },
            }
        )
    )

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
    monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])

    status_summary_module.write_status()
    refreshed = json.loads(status_path.read_text())

    assert "entries_paused" not in refreshed["cycle"]
    assert "entries_pause_reason" not in refreshed["cycle"]
    assert "entries_blocked_reason" not in refreshed["cycle"]
    assert refreshed["control"]["entries_paused"] is False
    assert refreshed["control"]["entries_pause_source"] is None
    assert refreshed["control"]["entries_pause_reason"] is None


def test_inv_write_status_overrides_stale_blocker_when_refreshing_during_pause(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    _insert_position_current_row(conn, position_id="t1", strategy_key="center_buy", size_usd=5.0)
    _insert_strategy_health_row(conn, strategy_key="center_buy", as_of="2026-04-04T00:00:00+00:00", open_exposure_usd=5.0)
    conn.close()
    status_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-02T00:00:00Z",
                "process": {"pid": 1, "mode": "live", "version": "zeus_v2"},
                "risk": {"level": "GREEN"},
                "portfolio": {"open_positions": 0, "total_exposure_usd": 0.0},
                "cycle": {
                    "entries_blocked_reason": "risk_level=ORANGE",
                    "failed": False,
                },
            }
        )
    )

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: True)
    monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: "manual_command")
    monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(db_path))
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])

    status_summary_module.write_status()
    refreshed = json.loads(status_path.read_text())

    assert refreshed["cycle"]["entries_paused"] is True
    assert refreshed["cycle"]["entries_blocked_reason"] == "entries_paused"
    assert "entries_pause_reason" not in refreshed["cycle"]
    assert refreshed["control"]["entries_pause_source"] == "manual_command"


def test_inv_status_surfaces_db_substrate_degradation(monkeypatch, tmp_path):
    status_path = tmp_path / "status_summary.json"

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
    monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
    monkeypatch.setattr(
        status_summary_module,
        "query_position_current_status_view",
        lambda conn: {
            "status": "missing_table",
            "positions": [],
            "open_positions": 0,
            "total_exposure_usd": 0.0,
            "unrealized_pnl": 0.0,
            "strategy_open_counts": {},
            "chain_state_counts": {},
            "exit_state_counts": {},
            "unverified_entries": 0,
            "day0_positions": 0,
        },
    )
    monkeypatch.setattr(
        status_summary_module,
        "query_strategy_health_snapshot",
        lambda conn, now=None: {"status": "stale", "by_strategy": {}, "stale_strategy_keys": ["center_buy"]},
    )
    monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
    monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})

    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())

    assert status["risk"]["infrastructure_level"] == "RED"
    assert "position_current_missing_table" in status["risk"]["consistency_check"]["issues"]
    assert "strategy_health_stale" in status["risk"]["consistency_check"]["issues"]
    assert status["truth"]["db_primary_inputs"] == {
        "position_current": "missing_table",
        "strategy_health": "stale",
    }


def test_inv_control_pause_stops_entries(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    class DummyClob:
        def __init__(self):
            pass

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda *args, **kwargs: None)
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
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    conn.close()
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(control_plane_module, "get_world_connection", lambda: get_connection(db_path))
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
    control_plane_module.clear_control_state()
    assert control_plane_module.is_strategy_enabled("opening_inertia") is False
    row = get_connection(db_path).execute(
        "SELECT value, issued_by, precedence FROM control_overrides WHERE override_id = 'control_plane:strategy:opening_inertia:gate'"
    ).fetchone()
    assert row["value"] == "true"
    assert row["issued_by"] == "control_plane"
    assert row["precedence"] == 100


def test_inv_pause_entries_survives_control_state_refresh(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    conn.close()
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(control_plane_module, "get_world_connection", lambda: get_connection(db_path))
    control_plane_module.clear_control_state()
    control_path.write_text(json.dumps({"commands": [{"command": "pause_entries"}], "acks": []}))

    processed = control_plane_module.process_commands()

    assert processed == ["pause_entries"]
    assert control_plane_module.is_entries_paused() is True
    assert control_plane_module.get_entries_pause_source() == "manual_command"
    assert control_plane_module.get_entries_pause_reason() == "control_plane:pause_entries"

    control_plane_module.clear_control_state()

    assert control_plane_module.is_entries_paused() is True
    assert control_plane_module.get_entries_pause_source() == "manual_command"
    assert control_plane_module.get_entries_pause_reason() == "control_plane:pause_entries"
    row = get_connection(db_path).execute(
        "SELECT value, issued_by, reason, precedence FROM control_overrides WHERE override_id = 'control_plane:global:entries_paused'"
    ).fetchone()
    assert row["value"] == "true"
    assert row["issued_by"] == "control_plane"
    assert row["reason"] == "control_plane:pause_entries"
    assert row["precedence"] == 100


def test_inv_pause_entries_source_is_manual_for_custom_issuer(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    conn.close()
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(control_plane_module, "get_world_connection", lambda: get_connection(db_path))
    control_plane_module.clear_control_state()
    control_path.write_text(
        json.dumps(
            {
                "commands": [
                    {"command": "pause_entries", "issued_by": "ui", "note": "operator requested pause"}
                ],
                "acks": [],
            }
        )
    )

    processed = control_plane_module.process_commands()

    assert processed == ["pause_entries"]
    assert control_plane_module.is_entries_paused() is True
    assert control_plane_module.get_entries_pause_source() == "manual_command"
    assert control_plane_module.get_entries_pause_reason() == "operator requested pause"
    row = get_connection(db_path).execute(
        "SELECT issued_by, reason FROM control_overrides WHERE override_id = 'control_plane:global:entries_paused'"
    ).fetchone()
    assert row["issued_by"] == "control_plane"
    assert row["reason"] == "operator requested pause"


def test_inv_pause_entries_source_ignores_manual_note_that_looks_auto(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    conn.close()
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(control_plane_module, "get_world_connection", lambda: get_connection(db_path))
    control_plane_module.clear_control_state()
    control_path.write_text(
        json.dumps(
            {
                "commands": [
                    {"command": "pause_entries", "issued_by": "ui", "note": "auto_pause:manual-note"}
                ],
                "acks": [],
            }
        )
    )

    processed = control_plane_module.process_commands()

    assert processed == ["pause_entries"]
    assert control_plane_module.is_entries_paused() is True
    assert control_plane_module.get_entries_pause_source() == "manual_command"
    assert control_plane_module.get_entries_pause_reason() == "auto_pause:manual-note"
    row = get_connection(db_path).execute(
        "SELECT issued_by, reason FROM control_overrides WHERE override_id = 'control_plane:global:entries_paused'"
    ).fetchone()
    assert row["issued_by"] == "control_plane"
    assert row["reason"] == "auto_pause:manual-note"


def test_inv_tighten_risk_survives_control_state_refresh_until_resume(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    conn.close()
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(control_plane_module, "get_world_connection", lambda: get_connection(db_path))
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
    row = get_connection(db_path).execute(
        "SELECT value, issued_by, precedence, effective_until FROM control_overrides WHERE override_id = 'control_plane:global:edge_threshold_multiplier'"
    ).fetchone()
    assert row["value"] == "2.0"
    assert row["issued_by"] == "control_plane"
    assert row["precedence"] == 100
    assert row["effective_until"] is not None


def test_inv_control_plane_records_explicit_skip_when_control_overrides_table_missing(monkeypatch, tmp_path):
    control_path = tmp_path / "control_plane.json"
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    conn.close()
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)
    monkeypatch.setattr(control_plane_module, "get_world_connection", lambda: get_connection(db_path))
    control_plane_module.clear_control_state()
    control_path.write_text(json.dumps({"commands": [{"command": "pause_entries"}], "acks": []}))

    processed = control_plane_module.process_commands()
    payload = json.loads(control_path.read_text())

    assert processed == ["pause_entries"]
    assert payload["commands"] == []
    assert payload["acks"][-1]["reason"] == "missing_control_overrides_table"
    control_plane_module.clear_control_state()
    assert control_plane_module.is_entries_paused() is False


def test_inv_supervisor_command_matches_real_control_plane_contract():
    cmd = SupervisorCommand(
        command="set_strategy_gate",
        reason="edge compression",
        strategy="opening_inertia",
        enabled=False,
        env="test",
        timestamp="2026-04-27T00:00:00+00:00",
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
                "title": "38°F or lower",
                "range_low": None,
                "range_high": 38,
                "token_id": "yes0",
                "no_token_id": "no0",
                "market_id": "m0",
                "price": 0.05,
            },
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
        bankroll=211.37,
        recent_exits=[_recent_exit(3.0)],
        positions=[_position(bin_label="41-42°F", last_monitor_market_price=0.08)],
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap1")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)

    def _capture_kelly(p_posterior, entry_price, bankroll, kelly_mult, **kwargs):
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
            min_order_usd=1.0,
        ),
        decision_time=datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc),
    )

    assert decisions[0].should_trade is True
    assert captured["bankroll"] == pytest.approx(portfolio.initial_bankroll)
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
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.05},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-tighten")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(
        evaluator_module,
        "resolve_strategy_policy",
        lambda conn, strategy_key, now: evaluator_module.StrategyPolicy(
            strategy_key=strategy_key,
            gated=False,
            allocation_multiplier=1.0,
            threshold_multiplier=2.0,
            exit_only=False,
            sources=["manual_override:threshold_multiplier"],
        ),
    )

    def _capture_kelly(p_posterior, entry_price, bankroll, kelly_mult, **kwargs):
        captured["kelly_mult"] = kelly_mult
        return 5.0

    monkeypatch.setattr(evaluator_module, "kelly_size", _capture_kelly)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=object(),
        portfolio=PortfolioState(bankroll=211.37),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            min_order_usd=1.0,
        ),
        decision_time=datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc),
    )

    assert decisions[0].should_trade is True
    assert captured["kelly_mult"] == pytest.approx(0.125)
    assert "strategy_policy_threshold_2x" in decisions[0].applied_validations


def test_inv_strategy_policy_gate_yields_risk_rejected(monkeypatch):
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.05},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-gated")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(
        evaluator_module,
        "resolve_strategy_policy",
        lambda conn, strategy_key, now: evaluator_module.StrategyPolicy(
            strategy_key=strategy_key,
            gated=True,
            allocation_multiplier=1.0,
            threshold_multiplier=1.0,
            exit_only=False,
            sources=["manual_override:gate"],
        ),
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=object(),
        portfolio=PortfolioState(bankroll=211.37),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            min_order_usd=1.0,
        ),
        decision_time=datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc),
    )

    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "RISK_REJECTED"
    assert decisions[0].rejection_reasons == ["POLICY_GATED(manual_override:gate)"]
    assert "strategy_policy" in decisions[0].applied_validations


def test_inv_strategy_policy_allocation_multiplier_reduces_final_size(monkeypatch):
    captured: dict[str, float] = {}

    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.05},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-alloc")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(
        evaluator_module,
        "resolve_strategy_policy",
        lambda conn, strategy_key, now: evaluator_module.StrategyPolicy(
            strategy_key=strategy_key,
            gated=False,
            allocation_multiplier=0.4,
            threshold_multiplier=2.0,
            exit_only=False,
            sources=["manual_override:allocation_multiplier", "risk_action:threshold_multiplier"],
        ),
    )

    def _capture_kelly(p_posterior, entry_price, bankroll, kelly_mult, **kwargs):
        captured["kelly_mult"] = kelly_mult
        return 10.0

    monkeypatch.setattr(evaluator_module, "kelly_size", _capture_kelly)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=object(),
        portfolio=PortfolioState(bankroll=211.37),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            min_order_usd=1.0,
        ),
        decision_time=datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc),
    )

    assert decisions[0].should_trade is True
    assert captured["kelly_mult"] == pytest.approx(0.125)
    assert decisions[0].size_usd == pytest.approx(4.0)
    assert "strategy_policy_threshold_2x" in decisions[0].applied_validations
    assert "strategy_policy_allocation_0.4x" in decisions[0].applied_validations


def test_inv_strategy_policy_is_read_before_anti_churn_rejection(monkeypatch):
    called: dict[str, bool] = {"policy": False}

    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.05},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-anti-churn")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "is_reentry_blocked", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        evaluator_module,
        "resolve_strategy_policy",
        lambda conn, strategy_key, now: (
            called.__setitem__("policy", True)
            or evaluator_module.StrategyPolicy(
                strategy_key=strategy_key,
                gated=False,
                allocation_multiplier=1.0,
                threshold_multiplier=1.0,
                exit_only=False,
                sources=[],
            )
        ),
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=object(),
        portfolio=PortfolioState(bankroll=211.37),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            min_order_usd=1.0,
        ),
        decision_time=datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc),
    )

    assert called["policy"] is True
    assert decisions[0].rejection_stage == "ANTI_CHURN"
    assert "strategy_policy" in decisions[0].applied_validations


def test_inv_manual_override_beats_automatic_risk_action_on_active_evaluator_path(monkeypatch):
    conn = _policy_conn()
    now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
    _insert_risk_action_row(
        conn,
        action_id="ra-gate-center",
        strategy_key="center_buy",
        action_type="gate",
        value="true",
        issued_at=(now - timedelta(minutes=5)).isoformat(),
        effective_until=(now + timedelta(hours=1)).isoformat(),
    )
    _insert_control_override_row(
        conn,
        override_id="ov-ungate-center",
        target_type="strategy",
        target_key="center_buy",
        action_type="gate",
        value="false",
        issued_at=(now - timedelta(minutes=1)).isoformat(),
        effective_until=(now + timedelta(hours=1)).isoformat(),
    )

    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.05},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-override")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))
    monkeypatch.setattr("src.riskguard.policy.is_entries_paused", lambda: False)
    monkeypatch.setattr("src.riskguard.policy.get_edge_threshold_multiplier", lambda: 1.0)

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=conn,
        portfolio=PortfolioState(bankroll=211.37),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            min_order_usd=1.0,
        ),
        decision_time=now,
    )

    assert decisions[0].should_trade is True
    assert decisions[0].rejection_stage == ""
    assert "strategy_policy" in decisions[0].applied_validations
    conn.close()


def test_inv_expired_manual_override_restores_automatic_risk_action_on_active_evaluator_path(monkeypatch):
    conn = _policy_conn()
    now = datetime(2026, 4, 3, 17, 0, tzinfo=timezone.utc)
    _insert_risk_action_row(
        conn,
        action_id="ra-gate-center",
        strategy_key="center_buy",
        action_type="gate",
        value="true",
        issued_at=(now - timedelta(minutes=5)).isoformat(),
        effective_until=(now + timedelta(hours=1)).isoformat(),
    )
    _insert_control_override_row(
        conn,
        override_id="ov-ungate-expired",
        target_type="strategy",
        target_key="center_buy",
        action_type="gate",
        value="false",
        issued_at=(now - timedelta(hours=2)).isoformat(),
        effective_until=(now - timedelta(minutes=1)).isoformat(),
    )

    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.05},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-override-expired")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))
    monkeypatch.setattr("src.riskguard.policy.is_entries_paused", lambda: False)
    monkeypatch.setattr("src.riskguard.policy.get_edge_threshold_multiplier", lambda: 1.0)

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=conn,
        portfolio=PortfolioState(bankroll=211.37),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(
            max_single_position_pct=0.10,
            max_portfolio_heat_pct=0.50,
            max_correlated_pct=0.25,
            max_city_pct=0.20,
            min_order_usd=1.0,
        ),
        decision_time=now,
    )

    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "RISK_REJECTED"
    assert decisions[0].rejection_reasons == ["POLICY_GATED(risk_action:gate)"]
    conn.close()


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
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.05},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.05, 0.60, 0.20, 0.15])

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            edge = BinEdge(
                bin=self.bins[1],
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
                support_index=1,
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
        lambda city, forecast_days=8, model=None, **kwargs: _make_ens_result_with_evidence(model=model),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-bias")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))
    monkeypatch.setattr("src.riskguard.policy.is_entries_paused", lambda: False)
    monkeypatch.setattr("src.riskguard.policy.get_edge_threshold_multiplier", lambda: 1.0)

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=conn,
        portfolio=PortfolioState(bankroll=211.37),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc),
    )
    conn.close()

    epistemic = json.loads(decisions[0].epistemic_context_json)
    assert epistemic["forecast_context"]["location"]["bias_reference"]["bias"] == 1.5


@pytest.mark.skip(reason="BI-05")
def test_inv_daily_loss_enforced(monkeypatch, tmp_path):
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    conn = get_connection(zeus_db)
    init_schema(conn)
    conn.close()

    portfolio = PortfolioState(
        bankroll=211.37,
        daily_baseline_total=211.37,
        recent_exits=[_recent_exit(-13.0)],
    )

    def _fake_get_connection(path=None, **_kwargs):
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


def test_inv_riskguard_does_not_promote_legacy_settlement_pnl(monkeypatch, tmp_path):
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
    conn.commit()  # Fix B: store_settlement_records no longer commits internally.
    conn.close()

    portfolio = PortfolioState(bankroll=211.37)

    def _fake_get_connection(path=None, **_kwargs):
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
    assert details["probability_directional_accuracy"] == pytest.approx(0.5)
    assert details["settlement_storage_source"] == "decision_log"
    assert details["settlement_metric_ready_count"] == 0
    assert details["realized_pnl"] == pytest.approx(0.0)
    assert details["total_pnl"] == pytest.approx(0.0)
    assert details["realized_pnl_source"] == "strategy_health.realized_pnl_30d"
    assert details["realized_pnl_window_days"] == 30


def test_inv_status_summary_does_not_promote_legacy_realized_truth(monkeypatch, tmp_path):
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    status_path = tmp_path / "status_summary.json"
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
            strategy="center_buy",
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
            strategy="opening_inertia",
        ),
    ])
    conn.commit()  # Fix B: store_settlement_records no longer commits internally.
    conn.close()

    def _fake_get_connection(path=None, **_kwargs):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
    monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
    monkeypatch.setattr(status_summary_module, "state_path", lambda name: risk_db if name == "risk_state.db" else tmp_path / name)
    monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: get_connection(zeus_db))
    monkeypatch.setattr(status_summary_module, "query_position_current_status_view", lambda conn: {
        "status": "empty",
        "positions": [],
        "open_positions": 0,
        "total_exposure_usd": 0.0,
        "unrealized_pnl": 0.0,
        "strategy_open_counts": {},
        "chain_state_counts": {},
        "exit_state_counts": {},
        "unverified_entries": 0,
        "day0_positions": 0,
    })
    monkeypatch.setattr(status_summary_module, "query_strategy_health_snapshot", lambda conn, now=None: {"status": "missing_table", "by_strategy": {}})
    monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
    monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
    monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
    monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})

    riskguard_module.tick()
    status_summary_module.write_status({"mode": "test"})
    status = json.loads(status_path.read_text())

    assert status["risk"]["details"]["settlement_storage_source"] == "decision_log"
    assert status["risk"]["details"]["settlement_metric_ready_count"] == 0
    assert status["portfolio"]["realized_pnl"] == pytest.approx(0.0)
    assert status["portfolio"]["total_pnl"] == pytest.approx(0.0)
    # P0-A DEF A (followup_design.md §2.1): effective_bankroll == wallet_balance,
    # NOT wallet+pnl. The retired DEF B path added PnL to a fixed bankroll;
    # now the value equals the wallet snapshot.
    assert status["portfolio"]["effective_bankroll"] == pytest.approx(211.37)


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
    conn.commit()  # Fix B: store_settlement_records no longer commits internally.
    conn.close()

    def _fake_get_connection(path=None, **_kwargs):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))

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
        cluster="NYC",
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
    # P9: log_settlement_event no longer writes to position_events.
    # Write SETTLED event directly to position_events + position_current
    # so query_settlement_events finds it via the canonical path.
    import json as _json
    conn.execute("""
        INSERT INTO position_current
        (position_id, phase, strategy_key, updated_at, city, target_date, bin_label, direction,
         market_id, edge_source, size_usd, shares, cost_basis_usd, entry_price, unit, temperature_metric)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, ("rt-settle-auth", "economically_closed", "center_buy",
           "2026-04-01T23:00:00+00:00", "NYC", "2026-04-01", "39-40°F", "buy_yes",
           "m6", "center_buy", 10.0, 25.0, 10.0, 0.40, "F", "high"))
    conn.execute("""
        INSERT INTO position_events
        (event_id, position_id, event_version, sequence_no, event_type,
         occurred_at, strategy_key, source_module, env, payload_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, ("rt-settle-auth:settled:1", "rt-settle-auth", 1, 1, "SETTLED",
           "2026-04-01T23:00:00+00:00", "center_buy", "src.state.db", "live",
           _json.dumps({
               "contract_version": "position_settled.v1",
               "winning_bin": "39-40°F",
               "position_bin": "39-40°F",
               "won": True,
               "outcome": 1,
               "p_posterior": 0.61,
               "exit_price": 1.0,
               "pnl": 15.0,
               "exit_reason": "SETTLEMENT",
               "settlement_authority": "VERIFIED",
               "settlement_truth_source": "world.settlements",
               "settlement_market_slug": "nyc-high-2026-04-01",
               "settlement_temperature_metric": "high",
               "settlement_source": "WU",
               "settlement_value": 40.0,
           })))
    conn.commit()
    conn.close()

    def _fake_get_connection(path=None, **_kwargs):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    # P0-A masking-test repoint (architect_memo §6, followup_design §2.1):
    # this test's axis is "INV settlement source = position_events"; bankroll
    # is now provider-sourced. Monkeypatch bankroll_provider.current() so the
    # test stops enshrining PortfolioState.bankroll as a truth source.
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

    riskguard_module.tick()
    row = get_connection(risk_db).execute(
        "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert details["settlement_storage_source"] == "position_events"
    assert details["settlement_row_storage_sources"] == ["position_events"]
    assert details["settlement_sample_size"] == 1
    assert details["probability_directional_accuracy"] == pytest.approx(1.0)



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
    conn.commit()  # Fix B: store_settlement_records no longer commits internally.
    conn.close()

    def _fake_get_connection(path=None, **_kwargs):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37))

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
        def __init__(self):
            pass

    calls: list[dict] = []
    _allow_entry_gates_for_cycle_test(monkeypatch)

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    _market_list = [{
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 2.0,
        "hours_to_resolution": 30.0,
        "temperature_metric": "high",
    }]
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: _market_list)
    monkeypatch.setattr("src.data.market_scanner.find_weather_markets", lambda **kwargs: _market_list)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: False)
    # DummyClob lacks get_positions_from_api/get_balance → chain sync and wallet fail → entries blocked.
    # Stub both so chain_ready=True and entry_bankroll is set and discovery phase runs.
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "_entry_bankroll_for_cycle", lambda portfolio, clob: (211.37, {}))
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
                support_index=1,
            ),
            tokens={"market_id": "m1", "token_id": "yes123", "no_token_id": "no456"},
            size_usd=5.0,
            decision_id="dec1",
            decision_snapshot_id="snap1",
            edge_source="settlement_capture",
            strategy_key="settlement_capture",
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
        lambda *args, **kwargs: OrderResult(trade_id="trade-1", status="filled", fill_price=0.35, shares=14.29, command_state="FILLED"),
    )
    monkeypatch.setattr(
        "src.state.strategy_tracker.StrategyTracker.record_entry",
        lambda self, pos: calls.append(pos),
    )

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert calls, "StrategyTracker never received the filled trade"


def test_inv_harvester_triggers_refit(monkeypatch, tmp_path):
    from src.calibration.decision_group import compute_id

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    season = season_from_date("2026-04-01")
    for i in range(15):
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
            decision_group_id=compute_id(
                "NYC",
                f"2026-04-{i+1:02d}",
                "2026-03-30T01:00:00Z",
                "test_pnl_flow_and_audit_v1",
            ),
            city_obj=NYC,
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
    # P9: log_settlement_event no longer writes to position_events or decision_log.
    # Use store_settlement_records so query_authoritative_settlement_rows finds the record.
    store_settlement_records(conn, [SettlementRecord(
        trade_id="trade-1",
        city="NYC",
        target_date="2026-04-01",
        range_label="39-40°F",
        direction="buy_yes",
        p_posterior=0.61,
        outcome=1,
        pnl=15.0,
        decision_snapshot_id=snapshot_id,
        strategy="center_buy",
        edge_source="center_buy",
        settled_at="2026-04-01T23:00:00Z",
    )])
    _insert_source_correct_harvester_obs(conn)
    conn.commit()
    _ensure_auth_verified(conn)
    conn.close()

    event = {
        "title": "Highest temperature in New York City on April 1 2026",
        "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
        "markets": [
            {
                "question": "39-40°F",
                "winningOutcome": "Yes",
                "clobTokenIds": json.dumps(["yes1", "no1"]),
                "outcomePrices": json.dumps(["1", "0"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps(["0", "1"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m2",
            },
        ],
    }

    _hconn = get_connection(db_path)
    monkeypatch.setattr(harvester_module, "get_trade_connection", lambda: _hconn)
    monkeypatch.setattr(harvester_module, "get_forecasts_connection", lambda: _hconn)
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(bankroll=211.37, positions=[]),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
    _enable_live_harvester_test_path(monkeypatch)
    refit_calls = []
    monkeypatch.setattr(
        harvester_module,
        "maybe_refit_bucket",
        lambda conn, city, target_date: refit_calls.append((city.name, target_date)) or True,
    )

    result = harvester_module.run_harvester()

    assert result["pairs_created"] == 2
    assert result["stage2_status"] == "ready"
    assert refit_calls == [("NYC", "2026-04-01")]


def test_harvester_stage2_preflight_skips_canonical_bootstrap_shape(
    monkeypatch,
    tmp_path,
    caplog,
):
    db_path = tmp_path / "canonical_bootstrap.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
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
                "outcomePrices": json.dumps(["1", "0"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m1",
            }
        ],
    }

    hconn = get_connection(db_path)
    monkeypatch.setattr(harvester_module, "get_trade_connection", lambda: hconn)
    monkeypatch.setattr(harvester_module, "get_forecasts_connection", lambda: hconn)
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(
            bankroll=211.37,
            positions=[
                _position(
                    trade_id="stage2-skip-settles",
                    city="NYC",
                    target_date="2026-04-01",
                    bin_label="39-40°F",
                )
            ],
        ),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
    monkeypatch.setattr(
        harvester_module,
        "_snapshot_contexts_for_market",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preflight should skip Stage-2")),
    )
    settled_calls = []

    def _settle_positions(*args, **kwargs):
        settled_calls.append(args[2:5])
        kwargs["settlement_records"].append(SettlementRecord(
            trade_id="stage2-skip-settles",
            city="NYC",
            target_date="2026-04-01",
            range_label="39-40°F",
            direction="buy_yes",
            p_posterior=0.61,
            outcome=1,
            pnl=1.0,
            decision_snapshot_id="snap-missing",
            strategy="center_buy",
            edge_source="center_buy",
            settled_at="2026-04-01T23:00:00Z",
        ))
        return 1

    monkeypatch.setattr(harvester_module, "_settle_positions", _settle_positions)
    monkeypatch.setattr(
        harvester_module,
        "store_settlement_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("decision_log writer must be skipped")),
    )
    _enable_live_harvester_test_path(monkeypatch, mock_lookup=True)

    with caplog.at_level(logging.ERROR):
        result = harvester_module.run_harvester()

    assert result["settlements_found"] == 1
    assert result["pairs_created"] == 0
    assert result["positions_settled"] == 1
    assert result["legacy_settlement_records_skipped"] == 1
    assert result["stage2_status"] == "skipped_db_shape_preflight"
    assert "decision_log" in result["stage2_missing_trade_tables"]
    assert "chronicle" in result["stage2_missing_trade_tables"]
    assert "ensemble_snapshots" in result["stage2_missing_shared_tables"]
    assert settled_calls == [("NYC", "2026-04-01", "39-40°F")]
    assert not any("Harvester error" in record.getMessage() for record in caplog.records)


def test_inv_harvester_falls_back_to_open_portfolio_snapshot_when_no_durable_settlement_exists(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    snapshot_id = _insert_snapshot(conn, "NYC", "2026-04-01", [0.65, 0.35])
    _insert_source_correct_harvester_obs(conn)
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
                "outcomePrices": json.dumps(["1", "0"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps(["0", "1"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m2",
            },
        ],
    }

    _hconn = get_connection(db_path)
    monkeypatch.setattr(harvester_module, "get_trade_connection", lambda: _hconn)
    monkeypatch.setattr(harvester_module, "get_forecasts_connection", lambda: _hconn)
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(
            bankroll=211.37,
            positions=[_position(
                trade_id="trade-open-fallback",
                target_date="2026-04-01",
                bin_label="39-40°F",
                decision_snapshot_id=snapshot_id,
            )],
        ),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
    _enable_live_harvester_test_path(monkeypatch)

    result = harvester_module.run_harvester()

    assert result["pairs_created"] == 0

    conn = get_connection(db_path)
    snapshot_event = conn.execute(
        """
        SELECT details_json FROM chronicle
        WHERE event_type = 'SETTLEMENT_SNAPSHOT_SOURCE'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
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
    _insert_source_correct_harvester_obs(conn)
    conn.commit()  # Fix B: store_settlement_records no longer commits internally.
    conn.close()

    event = {
        "title": "Highest temperature in New York City on April 1 2026",
        "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
        "markets": [
            {
                "question": "39-40°F",
                "winningOutcome": "Yes",
                "clobTokenIds": json.dumps(["yes1", "no1"]),
                "outcomePrices": json.dumps(["1", "0"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps(["0", "1"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m2",
            },
        ],
    }

    _hconn = get_connection(db_path)
    monkeypatch.setattr(harvester_module, "get_trade_connection", lambda: _hconn)
    monkeypatch.setattr(harvester_module, "get_forecasts_connection", lambda: _hconn)
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(
            bankroll=211.37,
            positions=[_position(
                trade_id="trade-open-ignored",
                target_date="2026-04-01",
                bin_label="39-40°F",
                decision_snapshot_id=portfolio_snapshot_id,
            )],
        ),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
    _enable_live_harvester_test_path(monkeypatch)

    result = harvester_module.run_harvester()

    assert result["pairs_created"] == 2

    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT range_label, p_raw
        FROM calibration_pairs_v2
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
    # P9: log_settlement_event no longer writes to position_events.
    # Write SETTLED event + position_current directly so query_settlement_events
    # finds it via the canonical path (asserted as source=="position_events" below).
    import json as _json
    conn.execute("""
        INSERT INTO position_current
        (position_id, phase, strategy_key, updated_at, city, target_date, bin_label, direction,
         market_id, edge_source, size_usd, shares, cost_basis_usd, entry_price, unit, temperature_metric)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, ("trade-durable-preferred", "economically_closed", "center_buy",
           "2026-04-01T23:00:00+00:00", "NYC", "2026-04-01", "39-40°F", "buy_yes",
           "m1", "center_buy", 10.0, 10.0, 10.0, 0.40, "F", "high"))
    conn.execute("""
        INSERT INTO position_events
        (event_id, position_id, event_version, sequence_no, event_type,
         occurred_at, strategy_key, snapshot_id, source_module, env, payload_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, ("trade-durable-preferred:settled:1", "trade-durable-preferred", 1, 1, "SETTLED",
           "2026-04-01T23:00:00+00:00", "center_buy", durable_snapshot_id, "src.state.db", "live",
           _json.dumps({
               "contract_version": "position_settled.v1",
               "winning_bin": "39-40°F",
               "position_bin": "39-40°F",
               "won": True,
               "outcome": 1,
               "p_posterior": 0.61,
               "exit_price": 1.0,
               "pnl": 15.0,
               "exit_reason": "SETTLEMENT",
               "settlement_authority": "VERIFIED",
               "settlement_truth_source": "world.settlements",
               "settlement_market_slug": "nyc-high-2026-04-01",
               "settlement_temperature_metric": "high",
               "settlement_source": "WU",
               "settlement_value": 40.0,
           })))
    _insert_source_correct_harvester_obs(conn)
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
                "outcomePrices": json.dumps(["1", "0"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps(["0", "1"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m2",
            },
        ],
    }

    _hconn = get_connection(db_path)
    monkeypatch.setattr(harvester_module, "get_trade_connection", lambda: _hconn)
    monkeypatch.setattr(harvester_module, "get_forecasts_connection", lambda: _hconn)
    monkeypatch.setattr(
        harvester_module,
        "load_portfolio",
        lambda: PortfolioState(
            bankroll=211.37,
            positions=[_position(
                trade_id="trade-open-ignored",
                target_date="2026-04-01",
                bin_label="39-40°F",
                decision_snapshot_id=portfolio_snapshot_id,
            )],
        ),
    )
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
    _enable_live_harvester_test_path(monkeypatch)

    result = harvester_module.run_harvester()

    assert result["pairs_created"] == 2

    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT range_label, p_raw
        FROM calibration_pairs_v2
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
    # P9: log_settlement_event no longer writes to position_events or decision_log.
    # Use store_settlement_records for both so query_authoritative_settlement_rows finds them.
    # good_pos has decision_snapshot_id -> context found; bad_pos has none -> dropped.
    store_settlement_records(conn, [
        SettlementRecord(
            trade_id="trade-good-context",
            city="NYC",
            target_date="2026-04-01",
            range_label="39-40°F",
            direction="buy_yes",
            p_posterior=0.61,
            outcome=1,
            pnl=15.0,
            decision_snapshot_id=good_snapshot_id,
            strategy="center_buy",
            edge_source="center_buy",
            settled_at="2026-04-01T23:00:00Z",
        ),
        SettlementRecord(
            trade_id="trade-missing-context",
            city="NYC",
            target_date="2026-04-01",
            range_label="41-42°F",
            direction="buy_yes",
            p_posterior=0.40,
            outcome=0,
            pnl=-3.0,
            decision_snapshot_id="",
            strategy="center_buy",
            edge_source="center_buy",
            settled_at="2026-04-01T23:00:00Z",
        ),
    ])
    _insert_source_correct_harvester_obs(conn)
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
                "outcomePrices": json.dumps(["1", "0"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m1",
            },
            {
                "question": "41-42°F",
                "winningOutcome": "No",
                "clobTokenIds": json.dumps(["yes2", "no2"]),
                "outcomePrices": json.dumps(["0", "1"]),
                "outcomes": json.dumps(["Yes", "No"]),
                "umaResolutionStatus": "resolved",
                "conditionId": "m2",
            },
        ],
    }

    _hconn = get_connection(db_path)
    monkeypatch.setattr(harvester_module, "get_trade_connection", lambda: _hconn)
    monkeypatch.setattr(harvester_module, "get_forecasts_connection", lambda: _hconn)
    monkeypatch.setattr(harvester_module, "load_portfolio", lambda: PortfolioState(bankroll=211.37, positions=[]))
    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
    monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
    _enable_live_harvester_test_path(monkeypatch)

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


def test_query_position_current_status_view_ignores_terminal_trade_decision_shadow_rows(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    apply_architecture_kernel_schema(conn)
    init_schema(conn)

    _insert_position_current_row(
        conn,
        position_id="trade-exited",
        strategy_key="center_buy",
        phase="active",
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        entry_price=0.5,
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, edge_source, runtime_trade_id, filled_at, fill_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "m1",
            "39-40°F",
            "buy_yes",
            10.0,
            0.5,
            "2026-04-01T01:00:00Z",
            0.6,
            0.6,
            0.1,
            0.5,
            0.7,
            0.0,
            "exited",
            "center_buy",
            "trade-exited",
            "2026-04-01T02:00:00Z",
            0.46,
        ),
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)
    conn.close()

    assert status_view["open_positions"] == 1
    assert status_view["positions"][0]["trade_id"] == "trade-exited"
    assert status_view["positions"][0]["state"] == "active"
    assert loader_view["status"] == "ok"
    assert loader_view["positions"][0]["trade_id"] == "trade-exited"
    assert loader_view["positions"][0]["state"] == "entered"


def test_position_current_views_ignore_terminal_trade_decision_shadow_status_when_current_db_lags(tmp_path, monkeypatch):
    import src.state.db as db_module

    current_db = tmp_path / "zeus-current.db"
    legacy_db = tmp_path / "zeus.db"

    current_conn = get_connection(current_db)
    apply_architecture_kernel_schema(current_conn)
    init_schema(current_conn)
    _insert_position_current_row(
        current_conn,
        position_id="trade-lagged",
        strategy_key="center_buy",
        phase="active",
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        entry_price=0.5,
    )
    current_conn.commit()

    legacy_conn = get_connection(legacy_db)
    init_schema(legacy_conn)
    legacy_conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, edge_source, runtime_trade_id, filled_at, fill_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "m1",
            "39-40°F",
            "buy_yes",
            10.0,
            0.5,
            "2026-04-01T02:00:00Z",
            0.6,
            0.6,
            0.1,
            0.5,
            0.7,
            0.0,
            "exited",
            "center_buy",
            "trade-lagged",
            "2026-04-01T02:00:00Z",
            0.46,
        ),
    )
    legacy_conn.commit()
    legacy_conn.close()

    monkeypatch.setattr(db_module, "ZEUS_DB_PATH", legacy_db)

    status_view = query_position_current_status_view(current_conn)
    loader_view = query_portfolio_loader_view(current_conn)
    current_conn.close()

    assert status_view["open_positions"] == 1
    assert status_view["positions"][0]["trade_id"] == "trade-lagged"
    assert status_view["positions"][0]["state"] == "active"
    assert loader_view["positions"][0]["trade_id"] == "trade-lagged"
    assert loader_view["positions"][0]["state"] == "entered"


def test_harvester_settlement_chronicle_event_carries_exit_price(tmp_path):
    from src.execution.harvester import _settle_positions

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = _position(
        trade_id="trade-settle",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        entry_price=0.40,
        size_usd=10.0,
        shares=25.0,
        state="entered",
        strategy="center_buy",
        edge_source="center_buy",
    )
    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-01",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    chronicle_row = conn.execute(
        """
        SELECT details_json
        FROM chronicle
        WHERE event_type = 'SETTLEMENT'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert settled == 1
    details = json.loads(chronicle_row["details_json"])
    assert details["exit_price"] == pytest.approx(1.0)
    assert details["exit_reason"] == "SETTLEMENT"
