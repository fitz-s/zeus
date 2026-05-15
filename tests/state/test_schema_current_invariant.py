# Created: 2026-05-11
# Last reused or audited: 2026-05-15
# Authority basis: PLAN docs/operations/task_2026-05-11_init_schema_boot_invariant/PLAN.md §5.5 + §6
#                  + docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md

import hashlib
import sqlite3
import time
import pytest

from src.state.db import (
    SCHEMA_VERSION,
    SchemaOutOfDateError,
    assert_schema_current,
    init_schema,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _sqlite_master_hash(conn: sqlite3.Connection) -> str:
    rows = sorted(
        conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    )
    return hashlib.sha256(repr(rows).encode()).hexdigest()


# ---------------------------------------------------------------------------
# REL-1: no hot-path init_schema( calls outside src/main.py + src/state/db.py
# ---------------------------------------------------------------------------

def test_rel1_no_hot_path_init_schema():
    """init_schema( must only appear in boot paths and src/state/db.py (def).

    Boot paths (allowed):
      - src/main.py        — trade daemon boot, owns trade DB init
      - src/ingest_main.py — ingest daemon boot, owns world DB init (task #6
                             removed it from src/main.py:679-683, so this is
                             the SOLE world-DB upgrade caller)
    Hot-path callers must use assert_schema_current(conn) instead.
    """
    import subprocess
    from pathlib import Path
    _repo_root = str(Path(__file__).parent.parent.parent)
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", "init_schema(", "src/"],
        capture_output=True,
        text=True,
        cwd=_repo_root,
    )
    lines = [l for l in result.stdout.strip().splitlines() if l]
    ALLOWED = ("src/main.py", "src/ingest_main.py", "src/state/db.py")
    for line in lines:
        path = line.split(":")[0]
        assert path in ALLOWED, (
            f"Unexpected init_schema( call site: {line}\n"
            f"Hot-path callers must use assert_schema_current(conn) instead.\n"
            f"Allowed boot-path sites: {ALLOWED}"
        )


# ---------------------------------------------------------------------------
# REL-2: PRAGMA user_version unchanged if init_schema raises before §5.2 anchor
# ---------------------------------------------------------------------------

def test_rel2_pragma_unchanged_on_partial_init_failure(monkeypatch):
    """If _apply_v2_schema raises, user_version must remain 0 (PRAGMA not yet set)."""
    import src.state.db as _db

    conn = sqlite3.connect(":memory:")

    def _boom(c, **kwargs):
        raise RuntimeError("simulated _apply_v2_schema failure")

    monkeypatch.setattr(_db, "_apply_v2_schema", _boom, raising=False)
    # _apply_v2_schema is imported locally inside init_schema — patch via module attr
    # The local import aliases it; we need to patch the module it's imported FROM.
    # Patch at the schema.v2_schema level instead.
    import src.state.schema.v2_schema as _v2
    monkeypatch.setattr(_v2, "apply_v2_schema", _boom)

    with pytest.raises(RuntimeError, match="simulated"):
        init_schema(conn)

    v = conn.execute("PRAGMA user_version").fetchone()[0]
    assert v == 0, (
        f"PRAGMA user_version={v} after failed init_schema; expected 0. "
        "PRAGMA write must be placed AFTER _apply_v2_schema (§5.2 anchor)."
    )


# ---------------------------------------------------------------------------
# REL-3a: fresh :memory: idempotency
# ---------------------------------------------------------------------------

def test_rel3a_fresh_idempotent():
    """init_schema on a fresh :memory: DB is idempotent (sqlite_master hash stable)."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    h1 = _sqlite_master_hash(conn)
    init_schema(conn)
    h2 = _sqlite_master_hash(conn)
    assert h1 == h2, "sqlite_master changed between first and second init_schema call"


def test_rel3a_position_current_metric_column_existing_rows_idempotent():
    """init_schema must be a fixed point once temperature_metric already exists.

    Live boot re-runs init_schema against DBs with active/pending positions.
    The Phase 5A zero-data guard applies only when the column is missing.
    """
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, strategy_key, updated_at, temperature_metric
        ) VALUES (
            'pending-live-row', 'pending_entry', 'pending-live-row',
            'opening_inertia', '2026-05-15T00:00:00+00:00', 'high'
        )
        """
    )
    conn.commit()

    init_schema(conn)

    row = conn.execute(
        "SELECT phase, temperature_metric FROM position_current WHERE position_id = ?",
        ("pending-live-row",),
    ).fetchone()
    assert row == ("pending_entry", "high")


# ---------------------------------------------------------------------------
# REL-3b: legacy pre-REOPEN-2 settlements idempotency
# ---------------------------------------------------------------------------

def test_rel3b_legacy_idempotent_post_reopen2():
    """init_schema on a DB with pre-REOPEN-2 settlements table is idempotent.

    Seeds the legacy schema (UNIQUE(city, target_date) only, per db.py:1973-1974)
    then runs init_schema twice; covers REOPEN-2 rebuild + 4 DROP TRIGGER cycles.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE settlements ("
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    city TEXT NOT NULL,"
        "    target_date TEXT NOT NULL,"
        "    UNIQUE(city, target_date)"
        ");"
    )
    init_schema(conn)
    h1 = _sqlite_master_hash(conn)
    init_schema(conn)
    h2 = _sqlite_master_hash(conn)
    assert h1 == h2, (
        "sqlite_master changed between first and second init_schema on legacy-settlements DB. "
        "REOPEN-2 rebuild or DROP TRIGGER cycle is not idempotent."
    )


# ---------------------------------------------------------------------------
# REL-4: assert_schema_current is O(1) — 1000 calls < 2 s
# ---------------------------------------------------------------------------

def test_rel4_assert_schema_current_o1():
    """1000 assert_schema_current calls complete in < 2 s."""
    assert sqlite3.sqlite_version_info >= (3, 37, 0), (
        f"SQLite {sqlite3.sqlite_version} < 3.37.0; PRAGMA user_version page-1 "
        "guarantee may not hold."
    )
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    t0 = time.perf_counter()
    for _ in range(1000):
        assert_schema_current(conn)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 2000, (
        f"assert_schema_current 1000x took {elapsed_ms:.1f} ms (limit 2000 ms). "
        "PRAGMA user_version must be O(1) page-1 read."
    )


# ---------------------------------------------------------------------------
# REL-5: schema drift detected — sqlite version guard (from §5.6 N3)
# ---------------------------------------------------------------------------

def test_rel5_schema_drift_detected():
    """SQLite version >= 3.37.0 (PRAGMA user_version page-1 guarantee)."""
    assert sqlite3.sqlite_version_info >= (3, 37, 0), (
        f"SQLite {sqlite3.sqlite_version} < 3.37.0"
    )


# ---------------------------------------------------------------------------
# REL-6: fixed-point — init_schema(any_prior_user_version) → SCHEMA_VERSION
# ---------------------------------------------------------------------------

def test_rel6_fixed_point():
    """init_schema always writes SCHEMA_VERSION regardless of prior user_version."""
    prior_versions = {0, max(0, SCHEMA_VERSION - 1), SCHEMA_VERSION, SCHEMA_VERSION + 1}
    for prior_uv in sorted(prior_versions):
        conn = sqlite3.connect(":memory:")
        conn.execute(f"PRAGMA user_version = {prior_uv}")
        init_schema(conn)
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == SCHEMA_VERSION, (
            f"After init_schema with prior user_version={prior_uv}: "
            f"got user_version={v}, expected SCHEMA_VERSION={SCHEMA_VERSION}"
        )


# ---------------------------------------------------------------------------
# assert_schema_current raises on mismatch
# ---------------------------------------------------------------------------

def test_assert_raises_on_mismatch():
    """assert_schema_current raises SchemaOutOfDateError when user_version != SCHEMA_VERSION."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    with pytest.raises(SchemaOutOfDateError):
        assert_schema_current(conn)
