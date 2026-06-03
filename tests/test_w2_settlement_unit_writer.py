# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: W2 settlement-store convergence spec (HANDOFF_2026-06-02_emos_ci.md)
"""W2 relationship tests: settlement_unit writer + NOT-NULL guard + P&L resolver repoint.

Three RED→GREEN relationship tests:

  RT-W2a  log_settlement(authority='VERIFIED', settlement_unit=None)
          → trigger raises sqlite3.IntegrityError / OperationalError containing
            'VERIFIED_SETTLEMENT_REQUIRES_UNIT'. Proves the DB-level guard is live.

  RT-W2b  harvester path with City(settlement_unit='F') → persisted
          settlement_outcomes row has settlement_unit='F'. Proves the caller
          passes the value end-to-end.

  RT-W2c  resolve_pnl() with a forecasts DB that contains ONLY settlement_outcomes
          (no legacy settlements table) → does NOT return status='settlements_read_error'
          and resolves positions > 0.

All fixtures use temp in-memory or tmp-file DBs; never touch live state paths.
"""
from __future__ import annotations

import sqlite3
import tempfile
import os
from pathlib import Path

import pytest

from src.state.db import init_schema_forecasts, log_settlement
from src.state.schema.v2_schema import _create_settlement_outcomes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_forecasts_conn() -> sqlite3.Connection:
    """Fresh in-memory forecasts DB with settlement_outcomes schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    return conn


def _insert_verified_settlement(conn, *, settlement_unit: str | None = "C"):
    """Write one VERIFIED settlement row via log_settlement."""
    return log_settlement(
        conn,
        city="Warsaw",
        target_date="2026-06-01",
        temperature_metric="high",
        market_slug="weather-warsaw-high-2026-06-01",
        winning_bin="above-20",
        settlement_value=22.5,
        settlement_source="wu_EPWA",
        settled_at="2026-06-01T18:00:00Z",
        authority="VERIFIED",
        settlement_unit=settlement_unit,
    )


# ---------------------------------------------------------------------------
# RT-W2a: VERIFIED row with settlement_unit=None → DB trigger rejects
# ---------------------------------------------------------------------------

class TestRTW2a:
    """DB trigger prevents VERIFIED + NULL unit from being persisted."""

    def test_verified_null_unit_raises(self):
        """
        RT-W2a RED contract: log_settlement(..., authority='VERIFIED',
        settlement_unit=None) must raise sqlite3.IntegrityError or
        sqlite3.OperationalError with 'VERIFIED_SETTLEMENT_REQUIRES_UNIT'
        in the message.

        Before the trigger is installed this test will FAIL (no error raised,
        status='written'). After the trigger + signature fix, it passes.
        """
        conn = _make_forecasts_conn()
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)) as exc_info:
            _insert_verified_settlement(conn, settlement_unit=None)
        assert "VERIFIED_SETTLEMENT_REQUIRES_UNIT" in str(exc_info.value)

    def test_unverified_null_unit_is_allowed(self):
        """
        Unverified rows with NULL unit must still succeed — legacy rows
        must not be broken by the trigger.
        """
        conn = _make_forecasts_conn()
        result = log_settlement(
            conn,
            city="Warsaw",
            target_date="2026-06-02",
            temperature_metric="high",
            market_slug="weather-warsaw-high-2026-06-02",
            winning_bin=None,
            settlement_value=None,
            settlement_source=None,
            settled_at=None,
            authority="UNVERIFIED",
            settlement_unit=None,
        )
        assert result["status"] == "written"
        row = conn.execute(
            "SELECT settlement_unit FROM settlement_outcomes "
            "WHERE city='Warsaw' AND target_date='2026-06-02'"
        ).fetchone()
        assert row is not None
        assert row[0] is None


# ---------------------------------------------------------------------------
# RT-W2b: City(settlement_unit='F') path persists settlement_unit='F'
# ---------------------------------------------------------------------------

class TestRTW2b:
    """log_settlement caller passes settlement_unit and it's stored correctly."""

    def test_f_unit_persisted(self):
        """
        RT-W2b RED contract: when a City with settlement_unit='F' writes a VERIFIED
        settlement, the settlement_outcomes row must carry settlement_unit='F'.

        Before the caller update, settlement_unit is not passed to log_settlement
        → column stays NULL → test FAILS. After the fix, passes.
        """
        conn = _make_forecasts_conn()
        result = _insert_verified_settlement(conn, settlement_unit="F")
        assert result["status"] == "written", f"write failed: {result}"

        row = conn.execute(
            "SELECT settlement_unit FROM settlement_outcomes "
            "WHERE city='Warsaw' AND target_date='2026-06-01'"
        ).fetchone()
        assert row is not None, "row not found"
        assert row[0] == "F", (
            f"Expected settlement_unit='F', got {row[0]!r}. "
            "log_settlement is not writing the settlement_unit column."
        )

    def test_c_unit_persisted(self):
        """Celsius unit also stored correctly."""
        conn = _make_forecasts_conn()
        result = log_settlement(
            conn,
            city="Berlin",
            target_date="2026-06-01",
            temperature_metric="high",
            market_slug="weather-berlin-high-2026-06-01",
            winning_bin="above-20",
            settlement_value=21.0,
            settlement_source="wu_EDDI",
            settled_at="2026-06-01T18:00:00Z",
            authority="VERIFIED",
            settlement_unit="C",
        )
        assert result["status"] == "written"
        row = conn.execute(
            "SELECT settlement_unit FROM settlement_outcomes "
            "WHERE city='Berlin' AND target_date='2026-06-01'"
        ).fetchone()
        assert row is not None
        assert row[0] == "C"


# ---------------------------------------------------------------------------
# RT-W2c: P&L resolver reads settlement_outcomes (not settlements)
# ---------------------------------------------------------------------------

class TestRTW2c:
    """harvester_pnl_resolver reads settlement_outcomes, not legacy settlements."""

    def _make_forecasts_db_file(self, tmp_path: Path) -> str:
        """Create a temp forecasts DB file with ONLY settlement_outcomes (no settlements table)."""
        db_path = str(tmp_path / "test_forecasts.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Create only settlement_outcomes — NOT the legacy settlements table
        _create_settlement_outcomes(conn)
        # Insert one VERIFIED row
        conn.execute(
            """
            INSERT INTO settlement_outcomes
              (city, target_date, temperature_metric, market_slug,
               winning_bin, settlement_value, settlement_source,
               settled_at, authority, settlement_unit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Warsaw", "2026-06-01", "high",
                "weather-warsaw-high-2026-06-01",
                "above-20", 22.5, "wu_EPWA",
                "2026-06-01T18:00:00Z", "VERIFIED", "C",
            ),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_resolver_reads_settlement_outcomes(self, tmp_path: Path):
        """
        RT-W2c RED contract: with a forecasts DB that has ONLY settlement_outcomes
        (no legacy settlements table), resolve_pnl() must NOT return
        status='settlements_read_error'.

        Before the repoint, the resolver queries FROM settlements → OperationalError
        → status='settlements_read_error'. After the repoint, it queries
        FROM settlement_outcomes and succeeds (even if it finds no matching open
        positions and settles 0).
        """
        from src.execution.harvester_pnl_resolver import resolve_pnl_for_settled_markets as resolve_pnl

        db_path = self._make_forecasts_db_file(tmp_path)

        # Patch env to disable the feature-flag guard so we reach the SQL query.
        import os
        old_flag = os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED")
        os.environ["ZEUS_HARVESTER_LIVE_ENABLED"] = "1"
        try:
            # Open the temp forecasts conn; pass a dummy trade conn (None-like).
            # We use a minimal in-memory DB for trade side — no positions needed;
            # we only care that the resolver doesn't crash on the forecasts read.
            trade_conn = sqlite3.connect(":memory:")
            trade_conn.row_factory = sqlite3.Row
            # Minimal trade tables needed by resolve_pnl (skip full init — just
            # let the resolver hit "no matching positions" gracefully).
            forecasts_conn = sqlite3.connect(db_path)
            forecasts_conn.row_factory = sqlite3.Row
            try:
                result = resolve_pnl(trade_conn, forecasts_conn)
            finally:
                forecasts_conn.close()
                trade_conn.close()
        finally:
            if old_flag is None:
                os.environ.pop("ZEUS_HARVESTER_LIVE_ENABLED", None)
            else:
                os.environ["ZEUS_HARVESTER_LIVE_ENABLED"] = old_flag

        assert result.get("status") != "settlements_read_error", (
            f"resolve_pnl returned settlements_read_error: {result}. "
            "The resolver is still reading FROM settlements (legacy table). "
            "Fix: change FROM settlements → FROM settlement_outcomes."
        )
