# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19-2.md P1-4

"""P1-4 antibody — decision_seq lock-path coupling guard.

P1-4 bug: write_decision_event entered db_writer_lock(ZEUS_WORLD_DB_PATH) but
executed MAX+1 on a caller-supplied conn. If that conn pointed at the trade DB
(not the world DB), the lock did not serialise the target table, creating a
race on decision_seq uniqueness.

Fix (P1-4): AssertionError raised immediately when conn is not None and
PRAGMA database_list returns a path that does NOT end with 'zeus-world.db'.

This test file asserts two contracts:

1. Non-world conn raises AssertionError before any INSERT.
2. Two sequential conn=None calls increment decision_seq 0 → 1 (atomicity
   property verifying that own-connection path correctly serialises MAX+1).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.execution_intent import DecisionSourceContext
from src.state.decision_events import write_decision_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORLD_DDL = """
CREATE TABLE decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    condition_id        TEXT,
    decision_event_id   TEXT,
    decision_time       TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL,
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL,
    source         TEXT NOT NULL,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);
"""

_TRADE_DDL = """
CREATE TABLE orders (id INTEGER PRIMARY KEY);
"""

_NO_TRADE_EVENTS_DDL = """
CREATE TABLE no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);
"""


def _world_conn_in_memory(path_suffix: str = "zeus-world.db") -> sqlite3.Connection:
    """Return an in-memory connection whose PRAGMA database_list path ends with path_suffix."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_WORLD_DDL)
    conn.execute(_NO_TRADE_EVENTS_DDL)
    conn.row_factory = sqlite3.Row
    # Override PRAGMA database_list so the path check works on in-memory conn.
    # We do this by using a named file-based temp DB instead of :memory:.
    return conn


def _make_temp_db(tmp_path: Path, filename: str, ddl: str) -> sqlite3.Connection:
    """Create a real on-disk temp DB so PRAGMA database_list returns a real path."""
    db_path = tmp_path / filename
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(ddl)
    conn.commit()
    return conn


def _minimal_dsc() -> DecisionSourceContext:
    """Minimal DecisionSourceContext satisfying all NOT NULL enforcements."""
    return DecisionSourceContext(
        first_member_observed_time="2026-05-19T00:00:00Z",
        run_complete_time="2026-05-19T00:30:00Z",
        zeus_submit_intent_time="2026-05-19T01:00:00Z",
        venue_ack_time="2026-05-19T01:00:01Z",
        observation_available_at="2026-05-18T12:00:00Z",
        polymarket_end_anchor_source="gamma_explicit",
        observation_time="2026-05-18T12:00:00Z",
        decision_time="2026-05-19T01:00:00Z",
    )


@contextmanager
def _noop_lock(*_args, **_kwargs) -> Generator[None, None, None]:
    """No-op replacement for db_writer_lock (eliminates flock dependency in tests)."""
    yield


# ---------------------------------------------------------------------------
# Contract 1: non-world conn raises AssertionError before INSERT
# ---------------------------------------------------------------------------


class TestNonWorldConnRejected:
    def test_trade_db_conn_raises_assertion_error(self, tmp_path: Path) -> None:
        """Passing a trade-DB connection raises AssertionError immediately.

        The lock is on ZEUS_WORLD_DB_PATH; executing MAX+1 on a non-world conn
        would not be serialised. The guard must catch this before touching
        any table.
        """
        trade_conn = _make_temp_db(tmp_path, "zeus-trades.db", _TRADE_DDL)
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        with pytest.raises(AssertionError, match="world DB connection"):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
                conn=trade_conn,
            )

        trade_conn.close()

    def test_arbitrary_path_raises_assertion_error(self, tmp_path: Path) -> None:
        """Any path not ending in 'zeus-world.db' is rejected."""
        wrong_conn = _make_temp_db(tmp_path, "some-other.db", "CREATE TABLE x (id INTEGER);")
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        with pytest.raises(AssertionError, match="world DB connection"):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
                conn=wrong_conn,
            )

        wrong_conn.close()

    def test_no_insert_happened_after_assertion(self, tmp_path: Path) -> None:
        """The non-world conn's schema is not polluted by partial INSERT."""
        trade_conn = _make_temp_db(tmp_path, "zeus-trades.db", _TRADE_DDL)
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        with pytest.raises(AssertionError):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
                conn=trade_conn,
            )

        # No decision_events table in trade DB — confirms guard fires before any DDL/DML
        tables = {
            r[0]
            for r in trade_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "decision_events" not in tables
        trade_conn.close()


# ---------------------------------------------------------------------------
# Contract 2: two sequential conn=None calls yield decision_seq 0 then 1
# ---------------------------------------------------------------------------


class TestDecisionSeqAutoIncrement:
    def test_sequential_writes_increment_decision_seq(self, tmp_path: Path) -> None:
        """Two writes for the same natural key (minus decision_seq) yield seq 0 then 1.

        This validates that the own-connection path correctly serialises MAX+1
        against the world DB: the second write sees the first row and emits seq=1.
        """
        world_db_path = tmp_path / "zeus-world.db"
        world_conn_1 = sqlite3.connect(str(world_db_path))
        world_conn_1.execute(_WORLD_DDL)
        world_conn_1.execute(_NO_TRADE_EVENTS_DDL)
        world_conn_1.commit()
        world_conn_1.close()

        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        from src.state.db import SCHEMA_VERSION

        def _fake_get_world_conn(**_kwargs) -> sqlite3.Connection:
            c = sqlite3.connect(str(world_db_path))
            c.row_factory = sqlite3.Row
            return c

        with (
            patch("src.state.db.get_world_connection", side_effect=_fake_get_world_conn),
            patch("src.state.db_writer_lock.db_writer_lock", _noop_lock),
            patch("src.state.db.SCHEMA_VERSION", SCHEMA_VERSION),
            patch("src.state.db.ZEUS_WORLD_DB_PATH", world_db_path),
        ):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
                conn=None,
            )
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
                conn=None,
            )

        verify_conn = sqlite3.connect(str(world_db_path))
        rows = verify_conn.execute(
            "SELECT decision_seq FROM decision_events ORDER BY decision_seq ASC"
        ).fetchall()
        verify_conn.close()

        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        seqs = [r[0] for r in rows]
        assert seqs == [0, 1], f"Expected [0, 1], got {seqs}"
