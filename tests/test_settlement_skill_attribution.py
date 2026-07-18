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


def test_F1b_regrade_archives_prior_row_byte_frozen() -> None:
    """LX-E packet (2026-07-13): a re-grade with a DIFFERENT verdict for the SAME
    position_id (e.g. newer settlement truth) still shows ONE current row (the
    read contract is unchanged), but the CURRENT table's read is now the LATEST
    canonical version — and the OLD version is archived byte-for-byte into
    settlement_attribution_supersessions, never silently destroyed."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    g1 = grade_position(
        position_id="regrade-1",
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
    persist_grade(conn, g1, now_utc=datetime(2026, 6, 12, 21, 0, tzinfo=timezone.utc))

    first_row = conn.execute(
        "SELECT category, q_live FROM settlement_attribution WHERE position_id='regrade-1'"
    ).fetchone()
    assert first_row[0] == "SKILL_WIN"
    assert first_row[1] == pytest.approx(0.72)

    # A re-grade with a DIFFERENT fresh signal flips the category.
    g2 = grade_position(
        position_id="regrade-1",
        direction="buy_no",
        traded_bin_label="50-51°F",
        won=True,
        settled_in_bin=False,
        settled_value=47.0,
        settlement_unit="F",
        settled_at="2026-06-12T20:00:00Z",
        avg_fill_price=0.35,
        q_live=0.72,
        fresh_q_held=0.10,  # disagrees now -> LUCKY_WIN
        fresher_cycle_existed_at_decision=False,
    )
    persist_grade(conn, g2, now_utc=datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc))

    # Current table: exactly one row, holding the NEW (latest-canonical) verdict.
    n = conn.execute(
        "SELECT COUNT(*) FROM settlement_attribution WHERE position_id='regrade-1'"
    ).fetchone()[0]
    assert n == 1
    current = conn.execute(
        "SELECT category FROM settlement_attribution WHERE position_id='regrade-1'"
    ).fetchone()
    assert current[0] == "LUCKY_WIN"

    # Supersession archive: exactly ONE row, byte-frozen with the OLD verdict.
    archived = conn.execute(
        "SELECT prior_row_json, superseded_at FROM settlement_attribution_supersessions "
        "WHERE position_id='regrade-1'"
    ).fetchall()
    assert len(archived) == 1
    prior = json.loads(archived[0][0])
    assert prior["category"] == "SKILL_WIN"
    assert prior["q_live"] == pytest.approx(0.72)
    assert archived[0][1] == "2026-06-13T09:00:00+00:00"


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


def test_F3_end_to_end_db_grade(tmp_path, monkeypatch) -> None:
    """W3: grades trades.position_current (the real ledger).

    Phase 3 (2026-06-20): the grader reads ``trades.position_current`` instead of
    the ``edli_live_profit_audit`` filled-fill subset. LX-E packet (2026-07-13):
    the settlement-derived P&L label now lands on
    ``settlement_attribution.world_grade_pnl_usd`` — NEVER written back onto
    ``edli_live_profit_audit.pnl_usd`` (the removed writeback_settlement_pnl_to_audit
    was a forbidden world-grade/chain-money collapse). RED on revert: against the
    audit-subset grader the settlement_attribution row is keyed ``aud-1`` (audit
    grain); against this fix it is keyed ``pos-1`` (position_current grain).
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

    # q-provenance (2026-06-21): the grader resolves the immutable decision-q from
    # the ActionableTradeCertificate bridged off the audit row (condition_id,
    # direction). Seed a VERIFIED cert carrying q_live=0.72 and stamp its hash on
    # the audit fill so the position grades SKILL/LUCK (not UNATTRIBUTABLE).
    f3_cert_hash = "f3" + "0" * 62
    _seed_belief_certificate(
        wconn, certificate_hash=f3_cert_hash, condition_id="cond-1",
        token_id="tok-1", q_live=0.72, q_lcb_5pct=0.60,
    )
    # An audit fill on the same market — fees/avg_fill_price/filled_size feed
    # world_grade_pnl_usd (LX-E: an ancillary dollar figure, not the certificate
    # identity join). pnl_usd stays NULL forever — never written back.
    wconn.execute(
        """INSERT INTO edli_live_profit_audit
           (audit_id, event_id, aggregate_id, condition_id, token_id, direction,
            avg_fill_price, filled_size, fees, q_live,
            expected_edge_source_certificate_hash, order_lifecycle_state,
            created_at, schema_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("aud-1", "evt-1", "agg-1", "cond-1", "tok-1", "buy_no",
         0.35, 10.0, 0.0, None, f3_cert_hash, "FILLED",
         "2026-06-11T12:00:00Z", 1),
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
    assert "settlement_pnl_written" not in stats, (
        "writeback_settlement_pnl_to_audit is removed — no such stat any more"
    )
    # W3: the graded row is keyed by the position_current grain (pos-1), NOT the
    # audit fill (aud-1). On revert to the audit-subset grader this would be aud-1.
    row = wconn.execute(
        "SELECT position_id, direction, won, category, world_grade_pnl_usd "
        "FROM settlement_attribution"
    ).fetchone()
    assert row[0] == "pos-1"
    assert row[1] == "buy_no"
    assert row[2] == 1  # won
    assert row[3] in ("SKILL_WIN", "LUCKY_WIN")
    # world_grade_pnl_usd: buy_no WON -> payoff=1.0; (1.0 - 0.35) * 10.0 - 0.0 = 6.5.
    # SAME formula the removed writeback used, now landing on the grade receipt.
    assert row[4] == pytest.approx(6.5)

    # LX-T3 law: edli_live_profit_audit.pnl_usd/settlement_outcome are NEVER
    # written by the grading batch any more.
    audit = wconn.execute(
        "SELECT pnl_usd, settlement_outcome FROM edli_live_profit_audit WHERE audit_id='aud-1'"
    ).fetchone()
    assert audit[0] is None
    assert audit[1] is None

    # Idempotent: re-run filters existing positions before loading broad
    # market, settlement, entry-event, or posterior inputs.
    import src.analysis.settlement_skill_attribution as attribution_module

    monkeypatch.setattr(
        attribution_module,
        "_load_market_meta",
        lambda _conn: (_ for _ in ()).throw(
            AssertionError("existing grades must be filtered before broad DB reads")
        ),
    )
    stats2 = run_settlement_skill_attribution(world_conn=wconn, only_new=True)
    assert stats2["graded"] == 0
    assert stats2["skipped_existing"] == 1
    wconn.close()


def test_LXE_writeback_function_removed() -> None:
    """writeback_settlement_pnl_to_audit no longer exists — the grading batch
    never writes into edli_live_profit_audit at all (LX-T3 logical excision)."""
    import src.analysis.settlement_skill_attribution as mod

    assert not hasattr(mod, "writeback_settlement_pnl_to_audit")


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


# ---------------------------------------------------------------------------
# Q-PROVENANCE (the GRADER side of the immutable decision-q fix, 2026-06-21)
# ---------------------------------------------------------------------------
#
# Ground truth (verified read-only against zeus-world.db + zeus_trades.db on
# 2026-06-21): of 76 terminal-held trades.position_current rows, the immutable
# decision-q is reachable via the matching edli_live_profit_audit row's
# expected_edge_source_certificate_hash bridged by (condition_id, direction).
# 53/76 resolve a VERIFIED ActionableTradeCertificate carrying q_live + q_lcb_5pct
# directly; 23 have no resolvable cert. The grader must read the REAL decision-q
# from the cert (not a time-rebuilt posterior) when resolvable, and brand the
# unresolvable UNATTRIBUTABLE_Q_MISSING — never SKILL/LUCK.
#
# These tests assert the GRADER side over the #416 position_current loader: the
# grader bridges position_current -> edli_live_profit_audit (cert hash) ->
# decision_certificates, NOT the obsolete audit-only loader.


def _seed_belief_certificate(
    wconn: sqlite3.Connection,
    *,
    certificate_hash: str,
    condition_id: str,
    token_id: str,
    q_live: float,
    q_lcb_5pct: float,
    verifier_status: str = "VERIFIED",
) -> None:
    """Seed an ActionableTradeCertificate carrying the immutable decision-time q.

    Mirrors the real cert shape: payload_json holds q_live + q_lcb_5pct (verified
    against a live cert 2026-06-21). The grader resolves this off the audit row's
    expected_edge_source_certificate_hash.
    """
    payload = json.dumps({
        "condition_id": condition_id,
        "token_id": token_id,
        "q_live": q_live,
        "q_lcb_5pct": q_lcb_5pct,
    })
    wconn.execute(
        """INSERT INTO decision_certificates
           (certificate_id, certificate_type, schema_version,
            canonicalization_version, semantic_key, claim_type, mode,
            decision_time, authority_id, authority_version, algorithm_id,
            algorithm_version, payload_json, payload_hash, certificate_hash,
            verifier_status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"cert-{certificate_hash[:8]}", "ActionableTradeCertificate", 1,
         "v1", f"sk-{certificate_hash[:8]}", "actionable_trade", "LIVE",
         "2026-06-21T00:00:00Z", "auth", "1", "algo", "1", payload,
         f"ph-{certificate_hash[:8]}", certificate_hash, verifier_status,
         "2026-06-21T00:00:00Z"),
    )


def _seed_audit_bridge_row(
    wconn: sqlite3.Connection,
    *,
    audit_id: str,
    condition_id: str,
    direction: str,
    token_id: str,
    expected_edge_source_certificate_hash,
) -> None:
    """Seed the edli_live_profit_audit fill row the grader bridges to.

    q_live is NULL on the row (the live posture); the cert reached via
    expected_edge_source_certificate_hash + (condition_id, direction) is the
    authority. This is the bridge the position_current loader walks.
    """
    wconn.execute(
        """INSERT INTO edli_live_profit_audit
           (audit_id, event_id, aggregate_id, condition_id, token_id, direction,
            avg_fill_price, filled_size, q_live, q_lcb_5pct,
            expected_edge_source_certificate_hash, order_lifecycle_state,
            created_at, schema_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (audit_id, f"evt-{audit_id}", f"agg-{audit_id}", condition_id, token_id,
         direction, 0.30, 10.0, None, None,
         expected_edge_source_certificate_hash, "FILLED",
         "2026-06-21T06:00:00Z", 1),
    )


def _seed_q_market_and_settlement(
    fconn: sqlite3.Connection,
    *,
    condition_id: str,
    city: str,
    target_date: str,
    range_low: float,
    range_high: float,
    settlement_value: float,
) -> None:
    """Seed one market_events bin + VERIFIED settlement on the forecasts DB."""
    fconn.execute(
        """INSERT INTO market_events
           (market_slug, condition_id, city, target_date, temperature_metric,
            range_low, range_high, recorded_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (f"{city}-{condition_id}", condition_id, city, target_date, "high",
         range_low, range_high, "2026-06-20T00:00:00Z"),
    )
    fconn.execute(
        """INSERT INTO settlement_outcomes
           (city, target_date, temperature_metric, settlement_value,
            settlement_unit, settled_at, authority, provenance_json, recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (city, target_date, "high", settlement_value, "F",
         "2026-06-21T00:00:00Z", "VERIFIED", "{}", "2026-06-21T00:00:00Z"),
    )


def _seed_q_position(
    tconn: sqlite3.Connection,
    *,
    position_id: str,
    condition_id: str,
    direction: str,
    city: str,
    target_date: str,
) -> None:
    """Seed a settled trades.position_current row (the ledger the grader reads)."""
    tconn.execute(
        """INSERT INTO position_current
           (position_id, phase, strategy_key, condition_id, direction, entry_price,
            shares, cost_basis_usd, city, target_date, temperature_metric, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, "settled", "center_buy", condition_id, direction, 0.30, 10.0,
         3.0, city, target_date, "high", "2026-06-21T06:00:00Z"),
    )


def test_Q1_grader_populates_q_from_resolvable_certificate(tmp_path) -> None:
    """When the decision-q cert IS resolvable (bridged from position_current via
    the audit row's expected_edge_source_certificate_hash), the grader populates
    q_live / q_lcb_5pct from the cert payload (NOT from a time-reconstructed
    posterior) and grades SKILL/LUCK on that REAL decision-q.

    Fixture: a buy_no on the 90-91F bin that WON (settle 87, OUT of bin). The
    cert's q_live=0.80 means our NO held-token q=0.80 > 0.5 -> decision-q supports
    the NO -> SKILL_WIN, and the grade carries the cert's q values.
    """
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)
    fconn = sqlite3.connect(fcst_path); init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path); init_schema(tconn)
    _seed_q_market_and_settlement(
        fconn, condition_id="condQ1", city="Phoenix", target_date="2026-06-20",
        range_low=90.0, range_high=91.0, settlement_value=87.0,  # OUT -> NO wins
    )
    fconn.commit(); fconn.close()

    cert_hash = "a" * 64
    _seed_belief_certificate(
        wconn, certificate_hash=cert_hash, condition_id="condQ1", token_id="tokQ1",
        q_live=0.80, q_lcb_5pct=0.70,
    )
    _seed_audit_bridge_row(
        wconn, audit_id="audQ1", condition_id="condQ1", direction="buy_no",
        token_id="tokQ1", expected_edge_source_certificate_hash=cert_hash,
    )
    wconn.commit()
    _seed_q_position(
        tconn, position_id="posQ1", condition_id="condQ1", direction="buy_no",
        city="Phoenix", target_date="2026-06-20",
    )
    tconn.commit(); tconn.close()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    grades = load_settled_positions(wconn)
    assert len(grades) == 1
    g = grades[0]
    # q resolved from the cert (the position ledger carries no q at all).
    assert g.q_live == pytest.approx(0.80, abs=1e-9), (
        "q_live must be resolved from the immutable decision-q certificate"
    )
    assert g.q_lcb_5pct == pytest.approx(0.70, abs=1e-9)
    # The real decision-q (NO held-token q 0.80 > 0.5) supports the NO -> SKILL_WIN.
    assert g.category == "SKILL_WIN", g.rationale
    assert g.counts_as_skill_win is True
    wconn.close()


def test_Q2_unresolvable_cert_grades_unattributable_never_skill_or_lucky(
    tmp_path,
) -> None:
    """When the decision-q cert is missing / unresolvable, the position grades
    UNATTRIBUTABLE_Q_MISSING — NEVER SKILL_WIN / LUCKY_WIN, and is NEVER silently
    time-reconstructed as the skill authority (excluded from the skill
    denominator).

    Fixture: a WON buy_no whose bridging audit row points at a cert hash that does
    not resolve (no decision_certificates row). Without the cert there is no
    immutable decision-q, so the win cannot be attributed to skill or luck.
    """
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)
    fconn = sqlite3.connect(fcst_path); init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path); init_schema(tconn)
    _seed_q_market_and_settlement(
        fconn, condition_id="condQ2", city="Dallas", target_date="2026-06-20",
        range_low=90.0, range_high=91.0, settlement_value=87.0,  # OUT -> NO wins
    )
    fconn.commit(); fconn.close()

    # Bridging audit row references a cert hash with NO matching cert row.
    _seed_audit_bridge_row(
        wconn, audit_id="audQ2", condition_id="condQ2", direction="buy_no",
        token_id="tokQ2", expected_edge_source_certificate_hash="deadbeef" * 8,
    )
    wconn.commit()
    _seed_q_position(
        tconn, position_id="posQ2", condition_id="condQ2", direction="buy_no",
        city="Dallas", target_date="2026-06-20",
    )
    tconn.commit(); tconn.close()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    grades = load_settled_positions(wconn)
    assert len(grades) == 1
    g = grades[0]
    assert g.category == "UNATTRIBUTABLE_Q_MISSING", g.rationale
    assert g.category not in ("SKILL_WIN", "LUCKY_WIN"), (
        "an unresolvable decision-q must never be classified as a win category"
    )
    assert g.counts_as_skill_win is False
    # q stays None — never invented from a time-reconstructed posterior.
    assert g.q_live is None
    # Persisting it must succeed (the CHECK accepts the new category) and it must
    # be excluded from the skill denominator.
    persist_grade(wconn, g)
    rate = compute_skill_win_rate(wconn)
    assert rate.skill_denominator == 0, (
        "UNATTRIBUTABLE_Q_MISSING must be excluded from the skill denominator"
    )
    wconn.close()


def test_Q3_no_audit_bridge_grades_unattributable(tmp_path) -> None:
    """A settled position with NO bridging edli_live_profit_audit row at all (so no
    path to the immutable decision-q) grades UNATTRIBUTABLE_Q_MISSING — there is no
    cert hash to resolve."""
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)
    fconn = sqlite3.connect(fcst_path); init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path); init_schema(tconn)
    _seed_q_market_and_settlement(
        fconn, condition_id="condQ3", city="Austin", target_date="2026-06-20",
        range_low=90.0, range_high=91.0, settlement_value=87.0,
    )
    fconn.commit(); fconn.close()

    # No edli_live_profit_audit row for condQ3 -> no cert hash bridge.
    _seed_q_position(
        tconn, position_id="posQ3", condition_id="condQ3", direction="buy_no",
        city="Austin", target_date="2026-06-20",
    )
    tconn.commit(); tconn.close()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    grades = load_settled_positions(wconn)
    assert len(grades) == 1
    assert grades[0].category == "UNATTRIBUTABLE_Q_MISSING", grades[0].rationale
    assert grades[0].counts_as_skill_win is False
    wconn.close()


# ---------------------------------------------------------------------------
# LX-E (2026-07-13): position_decision_attribution reader precedence
# ---------------------------------------------------------------------------
#
# docs/rebuild/local_ledger_excision_2026-07-12.md Round-2 delta §(c): the reader
# reads trades.position_decision_attribution FIRST; the legacy (condition_id,
# direction) inference is a fallback ONLY for positions with no attribution row at
# all (predating the table). An explicit UNATTRIBUTABLE row is never second-guessed
# via the legacy path.

def _seed_attribution_row(
    tconn: sqlite3.Connection,
    *,
    position_id: str,
    resolution: str,
    decision_certificate_hash: Optional[str],
    command_id: str = "cmd-1",
) -> None:
    from src.state.schema.position_decision_attribution_schema import ensure_table

    ensure_table(tconn)
    tconn.execute(
        """INSERT INTO position_decision_attribution
           (attribution_id, position_id, command_id, decision_certificate_hash,
            resolution, resolution_reason, source, intent_kind, created_at,
            schema_version)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            f"attr-{position_id}", position_id, command_id, decision_certificate_hash,
            resolution,
            None if resolution == "ATTRIBUTED" else "no_audit_row_for_command",
            "BACKFILL", "ENTRY", "2026-06-20T00:00:00Z", 1,
        ),
    )


def test_LXE_attribution_table_row_takes_precedence_over_legacy_bridge(tmp_path) -> None:
    """A position with an ATTRIBUTED position_decision_attribution row resolves its
    certificate hash from THAT row, even when the legacy (condition_id, direction)
    bridge would resolve a DIFFERENT hash — the new table wins, no fallback."""
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)
    fconn = sqlite3.connect(fcst_path); init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path); init_schema(tconn)
    _seed_q_market_and_settlement(
        fconn, condition_id="condLXE1", city="Miami", target_date="2026-06-20",
        range_low=90.0, range_high=91.0, settlement_value=87.0,  # OUT -> NO wins
    )
    fconn.commit(); fconn.close()

    # Legacy bridge would resolve "cert-legacy" via (condition_id, direction).
    legacy_hash = "b" * 64
    _seed_belief_certificate(
        wconn, certificate_hash=legacy_hash, condition_id="condLXE1", token_id="tokLXE1",
        q_live=0.20, q_lcb_5pct=0.10,  # supports LOSING the NO (q<0.5) if used
    )
    _seed_audit_bridge_row(
        wconn, audit_id="audLXE1", condition_id="condLXE1", direction="buy_no",
        token_id="tokLXE1", expected_edge_source_certificate_hash=legacy_hash,
    )
    wconn.commit()

    # position_decision_attribution resolves a DIFFERENT cert (the real, exact link).
    exact_hash = "c" * 64
    _seed_belief_certificate(
        wconn, certificate_hash=exact_hash, condition_id="condLXE1", token_id="tokLXE1",
        q_live=0.80, q_lcb_5pct=0.70,  # supports WINNING the NO (q>0.5)
    )
    wconn.commit()

    _seed_q_position(
        tconn, position_id="posLXE1", condition_id="condLXE1", direction="buy_no",
        city="Miami", target_date="2026-06-20",
    )
    _seed_attribution_row(
        tconn, position_id="posLXE1", resolution="ATTRIBUTED",
        decision_certificate_hash=exact_hash,
    )
    tconn.commit(); tconn.close()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    grades = load_settled_positions(wconn)
    assert len(grades) == 1
    g = grades[0]
    assert g.q_live == pytest.approx(0.80, abs=1e-9), (
        "must resolve the EXACT attribution-table hash, not the legacy-bridge hash"
    )
    assert g.category == "SKILL_WIN", g.rationale
    wconn.close()


def test_LXE_explicit_unattributable_row_skips_grading_without_legacy_fallback(tmp_path) -> None:
    """A position marked UNATTRIBUTABLE in position_decision_attribution grades
    UNATTRIBUTABLE_Q_MISSING even though the legacy (condition_id, direction)
    bridge WOULD resolve a certificate — the explicit verdict is never
    second-guessed."""
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)
    fconn = sqlite3.connect(fcst_path); init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path); init_schema(tconn)
    _seed_q_market_and_settlement(
        fconn, condition_id="condLXE2", city="Tampa", target_date="2026-06-20",
        range_low=90.0, range_high=91.0, settlement_value=87.0,
    )
    fconn.commit(); fconn.close()

    # Legacy bridge WOULD resolve this hash — must be ignored.
    legacy_hash = "d" * 64
    _seed_belief_certificate(
        wconn, certificate_hash=legacy_hash, condition_id="condLXE2", token_id="tokLXE2",
        q_live=0.80, q_lcb_5pct=0.70,
    )
    _seed_audit_bridge_row(
        wconn, audit_id="audLXE2", condition_id="condLXE2", direction="buy_no",
        token_id="tokLXE2", expected_edge_source_certificate_hash=legacy_hash,
    )
    wconn.commit()

    _seed_q_position(
        tconn, position_id="posLXE2", condition_id="condLXE2", direction="buy_no",
        city="Tampa", target_date="2026-06-20",
    )
    _seed_attribution_row(
        tconn, position_id="posLXE2", resolution="UNATTRIBUTABLE",
        decision_certificate_hash=None,
    )
    tconn.commit(); tconn.close()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    grades = load_settled_positions(wconn)
    assert len(grades) == 1
    assert grades[0].category == "UNATTRIBUTABLE_Q_MISSING", grades[0].rationale
    assert grades[0].q_live is None
    wconn.close()


def test_LXE_no_attribution_row_falls_back_to_legacy_bridge(tmp_path) -> None:
    """A position with NO position_decision_attribution row at all (predates the
    table + backfill) falls back to the legacy (condition_id, direction) bridge —
    identical behavior to pre-LX-E."""
    world_path = str(tmp_path / "world.db")
    fcst_path = str(tmp_path / "fcst.db")
    trades_path = str(tmp_path / "trades.db")
    wconn = sqlite3.connect(world_path); init_schema(wconn)
    fconn = sqlite3.connect(fcst_path); init_schema_forecasts(fconn)
    tconn = sqlite3.connect(trades_path); init_schema(tconn)
    _seed_q_market_and_settlement(
        fconn, condition_id="condLXE3", city="Orlando", target_date="2026-06-20",
        range_low=90.0, range_high=91.0, settlement_value=87.0,
    )
    fconn.commit(); fconn.close()

    legacy_hash = "e" * 64
    _seed_belief_certificate(
        wconn, certificate_hash=legacy_hash, condition_id="condLXE3", token_id="tokLXE3",
        q_live=0.80, q_lcb_5pct=0.70,
    )
    _seed_audit_bridge_row(
        wconn, audit_id="audLXE3", condition_id="condLXE3", direction="buy_no",
        token_id="tokLXE3", expected_edge_source_certificate_hash=legacy_hash,
    )
    wconn.commit()

    _seed_q_position(
        tconn, position_id="posLXE3", condition_id="condLXE3", direction="buy_no",
        city="Orlando", target_date="2026-06-20",
    )
    # No attribution row written for posLXE3 at all.
    tconn.commit(); tconn.close()
    wconn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))
    wconn.execute("ATTACH DATABASE ? AS trades", (trades_path,))

    grades = load_settled_positions(wconn)
    assert len(grades) == 1
    assert grades[0].q_live == pytest.approx(0.80, abs=1e-9)
    assert grades[0].category == "SKILL_WIN", grades[0].rationale
