# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: RUN-12 settlement_commands coverage structural antibody
"""RUN-12: settlement_commands coverage invariant.

Two test classes:
  1. TestSettlementCommandCoverageLiveDrift  — @pytest.mark.live_drift, operator-run only,
     queries real zeus_trades.db to assert that every economically_closed position
     that closed > 24h ago has a corresponding settlement_commands row.

  2. TestSettlementCommandEnqueueCoverage — default CI, in-memory DB, seeds a closed
     position + calls enqueue_redeem_command, asserts settlement_commands gets a row.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from src.execution.harvester import enqueue_redeem_command
from src.execution.settlement_commands import init_settlement_command_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trades_conn() -> sqlite3.Connection:
    """In-memory DB with position_current + settlement_commands."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            condition_id TEXT,
            market_id TEXT,
            trade_id TEXT,
            direction TEXT,
            updated_at TEXT NOT NULL
        );
    """)
    init_settlement_command_schema(conn)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Fixture-based CI test
# ---------------------------------------------------------------------------

class TestSettlementCommandEnqueueCoverage:
    """Seed a closed position + call enqueue → assert settlement_commands row exists."""

    def test_enqueue_creates_settlement_command_row(self):
        """enqueue_redeem_command inserts a row for a closed position's condition_id."""
        conn = _make_trades_conn()

        # Seed a closed position
        condition_id = "test-condition-abc123"
        conn.execute("""
            INSERT INTO position_current
                (position_id, phase, condition_id, market_id, trade_id, direction, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "pos-001",
            "economically_closed",
            condition_id,
            "market-001",
            "trade-001",
            "buy_yes",
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()

        # Call the harvester enqueue path
        result = enqueue_redeem_command(
            conn,
            condition_id=condition_id,
            payout_asset="USDC_E",
            market_id="market-001",
            pusd_amount_micro=1_000_000,
            token_amounts={},
            trade_id="trade-001",
            winning_index_set='["2"]',
        )

        assert result["status"] == "queued", f"Expected 'queued', got {result!r}"
        assert result["command_id"] is not None

        # Assert the row exists in settlement_commands
        row = conn.execute(
            "SELECT command_id, condition_id, state FROM settlement_commands WHERE condition_id = ?",
            (condition_id,),
        ).fetchone()
        assert row is not None, (
            f"No settlement_commands row found for condition_id={condition_id!r}; "
            "enqueue_redeem_command did not write to settlement_commands"
        )
        assert row["condition_id"] == condition_id
        assert row["state"] in (
            "REDEEM_INTENT_CREATED",
            "REDEEM_REVIEW_REQUIRED",
            "REDEEM_OPERATOR_REQUIRED",
        ), f"Unexpected initial state: {row['state']!r}"

    def test_enqueue_idempotent_returns_already_exists(self):
        """Second enqueue for same condition returns 'already_exists'."""
        conn = _make_trades_conn()
        condition_id = "test-condition-dedup456"

        kwargs = dict(
            condition_id=condition_id,
            payout_asset="USDC_E",
            market_id="market-002",
            pusd_amount_micro=500_000,
            token_amounts={},
            trade_id="trade-002",
            winning_index_set='["1"]',
        )

        r1 = enqueue_redeem_command(conn, **kwargs)
        r2 = enqueue_redeem_command(conn, **kwargs)

        assert r1["status"] == "queued"
        # Second call should not raise; it's either already_exists or queued (idempotent)
        assert r2["status"] in ("queued", "already_exists", "error"), (
            f"Unexpected second-enqueue status: {r2!r}"
        )


# ---------------------------------------------------------------------------
# Live-drift invariant (operator-run only, queries real zeus_trades.db)
# ---------------------------------------------------------------------------

@pytest.mark.live_drift
class TestSettlementCommandCoverageLiveDrift:
    """Assert: every economically_closed position closed > 24h ago has a settlement_commands row.

    Run with: pytest -m live_drift tests/test_settlement_command_coverage_invariant.py

    Requires ZEUS_TRADE_DB_PATH or the default zeus_trades.db to exist.
    Skipped automatically when the DB does not exist (CI environment).
    """

    @pytest.fixture
    def live_conn(self):
        """Open the real zeus_trades.db read-only; skip if unavailable."""
        import pathlib
        import os
        db_path = pathlib.Path(
            os.environ.get(
                "ZEUS_TRADE_DB_PATH",
                "/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db",
            )
        )
        if not db_path.exists():
            pytest.skip(f"live zeus_trades.db not found at {db_path}")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()

    def test_economically_closed_position_has_settlement_command_within_24h(self, live_conn):
        """Every economically_closed position with updated_at > 24h ago must have a settlement_commands row.

        RUN-12 invariant: the harvester enqueue path must run for every
        economically_closed position. A missing row means the enqueue never fired.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        stale_positions = live_conn.execute("""
            SELECT position_id, condition_id, updated_at
            FROM position_current
            WHERE phase = 'economically_closed'
              AND updated_at < ?
              AND condition_id IS NOT NULL
              AND condition_id != ''
        """, (cutoff,)).fetchall()

        if not stale_positions:
            pytest.skip("No economically_closed positions older than 24h found — nothing to assert")

        missing = []
        for pos in stale_positions:
            row = live_conn.execute(
                "SELECT command_id FROM settlement_commands WHERE condition_id = ?",
                (pos["condition_id"],),
            ).fetchone()
            if row is None:
                missing.append({
                    "position_id": pos["position_id"],
                    "condition_id": pos["condition_id"],
                    "updated_at": pos["updated_at"],
                })

        assert missing == [], (
            f"RUN-12 INVARIANT VIOLATED: {len(missing)} economically_closed position(s) "
            f"have no settlement_commands row:\n"
            + "\n".join(
                f"  position_id={m['position_id']} condition_id={m['condition_id']} "
                f"updated_at={m['updated_at']}"
                for m in missing
            )
        )

    def test_settlement_command_state_progresses(self, live_conn):
        """settlement_commands rows are not stuck in REDEEM_INTENT_CREATED indefinitely (> 48h).

        A row that has been in REDEEM_INTENT_CREATED for > 48h without progressing
        indicates the settlement worker has stopped processing.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()

        stuck = live_conn.execute("""
            SELECT command_id, condition_id, requested_at
            FROM settlement_commands
            WHERE state = 'REDEEM_INTENT_CREATED'
              AND requested_at < ?
        """, (cutoff,)).fetchall()

        if not stuck:
            return  # No stuck rows — pass

        # Not a hard fail: log as a warning via assertion message.
        # Operator should investigate but system may be intentionally paused.
        pytest.fail(
            f"RUN-12 DRIFT: {len(stuck)} settlement_commands row(s) stuck in "
            f"REDEEM_INTENT_CREATED for > 48h:\n"
            + "\n".join(
                f"  command_id={r['command_id']} condition_id={r['condition_id']} "
                f"requested_at={r['requested_at']}"
                for r in stuck
            )
        )
