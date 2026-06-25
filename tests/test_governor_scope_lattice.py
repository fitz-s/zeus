# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: docs/evidence/live_order_pathology/2026-06-22_governor_scope_lattice_decision.md
#                  (frontier consult REQ-20260621-211850, Pro Extended, HIGH confidence)
"""Scope-aware governor gating: a SCOPED single-market unknown side effect triggers
per-market reduce_only (existing line-186 path) but must NOT trip GLOBAL reduce_only.

GLOBAL reduce_only is reserved for SYSTEMIC signals:
  - reconcile findings (unchanged),
  - unscopeable unknowns (fail closed: cannot bound the blast radius -> global),
  - >= SYSTEMIC_MARKET_COUNT_LIMIT distinct independent markets each carrying an unknown.

These tests are the RED-first contract for the scope lattice. They exercise both the
pure-unit gating predicate (GovernorState + reduce_only_mode_active / can_allocate)
and the DB-backed scope classifier (count_unknown_side_effects / classify scope).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.contracts import Direction, ExecutionIntent, DecisionSourceContext
from src.contracts.slippage_bps import SlippageBps
from src.control.heartbeat_supervisor import HeartbeatHealth
from src.risk_allocator.governor import (
    AllocationDecision,
    CapPolicy,
    GovernorState,
    RiskAllocator,
    classify_unknown_side_effect_scope,
    count_unknown_side_effects,
)
from src.riskguard.risk_level import RiskLevel

NOW = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Pure-unit fixtures (no DB) — exercise the gating predicate directly.
# ---------------------------------------------------------------------------
def _intent(*, market: str = "m1", size: float = 10.0, token: str = "t1", event: str | None = None) -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction.YES,
        target_size_usd=size,
        limit_price=0.5,
        toxicity_budget=0.01,
        max_slippage=SlippageBps(value_bps=100.0, direction="adverse"),
        is_sandbox=True,
        market_id=market,
        token_id=token,
        timeout_seconds=10,
        executable_snapshot_id="snap-1",
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        event_id=event or market,
        resolution_window="day0",
        correlation_key=None,
        decision_source_context=DecisionSourceContext(
            source_id="tigge",
            model_family="ecmwf_ifs025",
            forecast_issue_time="2026-06-22T00:00:00+00:00",
            forecast_valid_time="2026-06-22T06:00:00+00:00",
            forecast_fetch_time="2026-06-22T01:00:00+00:00",
            forecast_available_at="2026-06-22T00:30:00+00:00",
            raw_payload_hash="a" * 64,
            degradation_level="OK",
            forecast_source_role="entry_primary",
            authority_tier="FORECAST",
            decision_time="2026-06-22T02:00:00+00:00",
            decision_time_status="OK",
        ),
    )


def _state(**kwargs) -> GovernorState:
    base = dict(
        current_drawdown_pct=0.0,
        heartbeat_health=HeartbeatHealth.HEALTHY,
        ws_gap_active=False,
        ws_gap_seconds=0,
        unknown_side_effect_count=0,
        reconcile_finding_count=0,
        risk_level=RiskLevel.GREEN,
    )
    base.update(kwargs)
    return GovernorState(**base)


# ---------------------------------------------------------------------------
# Case 1: single scoped single-market unknown -> per-market block, NOT global.
# ---------------------------------------------------------------------------
def test_single_scoped_market_unknown_sets_market_reduce_only_not_global():
    allocator = RiskAllocator(CapPolicy(max_per_market_micro=500_000_000))
    # One scoped unknown on market "2615258"; classified scoped (markets present,
    # zero systemic). The affected market is listed for the line-186 isolation path.
    state = _state(
        unknown_side_effect_count=1,
        unknown_side_effect_markets=("2615258",),
        systemic_unknown_side_effect_count=0,
    )

    # Global latch must NOT be active.
    assert allocator.reduce_only_mode_active(state) is False

    # The affected market itself is blocked (per-market isolation).
    blocked = allocator.can_allocate(_intent(market="2615258", size=10), state)
    assert blocked.allowed is False
    assert blocked.reason == "unknown_side_effect_same_market"

    # An UNRELATED healthy market still admits new entries.
    admitted = allocator.can_allocate(_intent(market="999", size=10), state)
    assert admitted.allowed is True
    assert admitted.reason == "allowed"


# ---------------------------------------------------------------------------
# Case 2: unscopeable unknown (empty/ambiguous market) -> global (fail closed).
# ---------------------------------------------------------------------------
def test_unscopeable_unknown_fails_closed_to_global():
    allocator = RiskAllocator(CapPolicy(max_per_market_micro=500_000_000))
    # An unknown that could not be scoped to a single market: no scoped markets,
    # systemic count > 0 (unscopeable -> systemic).
    state = _state(
        unknown_side_effect_count=1,
        unknown_side_effect_markets=(),
        systemic_unknown_side_effect_count=1,
    )

    assert allocator.reduce_only_mode_active(state) is True
    decision = allocator.can_allocate(_intent(market="anything", size=10), state)
    assert decision.allowed is False
    assert decision.reason == "reduce_only_mode_active"


def test_bare_unknown_count_without_scope_classification_fails_closed_to_global():
    """Backward-compat + fail-closed: an unknown count with NO scope evidence
    (no scoped markets, no explicit systemic count) must be treated as systemic."""
    allocator = RiskAllocator(CapPolicy(max_per_market_micro=500_000_000))
    state = _state(unknown_side_effect_count=1)  # additive scope fields at defaults

    assert allocator.reduce_only_mode_active(state) is True
    decision = allocator.can_allocate(_intent(market="m1", size=10), state)
    assert decision.allowed is False
    assert decision.reason == "reduce_only_mode_active"


# ---------------------------------------------------------------------------
# Case 3: >= SYSTEMIC_MARKET_COUNT_LIMIT distinct markets -> global.
# ---------------------------------------------------------------------------
def test_two_distinct_market_unknowns_escalate_to_global():
    allocator = RiskAllocator(CapPolicy(max_per_market_micro=500_000_000))
    # Two independent markets each carry an unknown -> systemic escalation.
    state = _state(
        unknown_side_effect_count=2,
        unknown_side_effect_markets=("mA", "mB"),
        systemic_unknown_side_effect_count=2,
    )

    assert allocator.reduce_only_mode_active(state) is True
    decision = allocator.can_allocate(_intent(market="mC", size=10), state)
    assert decision.allowed is False
    assert decision.reason == "reduce_only_mode_active"


# ---------------------------------------------------------------------------
# Case 4: reconcile findings -> global (UNCHANGED behavior).
# ---------------------------------------------------------------------------
def test_reconcile_finding_still_trips_global():
    allocator = RiskAllocator(CapPolicy(max_per_market_micro=500_000_000))
    state = _state(reconcile_finding_count=1)

    assert allocator.reduce_only_mode_active(state) is True
    decision = allocator.can_allocate(_intent(market="m1", size=10), state)
    assert decision.allowed is False
    assert decision.reason == "reduce_only_mode_active"


# ---------------------------------------------------------------------------
# Case 5: the affected scoped market still rejects new entries (line-186 intact).
# ---------------------------------------------------------------------------
def test_scoped_market_itself_rejects_new_entries():
    allocator = RiskAllocator(CapPolicy(max_per_market_micro=500_000_000))
    state = _state(
        unknown_side_effect_count=1,
        unknown_side_effect_markets=("2615258",),
        systemic_unknown_side_effect_count=0,
    )

    decision = allocator.can_allocate(_intent(market="2615258", size=10), state)
    assert decision.allowed is False
    assert decision.reason == "unknown_side_effect_same_market"


# ---------------------------------------------------------------------------
# DB-backed scope classification.
# ---------------------------------------------------------------------------
@pytest.fixture
def conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    return c


def _insert_review_required(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    market_id: str,
    token_id: str = "tok",
    venue_order_id: str | None = "voi",
    updated_at: str | None = None,
) -> None:
    """Insert a REVIEW_REQUIRED venue_command that carries submit-side-effect risk
    (a venue_order_id makes _review_required_carries_submit_side_effect_risk True)."""
    ts = updated_at or NOW.isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands
          (command_id, snapshot_id, envelope_id, position_id, decision_id,
           idempotency_key, intent_kind, market_id, token_id, side, size, price,
           venue_order_id, state, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            command_id,
            f"snap-{command_id}",
            f"env-{command_id}",
            f"pos-{command_id}",
            f"dec-{command_id}",
            (command_id * 32)[:32],
            "ENTRY",
            market_id,
            token_id,
            "BUY",
            10.0,
            0.5,
            venue_order_id,
            "REVIEW_REQUIRED",
            ts,
            ts,
        ),
    )
    conn.commit()


def test_classify_single_scoped_market(conn):
    _insert_review_required(conn, command_id="c1", market_id="2615258")

    scope = classify_unknown_side_effect_scope(conn, CapPolicy())
    assert scope.total_count == 1
    assert scope.scoped_markets == ("2615258",)
    assert scope.unscopeable_count == 0
    assert scope.systemic_count == 0  # one scoped market, below systemic limit
    assert scope.is_systemic is False

    # Legacy tuple signature is preserved.
    count, markets = count_unknown_side_effects(conn)
    assert count == 1
    assert markets == ("2615258",)


def test_classify_unscopeable_empty_market_is_systemic(conn):
    # Empty market_id row carrying side-effect risk -> in count, NOT in markets.
    _insert_review_required(conn, command_id="c1", market_id="")

    scope = classify_unknown_side_effect_scope(conn, CapPolicy())
    assert scope.total_count == 1
    assert scope.scoped_markets == ()
    assert scope.unscopeable_count == 1
    assert scope.systemic_count >= 1
    assert scope.is_systemic is True


def test_classify_two_distinct_markets_is_systemic(conn):
    _insert_review_required(conn, command_id="c1", market_id="mA", updated_at=NOW.isoformat())
    _insert_review_required(conn, command_id="c2", market_id="mB", updated_at=(NOW + timedelta(seconds=1)).isoformat())

    scope = classify_unknown_side_effect_scope(conn, CapPolicy(systemic_market_count_limit=2))
    assert scope.total_count == 2
    assert set(scope.scoped_markets) == {"mA", "mB"}
    assert scope.unscopeable_count == 0
    assert scope.is_systemic is True


def test_classify_two_unknowns_same_market_is_not_systemic(conn):
    _insert_review_required(conn, command_id="c1", market_id="mA", updated_at=NOW.isoformat())
    _insert_review_required(conn, command_id="c2", market_id="mA", updated_at=(NOW + timedelta(seconds=1)).isoformat())

    scope = classify_unknown_side_effect_scope(conn, CapPolicy(systemic_market_count_limit=2))
    assert scope.total_count == 2
    assert scope.scoped_markets == ("mA",)
    assert scope.unscopeable_count == 0
    # Two unknowns, but ONE market -> below the distinct-market systemic limit.
    assert scope.is_systemic is False


def test_classify_no_unknowns(conn):
    scope = classify_unknown_side_effect_scope(conn, CapPolicy())
    assert scope.total_count == 0
    assert scope.scoped_markets == ()
    assert scope.unscopeable_count == 0
    assert scope.is_systemic is False


# ---------------------------------------------------------------------------
# Live-wiring end-to-end: refresh_global_allocator must publish a SCOPED state
# (not global) for the real production instance (single market 2615258).
# ---------------------------------------------------------------------------
def test_refresh_global_allocator_scoped_market_does_not_freeze_book(conn, monkeypatch):
    from src.risk_allocator.governor import (
        clear_global_allocator,
        configure_global_allocator,
        refresh_global_allocator,
        summary as governor_summary,
    )

    # The exact live instance: one scoped REVIEW_REQUIRED command on market 2615258
    # carrying a venue_order_id (submit-side-effect risk).
    _insert_review_required(conn, command_id="7e07c586", market_id="2615258", venue_order_id="voi-hk")

    snap = refresh_global_allocator(
        conn,
        ledger={"current_drawdown_pct": 0.0, "risk_level": "GREEN"},
        heartbeat={"health": "HEALTHY"},
        ws_status={"m5_reconcile_required": False},
        cap_policy=CapPolicy(),
    )
    try:
        state = snap["state"]
        # Scoped: the market is listed for isolation, but the global reduce_only
        # latch (entry allow_submit) is NOT tripped by the single scoped unknown.
        assert state["unknown_side_effect_count"] == 1
        assert state["unknown_side_effect_markets"] == ["2615258"]
        assert state["systemic_unknown_side_effect_count"] == 0
        assert snap["kill_switch_reason"] is None
        assert snap["reduce_only"] is False
        assert snap["entry"]["allow_submit"] is True

        # The affected market is still blocked; an unrelated market admits.
        from src.risk_allocator.governor import assert_global_allocation_allows, AllocationDenied

        with pytest.raises(AllocationDenied) as blocked:
            assert_global_allocation_allows(_intent(market="2615258", size=10))
        assert blocked.value.decision.reason == "unknown_side_effect_same_market"

        admitted = assert_global_allocation_allows(_intent(market="999", size=10))
        assert admitted.allowed is True
    finally:
        clear_global_allocator()


def test_refresh_global_allocator_unscopeable_freezes_book(conn):
    from src.risk_allocator.governor import (
        clear_global_allocator,
        refresh_global_allocator,
    )

    # An unscopeable unknown (blank market_id) carrying side-effect risk.
    _insert_review_required(conn, command_id="c-empty", market_id="", venue_order_id="voi-x")

    snap = refresh_global_allocator(
        conn,
        ledger={"current_drawdown_pct": 0.0, "risk_level": "GREEN"},
        heartbeat={"health": "HEALTHY"},
        ws_status={"m5_reconcile_required": False},
        cap_policy=CapPolicy(),
    )
    try:
        state = snap["state"]
        assert state["unknown_side_effect_count"] == 1
        assert state["systemic_unknown_side_effect_count"] >= 1
        # Fail closed: the book is frozen (reduce_only) for entries.
        assert snap["reduce_only"] is True
        assert snap["entry"]["allow_submit"] is False
    finally:
        clear_global_allocator()
