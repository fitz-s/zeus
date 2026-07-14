# Created: 2026-07-14
# Last reused or audited: 2026-07-14
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-3R +
#   src/contracts/economics_ownership.py FORBIDDEN_COLUMNS_BY_TABLE +
#   src/state/table_registry.py (authoritative table->DB ownership: this
#   migration's own fixture setup must match production reality, not the
#   simpler single-DB assumption an earlier draft of this test made --
#   position_current is authoritative on zeus_trades.db,
#   edli_live_profit_audit is authoritative on zeus-world.db, per
#   architecture/db_table_ownership.yaml).
"""Acceptance gate for scripts/migrations/2026_07_economics_column_firewall.py.

Proves the STAGED, cross-DB firewall migration -- never applied to a live DB
by this packet, see that script's module docstring -- does what it claims
against fresh temp-directory fixture DBs:

  1. A forbidden-column INSERT/UPDATE on position_current (zeus_trades.db)
     or edli_live_profit_audit (zeus-world.db) RAISEs (sqlite3.IntegrityError,
     via the trigger's RAISE(ABORT, ...)).
  2. A phase-only (non-forbidden-column) UPDATE on position_current still
     succeeds -- SQLite's own "UPDATE OF col-list" semantics, no extra logic.
  3. edli_live_profit_audit.promotion_eligible's schema-mandated NOT NULL
     DEFAULT 0 does not permanently brick every insert into that table: 0
     (the neutral "not yet eligible" state, the value the real writer
     src/events/live_profit_audit.py always uses on a fresh row) passes; 1
     (the affirmative claim) is blocked exactly like every other forbidden
     column's non-neutral value.
  4. A reduce_position_economics insert (a DIFFERENT table, the deterministic
     reducer's own publish target) is completely unaffected.
  5. Idempotent re-run, and the writer-plane-fence refusal.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SCRIPT = ROOT / "scripts" / "migrations" / "2026_07_economics_column_firewall.py"

sys.path.insert(0, str(ROOT))

from src.reduce.schema.generation_schema import ensure_tables as ensure_reduce_tables  # noqa: E402
from src.state.db import init_schema_trade_only, init_schema_world_only  # noqa: E402


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("economics_column_firewall_migration", MIGRATION_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mig = _load_migration_module()


# ---------------------------------------------------------------------------
# Fixture DBs -- position_current lives on trade, edli_live_profit_audit on
# world (verified against architecture/db_table_ownership.yaml).
# ---------------------------------------------------------------------------


def _build_fixture(state_dir: Path):
    """Fresh trade + world DBs -- current schema, both forbidden tables
    present in their real (registry-authoritative) homes, firewall not yet
    installed. Returns a mig.DbPaths."""
    state_dir.mkdir(parents=True, exist_ok=True)
    trade_path = state_dir / "zeus_trades.db"
    world_path = state_dir / "zeus-world.db"

    trade_conn = sqlite3.connect(str(trade_path))
    trade_conn.execute("PRAGMA journal_mode = WAL")
    init_schema_trade_only(trade_conn)
    trade_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    trade_conn.close()

    world_conn = sqlite3.connect(str(world_path))
    world_conn.execute("PRAGMA journal_mode = WAL")
    init_schema_world_only(world_conn)
    world_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    world_conn.close()

    return mig.DbPaths(trade=trade_path, world=world_path)


@pytest.fixture()
def fixture_env(monkeypatch):
    monkeypatch.setenv(mig._SKIP_PROCESS_CHECK_ENV_VAR, "1")
    yield


def _apply_migration(tmp_path: Path, state_dir: Path):
    paths = _build_fixture(state_dir)
    rc = mig.main([
        "--operator-confirms-fenced",
        "--state-dir", str(state_dir),
        "--backup-dir", str(tmp_path / "backups"),
    ])
    assert rc == 0
    return paths


def _insert_position_current(conn: sqlite3.Connection, position_id: str, **overrides) -> None:
    defaults = dict(
        phase="active",
        strategy_key="edli",
        temperature_metric="high",
        updated_at="2026-07-14T00:00:00+00:00",
    )
    defaults.update(overrides)
    columns = ["position_id", *defaults.keys()]
    values = [position_id, *defaults.values()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO position_current ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def _insert_edli(conn: sqlite3.Connection, audit_id: str, **overrides) -> None:
    defaults = dict(
        event_id=f"evt-{audit_id}",
        aggregate_id=f"agg-{audit_id}",
        condition_id="0xcond1",
        token_id="tok-1",
        order_lifecycle_state="FILLED",
        created_at="2026-07-14T00:00:00+00:00",
        schema_version=1,
    )
    defaults.update(overrides)
    columns = ["audit_id", *defaults.keys()]
    values = [audit_id, *defaults.values()]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO edli_live_profit_audit ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


# ---------------------------------------------------------------------------
# position_current (zeus_trades.db)
# ---------------------------------------------------------------------------


def test_forbidden_column_insert_raises_on_position_current(tmp_path, fixture_env):
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.trade))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="forbidden chain-derivable economics column"):
            _insert_position_current(conn, "p1", shares=10.0)
    finally:
        conn.close()


def test_non_forbidden_insert_succeeds_on_position_current(tmp_path, fixture_env):
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.trade))
    try:
        _insert_position_current(conn, "p1")  # no forbidden column touched
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 1
    finally:
        conn.close()


def test_forbidden_column_update_raises_on_position_current(tmp_path, fixture_env):
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.trade))
    try:
        _insert_position_current(conn, "p1")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="forbidden chain-derivable economics column"):
            conn.execute("UPDATE position_current SET realized_pnl_usd = 5.0 WHERE position_id = 'p1'")
    finally:
        conn.close()


def test_phase_only_update_succeeds_on_position_current(tmp_path, fixture_env):
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.trade))
    try:
        _insert_position_current(conn, "p1")
        conn.commit()
        conn.execute("UPDATE position_current SET phase = 'settled' WHERE position_id = 'p1'")
        conn.commit()
        assert conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'p1'"
        ).fetchone()[0] == "settled"
    finally:
        conn.close()


def test_update_setting_forbidden_column_to_null_does_not_raise(tmp_path, fixture_env):
    """Clearing a forbidden column to NULL is not "setting it to a non-null
    value" -- the firewall must not block that (only an affirmative
    non-null/non-neutral write is forbidden)."""
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.trade))
    try:
        _insert_position_current(conn, "p1")
        conn.commit()
        conn.execute("UPDATE position_current SET shares = NULL WHERE position_id = 'p1'")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# edli_live_profit_audit (zeus-world.db) -- including the promotion_eligible
# NOT NULL exception
# ---------------------------------------------------------------------------


def test_forbidden_column_insert_raises_on_edli(tmp_path, fixture_env):
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.world))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="forbidden chain-derivable economics column"):
            _insert_edli(conn, "a1", pnl_usd=12.5)
    finally:
        conn.close()


def test_promotion_eligible_zero_insert_succeeds_on_edli(tmp_path, fixture_env):
    """The neutral/default state -- the real writer (src/events/
    live_profit_audit.py) always sets this explicitly to 0 or 1 on a fresh
    row; 0 must keep working post-firewall or every legitimate audit-row
    insert would be permanently bricked."""
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.world))
    try:
        _insert_edli(conn, "a1", promotion_eligible=0)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM edli_live_profit_audit").fetchone()[0] == 1
    finally:
        conn.close()


def test_promotion_eligible_default_omitted_insert_succeeds_on_edli(tmp_path, fixture_env):
    """Same as above but relying on the schema DEFAULT 0 entirely (column
    omitted from the INSERT column list) -- SQLite resolves the default
    before the BEFORE INSERT trigger ever sees the row."""
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.world))
    try:
        _insert_edli(conn, "a1")  # promotion_eligible omitted -> DEFAULT 0
        conn.commit()
        assert conn.execute(
            "SELECT promotion_eligible FROM edli_live_profit_audit WHERE audit_id = 'a1'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_promotion_eligible_one_insert_raises_on_edli(tmp_path, fixture_env):
    """The affirmative "eligible" claim is exactly the chain-derivable-
    adjacent forbidden authority this firewall exists to block."""
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.world))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="forbidden chain-derivable economics column"):
            _insert_edli(conn, "a1", promotion_eligible=1)
    finally:
        conn.close()


def test_promotion_eligible_update_to_one_raises_but_other_column_update_succeeds(tmp_path, fixture_env):
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.world))
    try:
        _insert_edli(conn, "a1", promotion_eligible=0)
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="forbidden chain-derivable economics column"):
            conn.execute("UPDATE edli_live_profit_audit SET promotion_eligible = 1 WHERE audit_id = 'a1'")

        conn.execute("UPDATE edli_live_profit_audit SET order_lifecycle_state = 'CONFIRMED' WHERE audit_id = 'a1'")
        conn.commit()
        assert conn.execute(
            "SELECT order_lifecycle_state FROM edli_live_profit_audit WHERE audit_id = 'a1'"
        ).fetchone()[0] == "CONFIRMED"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# reduce_position_economics -- a different table, unaffected
# ---------------------------------------------------------------------------


def test_reduce_position_economics_insert_succeeds_unaffected_by_firewall(tmp_path, fixture_env):
    paths = _apply_migration(tmp_path, tmp_path / "state")
    conn = sqlite3.connect(str(paths.trade))
    try:
        ensure_reduce_tables(conn)
        conn.execute(
            """
            INSERT INTO reduce_generations (
                generation_id, reducer_version, computed_at,
                input_fingerprint, coverage_json, position_ids_json
            ) VALUES ('gen-1', 'lx2r-synthetic-1', '2026-07-14T00:00:00+00:00', 'fp', '{}', '["p1"]')
            """
        )
        conn.execute(
            """
            INSERT INTO reduce_position_economics (
                generation_id, position_id, keeper_position_id,
                absorbed_position_ids_json, net_shares, cost_basis_usd,
                realized_pnl_usd, fees_usd, fill_count, payout_status,
                payout_pnl_usd
            ) VALUES ('gen-1', 'p1', 'p1', '[]', 0.0, 0.0, 5.0, 0.0, 1, 'CLOSED_VIA_FILLS', NULL)
            """
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM reduce_position_economics").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Idempotency + writer-plane fence + no-op edges
# ---------------------------------------------------------------------------


def test_migration_idempotent_rerun_is_noop(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    paths = _build_fixture(state_dir)
    backup_dir = tmp_path / "backups"

    rc1 = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc1 == 0

    def _firewall_trigger_snapshot() -> tuple:
        trade_conn = sqlite3.connect(str(paths.trade))
        world_conn = sqlite3.connect(str(paths.world))
        try:
            trade_rows = trade_conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%_economics_firewall_%' ORDER BY name"
            ).fetchall()
            world_rows = world_conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%_economics_firewall_%' ORDER BY name"
            ).fetchall()
            return (tuple(trade_rows), tuple(world_rows))
        finally:
            trade_conn.close()
            world_conn.close()

    snap1 = _firewall_trigger_snapshot()

    rc2 = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc2 == 0
    snap2 = _firewall_trigger_snapshot()
    assert snap1 == snap2, "re-running a completed migration must be a pure no-op"

    trade_triggers, world_triggers = snap1
    assert len(trade_triggers) == 2, "position_current: insert + update"
    assert len(world_triggers) == 2, "edli_live_profit_audit: insert + update"

    # No-op re-run must not have taken a second backup.
    assert len(list(backup_dir.glob("*"))) == 1


def test_migration_no_op_when_trade_db_missing(tmp_path, fixture_env):
    state_dir = tmp_path / "state_never_created"
    rc = mig.main(["--operator-confirms-fenced", "--state-dir", str(state_dir)])
    assert rc == 0


def test_migration_no_op_when_no_forbidden_tables_present(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(state_dir / "zeus_trades.db"))
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    rc = mig.main(["--operator-confirms-fenced", "--state-dir", str(state_dir)])
    assert rc == 0


def test_refuses_without_operator_confirms_fenced(tmp_path, monkeypatch):
    monkeypatch.delenv(mig._SKIP_PROCESS_CHECK_ENV_VAR, raising=False)
    state_dir = tmp_path / "state"
    _build_fixture(state_dir)

    with pytest.raises(SystemExit):
        mig.main(["--state-dir", str(state_dir)])


def test_backup_written_for_both_dbs_and_integrity_ok(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    _build_fixture(state_dir)
    backup_dir = tmp_path / "backups"

    rc = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir),
    ])
    assert rc == 0

    import json
    manifests = list(backup_dir.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["trade"]["integrity_check"] == "ok"
    assert manifest["world"]["integrity_check"] == "ok"


def test_skip_backup_flag_writes_no_backup(tmp_path, fixture_env):
    state_dir = tmp_path / "state"
    _build_fixture(state_dir)
    backup_dir = tmp_path / "backups"

    rc = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(state_dir),
        "--backup-dir", str(backup_dir), "--skip-backup",
    ])
    assert rc == 0
    assert not backup_dir.exists() or list(backup_dir.glob("*")) == []
