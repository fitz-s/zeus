# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: Mission — automated 守護 settlement measurement loop.
#   Relationship tests FIRST (Fitz methodology): verify the cross-module
#   invariants that hold when executed-fill economics flow into the
#   settlement-grading spine and out as after-cost win-rate, NOT just function
#   I/O. The load-bearing relationships under test:
#     R1  buy_no payout semantics: a buy_no fill WINS iff the settled value
#         lands OUTSIDE the traded bin (Direction Law via grade_receipt), and
#         its NO token then pays $1/share.
#     R2  after-cost PnL subtracts the captured fee envelope.
#     R3  n=0 produces an honest report, never a crash.
#     R4  SUSPEND_CANDIDATE fires iff the rolling-window CI UPPER bound < 0.50.
#     R5  small-n suppresses the point win-rate claim (CI only).
"""Relationship + function tests for settlement_guard_report."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.analysis import settlement_guard_report as sgr
from src.analysis.settlement_guard_report import (
    GradedFill,
    build_report,
    clopper_pearson_ci,
    load_graded_fills,
    one_line_summary,
    report_to_json,
    report_to_markdown,
)

NOW = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)


def _fill(
    *,
    city="Tokyo",
    metric="low",
    direction="buy_no",
    range_low=16.0,
    range_high=None,  # defaults to range_low (exact point bin) when omitted
    settled_value=19.0,
    unit="C",
    price=0.90,
    size=10.0,
    fees=0.0,
    fees_were_null=False,
    q_entry=None,
    days_ago=0,
) -> GradedFill:
    """Build a graded fill via the SAME grading the production path uses.

    This routes the synthetic (bin, direction, settlement) through grade_receipt
    so the test asserts the real Direction Law, not a hand-computed `won`.
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.types.market import Bin

    if range_high is None:
        range_high = range_low
    deg = "°C" if unit == "C" else "°F"
    label = f"{range_low:g}{deg}" if range_low == range_high else f"{range_low:g}-{range_high:g}{deg}"
    bin_obj = Bin(low=range_low, high=range_high, unit=unit, label=label)

    class _S:
        settlement_value = settled_value
        settlement_unit = unit

    graded = grade_receipt(bin_obj, direction, _S())
    payoff = 1.0 if graded.won else 0.0
    cost_basis = price * size + fees
    after_cost = (payoff - price) * size - fees
    settled_at = (NOW - timedelta(days=days_ago)).isoformat()
    return GradedFill(
        condition_id=f"cid-{city}-{metric}-{settled_value}",
        city=city,
        target_date="2026-06-08",
        metric=metric,
        direction=direction,
        traded_bin_label=label,
        settled_value=settled_value,
        settlement_unit=unit,
        avg_fill_price=price,
        filled_size=size,
        fees_usd=fees,
        fees_were_null=fees_were_null,
        q_entry=q_entry,
        won=graded.won,
        settled_in_bin=graded.settled_in_bin,
        cost_basis_usd=cost_basis,
        after_cost_pnl_usd=after_cost,
        settled_at_utc=settled_at,
    )


# ---------------------------------------------------------------------------
# R1 — buy_no payout semantics (the Direction Law boundary)
# ---------------------------------------------------------------------------

def test_R1_buy_no_wins_when_settled_value_outside_bin():
    """buy_no on bin 16°C, settled 19°C → settled NOT in bin → buy_no WINS."""
    f = _fill(direction="buy_no", range_low=16.0, range_high=16.0, settled_value=19.0)
    assert f.settled_in_bin is False
    assert f.won is True


def test_R1_buy_no_loses_when_settled_value_inside_bin():
    """buy_no on bin 19°C, settled 19°C → settled IN bin → buy_no LOSES."""
    f = _fill(direction="buy_no", range_low=19.0, range_high=19.0, settled_value=19.0)
    assert f.settled_in_bin is True
    assert f.won is False


def test_R1_buy_yes_wins_when_settled_value_inside_bin():
    f = _fill(direction="buy_yes", range_low=19.0, range_high=19.0, settled_value=19.0)
    assert f.settled_in_bin is True
    assert f.won is True


def test_R1_buy_no_winner_pays_one_dollar_per_share_minus_entry():
    """A WINNING buy_no fill pays $1/share; PnL = (1 - price)*size - fees."""
    f = _fill(direction="buy_no", range_low=16.0, settled_value=19.0,
              price=0.90, size=10.0, fees=0.0)
    assert f.won is True
    # (1.0 - 0.90) * 10 = +1.00
    assert f.after_cost_pnl_usd == pytest.approx(1.00, abs=1e-9)


def test_R1_buy_no_loser_pays_zero_loses_full_cost_basis():
    f = _fill(direction="buy_no", range_low=19.0, settled_value=19.0,
              price=0.90, size=10.0, fees=0.0)
    assert f.won is False
    # (0.0 - 0.90) * 10 = -9.00
    assert f.after_cost_pnl_usd == pytest.approx(-9.00, abs=1e-9)


# ---------------------------------------------------------------------------
# R2 — after-cost PnL subtracts the captured fee envelope
# ---------------------------------------------------------------------------

def test_R2_fees_are_subtracted_from_after_cost_pnl():
    no_fee = _fill(direction="buy_no", range_low=16.0, settled_value=19.0,
                   price=0.80, size=10.0, fees=0.0)
    with_fee = _fill(direction="buy_no", range_low=16.0, settled_value=19.0,
                     price=0.80, size=10.0, fees=0.50)
    # Same win, same gross; the only difference is the $0.50 fee.
    assert with_fee.won is True
    assert no_fee.after_cost_pnl_usd - with_fee.after_cost_pnl_usd == pytest.approx(0.50, abs=1e-9)


def test_R2_aggregate_after_cost_pnl_sums_per_fill():
    fills = [
        _fill(direction="buy_no", range_low=16.0, settled_value=19.0, price=0.90, size=10.0),  # +1.00
        _fill(direction="buy_no", range_low=19.0, settled_value=19.0, price=0.90, size=10.0),  # -9.00
    ]
    report = build_report(fills, now_utc=NOW)
    assert report.overall.n_settled == 2
    assert report.overall.n_wins == 1
    assert report.overall.after_cost_pnl_usd == pytest.approx(-8.00, abs=1e-9)


def test_R2_null_fee_envelope_is_flagged_not_silently_dropped():
    fills = [_fill(direction="buy_no", range_low=16.0, settled_value=19.0,
                   fees=0.0, fees_were_null=True)]
    report = build_report(fills, now_utc=NOW)
    assert report.fee_coverage_gap_count == 1
    assert any("fee coverage gap" in n for n in report.notes)


# ---------------------------------------------------------------------------
# R3 — n=0 honest report (pre-first-settlement state)
# ---------------------------------------------------------------------------

def test_R3_empty_fills_produce_valid_report_not_crash():
    report = build_report([], now_utc=NOW)
    assert report.n_settled_total == 0
    assert report.overall.n_settled == 0
    assert report.overall.win_rate is None        # no point claim at n=0
    assert report.overall.ci_95 == (0.0, 1.0)     # total ignorance, not fabricated
    assert any("n=0" in n for n in report.notes)


def test_R3_empty_report_serialises_to_json_and_markdown():
    report = build_report([], now_utc=NOW)
    j = report_to_json(report)
    assert j["n_settled_total"] == 0
    md = report_to_markdown(report)
    assert "n=0" in one_line_summary(report)
    assert "Settlement Guard Report" in md


# ---------------------------------------------------------------------------
# R4 — SUSPEND_CANDIDATE CI-bound math (the regression sentinel)
# ---------------------------------------------------------------------------

def test_R4_suspend_fires_when_ci_upper_below_half():
    """A city with mostly LOSSES on the 30d window → CI upper < 0.50 → flagged."""
    # 1 win / 12 buy_no losses → strongly losing; CI upper bound below 0.50.
    fills = [_fill(direction="buy_no", range_low=16.0, settled_value=19.0, price=0.9)]  # win
    fills += [
        _fill(direction="buy_no", range_low=19.0, settled_value=19.0, price=0.9)  # loss
        for _ in range(12)
    ]
    report = build_report(fills, now_utc=NOW)
    assert len(report.suspend_candidates) == 1
    s = report.suspend_candidates[0]
    assert s["city_metric"] == "Tokyo|low"
    assert s["ci_upper"] < sgr.SUSPEND_CI_UPPER_BAR


def test_R4_no_suspend_when_ci_upper_above_half():
    """A mostly-winning city → CI upper well above 0.50 → not flagged."""
    fills = [
        _fill(direction="buy_no", range_low=16.0, settled_value=19.0)  # win
        for _ in range(12)
    ]
    fills.append(_fill(direction="buy_no", range_low=19.0, settled_value=19.0))  # 1 loss
    report = build_report(fills, now_utc=NOW)
    assert report.suspend_candidates == []


def test_R4_suspend_respects_window_excludes_old_fills():
    """Fills older than the SUSPEND window do not enter the sentinel count."""
    old_losses = [
        _fill(direction="buy_no", range_low=19.0, settled_value=19.0,
              days_ago=sgr.SUSPEND_WINDOW_DAYS + 5)
        for _ in range(20)
    ]
    report = build_report(old_losses, now_utc=NOW)
    # All losses are outside the window → no in-window rows → no flag.
    assert report.suspend_candidates == []


# ---------------------------------------------------------------------------
# R5 — small-n suppresses the point win-rate claim
# ---------------------------------------------------------------------------

def test_R5_small_n_returns_none_win_rate_but_keeps_ci():
    fills = [
        _fill(direction="buy_no", range_low=16.0, settled_value=19.0)
        for _ in range(sgr.MIN_N_FOR_POINT_CLAIM - 1)
    ]
    report = build_report(fills, now_utc=NOW)
    assert report.overall.n_settled == sgr.MIN_N_FOR_POINT_CLAIM - 1
    assert report.overall.win_rate is None            # no point claim
    assert report.overall.win_rate_raw is not None    # raw still available
    lo, hi = report.overall.ci_95
    assert 0.0 <= lo <= hi <= 1.0


def test_R5_large_n_yields_point_claim():
    fills = [
        _fill(direction="buy_no", range_low=16.0, settled_value=19.0)
        for _ in range(sgr.MIN_N_FOR_POINT_CLAIM)
    ]
    report = build_report(fills, now_utc=NOW)
    assert report.overall.win_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Calibration relationship: mean entry q vs realized win-rate (overconfidence)
# ---------------------------------------------------------------------------

def test_calibration_gap_positive_when_entry_q_overstates():
    """entry q=0.9 on rows that win only 50% → gap = 0.9 - 0.5 = +0.4 (overconfident)."""
    fills = [
        _fill(direction="buy_no", range_low=16.0, settled_value=19.0, q_entry=0.9),  # win
        _fill(direction="buy_no", range_low=19.0, settled_value=19.0, q_entry=0.9),  # loss
    ]
    report = build_report(fills, now_utc=NOW)
    assert report.overall.calibration_gap == pytest.approx(0.4, abs=1e-9)
    assert report.overall.brier == pytest.approx(((0.9 - 1) ** 2 + (0.9 - 0) ** 2) / 2, abs=1e-9)


def test_calibration_gap_none_when_no_q_captured():
    fills = [_fill(direction="buy_no", range_low=16.0, settled_value=19.0, q_entry=None)]
    report = build_report(fills, now_utc=NOW)
    assert report.overall.calibration_gap is None
    assert report.overall.brier is None


# ---------------------------------------------------------------------------
# Clopper-Pearson CI properties
# ---------------------------------------------------------------------------

def test_ci_zero_trials_is_total_ignorance():
    assert clopper_pearson_ci(0, 0) == (0.0, 1.0)


def test_ci_all_wins_upper_is_one():
    lo, hi = clopper_pearson_ci(9, 9)
    assert hi == 1.0
    assert 0.0 < lo < 1.0


def test_ci_rejects_impossible_counts():
    with pytest.raises(ValueError):
        clopper_pearson_ci(11, 10)


# ---------------------------------------------------------------------------
# End-to-end grading relationship: fill → market_events → settlement → grade
# ---------------------------------------------------------------------------

def _build_join_db() -> sqlite3.Connection:
    """In-memory DB mirroring the WORLD-main + forecasts-ATTACHed join shape.

    grade_receipt reads only settlement value+unit; load_graded_fills reads
    edli_live_profit_audit (main) + forecasts.market_events +
    forecasts.settlement_outcomes (ATTACHed). We model the ATTACH with a
    'forecasts' schema attached to an in-memory main.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS forecasts")
    conn.execute(
        """
        CREATE TABLE edli_live_profit_audit (
            condition_id TEXT, direction TEXT, avg_fill_price REAL,
            filled_size REAL, fees REAL, q_live REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE forecasts.market_events (
            condition_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, range_low REAL, range_high REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE forecasts.settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, settlement_unit TEXT, settled_at TEXT,
            authority TEXT
        )
        """
    )
    return conn


def test_e2e_join_grades_buy_no_winner_through_full_chain():
    conn = _build_join_db()
    # Tokyo low: traded bin 16°C buy_no, settled 19°C → buy_no WINS.
    conn.execute(
        "INSERT INTO edli_live_profit_audit VALUES (?,?,?,?,?,?)",
        ("cid1", "buy_no", 0.90, 10.0, 0.0, 0.7),
    )
    conn.execute(
        "INSERT INTO forecasts.market_events VALUES (?,?,?,?,?,?)",
        ("cid1", "Tokyo", "2026-06-08", "low", 16.0, 16.0),
    )
    conn.execute(
        "INSERT INTO forecasts.settlement_outcomes VALUES (?,?,?,?,?,?,?)",
        ("Tokyo", "2026-06-08", "low", 19.0, "C", NOW.isoformat(), "VERIFIED"),
    )
    fills = load_graded_fills(conn)
    assert len(fills) == 1
    f = fills[0]
    assert f.city == "Tokyo" and f.metric == "low"
    assert f.won is True and f.settled_in_bin is False
    assert f.after_cost_pnl_usd == pytest.approx(1.00, abs=1e-9)
    assert f.q_entry == pytest.approx(0.7)


def test_e2e_join_skips_unverified_settlement():
    conn = _build_join_db()
    conn.execute(
        "INSERT INTO edli_live_profit_audit VALUES (?,?,?,?,?,?)",
        ("cid1", "buy_no", 0.90, 10.0, 0.0, None),
    )
    conn.execute(
        "INSERT INTO forecasts.market_events VALUES (?,?,?,?,?,?)",
        ("cid1", "Tokyo", "2026-06-08", "low", 16.0, 16.0),
    )
    conn.execute(
        "INSERT INTO forecasts.settlement_outcomes VALUES (?,?,?,?,?,?,?)",
        ("Tokyo", "2026-06-08", "low", 19.0, "C", NOW.isoformat(), "DISPUTED"),
    )
    # No VERIFIED settlement → fill is not gradeable yet → 0 graded fills.
    assert load_graded_fills(conn) == []


def test_e2e_join_ignores_unfilled_rows():
    conn = _build_join_db()
    # filled_size=0 is not a real position.
    conn.execute(
        "INSERT INTO edli_live_profit_audit VALUES (?,?,?,?,?,?)",
        ("cid1", "buy_no", 0.90, 0.0, 0.0, None),
    )
    conn.execute(
        "INSERT INTO forecasts.market_events VALUES (?,?,?,?,?,?)",
        ("cid1", "Tokyo", "2026-06-08", "low", 16.0, 16.0),
    )
    conn.execute(
        "INSERT INTO forecasts.settlement_outcomes VALUES (?,?,?,?,?,?,?)",
        ("Tokyo", "2026-06-08", "low", 19.0, "C", NOW.isoformat(), "VERIFIED"),
    )
    assert load_graded_fills(conn) == []
