# Created: 2026-05-11
# Last reused or audited: 2026-05-15
# Authority basis: PLAN docs/operations/task_2026-05-11_init_schema_boot_invariant/PLAN.md §5.5 + §6
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md

import hashlib
import sqlite3
import time
import pytest

from src.state.db import (
    SchemaOutOfDateError,
    assert_schema_current,
    init_schema,
)

# B2 (2026-05-28) + operator directive 2026-06-13: PRAGMA user_version mechanism removed.
# init_schema no longer writes a version counter; schema currency via structural table presence.


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
# REL-2: partial init_schema failure leaves no spurious canonical tables
# ---------------------------------------------------------------------------

def test_rel2_partial_init_failure_leaves_no_canonical_tables(monkeypatch):
    """If apply_canonical_schema raises, no canonical tables should be visible.

    B2 + operator directive 2026-06-13: PRAGMA user_version removed; this REL-2
    now tests the structural invariant: a failed init_schema must not leave the DB
    in a half-initialized state where _check_world_schema could falsely pass.
    """
    import src.state.schema.v2_schema as _v2

    conn = sqlite3.connect(":memory:")

    def _boom(c, **kwargs):
        raise RuntimeError("simulated apply_canonical_schema failure")

    monkeypatch.setattr(_v2, "apply_canonical_schema", _boom)

    with pytest.raises(RuntimeError, match="simulated"):
        init_schema(conn)

    # No tables should be present (the canonical tables come from apply_canonical_schema)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "market_price_history" not in tables, (
        "canonical table market_price_history present after failed init_schema; "
        "partial initialization must not leave canonical tables"
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
# B2: assert_schema_current is now a no-op; test verifies it still completes quickly.
# ---------------------------------------------------------------------------

def test_rel4_assert_schema_current_o1():
    """1000 assert_schema_current calls complete in < 2 s (B2: no-op, trivially fast)."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    t0 = time.perf_counter()
    for _ in range(1000):
        assert_schema_current(conn)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 2000, (
        f"assert_schema_current 1000x took {elapsed_ms:.1f} ms (limit 2000 ms)."
    )


# ---------------------------------------------------------------------------
# REL-5: schema drift detected — sqlite version guard (from §5.6 N3)
# ---------------------------------------------------------------------------

def test_rel5_schema_drift_detected():
    """SQLite version >= 3.37.0 required for strict table support and write-ahead stability."""
    assert sqlite3.sqlite_version_info >= (3, 37, 0), (
        f"SQLite {sqlite3.sqlite_version} < 3.37.0"
    )


# ---------------------------------------------------------------------------
# REL-6: fixed-point — init_schema is structurally idempotent
# ---------------------------------------------------------------------------

def test_rel6_fixed_point():
    """init_schema is a structural fixed-point: sqlite_master hash is stable after 2 calls.

    B2 + operator directive 2026-06-13: PRAGMA user_version removed. REL-6 now verifies
    the structural fixed-point property: init_schema on any DB (fresh or pre-initialized)
    produces the same canonical sqlite_master regardless of call count.
    """
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    h1 = _sqlite_master_hash(conn)
    init_schema(conn)
    h2 = _sqlite_master_hash(conn)
    assert h1 == h2, (
        "sqlite_master changed between first and second init_schema call — "
        "init_schema is not idempotent (structural fixed-point violated)"
    )


# ---------------------------------------------------------------------------
# assert_schema_current is a no-op (B2: counter cancelled)
# ---------------------------------------------------------------------------

def test_assert_schema_current_is_noop():
    """B2: assert_schema_current is a no-op; does not raise regardless of user_version."""
    conn = sqlite3.connect(":memory:")
    # Should not raise even without init_schema (user_version=0)
    assert_schema_current(conn)  # no-op
    conn.execute("PRAGMA user_version = 1")
    assert_schema_current(conn)  # still no-op
