# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: boot crash-loop incident 2026-06-16 (live-trading daemon
#   bootout). docs/evidence/settlement_guard/boot_resting_absorbed_2026-06-16.md
"""Boot resting/absorbed resolution: the two cases neither absence nor presence
can clear.

CASE A — a SUBMITTED-AND-LIVE-RESTING funder-owned open order (no fill) ->
reconcile + cap CONSUMED, order left live.
CASE B — a CONFIRMED funder-owned fill that filled MORE than ordered AND is
ALREADY a non-EDLI-keyed position_current row -> ledger-only reconcile (NO new
position) + cap CONSUMED.

RED-on-revert guards: a funder-owned fill with NO existing position must NOT be
absorbed by this resolver (it defers to presence — never invents absorption); a
foreign-wallet order/trade on the shared wallet must NEVER qualify.
"""
from __future__ import annotations

import sqlite3

import pytest

import src.execution.edli_resting_absorbed_resolver as rr

FUNDER = "0x6a096d5042cba434521e2cdb95a1fba789a09b7f"
OUR_TOKEN = "35015396764119764057109967922516391182815114821189461579432074152958132060729"
OUR_ORDER_ID = "0xa323fadfa30e055ed3b9498512364e7bd1e625c08cdeb54d5fd292d7a4374730"


def _confirmed_taker_trade(*, size="19.5", price="0.58"):
    """The AGG2 incident shape: we are the TAKER on our token (asset_id ==
    our token, top-level maker_address == funder), order id == taker_order_id."""
    return {
        "id": "e5fdaf9d",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "asset_id": OUR_TOKEN,
        "maker_address": FUNDER,
        "price": price,
        "size": size,
        "taker_order_id": OUR_ORDER_ID,
        "maker_orders": [
            {
                "order_id": "0xcounterparty",
                "asset_id": "62438666644722416806931564458671160978861026574235722073650672557737403040011",
                "maker_address": "0xec62d23eff1a957d02293c5475d4b7a52f8d9191",
                "price": "0.42",
                "matched_amount": size,
            }
        ],
    }


def _live_resting_order(*, price="0.67", size="10.86"):
    """The live-resting shape: funder-owned LIVE open order, no fill yet."""
    return {
        "id": "0x250ca6e0restingorder",
        "status": "LIVE",
        "side": "BUY",
        "asset_id": OUR_TOKEN,
        "maker_address": FUNDER,
        "owner": "ae7780b0-ef05-c65c-97b8-47b9bad2f613",
        "price": price,
        "original_size": size,
        "size_matched": "0",
        "associate_trades": [],
    }


def _trade_db_with_position(**overrides):
    """In-memory zeus_trades.db with one position_current row proving prior
    absorption of the AGG2 fill (order_id == the trade's taker_order_id)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY, token_id TEXT, no_token_id TEXT,
            direction TEXT, shares REAL, entry_price REAL, phase TEXT,
            fill_authority TEXT, order_id TEXT, condition_id TEXT
        )
        """
    )
    row = {
        "position_id": "edlic02aab_existing",
        "token_id": None,
        "no_token_id": OUR_TOKEN,
        "direction": "buy_no",
        "shares": 19.5,
        "entry_price": 0.58,
        "phase": "active",
        "fill_authority": "venue_confirmed_full",
        "order_id": OUR_ORDER_ID,
        "condition_id": "0xcond",
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO position_current (position_id, token_id, no_token_id, direction, "
        "shares, entry_price, phase, fill_authority, order_id, condition_id) "
        "VALUES (:position_id,:token_id,:no_token_id,:direction,:shares,:entry_price,"
        ":phase,:fill_authority,:order_id,:condition_id)",
        row,
    )
    conn.commit()
    return conn


def _empty_trade_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY, token_id TEXT, no_token_id TEXT,
            direction TEXT, shares REAL, entry_price REAL, phase TEXT,
            fill_authority TEXT, order_id TEXT, condition_id TEXT
        )
        """
    )
    conn.commit()
    return conn


def _patch_plan(monkeypatch, *, size=18.5, price=0.58, direction="buy_no", token=OUR_TOKEN):
    def _lp(conn, agg, et):
        if et == "SubmitUnknown":
            return {"venue_call_started": True, "execution_command_id": "cmd1"}
        if et == "SubmitPlanBuilt":
            return {
                "token_id": token,
                "condition_id": "0xcond",
                "direction": direction,
                "limit_price": price,
                "size": size,
                "event_id": "ev1",
                "final_intent_id": "fi1",
            }
        return {}

    monkeypatch.setattr(rr, "_latest_payload", _lp)


# ---- CASE B: already-absorbed fill ---------------------------------------


def test_case_b_absorbed_fill_resolves_when_position_exists(monkeypatch):
    """A funder-owned CONFIRMED over-fill (19.5 vs 18.5) ALREADY absorbed into a
    position -> CASE B: ledger-only reconcile, cap CONSUMED, no new fill event."""
    _patch_plan(monkeypatch)
    monkeypatch.setattr(rr, "get_trade_connection_read_only", _trade_db_with_position)
    proof = rr.build_resolution(
        None, "agg", open_orders=[], trades=[_confirmed_taker_trade()], funder_address=FUNDER
    )
    assert proof is not None
    assert proof["case"] == "CONFIRMED_FILL_ALREADY_ABSORBED"
    assert proof["cap_transition"] == "CONSUMED"
    assert proof["venue_trade_exists"] is True
    assert proof["absorbed_position"]["position_id"] == "edlic02aab_existing"
    assert proof["matched_trade_ids"] == ["e5fdaf9d"]


def test_case_b_refuses_without_existing_position(monkeypatch):
    """RED-on-revert: a funder-owned fill with NO existing position is NOT this
    resolver's case (defers to presence — never invents absorption)."""
    _patch_plan(monkeypatch)
    monkeypatch.setattr(rr, "get_trade_connection_read_only", _empty_trade_db)
    proof = rr.build_resolution(
        None, "agg", open_orders=[], trades=[_confirmed_taker_trade()], funder_address=FUNDER
    )
    assert proof is None


def test_case_b_refuses_foreign_wallet_trade(monkeypatch):
    """Shared-wallet antibody: a CONFIRMED trade on our token but a FOREIGN
    wallet never qualifies, even if a (foreign) position happened to exist."""
    _patch_plan(monkeypatch)
    monkeypatch.setattr(rr, "get_trade_connection_read_only", _trade_db_with_position)
    t = _confirmed_taker_trade()
    t["maker_address"] = "0xforeignwallet"
    proof = rr.build_resolution(
        None, "agg", open_orders=[], trades=[t], funder_address=FUNDER
    )
    assert proof is None


def test_case_b_refuses_voided_position(monkeypatch):
    """A voided/quarantined position is NOT proof of a live absorbed fill."""
    _patch_plan(monkeypatch)
    monkeypatch.setattr(
        rr, "get_trade_connection_read_only", lambda: _trade_db_with_position(phase="voided")
    )
    proof = rr.build_resolution(
        None, "agg", open_orders=[], trades=[_confirmed_taker_trade()], funder_address=FUNDER
    )
    assert proof is None


# ---- CASE A: live-resting order ------------------------------------------


def test_case_a_live_resting_order_resolves(monkeypatch):
    """A funder-owned LIVE open order matching economics, with NO confirmed fill
    -> CASE A: reconcile + cap CONSUMED, order untouched."""
    _patch_plan(monkeypatch, size=10.865, price=0.67)
    monkeypatch.setattr(rr, "get_trade_connection_read_only", _empty_trade_db)
    proof = rr.build_resolution(
        None, "agg", open_orders=[_live_resting_order()], trades=[], funder_address=FUNDER
    )
    assert proof is not None
    assert proof["case"] == "SUBMITTED_AND_LIVE_RESTING"
    assert proof["cap_transition"] == "CONSUMED"
    assert proof["venue_order_exists"] is True
    assert proof["live_order"]["venue_order_id"] == "0x250ca6e0restingorder"


def test_case_a_refuses_foreign_wallet_order(monkeypatch):
    """Shared-wallet antibody: a LIVE order on our token but a FOREIGN wallet
    never qualifies as a resting case."""
    _patch_plan(monkeypatch, size=10.865, price=0.67)
    monkeypatch.setattr(rr, "get_trade_connection_read_only", _empty_trade_db)
    o = _live_resting_order()
    o["maker_address"] = "0xforeignwallet"
    proof = rr.build_resolution(
        None, "agg", open_orders=[o], trades=[], funder_address=FUNDER
    )
    assert proof is None


def test_case_a_refuses_economics_mismatch(monkeypatch):
    """A funder-owned LIVE order whose size/price does NOT match the aggregate is
    a DIFFERENT order (foreign intent on the same token) -> never matched."""
    _patch_plan(monkeypatch, size=10.865, price=0.67)
    monkeypatch.setattr(rr, "get_trade_connection_read_only", _empty_trade_db)
    o = _live_resting_order(size="50.0")  # way off our 10.865
    proof = rr.build_resolution(
        None, "agg", open_orders=[o], trades=[], funder_address=FUNDER
    )
    assert proof is None


def test_neither_case_returns_none(monkeypatch):
    """No funder fill and no funder resting order -> fail-closed preserved."""
    _patch_plan(monkeypatch)
    monkeypatch.setattr(rr, "get_trade_connection_read_only", _empty_trade_db)
    proof = rr.build_resolution(
        None, "agg", open_orders=[], trades=[], funder_address=FUNDER
    )
    assert proof is None
