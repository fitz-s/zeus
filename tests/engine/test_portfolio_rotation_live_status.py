# Created: 2026-06-18
# Last reused/audited: 2026-07-15
# Authority basis: live redecision repair; non-actuating rotation output must not appear as live action.
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.engine.cycle_runtime import (
    _emit_portfolio_rotation_evaluation_status,
    _rotation_candidates,
)
from src.state.db import init_schema


class _Logger:
    def __init__(self) -> None:
        self.infos: list[tuple] = []
        self.warnings: list[tuple] = []

    def info(self, *args, **kwargs) -> None:
        self.infos.append((args, kwargs))

    def warning(self, *args, **kwargs) -> None:
        self.warnings.append((args, kwargs))


def _deps() -> SimpleNamespace:
    return SimpleNamespace(
        logger=_Logger(),
        _utcnow=lambda: datetime(2026, 6, 7, 6, 30, tzinfo=timezone.utc),
    )


def _create_main_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            trade_id TEXT,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            bin_label TEXT,
            direction TEXT,
            shares REAL,
            last_monitor_prob REAL,
            last_monitor_prob_is_fresh INTEGER,
            last_monitor_market_price REAL,
            last_monitor_market_price_is_fresh INTEGER,
            last_monitor_best_bid REAL,
            token_id TEXT,
            no_token_id TEXT,
            condition_id TEXT
        )
        """
    )
def _create_world_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE world.no_trade_regret_events (
            event_id TEXT,
            rejection_stage TEXT,
            rejection_reason TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            q_lcb_5pct REAL,
            c_fee_adjusted REAL,
            p_fill_lcb REAL,
            trade_score REAL,
            token_id TEXT,
            condition_id TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX world.idx_no_trade_regret_created_at "
        "ON no_trade_regret_events(created_at DESC)"
    )


def test_rotation_candidates_bounds_the_index_scan_by_lookback(tmp_path) -> None:
    world_path = tmp_path / "world.db"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    _create_world_schema(conn)
    conn.executemany(
        """
        INSERT INTO world.no_trade_regret_events VALUES (
            ?, 'KELLY', 'KELLY_REJECTED:corr_budget', 'Seoul', '2026-05-01',
            'high', 'old-bin', 'buy_no', 0.8, 0.5, 1.0, NULL,
            'old-token', 'old-condition', ?
        )
        """,
        [
            (f"old-{index}", f"2026-05-01T00:{index % 60:02d}:00+00:00")
            for index in range(5_000)
        ],
    )

    progress_calls = 0

    def stop_unbounded_scan() -> int:
        nonlocal progress_calls
        progress_calls += 1
        return int(progress_calls > 20)

    conn.set_progress_handler(stop_unbounded_scan, 100)
    candidates, missing = _rotation_candidates(
        conn,
        decision_time=datetime(2026, 6, 7, 6, 30, tzinfo=timezone.utc),
    )

    assert candidates == []
    assert missing == []
    assert progress_calls <= 20


def _write_qkernel_kelly_regret_row_via_reactor(
    conn: sqlite3.Connection,
    *,
    event_id_suffix: str,
    global_probability_functional: str,
    edge_lcb: float,
    edge_expected: float,
    payoff_q_point: float = 0.60,
    payoff_q_lcb: float = 0.50,
    cost: float = 0.50,
    city: str = "Seoul",
    target_date: str = "2026-06-07",
    metric: str = "high",
    bin_label: str,
    direction: str = "buy_no",
) -> str:
    """Write one no_trade_regret_events row through the REAL _write_regret
    path (rejection_stage='KELLY', matching the rotation candidate query's own
    admission filter) so trade_score originates from an actual
    _qkernel_regret_trade_score call against a real qkernel certificate --
    not a hand-picked literal. Returns the written event_id.
    """
    from src.events.event_store import EventStore
    from src.events.opportunity_event import make_opportunity_event
    from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    store = EventStore(conn)
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{target_date}|{metric}|{bin_label}|{direction}|{event_id_suffix}",
        source="rotation-regression-test",
        observed_at="2026-06-07T05:00:00+00:00",
        available_at="2026-06-07T05:00:00+00:00",
        received_at="2026-06-07T05:00:01+00:00",
        payload={"city": city, "target_date": target_date, "metric": metric},
        priority=50,
    )
    store.insert_or_ignore(event)
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        city=city,
        target_date=target_date,
        metric=metric,
        family_id=f"{city}|{target_date}|{metric}",
        bin_label=bin_label,
        direction=direction,
        condition_id=f"condition-{event_id_suffix}",
        token_id=f"token-{event_id_suffix}",
        executable_snapshot_id="exec-qkernel-rotation-test",
        qkernel_execution_economics={
            "payoff_q_point": payoff_q_point,
            "payoff_q_lcb": payoff_q_lcb,
            "cost": cost,
            "edge_lcb": edge_lcb,
            "edge_expected": edge_expected,
            "global_probability_functional": global_probability_functional,
        },
    )
    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _event: True,
        executable_snapshot_gate=lambda _event, _dt: True,
        riskguard_gate=lambda _event: True,
        final_intent_submit=lambda _event, _decision_time: None,
        reject=lambda _event, _stage, _reason: None,
        regret_ledger=NoTradeRegretLedger(conn),
    )
    reactor._write_regret(
        event,
        "KELLY",
        "KELLY_REJECTED:corr_budget",
        receipt=receipt,
        decision_time=datetime(2026, 6, 7, 5, 5, tzinfo=timezone.utc),
    )
    return event.event_id


def test_rotation_candidates_admits_positive_mean_route_row_excludes_nonpositive_robust_row() -> None:
    """Pin the live-behavior change from src/events/reactor.py's regret
    trade_score fix (commit 70ba2e88a) THROUGH the actual write path -- not by
    hand-inserting a literal trade_score directly into the table. A
    POSTERIOR_PREDICTIVE_MEAN regret row is written with a real qkernel
    certificate carrying edge_expected > 0 / edge_lcb <= 0; a
    LOWER_CVAR_PARAMETER_DRAWS (robust) twin is written with the identical
    cert shape but the opposite functional. This SQL gate (``trade_score >
    0``) admits a new population of mean-route rejections into
    portfolio-rotation candidate sourcing that it never saw before; the
    robust twin is still excluded exactly as before. Reverting
    _qkernel_regret_trade_score's mean/robust branch selection makes the mean
    route's persisted trade_score go back to edge_lcb (<= 0) and this test
    fails.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    mean_event_id = _write_qkernel_kelly_regret_row_via_reactor(
        conn,
        event_id_suffix="mean",
        global_probability_functional="POSTERIOR_PREDICTIVE_MEAN",
        edge_lcb=-0.05,
        edge_expected=0.03,
        bin_label="mean-bin",
    )
    robust_event_id = _write_qkernel_kelly_regret_row_via_reactor(
        conn,
        event_id_suffix="robust",
        global_probability_functional="LOWER_CVAR_PARAMETER_DRAWS",
        edge_lcb=-0.05,
        edge_expected=0.03,
        bin_label="robust-bin",
    )
    assert mean_event_id != robust_event_id

    candidates, missing = _rotation_candidates(
        conn,
        decision_time=datetime(2026, 6, 7, 6, 30, tzinfo=timezone.utc),
    )

    assert missing == []
    by_event_id = {candidate.event_id: candidate for candidate in candidates}
    assert mean_event_id in by_event_id
    assert by_event_id[mean_event_id].trade_score == pytest.approx(0.03)
    assert robust_event_id not in by_event_id


def test_portfolio_rotation_evaluation_status_reports_positive_value_without_actuator(tmp_path) -> None:
    world_path = tmp_path / "world.db"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    _create_main_schema(conn)
    _create_world_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current VALUES (
            'pos-1', 'trade-1', 'active', 'Seoul', '2026-06-08', 'high',
            'Will the highest temperature in Seoul be 25°C on June 8?',
            'buy_no', 10.0, 0.80, 1, 0.79, 1, 0.79,
            'held-yes-token', 'held-no-token', 'held-condition'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO world.no_trade_regret_events VALUES (
            'evt-1', 'KELLY', 'KELLY_REJECTED:corr_budget', 'Madrid', '2026-06-08',
            'high', 'Will the highest temperature in Madrid be 34°C on June 8?',
            'buy_no', 0.88, 0.55, 1.0, 0.20, 'candidate-token',
            'candidate-condition', '2026-06-07T06:25:00+00:00'
        )
        """
    )
    summary: dict = {}

    _emit_portfolio_rotation_evaluation_status(conn, summary, deps=_deps())

    assert (
        summary["portfolio_rotation_evaluation_status"]
        == "evaluated:positive_rotation_value_no_cross_family_actuator"
    )
    assert summary["portfolio_rotation_held_positions_evaluated"] == 1
    assert summary["portfolio_rotation_candidates_evaluated"] == 1
    best = summary["portfolio_rotation_best"]
    assert best["hold_position_id"] == "pos-1"
    assert best["candidate_event_id"] == "evt-1"
    assert best["net_improvement_usd"] > 0.0


def test_portfolio_rotation_counts_monitor_owned_positive_candidate(tmp_path) -> None:
    world_path = tmp_path / "world.db"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    _create_main_schema(conn)
    _create_world_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current VALUES (
            'pos-1', 'trade-1', 'active', 'Shanghai', '2026-06-25', 'high',
            'Will the highest temperature in Shanghai be 31°C on June 25?',
            'buy_no', 5.6, 0.20, 1, 0.22, 1, 0.22,
            'held-yes-token', 'held-no-token', 'held-condition'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO world.no_trade_regret_events VALUES (
            'evt-sh-buy-yes', 'TRADE_SCORE',
            'EVENT_BOUND_CANDIDATE_REJECTED:OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED',
            'Shanghai', '2026-06-25', 'high',
            'Will the highest temperature in Shanghai be 25°C on June 25?',
            'buy_yes', 0.9616, 0.6712, 1.0, 0.4327, 'candidate-yes-token',
            'candidate-condition', '2026-06-07T06:25:00+00:00'
        )
        """
    )
    summary: dict = {}

    _emit_portfolio_rotation_evaluation_status(conn, summary, deps=_deps())

    assert summary["portfolio_rotation_candidates_evaluated"] == 1
    assert (
        summary["portfolio_rotation_evaluation_status"]
        == "evaluated:positive_rotation_value_no_cross_family_actuator"
    )
    best = summary["portfolio_rotation_best"]
    assert best["candidate_event_id"] == "evt-sh-buy-yes"
    assert best["candidate_direction"] == "buy_yes"
    assert best["net_improvement_usd"] > 0.0


def test_portfolio_rotation_evaluation_status_holds_without_positive_candidate(tmp_path) -> None:
    world_path = tmp_path / "world.db"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    _create_main_schema(conn)
    _create_world_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current VALUES (
            'pos-1', 'trade-1', 'active', 'Seoul', '2026-06-08', 'high',
            'Will the highest temperature in Seoul be 25°C on June 8?',
            'buy_no', 10.0, 0.80, 1, 0.79, 1, 0.79,
            'held-yes-token', 'held-no-token', 'held-condition'
        )
        """
    )
    summary: dict = {}

    _emit_portfolio_rotation_evaluation_status(conn, summary, deps=_deps())

    assert summary["portfolio_rotation_evaluation_status"] == "evaluated:no_capital_constrained_positive_candidates"
    assert summary["portfolio_rotation_held_positions_evaluated"] == 1
    assert summary["portfolio_rotation_candidates_evaluated"] == 0


def test_portfolio_rotation_evaluation_status_is_noop_without_connection() -> None:
    summary: dict = {}

    _emit_portfolio_rotation_evaluation_status(None, summary, deps=_deps())

    assert summary["portfolio_rotation_evaluation_status"] == "unavailable:no_connection"


def test_portfolio_rotation_telemetry_yields_after_held_monitor_deadline() -> None:
    summary: dict = {}

    _emit_portfolio_rotation_evaluation_status(
        sqlite3.connect(":memory:"),
        summary,
        deps=_deps(),
        deadline_monotonic=time.monotonic() - 0.001,
    )

    assert summary["portfolio_rotation_evaluation_status"] == (
        "deferred:held_monitor_deadline_expired"
    )


def test_portfolio_rotation_sql_scan_cannot_outlive_held_monitor_deadline(
    monkeypatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_main_schema(conn)
    conn.execute(
        "INSERT INTO position_current VALUES ("
        "'pos-1', 'trade-1', 'active', 'Seoul', '2026-06-08', 'high', "
        "'bin', 'buy_yes', 1.0, 0.5, 1, 0.5, 1, 0.5, "
        "'yes', 'no', 'condition')"
    )
    clock = iter((10.0, 10.0, 10.0, 11.0, 11.0))
    monkeypatch.setattr(
        "src.engine.cycle_runtime.time.monotonic",
        lambda: next(clock, 11.0),
    )
    summary: dict = {}

    _emit_portfolio_rotation_evaluation_status(
        conn,
        summary,
        deps=_deps(),
        deadline_monotonic=10.5,
    )

    assert summary["portfolio_rotation_evaluation_status"] == (
        "deferred:held_monitor_deadline_expired"
    )
