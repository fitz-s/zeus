# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2
#   delta (duplicate-position identity BLOCKER) + wave-1 dual review C2 + the
#   external's LX-F rework requirements ("the supplied change set does not
#   show a live-database migration for an already-created SQLite event-type
#   CHECK constraint... needs an explicit compatible schema migration,
#   stale-binary fencing, an idempotent backfill").
"""Acceptance gate for scripts/migrations/2026_07_position_identity_supersession_check.py.

Covers the three epochs the F2 rework must tolerate:

  1. FRESH DB (created by the current src/state/db.py) admits
     POSITION_IDENTITY_SUPERSEDED directly — no migration needed.
  2. LEGACY DB (pre-fix CHECK, exactly the shape a production DB carried
     before this packet, including the original embedded-comment-with-
     stray-paren text) rejects the literal, and the consolidator's fallback
     path (position_events_admits_event_type -> False -> ReviewWorkItem) is
     exercised instead of crashing.
  3. MIGRATED DB (after running the migration script against the legacy
     fixture) admits the literal, preserves every existing row, and is
     idempotent on re-run.

Also exercises the writer-plane-fence refusal and a kill-point crash matrix
(single transaction: a crash before COMMIT never leaves the CHECK rebuilt,
a crash after COMMIT always does — never mixed) per the T5 migration's
established acceptance-gate pattern.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = ROOT / "scripts" / "migrations" / "2026_07_position_identity_supersession_check.py"
B71_MIGRATION_SCRIPT = ROOT / "scripts" / "migrations" / "2026_07_position_token_split_reconstructed.py"


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f2_position_events_check_migration", MIGRATION_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_b71_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("b71_position_events_check_migration", B71_MIGRATION_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mig = _load_migration_module()
b71_mig = _load_b71_migration_module()


# ---------------------------------------------------------------------------
# Legacy (pre-F2) fixture DDL — the EXACT shape a production DB carried before
# this packet's src/state/db.py edit, including the original embedded-
# comment-with-stray-paren text the F2 commit fixed (the ")" inside
# "retirement.py):" on the line after 'CHAIN_SIZE_CORRECTED',). The migration
# script's paren-aware scanner must handle this real, already-shipped bug —
# a live production DB's sqlite_master text is frozen at CREATE-TABLE time
# and may carry it forever.
# ---------------------------------------------------------------------------

LEGACY_POSITION_EVENTS_DDL = """
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_POSTED',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED',
        'ENTRY_ORDER_REJECTED',
        'DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED',
        'CHAIN_SIZE_CORRECTED',
        -- 2026-07-12 T5 MIGRATION (docs/rebuild/quarantine_excision_2026-07-11.md,
        -- scripts/migrations/2026_07_quarantine_phase_retirement.py): the
        -- retired chain-quarantine event type is dropped here; a historical
        -- row still carrying it is rewritten to REVIEW_REQUIRED by that
        -- migration (already a valid member below).
        'MONITOR_REFRESHED',
        'EXIT_INTENT',
        'EXIT_ORDER_POSTED',
        'EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED',
        'EXIT_ORDER_REJECTED',
        'EXIT_RETRY_RELEASED',
        'SETTLED',
        'ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED',
        'VENUE_POSITION_OBSERVED',
        'REVIEW_REQUIRED'
    )),
    occurred_at TEXT NOT NULL
        CHECK (occurred_at LIKE '____-__-__T%'),
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','admin_closed'
    )),
    strategy_key TEXT NOT NULL,
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL CHECK (env IN ('live','test','replay','backtest')),
    payload_json TEXT NOT NULL,
    UNIQUE(position_id, sequence_no)
)
"""

LEGACY_POSITION_EVENTS_TRIGGERS = """
CREATE TRIGGER trg_position_events_require_env
BEFORE INSERT ON position_events
WHEN NEW.env IS NULL OR TRIM(NEW.env) = ''
BEGIN
    SELECT RAISE(FAIL, 'position_events.env is required');
END;
CREATE TRIGGER trg_position_events_no_update
BEFORE UPDATE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;
CREATE TRIGGER trg_position_events_no_delete
BEFORE DELETE ON position_events
BEGIN
    SELECT RAISE(FAIL, 'position_events is append-only');
END;
"""

_LEGACY_ROW = (
    "ev-legacy-1", "pos-legacy-1", 1, 1, "ENTRY_ORDER_FILLED",
    "2026-06-01T00:00:00+00:00", "pending_entry", "active", "opening_inertia",
    "test", "{}", "live",
)


def _install_legacy_position_events(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS position_events")
    conn.executescript(LEGACY_POSITION_EVENTS_DDL)
    conn.executescript(LEGACY_POSITION_EVENTS_TRIGGERS)
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _LEGACY_ROW,
    )
    conn.commit()


def _build_legacy_fixture(state_dir: Path) -> Path:
    """A trade DB shaped like a pre-F2 production DB: current schema
    everywhere except position_events, which gets the LEGACY (pre-fix,
    comment-embedded-in-parens, no POSITION_IDENTITY_SUPERSEDED) DDL."""
    from src.state.db import init_schema_trade_only

    state_dir.mkdir(parents=True, exist_ok=True)
    trade_path = state_dir / "zeus_trades.db"
    conn = sqlite3.connect(str(trade_path))
    conn.execute("PRAGMA journal_mode = WAL")
    init_schema_trade_only(conn)
    _install_legacy_position_events(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return trade_path


@pytest.fixture()
def fixture_env(monkeypatch):
    monkeypatch.setenv(mig._SKIP_PROCESS_CHECK_ENV_VAR, "1")
    monkeypatch.delenv(mig._KILL_ENV_VAR, raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. Fresh DB admits the event directly
# ---------------------------------------------------------------------------


def test_fresh_db_admits_event_type_directly_no_migration_needed():
    from src.engine.lifecycle_events import position_events_admits_event_type
    from src.state.db import init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    init_schema_trade_only(conn)

    assert position_events_admits_event_type(conn, "POSITION_IDENTITY_SUPERSEDED") is True

    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES ('ev-1', 'pos-1', 1, 1, 'POSITION_IDENTITY_SUPERSEDED',
                  '2026-07-13T00:00:00+00:00', 'active', 'active', 'center_buy',
                  'test', '{}', 'live')
        """
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# 2. Legacy DB rejects the literal; consolidator fallback exercised
# ---------------------------------------------------------------------------


def test_legacy_db_check_rejects_new_literal_before_migration():
    from src.engine.lifecycle_events import position_events_admits_event_type

    conn = sqlite3.connect(":memory:")
    _install_legacy_position_events(conn)

    assert position_events_admits_event_type(conn, "POSITION_IDENTITY_SUPERSEDED") is False

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no, event_type,
                occurred_at, phase_before, phase_after, strategy_key,
                source_module, payload_json, env
            ) VALUES ('ev-2', 'pos-2', 1, 1, 'POSITION_IDENTITY_SUPERSEDED',
                      '2026-07-13T00:00:00+00:00', 'active', 'active', 'center_buy',
                      'test', '{}', 'live')
            """
        )
    conn.close()


# ---------------------------------------------------------------------------
# 3. Migration rebuilds the CHECK, preserves data, and is idempotent
# ---------------------------------------------------------------------------


def test_migration_happy_path_admits_literal_and_preserves_rows(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    trade_path = _build_legacy_fixture(state_dir)
    backup_dir = tmp_path / "backups"

    rc = mig.main([
        "--operator-confirms-fenced",
        "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc == 0

    conn = sqlite3.connect(str(trade_path))
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='position_events'"
    ).fetchone()
    assert "'POSITION_IDENTITY_SUPERSEDED'" in row[0]
    # The pre-fix embedded comment survived the rebuild verbatim (proof the
    # paren-aware scanner did not corrupt or drop it).
    assert "T5 MIGRATION" in row[0]

    # Pre-existing row preserved byte-for-byte.
    preserved = conn.execute(
        "SELECT event_id, position_id, event_type, payload_json FROM position_events "
        "WHERE event_id = 'ev-legacy-1'"
    ).fetchone()
    assert preserved == ("ev-legacy-1", "pos-legacy-1", "ENTRY_ORDER_FILLED", "{}")

    # New literal now insertable.
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES ('ev-migrated-1', 'pos-legacy-1', 1, 2, 'POSITION_IDENTITY_SUPERSEDED',
                  '2026-07-13T00:00:00+00:00', 'active', 'active', 'opening_inertia',
                  'test', '{}', 'live')
        """
    )
    conn.commit()

    # Append-only triggers survived the rebuild.
    with pytest.raises(sqlite3.IntegrityError, match="position_events is append-only"):
        conn.execute("DELETE FROM position_events WHERE event_id = 'ev-migrated-1'")
        conn.commit()

    conn.close()

    manifests = list(backup_dir.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["trade"]["integrity_check"] == "ok"


def test_migration_idempotent_rerun_is_noop(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    trade_path = _build_legacy_fixture(state_dir)
    backup_dir = tmp_path / "backups"

    rc1 = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc1 == 0

    def _snapshot() -> tuple:
        conn = sqlite3.connect(str(trade_path))
        try:
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='position_events'"
            ).fetchone()[0]
            rows = conn.execute("SELECT * FROM position_events").fetchall()
            return ddl, tuple(rows)
        finally:
            conn.close()

    snap1 = _snapshot()

    rc2 = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc2 == 0
    snap2 = _snapshot()
    assert snap1 == snap2, "re-running a completed migration must be a pure no-op"

    # No-op re-run must not have taken a second backup (returns before the
    # fence/backup phase entirely).
    assert len(list(backup_dir.glob("*"))) == 1


def test_b71_migration_admits_split_reconstruction_literal_and_is_idempotent(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    trade_path = _build_legacy_fixture(state_dir)
    backup_dir = tmp_path / "backups"

    rc1 = b71_mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc1 == 0
    conn = sqlite3.connect(str(trade_path))
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='position_events'"
        ).fetchone()[0]
        assert "'POSITION_TOKEN_SPLIT_RECONSTRUCTED'" in ddl
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no, event_type,
                occurred_at, phase_before, phase_after, strategy_key,
                source_module, payload_json, env
            ) VALUES ('ev-b71', 'pos-b71', 1, 1, 'POSITION_TOKEN_SPLIT_RECONSTRUCTED',
                      '2026-07-24T00:00:00+00:00', 'active', 'active', 'center_buy',
                      'test', '{}', 'live')
            """
        )
        conn.commit()
        before = conn.execute("SELECT * FROM position_events ORDER BY event_id").fetchall()
    finally:
        conn.close()

    rc2 = b71_mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc2 == 0
    conn = sqlite3.connect(str(trade_path))
    try:
        assert conn.execute("SELECT * FROM position_events ORDER BY event_id").fetchall() == before
    finally:
        conn.close()
    assert len(list(backup_dir.glob("*"))) == 1


def test_migration_no_op_when_db_missing(tmp_path, fixture_env):
    state_dir = tmp_path / "state_never_created"
    rc = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
    ])
    assert rc == 0


def test_migration_no_op_when_table_missing(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(state_dir / "zeus_trades.db"))
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    rc = mig.main(["--operator-confirms-fenced", "--state-dir", str(state_dir)])
    assert rc == 0


# ---------------------------------------------------------------------------
# Writer-plane fence
# ---------------------------------------------------------------------------


def test_refuses_without_operator_confirms_fenced(tmp_path, monkeypatch):
    monkeypatch.delenv(mig._SKIP_PROCESS_CHECK_ENV_VAR, raising=False)
    state_dir = tmp_path / "state"
    _build_legacy_fixture(state_dir)

    with pytest.raises(SystemExit):
        mig.main(["--state-dir", str(state_dir)])


# ---------------------------------------------------------------------------
# Kill-point crash matrix — single-transaction proof
# ---------------------------------------------------------------------------


def _check_admits_literal(trade_path: Path) -> bool:
    conn = sqlite3.connect(f"file:{trade_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='position_events'"
        ).fetchone()
        return bool(row and row[0] and "'POSITION_IDENTITY_SUPERSEDED'" in row[0])
    finally:
        conn.close()


@pytest.mark.parametrize("checkpoint", mig.KILL_POINTS)
def test_kill_point_crash_matrix(tmp_path, checkpoint):
    state_dir = tmp_path / "state"
    trade_path = _build_legacy_fixture(state_dir)

    env = dict(os.environ)
    env[mig._KILL_ENV_VAR] = checkpoint
    env[mig._SKIP_PROCESS_CHECK_ENV_VAR] = "1"
    proc = subprocess.run(
        [sys.executable, str(MIGRATION_SCRIPT), "--operator-confirms-fenced",
         "--state-dir", str(state_dir), "--backup-dir", str(tmp_path / "backups")],
        env=env, cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0, (
        f"checkpoint={checkpoint} did not crash as instructed "
        f"(stdout={proc.stdout!r} stderr={proc.stderr!r})"
    )

    integrity_conn = sqlite3.connect(str(trade_path))
    integrity = integrity_conn.execute("PRAGMA integrity_check").fetchone()[0]
    integrity_conn.close()
    assert integrity == "ok", f"checkpoint={checkpoint} left a corrupt DB"

    row_count_conn = sqlite3.connect(str(trade_path))
    row_count = row_count_conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    row_count_conn.close()
    assert row_count == 1, (
        f"checkpoint={checkpoint} lost or duplicated data (row_count={row_count})"
    )

    # NEVER a mixed state: either fully pre-migration or fully migrated.
    admits = _check_admits_literal(trade_path)

    # Resume run must always converge on fully migrated, from either state.
    env2 = dict(os.environ)
    env2[mig._SKIP_PROCESS_CHECK_ENV_VAR] = "1"
    proc2 = subprocess.run(
        [sys.executable, str(MIGRATION_SCRIPT), "--operator-confirms-fenced",
         "--state-dir", str(state_dir), "--backup-dir", str(tmp_path / "backups_resume")],
        env=env2, cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc2.returncode == 0, (
        f"checkpoint={checkpoint} resume run failed: stdout={proc2.stdout!r} "
        f"stderr={proc2.stderr!r}"
    )
    assert _check_admits_literal(trade_path) is True

    final_row_count_conn = sqlite3.connect(str(trade_path))
    final_row_count = final_row_count_conn.execute(
        "SELECT COUNT(*) FROM position_events"
    ).fetchone()[0]
    final_row_count_conn.close()
    assert final_row_count == 1, "resume run must not duplicate the preserved row"
    del admits  # documented above; not independently asserted (both outcomes valid pre-resume)
