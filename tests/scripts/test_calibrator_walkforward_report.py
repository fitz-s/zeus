# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 report deliverable — extraction/coverage correctness against the
#   real settlement_attribution schema (via src.state.db.init_schema, matching
#   tests/scripts/test_scoreboard_panels.py's fixture pattern).
"""Tests for scripts/calibrator_walkforward_report.py.

Covers the DB-facing half item 9 doesn't put in src/calibration/
market_anchored_residual.py: lead_bucket derivation from real columns,
decision_posterior_computed_at as the decision-time source (never a
KeyError, never a silent guess when absent), and that the report renders
without crashing over a small fixture that exercises every coverage path.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.calibrator_walkforward_report import (
    build_walk_forward_rows,
    load_rows,
    render_report,
)
from src.calibration.market_anchored_residual import walk_forward
from src.state.db import init_schema


@pytest.fixture
def world_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    yield conn
    conn.close()


def _insert(conn: sqlite3.Connection, *, attribution_id: str, **overrides) -> None:
    base = dict(
        attribution_id=attribution_id,
        position_id=f"pos-{attribution_id}",
        city="Denver",
        target_date="2026-07-10",
        direction="buy_yes",
        q_in_bin=0.4,
        market_in_bin_prob=0.3,
        settled_in_bin=1,
        decision_posterior_computed_at="2026-07-09T12:00:00+00:00",
        settled_at="2026-07-10T23:00:00+00:00",
        graded_at="2026-07-11T00:00:00+00:00",
        category="UNATTRIBUTABLE_Q_MISSING",
        won=1,
        counts_as_skill_win=0,
        schema_version=1,
    )
    base.update(overrides)
    conn.execute(
        """
        INSERT INTO settlement_attribution (
            attribution_id, position_id, city, target_date, direction,
            q_in_bin, market_in_bin_prob, settled_in_bin,
            decision_posterior_computed_at, settled_at, graded_at,
            category, won, counts_as_skill_win, schema_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            base["attribution_id"], base["position_id"], base["city"], base["target_date"],
            base["direction"], base["q_in_bin"], base["market_in_bin_prob"],
            base["settled_in_bin"], base["decision_posterior_computed_at"],
            base["settled_at"], base["graded_at"], base["category"], base["won"],
            base["counts_as_skill_win"], base["schema_version"],
        ),
    )
    conn.commit()


class TestLoadRows:
    def test_excludes_rows_missing_required_fields(self, world_conn):
        _insert(world_conn, attribution_id="a1")
        _insert(world_conn, attribution_id="a2", q_in_bin=None)
        _insert(world_conn, attribution_id="a3", market_in_bin_prob=None)
        _insert(world_conn, attribution_id="a4", settled_in_bin=None)
        _insert(world_conn, attribution_id="a5", direction=None)
        rows = load_rows(world_conn)
        assert [r["attribution_id"] for r in rows] == ["a1"]


class TestBuildWalkForwardRows:
    def test_derives_lead_bucket_day1_from_decision_and_target_date(self, world_conn):
        _insert(
            world_conn,
            attribution_id="a1",
            decision_posterior_computed_at="2026-07-09T12:00:00+00:00",
            target_date="2026-07-10",
        )
        rows = load_rows(world_conn)
        wf_rows, context, unparsable = build_walk_forward_rows(rows)
        assert wf_rows[0].lead_bucket == "day1"
        assert context["a1"]["city"] == "Denver"
        assert unparsable == {"unparsable_target_date": 0, "lead_not_modeled": 0}

    def test_missing_decision_posterior_computed_at_is_none_not_a_crash(self, world_conn):
        _insert(world_conn, attribution_id="a1", decision_posterior_computed_at=None)
        rows = load_rows(world_conn)
        wf_rows, _, unparsable = build_walk_forward_rows(rows)
        assert wf_rows[0].decision_at is None
        assert wf_rows[0].lead_bucket is None
        # decision_at itself is absent -> walk_forward()'s own coverage
        # counts this, not the unparsable_target_date/lead_not_modeled here.
        assert unparsable == {"unparsable_target_date": 0, "lead_not_modeled": 0}

    def test_lead_outside_modeled_buckets_counted_lead_not_modeled(self, world_conn):
        _insert(
            world_conn,
            attribution_id="a1",
            decision_posterior_computed_at="2026-07-01T00:00:00+00:00",
            target_date="2026-07-10",  # lead=9 -> not modeled
        )
        rows = load_rows(world_conn)
        wf_rows, _, unparsable = build_walk_forward_rows(rows)
        assert wf_rows[0].lead_bucket is None
        assert unparsable["lead_not_modeled"] == 1


class TestReportRenders:
    def test_end_to_end_report_does_not_crash_and_documents_coverage(self, world_conn):
        # 25 well-formed rows across two decision dates so at least one
        # refit clears a small min_train_rows threshold.
        for i in range(25):
            day = "09" if i < 15 else "10"
            _insert(
                world_conn,
                attribution_id=f"row{i}",
                decision_posterior_computed_at=f"2026-07-{day}T12:00:00+00:00",
                target_date="2026-07-11",
                q_in_bin=0.3 + (i % 5) * 0.02,
                market_in_bin_prob=0.28,
                settled_in_bin=i % 2,
            )
        # One row with no decision_posterior_computed_at -> coverage-excluded.
        _insert(world_conn, attribution_id="no_decision", decision_posterior_computed_at=None)

        raw_rows = load_rows(world_conn)
        wf_rows, context, unparsable_lead = build_walk_forward_rows(raw_rows)
        y_by_row = {r["attribution_id"]: int(r["settled_in_bin"]) for r in raw_rows}
        result = walk_forward(wf_rows, min_train_rows=10)

        report = render_report(
            raw_rows=raw_rows,
            context=context,
            unparsable_lead=unparsable_lead,
            y_by_row=y_by_row,
            result=result,
        )
        assert "Market-anchored walk-forward residual calibrator" in report
        assert "challenger unavailable" in report
        assert "Paired log-loss by month" in report
        assert "Paired log-loss by |q-p| bucket" in report
        assert "Final frozen artifact" in report
        assert "missing_decision_at" in report
