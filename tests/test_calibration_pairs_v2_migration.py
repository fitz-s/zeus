# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.3, migration_dry_runs.json
"""R-4.3: Migration fixture tests — NOT NULL rebuild on calibration_pairs_v2.

SCAFFOLD — test bodies marked xfail(strict=False, reason="SCAFFOLD").
Activate in PR 4 implementation phase once migration scripts are implemented.

Test plan:
    T1: Dry-run on in-memory fixture DB with 0 NULLs exits 0 and emits plan.
    T2: Dry-run on in-memory fixture DB with NULL rows exits 0 (dry-run never
        errors; it reports blocked status in plan output).
    T3: --apply on fixture with 0 NULLs succeeds; new schema has NOT NULL.
    T4: --apply on fixture with NULL rows raises IntegrityError or exits 1.
    T5: --require-free-disk-gib guard: mock insufficient disk → exits 1.
    T6: SAVEPOINT rollback: simulate mid-migration OS failure → table restored.
    T7: Row count invariant: post-migration row count == pre-migration count.
    T8: Schema invariant: post-migration PRAGMA table_info shows NOT NULL.
"""

import pytest


@pytest.fixture
def calibration_pairs_fixture_clean():
    """In-memory SQLite DB with calibration_pairs_v2, 0 NULL decision_group_id.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("fixture: SCAFFOLD only")


@pytest.fixture
def calibration_pairs_fixture_with_nulls():
    """In-memory SQLite DB with calibration_pairs_v2 containing NULL rows.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("fixture: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_dry_run_clean_fixture_exits_0(calibration_pairs_fixture_clean):
    """Dry-run on clean fixture emits plan and exits 0.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T1: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_dry_run_null_fixture_exits_0_with_blocked_status(calibration_pairs_fixture_with_nulls):
    """Dry-run on NULL-containing fixture exits 0, reports BLOCKED in plan.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T2: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_apply_clean_fixture_adds_not_null(calibration_pairs_fixture_clean):
    """--apply on clean fixture: new schema has NOT NULL on decision_group_id.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T3: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_apply_null_fixture_fails(calibration_pairs_fixture_with_nulls):
    """--apply on NULL-containing fixture must fail (exit 1 or IntegrityError).

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T4: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_disk_guard_blocks_on_insufficient_free_space(calibration_pairs_fixture_clean, monkeypatch):
    """Mock disk_usage to return insufficient free space → exits 1.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T5: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_savepoint_rollback_on_mid_migration_failure(calibration_pairs_fixture_clean, monkeypatch):
    """Simulated mid-migration failure triggers SAVEPOINT rollback.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T6: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_row_count_invariant_after_migration(calibration_pairs_fixture_clean):
    """Post-migration row count == pre-migration count.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T7: SCAFFOLD only")


@pytest.mark.xfail(strict=False, reason="SCAFFOLD: migration script not yet implemented")
def test_schema_pragma_shows_not_null_after_migration(calibration_pairs_fixture_clean):
    """PRAGMA table_info on post-migration table shows notnull=1.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("T8: SCAFFOLD only")
