# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 4 acceptance criteria — hand-computable fixture asserting exact
#   values for the fill dedup law, clustered SE, and paired proper scores.
"""Tests for scripts/scoreboard_panels.py.

Covers, per the reversal-plan item 4 mandate:
  - the two-stage fill dedup law (state-collapse + 0x-placeholder drop),
  - a buy_no side-flip in P2 selection,
  - a NULL q_in_bin row excluded from P1 coverage (never silently dropped),
  - two-way clustered SE with an exact hand-computed value,
  - P3/P4 latency, slippage, and cheap-position lifecycle math.
"""
from __future__ import annotations

import math
import sqlite3

import pytest

from scripts.scoreboard_panels import (
    clustered_se,
    compute_panel1,
    compute_panel2,
    compute_panel3,
    compute_panel4,
    dedup_fill_facts,
    month_of,
    open_ro,
)
from src.state.db import init_schema, init_schema_trade_only


# ---------------------------------------------------------------------------
# Unit tests: pure helpers.
# ---------------------------------------------------------------------------


class TestClusteredSe:
    def test_two_clusters_exact_value(self):
        # cluster A mean diff = 0.5, cluster B mean diff = -0.5.
        # stdev([0.5, -0.5], ddof=1) = sqrt(0.5) ; se = sqrt(0.5)/sqrt(2) = 0.5
        se, n = clustered_se({"A": [0.5, 0.5], "B": [-0.5, -0.5]})
        assert n == 2
        assert se == pytest.approx(0.5, abs=1e-9)

    def test_single_cluster_is_undefined(self):
        se, n = clustered_se({"A": [0.1, 0.2, -0.1]})
        assert n == 1
        assert se is None

    def test_no_clusters_is_undefined(self):
        se, n = clustered_se({})
        assert n == 0
        assert se is None


class TestMonthOf:
    def test_iso_timestamp(self):
        assert month_of("2026-07-15T00:00:00+00:00") == "2026-07"

    def test_epoch_like_string_is_unknown_not_a_fake_year(self):
        # Legacy adopted_exit_* rows carry bare unix-epoch-seconds strings;
        # naive slicing would produce a bogus fake "month" like "1782750".
        assert month_of("1782750705") == "UNKNOWN"

    def test_none_is_unknown(self):
        assert month_of(None) == "UNKNOWN"


class TestDedupFillFacts:
    def _row(self, trade_id, command_id, state, filled_size, observed_at):
        return {
            "trade_id": trade_id,
            "command_id": command_id,
            "state": state,
            "filled_size": filled_size,
            "fill_price": "0.5",
            "observed_at": observed_at,
        }

    def test_stage1_collapses_same_trade_id_to_best_state(self):
        rows = [
            self._row("t1", "c1", "MATCHED", "5.0", "2026-07-01T00:00:01+00:00"),
            self._row("t1", "c1", "CONFIRMED", "5.0", "2026-07-01T00:00:05+00:00"),
        ]
        result = dedup_fill_facts(rows)
        assert len(result.kept) == 1
        assert result.kept[0]["state"] == "CONFIRMED"
        assert result.stage1_state_collapsed == 1
        assert result.stage2_placeholder_dropped == 0

    def test_stage2_drops_0x_placeholder_with_matching_sibling(self):
        rows = [
            self._row("0xabc123", "c1", "MATCHED", "10.0", "2026-07-01T00:00:01+00:00"),
            self._row("uuid-real", "c1", "CONFIRMED", "10.0", "2026-07-01T00:00:04+00:00"),
        ]
        result = dedup_fill_facts(rows)
        assert len(result.kept) == 1
        assert result.kept[0]["trade_id"] == "uuid-real"
        assert result.stage2_placeholder_dropped == 1

    def test_0x_without_matching_sibling_is_kept(self):
        # No non-0x row shares the command_id/size -> not a duplicate, keep it.
        rows = [self._row("0xabc123", "c1", "MATCHED", "10.0", "2026-07-01T00:00:01+00:00")]
        result = dedup_fill_facts(rows)
        assert len(result.kept) == 1
        assert result.stage2_placeholder_dropped == 0


# ---------------------------------------------------------------------------
# Integration fixtures: real world/trades schema via init_schema helpers so
# column shapes cannot drift from production (repo law: read the real schema
# first, but test against fixtures only).
# ---------------------------------------------------------------------------


@pytest.fixture
def world_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def trades_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_trade_only(conn)
    yield conn
    conn.close()


def _insert_attribution(conn: sqlite3.Connection, *, attribution_id: str, **overrides) -> None:
    base = dict(
        attribution_id=attribution_id,
        position_id=f"pos-{attribution_id}",
        condition_id=None,
        city=None,
        target_date=None,
        temperature_metric="high",
        direction="buy_yes",
        traded_bin_label=None,
        category="UNATTRIBUTABLE_Q_MISSING",
        won=1,
        counts_as_skill_win=0,
        avg_fill_price=None,
        q_live=None,
        q_lcb_5pct=None,
        q_in_bin=None,
        market_in_bin_prob=None,
        settled_in_bin=None,
        settled_at=None,
        graded_at="2026-07-20T00:00:00+00:00",
        schema_version=1,
    )
    base.update(overrides)
    conn.execute(
        """
        INSERT INTO settlement_attribution (
            attribution_id, position_id, condition_id, city, target_date,
            temperature_metric, direction, traded_bin_label, category, won,
            counts_as_skill_win, avg_fill_price, q_live, q_lcb_5pct, q_in_bin,
            market_in_bin_prob, settled_in_bin, settled_at, graded_at, schema_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            base["attribution_id"], base["position_id"], base["condition_id"], base["city"],
            base["target_date"], base["temperature_metric"], base["direction"],
            base["traded_bin_label"], base["category"], base["won"], base["counts_as_skill_win"],
            base["avg_fill_price"], base["q_live"], base["q_lcb_5pct"], base["q_in_bin"],
            base["market_in_bin_prob"], base["settled_in_bin"], base["settled_at"],
            base["graded_at"], base["schema_version"],
        ),
    )
    conn.commit()


@pytest.fixture
def p1_p2_world_conn(world_conn):
    """10-row settlement_attribution fixture, hand-computable P1/P2 values.

    Rows 1-4: two city-date clusters, price==0.5, split 2/2 win/loss -> a
              clean 2-cluster P2 band with exact edge=0.0 and se=0.5.
    Row 5:    buy_no side-flip (settled_in_bin=1 but the NO side loses).
    Row 6:    q_in_bin NULL -> must be excluded from P1, counted in coverage.
    Rows 7-8: q vs p disagreement pair in the 0.30-0.50 |q-p| bucket, one
              where q beats p and one where p beats q.
    Row 9:    market_in_bin_prob NULL -> excluded from P1 coverage.
    Row 10:   settled_in_bin NULL -> excluded from BOTH P1 and P2 coverage.
    """
    month = "2026-07-15T00:00:00+00:00"
    _insert_attribution(
        world_conn, attribution_id="a1", city="CityA", target_date="2026-07-01",
        direction="buy_yes", avg_fill_price=0.5, q_in_bin=0.5, market_in_bin_prob=0.5,
        settled_in_bin=1, settled_at=month,
    )
    _insert_attribution(
        world_conn, attribution_id="a2", city="CityA", target_date="2026-07-01",
        direction="buy_yes", avg_fill_price=0.5, q_in_bin=0.5, market_in_bin_prob=0.5,
        settled_in_bin=1, settled_at=month,
    )
    _insert_attribution(
        world_conn, attribution_id="a3", city="CityB", target_date="2026-07-02",
        direction="buy_yes", avg_fill_price=0.5, q_in_bin=0.5, market_in_bin_prob=0.5,
        settled_in_bin=0, settled_at=month,
    )
    _insert_attribution(
        world_conn, attribution_id="a4", city="CityB", target_date="2026-07-02",
        direction="buy_yes", avg_fill_price=0.5, q_in_bin=0.5, market_in_bin_prob=0.5,
        settled_in_bin=0, settled_at=month,
    )
    _insert_attribution(
        world_conn, attribution_id="a5", city="CityC", target_date="2026-07-03",
        direction="buy_no", avg_fill_price=0.30, q_in_bin=0.7, market_in_bin_prob=0.7,
        settled_in_bin=1, settled_at=month,
    )
    _insert_attribution(
        world_conn, attribution_id="a6", city="CityD", target_date="2026-07-04",
        direction="buy_yes", avg_fill_price=0.05, q_in_bin=None, market_in_bin_prob=0.4,
        settled_in_bin=1, settled_at=month,
    )
    _insert_attribution(
        world_conn, attribution_id="a7", city="CityE", target_date="2026-07-05",
        direction="buy_yes", avg_fill_price=0.85, q_in_bin=0.9, market_in_bin_prob=0.5,
        settled_in_bin=1, settled_at="2026-07-16T00:00:00+00:00",
    )
    _insert_attribution(
        world_conn, attribution_id="a8", city="CityF", target_date="2026-07-06",
        direction="buy_yes", avg_fill_price=0.85, q_in_bin=0.1, market_in_bin_prob=0.5,
        settled_in_bin=1, settled_at="2026-07-16T00:00:00+00:00",
    )
    _insert_attribution(
        world_conn, attribution_id="a9", city="CityG", target_date="2026-07-07",
        direction="buy_yes", avg_fill_price=0.06, q_in_bin=0.5, market_in_bin_prob=None,
        settled_in_bin=1, settled_at="2026-07-16T00:00:00+00:00",
    )
    _insert_attribution(
        world_conn, attribution_id="a10", city="CityH", target_date="2026-07-08",
        direction="buy_yes", avg_fill_price=0.06, q_in_bin=0.5, market_in_bin_prob=0.5,
        settled_in_bin=None, settled_at="2026-07-16T00:00:00+00:00",
    )
    return world_conn


class TestPanel1Forecast:
    def test_coverage_excludes_null_q_but_never_drops_silently(self, p1_p2_world_conn):
        p1 = compute_panel1(p1_p2_world_conn)
        assert p1["total_rows"] == 10
        assert p1["excluded_null_q_in_bin"] == 1
        assert p1["excluded_null_market_in_bin_prob"] == 1
        assert p1["excluded_null_settled_in_bin"] == 1
        assert p1["usable_rows"] == 7  # 10 - 3 exclusions
        assert p1["grand_total"].n == 7
        assert p1["grand_total"].clusters == 5

    def test_disagreement_bucket_exact_logloss_and_brier(self, p1_p2_world_conn):
        p1 = compute_panel1(p1_p2_world_conn)
        stats = p1["pooled_by_bucket"]["0.30-0.50"]
        assert stats.n == 2
        assert stats.clusters == 2

        ll_q = (-math.log(0.9) + -math.log(0.1)) / 2
        ll_p = (-math.log(0.5) + -math.log(0.5)) / 2
        br_q = ((0.9 - 1) ** 2 + (0.1 - 1) ** 2) / 2
        br_p = ((0.5 - 1) ** 2 + (0.5 - 1) ** 2) / 2

        assert stats.logloss_q == pytest.approx(ll_q, abs=1e-9)
        assert stats.logloss_p == pytest.approx(ll_p, abs=1e-9)
        assert stats.brier_q == pytest.approx(br_q, abs=1e-9)
        assert stats.brier_p == pytest.approx(br_p, abs=1e-9)
        # q beats p on row a7 (0.105 < 0.693) but loses on a8 (2.303 > 0.693).
        assert stats.q_beats_p_share == pytest.approx(0.5, abs=1e-9)


class TestPanel2Selection:
    def test_coverage_excludes_null_settled_only(self, p1_p2_world_conn):
        p2 = compute_panel2(p1_p2_world_conn)
        assert p2["total_rows"] == 10
        assert p2["excluded_null_direction"] == 0
        assert p2["excluded_null_avg_fill_price"] == 0
        assert p2["excluded_null_settled_in_bin"] == 1
        assert p2["usable_rows"] == 9
        assert p2["grand_total"].n == 9
        assert p2["grand_total"].clusters == 7

    def test_buy_no_side_flip(self, p1_p2_world_conn):
        p2 = compute_panel2(p1_p2_world_conn)
        band = p2["pooled_by_band"]["0.25-0.45"]
        # a5: buy_no, settled_in_bin=1 -> side_win = 1 - 1 = 0 (the NO side lost).
        assert band.n == 1
        assert band.mean_p == pytest.approx(0.30, abs=1e-9)
        assert band.mean_y == pytest.approx(0.0, abs=1e-9)
        assert band.edge == pytest.approx(-0.30, abs=1e-9)
        assert band.se_gate is None  # single cluster -> SE undefined

    def test_two_cluster_band_exact_edge_and_clustered_se(self, p1_p2_world_conn):
        p2 = compute_panel2(p1_p2_world_conn)
        band = p2["pooled_by_band"]["0.45-0.65"]
        assert band.n == 4
        assert band.clusters == 2
        assert band.mean_p == pytest.approx(0.5, abs=1e-9)
        assert band.mean_y == pytest.approx(0.5, abs=1e-9)
        assert band.edge == pytest.approx(0.0, abs=1e-9)
        assert band.n_clusters_city_date == 2
        assert band.se_city_date == pytest.approx(0.5, abs=1e-9)
        assert band.se_gate == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# P3/P4 — trades DB fixture.
# ---------------------------------------------------------------------------


def _insert_command(conn: sqlite3.Connection, *, command_id: str, **overrides) -> None:
    base = dict(
        command_id=command_id,
        snapshot_id=f"snap-{command_id}",
        envelope_id=f"env-{command_id}",
        position_id="pos-x",
        decision_id=f"dec-{command_id}",
        idempotency_key=f"idem-{command_id}",
        intent_kind="ENTRY",
        market_id="market-1",
        token_id="token-1",
        side="BUY",
        size=1.0,
        price=0.5,
        venue_order_id=None,
        state="FILLED",
        last_event_id=None,
        created_at="2026-07-01T00:00:00+00:00",
        updated_at="2026-07-01T00:00:00+00:00",
        review_required_reason=None,
        q_version=None,
    )
    base.update(overrides)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason, q_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            base["command_id"], base["snapshot_id"], base["envelope_id"], base["position_id"],
            base["decision_id"], base["idempotency_key"], base["intent_kind"], base["market_id"],
            base["token_id"], base["side"], base["size"], base["price"], base["venue_order_id"],
            base["state"], base["last_event_id"], base["created_at"], base["updated_at"],
            base["review_required_reason"], base["q_version"],
        ),
    )
    conn.commit()


_seq_counter = {"n": 0}


def _insert_fact(conn: sqlite3.Connection, *, trade_id: str, command_id: str, **overrides) -> None:
    _seq_counter["n"] += 1
    base = dict(
        trade_id=trade_id,
        venue_order_id=f"vo-{trade_id}",
        command_id=command_id,
        state="CONFIRMED",
        filled_size="1.0",
        fill_price="0.5",
        fee_paid_micro=None,
        tx_hash=None,
        block_number=None,
        confirmation_count=0,
        source="REST",
        observed_at="2026-07-01T00:00:00+00:00",
        venue_timestamp=None,
        local_sequence=_seq_counter["n"],
        raw_payload_hash=f"hash-{trade_id}-{_seq_counter['n']}",
        raw_payload_json=None,
    )
    base.update(overrides)
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, state, filled_size, fill_price,
            fee_paid_micro, tx_hash, block_number, confirmation_count, source,
            observed_at, venue_timestamp, local_sequence, raw_payload_hash, raw_payload_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            base["trade_id"], base["venue_order_id"], base["command_id"], base["state"],
            base["filled_size"], base["fill_price"], base["fee_paid_micro"], base["tx_hash"],
            base["block_number"], base["confirmation_count"], base["source"], base["observed_at"],
            base["venue_timestamp"], base["local_sequence"], base["raw_payload_hash"],
            base["raw_payload_json"],
        ),
    )
    conn.commit()


@pytest.fixture
def p3_p4_trades_conn(trades_conn):
    """Trades fixture: dedup case (0x + UUID), a partial-fill state-collapse,
    a cheap early-exited position, a cheap held-to-settlement position, and a
    non-cheap position — every number below is hand-computed in the test.
    """
    # pos-1: two partial ENTRY fills, avg price = (10*0.20 + 5*0.30) / 15 = 0.2333 (cheap).
    _insert_command(
        trades_conn, command_id="cmd-entry-1", position_id="pos-1", intent_kind="ENTRY",
        state="FILLED", price=0.18, created_at="2026-07-01T00:00:00+00:00",
        updated_at="2026-07-01T00:00:00+00:00",
    )
    # 0x placeholder + real UUID sibling, same command_id, same filled_size -> stage2 drop.
    _insert_fact(
        trades_conn, trade_id="0xabc111", command_id="cmd-entry-1", state="MATCHED",
        filled_size="10.0", fill_price="0.20", observed_at="2026-07-01T00:00:03+00:00",
    )
    _insert_fact(
        trades_conn, trade_id="uuid-real-01", command_id="cmd-entry-1", state="CONFIRMED",
        filled_size="10.0", fill_price="0.20", observed_at="2026-07-01T00:00:04+00:00",
    )

    _insert_command(
        trades_conn, command_id="cmd-entry-2", position_id="pos-1", intent_kind="ENTRY",
        state="FILLED", price=0.28, created_at="2026-07-01T01:00:00+00:00",
        updated_at="2026-07-01T01:00:00+00:00",
    )
    # Same trade_id observed twice (MATCHED then CONFIRMED) -> stage1 collapse.
    _insert_fact(
        trades_conn, trade_id="uuid-real-02", command_id="cmd-entry-2", state="MATCHED",
        filled_size="5.0", fill_price="0.30", observed_at="2026-07-01T01:00:02+00:00",
    )
    _insert_fact(
        trades_conn, trade_id="uuid-real-02", command_id="cmd-entry-2", state="CONFIRMED",
        filled_size="5.0", fill_price="0.30", observed_at="2026-07-01T01:00:10+00:00",
    )

    # pos-1 EXIT, FILLED -> early-exited. proceeds = 15 * 0.90 = 13.5.
    _insert_command(
        trades_conn, command_id="cmd-exit-1", position_id="pos-1", intent_kind="EXIT",
        state="FILLED", price=0.90, created_at="2026-07-02T00:00:00+00:00",
        updated_at="2026-07-02T00:00:00+00:00",
    )
    _insert_fact(
        trades_conn, trade_id="uuid-exit-01", command_id="cmd-exit-1", state="CONFIRMED",
        filled_size="15.0", fill_price="0.90", observed_at="2026-07-02T00:00:05+00:00",
    )

    # pos-2: single ENTRY at 0.80 (not cheap). Never exited.
    _insert_command(
        trades_conn, command_id="cmd-entry-3", position_id="pos-2", intent_kind="ENTRY",
        state="FILLED", price=0.75, created_at="2026-07-03T00:00:00+00:00",
        updated_at="2026-07-03T00:00:00+00:00",
    )
    _insert_fact(
        trades_conn, trade_id="uuid-entry-03", command_id="cmd-entry-3", state="CONFIRMED",
        filled_size="8.0", fill_price="0.80", observed_at="2026-07-03T00:00:07+00:00",
    )
    # A CANCELLED exit attempt on pos-2 -> does not count as an early exit for pos-2
    # (pos-2 is not cheap anyway) but exercises the EXIT state-count coverage.
    _insert_command(
        trades_conn, command_id="cmd-exit-2", position_id="pos-2", intent_kind="EXIT",
        state="CANCELLED", price=0.5, created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    _insert_command(
        trades_conn, command_id="cmd-exit-5", position_id="pos-2", intent_kind="EXIT",
        state="REJECTED", price=0.5, created_at="2026-07-07T00:00:00+00:00",
        updated_at="2026-07-07T00:00:00+00:00",
    )

    # pos-3: single cheap ENTRY at 0.10, an EXPIRED exit attempt but never FILLED
    # -> held to settlement.
    _insert_command(
        trades_conn, command_id="cmd-entry-4", position_id="pos-3", intent_kind="ENTRY",
        state="FILLED", price=0.08, created_at="2026-07-04T00:00:00+00:00",
        updated_at="2026-07-04T00:00:00+00:00",
    )
    _insert_fact(
        trades_conn, trade_id="uuid-entry-04", command_id="cmd-entry-4", state="CONFIRMED",
        filled_size="20.0", fill_price="0.10", observed_at="2026-07-04T00:03:00+00:00",
    )
    _insert_command(
        trades_conn, command_id="cmd-exit-4", position_id="pos-3", intent_kind="EXIT",
        state="EXPIRED", price=0.5, created_at="2026-07-06T00:00:00+00:00",
        updated_at="2026-07-06T00:00:00+00:00",
    )

    return trades_conn


class TestPanel3Execution:
    def test_dedup_counts_and_fill_count(self, p3_p4_trades_conn):
        p3 = compute_panel3(p3_p4_trades_conn)
        assert p3["total_entry_commands"] == 4
        assert p3["raw_fact_rows"] == 6
        assert p3["dedup_stage1_state_collapsed"] == 1
        assert p3["dedup_stage2_placeholder_dropped"] == 1
        assert p3["deduped_fact_rows"] == 4
        assert p3["entries_with_no_fill_facts"] == 0

    def test_latency_and_slippage_exact(self, p3_p4_trades_conn):
        p3 = compute_panel3(p3_p4_trades_conn)
        month = p3["monthly"]["2026-07"]
        assert month.n_fills == 4
        assert month.n_commands == 4
        # latencies: cmd1=4s, cmd2=10s, cmd3=7s, cmd4=180s
        assert month.median_latency_s == pytest.approx(8.5, abs=1e-6)
        assert month.p90_latency_s == pytest.approx(129.0, abs=1e-6)
        assert month.share_taker_lt5s == pytest.approx(0.25, abs=1e-9)  # cmd1 only
        assert month.share_maker_gt120s == pytest.approx(0.25, abs=1e-9)  # cmd4 only
        assert month.avg_slippage_taker == pytest.approx(0.02, abs=1e-9)
        assert month.avg_slippage_mid == pytest.approx(0.035, abs=1e-9)
        assert month.avg_slippage_maker == pytest.approx(0.02, abs=1e-9)


class TestPanel4Lifecycle:
    def test_exit_state_counts(self, p3_p4_trades_conn):
        p4 = compute_panel4(p3_p4_trades_conn)
        assert p4["total_exit_commands"] == 4
        assert p4["other_state_exit_commands"] == 0
        assert p4["monthly_state_counts"][("2026-07", "FILLED")] == 1
        assert p4["monthly_state_counts"][("2026-07", "CANCELLED")] == 1
        assert p4["monthly_state_counts"][("2026-07", "EXPIRED")] == 1
        assert p4["monthly_state_counts"][("2026-07", "REJECTED")] == 1

    def test_cheap_position_split_and_pooled_ratio(self, p3_p4_trades_conn):
        p4 = compute_panel4(p3_p4_trades_conn)
        assert p4["cheap_position_count"] == 2  # pos-1 (0.2333), pos-3 (0.10)
        assert p4["cheap_held_to_settlement"] == 1  # pos-3
        assert p4["cheap_early_exited"] == 1  # pos-1
        assert p4["cheap_pooled_entry_cost"] == pytest.approx(3.5, abs=1e-9)  # pos-1 only
        assert p4["cheap_pooled_exit_proceeds"] == pytest.approx(13.5, abs=1e-9)  # pos-1 only
        assert p4["cheap_pooled_exit_proceeds_over_entry_cost"] == pytest.approx(
            13.5 / 3.5, abs=1e-9
        )


# ---------------------------------------------------------------------------
# Read-only URI smoke test.
# ---------------------------------------------------------------------------


class TestOpenRo:
    def test_opens_readonly_and_rejects_writes(self, tmp_path):
        db_path = tmp_path / "ro_smoke.db"
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("CREATE TABLE t (x INTEGER)")
        setup_conn.execute("INSERT INTO t VALUES (1)")
        setup_conn.commit()
        setup_conn.close()

        conn = open_ro(db_path)
        try:
            row = conn.execute("SELECT x FROM t").fetchone()
            assert row[0] == 1
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO t VALUES (2)")
        finally:
            conn.close()
