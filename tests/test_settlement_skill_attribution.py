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
    """W3: grades trades.position_current (the real ledger) + W2: writes
    settlement-derived pnl onto the audit row, through the real load path.

    Phase 3 (2026-06-20): the grader now reads ``trades.position_current`` instead
    of the ``edli_live_profit_audit`` filled-fill subset, and the same tick writes
    ``pnl_usd``/``settlement_outcome`` back onto the audit row from the SAME
    grade_receipt payoff. RED on revert: against the audit-subset grader the
    settlement_attribution row is keyed ``aud-1`` (audit grain); against this fix it
    is keyed ``pos-1`` (position_current grain), and pnl_usd is NULL on revert.
    """
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path)
    init_schema(wconn)
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path)
    init_schema(tconn)  # creates trade-class tables incl. position_current

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

    # W3: the REAL ledger row — a buy_no position on cond-1 in position_current.
    tconn.execute(
        """INSERT INTO position_current
           (position_id, phase, strategy_key, condition_id, direction, entry_price,
            shares, cost_basis_usd, city, target_date, temperature_metric, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("pos-1", "settled", "center_buy", "cond-1", "buy_no", 0.35, 10.0, 3.5,
         "Denver", "2026-06-12", "high", "2026-06-11T12:00:00Z"),
    )
    tconn.commit()
    tconn.close()

    # W2: an audit fill on the same market — pnl_usd starts NULL, gets written back.
    wconn.execute(
        """INSERT INTO edli_live_profit_audit
           (audit_id, event_id, aggregate_id, condition_id, token_id, direction,
            avg_fill_price, filled_size, fees, q_live, order_lifecycle_state,
            created_at, schema_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("aud-1", "evt-1", "agg-1", "cond-1", "tok-1", "buy_no",
         0.35, 10.0, 0.0, 0.72, "FILLED", "2026-06-11T12:00:00Z", 1),
    )
    assert wconn.execute(
        "SELECT pnl_usd FROM edli_live_profit_audit WHERE audit_id='aud-1'"
    ).fetchone()[0] is None
    wconn.commit()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    stats = run_settlement_skill_attribution(world_conn=wconn, only_new=True)
    assert stats["total_settled_positions"] == 1
    assert stats["graded"] == 1
    # W3: the graded row is keyed by the position_current grain (pos-1), NOT the
    # audit fill (aud-1). On revert to the audit-subset grader this would be aud-1.
    row = wconn.execute(
        "SELECT position_id, direction, won, category FROM settlement_attribution"
    ).fetchone()
    assert row[0] == "pos-1"
    assert row[1] == "buy_no"
    assert row[2] == 1  # won
    assert row[3] in ("SKILL_WIN", "LUCKY_WIN")

    # W2: pnl_usd written from settlement payoff. buy_no WON → payoff=1.0;
    # pnl = (1.0 - 0.35) * 10.0 - 0.0 = 6.5. Derived from settlement, not price.
    assert stats["settlement_pnl_written"] == 1
    audit = wconn.execute(
        "SELECT pnl_usd, settlement_outcome FROM edli_live_profit_audit WHERE audit_id='aud-1'"
    ).fetchone()
    assert audit[0] == pytest.approx(6.5)
    assert audit[1] == "WON"

    # Idempotent: re-run grades nothing new (pnl writeback re-runs harmlessly).
    stats2 = run_settlement_skill_attribution(world_conn=wconn, only_new=True)
    assert stats2["graded"] == 0
    assert stats2["skipped_existing"] == 1
    wconn.close()


def test_W2_unverified_settlement_yields_null_pnl(tmp_path) -> None:
    """W2 negative: an audit fill on a market whose settlement is NOT VERIFIED
    must leave pnl_usd NULL — settlement truth is never fabricated."""
    from src.analysis.settlement_skill_attribution import writeback_settlement_pnl_to_audit

    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    wconn = sqlite3.connect(world_path)
    init_schema(wconn)
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)
    fconn.execute(
        """INSERT INTO market_events
           (market_slug, condition_id, city, target_date, temperature_metric,
            range_low, range_high, recorded_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("denver-high-50-51-06-12", "cond-1", "Denver", "2026-06-12", "high",
         50.0, 51.0, "2026-06-11T00:00:00Z"),
    )
    # Settlement present but UNVERIFIED (authority != 'VERIFIED').
    fconn.execute(
        """INSERT INTO settlement_outcomes
           (city, target_date, temperature_metric, settlement_value,
            settlement_unit, settled_at, authority, provenance_json, recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("Denver", "2026-06-12", "high", 47.0, "F", "2026-06-12T20:00:00Z",
         "UNVERIFIED", "{}", "2026-06-12T20:00:00Z"),
    )
    fconn.commit()
    fconn.close()

    wconn.execute(
        """INSERT INTO edli_live_profit_audit
           (audit_id, event_id, aggregate_id, condition_id, token_id, direction,
            avg_fill_price, filled_size, fees, order_lifecycle_state,
            created_at, schema_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("aud-1", "evt-1", "agg-1", "cond-1", "tok-1", "buy_no",
         0.35, 10.0, 0.0, "FILLED", "2026-06-11T12:00:00Z", 1),
    )
    wconn.commit()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))

    written = writeback_settlement_pnl_to_audit(wconn)
    assert written == 0
    pnl = wconn.execute(
        "SELECT pnl_usd, settlement_outcome FROM edli_live_profit_audit WHERE audit_id='aud-1'"
    ).fetchone()
    assert pnl[0] is None
    assert pnl[1] is None
    wconn.close()


def _seed_position_with_events(
    tconn: sqlite3.Connection,
    *,
    position_id: str,
    entry_at: str,
    updated_at: str,
    direction: str = "buy_no",
) -> None:
    """A settled position_current row + an immutable POSITION_OPEN_INTENT entry event."""
    tconn.execute(
        """INSERT INTO position_current
           (position_id, phase, strategy_key, condition_id, direction, entry_price,
            shares, cost_basis_usd, city, target_date, temperature_metric, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, "settled", "center_buy", "cond-1", direction, 0.35, 10.0, 3.5,
         "Denver", "2026-06-12", "high", updated_at),
    )
    tconn.execute(
        """INSERT INTO position_events
           (event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, strategy_key, source_module, env, payload_json)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f"ev-{position_id}", position_id, 1, 1, "POSITION_OPEN_INTENT",
         entry_at, "center_buy", "test", "test", "{}"),
    )


def test_BLOCKER2_decision_time_uses_immutable_entry_not_updated_at(tmp_path) -> None:
    """BLOCKER 2 (RED on revert): the decision-time posterior bound must be the
    IMMUTABLE entry time (position_events), NOT position_current.updated_at.

    Scenario: entry T0=06-09, a FRESHER posterior at T1=06-10 (after entry), and a
    mutated updated_at T2=06-12 (a settlement/monitor bump). The decision-time
    posterior MUST be the T0 one, and a fresher cycle (T1) MUST be detected as having
    existed at decision (stale signal). Under the old updated_at(T2) bound, the T1
    posterior would itself be selected as 'decision-time' → no fresher cycle → the
    stale classification is corrupted. Asserts the grade's decision posterior is the
    T0 one and fresher_cycle_existed_at_decision is True.
    """
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)
    fconn = sqlite3.connect(fcst_path); init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path); init_schema(tconn)

    fconn.execute(
        """INSERT INTO market_events
           (market_slug, condition_id, city, target_date, temperature_metric,
            range_low, range_high, recorded_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("m", "cond-1", "Denver", "2026-06-12", "high", 50.0, 51.0, "2026-06-08T00:00:00Z"),
    )
    fconn.execute(
        """INSERT INTO settlement_outcomes
           (city, target_date, temperature_metric, settlement_value,
            settlement_unit, settled_at, authority, provenance_json, recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("Denver", "2026-06-12", "high", 47.0, "F", "2026-06-12T20:00:00Z",
         "VERIFIED", "{}", "2026-06-12T20:00:00Z"),
    )
    def _insert_posterior(pid, computed_at, q_json):
        fconn.execute(
            """INSERT INTO forecast_posteriors
               (posterior_id, source_id, product_id, data_version, city, target_date,
                temperature_metric, source_cycle_time, source_available_at, computed_at,
                q_json, posterior_method, training_allowed, recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, "src", "prod", "v1", "Denver", "2026-06-12", "high",
             computed_at, computed_at, computed_at, q_json, "test", 0, computed_at),
        )

    # Decision-time posterior at T0 (computed 06-09, before entry-time bound 06-09T12).
    _insert_posterior(1, "2026-06-09T06:00:00Z", '{"50-51F": 0.30}')
    # FRESHER posterior at T1 (computed 06-10, AFTER entry — must NOT be the decision one).
    _insert_posterior(2, "2026-06-10T06:00:00Z", '{"50-51F": 0.10}')
    fconn.commit(); fconn.close()

    # Entry T0=06-09T12 (immutable), but updated_at mutated to T2=06-12 (post-fresh).
    _seed_position_with_events(
        tconn, position_id="pos-1",
        entry_at="2026-06-09T12:00:00Z", updated_at="2026-06-12T20:00:00Z",
    )
    tconn.commit(); tconn.close()

    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    grades = load_settled_positions(wconn)
    assert len(grades) == 1
    g = grades[0]
    # The decision-time posterior is T0 (06-09), NOT the fresher T1 (06-10).
    assert g.decision_posterior_computed_at == "2026-06-09T06:00:00Z"
    # A strictly-fresher cycle (T1) existed at decision → stale signal present.
    assert g.fresher_cycle_existed_at_decision is True
    wconn.close()


def test_INV37_trades_attached_read_only_blocks_writes(tmp_path) -> None:
    """INV-37 re-review (RED on revert): the 'trades' ATTACH is read-only — an
    attempted write to trades.position_current must fail. A plain ATTACH would
    permit the write."""
    from src.analysis.settlement_skill_attribution import _ensure_trades_attached
    import src.state.db as dbmod
    import pathlib

    trades_path = str(tmp_path / "trades.db")
    tconn = sqlite3.connect(trades_path); init_schema(tconn); tconn.commit(); tconn.close()

    world_path = str(tmp_path / "world.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)

    orig = dbmod._zeus_trade_db_path
    dbmod._zeus_trade_db_path = lambda: pathlib.Path(trades_path)
    try:
        _ensure_trades_attached(wconn)
        # Reads work.
        wconn.execute("SELECT COUNT(*) FROM trades.position_current").fetchone()
        # Writes are rejected by the read-only attachment.
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            wconn.execute(
                """INSERT INTO trades.position_current
                   (position_id, phase, strategy_key, temperature_metric, updated_at)
                   VALUES ('x','settled','center_buy','high','2026-06-12T00:00:00Z')"""
            )
    finally:
        dbmod._zeus_trade_db_path = orig
        wconn.close()
