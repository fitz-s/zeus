# Created: 2026-06-12
# Last reused or audited: 2026-06-29
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
    freshness_deadline="2026-06-12T02:00:00+00:00",
    captured_at="2026-06-12T00:30:00+00:00",
):
    conn.execute(
        "INSERT INTO executable_market_snapshots "
        "(snapshot_id, condition_id, yes_token_id, no_token_id, selected_outcome_token_id, "
        "orderbook_top_bid, orderbook_top_ask, freshness_deadline, captured_at) VALUES "
        "(?,?,?,?,?,?,?,?,?)",
        (
            snapshot_id,
            condition_id,
            yes_token_id,
            no_token_id,
            selected_outcome_token_id,
            bid,
            ask,
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
    bids = cr.read_freshest_resting_best_bids(trade, condition_ids={"0xc30"})

    assert quotes[("0xc30", "buy_yes")].price == pytest.approx(0.32)
    assert quotes[("0xc30", "buy_no")].price == pytest.approx(0.70)
    assert bids[("0xc30", "buy_yes")].price == pytest.approx(0.30)
    assert bids[("0xc30", "buy_no")].price == pytest.approx(0.68)


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
def test_rest_pull_fires_on_belief_decay_new_evidence():
    world = _mem_world()
    trade = _mem_trade()
    # Rest was priced on snap1 (belief YES=0.90). New evidence snap2 decays belief to 0.60 (Δ0.30).
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30", recorded_at="2026-06-12T00:00:00+00:00")
    _cache(world, p_yes=0.60, snapshot_id="snap2", cond="0xc30", recorded_at="2026-06-12T12:00:00+00:00")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=0.0,
    )
    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
    )
    assert len(pulls) == 1
    _rest, decision = pulls[0]
    assert decision.reason == "BELIEF_WORSENING"


def test_rest_pull_holds_on_same_snapshot_price_wiggle():
    world = _mem_world()
    trade = _mem_trade()
    # Only the rest's OWN snapshot is cached (no new evidence). Belief unchanged → HOLD.
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=0.0,  # fresh quote, no stale pull
    )
    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
    )
    assert pulls == [], "a bare wiggle on the same snapshot must never pull a rest (anti-twitch)"


def test_rest_pull_does_not_cancel_by_order_age_alone():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=120_000.0,
    )

    pulls = cr.screen_resting_orders(world, trade, open_rests=[rest])

    assert pulls == [], "resting order age alone is not confirmed trading value or cancel evidence"


def test_rest_pull_does_not_use_entry_screen_candidate_as_cancel_authority():
    """A cheap-screen candidate is not enough evidence to cancel a live rest."""

    world = _mem_world()
    trade = _mem_trade()
    family_id = "hyp|live|Moscow|2026-06-30|high|disc"
    rest = cr.OpenRest(
        command_id="cmd-rest",
        venue_order_id="order-rest",
        family_id=family_id,
        bin_label="27C",
        side="buy_no",
        condition_id="0xc27",
        resting_posterior=0.72,
        resting_snapshot_id="snap-rest",
        limit_price=0.68,
        quote_age_ms=1_000.0,
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
    )

    assert pulls == []


def test_duplicate_suppressed_receipt_pulls_rest_when_full_reactor_best_moved():
    """Full-reactor duplicate lock is rest-management evidence, not a dead end."""

    world = _mem_world()
    _regret_table(world)
    rest = cr.OpenRest(
        command_id="cmd-rest",
        venue_order_id="order-rest",
        family_id="hyp|live|Singapore|2026-07-01|high|disc",
        bin_label="30C",
        side="buy_yes",
        condition_id="0xc30",
        resting_posterior=0.82,
        resting_snapshot_id="snap-rest",
        limit_price=0.06,
        quote_age_ms=120_000.0,
        city="Singapore",
        target_date="2026-07-01",
        metric="high",
    )
    world.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            city, target_date, metric, family_id, bin_label, direction, q_lcb_5pct,
            c_fee_adjusted, trade_score, created_at
        ) VALUES (?, ?, 'TRADE_SCORE', ?, 'NO_EDGE',
                  'Singapore', '2026-07-01', 'high', 'edli_family_dynamic',
                  '', '', NULL, NULL, NULL, ?)
        """,
        (
            "regret-dup",
            "event-dup",
            (
                "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 other=22; "
                "best_rejected=Will the highest temperature in Singapore be 32°C on July 1? "
                "buy_no reason_class=other missing_reason="
                "EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED:"
                "condition_id=0xc32:token_id=tok32:direction=buy_no:"
                "family_id=:city=Singapore:target_date=2026-07-01:metric=high "
                "q_lcb=0.6447 price=0.5500 rejected_ev_per_dollar=0.1722"
            ),
            "2026-06-29T08:47:16+00:00",
        ),
    )

    pulls = cr.active_duplicate_suppressed_rest_pulls(
        world,
        open_rests=[rest],
        decision_time="2026-06-29T08:48:00+00:00",
    )

    assert len(pulls) == 1
    pulled_rest, decision = pulls[0]
    assert pulled_rest.command_id == "cmd-rest"
    assert decision.action == "CANCEL_REPLACE"
    assert decision.reason == "ACTIVE_DUPLICATE_SUPPRESSED_BETTER_CANDIDATE"
    assert decision.detail == pytest.approx(0.1722)


def test_duplicate_suppressed_receipt_does_not_pull_same_active_rest():
    world = _mem_world()
    _regret_table(world)
    rest = cr.OpenRest(
        command_id="cmd-rest",
        venue_order_id="order-rest",
        family_id="hyp|live|Singapore|2026-07-01|high|disc",
        bin_label="30C",
        side="buy_yes",
        condition_id="0xc30",
        resting_posterior=0.82,
        resting_snapshot_id="snap-rest",
        limit_price=0.06,
        quote_age_ms=120_000.0,
        city="Singapore",
        target_date="2026-07-01",
        metric="high",
    )
    world.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            city, target_date, metric, family_id, bin_label, direction, q_lcb_5pct,
            c_fee_adjusted, trade_score, created_at
        ) VALUES (?, ?, 'TRADE_SCORE', ?, 'NO_EDGE',
                  'Singapore', '2026-07-01', 'high', 'edli_family_dynamic',
                  '', '', NULL, NULL, NULL, ?)
        """,
        (
            "regret-same",
            "event-same",
            (
                "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 other=22; "
                "best_rejected=30C buy_yes reason_class=other missing_reason="
                "EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED:"
                "condition_id=0xc30:token_id=tok30:direction=buy_yes:"
                "family_id=:city=Singapore:target_date=2026-07-01:metric=high"
            ),
            "2026-06-29T08:47:16+00:00",
        ),
    )

    pulls = cr.active_duplicate_suppressed_rest_pulls(
        world,
        open_rests=[rest],
        decision_time="2026-06-29T08:48:00+00:00",
    )

    assert pulls == []


def test_rest_pull_refreshes_confirmed_value_after_cooldown_with_fresh_book():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.69", ask="0.72")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=6 * 60 * 1000.0,
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
        value_refresh_min_age_seconds=5 * 60,
    )

    assert len(pulls) == 1
    assert pulls[0][1].reason == "CONFIRMED_VALUE_REFRESH"
    assert pulls[0][1].detail > cr.IMPROVE_DELTA


def test_rest_pull_refreshes_confirmed_value_on_one_tick_cross_when_taker_edge_is_real():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.69", ask="0.71")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=6 * 60 * 1000.0,
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
        value_refresh_min_age_seconds=5 * 60,
    )

    assert len(pulls) == 1
    assert pulls[0][1].reason == "CONFIRMED_VALUE_REFRESH"
    assert pulls[0][1].detail > cr.IMPROVE_DELTA


def test_rest_pull_does_not_refresh_confirmed_value_on_stale_book():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.69", ask="0.72")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=6 * 60 * 1000.0,
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T02:00:01+00:00",
        value_refresh_min_age_seconds=5 * 60,
    )

    assert pulls == []


def test_rest_pull_does_not_refresh_confirmed_value_when_taker_edge_not_real():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.73, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.69", ask="0.72")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.73, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=6 * 60 * 1000.0,
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
        value_refresh_min_age_seconds=5 * 60,
    )

    assert pulls == []


def test_rest_pull_does_not_treat_normal_spread_as_book_moved():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    # YES best bid is below the resting limit, while ask is two ticks above it.
    # The old bug used ask cost and would pull; maker-rest drift must use best bid.
    _snapshot(trade, bid="0.69", ask="0.72")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=0.0,
    )

    pulls = cr.screen_resting_orders(world, trade, open_rests=[rest])

    assert pulls == []


def test_rest_pull_fires_when_best_bid_moves_past_limit_tolerance():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.73", ask="0.75")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=6 * 60 * 1000.0,  # post-floor: BOOK_MOVED fires after the maker window
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
    )

    assert len(pulls) == 1
    assert pulls[0][1].reason == "BOOK_MOVED"


def test_rest_pull_fires_when_best_bid_is_one_tick_ahead_of_limit():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.71", ask="0.73")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=6 * 60 * 1000.0,  # post-floor: BOOK_MOVED fires after the maker window
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
    )

    assert len(pulls) == 1
    assert pulls[0][1].reason == "BOOK_MOVED"
    assert pulls[0][1].detail == pytest.approx(cr.TICK_SIZE)


def test_book_moved_holds_within_maker_window():
    """ANTIBODY (2026-06-23 entry fill-lane diagnosis): sub-floor (quote younger
    than the 300s REST_VALUE_REFRESH_MIN_AGE_SECONDS maker window), even when the
    best bid moves a full tick past our limit, HOLD — do NOT BOOK_MOVED-pull.
    Every maker rest must get a real fill window and survive to
    escalation-eligibility (the next decision crosses TAKER_ESCALATED_AFTER_REST),
    instead of the documented infinite sub-floor rest->pull->re-rest loop that
    produced 0 crosses / 0 +EV-band fills (venue_commands 24h: 0/12 fills in the
    0.40-0.80 band). The belief-decay pull (NEW evidence) is NOT gated and still
    fires; only the microstructure BOOK_MOVED twitch is dampened to the floor.
    """
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.73", ask="0.75")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=60_000.0,  # 60s < 300s floor
    )

    pulls = cr.screen_resting_orders(
        world, trade, open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
    )

    assert pulls == [], (
        "a book-moved pull within the 300s maker window must HOLD so the rest "
        "survives to escalation; sub-floor BOOK_MOVED churn caused 0 +EV fills"
    )


def test_rest_pull_ignores_stale_book_moved_evidence():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.90, snapshot_id="snap1", cond="0xc30")
    _snapshot(trade, bid="0.73", ask="0.75")
    rest = cr.OpenRest(
        command_id="cmd1", venue_order_id="vo1",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_yes",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.70, quote_age_ms=0.0,
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T02:00:01+00:00",
    )

    assert pulls == []


def test_buy_no_rest_uses_native_no_best_bid_for_book_moved():
    world = _mem_world()
    trade = _mem_trade()
    _cache(world, p_yes=0.10, snapshot_id="snap1", cond="0xc30")
    # NO selected-token rows are native NO books; the top bid is 0.80.
    _snapshot(trade, bid="0.80", ask="0.82", selected_outcome_token_id="no-c30")
    rest = cr.OpenRest(
        command_id="cmd-no", venue_order_id="vo-no",
        family_id="hyp|live|Wuhan|2026-06-12|high|disc", bin_label="b30", side="buy_no",
        condition_id="0xc30", resting_posterior=0.90, resting_snapshot_id="snap1",
        limit_price=0.76, quote_age_ms=6 * 60 * 1000.0,  # post-floor: BOOK_MOVED fires after the maker window
    )

    pulls = cr.screen_resting_orders(
        world,
        trade,
        open_rests=[rest],
        decision_time="2026-06-12T00:45:00+00:00",
    )

    assert len(pulls) == 1
    assert pulls[0][1].reason == "BOOK_MOVED"


def test_open_maker_rests_preserve_no_token_direction_and_held_side_posterior():
    import src.main as main

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

    rests = main._edli_open_maker_rests_for_screen(trade, world)

    assert len(rests) == 1
    assert rests[0].side == "buy_no"
    assert rests[0].resting_posterior == pytest.approx(0.80)
    assert rests[0].created_at == "2026-06-12T00:00:00+00:00"
    assert rests[0].fact_state == "LIVE"
    assert rests[0].matched_size is None


def test_open_maker_rests_resolve_token_from_latest_snapshot_mirror_without_append_scan():
    import src.main as main

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

    rests = main._edli_open_maker_rests_for_screen(captured_trade, world)

    assert len(rests) == 1
    assert rests[0].side == "buy_no"
    statements = "\n".join(captured_trade.statements)
    assert "FROM executable_market_snapshot_latest" in statements
    assert "FROM executable_market_snapshots" not in statements


def test_open_maker_rests_avoids_full_order_fact_window_scan():
    import src.main as main

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

    rests = main._edli_open_maker_rests_for_screen(captured, world)

    assert len(rests) == 1
    statements = "\n".join(captured.statements).upper()
    assert "ROW_NUMBER" not in statements
    assert "PARTITION BY" not in statements
    assert "WHERE VENUE_ORDER_ID = ?" in statements


def test_open_maker_rests_skip_unresolved_orders_from_redecision_screen():
    import src.main as main

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

    rests = main._edli_open_maker_rests_for_screen(trade, world)

    assert rests == []


def test_open_rest_families_are_priority_warm_inputs_without_fact_window_scan():
    import src.main as main

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

    families = main._open_rest_family_rows_for_refresh(captured)

    assert families == [("Wuhan", "2026-06-12", "high")]
    statements = "\n".join(captured.statements).upper()
    assert "ROW_NUMBER" not in statements
    assert "PARTITION BY" not in statements
    assert "WHERE VENUE_ORDER_ID = ?" in statements


def test_open_rest_priority_uses_snapshot_family_before_position_projection():
    import src.main as main

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

    families = main._open_rest_family_rows_for_refresh(trade)

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
    assert cr.BELIEF_REPRICE_DELTA == pytest.approx(3.0 * cr.TICK_SIZE)
    assert cr.BELIEF_REPRICE_DELTA > cr.IMPROVE_DELTA  # must exceed entry friction


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


def test_redecision_event_consumed_and_belief_persisted():
    import json
    from dataclasses import replace
    from datetime import datetime, timezone

    from src.events.event_store import EventStore
    from src.events.reactor import (
        EventSubmissionReceipt,
        OpportunityEventReactor,
        ReactorConfig,
    )
    from src.state.db import init_schema
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    store = EventStore(conn)
    event = _redecision_event(event_type="EDLI_REDECISION_PENDING")
    store.insert_or_ignore(event)

    submitted: list[str] = []

    def _submit(ev, _dt):
        submitted.append(ev.event_id)
        payload = json.loads(ev.payload_json)
        receipt = EventSubmissionReceipt(
            submitted=False, proof_accepted=True, event_id=ev.event_id,
            causal_snapshot_id=ev.causal_snapshot_id,
            city=payload.get("city"), target_date=payload.get("target_date"),
            metric=payload.get("metric"), condition_id="condition-1", token_id="yes-1",
            executable_snapshot_id="snapshot-exec-1", family_id="family-1",
            trade_score_positive=True, fdr_pass=True, fdr_family_id="family-1",
            fdr_hypothesis_count=2, kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice", kelly_price_fee_deducted=True,
            kelly_size_usd=1.0, kelly_cost_basis_id="cost-1", kelly_decision_id="kelly-1",
            risk_decision_id="risk-1", final_intent_id="intent-1",
            # The captured belief the adapter would attach — the reactor must persist it.
            belief_payload={
                "family_id": "hyp|live|Chicago|2026-05-24|high|d", "city": "Chicago",
                "target_date": "2026-05-24", "snapshot_id": "snap-1",
                "calibrator_model_hash": "identity", "bin_labels": ["b73", "b74"],
                "p_posterior_vec": [0.4, 0.6], "condition_ids": ["0xa", "0xb"],
                "q_lcb_yes_vec": [0.35, 0.55], "q_lcb_no_vec": [0.60, 0.40],
            },
        )
        return replace(
            receipt,
            decision_proof_bundle=build_test_no_submit_proof_bundle(ev, receipt, decision_time=_dt),
        )

    reactor = OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: True,
        riskguard_gate=lambda _e: True,
        final_intent_submit=_submit,
        reject=lambda *_a: None,
        config=ReactorConfig(reactor_mode="live_no_submit"),
        regret_ledger=NoTradeRegretLedger(store.conn),
    )
    result = reactor.process_pending(
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    )

    # The redecision event reached the SUBMIT path (was NOT fail-closed as an unknown type).
    assert submitted == [event.event_id], "EDLI_REDECISION_PENDING must reach the forecast decision path"
    assert result.dead_lettered == 0
    # P1: the belief was persisted through the reactor's OWN conn (deadlock-free), now queryable.
    belief_rows = conn.execute(
        "SELECT decision_id, q_lcb_yes_json, q_lcb_no_json "
        "FROM probability_trace_fact WHERE decision_id LIKE 'edli_belief:%'"
    ).fetchall()
    assert len(belief_rows) == 1, "the reactor must persist the receipt belief_payload (P1)"
    assert "hyp|live|Chicago|2026-05-24|high|d" in belief_rows[0][0]
    assert belief_rows[0][1] == "[0.35, 0.55]"
    assert belief_rows[0][2] == "[0.6, 0.4]"
