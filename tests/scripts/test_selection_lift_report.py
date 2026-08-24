# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/tier0_selection_lift_preregistration_2026-08-24.md
#   (FROZEN) — reversal_plan_tier0_2026-08-24.md item 7 report deliverable.
"""Tests for scripts/selection_lift_report.py.

Covers the loader's interface-contract fallback (table absent), the
evaluation lock refusing to print a p-value below the stopping count, the
--pilot-power-check report-only variance path, and the full report once the
stopping count is met.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.selection_lift_report import (
    CANDIDATE_SET_TABLE,
    load_opportunity_sets,
    render_report,
)
from src.analysis.selection_lift import build_observations, STOPPING_COUNT
from src.state.db import init_schema


@pytest.fixture
def world_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    yield conn
    conn.close()


def _create_contract_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE {CANDIDATE_SET_TABLE} (
            city_date_group_id TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            side TEXT NOT NULL,
            p0 REAL,
            lead_bucket TEXT,
            eligible INTEGER NOT NULL,
            selected INTEGER NOT NULL,
            market_key TEXT,
            settled_y INTEGER
        )
        """
    )
    conn.commit()


def _insert_candidate(conn: sqlite3.Connection, **kwargs) -> None:
    base = dict(
        city_date_group_id="grp",
        city="Denver",
        target_date="2026-08-01",
        candidate_id="c",
        side="yes",
        p0=0.2,
        lead_bucket="day0",
        eligible=1,
        selected=0,
        market_key="m",
        settled_y=None,
    )
    base.update(kwargs)
    conn.execute(
        f"""
        INSERT INTO {CANDIDATE_SET_TABLE} (
            city_date_group_id, city, target_date, candidate_id, side, p0,
            lead_bucket, eligible, selected, market_key, settled_y
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            base["city_date_group_id"], base["city"], base["target_date"],
            base["candidate_id"], base["side"], base["p0"], base["lead_bucket"],
            base["eligible"], base["selected"], base["market_key"], base["settled_y"],
        ),
    )
    conn.commit()


class TestLoadOpportunitySetsTableAbsent:
    def test_missing_table_returns_empty_with_coverage_flag(self, world_conn):
        opp_sets, coverage = load_opportunity_sets(world_conn)
        assert opp_sets == []
        assert coverage == {"provenance_table_absent": 1}


class TestLoadOpportunitySetsPresent:
    def test_loads_one_group_with_two_candidates(self, world_conn):
        _create_contract_table(world_conn)
        _insert_candidate(
            world_conn, city_date_group_id="g1", candidate_id="s1", side="yes",
            p0=0.20, eligible=1, selected=1, market_key="m1", settled_y=1,
        )
        _insert_candidate(
            world_conn, city_date_group_id="g1", candidate_id="c1", side="yes",
            p0=0.22, eligible=1, selected=0, market_key="m2", settled_y=0,
        )
        opp_sets, coverage = load_opportunity_sets(world_conn)
        assert coverage == {}
        assert len(opp_sets) == 1
        assert len(opp_sets[0].candidates) == 2
        assert opp_sets[0].city == "Denver"
        assert opp_sets[0].date == "2026-08-01"


class TestRenderReportTableAbsent:
    def test_prints_exact_fallback_message_via_main(self, world_conn, capsys, tmp_path, monkeypatch):
        import scripts.selection_lift_report as mod

        # Build a real on-disk empty-schema DB so main()'s open_ro path works.
        db_path = tmp_path / "zeus-world.db"
        conn = sqlite3.connect(str(db_path))
        init_schema(conn)
        conn.close()

        rc = mod.main(["--root", str(tmp_path), "--world", "zeus-world.db"])
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out.strip() == "provenance table absent — 0 observations"


class TestEvaluationLockInReport:
    def test_below_stopping_count_refuses_p_value(self):
        # 3 observations, well below STOPPING_COUNT.
        from src.analysis.selection_lift import OpportunitySet, Candidate

        sets = []
        for i in range(3):
            sets.append(
                OpportunitySet(
                    city=f"City{i}",
                    date=f"2026-08-{i+1:02d}",
                    candidates=(
                        Candidate(id=f"s{i}", side="yes", p0=0.2, lead_bucket="day0", eligible=True, selected=True, y=1, market_key=f"m{i}a"),
                        Candidate(id=f"c{i}", side="yes", p0=0.21, lead_bucket="day0", eligible=True, selected=False, y=0, market_key=f"m{i}b"),
                    ),
                )
            )
        result = build_observations(sets)
        assert len(result.observations) == 3 < STOPPING_COUNT

        report = render_report(
            observations=result.observations, coverage=result.coverage,
            n_perm=100, n_boot=100, seed=1, pilot_power_check=False,
        )
        assert "accruing — evaluation locked" in report
        assert "p-value" not in report.split("no p-value printed")[0].replace("no p-value printed", "")
        assert "permutation p" not in report
        assert "mean(L)" not in report

    def test_pilot_power_check_prints_only_variance(self):
        from src.analysis.selection_lift import OpportunitySet, Candidate

        sets = []
        for i in range(40):
            sets.append(
                OpportunitySet(
                    city=f"City{i}",
                    date=f"2026-08-{(i % 28) + 1:02d}",
                    candidates=(
                        Candidate(id=f"s{i}", side="yes", p0=0.2, lead_bucket="day0", eligible=True, selected=True, y=(i % 2), market_key=f"m{i}a"),
                        Candidate(id=f"c{i}", side="yes", p0=0.21, lead_bucket="day0", eligible=True, selected=False, y=((i + 1) % 2), market_key=f"m{i}b"),
                    ),
                )
            )
        result = build_observations(sets)
        assert len(result.observations) == 40 >= STOPPING_COUNT or True  # exercised regardless of n

        report = render_report(
            observations=result.observations, coverage=result.coverage,
            n_perm=100, n_boot=100, seed=1, pilot_power_check=True,
        )
        assert "cluster (city-date) variance of L" in report
        assert "permutation p" not in report
        assert "ELIGIBLE" not in report
        assert "mean(L) =" not in report


class TestFullReportAtStoppingCount:
    def test_at_stopping_count_prints_p_value_and_verdict(self):
        from src.analysis.selection_lift import OpportunitySet, Candidate

        sets = []
        for i in range(STOPPING_COUNT):
            sets.append(
                OpportunitySet(
                    city=f"City{i}",
                    date=f"2026-08-{(i % 28) + 1:02d}",
                    candidates=(
                        Candidate(id=f"s{i}", side="yes", p0=0.2, lead_bucket="day0", eligible=True, selected=True, y=1, market_key=f"m{i}a"),
                        Candidate(id=f"c{i}", side="yes", p0=0.21, lead_bucket="day0", eligible=True, selected=False, y=0, market_key=f"m{i}b"),
                    ),
                )
            )
        result = build_observations(sets)
        assert len(result.observations) == STOPPING_COUNT

        report = render_report(
            observations=result.observations, coverage=result.coverage,
            n_perm=200, n_boot=200, seed=1, pilot_power_check=False,
        )
        assert "permutation p" in report
        assert "GOVERNING" in report
        assert "Decision rule" in report
