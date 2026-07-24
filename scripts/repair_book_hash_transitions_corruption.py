#!/usr/bin/env python3
# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Purpose: candidate-only repair of a bounded corrupt book-hash transition tail.
"""Repair a fingerprinted unreadable tail in ``book_hash_transitions``.

Apply requires an explicit candidate clone and the all-writer fence. The repair
preserves every readable row, removes only a bounded tail whose primary-key
identities remain independently readable, and requires that the matching
snapshot interval is already absent. It then rebuilds every index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.repair_executable_snapshot_corruption import _hide_explicit_indexes
from scripts.repair_position_events_corruption import (
    APPROVED_SQLITE_SOURCE_IDS,
    _assert_writer_fence,
    _connect,
    _interior_cells,
    _tail_parent_patch,
)


TABLE = "book_hash_transitions"
AUTO_INDEX = "sqlite_autoindex_book_hash_transitions_1"
EXPECTED_SCHEMA_SHA256 = (
    "0f84b3b3faaab6ae484d39f780441ac6fcbbc6ff9a8902b364bb748a66e7e168"
)
EXPECTED_EXPLICIT_INDEXES = frozenset(
    {
        "idx_book_hash_transitions_market_time",
        "idx_book_hash_transitions_new_hash",
    }
)
_FIXTURE_ENV = "ZEUS_BOOK_HASH_REPAIR_ALLOW_FIXTURE_SCHEMA"


@dataclass(frozen=True)
class LostTransition:
    rowid: int
    market_slug: str
    observed_at: str
    transition_seq: int


@dataclass(frozen=True)
class Inspection:
    db_path: str
    sqlite_source_id: str
    schema_sha256: str
    indexed_count: int
    indexed_max_rowid: int
    last_readable_rowid: int
    lost_transitions: tuple[LostTransition, ...]

    @property
    def lost_count(self) -> int:
        return len(self.lost_transitions)


def _fixture_schema_allowed() -> bool:
    return (
        os.environ.get(_FIXTURE_ENV) == "1"
        and bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def _schema_identity(conn: sqlite3.Connection) -> tuple[str, str]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    if row is None or not row["sql"]:
        raise RuntimeError(f"{TABLE} table is missing")
    source_id = str(conn.execute("SELECT sqlite_source_id()").fetchone()[0])
    if source_id not in APPROVED_SQLITE_SOURCE_IDS:
        raise RuntimeError(f"REFUSED: unapproved SQLite source build: {source_id}")
    schema_sha = hashlib.sha256(str(row["sql"]).encode()).hexdigest()
    if schema_sha != EXPECTED_SCHEMA_SHA256 and not _fixture_schema_allowed():
        raise RuntimeError(
            "REFUSED: book-hash schema drifted; "
            f"expected={EXPECTED_SCHEMA_SHA256} actual={schema_sha}"
        )
    return source_id, schema_sha


def _index_sql(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT name, sql
          FROM sqlite_master
         WHERE type='index' AND tbl_name=? AND sql IS NOT NULL
        """,
        (TABLE,),
    ).fetchall()
    explicit = {str(row["name"]): str(row["sql"]) for row in rows}
    if set(explicit) != EXPECTED_EXPLICIT_INDEXES:
        raise RuntimeError(
            f"REFUSED: book-hash explicit index drift: {sorted(explicit)}"
        )
    auto = conn.execute(
        "SELECT rootpage FROM sqlite_master WHERE type='index' AND name=?",
        (AUTO_INDEX,),
    ).fetchone()
    if auto is None or int(auto["rootpage"]) <= 0:
        raise RuntimeError("REFUSED: book-hash primary auto-index is missing")
    return explicit


def inspect(path: Path, *, max_orphan_tail: int = 64) -> Inspection:
    conn = _connect(path, read_only=True)
    try:
        source_id, schema_sha = _schema_identity(conn)
        _index_sql(conn)
        count, max_rowid = conn.execute(
            f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) "
            f"FROM {TABLE} INDEXED BY {AUTO_INDEX}"
        ).fetchone()
        indexed_count = int(count)
        indexed_max = int(max_rowid)
        if indexed_count <= 0 or indexed_count != indexed_max:
            raise RuntimeError("REFUSED: indexed book-hash rowids are not dense")

        last_readable = 0
        for rowid in range(
            indexed_max,
            max(0, indexed_max - max_orphan_tail - 1),
            -1,
        ):
            try:
                row = conn.execute(
                    f"SELECT rowid FROM {TABLE} NOT INDEXED WHERE rowid=?",
                    (rowid,),
                ).fetchone()
            except sqlite3.DatabaseError:
                continue
            if row is not None:
                last_readable = int(row["rowid"])
                break
        if last_readable <= 0 or last_readable >= indexed_max:
            raise RuntimeError("REFUSED: no bounded unreadable tail was found")

        rows = conn.execute(
            f"""
            SELECT rowid, market_slug, observed_at, transition_seq
              FROM {TABLE} INDEXED BY {AUTO_INDEX}
             WHERE rowid BETWEEN ? AND ?
             ORDER BY rowid
            """,
            (last_readable + 1, indexed_max),
        ).fetchall()
        expected = tuple(range(last_readable + 1, indexed_max + 1))
        if tuple(int(row["rowid"]) for row in rows) != expected:
            raise RuntimeError("REFUSED: primary index does not identify every tail row")
        lost = tuple(
            LostTransition(
                rowid=int(row["rowid"]),
                market_slug=str(row["market_slug"]),
                observed_at=str(row["observed_at"]),
                transition_seq=int(row["transition_seq"]),
            )
            for row in rows
        )
        markets = {item.market_slug for item in lost}
        if len(markets) != 1:
            raise RuntimeError("REFUSED: corrupt transition tail spans markets")
        survivor = conn.execute(
            """
            SELECT COUNT(*)
              FROM executable_market_snapshots
             WHERE event_slug=?
               AND captured_at BETWEEN ? AND ?
            """,
            (
                lost[0].market_slug,
                min(item.observed_at for item in lost),
                max(item.observed_at for item in lost),
            ),
        ).fetchone()[0]
        if int(survivor) != 0:
            raise RuntimeError(
                "REFUSED: transition tail has surviving snapshot evidence"
            )
        return Inspection(
            db_path=str(path.resolve()),
            sqlite_source_id=source_id,
            schema_sha256=schema_sha,
            indexed_count=indexed_count,
            indexed_max_rowid=indexed_max,
            last_readable_rowid=last_readable,
            lost_transitions=lost,
        )
    finally:
        conn.close()


def _prepare_bridge(path: Path, inspection: Inspection) -> dict[str, object]:
    conn = _connect(path, read_only=False)
    try:
        root = int(
            conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE type='table' AND name=?",
                (TABLE,),
            ).fetchone()[0]
        )
    finally:
        conn.close()
    parent_page, header_offset, tail_offsets = _tail_parent_patch(
        path, root, inspection.last_readable_rowid
    )
    names = tuple(
        f"__book_hash_repair_bridge_{index}"
        for index in range(len(tail_offsets) + 1)
    )
    with path.open("rb", buffering=0) as handle:
        header = handle.read(100)
        raw_size = int.from_bytes(header[16:18], "big")
        page_size = 65_536 if raw_size == 1 else raw_size
        handle.seek((parent_page - 1) * page_size)
        parent = handle.read(page_size)
    cells, _rightmost = _interior_cells(parent, header_offset=header_offset)
    key_by_offset = {offset: key for _child, key, offset in cells}
    upper_bounds = tuple(key_by_offset[offset] for offset in tail_offsets) + (
        inspection.indexed_max_rowid,
    )
    grouped: list[tuple[LostTransition, ...]] = []
    lower = inspection.last_readable_rowid
    for upper in upper_bounds:
        group = tuple(
            item for item in inspection.lost_transitions if lower < item.rowid <= upper
        )
        if not group:
            raise RuntimeError("REFUSED: corrupt child range lacks indexed identities")
        grouped.append(group)
        lower = upper

    conn = _connect(path, read_only=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for name, group in zip(names, grouped, strict=True):
            conn.execute(f'CREATE TABLE "{name}" AS SELECT * FROM {TABLE} WHERE 0')
            for item in group:
                conn.execute(
                    f"""
                    INSERT INTO "{name}" (
                        rowid, market_slug, observed_at, transition_seq,
                        prev_hash, new_hash, delta_ms, cycle_id, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL, 14)
                    """,
                    (
                        item.rowid,
                        item.market_slug,
                        item.observed_at,
                        item.transition_seq,
                        f"lost-prev-{item.rowid}",
                        f"lost-new-{item.rowid}",
                    ),
                )
        conn.execute("COMMIT")
        pages = tuple(
            int(
                conn.execute(
                    "SELECT rootpage FROM sqlite_master WHERE name=?", (name,)
                ).fetchone()[0]
            )
            for name in names
        )
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    with path.open("r+b", buffering=0) as handle:
        header = handle.read(100)
        raw_size = int.from_bytes(header[16:18], "big")
        page_size = 65_536 if raw_size == 1 else raw_size
        handle.seek((parent_page - 1) * page_size)
        page = bytearray(handle.read(page_size))
        for offset, bridge_page in zip(tail_offsets, pages[:-1], strict=True):
            page[offset : offset + 4] = struct.pack(">I", bridge_page)
        page[header_offset + 8 : header_offset + 12] = struct.pack(">I", pages[-1])
        handle.seek((parent_page - 1) * page_size)
        handle.write(page)
        handle.flush()
        os.fsync(handle.fileno())

    conn = _connect(path, read_only=False)
    try:
        version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "DELETE FROM sqlite_schema WHERE name=?", ((name,) for name in names)
        )
        conn.execute("COMMIT")
        conn.execute(f"PRAGMA schema_version={version + 1}")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return {"parent_page": parent_page, "bridge_pages": pages}


def _replace_auto_index(conn: sqlite3.Connection) -> None:
    temporary = "__book_hash_repair_primary_index"
    conn.execute(
        f'CREATE UNIQUE INDEX "{temporary}" ON {TABLE} '
        "(market_slug, observed_at, transition_seq)"
    )
    root = int(
        conn.execute(
            "SELECT rootpage FROM sqlite_master WHERE name=?", (temporary,)
        ).fetchone()[0]
    )
    version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE sqlite_schema SET rootpage=? WHERE name=?", (root, AUTO_INDEX)
    )
    if int(conn.execute("SELECT changes()").fetchone()[0]) != 1:
        raise RuntimeError("book-hash primary root swap failed")
    conn.execute("DELETE FROM sqlite_schema WHERE name=?", (temporary,))
    conn.execute("COMMIT")
    conn.execute(f"PRAGMA schema_version={version + 1}")
    conn.execute("PRAGMA writable_schema=OFF")


def apply_repair(
    path: Path,
    inspection: Inspection,
    *,
    operator_confirms_fenced: bool,
    candidate_clone: bool,
) -> dict[str, object]:
    if not candidate_clone:
        raise RuntimeError("REFUSED: apply is candidate-clone only")
    _assert_writer_fence(path, operator_confirms_fenced)
    if inspect(path, max_orphan_tail=max(64, inspection.lost_count)) != inspection:
        raise RuntimeError("REFUSED: corruption fingerprint changed")
    bridge = _prepare_bridge(path, inspection)
    conn: sqlite3.Connection | None = _connect(path, read_only=False)
    try:
        _schema_identity(conn)
        explicit = _index_sql(conn)
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise RuntimeError("REFUSED: repair requires WAL journal mode")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA fullfsync=ON")
        _hide_explicit_indexes(conn, explicit)
        conn.close()
        conn = None
        conn = _connect(path, read_only=False)
        _replace_auto_index(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"DELETE FROM {TABLE} WHERE rowid BETWEEN ? AND ?",
            (inspection.last_readable_rowid + 1, inspection.indexed_max_rowid),
        )
        if int(conn.execute("SELECT changes()").fetchone()[0]) != inspection.lost_count:
            raise RuntimeError("synthetic transition delete count mismatch")
        conn.execute("COMMIT")
        for _name, sql in sorted(explicit.items()):
            conn.execute(sql)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        if conn is not None:
            try:
                conn.execute("PRAGMA writable_schema=OFF")
            except sqlite3.Error:
                pass
            conn.close()

    verify = _connect(path, read_only=True)
    try:
        integrity = [
            str(row[0]) for row in verify.execute(f"PRAGMA integrity_check('{TABLE}')")
        ]
        count, max_rowid = verify.execute(
            f"SELECT COUNT(*), MAX(rowid) FROM {TABLE}"
        ).fetchone()
    finally:
        verify.close()
    if integrity != ["ok"]:
        raise RuntimeError(f"book-hash integrity failed: {integrity}")
    return {
        "status": "repaired",
        "db_path": str(path.resolve()),
        "preserved_rows": int(count),
        "preserved_max_rowid": int(max_rowid),
        "dropped_unreadable_transitions": [
            asdict(item) for item in inspection.lost_transitions
        ],
        "bridge": bridge,
        "book_hash_integrity": integrity,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator-confirms-fenced", action="store_true")
    parser.add_argument("--candidate-clone", action="store_true")
    parser.add_argument("--max-orphan-tail", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        report = inspect(args.db, max_orphan_tail=max(1, args.max_orphan_tail))
        if not args.apply:
            print(
                json.dumps(
                    {"status": "repairable", **asdict(report), "lost_count": report.lost_count},
                    sort_keys=True,
                )
            )
            return 0
        result = apply_repair(
            args.db,
            report,
            operator_confirms_fenced=args.operator_confirms_fenced,
            candidate_clone=args.candidate_clone,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
