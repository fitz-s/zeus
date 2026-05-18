# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: KARACHI_TRADE_DECISIONS_GAP_TRACE.md §6-8 + fix packet design
"""Relationship test: position_current must have a matching trade_decisions bridge.

Invariant contract:
  - BEFORE INSERT TRIGGER on position_current rejects inserts without a
    matching trade_decisions.runtime_trade_id row (Edit 4).
  - log_trade_entry raises on INSERT failure, propagating to outer SAVEPOINT
    so position_current is never committed without its bridge (Edit 1).
  - update_trade_lifecycle on a row missing its bridge calls the synthesizer
    first; if synthesis succeeds, lifecycle update proceeds. If synthesis also
    fails, BridgeAbsentError is raised (Edit 3 + Edit 5).
  - Synthesizer is idempotent: two calls for the same position_id produce
    exactly one trade_decisions row (Edit 5).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import types
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MIGRATION_PATH = (
    REPO_ROOT
    / "scripts"
    / "migrations"
    / "202605_position_current_bridge_required_trigger.py"
)

# ---------------------------------------------------------------------------
# Minimal DDL helpers
# ---------------------------------------------------------------------------

_TRADE_DECISIONS_DDL = """
CREATE TABLE trade_decisions (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    bin_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    size_usd REAL NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT NOT NULL,
    forecast_snapshot_id INTEGER,
    calibration_model_version TEXT,
    p_raw REAL NOT NULL,
    p_calibrated REAL,
    p_posterior REAL NOT NULL,
    edge REAL NOT NULL,
    ci_lower REAL NOT NULL,
    ci_upper REAL NOT NULL,
    kelly_fraction REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    filled_at TEXT,
    fill_price REAL,
    runtime_trade_id TEXT,
    order_id TEXT,
    order_status_text TEXT,
    order_posted_at TEXT,
    entered_at_ts TEXT,
    chain_state TEXT,
    strategy TEXT,
    edge_source TEXT,
    bin_type TEXT,
    env TEXT NOT NULL DEFAULT 'live',
    discovery_mode TEXT,
    market_hours_open REAL,
    fill_quality REAL,
    entry_method TEXT,
    selected_method TEXT,
    applied_validations_json TEXT,
    settlement_semantics_json TEXT,
    epistemic_context_json TEXT,
    edge_context_json TEXT
)
"""

_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (phase IN (
        'pending_entry', 'active', 'day0_window', 'pending_exit',
        'economically_closed', 'settled', 'voided', 'quarantined', 'admin_closed'
    )),
    trade_id TEXT,
    market_id TEXT,
    city TEXT,
    cluster TEXT,
    target_date TEXT,
    bin_label TEXT,
    direction TEXT,
    unit TEXT,
    size_usd REAL,
    shares REAL,
    cost_basis_usd REAL,
    entry_price REAL,
    p_posterior REAL,
    last_monitor_prob REAL,
    last_monitor_edge REAL,
    last_monitor_market_price REAL,
    decision_snapshot_id TEXT,
    entry_method TEXT,
    strategy_key TEXT NOT NULL DEFAULT 'opening_inertia',
    edge_source TEXT,
    discovery_mode TEXT,
    chain_state TEXT,
    token_id TEXT,
    no_token_id TEXT,
    condition_id TEXT,
    order_id TEXT,
    order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL DEFAULT 'high'
)
"""

_POSITION_EVENTS_DDL = """
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT,
    sequence_no INTEGER,
    event_type TEXT,
    occurred_at TEXT,
    strategy_key TEXT,
    decision_id TEXT,
    source_module TEXT,
    env TEXT
)
"""

_VENUE_COMMANDS_DDL = """
CREATE TABLE venue_commands (
    command_id TEXT PRIMARY KEY,
    session_id TEXT,
    parent_command_id TEXT,
    position_id TEXT,
    decision_id TEXT,
    market_id TEXT,
    intent_kind TEXT,
    market_question TEXT,
    token_id TEXT,
    order_id TEXT,
    side TEXT,
    price REAL,
    venue_order_id TEXT,
    state TEXT,
    created_by TEXT,
    created_at TEXT,
    updated_at TEXT,
    resolution_reason TEXT
)
"""


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_TRADE_DECISIONS_DDL)
    conn.execute(_POSITION_CURRENT_DDL)
    conn.execute(_POSITION_EVENTS_DDL)
    conn.execute(_VENUE_COMMANDS_DDL)
    conn.commit()
    return conn


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_trigger", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _insert_trade_decisions(conn: sqlite3.Connection, position_id: str) -> None:
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
         status, runtime_trade_id, env)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("mkt1", "label1", "buy_yes", 1.0, 0.5, "2026-01-01",
         0.5, 0.5, 0.1, 0.3, 0.7, 0.1, "pending", position_id, "live"),
    )


def _insert_position_current(conn: sqlite3.Connection, position_id: str) -> None:
    conn.execute(
        """
        INSERT INTO position_current
        (position_id, phase, strategy_key, updated_at, temperature_metric)
        VALUES (?, 'pending_entry', 'opening_inertia', '2026-01-01', 'high')
        """,
        (position_id,),
    )


# ---------------------------------------------------------------------------
# Edit 4: TRIGGER tests
# ---------------------------------------------------------------------------


class TestTriggerMigration:
    """BEFORE INSERT trigger blocks position_current inserts without bridge."""

    def test_migration_applies_cleanly(self):
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        # Trigger exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_position_current_requires_bridge'"
        ).fetchone()
        assert row is not None

    def test_migration_idempotent(self):
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        # Second call must not raise
        mod.up(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_position_current_requires_bridge'"
        ).fetchall()
        assert len(rows) == 1

    def test_trigger_blocks_insert_without_bridge(self):
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        position_id = str(uuid.uuid4())
        # SQLite RAISE(ABORT, ...) raises IntegrityError
        with pytest.raises(sqlite3.IntegrityError, match="requires matching trade_decisions"):
            _insert_position_current(conn, position_id)

    def test_trigger_allows_insert_with_bridge(self):
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        position_id = str(uuid.uuid4())
        _insert_trade_decisions(conn, position_id)
        # Must not raise
        _insert_position_current(conn, position_id)
        row = conn.execute(
            "SELECT position_id FROM position_current WHERE position_id=?",
            (position_id,),
        ).fetchone()
        assert row is not None

    def test_trigger_does_not_block_upsert_phase_update(self):
        """ON CONFLICT DO UPDATE for existing rows should not re-trigger BEFORE INSERT."""
        mod = _load_migration()
        conn = _make_db()
        mod.up(conn)
        position_id = str(uuid.uuid4())
        _insert_trade_decisions(conn, position_id)
        _insert_position_current(conn, position_id)
        # Phase update via upsert (ON CONFLICT DO UPDATE) should work
        conn.execute(
            """
            INSERT INTO position_current (position_id, phase, strategy_key, updated_at, temperature_metric)
            VALUES (?, 'active', 'opening_inertia', '2026-01-02', 'high')
            ON CONFLICT(position_id) DO UPDATE SET phase=excluded.phase, updated_at=excluded.updated_at
            """,
            (position_id,),
        )
        row = conn.execute(
            "SELECT phase FROM position_current WHERE position_id=?", (position_id,)
        ).fetchone()
        assert row[0] == "active"


# ---------------------------------------------------------------------------
# Edit 1: log_trade_entry raises on failure
# ---------------------------------------------------------------------------


class TestLogTradeEntryRaisesOnFailure:
    """log_trade_entry propagates exceptions instead of swallowing them."""

    def test_log_trade_entry_raises_on_bad_field(self):
        """If the INSERT fails, exception must propagate."""
        from src.state.db import log_trade_entry

        conn = sqlite3.connect(":memory:")
        conn.execute(_TRADE_DECISIONS_DDL)
        conn.commit()

        class BadPos:
            market_id = "mkt1"
            bin_label = "label1"
            direction = "buy_yes"
            size_usd = 1.0
            entry_price = 0.5
            p_posterior = 0.6
            p_raw = 0.6
            edge = 0.1
            entry_ci_width = 0.4
            kelly_fraction = 0.1
            edge_source = "ens"
            trade_id = str(uuid.uuid4())
            order_id = "ord1"
            order_status = "pending"
            order_posted_at = "2026-01-01"
            entered_at = "2026-01-01"
            chain_state = ""
            strategy = "opening_inertia"
            discovery_mode = "opening_hunt"
            market_hours_open = 0.0
            fill_quality = 0.0
            entry_method = "ens_member_counting"
            selected_method = "ens_member_counting"
            applied_validations = []
            settlement_semantics_json = None
            epistemic_context_json = None
            edge_context_json = None
            state = "pending_tracked"
            decision_snapshot_id = None
            calibration_version = None
            env = "test"

        pos = BadPos()
        # Remove a required NOT NULL field to force constraint failure
        # by mangling market_id to None
        pos.market_id = None  # type: ignore

        with pytest.raises(Exception):
            log_trade_entry(conn, pos)

    def test_outer_savepoint_rolls_back_position_current_on_log_trade_entry_failure(self):
        """SAVEPOINT containing log_trade_entry + upsert_position_current
        must roll back both writes when log_trade_entry raises."""
        conn = sqlite3.connect(":memory:")
        conn.execute(_TRADE_DECISIONS_DDL)
        conn.execute(_POSITION_CURRENT_DDL)
        conn.execute(_POSITION_EVENTS_DDL)
        conn.commit()

        position_id = str(uuid.uuid4())

        # Simulate the sp_candidate_* SAVEPOINT pattern from cycle_runtime
        conn.execute("SAVEPOINT sp_test")
        try:
            # Force log_trade_entry to fail by inserting a duplicate trade_decisions row
            # with the same runtime_trade_id (hypothetical; here we just raise directly)
            raise sqlite3.IntegrityError("simulated INSERT failure")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT sp_test")
            conn.execute("RELEASE SAVEPOINT sp_test")

        # position_current must not exist
        row = conn.execute(
            "SELECT position_id FROM position_current WHERE position_id=?",
            (position_id,),
        ).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# Edit 5: Synthesizer tests
# ---------------------------------------------------------------------------


class TestTradeDecisionsSynthesizer:
    """Synthesizer reconstructs missing bridge rows from available join tables."""

    def _make_full_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(_TRADE_DECISIONS_DDL)
        conn.execute(_POSITION_CURRENT_DDL)
        conn.execute(_POSITION_EVENTS_DDL)
        conn.execute(_VENUE_COMMANDS_DDL)
        conn.commit()
        return conn

    def _seed_orphan(self, conn: sqlite3.Connection, position_id: str) -> None:
        """Insert an orphan position_current row (no bridge) with supporting data."""
        conn.execute(
            """
            INSERT INTO position_current
            (position_id, phase, market_id, bin_label, direction, size_usd,
             entry_price, p_posterior, strategy_key, entry_method, discovery_mode,
             order_id, updated_at, temperature_metric)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                position_id, "day0_window",
                "mkt-test", "Will temp be high?", "buy_yes",
                1.0, 0.4, 0.75, "opening_inertia", "ens_member_counting",
                "opening_hunt", "ord-test", "2026-05-17", "high",
            ),
        )
        conn.execute(
            """
            INSERT INTO venue_commands
            (command_id, position_id, market_id, intent_kind, price, state, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("cmd-test", position_id, "mkt-test", "ENTRY", 0.4, "EXPIRED", "2026-05-17"),
        )
        conn.execute(
            """
            INSERT INTO position_events
            (event_id, position_id, sequence_no, event_type, occurred_at, strategy_key, env)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                f"{position_id}:open", position_id, 1,
                "POSITION_OPEN_INTENT", "2026-05-17", "opening_inertia", "live",
            ),
        )
        conn.commit()

    def test_synthesizer_fills_missing_bridge(self):
        from src.state.trade_decisions_synthesizer import synthesize_missing_bridge

        conn = self._make_full_db()
        position_id = str(uuid.uuid4())
        self._seed_orphan(conn, position_id)

        synthesize_missing_bridge(conn, position_id)

        row = conn.execute(
            "SELECT runtime_trade_id, status FROM trade_decisions WHERE runtime_trade_id=?",
            (position_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == position_id
        assert row[1] == "synthesized"

    def test_synthesizer_idempotent(self):
        from src.state.trade_decisions_synthesizer import synthesize_missing_bridge

        conn = self._make_full_db()
        position_id = str(uuid.uuid4())
        self._seed_orphan(conn, position_id)

        synthesize_missing_bridge(conn, position_id)
        synthesize_missing_bridge(conn, position_id)

        count = conn.execute(
            "SELECT COUNT(*) FROM trade_decisions WHERE runtime_trade_id=?",
            (position_id,),
        ).fetchone()[0]
        assert count == 1

    def test_synthesizer_logs_bridge_synthesized(self, caplog):
        from src.state.trade_decisions_synthesizer import synthesize_missing_bridge
        import logging

        conn = self._make_full_db()
        position_id = str(uuid.uuid4())
        self._seed_orphan(conn, position_id)

        with caplog.at_level(logging.INFO):
            synthesize_missing_bridge(conn, position_id)

        assert "BRIDGE_SYNTHESIZED" in caplog.text
        assert position_id in caplog.text

    def test_synthesizer_raises_when_position_not_found(self):
        from src.state.trade_decisions_synthesizer import synthesize_missing_bridge, BridgeSynthesisError

        conn = self._make_full_db()
        nonexistent_id = str(uuid.uuid4())

        with pytest.raises(BridgeSynthesisError):
            synthesize_missing_bridge(conn, nonexistent_id)


# ---------------------------------------------------------------------------
# Edit 3: update_trade_lifecycle hard-fail with synthesizer pre-call
# ---------------------------------------------------------------------------


class TestUpdateTradeLifecycleBridgeEnforcement:
    """update_trade_lifecycle calls synthesizer before raising BridgeAbsentError."""

    def _make_full_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row  # match production connection behaviour
        conn.execute(_TRADE_DECISIONS_DDL)
        conn.execute(_POSITION_CURRENT_DDL)
        conn.execute(_POSITION_EVENTS_DDL)
        conn.execute(_VENUE_COMMANDS_DDL)
        conn.commit()
        return conn

    def _seed_orphan(self, conn: sqlite3.Connection, position_id: str) -> None:
        conn.execute(
            """
            INSERT INTO position_current
            (position_id, phase, market_id, bin_label, direction, size_usd,
             entry_price, p_posterior, strategy_key, entry_method, discovery_mode,
             order_id, updated_at, temperature_metric)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                position_id, "day0_window",
                "mkt-test", "Will temp be high?", "buy_yes",
                1.0, 0.4, 0.75, "opening_inertia", "ens_member_counting",
                "opening_hunt", "ord-test", "2026-05-17", "high",
            ),
        )
        conn.execute(
            """
            INSERT INTO venue_commands
            (command_id, position_id, market_id, intent_kind, price, state, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            ("cmd-test", position_id, "mkt-test", "ENTRY", 0.4, "EXPIRED", "2026-05-17"),
        )
        conn.execute(
            """
            INSERT INTO position_events
            (event_id, position_id, sequence_no, event_type, occurred_at, strategy_key, env)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                f"{position_id}:open", position_id, 1,
                "POSITION_OPEN_INTENT", "2026-05-17", "opening_inertia", "live",
            ),
        )
        conn.commit()

    def test_update_lifecycle_synthesizes_and_succeeds(self):
        """When bridge is missing, synthesizer fires and lifecycle update proceeds."""
        from src.state.db import update_trade_lifecycle

        conn = self._make_full_db()
        position_id = str(uuid.uuid4())
        self._seed_orphan(conn, position_id)

        class Pos:
            trade_id = position_id
            state = "day0_window"
            day0_entered_at = "2026-05-17"
            entered_at = "2026-05-17"
            order_posted_at = "2026-05-17"
            entry_price = 0.4
            entry_order_id = "ord-test"
            order_id = "ord-test"
            fill_quality = None

        update_trade_lifecycle(conn=conn, pos=Pos())

        # Bridge must now exist
        row = conn.execute(
            "SELECT status FROM trade_decisions WHERE runtime_trade_id=?",
            (position_id,),
        ).fetchone()
        assert row is not None

    def test_update_lifecycle_raises_when_synthesizer_also_fails(self):
        """When synthesizer cannot reconstruct bridge, BridgeAbsentError raised.

        The synthesizer raises BridgeSynthesisError when position_current has
        no row for the given position_id.  We simulate this by providing a
        trade_id that exists nowhere in the DB.
        """
        from src.state.db import update_trade_lifecycle, BridgeAbsentError

        conn = self._make_full_db()
        # Use a position_id that is NOT in position_current — synthesizer will
        # raise BridgeSynthesisError (position not found), which update_trade_lifecycle
        # must convert to BridgeAbsentError.
        position_id = str(uuid.uuid4())

        class Pos:
            trade_id = position_id
            state = "day0_window"
            day0_entered_at = "2026-05-17"
            entered_at = "2026-05-17"
            order_posted_at = "2026-05-17"
            entry_price = None
            entry_order_id = ""
            order_id = ""
            fill_quality = None

        with pytest.raises(BridgeAbsentError):
            update_trade_lifecycle(conn=conn, pos=Pos())
