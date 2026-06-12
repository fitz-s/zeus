# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: Operator mission 2026-06-09 — the ONE standing shadow-vs-live
#   comparator (src/analysis/shadow_comparator.py). These are RELATIONSHIP tests:
#   they assert the cross-module property "when a shadow lane's q flows into the
#   comparator alongside the live q and BOTH are scored against the SAME settled
#   outcome (via grade_receipt), the verdict reflects which side the settled
#   record favors" — not merely that a function returns a value. The settlement
#   truth is produced ONLY through the spine (grade_receipt); no test hand-sets a
#   win/loss bool that bypasses the Direction Law.
"""Tests for the standing shadow-vs-live comparator.

Coverage (mission §4):
  1. synthetic settled cohort where SHADOW is strictly better -> PROMOTE_SUPPORTED
     with the correct paired stats (CI excludes 0, sign test agrees);
  2. a TIE (shadow == live) -> INSUFFICIENT_N (CI straddles 0, no promote);
  3. n too small -> INSUFFICIENT_N (the floor gate fires before any verdict);
  4. missing shadow receipts -> honest absence (missing_shadow surfaced, no
     fabricated cohort).

Plus the live-DB-shaped pipeline test: the day0 adapter over an in-memory WORLD
DB with day0 events but NO shadow-cert field returns honest INSUFFICIENT_N.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.analysis.shadow_comparator import (
    CohortObservation,
    PairedCell,
    _bernoulli_scores,
    day0_remaining_day_adapter,
    pair_settled_cells,
    score_candidate,
)
from src.state.db import init_schema, init_schema_forecasts


# --------------------------------------------------------------------------- #
# In-memory WORLD + forecasts fixture (mirrors test_attribution_receipt_repoint)
# --------------------------------------------------------------------------- #


def _attach(world_conn: sqlite3.Connection, fcst_path: str) -> None:
    world_conn.execute("ATTACH DATABASE ? AS forecasts", (fcst_path,))


@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _insert_settlement(conn, *, city, target_date, metric, value, unit) -> None:
    conn.execute(
        """
        INSERT INTO settlement_outcomes
            (city, target_date, temperature_metric,
             settlement_value, settlement_unit, authority,
             provenance_json, recorded_at)
        VALUES (?, ?, ?, ?, ?, 'VERIFIED', '{}', '2026-06-02T12:00:00+00:00')
        """,
        (city, target_date, metric, value, unit),
    )


def _settled_world(tmp_path, settlements):
    """Return a WORLD conn with `settlements` ATTACHed as VERIFIED forecasts rows.

    settlements: list of (city, target_date, metric, value, unit).
    """
    fcst_path = str(tmp_path / "fcst.db")
    fconn = sqlite3.connect(fcst_path)
    init_schema_forecasts(fconn)
    for city, tdate, metric, value, unit in settlements:
        _insert_settlement(fconn, city=city, target_date=tdate, metric=metric,
                           value=value, unit=unit)
    fconn.commit()
    fconn.close()
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _attach(conn, fcst_path)
    return conn


# --------------------------------------------------------------------------- #
# Bernoulli scoring sanity (the proper-score reuse)
# --------------------------------------------------------------------------- #


def test_bernoulli_score_rewards_confident_correct_call():
    """A confident-correct q (q=0.9, won) must score LOWER loss than a confident-
    wrong q (q=0.9, lost). This is the proper-scoring property the verdict relies on."""
    ll_correct, br_correct = _bernoulli_scores(0.9, won=True)
    ll_wrong, br_wrong = _bernoulli_scores(0.9, won=False)
    assert ll_correct < ll_wrong
    assert br_correct < br_wrong
    # A q=0.5 is the maximum-entropy reference: its log-loss is -ln(0.5).
    ll_half, _ = _bernoulli_scores(0.5, won=True)
    assert ll_half == pytest.approx(0.6931, abs=1e-3)


# --------------------------------------------------------------------------- #
# §1 — shadow strictly better -> PROMOTE_SUPPORTED
# --------------------------------------------------------------------------- #


def _paired_cells(specs):
    """Build PairedCells from (won, shadow_q, live_q) specs via the real scorer."""
    cells = []
    for i, (won, sq, lq) in enumerate(specs):
        s_ll, s_br = _bernoulli_scores(sq, won)
        l_ll, l_br = _bernoulli_scores(lq, won)
        cells.append(PairedCell(
            cohort_key=("CityX", "high", f"2026-06-{i:02d}", "64-65°F", "buy_yes"),
            won=won, shadow_q=sq, live_q=lq,
            shadow_logloss=s_ll, live_logloss=l_ll,
            shadow_brier=s_br, live_brier=l_br,
        ))
    return cells


def test_shadow_strictly_better_promotes():
    """On every settled cell the shadow q is closer to the truth than the live q.
    The paired log-loss difference is consistently negative -> PROMOTE_SUPPORTED,
    CI excludes 0, and the sign test counts every cell shadow-better."""
    # 40 cells: winners where shadow=0.85 vs live=0.55; losers where shadow=0.15
    # vs live=0.45. Shadow is always nearer the realized outcome.
    specs = []
    for i in range(40):
        if i % 2 == 0:
            specs.append((True, 0.85, 0.55))
        else:
            specs.append((False, 0.15, 0.45))
    cells = _paired_cells(specs)
    board = score_candidate("syn_shadow_better", cells, {"paired": len(cells)}, min_n=30)

    assert board.verdict == "PROMOTE_SUPPORTED"
    assert board.shadow_logloss_mean < board.live_logloss_mean
    assert board.logloss_diff_mean < 0.0
    # CI must exclude 0 on the better side.
    assert board.logloss_diff_ci_hi < 0.0
    # Sign test: every cell favors shadow.
    assert board.sign_shadow_better == 40
    assert board.sign_live_better == 0
    assert board.sign_p_value < 0.05


# --------------------------------------------------------------------------- #
# §2 — tie -> INSUFFICIENT_N (no promote)
# --------------------------------------------------------------------------- #


def test_tie_does_not_promote():
    """Shadow q == live q on every cell: the paired difference is exactly 0, the
    CI is degenerate at 0 (does not exclude 0) -> INSUFFICIENT_N, never PROMOTE."""
    specs = [(i % 2 == 0, 0.6, 0.6) for i in range(40)]
    cells = _paired_cells(specs)
    board = score_candidate("syn_tie", cells, {"paired": len(cells)}, min_n=30)

    assert board.verdict == "INSUFFICIENT_N"
    assert board.logloss_diff_mean == pytest.approx(0.0, abs=1e-12)
    assert board.sign_shadow_better == 0
    assert board.sign_live_better == 0


def test_noisy_tie_with_zero_centered_ci_does_not_promote():
    """Shadow and live differ per-cell but neither dominates: on half the WINNING
    cells shadow is the sharper side, on the other half live is — by equal
    log-loss magnitude, so the mean paired difference is ~0 and the CI straddles
    0 -> INSUFFICIENT_N (the settled record does not separate the sides)."""
    specs = []
    for i in range(60):
        won = True  # fix the outcome so the two halves cancel exactly in log-loss
        if i % 2 == 0:
            specs.append((won, 0.7, 0.6))   # shadow sharper-correct
        else:
            specs.append((won, 0.6, 0.7))   # live sharper-correct (mirror)
    cells = _paired_cells(specs)
    board = score_candidate("syn_noisy_tie", cells, {"paired": len(cells)}, min_n=30)

    assert board.verdict == "INSUFFICIENT_N"
    assert board.logloss_diff_ci_lo < 0.0 < board.logloss_diff_ci_hi


# --------------------------------------------------------------------------- #
# §3 — n too small -> INSUFFICIENT_N
# --------------------------------------------------------------------------- #


def test_small_n_is_insufficient_even_when_shadow_better():
    """Only 5 settled cells, all shadow-better: the floor gate fires FIRST. A
    shadow that looks better on thin data must NOT be promoted."""
    specs = [(True, 0.9, 0.5)] * 5
    cells = _paired_cells(specs)
    board = score_candidate("syn_small_n", cells, {"paired": len(cells)}, min_n=30)

    assert board.verdict == "INSUFFICIENT_N"
    assert "min_n" in board.verdict_line
    assert board.n_settled == 5


# --------------------------------------------------------------------------- #
# §4 — missing shadow receipts -> honest absence
# --------------------------------------------------------------------------- #


def test_missing_shadow_is_honest_absence(world_conn, tmp_path):
    """A cohort cell with a live q but NO shadow q is DROPPED as missing_shadow —
    never fabricated into a paired comparison. The verdict is an honest
    INSUFFICIENT_N citing the absence."""
    conn = _settled_world(tmp_path, [("Tokyo", "2026-06-01", "high", 17.0, "C")])
    observations = [
        CohortObservation(
            city="Tokyo", metric="high", target_date="2026-06-01",
            bin_label="17°C", direction="buy_yes",
            shadow_q=None, live_q=0.6,  # shadow lane never persisted its q
        ),
    ]
    paired, counters = pair_settled_cells(observations, conn)
    assert paired == []
    assert counters["missing_shadow"] == 1
    assert counters["paired"] == 0

    board = score_candidate("day0_remaining_day_q", paired, counters, min_n=30)
    assert board.verdict == "INSUFFICIENT_N"
    assert "honest absence" in board.verdict_line


def test_no_settlement_drops_cell_not_fabricated(world_conn, tmp_path):
    """A cohort cell with BOTH q sides but NO VERIFIED settlement is dropped as
    no_settlement — the comparator never scores against an unsettled/unverified
    outcome (data-provenance law)."""
    conn = _settled_world(tmp_path, [])  # zero settlements
    observations = [
        CohortObservation(
            city="Paris", metric="high", target_date="2026-06-05",
            bin_label="20°C", direction="buy_yes",
            shadow_q=0.7, live_q=0.5,
        ),
    ]
    paired, counters = pair_settled_cells(observations, conn)
    assert paired == []
    assert counters["no_settlement"] == 1


# --------------------------------------------------------------------------- #
# Full pipeline through the settlement spine (grade_receipt produces `won`)
# --------------------------------------------------------------------------- #


def test_pipeline_grades_through_spine_and_scores_both_sides(world_conn, tmp_path):
    """End-to-end: two cohort cells settle, grade_receipt decides each `won` via
    the Direction Law, and BOTH q sides are scored against it. The shadow side
    (sharper, correct) beats the live side on the cell it called better."""
    conn = _settled_world(tmp_path, [
        # Tokyo 17°C settled -> a buy_yes on the 17°C bin WINS.
        ("Tokyo", "2026-06-01", "high", 17.0, "C"),
        # Paris 22°C settled -> a buy_yes on the 20-21°C bin LOSES.
        ("Paris", "2026-06-02", "high", 22.0, "C"),
    ])
    observations = [
        CohortObservation(city="Tokyo", metric="high", target_date="2026-06-01",
                          bin_label="17°C", direction="buy_yes",
                          shadow_q=0.80, live_q=0.55),
        CohortObservation(city="Paris", metric="high", target_date="2026-06-02",
                          bin_label="20°C", direction="buy_yes",
                          shadow_q=0.10, live_q=0.40),
    ]
    paired, counters = pair_settled_cells(observations, conn)
    assert counters["paired"] == 2
    by_city = {c.cohort_key[0]: c for c in paired}
    # grade_receipt applied the Direction Law, not a hand-set bool.
    assert by_city["Tokyo"].won is True
    assert by_city["Paris"].won is False
    # On both cells the shadow q was nearer the truth -> lower log-loss.
    assert by_city["Tokyo"].shadow_logloss < by_city["Tokyo"].live_logloss
    assert by_city["Paris"].shadow_logloss < by_city["Paris"].live_logloss


def test_buy_no_direction_law_through_spine(world_conn, tmp_path):
    """A buy_no position WINS iff the settled value lands OUTSIDE the bin. The
    comparator scores P(traded-outcome-wins)=q_live against that graded `won`,
    so a buy_no whose bin the settlement missed is a WIN and a high shadow q is
    rewarded."""
    conn = _settled_world(tmp_path, [("Seoul", "2026-06-03", "high", 25.0, "C")])
    observations = [
        # buy_no on "20°C"; settled 25°C is OUTSIDE -> buy_no WINS.
        CohortObservation(city="Seoul", metric="high", target_date="2026-06-03",
                          bin_label="20°C", direction="buy_no",
                          shadow_q=0.85, live_q=0.60),
    ]
    paired, counters = pair_settled_cells(observations, conn)
    assert counters["paired"] == 1
    assert paired[0].won is True  # buy_no won (settled outside bin)
    assert paired[0].shadow_logloss < paired[0].live_logloss


# --------------------------------------------------------------------------- #
# day0 adapter over a live-shaped WORLD DB: honest absence (no shadow-cert yet)
# --------------------------------------------------------------------------- #


def _insert_day0_event_and_receipt(conn, *, city, tdate, metric, bin_label,
                                   direction, q_live, q_mode, q_remaining_day=None,
                                   rid="r1"):
    event_id = f"evt_{rid}"
    payload = {"_edli_day0_q_mode": q_mode} if q_mode else {}
    conn.execute(
        """
        INSERT INTO opportunity_events
            (event_id, event_type, entity_key, source, observed_at, available_at,
             received_at, payload_hash, idempotency_key, payload_json,
             schema_version, created_at)
        VALUES (?, 'DAY0_EXTREME_UPDATED', ?, 'test', ?, ?, ?, 'ph', ?, ?, 1, ?)
        """,
        (event_id, f"{city}:{tdate}", "2026-06-01T00:00:00+00:00",
         "2026-06-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00",
         f"idem_{rid}", json.dumps(payload), "2026-06-01T00:00:00+00:00"),
    )
    rj = {
        "city": city, "target_date": tdate, "metric": metric,
        "bin_label": bin_label, "direction": direction, "q_live": q_live,
    }
    if q_remaining_day is not None:
        rj["q_remaining_day"] = q_remaining_day
    conn.execute(
        """
        INSERT INTO edli_no_submit_receipts
            (receipt_id, event_id, decision_time, direction, c_fee_adjusted,
             kelly_size_usd, side_effect_status, fdr_hypothesis_count,
             projection_hash, receipt_json, receipt_hash, created_at, schema_version)
        VALUES (?, ?, ?, ?, ?, ?, 'NO_SUBMIT', 1, ?, ?, ?, ?, 29)
        """,
        (rid, event_id, "2026-06-01T12:00:00+00:00", direction, 0.4, 5.0,
         f"ph_{rid}", json.dumps(rj), f"rh_{rid}", "2026-06-01T12:00:00+00:00"),
    )


def test_day0_adapter_honest_absence_without_shadow_cert(world_conn):
    """Live state: day0 receipts carry ONLY the legacy q (no _edli_day0_q_mode=
    remaining_day, no q_remaining_day shadow-cert). The adapter yields the cell
    with shadow_q=None -> the comparator reports missing_shadow, not a fabricated
    pairing. This is the verdict the operator sees until the day0 owner adds the
    one-line shadow-cert write."""
    _insert_day0_event_and_receipt(
        world_conn, city="NYC", tdate="2026-06-01", metric="high",
        bin_label="64-65°F", direction="buy_yes", q_live=0.62, q_mode=None,
    )
    world_conn.commit()
    obs = day0_remaining_day_adapter(world_conn)
    assert len(obs) == 1
    assert obs[0].live_q == pytest.approx(0.62)
    assert obs[0].shadow_q is None  # honest absence


def test_day0_adapter_reads_shadow_cert_when_present(world_conn):
    """Once the SPEC'd shadow-cert lands (q_remaining_day written alongside
    q_live on the SAME decision while the flag is OFF), the adapter pairs the
    shadow vs live q from ONE receipt — the only way to get a true paired cell
    while the promotion is shadowed."""
    _insert_day0_event_and_receipt(
        world_conn, city="NYC", tdate="2026-06-01", metric="high",
        bin_label="64-65°F", direction="buy_yes", q_live=0.62, q_mode=None,
        q_remaining_day=0.48,
    )
    world_conn.commit()
    obs = day0_remaining_day_adapter(world_conn)
    assert len(obs) == 1
    assert obs[0].live_q == pytest.approx(0.62)
    assert obs[0].shadow_q == pytest.approx(0.48)
