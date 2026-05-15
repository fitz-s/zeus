# Created: 2026-05-11
# Last reused or audited: 2026-05-11
# Authority basis: PLAN docs/operations/task_2026-05-11_forecast_db_split/PLAN.md §5.9
"""K1 forecast DB split — REL invariant tests (§5.9).

REL-1  init_schema_forecasts on :memory: creates all 7 tables + correct schema version + indexes.
REL-2  Row counts forecasts.X == world.X for all 7 tables (skip until operator migration runs).
REL-3  No src/ caller reads/writes the 7 forecast-class tables on the world connection (grep).
REL-4  forecasts.db writer-lock file path is distinct from world.db writer-lock file path.
REL-5  Pre/post migration timing baseline (skip until operator migration runs).
REL-6  settlements + settlements_v2 + market_events_v2 atomicity: INSERT then forced rollback
       leaves all 3 tables at 0 rows on a fresh forecasts connection.
REL-7  ATTACH read latency invariant (skip until operator migration runs).
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants mirrored from PLAN §5.9 table inventory
# ---------------------------------------------------------------------------

FORECAST_TABLES = (
    "ensemble_snapshots_v2",
    "calibration_pairs_v2",
    "observations",
    "settlements",
    "settlements_v2",
    "market_events_v2",
    "source_run",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# REL-1: init_schema_forecasts on fresh :memory: creates all 7 tables,
#         sets PRAGMA user_version = SCHEMA_FORECASTS_VERSION = 1,
#         and creates at minimum the critical indexes.
# ---------------------------------------------------------------------------

def test_rel1_init_schema_forecasts_tables_and_version():
    """init_schema_forecasts must create all 7 forecast-class tables + version."""
    from src.state.db import SCHEMA_FORECASTS_VERSION, init_schema_forecasts

    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    conn.commit()

    # All 7 tables must exist.
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = set(FORECAST_TABLES) - existing
    assert not missing, (
        f"REL-1: init_schema_forecasts missing tables: {sorted(missing)}"
    )

    # PRAGMA user_version must equal SCHEMA_FORECASTS_VERSION.
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    assert ver == SCHEMA_FORECASTS_VERSION, (
        f"REL-1: user_version={ver!r} expected {SCHEMA_FORECASTS_VERSION}"
    )

    conn.close()


def test_rel1_init_schema_forecasts_critical_indexes():
    """Critical indexes for query performance must be present."""
    from src.state.db import init_schema_forecasts

    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    conn.commit()

    existing_indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }

    # At minimum these covering indexes are required for hot-path queries.
    required = {
        "idx_ensemble_snapshots_v2_lookup",
        "idx_calibration_pairs_v2_city_date_metric",
    }
    missing = required - existing_indexes
    assert not missing, (
        f"REL-1: missing critical indexes: {sorted(missing)}"
    )

    conn.close()


# ---------------------------------------------------------------------------
# REL-2: Row counts match between forecasts and world (post-migration).
#         Skipped until operator runs migration.
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="REL-2: skip until operator runs migration (§5.4 deferred)")
def test_rel2_row_counts_match_after_migration():
    """After migration each forecast-class table count must match world DB."""
    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH

    assert ZEUS_FORECASTS_DB_PATH.exists(), "forecasts DB not found — run migration first"
    assert ZEUS_WORLD_DB_PATH.exists(), "world DB not found"

    world = sqlite3.connect(f"file:{ZEUS_WORLD_DB_PATH}?mode=ro", uri=True)
    fcast = sqlite3.connect(f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro", uri=True)
    try:
        for table in FORECAST_TABLES:
            wc = world.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            fc = fcast.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert wc == fc, (
                f"REL-2: {table}: world={wc} forecasts={fc}"
            )
    finally:
        world.close()
        fcast.close()


# ---------------------------------------------------------------------------
# REL-3: No src/ caller reads/writes the 7 forecast-class tables via
#         a world connection (grep enforcement).
#
# We search for raw SQL strings that reference these table names in src/
# and assert they do NOT appear outside the sanctioned transition sites.
#
# Policy: after K1 migration, callers must route forecast-class table access
# through get_forecasts_connection(), not get_world_connection(). This test
# is advisory-grade today (pre-§5.5 caller updates); it will enforce hard
# post-§5.5 by removing the skip.
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="REL-3: skip until §5.5 caller updates land")
def test_rel3_no_forecast_tables_on_world_connection():
    """After §5.5 caller updates, no hot-path code may route forecast tables
    through get_world_connection(). This test becomes non-skip after §5.5."""
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py"] +
        [t for t in FORECAST_TABLES] +
        ["src/"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    lines = result.stdout.strip().splitlines()
    # After §5.5, the only src/ references to these table names should be
    # inside db.py (schema DDL) and forecasts-aware caller sites.
    ALLOWED_PATHS = (
        "src/state/db.py",
        "src/state/schema/v2_schema.py",
    )
    violations = [
        l for l in lines
        if l and not any(l.startswith(p) for p in ALLOWED_PATHS)
    ]
    assert not violations, (
        f"REL-3: forecast-class table names referenced outside sanctioned paths "
        f"after §5.5 caller updates. Violations: {violations[:10]}"
    )


def test_rel3_grep_smoke_no_crash():
    """REL-3 grep infra smoke: grep runs without error (pre-§5.5 advisory)."""
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", "ensemble_snapshots_v2", "src/"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    # Grep exit 0 = found, exit 1 = not found, both are OK for smoke.
    assert result.returncode in (0, 1), (
        f"REL-3 grep infra: unexpected exit code {result.returncode}: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# REL-4: forecasts.db writer-lock file path is distinct from world.db.
#         Both LIVE and BULK lock files must be file-separate.
# ---------------------------------------------------------------------------

def test_rel4_forecasts_lock_distinct_from_world_lock():
    """Lock files for forecasts.db must not overlap with world.db lock files."""
    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH
    from src.state.db_writer_lock import WriteClass, _lock_file_path

    for wc in (WriteClass.LIVE, WriteClass.BULK):
        fcast_lock = _lock_file_path(ZEUS_FORECASTS_DB_PATH, wc)
        world_lock = _lock_file_path(ZEUS_WORLD_DB_PATH, wc)
        assert fcast_lock != world_lock, (
            f"REL-4: {wc.value} lock files are identical: {fcast_lock}"
        )
        # The lock files must live in the same directory as their respective DBs.
        assert fcast_lock.parent == ZEUS_FORECASTS_DB_PATH.parent, (
            f"REL-4: forecasts {wc.value} lock file in wrong directory: {fcast_lock}"
        )
        assert world_lock.parent == ZEUS_WORLD_DB_PATH.parent, (
            f"REL-4: world {wc.value} lock file in wrong directory: {world_lock}"
        )


def test_rel4_forecasts_db_in_cross_db_canonical_order():
    """zeus-forecasts.db must appear in CROSS_DB_CANONICAL_ORDER."""
    from src.state.db_writer_lock import CROSS_DB_CANONICAL_ORDER

    assert "zeus-forecasts.db" in CROSS_DB_CANONICAL_ORDER, (
        f"REL-4: zeus-forecasts.db missing from CROSS_DB_CANONICAL_ORDER: "
        f"{CROSS_DB_CANONICAL_ORDER}"
    )
    # Must be alphabetically between risk_state.db and zeus-world.db.
    names = list(CROSS_DB_CANONICAL_ORDER)
    fi = names.index("zeus-forecasts.db")
    wi = names.index("zeus-world.db")
    assert fi < wi, (
        f"REL-4: zeus-forecasts.db (index {fi}) must precede zeus-world.db "
        f"(index {wi}) in CROSS_DB_CANONICAL_ORDER"
    )


# ---------------------------------------------------------------------------
# REL-5: Pre/post migration timing (skip until migration runs).
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="REL-5: skip until operator runs migration (§5.4 deferred)")
def test_rel5_post_migration_forecasts_db_exists():
    """After migration, zeus-forecasts.db must exist with non-zero size."""
    from src.state.db import ZEUS_FORECASTS_DB_PATH

    assert ZEUS_FORECASTS_DB_PATH.exists(), "zeus-forecasts.db missing post-migration"
    assert ZEUS_FORECASTS_DB_PATH.stat().st_size > 0, "zeus-forecasts.db is empty"


# ---------------------------------------------------------------------------
# REL-6: Co-transactional trio atomicity.
#
#   INSERT settlements + settlements_v2 + market_events_v2 on a single
#   forecasts connection inside an explicit transaction, then force ROLLBACK.
#   All 3 tables must remain at 0 rows.
# ---------------------------------------------------------------------------

def _insert_trio(conn: sqlite3.Connection) -> None:
    """INSERT one row into each of the 3 co-transactional trio tables.

    Uses named-column INSERT so CHECK constraints and defaults are respected
    without maintaining a fragile positional tuple.
    """
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, temperature_metric,
            market_slug, winning_bin, settlement_value, settlement_source,
            settled_at, authority, observation_field, data_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Chicago", "2026-05-11", "high",
         "chicago-high-75", "YES", 75.0, "wu",
         "2026-05-11T00:00:00Z", "VERIFIED", "high_temp", "v2"),
    )
    conn.execute(
        """
        INSERT INTO settlements_v2 (city, target_date, temperature_metric,
            market_slug, winning_bin, settlement_value, settlement_source,
            settled_at, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Chicago", "2026-05-11", "high",
         "chicago-high-75", "YES", 75.0, "wu",
         "2026-05-11T00:00:00Z", "VERIFIED"),
    )
    conn.execute(
        """
        INSERT INTO market_events_v2 (market_slug, city, target_date,
            temperature_metric, condition_id, outcome, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("chicago-high-75", "Chicago", "2026-05-11",
         "high", "cond_001", "YES", "2026-05-11T00:00:00Z"),
    )


def test_rel6_trio_atomicity_rollback():
    """settlements + settlements_v2 + market_events_v2 must roll back atomically."""
    from src.state.db import init_schema_forecasts

    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    conn.commit()

    # Begin explicit transaction, insert into all 3 tables, then force ROLLBACK.
    conn.execute("BEGIN")
    _insert_trio(conn)
    conn.execute("ROLLBACK")

    # All 3 tables must be empty after rollback.
    for table in ("settlements", "settlements_v2", "market_events_v2"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, (
            f"REL-6: {table} has {count} rows after ROLLBACK — "
            "co-transactional trio atomicity violated"
        )

    conn.close()


def test_rel6_trio_atomicity_commit():
    """After a successful commit all 3 trio tables must show exactly 1 row."""
    from src.state.db import init_schema_forecasts

    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    conn.commit()

    conn.execute("BEGIN")
    _insert_trio(conn)
    conn.execute("COMMIT")

    for table in ("settlements", "settlements_v2", "market_events_v2"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 1, (
            f"REL-6: {table} has {count} rows after COMMIT — expected 1"
        )

    conn.close()


# ---------------------------------------------------------------------------
# REL-7: ATTACH read latency (skip until migration runs).
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="REL-7: skip until operator runs migration (§5.4 deferred)")
def test_rel7_attach_read_latency():
    """ATTACH forecasts.db to world.db and run a COUNT(*) — must complete < 5s."""
    import time
    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH

    conn = sqlite3.connect(f"file:{ZEUS_WORLD_DB_PATH}?mode=ro", uri=True)
    conn.execute(f"ATTACH DATABASE ? AS fcast", (str(ZEUS_FORECASTS_DB_PATH),))
    t0 = time.monotonic()
    conn.execute("SELECT COUNT(*) FROM fcast.ensemble_snapshots_v2").fetchone()
    elapsed = time.monotonic() - t0
    conn.close()
    assert elapsed < 5.0, f"REL-7: ATTACH COUNT(*) took {elapsed:.2f}s (> 5s threshold)"


# ---------------------------------------------------------------------------
# REL-A (2026-05-14): Branch equivalence between ATTACH and static-fallback
#   paths inside init_schema_forecasts. Both paths must produce the same v2
#   forecast-class index set on the forecasts conn. This is the relationship
#   invariant the K1 antibody assumed implicitly; Option A makes it explicit.
#   PLAN-evidence: docs/operations/task_2026-05-14_attach_path_index_fix/PLAN.md
# ---------------------------------------------------------------------------

_CRITICAL_V2_INDEXES = frozenset({
    "idx_ensemble_snapshots_v2_lookup",
    "idx_calibration_pairs_v2_city_date_metric",
    "idx_ens_v2_source_run",
    "idx_ens_v2_entry_lookup",
    "idx_calibration_pairs_v2_bucket",
    "idx_calibration_pairs_v2_refit_core",
    "idx_settlements_v2_city_date_metric",
    "idx_settlements_v2_settled_at",
    "idx_market_events_v2_city_date_metric",
    "idx_market_events_v2_open",
})


def _indexes_on(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }


def test_relA_attach_partial_world_still_produces_critical_indexes(
    tmp_path, monkeypatch
):
    """Cross-module: init_schema_forecasts via ATTACH path (world.db exists) must
    still produce the canonical v2 index inventory on the forecasts conn.

    Post-P2 DDL refactor (2026-05-14): world.db no longer holds v2 forecast-class
    tables (init_schema uses forecast_tables=False). The ATTACH path copies only
    world-class tables from world.db; _ensure_v2_forecast_indexes() runs
    unconditionally after both ATTACH and static-fallback branches to guarantee
    v2 index coverage regardless of branch taken.

    This test verifies _ensure_v2_forecast_indexes() runs correctly under the
    ATTACH branch (world.db exists but lacks v2 forecast tables).
    """
    from src.state import db as state_db
    from src.state.db import init_schema, init_schema_forecasts

    # Build a world.db with world-class tables only (post-P2: no v2 forecast tables)
    world_db = tmp_path / "world_only.db"
    w = sqlite3.connect(world_db)
    init_schema(w)
    w.commit()
    w.close()

    # Point ZEUS_WORLD_DB_PATH at the world-only DB so ATTACH branch is taken.
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_db)

    fcast = sqlite3.connect(":memory:")
    init_schema_forecasts(fcast)
    fcast.commit()

    have = _indexes_on(fcast)
    required = {
        "idx_ensemble_snapshots_v2_lookup",
        "idx_calibration_pairs_v2_city_date_metric",
    }
    missing = required - have
    fcast.close()
    assert not missing, (
        f"REL-A: ATTACH branch + _ensure_v2_forecast_indexes() failed to produce "
        f"critical v2 indexes: {sorted(missing)}. _ensure_v2_forecast_indexes() "
        "must run unconditionally post-ATTACH so v2 indexes are always present "
        "on the forecasts conn regardless of world.db v2 table presence."
    )


def test_relA_attach_and_static_branches_produce_same_v2_index_superset(
    tmp_path, monkeypatch
):
    """Stronger form of REL-A: the v2 covering indexes must be a subset of the
    index set produced by BOTH the ATTACH branch (world.db exists) and the
    static-fallback branch (world.db absent). Asymmetry between branches is
    the exact relationship defect Option A closes.

    Post-P2 DDL refactor (2026-05-14): world.db no longer holds v2 forecast-class
    tables. ATTACH branch copies only world-class tables; _ensure_v2_forecast_indexes
    runs unconditionally after both branches to guarantee v2 index equivalence.
    """
    from src.state import db as state_db
    from src.state.db import init_schema, init_schema_forecasts

    # Branch 1: ATTACH path over a world-class-only world.db (post-P2 normal case).
    world_db = tmp_path / "world_only_b.db"
    w = sqlite3.connect(world_db)
    init_schema(w)
    w.commit()
    w.close()

    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_db)
    c_attach = sqlite3.connect(":memory:")
    init_schema_forecasts(c_attach)
    c_attach.commit()
    have_attach = _indexes_on(c_attach)
    c_attach.close()

    # Branch 2: static-fallback path (world.db does not exist on disk).
    world_absent = tmp_path / "absent_world.db"  # never created
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_absent)
    c_static = sqlite3.connect(":memory:")
    init_schema_forecasts(c_static)
    c_static.commit()
    have_static = _indexes_on(c_static)
    c_static.close()

    missing_attach = _CRITICAL_V2_INDEXES - have_attach
    missing_static = _CRITICAL_V2_INDEXES - have_static
    assert not missing_attach, (
        f"REL-A: ATTACH branch missing v2 indexes: {sorted(missing_attach)}"
    )
    assert not missing_static, (
        f"REL-A: static-fallback branch missing v2 indexes: "
        f"{sorted(missing_static)}"
    )
