#!/usr/bin/env python3
# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Purpose: candidate-only repair of a bounded corrupt executable snapshot tail.
# Authority basis: executable_market_snapshots is append-only execution evidence;
# canonical venue/order/position facts are owned by separate tables.
"""Repair a narrowly fingerprinted corrupt executable snapshot table tail.

Apply requires the operator to identify the target as a candidate clone and to
confirm the writer fence; the command cannot independently prove clone identity.
It raw-bridges only the unreadable right tail, then rebuilds every index from the
readable table. The full table is not copied, so a large append-only history can
be retained without requiring a second table-sized allocation.
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

from scripts.repair_position_events_corruption import (
    APPROVED_SQLITE_SOURCE_IDS,
    _assert_writer_fence,
    _connect,
    _interior_cells,
    _tail_parent_patch,
)


TABLE = "executable_market_snapshots"
AUTO_INDEX = "sqlite_autoindex_executable_market_snapshots_1"
EXPECTED_SCHEMA_SHA256 = (
    "ac285a98c11071b3265d64f8deb099120e76fe79a326770e087c1d7b1b67f1a5"
)
EXPECTED_EXPLICIT_INDEXES = frozenset(
    {
        "idx_snapshots_condition_captured",
        "idx_snapshots_no_token_captured",
        "idx_snapshots_selected_token_captured",
        "idx_snapshots_yes_token_captured",
    }
)
_FIXTURE_ENV = "ZEUS_EXECUTABLE_SNAPSHOT_REPAIR_ALLOW_FIXTURE_SCHEMA"


@dataclass(frozen=True)
class LostSnapshot:
    rowid: int
    snapshot_id: str
    primary_index_key_recovered: bool


@dataclass(frozen=True)
class Inspection:
    db_path: str
    sqlite_source_id: str
    schema_sha256: str
    indexed_count: int
    indexed_max_rowid: int
    indexed_tail_count: int
    last_readable_rowid: int
    lost_snapshots: tuple[LostSnapshot, ...]

    @property
    def lost_count(self) -> int:
        return len(self.lost_snapshots)


def _fixture_schema_allowed() -> bool:
    return (
        os.environ.get(_FIXTURE_ENV) == "1"
        and bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def _schema_fingerprint(conn: sqlite3.Connection) -> tuple[str, str]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    if row is None or not row["sql"]:
        raise RuntimeError(f"{TABLE} table is missing")
    source_id = str(conn.execute("SELECT sqlite_source_id()").fetchone()[0])
    if source_id not in APPROVED_SQLITE_SOURCE_IDS:
        raise RuntimeError(
            "REFUSED: SQLite source build is not approved for this repair: "
            f"{source_id}"
        )
    schema_sha = hashlib.sha256(str(row["sql"]).encode()).hexdigest()
    if schema_sha != EXPECTED_SCHEMA_SHA256 and not _fixture_schema_allowed():
        raise RuntimeError(
            "REFUSED: executable snapshot schema drifted; "
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
    result = {str(row["name"]): str(row["sql"]) for row in rows}
    if set(result) != EXPECTED_EXPLICIT_INDEXES:
        raise RuntimeError(
            "REFUSED: executable snapshot explicit index set drifted: "
            f"{sorted(result)}"
        )
    auto = conn.execute(
        "SELECT rootpage FROM sqlite_master WHERE type='index' AND name=?",
        (AUTO_INDEX,),
    ).fetchone()
    if auto is None or int(auto["rootpage"]) <= 0:
        raise RuntimeError("REFUSED: executable snapshot primary auto-index is missing")
    return result


def inspect(path: Path, *, max_orphan_tail: int = 64) -> Inspection:
    if not path.is_file():
        raise RuntimeError(f"REFUSED: DB path is not a file: {path}")
    conn = _connect(path, read_only=True)
    try:
        source_id, schema_sha = _schema_fingerprint(conn)
        _index_sql(conn)
        indexed = conn.execute(
            f"""
            SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS max_rowid
              FROM {TABLE} INDEXED BY {AUTO_INDEX}
            """
        ).fetchone()
        indexed_count = int(indexed["n"])
        indexed_max = int(indexed["max_rowid"])
        if indexed_count <= 0 or indexed_max <= 0:
            raise RuntimeError("REFUSED: executable snapshot auto-index is empty")

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
            raise RuntimeError("REFUSED: no bounded unreadable indexed tail was found")

        expected = set(range(last_readable + 1, indexed_max + 1))
        by_snapshot = {
            int(row["rowid"]): str(row["snapshot_id"])
            for row in conn.execute(
                f"""
                SELECT rowid, snapshot_id
                  FROM {TABLE} INDEXED BY {AUTO_INDEX}
                 WHERE rowid BETWEEN ? AND ?
                """,
                (last_readable + 1, indexed_max),
            )
        }
        if not set(by_snapshot).issubset(expected):
            raise RuntimeError(
                "REFUSED: primary index returned rowids outside the unreadable tail"
            )
        lost = tuple(
            LostSnapshot(
                rowid=rowid,
                snapshot_id=by_snapshot.get(
                    rowid, f"__unreadable_snapshot_tail_{rowid}"
                ),
                primary_index_key_recovered=rowid in by_snapshot,
            )
            for rowid in sorted(expected)
        )
        if len(lost) > max_orphan_tail:
            raise RuntimeError(
                f"REFUSED: corrupt tail exceeds bound {max_orphan_tail}: {len(lost)}"
            )
        for item in lost:
            try:
                direct = conn.execute(
                    f"SELECT rowid FROM {TABLE} NOT INDEXED WHERE rowid=?",
                    (item.rowid,),
                ).fetchone()
            except sqlite3.DatabaseError:
                continue
            if direct is not None:
                raise RuntimeError(
                    f"REFUSED: indexed tail row {item.rowid} is directly readable"
                )
        return Inspection(
            db_path=str(path.resolve()),
            sqlite_source_id=source_id,
            schema_sha256=schema_sha,
            indexed_count=indexed_count,
            indexed_max_rowid=indexed_max,
            indexed_tail_count=len(by_snapshot),
            last_readable_rowid=last_readable,
            lost_snapshots=lost,
        )
    finally:
        conn.close()


def _prepare_table_tail_bridge(path: Path, inspection: Inspection) -> dict[str, object]:
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
    parent_page, header_offset, tail_cell_offsets = _tail_parent_patch(
        path, root, inspection.last_readable_rowid
    )
    bridge_count = len(tail_cell_offsets) + 1
    names = tuple(f"__snapshot_repair_bridge_{index}" for index in range(bridge_count))
    with path.open("rb", buffering=0) as handle:
        header = handle.read(100)
        raw_size = int.from_bytes(header[16:18], "big")
        page_size = 65_536 if raw_size == 1 else raw_size
        handle.seek((parent_page - 1) * page_size)
        parent = handle.read(page_size)
    cells, _rightmost = _interior_cells(parent, header_offset=header_offset)
    key_by_offset = {cell_offset: key for _child, key, cell_offset in cells}
    upper_bounds = tuple(key_by_offset[offset] for offset in tail_cell_offsets) + (
        inspection.indexed_max_rowid,
    )
    lower_bound = inspection.last_readable_rowid
    grouped: list[tuple[LostSnapshot, ...]] = []
    for upper_bound in upper_bounds:
        rows = tuple(
            item
            for item in inspection.lost_snapshots
            if lower_bound < item.rowid <= upper_bound
        )
        if not rows:
            raise RuntimeError(
                "REFUSED: corrupt tail child range has no independently indexed rows"
            )
        grouped.append(rows)
        lower_bound = upper_bound
    if tuple(item for rows in grouped for item in rows) != inspection.lost_snapshots:
        raise RuntimeError("REFUSED: corrupt tail child ranges do not cover the tail")

    conn = _connect(path, read_only=False)
    try:
        columns = tuple(
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info('{TABLE}')")
        )
        not_null = {
            str(row["name"]): bool(row["notnull"])
            for row in conn.execute(f"PRAGMA table_info('{TABLE}')")
        }
        conn.execute("BEGIN IMMEDIATE")
        for name, rows in zip(names, grouped, strict=True):
            quoted = ", ".join(f'"{column}"' for column in columns)
            conn.execute(
                f'CREATE TABLE "{name}" AS SELECT {quoted} FROM "{TABLE}" WHERE 0'
            )
            placeholders = ", ".join("?" for _ in range(len(columns) + 1))
            for item in rows:
                known: dict[str, object] = {
                    "snapshot_id": item.snapshot_id,
                }
                values = [
                    known.get(column, "" if not_null[column] else None)
                    for column in columns
                ]
                conn.execute(
                    f'INSERT INTO "{name}" (rowid, {quoted}) VALUES ({placeholders})',
                    (item.rowid, *values),
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
        for cell_offset, bridge_page in zip(
            tail_cell_offsets, pages[:-1], strict=True
        ):
            page[cell_offset : cell_offset + 4] = struct.pack(">I", bridge_page)
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
            "DELETE FROM sqlite_schema WHERE name=?",
            ((name,) for name in names),
        )
        conn.execute("COMMIT")
        conn.execute(f"PRAGMA schema_version={version + 1}")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return {
        "parent_page": parent_page,
        "tail_child_slots_replaced": bridge_count,
        "bridge_pages": pages,
    }


def _hide_explicit_indexes(
    conn: sqlite3.Connection, explicit: dict[str, str]
) -> None:
    version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute("BEGIN IMMEDIATE")
    placeholders = ", ".join("?" for _ in explicit)
    conn.execute(
        f"DELETE FROM sqlite_schema WHERE type='index' AND name IN ({placeholders})",
        tuple(explicit),
    )
    if int(conn.execute("SELECT changes()").fetchone()[0]) != len(explicit):
        raise RuntimeError("explicit executable snapshot index schema deletion failed")
    conn.execute("COMMIT")
    conn.execute(f"PRAGMA schema_version={version + 1}")
    conn.execute("PRAGMA writable_schema=OFF")


def _replace_auto_index(conn: sqlite3.Connection) -> None:
    temporary = "__snapshot_repair_primary_index"
    conn.execute(
        f'CREATE UNIQUE INDEX "{temporary}" ON "{TABLE}" (snapshot_id)'
    )
    row = conn.execute(
        "SELECT rootpage FROM sqlite_master WHERE type='index' AND name=?",
        (temporary,),
    ).fetchone()
    if row is None:
        raise RuntimeError("temporary executable snapshot primary index is missing")
    replacement_root = int(row["rootpage"])
    version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE sqlite_schema SET rootpage=? WHERE type='index' AND name=?",
        (replacement_root, AUTO_INDEX),
    )
    if int(conn.execute("SELECT changes()").fetchone()[0]) != 1:
        raise RuntimeError("primary auto-index root swap did not update one row")
    conn.execute(
        "DELETE FROM sqlite_schema WHERE type='index' AND name=?", (temporary,)
    )
    if int(conn.execute("SELECT changes()").fetchone()[0]) != 1:
        raise RuntimeError("temporary primary index schema deletion failed")
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
        raise RuntimeError(
            "REFUSED: raw B-tree repair is allowed only on an explicit candidate clone"
        )
    _assert_writer_fence(path, operator_confirms_fenced)
    if inspect(path, max_orphan_tail=max(64, inspection.lost_count)) != inspection:
        raise RuntimeError("REFUSED: corruption fingerprint changed before apply")
    bridge = _prepare_table_tail_bridge(path, inspection)

    conn: sqlite3.Connection | None = _connect(path, read_only=False)
    try:
        _schema_fingerprint(conn)
        explicit = _index_sql(conn)
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise RuntimeError("REFUSED: repair requires WAL journal mode")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA fullfsync=ON")
        _hide_explicit_indexes(conn, explicit)
        conn.close()
        conn = None
        conn = _connect(path, read_only=False)
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA fullfsync=ON")
        _replace_auto_index(conn)
        trigger = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='no_delete_executable_market_snapshots'"
        ).fetchone()
        if trigger is None or not trigger["sql"]:
            raise RuntimeError("REFUSED: append-only delete trigger is missing")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TRIGGER no_delete_executable_market_snapshots")
            conn.execute(
                f"DELETE FROM {TABLE} WHERE rowid BETWEEN ? AND ?",
                (
                    inspection.last_readable_rowid + 1,
                    inspection.indexed_max_rowid,
                ),
            )
            if int(conn.execute("SELECT changes()").fetchone()[0]) != inspection.lost_count:
                raise RuntimeError("synthetic tail delete count mismatch")
            conn.execute(str(trigger["sql"]))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        for name, sql in sorted(explicit.items()):
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(sql)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
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
        integrity = tuple(
            str(row[0])
            for row in verify.execute(f"PRAGMA integrity_check('{TABLE}')")
        )
        count, max_rowid = verify.execute(
            f"SELECT COUNT(*), MAX(rowid) FROM {TABLE}"
        ).fetchone()
    finally:
        verify.close()
    if integrity != ("ok",):
        raise RuntimeError(f"post-repair executable snapshot integrity failed: {integrity}")
    if int(max_rowid) != inspection.last_readable_rowid:
        raise RuntimeError("post-repair high-water does not match last readable row")
    indexed_lower_bound = inspection.indexed_count - inspection.indexed_tail_count
    if not indexed_lower_bound <= int(count) <= inspection.last_readable_rowid:
        raise RuntimeError(
            "post-repair row count escaped the retained-history bounds: "
            f"indexed_lower_bound={indexed_lower_bound} "
            f"rowid_high_water={inspection.last_readable_rowid} actual={count}"
        )
    return {
        "status": "repaired",
        "db_path": str(path.resolve()),
        "preserved_rows": int(count),
        "preserved_max_rowid": int(max_rowid),
        "drop_bridge": bridge,
        "dropped_unreadable_snapshots": [
            asdict(item) for item in inspection.lost_snapshots
        ],
        "executable_snapshot_integrity": list(integrity),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator-confirms-fenced", action="store_true")
    parser.add_argument("--candidate-clone", action="store_true")
    parser.add_argument("--max-orphan-tail", type=int, default=64)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
    except Exception as exc:  # noqa: BLE001 - operator command must fail closed
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
