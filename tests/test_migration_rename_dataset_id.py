# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: Stage-C blocker (#26 / #21 Gap #2). TDD for
#   scripts/migrations/202605_rename_ensemble_snapshots_data_version_to_dataset_id.py —
#   bridges the LIVE forecasts DB (ensemble_snapshots.data_version) up to the canonical
#   B5 shape (ensemble_snapshots.dataset_id) without altering v2_schema or the pinned
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Relationship test asserting the data_version->dataset_id rename migration leaves a DB indistinguishable from a freshly-initialized canonical forecasts DB.
# Reuse: Run after any change to the rename migration or init_schema_forecasts ensemble_snapshots DDL.
#   fingerprint.
"""Relationship test for the ensemble_snapshots data_version→dataset_id rename migration.

The cross-module invariant under test: after the migration runs against a DB carrying the
PRE-rename LIVE shape (column `data_version`), the table must be INDISTINGUISHABLE from a
freshly-initialised canonical ensemble_snapshots (column `dataset_id`, same column set, same
UNIQUE, working indexes) AND every row's value must survive the rename unchanged. This is
what lets the daemon's schema-readiness / fingerprint check pass on the live DB post-merge.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

_ZEUS_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZEUS_ROOT))

from src.state.db import init_schema_forecasts  # noqa: E402

# Import the migration module under test by file location (its stem starts with a digit
# so it is not a normal importable module name).
import importlib.util  # noqa: E402

_MIG_PATH = (
    _ZEUS_ROOT
    / "scripts"
    / "migrations"
    / "202605_rename_ensemble_snapshots_data_version_to_dataset_id.py"
)
_spec = importlib.util.spec_from_file_location("rename_dataset_id_migration", _MIG_PATH)
assert _spec is not None and _spec.loader is not None
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)  # type: ignore[union-attr]


_TABLE = "ensemble_snapshots"


def _canonical_columns() -> set[str]:
    ref = sqlite3.connect(":memory:")
    try:
        init_schema_forecasts(ref)
        return {r[1] for r in ref.execute(f"PRAGMA table_info({_TABLE})")}
    finally:
        ref.close()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({_TABLE})")}


def _make_pre_rename_db() -> sqlite3.Connection:
    """Build a DB whose ensemble_snapshots carries the PRE-rename shape.

    Strategy: init the CANONICAL forecasts schema (which yields `dataset_id`), then
    REVERSE-rename dataset_id → data_version. This guarantees the synthetic table is
    byte-identical to canonical except for the single renamed column — exactly the LIVE
    pre-B5 shape — and stays correct if the canonical column set evolves.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    # Sanity: a fresh canonical DB has dataset_id, not data_version.
    cols = _columns(conn)
    assert "dataset_id" in cols and "data_version" not in cols
    conn.execute(
        f"ALTER TABLE {_TABLE} RENAME COLUMN dataset_id TO data_version"
    )
    cols = _columns(conn)
    assert "data_version" in cols and "dataset_id" not in cols
    return conn


def _insert_rows(conn: sqlite3.Connection) -> list[tuple]:
    """Insert a few rows under the pre-rename column name; return the natural keys."""
    rows = [
        # (city, target_date, temperature_metric, issue_time, data_version)
        ("London", "2026-05-10", "high", "2026-05-08T00:00:00Z", "tigge_step120_v1_high"),
        ("London", "2026-05-10", "high", "2026-05-08T12:00:00Z", "tigge_step120_v1_high"),
        ("Tokyo", "2026-05-11", "low", "2026-05-09T00:00:00Z", "ecmwf_opendata_v1_low"),
    ]
    for city, td, metric, issue, dv in rows:
        conn.execute(
            f"""
            INSERT INTO {_TABLE}
                (city, target_date, temperature_metric, physical_quantity,
                 observation_field, available_at, fetch_time, lead_hours,
                 members_json, model_version, data_version, issue_time)
            VALUES (?, ?, ?, 'temperature',
                    ?, ?, ?, 72.0,
                    '[10.0, 11.0]', 'test-model', ?, ?)
            """,
            (
                city,
                td,
                metric,
                "high_temp" if metric == "high" else "low_temp",
                issue,  # available_at (reuse issue for the fixture)
                issue,  # fetch_time
                dv,
                issue,
            ),
        )
    conn.commit()
    return rows


def test_rename_brings_pre_rename_db_to_canonical_shape() -> None:
    """RED-first: pre-rename DB has data_version; after up() it is canonical (dataset_id)."""
    conn = _make_pre_rename_db()
    rows = _insert_rows(conn)
    pre_count = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
    assert pre_count == len(rows)

    # Pre-condition: the table carries the OLD column, not the new one.
    cols_before = _columns(conn)
    assert "data_version" in cols_before
    assert "dataset_id" not in cols_before

    mig.up(conn)
    conn.commit()

    cols_after = _columns(conn)
    # dataset_id present, data_version gone.
    assert "dataset_id" in cols_after, "rename did not introduce dataset_id"
    assert "data_version" not in cols_after, "rename left data_version behind"

    # Column set EXACTLY matches a freshly-initialised canonical table.
    assert cols_after == _canonical_columns(), (
        "post-rename column set diverges from canonical init_schema_forecasts"
    )

    # Rows preserved, values intact (read back under the NEW column name).
    post_count = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
    assert post_count == pre_count, "rename lost rows"
    read = conn.execute(
        f"SELECT city, target_date, temperature_metric, issue_time, dataset_id "
        f"FROM {_TABLE} ORDER BY city, issue_time"
    ).fetchall()
    got = {(r["city"], r["target_date"], r["temperature_metric"], r["issue_time"], r["dataset_id"]) for r in read}
    expect = {(c, td, m, i, dv) for (c, td, m, i, dv) in rows}
    assert got == expect, "row values mutated by the rename"

    conn.close()


def test_rename_preserves_unique_constraint_on_dataset_id() -> None:
    """The canonical UNIQUE(city,target_date,temperature_metric,issue_time,dataset_id)
    must be live on the renamed column — a duplicate natural key is refused."""
    conn = _make_pre_rename_db()
    _insert_rows(conn)
    mig.up(conn)
    conn.commit()

    # The canonical UNIQUE includes dataset_id; inserting a row with an existing
    # (city,target_date,metric,issue_time,dataset_id) tuple must raise IntegrityError.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"""
            INSERT INTO {_TABLE}
                (city, target_date, temperature_metric, physical_quantity,
                 observation_field, available_at, fetch_time, lead_hours,
                 members_json, model_version, dataset_id, issue_time)
            VALUES ('London', '2026-05-10', 'high', 'temperature',
                    'high_temp', 'x', 'x', 72.0,
                    '[1.0]', 'm', 'tigge_step120_v1_high', '2026-05-08T00:00:00Z')
            """
        )
    conn.close()


def test_rename_preserves_dataset_id_index() -> None:
    """idx_ens_v2_entry_lookup (names dataset_id) must survive the rename and reference
    the renamed column — SQLite ≥3.25 RENAME COLUMN auto-rewrites it."""
    conn = _make_pre_rename_db()
    mig.up(conn)
    conn.commit()

    # The canonical index that references dataset_id must exist and its DDL must now
    # name dataset_id (not data_version).
    idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_ens_v2_entry_lookup'"
    ).fetchone()
    assert idx is not None, "idx_ens_v2_entry_lookup missing after rename"
    assert "dataset_id" in idx[0], "index still references the old column name"
    assert "data_version" not in idx[0], "index DDL still names data_version"
    conn.close()


def test_idempotent_rerun_is_noop() -> None:
    """Re-running up() after a successful apply changes nothing."""
    conn = _make_pre_rename_db()
    _insert_rows(conn)
    mig.up(conn)
    conn.commit()
    cols_once = _columns(conn)
    count_once = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]

    # Second call: no-op (dataset_id present, data_version absent).
    mig.up(conn)
    conn.commit()
    assert _columns(conn) == cols_once
    assert conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0] == count_once
    conn.close()


def test_fresh_canonical_db_is_noop() -> None:
    """Running on a fresh canonical DB (already dataset_id) is a no-op, not an error."""
    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    cols_before = _columns(conn)
    assert "dataset_id" in cols_before and "data_version" not in cols_before

    mig.up(conn)  # must not raise
    conn.commit()
    assert _columns(conn) == cols_before
    conn.close()


def test_both_columns_present_raises() -> None:
    """Unexpected state (BOTH data_version and dataset_id) → raise, never guess."""
    conn = _make_pre_rename_db()
    # Add dataset_id alongside data_version → ambiguous state.
    conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN dataset_id TEXT")
    with pytest.raises(AssertionError):
        mig.up(conn)
    conn.close()


def test_drift_beyond_rename_is_flagged_not_silently_rebuilt() -> None:
    """If the live-shaped table has OTHER drift beyond the single rename, up() must FLAG
    (raise) after the rename rather than silently leave a divergent shape.

    We add an EXTRA column the canonical table does not have; after the rename the column
    set will not equal canonical, so the drift guard must fire and roll back."""
    conn = _make_pre_rename_db()
    conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN _bogus_drift_col TEXT")
    conn.commit()
    with pytest.raises(AssertionError, match="drift beyond the single"):
        mig.up(conn)
    # Rolled back: the rename did NOT partially apply (data_version still present,
    # dataset_id absent) because the guard raised inside the SAVEPOINT.
    cols = _columns(conn)
    assert "data_version" in cols and "dataset_id" not in cols, (
        "drift-guard failure must roll the rename back, not leave a half-applied state"
    )
    conn.close()


def test_compute_receipts_classifies_state() -> None:
    """compute_receipts reports the PRE_RENAME_LIVE_SHAPE state and a clean-rename verdict."""
    conn = _make_pre_rename_db()
    _insert_rows(conn)
    receipts = mig.compute_receipts(conn)
    assert receipts["state"] == "PRE_RENAME_LIVE_SHAPE"
    assert receipts["has_data_version"] is True
    assert receipts["has_dataset_id"] is False
    assert receipts["clean_single_rename"] is True
    assert receipts["ensemble_snapshots_rows"] == 3
    conn.close()
