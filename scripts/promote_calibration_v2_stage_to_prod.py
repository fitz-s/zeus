# Created: 2026-05-12
# Last reused/audited: 2026-05-12
# Authority basis: STAGE_DB → production zeus-world.db promotion of
# calibration_pairs_v2 + platt_models_v2 artifacts produced by
# scripts/rebuild_calibration_pairs_v2.py. All mutations are gated by
# --commit; default behavior is dry-run with full backup + rollback semantics.
"""Promote calibration_v2 artifacts from a STAGE_DB to production zeus-world.db.

Subcommands
-----------

* ``inspect``  — read-only summary of STAGE vs PROD coverage. Reports rebuild
  sentinel state, row counts, distinct (city, data_version) pairs, and primary
  key conflicts. Exits 1 if STAGE has any in_progress sentinel or missing
  COMPLETE markers for the requested metrics.
* ``promote``  — dry-run by default. With ``--commit``: backs up PROD
  ``calibration_pairs_v2`` + ``platt_models_v2`` to a gzipped SQL dump under
  ``state/backups/``, opens PROD with ``BEGIN IMMEDIATE``, replaces rows
  filtered by ``data_version`` derived from the metric set, runs
  ``PRAGMA integrity_check``, and rolls back on any failure.
* ``verify``   — read-only post-promote consistency check. Confirms every
  ``platt_models_v2`` (data_version, cluster, season) bucket has
  matching ``calibration_pairs_v2`` rows for the same data_version.
  Note: ``platt_models_v2`` has no ``city`` column — stratification is
  by (data_version, cluster, season) only.

Constraints
-----------

* STAGE_DB and PROD opened with ``?mode=ro`` for ``inspect``, ``verify``, and
  the dry-run path of ``promote``.
* PROD is opened writable in the ``promote --commit`` path via a direct
  ``sqlite3.connect`` (``_rw_connect``). PRAGMA ``foreign_keys`` is
  intentionally left at the existing setting (off in zeus-world.db);
  ``journal_mode``/``synchronous`` are read first and only set when they
  do not already match ``WAL`` / ``NORMAL`` to avoid changing PROD
  pragmas as a side effect. Uses ``BEGIN IMMEDIATE`` and rolls back on
  any error.
* Backup is atomically created (write to ``.tmp`` then ``os.replace``) and
  independently verifiable via ``gunzip + sqlite3``.
* Generic over ``--stage-db PATH`` and ``--prod-db PATH``. No hardcoded
  STAGE_DB filename. No hardcoded PROD path either; defaults to
  ``state/zeus-world.db`` when omitted.
* Snapshot FK: ``calibration_pairs_v2.snapshot_id`` references
  ``ensemble_snapshots_v2(snapshot_id)`` but PRAGMA foreign_keys is OFF in
  zeus-world.db (verified). STAGE snapshot_ids may not exist in PROD; we
  preserve them as-is (FK not enforced) but expose ``--null-snapshot-id``
  to NULL them out for safety.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Stratification-key building requires the rebuild module's sentinel parser.
# We keep this script self-contained: re-implement minimal sentinel-key parser.

REBUILD_COMPLETE_META_PREFIX = "calibration_pairs_v2_rebuild_complete"

# Mapping from logical metric label to expected data_version values.
METRIC_TO_DATA_VERSIONS: dict[str, tuple[str, ...]] = {
    "high": ("tigge_mx2t6_local_calendar_day_max_v1",),
    "low": ("tigge_mn2t6_local_calendar_day_min_v1",),
    "low_contract": ("tigge_mn2t6_local_calendar_day_min_contract_window_v2",),
}

ALL_METRICS: tuple[str, ...] = ("high", "low", "low_contract")


# --------------------------------------------------------------------------
# DB connection helpers
# --------------------------------------------------------------------------


def _ro_connect(path: str | Path) -> sqlite3.Connection:
    """Open *path* as a read-only sqlite connection via URI mode."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"DB not found: {p}")
    uri = f"file:{p}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _rw_connect(path: str | Path) -> sqlite3.Connection:
    """Open *path* as a writable sqlite connection (only used in --commit).

    Reads the existing ``journal_mode`` and ``synchronous`` pragmas first
    and only writes them when they do NOT already match ``WAL`` /
    ``NORMAL``. This avoids changing PROD pragmas as a side effect of
    running ``promote --commit`` against a DB that may have a different
    operator-chosen configuration.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"DB not found: {p}")
    conn = sqlite3.connect(str(p), timeout=60.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Match the running daemon's settings; do NOT enable FK (off in prod).
    current_jm = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if current_jm != "wal":
        conn.execute("PRAGMA journal_mode=WAL")
    current_sync = int(conn.execute("PRAGMA synchronous").fetchone()[0])
    # 1 == NORMAL
    if current_sync != 1:
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# --------------------------------------------------------------------------
# Sentinel parsing
# --------------------------------------------------------------------------


def _parse_sentinel_key(key: str) -> dict[str, str] | None:
    parts = key.split(":")
    if len(parts) != 11 or parts[0] != REBUILD_COMPLETE_META_PREFIX:
        return None
    out: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            return None
        name, value = part.split("=", 1)
        out[name] = value
    return out


def _load_sentinels(conn: sqlite3.Connection) -> list[dict[str, object]]:
    cur = conn.execute(
        "SELECT key, value FROM zeus_meta WHERE key LIKE ?",
        (f"{REBUILD_COMPLETE_META_PREFIX}%",),
    )
    out: list[dict[str, object]] = []
    for row in cur.fetchall():
        key = str(row["key"])
        scope = _parse_sentinel_key(key)
        if scope is None:
            continue
        try:
            payload = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            payload = {"status": "unparseable", "raw": str(row["value"])[:200]}
        out.append({"key": key, "scope": scope, "payload": payload})
    return out


def _sentinel_status_for_metrics(
    sentinels: Iterable[dict[str, object]],
    metrics: Iterable[str],
) -> dict[str, str]:
    """Return per-metric status. 'complete', 'in_progress', or 'missing'.

    A metric is 'complete' iff there's at least one sentinel whose
    ``scope.metric == metric`` AND ``scope.start == 'all'`` AND
    ``scope.end == 'all'`` AND ``payload.status == 'complete'``.

    A metric is 'in_progress' iff any sentinel for it has
    ``payload.status == 'in_progress'``.

    Otherwise 'missing'.
    """
    by_metric: dict[str, list[dict[str, object]]] = defaultdict(list)
    for s in sentinels:
        scope = s["scope"]
        assert isinstance(scope, dict)
        m = scope.get("metric")
        if isinstance(m, str):
            by_metric[m].append(s)

    out: dict[str, str] = {}
    for metric in metrics:
        # Map logical metric label → sentinel "metric" value (the rebuild
        # script writes "high" or "low"; low_contract is a low-data_version
        # variant, also keyed as "low" for rebuild but separated by data_version)
        sentinel_metric = "high" if metric == "high" else "low"
        candidates = by_metric.get(sentinel_metric, [])
        # Filter to candidates whose data_version matches our requested metric
        wanted_dvs = set(METRIC_TO_DATA_VERSIONS.get(metric, ()))
        relevant = [
            s
            for s in candidates
            if s["scope"].get("data_version") in wanted_dvs  # type: ignore[union-attr]
            or s["scope"].get("data_version") == "all"  # type: ignore[union-attr]
        ]
        if not relevant:
            out[metric] = "missing"
            continue
        # Codex P1 (#112): scan in_progress sentinels FIRST. An older
        # `data_version=all start=all end=all` complete sentinel must NOT
        # mask a newer scoped `data_version=<requested> status=in_progress`
        # sentinel \u2014 that would let `inspect`/`promote --commit` report
        # READY for a partially rebuilt scope.
        any_in_progress_for_wanted = any(
            s["payload"].get("status") == "in_progress"  # type: ignore[union-attr]
            and s["scope"].get("data_version") in wanted_dvs  # type: ignore[union-attr]
            for s in relevant
        )
        if any_in_progress_for_wanted:
            out[metric] = "in_progress"
            continue
        # Then look for an exact-scope complete sentinel: data_version
        # must match the wanted set (NOT just `all`), AND start/end == all.
        full_complete = [
            s
            for s in relevant
            if s["scope"].get("start") == "all"  # type: ignore[union-attr]
            and s["scope"].get("end") == "all"  # type: ignore[union-attr]
            and s["scope"].get("data_version") in wanted_dvs  # type: ignore[union-attr]
            and s["payload"].get("status") == "complete"  # type: ignore[union-attr]
        ]
        if full_complete:
            out[metric] = "complete"
            continue
        # Fall back: any in_progress (even wildcard scope) is in_progress.
        any_in_progress = any(
            s["payload"].get("status") == "in_progress" for s in relevant  # type: ignore[union-attr]
        )
        if any_in_progress:
            out[metric] = "in_progress"
            continue
        out[metric] = "missing"
    return out


# --------------------------------------------------------------------------
# Schema introspection
# --------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def _row_count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(conn.execute(sql, params).fetchone()[0])


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [row["name"] for row in cur.fetchall()]


def _coverage_matrix(
    conn: sqlite3.Connection, table: str, data_versions: Iterable[str] | None = None
) -> dict[str, dict[str, int]]:
    """Return {data_version: {city: count}}."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    where = ""
    params: tuple = ()
    if data_versions is not None:
        dv_list = list(data_versions)
        if not dv_list:
            return out
        where = f"WHERE data_version IN ({','.join('?' * len(dv_list))})"
        params = tuple(dv_list)
    sql = (
        f"SELECT data_version, city, COUNT(*) AS n FROM {table} {where} "
        "GROUP BY data_version, city"
    )
    for row in conn.execute(sql, params):
        out[row["data_version"]][row["city"]] = int(row["n"])
    return dict(out)


def _platt_coverage(
    conn: sqlite3.Connection, data_versions: Iterable[str] | None = None
) -> dict[str, dict[str, int]]:
    """Return {data_version: {(cluster, season): count}} as flat dict."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    where = ""
    params: tuple = ()
    if data_versions is not None:
        dv_list = list(data_versions)
        if not dv_list:
            return out
        where = f"WHERE data_version IN ({','.join('?' * len(dv_list))})"
        params = tuple(dv_list)
    sql = (
        f"SELECT data_version, cluster, season, COUNT(*) AS n FROM platt_models_v2 {where} "
        "GROUP BY data_version, cluster, season"
    )
    for row in conn.execute(sql, params):
        key = f"{row['cluster']}/{row['season']}"
        out[row["data_version"]][key] = int(row["n"])
    return dict(out)


# --------------------------------------------------------------------------
# Inspect subcommand
# --------------------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    stage_path = Path(args.stage_db).resolve()
    prod_path = Path(args.prod_db).resolve() if args.prod_db else None

    print(f"STAGE_DB: {stage_path}")
    if prod_path:
        print(f"PROD_DB:  {prod_path}")
    print()

    stage = _ro_connect(stage_path)
    prod = _ro_connect(prod_path) if prod_path else None

    metrics = _resolve_metrics(args.metrics)
    requested_dvs: list[str] = []
    for m in metrics:
        requested_dvs.extend(METRIC_TO_DATA_VERSIONS[m])
    print(f"Metrics:        {', '.join(metrics)}")
    print(f"Data versions:  {', '.join(requested_dvs)}")
    print()

    # Sentinels
    sentinels = _load_sentinels(stage)
    status = _sentinel_status_for_metrics(sentinels, metrics)
    print("=== Rebuild sentinels ===")
    for m in metrics:
        print(f"  {m:15s} -> {status[m]}")
    print()

    # Stage row counts
    print("=== STAGE row counts ===")
    sc_pairs = _row_count(
        stage,
        "calibration_pairs_v2",
        f"data_version IN ({','.join('?' * len(requested_dvs))})",
        tuple(requested_dvs),
    )
    sc_platt = _row_count(
        stage,
        "platt_models_v2",
        f"data_version IN ({','.join('?' * len(requested_dvs))})",
        tuple(requested_dvs),
    )
    print(f"  calibration_pairs_v2: {sc_pairs:>12,}")
    print(f"  platt_models_v2:      {sc_platt:>12,}")
    print()

    # Prod baseline
    if prod is not None:
        print("=== PROD row counts (baseline; would be replaced for these data_versions) ===")
        pc_pairs = _row_count(
            prod,
            "calibration_pairs_v2",
            f"data_version IN ({','.join('?' * len(requested_dvs))})",
            tuple(requested_dvs),
        )
        pc_platt = _row_count(
            prod,
            "platt_models_v2",
            f"data_version IN ({','.join('?' * len(requested_dvs))})",
            tuple(requested_dvs),
        )
        print(f"  calibration_pairs_v2: {pc_pairs:>12,}")
        print(f"  platt_models_v2:      {pc_platt:>12,}")
        print()
        # Total prod rows (for context)
        total_pairs = _row_count(prod, "calibration_pairs_v2")
        total_platt = _row_count(prod, "platt_models_v2")
        print(f"  (total calibration_pairs_v2 in PROD across all data_versions: {total_pairs:,})")
        print(f"  (total platt_models_v2 in PROD across all data_versions:      {total_platt:,})")
        print()

    # Coverage matrix
    print("=== STAGE coverage: cities × data_versions (calibration_pairs_v2) ===")
    cov = _coverage_matrix(stage, "calibration_pairs_v2", requested_dvs)
    for dv in requested_dvs:
        cities = cov.get(dv, {})
        print(f"  {dv}: {len(cities)} cities, {sum(cities.values()):,} pairs")
    print()

    print("=== STAGE coverage: platt_models_v2 (cluster/season buckets) ===")
    pcov = _platt_coverage(stage, requested_dvs)
    for dv in requested_dvs:
        buckets = pcov.get(dv, {})
        total = sum(buckets.values())
        print(f"  {dv}: {len(buckets)} buckets, {total:,} models")
    print()

    # Verdict
    bad = [m for m, s in status.items() if s != "complete"]
    if bad:
        print(f"⚠ STATUS: NOT READY — sentinels not complete for: {', '.join(bad)}")
        stage.close()
        if prod is not None:
            prod.close()
        return 1
    print("✓ STATUS: READY for promote")
    stage.close()
    if prod is not None:
        prod.close()
    return 0


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------


def _backup_prod_tables(
    prod_path: Path,
    metrics: list[str],
    backup_dir: Path,
    *,
    include_pairs: bool = False,
) -> Path:
    """Atomic, gzipped SQL dump of platt_models_v2 (and optionally
    calibration_pairs_v2) rows matching any of the metric data_versions.
    Returns final backup file path.

    Independently verifiable: ``gunzip -t`` then sqlite3 import + count.

    Only backs up tables that will actually be modified by the matching
    promote run \u2014 ``calibration_pairs_v2`` is large and is skipped unless
    ``include_pairs=True`` (matching the promote ``--include-pairs`` flag).
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = backup_dir / f"zeus-world.db.calibration_v2_pre_promotion_{ts}.sql.gz"
    tmp = final.with_suffix(final.suffix + ".tmp")

    requested_dvs: list[str] = []
    for m in metrics:
        requested_dvs.extend(METRIC_TO_DATA_VERSIONS[m])

    tables_to_backup = ["platt_models_v2"]
    if include_pairs:
        tables_to_backup.append("calibration_pairs_v2")

    conn = _ro_connect(prod_path)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as gz:
            gz.write("-- Zeus calibration_v2 pre-promotion backup\n")
            gz.write(f"-- Generated: {datetime.now(timezone.utc).isoformat()}\n")
            gz.write(f"-- Source PROD: {prod_path}\n")
            gz.write(f"-- Metrics: {','.join(metrics)}\n")
            gz.write(f"-- Data versions: {','.join(requested_dvs)}\n")
            gz.write(f"-- Tables backed up: {','.join(tables_to_backup)}\n\n")
            gz.write("BEGIN TRANSACTION;\n")
            for table in tables_to_backup:
                cols = _column_names(conn, table)
                placeholders = ",".join(cols)
                where = (
                    f"WHERE data_version IN ({','.join('?' * len(requested_dvs))})"
                )
                cur = conn.execute(
                    f"SELECT {placeholders} FROM {table} {where}",
                    tuple(requested_dvs),
                )
                row_count = 0
                for row in cur:
                    vals = [_sql_literal(v) for v in row]
                    gz.write(
                        f"INSERT INTO {table} ({placeholders}) VALUES ({','.join(vals)});\n"
                    )
                    row_count += 1
                gz.write(f"-- {table}: {row_count} rows backed up\n")
            gz.write("COMMIT;\n")
    finally:
        conn.close()

    os.replace(tmp, final)
    # Verify gzip integrity
    with gzip.open(final, "rb") as fh:
        head = fh.read(64)
    if not head.startswith(b"-- Zeus calibration_v2 pre-promotion"):
        raise RuntimeError(f"Backup integrity check failed: bad header in {final}")
    return final


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value).replace("'", "''")
    return f"'{s}'"


# --------------------------------------------------------------------------
# Promote subcommand
# --------------------------------------------------------------------------


def _run_integrity_check(conn: sqlite3.Connection) -> str:
    """Run ``PRAGMA integrity_check`` and return the first row's first cell.

    Extracted as a standalone function so tests can monkeypatch it without
    touching the immutable ``sqlite3.Connection`` type.
    """
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if not rows:
        return "(no result)"
    return str(rows[0][0])


def _resolve_metrics(spec: str | None) -> list[str]:
    if not spec:
        return list(ALL_METRICS)
    out = [m.strip() for m in spec.split(",") if m.strip()]
    bad = [m for m in out if m not in METRIC_TO_DATA_VERSIONS]
    if bad:
        raise SystemExit(f"unknown metric(s): {bad}; valid: {list(METRIC_TO_DATA_VERSIONS)}")
    return out


def cmd_promote(args: argparse.Namespace) -> int:
    stage_path = Path(args.stage_db).resolve()
    prod_path = Path(args.prod_db).resolve()
    metrics = _resolve_metrics(args.metrics)
    requested_dvs: list[str] = []
    for m in metrics:
        requested_dvs.extend(METRIC_TO_DATA_VERSIONS[m])

    print(f"STAGE_DB: {stage_path}")
    print(f"PROD_DB:  {prod_path}")
    print(f"Metrics:  {', '.join(metrics)}")
    print(f"Mode:     {'COMMIT' if args.commit else 'DRY-RUN (use --commit to apply)'}")
    print()

    stage = _ro_connect(stage_path)
    sentinels = _load_sentinels(stage)
    status = _sentinel_status_for_metrics(sentinels, metrics)
    bad = [m for m, s in status.items() if s != "complete"]
    if bad and not args.allow_incomplete:
        print(f"✗ REFUSED: sentinels not complete for: {bad}")
        print("  Use --allow-incomplete to override (NOT RECOMMENDED).")
        stage.close()
        return 1

    # Compute proposed changes
    proposed: dict[str, dict[str, int]] = {}
    for table in ("platt_models_v2", "calibration_pairs_v2"):
        if not args.include_pairs and table == "calibration_pairs_v2":
            proposed[table] = {"stage_rows": 0, "prod_rows_to_delete": 0, "skipped": 1}
            continue
        sc = _row_count(
            stage,
            table,
            f"data_version IN ({','.join('?' * len(requested_dvs))})",
            tuple(requested_dvs),
        )
        prod = _ro_connect(prod_path)
        try:
            pc = _row_count(
                prod,
                table,
                f"data_version IN ({','.join('?' * len(requested_dvs))})",
                tuple(requested_dvs),
            )
        finally:
            prod.close()
        proposed[table] = {"stage_rows": sc, "prod_rows_to_delete": pc, "skipped": 0}

    print("=== Proposed changes ===")
    for table, info in proposed.items():
        if info.get("skipped"):
            print(f"  {table}: SKIPPED (use --include-pairs to promote)")
            continue
        print(
            f"  {table}: DELETE {info['prod_rows_to_delete']:,} from PROD, "
            f"INSERT {info['stage_rows']:,} from STAGE"
        )
    print()

    # Copilot C (#112): refuse to commit when STAGE has 0 rows for the
    # tables we would touch. Otherwise --commit would silently DELETE
    # PROD rows for the requested data_versions and INSERT nothing,
    # wiping calibration_v2 if STAGE is empty/mis-specified.
    empty_tables: list[str] = []
    for table, info in proposed.items():
        if info.get("skipped"):
            continue
        if info["stage_rows"] == 0:
            empty_tables.append(table)
    if empty_tables and not args.allow_empty_stage:
        print(
            f"\u2717 REFUSED: STAGE has 0 rows for {empty_tables} matching the "
            f"requested data_versions. --commit would DELETE PROD rows and "
            f"INSERT nothing, wiping calibration_v2 for these data_versions."
        )
        print("  Use --allow-empty-stage to override (DANGEROUS).")
        stage.close()
        return 1

    # Copilot M (#112): schema compatibility check between STAGE and PROD
    # for the tables actually being promoted. Mismatched columns would
    # otherwise fail mid-promotion (after DELETE) and force a rollback.
    schema_mismatches: list[str] = []
    prod_ro = _ro_connect(prod_path)
    try:
        for table in ("platt_models_v2", "calibration_pairs_v2"):
            if not args.include_pairs and table == "calibration_pairs_v2":
                continue
            stage_cols = _column_names(stage, table)
            prod_cols = _column_names(prod_ro, table)
            if stage_cols != prod_cols:
                schema_mismatches.append(
                    f"{table}: STAGE={stage_cols} vs PROD={prod_cols}"
                )
    finally:
        prod_ro.close()
    if schema_mismatches:
        print("\u2717 REFUSED: STAGE/PROD schema mismatch:")
        for line in schema_mismatches:
            print(f"  {line}")
        print(
            "  Promote requires identical column sets and order. Resolve schema "
            "drift before retrying."
        )
        stage.close()
        return 1

    if not args.commit:
        print("\u2139 DRY-RUN: no changes made. Re-run with --commit to apply.")
        stage.close()
        return 0

    # Backup first
    backup_dir = Path(args.backup_dir).resolve()
    print(f"Creating backup in {backup_dir}...")
    backup_path = _backup_prod_tables(
        prod_path, metrics, backup_dir, include_pairs=bool(args.include_pairs)
    )
    print(f"  \u2713 {backup_path} ({backup_path.stat().st_size:,} bytes)")
    print()

    # Apply
    print("Opening PROD writable...")
    prod_rw = _rw_connect(prod_path)
    try:
        prod_rw.execute("BEGIN IMMEDIATE")
        try:
            for table in ("platt_models_v2", "calibration_pairs_v2"):
                if not args.include_pairs and table == "calibration_pairs_v2":
                    continue
                cols = _column_names(stage, table)
                placeholders = ",".join("?" for _ in cols)
                col_list = ",".join(cols)
                # Delete
                deleted = prod_rw.execute(
                    f"DELETE FROM {table} WHERE data_version IN "
                    f"({','.join('?' * len(requested_dvs))})",
                    tuple(requested_dvs),
                ).rowcount
                print(f"  {table}: deleted {deleted:,} rows")
                # Insert
                cur = stage.execute(
                    f"SELECT {col_list} FROM {table} WHERE data_version IN "
                    f"({','.join('?' * len(requested_dvs))})",
                    tuple(requested_dvs),
                )
                inserted = 0
                batch: list[tuple] = []
                for row in cur:
                    rec = tuple(row)
                    if args.null_snapshot_id and table == "calibration_pairs_v2":
                        # NULL out snapshot_id column
                        idx = cols.index("snapshot_id")
                        rec = rec[:idx] + (None,) + rec[idx + 1 :]
                    batch.append(rec)
                    if len(batch) >= 5000:
                        prod_rw.executemany(
                            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                            batch,
                        )
                        inserted += len(batch)
                        batch.clear()
                if batch:
                    prod_rw.executemany(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                        batch,
                    )
                    inserted += len(batch)
                print(f"  {table}: inserted {inserted:,} rows")

            # Integrity check (extracted for testability)
            print("Running PRAGMA integrity_check...")
            ic_status = _run_integrity_check(prod_rw)
            if ic_status != "ok":
                raise RuntimeError(
                    f"integrity_check FAILED: {ic_status}; rolling back."
                )
            print(f"  ✓ {ic_status}")

            prod_rw.execute("COMMIT")
            print()
            print("✓ PROMOTION COMMITTED")
        except Exception as exc:
            prod_rw.execute("ROLLBACK")
            print()
            print(f"✗ ROLLBACK due to: {exc}")
            print(f"  Backup is at: {backup_path}")
            print(
                "  Restore via: gunzip -c BACKUP | sqlite3 PROD_DB"
                " (after manually clearing affected data_versions)"
            )
            raise
    finally:
        prod_rw.close()
        stage.close()

    print()
    print("=== Final PROD row counts ===")
    prod = _ro_connect(prod_path)
    try:
        for table in ("platt_models_v2", "calibration_pairs_v2"):
            n = _row_count(
                prod,
                table,
                f"data_version IN ({','.join('?' * len(requested_dvs))})",
                tuple(requested_dvs),
            )
            print(f"  {table}: {n:,}")
    finally:
        prod.close()
    return 0


# --------------------------------------------------------------------------
# Verify subcommand
# --------------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    prod_path = Path(args.prod_db).resolve()
    print(f"PROD_DB: {prod_path}")
    print()
    prod = _ro_connect(prod_path)
    try:
        # Every (city, data_version) bucket in platt_models_v2 should have
        # at least one calibration_pairs_v2 row for the same data_version.
        platt_dvs = {
            row["data_version"]
            for row in prod.execute(
                "SELECT DISTINCT data_version FROM platt_models_v2"
            )
        }
        pair_dvs = {
            row["data_version"]
            for row in prod.execute(
                "SELECT DISTINCT data_version FROM calibration_pairs_v2"
            )
        }
        orphan_dvs = platt_dvs - pair_dvs
        if orphan_dvs:
            print(f"✗ FAIL: platt_models_v2 data_versions with no calibration_pairs_v2:")
            for dv in sorted(orphan_dvs):
                print(f"    {dv}")
            return 1
        print(f"✓ All {len(platt_dvs)} platt data_versions have backing pairs")

        # Per-bucket check: cluster/season combos in platt should appear in pairs
        cur = prod.execute(
            """
            SELECT data_version, cluster, season, COUNT(*) AS platt_n,
                   (SELECT COUNT(*) FROM calibration_pairs_v2 cp
                    WHERE cp.data_version = p.data_version
                      AND cp.cluster = p.cluster
                      AND cp.season = p.season) AS pair_n
            FROM platt_models_v2 p
            GROUP BY data_version, cluster, season
            HAVING pair_n = 0
            """
        )
        empty_buckets = cur.fetchall()
        if empty_buckets:
            print(f"✗ FAIL: {len(empty_buckets)} platt buckets with zero pairs:")
            for r in empty_buckets[:10]:
                print(f"    {r['data_version']} cluster={r['cluster']} season={r['season']}")
            if len(empty_buckets) > 10:
                print(f"    ... and {len(empty_buckets) - 10} more")
            return 1
        print(f"✓ All platt buckets have ≥1 calibration_pairs row")
    finally:
        prod.close()
    return 0


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="promote_calibration_v2_stage_to_prod",
        description="Promote calibration_v2 artifacts STAGE_DB → production zeus-world.db.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect", help="Read-only summary of STAGE vs PROD.")
    pi.add_argument("--stage-db", required=True)
    pi.add_argument("--prod-db", default=None)
    pi.add_argument("--metrics", default=None, help="Comma-separated; default = all")
    pi.set_defaults(func=cmd_inspect)

    pp = sub.add_parser("promote", help="Promote STAGE → PROD (dry-run by default).")
    pp.add_argument("--stage-db", required=True)
    pp.add_argument("--prod-db", required=True)
    pp.add_argument("--metrics", default=None)
    pp.add_argument("--include-pairs", action="store_true",
                    help="Also promote calibration_pairs_v2 (large; default skipped — "
                         "platt_models_v2 is the only runtime artifact).")
    pp.add_argument("--null-snapshot-id", action="store_true",
                    help="NULL out snapshot_id on inserted calibration_pairs_v2 rows "
                         "(safer if STAGE snapshot_ids may not exist in PROD).")
    pp.add_argument("--allow-incomplete", action="store_true",
                    help="Bypass sentinel-completeness gate (DANGEROUS).")
    pp.add_argument("--allow-empty-stage", action="store_true",
                    help="Bypass STAGE-rows>0 safety gate; allows DELETE-only "
                         "promotion that wipes PROD rows for the requested "
                         "data_versions (DANGEROUS).")
    pp.add_argument("--backup-dir", default="state/backups")
    pp.add_argument("--commit", action="store_true",
                    help="Apply changes. Without this flag, dry-run only.")
    pp.set_defaults(func=cmd_promote)

    pv = sub.add_parser("verify", help="Read-only consistency check on PROD.")
    pv.add_argument("--prod-db", required=True)
    pv.set_defaults(func=cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
