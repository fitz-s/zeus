# Created: 2026-06-16
# Last audited: 2026-07-23
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

import sqlite3
from datetime import datetime, timezone

import pytest

import src.execution.edli_presence_resolver as pr

FUNDER = "0x6a096d5042cba434521e2cdb95a1fba789a09b7f"
OUR_TOKEN = "94919691709339926248609367448320440419051501993103034121677455440943187656517"
NO_ASSET = "49128580161441347983725531208820142357219356580732147594666453507088262789094"
OUR_ORDER = "0x5ce1f9our"


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
        "match_time": "1002",
        "taker_order_id": "0x189f74counterparty",
        "maker_orders": [
            {
                "order_id": OUR_ORDER,
                "asset_id": OUR_TOKEN,
                "maker_address": FUNDER,
                "side": "BUY",
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


def _patch_plan(monkeypatch, size=5.078125, limit_price=0.65, order_type="GTC"):
    def _lp(conn, agg, et):
        if et == "SubmitUnknown":
            return {"venue_call_started": True, "execution_command_id": "cmd1"}
        if et == "SubmitPlanBuilt":
            return {
                "token_id": OUR_TOKEN,
                "condition_id": "0xcond",
                "direction": "buy_no",
                "size": size,
                "limit_price": limit_price,
                "order_type": order_type,
                "event_id": "ev1",
                "final_intent_id": "fi1",
            }
        return {}

    monkeypatch.setattr(pr, "_latest_payload", _lp)
    monkeypatch.setattr(
        pr,
        "_latest_event_time",
        lambda conn, agg, et: datetime.fromtimestamp(
            1000 if et == "VenueSubmitAttempted" else 1005,
            tz=timezone.utc,
        ),
    )
    monkeypatch.setattr(
        pr,
        "_venue_command_for_decision_id",
        lambda decision_id: {
            "command_id": "local-command",
            "decision_id": decision_id,
            "intent_kind": "ENTRY",
            "token_id": OUR_TOKEN,
            "side": "BUY",
            "size": size,
            "price": limit_price,
            "state": "SUBMITTING",
            "created_at": "1001",
            "venue_order_id": OUR_ORDER,
            "envelope_size": str(size),
            "envelope_price": str(limit_price),
            "envelope_order_type": order_type,
        },
    )


def _command_db(*, duplicate=False):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT, decision_id TEXT, intent_kind TEXT, token_id TEXT,
            side TEXT, size REAL, price REAL, state TEXT, created_at TEXT,
            envelope_id TEXT, venue_order_id TEXT
        );
        CREATE TABLE venue_submission_envelopes (
            envelope_id TEXT, size TEXT, price TEXT, order_type TEXT
        );
        """
    )
    rows = [("local-command", "cmd1", "1001", "envelope-1")]
    if duplicate:
        rows.append(("other-command", "other-decision", "1002", "envelope-2"))
    for command_id, decision_id, created_at, envelope_id in rows:
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                command_id,
                decision_id,
                "ENTRY",
                OUR_TOKEN,
                "BUY",
                173.0,
                0.09,
                "SUBMITTING",
                created_at,
                envelope_id,
                OUR_ORDER if command_id == "local-command" else "0xother",
            ),
        )
        conn.execute(
            "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?)",
            (envelope_id, "173.0", "0.09", "FAK"),
        )
    return conn


def test_venue_command_binding_reads_exact_persisted_order_identity(monkeypatch):
    conn = _command_db(duplicate=True)
    monkeypatch.setattr(pr, "get_trade_connection_read_only", lambda: conn)
    command = pr._venue_command_for_decision_id("cmd1")
    assert command["command_id"] == "local-command"
    assert command["venue_order_id"] == OUR_ORDER


def test_build_presence_proof_happy_path(monkeypatch):
    _patch_plan(monkeypatch)
    proof = pr.build_presence_proof(
        None, "agg", trades=[_our_maker_trade()], funder_address=FUNDER
    )
    assert proof["filled_size"] == 5.07
    assert proof["avg_fill_price"] == 0.64
    assert proof["venue_order_id"] == OUR_ORDER
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


def test_build_presence_proof_cross_order_guard_refuses(monkeypatch):
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
    with pytest.raises(RuntimeError, match="exactly one venue order"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_accepts_price_improved_fak_share_overfill(monkeypatch):
    """Real live shape: better BUY price yields more shares but spends less
    than the submitted size*limit capital bound."""
    _patch_plan(monkeypatch, size=173.0, limit_price=0.09, order_type="FAK_LIMIT")
    t = {
        "id": "live-price-improvement",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "asset_id": OUR_TOKEN,
        "maker_address": FUNDER,
        "side": "BUY",
        "price": "0.08",
        "size": "189.77",
        "match_time": "1002",
        "taker_order_id": OUR_ORDER,
        "maker_orders": [],
    }
    proof = pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)
    assert proof["filled_size"] == 189.77
    assert proof["filled_notional"] == pytest.approx(15.1816)
    assert proof["max_submitted_notional"] == pytest.approx(15.57)
    assert proof["fill_bound_semantics"] == "PRICE_IMPROVED_NOTIONAL_BOUNDED"
    assert proof["venue_command_state"] == "FILLED"


def test_build_presence_proof_refuses_notional_overspend(monkeypatch):
    _patch_plan(monkeypatch, size=5.0, limit_price=0.65, order_type="FAK_LIMIT")
    t = {
        "id": "live-price-improvement",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "asset_id": OUR_TOKEN,
        "maker_address": FUNDER,
        "side": "BUY",
        "price": "0.65",
        "size": "5.000001",
        "match_time": "1002",
        "taker_order_id": OUR_ORDER,
        "maker_orders": [],
    }
    with pytest.raises(RuntimeError, match="fill notional"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_refuses_non_fak_share_overfill(monkeypatch):
    _patch_plan(monkeypatch, size=5.0, limit_price=0.65, order_type="GTC")
    t = _our_maker_trade()
    t["maker_orders"][0]["price"] = "0.50"
    t["maker_orders"][0]["matched_amount"] = "5.3"
    with pytest.raises(RuntimeError, match="non-FAK presence legs sum"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_refuses_fill_above_buy_limit(monkeypatch):
    _patch_plan(monkeypatch, size=5.0, limit_price=0.65)
    t = _our_maker_trade()
    t["maker_orders"][0]["price"] = "0.66"
    with pytest.raises(RuntimeError, match="price exceeds"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_refuses_authenticated_sell_order(monkeypatch):
    _patch_plan(monkeypatch, size=5.0, limit_price=0.65)
    t = _our_maker_trade()
    t["maker_orders"][0]["side"] = "SELL"
    with pytest.raises(RuntimeError, match="side is missing or not BUY"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_refuses_historical_same_token_trade(monkeypatch):
    _patch_plan(monkeypatch)
    t = _our_maker_trade()
    t["match_time"] = "900"
    with pytest.raises(RuntimeError, match="outside this submit attempt window"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_refuses_different_canonical_order(monkeypatch):
    _patch_plan(monkeypatch)
    t = _our_maker_trade()
    t["maker_orders"][0]["order_id"] = "0xother"
    with pytest.raises(RuntimeError, match="does not match the canonical"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_build_presence_proof_keeps_raw_decimal_precision(monkeypatch):
    _patch_plan(monkeypatch, size=1.0, limit_price=0.5, order_type="FAK_LIMIT")
    t = {
        "id": "precision-boundary",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "asset_id": OUR_TOKEN,
        "maker_address": FUNDER,
        "side": "BUY",
        "price": "0.5",
        "size": "1.00000000000000001",
        "match_time": "1002",
        "taker_order_id": OUR_ORDER,
        "maker_orders": [],
    }
    with pytest.raises(RuntimeError, match="fill notional"):
        pr.build_presence_proof(None, "agg", trades=[t], funder_address=FUNDER)


def test_resolve_presence_returns_incomplete_when_stuck_but_no_fill_proofs(monkeypatch):
    """A non-fill proof refusal must let boot continue to resting/absorbed.

    Returning success here would strand a live-resting order before the third
    resolver rung can prove and reconcile it.
    """

    class DummyConn:
        def close(self):
            pass

    monkeypatch.setattr(pr, "_read_authenticated_venue", lambda: ([], []))
    monkeypatch.setattr(pr, "_funder_address", lambda: FUNDER)
    monkeypatch.setattr(pr, "get_world_connection_read_only", lambda: DummyConn())
    monkeypatch.setattr(pr, "_readiness_counts", lambda conn: (1, 1))
    monkeypatch.setattr(pr, "_pending_aggregates", lambda conn, aggregate_id: ["agg1"])

    def _no_presence(*args, **kwargs):
        raise RuntimeError("no CONFIRMED trade owned by our funder on this token")

    monkeypatch.setattr(pr, "build_presence_proof", _no_presence)
    logs: list[str] = []

    assert pr.resolve_presence(aggregate_id=None, apply=True, log=logs.append) == 1
    assert any("PRESENCE_SKIP" in msg for msg in logs)
    assert "Nothing to resolve." in logs


def test_resolve_presence_returns_clean_when_no_stuck_work(monkeypatch):
    class DummyConn:
        def close(self):
            pass

    monkeypatch.setattr(pr, "_read_authenticated_venue", lambda: ([], []))
    monkeypatch.setattr(pr, "_funder_address", lambda: FUNDER)
    monkeypatch.setattr(pr, "get_world_connection_read_only", lambda: DummyConn())
    monkeypatch.setattr(pr, "_readiness_counts", lambda conn: (0, 0))
    monkeypatch.setattr(pr, "_pending_aggregates", lambda conn, aggregate_id: [])
    logs: list[str] = []

    assert pr.resolve_presence(aggregate_id=None, apply=True, log=logs.append) == 0
    assert "Nothing to resolve." in logs
