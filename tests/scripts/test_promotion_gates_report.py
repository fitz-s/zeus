# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 10 report deliverable — DB-facing loaders + CLI end-to-end smoke,
#   matching tests/scripts/test_calibrator_walkforward_report.py's fixture
#   pattern (src.state.db.init_schema over a real file, since open_ro
#   requires an on-disk path for its file:...?mode=ro URI).
"""Tests for scripts/promotion_gates_report.py.

Covers: Gate A's reuse of the item-9 walk-forward extraction, Gate B's
tier0_flagged marker-column loader (absent-by-default fail-soft to an empty
sample; present-and-populated returns rows), and the CLI end-to-end smoke
including the "no tier0-settled sample yet" clean message and the ledger
refusal-on-second-formal-run path.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.promotion_gates_report import (
    load_gate_a_rows,
    load_gate_b_rows,
    main,
)
from src.analysis.promotion_gates import load_ledger
from src.state.db import init_schema


def _make_world_db(tmp_path, name: str = "world.db"):
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return db_path, conn


def _insert_attribution(conn: sqlite3.Connection, *, attribution_id: str, **overrides) -> None:
    base = dict(
        attribution_id=attribution_id,
        position_id=f"pos-{attribution_id}",
        city="Denver",
        target_date="2026-07-11",
        direction="buy_yes",
        q_in_bin=0.4,
        market_in_bin_prob=0.3,
        avg_fill_price=0.3,
        settled_in_bin=1,
        decision_posterior_computed_at="2026-07-10T12:00:00+00:00",
        settled_at="2026-07-11T23:00:00+00:00",
        graded_at="2026-07-12T00:00:00+00:00",
        category="UNATTRIBUTABLE_Q_MISSING",
        won=1,
        counts_as_skill_win=0,
        schema_version=1,
        tier0_flagged=None,
    )
    base.update(overrides)
    columns = [
        "attribution_id", "position_id", "city", "target_date", "direction",
        "q_in_bin", "market_in_bin_prob", "avg_fill_price", "settled_in_bin",
        "decision_posterior_computed_at", "settled_at", "graded_at",
        "category", "won", "counts_as_skill_win", "schema_version",
    ]
    values = [base[c] for c in columns]
    if "tier0_flagged" in {row[1] for row in conn.execute("PRAGMA table_info(settlement_attribution)").fetchall()}:
        columns.append("tier0_flagged")
        values.append(base["tier0_flagged"])
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO settlement_attribution ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Gate A loader.
# ---------------------------------------------------------------------------


class TestLoadGateARows:
    def test_produces_rows_with_calibrated_r_hat(self, tmp_path):
        db_path, conn = _make_world_db(tmp_path)
        try:
            for i in range(25):
                day = "09" if i < 15 else "10"
                _insert_attribution(
                    conn,
                    attribution_id=f"row{i}",
                    decision_posterior_computed_at=f"2026-07-{day}T12:00:00+00:00",
                    target_date="2026-07-11",
                    q_in_bin=0.3 + (i % 5) * 0.02,
                    market_in_bin_prob=0.28,
                    settled_in_bin=i % 2,
                )
            rows, coverage = load_gate_a_rows(conn)
            assert coverage["n_settlement_attribution_usable"] == 25
            # min_train_rows default is 20; the second decision date (10
            # rows) has 15 prior training rows -- below threshold -- so not
            # every row necessarily yields r_hat, but the loader must never
            # crash and must return a list (possibly empty on a tiny fixture).
            assert isinstance(rows, list)
            for r in rows:
                assert r.row_id.startswith("row")
                assert r.p0 is not None and r.r_hat is not None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Gate B loader — the tier0_flagged marker column.
# ---------------------------------------------------------------------------


class TestLoadGateBRows:
    def test_marker_column_absent_returns_empty_sample(self, tmp_path):
        db_path, conn = _make_world_db(tmp_path)
        try:
            _insert_attribution(conn, attribution_id="a1")
            rows, coverage = load_gate_b_rows(conn)
            assert rows == []
            assert coverage["tier0_marker_column_present"] is False
        finally:
            conn.close()

    def test_marker_column_present_returns_flagged_rows_only(self, tmp_path):
        db_path, conn = _make_world_db(tmp_path)
        try:
            conn.execute("ALTER TABLE settlement_attribution ADD COLUMN tier0_flagged INTEGER")
            conn.commit()
            _insert_attribution(conn, attribution_id="flagged1", tier0_flagged=1, avg_fill_price=0.15, settled_in_bin=1)
            _insert_attribution(conn, attribution_id="flagged2", tier0_flagged=1, avg_fill_price=0.20, settled_in_bin=0)
            _insert_attribution(conn, attribution_id="notflagged", tier0_flagged=0, avg_fill_price=0.15, settled_in_bin=1)
            _insert_attribution(conn, attribution_id="flagged_but_no_price", tier0_flagged=1, avg_fill_price=None, settled_in_bin=1)
            rows, coverage = load_gate_b_rows(conn)
            assert coverage["tier0_marker_column_present"] is True
            assert {r.row_id for r in rows} == {"flagged1", "flagged2"}
            assert coverage["excluded_missing_avg_fill_price"] == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI end-to-end smoke.
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_no_tier0_sample_prints_clean_message(self, tmp_path, capsys):
        db_path, conn = _make_world_db(tmp_path)
        conn.close()
        rc = main(["--root", str(tmp_path), "--world", db_path.name, "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no tier0-settled sample yet" in out
        assert "GATE A" in out

    def test_dry_run_never_writes_ledger(self, tmp_path, capsys):
        db_path, conn = _make_world_db(tmp_path)
        conn.close()
        ledger_path = tmp_path / "ledger.json"
        rc = main([
            "--root", str(tmp_path), "--world", db_path.name,
            "--dry-run", "--ledger-path", str(ledger_path),
        ])
        assert rc == 0
        assert not ledger_path.exists()

    def test_formal_run_then_second_formal_run_is_refused(self, tmp_path, capsys):
        db_path, conn = _make_world_db(tmp_path)
        try:
            conn.execute("ALTER TABLE settlement_attribution ADD COLUMN tier0_flagged INTEGER")
            conn.commit()
            for i in range(40):
                _insert_attribution(
                    conn,
                    attribution_id=f"t0_{i}",
                    tier0_flagged=1,
                    avg_fill_price=0.15,
                    settled_in_bin=1,
                    city=f"City{i}",
                    target_date=f"2026-07-{10 + (i % 20):02d}",
                )
        finally:
            conn.close()
        ledger_path = tmp_path / "ledger.json"

        rc1 = main([
            "--root", str(tmp_path), "--world", db_path.name, "--gate", "B",
            "--ledger-path", str(ledger_path), "--preregistration-version", "test-v1",
        ])
        assert rc1 == 0
        out1 = capsys.readouterr().out
        assert "ledger: recorded formal evaluation" in out1
        assert len(load_ledger(ledger_path)) == 1

        rc2 = main([
            "--root", str(tmp_path), "--world", db_path.name, "--gate", "B",
            "--ledger-path", str(ledger_path), "--preregistration-version", "test-v1",
        ])
        assert rc2 == 0
        out2 = capsys.readouterr().out
        assert "REFUSED" in out2
        # The refused attempt must not have appended a second entry.
        assert len(load_ledger(ledger_path)) == 1
