# Lifecycle: created=2026-05-20; last_reviewed=2026-05-20; last_reused=never
# Purpose: Regression — cross-table decision_seq collision guard (BUG 1 fix).
# Reuse: Verify allocate_decision_seq UNION logic; re-run after any change to either writer.
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742); bot review BUG 1

"""Regression test: write_no_trade_event + write_decision_event for the same 4-tuple
must produce non-colliding decision_seq values.

Scenario:
  1. write_no_trade_event for 4-tuple A → expect seq=0
  2. write_decision_event for SAME 4-tuple A → expect seq=1
  3. INTERSECT on full 5-tuple PK across both tables → expect empty set (mutual exclusion)

Uses real on-disk temp DBs (PRAGMA database_list check requires a real path).
Monkeypatches ZEUS_WORLD_DB_PATH and db_writer_lock (no-op lock for test isolation).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.execution_intent import DecisionSourceContext
from src.contracts.no_trade_reason import NoTradeReason
from src.state.decision_events import write_decision_event
from src.state.no_trade_events import (
    NoTradeEventsSchemaCompatibilityError,
    assert_no_trade_events_schema_current_for_live,
    write_no_trade_event,
)
from src.state.db import SCHEMA_VERSION
from src.state.schema.no_trade_events_schema import ensure_table as ensure_no_trade_events_table


# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------

_DECISION_EVENTS_DDL = """
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

_OLD_NO_TRADE_EVENTS_DDL = """
CREATE TABLE no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL CHECK (reason IN ('uncategorized','observation_quality_rejected')),
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15)),
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);
"""

_REASON_VALUES_SQL_FOR_TEST = ", ".join(f"'{reason.value}'" for reason in NoTradeReason)

_V20_NO_TRADE_EVENTS_WITH_COMPATIBILITY_DDL = f"""
CREATE TABLE no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL CHECK (reason IN ({_REASON_VALUES_SQL_FOR_TEST})),
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (14, 15, 16, 17, 18, 19, 20)),
    schema_compatibility TEXT NOT NULL DEFAULT 'current'
        CHECK (schema_compatibility IN ('current', 'degraded')),
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);
"""


def _make_world_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a real on-disk world DB with both tables.

    Returns (db_path, connection). Caller is responsible for closing.
    """
    db_path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_DECISION_EVENTS_DDL)
    ensure_no_trade_events_table(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return db_path, conn


def _make_old_world_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a pre-SV16 world DB with stale no_trade_events CHECK constraints."""

    db_path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA user_version = 15")
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_OLD_NO_TRADE_EVENTS_DDL)
    conn.commit()
    return db_path, conn


def _make_v20_world_db_with_compatibility_column(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a v20 world DB that has schema_compatibility but stale version CHECK."""

    db_path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA user_version = 20")
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_V20_NO_TRADE_EVENTS_WITH_COMPATIBILITY_DDL)
    conn.commit()
    return db_path, conn


@contextmanager
def _noop_lock(*_args, **_kwargs) -> Generator[None, None, None]:
    """No-op replacement for db_writer_lock (eliminates flock dependency in tests)."""
    yield


def _minimal_dsc() -> DecisionSourceContext:
    """Minimal DecisionSourceContext satisfying all NOT NULL enforcements."""
    return DecisionSourceContext(
        first_member_observed_time="2026-05-20T00:00:00Z",
        run_complete_time="2026-05-20T00:30:00Z",
        zeus_submit_intent_time="2026-05-20T01:00:00Z",
        venue_ack_time="2026-05-20T01:00:01Z",
        observation_available_at="2026-05-19T12:00:00Z",
        polymarket_end_anchor_source="gamma_explicit",
        observation_time="2026-05-19T12:00:00Z",
        decision_time="2026-05-20T01:00:00Z",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_MARKET_SLUG = "chicago-high-2026-06-15"
_TEMP_METRIC = "high"
_TARGET_DATE = "2026-06-15"
_OBS_TIME = "2026-05-20T12:00:00Z"
_OBS_AT = "2026-05-20T13:00:00Z"


class TestCrossTableDecisionSeqNoCollision:
    """decision_seq must never collide across decision_events and no_trade_events
    for the same 4-tuple when using the shared allocate_decision_seq UNION allocator.
    """

    def test_no_trade_then_decision_seq_increments(self, tmp_path: Path) -> None:
        """write_no_trade_event → seq=0, write_decision_event → seq=1 for same 4-tuple."""
        db_path, conn = _make_world_db(tmp_path)

        nk_placeholder = make_decision_natural_key(
            market_slug=_MARKET_SLUG,
            temperature_metric=_TEMP_METRIC,
            target_date=_TARGET_DATE,
            observation_time=_OBS_TIME,
            decision_seq=0,  # ignored — overwritten by allocator
        )

        with patch("src.state.db.ZEUS_WORLD_DB_PATH", db_path), \
             patch("src.state.db_writer_lock.db_writer_lock", _noop_lock):

            # Step 1: write a no-trade rejection → expect seq=0
            returned_key = write_no_trade_event(
                nk_placeholder,
                NoTradeReason.OBSERVATION_QUALITY_REJECTED,
                "raw detail string",
                _OBS_AT,
                conn=conn,
            )
            assert returned_key[4] == 0, f"Expected no_trade seq=0, got {returned_key[4]}"

            # Step 2: write a decision_event for SAME 4-tuple → expect seq=1
            dsc = _minimal_dsc()
            write_decision_event(
                nk_placeholder,
                dsc,
                None,  # ekc not needed for seq allocation test
                direction="YES",
                strategy_key="center_buy",
                target_size_usd=50.0,
                limit_price=0.55,
                conn=conn,
            )

            # Verify decision_seq in decision_events is 1 (not 0)
            de_rows = conn.execute(
                "SELECT decision_seq FROM decision_events WHERE market_slug=?",
                (_MARKET_SLUG,),
            ).fetchall()
            assert len(de_rows) == 1
            assert de_rows[0]["decision_seq"] == 1, (
                f"Expected decision_events seq=1, got {de_rows[0]['decision_seq']}"
            )

        conn.close()

    def test_live_no_trade_write_fails_closed_on_pre_migration_check_constraints(
        self,
        tmp_path: Path,
    ) -> None:
        """Live writes must not downgrade semantic no-trade reasons."""

        db_path, conn = _make_old_world_db(tmp_path)

        nk_placeholder = make_decision_natural_key(
            market_slug=_MARKET_SLUG,
            temperature_metric=_TEMP_METRIC,
            target_date=_TARGET_DATE,
            observation_time=_OBS_TIME,
            decision_seq=0,
        )

        with patch("src.state.db.ZEUS_WORLD_DB_PATH", db_path), \
             patch("src.state.db_writer_lock.db_writer_lock", _noop_lock), \
             pytest.raises(NoTradeEventsSchemaCompatibilityError, match="not current"):
            write_no_trade_event(
                nk_placeholder,
                NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP,
                "family dedup",
                _OBS_AT,
                conn=conn,
            )

        assert conn.execute("SELECT COUNT(*) FROM no_trade_events").fetchone()[0] == 0
        conn.close()

    def test_no_trade_paper_fallback_preserves_degraded_row_on_pre_migration_check_constraints(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-live compatibility writes must mark degraded rows explicitly."""

        db_path, conn = _make_old_world_db(tmp_path)

        nk_placeholder = make_decision_natural_key(
            market_slug=_MARKET_SLUG,
            temperature_metric=_TEMP_METRIC,
            target_date=_TARGET_DATE,
            observation_time=_OBS_TIME,
            decision_seq=0,
        )

        with patch("src.state.db.ZEUS_WORLD_DB_PATH", db_path), \
             patch("src.state.db_writer_lock.db_writer_lock", _noop_lock):
            returned_key = write_no_trade_event(
                nk_placeholder,
                NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP,
                "family dedup",
                _OBS_AT,
                conn=conn,
                allow_schema_compatibility_downgrade=True,
            )

        row = conn.execute(
            """
            SELECT decision_seq, reason, reason_detail, schema_version, schema_compatibility
            FROM no_trade_events
            """
        ).fetchone()
        assert returned_key[4] == 0
        assert row["decision_seq"] == 0
        assert row["reason"] == NoTradeReason.UNCATEGORIZED.value
        assert "reason_raw=mutually_exclusive_family_dedup" in row["reason_detail"]
        assert row["schema_version"] == 15
        assert row["schema_compatibility"] == "degraded"
        conn.close()

    def test_no_trade_schema_rebuild_upgrades_stale_check_constraints(
        self,
        tmp_path: Path,
    ) -> None:
        """ensure_table must upgrade existing tables, not only create new ones."""

        _, conn = _make_old_world_db(tmp_path)
        conn.execute(
            """
            INSERT INTO no_trade_events (
                market_slug, temperature_metric, target_date, observation_time,
                decision_seq, reason, reason_detail, observed_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _MARKET_SLUG,
                _TEMP_METRIC,
                _TARGET_DATE,
                _OBS_TIME,
                0,
                NoTradeReason.OBSERVATION_QUALITY_REJECTED.value,
                "old row",
                _OBS_AT,
                15,
            ),
        )

        ensure_no_trade_events_table(conn)

        conn.execute(
            """
            INSERT INTO no_trade_events (
                market_slug, temperature_metric, target_date, observation_time,
                decision_seq, reason, reason_detail, observed_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _MARKET_SLUG,
                _TEMP_METRIC,
                _TARGET_DATE,
                _OBS_TIME,
                1,
                NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP.value,
                "new row",
                _OBS_AT,
                16,
            ),
        )
        rows = conn.execute(
            "SELECT decision_seq, reason, schema_version FROM no_trade_events ORDER BY decision_seq"
        ).fetchall()
        assert [(r["decision_seq"], r["reason"], r["schema_version"]) for r in rows] == [
            (0, NoTradeReason.OBSERVATION_QUALITY_REJECTED.value, 15),
            (1, NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP.value, 16),
        ]
        conn.close()

    def test_no_trade_schema_rebuild_upgrades_v20_compatibility_table_version_check(
        self,
        tmp_path: Path,
    ) -> None:
        """v20 tables with schema_compatibility still need the v21/v22 CHECK rebuild."""

        _, conn = _make_v20_world_db_with_compatibility_column(tmp_path)

        ensure_no_trade_events_table(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        assert_no_trade_events_schema_current_for_live(
            conn,
            expected_schema_version=SCHEMA_VERSION,
        )
        conn.execute(
            """
            INSERT INTO no_trade_events (
                market_slug, temperature_metric, target_date, observation_time,
                decision_seq, reason, reason_detail, observed_at,
                schema_version, schema_compatibility
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _MARKET_SLUG,
                _TEMP_METRIC,
                _TARGET_DATE,
                _OBS_TIME,
                0,
                NoTradeReason.OBSERVATION_QUALITY_REJECTED.value,
                "current row",
                _OBS_AT,
                SCHEMA_VERSION,
                "current",
            ),
        )
        row = conn.execute(
            "SELECT schema_version, schema_compatibility FROM no_trade_events"
        ).fetchone()
        assert row["schema_version"] == SCHEMA_VERSION
        assert row["schema_compatibility"] == "current"
        conn.close()

    def test_mutual_exclusion_no_5tuple_overlap(self, tmp_path: Path) -> None:
        """After writing one no-trade and one decision for same 4-tuple, INTERSECT on
        full 5-tuple PK returns empty set (mutual exclusion invariant).
        """
        db_path, conn = _make_world_db(tmp_path)

        nk_placeholder = make_decision_natural_key(
            market_slug=_MARKET_SLUG,
            temperature_metric=_TEMP_METRIC,
            target_date=_TARGET_DATE,
            observation_time=_OBS_TIME,
            decision_seq=0,
        )

        with patch("src.state.db.ZEUS_WORLD_DB_PATH", db_path), \
             patch("src.state.db_writer_lock.db_writer_lock", _noop_lock):

            write_no_trade_event(
                nk_placeholder,
                NoTradeReason.OBSERVATION_QUALITY_REJECTED,
                "detail",
                _OBS_AT,
                conn=conn,
            )
            dsc = _minimal_dsc()
            write_decision_event(
                nk_placeholder,
                dsc,
                None,
                direction="YES",
                strategy_key="center_buy",
                target_size_usd=50.0,
                limit_price=0.55,
                conn=conn,
            )

            overlap = conn.execute(
                """
                SELECT market_slug, temperature_metric, target_date,
                       observation_time, decision_seq
                FROM decision_events
                INTERSECT
                SELECT market_slug, temperature_metric, target_date,
                       observation_time, decision_seq
                FROM no_trade_events
                """
            ).fetchall()

        assert len(overlap) == 0, (
            f"Mutual exclusion violated: {len(overlap)} 5-tuple(s) appear in both tables: "
            + str([tuple(r) for r in overlap])
        )
        conn.close()

    def test_decision_then_no_trade_seq_increments(self, tmp_path: Path) -> None:
        """Reverse order: write_decision_event → seq=0, write_no_trade_event → seq=1."""
        db_path, conn = _make_world_db(tmp_path)

        nk_placeholder = make_decision_natural_key(
            market_slug=_MARKET_SLUG,
            temperature_metric=_TEMP_METRIC,
            target_date=_TARGET_DATE,
            observation_time=_OBS_TIME,
            decision_seq=0,
        )

        with patch("src.state.db.ZEUS_WORLD_DB_PATH", db_path), \
             patch("src.state.db_writer_lock.db_writer_lock", _noop_lock):

            dsc = _minimal_dsc()
            write_decision_event(
                nk_placeholder,
                dsc,
                None,
                direction="YES",
                strategy_key="center_buy",
                target_size_usd=50.0,
                limit_price=0.55,
                conn=conn,
            )

            returned_key = write_no_trade_event(
                nk_placeholder,
                NoTradeReason.OBSERVATION_QUALITY_REJECTED,
                "detail after decision",
                _OBS_AT,
                conn=conn,
            )
            assert returned_key[4] == 1, (
                f"Expected no_trade seq=1 (after decision seq=0), got {returned_key[4]}"
            )

        conn.close()
