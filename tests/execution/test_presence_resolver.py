# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: docs/evidence/settlement_guard/boot_presence_reconcile_2026-06-16.md
#   + presence_resolver_review_2026-06-16.md (the double-count guard).
"""Boot presence-resolution: attribution + double-count antibody + fail-closed.

The #122 db-lock orphan (a FILLED maker order stuck at SubmitUnknown) must
reconcile to EXACTLY its own leg (5.07 @ 0.64), attribute ONLY our funder's leg
on our token (shared-wallet antibody — operator co-trades on the same wallet),
REFUSE on a genuine absence, and REFUSE rather than inflate if attribution ever
over-counts.
"""
from __future__ import annotations

import pytest

import src.execution.edli_presence_resolver as pr

FUNDER = "0x6a096d5042cba434521e2cdb95a1fba789a09b7f"
OUR_TOKEN = "94919691709339926248609367448320440419051501993103034121677455440943187656517"
NO_ASSET = "49128580161441347983725531208820142357219356580732147594666453507088262789094"


def _our_maker_trade():
    """The real incident shape: trader_side=MAKER, our leg in maker_orders[],
    top-level fields are the counterparty/taker perspective."""
    return {
        "id": "cf967209",
        "status": "CONFIRMED",
        "trader_side": "MAKER",
        "asset_id": NO_ASSET,
        "maker_address": "0x0a0c42f6counterpartywallet",
        "price": "0.36",
        "size": "5.07",
        "taker_order_id": "0x189f74counterparty",
        "maker_orders": [
            {
                "order_id": "0x5ce1f9our",
                "asset_id": OUR_TOKEN,
                "maker_address": FUNDER,
                "price": "0.64",
                "matched_amount": "5.07",
                "fee_rate_bps": "",
            }
        ],
    }


def test_our_fill_legs_attributes_only_our_maker_leg():
    legs = pr._our_fill_legs(_our_maker_trade(), OUR_TOKEN, FUNDER)
    assert len(legs) == 1
    assert legs[0]["role"] == "MAKER"
    assert legs[0]["price"] == 0.64
    assert legs[0]["size"] == 5.07
    assert legs[0]["venue_order_id"] == "0x5ce1f9our"


def test_our_fill_legs_rejects_foreign_wallet_same_token():
    """Operator co-trades the SAME wallet on other markets: a leg on our token
    but a different wallet is NOT ours."""
    t = _our_maker_trade()
    t["maker_orders"][0]["maker_address"] = "0xforeignwallet"
    assert pr._our_fill_legs(t, OUR_TOKEN, FUNDER) == []


def test_our_fill_legs_rejects_wrong_token():
    assert pr._our_fill_legs(_our_maker_trade(), "0xnotourtoken", FUNDER) == []


def test_our_fill_legs_rejects_unconfirmed():
    t = _our_maker_trade()
    t["status"] = "MATCHED"
    assert pr._our_fill_legs(t, OUR_TOKEN, FUNDER) == []


def test_our_fill_legs_taker_branch_attributes_our_taker_order():
    """If we were the TAKER: top-level asset == our token and top-level
    maker_address (the aggressor wallet) == our funder; our id is taker_order_id."""
    t = {
        "id": "tk1",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "asset_id": OUR_TOKEN,
        "maker_address": FUNDER,
        "price": "0.64",
        "size": "5.0",
        "taker_order_id": "0xourtaker",
        "maker_orders": [
            {
                "order_id": "0xcpmaker",
                "asset_id": NO_ASSET,
                "maker_address": "0xcp",
                "price": "0.36",
                "matched_amount": "5.0",
            }
        ],
    }
    legs = pr._our_fill_legs(t, OUR_TOKEN, FUNDER)
    assert len(legs) == 1
    assert legs[0]["role"] == "TAKER"
    assert legs[0]["venue_order_id"] == "0xourtaker"


def _patch_plan(monkeypatch, size=5.078125):
    def _lp(conn, agg, et):
        if et == "SubmitUnknown":
            return {"venue_call_started": True, "execution_command_id": "cmd1"}
        if et == "SubmitPlanBuilt":
            return {
                "token_id": OUR_TOKEN,
                "condition_id": "0xcond",
                "direction": "buy_no",
                "size": size,
                "event_id": "ev1",
                "final_intent_id": "fi1",
            }
        return {}

    monkeypatch.setattr(pr, "_latest_payload", _lp)


def test_build_presence_proof_happy_path(monkeypatch):
    _patch_plan(monkeypatch)
    proof = pr.build_presence_proof(
        None, "agg", trades=[_our_maker_trade()], funder_address=FUNDER
    )
    assert proof["filled_size"] == 5.07
    assert proof["avg_fill_price"] == 0.64
    assert proof["venue_order_id"] == "0x5ce1f9our"
    assert proof["venue_command_state"] == "PARTIAL"  # 5.07 filled < 5.078125 ordered
    assert proof["matched_trade_ids"] == ["cf967209"]


def test_build_presence_proof_refuses_genuine_absence(monkeypatch):
    """No CONFIRMED trade owned by our funder on our token -> presence REFUSES
    (so the absence path / fail-closed handles it; never a fabricated fill)."""
    _patch_plan(monkeypatch)
    t = _our_maker_trade()
    t["maker_orders"][0]["maker_address"] = "0xforeign"
    with pytest.raises(RuntimeError, match="not a presence"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_double_count_guard_refuses(monkeypatch):
    """If two DISTINCT legs (different order ids) of ours qualify and sum past the
    order size, REFUSE rather than record an inflated position (the reviewer's
    feared mis-attribution class)."""
    _patch_plan(monkeypatch, size=5.078125)
    t = _our_maker_trade()
    t["maker_orders"].append(
        {
            "order_id": "0xsecond",
            "asset_id": OUR_TOKEN,
            "maker_address": FUNDER,
            "price": "0.64",
            "matched_amount": "5.07",
        }
    )
    with pytest.raises(RuntimeError, match="exceed order size"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)
