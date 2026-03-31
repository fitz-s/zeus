"""Cross-module invariant tests for Zeus lifecycle contracts."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

import src.engine.cycle_runner as cycle_runner
from src.config import City, settings
from src.contracts import (
    EntryMethod,
    HeldSideProbability,
    NativeSidePrice,
    compute_forward_edge,
    compute_native_limit_price,
    recompute_native_probability,
)
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import EdgeDecision
from src.execution.executor import OrderResult
from src.execution.harvester import (
    _get_stored_p_raw,
    _settle_positions,
    _snapshot_contexts_for_market,
    get_snapshot_context,
    get_snapshot_p_raw,
)
from src.signal.day0_signal import Day0Signal
from src.signal.ensemble_signal import EnsembleSignal, sigma_instrument
from src.state.db import get_connection, init_schema
from src.state.portfolio import PortfolioState, Position, load_portfolio, save_portfolio
from src.state.strategy_tracker import StrategyTracker
from src.types import Bin, BinEdge
from tests.contracts.spec_validation_manifest import (
    EXIT_REQUIRED_STEPS,
    SPEC_ENTRY_VALIDATIONS,
    SPEC_EXIT_VALIDATIONS,
    SYMMETRY_PAIRS,
    required_exit_ratio,
)


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="US-Northeast",
    settlement_unit="F",
    wu_station="KLGA",
)


class FlipProbe:
    """Arithmetic probe: any cross-space `1 - p` should become observable here."""

    def __init__(self, value: float, direction: str):
        self.value = value
        self.direction = direction
        self.flip_attempts = 0

    def __float__(self) -> float:
        return float(self.value)

    def __rsub__(self, other: object) -> float:
        self.flip_attempts += 1
        raise AssertionError("Cross-space flip attempted on a semantic boundary value")


def _edge(direction: str = "buy_yes", p_posterior: float = 0.60, vwmp: float = 0.40) -> BinEdge:
    return BinEdge(
        bin=Bin(low=39, high=40, label="39-40°F"),
        direction=direction,
        edge=max(p_posterior - vwmp, 0.01),
        ci_lower=0.03,
        ci_upper=0.17,
        p_model=p_posterior,
        p_market=vwmp,
        p_posterior=p_posterior,
        entry_price=vwmp,
        p_value=0.02,
        vwmp=vwmp,
    )


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
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        entered_at="2026-03-30T00:00:00Z",
        token_id="yes123",
        no_token_id="no456",
        edge_source="settlement_capture",
        strategy="settlement_capture",
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _insert_snapshot(conn, city: str, target_date: str, issue_time: str, fetch_time: str, p_raw: list[float]) -> str:
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
            fetch_time,
            fetch_time,
            72.0,
            json.dumps([40.0] * 51),
            json.dumps(p_raw),
            2.0,
            0,
            "ecmwf_ifs025",
            "live_v1",
        ),
    )
    row = conn.execute("SELECT last_insert_rowid() AS snapshot_id").fetchone()
    conn.commit()
    return str(row["snapshot_id"])


def test_inv01_buy_no_limit_price_stays_in_native_space():
    price = compute_native_limit_price(
        HeldSideProbability(0.85, "buy_no"),
        NativeSidePrice(0.82, "buy_no"),
        limit_offset=0.01,
    )

    assert price == pytest.approx(0.81)
    assert price > 0.50
    assert compute_forward_edge(
        HeldSideProbability(0.85, "buy_no"),
        NativeSidePrice(0.82, "buy_no"),
    ) == pytest.approx(0.03)


def test_inv01_flip_probe_makes_cross_space_conversion_observable():
    prob = FlipProbe(0.85, "buy_no")
    price = FlipProbe(0.82, "buy_no")

    limit_price = compute_native_limit_price(prob, price, limit_offset=0.01)

    assert limit_price == pytest.approx(0.81)
    assert prob.flip_attempts == 0
    assert price.flip_attempts == 0


@pytest.mark.parametrize(
    ("entry_method", "expected_prob"),
    [
        (EntryMethod.ENS_MEMBER_COUNTING.value, 0.61),
        (EntryMethod.DAY0_OBSERVATION.value, 0.77),
    ],
)
def test_inv02_recompute_dispatches_exact_entry_method(entry_method, expected_prob):
    calls: list[str] = []

    def make_handler(name: str, value: float):
        def _handler(**kwargs):
            calls.append(name)
            return value, [name]
        return _handler

    registry = {
        EntryMethod.ENS_MEMBER_COUNTING.value: make_handler(EntryMethod.ENS_MEMBER_COUNTING.value, 0.61),
        EntryMethod.DAY0_OBSERVATION.value: make_handler(EntryMethod.DAY0_OBSERVATION.value, 0.77),
    }

    pos = _position(entry_method=entry_method)
    prob = recompute_native_probability(pos, current_p_market=0.40, registry=registry)

    assert prob == pytest.approx(expected_prob)
    assert calls == [entry_method]
    assert pos.selected_method == entry_method
    assert entry_method in pos.applied_validations


def test_inv02_day0_monitor_refresh_recomputes_probability(monkeypatch, tmp_path):
    from src.engine.monitor_refresh import refresh_position

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    class DummyClob:
        paper_mode = True

        def get_best_bid_ask(self, token_id):
            return (0.40, 0.42, 10.0, 10.0)

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "src.engine.monitor_refresh.get_current_observation",
        lambda city: {
            "high_so_far": 44.0,
            "current_temp": 43.0,
            "source": "wu",
            "observation_time": now.isoformat(),
            "unit": "F",
        },
    )
    monkeypatch.setattr("src.engine.monitor_refresh.get_current_yes_price", lambda market_id: 0.48)
    monkeypatch.setattr("src.engine.monitor_refresh.get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr("src.engine.monitor_refresh.validate_ensemble", lambda result: True)
    target_day = now.astimezone(ZoneInfo(NYC.timezone)).date()
    monkeypatch.setattr(
        "src.engine.monitor_refresh.fetch_ensemble",
        lambda city, forecast_days=2: {
            "members_hourly": np.tile(np.linspace(43.0, 47.0, 12), (51, 1)),
            "times": [(now + timedelta(hours=i)).replace(microsecond=0).isoformat() for i in range(12)],
        },
    )

    pos = _position(
        entry_method=EntryMethod.DAY0_OBSERVATION.value,
        p_posterior=0.55,
        target_date=str(target_day),
    )
    _, refreshed = refresh_position(conn, DummyClob(), pos)
    conn.close()

    assert refreshed != pytest.approx(0.55)
    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert "day0_observation" in pos.applied_validations
    assert "mc_instrument_noise" in pos.applied_validations


def test_inv03_recent_exits_survive_restart(tmp_path):
    path = tmp_path / "positions.json"
    state = PortfolioState(
        bankroll=150.0,
        recent_exits=[{"city": "NYC", "bin_label": "39-40°F", "exit_reason": "EDGE_REVERSAL"}],
    )

    save_portfolio(state, path)
    loaded = load_portfolio(path)

    assert loaded.recent_exits == state.recent_exits


def test_inv03_pending_order_becomes_durable_position(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    portfolio_path = tmp_path / "positions.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: cycle_runner.RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: load_portfolio(portfolio_path))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: save_portfolio(state, portfolio_path))
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 2.0,
        "hours_to_resolution": 30.0,
    }])

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = paper_mode

    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: [EdgeDecision(
            should_trade=True,
            edge=_edge(direction="buy_yes"),
            tokens={"market_id": "m1", "token_id": "yes123", "no_token_id": "no456"},
            size_usd=5.0,
            decision_id="dec1",
            decision_snapshot_id="snap1",
            edge_source="opening_inertia",
            applied_validations=["ens_fetch"],
        )],
    )
    monkeypatch.setattr(
        cycle_runner,
        "execute_order",
        lambda *args, **kwargs: OrderResult(
            trade_id="trade-pending",
            status="pending",
            reason="posted",
            order_id="ord-1",
            timeout_seconds=300,
            submitted_price=0.35,
            shares=14.29,
        ),
    )

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    loaded = load_portfolio(portfolio_path)

    pending = next(p for p in loaded.positions if p.trade_id == "trade-pending")
    assert pending.state == "pending_tracked"
    assert pending.order_id == "ord-1"
    assert pending.order_status == "pending"
    assert pending.order_timeout_at


def test_inv03_harvester_prefers_decision_snapshot_over_latest(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    first_id = _insert_snapshot(
        conn,
        "NYC",
        "2026-04-01",
        "2026-03-29T00:00:00Z",
        "2026-03-29T01:00:00Z",
        [0.90, 0.10],
    )
    _insert_snapshot(
        conn,
        "NYC",
        "2026-04-01",
        "2026-03-30T00:00:00Z",
        "2026-03-30T01:00:00Z",
        [0.10, 0.90],
    )

    assert get_snapshot_p_raw(conn, first_id) == [0.90, 0.10]
    assert _get_stored_p_raw(conn, "NYC", "2026-04-01", snapshot_id=first_id) == [0.90, 0.10]
    assert get_snapshot_context(conn, first_id)["p_raw_vector"] == [0.90, 0.10]
    portfolio = PortfolioState(positions=[_position(decision_snapshot_id=first_id)])
    contexts = _snapshot_contexts_for_market(conn, portfolio, "NYC", "2026-04-01")
    assert [ctx["p_raw_vector"] for ctx in contexts] == [[0.90, 0.10]]
    conn.close()


def test_inv04_runtime_uses_unit_aware_temperature_noise():
    members = np.linspace(40.0, 44.0, 51 * 48).reshape(51, 48)
    ens = EnsembleSignal(members, NYC, date.today())
    assert ens.spread().unit == "F"

    day0 = Day0Signal(
        observed_high_so_far=4.0,
        current_temp=3.5,
        hours_remaining=5.0,
        member_maxes_remaining=np.array([4.0, 4.5, 5.0]),
        unit="C",
    )
    assert day0._sigma == pytest.approx(sigma_instrument("C").value)


def test_inv04_no_bare_temperature_threshold_comparisons_in_src():
    root = Path(__file__).resolve().parents[1] / "src"
    pattern = re.compile(
        r"(?:<=|>=|<|>|==)\s*(?:0\.5|2\.0|3\.0|5\.0)\b|\b(?:0\.5|2\.0|3\.0|5\.0)\s*(?:<=|>=|<|>|==)"
    )

    offenders = []
    for path in root.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}:{line.strip()}")

    assert offenders == []


def test_inv05_strategy_attribution_survives_settlement_chronicle(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    portfolio = PortfolioState(positions=[_position()])

    settled = _settle_positions(conn, portfolio, "NYC", "2026-04-01", "39-40°F")
    assert settled == 1

    row = conn.execute(
        "SELECT event_type, details_json FROM chronicle ORDER BY id DESC LIMIT 1"
    ).fetchone()
    details = json.loads(row["details_json"])

    assert details["edge_source"] == "settlement_capture"
    tracker = StrategyTracker()
    tracker.record_chronicle_event(row["event_type"], details)
    assert tracker.summary()["settlement_capture"]["trades"] == 1
    conn.close()


def test_inv06_spec_manifest_exit_entry_ratio():
    assert len(SPEC_EXIT_VALIDATIONS) >= required_exit_ratio()


def test_inv06_runtime_exit_evidence_covers_required_steps():
    pos = _position(entry_method=EntryMethod.ENS_MEMBER_COUNTING.value)
    registry = {
        EntryMethod.ENS_MEMBER_COUNTING.value: lambda **kwargs: (
            0.32,
            ["fresh_ens_fetch", "mc_instrument_noise", "platt_recalibration"],
        ),
    }
    recompute_native_probability(pos, current_p_market=0.40, registry=registry)
    decision = pos.evaluate_exit(current_p_posterior=0.32, current_p_market=0.40, best_bid=0.45)

    assert pos.selected_method == EntryMethod.ENS_MEMBER_COUNTING.value
    for step in EXIT_REQUIRED_STEPS:
        assert step in decision.applied_validations
    for entry_step, exit_step in SYMMETRY_PAIRS.items():
        if entry_step in SPEC_ENTRY_VALIDATIONS:
            assert exit_step in SPEC_EXIT_VALIDATIONS


def test_inv07_every_rejected_candidate_records_notradecase(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: cycle_runner.RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 2.0,
        "hours_to_resolution": 30.0,
    }])

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = paper_mode

    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: [EdgeDecision(
            should_trade=False,
            decision_id="dec-risk",
            rejection_stage="RISK_REJECTED",
            rejection_reasons=["city cap"],
            selected_method=EntryMethod.ENS_MEMBER_COUNTING.value,
            applied_validations=["risk_limits"],
        )],
    )

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT artifact_json FROM decision_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    artifact = json.loads(row["artifact_json"])
    conn.close()

    assert artifact["summary"]["candidates"] == 1
    assert len(artifact["trade_cases"]) + len(artifact["no_trade_cases"]) == 1
    assert artifact["no_trade_cases"][0]["rejection_stage"] == "RISK_REJECTED"
    assert artifact["no_trade_cases"][0]["rejection_reasons"] == ["city cap"]
