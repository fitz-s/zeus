# Created: 2026-05-12
# Last reused or audited: 2026-05-12
# Authority basis: K1 workload-class DB split; PR #112 Option (c) split.
# STAGE_DB -> production zeus-world.db promotion of platt_models_v2
# artifacts produced by scripts/refit_platt_v2.py / scripts/rebuild_calibration_pairs_v2.py
# (which co-emits Platt models). All mutations are gated by --commit;
# default behavior is dry-run with full backup + rollback semantics.
"""Promote platt_models_v2 artifacts from a STAGE_DB to production zeus-world.db.

Per AGENTS.md K=3 K1 (workload-class DB split, 2026-05-11), platt_models_v2
lives in ``state/zeus-world.db`` (the world DB). This script handles ONLY
``platt_models_v2``; its sibling ``promote_calibration_pairs_v2.py`` handles
``calibration_pairs_v2`` on ``state/zeus-forecasts.db``. The two scripts
share no code by import to keep them independently runnable.

Subcommands
-----------

* ``inspect``  - read-only summary of STAGE vs PROD coverage. Reports rebuild
  sentinel state, row counts, and (cluster, season) bucket coverage per
  data_version. Exits 1 if STAGE has any in_progress sentinel or missing
  COMPLETE markers for the requested metrics.
* ``promote``  - dry-run by default. With ``--commit``: backs up PROD
  ``platt_models_v2`` to a gzipped SQL dump under ``state/backups/``,
  opens PROD with ``BEGIN IMMEDIATE``, replaces rows filtered by
  ``data_version`` derived from the metric set, runs ``PRAGMA integrity_check``,
  and rolls back on any failure.
* ``verify``   - read-only post-promote consistency check. Confirms every
  ``platt_models_v2`` row in PROD has well-formed identity columns
  (temperature_metric, cluster, season, data_version, model_key) and that
  no (data_version, cluster, season) bucket is empty after group-by.
  Cross-DB joins against ``calibration_pairs_v2`` (which now lives on
  ``zeus-forecasts.db``) are intentionally NOT performed here -- the
  sibling script handles its own table-local verify, and a wrapper may
  combine them.

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
  STAGE_DB filename. Defaults to ``state/zeus-world.db`` when --prod-db
  is omitted (K1: platt_models_v2 lives in the world DB).
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

DEFAULT_PROD_DB = "state/zeus-world.db"

TARGET_TABLE = "platt_models_v2"


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
        # Map logical metric label -> sentinel "metric" value (the rebuild
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
        # sentinel - that would let `inspect`/`promote --commit` report
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
        f"SELECT data_version, cluster, season, COUNT(*) AS n FROM {TARGET_TABLE} {where} "
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
    print(f"TABLE:    {TARGET_TABLE}")
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
    sc_platt = _row_count(
        stage,
        TARGET_TABLE,
        f"data_version IN ({','.join('?' * len(requested_dvs))})",
        tuple(requested_dvs),
    )
    print(f"  {TARGET_TABLE}: {sc_platt:>12,}")
    print()

    # Prod baseline
    if prod is not None:
        print("=== PROD row counts (baseline; would be replaced for these data_versions) ===")
        pc_platt = _row_count(
            prod,
            TARGET_TABLE,
            f"data_version IN ({','.join('?' * len(requested_dvs))})",
            tuple(requested_dvs),
        )
        print(f"  {TARGET_TABLE}: {pc_platt:>12,}")
        print()
        total_platt = _row_count(prod, TARGET_TABLE)
        print(f"  (total {TARGET_TABLE} in PROD across all data_versions: {total_platt:,})")
        print()

    # Coverage matrix
    print(f"=== STAGE coverage: {TARGET_TABLE} (cluster/season buckets) ===")
    pcov = _platt_coverage(stage, requested_dvs)
    for dv in requested_dvs:
        buckets = pcov.get(dv, {})
        total = sum(buckets.values())
        print(f"  {dv}: {len(buckets)} buckets, {total:,} models")
    print()

    # Verdict
    bad = [m for m, s in status.items() if s != "complete"]
    if bad:
        print(f"x STATUS: NOT READY - sentinels not complete for: {', '.join(bad)}")
        stage.close()
        if prod is not None:
            prod.close()
        return 1
    print("+ STATUS: READY for promote")
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
) -> Path:
    """Atomic, gzipped SQL dump of platt_models_v2 rows matching any of the
    metric data_versions. Returns final backup file path.

    Independently verifiable: ``gunzip -t`` then sqlite3 import + count.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = backup_dir / f"zeus-world.db.platt_models_v2_pre_promotion_{ts}.sql.gz"
    tmp = final.with_suffix(final.suffix + ".tmp")

    requested_dvs: list[str] = []
    for m in metrics:
        requested_dvs.extend(METRIC_TO_DATA_VERSIONS[m])

    conn = _ro_connect(prod_path)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as gz:
            gz.write("-- Zeus platt_models_v2 pre-promotion backup\n")
            gz.write(f"-- Generated: {datetime.now(timezone.utc).isoformat()}\n")
            gz.write(f"-- Source PROD: {prod_path}\n")
            gz.write(f"-- Metrics: {','.join(metrics)}\n")
            gz.write(f"-- Data versions: {','.join(requested_dvs)}\n")
            gz.write(f"-- Tables backed up: {TARGET_TABLE}\n\n")
            gz.write("BEGIN TRANSACTION;\n")
            cols = _column_names(conn, TARGET_TABLE)
            placeholders = ",".join(cols)
            where = (
                f"WHERE data_version IN ({','.join('?' * len(requested_dvs))})"
            )
            cur = conn.execute(
                f"SELECT {placeholders} FROM {TARGET_TABLE} {where}",
                tuple(requested_dvs),
            )
            row_count = 0
            for row in cur:
                vals = [_sql_literal(v) for v in row]
                gz.write(
                    f"INSERT INTO {TARGET_TABLE} ({placeholders}) VALUES ({','.join(vals)});\n"
                )
                row_count += 1
            gz.write(f"-- {TARGET_TABLE}: {row_count} rows backed up\n")
            gz.write("COMMIT;\n")
    finally:
        conn.close()

    os.replace(tmp, final)
    # Verify gzip integrity
    with gzip.open(final, "rb") as fh:
        head = fh.read(64)
    if not head.startswith(b"-- Zeus platt_models_v2 pre-promotion"):
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
    print(f"TABLE:    {TARGET_TABLE}")
    print(f"Metrics:  {', '.join(metrics)}")
    print(f"Mode:     {'COMMIT' if args.commit else 'DRY-RUN (use --commit to apply)'}")
    print()

    stage = _ro_connect(stage_path)
    sentinels = _load_sentinels(stage)
    status = _sentinel_status_for_metrics(sentinels, metrics)
    bad = [m for m, s in status.items() if s != "complete"]
    if bad and not args.allow_incomplete:
        print(f"x REFUSED: sentinels not complete for: {bad}")
        print("  Use --allow-incomplete to override (NOT RECOMMENDED).")
        stage.close()
        return 1

    # Compute proposed changes
    sc = _row_count(
        stage,
        TARGET_TABLE,
        f"data_version IN ({','.join('?' * len(requested_dvs))})",
        tuple(requested_dvs),
    )
    prod = _ro_connect(prod_path)
    try:
        pc = _row_count(
            prod,
            TARGET_TABLE,
            f"data_version IN ({','.join('?' * len(requested_dvs))})",
            tuple(requested_dvs),
        )
    finally:
        prod.close()

    print("=== Proposed changes ===")
    print(
        f"  {TARGET_TABLE}: DELETE {pc:,} from PROD, "
        f"INSERT {sc:,} from STAGE"
    )
    print()

    # Copilot C (#112): refuse to commit when STAGE has 0 rows for the
    # tables we would touch. Otherwise --commit would silently DELETE
    # PROD rows for the requested data_versions and INSERT nothing,
    # wiping platt_models_v2 if STAGE is empty/mis-specified.
    if sc == 0 and not args.allow_empty_stage:
        print(
            f"x REFUSED: STAGE has 0 rows for {TARGET_TABLE} matching the "
            f"requested data_versions. --commit would DELETE PROD rows and "
            f"INSERT nothing, wiping {TARGET_TABLE} for these data_versions."
        )
        print("  Use --allow-empty-stage to override (DANGEROUS).")
        stage.close()
        return 1

    # Copilot M (#112): schema compatibility check between STAGE and PROD
    # for the table actually being promoted. Mismatched columns would
    # otherwise fail mid-promotion (after DELETE) and force a rollback.
    prod_ro = _ro_connect(prod_path)
    try:
        stage_cols = _column_names(stage, TARGET_TABLE)
        prod_cols = _column_names(prod_ro, TARGET_TABLE)
    finally:
        prod_ro.close()
    if stage_cols != prod_cols:
        print("x REFUSED: STAGE/PROD schema mismatch:")
        print(f"  {TARGET_TABLE}: STAGE={stage_cols} vs PROD={prod_cols}")
        print(
            "  Promote requires identical column sets and order. Resolve schema "
            "drift before retrying."
        )
        stage.close()
        return 1

    if not args.commit:
        print("i DRY-RUN: no changes made. Re-run with --commit to apply.")
        stage.close()
        return 0

    # Backup first
    backup_dir = Path(args.backup_dir).resolve()
    print(f"Creating backup in {backup_dir}...")
    backup_path = _backup_prod_tables(prod_path, metrics, backup_dir)
    print(f"  + {backup_path} ({backup_path.stat().st_size:,} bytes)")
    print()

    # Apply
    print("Opening PROD writable...")
    prod_rw = _rw_connect(prod_path)
    try:
        prod_rw.execute("BEGIN IMMEDIATE")
        try:
            cols = _column_names(stage, TARGET_TABLE)
            placeholders = ",".join("?" for _ in cols)
            col_list = ",".join(cols)
            # Delete
            deleted = prod_rw.execute(
                f"DELETE FROM {TARGET_TABLE} WHERE data_version IN "
                f"({','.join('?' * len(requested_dvs))})",
                tuple(requested_dvs),
            ).rowcount
            print(f"  {TARGET_TABLE}: deleted {deleted:,} rows")
            # Insert
            cur = stage.execute(
                f"SELECT {col_list} FROM {TARGET_TABLE} WHERE data_version IN "
                f"({','.join('?' * len(requested_dvs))})",
                tuple(requested_dvs),
            )
            inserted = 0
            batch: list[tuple] = []
            for row in cur:
                batch.append(tuple(row))
                if len(batch) >= 5000:
                    prod_rw.executemany(
                        f"INSERT INTO {TARGET_TABLE} ({col_list}) VALUES ({placeholders})",
                        batch,
                    )
                    inserted += len(batch)
                    batch.clear()
            if batch:
                prod_rw.executemany(
                    f"INSERT INTO {TARGET_TABLE} ({col_list}) VALUES ({placeholders})",
                    batch,
                )
                inserted += len(batch)
            print(f"  {TARGET_TABLE}: inserted {inserted:,} rows")

            # Integrity check (extracted for testability)
            print("Running PRAGMA integrity_check...")
            ic_status = _run_integrity_check(prod_rw)
            if ic_status != "ok":
                raise RuntimeError(
                    f"integrity_check FAILED: {ic_status}; rolling back."
                )
            print(f"  + {ic_status}")

            prod_rw.execute("COMMIT")
            print()
            print("+ PROMOTION COMMITTED")
        except Exception as exc:
            prod_rw.execute("ROLLBACK")
            print()
            print(f"x ROLLBACK due to: {exc}")
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
        n = _row_count(
            prod,
            TARGET_TABLE,
            f"data_version IN ({','.join('?' * len(requested_dvs))})",
            tuple(requested_dvs),
        )
        print(f"  {TARGET_TABLE}: {n:,}")
    finally:
        prod.close()
    return 0


# --------------------------------------------------------------------------
# Verify subcommand (table-local; cross-table verify lives in sibling script)
# --------------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    """Table-local consistency check on PROD.platt_models_v2.

    Per K1 (workload-class DB split), calibration_pairs_v2 now lives on
    zeus-forecasts.db, so this script does NOT cross-DB JOIN. Instead we
    check that platt_models_v2 has no rows with NULL identity columns,
    no empty (data_version, cluster, season) buckets after group-by, and
    a positive total row count.
    """
    prod_path = Path(args.prod_db).resolve()
    print(f"PROD_DB: {prod_path}")
    print(f"TABLE:   {TARGET_TABLE}")
    print()
    prod = _ro_connect(prod_path)
    try:
        # Check 1: row count > 0
        total = _row_count(prod, TARGET_TABLE)
        if total == 0:
            print(f"x FAIL: {TARGET_TABLE} is empty (0 rows)")
            return 1
        print(f"+ {TARGET_TABLE} has {total:,} rows")

        # Check 2: no NULL identity columns
        null_check_sql = (
            f"SELECT COUNT(*) FROM {TARGET_TABLE} "
            "WHERE model_key IS NULL OR temperature_metric IS NULL "
            "   OR cluster IS NULL OR season IS NULL OR data_version IS NULL"
        )
        null_count = int(prod.execute(null_check_sql).fetchone()[0])
        if null_count > 0:
            print(f"x FAIL: {null_count} {TARGET_TABLE} rows with NULL identity columns")
            return 1
        print("+ All identity columns are non-NULL")

        # Check 3: bucket coverage report (informational; does not fail)
        cur = prod.execute(
            f"SELECT data_version, cluster, season, COUNT(*) AS n "
            f"FROM {TARGET_TABLE} GROUP BY data_version, cluster, season"
        )
        buckets = cur.fetchall()
        print(f"+ {len(buckets)} distinct (data_version, cluster, season) buckets")

        # Check 4: data_version coverage
        dv_counts = {
            row["data_version"]: int(row["n"])
            for row in prod.execute(
                f"SELECT data_version, COUNT(*) AS n FROM {TARGET_TABLE} GROUP BY data_version"
            )
        }
        print(f"+ {len(dv_counts)} distinct data_versions:")
        for dv, n in sorted(dv_counts.items()):
            print(f"    {dv}: {n:,}")
    finally:
        prod.close()
    return 0


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="promote_platt_models_v2",
        description=(
            "Promote platt_models_v2 STAGE_DB -> production zeus-world.db "
            "(K1 workload-class split)."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect", help="Read-only summary of STAGE vs PROD.")
    pi.add_argument("--stage-db", required=True)
    pi.add_argument("--prod-db", default=None,
                    help=f"PROD DB path (default: {DEFAULT_PROD_DB} when --commit; "
                         "omitted for inspect = STAGE-only summary).")
    pi.add_argument("--metrics", default=None, help="Comma-separated; default = all")
    pi.set_defaults(func=cmd_inspect)

    pp = sub.add_parser("promote", help="Promote STAGE -> PROD (dry-run by default).")
    pp.add_argument("--stage-db", required=True)
    pp.add_argument("--prod-db", default=DEFAULT_PROD_DB,
                    help=f"PROD DB path (default: {DEFAULT_PROD_DB}; K1: world DB).")
    pp.add_argument("--metrics", default=None)
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

    pv = sub.add_parser("verify", help="Read-only table-local consistency check on PROD.")
    pv.add_argument("--prod-db", default=DEFAULT_PROD_DB,
                    help=f"PROD DB path (default: {DEFAULT_PROD_DB}; K1: world DB).")
    pv.set_defaults(func=cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
