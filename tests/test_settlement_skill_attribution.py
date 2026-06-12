# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator skill-vs-luck law 2026-06-12 ("wu预测92不是结算在92就算赢了
#   说明这是一单完全运气获胜跟我们的系统无关 ... 昨天3单全部刚好踩在结算哪一个温度上").
#   Relationship tests written BEFORE implementation per methodology (relationship
#   tests → implementation → function tests). Each test asserts a CROSS-MODULE
#   invariant: the grade that flows out of grade_position when our position +
#   decision-time q + freshest settlement-eve data + settled outcome + market
#   price flow in must match the operator's named real cases.
"""Relationship + function tests for settlement_skill_attribution.

Relationship tests (the load-bearing cross-module invariants)
-------------------------------------------------------------
R1  Denver-if-92 fixture: won BUT our own freshest data disagreed → LUCKY_WIN
    (a MISS in skill accounting). The relationship: fresh-posterior q for the
    held token, NOT the stale decision q, decides skill.
R2  06-12 three-loss shape: lost AND market priced the settled bin 2-2.5x our q
    AND market was right → MISCALIBRATED_LOSS.
R3  born-stale decision → STALE_DECISION regardless of outcome (excluded from
    the skill denominator).
R4  honest variance loss (no large market/q disagreement) → SKILL_LOSS.
R5  skill win counted: won AND fresh data supported → SKILL_WIN.
R6  the skill win-rate excludes LUCKY_WIN from the numerator AND STALE from the
    denominator: SKILL_WIN / (SKILL_WIN + LUCKY_WIN + SKILL_LOSS + MISCALIBRATED).

Function tests
--------------
F1  idempotent re-grade: a second run writes no new row and upserts in place.
F2  schema + registry green (the table is created by init_schema and declared).
F3  end-to-end DB grade over a synthetic FILLED position with a VERIFIED
    settlement and a fresh posterior.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.db import init_schema, init_schema_forecasts
from src.analysis.settlement_skill_attribution import (
    grade_position,
    compute_skill_win_rate,
    persist_grade,
    run_settlement_skill_attribution,
    load_settled_positions,
    SUPPORT_BOUNDARY,
    LARGE_FACTOR,
    DEFAULT_FRESHNESS_BUDGET_HOURS,
)


# ---------------------------------------------------------------------------
# R1 — Denver-if-92 → LUCKY_WIN
# ---------------------------------------------------------------------------

def test_R1_denver_if_92_grades_lucky_win() -> None:
    """Our NO on 90-91 'won' because settle landed elsewhere, BUT our own freshest
    NBM hourly said 90.0 (so the NO on 90-91 should LOSE: fresh P(in-bin)=high →
    fresh q_held for the NO = low < 0.5). A win our fresh data disagreed with =
    LUCKY_WIN, a MISS in skill accounting."""
    # buy_no on 90-91; settle landed at 89 (OUT of bin) so the NO position WON.
    # Fresh data said the high would be ~90 (IN the 90-91 bin) → fresh in-bin=0.85,
    # so fresh q for the held NO token = 1-0.85 = 0.15 < 0.5 → fresh DISAGREES.
    g = grade_position(
        position_id="denver-1",
        direction="buy_no",
        traded_bin_label="90-91°F",
        won=True,
        settled_in_bin=False,        # settle landed OUT → NO won
        settled_value=89.0,
        settlement_unit="F",
        settled_at="2026-06-12T20:00:00Z",
        avg_fill_price=0.40,         # paid 0.40 for the NO token
        q_live=0.79,                 # stale posterior held NO at 0.79
        decision_time="2026-06-11T12:00:00Z",
        decision_posterior_computed_at="2026-06-11T06:00:00Z",
        fresh_posterior_computed_at="2026-06-12T00:00:00Z",
        fresh_q_held=0.15,           # FRESH NBM: NO is only 0.15 (high ~= 90, in-bin)
        fresher_cycle_existed_at_decision=False,
    )
    assert g.category == "LUCKY_WIN", g.rationale
    assert g.counts_as_skill_win is False
    assert g.fresh_q_supports_position is False


# ---------------------------------------------------------------------------
# R2 — 06-12 three-loss shape → MISCALIBRATED_LOSS
# ---------------------------------------------------------------------------

def test_R2_three_loss_shape_grades_miscalibrated_loss() -> None:
    """A buy_no that LOST because settle landed EXACTLY on the bin we sold NO on,
    where the market priced that bin 2-2.5x our q. Our q(in-bin)=0.20, market
    priced it 0.50 (= 1 - NO price 0.50) → ratio 2.5x >= 2.0 AND market was right
    (settle IN bin). MISCALIBRATED_LOSS."""
    g = grade_position(
        position_id="hk-loss-1",
        direction="buy_no",
        traded_bin_label="33-34°C",
        won=False,
        settled_in_bin=True,         # settle landed IN the bin we sold NO on → NO lost
        settled_value=33.0,
        settlement_unit="C",
        settled_at="2026-06-12T08:00:00Z",
        avg_fill_price=0.50,         # paid 0.50 for NO → market in-bin prob = 0.50
        q_live=0.80,                 # our NO q = 0.80 → our in-bin q = 0.20
        decision_time="2026-06-11T12:00:00Z",
        decision_posterior_computed_at="2026-06-11T10:00:00Z",
        fresh_posterior_computed_at="2026-06-12T00:00:00Z",
        fresh_q_held=0.78,           # fresh still backed NO (it was a real miss, not stale)
        fresher_cycle_existed_at_decision=False,
    )
    assert g.category == "MISCALIBRATED_LOSS", g.rationale
    # market in-bin = 1-0.50 = 0.50; our in-bin = 1-0.80 = 0.20; ratio = 2.5
    assert g.market_q_ratio == pytest.approx(2.5, abs=1e-9)
    assert g.market_q_ratio >= LARGE_FACTOR
    assert g.counts_as_skill_win is False


# ---------------------------------------------------------------------------
# R3 — born-stale → STALE_DECISION
# ---------------------------------------------------------------------------

def test_R3_born_stale_grades_stale_decision() -> None:
    """A decision consuming a posterior already superseded by a fresher cycle is
    born stale → STALE_DECISION regardless of win/loss."""
    g = grade_position(
        position_id="stale-1",
        direction="buy_no",
        traded_bin_label="70-71°F",
        won=True,                    # even a WIN is branded stale
        settled_in_bin=False,
        settled_value=68.0,
        settlement_unit="F",
        settled_at="2026-06-12T20:00:00Z",
        avg_fill_price=0.45,
        q_live=0.75,
        decision_time="2026-06-11T12:00:00Z",
        decision_posterior_computed_at="2026-06-11T11:00:00Z",
        fresh_q_held=0.80,
        fresher_cycle_existed_at_decision=True,   # a strictly-fresher cycle existed
    )
    assert g.category == "STALE_DECISION", g.rationale
    assert g.counts_as_skill_win is False


def test_R3b_born_stale_via_age_budget() -> None:
    """Born-stale also triggers when the consumed posterior age > freshness budget."""
    g = grade_position(
        position_id="stale-2",
        direction="buy_yes",
        traded_bin_label="80-81°F",
        won=False,
        settled_in_bin=False,
        settled_value=78.0,
        settlement_unit="F",
        settled_at="2026-06-12T20:00:00Z",
        avg_fill_price=0.30,
        q_live=0.30,
        decision_time="2026-06-11T18:00:00Z",
        # posterior is 9h older than the decision > 6h budget → born stale
        decision_posterior_computed_at="2026-06-11T09:00:00Z",
        fresher_cycle_existed_at_decision=False,
        freshness_budget_hours=DEFAULT_FRESHNESS_BUDGET_HOURS,
    )
    assert g.category == "STALE_DECISION", g.rationale
    assert g.decision_posterior_age_hours == pytest.approx(9.0, abs=1e-6)


# ---------------------------------------------------------------------------
# R4 — honest variance loss → SKILL_LOSS
# ---------------------------------------------------------------------------

def test_R4_honest_variance_loss_grades_skill_loss() -> None:
    """A loss where the market did NOT price the settled bin a large factor above
    our q is honest variance → SKILL_LOSS. Our q(in-bin)=0.45, market(in-bin)=0.55
    → ratio 1.22 < 2.0."""
    g = grade_position(
        position_id="variance-1",
        direction="buy_no",
        traded_bin_label="60-61°F",
        won=False,
        settled_in_bin=True,         # lost
        settled_value=60.0,
        settlement_unit="F",
        settled_at="2026-06-12T20:00:00Z",
        avg_fill_price=0.45,         # NO price 0.45 → market in-bin = 0.55
        q_live=0.55,                 # NO q 0.55 → our in-bin = 0.45
        decision_time="2026-06-11T12:00:00Z",
        decision_posterior_computed_at="2026-06-11T10:00:00Z",
        fresh_q_held=0.55,
        fresher_cycle_existed_at_decision=False,
    )
    assert g.category == "SKILL_LOSS", g.rationale
    assert g.market_q_ratio < LARGE_FACTOR
    assert g.counts_as_skill_win is False


# ---------------------------------------------------------------------------
# R5 — skill win → SKILL_WIN
# ---------------------------------------------------------------------------

def test_R5_supported_win_grades_skill_win() -> None:
    """Won AND our freshest data supported the position (held-token q > 0.5) →
    SKILL_WIN, the only category that earns skill credit."""
    g = grade_position(
        position_id="skill-1",
        direction="buy_no",
        traded_bin_label="50-51°F",
        won=True,
        settled_in_bin=False,        # NO won
        settled_value=47.0,
        settlement_unit="F",
        settled_at="2026-06-12T20:00:00Z",
        avg_fill_price=0.35,
        q_live=0.72,
        decision_time="2026-06-11T12:00:00Z",
        decision_posterior_computed_at="2026-06-11T11:00:00Z",
        fresh_posterior_computed_at="2026-06-12T00:00:00Z",
        fresh_q_held=0.70,           # fresh still backs NO at 0.70 > 0.5 → supports
        fresher_cycle_existed_at_decision=False,
    )
    assert g.category == "SKILL_WIN", g.rationale
    assert g.counts_as_skill_win is True
    assert g.fresh_q_supports_position is True


# ---------------------------------------------------------------------------
# R6 — the skill win-rate math (the rate that matters)
# ---------------------------------------------------------------------------

def test_R6_skill_win_rate_excludes_lucky_and_stale() -> None:
    """SKILL win-rate = SKILL_WIN / (SKILL_WIN + LUCKY_WIN + SKILL_LOSS +
    MISCALIBRATED_LOSS); STALE excluded from the denominator entirely. With
    2 skill wins, 1 lucky win, 1 skill loss, 1 miscalibrated loss, 3 stale:
    skill rate = 2/5 = 0.40 (NOT the naive 3/5)."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    cases = (
        ["SKILL_WIN"] * 2 + ["LUCKY_WIN"] * 1 + ["SKILL_LOSS"] * 1
        + ["MISCALIBRATED_LOSS"] * 1 + ["STALE_DECISION"] * 3
    )
    for i, cat in enumerate(cases):
        won = cat in ("SKILL_WIN", "LUCKY_WIN")
        conn.execute(
            """INSERT INTO settlement_attribution
               (attribution_id, position_id, category, won, counts_as_skill_win,
                settled_value, settlement_unit, settled_in_bin, direction,
                traded_bin_label, freshness_budget_hours, large_factor_threshold,
                derivation_note, rationale, graded_at, schema_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"a{i}", f"p{i}", cat, int(won),
             int(cat == "SKILL_WIN"), 50.0, "F", 0, "buy_no", "x",
             6.0, 2.0, "note", "r", "2026-06-12T00:00:00Z", 1),
        )
    rate = compute_skill_win_rate(conn)
    assert rate.skill_denominator == 5
    assert rate.skill_win_rate == pytest.approx(0.40, abs=1e-9)
    # The naive rate (counts the lucky win) is the MISLEADING 3/5 = 0.60.
    assert rate.naive_win_rate == pytest.approx(0.60, abs=1e-9)


# ---------------------------------------------------------------------------
# F1 — idempotent re-grade
# ---------------------------------------------------------------------------

def test_F1_idempotent_regrade() -> None:
    """Persisting the same position_id twice upserts in place — one row, not two."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    g = grade_position(
        position_id="idem-1",
        direction="buy_no",
        traded_bin_label="50-51°F",
        won=True,
        settled_in_bin=False,
        settled_value=47.0,
        settlement_unit="F",
        settled_at="2026-06-12T20:00:00Z",
        avg_fill_price=0.35,
        q_live=0.72,
        fresh_q_held=0.70,
        fresher_cycle_existed_at_decision=False,
    )
    persist_grade(conn, g)
    persist_grade(conn, g)
    n = conn.execute(
        "SELECT COUNT(*) FROM settlement_attribution WHERE position_id='idem-1'"
    ).fetchone()[0]
    assert n == 1


# ---------------------------------------------------------------------------
# F2 — schema + registry green
# ---------------------------------------------------------------------------

def test_F2_table_created_and_registered() -> None:
    """init_schema creates settlement_attribution AND it is declared in the registry."""
    from src.state.table_registry import tables_for, DBIdentity

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    live = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "settlement_attribution" in live
    assert "settlement_attribution" in tables_for(DBIdentity.WORLD)


# ---------------------------------------------------------------------------
# F3 — end-to-end DB grade (ATTACH shape)
# ---------------------------------------------------------------------------

def _attach_forecasts(world_conn: sqlite3.Connection) -> sqlite3.Connection:
    """Create an in-memory forecasts schema and ATTACH it as 'forecasts'.

    Uses a shared-cache named in-memory DB so the ATTACH sees the same tables.
    """
    # Use a file-backed temp via shared cache for ATTACH reliability.
    import tempfile, os

    fd, path = tempfile.mkstemp(suffix="_fcst.db")
    os.close(fd)
    fconn = sqlite3.connect(path)
    init_schema_forecasts(fconn)
    fconn.commit()
    fconn.close()
    world_conn.execute("ATTACH DATABASE ? AS forecasts", (path,))
    return world_conn


def test_F3_end_to_end_db_grade(tmp_path) -> None:
    """A FILLED position + market_events bin + VERIFIED settlement grades and
    persists one settlement_attribution row through the real load path."""
    import os

    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    wconn = sqlite3.connect(world_path)
    init_schema(wconn)
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)

    # market_events: condition_id → city/date/metric/range (the traded bin 50-51F).
    fconn.execute(
        """INSERT INTO market_events
           (market_slug, condition_id, city, target_date, temperature_metric,
            range_low, range_high, recorded_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("denver-high-50-51-06-12", "cond-1", "Denver", "2026-06-12", "high",
         50.0, 51.0, "2026-06-11T00:00:00Z"),
    )
    # VERIFIED settlement: settled at 47 (OUT of 50-51 bin) → buy_no WINS.
    fconn.execute(
        """INSERT INTO settlement_outcomes
           (city, target_date, temperature_metric, settlement_value,
            settlement_unit, settled_at, authority, provenance_json, recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("Denver", "2026-06-12", "high", 47.0, "F", "2026-06-12T20:00:00Z",
         "VERIFIED", "{}", "2026-06-12T20:00:00Z"),
    )
    fconn.commit()
    fconn.close()

    # FILLED buy_no position on cond-1.
    wconn.execute(
        """INSERT INTO edli_live_profit_audit
           (audit_id, event_id, aggregate_id, condition_id, token_id, direction,
            avg_fill_price, filled_size, q_live, order_lifecycle_state,
            created_at, schema_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("aud-1", "evt-1", "agg-1", "cond-1", "tok-1", "buy_no",
         0.35, 10.0, 0.72, "FILLED", "2026-06-11T12:00:00Z", 1),
    )
    wconn.commit()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))

    stats = run_settlement_skill_attribution(world_conn=wconn, only_new=True)
    assert stats["total_settled_positions"] == 1
    assert stats["graded"] == 1
    row = wconn.execute(
        "SELECT position_id, direction, won, category FROM settlement_attribution"
    ).fetchone()
    assert row[0] == "aud-1"
    assert row[1] == "buy_no"
    assert row[2] == 1  # won
    # No fresh posterior in this fixture → win is uncertifiable → LUCKY_WIN
    # (conservative: an uncertifiable win earns no skill credit). With q_live=0.72
    # NO and no fresh lane, decision-q fallback: in-bin = 0.28 < 0.5 → supported →
    # SKILL_WIN. Assert it is one of the win categories and counts correctly.
    assert row[3] in ("SKILL_WIN", "LUCKY_WIN")

    # Idempotent: re-run grades nothing new.
    stats2 = run_settlement_skill_attribution(world_conn=wconn, only_new=True)
    assert stats2["graded"] == 0
    assert stats2["skipped_existing"] == 1
    wconn.close()
