# Lifecycle: created=2026-05-16; last_reviewed=2026-05-16; last_reused=never
# Purpose: Coverage for scripts/migrations/202605_add_redeem_operator_required_state.py
#   — row preservation, CHECK acceptance of new state, FK validity post-rebuild,
#   re-run idempotency, FK violation triggers ROLLBACK, dry-run no-modify,
#   user_version-only path for DBs without settlement_commands (PR #126
#   review-fix from Codex P1 #2).
# Reuse: Run on every PR touching the migration script or SCHEMA_VERSION
#   bump logic. Authority basis: SCAFFOLD_F14_F16.md §K.8 v5 (tests a-f + g/h).

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Script filename starts with digits ("202605_..."), so direct package
# import is impossible. Load via importlib spec instead.
import importlib.util


def _import_migration_module():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = (
        repo_root
        / "scripts"
        / "migrations"
        / "202605_add_redeem_operator_required_state.py"
    )
    spec = importlib.util.spec_from_file_location("_migr_module", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


migration = _import_migration_module()


# v1 (pre-migration) schema — minimal subset, mirrors settlement_commands.py:28-66
V1_SCHEMA = """
CREATE TABLE settlement_commands (
  command_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN (
    'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',
    'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING','REDEEM_REVIEW_REQUIRED'
  )),
  condition_id TEXT NOT NULL,
  market_id TEXT NOT NULL,
  payout_asset TEXT NOT NULL CHECK (payout_asset IN ('pUSD','USDC','USDC_E')),
  pusd_amount_micro INTEGER,
  token_amounts_json TEXT,
  tx_hash TEXT,
  block_number INTEGER,
  confirmation_count INTEGER DEFAULT 0,
  requested_at TEXT NOT NULL,
  submitted_at TEXT,
  terminal_at TEXT,
  error_payload TEXT
);
CREATE INDEX idx_settlement_commands_state ON settlement_commands (state, requested_at);
CREATE INDEX idx_settlement_commands_condition ON settlement_commands (condition_id, market_id);
CREATE UNIQUE INDEX ux_settlement_commands_active_condition_asset
  ON settlement_commands (condition_id, market_id, payout_asset)
  WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');

CREATE TABLE settlement_command_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id TEXT NOT NULL REFERENCES settlement_commands(command_id),
  event_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _seed_v1_db(db_path: Path, with_row: bool = True) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(V1_SCHEMA)
        if with_row:
            conn.execute(
                """
                INSERT INTO settlement_commands
                  (command_id, state, condition_id, market_id, payout_asset,
                   requested_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("cmd-001", "REDEEM_INTENT_CREATED", "c30f28a5", "m1", "USDC_E", "2026-05-16T20:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO settlement_command_events
                  (command_id, event_type, payload_hash, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                ("cmd-001", "REDEEM_INTENT_CREATED", "h", "{}"),
            )
            conn.commit()
    finally:
        conn.close()


def test_a_row_preserved_after_migration(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _seed_v1_db(db, with_row=True)

    outcome = migration._migrate_one_db(db, dry_run=False)
    assert outcome["action"] == "migrated", outcome

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT command_id, state, condition_id FROM settlement_commands WHERE command_id='cmd-001'"
        ).fetchone()
        assert row == ("cmd-001", "REDEEM_INTENT_CREATED", "c30f28a5"), row
    finally:
        conn.close()


def test_b_new_state_accepted_post_migration(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _seed_v1_db(db, with_row=True)
    migration._migrate_one_db(db, dry_run=False)

    conn = sqlite3.connect(str(db))
    try:
        # Should accept the new state now
        conn.execute(
            """
            INSERT INTO settlement_commands
              (command_id, state, condition_id, market_id, payout_asset, requested_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("cmd-002", "REDEEM_OPERATOR_REQUIRED", "c2", "m2", "USDC_E", "2026-05-16T20:01:00+00:00"),
        )
        conn.commit()
        row = conn.execute("SELECT state FROM settlement_commands WHERE command_id='cmd-002'").fetchone()
        assert row[0] == "REDEEM_OPERATOR_REQUIRED"
    finally:
        conn.close()


def test_c_foreign_keys_valid_post_migration(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _seed_v1_db(db, with_row=True)
    migration._migrate_one_db(db, dry_run=False)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        violations = list(conn.execute("PRAGMA foreign_key_check"))
        assert violations == [], f"unexpected FK violations: {violations!r}"
        # And: settlement_command_events.command_id still references the row
        ev = conn.execute(
            "SELECT command_id FROM settlement_command_events WHERE command_id='cmd-001'"
        ).fetchone()
        assert ev is not None
    finally:
        conn.close()


def test_d_re_run_is_no_op(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _seed_v1_db(db, with_row=True)
    first = migration._migrate_one_db(db, dry_run=False)
    assert first["action"] == "migrated"
    second = migration._migrate_one_db(db, dry_run=False)
    assert second["action"] == "no_op_already_applied", second


def test_e_fk_violation_triggers_rollback(tmp_path: Path) -> None:
    """Artificially seed an FK violation (orphan event row) then run migration.

    SQLite v3 allows orphan FK rows when foreign_keys=OFF. After table rebuild,
    the new table has the same PK; the orphan event still references a missing
    row (but the migration only rebuilds settlement_commands, not events).
    Force violation by inserting an orphan AFTER seeding the row that gets
    preserved — then the rebuild keeps the row, FK valid. So instead: insert
    an event with command_id that points to a NON-existent command.
    """
    db = tmp_path / "test.db"
    _seed_v1_db(db, with_row=True)
    conn = sqlite3.connect(str(db))
    try:
        # Insert orphan event row with foreign_keys=OFF to bypass enforcement
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO settlement_command_events (command_id, event_type, payload_hash) "
            "VALUES (?, ?, ?)",
            ("nonexistent-cmd", "BAD", "h"),
        )
        conn.commit()
    finally:
        conn.close()

    outcome = migration._migrate_one_db(db, dry_run=False)
    assert outcome["action"] == "abort_fk_violations", outcome
    # And: original row preserved (rollback should not have lost it)
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT command_id FROM settlement_commands WHERE command_id='cmd-001'").fetchone()
        assert row is not None, "rollback must preserve pre-migration row"
    finally:
        conn.close()


def test_f_dry_run_does_not_modify(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _seed_v1_db(db, with_row=True)
    before = db.read_bytes()
    outcome = migration._migrate_one_db(db, dry_run=True)
    assert outcome["action"] == "dry_run_would_migrate", outcome
    after = db.read_bytes()
    assert before == after, "dry-run must not modify DB bytes"


def test_skip_missing_db(tmp_path: Path) -> None:
    """Per v5 spec: migration on a non-existent DB returns skip outcome, exit 0."""
    db = tmp_path / "not-here.db"
    outcome = migration._migrate_one_db(db, dry_run=False)
    assert outcome["action"] == "skip_missing"


def test_g_user_version_only_for_world_or_forecasts_db(tmp_path: Path) -> None:
    """PR #126 review-fix (Codex P1 #2): a DB without settlement_commands
    (i.e. world.db or forecasts.db) gets user_version bumped, not rebuild.
    """
    db = tmp_path / "world.db"
    # Seed an empty DB with user_version=3 (NO settlement_commands table)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    finally:
        conn.close()

    outcome = migration._migrate_one_db(db, dry_run=False)
    assert outcome["action"] == "user_version_only", outcome

    conn = sqlite3.connect(str(db))
    try:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == 4, f"user_version should be 4 post-bump, got {v}"
    finally:
        conn.close()

    # Re-run is no-op
    outcome2 = migration._migrate_one_db(db, dry_run=False)
    assert outcome2["action"] == "user_version_only"
    assert "no_op_user_version_already_4" in outcome2["details"]


def test_h_existing_migrated_db_bumps_user_version_too(tmp_path: Path) -> None:
    """Edge case: settlement_commands rebuilt but user_version somehow stale.

    Migration on an already-CHECK-current DB still bumps user_version if behind.
    """
    db = tmp_path / "trade.db"
    _seed_v1_db(db, with_row=False)
    migration._migrate_one_db(db, dry_run=False)  # first run: full migrate
    # Force user_version back to 3 (simulating a partial-state scenario)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    finally:
        conn.close()

    outcome = migration._migrate_one_db(db, dry_run=False)
    assert outcome["action"] == "no_op_already_applied", outcome
    assert "user_version_bumped_3_to_4" in outcome["details"], outcome["details"]

    conn = sqlite3.connect(str(db))
    try:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == 4
    finally:
        conn.close()


def test_i_default_targets_exclude_zeus_forecasts_db() -> None:
    """G5c FA3 ship-blocker regression: forecasts DB uses an INDEPENDENT
    sentinel SCHEMA_FORECASTS_VERSION (src/state/db.py:2427) that this
    PR does NOT change. Migration MUST NOT include zeus-forecasts.db in
    its default target list; bumping its user_version to 4 would trigger
    assert_schema_current_forecasts(conn) to raise on next forecast-live
    daemon boot — fatal.

    Operator may still explicitly target it via --db, but the default
    must never include it.
    """
    import sys
    from io import StringIO

    # Inspect the default-target list inline by reading the script source
    # and asserting the absence of zeus-forecasts.db. AST-level enforcement
    # is more robust than substring match against possible refactors.
    import ast
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "migrations"
        / "202605_add_redeem_operator_required_state.py"
    )
    src = script_path.read_text()
    # AST-only check: walk for any string literal "zeus-forecasts.db" that
    # lands as a runtime constant (i.e., NOT a docstring/comment — comments are
    # not in the AST; docstrings ARE Constant nodes but only at module/class/
    # function head as the first statement). We exclude docstring positions
    # by checking the parent context.
    tree = ast.parse(src)
    # Collect all docstring nodes to exclude
    docstring_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                docstring_ids.add(id(node.body[0].value))
    # Now walk for runtime Constant nodes containing the bad literal
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            if "zeus-forecasts.db" in node.value:
                pytest.fail(
                    f"AST-detected RUNTIME literal 'zeus-forecasts.db' in migration "
                    f"script at line {node.lineno}; would bump forecasts user_version "
                    f"and break SCHEMA_FORECASTS_VERSION boot check (G5c FA3)."
                )


def test_j_forecasts_shaped_db_user_version_preserved(tmp_path: Path) -> None:
    """Defensive functional test: if operator runs default migration against
    a checkout that includes a forecasts-shaped DB (no settlement_commands)
    at user_version=3, migration with DEFAULT targets must leave it untouched.

    We simulate by calling main() with default targets pointing at a tmp
    repo_root that contains ONLY a forecasts-shaped DB at the
    state/zeus-forecasts.db location. The default-target list (post-R2)
    does NOT include this path, so migration should skip_missing on all
    other targets and never touch zeus-forecasts.db's user_version.
    """
    fake_repo = tmp_path / "fake-repo"
    state = fake_repo / "state"
    state.mkdir(parents=True)
    # Only forecasts DB exists in this fake repo (sibling DBs missing)
    forecasts = state / "zeus-forecasts.db"
    conn = sqlite3.connect(str(forecasts))
    try:
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    finally:
        conn.close()

    # Run migration with --repo-root pointing at fake-repo (default targets)
    exit_code = migration.main([
        "--repo-root", str(fake_repo),
    ])
    # All targets are missing → exit 0 with skip_missing actions
    assert exit_code == 0, f"unexpected exit_code={exit_code}"

    # Verify forecasts DB user_version is STILL 3 (untouched)
    conn = sqlite3.connect(str(forecasts))
    try:
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == 3, (
            f"forecasts.db user_version must remain at 3 (SCHEMA_FORECASTS_VERSION) "
            f"after default migration; got {v}. R2 ship-blocker regression."
        )
    finally:
        conn.close()
