# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase7_settlement_type_gate/PHASE_7_PLAN.md §T4 acceptance criteria
"""Tests for scripts/backfill_settlement_outcome_type.py."""
from __future__ import annotations

import sqlite3

import pytest

from scripts.backfill_settlement_outcome_type import run_backfill, _authority_to_outcome
from src.contracts.settlement_outcome import SettlementOutcome


# ---------------------------------------------------------------------------
# Helper — minimal in-memory settlements_v2
# ---------------------------------------------------------------------------

def _make_conn(rows: list[dict] | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE settlements_v2 (
            settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            winning_bin TEXT,
            outcome_type INTEGER
        )
    """)
    if rows:
        for row in rows:
            conn.execute(
                "INSERT INTO settlements_v2 (city, target_date, temperature_metric, authority, winning_bin, outcome_type) "
                "VALUES (?,?,?,?,?,?)",
                (
                    row["city"],
                    row["target_date"],
                    row["temperature_metric"],
                    row.get("authority", "UNVERIFIED"),
                    row.get("winning_bin"),
                    row.get("outcome_type"),
                ),
            )
        conn.commit()
    return conn


# ---------------------------------------------------------------------------
# T4: Authority → outcome mapping
# ---------------------------------------------------------------------------

class TestAuthorityToOutcome:
    def test_verified_with_winning_bin(self):
        assert _authority_to_outcome("VERIFIED", "80-90") == int(SettlementOutcome.VENUE_RESOLVED_WIN)

    def test_verified_no_winning_bin(self):
        """VERIFIED + None winning_bin → UNRESOLVED (cannot infer direction)."""
        assert _authority_to_outcome("VERIFIED", None) == int(SettlementOutcome.UNRESOLVED)

    def test_unverified(self):
        assert _authority_to_outcome("UNVERIFIED", None) == int(SettlementOutcome.UNRESOLVED)

    def test_quarantined(self):
        assert _authority_to_outcome("QUARANTINED", None) == int(SettlementOutcome.DISPUTED)

    def test_unknown_authority_fallback(self):
        assert _authority_to_outcome("UNKNOWN", None) == int(SettlementOutcome.UNRESOLVED)


# ---------------------------------------------------------------------------
# T4: Backfill logic
# ---------------------------------------------------------------------------

class TestRunBackfill:
    def _make_1k_rows(self) -> list[dict]:
        rows = []
        authorities = ["VERIFIED", "UNVERIFIED", "QUARANTINED"]
        for i in range(1000):
            auth = authorities[i % 3]
            rows.append({
                "city": f"City{i}",
                "target_date": "2026-07-01",
                "temperature_metric": "high",
                "authority": auth,
                "winning_bin": f"bin{i}" if auth == "VERIFIED" else None,
            })
        return rows

    def test_verified_winning_bin_maps_to_3(self):
        conn = _make_conn([{
            "city": "Chicago", "target_date": "2026-07-04", "temperature_metric": "high",
            "authority": "VERIFIED", "winning_bin": "80-90",
        }])
        run_backfill(conn, dry_run=False)
        row = conn.execute("SELECT outcome_type FROM settlements_v2").fetchone()
        assert row[0] == 3  # VENUE_RESOLVED_WIN

    def test_quarantined_maps_to_100(self):
        conn = _make_conn([{
            "city": "Dallas", "target_date": "2026-07-04", "temperature_metric": "high",
            "authority": "QUARANTINED",
        }])
        run_backfill(conn, dry_run=False)
        row = conn.execute("SELECT outcome_type FROM settlements_v2").fetchone()
        assert row[0] == 100  # DISPUTED

    def test_unverified_maps_to_0(self):
        conn = _make_conn([{
            "city": "Miami", "target_date": "2026-07-04", "temperature_metric": "high",
            "authority": "UNVERIFIED",
        }])
        run_backfill(conn, dry_run=False)
        row = conn.execute("SELECT outcome_type FROM settlements_v2").fetchone()
        assert row[0] == 0  # UNRESOLVED

    def test_dry_run_no_write(self):
        conn = _make_conn([{
            "city": "Chicago", "target_date": "2026-07-04", "temperature_metric": "high",
            "authority": "VERIFIED", "winning_bin": "80-90",
        }])
        stats = run_backfill(conn, dry_run=True)
        row = conn.execute("SELECT outcome_type FROM settlements_v2").fetchone()
        assert row[0] is None, "dry-run must not write"
        assert stats["total_updated"] == 1  # counted but not written

    def test_dry_run_deterministic_two_runs(self):
        """Two dry-run passes on identical data produce same total_updated."""
        rows = self._make_1k_rows()
        conn = _make_conn(rows)
        s1 = run_backfill(conn, dry_run=True)
        s2 = run_backfill(conn, dry_run=True)
        assert s1["total_updated"] == s2["total_updated"]

    def test_idempotent_rerun(self):
        rows = self._make_1k_rows()
        conn = _make_conn(rows)
        run_backfill(conn, dry_run=False)
        count_after_first = conn.execute(
            "SELECT COUNT(*) FROM settlements_v2 WHERE outcome_type IS NOT NULL"
        ).fetchone()[0]
        run_backfill(conn, dry_run=False)
        count_after_second = conn.execute(
            "SELECT COUNT(*) FROM settlements_v2 WHERE outcome_type IS NOT NULL"
        ).fetchone()[0]
        assert count_after_first == count_after_second == 1000

    def test_skips_already_set_rows(self):
        conn = _make_conn([
            {
                "city": "Chicago", "target_date": "2026-07-04", "temperature_metric": "high",
                "authority": "VERIFIED", "winning_bin": "80-90", "outcome_type": 3,
            },
            {
                "city": "Dallas", "target_date": "2026-07-04", "temperature_metric": "high",
                "authority": "UNVERIFIED",
            },
        ])
        stats = run_backfill(conn, dry_run=False)
        # Only the NULL row should be processed
        assert stats["total_processed"] == 1

    def test_no_raw_commit_at_top_level(self):
        """After run_backfill, we can still roll back — no top-level conn.commit() call."""
        conn = _make_conn([{
            "city": "Phoenix", "target_date": "2026-07-04", "temperature_metric": "high",
            "authority": "VERIFIED", "winning_bin": "80-90",
        }])
        conn.execute("BEGIN")
        run_backfill(conn, dry_run=False)
        conn.execute("ROLLBACK")
        row = conn.execute("SELECT outcome_type FROM settlements_v2").fetchone()
        assert row[0] is None, "rollback must undo the backfill when no top-level commit"
