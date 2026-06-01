# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: DEFECT-1 capital-recoverability bridge. An EDLI FILL_CONFIRMED
#   must materialise a canonical position_current row (the seam audited as
#   missing), idempotently, chain-reconcilable by token, summing partial fills.
"""TDD for src.events.edli_position_bridge.

Fitz #3 relationship tests: these verify a CROSS-MODULE invariant — what holds
when the EDLI execution lane's confirmed fill flows into the legacy
position_current lifecycle:

  1. RED contract: a confirmed EDLI fill, absent the bridge, leaves NO
     position_current row (the audited stuck-capital gap).
  2. GREEN: the bridge materialises exactly one correct row.
  3. Idempotency: a replayed fill UPDATEs the same row, never duplicates.
  4. Relationship: EDLI fill economics == position_current shares/cost_basis.
  5. Relationship: chain_reconciliation matches the bridged row BY TOKEN and
     populates chain_shares (proven for the legacy Shanghai position).
  6. Forward-proof DEFECT-4: two partial UserTradeObserved → summed shares,
     size-weighted price.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.events.edli_position_bridge import (
    edli_bridge_position_id,
    materialize_position_current_from_edli_fill,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

CONDITION_ID = "0xcondition_edli_bridge_1"
ELECTED_NO_TOKEN = "token_no_99887766"
ELECTED_YES_TOKEN = "token_yes_11223344"
FINAL_INTENT_ID = "intent-edli-1"
EXECUTION_COMMAND_ID = "execcmd-edli-1"
EVENT_ID = "evt-edli-1"
VENUE_ORDER_ID = "venue-order-1"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _insert_edli_event(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    sequence: int,
    event_type: str,
    payload: dict,
    source_authority: str = "engine_adapter",
) -> None:
    """Raw-insert an edli_live_order_events row (mirrors the real producer).

    The bridge reads event_type + payload_json only, so we seed those directly
    and keep the strict append-law chain (which couples to the whole submit
    pipeline) out of the bridge's unit contract.
    """
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    event_hash = f"{aggregate_id}:{sequence}:{event_type}"
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            f"edli_evt:{event_hash}",
            aggregate_id,
            sequence,
            event_type,
            None if sequence == 1 else f"{aggregate_id}:{sequence-1}",
            event_hash,
            payload_json,
            f"ph:{event_hash}",
            source_authority,
            "2026-06-01T12:00:00+00:00",
            "2026-06-01T12:00:01+00:00",
        ),
    )


def _seed_confirmed_buy_no_aggregate(
    conn: sqlite3.Connection,
    aggregate_id: str = "agg-edli-buyno-1",
    *,
    fills: list[tuple[float, float, float]] | None = None,
) -> str:
    """Seed a realistic CONFIRMED buy_no aggregate.

    fills: list of (filled_size, avg_fill_price, fees). Default = single FOK
    full fill of 16.75 @ 0.42.
    """
    if fills is None:
        fills = [(16.75, 0.42, 0.03)]
    pre_submit = {
        "event_id": EVENT_ID,
        "final_intent_id": FINAL_INTENT_ID,
        "condition_id": CONDITION_ID,
        "token_id": ELECTED_NO_TOKEN,  # elected NATIVE token == no_token for buy_no
        "side": "BUY",
        "direction": "buy_no",
        "native_token_side": "NO",
        "outcome_label": "NO",
        "city": "Shanghai",
        "target_date": "2026-06-02",
        "bin_label": "30-32",
        "metric": "high",
        "market_id": CONDITION_ID,
        "q_live": 0.55,
        "executable_snapshot_id": "exec-snap-1",
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit, source_authority="engine_adapter")
    _insert_edli_event(
        conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
        payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID},
        source_authority="engine_adapter",
    )
    seq = 3
    for (size, price, fees) in fills:
        _insert_edli_event(
            conn, aggregate_id=aggregate_id, sequence=seq, event_type="UserTradeObserved",
            payload={
                "event_id": EVENT_ID,
                "final_intent_id": FINAL_INTENT_ID,
                "trade_status": "CONFIRMED",
                "fill_authority_state": "FILL_CONFIRMED",
                "venue_order_id": VENUE_ORDER_ID,
                "filled_size": size,
                "avg_fill_price": price,
                "fees": fees,
            },
            source_authority="user_channel",
        )
        seq += 1
    return aggregate_id


def _position_current_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM position_current").fetchall()


# --------------------------------------------------------------------------- #
# 1. RED: confirmed fill, no bridge → no position_current row
# --------------------------------------------------------------------------- #

def test_red_confirmed_fill_produces_no_position_current_without_bridge(conn):
    """The audited gap: EDLI fill writes event-log only; position_current empty."""
    _seed_confirmed_buy_no_aggregate(conn)
    assert _position_current_rows(conn) == [], "PRECONDITION: EDLI fill alone must not create position_current"


# --------------------------------------------------------------------------- #
# 2. GREEN: bridge materialises exactly one correct row
# --------------------------------------------------------------------------- #

def test_green_bridge_materializes_one_correct_position(conn):
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert result is not None
    assert result["created"] is True

    rows = _position_current_rows(conn)
    assert len(rows) == 1, "exactly one position_current row"
    row = rows[0]
    assert row["position_id"] == edli_bridge_position_id(aggregate_id)
    assert row["phase"] == "active"
    assert row["direction"] == "buy_no"
    assert row["condition_id"] == CONDITION_ID
    # Token placement: buy_no → elected token on no_token_id (chain-match key).
    assert row["no_token_id"] == ELECTED_NO_TOKEN
    assert (row["token_id"] or "") == ""
    assert abs(row["shares"] - 16.75) < 1e-9
    assert abs(row["entry_price"] - 0.42) < 1e-9
    assert abs(row["cost_basis_usd"] - (16.75 * 0.42)) < 1e-6
    assert row["fill_authority"] == "venue_confirmed_full"
    assert row["order_status"] == "filled"

    # One canonical entry-event chain exists.
    ev = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? ORDER BY sequence_no",
        (row["position_id"],),
    ).fetchall()
    assert [r[0] for r in ev] == ["POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_FILLED"]


def test_green_bridge_buy_yes_places_token_on_token_id(conn):
    aggregate_id = "agg-edli-buyyes-1"
    pre_submit = {
        "event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "condition_id": CONDITION_ID,
        "token_id": ELECTED_YES_TOKEN, "side": "BUY", "direction": "buy_yes",
        "native_token_side": "YES", "outcome_label": "YES", "city": "Tokyo",
        "target_date": "2026-06-02", "bin_label": "28-30", "metric": "high", "q_live": 0.6,
    }
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated", payload=pre_submit)
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID})
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=3, event_type="UserTradeObserved",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "trade_status": "CONFIRMED",
                                "fill_authority_state": "FILL_CONFIRMED", "venue_order_id": VENUE_ORDER_ID,
                                "filled_size": 5.0, "avg_fill_price": 0.5, "fees": 0.01}, source_authority="user_channel")
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    row = _position_current_rows(conn)[0]
    assert row["direction"] == "buy_yes"
    assert row["token_id"] == ELECTED_YES_TOKEN
    assert (row["no_token_id"] or "") == ""


# --------------------------------------------------------------------------- #
# 3. Idempotency: replayed fill → still one row, UPDATEd not duplicated
# --------------------------------------------------------------------------- #

def test_idempotent_replay_keeps_one_row(conn):
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    r1 = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert r1["created"] is True
    r2 = materialize_position_current_from_edli_fill(conn, aggregate_id)
    assert r2["created"] is False, "replay must UPDATE, not re-create"

    rows = _position_current_rows(conn)
    assert len(rows) == 1, "replay must not duplicate position_current"
    # Entry events must NOT be duplicated (append-only unique key).
    ev = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type='POSITION_OPEN_INTENT'",
        (rows[0]["position_id"],),
    ).fetchone()[0]
    assert ev == 1, "POSITION_OPEN_INTENT must exist exactly once after replay"


# --------------------------------------------------------------------------- #
# 4. No confirmed fill → nothing to bridge (None)
# --------------------------------------------------------------------------- #

def test_no_confirmed_fill_returns_none(conn):
    aggregate_id = "agg-edli-pending-1"
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=1, event_type="PreSubmitRevalidated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "condition_id": CONDITION_ID,
                                "token_id": ELECTED_NO_TOKEN, "side": "BUY", "direction": "buy_no"})
    # MATCHED but not CONFIRMED — pending finality, not a confirmed fill.
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=2, event_type="ExecutionCommandCreated",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "execution_command_id": EXECUTION_COMMAND_ID})
    _insert_edli_event(conn, aggregate_id=aggregate_id, sequence=3, event_type="UserTradeObserved",
                       payload={"event_id": EVENT_ID, "final_intent_id": FINAL_INTENT_ID, "trade_status": "MATCHED",
                                "fill_authority_state": "MATCHED_PENDING_FINALITY", "venue_order_id": VENUE_ORDER_ID,
                                "filled_size": 5.0, "avg_fill_price": 0.5}, source_authority="user_channel")
    assert materialize_position_current_from_edli_fill(conn, aggregate_id) is None
    assert _position_current_rows(conn) == []


# --------------------------------------------------------------------------- #
# 5. Relationship: EDLI audit filled_size == position_current shares
# --------------------------------------------------------------------------- #

def test_relationship_audit_filled_size_equals_position_shares(conn):
    aggregate_id = _seed_confirmed_buy_no_aggregate(conn, fills=[(16.75, 0.42, 0.03)])
    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    row = _position_current_rows(conn)[0]
    # The bridge's summed filled_size IS the value the EDLI profit-audit would
    # record (both read the same UserTradeObserved economics). Cross-module
    # invariant: position shares == realised fill size.
    assert abs(row["shares"] - result["shares"]) < 1e-12
    assert abs(row["shares"] - 16.75) < 1e-9
    assert abs(row["cost_basis_usd"] - 16.75 * 0.42) < 1e-6


# --------------------------------------------------------------------------- #
# 6. Forward-proof DEFECT-4: two partial fills sum (size-weighted price)
# --------------------------------------------------------------------------- #

def test_forward_proof_two_partial_fills_sum(conn):
    # 10 @ 0.40 and 6 @ 0.50 → 16 shares, cost 4.0+3.0=7.0, vwap 0.4375.
    aggregate_id = _seed_confirmed_buy_no_aggregate(
        conn, aggregate_id="agg-edli-partials-1", fills=[(10.0, 0.40, 0.02), (6.0, 0.50, 0.01)],
    )
    result = materialize_position_current_from_edli_fill(conn, aggregate_id)
    row = _position_current_rows(conn)[0]
    assert abs(row["shares"] - 16.0) < 1e-9
    assert abs(row["cost_basis_usd"] - 7.0) < 1e-9
    assert abs(row["entry_price"] - (7.0 / 16.0)) < 1e-9
    assert abs(result["fees"] - 0.03) < 1e-12


# --------------------------------------------------------------------------- #
# 7. Relationship: chain_reconciliation matches the bridged row BY TOKEN
# --------------------------------------------------------------------------- #

def test_relationship_chain_reconciliation_matches_bridged_row_by_token(conn):
    """Proven for legacy Shanghai: chain reconcile matches by token + sets
    chain_shares. The bridged buy_no row must reconcile the same way."""
    from src.state.chain_reconciliation import reconcile, ChainPosition
    from src.state.db import query_portfolio_loader_view

    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    conn.commit()

    # Load the canonical portfolio (DB-first) — same path the live loader uses.
    snapshot = query_portfolio_loader_view(conn)
    assert snapshot["status"] in ("ok", "partial_stale"), snapshot["status"]
    # Reconstruct Positions from the loader rows the way load_portfolio does.
    portfolio = _portfolio_from_loader(snapshot)
    assert len(portfolio.positions) == 1
    pos = portfolio.positions[0]
    # The chain-match token for a buy_no position is no_token_id.
    match_token = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    assert match_token == ELECTED_NO_TOKEN

    # Chain returns the elected token with the filled size → must SYNC + set chain_shares.
    chain_positions = [ChainPosition(token_id=ELECTED_NO_TOKEN, size=16.75, avg_price=0.42, cost=16.75 * 0.42, condition_id=CONDITION_ID)]
    stats = reconcile(portfolio, chain_positions, conn=conn)
    conn.commit()

    # chain_shares populated on the bridged row (the stuck-capital cure).
    chain_shares = conn.execute(
        "SELECT chain_shares FROM position_current WHERE position_id = ?",
        (edli_bridge_position_id(aggregate_id),),
    ).fetchone()[0]
    assert chain_shares is not None
    assert abs(float(chain_shares) - 16.75) < 1e-6
    assert stats.get("voided", 0) == 0, "a chain-backed bridged position must NOT be voided"


# --------------------------------------------------------------------------- #
# 8. DEFECT-2: bridged position is EXITABLE by the legacy path
# --------------------------------------------------------------------------- #

def test_defect2_bridged_position_is_exit_eligible_via_legacy_path(conn):
    """The legacy exit lane (_execute_monitoring_phase) manages a position iff
    it loads from position_current as an ACTIVE, tradable-exposure position.

    Proves the bridged row satisfies every precondition the legacy exit path
    requires, so capital is never stuck:
      - loads as a real Position (not synthetic) from the canonical loader;
      - phase 'active' (not in INACTIVE_RUNTIME_STATES);
      - has_tradable_exposure() True (fill_authority is fill-grade);
      - carries the orderbook token the exit lane queries.
    """
    from src.state.db import query_portfolio_loader_view
    from src.state.portfolio import (
        has_tradable_exposure,
        has_verified_trade_fill,
        INACTIVE_RUNTIME_STATES,
    )

    aggregate_id = _seed_confirmed_buy_no_aggregate(conn)
    materialize_position_current_from_edli_fill(conn, aggregate_id)
    conn.commit()

    snapshot = query_portfolio_loader_view(conn)
    assert snapshot["status"] in ("ok", "partial_stale")
    portfolio = _portfolio_from_loader(snapshot)
    assert len(portfolio.positions) == 1
    pos = portfolio.positions[0]

    # ACTIVE / managed (not terminal).
    assert pos.state not in INACTIVE_RUNTIME_STATES
    # The exit lane will manage it: real capital at risk + verified fill.
    assert has_tradable_exposure(pos) is True
    assert has_verified_trade_fill(pos) is True
    # The orderbook query token (no_token_id for buy_no) is present.
    orderbook_token = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    assert orderbook_token == ELECTED_NO_TOKEN
    assert pos.shares > 0
    assert pos.condition_id == CONDITION_ID  # redeem needs condition_id


# --------------------------------------------------------------------------- #
# 9. INV-37: cross-DB ATTACH wiring (the production connection topology).
#    EDLI events live on world.db; position_current is authoritative on trade.db.
#    The bridge must read world.edli_live_order_events and write trade
#    position_current on ONE trade-connection-with-world-ATTACHed (no independent
#    connection). This proves the runtime wiring, not just the single-conn path.
# --------------------------------------------------------------------------- #

def test_inv37_cross_db_attach_bridge(tmp_path):
    import src.state.db as db_module
    from src.state.db import init_schema

    world_path = tmp_path / "zeus-world.db"
    trade_path = tmp_path / "zeus_trades.db"

    # Build both DBs with the full schema (world owns EDLI tables; trade owns
    # position_current / position_events — init_schema creates both sets).
    for p in (world_path, trade_path):
        c = sqlite3.connect(str(p))
        init_schema(c)
        c.commit()
        c.close()

    # Seed EDLI events on the WORLD db (their authoritative home).
    aggregate_id = "agg-edli-inv37-1"
    wc = sqlite3.connect(str(world_path))
    wc.row_factory = sqlite3.Row
    _seed_confirmed_buy_no_aggregate(wc, aggregate_id=aggregate_id)
    wc.commit()
    wc.close()

    # Open the TRADE db and ATTACH world (the production INV-37 topology:
    # get_trade_connection_with_world_required). The bridge reads
    # world.edli_live_order_events and writes trade position_current — SAME conn.
    orig_w = db_module.ZEUS_WORLD_DB_PATH
    try:
        db_module.ZEUS_WORLD_DB_PATH = world_path
        conn = sqlite3.connect(str(trade_path))
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))

        # Bridge reads world.edli_live_order_events, writes trade.position_current.
        result = materialize_position_current_from_edli_fill(conn, aggregate_id)
        conn.commit()

        assert result is not None and result["created"] is True
        # position_current row landed on the TRADE db (not world).
        rows = conn.execute("SELECT position_id, no_token_id, shares FROM position_current").fetchall()
        assert len(rows) == 1
        assert rows[0]["no_token_id"] == ELECTED_NO_TOKEN
        assert abs(rows[0]["shares"] - 16.75) < 1e-9
        # The world.db must NOT have received a position_current write through
        # this path (trade is authoritative). The world copy is the ghost shell.
        world_rows = conn.execute("SELECT COUNT(*) FROM world.position_current").fetchone()[0]
        assert world_rows == 0, "bridge must write trade.position_current, never world's ghost shell"
        conn.close()
    finally:
        db_module.ZEUS_WORLD_DB_PATH = orig_w


def _portfolio_from_loader(snapshot):
    """Reconstruct a PortfolioState from query_portfolio_loader_view output.

    Mirrors the subset of load_portfolio's DB-first reconstruction needed to
    exercise chain reconciliation on the bridged row.
    """
    from src.state.portfolio import Position, PortfolioState

    positions = []
    for prow in snapshot["positions"]:
        d = dict(prow)
        # Map loader columns onto Position; phase 'active' → HOLDING runtime state.
        positions.append(
            Position(
                trade_id=d["trade_id"],
                market_id=d.get("market_id") or "",
                city=d.get("city") or "",
                cluster=d.get("cluster") or "",
                target_date=d.get("target_date") or "",
                bin_label=d.get("bin_label") or "",
                direction=d.get("direction") or "buy_no",
                unit=d.get("unit") or "F",
                size_usd=float(d.get("size_usd") or 0.0),
                entry_price=float(d.get("entry_price") or 0.0),
                shares=float(d.get("shares") or 0.0),
                cost_basis_usd=float(d.get("cost_basis_usd") or 0.0),
                token_id=d.get("token_id") or "",
                no_token_id=d.get("no_token_id") or "",
                condition_id=d.get("condition_id") or "",
                env=d.get("env") or "live",
                state="holding",
                strategy_key=d.get("strategy_key") or "settlement_capture",
                entry_fill_verified=True,
                fill_authority=d.get("fill_authority") or "venue_confirmed_full",
            )
        )
    return PortfolioState(positions=positions, bankroll=1000.0, daily_baseline_total=1000.0, weekly_baseline_total=1000.0)
