# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §F1 (Program A — Economics-Authority Split)
"""F1 antibody invariants: balance-only chain rescue stops mutating entry/fill economics.

The pre-F1 chain reconciliation rescue branch (src/state/chain_reconciliation.py)
overwrote `entry_price`, `cost_basis_usd`, `size_usd`, `shares` with the chain
aggregate even on **balance-only** rescues (no linked venue trade fact). This
collided three distinct economics objects:

  LocalIntentEconomics       = submitted_limit_price, submitted_notional_usd, submitted_shares
  VenueTradeFillEconomics    = avg_fill_price, filled_cost_basis_usd, shares_filled
  VenuePositionObservedEcon  = chain_avg_price, chain_cost_basis_usd, chain_shares

F1 splits these on the Position dataclass + on `position_current`, and routes
exit/risk consumers through a single typed `effective_exposure()` derived view
whose authority field is `venue_trade_fill` or `venue_position_observed`.

Acceptance tests (per docs/findings_2026_05_28.md §F1):
1. Balance-only rescue preserves submitted entry/fill economics (limit, notional,
   shares); chain values land in chain_* fields with fill_authority=venue_position_observed.
2. Projection (position_current) carries chain_avg_price + chain_cost_basis_usd;
   entry_price column stays at submitted value, not chain aggregate.
3. Trade-verified rescue path is UNCHANGED — fill economics may move with chain.
4. effective_exposure() routes by fill_authority — chain economics for
   venue_position_observed, fill economics for venue_confirmed_*.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest


_DUMMY_TS = "2026-05-28T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Helpers — minimal fixture wiring for chain_reconciliation.reconcile()
# ---------------------------------------------------------------------------


def _make_pending_position(*, limit_price: float, submitted_notional: float):
    """A pending_tracked position with submitted economics — pre-rescue state."""
    from src.state.portfolio import (
        ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
        Position,
    )

    return Position(
        trade_id="pos-f1-test",
        market_id="m1",
        city="ATL",
        cluster="ATL",
        target_date="2026-05-29",
        bin_label="60-65",
        direction="buy_yes",
        unit="F",
        env="test",
        token_id="t-yes",
        no_token_id="t-no",
        condition_id="c1",
        order_id="ord-f1",
        entry_order_id="ord-f1",
        state="pending_tracked",
        chain_state="unknown",
        entered_at=_DUMMY_TS,
        # submitted economics — these MUST survive a balance-only rescue
        entry_price=limit_price,
        size_usd=submitted_notional,
        cost_basis_usd=submitted_notional,
        shares=submitted_notional / limit_price if limit_price > 0 else 0.0,
        entry_price_submitted=limit_price,
        shares_submitted=submitted_notional / limit_price if limit_price > 0 else 0.0,
        submitted_notional_usd=submitted_notional,
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
        decision_snapshot_id="snap-f1",
        entry_method="ens_member_counting",
        strategy_key="settlement_capture",
    )


def _setup_world_db():
    """Fresh in-memory DB with init_schema."""
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _seed_canonical_pending_baseline(conn, pos):
    """Write a canonical pending baseline so rescue's
    `_canonical_rescue_baseline_available` returns True."""
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.ledger import append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase

    # F4 (docs/findings_2026_05_28.md §F4, 2026-05-28): builder requires
    # explicit phase_after. This seeds a pending-entry baseline.
    events, projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-f1",
        source_module="tests.f1_setup",
    )
    append_many_and_project(conn, events, projection)


def _seed_venue_trade_fact(conn, *, trade_id: str, order_id: str, filled_size: float, fill_price: float):
    """Mark an order as a trade-verified fill so
    `_pending_entry_has_linked_fill_fact` returns True."""
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, source, state,
            filled_size, fill_price, observed_at, local_sequence,
            raw_payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"vtf-{order_id}",
            order_id,
            f"cmd-{order_id}",
            "REST",
            "CONFIRMED",
            str(filled_size),
            str(fill_price),
            _DUMMY_TS,
            1,
            f"hash-{order_id}",
        ),
    )
    conn.commit()


def _make_chain_position(*, token_id: str, size: float, avg_price: float, cost: float):
    from src.state.chain_reconciliation import ChainPosition

    return ChainPosition(
        token_id=token_id,
        condition_id="c1",
        size=size,
        avg_price=avg_price,
        cost=cost,
    )


def _run_rescue(conn, pos, chain):
    """Drive reconcile() on a single pending position + its chain match."""
    from src.state.chain_reconciliation import reconcile
    from src.state.portfolio import PortfolioState

    portfolio = PortfolioState(positions=[pos])
    return reconcile(portfolio, [chain], conn=conn)


# ---------------------------------------------------------------------------
# F1 #1 — balance-only rescue preserves submitted entry economics
# ---------------------------------------------------------------------------


def test_balance_only_rescue_preserves_submitted_economics() -> None:
    """No linked venue_trade_facts row → balance-only rescue branch.

    The Position's `entry_price`, `cost_basis_usd`, `size_usd`, `shares`
    MUST remain at their submitted values. The chain aggregate lands in
    `chain_avg_price`, `chain_cost_basis_usd`, `chain_shares`.
    `fill_authority` becomes `venue_position_observed`.
    """
    from src.state.portfolio import FILL_AUTHORITY_VENUE_POSITION_OBSERVED

    conn = _setup_world_db()
    pos = _make_pending_position(limit_price=0.38, submitted_notional=10.0)
    _seed_canonical_pending_baseline(conn, pos)
    # NO venue_trade_facts row → balance-only branch.

    chain = _make_chain_position(
        token_id=pos.token_id, size=25.0, avg_price=0.44, cost=11.0
    )
    _run_rescue(conn, pos, chain)

    # Submitted economics survive.
    assert pos.entry_price == pytest.approx(0.38), (
        f"balance-only rescue must NOT overwrite entry_price; got {pos.entry_price}"
    )
    assert pos.cost_basis_usd == pytest.approx(10.0), (
        f"balance-only rescue must NOT overwrite cost_basis_usd; got {pos.cost_basis_usd}"
    )
    assert pos.size_usd == pytest.approx(10.0), (
        f"balance-only rescue must NOT overwrite size_usd; got {pos.size_usd}"
    )
    # Chain economics land on chain_* fields.
    assert pos.chain_avg_price == pytest.approx(0.44)
    assert pos.chain_cost_basis_usd == pytest.approx(11.0)
    assert pos.chain_shares == pytest.approx(25.0)
    assert pos.fill_authority == FILL_AUTHORITY_VENUE_POSITION_OBSERVED


# ---------------------------------------------------------------------------
# F1 #2 — projection writes chain economics, NOT entry economics
# ---------------------------------------------------------------------------


def test_balance_only_rescue_writes_chain_economics_into_projection() -> None:
    """After append_many_and_project, position_current.chain_avg_price and
    chain_cost_basis_usd reflect chain values; entry_price column reflects the
    submitted (pre-rescue) value, NOT chain aggregate."""
    conn = _setup_world_db()
    pos = _make_pending_position(limit_price=0.38, submitted_notional=10.0)
    _seed_canonical_pending_baseline(conn, pos)
    chain = _make_chain_position(
        token_id=pos.token_id, size=25.0, avg_price=0.44, cost=11.0
    )
    _run_rescue(conn, pos, chain)

    row = conn.execute(
        """
        SELECT entry_price, cost_basis_usd, size_usd, shares,
               chain_avg_price, chain_cost_basis_usd, chain_shares,
               fill_authority
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    assert row is not None, "rescue must project a position_current row"

    # Projection entry/fill economics: submitted (pre-rescue) values.
    assert row["entry_price"] == pytest.approx(0.38), (
        f"projection entry_price must be submitted (0.38), got {row['entry_price']}"
    )
    assert row["cost_basis_usd"] == pytest.approx(10.0)
    assert row["size_usd"] == pytest.approx(10.0)
    # Projection chain economics: chain aggregate.
    assert row["chain_avg_price"] == pytest.approx(0.44)
    assert row["chain_cost_basis_usd"] == pytest.approx(11.0)
    assert row["chain_shares"] == pytest.approx(25.0)
    assert row["fill_authority"] == "venue_position_observed"


# ---------------------------------------------------------------------------
# F1 #3 — trade-verified rescue still updates entry economics
# ---------------------------------------------------------------------------


def test_trade_verified_rescue_still_writes_fill_economics() -> None:
    """When a positive venue_trade_facts row links the order, fill_authority
    is venue_confirmed_full and entry_price reflects the verified avg fill
    price (the existing trade-verified rescue path is preserved verbatim).
    """
    from src.state.portfolio import FILL_AUTHORITY_VENUE_CONFIRMED_FULL

    conn = _setup_world_db()
    pos = _make_pending_position(limit_price=0.38, submitted_notional=10.0)
    _seed_canonical_pending_baseline(conn, pos)
    # Trade-verified path: a confirmed venue trade fact links the order.
    _seed_venue_trade_fact(
        conn,
        trade_id=pos.trade_id,
        order_id="ord-f1",
        filled_size=25.0,
        fill_price=0.44,
    )

    chain = _make_chain_position(
        token_id=pos.token_id, size=25.0, avg_price=0.44, cost=11.0
    )
    _run_rescue(conn, pos, chain)

    # Trade-verified path: chain values move into entry/fill fields (unchanged behavior).
    assert pos.entry_price == pytest.approx(0.44), (
        f"trade-verified rescue path keeps prior behavior — entry_price should "
        f"reflect verified avg fill price, got {pos.entry_price}"
    )
    assert pos.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL


# ---------------------------------------------------------------------------
# F1 #4 — effective_exposure routes by fill_authority
# ---------------------------------------------------------------------------


def test_effective_exposure_routes_by_authority_balance_only() -> None:
    """effective_exposure() returns chain economics + source='venue_position_observed'
    when fill_authority == venue_position_observed."""
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        Position,
    )

    pos = Position(
        trade_id="p1",
        market_id="m1",
        city="ATL",
        cluster="ATL",
        target_date="2026-05-29",
        bin_label="60-65",
        direction="buy_yes",
        unit="F",
        env="test",
        # Submitted economics:
        entry_price=0.38,
        size_usd=10.0,
        cost_basis_usd=10.0,
        shares=26.31,
        # Chain economics:
        chain_avg_price=0.44,
        chain_cost_basis_usd=11.0,
        chain_shares=25.0,
        fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        state="entered",
    )
    exposure = pos.effective_exposure()
    assert exposure.source_authority == "venue_position_observed"
    assert exposure.shares == pytest.approx(25.0)
    assert exposure.cost_basis_usd == pytest.approx(11.0)
    assert exposure.avg_price == pytest.approx(0.44)


def test_effective_exposure_routes_by_authority_trade_verified() -> None:
    """effective_exposure() returns fill economics + source='venue_trade_fill'
    when fill_authority is venue_confirmed_*."""
    from src.state.portfolio import (
        ENTRY_ECONOMICS_AVG_FILL_PRICE,
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        Position,
    )

    pos = Position(
        trade_id="p2",
        market_id="m1",
        city="ATL",
        cluster="ATL",
        target_date="2026-05-29",
        bin_label="60-65",
        direction="buy_yes",
        unit="F",
        env="test",
        # Submitted economics:
        entry_price=0.44,
        size_usd=11.0,
        cost_basis_usd=11.0,
        shares=25.0,
        # Trade fill economics:
        entry_price_avg_fill=0.44,
        shares_filled=25.0,
        filled_cost_basis_usd=11.0,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        state="entered",
    )
    exposure = pos.effective_exposure()
    assert exposure.source_authority == "venue_trade_fill"
    assert exposure.shares == pytest.approx(25.0)
    assert exposure.cost_basis_usd == pytest.approx(11.0)
    assert exposure.avg_price == pytest.approx(0.44)


def test_balance_only_partial_exit_updates_chain_exposure() -> None:
    """Antibody for PR1 critic SEV-1: partial exit on a balance-only position
    must reduce chain_shares / chain_cost_basis_usd proportionally so that
    effective_exposure() reflects updated exposure before the next reconcile
    cycle.

    Pre-fix: _apply_partial_exit_fill wrote only position.shares + position.cost_basis_usd.
    Since effective_shares routes via chain_shares for fill_authority=venue_position_observed,
    effective_exposure().shares would still return the pre-exit stale aggregate.
    """
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        Position,
    )
    from src.execution.exit_lifecycle import _apply_partial_exit_fill

    pos = Position(
        trade_id="bal-only-partial",
        market_id="m-partial",
        city="ATL",
        cluster="ATL",
        target_date="2026-06-01",
        bin_label="60-65",
        direction="buy_yes",
        unit="F",
        env="test",
        token_id="t-partial",
        no_token_id="t-partial-no",
        condition_id="c-partial",
        order_id="ord-partial",
        entry_order_id="ord-partial",
        state="entered",
        chain_state="synced",
        # Submitted / legacy mirrors:
        entry_price=0.48,
        size_usd=9.6,
        cost_basis_usd=9.6,
        shares=20.0,
        # Chain-observed economics (authoritative for this fill_authority):
        chain_shares=20.0,
        chain_avg_price=0.48,
        chain_cost_basis_usd=9.6,
        fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        # Fill-grade fields empty (balance-only):
        shares_filled=0.0,
        filled_cost_basis_usd=0.0,
    )

    # Partial exit: sell 5 of 20 → remaining 15.
    changed = _apply_partial_exit_fill(
        pos,
        filled_shares=5.0,
        remaining_shares=15.0,
        fill_price=0.70,
        order_id="sell-partial-bal",
        status="PARTIAL",
    )

    assert changed is True, "_apply_partial_exit_fill returned False unexpectedly"

    # Chain fields must reflect reduced exposure (remaining_ratio = 15/20 = 0.75).
    assert pos.chain_shares == pytest.approx(15.0), (
        f"chain_shares={pos.chain_shares!r} — effective_exposure would return stale 20.0"
    )
    assert pos.chain_cost_basis_usd == pytest.approx(7.2), (
        f"chain_cost_basis_usd={pos.chain_cost_basis_usd!r} expected 9.6 * 0.75 = 7.2"
    )

    # effective_exposure() must return updated chain values, NOT stale pre-exit aggregate.
    exposure = pos.effective_exposure()
    assert exposure.source_authority == "venue_position_observed", (
        f"source_authority={exposure.source_authority!r}"
    )
    assert exposure.shares == pytest.approx(15.0), (
        f"effective_exposure().shares={exposure.shares!r} — SEV-1 regression: got stale 20.0"
    )
    assert exposure.cost_basis_usd == pytest.approx(7.2), (
        f"effective_exposure().cost_basis_usd={exposure.cost_basis_usd!r}"
    )

    # Submitted / fill-grade economics are NOT mutated by a balance-only partial exit.
    assert pos.entry_price == pytest.approx(0.48), "entry_price must remain submitted value"
    assert pos.shares_filled == pytest.approx(0.0), "shares_filled must not be touched"
    assert pos.filled_cost_basis_usd == pytest.approx(0.0), "filled_cost_basis_usd must not be touched"


def test_size_mismatch_sync_populates_chain_economics_additively() -> None:
    """Antibody for PR1 critic in-spirit-F1: Rule-2 size-mismatch sync must
    populate chain_avg_price + chain_cost_basis_usd from chain observation,
    unconditionally, regardless of whether entry/fill fields are also updated.

    This ensures VenuePositionObservedEcon is always current after a Rule-2
    sync — callers reading chain_* fields (e.g. effective_exposure() for
    balance-only authority) get fresh values without waiting for a full rescue.
    """
    from src.state.chain_reconciliation import reconcile
    from src.state.portfolio import (
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        ENTRY_ECONOMICS_AVG_FILL_PRICE,
        PortfolioState,
        Position,
    )

    conn = _setup_world_db()

    # An entered (non-pending) position already in the Rule-2 branch.
    # chain_avg_price / chain_cost_basis_usd start empty.
    pos = Position(
        trade_id="sm-test-trade",
        market_id="m-sm",
        city="CHI",
        cluster="CHI",
        target_date="2026-06-01",
        bin_label="70-75",
        direction="buy_yes",
        unit="F",
        env="test",
        token_id="t-sm",
        no_token_id="t-sm-no",
        condition_id="c-sm",
        order_id="ord-sm",
        entry_order_id="ord-sm",
        state="entered",
        chain_state="synced",
        entry_price=0.50,
        size_usd=10.0,
        cost_basis_usd=10.0,
        shares=20.0,
        shares_filled=20.0,
        filled_cost_basis_usd=10.0,
        entry_price_avg_fill=0.50,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        # Start with no chain economics:
        chain_avg_price=0.0,
        chain_cost_basis_usd=0.0,
        chain_shares=20.0,
    )

    # Chain observation with updated average price / cost.
    from src.state.chain_reconciliation import ChainPosition
    chain = ChainPosition(
        token_id="t-sm",
        condition_id="c-sm",
        size=20.0,
        avg_price=0.52,
        cost=10.4,
    )

    portfolio = PortfolioState(positions=[pos])
    reconcile(portfolio, [chain], conn=conn)

    # chain_* fields must be populated from chain observation.
    assert pos.chain_avg_price == pytest.approx(0.52), (
        f"chain_avg_price={pos.chain_avg_price!r} — Rule-2 sync must populate chain economics"
    )
    assert pos.chain_cost_basis_usd == pytest.approx(10.4), (
        f"chain_cost_basis_usd={pos.chain_cost_basis_usd!r}"
    )
    # Entry/fill economics remain from trade-verified fill (not overwritten by chain
    # because _size_mismatch_eligible defaults False for a plain entered position).
    assert pos.entry_price == pytest.approx(0.52), (
        "Rule-2 non-eligible: entry_price updated from chain (existing behavior unchanged)"
    )

    conn.close()
