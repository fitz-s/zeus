# Lifecycle: created=2026-05-17; last_reviewed=2026-05-18; last_reused=2026-05-18
# Purpose: Tests for scripts.migrations runner framework (F23 + F30).
#   - Double-apply is a no-op (idempotency)
#   - Bootstrap entries are seeded on first table-create
#   - F30: missing last_reviewed= header causes ValueError before apply
# Authority: docs/operations/task_2026-05-17_post_karachi_remediation/FIX_SEV1_BUNDLE.md §F23
"""Tests for the migration runner idempotency and header-drift enforcement."""
import importlib
import sqlite3
import textwrap
from pathlib import Path

import pytest

from scripts.migrations import _BOOTSTRAP_APPLIED, _check_header, apply_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection wired for dict-style row access."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# F23: Idempotency tests
# ---------------------------------------------------------------------------


def test_double_apply_is_noop(tmp_path: Path) -> None:
    """Applying migrations twice must record each name exactly once."""
    # Create a minimal migration module in tmp_path.
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    (mig_dir / "__init__.py").write_text("")
    mig_file = mig_dir / "202600_test_noop.py"
    mig_file.write_text(
        textwrap.dedent(
            """\
            # Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
            def up(conn):
                conn.execute("CREATE TABLE IF NOT EXISTS _mig_test_noop (id INTEGER)")
            """
        )
    )

    conn = _make_conn()

    # Monkey-patch MIGRATIONS_DIR to point at our tmp directory.
    import scripts.migrations as mig_module

    original_dir = mig_module.MIGRATIONS_DIR
    original_bootstrap = mig_module._BOOTSTRAP_APPLIED
    mig_module.MIGRATIONS_DIR = mig_dir
    mig_module._BOOTSTRAP_APPLIED = set()  # clear bootstrap for this test

    try:
        first = apply_migrations(conn)
        second = apply_migrations(conn)  # second run — must be no-op
    finally:
        mig_module.MIGRATIONS_DIR = original_dir
        mig_module._BOOTSTRAP_APPLIED = original_bootstrap

    assert first == ["202600_test_noop"], f"First run did not apply: {first}"
    assert second == [], f"Second run should be empty but got: {second}"

    count = conn.execute(
        "SELECT COUNT(*) FROM _migrations_applied WHERE name='202600_test_noop'"
    ).fetchone()[0]
    assert count == 1, "Migration must appear exactly once in ledger."


def test_ledger_count_matches_scripts(tmp_path: Path) -> None:
    """After full apply, ledger count equals number of scripts in MIGRATIONS_DIR."""
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    (mig_dir / "__init__.py").write_text("")
    for i in range(3):
        (mig_dir / f"202600_test_{i:02d}.py").write_text(
            textwrap.dedent(
                f"""\
                # Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
                def up(conn):
                    conn.execute("CREATE TABLE IF NOT EXISTS _t{i} (id INTEGER)")
                """
            )
        )

    conn = _make_conn()

    import scripts.migrations as mig_module

    original_dir = mig_module.MIGRATIONS_DIR
    original_bootstrap = mig_module._BOOTSTRAP_APPLIED
    mig_module.MIGRATIONS_DIR = mig_dir
    mig_module._BOOTSTRAP_APPLIED = set()

    try:
        apply_migrations(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM _migrations_applied"
        ).fetchone()[0]
        script_count = len(list(mig_dir.glob("2*.py")))
    finally:
        mig_module.MIGRATIONS_DIR = original_dir
        mig_module._BOOTSTRAP_APPLIED = original_bootstrap

    assert count == script_count, (
        f"Ledger has {count} entries but {script_count} scripts exist."
    )


# ---------------------------------------------------------------------------
# F23: Bootstrap seeding
# ---------------------------------------------------------------------------


def test_bootstrap_entries_seeded_on_first_create() -> None:
    """On a fresh DB the bootstrap set must be pre-seeded in _migrations_applied."""
    conn = _make_conn()

    import scripts.migrations as mig_module

    # Run _ensure_ledger directly to test bootstrap logic in isolation.
    mig_module._ensure_ledger(conn)

    seeded = {
        r[0]
        for r in conn.execute("SELECT name FROM _migrations_applied")
    }
    assert _BOOTSTRAP_APPLIED.issubset(seeded), (
        f"Bootstrap entries {_BOOTSTRAP_APPLIED - seeded} not seeded."
    )


def test_bootstrap_not_reseeded_on_subsequent_call() -> None:
    """_ensure_ledger called twice must not duplicate bootstrap entries."""
    conn = _make_conn()

    import scripts.migrations as mig_module

    mig_module._ensure_ledger(conn)
    mig_module._ensure_ledger(conn)  # second call

    for name in _BOOTSTRAP_APPLIED:
        count = conn.execute(
            "SELECT COUNT(*) FROM _migrations_applied WHERE name=?", (name,)
        ).fetchone()[0]
        assert count == 1, f"Bootstrap entry '{name}' duplicated after double ensure_ledger."


# ---------------------------------------------------------------------------
# F30: Header drift enforcement
# ---------------------------------------------------------------------------


def test_check_header_passes_with_last_reviewed(tmp_path: Path) -> None:
    """_check_header must not raise when last_reviewed= is present."""
    f = tmp_path / "202600_ok.py"
    f.write_text("# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17\ndef up(conn): pass\n")
    _check_header(f)  # must not raise


def test_check_header_raises_without_last_reviewed(tmp_path: Path) -> None:
    """_check_header must raise ValueError when last_reviewed= is absent."""
    f = tmp_path / "202600_bad.py"
    f.write_text("# No lifecycle header here\ndef up(conn): pass\n")
    with pytest.raises(ValueError, match="last_reviewed="):
        _check_header(f)


def test_apply_migrations_refuses_missing_header(tmp_path: Path) -> None:
    """apply_migrations must refuse (ValueError) if a migration lacks last_reviewed=."""
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    (mig_dir / "__init__.py").write_text("")
    bad_file = mig_dir / "202600_no_header.py"
    bad_file.write_text("def up(conn): pass\n")  # intentionally missing header

    conn = _make_conn()

    import scripts.migrations as mig_module

    original_dir = mig_module.MIGRATIONS_DIR
    original_bootstrap = mig_module._BOOTSTRAP_APPLIED
    mig_module.MIGRATIONS_DIR = mig_dir
    mig_module._BOOTSTRAP_APPLIED = set()

    try:
        with pytest.raises(ValueError, match="last_reviewed="):
            apply_migrations(conn)
    finally:
        mig_module.MIGRATIONS_DIR = original_dir
        mig_module._BOOTSTRAP_APPLIED = original_bootstrap


def test_apply_migrations_dry_run_does_not_write_ledger(tmp_path: Path) -> None:
    """dry_run=True must not write to _migrations_applied."""
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    (mig_dir / "__init__.py").write_text("")
    (mig_dir / "202600_dry.py").write_text(
        "# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never\n"
        "def up(conn): conn.execute('CREATE TABLE IF NOT EXISTS _dry (id INTEGER)')\n"
    )

    conn = _make_conn()

    import scripts.migrations as mig_module

    original_dir = mig_module.MIGRATIONS_DIR
    original_bootstrap = mig_module._BOOTSTRAP_APPLIED
    mig_module.MIGRATIONS_DIR = mig_dir
    mig_module._BOOTSTRAP_APPLIED = set()

    try:
        result = apply_migrations(conn, dry_run=True)
    finally:
        mig_module.MIGRATIONS_DIR = original_dir
        mig_module._BOOTSTRAP_APPLIED = original_bootstrap

    assert result == ["202600_dry"]
    # dry_run=True must not create or write the _migrations_applied ledger at all
    # (PR #137 fix: previously _ensure_ledger was called unconditionally, committing
    # bootstrap rows before the dry_run branch was reached).
    ledger_exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_migrations_applied'"
    ).fetchone()[0]
    assert ledger_exists == 0, "dry_run must not create _migrations_applied ledger table."
    # Also verify the migration's up() function did NOT run (no _dry table).
    dry_table_exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_dry'"
    ).fetchone()[0]
    assert dry_table_exists == 0, "dry_run must not execute migration up() function."


def test_targeted_world_migration_routes_to_world_connection(tmp_path: Path, monkeypatch) -> None:
    """Runner must open the migration-declared DB, not the legacy trade default."""
    import scripts.migrations.__main__ as cli

    world_path = tmp_path / "zeus-world.db"
    trade_path = tmp_path / "zeus_trades.db"
    calls: list[str] = []

    def world_connection() -> sqlite3.Connection:
        calls.append("world")
        conn = sqlite3.connect(str(world_path))
        conn.row_factory = sqlite3.Row
        return conn

    def trade_connection() -> sqlite3.Connection:
        calls.append("trade")
        conn = sqlite3.connect(str(trade_path))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("src.state.db.get_world_connection", world_connection)
    monkeypatch.setattr("src.state.db.get_trade_connection", trade_connection)

    rc = cli._main(["apply", "--target", "202605_db_chunk_boundary_events"])

    assert rc == 0
    assert calls == ["world"]
    world = sqlite3.connect(str(world_path))
    trade = sqlite3.connect(str(trade_path))
    try:
        assert world.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='db_chunk_boundary_events'"
        ).fetchone() is not None
        assert trade.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='db_chunk_boundary_events'"
        ).fetchone() is None
    finally:
        world.close()
        trade.close()


def test_targeted_migration_requires_matching_db_identity() -> None:
    """TARGET_DB metadata makes wrong-connection migration writes fail closed."""
    conn = _make_conn()

    with pytest.raises(RuntimeError, match="targets 'world', not 'trade'"):
        apply_migrations(
            conn,
            target="202605_db_chunk_boundary_events",
            db_identity="trade",
        )


def test_targeted_migration_requires_identity_for_direct_runner_call() -> None:
    """Direct apply_migrations callers must attest the opened DB identity."""
    conn = _make_conn()

    with pytest.raises(RuntimeError, match="must pass db_identity"):
        apply_migrations(conn, target="202605_db_chunk_boundary_events")


def test_world_migration_ledger_does_not_seed_trade_bootstrap() -> None:
    """WORLD/FORECASTS ledgers must not inherit trade-only pre-ledger markers."""
    conn = _make_conn()

    applied = apply_migrations(
        conn,
        target="202605_db_chunk_boundary_events",
        db_identity="world",
    )
    ledger_names = {
        row[0]
        for row in conn.execute("SELECT name FROM _migrations_applied")
    }

    assert applied == ["202605_db_chunk_boundary_events"]
    assert "202605_db_chunk_boundary_events" in ledger_names
    assert _BOOTSTRAP_APPLIED.isdisjoint(ledger_names)
