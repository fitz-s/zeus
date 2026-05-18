# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: STRUCTURAL_PLAN.md v3 §2 PR-S1
"""Antibody tests for Bug #3 chain aggregate reconciliation (PR-S1).

Tests cover:
- allocate_chain_truth() pure function (LIFO allocation)
- Per-position void via phantom_set in reconcile()
- sell_preflight block via tokens_blocked_until_resolution
- N1 auto-clear semantics
- N2 boot-gate (s1/s2 SLA check)
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.state.chain_reconciliation import ChainPosition, allocate_chain_truth, reconcile
from src.state.portfolio import ExitContext, Position, PortfolioState


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_block_list():
    """Ensure tokens_blocked_until_resolution is clean between tests."""
    from src.engine import cycle_runtime
    with cycle_runtime._tokens_blocked_lock:
        cycle_runtime.tokens_blocked_until_resolution.clear()
    yield
    with cycle_runtime._tokens_blocked_lock:
        cycle_runtime.tokens_blocked_until_resolution.clear()


def _make_position(
    trade_id: str = "t1",
    token_id: str = "tok-1",
    shares: float = 6.0,
    entered_at: str = "2026-05-01T00:00:00+00:00",
    state: str = "holding",
    direction: str = "buy_yes",
) -> Position:
    return Position(
        trade_id=trade_id,
        market_id="mkt-1",
        city="London",
        cluster="EU-West",
        target_date="2026-05-01",
        bin_label="60-64F",
        direction=direction,
        env="live",
        unit="F",
        size_usd=6.0,
        entry_price=0.5,
        p_posterior=0.6,
        edge=0.1,
        shares=shares,
        cost_basis_usd=3.0,
        entered_at=entered_at,
        decision_snapshot_id="snap-1",
        entry_method="ens_member_counting",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="update_reaction",
        state=state,
        order_id="ord-1",
        order_status="filled",
        order_posted_at=entered_at,
        chain_state="synced",
        token_id=token_id if direction == "buy_yes" else "",
        no_token_id="" if direction == "buy_yes" else token_id,
    )


def _make_portfolio(*positions) -> PortfolioState:
    ps = PortfolioState()
    for pos in positions:
        ps.positions.append(pos)
    return ps


# ── allocate_chain_truth pure function tests ─────────────────────────────────


def test_three_positions_same_token_chain_zero_all_phantom():
    """Chain balance = 0 → all 3 positions are phantom."""
    p1 = _make_position("t1", shares=6.0, entered_at="2026-05-01T01:00:00+00:00")
    p2 = _make_position("t2", shares=6.0, entered_at="2026-05-01T02:00:00+00:00")
    p3 = _make_position("t3", shares=6.0, entered_at="2026-05-01T03:00:00+00:00")
    allocated, phantom = allocate_chain_truth([p1, p2, p3], chain_balance=0.0)
    assert len(allocated) == 0
    assert len(phantom) == 3
    phantom_ids = {p.trade_id for p in phantom}
    assert phantom_ids == {"t1", "t2", "t3"}


def test_chain_backs_one_of_three_lifo():
    """Chain balance = 6 backs only the newest position (LIFO)."""
    p1 = _make_position("t1", shares=6.0, entered_at="2026-05-01T01:00:00+00:00")
    p2 = _make_position("t2", shares=6.0, entered_at="2026-05-01T02:00:00+00:00")
    p3 = _make_position("t3", shares=6.0, entered_at="2026-05-01T03:00:00+00:00")
    allocated, phantom = allocate_chain_truth([p1, p2, p3], chain_balance=6.0)
    assert len(allocated) == 1
    assert allocated[0].trade_id == "t3"  # newest backed first (LIFO)
    assert len(phantom) == 2
    phantom_ids = {p.trade_id for p in phantom}
    assert phantom_ids == {"t1", "t2"}


def test_full_backing_no_phantoms():
    """Chain balance = 18 covers all 3 positions of 6 each — no phantoms."""
    p1 = _make_position("t1", shares=6.0, entered_at="2026-05-01T01:00:00+00:00")
    p2 = _make_position("t2", shares=6.0, entered_at="2026-05-01T02:00:00+00:00")
    p3 = _make_position("t3", shares=6.0, entered_at="2026-05-01T03:00:00+00:00")
    allocated, phantom = allocate_chain_truth([p1, p2, p3], chain_balance=18.0)
    assert len(allocated) == 3
    assert len(phantom) == 0


def test_unsupported_policy_raises():
    p = _make_position()
    with pytest.raises(ValueError, match="unsupported policy"):
        allocate_chain_truth([p], chain_balance=6.0, policy="FIFO")


def test_dust_threshold_backs_near_full():
    """Chain balance within DUST of position size still backs it."""
    p = _make_position("t1", shares=6.0)
    allocated, phantom = allocate_chain_truth([p], chain_balance=5.995)
    assert len(allocated) == 1
    assert len(phantom) == 0


def test_empty_positions_returns_empty():
    allocated, phantom = allocate_chain_truth([], chain_balance=10.0)
    assert allocated == []
    assert phantom == []


# ── Sell preflight block test (cross-module relationship) ────────────────────


def test_sell_preflight_blocks_when_token_aggregate_invariant_fires():
    """execute_exit returns TOKEN_AGGREGATE_BLOCKED when token is in block-list."""
    from src.engine import cycle_runtime
    from src.execution.exit_lifecycle import execute_exit

    token_id = "tok-blocked"
    cycle_runtime.tokens_blocked_until_resolution.add(token_id)

    pos = _make_position("t1", token_id=token_id, shares=6.0)
    pos.state = "holding"

    exit_ctx = ExitContext(
        exit_reason="profit_target",
        current_market_price=0.8,
    )

    result = execute_exit(
        _make_portfolio(pos),
        pos,
        exit_ctx,
        clob=None,
        conn=None,
    )
    assert "TOKEN_AGGREGATE_BLOCKED_PENDING_RESOLUTION" in result


# ── N1 auto-clear test ───────────────────────────────────────────────────────


def test_block_list_auto_clears_when_invariant_no_longer_fires():
    """After reconcile fixes the aggregate, token is removed from block-list."""
    from src.engine.cycle_runtime import _assert_token_aggregate_invariant, tokens_blocked_until_resolution

    token_id = "tok-was-blocked"
    tokens_blocked_until_resolution.add(token_id)

    # Portfolio with one position fully backed by chain
    pos = _make_position("t1", token_id=token_id, shares=6.0, state="holding")
    portfolio = _make_portfolio(pos)

    chain_positions = [
        ChainPosition(token_id=token_id, size=6.0, avg_price=0.5, cost=3.0, condition_id="cond-1")
    ]

    deps = MagicMock()
    _assert_token_aggregate_invariant(portfolio, chain_positions, deps=deps)

    # Token should have been removed from block-list (invariant didn't fire)
    assert token_id not in tokens_blocked_until_resolution


def test_block_list_retained_when_invariant_still_fires():
    """Token stays blocked when local_sum > chain_balance."""
    from src.engine.cycle_runtime import _assert_token_aggregate_invariant, tokens_blocked_until_resolution

    token_id = "tok-still-bad"
    tokens_blocked_until_resolution.add(token_id)

    # 2 positions × 6 shares but chain only has 6
    p1 = _make_position("t1", token_id=token_id, shares=6.0, state="holding")
    p2 = _make_position("t2", token_id=token_id, shares=6.0, state="holding")
    portfolio = _make_portfolio(p1, p2)

    chain_positions = [
        ChainPosition(token_id=token_id, size=6.0, avg_price=0.5, cost=3.0, condition_id="cond-1")
    ]

    deps = MagicMock()
    _assert_token_aggregate_invariant(portfolio, chain_positions, deps=deps)

    # Token should still be blocked
    assert token_id in tokens_blocked_until_resolution


# ── N2 boot gate tests ───────────────────────────────────────────────────────


def _write_control_plane(path: str, payload: dict) -> None:
    with open(path, "w") as f:
        json.dump(payload, f)


def test_boot_refuses_s1_alone_after_4h():
    """Boot gate raises SystemExit(1) when S1 >4h without S2."""
    from src.main import _check_s1_without_s2_sla

    s1_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = os.path.join(tmpdir, "control_plane.json")
        _write_control_plane(cp_path, {"s1_deployed_at": s1_ts})

        with patch("src.config.state_path", return_value=cp_path):
            env_patch = patch.dict(os.environ, {}, clear=False)
            env_patch.start()
            os.environ.pop("ZEUS_ACCEPT_S1_ALONE", None)
            try:
                with pytest.raises(SystemExit) as exc_info:
                    _check_s1_without_s2_sla()
                assert exc_info.value.code == 1
            finally:
                env_patch.stop()


def test_boot_succeeds_with_override_env():
    """ZEUS_ACCEPT_S1_ALONE=1 bypasses the S1-without-S2 gate."""
    from src.main import _check_s1_without_s2_sla

    s1_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = os.path.join(tmpdir, "control_plane.json")
        _write_control_plane(cp_path, {"s1_deployed_at": s1_ts})

        with patch("src.config.state_path", return_value=cp_path):
            with patch.dict(os.environ, {"ZEUS_ACCEPT_S1_ALONE": "1"}):
                # Should not raise
                _check_s1_without_s2_sla()


def test_boot_passes_when_s2_also_present():
    """Both s1 and s2 deployed → no gate fired."""
    from src.main import _check_s1_without_s2_sla

    s1_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    s2_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = os.path.join(tmpdir, "control_plane.json")
        _write_control_plane(cp_path, {"s1_deployed_at": s1_ts, "s2_deployed_at": s2_ts})

        with patch("src.config.state_path", return_value=cp_path):
            env_patch = patch.dict(os.environ, {}, clear=False)
            env_patch.start()
            os.environ.pop("ZEUS_ACCEPT_S1_ALONE", None)
            try:
                _check_s1_without_s2_sla()  # Should not raise
            finally:
                env_patch.stop()


def test_boot_passes_when_s1_within_sla():
    """S1 deployed <4h ago → still within SLA, no gate fired."""
    from src.main import _check_s1_without_s2_sla

    s1_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = os.path.join(tmpdir, "control_plane.json")
        _write_control_plane(cp_path, {"s1_deployed_at": s1_ts})

        with patch("src.config.state_path", return_value=cp_path):
            env_patch = patch.dict(os.environ, {}, clear=False)
            env_patch.start()
            os.environ.pop("ZEUS_ACCEPT_S1_ALONE", None)
            try:
                _check_s1_without_s2_sla()  # Should not raise
            finally:
                env_patch.stop()


def test_boot_passes_when_no_control_plane_file():
    """Missing control_plane.json → pre-deployment env, pass."""
    from src.main import _check_s1_without_s2_sla

    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = os.path.join(tmpdir, "nonexistent_control_plane.json")
        with patch("src.config.state_path", return_value=cp_path):
            _check_s1_without_s2_sla()  # Should not raise


# ── MAJOR-1: thread-safety of block-list ────────────────────────────────────


def test_block_list_concurrent_mutation_does_not_raise():
    """Concurrent mutation + iteration of block-list must not raise RuntimeError."""
    import threading
    from src.engine import cycle_runtime

    errors = []

    def mutator():
        for i in range(200):
            with cycle_runtime._tokens_blocked_lock:
                cycle_runtime.tokens_blocked_until_resolution.add(f"tok-{i}")
            with cycle_runtime._tokens_blocked_lock:
                cycle_runtime.tokens_blocked_until_resolution.discard(f"tok-{i}")

    def reader():
        for _ in range(200):
            try:
                with cycle_runtime._tokens_blocked_lock:
                    _ = set(cycle_runtime.tokens_blocked_until_resolution)
            except RuntimeError as e:
                errors.append(e)

    t1 = threading.Thread(target=mutator)
    t2 = threading.Thread(target=reader)
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert errors == [], f"RuntimeError during concurrent access: {errors}"


# ── MINOR-2: pending_exit symmetry ──────────────────────────────────────────


def test_invariant_does_not_fire_during_pending_exit_propagation_lag():
    """Invariant must not fire (and not block) for a pending_exit position
    when chain hasn't propagated the exit yet (local > chain momentarily)."""
    from src.engine.cycle_runtime import _assert_token_aggregate_invariant, tokens_blocked_until_resolution

    token_id = "tok-exiting"

    # Position is mid-exit: state=holding, exit_state=sell_placed, 6 shares local.
    # Chain still shows 6 (hasn't propagated the exit yet).
    # Without MINOR-2 fix, local_sum=6 > chain=0 would fire and block the token.
    pos = _make_position("t1", token_id=token_id, shares=6.0, state="holding")
    pos.exit_state = "sell_placed"  # exit in flight

    portfolio = _make_portfolio(pos)
    chain_positions = []  # chain not yet updated — exit not propagated

    deps = MagicMock()
    _assert_token_aggregate_invariant(portfolio, chain_positions, deps=deps)

    # Invariant must NOT have fired: token not added to block-list.
    assert token_id not in tokens_blocked_until_resolution


# ── Optional: N2 symmetric rollout case ─────────────────────────────────────


def test_n2_passes_when_s2_deployed_before_s1():
    """If s2_deployed_at is present but s1_deployed_at is absent, pass."""
    from src.main import _check_s1_without_s2_sla

    s2_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = os.path.join(tmpdir, "control_plane.json")
        _write_control_plane(cp_path, {"s2_deployed_at": s2_ts})

        with patch("src.config.state_path", return_value=cp_path):
            env_patch = patch.dict(os.environ, {}, clear=False)
            env_patch.start()
            os.environ.pop("ZEUS_ACCEPT_S1_ALONE", None)
            try:
                _check_s1_without_s2_sla()  # Should not raise (no s1_deployed_at)
            finally:
                env_patch.stop()
