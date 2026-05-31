# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: task #56 chain-shares-persist — matched-no-mismatch path
#   issued no canonical write, leaving position_current.chain_shares NULL for
#   every synced position (local DB diverges from on-chain reality).
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
) -> None:
    """Seed an ACTIVE position_current row with the given chain_shares (None = NULL).

    Mirrors a daemon that opened/entered the position but has never persisted
    a chain observation (chain_shares NULL) — the exact pre-fix on-disk state.
    """
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, ordered_values

    payload = {
        "position_id": pos.trade_id,
        "phase": "active",
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
        "chain_shares": chain_shares,  # None → persisted NULL (pre-fix on-disk state)
        "chain_avg_price": None,
        "chain_cost_basis_usd": None,
        "chain_seen_at": "",
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
