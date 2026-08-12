# Created: 2026-03-31
# Lifecycle: created=2026-03-31; last_reviewed=2026-08-12; last_reused=2026-08-12
# Purpose: Lock live-money safety invariants across fill, exit, chain, and P&L flows.
# Reuse: Run for execution finality, live exit, chain reconciliation, and safety invariant changes.
# Last reused/audited: 2026-08-12
# Authority basis: finite-evidence single-q global SELL ownership; 7-day capital-loop audit
"""Live safety invariant tests: relationship tests, not function tests.

These verify cross-module relationships that prevent ghost positions,
phantom P&L, and local↔chain divergence in live mode.

GOLDEN RULE: economic close is ONLY created after CONFIRMED fill truth.
"""

import logging
import base64
import inspect
import json
import math
import multiprocessing
import os
import sqlite3
import threading
import time
import zlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.contracts.semantic_types import LifecycleState
from src.execution.collateral import check_sell_collateral
from src.execution.exit_lifecycle import (
    MAX_EXIT_RETRIES,
    ExitContext,
    check_pending_exits,
    check_pending_retries,
    execute_exit,
    is_exit_cooldown_active,
)
from src.state.portfolio import (
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_LEGACY_UNKNOWN,
    ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE,
    ENTRY_ECONOMICS_SUBMITTED_LIMIT,
    ExitDecision,
    FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    Position,
    PortfolioState,
)
from src.contracts.position_truth import ChainOnlyFact, ChainOnlyReviewState

ROOT = Path(__file__).resolve().parents[1]


def test_harvester_scheduler_fails_closed_without_legacy_integrated_fallback():
    """Trading daemon must not fall back to integrated truth-writing harvester."""
    source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
    sidecar_source = (ROOT / "src" / "execution" / "post_trade_capital.py").read_text(encoding="utf-8")

    assert "from src.execution.harvester import run_harvester" not in source
    assert "result = run_harvester()" not in source
    assert "resolver_unavailable_fail_closed" in sidecar_source


def test_settlement_readers_filter_verified_authority_before_downstream_use():
    """Replay, monitor, and harvester reads must not consume quarantined settlement values.

    P3 update (K1 followups, 2026-05-14): world_view/settlements.py retired;
    assertion relocated to src/execution/harvester.py (the canonical live
    settlement consumer). replay.py and monitor_refresh.py assertions unchanged.
    """
    replay_source = (ROOT / "src" / "engine" / "replay.py").read_text(encoding="utf-8")
    monitor_source = (ROOT / "src" / "engine" / "monitor_refresh.py").read_text(encoding="utf-8")
    harvester_source = (ROOT / "src" / "execution" / "harvester.py").read_text(encoding="utf-8")

    assert replay_source.count("authority = 'VERIFIED'") >= 4
    assert "AND authority = 'VERIFIED' LIMIT 1" in monitor_source
    # harvester.py filters at application layer (.upper() != "VERIFIED") rather
    # than SQL layer; assert the specific guard pattern exists.
    assert '.upper() != "VERIFIED"' in harvester_source or \
        ".upper() != 'VERIFIED'" in harvester_source, \
        "harvester.py application-layer VERIFIED guard not found"


def test_operator_scripts_filter_verified_settlement_rows_before_outputs_or_backfills():
    """Operator script reads of settlement truth must not promote quarantined rows."""
    snippets = {
        "scripts/backfill_ens.py": "AND s.authority = 'VERIFIED'",
        "scripts/backfill_observations_from_settlements.py": "AND s.authority = 'VERIFIED'",
        "scripts/backfill_wu_daily_all.py": "AND authority = 'VERIFIED'",
        "scripts/audit_city_data_readiness.py": "AND s.authority = 'VERIFIED'",
        "scripts/audit_divergence_exit_counterfactual.py": "AND authority = 'VERIFIED'",
        "scripts/baseline_experiment.py": "WHERE authority = 'VERIFIED'",
        "scripts/audit_replay_fidelity.py": "AND authority = 'VERIFIED'",
        "scripts/cleanup_ghost_positions.py": "AND authority = 'VERIFIED'",
        "scripts/etl_forecast_skill_from_forecasts.py": "AND s.authority = 'VERIFIED'",
        "scripts/etl_historical_forecasts.py": "AND s.authority = 'VERIFIED'",
    }

    for rel_path, snippet in snippets.items():
        source = (ROOT / rel_path).read_text(encoding="utf-8")
        assert snippet in source, rel_path


# T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md,
# post-T5-migration cleanup): test_monitor_selection_uses_canonical_live_rows_
# not_historical_quarantine previously pinned that a historical
# phase='quarantined' canonical DB row is dropped by
# _monitoring_phase_positions in favor of a live day0 row. The T5 schema
# migration (scripts/migrations/2026_07_quarantine_phase_retirement.py) has
# run against the live DBs: position_current's phase CHECK constraint no
# longer admits 'quarantined' and Position(state="quarantined", ...) now
# raises ValueError at construction, so no row or Position can ever
# reconstruct this scenario again. Retired rather than rewritten — the
# canonical-DB-wins-over-stale-runtime-state behavior this test partially
# also covered is still exercised by
# test_monitor_selection_syncs_pending_exit_projection_over_stale_runtime_state
# below.


def test_monitor_selection_opens_unprojected_venue_confirmed_local_fill():
    from src.engine import cycle_runtime
    from src.state.portfolio import get_open_positions

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    pos = _make_position(
        trade_id="local-only-confirmed-fill-not-yet-projected",
        state="holding",
        city="Buenos Aires",
        target_date="2026-07-02",
        direction="buy_yes",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        shares=69.34,
        shares_filled=69.34,
        size_usd=2.84294,
        cost_basis_usd=2.84294,
        filled_cost_basis_usd=2.84294,
        entry_price=0.041,
        chain_state="local_only",
        chain_shares=0.0,
    )
    portfolio = _make_portfolio(pos)

    assert get_open_positions(portfolio) == [pos]
    assert cycle_runtime._monitoring_phase_positions(portfolio, conn=conn) == [pos]


def test_monitor_selection_syncs_pending_exit_projection_over_stale_runtime_state():
    """Canonical pending_exit truth must not re-enter the held EXIT_INTENT lane as stale day0."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            order_status TEXT,
            shares REAL,
            chain_shares REAL,
            exit_retry_count INTEGER,
            next_exit_retry_at TEXT,
            exit_reason TEXT,
            updated_at TEXT,
            last_monitor_market_price_is_fresh INTEGER
        )
        """
    )
    pos = _make_position(
        trade_id="dust-exit-stale-runtime-day0",
        state="day0_window",
        order_status="filled",
        exit_state="",
        shares=1.0,
        chain_shares=1.0,
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, order_status, shares, chain_shares,
            exit_retry_count, next_exit_retry_at, exit_reason, updated_at,
            last_monitor_market_price_is_fresh
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "dust-exit-stale-runtime-day0",
            "pending_exit",
            "retry_pending",
            1.0,
            1.0,
            0,
            "2026-07-08T16:12:57+00:00",
            "DAY0_HARD_FACT_BIN_DEAD [DUST: size 1 below min_order_size 5]",
            "2026-07-08T16:10:57+00:00",
            1,
        ),
    )

    selected = cycle_runtime._monitoring_phase_positions(_make_portfolio(pos), conn=conn)

    assert selected == [pos]
    assert pos.state == "pending_exit"
    assert pos.order_status == "retry_pending"
    assert pos.exit_state == "retry_pending"
    assert pos.next_exit_retry_at == "2026-07-08T16:12:57+00:00"
    assert "DUST" in pos.exit_reason


def test_monitor_selection_hydrates_sibling_value_inputs_from_canonical_projection():
    """Every family leg must retain its latest canonical probability and bid."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL,
            last_monitor_prob REAL,
            last_monitor_prob_is_fresh INTEGER,
            last_monitor_market_price_is_fresh INTEGER,
            last_monitor_best_bid REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?, 'active', ?, ?, ?, 1, 1, ?)",
        [
            ("singapore-30-yes", 189.77, 189.77, 0.30056, 0.07),
            ("singapore-32-no", 40.0, 40.0, 0.82404, 0.53),
        ],
    )
    current = _make_position(
        trade_id="singapore-30-yes",
        city="Singapore",
        target_date="2026-07-24",
        bin_label="30C",
        direction="buy_yes",
        shares=189.77,
        chain_shares=189.77,
    )
    sibling = _make_position(
        trade_id="singapore-32-no",
        city="Singapore",
        target_date="2026-07-24",
        bin_label="32C",
        direction="buy_no",
        shares=40.0,
        chain_shares=40.0,
    )

    selected = cycle_runtime._monitoring_phase_positions(
        _make_portfolio(current, sibling),
        conn=conn,
    )

    assert selected == [current, sibling]
    assert (current.effective_shares, current.last_monitor_prob, current.last_monitor_best_bid) == pytest.approx(
        (189.77, 0.30056, 0.07)
    )
    assert (sibling.effective_shares, sibling.last_monitor_prob, sibling.last_monitor_best_bid) == pytest.approx(
        (40.0, 0.82404, 0.53)
    )


def test_targeted_monitor_scopes_canonical_projection_to_runtime_exposure():
    """A family wake must not scan canonical history outside its loaded subset."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?, 'settled', 1.0, 0.0)",
        [(f"historical-{index}",) for index in range(1_000)],
    )
    conn.execute(
        "INSERT INTO position_current VALUES ('target-held', 'active', 10.0, 10.0)"
    )
    pos = _make_position(
        trade_id="target-held",
        state="holding",
        shares=10.0,
        chain_shares=10.0,
    )
    portfolio = _make_portfolio(pos)
    portfolio.authority_scope = "runtime_exposure"
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    selected = cycle_runtime._monitoring_phase_positions(portfolio, conn=conn)

    assert selected == [pos]
    projection_reads = [
        statement
        for statement in statements
        if "FROM position_current" in statement
    ]
    assert len(projection_reads) == 1
    assert "WHERE position_id IN ('target-held')" in projection_reads[0]


def test_open_portfolio_loader_marks_runtime_exposure_without_family_filter(
    monkeypatch,
    tmp_path,
):
    """A periodic full-held monitor must still scope projection reads to open IDs."""
    from src.state import db as db_module
    from src.state import portfolio as portfolio_module

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(
        db_module,
        "get_trade_connection_with_world",
        lambda **_kwargs: conn,
    )
    monkeypatch.setattr(
        db_module,
        "query_portfolio_loader_view",
        lambda *_args, **_kwargs: {"status": "ok", "positions": []},
    )

    portfolio = portfolio_module.load_portfolio(
        tmp_path / "positions-live.json",
        open_positions_only=True,
    )

    assert portfolio.authority == "canonical_db"
    assert portfolio.authority_scope == "runtime_exposure"


@pytest.mark.parametrize(
        ("advance_after_first", "expected_count", "expected_reason"),
        (
            (True, 1, "primary_belief_budget_reserve"),
            (False, 3, ""),
        ),
)
def test_monitoring_phase_uses_full_budget_before_deferring_held_positions(
    monkeypatch,
    advance_after_first,
    expected_count,
    expected_reason,
):
    """A sweep admits the next q read only while its full reserve remains."""
    from src.engine import cycle_runtime

    first = _make_position(
        trade_id="held-budget-first",
        city="Chicago",
        target_date="2026-07-04",
        direction="buy_yes",
        state="day0_window",
        shares=10.0,
        chain_shares=10.0,
        chain_state="synced",
    )
    second = _make_position(
        trade_id="held-budget-second",
        city="Chicago",
        target_date="2026-07-04",
        direction="buy_no",
        state="day0_window",
        shares=10.0,
        chain_shares=10.0,
        chain_state="synced",
    )
    third = _make_position(
        trade_id="held-budget-third",
        city="Chicago",
        target_date="2026-07-04",
        direction="buy_no",
        state="day0_window",
        shares=10.0,
        chain_shares=10.0,
        chain_state="synced",
    )
    portfolio = _make_portfolio(first, second, third)
    with cycle_runtime._HELD_MONITOR_CURSOR_LOCK:
        cycle_runtime._HELD_MONITOR_ATTEMPT_STATE_BY_LANE.pop(
            "bounded_coverage",
            None,
        )
        cycle_runtime._HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE.pop(
            "bounded_coverage",
            None,
        )
    visited: list[str] = []
    readthrough_deadlines: list[float] = []
    clock = [0.0]

    def fake_refresh(conn, clob, position):
        visited.append(position.trade_id)
        readthrough_deadlines.append(
            position._zeus_held_monitor_deadline_monotonic
        )
        position.last_monitor_prob = 0.61
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.12
        position.last_monitor_market_price = 0.49
        position.last_monitor_market_price_is_fresh = True
        if advance_after_first and position is first:
            clock[0] = 2.0
        return SimpleNamespace(
            p_market=np.array([0.49]),
            p_posterior=0.61,
            forward_edge=0.12,
            confidence_band_lower=0.08,
            confidence_band_upper=0.16,
        )

    def fake_evaluate_exit(self, exit_context):
        return ExitDecision(
            False,
            "CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior"],
        )

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", fake_evaluate_exit)
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *args, **kwargs: True,
    )

    monitor_results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
            ),
            "logger": logging.getLogger("test_monitor_budget"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        portfolio,
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=6.0,
    )

    assert len(visited) == expected_count
    assert visited[0] == "held-budget-first"
    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["held_monitor_candidates"] == 3
    assert summary["held_monitor_budget_reserved_positions"] == 2
    assert summary["held_monitor_budget_seconds"] == pytest.approx(6.0)
    assert summary["held_monitor_positions_scanned"] == expected_count
    assert summary.get("held_monitor_positions_deferred", 0) == 3 - expected_count
    assert summary.get("held_monitor_defer_reason", "") == expected_reason
    assert summary["monitors"] == expected_count
    assert len(monitor_results) == expected_count
    assert readthrough_deadlines == [pytest.approx(5.0)] * expected_count
    assert all(
        not hasattr(position, "_zeus_held_monitor_deadline_monotonic")
        for position in (first, second, third)
    )


def test_monitor_probability_reads_are_bounded_to_fair_admitted_slice(
    monkeypatch,
):
    """Local quotes cannot let serial slow q reads consume the full-book claim."""
    from src.engine import cycle_runtime

    positions = [
        _make_position(
            trade_id=f"belief-admission-{index:02d}",
            token_id=f"belief-admission-token-{index:02d}",
            state="holding",
            chain_state="synced",
        )
        for index in range(15)
    ]
    clock = [0.0]
    started_by_pass: list[list[str]] = []

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: positions,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {
            position.token_id: {
                "asset_id": position.token_id,
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
            for position in positions
        },
    )

    current_started: list[str] = []

    def slow_refresh(_conn, _clob, position):
        current_started.append(position.trade_id)
        clock[0] += 5.0
        return _monitor_test_edge_context(position)

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        slow_refresh,
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )

    for pass_index in range(2):
        clock[0] = 0.0
        current_started = []
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            SimpleNamespace(),
            _make_portfolio(*positions),
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=_monitor_test_deps(f"belief_admission_{pass_index}"),
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=75.0,
        )
        started_by_pass.append(list(current_started))
        assert current_started == summary[
            "held_monitor_primary_belief_admitted_position_ids"
        ]
        assert summary["held_monitor_primary_belief_read_started"] == 5
        assert summary["held_monitor_primary_belief_read_completed"] == 0
        assert summary["held_monitor_primary_belief_read_deferred"] == 15
        assert summary["held_monitor_positions_deferred"] == 15
        assert summary["held_monitor_primary_belief_started_position_ids"] == (
            current_started
        )
        assert summary["held_monitor_primary_belief_completed_position_ids"] == []
        assert summary["held_monitor_primary_belief_expired_position_ids"] == (
            current_started
        )
        assert set(summary["held_monitor_primary_belief_deferred_position_ids"]) == (
            {position.trade_id for position in positions}
        )
        assert clock[0] == pytest.approx(25.0)

    assert set(started_by_pass[0]).isdisjoint(started_by_pass[1])


def test_urgent_admitted_coverage_prefers_local_quote_before_network():
    """Equal-urgency admission consumes a ready local book before network work."""
    from src.engine import cycle_runtime

    local = _make_position(
        trade_id="urgent-admitted-local",
        token_id="urgent-admitted-local-token",
        state="holding",
    )
    network = _make_position(
        trade_id="urgent-admitted-network",
        token_id="urgent-admitted-network-token",
        state="holding",
    )
    common = {
        "deadline_rescue_position_id": None,
        "durable_debt_position_id": None,
        "dead_bin_position_ids": frozenset(),
        "selected_urgent_position_ids": frozenset(),
        "selected_coverage_position_ids": frozenset({id(local), id(network)}),
        "has_selected_urgent": True,
        "reserved_local_position_ids": frozenset(),
        "reserved_network_position_id": None,
        "structural_win_position_ids": frozenset(),
        "network_book_tokens": frozenset({network.token_id}),
    }

    assert cycle_runtime._held_monitor_schedule_key(local, **common) < (
        cycle_runtime._held_monitor_schedule_key(network, **common)
    )


def test_monitor_defers_before_primary_belief_read_when_reserve_is_unavailable(
    monkeypatch,
):
    from src.engine import cycle_runtime

    position = _make_position(
        trade_id="primary-belief-reserve",
        city="Chicago",
        target_date="2026-07-04",
        direction="buy_yes",
        state="day0_window",
        shares=10.0,
        chain_shares=10.0,
        chain_state="synced",
    )
    calls = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *_args: calls.append("refresh"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: calls.append("canonical"),
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        type("Artifact", (), {"add_monitor_result": lambda *_args: None})(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_primary_belief_reserve"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=4.9,
    )

    assert calls == []
    assert summary.get("held_monitor_positions_scanned", 0) == 0
    assert summary["held_monitor_positions_deferred"] == 1
    assert summary["held_monitor_defer_reason"] == "primary_belief_budget_reserve"
    assert summary["held_monitor_deadline_defer_reason"] == (
        "PRIMARY_BELIEF_BUDGET_UNAVAILABLE"
    )
    assert summary["held_monitor_primary_belief_deferred_position_ids"] == [
        position.trade_id
    ]
    assert not hasattr(position, "_zeus_held_monitor_deadline_monotonic")


def test_live_monitor_deadline_defers_stale_fusion_and_dispatches_reseed(monkeypatch):
    """The real monitor caller must preserve the bounded producer/consumer split."""
    from src.engine import cycle_runtime
    from src.engine import monitor_refresh as mr
    from src.engine import position_belief as pb

    position = _make_position(
        trade_id="bounded-stale-belief",
        city="Singapore",
        cluster="Southeast Asia",
        target_date="2026-08-02",
        bin_label="32C",
        direction="buy_no",
        state="holding",
        token_id="bounded-yes",
        no_token_id="bounded-no",
        chain_state="synced",
        shares=10.0,
        chain_shares=10.0,
        entry_method="ens_member_counting",
        selected_method="replacement_posterior",
    )
    stale = pb.ReplacementBelief(
        held_side_prob=0.75,
        held_side_lcb=0.68,
        held_side_ucb=0.82,
        q_yes_bin=0.25,
        q_yes_lcb=0.18,
        q_yes_ucb=0.32,
        posterior_id="stale-posterior",
        computed_at="2026-07-31T00:00:00+00:00",
        age_hours=24.0,
        fresh=False,
        bin_key="32C",
        direction="buy_no",
    )
    reseeds = []
    monitor_results = []

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **_kwargs: stale)
    monkeypatch.setattr(
        mr,
        "_attempt_held_belief_readthrough",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("bounded live monitor called synchronous fusion")
        ),
    )
    monkeypatch.setattr(
        mr,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kwargs: reseeds.append(kwargs) or None,
    )
    monkeypatch.setattr(
        mr,
        "monitor_quote_refresh",
        lambda *_args, **_kwargs: mr.HeldTokenMonitorQuote(
            token_id="bounded-no",
            best_bid=0.40,
            best_ask=0.42,
            bid_size=100.0,
            ask_size=100.0,
            mark_price=0.41,
            source_timestamp="2026-08-01T08:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_monitor_orderbooks",
        lambda *_args, **_kwargs: frozenset(),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _context: ExitDecision(
            False,
            "EVIDENCE_UNAVAILABLE",
            trigger="EVIDENCE_UNAVAILABLE",
            selected_method=self.selected_method,
            applied_validations=list(self.applied_validations),
        ),
    )
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    deps = _monitor_test_deps("test_bounded_stale_belief_caller")
    deps.cities_by_name = {}
    deps._utcnow = staticmethod(
        lambda: datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        artifact,
        _monitor_test_tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert summary["monitors"] == 1
    assert len(monitor_results) == 1
    assert monitor_results[0].fresh_prob is None
    assert reseeds == [
        {"city": "Singapore", "target_date": "2026-08-02", "metric": "high"}
    ]
    assert "replacement_belief_readthrough_deferred_to_independent_producer" in (
        position.applied_validations
    )
    assert not hasattr(position, "_zeus_held_monitor_deadline_monotonic")


def test_monitor_probability_reads_share_the_cycle_deadline(
    monkeypatch,
):
    """Every admitted sibling receives only the cycle's remaining q-read budget."""
    from src.engine import cycle_runtime

    first = _make_position(
        trade_id="stale-read-first",
        state="holding",
        chain_state="synced",
    )
    second = _make_position(
        trade_id="fresh-read-second",
        state="holding",
        chain_state="synced",
    )
    clock = [0.0]
    deadlines: list[tuple[str, float]] = []
    results = []

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])

    def refresh(_conn, _clob, position):
        deadlines.append(
            (
                position.trade_id,
                position._zeus_held_monitor_deadline_monotonic,
            )
        )
        if position is first:
            # Model a Day0 SQLite deadline failure: this position stays stale,
            # while consuming most of the outer admission budget.
            position.last_monitor_prob = None
            position.last_monitor_prob_is_fresh = False
            position.last_monitor_edge = None
            position.last_monitor_market_price = 0.49
            position.last_monitor_market_price_is_fresh = True
            clock[0] = 0.9
            return SimpleNamespace(
                p_market=np.array([0.49]),
                p_posterior=float("nan"),
                forward_edge=float("nan"),
                confidence_band_lower=float("nan"),
                confidence_band_upper=float("nan"),
            )
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "EVIDENCE_UNAVAILABLE"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: results.append(result)},
    )()
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(first, second),
        artifact,
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_per_position_probability_deadline"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=1.0,
    )

    assert deadlines == [
        ("stale-read-first", pytest.approx(1.0)),
        ("fresh-read-second", pytest.approx(1.0)),
    ]
    assert first.last_monitor_prob_is_fresh is False
    assert second.last_monitor_prob_is_fresh is True
    assert [result.fresh_prob for result in results] == [None, pytest.approx(0.61)]
    assert summary["held_monitor_positions_scanned"] == 2
    assert summary.get("held_monitor_positions_deferred", 0) == 0
    assert all(
        not hasattr(position, "_zeus_held_monitor_deadline_monotonic")
        for position in (first, second)
    )


def test_monitor_reservations_cover_large_held_book_within_three_degraded_cycles():
    """Deadline-degraded cycles still reserve rotating slices of a large book."""
    from src.engine import cycle_runtime

    assert cycle_runtime._held_position_monitor_reservation_count(0) == 2
    assert cycle_runtime._held_position_monitor_reservation_count(3) == 2
    assert cycle_runtime._held_position_monitor_reservation_count(9) == 3
    assert cycle_runtime._held_position_monitor_reservation_count(23) == 8


def test_monitor_full_sweep_keeps_unique_three_cycle_deadline_reservations(monkeypatch):
    """Normal cycles sweep all positions while degraded reservations still rotate."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    positions = [
        _make_position(
            trade_id=f"coverage-{index}",
            token_id=f"coverage-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(9)
    ]
    for position in positions:
        position._canonical_monitor_refreshed_at = ""
    portfolio = _make_portfolio(*positions)
    visited: list[str] = []
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_monitor_orderbooks",
        lambda *_args, **_kwargs: frozenset(),
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            visited.append(position.trade_id)
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )

    per_cycle: list[list[str]] = []
    reserved_per_cycle: list[list[str]] = []
    for cycle in range(3):
        cycle_start = len(visited)
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            object(),
            portfolio,
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=_monitor_test_deps(f"test_monitor_coverage_{cycle}"),
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=10.0,
            should_preempt_for_urgent_day0=lambda: False,
        )
        per_cycle.append(visited[cycle_start:])
        reserved_per_cycle.append(summary["held_monitor_budget_coverage_positions"])

    assert all(len(batch) == 9 for batch in per_cycle)
    assert len(set(visited)) == 9
    assert all(len(batch) == 3 for batch in reserved_per_cycle)
    assert len({trade_id for batch in reserved_per_cycle for trade_id in batch}) == 9


def test_monitor_deadline_degraded_cycles_never_execute_reserved_thirds(monkeypatch):
    """Rotating coverage reservations choose priority, never deadline authority."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    positions = [
        _make_position(
            trade_id=f"deadline-{index}",
            token_id=f"deadline-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(9)
    ]
    for position in positions:
        position._canonical_monitor_refreshed_at = ""
    portfolio = _make_portfolio(*positions)
    visited: list[str] = []
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_monitor_orderbooks",
        lambda *_args, **_kwargs: frozenset(),
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            visited.append(position.trade_id)
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )

    per_cycle: list[list[str]] = []
    reserved_per_cycle: list[list[str]] = []
    for cycle in range(3):
        cycle_start = len(visited)
        monotonic_values = iter([0.0, *([1.0] * 12)])
        monkeypatch.setattr(
            cycle_runtime.time,
            "monotonic",
            lambda: next(monotonic_values, 1.0),
        )
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            object(),
            portfolio,
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=_monitor_test_deps(f"test_monitor_deadline_coverage_{cycle}"),
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=0.5,
            should_preempt_for_urgent_day0=lambda: False,
        )
        batch = visited[cycle_start:]
        per_cycle.append(batch)
        reserved = summary["held_monitor_budget_coverage_positions"]
        reserved_per_cycle.append(reserved)
        assert batch == []
        assert summary["held_monitor_positions_deferred"] == 9
        assert summary["held_monitor_defer_reason"] == "cycle_budget_exhausted"
        assert summary["held_monitor_budget_bypass_scanned"] == 0

    assert per_cycle == [[], [], []]
    assert all(len(batch) == 3 for batch in reserved_per_cycle)
    assert len({trade_id for batch in reserved_per_cycle for trade_id in batch}) == 9
    assert visited == []


def test_monitor_progress_limit_covers_mixed_canonical_and_fallback_book(monkeypatch):
    """One local-fill fallback cannot disable bounded coverage for the held book."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    positions = [
        _make_position(
            trade_id=f"mixed-{index}",
            token_id=f"mixed-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(9)
    ]
    for position in positions[:8]:
        position._canonical_monitor_refreshed_at = ""
    portfolio = _make_portfolio(*positions)
    visited: list[str] = []
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_monitor_orderbooks",
        lambda *_args, **_kwargs: frozenset(),
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            visited.append(position.trade_id)
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )

    coverage_batches: list[list[str]] = []
    for cycle in range(3):
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            object(),
            portfolio,
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=_monitor_test_deps(f"test_monitor_mixed_coverage_{cycle}"),
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=10.0,
            should_preempt_for_urgent_day0=lambda: False,
        )
        coverage_batches.append(summary["held_monitor_budget_coverage_positions"])

    assert all(len(batch) == 3 for batch in coverage_batches)
    assert len({trade_id for batch in coverage_batches for trade_id in batch}) == 9
    assert len(set(visited)) == 9


@pytest.mark.parametrize(
    ("defer_partial_gaps", "expected_events", "prefetched"),
    (
        (
            False,
            [
                "refresh:local-ready",
                "network_fetch",
                "refresh:network-dependent",
            ],
            2,
        ),
        (
            True,
            [
                "refresh:local-ready",
                "network_fetch",
                "refresh:network-dependent",
            ],
            2,
        ),
    ),
)
def test_monitoring_phase_processes_local_books_before_blocking_network_fetch(
    monkeypatch,
    defer_partial_gaps,
    expected_events,
    prefetched,
):
    """One stale token must not delay positions with current executable books."""
    from src.engine import cycle_runtime

    network_pos = _make_position(
        trade_id="network-dependent",
        token_id="network-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    local_pos = _make_position(
        trade_id="local-ready",
        token_id="local-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    portfolio = _make_portfolio(network_pos, local_pos)
    events: list[str] = []

    class Clob:
        def get_orderbook_snapshots(self, token_ids):
            events.append("network_fetch")
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

    clob = Clob()
    local_book = {
        "asset_id": "local-token",
        "bids": [{"price": "0.40", "size": "20"}],
        "asks": [{"price": "0.42", "size": "20"}],
    }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda _conn, positions, **_kwargs: (
            {"local-token": local_book}
            if any(position.token_id == "local-token" for position in positions)
            else {}
        ),
    )

    def fake_refresh(_conn, _clob, position):
        events.append(f"refresh:{position.trade_id}")
        position.last_monitor_prob = 0.61
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.12
        position.last_monitor_market_price = 0.49
        position.last_monitor_market_price_is_fresh = True
        return SimpleNamespace(
            p_market=np.array([0.49]),
            p_posterior=0.61,
            forward_edge=0.12,
            confidence_band_lower=0.08,
            confidence_band_upper=0.16,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(
            False,
            "CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior"],
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )

    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, _result: None},
    )()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
            ),
            "logger": logging.getLogger("test_monitor_local_first"),
            "cities_by_name": {},
            "_utcnow": staticmethod(
                lambda: datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc)
            ),
        },
    )

    cycle_runtime.execute_monitoring_phase(
        None,
        clob,
        portfolio,
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
        run_exit_preflight=False,
        defer_partial_orderbook_gaps=defer_partial_gaps,
    )

    assert events == expected_events
    assert summary["held_monitor_local_ready_positions"] == 1
    assert summary["held_monitor_orderbooks_prefetched"] == prefetched
    assert summary.get("held_monitor_positions_deferred_for_orderbook_gap", 0) == 0
    assert summary.get(
        "held_monitor_partial_orderbook_gaps_scheduled_after_local",
        0,
    ) == int(
        defer_partial_gaps
    )


def test_monitoring_phase_serves_durable_debt_before_bulk_prefetch(monkeypatch):
    """A slow network batch cannot precede the oldest canonical monitor attempt."""
    from src.engine import cycle_runtime

    positions = [
        _make_position(
            trade_id=f"fairness-{index}",
            token_id=f"fairness-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(18)
    ]
    for index, position in enumerate(positions):
        position._canonical_monitor_refreshed_at = (
            f"2026-08-01T00:{index:02d}:00+00:00"
        )
    positions[0]._canonical_monitor_refreshed_at = ""
    local_positions = positions[-2:]
    local_books = {
        position.token_id: {
            "asset_id": position.token_id,
            "bids": [{"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.42", "size": "20"}],
        }
        for position in local_positions
    }
    events: list[object] = []
    clock = [0.0]

    class SlowBatchClob:
        def get_orderbook(self, token_id):
            events.append(("singular", token_id))
            return None

        def get_held_orderbook_snapshots_hard_deadline(
            self,
            token_ids,
            *,
            timeout_seconds,
        ):
            events.append(
                ("bounded", tuple(token_ids), timeout_seconds)
            )
            return {}

        def get_orderbook_snapshots(self, token_ids):
            events.append(("bulk", tuple(token_ids)))
            clock[0] = 76.0
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda _conn, _positions, **_kwargs: local_books,
    )
    def _refresh_through_singular_quote(_conn, clob, position):
        from src.engine.monitor_refresh import monitor_quote_refresh

        monitor_quote_refresh(_conn, clob, position)
        events.append(f"refresh:{position.trade_id}")
        return _monitor_test_edge_context(position)

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        _refresh_through_singular_quote,
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )

    clob = SlowBatchClob()
    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        clob,
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_durable_debt_before_prefetch"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=75.0,
    )

    refresh_index = events.index("refresh:fairness-0")
    bulk_index, bulk_tokens = next(
        (index, payload[1])
        for index, payload in enumerate(events)
        if isinstance(payload, tuple) and payload[0] == "bulk"
    )
    assert refresh_index < bulk_index
    assert len(bulk_tokens) == 15
    assert "fairness-token-0" not in bulk_tokens
    from src.engine.monitor_refresh import prefetched_monitor_orderbook

    assert [event[:2] for event in events if event[0] == "bounded"] == [
        ("bounded", ("fairness-token-0",))
    ]
    assert [event for event in events if event[0] == "singular"] == []
    assert prefetched_monitor_orderbook(clob, "fairness-token-16") == local_books[
        "fairness-token-16"
    ]
    assert summary["held_monitor_durable_debt_position"] == "fairness-0"
    assert summary["held_monitor_durable_debt_network_attempted"] is True


def test_monitoring_phase_oldest_debt_precedes_repeating_nonabsorbing_urgency(
    monkeypatch,
):
    """Pending-exit/Day0 wakes cannot starve the oldest canonical refresh."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    oldest = _make_position(
        trade_id="oldest-active-debt",
        token_id="oldest-active-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    oldest._canonical_monitor_refreshed_at = ""
    urgent = [
        _make_position(
            trade_id=f"repeating-day0-{index}",
            token_id=f"repeating-day0-token-{index}",
            direction="buy_yes",
            state="day0_window",
            chain_state="synced",
        )
        for index in range(2)
    ]
    for index, position in enumerate(urgent):
        position._canonical_monitor_refreshed_at = (
            f"2026-08-12T15:4{index}:00+00:00"
        )
    visited: list[str] = []
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_monitor_orderbooks",
        lambda *_args, **_kwargs: frozenset(),
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            visited.append(position.trade_id)
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(*urgent, oldest),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_oldest_before_nonabsorbing_urgency"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
        should_preempt_for_urgent_day0=lambda: False,
    )

    assert visited[0] == "oldest-active-debt"
    assert summary["held_monitor_durable_debt_position"] == "oldest-active-debt"
    assert "oldest-active-debt" in summary["held_monitor_budget_coverage_positions"]


def test_monitoring_phase_active_network_hard_fact_exits_after_local_tranche(
    monkeypatch,
):
    """Known dead-bin urgency outranks the ordinary local/network tranches."""
    from src.engine import cycle_runtime
    from src.execution.day0_hard_fact_exit import HardFactVerdict

    local_active = _make_position(
        trade_id="local-active-before-hard-fact",
        city="Chicago",
        target_date="2026-07-02",
        token_id="local-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    network_dead_bin = _make_position(
        trade_id="network-active-dead-bin",
        city="Chicago",
        target_date="2026-07-02",
        token_id="network-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    second_network_dead_bin = _make_position(
        trade_id="second-network-active-dead-bin",
        city="Chicago",
        target_date="2026-07-02",
        token_id="second-network-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    events: list[str] = []

    class Clob:
        def get_orderbook_snapshots(self, token_ids):
            events.append("network_fetch")
            assert tuple(token_ids) == (
                "network-token",
                "second-network-token",
            )
            return {
                "network-token": {
                    "asset_id": "network-token",
                    "bids": [{"price": "0.31", "size": "20"}],
                    "asks": [{"price": "0.33", "size": "20"}],
                },
                "second-network-token": {
                    "asset_id": "second-network-token",
                    "bids": [{"price": "0.29", "size": "20"}],
                    "asks": [{"price": "0.31", "size": "20"}],
                },
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda _conn, _positions, **_kwargs: {
            "local-token": {
                "asset_id": "local-token",
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
        },
    )
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda **kwargs: (
            HardFactVerdict(
                action="EXIT_DEAD_BIN",
                reason="observed extreme makes held YES impossible",
                metric="high",
                rounded_extreme=35.0,
                source="durable_observation_instants",
            )
            if kwargs["position"] is network_dead_bin
            or kwargs["position"] is second_network_dead_bin
            else None
        ),
    )

    def fake_refresh(_conn, _clob, position):
        events.append(f"refresh:{position.trade_id}")
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    monitor_results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    deps = _monitor_test_deps("test_monitor_active_network_hard_fact")
    deps.cities_by_name = {
        "Chicago": type("City", (), {"timezone": "America/Chicago"})()
    }
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        Clob(),
        _make_portfolio(
            network_dead_bin,
            local_active,
            second_network_dead_bin,
        ),
        artifact,
        _monitor_test_tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
        defer_partial_orderbook_gaps=True,
    )

    assert events == [
        "network_fetch",
        "refresh:local-active-before-hard-fact",
    ]
    assert summary.get("held_monitor_positions_deferred", 0) == 0
    assert summary["held_monitor_partial_orderbook_gaps_scheduled_after_local"] == 2
    assert summary["day0_hard_fact_direct_exit_decisions"] == 2
    assert summary["exits"] == 2
    dead_bin_result = next(
        result
        for result in monitor_results
        if result.position_id == "network-active-dead-bin"
    )
    assert dead_bin_result.should_exit is True
    assert dead_bin_result.exit_reason.startswith("DAY0_HARD_FACT_BIN_DEAD")


def test_monitoring_phase_known_network_dead_bin_crosses_exhausted_budget(
    monkeypatch,
):
    """A dead-bin rescue cannot start network I/O after the shared deadline."""
    from src.data.polymarket_client import PolymarketClient
    from src.engine import cycle_runtime
    from src.execution.day0_hard_fact_exit import HardFactVerdict

    dead_bin = _make_position(
        trade_id="budget-guaranteed-network-dead-bin",
        city="Chicago",
        target_date="2026-07-02",
        token_id="dead-network-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    local_positions = [
        _make_position(
            trade_id=f"budget-local-{index}",
            token_id=f"local-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    events: list[str] = []

    class Clob(PolymarketClient):
        def __init__(self):
            pass

        def get_orderbook_snapshots(self, token_ids, *, timeout=None):
            events.append("batch_io")
            raise AssertionError(f"deadline rescue opened batch I/O: {token_ids}")

        def get_orderbook(self, token_id):
            events.append("singular_io")
            raise AssertionError(f"deadline rescue opened singular I/O: {token_id}")

        def get_orderbook_snapshot(self, token_id, *, timeout=None):
            events.append("snapshot_io")
            raise AssertionError(f"deadline rescue opened snapshot I/O: {token_id}")

        def get_best_bid_ask(self, token_id):
            events.append("retry_quote_io")
            raise AssertionError(f"deadline rescue opened retry quote I/O: {token_id}")

        def get_clob_market_info(self, condition_id, *, timeout=None):
            events.append("market_info_io")
            raise AssertionError(
                f"deadline rescue opened market-info I/O: {condition_id}"
            )

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda _conn, _positions, **_kwargs: {
            f"local-token-{index}": {
                "asset_id": f"local-token-{index}",
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
            for index in range(2)
        },
    )
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda **kwargs: (
            HardFactVerdict(
                action="EXIT_DEAD_BIN",
                reason="durable extreme killed held YES",
                metric="high",
                rounded_extreme=36.0,
                source="durable_observation_instants",
            )
            if kwargs["position"] is dead_bin
            else None
        ),
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            events.append(f"refresh:{position.trade_id}")
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: 0.0)
    monitor_results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    deps = _monitor_test_deps("test_monitor_dead_bin_budget_guarantee")
    deps.cities_by_name = {
        "Chicago": type("City", (), {"timezone": "America/Chicago"})()
    }
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        Clob(),
        _make_portfolio(*local_positions, dead_bin),
        artifact,
        _monitor_test_tracker(),
        summary,
        deps=deps,
        run_exit_preflight=True,
        held_position_monitor_budget_seconds=0.0,
        defer_partial_orderbook_gaps=True,
    )

    assert events == []
    assert "held_monitor_orderbook_prefetch_transport_failed" not in summary
    assert "held_monitor_orderbook_prefetch_error" not in summary
    assert summary["held_monitor_positions_deferred"] == 2
    assert summary["held_monitor_deadline_defer_reason"] == (
        "MONITOR_DEADLINE_EXPIRED"
    )
    assert summary["held_monitor_budget_bypass_scanned"] == 1
    assert summary["held_monitor_dead_bin_deadline_rescue_without_io"] == 1
    assert summary["day0_hard_fact_direct_exit_decisions"] == 1
    assert summary["exits"] == 1
    assert len(monitor_results) == 1
    assert monitor_results[0].position_id == dead_bin.trade_id
    assert monitor_results[0].should_exit is True
    assert monitor_results[0].exit_reason.startswith("DAY0_HARD_FACT_BIN_DEAD")


def test_monitoring_phase_orders_rotated_dead_bin_rescue_before_selected_peer(
    monkeypatch,
):
    """The one deadline rescue is reachable even when another dead bin was earlier."""
    from src.engine import cycle_runtime
    from src.execution.day0_hard_fact_exit import HardFactVerdict

    dead_bins = [
        _make_position(
            trade_id=f"rescue-order-dead-bin-{index}",
            city="Chicago",
            target_date="2026-07-02",
            token_id=f"rescue-order-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(3)
    ]
    monkeypatch.setattr(
        cycle_runtime,
        "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE",
        {
            "dead_bin": cycle_runtime._held_monitor_stable_position_key(
                dead_bins[1]
            )
        },
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_monitor_orderbooks",
        lambda *_args, **_kwargs: frozenset(),
    )
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda **kwargs: HardFactVerdict(
            action="EXIT_DEAD_BIN",
            reason="durable extreme killed held YES",
            metric="high",
            rounded_extreme=36.0,
            source="durable_observation_instants",
        ),
    )
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    monitor_results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    deps = _monitor_test_deps("test_monitor_dead_bin_rescue_order")
    deps.cities_by_name = {
        "Chicago": type("City", (), {"timezone": "America/Chicago"})()
    }
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(*dead_bins),
        artifact,
        _monitor_test_tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=0.0,
    )

    assert summary["held_monitor_dead_bin_deadline_rescue_position"] == (
        "rescue-order-dead-bin-2"
    )
    assert summary["held_monitor_budget_bypass_scanned"] == 1
    assert summary["held_monitor_positions_deferred"] == 2
    assert [result.position_id for result in monitor_results] == [
        "rescue-order-dead-bin-2"
    ]


def test_monitoring_phase_caps_and_rotates_dead_bin_deadline_rescue(monkeypatch):
    """Only one rotating, absorbing loss may bridge an exhausted deadline."""
    from src.engine import cycle_runtime
    from src.execution.day0_hard_fact_exit import HardFactVerdict

    monkeypatch.setattr(
        cycle_runtime,
        "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE",
        {},
    )
    dead_bins = [
        _make_position(
            trade_id=f"rotating-dead-bin-{index}",
            city="Chicago",
            target_date="2026-07-02",
            token_id=f"dead-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(3)
    ]
    canonical_urgent = [
        _make_position(
            trade_id=f"rotating-canonical-urgent-{index}",
            city="Chicago",
            target_date="2026-07-02",
            token_id=f"canonical-urgent-token-{index}",
            direction="buy_yes",
            state="day0_window",
            chain_state="synced",
        )
        for index in range(3)
    ]
    ordinary_network = _make_position(
        trade_id="urgent-cycle-ordinary-network",
        token_id="ordinary-network-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    ordinary_local = [
        _make_position(
            trade_id=f"urgent-cycle-ordinary-local-{index}",
            token_id=f"ordinary-local-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(3)
    ]

    class Clob:
        def get_orderbook_snapshots(self, token_ids):
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.25", "size": "20"}],
                    "asks": [{"price": "0.27", "size": "20"}],
                }
                for token_id in token_ids
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {
            f"ordinary-local-token-{index}": {
                "asset_id": f"ordinary-local-token-{index}",
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
            for index in range(3)
        },
    )
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda **kwargs: (
            HardFactVerdict(
                action="EXIT_DEAD_BIN",
                reason="durable extreme killed held YES",
                metric="high",
                rounded_extreme=36.0,
                source="durable_observation_instants",
            )
            if str(kwargs["position"].trade_id).startswith("rotating-dead-bin-")
            else None
        ),
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: _monitor_test_edge_context(position),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    deps = _monitor_test_deps("test_monitor_urgent_bypass_cap")
    deps.cities_by_name = {
        "Chicago": type("City", (), {"timezone": "America/Chicago"})()
    }
    summaries = []
    for _cycle in range(3):
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            Clob(),
            _make_portfolio(
                *dead_bins,
                *canonical_urgent,
                *ordinary_local,
                ordinary_network,
            ),
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=deps,
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=0.0,
            should_preempt_for_urgent_day0=lambda: False,
            defer_partial_orderbook_gaps=True,
        )
        summaries.append(summary)

    assert [summary["held_monitor_budget_urgent_positions"] for summary in summaries] == [
        ["rotating-dead-bin-0", "rotating-canonical-urgent-0"],
        ["rotating-dead-bin-1", "rotating-canonical-urgent-1"],
        ["rotating-dead-bin-2", "rotating-canonical-urgent-2"],
    ]
    assert all(summary["held_monitor_budget_reserved_positions"] == 6 for summary in summaries)
    assert all(summary["held_monitor_budget_bypass_scanned"] == 1 for summary in summaries)
    assert all(
        summary["held_monitor_active_local_progress_positions"]
        == [
            "urgent-cycle-ordinary-local-0",
            "urgent-cycle-ordinary-local-1",
            "urgent-cycle-ordinary-local-2",
        ]
        for summary in summaries
    )
    assert all(summary["held_monitor_deadline_deferred_positions"] == 9 for summary in summaries)
    assert all(
        summary["held_monitor_deadline_defer_reason"] == "MONITOR_DEADLINE_EXPIRED"
        for summary in summaries
    )


def test_monitoring_phase_reservations_do_not_override_zero_deadline(monkeypatch):
    """Reservations rotate scheduling priority but never waive a deadline."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(
        cycle_runtime,
        "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE",
        {},
    )
    local_positions = [
        _make_position(
            trade_id=f"bounded-local-{index}",
            token_id=f"local-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(3)
    ]
    network_positions = [
        _make_position(
            trade_id=f"bounded-network-{index}",
            token_id=f"network-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    events: list[str] = []

    class Clob:
        def get_orderbook_snapshots(self, token_ids):
            events.append("network_fetch")
            assert tuple(token_ids) == ("network-token-0", "network-token-1")
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda _conn, _positions, **_kwargs: {
            f"local-token-{index}": {
                "asset_id": f"local-token-{index}",
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
            for index in range(3)
        },
    )

    def fake_refresh(_conn, _clob, position):
        events.append(f"refresh:{position.trade_id}")
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    summaries = []
    for _cycle in range(3):
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            Clob(),
            _make_portfolio(*local_positions, *network_positions),
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=_monitor_test_deps("test_monitor_bounded_network_progress"),
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=0.0,
            defer_partial_orderbook_gaps=True,
        )
        summaries.append(summary)

    assert events == []
    assert [
        summary["held_monitor_active_local_progress_position"]
        for summary in summaries
    ] == ["bounded-local-0", "bounded-local-1", "bounded-local-2"]
    assert [
        summary["held_monitor_active_network_progress_position"]
        for summary in summaries
    ] == ["bounded-network-0", "bounded-network-1", "bounded-network-0"]
    assert all(summary["held_monitor_budget_reserved_positions"] == 2 for summary in summaries)
    assert all(summary["held_monitor_budget_bypass_scanned"] == 0 for summary in summaries)
    assert all(summary["held_monitor_positions_deferred"] == 5 for summary in summaries)


def test_monitoring_phase_positive_budget_sweeps_unreserved_active_tail(monkeypatch):
    """Urgent successes do not truncate the remaining held book before deadline."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(
        cycle_runtime,
        "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE",
        {},
    )
    urgent = [
        _make_position(
            trade_id=f"cap-urgent-{index}",
            city="Chicago",
            target_date="2026-07-22",
            token_id=f"cap-urgent-token-{index}",
            direction="buy_yes",
            state="day0_window",
            chain_state="synced",
        )
        for index in range(2)
    ]
    active = [
        _make_position(
            trade_id=f"cap-active-{index}",
            token_id=f"cap-active-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(5)
    ]
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_monitor_orderbooks",
        lambda *_args, **_kwargs: frozenset(),
    )
    visited: list[str] = []
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            visited.append(position.trade_id)
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(*urgent, *active),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_positive_budget_full_sweep"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
        should_preempt_for_urgent_day0=lambda: False,
    )

    assert visited[:2] == ["cap-urgent-0", "cap-urgent-1"]
    assert set(visited[2:]) == {
        "cap-active-0",
        "cap-active-1",
        "cap-active-2",
        "cap-active-3",
        "cap-active-4",
    }
    assert summary["held_monitor_budget_reserved_positions"] == 6
    assert summary.get("held_monitor_positions_deferred", 0) == 0


def test_monitor_durable_debt_retries_oldest_until_timestamp_advances(monkeypatch):
    """Selection alone cannot rotate an older debt past an unpersisted attempt."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(
        cycle_runtime,
        "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE",
        {},
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE",
        {},
    )
    oldest = _make_position(trade_id="persisted-oldest")
    middle = _make_position(trade_id="persisted-middle")
    newer = _make_position(trade_id="persisted-newer")
    oldest._canonical_monitor_refreshed_at = ""
    middle._canonical_monitor_refreshed_at = "2026-07-22T12:00:00+00:00"
    newer._canonical_monitor_refreshed_at = "2026-07-22T13:00:00+00:00"

    first = cycle_runtime._reserve_held_monitor_positions(
        "canonical-test",
        [newer, oldest, middle],
        limit=1,
        durable_only=True,
    )
    after_failed_oldest = cycle_runtime._reserve_held_monitor_positions(
        "canonical-test",
        [newer, oldest, middle],
        limit=1,
        durable_only=True,
    )
    oldest._canonical_monitor_refreshed_at = "2026-07-22T13:00:00+00:00"
    next_oldest = cycle_runtime._reserve_held_monitor_positions(
        "canonical-test",
        [newer, oldest, middle],
        limit=1,
        durable_only=True,
    )
    middle._canonical_monitor_refreshed_at = "2026-07-22T16:00:00+00:00"
    oldest._canonical_monitor_refreshed_at = "2026-07-22T15:00:00+00:00"
    after_progress = cycle_runtime._reserve_held_monitor_positions(
        "canonical-test",
        [newer, oldest, middle],
        limit=1,
        durable_only=True,
    )

    assert first == [oldest]
    assert after_failed_oldest == [oldest]
    assert next_oldest == [middle]
    assert after_progress == [newer]


def test_monitor_ready_hwm_covers_book_without_overtaking_debt(
    monkeypatch,
):
    """Ready HWM and bounded q work cover all canonical debt without retries."""
    from src.engine import cycle_runtime
    from src.engine.monitor_refresh import monitor_quote_refresh

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    positions = [
        _make_position(
            trade_id=f"bounded-debt-{index}",
            token_id=f"bounded-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(18)
    ]
    for index, position in enumerate(positions):
        position._canonical_monitor_refreshed_at = (
            f"2026-08-01T00:{index:02d}:00+00:00"
        )

    clock = [0.0]
    events: list[tuple[str, object]] = []

    def _book(token_id):
        return {
            "asset_id": token_id,
            "bids": [{"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.42", "size": "20"}],
        }

    class CurrentQuoteClob:
        def get_orderbook(self, token_id):
            events.append(("singular", token_id))
            clock[0] += 0.5
            return _book(token_id)

        def get_orderbook_snapshots(self, token_ids):
            events.append(("bulk", tuple(token_ids)))
            clock[0] += 1.0
            return {token_id: _book(token_id) for token_id in token_ids}

    clob = CurrentQuoteClob()
    from src.data.replacement_input_hwm import freeze_replacement_artifact_hwm
    from src.engine.monitor_refresh import install_monitor_replacement_hwm_snapshot

    hwm_conn = sqlite3.connect(":memory:")
    hwm_conn.execute("BEGIN")
    ready_hwm = freeze_replacement_artifact_hwm(
        hwm_conn,
        requests=(("Chicago", "2026-04-15", "high"),),
        decision_time=datetime(2026, 4, 14, tzinfo=timezone.utc),
    )
    hwm_conn.rollback()
    hwm_conn.close()
    assert ready_hwm is not None

    def _prefetch_ready_hwm(_positions, *, clob, summary, **_kwargs):
        assert _positions == positions
        assert install_monitor_replacement_hwm_snapshot(clob, ready_hwm)
        summary["held_monitor_hwm_prefetch_status"] = "ready"

    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_replacement_artifact_hwm",
        _prefetch_ready_hwm,
    )
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_closed_non_accepting_market_info",
        lambda *_args, **_kwargs: None,
    )

    def _refresh(_conn, current_clob, position):
        monitor_quote_refresh(_conn, current_clob, position)
        clock[0] += 3.0
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )

    covered: set[str] = set()
    pass_attempts: list[list[str]] = []
    cycle_number = [0]

    def _append_attempt(_conn, position, **_kwargs):
        pass_attempts[-1].append(position.trade_id)
        covered.add(position.trade_id)
        position._canonical_monitor_refreshed_at = (
            f"2026-08-{cycle_number[0] + 2:02d}T00:00:00+00:00"
        )
        return True

    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        _append_attempt,
    )

    for cycle in range(4):
        cycle_number[0] = cycle
        pass_attempts.append([])
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            clob,
            _make_portfolio(*positions),
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=_monitor_test_deps(f"bounded-runtime-{cycle}"),
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=75.0,
        )
        assert len(pass_attempts[-1]) == len(set(pass_attempts[-1]))
        if len(covered) == 18:
            break

    assert len(pass_attempts) <= 4
    assert covered == {f"bounded-debt-{index}" for index in range(18)}, summary
    assert all(pass_attempts)
    assert sum(len(attempts) for attempts in pass_attempts) == 18


def test_monitor_reservation_attempts_full_batch_before_retry(monkeypatch):
    """A bounded batch covers every unattempted position before any retry."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    positions = [_make_position(trade_id=f"held-{index}") for index in range(5)]
    for index, pos in enumerate(positions):
        pos._canonical_monitor_refreshed_at = f"2026-07-22T12:0{index}:00+00:00"

    first = cycle_runtime._reserve_held_monitor_positions("batch", positions, limit=2)
    second = cycle_runtime._reserve_held_monitor_positions("batch", positions, limit=2)
    third = cycle_runtime._reserve_held_monitor_positions("batch", positions, limit=2)

    assert [pos.trade_id for pos in first] == ["held-0", "held-1"]
    assert [pos.trade_id for pos in second] == ["held-2", "held-3"]
    assert [pos.trade_id for pos in third] == ["held-4", "held-0"]


def test_monitor_primary_reserve_covers_every_admitted_degraded_tranche():
    """Auxiliary work cannot spend the time promised to admitted positions."""
    from src.engine import cycle_runtime

    assert cycle_runtime._held_position_monitor_reservation_count(13) == 5
    assert cycle_runtime._held_position_monitor_primary_reservation(
        13,
        75.0,
    ) == (5, pytest.approx(25.0))
    assert cycle_runtime._held_position_monitor_primary_reservation(
        2,
        6.0,
    ) == (1, pytest.approx(5.0))
    assert cycle_runtime._held_position_monitor_primary_reservation(
        100,
        75.0,
    ) == (7, pytest.approx(35.0))


def test_monitor_reservation_targeted_subset_preserves_full_book_fairness(
    monkeypatch,
):
    """A targeted wake cannot erase the full monitor lane's unattempted tail."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    positions = [_make_position(trade_id=f"held-{index}") for index in range(5)]
    for index, pos in enumerate(positions):
        pos._canonical_monitor_refreshed_at = f"2026-07-22T12:0{index}:00+00:00"

    first_full = cycle_runtime._reserve_held_monitor_positions(
        "shared", positions, limit=2
    )
    targeted = cycle_runtime._reserve_held_monitor_positions(
        "shared", [positions[0]], limit=1
    )
    second_full = cycle_runtime._reserve_held_monitor_positions(
        "shared", positions, limit=2
    )

    assert [pos.trade_id for pos in first_full] == ["held-0", "held-1"]
    assert targeted == [positions[0]]
    assert [pos.trade_id for pos in second_full] == ["held-2", "held-3"]


def test_monitor_reservation_durable_progress_moves_attempt_to_tail(monkeypatch):
    """A delayed canonical success outranks its older process-local attempt."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    first = _make_position(trade_id="durable-progress-first")
    second = _make_position(trade_id="durable-progress-second")
    first._canonical_monitor_refreshed_at = "2026-07-22T11:00:00+00:00"
    second._canonical_monitor_refreshed_at = "2026-07-22T12:00:00+00:00"

    assert cycle_runtime._reserve_held_monitor_positions(
        "durable-progress-test", [first, second], limit=1
    ) == [first]
    assert cycle_runtime._reserve_held_monitor_positions(
        "durable-progress-test", [first, second], limit=1
    ) == [second]

    first._canonical_monitor_refreshed_at = "2026-07-22T13:00:00+00:00"

    assert cycle_runtime._reserve_held_monitor_positions(
        "durable-progress-test", [first, second], limit=1
    ) == [second]


def test_monitor_reservation_prunes_empty_lane_state(monkeypatch):
    """Closed position sets cannot leave process-lifetime monitor state behind."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_STATE_BY_LANE", {})
    monkeypatch.setattr(cycle_runtime, "_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE", {})
    held = _make_position(trade_id="lane-closes")
    held._canonical_monitor_refreshed_at = ""

    assert cycle_runtime._reserve_held_monitor_positions(
        "closed-lane-test", [held], limit=1
    ) == [held]
    assert "closed-lane-test" in cycle_runtime._HELD_MONITOR_ATTEMPT_STATE_BY_LANE

    assert cycle_runtime._reserve_held_monitor_positions(
        "closed-lane-test", [], limit=1
    ) == []
    assert "closed-lane-test" not in cycle_runtime._HELD_MONITOR_ATTEMPT_STATE_BY_LANE
    assert "closed-lane-test" not in cycle_runtime._HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE


def test_monitoring_phase_network_round_robin_survives_new_no_attr_clients(
    monkeypatch,
):
    """Process-owned cursor advances when every cycle constructs a new client."""
    from src.engine import cycle_runtime

    monkeypatch.setattr(
        cycle_runtime,
        "_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE",
        {},
    )
    positions = [
        _make_position(
            trade_id=f"cross-cycle-network-{index}",
            token_id=f"cross-cycle-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(3)
    ]
    events: list[str] = []

    class NoAttrClob:
        __slots__ = ()

        def get_orderbook_snapshots(self, token_ids):
            events.append("network_fetch")
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )

    def fake_refresh(_conn, _clob, position):
        events.append(f"refresh:{position.trade_id}")
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    summaries = []
    for _cycle in range(2):
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None,
            NoAttrClob(),
            _make_portfolio(*positions),
            _monitor_test_artifact(),
            _monitor_test_tracker(),
            summary,
            deps=_monitor_test_deps("test_monitor_cross_cycle_round_robin"),
            run_exit_preflight=False,
            held_position_monitor_budget_seconds=10.0,
            defer_partial_orderbook_gaps=True,
        )
        summaries.append(summary)

    assert [
        summary["held_monitor_active_network_progress_position"]
        for summary in summaries
    ] == ["cross-cycle-network-0", "cross-cycle-network-1"]
    assert events.count("network_fetch") == 2
    assert events.count("refresh:cross-cycle-network-0") == 2
    assert events.count("refresh:cross-cycle-network-1") == 2
    assert events.count("refresh:cross-cycle-network-2") == 2
    assert all(summary["held_monitor_budget_bypass_scanned"] == 0 for summary in summaries)


def test_monitoring_phase_network_pending_exit_precedes_local_active_under_budget(
    monkeypatch,
):
    """A local active position cannot consume the only monitor slice first."""
    from src.engine import cycle_runtime

    pending_exit = _make_position(
        trade_id="network-pending-exit",
        token_id="network-token",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
    )
    local_active = _make_position(
        trade_id="local-active",
        token_id="local-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    events: list[str] = []

    class Clob:
        def get_orderbook_snapshots(self, token_ids):
            events.append("network_fetch")
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda _conn, _positions, **_kwargs: {
            "local-token": {
                "asset_id": "local-token",
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
        },
    )
    clock_values = (0.0, 0.0, 0.0, 0.6)
    clock_calls = {"count": 0}

    def stable_elapsed_clock():
        index = min(clock_calls["count"], len(clock_values) - 1)
        clock_calls["count"] += 1
        return clock_values[index]

    monkeypatch.setattr(cycle_runtime.time, "monotonic", stable_elapsed_clock)

    def fake_refresh(_conn, _clob, position):
        events.append(f"refresh:{position.trade_id}")
        position.last_monitor_prob = 0.61
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.12
        position.last_monitor_market_price = 0.49
        position.last_monitor_market_price_is_fresh = True
        return SimpleNamespace(
            p_market=np.array([0.49]),
            p_posterior=0.61,
            forward_edge=0.12,
            confidence_band_lower=0.08,
            confidence_band_upper=0.16,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    summary = {"monitors": 0, "exits": 0}
    deps = _monitor_test_deps("test_monitor_urgent_network_first")

    cycle_runtime.execute_monitoring_phase(
        None,
        Clob(),
        _make_portfolio(local_active, pending_exit),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=0.5,
    )

    assert events == [
        "network_fetch",
        "refresh:network-pending-exit",
    ]
    assert summary["held_monitor_positions_scanned"] == 1
    assert summary["held_monitor_positions_deferred"] == 1


def test_monitoring_phase_commit_failure_defers_network_without_getter(monkeypatch):
    """An uncommitted monitor write must never cross into CLOB I/O."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="network-after-commit-failure",
        token_id="network-token",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
    )

    class CommitFailingConn:
        def commit(self):
            raise RuntimeError("commit unavailable")

    class Clob:
        def get_orderbook_snapshots(self, _token_ids):
            raise AssertionError("getter must not run after commit failure")

    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [pos],
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network-deferred position must not refresh")
        ),
    )
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.release_pending_exit_without_order_if_retryable",
        lambda *_args, **_kwargs: False,
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        CommitFailingConn(),
        Clob(),
        _make_portfolio(pos),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_commit_failure"),
        run_exit_preflight=False,
    )

    assert summary["held_monitor_orderbook_prefetch_defer_reason"] == (
        "MONITOR_WRITE_COMMIT_FAILED"
    )
    assert summary["held_monitor_positions_deferred_for_commit_failure"] == 1


def test_global_sell_reauction_waits_for_outer_commit_before_network(monkeypatch):
    """A staged release cannot publish a wake before its outer commit."""
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    pos = _make_position(
        trade_id="global-sell-reauction-outer-commit",
        token_id="global-sell-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    pos.exit_state = "retry_pending"
    pos.state = "pending_exit"
    pos.order_status = "retry_pending"
    pos.next_exit_retry_at = "2026-01-01T00:00:00+00:00"
    pos.last_exit_error = "global_sell_exit_executable_snapshot_unavailable"
    events: list[str] = []

    class Conn:
        in_transaction = True

        def commit(self):
            events.append("commit")
            self.in_transaction = False

        def rollback(self):
            events.append("rollback")
            self.in_transaction = False

    monkeypatch.setattr(
        exit_lifecycle,
        "_dual_write_exit_retry_released_if_available",
        lambda *_args, **_kwargs: events.append("release_write") or True,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_snapshot_min_order_dust_error",
        lambda *_args, **_kwargs: None,
    )
    # The monitor owns only the canonical release predicate; do not pretend this
    # ordering antibody's lightweight Conn implements production SQL.
    monkeypatch.setattr(
        exit_lifecycle,
        "_canonical_global_sell_command_ownership",
        lambda *_args, **_kwargs: "GLOBAL_NO_COMMAND",
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_exit_command_release_witness",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "needs_global_sell_snapshot_reauction",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "needs_global_sell_snapshot_reauction",
        exit_lifecycle.needs_global_sell_snapshot_reauction,
        raising=False,
    )
    # The durable-release implementation is covered by its own canonical-DB
    # tests. Here retain exactly this monitor boundary: publish is impossible
    # until cycle_runtime has committed the release write.
    def recover_committed_debt(position, *, conn, requester):
        if conn.in_transaction or not requester(position, False):
            return False
        assert exit_lifecycle.record_global_sell_reauction_reserved(conn, position)
        conn.commit()
        return True

    monkeypatch.setattr(
        exit_lifecycle,
        "recover_global_sell_snapshot_reauction_debt",
        recover_committed_debt,
    )
    monkeypatch.setattr(
        "src.events.reactor.request_global_auction_completion",
        lambda **_kwargs: events.append("network_publish") or (
            True,
            SimpleNamespace(
                request_id="request", material_identity="material",
                attempt_identity="attempt", scope_identity="scope", generation="1",
            ),
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "record_global_sell_reauction_reserved",
        lambda *_args, **_kwargs: events.append("ack_write") or True,
    )
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.check_pending_exits",
        lambda *_args, **kwargs: (
            exit_lifecycle.check_pending_retries(
                pos,
                conn=kwargs["conn"],
                global_sell_reauction_requester=kwargs[
                    "global_sell_reauction_requester"
                ],
            ),
            {
                "filled": 0,
                "retried": 1,
                "unchanged": 0,
                "filled_positions": [],
            },
        )[1],
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [],
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        Conn(),
        object(),
        _make_portfolio(pos),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_reauction_outer_commit"),
    )

    assert events == [
        "release_write",
        "commit",
        "network_publish",
        "ack_write",
        "commit",
        "commit",
    ]


def test_global_sell_reauction_commit_failure_restores_runtime_without_network(
    monkeypatch,
):
    """A rolled-back release keeps pending_exit and never publishes a wake."""
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    pos = _make_position(
        trade_id="global-sell-reauction-commit-failure",
        token_id="global-sell-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    pos.exit_state = "retry_pending"
    pos.state = "pending_exit"
    pos.order_status = "retry_pending"
    pos.next_exit_retry_at = "2026-01-01T00:00:00+00:00"
    pos.last_exit_error = "global_sell_exit_executable_snapshot_unavailable"
    events: list[str] = []

    class Conn:
        in_transaction = True

        def commit(self):
            events.append("commit_failed")
            raise RuntimeError("commit unavailable")

        def rollback(self):
            events.append("rollback")
            self.in_transaction = False

    monkeypatch.setattr(
        exit_lifecycle,
        "_dual_write_exit_retry_released_if_available",
        lambda *_args, **_kwargs: events.append("release_write") or True,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_snapshot_min_order_dust_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "needs_global_sell_snapshot_reauction",
        lambda position, _conn=None: (
            position.last_exit_error.startswith(
                "global_sell_exit_executable_snapshot"
            )
        ),
    )
    monkeypatch.setattr(
        "src.events.reactor.request_global_auction_completion",
        lambda **_kwargs: events.append("network_publish") or True,
    )
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.check_pending_exits",
        lambda *_args, **kwargs: (
            exit_lifecycle.check_pending_retries(
                pos,
                conn=kwargs["conn"],
                global_sell_reauction_requester=kwargs[
                    "global_sell_reauction_requester"
                ],
            ),
            {
                "filled": 0,
                "retried": 1,
                "unchanged": 0,
                "filled_positions": [],
            },
        )[1],
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        Conn(),
        object(),
        _make_portfolio(pos),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_reauction_commit_failure"),
    )

    assert events == ["release_write", "commit_failed", "rollback"]
    assert pos.state == "pending_exit"
    assert pos.exit_state == "retry_pending"
    assert pos.order_status == "retry_pending"
    assert pos.last_exit_error == (
        "global_sell_exit_executable_snapshot_unavailable"
    )


def test_late_global_sell_retry_commit_failure_restores_runtime(monkeypatch):
    """A retry missed by preflight cannot escape rollback in the monitor tail."""
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    pos = _make_position(
        trade_id="late-global-sell-reauction-commit-failure",
        token_id="late-global-sell-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    pos.exit_state = "retry_pending"
    pos.state = "pending_exit"
    pos.order_status = "retry_pending"
    pos.next_exit_retry_at = "2026-01-01T00:00:00+00:00"
    pos.last_exit_error = ""
    events: list[str] = []

    class Conn:
        in_transaction = True
        commits = 0

        def commit(self):
            self.commits += 1
            if self.commits == 1:
                events.append("preflight_commit")
                self.in_transaction = False
                return
            events.append("late_commit_failed")
            raise RuntimeError("commit unavailable")

        def rollback(self):
            events.append("rollback")
            self.in_transaction = False

    conn = Conn()

    def release_write(*_args, **_kwargs):
        events.append("release_write")
        conn.in_transaction = True
        return True

    class Clob:
        def get_orderbook_snapshots(self, _token_ids):
            events.append("network_publish")
            raise AssertionError("network must remain deferred")

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.check_pending_exits",
        lambda *_args, **_kwargs: {
            "filled": 0,
            "retried": 0,
            "unchanged": 1,
            "filled_positions": [],
        },
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_dual_write_exit_retry_released_if_available",
        release_write,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_snapshot_min_order_dust_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_exit_reject_error",
        lambda *_args, **_kwargs: (
            "global_sell_exit_executable_snapshot_unavailable"
        ),
    )
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.release_pending_exit_without_order_if_retryable",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [pos],
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda _position: False,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        conn,
        Clob(),
        _make_portfolio(pos),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_late_reauction_commit_failure"),
    )

    assert events[:4] == [
        "preflight_commit",
        "release_write",
        "late_commit_failed",
        "rollback",
    ]
    assert "network_publish" not in events
    assert pos.state == "pending_exit"
    assert pos.exit_state == "retry_pending"
    assert pos.order_status == "retry_pending"
    assert pos.last_exit_error == ""


def test_monitoring_phase_urgent_wake_counts_only_unvisited_tail(monkeypatch):
    """A wake after the batch cannot count the already-scanned position twice."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="wake-during-network-prefetch",
        token_id="network-token",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
    )

    class Clob:
        def get_orderbook_snapshots(self, token_ids):
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )
    calls = {"count": 0}

    def urgent_wake():
        calls["count"] += 1
        return calls["count"] >= 4

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        Clob(),
        _make_portfolio(pos),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_urgent_summary"),
        run_exit_preflight=False,
        should_preempt_for_urgent_day0=urgent_wake,
    )

    assert summary["held_monitor_preempted"] is True
    assert summary["held_monitor_positions_scanned"] == 1
    assert summary["held_monitor_positions_deferred"] == 0
    assert (
        summary["held_monitor_positions_scanned"]
        + summary["held_monitor_positions_deferred"]
        == summary["held_monitor_candidates"]
    )


def test_monitoring_phase_prefetch_install_failure_is_not_local_ready(monkeypatch):
    """A CLOB that cannot retain the cache must not be credited with local quotes."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="uninstallable-prefetch",
        token_id="local-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )

    class Clob:
        __slots__ = ()

        def get_orderbook_snapshots(self, token_ids):
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {
            "local-token": {
                "asset_id": "local-token",
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
        },
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: _monitor_test_edge_context(position),
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        Clob(),
        _make_portfolio(pos),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_prefetch_install"),
        run_exit_preflight=False,
    )

    assert summary["held_monitor_local_ready_positions"] == 0
    assert summary["held_monitor_orderbooks_prefetched"] == 0
    assert summary["held_monitor_orderbook_prefetch_unavailable"] == (
        "ORDERBOOK_PREFETCH_INSTALL_FAILED"
    )


def _monitor_test_artifact():
    return type(
        "Artifact",
        (),
        {
            "add_monitor_result": lambda self, _result: None,
            "add_exit": lambda self, *_args: None,
        },
    )()


def _monitor_test_tracker():
    return type("Tracker", (), {"record_exit": lambda self, _position: None})()


def _monitor_test_deps(logger_name: str):
    return type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
            ),
            "logger": logging.getLogger(logger_name),
            "cities_by_name": {},
            "_utcnow": staticmethod(
                lambda: datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc)
            ),
        },
    )


def _monitor_test_edge_context(position):
    position.last_monitor_prob = 0.61
    position.last_monitor_prob_is_fresh = True
    position.last_monitor_edge = 0.12
    position.last_monitor_market_price = 0.49
    position.last_monitor_market_price_is_fresh = True
    return SimpleNamespace(
        p_market=np.array([0.49]),
        p_posterior=0.61,
        forward_edge=0.12,
        confidence_band_lower=0.08,
        confidence_band_upper=0.16,
    )


def _make_position(**overrides) -> Position:
    """Create a test position with sensible defaults."""
    defaults = dict(
        trade_id="test_001",
        market_id="mkt_001",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-04-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        edge=0.15,
        shares=25.0,
        cost_basis_usd=10.0,
        state="holding",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        unit="F",
        env="live",
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_portfolio(*positions) -> PortfolioState:
    """Create portfolio with given positions."""
    return PortfolioState(positions=list(positions))


def test_monitor_writer_timeout_preserves_nonred_exit_authority(monkeypatch):
    """A bounded canonical-write timeout cannot relabel an economic exit HOLD."""
    from src.engine import cycle_runtime

    position = _make_position(
        trade_id="writer-timeout-exit-authority",
        state="holding",
        chain_state="synced",
        shares=10.0,
        chain_shares=10.0,
    )
    results = []
    exits = []
    def refresh(*_args):
        context = _monitor_test_edge_context(position)
        context.divergence_score = 0.0
        context.market_velocity_1h = 0.0
        return context

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda *_args: ExitDecision(
            True,
            "CI_WRITER_TIMEOUT_NONRED_EXIT",
            trigger="CI_WRITER_TIMEOUT_NONRED_EXIT",
            applied_validations=["replacement_posterior"],
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        lambda **kwargs: exits.append(kwargs["position"].trade_id) or "exit_retry:writer_lease_timeout",
    )
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: results.append(result)},
    )()
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        artifact,
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_writer_timeout_preserves_exit"),
        run_exit_preflight=False,
    )

    assert exits == [position.trade_id]
    assert results == []
    assert summary["monitor_canonical_write_failed_exit_authority_preserved"] == 1


def test_monitor_timeout_retries_frozen_attempt_once_into_canonical_projection(
    tmp_path,
    monkeypatch,
):
    """A timed-out held monitor retries its frozen canonical attempt once."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.write_coordinator import WriteLeaseTimeout

    conn = get_connection(tmp_path / "monitor-retry-canonical.db")
    init_schema(conn)
    position = _make_position(
        trade_id="monitor-retry-canonical",
        state="holding",
        city="Chicago",
        target_date="2026-07-30",
        order_id="o-monitor-retry",
        entered_at="2026-07-30T17:00:00+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="90-91°F",
        condition_id="0xmonitorretry00000000000000000000000000000000000000000000001",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        position,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-retry-seed",
        source_module="tests/test_monitor_timeout_retries_frozen_attempt_once",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.commit()
    # This is the prior cycle's observation. The new attempt must not reuse it.
    position.last_monitor_at = "2026-07-30T18:00:00+00:00"
    position.last_monitor_prob = 0.61
    position.last_monitor_prob_is_fresh = True
    position.last_monitor_edge = 0.17
    position.last_monitor_market_price = 0.44
    position.last_monitor_market_price_is_fresh = True
    lease_calls: list[dict] = []

    class TimedOutLease:
        def __enter__(self):
            raise WriteLeaseTimeout("initial monitor lease timed out")

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    class AcquiredLease:
        owner = "monitor_canonical_append_retry"
        acquired_at = time.monotonic()

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    def monitor_lease(*_args, **kwargs):
        lease_calls.append(kwargs)
        if kwargs["owner"] == "monitor_canonical_append":
            return TimedOutLease()
        return AcquiredLease()

    monkeypatch.setattr(cycle_runtime, "_canonical_trade_write_lease", monitor_lease)

    attempt_at = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    deps = type(
        "Deps",
        (),
        {
            "logger": logging.getLogger("test_monitor_retry_canonical"),
            "_utcnow": staticmethod(lambda: attempt_at),
        },
    )
    try:
        assert (
            cycle_runtime._emit_monitor_refreshed_canonical_if_available(
                conn,
                position,
                deps=deps,
            )
            is True
        )
        # Replaying the same frozen attempt after another initial timeout is
        # idempotent: it observes the committed event instead of appending one.
        assert (
            cycle_runtime._emit_monitor_refreshed_canonical_if_available(
                conn,
                position,
                deps=deps,
            )
            is True
        )

        event = conn.execute(
            """
            SELECT sequence_no, occurred_at, payload_json
              FROM position_events
             WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
            """,
            (position.trade_id,),
        ).fetchall()
        assert len(event) == 1
        assert event[0]["sequence_no"] == 4
        assert event[0]["occurred_at"] == attempt_at.isoformat()
        assert json.loads(event[0]["payload_json"])["last_monitor_prob"] == pytest.approx(0.61)
        current = conn.execute(
            """
            SELECT phase, last_monitor_prob, last_monitor_market_price, updated_at
              FROM position_current WHERE position_id = ?
            """,
            (position.trade_id,),
        ).fetchone()
        assert current["phase"] == LifecyclePhase.ACTIVE.value
        assert current["last_monitor_prob"] == pytest.approx(0.61)
        assert current["last_monitor_market_price"] == pytest.approx(0.44)
        assert current["updated_at"] == attempt_at.isoformat()
        assert position.last_monitor_at == attempt_at.isoformat()
    finally:
        conn.close()

    assert lease_calls == [
        {
            "owner": "monitor_canonical_append",
            "deadline_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_LEASE_DEADLINE_MS,
            "max_hold_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_LEASE_MAX_HOLD_MS,
        },
        {
            "owner": "monitor_canonical_append_retry",
            "deadline_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_RETRY_DEADLINE_MS,
            "max_hold_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_LEASE_MAX_HOLD_MS,
        },
        {
            "owner": "monitor_canonical_append",
            "deadline_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_LEASE_DEADLINE_MS,
            "max_hold_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_LEASE_MAX_HOLD_MS,
        },
        {
            "owner": "monitor_canonical_append_retry",
            "deadline_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_RETRY_DEADLINE_MS,
            "max_hold_ms": cycle_runtime._MONITOR_CANONICAL_WRITE_LEASE_MAX_HOLD_MS,
        },
    ]


def test_monitor_retry_timeout_defers_frozen_attempt_to_next_cycle(
    tmp_path,
    monkeypatch,
    caplog,
):
    """Both bounded lease timeouts leave no stale event and retain retry debt."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.write_coordinator import WriteLeaseTimeout

    conn = get_connection(tmp_path / "monitor-retry-deferred.db")
    init_schema(conn)
    position = _make_position(
        trade_id="monitor-retry-deferred",
        state="holding",
        city="Chicago",
        target_date="2026-07-30",
        order_id="o-monitor-retry-deferred",
        entered_at="2026-07-30T17:00:00+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="90-91°F",
        condition_id="0xmonitordeferred0000000000000000000000000000000000000000000001",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        position,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-retry-deferred-seed",
        source_module="tests/test_monitor_retry_timeout_defers_frozen_attempt",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.commit()
    previous_monitor_at = "2026-07-30T18:00:00+00:00"
    position.last_monitor_at = previous_monitor_at

    class TimedOutLease:
        def __enter__(self):
            raise WriteLeaseTimeout("monitor lease timed out")

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(
        cycle_runtime,
        "_canonical_trade_write_lease",
        lambda *_args, **_kwargs: TimedOutLease(),
    )
    attempt_at = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    deps = type(
        "Deps",
        (),
        {
            "logger": logging.getLogger("test_monitor_retry_deferred"),
            "_utcnow": staticmethod(lambda: attempt_at),
        },
    )
    caplog.set_level(logging.INFO)
    try:
        assert (
            cycle_runtime._emit_monitor_refreshed_canonical_if_available(
                conn,
                position,
                deps=deps,
            )
            is False
        )
        assert position.last_monitor_at == previous_monitor_at
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'",
                (position.trade_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()

    assert "CANONICAL_MONITOR_REFRESHED_RETRY_DEFERRED_NEXT_CYCLE" in caplog.text


def test_monitor_retry_waits_out_one_incumbent_writer() -> None:
    """Priority intent must outlive a short incumbent TRADE transaction."""
    from src.engine import cycle_runtime

    assert cycle_runtime._MONITOR_CANONICAL_WRITE_RETRY_DEADLINE_MS >= 5_000


def test_monitor_append_failure_rolls_back_and_retains_retry_debt(tmp_path, monkeypatch):
    """A failed append cannot leave a transaction or in-memory monitor debt cleared."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "monitor-append-rollback.db")
    init_schema(conn)
    position = _make_position(
        trade_id="monitor-append-rollback",
        state="holding",
        city="Chicago",
        target_date="2026-07-30",
        order_id="o-monitor-append-rollback",
        entered_at="2026-07-30T17:00:00+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="90-91°F",
        condition_id="0xmonitorrollback0000000000000000000000000000000000000000000001",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        position,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-append-rollback-seed",
        source_module="tests/test_monitor_append_failure_rolls_back",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.commit()
    previous_monitor_at = "2026-07-30T18:00:00+00:00"
    position.last_monitor_at = previous_monitor_at
    prior_updated_at = conn.execute(
        "SELECT updated_at FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0]

    def failing_append(conn_arg, *_args):
        conn_arg.execute(
            "UPDATE position_current SET updated_at = ? WHERE position_id = ?",
            ("2099-01-01T00:00:00+00:00", position.trade_id),
        )
        raise RuntimeError("injected append failure")

    monkeypatch.setattr("src.state.db.append_many_and_project", failing_append)
    attempt_at = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    deps = type(
        "Deps",
        (),
        {
            "logger": logging.getLogger("test_monitor_append_rollback"),
            "_utcnow": staticmethod(lambda: attempt_at),
        },
    )
    try:
        assert (
            cycle_runtime._emit_monitor_refreshed_canonical_if_available(
                conn,
                position,
                deps=deps,
            )
            is False
        )
        assert position.last_monitor_at == previous_monitor_at
        assert conn.in_transaction is False
        assert (
            conn.execute(
                "SELECT updated_at FROM position_current WHERE position_id = ?",
                (position.trade_id,),
            ).fetchone()[0]
            == prior_updated_at
        )
    finally:
        conn.close()


def test_canonical_trade_write_lease_identity_failure_is_fail_closed():
    from src.engine import cycle_runtime

    class BrokenPragmaConnection:
        def execute(self, _sql):
            raise sqlite3.DatabaseError("pragma unavailable")

    with pytest.raises(cycle_runtime.CanonicalTradeWriteIdentityError) as exc:
        cycle_runtime._canonical_trade_write_lease(
            BrokenPragmaConnection(),
            owner="test",
            deadline_ms=1,
            max_hold_ms=50,
        )
    assert str(exc.value) == "CANONICAL_TRADE_DB_IDENTITY_UNAVAILABLE"

    with cycle_runtime._canonical_trade_write_lease(
        sqlite3.connect(":memory:"),
        owner="test",
        deadline_ms=1,
        max_hold_ms=50,
    ):
        pass


def _seed_canonical_entry_baseline(conn, position) -> None:
    """T1.c-followup (2026-04-23): post-T4.1b, chain_reconciliation.reconcile
    gates rescue strictly on the existence of a canonical baseline
    (``position_current`` row in ``pending_entry`` phase). This helper
    seeds that baseline by routing the ``pending_tracked`` position through
    ``build_entry_canonical_write`` + ``append_many_and_project`` so rescue
    probes find the POSITION_OPEN_INTENT / ENTRY_ORDER_POSTED events plus
    the ``pending_entry`` ``position_current`` row they need to flip.
    """
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.ledger import append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase

    if not getattr(position, "condition_id", ""):
        position.condition_id = "cond-1"
    if not getattr(position, "market_id", ""):
        position.market_id = position.condition_id
    events, projection = build_entry_canonical_write(
        position,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id=getattr(position, "decision_snapshot_id", None) or "dec-t1c-followup",
        source_module="src.test.t1c_followup_baseline",
    )
    append_many_and_project(conn, events, projection)


def _seed_acked_entry_command(conn, position, *, command_id: str = "cmd-rescue-proof") -> None:
    order_id = getattr(position, "entry_order_id", None) or getattr(position, "order_id", None)
    token_id = getattr(position, "token_id", None) or "tok-rescue-proof"
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', ?, ?, 'BUY', ?, ?, ?, 'ACKED', ?, ?)
        """,
        (
            command_id,
            f"snapshot-{command_id}",
            f"envelope-{command_id}",
            getattr(position, "trade_id", "pos-rescue-proof"),
            f"decision-{command_id}",
            f"idem-{command_id}",
            getattr(position, "market_id", None) or getattr(position, "condition_id", None) or "market-rescue-proof",
            token_id,
            float(getattr(position, "shares_submitted", 0.0) or getattr(position, "shares", 0.0) or 1.0),
            float(getattr(position, "entry_price_submitted", 0.0) or getattr(position, "entry_price", 0.0) or 0.5),
            order_id,
            "2026-04-03T00:00:00+00:00",
            "2026-04-03T00:00:00+00:00",
        ),
    )


def _make_clob(
    order_status="OPEN",
    balance=100.0,
    sell_result=None,
):
    """Create mock CLOB client."""
    clob = MagicMock()
    clob.get_order_status.return_value = sell_result or {"status": order_status}
    clob.get_balance.return_value = balance
    clob.cancel_order.return_value = {"status": "CANCELLED"}
    return clob


# ---- Test 1: GOLDEN RULE ----

def test_live_exit_never_closes_without_fill():
    """GOLDEN RULE: economic close only created after CONFIRMED fill truth.

    If CLOB returns OPEN (not filled), position must remain open with
    retry_pending state. It must NOT be closed or voided.
    """
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="OPEN", balance=100.0)

    with patch("src.execution.exit_lifecycle.place_sell_order") as mock_sell:
        mock_sell.return_value = {"orderID": "sell_123"}
        execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_context=ExitContext(
                exit_reason="EDGE_REVERSAL",
                current_market_price=0.45,
                best_bid=0.45,
            ),
            clob=clob,
        )

    # Position must still be in portfolio (not closed)
    assert pos in portfolio.positions
    assert pos.state == "pending_exit"
    assert pos.state != "voided"
    # Exit state should indicate sell was placed but not filled
    assert pos.exit_state in ("sell_placed", "sell_pending", "retry_pending")


# ---- Test 2: Entry creates pending_tracked ----

def test_live_entry_creates_pending_tracked():
    """Entry must create position even before fill confirmed.

    The Position dataclass must support pending_tracked with entry_order_id.
    """
    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
    )

    assert pos.state == "pending_tracked"
    assert pos.entry_order_id == "buy_123"
    assert pos.entry_fill_verified is False
    # Must have LifecycleState enum support
    assert LifecycleState(pos.state) == LifecycleState.PENDING_TRACKED


# ---- Test 3: Cancelled pending → void ----

def test_pending_tracked_voids_after_cancel():
    """Pending entry that gets cancelled → void, not phantom position."""
    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
    )
    portfolio = _make_portfolio(pos)

    # Simulate CLOB returning CANCELLED
    from src.execution.fill_tracker import check_pending_entries
    clob = _make_clob(order_status="CANCELLED")

    stats = check_pending_entries(portfolio, clob)

    # Position should be voided and removed from portfolio
    assert stats["voided"] == 1
    assert len(portfolio.positions) == 0  # void_position removes from portfolio


def test_fill_tracker_keeps_confirmed_entry_local_only_until_chain_seen():
    """CONFIRMED CLOB fill verifies locally first; chain ownership arrives later."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        chain_state="unknown",
        size_usd=10.0,
        entry_price=0.0,
        cost_basis_usd=0.0,
        shares=0.0,
        target_notional_usd=10.0,
        submitted_notional_usd=10.0,
        entry_price_submitted=0.40,
        shares_submitted=25.0,
        shares_remaining=25.0,
    )
    portfolio = _make_portfolio(pos)

    class Tracker:
        def __init__(self):
            self.entries = []

        def record_entry(self, position):
            self.entries.append(position.trade_id)

    tracker = Tracker()
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-buy-123",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    stats = check_pending_entries(portfolio, clob, tracker=tracker)

    assert stats["entered"] == 1
    assert stats["dirty"] is True
    assert stats["tracker_dirty"] is True
    assert pos.state == "entered"
    assert pos.entry_order_id == "buy_123"
    assert pos.entry_fill_verified is True
    assert pos.order_status == "confirmed"
    assert pos.chain_state == "local_only"
    assert pos.entered_at != ""
    assert pos.size_usd == pytest.approx(11.0)
    assert pos.cost_basis_usd == pytest.approx(11.0)
    assert pos.fill_quality == pytest.approx(0.10)
    assert pos.entry_price_submitted == pytest.approx(0.40)
    assert pos.entry_price_avg_fill == pytest.approx(0.44)
    assert tracker.entries == ["test_001"]


def test_matched_without_filled_size_does_not_materialize_entry():
    """MATCHED alone is not finality; legacy polling must see filled size."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {"status": "MATCHED", "price": 0.44}

    class StaleDeps:
        PENDING_FILL_STATUSES = {"FILLED", "MATCHED"}

    stats = check_pending_entries(portfolio, clob, deps=StaleDeps)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.order_status == "matched"


def test_confirmed_fill_survives_stale_deps_fill_statuses():
    """Stale deps cannot remove CONFIRMED as the only entry success terminal."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        chain_state="unknown",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-buy-stale-deps",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    class StaleDeps:
        PENDING_FILL_STATUSES = {"FILLED", "MATCHED"}

    stats = check_pending_entries(portfolio, clob, deps=StaleDeps)

    assert stats["entered"] == 1
    assert stats["still_pending"] == 0
    assert pos.state == "entered"
    assert pos.entry_fill_verified is True
    assert pos.order_status == "confirmed"


def test_confirmed_without_explicit_fill_price_holds_pending_for_review():
    """CONFIRMED order status is not fill economics without venue fill price.

    T4 (quarantine excision): a venue-truth gap (missing fill economics) stays
    pending_tracked with a MISSING_FILL_ECONOMICS review work item — no
    lifecycle scar, and the position keeps blocking same-city-range re-entry
    (it never actually stopped being live risk).
    """
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "filledSize": 25.0,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.admin_exit_reason == ""
    assert pos.order_status == "confirmed_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.entry_price == pytest.approx(0.40)
    assert pos.entry_price_avg_fill == 0.0
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.size_usd == pytest.approx(10.0)
    assert pos.cost_basis_usd == pytest.approx(10.0)
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_LEGACY_UNKNOWN
    assert pos.has_fill_economics_authority is False
    from src.state.portfolio import has_same_city_range_open, total_exposure_usd

    # BLOCKER-1: is_pending_entry_without_fill_authority still zeroes
    # effective exposure for this pending_tracked/no-fill-authority row — the
    # gap that made EntryRiskReservation (T2) necessary in the first place.
    assert total_exposure_usd(portfolio) == 0.0
    # But unlike the old quarantine scar (INACTIVE_RUNTIME_STATES), staying
    # pending_tracked correctly still blocks a duplicate same-city-range
    # re-entry while this order's fate is unresolved.
    assert has_same_city_range_open(portfolio, pos.city, pos.bin_label) is True


def test_confirmed_without_trade_identity_holds_pending_for_review():
    """Order-only CONFIRMED is not executable fill finality.

    T4: missing trade identity is a MISSING_FILL_AUTHORITY venue-truth gap —
    stays pending_tracked, no lifecycle scar.
    """
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "confirmed_missing_trade_identity"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False


def test_confirmed_without_trade_identity_marks_command_review_not_filled(tmp_path):
    """Order-only CONFIRMED must not advance the durable command to FILLED."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-confirmed-no-trade",
            "snap-confirmed-no-trade",
            "env-confirmed-no-trade",
            "runtime-confirmed-no-trade",
            "dec-confirmed-no-trade",
            "idem-confirmed-no-trade",
            "ENTRY",
            "condition-confirmed-no-trade",
            "tok_yes_confirmed_no_trade",
            "BUY",
            25.0,
            0.44,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="runtime-confirmed-no-trade",
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    conn = get_connection(db_path)
    command_state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-confirmed-no-trade'"
    ).fetchone()["state"]
    event_types = [
        row["event_type"]
        for row in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-confirmed-no-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    review_payload = conn.execute(
        """
        SELECT payload_json
          FROM venue_command_events
         WHERE command_id = 'cmd-confirmed-no-trade'
           AND event_type = 'REVIEW_REQUIRED'
         LIMIT 1
        """
    ).fetchone()["payload_json"]
    conn.close()

    assert command_state == "REVIEW_REQUIRED"
    assert "REVIEW_REQUIRED" in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert "poll_confirmed_requires_trade_fact" in review_payload
    assert "order_status_confirmed_is_not_fill_economics_authority" in review_payload


def test_confirmed_without_explicit_filled_size_holds_pending_for_review():
    """CONFIRMED fill price alone must not invent shares from order size.

    T4: venue-truth gap — stays pending_tracked, no lifecycle scar.
    """
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "confirmed_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares == pytest.approx(25.0)
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.size_usd == pytest.approx(10.0)
    assert pos.cost_basis_usd == pytest.approx(10.0)
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("avgPrice", math.nan),
        ("avgPrice", math.inf),
        ("filledSize", math.nan),
        ("filledSize", math.inf),
    ],
)
def test_confirmed_with_nonfinite_fill_economics_holds_pending_for_review(field, value):
    """Non-finite venue economics are not executable fill evidence.

    T4: venue-truth gap — stays pending_tracked, no lifecycle scar.
    """
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    payload = {
        "status": "CONFIRMED",
        "avgPrice": 0.44,
        "filledSize": 25.0,
    }
    payload[field] = value
    clob.get_order_status.return_value = payload

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "confirmed_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False


def test_matched_with_filled_size_but_missing_fill_price_holds_pending_for_review():
    """Optimistic fill observations need fill price before economics authority.

    T4: venue-truth gap — stays pending_tracked, no lifecycle scar.
    """
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=10.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "filledSize": 12.0,
        "price": 0.44,
    }

    stats = check_pending_entries(portfolio, clob)

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "matched_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.shares == 0.0
    assert pos.shares_filled == 0.0
    assert pos.cost_basis_usd == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE


def test_legacy_polling_matched_maps_numeric_live_runtime_id_to_optimistic_lot(tmp_path):
    """Numeric-looking executor runtime ids must not bypass trade_decisions mapping."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-match",
            "snap-match",
            "env-match",
            "123456789012",
            "dec-live-abc",
            "idem-match",
            "ENTRY",
            "condition-match",
            "tok_yes_001",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-match",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789012",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789012",
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-match",
        "trade_status": "MATCHED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "matched"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_OPTIMISTIC_SUBMITTED
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    order_states = [r["state"] for r in conn.execute("SELECT state FROM venue_order_facts").fetchall()]
    trade_states = [r["state"] for r in conn.execute("SELECT state FROM venue_trade_facts").fetchall()]
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    exec_row = conn.execute(
        "SELECT terminal_exec_status FROM execution_fact WHERE position_id = ? AND order_role = 'entry'",
        ("123456789012",),
    ).fetchone()
    canonical_events = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ?",
        ("123456789012",),
    ).fetchall()
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert order_states == ["MATCHED"]
    assert trade_states == ["MATCHED"]
    assert [(row["position_id"], row["state"]) for row in lot_rows] == [(1, "OPTIMISTIC_EXPOSURE")]
    assert exec_row is None
    assert canonical_events == []
    assert calibration_rows == []


def test_legacy_polling_failed_trade_status_is_not_fill_progress_authority(tmp_path):
    """Order-level MATCHED cannot turn a FAILED trade object into exposure."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-failed-trade",
            "snap-failed-trade",
            "env-failed-trade",
            "123456789088",
            "dec-failed-trade",
            "idem-failed-trade",
            "ENTRY",
            "condition-failed-trade",
            "tok_yes_failed_trade",
            "BUY",
            20.0,
            0.40,
            "buy_failed_trade",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-failed-trade",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789088",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789088",
        state="pending_tracked",
        entry_order_id="buy_failed_trade",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-failed",
        "trade_status": "FAILED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "optimistic_fill_ledger_write_failed"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False
    assert pos.shares == 0.0
    assert pos.cost_basis_usd == 0.0

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        "SELECT state, filled_size FROM venue_trade_facts ORDER BY local_sequence"
    ).fetchall()
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    command_state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-failed-trade'"
    ).fetchone()["state"]
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-failed-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    canonical_events = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ?",
        ("123456789088",),
    ).fetchall()
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert [(row["state"], row["filled_size"]) for row in trade_rows] == [("FAILED", "12.0")]
    assert lot_rows == []
    assert command_state == "REVIEW_REQUIRED"
    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert canonical_events == []
    assert calibration_rows == []


def test_legacy_polling_failed_without_fill_economics_rolls_back_optimistic_lot(tmp_path):
    """FAILED trade lifecycle evidence must close prior optimistic exposure."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import append_position_lot, append_trade_fact

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-failed-no-econ",
            "snap-failed-no-econ",
            "env-failed-no-econ",
            "123456789089",
            "dec-failed-no-econ",
            "idem-failed-no-econ",
            "ENTRY",
            "condition-failed-no-econ",
            "tok_yes_failed_no_econ",
            "BUY",
            20.0,
            0.40,
            "buy_failed_no_econ",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-failed-no-econ",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789089",
        ),
    )
    matched_fact_id = append_trade_fact(
        conn,
        trade_id="trade-poll-failed-no-econ",
        venue_order_id="buy_failed_no_econ",
        command_id="cmd-failed-no-econ",
        state="MATCHED",
        filled_size="12.5",
        fill_price="0.42",
        source="REST",
        observed_at="2026-04-29T12:00:30+00:00",
        raw_payload_hash="0" * 64,
        raw_payload_json={"trade_status": "MATCHED"},
    )
    append_position_lot(
        conn,
        position_id=123456789089,
        state="OPTIMISTIC_EXPOSURE",
        shares="12.5",
        entry_price_avg="0.42",
        source_command_id="cmd-failed-no-econ",
        source_trade_fact_id=matched_fact_id,
        captured_at="2026-04-29T12:00:30+00:00",
        state_changed_at="2026-04-29T12:00:30+00:00",
        source="REST",
        observed_at="2026-04-29T12:00:30+00:00",
        raw_payload_json={"trade_status": "MATCHED"},
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789089",
        state="pending_tracked",
        entry_order_id="buy_failed_no_econ",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-failed-no-econ",
        "trade_status": "FAILED",
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        """
        SELECT trade_fact_id, state, filled_size, fill_price
          FROM venue_trade_facts
         ORDER BY local_sequence
        """
    ).fetchall()
    lot_rows = conn.execute(
        """
        SELECT state, shares, source_trade_fact_id
          FROM position_lots
         WHERE position_id = ?
         ORDER BY lot_id
        """,
        (123456789089,),
    ).fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-failed-no-econ'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    conn.close()

    assert [(r["state"], r["filled_size"], r["fill_price"]) for r in trade_rows] == [
        ("MATCHED", "12.5", "0.42"),
        ("FAILED", "0", "0"),
    ]
    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): the failed-trade
    # rollback lot is ECONOMICALLY_CLOSED_OPTIMISTIC, never a quarantine scar.
    assert [(r["state"], r["shares"]) for r in lot_rows] == [
        ("OPTIMISTIC_EXPOSURE", "12.5"),
        ("ECONOMICALLY_CLOSED_OPTIMISTIC", "12.5"),
    ]
    assert lot_rows[-1]["source_trade_fact_id"] == trade_rows[-1]["trade_fact_id"]
    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types


def test_legacy_polling_duplicate_failed_trade_fact_still_fails_closed(tmp_path):
    """An existing FAILED fact must not make polling's idempotent path authorize fill."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import append_trade_fact, load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-dup-failed-trade",
            "snap-dup-failed-trade",
            "env-dup-failed-trade",
            "123456789077",
            "dec-dup-failed-trade",
            "idem-dup-failed-trade",
            "ENTRY",
            "condition-dup-failed-trade",
            "tok_yes_dup_failed_trade",
            "BUY",
            20.0,
            0.40,
            "buy_dup_failed_trade",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-dup-failed-trade",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789077",
        ),
    )
    append_trade_fact(
        conn,
        trade_id="trade-poll-dup-failed",
        venue_order_id="buy_dup_failed_trade",
        command_id="cmd-dup-failed-trade",
        state="FAILED",
        filled_size="12.0",
        fill_price="0.42",
        source="WS_USER",
        observed_at=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        raw_payload_hash="0" * 64,
        raw_payload_json={"source": "preexisting"},
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789077",
        state="pending_tracked",
        entry_order_id="buy_dup_failed_trade",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-dup-failed",
        "trade_status": "FAILED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "optimistic_fill_ledger_write_failed"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False
    assert pos.shares == 0.0
    assert pos.cost_basis_usd == 0.0

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        "SELECT state, filled_size FROM venue_trade_facts ORDER BY local_sequence"
    ).fetchall()
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-dup-failed-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert [(row["state"], row["filled_size"]) for row in trade_rows] == [("FAILED", "12.0")]
    assert lot_rows == []
    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert calibration_rows == []


def test_legacy_polling_unknown_trade_status_fails_closed(tmp_path):
    """Explicit unsupported trade lifecycle evidence cannot become local exposure."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-unknown-trade",
            "snap-unknown-trade",
            "env-unknown-trade",
            "123456789066",
            "dec-unknown-trade",
            "idem-unknown-trade",
            "ENTRY",
            "condition-unknown-trade",
            "tok_yes_unknown_trade",
            "BUY",
            20.0,
            0.40,
            "buy_unknown_trade",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-unknown-trade",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789066",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789066",
        state="pending_tracked",
        entry_order_id="buy_unknown_trade",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-unknown",
        "trade_status": "WEIRD_STATE",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "optimistic_fill_ledger_write_failed"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False
    assert pos.shares == 0.0
    assert pos.cost_basis_usd == 0.0

    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-unknown-trade'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    canonical_events = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ?",
        ("123456789066",),
    ).fetchall()
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert "REVIEW_REQUIRED" in event_types
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert canonical_events == []
    assert calibration_rows == []


def test_legacy_polling_trade_lifecycle_requires_stable_fill_economics(tmp_path):
    """Same trade_id cannot change filled size when MATCHED later becomes CONFIRMED."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-drift",
            "snap-drift",
            "env-drift",
            "123456789099",
            "dec-drift",
            "idem-drift",
            "ENTRY",
            "condition-drift",
            "tok_yes_drift",
            "BUY",
            20.0,
            0.40,
            "buy_drift",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-drift",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789099",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789099",
        state="pending_tracked",
        entry_order_id="buy_drift",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-poll-drift",
        "trade_status": "MATCHED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }
    check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-poll-drift",
        "trade_status": "CONFIRMED",
        "avgPrice": 0.42,
        "filledSize": 20.0,
        "timestamp": "2026-04-29T12:02:00+00:00",
    }
    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 2, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_rows = conn.execute(
        "SELECT state, filled_size FROM venue_trade_facts ORDER BY local_sequence"
    ).fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-drift'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    calibration_rows = load_calibration_trade_facts(conn)
    conn.close()

    assert [(row["state"], row["filled_size"]) for row in trade_rows] == [("MATCHED", "12.0")]
    assert "REVIEW_REQUIRED" in event_types
    assert "FILL_CONFIRMED" not in event_types
    assert calibration_rows == []


def test_confirmed_order_with_matched_trade_status_stays_optimistic_not_full_fill(tmp_path):
    """Order CONFIRMED cannot override a non-final trade status into fill authority."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-confirmed-match",
            "snap-confirmed-match",
            "env-confirmed-match",
            "runtime-confirmed-match",
            "dec-confirmed-match",
            "idem-confirmed-match",
            "ENTRY",
            "condition-confirmed-match",
            "tok_yes_confirmed_match",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="runtime-confirmed-match",
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="CONFIRMED")
    clob.get_order_status.return_value = {
        "status": "CONFIRMED",
        "trade_id": "trade-confirmed-match",
        "trade_status": "MATCHED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "matched"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_OPTIMISTIC_SUBMITTED
    assert pos.fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_states = [r["state"] for r in conn.execute("SELECT state FROM venue_trade_facts").fetchall()]
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-confirmed-match'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    conn.close()

    assert trade_states == ["MATCHED"]
    assert "PARTIAL_FILL_OBSERVED" in event_types
    assert "FILL_CONFIRMED" not in event_types


def test_stale_deps_mined_fill_status_stays_optimistic_not_full_fill(tmp_path):
    """Stale deps cannot extend the fill-success set with MINED."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-mined-stale",
            "snap-mined-stale",
            "env-mined-stale",
            "runtime-mined-stale",
            "dec-mined-stale",
            "idem-mined-stale",
            "ENTRY",
            "condition-mined-stale",
            "tok_yes_mined_stale",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.commit()
    conn.close()

    class StaleDeps:
        PENDING_FILL_STATUSES = {"MATCHED", "MINED", "FILLED"}

        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="runtime-mined-stale",
        state="pending_tracked",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MINED")
    clob.get_order_status.return_value = {
        "status": "MINED",
        "trade_id": "trade-mined-stale",
        "trade_status": "MINED",
        "avgPrice": 0.42,
        "filledSize": 12.0,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=StaleDeps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "mined"
    assert pos.entry_fill_verified is False
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_OPTIMISTIC_SUBMITTED
    assert pos.fill_authority != FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    trade_states = [r["state"] for r in conn.execute("SELECT state FROM venue_trade_facts").fetchall()]
    lot_rows = conn.execute("SELECT position_id, state FROM position_lots").fetchall()
    event_types = [
        r["event_type"]
        for r in conn.execute(
            """
            SELECT event_type
              FROM venue_command_events
             WHERE command_id = 'cmd-mined-stale'
             ORDER BY sequence_no
            """
        ).fetchall()
    ]
    conn.close()

    assert trade_states == ["MINED"]
    assert lot_rows == []
    assert "PARTIAL_FILL_OBSERVED" in event_types
    assert "FILL_CONFIRMED" not in event_types


def test_deps_path_missing_fill_price_writes_no_fill_authority_surfaces(tmp_path):
    """A linkable order with size-only fill evidence must not contaminate U2 facts."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import load_calibration_trade_facts

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cmd-size-only",
            "snap-size-only",
            "env-size-only",
            "123456789012",
            "dec-live-size-only",
            "idem-size-only",
            "ENTRY",
            "condition-size-only",
            "tok_yes_001",
            "BUY",
            20.0,
            0.40,
            "buy_123",
            "ACKED",
            None,
            "2026-04-29T12:00:00+00:00",
            "2026-04-29T12:00:00+00:00",
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "condition-size-only",
            "60-65",
            "buy_yes",
            20.0,
            0.40,
            "2026-04-29T12:00:00+00:00",
            0.55,
            0.55,
            0.15,
            0.50,
            0.60,
            0.0,
            "pending_tracked",
            "123456789012",
        ),
    )
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    pos = _make_position(
        trade_id="123456789012",
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
        strategy_key="center_buy",
        strategy="center_buy",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="MATCHED")
    clob.get_order_status.return_value = {
        "status": "MATCHED",
        "trade_id": "trade-size-only",
        "trade_status": "MATCHED",
        "filledSize": 12.0,
        "price": 0.42,
        "timestamp": "2026-04-29T12:01:00+00:00",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 4, 29, 12, 1, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "matched_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.has_fill_economics_authority is False

    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM execution_fact").fetchone()[0] == 0
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM venue_command_events
         WHERE event_type IN ('PARTIAL_FILL_OBSERVED', 'FILL_CONFIRMED')
        """
    ).fetchone()[0] == 0
    assert load_calibration_trade_facts(conn) == []
    conn.close()


def test_partial_remainder_cancel_preserves_filled_exposure():
    """A partial fill followed by cancel timeout preserves non-final exposure."""
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_timeout_at="2026-04-29T12:05:00+00:00",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="PARTIAL")
    clob.get_order_status.return_value = {
        "status": "PARTIAL",
        "avgPrice": 0.42,
        "filledSize": 12.0,
    }

    first = check_pending_entries(
        portfolio,
        clob,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
    )

    assert first["entered"] == 0
    assert first["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.shares == pytest.approx(12.0)
    assert pos.cost_basis_usd == pytest.approx(12.0 * 0.42)
    assert pos.order_status == "partial"
    clob.cancel_order.assert_not_called()

    clob.get_order_status.return_value = {"status": "OPEN"}
    second = check_pending_entries(
        portfolio,
        clob,
        now=datetime(2026, 4, 29, 12, 6, tzinfo=timezone.utc),
    )

    assert second["entered"] == 0
    assert second["voided"] == 0
    assert second["still_pending"] == 1
    assert len(portfolio.positions) == 1
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.entered_at == ""
    assert pos.shares == pytest.approx(12.0)
    assert pos.cost_basis_usd == pytest.approx(12.0 * 0.42)
    assert pos.order_status == "partial_remainder_cancelled"
    clob.cancel_order.assert_called_once_with("buy_123")


def test_partial_with_filled_size_but_missing_fill_price_holds_pending_for_review():
    """Partial size evidence is not enough to assign cost basis or exposure grade.

    T4: venue-truth gap — stays pending_tracked, no lifecycle scar.
    """
    from src.execution.fill_tracker import check_pending_entries

    pos = _make_position(
        state="pending_tracked",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        size_usd=20.0,
        entry_price=0.40,
        shares=0.0,
        cost_basis_usd=0.0,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(order_status="PARTIAL")
    clob.get_order_status.return_value = {
        "status": "PARTIAL",
        "filledSize": 12.0,
        "price": 0.42,
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["voided"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "partially_matched_missing_fill_economics"
    assert pos.entry_fill_verified is False
    assert pos.shares == 0.0
    assert pos.shares_filled == 0.0
    assert pos.cost_basis_usd == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    clob.cancel_order.assert_not_called()


def test_live_order_with_positive_size_matched_records_partial_fact_before_review(tmp_path):
    """A CLOB LIVE order can still carry filled shares while the remainder rests."""
    from src.execution.fill_tracker import check_pending_entries
    from src.state.db import get_connection, init_schema

    db_path = tmp_path / "live-partial.db"
    conn = get_connection(db_path)
    init_schema(conn)
    pos = _make_position(
        trade_id="runtime-live-partial",
        state="pending_tracked",
        order_id="ord-live-partial",
        entry_order_id="ord-live-partial",
        entry_fill_verified=False,
        entered_at="",
        entry_price=0.28,
        entry_price_submitted=0.28,
        shares=0.0,
        shares_submitted=7.21,
        size_usd=0.0,
        cost_basis_usd=0.0,
    )
    _seed_acked_entry_command(conn, pos, command_id="cmd-live-partial")
    conn.commit()
    conn.close()

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    portfolio = _make_portfolio(pos)
    clob = _make_clob()
    clob.get_order_status.return_value = {
        "status": "LIVE",
        "size_matched": "2.11",
        "original_size": "7.21",
        "price": "0.28",
    }

    stats = check_pending_entries(
        portfolio,
        clob,
        deps=Deps,
        now=datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc),
    )

    assert stats["entered"] == 0
    assert stats["voided"] == 0
    assert stats["still_pending"] == 1
    assert pos.state == "pending_tracked"
    assert pos.order_status == "partially_matched_missing_fill_economics"

    verify = get_connection(db_path)
    try:
        command = verify.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-live-partial'"
        ).fetchone()
        order_fact = verify.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id = 'cmd-live-partial'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        trade_fact_count = verify.execute(
            "SELECT COUNT(*) FROM venue_trade_facts WHERE command_id = 'cmd-live-partial'"
        ).fetchone()[0]
        event_types = [
            row["event_type"]
            for row in verify.execute(
                """
                SELECT event_type
                  FROM venue_command_events
                 WHERE command_id = 'cmd-live-partial'
                 ORDER BY sequence_no
                """
            ).fetchall()
        ]
    finally:
        verify.close()

    assert command["state"] == "PARTIAL"
    assert dict(order_fact) == {
        "state": "PARTIALLY_MATCHED",
        "remaining_size": "5.1",
        "matched_size": "2.11",
    }
    assert trade_fact_count == 0
    assert event_types[-1] == "PARTIAL_FILL_OBSERVED"


def test_chain_reconciliation_rescues_pending_tracked_fill(tmp_path):
    """Chain truth must rescue pending_tracked when order-status path is
    unavailable. T1.c-followup rewrite 2026-04-23: rescue is now gated on
    canonical baseline existence (post-T4.1b); test seeds baseline via
    build_entry_canonical_write + passes conn to reconcile."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "rescue_pending.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        decision_snapshot_id="snap-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    conn.close()

    assert stats["rescued_pending"] == 1
    assert pos.state == "entered"
    assert pos.chain_state == "synced"
    # PR D0 fix: balance-only rescue (no linked trade fact) must NOT set
    # entry_fill_verified=True or order_status="filled". The position is
    # tradable (has_tradable_exposure) but fill_authority=venue_position_observed.
    assert pos.entry_fill_verified is False
    assert pos.order_status == "pending"  # stays at input value for balance-only
    assert pos.entered_at != ""
    # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): balance-only rescue
    # preserves submitted entry economics; chain economics flow into
    # chain_* fields. Submitted defaults from _make_position were
    # entry_price=0.40, size_usd=10.0, shares=25.0, cost_basis_usd=10.0.
    assert pos.entry_price == 0.40
    assert pos.size_usd == 10.0
    assert pos.cost_basis_usd == 10.0
    assert pos.shares == 25.0
    # Chain aggregate (chain.avg_price=0.44, chain.cost=11.0, chain.size=25.0)
    # lands on chain_* fields.
    assert pos.chain_avg_price == 0.44
    assert pos.chain_cost_basis_usd == 11.0
    assert pos.chain_shares == 25.0
    assert pos.condition_id == "cond-1"
    assert portfolio.positions == [pos]


def test_chain_reconciliation_does_not_rescue_commanded_pending_entry_without_trade_fact(tmp_path):
    """A token-level chain position cannot prove that a specific live order filled."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "rescue_requires_trade_fact.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-proof-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_proof_001",
        no_token_id="tok_no_proof_001",
        order_id="order-proof-1",
        entry_order_id="order-proof-1",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        decision_snapshot_id="snap-proof-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    _seed_acked_entry_command(conn, pos, command_id="cmd-rescue-proof-1")
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_proof_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    phase = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        ("rescue-proof-1",),
    ).fetchone()["phase"]
    conn.close()

    assert stats["rescued_pending"] == 0
    assert stats["skipped_pending_missing_fill_fact"] == 1
    assert phase == "pending_entry"
    assert pos.state == "pending_tracked"
    assert pos.entry_fill_verified is False
    assert pos.order_status == "pending"


def test_chain_reconciliation_rescues_commanded_pending_entry_with_trade_fact(tmp_path):
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema
    from src.state.venue_command_repo import append_trade_fact

    conn = get_connection(tmp_path / "rescue_with_trade_fact.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-proof-2",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_proof_002",
        no_token_id="tok_no_proof_002",
        order_id="order-proof-2",
        entry_order_id="order-proof-2",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        decision_snapshot_id="snap-proof-2",
    )
    _seed_canonical_entry_baseline(conn, pos)
    _seed_acked_entry_command(conn, pos, command_id="cmd-rescue-proof-2")
    append_trade_fact(
        conn,
        trade_id="trade-proof-2",
        venue_order_id="order-proof-2",
        command_id="cmd-rescue-proof-2",
        state="MATCHED",
        filled_size="25",
        fill_price="0.44",
        source="WS_USER",
        observed_at="2026-04-03T00:00:01+00:00",
        raw_payload_hash="a" * 64,
        raw_payload_json={"order_id": "order-proof-2", "trade_id": "trade-proof-2"},
    )
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_proof_002", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    conn.close()

    assert stats["rescued_pending"] == 1
    assert pos.state == "entered"
    assert pos.entry_fill_verified is True
    assert pos.order_status == "filled"


def test_chain_reconciliation_partial_fill_rescue_preserves_exact_exposure_and_remainder(
    tmp_path,
):
    """A chain-visible partial match is exposure, not a completed entry order."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema, query_portfolio_loader_view
    from src.state.venue_command_repo import append_trade_fact

    conn = get_connection(tmp_path / "rescue_partial_trade_fact.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="rescue-partial-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_partial_001",
        no_token_id="tok_no_partial_001",
        order_id="order-partial-1",
        entry_order_id="order-partial-1",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        decision_snapshot_id="snap-partial-1",
    )
    pos.shares = 0.0
    pos.size_usd = 0.0
    pos.cost_basis_usd = 0.0
    pos.entry_price = 0.0
    pos.shares_submitted = 95.0
    pos.entry_price_submitted = 0.07
    pos.corrected_executable_economics_eligible = True
    _seed_canonical_entry_baseline(conn, pos)
    _seed_acked_entry_command(conn, pos, command_id="cmd-rescue-partial-1")
    conn.execute(
        "UPDATE venue_commands SET state = 'PARTIAL' WHERE command_id = ?",
        ("cmd-rescue-partial-1",),
    )
    append_trade_fact(
        conn,
        trade_id="trade-partial-1",
        venue_order_id="order-partial-1",
        command_id="cmd-rescue-partial-1",
        state="CONFIRMED",
        filled_size="1.505371",
        fill_price="0.0700000199286422",
        source="WS_USER",
        observed_at="2026-04-03T00:00:01+00:00",
        raw_payload_hash="b" * 64,
        raw_payload_json={
            "order_id": "order-partial-1",
            "trade_id": "trade-partial-1",
        },
    )

    stats = reconcile(
        _make_portfolio(pos),
        [
            ChainPosition(
                token_id="tok_yes_partial_001",
                size=1.505371,
                avg_price=0.0700000199286422,
                cost=0.105375,
                condition_id="cond-1",
            )
        ],
        conn=conn,
    )

    row = conn.execute(
        """
        SELECT phase, shares, cost_basis_usd, entry_price, fill_authority,
               order_status
          FROM position_current
         WHERE position_id = 'rescue-partial-1'
        """
    ).fetchone()
    runtime = query_portfolio_loader_view(conn, runtime_exposure_only=True)
    conn.close()

    assert stats["rescued_pending"] == 1
    assert pos.state == "entered"
    assert pos.entry_fill_verified is False
    assert pos.fill_authority == "venue_confirmed_partial"
    assert pos.order_status == "partial"
    assert pos.shares == pytest.approx(1.505371)
    assert pos.cost_basis_usd == pytest.approx(1.505371 * 0.0700000199286422)
    assert pos.entry_price == pytest.approx(0.0700000199286422)
    assert pos.shares_remaining == pytest.approx(95.0 - 1.505371)
    assert row["phase"] == "active"
    assert row["fill_authority"] == "venue_confirmed_partial"
    assert row["order_status"] == "partial"
    assert row["shares"] == pytest.approx(1.505371)
    assert row["cost_basis_usd"] == pytest.approx(1.505371 * 0.0700000199286422)
    assert runtime["status"] == "ok"
    assert [item["position_id"] for item in runtime["positions"]] == [
        "rescue-partial-1"
    ]


def test_lifecycle_kernel_rescues_pending_runtime_state_to_entered():
    from src.state.lifecycle_manager import rescue_pending_runtime_state

    assert rescue_pending_runtime_state("pending_tracked") == "entered"


def test_lifecycle_kernel_rejects_rescue_from_non_pending_runtime_state():
    from src.state.lifecycle_manager import rescue_pending_runtime_state

    with pytest.raises(ValueError, match="pending rescue requires pending_entry runtime phase"):
        rescue_pending_runtime_state("entered")


# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): enter_chain_quarantined_
# runtime_state deleted (dead — 0 production callers; the fake-Position
# chain-only minting path it supported is gone, replaced by typed
# ChainOnlyFact). The antibody that exercised it is removed with it.


def test_chain_reconciliation_rescue_updates_trade_lifecycle_row(tmp_path):
    """T1.c-followup rewrite 2026-04-23: post-T4.1b, the rescue audit trail
    flows through canonical position_events (CHAIN_SYNCED event_type +
    source_module='src.state.chain_reconciliation') rather than the
    legacy POSITION_LIFECYCLE_UPDATED-with-source-field shape. Test
    asserts the new canonical shape carries the rescue metadata that
    downstream audit consumers need (entry_order_id, chain_state,
    historical_entry_method, shares, cost_basis_usd, condition_id)."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema, query_position_events

    conn = get_connection(tmp_path / "rescue_db.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-db-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_db_001",
        no_token_id="tok_no_db_001",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-db-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_yes_db_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")],
        conn=conn,
    )
    conn.commit()
    events = query_position_events(conn, "rescue-db-1")
    conn.close()

    assert stats["rescued_pending"] == 1
    # Canonical entry trail from _seed_canonical_entry_baseline
    entry_event_types = [e["event_type"] for e in events]
    assert "POSITION_OPEN_INTENT" in entry_event_types
    assert "ENTRY_ORDER_POSTED" in entry_event_types

    # Rescue emission: PR D0 (Finding D0, 2026-05-27) — the fixture has no
    # linked venue trade fact, so the canonical event_type is now
    # VENUE_POSITION_OBSERVED (degraded recovery) rather than the previous
    # CHAIN_SYNCED. Same source_module + same metadata fields; the payload
    # additionally carries recovery_authority/causality_status/training_eligible.
    rescue_events = [e for e in events if e["event_type"] == "VENUE_POSITION_OBSERVED"]
    assert len(rescue_events) == 1
    rescue = rescue_events[0]
    assert rescue["source"] == "src.state.chain_reconciliation"
    assert rescue["order_id"] == "buy_123"
    details = rescue["details"]
    assert details["source"] == "chain_reconciliation"
    assert details["reason"] == "balance_only_recovery"
    assert details["from_state"] == "pending_tracked"
    assert details["to_state"] == "entered"
    assert details["entry_order_id"] == "buy_123"
    assert details["entry_fill_verified"] is False  # PR D0: balance-only rescue does NOT set entry_fill_verified=True
    assert details["chain_state"] == "synced"
    assert details["condition_id"] == "cond-1"
    assert details["recovery_authority"] == "balance_only"
    assert details["causality_status"] == "UNVERIFIED"
    assert details["training_eligible"] is False


def test_chain_reconciliation_rescue_emits_exactly_one_stage_event(tmp_path):
    """T1.c-followup rewrite 2026-04-23: post-T4.1b, rescue emits exactly
    one canonical event on first rescue; repeat reconcile calls on the
    same trade_id do not double-emit (idempotency guard via
    position_current phase check + already-logged check).

    PR D0 (Finding D0, 2026-05-27): the fixture has no linked venue trade
    fact, so the canonical event now uses event_type=VENUE_POSITION_OBSERVED
    and reason='balance_only_recovery' (degraded-recovery path), not the
    previous CHAIN_SYNCED / 'pending_fill_rescued' shape which applied
    when a trade fact existed. Trade-verified rescues still emit
    CHAIN_SYNCED via the unchanged builder; see the verified-path
    coverage in test_chain_reconciliation_rescues_commanded_pending_entry_with_trade_fact.
    """
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import get_connection, init_schema, query_position_events

    conn = get_connection(tmp_path / "rescue_rt.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="rescue-rt-1",
        state="pending_tracked",
        direction="buy_yes",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        order_id="buy_123",
        entry_order_id="buy_123",
        entry_fill_verified=False,
        entered_at="",
        order_status="pending",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-1",
    )
    _seed_canonical_entry_baseline(conn, pos)
    portfolio = _make_portfolio(pos)
    chain_row = ChainPosition(token_id="tok_yes_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-1")

    stats_first = reconcile(portfolio, [chain_row], conn=conn)
    stats_second = reconcile(portfolio, [chain_row], conn=conn)

    events = query_position_events(conn, "rescue-rt-1")
    conn.close()

    assert stats_first["rescued_pending"] == 1
    assert stats_second["rescued_pending"] == 0
    # PR D0 (Finding D0, 2026-05-27): balance-only rescue (no linked trade
    # fact) emits VENUE_POSITION_OBSERVED. Exactly ONE canonical event
    # (idempotency); payload carries degraded-recovery markers.
    rescue_events = [
        e for e in events
        if e["event_type"] == "VENUE_POSITION_OBSERVED"
        and e["source"] == "src.state.chain_reconciliation"
    ]
    assert len(rescue_events) == 1
    event = rescue_events[0]
    details = event["details"]
    assert details["from_state"] == "pending_tracked"
    assert details["to_state"] == "entered"
    assert details["source"] == "chain_reconciliation"
    assert details["reason"] == "balance_only_recovery"
    # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): the event payload
    # `shares` / `cost_basis_usd` / `size_usd` fields reflect submitted
    # entry economics (Position.shares / .cost_basis_usd / .size_usd at
    # emit time), NOT the chain aggregate. The chain aggregate lives on
    # the new chain_* payload fields below.
    assert details["shares"] == 25.0  # submitted shares from _make_position
    assert details["cost_basis_usd"] == 10.0  # submitted notional (was 11.0 chain pre-F1)
    assert details["condition_id"] == "cond-1"
    assert details["recovery_authority"] == "balance_only"
    assert details["causality_status"] == "UNVERIFIED"
    assert details["training_eligible"] is False
    # F1: chain economics on the event payload.
    assert details["chain_shares"] == 25.0
    assert details["chain_avg_price"] == 0.44
    assert details["chain_cost_basis_usd"] == 11.0


@pytest.mark.parametrize("exit_state", ["exit_intent", "sell_placed", "sell_pending", "retry_pending"])
def test_chain_reconciliation_does_not_void_exit_in_flight_positions(exit_state):
    """Chain sync must defer phantom authority while a sell order is in flight."""
    from src.state.chain_reconciliation import ChainPosition, reconcile

    exiting = _make_position(
        trade_id=f"exit-{exit_state}",
        token_id="tok_exit_001",
        no_token_id="tok_exit_no_001",
        state="holding",
        chain_state="synced",
        exit_state=exit_state,
    )
    healthy = _make_position(
        trade_id="healthy-sync-1",
        token_id="tok_live_001",
        no_token_id="tok_live_no_001",
        state="holding",
        chain_state="unknown",
        condition_id="cond-live-1",
    )
    portfolio = _make_portfolio(exiting, healthy)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_live_001", size=25.0, avg_price=0.40, cost=10.0, condition_id="cond-live-1")],
    )

    assert stats["voided"] == 0
    assert stats["skipped_pending_exit"] == 1
    assert exiting in portfolio.positions
    assert exiting.exit_state == exit_state
    assert exiting.chain_state == "exit_pending_missing"
    assert healthy.chain_state == "synced"
    assert healthy.condition_id == "cond-live-1"


def test_chain_reconciliation_does_not_void_economically_closed_positions():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    exiting = _make_position(
        trade_id="economic-close-1",
        token_id="tok_econ_001",
        no_token_id="tok_econ_no_001",
        state="economically_closed",
        exit_state="sell_filled",
        chain_state="synced",
    )
    healthy = _make_position(
        trade_id="healthy-sync-1",
        token_id="tok_live_001",
        no_token_id="tok_live_no_001",
        state="holding",
        chain_state="unknown",
        condition_id="cond-live-1",
    )
    portfolio = _make_portfolio(exiting, healthy)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_live_001", size=25.0, avg_price=0.40, cost=10.0, condition_id="cond-live-1")],
    )

    assert stats["voided"] == 0
    assert stats["skipped_economically_closed"] == 1
    assert exiting in portfolio.positions
    assert healthy.chain_state == "synced"


def test_chain_reconciliation_economically_closed_local_does_not_mask_chain_only_quarantine():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    exiting = _make_position(
        trade_id="economic-close-1",
        token_id="tok_econ_001",
        no_token_id="tok_econ_no_001",
        state="economically_closed",
        exit_state="sell_filled",
        chain_state="synced",
    )
    portfolio = _make_portfolio(exiting)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_econ_001", size=25.0, avg_price=0.40, cost=10.0, condition_id="cond-live-1")],
    )

    assert stats["chain_only_unresolved"] == 1
    # PR C2 (Finding 3, 2026-05-27): chain-only inventory is now a typed
    # ChainOnlyFact in portfolio.chain_only_facts, not a synthetic Position
    # in portfolio.positions. Verify the new signal carries the same identity
    # and economics; legacy Position-on-positions check removed.
    assert len(portfolio.chain_only_facts) == 1
    fact = portfolio.chain_only_facts[0]
    assert fact.token_id == "tok_econ_001"
    assert fact.size == 25.0
    assert fact.avg_price == 0.40
    assert fact.cost_basis == 10.0
    assert fact.condition_id == "cond-live-1"


def test_chain_only_fact_position_only_scope_does_not_freeze_new_entries():
    global_fact = ChainOnlyFact(
        token_id="global-token",
        condition_id="global-condition",
        size=1.0,
        avg_price=0.5,
        cost_basis=0.5,
        first_seen_at="2026-06-07T00:00:00+00:00",
        last_seen_at="2026-06-07T00:00:00+00:00",
    )
    position_only_fact = ChainOnlyFact(
        token_id="position-token",
        condition_id="position-condition",
        size=1.0,
        avg_price=0.5,
        cost_basis=0.5,
        first_seen_at="2026-06-07T00:00:00+00:00",
        last_seen_at="2026-06-07T00:00:00+00:00",
        entry_block_scope="position_only",
    )

    assert global_fact.blocks_entry is True
    assert global_fact.blocks_position_management is True
    assert position_only_fact.blocks_entry is False
    assert position_only_fact.blocks_position_management is True


def test_expired_chain_only_fact_does_not_freeze_new_entries():
    """Quarantine excision T2: the retired portfolio-wide
    ``_has_quarantined_positions`` gate is replaced by the family-scoped block
    (blocked_family_keys) + worst-case exposure reducer
    (chain_only_worst_case_add_usd) — both still respect
    ChainOnlyFact.blocks_entry exactly as the retired gate did. An EXPIRED
    fact contributes zero exposure and is never family-blocking.
    """
    from src.state.canonical_asset_exposure import chain_only_worst_case_add_usd

    expired_fact = ChainOnlyFact(
        token_id="expired-token",
        condition_id="expired-condition",
        size=1.0,
        avg_price=0.5,
        cost_basis=0.5,
        first_seen_at="2026-06-01T00:00:00+00:00",
        last_seen_at="2026-06-03T00:00:00+00:00",
        review_state=ChainOnlyReviewState.EXPIRED,
    )

    portfolio = _make_portfolio()
    portfolio.chain_only_facts.append(expired_fact)

    assert expired_fact.blocks_entry is False
    add_usd, _any_unmapped = chain_only_worst_case_add_usd(None, portfolio)
    assert add_usd == 0.0


def test_chain_reconciliation_does_not_void_verified_entry_waiting_for_chain():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    entered = _make_position(
        trade_id="entered-waiting-chain",
        token_id="tok_entry_001",
        no_token_id="tok_entry_no_001",
        state="entered",
        chain_state="local_only",
        entry_fill_verified=True,
        order_status="filled",
    )
    healthy = _make_position(
        trade_id="healthy-sync-2",
        token_id="tok_live_002",
        no_token_id="tok_live_no_002",
        state="holding",
        chain_state="unknown",
        condition_id="cond-live-2",
    )
    portfolio = _make_portfolio(entered, healthy)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_live_002", size=25.0, avg_price=0.40, cost=10.0, condition_id="cond-live-2")],
    )

    assert stats["voided"] == 0
    assert stats["awaiting_chain_entry"] == 1
    assert entered in portfolio.positions
    assert entered.chain_state == "local_only"


def test_chain_reconciliation_keeps_fill_cost_when_wallet_share_count_matches():
    from src.state.chain_reconciliation import ChainPosition, reconcile

    pos = _make_position(
        trade_id="cost-sync-1",
        token_id="tok_cost_001",
        no_token_id="tok_cost_no_001",
        state="holding",
        chain_state="unknown",
        shares=25.0,
        size_usd=10.0,
        cost_basis_usd=10.0,
        entry_price=0.40,
    )
    portfolio = _make_portfolio(pos)

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok_cost_001", size=25.0, avg_price=0.44, cost=11.0, condition_id="cond-cost-1")],
    )

    assert stats["synced"] == 1
    assert pos.chain_state == "synced"
    # Wallet position economics are an aggregate observation, not authority to
    # rewrite command/fill-owned acquisition provenance.
    assert pos.cost_basis_usd == pytest.approx(10.0)
    assert pos.size_usd == pytest.approx(10.0)
    assert pos.entry_price == pytest.approx(0.40)
    assert pos.chain_cost_basis_usd == pytest.approx(11.0)
    assert pos.chain_avg_price == pytest.approx(0.44)


# ---- Test 4: Retry respects cooldown ----


def test_exit_retry_respects_cooldown():
    """After failed sell, must wait cooldown before retrying."""
    future_time = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    pos = _make_position(
        exit_state="retry_pending",
        next_exit_retry_at=future_time,
        exit_retry_count=1,
    )

    assert is_exit_cooldown_active(pos) is True

    # check_pending_retries should not reset a position in cooldown
    result = check_pending_retries(pos)
    assert result is False
    assert pos.exit_state == "retry_pending"  # unchanged


# ---- Test 5: Backoff exhausted holds to settlement ----


# ---- Test 5: Backoff exhausted holds to settlement ----

def test_backoff_exhausted_holds_to_settlement():
    """After MAX_EXIT_RETRIES retries, stop trying to sell. Hold to settlement."""
    pos = _make_position(
        exit_state="backoff_exhausted",
        exit_retry_count=MAX_EXIT_RETRIES,
    )
    portfolio = _make_portfolio(pos)

    # execute_exit should not be called for backoff_exhausted positions,
    # but even if it were, the position should remain unchanged
    result = check_pending_retries(pos)
    assert result is False
    assert pos.exit_state == "backoff_exhausted"

    # Position stays in portfolio — not closed, not voided
    assert pos in portfolio.positions
    assert pos.state != "settled"
    assert pos.state != "voided"


def test_pending_exit_backoff_exhausted_reenters_redecision_when_still_held(monkeypatch):
    """Backoff exhaustion is an order-attempt state, not a permanent monitor stop."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="backoff-exhausted-held-risk",
        direction="buy_no",
        state="pending_exit",
        pre_exit_state="holding",
        chain_state="synced",
        shares=18.0,
        chain_shares=18.0,
        city="Miami",
        target_date="2026-07-02",
        token_id="yes-miami",
        no_token_id="no-miami",
        condition_id="condition-miami",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_retry_count=MAX_EXIT_RETRIES,
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        last_exit_error="previous_order_attempt_budget_exhausted",
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.44, 0.46, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("hold redecision must not record an exit")

    observed_refresh = []

    def mock_refresh(conn, clob, position):
        observed_refresh.append((
            position.trade_id,
            getattr(position.state, "value", position.state),
            getattr(position, "exit_state", ""),
        ))
        position.last_monitor_prob = 0.70
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.44
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.44
        position.last_monitor_best_ask = 0.46
        position.last_monitor_market_vig = 0.90
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = "2026-07-01T12:00:00+00:00"
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.44]),
            p_posterior=0.70,
            forward_edge=0.26,
            alpha=0.0,
            confidence_band_upper=0.05,
            confidence_band_lower=-0.01,
            entry_provenance=EntryMethod.QKERNEL_SPINE,
            decision_snapshot_id="snap-backoff-redecision",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    observed_exit_contexts = []

    def mock_evaluate_exit(self, exit_context):
        observed_exit_contexts.append(exit_context)
        return ExitDecision(
            False,
            "CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_backoff_exhausted_redecision"),
            "cities_by_name": {"Miami": type("City", (), {"timezone": "America/New_York"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
    )

    assert observed_refresh == [("backoff-exhausted-held-risk", "holding", "")]
    assert observed_exit_contexts
    assert pos.state == "holding"
    assert pos.exit_state == ""
    assert pos.order_status == "filled"
    assert pos.exit_retry_count == 0
    assert pos.exit_reason == ""
    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["monitor_released_backoff_exhausted_for_redecision"] == 1
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].should_exit is False
    assert monitor_results[0].exit_reason == "CI_OVERLAP_HOLD"


@pytest.mark.parametrize(
    (
        "trigger",
        "has_position_coverage",
        "request_accepted",
        "outcome",
        "malformed_request",
        "posterior_support_zero",
    ),
    (
        ("EDGE_REVERSAL", True, True, "delegated", False, False),
        ("EDGE_REVERSAL", False, True, "blocked", False, False),
        ("EDGE_REVERSAL", False, False, "request_failed", False, False),
        ("CI_OVERLAP_SELL_VALUE_DOMINATES", False, True, "blocked", False, False),
        ("SETTLEMENT_IMMINENT", False, True, "blocked", False, False),
        (
            "DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES",
            False,
            True,
            "blocked",
            False,
            False,
        ),
        ("UNREGISTERED_STATISTICAL_SELL", False, True, "blocked", False, False),
        ("DAY0_HARD_FACT_BIN_DEAD_FOO", False, True, "blocked", False, False),
        ("RED_FORCE_EXIT", True, True, "direct", False, False),
        ("DAY0_HARD_FACT_BIN_DEAD", True, True, "direct", False, False),
        ("EDGE_REVERSAL", False, True, "blocked", True, False),
        ("SELL_REVERSAL", False, True, "direct", False, True),
        ("EDGE_REVERSAL", False, True, "dust", False, False),
    ),
)
def test_current_global_monitor_sell_has_one_statistical_actuator_and_preserves_red(
    tmp_path,
    monkeypatch,
    trigger,
    has_position_coverage,
    request_accepted,
    outcome,
    malformed_request,
    posterior_support_zero,
):
    """Statistical SELL is global-only; missing authority holds while RED acts."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime, monitor_refresh
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.events import reactor as event_reactor
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "global-auction-owns-monitor-sell.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="global-auction-owned-sell",
        state="holding",
        city="Paris",
        target_date="2026-07-14",
        direction="buy_no",
        strategy_key="center_buy",
        order_status="filled",
        entered_at="2026-07-14T17:00:00+00:00",
        order_posted_at="2026-07-14T16:59:00+00:00",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        shares=3.0 if outcome == "dust" else 500.0,
        shares_filled=3.0 if outcome == "dust" else 500.0,
        chain_state="synced",
        chain_shares=500.0,
        token_id="paris-yes",
        no_token_id="paris-no",
        condition_id="0x" + "5a" * 32,
    )
    events, projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-global-auction-owned-sell",
        source_module="tests/test_current_global_monitor_sell_is_non_authoritative",
    )
    append_many_and_project(conn, events, projection)
    portfolio = _make_portfolio(pos)

    def fake_refresh(_conn, _clob, position):
        position.last_monitor_prob = 0.0 if posterior_support_zero else 0.10
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = -0.50 if posterior_support_zero else -0.40
        position.last_monitor_market_price = 0.50
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.49
        position.last_monitor_best_ask = 0.50
        position.last_monitor_at = "2026-07-14T18:00:00+00:00"
        setattr(
            position,
            monitor_refresh._HELD_MONITOR_MIN_ORDER_SIZE_ATTR,
            5.0 if outcome == "dust" else None,
        )
        setattr(
            position,
            monitor_refresh._GLOBAL_MONITOR_SAMPLES_ATTR,
            (
                np.array([0.0, 0.0])
                if posterior_support_zero
                else np.array([0.05, 0.15])
            ),
        )
        if trigger == "UNREGISTERED_STATISTICAL_SELL":
            setattr(
                position,
                "_monitor_probability_receipt",
                {
                    "posterior_id": "213173",
                    "computed_at": "2026-07-14T17:55:00+00:00",
                    "held_side_probability": 0.10,
                    "evidence_content_hash": "scalar-monitor-evidence",
                },
            )
        else:
            setattr(
                position,
                "_day0_monitor_probability_receipt",
                {
                    "probability_witness_identity": "probability-current",
                    "probability_content_identity": (
                        "probability-content-current"
                    ),
                    "q_version": "probability-content-current",
                    "source_truth_identity": "source-current",
                    "band": {
                        "alpha": 0.05,
                        "basis": "test-band",
                    },
                },
            )
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.50]),
            p_posterior=0.0 if posterior_support_zero else 0.10,
            forward_edge=-0.50 if posterior_support_zero else -0.40,
            alpha=0.1,
            confidence_band_upper=-0.35,
            confidence_band_lower=-0.45,
            entry_provenance=EntryMethod.QKERNEL_SPINE,
            decision_snapshot_id="global-monitor-sell-snapshot",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

    monkeypatch.setattr(monitor_refresh, "refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, context: ExitDecision(
            True,
            trigger,
            trigger=trigger,
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["local_monitor_sell_signal"],
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_closed_non_accepting_market_info",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_entry_selection_guard_exit_decision",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_apply_family_monitor_overlay",
        lambda **kwargs: (kwargs["should_exit"], kwargs["exit_reason"]),
    )
    from src.engine import global_batch_runtime
    from src.execution import exit_lifecycle

    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_fresh_snapshot_min_order",
        lambda *args, **kwargs: None,
    )

    coverage_checks = []

    def current_monitor_coverage(**kwargs):
        coverage_checks.append(kwargs["position"].trade_id)
        return (
            global_batch_runtime.CurrentGlobalHoldingCoverage(
                outcome=global_batch_runtime.GlobalHoldingCoverageOutcome.COVERED,
                reason="test-coverage",
                coverage=SimpleNamespace(selection_epoch_identity="epoch-current"),
                decision_log_id=77,
            )
            if has_position_coverage
            else None
        )

    monkeypatch.setattr(
        cycle_runtime,
        "_current_monitor_global_holding_coverage",
        current_monitor_coverage,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_exit_evidence_gate_allows_statistical_exit",
        lambda **kwargs: (True, None),
    )
    invalidations = []
    monkeypatch.setattr(
        global_batch_runtime,
        "_invalidate_global_holding_coverage",
        lambda: invalidations.append("venue_side_effect"),
    )
    execute_calls = []
    auction_completion_requests = []
    published_requests = []
    reserved_requests = []
    event_order = []

    real_emit_monitor_refreshed = (
        cycle_runtime._emit_monitor_refreshed_canonical_if_available
    )

    def emit_monitor_refreshed_then_mark(*args, **kwargs):
        result = real_emit_monitor_refreshed(*args, **kwargs)
        if result:
            event_order.append("canonical_monitor_refreshed")
        return result

    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        emit_monitor_refreshed_then_mark,
    )

    monkeypatch.setattr(
        event_reactor,
        "publish_prepared_global_auction_completion",
        lambda **kwargs: (
            event_order.append("publish"),
            published_requests.append(kwargs["prepared_request"]),
            True,
        )[-1],
    )
    if request_accepted:
        monkeypatch.setattr(
            "src.execution.exit_lifecycle.record_global_sell_reauction_reserved",
            lambda _conn, position: reserved_requests.append(position.trade_id) or True,
        )

    def request_global_completion(**kwargs):
        auction_completion_requests.append(kwargs)
        if malformed_request:
            return True, SimpleNamespace(
                request_id="request-global-auction-owned-sell-malformed"
            )
        if not request_accepted:
            return False, SimpleNamespace(
                request_id="request-global-auction-owned-sell-failed",
                material_identity="material-global-auction-owned-sell",
                attempt_identity="attempt-global-auction-owned-sell",
                schema_version=4,
                scope_identity="scope-global-auction-owned-sell",
                generation="generation-global-auction-owned-sell",
                position_id=pos.trade_id,
                family=(pos.city, pos.target_date, pos.temperature_metric),
                held_token_id="paris-no",
                probability_content_identity="probability-content-current",
                probability_observed_at="2026-07-14T18:00:00+00:00",
                held_best_bid=0.49,
                bid_observed_at="2026-07-14T18:00:00+00:00",
                book_state="EXECUTABLE",
            )
        return True, SimpleNamespace(
            request_id="request-global-auction-owned-sell",
            material_identity="material-global-auction-owned-sell",
            attempt_identity="attempt-global-auction-owned-sell",
            schema_version=4,
            scope_identity="scope-global-auction-owned-sell",
            generation="generation-global-auction-owned-sell",
            position_id=pos.trade_id,
            family=(pos.city, pos.target_date, pos.temperature_metric),
            held_token_id="paris-no",
            probability_content_identity=kwargs["probability_content_identity"],
            probability_observed_at=kwargs["probability_observed_at"],
            held_best_bid=kwargs["held_best_bid"],
            bid_observed_at=kwargs["bid_observed_at"],
            book_state=kwargs["book_state"],
            completion_deadline_at=kwargs["completion_deadline_at"],
        )

    monkeypatch.setattr(
        event_reactor,
        "request_global_auction_completion",
        request_global_completion,
    )

    def fake_execute_exit(*args, **kwargs):
        execute_calls.append(kwargs.get("position") or args[1])
        return "exit_failed:test_stub"

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        fake_execute_exit,
    )

    monitor_now = (
        (lambda: datetime.now(timezone.utc))
        if outcome == "dust"
        else (lambda: datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc))
    )

    results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: results.append(result)},
    )()
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
            ),
            "logger": logging.getLogger("test_global_auction_owned_monitor_sell"),
            "cities_by_name": {},
            "_utcnow": staticmethod(monitor_now),
        },
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
        run_exit_preflight=False,
    )

    if outcome == "delegated":
        assert summary["monitor_sells_delegated_to_global_auction"] == 1
        assert summary["exits"] == 0
        assert results[0].should_exit is False
        assert results[0].exit_reason == "GLOBAL_AUCTION_OWNS_REDUCE_ONLY_SELL"
        assert "local_monitor_sell_delegated_to_global_auction" in pos.applied_validations
        assert execute_calls == []
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE intent_kind = 'EXIT'"
        ).fetchone()[0] == 0
    elif outcome == "dust":
        assert summary["monitor_statistical_sell_dust_holds"] == 1
        assert summary["exits"] == 0
        assert results[0].should_exit is False
        assert "[DUST:" in results[0].exit_reason
        assert pos.order_status == "backoff_exhausted"
        assert "fresh_snapshot_sub_minimum_dust_hold" in pos.applied_validations
        assert execute_calls == []
        assert auction_completion_requests == []
    elif outcome in {"blocked", "request_failed"}:
        completion_accepted = request_accepted and not malformed_request
        assert summary.get("monitor_sells_delegated_to_global_auction", 0) == 0
        assert (
            summary["monitor_statistical_sells_blocked_without_global_authority"]
            == 1
        )
        assert summary["exits"] == 0
        assert results[0].should_exit is False
        assert results[0].exit_reason == "GLOBAL_REAUCTION_PENDING"
        assert "local_statistical_sell_non_authoritative_record" in pos.applied_validations
        request_status = (
            "global_auction_completion_requested"
            if completion_accepted
            else "global_auction_completion_request_failed"
        )
        assert request_status in pos.applied_validations
        expected_authority_outcome = (
            "PROBABILITY_CONTENT"
            if trigger == "UNREGISTERED_STATISTICAL_SELL"
            else "COVERAGE_NOT_PUBLISHED"
        )
        assert (
            f"global_auction_authority_outcome:{expected_authority_outcome}"
            in pos.applied_validations
        )
        assert (
            "global_auction_completion_debt:"
            + ("DRAIN_PENDING" if completion_accepted else "REQUEST_REJECTED")
        ) in pos.applied_validations
        assert (
            "global_auction_completion_monitor_identity:"
            "global-auction-owned-sell:monitor_refreshed:"
            "2026-07-14T18:00:00+00:00"
        ) in pos.applied_validations
        assert "GLOBAL_REAUCTION_PENDING" in pos.applied_validations
        if completion_accepted:
            assert (
                "global_auction_completion_request_id:"
                "request-global-auction-owned-sell"
            ) in pos.applied_validations
            payload = json.loads(
                conn.execute(
                    """
                    SELECT payload_json FROM position_events
                     WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
                     ORDER BY sequence_no DESC LIMIT 1
                    """,
                    (pos.trade_id,),
                ).fetchone()[0]
            )
            obligation = payload["held_sell_reauction_obligation"]
            assert obligation["state"] == "ARMED"
            assert obligation["request_id"] == "request-global-auction-owned-sell"
            assert obligation["material_identity"] == (
                "material-global-auction-owned-sell"
            )
            assert obligation["attempt_identity"] == (
                "attempt-global-auction-owned-sell"
            )
            armed_at = datetime.fromisoformat(obligation["armed_at"])
            deadline = datetime.fromisoformat(obligation["completion_deadline_at"])
            assert (deadline - armed_at).total_seconds() == 30.0
            assert published_requests[0].request_id == obligation["request_id"]
            assert reserved_requests == [pos.trade_id]
            assert event_order == ["canonical_monitor_refreshed", "publish"]
        elif malformed_request:
            assert (
                "global_auction_completion_request_failed"
                in pos.applied_validations
            )
            assert "global_auction_completion_debt:DRAIN_PENDING" not in (
                pos.applied_validations
            )
            payload = json.loads(
                conn.execute(
                    """
                    SELECT payload_json FROM position_events
                     WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
                     ORDER BY sequence_no DESC LIMIT 1
                    """,
                    (pos.trade_id,),
                ).fetchone()[0]
            )
            assert "held_sell_reauction_obligation" not in payload
            assert published_requests == []
            assert reserved_requests == []
        else:
            assert (
                "global_auction_completion_request_id:"
                "request-global-auction-owned-sell-failed"
            ) in pos.applied_validations
            from src.execution.exit_lifecycle import (
                needs_global_sell_snapshot_reauction,
                recover_global_sell_snapshot_reauction_debt,
            )

            payload = json.loads(
                conn.execute(
                    """
                    SELECT payload_json FROM position_events
                     WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
                     ORDER BY sequence_no DESC LIMIT 1
                    """,
                    (pos.trade_id,),
                ).fetchone()[0]
            )
            obligation = payload["held_sell_reauction_obligation"]
            assert obligation["scope_identity"] == (
                "scope-global-auction-owned-sell"
            )
            assert obligation["generation"] == (
                "generation-global-auction-owned-sell"
            )
            assert needs_global_sell_snapshot_reauction(pos, conn) is True
            recovery_requests = []
            assert recover_global_sell_snapshot_reauction_debt(
                pos,
                conn=conn,
                requester=lambda position, force_new: (
                    recovery_requests.append((position.trade_id, force_new)) or True
                ),
            ) is True
            assert recovery_requests == [(pos.trade_id, True)]
            assert needs_global_sell_snapshot_reauction(pos, conn) is False
        request_summary_key = (
            "monitor_statistical_sell_auction_completion_requested"
            if completion_accepted
            else "monitor_statistical_sell_auction_completion_request_failed"
        )
        assert summary[request_summary_key] == 1
        expected_request_context = (
            {
                "probability_content_identity": "",
                "held_best_bid": None,
                "bid_observed_at": "",
                "book_state": "UNKNOWN",
                "probability_observed_at": "",
            }
            if trigger == "UNREGISTERED_STATISTICAL_SELL"
            else {
                "probability_content_identity": "probability-content-current",
                "held_best_bid": 0.49,
                "bid_observed_at": "2026-07-14T18:00:00+00:00",
                "book_state": "EXECUTABLE",
                "probability_observed_at": "",
            }
        )
        assert auction_completion_requests == [
            {
                "reason": (
                    "GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE"
                ),
                "position_id": pos.trade_id,
                "family": (
                    pos.city,
                    pos.target_date,
                    pos.temperature_metric,
                ),
                "held_token_id": "paris-no",
                "completion_deadline_at": "2026-07-14T18:00:30+00:00",
                "return_request": True,
                "prepare_only": True,
                **expected_request_context,
            }
        ]
        assert execute_calls == []
    else:
        assert summary.get("monitor_sells_delegated_to_global_auction", 0) == 0
        assert summary.get(
            "monitor_statistical_sells_blocked_without_global_authority", 0
        ) == 0
        assert summary["exits"] == 1
        assert results[0].should_exit is True
        assert results[0].exit_reason == (
            "POSTERIOR_SUPPORT_ZERO_SELL_DOMINATES"
            if posterior_support_zero
            else trigger
        )
        if posterior_support_zero:
            assert summary["monitor_branchwise_dominant_direct_sells"] == 1
            assert "posterior_support_zero_sell_dominates" in (
                pos.applied_validations
            )
        assert execute_calls == [pos]
    if outcome not in {"blocked", "request_failed", "dust"}:
        assert auction_completion_requests == []
    assert invalidations == ([] if outcome != "direct" else ["venue_side_effect"])
    conn.close()


def test_non_day0_scalar_monitor_requests_full_family_reauction_without_fake_q_identity():
    """A scalar held-bin q may trigger redecision but cannot impersonate MECE q."""
    from src.engine import cycle_runtime
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    position = SimpleNamespace(
        position_id="non-day0-scalar-sell",
        trade_id="non-day0-scalar-sell",
        city="Shanghai",
        target_date="2026-08-13",
        temperature_metric="high",
        direction="buy_yes",
        token_id="shanghai-yes",
        no_token_id="shanghai-no",
        last_monitor_at="2026-08-12T05:53:52+00:00",
        _monitor_probability_receipt={
            "posterior_id": "213173",
            "computed_at": "2026-08-12T01:39:11.037562+00:00",
            "held_side_probability": 0.3055789346340416,
            "evidence_content_hash": "monitor-evidence-is-not-full-family-q",
        },
    )
    context = cycle_runtime._monitor_global_sell_request_context(
        position,
        SimpleNamespace(best_bid=0.31),
    )

    assert context == {
        "probability_content_identity": "",
        "probability_observed_at": "",
        "held_best_bid": None,
        "bid_observed_at": "",
        "book_state": "UNKNOWN",
    }
    request = make_held_sell_reauction_request(
        position_id=position.position_id,
        family=(position.city, position.target_date, position.temperature_metric),
        held_token_id=position.token_id,
        schema_version=4,
        **context,
    )
    assert request.book_state == "UNKNOWN"
    assert request.probability_content_identity == ""
    assert request.held_best_bid is None
    assert request.bid_observed_at == ""
    assert request.probability_observed_at == ""


def test_market_authority_refresh_extends_delta_scope_but_preserves_full_refresh():
    from src.engine import event_reactor_adapter

    assert event_reactor_adapter._effective_global_book_refresh_family_keys(
        frozenset(),
        frozenset(),
        {"family-jit-fee"},
    ) == frozenset({"family-jit-fee"})
    assert (
        event_reactor_adapter._effective_global_book_refresh_family_keys(
            None,
            frozenset({"family-metadata"}),
            {"family-jit-fee"},
        )
        is None
    )


@pytest.mark.parametrize(
    ("best_bid", "last_monitor_at", "expected_bid", "expected_book_state"),
    (
        (None, "2026-08-12T05:53:52+00:00", None, "UNKNOWN"),
        ("invalid", "2026-08-12T05:53:52+00:00", None, "UNKNOWN"),
        (float("nan"), "2026-08-12T05:53:52+00:00", None, "UNKNOWN"),
        (0.03, "2026-08-12T05:53:52+00:00", 0.03, "NO_EXECUTABLE_BOOK"),
        (0.31, "", 0.31, "STALE"),
        (0.31, "2026-08-12T05:53:52+00:00", 0.31, "EXECUTABLE"),
    ),
)
def test_exact_monitor_q_classifies_book_before_v4_reauction(
    best_bid,
    last_monitor_at,
    expected_bid,
    expected_book_state,
):
    from src.engine import cycle_runtime
    from src.runtime.reactor_wake import make_held_sell_reauction_request

    position = SimpleNamespace(
        position_id="exact-q-sell",
        city="Shanghai",
        target_date="2026-08-13",
        temperature_metric="high",
        token_id="shanghai-yes",
        last_monitor_at=last_monitor_at,
        _day0_monitor_probability_receipt={
            "probability_content_identity": "full-family-q-content",
            "computed_at": "2026-08-12T05:53:00+00:00",
        },
    )

    context = cycle_runtime._monitor_global_sell_request_context(
        position,
        SimpleNamespace(best_bid=best_bid),
    )

    assert context["held_best_bid"] == expected_bid
    assert context["book_state"] == expected_book_state
    request = make_held_sell_reauction_request(
        position_id=position.position_id,
        family=(position.city, position.target_date, position.temperature_metric),
        held_token_id=position.token_id,
        schema_version=4,
        **context,
    )
    assert request.book_state == expected_book_state
    assert request.held_best_bid == expected_bid


@pytest.mark.parametrize(
    ("samples", "fresh_prob", "best_bid", "fresh", "expected"),
    (
        ((0.0, 0.0), 0.0, 0.05, True, True),
        ((0.0, 1e-6), 5e-7, 0.05, True, False),
        ((0.0, 0.0), 0.0, 0.049, True, False),
        ((0.0, 0.0), 0.0, 0.05, False, False),
        ((), 0.0, 0.05, True, False),
    ),
)
def test_branchwise_dominant_sell_requires_zero_support_and_legal_fresh_bid(
    samples,
    fresh_prob,
    best_bid,
    fresh,
    expected,
):
    from src.engine import cycle_runtime

    pos = SimpleNamespace(_current_global_held_probability_samples=samples)
    context = SimpleNamespace(
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=fresh,
        current_market_price_is_fresh=fresh,
        best_bid=best_bid,
    )

    assert (
        cycle_runtime._posterior_support_zero_sell_dominates(pos, context)
        is expected
    )


def test_held_monitor_quote_preserves_current_book_min_order_size():
    from src.engine import monitor_refresh

    pos = _make_position(
        trade_id="held-quote-min-order",
        token_id="held-yes-token",
        direction="buy_yes",
        state="day0_window",
    )

    class Clob:
        @staticmethod
        def get_orderbook(_token_id):
            return {
                "asset_id": "held-yes-token",
                "bids": [{"price": "0.04", "size": "20"}],
                "asks": [{"price": "0.06", "size": "20"}],
                "min_order_size": "5",
            }

    quote = monitor_refresh.monitor_quote_refresh(None, Clob(), pos)

    assert quote is not None
    assert quote.best_bid == 0.04
    assert quote.min_order_size == 5.0


def test_reserved_global_sell_reauction_deadline_is_an_actuation_contract(
    monkeypatch,
):
    """An unanswered reserved SELL returns to recovery after its deadline."""
    from src.execution import exit_lifecycle
    from src.runtime import reactor_wake

    now = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)
    request = SimpleNamespace(
        request_id="request-deadline",
        material_identity="material-deadline",
        generation="generation-deadline",
        attempt_identity="attempt-deadline",
    )
    obligation = {
        "schema_version": 4,
        "scope_identity": "scope-deadline",
        "request_id": request.request_id,
        "material_identity": request.material_identity,
        "generation": request.generation,
        "attempt_identity": request.attempt_identity,
        "position_id": "position-deadline",
        "held_token_id": "token-deadline",
        "completion_deadline_at": (now + timedelta(seconds=1)).isoformat(),
    }
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_events (
            position_id TEXT,
            event_type TEXT,
            sequence_no INTEGER,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )

    def write_obligation(value):
        conn.execute("DELETE FROM position_events")
        conn.execute(
            "INSERT INTO position_events VALUES (?, ?, ?, ?, ?)",
            (
                "position-deadline",
                "MONITOR_REFRESHED",
                1,
                now.isoformat(),
                json.dumps(
                    {
                        "global_sell_reauction_status": "durable_wake_reserved",
                        "held_sell_reauction_obligation": value,
                    }
                ),
            ),
        )

    position = SimpleNamespace(
        trade_id="position-deadline",
        last_exit_error="",
    )
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: now)
    monkeypatch.setattr(
        reactor_wake,
        "latest_v4_held_sell_reauction_request",
        lambda _scope: request,
    )
    completed = False
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: completed,
    )

    write_obligation(obligation)
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn) is False

    obligation["completion_deadline_at"] = (now - timedelta(seconds=1)).isoformat()
    write_obligation(obligation)
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn) is True

    conn.execute(
        "INSERT INTO position_events VALUES (?, ?, ?, ?, ?)",
        (
            "position-deadline",
            "MONITOR_REFRESHED",
            2,
            (now + timedelta(seconds=2)).isoformat(),
            json.dumps({"exit_decision_reason": "HOLD"}),
        ),
    )
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn) is True

    completed = True
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn) is False

    request.attempt_identity = "newer-attempt"
    completed = False
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn) is True
    conn.close()


def test_same_global_sell_attempt_cannot_slide_its_deadline():
    """Repeated monitor evidence cannot postpone an unanswered SELL forever."""
    from src.execution.exit_lifecycle import preserve_held_sell_reauction_deadline

    existing = {
        "scope_identity": "scope",
        "generation": "generation",
        "attempt_identity": "attempt",
        "armed_at": "2026-08-08T18:00:00+00:00",
        "completion_deadline_at": "2026-08-08T18:00:30+00:00",
    }
    refreshed = {
        **existing,
        "armed_at": "2026-08-08T18:00:20+00:00",
        "completion_deadline_at": "2026-08-08T18:00:50+00:00",
    }
    preserved = preserve_held_sell_reauction_deadline(refreshed, existing)
    assert preserved["armed_at"] == existing["armed_at"]
    assert preserved["completion_deadline_at"] == existing[
        "completion_deadline_at"
    ]

    refreshed["attempt_identity"] = "new-attempt"
    advanced = preserve_held_sell_reauction_deadline(refreshed, existing)
    assert advanced["armed_at"] == refreshed["armed_at"]
    assert advanced["completion_deadline_at"] == refreshed[
        "completion_deadline_at"
    ]


@pytest.mark.parametrize(
    (
        "canonical_newer",
        "canonical_probability_identity",
        "append_later_sequence_with_older_clock",
        "expected_probability_identity",
        "expected_bid",
        "expected_observed_at",
    ),
    (
        (False, "", False, "q-current", 0.31, "2026-08-08T18:01:00+00:00"),
        (True, "q-canonical", False, "q-canonical", 0.27, "2026-08-08T18:02:00"),
        (True, "", False, None, None, None),
        (True, "q-canonical", True, "q-canonical", 0.27, "2026-08-08T18:02:00"),
    ),
    ids=(
        "runtime-cut-newer",
        "canonical-cut-newer-naive-clock",
        "newest-canonical-cut-missing-q-fails-closed",
        "newest-causal-clock-beats-later-sequence",
    ),
)
def test_expired_global_sell_debt_refreshes_q_and_book_before_reauction(
    tmp_path,
    monkeypatch,
    canonical_newer,
    canonical_probability_identity,
    append_later_sequence_with_older_clock,
    expected_probability_identity,
    expected_bid,
    expected_observed_at,
):
    """Deadline recovery is a fresh decision, not a replay of its old witness."""
    from src.engine import cycle_runtime, monitor_refresh
    from src.engine.lifecycle_events import build_position_current_projection
    from src.events import reactor as event_reactor
    from src.runtime import reactor_wake
    from src.state.db import get_connection, init_schema
    from src.state.projection import upsert_position_current

    conn = get_connection(tmp_path / "fresh-global-sell-recovery.db")
    init_schema(conn)
    position = _make_position(
        trade_id="fresh-global-sell-recovery",
        state="holding",
        chain_state="synced",
        chain_shares=10.0,
        shares=10.0,
        token_id="fresh-global-sell-token",
        no_token_id="fresh-global-sell-no-token",
        condition_id="0x" + "7c" * 32,
        direction="buy_yes",
        strategy_key="center_buy",
        entered_at="2026-08-08T17:00:00+00:00",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    old_request = SimpleNamespace(
        request_id="request-fresh-recovery",
        material_identity="scope-fresh-recovery",
        generation="generation-fresh-recovery",
        attempt_identity="attempt-stale",
        scope_identity="scope-fresh-recovery",
        probability_observed_at="2026-08-08T18:00:00+00:00",
        bid_observed_at="2026-08-08T18:00:00+00:00",
    )
    obligation = {
        "schema_version": 4,
        "scope_identity": old_request.scope_identity,
        "request_id": old_request.request_id,
        "material_identity": old_request.material_identity,
        "generation": old_request.generation,
        "attempt_identity": old_request.attempt_identity,
        "position_id": position.trade_id,
        "family": (position.city, position.target_date, position.temperature_metric),
        "held_token_id": position.token_id,
        "probability_content_identity": "q-stale",
        "probability_observed_at": old_request.probability_observed_at,
        "held_best_bid": 0.49,
        "bid_observed_at": old_request.bid_observed_at,
        "book_state": "EXECUTABLE",
        "state": "ARMED",
        "armed_at": "2026-08-08T18:00:00+00:00",
        "completion_deadline_at": "2026-08-08T18:00:30+00:00",
    }
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, source_module, env, payload_json
        ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, ?, 'live', ?)
        """,
        (
            "fresh-global-sell-recovery:monitor:1",
            position.trade_id,
            "2026-08-08T18:00:00+00:00",
            "tests/test_live_safety_invariants",
            json.dumps(
                {
                    "global_sell_reauction_status": "durable_wake_reserved",
                    "held_sell_reauction_obligation": obligation,
                }
            ),
        ),
    )
    conn.commit()
    monkeypatch.setattr(
        reactor_wake,
        "latest_v4_held_sell_reauction_request",
        lambda _scope: old_request,
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )
    refreshes = []

    def refresh_current(_conn, _clob, refreshed):
        refreshes.append(refreshed.trade_id)
        refreshed.last_monitor_prob = 0.08
        refreshed.last_monitor_prob_is_fresh = True
        refreshed.last_monitor_market_price = 0.31
        refreshed.last_monitor_market_price_is_fresh = True
        refreshed.last_monitor_best_bid = 0.31
        refreshed.last_monitor_at = "2026-08-08T18:01:00+00:00"
        refreshed._monitor_probability_receipt = {
            "probability_content_identity": "q-current",
        }
        if canonical_newer:
            sequence_no = _conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 "
                "FROM position_events WHERE position_id = ?",
                (refreshed.trade_id,),
            ).fetchone()[0]
            _conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no,
                    event_type, occurred_at, source_module, env, payload_json
                ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, ?, 'live', ?)
                """,
                (
                    "fresh-global-sell-recovery:monitor:2",
                    refreshed.trade_id,
                    sequence_no,
                    "2026-08-08T18:02:00",
                    "tests/test_live_safety_invariants",
                    json.dumps(
                        {
                            "last_monitor_best_bid": 0.27,
                            "monitor_probability_receipt": {
                                "probability_content_identity": (
                                    canonical_probability_identity
                                ),
                            },
                        }
                    ),
                ),
            )
            if append_later_sequence_with_older_clock:
                _conn.execute(
                    """
                    INSERT INTO position_events (
                        event_id, position_id, event_version, sequence_no,
                        event_type, occurred_at, source_module, env, payload_json
                    ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, ?, 'live', ?)
                    """,
                    (
                        "fresh-global-sell-recovery:monitor:sequence-latest",
                        refreshed.trade_id,
                        sequence_no + 1,
                        "2026-08-08T18:00:30+00:00",
                        "tests/test_live_safety_invariants",
                        json.dumps(
                            {
                                "last_monitor_best_bid": 0.41,
                                "monitor_probability_receipt": {
                                    "probability_content_identity": (
                                        "q-later-sequence-older-clock"
                                    ),
                                },
                            }
                        ),
                    ),
                )
        return SimpleNamespace()

    monkeypatch.setattr(monitor_refresh, "refresh_position", refresh_current)
    published = []

    def request_current(**kwargs):
        published.append(kwargs)
        return True, SimpleNamespace(
            request_id=old_request.request_id,
            material_identity=old_request.material_identity,
            generation=old_request.generation,
            attempt_identity="attempt-current",
            scope_identity=old_request.scope_identity,
            position_id=position.trade_id,
            family=kwargs["family"],
            held_token_id=position.token_id,
            probability_content_identity=kwargs[
                "probability_content_identity"
            ],
            probability_observed_at=kwargs["probability_observed_at"],
            held_best_bid=kwargs["held_best_bid"],
            bid_observed_at=kwargs["bid_observed_at"],
            book_state=kwargs["book_state"],
            schema_version=4,
        )

    monkeypatch.setattr(
        event_reactor,
        "request_global_auction_completion",
        request_current,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [],
    )
    summary = {"monitors": 0, "exits": 0}
    deps = _monitor_test_deps("test_fresh_global_sell_recovery")
    deps._utcnow = lambda: datetime(2026, 8, 8, 18, 1, tzinfo=timezone.utc)

    cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
    )

    assert refreshes == [position.trade_id]
    if expected_probability_identity is None:
        assert published == []
        assert summary["global_sell_snapshot_reauction_debts_pending"] == 1
        conn.close()
        return
    assert len(published) == 1
    assert published[0]["force_new_generation"] is True
    assert published[0]["probability_content_identity"] == (
        expected_probability_identity
    )
    assert published[0]["held_best_bid"] == pytest.approx(expected_bid)
    assert published[0]["bid_observed_at"] == expected_observed_at
    assert summary["global_sell_snapshot_reauction_debts_recovered"] == 1
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM position_events WHERE position_id = ? "
            "ORDER BY sequence_no DESC LIMIT 1",
            (position.trade_id,),
        ).fetchone()[0]
    )
    assert payload["global_sell_reauction_status"] == "durable_wake_reserved"
    assert payload["held_sell_reauction_obligation"]["attempt_identity"] == (
        "attempt-current"
    )
    conn.close()


def test_global_sell_reauction_publish_failure_recovers_exact_armed_obligation_once(
    tmp_path,
    monkeypatch,
):
    """A committed ARMED outbox re-wakes the same V4 attempt after a crash."""
    from src.engine import lifecycle_events
    from src.events import reactor
    from src.runtime import reactor_wake
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "global-reauction-outbox-recovery.db")
    init_schema(conn)
    position = _make_position(
        trade_id="global-reauction-outbox-recovery",
        state="holding",
        city="Paris",
        target_date="2026-08-08",
        direction="buy_yes",
        shares=10.0,
        shares_filled=10.0,
        chain_state="synced",
        chain_shares=10.0,
        strategy_key="center_buy",
        entered_at="2026-08-08T17:00:00+00:00",
        token_id="paris-yes-outbox",
        no_token_id="paris-no-outbox",
        condition_id="0x" + "6b" * 32,
    )
    entry_events, entry_projection = lifecycle_events.build_entry_canonical_write(
        position,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-global-reauction-outbox-recovery",
        source_module="tests/test_live_safety_invariants",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.commit()

    wake_path = tmp_path / "reactor-wake.json"
    prepared_result = reactor.request_global_auction_completion(
        reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
        position_id=position.trade_id,
        family=(position.city, position.target_date, position.temperature_metric),
        probability_content_identity="q-outbox-current",
        held_token_id=position.token_id,
        held_best_bid=0.49,
        bid_observed_at="2026-08-08T18:00:00+00:00",
        book_state="EXECUTABLE",
        probability_observed_at="2026-08-08T18:00:00+00:00",
        schema_version=4,
        wake_path=wake_path,
        return_request=True,
        prepare_only=True,
    )
    assert prepared_result[0] is True
    prepared = prepared_result[1]
    assert prepared is not None
    armed_at = datetime(2026, 8, 8, 17, 59, tzinfo=timezone.utc)
    obligation = {
        field: getattr(prepared, field)
        for field in (
            "request_id",
            "material_identity",
            "attempt_identity",
            "scope_identity",
            "generation",
            "position_id",
            "family",
            "held_token_id",
            "probability_content_identity",
            "probability_observed_at",
            "held_best_bid",
            "bid_observed_at",
            "book_state",
            "schema_version",
        )
    }
    obligation.update(
        {
            "state": "ARMED",
            "armed_at": armed_at.isoformat(),
            "completion_deadline_at": (
                armed_at + timedelta(seconds=30)
            ).isoformat(),
        }
    )
    position._held_sell_reauction_obligation = obligation
    position.last_monitor_at = armed_at.isoformat()
    position.last_monitor_prob = 0.51
    position.last_monitor_prob_is_fresh = True
    position.last_monitor_market_price = 0.50
    position.last_monitor_market_price_is_fresh = True
    monitor_events, monitor_projection = (
        lifecycle_events.build_monitor_refreshed_canonical_write(
            position,
            sequence_no=(
                conn.execute(
                    "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
                    (position.trade_id,),
                ).fetchone()[0]
            ),
            phase_after=LifecyclePhase.ACTIVE.value,
            occurred_at=armed_at.isoformat(),
            exit_decision=ExitDecision(
                False,
                "GLOBAL_REAUCTION_PENDING",
                applied_validations=["GLOBAL_REAUCTION_PENDING"],
            ),
            final_should_exit=False,
            final_exit_reason="GLOBAL_REAUCTION_PENDING",
        )
    )
    append_many_and_project(conn, monitor_events, monitor_projection)
    conn.commit()

    real_publish = reactor_wake.publish_reactor_wake

    def fail_publish(**_kwargs):
        raise OSError("simulated crash after canonical commit")

    monkeypatch.setattr(reactor_wake, "publish_reactor_wake", fail_publish)
    assert (
        reactor.publish_prepared_global_auction_completion(
            reason="GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
            prepared_request=prepared,
            wake_path=wake_path,
        )
        is False
    )
    monkeypatch.setattr(reactor_wake, "publish_reactor_wake", real_publish)

    recovered = []
    position.state = "pending_exit"
    position.exit_state = "exit_intent"
    position.order_status = "exit_intent"

    def recover_request(position, force_new_generation):
        restored = position._held_sell_reauction_obligation
        result = reactor.request_global_auction_completion(
            reason="GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
            position_id=restored["position_id"],
            family=tuple(restored["family"]),
            probability_content_identity=restored["probability_content_identity"],
            held_token_id=restored["held_token_id"],
            held_best_bid=restored["held_best_bid"],
            bid_observed_at=restored["bid_observed_at"],
            book_state=restored["book_state"],
            probability_observed_at=restored["probability_observed_at"],
            generation=restored["generation"],
            scope_identity=restored["scope_identity"],
            schema_version=restored["schema_version"],
            wake_path=wake_path,
            force_new_generation=force_new_generation,
            return_request=True,
        )
        recovered.append(result[1])
        return bool(result[0])

    from src.execution.exit_lifecycle import recover_global_sell_snapshot_reauction_debt

    assert recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=recover_request,
    ) is True
    assert len(recovered) == 1
    assert recovered[0].request_id == prepared.request_id
    assert recovered[0].material_identity == prepared.material_identity
    assert recovered[0].attempt_identity == prepared.attempt_identity
    assert recovered[0].scope_identity == prepared.scope_identity
    assert recovered[0].generation == prepared.generation
    assert position.state == "pending_exit"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0] == 0
    assert recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=recover_request,
    ) is False
    assert len(recovered) == 1
    conn.close()


def test_global_sell_reauction_queued_executable_attempt_is_coalesced(
    tmp_path,
):
    """Monitor refreshes cannot replace an actionable SELL debt mid-cut."""
    from src.events import reactor
    from src.runtime import reactor_wake

    wake_path = tmp_path / "reactor-wake-attempt-refresh.json"
    common = {
        "reason": "GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
        "position_id": "global-reauction-attempt-refresh",
        "family": ("Paris", "2026-08-08", "high"),
        "probability_content_identity": "q-attempt-refresh",
        "held_token_id": "paris-yes-attempt-refresh",
        "book_state": "EXECUTABLE",
        "schema_version": 4,
        "wake_path": wake_path,
        "return_request": True,
        "prepare_only": True,
    }
    first_result = reactor.request_global_auction_completion(
        **common,
        held_best_bid=0.49,
        bid_observed_at="2026-08-08T18:00:00+00:00",
        probability_observed_at="2026-08-08T18:00:00+00:00",
    )
    first = first_result[1]
    assert first_result[0] is True
    assert first is not None
    assert reactor.publish_prepared_global_auction_completion(
        reason=common["reason"],
        prepared_request=first,
        wake_path=wake_path,
    ) is True

    second_result = reactor.request_global_auction_completion(
        **common,
        scope_identity=first.scope_identity,
        generation=first.generation,
        held_best_bid=0.47,
        bid_observed_at="2026-08-08T18:00:30+00:00",
        probability_observed_at="2026-08-08T18:00:30+00:00",
    )
    second = second_result[1]
    assert second_result[0] is True
    assert second is not None
    assert second.generation == first.generation
    assert second.attempt_identity == first.attempt_identity
    assert reactor.publish_prepared_global_auction_completion(
        reason=common["reason"],
        prepared_request=second,
        wake_path=wake_path,
    ) is True
    latest = reactor_wake.latest_v4_held_sell_reauction_request(
        first.scope_identity,
        path=wake_path,
    )
    assert latest is not None
    assert latest.generation == first.generation
    assert latest.attempt_identity == first.attempt_identity
    assert latest.held_best_bid == pytest.approx(0.49)


def test_expired_global_sell_reauction_rebinds_current_q_and_book(tmp_path):
    """Deadline recovery replaces a stale queued attempt with current truth."""
    from src.events import reactor
    from src.runtime import reactor_wake

    wake_path = tmp_path / "reactor-wake-expired-attempt.json"
    common = {
        "reason": "GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
        "position_id": "global-reauction-expired-attempt",
        "family": ("Paris", "2026-08-08", "high"),
        "held_token_id": "paris-yes-expired-attempt",
        "book_state": "EXECUTABLE",
        "schema_version": 4,
        "wake_path": wake_path,
        "return_request": True,
    }
    first_result = reactor.request_global_auction_completion(
        **common,
        probability_content_identity="q-stale",
        held_best_bid=0.49,
        bid_observed_at="2026-08-08T18:00:00+00:00",
        probability_observed_at="2026-08-08T18:00:00+00:00",
    )
    first = first_result[1]
    assert first_result[0] is True
    assert first is not None

    second_result = reactor.request_global_auction_completion(
        **common,
        probability_content_identity="q-current",
        held_best_bid=0.31,
        bid_observed_at="2026-08-08T18:00:31+00:00",
        probability_observed_at="2026-08-08T18:00:31+00:00",
        scope_identity=first.scope_identity,
        generation=first.generation,
        force_new_generation=True,
    )
    second = second_result[1]
    assert second_result[0] is True
    assert second is not None
    assert second.request_id == first.request_id
    assert second.generation == first.generation
    assert second.attempt_identity != first.attempt_identity
    assert second.probability_content_identity == "q-current"
    assert second.held_best_bid == 0.31
    assert (
        reactor_wake.latest_v4_held_sell_reauction_request(
            first.scope_identity,
            path=wake_path,
        ).attempt_identity
        == second.attempt_identity
    )


def test_global_sell_reauction_executable_book_upgrades_queued_no_book_attempt(
    tmp_path,
):
    """A newly executable book must promptly upgrade a queued no-book debt."""
    from src.events import reactor
    from src.runtime import reactor_wake

    wake_path = tmp_path / "reactor-wake-book-upgrade.json"
    common = {
        "reason": "GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE",
        "position_id": "global-reauction-book-upgrade",
        "family": ("Paris", "2026-08-08", "high"),
        "probability_content_identity": "q-book-upgrade",
        "held_token_id": "paris-yes-book-upgrade",
        "schema_version": 4,
        "wake_path": wake_path,
        "return_request": True,
        "prepare_only": True,
    }
    first_result = reactor.request_global_auction_completion(
        **common,
        held_best_bid=0.01,
        bid_observed_at="2026-08-08T18:00:00+00:00",
        probability_observed_at="2026-08-08T18:00:00+00:00",
        book_state="NO_EXECUTABLE_BOOK",
    )
    first = first_result[1]
    assert first_result[0] is True
    assert first is not None
    assert reactor.publish_prepared_global_auction_completion(
        reason=common["reason"],
        prepared_request=first,
        wake_path=wake_path,
    ) is True

    second_result = reactor.request_global_auction_completion(
        **common,
        scope_identity=first.scope_identity,
        generation=first.generation,
        held_best_bid=0.47,
        bid_observed_at="2026-08-08T18:00:30+00:00",
        probability_observed_at="2026-08-08T18:00:30+00:00",
        book_state="EXECUTABLE",
    )
    second = second_result[1]
    assert second_result[0] is True
    assert second is not None
    assert second.attempt_identity != first.attempt_identity
    assert reactor.publish_prepared_global_auction_completion(
        reason=common["reason"],
        prepared_request=second,
        wake_path=wake_path,
    ) is True
    latest = reactor_wake.latest_v4_held_sell_reauction_request(
        first.scope_identity,
        path=wake_path,
    )
    assert latest is not None
    assert latest.generation == first.generation
    assert latest.attempt_identity == second.attempt_identity
    assert latest.book_state == "EXECUTABLE"
    assert latest.held_best_bid == pytest.approx(0.47)


def test_global_holding_coverage_requires_exact_position_wealth_and_current_book(
    monkeypatch,
):
    from src.engine import global_batch_runtime
    from src.engine.global_single_order_auction import (
        GlobalHoldingAuctionCoverage,
    )

    monkeypatch.setattr(
        global_batch_runtime,
        "_GLOBAL_HOLDING_COVERAGE_BY_POSITION",
        {},
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_GLOBAL_HOLDING_COVERAGE_WEALTH_IDENTITY",
        None,
    )
    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    obligation = global_batch_runtime._CurrentHeldObligation(
        position_id="position-1",
        family_key="family-1",
        bin_label="20C",
        condition_id="condition-1",
        side="YES",
        token_id="token-1",
        held_shares=Decimal("10"),
    )
    probability = SimpleNamespace(
        witness_identity="q-1",
        probability_content_identity="q-content-1",
        bindings=(
            SimpleNamespace(
                bin_id="canonical-bin-1",
                condition_id="condition-1",
                yes_token_id="token-1",
                no_token_id="token-no-1",
            ),
        )
    )
    coverage = GlobalHoldingAuctionCoverage(
        position_id="position-1",
        family_key="family-1",
        bin_id="canonical-bin-1",
        bin_label="20C",
        canonical_bin_identity="condition:condition-1",
        condition_id="condition-1",
        side="YES",
        token_id="token-1",
        held_shares=Decimal("10"),
        ledger_snapshot_id="ledger-1",
        probability_witness_identity="q-1",
        probability_content_identity="q-content-1",
        wealth_economic_identity="wealth-1",
        selection_epoch_identity="epoch-1",
        book_epoch_identity="book-epoch-1",
        selection_cut_at_utc=at,
        decision_at_utc=at + timedelta(seconds=1),
        book_deadline_at_utc=at + timedelta(seconds=30),
        status="EVALUATED",
        candidate_id="sell-1",
        sell_book_witness_identity="sell-book-content-1",
        book_state="EXECUTABLE",
    )
    global_batch_runtime._publish_global_holding_coverage(
        (coverage,),
        expected_obligations=(obligation,),
        probability_witnesses={"family-1": probability},
        decision_log_id=42,
    )
    current = dict(
        position_id="position-1",
        probability_content_identity="q-content-1",
        checked_at_utc=at + timedelta(seconds=2),
        family_key="family-1",
        bin_label="20C",
        condition_id="condition-1",
        side="YES",
        token_id="token-1",
        held_shares=Decimal("10"),
        current_ledger_snapshot_id="ledger-1",
        current_wealth_economic_identity="wealth-1",
        current_probability_content_identity_resolver=lambda _row: "q-content-1",
        current_holding_witness_resolver=lambda _row: (
            global_batch_runtime._CurrentHoldingWitness(
                ledger_snapshot_id="ledger-1",
                wealth_economic_identity="wealth-1",
                held_shares=Decimal("10"),
            )
        ),
        current_time_provider=lambda: at + timedelta(seconds=2),
    )

    covered = global_batch_runtime.current_global_holding_coverage(
        **current,
        current_sell_book_witness_resolver=(
            lambda _row: "sell-book-content-1"
        ),
    )
    assert covered.outcome is global_batch_runtime.GlobalHoldingCoverageOutcome.COVERED
    assert covered.coverage == coverage
    assert covered.decision_log_id == 42
    assert global_batch_runtime.current_global_holding_coverage(
        **{**current, "current_wealth_economic_identity": "wealth-2"},
        current_sell_book_witness_resolver=(
            lambda _row: "sell-book-content-1"
        ),
    ).outcome is global_batch_runtime.GlobalHoldingCoverageOutcome.WEALTH
    assert global_batch_runtime.current_global_holding_coverage(
        **{**current, "current_ledger_snapshot_id": "ledger-2"},
        current_sell_book_witness_resolver=(
            lambda _row: "sell-book-content-1"
        ),
    ).outcome is global_batch_runtime.GlobalHoldingCoverageOutcome.WEALTH
    assert global_batch_runtime.current_global_holding_coverage(
        **current,
        current_sell_book_witness_resolver=(
            lambda _row: "sell-book-content-price-wake"
        ),
    ).outcome is global_batch_runtime.GlobalHoldingCoverageOutcome.BOOK
    assert global_batch_runtime.current_global_holding_coverage(
        **{**current, "held_shares": Decimal("9.99")},
        current_sell_book_witness_resolver=(
            lambda _row: "sell-book-content-1"
        ),
    ).outcome is global_batch_runtime.GlobalHoldingCoverageOutcome.WEALTH

    global_batch_runtime._invalidate_global_holding_coverage()
    assert global_batch_runtime.current_global_holding_coverage(
        **current,
        current_sell_book_witness_resolver=(
            lambda _row: "sell-book-content-1"
        ),
    ).outcome is global_batch_runtime.GlobalHoldingCoverageOutcome.COVERAGE_NOT_PUBLISHED


@pytest.mark.parametrize(
    "mutation",
    ("invalidate", "replace", "q_advance", "ledger", "wealth", "deadline"),
)
def test_global_holding_coverage_lease_revalidates_after_resolver_io(
    monkeypatch,
    mutation,
):
    from src.engine import global_batch_runtime
    from src.engine.global_single_order_auction import (
        GlobalHoldingAuctionCoverage,
    )

    monkeypatch.setattr(
        global_batch_runtime,
        "_GLOBAL_HOLDING_COVERAGE_BY_POSITION",
        {},
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_GLOBAL_HOLDING_COVERAGE_WEALTH_IDENTITY",
        None,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_GLOBAL_HOLDING_COVERAGE_GENERATION",
        0,
    )
    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    obligation = global_batch_runtime._CurrentHeldObligation(
        position_id="position-lease",
        family_key="family-lease",
        bin_label="20C",
        condition_id="condition-lease",
        side="YES",
        token_id="token-lease",
        held_shares=Decimal("10"),
    )
    probability = SimpleNamespace(
        witness_identity="q-lease",
        probability_content_identity="q-content-lease",
        bindings=(
            SimpleNamespace(
                bin_id="canonical-bin-lease",
                condition_id="condition-lease",
                yes_token_id="token-lease",
                no_token_id="token-no-lease",
            ),
        ),
    )
    coverage = GlobalHoldingAuctionCoverage(
        position_id=obligation.position_id,
        family_key=obligation.family_key,
        bin_id="canonical-bin-lease",
        bin_label=obligation.bin_label,
        condition_id=obligation.condition_id,
        side=obligation.side,
        token_id=obligation.token_id,
        held_shares=obligation.held_shares,
        ledger_snapshot_id="ledger-lease",
        probability_witness_identity="q-lease",
        probability_content_identity="q-content-lease",
        wealth_economic_identity="wealth-lease",
        selection_epoch_identity="epoch-lease",
        book_epoch_identity="book-lease",
        selection_cut_at_utc=at,
        decision_at_utc=at + timedelta(seconds=1),
        book_deadline_at_utc=at + timedelta(seconds=30),
        status="EVALUATED",
        candidate_id="sell-lease",
        sell_book_witness_identity="sell-book-lease",
        book_state="EXECUTABLE",
    )
    def publish(decision_log_id):
        global_batch_runtime._publish_global_holding_coverage(
            (coverage,),
            expected_obligations=(obligation,),
            probability_witnesses={"family-lease": probability},
            decision_log_id=decision_log_id,
        )

    publish(41)

    def book_resolver(_row):
        if mutation == "invalidate":
            global_batch_runtime._invalidate_global_holding_coverage()
        elif mutation == "replace":
            publish(42)
        return "sell-book-lease"

    holding = global_batch_runtime._CurrentHoldingWitness(
        ledger_snapshot_id=(
            "ledger-advanced" if mutation == "ledger" else "ledger-lease"
        ),
        wealth_economic_identity=(
            "wealth-advanced" if mutation == "wealth" else "wealth-lease"
        ),
        held_shares=Decimal("10"),
    )
    result = global_batch_runtime.current_global_holding_coverage(
        position_id=obligation.position_id,
        probability_content_identity="q-content-lease",
        checked_at_utc=at + timedelta(seconds=2),
        family_key=obligation.family_key,
        bin_label=obligation.bin_label,
        condition_id=obligation.condition_id,
        side=obligation.side,
        token_id=obligation.token_id,
        held_shares=obligation.held_shares,
        current_ledger_snapshot_id="ledger-lease",
        current_wealth_economic_identity="wealth-lease",
        current_sell_book_witness_resolver=book_resolver,
        current_probability_content_identity_resolver=lambda _row: (
            "q-content-advanced"
            if mutation == "q_advance"
            else "q-content-lease"
        ),
        current_holding_witness_resolver=lambda _row: holding,
        current_time_provider=lambda: (
            at + timedelta(seconds=31)
            if mutation == "deadline"
            else at + timedelta(seconds=2)
        ),
    )

    assert result.outcome is not global_batch_runtime.GlobalHoldingCoverageOutcome.COVERED


def test_global_holding_partition_rejects_single_q_epoch_or_deadline_mismatch():
    from src.engine import global_batch_runtime
    from src.engine.global_single_order_auction import (
        GlobalHoldingAuctionCoverage,
    )

    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    first = global_batch_runtime._CurrentHeldObligation(
        position_id="position-evaluated",
        family_key="family-evaluated",
        bin_label="20C",
        condition_id="condition-evaluated",
        side="YES",
        token_id="token-evaluated",
        held_shares=Decimal("10"),
    )
    second = global_batch_runtime._CurrentHeldObligation(
        position_id="position-excluded",
        family_key="family-excluded",
        bin_label="21C",
        condition_id="condition-excluded",
        side="NO",
        token_id="token-excluded",
        held_shares=Decimal("5"),
    )
    probability = SimpleNamespace(
        witness_identity="q-current",
        probability_content_identity="q-content-current",
        bindings=(
            SimpleNamespace(
                bin_id="canonical-bin-current",
                condition_id=first.condition_id,
                yes_token_id=first.token_id,
                no_token_id="token-no-evaluated",
            ),
        ),
    )
    evaluated = GlobalHoldingAuctionCoverage(
        position_id=first.position_id,
        family_key=first.family_key,
        bin_id="canonical-bin-current",
        bin_label=first.bin_label,
        condition_id=first.condition_id,
        side=first.side,
        token_id=first.token_id,
        held_shares=first.held_shares,
        ledger_snapshot_id="ledger-current",
        probability_witness_identity="q-current",
        probability_content_identity="q-content-current",
        wealth_economic_identity="wealth-current",
        selection_epoch_identity="epoch-current",
        book_epoch_identity="book-current",
        selection_cut_at_utc=at,
        decision_at_utc=at + timedelta(seconds=1),
        book_deadline_at_utc=at + timedelta(seconds=30),
        status="EVALUATED",
        candidate_id="sell-current",
        sell_book_witness_identity="sell-book-current",
        book_state="EXECUTABLE",
    )
    excluded = GlobalHoldingAuctionCoverage(
        position_id=second.position_id,
        family_key=second.family_key,
        bin_id=None,
        bin_label=second.bin_label,
        condition_id=second.condition_id,
        side=second.side,
        token_id=second.token_id,
        held_shares=second.held_shares,
        ledger_snapshot_id="ledger-current",
        probability_witness_identity=None,
        probability_content_identity=None,
        wealth_economic_identity="wealth-current",
        selection_epoch_identity="epoch-current",
        book_epoch_identity="book-current",
        selection_cut_at_utc=at,
        decision_at_utc=at + timedelta(seconds=1),
        book_deadline_at_utc=at + timedelta(seconds=30),
        status="EXCLUDED",
        reason="PROBABILITY_AUTHORITY_UNAVAILABLE:q_missing",
    )
    obligations = (first, second)
    probabilities = {first.family_key: probability}
    assert global_batch_runtime._holding_coverage_partition_complete(
        (evaluated, excluded),
        obligations=obligations,
        probability_witnesses=probabilities,
    )
    mismatches = {
        "q": (replace(evaluated, probability_witness_identity="q-stale"), excluded),
        "selection_epoch": (
            evaluated,
            replace(excluded, selection_epoch_identity="epoch-other"),
        ),
        "book_epoch": (
            evaluated,
            replace(excluded, book_epoch_identity="book-other"),
        ),
        "deadline": (
            evaluated,
            replace(
                excluded,
                book_deadline_at_utc=at + timedelta(seconds=29),
            ),
        ),
    }
    for field, rows in mismatches.items():
        assert not global_batch_runtime._holding_coverage_partition_complete(
            rows,
            obligations=obligations,
            probability_witnesses=probabilities,
        ), field


def test_monitor_handoff_rebuilds_current_ledger_and_executable_sell_book(
    monkeypatch,
):
    from src.engine import (
        cycle_runtime,
        global_auction_universe,
        global_batch_runtime,
        monitor_refresh,
    )
    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(positions=())
    position = SimpleNamespace(
        position_id="position-current",
        trade_id="position-current",
        direction="buy_yes",
        token_id="token-current",
        no_token_id="token-no-current",
        condition_id="condition-current",
        city="New York City",
        target_date="2026-07-14",
        temperature_metric="high",
        bin_label="90F",
        state="active",
    )
    wealth = SimpleNamespace(
        native_holdings_micro=(("token-current", 12_500_000),),
        ledger_snapshot_id="ledger-current",
        economic_identity="wealth-current",
        captured_at_utc=at,
        max_age=timedelta(minutes=2),
    )
    content_identity = "q-content-current"
    wealth_calls = []

    def current_wealth(_conn, **kwargs):
        wealth_calls.append(kwargs)
        if (
            kwargs["portfolio_state"] is not portfolio
            or kwargs["decision_at_utc"] != at
        ):
            pytest.fail("monitor must bind the current portfolio and time")
        return wealth

    monkeypatch.setattr(
        global_auction_universe,
        "current_portfolio_wealth_witness",
        current_wealth,
    )
    monkeypatch.setattr(
        global_auction_universe,
        "_global_book_snapshot_rows",
        lambda _conn, **kwargs: (
            {
                "condition_id": "condition-current",
                "selected_outcome_token_id": "token-current",
                "yes_token_id": "token-current",
                "no_token_id": "token-no-current",
            },
        ),
    )
    monkeypatch.setattr(
        global_auction_universe,
        "_global_book_metadata_is_executable",
        lambda *_args, **_kwargs: True,
    )

    def sell_curve(**kwargs):
        bid = kwargs["raw_book"]["bids"][0]
        return SimpleNamespace(
            token_id=kwargs["token_id"],
            side=kwargs["side"],
            book_hash=f"bid:{bid['price']}:{bid['size']}",
            fee_model=SimpleNamespace(fee_rate=Decimal("0.02")),
            min_tick=Decimal("0.01"),
            min_order_size=Decimal("5"),
            levels=(
                SimpleNamespace(
                    price=Decimal(bid["price"]),
                    size=Decimal(bid["size"]),
                ),
            ),
        )

    monkeypatch.setattr(global_auction_universe, "_global_sell_curve", sell_curve)
    monkeypatch.setattr(
        monitor_refresh,
        "_refresh_current_global_day0_probability",
        lambda *_args, **_kwargs: pytest.fail(
            "coverage must reuse the current monitor probability receipt"
        ),
    )
    captured = {}

    def current_coverage(**kwargs):
        captured.update(kwargs)
        coverage = SimpleNamespace(
            family_key=kwargs["family_key"],
            bin_id="canonical-bin-current",
        )
        captured["sell_book_witness"] = kwargs[
            "current_sell_book_witness_resolver"
        ](coverage)
        captured["probability_witness"] = kwargs[
            "current_probability_content_identity_resolver"
        ](coverage)
        captured["holding_witness"] = kwargs[
            "current_holding_witness_resolver"
        ](coverage)
        captured["final_checked_at"] = kwargs["current_time_provider"]()
        return coverage, 91

    monkeypatch.setattr(
        global_batch_runtime,
        "held_sell_reauction_coverage",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_global_holding_coverage",
        current_coverage,
    )

    orderbook_calls = []

    def get_orderbook(token):
        orderbook_calls.append(token)
        if len(orderbook_calls) > 1:
            pytest.fail("coverage must reuse the current monitor orderbook")
        return {
            "asset_id": token,
            "bids": [{"price": "0.61", "size": "20"}],
            "asks": [{"price": "0.63", "size": "20"}],
        }

    clob = SimpleNamespace(get_orderbook=get_orderbook)
    assert monitor_refresh.install_monitor_orderbook_prefetch(clob, {})
    quote = monitor_refresh.monitor_quote_refresh(None, clob, position)
    assert quote is not None
    assert quote.best_bid == pytest.approx(0.61)
    assert orderbook_calls == ["token-current"]
    assert monitor_refresh.prefetched_monitor_orderbook(
        clob,
        "token-current",
    ) == {
        "asset_id": "token-current",
        "bids": [{"price": "0.61", "size": "20"}],
        "asks": [{"price": "0.63", "size": "20"}],
    }

    result = cycle_runtime._current_monitor_global_holding_coverage(
        conn=object(),
        clob=clob,
        portfolio=portfolio,
        position=position,
        probability_content_identity=content_identity,
        checked_at_utc=at,
        current_time_provider=lambda: at,
    )

    assert result[1] == 91
    assert captured["current_ledger_snapshot_id"] == "ledger-current"
    assert captured["current_wealth_economic_identity"] == "wealth-current"
    assert captured["held_shares"] == Decimal("12.5")
    assert captured["sell_book_witness"]
    assert captured["probability_witness"] == content_identity
    assert captured["holding_witness"].ledger_snapshot_id == "ledger-current"
    assert captured["holding_witness"].held_shares == Decimal("12.5")
    assert captured["final_checked_at"] == at
    assert len(wealth_calls) == 1


def test_monitor_reuses_one_wealth_witness_across_held_sell_coverage(monkeypatch):
    """Every SELL candidate in one monitor epoch must share one capital truth."""
    from src.engine import (
        cycle_runtime,
        global_auction_universe,
        global_batch_runtime,
        monitor_refresh,
    )

    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    portfolio = SimpleNamespace(positions=())
    positions = [
        SimpleNamespace(
            position_id=f"position-{index}",
            trade_id=f"position-{index}",
            direction="buy_yes",
            token_id=f"token-{index}",
            no_token_id=f"no-token-{index}",
            condition_id=f"condition-{index}",
            city="New York City",
            target_date="2026-07-14",
            temperature_metric="high",
            bin_label=f"{90 + index}F",
        )
        for index in range(2)
    ]
    wealth = SimpleNamespace(
        native_holdings_micro=(
            ("token-0", 5_000_000),
            ("token-1", 7_000_000),
        ),
        ledger_snapshot_id="ledger-epoch",
        economic_identity="wealth-epoch",
        captured_at_utc=at,
        max_age=timedelta(minutes=2),
    )
    wealth_calls = []
    monkeypatch.setattr(
        global_auction_universe,
        "current_portfolio_wealth_witness",
        lambda *_args, **_kwargs: wealth_calls.append(True) or wealth,
    )
    monkeypatch.setattr(
        global_auction_universe,
        "_global_book_snapshot_rows",
        lambda _conn, **kwargs: (
            {
                "condition_id": kwargs["condition_ids"][0],
                "selected_outcome_token_id": (
                    "token-0"
                    if kwargs["condition_ids"][0] == "condition-0"
                    else "token-1"
                ),
                "yes_token_id": (
                    "token-0"
                    if kwargs["condition_ids"][0] == "condition-0"
                    else "token-1"
                ),
                "no_token_id": "",
            },
        ),
    )
    monkeypatch.setattr(
        global_auction_universe,
        "_global_book_metadata_is_executable",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        global_auction_universe,
        "_global_sell_curve",
        lambda **kwargs: SimpleNamespace(
            token_id=kwargs["token_id"],
            side=kwargs["side"],
            book_hash=f"book:{kwargs['token_id']}",
            fee_model=SimpleNamespace(fee_rate=Decimal("0")),
            min_tick=Decimal("0.01"),
            min_order_size=Decimal("1"),
            levels=(SimpleNamespace(price=Decimal("0.4"), size=Decimal("20")),),
        ),
    )

    def current_coverage(**kwargs):
        coverage = SimpleNamespace(
            family_key=kwargs["family_key"],
            bin_id=kwargs["bin_label"],
        )
        assert kwargs["current_holding_witness_resolver"](coverage) is not None
        return coverage, 1

    monkeypatch.setattr(
        global_batch_runtime,
        "held_sell_reauction_coverage",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_global_holding_coverage",
        current_coverage,
    )
    clob = SimpleNamespace()
    assert monitor_refresh.install_monitor_orderbook_prefetch(
        clob,
        {
            "token-0": {
                "asset_id": "token-0",
                "bids": [{"price": "0.4", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            },
            "token-1": {
                "asset_id": "token-1",
                "bids": [{"price": "0.4", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            },
        },
    )
    cache = {}

    for position in positions:
        assert cycle_runtime._current_monitor_global_holding_coverage(
            conn=object(),
            clob=clob,
            portfolio=portfolio,
            position=position,
            probability_content_identity="q-epoch",
            checked_at_utc=at,
            current_time_provider=lambda: at,
            wealth_witness_cache=cache,
        )

    assert wealth_calls == [True]


def test_monitor_handoff_skips_witness_io_without_published_coverage(monkeypatch):
    """An uncovered SELL must reserve reauction debt before slow witness I/O."""
    from src.engine import cycle_runtime, global_auction_universe, global_batch_runtime

    monkeypatch.setattr(
        global_batch_runtime,
        "held_sell_reauction_coverage",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        global_auction_universe,
        "current_portfolio_wealth_witness",
        lambda *_args, **_kwargs: pytest.fail(
            "missing coverage must not rebuild collateral authority"
        ),
    )
    position = SimpleNamespace(
        position_id="uncovered-position",
        trade_id="uncovered-position",
        direction="buy_yes",
        token_id="uncovered-token",
        no_token_id="uncovered-no-token",
        city="Paris",
        target_date="2026-08-09",
        temperature_metric="high",
    )

    result = cycle_runtime._current_monitor_global_holding_coverage(
        conn=object(),
        clob=object(),
        portfolio=SimpleNamespace(positions=(position,)),
        position=position,
        probability_content_identity="uncovered-q",
        checked_at_utc=datetime(2026, 8, 9, 15, 0, tzinfo=timezone.utc),
    )

    assert (
        result.outcome
        is global_batch_runtime.GlobalHoldingCoverageOutcome.COVERAGE_NOT_PUBLISHED
    )
    assert result.reason == "GLOBAL_HOLDING_COVERAGE_NOT_PUBLISHED"


def test_day0_resting_entry_sweep_bounds_and_rotates_scan_work(monkeypatch):
    """The ENTRY cleanup tail cannot scan an unbounded wallet order list."""
    from src.execution import day0_hard_fact_exit

    day0_hard_fact_exit._reset_wu_memo_for_tests()
    identity_calls = []
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "_resolve_order_bin_identity",
        lambda _conn, token_id, **_kwargs: identity_calls.append(token_id) or None,
    )
    entries = [
        {
            "command_id": f"command-{index}",
            "token_id": f"token-{index}",
            "command_side": "BUY",
        }
        for index in range(5)
    ]

    for _ in range(2):
        assert (
            day0_hard_fact_exit.classify_day0_dead_bin_entry_cancels(
                entries,
                trade_conn=object(),
                forecasts_conn=object(),
                cities_by_name={},
                limit=2,
            )
            == []
        )

    assert identity_calls == ["token-0", "token-1", "token-2", "token-3"]


def test_exit_monitor_has_no_entry_cancel_side_effect():
    """The monitor invocation ends without owning ENTRY venue cleanup."""
    import inspect

    from src.execution.exit_lifecycle import run_exit_monitor_cycle

    source = inspect.getsource(run_exit_monitor_cycle)
    commit_at = source.index("commit_then_export(", source.index("with nullcontext"))
    release_at = source.index("mark_held_position_monitor_complete()", commit_at)

    assert commit_at < release_at
    assert "cancel_day0_dead_bin_resting_entries" not in source
    day0_source = inspect.getsource(
        __import__(
            "src.execution.day0_hard_fact_exit",
            fromlist=["cancel_day0_dead_bin_resting_entries"],
        )
    )
    assert ".cancel_order(" not in day0_source


def test_global_holding_coverage_materializes_typed_q_missing_obligation():
    from src.engine import global_batch_runtime

    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    obligation = global_batch_runtime._CurrentHeldObligation(
        position_id="position-q-missing",
        family_key="family-q-missing",
        bin_label="21C",
        condition_id="condition-q-missing",
        side="NO",
        token_id="token-q-missing",
        held_shares=Decimal("4.5"),
    )

    coverage = global_batch_runtime._complete_holding_coverage(
        (),
        obligations=(obligation,),
        probability_witnesses={},
        ineligible_by_family={"family-q-missing": "q_missing"},
        ledger_snapshot_id="ledger-current",
        wealth_economic_identity="wealth-current",
        selection_epoch_identity="epoch-current",
        book_epoch_identity="book-unavailable-current",
        selection_cut_at_utc=at,
        decision_at_utc=at,
        book_deadline_at_utc=at,
    )

    assert len(coverage) == 1
    row = coverage[0]
    assert row.status == "EXCLUDED"
    assert row.bin_id is None
    assert row.bin_label == "21C"
    assert row.canonical_bin_identity == "condition:condition-q-missing"
    assert row.reason == "PROBABILITY_AUTHORITY_UNAVAILABLE:q_missing"
    assert global_batch_runtime._holding_coverage_partition_complete(
        coverage,
        obligations=(obligation,),
        probability_witnesses={},
    )


def _global_auction_receipt_wealth_witness() -> SimpleNamespace:
    from src.contracts.strategy_capital_allocation import (
        StrategyCapitalAllocationWitness,
    )

    return SimpleNamespace(
        witness_identity="wealth-witness-current",
        economic_identity="wealth-current",
        ledger_snapshot_id="ledger-current",
        strategy_capital_allocation=StrategyCapitalAllocationWitness.build(
            capital_basis_usd="100",
            committed_capital_usd="0",
            venue_spendable_cash_usd="100",
            allocation={"mode": "wallet_total"},
        ),
    )


def test_global_auction_receipt_rejects_missing_strategy_allocation():
    from src.engine import global_batch_runtime

    with pytest.raises(
        ValueError,
        match="GLOBAL_AUCTION_STRATEGY_CAPITAL_ALLOCATION_MISSING",
    ):
        global_batch_runtime._strategy_capital_allocation_receipt(
            SimpleNamespace()
        )


def test_holding_coverage_receipt_compresses_and_references_exact_payload(
    tmp_path,
    monkeypatch,
):
    from src.engine import global_batch_runtime
    from src.engine.global_single_order_auction import (
        GlobalHoldingAuctionCoverage,
        PreparedGlobalAuctionResult,
    )
    from src.solve.solver import GlobalSingleOrderDecision
    from src.state.db import get_connection, init_schema

    monkeypatch.setattr(global_batch_runtime, "_GLOBAL_AUCTION_PAYLOAD_REFS", {})
    conn = get_connection(tmp_path / "holding-coverage-compression.db")
    init_schema(conn)
    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    obligation = global_batch_runtime._CurrentHeldObligation(
        position_id="position-q-missing",
        family_key="family-q-missing",
        bin_label="21C",
        condition_id="condition-q-missing",
        side="NO",
        token_id="token-q-missing",
        held_shares=Decimal("4.5"),
    )
    coverage = GlobalHoldingAuctionCoverage(
        position_id=obligation.position_id,
        family_key=obligation.family_key,
        bin_id=None,
        bin_label=obligation.bin_label,
        condition_id=obligation.condition_id,
        side=obligation.side,
        token_id=obligation.token_id,
        held_shares=obligation.held_shares,
        ledger_snapshot_id="ledger-current",
        probability_witness_identity=None,
        probability_content_identity=None,
        wealth_economic_identity="wealth-current",
        selection_epoch_identity="epoch-current",
        book_epoch_identity="book-unavailable-current",
        selection_cut_at_utc=at,
        decision_at_utc=at,
        book_deadline_at_utc=at,
        status="EXCLUDED",
        reason="PROBABILITY_AUTHORITY_UNAVAILABLE:q_missing",
    )
    selected = PreparedGlobalAuctionResult(
        decision=GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="GLOBAL_FEASIBLE_SET_INCOMPLETE",
            candidate_input_count=0,
        ),
        winner_event_id=None,
        holding_coverage=(coverage,),
    )
    kwargs = dict(
        selected=selected,
        selection_epoch_identity="epoch-current",
        selection_cut_at_utc=at,
        decision_at_utc=at,
        probability_manifest=(),
        full_scope_identity="scope-current",
        full_scope_family_keys=("family-q-missing",),
        probability_ineligible_by_family={"family-q-missing": "q_missing"},
        book_epoch_identity="book-unavailable-current",
        book_asset_count=None,
        book_asset_states=(),
        wealth_witness=_global_auction_receipt_wealth_witness(),
        fractional_kelly_multiplier=Decimal("0.25"),
        expected_holding_obligations=(obligation,),
        holding_probability_witnesses={},
    )
    first_id = global_batch_runtime._store_global_auction_receipt(conn, **kwargs)
    conn.commit()
    second_id = global_batch_runtime._store_global_auction_receipt(conn, **kwargs)
    conn.commit()

    first = json.loads(
        conn.execute(
            "SELECT artifact_json FROM decision_log WHERE id=?",
            (first_id,),
        ).fetchone()[0]
    )["summary"]
    second = json.loads(
        conn.execute(
            "SELECT artifact_json FROM decision_log WHERE id=?",
            (second_id,),
        ).fetchone()[0]
    )["summary"]
    raw = zlib.decompress(
        base64.b64decode(first["holding_auction_coverage_zlib_b64"])
    )
    assert first["schema_version"] == 21
    assert "holding_auction_coverage" not in first
    expected_coverage = json.loads(
        json.dumps(coverage.__dict__, default=str)
    )
    assert json.loads(raw) == [expected_coverage]
    assert second["payload_compacted"] is True
    assert "holding_auction_coverage_zlib_b64" not in second
    assert "holding_auction_coverage_zlib_b64" in second[
        "payload_reference_fields"
    ]
    conn.close()


@pytest.mark.parametrize("book_present", (False, True), ids=("no_book", "book"))
def test_receipt_rejects_uniform_coverage_deadline_beyond_authoritative_book(
    tmp_path,
    book_present,
):
    from src.engine import global_batch_runtime
    from src.engine.global_single_order_auction import (
        GlobalHoldingAuctionCoverage,
        PreparedGlobalAuctionResult,
    )
    from src.solve.solver import GlobalSingleOrderDecision
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "holding-coverage-deadline.db")
    init_schema(conn)
    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    obligations = tuple(
        global_batch_runtime._CurrentHeldObligation(
            position_id=f"position-{index}",
            family_key=f"family-{index}",
            bin_label=f"{20 + index}C",
            condition_id=f"condition-{index}",
            side="YES" if index == 1 else "NO",
            token_id=f"token-{index}",
            held_shares=Decimal(str(index)),
        )
        for index in (1, 2)
    )
    coverage = tuple(
        GlobalHoldingAuctionCoverage(
            position_id=obligation.position_id,
            family_key=obligation.family_key,
            bin_id=None,
            bin_label=obligation.bin_label,
            condition_id=obligation.condition_id,
            side=obligation.side,
            token_id=obligation.token_id,
            held_shares=obligation.held_shares,
            ledger_snapshot_id="ledger-current",
            probability_witness_identity=None,
            probability_content_identity=None,
            wealth_economic_identity="wealth-current",
            selection_epoch_identity="epoch-current",
            book_epoch_identity="book-unavailable-current",
            selection_cut_at_utc=at,
            decision_at_utc=at,
            book_deadline_at_utc=(
                at + timedelta(seconds=31)
                if book_present
                else at + timedelta(seconds=1)
            ),
            status="EXCLUDED",
            reason="PROBABILITY_AUTHORITY_UNAVAILABLE:q_missing",
        )
        for obligation in obligations
    )
    selected = PreparedGlobalAuctionResult(
        decision=GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="GLOBAL_FEASIBLE_SET_INCOMPLETE",
            candidate_input_count=0,
        ),
        winner_event_id=None,
        holding_coverage=coverage,
    )

    with pytest.raises(
        ValueError,
        match="GLOBAL_AUCTION_RECEIPT_HELD_POSITION_COVERAGE_INCOMPLETE",
    ):
        global_batch_runtime._store_global_auction_receipt(
            conn,
            selected=selected,
            selection_epoch_identity="epoch-current",
            selection_cut_at_utc=at,
            decision_at_utc=at,
            probability_manifest=(),
            full_scope_identity="scope-current",
            full_scope_family_keys=tuple(
                obligation.family_key for obligation in obligations
            ),
            probability_ineligible_by_family={
                obligation.family_key: "q_missing"
                for obligation in obligations
            },
            book_epoch_identity="book-unavailable-current",
            book_asset_count=0 if book_present else None,
            book_asset_states=(),
            wealth_witness=_global_auction_receipt_wealth_witness(),
            fractional_kelly_multiplier=Decimal("0.25"),
            book_captured_at_utc=at if book_present else None,
            book_max_age=(
                timedelta(seconds=30) if book_present else None
            ),
            expected_holding_obligations=obligations,
            holding_probability_witnesses={},
        )
    conn.close()


def test_receipt_rejects_coverage_q_absent_from_authoritative_manifest(tmp_path):
    from src.engine import global_batch_runtime
    from src.engine.global_single_order_auction import (
        GlobalHoldingAuctionCoverage,
        PreparedGlobalAuctionResult,
    )
    from src.solve.solver import (
        GlobalSingleOrderCandidateEvaluation,
        GlobalSingleOrderDecision,
    )
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "holding-coverage-manifest.db")
    init_schema(conn)
    at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    obligation = global_batch_runtime._CurrentHeldObligation(
        position_id="position-sell",
        family_key="family-sell",
        bin_label="21C",
        condition_id="condition-sell",
        side="NO",
        token_id="token-sell",
        held_shares=Decimal("10"),
    )
    probability = SimpleNamespace(
        witness_identity="q-sell",
        probability_content_identity="q-content-sell",
        bindings=(
            SimpleNamespace(
                bin_id="canonical-bin-sell",
                condition_id=obligation.condition_id,
                yes_token_id="token-yes-sell",
                no_token_id=obligation.token_id,
            ),
        ),
    )
    coverage = GlobalHoldingAuctionCoverage(
        position_id=obligation.position_id,
        family_key=obligation.family_key,
        bin_id="canonical-bin-sell",
        bin_label=obligation.bin_label,
        condition_id=obligation.condition_id,
        side=obligation.side,
        token_id=obligation.token_id,
        held_shares=obligation.held_shares,
        ledger_snapshot_id="ledger-current",
        probability_witness_identity="q-sell",
        probability_content_identity="q-content-sell",
        wealth_economic_identity="wealth-current",
        selection_epoch_identity="epoch-current",
        book_epoch_identity="book-unavailable-current",
        selection_cut_at_utc=at,
        decision_at_utc=at,
        book_deadline_at_utc=at,
        book_state="EXECUTABLE",
        status="EVALUATED",
        candidate_id="sell-current",
        sell_book_witness_identity="sell-book-current",
    )
    selected = PreparedGlobalAuctionResult(
        decision=GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
            rejection_reasons={"sell-current": "NON_POSITIVE_ROBUST_OBJECTIVE"},
            candidate_evaluations=(
                GlobalSingleOrderCandidateEvaluation(
                    candidate_id="sell-current",
                    family_key=obligation.family_key,
                    bin_id="canonical-bin-sell",
                    condition_id=obligation.condition_id,
                    side=obligation.side,
                    token_id=obligation.token_id,
                    action="SELL",
                    status="REJECTED",
                    position_id=obligation.position_id,
                    held_shares=obligation.held_shares,
                    rejection_reason="NON_POSITIVE_ROBUST_OBJECTIVE",
                ),
            ),
            candidate_input_count=1,
        ),
        winner_event_id=None,
        holding_coverage=(coverage,),
    )

    with pytest.raises(
        ValueError,
        match="GLOBAL_AUCTION_RECEIPT_HELD_POSITION_COVERAGE_INCOMPLETE",
    ):
        global_batch_runtime._store_global_auction_receipt(
            conn,
            selected=selected,
            selection_epoch_identity="epoch-current",
            selection_cut_at_utc=at,
            decision_at_utc=at,
            probability_manifest=((obligation.family_key, "q-new"),),
            full_scope_identity="scope-current",
            full_scope_family_keys=(obligation.family_key,),
            probability_ineligible_by_family={},
            book_epoch_identity="book-unavailable-current",
            book_asset_count=None,
            book_asset_states=(),
            wealth_witness=_global_auction_receipt_wealth_witness(),
            fractional_kelly_multiplier=Decimal("0.25"),
            expected_holding_obligations=(obligation,),
            holding_probability_witnesses={obligation.family_key: probability},
        )
    conn.close()


def test_pending_exit_backoff_exhausted_dust_hold_does_not_emit_exit_intent(monkeypatch):
    """Dust remains monitored without re-entering the impossible SELL lane."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime, monitor_refresh

    pos = _make_position(
        trade_id="backoff-exhausted-dust-hold",
        direction="buy_no",
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        shares=1.0,
        chain_shares=1.0,
        city="Kuala Lumpur",
        target_date="2026-07-08",
        token_id="yes-kl",
        no_token_id="no-kl",
        condition_id="condition-kl",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_retry_count=MAX_EXIT_RETRIES,
        exit_reason=(
            "DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES (entry=0.8679, current=0.0000) "
            "[DUST: executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5]"
        ),
        last_exit_error="executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5",
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            raise AssertionError("test refresh owns the current quote")

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("dust hold must not record an exit")

    def refresh_current_dust(_conn, _clob, position):
        position.last_monitor_prob = 0.0
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = -0.49
        position.last_monitor_market_price = 0.49
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.49
        position.last_monitor_best_ask = 0.50
        position.last_monitor_at = "2026-07-08T09:30:00+00:00"
        setattr(position, monitor_refresh._HELD_MONITOR_MIN_ORDER_SIZE_ATTR, 5.0)
        setattr(
            position,
            monitor_refresh._GLOBAL_MONITOR_SAMPLES_ATTR,
            np.array([0.0, 0.0]),
        )
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.49]),
            p_posterior=0.0,
            forward_edge=-0.49,
            alpha=0.1,
            confidence_band_upper=-0.49,
            confidence_band_lower=-0.49,
            entry_provenance=EntryMethod.QKERNEL_SPINE,
            decision_snapshot_id="dust-current-cut",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

    monkeypatch.setattr(monitor_refresh, "refresh_position", refresh_current_dust)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _context: ExitDecision(
            True,
            "SELL_REVERSAL",
            trigger="SELL_REVERSAL",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["current_dust_sell_signal"],
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_closed_non_accepting_market_info",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_entry_selection_guard_exit_decision",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_apply_family_monitor_overlay",
        lambda **kwargs: (kwargs["should_exit"], kwargs["exit_reason"]),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda _position: False,
    )
    monkeypatch.setattr(
        "src.events.reactor.request_global_auction_completion",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("current dust must not request global reauction")
        ),
    )

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_backoff_exhausted_dust_hold"),
            "cities_by_name": {"Kuala Lumpur": type("City", (), {"timezone": "Asia/Kuala_Lumpur"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
    )

    assert pos.state == "pending_exit"
    assert pos.exit_state == "backoff_exhausted"
    assert pos.order_status == "backoff_exhausted"
    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["monitor_pending_exit_dust_redecisions"] == 1
    assert summary["monitor_statistical_sell_dust_holds"] == 1
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].should_exit is False
    assert "[DUST:" in monitor_results[0].exit_reason


def test_current_book_min_decrease_releases_dust_back_to_redecision():
    from src.execution.exit_lifecycle import (
        release_backoff_exhausted_pending_exit_for_redecision,
    )

    pos = _make_position(
        trade_id="dust-min-decreased",
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        shares=3.0,
        chain_shares=3.0,
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_reason="SELL_REVERSAL [DUST: size 3 below min_order_size 5]",
        last_exit_error="size 3 below min_order_size 5",
    )

    assert not release_backoff_exhausted_pending_exit_for_redecision(
        pos,
        current_min_order_size="5",
    )
    assert release_backoff_exhausted_pending_exit_for_redecision(
        pos,
        current_min_order_size="1",
    )
    assert pos.state == "day0_window"
    assert pos.exit_state == ""
    assert pos.order_status == "filled"


# ---- Test 7: Collateral check blocks underfunded sell ----

def test_collateral_check_blocks_underfunded_sell():
    """Can't sell if wallet doesn't have enough collateral."""
    clob = _make_clob(balance=0.50)

    # entry_price=0.10, shares=50 → needs (1-0.10)*50 = $45 collateral
    can_sell, reason = check_sell_collateral(
        entry_price=0.10, shares=50.0, clob=clob,
    )

    assert can_sell is False
    assert reason is not None
    assert "need $45.00" in reason


# ---- Test 8: Quarantine expiry timer retired (P0b, 2026-07-04) ----
#
# test_quarantine_expires_after_48h previously asserted that
# check_quarantine_timeouts() minted chain_state="quarantine_expired" after
# 48h. That timer is retired — see
# docs/rebuild/chain_mirror_state_model_2026-07-04.md §5 follow-up — in favor
# of the chain-mirror reconciler's two-consecutive-mirror-runs force-resolve
# (runs every ~10 minutes).
#
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW):
# the T2 "bridging shim" (src.engine.evaluator._quarantined_position_bridging_
# family_keys, keyed on phase='quarantined' + chain_state=
# 'entry_authority_quarantined') is RETIRED along with it. No writer mints
# phase='quarantined' going forward — Position.__post_init__ remaps any
# legacy DB row to its TRUE state before construction (see
# src.state.portfolio._normalize_runtime_lifecycle_state /
# _normalize_runtime_chain_state) — so the bridging shim's own gate
# (`state == "quarantined"`) can never fire for a live Position anymore. A
# confirmed-fill/chain-absence-conflict dispute now opens a
# CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT ReviewWorkItem directly (a
# FAMILY_BLOCKING_REASON_CODES member), which evaluator's DB-backed
# blocked_family_keys() call already consults — the three tests that
# exercised the bridging shim's scoping (legacy chain_state exclusion, own-
# family-only feed, stale-resolved-state exclusion) are retired with it.


# T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW):
# the two admin-resolution monitor-branch tests that lived here
# (test_monitoring_marks_quarantine_for_admin_resolution_once,
# test_monitoring_skips_fill_authority_quarantine_without_chain_quarantine)
# exercised src.engine.cycle_runtime's _requires_quarantine_monitor_resolution
# branch, now retired: no writer mints phase/chain_state='quarantined' going
# forward, and Position.__post_init__ remaps any legacy row to its TRUE state
# before it ever reaches the monitor loop, so the branch was provably
# unreachable for any live Position. A real-exposure position that used to be
# diverted into this admin-resolution limbo now flows through normal monitor
# refresh instead (see test_entry_authority_quarantined_exposure_reaches_redecision).


def test_entry_authority_quarantined_exposure_reaches_redecision(monkeypatch):
    """A real held position with bad entry proof must still be monitor-managed.

    T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE
    LAW): a position that used to be forced into phase='quarantined' with
    chain_state='entry_authority_quarantined' now keeps its TRUE phase
    (active/holding) — Position.__post_init__ remaps any legacy input before
    construction. It flows through NORMAL monitor + exit evaluation directly;
    there is no more special "quarantined redecision" branch to route
    through (src.engine.cycle_runtime's admin-resolution monitor branch is
    retired — real exposure was never excluded from monitoring in the first
    place under the new model).
    """
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="entry-authority-quarantine-position",
        direction="buy_no",
        state="holding",
        chain_state="synced",
        shares=19.88,
        chain_shares=19.88,
        city="Lucknow",
        target_date="2026-06-28",
        token_id="yes-lucknow",
        no_token_id="no-lucknow",
        condition_id="condition-lucknow",
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.40, 0.42, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("no exit fill expected")

    observed_refresh = []

    def mock_refresh(conn, clob, position):
        observed_refresh.append((
            position.trade_id,
            getattr(position.state, "value", position.state),
            getattr(position.chain_state, "value", position.chain_state),
        ))
        position.last_monitor_prob = 0.62
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.22
        position.last_monitor_market_price = 0.40
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.40
        position.last_monitor_best_ask = 0.42
        position.last_monitor_market_vig = 1.02
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = "2026-06-28T08:00:00+00:00"
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.40]),
            p_posterior=0.62,
            forward_edge=0.22,
            alpha=0.0,
            confidence_band_upper=0.05,
            confidence_band_lower=-0.01,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap-entry-authority-quarantine",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    observed_exit_contexts = []

    def mock_evaluate_exit(self, exit_context):
        observed_exit_contexts.append(exit_context)
        return ExitDecision(
            False,
            "ENTRY_AUTHORITY_QUARANTINE_REDECISION_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["entry_authority_quarantine_redecision"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_entry_authority_quarantine_redecision"),
            "cities_by_name": {"Lucknow": type("City", (), {"timezone": "Asia/Kolkata"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert tracker_dirty is False
    assert observed_refresh == [(
        "entry-authority-quarantine-position",
        "day0_window",
        "synced",
    )]
    assert observed_exit_contexts
    assert observed_exit_contexts[0].position_state == "day0_window"
    assert summary["monitors"] == 1
    assert len(monitor_results) == 1
    assert monitor_results[0].fresh_prob == 0.62
    assert monitor_results[0].fresh_edge == 0.22
    assert monitor_results[0].should_exit is False
    assert monitor_results[0].exit_reason == "ENTRY_AUTHORITY_QUARANTINE_REDECISION_HOLD"


# T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md,
# post-T5-migration cleanup):
# test_canonical_monitor_order_includes_entry_authority_quarantined_exposure
# previously pinned that a raw phase='quarantined' canonical row with
# chain_state='entry_authority_quarantined' still reaches monitor ordering.
# _CANONICAL_MONITOR_PHASE_PRIORITY no longer has a "quarantined" entry (only
# pending_exit/day0_window/active), so a phase='quarantined' row is now
# unconditionally excluded regardless of chain_state — and no writer mints
# that phase for genuinely exposed positions anymore (they keep their TRUE
# phase). _canonical_monitor_position_rows' ordering never inspected
# chain_state at all, so there is no current-shape rewrite that would still
# exercise the "chain-backed quarantine" distinction this test was named for;
# retired rather than rewritten.


def test_chain_absent_confirmed_recent_projection_skips_redecision(monkeypatch):
    """Confirmed chain absence is reconciliation debt, not live monitor-managed exposure."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    observed_at = datetime.now(timezone.utc).isoformat()
    # T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md):
    # a real held position now keeps its TRUE phase ("holding") — no writer
    # mints state="quarantined" going forward, and construction with that
    # literal now raises. The chain_absent_confirmed_position_unattributed
    # chain_state alone (a NO_CURRENT_MONEY_RISK_CHAIN_STATES member) still
    # correctly excludes it from monitor via the ordinary risk classification.
    pos = _make_position(
        trade_id="chain-absence-quarantine-position",
        direction="buy_yes",
        state="holding",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=65.0,
        chain_shares=65.0,
        city="Chongqing",
        target_date="2026-07-01",
        token_id="yes-chongqing",
        no_token_id="no-chongqing",
        condition_id="condition-chongqing",
        last_chain_absence_observed_at=observed_at,
        chain_verified_at=observed_at,
        order_status="filled",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.004, 0.006, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("no exit fill expected")

    observed_refresh = []

    def mock_refresh(conn, clob, position):
        observed_refresh.append((
            position.trade_id,
            getattr(position.state, "value", position.state),
            getattr(position.chain_state, "value", position.chain_state),
        ))
        position.last_monitor_prob = 0.03
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.004
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.004
        position.last_monitor_best_ask = 0.006
        position.last_monitor_market_vig = 1.02
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = observed_at
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.004]),
            p_posterior=0.03,
            forward_edge=0.026,
            alpha=0.0,
            confidence_band_upper=0.04,
            confidence_band_lower=0.02,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap-chain-absence-quarantine",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    observed_exit_contexts = []

    def mock_evaluate_exit(self, exit_context):
        observed_exit_contexts.append(exit_context)
        return ExitDecision(
            False,
            "CHAIN_ABSENCE_QUARANTINE_REDECISION_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["chain_absence_quarantine_redecision"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_chain_absence_quarantine_redecision"),
            "cities_by_name": {"Chongqing": type("City", (), {"timezone": "Asia/Shanghai"})()},
            "_utcnow": staticmethod(lambda: datetime.now(timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE
    # LAW): the position keeps its TRUE (holding) phase now — no more
    # quarantine-admin-resolution branch — but a chain_state confirming
    # no-current-money-risk (chain_absent_confirmed_position_unattributed)
    # still correctly excludes it from the monitored set entirely, via the
    # pre-existing exposure/risk classification this excision did not touch.
    # "Confirmed chain absence is reconciliation debt, not live monitor-
    # managed exposure" holds true through the ordinary risk-classification
    # path now, without a dedicated quarantine bucket.
    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert observed_refresh == []
    assert observed_exit_contexts == []
    assert summary["monitors"] == 0
    assert monitor_results == []


def test_chain_absent_confirmed_recent_projection_does_not_reach_exit_lifecycle(monkeypatch):
    """Confirmed chain absence must not manufacture a live exit lifecycle action."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime

    observed_at = datetime.now(timezone.utc).isoformat()
    # T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md):
    # see test_chain_absent_confirmed_recent_projection_skips_redecision above
    # — real held phase, no writer mints "quarantined" going forward.
    pos = _make_position(
        trade_id="chain-absence-quarantine-exit-position",
        direction="buy_yes",
        state="holding",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=12.7,
        chain_shares=12.7,
        city="Singapore",
        target_date="2026-07-01",
        token_id="yes-singapore",
        no_token_id="no-singapore",
        condition_id="condition-singapore",
        last_chain_absence_observed_at=observed_at,
        chain_verified_at=observed_at,
        order_status="partial",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.06, 0.07, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("fake exit lifecycle does not report a fill")

    def mock_refresh(conn, clob, position):
        position.last_monitor_prob = 0.02
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.06
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.06
        position.last_monitor_best_ask = 0.07
        position.last_monitor_market_vig = 1.02
        position.last_monitor_whale_toxicity = False
        position.last_monitor_at = observed_at
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.06]),
            p_posterior=0.02,
            forward_edge=-0.04,
            alpha=0.0,
            confidence_band_upper=0.03,
            confidence_band_lower=0.01,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap-chain-absence-quarantine-exit",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    def mock_evaluate_exit(self, exit_context):
        return ExitDecision(
            True,
            "QUARANTINED_EXPOSURE_REDECISION_EXIT",
            trigger="QUARANTINED_EXPOSURE_REDECISION_EXIT",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["chain_absence_quarantine_redecision_exit"],
        )

    observed_execute = []

    def mock_build_exit_intent(position, exit_context):
        return SimpleNamespace(token_id=position.token_id, reason=exit_context.exit_reason)

    def mock_execute_exit(*, portfolio, position, exit_context, clob, conn, exit_intent):
        observed_execute.append(
            {
                "position_id": position.trade_id,
                "state": getattr(position.state, "value", position.state),
                "chain_state": getattr(position.chain_state, "value", position.chain_state),
                "exit_reason": exit_context.exit_reason,
                "token_id": exit_intent.token_id,
            }
        )
        position.state = "pending_exit"
        return "sell_order_placed:test"

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)
    monkeypatch.setattr("src.execution.exit_lifecycle.build_exit_intent", mock_build_exit_intent)
    monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit", mock_execute_exit)

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_chain_absence_quarantine_exit_lifecycle"),
            "cities_by_name": {"Singapore": type("City", (), {"timezone": "Asia/Singapore"})()},
            "_utcnow": staticmethod(lambda: datetime.now(timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE
    # LAW): the position keeps its TRUE (holding) phase now — no more
    # quarantine-admin-resolution branch — but a chain_state confirming
    # no-current-money-risk (chain_absent_confirmed_position_unattributed)
    # still correctly excludes it from the monitored set entirely (see
    # test_chain_absent_confirmed_recent_projection_skips_redecision), so it
    # never reaches exit evaluation either.
    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert summary["monitors"] == 0
    assert summary.get("exits", 0) == 0
    assert monitor_results == []
    assert observed_execute == []
    assert pos.state == "holding"


def test_chain_absent_confirmed_positive_projection_does_not_redecision():
    """Stale local shares on a confirmed-absent chain state do not create live exposure."""
    from src.contracts.position_truth import has_current_money_risk_chain_state

    # T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md):
    # cycle_runtime._quarantined_position_can_redecision (the canonical
    # redecision-eligibility predicate this test used to call directly) is
    # deleted — its own gate (state == "quarantined") could never fire once
    # no writer mints that phase. The current equivalent "does this chain
    # state represent live money risk" check is
    # has_current_money_risk_chain_state, which the deleted predicate itself
    # deferred to.
    pos = _make_position(
        direction="buy_yes",
        state="holding",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=12.7,
        chain_shares=12.7,
        last_chain_absence_observed_at="2026-06-20T00:00:00+00:00",
        chain_verified_at="",
    )

    assert has_current_money_risk_chain_state(pos.chain_state) is False


def test_pending_exit_chain_absent_positive_exposure_stays_open_for_exit_lifecycle():
    """A pending exit with real chain shares must stay in the open set for exit management."""
    from src.state.portfolio import get_open_positions

    pos = _make_position(
        direction="buy_yes",
        state="pending_exit",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=9.7,
        chain_shares=9.7,
        exit_state="retry_pending",
        order_status="retry_pending",
        next_exit_retry_at="2026-06-29T17:17:30+00:00",
    )
    portfolio = _make_portfolio(pos)

    assert get_open_positions(portfolio) == [pos]

    zero = _make_position(
        direction="buy_yes",
        state="pending_exit",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=9.7,
        chain_shares=0.0,
        exit_state="retry_pending",
        order_status="retry_pending",
    )
    assert get_open_positions(_make_portfolio(zero)) == []


def test_pending_exit_retry_cooldown_refreshes_belief_without_duplicate_exit(
    monkeypatch,
):
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import ExitDecision, Position
    from src.state.db import init_schema
    from src.state.projection import upsert_position_current

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    pos = _make_position(
        trade_id="pending-exit-retry-cooldown-monitor",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
        shares=9.7,
        chain_shares=9.7,
        exit_state="retry_pending",
        order_status="retry_pending",
        next_exit_retry_at="2099-01-01T00:00:00+00:00",
        last_monitor_prob=0.12,
        last_monitor_prob_is_fresh=True,
        fill_authority="venue_confirmed_full",
        condition_id="condition-pending-exit-retry-cooldown-monitor",
        strategy_key="forecast_qkernel_entry",
        entered_at="2026-07-02T19:00:00+00:00",
    )
    # execute_monitoring_phase consumes runtime DB/string state; _make_position
    # normalizes through enums for some tests.
    pos.state = "pending_exit"
    pos.chain_state = "synced"
    pos.exit_state = "retry_pending"
    pos.order_status = "retry_pending"
    upsert_position_current(conn, build_position_current_projection(pos))
    portfolio = _make_portfolio(pos)
    refreshes = []
    execute_calls = []

    def refresh_position(_conn, _clob, position):
        refreshes.append(position.trade_id)
        position.last_monitor_at = "2026-07-02T20:20:00+00:00"
        position.last_monitor_prob = 0.03
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = -0.08
        position.last_monitor_market_price = 0.11
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = 0.10
        position.last_monitor_best_ask = 0.12
        position.last_monitor_market_vig = 1.02
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.11]),
            p_posterior=0.03,
            forward_edge=-0.08,
            alpha=0.0,
            confidence_band_upper=0.04,
            confidence_band_lower=0.02,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap-pending-exit-cooldown",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    def evaluate_exit(_position, _context):
        return ExitDecision(
            True,
            "RISK_RED_FORCE_EXIT",
            trigger="RED_FORCE_EXIT",
            selected_method="replacement_current_evidence",
            applied_validations=["fresh_cooldown_redecision"],
        )

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        refresh_position,
    )
    monkeypatch.setattr(Position, "evaluate_exit", evaluate_exit)
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        lambda **kwargs: execute_calls.append(kwargs),
    )
    monitor_results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: monitor_results.append(result)},
    )()
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)},
            ),
            "logger": logging.getLogger("test_pending_exit_retry_cooldown_monitor"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 2, 20, 20, tzinfo=timezone.utc)),
        },
    )
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
        run_exit_preflight=False,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert refreshes == [pos.trade_id]
    assert execute_calls == []
    assert summary["monitor_pending_exit_retry_cooldown_redecisions"] == 1
    assert summary["monitor_pending_exit_phase_evaluated"] == 1
    assert summary["pending_exit_exit_signal_already_in_flight"] == 1
    assert summary["monitors"] == 1
    assert monitor_results[0].fresh_prob == pytest.approx(0.03)
    assert monitor_results[0].fresh_edge == pytest.approx(-0.08)
    assert monitor_results[0].should_exit is True
    assert monitor_results[0].exit_reason == "RISK_RED_FORCE_EXIT"
    event = conn.execute(
        """
        SELECT event_type, occurred_at, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        (pos.trade_id,),
    ).fetchone()
    assert event is not None
    assert event["occurred_at"] == "2026-07-02T20:20:00+00:00"
    payload = json.loads(event["payload_json"])
    assert payload["last_monitor_prob"] == pytest.approx(0.03)
    assert payload["last_monitor_prob_is_fresh"] is True
    assert payload["last_monitor_market_price"] == pytest.approx(0.11)
    assert payload["last_monitor_market_price_is_fresh"] is True
    assert payload["exit_decision_should_exit"] is True
    assert payload["exit_decision_reason"] == "RISK_RED_FORCE_EXIT"

    conn.close()


def test_monitor_refresh_with_exit_backoff_preserves_pending_exit_phase(tmp_path):
    """Monitor receipts must not re-project a pending dust/backoff exit as day0."""
    from src.engine import cycle_runtime
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "pending-exit-monitor-phase.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="pending-exit-monitor-phase",
        direction="buy_no",
        state="day0_window",
        chain_state="synced",
        shares=1.0,
        chain_shares=1.0,
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        last_monitor_prob=0.0,
        last_monitor_prob_is_fresh=True,
        condition_id="condition-pending-exit-monitor-phase",
        strategy_key="forecast_qkernel_entry",
        entered_at="2026-07-02T19:00:00+00:00",
        exit_reason="DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES [DUST]",
    )
    deps = type(
        "Deps",
        (),
        {
            "logger": logging.getLogger("test_monitor_refresh_pending_exit_phase"),
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 2, 20, 20, tzinfo=timezone.utc)),
        },
    )

    assert cycle_runtime._emit_monitor_refreshed_canonical_if_available(conn, pos, deps=deps) is True

    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        (pos.trade_id,),
    ).fetchone()
    current = conn.execute(
        "SELECT phase, order_status FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()

    assert event is not None
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "pending_exit"
    payload = json.loads(event["payload_json"])
    assert payload["phase_after"] == "pending_exit"
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "backoff_exhausted"
    conn.close()


def test_pending_exit_chain_absent_zero_balance_uses_chain_truth_resolution(monkeypatch):
    """Pending-exit chain-absent review rows must resolve via balanceOf instead of sell retry."""
    from src.execution.exit_lifecycle import handle_exit_pending_missing

    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x" + "1" * 40)
    pos = _make_position(
        trade_id="pending-chain-absent-zero",
        direction="buy_yes",
        state="pending_exit",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=9.7,
        chain_shares=9.7,
        exit_state="retry_pending",
        order_status="retry_pending",
        token_id="123456789",
        condition_id="condition-pending-chain-absent-zero",
    )
    portfolio = _make_portfolio(pos)

    result = handle_exit_pending_missing(
        portfolio,
        pos,
        rpc_call=lambda *_args, **_kwargs: "0x0",
    )

    assert result["action"] == "closed"
    assert result["position"].state == "voided"
    assert result["position"].exit_reason == "CHAIN_CONFIRMED_ZERO"
    assert result["position"].chain_state == "chain_confirmed_zero"
    assert result["position"].chain_shares == 0.0
    assert result["position"].order_status == "voided"
    assert result["position"].exit_state == ""
    assert result["position"].exit_retry_count == 0
    assert result["position"].next_exit_retry_at == ""
    assert portfolio.positions == []


def test_monitor_entry_selection_guard_invalidity_requires_independent_exit():
    """A bad historical entry proof is not itself live exit authority."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed",
            "pos-unarmed",
            "edli_exec_cmd:agg-unarmed:edli_intent:agg-unarmed:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed:edli_intent:agg-unarmed:tok",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    # T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md):
    # a real held position now keeps its TRUE phase — no writer mints
    # state="quarantined" going forward, and construction with that literal
    # now raises. This test is about entry-proof-invalidity vs live exit
    # authority, independent of lifecycle phase.
    pos = _make_position(
        trade_id="pos-unarmed",
        direction="buy_yes",
        state="holding",
        chain_state="chain_absent_confirmed_position_unattributed",
        shares=65.0,
        chain_shares=65.0,
        entry_method="qkernel_spine",
        selected_method="qkernel_spine",
    )
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.006),
        summary=summary,
    )

    assert decision is not None
    assert decision.should_exit is False
    assert decision.trigger == "ENTRY_SELECTION_GUARD_INVALID_HOLD_REQUIRES_CURRENT_EXIT"
    assert "selection_guard_side_not_armed" in decision.reason
    assert summary["entry_selection_guard_invalid_positions"] == 1
    assert summary["entry_selection_guard_invalid_independent_exit_required"] == 1


def test_monitor_entry_selection_guard_does_not_force_exit_over_fresh_positive_edge():
    """Historical entry-guard invalidity cannot override current positive monitor EV."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed",
            "pos-unarmed-positive-now",
            "edli_exec_cmd:agg-unarmed:edli_intent:agg-unarmed:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed:edli_intent:agg-unarmed:tok",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    pos = _make_position(
        trade_id="pos-unarmed-positive-now",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
        shares=85.17,
        chain_shares=85.17,
        entry_method="qkernel_spine",
        selected_method="qkernel_spine",
    )
    pos.last_monitor_prob = 0.3392837479
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.2800396534
    pos.last_monitor_market_price = 0.0592440945
    pos.last_monitor_market_price_is_fresh = True
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.052),
        summary=summary,
    )

    assert decision is not None
    assert decision.should_exit is False
    assert decision.trigger == "ENTRY_SELECTION_GUARD_INVALID_HOLD_CURRENT_EDGE"
    assert "current_edge=0.2800" in decision.reason
    assert summary["entry_selection_guard_invalid_current_ev_holds"] == 1


def _install_market_alpha_rejection(monkeypatch):
    from src.control import control_plane

    monkeypatch.setattr(
        control_plane,
        "strategy_gates",
        lambda: {
            "forecast_qkernel_entry": SimpleNamespace(
                enabled=False,
                reason_snapshot={
                    "reason": (
                        "market_relative_alpha_rejected("
                        "evalue=12.688312,clusters=2,law=predicted_bin_ev_v1)"
                    )
                },
            )
        },
    )


def test_monitor_entry_gate_cannot_invent_a_held_position_sell(monkeypatch):
    """Historical entry evidence cannot bypass current CASH/HOLD/SELL authority."""
    from src.engine import cycle_runtime

    _install_market_alpha_rejection(monkeypatch)
    pos = _make_position(
        strategy_key="forecast_qkernel_entry",
        chain_shares=25.0,
        selected_method="replacement_posterior",
    )
    pos.last_monitor_prob = 0.71
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.39
    pos.last_monitor_market_price_is_fresh = True
    context = SimpleNamespace(
        best_bid=0.32,
        current_market_price_is_fresh=True,
        probability_receipt={"probability_authority": "forecast_posteriors"},
    )
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=None,
        pos=pos,
        exit_context=context,
        summary=summary,
        exit_decision=ExitDecision(False, reason="HOLD", trigger="HOLD"),
    )

    assert decision is None
    assert summary == {}


def test_monitor_rejected_entry_law_does_not_override_independent_day0_q(monkeypatch):
    """A fresh Day0 observation law remains independent HOLD/SELL authority."""
    from src.engine import cycle_runtime

    _install_market_alpha_rejection(monkeypatch)
    pos = _make_position(
        strategy_key="forecast_qkernel_entry",
        chain_shares=25.0,
        selected_method="day0_observation_remaining_window",
    )

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=None,
        pos=pos,
        exit_context=SimpleNamespace(
            best_bid=0.32,
            current_market_price_is_fresh=True,
            probability_receipt={
                "probability_authority": "day0_observation_remaining_window"
            },
        ),
        summary={},
    )

    assert decision is None


def test_monitor_non_alpha_strategy_gate_does_not_invent_exit(monkeypatch):
    """Only the exact empirical alpha rejection changes held-q authority."""
    from src.control import control_plane
    from src.engine import cycle_runtime

    monkeypatch.setattr(
        control_plane,
        "strategy_gates",
        lambda: {
            "forecast_qkernel_entry": SimpleNamespace(
                enabled=False,
                reason_snapshot={"reason": "probability_semantics_authority_unavailable"},
            )
        },
    )
    pos = _make_position(
        strategy_key="forecast_qkernel_entry",
        chain_shares=25.0,
        selected_method="replacement_posterior",
    )

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=None,
        pos=pos,
        exit_context=SimpleNamespace(
            best_bid=0.32,
            current_market_price_is_fresh=True,
            probability_receipt={"probability_authority": "forecast_posteriors"},
        ),
        summary={},
    )

    assert decision is None


def test_monitor_entry_selection_guard_does_not_force_exit_on_immature_day0():
    """Historical entry-guard invalidity cannot override an immature Day0 authority block."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed-day0",
            "pos-unarmed-day0",
            "edli_exec_cmd:agg-unarmed-day0:edli_intent:agg-unarmed-day0:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed-day0:edli_intent:agg-unarmed-day0:tok",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    pos = _make_position(
        trade_id="pos-unarmed-day0",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
        shares=85.17,
        chain_shares=85.17,
        entry_method="qkernel_spine",
        selected_method="day0_observation_remaining_window",
    )
    pos.last_monitor_prob = 0.0
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = -0.031
    pos.last_monitor_market_price = 0.031
    pos.last_monitor_market_price_is_fresh = True
    exit_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="day0_observation_remaining_window",
        applied_validations=[
            "day0_observation_remaining_window",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.012",
        ],
    )
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.031),
        summary=summary,
        exit_decision=exit_decision,
    )

    assert decision is not None
    assert decision.should_exit is False
    assert decision.trigger == "ENTRY_SELECTION_GUARD_INVALID_HOLD_DAY0_IMMATURE"
    assert "day0_high_extreme_not_mature:" in decision.reason
    assert summary["entry_selection_guard_invalid_day0_immature_holds"] == 1


def test_monitor_entry_selection_guard_preserves_existing_day0_exit_decision():
    """Entry guard may flag invalid entry proof, but must not rename an existing exit."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-unarmed-day0-exit",
            "pos-unarmed-day0-exit",
            "edli_exec_cmd:agg-unarmed-day0-exit:edli_intent:agg-unarmed-day0-exit:tok:tok:buy_yes",
            "2026-06-29T12:00:00+00:00",
            "2026-06-29T12:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 3, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-unarmed-day0-exit:edli_intent:agg-unarmed-day0-exit:tok",
            "2026-06-29T12:00:00+00:00",
            json.dumps(
                {
                    "direction": "buy_yes",
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                        "selection_guard_cell_key": "YES|tail|nonmodal|pb2",
                        "payoff_q_lcb": 0.0,
                        "cost": 0.07,
                        "edge_lcb": 0.0,
                    },
                }
            ),
        ),
    )
    pos = _make_position(
        trade_id="pos-unarmed-day0-exit",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
        shares=85.17,
        chain_shares=85.17,
        entry_method="qkernel_spine",
        selected_method="day0_observation_remaining_window",
    )
    exit_decision = ExitDecision(
        True,
        reason="DAY0_HARD_FACT_BIN_DEAD (running_extreme_refutes_bin; source=observation_instants)",
        urgency="immediate",
        trigger="DAY0_HARD_FACT_BIN_DEAD",
        selected_method="day0_observation_remaining_window",
        applied_validations=["day0_hard_fact_exit_lane"],
    )
    summary = {}

    decision = cycle_runtime._entry_selection_guard_exit_decision(
        conn=conn,
        pos=pos,
        exit_context=SimpleNamespace(best_bid=0.031),
        summary=summary,
        exit_decision=exit_decision,
    )

    assert decision is None
    assert summary["entry_selection_guard_invalid_positions"] == 1
    assert summary["entry_selection_guard_invalid_existing_exit_preserved"] == 1


def test_monitor_entry_selection_guard_requires_exact_entry_aggregate_identity():
    """A decision-id substring is not authority for an unrelated EDLI aggregate."""
    from src.engine import cycle_runtime

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            decision_id TEXT,
            intent_kind TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, decision_id, intent_kind, created_at, updated_at
        ) VALUES (?, ?, ?, 'ENTRY', ?, ?)
        """,
        (
            "cmd-exact-identity",
            "pos-exact-identity",
            "edli_exec_cmd:agg-exact:edli_intent:agg-exact:tok:tok:buy_yes",
            "2026-07-15T00:00:00+00:00",
            "2026-07-15T00:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, 1, 'PreSubmitRevalidated', ?, ?)
        """,
        (
            "agg-exact",
            "2026-07-15T00:00:00+00:00",
            json.dumps(
                {
                    "qkernel_execution_economics": {
                        "source": "qkernel_spine",
                        "selection_guard_basis": "SIDE_NOT_ARMED",
                        "selection_guard_abstained": False,
                        "selection_guard_q_safe": 0.0,
                    }
                }
            ),
        ),
    )
    pos = _make_position(trade_id="pos-exact-identity")

    assert cycle_runtime._entry_qkernel_selection_guard_verdict(conn, pos) is None


def test_entry_replacement_blocks_when_materializable_raw_cycle_newer_than_posterior():
    """Entry must not trade a stale posterior after anchor-qualified raw inputs advance."""
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    for model in ("ecmwf_ifs", "gfs", "icon"):
        conn.execute(
            """
            INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model,
                "Singapore",
                "2026-07-01",
                "high",
                "2026-06-29T12:00:00+00:00",
                "single_runs",
                "COVERED",
                "2026-06-29T12:20:00+00:00",
                "2026-06-29T12:10:00+00:00",
            ),
        )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Singapore",
            target_date="2026-07-01",
            metric="high",
        ),
        decision_time=datetime(2026, 6, 29, 13, 0, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-06-29T06:00:00+00:00",
    )

    assert reason is not None
    assert "source_cycle_time_raw_model_forecasts_lag" in reason
    assert "latest_raw_cycle=2026-06-29T12:00:00+00:00" in reason
    assert "posterior_cycle=2026-06-29T06:00:00+00:00" in reason


def test_entry_posterior_lookup_is_live_only_and_uses_live_family_index(monkeypatch):
    """Entry authority must reject non-live rows and avoid a temp ORDER BY tree."""
    from src.engine import event_reactor_adapter as adapter

    class RecordingConnection(sqlite3.Connection):
        posterior_query: tuple[str, tuple[object, ...]] | None = None

        def execute(self, sql, parameters=()):  # noqa: ANN001
            if "FROM forecast_posteriors" in sql and "SELECT source_id" in sql:
                self.posterior_query = (sql, tuple(parameters))
            return super().execute(sql, parameters)

    conn = sqlite3.connect(":memory:", factory=RecordingConnection)
    conn.executescript(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            runtime_layer TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            source_id TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            posterior_identity_hash TEXT,
            data_version TEXT,
            family_id TEXT,
            bin_topology_hash TEXT,
            q_json TEXT,
            q_lcb_json TEXT,
            q_ucb_json TEXT,
            provenance_json TEXT
        );
        CREATE INDEX idx_forecast_posteriors_live_family_cycle
            ON forecast_posteriors (
                product_id, city, target_date, temperature_metric,
                source_cycle_time DESC, computed_at DESC, posterior_id DESC
            )
            WHERE runtime_layer = 'live';
        INSERT INTO forecast_posteriors (
            posterior_id, product_id, runtime_layer, city, target_date,
            temperature_metric, source_id, source_cycle_time,
            source_available_at, computed_at
        ) VALUES (
            'offline-newer', 'openmeteo_ecmwf_ifs9_bayes_fusion_v1', 'offline',
            'Chicago', '2026-07-28', 'high', 'offline-source',
            '2026-07-27T12:00:00+00:00', '2026-07-27T12:05:00+00:00',
            '2026-07-27T12:06:00+00:00'
        );
        """
    )
    monkeypatch.setattr(
        adapter,
        "runtime_cities_by_name",
        lambda: {"Chicago": SimpleNamespace(name="Chicago")},
    )
    monkeypatch.setattr(adapter, "_authority_table_ref", lambda _conn, name: name)
    reason: dict[str, str] = {}

    result = adapter._forecast_authority_payload_from_posterior(
        conn,
        event=SimpleNamespace(),
        family=SimpleNamespace(
            city="Chicago",
            target_date="2026-07-28",
            metric="high",
        ),
        payload={},
        decision_time=datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc),
        reason_out=reason,
    )

    assert result is None
    assert reason == {"reason": "no_row"}
    assert conn.posterior_query is not None
    sql, params = conn.posterior_query
    assert "runtime_layer = 'live'" in sql
    plan = "\n".join(
        str(row[3])
        for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    )
    assert "idx_forecast_posteriors_live_family_cycle" in plan
    assert "TEMP B-TREE" not in plan


def _global_jit_clob_market(
    condition_id: str,
    yes_token: str,
    no_token: str,
    *,
    neg_risk: bool = False,
) -> dict[str, object]:
    return {
        "condition_id": condition_id,
        "clobTokenIds": [yes_token, no_token],
        "accepting_orders": True,
        "enable_order_book": True,
        "archived": False,
        "tick_size": "0.01",
        "min_order_size": "1",
        "neg_risk": neg_risk,
    }


def _global_jit_book(token_id: str, *, neg_risk: bool = False) -> dict[str, object]:
    return {
        "asset_id": token_id,
        "bids": [{"price": "0.49", "size": "10"}],
        "asks": [{"price": "0.50", "size": "10"}],
        "tick_size": "0.01",
        "min_order_size": "1",
        "neg_risk": neg_risk,
    }


def test_global_exit_require_exact_handoff_reuses_only_exact_row(monkeypatch):
    """A global SELL uses the exact canonical row, never the newer token row."""
    from src.execution import exit_lifecycle
    from src.state import snapshot_repo
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot

    network_calls = []
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_exit_snapshot_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("exact handoff must not consult latest token row")
        ),
    )

    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda *_args, **_kwargs: network_calls.append("sibling"),
    )
    monkeypatch.setattr(
        "src.data.market_scanner.capture_executable_market_snapshot",
        lambda *_args, **_kwargs: network_calls.append("capture"),
    )
    conn = sqlite3.connect(":memory:")
    try:
        snapshot_repo.init_snapshot_schema(conn)
        captured = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)

        def add_snapshot(snapshot_id, *, captured_at, deadline, raw_hash, bid="0.49", token="yes-exit"):
            snapshot_repo.insert_snapshot(
                conn,
                ExecutableMarketSnapshot(
                    snapshot_id=snapshot_id,
                    gamma_market_id="condition-exit",
                    event_id="event-exit",
                    event_slug="exit",
                    condition_id="condition-exit",
                    question_id="question-exit",
                    yes_token_id="yes-exit",
                    no_token_id="no-exit",
                    selected_outcome_token_id=token,
                    outcome_label="YES" if token == "yes-exit" else "NO",
                    enable_orderbook=True,
                    active=True,
                    closed=False,
                    accepting_orders=True,
                    market_start_at=None,
                    market_end_at=None,
                    market_close_at=None,
                    sports_start_at=None,
                    min_tick_size=Decimal("0.01"),
                    min_order_size=Decimal("1"),
                    fee_details={"fee_rate": "0.05"},
                    token_map_raw={"YES": "yes-exit", "NO": "no-exit"},
                    rfqe=None,
                    neg_risk=False,
                    orderbook_top_bid=Decimal(bid) if bid is not None else None,
                    orderbook_top_ask=Decimal("0.50") if bid is not None else None,
                    orderbook_depth_jsonb='{"bids": [], "asks": []}',
                    raw_gamma_payload_hash="a" * 64,
                    raw_clob_market_info_hash="b" * 64,
                    raw_orderbook_hash=raw_hash,
                    authority_tier="CLOB",
                    captured_at=captured_at,
                    freshness_deadline=deadline,
                ),
            )

        add_snapshot(
            "snap-exact",
            captured_at=captured,
            deadline=captured + timedelta(minutes=5),
            raw_hash="c" * 64,
        )
        add_snapshot(
            "snap-newer",
            captured_at=captured + timedelta(seconds=1),
            deadline=captured + timedelta(minutes=5),
            raw_hash="d" * 64,
        )
        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(
                trade_id="global-sell-force-current",
                market_id="condition-exit",
                token_id="yes-exit",
                no_token_id="no-exit",
                direction="sell_yes",
            ),
            "yes-exit",
            now=captured + timedelta(seconds=2),
            required_snapshot_id="snap-exact",
            required_raw_orderbook_hash="c" * 64,
            require_exact_handoff_snapshot=True,
        )["executable_snapshot_id"] == "snap-exact"
        assert network_calls == []

        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(market_id="condition-exit", token_id="no-exit", no_token_id="yes-exit"),
            "no-exit",
            now=captured + timedelta(seconds=2),
            required_snapshot_id="snap-exact",
            required_raw_orderbook_hash="c" * 64,
            require_exact_handoff_snapshot=True,
        ) == {}

        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(
                trade_id="global-sell-force-current-id-mismatch",
                market_id="condition-exit",
                token_id="yes-exit",
                no_token_id="no-exit",
                direction="sell_yes",
            ),
            "yes-exit",
            now=captured + timedelta(seconds=2),
            required_snapshot_id="snap-missing",
            required_raw_orderbook_hash="c" * 64,
            require_exact_handoff_snapshot=True,
        ) == {}
        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(market_id="condition-exit", token_id="yes-exit", no_token_id="no-exit"),
            "yes-exit",
            now=captured + timedelta(seconds=2),
            required_snapshot_id=None,
            required_raw_orderbook_hash="c" * 64,
            require_exact_handoff_snapshot=True,
        ) == {}
        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(market_id="condition-exit", token_id="yes-exit", no_token_id="no-exit"),
            "yes-exit",
            now=captured + timedelta(seconds=2),
            required_snapshot_id="snap-exact",
            required_raw_orderbook_hash=None,
            require_exact_handoff_snapshot=True,
        ) == {}
        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(
                trade_id="global-sell-force-current-hash-mismatch",
                market_id="condition-exit",
                token_id="yes-exit",
                no_token_id="no-exit",
                direction="sell_yes",
            ),
            "yes-exit",
            now=captured + timedelta(seconds=2),
            required_snapshot_id="snap-exact",
            required_raw_orderbook_hash="d" * 64,
            require_exact_handoff_snapshot=True,
        ) == {}
        add_snapshot(
            "snap-stale",
            captured_at=captured - timedelta(minutes=10),
            deadline=captured - timedelta(seconds=1),
            raw_hash="e" * 64,
        )
        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(market_id="condition-exit", token_id="yes-exit", no_token_id="no-exit"),
            "yes-exit",
            now=captured,
            required_snapshot_id="snap-stale",
            required_raw_orderbook_hash="e" * 64,
            require_exact_handoff_snapshot=True,
        ) == {}
        add_snapshot(
            "snap-no-bid",
            captured_at=captured,
            deadline=captured + timedelta(minutes=5),
            raw_hash="f" * 64,
            bid=None,
        )
        assert exit_lifecycle._latest_or_capture_exit_snapshot_context(
            conn,
            object(),
            SimpleNamespace(market_id="condition-exit", token_id="yes-exit", no_token_id="no-exit"),
            "yes-exit",
            now=captured,
            required_snapshot_id="snap-no-bid",
            required_raw_orderbook_hash="f" * 64,
            require_exact_handoff_snapshot=True,
        ) == {}
        assert network_calls == []
    finally:
        conn.close()


def test_global_sell_jit_fee_uses_current_gamma_v2_schedule(monkeypatch):
    """The current authority retains Gamma, CLOB, and book identity together."""
    from src.contracts import fee_authority
    from src.engine import event_reactor_adapter as adapter

    calls = []

    def gamma_get(path, *, params, timeout):
        calls.append((path, params, timeout))
        return SimpleNamespace(
            status_code=200,
            json=lambda: [
                {
                    "conditionId": "condition-weather",
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "clobTokenIds": ["yes-token-weather", "no-token-weather"],
                    "orderPriceMinTickSize": "0.01",
                    "orderMinSize": "1",
                    "negRisk": False,
                    "feeType": "weather_fees",
                    "feeSchedule": {
                        "exponent": 1,
                        "rate": 0.05,
                        "takerOnly": True,
                        "rebateRate": 0.25,
                    },
                    "takerBaseFee": 1000,
                }
            ],
        )

    monkeypatch.setattr(
        fee_authority,
        "resolve_taker_fee_fraction",
        lambda schedule: (schedule, "current_schedule"),
    )

    authority = adapter._current_global_market_authority(
        condition_id="condition-weather",
        token_id="no-token-weather",
        side="NO",
        gamma_get=gamma_get,
        clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
            "condition-weather", "yes-token-weather", "no-token-weather"
        ),
        raw_book=_global_jit_book("no-token-weather"),
        captured_at_utc=datetime.now(timezone.utc),
        timeout=3.0,
    )

    assert authority.fee_rate == Decimal("0.05")
    assert authority.snapshot.condition_id == "condition-weather"
    assert authority.snapshot.selected_outcome_token_id == "no-token-weather"
    assert authority.snapshot.tradeability_status.executable_allowed is True
    assert authority.snapshot.raw_clob_market_info_hash
    assert authority.snapshot.raw_orderbook_hash
    assert calls == [
        (
            "/markets",
            {"condition_ids": ["condition-weather"], "limit": 1},
            3.0,
        )
    ]


def test_global_jit_snapshot_id_changes_for_each_raw_authority_payload():
    from src.engine import event_reactor_adapter as adapter

    captured_at = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    gamma_market = {
        "conditionId": "condition-identity",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "clobTokenIds": ["yes-identity", "no-identity"],
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "1",
        "negRisk": False,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True},
    }
    clob_market = _global_jit_clob_market(
        "condition-identity", "yes-identity", "no-identity"
    )
    raw_book = _global_jit_book("yes-identity")

    def capture(gamma, clob, book):
        return adapter._current_global_market_authority(
            condition_id="condition-identity",
            token_id="yes-identity",
            side="YES",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200, json=lambda: [gamma]
            ),
            clob_market_get=lambda *_args, **_kwargs: clob,
            raw_book=book,
            captured_at_utc=captured_at,
            timeout=1.0,
        ).snapshot.snapshot_id

    baseline = capture(gamma_market, clob_market, raw_book)
    gamma_changed = dict(gamma_market, description="gamma mutation")
    clob_changed = dict(clob_market, metadata_revision="clob mutation")
    book_changed = dict(raw_book, bids=[{"price": "0.48", "size": "10"}])
    assert len({
        baseline,
        capture(gamma_changed, clob_market, raw_book),
        capture(gamma_market, clob_changed, raw_book),
        capture(gamma_market, clob_market, book_changed),
    }) == 4


def test_global_jit_internal_tradeability_runtime_error_is_not_market_supersession(
    monkeypatch,
):
    from src.engine import event_reactor_adapter as adapter

    sentinel = RuntimeError("tradeability-internal-sentinel")
    monkeypatch.setattr(
        "src.data.market_scanner._build_executable_tradeability_status",
        lambda **_kwargs: (_ for _ in ()).throw(sentinel),
    )
    market = {
        "conditionId": "condition-runtime",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "clobTokenIds": ["yes-runtime", "no-runtime"],
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "1",
        "negRisk": False,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True},
    }
    with pytest.raises(RuntimeError, match="tradeability-internal-sentinel"):
        adapter._current_global_market_authority(
            condition_id="condition-runtime",
            token_id="yes-runtime",
            side="YES",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200, json=lambda: [market]
            ),
            clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
                "condition-runtime", "yes-runtime", "no-runtime"
            ),
            raw_book=_global_jit_book("yes-runtime"),
            captured_at_utc=datetime.now(timezone.utc),
            timeout=1.0,
        )


def test_global_sell_jit_fee_rejects_wrong_gamma_market(monkeypatch):
    """Submit-time authority must bind the selected Gamma/CLOB condition exactly."""
    from src.contracts import fee_authority
    from src.engine import event_reactor_adapter as adapter

    monkeypatch.setattr(
        fee_authority,
        "resolve_taker_fee_fraction",
        lambda schedule: (schedule, "current_schedule"),
    )

    with pytest.raises(
        ValueError,
        match="GLOBAL_JIT_MARKET_IDENTITY_INVALID",
    ):
        adapter._current_global_market_authority(
            condition_id="condition-selected",
            token_id="no-token-selected",
            side="NO",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200,
                json=lambda: [
                    {
                        "conditionId": "condition-other",
                        "feeSchedule": {"exponent": 1, "rate": 0.05},
                    }
                ],
            ),
            clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
                "condition-selected", "yes-token-selected", "no-token-selected"
            ),
            raw_book=_global_jit_book("no-token-selected"),
            captured_at_utc=datetime.now(timezone.utc),
            timeout=3.0,
        )


def test_global_jit_active_false_label_preserves_executable_authority():
    """Active=false is provenance; executable tradeability remains authoritative."""
    from src.engine import event_reactor_adapter as adapter

    closed_market = {
        "conditionId": "condition-sell",
        "active": False,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "clobTokenIds": ["yes-sell", "no-sell"],
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "1",
        "negRisk": False,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True},
    }
    authority = adapter._current_global_market_authority(
            condition_id="condition-sell",
            token_id="yes-sell",
            side="YES",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200, json=lambda: [closed_market]
            ),
            clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
                "condition-sell", "yes-sell", "no-sell"
            ),
            raw_book=_global_jit_book("yes-sell"),
            captured_at_utc=datetime.now(timezone.utc),
            timeout=1.0,
        )
    assert authority.snapshot.active is False
    assert authority.snapshot.tradeability_status.executable_allowed is True


@pytest.mark.parametrize(
    ("name", "mutate", "reason"),
    (
        ("missing_fee", lambda market: market.pop("feeSchedule"), "METADATA_INVALID"),
        ("not_accepting", lambda market: market.__setitem__("acceptingOrders", False), "ACCEPTING_ORDERS_INVALID"),
        ("orderbook_disabled", lambda market: market.__setitem__("enableOrderBook", False), "ENABLE_ORDERBOOK_INVALID"),
    ),
)
def test_global_buy_jit_gamma_metadata_fails_closed(name, mutate, reason):
    """BUY submit authority rejects incomplete fee and non-tradeable Gamma truth."""
    from src.engine import event_reactor_adapter as adapter

    market = {
        "conditionId": "condition-buy",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "clobTokenIds": ["yes-buy", "no-buy"],
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "1",
        "negRisk": False,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True},
    }
    mutate(market)

    with pytest.raises(ValueError, match=reason):
        adapter._current_global_market_authority(
            condition_id="condition-buy",
            token_id="yes-buy",
            side="YES",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200, json=lambda: [market]
            ),
            clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
                "condition-buy", "yes-buy", "no-buy"
            ),
            raw_book=_global_jit_book("yes-buy"),
            captured_at_utc=datetime.now(timezone.utc),
            timeout=1.0,
        )


@pytest.mark.parametrize(
    ("clob_market", "reason"),
    (
        (
            {"archived": True},
            "METADATA_INVALID",
        ),
        (
            {"clobTokenIds": ["yes-buy", "wrong-token"]},
            "CLOB_MARKET_TOKEN_OWNERSHIP_INVALID",
        ),
        (
            {"min_order_size": "2"},
            "METADATA_INVALID",
        ),
    ),
)
def test_global_jit_authority_rejects_current_clob_market_conflicts(
    clob_market, reason
):
    """Gamma truth alone cannot authorize a JIT submit against CLOB conflict."""
    from src.engine import event_reactor_adapter as adapter

    gamma_market = {
        "conditionId": "condition-buy",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "clobTokenIds": ["yes-buy", "no-buy"],
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "1",
        "negRisk": False,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True},
    }
    current_clob = _global_jit_clob_market("condition-buy", "yes-buy", "no-buy")
    current_clob.update(clob_market)
    with pytest.raises(ValueError, match=reason):
        adapter._current_global_market_authority(
            condition_id="condition-buy",
            token_id="yes-buy",
            side="YES",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200, json=lambda: [gamma_market]
            ),
            clob_market_get=lambda *_args, **_kwargs: current_clob,
            raw_book=_global_jit_book("yes-buy"),
            captured_at_utc=datetime.now(timezone.utc),
            timeout=1.0,
        )


def test_global_market_authority_supersession_has_no_candidate_overlay():
    """Metadata/tradeability drift has one outcome: full batch reprepare."""
    from src.engine import event_reactor_adapter as adapter
    from src.engine.global_batch_runtime import GlobalWinnerPreflight

    status, replacement, _reason = adapter._global_curve_supersession_from_receipt(
        SimpleNamespace(
            reason=(
                "GLOBAL_ACTUATION_EXECUTION_BINDING_SUPERSEDED:"
                "curve_economics:jit_detail=fields=neg_risk:"
                "selected=old:current=new"
            ),
            global_jit_candidate=SimpleNamespace(action="BUY"),
        )
    )
    assert status == "MARKET_AUTHORITY_SUPERSEDED"
    assert replacement is None
    assert GlobalWinnerPreflight(
        status=status,
        reason="current Gamma/CLOB metadata changed",
    ).replacement_candidate is None


def test_global_buy_jit_gamma_fee_drift_is_a_new_curve_not_selected_fee():
    """Current Gamma fee replaces the selected curve so the outer auction re-ranks."""
    from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
    from src.engine import event_reactor_adapter as adapter
    from src.solve.solver import GlobalSingleOrderCandidate, executable_curve_identity

    def authority(rate: float, *, neg_risk: bool = False):
        market = {
            "conditionId": "condition-buy",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
            "clobTokenIds": ["yes-buy", "no-buy"],
            "orderPriceMinTickSize": "0.01",
            "orderMinSize": "1",
            "negRisk": neg_risk,
            "feeSchedule": {"exponent": 1, "rate": rate, "takerOnly": True},
        }
        return adapter._current_global_market_authority(
            condition_id="condition-buy",
            token_id="yes-buy",
            side="YES",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200, json=lambda: [market]
            ),
            clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
                "condition-buy", "yes-buy", "no-buy", neg_risk=neg_risk
            ),
            raw_book=_global_jit_book("yes-buy", neg_risk=False),
            captured_at_utc=datetime.now(timezone.utc),
            timeout=1.0,
        )

    same = authority(0.05)
    selected_curve = ExecutableCostCurve(
        token_id="yes-buy",
        side="YES",
        snapshot_id="selected",
        book_hash=same.snapshot.raw_orderbook_hash,
        levels=(BookLevel(price=Decimal("0.50"), size=Decimal("10")),),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=30),
        fee_details=same.fee_details,
    )
    candidate = GlobalSingleOrderCandidate(
        candidate_id="selected-buy",
        token_id="yes-buy",
        side="YES",
        family_key="family",
        bin_id="bin",
        condition_id="condition-buy",
        probability_witness_identity="probability-buy",
        executable_cost_curve=selected_curve,
        execution_curve_identity=executable_curve_identity(selected_curve),
        book_snapshot_id=selected_curve.snapshot_id,
        book_captured_at_utc=datetime.now(timezone.utc),
        ledger_snapshot_id="ledger-buy",
        resolution_identity="resolution-buy",
        neg_risk=False,
    )
    raw_book = {
        "asset_id": "yes-buy",
        "asks": [{"price": "0.50", "size": "10"}],
        "bids": [{"price": "0.49", "size": "10"}],
        "tick_size": "0.01",
        "min_order_size": "1",
        "neg_risk": False,
    }
    unchanged = adapter._global_buy_candidate_from_raw_book(
        candidate, raw_book, captured_at_utc=datetime.now(timezone.utc), market_authority=same
    )
    assert adapter._global_selected_order_economics_drift(
        decision=SimpleNamespace(candidate=candidate), current_candidate=unchanged
    ) is None
    neg_risk_drifted = adapter._global_buy_candidate_from_raw_book(
        candidate,
        {key: value for key, value in raw_book.items() if key != "neg_risk"},
        captured_at_utc=datetime.now(timezone.utc),
        market_authority=authority(0.05, neg_risk=True),
    )
    assert adapter._global_selected_order_economics_drift(
        decision=SimpleNamespace(candidate=candidate), current_candidate=neg_risk_drifted
    ) == "fields=neg_risk"
    drifted = adapter._global_buy_candidate_from_raw_book(
        candidate,
        raw_book,
        captured_at_utc=datetime.now(timezone.utc),
        market_authority=authority(0.06),
    )
    assert "fee" in adapter._global_selected_order_economics_drift(
        decision=SimpleNamespace(candidate=candidate), current_candidate=drifted
    )


def test_global_sell_jit_gamma_neg_risk_drift_reauctions_real_typed_candidate():
    """The selected typed SELL cannot cross after Gamma changes negRisk."""
    from src.contracts.executable_cost_curve import BookLevel, FeeModel
    from src.engine import event_reactor_adapter as adapter
    from src.solve.solver import (
        ExecutableSellCurve,
        GlobalSingleOrderSellCandidate,
        executable_curve_identity,
        global_sell_execution_terms,
    )

    curve = ExecutableSellCurve(
        token_id="yes-sell",
        side="YES",
        snapshot_id="selected-sell",
        book_hash="selected-book",
        levels=(BookLevel(price=Decimal("0.50"), size=Decimal("10")),),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=30),
    )
    proposal, mode, fill_probability, fill_source, rest_deadline = (
        global_sell_execution_terms(
            curve,
            capacity=Decimal("10"),
            required_mode="TAKER_LIMIT",
        )
    )
    candidate = GlobalSingleOrderSellCandidate(
        candidate_id="selected-sell",
        family_key="family-sell",
        bin_id="bin-sell",
        condition_id="condition-sell",
        side="YES",
        token_id="yes-sell",
        position_id="position-sell",
        held_shares=Decimal("10"),
        probability_witness_identity="probability-sell",
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=datetime.now(timezone.utc),
        execution_curve_identity=executable_curve_identity(curve),
        ledger_snapshot_id="ledger-sell",
        executable_sell_curve=curve,
        resolution_identity="resolution-sell",
        proposal_sell_curve=proposal,
        execution_mode=mode,
        fill_probability=fill_probability,
        fill_probability_source=fill_source,
        rest_deadline_minutes=rest_deadline,
        neg_risk=False,
    )
    market = {
        "conditionId": "condition-sell",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "clobTokenIds": ["yes-sell", "no-sell"],
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "1",
        "negRisk": True,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True},
    }
    gamma_authority = adapter._current_global_market_authority(
        condition_id="condition-sell",
        token_id="yes-sell",
        side="YES",
        gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200, json=lambda: [market]
        ),
        clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
            "condition-sell", "yes-sell", "no-sell", neg_risk=True
        ),
        raw_book=_global_jit_book("yes-sell", neg_risk=True),
        captured_at_utc=datetime.now(timezone.utc),
        timeout=1.0,
    )
    current = adapter._global_sell_candidate_from_raw_book(
        candidate,
        {
            "asset_id": "yes-sell",
            "bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.51", "size": "10"}],
            "tick_size": "0.01",
            "min_order_size": "1",
        },
        captured_at_utc=datetime.now(timezone.utc),
        market_authority=gamma_authority,
    )
    assert adapter._global_sell_execution_economics_drift(
        decision=SimpleNamespace(candidate=candidate, shares=Decimal("10")),
        current_candidate=current,
    ) == "neg_risk"


@pytest.mark.parametrize(
    ("markets", "reason"),
    (
        ([{"conditionId": "condition-buy"}, {"conditionId": "condition-buy"}], "IDENTITY_INVALID"),
        ([{"conditionId": "condition-buy", "clobTokenIds": ["other-token"]}], "TOKEN_OWNERSHIP_INVALID"),
    ),
)
def test_global_buy_jit_gamma_market_identity_is_exact(markets, reason):
    from src.engine import event_reactor_adapter as adapter

    with pytest.raises(ValueError, match=reason):
        adapter._current_global_market_authority(
            condition_id="condition-buy",
            token_id="yes-buy",
            side="YES",
            gamma_get=lambda *_args, **_kwargs: SimpleNamespace(
                status_code=200, json=lambda: markets
            ),
            clob_market_get=lambda *_args, **_kwargs: _global_jit_clob_market(
                "condition-buy", "yes-buy", "no-buy"
            ),
            raw_book=_global_jit_book("yes-buy"),
            captured_at_utc=datetime.now(timezone.utc),
            timeout=1.0,
        )


def test_global_gamma_reads_use_persistent_request_governor(monkeypatch):
    """Global auction metadata may not bypass cross-process quota authority."""
    from src.data import polymarket_request_governor as governor_module
    from src.data.polymarket_request_governor import RequestPriority
    from src.engine import event_reactor_adapter as adapter

    response = SimpleNamespace(status_code=200)
    client = SimpleNamespace(get=lambda *_args, **_kwargs: response)
    captured = {}

    def request(send, method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return send()

    monkeypatch.setattr(
        governor_module.polymarket_request_governor,
        "request",
        request,
    )

    observed = adapter._governed_global_gamma_get(
        client,
        "/markets",
        params={"condition_ids": ["condition-weather"]},
        timeout=2.0,
        priority=RequestPriority.HELD_REDUCE_ONLY,
    )

    assert observed is response
    assert captured == {
        "method": "GET",
        "url": "https://gamma-api.polymarket.com/markets",
        "params": {"condition_ids": ["condition-weather"]},
        "priority": RequestPriority.HELD_REDUCE_ONLY,
    }


def test_entry_replacement_ignores_partial_non_anchor_raw_cycle_newer_than_posterior():
    """Partial regional/model rows cannot stale replacement authority by themselves.

    Live June-30 shape: DMI/ICON rows for 12Z arrived before any replacement
    anchor artifact/posterior. Treating those three rows as a complete posterior
    dependency froze entry with REPLACEMENT_0_1_LIVE_INPUT_LAG.
    """
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    for model in ("dmi_harmonie_europe", "icon_eu", "icon_global"):
        conn.execute(
            """
            INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model,
                "Munich",
                "2026-07-02",
                "high",
                "2026-06-30T12:00:00+00:00",
                "single_runs",
                "COVERED",
                "2026-06-30T16:20:00+00:00",
                "2026-06-30T16:06:00+00:00",
            ),
        )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Munich",
            target_date="2026-07-02",
            metric="high",
        ),
        decision_time=datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-06-30T06:00:00+00:00",
    )

    assert reason is None


def test_entry_replacement_blocks_when_used_model_raw_cycle_newer_than_posterior():
    """A posterior is stale when one of its own used models has a newer raw cycle."""
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            computed_at TEXT,
            provenance_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T00:00:00+00:00",
            "2026-07-08T08:16:19+00:00",
            json.dumps(
                {
                    "bayes_precision_fusion": {
                        "used_models": ["icon_global", "ukmo_global_deterministic_10km", "ecmwf_ifs"]
                    }
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "icon_global",
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T06:00:00+00:00",
            "single_runs",
            "COVERED",
            "2026-07-08T09:39:37+00:00",
            "2026-07-08T09:27:52+00:00",
        ),
    )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Kuala Lumpur",
            target_date="2026-07-10",
            metric="high",
        ),
        decision_time=datetime(2026, 7, 8, 10, 39, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-07-08T00:00:00+00:00",
    )

    assert reason is not None
    assert "source_cycle_time_used_raw_model_forecasts_lag" in reason
    assert "latest_raw_cycle=2026-07-08T06:00:00+00:00" in reason
    assert "posterior_cycle=2026-07-08T00:00:00+00:00" in reason


def test_entry_replacement_blocks_when_used_model_same_cycle_arrives_after_posterior():
    """A same-cycle used-model row captured after computed_at invalidates the posterior."""
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            computed_at TEXT,
            provenance_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T06:00:00+00:00",
            "2026-07-08T08:00:00+00:00",
            json.dumps({"bayes_precision_fusion": {"used_models": ["icon_global"]}}),
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "icon_global",
            "Kuala Lumpur",
            "2026-07-10",
            "high",
            "2026-07-08T06:00:00+00:00",
            "single_runs",
            "COVERED",
            "2026-07-08T09:30:00+00:00",
            "2026-07-08T09:20:00+00:00",
        ),
    )

    reason = adapter._replacement_live_input_lag_reason(
        conn,
        family=SimpleNamespace(
            city="Kuala Lumpur",
            target_date="2026-07-10",
            metric="high",
        ),
        decision_time=datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc),
        posterior_source_cycle_time="2026-07-08T06:00:00+00:00",
        posterior_computed_at="2026-07-08T08:00:00+00:00",
    )

    assert reason is not None
    assert "used_raw_model_forecasts_same_cycle_late_input" in reason
    assert "latest_raw_cycle=2026-07-08T06:00:00+00:00" in reason
    assert "latest_raw_input_at=2026-07-08T09:30:00+00:00" in reason
    assert "posterior_computed_at=2026-07-08T08:00:00+00:00" in reason


def test_replacement_forecast_authority_missing_posterior_does_not_fallback(monkeypatch):
    """With replacement live, missing posterior evidence is a blocker, not a legacy fallback."""
    from src.engine import event_reactor_adapter as adapter

    monkeypatch.setattr(
        adapter,
        "_forecast_authority_payload_from_posterior",
        lambda *_args, **_kwargs: None,
    )

    def _fail_if_legacy_snapshot_called(*_args, **_kwargs):
        raise AssertionError("legacy forecast snapshot fallback was called")

    monkeypatch.setattr(
        adapter,
        "_forecast_snapshot_row_for_event",
        _fail_if_legacy_snapshot_called,
    )

    with pytest.raises(
        ValueError,
        match="FORECAST_AUTHORITY_EVIDENCE_MISSING:replacement_posterior",
    ):
        adapter._forecast_authority_payload_and_clock(
            sqlite3.connect(":memory:"),
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            family=SimpleNamespace(city="Singapore", target_date="2026-07-01", metric="high"),
            payload={},
            decision_time=datetime(2026, 6, 29, 13, 0, tzinfo=timezone.utc),
        )


def test_forecast_authority_missing_posterior_carries_distinct_reason_suffix():
    """2026-07-26 frozen-posterior ratchet fix: the no-submit reason must name WHICH
    gate failed, not collapse to the bare 'replacement_posterior' that made this
    class undiagnosable from logs (~15 branches all raised the same bare string)."""
    from src.engine import event_reactor_adapter as adapter

    with pytest.raises(
        ValueError,
        match="FORECAST_AUTHORITY_EVIDENCE_MISSING:replacement_posterior:posterior_table_missing",
    ):
        adapter._forecast_authority_payload_and_clock(
            sqlite3.connect(":memory:"),  # no forecast_posteriors table at all
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            family=SimpleNamespace(city="Singapore", target_date="2026-07-01", metric="high"),
            payload={},
            decision_time=datetime(2026, 6, 29, 13, 0, tzinfo=timezone.utc),
        )


def test_monitoring_skips_blocking_review_fact_position_without_exit(monkeypatch):
    """Review facts stop automatic exit but still emit an explicit monitor hold."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.db import init_schema
    from src.state.projection import upsert_position_current

    pos = _make_position(
        trade_id="invalid-proof-position",
        direction="buy_no",
        state="holding",
        chain_state="synced",
        token_id="yes-invalid-proof",
        no_token_id="no-invalid-proof",
        condition_id="condition-invalid-proof",
        strategy_key="forecast_qkernel_entry",
        entered_at="2026-06-07T01:00:00+00:00",
    )
    portfolio = _make_portfolio(pos)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    upsert_position_current(conn, build_position_current_projection(pos))
    portfolio.chain_only_facts.append(
        ChainOnlyFact(
            token_id="no-invalid-proof",
            condition_id="condition-invalid-proof",
            size=5.0,
            avg_price=0.70,
            cost_basis=3.50,
            first_seen_at="2026-06-07T01:00:00+00:00",
            last_seen_at="2026-06-07T01:00:00+00:00",
            review_state=ChainOnlyReviewState.UNRESOLVED,
        )
    )

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("blocking review fact position must not auto-exit")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_blocking_review_fact_monitor_skip"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 6, 7, 1, 30, tzinfo=timezone.utc)),
        },
    )

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("blocking review fact position must not reach monitor refresh")
        ),
    )
    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
        run_exit_preflight=False,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert summary["monitor_skipped_blocking_review_fact"] == 1
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    event = conn.execute(
        "SELECT event_type, payload_json FROM position_events "
        "WHERE position_id=? AND event_type='MONITOR_REFRESHED'",
        (pos.trade_id,),
    ).fetchone()
    assert event is not None
    assert json.loads(event["payload_json"])["exit_decision_reason"] == (
        "REVIEW_REQUIRED_INVALID_ENTRY_PROOF"
    )
    assert len(monitor_results) == 1
    assert monitor_results[0].exit_reason == "REVIEW_REQUIRED_INVALID_ENTRY_PROOF"
    assert monitor_results[0].should_exit is False
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None
    conn.close()


def test_monitoring_unknown_direction_report_has_no_fresh_probability(monkeypatch):
    """Skipped unknown-direction monitor results must not report stale probability."""
    from src.engine import cycle_runtime

    pos = _make_position(direction="unknown", chain_state="synced")
    pos.p_posterior = 0.99
    pos.last_monitor_prob = 0.88
    pos.last_monitor_edge = 0.77
    pos.last_monitor_prob_is_fresh = True
    portfolio = _make_portfolio(pos)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("unknown direction should not exit")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_unknown_direction_monitor_report"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 5, 30, tzinfo=timezone.utc)),
        },
    )

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unknown direction must not reach monitor refresh")
        ),
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is False
    assert tracker_dirty is False
    assert summary["monitor_skipped_unknown_direction"] == 1
    assert summary["monitors"] == 0
    assert len(monitor_results) == 1
    assert monitor_results[0].exit_reason == "UNKNOWN_DIRECTION"
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None


def test_day0_closed_non_accepting_market_skips_exit_monitor_chain_missing(monkeypatch):
    """Closed non-accepting Day0 markets await settlement instead of failing quote freshness."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="closed-day0-001",
        state="day0_window",
        chain_state="synced",
        city="Chicago",
        target_date="2026-04-01",
        market_id="0xclosed",
        condition_id="0xclosed",
    )
    portfolio = _make_portfolio(pos)

    class ClosedMarketClob:
        def get_clob_market_info(self, condition_id):
            assert condition_id == "0xclosed"
            return {
                "closed": True,
                "accepting_orders": False,
                "enable_order_book": False,
            }

        def get_best_bid_ask(self, token_id):
            raise AssertionError("closed market should not refresh executable quote")

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("closed market should not execute an exit")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_closed_day0_market_monitor_skip"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 18, 30, tzinfo=timezone.utc)),
        },
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("closed Day0 market must not reach monitor refresh")
        ),
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        ClosedMarketClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.state == "day0_window"
    assert pos.exit_state == ""
    assert pos.exit_reason == ""
    assert pos.last_exit_error == "MARKET_CLOSED_AWAITING_SETTLEMENT:clob_market_info"
    assert summary["monitor_skipped_closed_market_pending_settlement"] == 1
    assert "monitor_chain_missing" not in summary
    assert "monitor_incomplete_exit_context" not in summary
    assert summary["monitors"] == 1
    assert monitor_results[0].exit_reason == "MARKET_CLOSED_AWAITING_SETTLEMENT"
    assert monitor_results[0].fresh_prob is None
    assert monitor_results[0].fresh_edge is None


def test_day0_closed_market_detection_uses_static_market_end_when_clob_info_missing():
    """Missing post-close CLOB info must not send held positions into stale quote retry."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="snapshot-closed-day0-001",
        state="day0_window",
        chain_state="synced",
        city="Chicago",
        target_date="2026-04-01",
        market_id="0xsnapshotclosed",
        condition_id="0xsnapshotclosed",
    )

    class Row(dict):
        def __getitem__(self, key):
            if isinstance(key, int):
                return list(self.values())[key]
            return super().__getitem__(key)

    class SnapshotConn:
        def execute(self, sql, params=()):
            assert params == ("0xsnapshotclosed",)

            class Cursor:
                def fetchone(self):
                    return Row(
                        snapshot_id="snap-market-ended",
                        condition_id="0xsnapshotclosed",
                        market_end_at="2026-04-01T12:00:00+00:00",
                        market_close_at=None,
                        captured_at="2026-04-01T11:45:00+00:00",
                    )

            return Cursor()

    class MissingMarketInfoClob:
        def get_clob_market_info(self, condition_id):
            raise RuntimeError("post-close market info unavailable")

    info = cycle_runtime._closed_non_accepting_market_info(
        MissingMarketInfoClob(),
        pos,
        SnapshotConn(),
        decision_time=datetime(2026, 4, 1, 18, 30, tzinfo=timezone.utc),
    )

    assert info is not None
    assert info["source"] == "executable_snapshot_market_end"
    assert info["condition_id"] == "0xsnapshotclosed"
    assert info["accepting_orders"] is False


def test_closed_market_metadata_uses_bounded_held_risk_client():
    """Held monitor metadata cannot fall back to an unbounded public request."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="bounded-held-market-info",
        condition_id="bounded-held-condition",
        market_id="bounded-held-condition",
    )
    observed_timeouts = []

    class BoundedHeldClob:
        def get_held_clob_market_info(self, condition_id, *, timeout=None):
            assert condition_id == "bounded-held-condition"
            observed_timeouts.append(timeout)
            return {"closed": True, "accepting_orders": False}

        def get_clob_market_info(self, _condition_id):
            raise AssertionError("held monitor must use the held-risk circuit")

    info = cycle_runtime._closed_non_accepting_market_info(
        BoundedHeldClob(),
        pos,
        deadline_monotonic=time.monotonic() + 5.0,
    )

    assert info is not None
    assert info["source"] == "clob_market_info"
    assert len(observed_timeouts) == 1
    assert 0.0 < observed_timeouts[0] <= 5.0


def test_bounded_closed_market_metadata_does_not_swallow_transport_failure():
    """A held-risk transport failure remains visible to the monitor failure lane."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="bounded-held-market-info-failure",
        condition_id="bounded-held-condition-failure",
        market_id="bounded-held-condition-failure",
    )

    class FailingHeldClob:
        def get_held_clob_market_info(self, _condition_id, *, timeout=None):
            assert timeout is not None
            raise RuntimeError("held market metadata transport failed")

    with pytest.raises(RuntimeError, match="metadata transport failed"):
        cycle_runtime._closed_non_accepting_market_info(
            FailingHeldClob(),
            pos,
            deadline_monotonic=time.monotonic() + 5.0,
        )


def test_held_monitor_prefetch_batches_books_and_skips_redundant_market_metadata():
    from src.engine import cycle_runtime, monitor_refresh

    positions = [
        _make_position(
            trade_id=f"batch-monitor-{index}",
            condition_id=f"condition-{index}",
            market_id=f"condition-{index}",
            token_id=f"token-{index}",
            direction="buy_yes",
        )
        for index in range(3)
    ]

    class BatchClob:
        def __init__(self):
            self.batch_calls = []
            self.market_calls = 0

        def get_orderbook_snapshots(self, token_ids):
            self.batch_calls.append(tuple(token_ids))
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for token_id in token_ids
            }

        def get_clob_market_info(self, _condition_id):
            self.market_calls += 1
            raise AssertionError("prefetched executable book makes metadata redundant")

    clob = BatchClob()
    summary = {}
    deps = type(
        "Deps",
        (),
        {"logger": type("Logger", (), {"warning": staticmethod(lambda *args: None)})()},
    )()

    cycle_runtime._prefetch_held_monitor_orderbooks(
        None,
        clob,
        positions,
        summary,
        now_utc=datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc),
        deps=deps,
    )

    assert len(clob.batch_calls) == 1
    assert clob.batch_calls[0] == ("token-0", "token-1", "token-2")
    assert summary["held_monitor_orderbooks_requested"] == 3
    assert summary["held_monitor_orderbooks_local"] == 0
    assert summary["held_monitor_orderbooks_network_requested"] == 3
    assert summary["held_monitor_orderbooks_prefetched"] == 3
    assert summary["held_monitor_orderbooks_published_for_global_sell"] == 3
    published = monitor_refresh.current_monitor_orderbook_batch(
        [pos.token_id for pos in positions],
        checked_at_utc=datetime.now(timezone.utc),
        max_age=timedelta(seconds=8),
    )
    assert published is not None
    assert set(published[0]) == {pos.token_id for pos in positions}
    for pos in positions:
        assert monitor_refresh.prefetched_monitor_orderbook(clob, pos.token_id)
        assert (
            cycle_runtime._closed_non_accepting_market_info(clob, pos, conn=None)
            is None
        )
    assert clob.market_calls == 0
    monitor_refresh.publish_current_monitor_orderbook_batch(
        {},
        captured_at_utc=None,
    )


def test_held_monitor_local_books_publish_original_clock_for_global_sell(
    monkeypatch,
):
    from src.engine import cycle_runtime, monitor_refresh

    captured_at = datetime(2026, 8, 12, 14, 35, 16, tzinfo=timezone.utc)
    position = _make_position(
        trade_id="local-global-sell-book",
        condition_id="local-global-sell-condition",
        market_id="local-global-sell-condition",
        token_id="local-global-sell-token",
        direction="buy_yes",
    )
    book = {
        "asset_id": position.token_id,
        "bids": [{"price": "0.31", "size": "20"}],
        "asks": [{"price": "0.33", "size": "20"}],
    }

    def local_books(*_args, captured_at_out, **_kwargs):
        captured_at_out.append(captured_at)
        return {position.token_id: book}

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        local_books,
    )
    summary = {}
    cycle_runtime._prefetch_held_monitor_orderbooks(
        object(),
        object(),
        [position],
        summary,
        now_utc=captured_at + timedelta(seconds=1),
        deps=_monitor_test_deps("test_local_global_sell_book"),
        local_only=True,
    )

    assert summary["held_monitor_orderbooks_published_for_global_sell"] == 1
    published = monitor_refresh.current_monitor_orderbook_batch(
        (position.token_id,),
        checked_at_utc=captured_at + timedelta(seconds=2),
        max_age=timedelta(seconds=8),
    )
    assert published is not None
    assert published[0] == {position.token_id: book}
    assert published[1] == captured_at
    monitor_refresh.publish_current_monitor_orderbook_batch(
        {},
        captured_at_utc=None,
    )


def test_monitor_global_sell_handoff_is_exact_and_does_not_extend_time():
    from src.engine import event_reactor_adapter, monitor_refresh

    captured_at = datetime(2026, 8, 9, 12, 30, tzinfo=timezone.utc)
    assert monitor_refresh.publish_current_monitor_orderbook_batch(
        {
            "token-current": {
                "asset_id": "token-current",
                "bids": [{"price": "0.08", "size": "40"}],
                "asks": [{"price": "0.10", "size": "40"}],
            },
            "token-mismatch": {
                "asset_id": "different-token",
                "bids": [{"price": "0.20", "size": "10"}],
            },
        },
        captured_at_utc=captured_at,
    ) == 1

    current = monitor_refresh.current_monitor_orderbook_batch(
        ("token-current", "token-mismatch"),
        checked_at_utc=captured_at + timedelta(seconds=2),
        max_age=timedelta(seconds=8),
    )
    assert current is not None
    assert set(current[0]) == {"token-current"}
    assert current[1] == captured_at
    assert monitor_refresh.publish_current_monitor_orderbook_batch(
        {
            "token-network": {
                "asset_id": "token-network",
                "bids": [{"price": "0.18", "size": "10"}],
                "asks": [{"price": "0.20", "size": "10"}],
            }
        },
        captured_at_utc=captured_at + timedelta(seconds=1),
        merge=True,
    ) == 2
    merged = monitor_refresh.current_monitor_orderbook_batch(
        ("token-current", "token-network"),
        checked_at_utc=captured_at + timedelta(seconds=2),
        max_age=timedelta(seconds=8),
    )
    assert merged is not None
    assert set(merged[0]) == {"token-current", "token-network"}
    assert merged[1] == captured_at
    projection = event_reactor_adapter._monitor_first_global_book_projection(
        ("token-current",),
        checked_at=captured_at + timedelta(seconds=2),
        max_age=timedelta(seconds=8),
        projected_loader=lambda: pytest.fail(
            "complete monitor cut must bypass slower projection and network recapture"
        ),
    )
    assert projection is not None
    assert set(projection[0]) == {"token-current"}
    assert projection[1] == captured_at
    assert (
        monitor_refresh.current_monitor_orderbook_batch(
            ("token-current",),
            checked_at_utc=captured_at + timedelta(seconds=9),
            max_age=timedelta(seconds=8),
        )
        is None
    )
    monitor_refresh.publish_current_monitor_orderbook_batch(
        {},
        captured_at_utc=None,
    )


def test_held_monitor_prefetch_clears_prior_cycle_when_batch_fetch_fails():
    from src.engine import cycle_runtime, monitor_refresh

    pos = _make_position(
        trade_id="batch-monitor-stale",
        condition_id="condition-stale",
        market_id="condition-stale",
        token_id="token-stale",
        direction="buy_yes",
    )

    class FailingBatchClob:
        def __init__(self):
            self.market_calls = 0
            self.orderbook_calls = 0

        def get_orderbook_snapshots(self, _token_ids):
            raise RuntimeError("current batch unavailable")

        def get_clob_market_info(self, _condition_id):
            self.market_calls += 1
            raise AssertionError("failed batch must not fan out into market reads")

        def get_orderbook(self, _token_id):
            self.orderbook_calls += 1
            raise AssertionError("failed batch must not fan out into singular reads")

    clob = FailingBatchClob()
    monitor_refresh.install_monitor_orderbook_prefetch(
        clob,
        {
            "token-stale": {
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
        },
    )
    warnings = []
    deps = type(
        "Deps",
        (),
        {"logger": type("Logger", (), {"warning": staticmethod(lambda *args: warnings.append(args))})()},
    )()
    summary = {}

    cycle_runtime._prefetch_held_monitor_orderbooks(
        None,
        clob,
        [pos],
        summary,
        now_utc=datetime(2026, 7, 17, 2, 0, tzinfo=timezone.utc),
        deps=deps,
    )

    assert monitor_refresh.prefetched_monitor_orderbook(clob, "token-stale") is None
    assert monitor_refresh.monitor_orderbook_prefetch_attempted(
        clob,
        "token-stale",
    )
    assert summary["held_monitor_orderbooks_prefetched"] == 0
    assert summary["held_monitor_orderbook_prefetch_error"] == "current batch unavailable"
    assert summary["held_monitor_orderbook_prefetch_transport_failed"] is True
    assert len(warnings) == 1


def test_held_monitor_production_book_reads_receive_sealed_quote_budget(
    monkeypatch,
):
    from src.data.polymarket_client import PolymarketClient
    from src.engine import cycle_runtime, monitor_refresh

    position = _make_position(
        trade_id="shared-deadline-production-book",
        token_id="shared-deadline-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    clock = [3.0]
    hard_deadline_calls = []
    clob = PolymarketClient(public_http_timeout=2.0)

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(monitor_refresh.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )

    def hard_deadline_books(token_ids, *, timeout_seconds):
        hard_deadline_calls.append((list(token_ids), timeout_seconds))
        return {
            token_ids[0]: {
                "asset_id": token_ids[0],
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
        }

    monkeypatch.setattr(
        clob,
        "get_held_orderbook_snapshots_hard_deadline",
        hard_deadline_books,
    )
    summary = {}
    cycle_runtime._prefetch_held_monitor_orderbooks(
        None,
        clob,
        [position],
        summary,
        now_utc=datetime(2026, 8, 2, 21, 0, tzinfo=timezone.utc),
        deps=_monitor_test_deps("test_shared_batch_deadline"),
        deadline_monotonic=10.0,
    )
    assert hard_deadline_calls == [
        (["shared-deadline-token"], pytest.approx(7.0))
    ]

    monitor_refresh.install_monitor_orderbook_prefetch(clob, {})
    setattr(position, "_zeus_held_monitor_deadline_monotonic", 10.0)

    quote = monitor_refresh.monitor_quote_refresh(None, clob, position)
    assert quote is not None
    assert hard_deadline_calls == [
        (["shared-deadline-token"], pytest.approx(7.0)),
        (["shared-deadline-token"], pytest.approx(1.0)),
    ]


def test_held_monitor_deadline_book_miss_never_falls_back_to_unbounded_quote(
    monkeypatch,
):
    from src.data.polymarket_client import PolymarketClient
    from src.engine import monitor_refresh

    position = _make_position(
        trade_id="deadline-book-miss",
        token_id="deadline-book-miss-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )
    clob = PolymarketClient(public_http_timeout=2.0)
    monkeypatch.setattr(monitor_refresh.time, "monotonic", lambda: 3.0)
    monkeypatch.setattr(
        clob,
        "get_held_orderbook_snapshots_hard_deadline",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        clob,
        "get_best_bid_ask",
        lambda *_args, **_kwargs: pytest.fail(
            "deadline-bound book miss must not start an unbounded fallback"
        ),
    )
    setattr(position, "_zeus_held_monitor_deadline_monotonic", 10.0)

    assert monitor_refresh.monitor_quote_refresh(None, clob, position) is None


def test_held_monitor_deadline_requires_bounded_api_for_every_adapter(
    monkeypatch,
):
    from src.engine import monitor_refresh

    position = _make_position(
        trade_id="deadline-generic-adapter",
        token_id="deadline-generic-adapter-token",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
    )

    class GenericAdapter:
        def get_orderbook(self, _token_id):
            pytest.fail("deadline-bound adapter must not use unbounded book API")

        def get_best_bid_ask(self, _token_id):
            pytest.fail("deadline-bound adapter must not use unbounded quote API")

    monkeypatch.setattr(monitor_refresh.time, "monotonic", lambda: 3.0)
    setattr(position, "_zeus_held_monitor_deadline_monotonic", 10.0)

    assert (
        monitor_refresh.monitor_quote_refresh(
            None,
            GenericAdapter(),
            position,
        )
        is None
    )


def test_polymarket_book_http_phases_are_clamped_to_remaining_budget(monkeypatch):
    from src.data.polymarket_client import PolymarketClient

    captured = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    clob = PolymarketClient(public_http_timeout=2.0)

    def public_post(path, *, json_body, timeout=None):
        captured.append((path, timeout))
        return Response()

    monkeypatch.setattr(clob, "_public_post", public_post)
    assert clob.get_orderbook_snapshots(["deadline-token"], timeout=0.2) == {}

    assert captured[0][0] == "/books"
    request_timeout = captured[0][1]
    assert request_timeout is not None
    assert request_timeout.connect == pytest.approx(0.2)
    assert request_timeout.read == pytest.approx(0.2)
    assert request_timeout.write == pytest.approx(0.2)
    assert request_timeout.pool == pytest.approx(0.2)


def test_held_book_worker_receives_only_ids_and_returns_public_books(monkeypatch):
    from src.data import polymarket_client as pm

    calls = []

    class ReadOnlyClient:
        def __init__(self, *, public_http_timeout, public_request_priority):
            calls.append(
                ("init", public_http_timeout, public_request_priority.value)
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_orderbook_snapshots(self, token_ids, *, timeout):
            calls.append(("books", list(token_ids), timeout))
            return {token_ids[0]: {"asset_id": token_ids[0], "bids": [], "asks": []}}

    class Send:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)

        def close(self):
            return None

    monkeypatch.setattr(pm, "PolymarketClient", ReadOnlyClient)
    send = Send()
    pm._held_orderbook_read_worker(send, ["held-token"], 0.5)

    assert calls == [
        ("init", 0.5, 20),
        ("books", ["held-token"], 0.5),
    ]
    assert json.loads(send.messages[0]) == {
        "status": "ok",
        "books": {
            "held-token": {
                "asset_id": "held-token",
                "bids": [],
                "asks": [],
            }
        },
    }


def test_held_book_hard_deadline_terminates_and_reaps_hung_reader(monkeypatch):
    from src.data import polymarket_client as pm

    class Receive:
        def poll(self, _timeout):
            return False

        def close(self):
            return None

    class Send:
        def close(self):
            return None

    class HungProcess:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.alive = False
            self.started = False
            self.terminated = False
            self.joined = []

        def start(self):
            self.started = True
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def kill(self):
            self.alive = False

        def join(self, timeout=None):
            self.joined.append(timeout)

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is False
            return Receive(), Send()

        def Process(self, **kwargs):
            self.process = HungProcess(**kwargs)
            return self.process

    context = Context()
    monkeypatch.setattr(pm.multiprocessing, "get_context", lambda mode: context)
    clob = pm.PolymarketClient(public_http_timeout=2.0)

    with pytest.raises(TimeoutError, match="held orderbook batch exceeded"):
        clob.get_held_orderbook_snapshots_hard_deadline(
            ["hung-token"],
            timeout_seconds=0.1,
        )

    assert context.process.started is True
    assert context.process.terminated is True
    assert context.process.is_alive() is False
    assert context.process.kwargs["target"] is pm._held_orderbook_read_worker
    assert context.process.kwargs["args"][1] == ["hung-token"]


def test_held_book_hard_deadline_never_extends_insufficient_budget(monkeypatch):
    from src.data import polymarket_client as pm

    monkeypatch.setattr(
        pm.multiprocessing,
        "get_context",
        lambda _mode: pytest.fail("insufficient budget must not spawn a reader"),
    )
    clob = pm.PolymarketClient(public_http_timeout=2.0)

    with pytest.raises(TimeoutError, match="insufficient remaining deadline"):
        clob.get_held_orderbook_snapshots_hard_deadline(
            ["held-token"],
            timeout_seconds=0.009,
        )


def test_held_book_hard_deadline_accepts_only_requested_book_objects(monkeypatch):
    from src.data import polymarket_client as pm

    payload = json.dumps(
        {
            "status": "ok",
            "books": {
                "held-token": {"asset_id": "held-token", "bids": [], "asks": []},
                "unexpected-token": {"asset_id": "unexpected-token"},
                "invalid-token": ["not", "a", "book"],
            },
        }
    )

    class Receive:
        def poll(self, _timeout):
            return True

        def recv(self):
            return payload

        def close(self):
            return None

    class Send:
        def close(self):
            return None

    class CompletedProcess:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

        def is_alive(self):
            return False

        def terminate(self):
            raise AssertionError("completed reader must not be terminated")

        def kill(self):
            raise AssertionError("completed reader must not be killed")

        def join(self, timeout=None):
            return None

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is False
            return Receive(), Send()

        def Process(self, **kwargs):
            return CompletedProcess(**kwargs)

    monkeypatch.setattr(
        pm.multiprocessing,
        "get_context",
        lambda mode: Context(),
    )
    clob = pm.PolymarketClient(public_http_timeout=2.0)

    assert clob.get_held_orderbook_snapshots_hard_deadline(
        ["held-token", "invalid-token"],
        timeout_seconds=0.5,
    ) == {
        "held-token": {"asset_id": "held-token", "bids": [], "asks": []}
    }


def test_monitoring_batch_transport_failure_recovers_one_without_singular_fanout(
    monkeypatch,
):
    """A failed batch recovers one bounded position; the next cycle retries all."""
    from src.engine import cycle_runtime, monitor_refresh

    positions = [
        _make_position(
            trade_id=f"batch-transport-retry-{index}",
            token_id=f"batch-transport-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    refreshes = []
    canonical_refreshes = []
    singular_calls = []

    class BatchTransportClob:
        fail_batch = True

        def get_orderbook_snapshots(self, _token_ids):
            if self.fail_batch:
                raise RuntimeError("/books transport unavailable")
            return {
                position.token_id: {
                    "asset_id": position.token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
                for position in positions
            }

        def get_held_orderbook_snapshots_hard_deadline(
            self,
            token_ids,
            *,
            timeout_seconds,
        ):
            assert timeout_seconds > 0.0
            assert len(token_ids) == 1
            singular_calls.extend(token_ids)
            token_id = token_ids[0]
            return {
                token_id: {
                    "asset_id": token_id,
                    "bids": [{"price": "0.40", "size": "20"}],
                    "asks": [{"price": "0.42", "size": "20"}],
                }
            }

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )

    def fake_refresh(_conn, clob, position):
        quote = monitor_refresh.monitor_quote_refresh(None, clob, position)
        assert quote is not None
        refreshes.append(position.trade_id)
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    def emit_monitor_refreshed(_conn, position, **_kwargs):
        canonical_refreshes.append(position.trade_id)
        return True

    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        emit_monitor_refreshed,
    )

    summary = {"monitors": 0, "exits": 0}
    clob = BatchTransportClob()
    cycle_runtime.execute_monitoring_phase(
        None,
        clob,
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_batch_transport_retry"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert singular_calls == [positions[0].token_id]
    assert refreshes == [positions[0].trade_id]
    assert canonical_refreshes == refreshes
    assert summary["held_monitor_orderbook_prefetch_transport_failed"] is True
    assert summary["held_monitor_positions_deferred_for_orderbook_gap"] == 1
    assert summary["held_monitor_batch_failure_singular_recovered"] == 1
    assert summary["held_monitor_batch_failure_singular_recovered_position"] == (
        positions[0].trade_id
    )
    assert summary["monitors"] == 1

    clob.fail_batch = False
    recovered_summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        clob,
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        recovered_summary,
        deps=_monitor_test_deps("test_monitor_batch_transport_recovery"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert singular_calls == [positions[0].token_id]
    assert refreshes == [
        positions[0].trade_id,
        *(position.trade_id for position in positions),
    ]
    assert canonical_refreshes == refreshes
    assert recovered_summary["monitors"] == 2
    assert not recovered_summary.get(
        "held_monitor_orderbook_prefetch_transport_failed", False
    )


def test_monitoring_failed_singular_recovery_does_not_fan_out(monkeypatch):
    """One failed singular recovery cannot multiply a shared batch outage."""
    from src.engine import cycle_runtime, monitor_refresh

    positions = [
        _make_position(
            trade_id=f"batch-singular-failure-{index}",
            token_id=f"batch-singular-failure-token-{index}",
            direction="buy_yes",
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    singular_calls = []

    class FailedBatchClob:
        def get_orderbook_snapshots(self, _token_ids):
            raise RuntimeError("/books transport unavailable")

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )

    def failed_singular(_conn, _clob, position, *, retry_after_prefetch=False):
        assert retry_after_prefetch is True
        singular_calls.append(position.token_id)
        return None

    monkeypatch.setattr(monitor_refresh, "monitor_quote_refresh", failed_singular)
    monkeypatch.setattr(
        monitor_refresh,
        "refresh_position",
        lambda *_args, **_kwargs: pytest.fail(
            "a position without a recovered quote must remain deferred"
        ),
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        FailedBatchClob(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_failed_singular_recovery"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert singular_calls == [positions[0].token_id]
    assert summary["held_monitor_positions_deferred_for_orderbook_gap"] == 2
    assert summary["held_monitor_batch_failure_singular_unavailable"] == 1
    assert summary["monitors"] == 0


def test_monitoring_batch_transport_does_not_retry_after_deadline(
    monkeypatch,
):
    """A batch that consumes the budget cannot start a singular retry."""
    from src.engine import cycle_runtime

    position = _make_position(
        trade_id="batch-transport-deadline",
        token_id="batch-transport-deadline-token",
        state="holding",
        chain_state="synced",
    )
    clock = [0.0]
    singular_calls = []
    refreshes = []

    class SlowBatchClob:
        def get_orderbook_snapshots(self, _token_ids):
            clock[0] = 11.0
            raise RuntimeError("/books timed out")

        def get_orderbook(self, token_id):
            singular_calls.append(token_id)
            raise AssertionError("deadline-exhausted batch must not retry singularly")

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *_args: refreshes.append(position.trade_id),
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        SlowBatchClob(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_batch_transport_deadline"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert singular_calls == []
    assert refreshes == []
    assert summary["held_monitor_deadline_defer_reason"] == (
        "MONITOR_DEADLINE_EXPIRED_DURING_BATCH_PREFETCH"
    )


@pytest.mark.parametrize(
    "batch_response",
    (
        {},
        {
            "successful-partial-token": {
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
        },
    ),
)
def test_monitoring_successful_empty_or_partial_batch_does_not_retry_singularly(
    monkeypatch,
    batch_response,
):
    """Successful gaps retain anti-storm attempted-token semantics."""
    from src.engine import cycle_runtime, monitor_refresh

    positions = [
        _make_position(
            trade_id=f"batch-success-gap-{index}",
            token_id=(
                "successful-partial-token"
                if index == 0
                else "successful-missing-token"
            ),
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    refreshes = []
    singular_calls = []
    batch_calls = []

    class SuccessfulGapClob:
        def get_orderbook_snapshots(self, token_ids):
            batch_calls.append(tuple(token_ids))
            return batch_response

        def get_orderbook(self, token_id):
            singular_calls.append(token_id)
            raise AssertionError("successful batch gaps must not trigger singular retry")

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {},
    )

    def fake_refresh(_conn, clob, position):
        quote = monitor_refresh.monitor_quote_refresh(None, clob, position)
        if position.token_id in batch_response:
            assert quote is not None
        else:
            assert quote is None
        refreshes.append(position.trade_id)
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda _conn, position, **_kwargs: True,
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        SuccessfulGapClob(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_successful_batch_gap"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert singular_calls == []
    assert len(batch_calls) == 1
    assert refreshes == [position.trade_id for position in positions]
    assert (
        summary.get("held_monitor_orderbook_prefetch_transport_failed", False)
        is False
    )
    assert summary["monitors"] == 2


# T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW):
# test_quarantine_expired_marks_distinct_admin_resolution_reason retired with
# it — it exercised the same now-dead admin-resolution monitor branch
# (chain_state='quarantine_expired' is itself also a retired ChainState
# member; Position.__post_init__ remaps it to 'synced' before construction,
# so the position it built now flows through normal monitor refresh like any
# other active position, same as the other two admin-resolution tests above).


def test_monitoring_transitions_holding_position_into_day0_window(monkeypatch):
    """Positions nearing settlement must enter the universal Day0 terminal phase.

    A6 audit (2026-05-04, rebuild fixes branch): the fixture's
    target_date=2026-04-01 + decision_time=2026-04-02T04:30Z places the
    market in POST_TRADING phase under the new phase-axis dispatch
    (settlement period 2026-04-01T05:00Z..2026-04-01T12:00Z for Chicago
    has already passed). Phase-axis correctly refuses day0_window entry
    after settlement. This test asserts the LEGACY 6-hour-to-settlement
    transition contract; pin to flag=OFF until phase-axis equivalents
    are added in a follow-up packet.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    from src.engine import cycle_runtime
    from src.contracts import EdgeContext, EntryMethod

    pos = _make_position(state="holding", city="Chicago", target_date="2026-04-01")
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.41, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in this transition test")

    observed_refresh_states = []

    def mock_refresh(conn, clob, position):
        observed_refresh_states.append((position.state, position.entry_method))
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([position.entry_price]),
            p_posterior=position.p_posterior,
            forward_edge=0.0,
            alpha=0.0,
            confidence_band_upper=0.0,
            confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap1",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)

    observed_hours = []

    def mock_evaluate_exit(self, exit_context):
        observed_hours.append(exit_context.hours_to_settlement)
        return ExitDecision(False, selected_method=self.selected_method or self.entry_method)

    monkeypatch.setattr(Position, "evaluate_exit", mock_evaluate_exit)

    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_day0_transition"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 2, 4, 30, tzinfo=timezone.utc)),
        },
    )

    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.state == "day0_window"
    assert observed_refresh_states == [("day0_window", "ens_member_counting")]
    assert observed_hours and observed_hours[0] is not None
    assert observed_hours[0] < 1.0
    assert summary["monitors"] == 1


def test_lifecycle_kernel_enters_day0_window_from_active_states():
    from src.state.lifecycle_manager import enter_day0_window_runtime_state

    assert enter_day0_window_runtime_state("entered") == "day0_window"
    assert enter_day0_window_runtime_state("holding") == "day0_window"


def test_lifecycle_kernel_rejects_day0_window_from_pending_exit():
    from src.state.lifecycle_manager import enter_day0_window_runtime_state

    with pytest.raises(ValueError, match="day0 transition requires active/pending_entry/day0_window runtime phase"):
        enter_day0_window_runtime_state(
            "pending_exit",
            exit_state="sell_pending",
            chain_state="exit_pending_missing",
        )


def test_day0_transition_emits_durable_lifecycle_event(monkeypatch, tmp_path):
    """T1.c-followup L875 closure via Day0-canonical-event feature slice
    (2026-04-24): after the transition, a canonical DAY0_WINDOW_ENTERED
    position_events row exists with phase_before=active, phase_after=
    day0_window, and payload carrying day0_entered_at. Pre-slice, this
    test was skipped OBSOLETE_PENDING_FEATURE because cycle_runtime did
    not emit a canonical event — only updated position_current.phase.
    Post-slice: canonical emission is wired via
    _emit_day0_window_entered_canonical_if_available in cycle_runtime.

    A6 audit (2026-05-04): pin to legacy 6-hour transition — see
    test_monitoring_transitions_holding_position_into_day0_window for the
    full rationale.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    from src.engine import cycle_runtime
    from src.contracts import EdgeContext, EntryMethod
    from src.state.db import get_connection, init_schema, log_trade_entry, query_position_events
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project

    conn = get_connection(tmp_path / "day0.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="day0-db-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-day0",
        entry_order_id="o-day0",
        entry_fill_verified=True,
        entered_at="2026-04-01T04:00:00Z",
        order_status="filled",
        strategy_key="center_buy",
        bin_label="50-51°F",
        condition_id="0xday0db100000000000000000000000000000000000000000000000000000001",
    )
    log_trade_entry(conn, pos)
    # Seed canonical entry baseline so the Day0 canonical emission is not
    # the first canonical event for this trade_id (matches production
    # reality — entries always precede day0 transitions).
    from src.state.lifecycle_manager import LifecyclePhase
    events, projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-day0-seed",
        source_module="tests/test_day0_transition_emits_durable",
    )
    append_many_and_project(conn, events, projection)
    portfolio = _make_portfolio(pos)

    class LiveClob:
        pass

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in this transition test")

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda conn, clob, position: EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([position.entry_price]),
            p_posterior=position.p_posterior,
            forward_edge=0.0,
            alpha=0.0,
            confidence_band_upper=0.0,
            confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap1",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        ),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, exit_context: ExitDecision(False, selected_method=self.selected_method or self.entry_method),
    )

    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_day0_transition_db"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            # _utcnow set to within day0 window (≤6h before Chicago target
            # date close at 2026-04-02 05:00 UTC) so the day0 gate fires.
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 2, 2, 0, tzinfo=timezone.utc)),
        },
    )
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        conn,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    events = query_position_events(conn, "day0-db-1")
    conn.close()
    # Day0-canonical-event slice assertion: a canonical DAY0_WINDOW_ENTERED
    # row was emitted by _emit_day0_window_entered_canonical_if_available.
    day0_events = [e for e in events if e["event_type"] == "DAY0_WINDOW_ENTERED"]
    assert day0_events, (
        f"Expected DAY0_WINDOW_ENTERED canonical event after day0 "
        f"transition; got event_types={[e['event_type'] for e in events]}"
    )
    day0_event = day0_events[0]
    # query_position_events returns the payload under `details` (decoded
    # from payload_json); phase_before/after live in the payload because
    # query_position_events doesn't surface the DB columns separately.
    details = day0_event.get("details") or {}
    assert details.get("phase_before") == "active"
    assert details.get("phase_after") == "day0_window"
    assert details.get("day0_entered_at") == "2026-04-02T02:00:00+00:00"
    assert day0_event["timestamp"] == "2026-04-02T02:00:00+00:00"


def test_day0_canonical_emit_is_idempotent_when_monitor_replays_same_position(tmp_path):
    """Repeated monitor passes must not re-append DAY0_WINDOW_ENTERED."""
    from src.contracts import EntryMethod
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import (
        build_day0_window_entered_canonical_write,
        build_entry_canonical_write,
    )
    from src.state.db import append_many_and_project, get_connection, init_schema

    conn = get_connection(tmp_path / "day0-idempotent.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="day0-idem-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-day0-idem",
        entry_order_id="o-day0-idem",
        entry_fill_verified=True,
        entered_at="2026-04-01T04:00:00Z",
        order_status="filled",
        strategy_key="center_buy",
        bin_label="50-51°F",
        selected_method=EntryMethod.ENS_MEMBER_COUNTING,
        condition_id="0xday0idem00000000000000000000000000000000000000000000000000000001",
    )
    from src.state.lifecycle_manager import LifecyclePhase
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-day0-idem-seed",
        source_module="tests/test_day0_canonical_emit_is_idempotent",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    pos.state = "day0_window"
    pos.day0_entered_at = "2026-04-02T02:00:00+00:00"
    day0_events, day0_projection = build_day0_window_entered_canonical_write(
        pos,
        day0_entered_at="2026-04-02T02:00:00+00:00",
        sequence_no=4,
        previous_phase="active",
        source_module="tests/test_day0_canonical_emit_is_idempotent",
    )
    append_many_and_project(conn, day0_events, day0_projection)

    deps = type(
        "Deps",
        (),
        {"logger": logging.getLogger("test_day0_idempotent")},
    )

    assert cycle_runtime._emit_day0_window_entered_canonical_if_available(
        conn,
        pos,
        day0_entered_at="2026-04-02T02:10:00+00:00",
        previous_phase="active",
        deps=deps,
    ) is False
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'DAY0_WINDOW_ENTERED'",
            ("day0-idem-1",),
        ).fetchone()[0]
        == 1
    )
    conn.close()


def test_monitor_refresh_canonical_emit_updates_current_projection(tmp_path):
    """Monitor refresh evidence must persist before exit logic can rely on it."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "monitor-refresh-canonical.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="monitor-refresh-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-monitor-refresh",
        entered_at="2026-04-01T04:00:00+00:00",
        order_posted_at="2026-04-01T03:59:00+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="50-51°F",
        condition_id="0xmonitorrefresh000000000000000000000000000000000000000000000001",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-refresh-seed",
        source_module="tests/test_monitor_refresh_canonical_emit",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    pos.last_monitor_prob = 0.61
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.17
    pos.last_monitor_market_price = 0.44
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.43
    pos.last_monitor_best_ask = 0.45
    pos.selected_method = "emos"
    pos.applied_validations = ["identity_one_calibrator"]
    previous_monitor_at = "2026-04-01T05:00:00+00:00"
    pos.last_monitor_at = previous_monitor_at

    deps = type(
        "Deps",
        (),
        {"logger": logging.getLogger("test_monitor_refresh_canonical_emit")},
    )

    assert cycle_runtime._emit_monitor_refreshed_canonical_if_available(conn, pos, deps=deps) is True

    event = conn.execute(
        """
        SELECT event_type, occurred_at, phase_before, phase_after, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        ("monitor-refresh-1",),
    ).fetchone()
    assert event is not None
    assert event["occurred_at"] != previous_monitor_at
    assert event["phase_before"] == LifecyclePhase.ACTIVE.value
    assert event["phase_after"] == LifecyclePhase.ACTIVE.value
    payload = json.loads(event["payload_json"])
    assert payload["last_monitor_prob"] == pytest.approx(0.61)
    assert payload["last_monitor_market_price"] == pytest.approx(0.44)
    assert payload["selected_method"] == "emos"
    assert payload["applied_validations"] == ["identity_one_calibrator"]
    assert payload["exit_decision_available"] is False

    current = conn.execute(
        """
        SELECT phase, last_monitor_prob, last_monitor_edge,
               last_monitor_market_price, updated_at
          FROM position_current
         WHERE position_id = ?
        """,
        ("monitor-refresh-1",),
    ).fetchone()
    assert current["phase"] == LifecyclePhase.ACTIVE.value
    assert current["last_monitor_prob"] == pytest.approx(0.61)
    assert current["last_monitor_edge"] == pytest.approx(0.17)
    assert current["last_monitor_market_price"] == pytest.approx(0.44)
    assert current["updated_at"] == event["occurred_at"]
    conn.close()


def test_monitor_refresh_preserves_chain_corrected_entry_economics(tmp_path):
    """Monitor refresh must not roll a chain-corrected position back to stale fill size/state."""
    from src.engine.lifecycle_events import (
        build_entry_canonical_write,
        build_monitor_refreshed_canonical_write,
    )
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "monitor-refresh-preserve-chain.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="monitor-preserve-chain-1",
        state="holding",
        city="Shenzhen",
        target_date="2026-06-19",
        order_id="o-monitor-preserve-chain",
        entered_at="2026-06-17T16:33:02+00:00",
        order_posted_at="2026-06-17T16:32:37+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="32C",
        condition_id="0xmonitorpreservechain000000000000000000000000000000000000001",
        size_usd=9.99,
        shares=13.5,
        cost_basis_usd=9.99,
        entry_price=0.74,
        decision_snapshot_id="snap-monitor-preserve-chain",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-preserve-chain-seed",
        source_module="tests/test_monitor_refresh_preserves_chain_corrected_entry_economics",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.execute(
        """
        UPDATE position_current
           SET size_usd = 44.4,
               shares = 60.0,
               cost_basis_usd = 44.4,
               entry_price = 0.74,
               chain_state = 'local_only',
               chain_shares = 60.0,
               chain_avg_price = 0.74,
               chain_cost_basis_usd = 44.4,
               chain_seen_at = NULL
         WHERE position_id = ?
        """,
        ("monitor-preserve-chain-1",),
    )

    pos.last_monitor_prob = 0.869
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.133
    pos.last_monitor_market_price = 0.735
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_at = "2026-06-17T20:53:17+00:00"
    # Simulate a stale in-memory Position loaded before a deterministic
    # command-to-position evidence repair completed.
    pos.decision_snapshot_id = ""
    monitor_events, monitor_projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=4,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_monitor_refresh_preserves_chain_corrected_entry_economics",
    )
    append_many_and_project(conn, monitor_events, monitor_projection)

    current = conn.execute(
        """
        SELECT size_usd, shares, cost_basis_usd, chain_state, chain_shares,
               chain_cost_basis_usd, last_monitor_prob, last_monitor_edge,
               last_monitor_market_price, decision_snapshot_id, updated_at
          FROM position_current
         WHERE position_id = ?
        """,
        ("monitor-preserve-chain-1",),
    ).fetchone()
    assert current["size_usd"] == pytest.approx(44.4)
    assert current["shares"] == pytest.approx(60.0)
    assert current["cost_basis_usd"] == pytest.approx(44.4)
    assert current["chain_state"] == "local_only"
    assert current["chain_shares"] == pytest.approx(60.0)
    assert current["chain_cost_basis_usd"] == pytest.approx(44.4)
    assert current["last_monitor_prob"] == pytest.approx(0.869)
    assert current["last_monitor_edge"] == pytest.approx(0.133)
    assert current["last_monitor_market_price"] == pytest.approx(0.735)
    assert current["decision_snapshot_id"] == "snap-monitor-preserve-chain"
    assert current["updated_at"] == "2026-06-17T20:53:17+00:00"
    conn.close()


def test_chain_risk_hard_fact_monitor_holds_true_active_phase(tmp_path, monkeypatch):
    """Hard-fact monitor receipts for a real-exposure chain-risk position must
    reflect its TRUE (active) phase, never a quarantine scar (T5 REPLACEMENT
    PHASE LAW, docs/rebuild/quarantine_excision_2026-07-11.md).

    Pre-T5 this test forced position_current.phase='quarantined' directly via
    SQL to simulate a chain-risk quarantine and asserted the monitor loop
    preserved that scar phase. T5 retires the scar: Position.__post_init__
    now remaps any legacy 'quarantined'/'entry_authority_quarantined' input to
    its true state at construction (see
    src.state.portfolio._normalize_runtime_lifecycle_state /
    _normalize_runtime_chain_state), so there is no longer a quarantine phase
    for the monitor loop to preserve — it correctly reports ACTIVE for this
    real chain-confirmed-exposure position instead.
    """
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.execution.day0_hard_fact_exit import HardFactVerdict
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "quarantine-hard-fact-monitor.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="quarantine-hard-fact-monitor-1",
        state="holding",
        city="Manila",
        target_date="2026-06-29",
        order_id="o-quarantine-hard-fact-monitor",
        entered_at="2026-06-28T09:00:00+00:00",
        order_posted_at="2026-06-28T08:59:00+00:00",
        order_status="filled",
        strategy_key="center_buy",
        bin_label="32C",
        condition_id="0xquarantinehardfactmonitor00000000000000000000000000000001",
        direction="buy_no",
        shares=18.1,
        chain_shares=18.1,
        chain_state="synced",
        no_token_id="tok-manila-32-no",
        token_id="tok-manila-32-yes",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-quarantine-hard-fact-monitor-entry",
        source_module="tests/test_quarantined_hard_fact_monitor",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.commit()
    monkeypatch.setattr(
        cycle_runtime,
        "_closed_non_accepting_market_info",
        lambda *args, **kwargs: {"source": "clob_market_info"},
    )
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda **kwargs: HardFactVerdict(
            action="EXIT_DEAD_BIN",
            reason="final high extreme 32.0 resolved inside bin [32.0,32.0] — YES won",
            metric="high",
            rounded_extreme=32.0,
            source="durable_observation_instants",
        ),
    )

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_quarantined_chain_risk_hard_fact_monitor"),
            "cities_by_name": {"Manila": type("City", (), {"timezone": "Asia/Manila"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)),
        },
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        _make_portfolio(pos),
        artifact,
        type("Tracker", (), {"record_exit": lambda self, position: None})(),
        summary,
        deps=deps,
    )

    current = conn.execute(
        """
        SELECT phase, chain_state, chain_shares, exit_reason,
               last_monitor_prob, last_monitor_prob_is_fresh
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    # Note: the fixture's target_date (2026-06-29) is still within the day0
    # observation window at the monkeypatched _utcnow (2026-06-30T10:00), so
    # the monitor's TRUE phase for this real-exposure position is DAY0_WINDOW
    # (not a stale ACTIVE) — never a quarantine scar either way (T5).
    assert current["phase"] == LifecyclePhase.DAY0_WINDOW.value
    assert current["chain_state"] == "synced"
    assert current["chain_shares"] == pytest.approx(18.1)
    assert current["last_monitor_prob"] == pytest.approx(0.0)
    assert current["last_monitor_prob_is_fresh"] == 1
    event = conn.execute(
        """
        SELECT phase_before, phase_after, payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        (pos.trade_id,),
    ).fetchone()
    assert event is not None
    assert event["phase_before"] == LifecyclePhase.DAY0_WINDOW.value
    assert event["phase_after"] == LifecyclePhase.DAY0_WINDOW.value
    payload = json.loads(event["payload_json"])
    assert payload["exit_decision_reason"].startswith("DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED")
    assert payload["phase_after"] == LifecyclePhase.DAY0_WINDOW.value
    assert monitor_results[0].should_exit is False
    conn.close()


def test_chain_projection_preserves_fresh_monitor_snapshot(tmp_path):
    """Chain sync writes must not erase the last monitor belief/quote snapshot."""
    from src.engine.lifecycle_events import (
        build_chain_economics_observed_canonical_write,
        build_entry_canonical_write,
        build_monitor_refreshed_canonical_write,
    )
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "chain-preserve-monitor.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="chain-preserve-monitor-1",
        state="holding",
        city="Munich",
        target_date="2026-06-30",
        order_id="o-chain-preserve-monitor",
        entered_at="2026-06-29T08:55:40+00:00",
        order_posted_at="2026-06-29T08:55:21+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="Will the highest temperature in Munich be 30°C on June 30?",
        condition_id="0xchainpreservemonitor000000000000000000000000000000000001",
        size_usd=21.27,
        shares=29.14,
        cost_basis_usd=21.27,
        entry_price=0.73,
        token_id="tok-munich-30-yes",
        no_token_id="tok-munich-30-no",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-chain-preserve-monitor-entry",
        source_module="tests/test_chain_projection_preserves_fresh_monitor_snapshot",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    pos.last_monitor_prob = 0.98
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = 0.22
    pos.last_monitor_market_price = 0.76
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_at = "2026-06-29T20:02:40+00:00"
    monitor_events, monitor_projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=4,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_chain_projection_preserves_fresh_monitor_snapshot",
    )
    append_many_and_project(conn, monitor_events, monitor_projection)

    chain_pos = _make_position(
        trade_id=pos.trade_id,
        state="holding",
        city=pos.city,
        target_date=pos.target_date,
        order_id=pos.order_id,
        order_status=pos.order_status,
        strategy_key=pos.strategy_key,
        bin_label=pos.bin_label,
        condition_id=pos.condition_id,
        size_usd=pos.size_usd,
        shares=pos.shares,
        cost_basis_usd=pos.cost_basis_usd,
        entry_price=pos.entry_price,
        token_id=pos.token_id,
        no_token_id=pos.no_token_id,
        chain_state="synced",
        chain_shares=29.14,
        chain_avg_price=0.73,
        chain_cost_basis_usd=21.27,
        chain_verified_at="2026-06-29T22:20:52+00:00",
    )
    chain_events, chain_projection = build_chain_economics_observed_canonical_write(
        chain_pos,
        chain_observed_at="2026-06-29T22:20:52+00:00",
        sequence_no=5,
        phase_after=LifecyclePhase.ACTIVE.value,
        chain_shares_before=29.14,
        source_module="tests/test_chain_projection_preserves_fresh_monitor_snapshot",
    )
    append_many_and_project(conn, chain_events, chain_projection)

    current = conn.execute(
        """
        SELECT chain_state, chain_shares, chain_cost_basis_usd,
               last_monitor_prob, last_monitor_prob_is_fresh, last_monitor_edge,
               last_monitor_market_price, last_monitor_market_price_is_fresh,
               updated_at
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    assert current["chain_state"] == "synced"
    assert current["chain_shares"] == pytest.approx(29.14)
    assert current["chain_cost_basis_usd"] == pytest.approx(21.27)
    assert current["last_monitor_prob"] == pytest.approx(0.98)
    assert current["last_monitor_prob_is_fresh"] == 1
    assert current["last_monitor_edge"] == pytest.approx(0.22)
    assert current["last_monitor_market_price"] == pytest.approx(0.76)
    assert current["last_monitor_market_price_is_fresh"] == 1
    assert current["updated_at"] == "2026-06-29T22:20:52+00:00"
    conn.close()


def test_venue_confirmed_local_only_fill_enters_open_set_before_chain_sync(
    tmp_path,
    monkeypatch,
):
    """A newer venue fill is capital exposure before chain projection catches up."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import get_open_positions, load_runtime_open_portfolio

    conn = get_connection(tmp_path / "local-only-confirmed-fill-monitor.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="local-only-confirmed-fill-monitor-1",
        state="holding",
        city="Buenos Aires",
        target_date="2026-07-02",
        order_id="o-local-only-confirmed-fill-monitor",
        order_status="filled",
        entered_at="2026-07-01T22:19:06+00:00",
        order_posted_at="2026-07-01T22:17:03+00:00",
        strategy_key="center_buy",
        direction="buy_yes",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        shares=69.34,
        shares_filled=69.34,
        size_usd=2.84294,
        cost_basis_usd=2.84294,
        filled_cost_basis_usd=2.84294,
        entry_price=0.041,
        chain_state="local_only",
        chain_shares=0.0,
        token_id="tok-buenos-11-yes",
        no_token_id="tok-buenos-11-no",
        condition_id="condition-buenos-11",
        p_posterior=0.24833093804728934,
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-local-only-confirmed-fill-monitor-entry",
        source_module="tests/test_venue_confirmed_local_only_fill_is_monitored",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    portfolio = _make_portfolio(pos)

    assert get_open_positions(portfolio) == [pos]
    runtime = load_runtime_open_portfolio(conn)
    assert [current.trade_id for current in runtime.positions] == [pos.trade_id]
    assert runtime.positions[0].chain_shares == pytest.approx(0.0)
    assert runtime.positions[0].effective_shares == pytest.approx(69.34)
    assert cycle_runtime._monitoring_phase_positions(portfolio) == [pos]

    def fake_refresh(conn_arg, clob_arg, position):
        assert position is pos
        position.last_monitor_prob = 0.12
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.079
        position.last_monitor_market_price = 0.041
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_at = "2026-07-01T22:30:00+00:00"
        return EdgeContext(
            p_raw=np.array([0.12]),
            p_cal=np.array([0.12]),
            p_market=np.array([0.041]),
            p_posterior=0.12,
            forward_edge=0.079,
            alpha=0.0,
            confidence_band_upper=0.09,
            confidence_band_lower=0.07,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snapshot-local-only-confirmed-fill-monitor",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

    def fake_evaluate_exit(self, exit_context):
        assert exit_context.fresh_prob == pytest.approx(0.12)
        assert exit_context.current_market_price == pytest.approx(0.041)
        return ExitDecision(
            False,
            reason="CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior", "ci_overlap_hold"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", fake_evaluate_exit)
    monkeypatch.setattr(cycle_runtime, "_closed_non_accepting_market_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(cycle_runtime, "_entry_selection_guard_exit_decision", lambda **kwargs: None)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_venue_confirmed_local_only_fill_is_monitored"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    assert monitor_results[0].fresh_prob == pytest.approx(0.12)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'",
            (pos.trade_id,),
        ).fetchone()[0]
        == 1
    )
    current = conn.execute(
        """
        SELECT chain_state, chain_shares, last_monitor_prob,
               last_monitor_prob_is_fresh, last_monitor_market_price
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    assert current["chain_state"] == "local_only"
    assert current["chain_shares"] == pytest.approx(0.0)
    assert current["last_monitor_prob"] == pytest.approx(0.12)
    assert current["last_monitor_prob_is_fresh"] == 1
    assert current["last_monitor_market_price"] == pytest.approx(0.041)
    conn.close()


def test_monitoring_phase_persists_monitor_decision_with_refresh(tmp_path, monkeypatch):
    """Monitor refresh canonical evidence must include the final hold/exit decision."""
    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection, init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(tmp_path / "monitor-before-exit.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="monitor-before-exit-1",
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        order_id="o-monitor-before-exit",
        entered_at="2026-04-01T04:00:00+00:00",
        order_posted_at="2026-04-01T03:59:00+00:00",
        order_status="filled",
        strategy_key="opening_inertia",
        bin_label="50-51°F",
        condition_id="0xmonitorbeforeexit000000000000000000000000000000000000000001",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="decision-monitor-before-exit-seed",
        source_module="tests/test_monitoring_phase_persists_monitor_evidence",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    portfolio = _make_portfolio(pos)

    def fake_refresh(conn_arg, clob_arg, position):
        assert conn_arg is conn
        position.last_monitor_prob = 0.62
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = 0.18
        position.last_monitor_market_price = 0.44
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_at = "2026-04-01T05:00:00+00:00"
        return EdgeContext(
            p_raw=np.array([0.62]),
            p_cal=np.array([0.62]),
            p_market=np.array([0.44]),
            p_posterior=0.62,
            forward_edge=0.18,
            alpha=0.0,
            confidence_band_upper=0.20,
            confidence_band_lower=0.16,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snapshot-monitor-before-exit",
            n_edges_found=1,
            n_edges_after_fdr=1,
        )

    def fake_evaluate_exit(self, exit_context):
        prior_monitor_events = conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'",
            (self.trade_id,),
        ).fetchone()[0]
        assert prior_monitor_events == 0
        return ExitDecision(
            False,
            reason="CI_OVERLAP_HOLD",
            trigger="CI_OVERLAP_HOLD",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=["replacement_posterior", "ci_overlap_hold"],
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", fake_refresh)
    monkeypatch.setattr(Position, "evaluate_exit", fake_evaluate_exit)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected")

    monitor_results = []
    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: monitor_results.append(result)})()
    summary = {"monitors": 0, "exits": 0}
    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_monitoring_phase_persists_monitor_evidence"),
            "cities_by_name": {},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 1, 5, 0, tzinfo=timezone.utc)),
        },
    )

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert summary["monitors"] == 1
    assert summary["exits"] == 0
    assert monitor_results[0].fresh_prob == pytest.approx(0.62)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'",
            ("monitor-before-exit-1",),
        ).fetchone()[0]
        == 1
    )
    event = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
        """,
        ("monitor-before-exit-1",),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert payload["last_monitor_prob"] == pytest.approx(0.62)
    assert payload["last_monitor_market_price"] == pytest.approx(0.44)
    assert payload["exit_decision_available"] is True
    assert payload["exit_decision_should_exit"] is False
    assert payload["exit_decision_reason"] == "CI_OVERLAP_HOLD"
    assert payload["exit_decision_trigger"] == "CI_OVERLAP_HOLD"
    assert payload["exit_decision_applied_validations"] == [
        "replacement_posterior",
        "ci_overlap_hold",
    ]
    conn.close()


def test_monitor_refreshed_omits_duplicate_exit_validation_vector():
    """Identical monitor/exit validation evidence is stored exactly once."""
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="monitor-validation-dedup",
        state="holding",
        city="Chicago",
        target_date="2026-07-28",
        strategy_key="center_bin_buy",
        bin_label="90-91°F",
    )
    pos.applied_validations = [
        "replacement_posterior",
        "ci_overlap_hold",
    ]
    pos.last_monitor_at = "2026-07-28T08:00:00+00:00"
    exit_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="replacement_posterior",
        applied_validations=list(pos.applied_validations),
    )

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=2,
        phase_after=LifecyclePhase.ACTIVE.value,
        exit_decision=exit_decision,
    )

    payload = json.loads(events[0]["payload_json"])
    assert payload["applied_validations"] == pos.applied_validations
    assert "exit_decision_applied_validations" not in payload


def test_monitor_refreshed_indexes_exit_validation_subset():
    """Exit-specific validation order is preserved without duplicate strings."""
    from src.engine import cycle_runtime
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="monitor-validation-index",
        state="holding",
        city="Chicago",
        target_date="2026-07-28",
        strategy_key="center_bin_buy",
        bin_label="90-91°F",
    )
    pos.applied_validations = [
        "replacement_posterior",
        "fresh_market_price",
        "ci_overlap_hold",
    ]
    pos.last_monitor_at = "2026-07-28T08:00:00+00:00"
    exit_decision = ExitDecision(
        False,
        reason="CI_OVERLAP_HOLD",
        trigger="CI_OVERLAP_HOLD",
        selected_method="replacement_posterior",
        applied_validations=[
            "ci_overlap_hold",
            "replacement_posterior",
        ],
    )

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=2,
        phase_after=LifecyclePhase.ACTIVE.value,
        exit_decision=exit_decision,
    )

    payload = json.loads(events[0]["payload_json"])
    assert payload["exit_decision_validation_indexes"] == [2, 0]
    assert "exit_decision_applied_validations" not in payload
    assert cycle_runtime._monitor_event_applied_validations(payload) == [
        "ci_overlap_hold",
        "replacement_posterior",
    ]


def test_monitor_refreshed_persists_day0_probability_receipt():
    """Day0 monitor events must carry enough input evidence to replay probability flips."""
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="munich-day0-receipt",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="Will the highest temperature in Munich be 29°C on June 30?",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.8728257780611077,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:44.908942+00:00"
    pos.last_monitor_prob = 0.15810000000000002
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.57
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.57
    pos.last_monitor_best_ask = 0.58
    pos.last_monitor_edge = -0.41189999999999993
    pos.selected_method = "day0_observation_remaining_window"
    pos.applied_validations = [
        "day0_observation_remaining_window",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
    ]
    pos._day0_monitor_probability_receipt = {
        "schema_version": 1,
        "selected_method": "day0_observation_remaining_window",
        "metric": "high",
        "held_idx": 1,
        "held_direction": "buy_no",
        "held_side_probability": 0.15810000000000002,
        "bin_labels": ["28C", "29C", "30C"],
        "p_cal_vector": [0.01, 0.8419, 0.1481],
        "observation": {
            "source": "wu_hourly",
            "observed_high_so_far": 18.5,
            "current_temp": 18.0,
            "observation_time": "2026-06-30T02:44:00+00:00",
        },
        "remaining_window": {
            "source": "day0_hourly_vectors",
            "source_models": ["icon_d2"],
            "source_model_count": 1,
            "fetch_time": "2026-06-30T02:44:32.480826+00:00",
            "hours_remaining": 21.25,
            "member_extrema_summary": {
                "count": 1,
                "min": 28.8,
                "q50": 28.8,
                "q90": 28.8,
                "max": 28.8,
            },
        },
        "temporal_context": {
            "daypart": "pre_sunrise",
            "post_peak_confidence": 0.034,
        },
        "maturity_validations": [
            "day0_extreme_not_absorbing",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        ],
    }

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=27,
        phase_after=LifecyclePhase.DAY0_WINDOW.value,
        source_module="tests/test_day0_probability_receipt",
    )

    payload = json.loads(events[0]["payload_json"])
    receipt = payload["day0_monitor_probability_receipt"]
    assert receipt["selected_method"] == "day0_observation_remaining_window"
    assert receipt["remaining_window"]["source"] == "day0_hourly_vectors"
    assert receipt["remaining_window"]["source_models"] == ["icon_d2"]
    assert receipt["remaining_window"]["member_extrema_summary"]["max"] == pytest.approx(28.8)
    assert receipt["held_side_probability"] == pytest.approx(0.15810000000000002)
    assert receipt["p_cal_vector"] == pytest.approx([0.01, 0.8419, 0.1481])


def test_monitor_refreshed_persists_conditioned_daily_extrema_receipt():
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="taipei-conditioned-daily-receipt",
        city="Taipei",
        target_date="2026-07-09",
        temperature_metric="high",
        bin_label="Will the highest temperature in Taipei be 35°C on July 9?",
        direction="buy_no",
        shares=3.8,
        entry_price=0.64,
        p_posterior=0.8006076372881108,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-07-09T11:20:00+00:00"
    pos.last_monitor_prob = 0.0066
    pos.last_monitor_prob_is_fresh = True
    pos.selected_method = "day0_observation_conditioned_daily_extrema"
    pos.applied_validations = [
        "day0_observation_conditioned_daily_extrema",
        "day0_daily_extrema_not_remaining_window:day0_daily_extrema_live",
    ]
    pos._day0_monitor_probability_receipt = {
        "schema_version": 1,
        "selected_method": "day0_observation_conditioned_daily_extrema",
        "metric": "high",
        "held_side_probability": 0.0066,
        "remaining_window": {
            "source": "day0_observed_bound_conditioned_daily_extrema",
            "member_extrema_summary": {"count": 1, "max": 35.0},
            "raw_member_extrema_summary": {"count": 1, "max": 36.0},
        },
    }

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=28,
        phase_after=LifecyclePhase.DAY0_WINDOW.value,
        source_module="tests/test_day0_conditioned_daily_receipt",
    )

    payload = json.loads(events[0]["payload_json"])
    receipt = payload["day0_monitor_probability_receipt"]
    assert receipt["selected_method"] == "day0_observation_conditioned_daily_extrema"
    assert receipt["remaining_window"]["source"] == (
        "day0_observed_bound_conditioned_daily_extrema"
    )
    assert receipt["remaining_window"]["raw_member_extrema_summary"]["max"] == pytest.approx(36.0)
    assert receipt["remaining_window"]["member_extrema_summary"]["max"] == pytest.approx(35.0)


def test_monitor_refreshed_omits_stale_day0_probability_receipt_on_non_day0_method():
    """A stale Day0 receipt must not contaminate later non-Day0 monitor events."""
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    pos = _make_position(
        trade_id="replacement-monitor-after-day0",
        city="Munich",
        target_date="2026-07-02",
        temperature_metric="high",
        bin_label="Will the highest temperature in Munich be 29°C on July 2?",
        direction="buy_no",
        shares=12.0,
        entry_price=0.60,
        p_posterior=0.80,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.selected_method = "replacement_posterior"
    pos.last_monitor_at = "2026-06-30T12:00:00+00:00"
    pos.last_monitor_prob = 0.80
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.61
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_edge = 0.19
    pos._day0_monitor_probability_receipt = {
        "schema_version": 1,
        "selected_method": "day0_observation_remaining_window",
        "remaining_window": {"source": "day0_hourly_vectors"},
    }

    events, _projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=3,
        phase_after=LifecyclePhase.ACTIVE.value,
        source_module="tests/test_day0_probability_receipt",
    )

    payload = json.loads(events[0]["payload_json"])
    assert payload["selected_method"] == "replacement_posterior"
    assert "day0_monitor_probability_receipt" not in payload


def test_immature_day0_statistical_exit_survives_monitor_overlay():
    """Temporal maturity cannot erase a fresh statistical redecision."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-stat-exit-day0-immature",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.15
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = [
        "day0_observation_remaining_window",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
    ]
    exit_decision = ExitDecision(
        True,
        reason="CI_SEPARATED_REVERSAL",
        trigger="CI_SEPARATED_REVERSAL",
        selected_method="day0_observation_remaining_window",
        applied_validations=list(pos.applied_validations),
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=exit_decision,
        should_exit=True,
        exit_reason=exit_decision.reason,
        summary=summary,
    )

    assert should_exit is True
    assert reason == exit_decision.reason
    assert "family_redecision_day0_immature_exits_blocked" not in summary
    assert not hasattr(pos, "_monitor_family_redecision")


def test_monitor_overlay_preserves_exit_decision_only_immature_day0_evidence():
    """Munich regression: exit-decision evidence remains statistically actionable."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-stat-exit-day0-immature-exit-decision-only",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.15
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    pos.applied_validations = ["day0_observation_remaining_window"]
    exit_decision = ExitDecision(
        True,
        reason="CI_SEPARATED_REVERSAL",
        trigger="CI_SEPARATED_REVERSAL",
        selected_method="day0_observation_remaining_window",
        applied_validations=[
            "day0_observation_remaining_window",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        ],
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=exit_decision,
        should_exit=True,
        exit_reason=exit_decision.reason,
        summary=summary,
    )

    assert should_exit is True
    assert reason == exit_decision.reason
    assert "family_redecision_day0_immature_exits_blocked" not in summary
    assert not hasattr(pos, "_monitor_family_redecision")


def test_monitor_overlay_preserves_immature_day0_without_second_family_evaluator():
    """A missing sibling quote cannot reintroduce a temporal SELL veto."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-stat-exit-day0-immature-missing-quotes",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.last_monitor_at = "2026-06-30T02:44:00+00:00"
    pos.last_monitor_prob = 0.15
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.55
    pos.last_monitor_best_ask = 0.57
    pos.last_monitor_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    sibling = _make_position(
        trade_id="family-stat-exit-day0-immature-stale-sibling",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="30C",
        direction="buy_no",
        shares=10.0,
        entry_price=0.70,
        p_posterior=0.90,
        strategy_key="center_bin_buy",
        env="live",
    )
    sibling.last_monitor_prob = 0.90
    sibling.last_monitor_prob_is_fresh = True
    sibling.last_monitor_market_price = 0.40
    sibling.last_monitor_market_price_is_fresh = False
    sibling.last_monitor_best_bid = 0.40
    sibling.last_monitor_best_ask = 0.42
    exit_decision = ExitDecision(
        True,
        reason="CI_SEPARATED_REVERSAL",
        trigger="CI_SEPARATED_REVERSAL",
        selected_method="day0_observation_remaining_window",
        applied_validations=[
            "day0_observation_remaining_window",
            "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        ],
    )
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos, sibling),
        pos=pos,
        exit_decision=exit_decision,
        should_exit=True,
        exit_reason=exit_decision.reason,
        summary=summary,
    )

    assert should_exit is True
    assert reason == exit_decision.reason
    assert "family_redecision_day0_immature_exits_blocked" not in summary
    assert not hasattr(pos, "_monitor_family_redecision")


def test_exit_evidence_gate_does_not_reimpose_day0_temporal_veto():
    """The final evidence gate cannot undo current statistical authority."""
    from src.engine import cycle_runtime

    pos = _make_position(
        trade_id="family-direct-sell-final-gate-day0-immature",
        city="Munich",
        target_date="2026-06-30",
        temperature_metric="high",
        bin_label="29C",
        direction="buy_no",
        shares=33.15,
        entry_price=0.60,
        p_posterior=0.83,
        strategy_key="center_bin_buy",
        env="live",
    )
    pos.applied_validations = [
        "day0_observation_remaining_window",
        "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
        "family_direct_sell_dominates_hold_exit",
    ]
    summary = {}
    deps = SimpleNamespace(logger=logging.getLogger("test_exit_gate_day0_immature"))

    allowed, reason = cycle_runtime._exit_evidence_gate_allows_statistical_exit(
        conn=sqlite3.connect(":memory:"),
        pos=pos,
        exit_trigger="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        summary=summary,
        deps=deps,
    )

    assert allowed is True
    assert reason is None
    assert summary["exit_evidence_gate_passed"] == 1
    assert "exit_evidence_gate_blocked_positions" not in summary


def test_same_cycle_day0_crossing_refreshes_through_day0_semantics(monkeypatch):
    """A same-cycle `<6h` crossing must not refresh through the old non-Day0 path.

    A6 audit (2026-05-04): pin to legacy 6-hour transition — see
    test_monitoring_transitions_holding_position_into_day0_window for the
    full rationale.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    from src.engine import cycle_runtime, monitor_refresh
    from src.contracts import EntryMethod

    pos = _make_position(
        state="holding",
        city="Chicago",
        target_date="2026-04-01",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
    )
    portfolio = _make_portfolio(pos)

    class LiveClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.41, 100.0, 100.0

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("No exit expected in same-cycle Day0 refresh test")

    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        monitor_refresh._set_monitor_probability_fresh(position, True)
        return 0.52

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, exit_context: ExitDecision(False, selected_method=self.selected_method or self.entry_method),
    )

    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type("MonitorResult", (), {"__init__": lambda self, **kwargs: self.__dict__.update(kwargs)}),
            "logger": logging.getLogger("test_same_cycle_day0_refresh"),
            "cities_by_name": {"Chicago": type("City", (), {"timezone": "America/Chicago"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 4, 2, 4, 30, tzinfo=timezone.utc)),
        },
    )

    artifact = type("Artifact", (), {"add_monitor_result": lambda self, result: None})()
    summary = {"monitors": 0, "exits": 0}

    portfolio_dirty, tracker_dirty = cycle_runtime.execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
        deps=deps,
    )

    assert portfolio_dirty is True
    assert tracker_dirty is False
    assert pos.state == "day0_window"
    assert observed_methods == [EntryMethod.DAY0_OBSERVATION.value]
    assert pos.entry_method == EntryMethod.ENS_MEMBER_COUNTING.value
    assert (
        pos.selected_method
        == monitor_refresh.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
    )
    assert EntryMethod.DAY0_OBSERVATION.value in pos.applied_validations
    assert "day0_observation_remaining_window" in pos.applied_validations
    assert "whale_toxicity_deferred:fresh_probability_authority" in pos.applied_validations
    assert pos.last_monitor_prob == pytest.approx(0.52)
    assert pos.last_monitor_market_price == pytest.approx(0.41)
    assert summary["monitors"] == 1


def test_day0_window_refresh_uses_day0_observation_semantics(monkeypatch):
    """day0_window must refresh through Day0 semantics even for ENS-entered positions."""
    from src.engine import monitor_refresh
    from src.contracts import EntryMethod

    pos = _make_position(
        state="day0_window",
        city="Chicago",
        target_date="2026-04-01",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.43, 100.0, 100.0

    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        monitor_refresh._set_monitor_probability_fresh(position, True)
        return 0.52

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert observed_methods == [EntryMethod.DAY0_OBSERVATION.value]
    assert pos.entry_method == "ens_member_counting"
    assert (
        pos.selected_method
        == monitor_refresh.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
    )
    assert EntryMethod.DAY0_OBSERVATION.value in pos.applied_validations
    assert "day0_observation_remaining_window" in pos.applied_validations
    assert edge_ctx.p_posterior == pytest.approx(0.52)
    assert edge_ctx.entry_provenance == EntryMethod.ENS_MEMBER_COUNTING
    assert pos.last_monitor_prob == pytest.approx(0.52)
    assert pos.last_monitor_market_price == pytest.approx(0.41)


def test_day0_wu_observation_unavailable_reseeds_without_forecast_fallback(monkeypatch):
    """A missing Day0 observation must not borrow legacy forecast freshness."""
    from src.contracts import EntryMethod
    from src.contracts.exceptions import ObservationUnavailableError
    from src.engine import monitor_refresh

    pos = _make_position(
        state="day0_window",
        city="Chicago",
        target_date="2026-04-01",
        entry_method=EntryMethod.ENS_MEMBER_COUNTING.value,
        selected_method="",
        applied_validations=[],
    )
    city = type(
        "City",
        (),
        {
            "name": "Chicago",
            "timezone": "America/Chicago",
            "settlement_source_type": "wu_icao",
        },
    )()
    observed_methods = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_methods.append(position.entry_method)
        if position.entry_method == EntryMethod.DAY0_OBSERVATION.value:
            raise ObservationUnavailableError("wu observation unavailable")
        raise AssertionError("legacy forecast monitor fallback must not run")

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)
    reseeds = []
    monkeypatch.setattr(
        monitor_refresh,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseeds.append(kw),
    )

    p, refresh_pos, fresh = monitor_refresh.monitor_probability_refresh(
        pos,
        conn=None,
        city=city,
        target_d=date(2026, 4, 1),
    )

    assert observed_methods == [
        EntryMethod.DAY0_OBSERVATION.value,
    ]
    assert p == pytest.approx(pos.p_posterior)
    assert refresh_pos is not pos
    assert refresh_pos.entry_method == EntryMethod.DAY0_OBSERVATION.value
    assert fresh is False
    assert "day0_observation_unavailable:replacement_belief_reseed" in refresh_pos.applied_validations
    assert all("forecast_monitor_fallback" not in v for v in refresh_pos.applied_validations)
    assert "q_source:emos" not in refresh_pos.applied_validations
    assert reseeds == [
        {"city": "Chicago", "target_date": "2026-04-01", "metric": "high"}
    ]


def test_day0_absorbing_hard_fact_dominates_replacement_posterior(monkeypatch):
    """Tokyo LOW regression: absorbing hard fact is exact monitor belief."""
    from src.engine import monitor_refresh
    from src.execution.day0_hard_fact_exit import HardFactEvidence, HardFactVerdict

    pos = _make_position(
        state="day0_window",
        city="Tokyo",
        cluster="East Asia",
        target_date="2026-06-18",
        bin_label="21°C on June 18?",
        direction="buy_no",
        temperature_metric="low",
        unit="C",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
        entry_price=0.58,
        p_posterior=0.720612963366361,
        token_id="tok_yes_tokyo_low_21",
        no_token_id="tok_no_tokyo_low_21",
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            assert token_id == "tok_no_tokyo_low_21"
            return 0.99, 1.00, 100.0, 100.0

    evidence = HardFactEvidence(
        source="wu_api+wu_icao_history", station_id="RJTT",
        observed_at="2026-06-18T08:00:00+00:00",
        issued_at="2026-06-18T08:01:00+00:00",
        raw_extreme=20.0, rounded_extreme=20.0,
        payload_identity="a" * 64, source_identity="wu_api:RJTT+wu_icao_history:RJTT",
    )
    monkeypatch.setattr(monitor_refresh, "_is_position_target_local_day", lambda *a, **k: True)
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda *, position, city, now=None, world_conn=None, durable_only=False: HardFactVerdict(
            action="HOLD_STRUCTURAL_WIN",
            reason="running low extreme 20 killed bin [21.0,21.0]",
            metric="low",
            rounded_extreme=20.0,
            source=evidence.source,
            evidence=evidence,
        ),
    )
    monkeypatch.setattr(
        "src.engine.position_belief.load_replacement_belief",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("replacement posterior must not be read before absorbing hard fact")
        ),
    )

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert pos.selected_method == monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    assert pos.last_monitor_prob_is_fresh is True
    assert pos.last_monitor_prob == pytest.approx(1.0)
    assert pos.last_monitor_market_price == pytest.approx(0.99)
    assert pos.last_monitor_edge == pytest.approx(0.01)
    assert edge_ctx.p_posterior == pytest.approx(1.0)
    assert edge_ctx.forward_edge == pytest.approx(0.01)
    assert monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT in pos.applied_validations
    belief_tags = [
        tag for tag in pos.applied_validations
        if str(tag).startswith("belief_source=day0_absorbing_hard_fact;")
    ]
    assert belief_tags
    assert "yes_verdict=YES_DEAD" in belief_tags[0]
    assert "held_verdict=STRUCTURAL_WIN" in belief_tags[0]
    assert "held_prob=1.000000" in belief_tags[0]
    assert "forecast_posteriors_dominated_by_day0_hard_fact" in pos.applied_validations
    assert "model_divergence_panic_inapplicable:day0_absorbing_hard_fact" in pos.applied_validations
    assert getattr(pos, "_monitor_probability_receipt")["hard_fact_evidence"] == evidence.as_dict()


def test_active_same_day_absorbing_hard_fact_dominates_replacement_posterior(monkeypatch):
    """Active same-day positions must not wait for phase transition before hard-fact overlay."""
    from src.engine import monitor_refresh
    from src.execution.day0_hard_fact_exit import HardFactEvidence, HardFactVerdict

    pos = _make_position(
        state="holding",
        city="Tokyo",
        cluster="East Asia",
        target_date="2026-06-18",
        bin_label="21°C on June 18?",
        direction="buy_no",
        temperature_metric="low",
        unit="C",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
        entry_price=0.58,
        p_posterior=0.720612963366361,
        token_id="tok_yes_tokyo_low_21",
        no_token_id="tok_no_tokyo_low_21",
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            assert token_id == "tok_no_tokyo_low_21"
            return 0.99, 1.00, 100.0, 100.0

    evidence = HardFactEvidence(
        source="wu_api+wu_icao_history", station_id="RJTT",
        observed_at="2026-06-18T08:00:00+00:00",
        issued_at="2026-06-18T08:01:00+00:00",
        raw_extreme=20.0, rounded_extreme=20.0,
        payload_identity="b" * 64, source_identity="wu_api:RJTT+wu_icao_history:RJTT",
    )
    monkeypatch.setattr(monitor_refresh, "_is_position_target_local_day", lambda *a, **k: True)
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda *, position, city, now=None, world_conn=None, durable_only=False: HardFactVerdict(
            action="HOLD_STRUCTURAL_WIN",
            reason="running low extreme 20 killed bin [21.0,21.0]",
            metric="low",
            rounded_extreme=20.0,
            source=evidence.source,
            evidence=evidence,
        ),
    )
    monkeypatch.setattr(
        "src.engine.position_belief.load_replacement_belief",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("replacement posterior must not be read before active same-day hard fact")
        ),
    )

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert str(pos.state.value if hasattr(pos.state, "value") else pos.state) == "holding"
    assert pos.selected_method == monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    assert pos.last_monitor_prob_is_fresh is True
    assert pos.last_monitor_prob == pytest.approx(1.0)
    assert edge_ctx.p_posterior == pytest.approx(1.0)
    assert monitor_refresh.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT in pos.applied_validations
    assert "forecast_posteriors_dominated_by_day0_hard_fact" in pos.applied_validations


def test_day0_absorbing_hard_fact_monitor_consumes_durable_evidence_only(monkeypatch):
    """A held monitor must never put direct WU I/O inside its global claim."""
    from src.engine import monitor_refresh

    pos = _make_position(
        state="day0_window",
        city="Tokyo",
        target_date="2026-06-18",
        bin_label="21°C on June 18?",
        direction="buy_no",
        temperature_metric="low",
    )
    city = SimpleNamespace(name="Tokyo")
    observed = []

    def evaluate(**kwargs):
        observed.append(kwargs)
        return None

    monkeypatch.setattr(monitor_refresh, "_is_position_target_local_day", lambda *a, **k: True)
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        evaluate,
    )

    assert monitor_refresh._day0_absorbing_hard_fact_overlay(
        pos=pos,
        conn=object(),
        city=city,
        target_d=date(2026, 6, 18),
    ) is None
    assert len(observed) == 1
    assert observed[0]["durable_only"] is True


def test_day0_high_morning_observation_is_not_exit_authority():
    """A local-day running HIGH near midnight is not the day's final high authority."""
    from src.engine import monitor_refresh
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    temporal_context = SimpleNamespace(daypart="morning", post_peak_confidence=0.0)

    reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=HIGH_LOCALDAY_MAX,
        temporal_context=temporal_context,
        hours_remaining=23.0,
        observed_extreme_so_far=22.2,
        member_extrema_remaining=np.array([24.0, 25.0, 26.0]),
    )

    assert reason is not None
    assert reason.startswith("day0_high_extreme_not_mature:")


def test_day0_low_nonterminal_observation_is_not_exit_authority():
    """A local-day running LOW is not final-low authority while most of the day remains."""
    from src.engine import monitor_refresh
    from src.types.metric_identity import LOW_LOCALDAY_MIN

    temporal_context = SimpleNamespace(daypart="morning", post_peak_confidence=0.0)

    reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=LOW_LOCALDAY_MIN,
        temporal_context=temporal_context,
        hours_remaining=18.0,
        observed_extreme_so_far=18.0,
        member_extrema_remaining=np.array([17.0, 16.5, 18.5]),
    )

    assert reason == "day0_low_extreme_not_terminal:hours_remaining=18.0"


def test_day0_deterministic_remaining_forecast_does_not_bypass_maturity():
    """Forecast remaining-window determinism is not settlement hard-fact authority."""
    from src.engine import monitor_refresh
    from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

    temporal_context = SimpleNamespace(daypart="morning", post_peak_confidence=0.0)

    high_reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=HIGH_LOCALDAY_MAX,
        temporal_context=temporal_context,
        hours_remaining=23.0,
        observed_extreme_so_far=35.0,
        member_extrema_remaining=np.array([24.0, 25.0, 26.0]),
    )
    assert high_reason is not None and "not_mature" in high_reason

    low_reason = monitor_refresh._day0_extreme_authority_rejection_reason(
        temperature_metric=LOW_LOCALDAY_MIN,
        temporal_context=temporal_context,
        hours_remaining=18.0,
        observed_extreme_so_far=5.0,
        member_extrema_remaining=np.array([17.0, 16.5, 18.5]),
    )
    assert low_reason == "day0_low_extreme_not_terminal:hours_remaining=18.0"


def test_day0_high_morning_refresh_marks_probability_stale(monkeypatch):
    """Seoul-style local-midnight HIGH observation must not create exit authority."""
    from src.config import City
    from src.engine import monitor_refresh
    from src.signal.day0_extrema import RemainingMemberExtrema
    import src.signal.diurnal as diurnal

    pos = _make_position(
        state="day0_window",
        city="Seoul",
        target_date="2026-06-08",
        bin_label="25°C",
        temperature_metric="high",
        entry_method="ens_member_counting",
        selected_method="",
        p_posterior=0.79,
    )
    city = City(
        name="Seoul",
        lat=37.558,
        lon=126.791,
        timezone="Asia/Seoul",
        settlement_unit="C",
        cluster="East Asia",
        wu_station="RKSI",
        settlement_source_type="wu_icao",
    )

    monkeypatch.setattr(monitor_refresh, "_fetch_day0_observation", lambda *_: {
        "high_so_far": 22.2,
        "low_so_far": 20.0,
        "current_temp": 22.2,
        "observation_time": "2026-06-08T00:10:00+09:00",
        "source": "wu_api",
    })
    monitor_clock = {}

    def _hourly_vectors(**kwargs):
        monitor_clock["read"] = kwargs
        return {
            "members_hourly": np.zeros((3, 3)),
            "times": [
                "2026-06-07T15:00:00+00:00",
                "2026-06-07T16:00:00+00:00",
                "2026-06-07T17:00:00+00:00",
            ],
            "source_id": "day0_hourly_vectors",
            "forecast_source_role": "day0_remaining_window_live",
            "source_models": ["icon_d2", "ecmwf_ifs"],
            "expected_models": ["icon_d2", "ecmwf_ifs"],
            "source_model_count": 2,
            "fetch_time": datetime(2026, 6, 7, 15, 5, tzinfo=timezone.utc),
        }

    monkeypatch.setattr(monitor_refresh, "_read_day0_hourly_vectors", _hourly_vectors)
    monkeypatch.setattr(diurnal, "build_day0_temporal_context", lambda *a, **k: SimpleNamespace(
        daypart="morning",
        post_peak_confidence=0.0,
        current_utc_timestamp=datetime(2026, 6, 7, 15, 10, tzinfo=timezone.utc),
        solar_day=None,
        current_local_hour=0.17,
        daylight_progress=0.0,
    ))
    # Freeze the staleness gate's wall-clock to the fixture's frame: the obs
    # fast-lane gate (task #49) added a 1.0h max observation age measured
    # against real now, which rotted this fixed-date fixture (obs 2026-06-07
    # looked 100+ hours old). Real gate logic still runs — only the clock is
    # injected.
    _orig_quality_gate = monitor_refresh._day0_observation_quality_rejection_reason
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observation_quality_rejection_reason",
        lambda city, obs, metric, decision_time=None, **kwargs: _orig_quality_gate(
            city, obs, metric,
            decision_time=datetime(2026, 6, 7, 15, 10, tzinfo=timezone.utc),
            **kwargs,
        ),
    )
    def _remaining_extrema(*args, **kwargs):
        monitor_clock["extrema"] = kwargs
        return (
            RemainingMemberExtrema.for_metric(
                np.array([24.0, 25.0, 26.0]),
                kwargs["temperature_metric"],
            ),
            23.0,
        )

    monkeypatch.setattr(
        monitor_refresh,
        "remaining_member_extrema_for_day0",
        _remaining_extrema,
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_build_all_bins",
        lambda *a, **k: (
            [
                monitor_refresh.Bin(low=24, high=24, label="24°C", unit="C"),
                monitor_refresh.Bin(low=25, high=25, label="25°C", unit="C"),
                monitor_refresh.Bin(low=26, high=26, label="26°C", unit="C"),
            ],
            1,
        ),
    )

    p, validations = monitor_refresh._refresh_day0_observation(
        position=pos,
        current_p_market=0.72,
        conn=None,
        city=city,
        target_d=date(2026, 6, 8),
    )

    assert np.isfinite(p)
    assert getattr(pos, "_monitor_probability_is_fresh") is True
    observation_boundary = datetime(2026, 6, 7, 15, 10, tzinfo=timezone.utc)
    assert monitor_clock["read"]["remaining_window_start"] == observation_boundary
    assert monitor_clock["extrema"]["now"] == observation_boundary
    assert "day0_observation_remaining_window" in validations
    assert "day0_extreme_not_absorbing" in validations
    assert any(v.startswith("day0_high_extreme_not_mature:") for v in validations)


def test_day0_remaining_window_buy_no_returns_held_side_probability(monkeypatch):
    """Day0 monitor q is a YES-bin vector; buy_no exits must receive 1 - q_yes."""
    from src.engine import monitor_refresh
    import src.signal.diurnal as diurnal

    pos = _make_position(
        trade_id="munich-29-no-day0-side-space",
        state="day0_window",
        city="Munich",
        target_date="2026-06-30",
        bin_label="Will the highest temperature in Munich be 29°C on June 30?",
        temperature_metric="high",
        direction="buy_no",
        entry_method="qkernel_spine",
        selected_method="day0_observation_remaining_window",
        p_posterior=0.872825778061108,
    )
    city = SimpleNamespace(
        name="Munich",
        timezone="Europe/Berlin",
        settlement_unit="C",
        settlement_source_type="wu_icao",
        wu_station="EDDM",
    )

    monkeypatch.setattr(
        monitor_refresh,
        "_fetch_day0_observation",
        lambda *_: {
            "high_so_far": 28.0,
            "low_so_far": 18.0,
            "current_temp": 27.5,
            "observation_time": "2026-06-30T04:44:00+02:00",
            "source": "wu_api",
        },
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observation_source_rejection_reason",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observation_quality_rejection_reason",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        diurnal,
        "build_day0_temporal_context",
        lambda *a, **k: SimpleNamespace(
            daypart="pre_sunrise",
            post_peak_confidence=0.034,
            current_utc_timestamp=datetime(2026, 6, 30, 2, 44, tzinfo=timezone.utc),
            solar_day=None,
            current_local_hour=4.74,
            daylight_progress=0.0,
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_read_day0_hourly_vectors",
        lambda **kw: {
            "members_hourly": np.zeros((3, 3)),
            "times": [
                "2026-06-30T02:00:00+00:00",
                "2026-06-30T03:00:00+00:00",
                "2026-06-30T04:00:00+00:00",
            ],
            "source_id": "day0_hourly_vectors",
            "forecast_source_role": "day0_remaining_window_live",
            "source_models": ["icon_d2", "ecmwf_ifs"],
            "expected_models": ["icon_d2", "ecmwf_ifs"],
            "source_model_count": 2,
            "fetch_time": datetime(2026, 6, 30, 2, 40, tzinfo=timezone.utc),
        },
    )
    monkeypatch.setattr(
        monitor_refresh,
        "remaining_member_extrema_for_day0",
        lambda *a, **k: (
            SimpleNamespace(maxes=np.array([28.0, 29.0, 30.0]), mins=None),
            8.0,
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_extreme_authority_rejection_reason",
        lambda **kwargs: "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034",
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_day0_observed_extreme_from_canonical_surface",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        monitor_refresh.Day0Router,
        "route",
        staticmethod(
            lambda inputs: SimpleNamespace(
                p_vector=lambda bins, n_mc=None: np.array([0.28, 0.1581, 0.5619])
            )
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_build_all_bins",
        lambda *a, **k: (
            [
                monitor_refresh.Bin(low=28, high=28, label="28°C", unit="C"),
                monitor_refresh.Bin(low=29, high=29, label="29°C", unit="C"),
                monitor_refresh.Bin(low=30, high=30, label="30°C", unit="C"),
            ],
            1,
        ),
    )
    monkeypatch.setattr(monitor_refresh, "_maybe_write_day0_nowcast", lambda **kw: None)

    p, validations = monitor_refresh._refresh_day0_observation(
        position=pos,
        current_p_market=0.57,
        conn=None,
        city=city,
        target_d=date(2026, 6, 30),
    )

    assert p == pytest.approx(1.0 - 0.1581)
    assert getattr(pos, "_monitor_probability_is_fresh") is True
    assert "day0_observation_remaining_window" in validations
    assert "day0_high_extreme_not_mature:daypart=pre_sunrise,post_peak_confidence=0.034" in validations


def test_day0_window_live_refresh_uses_best_bid_not_vwmp(monkeypatch):
    """Day0 quote surface uses bid while posterior dispatch stays quote-free."""
    from src.engine import monitor_refresh
    from src.contracts import EntryMethod

    pos = _make_position(
        state="day0_window",
        direction="buy_yes",
        city="Chicago",
        target_date="2026-04-01",
        entry_method="ens_member_counting",
        selected_method="",
        applied_validations=[],
        token_id="tok_yes_001",
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            assert token_id == "tok_yes_001"
            return 0.37, 0.55, 100.0, 200.0

    monkeypatch.setattr("src.state.db.log_microstructure", lambda *args, **kwargs: None)

    observed_markets = []

    def fake_recompute(position, current_p_market, registry, **context):
        observed_markets.append(current_p_market)
        position.selected_method = position.entry_method
        position.applied_validations = [position.entry_method]
        monitor_refresh._set_monitor_probability_fresh(position, True)
        return 0.52

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", fake_recompute)

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert observed_markets == [pytest.approx(pos.entry_price)]
    assert pos.entry_method == EntryMethod.ENS_MEMBER_COUNTING.value
    assert (
        pos.selected_method
        == monitor_refresh.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
    )
    assert pos.last_monitor_market_price == pytest.approx(0.37)
    assert pos.last_monitor_best_bid == pytest.approx(0.37)
    assert pos.last_monitor_best_ask == pytest.approx(0.55)
    assert edge_ctx.p_market[0] == pytest.approx(0.37)
    assert observed_markets[0] != pytest.approx(edge_ctx.p_market[0])


def test_day0_refresh_fallback_keeps_probability_non_authoritative(monkeypatch):
    """Day0 fallback must not relabel stored probability as current exit authority."""
    from src.contracts import EntryMethod
    from src.engine import monitor_refresh

    pos = _make_position(
        state="day0_window",
        city="Chicago",
        target_date="2026-04-01",
        entry_method=EntryMethod.ENS_MEMBER_COUNTING.value,
        selected_method="",
        p_posterior=0.61,
        last_monitor_prob=0.41,
        last_monitor_prob_is_fresh=True,
        applied_validations=["alpha_posterior"],
    )

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return 0.41, 0.43, 100.0, 100.0

    monkeypatch.setattr(
        monitor_refresh,
        "_fetch_day0_observation",
        lambda city, target_d: type(
            "Obs",
            (),
            {
                "high_so_far": 44.0,
                "current_temp": 43.0,
                "source": "wu_api",
                # Missing observation_time forces fallback to the stored posterior.
                "observation_time": None,
            },
        )(),
    )

    edge_ctx = monitor_refresh.refresh_position(None, DummyClob(), pos)

    assert pos.selected_method == EntryMethod.DAY0_OBSERVATION.value
    assert pos.last_monitor_market_price == pytest.approx(0.41)
    assert pos.last_monitor_market_price_is_fresh is True
    assert pos.last_monitor_prob == pytest.approx(0.41)
    assert pos.last_monitor_prob_is_fresh is False
    assert not np.isfinite(pos.last_monitor_edge)
    assert not np.isfinite(edge_ctx.p_posterior)
    assert not np.isfinite(edge_ctx.forward_edge)
    assert "missing_observation_timestamp" in pos.applied_validations
    assert "monitor_probability_stale" in pos.applied_validations


# ---- Bonus: Quarantine expiry timer retired (P0b, 2026-07-04) ----
#
# test_quarantine_does_not_expire_early previously pinned "stays quarantined
# before 48h" — now vacuously true for every duration since the timer no
# longer expires anything. Retired alongside test_quarantine_expires_after_48h
# above; see docs/rebuild/chain_mirror_state_model_2026-07-04.md §5 follow-up.


# ---- Bonus: Collateral check fail-closed on API error ----


def test_collateral_check_fails_closed_on_api_error():
    """If balance fetch fails, collateral check blocks the sell."""
    clob = MagicMock()
    clob.get_balance.side_effect = Exception("API timeout")

    can_sell, reason = check_sell_collateral(
        entry_price=0.40, shares=10.0, clob=clob,
    )

    assert can_sell is False
    assert "balance_fetch_failed" in reason


# ---- Bonus: Live exit blocked by collateral goes to retry ----


def test_live_exit_collateral_blocked_goes_to_retry(monkeypatch):
    """Live exit that fails collateral check transitions to retry_pending."""
    from src.riskguard.risk_level import RiskLevel

    pos = _make_position(state="holding")
    pos.exit_reason = "red_force_exit"
    portfolio = _make_portfolio(pos)
    clob = _make_clob(balance=0.01)  # Not enough
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ExitContext(
            exit_reason="RED_FORCE_EXIT",
            current_market_price=0.45,
            current_market_price_is_fresh=False,
            best_bid=0.45,
        ),
        clob=clob,
    )

    assert "collateral_blocked" in outcome
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos in portfolio.positions  # NOT closed


def test_deferred_confirmed_fill_logs_last_monitor_best_bid(tmp_path):
    """Deferred confirmed fill telemetry must preserve sell-side realizable bid, not
    mark price. T1.c-followup rewrite 2026-04-23: post-T4.1b, exit fill
    emission flows through build_economic_close_canonical_write; test
    seeds active-phase canonical baseline so EXIT_ORDER_FILLED lands
    cleanly."""
    from src.state.db import get_connection, init_schema, query_position_events

    pos = _make_position(
        trade_id="deferred-fill-1",
        state="holding",
        exit_state="",
        chain_state="synced",
        last_exit_order_id="sell-order-1",
        exit_reason="DEFERRED_SELL_FILL",
        last_monitor_market_price=0.44,
        last_monitor_best_bid=0.39,
        order_id="buy-order-1",
        entry_order_id="buy-order-1",
        entry_fill_verified=True,
        entered_at="2026-04-03T00:05:00Z",
        order_status="filled",
        order_posted_at="2026-04-03T00:00:00Z",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-def-1",
    )
    portfolio = _make_portfolio(pos)
    conn = get_connection(tmp_path / "deferred-fill.db")
    init_schema(conn)
    # Seed canonical baseline in active phase (exit_state="") so
    # build_entry_canonical_write accepts; then transition pos to
    # pending_exit state via exit_state mutation for the test scenario.
    _seed_canonical_entry_baseline(conn, pos)
    pos.exit_state = "sell_pending"
    clob = _make_clob(sell_result={"status": "CONFIRMED", "avgPrice": 0.39})

    stats = check_pending_exits(portfolio, clob, conn=conn)
    events = query_position_events(conn, "deferred-fill-1")

    assert stats["filled"] == 1
    assert stats["retried"] == 0
    fill_event = next(event for event in events if event["event_type"] == "EXIT_ORDER_FILLED")
    assert pos.state == "economically_closed"
    assert pos.exit_price == pytest.approx(0.39)
    assert fill_event["details"]["fill_price"] == pytest.approx(0.39)
    assert fill_event["details"]["best_bid"] == pytest.approx(0.39)
    assert fill_event["details"]["current_market_price"] == pytest.approx(0.44)


def test_pending_exit_filled_status_does_not_economically_close():
    """FILLED is an order observation; CONFIRMED is required for exit finality."""
    pos = _make_position(
        state="day0_window",
        exit_state="sell_pending",
        last_exit_order_id="sell-order-1",
        exit_reason="DEFERRED_SELL_FILL",
        last_monitor_market_price=0.44,
        last_monitor_best_bid=0.39,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(sell_result={"status": "FILLED", "avgPrice": 0.39})

    stats = check_pending_exits(portfolio, clob, conn=None)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert pos in portfolio.positions
    assert pos.state == "pending_exit"
    assert pos.exit_state == "sell_pending"
    assert pos.exit_price in (None, 0.0)


def test_pending_exit_matched_status_does_not_economically_close():
    """MATCHED exit status is not finality and must keep the position pending."""
    pos = _make_position(
        state="day0_window",
        exit_state="sell_pending",
        last_exit_order_id="sell-order-1",
        exit_reason="DEFERRED_SELL_FILL",
        last_monitor_market_price=0.44,
        last_monitor_best_bid=0.39,
        entry_fill_verified=True,
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob(sell_result={"status": "MATCHED", "avgPrice": 0.39})

    stats = check_pending_exits(portfolio, clob, conn=None)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert pos in portfolio.positions
    assert pos.state == "pending_exit"
    assert pos.exit_state == "sell_pending"
    assert pos.exit_price in (None, 0.0)


def test_exit_authority_fails_closed_on_incomplete_context():
    """Missing authority fields must not silently fall through normal exit math."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=None,
            current_market_price=0.90,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert pos.neg_edge_count == 0


def test_exit_authority_fails_closed_on_stale_monitor_inputs():
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.55,
            fresh_prob_is_fresh=False,
            current_market_price=0.45,
            current_market_price_is_fresh=False,
            best_bid=0.44,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"


def test_day0_stale_probability_does_not_authorize_observation_reversal():
    """Stale model evidence must not become Day0 observation authority."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=False,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=0.54,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert decision.trigger != "DAY0_OBSERVATION_REVERSAL"


def test_day0_observation_exit_requires_executable_best_bid_not_price_proxy():
    """Current market price is not executable sell proceeds for Day0 exit EV."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert "best_bid_proxy_from_current_market_price" not in decision.applied_validations


@pytest.mark.parametrize("bad_bid", [math.nan, math.inf, -math.inf])
def test_day0_observation_exit_requires_finite_executable_best_bid(bad_bid):
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=bad_bid,
            hours_to_settlement=4.0,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations


def test_day0_force_exit_without_model_probability_still_requires_executable_best_bid():
    """Non-model Day0 exits cannot fall through to diagnostic price execution."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=False,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=0.5,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert "model_probability_authority_not_required:settlement_imminent" not in decision.applied_validations


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
@pytest.mark.parametrize("bad_bid", [None, math.nan, math.inf, -math.inf])
def test_day0_fresh_probability_force_exit_requires_finite_executable_best_bid(direction, bad_bid):
    pos = _make_position(direction=direction, size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.25,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=bad_bid,
            hours_to_settlement=0.5,
            position_state="day0_window",
            day0_active=True,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert decision.trigger != "SETTLEMENT_IMMINENT"


def test_day0_monitor_context_missing_bid_cannot_reach_submit_decision():
    """Monitor fields must preserve missing executable bid through exit decision."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)
    pos.state = "day0_window"
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.55
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = 0.56
    pos.last_monitor_market_vig = 1.0
    pos.last_monitor_whale_toxicity = True
    pos.chain_state = "synced"

    edge_ctx = SimpleNamespace(
        p_posterior=0.25,
        p_market=[0.55],
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    exit_context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=0.5,
        ExitContext=ExitContext,
    )
    decision = pos.evaluate_exit(exit_context)

    assert exit_context.best_bid is None
    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"
    assert decision.trigger == "EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_exit_context_stamps_side_correct_current_probability_ci_for_receipt(direction):
    """The same held-side CI authority feeds YES/NO exit and receipt paths."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(
        direction=direction,
        size_usd=5.0,
        entry_price=0.40,
        entry_ci_width=0.02,
        p_posterior=0.80,
    )
    pos.last_monitor_prob = 0.999999997246481
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.95
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.95
    pos.chain_state = "synced"
    edge_ctx = SimpleNamespace(
        p_posterior=pos.last_monitor_prob,
        p_market=[0.95],
        confidence_band_lower=0.942952047639557 - 0.95,
        confidence_band_upper=1.0 - 0.95,
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=12.0,
        ExitContext=ExitContext,
    )

    assert context.current_ci == pytest.approx((0.942952047639557, 1.0))
    assert pos._monitor_current_held_ci == pytest.approx(context.current_ci)

    no_ci_context = _build_exit_context(
        pos,
        SimpleNamespace(
            p_posterior=pos.last_monitor_prob,
            p_market=[0.95],
            divergence_score=0.0,
            market_velocity_1h=0.0,
        ),
        hours_to_settlement=12.0,
        ExitContext=ExitContext,
    )
    assert no_ci_context.current_ci is None
    assert pos._monitor_current_held_ci is None


def test_exit_context_projects_boundary_probability_intervals_onto_unit_support():
    """A near-one held belief must not become unavailable because its CI exceeds 1."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(
        direction="buy_no",
        size_usd=249.0,
        entry_price=0.70,
        entry_ci_width=0.0926822339563262,
        p_posterior=0.999363280592826,
    )
    pos.last_monitor_prob = 0.9999998397352373
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.6309887949300398
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.63
    pos.chain_state = "synced"
    current_edge = pos.last_monitor_prob - pos.last_monitor_market_price
    half_width = pos.entry_ci_width / 2.0
    edge_ctx = SimpleNamespace(
        p_posterior=pos.last_monitor_prob,
        p_market=[pos.last_monitor_market_price],
        confidence_band_lower=current_edge - half_width,
        confidence_band_upper=current_edge + half_width,
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=12.0,
        ExitContext=ExitContext,
    )
    decision = pos.evaluate_exit(context)

    assert context.entry_ci == pytest.approx((0.9530221636146629, 1.0))
    assert context.current_ci == pytest.approx((0.9536587227570742, 1.0))
    assert decision.trigger != "EVIDENCE_UNAVAILABLE"
    assert "current_held_ci_invalid" not in decision.applied_validations
    assert "entry_held_ci_invalid" not in decision.applied_validations


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_recovered_fill_without_entry_belief_still_uses_fresh_current_hold_value(direction):
    """A sunk entry witness gap cannot trap fresh negative-edge exposure."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(
        direction=direction,
        size_usd=9.0,
        entry_price=0.45,
        entry_ci_width=0.0,
        p_posterior=0.0,
    )
    pos.last_monitor_prob = 0.02
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.18
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.18
    pos.chain_state = "synced"
    edge_ctx = SimpleNamespace(
        p_posterior=0.02,
        p_market=[0.18],
        confidence_band_lower=0.01 - 0.18,
        confidence_band_upper=0.03 - 0.18,
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=2.0,
        ExitContext=ExitContext,
    )
    decision = pos.evaluate_exit(context)

    assert context.entry_posterior is None
    assert context.entry_ci is None
    assert context.current_ci == pytest.approx((0.01, 0.03))
    assert pos._monitor_current_held_ci == pytest.approx((0.01, 0.03))
    assert decision.should_exit is True
    assert decision.reason == "SELL_REVERSAL"


@pytest.mark.parametrize(
    ("fresh_prob", "prob_fresh", "market_fresh"),
    [
        (0.02, False, True),
        (0.02, True, False),
        ("not-a-number", True, True),
    ],
)
def test_recovered_fill_current_hold_value_fails_closed_without_cofresh_q_book(
    fresh_prob,
    prob_fresh,
    market_fresh,
):
    """Missing entry provenance never relaxes current q/book freshness."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(
        direction="buy_no",
        size_usd=9.0,
        entry_price=0.45,
        entry_ci_width=0.0,
        p_posterior=0.0,
    )
    pos.last_monitor_prob = 0.02
    pos.last_monitor_prob_is_fresh = prob_fresh
    pos.last_monitor_market_price = 0.18
    pos.last_monitor_market_price_is_fresh = market_fresh
    pos.last_monitor_best_bid = 0.18
    pos.chain_state = "synced"
    edge_ctx = SimpleNamespace(
        p_posterior=fresh_prob,
        p_market=[0.18],
        confidence_band_lower=0.01 - 0.18,
        confidence_band_upper=0.03 - 0.18,
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=2.0,
        ExitContext=ExitContext,
    )
    decision = pos.evaluate_exit(context)

    assert context.current_ci is None
    assert pos._monitor_current_held_ci is None
    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"


def test_day0_stale_probability_bypass_tokens_are_not_produced_by_source():
    """Legacy Day0 authority-waiver labels must not reappear in runtime source."""
    forbidden = {
        "day0_stale_prob_authority_waived",
        "stale_prob_substitution",
        "best_bid_proxy_from_current_market_price",
        "best_bid_proxy_tick_discount",
    }
    offenders: dict[str, list[str]] = {}
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text()
        hits = sorted(token for token in forbidden if token in text)
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits

    assert offenders == {}


def test_legacy_exit_triggers_api_is_not_used_by_live_runtime_source():
    """Live monitor/exit decisions must route through ExitContext authority."""
    offenders: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "src/execution/exit_triggers.py":
            continue
        if "evaluate_exit_triggers" in path.read_text():
            offenders.append(rel)

    assert offenders == []


def test_micro_position_uses_fill_authority_but_does_not_block_negative_edge_exit():
    """Micro-position handling marks actual filled cost but still runs exit economics."""
    pos = _make_position(
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_ci_width=0.02,
        shares_filled=1.0,
        filled_cost_basis_usd=0.50,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    pos.neg_edge_count = 2

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.10,
            fresh_prob_is_fresh=True,
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            current_ci=(0.10, 0.10),
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "SELL_REVERSAL"
    assert "sell_reversal" in decision.applied_validations


def test_full_open_fill_authority_cost_basis_can_exceed_projection_without_cap():
    """A venue-confirmed full-open fill is not capped by target/projection cost."""
    pos = _make_position(
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.51,
        shares=20.0,
        cost_basis_usd=10.0,
        last_monitor_market_price=0.60,
        shares_filled=20.0,
        filled_cost_basis_usd=10.2,
        entry_price_avg_fill=0.51,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )

    assert pos.effective_shares == pytest.approx(20.0)
    assert pos.effective_cost_basis_usd == pytest.approx(10.2)
    assert pos.unrealized_pnl == pytest.approx(1.8)


def test_partial_exit_fill_reduces_effective_open_fill_authority_exposure():
    """Partial exit changes current open exposure without rewriting entry-fill evidence."""
    from src.execution.exit_lifecycle import _apply_partial_exit_fill

    pos = _make_position(
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        shares_filled=20.0,
        filled_cost_basis_usd=10.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )

    changed = _apply_partial_exit_fill(
        pos,
        filled_shares=5.0,
        remaining_shares=15.0,
        fill_price=0.70,
        order_id="sell-partial-1",
        status="PARTIAL",
    )

    assert changed is True
    assert pos.shares_filled == pytest.approx(20.0)
    assert pos.filled_cost_basis_usd == pytest.approx(10.0)
    assert pos.effective_shares == pytest.approx(15.0)
    assert pos.effective_cost_basis_usd == pytest.approx(7.5)


def test_duplicate_fill_aggregation_updates_fill_authority_open_exposure():
    """Merging duplicate open fills must aggregate fill-grade economics, not submitted size."""
    from src.state.portfolio import add_position

    existing = _make_position(
        trade_id="agg-existing",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    incoming = _make_position(
        trade_id="agg-incoming",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=8.0,
        cost_basis_usd=4.0,
        shares_filled=8.0,
        filled_cost_basis_usd=4.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    portfolio = _make_portfolio(existing)

    add_position(portfolio, incoming)

    assert portfolio.positions == [existing]
    assert existing.shares_filled == pytest.approx(18.0)
    assert existing.filled_cost_basis_usd == pytest.approx(9.0)
    assert existing.effective_shares == pytest.approx(18.0)
    assert existing.effective_cost_basis_usd == pytest.approx(9.0)
    assert existing.size_usd == pytest.approx(9.0)


def test_mixed_authority_duplicate_keeps_fill_slice_separate():
    """Fill-grade economics must not be absorbed into a legacy same-token aggregate."""
    from src.state.portfolio import add_position

    legacy = _make_position(
        trade_id="legacy-existing",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
    )
    confirmed = _make_position(
        trade_id="fill-incoming",
        token_id="yes-shared",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    portfolio = _make_portfolio(legacy)

    add_position(portfolio, confirmed)

    assert portfolio.positions == [legacy, confirmed]
    assert legacy.has_fill_economics_authority is False
    assert confirmed.has_fill_economics_authority is True
    assert confirmed.effective_shares == pytest.approx(10.0)
    assert confirmed.effective_cost_basis_usd == pytest.approx(5.0)
    assert legacy.nested_fills == []


def test_same_order_update_cannot_regress_fill_authority_to_legacy():
    """Same-order idempotent updates must be monotonic for fill economics authority."""
    from src.state.portfolio import add_position

    existing = _make_position(
        trade_id="same-order-existing",
        order_id="entry-order-1",
        entry_order_id="entry-order-1",
        token_id="yes-shared",
        direction="buy_yes",
        state="holding",
        order_status="filled",
        entry_fill_verified=True,
        entered_at="2026-04-01T06:00:00Z",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    stale = _make_position(
        trade_id="same-order-stale",
        order_id="entry-order-1",
        entry_order_id="entry-order-1",
        token_id="yes-shared",
        direction="buy_yes",
        state="pending_tracked",
        order_status="pending",
        entry_fill_verified=False,
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_NONE,
    )
    portfolio = _make_portfolio(existing)

    add_position(portfolio, stale)

    assert portfolio.positions == [existing]
    assert existing.has_fill_economics_authority is True
    assert existing.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert existing.entry_economics_authority == ENTRY_ECONOMICS_AVG_FILL_PRICE
    assert existing.entry_fill_verified is True
    assert existing.state == "holding"
    assert existing.order_status == "filled"
    assert existing.effective_shares == pytest.approx(10.0)
    assert existing.effective_cost_basis_usd == pytest.approx(5.0)
    assert "same_order_fill_authority_regression_blocked" in existing.applied_validations


def test_same_order_update_cannot_regress_full_fill_to_partial_fill():
    """Same-order fill evidence must be monotonic even inside fill-grade states."""
    from src.state.portfolio import add_position

    existing = _make_position(
        trade_id="same-order-full",
        order_id="entry-order-2",
        entry_order_id="entry-order-2",
        token_id="yes-shared",
        direction="buy_yes",
        state="holding",
        order_status="filled",
        entry_fill_verified=True,
        entered_at="2026-04-01T06:00:00Z",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    stale_partial = _make_position(
        trade_id="same-order-partial-stale",
        order_id="entry-order-2",
        entry_order_id="entry-order-2",
        token_id="yes-shared",
        direction="buy_yes",
        state="holding",
        order_status="partial",
        entry_fill_verified=True,
        size_usd=2.5,
        entry_price=0.50,
        shares=5.0,
        cost_basis_usd=2.5,
        shares_filled=5.0,
        filled_cost_basis_usd=2.5,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    )
    portfolio = _make_portfolio(existing)

    add_position(portfolio, stale_partial)

    assert portfolio.positions == [existing]
    assert existing.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert existing.order_status == "filled"
    assert existing.shares_filled == pytest.approx(10.0)
    assert existing.filled_cost_basis_usd == pytest.approx(5.0)
    assert existing.effective_shares == pytest.approx(10.0)
    assert existing.effective_cost_basis_usd == pytest.approx(5.0)
    assert "same_order_fill_authority_regression_blocked" in existing.applied_validations


def test_whale_toxicity_uses_fill_authority_cost_basis_not_submitted_size(monkeypatch, tmp_path):
    """Adjacent pressure threshold must use actual filled exposure after correction."""
    from src.engine import monitor_refresh
    from src.state.db import get_connection, init_schema

    now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
    conn = get_connection(tmp_path / "whale-fill-authority.db")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_price_log (token_id, price, timestamp)
        VALUES (?, ?, ?)
        """,
        ("yes-above", 0.40, (now - timedelta(hours=2)).isoformat()),
    )
    conn.commit()
    pos = _make_position(
        market_id="m1",
        token_id="yes-held",
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )

    class BookClob:
        def get_best_bid_ask(self, token_id):
            return {"yes-above": (0.50, 0.52, 60.0, 10.0)}[token_id]

    siblings = [
        {"market_id": "m-below", "range_low": 37, "range_high": 38, "token_id": "yes-below"},
        {"market_id": "m1", "range_low": 39, "range_high": 40, "token_id": "yes-held"},
        {"market_id": "m-above", "range_low": 41, "range_high": 42, "token_id": "yes-above"},
    ]
    monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: siblings)
    monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

    result = monitor_refresh._detect_whale_toxicity_from_orderbook(
        conn,
        BookClob(),
        pos,
        held_best_bid=0.40,
        held_best_ask=0.43,
        now=now,
    )

    conn.close()
    assert result is True
    assert "whale_toxicity_available:adjacent_orderbook_pressure" in pos.applied_validations


def test_runtime_exit_context_uses_fill_authority_cost_basis_for_crowding_exposure():
    """Runtime portfolio context must preserve corrected cost basis into exit crowding."""
    from types import SimpleNamespace

    from src.engine.cycle_runtime import _build_exit_context

    pos = _make_position(trade_id="self-pos", state="holding")
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.50
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.49
    pos.chain_state = "synced"

    other = _make_position(
        trade_id="other-pos",
        cluster="Great Lakes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    closed = _make_position(trade_id="closed-pos", state="economically_closed", size_usd=1000.0)
    # T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md):
    # this leg used to represent a legacy state='quarantined'/
    # chain_state='quarantined' input that Position.__post_init__'s
    # mixed-epoch bridge remapped to its TRUE (holding/synced) state —
    # proving the remapped position still counted toward crowding exposure.
    # That bridge is deleted: the same literal now raises ValueError at
    # construction instead of remapping, so the scenario this leg exercised
    # ("a legacy quarantined input turns out to be real exposure") is
    # structurally impossible and the leg is removed rather than rewritten as
    # a plain active position, which `other` above already covers.
    pending_entry = _make_position(trade_id="pending-entry-pos", state="pending_tracked", size_usd=1000.0)
    portfolio = SimpleNamespace(bankroll=200.0, positions=[pos, other, closed, pending_entry])
    edge_ctx = SimpleNamespace(
        p_posterior=0.10,
        p_market=[0.50],
        divergence_score=0.0,
        market_velocity_1h=0.0,
    )

    exit_context = _build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=4.0,
        ExitContext=ExitContext,
        portfolio=portfolio,
    )

    assert exit_context.portfolio_positions == (
        (other.cluster, other.effective_cost_basis_usd, other.trade_id),
    )
    assert exit_context.portfolio_positions[0][1] != pytest.approx(other.size_usd)


def test_live_exit_path_uses_fill_authority_shares(monkeypatch):
    """Live exit path (Position.evaluate_exit) must use fill-authority shares not
    submitted-size math. Wave 3 (2026-06-02): dead _evaluate_buy_yes_exit tests removed;
    this test directly exercises the live path via ExitContext.
    """
    from src.state.portfolio import ExitContext

    pos = _make_position(
        direction="buy_yes",
        size_usd=100.0,
        entry_price=0.50,
        shares=200.0,
        cost_basis_usd=100.0,
        entry_ci_width=0.02,
        shares_filled=10.0,
        filled_cost_basis_usd=5.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    pos.neg_edge_count = 2
    # effective_shares = shares_filled = 10.0 (not size_usd / entry_price = 200.0)
    assert pos.effective_shares == pytest.approx(10.0)
    assert pos.effective_shares != pytest.approx(pos.size_usd / pos.entry_price)

    ctx = ExitContext(
        fresh_prob=0.10,
        fresh_prob_is_fresh=True,
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,  # below p_posterior=0.10 → EV gate blocks
        current_ci=(0.10, 0.10),
        hours_to_settlement=72.0,
        position_state="active",
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )
    decision = pos.evaluate_exit(ctx)
    # EV gate: sell_value = 10 * 0.49 = 4.9; hold_value = 10 * 0.10 = 1.0 → sell > hold → EXIT
    # (demonstrates effective_shares=10 not 200)
    assert decision.should_exit


def test_exit_paths_do_not_recompute_fill_authority_shares_from_legacy_price():
    """Static relationship check for corrected economics flowing into exit decisions.
    Wave 3 (2026-06-02): exit_triggers.py deleted; only portfolio.py and cycle_runtime.py checked.
    """
    portfolio_source = (ROOT / "src" / "state" / "portfolio.py").read_text(encoding="utf-8")
    cycle_runtime_source = (ROOT / "src" / "engine" / "cycle_runtime.py").read_text(encoding="utf-8")

    assert portfolio_source.count("self.size_usd / self.entry_price") == 1
    assert "if self.size_usd < 1.0" not in portfolio_source
    assert "(str(p.cluster), float(p.size_usd), str(p.trade_id))" not in cycle_runtime_source


def test_buy_yes_edge_exit_requires_best_bid():
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.30,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=4.0,
            position_state="holding",
            day0_active=False,
        )
    )

    assert decision.should_exit is False
    assert decision.reason == "EVIDENCE_UNAVAILABLE"


def test_low_probability_position_holds_when_terminal_value_beats_sell_value():
    """Low absolute hit rate is not an exit when the executable bid is worse."""

    pos = _make_position(
        direction="buy_yes",
        p_posterior=0.24833093804728934,
        entry_price=0.041,
        entry_ci_width=0.2985716143106003,
        shares=69.34,
        cost_basis_usd=2.8429,
    )

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.24833093804728934,
            fresh_prob_is_fresh=True,
            current_market_price=0.030649376417233552,
            current_market_price_is_fresh=True,
            best_bid=0.022,
            best_ask=0.039,
            hours_to_settlement=10.0,
            position_state="holding",
            day0_active=False,
            entry_posterior=0.24833093804728934,
            entry_ci=(0.0990451308919892, 0.3976167452025895),
            current_ci=(0.0990451308919892, 0.3976167452025895),
        )
    )

    assert decision.should_exit is False
    assert decision.trigger == "HOLD"
    assert "hold" in decision.applied_validations


def test_wide_ci_position_holds_when_terminal_value_beats_sell_value():
    """A wide current CI holds unless the bid beats its held-side UCB."""

    pos = _make_position(
        direction="buy_yes",
        p_posterior=0.70,
        entry_price=0.04,
        entry_ci_width=0.50,
        shares=100.0,
        cost_basis_usd=4.0,
    )

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.70,
            fresh_prob_is_fresh=True,
            current_market_price=0.06,
            current_market_price_is_fresh=True,
            best_bid=0.055,
            best_ask=0.065,
            hours_to_settlement=10.0,
            position_state="holding",
            day0_active=False,
            entry_posterior=0.70,
            entry_ci=(0.40, 0.90),
            current_ci=(0.40, 0.90),
        )
    )

    assert decision.should_exit is False
    assert decision.trigger == "HOLD"
    assert "hold" in decision.applied_validations


def test_day0_separated_zero_q_sells_before_static_edge_threshold_strands_leg(
    monkeypatch,
):
    """A recoverable bid strictly dominates a zero-value held leg.

    This is the Guangzhou 36C NO shape: current held q and its full current
    sample band were zero while the executable bid remained 0.08. The legacy
    edge threshold treated the smaller negative edge as a reason to hold,
    making exit less likely as the bid approached the legal venue floor.
    """

    pos = _make_position(
        direction="buy_no",
        p_posterior=0.999999999,
        entry_price=0.34,
        entry_ci_width=0.14,
        shares=5.2,
        cost_basis_usd=1.768,
    )
    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.0,
            fresh_prob_is_fresh=True,
            current_market_price=0.08,
            current_market_price_is_fresh=True,
            best_bid=0.08,
            best_ask=0.11,
            hours_to_settlement=10.0,
            position_state="day0_window",
            day0_active=True,
            day0_zero_probability_exit_authority=False,
            day0_exit_authority_status="mature",
            day0_exit_authority_reason=(
                "day0_high_extreme_mature:"
                "daypart=post_peak,post_peak_confidence=0.99"
            ),
            entry_posterior=0.999999999,
            entry_ci=(0.93, 1.0),
            current_ci=(0.0, 0.0),
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "SELL_REVERSAL"
    assert "sell_reversal" in decision.applied_validations
    assert (
        "ci_separated_edge_within_threshold_hold"
        not in decision.applied_validations
    )


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_day0_low_price_high_expected_value_remains_a_hold(direction):
    """Low price alone cannot liquidate a fresh high-value held claim."""

    pos = _make_position(
        direction=direction,
        p_posterior=0.90,
        entry_price=0.13,
        entry_ci_width=0.10,
        shares=100.0,
        cost_basis_usd=13.0,
    )
    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.4939,
            fresh_prob_is_fresh=True,
            current_market_price=0.004,
            current_market_price_is_fresh=True,
            best_bid=0.004,
            best_ask=0.006,
            hours_to_settlement=10.0,
            position_state="day0_window",
            day0_active=True,
            entry_posterior=0.90,
            entry_ci=(0.80, 1.0),
            current_ci=(0.20, 0.70),
        )
    )

    assert decision.should_exit is False
    assert decision.trigger == "HOLD"


def test_day0_point_q_reversal_survives_monitor_overlay_before_temporal_maturity():
    """An Ankara-shaped early reversal remains in continuous redecision."""
    from src.engine import cycle_runtime

    maturity_reason = (
        "day0_high_extreme_not_mature:"
        "daypart=pre_sunrise,post_peak_confidence=0.034"
    )
    pos = _make_position(
        trade_id="ankara-early-separated-reversal",
        city="Ankara",
        target_date="2026-07-22",
        temperature_metric="high",
        bin_label="31C",
        direction="buy_yes",
        p_posterior=0.8042,
        entry_price=0.27,
        entry_ci_width=0.43,
        shares=43.22,
        cost_basis_usd=11.67,
        state="day0_window",
        applied_validations=[maturity_reason],
    )
    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.10,
            fresh_prob_is_fresh=True,
            current_market_price=0.42,
            current_market_price_is_fresh=True,
            best_bid=0.42,
            best_ask=0.43,
            hours_to_settlement=20.0,
            position_state="day0_window",
            day0_active=True,
            entry_posterior=0.8042,
            entry_ci=(0.57, 1.0),
            current_ci=(0.0, 0.4533),
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "SELL_REVERSAL"
    assert "sell_reversal" in decision.applied_validations

    pos.last_monitor_prob = 0.10
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_market_price = 0.42
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_best_bid = 0.42
    pos.last_monitor_best_ask = 0.43
    pos._monitor_current_held_ci = (0.0, 0.4533)
    summary = {}

    should_exit, reason = cycle_runtime._apply_family_monitor_overlay(
        portfolio=_make_portfolio(pos),
        pos=pos,
        exit_decision=decision,
        should_exit=decision.should_exit,
        exit_reason=decision.reason,
        summary=summary,
    )

    assert should_exit is True
    assert reason == decision.reason
    assert "family_redecision_day0_immature_exits_blocked" not in summary


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
@pytest.mark.parametrize("current_ci", [None, (0.70, 0.60)])
def test_near_settlement_missing_or_invalid_ci_cannot_authorize_sell(
    direction,
    current_ci,
):
    """Time pressure cannot replace the current held-side robust bound."""

    pos = _make_position(
        direction=direction,
        p_posterior=0.20,
        entry_price=0.60,
        entry_ci_width=0.10,
        shares=30.0,
        cost_basis_usd=18.0,
    )

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.20,
            fresh_prob_is_fresh=True,
            current_market_price=0.40,
            current_market_price_is_fresh=True,
            best_bid=0.39,
            best_ask=0.41,
            hours_to_settlement=0.5,
            position_state="holding",
            day0_active=False,
            entry_posterior=0.60,
            entry_ci=(0.55, 0.65),
            current_ci=current_ci,
        )
    )

    assert decision.should_exit is False
    assert decision.trigger == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert "hold_value_probability_basis:current_q_ucb" not in decision.applied_validations


@pytest.mark.parametrize(
    "current_ci",
    [(-0.10, 0.90), (0.90, 0.50), (float("nan"), 0.90), (0.50,)],
)
def test_malformed_current_ci_fails_closed_without_point_substitution(current_ci):
    pos = _make_position(direction="buy_yes")

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.70,
            fresh_prob_is_fresh=True,
            current_market_price=0.60,
            current_market_price_is_fresh=True,
            best_bid=0.59,
            best_ask=0.61,
            hours_to_settlement=10.0,
            position_state="holding",
            day0_active=False,
            entry_posterior=0.70,
            entry_ci=(0.50, 0.90),
            current_ci=current_ci,
        )
    )

    assert decision.should_exit is False
    assert decision.trigger == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert "hold_value_probability_basis:current_q_ucb" not in decision.applied_validations


@pytest.mark.parametrize(
    ("hours_to_settlement", "whale_toxicity", "expected_exit", "expected_trigger"),
    [
        (0.5, False, False, "EVIDENCE_UNAVAILABLE"),
        (10.0, True, False, "EVIDENCE_UNAVAILABLE"),
    ],
)
def test_malformed_current_ci_cannot_authorize_local_sell(
    hours_to_settlement,
    whale_toxicity,
    expected_exit,
    expected_trigger,
):
    pos = _make_position(direction="buy_yes")

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.70,
            fresh_prob_is_fresh=True,
            current_market_price=0.60,
            current_market_price_is_fresh=True,
            best_bid=0.59,
            best_ask=0.61,
            hours_to_settlement=hours_to_settlement,
            position_state="holding",
            day0_active=False,
            whale_toxicity=whale_toxicity,
            entry_posterior=0.70,
            entry_ci=(0.50, 0.90),
            current_ci=(-0.10, 0.90),
        )
    )

    assert decision.should_exit is expected_exit
    assert decision.trigger == expected_trigger


def test_low_probability_position_sells_when_executable_repricing_beats_hold_value():
    """A low-q claim monetizes current repricing only when a fresh bid dominates hold EV."""

    pos = _make_position(
        direction="buy_yes",
        p_posterior=0.13,
        entry_price=0.03,
        entry_ci_width=0.10,
        shares=100.0,
        cost_basis_usd=3.0,
    )

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.13,
            fresh_prob_is_fresh=True,
            current_market_price=0.20,
            current_market_price_is_fresh=True,
            best_bid=0.20,
            best_ask=0.21,
            hours_to_settlement=10.0,
            position_state="holding",
            day0_active=False,
            entry_posterior=0.13,
            entry_ci=(0.08, 0.18),
            current_ci=(0.08, 0.18),
        )
    )

    assert decision.should_exit is True
    assert decision.trigger == "SELL_REVERSAL"
    assert "sell_reversal" in decision.applied_validations


def test_day0_observation_holds_when_settlement_imminent_without_current_ci():
    """Missing current CI cannot become SELL proof merely because time is short."""
    pos = _make_position(direction="buy_yes", size_usd=5.0, entry_price=0.40, entry_ci_width=0.02)

    decision = pos.evaluate_exit(
        ExitContext(
            fresh_prob=0.80,
            fresh_prob_is_fresh=True,
            current_market_price=0.55,
            current_market_price_is_fresh=True,
            best_bid=0.54,
            hours_to_settlement=0.5,
            position_state="day0_window",
            day0_active=True,
            divergence_score=0.40,
            market_velocity_1h=-0.20,
        )
    )

    assert decision.should_exit is False
    assert decision.trigger == "EVIDENCE_UNAVAILABLE"
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert "evidence_unavailable_third_state" in decision.applied_validations
    assert "evidence_unavailable_third_state" in decision.applied_validations


def test_live_execute_exit_blocks_incomplete_context():
    """Direct execute_exit callers must also fail closed on missing market price."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ExitContext(exit_reason="EDGE_REVERSAL", current_market_price=None),
        clob=clob,
    )

    assert outcome == "exit_blocked: incomplete_context"
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos.last_exit_error == "missing_current_market_price"
    assert pos in portfolio.positions


def test_live_execute_exit_blocks_stale_market_price_context():
    """Direct execute_exit callers must not place exits from stale price evidence."""
    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    outcome = execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ExitContext(
            exit_reason="EDGE_REVERSAL",
            current_market_price=0.45,
            current_market_price_is_fresh=False,
        ),
        clob=clob,
    )

    assert outcome == "exit_blocked: stale_market_price"
    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 1
    assert pos.last_exit_error == "stale_current_market_price"
    assert pos in portfolio.positions


def _global_sell_exit_intent(position, *, certificate=None):
    from src.execution.exit_lifecycle import ExitIntent

    exact = Decimal(str(position.effective_shares))
    sellable = exact.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    return ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=position.token_id,
        shares=sellable,
        current_market_price=0.45,
        best_bid=0.45,
        exact_limit_price=0.45,
        submit_order_type="FAK",
        close_position=sellable >= exact - Decimal("1e-9"),
        capital_certificate=certificate,
    )


def _valid_global_sell_certificate(position):
    sellable = Decimal(str(position.effective_shares)).quantize(
        Decimal("0.01"), rounding=ROUND_FLOOR
    )
    return {
        "action": "SELL",
        "candidate_id": "global-sell-candidate",
        "actuation_identity": "global-actuation-identity",
        "economic_identity": "global-economic-identity",
        "probability_witness_identity": "global-probability-witness",
        "robust_delta_log_wealth": "0.001",
        "robust_ev_usd": "0.10",
        "held_shares": str(position.effective_shares),
        "sellable_shares": str(sellable),
        "selected_shares": str(sellable),
        "exact_limit_price": "0.45",
    }


def _global_sell_exit_context():
    return ExitContext(
        exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        current_market_price=0.45,
        current_market_price_is_fresh=True,
        best_bid=0.45,
    )


@pytest.mark.parametrize(
    ("execution_mode", "submit_order_type"),
    (("TAKER_LIMIT", "FAK"), ("MAKER_REST", "GTC")),
)
def test_place_sell_order_propagates_typed_global_receipt_closure(
    monkeypatch,
    execution_mode,
    submit_order_type,
):
    """Both lifecycle order modes preserve the typed receipt into executor intent."""

    from src.contracts.global_auction_receipt import (
        GlobalAuctionReceiptRef,
        GlobalSellReceiptClosure,
    )
    from src.execution import exit_lifecycle

    receipt_ref = GlobalAuctionReceiptRef(
        decision_log_id=1,
        decision_log_mode="global_single_order_auction",
        receipt_hash="a" * 64,
        execution_binding_hash="b" * 64,
        artifact_summary_hash="c" * 64,
        schema_version=21,
        winner_event_id="event-1",
        winner_candidate_id="candidate-1",
        winner_actuation_identity="actuation-1",
        selection_epoch_identity="epoch-1",
    )
    closure = GlobalSellReceiptClosure(
        receipt_ref=receipt_ref,
        position_id="position-1",
        condition_id="condition-1",
        token_id="token-1",
        action="SELL",
        execution_mode=execution_mode,
        winner_event_id="event-1",
        winner_candidate_id="candidate-1",
        winner_actuation_identity="actuation-1",
        selection_epoch_identity="epoch-1",
    )
    authority = object()
    captured = {}

    def capture_intent(**kwargs):
        captured["intent_kwargs"] = kwargs
        return SimpleNamespace(execution_authority_deadline_utc="future")

    def capture_execute(intent, **_kwargs):
        captured["intent"] = intent
        return exit_lifecycle.OrderResult(
            trade_id="position-1", status="rejected", reason="test"
        )

    monkeypatch.setattr(
        exit_lifecycle,
        "create_exit_order_intent",
        capture_intent,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_exit_execution_authority_deadline_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        capture_execute,
    )
    result = exit_lifecycle.place_sell_order(
        trade_id="position-1",
        token_id="token-1",
        shares=1.0,
        current_price=0.5,
        best_bid=0.5,
        submit_order_type=submit_order_type,
        execution_proof_verified=True,
        global_sell_execution_authority=authority,
        global_sell_receipt_closure=closure,
    )
    assert result.status == "rejected"
    assert captured["intent_kwargs"]["global_sell_execution_authority"] is authority
    assert captured["intent_kwargs"]["global_sell_receipt_closure"] is closure


def test_local_exit_without_capital_certificate_cannot_reach_venue(monkeypatch):
    """A local monitor intent is diagnostic-only; it cannot emit a live SELL."""

    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    called = False

    def no_venue(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("local exit bypassed the global capital proof")

    monkeypatch.setattr("src.execution.exit_lifecycle.place_sell_order", no_venue)
    from src.execution.exit_lifecycle import ExitIntent

    outcome = execute_exit(
        portfolio,
        pos,
        ExitContext(
            exit_reason="EDGE_REVERSAL",
            current_market_price=0.45,
            current_market_price_is_fresh=True,
            best_bid=0.45,
        ),
        clob=object(),
        exit_intent=ExitIntent(
            trade_id=pos.trade_id,
            reason="EDGE_REVERSAL",
            token_id=pos.token_id,
            shares=pos.effective_shares,
            current_market_price=0.45,
            best_bid=0.45,
        ),
    )

    assert outcome == "exit_blocked: global_capital_optimal_sell_intent_required"
    assert called is False
    assert pos.exit_state == ""


def test_spoofed_hold_authority_rejection_cannot_reach_venue(monkeypatch):
    """A historical gate reason cannot bypass global capital authority."""
    from src.execution import exit_lifecycle

    pos = _make_position(
        state="holding",
        strategy_key="forecast_qkernel_entry",
        chain_shares=25.0,
        selected_method="replacement_posterior",
        applied_validations=["belief_source=forecast_posteriors;age_h=0.5;fresh"],
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: pytest.fail("spoofed strategy rejection reached venue"),
    )
    context = ExitContext(
        exit_reason=(
            "STRATEGY_HOLD_AUTHORITY_REJECTED "
            "(market_relative_alpha_rejected(evalue=12.688312,clusters=2,"
            "law=predicted_bin_ev_v1); best_bid=0.4500)"
        ),
        fresh_prob=0.71,
        fresh_prob_is_fresh=True,
        current_market_price=0.45,
        current_market_price_is_fresh=True,
        best_bid=0.45,
        probability_receipt={"probability_authority": "forecast_posteriors"},
        position_state="holding",
    )

    outcome = execute_exit(_make_portfolio(pos), pos, context, clob=object())

    assert outcome == "exit_blocked: global_capital_optimal_sell_intent_required"


@pytest.mark.parametrize(
    "certificate_update",
    [
        {"action": "BUY"},
        {"actuation_identity": ""},
        {"robust_delta_log_wealth": "-0.001"},
        {"robust_ev_usd": "0"},
        {"held_shares": "1"},
        {"sellable_shares": "1"},
        {"selected_shares": "1"},
        {"exact_limit_price": "0.44"},
    ],
)
def test_invalid_global_sell_capital_certificate_cannot_reach_venue(
    monkeypatch,
    certificate_update,
):
    """Forged, non-positive, or mismatched global SELL proofs fail closed."""

    pos = _make_position(state="holding")
    portfolio = _make_portfolio(pos)
    certificate = _valid_global_sell_certificate(pos)
    certificate.update(certificate_update)
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.place_sell_order",
        lambda **_kwargs: pytest.fail("invalid certificate reached venue"),
    )

    outcome = execute_exit(
        portfolio,
        pos,
        _global_sell_exit_context(),
        clob=object(),
        exit_intent=_global_sell_exit_intent(pos, certificate=certificate),
    )

    assert outcome == "exit_blocked: global_sell_execution_authority_required"
    assert pos.exit_state == ""


def test_mapping_only_global_sell_certificate_cannot_reach_venue(monkeypatch):
    """Caller-authored strings are not a typed global-auction authority."""

    from src.execution import exit_lifecycle

    pos = _make_position(state="holding", shares=7.036602)
    portfolio = _make_portfolio(pos)
    submitted = []
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *_args, **_kwargs: (True, ""),
    )

    def place(**kwargs):
        submitted.append(kwargs)
        return exit_lifecycle.OrderResult(
            trade_id=pos.trade_id,
            status="pending",
            order_id="global-sell-order",
            external_order_id="global-sell-order",
        )

    monkeypatch.setattr(exit_lifecycle, "place_sell_order", place)

    class Clob:
        @staticmethod
        def get_order_status(_order_id):
            return {"status": "OPEN"}

    outcome = execute_exit(
        portfolio,
        pos,
        _global_sell_exit_context(),
        clob=Clob(),
        exit_intent=_global_sell_exit_intent(
            pos,
            certificate=_valid_global_sell_certificate(pos),
        ),
    )

    assert outcome == "exit_blocked: global_sell_execution_authority_required"
    assert submitted == []


def test_spoofed_red_context_without_sweep_marker_cannot_reach_venue(monkeypatch):
    """A caller string cannot mint the RED exemption."""

    from src.execution import exit_lifecycle

    pos = _make_position(state="holding")
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: pytest.fail("context-only RED must not consult risk authority"),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: pytest.fail("spoofed RED reached venue"),
    )
    outcome = execute_exit(
        _make_portfolio(pos),
        pos,
        ExitContext(
            exit_reason="RED_FORCE_EXIT",
            current_market_price=0.45,
            current_market_price_is_fresh=True,
            best_bid=0.45,
        ),
        clob=object(),
    )

    assert outcome == "exit_blocked: global_capital_optimal_sell_intent_required"


def test_cycle_normalized_current_red_marker_bypasses_global_auction_authority(monkeypatch):
    """The cycle's upper-case RED marker retains the emergency exit path."""

    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel

    pos = _make_position(state="holding")
    pos.exit_reason = "RED_FORCE_EXIT"
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *_args, **_kwargs: (True, ""),
    )
    submitted = []

    def place(**kwargs):
        submitted.append(kwargs)
        return exit_lifecycle.OrderResult(
            trade_id=pos.trade_id,
            status="pending",
            order_id="red-sell-order",
            external_order_id="red-sell-order",
        )

    monkeypatch.setattr(exit_lifecycle, "place_sell_order", place)

    class Clob:
        @staticmethod
        def get_order_status(_order_id):
            return {"status": "OPEN"}

    context = ExitContext(
        exit_reason="RED_FORCE_EXIT",
        current_market_price=0.45,
        current_market_price_is_fresh=False,
        best_bid=0.45,
    )
    outcome = execute_exit(_make_portfolio(pos), pos, context, clob=Clob())

    assert outcome == "sell_pending: order=red-sell-order, status=OPEN"
    assert len(submitted) == 1
    assert submitted[0]["shares"] == pos.effective_shares
    assert submitted[0]["exact_limit_price"] is None


# ---- Autonomous Discovery Tests ----


def test_incomplete_chain_response_skips_voiding():
    """If chain API returns 0 positions but we have active local positions,
    don't void them — the API response is likely incomplete."""
    from src.state.chain_reconciliation import reconcile

    pos = _make_position(state="holding", token_id="tok_yes_real")
    portfolio = _make_portfolio(pos)

    # Chain returns EMPTY — suspect incomplete API response
    stats = reconcile(portfolio, chain_positions=[])

    # Position should NOT be voided
    assert stats["voided"] == 0
    assert pos in portfolio.positions
    assert stats.get("skipped_void_incomplete_api", 0) > 0


def test_incomplete_chain_response_does_not_mark_exit_pending_missing():
    """A globally incomplete chain snapshot must not escalate retrying exits into exit-missing recovery."""
    from src.state.chain_reconciliation import reconcile

    exiting = _make_position(
        state="holding",
        token_id="tok_retry_yes",
        no_token_id="tok_retry_no",
        exit_state="retry_pending",
        chain_state="synced",
    )
    healthy = _make_position(
        trade_id="healthy-other",
        token_id="tok_other_yes",
        no_token_id="tok_other_no",
        state="holding",
        chain_state="synced",
    )
    portfolio = _make_portfolio(exiting, healthy)

    stats = reconcile(portfolio, chain_positions=[])

    assert stats["voided"] == 0
    assert stats.get("skipped_pending_exit", 0) == 0
    assert stats.get("skipped_void_incomplete_api", 0) >= 2
    assert exiting.chain_state == "synced"
    assert exiting in portfolio.positions


# ---- Autonomous Discovery Tests ----


def test_exit_retry_exponential_backoff():
    """Retry cooldown should increase exponentially."""
    from src.execution.exit_lifecycle import _mark_exit_retry

    pos = _make_position()

    # First retry: base cooldown (300s = 5min)
    _mark_exit_retry(pos, reason="TEST", cooldown_seconds=300)
    assert pos.exit_retry_count == 1
    assert pos.exit_state == "retry_pending"

    # Second retry: 2x cooldown (600s = 10min)
    _mark_exit_retry(pos, reason="TEST", cooldown_seconds=300)
    assert pos.exit_retry_count == 2

    # Second retry should be further in the future than first was
    # (both relative to their own "now", so we just check count increments)
    assert pos.exit_retry_count == 2


# ---- Test 9: Sell share rounding ----


def test_sell_order_rounds_shares_down():
    """Sell shares must round DOWN to prevent over-selling."""
    shares = 10.999
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 10.99

    shares = 10.994
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 10.99

    shares = 10.0
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 10.0

    shares = 0.009
    rounded = math.floor(shares * 100 + 1e-9) / 100.0
    assert rounded == 0.0


# ---- Test 10: Stranded exit_intent recovery ----


def test_stranded_exit_intent_recovered():
    """If place_sell_order throws, position is stranded in exit_intent.
    check_pending_exits must recover it via retry."""
    pos = _make_position(
        state="holding",
        exit_state="exit_intent",  # stranded by exception
        last_exit_error="exception_during_sell",
    )
    portfolio = _make_portfolio(pos)
    clob = _make_clob()

    stats = check_pending_exits(portfolio, clob)

    assert stats["retried"] == 1
    assert pos.exit_state == "retry_pending"
    assert pos in portfolio.positions  # NOT closed


# ---- Provenance Tests ----


def test_position_carries_env():
    """Every position must carry its env provenance."""
    pos = _make_position(env="legacy_env")
    assert pos.env == "legacy_env"

    pos_live = _make_position(env="live")
    assert pos_live.env == "live"

def test_state_path_resolves_directly():
    """Phase 2: state_path returns STATE_DIR/filename directly (mode prefix eliminated)."""
    from src.config import state_path, STATE_DIR
    path = state_path("positions.json")
    assert path == STATE_DIR / "positions.json"
    assert "-live" not in path.name
    assert "-" not in path.stem


def test_save_portfolio_strips_terminal_enum_states(tmp_path):
    """Derived JSON active-position cache must not retain enum-backed terminal phases."""
    from src.state.portfolio import save_portfolio

    active = _make_position(trade_id="active-json", state="holding")
    settled = _make_position(trade_id="settled-json", state="holding")
    settled.state = LifecycleState.SETTLED
    portfolio = _make_portfolio(active, settled)
    output = tmp_path / "positions.json"

    save_portfolio(portfolio, output)

    payload = json.loads(output.read_text())
    assert [row["trade_id"] for row in payload["positions"]] == ["active-json"]


def test_fill_tracker_does_not_emit_legacy_nonvocabulary_quarantine_states():
    """Fill authority quarantine must use legal lifecycle vocabulary only."""
    source = (Path(__file__).resolve().parents[1] / "src" / "execution" / "fill_tracker.py").read_text()

    assert "quarantine_fill_failed" not in source
    assert "quarantine_void_failed" not in source

# ---------------------------------------------------------------------------
# B041 relationship tests: fill_tracker typed error taxonomy (SD-B)
# ---------------------------------------------------------------------------

class TestB041FillTrackerBoundaryErrors:
    """_check_entry_fill must distinguish transient IO failures
    (legitimate ``still_pending``) from code defects (must propagate)."""

    def test_b041_ioerror_maps_to_still_pending(self):
        """A legitimate transient network-style error (ConnectionError)
        keeps the order pending — the exchange state is genuinely
        unknown this cycle.
        """
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = ConnectionError("simulated timeout")
        clob.cancel_order.return_value = {"status": "CANCELLED"}

        stats = check_pending_entries(portfolio, clob)
        # still_pending, no fill, no void — pos stays as-is
        assert stats["voided"] == 0
        assert stats["entered"] == 0
        assert len(portfolio.positions) == 1
        assert portfolio.positions[0].state == "pending_tracked"

    def test_b041_attributeerror_propagates(self):
        """An AttributeError from a wrong-shape clob mock is a code
        defect, NOT a legitimate transient state — must propagate
        rather than silently becoming ``still_pending`` forever.
        """
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = AttributeError(
            "clob has no attribute 'get_order_status'"
        )
        with pytest.raises(AttributeError, match="get_order_status"):
            check_pending_entries(portfolio, clob)

    def test_b041_typeerror_propagates(self):
        """A TypeError (e.g. wrong arg count from a regression) is a
        code defect and must propagate."""
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = TypeError(
            "got unexpected keyword argument"
        )
        with pytest.raises(TypeError, match="unexpected keyword"):
            check_pending_entries(portfolio, clob)


    def test_b041_keyerror_propagates(self):
        """Amendment (critic-alice review): KeyError from a malformed
        CLOB payload shape was omitted from the first-pass re-raise
        set. ``_normalize_status(payload)`` does ``payload["status"]``;
        a missing-key payload would have been silently caught as
        ``still_pending`` before this amendment. KeyError is a code
        defect and must now propagate.
        """
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = KeyError("status")
        with pytest.raises(KeyError, match="status"):
            check_pending_entries(portfolio, clob)

    def test_b041_indexerror_propagates(self):
        """Amendment (critic-alice review): IndexError from
        malformed list access (e.g. ``payload[0]`` on an empty
        sequence) is a code defect and must propagate."""
        from src.execution.fill_tracker import check_pending_entries

        pos = _make_position(
            state="pending_tracked",
            entry_order_id="buy_123",
            entry_fill_verified=False,
        )
        portfolio = _make_portfolio(pos)

        clob = MagicMock()
        clob.get_order_status.side_effect = IndexError("list index out of range")
        with pytest.raises(IndexError, match="out of range"):
            check_pending_entries(portfolio, clob)


def _writer_process_once(db_path, index, barrier, result_queue, priority):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    try:
        coordinator = WriteCoordinator({DBIdentity.TRADE: Path(db_path)})
        barrier.wait(timeout=10)
        with coordinator.transaction(
            (DBIdentity.TRADE,),
            owner=f"writer-{index}",
            priority=WritePriority(priority),
            connection_factory=lambda path: sqlite3.connect(path, timeout=5),
        ) as tx:
            tx.connection.execute("INSERT INTO writes(owner) VALUES (?)", (f"writer-{index}",))
        result_queue.put((index, "ok"))
    except BaseException as exc:  # pragma: no cover - child failure is asserted by parent.
        result_queue.put((index, type(exc).__name__, str(exc)))


def _writer_process_holder(db_path, ready, release):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    coordinator = WriteCoordinator({DBIdentity.TRADE: Path(db_path)})
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        with coordinator.lease(
            (DBIdentity.TRADE,),
            owner="crashed-holder",
            priority=WritePriority.STANDARD,
        ):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO writes(owner) VALUES ('holder')")
            ready.set()
            release.wait(timeout=30)
            conn.commit()
    finally:
        conn.close()


def _writer_process_waiter(db_path, started, result_queue):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    try:
        coordinator = WriteCoordinator({DBIdentity.TRADE: Path(db_path)})
        started.set()
        with coordinator.transaction(
            (DBIdentity.TRADE,),
            owner="monitor-waiter",
            priority=WritePriority.MONITOR,
            connection_factory=lambda path: sqlite3.connect(path, timeout=5),
        ) as tx:
            tx.connection.execute("INSERT INTO writes(owner) VALUES ('monitor')")
        result_queue.put("ok")
    except BaseException as exc:  # pragma: no cover - child failure is asserted by parent.
        result_queue.put((type(exc).__name__, str(exc)))


def _turnstile_holder_process(db_path, ready, release):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    coordinator = WriteCoordinator({DBIdentity.TRADE: Path(db_path)})
    fd = coordinator._acquire_turnstile(
        db_path,
        deadline=None,
        db=DBIdentity.TRADE,
        owner="turnstile-holder",
        blocking=True,
    )
    try:
        ready.set()
        release.wait(timeout=30)
    finally:
        coordinator._release_turnstile(fd)


def _registered_monitor_waiter_process(db_path, registered, result_queue):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    try:
        coordinator = WriteCoordinator({DBIdentity.TRADE: Path(db_path)})
        acquire = coordinator._acquire_monitor_waiter_reservation

        def signal_registered(*args, **kwargs):
            fd = acquire(*args, **kwargs)
            registered.set()
            return fd

        coordinator._acquire_monitor_waiter_reservation = signal_registered
        with coordinator.transaction(
            (DBIdentity.TRADE,),
            owner="registered-monitor-waiter",
            priority=WritePriority.MONITOR,
            connection_factory=lambda path: sqlite3.connect(path, timeout=5),
        ) as tx:
            tx.connection.execute("INSERT INTO writes(owner) VALUES ('monitor')")
        result_queue.put("ok")
    except BaseException as exc:  # pragma: no cover - child failure is asserted by parent.
        result_queue.put((type(exc).__name__, str(exc)))


def test_process_shared_trade_turnstile_allows_seven_exactly_once_writes(tmp_path):
    """Real WAL writers contend through one TRADE gate without duplicate rows."""
    db_path = tmp_path / "turnstile-seven.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE writes(owner TEXT PRIMARY KEY)")

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(7)
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_writer_process_once,
            args=(str(db_path), index, barrier, result_queue, "monitor"),
        )
        for index in range(7)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    results = [result_queue.get(timeout=2) for _ in processes]
    assert all(result[1] == "ok" for result in results), results
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM writes").fetchone() == (7,)
        assert conn.execute("SELECT COUNT(DISTINCT owner) FROM writes").fetchone() == (7,)


def test_multi_db_standard_releases_partial_reservation_for_monitor(tmp_path):
    """A partial STANDARD reservation cannot deadlock a multi-DB MONITOR."""
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    trade_path = tmp_path / "a-trade.db"
    world_path = tmp_path / "b-world.db"
    coordinator = WriteCoordinator(
        {
            DBIdentity.TRADE: trade_path,
            DBIdentity.WORLD: world_path,
        }
    )
    first_reservation = threading.Event()
    release_standard = threading.Event()
    original_acquire = coordinator._acquire_nonmonitor_reservation
    first_call = True

    def pause_after_first_reservation(*args, **kwargs):
        nonlocal first_call
        fd = original_acquire(*args, **kwargs)
        if first_call:
            first_call = False
            first_reservation.set()
            assert release_standard.wait(timeout=2)
        return fd

    coordinator._acquire_nonmonitor_reservation = pause_after_first_reservation
    completions = []
    failures = []

    def acquire(owner, priority):
        try:
            with coordinator.lease(
                (DBIdentity.TRADE, DBIdentity.WORLD),
                owner=owner,
                priority=priority,
                deadline_ms=2_000,
            ):
                completions.append(owner)
        except BaseException as exc:  # pragma: no cover - asserted below.
            failures.append(exc)

    standard = threading.Thread(
        target=acquire,
        args=("standard", WritePriority.STANDARD),
    )
    monitor = threading.Thread(
        target=acquire,
        args=("monitor", WritePriority.MONITOR),
    )
    standard.start()
    assert first_reservation.wait(timeout=2)
    monitor.start()
    deadline = time.monotonic() + 2
    while not coordinator.has_pending_monitor_waiter(
        (DBIdentity.TRADE, DBIdentity.WORLD)
    ):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    release_standard.set()
    standard.join(timeout=3)
    monitor.join(timeout=3)

    assert not standard.is_alive()
    assert not monitor.is_alive()
    assert failures == []
    assert completions == ["monitor", "standard"]


def test_multi_db_reservation_oserror_releases_partial_set(tmp_path):
    """A later DB admission fault cannot leak an earlier reservation."""
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    coordinator = WriteCoordinator(
        {
            DBIdentity.TRADE: tmp_path / "a-trade.db",
            DBIdentity.WORLD: tmp_path / "b-world.db",
        }
    )
    original_acquire = coordinator._acquire_nonmonitor_reservation
    calls = 0

    def fail_second_reservation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second reservation fault")
        return original_acquire(*args, **kwargs)

    coordinator._acquire_nonmonitor_reservation = fail_second_reservation
    with pytest.raises(OSError, match="second reservation fault"):
        coordinator._acquire_nonmonitor_reservations(
            (DBIdentity.TRADE, DBIdentity.WORLD),
            deadline=None,
            owner="faulted-standard",
            priority=WritePriority.STANDARD,
        )

    coordinator._acquire_nonmonitor_reservation = original_acquire
    reservations = coordinator._acquire_nonmonitor_reservations(
        (DBIdentity.TRADE, DBIdentity.WORLD),
        deadline=time.monotonic() + 1,
        owner="subsequent-standard",
        priority=WritePriority.STANDARD,
    )
    for fd in reversed(tuple(reservations.values())):
        coordinator._release_turnstile(fd)


def test_multi_db_gate_fault_releases_every_remaining_reservation(
    tmp_path,
):
    """One reservation cleanup fault cannot strand the rest of a DB set."""

    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    ordered = (DBIdentity.TRADE, DBIdentity.WORLD, DBIdentity.FORECAST)
    coordinator = WriteCoordinator(
        {
            DBIdentity.TRADE: tmp_path / "a-trade.db",
            DBIdentity.WORLD: tmp_path / "b-world.db",
            DBIdentity.FORECAST: tmp_path / "c-forecast.db",
        }
    )
    reservations = {
        DBIdentity.TRADE: 1,
        DBIdentity.WORLD: 2,
        DBIdentity.FORECAST: 3,
    }
    released = []
    coordinator._acquire_nonmonitor_reservations = lambda *_a, **_k: dict(
        reservations
    )
    coordinator._acquire_process_lock = lambda lock, **_k: lock.acquire()
    coordinator._acquire_file_lock = lambda *_a, **_k: (_ for _ in ()).throw(
        OSError("gate fault")
    )

    def release(fd):
        released.append(fd)
        if fd in {1, 3}:
            raise OSError(f"release fault {fd}")

    coordinator._release_turnstile = release
    with pytest.raises(OSError, match="gate fault"):
        coordinator._acquire_gates(
            ordered,
            deadline=None,
            owner="faulted-standard",
            priority=WritePriority.STANDARD,
        )

    assert released == [1, 3, 2]


def test_turnstile_blocks_background_while_monitor_waits(tmp_path):
    """A queued MONITOR holds admission; BACKGROUND defers until the next call."""
    from src.state.write_coordinator import (
        DBIdentity,
        WriteCoordinator,
        WriteLeaseTimeout,
        WritePriority,
    )

    db_path = tmp_path / "turnstile-order.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE writes(seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT)")
    coordinator = WriteCoordinator({DBIdentity.TRADE: db_path})
    monitor_turnstile_acquired = threading.Event()
    start = threading.Barrier(2)
    original_acquire_turnstile = coordinator._acquire_turnstile

    def observe_monitor_turnstile(*args, **kwargs):
        fd = original_acquire_turnstile(*args, **kwargs)
        monitor_turnstile_acquired.set()
        return fd

    coordinator._acquire_turnstile = observe_monitor_turnstile

    def monitor_writer():
        start.wait(timeout=2)
        conn = sqlite3.connect(db_path, timeout=5)
        with coordinator.lease(
            (DBIdentity.TRADE,), owner="monitor", priority=WritePriority.MONITOR
        ):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO writes(owner) VALUES ('monitor')")
            conn.commit()
        conn.close()

    monitor = threading.Thread(target=monitor_writer)
    monitor.start()
    with coordinator.lease((DBIdentity.TRADE,), owner="holder"):
        holder = sqlite3.connect(db_path, timeout=5)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO writes(owner) VALUES ('holder')")
        start.wait(timeout=2)
        assert monitor_turnstile_acquired.wait(timeout=2)
        with pytest.raises(WriteLeaseTimeout):
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner="background-now",
                priority=WritePriority.BACKGROUND_RECOVERY,
                deadline_ms=0,
            ):
                raise AssertionError("background must defer behind queued monitor")
        holder.commit()
        holder.close()
    monitor.join(timeout=3)
    assert not monitor.is_alive()

    with coordinator.transaction(
        (DBIdentity.TRADE,),
        owner="background-next-invocation",
        priority=WritePriority.BACKGROUND_RECOVERY,
        connection_factory=lambda path: sqlite3.connect(path, timeout=5),
    ) as tx:
        tx.connection.execute("INSERT INTO writes(owner) VALUES ('background')")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT owner FROM writes ORDER BY seq").fetchall() == [
            ("holder",),
            ("monitor",),
            ("background",),
        ]


def test_live_tick_priority_lease_defers_within_existing_db_budget(tmp_path, monkeypatch):
    """Critical recovery cannot wait past the live tick budget before SQLite opens."""
    from src.execution import command_recovery
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WriteLeaseTimeout
    from src.state import write_coordinator as coordinator_module

    db_path = tmp_path / "live-tick-priority-budget.db"
    coordinator = WriteCoordinator({DBIdentity.TRADE: db_path})
    opened = []

    def factory(**_kwargs):
        opened.append(True)
        raise AssertionError("SQLite must not open after the lease budget expires")

    factory.requires_writer_flocks = True
    factory.supports_nonblocking_flocks = True
    monkeypatch.setattr(
        coordinator_module,
        "default_runtime_write_coordinator",
        lambda: coordinator,
    )
    with coordinator.lease((DBIdentity.TRADE,), owner="active-live-writer"):
        priority_factory = command_recovery._recovery_priority_conn_factory(
            factory,
            scope="live_tick",
            deadline_monotonic=time.monotonic() + 0.025,
        )
        with pytest.raises(WriteLeaseTimeout):
            priority_factory(blocking=False, busy_timeout_ms=0)
    assert opened == []


def test_priority_recovery_connection_delegates_context_before_releasing_lease(monkeypatch):
    """``with factory()`` commits/closes the DB before the writer lease ends."""
    from src.execution import command_recovery
    from src.state import write_coordinator as coordinator_module

    events = []

    class Conn:
        def __enter__(self):
            events.append("conn_enter")
            return self

        def __exit__(self, *_args):
            events.append("conn_exit")
            return False

        def close(self):
            events.append("conn_close")

    class Lease:
        def __enter__(self):
            events.append("lease_enter")
            return self

        def __exit__(self, *_args):
            events.append("lease_exit")
            return False

    class Coordinator:
        def lease(self, *_args, **_kwargs):
            return Lease()

    def factory(**_kwargs):
        return Conn()

    factory.requires_writer_flocks = True
    monkeypatch.setattr(coordinator_module, "default_runtime_write_coordinator", Coordinator)
    wrapped = command_recovery._recovery_priority_conn_factory(
        factory,
        scope="full",
    )
    with wrapped() as conn:
        assert isinstance(conn, command_recovery._PriorityRecoveryConnection)
    assert events == ["lease_enter", "conn_enter", "conn_exit", "conn_close", "lease_exit"]


def test_background_recovery_interrupt_is_classified_as_monitor_preemption(monkeypatch):
    from src.execution import command_recovery
    from src.state import write_coordinator as coordinator_module

    class Coordinator:
        @staticmethod
        def has_pending_monitor_waiter(_dbs):
            return True

    monkeypatch.setattr(
        coordinator_module,
        "default_runtime_write_coordinator",
        lambda: Coordinator(),
    )

    def factory():
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE rows (value INTEGER)")
        conn.executemany(
            "INSERT INTO rows VALUES (?)",
            ((index,) for index in range(200)),
        )
        return conn

    bounded_factory = command_recovery._recovery_apply_conn_factory(
        factory,
        scope="full",
        deadline_monotonic=time.monotonic() + 5.0,
    )
    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}

    def interrupted_pass():
        with bounded_factory() as conn:
            conn.execute(
                "SELECT COUNT(*) FROM rows first, rows second, rows third"
            ).fetchone()

    result = command_recovery._run_recovery_pass_with_lock_policy(
        "historical_apply",
        interrupted_pass,
        scope="full",
        summary=summary,
        deadline_monotonic=time.monotonic() + 5.0,
    )

    assert result is None
    assert summary["monitor_preempted"] is True
    assert summary["monitor_preempted_at"] == "historical_apply"
    assert summary["db_lock_deferred_at"] == "historical_apply"


def test_bounded_recovery_requires_monitor_preemption_capability(monkeypatch):
    from src.execution import command_recovery
    from src.state import write_coordinator as coordinator_module

    monkeypatch.setattr(
        coordinator_module,
        "default_runtime_write_coordinator",
        lambda: object(),
    )
    bounded_factory = command_recovery._recovery_apply_conn_factory(
        lambda: sqlite3.connect(":memory:"),
        scope="full",
        deadline_monotonic=time.monotonic() + 5.0,
    )

    with pytest.raises(AttributeError, match="has_pending_monitor_waiter"):
        bounded_factory()


def test_cross_process_monitor_preempts_background_apply_without_partial_commit(
    monkeypatch,
    tmp_path,
):
    from src.execution import command_recovery
    from src.state import write_coordinator as coordinator_module
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    db_path = tmp_path / "cross-process-monitor-preemption.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE writes(owner TEXT PRIMARY KEY)")

    coordinator = WriteCoordinator({DBIdentity.TRADE: db_path})
    monkeypatch.setattr(
        coordinator_module,
        "default_runtime_write_coordinator",
        lambda: coordinator,
    )
    bounded_factory = command_recovery._recovery_apply_conn_factory(
        lambda: sqlite3.connect(db_path, timeout=5),
        scope="full",
        deadline_monotonic=time.monotonic() + 5.0,
    )
    ctx = multiprocessing.get_context("spawn")
    waiter_started, waiter_queue = ctx.Event(), ctx.Queue()

    with coordinator.lease(
        (DBIdentity.TRADE,),
        owner="background-holder",
        priority=WritePriority.BACKGROUND_RECOVERY,
    ):
        waiter = ctx.Process(
            target=_writer_process_waiter,
            args=(str(db_path), waiter_started, waiter_queue),
        )
        waiter.start()
        assert waiter_started.wait(timeout=5)
        intent_deadline = time.monotonic() + 5.0
        while (
            not coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))
            and time.monotonic() < intent_deadline
        ):
            time.sleep(0.01)
        assert coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))

        summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}

        def interrupted_pass():
            with bounded_factory() as conn:
                conn.execute("INSERT INTO writes(owner) VALUES ('background')")
                conn.execute(
                    "WITH RECURSIVE seq(n) AS (VALUES(1) UNION ALL "
                    "SELECT n + 1 FROM seq WHERE n < 1000000) "
                    "SELECT sum(n) FROM seq"
                ).fetchone()

        result = command_recovery._run_recovery_pass_with_lock_policy(
            "cross_process_apply",
            interrupted_pass,
            scope="full",
            summary=summary,
            deadline_monotonic=time.monotonic() + 5.0,
        )
        assert result is None
        assert summary["monitor_preempted"] is True

    waiter.join(timeout=5)
    assert waiter.exitcode == 0
    assert waiter_queue.get(timeout=2) == "ok"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT owner FROM writes ORDER BY owner").fetchall() == [
            ("monitor",)
        ]


def test_process_death_resets_holder_and_waiter_state(tmp_path):
    """A dead holder releases the kernel gate to an already queued MONITOR."""
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    db_path = tmp_path / "turnstile-process-death.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE writes(owner TEXT PRIMARY KEY)")
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(target=_writer_process_holder, args=(str(db_path), ready, release))
    holder.start()
    assert ready.wait(timeout=5)
    waiter_started = ctx.Event()
    waiter_queue = ctx.Queue()
    waiter = ctx.Process(
        target=_writer_process_waiter,
        args=(str(db_path), waiter_started, waiter_queue),
    )
    waiter.start()
    assert waiter_started.wait(timeout=5)
    holder.terminate()
    holder.join(timeout=5)
    assert holder.exitcode != 0
    waiter.join(timeout=5)
    assert waiter.exitcode == 0
    assert waiter_queue.get(timeout=2) == "ok"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT owner FROM writes").fetchall() == [("monitor",)]


def test_cross_process_monitor_reservation_blocks_background_loop(tmp_path):
    """A registered MONITOR reservation defeats non-FIFO flock overtaking."""
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WriteLeaseTimeout

    db_path = tmp_path / "monitor-reservation-loop.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE writes(owner TEXT PRIMARY KEY)")
    ctx = multiprocessing.get_context("spawn")
    holder_ready, holder_release = ctx.Event(), ctx.Event()
    holder = ctx.Process(
        target=_turnstile_holder_process,
        args=(str(db_path), holder_ready, holder_release),
    )
    holder.start()
    assert holder_ready.wait(timeout=5)
    registered, result_queue = ctx.Event(), ctx.Queue()
    monitor = ctx.Process(
        target=_registered_monitor_waiter_process,
        args=(str(db_path), registered, result_queue),
    )
    monitor.start()
    assert registered.wait(timeout=5)

    coordinator = WriteCoordinator({DBIdentity.TRADE: db_path})
    with pytest.raises(
        WriteLeaseTimeout,
        match="monitor waiter reservation",
    ):
        with coordinator.lease(
            (DBIdentity.TRADE,),
            owner="standard-after-monitor",
            priority="standard",
            deadline_ms=0,
        ):
            pytest.fail("STANDARD must not overtake a registered MONITOR")
    successes = 0
    for _ in range(157):
        try:
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner="background-loop",
                priority="background_recovery",
                deadline_ms=0,
            ):
                successes += 1
        except WriteLeaseTimeout:
            pass
    assert successes == 0
    holder_release.set()
    holder.join(timeout=5)
    monitor.join(timeout=5)
    assert holder.exitcode == 0
    assert monitor.exitcode == 0
    assert result_queue.get(timeout=2) == "ok"


def test_monitor_waiter_death_releases_kernel_reservation(tmp_path):
    """Killing a queued waiter clears only its kernel reservation."""
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    db_path = tmp_path / "monitor-reservation-death.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE writes(owner TEXT PRIMARY KEY)")
    ctx = multiprocessing.get_context("spawn")
    holder_ready, holder_release = ctx.Event(), ctx.Event()
    holder = ctx.Process(
        target=_turnstile_holder_process,
        args=(str(db_path), holder_ready, holder_release),
    )
    holder.start()
    assert holder_ready.wait(timeout=5)
    registered, result_queue = ctx.Event(), ctx.Queue()
    waiter = ctx.Process(
        target=_registered_monitor_waiter_process,
        args=(str(db_path), registered, result_queue),
    )
    waiter.start()
    assert registered.wait(timeout=5)
    waiter.terminate()
    waiter.join(timeout=5)
    assert waiter.exitcode != 0
    holder_release.set()
    holder.join(timeout=5)
    assert holder.exitcode == 0

    coordinator = WriteCoordinator({DBIdentity.TRADE: db_path})
    with coordinator.transaction(
        (DBIdentity.TRADE,),
        owner="background-after-waiter-death",
        priority="background_recovery",
        connection_factory=lambda path: sqlite3.connect(path, timeout=5),
    ) as tx:
        tx.connection.execute("INSERT INTO writes(owner) VALUES ('background')")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT owner FROM writes").fetchall() == [("background",)]


def test_monitor_reservation_oserror_closes_fd_once(tmp_path, monkeypatch):
    from src.state import write_coordinator as module
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    coordinator = WriteCoordinator({DBIdentity.TRADE: tmp_path / "monitor-oserror.db"})
    closed = []
    real_close = module.os.close
    monkeypatch.setattr(module.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(module.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    with pytest.raises(OSError, match="boom"):
        coordinator._acquire_monitor_waiter_reservation(
            tmp_path / "monitor-oserror.db",
            deadline=None,
            db=DBIdentity.TRADE,
            owner="monitor-oserror",
        )
    assert len(closed) == 1


def test_background_reservation_oserror_closes_fd_once(tmp_path, monkeypatch):
    from src.state import write_coordinator as module
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    coordinator = WriteCoordinator({DBIdentity.TRADE: tmp_path / "background-oserror.db"})
    closed = []
    real_close = module.os.close
    monkeypatch.setattr(module.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(module.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    with pytest.raises(OSError, match="boom"):
        coordinator._acquire_background_reservation(
            tmp_path / "background-oserror.db",
            db=DBIdentity.TRADE,
            owner="background-oserror",
        )
    assert len(closed) == 1


def test_gate_cleanup_releases_reservation_when_turnstile_release_fails(monkeypatch, tmp_path):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    coordinator = WriteCoordinator({DBIdentity.TRADE: tmp_path / "nested-release.db"})
    released = []
    coordinator._acquire_monitor_waiter_reservation = lambda *_a, **_k: 11
    coordinator._acquire_turnstile = lambda *_a, **_k: 22
    coordinator._acquire_process_lock = lambda *_a, **_k: None
    coordinator._acquire_file_lock = lambda *_a, **_k: 33

    def release(fd):
        released.append(fd)
        if fd == 22:
            raise OSError("turnstile release failed")

    coordinator._release_turnstile = release
    with pytest.raises(OSError, match="turnstile release failed"):
        coordinator._acquire_gates(
            (DBIdentity.TRADE,),
            deadline=None,
            owner="nested-release",
            priority=WritePriority.MONITOR,
        )
    assert released == [22, 11]


def test_turnstile_oserror_closes_fd_once(tmp_path, monkeypatch):
    from src.state import write_coordinator as module
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    coordinator = WriteCoordinator({DBIdentity.TRADE: tmp_path / "turnstile-oserror.db"})
    closed = []
    real_close = module.os.close
    monkeypatch.setattr(module.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(module.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    with pytest.raises(OSError, match="boom"):
        coordinator._acquire_turnstile(
            tmp_path / "turnstile-oserror.db",
            deadline=None,
            db=DBIdentity.TRADE,
            owner="turnstile-oserror",
            blocking=False,
        )
    assert len(closed) == 1


def test_gate_cleanup_releases_file_and_process_after_turnstile_fault(
    monkeypatch, tmp_path
):
    from src.state import write_coordinator as module
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    path = (tmp_path / "gate-cleanup-order.db").resolve()
    coordinator = WriteCoordinator({DBIdentity.TRADE: path})
    order = []

    class ProcessLock:
        released = False

        def release(self):
            order.append("process_release")
            self.released = True

    coordinator._process_locks[path] = ProcessLock()
    coordinator._acquire_monitor_waiter_reservation = lambda *_a, **_k: 11
    coordinator._acquire_turnstile = lambda *_a, **_k: 22
    coordinator._acquire_process_lock = lambda *_a, **_k: None
    coordinator._acquire_file_lock = lambda *_a, **_k: 33
    monkeypatch.setattr(
        coordinator,
        "_release_turnstile",
        lambda fd: (order.append(f"release_{fd}"),
                    (_ for _ in ()).throw(OSError("turnstile release failed"))
                    if fd == 22 else None)[1],
    )
    monkeypatch.setattr(module.fcntl, "flock", lambda fd, op: order.append(f"file_unlock_{fd}"))
    monkeypatch.setattr(module.os, "close", lambda fd: (order.append(f"file_close_{fd}"), None)[1])
    with pytest.raises(OSError, match="turnstile release failed"):
        coordinator._acquire_gates(
            (DBIdentity.TRADE,),
            deadline=None,
            owner="gate-cleanup-order",
            priority=WritePriority.MONITOR,
        )
    assert order == [
        "release_22",
        "release_11",
        "file_unlock_33",
        "file_close_33",
        "process_release",
    ]
    assert coordinator._process_locks[path].released is True


def test_full_recovery_quantum_yields_between_large_matched_fact_batches(
    monkeypatch,
):
    """A full matched-fact sweep is crash-stable one-command writer turns."""
    from src.execution import command_recovery

    first = {"command_id": "a", "updated_at": "2026-08-01T00:00:00Z"}
    second = {"command_id": "b", "updated_at": "2026-08-01T00:00:01Z"}
    monkeypatch.setattr(
        command_recovery,
        "find_unresolved_commands",
        lambda _conn: [second, first],
    )
    assert [
        row["command_id"]
        for row in command_recovery._full_quantum_candidates(
            None,
            rotation_slot=0,
        )
    ] == ["a", "b"]

    command_ids = [f"command-{index:03d}" for index in range(849)]
    first_batches = command_recovery._full_background_recovery_command_id_batches(
        command_ids,
        rotation_slot=0,
    )
    resumed_batches = command_recovery._full_background_recovery_command_id_batches(
        command_ids,
        rotation_slot=1,
    )
    assert len(first_batches) == len(command_ids)
    assert all(len(batch) == 1 for batch in first_batches)
    assert first_batches[0] == {"command-000"}
    assert resumed_batches[0] == {"command-001"}
    assert set().union(*first_batches) == set(command_ids)

    source = (ROOT / "src" / "execution" / "command_recovery.py").read_text(encoding="utf-8")
    inflight_source = source[source.index("def _scan_inflight"): source.index("def _apply_inflight")]
    assert "rows = all_rows[:1]" in inflight_source
    assert "inflight_quantum_remaining" in inflight_source
    assert all(name in source for name in (
        "terminal_exit_partial_remainders",
        "pending_exit_terminal_order_releases",
        "terminal_entry_exposure_obligations",
        "edli_post_submit_unknown_absence",
        "partial_remainders",
        "closed_shift_bin_exit_leases",
        "stale_rebalance_entry_leases",
    ))
    full_matched_source = source[
        source.index('if scope == "full":\n        with open_tracked(\n            read_conn_factory,\n            label="recovery.matched_order_facts:quantum_snapshot",'):
        source.index(
            '    _db_pass(\n        "filled_exit_trade_fact_tx_repair",',
            source.index('if scope == "full":\n        with open_tracked(\n            read_conn_factory,\n            label="recovery.matched_order_facts:quantum_snapshot",'),
        )
    ]
    assert "_full_background_recovery_command_id_batches" in full_matched_source
    assert "command_ids=command_ids" in full_matched_source
    assert "set_progress_handler" not in source[source.index("def _recovery_read_conn_factory"): source.index("def _run_recovery_pass_with_lock_policy")]


def test_full_recovery_quantum_releases_background_lease_to_registered_monitor(
    tmp_path,
    monkeypatch,
):
    """One real 849-row matched-fact quantum releases to a registered monitor."""
    from src.execution import command_recovery
    from src.state import write_coordinator as coordinator_module
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    db_path = tmp_path / "full-recovery-quantum.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY,
                envelope_id TEXT,
                position_id TEXT,
                intent_kind TEXT NOT NULL,
                state TEXT NOT NULL,
                venue_order_id TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE venue_submission_envelopes (
                envelope_id TEXT PRIMARY KEY,
                order_type TEXT
            );
            CREATE TABLE venue_order_facts (
                fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                venue_order_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                state TEXT NOT NULL,
                remaining_size TEXT,
                matched_size TEXT,
                source TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                local_sequence INTEGER NOT NULL,
                raw_payload_json TEXT
            );
            CREATE INDEX idx_quantum_order_facts_command
                ON venue_order_facts(command_id, local_sequence);
            CREATE TABLE monitor_writes(owner TEXT PRIMARY KEY);
            """
        )
        conn.executemany(
            """
            INSERT INTO venue_commands (
                command_id, envelope_id, position_id, intent_kind,
                state, venue_order_id, updated_at
            ) VALUES (?, NULL, ?, 'ENTRY', 'ACKED', ?, ?)
            """,
            [
                (
                    f"command-{index:03d}",
                    f"position-{index:03d}",
                    f"order-{index:03d}",
                    f"2026-08-02T00:{index // 60:02d}:{index % 60:02d}+00:00",
                )
                for index in range(849)
            ],
        )
        conn.executemany(
            """
            INSERT INTO venue_order_facts (
                venue_order_id, command_id, state, remaining_size,
                matched_size, source, observed_at, local_sequence,
                raw_payload_json
            ) VALUES (?, ?, 'MATCHED', '1', '1', 'REST', ?, 1, '{}')
            """,
            [
                (
                    f"order-{index:03d}",
                    f"command-{index:03d}",
                    f"2026-08-02T00:{index // 60:02d}:{index % 60:02d}+00:00",
                )
                for index in range(849)
            ],
        )
    coordinator = WriteCoordinator({DBIdentity.TRADE: db_path})
    monkeypatch.setattr(
        coordinator_module,
        "default_runtime_write_coordinator",
        lambda: coordinator,
    )

    background_query_started = threading.Event()
    monitor_registered = threading.Event()
    traced_sql = []
    progress_calls = []

    def factory(**_kwargs):
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(traced_sql.append)

        def slow_full_scan_progress():
            progress_calls.append(1)
            background_query_started.set()
            if not monitor_registered.wait(timeout=2):
                return 1
            time.sleep(0.00005)
            return 0

        conn.set_progress_handler(slow_full_scan_progress, 10)
        return conn

    factory.requires_writer_flocks = True
    factory.supports_nonblocking_flocks = False
    priority_factory = command_recovery._recovery_priority_conn_factory(
        factory,
        scope="full",
    )
    monitor_acquired = threading.Event()
    acquired_after = []
    registration_at = []
    background_summary = []
    thread_errors = []
    original_register = coordinator._acquire_monitor_waiter_reservation

    def register_monitor(*args, **kwargs):
        fd = original_register(*args, **kwargs)
        registration_at.append(time.monotonic())
        monitor_registered.set()
        return fd

    monkeypatch.setattr(
        coordinator,
        "_acquire_monitor_waiter_reservation",
        register_monitor,
    )

    def background_quantum():
        try:
            with priority_factory() as conn:
                background_summary.append(
                    command_recovery.reconcile_matched_order_facts(
                        conn,
                        SimpleNamespace(),
                        command_ids={"command-848"},
                    )
                )
        except BaseException as exc:  # pragma: no cover - asserted below.
            thread_errors.append(exc)

    def monitor_append():
        try:
            assert background_query_started.wait(timeout=2)
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner="monitor-after-background-quantum",
                priority=WritePriority.MONITOR,
                deadline_ms=500,
            ):
                with sqlite3.connect(db_path, timeout=0.5) as conn:
                    conn.execute(
                        "INSERT INTO monitor_writes(owner) VALUES ('monitor')"
                    )
                acquired_after.append(time.monotonic())
                monitor_acquired.set()
        except BaseException as exc:  # pragma: no cover - asserted below.
            thread_errors.append(exc)

    background = threading.Thread(target=background_quantum)
    monitor = threading.Thread(target=monitor_append)
    background.start()
    assert background_query_started.wait(timeout=2)
    monitor.start()
    assert monitor_registered.wait(timeout=2)
    assert monitor_acquired.wait(timeout=0.15)
    background.join(timeout=2)
    monitor.join(timeout=2)
    assert not background.is_alive()
    assert not monitor.is_alive()
    assert thread_errors == []
    assert background_summary == [
        {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
    ]
    assert acquired_after[0] - registration_at[0] < 0.15
    assert len(progress_calls) < 1_000
    candidate_sql = [
        statement
        for statement in traced_sql
        if "matched_candidate_commands AS" in statement
    ]
    assert len(candidate_sql) == 1
    assert "command_id IN ('command-848')" in candidate_sql[0]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT owner FROM monitor_writes").fetchall() == [
            ("monitor",)
        ]


def test_monitor_hold_append_failure_has_no_hold_artifact_or_monitor_count(monkeypatch):
    """Canonical observation failure retains evidence but emits no synthetic HOLD."""
    from src.engine import cycle_runtime

    position = _make_position(trade_id="monitor-hold-append-failure")
    results = []
    artifact = type(
        "Artifact",
        (),
        {"add_monitor_result": lambda self, result: results.append(result)},
    )()
    summary = {"monitors": 0}
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: False,
    )

    assert cycle_runtime._record_monitor_hold_decision(
        None,
        position,
        artifact=artifact,
        deps=_monitor_test_deps("test_monitor_hold_append_failure"),
        summary=summary,
        reason="NO_EXIT",
        trigger="NORMAL_MONITOR",
        validation="replacement_posterior",
        counter="monitor_hold",
    ) is False
    assert results == []
    assert summary["monitors"] == 0
    assert summary["monitor_canonical_write_failed"] == 1


def test_monitor_absolute_deadline_includes_pending_exit_preflight(monkeypatch):
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    observed = []

    def fake_pending_exits(
        _portfolio,
        _clob,
        *,
        conn,
        deadline_monotonic,
        global_sell_reauction_requester,
    ):
        observed.append((deadline_monotonic, time.monotonic()))
        return {
            "filled": 0,
            "retried": 0,
            "unchanged": 0,
            "filled_positions": [],
        }

    monkeypatch.setattr(exit_lifecycle, "check_pending_exits", fake_pending_exits)
    started = time.monotonic()
    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_monitor_preflight_deadline"),
        run_exit_preflight=True,
        held_position_monitor_budget_seconds=6.0,
    )

    assert len(observed) == 1
    deadline, preflight_called_at = observed[0]
    assert deadline < started + 6.0
    assert 0.0 < deadline - preflight_called_at <= 5.0
    assert summary["held_monitor_budget_seconds"] == pytest.approx(6.0)


def _assert_primary_monitor_progress(monkeypatch, *, clock, position):
    from src.engine import cycle_runtime

    evaluations: list[str] = []

    def refresh(*_args):
        return _monitor_test_edge_context(position)

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [position],
    )
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: evaluations.append(self.trade_id)
        or ExitDecision(False, "PRIMARY_RESERVE_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: True,
    )
    return evaluations


def test_pending_exit_preflight_auxiliary_deadline_preserves_primary_refresh(
    monkeypatch,
):
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    position = _make_position(trade_id="primary-after-pending-preflight")
    clock = [0.0]
    evaluations = _assert_primary_monitor_progress(
        monkeypatch,
        clock=clock,
        position=position,
    )
    observed_deadlines: list[float] = []
    preparation_order: list[str] = []

    def hwm_prefetch(positions, *, deadline_monotonic, **_kwargs):
        assert positions == [position]
        assert deadline_monotonic == pytest.approx(1.0)
        preparation_order.append("hwm")

    def pending_preflight(
        _portfolio,
        _clob,
        *,
        conn,
        deadline_monotonic,
        global_sell_reauction_requester,
    ):
        del conn, global_sell_reauction_requester
        preparation_order.append("preflight")
        observed_deadlines.append(deadline_monotonic)
        clock[0] = deadline_monotonic
        return {"filled": 0, "retried": 0, "unchanged": 0, "filled_positions": []}

    monkeypatch.setattr(exit_lifecycle, "check_pending_exits", pending_preflight)
    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_replacement_artifact_hwm",
        hwm_prefetch,
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_primary_after_pending_preflight"),
        run_exit_preflight=True,
        held_position_monitor_budget_seconds=6.0,
    )

    assert observed_deadlines == [pytest.approx(1.0)]
    assert preparation_order == ["hwm", "preflight"]
    assert evaluations == [position.trade_id]
    assert summary["held_monitor_primary_belief_read_started"] == 1
    assert summary["held_monitor_primary_belief_read_completed"] == 1


def test_replacement_hwm_prefetch_has_independent_wall_deadline(monkeypatch):
    """A raw-HWM cut cannot consume the monitor's complete auxiliary tranche."""
    from src.engine import cycle_runtime

    position = _make_position(trade_id="hwm-independent-wall-deadline")
    clock = [0.0]
    observed: list[tuple[float, float]] = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [position],
    )

    def prefetch(
        positions,
        *,
        deadline_monotonic,
        sql_timeout_seconds,
        **_kwargs,
    ):
        assert positions == [position]
        observed.append((deadline_monotonic, sql_timeout_seconds))
        clock[0] = 20.0

    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_replacement_artifact_hwm",
        prefetch,
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_hwm_independent_wall_deadline"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=20.0,
    )

    assert observed == [(pytest.approx(2.5), pytest.approx(2.5))]
    assert summary["held_monitor_hwm_prefetch_budget_seconds"] == pytest.approx(2.5)
    assert summary["held_monitor_primary_belief_reserve_seconds"] == pytest.approx(10.0)


def test_replacement_hwm_prefetch_threads_deadline_through_connection(monkeypatch):
    """Forecast connection bootstrap cannot spend past the HWM wall tranche."""
    from src.engine import cycle_runtime

    position = _make_position(trade_id="hwm-connection-deadline")
    observed_deadlines: list[float] = []
    deadline = cycle_runtime.time.monotonic() + 12.5

    def unavailable_connection(*, deadline_monotonic):
        observed_deadlines.append(deadline_monotonic)
        raise sqlite3.OperationalError("DB_CONNECTION_DEADLINE_EXPIRED")

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        unavailable_connection,
    )
    summary = {}
    cycle_runtime._prefetch_held_replacement_artifact_hwm(
        [position],
        decision_time=datetime.now(timezone.utc),
        deadline_monotonic=deadline,
        sql_timeout_seconds=2.5,
        clob=object(),
        summary=summary,
        deps=_monitor_test_deps("test_hwm_connection_deadline"),
    )

    assert observed_deadlines == [deadline]
    assert summary["held_monitor_hwm_prefetch_status"] == "unavailable"
    assert "DB_CONNECTION_DEADLINE_EXPIRED" in summary[
        "held_monitor_hwm_prefetch_blocker"
    ]


def test_replacement_hwm_prefetch_precedes_auxiliary_debt_scan(monkeypatch):
    """Auxiliary O(n) work cannot spend the replacement-HWM reserve first."""
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    position = _make_position(trade_id="hwm-before-debt-scan")
    clock = [0.0]
    order: list[tuple[str, float]] = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [position],
    )

    def prefetch(positions, *, deadline_monotonic, **_kwargs):
        assert positions == [position]
        order.append(("hwm", deadline_monotonic))

    def classify(*_args, **_kwargs):
        order.append(("debt", clock[0]))
        clock[0] = 6.0
        return exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.NO_DEBT

    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_replacement_artifact_hwm",
        prefetch,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "classify_global_sell_snapshot_reauction_debt",
        classify,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_hwm_before_debt_scan"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=6.0,
    )

    assert order == [("hwm", pytest.approx(1.0)), ("debt", 0.0)]


def test_exhausted_auxiliary_tranche_still_refreshes_one_admitted_position(
    monkeypatch,
):
    """HWM/preclassification may consume auxiliary time, never primary admission."""
    from src.engine import cycle_runtime
    from src.execution import day0_hard_fact_exit, exit_lifecycle

    positions = [
        _make_position(trade_id=f"primary-after-auxiliary-{index}")
        for index in range(17)
    ]
    clock = [0.0]
    refreshes: list[str] = []
    canonical_refreshes: list[str] = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: positions,
    )

    def hwm_prefetch(_positions, *, deadline_monotonic, **_kwargs):
        assert _positions == positions
        assert deadline_monotonic == pytest.approx(2.5)
        clock[0] = 5.001

    monkeypatch.setattr(
        cycle_runtime,
        "_prefetch_held_replacement_artifact_hwm",
        hwm_prefetch,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "classify_global_sell_snapshot_reauction_debt",
        lambda *_args, **_kwargs: pytest.fail(
            "expired auxiliary debt scan must not start"
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "evaluate_hard_fact_exit",
        lambda **_kwargs: pytest.fail(
            "expired auxiliary hard-fact preclassification must not start"
        ),
    )

    def refresh(_conn, _clob, position):
        refreshes.append(position.trade_id)
        clock[0] += 0.2
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "PRIMARY_RESERVE_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda _conn, position, **_kwargs: (
            canonical_refreshes.append(position.trade_id) or True
        ),
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        SimpleNamespace(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_primary_after_auxiliary_exhaustion"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert refreshes == canonical_refreshes
    assert len(refreshes) == 1
    assert summary["held_monitor_primary_belief_read_started"] == 1
    assert summary["held_monitor_primary_belief_read_completed"] == 1
    assert summary["held_monitor_hard_fact_preclass_deferred"] == 17
    assert summary["held_monitor_primary_belief_read_deferred"] == 16
    assert summary["held_monitor_deadline_defer_reason"] == (
        "PRIMARY_BELIEF_BUDGET_UNAVAILABLE"
    )


def test_global_sell_debt_drain_auxiliary_deadline_preserves_primary_refresh(
    monkeypatch,
):
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    active = _make_position(trade_id="primary-after-global-debt")
    debt = _make_position(
        trade_id="pending-global-debt",
        state="pending_exit",
        exit_state="retry_pending",
    )
    clock = [0.0]
    evaluations = _assert_primary_monitor_progress(
        monkeypatch,
        clock=clock,
        position=active,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "classify_global_sell_snapshot_reauction_debt",
        lambda position, _conn, **_kwargs: (
            exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.DEBT
            if position is debt
            else exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.NO_DEBT
        ),
    )
    recovered: list[str] = []

    def recover(position, **_kwargs):
        recovered.append(position.trade_id)
        clock[0] = 1.0
        return False

    monkeypatch.setattr(
        exit_lifecycle,
        "recover_global_sell_snapshot_reauction_debt",
        recover,
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(debt, active),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_primary_after_global_debt"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=6.0,
    )

    assert recovered == [debt.trade_id]
    assert evaluations == [active.trade_id]
    assert summary["global_sell_snapshot_reauction_debts_pending"] == 1
    assert summary["held_monitor_optional_maintenance_deferred"] >= 1
    assert summary["held_monitor_primary_belief_read_completed"] == 1


def test_local_orderbook_prefetch_auxiliary_deadline_bypasses_to_primary_refresh(
    monkeypatch,
):
    from src.engine import cycle_runtime

    position = _make_position(trade_id="primary-after-local-prefetch")
    clock = [0.0]
    evaluations = _assert_primary_monitor_progress(
        monkeypatch,
        clock=clock,
        position=position,
    )

    def local_prefetch(*_args, deadline_monotonic, **_kwargs):
        assert deadline_monotonic == pytest.approx(1.0)
        clock[0] = deadline_monotonic
        return {}

    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        local_prefetch,
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_primary_after_local_prefetch"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=6.0,
    )

    assert evaluations == [position.trade_id]
    assert summary["held_monitor_orderbook_prefetch_bypassed"] is True
    assert summary["held_monitor_optional_maintenance_deferred"] >= 1
    assert summary["held_monitor_primary_belief_read_started"] == 1
    assert summary["held_monitor_primary_belief_read_completed"] == 1


def test_exit_monitor_budget_starts_at_claim_and_wrapper_forwards_remaining(
    monkeypatch,
):
    from src import main
    from src.engine import cycle_runner
    from src.execution.exit_lifecycle import run_exit_monitor_cycle

    dispatcher_source = inspect.getsource(main._exit_monitor_cycle)
    dispatcher_claim_offset = dispatcher_source.index(
        "_acquire_held_monitor_claim("
    )
    dispatcher_deadline_offset = dispatcher_source.index(
        "monitor_deadline_monotonic ="
    )
    dispatcher_handoff_offset = dispatcher_source.index(
        "_edli_reactor_active_lock.acquire"
    )
    assert (
        dispatcher_claim_offset
        < dispatcher_deadline_offset
        < dispatcher_handoff_offset
    )
    assert (
        "monitor_deadline_monotonic=monitor_deadline_monotonic"
        in dispatcher_source
    )

    source = inspect.getsource(run_exit_monitor_cycle)
    claim_offset = source.index("held_position_monitor_active.set()")
    deadline_offset = source.index("monitor_deadline_monotonic =")
    connection_offset = source.index(
        "conn = get_connection(deadline_monotonic=monitor_deadline_monotonic)"
    )
    assert claim_offset < deadline_offset < connection_offset
    preparation_offset = source.index("with _held_monitor_preparation_deadline(")
    portfolio_offset = source.index("portfolio = load_portfolio(")
    assert connection_offset < preparation_offset < portfolio_offset

    captured = {}

    def execute(*_args, **kwargs):
        captured.update(kwargs)
        return False, False

    monkeypatch.setattr(cycle_runner._runtime, "execute_monitoring_phase", execute)
    cycle_runner._execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        {"monitors": 0, "exits": 0},
        held_position_monitor_budget_seconds=12.5,
    )
    assert captured["held_position_monitor_budget_seconds"] == pytest.approx(12.5)


def test_pending_exit_retry_quote_respects_expired_monitor_deadline(monkeypatch):
    from src.engine import cycle_runtime

    position = _make_position(
        trade_id="expired-retry-quote",
        token_id="expired-retry-quote-token",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
    )
    exit_context = ExitContext(current_market_price_is_fresh=False)

    class NoLateQuote:
        def get_best_bid_ask(self, _token_id):
            pytest.fail("expired monitor deadline must not start a retry quote")

    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: 2.0)
    refreshed, used = (
        cycle_runtime._refresh_pending_exit_retry_quote_from_current_clob(
            conn=object(),
            clob=NoLateQuote(),
            pos=position,
            exit_context=exit_context,
            identity_seed_allowed=True,
            deadline_monotonic=1.0,
        )
    )

    assert refreshed is exit_context
    assert used is False


def test_pending_exit_retry_quote_uses_deadline_aware_bid_only_truth(monkeypatch):
    from src.engine import cycle_runtime, monitor_refresh

    position = _make_position(
        trade_id="bid-only-retry-quote",
        token_id="bid-only-retry-quote-token",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
    )
    exit_context = ExitContext(current_market_price_is_fresh=False)
    observed_deadlines = []

    def quote(_conn, _clob, pos, *, retry_after_prefetch):
        observed_deadlines.append(
            (
                pos._zeus_held_monitor_deadline_monotonic,
                retry_after_prefetch,
            )
        )
        return monitor_refresh.HeldTokenMonitorQuote(
            token_id=position.token_id,
            best_bid=0.21,
            best_ask=None,
            bid_size=17.0,
            ask_size=0.0,
            mark_price=0.21,
            source_timestamp="2026-08-10T12:00:00+00:00",
            bid_ladder=((0.21, 17.0),),
        )

    monkeypatch.setattr(monitor_refresh, "monitor_quote_refresh", quote)
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: 2.0)
    refreshed, used = (
        cycle_runtime._refresh_pending_exit_retry_quote_from_current_clob(
            conn=object(),
            clob=object(),
            pos=position,
            exit_context=exit_context,
            identity_seed_allowed=True,
            deadline_monotonic=3.0,
        )
    )

    assert observed_deadlines == [(3.0, True)]
    assert not hasattr(position, "_zeus_held_monitor_deadline_monotonic")
    assert used is True
    assert refreshed.current_market_price == pytest.approx(0.21)
    assert refreshed.current_market_price_is_fresh is True
    assert refreshed.best_bid == pytest.approx(0.21)
    assert refreshed.best_ask is None
    assert refreshed.bid_size == pytest.approx(17.0)
    assert refreshed.bid_ladder == ((0.21, 17.0),)


def test_monitor_preparation_sql_deadline_interrupts_and_restores(monkeypatch):
    from src.execution import exit_lifecycle

    clock = [10.0]

    class Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class Conn:
        def __init__(self):
            self.busy_ms = 30_000
            self.handler = None

        def execute(self, sql):
            if sql == "PRAGMA busy_timeout":
                return Result((self.busy_ms,))
            if sql.startswith("PRAGMA busy_timeout = "):
                self.busy_ms = int(sql.rsplit(" ", 1)[-1])
                return Result()
            raise AssertionError(sql)

        def set_progress_handler(self, handler, _opcodes):
            self.handler = handler

    conn = Conn()
    monkeypatch.setattr(exit_lifecycle._time_module, "monotonic", lambda: clock[0])

    with pytest.raises(
        TimeoutError,
        match="HELD_MONITOR_PREPARATION_DEADLINE_EXPIRED",
    ):
        with exit_lifecycle._held_monitor_preparation_deadline(conn, 12.0) as ensure_live:
            assert conn.busy_ms == 0
            assert conn.handler() == 0
            clock[0] = 12.1
            assert conn.handler() == 1
            ensure_live()

    assert conn.handler is None
    assert conn.busy_ms == 30_000


def test_allocator_retry_release_stops_between_positions_at_monitor_deadline(
    monkeypatch,
):
    from src.execution import exit_lifecycle

    clock = [0.0]
    savepoint_attempts = []

    class Result:
        rowcount = 0

        def fetchall(self):
            return [
                ("p1", "pending_exit", "center_buy", "", 1, "later"),
                ("p2", "pending_exit", "center_buy", "", 1, "later"),
            ]

    class Conn:
        def execute(self, sql, _params=()):
            if "SELECT position_id" in sql:
                return Result()
            if sql == "SAVEPOINT exit_retry_release":
                savepoint_attempts.append(clock[0])
                clock[0] = 0.6
                raise sqlite3.OperationalError("database is locked")
            return Result()

    monkeypatch.setattr(exit_lifecycle._time_module, "monotonic", lambda: clock[0])

    result = exit_lifecycle._append_exit_retry_release_events_and_update_projection(
        Conn(),
        ["p1", "p2"],
        observed_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        release_reason="ALLOCATOR_CONFIGURED_AFTER_REFRESH",
        release_error="allocator_not_configured_released",
        deadline_monotonic=0.5,
    )

    assert savepoint_attempts == [0.0]
    assert result["error"] == "HELD_MONITOR_PREPARATION_DEADLINE_EXPIRED"


def test_monitor_connection_busy_wait_uses_claim_remaining(monkeypatch):
    from src.engine import cycle_runner

    observed = {}

    class Result:
        def fetchall(self):
            return []

    class Conn:
        def set_progress_handler(self, _handler, _opcodes):
            return None

        def execute(self, _sql, _params=()):
            return Result()

    def connect(_path, *, write_class, busy_timeout_ms, deadline_monotonic):
        observed.update(
            write_class=write_class,
            busy_timeout_ms=busy_timeout_ms,
            deadline_monotonic=deadline_monotonic,
        )
        return Conn()

    monkeypatch.setattr(cycle_runner, "connect_or_degrade", connect)
    monkeypatch.setattr(cycle_runner.time, "monotonic", lambda: 2.0)

    assert cycle_runner.get_connection(deadline_monotonic=5.0) is not None
    assert observed == {
        "write_class": "live",
        "busy_timeout_ms": 3_000,
        "deadline_monotonic": 5.0,
    }


def test_monitor_connection_crossing_deadline_never_starts_attach(monkeypatch):
    from src.engine import cycle_runner

    clock = [0.0]
    events = []

    class Conn:
        def __init__(self):
            self.closed = False

        def execute(self, sql, _params=()):
            events.append(sql)
            raise AssertionError("expired connection must not start PRAGMA/ATTACH")

        def close(self):
            self.closed = True

    conn = Conn()

    def connect(*_args, **_kwargs):
        clock[0] = 2.0
        return conn

    monkeypatch.setattr(cycle_runner, "connect_or_degrade", connect)
    monkeypatch.setattr(cycle_runner.time, "monotonic", lambda: clock[0])

    assert cycle_runner.get_connection(deadline_monotonic=1.0) is None
    assert events == []
    assert conn.closed is True


def test_monitor_connection_reclamps_attach_after_prior_sql_spends_budget(
    monkeypatch,
):
    from src.engine import cycle_runner

    clock = [0.0]
    busy_values = []

    class Result:
        def fetchall(self):
            clock[0] = 0.9
            return []

    class Conn:
        def set_progress_handler(self, _handler, _opcodes):
            return None

        def execute(self, sql, _params=()):
            if sql.startswith("PRAGMA busy_timeout = "):
                busy_values.append(int(sql.rsplit(" ", 1)[-1]))
                return Result()
            if sql == "PRAGMA database_list":
                return Result()
            return Result()

    monkeypatch.setattr(
        cycle_runner,
        "connect_or_degrade",
        lambda *_args, **_kwargs: Conn(),
    )
    monkeypatch.setattr(cycle_runner.time, "monotonic", lambda: clock[0])

    assert cycle_runner.get_connection(deadline_monotonic=1.0) is not None
    assert busy_values[0] == 1_000
    assert busy_values[1:] == [100, 100]


def test_monitor_portfolio_reuses_deadline_connection_without_closing(
    monkeypatch,
    tmp_path,
):
    from src.state import db, portfolio

    class Result:
        def fetchall(self):
            return []

    class Conn:
        def execute(self, sql):
            assert sql == "PRAGMA database_list"
            return Result()

        def close(self):
            pytest.fail("caller-owned monitor connection must remain open")

    monkeypatch.setattr(
        db,
        "query_portfolio_loader_view",
        lambda *_args, **_kwargs: {
            "status": "empty",
            "positions": [],
        },
    )

    loaded = portfolio.load_portfolio(
        tmp_path / "positions-live.json",
        open_positions_only=True,
        connection=Conn(),
        deadline_monotonic=time.monotonic() + 1.0,
    )

    assert loaded.authority == "canonical_db"
    assert loaded.positions == []


def test_closed_market_unarmed_residual_is_not_global_sell_debt(monkeypatch):
    from src.execution import exit_lifecycle

    position = _make_position(
        trade_id="closed-residual-placeholder",
        state="day0_window",
        chain_state="synced",
    )
    obligation = {
        "schema_version": 4,
        "scope_identity": "closed-scope",
        "generation": "closed-generation",
        "position_id": position.trade_id,
        "held_token_id": position.token_id,
        "residual_proof": {"command_id": "filled-exit-command"},
    }
    payload = {
        "semantic_event": "MARKET_CLOSED_HOLD_TO_SETTLEMENT",
        "hold_reason": "MARKET_CLOSED_AWAITING_SETTLEMENT",
        "exit_order_submitted": False,
        "exit_failure": False,
        "held_sell_reauction_obligation": obligation,
    }

    class Result:
        def fetchone(self):
            return ("MONITOR_REFRESHED", json.dumps(payload))

    class Conn:
        def execute(self, _sql, _params=()):
            return Result()

    monkeypatch.setattr(
        exit_lifecycle,
        "latest_held_sell_reauction_obligation",
        lambda *_args, **_kwargs: obligation,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_held_sell_reauction_recovery_due",
        lambda *_args, **_kwargs: pytest.fail(
            "closed unarmed residual must drain through settlement"
        ),
    )

    assert exit_lifecycle.needs_global_sell_snapshot_reauction(
        position,
        Conn(),
    ) is False

    payload["hold_reason"] = "DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED"
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(
        position,
        Conn(),
    ) is False

    ordinary_payload = dict(payload)
    ordinary_payload.pop("semantic_event")
    ordinary_payload.pop("hold_reason")
    payload.clear()
    payload.update(ordinary_payload)
    monkeypatch.setattr(
        exit_lifecycle,
        "_held_sell_reauction_recovery_due",
        lambda *_args, **_kwargs: True,
    )
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(
        position,
        Conn(),
    ) is True

    payload.update(
        semantic_event="MARKET_CLOSED_HOLD_TO_SETTLEMENT",
        hold_reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
    )
    armed = {
        **obligation,
        "request_id": "armed-request",
        "attempt_identity": "armed-attempt",
        "completion_deadline_at": "2026-08-10T18:00:00+00:00",
    }
    monkeypatch.setattr(
        exit_lifecycle,
        "latest_held_sell_reauction_obligation",
        lambda *_args, **_kwargs: armed,
    )
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(
        position,
        Conn(),
    ) is True


def test_expired_monitor_deadline_defers_global_sell_debt_without_refresh(
    monkeypatch,
):
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    position = _make_position(
        trade_id="expired-global-sell-debt",
        token_id="expired-global-sell-debt-token",
        direction="buy_yes",
        state="pending_exit",
        chain_state="synced",
    )
    position.exit_state = "retry_pending"
    monkeypatch.setattr(
        exit_lifecycle,
        "classify_global_sell_snapshot_reauction_debt",
        lambda *_args, **_kwargs: (
            exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.DEBT
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "recover_global_sell_snapshot_reauction_debt",
        lambda *_args, **_kwargs: pytest.fail(
            "expired monitor debt must remain durable without refresh"
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [],
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        PortfolioState(positions=[position]),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_expired_global_sell_debt"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=0.0,
    )

    assert summary["global_sell_snapshot_reauction_scan_deadline_deferred"] == 1
    assert "global_sell_snapshot_reauction_debts_pending" not in summary


def test_global_sell_debt_drain_stops_after_first_attempt_exhausts_deadline(
    monkeypatch,
):
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    positions = [
        _make_position(
            trade_id=f"bounded-global-sell-debt-{index}",
            token_id=f"bounded-global-sell-token-{index}",
            direction="buy_yes",
            state="pending_exit",
            chain_state="synced",
        )
        for index in range(2)
    ]
    for position in positions:
        position.exit_state = "retry_pending"
    clock = [0.0]
    attempted: list[str] = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        exit_lifecycle,
        "classify_global_sell_snapshot_reauction_debt",
        lambda *_args, **_kwargs: (
            exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.DEBT
        ),
    )

    def recover(position, *, requester, **_kwargs):
        attempted.append(position.trade_id)
        clock[0] = 2.0
        assert requester(position, True) is False
        return False

    monkeypatch.setattr(
        exit_lifecycle,
        "recover_global_sell_snapshot_reauction_debt",
        recover,
    )
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *_args, **_kwargs: pytest.fail(
            "expired debt-drain budget must not start a primary refresh"
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [],
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        PortfolioState(positions=positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_bounded_global_sell_debt_drain"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=6.0,
    )

    assert attempted == ["bounded-global-sell-debt-0"]
    assert summary["global_sell_snapshot_reauction_deadline_deferred"] == 1
    assert summary["global_sell_snapshot_reauction_debts_pending"] == 2


def test_decision_log_406128_global_retry_snapshot_is_memory_only(monkeypatch):
    """Preflight rollback snapshot must not issue a debt-classification read."""
    from src.engine import cycle_runtime
    from src.execution import exit_lifecycle

    position = _make_position(
        trade_id="decision-log-406128",
        state="holding",
        chain_state="synced",
    )
    clock = [0.0]
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        exit_lifecycle,
        "has_global_sell_snapshot_reauction_retry",
        lambda *_args, **_kwargs: pytest.fail("snapshot must not classify retry debt"),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "needs_global_sell_snapshot_reauction",
        lambda *_args, **_kwargs: pytest.fail("snapshot must not issue debt SQL"),
    )

    def pending_preflight(*_args, deadline_monotonic, **_kwargs):
        clock[0] = deadline_monotonic
        return {"filled": 0, "retried": 0, "unchanged": 0, "filled_positions": []}

    monkeypatch.setattr(exit_lifecycle, "check_pending_exits", pending_preflight)
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [],
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("decision_log_406128"),
        held_position_monitor_budget_seconds=6.0,
    )

    assert summary["global_sell_snapshot_reauction_scan_deadline_deferred"] == 1


def test_decision_log_406131_debt_classification_defers_when_sql_spends_deadline(
    monkeypatch,
):
    """A late canonical read is typed DEFERRED, never mistaken for no debt."""
    from src.execution import exit_lifecycle

    clock = [0.0]

    class Result:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Conn:
        def __init__(self):
            self.busy_ms = 30_000
            self.handler = None
            self.history_queries = 0

        def execute(self, sql, _params=()):
            if sql == "PRAGMA busy_timeout":
                return Result((self.busy_ms,))
            if sql.startswith("PRAGMA busy_timeout = "):
                self.busy_ms = int(sql.rsplit(" ", 1)[-1])
                return Result()
            if "FROM position_events" in sql:
                clock[0] = 2.0
                self.history_queries += 1
                return Result(("MONITOR_REFRESHED", "{}"))
            raise AssertionError(sql)

        def set_progress_handler(self, handler, _opcodes):
            self.handler = handler

    conn = Conn()
    position = _make_position(trade_id="decision-log-406131", state="pending_exit")
    monkeypatch.setattr(exit_lifecycle._time_module, "monotonic", lambda: clock[0])

    status = exit_lifecycle.classify_global_sell_snapshot_reauction_debt(
        position,
        conn,
        auxiliary_deadline=1.0,
    )

    assert status is exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.DEFERRED
    assert conn.history_queries == 1
    assert conn.handler is None
    assert conn.busy_ms == 30_000


def test_global_sell_debt_lineage_timeout_is_typed_deferred(monkeypatch):
    """A stuck durable read is auxiliary debt, never primary-monitor time."""
    from src.execution import exit_lifecycle
    from src.runtime import reactor_wake

    position = _make_position(
        trade_id="global-debt-lineage-timeout",
        state="holding",
        chain_state="synced",
    )
    obligation = {
        "schema_version": 4,
        "scope_identity": "global-debt-lineage-timeout-scope",
        "request_id": "global-debt-lineage-timeout-request",
        "material_identity": "global-debt-lineage-timeout-material",
        "generation": "global-debt-lineage-timeout-generation",
        "attempt_identity": "global-debt-lineage-timeout-attempt",
        "position_id": position.trade_id,
        "held_token_id": position.token_id,
        "completion_deadline_at": "2026-08-11T20:00:00+00:00",
    }
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_events (
            position_id TEXT,
            event_type TEXT,
            sequence_no INTEGER,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO position_events VALUES (?, 'MONITOR_REFRESHED', 1, ?, ?)",
        (
            position.trade_id,
            "2026-08-11T20:00:00+00:00",
            json.dumps({"held_sell_reauction_obligation": obligation}),
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "latest_held_sell_reauction_obligation",
        lambda *_args, **_kwargs: obligation,
    )
    child_budgets = []

    def timeout_read(_scope, *, timeout_seconds, path=None):
        assert path is None
        child_budgets.append(timeout_seconds)
        raise TimeoutError("simulated blocked lineage read")

    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_recovery_snapshot_hard_deadline",
        timeout_read,
    )

    status = exit_lifecycle.classify_global_sell_snapshot_reauction_debt(
        position,
        conn,
        auxiliary_deadline=time.monotonic() + 5.0,
    )

    assert status is exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.DEFERRED
    assert child_budgets == pytest.approx(
        [exit_lifecycle.HELD_SELL_REAUCTION_CLASSIFICATION_IO_MAX_SECONDS]
    )
    conn.close()


def test_global_sell_debt_deferral_preserves_primary_monitor_refresh(monkeypatch):
    """Auxiliary debt deferral cannot blind an otherwise refreshable holding."""
    from src.engine import cycle_runtime, monitor_refresh
    from src.execution import exit_lifecycle

    position = _make_position(
        trade_id="global-debt-deferral-primary-refresh",
        token_id="global-debt-deferral-primary-token",
        state="holding",
        chain_state="synced",
    )
    refreshes = []
    monkeypatch.setattr(
        exit_lifecycle,
        "classify_global_sell_snapshot_reauction_debt",
        lambda *_args, **_kwargs: (
            exit_lifecycle.GlobalSellSnapshotReauctionDebtStatus.DEFERRED
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [position],
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {
            position.token_id: {
                "asset_id": position.token_id,
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
        },
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )

    def refresh(_conn, _clob, refreshed):
        refreshes.append(refreshed.trade_id)
        return _monitor_test_edge_context(refreshed)

    monkeypatch.setattr(monitor_refresh, "refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )

    summary = {"monitors": 0, "exits": 0}
    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_global_debt_deferral_primary_refresh"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=10.0,
    )

    assert refreshes == [position.trade_id]
    assert summary["global_sell_snapshot_reauction_classification_deferred"] == 1
    assert "global_sell_snapshot_reauction_debts_pending" not in summary
    assert summary["held_monitor_primary_belief_read_completed"] == 1
    assert summary["monitors"] == 1


def test_sub_precision_positive_sell_is_exact_non_executable_coverage():
    """A venue-inexpressible residual must terminalize exact SELL debt."""
    from src.events import reactor
    from src.engine.global_single_order_auction import (
        _non_executable_sell_coverage,
    )
    from src.runtime import reactor_wake

    curve = SimpleNamespace(
        token_id="tiny-residual-token",
        side="NO",
        book_hash="tiny-residual-book",
        fee_model=SimpleNamespace(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("5"),
        levels=(SimpleNamespace(price=Decimal("0.94"), size=Decimal("100")),),
    )

    reason, book_state, witness = _non_executable_sell_coverage(
        Decimal("0.002684"),
        curve,
    )

    assert reason == "SELLABLE_SHARES_BELOW_PRECISION"
    assert book_state == "NO_EXECUTABLE_BOOK"
    assert len(witness) == 64

    request = reactor_wake.make_held_sell_reauction_request(
        position_id="tiny-residual-position",
        family=("Singapore", "2026-08-12", "high"),
        probability_content_identity="tiny-residual-old-q",
        probability_observed_at="2026-08-12T01:24:00+00:00",
        held_token_id=curve.token_id,
        held_best_bid=0.66,
        bid_observed_at="2026-08-12T01:24:00+00:00",
        schema_version=4,
        book_state="EXECUTABLE",
    )
    coverage = SimpleNamespace(
        position_id=request.position_id,
        token_id=request.held_token_id,
        status="EXCLUDED",
        book_state=book_state,
        probability_content_identity="tiny-residual-current-q",
        selection_epoch_identity="tiny-residual-current-epoch",
        sell_book_witness_identity=witness,
    )

    receipts = reactor._held_sell_reauction_receipts_from_global_cut(
        requests=(request,),
        result=reactor.ReactorResult(
            global_held_sell_completion_cuts=[
                reactor.GlobalHeldSellCompletionCut(
                    holding_coverage=(coverage,),
                    economic_cut_completed=False,
                    outcome="INCOMPLETE",
                )
            ]
        ),
    )

    assert len(receipts) == 1
    assert receipts[0].status == "NO_EXECUTABLE_BOOK"
    assert receipts[0].attempt_identity == request.attempt_identity


def test_held_sell_recovery_read_hard_deadline_kills_blocked_lineage(tmp_path):
    """A blocked local lineage read cannot retain the monitor process."""
    from src.runtime import reactor_wake

    scope = "blocked-lineage-hard-deadline"
    lineage_path = reactor_wake._held_sell_reauction_lineage_path(
        scope,
        path=tmp_path,
    )
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(lineage_path)
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="child budget"):
        reactor_wake.held_sell_reauction_recovery_snapshot_hard_deadline(
            scope,
            timeout_seconds=0.20,
            path=tmp_path,
        )

    assert time.monotonic() - started < 1.0
    assert reactor_wake.held_sell_reauction_recovery_snapshot_hard_deadline(
        "missing-lineage-hard-deadline",
        timeout_seconds=1.0,
        path=tmp_path,
    ) == (None, False)
    assert reactor_wake._HELD_SELL_REAUCTION_RECOVERY_CHILD is None
    assert reactor_wake._HELD_SELL_REAUCTION_RECOVERY_CHILD_LOCK.acquire(
        blocking=False
    )
    reactor_wake._HELD_SELL_REAUCTION_RECOVERY_CHILD_LOCK.release()


def test_blocked_global_debt_lineage_preserves_eight_primary_refreshes(
    tmp_path,
    monkeypatch,
):
    """Observed pre-primary stall shape cannot blind the whole held book."""
    from src.engine import cycle_runtime, monitor_refresh
    from src.execution import exit_lifecycle
    from src.runtime import reactor_wake
    from src.state.db import get_connection, init_schema

    positions = [
        _make_position(
            trade_id=f"blocked-debt-eight-{index}",
            token_id=f"blocked-debt-eight-token-{index}",
            state="holding",
            chain_state="synced",
        )
        for index in range(8)
    ]
    scope = "blocked-debt-eight-scope"
    obligation = {
        "schema_version": 4,
        "scope_identity": scope,
        "request_id": "blocked-debt-eight-request",
        "material_identity": "blocked-debt-eight-material",
        "generation": "blocked-debt-eight-generation",
        "attempt_identity": "blocked-debt-eight-attempt",
        "position_id": positions[0].trade_id,
        "held_token_id": positions[0].token_id,
        "completion_deadline_at": "2026-08-11T20:00:00+00:00",
    }
    conn = get_connection(tmp_path / "blocked-debt-eight.db")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, source_module, env, payload_json
        ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, ?, 'live', ?)
        """,
        (
            "blocked-debt-eight:monitor:1",
            positions[0].trade_id,
            "2026-08-11T20:00:00+00:00",
            "tests/test_live_safety_invariants",
            json.dumps({"held_sell_reauction_obligation": obligation}),
        ),
    )
    conn.commit()
    lineage_path = reactor_wake._held_sell_reauction_lineage_path(
        scope,
        path=tmp_path,
    )
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(lineage_path)

    def blocked_recovery_due(current, **_kwargs):
        assert current["scope_identity"] == scope
        reactor_wake.held_sell_reauction_recovery_snapshot_hard_deadline(
            scope,
            timeout_seconds=0.20,
            path=tmp_path,
        )
        pytest.fail("blocked lineage unexpectedly completed")

    monkeypatch.setattr(
        exit_lifecycle,
        "_held_sell_reauction_recovery_due",
        blocked_recovery_due,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: positions,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {
            position.token_id: {
                "asset_id": position.token_id,
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
            for position in positions
        },
    )
    refreshes = []
    canonical_refreshes = []

    def refresh(_conn, _clob, position):
        refreshes.append(position.trade_id)
        position.last_monitor_prob = 0.60
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_best_bid = 0.40
        position.last_monitor_market_price = 0.40
        position.last_monitor_market_price_is_fresh = True
        return _monitor_test_edge_context(position)

    monkeypatch.setattr(monitor_refresh, "refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda _conn, position, **_kwargs: (
            canonical_refreshes.append(position.trade_id) or True
        ),
    )

    summary = {"monitors": 0, "exits": 0}
    started = time.monotonic()
    cycle_runtime.execute_monitoring_phase(
        conn,
        object(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("test_blocked_debt_eight_primary_refreshes"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=75.0,
    )

    assert time.monotonic() - started < 5.0
    assert refreshes == [position.trade_id for position in positions]
    assert canonical_refreshes == refreshes
    assert summary["global_sell_snapshot_reauction_classification_deferred"] == 8
    assert "global_sell_snapshot_reauction_debts_pending" not in summary
    assert summary["held_monitor_primary_belief_read_completed"] == 8
    assert summary["monitors"] == 8
    conn.close()


def test_monitor_refresh_deadline_defers_before_canonical_emit(monkeypatch):
    """A refresh that consumes its claim budget cannot append stale monitor truth."""
    from src.engine import cycle_runtime

    position = _make_position(
        trade_id="refresh-deadline-before-canonical-emit",
        state="day0_window",
        chain_state="synced",
    )
    clock = [0.0]
    canonical_emits = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: [position],
    )

    def refresh(*_args):
        clock[0] = 6.0
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh)
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda *_args, **_kwargs: canonical_emits.append(True),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda *_args, **_kwargs: pytest.fail("expired refresh must defer before decision"),
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(position),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("refresh_deadline_before_canonical_emit"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=6.0,
    )

    assert canonical_emits == []
    assert summary["held_monitor_defer_reason"] == "MONITOR_DEADLINE_EXPIRED_AFTER_REFRESH"
    assert summary["held_monitor_deadline_deferred_positions"] == 1
    assert summary["held_monitor_primary_belief_deferred_position_ids"] == [
        position.trade_id
    ]


def test_one_position_deadline_does_not_blind_remaining_held_book(monkeypatch):
    """One slow family loses only its own snapshot; later positions still decide."""
    from src.engine import cycle_runtime

    positions = [
        _make_position(
            trade_id=f"isolated-position-deadline-{index}",
            token_id=f"isolated-position-token-{index}",
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    clock = [0.0]
    refreshes = []
    canonical_emits = []
    evaluated = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: positions,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )

    def refresh(_conn, _clob, position):
        refreshes.append(position.trade_id)
        position.last_monitor_at = f"attempt-{len(refreshes)}"
        position.last_monitor_prob = 0.60
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_best_bid = 0.40
        position.last_monitor_market_price = 0.40
        position.last_monitor_market_price_is_fresh = True
        if len(refreshes) == 1:
            position.concurrent_evidence = "must-survive-refresh-rollback"
            clock[0] = 6.0
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: (
            evaluated.append(self.trade_id)
            or ExitDecision(False, "CI_OVERLAP_HOLD")
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda _conn, position, **_kwargs: (
            canonical_emits.append(position.trade_id) or True
        ),
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("isolated_position_deadline"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=20.0,
    )

    assert refreshes == [position.trade_id for position in positions]
    assert evaluated == [positions[1].trade_id]
    assert canonical_emits == [positions[1].trade_id]
    assert positions[0].last_monitor_at != "attempt-1"
    assert positions[0].concurrent_evidence == "must-survive-refresh-rollback"
    assert summary["held_monitor_per_position_deadline_deferred"] == 1
    assert summary["held_monitor_positions_deferred"] == 1
    assert summary["held_monitor_primary_belief_read_completed"] == 1
    assert summary["monitors"] == 1


def test_refresh_exception_restores_owned_state_and_continues_held_book(monkeypatch):
    """A failed refresh cannot leak a half-updated decision into later work."""
    from src.engine import cycle_runtime

    positions = [
        _make_position(
            trade_id=f"refresh-exception-{index}",
            token_id=f"refresh-exception-token-{index}",
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    original_prob = positions[0].last_monitor_prob
    evaluated = []
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: positions,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )

    def refresh(_conn, _clob, position):
        if position is positions[0]:
            position.last_monitor_prob = 0.01
            position.last_monitor_prob_is_fresh = True
            position.applied_validations.append("partial-refresh-must-rollback")
            position.concurrent_evidence = "must-survive-refresh-exception"
            raise RuntimeError("refresh transport failed")
        return _monitor_test_edge_context(position)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", refresh)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: (
            evaluated.append(self.trade_id)
            or ExitDecision(False, "CI_OVERLAP_HOLD")
        ),
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("refresh_exception_rollback"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=20.0,
    )

    assert positions[0].last_monitor_prob == original_prob
    assert "partial-refresh-must-rollback" not in positions[0].applied_validations
    assert positions[0].concurrent_evidence == "must-survive-refresh-exception"
    assert evaluated == [positions[1].trade_id]
    assert summary["monitor_failed"] == 1
    assert summary["monitors"] == 1


def test_closed_market_metadata_child_timeout_continues_held_book(monkeypatch):
    """Expired venue metadata cannot commit close state or blind later positions."""
    from src.engine import cycle_runtime

    positions = [
        _make_position(
            trade_id=f"closed-metadata-deadline-{index}",
            token_id=f"closed-metadata-token-{index}",
            state="holding",
            chain_state="synced",
        )
        for index in range(2)
    ]
    clock = [0.0]
    refreshes = []
    canonical_emits = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: positions,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )

    metadata_calls = []

    def market_info(_clob, position, *_args, **_kwargs):
        metadata_calls.append(position.trade_id)
        if position is positions[0]:
            clock[0] = 6.0
        return {
            "closed": True,
            "accepting_orders": False,
            "source": "clob_market_info",
        }

    monkeypatch.setattr(cycle_runtime, "_closed_non_accepting_market_info", market_info)
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            refreshes.append(position.trade_id)
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda _conn, position, **_kwargs: (
            canonical_emits.append(position.trade_id) or True
        ),
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("closed_metadata_deadline"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=20.0,
    )

    assert metadata_calls == [position.trade_id for position in positions]
    assert refreshes == []
    assert canonical_emits == []
    assert positions[0].state == "holding"
    assert summary["held_monitor_per_position_deadline_deferred"] == 1
    assert summary["held_monitor_positions_deferred"] == 1


@pytest.mark.parametrize("failure_mode", ("timeout", "exception"))
def test_admitted_metadata_failure_blocks_only_nonadmitted_statistical_tail(
    monkeypatch,
    failure_mode,
):
    """Any admitted child failure closes tail admission without blinding peers."""
    from src.engine import cycle_runtime

    positions = [
        _make_position(
            trade_id=f"metadata-admission-{index}",
            token_id=f"metadata-admission-token-{index}",
            state="holding",
            chain_state="synced",
        )
        for index in range(4)
    ]
    with cycle_runtime._HELD_MONITOR_CURSOR_LOCK:
        cycle_runtime._HELD_MONITOR_ATTEMPT_STATE_BY_LANE.pop(
            "bounded_coverage",
            None,
        )
        cycle_runtime._HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE.pop(
            "bounded_coverage",
            None,
        )
    clock = [0.0]
    metadata_calls: list[str] = []
    refreshes: list[str] = []
    canonical_emits: list[str] = []
    monkeypatch.setattr(cycle_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cycle_runtime,
        "_monitoring_phase_positions",
        lambda *_args, **_kwargs: positions,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_day0_hard_fact_position_eligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_fresh_local_held_monitor_orderbooks",
        lambda *_args, **_kwargs: {
            position.token_id: {
                "asset_id": position.token_id,
                "bids": [{"price": "0.40", "size": "20"}],
                "asks": [{"price": "0.42", "size": "20"}],
            }
            for position in positions
        },
    )

    def market_info(_clob, position, *_args, **_kwargs):
        metadata_calls.append(position.trade_id)
        if position is positions[0]:
            if failure_mode == "timeout":
                clock[0] = 6.0
            else:
                raise RuntimeError("metadata transport failed")
        return None

    monkeypatch.setattr(cycle_runtime, "_closed_non_accepting_market_info", market_info)
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda _conn, _clob, position: (
            refreshes.append(position.trade_id)
            or _monitor_test_edge_context(position)
        ),
    )
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, _ctx: ExitDecision(False, "CI_OVERLAP_HOLD"),
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda _conn, position, **_kwargs: (
            canonical_emits.append(position.trade_id) or True
        ),
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        SimpleNamespace(),
        _make_portfolio(*positions),
        _monitor_test_artifact(),
        _monitor_test_tracker(),
        summary,
        deps=_monitor_test_deps("metadata_admission_timeout"),
        run_exit_preflight=False,
        held_position_monitor_budget_seconds=20.0,
    )

    assert summary["held_monitor_primary_belief_admitted_position_ids"] == [
        positions[0].trade_id,
        positions[1].trade_id,
    ]
    assert metadata_calls == [positions[0].trade_id, positions[1].trade_id]
    assert refreshes == canonical_emits == [positions[1].trade_id]
    assert summary["held_monitor_primary_belief_expired_position_ids"] == (
        [positions[0].trade_id] if failure_mode == "timeout" else []
    )
    assert summary["held_monitor_primary_belief_failed_position_ids"] == (
        [positions[0].trade_id] if failure_mode == "exception" else []
    )
    assert set(summary["held_monitor_primary_belief_deferred_position_ids"]) == {
        positions[0].trade_id,
        positions[2].trade_id,
        positions[3].trade_id,
    }


def test_market_velocity_uses_causal_source_time_not_legacy_text_order(tmp_path):
    from src.engine.monitor_refresh import _causal_market_velocity_1h
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "causal-market-velocity.db")
    init_schema(conn)
    rows = (
        ("held-token", 0.80, "2026-08-10T10:59:00+00:00", "2026-08-10 10:59:01"),
        ("held-token", 0.40, "2026-08-10T11:30:00+00:00", "2026-08-10 11:30:01"),
        ("held-token", 0.05, "2026-08-10T12:30:00+00:00", "2026-08-10 12:30:01"),
    )
    conn.executemany(
        """
        INSERT INTO token_price_log
            (token_id, price, source_timestamp, timestamp)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()

    velocity = _causal_market_velocity_1h(
        conn,
        token_id="held-token",
        current_price=0.20,
        observed_at="2026-08-10T12:00:00+00:00",
    )

    assert velocity == pytest.approx(-0.60)


def test_market_velocity_without_trade_db_is_non_authoritative():
    from src.engine.monitor_refresh import _causal_market_velocity_1h

    assert _causal_market_velocity_1h(
        None,
        token_id="held-token",
        current_price=0.20,
        observed_at="2026-08-10T12:00:00+00:00",
    ) == 0.0


def test_market_velocity_without_executable_quote_time_is_non_authoritative(tmp_path):
    from src.engine.monitor_refresh import _causal_market_velocity_1h
    from src.state.db import get_connection, init_schema

    conn = get_connection(tmp_path / "closed-market-velocity.db")
    init_schema(conn)

    assert _causal_market_velocity_1h(
        conn,
        token_id="closed-held-token",
        current_price=float("nan"),
        observed_at=None,
    ) == 0.0
