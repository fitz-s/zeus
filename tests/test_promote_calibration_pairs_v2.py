# Created: 2026-05-12
# Last reused or audited: 2026-05-12
# Authority basis: K1 workload-class DB split; PR #112 Option (c) split.
# Tests for scripts/promote_calibration_pairs_v2.py.
"""Unit tests for the STAGE -> prod calibration_pairs_v2 promotion script.

Each test builds a tiny synthetic STAGE_DB and PROD_DB inside ``tmp_path``,
exercises one subcommand, and asserts the expected outcome. None of these
tests touch the real production zeus-forecasts.db. Tests target ONLY the
calibration_pairs_v2 surface (its sibling platt_models_v2 promotion lives
on zeus-world.db and is covered by tests/test_promote_platt_models_v2.py).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import promote_calibration_pairs_v2 as P  # noqa: E402


# --------------------------------------------------------------------------
# Schema fixtures
# --------------------------------------------------------------------------

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

DV_HIGH = "tigge_mx2t6_local_calendar_day_max_v1"
DV_LOW = "tigge_mn2t6_local_calendar_day_min_v1"


def _build_db(path: Path, *, with_meta: bool = True) -> None:
    """Build a tiny calibration_pairs_v2-only DB. Stand-in for zeus-forecasts.db."""
    conn = sqlite3.connect(str(path))
    conn.executescript(PAIRS_SCHEMA)
    if with_meta:
        conn.executescript(META_SCHEMA)
    conn.commit()
    conn.close()


def _insert_pair(
    conn: sqlite3.Connection,
    city: str,
    data_version: str,
    pair_id: int | None = None,
    target_date: str = "2024-01-01",
    metric: str = "high",
) -> None:
    conn.execute(
        """
        INSERT INTO calibration_pairs_v2
        (pair_id, city, target_date, temperature_metric, observation_field,
         range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, snapshot_id, data_version)
        VALUES (?, ?, ?, ?, ?, '>=20', 0.5, 1, 0.0, 'winter', 'mid',
                '2024-01-01T00:00:00Z', NULL, ?)
        """,
        (
            pair_id,
            city,
            target_date,
            metric,
            "high_temp" if metric == "high" else "low_temp",
            data_version,
        ),
    )


def _insert_complete_sentinel(
    conn: sqlite3.Connection, metric_label: str, data_version: str, n_mc: int = 10000
) -> None:
    """Insert a sentinel matching the rebuild-script's full-rebuild pattern."""
    sentinel_metric = "high" if metric_label == "high" else "low"
    key = (
        f"{P.REBUILD_COMPLETE_META_PREFIX}:metric={sentinel_metric}:bin_source=canonical_v2:"
        f"city=all:start=all:end=all:data_version={data_version}:cycle=all:source_id=all:"
        f"horizon=all:n_mc={n_mc}"
    )
    payload = json.dumps(
        {
            "status": "complete",
            "completed": True,
            "scope": {"data_version": data_version, "n_mc": n_mc},
            "stats": {"pairs_written": 100},
        }
    )
    conn.execute("INSERT INTO zeus_meta (key, value) VALUES (?, ?)", (key, payload))


def _insert_in_progress_sentinel(
    conn: sqlite3.Connection, metric_label: str, data_version: str, n_mc: int = 10000
) -> None:
    sentinel_metric = "high" if metric_label == "high" else "low"
    key = (
        f"{P.REBUILD_COMPLETE_META_PREFIX}:metric={sentinel_metric}:bin_source=canonical_v2:"
        f"city=all:start=all:end=all:data_version={data_version}:cycle=all:source_id=all:"
        f"horizon=all:n_mc={n_mc}"
    )
    payload = json.dumps(
        {"status": "in_progress", "completed": False, "scope": {"data_version": data_version}}
    )
    conn.execute("INSERT INTO zeus_meta (key, value) VALUES (?, ?)", (key, payload))


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# K1 isolation invariant: script must target zeus-forecasts.db
# --------------------------------------------------------------------------


def test_default_prod_db_targets_zeus_forecasts():
    """K1 invariant: this script's default PROD is zeus-forecasts.db, NOT
    zeus-world.db. Cross-DB confusion would break the workload-class split."""
    assert P.DEFAULT_PROD_DB == "state/zeus-forecasts.db"
    assert P.TARGET_TABLE == "calibration_pairs_v2"


# --------------------------------------------------------------------------
# inspect
# --------------------------------------------------------------------------


def test_inspect_well_formed_stage(tmp_path, capsys):
    stage = tmp_path / "stage.db"
    _build_db(stage)
    conn = sqlite3.connect(str(stage))
    _insert_complete_sentinel(conn, "high", DV_HIGH)
    _insert_complete_sentinel(conn, "low", DV_LOW)
    _insert_pair(conn, "Tokyo", DV_HIGH)
    _insert_pair(conn, "London", DV_HIGH)
    _insert_pair(conn, "Tokyo", DV_LOW, metric="low")
    conn.commit()
    conn.close()

    args = P.build_parser().parse_args(
        ["inspect", "--stage-db", str(stage), "--metrics", "high,low"]
    )
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "high            -> complete" in out
    assert "low             -> complete" in out
    assert "READY for promote" in out


def test_inspect_refuses_incomplete_stage(tmp_path, capsys):
    stage = tmp_path / "stage.db"
    _build_db(stage)
    conn = sqlite3.connect(str(stage))
    _insert_in_progress_sentinel(conn, "high", DV_HIGH)
    conn.commit()
    conn.close()

    args = P.build_parser().parse_args(
        ["inspect", "--stage-db", str(stage), "--metrics", "high"]
    )
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert "high            -> in_progress" in out
    assert "NOT READY" in out


# --------------------------------------------------------------------------
# promote
# --------------------------------------------------------------------------


def test_promote_dry_run_does_not_touch_prod(tmp_path, capsys):
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)
    s = sqlite3.connect(str(stage))
    _insert_complete_sentinel(s, "high", DV_HIGH)
    _insert_pair(s, "Tokyo", DV_HIGH)
    s.commit()
    s.close()

    p = sqlite3.connect(str(prod))
    _insert_pair(p, "OldCity", DV_HIGH)
    p.commit()
    p.close()

    pre_hash = _file_hash(prod)
    pre_mtime = prod.stat().st_mtime_ns

    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    rc = args.func(args)
    capsys.readouterr()
    assert rc == 0
    assert _file_hash(prod) == pre_hash, "PROD content must not change in dry-run"
    assert prod.stat().st_mtime_ns == pre_mtime, "PROD mtime must not change in dry-run"
    assert not (tmp_path / "backups").exists(), "Backup must not be created in dry-run"


def test_promote_commit_replaces_metric_rows(tmp_path, capsys):
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)

    s = sqlite3.connect(str(stage))
    _insert_complete_sentinel(s, "high", DV_HIGH)
    # Explicit pair_ids in STAGE that do not collide with PROD's autoincrement.
    _insert_pair(s, "Tokyo", DV_HIGH, pair_id=1001)
    _insert_pair(s, "London", DV_HIGH, pair_id=1002)
    s.commit()
    s.close()

    p = sqlite3.connect(str(prod))
    _insert_pair(p, "OldCity", DV_HIGH, pair_id=1)
    # Rows for OTHER data_version must be untouched
    _insert_pair(p, "UntouchedCity", DV_LOW, metric="low", pair_id=2)
    p.commit()
    p.close()

    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--commit",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    rc = args.func(args)
    capsys.readouterr()
    assert rc == 0

    p = sqlite3.connect(str(prod))
    high_cities = {r[0] for r in p.execute(
        "SELECT city FROM calibration_pairs_v2 WHERE data_version=?", (DV_HIGH,)
    )}
    low_cities = {r[0] for r in p.execute(
        "SELECT city FROM calibration_pairs_v2 WHERE data_version=?", (DV_LOW,)
    )}
    p.close()
    assert high_cities == {"Tokyo", "London"}, f"got {high_cities}"
    assert low_cities == {"UntouchedCity"}, "Low metric must be untouched"


def test_promote_creates_verifiable_backup(tmp_path, capsys):
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)
    s = sqlite3.connect(str(stage))
    _insert_complete_sentinel(s, "high", DV_HIGH)
    _insert_pair(s, "Tokyo", DV_HIGH)
    s.commit()
    s.close()
    p = sqlite3.connect(str(prod))
    _insert_pair(p, "ToBeBackedUpCity", DV_HIGH)
    p.commit()
    p.close()

    backup_dir = tmp_path / "backups"
    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--commit", "--backup-dir", str(backup_dir),
        ]
    )
    rc = args.func(args)
    capsys.readouterr()
    assert rc == 0

    # Filename must be zeus-forecasts.db.calibration_pairs_v2_pre_promotion_*.sql.gz
    # (K1: forecasts DB; sibling script writes zeus-world.db.platt_models_v2_*).
    backups = list(
        backup_dir.glob("zeus-forecasts.db.calibration_pairs_v2_pre_promotion_*.sql.gz")
    )
    assert len(backups) == 1, f"expected 1 backup, got {backups}"
    backup = backups[0]
    # Verify gzip integrity
    with gzip.open(backup, "rt") as fh:
        content = fh.read()
    assert "ToBeBackedUpCity" in content, "Backup must contain pre-promotion row"
    assert "BEGIN TRANSACTION;" in content
    assert "COMMIT;" in content
    # Backup must NOT include platt_models_v2 (lives on zeus-world.db).
    assert "platt_models_v2" not in content


def test_promote_rollback_on_integrity_failure(tmp_path, capsys, monkeypatch):
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)
    s = sqlite3.connect(str(stage))
    _insert_complete_sentinel(s, "high", DV_HIGH)
    _insert_pair(s, "Tokyo", DV_HIGH)
    s.commit()
    s.close()
    p = sqlite3.connect(str(prod))
    _insert_pair(p, "OldCity", DV_HIGH)
    p.commit()
    p.close()

    # Capture row state before
    p = sqlite3.connect(str(prod))
    pre_cities = {r[0] for r in p.execute(
        "SELECT city FROM calibration_pairs_v2 WHERE data_version=?", (DV_HIGH,)
    )}
    p.close()

    # Force integrity check to report not-ok via monkeypatch on extracted helper
    monkeypatch.setattr(P, "_run_integrity_check", lambda conn: "forced_failure_for_test")

    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--commit",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    with pytest.raises(RuntimeError, match="integrity_check FAILED"):
        args.func(args)
    capsys.readouterr()

    # PROD content must be unchanged (rollback worked).
    p = sqlite3.connect(str(prod))
    post_cities = {r[0] for r in p.execute(
        "SELECT city FROM calibration_pairs_v2 WHERE data_version=?", (DV_HIGH,)
    )}
    p.close()
    assert post_cities == pre_cities, (
        f"Rollback failed to preserve content: pre={pre_cities} post={post_cities}"
    )


def test_promote_refuses_when_sentinel_in_progress(tmp_path, capsys):
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)
    s = sqlite3.connect(str(stage))
    _insert_in_progress_sentinel(s, "high", DV_HIGH)
    _insert_pair(s, "Tokyo", DV_HIGH)
    s.commit()
    s.close()

    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--commit",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED" in out


def test_promote_refuses_when_stage_has_zero_rows(tmp_path, capsys):
    """Copilot C (#112): --commit must refuse if STAGE has 0 rows for the
    requested metric, otherwise the DELETE+INSERT path silently wipes PROD."""
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)

    s = sqlite3.connect(str(stage))
    _insert_complete_sentinel(s, "high", DV_HIGH)  # complete but no rows
    s.commit()
    s.close()

    p = sqlite3.connect(str(prod))
    _insert_pair(p, "LiveCity", DV_HIGH)
    p.commit()
    p.close()

    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--commit",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED" in out and "0 rows" in out

    # PROD must be untouched
    p = sqlite3.connect(str(prod))
    cities = {r[0] for r in p.execute(
        "SELECT city FROM calibration_pairs_v2 WHERE data_version=?", (DV_HIGH,)
    )}
    p.close()
    assert cities == {"LiveCity"}


def test_promote_refuses_on_schema_mismatch(tmp_path, capsys):
    """Copilot M (#112): STAGE/PROD schema drift must be caught BEFORE
    BEGIN IMMEDIATE so a mid-promotion failure cannot leave PROD partial."""
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)

    # Drop a column on STAGE to force schema drift on calibration_pairs_v2.
    # SQLite ALTER TABLE DROP COLUMN was added in 3.35.
    s = sqlite3.connect(str(stage))
    _insert_complete_sentinel(s, "high", DV_HIGH)
    _insert_pair(s, "Tokyo", DV_HIGH)
    try:
        s.execute("ALTER TABLE calibration_pairs_v2 DROP COLUMN horizon_profile")
    except sqlite3.OperationalError:
        pytest.skip("SQLite < 3.35 does not support DROP COLUMN")
    s.commit()
    s.close()

    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--commit",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert "schema mismatch" in out


def test_promote_null_snapshot_id_flag(tmp_path, capsys):
    """--null-snapshot-id should NULL out snapshot_id on inserted rows."""
    stage = tmp_path / "stage.db"
    prod = tmp_path / "prod.db"
    _build_db(stage)
    _build_db(prod)

    s = sqlite3.connect(str(stage))
    _insert_complete_sentinel(s, "high", DV_HIGH)
    # Pair with non-NULL snapshot_id
    s.execute(
        """
        INSERT INTO calibration_pairs_v2
        (city, target_date, temperature_metric, observation_field, range_label,
         p_raw, outcome, lead_days, season, cluster, forecast_available_at,
         snapshot_id, data_version)
        VALUES ('Tokyo', '2024-01-01', 'high', 'high_temp', '>=20',
                0.5, 1, 0.0, 'winter', 'mid', '2024-01-01T00:00:00Z',
                42, ?)
        """,
        (DV_HIGH,),
    )
    s.commit()
    s.close()

    args = P.build_parser().parse_args(
        [
            "promote", "--stage-db", str(stage), "--prod-db", str(prod),
            "--metrics", "high", "--commit", "--null-snapshot-id",
            "--backup-dir", str(tmp_path / "backups"),
        ]
    )
    rc = args.func(args)
    capsys.readouterr()
    assert rc == 0

    p = sqlite3.connect(str(prod))
    snapshot_ids = [
        r[0] for r in p.execute(
            "SELECT snapshot_id FROM calibration_pairs_v2 WHERE data_version=?",
            (DV_HIGH,),
        )
    ]
    p.close()
    assert snapshot_ids == [None], f"expected NULL snapshot_id, got {snapshot_ids}"


def test_sentinel_in_progress_takes_precedence_over_wildcard_complete(tmp_path):
    """Codex P1 (#112): an older `data_version=all start=all end=all`
    complete sentinel must NOT mask a newer scoped in_progress sentinel
    for the same metric/data_version pair."""
    stage = tmp_path / "stage.db"
    _build_db(stage)
    s = sqlite3.connect(str(stage))

    # Stale wildcard complete sentinel (older rebuild scope)
    stale_key = (
        f"{P.REBUILD_COMPLETE_META_PREFIX}:metric=low:bin_source=canonical_v2:"
        f"city=all:start=all:end=all:data_version=all:cycle=all:source_id=all:"
        f"horizon=all:n_mc=10000"
    )
    s.execute(
        "INSERT INTO zeus_meta (key, value) VALUES (?, ?)",
        (stale_key, json.dumps({"status": "complete", "completed": True})),
    )
    # Newer scoped in_progress sentinel for the actual data_version
    _insert_in_progress_sentinel(s, "low", DV_LOW)
    s.commit()
    s.close()

    conn = sqlite3.connect(str(stage))
    conn.row_factory = sqlite3.Row
    sentinels = P._load_sentinels(conn)
    conn.close()
    status = P._sentinel_status_for_metrics(sentinels, ["low"])
    assert status == {"low": "in_progress"}, (
        f"in_progress must win over stale wildcard complete; got {status}"
    )


def test_rw_connect_preserves_existing_pragmas(tmp_path):
    """Copilot M (#112): _rw_connect should not switch journal_mode/synchronous
    on a DB that already has them at the desired values; and conversely it
    should keep DELETE journal mode if the DB was DELETE."""
    prod = tmp_path / "prod.db"
    _build_db(prod)
    # Default new DB starts in DELETE; promote it to WAL via first connect
    conn = P._rw_connect(prod)
    jm1 = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    sync1 = int(conn.execute("PRAGMA synchronous").fetchone()[0])
    conn.close()
    assert jm1 == "wal"
    assert sync1 == 1

    # Second open: pragmas already match, _rw_connect must be idempotent
    conn2 = P._rw_connect(prod)
    jm2 = str(conn2.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    sync2 = int(conn2.execute("PRAGMA synchronous").fetchone()[0])
    conn2.close()
    assert jm2 == jm1 and sync2 == sync1


# --------------------------------------------------------------------------
# verify (table-local on zeus-forecasts.db; no cross-DB JOIN)
# --------------------------------------------------------------------------


def test_verify_pass(tmp_path, capsys):
    prod = tmp_path / "prod.db"
    _build_db(prod)
    p = sqlite3.connect(str(prod))
    _insert_pair(p, "Tokyo", DV_HIGH)
    _insert_pair(p, "London", DV_LOW, metric="low")
    p.commit()
    p.close()

    args = P.build_parser().parse_args(["verify", "--prod-db", str(prod)])
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "calibration_pairs_v2 has" in out
    assert "All identity columns are non-NULL" in out
    # Must NOT do a cross-DB JOIN against platt_models_v2 (K1: lives on
    # zeus-world.db).
    assert "platt_models_v2" not in out


def test_verify_fail_when_empty(tmp_path, capsys):
    prod = tmp_path / "prod.db"
    _build_db(prod)
    # No rows inserted

    args = P.build_parser().parse_args(["verify", "--prod-db", str(prod)])
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out and "empty" in out
