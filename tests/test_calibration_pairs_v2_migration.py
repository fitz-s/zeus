# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.3, migration_dry_runs.json
"""R-4.3: Migration fixture tests — NOT NULL rebuild/trigger on calibration_pairs_v2.

SCAFFOLD — test bodies raise NotImplementedError, decorated with xfail(strict=False).
Fixtures return usable paths so pytest collection succeeds; bodies carry the xfail.
Activate (remove xfail + implement body) in PR 4 implementation phase.

Two migration modes tested (see --mode flag on migrate script):
    --mode=trigger  (default, disk-safe): BEFORE INSERT + BEFORE UPDATE triggers
    --mode=rebuild  (canonical NOT NULL): CREATE TABLE_new + INSERT + DROP + RENAME

Test plan:
    T1: Dry-run on in-memory fixture DB with 0 NULLs exits 0 and emits plan.
    T2: Dry-run on in-memory fixture DB with NULL rows exits 0, reports BLOCKED.
    T3: --apply --mode=trigger on clean fixture: triggers exist, NULL insert blocked.
    T4: --apply --mode=rebuild on clean fixture: schema shows notnull=1.
    T5: --apply on NULL-containing fixture must fail (IntegrityError or exit 1).
    T6: --require-free-disk-gib guard: mock insufficient disk → exits 1.
    T7: SAVEPOINT rollback: simulate mid-rebuild failure → table row count unchanged.
    T8: Row count invariant: post-rebuild row count == pre-rebuild count.
    T9: Trigger-mode: NULL INSERT blocked; non-NULL INSERT succeeds.
    T10: Trigger-mode: UPDATE to NULL blocked; UPDATE to non-NULL succeeds.
"""

import sqlite3

import pytest


@pytest.fixture
def calibration_pairs_db_path_clean(tmp_path):
    """SQLite DB at tmp_path with calibration_pairs_v2, zero NULL decision_group_id.

    Returns a Path to the DB file. Fixture creates a minimal schema with one row.
    Raises nothing — body-raises are in the test functions.
    """
    db = tmp_path / "fixture_clean.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE calibration_pairs_v2 (
            id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            decision_group_id TEXT
        )
    """)
    conn.execute(
        "INSERT INTO calibration_pairs_v2 (city, decision_group_id) VALUES (?, ?)",
        ("Chicago", "dgid_v1_test000"),
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def calibration_pairs_db_path_with_nulls(tmp_path):
    """SQLite DB at tmp_path with calibration_pairs_v2 containing NULL rows.

    Returns a Path to the DB file.
    """
    db = tmp_path / "fixture_nulls.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE calibration_pairs_v2 (
            id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            decision_group_id TEXT
        )
    """)
    conn.execute(
        "INSERT INTO calibration_pairs_v2 (city, decision_group_id) VALUES (?, ?)",
        ("Chicago", None),
    )
    conn.commit()
    conn.close()
    return db


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_dry_run_clean_fixture_exits_0(calibration_pairs_db_path_clean):
    """Dry-run on clean fixture emits plan and exits 0."""
    raise NotImplementedError("T1: SCAFFOLD — implement in PR 4 production phase")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_dry_run_null_fixture_exits_0_with_blocked_status(calibration_pairs_db_path_with_nulls):
    """Dry-run on NULL-containing fixture exits 0, reports BLOCKED in plan."""
    raise NotImplementedError("T2: SCAFFOLD — implement in PR 4 production phase")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_apply_trigger_mode_clean_fixture_blocks_null_insert(calibration_pairs_db_path_clean):
    """--apply --mode=trigger: NULL INSERT raises IntegrityError after trigger creation."""
    raise NotImplementedError("T3: SCAFFOLD — implement in PR 4 production phase")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_apply_rebuild_mode_clean_fixture_shows_not_null(calibration_pairs_db_path_clean):
    """--apply --mode=rebuild: PRAGMA table_info shows notnull=1 on decision_group_id."""
    raise NotImplementedError("T4: SCAFFOLD — implement in PR 4 production phase")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_apply_null_fixture_fails(calibration_pairs_db_path_with_nulls):
    """--apply on NULL-containing fixture must fail (IntegrityError or exit 1)."""
    raise NotImplementedError("T5: SCAFFOLD — implement in PR 4 production phase")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_disk_guard_blocks_on_insufficient_free_space(
    calibration_pairs_db_path_clean, monkeypatch
):
    """Mock disk_usage to return insufficient free space → exits 1."""
    raise NotImplementedError("T6: SCAFFOLD — implement in PR 4 production phase")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_savepoint_rollback_on_mid_rebuild_failure(
    calibration_pairs_db_path_clean, monkeypatch
):
    """Simulated mid-rebuild failure triggers SAVEPOINT rollback; row count unchanged."""
    raise NotImplementedError("T7: SCAFFOLD — implement in PR 4 production phase")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_row_count_invariant_after_rebuild(calibration_pairs_db_path_clean):
    """Post-rebuild row count == pre-rebuild count."""
    raise NotImplementedError("T8: SCAFFOLD — implement in PR 4 production phase")


def test_trigger_mode_blocks_null_insert_on_synthetic_fixture(tmp_path):
    """LIVE: trigger pattern blocks NULL INSERT on a synthetic fixture DB.

    Validates the trigger SQL template that migrate_calibration_pairs_v2_not_null.py
    will emit in --mode=trigger. Exercises the actual SQLite trigger mechanism
    so the production implementation has a verified template to follow.
    """
    db = tmp_path / "trigger_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE calibration_pairs_v2 (
            id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            decision_group_id TEXT
        )
    """)
    # Apply the trigger template from the SCAFFOLD design
    conn.execute("""
        CREATE TRIGGER calibration_pairs_v2_dgid_not_null_ins
        BEFORE INSERT ON calibration_pairs_v2
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: calibration_pairs_v2.decision_group_id');
        END
    """)
    conn.execute("""
        CREATE TRIGGER calibration_pairs_v2_dgid_not_null_upd
        BEFORE UPDATE OF decision_group_id ON calibration_pairs_v2
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: calibration_pairs_v2.decision_group_id');
        END
    """)
    conn.commit()

    # NULL INSERT must be blocked
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO calibration_pairs_v2 (city, decision_group_id) VALUES (?, ?)",
            ("Chicago", None),
        )

    # Non-NULL INSERT must succeed
    conn.execute(
        "INSERT INTO calibration_pairs_v2 (city, decision_group_id) VALUES (?, ?)",
        ("Chicago", "dgid_v1_abc123"),
    )
    conn.commit()

    # UPDATE to NULL must be blocked
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE calibration_pairs_v2 SET decision_group_id = NULL WHERE city = ?",
            ("Chicago",),
        )

    # UPDATE to non-NULL must succeed
    conn.execute(
        "UPDATE calibration_pairs_v2 SET decision_group_id = ? WHERE city = ?",
        ("dgid_v1_xyz789", "Chicago"),
    )
    conn.commit()

    # Verify final state
    row = conn.execute(
        "SELECT decision_group_id FROM calibration_pairs_v2 WHERE city = ?", ("Chicago",)
    ).fetchone()
    assert row[0] == "dgid_v1_xyz789"
    conn.close()


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_trigger_mode_null_update_blocked_via_migrate_script(calibration_pairs_db_path_clean):
    """--apply --mode=trigger: UPDATE to NULL raises IntegrityError after trigger creation."""
    raise NotImplementedError("T10: SCAFFOLD — implement in PR 4 production phase")
