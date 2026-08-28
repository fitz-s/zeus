# Created: 2026-08-28
# Last reused or audited: 2026-08-28
# Authority basis: external methodology-review requirement (winner's-curse /
#   selection-effect calibration test) — see scripts/enrich_no_trade_regret_outcomes.py
#   module docstring for the full rationale.
"""Tests for scripts/enrich_no_trade_regret_outcomes.py.

Covers:
  - the outcome-derivation truth matrix (buy_yes/buy_no x settled-in-bin/
    settled-out-of-bin), via the real grade_receipt() truth function;
  - would_have_filled derivation from hypothetical_fill_status;
  - ungradeable rows (no condition_id / no direction / no market_events
    match / no VERIFIED settlement) are left untouched;
  - idempotency: a second --apply run enriches 0 additional rows.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.enrich_no_trade_regret_outcomes import grade_candidate_row, run
from src.events.idempotency import stable_event_id


def _regret_event_id(event_id: str, stage: str, reason: str) -> str:
    return stable_event_id(event_id, stage, reason)


def _make_world_db(path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            regret_event_id TEXT NOT NULL PRIMARY KEY,
            event_id TEXT NOT NULL,
            rejection_stage TEXT NOT NULL,
            rejection_reason TEXT NOT NULL,
            regret_bucket TEXT NOT NULL,
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            direction TEXT,
            hypothetical_fill_status TEXT,
            later_outcome TEXT,
            would_have_won INTEGER,
            would_have_filled INTEGER,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        )
        """
    )
    for row in rows:
        rid = _regret_event_id(row["event_id"], row["rejection_stage"], row["rejection_reason"])
        conn.execute(
            """
            INSERT INTO no_trade_regret_events (
                regret_event_id, event_id, rejection_stage, rejection_reason,
                regret_bucket, condition_id, city, target_date, metric, direction,
                hypothetical_fill_status, created_at, schema_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)
            """,
            (
                rid,
                row["event_id"],
                row["rejection_stage"],
                row["rejection_reason"],
                row.get("regret_bucket", "HONEST_MARKET"),
                row.get("condition_id"),
                row.get("city"),
                row.get("target_date"),
                row.get("metric"),
                row.get("direction"),
                row.get("hypothetical_fill_status"),
                row.get("created_at", "2026-08-20T00:00:00+00:00"),
            ),
        )
    conn.commit()
    conn.close()


def _make_fcst_db(path, market_events: list[dict], settlements: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE market_events (
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            range_low REAL,
            range_high REAL,
            outcome TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            settlement_value REAL,
            settlement_unit TEXT,
            authority TEXT,
            settled_at TEXT
        )
        """
    )
    for me in market_events:
        conn.execute(
            "INSERT INTO market_events (condition_id, city, target_date, temperature_metric, "
            "range_low, range_high, outcome) VALUES (?,?,?,?,?,?,?)",
            (
                me["condition_id"], me["city"], me["target_date"], me["metric"],
                me.get("range_low"), me.get("range_high"), me.get("outcome"),
            ),
        )
    for so in settlements:
        conn.execute(
            "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, "
            "settlement_value, settlement_unit, authority, settled_at) VALUES (?,?,?,?,?,?,?)",
            (
                so["city"], so["target_date"], so["metric"], so["settlement_value"],
                so["settlement_unit"], so.get("authority", "VERIFIED"),
                so.get("settled_at", "2026-08-21T00:00:00+00:00"),
            ),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Outcome-derivation truth matrix (unit-level, via grade_candidate_row)
# ---------------------------------------------------------------------------

def _row(direction: str, range_low, range_high, settlement_value, unit="C",
         hypothetical_fill_status=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT ? AS direction, ? AS range_low, ? AS range_high, "
        "? AS settlement_value, ? AS settlement_unit, ? AS city, "
        "? AS target_date, ? AS metric, ? AS settled_at, "
        "? AS hypothetical_fill_status",
        (
            direction, range_low, range_high, settlement_value, unit,
            "TestCity", "2026-08-20", "high", "2026-08-21T00:00:00+00:00",
            hypothetical_fill_status,
        ),
    ).fetchone()


class TestGradeCandidateRowMatrix:
    """settlement=20C against an exact bin [20,20]: in-bin. buy_yes wins iff
    settled_in_bin; buy_no wins iff NOT settled_in_bin (Direction Law)."""

    def test_settled_in_bin_buy_yes_wins(self):
        graded = grade_candidate_row(_row("buy_yes", 20.0, 20.0, 20.0))
        assert graded is not None
        won, filled, later_outcome, proof = graded
        assert won is True
        assert filled is False
        assert later_outcome == "WOULD_HAVE_WON_BUT_UNFILLABLE"

    def test_settled_in_bin_buy_no_loses(self):
        graded = grade_candidate_row(_row("buy_no", 20.0, 20.0, 20.0))
        assert graded is not None
        won, _filled, _later_outcome, _proof = graded
        assert won is False

    def test_settled_out_of_bin_buy_yes_loses(self):
        graded = grade_candidate_row(_row("buy_yes", 20.0, 20.0, 19.0))
        assert graded is not None
        won, _filled, _later_outcome, _proof = graded
        assert won is False

    def test_settled_out_of_bin_buy_no_wins(self):
        graded = grade_candidate_row(_row("buy_no", 20.0, 20.0, 19.0))
        assert graded is not None
        won, _filled, _later_outcome, _proof = graded
        assert won is True

    def test_filled_when_executable_at_decision(self):
        graded = grade_candidate_row(
            _row("buy_yes", 20.0, 20.0, 20.0, hypothetical_fill_status="EXECUTABLE_AT_DECISION")
        )
        assert graded is not None
        won, filled, later_outcome, _proof = graded
        assert won is True
        assert filled is True
        assert later_outcome == "WOULD_HAVE_WON_AND_FILLABLE"

    def test_not_filled_lost_bucket(self):
        graded = grade_candidate_row(_row("buy_yes", 20.0, 20.0, 19.0))
        assert graded is not None
        _won, _filled, later_outcome, _proof = graded
        assert later_outcome == "WOULD_HAVE_LOST"

    def test_no_range_is_ungradeable(self):
        assert grade_candidate_row(_row("buy_yes", None, None, 20.0)) is None


# ---------------------------------------------------------------------------
# End-to-end run() over real (tmp) sqlite files: enrichment + idempotency
# ---------------------------------------------------------------------------

class TestRunEndToEnd:
    def test_apply_enriches_gradeable_rows_and_skips_ungradeable(self, tmp_path):
        world_db = tmp_path / "world.db"
        fcst_db = tmp_path / "forecasts.db"

        _make_world_db(
            world_db,
            [
                {
                    "event_id": "evt-1", "rejection_stage": "TRADE_SCORE",
                    "rejection_reason": "reason-1", "condition_id": "cid-1",
                    "city": "NYC", "target_date": "2026-08-20", "metric": "high",
                    "direction": "buy_yes",
                },
                {
                    "event_id": "evt-2", "rejection_stage": "TRADE_SCORE",
                    "rejection_reason": "reason-2", "condition_id": "cid-1",
                    "city": "NYC", "target_date": "2026-08-20", "metric": "high",
                    "direction": "buy_no",
                },
                # No condition_id -> structurally ungradeable, must stay NULL.
                {
                    "event_id": "evt-3", "rejection_stage": "EXECUTOR_EXPRESSIBILITY",
                    "rejection_reason": "reason-3", "condition_id": None,
                    "city": "NYC", "target_date": "2026-08-20", "metric": "high",
                    "direction": None,
                },
                # No VERIFIED settlement for this city/date/metric -> not joinable.
                {
                    "event_id": "evt-4", "rejection_stage": "TRADE_SCORE",
                    "rejection_reason": "reason-4", "condition_id": "cid-missing",
                    "city": "Nowhere", "target_date": "2026-08-20", "metric": "high",
                    "direction": "buy_yes",
                },
            ],
        )
        _make_fcst_db(
            fcst_db,
            market_events=[
                {"condition_id": "cid-1", "city": "NYC", "target_date": "2026-08-20",
                 "metric": "high", "range_low": 20.0, "range_high": 20.0, "outcome": "NO"},
            ],
            settlements=[
                {"city": "NYC", "target_date": "2026-08-20", "metric": "high",
                 "settlement_value": 20.0, "settlement_unit": "C"},
            ],
        )

        stats = run(
            world_db_path=world_db, fcst_db_path=fcst_db,
            since=None, limit=None, chunk_size=10, apply=True,
        )
        assert stats["enriched"] == 2
        assert stats["candidates_seen"] == 2  # evt-3/evt-4 never match the join WHERE clause

        conn = sqlite3.connect(str(world_db))
        conn.row_factory = sqlite3.Row
        rows = {r["event_id"]: r for r in conn.execute("SELECT * FROM no_trade_regret_events")}
        conn.close()

        assert rows["evt-1"]["would_have_won"] == 1  # buy_yes, settled in [20,20] -> won
        assert rows["evt-2"]["would_have_won"] == 0  # buy_no, settled in [20,20] -> lost
        assert rows["evt-3"]["would_have_won"] is None  # untouched (no condition_id/direction)
        assert rows["evt-4"]["would_have_won"] is None  # untouched (no VERIFIED settlement)
        assert rows["evt-1"]["later_outcome"] == "WOULD_HAVE_WON_BUT_UNFILLABLE"

    def test_second_apply_run_is_idempotent(self, tmp_path):
        world_db = tmp_path / "world.db"
        fcst_db = tmp_path / "forecasts.db"

        _make_world_db(
            world_db,
            [
                {
                    "event_id": "evt-1", "rejection_stage": "TRADE_SCORE",
                    "rejection_reason": "reason-1", "condition_id": "cid-1",
                    "city": "NYC", "target_date": "2026-08-20", "metric": "high",
                    "direction": "buy_yes",
                },
            ],
        )
        _make_fcst_db(
            fcst_db,
            market_events=[
                {"condition_id": "cid-1", "city": "NYC", "target_date": "2026-08-20",
                 "metric": "high", "range_low": 20.0, "range_high": 20.0, "outcome": "NO"},
            ],
            settlements=[
                {"city": "NYC", "target_date": "2026-08-20", "metric": "high",
                 "settlement_value": 20.0, "settlement_unit": "C"},
            ],
        )

        first = run(
            world_db_path=world_db, fcst_db_path=fcst_db,
            since=None, limit=None, chunk_size=10, apply=True,
        )
        assert first["enriched"] == 1

        second = run(
            world_db_path=world_db, fcst_db_path=fcst_db,
            since=None, limit=None, chunk_size=10, apply=True,
        )
        assert second["enriched"] == 0
        assert second["candidates_seen"] == 0

    def test_dry_run_makes_no_writes(self, tmp_path):
        world_db = tmp_path / "world.db"
        fcst_db = tmp_path / "forecasts.db"

        _make_world_db(
            world_db,
            [
                {
                    "event_id": "evt-1", "rejection_stage": "TRADE_SCORE",
                    "rejection_reason": "reason-1", "condition_id": "cid-1",
                    "city": "NYC", "target_date": "2026-08-20", "metric": "high",
                    "direction": "buy_yes",
                },
            ],
        )
        _make_fcst_db(
            fcst_db,
            market_events=[
                {"condition_id": "cid-1", "city": "NYC", "target_date": "2026-08-20",
                 "metric": "high", "range_low": 20.0, "range_high": 20.0, "outcome": "NO"},
            ],
            settlements=[
                {"city": "NYC", "target_date": "2026-08-20", "metric": "high",
                 "settlement_value": 20.0, "settlement_unit": "C"},
            ],
        )

        stats = run(
            world_db_path=world_db, fcst_db_path=fcst_db,
            since=None, limit=None, chunk_size=10, apply=False,
        )
        assert stats["enriched"] == 1  # counted as "would enrich"

        conn = sqlite3.connect(str(world_db))
        row = conn.execute(
            "SELECT would_have_won FROM no_trade_regret_events WHERE event_id='evt-1'"
        ).fetchone()
        conn.close()
        assert row[0] is None  # dry-run: no write happened
