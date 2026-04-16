"""Relationship tests for R-E + housekeeping: idempotency, migration safety,
dead-code removal.

Phase: 2 (World DB v2 Schema + DT#1 Commit Ordering + DT#4 Chain Three-State)
R-numbers covered: R-E (apply_v2_schema idempotency), C1 (dead tables absent),
                   C2 (migrate_rainstorm_full deleted + call removed)

These tests MUST FAIL today (2026-04-16) because:
  - src/state/schema/v2_schema.py does not exist (ImportError on schema tests).
  - scripts/migrate_rainstorm_full.py still exists (test 4 fails).
  - src/main.py still calls migrate_rainstorm_full (test 3 fails).

First commit that should turn these green: executor Phase 2 implementation commit
(creates v2_schema.py, deletes migrate_rainstorm_full.py, removes main.py:249 call).
"""
from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEAD_TABLES = [
    "promotion_registry",
    "model_eval_point",
    "model_eval_run",
    # model_skill intentionally excluded: etl_historical_forecasts.py writes to
    # it actively. Cleanup deferred to a later phase (Fix A, fixup pass).
]

# Absolute path anchors — never rely on cwd.
_REPO_ROOT = Path(__file__).parent.parent


def _make_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_and_get_conn() -> sqlite3.Connection:
    """Apply v2 schema to a fresh :memory: DB.

    Raises ImportError today — all callers fail RED until Phase 2 lands.
    """
    from src.state.schema.v2_schema import apply_v2_schema  # noqa: PLC0415
    conn = _make_memory_db()
    apply_v2_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# R-E — Idempotency
# ---------------------------------------------------------------------------

class TestApplyV2SchemaIdempotency(unittest.TestCase):
    """R-E: calling apply_v2_schema twice is a safe no-op."""

    def test_apply_v2_schema_idempotent(self):
        """Call apply_v2_schema(conn) twice on the same :memory: DB; second call
        must not raise; the 4 dead tables must NOT be re-created.

        Uses IF NOT EXISTS / DROP TABLE IF EXISTS DDL pattern to guarantee
        idempotency regardless of execution count.

        Fails today with ImportError.
        """
        from src.state.schema.v2_schema import apply_v2_schema  # noqa: PLC0415
        conn = _make_memory_db()

        # First application
        apply_v2_schema(conn)

        # Second application must not raise
        try:
            apply_v2_schema(conn)
        except Exception as exc:
            self.fail(
                f"apply_v2_schema raised on second call (must be idempotent): {exc!r}"
            )

        # Dead tables must NOT be re-created by the second call
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for dead in DEAD_TABLES:
            self.assertNotIn(
                dead,
                existing,
                msg=(
                    f"Dead table '{dead}' must remain absent after second "
                    "apply_v2_schema call (idempotency violated)"
                ),
            )

    def test_apply_v2_schema_preserves_foreign_keys_setting(self):
        """PRAGMA foreign_keys value before and after apply_v2_schema must match.

        The migration helper disables foreign_keys temporarily (per the DDL sketch
        which opens with PRAGMA foreign_keys = OFF) but must re-enable it on exit
        so the runtime connection is not left in a degraded state.

        Risk called out in phase2_plan.md §10 (WAL + PRAGMA foreign_keys concern).
        Fails today with ImportError.
        """
        from src.state.schema.v2_schema import apply_v2_schema  # noqa: PLC0415
        conn = _make_memory_db()

        # Capture foreign_keys setting before migration
        (fk_before,) = conn.execute("PRAGMA foreign_keys").fetchone()

        apply_v2_schema(conn)

        # Capture after migration
        (fk_after,) = conn.execute("PRAGMA foreign_keys").fetchone()

        self.assertEqual(
            fk_before,
            fk_after,
            (
                f"PRAGMA foreign_keys must be the same before ({fk_before}) "
                f"and after ({fk_after}) apply_v2_schema. "
                "The migration must re-enable foreign_keys if it temporarily disables them."
            ),
        )


# ---------------------------------------------------------------------------
# C2 — Dead code removal (migrate_rainstorm_full)
# ---------------------------------------------------------------------------

class TestMigrateRainstormFullRemoved(unittest.TestCase):
    """C2: migrate_rainstorm_full.py is deleted and its call in main.py is removed."""

    def test_migrate_rainstorm_full_call_removed(self):
        """src/main.py must NOT import or call migrate_rainstorm_full.

        Reads the file content at test time (not at import time) so the assertion
        is always live against the current file on disk.

        Today FAILS because src/main.py still references migrate_rainstorm_full
        at line 249 (checked via src/main.py:249 read during investigation).
        """
        main_path = _REPO_ROOT / "src" / "main.py"
        self.assertTrue(
            main_path.exists(),
            f"src/main.py not found at expected path: {main_path}",
        )
        content = main_path.read_text(encoding="utf-8")
        self.assertNotIn(
            "migrate_rainstorm_full",
            content,
            (
                "src/main.py still references 'migrate_rainstorm_full'. "
                "Phase 2 C2 cleanup requires removing this call (line ~249) "
                "and deleting scripts/migrate_rainstorm_full.py."
            ),
        )

    def test_scripts_migrate_rainstorm_full_absent(self):
        """scripts/migrate_rainstorm_full.py must not exist on disk.

        Phase 2 C2 cleanup deletes this self-reported COMPLETE no-op script.
        Fails today because the file still exists.
        """
        script_path = _REPO_ROOT / "scripts" / "migrate_rainstorm_full.py"
        self.assertFalse(
            script_path.exists(),
            (
                f"scripts/migrate_rainstorm_full.py still exists at {script_path}. "
                "Phase 2 C2 must delete this file (it self-reports COMPLETE and "
                "is called at src/main.py:249 — both must be removed together)."
            ),
        )
