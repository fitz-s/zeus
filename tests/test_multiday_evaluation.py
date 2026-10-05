# Created: 2026-10-05
# Last reused/audited: 2026-10-05
# Authority basis: operator law 2026-10-05 (records and continuous evaluation only);
#   scripts/multiday_evaluation.py is read-only, report-only, outside the money path.
"""Bucketing, HIGH/LOW split, unknown listing time, sell-vs-hold regret, fail-soft."""
from __future__ import annotations

import json
import sqlite3
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


def _fill(price, size=5.0, *_ignored):
    """One economic fill as load_economic_fills returns it: (filled_size, fill_price)."""
    return (size, price)


def _pos(pid, *, metric="high", phase="settled", direction="buy_yes", pnl=None, cost=1.0,
         entry_price=0.2, settlement_price=None, exit_reason="SETTLEMENT", settled_at="2026-10-03T01:00:00+00:00"):
    return {
        "position_id": pid, "phase": phase, "city": "Tokyo", "target_date": "2026-10-02",
        "metric": metric, "direction": direction, "cost_basis_usd": cost, "entry_price": entry_price,
        "realized_pnl_usd": pnl, "settled_at": settled_at, "settlement_price": settlement_price,
        "exit_reason": exit_reason,
    }


def _td(entries, positions, fills, *, entry_q=None, exit_cmds=(), exit_reasons=None, revals=()):
    return {
        "entries": entries, "positions": positions, "fills": fills,
        "entry_q": entry_q or {}, "exit_cmds": list(exit_cmds),
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
    assert sorted(me.load_economic_fills(conn, ["c1"])["c1"]) == [(2.0, 0.2), (3.0, 0.3)]
    assert me.fill_totals(me.load_economic_fills(conn, ["c1"])["c1"], 5.0)[:2] == (5.0, pytest.approx(1.3))


def test_equal_size_children_without_tx_hash_both_count():
    """Counterexample 2: two 5@0.4 children with no tx_hash are two fills, not one."""
    conn = _facts_conn([
        ("c1", "child-a", "CONFIRMED", 5, 0.4, None, 1),
        ("c1", "child-b", "CONFIRMED", 5, 0.4, None, 1),
    ])
    assert me.fill_totals(me.load_economic_fills(conn, ["c1"])["c1"], 10.0)[:2] == (10.0, pytest.approx(4.0))


def test_lifecycle_revisions_and_tx_aggregate_alias_count_once():
    conn = _facts_conn([
        ("c1", "t1", "MATCHED", 4, 0.30, TX, 1),
        ("c1", "t1", "MINED", 4, 0.30, TX, 2),
        ("c1", "t1", "CONFIRMED", 4, 0.3012345, TX, 3),   # lifecycle revisions of ONE trade
        ("c1", TX, "MATCHED", 4, 0.30, TX, 1),            # tx-hash aggregate alias of that child
        ("c1", "t2", "FAILED", 9, 0.9, None, 1),          # never an economic fill
        ("c2", "t3", "CONFIRMED", 1, 0.5, None, 1),
    ])
    fills = me.load_economic_fills(conn, ["c1", "c2"])
    assert fills["c1"] == [(4.0, pytest.approx(0.3012345))]
    assert fills["c2"] == [(1.0, 0.5)]
    assert me.load_economic_fills(conn, ["nope"]) == {}


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
    assert b["world_grade_pnl_usd"] == pytest.approx(0.0) and b["world_grade_n"] == 2


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
            size REAL, price REAL, state TEXT, created_at TEXT);
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
        CREATE TABLE decision_log (mode TEXT, timestamp TEXT, artifact_json TEXT);
        """
    )
    t.execute("INSERT INTO venue_commands VALUES ('c1','s1','p1','ENTRY',5,0.2,'FILLED','2026-10-01T06:00:00+00:00')")
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
    import src.main as main_module

    health: list = []
    monkeypatch.setattr(main_module, "_write_scheduler_health", lambda name, **kw: health.append((name, kw)))
    monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda _name: False)
    monkeypatch.setattr(main_module, "_edli_reactor_active", lambda: False)
    monkeypatch.setattr(
        main_module, "_edli_redecision_screen_lock", SimpleNamespace(locked=lambda: False)
    )
    return main_module, health


def _stub_run(monkeypatch, fn):
    monkeypatch.setattr(me, "run_multiday_evaluation", fn)


def test_tick_runs_the_report_when_money_path_is_idle(tick, monkeypatch):
    main_module, health = tick
    calls = []
    _stub_run(monkeypatch, lambda: calls.append(1) or {"since_target_date": "x", "entries": [], "coverage": {}})
    main_module._multiday_evaluation_tick()
    assert calls == [1] and health[-1] == ("multiday_evaluation", {"failed": False})


@pytest.mark.parametrize("busy", ["reactor", "screen", "monitor"])
def test_tick_defers_to_the_money_path(tick, monkeypatch, busy):
    main_module, _ = tick
    if busy == "reactor":
        monkeypatch.setattr(main_module, "_edli_reactor_active", lambda: True)
    elif busy == "screen":
        monkeypatch.setattr(main_module, "_edli_redecision_screen_lock", SimpleNamespace(locked=lambda: True))
    else:
        monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda _name: True)
    _stub_run(monkeypatch, lambda: pytest.fail("report must not run while the money path is active"))
    main_module._multiday_evaluation_tick()


def test_tick_never_raises_into_the_daemon(tick, monkeypatch):
    main_module, health = tick

    def busy():
        raise sqlite3.OperationalError("database is locked")

    _stub_run(monkeypatch, busy)
    main_module._multiday_evaluation_tick()
    assert health[-1] == ("multiday_evaluation", {"failed": False})  # deferred, not failed

    def broken():
        raise RuntimeError("boom")

    _stub_run(monkeypatch, broken)
    main_module._multiday_evaluation_tick()  # _scheduler_job swallows and records
    assert health[-1][1]["failed"] is True and health[-1][1]["reason"] == "boom"


def test_job_is_registered_daily_and_classified_non_collection():
    import re
    from pathlib import Path

    src = (Path(me.PROJECT_ROOT) / "src" / "main.py").read_text()
    assert re.search(r'_multiday_evaluation_tick, "cron", hour=9, minute=45,\s+id="multiday_evaluation"', src)
    from scripts.data_collection_inventory import _SRC_MAIN_NON_COLLECTION_JOB_IDS

    assert "multiday_evaluation" in _SRC_MAIN_NON_COLLECTION_JOB_IDS
