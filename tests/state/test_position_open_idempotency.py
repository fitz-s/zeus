# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_f109_fix/TRACE.md
"""Antibody tests for F109 — non-idempotent position-open.

Covers:
  1. Writer-side check raises DuplicatePositionOpenError on token-level duplicate.
  2. Writer-side check is NO-OP for normal first-INSERT and for same-position_id UPSERT.
  3. Migration installs partial UNIQUE INDEX; INDEX catches a race that slips past the writer.
  4. Migration refuses to apply when duplicates still exist (deploy-order guard).
  5. Consolidator OVERBOOK voids the oldest row(s) to match chain truth.
  6. Consolidator DIVERGENT (db <= chain) SKIPs and logs.
  7. Consolidator is idempotent (second pass = no-op).
  8. London 5/19 fixture: 2 active rows -> 1 active row matching on-chain 6 shares.
  9. Karachi safety: single-row token is a NO-OP through all paths.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MIGRATION_PATH = (
    REPO_ROOT
    / "scripts"
    / "migrations"
    / "202605_position_current_idempotent_open_per_token.py"
)

# Minimal DDL — only the columns the F109 path needs. Mirrors the live schema's
# (position_id, phase, shares, cost_basis_usd, strategy_key, token_id,
# updated_at, temperature_metric) NOT NULL constraints.
_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    trade_id TEXT, market_id TEXT, city TEXT, cluster TEXT,
    target_date TEXT, bin_label TEXT, direction TEXT, unit TEXT,
    size_usd REAL, shares REAL, cost_basis_usd REAL, entry_price REAL,
    p_posterior REAL, last_monitor_prob REAL, last_monitor_edge REAL,
    last_monitor_market_price REAL, decision_snapshot_id TEXT,
    entry_method TEXT, strategy_key TEXT NOT NULL, edge_source TEXT,
    discovery_mode TEXT, chain_state TEXT, token_id TEXT, no_token_id TEXT,
    condition_id TEXT, order_id TEXT, order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL
)
"""

_POSITION_EVENTS_DDL = """
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    phase_before TEXT, phase_after TEXT,
    strategy_key TEXT NOT NULL,
    decision_id TEXT, snapshot_id TEXT, order_id TEXT,
    command_id TEXT, caused_by TEXT, idempotency_key TEXT UNIQUE,
    venue_status TEXT, source_module TEXT NOT NULL,
    payload_json TEXT NOT NULL, env TEXT NOT NULL DEFAULT 'live',
    UNIQUE(position_id, sequence_no)
)
"""

_COLLATERAL_SNAPSHOTS_DDL = """
CREATE TABLE collateral_ledger_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    authority_tier TEXT NOT NULL,
    ctf_token_balances_json TEXT NOT NULL
)
"""

_LONDON_TOKEN = (
    "113959433546428599583458171463964346033318046435676830124564125503733330054946"
)
_KARACHI_TOKEN = (
    "53911939967084927688315552226298819187226280922490512356766442155641045757884"
)


def _make_projection(*, position_id: str, phase: str, token_id: str, shares: float = 6.0) -> dict:
    """Build a CANONICAL_POSITION_CURRENT_COLUMNS-shaped projection dict."""
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    base = {col: None for col in CANONICAL_POSITION_CURRENT_COLUMNS}
    base.update(
        position_id=position_id,
        phase=phase,
        trade_id=position_id,
        market_id="m1",
        city="London",
        cluster="UK",
        target_date="2026-05-19",
        bin_label="18C",
        direction="buy_yes",
        unit="C",
        size_usd=1.86,
        shares=shares,
        cost_basis_usd=shares * 0.31,
        entry_price=0.31,
        p_posterior=0.4,
        last_monitor_prob=0.4,
        last_monitor_edge=0.05,
        last_monitor_market_price=0.31,
        decision_snapshot_id="snap-1",
        entry_method="ens_member_counting",
        strategy_key="opening_inertia",
        edge_source="model",
        discovery_mode="opening_hunt",
        chain_state="synced",
        token_id=token_id,
        no_token_id="no-1",
        condition_id="cond-1",
        order_id="0x" + position_id,
        order_status="filled",
        updated_at="2026-05-17T22:00:00+00:00",
        temperature_metric="forecast_high_c",
    )
    return base


def _insert_event(conn: sqlite3.Connection, *, position_id: str, seq: int, occurred_at: str) -> None:
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, strategy_key, source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'POSITION_OPEN_INTENT', ?, 'opening_inertia',
                  'test', '{}', 'live')
        """,
        (f"ev-{position_id}-{seq}", position_id, seq, occurred_at),
    )


def _seed_london_duplicate(conn: sqlite3.Connection) -> None:
    """Reproduce the live F109 fixture: 2 active pending_exit rows on the
    London token, with on-chain showing 6 shares total."""
    from src.state.projection import (
        CANONICAL_POSITION_CURRENT_COLUMNS,
        ordered_values,
    )

    p1 = _make_projection(
        position_id="0a0e3b72-46e", phase="pending_exit", token_id=_LONDON_TOKEN
    )
    p2 = _make_projection(
        position_id="7557a029-4ad", phase="pending_exit", token_id=_LONDON_TOKEN
    )
    for proj in (p1, p2):
        conn.execute(
            f"INSERT INTO position_current ({', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)}) "
            f"VALUES ({', '.join(['?'] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})",
            ordered_values(proj, CANONICAL_POSITION_CURRENT_COLUMNS),
        )
    _insert_event(conn, position_id="0a0e3b72-46e", seq=1, occurred_at="2026-05-17T21:53:07+00:00")
    _insert_event(conn, position_id="7557a029-4ad", seq=1, occurred_at="2026-05-17T22:24:07+00:00")
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots (captured_at, authority_tier, ctf_token_balances_json) "
        "VALUES (?, 'CHAIN', ?)",
        ("2026-05-18T00:01:00+00:00", json.dumps({_LONDON_TOKEN: 6_000_000})),
    )


def _seed_karachi_singleton(conn: sqlite3.Connection) -> None:
    from src.state.projection import (
        CANONICAL_POSITION_CURRENT_COLUMNS,
        ordered_values,
    )

    p = _make_projection(
        position_id="c30f28a5-d4e",
        phase="day0_window",
        token_id=_KARACHI_TOKEN,
        shares=1.5873,
    )
    conn.execute(
        f"INSERT INTO position_current ({', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)}) "
        f"VALUES ({', '.join(['?'] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})",
        ordered_values(p, CANONICAL_POSITION_CURRENT_COLUMNS),
    )
    _insert_event(conn, position_id="c30f28a5-d4e", seq=1, occurred_at="2026-05-16T00:30:00+00:00")


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        _POSITION_CURRENT_DDL + ";" + _POSITION_EVENTS_DDL + ";" + _COLLATERAL_SNAPSHOTS_DDL
    )
    return conn


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_f109", MIGRATION_PATH)
    mod = types.ModuleType("mig_f109")
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# -------------------------- WRITER-SIDE TESTS -------------------------- #


class TestWriterIdempotencyCheck:

    def test_first_insert_succeeds(self):
        from src.state.projection import upsert_position_current

        conn = _fresh_conn()
        p = _make_projection(
            position_id="pos-a", phase="pending_exit", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p)
        assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 1

    def test_same_position_id_upsert_is_noop(self):
        """Re-upserting the same position_id (lifecycle update) must NOT raise."""
        from src.state.projection import upsert_position_current

        conn = _fresh_conn()
        p = _make_projection(
            position_id="pos-a", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p)
        p["phase"] = "active"
        upsert_position_current(conn, p)  # must not raise
        p["phase"] = "pending_exit"
        upsert_position_current(conn, p)
        assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 1

    def test_duplicate_open_raises(self):
        from src.state.projection import (
            DuplicatePositionOpenError,
            upsert_position_current,
        )

        conn = _fresh_conn()
        p1 = _make_projection(
            position_id="pos-a", phase="pending_exit", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p1)

        p2 = _make_projection(
            position_id="pos-b", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        with pytest.raises(DuplicatePositionOpenError) as excinfo:
            upsert_position_current(conn, p2)
        assert excinfo.value.existing_position_id == "pos-a"
        assert excinfo.value.attempted_position_id == "pos-b"
        assert excinfo.value.token_id == _LONDON_TOKEN
        # SAVEPOINT semantics: no row inserted for pos-b
        row_count = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = 'pos-b'"
        ).fetchone()[0]
        assert row_count == 0

    def test_voided_row_does_not_block_new_open(self):
        """Voided rows do NOT count as 'live'; re-entry after void must succeed."""
        from src.state.projection import upsert_position_current

        conn = _fresh_conn()
        p1 = _make_projection(
            position_id="pos-a", phase="voided", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p1)
        p2 = _make_projection(
            position_id="pos-b", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p2)  # voided sibling does not block
        assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 2

    def test_economically_closed_does_not_block_new_open(self):
        """economically_closed is not in OPEN_EXPOSURE_PHASES; re-entry must succeed."""
        from src.state.projection import upsert_position_current

        conn = _fresh_conn()
        p1 = _make_projection(
            position_id="pos-a", phase="economically_closed", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p1)
        p2 = _make_projection(
            position_id="pos-b", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p2)
        assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 2


# -------------------------- MIGRATION / INDEX TESTS -------------------------- #


class TestMigrationAndIndex:

    def test_migration_applies_on_clean_db(self):
        mod = _load_migration()
        conn = _fresh_conn()
        mod.up(conn)
        # Idempotent
        mod.up(conn)
        # Index exists
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='ux_position_current_open_per_token'"
        ).fetchone()
        assert row is not None

    def test_migration_refuses_when_duplicates_exist(self):
        mod = _load_migration()
        conn = _fresh_conn()
        _seed_london_duplicate(conn)
        with pytest.raises(RuntimeError) as excinfo:
            mod.up(conn)
        assert "F109 migration aborted" in str(excinfo.value)

    def test_unique_index_catches_race_past_writer(self):
        """If a race somehow inserts a duplicate via raw SQL (bypassing the
        writer check), the partial UNIQUE INDEX raises sqlite3.IntegrityError.
        """
        from src.state.projection import (
            CANONICAL_POSITION_CURRENT_COLUMNS,
            ordered_values,
        )

        mod = _load_migration()
        conn = _fresh_conn()
        mod.up(conn)
        p1 = _make_projection(
            position_id="pos-a", phase="pending_exit", token_id=_LONDON_TOKEN
        )
        conn.execute(
            f"INSERT INTO position_current ({', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)}) "
            f"VALUES ({', '.join(['?'] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})",
            ordered_values(p1, CANONICAL_POSITION_CURRENT_COLUMNS),
        )
        p2 = _make_projection(
            position_id="pos-b", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO position_current "
                f"({', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)}) "
                f"VALUES ({', '.join(['?'] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})",
                ordered_values(p2, CANONICAL_POSITION_CURRENT_COLUMNS),
            )

    def test_unique_index_allows_void_then_reopen(self):
        """Voiding an existing row must free the slot for a new open on the same token."""
        from src.state.projection import (
            CANONICAL_POSITION_CURRENT_COLUMNS,
            ordered_values,
            upsert_position_current,
        )

        mod = _load_migration()
        conn = _fresh_conn()
        mod.up(conn)
        p1 = _make_projection(
            position_id="pos-a", phase="pending_exit", token_id=_LONDON_TOKEN
        )
        conn.execute(
            f"INSERT INTO position_current ({', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)}) "
            f"VALUES ({', '.join(['?'] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})",
            ordered_values(p1, CANONICAL_POSITION_CURRENT_COLUMNS),
        )
        conn.execute(
            "UPDATE position_current SET phase='voided' WHERE position_id='pos-a'"
        )
        p2 = _make_projection(
            position_id="pos-b", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        upsert_position_current(conn, p2)


# -------------------------- CONSOLIDATOR TESTS -------------------------- #


class TestConsolidator:

    def test_noop_when_no_duplicates(self):
        from src.state.position_duplicate_consolidator import consolidate

        conn = _fresh_conn()
        report = consolidate(conn)
        assert report["scanned_tokens"] == 0
        assert report["voided_positions"] == []

    def test_overbook_voids_oldest_row(self):
        """London fixture: 2 rows × 6 shares = 12 DB; chain = 6 → void oldest."""
        from src.state.position_duplicate_consolidator import consolidate

        conn = _fresh_conn()
        _seed_london_duplicate(conn)

        report = consolidate(conn)
        assert _LONDON_TOKEN in report["overbook_tokens"]
        assert report["voided_positions"] == ["0a0e3b72-46e"]
        # The newer position (7557a029-4ad) keeps the active row
        rows = conn.execute(
            "SELECT position_id, phase FROM position_current "
            f"WHERE token_id = '{_LONDON_TOKEN}' ORDER BY position_id"
        ).fetchall()
        rows_dict = {r[0]: r[1] for r in rows}
        assert rows_dict["0a0e3b72-46e"] == "voided"
        assert rows_dict["7557a029-4ad"] == "pending_exit"
        # Surviving row's shares match on-chain truth (6.0)
        surviving_shares = conn.execute(
            "SELECT shares FROM position_current WHERE position_id = '7557a029-4ad'"
        ).fetchone()[0]
        assert surviving_shares == 6.0
        # ADMIN_VOIDED audit event recorded
        event = conn.execute(
            "SELECT event_type, phase_after FROM position_events "
            "WHERE position_id='0a0e3b72-46e' ORDER BY sequence_no DESC LIMIT 1"
        ).fetchone()
        assert event[0] == "ADMIN_VOIDED"
        assert event[1] == "voided"

    def test_consolidator_idempotent(self):
        """Running consolidator twice must be a no-op on the second pass."""
        from src.state.position_duplicate_consolidator import consolidate

        conn = _fresh_conn()
        _seed_london_duplicate(conn)
        first = consolidate(conn)
        second = consolidate(conn)
        assert len(first["voided_positions"]) == 1
        assert second["voided_positions"] == []
        assert second["scanned_tokens"] == 0

    def test_divergent_when_no_chain_snapshot_skips(self):
        """Without a CHAIN snapshot the consolidator MUST NOT void anything
        (conservative default)."""
        from src.state.position_duplicate_consolidator import consolidate
        from src.state.projection import (
            CANONICAL_POSITION_CURRENT_COLUMNS,
            ordered_values,
        )

        conn = _fresh_conn()
        # Seed 2 rows WITHOUT a CHAIN snapshot
        p1 = _make_projection(
            position_id="pos-a", phase="pending_exit", token_id=_LONDON_TOKEN
        )
        p2 = _make_projection(
            position_id="pos-b", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        for proj in (p1, p2):
            conn.execute(
                f"INSERT INTO position_current ({', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)}) "
                f"VALUES ({', '.join(['?'] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})",
                ordered_values(proj, CANONICAL_POSITION_CURRENT_COLUMNS),
            )
        _insert_event(conn, position_id="pos-a", seq=1, occurred_at="2026-05-17T10:00:00+00:00")
        _insert_event(conn, position_id="pos-b", seq=1, occurred_at="2026-05-17T11:00:00+00:00")

        report = consolidate(conn)
        assert report["chain_snapshot_used"] is False
        assert _LONDON_TOKEN in report["divergent_tokens"]
        assert report["voided_positions"] == []

    def test_divergent_when_chain_matches_db(self):
        """If db_sum <= chain_shares the consolidator SKIPs (legitimate split)."""
        from src.state.position_duplicate_consolidator import consolidate
        from src.state.projection import (
            CANONICAL_POSITION_CURRENT_COLUMNS,
            ordered_values,
        )

        conn = _fresh_conn()
        # 2 rows × 6 shares = 12 DB, chain = 20 → legitimate
        p1 = _make_projection(
            position_id="pos-a", phase="pending_exit", token_id=_LONDON_TOKEN
        )
        p2 = _make_projection(
            position_id="pos-b", phase="pending_entry", token_id=_LONDON_TOKEN
        )
        for proj in (p1, p2):
            conn.execute(
                f"INSERT INTO position_current ({', '.join(CANONICAL_POSITION_CURRENT_COLUMNS)}) "
                f"VALUES ({', '.join(['?'] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})",
                ordered_values(proj, CANONICAL_POSITION_CURRENT_COLUMNS),
            )
        _insert_event(conn, position_id="pos-a", seq=1, occurred_at="2026-05-17T10:00:00+00:00")
        _insert_event(conn, position_id="pos-b", seq=1, occurred_at="2026-05-17T11:00:00+00:00")
        conn.execute(
            "INSERT INTO collateral_ledger_snapshots (captured_at, authority_tier, ctf_token_balances_json) "
            "VALUES (?, 'CHAIN', ?)",
            ("2026-05-18T00:01:00+00:00", json.dumps({_LONDON_TOKEN: 20_000_000})),
        )

        report = consolidate(conn)
        assert _LONDON_TOKEN in report["divergent_tokens"]
        assert report["voided_positions"] == []

    def test_karachi_safety_singleton_is_noop(self):
        """Karachi (1 row) MUST pass through unaffected."""
        from src.state.position_duplicate_consolidator import consolidate

        conn = _fresh_conn()
        _seed_karachi_singleton(conn)
        conn.execute(
            "INSERT INTO collateral_ledger_snapshots (captured_at, authority_tier, ctf_token_balances_json) "
            "VALUES (?, 'CHAIN', ?)",
            ("2026-05-18T00:01:00+00:00", json.dumps({_KARACHI_TOKEN: 1_587_300})),
        )

        report = consolidate(conn)
        assert report["scanned_tokens"] == 0
        assert report["voided_positions"] == []
        # Karachi row untouched
        row = conn.execute(
            "SELECT phase, shares FROM position_current WHERE position_id='c30f28a5-d4e'"
        ).fetchone()
        assert row[0] == "day0_window"
        assert row[1] == 1.5873

    def test_consolidate_token_scoped(self):
        """consolidate_token(token) operates on a single token only."""
        from src.state.position_duplicate_consolidator import consolidate_token

        conn = _fresh_conn()
        _seed_london_duplicate(conn)
        _seed_karachi_singleton(conn)

        report = consolidate_token(conn, _LONDON_TOKEN)
        assert _LONDON_TOKEN in report["overbook_tokens"]
        assert "0a0e3b72-46e" in report["voided_positions"]
        # Karachi untouched
        kr = conn.execute(
            "SELECT phase FROM position_current WHERE position_id='c30f28a5-d4e'"
        ).fetchone()
        assert kr[0] == "day0_window"


# -------------------------- LONDON REPLAY END-TO-END -------------------------- #


class TestLondonReplay:

    def test_full_replay_then_migration_then_writer_block(self):
        """End-to-end: dirty DB → consolidator → migration → writer-side
        check blocks the next stale entry attempt for the same token."""
        from src.state.position_duplicate_consolidator import consolidate
        from src.state.projection import (
            DuplicatePositionOpenError,
            upsert_position_current,
        )

        mod = _load_migration()
        conn = _fresh_conn()
        _seed_london_duplicate(conn)

        # Step 1: consolidator collapses to 1 row
        report = consolidate(conn)
        assert "0a0e3b72-46e" in report["voided_positions"]

        # Step 2: migration applies cleanly (duplicates resolved)
        mod.up(conn)

        # Step 3: writer-side check blocks any new stale-entry attempt
        new_p = _make_projection(
            position_id="pos-stale-c",
            phase="pending_entry",
            token_id=_LONDON_TOKEN,
        )
        with pytest.raises(DuplicatePositionOpenError):
            upsert_position_current(conn, new_p)

        # Step 4: chain truth (6 shares) matches surviving row
        survivor = conn.execute(
            "SELECT position_id, shares FROM position_current "
            f"WHERE token_id='{_LONDON_TOKEN}' AND phase != 'voided'"
        ).fetchone()
        assert survivor[0] == "7557a029-4ad"
        assert survivor[1] == 6.0
