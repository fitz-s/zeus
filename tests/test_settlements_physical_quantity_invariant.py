# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
#   + INV-14 identity spine (src/types/metric_identity.py)
#   + test_harvester_metric_identity.py:27-30 (residual 1561-row migration owed)
"""Post-migration invariant: every live settlement row must carry a canonical physical_quantity.

These tests are the persistent immune-system antibody for the settlements
physical_quantity drift documented at:
  docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md

Before migration (current state): test_settlements_high_uses_canonical_physical_quantity
FAILS because 1561 rows carry the legacy string "daily_maximum_air_temperature".

After migration (--apply executed): all three tests PASS.

DB-touching tests use sqlite3 URI read-only mode and SKIP gracefully when
state/zeus-world.db does not exist (CI safety).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

def _find_live_db() -> Path | None:
    """Locate zeus-world.db by checking candidate paths in priority order.

    The worktree may carry an empty placeholder at state/zeus-world.db (size=0).
    Fall back to the canonical Zeus workspace path when the local copy is absent
    or uninitialized (no settlements table).
    """
    candidates = [
        PROJECT_ROOT / "state" / "zeus-world.db",
        Path("/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db"),
    ]
    for path in candidates:
        if not path.exists() or path.stat().st_size == 0:
            continue
        # Verify the DB is initialized (has the settlements table)
        try:
            uri = f"file:{path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='settlements'"
            ).fetchone()
            conn.close()
            if row is not None:
                return path
        except Exception:
            continue
    return None


LIVE_DB: Path | None = _find_live_db()


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Open the DB read-only via URI so no WAL writes or locking side-effects occur."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Test 1: no 'high' row carries a non-canonical physical_quantity
# ---------------------------------------------------------------------------

def test_settlements_high_uses_canonical_physical_quantity():
    """Every row with temperature_metric='high' must have physical_quantity=HIGH_LOCALDAY_MAX.physical_quantity.

    Pre-migration: FAILS (1561 rows carry "daily_maximum_air_temperature").
    Post-migration: PASSES.
    SKIP if state/zeus-world.db does not exist.
    """
    if LIVE_DB is None:
        pytest.skip("Live DB not present or not initialized — skipping in CI")

    conn = _open_ro(LIVE_DB)
    try:
        bad_rows = conn.execute(
            """
            SELECT city, target_date, physical_quantity
            FROM settlements
            WHERE temperature_metric = 'high'
              AND physical_quantity != ?
            LIMIT 10
            """,
            (HIGH_LOCALDAY_MAX.physical_quantity,),
        ).fetchall()

        total_bad = conn.execute(
            """
            SELECT COUNT(*) FROM settlements
            WHERE temperature_metric = 'high'
              AND physical_quantity != ?
            """,
            (HIGH_LOCALDAY_MAX.physical_quantity,),
        ).fetchone()[0]
    finally:
        conn.close()

    sample = [(r["city"], r["target_date"], r["physical_quantity"]) for r in bad_rows]
    assert total_bad == 0, (
        f"{total_bad} settlement row(s) have temperature_metric='high' but "
        f"physical_quantity != {HIGH_LOCALDAY_MAX.physical_quantity!r}. "
        f"First 10 offenders: {sample}. "
        f"Run the migration script with --apply to fix: "
        f"docs/operations/task_2026-04-28_settlements_physical_quantity_migration/"
        f"scripts/migrate_settlements_physical_quantity.py"
    )


# ---------------------------------------------------------------------------
# Test 2: no 'low' row carries a non-canonical physical_quantity (vacuous now)
# ---------------------------------------------------------------------------

def test_settlements_low_uses_canonical_physical_quantity_or_absent():
    """Every row with temperature_metric='low' must have physical_quantity=LOW_LOCALDAY_MIN.physical_quantity.

    Currently passes vacuously (no LOW rows exist in live DB).
    When the harvester LOW track is enabled and rows are written, this becomes
    a live gate ensuring canonical identity from day one.
    SKIP if state/zeus-world.db does not exist.
    """
    if LIVE_DB is None:
        pytest.skip("Live DB not present or not initialized — skipping in CI")

    conn = _open_ro(LIVE_DB)
    try:
        total_low = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'low'"
        ).fetchone()[0]

        bad_rows = conn.execute(
            """
            SELECT city, target_date, physical_quantity
            FROM settlements
            WHERE temperature_metric = 'low'
              AND physical_quantity != ?
            LIMIT 10
            """,
            (LOW_LOCALDAY_MIN.physical_quantity,),
        ).fetchall()

        total_bad = conn.execute(
            """
            SELECT COUNT(*) FROM settlements
            WHERE temperature_metric = 'low'
              AND physical_quantity != ?
            """,
            (LOW_LOCALDAY_MIN.physical_quantity,),
        ).fetchone()[0]
    finally:
        conn.close()

    sample = [(r["city"], r["target_date"], r["physical_quantity"]) for r in bad_rows]
    assert total_bad == 0, (
        f"{total_bad} LOW settlement row(s) have physical_quantity "
        f"!= {LOW_LOCALDAY_MIN.physical_quantity!r}. "
        f"total LOW rows={total_low}. First 10 offenders: {sample}."
    )


# ---------------------------------------------------------------------------
# Test 3: canonical string registry sanity (pure import check, no DB)
# ---------------------------------------------------------------------------

def test_canonical_strings_match_registry():
    """HIGH_LOCALDAY_MAX and LOW_LOCALDAY_MIN must have non-empty physical_quantity strings.

    This is a structural guard against accidental deletion or empty-string corruption
    of the canonical MetricIdentity constants. Runs in CI without any DB.
    """
    assert isinstance(HIGH_LOCALDAY_MAX.physical_quantity, str), (
        "HIGH_LOCALDAY_MAX.physical_quantity must be a str"
    )
    assert len(HIGH_LOCALDAY_MAX.physical_quantity) > 0, (
        "HIGH_LOCALDAY_MAX.physical_quantity must not be empty"
    )
    assert HIGH_LOCALDAY_MAX.physical_quantity == "mx2t6_local_calendar_day_max", (
        f"HIGH_LOCALDAY_MAX.physical_quantity has unexpected value: "
        f"{HIGH_LOCALDAY_MAX.physical_quantity!r}"
    )

    assert isinstance(LOW_LOCALDAY_MIN.physical_quantity, str), (
        "LOW_LOCALDAY_MIN.physical_quantity must be a str"
    )
    assert len(LOW_LOCALDAY_MIN.physical_quantity) > 0, (
        "LOW_LOCALDAY_MIN.physical_quantity must not be empty"
    )
    assert LOW_LOCALDAY_MIN.physical_quantity == "mn2t6_local_calendar_day_min", (
        f"LOW_LOCALDAY_MIN.physical_quantity has unexpected value: "
        f"{LOW_LOCALDAY_MIN.physical_quantity!r}"
    )
