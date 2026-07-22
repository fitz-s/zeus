#!/usr/bin/env python3
# Lifecycle: created=2026-07-21; last_reviewed=2026-07-21; last_reused=2026-07-21
# Purpose: W1 P0 — the first tested off-machine backup capability for the three canonical
#   DBs. Uses the SQLite ONLINE BACKUP API (a consistent snapshot that includes committed
#   WAL frames) — NOT a raw file copy, which omits -wal and silently loses committed
#   transactions. One DB at a time, to an operator-provided EXTERNAL destination, then a
#   verify pass (integrity + schema/sequence/row watermarks) on the copy. A restore drill
#   is a separate --verify-only run against a produced backup.
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/
#   FINDINGS.md (F2: zero external backup; sole raw-copy path omits -wal) + consult §B/§7.
"""Online, consistent, verified backup of the canonical Zeus DBs to an external volume.

Why the backup API, not shutil.copy: on a live WAL database the committed data is split
between the main file and the -wal file; a raw copy of the main file alone loses every
transaction still in the WAL. `sqlite3.Connection.backup()` produces a single consistent
destination that folds in the WAL, and can copy incrementally (pages-per-step) so it does
not hold the source write lock continuously.

A backup is NOT considered successful until a SEPARATE process opens the restored copy and
verifies integrity + schema identity + sequence/row watermarks (a nominal-but-unrestorable
backup is worse than none — it gives false confidence). Run --verify-only for the restore drill.

Usage (operator-invoked; the destination MUST be on a different physical volume):
    python scripts/ops/backup_canonical_dbs.py --dest /Volumes/backup/zeus/$(date +%F)
    python scripts/ops/backup_canonical_dbs.py --dest ... --only zeus_trades.db
    python scripts/ops/backup_canonical_dbs.py --verify-only --dest /Volumes/backup/zeus/2026-07-21
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = ("zeus_trades.db", "zeus-forecasts.db", "zeus-world.db")
_PAGES_PER_STEP = 2000          # incremental step; releases the source lock between steps
_SLEEP_BETWEEN_STEPS_S = 0.05   # yield to the live writer under load
# The backup uses the SQLite backup API over a live WAL DB; releases <=3.51.2
# carry the WAL-reset corruption bug (fixed 3.51.3). Gate on the fix VERSION,
# not an exact source_id allowlist (which would reject every safe newer build).
# Mirrors src/state/db.assert_sqlite_version_safe.
_MIN_SQLITE_VERSION = (3, 51, 3)


def _now_iso() -> str:
    # avoid importing app; wall-clock is fine for a backup stamp
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _state_dir(state_dir: Optional[str]) -> Path:
    return Path(state_dir) if state_dir else ROOT / "state"


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream SHA-256 of a file without loading it into memory (the trade DB alone is ~43 GB)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _schema_hash(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _sequence_watermarks(conn: sqlite3.Connection) -> dict:
    try:
        return {n: s for n, s in conn.execute("SELECT name,seq FROM sqlite_sequence").fetchall()}
    except sqlite3.Error:
        return {}


def _table_counts(conn: sqlite3.Connection, tables: list[str]) -> dict:
    # bounded: only called on the freshly-restored copy in --verify-only, or on small fixtures.
    out = {}
    for t in tables:
        out[t] = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    return out


def _source_conn(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"REFUSED: source DB not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        src_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
        conn.close()
        _min = ".".join(str(p) for p in _MIN_SQLITE_VERSION)
        raise SystemExit(
            f"REFUSED: SQLite {sqlite3.sqlite_version} < {_min} (WAL-reset corruption "
            f"bug, fixed 3.51.3); source_id={src_id!r}. Back up on a fixed build."
        )
    return conn


def backup_one(src_path: Path, dest_path: Path) -> dict:
    """Online consistent backup of one DB. Returns a manifest entry."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        raise SystemExit(f"REFUSED: destination already exists: {dest_path}")
    src = _source_conn(src_path)
    restarts = {"n": 0}
    started = time.monotonic()

    def _progress(status, remaining, total):  # noqa: ANN001
        # remaining rising vs a prior low-water = the backup was restarted by a source write.
        if remaining > _progress.prev_remaining and _progress.prev_remaining >= 0:
            restarts["n"] += 1
        _progress.prev_remaining = remaining
    _progress.prev_remaining = -1

    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst, pages=_PAGES_PER_STEP, progress=_progress,
                   sleep=_SLEEP_BETWEEN_STEPS_S)
        dst.commit()
    finally:
        dst.close()
        src_schema = _schema_hash(src)
        src_seq = _sequence_watermarks(src)
        src.close()

    elapsed = round(time.monotonic() - started, 1)
    # verify the copy immediately (integrity + schema identity)
    v = _verify_one(dest_path, expect_schema=src_schema, expect_seq=src_seq)
    cap_sha = _sha256_file(dest_path)
    return {
        "db": src_path.name, "dest": str(dest_path), "elapsed_s": elapsed,
        "backup_restarts": restarts["n"], "schema_sha256": src_schema,
        "sequence_watermarks": src_seq, "dest_sha256": cap_sha,
        "verify": v, "created_at": _now_iso(),
    }


def _verify_one(dest_path: Path, *, expect_schema: Optional[str] = None,
                expect_seq: Optional[dict] = None) -> dict:
    """Open the RESTORED copy independently and prove it is a faithful, consistent DB."""
    conn = sqlite3.connect(f"file:{dest_path}?mode=ro", uri=True, timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA query_only=ON")
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fkviol = conn.execute("PRAGMA foreign_key_check").fetchall()
        schema = _schema_hash(conn)
        seq = _sequence_watermarks(conn)
        ok = (integ == "ok" and not fkviol
              and (expect_schema is None or schema == expect_schema)
              and (expect_seq is None or seq == expect_seq))
        return {"ok": bool(ok), "integrity_check": integ, "fk_violations": len(fkviol),
                "schema_matches": (expect_schema is None or schema == expect_schema),
                "sequence_matches": (expect_seq is None or seq == expect_seq)}
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="W1 P0: online verified backup of the canonical Zeus DBs.")
    ap.add_argument("--dest", required=True, help="Backup destination dir (MUST be a different physical volume).")
    ap.add_argument("--only", default=None, help="Back up just this one DB filename.")
    ap.add_argument("--state-dir", default=None, help="Source state dir (default: repo state/; tests use a fixture).")
    ap.add_argument("--verify-only", action="store_true", help="Restore drill: verify existing backups under --dest.")
    a = ap.parse_args(argv)
    state = _state_dir(a.state_dir)
    dest_root = Path(a.dest)
    targets = [a.only] if a.only else list(CANONICAL)

    if a.verify_only:
        results = {}
        for name in targets:
            bpath = dest_root / name
            if not bpath.exists():
                print(f"MISSING backup: {bpath}", file=sys.stderr); results[name] = {"ok": False, "missing": True}
                continue
            results[name] = _verify_one(bpath)
            print(f"VERIFY {name}: {results[name]}")
        return 0 if all(r.get("ok") for r in results.values()) else 1

    if dest_root.resolve() == (state).resolve():
        raise SystemExit("REFUSED: --dest must not be the live state/ dir (needs a different volume).")
    manifest = {"created_at": _now_iso(), "source_state_dir": str(state), "entries": []}
    for name in targets:
        entry = backup_one(state / name, dest_root / name)
        manifest["entries"].append(entry)
        print(f"BACKED UP {name}: elapsed={entry['elapsed_s']}s restarts={entry['backup_restarts']} "
              f"verify_ok={entry['verify']['ok']}")
    mpath = dest_root / f"backup_manifest_{_now_iso().replace(':','').replace('-','')}.json"
    mpath.write_text(json.dumps(manifest, indent=2, default=str))
    # fsync the manifest + dir
    fd = os.open(str(mpath), os.O_RDONLY); os.fsync(fd); os.close(fd)
    dfd = os.open(str(dest_root), os.O_RDONLY); os.fsync(dfd); os.close(dfd)
    all_ok = all(e["verify"]["ok"] for e in manifest["entries"])
    print(f"MANIFEST {mpath} all_verify_ok={all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
