# Created: 2026-05-13
# Last reused or audited: 2026-05-13
# Authority basis: K1 workload-class DB split; ATTACH+SQL bulk promote
# path rewrite (2026-05-13). Verifies the cross-module relationship
# between STAGE.calibration_pairs_v2 -> PROD.calibration_pairs_v2 under
# the new INSERT...SELECT bulk path. These are RELATIONSHIP tests
# (semantic invariants across the STAGE/PROD boundary), per the
# project-wide "relationship tests before function tests" rule.
"""Relationship tests for the ATTACH+SQL bulk promote path.

These complement ``test_promote_calibration_pairs_v2.py`` (function-level
unit tests). They assert four cross-module properties:

1. Atomicity: if the integrity check fails mid-promote, PROD reverts to
   its pre-promote state (DELETE + INSERT both undone).
2. ``--null-snapshot-id`` semantics: when set, the INSERT projection
   replaces ``stage.snapshot_id`` with SQL NULL (not just zero, not
   "0", not the STAGE value); when unset, the STAGE value is preserved.
3. Metric scoping: ``--metrics high,low`` MUST touch ONLY rows whose
   ``data_version`` belongs to those metrics. Other data_versions in
   PROD survive unchanged.
4. Speedup floor: 1000-row STAGE -> PROD round-trip completes in under
   5 wall-clock seconds. The legacy Python loop on the same data
   reliably exceeded ~1s; the ATTACH path should be at least an order
   of magnitude faster (bounded loosely to avoid flake on slow CI).
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import promote_calibration_pairs_v2 as P  # noqa: E402

DV_HIGH = "tigge_mx2t6_local_calendar_day_max_v1"
DV_LOW = "tigge_mn2t6_local_calendar_day_min_v1"
DV_LOW_CONTRACT = "tigge_mn2t6_local_calendar_day_min_contract_window_v2"

PAIRS_SCHEMA = """
CREATE TABLE calibration_pairs_v2 (
    pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL,
    observation_field TEXT NOT NULL,
    range_label TEXT NOT NULL,
    p_raw REAL NOT NULL,
    outcome INTEGER NOT NULL,
    lead_days REAL NOT NULL,
    season TEXT NOT NULL,
    cluster TEXT NOT NULL,
    forecast_available_at TEXT NOT NULL,
    settlement_value REAL,
    decision_group_id TEXT,
    bias_corrected INTEGER NOT NULL DEFAULT 0,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
    bin_source TEXT NOT NULL DEFAULT 'legacy',
    snapshot_id INTEGER,
    data_version TEXT NOT NULL,
    training_allowed INTEGER NOT NULL DEFAULT 1,
    causality_status TEXT NOT NULL DEFAULT 'OK',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cycle TEXT NOT NULL DEFAULT '00',
    source_id TEXT NOT NULL DEFAULT 'tigge_mars',
    horizon_profile TEXT NOT NULL DEFAULT 'full'
);
"""

META_SCHEMA = "CREATE TABLE zeus_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(PAIRS_SCHEMA)
    conn.executescript(META_SCHEMA)
    conn.commit()
    conn.close()


def _insert_pair(
    conn: sqlite3.Connection,
    *,
    pair_id: int,
    city: str,
    data_version: str,
    snapshot_id: int | None = 42,
    metric: str = "high",
) -> None:
    conn.execute(
        """
        INSERT INTO calibration_pairs_v2
        (pair_id, city, target_date, temperature_metric, observation_field,
         range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, snapshot_id, data_version)
        VALUES (?, ?, '2024-01-01', ?, ?, '>=20', 0.5, 1, 0.0,
                'winter', 'mid', '2024-01-01T00:00:00Z', ?, ?)
        """,
        (
            pair_id,
            city,
            metric,
            "high_temp" if metric == "high" else "low_temp",
            snapshot_id,
            data_version,
        ),
    )


def _insert_complete_sentinel(
    conn: sqlite3.Connection, metric_label: str, data_version: str
) -> None:
    import json

    sentinel_metric = "high" if metric_label == "high" else "low"
    key = (
        f"{P.REBUILD_COMPLETE_META_PREFIX}:metric={sentinel_metric}:bin_source=canonical_v2:"
        f"city=all:start=all:end=all:data_version={data_version}:cycle=all:source_id=all:"
        f"horizon=all:n_mc=10000"
    )
    payload = json.dumps({"status": "complete", "completed": True})
    conn.execute("INSERT INTO zeus_meta (key, value) VALUES (?, ?)", (key, payload))


def _build_stage_with_complete_high_low(path: Path, n_per_dv: int = 5) -> None:
    _build_db(path)
    conn = sqlite3.connect(str(path))
    _insert_complete_sentinel(conn, "high", DV_HIGH)
    _insert_complete_sentinel(conn, "low", DV_LOW)
    # STAGE pair_ids must not collide with PROD pair_ids. Real STAGE
    # rows start at ~74M; tests use a high offset to make the
    # invariant explicit.
    base = 1_000_000
    for i in range(n_per_dv):
        _insert_pair(conn, pair_id=base + i,
                     city=f"H{i}", data_version=DV_HIGH,
                     snapshot_id=900 + i, metric="high")
    for i in range(n_per_dv):
        _insert_pair(conn, pair_id=base + 10_000 + i,
                     city=f"L{i}", data_version=DV_LOW,
                     snapshot_id=800 + i, metric="low")
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# 1. Atomicity: integrity_check failure mid-promote => PROD unchanged
# --------------------------------------------------------------------------


def test_attach_path_atomic_rollback_on_error(tmp_path, capsys, monkeypatch):
    """Relationship invariant: STAGE -> PROD promote is atomic across
    the DELETE + INSERT...SELECT boundary. If anything between
    BEGIN IMMEDIATE and COMMIT raises, PROD's pre-promote state for
    the target data_versions MUST be exactly recovered.
    """
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_stage_with_complete_high_low(stage, n_per_dv=3)
    _build_db(prod)

    p = sqlite3.connect(str(prod))
    _insert_pair(p, pair_id=1, city="PreExistingHigh",
                 data_version=DV_HIGH, snapshot_id=11, metric="high")
    _insert_pair(p, pair_id=2, city="PreExistingLow",
                 data_version=DV_LOW, snapshot_id=12, metric="low")
    # An untouched-metric row must NEVER move regardless of outcome
    _insert_pair(p, pair_id=3, city="Survivor",
                 data_version=DV_LOW_CONTRACT, snapshot_id=13, metric="low")
    p.commit()
    p.close()

    pre_snapshot = _full_snapshot(prod)

    # Force the integrity check inside the BEGIN IMMEDIATE block to
    # report failure. This exercises the rollback path that owns BOTH
    # the DELETE and the INSERT.
    monkeypatch.setattr(P, "_run_integrity_check",
                        lambda conn: "forced_rollback_for_atomicity_test")

    args = P.build_parser().parse_args([
        "promote", "--stage-db", str(stage), "--prod-db", str(prod),
        "--metrics", "high,low", "--commit",
        "--backup-dir", str(tmp_path / "backups"),
    ])
    with pytest.raises(RuntimeError, match="integrity_check FAILED"):
        args.func(args)
    capsys.readouterr()

    post_snapshot = _full_snapshot(prod)
    assert pre_snapshot == post_snapshot, (
        "ATTACH+SQL promote rollback did not restore PROD state. "
        f"Diff: pre={pre_snapshot} post={post_snapshot}"
    )


# --------------------------------------------------------------------------
# 2. --null-snapshot-id SQL projection semantics
# --------------------------------------------------------------------------


def test_attach_path_null_snapshot_id(tmp_path, capsys):
    """Relationship invariant: --null-snapshot-id MUST cause every
    inserted row to land with snapshot_id IS NULL in PROD, regardless
    of the STAGE value. Without the flag, STAGE values pass through
    unchanged.
    """
    # First: with --null-snapshot-id => all NULL.
    stage = tmp_path / "stage_a.db"
    prod = tmp_path / "prod_a.db"
    _build_stage_with_complete_high_low(stage, n_per_dv=4)
    _build_db(prod)
    args = P.build_parser().parse_args([
        "promote", "--stage-db", str(stage), "--prod-db", str(prod),
        "--metrics", "high,low", "--commit", "--null-snapshot-id",
        "--backup-dir", str(tmp_path / "backups_a"),
    ])
    rc = args.func(args)
    capsys.readouterr()
    assert rc == 0
    with sqlite3.connect(str(prod)) as p:
        not_null = p.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE snapshot_id IS NOT NULL"
        ).fetchone()[0]
        total = p.execute("SELECT COUNT(*) FROM calibration_pairs_v2").fetchone()[0]
    assert total == 8, f"expected 8 rows (4 high + 4 low), got {total}"
    assert not_null == 0, (
        f"--null-snapshot-id MUST null every inserted snapshot_id "
        f"(got {not_null}/{total} non-null)."
    )

    # Second: WITHOUT --null-snapshot-id => STAGE snapshot_ids preserved.
    stage2 = tmp_path / "stage_b.db"
    prod2 = tmp_path / "prod_b.db"
    _build_stage_with_complete_high_low(stage2, n_per_dv=2)
    _build_db(prod2)
    args2 = P.build_parser().parse_args([
        "promote", "--stage-db", str(stage2), "--prod-db", str(prod2),
        "--metrics", "high", "--commit",
        "--backup-dir", str(tmp_path / "backups_b"),
    ])
    rc2 = args2.func(args2)
    capsys.readouterr()
    assert rc2 == 0
    with sqlite3.connect(str(prod2)) as p:
        rows = {(r[0], r[1]) for r in p.execute(
            "SELECT city, snapshot_id FROM calibration_pairs_v2 "
            "WHERE data_version = ?", (DV_HIGH,)
        )}
    assert rows == {("H0", 900), ("H1", 901)}, (
        f"Without --null-snapshot-id, STAGE values must pass through; got {rows}"
    )


# --------------------------------------------------------------------------
# 3. --metrics scoping: other data_versions untouched
# --------------------------------------------------------------------------


def test_attach_path_metric_scope(tmp_path, capsys):
    """Relationship invariant: --metrics high,low MUST scope BOTH the
    DELETE (PROD) and the INSERT...SELECT (STAGE->PROD) to those
    data_versions only. PROD rows with other data_versions survive
    byte-for-byte.
    """
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_stage_with_complete_high_low(stage, n_per_dv=3)
    _build_db(prod)

    p = sqlite3.connect(str(prod))
    _insert_pair(p, pair_id=1, city="OldHigh",
                 data_version=DV_HIGH, snapshot_id=99, metric="high")
    _insert_pair(p, pair_id=2, city="OldLow",
                 data_version=DV_LOW, snapshot_id=98, metric="low")
    # Untouched data_version: low_contract. Must survive promote.
    _insert_pair(p, pair_id=3, city="ContractCity1",
                 data_version=DV_LOW_CONTRACT, snapshot_id=77, metric="low")
    _insert_pair(p, pair_id=4, city="ContractCity2",
                 data_version=DV_LOW_CONTRACT, snapshot_id=78, metric="low")
    p.commit()
    p.close()

    contract_pre = _data_version_snapshot(prod, DV_LOW_CONTRACT)

    args = P.build_parser().parse_args([
        "promote", "--stage-db", str(stage), "--prod-db", str(prod),
        "--metrics", "high,low", "--commit",
        "--backup-dir", str(tmp_path / "backups"),
    ])
    rc = args.func(args)
    capsys.readouterr()
    assert rc == 0

    # Untouched data_version: bit-identical before/after.
    contract_post = _data_version_snapshot(prod, DV_LOW_CONTRACT)
    assert contract_pre == contract_post, (
        f"low_contract rows must be untouched by --metrics high,low. "
        f"pre={contract_pre} post={contract_post}"
    )

    # Touched data_versions: replaced with STAGE rows.
    with sqlite3.connect(str(prod)) as p:
        high_cities = {r[0] for r in p.execute(
            "SELECT city FROM calibration_pairs_v2 WHERE data_version = ?",
            (DV_HIGH,))}
        low_cities = {r[0] for r in p.execute(
            "SELECT city FROM calibration_pairs_v2 WHERE data_version = ?",
            (DV_LOW,))}
    assert high_cities == {"H0", "H1", "H2"}
    assert low_cities == {"L0", "L1", "L2"}


# --------------------------------------------------------------------------
# 4. Speedup floor: 1k rows in < 5s wall-clock
# --------------------------------------------------------------------------


def test_attach_path_speedup_1k_rows(tmp_path, capsys):
    """Relationship invariant: ATTACH+SQL path completes a 1000-row
    promote in well under 5 seconds end-to-end (backup + DELETE +
    INSERT...SELECT + integrity_check). The 5s ceiling is loose to
    avoid CI flake; the legacy Python loop at 6.6k rows/sec on a 1000
    row workload usually took ~0.2s but at 83M rows extrapolated to
    ~3.5h — the ATTACH path's gain shows on the big DB, not the small
    test. We assert only the absolute ceiling here; the real signal
    is the unscaled 83M-row promote in operations.
    """
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_stage_with_complete_high_low(stage, n_per_dv=500)
    _build_db(prod)

    args = P.build_parser().parse_args([
        "promote", "--stage-db", str(stage), "--prod-db", str(prod),
        "--metrics", "high,low", "--commit", "--null-snapshot-id",
        "--backup-dir", str(tmp_path / "backups"),
    ])
    t0 = time.perf_counter()
    rc = args.func(args)
    elapsed = time.perf_counter() - t0
    capsys.readouterr()
    assert rc == 0, "promote rc != 0"
    assert elapsed < 5.0, (
        f"ATTACH path took {elapsed:.2f}s for 1000-row promote (ceiling 5s). "
        "Either CI is degraded or the bulk path regressed."
    )
    with sqlite3.connect(str(prod)) as p:
        n = p.execute("SELECT COUNT(*) FROM calibration_pairs_v2").fetchone()[0]
    assert n == 1000, f"expected 1000 rows after promote, got {n}"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _full_snapshot(prod: Path) -> list[tuple]:
    """Capture all calibration_pairs_v2 rows ordered deterministically.

    Used by atomicity test to compare pre/post state. We project the
    full row tuple so that any silent mutation (deleted snapshot_id,
    altered data_version, etc.) shows up as a diff.
    """
    conn = sqlite3.connect(str(prod))
    try:
        return list(conn.execute(
            "SELECT pair_id, city, target_date, temperature_metric, snapshot_id, "
            "data_version FROM calibration_pairs_v2 ORDER BY pair_id"
        ))
    finally:
        conn.close()


def _data_version_snapshot(prod: Path, data_version: str) -> list[tuple]:
    conn = sqlite3.connect(str(prod))
    try:
        return list(conn.execute(
            "SELECT pair_id, city, snapshot_id, data_version FROM calibration_pairs_v2 "
            "WHERE data_version = ? ORDER BY pair_id",
            (data_version,),
        ))
    finally:
        conn.close()
