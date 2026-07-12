# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-2 (offline RED cutover protocol, kill-point crash matrix is the acceptance
#   gate) + conductor log "T5-CORE: LANDED 05a751290" (T5 MIGRATION mapping).
"""Acceptance gate for scripts/migrations/2026_07_quarantine_phase_retirement.py.

Builds fixture DBs shaped exactly like a pre-migration production DB (legacy
CHECK constraints still permitting the quarantine-family literals, WAL mode,
representative legacy rows), then:

  1. exercises the happy path end-to-end (data remap, CHECK rebuild, schema_epoch
     stamp, ReviewWorkItem minting, idempotent re-run);
  2. exercises the writer-plane fence refusal;
  3. exercises the startup mixed-epoch refusal (src.state.db.assert_schema_epoch_not_mixed);
  4. THE ACCEPTANCE GATE: for every named kill point (post_fence_check,
     post_backup, mid_ddl, mid_copy, post_validate, post_stamp, pre_commit),
     runs the migration in a subprocess with ZEUS_T5_KILL_AT set, hard-kills it
     via os._exit(1) at that exact point, and asserts the three DBs are either
     byte-for-byte-equivalent-in-content to the pre-migration fixture (rollback
     journal recovered) OR fully migrated — NEVER a mixed state — and that the
     startup guard agrees.

"Byte-identical" per the task brief is interpreted as content-hash equivalence
(schema DDL text + full row content per table), not literal file-byte equality:
SQLite's rollback-journal recovery guarantees the former, never guarantees the
latter (free-page reuse, page cache ordering), and a literal byte-diff test
would be flaky for reasons unrelated to correctness.
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
MIGRATION_SCRIPT = ROOT / "scripts" / "migrations" / "2026_07_quarantine_phase_retirement.py"


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("t5_quarantine_migration", MIGRATION_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses' KW_ONLY/_is_type introspection needs the module registered
    # in sys.modules before exec (observed on Python 3.14) — register it
    # under a name that will never collide with a real package.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mig = _load_migration_module()


# ---------------------------------------------------------------------------
# Legacy (pre-migration) fixture DDL — the exact shape a production DB carried
# before this packet's db.py / architecture/2026_04_02_architecture_kernel.sql
# edits tightened the CHECK constraints. Mirrors the house style established
# by tests/test_fill_bridge_dispositions_migration.py's LEGACY_*_DDL fixtures
# for T1's analogous migration.
# ---------------------------------------------------------------------------

LEGACY_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (phase IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
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
    strategy_key TEXT NOT NULL,
    chain_state TEXT,
    token_id TEXT,
    condition_id TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL
)
"""

LEGACY_POSITION_EVENTS_DDL = """
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT','ENTRY_ORDER_POSTED','ENTRY_ORDER_FILLED',
        'CHAIN_SYNCED','CHAIN_QUARANTINED','MONITOR_REFRESHED','EXIT_INTENT',
        'EXIT_ORDER_POSTED','EXIT_ORDER_FILLED','SETTLED','ADMIN_VOIDED',
        'VENUE_POSITION_OBSERVED','REVIEW_REQUIRED'
    )),
    occurred_at TEXT NOT NULL,
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry','active','day0_window','pending_exit',
        'economically_closed','settled','voided','quarantined','admin_closed'
    )),
    strategy_key TEXT NOT NULL,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL,
    payload_json TEXT NOT NULL
)
"""
LEGACY_POSITION_EVENTS_TRIGGERS = """
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

LEGACY_POSITION_LOTS_DDL = """
CREATE TABLE position_lots (
    lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'OPTIMISTIC_EXPOSURE','CONFIRMED_EXPOSURE','EXIT_PENDING',
        'ECONOMICALLY_CLOSED_OPTIMISTIC','ECONOMICALLY_CLOSED_CONFIRMED',
        'SETTLED','QUARANTINED'
    )),
    shares TEXT NOT NULL,
    entry_price_avg TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    state_changed_at TEXT NOT NULL
)
"""


def _install_legacy_tables(conn: sqlite3.Connection) -> None:
    for stmt in ("DROP TABLE IF EXISTS position_events", "DROP TABLE IF EXISTS position_current",
                 "DROP TABLE IF EXISTS position_lots"):
        conn.execute(stmt)
    conn.executescript(LEGACY_POSITION_EVENTS_DDL)
    conn.executescript(LEGACY_POSITION_EVENTS_TRIGGERS)
    conn.executescript(LEGACY_POSITION_CURRENT_DDL)
    conn.executescript(LEGACY_POSITION_LOTS_DDL)


def build_legacy_fixture(state_dir: Path) -> "mig.DbPaths":
    """Build a pre-migration-shaped 3-DB fixture: current schema everywhere
    except position_current/position_events/position_lots, which get the
    LEGACY (quarantine-permitting) DDL + representative legacy rows."""
    from src.state.db import init_schema_forecasts, init_schema_trade_only, init_schema_world_only

    state_dir.mkdir(parents=True, exist_ok=True)
    paths = mig.DbPaths(
        world=state_dir / "zeus-world.db",
        forecasts=state_dir / "zeus-forecasts.db",
        trade=state_dir / "zeus_trades.db",
    )

    wc = sqlite3.connect(str(paths.world))
    init_schema_world_only(wc)
    _install_legacy_tables(wc)  # world carries the position_lots ghost shell too
    wc.execute(
        "INSERT INTO position_lots (lot_id, position_id, state, shares, entry_price_avg, "
        "captured_at, state_changed_at) VALUES (1, 9001, 'QUARANTINED', '10', '0.5', 't0', 't0')"
    )
    wc.commit()
    wc.close()

    fc = sqlite3.connect(str(paths.forecasts))
    init_schema_forecasts(fc)
    fc.commit()
    fc.close()

    tc = sqlite3.connect(str(paths.trade))
    tc.execute("PRAGMA journal_mode = WAL")
    init_schema_trade_only(tc)
    _install_legacy_tables(tc)

    # pos_a: quarantined phase, legacy chain_state, no exit intent -> expect 'active'.
    tc.execute(
        "INSERT INTO position_current (position_id, phase, strategy_key, updated_at, "
        "temperature_metric, chain_state, cost_basis_usd) VALUES "
        "('pos_a', 'quarantined', 'settlement_capture', '2026-06-01T00:00:00+00:00', "
        "'high', 'entry_authority_quarantined', 12.5)"
    )
    tc.execute(
        "INSERT INTO position_events (event_id, position_id, sequence_no, event_type, "
        "occurred_at, phase_before, phase_after, strategy_key, source_module, env, "
        "payload_json) VALUES ('ev_a1', 'pos_a', 1, 'ENTRY_ORDER_FILLED', "
        "'2026-06-01T00:00:00+00:00', 'pending_entry', 'active', 'settlement_capture', "
        "'test', 'test', '{}')"
    )
    tc.execute(
        "INSERT INTO position_events (event_id, position_id, sequence_no, event_type, "
        "occurred_at, phase_before, phase_after, strategy_key, source_module, env, "
        "payload_json) VALUES ('ev_a2', 'pos_a', 2, 'CHAIN_QUARANTINED', "
        "'2026-06-02T00:00:00+00:00', 'active', 'quarantined', 'settlement_capture', "
        "'test', 'test', '{}')"
    )

    # pos_b: quarantined phase, latest event is an open exit attempt -> expect 'pending_exit'.
    tc.execute(
        "INSERT INTO position_current (position_id, phase, strategy_key, updated_at, "
        "temperature_metric, chain_state, cost_basis_usd) VALUES "
        "('pos_b', 'quarantined', 'center_buy', '2026-06-01T00:00:00+00:00', "
        "'low', 'quarantine_expired', 7.0)"
    )
    tc.execute(
        "INSERT INTO position_events (event_id, position_id, sequence_no, event_type, "
        "occurred_at, phase_before, phase_after, strategy_key, source_module, env, "
        "payload_json) VALUES ('ev_b1', 'pos_b', 1, 'EXIT_INTENT', "
        "'2026-06-03T00:00:00+00:00', 'quarantined', 'quarantined', 'center_buy', "
        "'test', 'test', '{}')"
    )

    # pos_c: healthy row, untouched control.
    tc.execute(
        "INSERT INTO position_current (position_id, phase, strategy_key, updated_at, "
        "temperature_metric, chain_state, cost_basis_usd) VALUES "
        "('pos_c', 'active', 'opening_inertia', '2026-06-01T00:00:00+00:00', "
        "'high', 'synced', 3.0)"
    )

    tc.execute(
        "INSERT INTO position_lots (lot_id, position_id, state, shares, entry_price_avg, "
        "captured_at, state_changed_at) VALUES (1, 9001, 'QUARANTINED', '10', '0.5', 't0', 't0')"
    )
    tc.commit()
    tc.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    tc.close()
    return paths


def _content_snapshot(paths: "mig.DbPaths") -> dict:
    """Schema + full row-content hash per table, per DB — the "byte-identical"
    proxy this test module uses (see module docstring)."""
    snap = {}
    for label, path in (("world", paths.world), ("forecasts", paths.forecasts), ("trade", paths.trade)):
        if not path.exists():
            snap[label] = None
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            per_table = {}
            for t in sorted(tables):
                ddl = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
                ).fetchone()[0]
                rows = conn.execute(f"SELECT * FROM {t}").fetchall()  # noqa: S608
                per_table[t] = (ddl, tuple(sorted(str(r) for r in rows)))
            snap[label] = per_table
        finally:
            conn.close()
    return snap


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.fixture()
def fixture_env(monkeypatch):
    monkeypatch.setenv(mig._SKIP_PROCESS_CHECK_ENV_VAR, "1")
    monkeypatch.delenv(mig._KILL_ENV_VAR, raising=False)
    yield


def test_migration_happy_path(tmp_path, fixture_env):
    paths = build_legacy_fixture(tmp_path / "state")
    backup_dir = tmp_path / "backups"

    rc = mig.main([
        "--operator-confirms-fenced",
        "--state-dir", str(paths.trade.parent),
        "--backup-dir", str(backup_dir),
    ])
    assert rc == 0

    tc = sqlite3.connect(str(paths.trade))
    rows = {r[0]: dict(zip(("phase", "chain_state"), r[1:])) for r in
            tc.execute("SELECT position_id, phase, chain_state FROM position_current")}
    assert rows["pos_a"]["phase"] == "active"
    assert rows["pos_a"]["chain_state"] == "synced"
    assert rows["pos_b"]["phase"] == "pending_exit"
    assert rows["pos_b"]["chain_state"] == "synced"
    assert rows["pos_c"]["phase"] == "active"  # untouched control

    # CHECK actually rebuilt: legacy literal no longer insertable.
    with pytest.raises(sqlite3.IntegrityError):
        tc.execute(
            "INSERT INTO position_current (position_id, phase, strategy_key, updated_at, "
            "temperature_metric) VALUES ('poison', 'quarantined', 'x', 't', 'high')"
        )

    events = {r[0]: r[1] for r in
              tc.execute("SELECT event_id, event_type FROM position_events WHERE event_id='ev_a2'")}
    assert events["ev_a2"] == "REVIEW_REQUIRED"
    pb_pa = tc.execute(
        "SELECT phase_before, phase_after FROM position_events WHERE event_id='ev_a2'"
    ).fetchone()
    assert pb_pa == ("active", "active")

    lot_state = tc.execute("SELECT state FROM position_lots WHERE lot_id=1").fetchone()[0]
    assert lot_state == "CONFIRMED_EXPOSURE"

    rwi_rows = tc.execute(
        "SELECT subject_id, reason_code, status FROM review_work_items "
        "WHERE reason_code='LEGACY_QUARANTINE_MIGRATED' ORDER BY subject_id"
    ).fetchall()
    assert [r[0] for r in rwi_rows] == ["pos_a", "pos_b"]
    assert all(r[2] == "OPEN" for r in rwi_rows)

    epoch = tc.execute("SELECT epoch FROM schema_epoch WHERE id=1").fetchone()[0]
    assert epoch == mig.TARGET_SCHEMA_EPOCH
    tc.close()

    wc = sqlite3.connect(str(paths.world))
    world_epoch = wc.execute("SELECT epoch FROM schema_epoch WHERE id=1").fetchone()[0]
    assert world_epoch == mig.TARGET_SCHEMA_EPOCH
    world_lot_state = wc.execute("SELECT state FROM position_lots WHERE lot_id=1").fetchone()[0]
    assert world_lot_state == "CONFIRMED_EXPOSURE"
    wc.close()

    fcn = sqlite3.connect(str(paths.forecasts))
    forecasts_epoch = fcn.execute("SELECT epoch FROM schema_epoch WHERE id=1").fetchone()[0]
    assert forecasts_epoch == mig.TARGET_SCHEMA_EPOCH
    fcn.close()

    # Backup set exists and is integrity-verified.
    manifests = list(backup_dir.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    for label in ("world", "forecasts", "trade"):
        assert manifest[label]["integrity_check"] == "ok"


def test_migration_idempotent_rerun_is_noop(tmp_path, fixture_env):
    paths = build_legacy_fixture(tmp_path / "state")
    backup_dir = tmp_path / "backups"
    rc1 = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(paths.trade.parent),
        "--backup-dir", str(backup_dir),
    ])
    assert rc1 == 0
    snap1 = _content_snapshot(paths)

    rc2 = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(paths.trade.parent),
        "--backup-dir", str(backup_dir),
    ])
    assert rc2 == 0
    snap2 = _content_snapshot(paths)
    assert snap1 == snap2, "re-running a completed migration must be a pure no-op"

    # No-op re-run must not have taken a second backup (it returns before the
    # fence/backup phase entirely).
    assert len(list(backup_dir.glob("*"))) == 1


# ---------------------------------------------------------------------------
# Writer-plane fence
# ---------------------------------------------------------------------------


def test_refuses_without_operator_confirms_fenced(tmp_path, fixture_env):
    paths = build_legacy_fixture(tmp_path / "state")
    with pytest.raises(SystemExit):
        mig.main(["--state-dir", str(paths.trade.parent)])


def test_refuses_when_process_scan_finds_a_live_daemon(tmp_path, monkeypatch):
    monkeypatch.delenv(mig._SKIP_PROCESS_CHECK_ENV_VAR, raising=False)
    paths = build_legacy_fixture(tmp_path / "state")
    monkeypatch.setattr(mig, "_live_zeus_processes", lambda: ["12345 python -m src.main"])
    with pytest.raises(SystemExit):
        mig.main(["--operator-confirms-fenced", "--state-dir", str(paths.trade.parent)])


# ---------------------------------------------------------------------------
# Startup mixed-epoch refusal
# ---------------------------------------------------------------------------


def test_assert_schema_epoch_not_mixed():
    from src.state.db import assert_schema_epoch_not_mixed

    assert_schema_epoch_not_mixed(world_epoch=None, forecasts_epoch=None, trade_epoch=None)
    assert_schema_epoch_not_mixed(world_epoch="e1", forecasts_epoch="e1", trade_epoch="e1")
    with pytest.raises(RuntimeError, match="MIXED_SCHEMA_EPOCH"):
        assert_schema_epoch_not_mixed(world_epoch="e1", forecasts_epoch=None, trade_epoch="e1")
    with pytest.raises(RuntimeError, match="MIXED_SCHEMA_EPOCH"):
        assert_schema_epoch_not_mixed(world_epoch="e1", forecasts_epoch="e2", trade_epoch="e1")


def test_startup_refuses_after_partial_migration(tmp_path, fixture_env):
    """Simulates a partially-applied run (e.g. an operator manually restoring
    only one backup file) by stamping schema_epoch on the trade DB alone."""
    from src.state.db import SCHEMA_EPOCH_TABLE_DDL, assert_schema_epoch_not_mixed, read_schema_epoch

    paths = build_legacy_fixture(tmp_path / "state")
    tc = sqlite3.connect(str(paths.trade))
    tc.execute(SCHEMA_EPOCH_TABLE_DDL)
    tc.execute("INSERT INTO schema_epoch (id, epoch, stamped_at) VALUES (1, 'partial', 't0')")
    tc.commit()
    tc.close()

    tc = sqlite3.connect(f"file:{paths.trade}?mode=ro", uri=True)
    wc = sqlite3.connect(f"file:{paths.world}?mode=ro", uri=True)
    fcn = sqlite3.connect(f"file:{paths.forecasts}?mode=ro", uri=True)
    try:
        with pytest.raises(RuntimeError, match="MIXED_SCHEMA_EPOCH"):
            assert_schema_epoch_not_mixed(
                world_epoch=read_schema_epoch(wc),
                forecasts_epoch=read_schema_epoch(fcn),
                trade_epoch=read_schema_epoch(tc),
            )
    finally:
        tc.close(); wc.close(); fcn.close()

    # The migration's OWN idempotency pre-check must also refuse to run blind.
    rc = mig.main([
        "--operator-confirms-fenced", "--state-dir", str(paths.trade.parent),
    ])
    assert rc == 1


# ---------------------------------------------------------------------------
# Unit coverage for the generic literal-strip helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql,literal,expected_absent",
    [
        ("CHECK (x IN ('a','quarantined','b'))", "quarantined", "'quarantined'"),
        ("CHECK (x IN ('a','b','quarantined'))", "quarantined", "'quarantined'"),
        ("CHECK (x IN ('quarantined','a','b'))", "quarantined", "'quarantined'"),
        ("CHECK (x IN (\n  'a',\n  'quarantined',\n  'b'\n))", "quarantined", "'quarantined'"),
    ],
)
def test_strip_check_literal(sql, literal, expected_absent):
    result = mig._strip_check_literal(sql, literal)
    assert f"'{literal}'" not in result
    assert "'a'" in result or "x IN" in result  # sanity: didn't obliterate the whole clause


# ---------------------------------------------------------------------------
# Kill-point crash matrix — THE ACCEPTANCE GATE
# ---------------------------------------------------------------------------


def _classify_recovery(paths: "mig.DbPaths", pre_snapshot: dict) -> str:
    """Returns 'untouched' (content-hash-equal to the pre-migration snapshot,
    rollback journal recovered), 'migrated' (schema_epoch stamped + no
    remaining literals), or 'BAD' (neither — the failure this test exists to
    catch)."""
    # Trigger SQLite's hot-journal recovery by opening + querying each DB.
    for path in (paths.world, paths.forecasts, paths.trade):
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("SELECT 1").fetchone()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok", f"{path} failed integrity_check after recovery: {integrity}"
        finally:
            conn.close()

    epoch_state, epochs = mig._classify_epoch_state(paths)
    if epoch_state == "mixed":
        return "BAD"

    post_snapshot = _content_snapshot(paths)
    if epoch_state == "none":
        return "untouched" if post_snapshot == pre_snapshot else "BAD"

    # epoch_state == "complete": verify no remaining legacy literal anywhere.
    tc = sqlite3.connect(str(paths.trade))
    try:
        remaining = tc.execute(
            "SELECT COUNT(*) FROM position_current WHERE phase='quarantined'"
        ).fetchone()[0]
        remaining += tc.execute(
            "SELECT COUNT(*) FROM position_events WHERE event_type='CHAIN_QUARANTINED' "
            "OR phase_before='quarantined' OR phase_after='quarantined'"
        ).fetchone()[0]
        remaining += tc.execute(
            "SELECT COUNT(*) FROM position_lots WHERE state='QUARANTINED'"
        ).fetchone()[0]
    finally:
        tc.close()
    return "migrated" if remaining == 0 else "BAD"


@pytest.mark.parametrize("checkpoint", mig.KILL_POINTS)
def test_kill_point_crash_matrix(tmp_path, checkpoint):
    state_dir = tmp_path / "state"
    paths = build_legacy_fixture(state_dir)
    pre_snapshot = _content_snapshot(paths)

    env = dict(os.environ)
    env[mig._KILL_ENV_VAR] = checkpoint
    env[mig._SKIP_PROCESS_CHECK_ENV_VAR] = "1"
    proc = subprocess.run(
        [sys.executable, str(MIGRATION_SCRIPT), "--operator-confirms-fenced",
         "--state-dir", str(state_dir), "--backup-dir", str(tmp_path / "backups")],
        env=env, cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    # os._exit(1) at the kill point — never a clean 0.
    assert proc.returncode != 0, (
        f"checkpoint={checkpoint} did not crash as instructed "
        f"(stdout={proc.stdout!r} stderr={proc.stderr!r})"
    )

    outcome = _classify_recovery(paths, pre_snapshot)
    assert outcome in ("untouched", "migrated"), (
        f"checkpoint={checkpoint} left a MIXED / corrupt state (outcome={outcome})"
    )

    # The startup guard must independently agree this state is safe to boot
    # past (either clean pre-migration or fully migrated, never mixed).
    from src.state.db import assert_schema_epoch_not_mixed, read_schema_epoch

    conns = {
        label: sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        for label, p in (("world", paths.world), ("forecasts", paths.forecasts), ("trade", paths.trade))
    }
    try:
        assert_schema_epoch_not_mixed(
            world_epoch=read_schema_epoch(conns["world"]),
            forecasts_epoch=read_schema_epoch(conns["forecasts"]),
            trade_epoch=read_schema_epoch(conns["trade"]),
        )
    finally:
        for c in conns.values():
            c.close()

    # Idempotency after a crash: re-running to completion must still converge
    # on a fully migrated state with no data loss (whether it resumed from
    # 'untouched' or was already 'migrated').
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
    final_outcome = _classify_recovery(paths, pre_snapshot)
    assert final_outcome == "migrated"
