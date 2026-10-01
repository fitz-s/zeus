# Created: 2026-06-12
# Last reused or audited: 2026-10-01
# Lifecycle: created=2026-06-12; last_reviewed=2026-10-01; last_reused=2026-08-08
# Authority basis: operator stagnation root-cause 2026-06-12 ("continuous redecision没有作用中") +
#   /tmp/continuous_redecision_resurrection.md. RELATIONSHIP antibodies for the P1 deadlock-free
#   belief write, the P2 cheap screen, §4.5 rest management, and the EDLI_REDECISION_PENDING consume
#   path (forecast-lane acceptance under all scopes + the strategy classifier).
"""Antibodies for the continuous re-decision resurrection (deadlock-free P1 + P2 + §4.5)."""
from __future__ import annotations

import sqlite3

import pytest

import src.events.continuous_redecision as cr


def _mem_world() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cr.ensure_belief_cache_schema(conn)
    return conn


def _mem_trade() -> sqlite3.Connection:
    """A minimal executable_market_snapshots table (the columns the price reader needs)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            orderbook_top_bid TEXT,
            orderbook_top_ask TEXT,
            min_order_size TEXT,
            freshness_deadline TEXT,
            captured_at TEXT
        )
        """
    )
    conn.commit()
    return conn


class _SqlCaptureConn:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, sql, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.statements.append(str(sql))
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def _cache(conn, *, family_id="hyp|live|Wuhan|2026-06-12|high|disc", p_yes=0.99,
           snapshot_id="snap1", cond="0xc30", recorded_at="2026-06-12T00:00:00+00:00",
           temperature_metric="high"):
    cr.cache_belief(
        conn,
        family_id=family_id, city="Wuhan", target_date="2026-06-12",
        snapshot_id=snapshot_id, calibrator_model_hash="identity",
        bin_labels=["b29", "b30"], p_posterior_vec=[0.001, p_yes],
        recorded_at=recorded_at, temperature_metric=temperature_metric,
        condition_ids=["0xc29", cond],
    )

def test_belief_reads_use_indexable_prefix_ranges_not_like_scans():
    world = _mem_world()
    family_id = "hyp|live|Wuhan|2026-06-12|high|disc"
    _cache(
        world,
        family_id=family_id,
        p_yes=0.70,
        snapshot_id="old",
        recorded_at="2026-06-12T00:00:00+00:00",
    )
    _cache(
        world,
        family_id=family_id,
        p_yes=0.80,
        snapshot_id="new",
        recorded_at="2026-06-12T01:00:00+00:00",
    )
    captured = _SqlCaptureConn(world)

    latest = cr.latest_cached_belief(captured, family_id=family_id)
    beliefs = cr._all_latest_beliefs(captured)

    assert latest is not None
    assert latest.snapshot_id == "new"
    assert len(beliefs) == 1
    statements = "\n".join(captured.statements).upper()
    probability_reads = [
        stmt for stmt in captured.statements
        if "FROM probability_trace_fact" in stmt
    ]
    assert probability_reads
    for stmt in probability_reads:
        upper = stmt.upper()
        assert " LIKE " not in upper
        assert "DECISION_ID >= ?" in upper
        assert "DECISION_ID < ?" in upper
    all_latest = next(
        stmt
        for stmt in probability_reads
        if "LATEST_TRACE AS MATERIALIZED" in stmt.upper()
    )
    inner_select = all_latest.split(")", 1)[0].upper()
    assert "P_POSTERIOR_JSON" not in inner_select
    assert "BIN_LABELS_JSON" not in inner_select
    assert "ROW_NUMBER" not in statements
    assert "PARTITION BY" not in statements


def test_screen_entry_reuses_supplied_beliefs_without_probability_trace_read():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.99, cond="0xc30")
    _snapshot(trade, bid="0.30", ask="0.70", selected_outcome_token_id="yes-c30")
    beliefs = cr._all_latest_beliefs(world)
    captured_world = _SqlCaptureConn(world)

    fired = cr.screen_entry_redecisions(
        captured_world,
        trade,
        decision_time="2026-06-12T00:45:00+00:00",
        min_edge=0.01,
        beliefs=beliefs,
    )

    assert len(fired) == 1
    assert all("probability_trace_fact" not in stmt for stmt in captured_world.statements)


def _snapshot(
    conn,
    *,
    condition_id="0xc30",
    yes_token_id="yes-c30",
    no_token_id="no-c30",
    selected_outcome_token_id="yes-c30",
    bid="0.70",
    ask="0.72",
    snapshot_id="s1",
    min_order_size="5",
    freshness_deadline="2026-06-12T02:00:00+00:00",
    captured_at="2026-06-12T00:30:00+00:00",
):
    conn.execute(
        "INSERT INTO executable_market_snapshots "
        "(snapshot_id, condition_id, yes_token_id, no_token_id, selected_outcome_token_id, "
        "orderbook_top_bid, orderbook_top_ask, min_order_size, freshness_deadline, captured_at) VALUES "
        "(?,?,?,?,?,?,?,?,?,?)",
        (
            snapshot_id,
            condition_id,
            yes_token_id,
            no_token_id,
            selected_outcome_token_id,
            bid,
            ask,
            min_order_size,
            freshness_deadline,
            captured_at,
        ),
    )
    conn.commit()


def _regret_table(conn):
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            regret_event_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            rejection_stage TEXT NOT NULL,
            rejection_reason TEXT NOT NULL,
            regret_bucket TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            family_id TEXT,
            bin_label TEXT,
            direction TEXT,
            q_lcb_5pct REAL,
            c_fee_adjusted REAL,
            trade_score REAL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


# ───────────────────────────────────────────────────────────────────────────────────────────────
# ANTIBODY 1 — DEADLOCK REGRESSION: the belief write must NOT open a second connection / commit.
# This pins the 2026-05-31 self-deadlock (persist_belief_live opened get_world_connection() and
# committed WHILE the reactor held the world WAL write lock) as STRUCTURALLY IMPOSSIBLE: the kernel
# path must write through the GIVEN conn with no sqlite3.connect() and no commit() of its own.
# ───────────────────────────────────────────────────────────────────────────────────────────────
class _CommitCountingConn:
    """Wrap a real sqlite3 connection, counting commit() calls and forbidding new connections.

    sqlite3.Connection.commit is a read-only C attribute (cannot be monkeypatched directly), so we
    proxy. A second sqlite3.connect() inside the window is the deadlock — pinned by the connect
    patch in the test."""

    def __init__(self, conn):
        self._conn = conn
        self.commit_count = 0

    def commit(self):
        self.commit_count += 1
        return self._conn.commit()

    def execute(self, *a, **k):
        return self._conn.execute(*a, **k)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_belief_write_uses_given_conn_no_second_connection():
    raw = _mem_world()
    proxy = _CommitCountingConn(raw)
    # Simulate the reactor's open write transaction: BEGIN, then write the belief INSIDE it.
    raw.execute("BEGIN IMMEDIATE")

    # Any attempt to open a SECOND connection inside the window is the 2026-05-31 deadlock category.
    real_connect = sqlite3.connect

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("write_belief_row opened a SECOND sqlite connection — deadlock category")

    sqlite3.connect = _boom  # type: ignore[assignment]
    try:
        cr.write_belief_row(
            proxy,
            family_id="hyp|live|Wuhan|2026-06-12|high|disc", city="Wuhan", target_date="2026-06-12",
            snapshot_id="snap1", calibrator_model_hash="identity",
            bin_labels=["b29", "b30"], p_posterior_vec=[0.1, 0.9],
            recorded_at="2026-06-12T00:00:00+00:00", condition_ids=["0xc29", "0xc30"],
        )
    finally:
        sqlite3.connect = real_connect  # type: ignore[assignment]

    assert proxy.commit_count == 0, "write_belief_row must NOT commit — the reactor's window owns the commit"
    # The row is visible on THIS conn (same txn) before any commit — the in-transaction write.
    row = raw.execute(
        "SELECT decision_id FROM probability_trace_fact WHERE decision_id LIKE 'edli_belief:%'"
    ).fetchone()
    assert row is not None, "belief row must be present in the open transaction"
    raw.execute("ROLLBACK")


def test_screen_entry_uses_live_regret_backoff_from_world_table():
    from datetime import datetime, timezone

    world = _mem_world()
    trade = _mem_trade()
    _regret_table(world)
    family_id = "hyp|live|Wuhan|2026-06-12|high|disc"
    _cache(world, family_id=family_id, p_yes=0.90, cond="0xc30")
    _snapshot(trade, condition_id="0xc30", bid="0.20", ask="0.70")
    created_at = datetime.now(timezone.utc).isoformat()
    prior_all_in_cost = cr._all_in_cost(0.70)
    world.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            city, target_date, metric, family_id, bin_label, direction,
            q_lcb_5pct, c_fee_adjusted, trade_score, created_at
        ) VALUES (
            'r1', 'event-1', 'TRADE_SCORE', 'TRADE_SCORE_NON_POSITIVE:score=-0.01', 'FEE_ERASED_EDGE',
            'Wuhan', '2026-06-12', 'high', ?, 'b30', 'buy_yes',
            0.90, ?, -0.01, ?
        )
        """,
        (family_id, prior_all_in_cost, created_at),
    )
    world.commit()

    blocked = cr.screen_entry_redecisions(
        world,
        trade,
        decision_time="2026-06-12T00:30:00+00:00",
        min_edge=0.01,
        beliefs=cr._all_latest_beliefs(world),
    )
    assert blocked == []

    trade.execute("DELETE FROM executable_market_snapshots")
    _snapshot(trade, condition_id="0xc30", bid="0.20", ask="0.67", snapshot_id="s2")
    improved = cr.screen_entry_redecisions(
        world,
        trade,
        decision_time="2026-06-12T00:30:00+00:00",
        min_edge=0.01,
        beliefs=cr._all_latest_beliefs(world),
    )
    assert len(improved) == 1


def test_persist_belief_live_removed():
    """The deadlock-causing entry point must be GONE (replaced by write_belief_row)."""
    assert not hasattr(cr, "persist_belief_live"), (
        "persist_belief_live (second-connection write) must not be reintroduced"
    )


# ───────────────────────────────────────────────────────────────────────────────────────────────
# ANTIBODY 2 — SCREEN FIRES on an edge-appeared fixture (price drops → positive edge → enqueue).
# Reads cached belief (world) × freshest executable price (trade) end-to-end.
# ───────────────────────────────────────────────────────────────────────────────────────────────
def test_entry_screen_fires_on_edge_appeared():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.99, cond="0xc30")
    # Fresh executable snapshot: YES ask 0.70 → edge = 0.99 - 0.70 - fee ≈ +0.28.
    _snapshot(trade, bid="0.30", ask="0.70", selected_outcome_token_id="yes-c30")
    fired = cr.screen_entry_redecisions(
        world,
        trade,
        decision_time="2026-06-12T00:45:00+00:00",
        min_edge=0.01,
        beliefs=cr._all_latest_beliefs(world),
    )
    keys = {(e.family_id, e.bin_label, e.direction) for e in fired}
    assert ("hyp|live|Wuhan|2026-06-12|high|disc", "b30", "buy_yes") in keys


def test_stale_entry_price_requests_refresh_without_emit():
    """A stale executable book is not a no-edge verdict; it must refresh first.

    Regression: the live screen skipped stale quotes before confirmation refresh,
    so once most executable snapshots expired only the one family with a fresh
    sidecar row could ever be re-evaluated. This helper feeds confirmation
    refresh; the post-refresh screen still owns whether any order-worthy edge
    exists.
    """

    world = _mem_world()
    trade = _mem_trade()
    family_id = "hyp|live|Wuhan|2026-06-12|high|disc"
    _cache(world, family_id=family_id, p_yes=0.99, cond="0xc30")
    _snapshot(
        trade,
        condition_id="0xc30",
        bid="0.30",
        ask="0.70",
        selected_outcome_token_id="yes-c30",
        freshness_deadline="2026-06-12T00:10:00+00:00",
    )
    beliefs = cr._all_latest_beliefs(
        world,
        decision_time="2026-06-12T00:45:00+00:00",
    )

    fired = cr.screen_entry_redecisions(
        world,
        trade,
        decision_time="2026-06-12T00:45:00+00:00",
        min_edge=0.01,
        beliefs=beliefs,
    )
    refresh_scope = cr.entry_substrate_refresh_scope(
        trade,
        beliefs=beliefs,
        decision_time="2026-06-12T00:45:00+00:00",
    )

    assert fired == []
    assert refresh_scope == {("Wuhan", "2026-06-12", "high"): {"0xc30"}}


def test_entry_screen_fires_on_buy_no_edge_appeared():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.05, cond="0xc30")
    # YES bid 0.30 implies NO ask 0.70; NO posterior is 0.95.
    _snapshot(trade, bid="0.30", ask="0.72", selected_outcome_token_id="yes-c30")
    fired = cr.screen_entry_redecisions(
        world,
        trade,
        decision_time="2026-06-12T00:45:00+00:00",
        min_edge=0.01,
        beliefs=cr._all_latest_beliefs(world),
    )
    keys = {(e.family_id, e.bin_label, e.direction) for e in fired}
    assert ("hyp|live|Wuhan|2026-06-12|high|disc", "b30", "buy_no") in keys


def test_screened_family_keys_uses_persisted_metric_for_hash_family_id():
    world = _mem_world()
    family_id = "edli_family_hash_without_metric"
    _cache(world, family_id=family_id, temperature_metric="low")

    keys = cr.screened_family_keys(
        world,
        [cr.EnqueuedRedecision(family_id, "b30", "buy_yes", 0.12)],
    )

    assert keys == {("Wuhan", "2026-06-12", "low")}


def test_entry_screen_silent_when_no_edge():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.55, cond="0xc30")
    # YES ask 0.72 → edge = 0.55 - 0.72 - fee < 0 → no enqueue.
    _snapshot(trade, bid="0.20", ask="0.72", selected_outcome_token_id="yes-c30")
    fired = cr.screen_entry_redecisions(
        world, trade, decision_time="2026-06-12T00:45:00+00:00", min_edge=0.01,
    )
    assert all(e.direction != "buy_yes" or e.family_id != "hyp|live|Wuhan|2026-06-12|high|disc"
               for e in fired)


def test_price_reader_uses_bounded_condition_seeks_not_window_sort():
    trade = _mem_trade()
    _snapshot(
        trade,
        condition_id="0xc30",
        bid="0.10",
        ask="0.90",
        snapshot_id="old",
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshots
        (snapshot_id, condition_id, yes_token_id, no_token_id, selected_outcome_token_id,
         orderbook_top_bid, orderbook_top_ask, freshness_deadline, captured_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            "new",
            "0xc30",
            "yes-c30",
            "no-c30",
            "yes-c30",
            "0.70",
            "0.72",
            "2026-06-12T02:00:00+00:00",
            "2026-06-12T00:31:00+00:00",
        ),
    )
    trade.commit()
    captured = _SqlCaptureConn(trade)

    quotes = cr.read_freshest_executable_prices(captured, condition_ids={"0xc30"})

    sql_text = "\n".join(captured.statements).upper()
    assert "ROW_NUMBER" not in sql_text
    assert "PARTITION BY" not in sql_text
    assert quotes[("0xc30", "buy_yes")].price == 0.72
    assert quotes[("0xc30", "buy_no")].price == pytest.approx(0.30)


def test_price_reader_uses_native_selected_outcome_books():
    trade = _mem_trade()
    _snapshot(
        trade,
        condition_id="0xc30",
        selected_outcome_token_id="yes-c30",
        bid="0.30",
        ask="0.32",
        snapshot_id="yes-native",
    )
    _snapshot(
        trade,
        condition_id="0xc30",
        selected_outcome_token_id="no-c30",
        bid="0.68",
        ask="0.70",
        snapshot_id="no-native",
    )

    quotes = cr.read_freshest_executable_prices(trade, condition_ids={"0xc30"})

    assert quotes[("0xc30", "buy_yes")].price == pytest.approx(0.32)
    assert quotes[("0xc30", "buy_no")].price == pytest.approx(0.70)


def test_price_reader_does_not_treat_gamma_active_label_as_tradeability():
    trade = _mem_trade()
    trade.execute("ALTER TABLE executable_market_snapshots ADD COLUMN active INTEGER")
    trade.execute("ALTER TABLE executable_market_snapshots ADD COLUMN enable_orderbook INTEGER")
    trade.execute("ALTER TABLE executable_market_snapshots ADD COLUMN closed INTEGER")
    trade.execute("ALTER TABLE executable_market_snapshots ADD COLUMN accepting_orders INTEGER")
    trade.execute(
        """
        INSERT INTO executable_market_snapshots
        (snapshot_id, condition_id, yes_token_id, no_token_id, selected_outcome_token_id,
         orderbook_top_bid, orderbook_top_ask, freshness_deadline, captured_at,
         active, enable_orderbook, closed, accepting_orders)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "active-routing-label-false",
            "0xc30",
            "yes-c30",
            "no-c30",
            "yes-c30",
            "0.30",
            "0.32",
            "2026-06-12T02:00:00+00:00",
            "2026-06-12T00:30:00+00:00",
            0,
            1,
            0,
            1,
        ),
    )

    quotes = cr.read_freshest_executable_prices(trade, condition_ids={"0xc30"})

    assert quotes[("0xc30", "buy_yes")].price == pytest.approx(0.32)


def test_price_reader_skips_market_channel_invalidated_snapshots():
    trade = _mem_trade()
    _snapshot(
        trade,
        condition_id="0xc30",
        selected_outcome_token_id="yes-c30",
        bid="0.30",
        ask="0.32",
        snapshot_id="old",
        captured_at="2026-06-12T00:30:00+00:00",
    )
    trade.execute(
        """
        CREATE TABLE executable_market_snapshot_invalidations (
          invalidation_id TEXT PRIMARY KEY,
          condition_id TEXT,
          token_id TEXT,
          reason TEXT NOT NULL,
          invalidated_at TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshot_invalidations
        VALUES (?,?,?,?,?,?)
        """,
        (
            "inv-1",
            "0xc30",
            None,
            "tick_size_change",
            "2026-06-12T00:31:00+00:00",
            "2026-06-12T00:31:00+00:00",
        ),
    )

    quotes = cr.read_freshest_executable_prices(trade, condition_ids={"0xc30"})

    assert ("0xc30", "buy_yes") not in quotes


# ───────────────────────────────────────────────────────────────────────────────────────────────
# ANTIBODY 3 — REST PULL fires on belief-decay (NEW evidence), HOLDS on same-snapshot wiggle.
# ───────────────────────────────────────────────────────────────────────────────────────────────
def test_open_maker_rests_preserve_no_token_direction_and_held_side_posterior():
    from src.events import reactor

    world = _mem_world()
    trade = _mem_trade()
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT, "
        "side TEXT, price REAL, snapshot_id TEXT, created_at TEXT, intent_kind TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, local_sequence INTEGER)"
    )
    _cache(world, p_yes=0.20, snapshot_id="snap1", cond="0xc30")
    _snapshot(
        trade,
        condition_id="0xc30",
        yes_token_id="yes-c30",
        no_token_id="no-c30",
        selected_outcome_token_id="no-c30",
        bid="0.18",
        ask="0.22",
        snapshot_id="snap1",
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "cmd-no",
            "order-no",
            "no-c30",
            "m1",
            "BUY",
            0.75,
            "snap1",
            "2026-06-12T00:00:00+00:00",
            "ENTRY",
        ),
    )
    trade.execute("INSERT INTO venue_order_facts VALUES (?,?,?)", ("order-no", "LIVE", 1))
    trade.commit()

    rests = reactor._edli_open_maker_rests_for_screen(trade, world)

    assert len(rests) == 1
    assert rests[0].side == "buy_no"
    assert rests[0].resting_posterior == pytest.approx(0.80)
    assert rests[0].created_at == "2026-06-12T00:00:00+00:00"
    assert rests[0].fact_state == "LIVE"
    assert rests[0].matched_size is None
    assert rests[0].min_order_size == pytest.approx(5.0)


def test_open_maker_rest_recovers_causal_entry_belief_not_latest_belief():
    from src.events import reactor

    world = _mem_world()
    trade = _mem_trade()
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT, "
        "side TEXT, price REAL, snapshot_id TEXT, created_at TEXT, intent_kind TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, local_sequence INTEGER)"
    )
    family_id = "hyp|live|Sao Paulo|2026-08-09|high|disc"
    _cache(
        world,
        family_id=family_id,
        p_yes=0.16,
        snapshot_id="day0-entry",
        cond="0xc26",
        recorded_at="2026-08-09T14:57:35+00:00",
    )
    _cache(
        world,
        family_id=family_id,
        p_yes=0.88,
        snapshot_id="day0-reversal",
        cond="0xc26",
        recorded_at="2026-08-09T15:00:42+00:00",
    )
    _snapshot(
        trade,
        condition_id="0xc26",
        yes_token_id="yes-c26",
        no_token_id="no-c26",
        selected_outcome_token_id="no-c26",
        snapshot_id="clob-entry",
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "cmd-causal",
            "order-causal",
            "no-c26",
            "m1",
            "BUY",
            0.41,
            "clob-entry",
            "2026-08-09T14:57:41+00:00",
            "ENTRY",
        ),
    )
    trade.execute(
        "INSERT INTO venue_order_facts VALUES (?,?,?)",
        ("order-causal", "LIVE", 1),
    )
    trade.commit()

    rests = reactor._edli_open_maker_rests_for_screen(trade, world)

    assert len(rests) == 1
    assert rests[0].resting_posterior == pytest.approx(0.84)
    assert rests[0].resting_snapshot_id == "day0-entry"


def test_open_maker_rests_use_current_trade_fill_when_order_fact_is_stale():
    """A user-channel partial fill must prevent a cancel that would create dust."""
    from src.events import reactor

    world = _mem_world()
    trade = _mem_trade()
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT, "
        "side TEXT, price REAL, snapshot_id TEXT, created_at TEXT, intent_kind TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, matched_size TEXT, local_sequence INTEGER)"
    )
    trade.execute(
        "CREATE TABLE venue_trade_facts ("
        "trade_id TEXT, command_id TEXT, venue_order_id TEXT, state TEXT, filled_size TEXT, "
        "local_sequence INTEGER)"
    )
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, snapshot_id="snap1", min_order_size="5")
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "cmd1", "order1", "yes-c30", "m1", "BUY", 0.70, "snap1",
            "2026-06-12T00:00:00+00:00", "ENTRY",
        ),
    )
    trade.execute(
        "INSERT INTO venue_order_facts VALUES ('order1', 'LIVE', '0', 1)"
    )
    # MATCHED -> CONFIRMED is one trade's state progression, not two fills.
    trade.execute(
        "INSERT INTO venue_trade_facts VALUES "
        "('trade1', 'cmd1', 'order1', 'MATCHED', '1.724135', 1), "
        "('trade1', 'cmd1', 'order1', 'CONFIRMED', '1.724135', 2)"
    )
    trade.commit()

    rests = reactor._edli_open_maker_rests_for_screen(trade, world)

    assert len(rests) == 1
    assert rests[0].matched_size == pytest.approx(1.724135)
    assert rests[0].min_order_size == pytest.approx(5.0)


def test_open_maker_rests_use_exact_snapshot_for_minimum_without_append_scan():
    from src.events import reactor

    world = _mem_world()
    trade = _mem_trade()
    trade.execute(
        "CREATE TABLE executable_market_snapshot_latest ("
        "selected_outcome_token_id TEXT, condition_id TEXT, yes_token_id TEXT, "
        "no_token_id TEXT, captured_at TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT, "
        "side TEXT, price REAL, snapshot_id TEXT, created_at TEXT, intent_kind TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, local_sequence INTEGER)"
    )
    _cache(world, p_yes=0.20, snapshot_id="snap1", cond="0xc30")
    _snapshot(
        trade,
        condition_id="0xc30",
        yes_token_id="yes-c30",
        no_token_id="no-c30",
        selected_outcome_token_id="no-c30",
        bid="0.18",
        ask="0.22",
        snapshot_id="snap1",
    )
    trade.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?,?,?)",
        ("no-c30", "0xc30", "yes-c30", "no-c30", "2026-06-12T00:30:00+00:00"),
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "cmd-no",
            "order-no",
            "no-c30",
            "m1",
            "BUY",
            0.75,
            "snap1",
            "2026-06-12T00:00:00+00:00",
            "ENTRY",
        ),
    )
    trade.execute("INSERT INTO venue_order_facts VALUES (?,?,?)", ("order-no", "LIVE", 1))
    trade.commit()
    captured_trade = _SqlCaptureConn(trade)

    rests = reactor._edli_open_maker_rests_for_screen(captured_trade, world)

    assert len(rests) == 1
    assert rests[0].side == "buy_no"
    assert rests[0].min_order_size == pytest.approx(5.0)
    statements = "\n".join(captured_trade.statements)
    assert "FROM executable_market_snapshot_latest" in statements
    assert "FROM executable_market_snapshots" in statements
    append_reads = [
        statement
        for statement in captured_trade.statements
        if "FROM executable_market_snapshots\n" in statement
    ]
    assert len(append_reads) == 1
    assert "WHERE snapshot_id IN" in append_reads[0]


def test_open_maker_rests_avoids_full_order_fact_window_scan():
    from src.events import reactor

    world = _mem_world()
    trade = _mem_trade()
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT, "
        "side TEXT, price REAL, snapshot_id TEXT, created_at TEXT, intent_kind TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, local_sequence INTEGER)"
    )
    trade.execute(
        "CREATE UNIQUE INDEX idx_test_order_seq "
        "ON venue_order_facts(venue_order_id, local_sequence)"
    )
    _cache(world, p_yes=0.20, snapshot_id="snap1", cond="0xc30")
    _snapshot(
        trade,
        condition_id="0xc30",
        yes_token_id="yes-c30",
        no_token_id="no-c30",
        selected_outcome_token_id="no-c30",
        bid="0.18",
        ask="0.22",
        snapshot_id="snap1",
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "cmd-no",
            "order-no",
            "no-c30",
            "m1",
            "BUY",
            0.75,
            "snap1",
            "2026-06-12T00:00:00+00:00",
            "ENTRY",
        ),
    )
    for seq in range(1, 201):
        trade.execute(
            "INSERT INTO venue_order_facts VALUES (?,?,?)",
            (f"closed-{seq}", "EXPIRED", seq),
        )
    trade.execute("INSERT INTO venue_order_facts VALUES (?,?,?)", ("order-no", "LIVE", 1))
    trade.commit()

    captured = _SqlCaptureConn(trade)

    rests = reactor._edli_open_maker_rests_for_screen(captured, world)

    assert len(rests) == 1
    statements = "\n".join(captured.statements).upper()
    assert "ROW_NUMBER" not in statements
    assert "PARTITION BY" not in statements
    assert "WHERE VENUE_ORDER_ID = ?" in statements


def test_open_maker_rests_skip_unresolved_orders_from_redecision_screen():
    from src.events import reactor

    world = _mem_world()
    trade = _mem_trade()
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT, "
        "side TEXT, price REAL, snapshot_id TEXT, created_at TEXT, intent_kind TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, local_sequence INTEGER)"
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "cmd-unresolved",
            "order-unresolved",
            "token-not-in-snapshot",
            "m1",
            "BUY",
            0.75,
            "snap1",
            "2026-06-12T00:00:00+00:00",
            "ENTRY",
        ),
    )
    trade.execute("INSERT INTO venue_order_facts VALUES (?,?,?)", ("order-unresolved", "LIVE", 1))
    trade.commit()

    rests = reactor._edli_open_maker_rests_for_screen(trade, world)

    assert rests == []


def test_open_rest_families_are_priority_warm_inputs_without_fact_window_scan():
    # R4-b3 (2026-07-08): _open_rest_family_rows_for_refresh moved from
    # src/main.py to src.events.reactor with the reactor+prune cluster.
    from src.events import reactor

    trade = _mem_trade()
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, position_id TEXT, venue_order_id TEXT, intent_kind TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, local_sequence INTEGER)"
    )
    trade.execute(
        "CREATE UNIQUE INDEX idx_test_order_seq "
        "ON venue_order_facts(venue_order_id, local_sequence)"
    )
    trade.execute(
        "CREATE TABLE position_current ("
        "position_id TEXT PRIMARY KEY, city TEXT, target_date TEXT, "
        "temperature_metric TEXT, phase TEXT)"
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?)",
        ("cmd1", "pos1", "order-live", "ENTRY"),
    )
    trade.execute(
        "INSERT INTO position_current VALUES (?,?,?,?,?)",
        ("pos1", "Wuhan", "2026-06-12", "high", "pending_entry"),
    )
    for seq in range(1, 201):
        trade.execute(
            "INSERT INTO venue_order_facts VALUES (?,?,?)",
            (f"closed-{seq}", "EXPIRED", seq),
        )
    trade.execute("INSERT INTO venue_order_facts VALUES (?,?,?)", ("order-live", "LIVE", 1))
    trade.commit()
    captured = _SqlCaptureConn(trade)

    families = reactor._open_rest_family_rows_for_refresh(captured)

    assert families == [("Wuhan", "2026-06-12", "high")]
    statements = "\n".join(captured.statements).upper()
    assert "ROW_NUMBER" not in statements
    assert "PARTITION BY" not in statements
    assert "WHERE VENUE_ORDER_ID = ?" in statements


def test_open_rest_priority_uses_snapshot_family_before_position_projection():
    # R4-b3 (2026-07-08): _open_rest_family_rows_for_refresh moved from
    # src/main.py to src.events.reactor with the reactor+prune cluster.
    from src.events import reactor

    trade = _mem_trade()
    trade.execute("ALTER TABLE executable_market_snapshots ADD COLUMN event_id TEXT")
    trade.execute(
        "CREATE TABLE venue_commands ("
        "command_id TEXT, position_id TEXT, venue_order_id TEXT, intent_kind TEXT, "
        "token_id TEXT, snapshot_id TEXT)"
    )
    trade.execute(
        "CREATE TABLE venue_order_facts ("
        "venue_order_id TEXT, state TEXT, local_sequence INTEGER)"
    )
    trade.execute(
        "CREATE TABLE position_current ("
        "position_id TEXT PRIMARY KEY, city TEXT, target_date TEXT, "
        "temperature_metric TEXT, phase TEXT)"
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, yes_token_id, no_token_id,
            selected_outcome_token_id, orderbook_top_bid, orderbook_top_ask,
            freshness_deadline, captured_at, event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snap-chongqing-no",
            "condition-chongqing",
            "yes-token",
            "no-token",
            "no-token",
            "0.67",
            "0.68",
            "2026-06-19T12:40:00+00:00",
            "2026-06-19T12:30:00+00:00",
            "highest-temperature-in-chongqing-on-june-21-2026",
        ),
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?)",
        ("cmd-live", "pos-not-yet-projected", "order-live", "ENTRY", "no-token", "snap-chongqing-no"),
    )
    trade.execute("INSERT INTO venue_order_facts VALUES (?,?,?)", ("order-live", "LIVE", 1))
    trade.commit()

    families = reactor._open_rest_family_rows_for_refresh(trade)

    assert families == [("Chongqing", "2026-06-21", "high")]


# ───────────────────────────────────────────────────────────────────────────────────────────────
# ANTIBODY 4 — FEE is the canonical price-dependent model, not the flat 1¢ magic number.
# ───────────────────────────────────────────────────────────────────────────────────────────────
def test_fee_is_price_dependent_polymarket_model():
    from src.contracts.execution_price import polymarket_fee

    assert cr._fee_at(0.5) == pytest.approx(polymarket_fee(0.5))
    assert cr._fee_at(0.9) == pytest.approx(polymarket_fee(0.9))
    # Conservative fail-soft for a degenerate price (outside (0,1)) → parabola max at 0.5.
    assert cr._fee_at(1.5) == pytest.approx(polymarket_fee(0.5))


def test_screen_deltas_have_documented_tick_basis():
    assert cr.IMPROVE_DELTA == pytest.approx(2.0 * cr.TICK_SIZE)


# ───────────────────────────────────────────────────────────────────────────────────────────────
# ANTIBODY 5 — SCOPE GATE + classifier accept EDLI_REDECISION_PENDING as forecast-lane under ALL
# scopes (deliberate extension). A redecision must classify to the forecast strategy, never raise.
# ───────────────────────────────────────────────────────────────────────────────────────────────
def test_redecision_type_is_in_forecast_decision_set():
    import src.engine.event_reactor_adapter as adapter
    import src.events.reactor as reactor
    import src.events.event_store as store

    assert cr.REDECISION_EVENT_TYPE in adapter._FORECAST_DECISION_EVENT_TYPES
    assert cr.REDECISION_EVENT_TYPE in reactor._FORECAST_DECISION_EVENT_TYPES
    assert cr.REDECISION_EVENT_TYPE in store._FORECAST_DECISION_EVENT_TYPES
    # The forecast-decision set is the scope gate's forecast-lane set verbatim.
    assert adapter._FORECAST_DECISION_EVENT_TYPES == frozenset(
        {"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"}
    )


def test_strategy_classifier_accepts_redecision_type():
    """The classifier RAISES on unknown event types (fail-closed). EDLI_REDECISION_PENDING must
    resolve to the forecast strategy, exactly like FORECAST_SNAPSHOT_READY, never raise."""
    from src.engine.event_reactor_adapter import _event_bound_strategy_key

    fsr = _event_bound_strategy_key(
        event_type="FORECAST_SNAPSHOT_READY", direction="buy_yes", metric="high"
    )
    redecision = _event_bound_strategy_key(
        event_type="EDLI_REDECISION_PENDING", direction="buy_yes", metric="high"
    )
    assert redecision == fsr, "a redecision must classify to the SAME forecast strategy as an FSR"


def test_strategy_classifier_keeps_day0_out_of_forecast_entry_lanes():
    from src.engine.event_reactor_adapter import _event_bound_strategy_key

    day0_yes = _event_bound_strategy_key(
        event_type="DAY0_EXTREME_UPDATED", direction="buy_yes", metric="high"
    )
    day0_no = _event_bound_strategy_key(
        event_type="DAY0_EXTREME_UPDATED", direction="buy_no", metric="high"
    )

    assert day0_yes == "day0_nowcast_entry"
    assert day0_no == "day0_nowcast_entry"
    assert (
        _event_bound_strategy_key(
            event_type="DAY0_EXTREME_UPDATED",
            direction="buy_no",
            metric="high",
            day0_payoff_truth="locked",
        )
        == "settlement_capture"
    )


def test_day0_selected_payoff_truth_uses_settlement_rounding():
    from types import SimpleNamespace

    from src.engine.event_reactor_adapter import _settled_day0_extreme

    family = SimpleNamespace(city="Tokyo")
    assert (
        _settled_day0_extreme(
            {"high_so_far": 25.4},
            family=family,
            metric="high",
        )
        == 25.0
    )
    assert (
        _settled_day0_extreme(
            {"high_so_far": 25.6},
            family=family,
            metric="high",
        )
        == 26.0
    )


def test_timeliness_floor_applies_to_redecision_type():
    """A strictly-past EDLI_REDECISION_PENDING must be filtered by the same timeliness floor as an
    FSR — else a price-driven redecision could re-fire on an already-settled market."""
    import src.events.event_store as store

    # The forecast-decision set is what _is_timely branches on (not == FSR).
    assert "EDLI_REDECISION_PENDING" in store._FORECAST_DECISION_EVENT_TYPES


# ───────────────────────────────────────────────────────────────────────────────────────────────
# ANTIBODY 6 — END-TO-END CONSUME: an EDLI_REDECISION_PENDING event is consumed by the reactor and
# routed through the forecast decision path (submit called, processed — NOT rejected as unknown).
# The reactor also persists the receipt's belief_payload through its OWN conn (deadlock-free P1).
# ───────────────────────────────────────────────────────────────────────────────────────────────
def _redecision_event(*, event_type: str):
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event

    payload = ForecastSnapshotReadyPayload(
        city="Chicago", target_date="2026-05-24", metric="high",
        source_id="opendata", source_run_id="run-1", cycle="00", track="live",
        snapshot_id="snap-1", snapshot_hash="hash-1",
        captured_at="2026-05-24T18:00:00+00:00", available_at="2026-05-24T18:01:00+00:00",
        required_fields_present=True, required_steps_present=True, member_count=51,
        min_members_floor=40, completeness_status="COMPLETE", required_steps=[0],
        observed_steps=[0], expected_members=51, source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE", coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type=event_type,
        entity_key="Chicago|2026-05-24|high|run-1",
        source="edli_redecision:cycle-x",
        observed_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        received_at="2026-05-24T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-1",
    )
