# Created: 2026-10-05
# Last reused/audited: 2026-10-05
# Authority basis: operator law 2026-10-05 (records and continuous evaluation only);
#   scripts/multiday_evaluation.py is read-only, report-only, outside the money path.
"""Bucketing, HIGH/LOW split, unknown listing time, sell-vs-hold regret, fail-soft."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import multiday_evaluation as me

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SINCE = date(2026, 9, 28)
LISTED = "2026-10-01T00:00:00Z"
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _stub_resolution(_payload, family, *, city_configs):
    return datetime(2026, 10, 3, tzinfo=timezone.utc)


def _entry(cid, pid, cond, hours_after_listing, *, metric="high", size=5.0, price=0.2, state="FILLED"):
    stamp = T0.replace(hour=0) + (me.timedelta(hours=hours_after_listing))
    return {
        "command_id": cid, "position_id": pid, "state": state, "size": size, "price": price,
        "created_at": stamp.isoformat(), "snap_cond": cond, "pos_cond": cond,
        "pos_city": "Tokyo", "pos_date": "2026-10-02", "pos_metric": metric,
    }


def _fills(conn, ids):
    """load_economic_fills' fills half (its second half, the unverifiable commands, is tested apart)."""
    return me.load_economic_fills(conn, ids)[0]


def _fill(price, size=5.0, *_ignored):
    """One economic fill as load_economic_fills returns it: (size, price, execution_ts)."""
    return (size, price, "2026-10-01T06:00:00+00:00")


def _pos(pid, *, metric="high", phase="settled", direction="buy_yes", pnl=None, cost=1.0,
         entry_price=0.2, settlement_price=None, exit_reason="SETTLEMENT", settled_at="2026-10-03T01:00:00+00:00"):
    return {
        "position_id": pid, "phase": phase, "city": "Tokyo", "target_date": "2026-10-02",
        "metric": metric, "direction": direction, "cost_basis_usd": cost, "entry_price": entry_price,
        "realized_pnl_usd": pnl, "settled_at": settled_at, "settlement_price": settlement_price,
        "exit_reason": exit_reason,
    }


def _td(entries, positions, fills, *, entry_q=None, exit_cmds=(), exit_reasons=None, revals=(), exit_debt=()):
    """Test fixture: SELL fills given per EXIT command are re-keyed by position like the loader does."""
    exit_cmds = list(exit_cmds)
    exit_fills: dict = {}
    for x in exit_cmds:
        exit_fills.setdefault(x["position_id"], []).extend(fills.get(x["command_id"], ()))
    return {
        "entries": entries, "positions": positions, "fills": fills,
        "entry_q": entry_q or {}, "exit_cmds": exit_cmds,
        "exit_fills": exit_fills, "exit_fill_debt": list(exit_debt),
        "exit_reasons": exit_reasons or {}, "revaluations": list(revals),
        "open_older_than_window": (0, 0.0),
    }


def _listings(*conds, created=LISTED, metric="high"):
    return {
        c: {"city": "Tokyo", "target_date": "2026-10-02", "metric": metric, "created_at_values": [created]}
        for c in conds
    }


def _report(td, listings, attribution=None):
    return me.build_report(
        td, listings, attribution or {}, since=SINCE, now=NOW,
        city_configs=None, resolution_at=_stub_resolution,
    )


def _bucket(rep, metric, name):
    return next(r for r in rep["by_age_bucket"] if r["metric"] == metric and r["age_bucket"] == name)


@pytest.mark.parametrize(
    "age,name",
    [(0.0, "<12h"), (11.999, "<12h"), (12.0, "12-24h"), (23.999, "12-24h"),
     (24.0, "24-48h"), (47.999, "24-48h"), (48.0, ">=48h"), (500.0, ">=48h"), (None, "unknown")],
)
def test_age_bucket_edges(age, name):
    assert me.age_bucket(age) == name


def test_listing_clock_is_gamma_z_only_and_earliest():
    assert me.gamma_listed_at(["2026-10-01T05:00:00+00:00", None]) is None
    assert me.gamma_listed_at(["2026-10-01T05:00:00+00:00", "2026-10-01T03:00:00.5Z", "2026-10-01T04:00:00Z"]) == datetime(
        2026, 10, 1, 3, 0, 0, 500000, tzinfo=timezone.utc
    )
    assert me.market_age_hours(T0, datetime(2026, 10, 2, tzinfo=timezone.utc)) is None  # negative never recorded


def test_buckets_split_by_metric_and_unknown_listing():
    entries = [
        _entry("c11", "p11", "A", 11), _entry("c12", "p12", "B", 12), _entry("c24", "p24", "C", 24),
        _entry("c48", "p48", "D", 48), _entry("cl", "pl", "L", 30, metric="low"),
        _entry("cu", "pu", "NOLIST", 30),
        _entry("cn", "pn", "NONGAMMA", 30),
    ]
    fills = {e["command_id"]: [_fill(0.2)] for e in entries}
    listings = {
        **_listings("A", "B", "C", "D"),
        **_listings("L", metric="low"),
        "NONGAMMA": {"city": "Tokyo", "target_date": "2026-10-02", "metric": "high",
                     "created_at_values": ["2026-10-01T00:00:00+00:00"]},
    }
    rep = _report(_td(entries, [], fills), listings)

    assert _bucket(rep, "high", "<12h")["entries_submitted"] == 1
    assert _bucket(rep, "high", "12-24h")["entries_submitted"] == 1
    assert _bucket(rep, "high", "24-48h")["entries_submitted"] == 1
    assert _bucket(rep, "high", ">=48h")["entries_submitted"] == 1
    assert _bucket(rep, "high", "unknown")["entries_submitted"] == 2  # no row + non-Gamma clock
    assert _bucket(rep, "low", "24-48h")["entries_submitted"] == 1
    assert not any(r["metric"] == "low" and r["age_bucket"] == "<12h" for r in rep["by_age_bucket"])
    assert rep["coverage"]["listing_unknown_no_market_events_row"] == 1
    assert rep["coverage"]["listing_unknown_non_gamma_clock"] == 1
    rec = {e["command_id"]: e for e in rep["entries"]}
    assert rec["cu"]["market_listed_at"] is None and rec["cu"]["market_age_hours"] is None
    assert rec["c12"]["market_age_hours"] == 12.0 and rec["c12"]["market_listed_at"] == "2026-10-01T00:00:00+00:00"
    assert rec["c12"]["settlement_lead_hours"] == pytest.approx(48.0 - 12.0)


def test_entries_before_window_are_excluded():
    old = _entry("co", "po", "A", 10)
    old["pos_date"] = "2026-09-01"
    rep = _report(_td([old], [], {"co": [_fill(0.2)]}), {"A": {**_listings("A")["A"], "target_date": "2026-09-01"}})
    assert rep["by_age_bucket"] == [] and rep["entries"] == [] and "entries_in_window" not in rep["coverage"]


def test_mean_q_vs_price_is_share_weighted_and_counts_missing_q():
    entries = [_entry("c1", "p1", "A", 30, size=10.0), _entry("c2", "p2", "A", 30, size=10.0), _entry("c3", "p3", "A", 30)]
    fills = {
        "c1": [_fill(0.2, 10.0, "0x" + "1" * 64)],
        "c2": [_fill(0.4, 10.0, "0x" + "2" * 64)],
        "c3": [_fill(0.9, 5.0, "0x" + "3" * 64)],
    }
    rep = _report(_td(entries, [], fills, entry_q={"c1": 0.5, "c2": 0.7, "c3": None}), _listings("A"))
    b = _bucket(rep, "high", "24-48h")
    assert b["entries_filled"] == 3 and b["q_missing_fills"] == 1
    assert b["mean_acting_q"] == pytest.approx(0.6)
    assert b["mean_fill_price_on_q_fills"] == pytest.approx(0.3)
    assert b["mean_q_minus_fill_price"] == pytest.approx(0.3)


def test_fill_totals_flags_overshoot_only():
    assert me.fill_totals([(2.0, 0.2), (3.0, 0.3)], 5.0) == (5.0, pytest.approx(1.3), False)
    assert me.fill_totals([(5.0, 0.3), (5.0, 0.3)], 5.0)[2] is True
    assert me.fill_totals([], 5.0) == (0.0, 0.0, False)


def _facts_conn(rows):
    """In-memory venue_trade_facts (real column set) holding (command, trade, state, size, price, tx, seq)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT NOT NULL,
            venue_order_id TEXT NOT NULL, command_id TEXT NOT NULL, state TEXT NOT NULL,
            filled_size TEXT NOT NULL, fill_price TEXT NOT NULL, fee_paid_micro INTEGER,
            tx_hash TEXT, block_number INTEGER, confirmation_count INTEGER DEFAULT 0,
            source TEXT NOT NULL, observed_at TEXT NOT NULL, venue_timestamp TEXT,
            ingested_at TEXT, local_sequence INTEGER NOT NULL, raw_payload_hash TEXT NOT NULL,
            raw_payload_json TEXT, UNIQUE (trade_id, local_sequence))"""
    )
    for i, (cmd, trade, state, size, price, tx, seq) in enumerate(rows):
        conn.execute(
            "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,"
            " tx_hash, source, observed_at, local_sequence, raw_payload_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (trade, "o-" + cmd, cmd, state, str(size), str(price), tx, "REST", f"2026-10-01T00:00:{i:02d}+00:00", seq, f"h{i}"),
        )
    return conn


TX = "0x" + "c" * 64


def test_children_sharing_one_tx_hash_both_count():
    """Counterexample 1: two exact children (2@0.2, 3@0.3) under ONE tx are 5 sh / $1.30."""
    conn = _facts_conn([
        ("c1", "child-a", "CONFIRMED", 2, 0.2, TX, 1),
        ("c1", "child-b", "CONFIRMED", 3, 0.3, TX, 1),
    ])
    assert sorted(f[:2] for f in _fills(conn, ["c1"])["c1"]) == [(2.0, 0.2), (3.0, 0.3)]
    assert me.fill_totals(_fills(conn, ["c1"])["c1"], 5.0)[:2] == (5.0, pytest.approx(1.3))


def test_equal_size_children_without_tx_hash_both_count():
    """Counterexample 2: two 5@0.4 children with no tx_hash are two fills, not one."""
    conn = _facts_conn([
        ("c1", "child-a", "CONFIRMED", 5, 0.4, None, 1),
        ("c1", "child-b", "CONFIRMED", 5, 0.4, None, 1),
    ])
    assert me.fill_totals(_fills(conn, ["c1"])["c1"], 10.0)[:2] == (10.0, pytest.approx(4.0))


def test_matched_without_tx_then_confirmed_with_tx_counts_once():
    """Same trade_id, tx_hash filled in on the later revision: one fill, not two."""
    conn = _facts_conn([
        ("c1", "t1", "MATCHED", 5, 0.3, None, 1),
        ("c1", "t1", "CONFIRMED", 5, 0.3, TX, 2),
    ])
    assert [f[:2] for f in _fills(conn, ["c1"])["c1"]] == [(5.0, 0.3)]
    assert me.fill_totals(_fills(conn, ["c1"])["c1"], 5.0) == (5.0, pytest.approx(1.5), False)


def test_lifecycle_revisions_and_tx_aggregate_alias_count_once():
    conn = _facts_conn([
        ("c1", "t1", "MATCHED", 4, 0.30, TX, 1),
        ("c1", "t1", "MINED", 4, 0.30, TX, 2),
        ("c1", "t1", "CONFIRMED", 4, 0.3012345, TX, 3),   # lifecycle revisions of ONE trade
        ("c1", TX, "MATCHED", 4, 0.30, TX, 1),            # tx-hash aggregate alias of that child
        ("c1", "t2", "FAILED", 9, 0.9, None, 1),          # never an economic fill
        ("c2", "t3", "CONFIRMED", 1, 0.5, None, 1),
    ])
    fills = _fills(conn, ["c1", "c2"])
    assert [f[:2] for f in fills["c1"]] == [(4.0, pytest.approx(0.3012345))]
    assert [f[:2] for f in fills["c2"]] == [(1.0, 0.5)]
    assert all(f[2] for f in fills["c1"] + fills["c2"])  # canonical execution_ts is carried
    assert me.load_economic_fills(conn, ["nope"]) == ({}, set())


def test_sell_vs_hold_regret_sign_and_unresolved_exits():
    entries = [_entry(f"c{i}", f"p{i}", "A", 30) for i in range(1, 5)]
    fills = {e["command_id"]: [_fill(0.5, 5.0, "0x" + e["command_id"][1] * 64)] for e in entries}
    exits = [{"command_id": f"x{i}", "position_id": f"p{i}", "size": 5.0} for i in range(1, 5)]
    fills.update({f"x{i}": [_fill(p, 5.0, "0x" + f"{i}e" * 32)] for i, p in ((1, 0.4), (2, 0.7), (3, 0.5), (4, 0.5))})
    positions = [
        _pos("p1", direction="buy_no", pnl=-0.5),       # NO wins at settlement: hold would pay 5.0 vs 2.0 sold
        _pos("p2", direction="buy_yes", pnl=0.5),       # YES loses: hold pays 0 vs 3.5 sold
        _pos("p3", direction="buy_yes", pnl=0.0, phase="day0_window", exit_reason=None, settled_at=None),
        _pos("p4", direction="buy_yes", pnl=0.0, phase="economically_closed", exit_reason=None, settled_at=None),
    ]
    attribution = {
        "p1": {"category": "SKILL_LOSS", "settled_in_bin": 0, "direction": "buy_no", "world_pnl": -0.5},
        "p2": {"category": "SKILL_WIN", "settled_in_bin": 0, "direction": "buy_yes", "world_pnl": 0.5},
    }
    reasons = {f"p{i}": "GLOBAL_CAPITAL_OPTIMAL_SELL" for i in range(1, 5)}
    rep = _report(_td(entries, positions, fills, exit_cmds=exits, exit_reasons=reasons), _listings("A"), attribution)

    by_pos = {r["position_id"]: r for r in rep["exits"]}
    assert by_pos["p1"]["hold_minus_sell_usd"] == pytest.approx(5.0 - 2.0)    # hold better: positive
    assert by_pos["p2"]["hold_minus_sell_usd"] == pytest.approx(0.0 - 3.5)    # sell better: negative
    assert by_pos["p3"]["hold_minus_sell_usd"] is None                       # settlement unknown
    assert by_pos["p4"]["hold_minus_sell_usd"] is None
    b = _bucket(rep, "high", "24-48h")
    assert b["exits"] == 4 and b["exits_resolved"] == 2 and b["exits_hold_better"] == 1
    assert b["hold_minus_sell_usd"] == pytest.approx(3.0 - 3.5)
    assert b["exit_reasons"] == {"GLOBAL_CAPITAL_OPTIMAL_SELL": 4}
    assert b["open_positions"] == 1 and b["closed_unsettled"] == 1
    assert b["hold_to_settlement_pnl_usd"] == pytest.approx(0.0) and b["hold_to_settlement_n"] == 2


def _buy(ts, size, price):
    return (size, price, ts)


T1, T2, T3 = ("2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00", "2026-10-01T12:00:00+00:00")


def test_sell_before_a_later_buy_is_charged_only_the_inventory_it_held():
    """ENTRY 5@0.20, SELL 5@0.30, ENTRY 5@0.50: the sell gains +$0.50, not on the 0.35 VWAP."""
    sells = me.sell_costs([_buy(T1, 5, 0.20), _buy(T3, 5, 0.50)], [_buy(T2, 5, 0.30)])
    (sell,) = sells
    assert sell["cost_usd"] == pytest.approx(1.0) and sell["proceeds_usd"] == pytest.approx(1.5)
    assert sell["proceeds_usd"] - sell["cost_usd"] == pytest.approx(0.50)
    assert 5 * 0.30 - 5 * ((5 * 0.20 + 5 * 0.50) / 10) == pytest.approx(-0.25)  # the lifecycle-VWAP answer


def test_sell_after_both_buys_uses_the_blended_average():
    (sell,) = me.sell_costs([_buy(T1, 5, 0.20), _buy(T2, 5, 0.50)], [_buy(T3, 5, 0.60)])
    assert sell["cost_usd"] == pytest.approx(1.75)  # 5 of 10 held shares at the 0.35 running average


def test_sell_order_is_irrelevant_and_basis_shrinks_proportionally():
    sells = me.sell_costs([_buy(T1, 10, 0.20)], [_buy(T3, 4, 0.40), _buy(T2, 2, 0.30)])
    assert [round(s["cost_usd"], 6) for s in sorted(sells, key=lambda s: s["ts"])] == [0.4, 0.8]


def test_sell_with_inventory_outside_the_loaded_fills_has_null_cost():
    """No accountable buy (or a buy smaller than the sell): never a guessed cost."""
    assert me.sell_costs([], [_buy(T2, 5, 0.3)])[0]["cost_usd"] is None
    short = me.sell_costs([_buy(T1, 2, 0.20)], [_buy(T2, 5, 0.30)])
    assert short[0]["cost_usd"] is None and short[0]["proceeds_usd"] == pytest.approx(1.5)


def test_one_untimed_fill_makes_every_sell_of_the_position_unknown():
    """BUY 10@0.20 10:00, untimed SELL 10 (really 11:00), BUY 10@0.80 12:00, SELL 5 at 13:00.

    Skipping the untimed sell used to leave the last sell 'known' at $2.50; the truth is $4.00
    (the first sell consumed the 0.20 lot), so no sell of this position may carry a cost."""
    buys = [_buy(T1, 10, 0.20), _buy(T3, 10, 0.80)]
    sells = [(10, 0.30, None), _buy("2026-10-01T13:00:00+00:00", 5, 0.90)]
    out = me.sell_costs(buys, sells)
    assert [s["cost_usd"] for s in out] == [None, None]
    assert [s["unknown"] for s in out] == ["no_clock", "no_clock"]
    assert out[1]["proceeds_usd"] == pytest.approx(4.5)               # proceeds are still reported
    # with the clock restored the same fills price correctly: first sell $2.00, second $4.00
    ok = me.sell_costs(buys, [(10, 0.30, T2), _buy("2026-10-01T13:00:00+00:00", 5, 0.90)])
    assert [round(s["cost_usd"], 6) for s in ok] == [2.0, 4.0]


def test_untimed_buy_or_unparseable_clock_also_poisons_the_position():
    assert [s["cost_usd"] for s in me.sell_costs([(5, 0.2, None)], [_buy(T2, 5, 0.3)])] == [None]
    assert [s["cost_usd"] for s in me.sell_costs([_buy(T1, 5, 0.2)], [(5, 0.3, "not-a-time")])] == [None]
    assert me.sell_costs([_buy(T1, 5, 0.2)], []) == []                 # no sells, nothing to poison


def test_untimed_position_is_counted_and_costs_nothing_in_the_report():
    entries = [_entry("c1", "p1", "A", 10), _entry("c2", "p1", "A", 20)]
    fills = {
        "c1": [(10.0, 0.20, "2026-10-01T10:00:00+00:00")],
        "c2": [(10.0, 0.80, "2026-10-01T20:00:00+00:00")],
        "x1": [(10.0, 0.30, None)],                                        # untimed
        "x2": [(5.0, 0.90, "2026-10-01T22:00:00+00:00")],
    }
    positions = [_pos("p1", direction="buy_yes", pnl=0.0, phase="economically_closed", exit_reason=None, settled_at=None)]
    rep = _report(
        _td(entries, positions, fills, exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 10.0},
                                                  {"command_id": "x2", "position_id": "p1", "size": 5.0}]),
        _listings("A"),
    )
    (row,) = rep["exits"]
    assert row["cost_usd"] is None and all(s["cost_usd"] is None and s["unknown"] == "no_clock" for s in row["sells"])
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["exit_cost_unknown"] == 2 and total["exit_pnl_usd"] == 0.0
    assert total["exits"] == 1 and rep["coverage"]["exit_positions_untimed_fills"] == 1


def test_simultaneous_buy_and_sell_orders_buy_first():
    (sell,) = me.sell_costs([_buy(T1, 5, 0.20)], [_buy(T1, 5, 0.30)])
    assert sell["cost_usd"] == pytest.approx(1.0)


def test_exit_pnl_in_the_report_uses_per_sell_cost_not_lifecycle_vwap():
    entries = [_entry("c1", "p1", "A", 10), _entry("c2", "p1", "A", 20)]
    fills = {
        "c1": [(5.0, 0.20, "2026-10-01T10:00:00+00:00")],
        "c2": [(5.0, 0.50, "2026-10-01T20:00:00+00:00")],
        "x1": [(5.0, 0.30, "2026-10-01T15:00:00+00:00")],      # between the two buys
    }
    positions = [_pos("p1", direction="buy_yes", pnl=0.0, phase="economically_closed", exit_reason=None, settled_at=None)]
    rep = _report(
        _td(entries, positions, fills, exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 5.0}],
            exit_reasons={"p1": "GLOBAL_CAPITAL_OPTIMAL_SELL"}),
        _listings("A"),
    )
    (row,) = rep["exits"]
    assert row["cost_usd"] == pytest.approx(1.0) and row["proceeds_usd"] == pytest.approx(1.5)
    assert row["sells"] == [
        {"ts": "2026-10-01T15:00:00+00:00", "shares": 5.0, "proceeds_usd": 1.5, "cost_usd": 1.0, "unknown": None}
    ]
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["exit_pnl_usd"] == pytest.approx(0.50) and total["exit_cost_unknown"] == 0


def test_uncostable_sell_is_counted_and_excluded_from_exit_pnl():
    entries = [_entry("c1", "p1", "A", 10)]
    fills = {"c1": [(2.0, 0.20, "2026-10-01T10:00:00+00:00")], "x1": [(5.0, 0.30, "2026-10-01T15:00:00+00:00")]}
    positions = [_pos("p1", direction="buy_yes", pnl=0.0, phase="economically_closed", exit_reason=None, settled_at=None)]
    rep = _report(
        _td(entries, positions, fills, exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 5.0}]),
        _listings("A"),
    )
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["exit_cost_unknown"] == 1 and total["exit_pnl_usd"] == 0.0 and total["exits"] == 1
    assert rep["exits"][0]["cost_usd"] is None


def test_realized_and_hold_to_settlement_are_labelled_apart_and_never_equated():
    """realized includes the SELL; hold-to-settlement is the all-held counterfactual."""
    entries = [_entry("c1", "p1", "A", 30)]
    fills = {"c1": [(5.0, 0.4, "2026-10-01T10:00:00+00:00")], "x1": [(5.0, 0.7, "2026-10-01T12:00:00+00:00")]}
    positions = [_pos("p1", direction="buy_yes", pnl=1.5)]          # sold 5@0.7 for +1.5
    attribution = {"p1": {"category": "SKILL_LOSS", "settled_in_bin": 0, "direction": "buy_yes", "world_pnl": -2.0}}
    rep = _report(
        _td(entries, positions, fills, exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 5.0}]),
        _listings("A"), attribution,
    )
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["realized_pnl_usd"] == pytest.approx(1.5) and total["hold_to_settlement_pnl_usd"] == pytest.approx(-2.0)
    assert total["matched_n"] == 1 and total["matched_realized_minus_hold_usd"] == pytest.approx(3.5)
    assert total["hold_minus_sell_usd"] == pytest.approx(-3.5)  # the exit explains the whole gap
    assert total["ungraded_settled_n"] == 0 and total["ungraded_realized_pnl_usd"] == 0.0
    md = me.render_markdown(rep)
    assert "realized $1.50" in md and "hold-to-settlement $-2.00" in md
    assert "cross-check" not in md and "world-grade" not in md and " vs " not in md.split("## Settlement")[1]


def test_exits_effect_is_taken_over_matched_positions_only():
    """No SELLs; one graded position +$4, one ungraded position -$2: the exits' effect is 0, not -2."""
    entries = [_entry("c1", "p1", "A", 30), _entry("c2", "p2", "A", 30)]
    fills = {"c1": [(5.0, 0.2, "2026-10-01T10:00:00+00:00")], "c2": [(5.0, 0.2, "2026-10-01T10:00:00+00:00")]}
    positions = [_pos("p1", direction="buy_yes", pnl=4.0), _pos("p2", direction="buy_yes", pnl=-2.0)]
    attribution = {"p1": {"category": "SKILL_WIN", "settled_in_bin": 1, "direction": "buy_yes", "world_pnl": 4.0}}
    rep = _report(_td(entries, positions, fills), _listings("A"), attribution)
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["realized_pnl_usd"] == pytest.approx(2.0)                      # all settled
    assert total["hold_to_settlement_pnl_usd"] == pytest.approx(4.0) and total["hold_to_settlement_n"] == 1
    assert total["matched_n"] == 1
    assert total["matched_realized_minus_hold_usd"] == pytest.approx(0.0)       # truth: no exits, no effect
    assert total["ungraded_settled_n"] == 1 and total["ungraded_realized_pnl_usd"] == pytest.approx(-2.0)
    assert "realized_minus_hold_to_settlement_usd" not in total                  # the mixed-cohort field is gone
    md = me.render_markdown(rep)
    assert "= $0.00 over 1 positions settled AND graded" in md
    assert "ungraded settled, kept out of that difference: 1 positions, realized $-2.00" in md


def test_no_graded_position_leaves_the_matched_difference_null():
    rep = _report(_td([_entry("c1", "p1", "A", 30)], [_pos("p1", pnl=-1.0)], {"c1": [_fill(0.2)]}), _listings("A"))
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["matched_n"] == 0 and total["matched_realized_minus_hold_usd"] is None
    assert total["ungraded_settled_n"] == 1 and total["ungraded_realized_pnl_usd"] == pytest.approx(-1.0)
    assert "= $- over 0 positions" in me.render_markdown(rep)


def test_entry_history_wording_is_true_and_counters_are_present():
    rep = _report(_td([], [_pos("p1", pnl=1.0)], {}), {})
    assert rep["entry_floor_days"] == me.ENTRY_FLOOR_DAYS == 7
    assert rep["entry_floor_date"] == "2026-09-21"                  # SINCE 2026-09-28 minus 7 days
    assert rep["coverage"]["positions_without_filled_entry_in_window"] == 1
    md = me.render_markdown(rep)
    assert "COMPLETE ENTRY command history" in md and "never depend on a date cut" in md
    assert "2026-09-21" in md and "7-day floor only bounds" in md
    assert "bucketed 'unknown'" in md and "no filled ENTRY command in the DB at all" in md
    assert "bucketed 'unknown' (coverage.positions_without_filled_entry_in_window)" in md
    assert "older is bucketed" not in md                              # the false claim is gone


def test_position_with_an_entry_older_than_the_floor_is_costed_from_its_full_history():
    """Old 10@0.20 (before the floor), new 10@0.80, SELL 5: cost is $2.50 (avg 0.50), not $4.00."""
    old = _entry("c_old", "p1", "A", 4, size=10.0)                    # 2026-10-01T04:00
    new = _entry("c_new", "p1", "A", 30, size=10.0)
    fills = {
        "c_old": [(10.0, 0.20, "2026-10-01T04:00:00+00:00")],
        "c_new": [(10.0, 0.80, "2026-10-02T06:00:00+00:00")],
        "x1": [(5.0, 0.90, "2026-10-02T09:00:00+00:00")],
    }
    positions = [_pos("p1", direction="buy_yes", pnl=1.0)]
    rep = _report(
        _td([old, new], positions, fills, exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 5.0}]),
        _listings("A"),
    )
    (row,) = rep["exits"]
    assert row["cost_usd"] == pytest.approx(2.5) and row["proceeds_usd"] == pytest.approx(4.5)
    assert row["age_bucket"] == "<12h"                                 # first fill, 4 h after the 00:00 listing
    assert not any(c["age_bucket"] == "24-48h" and c["exits"] for c in rep["cells"])


def test_loader_reads_the_full_entry_history_of_window_positions_beyond_the_floor(tmp_path):
    trades, _fc, _world = _make_dbs(tmp_path)
    t = sqlite3.connect(trades)
    # an ENTRY of the window position that predates the floor (2026-09-21) by weeks
    t.execute(
        "INSERT INTO venue_commands (command_id, snapshot_id, position_id, intent_kind, size, price, state, created_at)"
        " VALUES ('c_old','s1','p1','ENTRY',10,0.2,'FILLED','2026-08-01T06:00:00+00:00')"
    )
    # an unrelated old ENTRY of a position OUTSIDE the window must stay out
    t.execute(
        "INSERT INTO venue_commands (command_id, snapshot_id, position_id, intent_kind, size, price, state, created_at)"
        " VALUES ('c_other','s1','pZ','ENTRY',10,0.2,'FILLED','2026-08-01T06:00:00+00:00')"
    )
    t.commit()
    t.close()
    conn = me._open_ro(trades)
    td = me.load_trades_data(conn, SINCE)
    conn.close()
    ids = {e["command_id"] for e in td["entries"]}
    assert {"c1", "c_old"} <= ids and "c_other" not in ids
    assert [e["command_id"] for e in td["entries"]] == sorted(ids, key=lambda i: next(e["created_at"] for e in td["entries"] if e["command_id"] == i))


def test_position_whose_history_cannot_be_loaded_is_unknown_with_null_cost():
    """No filled ENTRY in the DB at all (chain-only holding): bucket unknown, cost null, counted."""
    positions = [_pos("p1", direction="buy_yes", pnl=1.0)]
    fills = {"x1": [(5.0, 0.30, "2026-10-02T09:00:00+00:00")]}
    rep = _report(
        _td([], positions, fills, exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 5.0}]), {},
    )
    (row,) = rep["exits"]
    assert row["cost_usd"] is None and row["age_bucket"] == "unknown"
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["exit_cost_unknown"] == 1 and rep["coverage"]["positions_without_filled_entry_in_window"] == 1


def test_entry_history_older_than_the_floor_is_counted_in_coverage():
    old = _entry("c_old", "p1", "A", 4, size=10.0)
    old["created_at"] = "2026-09-01T06:00:00+00:00"                    # weeks before the 2026-09-21 floor
    fills = {"c_old": [(10.0, 0.2, "2026-09-01T06:00:00+00:00")]}
    rep = _report(_td([old], [_pos("p1", pnl=0.0)], fills), _listings("A"))
    assert rep["coverage"]["positions_entry_history_read_beyond_floor"] == 1


def test_outcome_win_rate_attribution_and_equity_series():
    entries = [_entry("c1", "p1", "A", 30), _entry("c2", "p2", "A", 30, metric="low"), _entry("c3", "p3", "A", 30)]
    fills = {e["command_id"]: [_fill(0.2, 5.0, "0x" + e["command_id"][1] * 64)] for e in entries}
    positions = [
        _pos("p1", pnl=4.0, settled_at="2026-10-03T01:00:00+00:00"),
        _pos("p2", metric="low", pnl=-1.0, settled_at="2026-10-03T02:00:00+00:00"),
        _pos("p3", pnl=-2.0, settled_at="2026-10-04T02:00:00+00:00"),
    ]
    attribution = {
        "p1": {"category": "SKILL_WIN", "settled_in_bin": 1, "direction": "buy_yes", "world_pnl": 4.0},
        "p2": {"category": "SKILL_LOSS", "settled_in_bin": 1, "direction": "buy_no", "world_pnl": -1.0},
    }
    rep = _report(_td(entries, positions, fills), {**_listings("A")}, attribution)
    high = next(t for t in rep["totals"] if t["metric"] == "high")
    low = next(t for t in rep["totals"] if t["metric"] == "low")
    assert (high["settled"], high["wins"], high["losses"], high["win_rate"]) == (2, 1, 1, 0.5)
    assert high["realized_pnl_usd"] == pytest.approx(2.0)
    assert high["attribution"] == {"SKILL_WIN": 1, "UNGRADED": 1}
    assert (low["settled"], low["wins"], low["losses"]) == (1, 0, 1) and low["attribution"] == {"SKILL_LOSS": 1}
    assert rep["equity"]["high"] == [
        {"settled_date": "2026-10-03", "pnl_usd": 4.0, "cumulative_usd": 4.0},
        {"settled_date": "2026-10-04", "pnl_usd": -2.0, "cumulative_usd": 2.0},
    ]
    assert rep["equity"]["all"][-1]["cumulative_usd"] == pytest.approx(1.0)


def test_unknown_resolution_horizon_is_null_not_fatal():
    def boom(*_a, **_k):
        raise ValueError("no timezone")

    rep = me.build_report(
        _td([_entry("c1", "p1", "A", 30)], [], {"c1": [_fill(0.2)]}), _listings("A"), {},
        since=SINCE, now=NOW, city_configs=None, resolution_at=boom,
    )
    assert rep["entries"][0]["settlement_lead_hours"] is None
    assert rep["coverage"]["settlement_lead_unknown"] == 1


def test_real_resolution_horizon_is_local_midnight_ending_target_date():
    cfg = {"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")}
    rep = me.build_report(
        _td([_entry("c1", "p1", "A", 0)], [], {"c1": [_fill(0.2)]}), _listings("A"), {},
        since=SINCE, now=NOW, city_configs=cfg,
    )
    # target 2026-10-02 Tokyo -> 2026-10-03T00:00+09:00 = 2026-10-02T15:00Z; decision 2026-10-01T00:00Z
    assert rep["entries"][0]["settlement_lead_hours"] == pytest.approx(39.0)


# ---------------------------------------------------------------- DB level
def _make_dbs(tmp_path, *, with_market_events=True, with_attribution=True):
    trades, fc, world = tmp_path / "trades.db", tmp_path / "fc.db", tmp_path / "world.db"
    t = sqlite3.connect(trades)
    t.executescript(
        """
        CREATE TABLE venue_commands (command_id TEXT, snapshot_id TEXT, position_id TEXT, intent_kind TEXT,
            size REAL, price REAL, state TEXT, created_at TEXT, venue_order_id TEXT, token_id TEXT,
            envelope_id TEXT);
        CREATE TABLE venue_submission_envelopes (envelope_id TEXT, selected_outcome_token_id TEXT,
            yes_token_id TEXT, no_token_id TEXT);
        CREATE TABLE executable_market_snapshots (snapshot_id TEXT, condition_id TEXT);
        CREATE TABLE position_current (position_id TEXT, phase TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, direction TEXT, cost_basis_usd REAL, entry_price REAL, condition_id TEXT,
            realized_pnl_usd REAL, settled_at TEXT, settlement_price REAL, exit_reason TEXT);
        CREATE TABLE venue_trade_facts (trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT,
            venue_order_id TEXT, command_id TEXT, state TEXT, filled_size TEXT, fill_price TEXT,
            fee_paid_micro INTEGER, tx_hash TEXT, observed_at TEXT, venue_timestamp TEXT,
            local_sequence INTEGER, raw_payload_json TEXT);
        CREATE TABLE venue_command_events (command_id TEXT, event_type TEXT, payload_json TEXT);
        CREATE TABLE position_events (position_id TEXT, event_type TEXT, payload_json TEXT, sequence_no INTEGER);
        CREATE INDEX idx_position_events_position_type_sequence
            ON position_events(position_id, event_type, sequence_no);
        CREATE TABLE decision_log (mode TEXT, timestamp TEXT, artifact_json TEXT);
        """
    )
    t.execute(
        "INSERT INTO venue_commands (command_id, snapshot_id, position_id, intent_kind, size, price, state, created_at)"
        " VALUES ('c1','s1','p1','ENTRY',5,0.2,'FILLED','2026-10-01T06:00:00+00:00')"
    )
    t.execute("INSERT INTO executable_market_snapshots VALUES ('s1','A')")
    t.execute("INSERT INTO position_current VALUES ('p1','settled','Tokyo','2026-10-02','high','buy_yes',1.0,0.2,'A',4.0,"
              "'2026-10-03T01:00:00+00:00',1.0,'SETTLEMENT')")
    t.execute(
        "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,"
        " tx_hash, observed_at, local_sequence) VALUES ('t1','o1','c1','CONFIRMED','5','0.2','0x" + "a" * 64
        + "','2026-10-01T06:00:05+00:00',1)"
    )
    payload = {"execution_capability": {"components": [{"component": "entry_economics", "details": {"q_live": 0.61}}]}}
    t.execute("INSERT INTO venue_command_events VALUES ('c1','SUBMIT_REQUESTED',?)", (json.dumps(payload),))
    t.commit(); t.close()
    f = sqlite3.connect(fc)
    if with_market_events:
        f.execute("CREATE TABLE market_events (condition_id TEXT, city TEXT, target_date TEXT, temperature_metric TEXT, created_at TEXT)")
        f.execute("INSERT INTO market_events VALUES ('A','Tokyo','2026-10-02','high','2026-10-01T00:30:00Z')")
    f.commit(); f.close()
    w = sqlite3.connect(world)
    if with_attribution:
        w.execute("CREATE TABLE settlement_attribution (position_id TEXT, category TEXT, settled_in_bin INTEGER, "
                  "direction TEXT, world_grade_pnl_usd REAL)")
        w.execute("INSERT INTO settlement_attribution VALUES ('p1','SKILL_WIN',1,'buy_yes',4.0)")
    w.commit(); w.close()
    return trades, fc, world


def _run(tmp_path, dbs, **kw):
    trades, fc, world = dbs
    return me.run_multiday_evaluation(
        since=SINCE, now=NOW, trades_db=trades, forecasts_db=fc, world_db=world, out_dir=tmp_path / "out",
        city_configs={"Tokyo": SimpleNamespace(timezone="Asia/Tokyo")}, **kw,
    )


def test_end_to_end_reads_ro_and_writes_json_and_markdown(tmp_path):
    rep = _run(tmp_path, _make_dbs(tmp_path))
    e = rep["entries"][0]
    assert e["market_age_hours"] == pytest.approx(5.5)
    assert e["age_bucket"] == "<12h" and e["acting_q"] == 0.61 and e["condition_id"] == "A"
    assert rep["totals"][0]["realized_pnl_usd"] == 4.0 and rep["totals"][0]["attribution"] == {"SKILL_WIN": 1}
    on_disk = json.loads((tmp_path / "out" / "multiday_evaluation.json").read_text())
    assert on_disk["schema"] == me.SCHEMA and "markdown" not in on_disk
    assert "HIGH by market age at entry" in (tmp_path / "out" / "multiday_evaluation.md").read_text()


def test_missing_market_events_and_attribution_degrade_to_unknown_never_raise(tmp_path):
    rep = _run(tmp_path, _make_dbs(tmp_path, with_market_events=False, with_attribution=False), write=False)
    assert rep["entries"][0]["age_bucket"] == "unknown" and rep["entries"][0]["market_listed_at"] is None
    assert rep["totals"][0]["attribution"] == {"UNGRADED": 1}
    assert len(rep["warnings"]) == 2 and not (tmp_path / "out").exists()


def test_sources_are_never_written(tmp_path):
    dbs = _make_dbs(tmp_path)
    before = [p.read_bytes() for p in dbs]
    _run(tmp_path, dbs)
    assert [p.read_bytes() for p in dbs] == before
    ro = me._open_ro(dbs[0])
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO venue_commands (command_id) VALUES ('x')")
    ro.close()


# ------------------------------------------------------- scheduler wiring
@pytest.fixture
def tick(monkeypatch):
    """Only the health-file write is faked; the money-path predicates stay real."""
    import src.main as main_module

    health: list = []
    monkeypatch.setattr(main_module, "_write_scheduler_health", lambda name, **kw: health.append((name, kw)))
    return main_module, health


def _stub_child(monkeypatch, main_module, **kw):
    """Replace subprocess.run inside src.main; returns the recorded invocations."""
    import subprocess

    calls: list = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "raise" in kw:
            raise kw["raise"]
        return subprocess.CompletedProcess(argv, kw.get("rc", 0), stdout="", stderr=kw.get("stderr", ""))

    monkeypatch.setattr(main_module.subprocess, "run", fake_run)
    return calls


def test_tick_runs_the_report_in_a_bounded_child(tick, monkeypatch):
    main_module, health = tick
    calls = _stub_child(monkeypatch, main_module)
    main_module._multiday_evaluation_tick()
    (argv, kwargs), = calls
    assert argv[0] == sys.executable and argv[1].endswith("scripts/multiday_evaluation.py") and "--quiet" in argv
    assert kwargs["timeout"] == main_module._MULTIDAY_EVALUATION_TIMEOUT_S and kwargs["stdin"] is not None
    assert health[-1] == ("multiday_evaluation", {"failed": False})


def test_child_db_busy_exit_skips_the_cadence_without_failing(tick, monkeypatch):
    main_module, health = tick
    _stub_child(monkeypatch, main_module, rc=me.EXIT_DB_BUSY, stderr="database busy")
    assert main_module._MULTIDAY_EVALUATION_EXIT_DB_BUSY == me.EXIT_DB_BUSY
    main_module._multiday_evaluation_tick()
    assert health[-1] == ("multiday_evaluation", {"failed": False})


def test_child_failure_and_timeout_are_recorded_and_never_raise_into_the_daemon(tick, monkeypatch):
    import subprocess

    main_module, health = tick
    _stub_child(monkeypatch, main_module, rc=1, stderr="Traceback ... boom")
    main_module._multiday_evaluation_tick()  # _scheduler_job swallows and records
    assert health[-1][1]["failed"] is True and "boom" in health[-1][1]["reason"]

    _stub_child(monkeypatch, main_module, **{"raise": subprocess.TimeoutExpired("x", 300)})
    main_module._multiday_evaluation_tick()
    assert health[-1][1]["failed"] is True and "timed out" in health[-1][1]["reason"]


def test_real_child_times_out_and_is_killed(tmp_path):
    """The parent's timeout is the hard bound: a hung child must be gone, not orphaned."""
    import src.main as main_module
    import subprocess

    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(60)\n")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, str(hang)], timeout=1.5, capture_output=True)
    assert time.monotonic() - started < 10
    assert main_module._MULTIDAY_EVALUATION_TIMEOUT_S >= 60  # cold-I/O runs reach ~65 s


def test_real_child_logs_with_timestamps_and_exits_cleanly(tmp_path):
    import re
    import subprocess

    dbs = _make_dbs(tmp_path)
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(me.PROJECT_ROOT / "scripts" / "multiday_evaluation.py"), "--quiet",
         "--since", "2026-09-28", "--trades-db", str(dbs[0]), "--forecasts-db", str(dbs[1]),
         "--world-db", str(dbs[2]), "--out-dir", str(out)],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""  # --quiet
    lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    stamp = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \[multiday_evaluation\] INFO: ")
    assert lines and all(stamp.match(ln) for ln in lines), lines
    phases = [re.search(r"phase=(\w+)", ln).group(1) for ln in lines if "phase=" in ln]
    assert phases == ["trades", "forecasts", "world", "build", "write"]
    assert (out / "multiday_evaluation.json").exists() and (out / "multiday_evaluation.md").exists()


def test_child_exits_75_when_a_database_is_locked(tmp_path):
    import subprocess

    dbs = _make_dbs(tmp_path)
    holder = sqlite3.connect(dbs[0], isolation_level=None)
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        proc = subprocess.run(
            [sys.executable, str(me.PROJECT_ROOT / "scripts" / "multiday_evaluation.py"), "--quiet",
             "--since", "2026-09-28", "--trades-db", str(dbs[0]), "--forecasts-db", str(dbs[1]),
             "--world-db", str(dbs[2]), "--no-write"],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
            env={**os.environ, "ZEUS_DB_READ_BUSY_TIMEOUT_MS": "200"},
        )
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert proc.returncode == me.EXIT_DB_BUSY, proc.stderr
    assert "database busy" in proc.stderr


def test_tick_runs_even_while_the_money_path_is_busy(tick, monkeypatch):
    """A child shares no GIL/lock/write txn with the reactor; yielding would skip most days."""
    main_module, health = tick
    calls = _stub_child(monkeypatch, main_module)
    assert main_module._defer_for_held_position_monitor("multiday_evaluation") is False  # never allowlisted
    assert main_module._edli_reactor_active_lock.acquire(blocking=False)
    assert main_module._edli_redecision_screen_lock.acquire(blocking=False)
    try:
        assert main_module._edli_reactor_active() and main_module._edli_redecision_screen_lock.locked()
        main_module._multiday_evaluation_tick()
    finally:
        main_module._edli_redecision_screen_lock.release()
        main_module._edli_reactor_active_lock.release()
    assert len(calls) == 1 and health[-1] == ("multiday_evaluation", {"failed": False})


def test_job_is_registered_daily_and_classified_non_collection():
    import re
    from pathlib import Path

    src = (Path(me.PROJECT_ROOT) / "src" / "main.py").read_text()
    assert re.search(r'_multiday_evaluation_tick, "cron", hour=9, minute=45,\s+id="multiday_evaluation"', src)
    from scripts.data_collection_inventory import _SRC_MAIN_NON_COLLECTION_JOB_IDS

    assert "multiday_evaluation" in _SRC_MAIN_NON_COLLECTION_JOB_IDS


def test_exit_reason_probe_is_pinned_to_the_composite_index(tmp_path):
    """Without the hint the planner walks the position_id autoindex (21-45 s live)."""
    trades, _fc, _world = _make_dbs(tmp_path)
    t = sqlite3.connect(trades)
    t.execute(
        "INSERT INTO venue_commands (command_id, snapshot_id, position_id, intent_kind, size, price, state, created_at,"
        " venue_order_id, token_id) VALUES ('x1','s1','p1','EXIT',5,0.3,'FILLED','2026-10-02T06:00:00+00:00','o-x1','TOK')"
    )
    t.execute("INSERT INTO position_events VALUES ('p1','EXIT_INTENT','{\"exit_reason\":\"GLOBAL_CAPITAL_OPTIMAL_SELL\"}',2)")
    t.commit()
    t.close()
    seen: list[str] = []
    conn = me._open_ro(trades)
    conn.set_trace_callback(seen.append)
    td = me.load_trades_data(conn, SINCE)
    conn.close()
    assert td["exit_reasons"] == {"p1": "GLOBAL_CAPITAL_OPTIMAL_SELL"}
    probes = [q for q in seen if "FROM position_events" in q]
    assert len(probes) == 3 and all("INDEXED BY idx_position_events_position_type_sequence" in q for q in probes)


# ---- finding 9: SELL proceeds and ENTRY cost use the canonical taker-leg economics -------------
YES_TOK, NO_TOK = "tok-yes", "tok-no"


def _leg_conn():
    """Minimal real-column schema: commands + envelope + trade facts, as fill_dedup reads them."""
    conn = _facts_conn([])
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (command_id TEXT, snapshot_id TEXT, position_id TEXT, intent_kind TEXT,
            side TEXT, size REAL, price REAL, state TEXT, created_at TEXT, venue_order_id TEXT, token_id TEXT,
            envelope_id TEXT);
        CREATE TABLE venue_submission_envelopes (envelope_id TEXT, condition_id TEXT, selected_outcome_token_id TEXT,
            yes_token_id TEXT, no_token_id TEXT, side TEXT);
        INSERT INTO venue_submission_envelopes VALUES ('env-buy', 'cond-1', 'tok-yes', 'tok-yes', 'tok-no', 'BUY');
        INSERT INTO venue_submission_envelopes VALUES ('env-sell', 'cond-1', 'tok-yes', 'tok-yes', 'tok-no', 'SELL');
        """
    )
    return conn


def _taker_fact(conn, command_id, order_id, side, top_price, size, legs, *, trade_id="child", state="CONFIRMED"):
    raw = {"asset_id": YES_TOK, "side": side, "trader_side": "TAKER", "taker_order_id": order_id,
           "market": "cond-1", "filled_size": str(size), "price": str(top_price), "maker_orders": legs}
    conn.execute(
        "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,"
        " tx_hash, source, observed_at, local_sequence, raw_payload_hash, raw_payload_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, order_id, command_id, state, str(size), str(top_price), None, "WS_USER",
         "2026-10-02T09:00:00+00:00", 1, "h-" + command_id, json.dumps(raw)),
    )




def test_exit_position_with_economics_debt_gets_no_proceeds_and_is_counted():
    entries = [_entry("c1", "p1", "A", 30)]
    td = _td(entries, [_pos("p1", direction="buy_yes", pnl=0.0)], {"c1": [_fill(0.2)]},
             exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 5.0}], exit_debt=["p1"])
    rep = _report(td, _listings("A"))
    assert rep["exits"] == [] and rep["coverage"]["exit_positions_economics_debt"] == 1






def test_floor_counterexample_end_to_end_from_the_database(tmp_path):
    """Old 10@0.20 omitted by the floor, new 10@0.80, SELL 5@0.90: cost $2.50, bucket from the FIRST entry."""
    trades, fc, world = _make_dbs(tmp_path)
    t = sqlite3.connect(trades)
    t.executescript(
        """
        DELETE FROM venue_commands; DELETE FROM venue_trade_facts; DELETE FROM position_events;
        INSERT INTO venue_commands (command_id, snapshot_id, position_id, intent_kind, size, price, state, created_at,
            venue_order_id, token_id, envelope_id)
        VALUES ('c_old','s1','p1','ENTRY',10,0.2,'FILLED','2026-08-01T00:30:00+00:00',NULL,NULL,NULL),
               ('c_new','s1','p1','ENTRY',10,0.8,'FILLED','2026-10-02T06:00:00+00:00',NULL,NULL,NULL),
               ('x1','s1','p1','EXIT',5,0.9,'FILLED','2026-10-02T09:00:00+00:00','o-x1','TOK',NULL);
        INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,
            tx_hash, observed_at, local_sequence)
        VALUES ('t_old','o','c_old','CONFIRMED','10','0.2',NULL,'2026-08-01T00:31:00+00:00',1),
               ('t_new','o','c_new','CONFIRMED','10','0.8',NULL,'2026-10-02T06:01:00+00:00',1),
               ('t_x1','o-x1','x1','CONFIRMED','5','0.9',NULL,'2026-10-02T09:01:00+00:00',1);
        UPDATE position_current SET realized_pnl_usd = 1.0, settled_at = '2026-10-03T01:00:00+00:00';
        INSERT INTO position_events VALUES ('p1','EXIT_INTENT','{"exit_reason":"GLOBAL_CAPITAL_OPTIMAL_SELL"}',2);
        """
    )
    f = sqlite3.connect(fc)
    f.execute("DELETE FROM market_events")
    f.execute("INSERT INTO market_events VALUES ('A','Tokyo','2026-10-02','high','2026-08-01T00:00:00Z')")
    f.commit()
    f.close()
    t.commit()
    t.close()
    rep = _run(tmp_path, (trades, fc, world), write=False)
    (row,) = rep["exits"]
    assert row["cost_usd"] == pytest.approx(2.5) and row["proceeds_usd"] == pytest.approx(4.5)
    assert row["age_bucket"] == "<12h"          # first ENTRY 30 min after listing, not the 24-48h of the later buy
    assert rep["coverage"]["positions_entry_history_read_beyond_floor"] == 1
    total = next(x for x in rep["totals"] if x["metric"] == "high")
    assert total["exit_cost_unknown"] == 0


# ------------------------------------------------------- the child's own deadline (no parent)
def test_child_dies_on_its_own_deadline_while_blocked_in_a_sqlite_lock_wait(tmp_path):
    """Parent gone / hung: the child must not keep reading. An exclusive lock plus a 60 s busy
    timeout parks the main thread inside sqlite's C wait; only a GIL-free watchdog can end it."""
    import subprocess

    dbs = _make_dbs(tmp_path)
    holder = sqlite3.connect(dbs[0], isolation_level=None)
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(me.PROJECT_ROOT / "scripts" / "multiday_evaluation.py"), "--quiet",
             "--since", "2026-09-28", "--trades-db", str(dbs[0]), "--forecasts-db", str(dbs[1]),
             "--world-db", str(dbs[2]), "--no-write", "--deadline-s", "2"],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,   # 60 s is only the test's safety net
            env={**os.environ, "ZEUS_DB_READ_BUSY_TIMEOUT_MS": "60000"},
        )
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    elapsed = time.monotonic() - started
    assert proc.returncode == 1, (proc.returncode, proc.stderr[-500:])
    assert elapsed < 20, f"the child outlived its 2 s deadline by far: {elapsed:.1f}s"
    assert "watchdog armed deadline_s=2" in proc.stderr
    assert "Timeout (0:00:02)!" in proc.stderr and "most recent call first" in proc.stderr   # stacks were dumped


def test_deadline_is_below_the_parents_timeout_and_a_normal_run_cancels_the_watchdog(tmp_path):
    import subprocess
    import src.main as main_module

    assert me.CHILD_DEADLINE_S < main_module._MULTIDAY_EVALUATION_TIMEOUT_S
    dbs = _make_dbs(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(me.PROJECT_ROOT / "scripts" / "multiday_evaluation.py"), "--quiet",
         "--since", "2026-09-28", "--trades-db", str(dbs[0]), "--forecasts-db", str(dbs[1]),
         "--world-db", str(dbs[2]), "--no-write", "--deadline-s", "30"],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr
    assert "watchdog armed deadline_s=30" in proc.stderr and "Timeout (" not in proc.stderr


def test_importing_the_script_does_not_arm_a_watchdog():
    """Only a CLI run arms it; library callers (the tests, the report API) never get killed."""
    import faulthandler

    assert me.main.__kwdefaults__["watchdog"] is False
    faulthandler.cancel_dump_traceback_later()   # no-op; nothing was scheduled by importing


# ------------------------------------------------------- one read snapshot for the loader
def _wal_db(tmp_path):
    db = tmp_path / "snap.db"
    w = sqlite3.connect(db, isolation_level=None)
    w.execute("PRAGMA journal_mode=WAL")
    w.execute("CREATE TABLE t(x)")
    w.execute("INSERT INTO t VALUES (1)")
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True, isolation_level=None)
    return w, ro


def test_read_snapshot_hides_a_commit_that_lands_between_two_reads(tmp_path):
    w, ro = _wal_db(tmp_path)
    # without it each SELECT is its own snapshot and straddles the commit
    first = ro.execute("SELECT count(*) FROM t").fetchone()[0]
    w.execute("INSERT INTO t VALUES (2)")
    assert (first, ro.execute("SELECT count(*) FROM t").fetchone()[0]) == (1, 2)
    w.execute("DELETE FROM t WHERE x = 2")
    with me._read_snapshot(ro):
        a = ro.execute("SELECT count(*) FROM t").fetchone()[0]
        w.execute("INSERT INTO t VALUES (2)")             # the writer is never blocked by the reader
        b = ro.execute("SELECT count(*) FROM t").fetchone()[0]
    assert (a, b) == (1, 1) and not ro.in_transaction
    assert ro.execute("SELECT count(*) FROM t").fetchone()[0] == 2     # the next read sees the commit
    w.close()
    ro.close()


def test_read_snapshot_nests_and_always_ends_its_transaction(tmp_path):
    _w, ro = _wal_db(tmp_path)
    with me._read_snapshot(ro):
        with me._read_snapshot(ro):                       # inner one joins, does not commit the outer
            assert ro.in_transaction
        assert ro.in_transaction
    assert not ro.in_transaction
    with pytest.raises(RuntimeError):
        with me._read_snapshot(ro):
            raise RuntimeError("boom")
    assert not ro.in_transaction                          # released even on an exception


def test_exit_fill_clock_map_and_economics_are_read_in_one_snapshot(tmp_path, monkeypatch):
    """A trade fact committed between the clock-map read and the economics read must not make a
    fill appear untimed: both reads of load_exit_fills run inside one transaction."""
    import src.state.fill_dedup as fd

    trades, _fc, _world = _make_dbs(tmp_path)
    t = sqlite3.connect(trades, isolation_level=None)
    t.execute("PRAGMA journal_mode=WAL")
    t.execute(
        "INSERT INTO venue_commands (command_id, snapshot_id, position_id, intent_kind, size, price, state, created_at,"
        " venue_order_id, token_id) VALUES ('x9','s1','p9','EXIT',5,0.3,'FILLED','2026-10-02T09:00:00+00:00','o-x9','TOK')"
    )
    conn = me._open_ro(trades)
    in_txn_at_economics: list[bool] = []

    def economics(c, position_id, **_kw):
        in_txn_at_economics.append(c.in_transaction)
        # a writer commits the SELL fact only AFTER the clock map was read
        t.execute(
            "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,"
            " tx_hash, observed_at, local_sequence) VALUES ('late','o-x9','x9','CONFIRMED','5','0.3',NULL,"
            "'2026-10-02T09:01:00+00:00',1)"
        )
        return [fd.EconomicExitFill("id", "x9", "o-x9", "late", Decimal("5"), Decimal("0.3"), Decimal("1.5"))]

    from decimal import Decimal
    monkeypatch.setattr(fd, "economic_exit_fills_for_position", economics)
    fills, debt = me.load_exit_fills(conn, [{"command_id": "x9", "position_id": "p9", "size": 5.0}])
    conn.close()
    t.close()
    assert in_txn_at_economics == [True]                  # economics read inside the snapshot transaction
    # the late fact is invisible to the snapshot, so its clock is None and sell_costs makes it unknown
    # rather than a half-timed position priced wrongly
    assert fills["p9"][0][2] is None and debt == []
    assert me.sell_costs([(5, 0.2, T1)], fills["p9"])[0]["cost_usd"] is None


def _cmd(conn, command_id, intent, side, size, price, order_id, envelope, position_id="p1",
         created="2026-10-02T09:00:00+00:00"):
    conn.execute(
        "INSERT INTO venue_commands (command_id, position_id, intent_kind, side, size, price, state, created_at,"
        " venue_order_id, token_id, envelope_id) VALUES (?,?,?,?,?,?,'FILLED',?,?,?,?)",
        (command_id, position_id, intent, side, size, price, created, order_id, YES_TOK, envelope),
    )


_LEG_5_20 = {"asset_id": YES_TOK, "side": "BUY", "matched_amount": "5", "price": "0.20"}
_LEG_5_40 = {"asset_id": YES_TOK, "side": "BUY", "matched_amount": "5", "price": "0.40"}


def test_taker_sell_proceeds_use_the_maker_leg_vwap_not_the_top_level_price():
    """10 sh with top-level price 0.20 but legs 5@0.20 + 5@0.40: proceeds $3.00, not $2.00."""
    conn = _leg_conn()
    _cmd(conn, "x1", "EXIT", "SELL", 10, 0.2, "ord-x1", "env-sell")
    _taker_fact(conn, "x1", "ord-x1", "SELL", 0.20, 10, [_LEG_5_20, _LEG_5_40])
    stored = conn.execute("SELECT filled_size, fill_price FROM venue_trade_facts WHERE command_id = 'x1'").fetchone()
    assert (float(stored[0]), float(stored[1])) == (10.0, 0.20)          # the raw fact: $2.00 at the top-level price
    # the ENTRY loader runs the ledger's binding on any fact with maker legs; a SELL command's
    # fact is classified the same way and priced from its legs, never from the rounded top line
    assert me.fill_totals(_fills(conn, ["x1"])["x1"], 10.0)[:2] == (10.0, pytest.approx(3.0))

    fills, debt = me.load_exit_fills(conn, [{"command_id": "x1", "position_id": "p1", "size": 10.0}])
    assert debt == []
    (qty, price, ts), = fills["p1"]
    assert qty == 10.0 and qty * price == pytest.approx(3.0)             # canonical economic exit fill
    assert ts == "2026-10-02T09:00:00+00:00"                             # joined execution clock

    # the report charges the sell its canonical proceeds, so the regret is off by exactly the $1 it was
    entries = [_entry("c1", "p1", "A", 30, size=10.0)]
    td = _td(entries, [_pos("p1", direction="buy_yes", pnl=1.0, phase="economically_closed",
                            exit_reason=None, settled_at=None)],
             {"c1": [(10.0, 0.20, "2026-10-01T10:00:00+00:00")]},
             exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 10.0}])
    td["exit_fills"] = fills
    (row,) = _report(td, _listings("A"))["exits"]
    assert row["proceeds_usd"] == pytest.approx(3.0) and row["cost_usd"] == pytest.approx(2.0)


def test_taker_buy_cost_uses_the_maker_leg_economics_when_the_ledger_has_not_repriced_it():
    """EXACT_TAKER: SELL YES 5@0.30 + BUY NO 5@0.60 (= 0.40 YES) is $3.50 for 10 shares, not 10 x 0.30."""
    conn = _leg_conn()
    _cmd(conn, "b1", "ENTRY", "BUY", 10, 0.3, "ord-b1", "env-buy", created="2026-10-01T10:00:00+00:00")
    _taker_fact(conn, "b1", "ord-b1", "BUY", 0.30, 10,
                [{"asset_id": YES_TOK, "side": "SELL", "matched_amount": "5", "price": "0.30"},
                 {"asset_id": NO_TOK, "side": "BUY", "matched_amount": "5", "price": "0.60"}])
    fills, unverifiable = me.load_economic_fills(conn, ["b1"])
    (qty, price, _ts), = fills["b1"]
    assert qty == 10.0 and qty * price == pytest.approx(3.5) and unverifiable == set()


def test_recognized_taker_buy_whose_legs_do_not_prove_the_full_size_is_unverifiable_not_priced():
    """TAKER_UNVERIFIABLE (exchange_reconcile:2477-2481): legs cover 4 of 10 shares, so the rounded
    top-level price 0.30 must NOT become a known cost; the command is flagged and gets no fill."""
    conn = _leg_conn()
    _cmd(conn, "b1", "ENTRY", "BUY", 10, 0.3, "ord-b1", "env-buy", created="2026-10-01T10:00:00+00:00")
    _taker_fact(conn, "b1", "ord-b1", "BUY", 0.30, 10,
                [{"asset_id": YES_TOK, "side": "SELL", "matched_amount": "4", "price": "0.30"}])
    fills, unverifiable = me.load_economic_fills(conn, ["b1"])
    assert unverifiable == {"b1"} and fills == {}


def test_non_taker_legacy_buy_with_maker_legs_keeps_its_canonical_price():
    """LEGACY_NON_TAKER: we were the maker (our order is one of maker_orders), so the stored price stands."""
    conn = _leg_conn()
    _cmd(conn, "b1", "ENTRY", "BUY", 10, 0.3, "ord-b1", "env-buy", created="2026-10-01T10:00:00+00:00")
    raw = {"asset_id": YES_TOK, "side": "SELL", "trader_side": "MAKER", "taker_order_id": "someone-else",
           "market": "cond-1", "filled_size": "10", "price": "0.30",
           "maker_orders": [{"order_id": "ord-b1", "asset_id": YES_TOK, "side": "BUY", "matched_amount": "10",
                             "price": "0.30"}]}
    conn.execute(
        "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,"
        " tx_hash, source, observed_at, local_sequence, raw_payload_hash, raw_payload_json)"
        " VALUES ('t1','ord-b1','b1','CONFIRMED','10','0.30',NULL,'WS_USER','2026-10-01T10:00:05+00:00',1,'h',?)",
        (json.dumps(raw),),
    )
    fills, unverifiable = me.load_economic_fills(conn, ["b1"])
    assert [f[:2] for f in fills["b1"]] == [(10.0, 0.30)] and unverifiable == set()


def test_taker_on_our_order_with_no_maker_orders_field_is_unverifiable_not_priced():
    """Sol's case: names TAKER on our order, carries NO maker_orders, top-level 10@0.3. The ledger's
    binding returns TAKER_UNVERIFIABLE; the report must not price it at the rounded top-level price."""
    from src.execution.exchange_reconcile import _trade_fill_economics_binding

    conn = _leg_conn()
    _cmd(conn, "b1", "ENTRY", "BUY", 10, 0.3, "ord-b1", "env-buy", created="2026-10-01T10:00:00+00:00")
    raw = {"asset_id": YES_TOK, "side": "BUY", "trader_side": "TAKER", "taker_order_id": "ord-b1",
           "market": "cond-1", "filled_size": "10", "price": "0.30"}                  # no maker_orders at all
    conn.execute(
        "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,"
        " tx_hash, source, observed_at, local_sequence, raw_payload_hash, raw_payload_json)"
        " VALUES ('t1','ord-b1','b1','CONFIRMED','10','0.30',NULL,'WS_USER','2026-10-01T10:00:05+00:00',1,'h',?)",
        (json.dumps(raw),),
    )
    cmd = me._command_rows(conn, ["b1"])["b1"]
    assert _trade_fill_economics_binding(conn, command=cmd, raw=raw, venue_order_id="ord-b1").state == "TAKER_UNVERIFIABLE"
    fills, unverifiable = me.load_economic_fills(conn, ["b1"])
    assert unverifiable == {"b1"} and fills == {}


def test_fact_with_neither_maker_legs_nor_a_taker_role_keeps_its_canonical_price():
    conn = _facts_conn([("c1", "t1", "CONFIRMED", 5, 0.3, None, 1)])        # raw_payload_json is NULL
    fills, unverifiable = me.load_economic_fills(conn, ["c1"])
    assert [f[:2] for f in fills["c1"]] == [(5.0, 0.3)] and unverifiable == set()
    conn.execute("UPDATE venue_trade_facts SET raw_payload_json = ''")
    assert [f[:2] for f in _fills(conn, ["c1"])["c1"]] == [(5.0, 0.3)]
    conn.execute("UPDATE venue_trade_facts SET raw_payload_json = '{\"price\": \"0.3\"}'")      # no role named
    assert [f[:2] for f in _fills(conn, ["c1"])["c1"]] == [(5.0, 0.3)]


def test_unparseable_entry_payload_proves_no_role_and_is_unverifiable_not_priced():
    conn = _facts_conn([("c1", "t1", "CONFIRMED", 5, 0.3, None, 1)])
    conn.execute("UPDATE venue_trade_facts SET raw_payload_json = '{not json'")
    fills, unverifiable = me.load_economic_fills(conn, ["c1"])
    assert unverifiable == {"c1"} and fills == {}


def test_unverifiable_entry_makes_every_sell_of_the_position_cost_unknown():
    buys = [(10.0, 0.20, "2026-10-01T10:00:00+00:00")]
    sells = [(5.0, 0.30, "2026-10-01T11:00:00+00:00"), (5.0, 0.40, "2026-10-01T12:00:00+00:00")]
    ok = me.sell_costs(buys, sells)
    assert [round(s["cost_usd"], 6) for s in ok] == [1.0, 1.0]
    bad = me.sell_costs(buys, sells, buys_unverifiable=True)
    assert [s["cost_usd"] for s in bad] == [None, None]
    assert [s["unknown"] for s in bad] == ["unverifiable_entry_cost"] * 2
    assert bad[1]["proceeds_usd"] == pytest.approx(2.0)                    # proceeds still reported


def test_report_counts_unverifiable_entry_positions_and_never_prices_their_sells():
    entries = [_entry("c1", "p1", "A", 10, size=10.0)]
    fills = {"c1": [(10.0, 0.30, "2026-10-01T10:00:00+00:00")], "x1": [(5.0, 0.50, "2026-10-01T14:00:00+00:00")]}
    td = _td(entries, [_pos("p1", direction="buy_yes", pnl=0.0, phase="economically_closed", exit_reason=None,
                            settled_at=None)], fills,
             exit_cmds=[{"command_id": "x1", "position_id": "p1", "size": 5.0}])
    td["entry_unverifiable"] = {"c1"}
    rep = _report(td, _listings("A"))
    (row,) = rep["exits"]
    assert row["cost_usd"] is None and row["sells"][0]["unknown"] == "unverifiable_entry_cost"
    total = next(t for t in rep["totals"] if t["metric"] == "high")
    assert total["exit_cost_unknown"] == 1 and total["exit_pnl_usd"] == 0.0
    assert rep["coverage"]["exit_positions_unverifiable_entry_cost"] == 1
    assert rep["coverage"]["entry_commands_taker_unverifiable"] == 1


# ---- the child's temp files after a watchdog kill ----------------------------------------------
def test_stale_tmp_artifacts_of_a_killed_run_are_removed_at_the_next_write(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for name in ("multiday_evaluation.md.tmp", "multiday_evaluation.json.tmp"):
        (out / name).write_text("half written")
    (out / "unrelated.tmp").write_text("keep")
    assert sorted(me.clear_stale_tmp(out)) == ["multiday_evaluation.json.tmp", "multiday_evaluation.md.tmp"]
    assert sorted(p.name for p in out.iterdir()) == ["unrelated.tmp"]
    assert me.clear_stale_tmp(out) == []                                 # nothing left, nothing to do


def test_a_run_clears_a_stale_tmp_before_writing_and_leaves_only_the_artifacts(tmp_path):
    dbs = _make_dbs(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "multiday_evaluation.json.tmp").write_text("left by a watchdog kill")
    _run(tmp_path, dbs)
    # the two artifacts, plus the lock file that is deliberately never removed
    assert sorted(p.name for p in out.iterdir()) == [
        "multiday_evaluation.json", "multiday_evaluation.md", me.WRITE_LOCK_NAME]


def test_watchdog_kill_between_write_and_replace_leaves_the_tmp_and_the_next_run_removes_it(tmp_path):
    """The claim being fixed: a kill does NOT clean up after itself. faulthandler exits without
    running finally or atexit, so the fixed-name .tmp stays until the next run."""
    import subprocess

    out = tmp_path / "out"
    out.mkdir()
    code = (
        "import faulthandler, time, pathlib\n"
        f"faulthandler.dump_traceback_later(1.0, exit=True)\n"
        f"p = pathlib.Path({str(out / 'multiday_evaluation.json.tmp')!r})\n"
        "try:\n"
        "    p.write_text('x')\n"
        "    time.sleep(30)          # the kill lands between write_text and os.replace\n"
        "finally:\n"
        "    p.unlink()              # would clean up if finally ran\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1 and (out / "multiday_evaluation.json.tmp").exists()   # the claim 'nothing left' was false
    assert me.clear_stale_tmp(out) == ["multiday_evaluation.json.tmp"]


# ------------------------------------------------------- one writer at a time (flock)
def test_write_lock_is_exclusive_non_blocking_and_released_on_exit(tmp_path):
    with me._write_lock(tmp_path):
        with pytest.raises(me.OutputBusy):
            with me._write_lock(tmp_path):                      # a second holder is refused at once
                pytest.fail("two holders of the same lock")
    with me._write_lock(tmp_path):                              # released: acquirable again
        pass
    assert (tmp_path / me.WRITE_LOCK_NAME).exists()            # the lock file is never removed


def test_write_lock_is_released_by_the_kernel_when_its_holder_is_killed(tmp_path):
    import subprocess

    code = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(me.PROJECT_ROOT)!r})\n"
        "from pathlib import Path\n"
        "from scripts import multiday_evaluation as me\n"
        f"with me._write_lock(Path({str(tmp_path)!r})):\n"
        "    print('held', flush=True)\n"
        "    time.sleep(60)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(me.OutputBusy):
            with me._write_lock(tmp_path):
                pass
    finally:
        holder.kill()                                           # SIGKILL: no finally, no atexit
        holder.wait()
    with me._write_lock(tmp_path):                              # no stale lock to clear
        pass


def test_a_run_that_cannot_take_the_lock_touches_nothing_and_exits_busy(tmp_path):
    import subprocess

    dbs = _make_dbs(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "multiday_evaluation.json.tmp").write_text("a live writer's temp file")
    with me._write_lock(out):                                   # another run is mid-write
        proc = subprocess.run(
            [sys.executable, str(me.PROJECT_ROOT / "scripts" / "multiday_evaluation.py"), "--quiet",
             "--since", "2026-09-28", "--trades-db", str(dbs[0]), "--forecasts-db", str(dbs[1]),
             "--world-db", str(dbs[2]), "--out-dir", str(out)],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        assert proc.returncode == me.EXIT_DB_BUSY, proc.stderr
        assert "output busy" in proc.stderr
        # the busy run neither cleaned up the live .tmp nor wrote anything
        assert (out / "multiday_evaluation.json.tmp").read_text() == "a live writer's temp file"
        assert not (out / "multiday_evaluation.json").exists() and not (out / "multiday_evaluation.md").exists()


def test_cleanup_only_runs_inside_the_lock_so_a_live_tmp_is_never_unlinked(tmp_path):
    """Sol's race: run B cleans up between run A's write_text and replace. With the lock, B cannot
    reach cleanup while A holds it, and A's replace never sees a missing .tmp."""
    out = tmp_path / "out"
    out.mkdir()
    with me._write_lock(out):                                   # run A, between write_text and replace
        tmp = out / "multiday_evaluation.json.tmp"
        tmp.write_text("A's half-written artifact")
        with pytest.raises(me.OutputBusy):                      # run B is refused before any cleanup
            with me._write_lock(out):
                me.clear_stale_tmp(out)
        assert tmp.exists()
        os.replace(tmp, out / "multiday_evaluation.json")        # A's replace succeeds
    assert (out / "multiday_evaluation.json").read_text() == "A's half-written artifact"


def test_two_concurrent_real_writers_never_corrupt_or_unlink_each_others_output(tmp_path):
    """Two processes write the same output dir at once, many times over: every run either writes both
    artifacts completely or exits 75 having touched nothing; the artifacts are always valid and no
    run ever dies on a FileNotFoundError."""
    import subprocess

    dbs = _make_dbs(tmp_path)
    out = tmp_path / "out"
    cmd = [sys.executable, str(me.PROJECT_ROOT / "scripts" / "multiday_evaluation.py"), "--quiet",
           "--since", "2026-09-28", "--trades-db", str(dbs[0]), "--forecasts-db", str(dbs[1]),
           "--world-db", str(dbs[2]), "--out-dir", str(out)]
    codes: list[int] = []
    errs: list[str] = []
    for _ in range(4):
        procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  stdin=subprocess.DEVNULL) for _ in range(3)]
        for p in procs:
            _o, e = p.communicate(timeout=120)
            codes.append(p.returncode)
            errs.append(e)
        assert all(c in (0, me.EXIT_DB_BUSY) for c in codes), (codes, errs[-3:])
        doc = json.loads((out / "multiday_evaluation.json").read_text())
        assert doc["schema"] == me.SCHEMA                                  # never a torn file
        assert (out / "multiday_evaluation.md").read_text().startswith("# Multi-day evaluation")
        assert not list(out.glob("*.tmp"))                                 # no temp left after clean exits
    assert 0 in codes and not any("FileNotFoundError" in e for e in errs)


def test_deterministic_interleaving_of_two_runs_write_phases(tmp_path, monkeypatch):
    """A writes its .tmp and pauses before os.replace; B then tries its whole cleanup + write phase.
    B must be refused before cleanup (so A's .tmp survives) and A's replace must succeed.
    Against the pre-lock code this exact interleaving made A die with FileNotFoundError."""
    import threading

    out = tmp_path / "out"
    out.mkdir()
    a_wrote, b_done = threading.Event(), threading.Event()
    result: dict = {}
    real_replace = os.replace

    def paused_replace(src, dst):
        a_wrote.set()
        assert b_done.wait(10)
        return real_replace(src, dst)

    def run_a():
        try:
            with me._write_lock(out):
                monkeypatch.setattr(me.os, "replace", paused_replace)
                me._atomic_write(out / "multiday_evaluation.json", '{"schema": "A"}')
            result["A"] = "ok"
        except Exception as exc:                                   # noqa: BLE001
            result["A"] = type(exc).__name__
        finally:
            monkeypatch.setattr(me.os, "replace", real_replace)

    def run_b():
        assert a_wrote.wait(10)
        try:
            with me._write_lock(out):
                me.clear_stale_tmp(out)
                result["B"] = "cleaned"
        except me.OutputBusy:
            result["B"] = "busy"
        finally:
            b_done.set()

    ta, tb = threading.Thread(target=run_a), threading.Thread(target=run_b)
    ta.start()
    tb.start()
    ta.join(20)
    tb.join(20)
    assert result == {"A": "ok", "B": "busy"}
    assert json.loads((out / "multiday_evaluation.json").read_text()) == {"schema": "A"}


def test_unlocked_concurrent_cleanup_reproduces_the_race_this_lock_prevents(tmp_path):
    """Pins the failure being fixed: without the lock, cleanup between write_text and replace makes the
    first writer's replace raise FileNotFoundError."""
    out = tmp_path / "out"
    out.mkdir()
    tmp = out / "multiday_evaluation.json.tmp"
    tmp.write_text("A")                                         # A: write_text done
    me.clear_stale_tmp(out)                                     # B: cleanup, no lock held
    with pytest.raises(FileNotFoundError):
        os.replace(tmp, out / "multiday_evaluation.json")       # A: replace
