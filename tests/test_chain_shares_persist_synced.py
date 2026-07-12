# Lifecycle: created=2026-05-31; last_reviewed=2026-07-11; last_reused=2026-07-11
# Purpose: Relationship test — chain economics (chain_shares, chain_seen_at) persist
#   to position_current for SYNCED positions and survive a fresh DB read (task #56).
# Reuse: inspect chain_reconciliation.reconcile() else-branch + _append_canonical_chain_observation_if_available
#   before re-running; requires position_current.chain_shares + chain_seen_at columns (added F1).
"""Relationship test: chain_shares persists to position_current for SYNCED positions.

Fitz methodology — relationship test, not a function test. The cross-module
invariant under test:

  WHEN chain_reconciliation.reconcile() confirms a position SYNCED to chain
  (matched, no size-mismatch, single-lot), THEN the chain economics it observed
  MUST be PERSISTED to position_current.chain_shares via a canonical write —
  the on-disk DB must equal the freshly-observed chain.size, and that equality
  must survive a fresh connection / reload.

Pre-fix (root, src/state/chain_reconciliation.py): the matched `else:` branch
set `corrected.chain_shares = chain.size` IN-MEMORY but issued NO canonical
write. Only the size-MISMATCH branch persisted chain economics (via
_append_canonical_size_correction_if_available). So a position that already
matched chain kept position_current.chain_shares = NULL forever. EVIDENCE:
16 on-chain positions, all chain_state='synced', chain_shares NULL on all 101
rows.

Three relationship assertions:
  1. SYNCED + persisted chain_shares NULL → after reconcile, position_current
     .chain_shares == chain.size, PERSISTED (survives fresh DB read).            [RED→GREEN]
  2. Multi-lot aggregate-backed position is NOT clobbered with the aggregate.    [no-regression]
  3. Size-mismatch position still routes through the existing correction path.   [no-regression]
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest

from src.state.chain_reconciliation import ChainPosition, reconcile
from src.state.portfolio import Position, PortfolioState

_DUMMY_TS = "2026-05-01T00:00:00+00:00"


def _setup_db_on_disk(path: str) -> sqlite3.Connection:
    """Fresh on-disk DB with full schema (so reconcile can write events)."""
    from src.state.db import init_schema

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _make_position(
    *,
    trade_id: str,
    token_id: str,
    shares: float,
    entered_at: str = _DUMMY_TS,
    chain_state: str = "synced",
) -> Position:
    return Position(
        trade_id=trade_id,
        market_id="mkt-1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-05-01",
        bin_label="39-40F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.5,
        p_posterior=0.6,
        edge=0.1,
        shares=shares,
        cost_basis_usd=10.0,
        entered_at=entered_at,
        decision_snapshot_id="snap-1",
        entry_method="ens_member_counting",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="update_reaction",
        state="entered",
        order_id="ord-1",
        order_status="filled",
        order_posted_at=_DUMMY_TS,
        chain_state=chain_state,
        token_id=token_id,
        condition_id="cond-1",
    )


def _seed_position_current(
    conn: sqlite3.Connection,
    pos: Position,
    *,
    chain_shares,
    phase: str = "active",
    exit_reason: str | None = None,
    exit_retry_count: int = 0,
    next_exit_retry_at: str | None = None,
    last_monitor_prob: float | None = None,
    last_monitor_prob_is_fresh: int | None = None,
    last_monitor_edge: float | None = None,
    last_monitor_market_price: float | None = None,
    last_monitor_market_price_is_fresh: int | None = None,
) -> None:
    """Seed an ACTIVE position_current row with the given chain_shares (None = NULL).

    Mirrors a daemon that opened/entered the position but has never persisted
    a chain observation (chain_shares NULL) — the exact pre-fix on-disk state.
    """
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, ordered_values

    payload = {
        "position_id": pos.trade_id,
        "phase": phase,
        "trade_id": pos.trade_id,
        "market_id": pos.market_id,
        "city": pos.city,
        "cluster": pos.cluster,
        "target_date": pos.target_date,
        "bin_label": pos.bin_label,
        "direction": pos.direction,
        "unit": "F",
        "size_usd": pos.size_usd,
        "shares": pos.shares,
        "cost_basis_usd": pos.cost_basis_usd,
        "entry_price": pos.entry_price,
        "p_posterior": pos.p_posterior,
        "last_monitor_prob": last_monitor_prob,
        "last_monitor_prob_is_fresh": last_monitor_prob_is_fresh,
        "last_monitor_edge": last_monitor_edge,
        "last_monitor_market_price": last_monitor_market_price,
        "last_monitor_market_price_is_fresh": last_monitor_market_price_is_fresh,
        "decision_snapshot_id": pos.decision_snapshot_id,
        "entry_method": pos.entry_method,
        "strategy_key": pos.strategy_key,
        "edge_source": pos.edge_source,
        "discovery_mode": pos.discovery_mode,
        "chain_state": pos.chain_state,
        "token_id": pos.token_id,
        "no_token_id": "",
        "condition_id": pos.condition_id,
        "order_id": pos.order_id,
        "order_status": pos.order_status,
        "updated_at": _DUMMY_TS,
        "temperature_metric": "high",
        "fill_authority": "",
        "recovery_authority": "",
        "chain_shares": chain_shares,  # None → persisted NULL (pre-fix on-disk state)
        "chain_avg_price": None,
        "chain_cost_basis_usd": None,
        "chain_seen_at": "",
        "chain_absence_at": "",
        "realized_pnl_usd": None,
        "exit_price": None,
        "settlement_price": None,
        "settled_at": None,
        "exit_reason": exit_reason,
        "exit_retry_count": exit_retry_count,
        "next_exit_retry_at": next_exit_retry_at,
    }
    conn.execute(
        f"""
        INSERT OR REPLACE INTO position_current ({", ".join(CANONICAL_POSITION_CURRENT_COLUMNS)})
        VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})
        """,
        ordered_values(payload, CANONICAL_POSITION_CURRENT_COLUMNS),
    )
    conn.commit()


def _read_persisted_chain_shares(db_path: str, trade_id: str):
    """Open a FRESH connection and read position_current.chain_shares.

    A fresh connection proves the value is committed to disk, not merely set on
    the in-memory Position or held in the writer's transaction.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT chain_shares FROM position_current WHERE position_id = ?",
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return ("missing", None)
    return ("ok", row["chain_shares"])


def _read_persisted_chain_seen_at(db_path: str, trade_id: str) -> str | None:
    """Open a FRESH connection and read position_current.chain_seen_at.

    Returns the persisted ISO-8601 string or None (empty / NULL / missing row).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT chain_seen_at FROM position_current WHERE position_id = ?",
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return row["chain_seen_at"] or None


def _read_persisted_chain_state(db_path: str, trade_id: str) -> tuple[str | None, float | None, str | None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT phase, chain_shares, chain_seen_at FROM position_current WHERE position_id = ?",
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return (None, None, None)
    return (row["phase"], row["chain_shares"], row["chain_seen_at"])


def _read_persisted_exit_state(db_path: str, trade_id: str) -> tuple[str | None, int | None, str | None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT exit_reason, exit_retry_count, next_exit_retry_at FROM position_current WHERE position_id = ?",
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return (None, None, None)
    return (row["exit_reason"], row["exit_retry_count"], row["next_exit_retry_at"])


def _read_persisted_monitor_state(db_path: str, trade_id: str) -> tuple[float | None, int | None, float | None, float | None, int | None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT last_monitor_prob,
                   last_monitor_prob_is_fresh,
                   last_monitor_edge,
                   last_monitor_market_price,
                   last_monitor_market_price_is_fresh
              FROM position_current
             WHERE position_id = ?
            """,
            (trade_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return (None, None, None, None, None)
    return (
        row["last_monitor_prob"],
        row["last_monitor_prob_is_fresh"],
        row["last_monitor_edge"],
        row["last_monitor_market_price"],
        row["last_monitor_market_price_is_fresh"],
    )


# ---------------------------------------------------------------------------
# 1. SYNCED + NULL chain_shares → persisted to position_current  [RED→GREEN]
# ---------------------------------------------------------------------------


def test_synced_null_chain_shares_persisted_across_fresh_read() -> None:
    """SYNCED position whose persisted chain_shares is NULL must, after
    reconcile, have position_current.chain_shares == chain.size — committed to
    disk and visible on a fresh connection.

    Pre-fix this FAILS: the matched-no-mismatch path mutates the in-memory
    Position but never writes position_current, so the persisted value stays
    NULL. This is the root of "local db misalign with chain".
    """
    chain_size = 20.0
    trade_id = "synced-null-pos"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(trade_id=trade_id, token_id="tok-sync", shares=chain_size)
        _seed_position_current(conn, pos, chain_shares=None)

        # Sanity: persisted chain_shares starts NULL.
        status, before = _read_persisted_chain_shares(db_path, trade_id)
        assert status == "ok" and before is None, (
            f"precondition: persisted chain_shares must start NULL, got {before!r}"
        )

        portfolio = PortfolioState(positions=[pos])
        chain = ChainPosition(
            token_id="tok-sync",
            size=chain_size,   # == shares → matched, no size mismatch
            avg_price=0.55,
            cost=11.0,
            condition_id="cond-1",
        )

        reconcile(portfolio, [chain], conn=conn)
        conn.close()

        # Relationship assertion: chain.size is PERSISTED, survives fresh read.
        status, persisted = _read_persisted_chain_shares(db_path, trade_id)

    assert status == "ok", f"position_current row missing after reconcile: {status}"
    assert persisted is not None, (
        "REGRESSION/PRE-FIX: position_current.chain_shares is still NULL after "
        "reconcile of a SYNCED position — the matched path issued no canonical "
        "write. Local DB diverges from on-chain reality."
    )
    assert persisted == pytest.approx(chain_size), (
        f"persisted chain_shares={persisted!r} must equal chain.size={chain_size}"
    )


def test_synced_chain_shares_observation_emits_canonical_event() -> None:
    """The persistence must come via a CANONICAL position_events write
    (not a raw UPDATE that bypasses authority / INV-37)."""
    trade_id = "synced-canonical-pos"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(trade_id=trade_id, token_id="tok-canon", shares=20.0)
        _seed_position_current(conn, pos, chain_shares=None)

        portfolio = PortfolioState(positions=[pos])
        chain = ChainPosition(
            token_id="tok-canon", size=20.0, avg_price=0.55, cost=11.0, condition_id="cond-1"
        )
        stats = reconcile(portfolio, [chain], conn=conn)

        rows = conn.execute(
            "SELECT event_type, payload_json FROM position_events WHERE position_id = ?",
            (trade_id,),
        ).fetchall()
        conn.close()

    assert stats.get("chain_observation_persisted", 0) == 1, (
        f"expected one chain_observation_persisted, stats={stats}"
    )
    obs = [r for r in rows if '"reason": "chain_economics_observed"' in (r["payload_json"] or "")]
    assert len(obs) == 1, (
        f"expected exactly one canonical chain-observation event; got "
        f"{[r['event_type'] for r in rows]}"
    )
    # No-op phase grammar: persisted as CHAIN_SIZE_CORRECTED (only allowed
    # no-op-phase chain event type) with the disambiguating reason.
    assert obs[0]["event_type"] == "CHAIN_SIZE_CORRECTED"


def test_pending_exit_chain_observation_preserves_pending_exit_phase() -> None:
    """A pending_exit position with chain-confirmed shares still needs chain
    observation refreshes.  The write must preserve pending_exit rather than
    requiring an active/day0 baseline or releasing the exit lifecycle state.
    """
    trade_id = "pending-exit-chain-observed"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(
            trade_id=trade_id,
            token_id="tok-pending-exit",
            shares=5.07,
            chain_state="synced",
        )
        pos.state = "pending_exit"
        pos.exit_state = "backoff_exhausted"
        _seed_position_current(
            conn,
            pos,
            chain_shares=None,
            phase="pending_exit",
            exit_reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
            exit_retry_count=7,
            last_monitor_prob=0.42,
            last_monitor_prob_is_fresh=1,
            last_monitor_edge=-0.05,
            last_monitor_market_price=0.31,
            last_monitor_market_price_is_fresh=1,
        )

        portfolio = PortfolioState(positions=[pos])
        chain = ChainPosition(
            token_id="tok-pending-exit",
            size=5.07,
            avg_price=0.64,
            cost=3.2448,
            condition_id="cond-pending-exit",
        )
        stats = reconcile(portfolio, [chain], conn=conn)

        rows = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no
            """,
            (trade_id,),
        ).fetchall()
        conn.close()

        phase, persisted_shares, persisted_seen_at = _read_persisted_chain_state(
            db_path, trade_id
        )
        exit_reason, exit_retry_count, _next_retry = _read_persisted_exit_state(
            db_path, trade_id
        )
        monitor_state = _read_persisted_monitor_state(db_path, trade_id)

    assert stats.get("chain_observation_persisted", 0) == 1
    assert phase == "pending_exit"
    assert persisted_shares == pytest.approx(5.07)
    assert persisted_seen_at
    assert exit_reason == "MARKET_CLOSED_AWAITING_SETTLEMENT"
    assert exit_retry_count == 7
    assert monitor_state == pytest.approx((0.42, 1, -0.05, 0.31, 1))
    obs = [r for r in rows if '"reason": "chain_economics_observed"' in (r["payload_json"] or "")]
    assert len(obs) == 1
    assert obs[0]["event_type"] == "CHAIN_SIZE_CORRECTED"
    assert obs[0]["phase_before"] == "pending_exit"
    assert obs[0]["phase_after"] == "pending_exit"


# ---------------------------------------------------------------------------
# 2. Multi-lot aggregate-backed NOT clobbered with the aggregate  [no-regression]
# ---------------------------------------------------------------------------


def test_aggregate_backed_multilot_not_clobbered_with_aggregate() -> None:
    """Two lots sharing a token (aggregate-backed): chain.size is the token
    AGGREGATE across both lots. Persisting the aggregate onto either single
    lot's chain_shares would corrupt per-lot truth. The observation helper
    must SKIP aggregate-backed lots (persisted chain_shares stays NULL)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos_a = _make_position(
            trade_id="agg-lot-a", token_id="tok-agg", shares=20.0,
            entered_at="2026-05-01T00:00:00+00:00",
        )
        pos_b = _make_position(
            trade_id="agg-lot-b", token_id="tok-agg", shares=20.0,
            entered_at="2026-05-02T00:00:00+00:00",
        )
        _seed_position_current(conn, pos_a, chain_shares=None)
        _seed_position_current(conn, pos_b, chain_shares=None)

        portfolio = PortfolioState(positions=[pos_a, pos_b])
        # chain.size = 40 = aggregate of both lots → both allocated, both aggregate-backed.
        chain = ChainPosition(
            token_id="tok-agg", size=40.0, avg_price=0.55, cost=22.0, condition_id="cond-1"
        )
        reconcile(portfolio, [chain], conn=conn)
        conn.close()

        status_a, persisted_a = _read_persisted_chain_shares(db_path, "agg-lot-a")
        status_b, persisted_b = _read_persisted_chain_shares(db_path, "agg-lot-b")

    # Neither single lot may carry the aggregate (40) as its chain_shares.
    assert persisted_a != pytest.approx(40.0), (
        f"agg-lot-a chain_shares={persisted_a!r} was clobbered with the aggregate 40"
    )
    assert persisted_b != pytest.approx(40.0), (
        f"agg-lot-b chain_shares={persisted_b!r} was clobbered with the aggregate 40"
    )
    # The aggregate-backed path skips the observation entirely → stays NULL.
    assert persisted_a is None and persisted_b is None, (
        f"aggregate-backed lots must not receive a chain observation; "
        f"got a={persisted_a!r} b={persisted_b!r}"
    )


# ---------------------------------------------------------------------------
# 3. Size-mismatch still routes through the existing correction path  [no-regression]
# ---------------------------------------------------------------------------


def test_size_mismatch_still_uses_correction_path() -> None:
    """A single-lot position whose chain.size differs from local shares must
    still go through the SIZE-MISMATCH correction path (CHAIN_SIZE_CORRECTED
    with reason='chain_size_corrected'), NOT the chain-observation path."""
    trade_id = "mismatch-pos"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(trade_id=trade_id, token_id="tok-mismatch", shares=20.0)
        _seed_position_current(conn, pos, chain_shares=None)

        portfolio = PortfolioState(positions=[pos])
        chain = ChainPosition(
            token_id="tok-mismatch",
            size=25.0,   # differs from shares=20.0 → SIZE MISMATCH branch
            avg_price=0.55,
            cost=11.0,
            condition_id="cond-1",
        )
        stats = reconcile(portfolio, [chain], conn=conn)

        rows = conn.execute(
            "SELECT payload_json FROM position_events WHERE position_id = ?",
            (trade_id,),
        ).fetchall()
        conn.close()

        status, persisted = _read_persisted_chain_shares(db_path, trade_id)

    payloads = [r["payload_json"] or "" for r in rows]
    # The correction path fired (reason chain_size_corrected), NOT observation.
    assert any('"reason": "chain_size_corrected"' in p for p in payloads), (
        f"size-mismatch must route through the correction path; payloads={payloads}"
    )
    assert not any('"reason": "chain_economics_observed"' in p for p in payloads), (
        "size-mismatch must NOT use the chain-observation path"
    )
    assert stats.get("chain_observation_persisted", 0) == 0, (
        f"observation path must not fire for a size mismatch; stats={stats}"
    )
    # Correction path persists chain.size too (the existing behaviour).
    assert status == "ok" and persisted == pytest.approx(25.0), (
        f"correction path must persist chain.size=25.0, got {persisted!r}"
    )


def test_pending_exit_size_mismatch_preserves_pending_exit_phase() -> None:
    """Size correction is also a no-transition chain fact for pending_exit.

    A sell-retry/backoff position can still be present on chain with updated
    aggregate economics.  Reconciliation must persist that chain truth without
    releasing pending_exit or quarantining the position for lacking an active
    baseline.
    """
    trade_id = "pending-exit-size-corrected"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(
            trade_id=trade_id,
            token_id="tok-pending-exit-size",
            shares=5.0,
            chain_state="synced",
        )
        pos.state = "pending_exit"
        pos.exit_state = "retry_pending"
        _seed_position_current(
            conn,
            pos,
            chain_shares=5.0,
            phase="pending_exit",
            exit_reason="EDGE_REVERSAL",
            exit_retry_count=2,
            next_exit_retry_at="2026-05-01T00:05:00+00:00",
            last_monitor_prob=0.58,
            last_monitor_prob_is_fresh=1,
            last_monitor_edge=-0.12,
            last_monitor_market_price=0.09,
            last_monitor_market_price_is_fresh=1,
        )

        portfolio = PortfolioState(positions=[pos])
        chain = ChainPosition(
            token_id="tok-pending-exit-size",
            size=6.25,
            avg_price=0.64,
            cost=4.0,
            condition_id="cond-pending-exit-size",
        )
        stats = reconcile(portfolio, [chain], conn=conn)

        rows = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no
            """,
            (trade_id,),
        ).fetchall()
        conn.close()

        phase, persisted_shares, persisted_seen_at = _read_persisted_chain_state(
            db_path, trade_id
        )
        exit_reason, exit_retry_count, next_retry = _read_persisted_exit_state(
            db_path, trade_id
        )
        monitor_state = _read_persisted_monitor_state(db_path, trade_id)

    payloads = [r["payload_json"] or "" for r in rows]
    assert stats.get("updated", 0) == 1
    assert stats.get("review_required_persisted", 0) == 0
    assert stats.get("skipped_size_correction_missing_canonical_baseline", 0) == 0
    assert phase == "pending_exit"
    assert persisted_shares == pytest.approx(6.25)
    assert persisted_seen_at
    assert exit_reason == "EDGE_REVERSAL"
    assert exit_retry_count == 2
    assert next_retry == "2026-05-01T00:05:00+00:00"
    assert monitor_state == pytest.approx((0.58, 1, -0.12, 0.09, 1))
    correction = [r for r in rows if '"reason": "chain_size_corrected"' in (r["payload_json"] or "")]
    assert len(correction) == 1, f"expected correction event; payloads={payloads}"
    assert correction[0]["event_type"] == "CHAIN_SIZE_CORRECTED"
    assert correction[0]["phase_before"] == "pending_exit"
    assert correction[0]["phase_after"] == "pending_exit"
    assert not any('"reason": "chain_economics_observed"' in p for p in payloads)


# ---------------------------------------------------------------------------
# 4. chain_seen_at advances on 2nd cycle even when chain_shares is unchanged
#    (Copilot review issue #1: stale positive-observation timestamp)
# ---------------------------------------------------------------------------


def test_chain_seen_at_advances_across_two_cycles_persisted() -> None:
    """Timestamp-refresh test (Copilot review 2026-05-31, issue #1).

    A synced position observed across two reconcile cycles where chain_shares
    is UNCHANGED must still have its chain_seen_at ADVANCED on the 2nd cycle
    when the first-cycle timestamp is old enough to be considered stale.

    Pre-fix: the helper returned False immediately when shares were unchanged,
    so chain_seen_at was permanently frozen at the first-population value.
    On daemon restart classify_chain_state() reads chain_seen_at back into
    Position.chain_verified_at; a stale positive-observation timestamp caused
    CHAIN_UNKNOWN mis-classification for long-lived synced positions.

    Post-fix: the observation event is re-emitted when the persisted
    chain_seen_at is older than _CHAIN_SEEN_AT_MAX_AGE_SECONDS, advancing
    the timestamp. The advanced value must survive a fresh DB read.
    """
    from src.state.chain_reconciliation import _CHAIN_SEEN_AT_MAX_AGE_SECONDS

    chain_size = 15.0
    trade_id = "ts-refresh-pos"

    # Use a timestamp that is clearly past the max-age threshold so cycle 2
    # always sees a stale timestamp even on fast machines.
    stale_offset_s = _CHAIN_SEEN_AT_MAX_AGE_SECONDS + 120  # 120s past the threshold
    from datetime import datetime, timezone, timedelta

    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_offset_s)
    ).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(trade_id=trade_id, token_id="tok-ts", shares=chain_size)
        # Seed with chain_shares already populated (post-first-cycle state) but
        # with a stale chain_seen_at to simulate a long-lived synced position.
        from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, ordered_values

        payload = {
            "position_id": trade_id,
            "phase": "active",
            "trade_id": trade_id,
            "market_id": pos.market_id,
            "city": pos.city,
            "cluster": pos.cluster,
            "target_date": pos.target_date,
            "bin_label": pos.bin_label,
            "direction": pos.direction,
            "unit": "F",
            "size_usd": pos.size_usd,
            "shares": pos.shares,
            "cost_basis_usd": pos.cost_basis_usd,
            "entry_price": pos.entry_price,
            "p_posterior": pos.p_posterior,
            "last_monitor_prob": None,
            "last_monitor_edge": None,
            "last_monitor_market_price": None,
            "decision_snapshot_id": pos.decision_snapshot_id,
            "entry_method": pos.entry_method,
            "strategy_key": pos.strategy_key,
            "edge_source": pos.edge_source,
            "discovery_mode": pos.discovery_mode,
            "chain_state": pos.chain_state,
            "token_id": pos.token_id,
            "no_token_id": "",
            "condition_id": pos.condition_id,
            "order_id": pos.order_id,
            "order_status": pos.order_status,
            "updated_at": _DUMMY_TS,
            "temperature_metric": "high",
            "fill_authority": "",
            "recovery_authority": "",
            "chain_shares": chain_size,   # already populated (post-first-cycle)
            "chain_avg_price": 0.55,
            "chain_cost_basis_usd": 8.25,
            "chain_seen_at": stale_ts,    # stale → must be advanced on 2nd cycle
            "chain_absence_at": "",
        }
        conn.execute(
            f"""
            INSERT OR REPLACE INTO position_current ({", ".join(CANONICAL_POSITION_CURRENT_COLUMNS)})
            VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})
            """,
            ordered_values(payload, CANONICAL_POSITION_CURRENT_COLUMNS),
        )
        conn.commit()

        # Verify stale timestamp is seeded correctly.
        assert _read_persisted_chain_seen_at(db_path, trade_id) == stale_ts, (
            "precondition: stale chain_seen_at must be seeded before 2nd cycle"
        )

        # Arm Position with matching chain economics (no shares drift).
        pos.chain_shares = chain_size
        pos.chain_avg_price = 0.55
        pos.chain_cost_basis_usd = 8.25

        portfolio = PortfolioState(positions=[pos])
        chain = ChainPosition(
            token_id="tok-ts",
            size=chain_size,   # == shares → NO size mismatch
            avg_price=0.55,
            cost=8.25,
            condition_id="cond-1",
        )
        stats = reconcile(portfolio, [chain], conn=conn)
        conn.close()

        # Relationship assertion: chain_seen_at advanced past the stale
        # timestamp, persisted to disk and visible on a fresh connection.
        # Must be inside the with-block so tmpdir still exists.
        refreshed_ts = _read_persisted_chain_seen_at(db_path, trade_id)

    assert refreshed_ts is not None, (
        "chain_seen_at must be non-NULL after 2nd-cycle reconcile"
    )
    assert refreshed_ts > stale_ts, (
        f"chain_seen_at must advance on 2nd cycle when stale; "
        f"stale={stale_ts!r} refreshed={refreshed_ts!r}. "
        "Pre-fix: timestamp was frozen; classify_chain_state() would mis-classify "
        "this position as CHAIN_UNKNOWN on restart."
    )
    assert stats.get("chain_observation_persisted", 0) == 1, (
        f"2nd-cycle stale-timestamp refresh must count as chain_observation_persisted; "
        f"stats={stats}"
    )


def test_chain_seen_at_refresh_does_not_emit_immediate_duplicate_observation() -> None:
    """A stale chain_seen_at refresh must not become an event storm.

    The first reconcile after the timestamp goes stale is allowed to append a
    chain_economics_observed event to advance positive chain evidence. A
    second reconcile with unchanged chain economics and a fresh chain_seen_at
    must skip the append.
    """
    from datetime import datetime, timedelta, timezone

    from src.state.chain_reconciliation import _CHAIN_SEEN_AT_MAX_AGE_SECONDS

    chain_size = 1.0
    trade_id = "ts-refresh-no-immediate-duplicate"
    stale_ts = (
        datetime.now(timezone.utc)
        - timedelta(seconds=_CHAIN_SEEN_AT_MAX_AGE_SECONDS + 120)
    ).isoformat()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(trade_id=trade_id, token_id="tok-no-dupe", shares=chain_size)
        _seed_position_current(conn, pos, chain_shares=chain_size)
        conn.execute(
            """
            UPDATE position_current
               SET chain_seen_at = ?,
                   chain_state = 'synced',
                   chain_avg_price = 0.64,
                   chain_cost_basis_usd = 0.64
             WHERE position_id = ?
            """,
            (stale_ts, trade_id),
        )
        conn.commit()

        pos.chain_shares = chain_size
        pos.chain_avg_price = 0.64
        pos.chain_cost_basis_usd = 0.64
        chain = ChainPosition(
            token_id="tok-no-dupe",
            size=chain_size,
            avg_price=0.64,
            cost=0.64,
            condition_id="cond-no-dupe",
        )

        first_stats = reconcile(PortfolioState(positions=[pos]), [chain], conn=conn)
        first_seen_at = _read_persisted_chain_seen_at(db_path, trade_id)
        first_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'CHAIN_SIZE_CORRECTED'
               AND json_extract(payload_json, '$.reason') = 'chain_economics_observed'
            """,
            (trade_id,),
        ).fetchone()[0]

        second_stats = reconcile(PortfolioState(positions=[pos]), [chain], conn=conn)
        second_seen_at = _read_persisted_chain_seen_at(db_path, trade_id)
        second_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'CHAIN_SIZE_CORRECTED'
               AND json_extract(payload_json, '$.reason') = 'chain_economics_observed'
            """,
            (trade_id,),
        ).fetchone()[0]
        conn.close()

    assert first_stats.get("chain_observation_persisted", 0) == 1
    assert first_seen_at is not None and first_seen_at > stale_ts
    assert first_count == 1
    assert second_stats.get("chain_observation_persisted", 0) == 0
    assert second_seen_at == first_seen_at
    assert second_count == 1


# T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW):
# test_current_risk_quarantine_refreshes_chain_seen_without_resolving_quarantine
# and test_current_risk_quarantine_missing_from_nonempty_snapshot_is_not_voided
# retired along with src.state.chain_reconciliation._is_current_money_risk_quarantine
# and its consumer block. Both tests forced pos.state = "quarantined" via a
# direct POST-construction attribute assignment (bypassing Position.
# __post_init__'s mixed-epoch remap) to simulate a runtime state that no
# longer exists for any live Position — no writer mints state='quarantined'
# going forward, so the "current-risk quarantine" chain-observation-refresh
# path they exercised was already provably unreachable in production and has
# been removed. Real chain-risk exposure flows through the normal
# ACTIVE/DAY0_WINDOW/PENDING_EXIT chain-observation refresh path instead
# (see test_matched_no_size_mismatch_persists_chain_shares above).


def test_targeted_ctf_zero_removes_current_risk_without_inventing_close() -> None:
    """Current zero balance changes capital truth, not fill or lifecycle history."""
    trade_id = "pending-exit-targeted-zero"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)
        pos = _make_position(
            trade_id=trade_id,
            token_id="tok-targeted-zero",
            shares=53.0081,
        )
        pos.state = "pending_exit"
        pos.chain_shares = 53.0081
        _seed_position_current(
            conn,
            pos,
            chain_shares=53.0081,
            phase="pending_exit",
            exit_reason="missing_exit_attribution",
            exit_retry_count=3,
        )
        chain = ChainPosition(
            token_id=pos.token_id,
            size=0.0,
            avg_price=0.0,
            condition_id=pos.condition_id,
            balance_authority="CHAIN",
            balance_source="targeted_ctf_balance_allowance",
        )

        first_stats = reconcile(PortfolioState(positions=[pos]), [chain], conn=conn)
        first = conn.execute(
            """
            SELECT phase, chain_state, chain_shares, shares, exit_reason,
                   exit_retry_count, chain_absence_at
              FROM position_current
             WHERE position_id = ?
            """,
            (trade_id,),
        ).fetchone()
        first_events = conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'CHAIN_SIZE_CORRECTED'
               AND json_extract(payload_json, '$.reason') =
                   'targeted_ctf_balance_confirmed_zero'
            """,
            (trade_id,),
        ).fetchall()

        second_stats = reconcile(PortfolioState(positions=[pos]), [chain], conn=conn)
        second_event_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'CHAIN_SIZE_CORRECTED'
               AND json_extract(payload_json, '$.reason') =
                   'targeted_ctf_balance_confirmed_zero'
            """,
            (trade_id,),
        ).fetchone()[0]
        conn.close()

    assert first["phase"] == "pending_exit"
    assert first["chain_state"] == "chain_confirmed_zero"
    assert first["chain_shares"] == pytest.approx(0.0)
    assert first["shares"] == pytest.approx(53.0081)
    assert first["exit_reason"] == "missing_exit_attribution"
    assert first["exit_retry_count"] == 3
    assert first["chain_absence_at"]
    assert first_stats.get("chain_confirmed_zero_persisted", 0) == 1
    assert len(first_events) == 1
    payload = json.loads(first_events[0]["payload_json"])
    assert payload["evidence_source"] == "targeted_ctf_balance_allowance"
    assert payload["balance_authority"] == "CHAIN"
    assert second_stats.get("chain_confirmed_zero_persisted", 0) == 0
    assert second_event_count == 1


def test_blank_chain_seen_at_refresh_projects_observation_time_once() -> None:
    """A synced projection with blank chain_seen_at must converge in one write.

    Live drift shape: position_current can contain chain_state='synced' and
    positive chain_shares while chain_seen_at is blank. The refresh event must
    project its observed timestamp, or every reconcile cycle sees the row as
    stale and emits another chain_economics_observed event.
    """

    chain_size = 1.0
    trade_id = "blank-chain-seen-at-no-dupe"

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)

        pos = _make_position(trade_id=trade_id, token_id="tok-blank-seen", shares=chain_size)
        _seed_position_current(conn, pos, chain_shares=chain_size)
        conn.execute(
            """
            UPDATE position_current
               SET chain_seen_at = '',
                   chain_state = 'synced',
                   chain_avg_price = 0.64,
                   chain_cost_basis_usd = 0.64
             WHERE position_id = ?
            """,
            (trade_id,),
        )
        conn.commit()

        pos.chain_shares = chain_size
        pos.chain_avg_price = 0.64
        pos.chain_cost_basis_usd = 0.64
        chain = ChainPosition(
            token_id="tok-blank-seen",
            size=chain_size,
            avg_price=0.64,
            cost=0.64,
            condition_id="cond-blank-seen",
        )

        first_stats = reconcile(PortfolioState(positions=[pos]), [chain], conn=conn)
        first_seen_at = _read_persisted_chain_seen_at(db_path, trade_id)
        first_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'CHAIN_SIZE_CORRECTED'
               AND json_extract(payload_json, '$.reason') = 'chain_economics_observed'
            """,
            (trade_id,),
        ).fetchone()[0]

        second_stats = reconcile(PortfolioState(positions=[pos]), [chain], conn=conn)
        second_seen_at = _read_persisted_chain_seen_at(db_path, trade_id)
        second_count = conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'CHAIN_SIZE_CORRECTED'
               AND json_extract(payload_json, '$.reason') = 'chain_economics_observed'
            """,
            (trade_id,),
        ).fetchone()[0]
        conn.close()

    assert first_stats.get("chain_observation_persisted", 0) == 1
    assert first_seen_at
    assert first_count == 1
    assert second_stats.get("chain_observation_persisted", 0) == 0
    assert second_seen_at == first_seen_at
    assert second_count == 1


def test_stale_open_projection_cannot_erase_newer_positive_chain_observation() -> None:
    """A fill/recovery replay without chain evidence must be monotone.

    Live failure shape: chain reconciliation persisted a positive observation,
    then the periodic command-recovery lane replayed the original fill
    projection (which has no chain timestamp) and cleared ``chain_seen_at``.
    That made the global wealth witness fail closed until the next reconcile.
    """

    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.projection import upsert_position_current

    trade_id = "stale-replay-preserves-chain-observation"
    observed_at = "2026-07-11T09:36:52.738010+00:00"

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)
        pos = _make_position(trade_id=trade_id, token_id="tok-stale-replay", shares=3.0)
        _seed_position_current(conn, pos, chain_shares=3.0)
        conn.execute(
            """
            UPDATE position_current
               SET chain_state = 'synced',
                   chain_shares = 3.0,
                   chain_avg_price = 0.28,
                   chain_cost_basis_usd = 0.84,
                   chain_seen_at = ?,
                   chain_absence_at = NULL
             WHERE position_id = ?
            """,
            (observed_at, trade_id),
        )
        conn.commit()

        stale = build_position_current_projection(pos)
        stale["phase"] = "active"
        stale["chain_state"] = "local_only"
        stale["chain_shares"] = None
        stale["chain_avg_price"] = None
        stale["chain_cost_basis_usd"] = None
        stale["chain_seen_at"] = None
        stale["chain_absence_at"] = None
        upsert_position_current(conn, stale)
        conn.commit()

        row = conn.execute(
            """
            SELECT chain_state, chain_shares, chain_avg_price,
                   chain_cost_basis_usd, chain_seen_at, chain_absence_at
              FROM position_current
             WHERE position_id = ?
            """,
            (trade_id,),
        ).fetchone()
        conn.close()

    assert dict(row) == {
        "chain_state": "synced",
        "chain_shares": 3.0,
        "chain_avg_price": 0.28,
        "chain_cost_basis_usd": 0.84,
        "chain_seen_at": observed_at,
        "chain_absence_at": None,
    }


def test_new_chain_absence_can_supersede_positive_chain_observation() -> None:
    """Monotonicity protects missing evidence, not stale positive state."""

    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.projection import upsert_position_current

    trade_id = "new-absence-supersedes-positive"
    absence_at = "2026-07-11T09:40:00+00:00"

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "world.db")
        conn = _setup_db_on_disk(db_path)
        pos = _make_position(trade_id=trade_id, token_id="tok-new-absence", shares=3.0)
        _seed_position_current(conn, pos, chain_shares=3.0)
        conn.execute(
            """
            UPDATE position_current
               SET chain_state = 'synced', chain_shares = 3.0,
                   chain_seen_at = '2026-07-11T09:36:52+00:00',
                   chain_absence_at = NULL
             WHERE position_id = ?
            """,
            (trade_id,),
        )
        conn.commit()

        observed_absence = build_position_current_projection(pos)
        observed_absence["phase"] = "active"
        observed_absence["chain_state"] = "local_only"
        observed_absence["chain_shares"] = 0.0
        observed_absence["chain_seen_at"] = None
        observed_absence["chain_absence_at"] = absence_at
        upsert_position_current(conn, observed_absence)
        conn.commit()

        row = conn.execute(
            """
            SELECT chain_state, chain_shares, chain_seen_at, chain_absence_at
              FROM position_current WHERE position_id = ?
            """,
            (trade_id,),
        ).fetchone()
        conn.close()

    assert dict(row) == {
        "chain_state": "local_only",
        "chain_shares": 0.0,
        "chain_seen_at": None,
        "chain_absence_at": absence_at,
    }
