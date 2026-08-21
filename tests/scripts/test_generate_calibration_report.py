# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: scripts/generate_calibration_report.py — settled-only reliability
#   report generator. Tests the pure statistics (Wilson interval, binning, Murphy
#   decomposition, cut summaries) plus an end-to-end small-fixture pass over the
#   real read-only ATTACH pattern (settlement_attribution main + trades ATTACHed).
"""Tests for scripts/generate_calibration_report.py."""
from __future__ import annotations

import math
import sqlite3
import xml.dom.minidom as minidom

import pytest

from scripts.generate_calibration_report import (
    Row,
    build_report,
    cut_summary,
    decompose,
    lead_bucket_label,
    load_rows,
    reliability_bins,
    render_svg,
    ro_connect,
    wilson_interval,
)


# ---------------------------------------------------------------------------
# wilson_interval
# ---------------------------------------------------------------------------

class TestWilsonInterval:
    def test_zero_n_returns_none(self):
        assert wilson_interval(0, 0) == (None, None)

    def test_zero_hits_lower_bound_is_zero(self):
        lo, hi = wilson_interval(0, 20)
        assert lo == 0.0
        assert hi is not None and hi < 1.0

    def test_all_hits_upper_bound_is_one(self):
        lo, hi = wilson_interval(20, 20)
        assert hi == 1.0
        assert lo is not None and lo > 0.0

    def test_half_rate_interval_centered_near_half(self):
        lo, hi = wilson_interval(500, 1000)
        assert lo is not None and hi is not None
        assert lo < 0.5 < hi
        # A large-n 50% sample has a tight interval around 0.5.
        assert (hi - lo) < 0.07

    def test_thin_sample_interval_is_wide(self):
        lo, hi = wilson_interval(2, 4)
        assert hi - lo > 0.4


# ---------------------------------------------------------------------------
# reliability_bins
# ---------------------------------------------------------------------------

def _row(q_live, won, category="SKILL_WIN", direction="buy_no", strategy_key="opening_inertia", lead_hours=48.0):
    return Row(
        position_id=f"p-{id(object())}",
        category=category,
        direction=direction,
        won=won,
        q_live=q_live,
        settled_at="2026-07-01T00:00:00+00:00",
        strategy_key=strategy_key,
        lead_hours=lead_hours,
    )


class TestReliabilityBins:
    def test_q_of_exactly_one_lands_in_last_bin(self):
        rows = [_row(1.0, True)]
        bins = reliability_bins(rows, width=0.1)
        assert bins[-1].n == 1
        assert bins[-1].lo == pytest.approx(0.9)
        assert bins[-1].hi == pytest.approx(1.0)

    def test_q_of_exactly_zero_lands_in_first_bin(self):
        rows = [_row(0.0, False)]
        bins = reliability_bins(rows, width=0.1)
        assert bins[0].n == 1

    def test_empty_bin_reports_zero_n_and_nan_rate(self):
        rows = [_row(0.95, True)]
        bins = reliability_bins(rows, width=0.1)
        empty = bins[0]
        assert empty.n == 0
        assert math.isnan(empty.obs_rate)
        assert empty.wilson_lo is None

    def test_bin_boundary_goes_to_upper_bin(self):
        # 0.3 is the boundary between [0.2,0.3) and [0.3,0.4) — must land in the
        # upper (>=) bin per the half-open convention documented in the bin table.
        rows = [_row(0.3, True)]
        bins = reliability_bins(rows, width=0.1)
        assert bins[3].n == 1  # [0.3, 0.4)
        assert bins[2].n == 0  # [0.2, 0.3)

    def test_counts_and_mean_predicted_correct(self):
        rows = [_row(0.82, True), _row(0.88, False), _row(0.91, True)]
        bins = reliability_bins(rows, width=0.1)
        b8 = bins[8]  # [0.8, 0.9)
        assert b8.n == 2
        assert b8.wins == 1
        assert b8.mean_pred == pytest.approx((0.82 + 0.88) / 2)
        b9 = bins[9]  # [0.9, 1.0)
        assert b9.n == 1
        assert b9.wins == 1


# ---------------------------------------------------------------------------
# decompose (Murphy 1973 two-term Brier decomposition)
# ---------------------------------------------------------------------------

class TestDecompose:
    def test_empty_returns_none(self):
        assert decompose([], []) is None

    def test_perfectly_calibrated_bins_have_zero_reliability(self):
        # Two bins, each internally perfectly calibrated (mean predicted ==
        # observed rate in every bin) -> reliability term is exactly 0.
        rows = [_row(0.2, False), _row(0.2, False), _row(0.2, True), _row(0.2, False), _row(0.2, False)]
        # 1/5 predicted 0.2, observed 1/5 = 0.2 -> perfectly calibrated bin.
        bins = reliability_bins(rows, width=0.1)
        d = decompose(rows, bins)
        assert d is not None
        assert d.reliability == pytest.approx(0.0, abs=1e-9)

    def test_brier_skill_score_sign_matches_relative_to_uncertainty(self):
        # A well-calibrated, informative predictor: two bins, near-0 predicted
        # rarely wins, near-1 predicted usually wins -> positive skill.
        rows = (
            [_row(0.05, False) for _ in range(18)] + [_row(0.05, True) for _ in range(2)]
            + [_row(0.95, True) for _ in range(18)] + [_row(0.95, False) for _ in range(2)]
        )
        bins = reliability_bins(rows, width=0.1)
        d = decompose(rows, bins)
        assert d is not None
        assert d.brier_skill_score > 0

    def test_uncertain_zero_variance_base_rate_guards_division(self):
        # base_rate is 0 or 1 -> uncertainty is 0 -> brier_skill_score must not
        # raise ZeroDivisionError; NaN is the documented degenerate answer.
        rows = [_row(0.9, True), _row(0.9, True), _row(0.9, True)]
        bins = reliability_bins(rows, width=0.1)
        d = decompose(rows, bins)
        assert d is not None
        assert math.isnan(d.brier_skill_score)


# ---------------------------------------------------------------------------
# cut_summary
# ---------------------------------------------------------------------------

class TestCutSummary:
    def test_rows_without_q_live_counted_but_excluded_from_calibration_numbers(self):
        rows = [
            _row(0.8, True, direction="buy_yes"),
            _row(None, True, direction="buy_yes"),
            _row(0.6, False, direction="buy_no"),
        ]
        cuts = cut_summary(rows, lambda r: r.direction, order=["buy_yes", "buy_no"])
        yes = next(c for c in cuts if c.group == "buy_yes")
        assert yes.n_total == 2
        assert yes.n_with_q == 1
        assert yes.mean_pred == pytest.approx(0.8)
        no = next(c for c in cuts if c.group == "buy_no")
        assert no.n_total == 1
        assert no.n_with_q == 1

    def test_group_with_zero_resolvable_q_reports_na_not_crash(self):
        rows = [_row(None, True)]
        cuts = cut_summary(rows, lambda r: "only")
        assert cuts[0].n_total == 1
        assert cuts[0].n_with_q == 0
        assert cuts[0].mean_pred is None
        assert cuts[0].thin is True

    def test_thin_flag_uses_min_n_30_floor(self):
        rows = [_row(0.7, True) for _ in range(29)]
        cuts = cut_summary(rows, lambda r: "g")
        assert cuts[0].n_with_q == 29
        assert cuts[0].thin is True
        rows.append(_row(0.7, True))
        cuts = cut_summary(rows, lambda r: "g")
        assert cuts[0].n_with_q == 30
        assert cuts[0].thin is False


# ---------------------------------------------------------------------------
# lead_bucket_label
# ---------------------------------------------------------------------------

class TestLeadBucketLabel:
    def test_none_returns_none(self):
        assert lead_bucket_label(None) is None

    def test_boundaries(self):
        assert lead_bucket_label(0.0) == "<24h"
        assert lead_bucket_label(23.99) == "<24h"
        assert lead_bucket_label(24.0) == "24-72h (1-3d)"
        assert lead_bucket_label(72.0) == "72-168h (3-7d)"
        assert lead_bucket_label(168.0) == "168h+ (7d+)"
        assert lead_bucket_label(10_000.0) == "168h+ (7d+)"


# ---------------------------------------------------------------------------
# render_svg
# ---------------------------------------------------------------------------

def test_render_svg_is_well_formed_xml():
    rows = [_row(0.8, True), _row(0.82, False), _row(0.2, False)]
    bins = reliability_bins(rows, width=0.1)
    svg = render_svg(bins)
    minidom.parseString(svg)  # raises on malformed XML
    assert "<svg" in svg and "</svg>" in svg


def test_render_svg_handles_all_empty_bins():
    svg = render_svg(reliability_bins([], width=0.1))
    minidom.parseString(svg)


# ---------------------------------------------------------------------------
# End-to-end: real ATTACH pattern over tiny temp DBs
# ---------------------------------------------------------------------------

def _make_world_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE settlement_attribution (
            attribution_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            direction TEXT,
            category TEXT NOT NULL,
            won INTEGER NOT NULL,
            q_live REAL,
            settled_at TEXT
        )
        """
    )
    rows = [
        ("a1", "pos-1", "buy_no", "SKILL_WIN", 1, 0.85, "2026-07-01T00:00:00+00:00"),
        ("a2", "pos-2", "buy_yes", "SKILL_LOSS", 0, 0.40, "2026-07-02T00:00:00+00:00"),
        ("a3", "pos-3", "buy_no", "UNATTRIBUTABLE_Q_MISSING", 1, None, "2026-07-03T00:00:00+00:00"),
        # Unsettled row (settled_at NULL) — MUST be excluded by the settled-only
        # WHERE clause; if it leaks in, n_total would be 4 instead of 3.
        ("a4", "pos-4", "buy_no", "SKILL_WIN", 1, 0.9, None),
    ]
    conn.executemany(
        "INSERT INTO settlement_attribution "
        "(attribution_id, position_id, direction, category, won, q_live, settled_at) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _make_trades_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE position_current (position_id TEXT PRIMARY KEY, strategy_key TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current (position_id, strategy_key) VALUES (?,?)",
        [("pos-1", "forecast_qkernel_entry"), ("pos-2", "opening_inertia")],
    )
    conn.executemany(
        "INSERT INTO position_events (event_id, position_id, event_type, occurred_at) VALUES (?,?,?,?)",
        [
            ("e1", "pos-1", "POSITION_OPEN_INTENT", "2026-06-30T12:00:00+00:00"),
            ("e2", "pos-2", "ENTRY_ORDER_FILLED", "2026-07-01T18:00:00+00:00"),
        ],
    )
    conn.commit()
    conn.close()


def test_load_rows_excludes_unsettled_and_joins_strategy_and_lead(tmp_path):
    world = tmp_path / "zeus-world.db"
    trades = tmp_path / "zeus_trades.db"
    _make_world_db(world)
    _make_trades_db(trades)
    conn = ro_connect(str(world), str(trades))
    try:
        rows = load_rows(conn)
    finally:
        conn.close()

    # The unsettled row (pos-4) must be excluded — settled-only assertion.
    assert {r.position_id for r in rows} == {"pos-1", "pos-2", "pos-3"}

    pos1 = next(r for r in rows if r.position_id == "pos-1")
    assert pos1.strategy_key == "forecast_qkernel_entry"
    assert pos1.lead_hours == pytest.approx(12.0)  # 06-30T12:00 -> 07-01T00:00

    pos3 = next(r for r in rows if r.position_id == "pos-3")
    assert pos3.q_live is None
    assert pos3.strategy_key is None  # no position_current row -> LEFT JOIN NULL


def test_build_report_states_capital_scale_exactly_once(tmp_path):
    world = tmp_path / "zeus-world.db"
    trades = tmp_path / "zeus_trades.db"
    _make_world_db(world)
    _make_trades_db(trades)
    conn = ro_connect(str(world), str(trades))
    try:
        rows = load_rows(conn)
    finally:
        conn.close()

    report = build_report(rows, generated_at="2026-07-29T00:00:00+00:00")
    assert report.count("Return scope") == 1
    assert "n = 2/3" in report  # 2 of 3 settled rows carry a resolvable q_live
    # Capital scale is disclosed as operating scope only — never as a statistical
    # argument about return standard errors, and never as a dollar-figure PnL claim.
    assert "operating scope" in report
    assert "distinguishable from zero" not in report


# ---------------------------------------------------------------------------
# Read-this-first verdict block (landing-page contract)
# ---------------------------------------------------------------------------

class TestReadThisFirst:
    def _adverse_rows(self):
        # Predictions anti-correlated with outcomes -> Brier > uncertainty -> BSS < 0.
        return [_row(0.9, 0) for _ in range(6)] + [_row(0.1, 1) for _ in range(6)]

    def _positive_rows(self):
        return [_row(0.9, 1) for _ in range(8)] + [_row(0.1, 0) for _ in range(8)]

    def _zero_rows(self):
        # q equals the realized base rate exactly -> Brier == uncertainty -> BSS == 0.
        return [_row(0.5, 1) for _ in range(5)] + [_row(0.5, 0) for _ in range(5)]

    def test_adverse_verdict_branch(self):
        report = build_report(self._adverse_rows(), generated_at="G")
        assert "**Current verdict: adverse.**" in report
        assert "performed worse in this sample than that constant predictor" in report

    def test_positive_verdict_never_becomes_alpha(self):
        report = build_report(self._positive_rows(), generated_at="G")
        assert "not evidence of alpha" in report
        assert "**Current verdict: positive" in report

    def test_zero_skill_verdict(self):
        report = build_report(self._zero_rows(), generated_at="G")
        assert "no measured skill over the base-rate benchmark" in report

    def test_unavailable_verdict_when_no_scoreable_rows(self):
        rows = [_row(None, 1, category="UNATTRIBUTABLE_Q_MISSING") for _ in range(3)]
        report = build_report(rows, generated_at="G")
        assert "**Current verdict: unavailable.**" in report

    def test_verdict_precedes_provenance_and_diagram(self):
        report = build_report(self._adverse_rows(), generated_at="G")
        first = report.index("## Read this first")
        assert first < report.index("Generation and provenance")
        assert first < report.index("Reliability diagram")

    def test_provenance_is_collapsed_with_visible_measurement_unit(self):
        report = build_report(self._adverse_rows(), generated_at="G")
        assert "<details>" in report and "</details>" in report
        unit = report.index("Measurement unit: frozen decision probability")
        assert unit < report.index("<details>")

    def test_outcome_filtered_sampling_claim_absent(self):
        report = build_report(self._adverse_rows(), generated_at="G")
        assert "only skill outcomes feed" not in report
        assert "does not filter this reliability sample" in report

    def test_scoreable_counts_agree_between_verdict_and_body(self):
        rows = self._adverse_rows() + [_row(None, 0, category="UNATTRIBUTABLE_Q_MISSING")]
        report = build_report(rows, generated_at="G")
        assert "**12/13** settled positions are scoreable" in report
        assert "n = 12/13" in report

    def test_missing_probabilities_excluded_never_zeroed(self):
        rows = self._positive_rows() + [_row(None, 0, category="UNATTRIBUTABLE_Q_MISSING")]
        report = build_report(rows, generated_at="G")
        assert "missing probabilities are not imputed" in report
        assert "Across **16** settled positions" in report  # None-q row not scored

    def test_data_through_is_settlement_time_not_generation_time(self):
        report = build_report(self._adverse_rows(), generated_at="2026-08-20T00:00:00+00:00")
        assert "**Data through:** `2026-07-01T00:00:00+00:00`" in report
        assert "**Generated:** `2026-08-20T00:00:00+00:00`" in report

    def test_no_dollar_figure_anywhere(self):
        report = build_report(self._adverse_rows(), generated_at="G")
        assert "$" not in report
